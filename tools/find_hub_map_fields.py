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
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    decode_captured_frame,
    decode_v576_lobby_return,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


def _read_packets(path: Path) -> tuple[PacketRecord, ...]:
    transport = V576TransportReassembler()
    packets: list[PacketRecord] = []
    for timestamp, adapter, original_length, frame in read_trace(path):
        if adapter in {254, 255}:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is not None:
            packets.extend(transport.feed(packet))
    packets.extend(transport.flush())
    return tuple(sorted(packets, key=lambda row: row.timestamp_seconds))


def _tcp_plaintext(packet: PacketRecord) -> bytes | None:
    decoded = (
        decode_registered_server_tcp_envelope(
            packet.payload,
            connection_context=packet,
        )
        if packet.inferred_direction() == "server_to_client"
        else decode_registered_tcp_envelope(packet.payload)
    )
    return decoded[0] if decoded is not None else None


def analyze(path: Path, window_seconds: float) -> None:
    packets = _read_packets(path)
    if not packets:
        raise SystemExit("capture has no logical packets")
    origin = packets[0].timestamp_seconds
    acknowledgements = [
        packet.timestamp_seconds - origin
        for packet in packets
        if decode_v576_lobby_return(
            packet.payload,
            packet.inferred_direction(),
        )
        is not None
    ]
    hub_ids = {
        int(map_id): entry.display_name
        for map_id, entry in load_dungeon_map_catalog().items()
        if int(entry.map_type) in {1, 2}
    }
    by_window: list[Counter[tuple[object, ...]]] = []
    samples: dict[tuple[object, ...], tuple[float, bytes]] = {}
    for start in acknowledgements:
        counts: Counter[tuple[object, ...]] = Counter()
        for packet in packets:
            relative_time = packet.timestamp_seconds - origin
            if not start <= relative_time <= start + window_seconds:
                continue
            direction = packet.inferred_direction() or "unknown"
            if packet.transport.upper() == "TCP":
                plaintext = _tcp_plaintext(packet)
                if plaintext is None:
                    continue
                candidates = ((-1, plaintext),)
                signature = plaintext[2:4].hex()
            else:
                signature_bytes = (
                    packet.payload[5:9] if len(packet.payload) >= 9 else b""
                )
                preferred = (
                    _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(signature_bytes[0])
                    if signature_bytes
                    else None
                )
                phases = (preferred,) if preferred is not None else range(13)
                candidates = tuple(
                    (phase, _decode_v576_body(packet.payload, phase))
                    for phase in phases
                )
                signature = signature_bytes.hex()
            for phase, decoded in candidates:
                for offset in range(9, max(9, len(decoded) - 3)):
                    value = int.from_bytes(decoded[offset : offset + 4], "little")
                    if value not in hub_ids:
                        continue
                    key = (
                        packet.transport.upper(),
                        direction,
                        len(packet.payload),
                        signature,
                        phase,
                        offset,
                        value,
                    )
                    counts[key] += 1
                    samples.setdefault(key, (relative_time, decoded))
        by_window.append(counts)

    print(f"capture={path} ack_times={acknowledgements}")
    if len(by_window) < 2:
        return
    shared = set(by_window[0]).intersection(by_window[1])
    if len(by_window) >= 3:
        shared.difference_update(by_window[2])
    print(
        "shared_first_two_not_third="
        f"{len(shared)} window_seconds={window_seconds}"
    )
    for key in sorted(
        shared,
        key=lambda item: (
            -sum(counter[item] for counter in by_window[:2]),
            str(item),
        ),
    ):
        timestamp, decoded = samples[key]
        offset = int(key[-2])
        map_id = int(key[-1])
        left = max(0, offset - 12)
        right = min(len(decoded), offset + 16)
        print(
            f"counts={[counter[key] for counter in by_window]} "
            f"key={key} map={hub_ids[map_id]} first={timestamp:.6f} "
            f"context={decoded[left:right].hex(' ')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find stable hub-map integer fields around scene transitions."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("--window", type=float, default=25.0)
    args = parser.parse_args()
    analyze(args.capture, max(0.0, float(args.window)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
