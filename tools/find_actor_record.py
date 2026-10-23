from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--actor", action="append", required=True)
    parser.add_argument("--record", required=True, help="Hex bytes after actor id")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=float("inf"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    actors = {int(value, 0) for value in args.actor}
    record = bytes.fromhex(args.record)
    reassembler = V576TransportReassembler()
    origin: float | None = None
    rows: list[dict[str, object]] = []

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        if not args.start <= seconds <= args.end:
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
                marker = actor_id.to_bytes(4, "little") + record
                offset = decoded.find(marker, 9)
                while offset >= 0:
                    rows.append(
                        {
                            "seconds": round(seconds, 6),
                            "actor_id": f"0x{actor_id:08X}",
                            "length": len(payload),
                            "signature": payload[5:9].hex(),
                            "xor_phase": phase,
                            "actor_offset": offset,
                            "after_hex": decoded[offset + 4 : offset + 24].hex(),
                            "decoded_hex": decoded.hex(),
                        }
                    )
                    offset = decoded.find(marker, offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(args.capture):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)

    rows.sort(key=lambda row: (float(row["seconds"]), str(row["actor_id"])))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
