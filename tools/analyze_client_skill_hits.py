from __future__ import annotations

"""Extract client-defined skill Hit structure from DNT and ACT resources.

This tool deliberately keeps two layers separate:

1. Evidence: SkillLevel action keys, player ACT damage phases, typed Projectile
   creation, recursive Projectile children, and the damage percentage stored in
   each ACT type-4 signal.
2. Conclusion: only simple direct actions and explicit Start/Loop/End processors
   receive an automatic fixed direct-Hit count. Projectile collision loops,
   spatial alternatives and branching actions remain formulas/evidence instead
   of being forced into a guessed integer.

Example::

    python tools/analyze_client_skill_hits.py \
        --resource-root "E:\\ext0811.rar\\Resource00\\Resource00\\resource" \
        --job 火舞 --character-level 70

    python tools/analyze_client_skill_hits.py \
        --resource-root "E:\\ext0811.rar\\Resource00\\Resource00\\resource" \
        --all-second-jobs --output-dir outputs/client_skill_hits

The JSON output is suitable for later comparison with packet-derived Hit
observations. It does not modify simulator data.
"""

import argparse
import json
import re
import struct
import sys
from collections import defaultdict
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
    ActAction,
    ActSignal,
    ParsedAct,
    _action_semantic_name,
    _is_player_combat_act_path,
    _resolve_projectile_act_path,
    parse_act,
)
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


DEFAULT_RESOURCE_ROOT = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")
DEFAULT_ATTACK_CATALOG = (
    ROOT / "outputs" / "damage_attribution" / "attack_instance_catalog_v576.json"
)
DEFAULT_JOB_MAP = DATA_DIR / "modular" / "tables" / "job_map.json"
ACT_FRAMES_PER_SECOND = 60.0
SIGNAL_DAMAGE = 4
SIGNAL_PROJECTILE = 15


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def damage_percent(signal: ActSignal) -> float:
    """Return the client damage weight from the first float of a type-4 signal."""

    if signal.signal_type != SIGNAL_DAMAGE or len(signal.payload) < 4:
        raise ValueError("damage_percent requires an ACT type-4 signal")
    value = float(struct.unpack("<f", signal.payload[:4])[0])
    return round(value, 6)


def raw_target_gate(signal: ActSignal) -> int | None:
    """Expose, but do not over-interpret, the raw target-repeat field.

    Offset 72 consistently carries values such as 500/1000/1500/2000 in the
    current ACT set. Its exact engine semantics are not assumed here; retaining
    it lets packet validation determine whether it is a repeat gate or another
    collision parameter.
    """

    if len(signal.payload) < 76:
        return None
    return int(struct.unpack("<I", signal.payload[72:76])[0])


def damage_channel_raw(signal: ActSignal) -> int | None:
    """Return the ACT damage-panel channel (0 physical, 1 magical).

    Type-4 payload offset 88 is stable across the audited client corpus.  It is
    kept as a raw value so downstream code can aggregate physical and magical
    panel components independently instead of adding both to one multiplier.
    """

    if signal.signal_type != SIGNAL_DAMAGE or len(signal.payload) < 92:
        return None
    return int(struct.unpack("<I", signal.payload[88:92])[0])


def group_damage_phases(action: ActAction) -> list[dict[str, Any]]:
    """Group simultaneous spatial regions into one temporal damage phase.

    Multiple type-4 signals with the same frame interval but different orders
    are usually alternative/overlapping hit volumes. They must not be blindly
    added as separate Hit events for one target. All variants are preserved.
    """

    grouped: dict[tuple[int, int, int | None], list[ActSignal]] = defaultdict(list)
    for signal in action.signals:
        if signal.signal_type == SIGNAL_DAMAGE:
            grouped[(
                signal.start_frame,
                signal.end_frame,
                damage_channel_raw(signal),
            )].append(signal)

    phases: list[dict[str, Any]] = []
    for (start, end, channel), signals in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or 0)
    ):
        variants = [
            {
                "order": signal.order,
                "damage_percent": damage_percent(signal),
                "target_gate_raw": raw_target_gate(signal),
                "damage_channel_raw": damage_channel_raw(signal),
                "hit_action": next(
                    (text for index, text in signal.strings if index == 1), ""
                ),
            }
            for signal in sorted(signals, key=lambda item: item.order)
        ]
        positive = [
            item["damage_percent"]
            for item in variants
            if item["damage_percent"] > 0
        ]
        phases.append(
            {
                "start_frame": start,
                "end_frame": end,
                "primary_damage_percent": max(positive) if positive else 0.0,
                "damage_channel_raw": channel,
                "spatial_variant_count": len(variants),
                "spatial_alternatives": variants,
            }
        )
    return phases


