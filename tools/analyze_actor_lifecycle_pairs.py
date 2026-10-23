from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body,
    V576TransportReassembler,
    decode_captured_frame,
    split_v576_payload,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


def analyze(
    path: Path,
    transitions: dict[int, tuple[float, float]],
) -> dict[str, object]:
    reassembler = V576TransportReassembler()
    origin: float | None = None
    rows: list[dict[str, object]] = []

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        relevant: list[tuple[int, str]] = []
        for actor_id, (last_action, first_action) in transitions.items():
            if last_action - 0.5 <= seconds <= last_action + 3.0:
                relevant.append((actor_id, "death"))
            if first_action - 2.0 <= seconds <= first_action + 0.5:
                relevant.append((actor_id, "revive"))
        if not relevant:
            return
        direction = packet.inferred_direction()
        payloads = (
            split_v576_payload(packet.payload)
            if packet.transport.casefold() == "udp"
            else (packet.payload,)
        )
        for payload in payloads:
            signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
            variants = (
                ((phase, _decode_v576_body(payload, phase)) for phase in range(12))
                if packet.transport.casefold() == "udp"
                else ((-1, payload),)
            )
            for phase, decoded in variants:
                for actor_id, side in relevant:
                    marker = actor_id.to_bytes(4, "little")
                    offset = decoded.find(marker)
                    while offset >= 0:
                        rows.append(
                            {
                                "actor_id": f"0x{actor_id:08X}",
                                "side": side,
                                "seconds": round(seconds, 6),
                                "transport": packet.transport.casefold(),
                                "direction": direction,
                                "length": len(payload),
                                "signature": signature,
                                "xor_phase": phase,
                                "actor_offset": offset,
                                "before_hex": decoded[max(0, offset - 10) : offset].hex(),
                                "after_hex": decoded[offset + 4 : offset + 36].hex(),
                            }
                        )
                        offset = decoded.find(marker, offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in reassembler.feed(packet):
            accept(logical_packet)
    for logical_packet in reassembler.flush():
        accept(logical_packet)

    layout_sides: dict[tuple[object, ...], dict[str, set[str]]] = defaultdict(
        lambda: {"death": set(), "revive": set()}
    )
    layout_counts: dict[tuple[object, ...], Counter[str]] = defaultdict(Counter)
    grouped_rows: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = (
            row["transport"],
            row["direction"],
            row["length"],
            row["signature"],
            row["xor_phase"],
            row["actor_offset"],
        )
        side = str(row["side"])
        layout_sides[key][side].add(str(row["actor_id"]))
        layout_counts[key][side] += 1
        grouped_rows[key].append(row)

    candidates = []
    for key, sides in layout_sides.items():
        common = sides["death"] & sides["revive"]
        if not common:
            continue
        candidates.append(
            {
                "transport": key[0],
                "direction": key[1],
                "length": key[2],
                "signature": key[3],
                "xor_phase": key[4],
                "actor_offset": key[5],
                "death_actors": sorted(sides["death"]),
                "revive_actors": sorted(sides["revive"]),
                "common_actors": sorted(common),
                "death_count": layout_counts[key]["death"],
                "revive_count": layout_counts[key]["revive"],
                "samples": sorted(
                    grouped_rows[key], key=lambda row: float(row["seconds"])
                )[:80],
            }
        )
    candidates.sort(
        key=lambda row: (
            -len(row["common_actors"]),
            -min(int(row["death_count"]), int(row["revive_count"])),
            int(row["length"]),
        )
    )

    def side_candidates(side: str) -> list[dict[str, object]]:
        result = []
        for key, sides in layout_sides.items():
            actors = sides[side]
            if not actors:
                continue
            side_rows = [
                row for row in grouped_rows[key] if row["side"] == side
            ]
            result.append(
                {
                    "transport": key[0],
                    "direction": key[1],
                    "length": key[2],
                    "signature": key[3],
                    "xor_phase": key[4],
                    "actor_offset": key[5],
                    "actors": sorted(actors),
                    "count": layout_counts[key][side],
                    "samples": sorted(
                        side_rows, key=lambda row: float(row["seconds"])
                    )[:80],
                }
            )
        result.sort(
            key=lambda row: (
                -len(row["actors"]),
                -int(row["count"]),
                int(row["length"]),
            )
        )
        return result

    return {
        "capture": str(path),
        "transitions": {
            f"0x{actor_id:08X}": {
                "last_action": values[0],
                "first_action": values[1],
            }
            for actor_id, values in transitions.items()
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
        "death_candidates": side_candidates("death"),
        "revive_candidates": side_candidates("revive"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--transition",
        action="append",
        required=True,
        help="ACTOR_ID:LAST_ACTION_SECONDS:FIRST_ACTION_SECONDS",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    transitions = {}
    for value in args.transition:
        actor, last_action, first_action = value.split(":")
        transitions[int(actor, 0)] = (float(last_action), float(first_action))
    result = analyze(args.capture, transitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"candidates={result['candidate_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
