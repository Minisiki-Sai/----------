"""Audit whether current-action state can safely seed a root damage-object skill.

This is an offline, read-only audit.  It deliberately refuses time, damage amount,
old label, hit pattern, or a player's latest action as proof.  A candidate must have
an exact (owner, object id, generation) root and an independently established native
skill key.  The script does not import or modify production attribution code.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "cleric_current_skill_v71_20260909"
INPUTS = {
    "baseline": ROOT / "outputs" / "current_native_baseline_v57_20260909" / "profession_direct_damage_report.json",
    "action_state": ROOT / "outputs" / "direct_action_state_v56_20260909" / "REPORT.json",
    "getter": ROOT / "outputs" / "damage_object_skill_getter_v56_20260909" / "REPORT.json",
    "chain_lightning": ROOT / "outputs" / "chain_lightning_meter_v60_20260909" / "REPORT.json",
    "cross_skill": ROOT / "outputs" / "cleric_cross_skill_v70_20260909" / "REPORT.json",
    "inquisitor_v63": ROOT / "outputs" / "cleric_skill_gaps_v63_20260909" / "REPORT.json",
    "inquisitor_rows": ROOT / "outputs" / "cleric_skill_gaps_v63_20260909" / "captures",
}
JOBS = ("十字军", "圣骑士", "雷神", "圣徒")


def load(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def scalar(value):
    """Convert report-only values into JSON-safe primitives."""
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def action_routes(action):
    routes = action["aggregate"]["route_summary"]
    names = (
        "hit_0e_direct_signal",
        "hit_0e_track_direct_signal",
        "hit_12_direct_signal",
        "hit_12_track_direct_signal",
        "hit_any_direct_signal",
        "result_group_0e_direct_signal",
        "result_group_any_direct_signal",
    )
    return {name: {key: scalar(routes[name][key]) for key in (
        "candidate_hits", "truth_hits", "truth_matches", "truth_conflicts", "gap_hits", "accepted_rules"
    )} for name in names}


def cleric_baseline(baseline, action):
    by_job = {row["职业"]: row for row in baseline["summary"]}
    action_jobs = {row["job"]: row for row in action["aggregate"]["jobs"]}
    result = []
    for job in JOBS:
        base = by_job[job]
        state = action_jobs[job]
        result.append({
            "job": job,
            "total_direct_hits": base["总直接Hit"],
            "deterministic_hits_before": base["确定性已归因"],
            "gap_hits_before": base["归因缺口"],
            "coverage_before_percent": base["确定性覆盖率%"],
            "current_action_upper_bound": state["accepted_unique_gain_upper_bound"],
            "coverage_if_all_prior_accepted_rules_replayed_percent": state["projected_coverage_percent"],
            "new_deterministic_hits_v71": 0,
            "same_job_old_to_new_corrections_v71": 0,
            "coverage_after_percent": base["确定性覆盖率%"],
            "reason": "No current-action proposal has a proved network write to this root generation's +0x1BC0 key.",
        })
    return result


def accepted_controls(action):
    """Keep only Cleric controls. They prove action can corroborate an existing chain,
    not that action itself writes a root skill key."""
    controls = []
    seen = set()
    for rule in action["aggregate"].get("accepted_rules", []):
        if rule.get("job") not in JOBS:
            continue
        key = (rule.get("job"), rule.get("candidate_skill_id"), rule.get("route"))
        if key in seen:
            continue
        seen.add(key)
        controls.append({
            "job": rule["job"],
            "skill_id": rule["candidate_skill_id"],
            "skill_name": rule["candidate_skill_name"],
            "hits": rule["candidate_hits"],
            "truth_hits": rule["truth_hits"],
            "truth_matches": rule["truth_matches"],
            "truth_conflicts": rule["truth_conflicts"],
            "independent_truth_method": rule["truth_methods"],
            "existing_gap_hits": rule["gap_hits"],
            "interpretation": "Corroborating control only: its truth already comes from an exact projectile/object chain.",
        })
    return controls


def inquisitor_skill_result_table(v63):
    rows = []
    for raw_skill_id, row in v63.get("skill_results", {}).items():
        rows.append({
            "skill_id": int(raw_skill_id),
            "skill_name": row["skill_name"],
            "old_gap_hits": row["baseline_gap_hits"],
            "v63_confirmed_upgrade_hits": row["confirmed_promotions"],
            "v63_cross_skill_corrected_out_hits": row["confirmed_corrected_out"],
            "v63_cross_skill_corrected_in_hits": row["confirmed_corrected_in"],
            "still_unclosed_hits": row["remaining_unclosed_old_bucket_hits"],
            "v71_new_deterministic_hits": 0,
            "v71_cross_skill_corrections": 0,
        })
    return rows


def inquisitor_gap_evidence(directory: Path):
    counts = Counter()
    samples = []
    checked = 0
    for path in sorted(directory.glob("*.json")):
        doc = load(path)
        for row in doc.get("gap_rows", []):
            event = row.get("event", {})
            skill_id = int(event.get("skill_id") or 0)
            if event.get("job") != "雷神" or skill_id not in (3019, 3425, 3553):
                continue
            checked += 1
            physical = row.get("physical", {})
            proof = row.get("candidate_proofs") or []
            counts["candidate_proof_present" if proof else "candidate_proof_absent"] += 1
            if physical.get("selected_native_mapped_skill_id"):
                counts["fixed_native_skill_present"] += 1
            else:
                counts["fixed_native_skill_absent"] += 1
            object_proof = physical.get("object_proof") or {}
            if object_proof.get("generation") is not None:
                counts["object_generation_present"] += 1
            else:
                counts["object_generation_absent"] += 1
            if len(samples) < 8:
                samples.append({
                    "capture": path.name,
                    "skill_id": skill_id,
                    "old_label": event.get("skill_name"),
                    "packet_serial": physical.get("packet_serial"),
                    "owner": physical.get("source_actor_id"),
                    "damage_object_id": physical.get("damage_object_id"),
                    "object_generation": object_proof.get("generation"),
                    "candidate_proofs": len(proof),
                    "fixed_native_skill": physical.get("selected_native_mapped_skill_id"),
                })
    return {"rows_checked": checked, "counts": dict(counts), "samples": samples}


def markdown(report):
    lines = [
        "# 圣职四职业 current-skill 根对象审计 v71",
        "",
        "## 结论",
        "",
        "本轮没有发现可生产化的新 Hit 或跨技能纠错。`0x0E` 是角色动作状态，能作为已闭合对象链的旁证，不能替代根对象 `(owner, object id, generation)` 的原生技能键。根对象 `+0x1BC0` 的首个非零写入仍未能从已解网络字段闭合，因此不能把动作状态、旧技能名或时间邻近冻结到后续伤害。",
        "",
        "- 新确定 Hit：0",
        "- 同职业旧名 → 真实新名纠错：0",
        "- 原始直接伤害：完全守恒（本审计不重放或修改生产结果）",
        "- 共享生产代码：未修改",
        "",
        "## 四职业结果",
        "",
        "| 职业 | 基线确定覆盖 | 缺口 | v71 新确定 Hit | v71 纠错 | 结论 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["cleric_baseline"]:
        lines.append(
            f"| {row['job']} | {row['coverage_before_percent']:.3f}% | {row['gap_hits_before']} | 0 | 0 | current-action 不能作为根键 |"
        )
    lines.extend([
        "",
        "## 可独立验证的反例",
        "",
        "直接把当前 `0x0E/0x12` 技能槽当作 Hit 来源，会与已有独立对象链真值发生大量冲突；因此不符合本任务的确定性条件。",
        "",
        "| 路由 | 有真值 Hit | 一致 | 冲突 | 已接受规则数 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for route, values in report["action_direct_routes"].items():
        lines.append(f"| {route} | {values['truth_hits']} | {values['truth_matches']} | {values['truth_conflicts']} | {values['accepted_rules']} |")
    lines.extend([
        "",
        "`object_birth_0e_*` 的少量零冲突规则仅说明动作与对象出生可共现；这些 Hit 已由 `damage_object_projectile_link` 独立确认，不能把该共现升级为未知根对象的写入规则。",
        "",
        "## 根键闭合状态",
        "",
        "- `CDnProjectile/CDnWeapon +0x1BC0` 的 getter/setter 和父→子复制已静态确认。",
        "- 构造函数会把该字段初始化为 0；子对象可从精确父对象继承。",
        "- 但“网络字段 → 根对象首个非零 `+0x1BC0` 写入”仍未闭合。",
        "- 因而允许：直接 Hit 的固定原生键、同 owner+generation 对象链、完整父链、可靠实体链。禁止：角色当前动作、时间相近、旧名、金额、Hit 模式单独定真。",
        "",
        "## 雷神目标样本",
        "",
        "v63 保存的 3019/3425 缺口逐 Hit 样本中，没有一条同时提供允许的候选证明与固定原生键/当前 generation 对象证明；3553 只在汇总层出现，未被 v63 的逐 Hit 缓存覆盖，不能伪造为已检查。",
        "",
        f"- 已检查逐 Hit 行：{report['inquisitor_gap_evidence']['rows_checked']}",
        f"- 无候选证明：{report['inquisitor_gap_evidence']['counts'].get('candidate_proof_absent', 0)}",
        f"- 有候选证明：{report['inquisitor_gap_evidence']['counts'].get('candidate_proof_present', 0)}",
        "",
        "| 技能 | v63 旧缺口 | v63 已确认升级 | v71 新确定 | v71 纠错 | 仍未闭合 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in report["inquisitor_target_skills"]:
        lines.append(
            f"| {row['skill_id']} {row['skill_name']} | {row['old_gap_hits']} | {row['v63_confirmed_upgrade_hits']} | 0 | 0 | {row['still_unclosed_hits']} |"
        )
    lines.extend([
        "",
        "## 下一步",
        "",
        "继续提升覆盖率应优先补解根对象出生包中的模板/父对象字段，或补实体的 owner→attack→object 链；不要扩大 `continuation_window` 或将 current-skill 固化为对象来源。",
        "",
    ])
    return "\n".join(lines)


def main():
    missing = [str(path) for path in INPUTS.values() if not path.exists()]
    if missing:
        raise SystemExit("Missing audit inputs:\n" + "\n".join(missing))
    OUT.mkdir(parents=True, exist_ok=True)
    baseline = load(INPUTS["baseline"])
    action = load(INPUTS["action_state"])
    getter = load(INPUTS["getter"])
    chain = load(INPUTS["chain_lightning"])
    cross = load(INPUTS["cross_skill"])
    v63 = load(INPUTS["inquisitor_v63"])
    report = {
        "schema": "cleric-current-skill-root-v71",
        "scope": "read-only fixed 35-capture audit; owner+object generation isolation required; no production mutation",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_changed": False,
        "verdict": {
            "safe_new_deterministic_hits": 0,
            "safe_same_job_old_to_new_corrections": 0,
            "root_network_to_nonzero_1bc0_closed": getter["field_write_provenance"][-1]["verdict"] == "closed",
            "current_action_is_a_root_key": False,
            "reason": "No decoded network scalar has been proved to write a root projectile/weapon generation's +0x1BC0 field; direct action routes have independent-truth conflicts.",
        },
        "input_manifest": {name: {"path": str(path), "sha256": sha256(path) if path.is_file() else None} for name, path in INPUTS.items()},
        "cleric_baseline": cleric_baseline(baseline, action),
        "action_direct_routes": action_routes(action),
        "accepted_existing_controls": accepted_controls(action),
        "inquisitor_gap_evidence": inquisitor_gap_evidence(INPUTS["inquisitor_rows"]),
        "inquisitor_target_skills": inquisitor_skill_result_table(v63),
        "static_root_contract": {
            "getter_classes": getter["compatible_getter_implementations"][0]["classes"],
            "field": getter["compatible_getter_implementations"][0]["field"],
            "proven_writes": getter["field_write_provenance"],
            "network_verdict": getter["network_reproducibility"]["overall_verdict"],
        },
        "continuation_window_risk": chain["verdict"],
        "cross_skill_previous_result": cross["verdict"],
        "conservation": {
            "four_job_baseline_direct_damage": sum(row["总直接伤害"] for row in baseline["summary"] if row["职业"] in JOBS),
            "four_job_direct_damage_after_v71": sum(row["总直接伤害"] for row in baseline["summary"] if row["职业"] in JOBS),
            "replayed_production_rows": 0,
            "damage_changed": 0,
            "explanation": "This audit proposes no promotions/corrections, so source/target/damage/critical/wire-ID rows remain the frozen baseline multiset.",
        },
    }
    (OUT / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "REPORT.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
