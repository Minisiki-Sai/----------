from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    _V576_BODY_XOR_KEY,
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.dungeon_map_catalog import (
    load_dungeon_map_catalog,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


@dataclass(frozen=True, slots=True)
class Match:
    relative_time: float
    transport: str
    direction: str
    payload_length: int
    signature: str
    representation: str
    phase: int | None
    offset: int
    value: int
    context: str


def _offsets(data: bytes, needle: bytes) -> tuple[int, ...]:
    found: list[int] = []
    cursor = 0
    while True:
        offset = data.find(needle, cursor)
        if offset < 0:
            return tuple(found)
        found.append(offset)
        cursor = offset + 1


def _context(data: bytes, offset: int, width: int) -> str:
    start = max(0, offset - 16)
    end = min(len(data), offset + width + 16)
    return data[start:end].hex(" ")


def _phases(payload: bytes) -> tuple[int, ...]:
    preferred = (
        _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(payload[5])
        if len(payload) > 5
        else None
    )
    if preferred is not None:
        return (preferred,)
    return tuple(range(len(_V576_BODY_XOR_KEY)))


def analyze(
    path: Path,
    values: tuple[int, ...],
    *,
    widths: tuple[int, ...] = (2, 4, 8),
    minimum_time: float | None = None,
    maximum_time: float | None = None,
) -> tuple[Match, ...]:
    transport = V576TransportReassembler()
    logical_packets = []
    for timestamp, adapter, original_length, frame in read_trace(path):
        # Adapter 255 is the identity history appended after combat. It must
        # not participate in map-entry inference.
        if adapter == 255:
            continue
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        logical_packets.extend(transport.feed(packet))
    logical_packets.extend(transport.flush())
    if not logical_packets:
        return ()

    origin = min(packet.timestamp_seconds for packet in logical_packets)
    matches: list[Match] = []
    seen: set[tuple[object, ...]] = set()
    for packet in logical_packets:
        relative_time = packet.timestamp_seconds - origin
        if minimum_time is not None and relative_time < minimum_time:
            continue
        if maximum_time is not None and relative_time > maximum_time:
            continue
        payload = packet.payload
        signature = payload[5:9].hex() if len(payload) >= 9 else "short"
        direction = packet.inferred_direction()
        representations: list[tuple[str, int | None, bytes]] = [
            ("wire", None, payload)
        ]
        if str(packet.transport or "").upper() == "TCP":
            envelope = (
                decode_registered_server_tcp_envelope(
                    payload,
                    connection_context=packet,
                )
                if direction == "server_to_client"
                else decode_registered_tcp_envelope(payload)
            )
            if envelope is not None:
                plaintext, key_index, _rounds, _source = envelope
                representations.append(("tcp-plaintext", key_index, plaintext))
        else:
            representations.extend(
                ("decoded", phase, _decode_v576_body(payload, phase))
                for phase in _phases(payload)
            )
        for representation, phase, data in representations:
            for value in values:
                for width in widths:
                    if value >= 1 << (width * 8):
                        continue
                    needle = value.to_bytes(width, "little")
                    for offset in _offsets(data, needle):
                        key = (
                            packet.timestamp_seconds,
                            packet.transport,
                            direction,
                            len(payload),
                            signature,
                            representation,
                            phase,
                            offset,
                            width,
                            value,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        matches.append(
                            Match(
                                relative_time=relative_time,
                                transport=packet.transport,
                                direction=direction,
                                payload_length=len(payload),
                                signature=signature,
                                representation=f"{representation}-u{width * 8}",
                                phase=phase,
                                offset=offset,
                                value=value,
                                context=_context(data, offset, width),
                            )
                        )
    return tuple(
        sorted(
            matches,
            key=lambda row: (
                row.relative_time,
                row.transport,
                row.value,
                row.representation,
                row.offset,
            ),
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Locate candidate dungeon/map IDs in DNDP1 wire records."
    )
    parser.add_argument("capture", type=Path)
    parser.add_argument("values", nargs="*", type=lambda value: int(value, 0))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--width",
        dest="width_bits",
        action="append",
        type=int,
        choices=(16, 32, 64),
        help="integer width to scan; repeat for more than one (default: all)",
    )
    parser.add_argument(
        "--catalog-u32",
        action="store_true",
        help="scan every named map ID as uint32",
    )
    parser.add_argument("--maximum-time", type=float)
    parser.add_argument("--minimum-time", type=float)
    args = parser.parse_args()

    values = tuple(dict.fromkeys(args.values))
    widths = (
        tuple(dict.fromkeys(value // 8 for value in args.width_bits))
        if args.width_bits
        else (2, 4, 8)
    )
    if args.catalog_u32:
        values = tuple(sorted(load_dungeon_map_catalog()))
        widths = (4,)
    if not values:
        parser.error("provide values or --catalog-u32")
    matches = analyze(
        args.capture,
        values,
        widths=widths,
        minimum_time=args.minimum_time,
        maximum_time=args.maximum_time,
    )
    counts = Counter(
        (
            row.value,
            row.transport,
            row.direction,
            row.payload_length,
            row.signature,
            row.representation,
            row.phase,
            row.offset,
        )
        for row in matches
    )
    print(f"capture={args.capture}")
    print(f"matches={len(matches)} grouped={len(counts)}")
    print("groups:")
    for key, count in counts.most_common():
        print(f"  count={count} key={key}")
    print("occurrences:")
    for row in matches[: max(0, args.limit)]:
        print(
            f"  t={row.relative_time:.6f}s value={row.value} "
            f"{row.transport}/{row.direction} len={row.payload_length} "
            f"sig={row.signature} {row.representation} phase={row.phase} "
            f"offset={row.offset} context={row.context}"
        )
    if len(matches) > args.limit:
        print(f"  ... {len(matches) - args.limit} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
