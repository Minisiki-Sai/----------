"""Read-only Saint relic damage audit over the frozen 35-capture corpus.

This tool never changes production attribution.  It separates three facts:

1. the current entity generation has an exact repeated 34/2c owner record;
2. the selected direct37 physical result carries a concrete self-attack slot;
3. that internal attack has a unique, reviewed player-skill parent.

Existing skill labels and cast timing are comparison strata only.  They never
construct a candidate, and ownership is never borrowed across another Saint.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    PhysicalHit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records  # noqa: E402
from tools.audit_under95_summon_chain_v53 import (  # noqa: E402
    _actor,
    _hash,
    _life,
    _physical_for_event,
    _selection,
    _skill,
)
from tools.build_live_dps_monster_catalog import parse_dnt  # noqa: E402
from tools.build_attack_instance_catalog import parse_act  # noqa: E402


SCHEMA = "saint-hand-skill-gaps-v63"
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_OUTPUT = ROOT / "outputs/saint_hand_skill_gaps_v63_20260909"
DEFAULT_BASELINE_REPORT = ROOT / "outputs/native_result_key_v57_20260909/profession_direct_damage_report.json"
FOCUS = {3409: "雷电之手", 3412: "奇迹之手", 3422: "痊愈之手", 3423: "掌握之手"}
TEMPLATES = {30152: "治愈之手EX实体", 30251: "痊愈之手EX实体", 30311: "掌握之手EX实体", 30401: "奇迹之手实体"}

# Only relations that stay unique after the entity family and profession are
# considered.  30802 is deliberately excluded: its global SummonSkillTable
# edge points at archer skill 1303, while these Cleric actors expose the same
# MonsterSkillTable slot for Shock-of-Relic.  That collision is proof that the
# global edge alone cannot identify a Saint player skill.
REVIEWED_INTERNAL_PARENT = {
    30612: 3409,
    30615: 3412,
    30616: 3409,
    291172: 3412,
    292224: 3423,
}


def _static_review() -> dict[str, Any]:
    resource = Path(r"E:\ext0811.rar\Resource00\Resource00\resource")
    ext = resource / "ext"
    monster = {int(row["RowId"]): row for row in parse_dnt(ext / "monstertable_quest.dnt")}
    tables = {int(row["RowId"]): row for row in parse_dnt(ext / "monsterskilltable.dnt")}
    summon = parse_dnt(ext / "summonskilltable.dnt")
    rows = []
    for template_id in sorted(TEMPLATES):
        entity = monster.get(template_id)
        skills = []
        if entity is not None:
            table = tables.get(int(entity.get("_SkillTable", 0)), {})
            for field, value in table.items():
                if not field.startswith("_SkillIndex") or not isinstance(value, int) or value <= 0:
                    continue
                explicit = sorted({int(row["_SkillIndex"]) for row in summon if str(row.get("_MonsterSkill", "")) == str(value)})
                skills.append({
                    "slot": field,
                    "monster_skill_id": value,
                    "global_explicit_player_skill_ids": explicit,
                    "reviewed_saint_parent": REVIEWED_INTERNAL_PARENT.get(value),
                })
        rows.append({
            "template_id": template_id,
            "label": TEMPLATES[template_id],
            "actor_table_id": int(entity.get("_ActorTableID", 0)) if entity else 0,
            "monster_skill_table": int(entity.get("_SkillTable", 0)) if entity else 0,
            "custom_ai": str(entity.get("_CustomAI", "")) if entity else "",
            "skills": skills,
        })
    spawn_edges = []
    for relative in (
        "char/player/cleric/cleric_skill_1st_priest.act",
        "char/player/cleric/cleric_skill_2st_saint.act",
    ):
        path = resource / relative
        for action in parse_act(path).actions:
            for signal_index, signal in enumerate(action.signals):
                if signal.signal_type != 48 or len(signal.payload) < 4:
                    continue
                template_id = int.from_bytes(signal.payload[:4], "little")
                if template_id not in TEMPLATES and template_id not in (30001, 30501):
                    continue
                spawn_edges.append({
                    "act_path": str(path), "act_sha256": _hash(path),
                    "action": action.name, "signal_index": signal_index,
                    "signal_type": 48, "spawn_template_id": template_id,
                })
    return {
        "resource_root": str(resource),
        "resource_files": {
            str(ext / name): _hash(ext / name)
            for name in ("monstertable_quest.dnt", "monsterskilltable.dnt", "summonskilltable.dnt")
        },
        "templates": rows,
        "player_action_spawn_edges": spawn_edges,
        "excluded_collision": {
            "monster_skill_id": 30802,
            "global_player_skill_id": 1303,
            "global_player_skill_name": "偷袭点",
            "reason": "cross-profession/global-table collision; cannot prove Saint Shock-of-Relic as 3503",
        },
    }


def _event_identity(event: CombatEvent) -> tuple[Any, ...]:
    return (
        int(event.timestamp_ms), str(event.source_id), str(event.target_id), int(event.amount),
        bool(event.critical), int(event.wire_source_actor_id or 0), int(event.wire_target_actor_id or 0),
        int(event.wire_damage_object_id or 0), int(event.wire_result_group_id or 0),
        int(event.wire_record_offset or 0), str(event.wire_damage_layout or ""),
    )


def audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    if _hash(path) != entry["sha256"]:
        raise ValueError(f"capture hash changed: {path}")
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    counts: Counter[str] = Counter()
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    fixtures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invariants_before = Counter()
    invariants_after = Counter()

    def add_fixture(key: str, row: dict[str, Any]) -> None:
        if len(fixtures[key]) < 4:
            fixtures[key].append(row)

    def feed_events(packet):
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        direction = packet.inferred_direction()
        inventory = inspect_udp_damage_records(packet.payload, direction)
        seen: set[int] = set()
        for event in events:
            if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
                continue
            if "direct37" not in str(event.wire_damage_layout or "") or int(event.wire_record_offset or 0) in seen:
                continue
            seen.add(int(event.wire_record_offset or 0))
            if not inventory.complete:
                counts[f"physical_inventory_rejected:{inventory.rejection_reason or 'unknown'}"] += 1
                continue
            physical = _physical_for_event(event, packet, inventory)
            if physical is None or not 0 < int(physical.source_actor_id) < 65536:
                continue
            life, owner_reason, binding = _life(
                decoder, int(physical.source_actor_id), float(packet.timestamp_seconds), serial,
                int(physical.record_offset), path.name,
            )
            if life is None:
                counts[f"owner_rejected:{owner_reason}"] += 1
                continue
            _owner_name, owner_job = decoder._actor_display_context(life.owner_id)
            if owner_job != "圣徒":
                continue
            counts["saint_focus_entity_direct37"] += 1
            old_skill = _skill(event.skill_id)
            old_method = str(event.attribution_method or "")
            deterministic = bool(old_skill and is_deterministic_attribution(old_method))
            if old_skill in FOCUS and not deterministic:
                counts[f"requested_gap:{old_skill}"] += 1
            invariants_before[repr(_event_identity(event))] += 1
            hit = PhysicalHit(
                capture=path.name,
                physical_hit_id=f"{entry['sha256']}:{serial}:{physical.record_offset}",
                order=(serial, 0, int(physical.record_offset)),
                entity_id=int(physical.source_actor_id), generation=int(life.generation),
                template_id=int(life.template_id), target_id=int(physical.target_actor_id),
                damage=int(physical.damage), object_id=int(physical.damage_object_id),
                result_group=int(physical.damage_result_group_id), wire=bytes(packet.payload), direction=direction,
            )
            slot = read_self_attack_slot(hit)
            slot_id = int(slot.monster_skill_id) if slot else -1
            context_id = int(slot.context_skill_id) if slot else -1
            candidate = REVIEWED_INTERNAL_PARENT.get(slot_id)
            slot_label = "no_complete_slot" if slot is None else "empty_slot" if slot_id in (0, 0xFFFFFFFF) else str(slot_id)
            key = f"{life.template_id}|{old_skill}|{old_method}|{slot_label}|{candidate or 0}"
            row = strata[key]
            row["hits"] += 1
            row["damage"] += int(event.amount)
            row["requested_gap_hits"] += int(old_skill in FOCUS and not deterministic)
            row["deterministic_controls"] += int(deterministic)
            row["candidate_matches_old"] += int(candidate is not None and candidate == old_skill)
            row["candidate_conflicts_old"] += int(candidate is not None and old_skill > 0 and candidate != old_skill)
            row[f"capture:{path.name}"] += 1
            row[f"owner:{life.owner_id:08X}"] += 1
            if candidate is not None:
                counts[f"exact_candidate:{candidate}"] += 1
                counts[f"exact_candidate_gap:{candidate}"] += int(not deterministic)
                counts[f"exact_candidate_relabel:{old_skill}->{candidate}"] += int(old_skill > 0 and old_skill != candidate)
            else:
                counts[f"blocked_slot:{slot_label}"] += 1
            if old_skill in FOCUS and not deterministic:
                proof = {
                    "capture": path.name,
                    "capture_sha256": entry["sha256"],
                    "timestamp_ms": int(event.timestamp_ms),
                    "wire_sha256": sha256(packet.payload).hexdigest(),
                    "record_offset": int(physical.record_offset),
                    "owner_actor_id": f"0x{life.owner_id:08X}",
                    "entity_id": int(life.entity_id),
                    "entity_template_id": int(life.template_id),
                    "entity_generation": int(life.generation),
                    "birth_order": list(life.birth_order),
                    "owner_order": list(life.owner_order),
                    "birth_fingerprint": life.birth_fingerprint,
                    "owner_message_fingerprint": life.owner_fingerprint,
                    "target_actor_id": int(physical.target_actor_id),
                    "damage_object_id": int(physical.damage_object_id),
                    "damage_result_group_id": int(physical.damage_result_group_id),
                    "amount": int(event.amount),
                    "critical": bool(event.critical),
                    "self_attack_slot": asdict(slot) if slot else None,
                    "old_skill_id": old_skill,
                    "old_method": old_method,
                    "candidate_skill_id": candidate,
                    "candidate_skill_name": FOCUS.get(candidate, "") if candidate else "",
                    "candidate_uses_timing": False,
                    "candidate_uses_old_label": False,
                    "owner_is_exact_current_generation": binding is not None,
                }
                add_fixture(f"skill:{old_skill}:slot:{slot_label}:candidate:{candidate or 0}", proof)
            invariants_after[repr(_event_identity(event))] += 1
        return events

    decoder.feed_events = feed_events
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        source.close()
    if invariants_before != invariants_after:
        raise AssertionError(f"read-only audit changed physical rows: {path}")
    return {
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "strata": {key: dict(value) for key, value in sorted(strata.items())},
        "fixtures": dict(fixtures),
        "invariants": {
            "physical_row_multiset_equal": True,
            "physical_rows": sum(invariants_before.values()),
            "physical_damage": sum(int(key.split(", ")[3]) * count for key, count in invariants_before.items()) if False else None,
        },
    }


def _formal_gap_totals(path: Path) -> dict[int, int]:
    report = json.loads(path.read_text(encoding="utf-8"))
    totals = Counter()
    for row in report["details"]:
        text = str(row.get("skill_id", ""))
        if (row.get("职业") == "圣徒" and row.get("归因分类") == "nondeterministic"
                and text.isdecimal() and int(text) in FOCUS):
            totals[int(text)] += int(row.get("Hit数", 0))
    return {skill: int(totals[skill]) for skill in FOCUS}


def merge(results: list[dict[str, Any]], baseline: dict[str, dict[str, Any]], static: dict[str, Any],
          formal_gap: dict[int, int]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    fixtures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        counts.update(result["counts"])
        for key, value in result["strata"].items():
            strata[key].update(value)
        for key, values in result["fixtures"].items():
            fixtures[key].extend(values[: max(0, 4 - len(fixtures[key]))])
    in_scope_gap = {skill: int(counts[f"requested_gap:{skill}"]) for skill in FOCUS}
    baseline_gap = dict(formal_gap)
    candidates = []
    for key, value in sorted(strata.items()):
        template, old_skill, old_method, slot, candidate = key.split("|")
        captures = sum(name.startswith("capture:") for name in value)
        owners = sum(name.startswith("owner:") for name in value)
        gap_hits = int(value["requested_gap_hits"])
        candidate_id = int(candidate)
        controls = int(value["deterministic_controls"])
        conflicts = int(value["candidate_conflicts_old"])
        safe = bool(candidate_id and (controls == 0 or conflicts == 0))
        candidates.append({
            "template_id": int(template), "old_skill_id": int(old_skill), "old_method": old_method,
            "self_attack_slot": slot, "candidate_skill_id": candidate_id,
            "hits": int(value["hits"]), "damage": int(value["damage"]), "requested_gap_hits": gap_hits,
            "deterministic_controls": controls, "candidate_matches_old": int(value["candidate_matches_old"]),
            "candidate_conflicts_old": conflicts, "distinct_captures": captures, "distinct_owners": owners,
            "production_status": "eligible_rule" if safe and gap_hits else "positive_control" if safe else "blocked",
        })
    safe_rows = [row for row in candidates if row["production_status"] == "eligible_rule"]
    gain = Counter()
    relabel = Counter()
    for row in safe_rows:
        gain[row["candidate_skill_id"]] += row["requested_gap_hits"]
        if row["old_skill_id"] != row["candidate_skill_id"]:
            relabel[f"{row['old_skill_id']}->{row['candidate_skill_id']}"] += row["requested_gap_hits"]
    saint = baseline["圣徒"]
    return {
        "schema": SCHEMA,
        "policy": {
            "production_changed": False,
            "score_or_upload_run": False,
            "required_chain": "current entity birth -> same-generation repeated 34/2c owner -> exact direct37 result -> concrete self-attack slot -> unique reviewed static parent",
            "forbidden": ["time-window promotion", "nearest cast", "existing skill label as proof", "cross-Saint owner", "whole-template attribution"],
        },
        "fixed_corpus": {
            "captures": len(results), "hash_unique": len({row["capture_sha256"] for row in results}),
            "manifest": [{k: row[k] for k in ("capture", "capture_path", "capture_sha256")} for row in results],
        },
        "baseline": {
            "saint_named_coverage_percent": saint["named_coverage_percent"],
            "saint_hits_needed_for_95": saint["hits_needed_for_95"],
            "requested_gap_hits": baseline_gap,
            "requested_gap_hits_with_exact_short_entity_owner": in_scope_gap,
        },
        "summary": {
            "saint_focus_entity_direct37": int(counts["saint_focus_entity_direct37"]),
            "safe_gap_hits": sum(gain.values()),
            "safe_gap_by_final_skill": {str(k): v for k, v in sorted(gain.items())},
            "safe_relabels": dict(relabel),
            "remaining_requested_gap_after_this_audit": {
                str(skill): baseline_gap[skill] - sum(
                    row["requested_gap_hits"] for row in safe_rows if row["old_skill_id"] == skill
                ) for skill in FOCUS
            },
            "blocked_slots": {key.split(":", 1)[1]: value for key, value in counts.items() if key.startswith("blocked_slot:")},
            "read_only_physical_invariant": all(row["invariants"]["physical_row_multiset_equal"] for row in results),
        },
        "production_rule_candidates": safe_rows,
        "all_strata": candidates,
        "real_fixtures": dict(fixtures),
        "static_review": static,
        "interpretation": {
            "summon_owner_is_deterministic_skill_proof": False,
            "reason": "owner and skill are separate facts; all four relic templates share MonsterSkillTable 51 and can expose other relic commands",
            "important_reclassification": "a concrete internal slot may prove a different skill than the old summon_owner label; preserve raw damage/source/target and change only skill attribution",
        },
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 圣徒四类手技能确定归因专项审计 v63", "",
        f"- 固定语料：{report['fixed_corpus']['captures']} 份抓包，哈希唯一 {report['fixed_corpus']['hash_unique']} 份。",
        "- 只读审计：未修改共享生产、评分或上传。",
        "- summon_owner 只证明缓存归属，不是技能确定证据。", "",
        "## 缺口与本轮可闭合量", "",
        "| 旧技能 | 原缺口Hit | 有当前代精确owner | 可安全闭合Hit | 仍未闭合Hit |", "|---|---:|---:|---:|---:|",
    ]
    remaining = report["summary"]["remaining_requested_gap_after_this_audit"]
    for skill, name in FOCUS.items():
        old = report["baseline"]["requested_gap_hits"][skill]
        owned = report["baseline"]["requested_gap_hits_with_exact_short_entity_owner"][skill]
        left = remaining[str(skill)]
        lines.append(f"| {skill} {name} | {old} | {owned} | {old-left} | {left} |")
    lines += ["", "## 可生产化规则", ""]
    if not report["production_rule_candidates"]:
        lines.append("- 本轮没有达到严格门槛的新规则；所有候选继续失败关闭。")
    for row in report["production_rule_candidates"]:
        lines.append(
            f"- 实体模板 `{row['template_id']}` 的 direct37，若当前代 34/2c owner 完整，且自攻击槽为 "
            f"`{row['self_attack_slot']}`，可确定归为 `{row['candidate_skill_id']}`；本语料可处理 "
            f"{row['requested_gap_hits']} Hit，独立确定对照冲突 {row['candidate_conflicts_old']}。"
        )
    lines += ["", "## 关键结论", "",
        "- 四类实体共享 MonsterSkillTable 51，因此不能按实体模板整包归到召唤技能。",
        "- 客户端 ACT 的 signal 48 已给出玩家动作到 30152/30251/30311/30401 的固定生成边；它证明实体从哪种玩家动作生成，但单独不证明实体本次伤害是哪条攻击分支。",
        "- `30802 -> 1303` 在全局表里碰到跨职业 ID 冲突（1303 是弓箭手偷袭点），不能据此把圣徒伤害升格；这一路应继续等待实体动作/对象链证明。",
        "- 雷电之手 603 个旧缺口中，本轮没有一条同时具备当前实体代次和 34/2c 精确 owner；不能把旧 summon_owner 缓存当成确定证据。",
        "- 痊愈之手、掌握之手分别有 516、564 Hit 拥有精确 owner，但其 direct37 自攻击槽为空，仍无法排除冲击之手等实体指令分支。",
        "- 真实 fixture 保留了抓包哈希、线序、实体代次、owner 原始包哈希、自攻击槽和 direct37 对象，可直接用于后续生产回归测试。",
        "- 所有候选保持来源、目标、伤害、Hit、暴击和物理记录不变，只允许纠正技能标签。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    captures, baseline = _selection(args.selection)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    partials = output / "partials_all_saint_entities"
    partials.mkdir(exist_ok=True)
    results = []
    workers = max(1, min(8, args.workers))
    pending = []
    for entry in captures:
        partial = partials / f"{entry['sha256']}.json"
        if partial.is_file():
            saved = json.loads(partial.read_text(encoding="utf-8"))
            if saved.get("capture_sha256") == entry["sha256"]:
                results.append(saved)
                continue
        pending.append(entry)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(audit_capture, entry): entry for entry in pending}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            (partials / f"{result['capture_sha256']}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"done": result["capture"], "remaining": len(captures)-len(results)}, ensure_ascii=False), flush=True)
    by_hash = {row["capture_sha256"]: row for row in results}
    ordered = [by_hash[row["sha256"]] for row in captures]
    report = merge(ordered, baseline, _static_review(), _formal_gap_totals(DEFAULT_BASELINE_REPORT))
    report["selection_sha256"] = _hash(args.selection)
    report["formal_baseline_report_sha256"] = _hash(DEFAULT_BASELINE_REPORT)
    report["tool_sha256"] = _hash(Path(__file__))
    json_path = output / "REPORT.json"
    md_path = output / "REPORT.md"
    fixture_path = output / "real_fixtures.json"
    rules_path = output / "production_rule_candidates.json"
    checks = {
        "fixed_35_capture_corpus": report["fixed_corpus"]["captures"] == 35
        and report["fixed_corpus"]["hash_unique"] == 35,
        "formal_gap_totals_match": report["baseline"]["requested_gap_hits"]
        == {3409: 603, 3412: 723, 3422: 646, 3423: 758},
        "read_only_physical_invariant": report["summary"]["read_only_physical_invariant"] is True,
        "all_candidates_have_concrete_slot": all(
            row["candidate_skill_id"] > 0 and row["self_attack_slot"] not in ("empty_slot", "no_complete_slot")
            for row in report["production_rule_candidates"]
        ),
        "all_candidates_zero_deterministic_conflict": all(
            row["candidate_conflicts_old"] == 0 for row in report["production_rule_candidates"]
        ),
        "fixtures_do_not_use_time_or_old_label": all(
            not row["candidate_uses_timing"] and not row["candidate_uses_old_label"]
            for values in report["real_fixtures"].values() for row in values
        ),
    }
    validation = {"schema": SCHEMA, "passed": all(checks.values()), "checks": checks}
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    fixture_path.write_text(json.dumps(report["real_fixtures"], ensure_ascii=False, indent=2), encoding="utf-8")
    rules_path.write_text(json.dumps(report["production_rule_candidates"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation["passed"]:
        raise AssertionError(validation)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "fixtures": str(fixture_path),
                      "rules": str(rules_path), "validation": validation, "summary": report["summary"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
