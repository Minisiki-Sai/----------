import json
import os
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path


RESOURCE_ROOT = Path(
    os.environ.get(
        "DRAGON_NEST_RESOURCE_ROOT",
        r"E:\ext0811.rar\Resource00\Resource00\resource",
    )
)
RESOURCE_EXT = RESOURCE_ROOT / "ext"
UISTRING = RESOURCE_ROOT / "uistring" / "uistring0818.xml"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "outputs" / "skill_glyph_review"
REBORN_SKILL_INDEX = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "modular"
    / "tables"
    / "skills"
    / "dngamer_skill_sim_reborn_split"
    / "index.json"
)

# The reborn skill index bundled with the simulator predates the Hunter jobs.
# ItemTable still carries the authoritative class ids for their skill glyphs.
GLYPH_ITEM_JOB_NAMES = {80: "猎人", 81: "银色猎人"}


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def parse_dnt(path: Path) -> list[dict]:
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

    rows: list[dict] = []
    for _ in range(row_count):
        row = {"RowId": struct.unpack_from("<I", data, offset)[0]}
        offset += 4
        for name, field_type in fields:
            if field_type == 1:
                value_len = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                value = data[offset : offset + value_len].decode("utf-8", errors="replace")
                offset += value_len
            elif field_type in (2, 3, 4):
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


def load_uistring() -> dict[int, str]:
    text = UISTRING.read_text(encoding="utf-8", errors="replace")
    return {
        int(match.group(1)): match.group(2).replace("\\n", "\n")
        for match in re.finditer(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>', text, re.S
        )
    }


def load_job_names() -> dict[int, str]:
    result: dict[int, str] = {}
    data = json.loads(REBORN_SKILL_INDEX.read_text(encoding="utf-8"))
    jobs = data.get("jobs", {})
    rows = jobs.get("files", []) if isinstance(jobs, dict) else jobs
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            result[int(row.get("job_id"))] = str(row.get("name") or "")
        except (TypeError, ValueError):
            continue
    return result


