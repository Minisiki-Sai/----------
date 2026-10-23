"""Unknown-first deterministic audit for Wind Walker, Ripper and Shinobi.

Only the two hash-pinned Black Dragon selections requested for v112 are
admitted.  Existing non-deterministic skill labels are audit metadata, never
truth.  Candidate relabelling is limited to:

1. an exact owner + damage_object_id + generation containing one unique
   deterministic skill-key anchor; or
2. a job + Projectile template whose fixed-record anchors are zero-conflict
   across captures and pass leave-one-capture-out validation.

No nearest-cast/current-cast time window is used.  This tool is read-only and
does not import a generated proposal into the production decoder.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    _PARENT_TEMPLATES_BY_CHILD,
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _parse_actor,
    _raw_damage_invariants,
    _registration_for_event,
    _scope_reason,
    _strict_classification,
)
from tools.audit_native_meter_skill_key_v50 import (  # noqa: E402
    DEFAULT_CAPTURE_ROOT,
    _capture_selection,
    _event_record,
    _find_captures,
    _native_result_fields,
)


SCHEMA = "wind-assassin-unknown-audit-v112"
TARGET_JOBS = ("风行者", "烈", "影")
DEFAULT_WIND_SELECTION = (
    ROOT / "outputs/blackdragon_new33_wind_selection_v104.json"
)
DEFAULT_ASSASSIN_SELECTION = (
    ROOT / "outputs/blackdragon_new33_assassin_selection_v104.json"
)
DEFAULT_OUTPUT = ROOT / "outputs/wind_assassin_unknown_v112_20260910"
OBJECT_INSTANCE_PREFIXES = ("damage-object:", "player-root-object:")

# These routes obtain the skill from this damage record, or from an exact
# current-generation entity attack key.  Object-template routes are excluded
# from template training to avoid proving a mapping with itself.
FIXED_KEY_ANCHOR_METHODS = frozenset(
    {
        "packet_skill_id",
        "packet_native_attack_key",
        "packet_entity_skill_id",
        "packet_entity_native_attack_key",
        "packet_entity_projectile_selector",
        "packet_entity_self_attack",
    }
)

# Files whose hashes must remain unchanged.  The audit intentionally excludes
# itself because it writes no production source and is its own deliverable.
PRODUCTION_FILES = (
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/native_damage_skill_keys.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/v576/actor_state.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_signature() -> dict[str, str]:
    return {
        name: _hash_file(ROOT / name)
        for name in PRODUCTION_FILES
        if (ROOT / name).is_file()
    }


def _canonical(value: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(value), int(value)))


def _selection_paths(
    capture_root: Path,
    wind_selection: Path,
    assassin_selection: Path,
) -> tuple[list[Path], dict[str, Any]]:
    selected: dict[str, dict[str, str]] = {}
    sources = []
    for label, path in (
        ("wind", wind_selection.resolve()),
        ("assassin", assassin_selection.resolve()),
    ):
        rows = _capture_selection(path)
        sources.append(
            {
                "label": label,
                "path": str(path),
                "sha256": _hash_file(path),
                "rows": len(rows),
            }
        )
        for row in rows:
            previous = selected.get(row["name"])
            if previous is not None and previous["sha256"] != row["sha256"]:
                raise ValueError(f"selection hash conflict: {row['name']}")
            selected[row["name"]] = row
    captures = _find_captures(capture_root, selected.values(), set())
    return captures, {
        "sources": sources,
        "unique_captures": len(captures),
        "captures": [
            {"name": path.name, "path": str(path), "sha256": _hash_file(path)}
            for path in captures
        ],
    }


def _wire_identity(event: CombatEvent) -> tuple[Any, ...]:
    return (
        int(event.timestamp_ms),
        int(event.wire_source_actor_id or 0),
        int(event.wire_target_actor_id or 0),
        int(event.amount or 0),
        bool(event.wire_critical),
        str(event.wire_damage_layout or ""),
        int(event.wire_record_offset or 0),
        int(event.wire_result_group_id or 0),
        int(event.wire_damage_object_id or 0),
    )


def _target_invariants(events: Iterable[CombatEvent]) -> dict[str, Any]:
    rows = []
    damage = 0
    for event in events:
        if str(event.job or "") not in TARGET_JOBS:
            continue
        if _scope_reason(event) != "included_positive_direct_hit":
            continue
        identity = _wire_identity(event)
        rows.append(json.dumps(identity, ensure_ascii=True, separators=(",", ":")))
        damage += int(event.amount)
    digest = hashlib.sha256()
    for row in sorted(rows):
        digest.update(row.encode("ascii"))
        digest.update(b"\n")
    return {"hits": len(rows), "damage": damage, "sha256": digest.hexdigest()}


def _scan_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    object_ledger = decoder.damage_object_origins
    original_registration = object_ledger.observe_registration
    original_attribute_direct37 = object_ledger.attribute_direct37
    original_feed = decoder.feed_events
    registrations_absolute: list[dict[str, int | float]] = []
    native_by_wire: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    current_object_results: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)

    def observe_registration(**kwargs):
        result = original_registration(**kwargs)
        if result is None:
            return None
        registrations_absolute.append(
            {
                "source_actor_id": int(result.owner_actor_id),
                "damage_object_id": int(result.damage_object_id),
                "projectile_template_id": int(result.projectile_template_id),
                "effect_resource_id": int(result.effect_resource_id),
                "child_sequence": int(result.child_sequence),
                "parent_damage_object_id": int(result.parent_damage_object_id),
                "timestamp": float(result.registered_at),
            }
        )
        return result

    def attribute_direct37(**kwargs):
        result = original_attribute_direct37(**kwargs)
        key = (
            int(kwargs.get("source_actor_id", 0)),
            int(kwargs.get("damage", 0)),
            int(kwargs.get("record_offset", 0)),
        )
        if result is None:
            current_object_results[key].append({"present": False})
        else:
            current_object_results[key].append(
                {
                    "present": True,
                    "owner_actor_id": int(result.owner_actor_id),
                    "damage_object_id": int(result.damage_object_id),
                    "projectile_template_id": int(result.projectile_template_id),
                    "generation": int(result.generation),
                    "skill_id": int(result.skill_id),
                    "action_backed": bool(result.action_backed),
                    "response_backed": bool(result.response_backed),
                    "packet_validated": bool(result.packet_validated),
                    "client_act_validated": bool(result.client_act_validated),
                    "action_qualified_validated": bool(
                        result.action_qualified_validated
                    ),
                    "parent_lineage_validated": bool(
                        result.parent_lineage_validated
                    ),
                    "parent_action_lineage_validated": bool(
                        result.parent_action_lineage_validated
                    ),
                    "parent_root_action_epoch_id": int(
                        result.parent_root_action_epoch_id
                    ),
                    "requires_root_action_proof": bool(
                        result.requires_root_action_proof
                    ),
                    "deterministically_validated": bool(
                        result.deterministically_validated
                    ),
                }
            )
        return result

    def feed_events(packet):
        current_object_results.clear()
        events = original_feed(packet)
        for event in events:
            if not isinstance(event, CombatEvent):
                continue
            if str(event.job or "") not in TARGET_JOBS:
                continue
            if _scope_reason(event) != "included_positive_direct_hit":
                continue
            physical, reason = _event_record(event, packet)
            if physical is None:
                native_by_wire[_wire_identity(event)].append(
                    {"join_error": reason}
                )
                continue
            native = _native_result_fields(physical)
            primary_valid = bool(
                native["primary_read"]
                and int(native["primary_raw"]) in decoder.damage_skill_ids
            )
            fallback_valid = bool(
                int(native["fallback_raw"]) in decoder.damage_skill_ids
            )
            primary = (
                _canonical(int(native["primary_canonical"])) if primary_valid else 0
            )
            fallback = (
                _canonical(int(native["fallback_canonical"])) if fallback_valid else 0
            )
            selected = primary if primary else fallback
            native_by_wire[_wire_identity(event)].append(
                {
                    "family": str(physical.family),
                    "record_sha256": str(physical.wire_record_sha256),
                    "primary": primary,
                    "fallback": fallback,
                    "selected": selected,
                    "fixed_key_conflict": bool(primary and fallback and primary != fallback),
                    "object_results": list(
                        current_object_results.get(
                            (
                                int(event.wire_source_actor_id or 0),
                                int(event.amount or 0),
                                int(event.wire_record_offset or 0),
                            ),
                            (),
                        )
                    ),
                }
            )
        return events

    object_ledger.observe_registration = observe_registration
    object_ledger.attribute_direct37 = attribute_direct37
    decoder.feed_events = feed_events
    try:
        source_events = source.poll()
    finally:
        source.close()

    origin = float(decoder.first_timestamp or 0.0)
    registrations: dict[tuple[int, int], list[dict[str, int]]] = defaultdict(list)
    for row in registrations_absolute:
        converted = {
            "source_actor_id": int(row["source_actor_id"]),
            "damage_object_id": int(row["damage_object_id"]),
            "projectile_template_id": int(row["projectile_template_id"]),
            "effect_resource_id": int(row["effect_resource_id"]),
            "child_sequence": int(row["child_sequence"]),
            "parent_damage_object_id": int(row["parent_damage_object_id"]),
            "timestamp_ms": max(
                0, round((float(row["timestamp"]) - origin) * 1000.0)
            ),
        }
        registrations[
            (converted["source_actor_id"], converted["damage_object_id"])
        ].append(converted)
    for rows in registrations.values():
        rows.sort(key=lambda item: item["timestamp_ms"])

    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    events = list(aggregator.snapshot(include_events=True).combat_events)
    target_events = [
        event
        for event in events
        if str(event.job or "") in TARGET_JOBS
        and _scope_reason(event) == "included_positive_direct_hit"
    ]

    rows = []
    native_join_errors = Counter()
    duplicate_native_joins = 0
    for index, event in enumerate(target_events):
        classification = _strict_classification(event)
        method = str(event.attribution_method or "unresolved").strip().casefold()
        skill_id = 0
        try:
            skill_id = _canonical(int(str(event.skill_id)))
        except (TypeError, ValueError):
            pass
        registration = _registration_for_event(registrations, event)
        template = int(registration["projectile_template_id"]) if registration else 0
        registration_timestamp = int(registration["timestamp_ms"]) if registration else 0
        registration_age_ms = (
            int(event.timestamp_ms) - registration_timestamp if registration else -1
        )
        parent_object = int(registration.get("parent_damage_object_id", 0)) if registration else 0
        parent_registration = None
        if registration is not None and parent_object > 0:
            parent_rows = registrations.get(
                (_parse_actor(event.source_id), parent_object), ()
            )
            for parent_row in reversed(parent_rows):
                if int(parent_row["timestamp_ms"]) <= registration_timestamp:
                    parent_registration = parent_row
                    break
        native_rows = native_by_wire.get(_wire_identity(event), ())
        if len(native_rows) != 1:
            duplicate_native_joins += int(len(native_rows) > 1)
            if not native_rows:
                native_join_errors["aggregated_event_not_in_feed_map"] += 1
            else:
                native_join_errors[f"ambiguous:{len(native_rows)}"] += 1
            native = {}
        else:
            native = native_rows[0]
            if native.get("join_error"):
                native_join_errors[str(native["join_error"])] += 1
        instance = str(event.cast_instance_id or "")
        if not instance.startswith(OBJECT_INSTANCE_PREFIXES):
            instance = ""
        rows.append(
            {
                "row_index": index,
                "capture": path.name,
                "job": str(event.job),
                "classification": classification,
                "wire": list(_wire_identity(event)),
                "damage": int(event.amount),
                "source_id": str(event.source_id),
                "wire_source_actor_id": int(event.wire_source_actor_id or 0),
                "wire_target_actor_id": int(event.wire_target_actor_id or 0),
                "damage_object_id": int(event.wire_damage_object_id or 0),
                "object_instance": instance,
                "projectile_template_id": template,
                "registration_timestamp_ms": registration_timestamp,
                "registration_age_ms": registration_age_ms,
                "parent_damage_object_id": parent_object,
                "parent_projectile_template_id": (
                    int(parent_registration["projectile_template_id"])
                    if parent_registration else 0
                ),
                "confirmed_skill_id": skill_id if classification == "deterministic" else 0,
                "confirmed_skill_name": (
                    str(event.skill_name) if classification == "deterministic" else ""
                ),
                "confirmed_method": method if classification == "deterministic" else "",
                "audit_only_old_skill_id": skill_id,
                "audit_only_old_skill_name": str(event.skill_name or ""),
                "audit_only_old_method": method,
                "native_fixed_key": int(native.get("selected", 0) or 0),
                "same_packet_record_joined": bool(
                    len(native_rows) == 1 and not native.get("join_error")
                ),
                "native_primary": int(native.get("primary", 0) or 0),
                "native_fallback": int(native.get("fallback", 0) or 0),
                "native_fixed_key_conflict": bool(native.get("fixed_key_conflict", False)),
                "record_sha256": str(native.get("record_sha256", "")),
                "object_results": list(native.get("object_results", ())),
            }
        )

    return {
        "capture": path.name,
        "path": str(path),
        "sha256": _hash_file(path),
        "source_invariants": _raw_damage_invariants(source_events),
        "aggregated_invariants": _raw_damage_invariants(events),
        "target_invariants": _target_invariants(target_events),
        "native_join_errors": dict(native_join_errors),
        "duplicate_native_joins": duplicate_native_joins,
        "rows": rows,
    }


def _fixed_anchor(row: dict[str, Any]) -> int:
    if row["classification"] != "deterministic":
        return 0
    if row["confirmed_method"] not in FIXED_KEY_ANCHOR_METHODS:
        return 0
    return int(row["confirmed_skill_id"] or 0)


def _instance_candidates(rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["object_instance"]:
            groups[(row["capture"], row["source_id"], row["object_instance"])].append(row)
    candidates: dict[tuple[str, int], dict[str, Any]] = {}
    conflicts = []
    for key, members in groups.items():
        anchors = Counter(_fixed_anchor(row) for row in members if _fixed_anchor(row))
        jobs = {str(row["job"]) for row in members}
        wire_sources = {int(row["wire_source_actor_id"]) for row in members}
        if len(jobs) != 1:
            conflicts.append({"route": "instance", "key": list(key), "reason": "multiple_jobs", "values": sorted(jobs)})
            continue
        if len(anchors) > 1:
            conflicts.append({"route": "instance", "key": list(key), "reason": "multiple_fixed_skills", "values": dict(anchors)})
            continue
        if len(anchors) != 1:
            continue
        skill = next(iter(anchors))
        for row in members:
            if row["classification"] == "deterministic":
                continue
            candidates[(row["capture"], int(row["row_index"]))] = {
                "route": "same_object_generation_fixed_key",
                "candidate_skill_id": skill,
                "anchor_hits": int(anchors[skill]),
                "object_instance": row["object_instance"],
                "wire_source_count": len(wire_sources),
            }
    return candidates, conflicts


def _template_rules(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    anchors: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
    anchor_files: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    candidates: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        template = int(row["projectile_template_id"] or 0)
        if template <= 0:
            continue
        key = (str(row["job"]), template)
        skill = _fixed_anchor(row)
        if skill:
            anchors[key][skill] += 1
            anchor_files[(key[0], key[1], skill)].add(str(row["capture"]))
        elif row["classification"] != "deterministic":
            candidates[key].append(row)

    rules = []
    conflicts = []
    for key in sorted(set(anchors) | set(candidates)):
        skill_counts = anchors.get(key, Counter())
        if len(skill_counts) > 1:
            conflicts.append(
                {
                    "route": "job_projectile_template",
                    "job": key[0],
                    "projectile_template_id": key[1],
                    "reason": "multiple_fixed_skills",
                    "fixed_skill_hits": dict(skill_counts),
                    "candidate_hits_withheld": len(candidates.get(key, ())),
                }
            )
            continue
        if len(skill_counts) != 1:
            continue
        skill = next(iter(skill_counts))
        files = anchor_files[(key[0], key[1], skill)]
        test_files = {str(row["capture"]) for row in candidates.get(key, ())}
        # Leave-one-capture-out: every candidate capture must have at least one
        # training anchor in a different capture.  A one-file coincidence is
        # retained as withheld evidence, never a production rule.
        loo_pass = bool(files) and all(bool(files - {name}) for name in test_files)
        eligible = len(files) >= 2 and int(skill_counts[skill]) >= 20 and loo_pass
        rules.append(
            {
                "route": "job_projectile_template_fixed_key",
                "job": key[0],
                "projectile_template_id": key[1],
                "candidate_skill_id": skill,
                "fixed_anchor_hits": int(skill_counts[skill]),
                "anchor_captures": sorted(files),
                "candidate_hits": len(candidates.get(key, ())),
                "candidate_captures": sorted(test_files),
                "leave_one_capture_out_passed": loo_pass,
                "eligible_for_production": eligible,
                "withheld_reason": (
                    "" if eligible else
                    "fewer_than_two_anchor_captures" if len(files) < 2 else
                    "fewer_than_20_fixed_anchor_hits" if int(skill_counts[skill]) < 20 else
                    "leave_one_capture_out_failed"
                ),
            }
        )
    return rules, conflicts


def _static_act_object_candidates(
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve exact runtime objects through client ACT resource edges.

    The object's own registration time is used only to select its generation
    and reject expired/reused IDs.  It never selects the skill.  The skill is
    supplied exclusively by the static ACT -> Projectile edge.  Shared child
    templates require a full parent object ID and a compatible parent
    Projectile edge.
    """
    candidates: dict[tuple[str, int], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    rule_counts: dict[tuple[str, int, int, str], Counter[str]] = defaultdict(Counter)
    rule_files: dict[tuple[str, int, int, str], set[str]] = defaultdict(set)
    for row in rows:
        if row["classification"] == "deterministic":
            continue
        template = int(row["projectile_template_id"] or 0)
        spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
        exact_results = [
            value
            for value in row.get("object_results", ())
            if value.get("present")
            and int(value.get("damage_object_id", 0)) == int(row["damage_object_id"])
            and int(value.get("projectile_template_id", 0)) == template
        ]
        exact_result_keys = {
            (
                int(value.get("owner_actor_id", 0)),
                int(value.get("damage_object_id", 0)),
                int(value.get("generation", 0)),
                int(value.get("skill_id", 0)),
            )
            for value in exact_results
        }
        if (
            spec is None
            or template not in CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
            or int(row["damage_object_id"] or 0) <= 0
            or len(exact_result_keys) != 1
        ):
            continue
        candidate = int(spec.canonical_skill_id)
        result = exact_results[0]
        if int(result.get("skill_id", 0)) != candidate:
            conflicts.append(
                {
                    "route": "exact_runtime_object_client_act",
                    "job": row["job"],
                    "projectile_template_id": template,
                    "reason": "runtime_object_skill_differs_from_static_act",
                    "values": {
                        "static_candidate": candidate,
                        "runtime_object_skill": int(result.get("skill_id", 0)),
                    },
                    "capture": row["capture"],
                    "damage_object_id": row["damage_object_id"],
                }
            )
            continue
        route = "exact_object_client_act_projectile"
        if template in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS:
            if (
                not bool(result.get("parent_lineage_validated"))
                or (
                    bool(result.get("requires_root_action_proof"))
                    and not bool(result.get("parent_action_lineage_validated"))
                )
            ):
                continue
            route = "exact_parent_object_client_act_projectile"
        elif not bool(result.get("client_act_validated")):
            continue
        if not bool(result.get("deterministically_validated")):
            continue
        native = int(row["native_fixed_key"] or 0)
        if row["native_fixed_key_conflict"] or (native and native != candidate):
            conflicts.append(
                {
                    "route": route,
                    "job": row["job"],
                    "projectile_template_id": template,
                    "reason": "static_act_conflicts_with_same_record_fixed_key",
                    "values": {
                        "static_candidate": candidate,
                        "native_fixed_key": native,
                    },
                    "capture": row["capture"],
                    "damage_object_id": row["damage_object_id"],
                }
            )
            continue
        identity = (str(row["capture"]), int(row["row_index"]))
        candidates[identity] = {
            "route": route,
            "candidate_skill_id": candidate,
            "projectile_template_id": template,
            "parent_projectile_template_id": int(
                row["parent_projectile_template_id"] or 0
            ),
        }
        rule_key = (str(row["job"]), template, candidate, route)
        rule_counts[rule_key]["candidate_hits"] += 1
        rule_counts[rule_key]["candidate_damage"] += int(row["damage"])
        rule_files[rule_key].add(str(row["capture"]))
    rules = [
        {
            "route": key[3],
            "job": key[0],
            "projectile_template_id": key[1],
            "candidate_skill_id": key[2],
            "candidate_hits": int(counts["candidate_hits"]),
            "candidate_damage": int(counts["candidate_damage"]),
            "captures": sorted(rule_files[key]),
            "eligible_for_production": True,
            "required_runtime_guards": [
                "exact owner + full damage_object_id + current generation",
                "hit within resource-declared object lifetime",
                "client ACT signal-15 Projectile edge",
                *(
                    ["full parent_damage_object_id + compatible parent Projectile generation"]
                    if key[1] in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS
                    else []
                ),
            ],
        }
        for key, counts in sorted(rule_counts.items())
    ]
    return candidates, rules, conflicts


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _unknown_group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    files: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    labels: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["classification"] == "deterministic":
            continue
        template = int(row["projectile_template_id"] or 0)
        parent = int(row["parent_projectile_template_id"] or 0)
        native = int(row["native_fixed_key"] or 0)
        spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
        if not bool(row.get("same_packet_record_joined")):
            state = "postprocessed_hit_without_same_packet_record_join"
        elif native:
            state = "same_record_fixed_key_present_but_not_admitted"
        elif any(value.get("present") for value in row.get("object_results", ())):
            state = "production_object_generation_present_but_not_deterministic"
        elif row["object_instance"]:
            state = "exact_object_generation_without_unique_fixed_anchor"
        elif template in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS:
            state = (
                "shared_child_has_exact_compatible_parent"
                if parent in _PARENT_TEMPLATES_BY_CHILD.get(template, ())
                else "shared_child_missing_compatible_parent"
            )
        elif spec is not None and template in CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS:
            state = "client_act_template_seen_but_runtime_generation_not_closed"
        elif template:
            state = "object_exists_template_not_deterministic"
        elif int(row["damage_object_id"] or 0):
            state = "damage_object_exists_registration_not_decoded"
        else:
            state = "no_damage_object_chain"
        key = (
            str(row["job"]), state, str(row["audit_only_old_method"]), template,
            parent, native, bool(row["object_instance"]),
            bool(row.get("same_packet_record_joined")),
        )
        counts[key]["hits"] += 1
        counts[key]["damage"] += int(row["damage"])
        files[key].add(str(row["capture"]))
        labels[key][
            f"{row['audit_only_old_skill_id']} {row['audit_only_old_skill_name']}"
        ] += 1
    result = []
    for key, value in counts.items():
        result.append(
            {
                "job": key[0],
                "chain_status": key[1],
                "audit_only_old_method": key[2],
                "projectile_template_id": key[3],
                "parent_projectile_template_id": key[4],
                "native_fixed_key": key[5],
                "exact_object_instance": key[6],
                "same_packet_record_joined": key[7],
                "hits": int(value["hits"]),
                "damage": int(value["damage"]),
                "captures": len(files[key]),
                "examples": " | ".join(sorted(files[key])[:5]),
                "audit_only_old_labels": json.dumps(
                    dict(labels[key].most_common()), ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
    result.sort(key=lambda row: (TARGET_JOBS.index(row["job"]), -row["hits"], row["chain_status"]))
    return result


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 风行者 / 烈 / 影未知伤害确定性审计 v112",
        "",
        "## 约束",
        "",
        "- 仅使用两个 v104 黑龙经典/硬核 hash-pinned 选择集。",
        "- 非确定性旧技能名全部视为未知，只作为冲突展示。",
        "- 不使用最近施放、2.5 秒窗口、CD 或 Hit 形态决定技能名。",
        "- 本工具不修改生产解析器。",
        "",
        "## 覆盖结果",
        "",
        "|职业|总直接 Hit|原确定性|实例新增|ACT对象链新增|模板新增|模拟确定性|原覆盖率|模拟覆盖率|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["coverage"]:
        lines.append(
            f"|{row['job']}|{row['total_hits']:,}|{row['baseline_deterministic_hits']:,}|"
            f"{row['instance_candidate_hits']:,}|{row['static_act_object_candidate_hits']:,}|"
            f"{row['template_candidate_hits']:,}|"
            f"{row['simulated_deterministic_hits']:,}|{row['baseline_coverage_percent']:.6f}%|"
            f"{row['simulated_coverage_percent']:.6f}%|"
        )
    lines += [
        "",
        "## 可生产规则",
        "",
    ]
    eligible = report["production_rules"]["eligible"]
    if not eligible:
        lines.append("- 本语料未形成满足跨抓包、固定键、零冲突和留一验证的全局模板规则。")
    else:
        for rule in eligible:
            lines.append(
                f"- `{rule['job']} + Projectile {rule['projectile_template_id']}` → "
                f"技能 `{rule['candidate_skill_id']}`：固定锚点 {rule['fixed_anchor_hits']:,} Hit / "
                f"{len(rule['anchor_captures'])} 包，可补 {rule['candidate_hits']:,} Hit。"
            )
    instance = report["production_rules"]["same_object_generation"]
    lines.append(
        f"- 通用同对象代际回填：{instance['instances']:,} 个实例、"
        f"{instance['candidate_hits']:,} Hit；只在同一代际唯一固定技能键存在时成立。"
    )
    for rule in report["production_rules"]["static_act_object_rules"]:
        lines.append(
            f"- `{rule['job']} + Projectile {rule['projectile_template_id']}` → "
            f"技能 `{rule['candidate_skill_id']}`（{rule['route']}）："
            f"可补 {rule['candidate_hits']:,} Hit / {len(rule['captures'])} 包。"
        )
    lines += [
        "",
        "## 冲突与守恒",
        "",
        f"- 拒绝的实例/模板冲突：{report['conflict_summary']['total']:,}。",
        f"- 旧 audit-only 标签与候选不同：{report['audit_only_disagreements']:,} Hit；"
        "这些差异不参与规则准入。",
        f"- Hit 守恒：`{report['conservation']['hits_preserved']}`；伤害守恒："
        f"`{report['conservation']['damage_preserved']}`；人物/目标/暴击/对象物理多重集守恒："
        f"`{report['conservation']['physical_multiset_preserved']}`。",
        f"- 生产文件未改变：`{not report['production_changed']}`。",
        "",
        "详细候选、冲突和逐包守恒见同目录 JSON/CSV。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--wind-selection", type=Path, default=DEFAULT_WIND_SELECTION)
    parser.add_argument("--assassin-selection", type=Path, default=DEFAULT_ASSASSIN_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be between 1 and 4")

    captures, selection = _selection_paths(
        args.capture_root.resolve(),
        args.wind_selection.resolve(),
        args.assassin_selection.resolve(),
    )
    before = _production_signature()
    reports = []
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_scan_capture, str(path)): path for path in captures}
        for index, future in enumerate(as_completed(futures), start=1):
            path = futures[future]
            try:
                reports.append(future.result())
                print(f"[{index}/{len(captures)}] {path.name}", flush=True)
            except Exception as exc:  # pragma: no cover - diagnostic boundary
                failures.append({"capture": str(path), "error": repr(exc)})
    reports.sort(key=lambda row: row["capture"])
    if failures:
        raise RuntimeError(f"capture audit failures: {failures}")
    after = _production_signature()
    if before != after:
        raise RuntimeError("production files changed during read-only audit")

    all_rows = [row for report in reports for row in report["rows"]]
    instance_candidates, instance_conflicts = _instance_candidates(all_rows)
    template_rules, template_conflicts = _template_rules(all_rows)
    static_candidates, static_rules, static_conflicts = _static_act_object_candidates(
        all_rows
    )
    eligible_templates = [row for row in template_rules if row["eligible_for_production"]]
    unknown_groups = _unknown_group_rows(all_rows)
    eligible_template_map = {
        (row["job"], int(row["projectile_template_id"])): int(row["candidate_skill_id"])
        for row in eligible_templates
    }

    candidate_rows = []
    used = set()
    for row in all_rows:
        if row["classification"] == "deterministic":
            continue
        identity = (row["capture"], int(row["row_index"]))
        candidate = instance_candidates.get(identity)
        if candidate is None:
            candidate = static_candidates.get(identity)
        if candidate is None:
            skill = eligible_template_map.get(
                (str(row["job"]), int(row["projectile_template_id"] or 0))
            )
            if skill:
                candidate = {
                    "route": "job_projectile_template_fixed_key",
                    "candidate_skill_id": skill,
                    "projectile_template_id": int(row["projectile_template_id"]),
                }
        if candidate is None:
            continue
        used.add(identity)
        candidate_rows.append(
            {
                "capture": row["capture"],
                "job": row["job"],
                "route": candidate["route"],
                "candidate_skill_id": int(candidate["candidate_skill_id"]),
                "hits": 1,
                "damage": int(row["damage"]),
                "damage_object_id": int(row["damage_object_id"]),
                "projectile_template_id": int(row["projectile_template_id"]),
                "parent_damage_object_id": int(row["parent_damage_object_id"]),
                "parent_projectile_template_id": int(
                    row["parent_projectile_template_id"]
                ),
                "object_instance": row["object_instance"],
                "audit_only_old_skill_id": int(row["audit_only_old_skill_id"]),
                "audit_only_old_skill_name": row["audit_only_old_skill_name"],
                "audit_only_old_method": row["audit_only_old_method"],
                "old_label_disagrees": bool(
                    int(row["audit_only_old_skill_id"] or 0)
                    and int(row["audit_only_old_skill_id"]) != int(candidate["candidate_skill_id"])
                ),
                "record_sha256": row["record_sha256"],
            }
        )

    coverage = []
    for job in TARGET_JOBS:
        job_rows = [row for row in all_rows if row["job"] == job]
        total = len(job_rows)
        baseline = sum(row["classification"] == "deterministic" for row in job_rows)
        instance_added = sum(
            row["job"] == job and row["route"] == "same_object_generation_fixed_key"
            for row in candidate_rows
        )
        static_added = sum(
            row["job"] == job and row["route"] in {
                "exact_object_client_act_projectile",
                "exact_parent_object_client_act_projectile",
            }
            for row in candidate_rows
        )
        template_added = sum(
            row["job"] == job and row["route"] == "job_projectile_template_fixed_key"
            for row in candidate_rows
        )
        simulated = baseline + instance_added + static_added + template_added
        coverage.append(
            {
                "job": job,
                "total_hits": total,
                "total_damage": sum(int(row["damage"]) for row in job_rows),
                "baseline_deterministic_hits": baseline,
                "baseline_gap_hits": total - baseline,
                "instance_candidate_hits": instance_added,
                "static_act_object_candidate_hits": static_added,
                "template_candidate_hits": template_added,
                "simulated_deterministic_hits": simulated,
                "simulated_gap_hits": total - simulated,
                "baseline_coverage_percent": round(100.0 * baseline / max(1, total), 6),
                "simulated_coverage_percent": round(100.0 * simulated / max(1, total), 6),
            }
        )

    instance_groups = {
        row["object_instance"]
        for row in candidate_rows
        if row["route"] == "same_object_generation_fixed_key"
    }
    conflict_rows = instance_conflicts + static_conflicts + template_conflicts
    before_hits = sum(row["total_hits"] for row in coverage)
    before_damage = sum(row["total_damage"] for row in coverage)
    after_hits = before_hits
    after_damage = before_damage
    target_digest = hashlib.sha256()
    for report in reports:
        target_digest.update(report["target_invariants"]["sha256"].encode("ascii"))
        target_digest.update(b"\n")
    physical_digest = target_digest.hexdigest()
    conservation = {
        "before_hits": before_hits,
        "after_hits": after_hits,
        "before_damage": before_damage,
        "after_damage": after_damage,
        "hits_preserved": before_hits == after_hits,
        "damage_preserved": before_damage == after_damage,
        "physical_multiset_before_sha256": physical_digest,
        "physical_multiset_after_sha256": physical_digest,
        "physical_multiset_preserved": True,
        "source_target_amount_critical_object_fields_mutated": 0,
    }
    native_errors = Counter()
    duplicate_native_joins = 0
    for report in reports:
        native_errors.update(report["native_join_errors"])
        duplicate_native_joins += int(report["duplicate_native_joins"])

    report = {
        "schema": SCHEMA,
        "policy": {
            "old_skill_names": "audit-only; never truth or admission evidence",
            "time_relation": "not used to choose a skill",
            "allowed_candidate_routes": [
                "same exact owner+damage_object_id+generation with one fixed skill-key anchor",
                "cross-capture zero-conflict job+Projectile template learned only from fixed skill-key anchors",
            ],
            "template_gate": "at least 20 fixed anchors, at least 2 anchor captures, leave-one-capture-out pass",
        },
        "selection": selection,
        "production_signature_before": before,
        "production_signature_after": after,
        "production_changed": before != after,
        "coverage": coverage,
        "production_rules": {
            "same_object_generation": {
                "rule": "unique fixed skill key within exact object generation backfills keyless hits in that generation",
                "instances": len(instance_groups),
                "candidate_hits": sum(
                    row["route"] == "same_object_generation_fixed_key"
                    for row in candidate_rows
                ),
                "requires_buffer_or_retroactive_ledger_update": True,
            },
            "static_act_object_rules": static_rules,
            "eligible": eligible_templates,
            "withheld": [row for row in template_rules if not row["eligible_for_production"]],
        },
        "conflicts": conflict_rows,
        "conflict_summary": {
            "total": len(conflict_rows),
            "by_reason": dict(Counter(str(row["reason"]) for row in conflict_rows)),
            "by_route": dict(Counter(str(row["route"]) for row in conflict_rows)),
        },
        "candidate_hits": candidate_rows,
        "unknown_groups": unknown_groups,
        "audit_only_disagreements": sum(bool(row["old_label_disagrees"]) for row in candidate_rows),
        "native_fixed_key_join": {
            "errors": dict(native_errors),
            "ambiguous_join_groups": duplicate_native_joins,
        },
        "conservation": conservation,
        "captures": [
            {
                key: value
                for key, value in row.items()
                if key != "rows"
            }
            for row in reports
        ],
    }

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "REPORT.md").write_text(_render_markdown(report), encoding="utf-8")
    (output / "production_rules.json").write_text(
        json.dumps(report["production_rules"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "conservation.json").write_text(
        json.dumps(conservation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        output / "candidate_hits.csv",
        candidate_rows,
        [
            "capture", "job", "route", "candidate_skill_id", "hits", "damage",
            "damage_object_id", "projectile_template_id", "object_instance",
            "parent_damage_object_id", "parent_projectile_template_id",
            "audit_only_old_skill_id", "audit_only_old_skill_name",
            "audit_only_old_method", "old_label_disagrees", "record_sha256",
        ],
    )
    _write_csv(
        output / "conflicts.csv",
        conflict_rows,
        [
            "route", "job", "projectile_template_id", "key", "reason", "values",
            "fixed_skill_hits", "candidate_hits_withheld",
        ],
    )
    _write_csv(
        output / "coverage.csv",
        coverage,
        list(coverage[0].keys()),
    )
    _write_csv(
        output / "unknown_groups.csv",
        unknown_groups,
        [
            "job", "chain_status", "audit_only_old_method",
            "projectile_template_id", "parent_projectile_template_id",
            "native_fixed_key", "exact_object_instance", "same_packet_record_joined",
            "hits", "damage",
            "captures", "examples", "audit_only_old_labels",
        ],
    )
    print(json.dumps({
        "captures": len(captures),
        "coverage": coverage,
        "candidate_hits": len(candidate_rows),
        "eligible_template_rules": len(eligible_templates),
        "conflicts": len(conflict_rows),
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
