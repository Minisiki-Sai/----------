from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _percentages(skill: dict[str, Any]) -> list[float]:
    return [
        round(100.0 * float(row.get("share", 0.0) or 0.0), 4)
        for row in skill.get("positions", ())
        if isinstance(row, dict)
    ]


def _compact_percentages(values: Iterable[float]) -> str:
    rows = [round(float(value), 1) for value in values]
    if not rows:
        return "—"
    parts: list[str] = []
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and rows[end] == rows[start]:
            end += 1
        if end - start >= 3:
            parts.append(f"H{start + 1}–H{end}: 各{rows[start]:.1f}%")
        else:
            parts.extend(
                f"H{index + 1}: {rows[index]:.1f}%"
                for index in range(start, end)
            )
        start = end
    return "；".join(parts)


def _level(
    skill: dict[str, Any],
    review_keys: set[str],
    quarantine_keys: set[str],
) -> str:
    key = str(skill.get("key", ""))
    if key in review_keys:
        return "结论候选"
    if bool(skill.get("persistent", False)):
        return "持续/召唤（无固定Hit）"
    if key in quarantine_keys:
        return "待复核"
    return "仅发现/样本不足"


def build(report_path: Path, output_dir: Path) -> tuple[Path, Path]:
    report = _load(report_path)
    overview = report.get("overview", {})
    quality = report.get("quality_gates", {})
    review_keys = set(quality.get("review_candidates", ()) or ())
    quarantine_keys = set(quality.get("quarantine", ()) or ())

    coverage_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report.get("coverage", ()):
        if isinstance(row, dict):
            coverage_by_job[str(row.get("job", "未知职业"))].append(row)

    configured_jobs = set(coverage_by_job)
    skills_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    simplified_skills: list[dict[str, Any]] = []
    for skill in report.get("skills", ()):
        if not isinstance(skill, dict):
            continue
        job = str(skill.get("job", "未知职业"))
        if job not in configured_jobs:
            continue
        skills_by_job[job].append(skill)
        percentages = _percentages(skill)
        simplified_skills.append(
            {
                "job": job,
                "skill_name": skill.get("skill_name", ""),
                "skill_id": skill.get("skill_id", ""),
                "result_level": _level(skill, review_keys, quarantine_keys),
                "fixed_hit": not bool(skill.get("persistent", False)),
                "recommended_full_hit_count": (
                    int(skill.get("hit_count", 0) or 0)
                    if not bool(skill.get("persistent", False))
                    else None
                ),
                "per_hit_damage_percent": percentages,
                "full_hit_sample_count": int(
                    skill.get("full_hit_sample_count", 0) or 0
                ),
                "full_hit_file_count": int(
                    skill.get("full_hit_file_count", 0) or 0
                ),
                "full_hit_actor_count": int(
                    skill.get("full_hit_actor_count", 0) or 0
                ),
                "training_sample_count": int(
                    skill.get("training_sample_count", 0) or 0
                ),
                "confidence_evidence_score": round(
                    float(skill.get("confidence", 0.0) or 0.0), 2
                ),
                "status": skill.get("status", ""),
                "hit_count_selection": skill.get("hit_count_selection", ""),
            }
        )

    missing_by_job: dict[str, list[str]] = defaultdict(list)
    for job, rows in coverage_by_job.items():
        for row in rows:
            if not bool(row.get("has_same_variant_real_hit", False)):
                missing_by_job[job].append(str(row.get("skill_name", "")))

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "全职业技能Hit与伤害分布_V5.json"
    json_payload = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_report": str(report_path.resolve()),
        "overview": overview,
        "method": {
            "unit": "单次施放实例 × 单一Boss目标",
            "hit_count": "跨抓包/角色重复出现的最大稳定完整Hit；较少Hit按未打满处理，孤立更大值不采纳",
            "damage_share": "只取达到稳定完整Hit数的样本，逐Hit校正暴击后，单次施放归一化为100%，再按抓包平衡求中位数",
            "excluded": "额外伤害、DOT、召唤物伤害、辅助脉冲、结果组继承与仅靠时间邻近的推断不训练固定Hit模板",
        },
        "skills": sorted(
            simplified_skills,
            key=lambda row: (str(row["job"]), str(row["skill_name"])),
        ),
        "configured_skills_without_same_variant_real_hit": dict(
            sorted(missing_by_job.items())
        ),
    }
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    all_jobs = sorted(configured_jobs)
    lines = [
        "# 全职业技能 Hit 数与伤害分布（V5 严格证据）",
        "",
        "## 原因/统计口径",
        "",
        "- 统计单位是“单次施放实例 × 同一个 Boss”，不会把群攻命中多个目标误算成技能 Hit 数。",
        "- Hit 数取跨抓包、跨角色重复支持的最大稳定完整上界；少于该数视为未打满，孤立更大的样本视为串轴或边界异常。",
        "- 每 Hit 占比只使用达到完整 Hit 数的样本；先校正暴击，再把该次施放总伤害归一化为 100%，最后按抓包平衡取中位数。",
        "- 只接受真实 Hit 与确定性技能归属。额外伤害、DOT、召唤物、辅助脉冲和纯时间邻近推断不会混入直接技能分布。",
        "- “结论候选”已通过样本数、抓包数、角色数、一致性和单目标门槛；“待复核/样本不足”只展示观察结果，不能当最终配置。",
        "",
        "## 总览",
        "",
        f"- 抓包：{overview.get('captures_analyzed', 0)} 份（重复跳过 {overview.get('duplicate_captures_skipped', 0)} 份）",
        f"- 观察职业：{overview.get('professions_observed', 0)}；配置二转职业：{overview.get('configured_second_jobs', 0)}",
        f"- 技能变体：{overview.get('skill_variants_observed', 0)}；施放样本：{overview.get('cast_observations', 0)}",
        f"- 结论候选：{overview.get('review_candidate_skills', 0)}；待复核：{overview.get('quarantined_skills', 0)}；仅发现：{overview.get('discovery_only_skills', 0)}",
        "",
        "## 各职业结果",
    ]
    for job in all_jobs:
        lines.extend(["", f"### {job}", ""])
        fixed = [
            row
            for row in skills_by_job.get(job, ())
            if not bool(row.get("persistent", False))
            and int(row.get("hit_count", 0) or 0) > 0
        ]
        fixed.sort(
            key=lambda row: (
                0 if str(row.get("key", "")) in review_keys else 1,
                str(row.get("skill_name", "")),
            )
        )
        if fixed:
            lines.extend(
                [
                    "| 技能 | 结果级别 | 完整Hit | 每Hit伤害占比（合计100%） | 完整样本/抓包/角色 |",
                    "|---|---:|---:|---|---:|",
                ]
            )
            for skill in fixed:
                level = _level(skill, review_keys, quarantine_keys)
                counts = (
                    f"{int(skill.get('full_hit_sample_count', 0) or 0)}/"
                    f"{int(skill.get('full_hit_file_count', 0) or 0)}/"
                    f"{int(skill.get('full_hit_actor_count', 0) or 0)}"
                )
                lines.append(
                    "| {name} | {level} | {hits} | {shares} | {counts} |".format(
                        name=str(skill.get("skill_name", "")).replace("|", "\\|"),
                        level=level,
                        hits=int(skill.get("hit_count", 0) or 0),
                        shares=_compact_percentages(_percentages(skill)),
                        counts=counts,
                    )
                )
        else:
            lines.append("暂无可展示的固定 Hit 观察结果。")
        persistent = [
            str(row.get("skill_name", ""))
            for row in skills_by_job.get(job, ())
            if bool(row.get("persistent", False))
        ]
        if persistent:
            lines.extend(
                [
                    "",
                    "持续/召唤技能（不宣称固定 Hit 总数）："
                    + "、".join(sorted(set(persistent))),
                ]
            )
        missing = sorted(set(missing_by_job.get(job, ())))
        if missing:
            lines.extend(
                [
                    "",
                    "当前没有同变体真实 Hit 样本的配置技能："
                    + "、".join(missing),
                ]
            )

    md_path = output_dir / "全职业技能Hit与伤害分布_V5.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export concise all-profession Hit counts and per-Hit damage shares."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    md_path, json_path = build(args.report.resolve(), args.output.resolve())
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
