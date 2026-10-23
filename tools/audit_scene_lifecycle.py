from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from dragon_nest_builder.live_dps.capture_sources import (
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.npcap import (
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_v576 import (
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


def audit(
    path: Path,
    *,
    tcp_window: float = 0.0,
    tcp_after: float = 5.0,
) -> dict[str, object]:
    transport = V576TransportReassembler()
    origin: float | None = None
    rows: list[dict[str, object]] = []
    tcp_rows: list[dict[str, object]] = []

    def observe(packet) -> None:
        direction = packet.inferred_direction()
        observed_at = max(
            0.0,
            float(packet.timestamp_seconds)
            - float(origin or packet.timestamp_seconds),
        )
        identity = decode_v576_map_identity(
            packet.payload,
            direction,
            connection_context=packet,
        )
        if identity is not None:
            rows.append(
                {
                    "at": round(observed_at, 6),
                    "kind": "map",
                    "map_id": int(identity.map_id),
                    "active": bool(identity.active),
                    "authoritative": bool(identity.authoritative),
                    "layout": identity.layout,
                }
            )
        teardown = decode_v576_scene_teardown(
            packet.payload,
            direction,
            connection_context=packet,
        )
        if teardown is not None:
            rows.append(
                {
                    "at": round(observed_at, 6),
                    "kind": "teardown",
                    "layout": teardown.layout,
                }
            )
        transition = decode_v576_lobby_return(packet.payload, direction)
        if transition is not None:
            rows.append(
                {
                    "at": round(observed_at, 6),
                    "kind": "transition_ack",
                    "route_code": int(transition.route_code),
                    "layout": transition.layout,
                }
            )
        health_rows = decode_v576_player_health(packet.payload, direction)
        if any(
            row.layout == "s2c-udp-player-resource-sync-210c-v576"
            for row in health_rows
        ):
            rows.append(
                {
                    "at": round(observed_at, 6),
                    "kind": "resource_sync_210c",
                }
            )
        if tcp_window > 0.0 and str(packet.transport or "").upper() == "TCP":
            decoded = (
                decode_registered_server_tcp_envelope(
                    packet.payload,
                    connection_context=packet,
                )
                if direction == "server_to_client"
                else decode_registered_tcp_envelope(packet.payload)
            )
            plaintext = decoded[0] if decoded is not None else b""
            if (
                direction == "server_to_client"
                and len(plaintext) >= 9
                and plaintext[2:4] == b"\x0c\x05"
            ):
                rows.append(
                    {
                        "at": round(observed_at, 6),
                        "kind": "tcp_scene_context",
                        "map_id_candidate": int.from_bytes(
                            plaintext[5:9], "little"
                        ),
                        "record_kind": int(plaintext[4]),
                        "plaintext_length": len(plaintext),
                        "sample": plaintext[:48].hex(" "),
                        "wire_hex": packet.payload.hex(),
                        "small_u32": [
                            [offset, value]
                            for offset in range(4, len(plaintext) - 3)
                            if 0 < (
                                value := int.from_bytes(
                                    plaintext[offset : offset + 4], "little"
                                )
                            ) <= 20000
                        ],
                    }
                )
            tcp_rows.append(
                {
                    "at": round(observed_at, 6),
                    "direction": direction,
                    "wire_length": len(packet.payload),
                    "key_index": (
                        int(decoded[1])
                        if decoded is not None
                        else (len(packet.payload) - 3) % 256
                    ),
                    "decoded": decoded is not None,
                    "opcode": (
                        plaintext[2:4].hex(" ")
                        if len(plaintext) >= 4
                        else ""
                    ),
                    "sample": (plaintext or packet.payload)[:96].hex(" "),
                }
            )

    with path.open("rb") as handle:
        if handle.read(len(V576DiagnosticReplaySource._MAGIC)) != (
            V576DiagnosticReplaySource._MAGIC
        ):
            raise ValueError(f"不是 DNDP1 抓包：{path}")
        while True:
            header = handle.read(V576DiagnosticReplaySource._FRAME_HEADER.size)
            if not header:
                break
            if len(header) != V576DiagnosticReplaySource._FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = (
                V576DiagnosticReplaySource._FRAME_HEADER.unpack(header)
            )
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter in {
                IDENTITY_METADATA_ADAPTER_INDEX,
                IDENTITY_TRACE_ADAPTER_INDEX,
                SESSION_EVIDENCE_ADAPTER_INDEX,
            }:
                continue
            if origin is None:
                origin = float(timestamp)
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical_packet in transport.feed(packet):
                observe(logical_packet)
        for logical_packet in transport.flush():
            observe(logical_packet)
    rows.sort(key=lambda row: float(row.get("at") or 0.0))
    windows: list[dict[str, object]] = []
    if tcp_window > 0.0:
        for ack in (row for row in rows if row.get("kind") == "transition_ack"):
            ack_at = float(ack.get("at") or 0.0)
            selected = [
                row
                for row in tcp_rows
                if ack_at - tcp_window <= float(row.get("at") or 0.0)
                <= ack_at + tcp_after
            ]
            counts = Counter(
                (
                    bool(row["decoded"]),
                    str(row["direction"]),
                    int(row["wire_length"]),
                    int(row["key_index"]),
                    str(row["opcode"]),
                )
                for row in selected
            )
            samples: list[dict[str, object]] = []
            for key, count in counts.most_common():
                sample = next(
                    row
                    for row in selected
                    if (
                        bool(row["decoded"]),
                        str(row["direction"]),
                        int(row["wire_length"]),
                        int(row["key_index"]),
                        str(row["opcode"]),
                    )
                    == key
                )
                samples.append({"count": count, **sample})
            windows.append({"ack_at": ack_at, "tcp_groups": samples})
    return {"file": str(path), "events": rows, "tcp_windows": windows}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只审计地图与场景生命周期包，不解码战斗事件",
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--tcp-window",
        type=float,
        default=0.0,
        help="列出每次切换确认前若干秒至后5秒的TCP结构分组",
    )
    parser.add_argument(
        "--tcp-after",
        type=float,
        default=5.0,
        help="列出每次切换确认后若干秒的TCP结构分组",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            [
                audit(
                    path.expanduser().resolve(),
                    tcp_window=max(0.0, args.tcp_window),
                    tcp_after=max(0.0, args.tcp_after),
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
