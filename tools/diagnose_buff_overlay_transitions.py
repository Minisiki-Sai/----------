from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import Counter, deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.models import (
    BossActionEvent,
    BossStatusEvent,
    PlayerHealthEvent,
    PlayerLifecycleEvent,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps import capture_sources as capture_sources_module
from dragon_nest_builder.live_dps import protocol_v576 as protocol_v576_module
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def _event_payload(event: object) -> dict[str, Any]:
    payload: dict[str, Any]
    if dataclasses.is_dataclass(event):
        payload = dataclasses.asdict(event)
    else:
        payload = dict(getattr(event, "__dict__", {}))
    useful = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", 0, False, (), [], {})
    }
    return {"type": type(event).__name__, **useful}


def _buff_signature(rows: object) -> tuple[tuple[str, str, str, bool], ...]:
    if not isinstance(rows, list):
        return ()
    return tuple(
        sorted(
            (
                str(row.get("target_id") or ""),
                str(row.get("skill_name") or ""),
                str(row.get("catalog_key") or row.get("key") or ""),
                bool(row.get("permanent")),
            )
            for row in rows
            if isinstance(row, dict)
        )
    )


def diagnose(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    events = sorted(source.poll(), key=lambda row: row.timestamp_ms)
    decoder = getattr(source, "_decoder", None)
    actor_id = int(getattr(decoder, "current_self_actor_id", 0) or 0)
    local_actor_ids = set(getattr(decoder, "local_actor_ids", set()) or set())
    if actor_id <= 0 and len(local_actor_ids) == 1:
        actor_id = int(next(iter(local_actor_ids)))
    self_source_id = f"0x{actor_id:08X}" if actor_id else ""

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    original_live_capture = core.live_capture
    aggregator = CombatAggregator()
    recent: deque[object] = deque()
    transitions: list[dict[str, Any]] = []
    suspicious_events: list[dict[str, Any]] = []
    previous_signature: tuple[tuple[str, str, str, bool], ...] = ()
    previous_raw_active: tuple[tuple[str, str], ...] = ()
    last_projected: dict[str, Any] = {}
    event_types = Counter(type(event).__name__ for event in events)

    try:
        index = 0
        while index < len(events):
            timestamp_ms = int(events[index].timestamp_ms)
            current_events: list[object] = []
            while index < len(events) and int(events[index].timestamp_ms) == timestamp_ms:
                event = events[index]
                aggregator.ingest(event)
                recent.append(event)
                current_events.append(event)
                index += 1
            while recent and timestamp_ms - int(recent[0].timestamp_ms) > 2_000:
                recent.popleft()

            raw = aggregator.overlay_snapshot(timestamp_ms)
            raw["selfSourceId"] = self_source_id
            core.live_capture = SimpleNamespace(
                overlay_status=lambda raw=raw: {
                    "state": "running",
                    "status": "buff transition diagnostic",
                    "overlaySnapshot": raw,
                    "overlaySnapshotPublishedMonotonic": time.monotonic(),
                    "overlaySnapshotRevision": raw.get("revision", 0),
                }
            )
            projected = core.buff_overlay_status()
            last_projected = projected
            signature = _buff_signature(projected.get("buffs"))
            raw_active = tuple(
                sorted(
                    (
                        str(row.get("target_id") or ""),
                        str(row.get("skill_name") or ""),
                    )
                    for row in raw.get("player_buffs", [])
                    if isinstance(row, dict) and bool(row.get("active"))
                )
            )
            if signature != previous_signature or raw_active != previous_raw_active:
                disappeared = sorted(set(previous_signature) - set(signature))
                appeared = sorted(set(signature) - set(previous_signature))
                raw_disappeared = sorted(set(previous_raw_active) - set(raw_active))
                raw_appeared = sorted(set(raw_active) - set(previous_raw_active))
                transitions.append(
                    {
                        "seconds": round(timestamp_ms / 1000.0, 3),
                        "visible_count": len(signature),
                        "raw_active_count": len(raw_active),
                        "appeared": appeared,
                        "disappeared": disappeared,
                        "raw_appeared": raw_appeared,
                        "raw_disappeared": raw_disappeared,
                        "trigger_events": [_event_payload(event) for event in current_events],
                        "recent_events": [_event_payload(event) for event in recent],
                        "clear_revision": raw.get("player_buff_clear_revision", 0),
                    }
                )
            previous_signature = signature
            previous_raw_active = raw_active

            for event in current_events:
                if (
                    isinstance(event, PlayerLifecycleEvent)
                    and not event.active
                ) or (
                    isinstance(event, PlayerHealthEvent)
                    and event.current_hp <= 0
                    and (
                        event.packet_kind == "s2c-udp-player-hp-state-086f-v576"
                        or event.max_hp > 0
                    )
                ) or (
                    isinstance(event, BossActionEvent)
                    and (
                        event.packet_kind == "boss_buff_clear_status"
                        or bool(event.affected_target_ids)
                    )
                ) or (
                    isinstance(event, BossStatusEvent)
                    and event.target_scope == "player"
                    and event.operation == "remove"
                ):
                    suspicious_events.append(_event_payload(event))
    finally:
        core.live_capture = original_live_capture
        core.shutdown()
        source.close()

    emptying = [
        row
        for row in transitions
        if row["visible_count"] == 0 and row["disappeared"]
    ]
    return {
        "file": str(path),
        "duration_seconds": round(
            max((event.timestamp_ms for event in events), default=0) / 1000.0,
            3,
        ),
        "event_count": len(events),
        "event_types": dict(event_types),
        "self_source_id": self_source_id,
        "decoder_local_actor_ids": [
            f"0x{int(value):08X}"
            for value in sorted(local_actor_ids)
        ],
        "suspicious_events": suspicious_events,
        "emptying_transitions": emptying,
        "final_visible_buffs": [
            {
                "target_id": row.get("target_id"),
                "skill_name": row.get("skill_name"),
                "remaining_seconds": round(
                    float(row.get("remaining_seconds") or 0.0), 3
                ),
                "permanent": bool(row.get("permanent")),
            }
            for row in last_projected.get("buffs", [])
            if isinstance(row, dict)
        ],
        "all_transitions": transitions,
    }


def trace_wire_identity(path: Path) -> list[dict[str, Any]]:
    """Report packets that change local-actor evidence or emit a foreign cast."""

    module = capture_sources_module
    decoder = module._V576PacketEventDecoder(DATA_DIR, {})
    transport = module.V576TransportReassembler()
    ledger = module.V576EventLedger()
    changes: list[dict[str, Any]] = []
    header_struct = V576DiagnosticReplaySource._FRAME_HEADER
    magic = V576DiagnosticReplaySource._MAGIC

    def phase_candidates(payload: bytes) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        markers = (b"\x0e\x23\x00", b"\x0e\x1f\x00", b"\x12\x24\x00", b"\x12\x20\x00")
        for phase in range(len(protocol_v576_module._V576_BODY_XOR_KEY)):
            decoded = protocol_v576_module._decode_v576_body(payload, phase)
            matches: list[dict[str, Any]] = []
            for marker in markers:
                offset = decoded.find(marker, 14)
                while offset >= 0 and offset + 7 <= len(decoded):
                    skill_id = int.from_bytes(decoded[offset + 3 : offset + 7], "little")
                    if skill_id in decoder.action_skill_ids:
                        matches.append(
                            {
                                "offset": offset,
                                "marker": marker.hex(),
                                "skill_id": skill_id,
                                "preceding_actor": (
                                    f"0x{int.from_bytes(decoded[offset - 4:offset], 'little'):08X}"
                                    if offset >= 4
                                    else ""
                                ),
                            }
                        )
                    offset = decoded.find(marker, offset + 1)
            if matches:
                rows.append(
                    {
                        "phase": phase,
                        "client_actor": f"0x{int.from_bytes(decoded[10:14], 'little'):08X}",
                        "prefix": decoded[:40].hex(),
                        "matches": matches,
                    }
                )
        return rows
    with path.open("rb") as handle:
        if handle.read(len(magic)) != magic:
            raise ValueError("不是有效的 Dragon Nest .dpscap 统计记录")
        while True:
            header = handle.read(header_struct.size)
            if len(header) != header_struct.size:
                break
            timestamp, adapter, original_length, captured_length = header_struct.unpack(
                header
            )
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter in {
                module.IDENTITY_METADATA_ADAPTER_INDEX,
                module.IDENTITY_TRACE_ADAPTER_INDEX,
                module.SESSION_EVIDENCE_ADAPTER_INDEX,
            }:
                continue
            packet = module.decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical_packet in transport.feed(packet):
                if not ledger.accept(logical_packet):
                    continue
                before_local = set(decoder.local_actor_ids)
                before_self = decoder.current_self_actor_id
                emitted = decoder.feed_events(logical_packet)
                after_local = set(decoder.local_actor_ids)
                after_self = decoder.current_self_actor_id
                foreign_casts = [
                    event
                    for event in emitted
                    if type(event).__name__ == "SkillCastEvent"
                    and int(str(getattr(event, "source_id", "0")).replace("0x", ""), 16)
                    not in before_local
                ]
                if (
                    before_local != after_local
                    or before_self != after_self
                    or foreign_casts
                ):
                    origin = decoder.first_timestamp or logical_packet.timestamp_seconds
                    changes.append(
                        {
                            "seconds": round(
                                (logical_packet.timestamp_seconds - origin) * 1000
                            )
                            / 1000.0,
                            "transport": logical_packet.transport,
                            "direction": logical_packet.inferred_direction(),
                            "source": (
                                f"{logical_packet.source_ip}:"
                                f"{logical_packet.source_port}"
                            ),
                            "destination": (
                                f"{logical_packet.destination_ip}:"
                                f"{logical_packet.destination_port}"
                            ),
                            "payload_length": len(logical_packet.payload),
                            "signature": logical_packet.payload[5:9].hex(),
                            "before_local": [
                                f"0x{value:08X}" for value in sorted(before_local)
                            ],
                            "after_local": [
                                f"0x{value:08X}" for value in sorted(after_local)
                            ],
                            "before_self": (
                                f"0x{before_self:08X}" if before_self else ""
                            ),
                            "after_self": f"0x{after_self:08X}" if after_self else "",
                            "events": [_event_payload(event) for event in emitted],
                            "phase_candidates": phase_candidates(logical_packet.payload),
                        }
                    )
    return changes


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--wire", action="store_true")
    args = parser.parse_args()
    capture = args.capture.resolve()
    if args.wire:
        print(json.dumps(trace_wire_identity(capture), ensure_ascii=False, indent=2))
        return
    result = diagnose(capture)
    if not args.all:
        result.pop("all_transitions", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
