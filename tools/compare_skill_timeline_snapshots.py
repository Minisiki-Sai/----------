from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CASES = (("long", "长时间实战"), ("burst", "精神爆发"), ("accel", "时间加速"))


def _skills(case: dict[str, Any]) -> dict[str, tuple[int, float]]:
    return {
        str(row.get("skill") or ""): (int(row.get("casts") or 0), float(row.get("score") or 0.0))
        for row in case.get("skills", [])
    }


def _opening(case: dict[str, Any]) -> list[tuple[float, str]]:
    return [
        (round(float(row.get("start") or 0.0), 4), str(row.get("skill") or ""))
        for row in case.get("opening", [])
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two all-job timeline snapshots.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    before_jobs = {row["second_job"]: row for row in before.get("rows", [])}
    after_jobs = {row["second_job"]: row for row in after.get("rows", [])}
    rows: list[dict[str, Any]] = []
    for job in sorted(set(before_jobs) | set(after_jobs)):
        row: dict[str, Any] = {"job": job, "cases": {}}
        for key, _label in CASES:
            old = before_jobs.get(job, {}).get("cases", {}).get(key, {})
            new = after_jobs.get(job, {}).get("cases", {}).get(key, {})
            old_score = float(old.get("score") or 0.0)
            new_score = float(new.get("score") or 0.0)
            delta = new_score - old_score
            percent = delta / old_score * 100.0 if old_score else 0.0
            skills_equal = _skills(old) == _skills(new)
            opening_equal = _opening(old) == _opening(new)
            segments_equal = int(old.get("segments") or 0) == int(new.get("segments") or 0)
            equal = abs(delta) <= 1e-6 and skills_equal and opening_equal and segments_equal
            row["cases"][key] = {
                "before": old_score,
                "after": new_score,
                "delta": delta,
                "percent": percent,
                "beforeSegments": int(old.get("segments") or 0),
                "afterSegments": int(new.get("segments") or 0),
                "skillsEqual": skills_equal,
                "openingEqual": opening_equal,
                "equal": equal,
                "direction": "不变" if equal else ("提高" if delta > 0 else "下降"),
            }
        rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "before": str(args.before),
        "after": str(args.after),
        "jobs": len(rows),
        "allEqual": all(case["equal"] for row in rows for case in row["cases"].values()),
        "rows": rows,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = [
        "# V4.01 / V4.10 全职业技能模拟对比",
        "",
        f"- 职业数：{len(rows)}",
        f"- 结论：{'全部一致' if payload['allEqual'] else '存在差异'}",
        "- 口径：同一默认配装、默认加点、自动技能纹章，125 秒长时间轴；短轴分别测试精神爆发和时间加速。",
        "",
        "| 职业 | 长时间实战 | 精神爆发 | 时间加速 |",
        "|---|---:|---:|---:|",
    ]
    for row in rows:
        cells = []
        for key, _label in CASES:
            case = row["cases"][key]
            cells.append("0.000%（不变）" if case["equal"] else f"{case['percent']:+.3f}%（{case['direction']}）")
        markdown.append(f"| {row['job']} | {' | '.join(cells)} |")
    markdown.extend(["", "## 校验范围", "", "每个场景同时比较总输出、技能段数量、逐技能施放次数与输出、前 12 个技能的顺序和起始时刻。"])
    args.output.with_suffix(".md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
