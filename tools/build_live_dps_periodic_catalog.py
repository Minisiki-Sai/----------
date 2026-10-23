from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from build_live_dps_status_catalog import load_ui_strings, parse_dnt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_EXT = Path(r"G:\Resource00\resource\ext")
UISTRING = Path(r"G:\Resource00\resource\uistring\uistring.xml")
OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "periodic_damage_skills_v576.json"
)

# These EffectClass values are the player-side status damage families in v576.
# Freeze/chill (144) is deliberately excluded because it does not itself deal
# periodic damage.
PERIODIC_EFFECT_CLASSES = {
    42: ("burn", "烧伤"),
    43: ("shock", "触电"),
    44: ("poison", "中毒"),
    136: ("disease", "疾病中毒"),
    244: ("curse", "诅咒伤害"),
}

# Internal summon/effect skills have no BaseSkillID. Map them back to the
# player-facing skill that created the status.
INTERNAL_SOURCE_ALIASES = {
    30612: 3409,  # 雷电之手内部攻击
    30616: 3409,  # 雷电之手EX内部攻击
    30810: 4405,  # 魔法草火焰攻击
    30812: 4405,  # 魔法草中毒攻击
    30813: 4410,  # 岩浆墙召唤体
    30820: 5216,  # 灵魂之门
    30823: 5216,
    30824: 5216,
}


def _normalize_parameters(value: object) -> str:
    tokens: list[str] = []
    for raw_token in str(value or "").split(";"):
        token = raw_token.strip()
        if not token:
            tokens.append("")
            continue
        try:
            number = float(token)
        except ValueError:
            tokens.append(token)
            continue
        tokens.append(f"{number:.12g}")
    return ";".join(tokens)


def _is_player_periodic_skill(skill_id: int) -> bool:
    return (
        0 < skill_id < 10_000
        or 30_000 <= skill_id < 40_000
        or 260_000 <= skill_id < 262_000
    )


def main() -> None:
    ui_strings = load_ui_strings(UISTRING)
    skill_rows = {
        int(row["RowId"]): row
        for row in parse_dnt(RESOURCE_EXT / "skilltable_character.dnt")
    }
    level_rows: dict[int, list[dict[str, object]]] = defaultdict(list)
    for path in sorted(RESOURCE_EXT.glob("skillleveltable_character*.dnt")):
        for row in parse_dnt(path):
            level_rows[int(row["_SkillIndex"])].append(row)

    output_rows: dict[str, list[dict[str, object]]] = {}
    for skill_id, skill in sorted(skill_rows.items()):
        if not _is_player_periodic_skill(skill_id):
            continue
        source_skill_id = int(skill.get("_BaseSkillID") or 0) or skill_id
        if 260_000 <= skill_id < 262_000:
            source_skill_id = 4217  # 重磅轰击的逐等级内部技能
        source_skill_id = INTERNAL_SOURCE_ALIASES.get(skill_id, source_skill_id)
        source = skill_rows.get(source_skill_id, skill)
        source_name = ui_strings.get(int(source.get("_NameID") or 0), "").strip()
        if not source_name:
            source_name = str(source.get("_StaticName") or source_skill_id).strip()

        rules: list[dict[str, object]] = []
        for slot in range(1, 11):
            effect_class = int(skill.get(f"_EffectClass{slot}") or 0)
            apply_type = int(skill.get(f"_EffectClass{slot}ApplyType") or 0)
            kind_info = PERIODIC_EFFECT_CLASSES.get(effect_class)
            if kind_info is None or apply_type != 1:
                continue
            variants = {
                (
                    _normalize_parameters(level.get(f"_EffectClassValue{slot}")),
                    int(level.get(f"_EffectClassValue{slot}Duration") or 0),
                )
                for level in level_rows.get(skill_id, ())
            }
            variants = {
                value for value in variants if value[0] and value[1] > 0
            }
            kind, label = kind_info
            rules.append(
                {
                    "source_skill_id": source_skill_id,
                    "source_skill_name": source_name,
                    "kind": kind,
                    "label": label,
                    "effect_class": effect_class,
                    "variants": [
                        {"parameters": parameters, "duration_ms": duration_ms}
                        for parameters, duration_ms in sorted(variants)
                    ],
                }
            )
        if rules:
            output_rows[str(skill_id)] = rules

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {
                "profile": "v576",
                "source": "skilltable_character + skillleveltable_character*",
                "skills": output_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(output_rows)} periodic skill IDs to {OUTPUT}")


if __name__ == "__main__":
    main()
