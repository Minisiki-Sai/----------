from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "skill_glyph_review" / "skill_glyphs_extracted.json"
TARGET = (
    ROOT
    / "dragon_nest_builder"
    / "data"
    / "modular"
    / "tables"
    / "skills"
    / "skill_glyphs.json"
)

ALLOWED_EFFECTS = {
    "伤害增加",
    "冷却缩减",
    "增益持续",
    "动作速度增加",
    "攻击力应用比例增加",
}
GRADE_ORDER = {"低档": 0, "中档": 1, "高档": 2}


def excluded_skill(job: str, skill: str) -> bool:
    if job == "未匹配":
        return True
    if job in {"猎人", "银色猎人"} and (
        skill.startswith("[神圣]")
        or "（即时）" in skill
        or "(即时)" in skill
    ):
        # These are combat variants of the same base skill and do not own a
        # second physical glyph item.
        return True
    if job != "复仇者":
        return False
    return "[噩梦]" in skill or "（即时）" in skill or "(即时)" in skill


def main() -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    grouped: dict[tuple[str, str], dict[str, list[dict[str, object]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    skill_ids: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in source.get("detail", []):
        job = str(row.get("job", "")).strip()
        skill = str(row.get("skill", "")).strip()
        effect = str(row.get("effect_type", "")).strip()
        if "动作速度" in str(row.get("effect_text", "")):
            # Older extraction files categorized action-speed glyphs as “其他”.
            # Keep the builder backward-compatible so the library can be rebuilt
            # without first regenerating the several-thousand-row raw workbook.
            effect = "动作速度增加"
        if job in {"猎人", "银色猎人"} and effect == "增益持续":
            # Hunter glyph choices in the simulator intentionally keep only
            # damage, cooldown and action-speed effects.
            continue
        if not job or not skill or effect not in ALLOWED_EFFECTS or excluded_skill(job, skill):
            continue
        option = {
            "grade": str(row.get("grade", "")).strip(),
            "value": float(row.get("value", 0) or 0),
        }
        group_key = (job, skill)
        skill_ids[group_key].add(int(row.get("skill_id", 0) or 0))
        bucket = grouped[group_key][effect]
        if option not in bucket:
            bucket.append(option)

    entries = []
    for (job, skill), effects in sorted(grouped.items()):
        # One physical glyph item may list the ordinary skill id and one or
        # more transformed ids.  The frontend selects by display name, so
        # emitting one row per id creates duplicate skills and hides later
        # effects behind Array.find().
        skill_id = min((value for value in skill_ids[(job, skill)] if value > 0), default=0)
        normalized_effects = {}
        for effect, options in effects.items():
            normalized_effects[effect] = sorted(
                options,
                key=lambda option: (
                    GRADE_ORDER.get(str(option.get("grade", "")), 99),
                    float(option.get("value", 0) or 0),
                ),
            )
        entries.append(
            {
                "job": job,
                "skill_id": skill_id,
                "skill": skill,
                "effects": normalized_effects,
            }
        )

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(entries)} skills to {TARGET}")


if __name__ == "__main__":
    main()
