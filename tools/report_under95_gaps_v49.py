from __future__ import annotations

import argparse
import gc
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TARGET_COVERAGE = 0.95
SCHEMA_VERSION = "under95-gaps-v49"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def named_gap_to_target(
    deterministic_hits: int,
    named_inferred_hits: int,
    target: float = TARGET_COVERAGE,
) -> int:
    """Return inferred Hits that must become deterministic to reach target.

    The formal report's named coverage denominator is deterministic plus named
    inferred Hits. Unknown Hits are deliberately outside this denominator.
    """

    named_population = int(deterministic_hits) + int(named_inferred_hits)
    required = math.ceil(target * named_population - 1e-12)
    return max(0, required - int(deterministic_hits))


def _expected_by_job(acceptance: dict[str, Any]) -> dict[str, dict[str, int]]:
    expected: dict[str, dict[str, int]] = {}
    for row in acceptance.get("jobs", []):
        after = row["after"]
        expected[str(row["job"])] = {
            "total_direct_hits": int(after["total_direct_hits"]),
            "deterministic_hits": int(after["deterministic_hits"]),
            "nondeterministic_hits": int(after["named_inferred_hits"]),
            "unattributed_hits": int(after["unknown_hits"]),
        }
    return expected


def _aggregate_summaries(
    headers: Iterable[dict[str, Any]],
) -> dict[str, Counter[str]]:
    aggregate: dict[str, Counter[str]] = defaultdict(Counter)
    for header in headers:
        for job, summary in header.get("profession_summary", {}).items():
            for key in (
                "total_direct_hits",
                "deterministic_hits",
                "nondeterministic_hits",
                "unattributed_hits",
            ):
                aggregate[str(job)][key] += int(summary.get(key, 0) or 0)
    return aggregate


def _summaries_match(
    headers: Iterable[dict[str, Any]], expected: dict[str, dict[str, int]]
) -> bool:
    aggregate = _aggregate_summaries(headers)
    for job, target in expected.items():
        for key, value in target.items():
            if int(aggregate.get(job, {}).get(key, 0)) != int(value):
                return False
    extra_jobs = set(aggregate) - set(expected)
    return not any(sum(aggregate[job].values()) for job in extra_jobs)


