"""Audit Soul Eater field/status generations without using timing for identity.

This is a discovery tool.  It follows a 5207 cast forward and reports concrete
status instances and damage-object generations.  Time deltas are only used to
present nearby protocol records; no production attribution is changed here.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_captured_frame,
    decode_v576_status_events,
)
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder  # noqa: E402
from dragon_nest_builder.live_dps.skill_attribution_v2 import (  # noqa: E402
    SkillAttributionEngineV2,
)

try:  # noqa: E402
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover
    from tools.analyze_npcap_trace import read_trace


FOCUS_SKILL_ID = 5207
FOCUS_JOBS = frozenset({"噬魂者", "黑暗萨满"})
WEAK_METHODS = frozenset(
    {
        "cast_instance_match",
        "continuation_window",
        "latest_profiled_direct_cast",
        "learned_cast_profile",
        "unique_active_cast",
        "sustained_cast_window",
        "damage_object_projectile_link_provisional",
        "unresolved",
    }
)


def _actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def analyze(path: Path) -> dict[str, Any]:
    decoder = _V576PacketEventDecoder(DATA_DIR)
    transport = V576TransportReassembler()
    origin: float | None = None
    casts: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    registrations: list[dict[str, Any]] = []
    damages: list[dict[str, Any]] = []
    packet_index = 0
    active_field_instances: dict[tuple[int, int], dict[str, Any]] = {}
    instance_occurrences: list[dict[str, Any]] = []
    focus_cast_events: list[SkillCastEvent] = []
    focus_damage_events: list[CombatEvent] = []

    original_registration = decoder.damage_object_origins.observe_registration

    def observe_registration(**kwargs):
        registrations.append(
            {
                "timestamp": float(kwargs["timestamp"]),
                "actor": int(kwargs["source_actor_id"]),
                "object": int(kwargs["damage_object_id"]),
                "template": int(kwargs["projectile_template_id"]),
                "layout": str(kwargs.get("layout") or ""),
            }
        )
        return original_registration(**kwargs)

    decoder.damage_object_origins.observe_registration = observe_registration

    def accept(packet) -> None:
        nonlocal origin, packet_index
        packet_index += 1
        if origin is None:
            origin = packet.timestamp_seconds
        if packet.transport.casefold() == "udp":
            decoded_statuses = decode_v576_status_events(
                packet.payload,
                packet.inferred_direction(),
                decoder.skill_catalog.keys(),
            )
            for status in decoded_statuses:
                decoded_body = _decode_v576_body(packet.payload, status.xor_phase)
                parameter_end = (
                    int(status.record_offset)
                    + 20
                    + len(status.parameters.encode("utf-8"))
                )
                statuses.append(
                    {
                        "timestamp": packet.timestamp_seconds,
                        "packet_index": packet_index,
                        "packet_length": len(packet.payload),
                        "packet_signature": (
                            packet.payload[5:9].hex()
                            if len(packet.payload) >= 9
                            else ""
                        ),
                        "tail_hex": decoded_body[
                            parameter_end : parameter_end + 24
                        ].hex(),
                        **asdict(status),
                    }
                )
                if (
                    status.skill_id == FOCUS_SKILL_ID
                    and status.effect_code == "135d00"
                    and status.effect_instance_id
                ):
                    active_field_instances[
                        (status.source_actor_id, status.effect_instance_id)
                    ] = {
                        "actor": status.source_actor_id,
                        "instance": status.effect_instance_id,
                        "applied_at": packet.timestamp_seconds,
                        "applied_packet": packet_index,
                        "expires_at": packet.timestamp_seconds
                        + max(0, status.duration_ms) / 1000.0,
                    }
            expired = [
                key
                for key, state in active_field_instances.items()
                if state["expires_at"] + 0.5 < packet.timestamp_seconds
            ]
            for key in expired:
                active_field_instances.pop(key, None)
            for state in active_field_instances.values():
                if state["applied_packet"] == packet_index:
                    continue
                needle = int(state["instance"]).to_bytes(4, "little")
                matches: set[tuple[int, int]] = set()
                for phase in _v576_candidate_xor_phases(packet.payload):
                    body = _decode_v576_body(packet.payload, phase)
                    offset = body.find(needle, 9)
                    while offset >= 0:
                        matches.add((phase, offset))
                        offset = body.find(needle, offset + 1)
                for phase, offset in sorted(matches):
                    instance_occurrences.append(
                        {
                            **state,
                            "packet_index": packet_index,
                            "timestamp": packet.timestamp_seconds,
                            "delta_ms": round(
                                (packet.timestamp_seconds - state["applied_at"])
                                * 1000
                            ),
                            "phase": phase,
                            "offset": offset,
                            "packet_length": len(packet.payload),
                            "packet_signature": (
                                packet.payload[5:9].hex()
                                if len(packet.payload) >= 9
                                else ""
                            ),
                        }
                    )
        for event in decoder.feed_events(packet):
            if isinstance(event, SkillCastEvent) and event.job in FOCUS_JOBS:
                try:
                    skill_id = int(event.skill_id)
                except (TypeError, ValueError):
                    continue
                casts.append(
                    {
                        "timestamp_ms": int(event.timestamp_ms),
                        "timestamp": packet.timestamp_seconds,
                        "packet_index": packet_index,
                        "actor": _actor(event.source_id),
                        "job": event.job,
                        "skill_id": skill_id,
                        "skill_name": event.skill_name,
                    }
                )
                focus_cast_events.append(event)
            elif (
                isinstance(event, CombatEvent)
                and not event.healing
                and event.job in FOCUS_JOBS
            ):
                focus_damage_events.append(event)
                try:
                    skill_id = int(event.skill_id)
                except (TypeError, ValueError):
                    skill_id = -1
                record_offset = int(event.wire_record_offset or 0)
                decoded_record_hex = ""
                decoded_record_phase = -1
                if "direct37" in str(event.wire_damage_layout or ""):
                    for phase in _v576_candidate_xor_phases(packet.payload):
                        body = _decode_v576_body(packet.payload, phase)
                        if body[record_offset : record_offset + 3] == b"\x08\x73\x00":
                            decoded_record_phase = phase
                            decoded_record_hex = body[
                                max(0, record_offset - 8) : record_offset + 64
                            ].hex()
                            break
                damages.append(
                    {
                        "timestamp_ms": int(event.timestamp_ms),
                        "timestamp": packet.timestamp_seconds,
                        "packet_index": packet_index,
                        "actor": _actor(event.source_id),
                        "job": event.job,
                        "skill_id": skill_id,
                        "skill_name": event.skill_name,
                        "method": str(event.attribution_method or ""),
                        "object": int(event.wire_damage_object_id or 0),
                        "result_group": int(event.wire_result_group_id or 0),
                        "damage": int(event.amount),
                        "layout": str(event.wire_damage_layout or ""),
                        "record_offset": record_offset,
                        "decoded_record_phase": decoded_record_phase,
                        "decoded_record_hex": decoded_record_hex,
                        "payload_length": len(packet.payload),
                        "packet_signature": (
                            packet.payload[5:9].hex()
                            if len(packet.payload) >= 9
                            else ""
                        ),
                        "cast_instance_id": str(event.cast_instance_id or ""),
                        "raw_skill_id": str(event.raw_skill_id or ""),
                        "raw_skill_name": str(event.raw_skill_name or ""),
                        "raw_attribution_method": str(
                            event.raw_attribution_method or ""
                        ),
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

    v2_result = SkillAttributionEngineV2().resolve(
        focus_damage_events,
        focus_cast_events,
    )
    if len(v2_result.events) != len(damages):
        raise RuntimeError("V2 audit event count changed")
    for row, event in zip(damages, v2_result.events, strict=True):
        row["v2_skill_id"] = str(event.skill_id)
        row["v2_skill_name"] = str(event.skill_name)
        row["v2_method"] = str(event.attribution_method or "")
        row["v2_raw_skill_id"] = str(event.raw_skill_id or "")
        row["v2_raw_skill_name"] = str(event.raw_skill_name or "")
        row["v2_raw_method"] = str(event.raw_attribution_method or "")

    start = float(origin or 0.0)
    for rows in (casts, statuses, registrations, damages):
        for row in rows:
            row["seconds"] = round(float(row["timestamp"]) - start, 6)

    registrations_by_key: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in registrations:
        registrations_by_key[(row["actor"], row["object"])].append(row)
    for rows in registrations_by_key.values():
        rows.sort(key=lambda item: item["timestamp"])

    for hit in damages:
        hit["generation_template"] = 0
        hit["generation_age_ms"] = -1
        rows = registrations_by_key.get((hit["actor"], hit["object"]), ())
        if rows:
            points = [row["timestamp"] for row in rows]
            index = bisect_right(points, hit["timestamp"]) - 1
            if index >= 0:
                registration = rows[index]
                hit["generation_template"] = registration["template"]
                hit["generation_age_ms"] = round(
                    (hit["timestamp"] - registration["timestamp"]) * 1000
                )

    focus_casts = [row for row in casts if row["skill_id"] == FOCUS_SKILL_ID]
    cast_reports: list[dict[str, Any]] = []
    status_signature_support: Counter[tuple[Any, ...]] = Counter()
    registration_support: Counter[int] = Counter()
    object_hit_summary: Counter[tuple[int, int, str, int]] = Counter()
    for cast in focus_casts:
        actor = cast["actor"]
        cast_time = cast["timestamp"]
        nearby_statuses = [
            row
            for row in statuses
            if row["source_actor_id"] == actor
            and -0.25 <= row["timestamp"] - cast_time <= 4.0
        ]
        status_signatures = {
            (
                row["skill_id"],
                row["effect_code"],
                row["effect_class_id"],
                row["parameters"],
                row["duration_ms"],
            )
            for row in nearby_statuses
        }
        status_signature_support.update(status_signatures)
        nearby_registrations = [
            row
            for row in registrations
            if row["actor"] == actor
            and -0.25 <= row["timestamp"] - cast_time <= 4.0
        ]
        registration_support.update({row["template"] for row in nearby_registrations})
        nearby_damage = [
            row
            for row in damages
            if row["actor"] == actor
            and 0.0 <= row["timestamp"] - cast_time <= 8.5
        ]
        cast_reports.append(
            {
                "cast_seconds": cast["seconds"],
                "actor": actor,
                "statuses": [
                    {
                        "delta_ms": round((row["timestamp"] - cast_time) * 1000),
                        "skill_id": row["skill_id"],
                        "skill_level_row_id": row["skill_level_row_id"],
                        "effect_code": row["effect_code"],
                        "effect_class_id": row["effect_class_id"],
                        "effect_instance_id": row["effect_instance_id"],
                        "target_actor_id": row["target_actor_id"],
                        "duration_ms": row["duration_ms"],
                        "parameters": row["parameters"],
                        "packet_index": row["packet_index"],
                        "packet_length": row["packet_length"],
                        "packet_signature": row["packet_signature"],
                        "tail_hex": row["tail_hex"],
                    }
                    for row in nearby_statuses
                ],
                "registrations": [
                    {
                        "delta_ms": round((row["timestamp"] - cast_time) * 1000),
                        "object": row["object"],
                        "template": row["template"],
                        "layout": row["layout"],
                    }
                    for row in nearby_registrations
                ],
                "damage": [
                    {
                        "delta_ms": round((row["timestamp"] - cast_time) * 1000),
                        "skill_id": row["skill_id"],
                        "skill_name": row["skill_name"],
                        "method": row["method"],
                        "object": row["object"],
                        "result_group": row["result_group"],
                        "generation_template": row["generation_template"],
                        "generation_age_ms": row["generation_age_ms"],
                        "damage": row["damage"],
                        "layout": row["layout"],
                        "record_offset": row["record_offset"],
                        "payload_length": row["payload_length"],
                        "packet_signature": row["packet_signature"],
                        "packet_index": row["packet_index"],
                        "decoded_record_phase": row["decoded_record_phase"],
                        "decoded_record_hex": row["decoded_record_hex"],
                    }
                    for row in nearby_damage
                ],
            }
        )

    for hit in damages:
        if hit["method"] not in WEAK_METHODS:
            continue
        object_hit_summary[
            (
                hit["skill_id"],
                hit["generation_template"],
                hit["method"],
                hit["object"],
            )
        ] += 1

    return {
        "capture": str(path),
        "focus_cast_count": len(focus_casts),
        "status_signature_support": [
            {
                "skill_id": key[0],
                "effect_code": key[1],
                "effect_class_id": key[2],
                "parameters": key[3],
                "duration_ms": key[4],
                "casts": count,
            }
            for key, count in status_signature_support.most_common()
        ],
        "registration_template_support": [
            {"template": template, "casts": count}
            for template, count in registration_support.most_common()
        ],
        "weak_object_hit_summary": [
            {
                "skill_id": key[0],
                "template": key[1],
                "method": key[2],
                "object": key[3],
                "hits": count,
            }
            for key, count in object_hit_summary.most_common()
        ],
        "v2_summary": {
            "accepted_hits": v2_result.accepted_hits,
            "unresolved_hits": v2_result.unresolved_hits,
            "accepted_damage": v2_result.accepted_damage,
            "unresolved_damage": v2_result.unresolved_damage,
        },
        "all_damage": damages,
        "field_instance_occurrences": [
            {
                **row,
                "damage_rows": [
                    {
                        "skill_id": hit["skill_id"],
                        "method": hit["method"],
                        "object": hit["object"],
                        "result_group": hit["result_group"],
                        "damage": hit["damage"],
                        "layout": hit["layout"],
                        "record_offset": hit["record_offset"],
                    }
                    for hit in damages
                    if hit["packet_index"] == row["packet_index"]
                ],
            }
            for row in instance_occurrences
        ],
        "casts": cast_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [analyze(path) for path in args.captures]
    output = {
        "schema": "soul-eater-field-forward-v135",
        "policy": "cast-forward evidence only; timing is not an identity key",
        "captures": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    for report in reports:
        print(Path(report["capture"]).name, report["focus_cast_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
