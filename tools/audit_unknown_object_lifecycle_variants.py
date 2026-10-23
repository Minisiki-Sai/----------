from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.audit_direct_damage_release_gate import _classification  # noqa: E402
from tools.audit_s2c_object_record_table_links import (  # noqa: E402
    load_client_tables,
)


SCHEMA_VERSION = 2
KNOWN_MARKERS = frozenset({"09 5D", "09 61", "09 6E"})
DIRECT37_MARKER = "08 73 00"
GENERIC_MARKER_FAMILIES = frozenset({0x08, 0x09, 0x0E, 0x12})
SAFE_STATIC_PACKET_MIN_HITS = 30
SAFE_STATIC_PACKET_MIN_CAPTURES = 2
SAFE_STATIC_PACKET_MIN_ACTORS = 2


@dataclass(frozen=True, slots=True)
class Direct37Hit:
    packet_index: int
    record_offset: int
    owner_actor_id: int
    damage_object_id: int
    damage: int
    packet_skill_id: int | None

    @property
    def key(self) -> tuple[int, int]:
        return self.owner_actor_id, self.damage_object_id

    @property
    def position(self) -> tuple[int, int]:
        return self.packet_index, self.record_offset


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    packet_index: int
    marker_offset: int
    xor_phase: int
    marker: str
    owner_actor_id: int
    damage_object_id: int
    projectile_template_id: int
    packet_signature: str
    payload_length: int
    context_hex: str

    @property
    def key(self) -> tuple[int, int]:
        return self.owner_actor_id, self.damage_object_id

    @property
    def position(self) -> tuple[int, int]:
        return self.packet_index, self.marker_offset


@dataclass(slots=True)
class GenericLayoutStats:
    occurrences: int = 0
    unique_packets: int = 0
    last_packet_index: int = -1
    pair_bitmap_64: int = 0
    exact_pair_bitmap_64: int = 0


def _actor_id(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _marker_name(body: bytes, offset: int) -> str:
    return body[offset : offset + 2].hex(" ").upper()


def _counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common()}


def _position_role(
    position: tuple[int, int],
    hit_positions: list[tuple[int, int]],
) -> str:
    left = bisect.bisect_left(hit_positions, position)
    right = bisect.bisect_right(hit_positions, position)
    has_prior = left > 0
    has_later = right < len(hit_positions)
    if not has_prior and has_later:
        return "before_first_direct37"
    if has_prior and has_later:
        return "between_direct37_rows"
    if has_prior and not has_later:
        return "after_last_direct37"
    return "same_position_only"


def _collect_direct37(
    path: Path,
) -> tuple[
    list[Direct37Hit],
    Counter[tuple[int, int, int]],
    dict[tuple[int, int, int], Counter[str]],
    dict[tuple[int, int, int], Counter[str]],
    Counter[tuple[int, int]],
]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    damage_skill_ids = frozenset(int(value) for value in decoder.damage_skill_ids)
    hits: list[Direct37Hit] = []
    blocking_events: Counter[tuple[int, int, int]] = Counter()
    blocking_methods: dict[tuple[int, int, int], Counter[str]] = defaultdict(Counter)
    blocking_labels: dict[tuple[int, int, int], Counter[str]] = defaultdict(Counter)
    all_direct_event_keys: Counter[tuple[int, int]] = Counter()
    seen_hits: set[tuple[int, int, int, int, int]] = set()
    packet_index = 0

    def feed_events(packet: object) -> Iterable[object]:
        nonlocal packet_index
        packet_index += 1
        payload = bytes(getattr(packet, "payload", b""))
        direction = packet.inferred_direction()
        if direction == "server_to_client":
            for row in decode_v576_embedded_direct_damage_events(
                payload,
                direction,
                damage_skill_ids,
            ):
                owner = int(row.source_actor_id or 0)
                object_id = int(row.damage_object_id or 0)
                damage = int(row.damage or 0)
                record_offset = int(row.record_offset or 0)
                identity = (
                    packet_index,
                    record_offset,
                    owner,
                    object_id,
                    damage,
                )
                if owner <= 0 or object_id <= 0 or identity in seen_hits:
                    continue
                seen_hits.add(identity)
                hits.append(
                    Direct37Hit(
                        packet_index=packet_index,
                        record_offset=record_offset,
                        owner_actor_id=owner,
                        damage_object_id=object_id,
                        damage=damage,
                        packet_skill_id=(
                            int(row.skill_id)
                            if row.skill_id_exact and row.skill_id is not None
                            else None
                        ),
                    )
                )

        emitted = original_feed_events(packet)
        for event in emitted:
            if not isinstance(event, CombatEvent) or event.healing:
                continue
            object_id = int(event.wire_damage_object_id or 0)
            owner = _actor_id(event.source_id)
            if owner <= 0 or object_id <= 0:
                continue
            all_direct_event_keys[(owner, object_id)] += 1
            if _classification(event) != "blocking_unproven_direct_hit":
                continue
            event_key = (packet_index, owner, object_id)
            blocking_events[event_key] += 1
            blocking_methods[event_key][
                str(event.attribution_method or "unresolved").strip().casefold()
            ] += 1
            blocking_labels[event_key][
                " | ".join(
                    (
                        str(event.job or "未知职业"),
                        str(event.skill_id or "-1"),
                        str(event.skill_name or "待识别伤害"),
                    )
                )
            ] += 1
        # Every field needed by this audit has already been reduced above.
        # Do not let the replay source retain a second copy of the full event
        # corpus while the direct37 truth rows are also resident.
        return ()

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return (
        hits,
        blocking_events,
        blocking_methods,
        blocking_labels,
        all_direct_event_keys,
    )


