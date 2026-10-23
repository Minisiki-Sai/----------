from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_damage_object_registration,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


MARKERS = {
    b"\x09\x5d\x00": "09 5D",
    b"\x09\x61\x00": "09 61",
    b"\x09\x6e\x00": "09 6E",
}


@dataclass(frozen=True, slots=True)
class Registration:
    capture: str
    timestamp: float
    actor_id: int
    object_id: int
    template_id: int
    resource_id: int
    child_sequence: int
    body: bytes


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    capture: str
    timestamp: float
    signature: str
    payload_length: int
    xor_phase: int
    marker: str
    marker_offset: int
    prefix_actor_candidate: int
    owner_id: int
    object_id: int
    marker_plus_12: int
    owner_repeat: int
    body: bytes


@dataclass(frozen=True, slots=True)
class DamageHit:
    capture: str
    timestamp: float
    actor_id: int
    object_id: int
    skill_id: int
    attribution_method: str


def _all_occurrences(body: bytes, token: bytes) -> Iterable[int]:
    offset = body.find(token, 9)
    while offset >= 0:
        yield offset
        offset = body.find(token, offset + 1)


def _u16(body: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 2 > len(body):
        return None
    return int.from_bytes(body[offset : offset + 2], "little")


def _u32(body: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 4 > len(body):
        return None
    return int.from_bytes(body[offset : offset + 4], "little")


def _normalise_resource_token(value: str) -> str:
    token = Path(str(value).replace("\\", "/")).stem.casefold()
    token = re.sub(r"^(?:skill_|soceress_|sorceress_|warrior_|archer_)", "", token)
    token = re.sub(r"_(?:pve|combo|start|loop|end|lv\d+)$", "", token)
    return re.sub(r"[^a-z0-9]", "", token)


def load_client_tables(resource_root: Path) -> dict[str, Any]:
    ext = resource_root / "ext"
    projectile_rows = parse_dnt(ext / "weapontable_projectile.dnt")
    file_rows = parse_dnt(ext / "filetable.dnt")
    skill_rows = parse_dnt(ext / "skilltable_character.dnt")
    state_rows = parse_dnt(ext / "stateeffecttable.dnt")

    files = {
        int(row["RowId"]): str(row.get("_FileName", "") or "")
        for row in file_rows
    }
    projectiles: dict[int, dict[str, Any]] = {}
    for row in projectile_rows:
        template_id = int(row["RowId"])
        act_id = int(row.get("_ActName", 0) or 0)
        projectiles[template_id] = {
            "template_id": template_id,
            "act_file_id": act_id,
            "act_file": files.get(act_id, ""),
            "length": int(row.get("_Length", 0) or 0),
            "set_effect_act_file_id": int(row.get("_SetEffect_ActName", 0) or 0),
        }

    skills = {
        int(row["RowId"]): {
            "skill_id": int(row["RowId"]),
            "static_name": str(row.get("_StaticName", "") or ""),
            "base_skill_id": int(row.get("_BaseSkillID", 0) or 0),
            "processors": [
                int(row.get(f"_Processor{index}", 0) or 0)
                for index in range(1, 6)
            ],
        }
        for row in skill_rows
    }

    process_params: dict[int, set[str]] = defaultdict(set)
    level_rows = 0
    skill_level_row_ids: set[int] = set()
    for level_path in sorted(ext.glob("skillleveltable_character*.dnt")):
        for row in parse_dnt(level_path):
            level_rows += 1
            skill_level_row_ids.add(int(row["RowId"]))
            skill_id = int(row.get("_SkillIndex", 0) or 0)
            if skill_id <= 0 or int(row.get("_ApplyType", 0) or 0) != 0:
                continue
            for index in range(1, 11):
                raw = str(row.get(f"_ProcessParam{index}", "") or "")
                for value in raw.split(";"):
                    value = value.strip()
                    if value and not value.replace(".", "", 1).isdigit():
                        process_params[skill_id].add(value)

    # This is deliberately kept as a descriptive hint, not protocol proof.
    # Projectile -> ACT is a numeric DNT join. SkillLevel ProcessParam -> ACT
    # has no numeric foreign key in these tables and can only be compared by
    # resource token/processor semantics.
    act_token_to_skills: dict[str, set[int]] = defaultdict(set)
    for skill_id, params in process_params.items():
        for param in params:
            token = _normalise_resource_token(param)
            if token:
                act_token_to_skills[token].add(skill_id)

    for projectile in projectiles.values():
        token = _normalise_resource_token(str(projectile["act_file"]))
        hints: set[int] = set()
        if token:
            for process_token, ids in act_token_to_skills.items():
                if token == process_token or token in process_token or process_token in token:
                    hints.update(ids)
        projectile["skill_name_hints_not_protocol_proof"] = sorted(hints)
        projectile["skilllevel_name_bridge_not_protocol_proof"] = [
            {
                **skills[skill_id],
                "process_params": sorted(process_params.get(skill_id, ())),
            }
            for skill_id in sorted(hints)
            if skill_id in skills
        ]

    return {
        "resource_root": str(resource_root),
        "row_counts": {
            "weapontable_projectile": len(projectile_rows),
            "filetable": len(file_rows),
            "skilltable_character": len(skill_rows),
            "skillleveltable_character_all": level_rows,
            "stateeffecttable": len(state_rows),
        },
        "projectiles": projectiles,
        "file_ids": set(files),
        "file_act_ids": {
            row_id
            for row_id, name in files.items()
            if Path(name).suffix.casefold() == ".act"
        },
        "file_effect_ids": {
            row_id
            for row_id, name in files.items()
            if Path(name).suffix.casefold() in {".eff", ".ptc"}
        },
        "projectile_act_file_ids": {
            int(row["act_file_id"])
            for row in projectiles.values()
            if int(row["act_file_id"]) > 0
        },
        "skill_ids": set(skills),
        "skill_level_row_ids": skill_level_row_ids,
        "state_effect_ids": {int(row["RowId"]) for row in state_rows},
        "state_effect_act_file_ids": {
            int(row.get("_EffectActFileName", 0) or 0)
            for row in state_rows
            if int(row.get("_EffectActFileName", 0) or 0) > 0
        },
    }


def collect_capture(
    path: Path,
) -> tuple[list[Registration], list[ObjectRecord], list[DamageHit]]:
    capture = str(path)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    registrations: list[Registration] = []
    records: list[ObjectRecord] = []
    seen_records: set[tuple[float, int, int, int, str]] = set()

    def feed_events(packet):
        payload = bytes(packet.payload)
        direction = packet.inferred_direction()
        registration = decode_v576_damage_object_registration(payload, direction)
        if registration is not None:
            registrations.append(
                Registration(
                    capture=capture,
                    timestamp=float(packet.timestamp_seconds),
                    actor_id=int(registration.source_actor_id),
                    object_id=int(registration.damage_object_id),
                    template_id=int(registration.projectile_template_id),
                    resource_id=int(registration.effect_resource_id),
                    child_sequence=int(registration.child_sequence),
                    body=_decode_v576_body(payload, registration.xor_phase),
                )
            )
        if direction == "server_to_client":
            signature = payload[5:9].hex() if len(payload) >= 9 else ""
            for phase in _v576_candidate_xor_phases(payload):
                body = _decode_v576_body(payload, phase)
                for marker_bytes, marker_name in MARKERS.items():
                    for marker_offset in _all_occurrences(body, marker_bytes):
                        if marker_offset < 8 or marker_offset + 48 > len(body):
                            continue
                        prefix_actor_candidate = int.from_bytes(
                            body[marker_offset - 8 : marker_offset - 4], "little"
                        )
                        owner_id = int.from_bytes(
                            body[marker_offset - 4 : marker_offset], "little"
                        )
                        object_id = int.from_bytes(
                            body[marker_offset + 8 : marker_offset + 12], "little"
                        )
                        marker_plus_12 = int.from_bytes(
                            body[marker_offset + 12 : marker_offset + 16], "little"
                        )
                        owner_repeat = int.from_bytes(
                            body[marker_offset + 44 : marker_offset + 48], "little"
                        )
                        if owner_id <= 0 or owner_repeat != owner_id or object_id <= 0:
                            continue
                        key = (
                            float(packet.timestamp_seconds),
                            marker_offset,
                            owner_id,
                            object_id,
                            marker_name,
                        )
                        if key in seen_records:
                            continue
                        seen_records.add(key)
                        records.append(
                            ObjectRecord(
                                capture=capture,
                                timestamp=float(packet.timestamp_seconds),
                                signature=signature,
                                payload_length=len(payload),
                                xor_phase=phase,
                                marker=marker_name,
                                marker_offset=marker_offset,
                                prefix_actor_candidate=prefix_actor_candidate,
                                owner_id=owner_id,
                                object_id=object_id,
                                marker_plus_12=marker_plus_12,
                                owner_repeat=owner_repeat,
                                body=body,
                            )
                        )
        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        events = source.poll()
    finally:
        source.close()
    origin = float(source.capture_origin_timestamp_seconds or 0.0)
    hits: list[DamageHit] = []
    for event in events:
        if (
            not isinstance(event, CombatEvent)
            or event.healing
            or int(event.wire_damage_object_id or 0) <= 0
        ):
            continue
        try:
            actor_id = int(str(event.source_id), 0)
        except ValueError:
            continue
        try:
            skill_id = int(str(event.skill_id), 0)
        except ValueError:
            skill_id = 0
        hits.append(
            DamageHit(
                capture=capture,
                timestamp=origin + int(event.timestamp_ms) / 1000.0,
                actor_id=actor_id,
                object_id=int(event.wire_damage_object_id),
                skill_id=skill_id,
                attribution_method=str(event.attribution_method or ""),
            )
        )
    return registrations, records, hits


def _match_records(
    registrations: list[Registration],
    records: list[ObjectRecord],
    window_ms: int,
) -> tuple[list[tuple[Registration, ObjectRecord, float]], int]:
    by_key: dict[tuple[str, int, int], list[ObjectRecord]] = defaultdict(list)
    for record in records:
        by_key[(record.capture, record.owner_id, record.object_id)].append(record)
    for values in by_key.values():
        values.sort(key=lambda row: row.timestamp)

    matched: list[tuple[Registration, ObjectRecord, float]] = []
    ambiguous = 0
    max_age = max(1, window_ms) / 1000.0
    for registration in registrations:
        candidates = by_key.get(
            (registration.capture, registration.actor_id, registration.object_id), ()
        )
        if not candidates:
            continue
        times = [row.timestamp for row in candidates]
        left = bisect.bisect_left(times, registration.timestamp - max_age)
        right = bisect.bisect_right(times, registration.timestamp + max_age)
        window = list(candidates[left:right])
        if not window:
            continue
        minimum = min(abs(row.timestamp - registration.timestamp) for row in window)
        nearest = [
            row
            for row in window
            if abs(abs(row.timestamp - registration.timestamp) - minimum) < 1e-9
        ]
        if len(nearest) != 1:
            ambiguous += 1
            continue
        record = nearest[0]
        matched.append(
            (
                registration,
                record,
                (record.timestamp - registration.timestamp) * 1000.0,
            )
        )
    return matched, ambiguous


def audit(
    captures: list[Path],
    resource_root: Path,
    window_ms: int,
) -> dict[str, Any]:
    tables = load_client_tables(resource_root)
    projectiles: dict[int, dict[str, Any]] = tables["projectiles"]
    all_registrations: list[Registration] = []
    all_records: list[ObjectRecord] = []
    all_hits: list[DamageHit] = []
    capture_rows: list[dict[str, Any]] = []
    for path in captures:
        registrations, records, hits = collect_capture(path)
        all_registrations.extend(registrations)
        all_records.extend(records)
        all_hits.extend(hits)
        capture_rows.append(
            {
                "capture": str(path),
                "registrations": len(registrations),
                "s2c_object_records": len(records),
                "direct37_object_hits": len(hits),
                "markers": dict(Counter(row.marker for row in records)),
                "marker_plus_12_projectile_support": {
                    marker: {
                        "records": len(marker_records),
                        "projectile_rows": sum(
                            row.marker_plus_12 in projectiles
                            for row in marker_records
                        ),
                    }
                    for marker in sorted({row.marker for row in records})
                    for marker_records in (
                        [row for row in records if row.marker == marker],
                    )
                },
            }
        )

    matched, ambiguous = _match_records(
        all_registrations, all_records, window_ms
    )
    registration_template_ids = {
        row.template_id for row in all_registrations
    }
    equality: dict[str, Counter[str]] = defaultdict(Counter)
    offset_value_templates: dict[
        tuple[str, int, int], dict[int, Counter[int]]
    ] = defaultdict(lambda: defaultdict(Counter))
    examples: list[dict[str, Any]] = []

    for registration, record, delta_ms in matched:
        projectile = projectiles.get(registration.template_id, {})
        labels = {
            "projectile_template_id": registration.template_id,
            "projectile_act_file_id": int(projectile.get("act_file_id", 0) or 0),
            "registration_effect_resource_id": registration.resource_id,
            "registration_child_sequence": registration.child_sequence,
        }
        for label, expected in labels.items():
            for width in (2, 4):
                if width == 2 and not 0 <= expected <= 0xFFFF:
                    continue
                for relative_offset in range(-8, 65):
                    absolute = record.marker_offset + relative_offset
                    value = (
                        _u16(record.body, absolute)
                        if width == 2
                        else _u32(record.body, absolute)
                    )
                    if value == expected:
                        equality[label][
                            f"{record.marker}:u{width * 8}:marker{relative_offset:+d}"
                        ] += 1

        for width in (2, 4):
            for relative_offset in range(-8, 65):
                absolute = record.marker_offset + relative_offset
                value = (
                    _u16(record.body, absolute)
                    if width == 2
                    else _u32(record.body, absolute)
                )
                if value is not None:
                    offset_value_templates[(record.marker, width, relative_offset)][
                        value
                    ][registration.template_id] += 1

        if len(examples) < 80:
            marker_value = record.marker_plus_12
            examples.append(
                {
                    "capture": registration.capture,
                    "delta_ms": round(delta_ms, 3),
                    "marker": record.marker,
                    "marker_offset": record.marker_offset,
                    "prefix_actor_candidate": record.prefix_actor_candidate,
                    "owner_id": record.owner_id,
                    "object_id": record.object_id,
                    "registration_template_id": registration.template_id,
                    "marker_plus_12": marker_value,
                    "marker_plus_12_is_same_template": (
                        marker_value == registration.template_id
                    ),
                    "marker_plus_12_table_membership": {
                        "weapontable_projectile.RowId": marker_value in projectiles,
                        "filetable.RowId": marker_value in tables["file_ids"],
                        "skilltable_character.RowId": marker_value in tables["skill_ids"],
                        "skillleveltable_character.RowId": (
                            marker_value in tables["skill_level_row_ids"]
                        ),
                        "stateeffecttable.RowId": (
                            marker_value in tables["state_effect_ids"]
                        ),
                    },
                    "projectile_row": projectile,
                    "record_hex": record.body[
                        max(0, record.marker_offset - 8) :
                        min(len(record.body), record.marker_offset + 64)
                    ].hex(" "),
                }
            )

    marker_summary: list[dict[str, Any]] = []
    for marker in sorted(set(row.marker for row in all_records)):
        rows = [(reg, rec, delta) for reg, rec, delta in matched if rec.marker == marker]
        same = sum(rec.marker_plus_12 == reg.template_id for reg, rec, _ in rows)
        marker_summary.append(
            {
                "marker": marker,
                "matched_registrations": len(rows),
                "marker_plus_12_equals_registration_template": same,
                "conflicts": len(rows) - same,
                "support_percent": round(same / len(rows) * 100.0, 6) if rows else 0.0,
                "marker_plus_12_in_projectile_table": sum(
                    rec.marker_plus_12 in projectiles for _, rec, _ in rows
                ),
                "distinct_marker_plus_12": len(
                    {rec.marker_plus_12 for _, rec, _ in rows}
                ),
            }
        )

    table_sets = {
        "weapontable_projectile.RowId": set(projectiles),
        "filetable.RowId": tables["file_ids"],
        "skilltable_character.RowId": tables["skill_ids"],
        "skillleveltable_character.RowId": tables["skill_level_row_ids"],
        "stateeffecttable.RowId": tables["state_effect_ids"],
        "stateeffecttable._EffectActFileName": tables[
            "state_effect_act_file_ids"
        ],
        "weapontable_projectile._ActName": tables[
            "projectile_act_file_ids"
        ],
        "filetable.ACT_resource_RowId": tables["file_act_ids"],
        "filetable.Effect_resource_RowId": tables["file_effect_ids"],
        "observed_107_template_id": registration_template_ids,
    }
    marker_field_membership: list[dict[str, Any]] = []
    for marker in sorted(set(row.marker for row in all_records)):
        marker_records = [row for row in all_records if row.marker == marker]
        for width in (2, 4):
            for relative_offset in range(-8, 65):
                values = [
                    value
                    for row in marker_records
                    if (
                        value := (
                            _u16(row.body, row.marker_offset + relative_offset)
                            if width == 2
                            else _u32(row.body, row.marker_offset + relative_offset)
                        )
                    )
                    is not None
                ]
                if not values:
                    continue
                memberships = {
                    name: sum(value in ids for value in values)
                    for name, ids in table_sets.items()
                }
                best_table, best_hits = max(
                    memberships.items(), key=lambda item: item[1]
                )
                if best_hits < max(3, len(values) // 20):
                    continue
                marker_field_membership.append(
                    {
                        "marker": marker,
                        "width_bits": width * 8,
                        "relative_offset": relative_offset,
                        "samples": len(values),
                        "distinct_values": len(set(values)),
                        "memberships": memberships,
                        "best_table": best_table,
                        "best_support_percent": round(
                            best_hits / len(values) * 100.0, 6
                        ),
                    }
                )
    marker_field_membership.sort(
        key=lambda row: (
            -float(row["best_support_percent"]),
            -int(row["distinct_values"]),
            -int(row["samples"]),
        )
    )

    marker_plus_12_all: list[dict[str, Any]] = []
    for marker in sorted(set(row.marker for row in all_records)):
        marker_records = [row for row in all_records if row.marker == marker]
        values = Counter(row.marker_plus_12 for row in marker_records)
        valid = sum(
            count for value, count in values.items() if value in projectiles
        )
        known_107 = sum(
            count
            for value, count in values.items()
            if value in registration_template_ids
        )
        act_exists = 0
        for value, count in values.items():
            projectile = projectiles.get(value)
            if not projectile or not projectile.get("act_file"):
                continue
            if (resource_root / "weapon" / str(projectile["act_file"])).is_file():
                act_exists += count
        marker_plus_12_all.append(
            {
                "marker": marker,
                "records": len(marker_records),
                "distinct_values": len(values),
                "projectile_row_support": valid,
                "projectile_row_support_percent": round(
                    valid / len(marker_records) * 100.0, 6
                )
                if marker_records
                else 0.0,
                "known_107_template_support": known_107,
                "known_107_template_support_percent": round(
                    known_107 / len(marker_records) * 100.0, 6
                )
                if marker_records
                else 0.0,
                "projectile_act_file_exists": act_exists,
                "missing_projectile_rows": [
                    {"value": value, "records": count}
                    for value, count in values.most_common()
                    if value not in projectiles
                ][:30],
                "projectile_rows": [
                    {
                        "template_id": value,
                        "records": count,
                        "act_file_id": int(
                            projectiles.get(value, {}).get("act_file_id", 0) or 0
                        ),
                        "act_file": str(
                            projectiles.get(value, {}).get("act_file", "") or ""
                        ),
                        "skill_name_hints_not_protocol_proof": projectiles.get(
                            value, {}
                        ).get("skill_name_hints_not_protocol_proof", []),
                        "skilllevel_name_bridge_not_protocol_proof": projectiles.get(
                            value, {}
                        ).get("skilllevel_name_bridge_not_protocol_proof", []),
                    }
                    for value, count in values.most_common()
                ],
            }
        )

    marker_template_sets = {
        marker: {
            row.marker_plus_12 for row in all_records if row.marker == marker
        }
        for marker in sorted(set(row.marker for row in all_records))
    }
    marker_template_overlap = []
    marker_names = sorted(marker_template_sets)
    for left_index, left in enumerate(marker_names):
        for right in marker_names[left_index + 1 :]:
            intersection = marker_template_sets[left] & marker_template_sets[right]
            marker_template_overlap.append(
                {
                    "left": left,
                    "right": right,
                    "shared_templates": len(intersection),
                    "examples": sorted(intersection)[:40],
                }
            )

    registration_offset_support: Counter[int] = Counter()
    registration_offset_conflicts: Counter[int] = Counter()
    for registration in all_registrations:
        for offset in range(9, len(registration.body) - 3):
            value = _u32(registration.body, offset)
            if value == registration.template_id:
                registration_offset_support[offset] += 1
            elif value in registration_template_ids:
                registration_offset_conflicts[offset] += 1

    registration_keys = {
        (row.capture, row.actor_id, row.object_id) for row in all_registrations
    }
    owner_object_keys = {
        (row.capture, row.owner_id, row.object_id) for row in all_records
    }
    prefix_actor_object_keys = {
        (row.capture, row.prefix_actor_candidate, row.object_id)
        for row in all_records
    }
    hit_keys = {
        (row.capture, row.actor_id, row.object_id) for row in all_hits
    }

    hits_by_key: dict[tuple[str, int, int], list[DamageHit]] = defaultdict(list)
    for hit in all_hits:
        hits_by_key[(hit.capture, hit.actor_id, hit.object_id)].append(hit)
    for values in hits_by_key.values():
        values.sort(key=lambda row: row.timestamp)
    object_record_hit_links = 0
    object_record_hit_links_temporal = 0
    object_hit_links_by_marker: Counter[str] = Counter()
    temporal_hit_links_by_marker: Counter[str] = Counter()
    exact_skill_supported = 0
    exact_skill_conflicts = 0
    exact_skill_unverifiable = 0
    exact_conflict_examples: list[dict[str, Any]] = []
    exact_by_template: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "records_with_exact_anchor": 0,
            "supported_records": 0,
            "conflicting_records": 0,
            "unverifiable_records": 0,
            "exact_skill_ids": Counter(),
        }
    )
    exact_methods = {
        "packet_skill_id",
        "direct_result_group_link",
        "direct_result_group_run_link",
    }
    for record in all_records:
        key = (record.capture, record.owner_id, record.object_id)
        hits = hits_by_key.get(key, ())
        if not hits:
            continue
        object_record_hit_links += 1
        object_hit_links_by_marker[record.marker] += 1
        nearby = [
            hit
            for hit in hits
            if -0.5 <= hit.timestamp - record.timestamp <= 30.0
        ]
        if not nearby:
            continue
        object_record_hit_links_temporal += 1
        temporal_hit_links_by_marker[record.marker] += 1
        projectile = projectiles.get(record.marker_plus_12, {})
        hints = {
            int(value)
            for value in projectile.get(
                "skill_name_hints_not_protocol_proof", ()
            )
        }
        exact = {
            hit.skill_id
            for hit in nearby
            if hit.skill_id > 0
            and hit.attribution_method.strip().casefold() in exact_methods
        }
        template_audit = exact_by_template[record.marker_plus_12]
        if exact:
            template_audit["records_with_exact_anchor"] += 1
            template_audit["exact_skill_ids"].update(exact)
        if not exact or not hints:
            exact_skill_unverifiable += 1
            template_audit["unverifiable_records"] += 1
        elif exact <= hints:
            exact_skill_supported += 1
            template_audit["supported_records"] += 1
        else:
            exact_skill_conflicts += 1
            template_audit["conflicting_records"] += 1
            if len(exact_conflict_examples) < 40:
                exact_conflict_examples.append(
                    {
                        "capture": record.capture,
                        "marker": record.marker,
                        "owner_id": record.owner_id,
                        "object_id": record.object_id,
                        "template_id": record.marker_plus_12,
                        "act_file": projectile.get("act_file", ""),
                        "skill_name_hints_not_protocol_proof": sorted(hints),
                        "exact_direct37_skills": sorted(exact),
                    }
                )

    s2c_structural_fields = []
    for marker in sorted({row.marker for row in all_records}):
        rows = [row for row in all_records if row.marker == marker]
        s2c_structural_fields.append(
            {
                "marker": marker,
                "records": len(rows),
                "marker_minus_4_owner_repeats_at_plus_44": sum(
                    row.owner_id == row.owner_repeat for row in rows
                ),
                "marker_plus_4_u32_zero": sum(
                    _u32(row.body, row.marker_offset + 4) == 0
                    for row in rows
                ),
                "marker_plus_8_owner_object_joins_direct37": (
                    object_hit_links_by_marker[marker]
                ),
                "marker_plus_8_owner_object_joins_future_30s_direct37": (
                    temporal_hit_links_by_marker[marker]
                ),
                "marker_plus_12_projectile_rows": sum(
                    row.marker_plus_12 in projectiles for row in rows
                ),
                "marker_plus_12_distinct": len(
                    {row.marker_plus_12 for row in rows}
                ),
            }
        )

    field_candidates: list[dict[str, Any]] = []
    for (marker, width, relative_offset), values in offset_value_templates.items():
        samples = sum(sum(counter.values()) for counter in values.values())
        if samples < 3:
            continue
        pure_values = sum(1 for counter in values.values() if len(counter) == 1)
        conflicts = sum(
            sum(counter.values())
            for counter in values.values()
            if len(counter) > 1
        )
        direct_equal = sum(
            count
            for value, counter in values.items()
            for template_id, count in counter.items()
            if value == template_id
        )
        field_candidates.append(
            {
                "marker": marker,
                "width_bits": width * 8,
                "relative_offset": relative_offset,
                "samples": samples,
                "distinct_values": len(values),
                "pure_values": pure_values,
                "cross_template_conflict_samples": conflicts,
                "direct_template_equal": direct_equal,
                "direct_template_equal_percent": round(
                    direct_equal / samples * 100.0, 6
                ),
            }
        )
    field_candidates.sort(
        key=lambda row: (
            -float(row["direct_template_equal_percent"]),
            int(row["cross_template_conflict_samples"]),
            -int(row["samples"]),
        )
    )

    serial_tables = {
        key: value
        for key, value in tables.items()
        if key
        not in {
            "projectiles",
            "file_ids",
            "file_act_ids",
            "file_effect_ids",
            "projectile_act_file_ids",
            "skill_ids",
            "skill_level_row_ids",
            "state_effect_ids",
            "state_effect_act_file_ids",
        }
    }
    return {
        "schema_version": 1,
        "method": (
            "Only same capture + exact owner(marker-4 and marker+44) + exact "
            "object(marker+8) registrations are paired. marker-8 is only an "
            "unclassified prefix actor candidate and is never treated as owner. "
            "Nearest unique record inside the window wins."
        ),
        "client_tables": serial_tables,
        "captures": capture_rows,
        "summary": {
            "registrations": len(all_registrations),
            "s2c_object_records": len(all_records),
            "strict_matches": len(matched),
            "ambiguous_nearest_matches_rejected": ambiguous,
            "matched_markers": dict(Counter(record.marker for _, record, _ in matched)),
            "exact_owner_object_key_intersection": len(
                registration_keys & owner_object_keys
            ),
            "prefix_actor_object_collision_keys": len(
                registration_keys & prefix_actor_object_keys
            ),
            "s2c_owner_object_keys_with_direct37": len(
                owner_object_keys & hit_keys
            ),
            "s2c_records_with_same_owner_object_direct37": (
                object_record_hit_links
            ),
            "s2c_records_with_temporally_following_direct37": (
                object_record_hit_links_temporal
            ),
            "s2c_records_with_direct37_by_marker": dict(
                object_hit_links_by_marker
            ),
            "s2c_records_with_temporally_following_direct37_by_marker": dict(
                temporal_hit_links_by_marker
            ),
        },
        "skilllevel_name_bridge_audit_only": {
            "policy": (
                "This comparison is descriptive only. It is excluded from "
                "protocol proof because SkillLevel ProcessParam and ACT names "
                "are strings rather than numeric foreign keys."
            ),
            "exact_skill_supported": exact_skill_supported,
            "exact_skill_conflicts": exact_skill_conflicts,
            "unverifiable_missing_exact_skill_or_name_bridge": (
                exact_skill_unverifiable
            ),
            "conflict_examples": exact_conflict_examples,
            "by_projectile_template": [
                {
                    "template_id": template_id,
                    "act_file": str(
                        projectiles.get(template_id, {}).get("act_file", "")
                        or ""
                    ),
                    "skill_name_hints_not_protocol_proof": projectiles.get(
                        template_id, {}
                    ).get("skill_name_hints_not_protocol_proof", []),
                    "records_with_exact_anchor": int(
                        row["records_with_exact_anchor"]
                    ),
                    "supported_records": int(row["supported_records"]),
                    "conflicting_records": int(row["conflicting_records"]),
                    "unverifiable_records": int(row["unverifiable_records"]),
                    "exact_skill_ids": dict(
                        row["exact_skill_ids"].most_common()
                    ),
                }
                for template_id, row in sorted(
                    exact_by_template.items(),
                    key=lambda item: (
                        -int(item[1]["records_with_exact_anchor"]),
                        item[0],
                    ),
                )
            ],
        },
        "s2c_structural_fields": s2c_structural_fields,
        "marker_plus_12_validation": marker_summary,
        "marker_plus_12_all_records": marker_plus_12_all,
        "marker_template_overlap": marker_template_overlap,
        "registration_template_exact_offsets": [
            {
                "body_offset": offset,
                "exact_template_matches": count,
                "other_observed_template_values": registration_offset_conflicts[
                    offset
                ],
            }
            for offset, count in registration_offset_support.most_common()
        ],
        "marker_field_table_membership": marker_field_membership[:180],
        "label_equal_offsets": {
            label: dict(counter.most_common()) for label, counter in equality.items()
        },
        "ranked_field_candidates": field_candidates[:120],
        "examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate S2C 09 5D/09 61/09 6E object-record fields against "
            "known 107-byte client registration labels and unpacked DNT tables."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--resource-root", type=Path, required=True)
    parser.add_argument("--window-ms", type=int, default=750)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(
        [path.resolve() for path in args.captures],
        args.resource_root.resolve(),
        max(1, int(args.window_ms)),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
