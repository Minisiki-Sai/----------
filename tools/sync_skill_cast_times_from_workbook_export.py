from __future__ import annotations

import argparse
import copy
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any


SHEET_NAME = "技能主表"
JOB_ALIASES = {
    "炼金": "炼金圣士",
    "药剂": "药剂师",
}


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"[\s·・]", "", text).casefold()


def as_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return None


def parse_job_specific_times(value: str) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for job, seconds in re.findall(
        r"([^\d，,、]+?)(\d+(?:\.\d+)?)\s*s?", value, flags=re.IGNORECASE
    ):
        job = re.sub(r"(?:的是|为|是)$", "", job.strip())
        job = JOB_ALIASES.get(job, job)
        if job:
            result.append((job, round(float(seconds), 6)))
    return result


def preferred_template(rows: list[dict[str, Any]], job: str, skill: str) -> dict[str, Any]:
    skill_key = norm(skill)
    exact = next(
        (
            row
            for row in rows
            if norm(row.get("skill")) == skill_key and norm(row.get("job")) == norm(job)
        ),
        None,
    )
    if exact is not None:
        return exact
    generic = next((row for row in rows if norm(row.get("skill")) == skill_key), None)
    return generic or {
        "job": job,
        "stage": "",
        "skill": skill,
        "cast_time": None,
        "quick_time": None,
        "speed_percent": 0.0,
        "corrected_cast_time": None,
        "corrected_quick_time": None,
        "fixed": False,
        "quick_fixed": False,
        "notes": "",
        "source_row": None,
    }


def sync(export_path: Path, table_path: Path, audit_path: Path | None) -> dict[str, Any]:
    export = json.loads(export_path.read_text(encoding="utf-8"))
    sheet_rows = export.get(SHEET_NAME, [])
    if len(sheet_rows) < 5:
        raise ValueError(f"{SHEET_NAME} 缺少数据")

    payload = json.loads(table_path.read_text(encoding="utf-8"))
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]

    # Older tables stored an unaccelerated base in cast_time and the workbook's
    # effective value in corrected_cast_time. The workbook value is now the
    # canonical cast time; speed_percent remains metadata for external speed buffs.
    migrated_speed_rows = 0
    for row in rows:
        if float(row.get("speed_percent") or 0.0) <= 0:
            continue
        corrected = as_seconds(row.get("corrected_cast_time"))
        corrected_quick = as_seconds(row.get("corrected_quick_time"))
        migrated = False
        if corrected is not None:
            row["cast_time"] = corrected
            migrated = True
        if corrected_quick is not None:
            row["quick_time"] = corrected_quick
            migrated = True
        row["corrected_cast_time"] = None
        row["corrected_quick_time"] = None
        if migrated:
            migrated_speed_rows += 1

    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        indexed.setdefault((norm(row.get("job")), norm(row.get("skill"))), []).append(row)

    updated: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    fixed_updates: list[dict[str, Any]] = []

    def apply(job: str, skill: str, seconds: float, source_row: int, *, fixed: bool = False) -> None:
        key = (norm(job), norm(skill))
        matches = indexed.get(key, [])
        if not matches:
            template = copy.deepcopy(preferred_template(rows, job, skill))
            template.update(
                {
                    "job": job,
                    "skill": skill,
                    "cast_time": seconds,
                    "quick_time": None,
                    "corrected_cast_time": None,
                    "corrected_quick_time": None,
                    "fixed": fixed,
                    "quick_fixed": False,
                    "source_row": source_row,
                }
            )
            note = str(template.get("notes") or "").strip()
            template["notes"] = "；".join(
                part for part in (note, "按二转职业覆盖工作簿施放时间") if part
            )
            rows.append(template)
            indexed.setdefault(key, []).append(template)
            added.append({"job": job, "skill": skill, "cast_time": seconds})
            return
        for row in matches:
            before = as_seconds(row.get("cast_time"))
            row["cast_time"] = seconds
            row["corrected_cast_time"] = None
            row["source_row"] = source_row
            if fixed:
                row["fixed"] = True
            if before != seconds:
                updated.append(
                    {"job": job, "skill": skill, "before": before, "after": seconds}
                )

    for source_row, workbook_row in enumerate(sheet_rows[4:], start=5):
        if not isinstance(workbook_row, list) or len(workbook_row) < 6:
            continue
        job = str(workbook_row[2] or "").strip()
        skill = str(workbook_row[4] or "").strip()
        cast_value = workbook_row[5]
        if not job or not skill or cast_value in (None, ""):
            continue

        numeric = as_seconds(cast_value)
        if numeric is not None:
            key = (norm(job), norm(skill))
            if key not in indexed:
                unmatched.append(
                    {"source_row": source_row, "job": job, "skill": skill, "cast_time": numeric}
                )
                continue
            apply(job, skill, numeric, source_row)
            continue

        text = str(cast_value).strip()
        if "固定" in text:
            match = re.search(r"\d+(?:\.\d+)?", text)
            if match:
                seconds = round(float(match.group()), 6)
                apply(job, skill, seconds, source_row, fixed=True)
                fixed_updates.append({"job": job, "skill": skill, "cast_time": seconds})
            continue

        specific = parse_job_specific_times(text)
        if not specific:
            unmatched.append(
                {"source_row": source_row, "job": job, "skill": skill, "cast_time": text}
            )
            continue
        for second_job, seconds in specific:
            apply(second_job, skill, seconds, source_row)

    payload["source"] = r"E:\新建文件夹\outputs\full_skill_list_20260806\龙之谷V3.50全职业技能列表.xlsx"
    payload["description"] = (
        "技能施放时间表。cast_time/quick_time 直接保存工作簿中的实际有效秒数；"
        "speed_percent 保留技能自身动作加速，用于与精神爆发等外部动作速度按组合公式换算。"
    )
    payload["generated_at"] = date.today().isoformat()
    payload["rows"] = rows
    table_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    audit = {
        "updated_count": len(updated),
        "added_count": len(added),
        "migrated_speed_rows": migrated_speed_rows,
        "fixed_updates": fixed_updates,
        "updated": updated,
        "added": added,
        "unmatched": unmatched,
    }
    if audit_path is not None:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path, help="artifact-tool 导出的 full_skill_list_values.json")
    parser.add_argument("table", type=Path, help="模拟器 skill_cast_times.json")
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    audit = sync(args.export, args.table, args.audit)
    print(json.dumps({key: value for key, value in audit.items() if key.endswith("count") or key == "migrated_speed_rows"}, ensure_ascii=False))
    if audit["unmatched"]:
        print("未匹配：", json.dumps(audit["unmatched"], ensure_ascii=False))


if __name__ == "__main__":
    main()
