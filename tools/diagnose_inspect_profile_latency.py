from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from dragon_nest_builder.live_dps.models import PlayerInspectEvent
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.sources import (
    _V576PacketEventDecoder,
    _split_length_framed_tcp_messages,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
    decode_tcp_inspect_identity_payload,
    decode_tcp_inspect_request_payload,
)
from tools.analyze_npcap_trace import read_trace


SKIPPED_ADAPTERS = {251, 254, 255}


def event_field(event: PlayerInspectEvent, key: str) -> str:
    return dict(event.fields).get(key, "")


def profile_id(event: PlayerInspectEvent) -> str:
    value = event_field(event, "角色档案标识")
    if value:
        return value
    target = str(event.target_id or "")
    return target.split(":", 1)[-1] if target.casefold().startswith("profile:") else target


def analyze(
    path: Path,
    data_root: Path,
    *,
    full_decoder: bool,
    adapters: frozenset[int] | None = None,
) -> dict[str, object]:
    init_started = time.perf_counter()
    decoder = _V576PacketEventDecoder(data_root, {})
    decoder_init_ms = (time.perf_counter() - init_started) * 1000.0
    transport = V576TransportReassembler()
    ledger = V576EventLedger(max_events=8_192 if not full_decoder else 262_144)
    frames = 0
    logical_packets = 0
    tcp_packets = 0
    decoder_ms = 0.0
    max_decode_ms = 0.0
    slow_packets: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    event_counts: Counter[str] = Counter()
    inspect_request_messages: list[dict[str, object]] = []
    inspect_identity_messages: list[dict[str, object]] = []
    tcp_plaintexts: list[dict[str, object]] = []
    short_client_messages: list[dict[str, object]] = []
    first_capture_time: float | None = None
    last_capture_time: float | None = None

    active_adapter = -1
    active_input_timestamp = 0.0

    def packet_flow(packet) -> dict[str, object]:
        return {
            "adapter": active_adapter,
            "released_at": active_input_timestamp,
            "reassembly_delay_seconds": round(
                max(0.0, active_input_timestamp - packet.timestamp_seconds),
                6,
            ),
            "source_ip": packet.source_ip,
            "source_port": packet.source_port,
            "destination_ip": packet.destination_ip,
            "destination_port": packet.destination_port,
        }

    def feed(packet) -> None:
        nonlocal logical_packets, tcp_packets, decoder_ms, max_decode_ms
        if not ledger.accept(packet):
            return
        logical_packets += 1
        if packet.transport.upper() != "TCP":
            return
        tcp_packets += 1
        direction = packet.inferred_direction()
        for message in _split_length_framed_tcp_messages(packet.payload):
            decoded_envelope = (
                decode_registered_tcp_envelope(message)
                if direction == "client_to_server"
                else decode_registered_server_tcp_envelope(
                    message, connection_context=packet
                )
                if direction == "server_to_client"
                else None
            )
            if decoded_envelope is not None:
                plaintext = decoded_envelope[0]
                tcp_plaintexts.append({
                    "capture_time": packet.timestamp_seconds,
                    "relative_seconds": round(
                        packet.timestamp_seconds
                        - (first_capture_time or packet.timestamp_seconds),
                        6,
                    ),
                    "direction": direction,
                    "opcode": plaintext[2:4].hex() if len(plaintext) >= 4 else "",
                    "plaintext_bytes": len(plaintext),
                    "wire_bytes": len(message),
                    **packet_flow(packet),
                })
            if direction == "client_to_server" and len(message) == 11:
                short_client_messages.append({
                    "capture_time": packet.timestamp_seconds,
                    "relative_seconds": round(
                        packet.timestamp_seconds
                        - (first_capture_time or packet.timestamp_seconds),
                        6,
                    ),
                    "wire_hex": message.hex(),
                    "decoded_opcode": (
                        decoded_envelope[0][2:4].hex()
                        if decoded_envelope is not None
                        and len(decoded_envelope[0]) >= 4
                        else ""
                    ),
                    **packet_flow(packet),
                })
            inspect_request = decode_tcp_inspect_request_payload(message)
            if inspect_request is None:
                inspect_identity = decode_tcp_inspect_identity_payload(message)
                if inspect_identity is not None:
                    inspect_identity_messages.append({
                        "capture_time": packet.timestamp_seconds,
                        "relative_seconds": round(
                            packet.timestamp_seconds
                            - (first_capture_time or packet.timestamp_seconds),
                            6,
                        ),
                        "profile_id": f"0x{inspect_identity.profile_id:08X}",
                        "identity_flags": inspect_identity.identity_flags,
                        "profile_marker": inspect_identity.profile_marker,
                        "wire_bytes": len(message),
                        "key_index": inspect_identity.key_index,
                        "key_source": inspect_identity.key_source,
                        **packet_flow(packet),
                    })
            else:
                inspect_request_messages.append({
                    "capture_time": packet.timestamp_seconds,
                    "relative_seconds": round(
                        packet.timestamp_seconds
                        - (first_capture_time or packet.timestamp_seconds),
                        6,
                    ),
                    "opcode": inspect_request.opcode.hex(),
                    "profile_id": f"0x{inspect_request.profile_id:08X}",
                    "wire_bytes": len(message),
                    "key_index": inspect_request.key_index,
                    "key_source": inspect_request.key_source,
                    **packet_flow(packet),
                })
        started = time.perf_counter()
        decoded_events = (
            decoder.feed_events(packet)
            if full_decoder
            else decoder.feed_identity_profile_events(packet)
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        decoder_ms += elapsed_ms
        max_decode_ms = max(max_decode_ms, elapsed_ms)
        if elapsed_ms >= 5.0:
            slow_packets.append({
                "capture_time": packet.timestamp_seconds,
                "decode_ms": round(elapsed_ms, 3),
                "direction": packet.inferred_direction(),
                "payload_bytes": len(packet.payload),
            })
        for event in decoded_events:
            if not isinstance(event, PlayerInspectEvent):
                continue
            event_counts[event.response_layout] += 1
            events.append({
                "capture_time": packet.timestamp_seconds,
                "relative_seconds": round(
                    packet.timestamp_seconds - (first_capture_time or packet.timestamp_seconds),
                    6,
                ),
                "decode_ms": round(elapsed_ms, 3),
                "profile_id": profile_id(event),
                "target_id": event.target_id,
                "target_name": event.target_name,
                "job": event.job,
                "status": event.status,
                "layout": event.response_layout,
                "inspection_key": event.inspection_key,
                "response_delay": event_field(event, "响应延迟"),
                "response_bytes": event_field(event, "响应字节"),
                "equipment_count": len(event.equipment),
                "field_count": len(event.fields),
                **packet_flow(packet),
            })

    wall_started = time.perf_counter()
    adapter_counts: Counter[int] = Counter()
    adapter_time_ranges: dict[int, list[float]] = {}
    for timestamp, adapter, original_length, frame in read_trace(path):
        frames += 1
        adapter_counts[int(adapter)] += 1
        bounds = adapter_time_ranges.setdefault(int(adapter), [timestamp, timestamp])
        bounds[0] = min(bounds[0], timestamp)
        bounds[1] = max(bounds[1], timestamp)
        if adapters is None and adapter in SKIPPED_ADAPTERS:
            continue
        if adapters is not None and int(adapter) not in adapters:
            continue
        active_adapter = int(adapter)
        active_input_timestamp = float(timestamp)
        first_capture_time = timestamp if first_capture_time is None else first_capture_time
        last_capture_time = timestamp
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical_packet in transport.feed(packet):
            feed(logical_packet)
    for logical_packet in transport.flush():
        feed(logical_packet)
    wall_ms = (time.perf_counter() - wall_started) * 1000.0

    requests = [row for row in events if row["layout"] == "TCP:inspect_request:pending"]
    completed = [
        row
        for row in events
        if row["inspection_key"] and row["layout"] != "TCP:inspect_request:pending"
    ]
    completions_by_key = {row["inspection_key"]: row for row in completed}
    inspections: list[dict[str, object]] = []
    for index, request in enumerate(requests, 1):
        completed_row = completions_by_key.get(request["inspection_key"])
        inspections.append({
            "index": index,
            "profile_id": request["profile_id"],
            "request_at": request["relative_seconds"],
            "request_decode_ms": request["decode_ms"],
            "completed_at": completed_row["relative_seconds"] if completed_row else None,
            "wire_delay_seconds": (
                round(
                    float(completed_row["capture_time"]) - float(request["capture_time"]),
                    6,
                )
                if completed_row
                else None
            ),
            "response_decode_ms": completed_row["decode_ms"] if completed_row else None,
            "response_bytes": completed_row["response_bytes"] if completed_row else "",
            "equipment_count": completed_row["equipment_count"] if completed_row else 0,
            "target_name": completed_row["target_name"] if completed_row else request["target_name"],
            "job": completed_row["job"] if completed_row else request["job"],
            "layout": completed_row["layout"] if completed_row else "missing",
        })

    return {
        "capture": str(path),
        "mode": "full" if full_decoder else "identity_profile_only",
        "selected_adapters": sorted(adapters) if adapters is not None else "normal",
        "adapter_counts": dict(adapter_counts),
        "adapter_time_ranges": {
            str(adapter): {
                "first": round(bounds[0], 6),
                "last": round(bounds[1], 6),
                "duration_seconds": round(bounds[1] - bounds[0], 6),
            }
            for adapter, bounds in sorted(adapter_time_ranges.items())
        },
        "decoder_init_ms": round(decoder_init_ms, 3),
        "capture_duration_seconds": round(
            (last_capture_time or 0.0) - (first_capture_time or 0.0), 3
        ),
        "frames": frames,
        "logical_packets": logical_packets,
        "tcp_packets": tcp_packets,
        "replay_wall_ms": round(wall_ms, 3),
        "decoder_total_ms": round(decoder_ms, 3),
        "decoder_max_packet_ms": round(max_decode_ms, 3),
        "event_counts": dict(event_counts),
        "inspect_request_messages": inspect_request_messages,
        "inspect_identity_messages": inspect_identity_messages,
        "tcp_plaintexts": tcp_plaintexts,
        "short_client_messages": short_client_messages,
        "inspections": inspections,
        "events": events,
        "slow_packets": sorted(
            slow_packets, key=lambda row: float(row["decode_ms"]), reverse=True
        )[:40],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("dragon_nest_builder/data"),
    )
    parser.add_argument("--full-decoder", action="store_true")
    parser.add_argument(
        "--adapter",
        action="append",
        type=int,
        help="Decode only this adapter index; may be specified more than once",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(
        args.capture,
        args.data_root,
        full_decoder=args.full_decoder,
        adapters=(frozenset(args.adapter) if args.adapter else None),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
