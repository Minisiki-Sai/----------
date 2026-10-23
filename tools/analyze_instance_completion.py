from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import struct
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
    split_v576_payload,
    _decode_v576_body,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_tcp_envelope,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


END_SIGNATURE = "f1110f00"


@dataclass(frozen=True, slots=True)
class Row:
    seconds: float
    transport: str
    direction: str
    length: int
    signature: str
    prefix: str
    timers: tuple[tuple[int, int, str], ...] = ()

    @property
    def shape(self) -> tuple[str, str, int, str]:
        return self.transport, self.direction, self.length, self.signature


def scan(
    path: Path,
    timer_windows: tuple[tuple[float, float], ...] = (),
) -> tuple[list[Row], float]:
    rows: list[Row] = []
    reassembler = V576TransportReassembler()
    origin: float | None = None

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        inspect_timers = any(start <= seconds <= end for start, end in timer_windows)
        transport = packet.transport.casefold()
        direction = packet.inferred_direction()
        payloads = split_v576_payload(packet.payload) if transport == "udp" else (packet.payload,)
        for payload in payloads:
            timer_markers: set[tuple[int, int, str]] = set()
            if transport == "udp":
                signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
                prefix = payload[:32].hex()
                decoded_candidates = (
                    ((phase, _decode_v576_body(payload, phase)) for phase in range(12))
                    if inspect_timers
                    else ()
                )
            else:
                decoded = decode_registered_tcp_envelope(payload) if inspect_timers else None
                if decoded is not None:
                    plaintext, key_index, rounds, _source = decoded
                    opcode = plaintext[2:4].hex() if len(plaintext) >= 4 else "short"
                    signature = f"plain-{opcode}-k{key_index}-r{rounds}"
                    prefix = plaintext[:32].hex()
                    decoded_candidates = ((-1, plaintext),)
                else:
                    signature = payload[3:7].hex() if len(payload) >= 7 else payload.hex()
                    prefix = payload[:32].hex()
                    decoded_candidates = ()
            for phase, decoded_body in decoded_candidates:
                for offset in range(9 if transport == "udp" else 4, len(decoded_body) - 3):
                    integer = int.from_bytes(decoded_body[offset : offset + 4], "little")
                    if integer in {5_000, 10_000, 15_000, 20_000, 30_000, 60_000, 90_000, 120_000}:
                        timer_markers.add((phase, offset, f"ms:{integer}"))
                    value = struct.unpack_from("<f", decoded_body, offset)[0]
                    if value in {5.0, 10.0, 15.0, 20.0, 30.0, 60.0, 90.0, 120.0}:
                        timer_markers.add((phase, offset, f"float:{value:g}"))
            rows.append(
                Row(
                    seconds=seconds,
                    transport=transport,
                    direction=direction,
                    length=len(payload),
                    signature=signature,
                    prefix=prefix,
                    timers=tuple(sorted(timer_markers)),
                )
            )

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in reassembler.feed(packet):
            accept(logical)
    for logical in reassembler.flush():
        accept(logical)
    return rows, rows[-1].seconds if rows else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--minimum-support", type=int, default=2)
    args = parser.parse_args()

    final_support: dict[tuple[str, str, int, str], set[str]] = defaultdict(set)
    final_counts: Counter[tuple[str, str, int, str]] = Counter()
    control_counts: Counter[tuple[str, str, int, str]] = Counter()
    final_offsets: dict[tuple[str, str, int, str], list[float]] = defaultdict(list)
    prefixes: dict[tuple[str, str, int, str], Counter[str]] = defaultdict(Counter)
    coarse_support: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    coarse_counts: Counter[tuple[str, str, int]] = Counter()
    coarse_control: Counter[tuple[str, str, int]] = Counter()
    coarse_offsets: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    timer_support: dict[tuple[str, str, int, str, int, int, str], set[str]] = defaultdict(set)
    timer_counts: Counter[tuple[str, str, int, str, int, int, str]] = Counter()
    timer_control: Counter[tuple[str, str, int, str, int, int, str]] = Counter()
    timer_offsets: dict[tuple[str, str, int, str, int, int, str], list[float]] = defaultdict(list)

    for path in args.captures:
        rows, duration = scan(path)
        ends = [row.seconds for row in rows if row.signature == END_SIGNATURE]
        print(f"{path.name}: duration={duration:.3f}s ends={[round(value, 3) for value in ends]}")
        if not ends:
            continue
        for end in ends:
            acknowledgements = [
                row
                for row in rows
                if row.signature == "8a188201" and end <= row.seconds <= end + 5.0
            ]
            if not acknowledgements:
                continue
            acknowledgement = acknowledgements[0]
            preceding = [
                row
                for row in rows
                if row.direction == "server_to_client"
                and end <= row.seconds <= acknowledgement.seconds
            ][-8:]
            print(
                f"  completion-pair end={end:.3f}s ack={acknowledgement.seconds:.3f}s "
                f"lag={acknowledgement.seconds - end:.3f}s preceding="
                + str(
                    [
                        (round(row.seconds - end, 3), row.transport, row.length, row.signature)
                        for row in preceding
                    ]
                )
            )
        rows, _duration = scan(
            path,
            tuple((start, start + args.seconds) for start in ends),
        )
        final = ends[-1]
        for row in rows:
            if final <= row.seconds <= final + args.seconds:
                final_support[row.shape].add(path.name)
                final_counts[row.shape] += 1
                final_offsets[row.shape].append(row.seconds - final)
                prefixes[row.shape][row.prefix] += 1
                coarse = row.transport, row.direction, row.length
                coarse_support[coarse].add(path.name)
                coarse_counts[coarse] += 1
                coarse_offsets[coarse].append(row.seconds - final)
                for phase, offset, value in row.timers:
                    marker = (*row.shape, phase, offset, value)
                    timer_support[marker].add(path.name)
                    timer_counts[marker] += 1
                    timer_offsets[marker].append(row.seconds - final)
            if any(start <= row.seconds <= start + args.seconds for start in ends[:-1]):
                control_counts[row.shape] += 1
                coarse_control[(row.transport, row.direction, row.length)] += 1
                for phase, offset, value in row.timers:
                    timer_control[(*row.shape, phase, offset, value)] += 1

    candidates = [
        shape
        for shape, captures in final_support.items()
        if len(captures) >= args.minimum_support and control_counts[shape] == 0
    ]
    candidates.sort(
        key=lambda shape: (-len(final_support[shape]), min(final_offsets[shape]), shape)
    )
    print("\nFinal-only candidates:")
    for shape in candidates:
        offsets = final_offsets[shape]
        print(
            f"support={len(final_support[shape])} count={final_counts[shape]} "
            f"offset={min(offsets):.3f}..{max(offsets):.3f}s "
            f"shape={shape} captures={sorted(final_support[shape])} "
            f"prefix={prefixes[shape].most_common(1)[0][0]}"
        )

    coarse_candidates = [
        shape
        for shape, captures in coarse_support.items()
        if len(captures) >= args.minimum_support and coarse_control[shape] == 0
    ]
    coarse_candidates.sort(
        key=lambda shape: (-len(coarse_support[shape]), min(coarse_offsets[shape]), shape)
    )
    print("\nFinal-only transport/length candidates:")
    for shape in coarse_candidates:
        offsets = coarse_offsets[shape]
        print(
            f"support={len(coarse_support[shape])} count={coarse_counts[shape]} "
            f"offset={min(offsets):.3f}..{max(offsets):.3f}s shape={shape} "
            f"captures={sorted(coarse_support[shape])}"
        )

    timer_candidates = [
        marker
        for marker, captures in timer_support.items()
        if len(captures) >= args.minimum_support and timer_control[marker] == 0
    ]
    timer_candidates.sort(
        key=lambda marker: (-len(timer_support[marker]), min(timer_offsets[marker]), marker)
    )
    print("\nFinal-only timer-value candidates:")
    for marker in timer_candidates:
        offsets = timer_offsets[marker]
        print(
            f"support={len(timer_support[marker])} count={timer_counts[marker]} "
            f"offset={min(offsets):.3f}..{max(offsets):.3f}s marker={marker} "
            f"captures={sorted(timer_support[marker])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
