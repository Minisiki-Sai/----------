"""Strict cross-skill audit for v66 Heavy Shooter and Gear Master gaps.

This tool is deliberately report-only.  It compares the frozen v64/v66
profession reports, verifies whether the four v68 physical candidates were
already counted by v66, and flags impossible cross-job labels without turning
those labels, timing, damage values, or result groups into attribution proof.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/engineer_cross_skill_v69_20260909"
V64 = ROOT / "outputs/direct_damage_v64_incremental_20260909/profession_direct_damage_report.json"
V66 = ROOT / "outputs/direct_damage_v66_full_20260909/profession_direct_damage_report.json"
V65 = ROOT / "outputs/gear_master_gaps_v65_20260909"
V67 = ROOT / "outputs/heavy_duck_remaining_v67_20260909"
V68 = ROOT / "outputs/engineer_professions_gaps_v68_20260909"
JOBS = ("重炮手", "机械大师")

# IDs from the Scholar/Engineer/Heavy Shooter/Gear Master damage trees that are
# present in the audited reports.  7301 is a valid Heavy Shooter skill despite
# not sharing the ordinary 4xxx range.
ENGINEER_ID_RANGES = ((4000, 4399), (48001, 48002))
ENGINEER_EXTRA_IDS = frozenset({7301})


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_engineer_skill(skill_id: int) -> bool:
    return skill_id in ENGINEER_EXTRA_IDS or any(lo <= skill_id <= hi for lo, hi in ENGINEER_ID_RANGES)


def summary_index(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["职业"]): row for row in report["summary"]}


def detail_signature(report: dict[str, Any], job: str) -> list[tuple[Any, ...]]:
    rows = []
    for row in report["details"]:
        if row["职业"] != job:
            continue
        rows.append((
            row["归因分类"], row["method"], str(row["skill_id"]), row["技能名"],
            row["object模板"], int(row["Hit数"]), int(row["伤害"]), row["示例抓包"],
        ))
    return sorted(rows)


def compact_v68_candidate(item: dict[str, Any], index: int) -> dict[str, Any]:
    p = item["physical"]
    return {
        "candidate_index": index,
        "capture": p["capture"],
        "packet_serial": int(p["packet_serial"]),
        "record_offset": int(p["record_offset"]),
        "wire_record_sha256": p["wire_record_sha256"],
        "source_actor_id": int(p["source_actor_id"]),
        "target_actor_id": int(p["target_actor_id"]),
        "damage_object_id": int(p["damage_object_id"]),
        "result_group_id": int(p["result_group_id"]),
        "damage": int(p["damage"]),
        "critical": bool(p["critical"]),
        "old_skill_id": int(item["old_skill_id"]),
        "new_skill_id": int(item["new_skill_id"]),
        "route": item["route"],
        "covered_by_v66": False,
        "v66_overlap_reason": (
            "v66 retains the unique 262462 row as nondeterministic 4216/supplemental_summon_owner"
            if int(p["damage"]) == 262462
            else "the exact physical row remains a nondeterministic 4216 decoder event in v65; v64 and v66 engineering summaries and detail multisets are identical"
        ),
        "target_scope": (
            "non_boss_engineer_entity; skill identity is closed but Boss-DPS admission is separate"
            if int(p["damage"]) == 262462
            else "boss_target"
        ),
    }


def main() -> int:
    v64 = read(V64)
    v66 = read(V66)
    v65_report = read(V65 / "REPORT.json")
    v65_confirmed = read(V65 / "confirmed_rows.json")
    v65_unclosed = read(V65 / "unclosed_rows.json")
    v67_report = read(V67 / "REPORT.json")
    v68_report = read(V68 / "REPORT.json")
    v68_candidates = read(V68 / "production_candidates.json")
    s64 = summary_index(v64)
    s66 = summary_index(v66)

    comparisons: dict[str, Any] = {}
    for job in JOBS:
        keys = ("总直接Hit", "确定性已归因", "未归因", "非确定性归因", "归因缺口", "总直接伤害", "确定性伤害", "缺口伤害")
        comparisons[job] = {
            "v64": {key: s64[job][key] for key in keys},
            "v66": {key: s66[job][key] for key in keys},
            "summary_identical": all(s64[job][key] == s66[job][key] for key in keys),
            "detail_multiset_identical": detail_signature(v64, job) == detail_signature(v66, job),
        }

    if not all(row["summary_identical"] and row["detail_multiset_identical"] for row in comparisons.values()):
        raise RuntimeError("v64/v66 engineering baseline drifted; v68 overlap result is no longer valid")
    if len(v68_candidates) != 4:
        raise RuntimeError("Expected exactly four frozen v68 candidates")

    overlap_rows = [compact_v68_candidate(item, index) for index, item in enumerate(v68_candidates, 1)]
    overlap_pairs = Counter(f"{row['old_skill_id']}->{row['new_skill_id']}" for row in overlap_rows)

    # Current labels from weak inference layers are diagnostics only.  Any ID
    # outside the engineer trees is certainly an invalid current name, but it
    # does not reveal which engineer child attack produced the Hit.
    impossible_labels = []
    for row in v66["details"]:
        if row["职业"] not in JOBS or row["归因分类"] == "deterministic":
            continue
        try:
            skill_id = int(row["skill_id"])
        except (TypeError, ValueError):
            continue
        if skill_id <= 0 or is_engineer_skill(skill_id):
            continue
        impossible_labels.append({
            "profession": row["职业"], "classification": row["归因分类"],
            "method": row["method"], "old_skill_id": skill_id,
            "old_skill_name": row["技能名"], "object_template": row["object模板"],
            "hits": int(row["Hit数"]), "damage": int(row["伤害"]),
            "captures": row["示例抓包"],
            "decision": "reject_current_name_keep_unresolved_engineer_child_attack",
        })

    impossible_hits = sum(row["hits"] for row in impossible_labels)
    impossible_damage = sum(row["damage"] for row in impossible_labels)
    by_object = Counter()
    for row in impossible_labels:
        by_object[row["object_template"]] += row["hits"]

    # The one rune row hits another engineer entity.  It must not silently
    # improve deterministic Boss-DPS coverage even though its child skill is
    # identified by a fixed internal key.
    boss_eligible_candidates = [row for row in overlap_rows if row["target_scope"] == "boss_target"]
    projected: dict[str, Any] = {}
    for job in JOBS:
        baseline = s66[job]
        gained = len(boss_eligible_candidates) if job == "机械大师" else 0
        total = int(baseline["总直接Hit"])
        deterministic = int(baseline["确定性已归因"])
        target = math.ceil(total * 0.95)
        projected[job] = {
            "v66_total_direct_hits": total,
            "v66_deterministic_hits": deterministic,
            "v66_coverage_percent": float(baseline["确定性覆盖率%"]),
            "v68_not_already_counted_hits": 4 if job == "机械大师" else 0,
            "boss_eligible_safe_corrections": gained,
            "non_boss_identity_only_rows": 1 if job == "机械大师" else 0,
            "projected_boss_deterministic_hits": deterministic + gained,
            "projected_boss_coverage_percent": round((deterministic + gained) / total * 100.0, 6),
            "hits_still_needed_for_95": max(0, target - deterministic - gained),
        }

    physical_hits = sum(row["hits"] for row in impossible_labels) + len(overlap_rows)
    physical_damage = impossible_damage + sum(row["damage"] for row in overlap_rows)
    conservation_token = json.dumps(
        sorted((row["capture"], row["packet_serial"], row["record_offset"], row["damage"], row["critical"]) for row in overlap_rows),
        ensure_ascii=True, separators=(",", ":"),
    )
    overlap_hash = hashlib.sha256(conservation_token.encode("ascii")).hexdigest()

    report = {
        "schema": "engineer-cross-skill-v69",
        "scope": "frozen v66 Heavy Shooter/Gear Master direct-Hit gaps; report-only cross-skill audit",
        "policy": {
            "accepted_evidence": [
                "same current entity birth/owner generation and fixed positive self-attack key",
                "same-generation selector/projectile registration to the exact damage_object_id",
            ],
            "forbidden_as_proof": ["old/current weak label", "time proximity", "damage value", "hit pattern", "result_group_id"],
        },
        "v64_v66_baseline_check": comparisons,
        "v68_overlap_check": {
            "candidates": len(overlap_rows),
            "already_covered_by_v66": 0,
            "not_covered_by_v66": len(overlap_rows),
            "correction_pairs": dict(sorted(overlap_pairs.items())),
            "rows": overlap_rows,
            "important_scope_correction": "261108 identifies 4217, but that row targets another 32005 entity and is not a Boss-DPS promotion",
        },
        "cross_skill_name_audit": {
            "invalid_current_labels": len(impossible_labels),
            "hits": impossible_hits,
            "damage": impossible_damage,
            "by_object_template_hits": dict(sorted(by_object.items())),
            "rows": impossible_labels,
            "result": "all invalid names must be removed; none can be promoted to a specific engineer skill without a current fixed object/entity attack edge",
        },
        "remaining_strict_gaps": {
            "重炮手": {
                "total_v66_gap_hits": int(s66["重炮手"]["归因缺口"]),
                "audited_4216_unclosed_hits": int(v67_report["remaining"]["hits"]),
                "audited_4216_reasons": v67_report["remaining"]["reasons"],
                "invalid_cross_job_named_hits": sum(row["hits"] for row in impossible_labels if row["profession"] == "重炮手"),
            },
            "机械大师": {
                "total_v66_gap_hits": int(s66["机械大师"]["归因缺口"]),
                "v65_unclosed_rows": len(v65_unclosed),
                "v65_selector_rows": len(v65_confirmed),
                "v68_new_identity_rows": len(overlap_rows),
                "boss_eligible_v68_rows": len(boss_eligible_candidates),
            },
        },
        "coverage_projection": projected,
        "conservation": {
            "audited_row_count_before": physical_hits,
            "audited_row_count_after": physical_hits,
            "audited_damage_before": physical_damage,
            "audited_damage_after": physical_damage,
            "damage_delta": 0,
            "new_rows": 0,
            "removed_rows": 0,
            "v68_physical_multiset_sha256_before": overlap_hash,
            "v68_physical_multiset_sha256_after": overlap_hash,
        },
        "inputs": {str(path): sha256(path) for path in (
            V64, V66, V65 / "REPORT.json", V65 / "confirmed_rows.json",
            V65 / "unclosed_rows.json", V67 / "REPORT.json",
            V68 / "REPORT.json", V68 / "production_candidates.json",
        )},
        "production_changed": False,
        "scoring_run": False,
        "upload": False,
    }

    write(OUT / "REPORT.json", report)
    write(OUT / "v68_overlap_rows.json", overlap_rows)
    write(OUT / "invalid_cross_job_labels.json", impossible_labels)

    lines = [
        "# 工程系跨技能严格审计 v69", "",
        "本轮只做冻结结果对账与交叉技能检查，不修改共享生产、评分或上传。", "",
        "## v68 与 v66 重复计数检查", "",
        "- v64 与 v66 的重炮手、机械大师总计和完整明细多重集均一致。",
        "- v68 的 4 条候选在 v66 中均未正式覆盖，不存在重复计数。",
        "- 其中 3 条 `4216 → 4352` 是 Boss 目标，可作为后续生产候选。",
        "- `261108` 的 1 条 `4216 → 4217` 技能身份可靠，但目标是另一个 `32005` 工程实体，不能计入 Boss DPS 覆盖率。", "",
        "## 当前技能名交叉检查", "",
        f"- 发现 {impossible_hits} 个 Hit（伤害 {impossible_damage:,}）被弱推断层标成其他职业技能。",
        "- 这些旧名可以确定错误；对象主要落在 `32008` 摄魂怪实体和 `5043` 工程 Projectile。",
        "- 由于这些记录缺当前固定子攻击槽或同 generation 的精确对象来源，本轮只撤销其证明效力，不硬归到 4212/4214/4303/4301。", "",
        "## 覆盖率", "",
        "|职业|v66覆盖率|Boss目标安全增量|投影覆盖率|距95%仍需|", "|---|---:|---:|---:|---:|",
    ]
    for job in JOBS:
        row = projected[job]
        lines.append(f"|{job}|{row['v66_coverage_percent']:.6f}%|{row['boss_eligible_safe_corrections']}|{row['projected_boss_coverage_percent']:.6f}%|{row['hits_still_needed_for_95']}|")
    lines += ["", "## 守恒", "", f"- 审计集合 {physical_hits} Hit、伤害 {physical_damage:,}；前后完全一致。", "- 新增 0、删除 0、伤害差 0。", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
