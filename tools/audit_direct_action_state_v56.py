"""Offline audit of client-like direct-attack action state.

The audit is intentionally shadow-only.  It does not mutate the production
decoder and it never uses a generic temporal window, damage magnitude, Hit
spacing, or the existing inferred skill name to construct a candidate.

Candidate state is built from complete typed 0x0E/0x12 player action records,
exact SkillLevel -> ACT action definitions, ACT type-4 direct-damage signals,
ACT type-15 object-spawn signals, player object generations, and damage result
families/groups.  Existing deterministic object/entity/status attribution is
consulted only after candidate construction as independent validation truth.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Iterable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    AttributionLayer,
    attribution_layer,
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_damage_skill_ids,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_player_object_creation_v53 import (  # noqa: E402
    A90370Stream,
    OBJECT_ID_MAX,
    PLAYER_ID_MIN,
    _parse_a90370,
)
from tools.audit_under95_native_joint_keys_v55 import (  # noqa: E402
    TARGET_JOBS,
    _event_map,
    _hash_file,
    _origin_state,
    _physical_event,
    _runtime_jobs,
    _selection,
    _tree_signature,
)


SCHEMA = "direct-action-state-v56"
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_RUNTIME = ROOT / "outputs/compact_entity_selectors_20260908/RUNTIME_RESULT.md"
DEFAULT_CATALOG = ROOT / "outputs/damage_attribution/attack_instance_catalog_v576.json"
DEFAULT_OUTPUT = ROOT / "outputs/direct_action_state_v56_20260909"

INDEPENDENT_TRUTH_METHODS = frozenset(
    {
        "damage_object_projectile_link",
        "packet_entity_skill_id",
        "packet_entity_projectile_selector",
        "packet_entity_self_attack",
        "packet_entity_template_owner",
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
    }
)

ROUTE_PRIORITY = {
    "object_birth_0e_spawn_signal": 0,
    "object_birth_any_spawn_signal": 1,
    "result_group_0e_direct_signal": 2,
    "result_group_any_direct_signal": 3,
    "hit_0e_direct_signal": 4,
    "hit_any_direct_signal": 5,
    "object_birth_0e_action_lifetime": 6,
    "object_birth_any_action_lifetime": 7,
    "hit_12_direct_signal": 8,
    "hit_0e_track_direct_signal": 9,
    "hit_12_track_direct_signal": 10,
}


def _canonical(value: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(value), int(value)))


def _positive(value: object) -> int:
    try:
        result = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    return result if result > 0 else 0


def _target_class(target_actor_id: int) -> str:
    # ``target_job`` can be populated from a stale display context for small
    # PvE actor IDs.  The physical wire ID is the requested source/target
    # evidence and is also the player-range discriminator used by the object
    # audits.
    return "player_id_range" if int(target_actor_id) >= PLAYER_ID_MIN else "pve_entity_id_range"


def _independent_truth(event: CombatEvent | None) -> int:
    if event is None:
        return 0
    skill = _canonical(_positive(event.skill_id))
    if skill <= 0:
        return 0
    method = str(event.attribution_method or "")
    if method in INDEPENDENT_TRUTH_METHODS:
        return skill
    if is_deterministic_attribution(method) and attribution_layer(method) in {
        AttributionLayer.OBJECT,
        AttributionLayer.ENTITY,
    }:
        return skill
    return 0


@dataclass(frozen=True, slots=True)
class ActionProfile:
    raw_skill_id: int
    canonical_skill_id: int
    name: str
    direct_intervals_ms: tuple[tuple[float, float], ...]
    spawn_intervals_ms: tuple[tuple[float, float], ...]
    action_lengths_ms: tuple[float, ...]
    primary_action_count: int
    continuation_action_count: int


@dataclass(frozen=True, slots=True)
class ActionEpoch:
    actor_id: int
    epoch_id: int
    kind: int
    raw_skill_id: int
    canonical_skill_id: int
    timestamp_ms: int
    packet_serial: int
    record_offset: int
    profile: ActionProfile


@dataclass(slots=True)
class ObjectGeneration:
    owner_actor_id: int
    object_id: int
    generation: int
    timestamp_ms: int
    packet_serial: int
    record_offset: int
    config_row: int
    candidates: dict[str, ActionEpoch]


def _merge(intervals: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    rows = sorted((max(0.0, float(a)), max(0.0, float(b))) for a, b in intervals)
    merged: list[list[float]] = []
    for start, end in rows:
        if end < start:
            start, end = end, start
        if not merged or start > merged[-1][1] + 0.001:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((a, b) for a, b in merged)


def _load_profiles(path: Path) -> tuple[dict[int, ActionProfile], dict[int, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    profiles: dict[int, ActionProfile] = {}
    names: dict[int, str] = {}
    for row in raw.get("skills", ()):
        skill = _positive(row.get("skill_id"))
        if not skill:
            continue
        canonical = _canonical(_positive(row.get("canonical_skill_id")) or skill)
        names.setdefault(canonical, str(row.get("name", "")))
        direct: list[tuple[float, float]] = []
        spawn: list[tuple[float, float]] = []
        lengths: list[float] = []
        primary_count = continuation_count = 0
        for action in row.get("matched_actions", ()):
            # Continuations have their own local frame zero.  The catalog does
            # not carry the transition frame, so treating them as cast-relative
            # would be a fabricated time offset.  Retain the count for audit,
            # but only primary action frames may construct candidates.
            if bool(action.get("explicit_action_continuation", False)):
                continuation_count += 1
                continue
            primary_count += 1
            length = max(0, int(action.get("length_frames", 0) or 0))
            if length:
                lengths.append(1000.0 * length / 60.0)
            for signal in action.get("signals", ()):
                signal_type = int(signal.get("type", -1) or -1)
                interval = (
                    1000.0 * int(signal.get("start_frame", 0) or 0) / 60.0,
                    1000.0 * int(signal.get("end_frame", 0) or 0) / 60.0,
                )
                if signal_type == 4:
                    direct.append(interval)
                elif signal_type == 15:
                    spawn.append(interval)
        profiles[skill] = ActionProfile(
            raw_skill_id=skill,
            canonical_skill_id=canonical,
            name=str(row.get("name", "")),
            direct_intervals_ms=_merge(direct),
            spawn_intervals_ms=_merge(spawn),
            action_lengths_ms=tuple(sorted(set(lengths))),
            primary_action_count=primary_count,
            continuation_action_count=continuation_count,
        )
    return profiles, names


def _inside(epoch: ActionEpoch | None, timestamp_ms: int, mode: str) -> bool:
    if epoch is None:
        return False
    age = float(timestamp_ms - epoch.timestamp_ms)
    if age < -0.001:
        return False
    if mode == "direct":
        intervals = epoch.profile.direct_intervals_ms
    elif mode == "spawn":
        intervals = epoch.profile.spawn_intervals_ms
    elif mode == "lifetime":
        return any(age <= end + 0.001 for end in epoch.profile.action_lengths_ms)
    else:
        raise ValueError(mode)
    return any(start - 0.001 <= age <= end + 0.001 for start, end in intervals)


def _current_candidates(
    latest_0e: ActionEpoch | None,
    latest_12: ActionEpoch | None,
    latest_any: ActionEpoch | None,
    timestamp_ms: int,
    mode: str,
) -> dict[str, ActionEpoch]:
    result: dict[str, ActionEpoch] = {}
    if latest_any is not None and latest_any.kind == 0x0E and _inside(latest_any, timestamp_ms, mode):
        result[f"0e_{mode}"] = latest_any
    if latest_any is not None and latest_any.kind == 0x12 and _inside(latest_any, timestamp_ms, mode):
        result[f"12_{mode}"] = latest_any
    if latest_any is not None and _inside(latest_any, timestamp_ms, mode):
        result[f"any_{mode}"] = latest_any
    if latest_0e is not None and _inside(latest_0e, timestamp_ms, mode):
        result[f"0e_track_{mode}"] = latest_0e
    if latest_12 is not None and _inside(latest_12, timestamp_ms, mode):
        result[f"12_track_{mode}"] = latest_12
    return result


def _capture_worker(
    entry: dict[str, str],
    catalog_text: str,
) -> dict[str, Any]:
    path = Path(entry["path"])
    if _hash_file(path) != entry["sha256"]:
        raise ValueError(f"capture hash changed: {path}")
    profiles, _names = _load_profiles(Path(catalog_text))
    known_skills = frozenset(int(value) for value in _load_damage_skill_ids(str(DATA_DIR)))
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    epoch_counter: Counter[int] = Counter()
    latest_0e: dict[int, ActionEpoch] = {}
    latest_12: dict[int, ActionEpoch] = {}
    latest_any: dict[int, ActionEpoch] = {}
    object_numbers: Counter[tuple[int, int]] = Counter()
    active_objects: dict[tuple[int, int], ObjectGeneration] = {}
    result_group_bindings: dict[tuple[int, int, int], dict[str, ActionEpoch]] = {}
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    gap_universe: dict[str, set[str]] = defaultdict(set)

    def feed_events(packet):
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
        if not inventory.complete:
            counts[f"unframed:{inventory.rejection_reason}"] += 1
            return events
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        event_rows = _event_map(events)
        damage_by_offset = {
            int(row.record_offset): row
            for row in inventory.damage_records
            if row.admissible_fields
        }
        for typed in sorted(inventory.typed_records, key=lambda value: value.record_offset):
            record = bytes(typed.record_bytes)
            owner = int.from_bytes(record[:4], "little") if len(record) >= 4 else 0
            kind = int(typed.record_type)
            marker = int(typed.record_offset)
            if kind in (0x0E, 0x12):
                counts[f"action_{kind:02x}_records"] += 1
                if len(record) < 11:
                    counts[f"action_{kind:02x}_truncated"] += 1
                    continue
                raw_skill = int.from_bytes(record[7:11], "little")
                profile = profiles.get(raw_skill)
                if owner < PLAYER_ID_MIN or raw_skill not in known_skills or profile is None:
                    counts[f"action_{kind:02x}_unusable"] += 1
                    continue
                epoch_counter[owner] += 1
                epoch = ActionEpoch(
                    actor_id=owner,
                    epoch_id=int(epoch_counter[owner]),
                    kind=kind,
                    raw_skill_id=raw_skill,
                    canonical_skill_id=profile.canonical_skill_id,
                    timestamp_ms=timestamp_ms,
                    packet_serial=serial,
                    record_offset=marker,
                    profile=profile,
                )
                if kind == 0x0E:
                    latest_0e[owner] = epoch
                else:
                    latest_12[owner] = epoch
                latest_any[owner] = epoch
                counts[f"action_{kind:02x}_accepted"] += 1
                continue

            if kind in (9, 56):
                try:
                    parsed = _parse_a90370(record)
                except ValueError:
                    continue
                object_id = int(parsed.object_id)
                is_player_root = (
                    owner >= PLAYER_ID_MIN
                    and int(parsed.reference_branch_i8) == 0
                    and int(parsed.reference_id) == owner
                    and 0 < object_id <= OBJECT_ID_MAX
                )
                if not is_player_root:
                    continue
                pair = (owner, object_id)
                object_numbers[pair] += 1
                candidates: dict[str, ActionEpoch] = {}
                for mode in ("spawn", "lifetime"):
                    for label, epoch in _current_candidates(
                        latest_0e.get(owner), latest_12.get(owner), latest_any.get(owner),
                        timestamp_ms, mode,
                    ).items():
                        if label == f"0e_{mode}":
                            candidates[f"object_birth_0e_{'spawn_signal' if mode == 'spawn' else 'action_lifetime'}"] = epoch
                        elif label == f"any_{mode}":
                            candidates[f"object_birth_any_{'spawn_signal' if mode == 'spawn' else 'action_lifetime'}"] = epoch
                active_objects[pair] = ObjectGeneration(
                    owner_actor_id=owner,
                    object_id=object_id,
                    generation=int(object_numbers[pair]),
                    timestamp_ms=timestamp_ms,
                    packet_serial=serial,
                    record_offset=marker,
                    config_row=int(parsed.config_row or 0),
                    candidates=candidates,
                )
                counts["player_object_generations"] += 1
                continue

            if kind != 8:
                continue
            physical = damage_by_offset.get(marker)
            if physical is None:
                continue
            event = _physical_event(physical, event_rows)
            if event is None or str(event.job) not in TARGET_JOBS:
                continue
            if event.healing or event.amount <= 0 or not bool(event.has_real_hit_evidence):
                continue
            counts["target_real_hits"] += 1
            source_actor = int(physical.source_actor_id)
            if source_actor < PLAYER_ID_MIN:
                counts["entity_source_hits_excluded"] += 1
                continue
            origin, origin_current = _origin_state(
                decoder, physical, float(packet.timestamp_seconds), serial
            )
            origin_template = int(origin.projectile_template_id if origin else 0)
            entity_template = int(event.entity_template_id or 0)
            template_state = (
                "resolved_projectile_or_entity_template"
                if origin_template or entity_template
                else "no_resolved_projectile_or_entity_template"
            )
            counts[f"player_hits:{template_state}"] += 1
            family = str(physical.family)
            object_id = int(physical.damage_object_id or 0)
            generation = active_objects.get((source_actor, object_id)) if family == "direct37" and object_id else None
            object_state = (
                "a90370_generation"
                if generation is not None
                else "origin_generation_without_template"
                if origin is not None and origin_current
                else "no_generation"
            )
            candidates: dict[str, ActionEpoch] = {}
            if generation is not None:
                candidates.update(generation.candidates)
            current = _current_candidates(
                latest_0e.get(source_actor), latest_12.get(source_actor), latest_any.get(source_actor),
                timestamp_ms, "direct",
            )
            route_names = {
                "0e_direct": "hit_0e_direct_signal",
                "12_direct": "hit_12_direct_signal",
                "any_direct": "hit_any_direct_signal",
                "0e_track_direct": "hit_0e_track_direct_signal",
                "12_track_direct": "hit_12_track_direct_signal",
            }
            for label, epoch in current.items():
                route = route_names.get(label)
                if route:
                    candidates[route] = epoch

            group_key = (source_actor, int(physical.damage_result_group_id or 0), serial)
            if group_key not in result_group_bindings:
                seed: dict[str, ActionEpoch] = {}
                if "hit_0e_direct_signal" in candidates:
                    seed["result_group_0e_direct_signal"] = candidates["hit_0e_direct_signal"]
                if "hit_any_direct_signal" in candidates:
                    seed["result_group_any_direct_signal"] = candidates["hit_any_direct_signal"]
                result_group_bindings[group_key] = seed
            candidates.update(result_group_bindings[group_key])

            method = str(event.attribution_method or "")
            deterministic = bool(is_deterministic_attribution(method))
            truth = _independent_truth(event)
            assigned = _canonical(_positive(event.skill_id))
            gap_target = bool(
                not deterministic
                and assigned > 0
                and template_state == "no_resolved_projectile_or_entity_template"
            )
            physical_id = (
                f"{entry['sha256']}:{serial}:{marker}:"
                f"{physical.wire_record_sha256}"
            )
            if gap_target:
                gap_universe[str(event.job)].add(physical_id)
            target_class = _target_class(int(physical.target_actor_id))
            for route, epoch in candidates.items():
                rows.append(
                    {
                        "physical_id": physical_id,
                        "capture": path.name,
                        "capture_sha256": entry["sha256"],
                        "job": str(event.job),
                        "route": route,
                        "candidate_skill_id": int(epoch.canonical_skill_id),
                        "candidate_raw_skill_id": int(epoch.raw_skill_id),
                        "action_kind": f"0x{epoch.kind:02X}",
                        "action_epoch_id": int(epoch.epoch_id),
                        "action_packet_serial": int(epoch.packet_serial),
                        "action_record_offset": int(epoch.record_offset),
                        "action_age_ms": int(timestamp_ms - epoch.timestamp_ms),
                        "family": family,
                        "damage_record_kind": "0x08",
                        "source_actor_id": source_actor,
                        "source_token": f"{entry['sha256']}:{source_actor:08X}",
                        "target_actor_id": int(physical.target_actor_id),
                        "target_class": target_class,
                        "damage_object_id": object_id,
                        "object_state": object_state,
                        "object_generation": int(
                            generation.generation if generation is not None
                            else origin.generation if origin is not None and origin_current
                            else 0
                        ),
                        "result_group_id": int(physical.damage_result_group_id or 0),
                        "truth_skill_id": int(truth),
                        "truth_method": method if truth else "",
                        "assigned_skill_id": int(assigned),
                        "deterministic": deterministic,
                        "template_state": template_state,
                        "gap_target": gap_target,
                    }
                )
            counts["candidate_rows"] += len(candidates)
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": path.name,
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "rows": rows,
        "gap_universe": {
            job: sorted(values) for job, values in sorted(gap_universe.items())
        },
    }


def _production_readiness(selected: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify statistically accepted rules using production-safe guards."""
    safe_assignment_rules = []
    guarded_review_rules = []
    for row in selected:
        if row["route"] != "object_birth_0e_spawn_signal":
            continue
        unvalidated = int(row["candidate_hits"]) - int(row["truth_hits"]) - int(row["gap_hits"])
        compact = {
            key: row[key]
            for key in (
                "job", "candidate_skill_id", "candidate_skill_name", "route",
                "family", "target_class", "object_state", "candidate_hits",
                "truth_hits", "truth_conflicts", "truth_capture_count",
                "truth_actor_count", "new_unique_gap_hits",
            )
        } | {"unvalidated_candidate_hits": unvalidated}
        if unvalidated == 0:
            safe_assignment_rules.append(compact)
        else:
            guarded_review_rules.append(compact)
    return {
        "safe_structural_rules": [
            "0x0E 与 0x12 必须维护相互独立的玩家动作 epoch",
            "A90370 对象必须按 owner + object id + generation 管理，禁止跨代复用",
            "damage result 自带技能键及确定性 Projectile/实体链始终优先",
            "动作状态只能作为末级补缺，绝不能覆盖任何确定性归因",
        ],
        "safe_guarded_assignment_contract": [
            "wire source is a player and target is in the PvE entity id range",
            "result family is direct37 and the object has the current A90370 owner/generation",
            "candidate comes from a 0x0E primary action type-15 spawn signal",
            "job and canonical skill are on the audited allowlist",
            "no damage-result skill key or deterministic Projectile/entity attribution exists",
            "apply only to an unresolved named gap; never rewrite a resolved Hit",
        ],
        "safe_guarded_assignment_rules": safe_assignment_rules,
        "safe_guarded_unique_gap_hits": sum(int(row["new_unique_gap_hits"]) for row in safe_assignment_rules),
        "requires_more_evidence": guarded_review_rules,
        "not_safe_for_production": [
            "所有基于 0x12 的技能归因路线",
            "所有可能选中 0x12 的 any-action 路线",
            "把 hit/result-group direct-signal 当作通用归因规则",
            "未从包内恢复动作速度状态时使用完整动作生命周期路线",
            "不按职业/技能白名单而全局启用 object_birth_0e_spawn_signal",
        ],
    }


