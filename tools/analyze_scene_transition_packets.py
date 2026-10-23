from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.dungeon_map_catalog import (
    load_dungeon_map_catalog,
)
from dragon_nest_builder.live_dps.protocol_v576 import (
    PacketRecord,
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_map_identity,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


def _read_logical_packets(path: Path) -> tuple[PacketRecord, ...]:
    transport = V576TransportReassembler()
    packets: list[PacketRecord] = []
    for timestamp, adapter, original_length, frame in read_trace(path):
        if adapter in {254, 255}:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        packets.extend(transport.feed(packet))
    packets.extend(transport.flush())
    return tuple(sorted(packets, key=lambda row: row.timestamp_seconds))


def _tcp_plaintext(packet: PacketRecord) -> bytes | None:
    if packet.transport.upper() != "TCP":
        return None
    direction = packet.inferred_direction()
    decoded = (
        decode_registered_server_tcp_envelope(
            packet.payload,
            connection_context=packet,
        )
        if direction == "server_to_client"
        else decode_registered_tcp_envelope(packet.payload)
    )
    return decoded[0] if decoded is not None else None


def _flow_label(packet: PacketRecord) -> str:
    return (
        f"{packet.source_ip}:{packet.source_port}>"
        f"{packet.destination_ip}:{packet.destination_port}"
    )


def _find_all(data: bytes, needle: bytes) -> tuple[int, ...]:
    offsets: list[int] = []
    cursor = 0
    while True:
        offset = data.find(needle, cursor)
        if offset < 0:
            return tuple(offsets)
        offsets.append(offset)
        cursor = offset + 1


def analyze(path: Path, window_seconds: float) -> None:
    packets = _read_logical_packets(path)
    if not packets:
        print(f"capture={path} packets=0")
        return
    origin = packets[0].timestamp_seconds
    map_events: list[tuple[float, int, bool, str]] = []
    for packet in packets:
        decoded = decode_v576_map_identity(
            packet.payload,
            packet.inferred_direction(),
            connection_context=packet,
        )
        if decoded is not None:
            map_events.append(
                (
                    packet.timestamp_seconds - origin,
                    int(decoded.map_id),
                    bool(decoded.active),
                    decoded.layout,
                )
            )
    print(f"capture={path}")
    print(f"duration={packets[-1].timestamp_seconds - origin:.6f}s packets={len(packets)}")
    print("map_events=" + " | ".join(
        f"{time_value:.6f}:{map_id}:{'enter' if active else 'leave'}:{layout}"
        for time_value, map_id, active, layout in map_events
    ))
    leave_times = [row[0] for row in map_events if not row[2]]
    if not leave_times:
        print("no exact map-leave event")
        return
    window_start = leave_times[-1]
    window_end = min(
        packets[-1].timestamp_seconds - origin,
        window_start + max(0.0, window_seconds),
    )
    selected = [
        packet
        for packet in packets
        if window_start
        <= packet.timestamp_seconds - origin
        <= window_end
    ]
    print(
        f"post_leave_window={window_start:.6f}..{window_end:.6f} "
        f"packets={len(selected)}"
    )

    flow_stats: dict[str, list[float]] = defaultdict(list)
    opcode_counts: Counter[tuple[str, str, int]] = Counter()
    undecoded_counts: Counter[tuple[str, int]] = Counter()
    undecoded_samples: dict[tuple[str, int], tuple[float, bytes]] = {}
    samples: dict[tuple[str, str, int], tuple[float, bytes]] = {}
    catalog = load_dungeon_map_catalog()
    exact_map_hits: list[tuple[float, str, int, int, str, int]] = []
    for packet in selected:
        relative_time = packet.timestamp_seconds - origin
        flow_stats[_flow_label(packet)].append(relative_time)
        if packet.transport.upper() != "TCP":
            continue
        direction = packet.inferred_direction() or "unknown"
        plaintext = _tcp_plaintext(packet)
        if plaintext is None:
            undecoded_key = (direction, len(packet.payload))
            undecoded_counts[undecoded_key] += 1
            undecoded_samples.setdefault(
                undecoded_key,
                (relative_time, packet.payload),
            )
            continue
        opcode = plaintext[2:4].hex(" ") if len(plaintext) >= 4 else "short"
        key = (direction, opcode, len(plaintext))
        opcode_counts[key] += 1
        samples.setdefault(key, (relative_time, plaintext))
        for map_id, entry in catalog.items():
            if not entry.display_name:
                continue
            needle = int(map_id).to_bytes(4, "little")
            for offset in _find_all(plaintext, needle):
                exact_map_hits.append(
                    (
                        relative_time,
                        direction,
                        int(map_id),
                        offset,
                        opcode,
                        len(plaintext),
                    )
                )

    print("flows:")
    for flow, times in sorted(flow_stats.items(), key=lambda item: item[1][0]):
        print(
            f"  {flow} count={len(times)} first={times[0]:.6f} "
            f"last={times[-1]:.6f}"
        )
    print("decoded_tcp_opcodes:")
    for key, count in opcode_counts.most_common():
        sample_time, sample = samples[key]
        print(
            f"  count={count} key={key} first={sample_time:.6f} "
            f"sample={sample[:96].hex(' ')}"
        )
    print("undecoded_tcp:")
    for key, count in undecoded_counts.most_common():
        sample_time, sample = undecoded_samples[key]
        print(
            f"  count={count} key={key} first={sample_time:.6f} "
            f"key_index={(len(sample) - 3) % 256} "
            f"sample={sample[:96].hex(' ')} "
            f"tail={sample[-32:].hex(' ')}"
        )
    print("named_map_u32_hits_in_tcp_plaintext:")
    for row in exact_map_hits:
        print(
            f"  t={row[0]:.6f} direction={row[1]} map_id={row[2]} "
            f"offset={row[3]} opcode={row[4]} len={row[5]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize decoded traffic immediately after a map leave."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--window", type=float, default=20.0)
    args = parser.parse_args()
    analyze(args.capture, args.window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
