#!/usr/bin/env python3
"""v72 shared entity / projectile attribution audit.

This is intentionally an evidence-only audit.  It consumes frozen reports from
the earlier packet work and does *not* replay captures or import any runtime
module.  In particular, a present object/template is not promoted to a skill
unless the report can also show an independent native slot, or a complete
current-generation chain with no known conflicting canonical family.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def find_rule(rules: list[dict[str, Any]], template: int) -> dict[str, Any]:
    for row in rules:
        if row.get("template_id") == template:
            return row
    raise KeyError(template)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build v72 shared-entity attribution audit")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "shared_entity_skill_gaps_v72_20260910",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    paths = {
        "v71": ROOT / "outputs" / "summon_root_generation_v71_20260909" / "REPORT.json",
        "v71_fixtures": ROOT / "outputs" / "summon_root_generation_v71_20260909" / "real_fixtures.json",
        "registration": ROOT / "outputs" / "audit_remaining_registration_packet_20260907" / "handoff.json",
        "raw_registration": ROOT / "outputs" / "audit_remaining_registration_ffff_raw_20260907" / "proofs.json",
        "grass": ROOT / "outputs" / "magic_grass_census_v38_20260908" / "projectile_review.json",
        "grass_scope": ROOT / "outputs" / "magic_grass_scope_v38_20260908" / "REPORT.md",
        "dark": ROOT / "outputs" / "dark_shaman_gaps_v65_20260909" / "report.json",
    }
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise SystemExit(f"missing audit inputs: {', '.join(missing)}")

    v71 = load(paths["v71"])
    v71_fixtures = load(paths["v71_fixtures"])
    registration = load(paths["registration"])
    raw_registration = load(paths["raw_registration"])
    grass = load(paths["grass"])
    dark = load(paths["dark"])
    rules = v71["rules"]

    shadow_status = registration.get("shadow_parent_status", {})
    representatives = registration.get("representatives", [])
    shadow_reps = [r for r in representatives if r.get("binding", {}).get("child_template") in (5709, 5737)]
    grass_routes = grass.get("positive_projectile_routes", [])
    grass_creators = grass.get("creators", [])

    target_rows: list[dict[str, Any]] = []
    for template in (5520, 11075, 5709, 5737):
        old = find_rule(rules, template)
        row = {
            "job": old.get("job"),
            "template_id": template,
            "display": old.get("display"),
            "observed_hits": old.get("matched_hits"),
            "strict_closeable_hits": 0,
            "strict_correctable_hits": 0,
            "strict_remaining_hits": old.get("matched_hits"),
            "old_label_counts": old.get("observed_skill_ids"),
            "old_label_is_positive_proof": False,
            "independent_fixed_skill_anchor_hits": old.get("independent_fixed_skill_anchor_hits"),
            "base_verdict": old.get("strict_verdict"),
            "production_action": "WITHHOLD",
            "reason": old.get("why"),
            "required_missing_proof": old.get("required_chain_fields"),
        }
        if template in (5709, 5737):
            row.update(
                {
                    "current_generation_parent_chain": {
                        "exact_generation_ACT_supported": shadow_status.get("exact_generation_ACT_supported"),
                        "template_generations": 300 if template == 5709 else 303,
                        "static_parent_edge": "5708 -> child template",
                        "same_object_direct37": True,
                        "independent_native_slot": False,
                    },
                    "counterexample_guard": "3023 -> 3021 has exact parent lineage but child fixed slot 2207 conflicts with parent canonical 2353; parent lineage alone is never a skill assignment.",
                    "verdict": "The entity/object lineage is real and current, but it proves the child belongs to the parent object family, not whether the final player skill is 6204 rather than 6212.",
                }
            )
        elif template == 11075:
            row.update(
                {
                    "static_family": {"base_skill": 5217, "ex_skill": 5406, "shared_base_skill": 5217},
                    "verdict": "A root player object with template 11075 cannot choose base versus EX without a native skill/config slot from the same current object generation.",
                }
            )
        else:
            row.update(
                {
                    "verdict": "Template 5520 is intentionally known as shared. Its existing 5204 label is circular evidence and remains excluded.",
                }
            )
        target_rows.append(row)

    magic_rows = []
    for template in (32012, 32017, 32018):
        routes = [r for r in grass_routes if any(c.get("template") == template for c in r.get("creates", []))]
        creators = [c for c in grass_creators if c.get("template") == template]
        if template in (32012, 32017):
            verdict = "WITHHOLD_RUNTIME_PROMOTION"
            reason = (
                "Static resource path proves a Magic Grass candidate (4405): player action -> Projectile -> child ACT -> template. "
                "The retained census is not target-complete and LoveVirus has an unresolved type29 reference, so an arbitrary runtime instance still cannot be promoted without its birth-specific chain."
            )
        else:
            verdict = "WITHHOLD"
            reason = (
                "The inspected positive projectile paths do not reach 32018. It may share Academic Slime behavior, "
                "but family behavior/AI is not evidence for the player skill that created a particular runtime instance."
            )
        magic_rows.append(
            {
                "job": "药剂师",
                "template_id": template,
                "direct_skill": 4405 if template in (32012, 32017) else None,
                "direct_skill_name": "召唤魔法草" if template in (32012, 32017) else None,
                "static_routes": routes,
                "creator_records": creators,
                "runtime_verdict": verdict,
                "strict_closeable_hits": 0,
                "strict_correctable_hits": 0,
                "strict_remaining_hits": "not enumerated by the retained static-only census",
                "reason": reason,
                "required_runtime_proof": [
                    "protocol-admitted entity birth for this instance",
                    "same full owner and current entity generation",
                    "exact creation route / Projectile binding",
                    "same entity/object direct37",
                ],
            }
        )

    projection = dark.get("projection", {})
    dark_section = {
        "job": "黑暗萨满",
        "capture": dark.get("scope", {}).get("capture"),
        "baseline_direct_hits": projection.get("baseline_total_direct_hits"),
        "baseline_deterministic_hits": projection.get("baseline_deterministic_hits"),
        "gap_hits": projection.get("baseline_total_direct_hits", 0) - projection.get("baseline_deterministic_hits", 0),
        "v65_added_or_corrected_hits": projection.get("new_deterministic_or_corrected_hits"),
        "verdict": "No promotion. Several major skills have no typed15 Projectile at all; current direct37 generation records and old labels do not provide a native slot or non-circular object chain.",
        "evidence_contract": dark.get("evidence_contract"),
    }

    fixtures = {
        "policy": {
            "accept": "static action + birth + same current owner/object generation + self action or exact lineage + same-object direct37",
            "reject_as_positive_proof": ["old label", "time window", "damage amount", "result group", "same-job cast"],
        },
        "shadow_parent_examples": shadow_reps[:4],
        "soul_static": raw_registration.get("soul_static", {}),
        "magic_grass_routes": grass_routes,
        "magic_grass_unresolved": grass.get("remaining", []),
        "dark_shaman_conservation": dark.get("physical_conservation", {}),
        "v71_fixture_excerpt": {
            key: value[:2] if isinstance(value, list) else value
            for key, value in v71_fixtures.items()
            if key in ("shared_5520", "shared_11075", "shared_5709", "shared_5737")
        },
    }

    report = {
        "schema": "shared-entity-skill-gaps-v72",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Dark Shaman, Soul Eater, Shadow, Physician shared entity/projectile review",
        "mode": "frozen-evidence audit; no capture replay; no runtime/production import",
        "production_changed": False,
        "policy": fixtures["policy"],
        "shared_templates": target_rows,
        "magic_grass": magic_rows,
        "dark_shaman": dark_section,
        "registration_protocol": {
            "parent_record_layouts": [112, 119, 122],
            "resource_record_layouts": [107, 114, 117],
            "shadow_current_generations": shadow_status.get("exact_generation_ACT_supported"),
            "shadow_generation_breakdown": {"5709": 300, "5737": 303},
            "shadow_raw_direct37": registration.get("shadow_parent_raw_hits"),
            "parent_inheritance_is_universal": False,
        },
        "conservation": {
            "new_events_replayed": False,
            "physical_hit_or_damage_changed": False,
            "source_v71_invariants": v71.get("invariants"),
            "source_dark_invariants": dark.get("physical_conservation", {}),
        },
        "inputs": {key: sha(path) for key, path in paths.items()},
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    lines = [
        "# 共享实体 / Projectile 归因审计 v72",
        "",
        "本次只读取既有冻结审计证据；没有重放抓包、没有导入运行时代码、没有修改生产归因或评分。",
        "",
        "## 结论",
        "",
        "- **不新增生产映射。** `5520`、`11075`、`5709`、`5737` 都不能只凭旧标签或模板存在安全地写入技能归因；本轮严格可闭合/可纠错 Hit 都是 0。",
        "- `5709/5737` 的父对象链已被确认是真实且当前代际，但这是对象归属证据，不能替代最终技能键；`3023→3021` 的固定槽反例证明不能做全局父技能继承。",
        "- 药剂师 `32012/32017` 有静态的“召唤魔法草(4405)→Projectile→实体”正向路径；缺少逐实例出生关联，故维持审计候选而不上线。`32018` 无此正向路径，明确暂缓。",
        "- 黑暗萨满 v65 的 284 个缺口仍不提升：许多技能没有 Projectile，且现有对象记录不能独立选出技能。",
        "",
        "## 共享模板逐项",
        "",
        "|职业|模板|观察 Hit|可闭合/纠错|已确认对象事实|最终技能结论|",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in target_rows:
        fact = "当前父代际/ACT 边" if row["template_id"] in (5709, 5737) else "静态候选/对象存在"
        lines.append(f"|{row['job']}|{row['template_id']}|{row['observed_hits']}|{row['strict_closeable_hits']}/{row['strict_correctable_hits']}|{fact}|暂缓：{row['verdict']}|")
    lines += [
        "",
        "## 药剂师实体",
        "",
        "|模板|静态候选|运行时结论|",
        "|---:|---|---|",
    ]
    for row in magic_rows:
        skill = row["direct_skill_name"] or "未证实"
        lines.append(f"|{row['template_id']}|{skill}|{row['runtime_verdict']}|" )
    lines += [
        "",
        "## 守恒与边界",
        "",
        "本报告没有重新产生或变更任何物理伤害事件，因此不会改变 Hit 数、伤害、角色归属、目标、暴击、时间轴或评分。后续若要上线，必须逐实例满足完整链，并额外验证独立技能槽或无冲突的最终技能键。",
    ]

    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "real_fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    validation = {
        "schema": "shared-entity-skill-gaps-v72-validation",
        "all_inputs_present": True,
        "production_changed": False,
        "no_replay": True,
        "withheld_shared_templates": [5520, 11075, 5709, 5737, 32018],
        "no_new_production_rules": True,
        "report_sha256": report["report_sha256"],
    }
    (output / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