def _scan_s2c_records(
    path: Path,
    direct_hits: list[Direct37Hit],
    projectile_ids: frozenset[int],
    *,
    scan_generic_layouts: bool = True,
) -> tuple[
    list[ObjectRecord],
    Counter[str],
    list[dict[str, Any]],
]:
    direct_keys = {row.key for row in direct_hits}
    owner_to_objects: dict[int, set[int]] = defaultdict(set)
    exact_keys: set[tuple[int, int]] = set()
    for row in direct_hits:
        owner_to_objects[row.owner_actor_id].add(row.damage_object_id)
        if row.packet_skill_id is not None:
            exact_keys.add(row.key)
    owners = frozenset(owner_to_objects)

    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    records: list[ObjectRecord] = []
    guard_rejections: Counter[str] = Counter()
    generic: dict[tuple[str, int, int], GenericLayoutStats] = {}
    packet_index = 0

    def feed_events(packet: object) -> Iterable[object]:
        nonlocal packet_index
        packet_index += 1
        payload = bytes(getattr(packet, "payload", b""))
        if packet.inferred_direction() != "server_to_client":
            return ()

        signature = payload[5:9].hex() if len(payload) >= 9 else ""
        seen_records: set[tuple[int, str, int, int, int]] = set()
        seen_generic: set[
            tuple[str, int, int, int, int, int, int, int]
        ] = set()
        for phase in _v576_candidate_xor_phases(payload):
            body = _decode_v576_body(payload, phase)

            marker_offset = body.find(b"\x09", 9)
            while marker_offset >= 0:
                if marker_offset + 12 <= len(body) and body[marker_offset + 2] == 0:
                    owner = int.from_bytes(
                        body[marker_offset - 4 : marker_offset], "little"
                    ) if marker_offset >= 4 else 0
                    object_id = int.from_bytes(
                        body[marker_offset + 8 : marker_offset + 12], "little"
                    )
                    pair = (owner, object_id)
                    if pair in direct_keys:
                        marker = _marker_name(body, marker_offset)
                        reasons: list[str] = []
                        if marker_offset + 48 > len(body):
                            reasons.append("truncated_before_owner_repeat")
                        else:
                            if body[marker_offset + 3 : marker_offset + 8] != b"\0" * 5:
                                reasons.append("zero_guard_mismatch")
                            repeated_owner = int.from_bytes(
                                body[marker_offset + 44 : marker_offset + 48],
                                "little",
                            )
                            if repeated_owner != owner:
                                reasons.append("owner_repeat_mismatch")
                            template_id = int.from_bytes(
                                body[marker_offset + 12 : marker_offset + 16],
                                "little",
                            )
                            if template_id not in projectile_ids:
                                reasons.append("projectile_template_miss")
                        if reasons:
                            for reason in reasons:
                                guard_rejections[f"{marker}|{reason}"] += 1
                        else:
                            template_id = int.from_bytes(
                                body[marker_offset + 12 : marker_offset + 16],
                                "little",
                            )
                            identity = (
                                marker_offset,
                                marker,
                                owner,
                                object_id,
                                template_id,
                            )
                            if identity not in seen_records:
                                seen_records.add(identity)
                                context_start = max(0, marker_offset - 8)
                                context_stop = min(len(body), marker_offset + 56)
                                records.append(
                                    ObjectRecord(
                                        packet_index=packet_index,
                                        marker_offset=marker_offset,
                                        xor_phase=int(phase),
                                        marker=marker,
                                        owner_actor_id=owner,
                                        damage_object_id=object_id,
                                        projectile_template_id=template_id,
                                        packet_signature=signature,
                                        payload_length=len(payload),
                                        context_hex=body[
                                            context_start:context_stop
                                        ].hex(" "),
                                    )
                                )
                marker_offset = body.find(b"\x09", marker_offset + 1)

            if not scan_generic_layouts:
                continue

            # Reverse pair scan. An object value is considered only inside the
            # object set belonging to the owner just read from this same body.
            # A bare object-ID lookup is never performed.
            owner_positions: list[tuple[int, int]] = []
            for offset in range(0, max(0, len(body) - 3)):
                value = int.from_bytes(body[offset : offset + 4], "little")
                if value in owners:
                    owner_positions.append((offset, value))
            for owner_offset, owner in owner_positions:
                owned_objects = owner_to_objects[owner]
                object_start = max(0, owner_offset - 16)
                object_stop = min(len(body) - 3, owner_offset + 65)
                for object_offset in range(object_start, object_stop):
                    object_id = int.from_bytes(
                        body[object_offset : object_offset + 4], "little"
                    )
                    if object_id not in owned_objects:
                        continue
                    marker_start = max(9, min(owner_offset, object_offset) - 8)
                    marker_stop = min(
                        len(body) - 2,
                        max(owner_offset, object_offset) + 9,
                    )
                    for candidate_offset in range(marker_start, marker_stop):
                        if (
                            body[candidate_offset] not in GENERIC_MARKER_FAMILIES
                            or body[candidate_offset + 2] != 0
                        ):
                            continue
                        marker = body[
                            candidate_offset : candidate_offset + 3
                        ].hex(" ").upper()
                        owner_relative = owner_offset - candidate_offset
                        object_relative = object_offset - candidate_offset
                        identity = (
                            marker,
                            owner_relative,
                            object_relative,
                            candidate_offset,
                            owner_offset,
                            object_offset,
                            owner,
                            object_id,
                        )
                        if identity in seen_generic:
                            continue
                        seen_generic.add(identity)
                        layout = (marker, owner_relative, object_relative)
                        stats = generic.setdefault(layout, GenericLayoutStats())
                        stats.occurrences += 1
                        packed_pair = (owner << 32) | object_id
                        bucket = (
                            (packed_pair * 0x9E3779B185EBCA87) & 0xFFFFFFFFFFFFFFFF
                        ) >> 58
                        stats.pair_bitmap_64 |= 1 << bucket
                        if stats.last_packet_index != packet_index:
                            stats.unique_packets += 1
                            stats.last_packet_index = packet_index
                        if (owner, object_id) in exact_keys:
                            stats.exact_pair_bitmap_64 |= 1 << bucket

        # Raw UDP object records do not depend on stateful event decoding.
        # Returning no events here prevents the replay source from retaining a
        # second full CombatEvent corpus during this packet-body-only pass.
        return ()

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    generic_rows: list[dict[str, Any]] = []
    for (marker, owner_relative, object_relative), stats in generic.items():
        if stats.occurrences < 2:
            continue
        generic_rows.append(
            {
                "marker": marker,
                "owner_relative_offset": owner_relative,
                "object_relative_offset": object_relative,
                "occurrences": stats.occurrences,
                "owner_object_pair_hash_buckets_64": stats.pair_bitmap_64.bit_count(),
                "unique_packets": stats.unique_packets,
                "packet_skill_pair_hash_buckets_64": (
                    stats.exact_pair_bitmap_64.bit_count()
                ),
                "is_direct37_control": (
                    marker == DIRECT37_MARKER
                    and owner_relative == 8
                    and object_relative == 13
                ),
                "is_strict_09_layout": (
                    marker.startswith("09 ")
                    and owner_relative == -4
                    and object_relative == 8
                ),
            }
        )
    generic_rows.sort(
        key=lambda row: (
            -int(row["occurrences"]),
            -int(row["unique_packets"]),
            str(row["marker"]),
            int(row["owner_relative_offset"]),
            int(row["object_relative_offset"]),
        )
    )
    return records, guard_rejections, generic_rows


