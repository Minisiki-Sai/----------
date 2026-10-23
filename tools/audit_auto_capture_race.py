"""Inspect map/scene traffic in a diagnostic capture without combat replay."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_lobby_return,
    decode_v576_map_identity,
    decode_v576_monster_spawn,
    decode_v576_player_health,
    decode_v576_scene_teardown,
)
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.settlement import decode_settlement
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)


def audit(path: Path) -> None:
    adapter_times: dict[int, list[float]] = defaultdict(list)
    transport = V576TransportReassembler()
    packets = []
    for timestamp, adapter, original_length, frame in read_trace(path):
        adapter_times[adapter].append(timestamp)
        if adapter >= 250:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is not None:
            packets.extend(transport.feed(packet))
    packets.extend(transport.flush())
    packets.sort(key=lambda packet: packet.timestamp_seconds)
    origin = min(adapter_times.get(3, adapter_times.get(6, [0])))
    print(path.name)
    print("adapters", {key: (len(value), round(min(value) - origin, 3), round(max(value) - origin, 3)) for key, value in adapter_times.items()})
    print("logical", Counter((packet.transport, packet.inferred_direction()) for packet in packets))
    flows: dict[tuple, list[float]] = defaultdict(list)
    opcodes: Counter[tuple] = Counter()
    selected_tcp = []
    late_opcode_times: dict[tuple, list[float]] = defaultdict(list)
    world_login = []
    transfer_records = []
    for packet in packets:
        key = (packet.transport, packet.source_ip, packet.source_port, packet.destination_ip, packet.destination_port)
        flows[key].append(packet.timestamp_seconds - origin)
        if packet.transport == "TCP":
            decoded = (
                decode_registered_server_tcp_envelope(packet.payload, connection_context=packet)
                if packet.inferred_direction() == "server_to_client"
                else decode_registered_tcp_envelope(packet.payload)
            )
            plaintext = decoded[0] if decoded is not None else None
            opcodes[(packet.inferred_direction(), plaintext[2:4].hex() if plaintext is not None and len(plaintext) >= 4 else "undecoded", len(packet.payload))] += 1
            if packet.timestamp_seconds - origin > 550:
                late_opcode_times[(packet.inferred_direction(), plaintext[2:4].hex() if plaintext is not None and len(plaintext) >= 4 else "undecoded", len(packet.payload))].append(packet.timestamp_seconds - origin)
            if plaintext is not None and plaintext[2:4].hex() in {"1511", "0313", "0201"}:
                world_login.append((round(packet.timestamp_seconds - origin, 3), packet.inferred_direction(), plaintext[2:4].hex(), len(plaintext), plaintext[:120].hex(), [i for i in range(len(plaintext) - 3) if plaintext[i:i+4] in {b"\x0b\x00\x00\x00", b"\x24\x00\x00\x00"}][:30]))
            if plaintext is not None and plaintext[2:4].hex() in {"0313", "0201", "0318", "0301", "0303", "060b"}:
                if plaintext[2:4].hex() != "0313" or plaintext[4:8] == b"\x02\x00\x00\x00":
                    transfer_records.append((round(packet.timestamp_seconds-origin, 3), plaintext[2:4].hex(), len(plaintext), plaintext[:16].hex()))
            if plaintext is not None and len(plaintext) >= 4 and plaintext[2:4].hex() in {"060b", "0606", "0618", "0301", "0318", "0c05", "1224", "070d", "071c", "0308", "0305"}:
                selected_tcp.append((round(packet.timestamp_seconds - origin, 3), packet.inferred_direction(), plaintext[2:4].hex(), len(packet.payload), plaintext[:48].hex(), packet.payload.hex() if plaintext[2:4] == b"\x06\x0b" else ""))
    print("flows", [(key, len(times), round(min(times), 2), round(max(times), 2)) for key, times in sorted(flows.items(), key=lambda row: -len(row[1]))[:15]])
    print("tcp_flows", [(key, len(times), round(min(times), 2), round(max(times), 2)) for key, times in sorted(flows.items(), key=lambda row: min(row[1])) if key[0] == "TCP"])
    print("tcp_opcodes", opcodes.most_common(25))
    print("boundary_tcp", [row for row in selected_tcp if row[2] in {"060b", "0618", "0301", "0318", "0c05", "1224"}])
    print("late_tcp", [row for row in selected_tcp if row[0] > 550 and row[2] not in {"070d"}][:80])
    print("late_opcodes", [(key, len(times), round(min(times), 2), round(max(times), 2)) for key, times in sorted(late_opcode_times.items(), key=lambda row: min(row[1])) if key[1] != "2210"])
    print("world_login", world_login)
    print("transfer_records", [row for row in transfer_records if row[1] != "0303"][:35], "player_spawn_count", sum(row[1] == "0303" for row in transfer_records))
    markers = []
    for packet in packets:
        at = round(packet.timestamp_seconds - origin, 3)
        direction = packet.inferred_direction()
        result = decode_settlement(packet, origin)
        if result is not None:
            markers.append((at, "formal_result", result.clear_table_id, result.server_elapsed_ms))
        map_row = decode_v576_map_identity(packet.payload, direction, connection_context=packet)
        if map_row is not None:
            markers.append((at, "map", map_row.map_id, map_row.active, map_row.layout))
        if (row := decode_v576_lobby_return(packet.payload, direction)) is not None:
            markers.append((at, "ack", row.route_code, row.layout))
        if (row := decode_v576_scene_teardown(packet.payload, direction, connection_context=packet)) is not None:
            markers.append((at, "teardown", row.layout))
        if (row := decode_v576_monster_spawn(packet.payload, direction)) is not None and row.template_id == 4180006:
            markers.append((at, "boss_spawn", row.template_id))
        health = decode_v576_player_health(packet.payload, direction)
        if any(row.layout == "s2c-udp-player-resource-sync-210c-v576" for row in health):
            markers.append((at, "resource_sync", len(health)))
    for marker in markers:
        print(*marker)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    args = parser.parse_args()
    for capture in args.captures:
        audit(capture)


if __name__ == "__main__":
    main()
