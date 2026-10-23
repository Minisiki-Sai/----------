"""Audit exact player action -> skill-actor template constructor edges.

This is static evidence only.  A row proves which player skill constructs a
template; it does not by itself prove which private monster attack slot caused
an individual hit.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_attack_instance_catalog import ActParseError, parse_act
from tools.build_live_dps_monster_catalog import parse_dnt


def _actions(row: dict) -> set[str]:
    result: set[str] = set()
    for i in range(1, 11):
        value = str(row.get(f"_ProcessParam{i}", "")).strip()
        if value and ";" not in value and re.search(r"[A-Za-z_]", value):
            result.add(value)
        result.update(re.findall(r"(?:Skill|Attack)_[A-Za-z0-9_]+", value))
    return result


def build(resource: Path) -> dict:
    ext = resource / "ext"
    skills = {row["RowId"]: row for row in parse_dnt(ext / "skilltable_character.dnt")}

    def canonical(skill: int) -> int:
        seen: set[int] = set()
        while skill in skills and int(skills[skill].get("_BaseSkillID", 0)) > 0:
            if skill in seen:
                raise ValueError(f"cyclic skill family: {skill}")
            seen.add(skill)
            skill = int(skills[skill]["_BaseSkillID"])
        return skill

    by_action: dict[str, set[int]] = collections.defaultdict(set)
    for cls in ("academic", "archer", "assassin", "cleric", "etc", "kali", "soceress", "warrior"):
        for row in parse_dnt(ext / f"skillleveltable_character{cls}.dnt"):
            sid = int(row["_SkillIndex"])
            if sid not in skills or int(skills[sid].get("_NeedJob", 0)) <= 0:
                continue
            for action in _actions(row):
                by_action[action.casefold()].add(canonical(sid))

    rows: list[dict] = []
    invalid: list[str] = []
    for path in resource.rglob("*.act"):
        normalized = path.as_posix().lower()
        if "/char/player/" not in normalized or "skill" not in path.name.lower():
            continue
        try:
            parsed = parse_act(path)
        except ActParseError:
            invalid.append(path.relative_to(resource).as_posix())
            continue
        for action in parsed.actions:
            families = sorted(by_action.get(action.name.casefold(), set()))
            for signal in action.signals:
                if signal.signal_type != 126 or len(signal.payload) < 4:
                    continue
                rows.append({
                    "template": int.from_bytes(signal.payload[:4], "little"),
                    "action": action.name,
                    "families": families,
                    "unique_family": families[0] if len(families) == 1 else None,
                    "act": path.relative_to(resource).as_posix(),
                })

    grouped: dict[int, dict] = {}
    for template in sorted({row["template"] for row in rows}):
        matching = [row for row in rows if row["template"] == template]
        families = sorted({family for row in matching for family in row["families"]})
        grouped[template] = {
            "template": template,
            "families": families,
            "unique_family": families[0] if len(families) == 1 else None,
            "evidence": matching,
        }
    return {
        "schema": "player-skill-actor-constructor-v97",
        "policy": "Exact player ACT type-126 constructor; no timing or class-only inference.",
        "templates": list(grouped.values()),
        "unique_templates": sum(row["unique_family"] is not None for row in grouped.values()),
        "ambiguous_or_unmapped_templates": sum(row["unique_family"] is None for row in grouped.values()),
        "invalid_legacy_act_files": invalid,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource", type=Path,
        default=Path("E:/ext0811.rar/Resource00/Resource00/resource"))
    parser.add_argument("--output", type=Path,
        default=ROOT / "outputs/player_skill_actor_constructors_v97_20260910.json")
    args = parser.parse_args()
    result = build(args.resource)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({
        "templates": len(result["templates"]),
        "unique": result["unique_templates"],
        "ambiguous": result["ambiguous_or_unmapped_templates"],
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