def _aggregate(
    captures: list[dict[str, Any]],
    runtime: dict[str, dict[str, Any]],
    names: dict[int, str],
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    buckets: dict[tuple[str, str, int, str, str, str], dict[str, Any]] = {}
    no_template_gap_ids: dict[str, set[str]] = defaultdict(set)
    candidate_gap_ids: dict[str, set[str]] = defaultdict(set)
    for capture in captures:
        totals.update(capture["counts"])
        for job, values in capture.get("gap_universe", {}).items():
            no_template_gap_ids[str(job)].update(str(value) for value in values)
        for row in capture["rows"]:
            job = str(row["job"])
            key = (
                str(row["route"]), job, int(row["candidate_skill_id"]),
                str(row["family"]), str(row["target_class"]), str(row["object_state"]),
            )
            bucket = buckets.setdefault(
                key,
                {
                    "route": key[0], "job": job, "candidate_skill_id": key[2],
                    "family": key[3], "target_class": key[4], "object_state": key[5],
                    "hits": set(), "truth_skills": Counter(), "truth_methods": Counter(),
                    "truth_captures": set(), "truth_actors": set(),
                    "gap_hits": set(), "gap_labels": Counter(), "gap_label_disagreements": 0,
                    "action_kinds": Counter(), "samples": [],
                },
            )
            physical_id = str(row["physical_id"])
            bucket["hits"].add(physical_id)
            bucket["action_kinds"][str(row["action_kind"])] += 1
            truth = int(row["truth_skill_id"])
            if truth:
                bucket["truth_skills"][truth] += 1
                bucket["truth_methods"][str(row["truth_method"])] += 1
                bucket["truth_captures"].add(str(row["capture_sha256"]))
                bucket["truth_actors"].add(str(row["source_token"]))
            if row["gap_target"]:
                bucket["gap_hits"].add(physical_id)
                candidate_gap_ids[job].add(physical_id)
                label = int(row["assigned_skill_id"])
                if label:
                    bucket["gap_labels"][label] += 1
                    bucket["gap_label_disagreements"] += int(label != key[2])
            if len(bucket["samples"]) < 4:
                bucket["samples"].append(
                    {name: row[name] for name in (
                        "capture", "physical_id", "action_kind", "action_epoch_id",
                        "action_age_ms", "family", "source_actor_id", "target_actor_id",
                        "damage_object_id", "object_generation", "result_group_id",
                        "truth_skill_id", "truth_method", "assigned_skill_id",
                    )}
                )

    rules: list[dict[str, Any]] = []
    for bucket in buckets.values():
        candidate = int(bucket["candidate_skill_id"])
        truth_hits = sum(bucket["truth_skills"].values())
        truth_matches = int(bucket["truth_skills"].get(candidate, 0))
        conflicts = truth_hits - truth_matches
        accepted = bool(
            truth_hits >= 8
            and len(bucket["truth_captures"]) >= 2
            and len(bucket["truth_actors"]) >= 2
            and conflicts == 0
            and truth_matches == truth_hits
        )
        rules.append(
            {
                "route": bucket["route"],
                "job": bucket["job"],
                "candidate_skill_id": candidate,
                "candidate_skill_name": names.get(candidate, ""),
                "family": bucket["family"],
                "target_class": bucket["target_class"],
                "object_state": bucket["object_state"],
                "candidate_hits": len(bucket["hits"]),
                "truth_hits": truth_hits,
                "truth_matches": truth_matches,
                "truth_conflicts": conflicts,
                "truth_skills": {str(k): v for k, v in sorted(bucket["truth_skills"].items())},
                "truth_methods": dict(sorted(bucket["truth_methods"].items())),
                "truth_capture_count": len(bucket["truth_captures"]),
                "truth_actor_count": len(bucket["truth_actors"]),
                "gap_hits": len(bucket["gap_hits"]),
                "gap_physical_ids": sorted(bucket["gap_hits"]),
                "gap_old_labels": {str(k): v for k, v in sorted(bucket["gap_labels"].items())},
                "gap_old_label_disagreements": int(bucket["gap_label_disagreements"]),
                "action_kinds": dict(sorted(bucket["action_kinds"].items())),
                "accepted": accepted,
                "samples": bucket["samples"],
            }
        )
    rules.sort(
        key=lambda row: (
            not row["accepted"], ROUTE_PRIORITY.get(row["route"], 99),
            row["job"], row["candidate_skill_id"], row["family"], row["object_state"],
        )
    )

    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for rule in sorted(
        (row for row in rules if row["accepted"] and row["gap_hits"]),
        key=lambda row: (
            ROUTE_PRIORITY.get(row["route"], 99), -row["truth_hits"], -row["gap_hits"],
            row["job"], row["candidate_skill_id"],
        ),
    ):
        new_ids = [value for value in rule["gap_physical_ids"] if value not in used]
        if not new_ids:
            continue
        used.update(new_ids)
        selected.append(
            {key: value for key, value in rule.items() if key != "gap_physical_ids"}
            | {"new_unique_gap_hits": len(new_ids), "new_gap_physical_ids": new_ids}
        )

    selected_by_job: dict[str, set[str]] = defaultdict(set)
    for row in selected:
        selected_by_job[row["job"]].update(row["new_gap_physical_ids"])
    jobs = []
    for job in TARGET_JOBS:
        base = runtime[job]
        gain = len(selected_by_job[job])
        confirmed = int(base["confirmed_hits"])
        named = int(base["named_hits"])
        jobs.append(
            {
                "job": job,
                "baseline_coverage_percent": float(base["coverage_percent"]),
                "baseline_confirmed_hits": confirmed,
                "baseline_named_hits": named,
                "hits_needed_for_95": int(base["hits_needed_for_95"]),
                "no_template_gap_hits": len(no_template_gap_ids[job]),
                "state_candidate_gap_hits": len(candidate_gap_ids[job]),
                "accepted_unique_gain_upper_bound": gain,
                "projected_coverage_percent": round(100.0 * (confirmed + gain) / named, 6) if named else 0.0,
                "reaches_95": bool(named and confirmed + gain >= 0.95 * named),
            }
        )

    route_summary: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rules:
        summary = route_summary[row["route"]]
        summary["rules"] += 1
        summary["candidate_hits"] += int(row["candidate_hits"])
        summary["truth_hits"] += int(row["truth_hits"])
        summary["truth_matches"] += int(row["truth_matches"])
        summary["truth_conflicts"] += int(row["truth_conflicts"])
        summary["gap_hits"] += int(row["gap_hits"])
        summary["accepted_rules"] += int(bool(row["accepted"]))

    # Production readiness is intentionally stricter than the statistical
    # acceptance contract.  A skill assignment is eligible only when every
    # candidate observed in the fixed corpus is either independently resolved
    # truth or the exact unresolved gap being projected.  This prevents a
    # zero-conflict rule with a large unvalidated tail from being described as
    # safe merely because the tail has no truth label yet.
    production_readiness = _production_readiness(selected)
    return {
        "counts": dict(totals),
        "jobs": jobs,
        "route_summary": {key: dict(value) for key, value in sorted(route_summary.items())},
        "production_readiness": production_readiness,
        "accepted_rules": [row for row in rules if row["accepted"]],
        "selected_nonoverlapping_rules": selected,
        "rejected_rules_with_truth": [
            {key: value for key, value in row.items() if key != "gap_physical_ids"}
            for row in rules if not row["accepted"] and row["truth_hits"]
        ],
    }


def _render(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# 无模板直接攻击动作状态离线审计 v56", "",
        "## 结论", "",
        "- 固定语料：35 份抓包，逐份 SHA-256 校验。",
        "- 本轮只新增 `tools/` 审计工具和本报告；生产代码哈希未改变。",
        "- 候选生成不读取旧技能名，不使用统一 2.5 秒、不比较伤害数值或 Hit 间隔。",
        "- 仅审计 `direct37/direct41`、玩家为 wire source、且没有已解析 Projectile/实体模板的真实 Hit。",
        "- `0x0E` 与 `0x12` 分开建 epoch；ACT 续接动作缺少相对入口偏移时不会伪造偏移。",
        "- “增益上限”只统计跨至少2包、2个角色、至少8个独立真值且零冲突规则在现有缺口上的不重复覆盖；仍是离线投影，不代表已上线。",
        "", "## 每职业增益上限", "",
        "|职业|当前覆盖|无模板命名缺口|有动作状态候选|零冲突增益上限|投影覆盖|达到95%|",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in aggregate["jobs"]:
        lines.append(
            f"|{row['job']}|{row['baseline_coverage_percent']:.2f}%|"
            f"{row['no_template_gap_hits']:,}|{row['state_candidate_gap_hits']:,}|"
            f"{row['accepted_unique_gain_upper_bound']:,}|{row['projected_coverage_percent']:.2f}%|"
            f"{'是' if row['reaches_95'] else '否'}|"
        )
    lines.extend(["", "## 动作路线整体真值表现", "",
                  "|路线|候选Hit|独立真值|匹配|冲突|精度|达标规则|",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for route, row in aggregate["route_summary"].items():
        truth = int(row.get("truth_hits", 0))
        matches = int(row.get("truth_matches", 0))
        precision = 100.0 * matches / truth if truth else 0.0
        lines.append(
            f"|`{route}`|{row.get('candidate_hits', 0):,}|{truth:,}|{matches:,}|"
            f"{row.get('truth_conflicts', 0):,}|{precision:.4f}%|{row.get('accepted_rules', 0):,}|"
        )
    selected = aggregate["selected_nonoverlapping_rules"]
    lines.extend(["", "## 跨包、跨角色、独立真值零冲突规则", ""])
    if selected:
        lines.extend([
            "|职业/技能|路线|结果族|目标类|对象代次状态|真值Hit|跨包/角色|缺口不重复增益|旧标签分歧|",
            "|---|---|---|---|---|---:|---:|---:|---:|",
        ])
        for row in selected:
            lines.append(
                f"|{row['job']} / {row['candidate_skill_id']} {row['candidate_skill_name']}|"
                f"`{row['route']}`|{row['family']}|{row['target_class']}|{row['object_state']}|"
                f"{row['truth_hits']:,}|{row['truth_capture_count']}/{row['truth_actor_count']}|"
                f"{row['new_unique_gap_hits']:,}|{row['gap_old_label_disagreements']:,}|"
            )
    else:
        lines.append("- 没有规则同时达到验收门槛；因此本专项不提出生产改动。")
    readiness = aggregate["production_readiness"]
    lines.extend(["", "## 生产化判定", "",
                  "### 可直接生产化的结构规则", ""])
    for value in readiness["safe_structural_rules"]:
        lines.append(f"- {value}")
    lines.extend(["", "### 可在严格白名单下生产化的归因规则", "",
                  "共同约束：仅补无确定性归因的空缺，不覆盖 damage result 自带技能键、Projectile/实体链或任何已有确定性结果。", "",
                  "|职业/技能|独立真值|跨包/角色|可补Hit|未验证候选|",
                  "|---|---:|---:|---:|---:|"])
    for row in readiness["safe_guarded_assignment_rules"]:
        lines.append(
            f"|{row['job']} / {row['candidate_skill_id']} {row['candidate_skill_name']}|"
            f"{row['truth_hits']:,}|{row['truth_capture_count']}/{row['truth_actor_count']}|"
            f"{row['new_unique_gap_hits']:,}|{row['unvalidated_candidate_hits']:,}|"
        )
    lines.extend(["", f"- 上述白名单合计可补 {readiness['safe_guarded_unique_gap_hits']:,} 个不重复 Hit。",
                  "- 仍需实现动作速度状态后再扩大规则；当前不允许把该路线全局化。",
                  "", "### 暂不可生产化", ""])
    for value in readiness["not_safe_for_production"]:
        lines.append(f"- {value}")
    if readiness["requires_more_evidence"]:
        lines.extend(["", "以下 `0x0E + type15` 规则虽为零真值冲突，但仍有未验证候选，继续留在离线观察：", "",
                      "|职业/技能|真值Hit|可补Hit|未验证候选|",
                      "|---|---:|---:|---:|"])
        for row in readiness["requires_more_evidence"]:
            lines.append(
                f"|{row['job']} / {row['candidate_skill_id']} {row['candidate_skill_name']}|"
                f"{row['truth_hits']:,}|{row['new_unique_gap_hits']:,}|"
                f"{row['unvalidated_candidate_hits']:,}|"
            )
    lines.extend([
        "", "## 对客户端内置统计器的判断", "",
        "- 静态客户端已证明：玩家分发器的 `0x0E` 分支会把正文首个 u32 传给玩家技能状态；`0x12` 走另一条通用分支，不能与 `0x0E` 等价处理。",
        "- 内置统计器最终优先读取 damage result 自带技能键；没有该键时才读取伤害对象保存的技能键。动作状态本身不是一个可以无条件套到后续伤害的全局字段。",
        "- 因而本报告只验证“动作状态是否可安全补无模板缺口”，不会把最近动作直接改成最终技能。",
        "", "## 明确边界", "",
        "- 续接 ACT 动作虽然保留在输入审计中，但当前静态目录没有可靠的入口相对帧，未参与候选时间计算。",
        "- 动作速度会改变墙钟时间；没有包内速度状态证明时，本工具不进行 1.2/1.4 等猜测缩放。",
        "- 实体 wire source、已有 Projectile/实体模板的 Hit 均排除，因为它们应由对象链或召唤实体链解决。",
        "- 旧技能名仅用于报告“标签分歧”，从未用于选择候选。",
        "", "## 复现", "",
        "```powershell",
        "$env:PYTHONUTF8='1'",
        "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B tools\\audit_direct_action_state_v56.py --workers 4",
        "```", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--render-existing", action="store_true",
        help="add the latest production-readiness classification to an existing JSON and re-render Markdown",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        raise ValueError("workers must be 1..8")
    if args.render_existing:
        json_path = args.output_dir / "REPORT.json"
        md_path = args.output_dir / "REPORT.md"
        report = json.loads(json_path.read_text(encoding="utf-8"))
        aggregate = report["aggregate"]
        aggregate["production_readiness"] = _production_readiness(
            aggregate["selected_nonoverlapping_rules"]
        )
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(_render(report), encoding="utf-8")
        print(md_path)
        print(json_path)
        return 0
    captures, selection = _selection(args.selection)
    runtime = _runtime_jobs(args.runtime)
    _profiles, names = _load_profiles(args.catalog)
    production_before = _tree_signature()
    reports: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_capture_worker, entry, str(args.catalog)): entry
            for entry in captures
        }
        for future in as_completed(futures):
            reports.append(future.result())
    reports.sort(key=lambda row: row["capture_sha256"])
    production_after = _tree_signature()
    report = {
        "schema": SCHEMA,
        "scope": "fixed 35-capture offline audit; no production mutation",
        "acceptance_contract": {
            "minimum_truth_hits": 8,
            "minimum_capture_count": 2,
            "minimum_actor_count": 2,
            "independent_truth_conflicts": 0,
            "candidate_generation_forbidden": [
                "generic_2.5_second_window", "damage_magnitude_similarity",
                "hit_spacing_similarity", "existing_or_old_skill_name",
            ],
        },
        "inputs": {
            "selection": str(args.selection),
            "selection_sha256": _hash_file(args.selection),
            "runtime": str(args.runtime),
            "runtime_sha256": _hash_file(args.runtime),
            "catalog": str(args.catalog),
            "catalog_sha256": _hash_file(args.catalog),
            "capture_count": len(captures),
            "capture_commitments": [
                {key: row[key] for key in ("name", "path", "sha256")}
                for row in captures
            ],
            "selection_source_count": len(selection.get("sources", {}).get("selected_results", ())),
        },
        "production_signature_before": production_before,
        "production_signature_after": production_after,
        "production_changed": production_before != production_after,
        "aggregate": _aggregate(reports, runtime, names),
        "capture_summaries": [
            {"capture": row["capture"], "capture_sha256": row["capture_sha256"], "counts": row["counts"]}
            for row in reports
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "REPORT.json"
    md_path = args.output_dir / "REPORT.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render(report), encoding="utf-8")
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
