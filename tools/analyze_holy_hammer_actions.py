from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent
from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_BODY_XOR_KEY,
    _decode_v576_body,
    decode_captured_frame,
    split_v576_payload,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


ACTION_MARKERS = (
    b"\x0e\x23\x00",
    b"\x0e\x1f\x00",
    b"\x12\x24\x00",
    b"\x12\x20\x00",
)


def _actor_matches(value: str, actor_id: int) -> bool:
    try:
        return int(str(value), 16) == actor_id
    except (TypeError, ValueError):
        return False


def _raw_actor_actions(path: Path, actor_id: int) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    seen_packets: dict[tuple[object, ...], tuple[float, int]] = {}
    first_timestamp: float | None = None
    for timestamp, adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        if first_timestamp is None:
            first_timestamp = packet.timestamp_seconds
        for payload in split_v576_payload(packet.payload):
            key = (
                packet.transport,
                packet.source_ip,
                packet.destination_ip,
                packet.source_port,
                packet.destination_port,
                payload,
            )
            previous = seen_packets.get(key)
            if (
                previous is not None
                and previous[1] != adapter
                and abs(timestamp - previous[0]) <= 0.020
            ):
                continue
            seen_packets[key] = (timestamp, adapter)

            direction = packet.inferred_direction()
            if direction not in {"client_to_server", "server_to_client"}:
                continue
            by_phase: dict[int, set[tuple[int, int, bytes]]] = defaultdict(set)
            for phase in range(len(_V576_BODY_XOR_KEY)):
                decoded = _decode_v576_body(payload, phase)
                client_actor = int.from_bytes(decoded[10:14], "little")
                for marker in ACTION_MARKERS:
                    offset = decoded.find(marker, 14)
                    while offset >= 0 and offset + 7 <= len(decoded):
                        source_actor = (
                            int.from_bytes(decoded[offset - 4 : offset], "little")
                            if direction == "server_to_client" and offset >= 4
                            else client_actor
                        )
                        if source_actor == actor_id:
                            skill_id = int.from_bytes(
                                decoded[offset + 3 : offset + 7], "little"
                            )
                            by_phase[phase].add((offset, skill_id, marker))
                        offset = decoded.find(marker, offset + 1)
            # A valid action identity should survive in one XOR phase only.
            identities: dict[tuple[int, int, bytes], set[int]] = defaultdict(set)
            for phase, records in by_phase.items():
                for record in records:
                    identities[record].add(phase)
            for (offset, skill_id, marker), phases in identities.items():
                if len(phases) != 1:
                    continue
                actions.append(
                    {
                        "time_ms": round(
                            (packet.timestamp_seconds - (first_timestamp or 0.0))
                            * 1000
                        ),
                        "skill_id": skill_id,
                        "direction": direction,
                        "signature": (
                            payload[5:9].hex() if len(payload) >= 9 else ""
                        ),
                        "marker": marker.hex(),
                        "phase": next(iter(phases)),
                        "offset": offset,
                        "packet_length": len(payload),
                    }
                )
    unique: dict[tuple[int, int, str], dict[str, object]] = {}
    for action in actions:
        identity = (
            int(action["time_ms"]),
            int(action["skill_id"]),
            str(action["direction"]),
        )
        unique.setdefault(identity, action)
    return sorted(unique.values(), key=lambda item: int(item["time_ms"]))


def _raw_client_packets(path: Path, actor_id: int) -> list[dict[str, object]]:
    packets: list[dict[str, object]] = []
    seen_packets: dict[tuple[object, ...], tuple[float, int]] = {}
    first_timestamp: float | None = None
    for timestamp, adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        if first_timestamp is None:
            first_timestamp = packet.timestamp_seconds
        direction = packet.inferred_direction()
        if direction not in {"client_to_server", "server_to_client"}:
            continue
        for payload in split_v576_payload(packet.payload):
            key = (
                packet.transport,
                packet.source_ip,
                packet.destination_ip,
                packet.source_port,
                packet.destination_port,
                payload,
            )
            previous = seen_packets.get(key)
            if (
                previous is not None
                and previous[1] != adapter
                and abs(timestamp - previous[0]) <= 0.020
            ):
                continue
            seen_packets[key] = (timestamp, adapter)
            phases = []
            positions_by_phase: dict[int, list[int]] = {}
            for phase in range(len(_V576_BODY_XOR_KEY)):
                decoded = _decode_v576_body(payload, phase)
                positions = [
                    offset
                    for offset in range(10, len(decoded) - 3)
                    if int.from_bytes(decoded[offset : offset + 4], "little")
                    == actor_id
                ]
                if positions:
                    phases.append(phase)
                    positions_by_phase[phase] = positions
            if len(phases) != 1:
                continue
            packets.append(
                {
                    "time_ms": round(
                        (packet.timestamp_seconds - (first_timestamp or 0.0))
                        * 1000
                    ),
                    "length": len(payload),
                    "signature": payload[5:9].hex() if len(payload) >= 9 else "",
                    "direction": direction,
                    "phase": phases[0],
                    "actor_positions": positions_by_phase[phases[0]],
                }
            )
    return packets


