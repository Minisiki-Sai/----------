from __future__ import annotations

import argparse
import json
from pathlib import Path

from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
    _decode_v576_body,
    decode_v576_lobby_return,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)
from tools.analyze_hub_transition_evidence import _read_logical_packets


def audit(
    path: Path,
    *,
    map_ids: tuple[int, ...],
    before_seconds: float,
    after_seconds: float,
    route_only: bool = False,
) -> dict[str, object]:
    packets = _read_logical_packets(path)
    if not packets:
        return {"file": str(path), "windows": []}
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
    windows: list[dict[str, object]] = []
    for ack_timestamp in ack_times:
        rows: list[dict[str, object]] = []
        for packet in packets:
            delta = float(packet.timestamp_seconds) - ack_timestamp
            if delta < -before_seconds or delta > after_seconds:
                continue
            direction = packet.inferred_direction()
            decoded = b""
            decode_kind = ""
            key_index: int | None = None
            if str(packet.transport or "").upper() == "TCP":
                envelope = (
                    decode_registered_server_tcp_envelope(
                        packet.payload,
                        connection_context=packet,
                    )
                    if direction == "server_to_client"
                    else decode_registered_tcp_envelope(packet.payload)
                )
                if envelope is not None:
                    decoded = envelope[0]
                    key_index = int(envelope[1])
                    decode_kind = "tcp-envelope"
            elif len(packet.payload) >= 9:
                phase = _V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(
                    int(packet.payload[5])
                )
                if phase is not None:
                    decoded = _decode_v576_body(packet.payload, phase)
                    decode_kind = f"udp-xor-phase-{phase}"
            if not decoded:
                continue
            is_route_record = (
                str(packet.transport or "").upper() == "TCP"
                and len(decoded) >= 8
                and decoded[2:4] == b"\x07\x0d"
            )
            if route_only and not is_route_record:
                continue
            matches: list[list[int]] = []
            for map_id in map_ids:
                token = int(map_id).to_bytes(4, "little")
                start = 0
                while True:
                    offset = decoded.find(token, start)
                    if offset < 0:
                        break
                    matches.append([int(map_id), offset])
                    start = offset + 1
            if not matches and not is_route_record:
                continue
            first_map_id = (
                int.from_bytes(decoded[4:8], "little")
                if is_route_record
                else None
            )
            rows.append(
                {
                    "delta": round(delta, 6),
                    "at": round(
                        float(packet.timestamp_seconds) - origin,
                        6,
                    ),
                    "transport": str(packet.transport or "").upper(),
                    "direction": direction,
                    "wire_length": len(packet.payload),
                    "plaintext_length": len(decoded),
                    "opcode": decoded[2:4].hex(" "),
                    "key_index": key_index,
                    "decode_kind": decode_kind,
                    "route_map_id": first_map_id,
                    "matches": matches,
                    "sample": decoded[:160].hex(" "),
                }
            )
        windows.append(
            {
                "ack_at": round(ack_timestamp - origin, 6),
                "candidates": rows,
            }
        )
    return {"file": str(path), "windows": windows}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find decoded map-ID candidates around scene transitions."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--map-id", action="append", type=int, default=[])
    parser.add_argument("--before", type=float, default=2.0)
    parser.add_argument("--after", type=float, default=60.0)
    parser.add_argument(
        "--route-only",
        action="store_true",
        help="Only report decoded TCP 07 0D scene-route records.",
    )
    args = parser.parse_args()
    map_ids = tuple(args.map_id or (11, 36))
    print(
        json.dumps(
            [
                audit(
                    path.expanduser().resolve(),
                    map_ids=map_ids,
                    before_seconds=max(0.0, float(args.before)),
                    after_seconds=max(0.0, float(args.after)),
                    route_only=bool(args.route_only),
                )
                for path in args.captures
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