def projectile_spawns(action: ActAction, projectile_ids: set[int]) -> list[dict[str, int]]:
    result: list[dict[str, int]] = []
    for signal in action.signals:
        if signal.signal_type != SIGNAL_PROJECTILE or len(signal.payload) < 4:
            continue
        template_id = int.from_bytes(signal.payload[:4], "little", signed=False)
        if template_id not in projectile_ids:
            continue
        result.append(
            {
                "template_id": template_id,
                "start_frame": signal.start_frame,
                "end_frame": signal.end_frame,
                "order": signal.order,
            }
        )
    return result


def expand_loop_phases(
    action: ActAction,
    duration_ms: float,
    *,
    frames_per_second: float = ACT_FRAMES_PER_SECOND,
) -> list[dict[str, Any]]:
    """Expand an explicit loop action for the processor's configured duration."""

    if action.length_frames <= 0 or duration_ms <= 0:
        return []
    total_frames = max(0, int(round(duration_ms * frames_per_second / 1000.0)))
    base = group_damage_phases(action)
    result: list[dict[str, Any]] = []
    cycle = 0
    while cycle * action.length_frames < total_frames:
        offset = cycle * action.length_frames
        remaining = total_frames - offset
        for phase in base:
            if phase["start_frame"] >= remaining:
                continue
            copied = dict(phase)
            copied["cycle"] = cycle
            copied["absolute_start_frame"] = offset + phase["start_frame"]
            copied["absolute_end_frame"] = min(
                offset + phase["end_frame"], total_frames
            )
            result.append(copied)
        cycle += 1
    return result


def expand_loop_spawns(
    action: ActAction,
    duration_ms: float,
    projectile_ids: set[int],
    *,
    frames_per_second: float = ACT_FRAMES_PER_SECOND,
) -> list[dict[str, int]]:
    if action.length_frames <= 0 or duration_ms <= 0:
        return []
    total_frames = max(0, int(round(duration_ms * frames_per_second / 1000.0)))
    base = projectile_spawns(action, projectile_ids)
    result: list[dict[str, int]] = []
    cycle = 0
    while cycle * action.length_frames < total_frames:
        offset = cycle * action.length_frames
        remaining = total_frames - offset
        for spawn in base:
            if spawn["start_frame"] >= remaining:
                continue
            copied = dict(spawn)
            copied["cycle"] = cycle
            copied["absolute_start_frame"] = offset + spawn["start_frame"]
            result.append(copied)
        cycle += 1
    return result


def normalized_distribution(weights: Iterable[float]) -> list[float]:
    values = [max(0.0, float(value)) for value in weights]
    total = sum(values)
    if total <= 0:
        return []
    return [round(value * 100.0 / total, 6) for value in values]


def _read_messages(resource_root: Path) -> dict[int, str]:
    directory = resource_root / "uistring"
    preferred = directory / "uistring0818.xml"
    candidates = [preferred] if preferred.is_file() and preferred.stat().st_size else []
    candidates.extend(
        path for path in sorted(directory.glob("uistring*.xml"), reverse=True)
        if path.stat().st_size and path not in candidates
    )
    if not candidates:
        return {}
    path = max(candidates, key=lambda item: (item.stat().st_size, item.name))
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        int(match.group(1)): match.group(2).replace("\\n", "\n")
        for match in re.finditer(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>',
            text,
            re.DOTALL,
        )
    }


def _render_description(row: dict[str, Any], messages: dict[int, str]) -> str:
    message_id = _as_int(row.get("_SkillExplanationID"))
    template = messages.get(message_id, "")
    params = str(row.get("_SkillExplanationIDParam", "") or "").split(",")

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return params[index] if index < len(params) else match.group(0)

    return (
        re.sub(r"\{(\d+)\}", replace, template)
        .replace("#y", "")
        .replace("#w", "")
        .replace("#n", "")
        .strip()
    )


