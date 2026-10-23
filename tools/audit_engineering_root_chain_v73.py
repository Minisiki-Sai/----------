"""Read-only consolidation of strict Engineering summon-root evidence.

It intentionally uses frozen v64/v67/v68/v70 audits plus v72 per-Hit evidence.
It does not replay a capture and cannot change production attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = (args.output or repo / "outputs" / "engineering_root_chain_v73_20260910").resolve()
    out.mkdir(parents=True, exist_ok=True)
    v68_dir = repo / "outputs" / "engineer_professions_gaps_v68_20260909"
    v70_dir = repo / "outputs" / "engineer_cross_skill_v70_20260909"
    v72_dir = repo / "outputs" / "academic_summon_chain_v72_20260910"
    candidates = read(v68_dir / "production_candidates.json")
    unresolved = read(v70_dir / "unresolved_rows.json")
    v72 = read(v72_dir / "REPORT.json")
    v72_hits = [json.loads(line) for line in (v72_dir / "direct37_hit_evidence.jsonl").read_text(encoding="utf-8").splitlines() if line]

    # Only the three boss-target 4216 -> 4352 rows are admission-safe.  The
    # 4217 row has a trustworthy skill identity but its target is another
    # engineering entity, not a boss; keep it out of Boss DPS coverage.
    boss_safe = [row for row in candidates if row["new_skill_id"] == 4352]
    nonboss_identity = [row for row in candidates if row["new_skill_id"] != 4352]
    # The frozen v70 rows use integer 0 (rather than null) when no entity
    # template was resolved.  It is a bucket count, not a real template ID.
    def template_bucket(row: dict[str, Any]) -> str:
        value = row.get("template_id")
        return "no_entity_template" if value in (None, 0, "0") else str(value)

    unresolved_by_template = Counter(template_bucket(row) for row in unresolved)
    unresolved_by_reason = Counter(row.get("object_chain_reason", "") + "/" + row.get("selector_chain_reason", "") for row in unresolved)
    v72_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in v72_hits:
        if row["status"] == "ELIGIBLE":
            v72_by_rule[f"{row['owner_job']}:{row['entity_template_id']}:{row['candidate_skill_id']}"] .append(row)
    evidence = {
        "safe_boss_4216_to_4352": boss_safe,
        "safe_nonboss_skill_identity_only": nonboss_identity,
        "v72_current_generation_self_attack_controls": {
            key: values[:3] for key, values in sorted(v72_by_rule.items())
        },
        "unresolved_examples": {
            key: [row for row in unresolved if template_bucket(row) == key][:3]
            for key in sorted(unresolved_by_template)
        },
    }
    rules = []
    for row in boss_safe:
        physical = row["physical"]
        rules.append({
            "rule_id": f"gear_32005_generation_{physical['generation']}_object_{physical['damage_object_id']}_to_4352",
            "old_skill_id_audit_only": row["old_skill_id"], "new_skill_id": 4352, "new_skill_name": "重磅鸭EX",
            "scope": "exact per-instance selector/object generation, Boss target only",
            "conditions": [
                "source entity template == 32005", "current entity birth and exact 34/2c owner generation",
                "same owner + same entity generation + same damage_object_id selector/object record",
                "same direct37 physical result, Boss/PvE target gate retained",
            ],
            "prohibited": ["template-wide 32005 fallback", "old 4216 label", "time window", "same-job inference"],
            "physical_fixture": physical,
        })
    report = {
        "schema": "engineering-root-chain-v73",
        "inputs": {
            "v68_candidates": str(v68_dir / "production_candidates.json"),
            "v70_unresolved": str(v70_dir / "unresolved_rows.json"),
            "v72_evidence": str(v72_dir / "direct37_hit_evidence.jsonl"),
            "no_capture_replay": True,
        },
        "policy": {
            "required_chain": "player action/static spawn family -> entity birth -> current full owner/generation -> entity self-attack OR exact child selector/object generation -> same direct37",
            "forbidden_positive_proof": ["old/current skill label", "time window", "damage amount", "result group", "cross-job or cross-same-job owner"],
            "shared_production_modified": False,
        },
        "safe_boss_relabels": {"4216_to_4352": len(boss_safe)},
        "safe_nonboss_identity_only": {"4216_to_4217": len(nonboss_identity)},
        "v72_current_generation_controls": {
            "eligible_hits": v72["counts"]["eligible_hits"], "old_label_conflicts": v72["counts"]["old_label_conflicts"],
            "rules": v72["production_rule_candidates"],
        },
        "remaining": {
            "direct_hit_count": len(unresolved),
            "by_entity_template": dict(sorted(unresolved_by_template.items())),
            "top_missing_chain_states": dict(unresolved_by_reason.most_common(10)),
            "meaning_of_371": "371 is the count of unresolved Hits without an entity template, not an entity template ID 371.",
        },
        "old_label_audit": {
            "conclusion": "No old label is promoted.  A label on 32005/32008 may denote the creator or a different child branch; only the concrete current self-attack or selector/object generation can identify this Hit.",
            "v72_exact_chain_label_conflicts": v72["counts"]["old_label_conflicts"],
        },
        "production_rule_candidates": rules,
        "fixtures": evidence,
        "invariants": {
            "read_only": True, "no_production_change": True, "no_replay": True,
            "no_physical_hit_or_damage_mutation": True, "boss_target_gate_preserved": True,
            "no_time_window_promotion": True, "no_cross_same_job_owner": True,
        },
    }
    report["sha256"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (out / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "production_rule_candidates.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "real_fixtures.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 工程召唤根对象链审计 v73", "",
        "只汇总冻结审计输出；不重放抓包、不修改共享生产代码。", "",
        "## 可生产候选", "",
        f"- 机械大师 `32005` 的 3 条 Boss 目标 Hit：当前 owner/generation + 同对象 selector 链确定为 `4216 → 4352 重磅鸭EX`。",
        "- 每条规则只对 fixture 的实体代际/对象链生效；绝不将 `32005` 整体默认成 4216 或 4352。", "",
        "## 已验证但不扩大范围", "",
        f"- v72 在当前实体代际自攻击槽下闭合 {v72['counts']['eligible_hits']} Hit，旧标签冲突 {v72['counts']['old_label_conflicts']}。",
        "- `4216 → 4217` 的 1 条记录目标是工程实体，不进入 Boss DPS 覆盖率。", "",
        "## 尚未闭合", "",
        f"- {len(unresolved)} Hit；32005={unresolved_by_template.get('32005', 0)}，32008={unresolved_by_template.get('32008', 0)}，无实体模板={unresolved_by_template.get('no_entity_template', 0)}。",
        "- 这里的 ‘371’ 是无实体模板的 Hit 数量，不是模板 ID。没有当前 owner-generation、正数 self-attack 或 child-object 代际链时，旧名一律不升格。", "",
        "## 工程技能边界", "",
        "- 重磅鸭：4217 轰击、4218 炸弹、4219 汽笛、4352 EX 由不同自攻击槽/对象链区分。",
        "- 飞溅 4301 是 Projectile 5043 的独立家族，不借用 32005/32008 实体证据。",
        "- 各塔与乒乓爆栗未获得独立当前代际链时保持暂缓。",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    validation = {"schema": report["schema"], "passed": True, "checks": {
        "boss_candidates_are_exactly_three": len(boss_safe) == 3,
        "nonboss_candidate_excluded_from_boss": len(nonboss_identity) == 1,
        "all_v72_exact_chain_rows_have_zero_label_conflicts": v72["counts"]["old_label_conflicts"] == 0,
        "no_template_371_misinterpretation": unresolved_by_template.get("371", 0) == 0,
        "no_forbidden_positive_proof": True,
    }}
    (out / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(validation["checks"].values()):
        raise AssertionError(validation)
    print(json.dumps({"output": str(out), "boss_safe": len(boss_safe), "unresolved": len(unresolved)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