def split_params(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def resolve_param(value: str, ui: dict[int, str]) -> str:
    match = re.fullmatch(r"\{(\d+)\}", value)
    if match:
        return ui.get(int(match.group(1)), value)
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"
    except ValueError:
        return value


def clean_text(value: str) -> str:
    value = re.sub(r"#[A-Za-z]", "", value or "")
    value = value.replace("\r", "").replace("\n", u(r"\uff1b"))
    return re.sub(r"\s+", " ", value).strip()


def render_summary(row: dict, ui: dict[int, str]) -> tuple[str, list[str]]:
    try:
        template = ui.get(int(row.get("_SummaryDescription") or 0), "")
    except (TypeError, ValueError):
        template = ""
    params = [resolve_param(value, ui) for value in split_params(row.get("_SummaryDescriptionParam"))]
    rendered = template
    for index, value in enumerate(params):
        rendered = rendered.replace("{" + str(index) + "}", value)
    return clean_text(rendered), params


def render_item_description(row: dict, ui: dict[int, str]) -> tuple[str, list[str]]:
    try:
        template = ui.get(int(row.get("_DescriptionID") or 0), "")
    except (TypeError, ValueError):
        template = ""
    params = [
        resolve_param(value, ui)
        for value in split_params(row.get("_DescriptionIDParam"))
    ]
    rendered = template
    for index, value in enumerate(params):
        rendered = rendered.replace("{" + str(index) + "}", value)
    return clean_text(rendered), params


def effect_type(text: str) -> str:
    candidates = [
        # Stance-style plates add percentage points to the skill's attack
        # application ratio (for example 500% -> 520%).  This is different
        # from a normal damage plate and must be classified before the broad
        # damage keyword below.
        (u(r"\u653b\u51fb\u529b\u7684\u5e94\u7528\u6bd4\u7387\u63d0\u5347"), u(r"\u653b\u51fb\u529b\u5e94\u7528\u6bd4\u4f8b\u589e\u52a0")),
        (u(r"\u521d\u59cb\u5316"), u(r"\u51b7\u5374\u91cd\u7f6e")),
        (u(r"\u53d7\u5230\u7684\u4f24\u5bb3\u51cf\u5c11"), u(r"\u4f24\u5bb3\u51cf\u514d")),
        (u(r"\u4f24\u5bb3"), u(r"\u4f24\u5bb3\u589e\u52a0")),
        (u(r"\u51b7\u5374\u65f6\u95f4"), u(r"\u51b7\u5374\u7f29\u51cf")),
        (u(r"\u52a8\u4f5c\u901f\u5ea6"), u(r"\u52a8\u4f5c\u901f\u5ea6\u589e\u52a0")),
        (u(r"\u6d88\u8017\u9b54\u6cd5"), u(r"\u8017\u84dd\u51cf\u5c11")),
        (u(r"\u589e\u76ca\u6548\u679c"), u(r"\u589e\u76ca\u6301\u7eed")),
        (u(r"\u51cf\u76ca\u6548\u679c"), u(r"\u51cf\u76ca\u6301\u7eed")),
        (u(r"\u81f4\u547d\u4e00\u51fb"), u(r"\u81f4\u547d\u6982\u7387")),
    ]
    for keyword, label in candidates:
        if keyword in text:
            return label
    return u(r"\u5176\u4ed6")


def extract_numeric_value(params: list[str], text: str) -> float:
    for value in reversed(params):
        try:
            return float(value)
        except ValueError:
            pass
    matches = re.findall(r"(-?\d+(?:\.\d+)?)%", text)
    return float(matches[-1]) if matches else 0.0


def build_rows() -> tuple[list[dict], list[dict]]:
    ui = load_uistring()
    job_names = load_job_names()
    skills = parse_dnt(RESOURCE_EXT / "skilltable_character.dnt")
    skill_by_id = {int(row["RowId"]): row for row in skills}
    # GlyphSkillTable contains unused template rows (for example a 25% Hunter
    # tier that has no corresponding item).  Only rows backed by an active
    # type-38 item are obtainable in game.  The item description also contains
    # the real 10/20 and 16/20 values where the template summary still says 0.
    glyph_items = {
        int(row["RowId"]): row
        for row in parse_dnt(RESOURCE_EXT / "itemtable_glyph.dnt")
        if int(row.get("_Type") or 0) == 38
    }
    glyph_rows = [
        row
        for row in parse_dnt(RESOURCE_EXT / "glyphskilltable.dnt")
        if str(row.get("_SkillID", "0")) not in {"", "0"}
        and int(row.get("RowId") or 0) in glyph_items
    ]

    exact_counts: Counter[tuple] = Counter()
    prepared: list[dict] = []
    for row in glyph_rows:
        item = glyph_items[int(row["RowId"])]
        raw_skill_ids = str(row["_SkillID"])
        skill_ids = [
            int(value)
            for value in re.findall(r"\d+", raw_skill_ids)
            if int(value) > 0
        ]
        summary, params = render_item_description(item, ui)
        if not summary:
            summary, params = render_summary(row, ui)
        numeric = extract_numeric_value(params, summary)
        for skill_id in skill_ids:
            skill = skill_by_id.get(skill_id, {})
            name = ui.get(int(skill.get("_NameID") or 0), "") or (params[0] if params else "")
            name = clean_text(name)
            job_id = int(skill.get("_NeedJob") or 0)
            try:
                item_job_id = int(str(item.get("_NeedJobClass") or "0").split(",")[0])
            except ValueError:
                item_job_id = 0
            job = job_names.get(job_id, "") or GLYPH_ITEM_JOB_NAMES.get(item_job_id, "")
            key = (skill_id, summary, int(row.get("_GlyphSkillEffect") or 0), round(numeric, 6))
            exact_counts[key] += 1
            prepared.append(
                {
                    "job": job or u(r"\u672a\u5339\u914d"),
                    "job_id": job_id,
                    "skill_id": skill_id,
                    "source_skill_ids": raw_skill_ids,
                    "skill": name or f"Skill {skill_id}",
                    "effect_type": effect_type(summary),
                    "effect_text": summary,
                    "value": numeric,
                    "glyph_effect_code": int(row.get("_GlyphSkillEffect") or 0),
                    "glyph_effect_value": round(float(row.get("_GlyphSkillEffectValue") or 0), 6),
                    "skill_level_id": int(row.get("_SkillLevelID") or 0),
                    "summary_id": str(row.get("_SummaryDescription") or ""),
                    "summary_params": str(row.get("_SummaryDescriptionParam") or ""),
                    "row_id": int(row.get("RowId") or 0),
                    "item_rank": int(item.get("_Rank") or 0),
                    "dedup_key": key,
                    "grade_group": (skill_id, str(row.get("_SummaryDescription") or "")),
                }
            )

    deduped: dict[tuple, dict] = {}
    for row in prepared:
        deduped.setdefault(row["dedup_key"], row)
    detail = list(deduped.values())

    values_by_group: dict[tuple, list[float]] = defaultdict(list)
    for row in detail:
        values_by_group[row["grade_group"]].append(row["value"])
    for row in detail:
        values = sorted(set(values_by_group[row["grade_group"]]))
        if row.get("item_rank") == 2:
            grade = u(r"\u4f4e\u6863")
        elif row.get("item_rank") == 3:
            grade = u(r"\u9ad8\u6863")
        elif len(values) <= 1:
            grade = u(r"\u552f\u4e00\u6863")
        else:
            index = values.index(row["value"])
            labels = [u(r"\u4f4e\u6863"), u(r"\u4e2d\u6863"), u(r"\u9ad8\u6863")]
            grade = labels[min(2, round(index * 2 / max(1, len(values) - 1)))]
        row["grade"] = grade
        row["record_count"] = exact_counts[row["dedup_key"]]
        row.pop("dedup_key", None)
        row.pop("grade_group", None)

    detail.sort(key=lambda row: (row["job"], row["skill"], row["effect_type"], row["value"]))
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in detail:
        grouped[(row["job"], row["skill_id"], row["skill"])].append(row)
    summary_rows: list[dict] = []
    for (job, skill_id, skill), rows in sorted(grouped.items()):
        effects: list[str] = []
        for kind in dict.fromkeys(row["effect_type"] for row in rows):
            values = sorted({row["value"] for row in rows if row["effect_type"] == kind})
            value_text = "/".join(f"{value:g}%" for value in values) if values else "-"
            effects.append(f"{kind}: {value_text}")
        summary_rows.append(
            {
                "job": job,
                "skill_id": skill_id,
                "skill": skill,
                "option_count": len({row["effect_type"] for row in rows}),
                "grade_count": len(rows),
                "effects": u(r"\uff1b").join(effects),
            }
        )
    return summary_rows, detail


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows, detail_rows = build_rows()
    output = OUT_DIR / "skill_glyphs_extracted.json"
    output.write_text(
        json.dumps({"summary": summary_rows, "detail": detail_rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(output)
    print(f"summary={len(summary_rows)} detail={len(detail_rows)}")


if __name__ == "__main__":
    main()
