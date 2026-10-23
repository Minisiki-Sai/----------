from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    REJECTED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.audit_direct_damage_release_gate import (  # noqa: E402
    _classification,
)


SCHEMA_VERSION = 2
SIGNATURE_FILES = (
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_ledger.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/v576/combat_decoder.py",
    "dragon_nest_builder/live_dps/v576/status_decoder.py",
    "dragon_nest_builder/data/live_dps_profiles/v576.json",
)
WINDOWS_MS = (5, 10, 20, 50, 100, 250, 500, 1000)
EXACT_PACKET_ANCHORS = frozenset({"packet_skill_id"})
EXACT_OBJECT_ANCHORS = frozenset(
    {
        "packet_skill_id",
        "damage_object_projectile_link",
    }
)
STATUS_METHODS = frozenset(
    {
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
        "periodic_status",
    }
)


def _analysis_signature() -> dict[str, Any]:
    files: dict[str, str] = {}
    combined = hashlib.sha256()
    for relative in SIGNATURE_FILES:
        path = ROOT / relative
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[relative] = digest
        combined.update(relative.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\0")
    return {"sha256": combined.hexdigest(), "files": files}


def _counter_dict(value: Counter[Any]) -> dict[str, int]:
    return {str(key): int(count) for key, count in value.most_common()}


def _parse_actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _identity(event: CombatEvent) -> tuple[str, str]:
    return str(event.skill_id or ""), str(event.skill_name or "")


def _raw_identity(event: CombatEvent) -> tuple[str, str]:
    return (
        str(event.raw_skill_id or event.skill_id or ""),
        str(event.raw_skill_name or event.skill_name or ""),
    )


def _raw_method(event: CombatEvent) -> str:
    return str(
        event.raw_attribution_method or event.attribution_method or "unresolved"
    ).strip().casefold()


def _event_key(event: CombatEvent, *, raw: bool = False) -> tuple[object, ...]:
    skill_id, skill_name = _raw_identity(event) if raw else _identity(event)
    method = _raw_method(event) if raw else str(
        event.attribution_method or "unresolved"
    ).strip().casefold()
    return (
        int(event.timestamp_ms),
        str(event.source_id),
        str(event.target_id),
        int(event.amount),
        int(event.wire_result_group_id),
        int(event.wire_damage_object_id),
        skill_id,
        skill_name,
        method,
    )


def _relaxed_key(event: CombatEvent) -> tuple[object, ...]:
    return (
        int(event.timestamp_ms),
        str(event.source_id),
        str(event.target_id),
        int(event.amount),
        int(event.wire_result_group_id),
        int(event.wire_damage_object_id),
    )


def _layout_family(layout: str, event: CombatEvent) -> str:
    # These two fields are decoded only from a direct37 record. They outrank
    # the audit-only layout matcher, where two equal damage rows can share a
    # timestamp and otherwise collide.
    if event.wire_result_group_id > 0 or event.wire_damage_object_id > 0:
        return "direct37"
    value = str(layout or "").casefold()
    if "direct37" in value:
        return "direct37"
    if "direct41" in value:
        return "direct41"
    if "periodic" in value or "status" in value:
        return "status_or_periodic"
    if "hit" in value or "damage" in value:
        return "other_direct_layout"
    method = str(event.attribution_method or "").strip().casefold()
    if method in STATUS_METHODS:
        return "status_or_periodic"
    if event.has_real_hit_evidence is True:
        return "unmapped_real_hit_layout"
    return "nonhit_or_unknown_layout"


def _unique_counter_value(counter: Counter[str] | None) -> str:
    if not counter or len(counter) != 1:
        return ""
    return next(iter(counter))


def _registration_for_event(
    registrations: dict[tuple[int, int], list[dict[str, int]]],
    event: CombatEvent,
) -> dict[str, int] | None:
    actor_id = _parse_actor(event.source_id)
    object_id = int(event.wire_damage_object_id)
    if actor_id <= 0 or object_id <= 0:
        return None
    rows = registrations.get((actor_id, object_id), ())
    if not rows:
        return None
    timestamps = [row["timestamp_ms"] for row in rows]
    index = bisect.bisect_right(timestamps, int(event.timestamp_ms)) - 1
    if index < 0:
        return None
    row = rows[index]
    if int(event.timestamp_ms) - row["timestamp_ms"] > 120_000:
        return None
    return row


def _counter_add(target: dict[str, int], key: object, value: int = 1) -> None:
    text = str(key)
    target[text] = int(target.get(text, 0)) + int(value)


def _audit_one(path_text: str, source_name: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder

    primary_layout_by_id: dict[int, str] = {}
    primary_layout_by_key: dict[tuple[object, ...], Counter[str]] = defaultdict(
        Counter
    )
    primary_layout_by_time_amount: dict[
        tuple[int, int], Counter[str]
    ] = defaultdict(Counter)
    registrations_absolute: list[dict[str, int | float]] = []

    original_feed = decoder.feed

    def feed(packet, packet_features=None):
        event = original_feed(packet, packet_features)
        if isinstance(event, CombatEvent):
            layout = str(getattr(decoder.last_decoded_message, "layout", "") or "")
            primary_layout_by_id[id(event)] = layout
            primary_layout_by_key[_event_key(event)][layout] += 1
            primary_layout_by_time_amount[
                (int(event.timestamp_ms), int(event.amount))
            ][layout] += 1
        return event

    object_ledger = decoder.damage_object_origins
    original_registration = object_ledger.observe_registration

    def observe_registration(**kwargs):
        registrations_absolute.append(
            {
                "source_actor_id": int(kwargs["source_actor_id"]),
                "damage_object_id": int(kwargs["damage_object_id"]),
                "projectile_template_id": int(kwargs["projectile_template_id"]),
                "effect_resource_id": int(kwargs["effect_resource_id"]),
                "child_sequence": int(kwargs["child_sequence"]),
                "timestamp": float(kwargs["timestamp"]),
            }
        )
        return original_registration(**kwargs)

    decoder.feed = feed
    object_ledger.observe_registration = observe_registration
    try:
        source_events = source.poll()
    finally:
        source.close()

    origin = float(decoder.first_timestamp or 0.0)
    registrations: dict[tuple[int, int], list[dict[str, int]]] = defaultdict(list)
    for ordinal, row in enumerate(registrations_absolute):
        converted = {
            "ordinal": ordinal,
            "source_actor_id": int(row["source_actor_id"]),
            "damage_object_id": int(row["damage_object_id"]),
            "projectile_template_id": int(row["projectile_template_id"]),
            "effect_resource_id": int(row["effect_resource_id"]),
            "child_sequence": int(row["child_sequence"]),
            "timestamp_ms": max(
                0, round((float(row["timestamp"]) - origin) * 1000.0)
            ),
        }
        registrations[
            (converted["source_actor_id"], converted["damage_object_id"])
        ].append(converted)
    for rows in registrations.values():
        rows.sort(key=lambda row: row["timestamp_ms"])

    raw_layout_full: dict[tuple[object, ...], Counter[str]] = defaultdict(Counter)
    raw_layout_relaxed: dict[tuple[object, ...], Counter[str]] = defaultdict(Counter)
    raw_layout_match: Counter[str] = Counter()
    for event in source_events:
        if not isinstance(event, CombatEvent):
            continue
        layout = primary_layout_by_id.get(id(event), "")
        if layout:
            raw_layout_match["same_object"] += 1
        else:
            layout = _unique_counter_value(primary_layout_by_key.get(_event_key(event)))
            if layout:
                raw_layout_match["stable_key"] += 1
        if not layout and (
            event.wire_result_group_id > 0 or event.wire_damage_object_id > 0
        ):
            layout = "direct37-metadata-fallback"
            raw_layout_match["direct37_metadata_fallback"] += 1
        if not layout and str(event.attribution_method).casefold() in STATUS_METHODS:
            layout = "status-method-fallback"
            raw_layout_match["status_method_fallback"] += 1
        if not layout:
            raw_layout_match["unmapped_source_event"] += 1
        raw_layout_full[_event_key(event)][layout] += 1
        raw_layout_relaxed[_relaxed_key(event)][layout] += 1

    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)

    classifications: Counter[str] = Counter()
    classification_damage: Counter[str] = Counter()
    families: Counter[str] = Counter()
    family_damage: Counter[str] = Counter()
    blocker_methods: Counter[str] = Counter()
    blocker_method_damage: Counter[str] = Counter()
    blocker_jobs: Counter[str] = Counter()
    blocker_job_damage: Counter[str] = Counter()
    job_hits: Counter[str] = Counter()
    job_damage: Counter[str] = Counter()
    job_blocking_hits: Counter[str] = Counter()
    job_blocking_damage: Counter[str] = Counter()
    job_deterministic_hits: Counter[str] = Counter()
    current_skill_buckets: dict[str, dict[str, Any]] = {}
    blocker_skills: dict[str, dict[str, Any]] = {}
    blocker_families: Counter[str] = Counter()
    blocker_family_damage: Counter[str] = Counter()
    blocker_layouts: Counter[str] = Counter()
    blocker_family_methods: Counter[str] = Counter()
    blocker_job_families: Counter[str] = Counter()
    blocker_object_presence: Counter[str] = Counter()
    blocker_group_presence: Counter[str] = Counter()
    final_layout_match: Counter[str] = Counter()
    layouts: Counter[str] = Counter()

    visible_events: list[tuple[CombatEvent, str, str, str]] = []
    for event in snapshot.combat_events:
        classification = _classification(event)
        if classification == "not_outgoing_damage":
            continue
        amount = max(0, int(event.amount))
        layout = _unique_counter_value(raw_layout_full.get(_event_key(event, raw=True)))
        if layout:
            final_layout_match["full_raw_key"] += 1
        else:
            layout = _unique_counter_value(raw_layout_relaxed.get(_relaxed_key(event)))
            if layout:
                final_layout_match["relaxed_key"] += 1
        if not layout:
            layout = _unique_counter_value(
                primary_layout_by_time_amount.get(
                    (int(event.timestamp_ms), int(event.amount))
                )
            )
            if layout:
                final_layout_match["time_amount_unique_layout"] += 1
        if not layout and (
            event.wire_result_group_id > 0 or event.wire_damage_object_id > 0
        ):
            layout = "direct37-metadata-fallback"
            final_layout_match["direct37_metadata_fallback"] += 1
        if not layout and str(event.attribution_method).casefold() in STATUS_METHODS:
            layout = "status-method-fallback"
            final_layout_match["status_method_fallback"] += 1
        if not layout:
            final_layout_match["unmapped"] += 1
        family = _layout_family(layout, event)
        method = str(event.attribution_method or "unresolved").strip().casefold()
        visible_events.append((event, layout, family, classification))
        classifications[classification] += 1
        classification_damage[classification] += amount
        families[family] += 1
        family_damage[family] += amount
        layouts[layout or "<unmapped>"] += 1
        job = str(event.job or "未知职业")
        job_hits[job] += 1
        job_damage[job] += amount
        if classification in {
            "deterministic",
            "deterministic_entity_owner",
            "deterministic_status_owner",
        }:
            job_deterministic_hits[job] += 1
        current_skill_key = "|".join(
            (job, str(event.skill_id), str(event.skill_name))
        )
        current_skill = current_skill_buckets.setdefault(
            current_skill_key,
            {
                "job": job,
                "skill_id": str(event.skill_id),
                "skill_name": str(event.skill_name),
                "hits": 0,
                "damage": 0,
                "blocking_hits": 0,
                "blocking_damage": 0,
                "methods": {},
                "families": {},
            },
        )
        current_skill["hits"] += 1
        current_skill["damage"] += amount
        _counter_add(current_skill["methods"], method)
        _counter_add(current_skill["families"], family)
        if classification != "blocking_unproven_direct_hit":
            continue
        blocker_methods[method] += 1
        blocker_method_damage[method] += amount
        blocker_jobs[job] += 1
        blocker_job_damage[job] += amount
        job_blocking_hits[job] += 1
        job_blocking_damage[job] += amount
        current_skill["blocking_hits"] += 1
        current_skill["blocking_damage"] += amount
        blocker_families[family] += 1
        blocker_family_damage[family] += amount
        blocker_layouts[layout or "<unmapped>"] += 1
        blocker_family_methods[f"{family}|{method}"] += 1
        blocker_job_families[f"{job}|{family}"] += 1
        blocker_object_presence[
            "object_id_present" if event.wire_damage_object_id > 0 else "no_object_id"
        ] += 1
        blocker_group_presence[
            "result_group_present" if event.wire_result_group_id > 0 else "no_result_group"
        ] += 1
        key = "|".join((job, str(event.skill_id), str(event.skill_name)))
        row = blocker_skills.setdefault(
            key,
            {
                "job": job,
                "skill_id": str(event.skill_id),
                "skill_name": str(event.skill_name),
                "hits": 0,
                "damage": 0,
                "methods": {},
                "families": {},
            },
        )
        row["hits"] += 1
        row["damage"] += amount
        _counter_add(row["methods"], method)
        _counter_add(row["families"], family)

    # Result-group audit: only the fixed packet skill field is an anchor.
    result_groups: dict[tuple[str, int], list[CombatEvent]] = defaultdict(list)
    for event, _layout, family, _classification_name in visible_events:
        if family.startswith("direct37") and event.wire_result_group_id > 0:
            result_groups[(event.source_id, event.wire_result_group_id)].append(event)
    result_group_stats: Counter[str] = Counter()
    result_group_windows: dict[str, Counter[str]] = {
        str(window): Counter() for window in WINDOWS_MS
    }
    result_group_conflicts: list[dict[str, Any]] = []
    for (source_id, result_group_id), events in result_groups.items():
        result_group_stats["groups"] += 1
        anchors = [
            event
            for event in events
            if str(event.attribution_method).casefold() in EXACT_PACKET_ANCHORS
        ]
        identities = {_identity(event) for event in anchors}
        blockers = [
            event
            for event in events
            if _classification(event) == "blocking_unproven_direct_hit"
        ]
        if anchors:
            result_group_stats["groups_with_packet_anchor"] += 1
        if len(identities) == 1:
            result_group_stats["groups_with_unique_packet_identity"] += 1
        elif len(identities) > 1:
            result_group_stats["groups_with_conflicting_packet_identities"] += 1
            if len(result_group_conflicts) < 20:
                result_group_conflicts.append(
                    {
                        "source_id": source_id,
                        "result_group_id": result_group_id,
                        "identities": sorted([list(row) for row in identities]),
                        "timestamps_ms": sorted(event.timestamp_ms for event in anchors),
                        "capture": source_name,
                    }
                )
        result_group_stats["blocking_hits"] += len(blockers)
        for blocker in blockers:
            for window in WINDOWS_MS:
                nearby = [
                    anchor
                    for anchor in anchors
                    if abs(anchor.timestamp_ms - blocker.timestamp_ms) <= window
                ]
                nearby_identities = {_identity(anchor) for anchor in nearby}
                stats = result_group_windows[str(window)]
                if len(nearby_identities) == 1:
                    stats["uniquely_recoverable_hits"] += 1
                    stats["uniquely_recoverable_damage"] += int(blocker.amount)
                elif len(nearby_identities) > 1:
                    stats["ambiguous_hits"] += 1
                    stats["ambiguous_damage"] += int(blocker.amount)
                else:
                    stats["unanchored_hits"] += 1

    # Actor + full object ID reuse audit.
    objects: dict[tuple[str, int], list[CombatEvent]] = defaultdict(list)
    for event, _layout, family, _classification_name in visible_events:
        if family.startswith("direct37") and event.wire_damage_object_id > 0:
            objects[(event.source_id, event.wire_damage_object_id)].append(event)
    object_stats: Counter[str] = Counter()
    object_methods: Counter[str] = Counter()
    object_skills: Counter[str] = Counter()
    object_conflicts: list[dict[str, Any]] = []
    for (source_id, object_id), events in objects.items():
        object_stats["objects"] += 1
        anchors = [
            event
            for event in events
            if str(event.attribution_method).casefold() in EXACT_OBJECT_ANCHORS
        ]
        identities = {_identity(event) for event in anchors}
        blockers = [
            event
            for event in events
            if _classification(event) == "blocking_unproven_direct_hit"
        ]
        if anchors:
            object_stats["objects_with_exact_anchor"] += 1
        if len(identities) == 1:
            object_stats["objects_with_unique_exact_identity"] += 1
            if blockers:
                object_stats["candidate_objects"] += 1
                object_stats["candidate_hits"] += len(blockers)
                object_stats["candidate_damage"] += sum(
                    int(event.amount) for event in blockers
                )
                identity = next(iter(identities))
                object_skills[f"{identity[0]}|{identity[1]}"] += len(blockers)
                for event in blockers:
                    object_methods[str(event.attribution_method).casefold()] += 1
        elif len(identities) > 1:
            object_stats["objects_with_conflicting_exact_identities"] += 1
            object_stats["conflicting_object_hits"] += len(events)
            span_ms = max(event.timestamp_ms for event in events) - min(
                event.timestamp_ms for event in events
            )
            if span_ms <= 5:
                span_bucket = "conflict_span_le_5ms"
            elif span_ms <= 20:
                span_bucket = "conflict_span_le_20ms"
            elif span_ms <= 100:
                span_bucket = "conflict_span_le_100ms"
            elif span_ms <= 1_000:
                span_bucket = "conflict_span_le_1s"
            elif span_ms <= 5_000:
                span_bucket = "conflict_span_le_5s"
            elif span_ms <= 30_000:
                span_bucket = "conflict_span_le_30s"
            else:
                span_bucket = "conflict_span_gt_30s"
            object_stats[span_bucket] += 1
            if len(object_conflicts) < 20:
                object_conflicts.append(
                    {
                        "capture": source_name,
                        "source_id": source_id,
                        "object_id": object_id,
                        "identities": sorted([list(row) for row in identities]),
                        "first_ms": min(event.timestamp_ms for event in events),
                        "last_ms": max(event.timestamp_ms for event in events),
                        "hits": len(events),
                    }
                )

    # Projectile template inference from packet anchors on the exact registered
    # object. No recent-action or learned-profile evidence participates.
    template_rows: dict[int, dict[str, Any]] = {}
    registration_instances_seen: set[int] = set()
    for event, _layout, family, classification in visible_events:
        if not family.startswith("direct37"):
            continue
        registration = _registration_for_event(registrations, event)
        if registration is None:
            continue
        template_id = registration["projectile_template_id"]
        registration_instances_seen.add(registration["ordinal"])
        row = template_rows.setdefault(
            template_id,
            {
                "projectile_template_id": template_id,
                "registration_ordinals": set(),
                "actors": set(),
                "direct37_hits": 0,
                "direct37_damage": 0,
                "packet_anchor_hits": {},
                "packet_anchor_damage": {},
                "blocker_hits": 0,
                "blocker_damage": 0,
                "blocker_methods": {},
                "blocker_jobs": {},
                "blocker_current_skills": {},
            },
        )
        row["registration_ordinals"].add(registration["ordinal"])
        row["actors"].add(event.source_id)
        row["direct37_hits"] += 1
        row["direct37_damage"] += int(event.amount)
        method = str(event.attribution_method or "").casefold()
        if method in EXACT_PACKET_ANCHORS:
            identity_key = f"{event.skill_id}|{event.skill_name}"
            _counter_add(row["packet_anchor_hits"], identity_key)
            _counter_add(row["packet_anchor_damage"], identity_key, int(event.amount))
        if classification == "blocking_unproven_direct_hit":
            row["blocker_hits"] += 1
            row["blocker_damage"] += int(event.amount)
            _counter_add(row["blocker_methods"], method)
            _counter_add(row["blocker_jobs"], event.job or "未知职业")
            _counter_add(
                row["blocker_current_skills"],
                f"{event.skill_id}|{event.skill_name}",
            )

    serialized_templates = []
    for template_id, row in sorted(template_rows.items()):
        serialized_templates.append(
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"registration_ordinals", "actors"}
                },
                "registrations_with_visible_hits": len(row["registration_ordinals"]),
                "actors": sorted(row["actors"]),
            }
        )

    total_hits = sum(classifications.values())
    blocking_hits = classifications["blocking_unproven_direct_hit"]
    total_damage = sum(classification_damage.values())
    blocking_damage = classification_damage["blocking_unproven_direct_hit"]
    return {
        "capture": source_name,
        "path": str(path),
        "decoded_events": len(source_events),
        "total_outgoing_hits": total_hits,
        "total_outgoing_damage": total_damage,
        "blocking_hits": blocking_hits,
        "blocking_damage": blocking_damage,
        "coverage_percent": round(
            (total_hits - blocking_hits) / max(1, total_hits) * 100.0, 6
        ),
        "classifications": _counter_dict(classifications),
        "classification_damage": _counter_dict(classification_damage),
        "families": _counter_dict(families),
        "family_damage": _counter_dict(family_damage),
        "layouts": _counter_dict(layouts),
        "raw_layout_match": _counter_dict(raw_layout_match),
        "final_layout_match": _counter_dict(final_layout_match),
        "blocker_methods": _counter_dict(blocker_methods),
        "blocker_method_damage": _counter_dict(blocker_method_damage),
        "blocker_jobs": _counter_dict(blocker_jobs),
        "blocker_job_damage": _counter_dict(blocker_job_damage),
        "job_hits": _counter_dict(job_hits),
        "job_damage": _counter_dict(job_damage),
        "job_blocking_hits": _counter_dict(job_blocking_hits),
        "job_blocking_damage": _counter_dict(job_blocking_damage),
        "job_deterministic_hits": _counter_dict(job_deterministic_hits),
        "current_skill_buckets": list(current_skill_buckets.values()),
        "blocker_skills": list(blocker_skills.values()),
        "blocker_families": _counter_dict(blocker_families),
        "blocker_family_damage": _counter_dict(blocker_family_damage),
        "blocker_layouts": _counter_dict(blocker_layouts),
        "blocker_family_methods": _counter_dict(blocker_family_methods),
        "blocker_job_families": _counter_dict(blocker_job_families),
        "blocker_object_presence": _counter_dict(blocker_object_presence),
        "blocker_group_presence": _counter_dict(blocker_group_presence),
        "result_group_stats": _counter_dict(result_group_stats),
        "result_group_windows": {
            key: _counter_dict(value) for key, value in result_group_windows.items()
        },
        "result_group_conflicts": result_group_conflicts,
        "object_stats": _counter_dict(object_stats),
        "object_methods": _counter_dict(object_methods),
        "object_skills": _counter_dict(object_skills),
        "object_conflicts": object_conflicts,
        "registrations": len(registrations_absolute),
        "registration_instances_with_visible_hits": len(registration_instances_seen),
        "templates": serialized_templates,
    }


