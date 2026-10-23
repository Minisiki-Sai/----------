from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_dungeon_map_catalog import parse_dnt


RESOURCE_EXT = Path(
    os.environ.get(
        "DRAGON_NEST_RESOURCE_ROOT",
        r"E:\ext0811.rar\Resource00\Resource00\resource",
    )
) / "ext"
GLYPH_LIBRARY = (
    ROOT
    / "dragon_nest_builder"
    / "data"
    / "modular"
    / "tables"
    / "skills"
    / "skill_glyphs.json"
)
OUTPUT = (
    ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "skill_application_ratio_baselines_v576.json"
)
EFFECT = "攻击力应用比例增加"
# These four stance/channel skills are the only obtainable glyphs whose text
# says that it adds percentage points to the attack-application ratio.  The
# ratio is not always stored in the same EffectClass slot, so keep the slot
# mapping explicit instead of treating every numeric SkillLevel parameter as
# a possible base value.  In particular, 包围 owns an unrelated class-125
# value of 0.05; accepting that value used to create a false +5% glyph.
APPLICATION_RATIO_VALUE_SLOTS = {
    207: 2,   # 挥砍姿态
    353: 1,   # 猛砍站姿
    1220: 1,  # 包围
    7504: 1,  # 黑暗冲浪
}


def numeric_value(row: dict[str, object], slot: int) -> float | None:
    raw = str(row.get(f"_EffectClassValue{slot}") or "").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def main() -> None:
    library = json.loads(GLYPH_LIBRARY.read_text(encoding="utf-8"))
    skill_ids = {
        int(row.get("skill_id") or 0)
        for row in library.get("entries", [])
        if isinstance(row, dict)
        and EFFECT in (row.get("effects") or {})
        and int(row.get("skill_id") or 0) > 0
    }
    skill_table = {
        int(row.get("RowId") or 0): row
        for row in parse_dnt(RESOURCE_EXT / "skilltable_character.dnt")
    }
    rows: dict[str, dict[str, object]] = {}
    for path in sorted(RESOURCE_EXT.glob("skillleveltable_character*.dnt")):
        for row in parse_dnt(path):
            skill_id = int(row.get("_SkillIndex") or 0)
            row_id = int(row.get("RowId") or 0)
            if skill_id not in skill_ids or row_id <= 0:
                continue
            ratio_slot = APPLICATION_RATIO_VALUE_SLOTS.get(skill_id, 0)
            ratio_value = numeric_value(row, ratio_slot)
            definition = skill_table.get(skill_id, {})
            ratio_effect_class_id = int(
                definition.get(f"_EffectClass{ratio_slot}") or 0
            )
            if ratio_value is None or ratio_effect_class_id <= 0:
                continue
            rows[str(row_id)] = {
                "skill_id": skill_id,
                "skill_level": int(row.get("_SkillLevel") or 0),
                "ratio_slot": ratio_slot,
                "ratio_effect_class_id": ratio_effect_class_id,
                "ratio_value": ratio_value,
            }
    payload = {
        "schema_version": 2,
        "protocol_version": 576,
        "effect": EFFECT,
        "skill_ids": sorted(skill_ids),
        "rows": dict(sorted(rows.items(), key=lambda item: int(item[0]))),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} rows for {len(skill_ids)} skills to {OUTPUT}")


if __name__ == "__main__":
    main()
