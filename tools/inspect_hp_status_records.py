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


def inspect(path: Path, player_ids: set[int]) -> dict[str, object]:
    reassembler = V576TransportReassembler()
    origin: float | None = None
    rows: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        if (
            packet.transport.casefold() != "udp"
            or packet.inferred_direction() != "server_to_client"
        ):
            return
        seconds = packet.timestamp_seconds - origin
        for payload in split_v576_payload(packet.payload):
            signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
            for phase in range(12):
                decoded = _decode_v576_body(payload, phase)
                observer_actor_id = int.from_bytes(decoded[10:14], "little")
                for subtype in range(5):
                    marker = bytes((0x1C, 0x1B, 0x00, subtype))
                    marker_offset = 9
                    while True:
                        marker_offset = decoded.find(marker, marker_offset)
                        if marker_offset < 0:
                            break
                        if marker_offset < 4 or marker_offset + 30 > len(decoded):
                            marker_offset += 1
                            continue
                        target_actor_id = int.from_bytes(
                            decoded[marker_offset - 4 : marker_offset], "little"
                        )
                        if target_actor_id not in player_ids:
                            marker_offset += 1
                            continue
                        signed_delta = int.from_bytes(
                            decoded[marker_offset + 4 : marker_offset + 12],
                            "little",
                            signed=True,
                        )
                        source_actor_id = int.from_bytes(
                            decoded[marker_offset + 12 : marker_offset + 16],
                            "little",
                        )
                        tail = decoded[marker_offset + 16 : marker_offset + 30]
                        key = (
                            round(seconds, 6),
                            len(payload),
                            signature,
                            phase,
                            marker_offset,
                            target_actor_id,
                            subtype,
                            signed_delta,
                            source_actor_id,
                            tail,
                        )
                        if key not in seen:
                            seen.add(key)
                            rows.append(
                                {
                                    "seconds": round(seconds, 6),
                                    "length": len(payload),
                                    "signature": signature,
                                    "xor_phase": phase,
                                    "observer_actor_id": f"0x{observer_actor_id:08X}",
                                    "target_actor_id": f"0x{target_actor_id:08X}",
                                    "subtype": subtype,
                                    "signed_delta": signed_delta,
                                    "source_actor_id": f"0x{source_actor_id:08X}",
                                    "tail_hex": tail.hex(),
                                    "marker_offset": marker_offset,
                                }
                            )
                        marker_offset += 1

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in reassembler.feed(packet):
            accept(logical_packet)
    for logical_packet in reassembler.flush():
        accept(logical_packet)

    by_tail = Counter(
        (
            str(row["tail_hex"]),
            int(row["subtype"]),
            int(row["xor_phase"]),
        )
        for row in rows
    )
    by_actor: dict[str, int] = defaultdict(int)
    for row in rows:
        by_actor[str(row["target_actor_id"])] += 1
    return {
        "capture": str(path),
        "record_count": len(rows),
        "records_by_actor": dict(sorted(by_actor.items())),
        "tail_counts": [
            {
                "tail_hex": key[0],
                "subtype": key[1],
                "xor_phase": key[2],
                "count": count,
            }
            for key, count in by_tail.most_common()
        ],
        "records": sorted(rows, key=lambda row: float(row["seconds"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--player-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(
        args.capture,
        {int(value, 0) for value in args.player_id},
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"records={result['record_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
