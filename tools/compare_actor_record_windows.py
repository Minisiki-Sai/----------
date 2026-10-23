from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    V576TransportReassembler,
    decode_captured_frame,
    split_v576_payload,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


def scan(
    path: Path,
    actors: set[int],
    start: float,
    end: float,
) -> dict[str, dict[str, object]]:
    reassembler = V576TransportReassembler()
    origin: float | None = None
    counts: Counter[str] = Counter()
    actor_sets: dict[str, set[int]] = defaultdict(set)
    samples: dict[str, list[dict[str, object]]] = defaultdict(list)

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        if not start <= seconds <= end:
            return
        if packet.transport.casefold() != "udp" or packet.inferred_direction() != "server_to_client":
            return
        for payload in split_v576_payload(packet.payload):
            if len(payload) <= 9:
                continue
            phase = _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(payload[5])
            if phase is None:
                continue
            decoded = _decode_v576_body(payload, phase)
            for actor_id in actors:
                marker = actor_id.to_bytes(4, "little")
                offset = decoded.find(marker, 14)
                while offset >= 0:
                    suffix = decoded[offset + 4 : offset + 7].hex()
                    key = suffix
                    counts[key] += 1
                    actor_sets[key].add(actor_id)
                    if len(samples[key]) < 12:
                        samples[key].append(
                            {
                                "seconds": round(seconds, 6),
                                "actor_id": f"0x{actor_id:08X}",
                                "length": len(payload),
                                "signature": payload[5:9].hex(),
                                "actor_offset": offset,
                                "after_hex": decoded[offset + 4 : offset + 20].hex(),
                            }
                        )
                    offset = decoded.find(marker, offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)
    return {
        key: {
            "count": count,
            "actor_count": len(actor_sets[key]),
            "actors": [f"0x{value:08X}" for value in sorted(actor_sets[key])],
            "samples": samples[key],
        }
        for key, count in counts.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("positive", type=Path)
    parser.add_argument("control", type=Path)
    parser.add_argument("--positive-actor", action="append", required=True)
    parser.add_argument("--control-actor", action="append", required=True)
    parser.add_argument("--positive-window", required=True)
    parser.add_argument("--control-window", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pstart, pend = (float(value) for value in args.positive_window.split(":"))
    cstart, cend = (float(value) for value in args.control_window.split(":"))
    positive = scan(
        args.positive,
        {int(value, 0) for value in args.positive_actor},
        pstart,
        pend,
    )
    control = scan(
        args.control,
        {int(value, 0) for value in args.control_actor},
        cstart,
        cend,
    )
    candidates = []
    for key, row in positive.items():
        other = control.get(key, {})
        candidates.append(
            {
                "record_hex": key,
                "positive_count": row["count"],
                "positive_actor_count": row["actor_count"],
                "positive_actors": row["actors"],
                "control_count": other.get("count", 0),
                "control_actor_count": other.get("actor_count", 0),
                "control_actors": other.get("actors", []),
                "samples": row["samples"],
            }
        )
    candidates.sort(
        key=lambda row: (
            int(row["control_actor_count"]),
            -int(row["positive_actor_count"]),
            -int(row["positive_count"]),
        )
    )
    result = {"candidates": candidates}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"candidates={len(candidates)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
