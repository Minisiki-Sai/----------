from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


UNKNOWN_STATUS = "保留未归因"
PERIODIC_TYPE = "Debuff周期伤害"


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize unresolved non-periodic extra damage by job and hint."
    )
    parser.add_argument("audits", nargs="+", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()

    audits = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.audits
    ]
    details = [
        row for audit in audits for row in audit.get("details", ())
    ]
    captures = [
        row for audit in audits for row in audit.get("captures", ())
    ]
    non_periodic = [
        row for row in details if str(row.get("effectType", "")) != PERIODIC_TYPE
    ]

    totals: dict[str, dict[str, object]] = {}
    for row in details:
        job = str(row.get("job", "") or "未知职业")
        totals.setdefault(
            job,
            {
                "job": job,
                "captures": set(),
                "totalHits": 0,
                "totalDamage": 0,
                "unknownHits": 0,
                "unknownDamage": 0,
                "hints": defaultdict(lambda: [0, 0]),
            },
        )["captures"].add(str(row.get("capture", "")))
    observed_jobs = set(totals)
    if args.coverage:
        with args.coverage.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                job = str(row.get("job", "") or "").strip()
                if not job:
                    continue
                totals.setdefault(
                    job,
                    {
                        "job": job,
                        "captures": set(),
                        "totalHits": 0,
                        "totalDamage": 0,
                        "unknownHits": 0,
                        "unknownDamage": 0,
                        "hints": defaultdict(lambda: [0, 0]),
                    },
                )
    for row in non_periodic:
        job = str(row.get("job", "") or "未知职业")
        item = totals[job]
        item["captures"].add(str(row.get("capture", "")))
        item["totalHits"] += 1
        item["totalDamage"] += _integer(row.get("extraDamage"))
        if str(row.get("auditStatus", "")) != UNKNOWN_STATUS:
            continue
        item["unknownHits"] += 1
        item["unknownDamage"] += _integer(row.get("extraDamage"))
        hint = str(row.get("currentSkillName", "") or "无技能提示")
        item["hints"][hint][0] += 1
        item["hints"][hint][1] += _integer(row.get("extraDamage"))

    job_rows: list[dict[str, object]] = []
    for item in totals.values():
        top_hints = sorted(
            item["hints"].items(), key=lambda pair: (-pair[1][1], -pair[1][0], pair[0])
        )[:4]
        total_damage = int(item["totalDamage"])
        total_hits = int(item["totalHits"])
        unknown_damage = int(item["unknownDamage"])
        unknown_hits = int(item["unknownHits"])
        job_rows.append(
            {
                "job": item["job"],
                "captureCount": len(item["captures"]),
                "totalExtraHits": total_hits,
                "totalExtraDamage": total_damage,
                "unknownHits": unknown_hits,
                "unknownDamage": unknown_damage,
                "unknownHitPct": round(100 * unknown_hits / total_hits, 3)
                if total_hits
                else 0,
                "unknownDamagePct": round(100 * unknown_damage / total_damage, 3)
                if total_damage
                else 0,
                "topCurrentSkillHints": "；".join(
                    f"{name}:{values[0]}Hit/{values[1]}伤害"
                    for name, values in top_hints
                ) or ("无实战样本" if item["job"] not in observed_jobs else ""),
            }
        )
    job_rows.sort(key=lambda row: (-int(row["unknownDamage"]), str(row["job"])))

    grouped: dict[tuple[str, str, str, str], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    for row in non_periodic:
        if str(row.get("auditStatus", "")) != UNKNOWN_STATUS:
            continue
        key = (
            str(row.get("capture", "")),
            str(row.get("job", "") or "未知职业"),
            str(row.get("currentSkillName", "") or "无技能提示"),
            str(row.get("currentMethod", "") or "无现有方法"),
        )
        grouped[key][0] += 1
        grouped[key][1] += _integer(row.get("extraDamage"))
    detail_rows = [
        {
            "capture": key[0],
            "job": key[1],
            "currentSkillHint": key[2],
            "currentMethod": key[3],
            "unknownHits": values[0],
            "unknownDamage": values[1],
        }
        for key, values in grouped.items()
    ]
    detail_rows.sort(
        key=lambda row: (
            -int(row["unknownDamage"]),
            str(row["job"]),
            str(row["capture"]),
        )
    )

    prefix = args.output_prefix
    jobs_path = prefix.with_name(prefix.name + "_by_job.csv")
    details_path = prefix.with_name(prefix.name + "_by_hint.csv")
    markdown_path = prefix.with_suffix(".md")
    _write_csv(jobs_path, job_rows)
    _write_csv(details_path, detail_rows)

    lines = [
        "# 额外伤害未归因审计",
        "",
        f"- 抓包数：{len(captures)}",
        f"- 静态覆盖职业数：{len(job_rows)}",
        f"- 有实战样本职业数：{len(observed_jobs)}",
        "- 口径：只统计非周期状态伤害；Debuff周期伤害不参与归因率。",
        "",
        "| 职业 | 样本场数 | 未归因Hit | 未归因伤害 | 伤害占比 | 主要现有技能提示 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in job_rows:
        lines.append(
            f"| {row['job']} | {row['captureCount']} | {row['unknownHits']} | "
            f"{int(row['unknownDamage']):,} | {row['unknownDamagePct']:.3f}% | "
            f"{row['topCurrentSkillHints'] or '—'} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "jobs": str(jobs_path),
                "hints": str(details_path),
                "markdown": str(markdown_path),
                "coveredJobs": len(job_rows),
                "observedJobs": len(observed_jobs),
                "unknownJobs": sum(1 for row in job_rows if row["unknownHits"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
