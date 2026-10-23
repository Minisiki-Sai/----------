from __future__ import annotations

import argparse
import json
import struct
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


def inspect(path: Path) -> dict[str, object]:
    reassembler = V576TransportReassembler()
    origin: float | None = None
    rows: list[dict[str, object]] = []

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        if packet.transport.casefold() != "udp":
            return
        seconds = packet.timestamp_seconds - origin
        for payload in split_v576_payload(packet.payload):
            if len(payload) != 25 or payload[5:9] != bytes.fromhex("181a1600"):
                continue
            decoded = _decode_v576_body(payload, 0)
            actor_id = struct.unpack_from("<I", decoded, 14)[0]
            status = decoded[18:21]
            value = struct.unpack_from("<I", decoded, 21)[0]
            rows.append(
                {
                    "seconds": round(seconds, 6),
                    "direction": packet.inferred_direction(),
                    "actor_id": f"0x{actor_id:08X}",
                    "status_hex": status.hex(),
                    "value": value,
                    "payload_hex": payload.hex(),
                    "decoded_hex": decoded.hex(),
                }
            )

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in reassembler.feed(packet):
            accept(logical_packet)
    for logical_packet in reassembler.flush():
        accept(logical_packet)

    by_actor: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_actor[str(row["actor_id"])].append(row)
    actors = []
    for actor_id, actor_rows in by_actor.items():
        values = Counter(int(row["value"]) for row in actor_rows)
        statuses = Counter(str(row["status_hex"]) for row in actor_rows)
        transitions = []
        previous: tuple[str, int] | None = None
        for row in actor_rows:
            state = (str(row["status_hex"]), int(row["value"]))
            if state != previous:
                transitions.append(
                    {
                        "seconds": row["seconds"],
                        "status_hex": state[0],
                        "value": state[1],
                    }
                )
                previous = state
        actors.append(
            {
                "actor_id": actor_id,
                "count": len(actor_rows),
                "first_seconds": actor_rows[0]["seconds"],
                "last_seconds": actor_rows[-1]["seconds"],
                "status_counts": dict(statuses),
                "value_one_count": values.get(1, 0),
                "nontrivial_values": [
                    {"value": value, "count": count}
                    for value, count in values.most_common(12)
                    if value not in (0, 1)
                ],
                "transitions": transitions,
            }
        )
    actors.sort(key=lambda item: (-int(item["count"]), str(item["actor_id"])))
    return {
        "capture": str(path),
        "packet_count": len(rows),
        "actor_count": len(actors),
        "actors": actors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"packets={result['packet_count']} actors={result['actor_count']} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
