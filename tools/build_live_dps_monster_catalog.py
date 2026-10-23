from __future__ import annotations

import json
import re
import struct
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_EXT = Path(r"G:\Resource00\resource\ext")
UISTRING = Path(r"G:\Resource00\resource\uistring\uistring.xml")
OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "monster_templates_v576.json"
)
TABLE_NAMES = (
    "monstertable.dnt",
    "monstertable_bot.dnt",
    "monstertable_champion.dnt",
    "monstertable_darklair.dnt",
    "monstertable_event.dnt",
    "monstertable_nest.dnt",
    "monstertable_nest70.dnt",
    "monstertable_nestch.dnt",
    "monstertable_quest.dnt",
    "monstertable_transform.dnt",
)


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
            elif field_type in (4, 5):
                value = struct.unpack_from("<f", data, offset)[0]
                offset += 4
            else:
                raise ValueError(f"Unknown DNT field type {field_type}: {name}")
            row[name] = value
        rows.append(row)
    return rows


def load_uistring() -> dict[int, str]:
    text = UISTRING.read_text(encoding="utf-8", errors="replace")
    return {
        int(match.group(1)): match.group(2).replace("\\n", "\n")
        for match in re.finditer(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>',
            text,
            re.S,
        )
    }


def localized_name(row: dict[str, object], strings: dict[int, str]) -> str:
    base = strings.get(int(row.get("_NameID") or 0), "").strip()
    parameter_ids = [
        int(value)
        for value in re.findall(r"\d+", str(row.get("_NameIDParam") or ""))
    ]
    parameters = [strings.get(value, "").strip() for value in parameter_ids]
    if base and parameters:
        try:
            return base.format(*parameters).strip()
        except (IndexError, KeyError, ValueError):
            pass
    return (base or " ".join(value for value in parameters if value)).strip()


def rounded_percent(value: object) -> float:
    return round(float(value or 0) * 100.0, 4)


def main() -> None:
    strings = load_uistring()
    templates: dict[str, dict[str, object]] = {}
    for table_name in TABLE_NAMES:
        path = RESOURCE_EXT / table_name
        if not path.exists():
            continue
        for row in parse_dnt(path):
            template_id = int(row["RowId"])
            name = localized_name(row, strings)
            templates[str(template_id)] = {
                "name": name or f"怪物模板 {template_id}",
                "level": int(row.get("_Level") or 0),
                "hp": int(row.get("_HP") or 0),
                "physical_defense": int(row.get("_DefP") or 0),
                "magic_defense": int(row.get("_DefM") or 0),
                "critical_resistance": int(row.get("_CriticalResistance") or 0),
                "fire_resistance": rounded_percent(row.get("_Fire_Def")),
                "ice_resistance": rounded_percent(row.get("_Ice_Def")),
                "light_resistance": rounded_percent(row.get("_Light_Def")),
                "dark_resistance": rounded_percent(row.get("_Dark_Def")),
                "actor_table_id": int(row.get("_ActorTableID") or 0),
                "boss": bool(int(row.get("_BossKillCheck") or 0)),
                "source": table_name,
            }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {"version": "v576", "templates": templates},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(templates)} monster templates to {OUTPUT}")


if __name__ == "__main__":
    main()
