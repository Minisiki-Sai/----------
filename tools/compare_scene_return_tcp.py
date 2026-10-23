from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import decode_v576_lobby_return
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)
from tools.analyze_hub_transition_evidence import _read_logical_packets


def summarize(path: Path, *, before: float, after: float) -> None:
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
        rows: list[tuple[float, str, str, str, int, str]] = []
        counts: Counter[tuple[str, str, int, str]] = Counter()
        for packet in packets:
            delta = float(packet.timestamp_seconds) - ack_at
            if delta < -before or delta > after:
                continue
            if str(packet.transport or "").upper() != "TCP":
                continue
            direction = packet.inferred_direction() or "unknown"
            decoded_row = (
                decode_registered_server_tcp_envelope(
                    packet.payload,
                    connection_context=packet,
                )
                if direction == "server_to_client"
                else decode_registered_tcp_envelope(packet.payload)
            )
            if decoded_row is None:
                continue
            decoded = decoded_row[0]
            opcode = decoded[2:4].hex(" ") if len(decoded) >= 4 else "short"
            endpoint = (
                f"{packet.source_ip}:{packet.source_port}>"
                f"{packet.destination_ip}:{packet.destination_port}"
            )
            counts[(direction, opcode, len(decoded), endpoint)] += 1
            if opcode not in {"07 0c", "07 0d"}:
                rows.append(
                    (
                        delta,
                        direction,
                        opcode,
                        endpoint,
                        len(decoded),
                        decoded[:128].hex(" "),
                    )
                )
        print(f"  ack={ack_at - origin:.6f}")
        print("  shapes:")
        for key, count in counts.most_common():
            print(f"    count={count} {key}")
        print("  ordered-non-list-records:")
        for delta, direction, opcode, endpoint, length, sample in rows:
            print(
                f"    {delta:+.6f} {direction} {opcode} len={length} "
                f"{endpoint} {sample}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare decoded TCP records around scene-return acknowledgements."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--before", type=float, default=2.0)
    parser.add_argument("--after", type=float, default=30.0)
    args = parser.parse_args()
    for path in args.captures:
        summarize(
            path.expanduser().resolve(),
            before=max(0.0, float(args.before)),
            after=max(0.0, float(args.after)),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
