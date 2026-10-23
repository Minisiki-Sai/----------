"""Strict, read-only audit for v68 provisional direct37 candidate rules.

This tool deliberately does not replay captures.  It reads only v68 partial rows
and classifies a candidate as production-safe only if its positive proof is not
derived from the same template/object attribution rule being proposed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


RULES = (
    ("origin_template_5520_to_5204", "origin_template", 5520, 5204,
     "噬魂者 / 幽灵之怒", "parent-lineage-required shared child"),
    ("player_object_template_11075_to_5217", "player_object_template", 11075, 5217,
     "噬魂者 / 野兽之灵", "A90370 player-object registration is not yet a runtime origin ledger"),
    ("origin_template_5709_to_6204", "origin_template", 5709, 6204,
     "影 / 天降雨点", "parent-lineage-required shared child"),
    ("origin_template_5737_to_6204", "origin_template", 5737, 6204,
     "影 / 天降雨点", "parent-lineage-required shared child"),
)

# These would be independent fixed skill anchors.  No label, timing, damage
# amount, result-group, or template-link method is allowed into this set.
INDEPENDENT_METHODS = frozenset({"packet_skill_id", "packet_native_attack_key"})


def _load_rows(partials: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(partials.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(payload.get("rows", ()))
    return rows


def _fixture(row: dict) -> dict:
    return {key: row.get(key) for key in (
        "capture", "capture_sha256", "physical_id", "packet_serial", "record_offset",
        "timestamp_ms", "actor_token", "owner_actor_id", "wire_source_actor_id",
        "damage_object_id", "origin_template", "player_object_template",
        "origin_generation", "player_object_generation", "object_generation_current",
        "method", "deterministic", "independent_deterministic", "skill_id", "skill_name",
    )}


def _audit_rule(rows: list[dict], name: str, field: str, template: int,
                expected_skill: int, display: str, reason: str) -> tuple[dict, list[dict]]:
    matched = [r for r in rows if r.get(field) == template and r.get("object_generation_current")]
    methods = Counter(str(r.get("method", "")) for r in matched)
    skills = Counter(r.get("skill_id") for r in matched)
    independent = [r for r in matched if r.get("method") in INDEPENDENT_METHODS]
    independent_skills = Counter(r.get("skill_id") for r in independent)
    is_circular = bool(matched) and not independent

    # A strict gate needs an evidence field that v68 never exported.  Do not
    # infer it from the current decoder's `deterministic` bit because that bit
    # is itself produced by damage_object_projectile_link.
    required = (
        "same-owner exact parent_damage_object_id/root instance generation for shared children"
        if template in {5520, 5709, 5737}
        else "root-self A90370 creation record carried into a current-generation player-object ledger"
    )
    result = {
        "rule": name,
        "display": display,
        "proposed_mapping": {field: template, "skill_id": expected_skill},
        "matched_hits": len(matched),
        "skill_ids_observed": {str(k): v for k, v in sorted(skills.items(), key=lambda item: str(item[0]))},
        "methods": dict(sorted(methods.items())),
        "decoder_marked_deterministic": sum(bool(r.get("deterministic")) for r in matched),
        "independent_fixed_skill_anchor_hits": len(independent),
        "independent_fixed_skill_anchor_skills": {str(k): v for k, v in sorted(independent_skills.items(), key=lambda item: str(item[0]))},
        "strict_verdict": "WITHHOLD",
        "why_withheld": (
            "All apparent positive rows are already attributed by the object/template chain under audit; "
            "none is an independent fixed packet-skill/native-attack anchor. " + reason + "."
            if is_circular else "Independent proof requires manual review."
        ),
        "required_wire_evidence": required,
        "forbidden_positive_evidence": ["old/current skill label", "time window", "damage amount", "result group", "same-job cast"],
        "allowed_use_now": "diagnostic display only; do not reclassify, relabel, or count as recovered Hit coverage",
    }
    # one deterministic and one non-deterministic fixture where possible.
    ordered = sorted(matched, key=lambda r: (not bool(r.get("deterministic")), r.get("capture", ""), r.get("packet_serial", 0)))
    return result, [_fixture(r) for r in ordered[:3]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = (args.output or repo / "outputs" / "under95_focus_v68_20260909").resolve()
    partials = output / "partials"
    rows = _load_rows(partials)
    results, fixtures = [], {}
    for rule in RULES:
        result, samples = _audit_rule(rows, *rule)
        results.append(result)
        fixtures[result["rule"]] = samples

    source_guards = {
        "damage_object_origins.py": {
            "parent_lineage_required_templates": [2023, 3021, 5520, 5709, 5711, 5737],
            "shared_direct_skill_display_only": {"5520": [5204], "5709": [6212], "5737": [6212]},
            "key_locations": ["lines 675-696", "lines 1579-1585", "lines 2031-2058"],
        },
        "generated_attack_instance_specs_v576.py": {
            "static_family_candidates": {"5520": 5204, "11075": 5217, "5709": 6204, "5737": 6204},
            "child_edges": {"5508": [5520], "5708": [5709, 5737], "11075": [11076]},
            "key_locations": ["lines 199-212", "lines 238-239", "lines 305-331"],
        },
        "tests": {
            "test_damage_object_origins.py": "lines 776-786: template 5520 remains wholly provisional",
            "test_damage_object_server_parent_registrations.py": "lines 597-601: shared fixed context/recent cast is display only, not typed proof",
        },
    }
    production = {
        "schema": "v69-strict-provisional-gate",
        "decision": "NO_NEW_PRODUCTION_MAPPING",
        "safe_rules": [],
        "withheld_rules": [r["rule"] for r in results],
        "minimal_safe_code_patch_recommendation": [
            "Export parent_damage_object_id, parent_lineage_exact, parent_qualified_subtype, root object id and generation into audit rows before evaluating 5520/5709/5737.",
            "Add a current-generation PlayerDamageObjectLedger fed only by root-self A90370 registrations; key it by full owner ID + damage_object_id + generation and invalidate on replacement or scene reset.",
            "Permit a player-object config mapping only after an independent fixed packet skill/native attack key anchors the same config row; do not use labels, time, amounts, result groups, or same-job casts.",
            "Keep the current parent-lineage gate for 5520/5709/5737. A static template table can narrow candidates but cannot become positive proof by itself.",
        ],
        "invariants": {
            "no_capture_replay": True,
            "no_shared_production_modification": True,
            "no_time_window_promotion": True,
            "no_cross_same_job_promotion": True,
            "no_damage_admission_change": True,
            "no_old_skill_label_as_positive_proof": True,
        },
    }
    report = {
        "schema": "v69-strict-provisional-gate",
        "input": {"partial_directory": str(partials), "row_count": len(rows), "partial_file_count": len(list(partials.glob('*.json')))},
        "strict_positive_proof_methods": sorted(INDEPENDENT_METHODS),
        "source_guard_audit": source_guards,
        "rules": results,
        "production_decision": production,
    }
    report["sha256"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "production_candidates.json").write_text(json.dumps(production, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "fixtures.json").write_text(json.dumps({"schema": report["schema"], "fixtures": fixtures}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# v69 严格候选规则审计", "",
        f"仅读取 `partials/*.json`：{len(rows):,} 条 direct37 行；未重放抓包，未修改生产代码。", "",
        "## 结论", "",
        "四条候选规则均 **暂缓生产化**。分片中所有看似正向的记录都已经由正在被审计的对象/模板链标注，不能循环地证明该链正确。", "",
        "| 候选 | 命中 Hit | 当前方法 | 独立固定技能锚点 | 结论 |",
        "|---|---:|---|---:|---|",
    ]
    for item in results:
        lines.append(f"| {item['rule']} | {item['matched_hits']} | {', '.join(item['methods'])} | {item['independent_fixed_skill_anchor_hits']} | {item['strict_verdict']} |")
    lines += [
        "", "## 为什么当前的 `deterministic` 不足以放行", "",
        "这批行的 `deterministic` 来自 `damage_object_projectile_link`。它是模板链的输出，而不是独立的 UDP 固定技能字段；把它再作为模板链的证据，会把同职业错误归因固化。", "",
        "`5520`、`5709`、`5737` 已被生产代码列为必须具有父代谱系的共享子模板；现有测试也明确禁止用固定上下文或临近施放充当类型证明。`11075` 则需要先把根自有 A90370 注册转为带代际的运行时账本。", "",
        "## 最小安全改动建议（不在本次实施）", "",
        "1. 审计输出增加父对象 ID、完整父谱系、根对象 ID 与代际；先验证，再决定是否放行共享子模板。", 
        "2. 为根自有 A90370 建立按 `完整 owner + object id + generation` 索引的玩家攻击对象账本，并在替换/场景切换时失效。", 
        "3. 只有同一配置行存在独立固定技能/原生攻击键锚点后，才可以把该配置行加入确定规则。", 
        "", "## 不变量", "",
        "- 不以旧技能名、时间窗、伤害数值、结果组或同职业施放作正向证明。", 
        "- 不改变物理 Hit 准入、总伤害、重复去重、跨同职业隔离或当前生产归因。",
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "row_count": len(rows), "decision": production["decision"], "rules": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
