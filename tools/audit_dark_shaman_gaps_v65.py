"""Offline audit of Black Shaman direct-Hit attribution gaps.

The audit is deliberately stricter than the production fallback labels.  A
Hit may be closed or corrected only by a native fixed skill slot, or by an
exact same-owner damage-object generation that is still inside the audited
Projectile lifetime.  Timestamps are used only to validate generation
lifetime and to locate the physical direct37 row; proximity to a cast is
never skill evidence.  The script does not modify production files.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict, deque
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _raw_damage_invariants,
    _scope_reason,
    _strict_classification,
)
from tools.audit_magic_archer_skill_gaps_v63 import (  # noqa: E402
    PRIMARY_09_SUBTYPES,
    PARENTS_BY_CHILD,
    RawHit,
    SKILL_CATALOG,
    Typed09,
    _decode_events,
    _event_key,
    _exact_parent_row,
    _mapping_for_template,
    _raw_hit_key,
    _scan_wire,
    _sha,
    _skill_name,
    _valid_fixed_skill,
)


SCHEMA = "dark-shaman-skill-gaps-v65"
DEFAULT_CAPTURE = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
    r"\0813硬核火舞.dpscap"
)
DEFAULT_BASELINE = ROOT / "outputs/direct_damage_v64_incremental_20260909/profession_direct_damage_report.json"
DEFAULT_OUTPUT = ROOT / "outputs/dark_shaman_gaps_v65_20260909"
TARGET_JOB = "黑暗萨满"
TARGET_SKILLS = {
    -1: "待识别伤害",
    5008: "微旋",
    5012: "鬼魂踢",
    5014: "傀儡之魂",
    5201: "幽灵之爪",
    5203: "狂暴之爪",
    5204: "幽灵之怒",
    5205: "束缚之爪",
    5206: "闪电之爪",
    5207: "憎恶之境",
    5208: "复仇之手",
    5210: "幽灵复仇者",
    5211: "科兹污点",
    5218: "怨恨之像",
    5303: "混沌阵",
}
EXPECTED_GAPS = {
    -1: 13, 5008: 2, 5012: 1, 5014: 2, 5201: 13,
    5203: 46, 5204: 2, 5205: 54, 5206: 1, 5207: 42,
    5208: 28, 5210: 9, 5211: 19, 5218: 18, 5303: 34,
}
SPEC_BY_TEMPLATE = {int(row.projectile_template_id): row for row in _DAMAGE_OBJECT_SPECS}
STATIC_VALIDATED = (
    frozenset(CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS)
    | frozenset(PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS)
)

# Parsed from the unpacked SkillLevel ProcessParam -> Kali ACT graph.  The
# listed type-15 templates are the complete Projectile-create signals in the
# corresponding actions; an empty tuple is important negative evidence, not a
# replacement for packet evidence.
CLIENT_ACT_GRAPH = {
    5203: {
        "actions": ["Skill_RampageClaw"],
        "type15_projectiles": [],
    },
    5205: {
        "actions": ["Skill_ChainClaw"],
        "type15_projectiles": [],
    },
    5207: {
        "actions": ["Skill_ResentArea", "Skill_ResentPVEArea"],
        "type15_projectiles": [],
    },
    5208: {
        "actions": ["Skill_RevengeHand"],
        "type15_projectiles": [5511],
    },
    5211: {
        "actions": ["Skill_StigmaOfCuse", "Skill_StigmaOfCuse_Lv2", "Skill_StigmaOfCuse_Lv3"],
        "type15_projectiles": [5518],
    },
    5218: {
        "actions": ["Skill_GrudgeFormation"],
        "type15_projectiles": [5508],
    },
    5303: {
        "actions": [
            "Skill_ChaosFormation_Start", "Skill_ChaosFormation_Loop",
            "Skill_ChaosFormation_End", "Skill_ChaosFormation_Mission_Start",
        ],
        "type15_projectiles": [11045],
        "note": "11045 occurs only on the Mission start branch; regular start/loop/end has no type-15 create signal",
    },
}


def _int_skill(value: object) -> int:
    try:
        return int(str(value or "0"), 0)
    except ValueError:
        return -1


def _find_physical_hit(
    event: CombatEvent,
    hit_queues: dict[tuple[int, ...], deque[RawHit]],
) -> RawHit | None:
    key = _event_key(event)
    queue = hit_queues.get(key)
    if queue:
        return queue.popleft()
    # Millisecond rounding is a locator only.  Every physical field remains
    # exact and the adjacent timestamp is never used to infer a skill.
    for delta in (-1, 1):
        queue = hit_queues.get((key[0] + delta, *key[1:]))
        if queue:
            return queue.popleft()
    return None


def _latest_generation(
    hit: RawHit,
    rows_by_key: dict[tuple[int, int], list[Typed09]],
    positions: dict[tuple[int, int], list[tuple[int, int]]],
) -> Typed09 | None:
    key = (hit.source_actor_id, hit.object_id)
    rows = rows_by_key.get(key, ())
    if not rows:
        return None
    index = bisect.bisect_right(positions[key], hit.position) - 1
    return rows[index] if index >= 0 else None


def _object_decision(
    hit: RawHit,
    row: Typed09 | None,
    rows_by_key: dict[tuple[int, int], list[Typed09]],
    positions: dict[tuple[int, int], list[tuple[int, int]]],
) -> tuple[str, int, str, dict[str, Any]]:
    fixed_skill, fixed_offset = _valid_fixed_skill(hit)
    if fixed_skill:
        return "fixed_skill_slot", fixed_skill, f"direct37 +{fixed_offset}", {}
    if row is None:
        return "no_pre_hit_generation", 0, "no complete typed09/other accepted creation before Hit", {}

    spec = SPEC_BY_TEMPLATE.get(row.template_id)
    age_seconds = (hit.timestamp_us - row.timestamp_us) / 1_000_000.0
    metadata = {
        "template_id": row.template_id,
        "generation_age_seconds": round(age_seconds, 6),
        "accepted_by_current_decoder": row.accepted_by_current_decoder,
        "source_layout": row.source_layout,
        "configured_lifetime_seconds": (spec.lifetime_seconds if spec else None),
    }
    if age_seconds < 0:
        return "future_generation", 0, "object create follows Hit", metadata
    if spec is None:
        return "unmapped_template", 0, "object template absent from audited static table", metadata
    if age_seconds > float(spec.lifetime_seconds):
        return "expired_generation", 0, "prior object generation is outside audited lifetime", metadata
    if row.template_id not in STATIC_VALIDATED:
        return "provisional_template", 0, "static mapping lacks independent client/packet validation", metadata
    if not (row.accepted_by_current_decoder or row.subtype in PRIMARY_09_SUBTYPES):
        return "unaccepted_creation_layout", 0, "complete row layout is not protocol-admitted", metadata

    canonical, aliases = _mapping_for_template(row.template_id)
    if not canonical or canonical == 0xFFFFFFFF:
        return "unmapped_template", 0, "template has no named canonical skill", metadata
    catalog_row = SKILL_CATALOG.get(canonical)
    if catalog_row and str(catalog_row[1]).strip() != TARGET_JOB:
        metadata["canonical_skill_job"] = str(catalog_row[1])
        return (
            "cross_job_template_conflict", 0,
            "same-owner object row resolves to a skill from another profession; fail closed",
            metadata,
        )
    if row.template_id in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS:
        parent = _exact_parent_row(row, rows_by_key, positions)
        allowed = PARENTS_BY_CHILD.get(row.template_id, frozenset())
        parent_canonical, parent_aliases = (
            _mapping_for_template(parent.template_id) if parent else (0, frozenset())
        )
        metadata["parent"] = asdict(parent) if parent else None
        if not (
            parent is not None
            and parent.template_id in allowed
            and parent.accepted_by_current_decoder
            and parent_canonical == canonical
            and aliases.intersection(parent_aliases)
        ):
            return "parent_lineage_incomplete", 0, "shared child template lacks exact validated parent generation", metadata
    return "live_exact_object_chain", canonical, "same-owner live generation + validated Projectile template", metadata


def _fixture(
    event: CombatEvent,
    hit: RawHit,
    row: Typed09 | None,
    decision: str,
    proposed: int,
    reason: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "current_skill_id": _int_skill(event.skill_id),
        "current_skill_name": str(event.skill_name or ""),
        "current_method": str(event.attribution_method or ""),
        "decision": decision,
        "proposed_skill_id": proposed,
        "proposed_skill_name": _skill_name(proposed) if proposed else "",
        "reason": reason,
        "metadata": metadata,
        "physical_direct37": asdict(hit),
        "object_generation": asdict(row) if row else None,
    }


def _baseline_info(baseline: dict[str, Any], capture: Path) -> dict[str, Any]:
    summary = next(row for row in baseline["summary"] if row["职业"] == TARGET_JOB)
    manifest = next(
        row for row in baseline["capture_manifest"]
        if Path(row["capture_path"]).resolve() == capture.resolve()
    )
    return {"summary": summary, "manifest": manifest}


def audit(capture: Path, baseline_path: Path) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_info = _baseline_info(baseline, capture)
    decoded = _decode_events(capture)
    wire = _scan_wire(capture)

    hit_queues: dict[tuple[int, ...], deque[RawHit]] = defaultdict(deque)
    for hit in wire["hits"]:
        hit_queues[_raw_hit_key(hit, decoded["origin_us"])].append(hit)

    rows_by_key: dict[tuple[int, int], list[Typed09]] = defaultdict(list)
    for row in wire["objects"]:
        rows_by_key[(row.owner_actor_id, row.object_id)].append(row)
    positions: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for key, rows in rows_by_key.items():
        rows.sort(key=lambda item: item.position)
        positions[key] = [item.position for item in rows]

    per_skill: dict[int, Counter[str]] = defaultdict(Counter)
    proposals: Counter[tuple[int, int]] = Counter()
    fixtures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fixture_keys: set[tuple[int, str, int]] = set()
    shadow: dict[int, tuple[str, str]] = {}
    gap_rows: list[tuple[CombatEvent, RawHit]] = []
    unmatched = 0

    # Deterministic siblings are used only to disprove result_group as a skill
    # key.  They never supply a positive label to a gap row.
    deterministic_by_group: dict[int, set[int]] = defaultdict(set)
    all_job_rows: list[CombatEvent] = []
    for event in decoded["events"]:
        if not isinstance(event, CombatEvent) or str(event.job or "").strip() != TARGET_JOB:
            continue
        if _scope_reason(event) != "included_positive_direct_hit":
            continue
        all_job_rows.append(event)
        if _strict_classification(event) == "deterministic":
            deterministic_by_group[int(event.wire_result_group_id or 0)].add(_int_skill(event.skill_id))

    for event in all_job_rows:
        if _strict_classification(event) == "deterministic":
            continue
        current = _int_skill(event.skill_id)
        if current == 0:
            current = -1
        hit = _find_physical_hit(event, hit_queues)
        per_skill[current]["gap_hits"] += 1
        per_skill[current][f"old_method:{event.attribution_method or 'unresolved'}"] += 1
        if hit is None:
            unmatched += 1
            per_skill[current]["unmatched_physical"] += 1
            continue
        gap_rows.append((event, hit))
        row = _latest_generation(hit, rows_by_key, positions)
        decision, proposed, reason, metadata = _object_decision(
            hit, row, rows_by_key, positions,
        )
        if decision in {"fixed_skill_slot", "live_exact_object_chain"}:
            category = "closeable_same_skill" if proposed == current else "correctable_to_other_skill"
            per_skill[current][category] += 1
            proposals[(current, proposed)] += 1
            shadow[id(event)] = (str(proposed), _skill_name(proposed))
        else:
            category = decision
            per_skill[current][category] += 1
        key = (current, category, proposed)
        if key not in fixture_keys and len(fixtures[category]) < 20:
            fixture_keys.add(key)
            fixtures[category].append(_fixture(
                event, hit, row, category, proposed, reason, metadata,
            ))

    # Prove a relabel-only shadow cannot change the physical damage multiset.
    shadow_events = [
        replace(event, skill_id=shadow[id(event)][0], skill_name=shadow[id(event)][1])
        if id(event) in shadow else event
        for event in decoded["events"]
    ]
    before = _raw_damage_invariants(decoded["events"])
    after = _raw_damage_invariants(shadow_events)
    if before != after:
        raise AssertionError("shadow relabel changed physical damage invariants")

    group_counter = Counter()
    group_examples: list[dict[str, Any]] = []
    for event, hit in gap_rows:
        siblings = deterministic_by_group.get(hit.result_group_id, set())
        if not siblings:
            continue
        group_counter["gap_hits_sharing_group_with_deterministic_hit"] += 1
        if len(siblings) > 1:
            group_counter["gap_hits_in_mixed_skill_group"] += 1
        if len(group_examples) < 12:
            group_examples.append({
                "result_group_id": hit.result_group_id,
                "gap_current_skill_id": _int_skill(event.skill_id),
                "deterministic_sibling_skill_ids": sorted(siblings),
                "physical_record_sha256": hit.record_sha256,
            })

    skill_rows = []
    for skill in TARGET_SKILLS:
        counts = per_skill[skill]
        closed = int(counts["closeable_same_skill"])
        corrected = int(counts["correctable_to_other_skill"])
        gap = int(counts["gap_hits"])
        skill_rows.append({
            "skill_id": skill,
            "skill_name": TARGET_SKILLS[skill],
            "expected_v64_gap_hits": EXPECTED_GAPS[skill],
            "observed_gap_hits": gap,
            "deterministic_closeable_hits": closed,
            "deterministic_correctable_hits": corrected,
            "still_unclosed_hits": gap - closed - corrected,
            "decision_counts": {
                key: int(value) for key, value in sorted(counts.items())
                if not key.startswith("old_method:") and key != "gap_hits"
            },
            "old_method_counts": {
                key.split(":", 1)[1]: int(value)
                for key, value in sorted(counts.items()) if key.startswith("old_method:")
            },
            "proposed_skill_counts": {
                str(dst): int(value)
                for (src, dst), value in proposals.items() if src == skill
            },
        })

    exact_added = sum(
        row["deterministic_closeable_hits"] + row["deterministic_correctable_hits"]
        for row in skill_rows
    )
    old_total = int(baseline_info["summary"]["总直接Hit"])
    old_deterministic = int(baseline_info["summary"]["确定性已归因"])
    target = 195
    return {
        "schema": SCHEMA,
        "scope": {
            "profession": TARGET_JOB,
            "capture": str(capture),
            "capture_sha256": _sha(capture),
            "fixed_35_baseline": str(baseline_path),
            "shared_production_modified": False,
        },
        "evidence_contract": {
            "accepted_positive_evidence": [
                "unique native skill ID in physical direct37 fixed slots",
                "same full owner ID + same damage_object_id generation, protocol-admitted creation, audited Projectile map, and unexpired lifetime",
                "explicit validated parent generation when a child Projectile is shared",
            ],
            "explicitly_forbidden": [
                "cast/Hit time proximity or a 2.5-second action window",
                "old/current inferred skill label",
                "damage amount or damage ratio",
                "result_group_id as a skill foreign key",
            ],
        },
        "baseline_verification": {
            "capture_sha256_equal": baseline_info["manifest"]["capture_sha256"] == _sha(capture),
            "raw_source_invariants_equal": baseline_info["manifest"]["raw_source_invariants"] == decoded["raw_source_invariants"],
            "aggregated_invariants_equal": baseline_info["manifest"]["aggregated_invariants"] == decoded["aggregated_invariants"],
            "baseline_summary": baseline_info["summary"],
            "observed_gap_hits": sum(row["observed_gap_hits"] for row in skill_rows),
            "unmatched_physical_rows": unmatched,
        },
        "projection": {
            "baseline_total_direct_hits": old_total,
            "baseline_deterministic_hits": old_deterministic,
            "new_deterministic_or_corrected_hits": exact_added,
            "projected_deterministic_hits": old_deterministic + exact_added,
            "projected_coverage_percent": round((old_deterministic + exact_added) * 100 / old_total, 6),
            "requested_minimum_new_hits": target,
            "target_met": exact_added >= target,
            "target_shortfall": max(0, target - exact_added),
            "note": "audit projection only; no production rule was changed",
        },
        "per_skill": skill_rows,
        "proposal_matrix": [
            {"from_skill_id": src, "to_skill_id": dst, "hits": count}
            for (src, dst), count in proposals.most_common()
        ],
        "creation_inventory": {
            "wire_counts": wire["counts"],
            "complete_creation_rows": len(wire["objects"]),
            "creation_source_layouts": dict(Counter(row.source_layout for row in wire["objects"])),
            "creation_subtypes": dict(Counter(str(row.subtype) for row in wire["objects"])),
        },
        "client_static_act_graph": CLIENT_ACT_GRAPH,
        "counterexamples": {
            "result_group_summary": dict(group_counter),
            "result_group_examples": group_examples,
            "interpretation": "a result group may contain multiple deterministic skills, so group membership cannot close a gap",
        },
        "fixtures": fixtures,
        "physical_conservation": {
            "before_shadow_relabel": before,
            "after_shadow_relabel": after,
            "exactly_equal": before == after,
            "raw_source_invariants": decoded["raw_source_invariants"],
            "aggregated_invariants": decoded["aggregated_invariants"],
        },
    }


def render_report(result: dict[str, Any]) -> str:
    p = result["projection"]
    base = result["baseline_verification"]["baseline_summary"]
    lines = [
        "# 黑暗萨满固定 35 份归因缺口审计 v65",
        "",
        "## 结论",
        "",
        f"- 基线：`{base['总直接Hit']}` 个直接 Hit，`{base['确定性已归因']}` 个确定归因，覆盖率 `{base['确定性覆盖率%']:.6f}%`。",
        f"- 本轮严格证据新增/纠错：`{p['new_deterministic_or_corrected_hits']}` Hit；投影覆盖率 `{p['projected_coverage_percent']:.6f}%`。",
        f"- 目标 195 Hit：`{'达到' if p['target_met'] else '未达到'}`；缺口 `{p['target_shortfall']}`。没有用时间、旧标签或伤害数值补数。",
        f"- 物理守恒：`{result['physical_conservation']['exactly_equal']}`；共享生产代码未修改。",
        "",
        "## 逐技能结果",
        "",
        "|技能|v64 缺口|本轮可闭合|本轮可纠错|仍未闭合|",
        "|---|---:|---:|---:|---:|",
    ]
    for row in result["per_skill"]:
        lines.append(
            f"|{row['skill_name']} ({row['skill_id']})|{row['observed_gap_hits']}|"
            f"{row['deterministic_closeable_hits']}|{row['deterministic_correctable_hits']}|"
            f"{row['still_unclosed_hits']}|"
        )
    lines.extend([
        "",
        "## 为什么大桶仍不能强行闭合",
        "",
        "- 完整 typed09 以及当前解码器认可的其他创建格式均已扫描；没有前置创建记录的对象不能反推 Projectile 模板。",
        "- 狂暴之爪、束缚之爪、憎恶之境的客户端 ACT 动作没有 type-15 Projectile 创建信号；混沌阵仅 Mission 起手分支创建 11045。该静态结果否定了“所有直接技能都必经可见 Projectile”的假设。",
        "- `result_group_id` 中存在多个已确定技能并存的物理反例，不能作为技能外键。",
        "- 2.5 秒仅是部分客户端动作到子对象创建的谱系上限，本报告完全不把它用于正向归因。",
        "",
        "## 验收",
        "",
        f"- 抓包 SHA256 一致：`{result['baseline_verification']['capture_sha256_equal']}`",
        f"- 原始事件守恒：`{result['baseline_verification']['raw_source_invariants_equal']}`",
        f"- 聚合事件守恒：`{result['baseline_verification']['aggregated_invariants_equal']}`",
        f"- 阴影重标前后物理多重集一致：`{result['physical_conservation']['exactly_equal']}`",
        "",
        "完整 direct37、创建记录十六进制、决策计数和反例见 `report.json`。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(args.capture.resolve(), args.baseline.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (args.output / "REPORT.md").write_text(render_report(result), encoding="utf-8")
    print(json.dumps(result["projection"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