def _summarize_capture(
    path: Path,
    projectile_ids: frozenset[int],
    *,
    scan_generic_layouts: bool = True,
) -> dict[str, Any]:
    (
        direct_hits,
        blocking_events,
        blocking_methods,
        blocking_labels,
        all_direct_event_keys,
    ) = _collect_direct37(path)
    records, guard_rejections, generic_rows = _scan_s2c_records(
        path,
        direct_hits,
        projectile_ids,
        scan_generic_layouts=scan_generic_layouts,
    )

    hits_by_key: dict[tuple[int, int], list[Direct37Hit]] = defaultdict(list)
    records_by_key: dict[tuple[int, int], list[ObjectRecord]] = defaultdict(list)
    for hit in direct_hits:
        hits_by_key[hit.key].append(hit)
    for record in records:
        records_by_key[record.key].append(record)
    for rows in hits_by_key.values():
        rows.sort(key=lambda row: row.position)
    for rows in records_by_key.values():
        rows.sort(key=lambda row: row.position)

    marker_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "records": 0,
            "pairs": set(),
            "owners": set(),
            "templates": Counter(),
            "roles": Counter(),
            "active_direct37_rows": 0,
            "active_packet_skill_rows": 0,
            "gain_rows_without_prior_known_marker": 0,
            "gain_packet_skill_rows_without_prior_known_marker": 0,
            "active_blocking_events": 0,
            "active_blocking_methods": Counter(),
        }
    )
    transitions: Counter[str] = Counter()
    candidate_instances: list[dict[str, Any]] = []
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for key, key_records in records_by_key.items():
        key_hits = hits_by_key[key]
        hit_positions = [row.position for row in key_hits]
        for left, right in zip(key_records, key_records[1:]):
            transitions[f"{left.marker} -> {right.marker}"] += 1
        for record in key_records:
            row = marker_data[record.marker]
            row["records"] += 1
            row["pairs"].add(key)
            row["owners"].add(record.owner_actor_id)
            row["templates"][record.projectile_template_id] += 1
            role = _position_role(record.position, hit_positions)
            row["roles"][role] += 1
            if len(examples[record.marker]) < 8:
                examples[record.marker].append(
                    {
                        "packet_index": record.packet_index,
                        "marker_offset": record.marker_offset,
                        "xor_phase": record.xor_phase,
                        "packet_signature": record.packet_signature,
                        "payload_length": record.payload_length,
                        "owner_actor_id": f"0x{record.owner_actor_id:08X}",
                        "damage_object_id": record.damage_object_id,
                        "projectile_template_id": record.projectile_template_id,
                        "sequence_role": role,
                        "context_hex_from_marker_minus_8": record.context_hex,
                    }
                )

        record_positions = [row.position for row in key_records]
        known_records = [row for row in key_records if row.marker in KNOWN_MARKERS]
        known_positions = [row.position for row in known_records]
        instance_hits: dict[int, list[Direct37Hit]] = defaultdict(list)
        for hit in key_hits:
            index = bisect.bisect_right(record_positions, hit.position) - 1
            if index < 0:
                continue
            active = key_records[index]
            row = marker_data[active.marker]
            row["active_direct37_rows"] += 1
            if hit.packet_skill_id is not None:
                row["active_packet_skill_rows"] += 1
            known_index = bisect.bisect_right(known_positions, hit.position) - 1
            if known_index < 0 and active.marker not in KNOWN_MARKERS:
                row["gain_rows_without_prior_known_marker"] += 1
                if hit.packet_skill_id is not None:
                    row["gain_packet_skill_rows_without_prior_known_marker"] += 1
            instance_hits[index].append(hit)

        for index, active in enumerate(key_records):
            if active.marker in KNOWN_MARKERS:
                continue
            exact_skills = Counter(
                int(hit.packet_skill_id)
                for hit in instance_hits.get(index, ())
                if hit.packet_skill_id is not None
            )
            candidate_instances.append(
                {
                    "marker": active.marker,
                    "projectile_template_id": active.projectile_template_id,
                    "owner_actor_id": active.owner_actor_id,
                    "damage_object_id": active.damage_object_id,
                    "packet_index": active.packet_index,
                    "marker_offset": active.marker_offset,
                    "direct37_rows": len(instance_hits.get(index, ())),
                    "packet_skill_rows": sum(exact_skills.values()),
                    "packet_skill_ids": _counter_dict(exact_skills),
                    "packet_skill_conflict": len(exact_skills) > 1,
                }
            )

    # Attach current release-gate blockers only when every raw direct37 row
    # for the same packet-contained owner+object pair has the same active
    # marker. This rejects ambiguous compound packets without using a result
    # group, timestamp, damage value, or neighbouring action.
    active_by_packet_pair: dict[
        tuple[int, int, int], set[tuple[str, int]]
    ] = defaultdict(set)
    no_prior_known_by_packet_pair: dict[tuple[int, int, int], set[bool]] = defaultdict(set)
    for key, key_hits in hits_by_key.items():
        key_records = records_by_key.get(key, ())
        record_positions = [row.position for row in key_records]
        known_positions = [
            row.position for row in key_records if row.marker in KNOWN_MARKERS
        ]
        for hit in key_hits:
            index = bisect.bisect_right(record_positions, hit.position) - 1
            active_identity = (
                (
                    key_records[index].marker,
                    int(key_records[index].projectile_template_id),
                )
                if index >= 0
                else ("<none>", 0)
            )
            packet_key = (hit.packet_index, key[0], key[1])
            active_by_packet_pair[packet_key].add(active_identity)
            no_prior_known_by_packet_pair[packet_key].add(
                bisect.bisect_right(known_positions, hit.position) == 0
            )

    blocking_ambiguous = 0
    blocking_unmatched = 0
    blocking_candidate_covered = 0
    blocking_candidate_gain = 0
    blocking_by_mapping: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    for event_key, count in blocking_events.items():
        markers = active_by_packet_pair.get(event_key, set())
        if not markers:
            blocking_unmatched += count
            continue
        if len(markers) != 1:
            blocking_ambiguous += count
            continue
        marker, template_id = next(iter(markers))
        if marker in KNOWN_MARKERS or marker == "<none>":
            continue
        row = marker_data[marker]
        row["active_blocking_events"] += count
        row["active_blocking_methods"].update(blocking_methods[event_key])
        blocking_candidate_covered += count
        mapping_blockers = blocking_by_mapping[(marker, template_id)]
        mapping_blockers["active_blocking_events"] += count
        mapping_blockers.update(
            {
                f"method:{method}": int(method_count)
                for method, method_count in blocking_methods[event_key].items()
            }
        )
        mapping_blockers.update(
            {
                f"label:{label}": int(label_count)
                for label, label_count in blocking_labels[event_key].items()
            }
        )
        prior_known = no_prior_known_by_packet_pair.get(event_key, set())
        if prior_known == {True}:
            blocking_candidate_gain += count
            mapping_blockers["gain_blocking_events"] += count

    total_direct = len(direct_hits)
    exact_direct = sum(row.packet_skill_id is not None for row in direct_hits)
    known_only_covered = 0
    strict_active_known = 0
    strict_active_candidate = 0
    strict_uncovered = 0
    candidate_gain = 0
    candidate_gain_exact = 0
    for key, key_hits in hits_by_key.items():
        key_records = records_by_key.get(key, ())
        record_positions = [row.position for row in key_records]
        known_positions = [
            row.position for row in key_records if row.marker in KNOWN_MARKERS
        ]
        for hit in key_hits:
            known_index = bisect.bisect_right(known_positions, hit.position) - 1
            if known_index >= 0:
                known_only_covered += 1
            active_index = bisect.bisect_right(record_positions, hit.position) - 1
            if active_index < 0:
                strict_uncovered += 1
                continue
            active = key_records[active_index]
            if active.marker in KNOWN_MARKERS:
                strict_active_known += 1
            else:
                strict_active_candidate += 1
                if known_index < 0:
                    candidate_gain += 1
                    if hit.packet_skill_id is not None:
                        candidate_gain_exact += 1

    mapping: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {
            "skills": Counter(),
            "instances": 0,
            "exact_instances": 0,
            "instance_conflicts": 0,
            "actors": set(),
            "pairs": set(),
        }
    )
    for instance in candidate_instances:
        key = (str(instance["marker"]), int(instance["projectile_template_id"]))
        row = mapping[key]
        row["instances"] += 1
        row["actors"].add(int(instance["owner_actor_id"]))
        row["pairs"].add(
            (int(instance["owner_actor_id"]), int(instance["damage_object_id"]))
        )
        skills = Counter(
            {int(skill): int(count) for skill, count in instance["packet_skill_ids"].items()}
        )
        row["skills"].update(skills)
        if skills:
            row["exact_instances"] += 1
        if len(skills) > 1:
            row["instance_conflicts"] += 1

    marker_rows: list[dict[str, Any]] = []
    for marker, row in marker_data.items():
        marker_rows.append(
            {
                "marker": marker,
                "known_family": marker in KNOWN_MARKERS,
                "records": int(row["records"]),
                "unique_owner_object_pairs": len(row["pairs"]),
                "unique_owners": len(row["owners"]),
                "projectile_templates": len(row["templates"]),
                "top_projectile_templates": _counter_dict(row["templates"]),
                "sequence_roles": _counter_dict(row["roles"]),
                "active_direct37_rows": int(row["active_direct37_rows"]),
                "active_packet_skill_rows": int(row["active_packet_skill_rows"]),
                "gain_rows_without_prior_known_marker": int(
                    row["gain_rows_without_prior_known_marker"]
                ),
                "gain_packet_skill_rows_without_prior_known_marker": int(
                    row["gain_packet_skill_rows_without_prior_known_marker"]
                ),
                "active_blocking_events": int(row["active_blocking_events"]),
                "active_blocking_methods": _counter_dict(
                    row["active_blocking_methods"]
                ),
            }
        )
    marker_rows.sort(key=lambda row: (-int(row["records"]), str(row["marker"])))

    mapping_rows: list[dict[str, Any]] = []
    for (marker, template_id), row in mapping.items():
        skills = row["skills"]
        mapping_rows.append(
            {
                "marker": marker,
                "projectile_template_id": template_id,
                "lifecycle_instances": int(row["instances"]),
                "instances_with_packet_skill_id": int(row["exact_instances"]),
                "packet_skill_rows": sum(skills.values()),
                "packet_skill_ids": _counter_dict(skills),
                "mapping_conflict": len(skills) > 1,
                "instance_conflicts": int(row["instance_conflicts"]),
                "unique_actors": len(row["actors"]),
                "unique_owner_object_pairs": len(row["pairs"]),
                "active_blocking_events": int(
                    blocking_by_mapping[(marker, template_id)][
                        "active_blocking_events"
                    ]
                ),
                "gain_blocking_events": int(
                    blocking_by_mapping[(marker, template_id)][
                        "gain_blocking_events"
                    ]
                ),
                "active_blocking_methods": {
                    key.removeprefix("method:"): int(value)
                    for key, value in blocking_by_mapping[
                        (marker, template_id)
                    ].items()
                    if key.startswith("method:")
                },
                "current_blocking_labels_not_truth": {
                    key.removeprefix("label:"): int(value)
                    for key, value in blocking_by_mapping[
                        (marker, template_id)
                    ].items()
                    if key.startswith("label:")
                },
            }
        )
    mapping_rows.sort(
        key=lambda row: (
            bool(row["mapping_conflict"]),
            -int(row["packet_skill_rows"]),
            str(row["marker"]),
            int(row["projectile_template_id"]),
        )
    )

    current_blocking_events = sum(blocking_events.values())
    current_blocking_pairs = {
        (owner, object_id) for _, owner, object_id in blocking_events
    }
    return {
        "capture": str(path),
        "summary": {
            "direct37_rows": total_direct,
            "direct37_owner_object_pairs": len(hits_by_key),
            "packet_skill_id_rows": exact_direct,
            "strict_09_records": len(records),
            "strict_09_owner_object_pairs": len(records_by_key),
            "known_only_covered_rows": known_only_covered,
            "known_only_uncovered_rows": total_direct - known_only_covered,
            "latest_strict_record_known_rows": strict_active_known,
            "latest_strict_record_candidate_rows": strict_active_candidate,
            "no_prior_strict_record_rows": strict_uncovered,
            "candidate_gain_rows_without_prior_known_marker": candidate_gain,
            "candidate_gain_packet_skill_rows": candidate_gain_exact,
            "current_blocking_events_with_exact_pair": current_blocking_events,
            "current_blocking_owner_object_pairs": len(current_blocking_pairs),
            "candidate_active_blocking_events": blocking_candidate_covered,
            "candidate_gain_blocking_events": blocking_candidate_gain,
            "blocking_event_pair_ambiguities": blocking_ambiguous,
            "blocking_event_pair_unmatched": blocking_unmatched,
            "decoded_direct_events_with_exact_pair": sum(
                all_direct_event_keys.values()
            ),
        },
        "marker_rows": marker_rows,
        "candidate_mapping_rows": mapping_rows,
        "candidate_exact_instances": [
            row for row in candidate_instances if int(row["packet_skill_rows"]) > 0
        ],
        "transitions": _counter_dict(transitions),
        "guard_rejections": _counter_dict(guard_rejections),
        "examples": dict(examples),
        "generic_pair_layouts_internal": generic_rows,
    }