def _cluster_times(values: list[int], tolerance_ms: int) -> list[list[int]]:
    clusters: list[list[int]] = []
    for value in sorted(values):
        if not clusters or value - clusters[-1][-1] > tolerance_ms:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return clusters


def analyze(path: Path, data_root: Path, actor_id: int) -> dict[str, object]:
    source = V576DiagnosticReplaySource(path, data_root)
    events = source.poll()
    casts = [
        event
        for event in events
        if isinstance(event, SkillCastEvent)
        and _actor_matches(event.source_id, actor_id)
    ]
    hits = [
        event
        for event in events
        if isinstance(event, CombatEvent)
        and _actor_matches(event.source_id, actor_id)
        and str(event.skill_id) == "3224"
        and not event.healing
    ]
    raw_actions = _raw_actor_actions(path, actor_id)
    raw_client_packets = _raw_client_packets(path, actor_id)
    action_counts = Counter(int(action["skill_id"]) for action in raw_actions)

    hit_clusters = _cluster_times([event.timestamp_ms for event in hits], 120)
    packet_signature_totals = Counter(
        (
            str(packet["direction"]),
            int(packet["length"]),
            str(packet["signature"]),
        )
        for packet in raw_client_packets
    )
    preceding_signatures: Counter[tuple[str, int, str]] = Counter()
    closest_signature: Counter[tuple[str, int, str]] = Counter()
    lag_samples: dict[tuple[str, int, str], list[int]] = defaultdict(list)
    for cluster in hit_clusters:
        cluster_start = cluster[0]
        candidates = [
            packet
            for packet in raw_client_packets
            if -600 <= int(packet["time_ms"]) - cluster_start <= -20
        ]
        seen_signatures: set[tuple[int, str]] = set()
        for packet in candidates:
            signature = (
                str(packet["direction"]),
                int(packet["length"]),
                str(packet["signature"]),
            )
            seen_signatures.add(signature)
            lag_samples[signature].append(int(packet["time_ms"]) - cluster_start)
        preceding_signatures.update(seen_signatures)
        if candidates:
            closest = min(
                candidates,
                key=lambda packet: abs(int(packet["time_ms"]) - cluster_start),
            )
            closest_signature[
                (
                    str(closest["direction"]),
                    int(closest["length"]),
                    str(closest["signature"]),
                )
            ] += 1

    windows: list[dict[str, object]] = []
    for action in raw_actions:
        action_time = int(action["time_ms"])
        related_hits = [
            hit
            for hit in hits
            if -100 <= hit.timestamp_ms - action_time <= 1800
        ]
        nearby_actions = [
            other
            for other in raw_actions
            if -900 <= int(other["time_ms"]) - action_time <= 900
        ]
        if int(action["skill_id"]) in {3224, 733042} or related_hits:
            windows.append(
                {
                    **action,
                    "hit_offsets_ms": [
                        hit.timestamp_ms - action_time for hit in related_hits
                    ],
                    "nearby_actions": [
                        {
                            "offset_ms": int(other["time_ms"]) - action_time,
                            "skill_id": int(other["skill_id"]),
                        }
                        for other in nearby_actions
                        if other is not action
                    ],
                }
            )

    return {
        "capture": str(path),
        "actor_id": f"0x{actor_id:08X}",
        "known_cast_counts": [
            {
                "skill_id": identity[0],
                "skill_name": identity[1],
                "packet_kind": identity[2],
                "count": count,
            }
            for identity, count in Counter(
                (str(event.skill_id), event.skill_name, event.packet_kind)
                for event in casts
            ).most_common()
        ],
        "holy_hammer_damage_hits": len(hits),
        "holy_hammer_damage_clusters_120ms": len(hit_clusters),
        "raw_action_counts": dict(action_counts.most_common()),
        "client_packet_signatures_before_clusters": [
            {
                "direction": signature[0],
                "packet_length": signature[1],
                "signature": signature[2],
                "cluster_count": count,
                "total_actor_packets": packet_signature_totals[signature],
                "cluster_ratio": round(
                    count / max(1, packet_signature_totals[signature]), 4
                ),
                "closest_count": closest_signature[signature],
                "median_lag_ms": sorted(lag_samples[signature])[
                    len(lag_samples[signature]) // 2
                ],
            }
            for signature, count in preceding_signatures.most_common(30)
        ],
        "candidate_windows": windows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("actor_id", type=lambda value: int(value, 0))
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("dragon_nest_builder/data"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.capture, args.data_root.resolve(), args.actor_id)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
