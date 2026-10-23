from __future__ import annotations

import json
import struct
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_EXT = Path(r"G:\Resource00\resource\ext")
OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "status_effect_catalog_v576.json"
)


GROUPS: dict[int, dict[str, object]] = {
    0: {"display_name": "后缀·额外物理伤害"},
    1: {"display_name": "后缀·额外魔法伤害"},
    2: {
        "display_name": "后缀·减硬直/眩晕抵抗",
        "rule": {"key": "减硬直/眩晕抵抗%", "group": "装备后缀·减硬直/眩晕抵抗"},
        "value_slots": [2, 3],
    },
    3: {
        "display_name": "疾风后缀",
        "rule": {"key": "减致命抵抗%", "group": "减致命抵抗"},
        "value_slots": [2],
    },
    4: {
        "display_name": "后缀·减全属性抵抗",
        "rule": {"key": "减全抗%", "group": "装备后缀·减全抗"},
        "value_slots": [2, 3, 4, 5],
    },
    5: {"display_name": "后缀·持续物理伤害"},
    6: {"display_name": "后缀·额外暗属性伤害"},
    7: {"display_name": "后缀·生命恢复"},
    8: {"display_name": "后缀·魔法恢复"},
    9: {"display_name": "后缀·持续魔法恢复"},
    10: {"display_name": "后缀·双重攻击"},
    11: {
        "display_name": "后缀·禁足",
        "rule": {"key": "禁足", "group": "装备后缀·禁足"},
        "value_slots": [],
    },
    12: {"display_name": "后缀·额外伤害/击倒"},
    13: {
        "display_name": "后缀·破坏超级盔甲",
        "rule": {"key": "破坏超级盔甲", "group": "装备后缀·破坏超级盔甲"},
        "value_slots": [],
    },
    14: {
        "display_name": "后缀·减速",
        "rule": {"key": "减移动速度%", "group": "装备后缀·减速"},
        "value_slots": [1, 2],
    },
    15: {
        "display_name": "铁壁后缀",
        "rule": {"key": "减物理防御%", "group": "client:state-effect:20013"},
        "value_slots": [1],
    },
    16: {
        "display_name": "帷幕后缀",
        "rule": {"key": "减魔法防御%", "group": "client:state-effect:20014"},
        "value_slots": [1],
    },
    17: {"display_name": "后缀·额外火属性伤害"},
    18: {"display_name": "后缀·额外水属性伤害"},
    19: {"display_name": "后缀·额外光属性伤害"},
}


def parse_dnt(path: Path) -> list[dict[str, object]]:
    data = path.read_bytes()
    offset = 4
    column_count = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    row_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    fields: list[tuple[str, int]] = []
    for _ in range(column_count):
        name_length = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        name = data[offset : offset + name_length].decode("ascii")
        offset += name_length
        field_type = data[offset]
        offset += 1
        fields.append((name, field_type))

    rows: list[dict[str, object]] = []
    for _ in range(row_count):
        row: dict[str, object] = {
            "RowId": struct.unpack_from("<I", data, offset)[0]
        }
        offset += 4
        for name, field_type in fields:
            if field_type == 1:
                value_length = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                value = data[offset : offset + value_length].decode(
                    "utf-8", errors="replace"
                )
                offset += value_length
            elif field_type in (2, 3):
                value = struct.unpack_from("<i", data, offset)[0]
                offset += 4
            elif field_type == 5:
                value = struct.unpack_from("<f", data, offset)[0]
                offset += 4
            else:
                raise ValueError(f"Unknown DNT field type {field_type}: {name}")
            row[name] = value
        rows.append(row)
    return rows


def _number(value: object) -> float | None:
    try:
        return float(str(value).split(";", 1)[0])
    except (TypeError, ValueError):
        return None


def _single_value(values: set[float]) -> float | None:
    if len(values) != 1:
        return None
    return round(abs(next(iter(values))) * 100.0, 6)


def main() -> None:
    skills = parse_dnt(RESOURCE_EXT / "skilltable_prefixwep.dnt")
    levels_by_skill: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in parse_dnt(RESOURCE_EXT / "skillleveltable_prefixwep.dnt"):
        # ApplyType 0 is the PvE row. The table also repeats every level for PvP.
        if int(row.get("_ApplyType") or 0) == 0:
            levels_by_skill[int(row["_SkillIndex"])].append(row)

    statuses: dict[str, dict[str, object]] = {}
    for skill in skills:
        skill_id = int(skill["RowId"])
        group = int(skill.get("_Group") or 0)
        definition = GROUPS[group]
        value_slots = tuple(int(value) for value in definition.get("value_slots", ()))
        values: set[float] = set()
        durations: set[int] = set()
        state_effect_ids: set[int] = set()
        for level in levels_by_skill.get(skill_id, ()):
            for slot in value_slots:
                value = _number(level.get(f"_EffectClassValue{slot}"))
                if value is not None:
                    values.add(round(value, 8))
                duration = int(level.get(f"_EffectClassValue{slot}Duration") or 0)
                if duration > 0:
                    durations.add(duration)
            raw_state_id = str(level.get("_StateEffectTableID") or "0").strip()
            for part in raw_state_id.split(";"):
                try:
                    state_effect_id = int(part)
                except ValueError:
                    continue
                if state_effect_id > 0:
                    state_effect_ids.add(state_effect_id)

        statuses[str(skill_id)] = {
            "group": group,
            "effect_classes": [
                int(skill.get(f"_EffectClass{index}") or 0)
                for index in range(1, 11)
                if int(skill.get(f"_EffectClass{index}") or 0) > 0
            ],
            "state_effect_ids": sorted(state_effect_ids),
            "fallback_value": _single_value(values),
            "fallback_duration_ms": (
                next(iter(durations)) if len(durations) == 1 else None
            ),
        }

    serializable_groups = {
        str(group): {
            key: value
            for key, value in definition.items()
            if key != "value_slots"
        }
        for group, definition in GROUPS.items()
    }
    payload = {
        "profile": "v576",
        "source_tables": [
            "skilltable_prefixwep.dnt",
            "skillleveltable_prefixwep.dnt",
        ],
        "description": (
            "Equipment suffix status IDs extracted from the v576 resource. "
            "Packet values and durations take precedence over fallbacks."
        ),
        "groups": serializable_groups,
        "statuses": statuses,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(statuses)} equipment status IDs to {OUTPUT}")


if __name__ == "__main__":
    main()
