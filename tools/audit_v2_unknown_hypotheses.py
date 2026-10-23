"""Audit user-supplied skill hypotheses against the fixed V2 corpus.

This tool is deliberately read-only.  It replays the exact capture manifest,
uses the production evidence_v2 reconciler, and reports wire/object/entity
facts for unresolved direct damage.  Legacy names and timing are retained only
as diagnostics; neither can produce a proposed skill identity.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    PhysicalHit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_direct_damage_by_profession import _scope_reason  # noqa: E402
from tools.audit_native_meter_skill_key_v50 import _native_result_fields  # noqa: E402
from tools.audit_under95_native_joint_keys_v55 import _origin_state  # noqa: E402
SCHEMA = "v2-unknown-hypotheses-v1"
DEFAULT_BASELINE = (
    ROOT / "outputs/direct_damage_v2_fixed36_20260911c/"
    "profession_direct_damage_report.json"
)
DEFAULT_OUTPUT = ROOT / "outputs/v2_unknown_hypotheses_fixed36_20260911"
TARGET_JOBS = (
    "圣骑士",
    "十字军",
    "黑暗萨满",
    "影",
    "机械大师",
    "雷神",
    "噬魂者",
    "圣徒",
)
HYPOTHESES = {
    "圣骑士": (("3205", "神圣惩戒"),),
    "十字军": (("3202", "雷电冲击"),),
    "圣徒": (("3409", "雷电之手"), ("3403", "治愈之手")),
    "雷神": (("3407", "闪电风暴"),),
    "机械大师": (("48001", "机械链锯"),),
    "黑暗萨满": (("5207", "憎恶之境"),),
    "噬魂者": (("5207", "憎恶之境"),),
    "影": (("6401", "天昏地暗"),),
}
EMPTY_U32 = frozenset({0, 0xFFFFFFFF})


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _event_key(event: CombatEvent) -> tuple[Any, ...]:
    return (
        int(event.timestamp_ms),
        int(event.wire_record_offset),
        int(event.wire_source_actor_id),
        int(event.wire_target_actor_id),
        int(event.wire_damage_object_id),
        int(event.amount),
        bool(event.wire_critical if event.wire_critical is not None else event.critical),
    )


def _event_map(events: list[object]) -> dict[tuple[Any, ...], list[CombatEvent]]:
    rows: dict[tuple[Any, ...], list[CombatEvent]] = defaultdict(list)
    for event in events:
        if isinstance(event, CombatEvent) and event.wire_record_offset > 0:
            rows[_event_key(event)].append(event)
    return rows


def _physical_key(timestamp_ms: int, physical: object) -> tuple[Any, ...]:
    return (
        int(timestamp_ms),
        int(physical.record_offset),
        int(physical.source_actor_id),
        int(physical.target_actor_id),
        int(physical.damage_object_id or 0),
        int(physical.damage),
        bool(physical.critical),
    )


def _entity_generation(event: CombatEvent) -> int:
    evidence = str(event.entity_owner_evidence_id or "")
    if evidence.startswith("34:"):
        try:
            return int(evidence.split(":", 2)[1])
        except (IndexError, ValueError):
            pass
    return 0


def _player_root_generation(
    decoder: object,
    physical: object,
    packet_serial: int,
) -> dict[str, int] | None:
    """Read the exact retained generation used by the production ledger."""
    ledger = getattr(decoder, "player_root_action_objects", None)
    generations = getattr(ledger, "_generations", {})
    rows = generations.get(
        (
            int(physical.source_actor_id),
            int(physical.damage_object_id or 0),
        ),
        (),
    )
    boundary_offset = int(physical.record_offset)
    generation = next(
        (
            row
            for row in reversed(rows)
            if int(row.packet_serial) < packet_serial
            or (
                int(row.packet_serial) == packet_serial
                and int(row.record_offset) <= boundary_offset
            )
        ),
        None,
    )
    if generation is None:
        return None
    return {
        "template": int(generation.config_row),
        "generation": int(generation.generation),
        "action": int(generation.action_index),
        "signal": int(generation.signal_key),
        "record_type": 9,
    }


def _wire_fact(
    event: CombatEvent,
    physical: object,
    packet: object,
    decoder: object,
    serial: int,
    player_object: dict[str, Any] | None,
) -> dict[str, Any]:
    native = _native_result_fields(physical)
    origin, origin_current = _origin_state(
        decoder, physical, float(packet.timestamp_seconds), serial
    )
    self_slot = None
    source = int(physical.source_actor_id)
    if physical.family == "direct37" and 0 < source < 0x01000000:
        hit = PhysicalHit(
            capture="",
            physical_hit_id="",
            order=(serial, 0, int(physical.record_offset)),
            entity_id=source,
            generation=_entity_generation(event),
            template_id=int(event.entity_template_id or 0),
            target_id=int(physical.target_actor_id),
            damage=int(physical.damage),
            object_id=int(physical.damage_object_id or 0),
            result_group=int(physical.damage_result_group_id or 0),
            wire=bytes(packet.payload),
            direction=str(packet.inferred_direction()),
        )
        self_slot = read_self_attack_slot(hit)
    return {
        "packet_serial": serial,
        "family": str(physical.family),
        "primary90": int(native["primary_raw"]),
        "primary90_read": bool(native["primary_read"]),
        "fallback_a4": int(native["fallback_raw"]),
        "origin_template": int(origin.projectile_template_id if origin else 0),
        "origin_generation": int(origin.generation if origin else 0),
        "origin_current": bool(origin_current),
        "player_object_template": int(
            player_object["template"] if player_object else 0
        ),
        "player_object_generation": int(
            player_object["generation"] if player_object else 0
        ),
        "player_object_action": int(
            player_object["action"] if player_object else 0
        ),
        "player_object_signal": int(
            player_object["signal"] if player_object else 0
        ),
        "player_object_record_type": int(
            player_object["record_type"] if player_object else 0
        ),
        "self_skill": int(
            self_slot.monster_skill_id
            if self_slot and self_slot.monster_skill_id not in EMPTY_U32
            else 0
        ),
        "self_context": int(
            self_slot.context_skill_id
            if self_slot and self_slot.context_skill_id not in EMPTY_U32
            else 0
        ),
    }


def _audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["capture_path"])
    if not path.is_file() or _hash_file(path) != entry["capture_sha256"]:
        raise ValueError(f"capture missing or hash changed: {path}")
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    wire_facts: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    def feed_events(packet):
        events = original(packet)
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            return events
        serial = int(decoder._damage_object_packet_serial)
        timestamp_ms = max(
            0,
            round(
                (float(packet.timestamp_seconds) - float(decoder.first_timestamp or 0.0))
                * 1000.0
            ),
        )
        event_rows = _event_map(events)
        for physical in inventory.damage_records:
            if not physical.admissible_fields:
                continue
            key = _physical_key(timestamp_ms, physical)
            candidates = event_rows.get(key, ())
            if len(candidates) != 1:
                continue
            wire_facts[key].append(
                _wire_fact(
                    candidates[0], physical, packet, decoder, serial,
                    _player_root_generation(decoder, physical, serial),
                )
            )
        return events

    decoder.feed_events = feed_events
    try:
        source_events = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)

    casts: Counter[tuple[str, str, str, str]] = Counter()
    for cast in snapshot.skill_cast_events:
        job = str(cast.job or "").strip()
        if job not in TARGET_JOBS:
            continue
        casts[(job, str(cast.skill_id), str(cast.skill_name), str(cast.packet_kind))] += 1

    events_out: list[dict[str, Any]] = []
    healing: Counter[tuple[str, str, str]] = Counter()
    for event in snapshot.combat_events:
        if not isinstance(event, CombatEvent) or event.job not in TARGET_JOBS:
            continue
        if event.healing:
            healing[(event.job, str(event.skill_id), str(event.skill_name))] += 1
            continue
        if _scope_reason(event) != "included_positive_direct_hit":
            continue
        key = _event_key(event)
        candidates = wire_facts.get(key, ())
        fact = {
            "packet_serial": 0,
            "family": "",
            "primary90": 0,
            "primary90_read": False,
            "fallback_a4": 0,
            "origin_template": 0,
            "origin_generation": 0,
            "origin_current": False,
            "player_object_template": 0,
            "player_object_generation": 0,
            "player_object_action": 0,
            "player_object_signal": 0,
            "player_object_record_type": 0,
            "self_skill": 0,
            "self_context": 0,
            **(candidates.pop(0) if candidates else {}),
        }
        events_out.append(
            {
                "capture": path.name,
                "capture_sha256": entry["capture_sha256"],
                "timestamp_ms": int(event.timestamp_ms),
                "source_id": str(event.source_id),
                "target_id": str(event.target_id),
                "job": str(event.job),
                "skill_id": str(event.skill_id),
                "skill_name": str(event.skill_name),
                "method": str(event.attribution_method),
                "amount": int(event.amount),
                "raw_skill_id": str(event.raw_skill_id),
                "raw_skill_name": str(event.raw_skill_name),
                "raw_method": str(event.raw_attribution_method),
                "cast_instance_id": str(event.cast_instance_id),
                "damage_object_id": int(event.wire_damage_object_id),
                "wire_entity_id": int(event.wire_entity_id),
                "entity_template": int(event.entity_template_id),
                "entity_owner_evidence": str(event.entity_owner_evidence_id),
                "entity_monster_skill": int(event.entity_monster_skill_id),
                "entity_attack_skill": int(event.entity_attack_skill_id),
                "entity_attack_evidence": str(event.entity_attack_evidence_id),
                **fact,
            }
        )
    return {
        "capture": path.name,
        "capture_sha256": entry["capture_sha256"],
        "events": events_out,
        "casts": [
            {"job": key[0], "skill_id": key[1], "skill_name": key[2],
             "packet_kind": key[3], "count": count}
            for key, count in casts.items()
        ],
        "healing": [
            {"job": key[0], "skill_id": key[1], "skill_name": key[2],
             "hits": count}
            for key, count in healing.items()
        ],
    }


def _signature_rows(event: dict[str, Any]):
    if event["primary90_read"] and event["primary90"] not in EMPTY_U32:
        yield "primary90", str(event["primary90"])
    if event["fallback_a4"] not in EMPTY_U32:
        yield "fallback_a4", str(event["fallback_a4"])
    if event["origin_current"] and event["origin_template"]:
        yield "origin_template", str(event["origin_template"])
    if event["entity_template"] and event["entity_monster_skill"]:
        yield "entity_monster_skill", (
            f"{event['entity_template']}:{event['entity_monster_skill']}"
        )
    if event["entity_template"] and event["entity_attack_skill"]:
        yield "entity_attack_skill", (
            f"{event['entity_template']}:{event['entity_attack_skill']}"
        )
    if event["entity_template"] and event["self_skill"]:
        yield "entity_self_skill", f"{event['entity_template']}:{event['self_skill']}"


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    events = [event for result in results for event in result["events"]]
    casts: Counter[tuple[str, str, str]] = Counter()
    cast_kinds: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for result in results:
        for row in result["casts"]:
            key = (row["job"], row["skill_id"], row["skill_name"])
            casts[key] += int(row["count"])
            cast_kinds[key][row["packet_kind"]] += int(row["count"])

    totals: dict[str, Counter[str]] = defaultdict(Counter)
    skills: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    methods: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    captures: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    unknown_shapes: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    unknown_captures: dict[tuple[Any, ...], set[str]] = defaultdict(set)

    # Only fixed packet fields are used as independent truth for matching an
    # unresolved structural signature. Object/entity methods would otherwise
    # circularly validate their own mappings.
    truth: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    truth_captures: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    truth_actors: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for event in events:
        job = event["job"]
        amount = int(event["amount"])
        totals[job]["hits"] += 1
        totals[job]["damage"] += amount
        unresolved = event["skill_id"] in {"", "-1"}
        totals[job]["unknown_hits"] += int(unresolved)
        totals[job]["unknown_damage"] += amount if unresolved else 0
        if unresolved:
            shape = (
                job,
                event["raw_skill_id"], event["raw_skill_name"], event["raw_method"],
                event["origin_template"] if event["origin_current"] else 0,
                event["player_object_template"],
                event["entity_template"], event["entity_monster_skill"],
                event["entity_attack_skill"], event["self_skill"],
                bool(event["entity_owner_evidence"]), bool(event["cast_instance_id"]),
                bool(event["damage_object_id"]), event["player_object_action"],
                event["player_object_signal"], event["player_object_record_type"],
            )
            unknown_shapes[shape]["hits"] += 1
            unknown_shapes[shape]["damage"] += amount
            unknown_captures[shape].add(event["capture"])
        else:
            key = (job, event["skill_id"], event["skill_name"])
            skills[key]["hits"] += 1
            skills[key]["damage"] += amount
            methods[key][event["method"]] += 1
            captures[key].add(event["capture"])
            if event["method"] in {"packet_skill_id", "packet_native_attack_key"}:
                for kind, value in _signature_rows(event):
                    signature = (job, kind, value)
                    truth[signature][event["skill_id"]] += 1
                    scoped = (*signature, event["skill_id"])
                    truth_captures[scoped].add(event["capture"])
                    truth_actors[scoped].add(event["source_id"])

    skill_rows = []
    for key, counts in skills.items():
        total_damage = totals[key[0]]["damage"]
        cast_key = key
        skill_rows.append(
            {
                "job": key[0], "skill_id": key[1], "skill_name": key[2],
                "casts": int(casts[cast_key]),
                "cast_packet_kinds": dict(cast_kinds[cast_key]),
                "hits": int(counts["hits"]), "damage": int(counts["damage"]),
                "damage_share_percent": round(
                    100.0 * counts["damage"] / max(1, total_damage), 6
                ),
                "capture_count": len(captures[key]),
                "methods": dict(methods[key]),
            }
        )
    for job, counts in totals.items():
        skill_rows.append(
            {
                "job": job, "skill_id": "-1", "skill_name": "待识别伤害",
                "casts": 0, "cast_packet_kinds": {},
                "hits": int(counts["unknown_hits"]),
                "damage": int(counts["unknown_damage"]),
                "damage_share_percent": round(
                    100.0 * counts["unknown_damage"] / max(1, counts["damage"]), 6
                ),
                "capture_count": sum(
                    1 for result in results
                    if any(e["job"] == job and e["skill_id"] in {"", "-1"}
                           for e in result["events"])
                ),
                "methods": {"v2_unresolved": int(counts["unknown_hits"])},
            }
        )
    skill_rows.sort(key=lambda row: (TARGET_JOBS.index(row["job"]), -row["damage"]))

    proposals: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    proposal_hits: Counter[tuple[str, str, str, str]] = Counter()
    for event in events:
        if event["skill_id"] not in {"", "-1"}:
            continue
        for kind, value in _signature_rows(event):
            signature = (event["job"], kind, value)
            skills_for_key = truth.get(signature, Counter())
            if len(skills_for_key) != 1:
                continue
            skill_id, truth_hits = next(iter(skills_for_key.items()))
            scoped = (*signature, skill_id)
            if (
                truth_hits < 8
                or len(truth_captures[scoped]) < 2
                or len(truth_actors[scoped]) < 2
            ):
                continue
            proposals[(event["job"], skill_id)].add((kind, value))
            proposal_hits[(event["job"], skill_id, kind, value)] += 1

    proposal_rows = [
        {
            "job": key[0], "skill_id": key[1], "signature_kind": key[2],
            "signature_value": key[3], "unknown_hits": count,
            "truth_hits": int(truth[(key[0], key[2], key[3])][key[1]]),
            "truth_capture_count": len(truth_captures[key]),
            "truth_actor_count": len(truth_actors[key]),
        }
        for key, count in proposal_hits.items()
    ]
    proposal_rows.sort(key=lambda row: (-row["unknown_hits"], row["job"]))

    shape_rows = []
    for key, counts in unknown_shapes.items():
        shape_rows.append(
            {
                "job": key[0], "raw_skill_id": key[1], "raw_skill_name": key[2],
                "raw_method": key[3], "origin_template": key[4],
                "player_object_template": key[5],
                "entity_template": key[6], "entity_monster_skill": key[7],
                "entity_attack_skill": key[8], "self_skill": key[9],
                "has_owner_evidence": key[10], "has_cast_instance": key[11],
                "has_damage_object": key[12], "player_object_action": key[13],
                "player_object_signal": key[14], "player_object_record_type": key[15],
                "hits": int(counts["hits"]),
                "damage": int(counts["damage"]),
                "capture_count": len(unknown_captures[key]),
                "captures": sorted(unknown_captures[key]),
            }
        )
    shape_rows.sort(key=lambda row: (TARGET_JOBS.index(row["job"]), -row["hits"]))

    healing = Counter()
    for result in results:
        for row in result["healing"]:
            healing[(row["job"], row["skill_id"], row["skill_name"])] += int(row["hits"])

    hypothesis_rows = []
    skill_lookup = {(r["job"], r["skill_id"]): r for r in skill_rows}
    for job, hypotheses in HYPOTHESES.items():
        for skill_id, skill_name in hypotheses:
            known = skill_lookup.get((job, skill_id), {})
            supported = sum(
                row["unknown_hits"] for row in proposal_rows
                if row["job"] == job and row["skill_id"] == skill_id
            )
            raw_only = sum(
                row["hits"] for row in shape_rows
                if row["job"] == job and row["raw_skill_id"] == skill_id
            )
            hypothesis_rows.append(
                {
                    "job": job, "skill_id": skill_id, "skill_name": skill_name,
                    "known_deterministic_hits": int(known.get("hits", 0)),
                    "known_damage_share_percent": float(
                        known.get("damage_share_percent", 0.0)
                    ),
                    "factual_cast_events": int(known.get("casts", casts[(job, skill_id, skill_name)])),
                    "strong_signature_unknown_hits": int(supported),
                    "legacy_label_only_unknown_hits": int(raw_only),
                    "healing_events": int(healing[(job, skill_id, skill_name)]),
                }
            )

    return {
        "totals": {job: dict(counts) for job, counts in totals.items()},
        "skill_details": skill_rows,
        "unknown_shapes": shape_rows,
        "strong_signature_proposals": proposal_rows,
        "hypotheses": hypothesis_rows,
        "healing": [
            {"job": key[0], "skill_id": key[1], "skill_name": key[2], "hits": count}
            for key, count in sorted(healing.items())
        ],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# V2 待识别技能假设审计（固定36包）", "",
        "口径：只统计 V2 正向直接 Hit。旧技能名、伤害形状和时间邻近仅作诊断，不能决定技能名称。释放次数只统计 V2 保留的事实 SkillCastEvent。", "",
        "## 假设核对", "",
        "|职业|假设技能|事实释放次数|已确定Hit|已确定伤害占比|强结构键可闭合未知Hit|仅旧标签相似Hit|治疗事件|", 
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in aggregate["hypotheses"]:
        lines.append(
            f"|{row['job']}|{row['skill_id']} {row['skill_name']}|"
            f"{row['factual_cast_events']:,}|{row['known_deterministic_hits']:,}|"
            f"{row['known_damage_share_percent']:.3f}%|"
            f"{row['strong_signature_unknown_hits']:,}|"
            f"{row['legacy_label_only_unknown_hits']:,}|{row['healing_events']:,}|"
        )
    lines += ["", "## 各职业技能详情", ""]
    for job in TARGET_JOBS:
        total = aggregate["totals"].get(job, {})
        lines += [
            f"### {job}", "",
            f"总直接 Hit {int(total.get('hits', 0)):,}；待识别 {int(total.get('unknown_hits', 0)):,}；"
            f"待识别伤害占比 {100.0 * int(total.get('unknown_damage', 0)) / max(1, int(total.get('damage', 0))):.3f}%。", "",
            "|技能|事实释放次数|Hit|伤害|伤害占比|证据方法|", 
            "|---|---:|---:|---:|---:|---|",
        ]
        for row in aggregate["skill_details"]:
            if row["job"] != job:
                continue
            methods = ", ".join(
                f"{key}:{value}" for key, value in sorted(row["methods"].items())
            )
            lines.append(
                f"|{row['skill_id']} {row['skill_name']}|{row['casts']:,}|"
                f"{row['hits']:,}|{row['damage']:,}|{row['damage_share_percent']:.3f}%|"
                f"{methods}|"
            )
        lines.append("")
    lines += ["## 当前可由独立固定字段交叉验证的未知子集", ""]
    if not aggregate["strong_signature_proposals"]:
        lines.append("固定36包中没有未知 Hit 同时满足：同职业唯一技能、固定字段真值至少8 Hit、至少2包且至少2个来源角色。")
    else:
        lines += [
            "|职业/技能|结构键|未知Hit|独立真值Hit|抓包|角色|", 
            "|---|---|---:|---:|---:|---:|",
        ]
        for row in aggregate["strong_signature_proposals"]:
            lines.append(
                f"|{row['job']} / {row['skill_id']}|"
                f"{row['signature_kind']}={row['signature_value']}|"
                f"{row['unknown_hits']:,}|{row['truth_hits']:,}|"
                f"{row['truth_capture_count']}|{row['truth_actor_count']}|"
            )
    lines += [
        "", "## 判定边界", "",
        "- `仅旧标签相似Hit` 不构成确认；它只说明旧解码器曾把该行猜成该技能。",
        "- `强结构键可闭合未知Hit` 仍需逐条检查对象/实体代次边界后，才可进入生产规则。",
        "- 治疗事件单独统计，不进入正向直接伤害 Hit 或伤害占比。",
    ]
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        parser.error("workers must be between 1 and 6")

    baseline = json.loads(args.baseline.read_text(encoding="utf-8-sig"))
    manifest = [
        {
            "capture_path": str(row["capture_path"]),
            "capture_sha256": str(row["capture_sha256"]),
        }
        for row in baseline["capture_manifest"]
    ]
    if len(manifest) != 36 or len({row["capture_sha256"] for row in manifest}) != 36:
        parser.error("baseline is not the fixed unique 36-capture corpus")

    results = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    partials = args.output_dir / "partials"
    partials.mkdir(exist_ok=True)
    pending = []
    for row in manifest:
        partial = partials / f"{row['capture_sha256']}.json"
        if partial.is_file():
            saved = json.loads(partial.read_text(encoding="utf-8"))
            if saved.get("capture_sha256") == row["capture_sha256"]:
                results.append(saved)
                continue
        pending.append(row)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_audit_capture, row): row for row in pending}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            (partials / f"{result['capture_sha256']}.json").write_text(
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            print(f"[{len(results):02d}/36] {result['capture']}", flush=True)
    results.sort(key=lambda row: row["capture_sha256"])
    aggregate = _aggregate(results)
    report = {
        "schema": SCHEMA,
        "baseline": str(args.baseline.resolve()),
        "baseline_sha256": _hash_file(args.baseline),
        "capture_count": len(results),
        "policy": {
            "attribution_mode": "evidence_v2",
            "time_can_name_skill": False,
            "legacy_label_can_name_skill": False,
            "release_count_source": "normalized factual SkillCastEvent only",
        },
        "aggregate": aggregate,
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "REPORT.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    _write_csv(args.output_dir / "skill_details.csv", aggregate["skill_details"])
    _write_csv(args.output_dir / "unknown_shapes.csv", aggregate["unknown_shapes"])
    _write_csv(
        args.output_dir / "strong_signature_proposals.csv",
        aggregate["strong_signature_proposals"],
    )
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "captures": len(results),
        "unknown_hits": sum(
            int(row.get("unknown_hits", 0)) for row in aggregate["totals"].values()
        ),
        "proposal_rows": len(aggregate["strong_signature_proposals"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
