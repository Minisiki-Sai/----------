from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_payload,
    split_v576_payload,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


def scan(path: Path, start: float, end: float) -> Counter[tuple[str, str, int, str, str]]:
    result: Counter[tuple[str, str, int, str, str]] = Counter()
    reassembler = V576TransportReassembler()
    origin: float | None = None

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        if not start <= seconds <= end:
            return
        transport = packet.transport.casefold()
        direction = packet.inferred_direction()
        payloads = split_v576_payload(packet.payload) if transport == "udp" else (packet.payload,)
        for payload in payloads:
            if transport == "udp":
                signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
                decoded = decode_v576_payload(payload, direction)
                kind = decoded.kind if decoded is not None else "unknown"
            else:
                signature = payload[3:7].hex() if len(payload) >= 7 else payload.hex()
                kind = "tcp"
            result[(transport, direction, len(payload), signature, kind)] += 1

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("positive", type=Path)
    parser.add_argument("control", type=Path)
    parser.add_argument("--positive-window", required=True)
    parser.add_argument("--control-window", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pstart, pend = (float(value) for value in args.positive_window.split(":"))
    cstart, cend = (float(value) for value in args.control_window.split(":"))
    positive = scan(args.positive, pstart, pend)
    control = scan(args.control, cstart, cend)
    pduration = max(0.001, pend - pstart)
    cduration = max(0.001, cend - cstart)
    rows = []
    for key, count in positive.items():
        other = control.get(key, 0)
        rows.append(
            {
                "transport": key[0],
                "direction": key[1],
                "length": key[2],
                "signature": key[3],
                "kind": key[4],
                "positive_count": count,
                "control_count": other,
                "positive_rate": round(count / pduration, 4),
                "control_rate": round(other / cduration, 4),
            }
        )
    rows.sort(
        key=lambda row: (
            int(row["control_count"]) > 0,
            -int(row["positive_count"]),
            str(row["transport"]),
            int(row["length"]),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
