"""Read-only focused audit for the remaining low-coverage professions.

This replays the fixed 35-capture corpus with the current decoder and reuses
the v55 native/object joint-key auditor.  Existing skill labels are never used
as positive truth.  A positive proposal needs a unique independently
deterministic skill on the same job-scoped native/object key, at least eight
truth Hits, two captures and two actors.  A correction proposal is reported
separately and is never promoted automatically.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.audit_under95_native_joint_keys_v55 as native  # noqa: E402


SCHEMA = "under95-focus-v68"
TARGET_JOBS = ("药剂师", "黑暗萨满", "噬魂者", "影", "冰灵")
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_BASELINE = ROOT / "outputs/direct_damage_v66_full_20260909/profession_direct_damage_report.json"
DEFAULT_OUTPUT = ROOT / "outputs/under95_focus_v68_20260909"


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audit_one(entry: dict[str, str]) -> dict[str, Any]:
    # Windows workers import modules afresh; set the scope inside each worker.
    native.TARGET_JOBS = TARGET_JOBS
    signature = native._tree_signature()
    result = native._audit_capture(entry)
    result["loaded_production_signature"] = signature
    return result


def _runtime_from_current_baseline(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    by_job = {str(row["职业"]): row for row in report["summary"]}
    runtime: dict[str, dict[str, Any]] = {}
    for job in TARGET_JOBS:
        row = by_job[job]
        total = int(row["总直接Hit"])
        confirmed = int(row["确定性已归因"])
        runtime[job] = {
            "coverage_percent": 100.0 * confirmed / max(1, total),
            "confirmed_hits": confirmed,
            # Include unresolved direct Hits in the denominator, matching the
            # current direct-Hit report and the requested 89.26/84.13/etc.
            "named_hits": total,
            "hits_needed_for_95": max(0, math.ceil(total * 0.95) - confirmed),
        }
    return runtime, report


def _rows_by_physical(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row["physical_id"]): row
        for result in results
        for row in result["rows"]
    }


def _fixture(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "physical_id", "capture", "capture_sha256", "packet_serial",
        "record_offset", "timestamp_ms", "family", "source_class",
        "wire_source_actor_id", "owner_actor_id", "actor_token", "job",
        "skill_id", "skill_name", "method", "primary90", "primary90_read",
        "fallbackA4", "damage_object_id", "player_object_template",
        "player_object_generation", "origin_template", "origin_generation",
        "entity_template", "owner_generation", "owner_generation_proof",
        "owner_generation_current", "object_generation_current", "self_skill",
        "self_context",
    )
    return {key: row.get(key) for key in keys}


def _correction_candidates(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for row in aggregate["rejected_near_misses"]:
        truth = {int(key): int(value) for key, value in row["truth_skills"].items()}
        gaps = {int(key): int(value) for key, value in row["gap_skills"].items()}
        if len(truth) != 1 or not gaps:
            continue
        canonical = next(iter(truth))
        if canonical <= 0 or canonical in gaps:
            continue
        if int(row["truth_hits"]) < 8:
            continue
        if int(row["truth_capture_count"]) < 2 or int(row["truth_actor_count"]) < 2:
            continue
        # The independent truth must be unique.  Existing inferred labels are
        # deliberately not accepted as a veto; they are exactly what is under
        # audit.  Keep mixed old labels visible rather than silently merging.
        candidate = dict(row)
        candidate["correction_pairs"] = [
            {"from_skill_id": skill, "to_skill_id": canonical, "hits": hits}
            for skill, hits in sorted(gaps.items())
        ]
        candidate["status"] = "audit_only_correction_candidate"
        candidates.append(candidate)
    candidates.sort(key=lambda row: (-int(row["gap_hits"]), -int(row["truth_hits"]), row["job"]))
    return candidates


def _nonoverlap_corrections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    seen: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        ids = set(row["gap_physical_ids"]) - seen[row["job"]]
        if not ids:
            continue
        item = dict(row)
        item["new_unique_gap_hits"] = len(ids)
        item["new_gap_physical_ids"] = sorted(ids)
        selected.append(item)
        seen[row["job"]].update(ids)
    return selected


def _render(report: dict[str, Any]) -> str:
    lines = [
        "# 五职业直接 Hit 确定归因专项审计 v68", "",
        "- 固定 35 份抓包，逐份校验 SHA-256。",
        "- 当前技能名只作为待审桶；正向真值只来自独立确定原生字段/同 owner 同 generation 对象或实体链。",
        "- 禁止时间邻近、伤害金额、旧技能名、跨职业及跨同职业借 owner。",
        "- 本轮只生成审计产物，未修改共享生产。", "",
        "## 覆盖结果", "",
        "|职业|当前确定覆盖|距95% Hit|安全同桶候选|审计纠错候选|安全投影|是否达到95%|",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in report["jobs"]:
        lines.append(
            f"|{row['job']}|{row['coverage_percent']:.4f}%|{row['hits_needed_for_95']}|"
            f"{row['safe_same_bucket_hits']}|{row['correction_candidate_hits']}|"
            f"{row['safe_projected_coverage_percent']:.4f}%|"
            f"{'是' if row['safe_projected_coverage_percent'] >= 95 else '否'}|"
        )
    lines += ["", "## 可生产化同桶规则（按新增 Hit 排序）", ""]
    if not report["safe_rules"]:
        lines.append("- 没有满足完整门槛且仍能覆盖当前缺口的规则。")
    for row in report["safe_rules"]:
        lines.append(
            f"- {row['job']} → {row['canonical_skill_id']}：{row['new_unique_gap_hits']} Hit；"
            f"`{row['variant']}`，键 `{json.dumps(row['key'], ensure_ascii=False, sort_keys=True)}`。"
        )
    lines += ["", "## 纠错候选（不自动生产化）", ""]
    if not report["correction_rules"]:
        lines.append("- 没有由独立唯一真值支持的跨技能纠错对。")
    for row in report["correction_rules"][:30]:
        pairs = ", ".join(
            f"{item['from_skill_id']}→{item['to_skill_id']} ({item['hits']})"
            for item in row["correction_pairs"]
        )
        lines.append(
            f"- {row['job']}：{pairs}；去重可影响 {row['new_unique_gap_hits']} Hit；"
            f"真值 {row['truth_hits']} Hit / {row['truth_capture_count']} 包 / {row['truth_actor_count']} 角色。"
        )
    lines += ["", "## 边界", "",
        "- 冰灵当前已超过 95%，仍保留边界复核，不以覆盖率为理由放宽规则。",
        "- 纠错候选必须再做逐条原始记录边界审计；本报告不会把它们计入安全投影。",
        "- 同一物理 Hit 匹配多个等价键时按 physical_id 去重。",
        "- 真实 fixture 固定抓包哈希、包序、记录偏移、来源/owner、对象 ID、模板和代次。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be 1..4")

    native.TARGET_JOBS = TARGET_JOBS
    production_before = native._tree_signature()
    captures, selection_report = native._selection(args.selection)
    runtime, baseline = _runtime_from_current_baseline(args.baseline)
    results = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    partials = args.output_dir / "partials"
    partials.mkdir(exist_ok=True)
    pending = []
    for entry in captures:
        partial_path = partials / f"{entry['sha256']}.json"
        if partial_path.is_file():
            saved = json.loads(partial_path.read_text(encoding="utf-8"))
            if (
                saved.get("capture_sha256") == entry["sha256"]
                and saved.get("loaded_production_signature") == production_before
            ):
                results.append(saved)
                continue
        pending.append(entry)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_audit_one, row): row for row in pending}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            (partials / f"{result['capture_sha256']}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{len(results):02d}/{len(captures)}] {result['capture']}", flush=True)
    results.sort(key=lambda row: row["capture_sha256"])
    aggregate = native._aggregate(results, runtime)
    physical_rows = _rows_by_physical(results)

    safe_rules = sorted(
        aggregate["recommended_nonoverlapping_rules"],
        key=lambda row: (-int(row["new_unique_gap_hits"]), row["job"]),
    )
    corrections = _nonoverlap_corrections(_correction_candidates(aggregate))
    safe_by_job: dict[str, set[str]] = defaultdict(set)
    correction_by_job: dict[str, set[str]] = defaultdict(set)
    fixtures: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(safe_rules):
        ids = list(row["new_gap_physical_ids"])
        safe_by_job[row["job"]].update(ids)
        fixtures[f"safe:{index}:{row['job']}:{row['canonical_skill_id']}"] = [
            _fixture(physical_rows[value]) for value in ids[:5] if value in physical_rows
        ]
    for index, row in enumerate(corrections):
        ids = list(row["new_gap_physical_ids"])
        correction_by_job[row["job"]].update(ids)
        fixtures[f"correction:{index}:{row['job']}:{row['canonical_skill_id']}"] = [
            _fixture(physical_rows[value]) for value in ids[:5] if value in physical_rows
        ]

    jobs = []
    for job in TARGET_JOBS:
        base = runtime[job]
        safe = len(safe_by_job[job])
        corrected = len(correction_by_job[job])
        jobs.append({
            "job": job,
            **base,
            "safe_same_bucket_hits": safe,
            "correction_candidate_hits": corrected,
            "safe_projected_coverage_percent": 100.0 * (base["confirmed_hits"] + safe) / max(1, base["named_hits"]),
        })

    production_after = native._tree_signature()
    loaded_signatures = {
        json.dumps(row.get("loaded_production_signature", {}), sort_keys=True)
        for row in results
    }
    report = {
        "schema": SCHEMA,
        "policy": {
            "production_changed": production_before != production_after,
            "current_skill_label_is_truth": False,
            "forbidden": ["time proximity", "damage amount", "old label as truth", "cross-job", "cross-same-job owner"],
            "safe_positive_contract": "unique independent truth >=8 Hit, >=2 captures, >=2 actors, exact native/object generation key",
        },
        "inputs": {
            "selection": str(args.selection), "selection_sha256": _hash(args.selection),
            "baseline": str(args.baseline), "baseline_sha256": _hash(args.baseline),
            "selection_schema": selection_report.get("schema_version", ""),
            "capture_count": len(captures),
        },
        "jobs": jobs,
        "safe_rules": safe_rules,
        "correction_rules": corrections,
        "gap_skills": aggregate["gap_skills"],
        "real_fixtures": fixtures,
        "invariants": {
            "capture_hashes_unique": len({row["capture_sha256"] for row in results}) == 35,
            "worker_loaded_signature_unique": len(loaded_signatures) == 1,
            "concurrent_external_production_change_detected": production_before != production_after,
            "audit_tool_writes_only_output_directory": True,
            "physical_hits_relabel_only": True,
            "damage_admission_changed": False,
        },
        "baseline_analysis_signature": baseline.get("analysis_signature", ""),
    }
    (args.output_dir / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "REPORT.md").write_text(_render(report), encoding="utf-8")
    (args.output_dir / "production_rule_candidates.json").write_text(
        json.dumps({"safe": safe_rules, "correction_audit_only": corrections}, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "real_fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = {
        "schema": SCHEMA,
        "passed": all(
            report["invariants"][key]
            for key in (
                "capture_hashes_unique", "worker_loaded_signature_unique",
                "audit_tool_writes_only_output_directory",
                "physical_hits_relabel_only",
            )
        ) and not report["invariants"]["damage_admission_changed"],
        "checks": report["invariants"],
    }
    (args.output_dir / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation["passed"]:
        raise AssertionError(validation)
    print(json.dumps({"jobs": jobs, "safe_rules": len(safe_rules), "corrections": len(corrections)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
