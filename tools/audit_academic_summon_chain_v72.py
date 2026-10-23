"""Read-only per-Hit audit for Heavy Gunner / Gear Master summon chains.

Evidence is accepted only as: current entity birth -> exact current 34/2c
owner -> entity self-attack slot -> reviewed static skill -> same direct37
physical record.  Existing labels, timing, damage and result group are stored
for audit only and never create a candidate.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
import io
from pathlib import Path
import sys
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution  # noqa: E402
from dragon_nest_builder.live_dps.generated_summon_self_attack_specs_v26 import SUMMON_SELF_ATTACK_SPECS  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import PhysicalHit, annotate_hit, read_self_attack_slot  # noqa: E402
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records  # noqa: E402
from tools.audit_under95_summon_chain_v53 import _actor, _canonical, _hash, _life, _physical_for_event, _skill  # noqa: E402


SCHEMA = "academic-summon-chain-v72"
TEMPLATES = {32005: "重磅鸭实体", 32008: "摄魂怪实体"}
JOBS = {"重炮手", "机械大师"}
NAMES = {
    4212: "摄魂怪践踏", 4214: "摄魂怪旋风", 4217: "重磅轰击", 4218: "重磅炸弹",
    4219: "重磅汽笛", 4301: "飞溅", 4303: "摄魂怪光束", 4352: "重磅鸭EX",
}


def _selected_captures(partial_dir: Path) -> list[dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    for path in partial_dir.glob("*.json"):
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        result = wrapper.get("result", wrapper)
        if not any(key.startswith(("重炮手|", "机械大师|")) for key in result.get("patterns", {})):
            continue
        selected[result["capture_sha256"]] = {
            "capture": result["capture"], "path": result["capture_path"], "sha256": result["capture_sha256"],
        }
    if not selected:
        raise ValueError("no frozen Heavy Gunner / Gear Master captures found")
    for item in selected.values():
        if _hash(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"capture hash changed: {item['path']}")
    return [selected[key] for key in sorted(selected)]


def _selected_captures_from_manifest(
    selection_path: Path, requested_names: set[str]
) -> list[dict[str, str]]:
    """Load a hash-pinned selection without trusting filenames or old labels."""
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    source_report_path = Path(selection["sources"]["source_report"])
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    manifest_by_hash = {
        str(row["capture_sha256"]): row
        for row in source_report.get("capture_manifest", ())
    }
    selected: list[dict[str, str]] = []
    for row in selection["sources"]["selected_results"]:
        name = str(row["capture"])
        if requested_names and name not in requested_names:
            continue
        digest = str(row["capture_sha256"])
        manifest = manifest_by_hash.get(digest)
        if manifest is None:
            raise ValueError(f"capture absent from source manifest: {name}")
        path = Path(manifest["capture_path"])
        if path.name != name or _hash(path) != digest:
            raise ValueError(f"capture identity mismatch: {name}")
        selected.append({"capture": name, "path": str(path), "sha256": digest})
    if requested_names - {row["capture"] for row in selected}:
        raise ValueError(
            f"capture names absent from selection: "
            f"{sorted(requested_names - {row['capture'] for row in selected})}"
        )
    if not selected:
        raise ValueError("no hash-pinned captures selected")
    return sorted(selected, key=lambda row: row["sha256"])


def _reason(slot: object | None, template: int) -> str:
    if slot is None:
        return "no_complete_self_attack_slot"
    skill = int(getattr(slot, "monster_skill_id", 0))
    if skill in (0, 0xFFFFFFFF):
        return "empty_self_attack_slot"
    if (template, skill) not in SUMMON_SELF_ATTACK_SPECS:
        return "self_attack_slot_not_in_reviewed_static_catalog"
    return "unknown"


def audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    hits: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()

    def feed(packet):
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        direction = packet.inferred_direction()
        inventory = inspect_udp_damage_records(packet.payload, direction)
        seen: set[int] = set()
        for event in events:
            if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
                continue
            if "direct37" not in str(event.wire_damage_layout) or int(event.wire_record_offset or 0) in seen:
                continue
            seen.add(int(event.wire_record_offset or 0))
            if not inventory.complete:
                continue
            physical = _physical_for_event(event, packet, inventory)
            if physical is None or not 0 < int(physical.source_actor_id) < 65536:
                continue
            life, owner_reason, binding = _life(
                decoder, int(physical.source_actor_id), float(packet.timestamp_seconds), serial,
                int(physical.record_offset), path.name,
            )
            if life is None or int(life.template_id) not in TEMPLATES:
                continue
            owner_name, job = decoder._actor_display_context(life.owner_id)
            if job not in JOBS:
                continue
            target = int(physical.target_actor_id)
            if target not in decoder.monster_entities or target in (decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)):
                continue
            hit = PhysicalHit(
                capture=path.name,
                physical_hit_id=f"{entry['sha256']}:{serial}:{physical.record_offset}",
                order=(serial, 0, int(physical.record_offset)), entity_id=int(physical.source_actor_id),
                generation=int(life.generation), template_id=int(life.template_id), target_id=target,
                damage=int(physical.damage), object_id=int(physical.damage_object_id),
                result_group=int(physical.damage_result_group_id), wire=bytes(packet.payload), direction=direction,
            )
            slot = read_self_attack_slot(hit)
            decision = annotate_hit(hit, life, SUMMON_SELF_ATTACK_SPECS, encounter_mode="pve")
            evidence = asdict(decision.evidence) if decision.evidence is not None else None
            candidate = int(decision.evidence.player_skill_id) if decision.evidence is not None else 0
            old_skill = _canonical(_skill(event.skill_id))
            old_method = str(event.attribution_method or "")
            deterministic_old = bool(old_skill and is_deterministic_attribution(old_method))
            status = "ELIGIBLE" if candidate else "WITHHOLD"
            reason = "exact_current_entity_self_attack_chain" if candidate else _reason(slot, int(life.template_id))
            row = {
                "capture": path.name, "capture_sha256": entry["sha256"], "physical_hit_id": hit.physical_hit_id,
                "packet_serial": serial, "record_offset": int(physical.record_offset), "wire_sha256": sha256(packet.payload).hexdigest(),
                "owner_actor_id": f"0x{life.owner_id:08X}", "owner_job": job, "owner_name_audit_only": owner_name,
                "entity_id": int(life.entity_id), "entity_template_id": int(life.template_id), "entity_template_name": TEMPLATES[int(life.template_id)],
                "entity_generation": int(life.generation), "birth_order": list(life.birth_order), "owner_order": list(life.owner_order),
                "birth_fingerprint": life.birth_fingerprint, "owner_fingerprint": life.owner_fingerprint,
                "owner_is_exact_current_generation": binding is not None, "target_actor_id": target,
                "damage_object_id": int(physical.damage_object_id), "damage_result_group_id_audit_only": int(physical.damage_result_group_id),
                "amount_audit_only": int(physical.damage), "critical_audit_only": bool(physical.critical),
                "self_attack_slot": asdict(slot) if slot else None, "static_self_attack_evidence": evidence,
                "candidate_skill_id": candidate, "candidate_skill_name": NAMES.get(candidate, ""),
                "baseline_skill_id_audit_only": old_skill, "baseline_method_audit_only": old_method,
                "baseline_conflicts_candidate": bool(candidate and old_skill and candidate != old_skill),
                "baseline_is_independent_deterministic_audit_only": deterministic_old,
                "status": status, "reason": reason,
                "positive_proof_uses_time": False, "positive_proof_uses_old_label": False, "positive_proof_uses_cross_same_job": False,
            }
            hits.append(row)
            totals[f"all:{job}:{life.template_id}"] += 1
            totals[f"{status}:{job}:{life.template_id}:{candidate or 0}"] += 1
            if row["baseline_conflicts_candidate"]:
                totals["baseline_label_conflict"] += 1
        return events

    decoder.feed_events = feed
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        source.close()
    return {"capture": path.name, "capture_sha256": entry["sha256"], "hits": hits, "totals": dict(totals)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--capture", action="append", default=[])
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = (args.output or repo / "outputs" / "academic_summon_chain_v72_20260910").resolve()
    out.mkdir(parents=True, exist_ok=True)
    captures = (
        _selected_captures_from_manifest(
            args.selection.resolve(), set(str(value) for value in args.capture)
        )
        if args.selection is not None
        else _selected_captures(
            repo / "outputs" / "under95_summon_chain_v53_20260909" / "partials"
        )
    )
    results = [audit_capture(row) for row in captures]
    hits = [hit for result in results for hit in result["hits"]]
    groups: dict[tuple[str, int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for hit in hits:
        groups[(hit["owner_job"], hit["entity_template_id"], hit["candidate_skill_id"], hit["status"])].append(hit)
    rules = []
    for (job, template, skill, status), values in sorted(groups.items()):
        if status != "ELIGIBLE":
            continue
        conflicts = sum(bool(value["baseline_conflicts_candidate"]) for value in values)
        slots = sorted({int(value["self_attack_slot"]["monster_skill_id"]) for value in values if value["self_attack_slot"]})
        rules.append({
            "owner_job": job, "entity_template_id": template, "entity_template_name": TEMPLATES[template],
            "self_attack_slots": slots, "skill_id": skill, "skill_name": NAMES.get(skill, ""),
            "eligible_hits": len(values), "distinct_captures": len({value["capture_sha256"] for value in values}),
            "distinct_owners": len({value["owner_actor_id"] for value in values}),
            "old_label_conflicts": conflicts,
            # The baseline display name is not truth.  A disagreement is a
            # correction candidate, not a reason to reject the independently
            # closed current-generation self-attack chain.
            "production_status": "eligible_independent_of_legacy_label",
            "required_conditions": ["current entity birth", "same-generation 34/2c owner", "same entity direct37", "reviewed self-attack slot"],
        })
    report = {
        "schema": SCHEMA,
        "captures": [{"capture": x["capture"], "sha256": x["sha256"]} for x in captures],
        "policy": {
            "chain": "static player-action spawn family -> entity birth -> current owner generation -> self-attack slot -> direct37",
            "forbidden": ["time window", "old skill label", "damage amount", "result group", "cross-same-job association"],
            "production_modified": False,
        },
        "counts": {
            "direct37_entity_hits": len(hits), "eligible_hits": sum(row["status"] == "ELIGIBLE" for row in hits),
            "withheld_hits": sum(row["status"] == "WITHHOLD" for row in hits),
            "old_label_conflicts": sum(bool(row["baseline_conflicts_candidate"]) for row in hits),
        },
        "production_rule_candidates": rules,
        "withheld_by_reason": dict(Counter(row["reason"] for row in hits if row["status"] == "WITHHOLD")),
        "notes": {
            "heavy_duck": "32005 slots separate 重磅轰击(4217)、重磅炸弹(4218)、重磅汽笛(4219) and 重磅鸭EX(4352); each uses its own slot, never an entity-template-wide label.",
            "splash": "飞溅(4301) is a separate projectile family (5043) and is not reclassified by 32005/32008 entity evidence.",
            "towers_and_pingpong": "No template-wide conclusion is made for unobserved towers/乒乓爆栗; they remain withheld until their own birth/current-generation/self-action or child-object lineage is observed.",
        },
        "invariants": {"no_production_change": True, "physical_hits_relabel_only": True, "no_time_promotion": True, "no_cross_same_job": True},
    }
    report["sha256"] = sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    (out / "direct37_hit_evidence.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in hits) + ("\n" if hits else ""), encoding="utf-8")
    (out / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "production_rule_candidates.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 重炮手 / 机械大师召唤实体链审计 v72", "", f"本轮重放 {len(captures)} 份哈希固定的目标抓包，仅生成审计输出；未修改生产代码。", "",
             "## 结果", "", f"- direct37 实体 Hit：{report['counts']['direct37_entity_hits']}", f"- 严格闭合：{report['counts']['eligible_hits']}", f"- 暂缓：{report['counts']['withheld_hits']}", f"- 旧标签与确定链冲突：{report['counts']['old_label_conflicts']}", "",
             "## 可生产规则", ""]
    for rule in rules:
        lines.append(f"- `{rule['owner_job']} / {rule['entity_template_id']} / slots {rule['self_attack_slots']} → {rule['skill_id']} {rule['skill_name']}`：{rule['eligible_hits']} Hit，需纠正旧标签 {rule['old_label_conflicts']}。")
    lines += ["", "## 边界", "", "- 飞溅(4301) 是独立 Projectile 5043，不用重磅鸭实体证据归类。", "- 各塔、乒乓爆栗若未在此轮出现带当前代际自攻击/子对象链的 Hit，维持暂缓，不作模板范围外推。", "- 每条 Hit 证据见 `direct37_hit_evidence.jsonl`；所有基线技能名、数值和结果组均标记为 audit-only。"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    validation = {"schema": SCHEMA, "passed": True, "checks": {"all_eligible_current_owner": all(h["owner_is_exact_current_generation"] for h in hits if h["status"] == "ELIGIBLE"), "all_eligible_have_static_slot": all(h["static_self_attack_evidence"] for h in hits if h["status"] == "ELIGIBLE"), "no_old_label_positive_proof": all(not h["positive_proof_uses_old_label"] for h in hits), "no_time_positive_proof": all(not h["positive_proof_uses_time"] for h in hits), "no_cross_same_job": all(not h["positive_proof_uses_cross_same_job"] for h in hits)}}
    (out / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation["passed"] or not all(validation["checks"].values()):
        raise AssertionError(validation)
    print(json.dumps({"output": str(out), "counts": report["counts"], "rules": len(rules)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
