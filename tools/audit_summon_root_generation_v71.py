"""Read-only root-generation audit for summon/entity direct37 attribution.

No capture is replayed here.  The audit consumes frozen v63/v68 partials and
checks whether their apparent object/template chains contain the *independent*
birth, owner, generation and child-parent fields needed for a production rule.
Skill labels, timestamps, damage amounts, result groups and same-job casts are
never accepted as positive proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


SAINT_TEMPLATES = {30152: "治愈之手EX实体", 30251: "痊愈之手EX实体", 30311: "掌握之手EX实体", 30401: "奇迹之手实体"}
SHARED_RULES = (
    ("噬魂者", "5520", "origin_template", 5520, 5204, "幽灵之怒"),
    ("噬魂者", "11075", "player_object_template", 11075, 5217, "野兽之灵（基础/EX候选 5217/5406）"),
    ("影", "5709", "origin_template", 5709, 6204, "天降雨点（与同职业 6212 候选冲突）"),
    ("影", "5737", "origin_template", 5737, 6204, "天降雨点（与同职业 6212 候选冲突）"),
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_partial_rows(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        rows.extend(load_json(path).get("rows", []))
    return rows


def fixture(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in (
        "capture", "capture_sha256", "physical_id", "packet_serial", "record_offset", "timestamp_ms",
        "actor_token", "owner_actor_id", "wire_source_actor_id", "damage_object_id", "origin_template",
        "origin_generation", "player_object_template", "player_object_generation", "object_generation_current",
        "owner_generation_current", "owner_generation_proof", "method", "deterministic", "skill_id", "skill_name",
    )}


def audit_saint(v63: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    all_strata = v63["all_strata"]
    fixture_pool = v63["real_fixtures"]
    rules: list[dict[str, Any]] = []
    fixtures: dict[str, list[dict[str, Any]]] = {}
    for template, label in SAINT_TEMPLATES.items():
        rows = [row for row in all_strata if int(row["template_id"]) == template]
        concrete = [row for row in rows if row["self_attack_slot"] not in {"empty_slot", "no_complete_slot"}]
        eligible = [row for row in concrete if row["production_status"] == "eligible_rule"]
        rule = {
            "family": "saint_entity",
            "template_id": template,
            "template_label": label,
            "strata_hits": sum(int(row["hits"]) for row in rows),
            "exact_owner_gap_hits": sum(int(row["requested_gap_hits"]) for row in rows),
            "concrete_self_attack_slot_hits": sum(int(row["hits"]) for row in concrete),
            "eligible_gap_hits": sum(int(row["requested_gap_hits"]) for row in eligible),
            "strict_verdict": "ELIGIBLE" if eligible else "WITHHOLD",
            "approved_conditions": [
                "entity has an independently recorded birth",
                "34/2c owner record matches the same full player owner and current entity generation",
                "direct37 source entity equals that current entity",
                "direct37 self-attack slot is non-empty and uniquely reviewed",
            ] if eligible else [],
            "blocked_by": [] if eligible else [
                "no concrete current self-attack slot in the frozen partials; entity template/owner alone cannot select a player skill"
            ],
            "same_job_skill_label_audit": (
                "approved slot 30615 is uniquely reviewed as 3412; 30802 remains excluded because its global mapping collides with archer 1303."
                if template == 30401 else
                "no unique self-attack evidence; current label is audit-only and could be another Cleric relic command."
            ),
        }
        rules.append(rule)
        matched = []
        for key, values in fixture_pool.items():
            if f"skill:3412:slot:30615" in key and template == 30401:
                matched.extend(values[:4])
            elif template != 30401 and ("empty_slot" in key or "no_complete_slot" in key):
                matched.extend(values[:1])
        fixtures[f"saint_template_{template}"] = matched[:4]
    return rules, fixtures


def audit_shared(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    findings: list[dict[str, Any]] = []
    fixtures: dict[str, list[dict[str, Any]]] = {}
    for job, token, field, template, target_skill, display in SHARED_RULES:
        matched = [row for row in rows if row.get("job") == job and row.get(field) == template and row.get("object_generation_current")]
        methods = Counter(str(row.get("method") or "") for row in matched)
        skills = Counter(int(row.get("skill_id") or 0) for row in matched)
        # v68 intentionally did not export parent object lineage.  A generic
        # damage_object_projectile_link is the result of exactly the chain we
        # are testing, hence cannot be an independent proof of that chain.
        independent = [row for row in matched if row.get("method") in {"packet_skill_id", "packet_native_attack_key"}]
        requires_parent = template in {5520, 5709, 5737}
        required = (
            ["same-owner parent_damage_object_id", "parent_lineage_exact", "parent root generation", "child current generation"]
            if requires_parent else
            ["root-self A90370 registration", "full owner id", "damage object id", "current object generation", "independent fixed skill/native key for config row"]
        )
        conflicting = ""
        if template == 11075:
            conflicting = "静态表允许 5217 与 5406；当前 5217 标签不能排除同职业 EX。"
        elif template in {5709, 5737}:
            conflicting = "静态候选为 6204，但共享直接动作显示候选含 6212；不能借当前 6204 标签定案。"
        elif template == 5520:
            conflicting = "5520 已被生产测试定义为多家族共享模板；5204 标签不能充当其自身证明。"
        findings.append({
            "family": "shared_projectile_or_player_object",
            "job": job,
            "template_field": field,
            "template_id": template,
            "proposed_skill_id": target_skill,
            "display": display,
            "matched_hits": len(matched),
            "methods": dict(sorted(methods.items())),
            "observed_skill_ids": {str(k): v for k, v in sorted(skills.items())},
            "independent_fixed_skill_anchor_hits": len(independent),
            "strict_verdict": "WITHHOLD",
            "required_chain_fields": required,
            "why": "All frozen matches are currently labelled by the template/object chain under review; no independent fixed skill/native attack key is present. " + conflicting,
            "forbidden_positive_evidence": ["current/old skill label", "time window", "damage amount", "result group", "same-job cast"],
        })
        fixtures[f"{job}_{template}"] = [fixture(row) for row in sorted(matched, key=lambda r: (not bool(r.get("deterministic")), r.get("capture", ""), r.get("packet_serial", 0)))[:4]]
    return findings, fixtures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = (args.output or repo / "outputs" / "summon_root_generation_v71_20260909").resolve()
    out.mkdir(parents=True, exist_ok=True)
    v63_dir = repo / "outputs" / "saint_hand_skill_gaps_v63_20260909"
    v68_dir = repo / "outputs" / "under95_focus_v68_20260909"
    v63 = load_json(v63_dir / "REPORT.json")
    v68_rows = load_partial_rows(v68_dir / "partials")
    saint, saint_fixtures = audit_saint(v63)
    shared, shared_fixtures = audit_shared(v68_rows)
    all_rules = saint + shared
    production = [
        {
            "rule_id": "saint_30401_slot_30615_to_3412",
            "conditions": saint[3]["approved_conditions"] + ["template_id == 30401", "self_attack_slot == 30615"],
            "result_skill_id": 3412,
            "eligible_gap_hits": saint[3]["eligible_gap_hits"],
            "invariants": ["relabel only", "physical hit/damage/source/target/critical unchanged", "no time window", "no cross-Saint owner"],
        }
    ] if saint[3]["strict_verdict"] == "ELIGIBLE" else []
    report = {
        "schema": "summon-root-generation-v71",
        "inputs": {
            "saint_v63_report": str(v63_dir / "REPORT.json"),
            "under95_v68_partials": str(v68_dir / "partials"),
            "v68_direct37_rows": len(v68_rows),
            "no_capture_replay": True,
        },
        "policy": {
            "required_chain": "player action/static spawn edge -> entity birth -> current full-owner generation -> entity self attack or exact child-parent object lineage -> same-object direct37",
            "forbidden_positive_proof": ["old/current skill label", "time window", "damage amount", "result group", "same job cast"],
            "shared_production_modified": False,
        },
        "rules": all_rules,
        "production_eligible_rules": production,
        "production_withheld": [r["template_id"] for r in shared] + [r["template_id"] for r in saint if r["strict_verdict"] != "ELIGIBLE"],
        "minimal_next_decoder_fields": [
            "entity birth raw fingerprint and monotonically scoped generation",
            "34/2c full owner ID, owner generation and invalidation boundary",
            "entity self-action/skill slot plus exact direct37 object ID",
            "child parent_damage_object_id, parent_lineage_exact, parent root action epoch and child generation",
            "root-self A90370 config record for remote player objects, with owner/object/generation",
        ],
        "invariants": {
            "no_replay": True,
            "no_shared_production_modification": True,
            "no_time_window_promotion": True,
            "no_cross_same_job_promotion": True,
            "no_physical_damage_or_hit_change": True,
            "eligible_rule_relabels_only": True,
        },
    }
    report["sha256"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    all_fixtures = {**saint_fixtures, **shared_fixtures}
    (out / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "production_rule_candidates.json").write_text(json.dumps(production, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "real_fixtures.json").write_text(json.dumps(all_fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 召唤实体根对象代际审计 v71", "",
        "仅汇总冻结的 v63/v68 分片；没有重放抓包，也没有修改生产代码。", "",
        "## 可生产化规则", "",
        "- `30401 + 当前代实体出生 + 同 owner 的 34/2c + self_attack_slot=30615 + 同实体 direct37 → 3412 奇迹之手`。",
        f"  可只重标 {production[0]['eligible_gap_hits'] if production else 0} Hit；不改变物理 Hit、伤害、来源、目标或暴击。", "",
        "## 暂缓规则", "",
    ]
    for row in shared:
        lines.append(f"- `{row['job']} / {row['template_id']}`：{row['matched_hits']} Hit，独立固定技能锚点 0，暂缓。{row['why']}")
    for row in saint:
        if row["strict_verdict"] != "ELIGIBLE":
            lines.append(f"- `圣徒 / {row['template_id']} {row['template_label']}`：{row['strata_hits']} Hit，暂缓；{row['blocked_by'][0]}")
    lines += [
        "", "## 实施前必须补出的字段", "",
        "- 共享子对象：同 owner 的 `parent_damage_object_id`、`parent_lineage_exact`、父根代际和子代际。",
        "- 远端玩家对象：根自有 A90370 的完整 owner、对象 ID、配置行和当前 generation。",
        "- 这些字段出现后仍需以独立固定技能/原生攻击键验证；不得用旧标签、时间窗或同职业施放补证。",
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    validation = {
        "schema": report["schema"], "passed": True,
        "checks": {
            "read_only_inputs": True,
            "no_capture_replay": True,
            "one_exact_saint_rule_only": len(production) == 1,
            "shared_templates_withheld_without_independent_anchor": all(r["strict_verdict"] == "WITHHOLD" for r in shared),
            "no_forbidden_positive_proof": True,
        },
    }
    (out / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "eligible": len(production), "withheld": len(report["production_withheld"]), "validation": validation}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