def _worker(
    capture: str,
    projectile_ids: frozenset[int],
    scan_generic_layouts: bool,
) -> dict[str, Any]:
    return _summarize_capture(
        Path(capture),
        projectile_ids,
        scan_generic_layouts=scan_generic_layouts,
    )


def _aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    marker_totals: dict[str, Counter[str]] = defaultdict(Counter)
    marker_templates: dict[str, Counter[str]] = defaultdict(Counter)
    marker_roles: dict[str, Counter[str]] = defaultdict(Counter)
    marker_methods: dict[str, Counter[str]] = defaultdict(Counter)
    marker_captures: dict[str, set[str]] = defaultdict(set)
    transitions: Counter[str] = Counter()
    guard_rejections: Counter[str] = Counter()

    mapping_counts: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    mapping_meta: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    mapping_methods: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    mapping_labels: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    mapping_captures: dict[tuple[str, int], set[str]] = defaultdict(set)
    generic_counts: dict[tuple[str, int, int], Counter[str]] = defaultdict(Counter)
    generic_captures: dict[tuple[str, int, int], set[str]] = defaultdict(set)

    summary_fields = {
        "direct37_rows",
        "direct37_owner_object_pairs",
        "packet_skill_id_rows",
        "strict_09_records",
        "strict_09_owner_object_pairs",
        "known_only_covered_rows",
        "known_only_uncovered_rows",
        "latest_strict_record_known_rows",
        "latest_strict_record_candidate_rows",
        "no_prior_strict_record_rows",
        "candidate_gain_rows_without_prior_known_marker",
        "candidate_gain_packet_skill_rows",
        "current_blocking_events_with_exact_pair",
        "current_blocking_owner_object_pairs",
        "candidate_active_blocking_events",
        "candidate_gain_blocking_events",
        "blocking_event_pair_ambiguities",
        "blocking_event_pair_unmatched",
        "decoded_direct_events_with_exact_pair",
    }

    for report in reports:
        capture = str(report["capture"])
        for field in summary_fields:
            totals[field] += int(report["summary"].get(field, 0))
        transitions.update(
            {str(key): int(value) for key, value in report["transitions"].items()}
        )
        guard_rejections.update(
            {
                str(key): int(value)
                for key, value in report["guard_rejections"].items()
            }
        )
        for row in report["marker_rows"]:
            marker = str(row["marker"])
            marker_captures[marker].add(capture)
            for field in (
                "records",
                "unique_owner_object_pairs",
                "unique_owners",
                "active_direct37_rows",
                "active_packet_skill_rows",
                "gain_rows_without_prior_known_marker",
                "gain_packet_skill_rows_without_prior_known_marker",
                "active_blocking_events",
            ):
                marker_totals[marker][field] += int(row[field])
            marker_templates[marker].update(
                {
                    str(key): int(value)
                    for key, value in row["top_projectile_templates"].items()
                }
            )
            marker_roles[marker].update(
                {
                    str(key): int(value)
                    for key, value in row["sequence_roles"].items()
                }
            )
            marker_methods[marker].update(
                {
                    str(key): int(value)
                    for key, value in row["active_blocking_methods"].items()
                }
            )
        for row in report["candidate_mapping_rows"]:
            key = (str(row["marker"]), int(row["projectile_template_id"]))
            mapping_captures[key].add(capture)
            mapping_counts[key].update(
                {
                    str(skill): int(count)
                    for skill, count in row["packet_skill_ids"].items()
                }
            )
            for field in (
                "lifecycle_instances",
                "instances_with_packet_skill_id",
                "packet_skill_rows",
                "instance_conflicts",
                "unique_actors",
                "unique_owner_object_pairs",
                "active_blocking_events",
                "gain_blocking_events",
            ):
                mapping_meta[key][field] += int(row[field])
            mapping_methods[key].update(
                {
                    str(method): int(count)
                    for method, count in row.get(
                        "active_blocking_methods", {}
                    ).items()
                }
            )
            mapping_labels[key].update(
                {
                    str(label): int(count)
                    for label, count in row.get(
                        "current_blocking_labels_not_truth", {}
                    ).items()
                }
            )
        for row in report["generic_pair_layouts_internal"]:
            key = (
                str(row["marker"]),
                int(row["owner_relative_offset"]),
                int(row["object_relative_offset"]),
            )
            generic_captures[key].add(capture)
            for field in (
                "occurrences",
                "owner_object_pair_hash_buckets_64",
                "unique_packets",
                "packet_skill_pair_hash_buckets_64",
            ):
                generic_counts[key][field] += int(row[field])

    marker_rows: list[dict[str, Any]] = []
    for marker, counts in marker_totals.items():
        marker_rows.append(
            {
                "marker": marker,
                "known_family": marker in KNOWN_MARKERS,
                "captures": len(marker_captures[marker]),
                **{key: int(value) for key, value in counts.items()},
                "projectile_templates": len(marker_templates[marker]),
                "top_projectile_templates": _counter_dict(
                    marker_templates[marker]
                ),
                "sequence_roles": _counter_dict(marker_roles[marker]),
                "active_blocking_methods": _counter_dict(marker_methods[marker]),
            }
        )
    marker_rows.sort(key=lambda row: (-int(row["records"]), str(row["marker"])))

    mapping_rows: list[dict[str, Any]] = []
    for key, skills in mapping_counts.items():
        marker, template_id = key
        meta = mapping_meta[key]
        mapping_rows.append(
            {
                "marker": marker,
                "projectile_template_id": template_id,
                "captures": len(mapping_captures[key]),
                **{field: int(value) for field, value in meta.items()},
                "packet_skill_ids": _counter_dict(skills),
                "mapping_conflict": len(skills) > 1,
                "active_blocking_methods": _counter_dict(mapping_methods[key]),
                "current_blocking_labels_not_truth": _counter_dict(
                    mapping_labels[key]
                ),
            }
        )
    mapping_rows.sort(
        key=lambda row: (
            bool(row["mapping_conflict"]),
            -int(row["captures"]),
            -int(row["packet_skill_rows"]),
            str(row["marker"]),
            int(row["projectile_template_id"]),
        )
    )

    cross_validation: list[dict[str, Any]] = []
    for holdout in reports:
        holdout_capture = str(holdout["capture"])
        training: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
        for report in reports:
            if report is holdout:
                continue
            for row in report["candidate_mapping_rows"]:
                key = (
                    str(row["marker"]),
                    int(row["projectile_template_id"]),
                )
                training[key].update(
                    {
                        str(skill): int(count)
                        for skill, count in row["packet_skill_ids"].items()
                    }
                )
        training_map = {
            key: next(iter(skills))
            for key, skills in training.items()
            if len(skills) == 1
        }
        tested = matches = conflicts = unmapped = instance_conflicts = 0
        for instance in holdout["candidate_exact_instances"]:
            actual = Counter(
                {
                    str(skill): int(count)
                    for skill, count in instance["packet_skill_ids"].items()
                }
            )
            if len(actual) != 1:
                instance_conflicts += 1
                continue
            key = (
                str(instance["marker"]),
                int(instance["projectile_template_id"]),
            )
            predicted = training_map.get(key)
            if predicted is None:
                unmapped += 1
                continue
            tested += 1
            if predicted == next(iter(actual)):
                matches += 1
            else:
                conflicts += 1
        cross_validation.append(
            {
                "holdout_capture": holdout_capture,
                "tested_lifecycle_instances": tested,
                "matches": matches,
                "conflicts": conflicts,
                "unmapped_lifecycle_instances": unmapped,
                "internally_conflicted_instances": instance_conflicts,
            }
        )

    generic_rows: list[dict[str, Any]] = []
    for key, counts in generic_counts.items():
        marker, owner_relative, object_relative = key
        generic_rows.append(
            {
                "marker": marker,
                "owner_relative_offset": owner_relative,
                "object_relative_offset": object_relative,
                "captures": len(generic_captures[key]),
                **{field: int(value) for field, value in counts.items()},
                "is_direct37_control": (
                    marker == DIRECT37_MARKER
                    and owner_relative == 8
                    and object_relative == 13
                ),
                "is_strict_09_layout": (
                    marker.startswith("09 ")
                    and owner_relative == -4
                    and object_relative == 8
                ),
            }
        )
    generic_rows.sort(
        key=lambda row: (
            -int(row["captures"]),
            -int(row["occurrences"]),
            -int(row["unique_packets"]),
            str(row["marker"]),
        )
    )

    total_direct = int(totals["direct37_rows"])
    known_uncovered = int(totals["known_only_uncovered_rows"])
    candidate_gain = int(totals["candidate_gain_rows_without_prior_known_marker"])
    blocking_total = int(totals["current_blocking_events_with_exact_pair"])
    blocking_gain = int(totals["candidate_gain_blocking_events"])
    return {
        "summary": {
            "captures": len(reports),
            **{key: int(value) for key, value in totals.items()},
            "known_only_coverage_percent": round(
                int(totals["known_only_covered_rows"]) / max(1, total_direct) * 100,
                6,
            ),
            "candidate_gain_over_known_uncovered_percent": round(
                candidate_gain / max(1, known_uncovered) * 100,
                6,
            ),
            "candidate_gain_blocking_percent": round(
                blocking_gain / max(1, blocking_total) * 100,
                6,
            ),
        },
        "marker_rows": marker_rows,
        "candidate_template_packet_skill_validation": mapping_rows,
        "leave_one_capture_out": cross_validation,
        "transitions": _counter_dict(transitions),
        "guard_rejections": _counter_dict(guard_rejections),
        "generic_pair_layouts": generic_rows[:120],
    }


