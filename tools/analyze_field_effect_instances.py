from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, deque
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent
from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_captured_frame,
    decode_v576_damage_object_registration,
    decode_v576_status_events,
)
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decode_registered_server_tcp_envelope,
    decode_registered_tcp_envelope,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


FIELD_EFFECT_OPCODES = frozenset({b"\x13\x4a", b"\x13\x4e", b"\x13\x47"})
FIELD_EFFECT_CODES = frozenset({b"\x13\x4a\x00", b"\x13\x4e\x00", b"\x13\x47\x00"})


def _direct37_structures(
    payload: bytes,
    decoded_message: object,
) -> list[dict[str, Any]]:
    expected_source = getattr(decoded_message, "source_actor_id", None)
    expected_damage = getattr(decoded_message, "damage", None)
    matches: list[dict[str, Any]] = []
    for xor_phase in _v576_candidate_xor_phases(payload):
        decoded_body = _decode_v576_body(payload, xor_phase)
        marker_offset = decoded_body.find(b"\x08\x73\x00", 9)
        while marker_offset >= 0:
            if marker_offset >= 4 and marker_offset + 43 <= len(decoded_body):
                source_actor_id = int.from_bytes(
                    decoded_body[marker_offset + 8 : marker_offset + 12],
                    "little",
                )
                damage = int.from_bytes(
                    decoded_body[marker_offset + 19 : marker_offset + 27],
                    "little",
                )
                if source_actor_id == expected_source and damage == expected_damage:
                    start = max(0, marker_offset - 24)
                    end = min(len(decoded_body), marker_offset + 64)
                    matches.append(
                        {
                            "xor_phase": xor_phase,
                            "marker_offset": marker_offset,
                            "target_before": int.from_bytes(
                                decoded_body[marker_offset - 4 : marker_offset],
                                "little",
                            ),
                            "value_after_3": int.from_bytes(
                                decoded_body[marker_offset + 3 : marker_offset + 7],
                                "little",
                            ),
                            "value_after_4": int.from_bytes(
                                decoded_body[marker_offset + 4 : marker_offset + 8],
                                "little",
                            ),
                            "source_actor_id": source_actor_id,
                            "value_after_12": int.from_bytes(
                                decoded_body[marker_offset + 12 : marker_offset + 16],
                                "little",
                            ),
                            "damage_object_id": int.from_bytes(
                                decoded_body[marker_offset + 13 : marker_offset + 17],
                                "little",
                            ),
                            "value_after_16": int.from_bytes(
                                decoded_body[marker_offset + 16 : marker_offset + 20],
                                "little",
                            ),
                            "damage": damage,
                            "value_after_27": int.from_bytes(
                                decoded_body[marker_offset + 27 : marker_offset + 31],
                                "little",
                            ),
                            "value_after_31": int.from_bytes(
                                decoded_body[marker_offset + 31 : marker_offset + 35],
                                "little",
                            ),
                            "critical_byte": decoded_body[marker_offset + 35],
                            "body_u32_10": int.from_bytes(
                                decoded_body[10:14], "little"
                            ),
                            "body_u32_14": int.from_bytes(
                                decoded_body[14:18], "little"
                            ),
                            "body_u32_18": int.from_bytes(
                                decoded_body[18:22], "little"
                            ),
                            "body_u32_22": int.from_bytes(
                                decoded_body[22:26], "little"
                            ),
                            "context_start": start,
                            "context_hex": decoded_body[start:end].hex(),
                        }
                    )
            marker_offset = decoded_body.find(b"\x08\x73\x00", marker_offset + 1)
    return matches


def _event_row(event: object) -> dict[str, Any]:
    payload = asdict(event) if is_dataclass(event) else {}
    payload["event_type"] = type(event).__name__
    return payload


def _tcp_plaintext(payload: bytes, direction: str | None):
    if direction == "server_to_client":
        decoded = decode_registered_server_tcp_envelope(payload)
        if decoded is not None:
            return decoded
    return decode_registered_tcp_envelope(payload)


