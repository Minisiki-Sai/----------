from __future__ import annotations

import argparse
import math
import struct
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.models import CombatEvent, PartyMemberEvent
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    _V576_BODY_XOR_KEY,
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder


MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")


@dataclass(slots=True)
class LogicalRow:
    timestamp: float
    packet: object
    decoded: bytes
    phase: int


@dataclass(slots=True)
class HitRow:
    timestamp: float
    target_id: int
    damage: int
    packet_index: int


def _iter_packets(path: Path):
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError("not a DNDP1 capture")
        while header := handle.read(FRAME_HEADER.size):
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, _adapter, original_length, captured_length = FRAME_HEADER.unpack(
                header
            )
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical in transport.feed(packet):
                if ledger.accept(logical):
                    yield logical
        for logical in transport.flush():
            if ledger.accept(logical):
                yield logical


def _phase_and_body(payload: bytes, actor_ids: set[int]) -> tuple[int, bytes]:
    preferred = (
        _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(payload[5])
        if len(payload) > 5
        else None
    )
    phases = (preferred,) if preferred is not None else range(len(_V576_BODY_XOR_KEY))
    actor_bytes = tuple(actor.to_bytes(4, "little") for actor in actor_ids)
    best: tuple[int, int, bytes] | None = None
    for phase in phases:
        decoded = _decode_v576_body(payload, int(phase))
        score = sum(decoded.count(raw) for raw in actor_bytes)
        if best is None or score > best[0]:
            best = (score, int(phase), decoded)
    assert best is not None
    return best[1], best[2]


def _candidate_values(decoded: bytes, actor_offset: int) -> dict[tuple[str, int], float]:
    values: dict[tuple[str, int], float] = {}
    start = max(9, actor_offset - 24)
    end = min(len(decoded), actor_offset + 80)
    for offset in range(start, end):
        if offset + 2 <= len(decoded):
            values[("u16", offset - actor_offset)] = int.from_bytes(
                decoded[offset : offset + 2], "little"
            )
        if offset + 4 <= len(decoded):
            raw = decoded[offset : offset + 4]
            values[("u32", offset - actor_offset)] = int.from_bytes(raw, "little")
            value = struct.unpack("<f", raw)[0]
            if math.isfinite(value):
                values[("f32", offset - actor_offset)] = value
    return values


