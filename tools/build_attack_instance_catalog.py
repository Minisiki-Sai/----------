from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_skill_catalog,
)
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


DEFAULT_RESOURCE_ROOT = Path(
    r"E:\ext0811.rar\Resource00\Resource00\resource"
)
DEFAULT_JSON = (
    ROOT / "outputs/damage_attribution/attack_instance_catalog_v576.json"
)
DEFAULT_MARKDOWN = (
    ROOT / "outputs/damage_attribution/attack_instance_catalog_v576.md"
)
DEFAULT_GENERATED_PYTHON = (
    ROOT
    / "dragon_nest_builder/live_dps/generated_attack_instance_specs_v576.py"
)

ACT_SIGNATURE = b"Eternity Action File"
SUPPORTED_ACT_VERSIONS = frozenset({1, 2, 3, 4, 5, 6})
SIGNAL_DIRECT_DAMAGE = 4
SIGNAL_ATTACK_OBJECT = 15
MAX_STRING_BYTES = 1 << 20
MAX_COUNT = 1 << 20


class ActParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActSignal:
    signal_type: int
    start_frame: int
    end_frame: int
    order: int
    payload: bytes
    strings: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ActAction:
    name: str
    length_frames: int
    signals: tuple[ActSignal, ...]
    next_action: str = ""
    animation_name: str = ""
    # These are execution fields, not cosmetic metadata. ACT5/6 loaders place
    # NextActionFrame at action+0x68; transitions can resume a tail rather than
    # replay from frame zero. Preserve them even for consumers that do not yet
    # model their native runtime semantics.
    blend_frame: int = 0
    next_action_frame: int = 0
    settings: bytes = b""


@dataclass(frozen=True, slots=True)
class ParsedAct:
    version: int
    actions: tuple[ActAction, ...]


class _Reader:
    def __init__(self, data: bytes, path: Path) -> None:
        self.data = data
        self.path = path
        self.offset = 0

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ActParseError(
                f"{self.path}: truncated at {self.offset}, need {size}, "
                f"remaining {self.remaining()}"
            )
        value = self.data[self.offset : self.offset + size]
        self.offset += size
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def count(self, label: str) -> int:
        value = self.i32()
        if value < 0 or value > MAX_COUNT:
            raise ActParseError(
                f"{self.path}: invalid {label}={value} at {self.offset - 4}"
            )
        return value

    def string(self) -> str:
        size = self.count("string length")
        if size > MAX_STRING_BYTES:
            raise ActParseError(f"{self.path}: overlong string {size}")
        raw = self.take(size).rstrip(b"\x00")
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace")


def _skip_signal_tables(reader: _Reader, version: int) -> tuple[tuple[int, str], ...]:
    # ACT.bt: indexed Vector2, Vector3, Vector4, then indexed strings.
    for width, label in ((8, "vec2"), (12, "vec3"), (16, "vec4")):
        count = reader.count(f"{label} table count")
        reader.take(count * 4)
        reader.take(count * width)
    count = reader.count("string table count")
    indices = [reader.i32() for _ in range(count)]
    strings = tuple((index, reader.string()) for index in indices)
    if version >= 6:
        # ACT6 adds one per-signal flag after the indexed string table.
        reader.u8()
    return strings


def parse_act(path: Path) -> ParsedAct:
    reader = _Reader(path.read_bytes(), path)
    header = reader.take(32)
    if not header.startswith(ACT_SIGNATURE):
        raise ActParseError(f"{path}: invalid ACT signature")
    version = reader.i32()
    if version not in SUPPORTED_ACT_VERSIONS:
        raise ActParseError(f"{path}: unsupported ACT version {version}")
    action_count = reader.count("action count")
    actions: list[ActAction] = []
    for action_index in range(action_count):
        name = reader.string()
        animation_name = reader.string()
        length_frames = reader.i32()
        next_action = reader.string()
        blend_frame = reader.i32()
        next_action_frame = reader.i32()
        settings = b""
        if version == 2:
            settings = reader.take(4)
        elif version == 3:
            settings = reader.take(8)
        elif version == 4:
            settings = reader.take(9)
        elif version == 5:
            settings = reader.take(13)
        elif version == 6:
            # ACT6 appends one flag byte to the ACT5 action settings.
            settings = reader.take(14)
        signal_count = reader.count("signal count")
        signals: list[ActSignal] = []
        for signal_index in range(signal_count):
            signal_offset = reader.offset
            signal_type = reader.i32()
            start_frame = reader.i32()
            end_frame = reader.i32()
            order = reader.i32()
            payload = reader.take(256)
            try:
                strings = _skip_signal_tables(reader, version)
            except ActParseError as exc:
                raise ActParseError(
                    f"{exc}; action[{action_index}]={name!r}, "
                    f"signal[{signal_index}] type={signal_type}, "
                    f"signal_offset={signal_offset}"
                ) from exc
            signals.append(
                ActSignal(
                    signal_type=signal_type,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    order=order,
                    payload=payload,
                    strings=strings,
                )
            )
        actions.append(
            ActAction(
                name=name,
                length_frames=length_frames,
                signals=tuple(signals),
                next_action=next_action,
                animation_name=animation_name,
                blend_frame=blend_frame,
                next_action_frame=next_action_frame,
                settings=settings,
            )
        )
    if reader.remaining() != 0:
        raise ActParseError(
            f"{path}: {reader.remaining()} trailing bytes at {reader.offset}"
        )
    return ParsedAct(version=version, actions=tuple(actions))


