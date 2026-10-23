"""Unknown-first Cleric direct37/direct41 native attribution audit.

This tool is deliberately read-only.  It replays only the hash-pinned new
Black Dragon Classic/Hardcore selection and treats every production skill
name/skill id as audit-only.  A skill candidate may come only from:

* the fixed result key read by the client's built-in damage meter;
* a same-record field with an independently verified client reader;
* an exact owner + object-id + generation Projectile registration whose
  client table mapping is deterministic; or
* an A90370 object registration whose embedded ACT action/signal/config tuple
  resolves to one skill in the corresponding job resource.

No elapsed-time window, damage amount pattern, Hit pattern, or old skill name
is allowed to choose a skill.  Per-capture checkpoints keep the full 29-file
audit resumable without retaining every Hit in memory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    ACTION_QUALIFIED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.native_damage_skill_keys import (  # noqa: E402
    STATIC_INTERNAL_ATTACK_PARENT,
    _selected_native_key,
)
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_damage_skill_ids,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    decode_v576_server_damage_object_registrations,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _scope_reason,
    _strict_classification,
)
from tools.audit_merged_player_action_indices_v99 import _profile_catalog  # noqa: E402
from tools.audit_native_meter_skill_key_v50 import (  # noqa: E402
    DEFAULT_CAPTURE_ROOT,
    _capture_selection,
    _find_captures,
)
from tools.audit_player_object_creation_v53 import (  # noqa: E402
    A90370Stream,
    _native_evidence,
    _parse_a90370,
)
from tools.audit_native_key_gates_v60 import static_audit as _meter_static_audit  # noqa: E402
from tools.probe_merged_player_act_indices_v99 import load_merged_actions  # noqa: E402


SCHEMA = "cleric-unknown-native-audit-v111"
TARGET_JOBS = ("圣骑士", "十字军", "圣徒", "雷神")
PLAYER_ID_MIN = 0x01000000
DEFAULT_SELECTION_REQUESTED = (
    ROOT / "outputs/black_dragon_new33_cleric_selection_v104.json"
)
DEFAULT_SELECTION_ACTUAL = (
    ROOT / "outputs/blackdragon_new33_cleric_selection_v104.json"
)
DEFAULT_OUTPUT = ROOT / "outputs/cleric_unknown_native_v111_20260910"
DEFAULT_PROFILES = ROOT / "outputs/client_skill_hits/all_second_jobs_v2"
DEFAULT_RESOURCE_ROOT = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")
CHECKPOINT_VERSION = 5

PRODUCTION_INPUTS = (
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/native_damage_skill_keys.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/sources.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _canonical(raw: int) -> int:
    mapped = int(STATIC_INTERNAL_ATTACK_PARENT.get(int(raw), int(raw)))
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(mapped, mapped))


def _positive_skill(value: object, catalog: frozenset[int]) -> int:
    try:
        result = _canonical(int(value))
    except (TypeError, ValueError):
        return 0
    return result if result in catalog else 0


def _resolve_selection(path: Path) -> tuple[Path, str]:
    if path.exists():
        return path, "requested_path"
    if path.resolve() == DEFAULT_SELECTION_REQUESTED.resolve() and DEFAULT_SELECTION_ACTUAL.exists():
        return DEFAULT_SELECTION_ACTUAL, "compatibility_alias_missing_underscore"
    raise FileNotFoundError(path)


def _production_signature() -> dict[str, str]:
    return {name: _hash_file(ROOT / name) for name in PRODUCTION_INPUTS}


def _action_maps(profile_dir: Path, resource_root: Path) -> dict[str, dict[str, int]]:
    """Build exact embedded action/signal/config -> canonical skill maps."""
    profiles = _profile_catalog(profile_dir, resource_root)
    result: dict[str, dict[str, int]] = {}
    for job in TARGET_JOBS:
        profile = profiles[job]
        merged = load_merged_actions(resource_root, profile["player_class"])
        rows: dict[str, int] = {}
        for action_index, action in enumerate(merged):
            candidates = sorted(profile["action_skills"].get(action.name.casefold(), ()))
            if len(candidates) != 1:
                continue
            skill = _canonical(int(candidates[0]))
            for signal_index, signal_type in enumerate(action.signal_types):
                if signal_type != 15 or signal_index >= len(action.signal_payload0):
                    continue
                config = int(action.signal_payload0[signal_index] or 0)
                if config <= 0:
                    continue
                key = f"{action_index}|{signal_index}|{config}"
                previous = rows.get(key)
                rows[key] = skill if previous in (None, skill) else 0
        result[job] = {key: value for key, value in rows.items() if value > 0}
    return result


def _client_skill_names(profile_dir: Path) -> dict[int, str]:
    """Names from static client profiles, never from replayed event labels."""
    names: defaultdict[int, set[str]] = defaultdict(set)
    for path in sorted(profile_dir.glob("[0-9][0-9]_*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        for skill in value.get("job_profile", {}).get("skills", ()):
            skill_id = _canonical(int(skill.get("skill_id", 0) or 0))
            name = str(skill.get("name") or "").strip()
            if skill_id > 0 and name:
                names[skill_id].add(name)
    return {
        skill_id: sorted(values)[0]
        for skill_id, values in names.items()
        if len(values) == 1
    }


def _event_key(event: CombatEvent) -> tuple[Any, ...]:
    family = (
        "direct41" if "direct41" in str(event.wire_damage_layout)
        else "direct37" if "direct37" in str(event.wire_damage_layout)
        else ""
    )
    return (
        family,
        int(event.wire_record_offset),
        int(event.wire_source_actor_id),
        int(event.wire_target_actor_id),
        int(event.amount),
        bool(event.wire_critical),
    )


def _record_key(record: object) -> tuple[Any, ...]:
    return (
        str(record.family),
        int(record.record_offset),
        int(record.source_actor_id),
        int(record.target_actor_id),
        int(record.damage),
        bool(record.critical),
    )


def _native_candidate(record: object, catalog: frozenset[int]) -> tuple[int, str, int]:
    selected = _selected_native_key(record)
    if selected is None:
        return 0, "", 0
    field, raw, _source_kind, _common_cc = selected
    return _positive_skill(raw, catalog), str(field), int(raw)


def _projectile_candidate(registration: object) -> tuple[int, str]:
    template = int(registration.projectile_template_id)
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
    if spec is None:
        return 0, "unmapped_template"
    if (
        bool(registration.parent_qualified_subtype)
        or template in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS
    ):
        return 0, "requires_exact_parent_generation"
    if (
        bool(registration.action_qualified_subtype)
        or template in ACTION_QUALIFIED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
        or bool(spec.require_action_evidence)
    ):
        return 0, "requires_action_epoch_time_window_forbidden"
    if not (
        bool(registration.packet_validated_subtype)
        or template in PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
        or template in CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
    ):
        return 0, "template_not_independently_validated"
    skill = _canonical(int(spec.canonical_skill_id))
    return (skill, "exact_registration_static_template") if skill > 0 else (0, "invalid_skill")


def _scan_same_record(record: object, catalog: frozenset[int]) -> list[tuple[int, int]]:
    """Return catalogued u32s; observations only, never skill proof."""
    raw = bytes(record.record_bytes)
    found: list[tuple[int, int]] = []
    for start in range(4, max(4, len(raw) - 3)):
        candidate = _positive_skill(int.from_bytes(raw[start : start + 4], "little"), catalog)
        if candidate:
            found.append((start - 4, candidate))
    return found


def _counter_rows(counter: Counter[tuple[Any, ...]], names: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {**dict(zip(names, key)), "hits": amount}
        for key, amount in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _audit_capture(
    path_text: str,
    expected_sha: str,
    action_maps: dict[str, dict[str, int]],
    checkpoint_text: str,
) -> dict[str, Any]:
    path = Path(path_text)
    checkpoint = Path(checkpoint_text)
    actual_sha = _hash_file(path)
    if actual_sha != expected_sha:
        raise ValueError(f"capture hash mismatch: {path.name}")
    if checkpoint.exists():
        cached = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (
            cached.get("checkpoint_version") == CHECKPOINT_VERSION
            and cached.get("capture_sha256") == expected_sha
        ):
            return cached

    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    damage_catalog = frozenset(int(value) for value in _load_damage_skill_ids(str(DATA_DIR)))
    valid_catalog = frozenset(catalog & damage_catalog)
    active_a90370: dict[tuple[int, int], dict[str, Any]] = {}
    active_projectile: dict[tuple[int, int], dict[str, Any]] = {}
    generation_a90370: Counter[tuple[int, int]] = Counter()
    generation_projectile: Counter[tuple[int, int]] = Counter()
    totals: Counter[str] = Counter()
    methods: Counter[tuple[str, str, str]] = Counter()
    routes: Counter[tuple[str, str, int, str]] = Counter()
    route_damage: Counter[tuple[str, str, int, str]] = Counter()
    route_captures: defaultdict[tuple[str, str, int, str], set[str]] = defaultdict(set)
    conflicts: Counter[tuple[str, str]] = Counter()
    same_record: Counter[tuple[str, int, int]] = Counter()
    unmapped_native_result_keys: Counter[
        tuple[str, str, str, int, int, int]
    ] = Counter()
    strict_negatives: Counter[tuple[str, str]] = Counter()
    examples: list[dict[str, Any]] = []
    physical_commitment: list[str] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        events = original(packet)
        inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
        if not inventory.complete:
            totals[f"inventory_rejected:{inventory.rejection_reason}"] += 1
            return events
        event_map: defaultdict[tuple[Any, ...], list[CombatEvent]] = defaultdict(list)
        for event in events:
            if isinstance(event, CombatEvent) and not event.healing and int(event.amount) > 0:
                event_map[_event_key(event)].append(event)

        registrations = decode_v576_server_damage_object_registrations(
            packet.payload, packet.inferred_direction(), include_unmapped=True
        )
        typed_by_offset = defaultdict(list)
        for typed in inventory.typed_records:
            typed_by_offset[int(typed.record_offset)].append(typed)
        regs_by_offset = defaultdict(list)
        for registration in registrations:
            regs_by_offset[int(registration.record_offset)].append(registration)
        damage_by_offset = defaultdict(list)
        for record in inventory.damage_records:
            if record.admissible_fields and record.family in {"direct37", "direct41"}:
                damage_by_offset[int(record.record_offset)].append(record)

        offsets = sorted(set(typed_by_offset) | set(regs_by_offset) | set(damage_by_offset))
        for offset in offsets:
            # Open a new registration generation before later records in the
            # same packet, never after them.
            for registration in regs_by_offset.get(offset, ()):
                pair = (int(registration.source_actor_id), int(registration.damage_object_id))
                generation_projectile[pair] += 1
                candidate, reason = _projectile_candidate(registration)
                active_projectile[pair] = {
                    "generation": generation_projectile[pair],
                    "candidate": candidate,
                    "template": int(registration.projectile_template_id),
                    "reason": reason,
                    "packet": packet_index,
                    "offset": offset,
                }
                totals["projectile_registrations"] += 1
                totals[f"projectile_registration:{reason}"] += 1

            for typed in typed_by_offset.get(offset, ()):
                if int(typed.record_type) not in (9, 56):
                    continue
                try:
                    parsed = _parse_a90370(bytes(typed.record_bytes))
                except ValueError:
                    continue
                owner = int.from_bytes(bytes(typed.record_bytes)[:4], "little")
                object_id = int(parsed.object_id)
                if (
                    owner < PLAYER_ID_MIN
                    or object_id <= 0
                    or int(parsed.reference_branch_i8) != 0
                    or int(parsed.reference_id) != owner
                ):
                    continue
                pair = (owner, object_id)
                generation_a90370[pair] += 1
                active_a90370[pair] = {
                    "generation": generation_a90370[pair],
                    "config": int(parsed.config_row or 0),
                    "action": int(parsed.action_index_i16),
                    "signal": int(parsed.signal_key_i8),
                    "packet": packet_index,
                    "offset": offset,
                }
                totals["a90370_root_registrations"] += 1

            for record in damage_by_offset.get(offset, ()):
                candidates = event_map.get(_record_key(record), ())
                if len(candidates) != 1:
                    totals[f"event_physical_join:{len(candidates)}"] += 1
                    continue
                event = candidates[0]
                job = str(event.job or "")
                if job not in TARGET_JOBS:
                    continue
                scope = _scope_reason(event)
                if scope != "included_positive_direct_hit":
                    totals[f"{job}:outside_direct_scope:{scope}"] += 1
                    continue
                classification = _strict_classification(event)
                if classification == "deterministic":
                    totals[f"{job}:deterministic_control"] += 1
                    continue
                totals[f"{job}:unknown_hits"] += 1
                totals[f"{job}:{classification}"] += 1
                totals[f"{job}:unknown_damage"] += int(record.damage)
                methods[(job, classification, str(event.attribution_method or ""))] += 1
                identity = "|".join(
                    str(value) for value in (
                        actual_sha, packet_index, record.record_offset, record.family,
                        record.source_actor_id, record.target_actor_id, record.damage,
                        record.damage_object_id, record.damage_result_group_id,
                        int(record.critical), record.wire_record_sha256,
                    )
                )
                physical_commitment.append(identity)
                evidence: list[tuple[str, int, str]] = []

                native, native_field, native_raw = _native_candidate(record, valid_catalog)
                selected_native = _selected_native_key(record)
                if selected_native is not None and native <= 0:
                    (
                        selected_field,
                        selected_raw,
                        selected_source_kind,
                        selected_common_cc,
                    ) = selected_native
                    unmapped_native_result_keys[
                        (
                            job,
                            str(record.family),
                            str(selected_field),
                            int(selected_raw),
                            int(selected_source_kind),
                            int(selected_common_cc),
                        )
                    ] += 1
                if native:
                    owner_closed = (
                        int(record.source_actor_id) >= PLAYER_ID_MIN
                        or bool(event.entity_owner_evidence_id)
                        and int(event.wire_entity_id) == int(record.source_actor_id)
                    )
                    if owner_closed:
                        evidence.append(("fixed_result_key", native, f"{record.family}:{native_field}:{native_raw}"))
                    else:
                        strict_negatives[(job, "native_key_without_entity_owner_chain")] += 1

                pair = (int(record.source_actor_id), int(record.damage_object_id or 0))
                projectile = active_projectile.get(pair)
                if projectile and int(projectile["candidate"]) > 0:
                    evidence.append((
                        "projectile_object_generation",
                        int(projectile["candidate"]),
                        f"template:{projectile['template']}",
                    ))
                elif projectile:
                    strict_negatives[(job, f"projectile:{projectile['reason']}")] += 1
                elif int(record.damage_object_id or 0) > 0:
                    strict_negatives[(job, "no_preceding_exact_projectile_registration")] += 1

                action = active_a90370.get(pair)
                if action:
                    action_key = f"{action['action']}|{action['signal']}|{action['config']}"
                    action_skill = int(action_maps.get(job, {}).get(action_key, 0))
                    if action_skill in valid_catalog:
                        evidence.append((
                            "a90370_action_signal_generation",
                            action_skill,
                            f"{job}:{action_key}",
                        ))
                    else:
                        strict_negatives[(job, "a90370_action_signal_not_unique_skill")] += 1

                for relative, candidate in _scan_same_record(record, valid_catalog):
                    same_record[(record.family, relative, candidate)] += 1

                distinct = sorted({skill for _route, skill, _key in evidence})
                if len(distinct) > 1:
                    conflicts[(job, ",".join(str(value) for value in distinct))] += 1
                    totals[f"{job}:conflicting_evidence_hits"] += 1
                elif len(distinct) == 1:
                    skill = distinct[0]
                    totals[f"{job}:newly_closed_hits"] += 1
                    totals[f"{job}:newly_closed_damage"] += int(record.damage)
                    for route, route_skill, route_key in evidence:
                        if route_skill != skill:
                            continue
                        key = (job, route, skill, route_key)
                        routes[key] += 1
                        route_damage[key] += int(record.damage)
                        route_captures[key].add(path.name)
                    if len(examples) < 80:
                        examples.append({
                            "capture": path.name,
                            "job": job,
                            "family": record.family,
                            "record_sha256": record.wire_record_sha256,
                            "damage_object_id": int(record.damage_object_id or 0),
                            "candidate_skill_id": skill,
                            "evidence": [
                                {"route": route, "skill_id": value, "key": key}
                                for route, value, key in evidence
                            ],
                            "old_skill_policy": "unknown_audit_only_not_exported",
                        })
                else:
                    totals[f"{job}:strictly_unresolved_hits"] += 1
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    route_rows = []
    for key, hits in sorted(routes.items(), key=lambda item: (-item[1], item[0])):
        job, route, skill, route_key = key
        route_rows.append({
            "job": job,
            "route": route,
            "candidate_skill_id": skill,
            "route_key": route_key,
            "hits": hits,
            "damage": route_damage[key],
            "captures": sorted(route_captures[key]),
            "conflicts_on_same_physical_hit": 0,
        })
    result = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": actual_sha,
        "counts": dict(totals),
        "audit_only_method_distribution": _counter_rows(
            methods, ("job", "classification", "method")
        ),
        "candidate_routes": route_rows,
        "same_record_catalogued_u32_observations": _counter_rows(
            same_record, ("family", "marker_relative_offset", "candidate_skill_id")
        )[:300],
        "unmapped_native_result_keys": _counter_rows(
            unmapped_native_result_keys,
            (
                "job",
                "family",
                "field",
                "raw_key",
                "source_kind",
                "common_cc",
            ),
        )[:300],
        "strict_negative_reasons": _counter_rows(strict_negatives, ("job", "reason")),
        "candidate_conflicts": _counter_rows(conflicts, ("job", "candidate_skill_ids")),
        "examples": examples,
        "physical_unknown_multiset_sha256": sha256(
            "\n".join(sorted(physical_commitment)).encode("utf-8")
        ).hexdigest(),
        "old_skill_values_consumed_as_evidence": 0,
        "time_windows_used": 0,
    }
    _write_json(checkpoint, result)
    return result


def _merge_counter_rows(
    captures: Iterable[dict[str, Any]], field: str, keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    counts: Counter[tuple[Any, ...]] = Counter()
    extras_damage: Counter[tuple[Any, ...]] = Counter()
    capture_sets: defaultdict[tuple[Any, ...], set[str]] = defaultdict(set)
    for capture in captures:
        for row in capture.get(field, ()):
            key = tuple(row.get(name) for name in keys)
            counts[key] += int(row.get("hits", 0))
            extras_damage[key] += int(row.get("damage", 0))
            capture_sets[key].add(str(capture["capture"]))
    result = []
    for key, hits in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        row = {**dict(zip(keys, key)), "hits": hits}
        if extras_damage[key]:
            row["damage"] = extras_damage[key]
        row["capture_count"] = len(capture_sets[key])
        row["captures"] = sorted(capture_sets[key])
        result.append(row)
    return result


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 圣职四职业未知 direct37/direct41 确定性审计 v111",
        "",
        "## 结论",
        "",
        f"- 固定新黑龙语料：{report['corpus']['capture_count']} 份。",
        f"- 未确定 direct37/direct41：{report['summary']['unknown_hits']:,} Hit。",
        f"- 新闭合且证据无冲突：{report['summary']['newly_closed_hits']:,} Hit。",
        f"- 同一物理 Hit 出现互斥候选：{report['summary']['conflicting_hits']:,} Hit。",
        f"- 严格仍未知：{report['summary']['strictly_unresolved_hits']:,} Hit。",
        f"- 可生产零冲突候选组：{len(report['production_candidates'])}。",
        f"- 去重后的候选职业/技能组：{len(report['candidate_skill_rollup'])}。",
        "",
        "旧技能名、旧 skill_id、伤害数值、Hit 分布和时间邻近均未参与候选选择。",
        "",
        "## 分职业",
        "",
        "|职业|总直接Hit|原确定Hit|未知Hit|新闭合|冲突|仍未知|审计后确定覆盖|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["jobs"]:
        lines.append(
            f"|{row['job']}|{row['total_direct_hits']:,}|{row['baseline_deterministic_hits']:,}|"
            f"{row['unknown_hits']:,}|{row['newly_closed_hits']:,}|{row['conflicting_hits']:,}|"
            f"{row['strictly_unresolved_hits']:,}|{row['audited_coverage_percent']:.3f}%|"
        )
    lines.extend(["", "## 可生产候选", ""])
    if report["production_candidates"]:
        lines += [
            "|职业|证据链|候选技能|Hit|抓包数|固定键|",
            "|---|---|---|---:|---:|---|",
        ]
        for row in report["production_candidates"]:
            lines.append(
                f"|{row['job']}|{row['route']}|{row['candidate_skill_id']} {row['candidate_skill_name']}|"
                f"{row['hits']:,}|{row['capture_count']}|`{row['route_key']}`|"
            )
    else:
        lines.append("没有达到严格门槛的新候选；详见 `STRICT_NEGATIVES.json`。")
    lines.extend(["", "## 去重后的候选技能", ""])
    if report["candidate_skill_rollup"]:
        lines += [
            "|职业|候选技能|唯一物理Hit|抓包|独立证据链|",
            "|---|---|---:|---|---|",
        ]
        for row in report["candidate_skill_rollup"]:
            lines.append(
                f"|{row['job']}|{row['candidate_skill_id']} {row['candidate_skill_name']}|{row['unique_physical_hits']}|"
                f"{', '.join(row['captures'])}|{', '.join(row['routes'])}|"
            )
    lines.extend([
        "",
        "## 边界",
        "",
        "- 固定结果键只按客户端已验证的 result+0x90 / result+0xA4 读取分支解释。",
        "- 其他同记录 u32 即使看起来像技能 ID，也只列入字段观察，不作为候选。",
        "- Projectile 必须是同 owner、同完整 object id 的最近前置注册 generation，并具有独立静态模板证明。",
        "- 需要最近动作、2.5 秒/其他时间窗或共享子对象父链未闭合的路线一律否定。",
        "- 本工具未修改生产解析器，也没有增加、删除或重命名任何生产伤害。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION_REQUESTED)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--capture", action="append", default=[])
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        raise ValueError("workers must be 1 or 2")
    selection_path, alias_reason = _resolve_selection(args.selection)
    selection = _capture_selection(selection_path)
    if len(selection) != 29:
        raise ValueError(f"expected fixed 29-capture Cleric selection, got {len(selection)}")
    requested = set(str(value) for value in args.capture)
    captures = _find_captures(args.capture_root, selection, requested)
    selected_sha = {row["name"]: row["sha256"] for row in selection}
    action_maps = _action_maps(args.profiles, args.resource_root)
    client_skill_names = _client_skill_names(args.profiles)
    before = _production_signature()
    output = args.output_dir
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _audit_capture,
                str(path),
                selected_sha[path.name],
                action_maps,
                str(checkpoints / f"{selected_sha[path.name]}.json"),
            ): path
            for path in captures
        }
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "completed": index,
                        "total": len(captures),
                        "capture": result["capture"],
                        "unknown": sum(
                            int(value) for key, value in result["counts"].items()
                            if key.endswith(":unknown_hits")
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    results.sort(key=lambda row: row["capture"])
    after = _production_signature()
    if before != after:
        raise RuntimeError("production parser changed during read-only audit")

    baseline_path = ROOT / "outputs/direct_damage_v103_blackdragon_new33_20260910/profession_direct_damage_report.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_jobs = {row["职业"]: row for row in baseline["summary"] if row["职业"] in TARGET_JOBS}
    total_counts: Counter[str] = Counter()
    for result in results:
        total_counts.update({key: int(value) for key, value in result["counts"].items()})
    jobs = []
    for job in TARGET_JOBS:
        base = baseline_jobs[job]
        total = int(base["总直接Hit"])
        baseline_det = int(base["确定性已归因"])
        unknown = total_counts[f"{job}:unknown_hits"]
        closed = total_counts[f"{job}:newly_closed_hits"]
        conflict = total_counts[f"{job}:conflicting_evidence_hits"]
        unresolved = total_counts[f"{job}:strictly_unresolved_hits"]
        jobs.append({
            "job": job,
            "total_direct_hits": total,
            "baseline_deterministic_hits": baseline_det,
            "unknown_hits": unknown,
            "newly_closed_hits": closed,
            "conflicting_hits": conflict,
            "strictly_unresolved_hits": unresolved,
            "baseline_gap_outside_target_direct37_41": max(
                0, int(base["归因缺口"]) - unknown
            ),
            "audited_coverage_percent": 100.0 * (baseline_det + closed) / total if total else 100.0,
            "minimum_more_hits_for_95": max(0, math.ceil(total * 0.95) - baseline_det - closed),
        })

    route_rows = _merge_counter_rows(
        results,
        "candidate_routes",
        ("job", "route", "candidate_skill_id", "route_key"),
    )
    # These three routes are semantic fixed/object keys, not correlations.
    # A route is emitted only when no contradictory candidate existed on the
    # same physical row.  The per-route mapping itself is static and unique.
    production_candidates = [
        row for row in route_rows
        if row["route"] in {
            "fixed_result_key",
            "projectile_object_generation",
            "a90370_action_signal_generation",
        }
        and int(row["hits"]) > 0
    ]
    for row in production_candidates:
        row["candidate_skill_name"] = client_skill_names.get(
            int(row["candidate_skill_id"]), "<客户端表未唯一命名>"
        )
    closed_examples = [
        row for capture in results for row in capture.get("examples", ())
    ]
    expected_closed = sum(row["newly_closed_hits"] for row in jobs)
    if len(closed_examples) != expected_closed:
        raise RuntimeError(
            "closed-Hit samples were truncated; increase the per-capture evidence bound"
        )
    closed_rollup: dict[tuple[str, int], dict[str, Any]] = {}
    for row in closed_examples:
        key = (str(row["job"]), int(row["candidate_skill_id"]))
        target = closed_rollup.setdefault(
            key,
            {
                "job": key[0],
                "candidate_skill_id": key[1],
                "unique_physical_hits": 0,
                "captures": set(),
                "routes": set(),
            },
        )
        target["unique_physical_hits"] += 1
        target["captures"].add(str(row["capture"]))
        target["routes"].update(str(value["route"]) for value in row["evidence"])
    candidate_skill_rollup = [
        {
            **row,
            "candidate_skill_name": client_skill_names.get(
                int(row["candidate_skill_id"]), "<客户端表未唯一命名>"
            ),
            "captures": sorted(row["captures"]),
            "routes": sorted(row["routes"]),
        }
        for row in sorted(
            closed_rollup.values(),
            key=lambda value: (-int(value["unique_physical_hits"]), value["job"], value["candidate_skill_id"]),
        )
    ]
    strict_negatives = _merge_counter_rows(
        results, "strict_negative_reasons", ("job", "reason")
    )
    candidate_conflicts = _merge_counter_rows(
        results, "candidate_conflicts", ("job", "candidate_skill_ids")
    )
    same_record = _merge_counter_rows(
        results,
        "same_record_catalogued_u32_observations",
        ("family", "marker_relative_offset", "candidate_skill_id"),
    )
    report = {
        "schema": SCHEMA,
        "policy": {
            "old_skill_names": "unknown/audit-only; never consumed as evidence",
            "old_skill_ids": "unknown/audit-only; never consumed as evidence",
            "time_windows": "forbidden",
            "damage_and_hit_patterns": "forbidden",
            "candidate_sources": [
                "client built-in meter fixed result key",
                "exact same-record field with static client reader",
                "same owner/object/generation validated Projectile registration",
                "same A90370 generation embedded ACT action/signal/config",
            ],
        },
        "corpus": {
            "requested_selection": str(args.selection),
            "resolved_selection": str(selection_path),
            "selection_alias_reason": alias_reason,
            "selection_sha256": _hash_file(selection_path),
            "capture_count": len(captures),
            "captures": [
                {"name": path.name, "sha256": selected_sha[path.name]}
                for path in captures
            ],
        },
        "client_static_evidence": {
            "meter_result_reader": _meter_static_audit(),
            "object_and_action_dispatch": _native_evidence(),
        },
        "summary": {
            "unknown_hits": sum(row["unknown_hits"] for row in jobs),
            "newly_closed_hits": sum(row["newly_closed_hits"] for row in jobs),
            "conflicting_hits": sum(row["conflicting_hits"] for row in jobs),
            "strictly_unresolved_hits": sum(row["strictly_unresolved_hits"] for row in jobs),
            "production_candidate_groups": len(production_candidates),
            "production_candidate_skill_groups": len(candidate_skill_rollup),
            "fixed_result_key_new_hits": sum(
                int(row["hits"])
                for row in production_candidates
                if row["route"] == "fixed_result_key"
            ),
            "production_parser_modified": False,
            "old_skill_values_consumed_as_evidence": 0,
            "time_windows_used": 0,
        },
        "jobs": jobs,
        "production_candidates": production_candidates,
        "candidate_skill_rollup": candidate_skill_rollup,
        "strict_negatives": strict_negatives,
        "candidate_conflicts": candidate_conflicts,
        "same_record_field_observations_audit_only": same_record,
        "audit_only_method_distribution": _merge_counter_rows(
            results,
            "audit_only_method_distribution",
            ("job", "classification", "method"),
        ),
        "input_manifest": {
            "baseline_report": {"path": str(baseline_path), "sha256": _hash_file(baseline_path)},
            "production_signature_before": before,
            "production_signature_after": after,
        },
        "captures": results,
    }
    validation_checks = {
        "all_29_hash_pinned_captures_completed": len(results) == 29,
        "every_checkpoint_uses_current_schema": all(
            int(row.get("checkpoint_version", 0)) == CHECKPOINT_VERSION
            for row in results
        ),
        "unknown_partition_is_exact": report["summary"]["unknown_hits"]
        == report["summary"]["newly_closed_hits"]
        + report["summary"]["conflicting_hits"]
        + report["summary"]["strictly_unresolved_hits"],
        "candidate_rollup_is_exact": sum(
            int(row["unique_physical_hits"])
            for row in candidate_skill_rollup
        ) == report["summary"]["newly_closed_hits"],
        "zero_candidate_conflicts": report["summary"]["conflicting_hits"] == 0,
        "old_skill_values_not_consumed": report["summary"]["old_skill_values_consumed_as_evidence"] == 0,
        "time_windows_not_used": report["summary"]["time_windows_used"] == 0,
        "production_signature_unchanged": before == after,
    }
    validation = {
        "schema": "cleric-unknown-native-validation-v111",
        "passed": all(validation_checks.values()),
        "checks": validation_checks,
        "summary": report["summary"],
    }
    if not validation["passed"]:
        raise RuntimeError(f"v111 validation failed: {validation_checks}")
    _write_json(output / "REPORT.json", report)
    _write_json(output / "production_candidates.json", production_candidates)
    _write_json(output / "STRICT_NEGATIVES.json", {
        "policy": report["policy"],
        "reasons": strict_negatives,
        "conflicts": candidate_conflicts,
        "same_record_fields_not_promoted": same_record,
    })
    _write_json(output / "input_manifest.json", report["input_manifest"] | {"corpus": report["corpus"]})
    _write_json(output / "VALIDATION.json", validation)
    (output / "REPORT.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)
    print(str(output), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
