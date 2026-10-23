from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    decode_v576_lobby_return,
)
from tools.analyze_hub_transition_evidence import _read_logical_packets


def summarize(
    path: Path,
    *,
    map_ids: tuple[int, ...],
    before: float,
    after: float,
) -> None:
    packets = _read_logical_packets(path)
    if not packets:
        print(f"capture={path} packets=0")
        return
    origin = float(packets[0].timestamp_seconds)
    ack_times = [
        float(packet.timestamp_seconds)
        for packet in packets
        if decode_v576_lobby_return(
            packet.payload,
            packet.inferred_direction(),
        )
        is not None
    ]
    print(f"capture={path}")
    for ack_at in ack_times:
        rows: list[tuple[float, str, int, str, tuple[tuple[int, int], ...], str]] = []
        shapes: Counter[tuple[str, int, str, tuple[tuple[int, int], ...]]] = Counter()
        for packet in packets:
            delta = float(packet.timestamp_seconds) - ack_at
            if delta < -before or delta > after:
                continue
            if str(packet.transport or "").upper() != "UDP" or len(packet.payload) < 9:
                continue
            phase = _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(int(packet.payload[5]))
            if phase is None:
                continue
            decoded = _decode_v576_body(packet.payload, phase)
            matches: list[tuple[int, int]] = []
            for map_id in map_ids:
                token = int(map_id).to_bytes(4, "little")
                start = 0
                while True:
                    offset = decoded.find(token, start)
                    if offset < 0:
                        break
                    matches.append((int(map_id), int(offset)))
                    start = offset + 1
            if not matches:
                continue
            direction = packet.inferred_direction() or "unknown"
            signature = packet.payload[5:9].hex(" ")
            key = (direction, len(packet.payload), signature, tuple(matches))
            shapes[key] += 1
            rows.append(
                (
                    delta,
                    direction,
                    len(packet.payload),
                    signature,
                    tuple(matches),
                    decoded[:160].hex(" "),
                )
            )
        print(f"  ack={ack_at - origin:.6f}")
        print("  shapes:")
        for key, count in shapes.most_common():
            print(f"    count={count} {key}")
        print("  first-rows:")
        seen: set[tuple[str, int, str, tuple[tuple[int, int], ...]]] = set()
        for delta, direction, length, signature, matches, sample in rows:
            key = (direction, length, signature, matches)
            if key in seen:
                continue
            seen.add(key)
            print(
                f"    {delta:+.6f} {direction} len={length} sig={signature} "
                f"matches={matches} {sample}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare decoded UDP map-ID candidates around scene returns."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--map-id", action="append", type=int, default=[])
    parser.add_argument("--before", type=float, default=1.0)
    parser.add_argument("--after", type=float, default=45.0)
    args = parser.parse_args()
    map_ids = tuple(args.map_id or (11, 36))
    for path in args.captures:
        summarize(
            path.expanduser().resolve(),
            map_ids=map_ids,
            before=max(0.0, float(args.before)),
            after=max(0.0, float(args.after)),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