def _is_action_key(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return any(character.isalpha() for character in text)
    return False


def _job_lineage(
    job_rows: dict[int, dict[str, Any]],
    job_id: int,
) -> tuple[int, ...]:
    """Return the client job ancestry from base class to the selected job."""

    lineage: list[int] = []
    seen: set[int] = set()
    current = int(job_id)
    while current > 0 and current not in seen:
        row = job_rows.get(current)
        if row is None:
            break
        seen.add(current)
        lineage.append(current)
        current = _as_int(row.get("_ParentJob"))
    return tuple(reversed(lineage))


def _job_depth(job_rows: dict[int, dict[str, Any]], job_id: int) -> int:
    return len(_job_lineage(job_rows, job_id))


def _contains_projectile_damage(template: dict[str, Any]) -> bool:
    if any(action.get("damage_phases") for action in template.get("actions", [])):
        return True
    return any(
        _contains_projectile_damage(child)
        for child in template.get("children", [])
    )


def _profile_has_damage_evidence(profile: dict[str, Any]) -> bool:
    if any(action.get("damage_phases") for action in profile["player_actions"]):
        return True
    return any(
        _contains_projectile_damage(template)
        for template in profile["projectile_templates"]
    )


def _safe_output_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "unknown_job"


class ClientSkillHitParser:
    def __init__(
        self,
        resource_root: Path,
        *,
        attack_catalog: Path | None = DEFAULT_ATTACK_CATALOG,
        character_level: int = 70,
        frames_per_second: float = ACT_FRAMES_PER_SECOND,
    ) -> None:
        self.resource_root = resource_root
        self.ext = resource_root / "ext"
        self.character_level = int(character_level)
        self.frames_per_second = float(frames_per_second)
        self.messages = _read_messages(resource_root)
        combined_skill_table = self.ext / "skilltable_character.dnt"
        if combined_skill_table.is_file() and combined_skill_table.stat().st_size:
            skill_rows = parse_dnt(combined_skill_table)
        else:
            skill_rows = [
                row
                for skill_path in sorted(self.ext.glob("skilltable_character*.dnt"))
                if skill_path.stat().st_size
                for row in parse_dnt(skill_path)
            ]
        self.skill_rows = {int(row["RowId"]): row for row in skill_rows}
        job_rows = parse_dnt(self.ext / "jobtable.dnt")
        self.job_rows = {int(row["RowId"]): row for row in job_rows}
        self.display_catalog = {
            skill_id: (
                self.messages.get(_as_int(row.get("_NameID")))
                or str(row.get("_StaticName", "") or "")
                or f"Skill {skill_id}",
                self._job_name(_as_int(row.get("_NeedJob"))),
                _job_depth(self.job_rows, _as_int(row.get("_NeedJob"))),
            )
            for skill_id, row in self.skill_rows.items()
        }
        self.second_job_map = self._discover_second_job_map()
        self.level_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for path in sorted(self.ext.glob("skillleveltable_character*.dnt")):
            for row in parse_dnt(path):
                if _as_int(row.get("_ApplyType")) != 0:
                    continue
                self.level_rows[_as_int(row.get("_SkillIndex"))].append(row)

        projectile_rows = parse_dnt(self.ext / "weapontable_projectile.dnt")
        file_rows = parse_dnt(self.ext / "filetable.dnt")
        self.projectile_rows = {
            int(row["RowId"]): row for row in projectile_rows
        }
        self.projectile_ids = set(self.projectile_rows)
        self.files_by_id = {
            int(row["RowId"]): str(row.get("_FileName", "") or "")
            for row in file_rows
        }
        indexed: dict[str, list[Path]] = defaultdict(list)
        for path in sorted((resource_root / "weapon").rglob("*.act")):
            indexed[path.name.casefold()].append(path)
        self.projectile_paths_by_basename = {
            name: tuple(paths) for name, paths in indexed.items()
        }
        self._parsed_acts: dict[Path, ParsedAct] = {}
        self._player_actions_by_semantic: dict[str, list[tuple[Path, ActAction]]] | None = None
        self._skill_profile_cache: dict[int, dict[str, Any]] = {}

        self.attack_catalog_by_skill: dict[int, dict[str, Any]] = {}
        if attack_catalog is not None and attack_catalog.is_file():
            payload = json.loads(attack_catalog.read_text(encoding="utf-8"))
            self.attack_catalog_by_skill = {
                int(item["skill_id"]): item for item in payload.get("skills", [])
            }
        self.second_job_ids = self._resolve_second_job_ids()

    def _job_name(self, job_id: int) -> str:
        row = self.job_rows.get(job_id, {})
        return (
            self.messages.get(_as_int(row.get("_JobName")))
            or str(row.get("_EnglishName", "") or "")
            or f"Job {job_id}"
        )

    def _discover_second_job_map(self) -> dict[str, dict[str, Any]]:
        parents = {_as_int(row.get("_ParentJob")) for row in self.job_rows.values()}
        need_jobs = {_as_int(row.get("_NeedJob")) for row in self.skill_rows.values()}
        discovered: list[tuple[int, str, dict[str, Any]]] = []
        for job_id, row in self.job_rows.items():
            if _as_int(row.get("_JobNumber")) < 2 or job_id in parents:
                continue
            lineage = _job_lineage(self.job_rows, job_id)
            if not need_jobs.intersection(lineage):
                continue
            root_id = lineage[0] if lineage else job_id
            root_name = self._job_name(root_id)
            discovered.append((job_id, self._job_name(job_id), {
                "family": f"{root_name}系",
                "service": _as_int(row.get("_Service")),
            }))
        return {
            name: metadata
            for _job_id, name, metadata in sorted(discovered, key=lambda item: item[0])
        }

    def _resolve_second_job_ids(self) -> dict[str, int]:
        """Map all configured second jobs to their deepest client job id.

        The display catalog intentionally labels inherited skills with one
        representative specialization.  It therefore cannot itself describe
        the complete skill set of the sibling specialization.  The selected
        leaf id plus ``jobtable._ParentJob`` is the authoritative inheritance
        chain.
        """

        names = set(self.second_job_map)
        return {
            self._job_name(job_id): job_id
            for job_id, row in sorted(self.job_rows.items())
            if self._job_name(job_id) in names
        }

    def second_jobs(self) -> tuple[str, ...]:
        """Return the configured 30 second jobs in stable UI order."""

        return tuple(
            job for job in self.second_job_map if job in self.second_job_ids
        )

    def skill_ids_for_second_job(self, job: str) -> list[int]:
        """Return all skills usable by a second job, including its ancestors."""

        job_id = self.second_job_ids.get(job)
        if job_id is None:
            raise ValueError(f"unknown configured second job: {job}")
        allowed_job_ids = set(_job_lineage(self.job_rows, job_id))
        selected: list[int] = []
        for skill_id, (_name, catalog_job, _generation) in self.display_catalog.items():
            need_job = _as_int(self.skill_rows.get(skill_id, {}).get("_NeedJob"))
            # Keep explicitly curated specialization entries even when a
            # malformed/legacy row lacks NeedJob, while inheritance itself is
            # always driven by the client job tree.
            if need_job in allowed_job_ids or catalog_job == job:
                selected.append(int(skill_id))
        return sorted(set(selected))

    def _parse_act(self, path: Path) -> ParsedAct:
        parsed = self._parsed_acts.get(path)
        if parsed is None:
            parsed = parse_act(path)
            self._parsed_acts[path] = parsed
        return parsed

    def _level_row(self, skill_id: int) -> dict[str, Any]:
        candidates = [
            row
            for row in self.level_rows.get(skill_id, [])
            if _as_int(row.get("_LevelLimit")) <= self.character_level
        ]
        if not candidates:
            candidates = list(self.level_rows.get(skill_id, []))
        if not candidates:
            return {}
        return max(candidates, key=lambda row: _as_int(row.get("_SkillLevel")))

    def _find_action(self, file_name: str, action_name: str) -> ActAction | None:
        path = self.resource_root / Path(file_name)
        if not path.is_file():
            return None
        parsed = self._parse_act(path)
        return next((action for action in parsed.actions if action.name == action_name), None)

    def _index_player_actions(self) -> dict[str, list[tuple[Path, ActAction]]]:
        """Build a lazy exact-semantic fallback for skills lacking ProcessParam keys.

        This is intentionally exact. For example ``FlameSpark`` may match
        ``Skill_FlameSpark`` but not a PVP, mutation or similarly named skill.
        Broad fuzzy matching would silently attach another skill's damage.
        """

        if self._player_actions_by_semantic is not None:
            return self._player_actions_by_semantic
        player_root = self.resource_root / "char" / "player"
        result: dict[str, list[tuple[Path, ActAction]]] = defaultdict(list)
        for path in sorted(player_root.rglob("*.act")):
            if not _is_player_combat_act_path(path, player_root):
                continue
            try:
                parsed = self._parse_act(path)
            except (OSError, ValueError):
                continue
            for action in parsed.actions:
                result[_action_semantic_name(action.name)].append((path, action))
        self._player_actions_by_semantic = dict(result)
        return self._player_actions_by_semantic

    def _static_action_fallback(self, skill_id: int) -> list[dict[str, Any]]:
        static_name = str(self.skill_rows.get(skill_id, {}).get("_StaticName", "") or "")
        semantic = _action_semantic_name(static_name)
        if not semantic:
            return []
        matches = self._index_player_actions().get(semantic, [])
        # An exact semantic appearing in several unrelated files is ambiguous.
        # Identical duplicate records may still be collapsed safely.
        unique: dict[tuple[int, str, tuple[tuple[int, int, int, int], ...]], tuple[Path, ActAction]] = {}
        for path, action in matches:
            signature = (
                action.length_frames,
                action.next_action,
                tuple(
                    (signal.signal_type, signal.start_frame, signal.end_frame, signal.order)
                    for signal in action.signals
                    if signal.signal_type in {SIGNAL_DAMAGE, SIGNAL_PROJECTILE}
                ),
            )
            unique.setdefault(signature, (path, action))
        if len(unique) != 1:
            return []
        path, action = next(iter(unique.values()))
        return [
            {
                "file": str(path.relative_to(self.resource_root)).replace("\\", "/"),
                "entry_action": action.name,
                "action_name": action.name,
                "length_frames": action.length_frames,
                "next_action": action.next_action,
                "self_loop": action.next_action.casefold() == action.name.casefold(),
                "explicit_continuation": False,
                "fallback": "exact_static_name_semantic",
                "damage_phases": group_damage_phases(action),
                "projectile_spawns": projectile_spawns(action, self.projectile_ids),
            }
        ]

    def _action_evidence(self, skill_id: int) -> list[dict[str, Any]]:
        item = self.attack_catalog_by_skill.get(skill_id, {})
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for match in item.get("matched_actions", []):
            key = (str(match.get("file", "")), str(match.get("action_name", "")))
            if key in seen:
                continue
            seen.add(key)
            action = self._find_action(*key)
            if action is None:
                continue
            result.append(
                {
                    "file": key[0],
                    "entry_action": str(match.get("entry_action_name", "")),
                    "action_name": action.name,
                    "length_frames": action.length_frames,
                    "next_action": action.next_action,
                    "self_loop": action.next_action.casefold() == action.name.casefold(),
                    "explicit_continuation": bool(
                        match.get("explicit_action_continuation")
                    ),
                    "damage_phases": group_damage_phases(action),
                    "projectile_spawns": projectile_spawns(
                        action, self.projectile_ids
                    ),
                }
            )
        return result or self._static_action_fallback(skill_id)

    def _projectile_path(self, template_id: int) -> Path | None:
        row = self.projectile_rows.get(template_id)
        if row is None:
            return None
        act_file_id = _as_int(row.get("_ActName"))
        act_file = self.files_by_id.get(act_file_id, "").strip()
        if not act_file:
            return None
        return _resolve_projectile_act_path(
            self.resource_root,
            act_file,
            self.projectile_paths_by_basename,
        )

    def _projectile_evidence(
        self,
        template_id: int,
        *,
        visiting: frozenset[int] = frozenset(),
    ) -> dict[str, Any]:
        if template_id in visiting:
            return {"template_id": template_id, "cycle": True}
        path = self._projectile_path(template_id)
        result: dict[str, Any] = {
            "template_id": template_id,
            "act_file": "",
            "actions": [],
            "children": [],
        }
        if path is None or not path.is_file():
            result["missing_act"] = True
            return result
        result["act_file"] = str(path.relative_to(self.resource_root)).replace("\\", "/")
        try:
            parsed = self._parse_act(path)
        except (OSError, ValueError) as exc:
            # Empty winning PAK entries are not proof that a skill has no
            # damage, and must not silently fall back to an older definition.
            result["unresolved_act"] = str(exc)
            return result
        child_ids: set[int] = set()
        for action in parsed.actions:
            phases = group_damage_phases(action)
            spawns = projectile_spawns(action, self.projectile_ids)
            if not phases and not spawns:
                continue
            result["actions"].append(
                {
                    "action_name": action.name,
                    "length_frames": action.length_frames,
                    "next_action": action.next_action,
                    "self_loop": action.next_action.casefold() == action.name.casefold(),
                    "damage_phases": phases,
                    "projectile_spawns": spawns,
                }
            )
            child_ids.update(spawn["template_id"] for spawn in spawns)
        next_visiting = visiting | {template_id}
        result["children"] = [
            self._projectile_evidence(child_id, visiting=next_visiting)
            for child_id in sorted(child_ids)
        ]
        return result

    @staticmethod
    def _action_by_name(
        evidence: list[dict[str, Any]], action_name: str
    ) -> dict[str, Any] | None:
        return next(
            (item for item in evidence if item["action_name"] == action_name),
            None,
        )

    def _loop_model(
        self,
        level_row: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        start_name = str(level_row.get("_ProcessParam1", "") or "").strip()
        loop_name = str(level_row.get("_ProcessParam2", "") or "").strip()
        end_name = str(level_row.get("_ProcessParam3", "") or "").strip()
        duration_ms = _as_float(level_row.get("_ProcessParam4"))
        if not all(_is_action_key(name) for name in (start_name, loop_name, end_name)):
            return None
        if duration_ms <= 0:
            return None
        start = self._action_by_name(actions, start_name)
        loop = self._action_by_name(actions, loop_name)
        end = self._action_by_name(actions, end_name)
        if loop is None:
            return None

        loop_action = self._find_action(loop["file"], loop["action_name"])
        if loop_action is None:
            return None
        expanded_phases = expand_loop_phases(
            loop_action,
            duration_ms,
            frames_per_second=self.frames_per_second,
        )
        expanded_spawns = expand_loop_spawns(
            loop_action,
            duration_ms,
            self.projectile_ids,
            frames_per_second=self.frames_per_second,
        )
        start_phases = list(start.get("damage_phases", [])) if start else []
        end_phases = list(end.get("damage_phases", [])) if end else []
        weights = [
            phase["primary_damage_percent"]
            for phase in start_phases + expanded_phases + end_phases
            if phase["primary_damage_percent"] > 0
        ]
        channels = [
            phase.get("damage_channel_raw")
            for phase in start_phases + expanded_phases + end_phases
            if phase["primary_damage_percent"] > 0
        ]
        spawn_counts: dict[int, int] = defaultdict(int)
        for spawn in expanded_spawns:
            spawn_counts[int(spawn["template_id"])] += 1
        return {
            "start_action": start_name,
            "loop_action": loop_name,
            "end_action": end_name,
            "duration_ms": duration_ms,
            "frames_per_second": self.frames_per_second,
            "expanded_loop_frames": int(
                round(duration_ms * self.frames_per_second / 1000.0)
            ),
            "fixed_direct_hit_count": len(start_phases)
            + len(expanded_phases)
            + len(end_phases),
            "direct_damage_weights": weights,
            "direct_damage_channels": channels,
            "normalized_direct_distribution": normalized_distribution(weights),
            "projectile_spawn_counts": dict(sorted(spawn_counts.items())),
        }

    def analyze_skill(self, skill_id: int) -> dict[str, Any]:
        cached = self._skill_profile_cache.get(int(skill_id))
        if cached is not None:
            return cached
        display = self.display_catalog.get(skill_id, ("", "", 0))
        skill_row = self.skill_rows.get(skill_id, {})
        level_row = self._level_row(skill_id)
        actions = self._action_evidence(skill_id)
        catalog_item = self.attack_catalog_by_skill.get(skill_id, {})
        template_ids = sorted(
            {
                int(template_id)
                for template_id in catalog_item.get("projectile_template_ids", [])
            }
            | {
                int(spawn["template_id"])
                for action in actions
                for spawn in action["projectile_spawns"]
            }
        )
        loop_model = self._loop_model(level_row, actions)

        non_continuation = [
            action for action in actions if not action["explicit_continuation"]
        ]
        simple_fixed: int | None = None
        simple_distribution: list[float] = []
        if (
            loop_model is None
            and len(non_continuation) == 1
            and not non_continuation[0]["self_loop"]
            and not template_ids
        ):
            phases = non_continuation[0]["damage_phases"]
            simple_fixed = len(phases)
            simple_distribution = normalized_distribution(
                phase["primary_damage_percent"]
                for phase in phases
                if phase["primary_damage_percent"] > 0
            )

        warnings: list[str] = []
        if any(
            phase["spatial_variant_count"] > 1
            for action in actions
            for phase in action["damage_phases"]
        ):
            warnings.append(
                "simultaneous spatial regions are alternatives/overlaps, not automatic extra Hit"
            )
        if any(action["self_loop"] for action in actions):
            warnings.append("self-loop action: Hit count may depend on duration or collision geometry")
        if template_ids:
            warnings.append(
                "Projectile Hit count must follow object actions/children; do not use a cast time window"
            )
        if not actions:
            warnings.append(
                "no exact player ACT action bridge in the current catalog; packet or explicit alias evidence is required"
            )

        result = {
            "skill_id": skill_id,
            "name": display[0],
            "job": display[1],
            "generation": display[2],
            "static_name": str(skill_row.get("_StaticName", "") or ""),
            "need_job_id": _as_int(skill_row.get("_NeedJob")),
            "skill_type": _as_int(skill_row.get("_SkillType")),
            "base_skill_id": _as_int(skill_row.get("_BaseSkillID")),
            "selected_pve_level": _as_int(level_row.get("_SkillLevel")),
            "selected_level_limit": _as_int(level_row.get("_LevelLimit")),
            "description": _render_description(level_row, self.messages),
            "process_params": [
                str(level_row.get(f"_ProcessParam{index}", "") or "")
                for index in range(1, 11)
            ],
            "client_classification": catalog_item.get("classification", "unresolved"),
            "simple_fixed_direct_hit_count": simple_fixed,
            "simple_normalized_direct_distribution": simple_distribution,
            "loop_model": loop_model,
            "player_actions": actions,
            "projectile_templates": [
                self._projectile_evidence(template_id)
                for template_id in template_ids
            ],
            "warnings": warnings,
        }
        self._skill_profile_cache[int(skill_id)] = result
        return result

    def _metadata(self, schema: str) -> dict[str, Any]:
        return {
            "schema": schema,
            "resource_root": str(self.resource_root),
            "character_level": self.character_level,
            "act_frames_per_second": self.frames_per_second,
            "method": {
                "skill_edge": "SkillLevel ProcessParam/action catalog -> player ACT",
                "job_edge": "skilltable._NeedJob -> jobtable._ParentJob ancestry",
                "object_edge": "typed signal 15 -> weapontable_projectile RowId -> Projectile ACT",
                "damage_edge": "ACT type 4; first float is retained as damage percentage",
                "spatial_rule": "same action and same frame interval are grouped as one temporal phase",
                "loop_rule": "explicit Start/Loop/End duration is expanded at 60 ACT frames per second",
                "safety_rule": "branching/self-loop/geometry-dependent objects remain evidence, not guessed fixed Hit",
            },
        }

    @staticmethod
    def _job_summary(profiles: list[dict[str, Any]]) -> dict[str, Any]:
        action_count = sum(bool(profile["player_actions"]) for profile in profiles)
        damage_evidence_count = sum(
            _profile_has_damage_evidence(profile) for profile in profiles
        )
        fixed_count = sum(
            profile["simple_fixed_direct_hit_count"] is not None
            or profile["loop_model"] is not None
            for profile in profiles
        )
        projectile_count = sum(bool(profile["projectile_templates"]) for profile in profiles)
        total = len(profiles)
        return {
            "skill_count": total,
            "player_action_bridge_count": action_count,
            "player_action_bridge_percent": round(
                action_count * 100.0 / total, 2
            ) if total else 0.0,
            "damage_evidence_count": damage_evidence_count,
            "fixed_direct_hit_model_count": fixed_count,
            "projectile_skill_count": projectile_count,
            "skills_without_action_bridge": [
                {
                    "skill_id": profile["skill_id"],
                    "name": profile["name"],
                    "skill_type": profile["skill_type"],
                }
                for profile in profiles
                if not profile["player_actions"]
            ],
        }

    def analyze_second_job(
        self,
        job: str,
        *,
        skill_ids: Iterable[int] = (),
    ) -> dict[str, Any]:
        job_id = self.second_job_ids.get(job)
        if job_id is None:
            raise ValueError(f"unknown configured second job: {job}")
        requested = {int(skill_id) for skill_id in skill_ids}
        selected = self.skill_ids_for_second_job(job)
        if requested:
            selected = [skill_id for skill_id in selected if skill_id in requested]
        profiles = [self.analyze_skill(skill_id) for skill_id in selected]
        lineage_ids = _job_lineage(self.job_rows, job_id)
        return {
            "job": job,
            "job_id": job_id,
            "family": self.second_job_map.get(job, {}).get("family", "其他"),
            "service": self.second_job_map.get(job, {}).get("service", 0),
            "job_lineage": [
                {
                    "job_id": ancestor_id,
                    "english_name": str(
                        self.job_rows[ancestor_id].get("_EnglishName", "") or ""
                    ),
                    "job_number": _as_int(
                        self.job_rows[ancestor_id].get("_JobNumber")
                    ),
                }
                for ancestor_id in lineage_ids
            ],
            "summary": self._job_summary(profiles),
            "skills": profiles,
        }

    def analyze_all_second_jobs(
        self,
        *,
        skill_ids: Iterable[int] = (),
    ) -> dict[str, Any]:
        result = self._metadata("client_skill_hit_profile_all_second_jobs_v2")
        jobs = [
            self.analyze_second_job(job, skill_ids=skill_ids)
            for job in self.second_jobs()
        ]
        unique_skill_ids = {
            profile["skill_id"]
            for job in jobs
            for profile in job["skills"]
        }
        result.update(
            {
                "second_job_count": len(jobs),
                "unique_skill_count": len(unique_skill_ids),
                "jobs": jobs,
            }
        )
        return result

    def analyze(
        self,
        *,
        job: str = "",
        skill_ids: Iterable[int] = (),
        include_inherited: bool = True,
    ) -> dict[str, Any]:
        if job and include_inherited and job in self.second_job_ids:
            result = self._metadata("client_skill_hit_profile_second_job_v2")
            result["job_profile"] = self.analyze_second_job(
                job,
                skill_ids=skill_ids,
            )
            return result
        requested = {int(skill_id) for skill_id in skill_ids}
        selected: list[int] = []
        for skill_id, (_name, skill_job, _generation) in self.display_catalog.items():
            if requested and skill_id not in requested:
                continue
            if job and skill_job != job:
                continue
            selected.append(int(skill_id))
        result = self._metadata("client_skill_hit_profile_v1")
        result["skills"] = [
            self.analyze_skill(skill_id) for skill_id in sorted(selected)
        ]
        return result


def _write_all_second_job_directory(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    common = {
        key: value for key, value in result.items()
        if key not in {"jobs", "second_job_count", "unique_skill_count"}
    }
    manifest_jobs: list[dict[str, Any]] = []
    for index, job_profile in enumerate(result["jobs"], start=1):
        file_name = f"{index:02d}_{_safe_output_name(job_profile['job'])}.json"
        payload = dict(common)
        payload["schema"] = "client_skill_hit_profile_second_job_v2"
        payload["job_profile"] = job_profile
        (output_dir / file_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_jobs.append(
            {
                "job": job_profile["job"],
                "job_id": job_profile["job_id"],
                "file": file_name,
                "summary": job_profile["summary"],
            }
        )
    manifest = dict(common)
    manifest.update(
        {
            "schema": "client_skill_hit_profile_manifest_v2",
            "second_job_count": result["second_job_count"],
            "unique_skill_count": result["unique_skill_count"],
            "jobs": manifest_jobs,
        }
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_ATTACK_CATALOG)
    parser.add_argument("--job", default="")
    parser.add_argument(
        "--all-second-jobs",
        "--all-jobs",
        action="store_true",
        help="analyze all configured second jobs with inherited base/first-job skills",
    )
    parser.add_argument(
        "--exact-job-only",
        action="store_true",
        help="keep the legacy exact display-catalog job filter",
    )
    parser.add_argument(
        "--list-jobs",
        action="store_true",
        help="print the configured second jobs and exit",
    )
    parser.add_argument("--skill-id", type=int, action="append", default=[])
    parser.add_argument("--character-level", type=int, default=70)
    parser.add_argument("--frames-per-second", type=float, default=ACT_FRAMES_PER_SECOND)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="write one JSON per second job plus manifest.json",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    parser = ClientSkillHitParser(
        args.resource_root,
        attack_catalog=args.catalog,
        character_level=args.character_level,
        frames_per_second=args.frames_per_second,
    )
    if args.list_jobs:
        print("\n".join(parser.second_jobs()))
        return 0
    if args.all_second_jobs or args.output_dir is not None:
        if args.job:
            raise SystemExit("--job cannot be combined with --all-second-jobs/--output-dir")
        result = parser.analyze_all_second_jobs(skill_ids=args.skill_id)
        if args.output_dir is not None:
            manifest_path = _write_all_second_job_directory(
                result,
                args.output_dir,
            )
            print(manifest_path)
            return 0
    else:
        result = parser.analyze(
            job=args.job,
            skill_ids=args.skill_id,
            include_inherited=not args.exact_job_only,
        )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
