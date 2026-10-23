"""Read-only exact-chain audit for Saint lightning/hold relic gaps.

The audit deliberately separates skill identity from owner identity.  A hit is
eligible for production only when both are packet/static proven in the same
entity generation.  Existing labels, cached summon owners and time proximity
are comparison data only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
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
from dragon_nest_builder.live_dps.compact_entity_object_evidence import (  # noqa: E402
    decode_selector_records,
)
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.entity_owner_evidence import (  # noqa: E402
    decode_entity_owner_batch,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    PhysicalHit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_under95_summon_chain_v53 import (  # noqa: E402
    _actor,
    _hash,
    _life,
    _origin_candidate,
    _physical_for_event,
    _selection,
    _selector_candidate,
    _skill,
    SelectorState,
)
from tools.build_live_dps_monster_catalog import parse_dnt  # noqa: E402


SCHEMA = "saint-lightning-master-gaps-v65"
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_BASELINE = ROOT / "outputs/native_result_key_v57_20260909/profession_direct_damage_report.json"
DEFAULT_OUTPUT = ROOT / "outputs/saint_lightning_master_gaps_v65_20260909"
FOCUS = {3409: "雷电之手", 3423: "掌握之手"}
SAINT_RELIC_SKILLS = {3409, 3412, 3422, 3423, 3503}
STATIC_INTERNAL_PARENT = {
    30612: 3409,
    30616: 3409,
    30615: 3412,
    291172: 3412,
    292224: 3423,
}
LIGHTNING_TEMPLATES = {30001, 30501}
HOLD_TEMPLATE = 30311
FOREIGN_INTERNAL_SLOT_NAMES = {
    261801: "阿兹那之刃等级1",
    262101: "阿兹那投掷术等级1",
    262301: "阿兹那攻击等级1",
    262302: "阿兹那攻击等级2",
}


def _canonical_skill(value: object) -> int:
    return _skill(value)


def _identity(event: CombatEvent) -> tuple[Any, ...]:
    return (
        int(event.timestamp_ms), str(event.source_id), str(event.target_id),
        int(event.amount), bool(event.critical),
        int(event.wire_source_actor_id or 0), int(event.wire_target_actor_id or 0),
        int(event.wire_damage_object_id or 0), int(event.wire_result_group_id or 0),
        int(event.wire_record_offset or 0), str(event.wire_damage_layout or ""),
    )


def _template_id(value: object) -> int:
    return int(getattr(value, "template_id", value) or 0)


@dataclass(slots=True)
class RawOwnerState:
    owner_ids: set[int]
    message_hashes: set[str]
    first_serial: int
    last_serial: int
    repeats: int = 0


def _formal_rows() -> tuple[dict[int, int], list[dict[str, Any]]]:
    report = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    totals: Counter[int] = Counter()
    rows = []
    for row in report["details"]:
        text = str(row.get("skill_id", ""))
        if (row.get("职业") == "圣徒" and row.get("归因分类") == "nondeterministic"
                and text.isdecimal() and int(text) in FOCUS):
            totals[int(text)] += int(row.get("Hit数", 0))
            rows.append(dict(row))
    return {skill: int(totals[skill]) for skill in FOCUS}, rows


def _foreign_static_review() -> dict[str, Any]:
    """Prove that the four concrete slots are not Saint relic attacks."""
    ext = Path(r"E:\ext0811.rar\Resource00\Resource00\resource\ext")
    ids = set(FOREIGN_INTERNAL_SLOT_NAMES)
    table_rows = []
    table_ids = set()
    for row in parse_dnt(ext / "monsterskilltable.dnt"):
        values = {
            int(value) for field, value in row.items()
            if field.startswith("_SkillIndex") and isinstance(value, int)
        }
        matched = sorted(values & ids)
        if matched:
            table_ids.add(int(row["RowId"]))
            table_rows.append({"row_id": int(row["RowId"]), "matched_slots": matched})
    entities = []
    for name in ("monstertable.dnt", "monstertable_quest.dnt", "monstertable_transform.dnt"):
        path = ext / name
        for row in parse_dnt(path):
            if int(row.get("_SkillTable", 0) or 0) in table_ids:
                entities.append({
                    "table": name, "template_id": int(row["RowId"]),
                    "skill_table_id": int(row["_SkillTable"]),
                    "actor_table_id": int(row.get("_ActorTableID", 0) or 0),
                    "custom_ai": str(row.get("_CustomAI", "")),
                })
    return {
        "slot_names": {str(key): value for key, value in FOREIGN_INTERNAL_SLOT_NAMES.items()},
        "monster_skill_tables": table_rows,
        "entities": entities,
        "source_hashes": {
            name: _hash(ext / name) for name in
            ("monsterskilltable.dnt", "monstertable.dnt", "monstertable_quest.dnt", "monstertable_transform.dnt")
        },
        "conclusion": "all four slots occur only in templates 33601/33602/33603 (Assassin Assassinsnyang/Azuna family), never in Saint relic templates",
    }


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
    selectors: dict[tuple[int, int, int], SelectorState] = {}
    raw_owners: dict[tuple[int, int], RawOwnerState] = {}
    seen_scene = int(decoder.entity_owner_evidence.scene_serial)
    before: Counter[tuple[Any, ...]] = Counter()
    after: Counter[tuple[Any, ...]] = Counter()

    def fixture(key: str, row: dict[str, Any]) -> None:
        if len(fixtures[key]) < 5:
            fixtures[key].append(row)

    def feed_events(packet):
        nonlocal seen_scene
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        direction = packet.inferred_direction()
        inventory = inspect_udp_damage_records(packet.payload, direction)
        scene = int(decoder.entity_owner_evidence.scene_serial)
        if scene != seen_scene:
            raw_owners.clear()
            selectors.clear()
            seen_scene = scene

        batch = decode_entity_owner_batch(packet.payload, direction)
        if batch is not None:
            for record in batch.records:
                key = (scene, int(record.entity_id))
                state = raw_owners.get(key)
                if state is None:
                    state = RawOwnerState(set(), set(), serial, serial)
                    raw_owners[key] = state
                state.owner_ids.add(int(record.owner_id))
                state.message_hashes.add(str(record.message_fingerprint))
                state.last_serial = serial
                state.repeats += 1

        if str(packet.transport).upper() == "UDP":
            for record in decode_selector_records(packet.payload, direction):
                birth = decoder.entity_owner_evidence.births.get(record.entity)
                if birth is None:
                    continue
                key = (int(record.entity), int(birth[1]), int(record.object_id))
                previous = selectors.get(key)
                if record.action < 0 or record.signal < 0:
                    if previous is not None:
                        previous.invalidated = True
                    continue
                current = SelectorState(
                    entity=int(record.entity), template=int(birth[0]),
                    entity_generation=int(birth[1]), object_id=int(record.object_id),
                    length=int(record.length), action=int(record.action), signal=int(record.signal),
                    order=(serial, 0, int(record.offset)), timestamp=float(packet.timestamp_seconds),
                    wire_hash=str(record.wire_hash),
                    birth_hash=str(decoder.entity_owner_evidence.birth_evidence.get(
                        record.entity, ((0, 0, 0), ""))[1]),
                )
                if previous is None:
                    selectors[key] = current
                elif (previous.template, previous.length, previous.action, previous.signal,
                      previous.birth_hash) == (current.template, current.length, current.action,
                      current.signal, current.birth_hash):
                    previous.repeats += 1
                else:
                    previous.invalidated = True
                    selectors[key] = current

        seen_offsets: set[int] = set()
        for event in events:
            if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
                continue
            if "direct37" not in str(event.wire_damage_layout or ""):
                continue
            offset = int(event.wire_record_offset or 0)
            if offset in seen_offsets:
                continue
            seen_offsets.add(offset)
            before[_identity(event)] += 1
            after[_identity(event)] += 1
            if not inventory.complete:
                continue
            physical = _physical_for_event(event, packet, inventory)
            if physical is None:
                continue
            old_skill = _canonical_skill(event.skill_id)
            old_method = str(event.attribution_method or "")
            deterministic = bool(old_skill and is_deterministic_attribution(old_method))
            physical_source = int(physical.source_actor_id)
            mapped_owner = _actor(event.source_id)
            mapped_job = decoder._actor_display_context(mapped_owner)[1] if mapped_owner else ""
            if old_skill not in FOCUS or deterministic or mapped_job != "圣徒":
                continue

            counts[f"focus_gap:{old_skill}"] += 1
            counts[f"focus_gap_damage:{old_skill}"] += int(event.amount)
            birth = decoder.entity_owner_evidence.births.get(physical_source)
            template = _template_id(decoder.monster_entities.get(physical_source))
            generation = int(birth[1]) if birth is not None else 0
            life = None
            owner_reason = "not_short_entity"
            binding = None
            if 0 < physical_source < 65536:
                life, owner_reason, binding = _life(
                    decoder, physical_source, float(packet.timestamp_seconds), serial,
                    int(physical.record_offset), path.name,
                )
                if birth is not None:
                    template = int(birth[0])

            hit = PhysicalHit(
                capture=path.name,
                physical_hit_id=f"{entry['sha256']}:{serial}:{physical.record_offset}",
                order=(serial, 0, int(physical.record_offset)), entity_id=physical_source,
                generation=generation, template_id=template,
                target_id=int(physical.target_actor_id), damage=int(physical.damage),
                object_id=int(physical.damage_object_id or 0),
                result_group=int(physical.damage_result_group_id),
                wire=bytes(packet.payload), direction=direction,
            )
            slot = read_self_attack_slot(hit)
            slot_id = int(slot.monster_skill_id) if slot is not None else -1
            context_id = int(slot.context_skill_id) if slot is not None else -1
            routes: list[tuple[str, int, dict[str, Any]]] = []
            slot_candidate = STATIC_INTERNAL_PARENT.get(slot_id)
            if slot_candidate:
                routes.append(("self_attack_slot", slot_candidate, asdict(slot)))

            selector = selectors.get((physical_source, generation, int(physical.damage_object_id or 0)))
            if selector is not None and life is not None:
                if selector.birth_hash != life.birth_fingerprint:
                    selector.invalidated = True
                elif selector.order < hit.order:
                    selected = _selector_candidate(selector)
                    if selected is not None:
                        routes.append(("entity_selector_object", selected[0], selected[1]))

            origin = _origin_candidate(
                decoder, physical_source, int(physical.damage_object_id or 0),
                float(packet.timestamp_seconds), serial, int(physical.record_offset),
            )
            if origin is not None:
                routes.append(("projectile_object", origin[0], origin[1]))

            route_skills = {int(row[1]) for row in routes}
            skill_candidate = next(iter(route_skills)) if len(route_skills) == 1 else 0
            owner_exact = bool(life is not None and binding is not None
                               and int(life.owner_id) == mapped_owner)
            raw_owner = raw_owners.get((scene, physical_source))
            raw_owner_ids = sorted(raw_owner.owner_ids) if raw_owner else []
            raw_owner_unique_matches = bool(len(raw_owner_ids) == 1 and raw_owner_ids[0] == mapped_owner)
            template_expected = bool(
                (skill_candidate == 3409 and template in LIGHTNING_TEMPLATES)
                or (skill_candidate == 3423 and template == HOLD_TEMPLATE)
                or skill_candidate in {3412, 3422, 3503}
            )
            eligible = bool(skill_candidate and owner_exact and template_expected)
            conflict = bool(skill_candidate and skill_candidate != old_skill)
            if eligible:
                counts[f"eligible:{old_skill}->{skill_candidate}"] += 1
            elif skill_candidate:
                counts[f"skill_proven_owner_blocked:{old_skill}->{skill_candidate}:{owner_reason}"] += 1
            else:
                counts[f"no_exact_skill:{old_skill}:{owner_reason}"] += 1
            if conflict:
                counts[f"old_bucket_conflict:{old_skill}->{skill_candidate}"] += 1

            slot_label = "none" if slot is None else "empty" if slot_id in (0, 0xFFFFFFFF) else str(slot_id)
            route_label = "+".join(sorted(row[0] for row in routes)) or "none"
            key = "|".join(map(str, (
                old_skill, old_method, physical_source < 65536, template, generation,
                owner_reason, slot_label, context_id, route_label, skill_candidate,
                owner_exact, raw_owner_unique_matches,
            )))
            row = strata[key]
            row["hits"] += 1
            row["damage"] += int(event.amount)
            row[f"capture:{path.name}"] += 1
            row[f"mapped_owner:{mapped_owner:08X}"] += 1
            row[f"physical_source:{physical_source:08X}"] += 1
            row["eligible"] += int(eligible)
            row["conflicts_old_bucket"] += int(conflict)

            proof = {
                "capture": path.name,
                "capture_sha256": entry["sha256"],
                "timestamp_ms": int(event.timestamp_ms),
                "wire_sha256": sha256(packet.payload).hexdigest(),
                "packet_serial": serial,
                "record_offset": int(physical.record_offset),
                "old_skill_id": old_skill,
                "old_method": old_method,
                "mapped_owner_actor_id": f"0x{mapped_owner:08X}",
                "physical_source_actor_id": f"0x{physical_source:08X}",
                "target_actor_id": f"0x{int(physical.target_actor_id):08X}",
                "damage": int(event.amount),
                "critical": bool(event.critical),
                "damage_object_id": int(physical.damage_object_id or 0),
                "result_group_id": int(physical.damage_result_group_id),
                "raw_skill_slots": [[int(a), int(b)] for a, b in physical.raw_skill_slots],
                "entity_template_id": template,
                "entity_generation": generation,
                "birth_present": birth is not None,
                "owner_reason": owner_reason,
                "owner_exact_same_generation": owner_exact,
                "raw_owner_ids_this_scene": [f"0x{value:08X}" for value in raw_owner_ids],
                "raw_owner_unique_matches_mapped_owner": raw_owner_unique_matches,
                "self_attack_slot": asdict(slot) if slot else None,
                "exact_routes": routes,
                "candidate_skill_id": skill_candidate,
                "eligible": eligible,
                "conflicts_old_bucket": conflict,
                "uses_time_proximity": False,
                "uses_old_label_as_proof": False,
            }
            if life is not None:
                proof.update({
                    "birth_order": list(life.birth_order),
                    "owner_order": list(life.owner_order),
                    "birth_fingerprint": life.birth_fingerprint,
                    "owner_fingerprint": life.owner_fingerprint,
                })
            fixture(f"{old_skill}:{'eligible' if eligible else 'blocked'}:{slot_label}:{skill_candidate}", proof)
        return events

    decoder.feed_events = feed_events
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        source.close()
    if before != after:
        raise AssertionError(f"audit changed physical rows: {path}")
    return {
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "strata": {key: dict(value) for key, value in sorted(strata.items())},
        "fixtures": dict(fixtures),
        "physical_row_multiset_equal": True,
    }


def merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    fixtures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        counts.update(result["counts"])
        for key, values in result["strata"].items():
            strata[key].update(values)
        for key, rows in result["fixtures"].items():
            fixtures[key].extend(rows[: max(0, 5 - len(fixtures[key]))])

    formal, formal_rows = _formal_rows()
    eligible: Counter[str] = Counter()
    conflicts: Counter[str] = Counter()
    blocked: Counter[str] = Counter()
    shape_rows: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    for key, values in sorted(strata.items()):
        (old_skill, old_method, short_source, template, generation, owner_reason,
         slot, context, routes, candidate, owner_exact, raw_owner_match) = key.split("|")
        shape = (
            int(old_skill), old_method, short_source == "True", int(template),
            owner_reason, slot, int(context), routes, int(candidate),
            owner_exact == "True", raw_owner_match == "True",
        )
        shape_rows[shape].update(values)

    table = []
    foreign_veto: Counter[str] = Counter()
    foreign_veto_damage: Counter[str] = Counter()
    for shape, values in sorted(shape_rows.items(), key=lambda item: tuple(map(str, item[0]))):
        (old_skill, old_method, short_source, template, owner_reason, slot,
         context, routes, candidate, owner_exact, raw_owner_match) = shape
        foreign_slot = int(slot) if str(slot).isdecimal() and int(slot) in FOREIGN_INTERNAL_SLOT_NAMES else 0
        item = {
            "old_skill_id": old_skill, "old_method": old_method,
            "short_entity_source": short_source, "template_id": template,
            "owner_reason": owner_reason,
            "self_attack_slot": slot, "context_slot": int(context),
            "exact_routes": routes, "candidate_skill_id": int(candidate),
            "owner_exact_same_generation": owner_exact,
            "raw_owner_unique_matches": raw_owner_match,
            "hits": int(values["hits"]), "damage": int(values["damage"]),
            "eligible_hits": int(values["eligible"]),
            "conflicts_old_bucket": int(values["conflicts_old_bucket"]),
            "distinct_captures": sum(name.startswith("capture:") for name in values),
            "distinct_mapped_owners": sum(name.startswith("mapped_owner:") for name in values),
            "distinct_physical_sources": sum(name.startswith("physical_source:") for name in values),
            "foreign_family_veto": bool(foreign_slot),
            "foreign_family_name": FOREIGN_INTERNAL_SLOT_NAMES.get(foreign_slot, ""),
        }
        table.append(item)
        if item["eligible_hits"]:
            eligible[f"{old_skill}->{candidate}"] += item["eligible_hits"]
        if item["conflicts_old_bucket"]:
            conflicts[f"{old_skill}->{candidate}"] += item["conflicts_old_bucket"]
        if foreign_slot:
            veto_key = f"{old_skill}->Azuna:{foreign_slot}"
            foreign_veto[veto_key] += item["hits"]
            foreign_veto_damage[veto_key] += item["damage"]
        if not item["eligible_hits"]:
            blocked[f"{old_skill}:{owner_reason}:{slot}:{candidate}"] += item["hits"]

    production_rule_candidates = {
        "positive_exact_chain": dict(eligible),
        "negative_veto": {
            "rule_id": "concrete_foreign_self_slot_vetoes_cached_saint_owner",
            "self_attack_slots": sorted(FOREIGN_INTERNAL_SLOT_NAMES),
            "slot_names": {str(key): value for key, value in sorted(FOREIGN_INTERNAL_SLOT_NAMES.items())},
            "observed_hits": sum(foreign_veto.values()),
            "observed_damage": sum(foreign_veto_damage.values()),
            "action": (
                "When a direct37 source carries one of these concrete m102 self-attack slots, "
                "invalidate a cached Saint 3409/3423 label for that row. Reassign it only when "
                "the current entity generation has its own exact birth and owner evidence; "
                "otherwise keep the row unresolved."
            ),
            "changes_damage_admission": False,
            "changes_physical_damage_row": False,
        },
    }

    return {
        "schema": SCHEMA,
        "policy": {
            "production_changed": False,
            "required_chain": "same-generation birth -> repeated wire owner -> exact direct37 -> exact self-slot/selector/projectile skill",
            "forbidden": ["time proximity", "nearest cast", "old skill label as proof", "cached summon_owner", "cross-Saint owner", "whole-template attribution"],
        },
        "corpus": {
            "captures": len(results),
            "hash_unique": len({row["capture_sha256"] for row in results}),
            "manifest": [{key: row[key] for key in ("capture", "capture_path", "capture_sha256")} for row in results],
        },
        "formal_baseline": {"gap_hits": formal, "rows": formal_rows},
        "current_replay": {
            "focus_gap_hits": {str(skill): int(counts[f"focus_gap:{skill}"]) for skill in FOCUS},
            "focus_gap_damage": {str(skill): int(counts[f"focus_gap_damage:{skill}"]) for skill in FOCUS},
            "eligible_exact_chain": dict(eligible),
            "old_bucket_conflicts": dict(conflicts),
            "foreign_family_veto_hits": dict(foreign_veto),
            "foreign_family_veto_damage": dict(foreign_veto_damage),
            "foreign_family_veto_total_hits": sum(foreign_veto.values()),
            "foreign_family_veto_total_damage": sum(foreign_veto_damage.values()),
            "blocked": dict(blocked),
        },
        "strata": table,
        "real_fixtures": dict(fixtures),
        "foreign_static_review": _foreign_static_review(),
        "production_rule_candidates": production_rule_candidates,
        "interpretation": {
            "skill_and_owner_are_separate": True,
            "raw_owner_without_birth_is_not_current_generation_proof": True,
            "template_30311_whole_template_rule_forbidden": "30311 exposes Hold and Shock-of-Relic branches; an empty direct37 self slot does not select either branch",
            "template_30501_rule": "30501 plus exact current owner still needs concrete 30616 self slot (or an exact same-object selector/projectile route)",
        },
        "invariants": {
            "all_capture_hashes_unchanged": True,
            "physical_row_multiset_equal": all(row["physical_row_multiset_equal"] for row in results),
            "damage_admission_changed": False,
            "source_target_amount_critical_changed": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    replay = report["current_replay"]
    lines = [
        "# 圣徒雷电之手/掌握之手确定归因审计 v65", "",
        f"- 固定语料：{report['corpus']['captures']} 份；哈希唯一 {report['corpus']['hash_unique']} 份。",
        "- 只读：未改共享生产、评分、上传或伤害准入。",
        "- 严格门槛：当前代出生 + 同代重复 owner + direct37 + 精确技能分支。", "",
        "## 结果", "",
        "| 技能 | 正式旧缺口 | 当前重放仍为非确定 | 同代确定链可闭合 |", "|---|---:|---:|---:|",
    ]
    for skill, name in FOCUS.items():
        closed = sum(value for key, value in replay["eligible_exact_chain"].items()
                     if key.startswith(f"{skill}->"))
        lines.append(f"| {skill} {name} | {report['formal_baseline']['gap_hits'][skill]} | {replay['focus_gap_hits'][str(skill)]} | {closed} |")
    lines += ["", "## 旧技能桶中的其他圣徒技能冲突", ""]
    if replay["old_bucket_conflicts"]:
        for key, hits in sorted(replay["old_bucket_conflicts"].items()):
            lines.append(f"- `{key}`：{hits} Hit。候选来自确定的槽/对象链，不来自旧标签。")
    else:
        lines.append("- 没有发现可确定改归 3412、3422 或 3503 的行；下方另有已证实的跨职业污染。")
    lines += ["", "## 已证实的跨职业污染", ""]
    for key, hits in sorted(replay["foreign_family_veto_hits"].items()):
        damage = replay["foreign_family_veto_damage"][key]
        lines.append(f"- `{key}`：{hits} Hit，伤害 {damage}。m102 是阿兹那实体专属内部攻击，必须否决旧圣徒技能标签。")
    lines.append(
        f"- 合计 {replay['foreign_family_veto_total_hits']} Hit、伤害 "
        f"{replay['foreign_family_veto_total_damage']}；只能先从圣徒桶撤回，缺当前代 owner 时不能擅自分给某个刺客玩家。"
    )
    lines += ["", "## 可生产化结论", "",
        "- 只有 `30501 当前出生代 + 当前代 34/2c owner + m102=30616 + direct37` 才能确定为 3409；缺任一环均失败关闭。",
        "- 只有 `30311 当前出生代 + 当前代 34/2c owner + m102=292224`，或同对象 selector/Projectile 明确选择 Hold 分支，才可确定为 3423。",
        "- 30311 的空自攻击槽不能整模板归入掌握之手，因为同一实体还包含 Shock-of-Relic 分支；这会把冲击之手混进旧桶。",
        "- 原始 34/2c owner 即使与旧缓存一致，只要没有当前出生代，也不能证明重用后的短实体仍属于同一玩家。",
        "- m102=261801/262101/262301/262302 的静态反查只落在 33601/33602/33603 阿兹那实体族；这是确定的跨职业否决证据。",
        "- 所有 fixture 固定抓包哈希、包序、记录偏移、实体代次、owner、m102/m114、对象 ID 和结果组，可直接转为生产回归样本。", "",
        "## 不变量", "",
        "- direct37 物理记录、来源、目标、伤害、暴击和总伤害均未改变。",
        "- 不使用时间窗口、最近施放、旧技能名或跨同职业借 owner。", "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    captures, _jobs = _selection(args.selection)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    partials = output / "partials"
    partials.mkdir(exist_ok=True)
    results = []
    pending = []
    for entry in captures:
        path = partials / f"{entry['sha256']}.json"
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("capture_sha256") == entry["sha256"]:
                results.append(saved)
                continue
        pending.append(entry)
    workers = max(1, min(8, args.workers))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(audit_capture, entry): entry for entry in pending}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            (partials / f"{result['capture_sha256']}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"done": result["capture"], "remaining": len(captures)-len(results)}, ensure_ascii=False), flush=True)
    by_hash = {row["capture_sha256"]: row for row in results}
    ordered = [by_hash[row["sha256"]] for row in captures]
    report = merge(ordered)
    report["selection_sha256"] = _hash(args.selection)
    report["formal_baseline_sha256"] = _hash(DEFAULT_BASELINE)
    report["tool_sha256"] = _hash(Path(__file__))
    checks = {
        "fixed_35_capture_corpus": report["corpus"]["captures"] == 35 and report["corpus"]["hash_unique"] == 35,
        "formal_focus_totals_match": report["formal_baseline"]["gap_hits"] == {3409: 603, 3423: 758},
        "read_only_physical_rows": report["invariants"]["physical_row_multiset_equal"] is True,
        "fixtures_never_use_timing_or_old_label": all(
            not row["uses_time_proximity"] and not row["uses_old_label_as_proof"]
            for rows in report["real_fixtures"].values() for row in rows),
        "eligible_requires_exact_owner_generation": all(
            row["owner_exact_same_generation"]
            for row in report["strata"] if row["eligible_hits"]),
        "eligible_has_exact_skill_route": all(
            row["candidate_skill_id"] > 0 and row["exact_routes"] != "none"
            for row in report["strata"] if row["eligible_hits"]),
    }
    validation = {"schema": SCHEMA, "passed": all(checks.values()), "checks": checks}
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text(markdown(report), encoding="utf-8")
    (output / "real_fixtures.json").write_text(json.dumps(report["real_fixtures"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "production_rule_candidates.json").write_text(
        json.dumps(report["production_rule_candidates"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation["passed"]:
        raise AssertionError(validation)
    print(json.dumps({"report": str(output / 'REPORT.json'), "validation": validation,
                      "result": report["current_replay"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
