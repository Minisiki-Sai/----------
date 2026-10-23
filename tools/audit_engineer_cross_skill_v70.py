"""Replay the frozen Heavy Shooter v66 gaps and audit strict engineer sub-skill evidence.

This is a report-only audit.  It deliberately reuses the v65 physical UDP
joiner, but changes its target to Heavy Shooter and inspects *every* final v66
gap rather than only the historical 4216 bucket.  No production module, score,
or upload state is changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import audit_gear_master_gaps_v65 as physical_audit  # noqa: E402


OUT = ROOT / "outputs/engineer_cross_skill_v70_20260909"
V66 = ROOT / "outputs/direct_damage_v66_full_20260909/profession_direct_damage_report.json"
CHECKPOINTS = ROOT / "outputs/direct_damage_v66_full_20260909/profession_direct_damage_checkpoints"
V67 = ROOT / "outputs/heavy_duck_remaining_v67_20260909/REPORT.json"
V64_HEAVY = ROOT / "outputs/heavy_duck_4216_v64_20260909/REPORT.json"
TARGET_JOB = "重炮手"

# Exact IDs represented by the audited Engineer trees.  This is used only to
# identify impossible *current labels*; it is never used to select a new skill.
ENGINEER_RANGES = ((4000, 4399), (48001, 48002))
ENGINEER_EXTRA = frozenset({7301})


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def engineer_skill(skill_id: int) -> bool:
    return skill_id in ENGINEER_EXTRA or any(low <= skill_id <= high for low, high in ENGINEER_RANGES)


def summary(report: dict[str, Any]) -> dict[str, Any]:
    return next(row for row in report["summary"] if row["职业"] == TARGET_JOB)


def capture_inputs() -> list[dict[str, Any]]:
    required = {
        str(row["capture"]): str(row["capture_sha256"])
        for row in read(V64_HEAVY)["capture_results"]
    }
    found: dict[str, dict[str, Any]] = {}
    for checkpoint in CHECKPOINTS.glob("*.json"):
        value = read(checkpoint)
        report = value.get("report", {})
        name = str(report.get("capture", ""))
        if name not in required or str(report.get("capture_sha256", "")) != required[name]:
            continue
        job = (report.get("profession_summary") or {}).get(TARGET_JOB)
        if not isinstance(job, dict):
            continue
        nondeterministic = int(job.get("nondeterministic_hits", 0) or 0)
        unattributed = int(job.get("unattributed_hits", 0) or 0)
        expected_hits = nondeterministic + unattributed
        found[name] = {
            "capture": str(report["capture_path"]),
            "capture_sha256": str(report["capture_sha256"]),
            "raw_source_invariants": report["raw_source_invariants"],
            "aggregated_invariants": report["aggregated_invariants"],
            "expected_gap_hits": expected_hits,
            "expected_gap_damage": int(job.get("nondeterministic_damage", 0) or 0)
            + int(job.get("unattributed_damage", 0) or 0),
            "expected_profession_summary": job,
            "checkpoint": str(checkpoint),
        }
    missing = sorted(set(required) - set(found))
    if missing:
        raise RuntimeError(f"Missing frozen v66 checkpoints: {missing}")
    return [found[name] for name in sorted(found)]


def physical_identity(row: dict[str, Any]) -> tuple[object, ...]:
    p = row["physical"]
    e = row["event"]
    return (
        p["capture"], int(p["packet_serial"]), int(p["record_offset"]),
        int(p["source_actor_id"]), int(p["target_actor_id"]),
        int(p["result_group_id"]), int(p["damage_object_id"]), int(p["damage"]),
        bool(p["critical"]), int(e["timestamp_ms"]),
    )


def compact(row: dict[str, Any], *, decision: str) -> dict[str, Any]:
    p = row["physical"]
    e = row["event"]
    slot = p.get("self_attack_slot") or {}
    return {
        "decision": decision,
        "capture": p["capture"], "packet_serial": int(p["packet_serial"]),
        "timestamp_ms": int(e["timestamp_ms"]), "record_offset": int(p["record_offset"]),
        "wire_record_sha256": p["wire_record_sha256"],
        "source_actor_id": int(p["source_actor_id"]), "target_actor_id": int(p["target_actor_id"]),
        "target_name": e.get("target_name", ""), "target_job": e.get("target_job", ""),
        "damage_object_id": int(p["damage_object_id"]), "result_group_id": int(p["result_group_id"]),
        "damage": int(p["damage"]), "critical": bool(p["critical"]),
        "old_skill_id": int(e["skill_id"]), "old_skill_name": e["skill_name"],
        "old_method": e["attribution_method"],
        "template_id": int(p.get("entity_template_id", 0) or 0),
        "generation": int(p.get("generation", 0) or 0),
        "native_key": int((p.get("native_result", {}).get("selected") or {}).get("raw_key", 0) or 0),
        "self_attack_key": int(slot.get("monster_skill_id", 0) or 0),
        "proof_skill_ids": row["proof_skill_ids"],
        "proofs": row["proofs"],
        "object_chain_reason": p.get("object_chain", {}).get("reason", ""),
        "selector_chain_reason": p.get("selector_chain", {}).get("reason", ""),
        "identity": list(physical_identity(row)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Replay captures even if the local v70 cache exists")
    args = parser.parse_args()

    baseline = read(V66)
    base = summary(baseline)
    inputs = capture_inputs()
    if sum(int(item["expected_gap_hits"]) for item in inputs) != int(base["归因缺口"]):
        raise RuntimeError("Frozen v66 Heavy Shooter gap totals do not match the selected captures")

    # The v66 baseline used the replay source's normal parser (None), whereas
    # the historical v65 audit deliberately used ``structured``.  Force the
    # v66 parser here so current labels and event identity are comparable to
    # the frozen report, while retaining v65's physical UDP join machinery.
    original_replay_source = physical_audit.V576DiagnosticReplaySource

    def v66_replay_source(*source_args: Any, **source_kwargs: Any) -> Any:
        source_kwargs["action_parser_mode"] = None
        return original_replay_source(*source_args, **source_kwargs)

    # The imported routine only writes v70 cache files and replays input data.
    physical_audit.OUT = OUT
    physical_audit.SCHEMA = "engineer-cross-skill-v70-v66-parser"
    physical_audit.TARGET_JOB = TARGET_JOB
    physical_audit.BASELINE_REPORT = V66
    physical_audit.V576DiagnosticReplaySource = v66_replay_source
    try:
        # Cache entries from the structured v65 parser have a different schema
        # and are not reused.  The first v70 run was forced; later report-only
        # reruns may reuse that byte-identical v66-parser cache.
        results = [physical_audit.run_capture(item, force=args.force) for item in inputs]
    finally:
        physical_audit.V576DiagnosticReplaySource = original_replay_source
    rows = [row for result in results for row in result["gap_rows"]]
    if len(rows) != int(base["归因缺口"]):
        raise RuntimeError("v70 physical row count does not match v66 Heavy Shooter gap")
    if sum(int(row["event"]["amount"]) for row in rows) != int(base["缺口伤害"]):
        raise RuntimeError("v70 physical damage does not match v66 Heavy Shooter gap")

    strict = [row for row in rows if len(row["proof_skill_ids"]) == 1]
    conflicts = [row for row in rows if len(row["proof_skill_ids"]) > 1]
    unresolved = [row for row in rows if not row["proof_skill_ids"]]
    corrections = [
        row for row in strict
        if int(row["event"]["skill_id"]) != int(row["proof_skill_ids"][0])
    ]
    label_errors = [
        row for row in rows
        if int(row["event"]["skill_id"]) > 0 and not engineer_skill(int(row["event"]["skill_id"]))
    ]
    label_errors_without_proof = [row for row in label_errors if not row["proof_skill_ids"]]

    # The frozen v66 summary report has a small set of cross-job labels in its
    # aggregated detail rows.  The current v66-parser replay conserves every
    # raw/aggregated physical invariant but no longer emits those names.  Keep
    # this delta explicit: a historical weak name is neither a candidate nor
    # evidence that the current Hit belongs to the static 5043 -> 4301 edge.
    historical_cross_job_labels = []
    for item in baseline["details"]:
        if item["职业"] != TARGET_JOB or item["归因分类"] == "deterministic":
            continue
        try:
            old_skill = int(item["skill_id"])
        except (TypeError, ValueError):
            continue
        if old_skill <= 0 or engineer_skill(old_skill):
            continue
        historical_cross_job_labels.append({
            "old_skill_id": old_skill, "old_skill_name": item["技能名"],
            "method": item["method"], "object_template": item["object模板"],
            "hits": int(item["Hit数"]), "damage": int(item["伤害"]),
            "captures": item["示例抓包"],
            "decision": "historical_weak_label_not_reproduced_by_current_physical_replay",
        })
    historical_cross_hits = sum(item["hits"] for item in historical_cross_job_labels)
    historical_cross_damage = sum(item["damage"] for item in historical_cross_job_labels)

    template_status: dict[str, Any] = {}
    for template in (32005, 32008, 0):
        subset = [row for row in rows if int(row["physical"].get("entity_template_id", 0) or 0) == template]
        template_status[str(template)] = {
            "gap_hits": len(subset),
            "gap_damage": sum(int(row["event"]["amount"]) for row in subset),
            "strict_hits": sum(len(row["proof_skill_ids"]) == 1 for row in subset),
            "conflict_hits": sum(len(row["proof_skill_ids"]) > 1 for row in subset),
            "unresolved_hits": sum(not row["proof_skill_ids"] for row in subset),
            "fixed_slot_values": dict(Counter(
                str((row["physical"].get("self_attack_slot") or {}).get("monster_skill_id", "missing"))
                for row in subset
            )),
            "object_reasons": dict(Counter(row["physical"].get("object_chain", {}).get("reason", "") for row in subset)),
            "selector_reasons": dict(Counter(row["physical"].get("selector_chain", {}).get("reason", "") for row in subset)),
        }

    exact_keys = sorted(json.dumps(physical_identity(row), ensure_ascii=True, separators=(",", ":")) for row in rows)
    invariant_hash = hashlib.sha256("\n".join(exact_keys).encode("ascii")).hexdigest()
    correction_pairs = Counter(
        f"{row['event']['skill_id']}->{row['proof_skill_ids'][0]}" for row in corrections
    )
    strict_pairs = Counter(str(row["proof_skill_ids"][0]) for row in strict)
    required = math.ceil(int(base["总直接Hit"]) * 0.95) - int(base["确定性已归因"])
    report = {
        "schema": "engineer-cross-skill-v70",
        "scope": "two frozen v66 Heavy Shooter captures; every final non-deterministic/unattributed direct Hit",
        "policy": {
            "accepted": [
                "same current entity birth/owner generation plus fixed self/native attack key",
                "same-generation selector or projectile registration to the exact damage_object_id",
            ],
            "rejected_as_proof": ["old label", "time proximity", "damage amount", "hit pattern", "result group"],
        },
        "baseline": {
            "total_direct_hits": int(base["总直接Hit"]), "deterministic_hits": int(base["确定性已归因"]),
            "gap_hits": int(base["归因缺口"]), "gap_damage": int(base["缺口伤害"]),
            "coverage_percent": float(base["确定性覆盖率%"]),
        },
        "capture_inputs": inputs,
        "strict_results": {
            "strict_unique_proof_hits": len(strict), "strict_unique_proof_damage": sum(int(row["event"]["amount"]) for row in strict),
            "cross_skill_corrections": len(corrections), "correction_pairs": dict(sorted(correction_pairs.items())),
            "proof_skill_ids": dict(sorted(strict_pairs.items())),
            "conflicting_proof_hits": len(conflicts), "unresolved_hits": len(unresolved),
        },
        "named_cross_skill_errors": {
            "hits": len(label_errors), "damage": sum(int(row["event"]["amount"]) for row in label_errors),
            "without_current_fixed_proof": len(label_errors_without_proof),
            "resolution": "revoke old cross-job label; keep unassigned to a specific engineer child skill until a fixed current chain exists",
        },
        "historical_v66_label_delta": {
            "frozen_report_cross_job_hits": historical_cross_hits,
            "frozen_report_cross_job_damage": historical_cross_damage,
            "current_v66_parser_replay_cross_job_hits": len(label_errors),
            "historical_rows": historical_cross_job_labels,
            "5043_interpretation": "5043 is statically Academic_Splash/4301, but the frozen report's 120-second retrospective registration display is not a same-generation damage_object proof; no promotion is allowed from it alone",
            "result": "The label delta does not change any physical Hit, total damage, or coverage. It demonstrates why old weak labels cannot be used as attribution evidence.",
        },
        "template_status": template_status,
        "coverage_projection_if_all_new_strict_rows_are_admitted": {
            "new_strict_rows": len(strict),
            "deterministic_hits": int(base["确定性已归因"]) + len(strict),
            "coverage_percent": round((int(base["确定性已归因"]) + len(strict)) / int(base["总直接Hit"]) * 100.0, 6),
            "hits_still_needed_for_95": max(0, required - len(strict)),
            "warning": "projection is diagnostic only; existing production-equivalent proofs must be de-duplicated before any production admission",
        },
        "conservation": {
            "before_hit_count": len(rows), "after_hit_count": len(rows),
            "before_damage": sum(int(row["event"]["amount"]) for row in rows),
            "after_damage": sum(int(row["event"]["amount"]) for row in rows),
            "damage_delta": 0, "new_rows": 0, "removed_rows": 0,
            "physical_multiset_sha256_before": invariant_hash,
            "physical_multiset_sha256_after": invariant_hash,
        },
        "inputs": {str(path): digest(path) for path in (V66, V67, V64_HEAVY)},
        "production_changed": False, "scoring_run": False, "upload": False,
    }
    write(OUT / "REPORT.json", report)
    write(OUT / "strict_rows.json", [compact(row, decision="strict_unique_proof") for row in strict])
    write(OUT / "cross_skill_label_errors.json", [compact(row, decision="revoke_cross_job_name") for row in label_errors])
    write(OUT / "unresolved_rows.json", [compact(row, decision="unresolved_no_fixed_current_chain") for row in unresolved])

    lines = [
        "# 工程系跨技能严格审计 v70", "",
        "本轮重放 v66 冻结的两份重炮手抓包，逐一回连每个最终缺口到原始 UDP 物理 Hit。未修改生产、评分或上传。", "",
        "## 结果", "",
        f"- 基线：{int(base['归因缺口']):,} 缺口 Hit，伤害 {int(base['缺口伤害']):,}。",
        f"- 严格唯一证据：{len(strict):,} Hit；其中跨技能纠错 {len(corrections):,} Hit。",
        f"- 冲突证据：{len(conflicts):,} Hit；无固定当前链：{len(unresolved):,} Hit。",
        f"- 错误跨职业当前名：{len(label_errors):,} Hit；其中 {len(label_errors_without_proof):,} Hit 仍不能安全指定工程子技能。", "",
        "## 历史标签差异", "",
        f"- 冻结 v66 汇总曾显示 {historical_cross_hits:,} 个跨职业弱标签；按相同 v66 物理输入重放后为 {len(label_errors):,} 个。",
        "- 两种视图的原始/聚合物理守恒都一致；旧标签不用于归因。特别是 `5043 → 飞溅(4301)` 仅是历史展示关联，缺少同 generation 的 exact damage_object 链。", "",
        "## 关键实体", "",
        "|来源模板|缺口Hit|严格唯一|未闭合|", "|---|---:|---:|---:|",
    ]
    for template in (32005, 32008, 0):
        item = template_status[str(template)]
        lines.append(f"|{template if template else '无实体模板'}|{item['gap_hits']}|{item['strict_hits']}|{item['unresolved_hits']}|")
    lines += ["", "## 守恒", "", f"- {len(rows):,} Hit、{sum(int(row['event']['amount']) for row in rows):,} 伤害在重放前后完全一致。", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