def analyze(path: Path, data_root: Path) -> None:
    decoder = _V576PacketEventDecoder(data_root)
    packets = list(_iter_packets(path))
    raw_rows: list[tuple[object, list[object]]] = []
    player_ids: set[int] = set()
    hits: list[HitRow] = []
    first_timestamp = packets[0].timestamp_seconds if packets else 0.0

    for index, packet in enumerate(packets):
        events = decoder.feed_events(packet)
        raw_rows.append((packet, events))
        for event in events:
            if isinstance(event, PartyMemberEvent):
                try:
                    player_ids.add(int(event.actor_id, 16))
                except ValueError:
                    pass
            elif (
                isinstance(event, CombatEvent)
                and event.attribution_method == "incoming_damage"
            ):
                try:
                    target_id = int(event.target_id, 16)
                except ValueError:
                    continue
                player_ids.add(target_id)
                hits.append(
                    HitRow(
                        timestamp=first_timestamp + event.timestamp_ms / 1000.0,
                        target_id=target_id,
                        damage=event.amount,
                        packet_index=index,
                    )
                )

    logical: list[LogicalRow] = []
    for packet, _events in raw_rows:
        phase, decoded = _phase_and_body(packet.payload, player_ids)
        logical.append(LogicalRow(packet.timestamp_seconds, packet, decoded, phase))

    observations: dict[
        tuple[str, int, int, int],
        list[tuple[float, int, int, float, bytes, dict[tuple[str, int], float]]],
    ] = defaultdict(list)
    signatures_by_hit: list[tuple[float, int, int, list[str]]] = []
    for hit in hits:
        nearby: list[str] = []
        for index in range(hit.packet_index, min(len(logical), hit.packet_index + 24)):
            row = logical[index]
            delta = row.timestamp - hit.timestamp
            if delta > 0.20:
                break
            if delta < -0.01 or row.packet.inferred_direction() != "server_to_client":
                continue
            actor_raw = hit.target_id.to_bytes(4, "little")
            actor_offset = row.decoded.find(actor_raw, 9)
            if actor_offset < 0:
                continue
            signature = row.packet.payload[5:9].hex() if len(row.packet.payload) >= 9 else ""
            key = (signature, len(row.decoded), actor_offset, row.phase)
            observations[key].append(
                (
                    hit.timestamp,
                    hit.target_id,
                    hit.damage,
                    delta,
                    row.decoded,
                    _candidate_values(row.decoded, actor_offset),
                )
            )
            nearby.append(f"{signature}/{len(row.decoded)}/actor+{actor_offset}/p{row.phase}")
        signatures_by_hit.append((hit.timestamp, hit.target_id, hit.damage, nearby))

    print(f"capture={path.name}")
    print(f"logical_packets={len(logical)} players={len(player_ids)} incoming_hits={len(hits)}")
    print("players=" + ",".join(f"0x{value:08X}" for value in sorted(player_ids)))
    print("\nTop packet families following proven incoming damage:")
    ranked_groups = sorted(observations.items(), key=lambda item: len(item[1]), reverse=True)
    for key, rows in ranked_groups[:20]:
        print(f"  {key}: observations={len(rows)}")

    print("\nDecoded examples for the most frequent compact candidate families:")
    compact_groups = [item for item in ranked_groups if item[0][1] <= 100]
    for key, rows in compact_groups[:8]:
        print(f"  family={key}")
        seen: set[bytes] = set()
        for timestamp, target_id, damage, delta, decoded, _values in rows:
            if decoded in seen:
                continue
            seen.add(decoded)
            print(
                f"    target=0x{target_id:08X} damage={damage} dt={delta:.4f}s "
                f"hex={decoded.hex(' ')}"
            )
            if len(seen) >= 3:
                break

    scored: list[tuple[float, tuple[str, int, int, int], tuple[str, int], list[float]]] = []
    for key, rows in observations.items():
        if len(rows) < 4:
            continue
        field_keys = set.intersection(
            *(set(values) for _time, _target, _damage, _delta, _decoded, values in rows)
        )
        for field_key in field_keys:
            values = [
                values[field_key]
                for _time, _target, _damage, _delta, _decoded, values in rows
            ]
            finite = [value for value in values if math.isfinite(value)]
            if len(finite) != len(values) or len(set(finite)) < 3:
                continue
            field_type, _relative_offset = field_key
            if field_type == "f32":
                plausible = sum(0.0 <= value <= 100.0 for value in finite) / len(finite)
            elif field_type == "u16":
                plausible = sum(0 <= value <= 10_000 for value in finite) / len(finite)
            else:
                plausible = sum(0 <= value <= 10_000_000 for value in finite) / len(finite)
            changes = sum(left != right for left, right in zip(finite, finite[1:]))
            decreases = sum(left > right for left, right in zip(finite, finite[1:]))
            score = plausible * 4.0 + changes / len(finite) + decreases / len(finite)
            if plausible >= 0.70:
                scored.append((score, key, field_key, finite))

    print("\nTop varying fields near the target actor (exploratory, not yet HP proof):")
    for score, key, field_key, values in sorted(scored, reverse=True)[:30]:
        preview = ",".join(f"{value:.5g}" for value in values[:10])
        print(f"  score={score:.2f} packet={key} field={field_key} values=[{preview}]")

    missing = sum(not nearby for _time, _target, _damage, nearby in signatures_by_hit)
    print(f"\nIncoming hits without a same-target S2C packet in +200ms: {missing}/{len(hits)}")
    print("First 12 hit-to-packet links:")
    for timestamp, target_id, damage, nearby in signatures_by_hit[:12]:
        relative = timestamp - first_timestamp
        print(
            f"  t={relative:.3f}s target=0x{target_id:08X} damage={damage} "
            f"packets={nearby[:5]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.capture, args.data_root)


if __name__ == "__main__":
    main()
