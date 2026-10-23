from __future__ import annotations

"""Build a read-only static internal-attack -> displayed-skill evidence graph.

The tool deliberately excludes temporal proximity, damage-size similarity and
old runtime labels.  It accepts only exact numeric foreign keys or exact typed
resource chains from the legacy client tables/ACT files.  Runtime audit output
is attached as corroboration after the static graph has been built; it never
creates a static edge.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_skill_catalog,
)
from tools.build_attack_instance_catalog import (  # noqa: E402
    ActParseError,
    _canonical_skill_id,
    _is_player_combat_act_path,
    _resolve_projectile_act_path,
    _unique_action_continuations,
    _verified_player_form_aliases,
    parse_act,
)
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


DEFAULT_RESOURCE_ROOT = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "static_internal_attack_parent_v55_20260909"
DEFAULT_NATIVE_REPORT = (
    ROOT
    / "outputs"
    / "native_wire_v50_20260909"
    / "native_meter_correlations_merged.json"
)
DEFAULT_RUNTIME_BASELINE = (
    ROOT
    / "outputs"
    / "compact_entity_selectors_20260908"
    / "RUNTIME_RESULT.md"
)
DEFAULT_JOB_MAP = DATA_DIR / "modular" / "tables" / "job_map.json"

SIGNAL_DIRECT_DAMAGE = 4
SIGNAL_PROJECTILE = 15
SIGNAL_ENTITY_CREATE = frozenset({48, 126})

UNDER_95_JOBS = (
    "魔羽",
    "圣骑士",
    "十字军",
    "圣徒",
    "雷神",
    "重炮手",
    "机械大师",
    "炼金圣士",
    "药剂师",
    "黑暗萨满",
    "噬魂者",
    "影",
)

CALIBRATION_KEYS = frozenset({1353, 30612, 30615, 30616})


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def _is_action_key(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return any(character.isalpha() for character in text)
    return False


def _action_params(row: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for index in range(1, 11):
        for raw in str(row.get(f"_ProcessParam{index}", "") or "").split(";"):
            value = raw.strip()
            if _is_action_key(value):
                result.add(value)
    return result


def _job_lineage(
    job_rows: dict[int, dict[str, Any]], job_id: int
) -> tuple[int, ...]:
    result: list[int] = []
    seen: set[int] = set()
    current = int(job_id)
    while current > 0 and current not in seen and current in job_rows:
        seen.add(current)
        result.append(current)
        current = _as_int(job_rows[current].get("_ParentJob"))
    return tuple(reversed(result))


def _job_depth(job_rows: dict[int, dict[str, Any]], job_id: int) -> int:
    return len(_job_lineage(job_rows, job_id))


def _resolve_second_job_ids(
    job_config: dict[str, Any],
    display_catalog: dict[int, tuple[str, str, int]],
    skill_rows: dict[int, dict[str, Any]],
    job_rows: dict[int, dict[str, Any]],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for job in job_config:
        candidates = {
            _as_int(skill_rows.get(skill_id, {}).get("_NeedJob"))
            for skill_id, (_name, catalog_job, _generation)
            in display_catalog.items()
            if catalog_job == job
        }
        candidates = {value for value in candidates if value in job_rows}
        if candidates:
            result[job] = max(
                candidates,
                key=lambda value: (
                    _job_depth(job_rows, value),
                    _as_int(job_rows[value].get("_JobNumber")),
                    value,
                ),
            )
    return result


def _parse_runtime_baseline(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return result
    pattern = re.compile(
        r"^\|([^|]+)\|[^|]*\|[^|]*\|([0-9.]+)%\|([0-9,]+)/([0-9,]+)\|([0-9,]+)\|$"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        job = match.group(1).strip()
        result[job] = {
            "confirmed_coverage_percent": float(match.group(2)),
            "confirmed_hits": int(match.group(3).replace(",", "")),
            "named_hits": int(match.group(4).replace(",", "")),
            "hits_needed_for_95": int(match.group(5).replace(",", "")),
        }
    return result


def _input_signature(hashes: dict[str, str]) -> str:
    body = json.dumps(hashes, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _resolve_act_reference(
    resource_root: Path,
    file_name: str,
    all_act_paths_by_basename: dict[str, tuple[Path, ...]],
) -> tuple[Path | None, str]:
    normalized = str(file_name or "").strip().replace("\\", "/")
    if not normalized:
        return None, "empty_reference"
    direct = resource_root / Path(normalized)
    if direct.is_file():
        return direct, "exact_relative_path"
    weapon_direct = resource_root / "weapon" / Path(normalized)
    if weapon_direct.is_file():
        return weapon_direct, "exact_weapon_relative_path"
    basename = Path(normalized).name.casefold()
    candidates = list(all_act_paths_by_basename.get(basename, ()))
    folded = normalized.casefold().lstrip("/")
    suffix = [
        path
        for path in candidates
        if _relative(path, resource_root).casefold().endswith(folded)
    ]
    if len(suffix) == 1:
        return suffix[0], "unique_exact_suffix"
    if len(candidates) == 1:
        return candidates[0], "unique_basename"
    return None, "ambiguous_or_missing_reference"


def _typed_signal_summary(action: Any, projectile_ids: set[int]) -> dict[str, Any]:
    projectiles: set[int] = set()
    direct_damage_signals = 0
    for signal in action.signals:
        if signal.signal_type == SIGNAL_DIRECT_DAMAGE:
            direct_damage_signals += 1
        elif signal.signal_type == SIGNAL_PROJECTILE:
            value = int.from_bytes(signal.payload[:4], "little", signed=False)
            if value in projectile_ids:
                projectiles.add(value)
    return {
        "direct_damage_signals": direct_damage_signals,
        "projectile_templates": sorted(projectiles),
    }


def _candidate_branch(
    skill_id: int,
    display_catalog: dict[int, tuple[str, str, int]],
    job_config: dict[str, Any],
) -> str:
    display = display_catalog.get(skill_id)
    if display is None:
        return ""
    job = display[1]
    value = job_config.get(job, {})
    return str(value.get("一转分支", "") or job)


def _skill_label(
    skill_id: int,
    display_catalog: dict[int, tuple[str, str, int]],
    skill_rows: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    name, job, generation = display_catalog.get(
        skill_id,
        (
            str(skill_rows.get(skill_id, {}).get("_StaticName", "") or ""),
            "",
            0,
        ),
    )
    row = skill_rows.get(skill_id, {})
    return {
        "skill_id": skill_id,
        "name": name,
        "job": job,
        "generation": generation,
        "static_name": str(row.get("_StaticName", "") or ""),
        "need_job_id": _as_int(row.get("_NeedJob")),
        "base_skill_id": _as_int(row.get("_BaseSkillID")),
    }


def _applicable_second_jobs(
    skill_id: int,
    *,
    display_catalog: dict[int, tuple[str, str, int]],
    skill_rows: dict[int, dict[str, Any]],
    job_rows: dict[int, dict[str, Any]],
    second_job_ids: dict[str, int],
) -> list[str]:
    need_job = _as_int(skill_rows.get(skill_id, {}).get("_NeedJob"))
    catalog_job = display_catalog.get(skill_id, ("", "", 0))[1]
    result = []
    for job, leaf_id in second_job_ids.items():
        if need_job in _job_lineage(job_rows, leaf_id) or catalog_job == job:
            result.append(job)
    return result


def _load_native_observations(path: Path) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not path.is_file():
        return result
    payload = json.loads(path.read_text(encoding="utf-8"))
    for decision, key in (
        ("accepted_zero_conflict", "accepted_mappings"),
        ("rejected_conflict", "conflicting_mappings"),
    ):
        for row in payload.get(key, []):
            raw_key = _as_int(row.get("raw_key"))
            if raw_key <= 0:
                continue
            result[raw_key].append(
                {
                    "decision": decision,
                    "source_class": row.get("source_class", ""),
                    "field": row.get("field", ""),
                    "independent_hits": _as_int(row.get("independent_hits")),
                    "capture_support": _as_int(row.get("capture_support")),
                    "assigned_skills": row.get("assigned_skills", {}),
                    "jobs": row.get("jobs", {}),
                    "methods": row.get("methods", {}),
                }
            )
    return result


def build(
    resource_root: Path,
    native_report: Path,
    runtime_baseline_path: Path,
) -> dict[str, Any]:
    ext = resource_root / "ext"
    required = {
        "skilltable": ext / "skilltable_character.dnt",
        "jobtable": ext / "jobtable.dnt",
        "projectile": ext / "weapontable_projectile.dnt",
        "filetable": ext / "filetable.dnt",
        "monstertable": ext / "monstertable_quest.dnt",
        "monsterskill": ext / "monsterskilltable.dnt",
        "actortable": ext / "actortable.dnt",
        "summonskill": ext / "summonskilltable.dnt",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing required resources: {missing}")

    hashes: dict[str, str] = {}

    def load_table(label: str) -> list[dict[str, Any]]:
        path = required[label]
        hashes[_relative(path, resource_root)] = _sha256(path)
        return parse_dnt(path)

    skill_rows = {int(row["RowId"]): row for row in load_table("skilltable")}
    job_rows = {int(row["RowId"]): row for row in load_table("jobtable")}
    projectile_rows = {int(row["RowId"]): row for row in load_table("projectile")}
    file_rows = {int(row["RowId"]): row for row in load_table("filetable")}
    monster_rows = {int(row["RowId"]): row for row in load_table("monstertable")}
    monster_skill_rows = {
        int(row["RowId"]): row for row in load_table("monsterskill")
    }
    actor_rows = {int(row["RowId"]): row for row in load_table("actortable")}
    summon_rows = load_table("summonskill")

    level_rows_by_skill: dict[int, list[dict[str, Any]]] = defaultdict(list)
    level_paths = sorted(ext.glob("skillleveltable_character*.dnt"))
    for path in level_paths:
        hashes[_relative(path, resource_root)] = _sha256(path)
        for row in parse_dnt(path):
            item = dict(row)
            item["_SourceTable"] = path.name
            level_rows_by_skill[_as_int(row.get("_SkillIndex"))].append(item)

    display_catalog = _load_skill_catalog(str(DATA_DIR))
    job_config = json.loads(DEFAULT_JOB_MAP.read_text(encoding="utf-8"))
    second_job_ids = _resolve_second_job_ids(
        job_config, display_catalog, skill_rows, job_rows
    )

    base_skill_by_id = {
        skill_id: _as_int(row.get("_BaseSkillID"))
        for skill_id, row in skill_rows.items()
    }
    base_skill_by_id.update(_verified_player_form_aliases(skill_rows))

    player_action_families: dict[str, set[int]] = defaultdict(set)
    action_spellings: dict[str, set[str]] = defaultdict(set)
    player_action_sources: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for skill_id, rows in level_rows_by_skill.items():
        if _as_int(skill_rows.get(skill_id, {}).get("_NeedJob")) <= 0:
            continue
        canonical = _canonical_skill_id(skill_id, base_skill_by_id)
        for row in rows:
            if _as_int(row.get("_ApplyType")) != 0:
                continue
            for action in _action_params(row):
                folded = action.casefold()
                player_action_families[folded].add(canonical)
                action_spellings[folded].add(action)
                player_action_sources[(canonical, folded)].append(
                    {
                        "skill_id": skill_id,
                        "source_table": row["_SourceTable"],
                        "row_id": _as_int(row.get("RowId")),
                        "skill_level": _as_int(row.get("_SkillLevel")),
                        "apply_type": _as_int(row.get("_ApplyType")),
                    }
                )
    identifying_actions = {
        action for action, families in player_action_families.items()
        if len(families) == 1
    }

    all_act_paths = sorted(resource_root.rglob("*.act"))
    act_paths_by_basename_mut: dict[str, list[Path]] = defaultdict(list)
    for path in all_act_paths:
        act_paths_by_basename_mut[path.name.casefold()].append(path)
    act_paths_by_basename = {
        key: tuple(value) for key, value in act_paths_by_basename_mut.items()
    }

    parsed_acts: dict[Path, Any | None] = {}
    act_errors: list[dict[str, str]] = []

    def load_act(path: Path) -> Any | None:
        if path not in parsed_acts:
            try:
                parsed_acts[path] = parse_act(path)
                hashes[_relative(path, resource_root)] = _sha256(path)
            except (OSError, ActParseError, ValueError) as exc:
                parsed_acts[path] = None
                act_errors.append(
                    {"path": _relative(path, resource_root), "error": str(exc)}
                )
        return parsed_acts[path]

    parent_projectile_edges: list[dict[str, Any]] = []
    parent_entity_edges: list[dict[str, Any]] = []
    player_root = resource_root / "char" / "player"
    player_act_paths = [
        path for path in sorted(player_root.rglob("*.act"))
        if _is_player_combat_act_path(path, player_root)
    ]
    for path in player_act_paths:
        parsed = load_act(path)
        if parsed is None:
            continue
        continuations = _unique_action_continuations(parsed, player_action_families)
        for entry, actions in continuations.items():
            if entry not in identifying_actions:
                continue
            canonical = next(iter(player_action_families[entry]))
            for action in actions:
                for signal in action.signals:
                    payload0 = int.from_bytes(
                        signal.payload[:4], "little", signed=False
                    )
                    common = {
                        "parent_skill_id": canonical,
                        "entry_action": sorted(action_spellings[entry])[0],
                        "act_action": action.name,
                        "player_act": _relative(path, resource_root),
                        "signal_type": signal.signal_type,
                        "signal_order": signal.order,
                        "start_frame": signal.start_frame,
                        "end_frame": signal.end_frame,
                        "payload0": payload0,
                        "source_level_rows": player_action_sources[(canonical, entry)],
                    }
                    if signal.signal_type == SIGNAL_PROJECTILE and payload0 in projectile_rows:
                        parent_projectile_edges.append(common)
                    elif signal.signal_type in SIGNAL_ENTITY_CREATE and payload0 in monster_rows:
                        parent_entity_edges.append(common)

    def dedupe_dicts(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
        result: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            result.setdefault(tuple(row.get(key) for key in keys), row)
        return [result[key] for key in sorted(result, key=lambda value: tuple(map(str, value)))]

    parent_projectile_edges = dedupe_dicts(
        parent_projectile_edges,
        ("parent_skill_id", "entry_action", "act_action", "player_act", "signal_type", "payload0"),
    )
    parent_entity_edges = dedupe_dicts(
        parent_entity_edges,
        ("parent_skill_id", "entry_action", "act_action", "player_act", "signal_type", "payload0"),
    )

    projectile_children: dict[int, set[int]] = defaultdict(set)
    projectile_act_info: dict[int, dict[str, Any]] = {}
    for template_id, row in projectile_rows.items():
        file_id = _as_int(row.get("_ActName"))
        file_name = str(file_rows.get(file_id, {}).get("_FileName", "") or "")
        if not file_name:
            continue
        path = _resolve_projectile_act_path(
            resource_root,
            file_name,
            {
                key: tuple(value)
                for key, value in act_paths_by_basename.items()
                if any("weapon" in part.casefold() for candidate in value for part in candidate.parts)
            },
        )
        if not path.is_file():
            continue
        parsed = load_act(path)
        if parsed is None:
            continue
        children: set[int] = set()
        direct = 0
        for action in parsed.actions:
            direct += sum(
                signal.signal_type == SIGNAL_DIRECT_DAMAGE
                for signal in action.signals
            )
            for signal in action.signals:
                if signal.signal_type != SIGNAL_PROJECTILE:
                    continue
                child = int.from_bytes(signal.payload[:4], "little", signed=False)
                if child in projectile_rows and child != template_id:
                    children.add(child)
        projectile_children[template_id].update(children)
        projectile_act_info[template_id] = {
            "act_file_id": file_id,
            "act_file_reference": file_name,
            "resolved_act": _relative(path, resource_root),
            "direct_damage_signals": direct,
            "child_projectile_templates": sorted(children),
        }

    projectile_parents: dict[int, set[int]] = defaultdict(set)
    projectile_paths: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for edge in parent_projectile_edges:
        root_template = _as_int(edge["payload0"])
        parent = _as_int(edge["parent_skill_id"])
        pending = [(root_template, [root_template])]
        visited: set[int] = set()
        while pending:
            template_id, path_ids = pending.pop()
            if template_id in visited:
                continue
            visited.add(template_id)
            projectile_parents[template_id].add(parent)
            projectile_paths[(template_id, parent)].append(
                {
                    "entry_action": edge["entry_action"],
                    "player_act": edge["player_act"],
                    "signal_type": edge["signal_type"],
                    "projectile_path": path_ids,
                }
            )
            for child in projectile_children.get(template_id, ()):
                pending.append((child, [*path_ids, child]))

    object_template_relations: list[dict[str, Any]] = []
    for template_id, parents in sorted(projectile_parents.items()):
        branches = {
            _candidate_branch(parent, display_catalog, job_config)
            for parent in parents
        } - {""}
        if len(parents) == 1:
            classification = "unique_typed_projectile_path"
            usable = True
        elif len(branches) == 1:
            classification = "same_profession_ambiguous"
            usable = False
        else:
            classification = "cross_profession_ambiguous"
            usable = False
        object_template_relations.append(
            {
                "projectile_template_id": template_id,
                "classification": classification,
                "safe_for_damage_object_parent_lookup": usable,
                "parent_candidates": [
                    _skill_label(parent, display_catalog, skill_rows)
                    for parent in sorted(parents)
                ],
                "paths": sorted(
                    [
                        value
                        for parent in sorted(parents)
                        for value in projectile_paths[(template_id, parent)]
                    ],
                    key=lambda value: (
                        value["projectile_path"],
                        value["player_act"].casefold(),
                        value["entry_action"].casefold(),
                        value["signal_type"],
                    ),
                ),
                "projectile_act": projectile_act_info.get(template_id, {}),
            }
        )

    # Build exact entity self-skill paths.  The MonsterSkill table can be shared
    # by many entities, so its membership is never enough by itself: the
    # internal SkillLevel action must also exactly exist in that entity's ACT.
    composite_evidence: list[dict[str, Any]] = []
    entity_resolution_failures: list[dict[str, Any]] = []
    for edge in parent_entity_edges:
        parent = _as_int(edge["parent_skill_id"])
        template_id = _as_int(edge["payload0"])
        monster = monster_rows[template_id]
        actor_id = _as_int(monster.get("_ActorTableID"))
        skill_table_id = _as_int(monster.get("_SkillTable"))
        actor = actor_rows.get(actor_id, {})
        act_file_id = _as_int(actor.get("_ActName"))
        act_reference = str(file_rows.get(act_file_id, {}).get("_FileName", "") or "")
        act_path, resolution = _resolve_act_reference(
            resource_root, act_reference, act_paths_by_basename
        )
        if act_path is None:
            entity_resolution_failures.append(
                {
                    "parent_skill_id": parent,
                    "monster_template_id": template_id,
                    "actor_table_id": actor_id,
                    "monster_skill_table_id": skill_table_id,
                    "actor_act_reference": act_reference,
                    "resolution": resolution,
                }
            )
            continue
        parsed = load_act(act_path)
        if parsed is None:
            continue
        entity_actions = {action.name.casefold(): action for action in parsed.actions}
        monster_skill_table = monster_skill_rows.get(skill_table_id, {})
        for slot, raw_skill in monster_skill_table.items():
            if not slot.startswith("_SkillIndex"):
                continue
            internal_skill = _as_int(raw_skill)
            if internal_skill <= 0:
                continue
            exact_actions: dict[str, dict[str, Any]] = {}
            level_sources: list[dict[str, Any]] = []
            for level_row in level_rows_by_skill.get(internal_skill, ()): 
                for action_name in _action_params(level_row):
                    action = entity_actions.get(action_name.casefold())
                    if action is None:
                        continue
                    exact_actions[action.name] = _typed_signal_summary(
                        action, set(projectile_rows)
                    )
                    level_sources.append(
                        {
                            "source_table": level_row["_SourceTable"],
                            "row_id": _as_int(level_row.get("RowId")),
                            "skill_level": _as_int(level_row.get("_SkillLevel")),
                            "apply_type": _as_int(level_row.get("_ApplyType")),
                            "action": action_name,
                        }
                    )
            if not exact_actions:
                continue
            level_sources.sort(
                key=lambda value: (
                    value["source_table"],
                    value["row_id"],
                    value["skill_level"],
                    value["apply_type"],
                    value["action"].casefold(),
                )
            )
            composite_evidence.append(
                {
                    "kind": "typed_entity_monsterskill_exact_act",
                    "internal_attack_key": internal_skill,
                    "parent_skill_id": parent,
                    "player_entry_action": edge["entry_action"],
                    "player_act_action": edge["act_action"],
                    "player_act": edge["player_act"],
                    "entity_create_signal_type": edge["signal_type"],
                    "monster_template_id": template_id,
                    "actor_table_id": actor_id,
                    "monster_skill_table_id": skill_table_id,
                    "monster_skill_slot": slot,
                    "actor_act_reference": act_reference,
                    "actor_act_resolution": resolution,
                    "actor_act": _relative(act_path, resource_root),
                    "exact_entity_actions": [
                        {"action": key, **value}
                        for key, value in sorted(exact_actions.items())
                    ],
                    "internal_skilllevel_rows": level_sources,
                }
            )
    composite_evidence = dedupe_dicts(
        composite_evidence,
        (
            "internal_attack_key",
            "parent_skill_id",
            "monster_template_id",
            "actor_table_id",
            "monster_skill_table_id",
            "monster_skill_slot",
            "actor_act",
        ),
    )
    entity_group_keys: dict[tuple[int, int, str], set[int]] = defaultdict(set)
    for row in composite_evidence:
        entity_group_keys[
            (
                row["parent_skill_id"],
                row["monster_template_id"],
                row["actor_act"],
            )
        ].add(row["internal_attack_key"])
    for row in composite_evidence:
        members = sorted(
            entity_group_keys[
                (
                    row["parent_skill_id"],
                    row["monster_template_id"],
                    row["actor_act"],
                )
            ]
        )
        row["entity_exact_internal_attack_keys"] = members
        row["safe_composite_edge"] = len(members) == 1
        row["composite_decision_reason"] = (
            "该实体 ACT 只闭合一个 MonsterSkill 内部攻击键"
            if len(members) == 1
            else "同一实体 ACT 闭合多个内部攻击键；创建技能不能区分实体后续自主/指令攻击"
        )

    exact_fk_evidence: list[dict[str, Any]] = []
    range_spec_evidence: list[dict[str, Any]] = []
    for row in summon_rows:
        spec = str(row.get("_MonsterSkill", "") or "").strip()
        parent = _canonical_skill_id(
            _as_int(row.get("_SkillIndex")), base_skill_by_id
        )
        if re.fullmatch(r"\d+", spec):
            exact_fk_evidence.append(
                {
                    "kind": "summonskill_exact_numeric_fk",
                    "internal_attack_key": int(spec),
                    "parent_skill_id": parent,
                    "summonskill_row_id": _as_int(row.get("RowId")),
                    "raw_parent_skill_id": _as_int(row.get("_SkillIndex")),
                }
            )
        else:
            numbers = [int(value) for value in re.findall(r"\d+", spec)]
            range_spec_evidence.append(
                {
                    "kind": "summonskill_non_singleton_spec",
                    "monster_skill_spec": spec,
                    "numeric_endpoints": numbers,
                    "parent_skill_id": parent,
                    "summonskill_row_id": _as_int(row.get("RowId")),
                    "usable_alone": False,
                    "reason": "非单值字段的范围/列表语义未由表结构声明，不能展开成唯一外键",
                }
            )

    range_by_key: dict[int, list[dict[str, Any]]] = defaultdict(list)
    composite_keys = {row["internal_attack_key"] for row in composite_evidence}
    for key in composite_keys:
        for row in range_spec_evidence:
            endpoints = row["numeric_endpoints"]
            if len(endpoints) == 2 and endpoints[0] <= key <= endpoints[1]:
                range_by_key[key].append(row)

    # EffectClassValue records are structured and exact, but the DNT schema does
    # not type their first integer as a parent/child FK.  Keep them as explicit
    # corroboration and never promote them alone.
    structured_refs: list[dict[str, Any]] = []
    for parent_skill, rows in level_rows_by_skill.items():
        if _as_int(skill_rows.get(parent_skill, {}).get("_NeedJob")) <= 0:
            continue
        canonical_parent = _canonical_skill_id(parent_skill, base_skill_by_id)
        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        for row in rows:
            if _as_int(row.get("_ApplyType")) != 0:
                continue
            for field_index in range(1, 11):
                field = f"_EffectClassValue{field_index}"
                value = str(row.get(field, "") or "").strip()
                if not value:
                    continue
                for segment in value.split(";"):
                    first = segment.split(",", 1)[0].strip()
                    if not re.fullmatch(r"\d+", first):
                        continue
                    child = int(first)
                    if child == canonical_parent or child not in skill_rows:
                        continue
                    key = (child, field)
                    item = grouped.setdefault(
                        key,
                        {
                            "kind": "skilllevel_structured_effect_reference",
                            "internal_attack_key": child,
                            "parent_skill_id": canonical_parent,
                            "field": field,
                            "source_rows": [],
                            "usable_alone": False,
                            "reason": "字段值明确引用该数字，但客户端表未把该位置声明为技能父子外键",
                        },
                    )
                    item["source_rows"].append(
                        {
                            "source_table": row["_SourceTable"],
                            "row_id": _as_int(row.get("RowId")),
                            "skill_level": _as_int(row.get("_SkillLevel")),
                            "raw_value": value,
                        }
                    )
        for item in grouped.values():
            item["distinct_levels"] = len(
                {row["skill_level"] for row in item["source_rows"]}
            )
            deduped_sources: dict[tuple[int, int, str], dict[str, Any]] = {}
            for source in item["source_rows"]:
                deduped_sources.setdefault(
                    (
                        source["row_id"],
                        source["skill_level"],
                        source["raw_value"],
                    ),
                    source,
                )
            item["source_rows"] = list(deduped_sources.values())[:6]
            structured_refs.append(item)

    native_observations = _load_native_observations(native_report)
    all_keys = (
        {row["internal_attack_key"] for row in exact_fk_evidence}
        | {row["internal_attack_key"] for row in composite_evidence}
        | {row["internal_attack_key"] for row in structured_refs}
        | set(native_observations)
        | set(CALIBRATION_KEYS)
    )

    exact_by_key: dict[int, list[dict[str, Any]]] = defaultdict(list)
    composite_by_key: dict[int, list[dict[str, Any]]] = defaultdict(list)
    safe_composite_by_key: dict[int, list[dict[str, Any]]] = defaultdict(list)
    structured_by_key: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in exact_fk_evidence:
        exact_by_key[row["internal_attack_key"]].append(row)
    for row in composite_evidence:
        composite_by_key[row["internal_attack_key"]].append(row)
        if row["safe_composite_edge"]:
            safe_composite_by_key[row["internal_attack_key"]].append(row)
    for row in structured_refs:
        structured_by_key[row["internal_attack_key"]].append(row)

    relations: list[dict[str, Any]] = []
    for internal_key in sorted(all_keys):
        exact_candidates = {
            row["parent_skill_id"] for row in exact_by_key[internal_key]
        }
        composite_candidates = {
            row["parent_skill_id"] for row in safe_composite_by_key[internal_key]
        }
        structured_candidates = {
            row["parent_skill_id"] for row in structured_by_key[internal_key]
        }
        range_candidates = {
            row["parent_skill_id"] for row in range_by_key[internal_key]
        }
        entity_family_candidates = {
            row["parent_skill_id"]
            for row in composite_by_key[internal_key]
            if not row["safe_composite_edge"]
        }
        self_candidate = (
            internal_key
            if internal_key in display_catalog
            and _as_int(skill_rows.get(internal_key, {}).get("_NeedJob")) > 0
            else None
        )
        strong_candidates = exact_candidates | composite_candidates
        weak_candidates = range_candidates | entity_family_candidates
        if self_candidate is not None:
            self_jobs = set(
                _applicable_second_jobs(
                    self_candidate,
                    display_catalog=display_catalog,
                    skill_rows=skill_rows,
                    job_rows=job_rows,
                    second_job_ids=second_job_ids,
                )
            )
            compatible_structured_candidates = {
                parent
                for parent in structured_candidates
                if self_jobs
                & set(
                    _applicable_second_jobs(
                        parent,
                        display_catalog=display_catalog,
                        skill_rows=skill_rows,
                        job_rows=job_rows,
                        second_job_ids=second_job_ids,
                    )
                )
            }
            runtime_assignments = {
                _as_int(skill_id)
                for observation in native_observations.get(internal_key, ())
                for skill_id in observation.get("assigned_skills", {})
            }
            structured_can_compete = (
                internal_key in CALIBRATION_KEYS
                or any(value != self_candidate for value in runtime_assignments)
            )
            if not structured_can_compete:
                compatible_structured_candidates = set()
        else:
            compatible_structured_candidates = set()
        all_candidates = strong_candidates | weak_candidates | compatible_structured_candidates
        if self_candidate is not None:
            all_candidates.add(self_candidate)

        competing_self = (
            self_candidate is not None
            and any(parent != self_candidate for parent in all_candidates)
        )
        strong_conflict = len(strong_candidates) > 1
        if exact_candidates and len(exact_candidates) == 1 and not strong_conflict and not competing_self:
            classification = "unique_foreign_key"
            usable = True
            reason = "SummonSkillTable 单值数值外键唯一，且无静态强证据冲突"
        elif composite_candidates and len(composite_candidates) == 1 and not strong_conflict and not competing_self:
            classification = "unique_composite_static_path"
            usable = True
            reason = "唯一玩家动作经类型化实体生成、MonsterSkill 表和实体 ACT 精确动作闭合"
        elif len(all_candidates) > 1:
            branches = {
                _candidate_branch(value, display_catalog, job_config)
                for value in all_candidates
            } - {""}
            if len(branches) == 1:
                classification = "same_profession_ambiguous"
                reason = "同一一转分支存在多个可显示父技能，原始键不能脱离对象/实体上下文全局改写"
            else:
                classification = "cross_profession_ambiguous"
                reason = "静态候选跨职业或跨分支，不能安全改写"
            usable = False
        elif self_candidate is not None and not (
            exact_candidates
            or composite_candidates
            or compatible_structured_candidates
            or weak_candidates
        ):
            classification = "identity_only"
            usable = True
            reason = "原始键本身就是唯一可显示技能；无需父子改写"
        elif structured_candidates or weak_candidates:
            classification = "corroboration_only_unusable"
            usable = False
            reason = "仅有结构化参数、非单值范围或多攻击实体创建关系，不能单独证明显示父技能"
        else:
            classification = "no_static_parent"
            usable = False
            reason = "指定静态表和 ACT 中没有形成可证明父子链"

        key_domains: set[str] = set()
        if composite_by_key[internal_key] or exact_by_key[internal_key]:
            key_domains.add("entity_self_skill")
        for observation in native_observations.get(internal_key, ()):
            key_domains.add(
                f"direct37_{observation['source_class']}_{observation['field']}"
            )
        if structured_by_key[internal_key]:
            key_domains.add("skilllevel_structured_reference")
        if self_candidate is not None:
            key_domains.add("displayed_player_skill_identity")

        candidate_rows = []
        for parent in sorted(all_candidates):
            applicable = _applicable_second_jobs(
                parent,
                display_catalog=display_catalog,
                skill_rows=skill_rows,
                job_rows=job_rows,
                second_job_ids=second_job_ids,
            )
            candidate_rows.append(
                {
                    **_skill_label(parent, display_catalog, skill_rows),
                    "applicable_second_jobs": applicable,
                }
            )

        relations.append(
            {
                "internal_attack_key": internal_key,
                "key_domains": sorted(key_domains),
                "classification": classification,
                "safe_for_static_parent_lookup": usable,
                "parent_candidates": candidate_rows,
                "self_display_candidate": (
                    _skill_label(self_candidate, display_catalog, skill_rows)
                    if self_candidate is not None else None
                ),
                "evidence": {
                    "exact_foreign_keys": exact_by_key[internal_key],
                    "composite_static_paths": composite_by_key[internal_key],
                    "non_singleton_summonskill_matches": range_by_key[internal_key],
                    "structured_references": structured_by_key[internal_key],
                },
                "runtime_observations_not_used_to_create_edge": native_observations.get(
                    internal_key, []
                ),
                "decision_reason": reason,
            }
        )

    relation_by_key = {row["internal_attack_key"]: row for row in relations}
    runtime_baseline = _parse_runtime_baseline(runtime_baseline_path)
    under95: list[dict[str, Any]] = []
    for job in UNDER_95_JOBS:
        safe: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        for row in relations:
            candidates = [
                candidate
                for candidate in row["parent_candidates"]
                if job in candidate["applicable_second_jobs"]
            ]
            if not candidates:
                continue
            compact = {
                "internal_attack_key": row["internal_attack_key"],
                "classification": row["classification"],
                "parent_candidates": [
                    {
                        "skill_id": value["skill_id"],
                        "name": value["name"],
                        "job": value["job"],
                    }
                    for value in candidates
                ],
                "runtime_independent_hits": sum(
                    _as_int(value.get("independent_hits"))
                    for value in row["runtime_observations_not_used_to_create_edge"]
                    if job in value.get("jobs", {})
                ),
            }
            # Identity rows are already handled by the ordinary packet skill
            # catalog and are not a newly discovered parent/child relation.
            if (
                row["safe_for_static_parent_lookup"]
                and row["classification"] != "identity_only"
            ):
                safe.append(compact)
            elif row["classification"] in {
                "same_profession_ambiguous",
                "cross_profession_ambiguous",
                "corroboration_only_unusable",
            } and (
                any(
                    job in observation.get("jobs", {})
                    for observation in row["runtime_observations_not_used_to_create_edge"]
                )
                or row["internal_attack_key"] in CALIBRATION_KEYS
            ):
                ambiguous.append(compact)

        object_safe = []
        for row in object_template_relations:
            candidates = []
            for parent in row["parent_candidates"]:
                jobs = _applicable_second_jobs(
                    parent["skill_id"],
                    display_catalog=display_catalog,
                    skill_rows=skill_rows,
                    job_rows=job_rows,
                    second_job_ids=second_job_ids,
                )
                if job in jobs:
                    candidates.append(
                        {"skill_id": parent["skill_id"], "name": parent["name"]}
                    )
            if candidates and row["safe_for_damage_object_parent_lookup"]:
                object_safe.append(
                    {
                        "projectile_template_id": row["projectile_template_id"],
                        "parent_candidates": candidates,
                    }
                )
        under95.append(
            {
                "job": job,
                "baseline": runtime_baseline.get(job, {}),
                "safe_internal_attack_relations": safe,
                "ambiguous_or_unusable_relations": ambiguous,
                "safe_projectile_object_relations": object_safe,
            }
        )

    calibration = {
        str(key): relation_by_key.get(
            key,
            {
                "internal_attack_key": key,
                "classification": "no_static_parent",
                "safe_for_static_parent_lookup": False,
            },
        )
        for key in sorted(CALIBRATION_KEYS)
    }

    def has_composite(
        key: int, parent: int, template: int, action_name: str
    ) -> bool:
        row = relation_by_key[key]
        return any(
            evidence["parent_skill_id"] == parent
            and evidence["monster_template_id"] == template
            and any(
                action["action"] == action_name
                for action in evidence["exact_entity_actions"]
            )
            for evidence in row["evidence"]["composite_static_paths"]
        )

    validation_checks = {
        "raw_1353_contains_1208_structured_reference": any(
            row["parent_skill_id"] == 1208 and row["field"] == "_EffectClassValue4"
            for row in relation_by_key[1353]["evidence"]["structured_references"]
        ),
        "raw_1353_is_not_global_rewrite": (
            relation_by_key[1353]["classification"] == "same_profession_ambiguous"
            and not relation_by_key[1353]["safe_for_static_parent_lookup"]
        ),
        "30615_exact_fk_row46_to_3412": any(
            row["summonskill_row_id"] == 46 and row["parent_skill_id"] == 3412
            for row in relation_by_key[30615]["evidence"]["exact_foreign_keys"]
        ),
        "30612_entity30001_action_skill_shoot_to_3409": has_composite(
            30612, 3409, 30001, "Skill_Shoot"
        ),
        "30616_entity30501_action_skill_shoot_ex_to_3409": has_composite(
            30616, 3409, 30501, "Skill_Shoot_EX"
        ),
        "all_12_under95_jobs_reported": len(under95) == 12,
    }
    if not all(validation_checks.values()):
        failed = [key for key, value in validation_checks.items() if not value]
        raise RuntimeError(f"static parent calibration failed: {failed}")

    class_counts = Counter(row["classification"] for row in relations)
    object_class_counts = Counter(
        row["classification"] for row in object_template_relations
    )
    input_hashes = dict(sorted(hashes.items()))
    return {
        "schema": "static-internal-attack-parent-v55.1",
        "generated_by": _relative(Path(__file__).resolve(), ROOT),
        "scope": {
            "project_root": str(ROOT.resolve()),
            "resource_root": str(resource_root.resolve()),
            "production_code_modified": False,
            "temporal_proximity_used": False,
            "damage_similarity_used": False,
            "runtime_labels_create_static_edges": False,
            "target_under_95_jobs": list(UNDER_95_JOBS),
        },
        "definitions": {
            "unique_foreign_key": "单值数值外键直接连接内部攻击键与玩家技能；仍需运行时来源/代次检查。",
            "unique_composite_static_path": "唯一玩家动作 → 类型化实体生成 → MonsterSkill 表 → 实体 ACT 精确动作。",
            "same_profession_ambiguous": "候选均在同一一转分支，但原始键不能区分具体显示技能。",
            "cross_profession_ambiguous": "候选跨职业/分支，禁止自动应用。",
            "corroboration_only_unusable": "存在明确资源引用，但字段语义不足以单独证明技能父子关系。",
            "identity_only": "原始键本身就是可显示技能，不需要父子改写。",
            "projectile_object_relation": "玩家动作 Signal 15 → Projectile.RowId，并递归跟随 Projectile ACT 的 Signal 15 子对象；用于 damage_object_id，不等同于 direct37 primary90。",
        },
        "summary": {
            "skilltable_rows": len(skill_rows),
            "skilllevel_rows": sum(map(len, level_rows_by_skill.values())),
            "player_act_files": len(player_act_paths),
            "parsed_act_files": sum(value is not None for value in parsed_acts.values()),
            "act_parse_errors": len(act_errors),
            "unique_player_action_keys": len(identifying_actions),
            "typed_player_projectile_edges": len(parent_projectile_edges),
            "typed_player_entity_edges": len(parent_entity_edges),
            "entity_composite_paths": len(composite_evidence),
            "exact_summonskill_foreign_keys": len(exact_fk_evidence),
            "non_singleton_summonskill_specs": len(range_spec_evidence),
            "structured_effect_references": len(structured_refs),
            "internal_attack_relation_classifications": dict(sorted(class_counts.items())),
            "projectile_object_relation_classifications": dict(sorted(object_class_counts.items())),
            "under_95_jobs_with_safe_internal_relation": sum(
                bool(row["safe_internal_attack_relations"]) for row in under95
            ),
            "under_95_jobs_with_safe_projectile_relation": sum(
                bool(row["safe_projectile_object_relations"]) for row in under95
            ),
            "validation_checks_passed": sum(validation_checks.values()),
            "validation_checks_total": len(validation_checks),
        },
        "validation_checks": validation_checks,
        "calibration": calibration,
        "under_95_jobs": under95,
        "internal_attack_relations": relations,
        "projectile_object_relations": object_template_relations,
        "parent_entity_edges": parent_entity_edges,
        "parent_projectile_edges": parent_projectile_edges,
        "non_singleton_summonskill_specs_unusable_alone": range_spec_evidence,
        "entity_resolution_failures": entity_resolution_failures,
        "act_parse_errors": act_errors,
        "sources": {
            "native_runtime_report": str(native_report.resolve()),
            "runtime_coverage_baseline": str(runtime_baseline_path.resolve()),
            "input_hashes": input_hashes,
            "input_signature_sha256": _input_signature(input_hashes),
        },
    }


def _format_skill_candidates(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "—"
    return " / ".join(
        f"{row['skill_id']} {row.get('name', '')}".strip() for row in candidates
    )


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    calibration = payload["calibration"]
    lines = [
        "# 内部攻击键 → 玩家显示技能：静态父子关系专项 v55",
        "",
        "## 结论",
        "",
        "本专项只从旧客户端的 SkillTable、SkillLevel、玩家/实体 ACT、Projectile、"
        "MonsterSkill 及其直接索引表建立关系。**没有使用施放/伤害时间邻近、伤害数值相似、"
        "旧技能名或同职业唯一性来生成父子边。**生产解析器、评分和界面均未修改。",
        "",
        f"- 内部攻击键关系：{len(payload['internal_attack_relations'])} 条；分类为 "
        + "、".join(f"`{key}` {value}" for key, value in summary["internal_attack_relation_classifications"].items())
        + "。",
        f"- Projectile 对象关系：{len(payload['projectile_object_relations'])} 条；"
        f"其中唯一类型化对象链 {summary['projectile_object_relation_classifications'].get('unique_typed_projectile_path', 0)} 条。",
        f"- 12 个低覆盖职业中，{summary['under_95_jobs_with_safe_internal_relation']} 个存在可用 self-skill/primary90 静态关系，"
        f"{summary['under_95_jobs_with_safe_projectile_relation']} 个存在可用 damage_object Projectile 关系。",
        "",
        "这里的“可用”表示静态父子关系已经唯一；运行时仍必须校验对象 ID、实体模板、主人和代次，"
        "不能把一个实体模板的全部伤害无条件归给创建技能。",
        "",
        "## 三条重点校准",
        "",
        "| 内部键 | 静态结论 | 静态父技能唯一 | 玩家技能候选 |",
        "|---:|---|:---:|---|",
    ]
    for key in (1353, 30615, 30612, 30616):
        row = calibration[str(key)]
        lines.append(
            f"| `{key}` | `{row['classification']}` | "
            f"{'是' if row.get('safe_for_static_parent_lookup') else '否'} | "
            f"{_format_skill_candidates(row.get('parent_candidates', []))} |"
        )
    lines.extend(
        [
            "",
            "### `1353 → 1208` 为什么不能做全局替换",
            "",
            "`skillleveltable_characterarcher.dnt` 中，玩家技能 `1208 集中射击` 的 "
            "`_EffectClassValue4` 在每个等级都直接写有 `1353,...`，所以 `1353` 与 `1208` 的父子关系"
            "不是时间推断。同时 `skilltable_character.dnt` 又把 `1353` 定义为可显示技能 `爆炸箭矢`。"
            "同一结构字段还在多个箭神技能中引用 `1353`，因此单看 `direct37 primary90=1353` "
            "存在多个同一箭神分支候选；只有再带上 `damage_object_id → Projectile 模板`时，"
            "才能在集中射击对象上下文中唯一选择 `1208`，绝不能把所有 `1353` 全局改写成 `1208`。",
            "",
            "### `30615 → 3412`",
            "",
            "`summonskilltable.dnt` 第 46 行直接给出 `_MonsterSkill=30615`、`_SkillIndex=3412`，"
            "属于单值数值外键。并且 `3412 奇迹之手` 的玩家 ACT 通过类型 48 信号生成 `30401`，"
            "该实体的 MonsterSkill 表包含 `30615`，实体 ACT 又精确包含其 SkillLevel 动作 `Attack`。"
            "两条静态链指向同一父技能。",
            "",
            "### `30612/30616 → 3409`",
            "",
            "`3409 雷电之手` 的玩家动作通过类型 48 信号生成普通实体 `30001`，"
            "其 MonsterSkill 表中的 `30612` 以 SkillLevel 动作 `Skill_Shoot` 精确命中实体 ACT；"
            "`3502 雷电之手EX` 通过 `_BaseSkillID=3409` 归一后生成 `30501`，其中 `30616` 的"
            "`Skill_Shoot_EX` 精确命中 EX 实体 ACT。这里不是单列外键，而是唯一复合静态路径。",
            "",
            "## 12 个低于 95% 的职业",
            "",
            "| 职业 | 当前确认覆盖 | 距 95% | 可用内部键关系 | 同职业歧义/不可用 | 唯一 Projectile 对象数 |",
            "|---|---:|---:|---|---|---:|",
        ]
    )
    for job in payload["under_95_jobs"]:
        baseline = job["baseline"]
        safe = "；".join(
            f"{row['internal_attack_key']}→{_format_skill_candidates(row['parent_candidates'])}"
            for row in job["safe_internal_attack_relations"][:10]
        ) or "—"
        if len(job["safe_internal_attack_relations"]) > 10:
            safe += f"；另 {len(job['safe_internal_attack_relations']) - 10} 条"
        ambiguous = "；".join(
            f"{row['internal_attack_key']}({row['classification']})"
            for row in job["ambiguous_or_unusable_relations"][:8]
        ) or "—"
        if len(job["ambiguous_or_unusable_relations"]) > 8:
            ambiguous += f"；另 {len(job['ambiguous_or_unusable_relations']) - 8} 条"
        lines.append(
            f"| {job['job']} | {baseline.get('confirmed_coverage_percent', 0):.2f}% | "
            f"{baseline.get('hits_needed_for_95', '—')} | {safe} | {ambiguous} | "
            f"{len(job['safe_projectile_object_relations'])} |"
        )

    lines.extend(
        [
            "",
            "## 分类规则",
            "",
            "- `unique_foreign_key`：单值数值外键；可作为静态父技能，但运行时仍校验实体上下文。",
            "- `unique_composite_static_path`：玩家精确动作、类型化实体创建、MonsterSkill 表、"
            "实体 SkillLevel/ACT 精确动作四段全部闭合。",
            "- `same_profession_ambiguous`：候选在同一一转分支；职业相同不是外键，禁止自动选择。",
            "- `cross_profession_ambiguous`：候选跨职业；禁止自动选择。",
            "- `corroboration_only_unusable`：存在参数引用，但字段语义不足以证明父子关系。",
            "- `identity_only`：原始键就是玩家显示技能，无需改写。",
            "",
            "`summonskilltable.dnt` 中形如 `260401,260430` 的值没有在表结构中声明是闭区间还是列表；"
            "本专项完整保留它们，但不展开为唯一外键。",
            "",
            "## 对低覆盖职业的实际价值",
            "",
        ]
    )
    for job in payload["under_95_jobs"]:
        safe = job["safe_internal_attack_relations"]
        objects = job["safe_projectile_object_relations"]
        if not safe and not objects:
            lines.append(f"- **{job['job']}**：本批旧资源没有新增可安全应用的静态父子键；不能用时间最近补齐。")
            continue
        labels = []
        if safe:
            labels.append(f"{len(safe)} 条内部 self-skill/primary90 关系")
        if objects:
            labels.append(f"{len(objects)} 条 Projectile 对象关系")
        lines.append(f"- **{job['job']}**：可利用" + "、".join(labels) + "；详见 JSON 的逐条证据链。")

    lines.extend(
        [
            "",
            "## 边界与复现",
            "",
            "- 本结果是静态资源图，不宣称它本身能把当前 12 个职业全部提升到 95%。"
            "它只能关闭有明确资源外键/类型化链的缺口；缺失对象注册、空 self-skill 和未解网络字段仍需另外破解。",
            "- Runtime 报告只用于附上命中数与冲突情况，未参与静态候选生成。",
            "- 输出记录了所有读取到的 DNT/ACT 哈希和组合签名，便于复核资源版本。",
            "",
            "复现命令：",
            "",
            "```powershell",
            "$env:PYTHONUTF8='1'",
            "& 'C:\\Users\\gcg59\\AppData\\Local\\Python\\pythoncore-3.12-64\\python.exe' -B "
            "tools\\build_static_internal_attack_parent_v55.py "
            "--resource-root 'E:\\ext0811.rar\\Resource00\\Resource00\\resource'",
            "```",
            "",
            f"输入组合签名：`{payload['sources']['input_signature_sha256']}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--native-report", type=Path, default=DEFAULT_NATIVE_REPORT)
    parser.add_argument("--runtime-baseline", type=Path, default=DEFAULT_RUNTIME_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    payload = build(
        args.resource_root.resolve(),
        args.native_report.resolve(),
        args.runtime_baseline.resolve(),
    )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "static_internal_attack_parent_v55.json"
    md_path = output_dir / "static_internal_attack_parent_v55.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_markdown(payload, md_path)
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(md_path),
                "summary": payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