def _iter_level_rows(ext: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(ext.glob("skillleveltable_character*.dnt")):
        yield from parse_dnt(path)


def _is_action_key(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return any(character.isalpha() for character in text)
    return False


def _is_player_combat_act_path(path: Path, player_root: Path) -> bool:
    """Return whether *path* is an actual player combat ACT.

    ``char/player`` also contains monster/bot stand-ins, doppelgangers and
    costume/body-part ACTs. Their copied action names are not player-skill
    foreign keys and previously polluted the deterministic catalog.
    """

    try:
        relative = path.relative_to(player_root)
    except ValueError:
        return False
    folded_parts = tuple(part.casefold() for part in relative.parts[:-1])
    if any(
        part == "parts" or part.endswith("_doppelganger")
        for part in folded_parts
    ):
        return False
    stem = path.stem.casefold()
    return not any(
        token in stem
        for token in ("_monster", "_bot", "_npc", "doppelganger")
    )


def _action_semantic_name(value: str) -> str:
    """Return the exact processor name without its typed namespace prefix."""

    text = str(value or "").strip()
    folded = text.casefold()
    for prefix in ("skill_", "event_"):
        if folded.startswith(prefix):
            return text[len(prefix) :].casefold()
    return folded


_PROJECTILE_ACT_NAMESPACES = (
    "plateskill_man_",
    "darkavenger_",
    "sorceress_",
    "soceress_",
    "academic_",
    "assassin_",
    "warrior_",
    "archer_",
    "cleric_",
    "hunter_",
    "lencea_",
    "machina_",
    "kali_",
)


def _projectile_act_semantic_name(value: str) -> str:
    stem = Path(str(value or "")).stem.casefold()
    for prefix in _PROJECTILE_ACT_NAMESPACES:
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _projectile_act_matches_action_key(act_file: str, action_key: str) -> bool:
    """Join a Projectile ACT to a SkillLevel processor without fuzzy matching.

    Projectile files carry a class namespace (for example ``Archer_``), while
    SkillLevel processors carry a typed namespace (``Skill_``).  The semantic
    suffix must otherwise be byte-for-byte equal after case folding.  This is
    intentionally narrower than substring or token similarity.
    """

    act_semantic = _projectile_act_semantic_name(act_file)
    action_semantic = _action_semantic_name(action_key)
    return bool(action_semantic) and act_semantic == action_semantic


def _canonical_skill_id(
    skill_id: int,
    base_skill_by_id: dict[int, int],
) -> int:
    current = skill_id
    visited: set[int] = set()
    while current not in visited:
        visited.add(current)
        parent = int(base_skill_by_id.get(current, 0) or 0)
        if parent <= 0 or parent not in base_skill_by_id:
            break
        current = parent
    return current


def _verified_player_form_aliases(
    skill_rows: dict[int, dict[str, Any]],
) -> dict[int, int]:
    """Audited transformed actions, not arbitrary unnamed client helpers.

    The Nightmare pairs are the same alternate-form IDs already normalized
    by the live damage reader. Recheck the numeric job/CD-family keys against
    this resource version before letting an unnamed form establish ACT edges.
    DeathNell 41 additionally has the exact same localized NameID as 40.
    A zero/shared global group alone is never sufficient evidence.
    """
    candidates = {
        41: 40,
        7601: 7501, 7602: 7502, 7603: 7503, 7605: 7505,
        7608: 7508, 7611: 7511, 7613: 7513,
    }
    verified: dict[int, int] = {}
    for form_id, displayed_id in candidates.items():
        form = skill_rows.get(form_id, {})
        displayed = skill_rows.get(displayed_id, {})
        group = int(form.get("_GlobalSkillGroup", 0) or 0)
        if (
            int(form.get("_NeedJob", 0) or 0) not in {75, 76}
            or int(displayed.get("_NeedJob", 0) or 0) not in {75, 76}
            or group <= 0
            or group != int(displayed.get("_GlobalSkillGroup", 0) or 0)
        ):
            continue
        if form_id == 41 and (
            not form.get("_NameID")
            or form.get("_NameID") != displayed.get("_NameID")
        ):
            continue
        verified[form_id] = displayed_id
    return verified


def _is_displayed_player_skill(row: dict[str, Any] | None) -> bool:
    return bool(
        row is not None
        and str(row.get("name", "")).strip()
        and str(row.get("job", "")).strip()
        and int(row.get("generation", 0) or 0) > 0
    )


def _resolve_projectile_act_path(
    resource_root: Path,
    act_file: str,
    paths_by_basename: dict[str, tuple[Path, ...]],
) -> Path:
    """Resolve a DNT ACT reference without guessing between duplicates.

    Several V576 Projectile rows contain only a basename even though the
    actual file lives in a profession subdirectory such as
    ``weapon/silverhunter``.  The old root-only lookup therefore reported a
    real file as missing and lost its typed child-Projectile edges.  A unique
    recursive basename is an exact filesystem identity on this resource set;
    ambiguous basenames deliberately keep the original missing path so the
    caller fails closed instead of choosing a profession by name similarity.
    """

    normalized = str(act_file or "").strip().replace("\\", "/")
    direct = resource_root / "weapon" / Path(normalized)
    if direct.is_file():
        return direct
    basename = Path(normalized).name.casefold()
    candidates = paths_by_basename.get(basename, ())
    if len(candidates) == 1:
        return candidates[0]
    return direct


def _compact_signal(
    signal: ActSignal,
    projectile_ids: set[int],
) -> dict[str, Any]:
    payload0 = int.from_bytes(signal.payload[:4], "little", signed=False)
    result: dict[str, Any] = {
        "type": signal.signal_type,
        "start_frame": signal.start_frame,
        "end_frame": signal.end_frame,
        "order": signal.order,
    }
    if signal.signal_type == SIGNAL_ATTACK_OBJECT:
        result["payload0"] = payload0
        result["projectile_template_id"] = (
            payload0 if payload0 in projectile_ids else None
        )
    return result


def _unique_action_continuations(
    parsed: ParsedAct,
    families_by_entry: dict[str, set[int]],
) -> dict[str, tuple[ActAction, ...]]:
    """Follow ACT action references; never manufacture edges from name suffixes.

    Type-1's indexed string 1 is the explicit target action (conditional
    continuation). A next-action header is also explicit. Shared continuations
    reached by different skill families are not evidence of a unique owner.
    Entering another learned skill stops traversal: a legal combo is not proof
    that the first skill owns the second skill's damage.
    """
    actions = {action.name.casefold(): action for action in parsed.actions}
    reached_by: dict[str, set[int]] = defaultdict(set)
    reachable: dict[str, set[str]] = {}
    for entry, families in families_by_entry.items():
        if entry not in actions or len(families) != 1:
            continue
        seen: set[str] = set()
        pending = [entry]
        while pending:
            current = pending.pop()
            if current in seen or current not in actions:
                continue
            other_families = families_by_entry.get(current)
            if other_families and other_families != families:
                continue
            seen.add(current)
            reached_by[current].update(families)
            action = actions[current]
            refs = {action.next_action.casefold()}
            refs.update(value.casefold() for signal in action.signals
                        if signal.signal_type == 1 for index, value in signal.strings
                        if index == 1 and value)
            pending.extend(refs - seen)
        reachable[entry] = seen
    return {
        entry: tuple(actions[key] for key in sorted(keys)
                     if reached_by[key] == families_by_entry[entry])
        for entry, keys in reachable.items()
    }


def _classify_skill(row: dict[str, Any]) -> str:
    has_projectile = bool(row["projectile_template_ids"])
    has_direct = bool(row["direct_damage_segments"])
    if has_projectile and has_direct:
        return "hybrid_projectile_and_direct"
    if has_projectile:
        return "projectile_attack_instance"
    if has_direct:
        return "direct_attack_segments"
    if row["matched_actions"]:
        return "matched_action_without_typed_damage_signal"
    return "unresolved_action_key"


def build_catalog(resource_root: Path) -> dict[str, Any]:
    ext = resource_root / "ext"
    combined_skill_table = ext / "skilltable_character.dnt"
    if combined_skill_table.is_file() and combined_skill_table.stat().st_size:
        skill_table_rows = parse_dnt(combined_skill_table)
    else:
        skill_table_rows = [
            row
            for skill_path in sorted(ext.glob("skilltable_character*.dnt"))
            if skill_path.stat().st_size
            for row in parse_dnt(skill_path)
        ]
    projectile_rows = parse_dnt(ext / "weapontable_projectile.dnt")
    file_rows = parse_dnt(ext / "filetable.dnt")
    projectile_ids = {int(row["RowId"]) for row in projectile_rows}
    files_by_id = {
        int(row["RowId"]): str(row.get("_FileName", "") or "")
        for row in file_rows
    }
    skill_rows = {int(row["RowId"]): row for row in skill_table_rows}
    base_skill_by_id = {
        skill_id: int(row.get("_BaseSkillID", 0) or 0)
        for skill_id, row in skill_rows.items()
    }
    verified_form_aliases = _verified_player_form_aliases(skill_rows)
    base_skill_by_id.update(verified_form_aliases)
    display_catalog = _load_skill_catalog(str(DATA_DIR))

    action_keys_by_skill: dict[int, set[str]] = defaultdict(set)
    level_rows = 0
    for row in _iter_level_rows(ext):
        level_rows += 1
        skill_id = int(row.get("_SkillIndex", 0) or 0)
        if skill_id <= 0 or skill_id not in skill_rows:
            continue
        if int(row.get("_ApplyType", 0) or 0) != 0:
            continue
        for index in range(1, 11):
            for raw in str(row.get(f"_ProcessParam{index}", "") or "").split(";"):
                key = raw.strip()
                if _is_action_key(key):
                    action_keys_by_skill[skill_id].add(key)

    wanted_action_keys = {
        key.casefold()
        for keys in action_keys_by_skill.values()
        for key in keys
    }
    canonical_families_by_action_key: dict[str, set[int]] = defaultdict(set)
    for skill_id, action_keys in action_keys_by_skill.items():
        canonical_skill_id = _canonical_skill_id(skill_id, base_skill_by_id)
        for action_key in action_keys:
            canonical_families_by_action_key[action_key.casefold()].add(
                canonical_skill_id
            )
    # Attack1/Attack2 and similar processors are reused by unrelated skills.
    # A typed signal inside such an action proves a Projectile exists, but not
    # which skill owns it. Only keys belonging to one canonical skill family
    # may create a deterministic Skill -> Projectile edge.
    identifying_action_keys = frozenset(
        action_key
        for action_key, canonical_ids in canonical_families_by_action_key.items()
        if len(canonical_ids) == 1
    )
    actions_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    player_act_root = resource_root / "char" / "player"
    all_player_tree_act_files = sorted(player_act_root.rglob("*.act"))
    act_files = [
        path
        for path in all_player_tree_act_files
        if _is_player_combat_act_path(path, player_act_root)
    ]
    parse_errors: list[dict[str, str]] = []
    parsed_files = 0
    for path in act_files:
        try:
            parsed = parse_act(path)
        except (OSError, ActParseError) as exc:
            parse_errors.append(
                {
                    "file": str(path.relative_to(resource_root)),
                    "error": str(exc),
                }
            )
            continue
        parsed_files += 1
        continuations = _unique_action_continuations(parsed, canonical_families_by_action_key)
        for action in parsed.actions:
            folded = action.name.casefold()
            if folded not in wanted_action_keys:
                continue
            for part in continuations.get(folded, (action,)):
                signals = [
                    _compact_signal(signal, projectile_ids)
                    for signal in part.signals
                    if signal.signal_type in {SIGNAL_DIRECT_DAMAGE, SIGNAL_ATTACK_OBJECT}
                ]
                actions_by_key[folded].append({
                    "file": str(path.relative_to(resource_root)).replace("\\", "/"),
                    "version": parsed.version,
                    "action_name": part.name,
                    "entry_action_name": action.name,
                    "explicit_action_continuation": part.name != action.name,
                    "length_frames": part.length_frames,
                    "signals": signals,
                })

    # Projectile ACT files can spawn one or more child Projectile templates.
    # This is the missing middle edge for skills whose player action creates a
    # launcher/arrow object and whose actual direct37 damage comes from a child
    # object.  Follow only typed signal-15 payloads that resolve to real rows.
    projectile_children: dict[int, set[int]] = defaultdict(set)
    projectile_act_files: dict[int, str] = {}
    projectile_act_name_to_skills: dict[int, set[int]] = defaultdict(set)
    projectile_act_parse_errors: list[dict[str, str]] = []
    parsed_projectile_acts: dict[Path, ParsedAct | None] = {}
    projectile_act_paths_by_basename: dict[str, tuple[Path, ...]] = {}
    indexed_paths: dict[str, list[Path]] = defaultdict(list)
    for candidate in sorted((resource_root / "weapon").rglob("*.act")):
        indexed_paths[candidate.name.casefold()].append(candidate)
    projectile_act_paths_by_basename = {
        name: tuple(paths) for name, paths in indexed_paths.items()
    }
    for row in projectile_rows:
        template_id = int(row["RowId"])
        act_file_id = int(row.get("_ActName", 0) or 0)
        act_file = files_by_id.get(act_file_id, "").strip()
        if not act_file:
            continue
        act_path = _resolve_projectile_act_path(
            resource_root,
            act_file,
            projectile_act_paths_by_basename,
        )
        projectile_act_files[template_id] = act_file
        for skill_id, action_keys in action_keys_by_skill.items():
            if any(
                _projectile_act_matches_action_key(act_file, action_key)
                for action_key in action_keys
            ):
                projectile_act_name_to_skills[template_id].add(skill_id)
        if act_path not in parsed_projectile_acts:
            try:
                parsed_projectile_acts[act_path] = parse_act(act_path)
            except (OSError, ActParseError) as exc:
                parsed_projectile_acts[act_path] = None
                projectile_act_parse_errors.append(
                    {"file": str(act_path), "error": str(exc)}
                )
        parsed = parsed_projectile_acts[act_path]
        if parsed is None:
            continue
        for action in parsed.actions:
            for signal in action.signals:
                if signal.signal_type != SIGNAL_ATTACK_OBJECT:
                    continue
                child = int.from_bytes(
                    signal.payload[:4], "little", signed=False
                )
                if child in projectile_ids and child != template_id:
                    projectile_children[template_id].add(child)

    def descendants(template_ids: set[int]) -> set[int]:
        result: set[int] = set()
        pending = list(template_ids)
        while pending:
            parent = pending.pop()
            for child in projectile_children.get(parent, ()):
                if child in template_ids or child in result:
                    continue
                result.add(child)
                pending.append(child)
        return result

    skills: list[dict[str, Any]] = []
    projectile_to_skills: dict[int, set[int]] = defaultdict(set)
    typed_projectile_to_skills: dict[int, set[int]] = defaultdict(set)
    projectile_act_name_templates_by_skill: dict[int, set[int]] = defaultdict(set)
    for template_id, candidate_ids in projectile_act_name_to_skills.items():
        for skill_id in candidate_ids:
            projectile_act_name_templates_by_skill[skill_id].add(template_id)
    for skill_id in sorted(action_keys_by_skill):
        action_keys = sorted(action_keys_by_skill[skill_id], key=str.casefold)
        matched_actions: list[dict[str, Any]] = []
        direct_projectile_template_ids: set[int] = set()
        direct_segments: set[tuple[str, int, int, int, str]] = set()
        for action_key in action_keys:
            folded_action_key = action_key.casefold()
            if folded_action_key not in identifying_action_keys:
                continue
            for occurrence in actions_by_key.get(folded_action_key, ()):
                matched_actions.append(occurrence)
                for signal in occurrence["signals"]:
                    template_id = signal.get("projectile_template_id")
                    if template_id:
                        direct_projectile_template_ids.add(int(template_id))
                    if signal["type"] == SIGNAL_DIRECT_DAMAGE:
                        direct_segments.add(
                            (
                                action_key,
                                int(signal["start_frame"]),
                                int(signal["end_frame"]),
                                int(signal["order"]),
                                occurrence["file"],
                            )
                        )
        display_name, job, generation = display_catalog.get(
            skill_id,
            (
                str(skill_rows[skill_id].get("_StaticName", "") or ""),
                "",
                0,
            ),
        )
        descendant_projectile_template_ids = descendants(
            direct_projectile_template_ids
        )
        exact_projectile_act_template_ids = (
            projectile_act_name_templates_by_skill.get(skill_id, set())
        )
        projectile_template_ids = (
            direct_projectile_template_ids
            | descendant_projectile_template_ids
            | exact_projectile_act_template_ids
        )
        for template_id in projectile_template_ids:
            projectile_to_skills[template_id].add(skill_id)
        for template_id in (
            direct_projectile_template_ids
            | descendant_projectile_template_ids
        ):
            typed_projectile_to_skills[template_id].add(skill_id)
        skill = {
            "skill_id": skill_id,
            "canonical_skill_id": _canonical_skill_id(skill_id, base_skill_by_id),
            "verified_player_form": skill_id in verified_form_aliases,
            "name": display_name,
            "job": job,
            "generation": generation,
            "static_name": str(skill_rows[skill_id].get("_StaticName", "") or ""),
            "action_keys": action_keys,
            "matched_actions": matched_actions,
            "projectile_template_ids": sorted(projectile_template_ids),
            "direct_projectile_template_ids": sorted(
                direct_projectile_template_ids
            ),
            "descendant_projectile_template_ids": sorted(
                descendant_projectile_template_ids
            ),
            "exact_projectile_act_template_ids": sorted(
                exact_projectile_act_template_ids
            ),
            "direct_damage_segments": [
                {
                    "action_key": action_key,
                    "start_frame": start,
                    "end_frame": end,
                    "order": order,
                    "file": file,
                }
                for action_key, start, end, order, file in sorted(direct_segments)
            ],
        }
        skill["classification"] = _classify_skill(skill)
        skills.append(skill)

    canonical_by_skill = {
        int(row["skill_id"]): int(row["canonical_skill_id"])
        for row in skills
    }
    skill_row_by_id = {int(row["skill_id"]): row for row in skills}
    projectile_evidence: list[dict[str, Any]] = []
    deterministic_template_ids: list[int] = []
    for template_id, candidate_ids in sorted(projectile_to_skills.items()):
        canonical_ids = {
            canonical_by_skill.get(skill_id, skill_id)
            for skill_id in candidate_ids
        }
        typed_candidate_ids = typed_projectile_to_skills.get(
            template_id, set()
        )
        typed_canonical_ids = {
            canonical_by_skill.get(skill_id, skill_id)
            for skill_id in typed_candidate_ids
        }
        # Exact ACT semantic names are corroboration and discovery candidates,
        # not an explicit foreign key.  A template becomes deterministic only
        # when a typed Signal-15/child edge exists and every other candidate
        # still collapses to that same canonical family.
        # A typed edge to an unnamed generation-0 client internal proves the
        # Projectile template, but not which displayed player skill owns the
        # effect.  Such rows include equipment/mastery/transformation
        # helpers and can legitimately appear on another profession.  They
        # must not enter the production player-skill mapping automatically.
        canonical_player_skills = tuple(
            skill_row_by_id.get(canonical_id)
            for canonical_id in sorted(canonical_ids)
        )
        has_displayed_player_owner = bool(canonical_player_skills) and all(
            _is_displayed_player_skill(row) for row in canonical_player_skills
        )
        # An internal/NPC/helper SkillLevel may canonicalize to a displayed
        # player family while emitting a different runtime object. Requiring
        # every raw typed/name candidate to be a displayed player skill keeps
        # that mirror edge out of the production deterministic set.
        has_only_displayed_player_candidates = bool(candidate_ids) and all(
            _is_displayed_player_skill(skill_row_by_id.get(candidate_id))
            or bool(skill_row_by_id.get(candidate_id, {}).get("verified_player_form"))
            for candidate_id in candidate_ids
        )
        deterministic = (
            len(typed_canonical_ids) == 1
            and canonical_ids == typed_canonical_ids
            and has_displayed_player_owner
            and has_only_displayed_player_candidates
        )
        if deterministic:
            deterministic_template_ids.append(template_id)
        projectile_evidence.append(
            {
                "projectile_template_id": template_id,
                "candidate_skill_ids": sorted(candidate_ids),
                "canonical_skill_ids": sorted(canonical_ids),
                "typed_signal_candidate_skill_ids": sorted(
                    typed_candidate_ids
                ),
                "exact_projectile_act_name_candidate_skill_ids": sorted(
                    projectile_act_name_to_skills.get(template_id, set())
                ),
                "deterministic_client_action_bridge": deterministic,
            }
        )

    classifications: dict[str, int] = defaultdict(int)
    for skill in skills:
        classifications[str(skill["classification"])] += 1
    known_examples = {
        "freezing_spike_2211_has_3003": any(
            row["skill_id"] == 2211
            and 3003 in row["projectile_template_ids"]
            for row in skills
        ),
        "freezing_spike_ex_2355_has_3032": any(
            row["skill_id"] == 2355
            and 3032 in row["projectile_template_ids"]
            for row in skills
        ),
        "guided_arrow_ex_1356_has_2036": any(
            row["skill_id"] == 1356
            and 2036 in row["projectile_template_ids"]
            for row in skills
        ),
        "swift_shot_ex_1355_has_exact_act_2035": any(
            row["skill_id"] == 1355
            and 2035 in row["exact_projectile_act_template_ids"]
            for row in skills
        ),
        "moonlight_splinter_208_has_1001": any(
            row["skill_id"] == 208
            and 1001 in row["projectile_template_ids"]
            for row in skills
        ),
    }
    return {
        "schema_version": 1,
        "resource_root": str(resource_root),
        "summary": {
            "skilltable_rows": len(skill_rows),
            "skilllevel_rows": level_rows,
            "skills_with_action_keys": len(action_keys_by_skill),
            "player_tree_act_files": len(all_player_tree_act_files),
            "excluded_non_player_act_files": (
                len(all_player_tree_act_files) - len(act_files)
            ),
            "player_act_files": len(act_files),
            "parsed_player_act_files": parsed_files,
            "act_parse_errors": len(parse_errors),
            "matched_action_keys": len(actions_by_key),
            "identifying_action_keys": len(identifying_action_keys),
            "projectile_templates_from_attack_instance_bridges": len(
                projectile_evidence
            ),
            "projectile_templates_from_typed_signals": len(
                typed_projectile_to_skills
            ),
            "projectile_act_files_parsed": sum(
                parsed is not None for parsed in parsed_projectile_acts.values()
            ),
            "projectile_act_parse_errors": len(projectile_act_parse_errors),
            "projectile_parent_templates": len(projectile_children),
            "projectile_child_edges": sum(
                len(children) for children in projectile_children.values()
            ),
            "deterministic_client_action_templates": len(deterministic_template_ids),
            "exact_projectile_act_name_templates": len(
                projectile_act_name_to_skills
            ),
            "classifications": dict(sorted(classifications.items())),
            "known_examples": known_examples,
        },
        "definitions": {
            "projectile_chain": (
                "SkillLevel exact ProcessParam action key -> ACT Signal 15 payload[0] "
                "-> weapontable_projectile.RowId -> runtime damage_object_id -> direct37"
            ),
            "projectile_act_name_chain": (
                "SkillLevel exact ProcessParam semantic name == Projectile ACT semantic "
                "name (namespace prefixes only are removed) -> "
                "weapontable_projectile.RowId. This is corroborating/candidate evidence, "
                "not independently deterministic."
            ),
            "direct_chain": (
                "SkillLevel exact ProcessParam action key -> ACT SI_DAMAGE (type 4) "
                "frame segment -> direct attack result"
            ),
            "deterministic_client_action_bridge": (
                "A typed ACT signal/child edge exists and all typed-signal and exact "
                "Projectile-ACT-name candidates map to one canonical skill family. "
                "Runtime ownership and object-generation checks are still required."
            ),
        },
        "deterministic_client_action_template_ids": deterministic_template_ids,
        "projectile_evidence": projectile_evidence,
        "projectile_child_edges": [
            {
                "parent_projectile_template_id": parent,
                "parent_act_file": projectile_act_files.get(parent, ""),
                "child_projectile_template_ids": sorted(children),
            }
            for parent, children in sorted(projectile_children.items())
        ],
        "skills": skills,
        "parse_errors": parse_errors,
        "projectile_act_parse_errors": projectile_act_parse_errors,
    }


def write_markdown(catalog: dict[str, Any], path: Path) -> None:
    summary = catalog["summary"]
    lines = [
        "# 客户端攻击实例目录（V576）",
        "",
        "## 结论",
        "",
        "客户端动作并不只有 Projectile。一条技能动作可产生投射物实例、直接伤害段，"
        "也可能两者兼有；附加伤害和 DOT 需要走独立状态/额外伤害链。",
        "",
        "本目录只使用 `SkillLevel._ProcessParam*` 的精确动作键、ACT 内类型化信号，"
        "以及去除命名空间前缀后完整相等的 Projectile ACT 名称。它不使用资源名"
        "包含/相似、伤害相似或时间最近作为确定性连接。",
        "",
        "## 扫描结果",
        "",
        f"- 玩家 ACT：{summary['parsed_player_act_files']} / {summary['player_act_files']} 份成功解析；",
        f"- 含动作键技能：{summary['skills_with_action_keys']}；",
        f"- 命中的动作键：{summary['matched_action_keys']}；",
        f"- Signal 15/父子链投射物模板：{summary['projectile_templates_from_typed_signals']}；",
        f"- 精确 Projectile ACT 名称桥：{summary['exact_projectile_act_name_templates']}；",
        f"- 攻击实例桥模板合计：{summary['projectile_templates_from_attack_instance_bridges']}；",
        f"- Projectile 父子边：{summary['projectile_child_edges']}；",
        f"- 确定性类型化技能族模板：{summary['deterministic_client_action_templates']}；",
        f"- ACT 解析错误：{summary['act_parse_errors']}。",
        "",
        "## 分类",
        "",
        "| 分类 | 技能数 |",
        "|---|---:|",
    ]
    for key, value in summary["classifications"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## 已知样例自检",
            "",
            "| 检查 | 结果 |",
            "|---|---|",
        ]
    )
    for key, value in summary["known_examples"].items():
        lines.append(f"| `{key}` | {'通过' if value else '失败'} |")
    lines.extend(
        [
            "",
            "## 重要边界",
            "",
            "- Signal 15 模板只有在运行时同时取得完整 `owner_actor_id + damage_object_id + generation` 后，才可归因 direct37。",
            "- 无职业、无显示名或 generation=0 的客户端内部技能不会自动升级为玩家技能映射。",
            "- SI_DAMAGE 只证明该动作存在直接攻击段，仍需包内攻击实例/固定技能字段完成运行时连接。",
            "- 突袭、冲击流、九天之舞二阶、连骨撞击额外伤害和 DOT 不得反向生成一次技能施放。",
            "- 精确 Projectile ACT 名称只作为候选/旁证，不能单独升级为确定性。",
            "- 解析不到类型化信号时保留待破解，不退回资源名模糊匹配。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_generated_python(catalog: dict[str, Any], path: Path) -> None:
    """Emit the reproducible deterministic client-ACT mapping table."""

    skills = {
        int(row["skill_id"]): row
        for row in catalog.get("skills", ())
    }
    rows: list[tuple[int, int, frozenset[int]]] = []
    for evidence in catalog.get("projectile_evidence", ()):
        if not evidence.get("deterministic_client_action_bridge"):
            continue
        canonical_ids = tuple(
            int(value) for value in evidence.get("canonical_skill_ids", ())
        )
        if len(canonical_ids) != 1:
            continue
        canonical_skill_id = canonical_ids[0]
        player_skill_ids = {
            int(skill_id)
            for skill_id in evidence.get("candidate_skill_ids", ())
            if (
                (skill := skills.get(int(skill_id))) is not None
                and (
                    _is_displayed_player_skill(skill)
                    or bool(skill.get("verified_player_form"))
                )
                and int(skill.get("canonical_skill_id", 0) or 0)
                == canonical_skill_id
            )
        }
        canonical_skill = skills.get(canonical_skill_id)
        if (
            canonical_skill is not None
            and bool(str(canonical_skill.get("name", "")).strip())
            and bool(str(canonical_skill.get("job", "")).strip())
            and int(canonical_skill.get("generation", 0) or 0) > 0
        ):
            player_skill_ids.add(canonical_skill_id)
        if not player_skill_ids:
            continue
        rows.append(
            (
                int(evidence["projectile_template_id"]),
                canonical_skill_id,
                frozenset(player_skill_ids),
            )
        )

    lines = [
        "from __future__ import annotations",
        "",
        "# Generated by tools/build_attack_instance_catalog.py.",
        "# Evidence chain: one canonical SkillLevel action key -> player-only ACT",
        "# typed signal 15/child Projectile -> unique canonical skill family.",
        "# Runtime attribution still requires full owner_actor_id +",
        "# damage_object_id + local registration generation.",
        "CLIENT_ACT_GENERATED_DAMAGE_OBJECT_SPECS: tuple[",
        "    tuple[int, int, frozenset[int]], ...",
        "] = (",
    ]
    for template_id, canonical_skill_id, skill_ids in sorted(rows):
        rendered_skill_ids = ", ".join(str(value) for value in sorted(skill_ids))
        lines.append(
            f"    ({template_id}, {canonical_skill_id}, "
            f"frozenset({{{rendered_skill_ids}}})),"
        )
    lines.extend((")", ""))
    lines.extend(
        (
            "# Exact Projectile -> child Projectile edges parsed from the client ACT",
            "# resources. Runtime code must still bind both objects to the same owner",
            "# and live object generation; this table alone never attributes a Hit.",
            "CLIENT_ACT_GENERATED_PROJECTILE_CHILD_EDGES: tuple[",
            "    tuple[int, frozenset[int]], ...",
            "] = (",
        )
    )
    for edge in catalog.get("projectile_child_edges", ()):
        parent_template_id = int(edge["parent_projectile_template_id"])
        child_template_ids = sorted(
            int(value)
            for value in edge.get("child_projectile_template_ids", ())
            if int(value) > 0
        )
        if not child_template_ids:
            continue
        rendered_child_ids = ", ".join(
            str(value) for value in child_template_ids
        )
        lines.append(
            f"    ({parent_template_id}, "
            f"frozenset({{{rendered_child_ids}}})),"
        )
    lines.extend((")", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an exact SkillLevel action -> typed ACT attack-instance catalog."
    )
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument(
        "--generated-python", type=Path, default=DEFAULT_GENERATED_PYTHON
    )
    args = parser.parse_args()
    catalog = build_catalog(args.resource_root.resolve())
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(catalog, args.markdown)
    write_generated_python(catalog, args.generated_python)
    print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))
    print(args.json)
    print(args.markdown)
    print(args.generated_python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