def analyze_capture(path: Path, data_root: Path) -> dict[str, Any]:
    decoder = _V576PacketEventDecoder(data_root)
    transport = V576TransportReassembler()
    origin: float | None = None
    tcp_opcode_counts: Counter[str] = Counter()
    field_records: list[dict[str, Any]] = []
    field_status_records: list[dict[str, Any]] = []
    udp_field_marker_counts: Counter[str] = Counter()
    udp_field_marker_samples: list[dict[str, Any]] = []
    direct_records: list[dict[str, Any]] = []
    dark_temple_casts: list[dict[str, Any]] = []
    damage_object_registrations: list[dict[str, Any]] = []
    recent_udp_bodies: deque[dict[str, Any]] = deque()

    def accept(packet) -> None:
        nonlocal origin
        if origin is None:
            origin = packet.timestamp_seconds
        seconds = packet.timestamp_seconds - origin
        direction = packet.inferred_direction()
        events = decoder.feed_events(packet)
        if packet.transport.casefold() == "tcp":
            envelope = _tcp_plaintext(packet.payload, direction)
            if envelope is not None:
                plaintext, key_index, rounds, key_source = envelope
                opcode = plaintext[2:4]
                tcp_opcode_counts[opcode.hex(" ")] += 1
                if opcode in FIELD_EFFECT_OPCODES:
                    field_records.append(
                        {
                            "seconds": round(seconds, 6),
                            "direction": direction,
                            "opcode": opcode.hex(" "),
                            "payload_length": len(packet.payload),
                            "plaintext_length": len(plaintext),
                            "key_index": key_index,
                            "rounds": rounds,
                            "key_source": key_source,
                            "plaintext_hex": plaintext.hex(),
                            "u32": [
                                {
                                    "offset": offset,
                                    "value": int.from_bytes(
                                        plaintext[offset : offset + 4], "little"
                                    ),
                                    "hex": plaintext[offset : offset + 4].hex(),
                                }
                                for offset in range(4, len(plaintext) - 3)
                            ],
                        }
                    )

        if packet.transport.casefold() == "udp":
            registration = decode_v576_damage_object_registration(
                packet.payload,
                direction,
            )
            if registration is not None:
                damage_object_registrations.append(
                    {
                        "seconds": round(seconds, 6),
                        **_event_row(registration),
                    }
                )

        if packet.transport.casefold() == "udp" and direction in {
            None,
            "server_to_client",
        }:
            statuses = decode_v576_status_events(
                packet.payload,
                direction,
                decoder.skill_catalog.keys(),
            )
            for status in statuses:
                if bytes.fromhex(status.effect_code) not in FIELD_EFFECT_CODES:
                    continue
                status_body = _decode_v576_body(
                    packet.payload,
                    status.xor_phase,
                )
                skill_offset = status.record_offset
                field_status_records.append(
                    {
                        "seconds": round(seconds, 6),
                        "direction": direction,
                        "payload_length": len(packet.payload),
                        "signature": (
                            packet.payload[5:9].hex()
                            if len(packet.payload) >= 9
                            else ""
                        ),
                        "status": _event_row(status),
                        "record_fields": {
                            "unknown_after_skill": int.from_bytes(
                                status_body[
                                    skill_offset + 4 : skill_offset + 8
                                ],
                                "little",
                            ),
                            "response_sequence": int.from_bytes(
                                status_body[
                                    skill_offset + 8 : skill_offset + 12
                                ],
                                "little",
                            ),
                            "tail_hex": status_body[
                                skill_offset + 20 + len(status.parameters.encode("utf-8")) :
                            ].hex(),
                        },
                    }
                )

            for xor_phase in _v576_candidate_xor_phases(packet.payload):
                decoded_body = _decode_v576_body(packet.payload, xor_phase)
                for marker in FIELD_EFFECT_CODES:
                    offset = decoded_body.find(marker, 9)
                    while offset >= 0:
                        marker_hex = marker.hex()
                        udp_field_marker_counts[marker_hex] += 1
                        if len(udp_field_marker_samples) < 1000:
                            udp_field_marker_samples.append(
                                {
                                    "seconds": round(seconds, 6),
                                    "direction": direction,
                                    "payload_length": len(packet.payload),
                                    "signature": (
                                        packet.payload[5:9].hex()
                                        if len(packet.payload) >= 9
                                        else ""
                                    ),
                                    "xor_phase": xor_phase,
                                    "marker": marker_hex,
                                    "offset": offset,
                                    "context_start": max(0, offset - 24),
                                    "context_hex": decoded_body[
                                        max(0, offset - 24) : offset + 64
                                    ].hex(),
                                    "contains_direct37": b"\x08\x73\x00"
                                    in decoded_body,
                                }
                            )
                        offset = decoded_body.find(marker, offset + 1)

        decoded = getattr(decoder, "last_decoded_message", None)
        if decoded is not None and "direct37" in str(getattr(decoded, "layout", "")):
            direct37_structures = _direct37_structures(
                packet.payload,
                decoded,
            )
            prior_object_references: list[dict[str, Any]] = []
            result_object_ids = {
                int(row["value_after_4"])
                for row in direct37_structures
                if int(row.get("value_after_4", 0)) > 0
            }
            for result_object_id in result_object_ids:
                needle = result_object_id.to_bytes(4, "little")
                for prior in recent_udp_bodies:
                    for xor_phase, body in prior["bodies"]:
                        offset = body.find(needle)
                        if offset < 0:
                            continue
                        prior_object_references.append(
                            {
                                "result_object_id": result_object_id,
                                "seconds": prior["seconds"],
                                "delta_seconds": round(
                                    seconds - prior["seconds"], 6
                                ),
                                "payload_length": prior["payload_length"],
                                "signature": prior["signature"],
                                "xor_phase": xor_phase,
                                "offset": offset,
                            }
                        )
            direct_records.append(
                {
                    "seconds": round(seconds, 6),
                    "direction": direction,
                    "payload_length": len(packet.payload),
                    "signature": (
                        packet.payload[5:9].hex()
                        if len(packet.payload) >= 9
                        else ""
                    ),
                    "decoded": _event_row(decoded),
                    "direct37_structures": direct37_structures,
                    "prior_object_references": prior_object_references,
                    "events": [
                        _event_row(event)
                        for event in events
                        if isinstance(event, CombatEvent)
                    ],
                }
            )

        for event in events:
            if (
                isinstance(event, SkillCastEvent)
                and event.skill_name in {"黑暗神殿", "黑暗神殿EX"}
            ):
                row = _event_row(event)
                row["packet"] = {
                    "seconds": round(seconds, 6),
                    "direction": direction,
                    "transport": packet.transport,
                    "payload_length": len(packet.payload),
                    "signature": (
                        packet.payload[5:9].hex()
                        if len(packet.payload) >= 9
                        else ""
                    ),
                    "decoded": (
                        _event_row(decoded) if decoded is not None else None
                    ),
                    "candidate_bodies": [
                        {
                            "xor_phase": xor_phase,
                            "body_hex": _decode_v576_body(
                                packet.payload, xor_phase
                            ).hex(),
                        }
                        for xor_phase in _v576_candidate_xor_phases(
                            packet.payload
                        )
                    ]
                    if packet.transport.casefold() == "udp"
                    else [],
                }
                dark_temple_casts.append(row)

        if packet.transport.casefold() == "udp":
            while (
                recent_udp_bodies
                and seconds - recent_udp_bodies[0]["seconds"] > 2.0
            ):
                recent_udp_bodies.popleft()
            recent_udp_bodies.append(
                {
                    "seconds": round(seconds, 6),
                    "payload_length": len(packet.payload),
                    "signature": (
                        packet.payload[5:9].hex()
                        if len(packet.payload) >= 9
                        else ""
                    ),
                    "bodies": [
                        (
                            xor_phase,
                            _decode_v576_body(packet.payload, xor_phase),
                        )
                        for xor_phase in _v576_candidate_xor_phases(
                            packet.payload
                        )
                    ],
                }
            )

    for timestamp, _adapter, original_length, frame in read_trace(path):
        packet = decode_captured_frame(timestamp, frame, original_length)
        if packet is None:
            continue
        for logical in transport.feed(packet):
            accept(logical)
    for logical in transport.flush():
        accept(logical)

    cast_seconds = [row["timestamp_ms"] / 1000.0 for row in dark_temple_casts]
    for record in field_records:
        seconds = float(record["seconds"])
        record["nearby_cast_offsets"] = [
            round(seconds - cast_second, 6)
            for cast_second in cast_seconds
            if -2.0 <= seconds - cast_second <= 10.0
        ]
    for record in direct_records:
        seconds = float(record["seconds"])
        record["nearby_cast_offsets"] = [
            round(seconds - cast_second, 6)
            for cast_second in cast_seconds
            if -2.0 <= seconds - cast_second <= 10.0
        ]

    return {
        "capture": str(path),
        "dark_temple_casts": dark_temple_casts,
        "damage_object_registrations": damage_object_registrations,
        "field_records": field_records,
        "field_status_records": field_status_records,
        "udp_field_marker_counts": dict(udp_field_marker_counts.most_common()),
        "udp_field_marker_samples": udp_field_marker_samples,
        "direct37_records": direct_records,
        "tcp_opcode_counts": dict(tcp_opcode_counts.most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT / "dragon_nest_builder" / "data",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze_capture(args.capture, args.data_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        " ".join(
            (
                f"casts={len(report['dark_temple_casts'])}",
                f"field_records={len(report['field_records'])}",
                f"field_status_records={len(report['field_status_records'])}",
                f"udp_field_markers={sum(report['udp_field_marker_counts'].values())}",
                f"direct37={len(report['direct37_records'])}",
                f"output={args.output}",
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
