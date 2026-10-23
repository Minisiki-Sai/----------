from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body,
    decode_v576_lobby_return,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)
from tools.analyze_hub_transition_evidence import _read_logical_packets


def collect(
    path: Path,
    *,
    map_ids: tuple[int, ...],
    before: float,
    after: float,
) -> list[set[tuple[object, ...]]]:
    packets = _read_logical_packets(path)
    ack_times = [
        float(packet.timestamp_seconds)
        for packet in packets
        if decode_v576_lobby_return(
            packet.payload,
            packet.inferred_direction(),
        )
        is not None
    ]
    windows: list[set[tuple[object, ...]]] = []
    for ack_at in ack_times:
        keys: set[tuple[object, ...]] = set()
        for packet in packets:
            delta = float(packet.timestamp_seconds) - ack_at
            if delta < -before or delta > after:
                continue
            direction = packet.inferred_direction() or "unknown"
            transport = str(packet.transport or "").upper()
            variants: list[tuple[str, int, bytes]] = []
            if transport == "UDP" and len(packet.payload) >= 9:
                for phase in range(12):
                    variants.append(("udp", phase, _decode_v576_body(packet.payload, phase)))
            elif transport == "TCP":
                decoded = (
                    decode_registered_server_tcp_envelope(
                        packet.payload,
                        connection_context=packet,
                    )
                    if direction == "server_to_client"
                    else decode_registered_tcp_envelope(packet.payload)
                )
                if decoded is not None:
                    variants.append(("tcp", int(decoded[1]), decoded[0]))
            for kind, phase, decoded in variants:
                opcode = decoded[2:4].hex() if len(decoded) >= 4 else ""
                for map_id in map_ids:
                    token = int(map_id).to_bytes(4, "little")
                    start = 0
                    while True:
                        offset = decoded.find(token, start)
                        if offset < 0:
                            break
                        # Keep the structural bytes around the candidate while
                        # masking the map value itself. This makes repeated
                        # layouts comparable without relying on random IDs.
                        prefix = decoded[max(0, offset - 4) : offset]
                        suffix = decoded[offset + 4 : offset + 8]
                        keys.add(
                            (
                                int(map_id),
                                kind,
                                direction,
                                len(packet.payload),
                                len(decoded),
                                phase,
                                opcode,
                                offset,
                                prefix.hex(),
                                suffix.hex(),
                            )
                        )
                        start = offset + 1
        windows.append(keys)
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-capture search for decrypted hub destination fields."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--map-id", action="append", type=int, default=[])
    parser.add_argument("--before", type=float, default=1.0)
    parser.add_argument("--after", type=float, default=45.0)
    parser.add_argument("--min-files", type=int, default=2)
    args = parser.parse_args()
    map_ids = tuple(args.map_id or (11, 36))
    file_hits: Counter[tuple[object, ...]] = Counter()
    window_hits: Counter[tuple[object, ...]] = Counter()
    examples: dict[tuple[object, ...], list[str]] = defaultdict(list)
    for path in args.captures:
        resolved = path.expanduser().resolve()
        windows = collect(
            resolved,
            map_ids=map_ids,
            before=max(0.0, float(args.before)),
            after=max(0.0, float(args.after)),
        )
        union: set[tuple[object, ...]] = set()
        for index, keys in enumerate(windows):
            for key in keys:
                window_hits[key] += 1
                if len(examples[key]) < 8:
                    examples[key].append(f"{resolved.name}#{index + 1}")
            union.update(keys)
        for key in union:
            file_hits[key] += 1

    ranked = sorted(
        file_hits,
        key=lambda key: (
            -file_hits[key],
            -window_hits[key],
            key,
        ),
    )
    for key in ranked:
        if file_hits[key] < max(1, int(args.min_files)):
            continue
        print(
            f"files={file_hits[key]} windows={window_hits[key]} "
            f"key={key} examples={examples[key]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
