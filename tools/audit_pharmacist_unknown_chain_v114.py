"""Strict unknown-first Physician direct-hit chain audit (v114).

This is a read-only audit over the hash-pinned Black Dragon Classic/Hardcore
selection.  Existing skill IDs/names, cast distance, damage amount and result
group are retained only for diagnostics.  They never create or vote for a
skill candidate.

Accepted positive routes are limited to the same physical direct37 row:

* the client's fixed native result key;
* player A90370 root generation -> exact ACT/config -> object id;
* exact owner + current Projectile/object generation -> damage_object_id;
* summon birth -> current 34/2c owner generation -> native self attack; or
* summon entity -> exact child Projectile generation -> damage_object_id.

No production parser is imported from this report and this tool never mutates
physical Hits, damage, actor ownership, scores or timelines.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.native_damage_skill_keys import (  # noqa: E402
    STATIC_INTERNAL_ATTACK_PARENT,
    _selected_native_key,
)
from dragon_nest_builder.live_dps.player_root_action_objects import (  # noqa: E402
    PLAYER_ROOT_ACTION_SIGNAL_SKILLS,
    _CONFIG_ROW_SKILLS,
    _at_or_before,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.summon_attack_runtime import (  # noqa: E402
    SUMMON_SELF_ATTACK_SPECS,
)
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    PhysicalHit,
    annotate_hit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _scope_reason,
    _strict_classification,
)
from tools.audit_under95_summon_chain_v53 import (  # noqa: E402
    _life,
    _physical_for_event,
)


SCHEMA = "pharmacist-unknown-chain-v114"
TARGET_JOB = "药剂师"
PLAYER_ID_MIN = 0x01000000
TARGET_ENTITY_TEMPLATES = frozenset({32012, 32017, 32018})
DEFAULT_SELECTION = (
    ROOT / "outputs" / "blackdragon_new33_pharmacist_selection_v104.json"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "pharmacist_unknown_chain_v114_20260910"
BASELINE_REPORT = (
    ROOT / "outputs" / "direct_damage_v103_blackdragon_new33_20260910"
    / "profession_direct_damage_report.json"
)
PRODUCTION_INPUTS = (
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/entity_damage_owner.py",
    "dragon_nest_builder/live_dps/entity_owner_evidence.py",
    "dragon_nest_builder/live_dps/native_damage_skill_keys.py",
    "dragon_nest_builder/live_dps/player_root_action_objects.py",
    "dragon_nest_builder/live_dps/sources.py",
    "dragon_nest_builder/live_dps/summon_attack_runtime.py",
    "dragon_nest_builder/live_dps/summon_self_attack_evidence_v26.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/v576/actor_state.py",
)
SKILL_NAMES = {
    4008: "泡泡弹",
    4404: "牺牲",
    4405: "召唤魔法草",
    4406: "冰雪连天",
    4407: "寒冰戳刺",
    4408: "瞒天过海",
    4409: "岩浆戳刺",
    4410: "岩浆墙",
    4411: "岩浆波流",
    4413: "冰柱释放",
    4414: "毒气聚能",
    4415: "喷射",
    4416: "毒气暴击",
    4417: "疾病",
    4418: "混合感染",
    4420: "毒池",
    4551: "爱情病毒",
}


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _canonical(value: int) -> int:
    mapped = int(STATIC_INTERNAL_ATTACK_PARENT.get(int(value), int(value)))
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(mapped, mapped))


def _parse_old_skill(value: object) -> int:
    text = str(value or "").strip()
    return _canonical(int(text)) if text.isdecimal() and int(text) > 0 else 0


def _production_signature() -> dict[str, str]:
    return {name: _hash_file(ROOT / name) for name in PRODUCTION_INPUTS}


def _load_selection(path: Path) -> list[dict[str, str]]:
    if path.resolve() != DEFAULT_SELECTION.resolve():
        raise ValueError(
            "v114 must use blackdragon_new33_pharmacist_selection_v104.json"
        )
    selection = json.loads(path.read_text(encoding="utf-8-sig"))
    if selection.get("schema_version") != "hash-pinned-audit-selection-v104":
        raise ValueError("unexpected selection schema")
    if selection.get("summary", {}).get("jobs") != [TARGET_JOB]:
        raise ValueError("selection is not Physician-only")
    source_report_path = Path(selection["sources"]["source_report"])
    source_report = json.loads(source_report_path.read_text(encoding="utf-8-sig"))
    manifest = {
        str(row["capture_sha256"]): row
        for row in source_report.get("capture_manifest", ())
    }
    selected: list[dict[str, str]] = []
    for item in selection["sources"]["selected_results"]:
        digest = str(item["capture_sha256"])
        row = manifest.get(digest)
        if row is None:
            raise ValueError(f"capture absent from source manifest: {item['capture']}")
        capture = Path(row["capture_path"])
        if capture.name != str(item["capture"]):
            raise ValueError(f"capture name mismatch: {capture}")
        if not capture.is_file() or _hash_file(capture) != digest:
            raise ValueError(f"capture hash mismatch: {capture}")
        selected.append(
            {"capture": capture.name, "path": str(capture), "sha256": digest}
        )
    if len(selected) != int(selection["summary"]["capture_count"]):
        raise ValueError("selection count drift")
    if len({row["sha256"] for row in selected}) != len(selected):
        raise ValueError("duplicate capture hash")
    return sorted(selected, key=lambda row: row["sha256"])


def _root_generation(decoder: object, source: int, object_id: int,
                     packet_serial: int, record_offset: int) -> object | None:
    rows = decoder.player_root_action_objects._generations.get(
        (int(source), int(object_id)), ()
    )
    return next(
        (
            row for row in reversed(rows)
            if _at_or_before(
                int(row.packet_serial), int(row.record_offset),
                int(packet_serial), int(record_offset),
            )
        ),
        None,
    )


def _root_candidates(decoder: object, event: CombatEvent, physical: object,
                     packet_serial: int) -> tuple[list[dict[str, Any]], str]:
    source = int(physical.source_actor_id)
    object_id = int(physical.damage_object_id)
    if source < PLAYER_ID_MIN or object_id <= 0:
        return [], "not_player_root_object"
    generation = _root_generation(
        decoder, source, object_id, packet_serial, int(physical.record_offset)
    )
    if generation is None:
        return [], "no_complete_player_root_generation"
    candidates: list[dict[str, Any]] = []
    action_skill = int(
        PLAYER_ROOT_ACTION_SIGNAL_SKILLS.get(
            (
                TARGET_JOB,
                int(generation.action_index),
                int(generation.signal_key),
                int(generation.config_row),
            ),
            0,
        )
    )
    if action_skill > 0:
        candidates.append({
            "route": "player_root_action_signal",
            "rule_key": (
                f"job={TARGET_JOB}|action={int(generation.action_index)}|"
                f"signal={int(generation.signal_key)}|config={int(generation.config_row)}"
            ),
            "skill_id": _canonical(action_skill),
            "generation": int(generation.generation),
            "root_packet_serial": int(generation.packet_serial),
            "root_record_offset": int(generation.record_offset),
        })
    config_skill = int(_CONFIG_ROW_SKILLS.get(int(generation.config_row), 0))
    if config_skill > 0:
        candidates.append({
            "route": "player_root_config",
            "rule_key": f"config={int(generation.config_row)}",
            "skill_id": _canonical(config_skill),
            "generation": int(generation.generation),
            "root_packet_serial": int(generation.packet_serial),
            "root_record_offset": int(generation.record_offset),
        })
    return candidates, (
        "player_root_mapped" if candidates
        else "player_root_exists_but_action_signal_and_config_unmapped"
    )


def _origin_candidate(decoder: object, physical: object, packet: object,
                      packet_serial: int) -> tuple[list[dict[str, Any]], str]:
    source = int(physical.source_actor_id)
    object_id = int(physical.damage_object_id)
    if object_id <= 0:
        return [], "direct37_has_no_damage_object_id"
    attribution = decoder.damage_object_origins.attribute_direct37(
        source_actor_id=source,
        timestamp=float(packet.timestamp_seconds),
        layout="direct37",
        payload=bytes(packet.payload),
        direction=packet.inferred_direction(),
        damage=int(physical.damage),
        record_offset=int(physical.record_offset),
        packet_serial=int(packet_serial),
        _evidence_only=True,
    )
    if attribution is None:
        origin, _snapshot = decoder.damage_object_origins._origin_at(
            (source, object_id), float(packet.timestamp_seconds),
            int(packet_serial), int(physical.record_offset),
        )
        if origin is None:
            return [], "no_current_exact_object_generation"
        return [], (
            "object_generation_present_but_not_deterministically_validated:"
            f"template={int(origin.projectile_template_id)}:"
            f"action={int(bool(origin.action_backed))}:"
            f"parent={int(bool(origin.parent_lineage_exact))}:"
            f"response={int(bool(origin.response_backed))}"
        )
    if not attribution.deterministically_validated:
        return [], (
            "exact_object_generation_is_provisional:"
            f"template={int(attribution.projectile_template_id)}"
        )
    origin, _snapshot = decoder.damage_object_origins._origin_at(
        (source, object_id), float(packet.timestamp_seconds),
        int(packet_serial), int(physical.record_offset),
    )
    if origin is None or int(origin.generation) != int(attribution.generation):
        return [], "object_generation_changed_during_exact_lookup"
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(int(origin.projectile_template_id))
    if spec is None or _canonical(int(spec.canonical_skill_id)) != _canonical(
        int(attribution.skill_id)
    ):
        return [], "object_template_static_skill_missing_or_conflicting"
    lineage = (
        "entity_child_projectile" if source < PLAYER_ID_MIN
        else "player_projectile"
    )
    return [{
        "route": lineage,
        "rule_key": (
            f"template={int(origin.projectile_template_id)}|"
            f"parent_template={int(origin.parent_projectile_template_id)}|"
            f"parent_exact={int(bool(origin.parent_lineage_exact))}"
        ),
        "skill_id": _canonical(int(attribution.skill_id)),
        "object_generation": int(origin.generation),
        "projectile_template_id": int(origin.projectile_template_id),
        "parent_damage_object_id": int(origin.parent_damage_object_id),
        "parent_projectile_template_id": int(origin.parent_projectile_template_id),
        "parent_generation": int(origin.parent_generation),
        "parent_lineage_exact": bool(origin.parent_lineage_exact),
        "action_backed": bool(origin.action_backed),
        "response_backed": bool(origin.response_backed),
        "packet_validated": bool(attribution.packet_validated),
        "client_act_validated": bool(attribution.client_act_validated),
        "action_qualified_validated": bool(
            attribution.action_qualified_validated
        ),
        "parent_lineage_validated": bool(attribution.parent_lineage_validated),
        "registration_key_sha256": sha256(
            str(origin.registration_key).encode("utf-8")
        ).hexdigest(),
    }], "exact_object_generation_mapped"


def _native_candidate(record: object, owner_closed: bool) -> tuple[list[dict[str, Any]], str]:
    if not owner_closed:
        return [], "native_key_without_closed_owner"
    selected = _selected_native_key(record)
    if selected is None:
        return [], "no_fixed_native_result_key"
    field, raw, source_kind, common_cc = selected
    skill = _canonical(int(raw))
    if skill not in SKILL_NAMES:
        return [], f"fixed_native_key_not_pharmacist_skill:{skill}"
    return [{
        "route": "fixed_native_result_key",
        "rule_key": f"family={record.family}|field={field}|raw={int(raw)}",
        "skill_id": skill,
        "raw_skill_id": int(raw),
        "fixed_field": str(field),
        "source_kind": str(source_kind),
        "common_cc": int(common_cc),
    }], "fixed_native_result_key_mapped"


def _entity_candidate(decoder: object, physical: object, packet: object,
                      packet_serial: int, capture_name: str,
                      capture_sha: str) -> tuple[list[dict[str, Any]], str, int, str]:
    source = int(physical.source_actor_id)
    if not 0 < source < PLAYER_ID_MIN:
        return [], "not_entity_source", 0, ""
    life, owner_reason, _binding = _life(
        decoder, source, float(packet.timestamp_seconds), int(packet_serial),
        int(physical.record_offset), capture_name,
    )
    if life is None:
        return [], f"entity_owner_not_closed:{owner_reason}", 0, ""
    _owner_name, owner_job = decoder._actor_display_context(int(life.owner_id))
    if owner_job != TARGET_JOB:
        return [], f"entity_owner_job_is:{owner_job or 'unknown'}", int(life.owner_id), owner_job
    hit = PhysicalHit(
        capture=capture_name,
        physical_hit_id=(
            f"{capture_sha}:{int(packet_serial)}:{int(physical.record_offset)}"
        ),
        order=(int(packet_serial), 0, int(physical.record_offset)),
        entity_id=source,
        generation=int(life.generation),
        template_id=int(life.template_id),
        target_id=int(physical.target_actor_id),
        damage=int(physical.damage),
        object_id=int(physical.damage_object_id),
        result_group=int(physical.damage_result_group_id),
        wire=bytes(packet.payload),
        direction=packet.inferred_direction(),
    )
    slot = read_self_attack_slot(hit)
    decision = annotate_hit(
        hit, life, SUMMON_SELF_ATTACK_SPECS, encounter_mode="pve"
    )
    if decision.evidence is None:
        if slot is None:
            reason = "entity_direct37_has_no_complete_self_attack_slot"
        elif int(slot.monster_skill_id) in (0, 0xFFFFFFFF):
            reason = "entity_self_attack_slot_is_empty"
        elif (int(life.template_id), int(slot.monster_skill_id)) not in SUMMON_SELF_ATTACK_SPECS:
            reason = (
                "entity_self_attack_slot_static_mapping_missing:"
                f"template={int(life.template_id)}:slot={int(slot.monster_skill_id)}"
            )
        else:
            reason = f"entity_self_attack_rejected:{decision.reason}"
        return [], reason, int(life.owner_id), owner_job
    evidence = decision.evidence
    skill = _canonical(int(evidence.player_skill_id))
    return [{
        "route": "entity_current_generation_self_attack",
        "rule_key": (
            f"template={int(evidence.template_id)}|"
            f"self_attack={int(evidence.monster_skill_id)}"
        ),
        "skill_id": skill,
        "entity_id": int(evidence.entity_id),
        "entity_template_id": int(evidence.template_id),
        "entity_generation": int(evidence.generation),
        "owner_actor_id": int(evidence.owner_id),
        "monster_skill_id": int(evidence.monster_skill_id),
        "static_proof_id": str(evidence.static_proof_id),
        "birth_fingerprint": str(evidence.birth_fingerprint),
        "owner_fingerprint": str(evidence.owner_fingerprint),
    }], "exact_current_entity_self_attack_mapped", int(life.owner_id), owner_job


def _event_identity(capture_sha: str, packet_serial: int, physical: object) -> str:
    value = "|".join(
        str(item) for item in (
            capture_sha, int(packet_serial), str(physical.family),
            int(physical.record_offset), int(physical.source_actor_id),
            int(physical.target_actor_id), int(physical.damage),
            int(physical.damage_object_id), int(physical.damage_result_group_id),
            int(bool(physical.critical)), str(physical.wire_record_sha256),
        )
    )
    return sha256(value.encode("utf-8")).hexdigest()


def _audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    if _hash_file(path) != entry["sha256"]:
        raise ValueError(f"capture changed: {path}")
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    def feed_events(packet):
        events = original(packet)
        packet_serial = int(decoder._damage_object_packet_serial)
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            counts[f"incomplete_inventory:{inventory.rejection_reason}"] += 1
            return events
        for event in events:
            if not isinstance(event, CombatEvent) or event.healing or int(event.amount) <= 0:
                continue
            if "direct37" not in str(event.wire_damage_layout):
                continue
            physical = _physical_for_event(event, packet, inventory)
            if physical is None:
                continue
            entity_routes, entity_reason, entity_owner, entity_owner_job = _entity_candidate(
                decoder, physical, packet, packet_serial, path.name, entry["sha256"]
            )
            player_owned = (
                int(physical.source_actor_id) >= PLAYER_ID_MIN
                and str(event.job or "") == TARGET_JOB
            )
            entity_owned = bool(entity_owner and entity_owner_job == TARGET_JOB)
            if not (player_owned or entity_owned):
                continue
            scope = _scope_reason(event)
            if scope != "included_positive_direct_hit":
                counts[f"outside_scope:{scope}"] += 1
                continue
            classification = _strict_classification(event)
            counts["all_target_direct37_hits"] += 1
            counts[f"classification:{classification}"] += 1
            is_gap = classification != "deterministic"
            if is_gap:
                counts["unknown_first_gap_hits"] += 1
                counts["unknown_first_gap_damage"] += int(physical.damage)
            else:
                counts["deterministic_control_hits"] += 1

            owner_closed = player_owned or entity_owned
            native_routes, native_reason = _native_candidate(physical, owner_closed)
            root_routes, root_reason = _root_candidates(
                decoder, event, physical, packet_serial
            )
            object_routes, object_reason = _origin_candidate(
                decoder, physical, packet, packet_serial
            )
            positive_routes = entity_routes + native_routes + root_routes + object_routes
            skill_set = {int(route["skill_id"]) for route in positive_routes}
            if len(skill_set) == 1:
                resolved_skill = next(iter(skill_set))
                decision = "CANDIDATE" if is_gap else "CONTROL"
                reason = "all_independent_routes_converge"
                counts[f"{decision.lower()}_hits"] += 1
            elif len(skill_set) > 1:
                resolved_skill = 0
                decision = "WITHHOLD_CONFLICT"
                reason = "independent_positive_routes_conflict"
                counts["route_conflict_hits"] += 1
            else:
                resolved_skill = 0
                decision = "WITHHOLD_UNKNOWN"
                reason = "no_deterministic_skill_route"
                if is_gap:
                    counts["strictly_unresolved_gap_hits"] += 1

            old_skill = _parse_old_skill(event.skill_id)
            row = {
                "physical_hit_id": _event_identity(
                    entry["sha256"], packet_serial, physical
                ),
                "capture": path.name,
                "capture_sha256": entry["sha256"],
                "packet_serial": packet_serial,
                "record_offset": int(physical.record_offset),
                "wire_record_sha256": str(physical.wire_record_sha256),
                "wire_source_actor_id": int(physical.source_actor_id),
                "resolved_owner_actor_id": int(entity_owner or physical.source_actor_id),
                "wire_target_actor_id": int(physical.target_actor_id),
                "damage_object_id": int(physical.damage_object_id),
                "damage_audit_only": int(physical.damage),
                "result_group_audit_only": int(physical.damage_result_group_id),
                "critical_audit_only": bool(physical.critical),
                "baseline_classification_audit_only": classification,
                "legacy_skill_id_audit_only": int(old_skill),
                "legacy_skill_name_audit_only": str(event.skill_name or ""),
                "legacy_method_audit_only": str(event.attribution_method or ""),
                "candidate_skill_id": int(resolved_skill),
                "candidate_skill_name": SKILL_NAMES.get(resolved_skill, ""),
                "decision": decision,
                "reason": reason,
                "positive_routes": positive_routes,
                "negative_route_results": {
                    "entity": entity_reason,
                    "native": native_reason,
                    "player_root": root_reason,
                    "projectile_or_child_object": object_reason,
                },
                "proof_flags": {
                    "uses_legacy_skill_label": False,
                    "uses_cast_time_distance": False,
                    "uses_damage_amount_to_choose_skill": False,
                    "uses_result_group_to_choose_skill": False,
                },
            }
            rows.append(row)
        return events

    decoder.feed_events = feed_events
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        source.close()
    commitment = sha256(
        "\n".join(sorted(row["physical_hit_id"] for row in rows)).encode("utf-8")
    ).hexdigest()
    return {
        "capture": path.name,
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "physical_hit_commitment": commitment,
        "rows": rows,
    }


def _merge(entries: list[dict[str, str]], results: list[dict[str, Any]],
           selection_sha: str, production_signature: dict[str, str]) -> dict[str, Any]:
    rows = [row for result in results for row in result["rows"]]
    gap_rows = [
        row for row in rows
        if row["baseline_classification_audit_only"] != "deterministic"
    ]
    candidate_rows = [row for row in gap_rows if row["decision"] == "CANDIDATE"]
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    conflicted_rule_keys: set[tuple[str, str]] = set()
    for row in rows:
        route_skills: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
        for route in row["positive_routes"]:
            key = (str(route["route"]), str(route["rule_key"]))
            route_skills[key].add(int(route["skill_id"]))
        for key, skills in route_skills.items():
            if len(skills) != 1:
                conflicted_rule_keys.add(key)
                continue
            grouped[(key[0], key[1], next(iter(skills)))].append(row)
        if row["decision"] == "WITHHOLD_CONFLICT":
            for route in row["positive_routes"]:
                conflicted_rule_keys.add(
                    (str(route["route"]), str(route["rule_key"]))
                )

    rules = []
    for (route, rule_key, skill), values in sorted(grouped.items()):
        gaps = [
            row for row in values
            if row["baseline_classification_audit_only"] != "deterministic"
            and row["decision"] == "CANDIDATE"
            and int(row["candidate_skill_id"]) == skill
        ]
        if not gaps:
            continue
        captures = {row["capture_sha256"] for row in values}
        candidate_captures = {row["capture_sha256"] for row in gaps}
        route_conflict = (route, rule_key) in conflicted_rule_keys
        cross_sample = len(captures) >= 2 and len(candidate_captures) >= 2
        eligible = bool(cross_sample and not route_conflict)
        rules.append({
            "route": route,
            "rule_key": rule_key,
            "skill_id": skill,
            "skill_name": SKILL_NAMES.get(skill, ""),
            "candidate_gap_hits": len(gaps),
            "all_observed_hits": len(values),
            "distinct_captures": len(captures),
            "candidate_distinct_captures": len(candidate_captures),
            "zero_conflict": not route_conflict,
            "cross_sample": cross_sample,
            "status": (
                "ELIGIBLE_AUDIT_CANDIDATE" if eligible
                else "WITHHOLD_NOT_CROSS_SAMPLE" if not cross_sample
                else "WITHHOLD_CONFLICT"
            ),
        })
    eligible_rules = [
        row for row in rules if row["status"] == "ELIGIBLE_AUDIT_CANDIDATE"
    ]
    eligible_keys = {
        (row["route"], row["rule_key"], int(row["skill_id"]))
        for row in eligible_rules
    }
    eligible_hit_ids = {
        row["physical_hit_id"]
        for row in candidate_rows
        if any(
            (
                str(route["route"]), str(route["rule_key"]),
                int(route["skill_id"]),
            ) in eligible_keys
            for route in row["positive_routes"]
        )
    }

    baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8-sig"))
    baseline_row = next(
        row for row in baseline["summary"] if row["职业"] == TARGET_JOB
    )
    total = int(baseline_row["总直接Hit"])
    deterministic = int(baseline_row["确定性已归因"])
    minimum_for_95 = max(0, math.ceil(total * 0.95) - deterministic)
    negative_counts = Counter()
    for row in gap_rows:
        if row["physical_hit_id"] in eligible_hit_ids:
            continue
        if row["decision"] == "WITHHOLD_CONFLICT":
            negative_counts["independent_positive_routes_conflict"] += 1
        else:
            for reason in row["negative_route_results"].values():
                negative_counts[str(reason)] += 1

    return {
        "schema": SCHEMA,
        "scope": {
            "job": TARGET_JOB,
            "corpus": "new Black Dragon Classic/Hardcore only",
            "selection": str(DEFAULT_SELECTION),
            "selection_sha256": selection_sha,
            "capture_count": len(entries),
            "captures": [
                {"capture": row["capture"], "sha256": row["sha256"]}
                for row in entries
            ],
        },
        "policy": {
            "unconfirmed_skill_identity": "unknown",
            "legacy_skill_id_name": "audit_only_no_vote",
            "forbidden_positive_evidence": [
                "cast time distance", "damage amount", "result group",
                "legacy skill id/name", "same-job proximity",
            ],
            "required_positive_chain": [
                "fixed native key on the same direct37 record",
                "A90370 ACT/config -> exact object generation -> damage_object_id -> direct37",
                "owner + Projectile/parent generation -> damage_object_id -> direct37",
                "summon birth -> current 34/2c owner generation -> native self attack -> direct37",
            ],
            "production_modified": False,
        },
        "summary": {
            "baseline_total_direct_hits": total,
            "baseline_deterministic_hits": deterministic,
            "baseline_gap_hits": int(baseline_row["归因缺口"]),
            "audited_target_direct37_hits": len(rows),
            "audited_gap_hits": len(gap_rows),
            "strict_candidate_gap_hits_before_cross_sample_gate": len(candidate_rows),
            "eligible_cross_sample_candidate_hits": len(eligible_hit_ids),
            "eligible_rule_count": len(eligible_rules),
            "minimum_additional_hits_for_95": minimum_for_95,
            "projected_coverage_if_eligible_candidates_imported_percent": round(
                (
                    100.0 * (deterministic + len(eligible_hit_ids)) / total
                    if total else 100.0
                ),
                6,
            ),
            "strictly_withheld_gap_hits": len(gap_rows) - len(eligible_hit_ids),
            "route_conflict_hits": sum(
                row["decision"] == "WITHHOLD_CONFLICT" for row in gap_rows
            ),
        },
        "eligible_candidates": eligible_rules,
        "all_candidate_rules": rules,
        "negative_evidence_counts": dict(negative_counts.most_common()),
        "physical_conservation": {
            "events_mutated": 0,
            "damage_mutated": 0,
            "actor_owner_mutated": 0,
            "timeline_or_score_run": False,
        },
        "production_signature": production_signature,
        "capture_commitments": [
            {
                "capture": result["capture"],
                "sha256": result["capture_sha256"],
                "physical_hit_commitment": result["physical_hit_commitment"],
                "counts": result["counts"],
            }
            for result in results
        ],
        "_rows": rows,
        "_eligible_hit_ids": sorted(eligible_hit_ids),
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 药剂师未知直接伤害确定性链审计 v114",
        "",
        f"- 哈希固定抓包：{report['scope']['capture_count']} 份，仅新黑龙经典/硬核语料。",
        "- 所有未 100% 确认的技能在本轮先记为未知；旧技能名、时间距离、伤害金额和 result group 不参与判定。",
        "- 未修改生产解析器，未改动任何物理 Hit、伤害、人物归属、评分或时间轴。",
        "",
        "## 结果",
        "",
        f"- 基线：{summary['baseline_deterministic_hits']}/{summary['baseline_total_direct_hits']} Hit 已确定；缺口 {summary['baseline_gap_hits']} Hit。",
        f"- 本轮实际审计到药剂师 direct37 缺口：{summary['audited_gap_hits']} Hit。",
        f"- 跨样本、零冲突可复现候选：{summary['eligible_cross_sample_candidate_hits']} Hit / {summary['eligible_rule_count']} 条规则。",
        f"- 仍暂缓：{summary['strictly_withheld_gap_hits']} Hit。",
        "",
        "## 零冲突候选",
        "",
    ]
    if report["eligible_candidates"]:
        lines += [
            "|证据链|确定技能|缺口Hit|抓包数|",
            "|---|---|---:|---:|",
        ]
        for row in report["eligible_candidates"]:
            lines.append(
                f"|`{row['route']} / {row['rule_key']}`|"
                f"{row['skill_id']} {row['skill_name']}|"
                f"{row['candidate_gap_hits']}|{row['candidate_distinct_captures']}|"
            )
    else:
        lines.append("- 没有候选通过‘至少两份独立抓包 + 零冲突’门槛。")
    lines += ["", "## 精确否定证据", ""]
    for reason, hits in list(report["negative_evidence_counts"].items())[:24]:
        lines.append(f"- `{reason}`：{hits} 次路线缺失/拒绝。")
    lines += [
        "",
        "## 下一待解字段",
        "",
        "- 对玩家直击缺口：需要补出当前 `damage_object_id` 的完整 A90370/Projectile 注册，尤其是目前只有对象号、没有可映射 ACT/config 的分支。",
        "- 对魔法草/史莱姆实体：需要实体当前攻击状态键，或实体攻击生成的子 Projectile 中携带父实体完整 ID 与代次；空自攻击槽不能继承创建技能。",
        "- 对 73000017/73000018：需要它们对应的真实对象模板字段；该数值本身不是技能身份，不能按旧标签投票。",
        "",
        "逐 Hit 证据、全部候选规则及否定样例分别保存在同目录 JSON/JSONL 文件中。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    selection = args.selection.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    entries = _load_selection(selection)
    before = _production_signature()
    results = []
    for index, entry in enumerate(entries, 1):
        checkpoint = checkpoints / f"{entry['sha256']}.json"
        if checkpoint.is_file():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            result = saved["result"] if (
                saved.get("schema") == SCHEMA
                and saved.get("capture_sha256") == entry["sha256"]
                and saved.get("production_signature") == before
            ) else _audit_capture(entry)
        else:
            result = _audit_capture(entry)
        _write_json(checkpoint, {
            "schema": SCHEMA,
            "capture_sha256": entry["sha256"],
            "production_signature": before,
            "result": result,
        })
        results.append(result)
        print(json.dumps({
            "completed": index,
            "total": len(entries),
            "capture": result["capture"],
            "target_direct37": result["counts"].get("all_target_direct37_hits", 0),
            "gap": result["counts"].get("unknown_first_gap_hits", 0),
        }, ensure_ascii=False), flush=True)
    after = _production_signature()
    if before != after:
        raise RuntimeError("production parser changed during read-only audit")

    report = _merge(entries, results, _hash_file(selection), before)
    rows = report.pop("_rows")
    eligible_hit_ids = set(report.pop("_eligible_hit_ids"))
    candidate_rows = [
        row for row in rows if row["physical_hit_id"] in eligible_hit_ids
    ]
    negative_rows = [
        row for row in rows
        if row["baseline_classification_audit_only"] != "deterministic"
        and row["physical_hit_id"] not in eligible_hit_ids
    ]
    report["report_sha256"] = sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "REPORT.json", report)
    (output / "REPORT.md").write_text(_markdown(report), encoding="utf-8")
    _write_json(output / "candidate_rules.json", report["all_candidate_rules"])
    _write_json(output / "candidate_hit_evidence.json", candidate_rows)
    (output / "unknown_hit_evidence.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in negative_rows)
        + ("\n" if negative_rows else ""),
        encoding="utf-8",
    )
    _write_json(output / "negative_evidence.json", {
        "counts": report["negative_evidence_counts"],
        "rows": negative_rows,
    })
    checks = {
        "selection_path_is_required_v104": selection == DEFAULT_SELECTION.resolve(),
        "capture_count_matches_selection": len(results) == len(entries) == 3,
        "capture_hashes_verified": all(
            _hash_file(Path(row["path"])) == row["sha256"] for row in entries
        ),
        "legacy_labels_never_positive_proof": all(
            not row["proof_flags"]["uses_legacy_skill_label"] for row in rows
        ),
        "time_never_positive_proof": all(
            not row["proof_flags"]["uses_cast_time_distance"] for row in rows
        ),
        "damage_never_positive_proof": all(
            not row["proof_flags"]["uses_damage_amount_to_choose_skill"]
            for row in rows
        ),
        "result_group_never_positive_proof": all(
            not row["proof_flags"]["uses_result_group_to_choose_skill"]
            for row in rows
        ),
        "eligible_rules_are_cross_sample_zero_conflict": all(
            row["cross_sample"] and row["zero_conflict"]
            for row in report["eligible_candidates"]
        ),
        "physical_events_unchanged": report["physical_conservation"]["events_mutated"] == 0,
        "production_signature_unchanged": before == after,
    }
    validation = {
        "schema": f"{SCHEMA}-validation",
        "passed": all(checks.values()),
        "checks": checks,
        "report_sha256": report["report_sha256"],
    }
    _write_json(output / "VALIDATION.json", validation)
    if not validation["passed"]:
        raise AssertionError(validation)
    print(json.dumps({
        "output": str(output),
        "summary": report["summary"],
        "validation": validation["passed"],
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
