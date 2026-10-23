from __future__ import annotations

import json
import re
import struct
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = Path(r"G:\Resource00\resource")
SKILL_TABLE = RESOURCE_ROOT / "ext" / "skilltable_character.dnt"
UISTRING = RESOURCE_ROOT / "uistring" / "uistring.xml"
OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "status_skills_v576.json"
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
        name_len = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        name = data[offset : offset + name_len].decode("ascii")
        offset += name_len
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
                value_len = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                value = data[offset : offset + value_len].decode(
                    "utf-8", errors="replace"
                )
                offset += value_len
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


def load_ui_strings(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        int(match.group(1)): match.group(2).replace("\\n", " ").strip()
        for match in re.finditer(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>',
            text,
            re.S,
        )
    }


def main() -> None:
    ui_strings = load_ui_strings(UISTRING)
    skills: dict[str, str] = {}
    for row in parse_dnt(SKILL_TABLE):
        skill_id = int(row["RowId"])
        name = ui_strings.get(int(row.get("_NameID") or 0), "").strip()
        if name:
            skills[str(skill_id)] = name
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {"profile": "v576", "skills": skills},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(skills)} status-skill names to {OUTPUT}")


if __name__ == "__main__":
    main()