def _merge_counter(target: Counter[str], values: dict[str, int]) -> None:
    target.update({str(key): int(value) for key, value in values.items()})


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _checkpoint_path(directory: Path, source_name: str) -> Path:
    digest = hashlib.sha256(source_name.encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _load_checkpoint(
    path: Path,
    *,
    source_name: str,
    analysis_signature: dict[str, Any],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("source_name") != source_name:
        return None
    if payload.get("analysis_signature") != analysis_signature:
        return None
    report = payload.get("report")
    return report if isinstance(report, dict) else None


def _write_checkpoint(
    path: Path,
    *,
    source_name: str,
    analysis_signature: dict[str, Any],
    report: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "source_name": source_name,
                "analysis_signature": analysis_signature,
                "report": report,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _merge(
    reports: list[dict[str, Any]], *, analysis_signature: dict[str, Any]
) -> dict[str, Any]:
    scalar_keys = (
        "decoded_events",
        "total_outgoing_hits",
        "total_outgoing_damage",
        "blocking_hits",
        "blocking_damage",
        "registrations",
        "registration_instances_with_visible_hits",
    )
    totals: Counter[str] = Counter()
    counter_keys = (
        "classifications",
        "classification_damage",
        "families",
        "family_damage",
        "layouts",
        "raw_layout_match",
        "final_layout_match",
        "blocker_methods",
        "blocker_method_damage",
        "blocker_jobs",
        "blocker_job_damage",
        "job_hits",
        "job_damage",
        "job_blocking_hits",
        "job_blocking_damage",
        "job_deterministic_hits",
        "blocker_families",
        "blocker_family_damage",
        "blocker_layouts",
        "blocker_family_methods",
        "blocker_job_families",
        "blocker_object_presence",
        "blocker_group_presence",
        "result_group_stats",
        "object_stats",
        "object_methods",
        "object_skills",
    )
    counters = {key: Counter() for key in counter_keys}
    window_counters = {str(window): Counter() for window in WINDOWS_MS}
    skills: dict[str, dict[str, Any]] = {}
    all_skill_buckets: dict[str, dict[str, Any]] = {}
    templates: dict[int, dict[str, Any]] = {}
    result_group_conflicts: list[dict[str, Any]] = []
    object_conflicts: list[dict[str, Any]] = []
    for report in reports:
        for key in scalar_keys:
            totals[key] += int(report.get(key, 0))
        for key in counter_keys:
            _merge_counter(counters[key], report.get(key, {}))
        for window, values in report["result_group_windows"].items():
            _merge_counter(window_counters[window], values)
        result_group_conflicts.extend(report["result_group_conflicts"])
        object_conflicts.extend(report["object_conflicts"])
        for row in report["blocker_skills"]:
            key = "|".join((row["job"], row["skill_id"], row["skill_name"]))
            merged = skills.setdefault(
                key,
                {
                    "job": row["job"],
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "hits": 0,
                    "damage": 0,
                    "methods": Counter(),
                    "families": Counter(),
                    "files": set(),
                },
            )
            merged["hits"] += int(row["hits"])
            merged["damage"] += int(row["damage"])
            _merge_counter(merged["methods"], row["methods"])
            _merge_counter(merged["families"], row["families"])
            merged["files"].add(report["capture"])
        for row in report["current_skill_buckets"]:
            key = "|".join((row["job"], row["skill_id"], row["skill_name"]))
            merged = all_skill_buckets.setdefault(
                key,
                {
                    "job": row["job"],
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "hits": 0,
                    "damage": 0,
                    "blocking_hits": 0,
                    "blocking_damage": 0,
                    "methods": Counter(),
                    "families": Counter(),
                    "files": set(),
                },
            )
            for scalar in ("hits", "damage", "blocking_hits", "blocking_damage"):
                merged[scalar] += int(row[scalar])
            _merge_counter(merged["methods"], row["methods"])
            _merge_counter(merged["families"], row["families"])
            merged["files"].add(report["capture"])
        for row in report["templates"]:
            template_id = int(row["projectile_template_id"])
            merged = templates.setdefault(
                template_id,
                {
                    "projectile_template_id": template_id,
                    "files": set(),
                    "actors": set(),
                    "registrations_with_visible_hits": 0,
                    "direct37_hits": 0,
                    "direct37_damage": 0,
                    "packet_anchor_hits": Counter(),
                    "packet_anchor_damage": Counter(),
                    "blocker_hits": 0,
                    "blocker_damage": 0,
                    "blocker_methods": Counter(),
                    "blocker_jobs": Counter(),
                    "blocker_current_skills": Counter(),
                },
            )
            merged["files"].add(report["capture"])
            merged["actors"].update(row["actors"])
            for key in (
                "registrations_with_visible_hits",
                "direct37_hits",
                "direct37_damage",
                "blocker_hits",
                "blocker_damage",
            ):
                merged[key] += int(row[key])
            for key in (
                "packet_anchor_hits",
                "packet_anchor_damage",
                "blocker_methods",
                "blocker_jobs",
                "blocker_current_skills",
            ):
                _merge_counter(merged[key], row[key])

    skill_rows = []
    for row in skills.values():
        skill_rows.append(
            {
                **{key: row[key] for key in ("job", "skill_id", "skill_name", "hits", "damage")},
                "file_count": len(row["files"]),
                "methods": _counter_dict(row["methods"]),
                "families": _counter_dict(row["families"]),
            }
        )
    skill_rows.sort(key=lambda row: (-row["hits"], -row["damage"], row["job"]))

    current_bucket_rows = []
    for row in all_skill_buckets.values():
        current_bucket_rows.append(
            {
                **{
                    key: row[key]
                    for key in (
                        "job",
                        "skill_id",
                        "skill_name",
                        "hits",
                        "damage",
                        "blocking_hits",
                        "blocking_damage",
                    )
                },
                "blocking_hit_percent": round(
                    row["blocking_hits"] / max(1, row["hits"]) * 100.0, 6
                ),
                "blocking_damage_percent": round(
                    row["blocking_damage"] / max(1, row["damage"]) * 100.0, 6
                ),
                "file_count": len(row["files"]),
                "methods": _counter_dict(row["methods"]),
                "families": _counter_dict(row["families"]),
            }
        )
    current_bucket_rows.sort(
        key=lambda row: (-row["blocking_hits"], -row["blocking_damage"], row["job"])
    )

    job_rows = []
    all_jobs = set(counters["job_hits"]) | set(counters["job_blocking_hits"])
    for job in all_jobs:
        hits = int(counters["job_hits"][job])
        damage = int(counters["job_damage"][job])
        blocking = int(counters["job_blocking_hits"][job])
        blocking_damage = int(counters["job_blocking_damage"][job])
        job_rows.append(
            {
                "job": job,
                "hits": hits,
                "damage": damage,
                "deterministic_hits": int(counters["job_deterministic_hits"][job]),
                "blocking_hits": blocking,
                "blocking_damage": blocking_damage,
                "blocking_hit_percent": round(blocking / max(1, hits) * 100.0, 6),
                "blocking_damage_percent": round(
                    blocking_damage / max(1, damage) * 100.0, 6
                ),
            }
        )
    job_rows.sort(key=lambda row: (-row["blocking_hits"], -row["blocking_damage"], row["job"]))

    known_specs = {int(row.projectile_template_id): row for row in _DAMAGE_OBJECT_SPECS}
    template_rows = []
    for template_id, row in templates.items():
        anchor_identities = list(row["packet_anchor_hits"])
        known = known_specs.get(template_id)
        candidate_status = "no_packet_anchor"
        anchor_hit_count = sum(int(value) for value in row["packet_anchor_hits"].values())
        if template_id in REJECTED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS:
            candidate_status = "rejected_by_corpus_provenance"
        elif len(anchor_identities) > 1:
            candidate_status = "conflicting_packet_anchors"
        elif len(anchor_identities) == 1:
            if known is not None:
                # ``skill_id`` is normally an integer, but the decoder keeps
                # synthetic packet-level identities such as
                # ``3224:basic_attack`` distinct from the ordinary 3224
                # skill.  Template validation only needs the numeric skill
                # family, so strip the optional identity suffix here instead
                # of aborting the whole-corpus report merge.
                anchor_skill_token = anchor_identities[0].split("|", 1)[0]
                anchor_skill_id = int(anchor_skill_token.split(":", 1)[0])
                candidate_status = (
                    "already_configured"
                    if anchor_skill_id in set(int(value) for value in known.skill_ids)
                    else "configured_mapping_or_registration_lifetime_conflict"
                )
            elif (
                anchor_hit_count >= 30
                and len(row["files"]) >= 3
                and len(row["actors"]) >= 2
                and row["registrations_with_visible_hits"] >= 5
                and row["blocker_hits"] > 0
            ):
                candidate_status = "safe_high_value_candidate"
            else:
                candidate_status = "unique_but_insufficient_independence"
        template_rows.append(
            {
                "projectile_template_id": template_id,
                "candidate_status": candidate_status,
                "known_skill_id": int(known.canonical_skill_id) if known else "",
                "known_key": str(known.key) if known else "",
                "file_count": len(row["files"]),
                "actor_count": len(row["actors"]),
                "registrations_with_visible_hits": row["registrations_with_visible_hits"],
                "direct37_hits": row["direct37_hits"],
                "direct37_damage": row["direct37_damage"],
                "packet_anchor_hits": _counter_dict(row["packet_anchor_hits"]),
                "packet_anchor_damage": _counter_dict(row["packet_anchor_damage"]),
                "blocker_hits": row["blocker_hits"],
                "blocker_damage": row["blocker_damage"],
                "blocker_methods": _counter_dict(row["blocker_methods"]),
                "blocker_jobs": _counter_dict(row["blocker_jobs"]),
                "blocker_current_skills": _counter_dict(row["blocker_current_skills"]),
                "files": sorted(row["files"]),
                "actors": sorted(row["actors"]),
            }
        )
    template_rows.sort(
        key=lambda row: (-row["blocker_hits"], -row["direct37_hits"], row["projectile_template_id"])
    )

    total_hits = totals["total_outgoing_hits"]
    blocking_hits = totals["blocking_hits"]
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_signature": analysis_signature,
        "policy": {
            "scope": (
                "all positive outgoing normal-skill damage in the 201-capture corpus; "
                "only explicit supplemental:/periodic: buckets and direct_hit_overflow "
                "remain outside the direct-skill release gate"
            ),
            "deterministic_only": True,
            "excluded_as_proof": [
                "nearest action in time",
                "result-group inheritance",
                "same-packet skill inheritance",
                "learned cast profile",
                "damage fingerprint/profile",
                "static ACT/resource-name similarity without a numeric packet edge",
                "candidate skill integer outside a fixed validated field",
            ],
        },
        "captures": len(reports),
        **{key: int(value) for key, value in totals.items()},
        "coverage_percent": round(
            (total_hits - blocking_hits) / max(1, total_hits) * 100.0, 6
        ),
        **{key: _counter_dict(value) for key, value in counters.items()},
        "result_group_windows": {
            key: _counter_dict(value) for key, value in window_counters.items()
        },
        "blocker_skills": skill_rows,
        "jobs": job_rows,
        "current_skill_buckets": current_bucket_rows,
        "projectile_templates": template_rows,
        "result_group_conflicts": result_group_conflicts[:100],
        "object_conflicts": object_conflicts[:100],
        "capture_reports": sorted(reports, key=lambda row: row["capture"]),
    }


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 201 份抓包直接伤害确定性归因审计",
        "",
        f"- 抓包：{result['captures']}",
        f"- 可见玩家输出 Hit：{result['total_outgoing_hits']:,}",
        f"- 阻断 Hit：{result['blocking_hits']:,}",
        f"- 当前确定性覆盖：{result['coverage_percent']:.6f}%",
        f"- 阻断伤害：{result['blocking_damage']:,}",
        "",
        "## 阻断包族",
        "",
        "| 包族 | Hit | 伤害 |",
        "|---|---:|---:|",
    ]
    for family, hits in result["blocker_families"].items():
        lines.append(
            f"| {family} | {hits:,} | {result['blocker_family_damage'].get(family, 0):,} |"
        )
    lines.extend(
        [
            "",
            "## 阻断归因方法",
            "",
            "| 方法 | Hit | 伤害 |",
            "|---|---:|---:|",
        ]
    )
    for method, hits in result["blocker_methods"].items():
        lines.append(
            f"| {method} | {hits:,} | {result['blocker_method_damage'].get(method, 0):,} |"
        )
    lines.extend(
        [
            "",
            "## 最高阻断技能",
            "",
            "| 职业 | 技能 | ID | Hit | 伤害 | 文件数 | 包族 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in result["blocker_skills"][:50]:
        family_text = ", ".join(
            f"{key}:{value}" for key, value in row["families"].items()
        )
        lines.append(
            f"| {row['job']} | {row['skill_name']} | {row['skill_id']} | "
            f"{row['hits']:,} | {row['damage']:,} | {row['file_count']} | "
            f"{family_text} |"
        )
    lines.extend(
        [
            "",
            "## Result-group 窗口审计",
            "",
            "| 窗口 | 可唯一补齐 Hit | 冲突 Hit | 无锚点 Hit |",
            "|---:|---:|---:|---:|",
        ]
    )
    for window, row in result["result_group_windows"].items():
        lines.append(
            f"| {window}ms | {row.get('uniquely_recoverable_hits', 0):,} | {row.get('ambiguous_hits', 0):,} | {row.get('unanchored_hits', 0):,} |"
        )
    lines.extend(
        [
            "",
            "## Projectile 模板候选",
            "",
            "| 模板 | 状态 | 包内锚点 | 潜在补齐 Hit | 文件 | 角色 |",
            "|---:|---|---|---:|---:|---:|",
        ]
    )
    for row in result["projectile_templates"]:
        if row["blocker_hits"] <= 0 and row["candidate_status"] != "conflicting_packet_anchors":
            continue
        lines.append(
            f"| {row['projectile_template_id']} | {row['candidate_status']} | {row['packet_anchor_hits']} | {row['blocker_hits']:,} | {row['file_count']} | {row['actor_count']} |"
        )
    lines.extend(
        [
            "",
            "## 约束",
            "",
            "本报告不把时间最近邻、learned profile、伤害形状或载荷中的任意技能样整数视为确定性证据。模板只有在完整对象 ID 与包内固定技能字段闭环、且跨文件/角色无冲突时才列为安全候选。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=None,
        help=(
            "Persist each completed capture and resume only when the exact "
            "production-analysis signature still matches. Defaults to "
            "<output>/.checkpoints."
        ),
    )
    args = parser.parse_args()

    capture_root = args.capture_root.resolve()
    corpus = json.loads(args.corpus.resolve().read_text(encoding="utf-8"))
    source_names = [str(row["source"]) for row in corpus["files"]]
    if args.limit > 0:
        source_names = source_names[: args.limit]
    paths = [(capture_root / name, name) for name in source_names]
    missing = [str(path) for path, _name in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"missing corpus captures: {missing[:10]}")

    signature_before = _analysis_signature()
    output = args.output.resolve()
    checkpoint_dir = (
        args.checkpoint_dir.resolve()
        if args.checkpoint_dir is not None
        else output / ".checkpoints"
    )
    reports: list[dict[str, Any]] = []
    pending: list[tuple[Path, str]] = []
    for path, name in paths:
        checkpoint = _load_checkpoint(
            _checkpoint_path(checkpoint_dir, name),
            source_name=name,
            analysis_signature=signature_before,
        )
        if checkpoint is None:
            pending.append((path, name))
        else:
            reports.append(checkpoint)
    if reports:
        print(
            f"[resume] loaded={len(reports)} pending={len(pending)}",
            flush=True,
        )
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(_audit_one, str(path), name): name
            for path, name in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            name = futures[future]
            report = future.result()
            reports.append(report)
            _write_checkpoint(
                _checkpoint_path(checkpoint_dir, name),
                source_name=name,
                analysis_signature=signature_before,
                report=report,
            )
            print(
                f"[{len(reports)}/{len(paths)}] {name}: hits={report['total_outgoing_hits']} blockers={report['blocking_hits']}",
                flush=True,
            )

    signature_after = _analysis_signature()
    if signature_after != signature_before:
        raise SystemExit(
            "audited production inputs changed during replay; refusing to publish mixed results"
        )
    result = _merge(reports, analysis_signature=signature_before)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(_render_markdown(result), encoding="utf-8")

    skill_csv = []
    for row in result["blocker_skills"]:
        skill_csv.append(
            {
                **row,
                "methods": json.dumps(row["methods"], ensure_ascii=False),
                "families": json.dumps(row["families"], ensure_ascii=False),
            }
        )
    _write_csv(
        output / "blockers_by_skill.csv",
        skill_csv,
        [
            "job",
            "skill_id",
            "skill_name",
            "hits",
            "damage",
            "file_count",
            "methods",
            "families",
        ],
    )
    _write_csv(
        output / "blockers_by_job.csv",
        result["jobs"],
        [
            "job",
            "hits",
            "damage",
            "deterministic_hits",
            "blocking_hits",
            "blocking_damage",
            "blocking_hit_percent",
            "blocking_damage_percent",
        ],
    )
    current_bucket_csv = []
    for row in result["current_skill_buckets"]:
        current_bucket_csv.append(
            {
                **row,
                "methods": json.dumps(row["methods"], ensure_ascii=False),
                "families": json.dumps(row["families"], ensure_ascii=False),
            }
        )
    _write_csv(
        output / "current_skill_buckets.csv",
        current_bucket_csv,
        [
            "job",
            "skill_id",
            "skill_name",
            "hits",
            "damage",
            "blocking_hits",
            "blocking_damage",
            "blocking_hit_percent",
            "blocking_damage_percent",
            "file_count",
            "methods",
            "families",
        ],
    )
    template_csv = []
    for row in result["projectile_templates"]:
        template_csv.append(
            {
                **row,
                "packet_anchor_hits": json.dumps(row["packet_anchor_hits"], ensure_ascii=False),
                "packet_anchor_damage": json.dumps(row["packet_anchor_damage"], ensure_ascii=False),
                "blocker_methods": json.dumps(row["blocker_methods"], ensure_ascii=False),
                "blocker_jobs": json.dumps(row["blocker_jobs"], ensure_ascii=False),
                "blocker_current_skills": json.dumps(row["blocker_current_skills"], ensure_ascii=False),
                "files": " | ".join(row["files"]),
                "actors": " | ".join(row["actors"]),
            }
        )
    _write_csv(
        output / "projectile_template_packet_anchors.csv",
        template_csv,
        [
            "projectile_template_id",
            "candidate_status",
            "known_skill_id",
            "known_key",
            "file_count",
            "actor_count",
            "registrations_with_visible_hits",
            "direct37_hits",
            "direct37_damage",
            "packet_anchor_hits",
            "packet_anchor_damage",
            "blocker_hits",
            "blocker_damage",
            "blocker_methods",
            "blocker_jobs",
            "blocker_current_skills",
            "files",
            "actors",
        ],
    )
    print(
        f"[done] captures={result['captures']} hits={result['total_outgoing_hits']} blockers={result['blocking_hits']} coverage={result['coverage_percent']}%",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
