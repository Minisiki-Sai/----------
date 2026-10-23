from __future__ import annotations

import argparse
import csv
import json
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
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


OBJECT_MARKERS = {
    b"\x09\x5d\x00": "09 5D",
    b"\x09\x61\x00": "09 61",
    b"\x09\x6e\x00": "09 6E",
}
ACTION_MARKERS = (
    b"\x0e\x23\x00",
    b"\x0e\x1f\x00",
    b"\x12\x24\x00",
    b"\x12\x20\x00",
)


@dataclass(frozen=True, slots=True)
class RegistrationRow:
    capture: str
    packet_index: int
    timestamp: float
    source_actor_id: int
    damage_object_id: int
    projectile_template_id: int
    effect_resource_id: int
    child_sequence: int
    body: bytes


@dataclass(frozen=True, slots=True)
class ObjectRow:
    capture: str
    packet_index: int
    timestamp: float
    signature: str
    payload_length: int
    xor_phase: int
    marker_name: str
    marker_offset: int
    source_actor_id: int
    record_actor_id: int
    damage_object_id: int
    link_field: int
    header_actor_id: int
    zero_guard: bool
    body: bytes


@dataclass(frozen=True, slots=True)
class ActionRow:
    capture: str
    packet_index: int
    timestamp: float
    direction: str
    signature: str
    payload_length: int
    xor_phase: int
    marker_name: str
    marker_offset: int
    source_actor_id: int
    skill_id: int
    body: bytes


@dataclass(frozen=True, slots=True)
class Direct37Row:
    capture: str
    packet_index: int
    timestamp: float
    source_actor_id: int
    damage_object_id: int
    skill_id: int | None
    skill_id_exact: bool
    record_offset: int
    damage: int


def _iter_offsets(body: bytes, marker: bytes, start: int = 9) -> Iterable[int]:
    offset = body.find(marker, start)
    while offset >= 0:
        yield offset
        offset = body.find(marker, offset + 1)


def _field(body: bytes, marker_offset: int, relative_offset: int, width: int) -> int | None:
    offset = marker_offset + relative_offset
    if offset < 0 or offset + width > len(body):
        return None
    return int.from_bytes(body[offset : offset + width], "little")


def _relative_fields(
    body: bytes,
    marker_offset: int,
    *,
    start: int,
    stop: int,
    width: int,
) -> dict[int, int]:
    fields: dict[int, int] = {}
    for relative_offset in range(start, stop + 1):
        value = _field(body, marker_offset, relative_offset, width)
        if value is not None:
            fields[relative_offset] = value
    return fields


