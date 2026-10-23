from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body,
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_payload,
    split_v576_payload,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


@dataclass(frozen=True, slots=True)
class PacketShape:
    transport: str
    direction: str
    length: int
    signature: str
    decoded_kind: str

    def label(self) -> str:
        return ":".join(
            (
                self.transport,
                self.direction,
                str(self.length),
                self.signature,
                self.decoded_kind,
            )
        )


def _shape(payload: bytes, transport: str, direction: str) -> PacketShape:
    decoded_kind = "unknown"
    normalized_transport = transport.casefold()
    if normalized_transport == "udp":
        decoded = decode_v576_payload(payload, direction)
        if decoded is not None:
            decoded_kind = decoded.kind
        signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
    else:
        # TCP messages are length-framed and may still be encrypted. The two
        # bytes after the length prefix are the most stable discriminator.
        signature = payload[3:5].hex() if len(payload) >= 5 else payload.hex()
    return PacketShape(
        normalized_transport, direction, len(payload), signature, decoded_kind
    )


def capture_shapes(
    path: Path,
) -> tuple[list[tuple[float, PacketShape]], Counter[PacketShape]]:
    reassembler = V576TransportReassembler()
    rows: list[tuple[float, PacketShape]] = []
    totals: Counter[PacketShape] = Counter()
    origin: float | None = None

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        relative = packet.timestamp_seconds - origin
        direction = packet.inferred_direction()
        payloads = (
            split_v576_payload(packet.payload)
            if packet.transport.casefold() == "udp"
            else (packet.payload,)
        )
        for payload in payloads:
            shape = _shape(payload, packet.transport, direction)
            rows.append((relative, shape))
            totals[shape] += 1

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in reassembler.feed(packet):
            accept(logical_packet)
    for logical_packet in reassembler.flush():
        accept(logical_packet)
    return rows, totals


def analyze(
    positive_path: Path,
    control_path: Path,
    windows: tuple[tuple[str, float, float], ...],
) -> dict[str, object]:
    positive_rows, positive_totals = capture_shapes(positive_path)
    _control_rows, control_totals = capture_shapes(control_path)
    reports: list[dict[str, object]] = []
    for name, start, end in windows:
        window_counts: Counter[PacketShape] = Counter(
            shape for seconds, shape in positive_rows if start <= seconds <= end
        )
        candidates = []
        for shape, count in window_counts.most_common():
            positive_total = positive_totals[shape]
            control_total = control_totals[shape]
            candidates.append(
                {
                    "shape": shape.label(),
                    "window_count": count,
                    "positive_capture_total": positive_total,
                    "control_capture_total": control_total,
                    "window_share": round(count / max(1, positive_total), 4),
                    "control_present": control_total > 0,
                }
            )
        reports.append(
            {
                "name": name,
                "start": start,
                "end": end,
                "packet_count": sum(window_counts.values()),
                "candidates": candidates,
            }
        )
    return {
        "positive_capture": str(positive_path),
        "control_capture": str(control_path),
        "positive_shape_count": len(positive_totals),
        "control_shape_count": len(control_totals),
        "windows": reports,
    }


def analyze_player_bound_candidates(
    path: Path,
    player_ids: tuple[int, ...],
    windows: tuple[tuple[str, float, float], ...],
) -> list[dict[str, object]]:
    """Find decoded UDP layouts that reference several known party actors.

    A lifecycle transition must be bound to player actors before it is useful.
    This deliberately reports structural groups instead of raw packet bodies so
    reset/object-state bursts do not look like player death evidence.
    """

    window_rows: dict[
        str,
        dict[tuple[str, str, int, str, int, int], Counter[int]],
    ] = {
        name: defaultdict(Counter) for name, _start, _end in windows
    }
    reassembler = V576TransportReassembler()
    origin: float | None = None

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        relative = packet.timestamp_seconds - origin
        matching_windows = [
            name for name, start, end in windows if start <= relative <= end
        ]
        if not matching_windows or packet.transport.casefold() != "udp":
            return
        direction = packet.inferred_direction()
        for payload in split_v576_payload(packet.payload):
            signature = payload[5:9].hex() if len(payload) >= 9 else payload.hex()
            for xor_phase in range(12):
                decoded = _decode_v576_body(payload, xor_phase)
                for player_id in player_ids:
                    actor_bytes = player_id.to_bytes(4, "little")
                    offset = decoded.find(actor_bytes)
                    while offset >= 0:
                        key = (
                            "udp",
                            direction,
                            len(payload),
                            signature,
                            xor_phase,
                            offset,
                        )
                        for name in matching_windows:
                            window_rows[name][key][player_id] += 1
                        offset = decoded.find(actor_bytes, offset + 1)

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in reassembler.feed(packet):
            accept(logical_packet)
    for logical_packet in reassembler.flush():
        accept(logical_packet)

    reports: list[dict[str, object]] = []
    for name, _start, _end in windows:
        candidates = []
        for key, actor_counts in window_rows[name].items():
            if len(actor_counts) < 2:
                continue
            transport, direction, length, signature, xor_phase, offset = key
            candidates.append(
                {
                    "transport": transport,
                    "direction": direction,
                    "length": length,
                    "signature": signature,
                    "xor_phase": xor_phase,
                    "actor_offset": offset,
                    "distinct_player_count": len(actor_counts),
                    "match_count": sum(actor_counts.values()),
                    "player_ids": [
                        f"0x{player_id:08X}" for player_id in sorted(actor_counts)
                    ],
                }
            )
        candidates.sort(
            key=lambda row: (
                -int(row["distinct_player_count"]),
                -int(row["match_count"]),
                int(row["length"]),
            )
        )
        reports.append({"name": name, "candidates": candidates})
    return reports


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("positive_capture", type=Path)
    parser.add_argument("control_capture", type=Path)
    parser.add_argument(
        "--window",
        action="append",
        default=[],
        metavar="NAME:START:END",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--player-id",
        action="append",
        default=[],
        help="Known party actor id, for example 0x4D48D023",
    )
    args = parser.parse_args()
    windows = []
    for raw in args.window:
        name, start, end = raw.rsplit(":", 2)
        windows.append((name, float(start), float(end)))
    report = analyze(args.positive_capture, args.control_capture, tuple(windows))
    if args.player_id:
        player_ids = tuple(int(value, 0) for value in args.player_id)
        report["player_bound_candidates"] = analyze_player_bound_candidates(
            args.positive_capture,
            player_ids,
            tuple(windows),
        )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