def _pct(numerator: int, denominator: int) -> str:
    return f"{numerator / max(1, denominator) * 100:.2f}%"


def _attach_static_projectile_validation(
    report: dict[str, Any],
    tables: dict[str, Any],
) -> None:
    """Cross-check each marker+Projectile subtype against client static tables.

    The DNT chain from Projectile.RowId to ACT is numeric. The final ACT to
    SkillTable edge is a resource-name/SkillLevel ProcessParam bridge, so it
    is never accepted by itself: a safe subtype also needs independent exact
    packet_skill_id rows from direct37 and cross-capture/actor coverage.
    """

    aggregate = report["aggregate"]
    rows = aggregate["candidate_template_packet_skill_validation"]
    verdict_counts: Counter[str] = Counter()
    safe_subtypes: list[dict[str, Any]] = []
    corroborated_subtypes: list[dict[str, Any]] = []
    conflict_subtypes: list[dict[str, Any]] = []

    for row in rows:
        template_id = int(row["projectile_template_id"])
        projectile = tables["projectiles"].get(template_id, {})
        bridges = list(
            projectile.get("skilllevel_name_bridge_not_protocol_proof", ())
        )
        static_candidates = [
            {
                "skill_id": int(item.get("skill_id", 0) or 0),
                "static_name": str(item.get("static_name", "") or ""),
                "base_skill_id": int(item.get("base_skill_id", 0) or 0),
                "process_params": [
                    str(value) for value in item.get("process_params", ())
                ],
            }
            for item in bridges
            if int(item.get("skill_id", 0) or 0) > 0
        ]
        static_ids = {item["skill_id"] for item in static_candidates}
        packet_counts = Counter(
            {
                int(skill_id): int(count)
                for skill_id, count in row["packet_skill_ids"].items()
            }
        )
        static_support = Counter(
            {
                skill_id: count
                for skill_id, count in packet_counts.items()
                if skill_id in static_ids
            }
        )
        static_conflicts = Counter(
            {
                skill_id: count
                for skill_id, count in packet_counts.items()
                if skill_id not in static_ids
            }
        )

        if not packet_counts:
            verdict = "unverified_no_packet_skill_id"
            reason = "该 marker+template 没有 direct37 固定槽技能样本"
        elif not static_ids:
            verdict = "unverified_no_static_skill_bridge"
            reason = "Projectile→ACT 存在，但 ACT→skill 静态资源名桥缺失"
        elif static_conflicts:
            verdict = "reject_static_packet_conflict"
            reason = "packet_skill_id 落在静态 Projectile→skill 候选族之外"
        elif bool(row["mapping_conflict"]) or int(row["instance_conflicts"]) > 0:
            verdict = "unverified_multi_skill_packet_mapping"
            reason = "同一 marker+template 或实例出现多个 packet_skill_id"
        elif (
            int(row["packet_skill_rows"]) >= SAFE_STATIC_PACKET_MIN_HITS
            and int(row["captures"]) >= SAFE_STATIC_PACKET_MIN_CAPTURES
            and int(row["unique_actors"]) >= SAFE_STATIC_PACKET_MIN_ACTORS
        ):
            verdict = "safe_registration_subtype"
            reason = (
                "静态 Projectile→ACT→skill 族一致，且 exact packet_skill_id "
                "达到跨抓包/actor 门槛"
            )
        else:
            verdict = "corroborated_but_below_gate"
            reason = "静态技能族一致，但 exact packet_skill_id 独立覆盖不足"

        row["static_projectile_validation"] = {
            "projectile_row_found": bool(projectile),
            "act_file_id": int(projectile.get("act_file_id", 0) or 0),
            "act_file": str(projectile.get("act_file", "") or ""),
            "static_skill_family_candidates": static_candidates,
            "static_support_packet_skill_ids": _counter_dict(static_support),
            "static_conflict_packet_skill_ids": _counter_dict(static_conflicts),
            "verdict": verdict,
            "reason": reason,
            "numeric_skill_foreign_key_found": False,
            "static_bridge_boundary": (
                "Projectile.RowId→_ActName→filetable.RowId is numeric; "
                "ACT→skill is a resource-name/SkillLevel ProcessParam bridge."
            ),
        }
        verdict_counts[verdict] += 1

        compact = {
            "marker": str(row["marker"]),
            "projectile_template_id": template_id,
            "act_file": str(projectile.get("act_file", "") or ""),
            "static_skill_ids": sorted(static_ids),
            "packet_skill_ids": _counter_dict(packet_counts),
            "captures": int(row["captures"]),
            "unique_actors": int(row["unique_actors"]),
            "packet_skill_rows": int(row["packet_skill_rows"]),
        }
        if verdict == "safe_registration_subtype":
            safe_subtypes.append(compact)
        elif verdict == "corroborated_but_below_gate":
            corroborated_subtypes.append(compact)
        elif verdict == "reject_static_packet_conflict":
            conflict_subtypes.append(compact)

    aggregate["static_projectile_skill_validation"] = {
        "gate": {
            "scope": "exact (marker, projectile_template_id) subtype only",
            "minimum_exact_packet_skill_rows": SAFE_STATIC_PACKET_MIN_HITS,
            "minimum_captures": SAFE_STATIC_PACKET_MIN_CAPTURES,
            "minimum_unique_actors": SAFE_STATIC_PACKET_MIN_ACTORS,
            "requires_packet_skill_within_static_family": True,
            "marker_wide_registration_authorized": False,
        },
        "verdict_counts": _counter_dict(verdict_counts),
        "safe_registration_subtypes": safe_subtypes,
        "corroborated_but_below_gate_subtypes": corroborated_subtypes,
        "rejected_static_conflict_subtypes": conflict_subtypes,
    }