def _load_confirmed_projectile_skills(path: Path) -> dict[int, tuple[int, str]]:
    result: dict[int, tuple[int, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            status = str(row.get("状态", row.get("status", ""))).strip()
            if status != "已确认并启用":
                continue
            try:
                template_id = int(
                    row.get("Projectile模板", row.get("projectile_template_id", ""))
                )
                skill_id = int(
                    row.get("第一候选技能ID", row.get("top_skill_id", ""))
                )
            except (KeyError, TypeError, ValueError):
                continue
            skill_name = str(
                row.get("第一候选技能", row.get("top_skill_name", ""))
            ).strip()
            if template_id > 0 and skill_id > 0:
                result[template_id] = (skill_id, skill_name)
    return result


def collect_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    known_skills = frozenset(int(value) for value in decoder.skill_catalog)
    registrations: list[RegistrationRow] = []
    objects: list[ObjectRow] = []
    actions: list[ActionRow] = []
    direct37_rows: list[Direct37Row] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        direction = str(packet.inferred_direction())
        payload = bytes(packet.payload)
        timestamp = float(packet.timestamp_seconds)
        signature = payload[5:9].hex() if len(payload) >= 9 else ""

        if direction == "server_to_client":
            for decoded_hit in decode_v576_embedded_direct_damage_events(
                payload,
                direction,
                known_skills,
            ):
                object_id = int(decoded_hit.damage_object_id or 0)
                source_actor_id = int(decoded_hit.source_actor_id or 0)
                if object_id <= 0 or source_actor_id <= 0:
                    continue
                direct37_rows.append(
                    Direct37Row(
                        capture=str(path),
                        packet_index=packet_index,
                        timestamp=timestamp,
                        source_actor_id=source_actor_id,
                        damage_object_id=object_id,
                        skill_id=(
                            int(decoded_hit.skill_id)
                            if decoded_hit.skill_id is not None
                            else None
                        ),
                        skill_id_exact=bool(decoded_hit.skill_id_exact),
                        record_offset=int(decoded_hit.record_offset or 0),
                        damage=int(decoded_hit.damage or 0),
                    )
                )

        registration = decode_v576_damage_object_registration(payload, direction)
        if registration is not None:
            body = _decode_v576_body(payload, registration.xor_phase)
            registrations.append(
                RegistrationRow(
                    capture=str(path),
                    packet_index=packet_index,
                    timestamp=timestamp,
                    source_actor_id=registration.source_actor_id,
                    damage_object_id=registration.damage_object_id,
                    projectile_template_id=registration.projectile_template_id,
                    effect_resource_id=registration.effect_resource_id,
                    child_sequence=registration.child_sequence,
                    body=body,
                )
            )

        if direction in {"server_to_client", "client_to_server"}:
            seen_actions: set[tuple[int, int, int, int]] = set()
            seen_objects: set[tuple[int, str, int, int]] = set()
            for phase in _v576_candidate_xor_phases(payload):
                body = _decode_v576_body(payload, phase)
                for marker in ACTION_MARKERS:
                    for offset in _iter_offsets(body, marker):
                        if offset < 4 or offset + 7 > len(body):
                            continue
                        marker_actor_id = int.from_bytes(
                            body[offset - 4 : offset], "little"
                        )
                        header_actor_id = int.from_bytes(body[10:14], "little")
                        skill_id = int.from_bytes(body[offset + 3 : offset + 7], "little")
                        actor_id = (
                            marker_actor_id
                            if direction == "server_to_client"
                            else header_actor_id
                        )
                        if actor_id < 0x01000000 or skill_id not in known_skills:
                            continue
                        key = (phase, offset, actor_id, skill_id)
                        if key in seen_actions:
                            continue
                        seen_actions.add(key)
                        actions.append(
                            ActionRow(
                                capture=str(path),
                                packet_index=packet_index,
                                timestamp=timestamp,
                                direction=direction,
                                signature=signature,
                                payload_length=len(payload),
                                xor_phase=phase,
                                marker_name=marker.hex(" ").upper(),
                                marker_offset=offset,
                                source_actor_id=actor_id,
                                skill_id=skill_id,
                                body=body,
                            )
                        )

                if direction != "server_to_client":
                    continue
                for marker, marker_name in OBJECT_MARKERS.items():
                    for offset in _iter_offsets(body, marker):
                        if offset < 4 or offset + 16 > len(body):
                            continue
                        record_actor_id = int.from_bytes(
                            body[offset - 4 : offset], "little"
                        )
                        actor_id = int.from_bytes(body[10:14], "little")
                        object_id = int.from_bytes(body[offset + 8 : offset + 12], "little")
                        if actor_id < 0x01000000 or not 1 <= object_id <= 0x00FFFFFF:
                            continue
                        key = (phase, marker_name, offset, object_id)
                        if key in seen_objects:
                            continue
                        seen_objects.add(key)
                        objects.append(
                            ObjectRow(
                                capture=str(path),
                                packet_index=packet_index,
                                timestamp=timestamp,
                                signature=signature,
                                payload_length=len(payload),
                                xor_phase=phase,
                                marker_name=marker_name,
                                marker_offset=offset,
                                source_actor_id=actor_id,
                                record_actor_id=record_actor_id,
                                damage_object_id=object_id,
                                link_field=int.from_bytes(
                                    body[offset + 12 : offset + 16], "little"
                                ),
                                header_actor_id=(
                                    int.from_bytes(body[10:14], "little")
                                    if len(body) >= 14
                                    else 0
                                ),
                                zero_guard=body[offset + 3 : offset + 8] == b"\0" * 5,
                                body=body,
                            )
                        )

        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": str(path),
        "known_skills": known_skills,
        "registrations": registrations,
        "objects": objects,
        "actions": actions,
        "direct37": direct37_rows,
    }


def _summarize_lifecycle(objects: list[ObjectRow]) -> dict[str, Any]:
    by_object: dict[tuple[str, int, int], list[ObjectRow]] = defaultdict(list)
    for row in objects:
        by_object[(row.capture, row.source_actor_id, row.damage_object_id)].append(row)
    patterns: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    for rows in by_object.values():
        ordered = sorted(rows, key=lambda row: (row.packet_index, row.marker_offset))
        pattern = " > ".join(row.marker_name for row in ordered)
        patterns[pattern] += 1
        for left, right in zip(ordered, ordered[1:]):
            transitions[f"{left.marker_name} -> {right.marker_name}"] += 1
    return {
        "objects": len(by_object),
        "patterns": dict(patterns.most_common(30)),
        "transitions": dict(transitions.most_common()),
    }


def summarize(
    results: list[dict[str, Any]],
    confirmed_projectiles: dict[int, tuple[int, str]],
) -> dict[str, Any]:
    registrations: list[RegistrationRow] = [
        row for result in results for row in result["registrations"]
    ]
    objects: list[ObjectRow] = [row for result in results for row in result["objects"]]
    actions: list[ActionRow] = [row for result in results for row in result["actions"]]
    direct37_rows: list[Direct37Row] = [
        row for result in results for row in result["direct37"]
    ]

    registration_labels: dict[tuple[str, int, int], set[int]] = defaultdict(set)
    registration_rows: dict[tuple[str, int, int], list[RegistrationRow]] = defaultdict(list)
    for row in registrations:
        key = (row.capture, row.source_actor_id, row.damage_object_id)
        registration_labels[key].add(row.projectile_template_id)
        registration_rows[key].append(row)
    registration_conflicts = {
        key: values for key, values in registration_labels.items() if len(values) != 1
    }

    labelled_objects: list[tuple[ObjectRow, int, int | None, str]] = []
    for row in objects:
        key = (row.capture, row.source_actor_id, row.damage_object_id)
        templates = registration_labels.get(key, set())
        if len(templates) != 1:
            continue
        template_id = next(iter(templates))
        skill = confirmed_projectiles.get(template_id)
        labelled_objects.append(
            (row, template_id, skill[0] if skill else None, skill[1] if skill else "")
        )

    marker_counts = Counter(row.marker_name for row in objects)
    zero_guard_counts = Counter(
        (row.marker_name, row.zero_guard) for row in objects
    )
    labelled_marker_counts = Counter(row.marker_name for row, *_ in labelled_objects)
    labelled_template_counts = Counter(template_id for _, template_id, _, _ in labelled_objects)

    # The structural interpretation under test is:
    #   actor[-4] + 09 xx 00 + zero[5] + object[+8] + skill[+12]
    # direct37 independently repeats source actor at +8 and object at +13.
    # Join the complete IDs across the whole capture with no time condition.
    object_skill_keys: dict[tuple[str, int, int], set[int]] = defaultdict(set)
    object_marker_keys: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    object_record_counts: Counter[tuple[str, int, int]] = Counter()
    for row in objects:
        if not row.zero_guard or not 1 <= row.link_field <= 1_000_000:
            continue
        key = (row.capture, row.record_actor_id, row.damage_object_id)
        object_skill_keys[key].add(row.link_field)
        object_marker_keys[key].add(row.marker_name)
        object_record_counts[key] += 1

    direct_join_support = 0
    direct_join_unmatched = 0
    direct_join_ambiguous = 0
    exact_support = 0
    raw_code_equals_skill = 0
    raw_code_differs_from_skill = 0
    exact_unmatched = 0
    joined_skill_counts: Counter[int] = Counter()
    joined_marker_counts: Counter[str] = Counter()
    per_capture_direct: dict[str, Counter[str]] = defaultdict(Counter)
    conflict_examples: list[dict[str, Any]] = []
    for row in direct37_rows:
        key = (row.capture, row.source_actor_id, row.damage_object_id)
        skills = object_skill_keys.get(key, set())
        stats = per_capture_direct[row.capture]
        stats["direct37"] += 1
        if len(skills) == 1:
            resolved_skill = next(iter(skills))
            direct_join_support += 1
            stats["joined"] += 1
            joined_skill_counts[resolved_skill] += 1
            for marker_name in object_marker_keys[key]:
                joined_marker_counts[marker_name] += 1
            if row.skill_id_exact and row.skill_id is not None:
                exact_support += 1
                stats["exact_joined"] += 1
                if row.skill_id == resolved_skill:
                    raw_code_equals_skill += 1
                    stats["exact_match"] += 1
                else:
                    raw_code_differs_from_skill += 1
                    stats["exact_conflict"] += 1
                    if len(conflict_examples) < 30:
                        conflict_examples.append(
                            {
                                "capture": row.capture,
                                "packet_index": row.packet_index,
                                "source_actor_id": row.source_actor_id,
                                "damage_object_id": row.damage_object_id,
                                "direct37_skill_id": row.skill_id,
                                "object_record_skill_id": resolved_skill,
                                "markers": sorted(object_marker_keys[key]),
                            }
                        )
        elif len(skills) > 1:
            direct_join_ambiguous += 1
            stats["ambiguous"] += 1
        else:
            direct_join_unmatched += 1
            stats["unmatched"] += 1
            if row.skill_id_exact:
                exact_unmatched += 1
                stats["exact_unmatched"] += 1

    code_skill_counts: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
    code_skill_objects: dict[tuple[str, int], set[tuple[str, int, int]]] = defaultdict(set)
    code_skill_captures: dict[tuple[str, int], set[str]] = defaultdict(set)
    code_skill_actors: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)
    for row in direct37_rows:
        if not row.skill_id_exact or row.skill_id is None:
            continue
        object_key = (row.capture, row.source_actor_id, row.damage_object_id)
        codes = object_skill_keys.get(object_key, set())
        if len(codes) != 1:
            continue
        code = next(iter(codes))
        for marker_name in object_marker_keys[object_key]:
            key = (marker_name, code)
            code_skill_counts[key][row.skill_id] += 1
            code_skill_objects[key].add(object_key)
            code_skill_captures[key].add(row.capture)
            code_skill_actors[key].add((row.capture, row.source_actor_id))

    action_code_mappings: list[dict[str, Any]] = []
    for key, skill_counts in code_skill_counts.items():
        marker_name, code = key
        action_code_mappings.append(
            {
                "marker": marker_name,
                "action_code_offset": 12,
                "action_code": code,
                "exact_hit_support": sum(skill_counts.values()),
                "unique_object_support": len(code_skill_objects[key]),
                "captures": len(code_skill_captures[key]),
                "actors": len(code_skill_actors[key]),
                "skills": dict(skill_counts.most_common()),
                "skill_conflict": len(skill_counts) > 1,
            }
        )
    action_code_mappings.sort(
        key=lambda row: (
            row["skill_conflict"],
            -row["captures"],
            -row["actors"],
            -row["unique_object_support"],
            -row["exact_hit_support"],
        )
    )

    unique_code_map = {
        key: next(iter(skill_counts))
        for key, skill_counts in code_skill_counts.items()
        if len(skill_counts) == 1
    }
    mapped_rows = 0
    mapped_exact_rows = 0
    mapped_exact_matches = 0
    mapped_exact_conflicts = 0
    mapping_ambiguous_rows = 0
    for row in direct37_rows:
        object_key = (row.capture, row.source_actor_id, row.damage_object_id)
        codes = object_skill_keys.get(object_key, set())
        if len(codes) != 1:
            continue
        code = next(iter(codes))
        mapped_skills = {
            unique_code_map[(marker_name, code)]
            for marker_name in object_marker_keys[object_key]
            if (marker_name, code) in unique_code_map
        }
        if len(mapped_skills) != 1:
            if mapped_skills:
                mapping_ambiguous_rows += 1
            continue
        mapped_skill = next(iter(mapped_skills))
        mapped_rows += 1
        if row.skill_id_exact and row.skill_id is not None:
            mapped_exact_rows += 1
            if mapped_skill == row.skill_id:
                mapped_exact_matches += 1
            else:
                mapped_exact_conflicts += 1

    cross_capture_validation: list[dict[str, Any]] = []
    capture_names = sorted({result["capture"] for result in results})
    for holdout in capture_names:
        training_counts: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
        for row in direct37_rows:
            if (
                row.capture == holdout
                or not row.skill_id_exact
                or row.skill_id is None
            ):
                continue
            object_key = (row.capture, row.source_actor_id, row.damage_object_id)
            codes = object_skill_keys.get(object_key, set())
            if len(codes) != 1:
                continue
            code = next(iter(codes))
            for marker_name in object_marker_keys[object_key]:
                training_counts[(marker_name, code)][row.skill_id] += 1
        training_map = {
            key: next(iter(counts))
            for key, counts in training_counts.items()
            if len(counts) == 1
        }
        tested = matched = conflicts = unmapped = ambiguous = 0
        tested_objects: set[tuple[str, int, int, int]] = set()
        for row in direct37_rows:
            if (
                row.capture != holdout
                or not row.skill_id_exact
                or row.skill_id is None
            ):
                continue
            object_key = (row.capture, row.source_actor_id, row.damage_object_id)
            dedupe_key = (*object_key, row.skill_id)
            if dedupe_key in tested_objects:
                continue
            tested_objects.add(dedupe_key)
            codes = object_skill_keys.get(object_key, set())
            if len(codes) != 1:
                unmapped += 1
                continue
            code = next(iter(codes))
            predicted = {
                training_map[(marker_name, code)]
                for marker_name in object_marker_keys[object_key]
                if (marker_name, code) in training_map
            }
            if not predicted:
                unmapped += 1
            elif len(predicted) > 1:
                ambiguous += 1
            else:
                tested += 1
                if next(iter(predicted)) == row.skill_id:
                    matched += 1
                else:
                    conflicts += 1
        cross_capture_validation.append(
            {
                "holdout_capture": holdout,
                "tested_unique_objects": tested,
                "matches": matched,
                "conflicts": conflicts,
                "unmapped_unique_objects": unmapped,
                "ambiguous_unique_objects": ambiguous,
            }
        )

    # Sequence-state validation. An explicit 09 record opens or replaces the
    # state for its exact (source actor, full runtime ID). A direct37 row may
    # consume only that exact active key. There is no time-distance threshold
    # and no nearest-neighbour search across different IDs.
    lifecycle_associations: list[tuple[Direct37Row, ObjectRow]] = []
    lifecycle_per_capture: list[dict[str, Any]] = []
    for result in results:
        active: dict[tuple[int, int], ObjectRow] = {}
        events: list[tuple[int, int, int, ObjectRow | Direct37Row]] = []
        for obj in result["objects"]:
            if obj.zero_guard and 1 <= obj.link_field <= 1_000_000:
                events.append((obj.packet_index, obj.marker_offset, 0, obj))
        for direct in result["direct37"]:
            events.append((direct.packet_index, direct.record_offset, 1, direct))
        joined = unmatched = exact_joined = 0
        for _, _, kind, event in sorted(events, key=lambda value: value[:3]):
            if kind == 0:
                assert isinstance(event, ObjectRow)
                active[(event.record_actor_id, event.damage_object_id)] = event
                continue
            assert isinstance(event, Direct37Row)
            origin = active.get((event.source_actor_id, event.damage_object_id))
            if origin is None:
                unmatched += 1
                continue
            joined += 1
            if event.skill_id_exact:
                exact_joined += 1
            lifecycle_associations.append((event, origin))
        lifecycle_per_capture.append(
            {
                "capture": result["capture"],
                "direct37_rows": len(result["direct37"]),
                "joined_rows": joined,
                "unmatched_rows": unmatched,
                "exact_joined_rows": exact_joined,
            }
        )

    lifecycle_code_skill_counts: dict[
        tuple[str, int], Counter[int]
    ] = defaultdict(Counter)
    lifecycle_code_all_rows: Counter[tuple[str, int]] = Counter()
    lifecycle_code_all_instances: dict[
        tuple[str, int], set[tuple[str, int, int, int]]
    ] = defaultdict(set)
    lifecycle_code_objects: dict[
        tuple[str, int], set[tuple[str, int, int, int]]
    ] = defaultdict(set)
    lifecycle_code_captures: dict[tuple[str, int], set[str]] = defaultdict(set)
    lifecycle_code_actors: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)
    for direct, origin in lifecycle_associations:
        code_key = (origin.marker_name, origin.link_field)
        lifecycle_code_all_rows[code_key] += 1
        lifecycle_code_all_instances[code_key].add(
            (
                direct.capture,
                direct.source_actor_id,
                direct.damage_object_id,
                origin.packet_index,
            )
        )
        if not direct.skill_id_exact or direct.skill_id is None:
            continue
        lifecycle_code_skill_counts[code_key][direct.skill_id] += 1
        lifecycle_code_objects[code_key].add(
            (
                direct.capture,
                direct.source_actor_id,
                direct.damage_object_id,
                origin.packet_index,
            )
        )
        lifecycle_code_captures[code_key].add(direct.capture)
        lifecycle_code_actors[code_key].add((direct.capture, direct.source_actor_id))

    lifecycle_mappings: list[dict[str, Any]] = []
    for key, counts in lifecycle_code_skill_counts.items():
        lifecycle_mappings.append(
            {
                "marker": key[0],
                "action_code": key[1],
                "associated_direct37_rows": lifecycle_code_all_rows[key],
                "all_lifecycle_instances": len(lifecycle_code_all_instances[key]),
                "exact_hit_support": sum(counts.values()),
                "lifecycle_instance_support": len(lifecycle_code_objects[key]),
                "captures": len(lifecycle_code_captures[key]),
                "actors": len(lifecycle_code_actors[key]),
                "skills": dict(counts.most_common()),
                "skill_conflict": len(counts) > 1,
            }
        )
    lifecycle_mappings.sort(
        key=lambda row: (
            row["skill_conflict"],
            -row["captures"],
            -row["actors"],
            -row["lifecycle_instance_support"],
            -row["exact_hit_support"],
        )
    )
    lifecycle_unique_map = {
        key: next(iter(counts))
        for key, counts in lifecycle_code_skill_counts.items()
        if len(counts) == 1
    }
    lifecycle_mapped = lifecycle_exact = lifecycle_matches = lifecycle_conflicts = 0
    for direct, origin in lifecycle_associations:
        skill_id = lifecycle_unique_map.get((origin.marker_name, origin.link_field))
        if skill_id is None:
            continue
        lifecycle_mapped += 1
        if direct.skill_id_exact and direct.skill_id is not None:
            lifecycle_exact += 1
            if direct.skill_id == skill_id:
                lifecycle_matches += 1
            else:
                lifecycle_conflicts += 1

    lifecycle_cross_validation: list[dict[str, Any]] = []
    for holdout in capture_names:
        training: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
        for direct, origin in lifecycle_associations:
            if (
                direct.capture != holdout
                and direct.skill_id_exact
                and direct.skill_id is not None
            ):
                training[(origin.marker_name, origin.link_field)][direct.skill_id] += 1
        training_map = {
            key: next(iter(counts))
            for key, counts in training.items()
            if len(counts) == 1
        }
        tested = matches = conflicts = unmapped = 0
        seen_instances: set[tuple[str, int, int, int, int]] = set()
        for direct, origin in lifecycle_associations:
            if (
                direct.capture != holdout
                or not direct.skill_id_exact
                or direct.skill_id is None
            ):
                continue
            instance = (
                direct.capture,
                direct.source_actor_id,
                direct.damage_object_id,
                origin.packet_index,
                direct.skill_id,
            )
            if instance in seen_instances:
                continue
            seen_instances.add(instance)
            predicted = training_map.get((origin.marker_name, origin.link_field))
            if predicted is None:
                unmapped += 1
                continue
            tested += 1
            if predicted == direct.skill_id:
                matches += 1
            else:
                conflicts += 1
        lifecycle_cross_validation.append(
            {
                "holdout_capture": holdout,
                "tested_lifecycle_instances": tested,
                "matches": matches,
                "conflicts": conflicts,
                "unmapped_lifecycle_instances": unmapped,
            }
        )

    lifecycle_link = {
        "join_rule": (
            "Track the latest explicit 09 record for the exact full "
            "(source actor at marker-4, runtime ID at marker+8); direct37 "
            "joins only by equal source actor(+8) and object ID(+13)."
        ),
        "uses_timestamp_distance": False,
        "per_capture": lifecycle_per_capture,
        "associated_direct37_rows": len(lifecycle_associations),
        "mapping_keys": len(lifecycle_code_skill_counts),
        "zero_conflict_mapping_keys": len(lifecycle_unique_map),
        "conflicted_mapping_keys": sum(
            1 for counts in lifecycle_code_skill_counts.values() if len(counts) > 1
        ),
        "mapped_direct37_rows": lifecycle_mapped,
        "mapped_exact_rows": lifecycle_exact,
        "mapped_exact_matches": lifecycle_matches,
        "mapped_exact_conflicts": lifecycle_conflicts,
        "action_code_mappings": lifecycle_mappings,
        "cross_capture_validation": lifecycle_cross_validation,
    }

    source_object_skill_link = {
        "record_layout": {
            "source_actor_relative_offset": -4,
            "marker_relative_offset": 0,
            "zero_guard_relative_range": [3, 8],
            "damage_object_id_relative_offset": 8,
            "action_code_relative_offset": 12,
        },
        "direct37_layout": {
            "source_actor_relative_offset": 8,
            "damage_object_id_relative_offset": 13,
            "exact_skill_relative_offsets": [102, 114],
        },
        "object_keys_with_action_code": len(object_skill_keys),
        "object_key_action_code_conflicts": sum(
            1 for skills in object_skill_keys.values() if len(skills) > 1
        ),
        "direct37_rows": len(direct37_rows),
        "joined_rows": direct_join_support,
        "ambiguous_rows": direct_join_ambiguous,
        "unmatched_rows": direct_join_unmatched,
        "exact_joined_rows": exact_support,
        "raw_code_equals_exact_skill": raw_code_equals_skill,
        "raw_code_differs_from_exact_skill": raw_code_differs_from_skill,
        "exact_unmatched_rows": exact_unmatched,
        "joined_action_code_counts": dict(joined_skill_counts.most_common()),
        "joined_marker_counts": dict(joined_marker_counts.most_common()),
        "per_capture": [
            {"capture": capture, **dict(stats)}
            for capture, stats in per_capture_direct.items()
        ],
        "raw_code_difference_examples": conflict_examples,
        "action_code_mappings": action_code_mappings,
        "action_code_mapping_summary": {
            "mapping_keys": len(code_skill_counts),
            "zero_conflict_keys": len(unique_code_map),
            "conflicted_keys": sum(
                1 for counts in code_skill_counts.values() if len(counts) > 1
            ),
            "mapped_direct37_rows": mapped_rows,
            "mapping_ambiguous_rows": mapping_ambiguous_rows,
            "mapped_exact_rows": mapped_exact_rows,
            "mapped_exact_matches": mapped_exact_matches,
            "mapped_exact_conflicts": mapped_exact_conflicts,
        },
        "cross_capture_validation": cross_capture_validation,
        "sequence_state_link": lifecycle_link,
    }

    link_to_templates: dict[tuple[str, int], set[int]] = defaultdict(set)
    link_to_skills: dict[tuple[str, int], set[int]] = defaultdict(set)
    link_support: Counter[tuple[str, int]] = Counter()
    for row, template_id, skill_id, _ in labelled_objects:
        key = (row.marker_name, row.link_field)
        link_support[key] += 1
        link_to_templates[key].add(template_id)
        if skill_id is not None:
            link_to_skills[key].add(skill_id)

    link_field_rows: list[dict[str, Any]] = []
    for key, support in link_support.most_common():
        marker_name, value = key
        templates = sorted(link_to_templates[key])
        skills = sorted(link_to_skills[key])
        link_field_rows.append(
            {
                "marker": marker_name,
                "offset": 12,
                "value": value,
                "support_records": support,
                "templates": templates,
                "template_conflict": len(templates) > 1,
                "skills": skills,
                "skill_conflict": len(skills) > 1,
            }
        )

    # Look for a stable packet-contained discriminator beyond +12. Dynamic
    # coordinates and counters can appear conflict-free merely because every
    # value is unique, so only values repeated by at least two labelled records
    # contribute to support/conflict counts.
    discriminator_rows: list[dict[str, Any]] = []
    for width in (1, 2, 4):
        for marker_name in OBJECT_MARKERS.values():
            marker_labelled = [
                row for row in labelled_objects if row[0].marker_name == marker_name
            ]
            for relative_offset in range(12, 65):
                value_rows: dict[int, list[tuple[ObjectRow, int, int | None, str]]] = (
                    defaultdict(list)
                )
                for labelled in marker_labelled:
                    value = _field(
                        labelled[0].body,
                        labelled[0].marker_offset,
                        relative_offset,
                        width,
                    )
                    if value is None or value in {0, (1 << (width * 8)) - 1}:
                        continue
                    value_rows[value].append(labelled)
                repeated = {
                    value: rows for value, rows in value_rows.items() if len(rows) >= 2
                }
                if not repeated:
                    continue
                support = 0
                conflicts = 0
                captures: set[str] = set()
                templates: set[int] = set()
                skills: set[int] = set()
                repeated_values = 0
                for rows in repeated.values():
                    row_templates = {row[1] for row in rows}
                    row_skills = {row[2] for row in rows if row[2] is not None}
                    repeated_values += 1
                    captures.update(row[0].capture for row in rows)
                    templates.update(row_templates)
                    skills.update(row_skills)
                    if len(row_skills) > 1 or (
                        not row_skills and len(row_templates) > 1
                    ):
                        conflicts += len(rows)
                    else:
                        support += len(rows)
                discriminator_rows.append(
                    {
                        "marker": marker_name,
                        "width": width,
                        "relative_offset": relative_offset,
                        "repeated_value_count": repeated_values,
                        "support_records": support,
                        "conflict_records": conflicts,
                        "captures": len(captures),
                        "templates": len(templates),
                        "skills": len(skills),
                    }
                )
    discriminator_rows.sort(
        key=lambda row: (
            row["conflict_records"] != 0,
            -row["captures"],
            -row["templates"],
            -row["skills"],
            -row["support_records"],
            -row["repeated_value_count"],
        )
    )

    # Compare record-local integer fields without using timestamp proximity.
    # A candidate is only credited when (capture, actor, value) independently
    # resolves to one action skill. Multiple action instances remain explicitly
    # marked as instance-ambiguous even when their skill is the same.
    field_join_rows: list[dict[str, Any]] = []
    for width in (2, 4):
        action_index: dict[
            tuple[str, str, int, int, int], list[ActionRow]
        ] = defaultdict(list)
        for action in actions:
            for relative_offset, value in _relative_fields(
                action.body,
                action.marker_offset,
                start=-4,
                stop=32,
                width=width,
            ).items():
                action_index[
                    (
                        action.capture,
                        action.direction,
                        action.source_actor_id,
                        relative_offset,
                        value,
                    )
                ].append(action)

        for direction in ("server_to_client", "client_to_server"):
            for action_offset in range(-4, 33):
                for object_offset in range(-4, 25):
                    support = 0
                    unique_instance_support = 0
                    same_skill_multi_instance = 0
                    skill_conflicts = 0
                    unmatched = 0
                    distinct_values: set[int] = set()
                    captures: set[str] = set()
                    templates: Counter[int] = Counter()
                    for obj, template_id, expected_skill_id, _ in labelled_objects:
                        if expected_skill_id is None:
                            continue
                        value = _field(
                            obj.body,
                            obj.marker_offset,
                            object_offset,
                            width,
                        )
                        if value is None or value in {0, (1 << (width * 8)) - 1}:
                            continue
                        matches = action_index.get(
                            (
                                obj.capture,
                                direction,
                                obj.source_actor_id,
                                action_offset,
                                value,
                            ),
                            (),
                        )
                        if not matches:
                            unmatched += 1
                            continue
                        matched_skills = {row.skill_id for row in matches}
                        if matched_skills != {expected_skill_id}:
                            skill_conflicts += 1
                            continue
                        support += 1
                        distinct_values.add(value)
                        captures.add(obj.capture)
                        templates[template_id] += 1
                        if len(matches) == 1:
                            unique_instance_support += 1
                        else:
                            same_skill_multi_instance += 1
                    if support < 3 and skill_conflicts == 0:
                        continue
                    field_join_rows.append(
                        {
                            "width": width,
                            "direction": direction,
                            "action_relative_offset": action_offset,
                            "object_relative_offset": object_offset,
                            "support_records": support,
                            "unique_action_instance_support": unique_instance_support,
                            "same_skill_multi_instance": same_skill_multi_instance,
                            "skill_conflicts": skill_conflicts,
                            "unmatched_labelled_records": unmatched,
                            "distinct_values": len(distinct_values),
                            "captures": len(captures),
                            "templates": dict(templates.most_common()),
                        }
                    )
    field_join_rows.sort(
        key=lambda row: (
            row["skill_conflicts"] != 0,
            -row["captures"],
            -row["unique_action_instance_support"],
            -row["support_records"],
            -row["distinct_values"],
        )
    )

    # Search complete explicit-action packet bodies for the full object ID or
    # the +12 value. This does not pair by time: actor and exact byte equality
    # are the only join conditions. A token shared by multiple action skills is
    # a conflict, and repeated actions of one skill do not prove an instance.
    action_body_token_joins: list[dict[str, Any]] = []
    for token_name, width in (
        ("damage_object_id", 4),
        ("link_field_u32", 4),
        ("link_field_u16", 2),
    ):
        for direction in ("server_to_client", "client_to_server"):
            support = 0
            unique_instance_support = 0
            same_skill_multi_instance = 0
            skill_conflicts = 0
            unmatched = 0
            captures: set[str] = set()
            templates: Counter[int] = Counter()
            for obj, template_id, expected_skill_id, _ in labelled_objects:
                if expected_skill_id is None:
                    continue
                value = (
                    obj.damage_object_id
                    if token_name == "damage_object_id"
                    else obj.link_field & ((1 << (width * 8)) - 1)
                )
                token = value.to_bytes(width, "little")
                matches_by_identity: dict[tuple[int, int], ActionRow] = {}
                for action in actions:
                    if (
                        action.capture != obj.capture
                        or action.direction != direction
                        or action.source_actor_id != obj.source_actor_id
                        or token not in action.body
                    ):
                        continue
                    matches_by_identity[(action.packet_index, action.marker_offset)] = action
                matches = list(matches_by_identity.values())
                if not matches:
                    unmatched += 1
                    continue
                matched_skills = {row.skill_id for row in matches}
                if matched_skills != {expected_skill_id}:
                    skill_conflicts += 1
                    continue
                support += 1
                captures.add(obj.capture)
                templates[template_id] += 1
                if len(matches) == 1:
                    unique_instance_support += 1
                else:
                    same_skill_multi_instance += 1
            action_body_token_joins.append(
                {
                    "token": token_name,
                    "width": width,
                    "direction": direction,
                    "support_records": support,
                    "unique_action_instance_support": unique_instance_support,
                    "same_skill_multi_instance": same_skill_multi_instance,
                    "skill_conflicts": skill_conflicts,
                    "unmatched_labelled_records": unmatched,
                    "captures": len(captures),
                    "templates": dict(templates.most_common()),
                }
            )

    # Equality between the client registration and S2C record helps identify
    # whether +12 is copied from the registration. The join itself is exact
    # actor+object truth, not a temporal neighbour.
    registration_field_joins: list[dict[str, Any]] = []
    for width in (2, 4):
        counters: Counter[tuple[int, int]] = Counter()
        values: dict[tuple[int, int], set[int]] = defaultdict(set)
        captures_by_pair: dict[tuple[int, int], set[str]] = defaultdict(set)
        for obj, _, _, _ in labelled_objects:
            key = (obj.capture, obj.source_actor_id, obj.damage_object_id)
            regs = registration_rows.get(key, ())
            if len(regs) != 1:
                continue
            reg = regs[0]
            object_fields = _relative_fields(
                obj.body, obj.marker_offset, start=-4, stop=24, width=width
            )
            registration_fields = {
                offset: int.from_bytes(reg.body[offset : offset + width], "little")
                for offset in range(9, len(reg.body) - width + 1)
            }
            by_value: dict[int, list[int]] = defaultdict(list)
            for offset, value in registration_fields.items():
                if value not in {0, (1 << (width * 8)) - 1}:
                    by_value[value].append(offset)
            for object_offset, value in object_fields.items():
                if value in {0, (1 << (width * 8)) - 1}:
                    continue
                for registration_offset in by_value.get(value, ()):
                    pair = (registration_offset, object_offset)
                    counters[pair] += 1
                    values[pair].add(value)
                    captures_by_pair[pair].add(obj.capture)
        for (registration_offset, object_offset), support in counters.items():
            if support < 3:
                continue
            registration_field_joins.append(
                {
                    "width": width,
                    "registration_absolute_offset": registration_offset,
                    "object_relative_offset": object_offset,
                    "support_records": support,
                    "distinct_values": len(values[(registration_offset, object_offset)]),
                    "captures": len(captures_by_pair[(registration_offset, object_offset)]),
                }
            )
    registration_field_joins.sort(
        key=lambda row: (
            -row["captures"],
            -row["distinct_values"],
            -row["support_records"],
        )
    )

    per_capture = []
    for result in results:
        capture = result["capture"]
        capture_objects = [row for row in objects if row.capture == capture]
        capture_labels = [row for row in labelled_objects if row[0].capture == capture]
        per_capture.append(
            {
                "capture": capture,
                "registrations": len(result["registrations"]),
                "object_records": len(capture_objects),
                "action_records": len(result["actions"]),
                "direct37_rows": len(result["direct37"]),
                "labelled_object_records": len(capture_labels),
                "labelled_templates": dict(
                    Counter(row[1] for row in capture_labels).most_common()
                ),
                "markers": dict(
                    Counter(row.marker_name for row in capture_objects).most_common()
                ),
            }
        )

    return {
        "captures": len(results),
        "per_capture": per_capture,
        "registrations": len(registrations),
        "registration_identity_keys": len(registration_labels),
        "registration_identity_conflicts": len(registration_conflicts),
        "object_records": len(objects),
        "action_records": len(actions),
        "direct37_rows": len(direct37_rows),
        "marker_counts": dict(marker_counts.most_common()),
        "zero_guard_counts": {
            f"{marker}|{guard}": count
            for (marker, guard), count in zero_guard_counts.most_common()
        },
        "labelled_object_records": len(labelled_objects),
        "labelled_marker_counts": dict(labelled_marker_counts.most_common()),
        "labelled_template_counts": dict(labelled_template_counts.most_common()),
        "rejected_header_actor_registration_hypothesis": {
            "description": (
                "Treating decoded body[10:14] as the 09 record owner creates "
                "cross-actor runtime-ID collisions. These labels are retained "
                "only to quantify and reject that hypothesis."
            ),
            "apparent_matches": len(labelled_objects),
            "projectile_templates": len(labelled_template_counts),
            "action_code_mapping_keys": len(link_field_rows),
            "template_conflicted_mapping_keys": sum(
                1 for row in link_field_rows if row["template_conflict"]
            ),
            "skill_conflicted_mapping_keys": sum(
                1 for row in link_field_rows if row["skill_conflict"]
            ),
        },
        "source_object_skill_link": source_object_skill_link,
        "lifecycle": _summarize_lifecycle(objects),
        "link_field_offset": 12,
        "link_field_mappings": link_field_rows,
        "record_field_discriminators": discriminator_rows[:200],
        "action_object_field_joins": field_join_rows[:200],
        "action_body_token_joins": action_body_token_joins,
        "registration_object_field_joins": registration_field_joins[:100],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit S2C UDP 09 5D/09 61/09 6E object records against exact "
            "client object registrations and explicit action records."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--projectile-csv",
        type=Path,
        default=ROOT
        / "outputs"
        / "skill_attribution_corpus_v5"
        / "projectile_template_candidates.csv",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    confirmed_projectiles = _load_confirmed_projectile_skills(
        args.projectile_csv.resolve()
    )
    results = [collect_capture(path.resolve()) for path in args.captures]
    report = {
        "schema_version": 1,
        "policy": {
            "registration_truth": (
                "Only a capture-local exact (source actor, full damage object ID) "
                "with one Projectile template is used as a label."
            ),
            "determinism": (
                "Timestamp proximity is not used for any support/conflict count. "
                "Action joins require an equal packet-contained integer key; "
                "multiple matching action instances remain ambiguous."
            ),
        },
        "summary": summarize(results, confirmed_projectiles),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
