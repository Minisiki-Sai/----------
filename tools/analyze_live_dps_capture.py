from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_v576_payload,
    read_pcapng_packets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Dragon Nest v576 capture")
    parser.add_argument("capture", type=Path)
    parser.add_argument("--server-ip", default="106.54.209.56")
    parser.add_argument("--server-port", type=int)
    parser.add_argument("--all", action="store_true", help="show undecoded packets too")
    args = parser.parse_args()

    packets = read_pcapng_packets(args.capture)
    if not packets:
        print("No TCP/UDP packets found")
        return 1
    start = packets[0].timestamp_seconds
    decoded_count = 0
    for packet in packets:
        direction = packet.direction(args.server_ip, args.server_port)
        decoded = decode_v576_payload(packet.payload, direction)
        if decoded is None and not args.all:
            continue
        elapsed = packet.timestamp_seconds - start
        prefix = (
            f"{elapsed:9.3f}s {direction:16s} {packet.transport:3s} "
            f"{packet.source_port:5d}->{packet.destination_port:5d} "
            f"len={len(packet.payload):4d}"
        )
        if decoded is None:
            print(prefix)
            continue
        decoded_count += 1
        details = []
        if decoded.source_name:
            details.append(f"name={decoded.source_name}")
        if decoded.source_actor_id is not None:
            details.append(f"source=0x{decoded.source_actor_id:08X}")
        if decoded.observer_actor_id is not None:
            details.append(f"observer=0x{decoded.observer_actor_id:08X}")
        if decoded.skill_id is not None:
            details.append(f"skill_id={decoded.skill_id}")
        if decoded.damage is not None:
            details.append(f"damage={decoded.damage}")
        print(f"{prefix} {decoded.kind} {decoded.layout} {' '.join(details)}")

    print(f"Packets: {len(packets)}, decoded candidates: {decoded_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
