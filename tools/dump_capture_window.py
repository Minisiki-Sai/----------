from __future__ import annotations

import argparse
import json
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--length", type=int, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

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
        payloads = (
            split_v576_payload(packet.payload)
            if packet.transport.casefold() == "udp"
            else (packet.payload,)
        )
        for payload in payloads:
            if args.length and len(payload) not in args.length:
                continue
            variants = []
            if packet.transport.casefold() == "udp" and len(payload) >= 9:
                for phase in range(12):
                    decoded = _decode_v576_body(payload, phase)
                    variants.append(
                        {"phase": phase, "decoded_hex": decoded.hex()}
                    )
            rows.append(
                {
                    "seconds": round(seconds, 6),
                    "transport": packet.transport.casefold(),
                    "direction": packet.inferred_direction(),
                    "length": len(payload),
                    "payload_hex": payload.hex(),
                    "variants": variants,
                }
            )

    for timestamp, _adapter, original_length, frame in read_trace(args.capture):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
