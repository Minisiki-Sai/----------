from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import defaultdict
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


def _u16(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 2 > len(data):
        return None
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def analyze(
    path: Path,
    player_ids: tuple[int, ...],
    windows: tuple[tuple[str, float, float], ...],
) -> dict[str, object]:
    reassembler = V576TransportReassembler()
    origin: float | None = None
    rows: list[dict[str, object]] = []

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        matched_windows = [
            name for name, start, end in windows if start <= seconds <= end
        ]
        if not matched_windows:
            return
        direction = packet.inferred_direction()
        payloads = (
            split_v576_payload(packet.payload)
            if packet.transport.casefold() == "udp"
            else (packet.payload,)
        )
        for payload in payloads:
            signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
            decoded_variants = (
                ((phase, _decode_v576_body(payload, phase)) for phase in range(12))
                if packet.transport.casefold() == "udp"
                else ((-1, payload),)
            )
            for phase, decoded in decoded_variants:
                for player_id in player_ids:
                    marker = player_id.to_bytes(4, "little")
                    offset = decoded.find(marker)
                    while offset >= 0:
                        before = decoded[max(0, offset - 12) : offset]
                        after = decoded[offset + 4 : offset + 44]
                        rows.append(
                            {
                                "windows": matched_windows,
                                "seconds": round(seconds, 6),
                                "transport": packet.transport.casefold(),
                                "direction": direction,
                                "length": len(payload),
                                "signature": signature,
                                "xor_phase": phase,
                                "actor_offset": offset,
                                "actor_id": f"0x{player_id:08X}",
                                "before_hex": before.hex(),
                                "after_hex": after.hex(),
                                "u8_after": list(after[:24]),
                                "u16_after": [
                                    _u16(decoded, offset + delta)
                                    for delta in range(4, 28, 2)
                                ],
                                "u32_after": [
                                    _u32(decoded, offset + delta)
                                    for delta in range(4, 40, 4)
                                ],
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

    grouped: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = (
            row["transport"],
            row["direction"],
            row["length"],
            row["signature"],
            row["xor_phase"],
            row["actor_offset"],
        )
        grouped[key].append(row)
    layouts = []
    for key, matches in grouped.items():
        actors = sorted({str(row["actor_id"]) for row in matches})
        window_names = sorted(
            {str(name) for row in matches for name in row["windows"]}
        )
        layouts.append(
            {
                "transport": key[0],
                "direction": key[1],
                "length": key[2],
                "signature": key[3],
                "xor_phase": key[4],
                "actor_offset": key[5],
                "windows": window_names,
                "actor_count": len(actors),
                "actors": actors,
                "match_count": len(matches),
                "matches": sorted(matches, key=lambda row: float(row["seconds"])),
            }
        )
    layouts.sort(
        key=lambda row: (
            -len(row["windows"]),
            -int(row["actor_count"]),
            -int(row["match_count"]),
            int(row["length"]),
        )
    )
    return {
        "capture": str(path),
        "windows": [
            {"name": name, "start": start, "end": end}
            for name, start, end in windows
        ],
        "layout_count": len(layouts),
        "layouts": layouts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--player-id", action="append", required=True)
    parser.add_argument("--window", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    windows = []
    for raw in args.window:
        name, start, end = raw.rsplit(":", 2)
        windows.append((name, float(start), float(end)))
    result = analyze(
        args.capture,
        tuple(int(value, 0) for value in args.player_id),
        tuple(windows),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"layouts={result['layout_count']} output={args.output}")


if __name__ == "__main__":
    main()
