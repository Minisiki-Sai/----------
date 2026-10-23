from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    PacketRecord,
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_lobby_return,
    decode_v576_map_identity,
    decode_v576_player_health,
    decode_v576_scene_teardown,
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


def analyze(path: Path, window_seconds: float, *, show_details: bool = False) -> None:
    packets = _read_logical_packets(path)
    if not packets:
        print(f"capture={path} packets=0")
        return
    origin = packets[0].timestamp_seconds
    evidence: list[tuple[float, str]] = []
    transition_details: list[tuple[float, str]] = []
    map_rows: list[tuple[float, int, bool, str]] = []
    for packet in packets:
        relative_time = packet.timestamp_seconds - origin
        direction = packet.inferred_direction()
        map_event = decode_v576_map_identity(
            packet.payload,
            direction,
            connection_context=packet,
        )
        if map_event is not None:
            map_rows.append(
                (
                    relative_time,
                    int(map_event.map_id),
                    bool(map_event.active),
                    map_event.layout,
                )
            )
            evidence.append(
                (
                    relative_time,
                    f"map:{map_event.map_id}:"
                    f"{'enter' if map_event.active else 'leave'}:"
                    f"{map_event.layout}:"
                    f"{packet.source_ip}:{packet.source_port}>"
                    f"{packet.destination_ip}:{packet.destination_port}",
                )
            )
        lobby_return = decode_v576_lobby_return(packet.payload, direction)
        if lobby_return is not None:
            evidence.append(
                (
                    relative_time,
                    f"transition-ack:route={lobby_return.route_code}:"
                    f"{lobby_return.layout}",
                )
            )
        scene_teardown = decode_v576_scene_teardown(
            packet.payload,
            direction,
            connection_context=packet,
        )
        if scene_teardown is not None:
            evidence.append(
                (
                    relative_time,
                    f"scene-teardown:{scene_teardown.layout}",
                )
            )
        health_rows = decode_v576_player_health(packet.payload, direction)
        resource_rows = [
            row
            for row in health_rows
            if row.layout == "s2c-udp-player-resource-sync-210c-v576"
        ]
        if resource_rows:
            actor_summary = ",".join(
                f"0x{row.actor_id:08X}:{row.current_hp}/{row.max_hp}:"
                f"{row.current_mp}/{row.max_mp}"
                for row in resource_rows[:8]
            )
            evidence.append(
                (
                    relative_time,
                    f"resource-210c:count={len(resource_rows)}:"
                    f"len={len(packet.payload)}:sig={packet.payload[5:9].hex()}:"
                    f"{actor_summary}",
                )
            )
        signature = packet.payload[5:9] if len(packet.payload) >= 9 else b""
        if signature in {
            b"\xb3\x73\x0b\x00",
            b"\xc7\xe7\x10\x00",
            b"\x2e\xad\x83\x00",
        }:
            phase = _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(signature[0])
            decoded = (
                _decode_v576_body(packet.payload, phase)
                if phase is not None
                else b""
            )
            transition_details.append(
                (
                    relative_time,
                    f"{packet.transport}:{direction}:"
                    f"{packet.source_ip}:{packet.source_port}>"
                    f"{packet.destination_ip}:{packet.destination_port}:"
                    f"len={len(packet.payload)}:phase={phase}:"
                    f"wire={packet.payload.hex(' ')}:decoded={decoded.hex(' ')}",
                )
            )

    print(f"capture={path}")
    print(
        f"duration={packets[-1].timestamp_seconds - origin:.6f}s "
        f"packets={len(packets)}"
    )
    print("evidence:")
    for timestamp, label in sorted(evidence):
        print(f"  {timestamp:.6f} {label}")
    if show_details:
        print("transition_details:")
        for timestamp, label in transition_details:
            print(f"  {timestamp:.6f} {label}")

    leave_times = [timestamp for timestamp, _map_id, active, _layout in map_rows if not active]
    for leave_time in leave_times:
        window_end = leave_time + window_seconds
        selected = [
            packet
            for packet in packets
            if leave_time <= packet.timestamp_seconds - origin <= window_end
        ]
        tcp_shapes: Counter[tuple[str, str, int]] = Counter()
        first_tcp: dict[tuple[str, str, int], tuple[float, str]] = {}
        udp_shapes: Counter[tuple[str, int, str]] = Counter()
        first_udp: dict[tuple[str, int, str], tuple[float, str]] = {}
        for packet in selected:
            relative_time = packet.timestamp_seconds - origin
            direction = packet.inferred_direction() or "unknown"
            if packet.transport.upper() == "TCP":
                plaintext = _tcp_plaintext(packet)
                if plaintext is None:
                    key = (direction, "undecoded", len(packet.payload))
                    sample = packet.payload[:48].hex(" ")
                else:
                    opcode = plaintext[2:4].hex(" ") if len(plaintext) >= 4 else "short"
                    key = (direction, opcode, len(plaintext))
                    sample = plaintext[:96].hex(" ")
                tcp_shapes[key] += 1
                first_tcp.setdefault(key, (relative_time, sample))
            elif packet.transport.upper() == "UDP":
                signature = packet.payload[5:9].hex(" ") if len(packet.payload) >= 9 else "short"
                key = (direction, len(packet.payload), signature)
                udp_shapes[key] += 1
                first_udp.setdefault(
                    key,
                    (relative_time, packet.payload[:48].hex(" ")),
                )
        print(f"window={leave_time:.6f}..{window_end:.6f}")
        print("  tcp:")
        for key, count in tcp_shapes.most_common():
            timestamp, sample = first_tcp[key]
            print(
                f"    count={count} key={key} first={timestamp:.6f} "
                f"sample={sample}"
            )
        print("  udp:")
        for key, count in udp_shapes.most_common(30):
            timestamp, sample = first_udp[key]
            print(
                f"    count={count} key={key} first={timestamp:.6f} "
                f"sample={sample}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare exact map, 21 0C and transition evidence after leaves."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--window", type=float, default=20.0)
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    analyze(
        args.capture,
        max(0.0, float(args.window)),
        show_details=bool(args.details),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