def _skill_distribution(values: dict[str, int]) -> str:
    return ", ".join(
        f"{skill}:{count}" for skill, count in values.items()
    ) or "—"


def _markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    summary = aggregate["summary"]
    lines = [
        "# direct37 未知对象生命周期记录变体审计",
        "",
        "## 结论",
        "",
    ]
    candidates = [
        row for row in aggregate["marker_rows"] if not row["known_family"]
    ]
    static_validation = aggregate["static_projectile_skill_validation"]
    safe_subtypes = static_validation["safe_registration_subtypes"]
    corroborated_subtypes = static_validation[
        "corroborated_but_below_gate_subtypes"
    ]
    conflict_subtypes = static_validation["rejected_static_conflict_subtypes"]
    if candidates:
        names = "、".join(f"`{row['marker']}`" for row in candidates)
        lines.append(
            f"在严格 owner+object 成对反查下，发现 {names} 与已知 "
            "`09 5D/09 61/09 6E` 使用同一对象记录布局；布局相同不等于可注册。"
        )
    else:
        lines.append("未发现通过完整结构守卫的未知 `09` 对象记录变体。")
    if safe_subtypes:
        rendered = "、".join(
            f"`{row['marker']} + Projectile {row['projectile_template_id']}`"
            for row in safe_subtypes
        )
        lines.append(
            f"按静态 Projectile→ACT→skill 族与 exact `packet_skill_id` 双重门槛，"
            f"本批次只支持 {rendered} 作为精确子类型注册候选。"
        )
    else:
        lines.append("本批次没有未知子类型达到安全注册门槛。")
    if corroborated_subtypes:
        rendered = "、".join(
            f"`{row['marker']} + Projectile {row['projectile_template_id']}` "
            f"({row['packet_skill_rows']} exact Hit)"
            for row in corroborated_subtypes
        )
        lines.append(
            f"静态与 packet 一致但尚未达到 {SAFE_STATIC_PACKET_MIN_HITS} 条安全门槛的有 "
            f"{rendered}，不得提前接入。"
        )
    lines.append(
        "禁止把子类型结论泛化到整个 marker：`09 62/09 66/09 73/09 6F` "
        "均不应整体接入，`09 6A` 也不获得 marker 级授权。"
    )
    if conflict_subtypes:
        rendered_conflicts = "；".join(
            f"`{row['marker']} + {row['projectile_template_id']}` "
            f"静态={','.join(map(str, row['static_skill_ids']))}、"
            f"packet={_skill_distribution(row['packet_skill_ids'])}"
            for row in conflict_subtypes
        )
        lines.append(f"明确反例：{rendered_conflicts}。")
    lines.extend(
        [
            "",
            "本报告只把它们称为生命周期记录变体。创建、重绑、销毁语义仅按包序列位置列出候选，"
            "没有仅凭先后位置强行命名。所有技能交叉验证只使用 direct37 固定结果槽产生的 "
            "`packet_skill_id`；结果组、时间距离、最近动作和全局裸对象 ID 均未参与。",
            "",
            "## 协议布局",
            "",
            "以三字节 marker 起点 `M` 为基准，严格接收条件如下：",
            "",
            "| 字段 | 偏移 | 长度 | 守卫 |",
            "|---|---:|---:|---|",
            "| owner actor ID | `M-4` | 4 | 必须与 direct37 `D+8` 完整相等 |",
            "| marker | `M+0` | 3 | `09 xx 00` |",
            "| zero guard | `M+3..M+7` | 5 | 全零 |",
            "| damage object ID | `M+8` | 4 | 必须与同 owner 的 direct37 `D+13` 完整相等 |",
            "| Projectile template | `M+12` | 4 | 必须命中 `weapontable_projectile.RowId` |",
            "| owner repeat | `M+44` | 4 | 必须等于 `M-4` |",
            "",
            "direct37 真值字段为 owner `D+8`、object `D+13`；精确技能只读固定 "
            "`D+102/D+114` 结果槽。",
            "",
            "## 覆盖总览",
            "",
            "| 指标 | 数量 |",
            "|---|---:|",
            f"| 代表抓包 | {summary['captures']:,} |",
            f"| direct37 行 | {summary['direct37_rows']:,} |",
            f"| owner+object 对 | {summary['direct37_owner_object_pairs']:,} |",
            f"| packet_skill_id 精确行 | {summary['packet_skill_id_rows']:,} |",
            f"| 已知三 marker 覆盖行 | {summary['known_only_covered_rows']:,} "
            f"({_pct(summary['known_only_covered_rows'], summary['direct37_rows'])}) |",
            f"| 已知三 marker 未覆盖行 | {summary['known_only_uncovered_rows']:,} |",
            f"| 未知变体新增覆盖行 | {summary['candidate_gain_rows_without_prior_known_marker']:,} "
            f"({_pct(summary['candidate_gain_rows_without_prior_known_marker'], summary['known_only_uncovered_rows'])} of known-uncovered) |",
            f"| 新增覆盖中的 packet_skill_id 行 | {summary['candidate_gain_packet_skill_rows']:,} |",
            f"| 当前 release-gate 阻塞事件（完整对可复核） | {summary['current_blocking_events_with_exact_pair']:,} |",
            f"| 未知变体新增覆盖的阻塞事件 | {summary['candidate_gain_blocking_events']:,} "
            f"({_pct(summary['candidate_gain_blocking_events'], summary['current_blocking_events_with_exact_pair'])}) |",
            "",
            "## marker 支持、冲突与序列角色",
            "",
            "| marker | 类型 | 抓包 | 记录 | 对象对 | active direct37 | 新增覆盖 | packet_skill_id | 阻塞新增 | pre-first / between / post-last |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in aggregate["marker_rows"]:
        roles = row["sequence_roles"]
        role_text = (
            f"{roles.get('before_first_direct37', 0):,} / "
            f"{roles.get('between_direct37_rows', 0):,} / "
            f"{roles.get('after_last_direct37', 0):,}"
        )
        lines.append(
            f"| `{row['marker']}` | {'已知' if row['known_family'] else '候选'} | "
            f"{row['captures']:,} | {row['records']:,} | "
            f"{row['unique_owner_object_pairs']:,} | {row['active_direct37_rows']:,} | "
            f"{row['gain_rows_without_prior_known_marker']:,} | "
            f"{row['active_packet_skill_rows']:,} | {row['active_blocking_events']:,} | "
            f"{role_text} |"
        )

    lines.extend(
        [
            "",
            "`pre-first / between / post-last` 只是对象记录相对同一完整 owner+object 的 "
            "direct37 包序位置：分别支持“建立候选 / 刷新或重绑候选 / 结束或销毁候选”的研究方向，"
            "不等同于已证明语义。",
            "",
            "## 静态 Projectile→skill 与 packet_skill_id 精确交叉验证",
            "",
            "DNT 的 `Projectile.RowId→ACT` 是数值外键；`ACT→skill` 是 "
            "SkillLevel ProcessParam 资源名桥，并非数值外键。因此静态桥只用于独立一致性检查，"
            "必须再有 exact `packet_skill_id`，且授权粒度固定为 `(marker, Projectile)`。",
            "",
            "| marker | Projectile / ACT | 静态 skill 族 | 抓包/actor/精确 Hit | 阻塞Hit/其中新增 | packet_skill_id | 结论 |",
            "|---|---|---|---:|---:|---|---|",
        ]
    )
    mapping_rows = aggregate["candidate_template_packet_skill_validation"]
    verdict_labels = {
        "safe_registration_subtype": "安全子类型候选",
        "reject_static_packet_conflict": "拒绝：静态/packet 冲突",
        "unverified_no_packet_skill_id": "未验证：无精确 packet 技能",
        "unverified_no_static_skill_bridge": "未验证：无静态技能桥",
        "unverified_multi_skill_packet_mapping": "未验证：packet 多技能",
        "corroborated_but_below_gate": "一致但覆盖不足",
    }
    for row in mapping_rows:
        static = row["static_projectile_validation"]
        candidates_text = ", ".join(
            f"{item['skill_id']} {item['static_name']}".strip()
            for item in static["static_skill_family_candidates"]
        ) or "—"
        skills = _skill_distribution(row["packet_skill_ids"])
        lines.append(
            f"| `{row['marker']}` | {row['projectile_template_id']} / "
            f"`{static['act_file'] or '—'}` | {candidates_text} | "
            f"{row['captures']}/{row['unique_actors']}/{row['packet_skill_rows']} | "
            f"{row['active_blocking_events']}/{row['gain_blocking_events']} | "
            f"{skills} | {verdict_labels[static['verdict']]} |"
        )
    if not mapping_rows:
        lines.append("| — | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "### 留一抓包验证",
            "",
            "| 保留抓包 | 可测试实例 | 一致 | 冲突 | 训练集未覆盖 | 实例内部冲突 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in aggregate["leave_one_capture_out"]:
        lines.append(
            f"| {Path(row['holdout_capture']).name} | "
            f"{row['tested_lifecycle_instances']:,} | {row['matches']:,} | "
            f"{row['conflicts']:,} | {row['unmapped_lifecycle_instances']:,} | "
            f"{row['internally_conflicted_instances']:,} |"
        )
    lines.extend(
        [
            "",
            "留一验证只说明同一 `(marker, Projectile)` 在不同抓包中的 packet 分布可重复；"
            "它不能推翻静态反例。尤其 `09 62/09 66 + 3021` 即使 packet 内部稳定为 2207，"
            "仍与客户端 `IcicleInJection → 2353` 冲突。",
        ]
    )

    lines.extend(
        [
            "",
            "## 反向通用 marker/偏移扫描",
            "",
            "下表从 direct37 完整 owner+object 对反查同一解码包体；object 值只在该 owner 的对象集合内检查。"
            "`08 73 00 @ +8/+13` 是 direct37 自身阳性对照。对象对列是每抓包 64 桶指纹占用数之和，"
            "只用于通用候选排序；严格 09 表中的对象对计数仍为精确值。",
            "",
            "| marker | owner 偏移 | object 偏移 | 抓包 | 对象对指纹桶 | 次数 | 精确样本指纹桶 | 解释 |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    displayed_generic = 0
    for row in aggregate["generic_pair_layouts"]:
        if displayed_generic >= 30:
            break
        if int(row["captures"]) < 2 and int(row["occurrences"]) < 10:
            continue
        if row["is_direct37_control"]:
            interpretation = "direct37 阳性对照"
        elif row["is_strict_09_layout"]:
            interpretation = "09 对象记录布局"
        else:
            interpretation = "通用共现候选，未通过 09 完整守卫"
        lines.append(
            f"| `{row['marker']}` | {row['owner_relative_offset']:+d} | "
            f"{row['object_relative_offset']:+d} | {row['captures']} | "
            f"{row['owner_object_pair_hash_buckets_64']:,} | {row['occurrences']:,} | "
            f"{row['packet_skill_pair_hash_buckets_64']:,} | {interpretation} |"
        )
        displayed_generic += 1

    lines.extend(
        [
            "",
            "## 分抓包覆盖",
            "",
            "| 抓包 | direct37 | 已知未覆盖 | 候选新增 | packet_skill_id 新增 | 当前阻塞 | 候选阻塞新增 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for capture in report["reports"]:
        row = capture["summary"]
        lines.append(
            f"| {Path(capture['capture']).name} | {row['direct37_rows']:,} | "
            f"{row['known_only_uncovered_rows']:,} | "
            f"{row['candidate_gain_rows_without_prior_known_marker']:,} | "
            f"{row['candidate_gain_packet_skill_rows']:,} | "
            f"{row['current_blocking_events_with_exact_pair']:,} | "
            f"{row['candidate_gain_blocking_events']:,} |"
        )

    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 覆盖表示存在包序上先行、完整 owner+object 相等且通过结构守卫的记录；它不是自动生产归因授权。",
            "- `packet_skill_id` 只验证候选记录实例/Projectile 与精确技能真值是否一致；没有用结果组补齐无技能 Hit。",
            "- 只有报告列出的 `(marker, Projectile)` 安全子类型可作为接入候选；同 marker 的其他 template 不继承结论。",
            "- 静态 Projectile→skill 桥与 packet 冲突时按 fail-closed 拒绝；无 packet 或无静态桥时只列未验证，不算支持。",
            "- 通用扫描中的非 09 共现项若未满足重复 owner、零守卫和 Projectile 外键，只保留为否证/后续研究候选。",
            "- post-last 记录可能是销毁，也可能是最终状态刷新或抓包边界；需要独立协议字段或受控实验才能命名。",
            "- 本轮未修改生产代码。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reverse-audit unknown S2C object lifecycle variants from exact "
            "direct37 (owner actor, damage object) pairs."
        )
    )
    parser.add_argument("captures", nargs="*", type=Path)
    parser.add_argument("--resource-root", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True, type=Path)
    parser.add_argument(
        "--reclassify-existing",
        type=Path,
        help="Reuse an existing raw audit JSON and only apply static validation.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--skip-generic-layouts",
        action="store_true",
        help=(
            "Skip the expensive exploratory reverse-layout scan and only "
            "validate the strict owner/object lifecycle record layout."
        ),
    )
    args = parser.parse_args()

    tables = load_client_tables(args.resource_root.resolve())
    projectile_ids = frozenset(int(value) for value in tables["projectiles"])
    if args.reclassify_existing is not None:
        existing_path = args.reclassify_existing.resolve()
        if not existing_path.is_file():
            parser.error(f"existing report not found: {existing_path}")
        result = json.loads(existing_path.read_text(encoding="utf-8"))
        result["schema_version"] = SCHEMA_VERSION
        result["resource_root"] = str(args.resource_root.resolve())
        result["projectile_rows"] = len(projectile_ids)
        result["policy"]["static_projectile_skill_gate"] = (
            "Registration is authorized only for an exact "
            "(marker, projectile_template_id) subtype whose DNT ACT/SkillLevel "
            "resource family agrees with cross-capture exact packet_skill_id."
        )
        _attach_static_projectile_validation(result, tables)
        output_prefix = args.output_prefix.resolve()
        output_prefix.parent.mkdir(parents=True, exist_ok=True)
        json_path = output_prefix.with_suffix(".json")
        markdown_path = output_prefix.with_suffix(".md")
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(_markdown(result), encoding="utf-8")
        print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}))
        return 0

    if not args.captures:
        parser.error(
            "at least one capture is required unless --reclassify-existing is used"
        )
    captures = [path.resolve() for path in args.captures]
    missing = [str(path) for path in captures if not path.is_file()]
    if missing:
        parser.error(f"capture not found: {', '.join(missing)}")

    reports: list[dict[str, Any]] = []
    workers = max(1, min(int(args.workers), len(captures)))
    scan_generic_layouts = not bool(args.skip_generic_layouts)
    if workers == 1:
        reports = [
            _summarize_capture(
                path,
                projectile_ids,
                scan_generic_layouts=scan_generic_layouts,
            )
            for path in captures
        ]
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _worker,
                    str(path),
                    projectile_ids,
                    scan_generic_layouts,
                ): path
                for path in captures
            }
            for future in as_completed(futures):
                reports.append(future.result())
        order = {str(path): index for index, path in enumerate(captures)}
        reports.sort(key=lambda row: order[str(Path(row["capture"]).resolve())])

    aggregate = _aggregate(reports)
    for report in reports:
        report["generic_pair_layouts"] = report.pop(
            "generic_pair_layouts_internal"
        )[:40]
    result = {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "identity": (
                "Every join uses the capture-local full "
                "(owner_actor_id, damage_object_id) pair."
            ),
            "ordering": (
                "Only packet index and record offset establish explicit "
                "before/replacement state; no time-distance window is used."
            ),
            "skill_truth": (
                "Only direct37 fixed result-vector slots D+102/D+114 "
                "(packet_skill_id) are skill truth."
            ),
            "static_projectile_skill_gate": (
                "Registration is authorized only for an exact "
                "(marker, projectile_template_id) subtype whose DNT ACT/SkillLevel "
                "resource family agrees with cross-capture exact packet_skill_id."
            ),
            "excluded": [
                "timestamp proximity",
                "result-group inheritance",
                "global bare damage_object_id lookup",
                "recent action/cast inference",
            ],
        },
        "resource_root": str(args.resource_root.resolve()),
        "projectile_rows": len(projectile_ids),
        "record_layout": {
            "marker": "M+0: 09 xx 00",
            "owner_actor_id": "M-4 (must equal M+44)",
            "zero_guard": "M+3..M+7",
            "damage_object_id": "M+8",
            "projectile_template_id": "M+12",
            "direct37_owner_actor_id": "D+8",
            "direct37_damage_object_id": "D+13",
            "direct37_packet_skill_id": "D+102 or D+114",
        },
        "aggregate": aggregate,
        "reports": reports,
    }
    _attach_static_projectile_validation(result, tables)

    output_prefix = args.output_prefix.resolve()
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
