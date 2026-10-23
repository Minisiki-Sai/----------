from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.auto_capture_stop import (
    V576AutoCaptureStopDetector,
)
from dragon_nest_builder.live_dps.capture_sources import (
    _V576PacketEventDecoder,
    scan_v576_diagnostic_map_history,
)
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.npcap import (
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
    PREBATTLE_STATE_ADAPTER_INDEX,
    PROFILE_IDENTITY_ADAPTER_INDEX,
    decode_prebattle_state_snapshot,
)
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
)


MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
DATA_ROOT = ROOT / "dragon_nest_builder" / "data"


def audit_capture_full(path: Path) -> dict[str, object]:
    decoder = _V576PacketEventDecoder(DATA_ROOT, {})
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    detector = V576AutoCaptureStopDetector()
    stop_reason = ""
    stop_timestamp = 0.0
    first_timestamp = 0.0
    last_timestamp = 0.0
    frame_count = 0
    last_probe_timestamp = 0.0

    def feed(logical_packets) -> None:
        for logical_packet in logical_packets:
            if ledger.accept(logical_packet):
                decoder.feed_events(logical_packet)

    def probe(observed_timestamp: float, *, force: bool = False) -> None:
        nonlocal stop_reason, stop_timestamp, last_probe_timestamp
        if (
            not force
            and last_probe_timestamp
            and observed_timestamp < last_probe_timestamp + 0.25
        ):
            return
        last_probe_timestamp = observed_timestamp
        if not stop_reason:
            stop_reason = detector.evaluate(
                object(),
                max(0.0, observed_timestamp - first_timestamp),
                decoder=decoder,
            )
            if stop_reason:
                stop_timestamp = observed_timestamp

    with path.open("rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"不是 DNDP1 抓包：{path}")
        while True:
            header = stream.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = (
                FRAME_HEADER.unpack(header)
            )
            frame = stream.read(captured_length)
            if len(frame) != captured_length:
                break
            frame_count += 1
            if adapter == PREBATTLE_STATE_ADAPTER_INDEX:
                evidence = decode_prebattle_state_snapshot(frame)
                decoder.seed_capture_boundary_evidence(evidence)
                if not first_timestamp and evidence.get("boundary_timestamp"):
                    first_timestamp = float(evidence["boundary_timestamp"])
                continue
            if adapter in {250, PROFILE_IDENTITY_ADAPTER_INDEX}:
                continue
            last_timestamp = max(last_timestamp, float(timestamp))
            if not first_timestamp:
                first_timestamp = float(timestamp)
            if adapter in {
                IDENTITY_METADATA_ADAPTER_INDEX,
                IDENTITY_TRACE_ADAPTER_INDEX,
                SESSION_EVIDENCE_ADAPTER_INDEX,
            }:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            feed(transport.feed(packet))
            # Mirror the live 250 ms maintenance timer.  The old audit called
            # both stale-flow scanning and stop detection for every captured
            # frame, turning large lobby-heavy captures into a many-minute
            # audit although production never follows that schedule.
            if (
                not last_probe_timestamp
                or float(timestamp) >= last_probe_timestamp + 0.25
            ):
                feed(transport.flush_stale(float(timestamp)))
                probe(float(timestamp), force=True)

    final_timestamp = max(first_timestamp, last_timestamp, stop_timestamp)
    if first_timestamp:
        # Flush only stale/recoverable frames. A full EOF flush would make a
        # boundary that was unavailable during live capture look successful.
        final_timestamp = max(
            final_timestamp,
            max(
                (
                    float(row.get("observed_at") or 0.0) + first_timestamp
                    for row in decoder.detected_map_history
                ),
                default=first_timestamp,
            ),
        )
        feed(transport.flush_stale(final_timestamp + 0.30))
        probe(final_timestamp + 0.30, force=True)

    return {
        "file": str(path),
        "frames": frame_count,
        "nest": decoder.detected_nest_name,
        "confidence": decoder.detected_nest_confidence,
        "stopReason": stop_reason,
        "stopAtSeconds": (
            round(stop_timestamp - first_timestamp, 3)
            if stop_reason and first_timestamp
            else None
        ),
        "mapHistory": [dict(row) for row in decoder.detected_map_history],
    }


def audit_capture(path: Path) -> dict[str, object]:
    """Audit the boundary from the production map/scene evidence scanner.

    The previous default rebuilt every combat event merely to obtain the map
    state consumed by the stop detector.  A 200 MB capture could therefore
    take tens of minutes.  The replay cache already uses the dedicated scanner
    below; it parses the same map, teardown, transition-ack and hub-return wire
    layouts without materialising damage/status events.
    """

    history = [
        dict(row)
        for row in scan_v576_diagnostic_map_history(path)
        if isinstance(row, dict)
    ]
    history.sort(key=lambda row: float(row.get("observed_at") or 0.0))
    detector = V576AutoCaptureStopDetector()
    visible_history: list[dict[str, object]] = []
    decoder = SimpleNamespace(
        detected_map_history=visible_history,
        detected_map_candidate_id=0,
        detected_map_candidate_observed_at=0.0,
        detected_map_candidate_layout="",
        detected_scene_transition_ack_revision=0,
        detected_scene_transition_ack_observed_at=0.0,
        detected_hub_return_revision=0,
        detected_lobby_return_revision=0,
        detected_lobby_return_observed_at=0.0,
    )
    stop_reason = ""
    stop_at_seconds: float | None = None
    for row in history:
        visible_history.append(row)
        observed_at = max(0.0, float(row.get("observed_at") or 0.0))
        stop_reason = detector.evaluate(
            object(),
            observed_at,
            decoder=decoder,
        )
        if stop_reason:
            stop_at_seconds = round(observed_at, 3)
            break
    return {
        "file": str(path),
        "bytes": path.stat().st_size,
        "auditMode": "map_scene_evidence",
        "stopReason": stop_reason,
        "stopAtSeconds": stop_at_seconds,
        "mapHistory": history,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按实时 TCP 重组语义审计自动停止边界",
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--full",
        action="store_true",
        help="重建完整战斗事件；仅用于解码器级诊断，通常无需使用",
    )
    args = parser.parse_args()
    audit = audit_capture_full if args.full else audit_capture
    results = [audit(path.expanduser().resolve()) for path in args.captures]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