def reconcile_fixed_results(
    acceptance: dict[str, Any], headers: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select the exact fixed corpus by reconciling all per-job counters.

    The directory can contain fresh controls in addition to the formal fixed
    corpus. No capture is selected by filename; selection must exactly reproduce
    every job's four counters from acceptance.json.
    """

    fixed_count = int(acceptance["fixed_coverage_captures"])
    expected = _expected_by_job(acceptance)
    exclude_count = len(headers) - fixed_count
    if exclude_count < 0:
        raise ValueError(
            f"after_results only has {len(headers)} entries; expected {fixed_count}"
        )
    if exclude_count > 3:
        raise ValueError(
            "refusing an ambiguous reconciliation requiring more than 3 exclusions"
        )

    solutions: list[tuple[int, ...]] = []
    for excluded_indices in itertools.combinations(range(len(headers)), exclude_count):
        excluded_set = set(excluded_indices)
        selected = [row for index, row in enumerate(headers) if index not in excluded_set]
        if _summaries_match(selected, expected):
            solutions.append(excluded_indices)
    if len(solutions) != 1:
        raise ValueError(
            f"fixed-corpus reconciliation is not unique: {len(solutions)} solutions"
        )

    excluded_set = set(solutions[0])
    selected = [row for index, row in enumerate(headers) if index not in excluded_set]
    excluded = [row for index, row in enumerate(headers) if index in excluded_set]
    return selected, excluded


def evidence_family(method: str, template: str) -> str:
    method = str(method or "")
    template = str(template or "")
    if method == "summon_owner":
        return "summon_entity_owner"
    if method == "skill_object_owner" or template.startswith("实体模板:"):
        return "entity_template_owner"
    if "projectile" in method:
        return "projectile_object_chain"
    if template == "<对象存在但模板未解析>":
        return "object_template_unresolved"
    if method == "unresolved":
        return "unresolved_damage_object"
    if method in {
        "latest_profiled_direct_cast",
        "learned_cast_profile",
        "cast_instance_match",
        "continuation_window",
        "embedded_single_candidate",
    }:
        return "cast_timing_inference"
    return "other_existing_evidence"


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"value": key, "hits": int(hits)}
        for key, hits in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _bucket() -> dict[str, Any]:
    return {
        "hits": 0,
        "damage": 0,
        "methods": Counter(),
        "templates": Counter(),
        "captures": Counter(),
        "evidence_families": Counter(),
    }


def _add_detail(bucket: dict[str, Any], detail: dict[str, Any], capture: str) -> None:
    hits = int(detail.get("hits", 0) or 0)
    damage = int(detail.get("damage", 0) or 0)
    method = str(detail.get("method", "") or "")
    template = str(detail.get("projectile_template_id", "") or "")
    bucket["hits"] += hits
    bucket["damage"] += damage
    bucket["methods"][method] += hits
    bucket["templates"][template or "<空>"] += hits
    bucket["captures"][str(capture)] += hits
    bucket["evidence_families"][evidence_family(method, template)] += hits


def _finalize_bucket(
    identity: tuple[str, str] | None, bucket: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "hits": int(bucket["hits"]),
        "damage": int(bucket["damage"]),
        "methods": _counter_rows(bucket["methods"]),
        "object_templates": _counter_rows(bucket["templates"]),
        "captures": _counter_rows(bucket["captures"]),
        "evidence_families": _counter_rows(bucket["evidence_families"]),
    }
    if identity is not None:
        result["skill_id"], result["skill_name"] = identity
    return result


def _verification_route(bucket: dict[str, Any]) -> str:
    families = {row["value"]: int(row["hits"]) for row in bucket["evidence_families"]}
    if families.get("summon_entity_owner", 0):
        return "召唤实体→实体动作/攻击模板→damage_object_id→内置统计技能槽"
    if families.get("entity_template_owner", 0):
        return "技能/实体生成→实体模板→damage_object_id→内置统计技能槽"
    if families.get("projectile_object_chain", 0):
        return "技能动作→Projectile模板→damage_object_id→内置统计技能槽"
    if families.get("object_template_unresolved", 0):
        return "技能动作→对象工厂/模板注册→damage_object_id→内置统计技能槽"
    return "技能动作/响应→直接攻击结果→内置统计技能槽"


def _top_values(rows: list[dict[str, Any]], limit: int = 3) -> str:
    return "；".join(f"{row['value']}({row['hits']})" for row in rows[:limit]) or "—"


def build_report(
    acceptance_path: Path, after_results_dir: Path
) -> dict[str, Any]:
    acceptance = _read_json(acceptance_path)
    jobs = acceptance.get("jobs", [])
    if len(jobs) != 30:
        raise ValueError(f"formal acceptance must contain 30 jobs, got {len(jobs)}")

    result_paths = sorted(after_results_dir.glob("*.json"))
    headers: list[dict[str, Any]] = []
    for path in result_paths:
        payload = _read_json(path)
        headers.append(
            {
                "path": path,
                "result_sha256": _sha256(path),
                "capture_sha256": str(payload.get("capture_sha256", "")),
                "capture": str(payload.get("capture", path.stem)),
                "profession_summary": payload.get("profession_summary", {}),
            }
        )
        del payload
        gc.collect()

    selected, excluded = reconcile_fixed_results(acceptance, headers)
    selected_stems = {row["path"].stem for row in selected}
    under_rows = [row for row in jobs if not bool(row.get("named_target_met"))]
    if len(under_rows) != 11:
        raise ValueError(f"formal acceptance must have 11 under-95 jobs, got {len(under_rows)}")
    under_jobs = {str(row["job"]) for row in under_rows}

    inferred: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    unknown: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    overall: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: {
            "captures": Counter(),
            "templates": Counter(),
            "evidence_families": Counter(),
            "methods": Counter(),
        }
    )
    detail_class_totals: dict[str, Counter[str]] = defaultdict(Counter)

    for path in result_paths:
        if path.stem not in selected_stems:
            continue
        payload = _read_json(path)
        fallback_capture = str(payload.get("capture", path.stem))
        for detail in payload.get("details", []):
            job = str(detail.get("job", ""))
            classification = str(detail.get("classification", ""))
            if job not in under_jobs or classification not in {
                "nondeterministic",
                "unattributed",
            }:
                continue
            capture = str(detail.get("capture", fallback_capture))
            hits = int(detail.get("hits", 0) or 0)
            method = str(detail.get("method", "") or "")
            template = str(detail.get("projectile_template_id", "") or "")
            detail_class_totals[job][classification] += hits
            overall[job]["captures"][capture] += hits
            overall[job]["templates"][template or "<空>"] += hits
            overall[job]["evidence_families"][evidence_family(method, template)] += hits
            overall[job]["methods"][method] += hits

            if classification == "nondeterministic":
                identity = (
                    str(detail.get("skill_id", "")),
                    str(detail.get("skill_name", "")),
                )
                bucket = inferred[job].setdefault(identity, _bucket())
            else:
                identity = (method, template or "<空>")
                bucket = unknown[job].setdefault(identity, _bucket())
            _add_detail(bucket, detail, capture)
        del payload
        gc.collect()

    report_jobs: list[dict[str, Any]] = []
    selected_skill_rows: list[dict[str, Any]] = []
    for acceptance_row in under_rows:
        job = str(acceptance_row["job"])
        after = acceptance_row["after"]
        deterministic_hits = int(after["deterministic_hits"])
        inferred_hits = int(after["named_inferred_hits"])
        unknown_hits = int(after["unknown_hits"])
        gap = named_gap_to_target(deterministic_hits, inferred_hits)

        if detail_class_totals[job]["nondeterministic"] != inferred_hits:
            raise ValueError(
                f"{job} inferred detail mismatch: "
                f"{detail_class_totals[job]['nondeterministic']} != {inferred_hits}"
            )
        if detail_class_totals[job]["unattributed"] != unknown_hits:
            raise ValueError(
                f"{job} unknown detail mismatch: "
                f"{detail_class_totals[job]['unattributed']} != {unknown_hits}"
            )

        ranked_inferred = sorted(
            (
                _finalize_bucket(identity, bucket)
                for identity, bucket in inferred[job].items()
            ),
            key=lambda row: (-row["hits"], row["skill_id"], row["skill_name"]),
        )
        ranked_unknown = sorted(
            (
                {
                    "method": identity[0],
                    "object_template": identity[1],
                    **_finalize_bucket(None, bucket),
                }
                for identity, bucket in unknown[job].items()
            ),
            key=lambda row: (-row["hits"], row["method"], row["object_template"]),
        )

        cumulative = 0
        minimum_targets: list[dict[str, Any]] = []
        for skill in ranked_inferred:
            if cumulative >= gap:
                break
            remaining = gap - cumulative
            credit = min(int(skill["hits"]), remaining)
            cumulative += int(skill["hits"])
            selected_row = {
                **skill,
                "job": job,
                "target_credit_hits": credit,
                "cumulative_candidate_hits": cumulative,
                "verification_route": _verification_route(skill),
            }
            minimum_targets.append(selected_row)
            selected_skill_rows.append(selected_row)
        if cumulative < gap:
            raise ValueError(f"{job} cannot reach 95% from named inferred details")
        previous_sum = sum(row["hits"] for row in ranked_inferred[: max(0, len(minimum_targets) - 1)])

        report_jobs.append(
            {
                "job": job,
                "named_coverage_percent": float(after["named_coverage_percent"]),
                "all_coverage_percent": float(after["all_coverage_percent"]),
                "total_direct_hits": int(after["total_direct_hits"]),
                "deterministic_hits": deterministic_hits,
                "named_inferred_hits": inferred_hits,
                "unknown_hits": unknown_hits,
                "named_population_hits": deterministic_hits + inferred_hits,
                "hits_needed_for_95": gap,
                "minimum_skill_target_count": len(minimum_targets),
                "minimum_skill_candidate_hits": cumulative,
                "minimum_count_proof": {
                    "sum_of_top_k_minus_1": previous_sum,
                    "is_below_gap": previous_sum < gap,
                    "sum_of_top_k": cumulative,
                    "reaches_gap": cumulative >= gap,
                },
                "ranked_named_inferred_skills": ranked_inferred,
                "ranked_unknown_buckets": ranked_unknown,
                "minimum_client_chain_targets": minimum_targets,
                "top_captures": _counter_rows(overall[job]["captures"]),
                "top_object_templates": _counter_rows(overall[job]["templates"]),
                "evidence_families": _counter_rows(overall[job]["evidence_families"]),
                "methods": _counter_rows(overall[job]["methods"]),
            }
        )

    global_targets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in selected_skill_rows:
        identity = (str(row["skill_id"]), str(row["skill_name"]))
        target = global_targets.setdefault(
            identity,
            {
                "skill_id": identity[0],
                "skill_name": identity[1],
                "jobs": [],
                "candidate_hits": 0,
                "target_credit_hits": 0,
                "verification_routes": Counter(),
            },
        )
        target["jobs"].append(
            {
                "job": row["job"],
                "candidate_hits": int(row["hits"]),
                "target_credit_hits": int(row["target_credit_hits"]),
            }
        )
        target["candidate_hits"] += int(row["hits"])
        target["target_credit_hits"] += int(row["target_credit_hits"])
        target["verification_routes"][row["verification_route"]] += int(row["hits"])

    finalized_global: list[dict[str, Any]] = []
    for target in global_targets.values():
        routes = _counter_rows(target.pop("verification_routes"))
        target["verification_routes"] = routes
        target["jobs"].sort(key=lambda row: row["job"])
        finalized_global.append(target)
    finalized_global.sort(
        key=lambda row: (
            -len(row["jobs"]),
            -row["target_credit_hits"],
            -row["candidate_hits"],
            row["skill_id"],
        )
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "target_named_coverage_percent": TARGET_COVERAGE * 100.0,
        "coverage_definition": {
            "named_coverage": "deterministic_hits / (deterministic_hits + named_inferred_hits)",
            "hits_needed_for_95": "ceil(0.95 * (deterministic_hits + named_inferred_hits)) - deterministic_hits",
            "unknown_policy": "unknown_hits are reported separately and never enter the named-coverage denominator",
        },
        "sources": {
            "acceptance": str(acceptance_path),
            "acceptance_sha256": _sha256(acceptance_path),
            "after_results_dir": str(after_results_dir),
            "after_result_count": len(headers),
            "fixed_result_count": len(selected),
            "selected_results": [
                {
                    "result": row["path"].name,
                    "capture": row["capture"],
                    "capture_sha256": row["capture_sha256"],
                    "result_sha256": row["result_sha256"],
                }
                for row in selected
            ],
            "excluded_non_fixed_results": [
                {
                    "result": row["path"].name,
                    "capture": row["capture"],
                    "capture_sha256": row["capture_sha256"],
                    "result_sha256": row["result_sha256"],
                    "reason": "excluded by unique exact reconciliation with the formal 35-capture acceptance counters",
                }
                for row in excluded
            ],
        },
        "summary": {
            "formal_jobs": len(jobs),
            "jobs_at_or_above_95": len(jobs) - len(under_rows),
            "jobs_under_95": len(under_rows),
            "under_95_jobs": [row["job"] for row in report_jobs],
            "per_job_minimum_target_rows": len(selected_skill_rows),
            "globally_merged_unique_skill_targets": len(finalized_global),
            "note": "counts are never pooled to decide whether an individual job passes",
        },
        "jobs": report_jobs,
        "minimum_client_builtin_stats_chain_set": finalized_global,
        "limitations": [
            "The minimum set is an optimistic Hit-volume set: every selected skill still requires a deterministic client-statistics chain witness before any Hit can be promoted.",
            "A matching timestamp, plausible cooldown, known skill name, object existence, or summon ownership alone is not a deterministic skill certificate.",
            "Unknown Hits are retained as a separate risk list and are not used to make the named-coverage target easier.",
            "This report only reads frozen acceptance/after_results; it does not rescore captures or change production code.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    sources = report["sources"]
    lines = [
        "# 35包正式覆盖率：11个未达95%职业缺口（v49）",
        "",
        "## 结论",
        "",
        f"正式口径共 **{summary['formal_jobs']}** 个二转职业：**{summary['jobs_at_or_above_95']}** 个已达95%，**{summary['jobs_under_95']}** 个未达标。本文所有达标判断均逐职业进行，严禁用总体覆盖率替代。",
        "",
        "命名覆盖率口径为 `确定性 Hit ÷（确定性 Hit + named inferred Hit）`。unknown Hit 单列，不进入该分母，也不会被用来降低95%门槛。",
        "",
        f"`after_results` 有 {sources['after_result_count']} 份结果；通过与正式验收30职业四项计数逐项唯一对齐，选出 {sources['fixed_result_count']} 份固定语料。",
        "",
    ]
    excluded = sources["excluded_non_fixed_results"]
    if excluded:
        lines.append(
            "额外结果未混入正式口径："
            + "、".join(f"`{row['capture']}`" for row in excluded)
            + "。"
        )
        lines.append("")

    lines.extend(
        [
            "## 逐职业差距",
            "",
            "| 职业 | 命名覆盖率 | 确定性 Hit | named inferred | unknown | 距95% | 乐观最少技能数 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for job in report["jobs"]:
        lines.append(
            f"| {job['job']} | {job['named_coverage_percent']:.4f}% | "
            f"{job['deterministic_hits']} | {job['named_inferred_hits']} | "
            f"{job['unknown_hits']} | **{job['hits_needed_for_95']}** | "
            f"{job['minimum_skill_target_count']} |"
        )

    lines.extend(
        [
            "",
            "“乐观最少技能数”表示：若所选技能的全部 named inferred Hit 都能被客户端内置统计链升级为确定性证据，达到95%至少要验证多少个技能；它不是已确认提升。",
            "",
            "## 各职业具体缺口",
            "",
        ]
    )

    for job in report["jobs"]:
        lines.extend(
            [
                f"### {job['job']}",
                "",
                f"当前 {job['named_coverage_percent']:.4f}%，距离95%还差 **{job['hits_needed_for_95']} Hit**。主要抓包：{_top_values(job['top_captures'], 5)}。",
                "",
                f"主要证据模式：{_top_values(job['evidence_families'], 5)}。主要对象模板：{_top_values(job['top_object_templates'], 5)}。",
                "",
                "named inferred 排名：",
                "",
                "| 排名 | 技能 | Hit | 主要方法 | 主要对象模板 | 主要抓包 |",
                "|---:|---|---:|---|---|---|",
            ]
        )
        for index, skill in enumerate(job["ranked_named_inferred_skills"][:8], 1):
            lines.append(
                f"| {index} | {skill['skill_name']} (`{skill['skill_id']}`) | {skill['hits']} | "
                f"{_top_values(skill['methods'], 3)} | {_top_values(skill['object_templates'], 3)} | "
                f"{_top_values(skill['captures'], 3)} |"
            )

        lines.extend(["", "unknown 排名：", "", "| 排名 | Hit | 方法 | 对象模板 | 主要抓包 |", "|---:|---:|---|---|---|"])
        if job["ranked_unknown_buckets"]:
            for index, bucket in enumerate(job["ranked_unknown_buckets"][:5], 1):
                lines.append(
                    f"| {index} | {bucket['hits']} | {bucket['method'] or '<空>'} | "
                    f"{bucket['object_template']} | {_top_values(bucket['captures'], 3)} |"
                )
        else:
            lines.append("| 1 | 0 | — | — | — |")

        target_text = "、".join(
            f"{row['skill_name']}({row['hits']} Hit)"
            for row in job["minimum_client_chain_targets"]
        )
        lines.extend(
            [
                "",
                f"优先验证的最少技能集合：{target_text}。",
                "",
            ]
        )

    lines.extend(
        [
            "## 客户端内置统计链优先验证的合并最小集合",
            "",
            f"逐职业共有 {summary['per_job_minimum_target_rows']} 个选择项；合并相同技能后为 **{summary['globally_merged_unique_skill_targets']} 个唯一技能目标**。",
            "",
            "| 技能 | 涉及职业 | 候选 Hit | 达标贡献上限 | 首选验证链 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for target in report["minimum_client_builtin_stats_chain_set"]:
        jobs = "、".join(row["job"] for row in target["jobs"])
        route = target["verification_routes"][0]["value"] if target["verification_routes"] else "—"
        lines.append(
            f"| {target['skill_name']} (`{target['skill_id']}`) | {jobs} | "
            f"{target['candidate_hits']} | {target['target_credit_hits']} | {route} |"
        )

    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 以上最小集合只按现有 named inferred Hit 数量确定，必须在客户端内置统计链中取得“技能槽/技能ID→对象或实体→damage_object_id→UDP伤害”的闭环后，才能升级为确定性 Hit。",
            "- 时间接近、CD合理、已有技能名、对象存在或召唤归属，只能用于排序，不能直接作为确定性证书。",
            "- unknown Hit 始终保留在各职业小节中；即使命名覆盖率达到95%，它们仍是独立的漏识别风险。",
            "- 本报告只读取冻结输出，没有重新评分、没有修改生产代码。",
            "",
            "## 输入指纹",
            "",
            f"- `acceptance.json`: `{sources['acceptance_sha256']}`",
            f"- 固定结果数：{sources['fixed_result_count']}；额外结果数：{len(excluded)}。逐文件哈希见同目录 JSON。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--acceptance",
        type=Path,
        default=Path("outputs/magma_runtime_v37_20260908/acceptance.json"),
    )
    parser.add_argument(
        "--after-results",
        type=Path,
        default=Path("outputs/magma_runtime_v37_20260908/after_results"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/native_wire_v49_20260909"),
    )
    args = parser.parse_args()

    report = build_report(args.acceptance, args.after_results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "under95_gaps.json"
    md_path = args.output_dir / "under95_gaps.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(md_path),
                "jobs_under_95": report["summary"]["jobs_under_95"],
                "unique_targets": report["summary"][
                    "globally_merged_unique_skill_targets"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
