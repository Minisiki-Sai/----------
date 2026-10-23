"""Audit Heavy Gunner entity action/object chains for keyless direct37 hits.

Scope is deliberately narrow:

* hash-pinned captures from the new Black Dragon Classic/Hardcore corpus;
* Heavy Gunner-owned entity templates 32005 and 32008;
* direct37 rows whose exact self-attack slot is ``FFFFFFFF``.

An existing displayed skill, time distance, damage amount, and result-group ID
are audit-only.  A candidate can only be created by the current entity birth
and 34/2c owner generation plus either:

    entity selector/action -> exact damage_object_id -> direct37

or:

    entity child Projectile registration -> exact damage_object_id -> direct37

This tool never imports a candidate into production and never changes a hit.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.compact_entity_object_evidence import (  # noqa: E402
    SELECTOR_SPECS,
    decode_selector_records,
)
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.entity_damage_owner import (  # noqa: E402
    resolve_current_entity_owner,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.summon_attack_runtime import (  # noqa: E402
    SUMMON_SELF_ATTACK_SPECS,
)
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    EntityLife,
    PhysicalHit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


SCHEMA = "heavy-entity-action-v110"
TARGET_JOB = "重炮手"
TARGET_TEMPLATES = {32005: "重磅鸭实体", 32008: "摄魂怪实体"}
SKILL_NAMES = {
    4015: "召唤摄魂怪",
    4212: "摄魂怪践踏",
    4214: "摄魂怪旋风",
    4216: "重磅鸭",
    4217: "重磅轰击",
    4218: "重磅炸弹",
    4219: "重磅汽笛",
    4301: "飞溅",
    4303: "摄魂怪光束",
    4352: "重磅鸭EX",
}
DEFAULT_SELECTION_NAMES = (
    "black_dragon_new33_heavy_gunner_selection_v104.json",
    # The existing v104 exporter used ``blackdragon`` in the filename.  The
    # content and source-report hashes remain authoritative, not this alias.
    "blackdragon_new33_heavy_gunner_selection_v104.json",
)
DEFAULT_STATIC = ROOT / "outputs" / "v26_summon_attack_chain" / "static_catalog.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "heavy_entity_action_v110_20260910"


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(skill_id: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(skill_id), int(skill_id)))


def parse_skill(value: object) -> int:
    text = str(value or "").strip()
    return canonical(int(text)) if text.isdecimal() and int(text) > 0 else 0


def default_selection() -> Path:
    for name in DEFAULT_SELECTION_NAMES:
        candidate = ROOT / "outputs" / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "missing hash-pinned Heavy Gunner selection: "
        + ", ".join(str(ROOT / "outputs" / name) for name in DEFAULT_SELECTION_NAMES)
    )


def selected_captures(selection_path: Path) -> list[dict[str, str]]:
    selection = json.loads(selection_path.read_text(encoding="utf-8-sig"))
    if selection.get("schema_version") != "hash-pinned-audit-selection-v104":
        raise ValueError(f"unexpected selection schema: {selection.get('schema_version')!r}")
    if selection.get("summary", {}).get("jobs") != [TARGET_JOB]:
        raise ValueError("selection is not Heavy Gunner-only")
    report_path = Path(selection["sources"]["source_report"])
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    manifest = {
        str(row["capture_sha256"]): row
        for row in report.get("capture_manifest", ())
    }
    result: list[dict[str, str]] = []
    for selected in selection["sources"]["selected_results"]:
        digest = str(selected["capture_sha256"])
        row = manifest.get(digest)
        if row is None:
            raise ValueError(f"capture absent from source manifest: {selected['capture']}")
        capture = Path(row["capture_path"])
        if capture.name != str(selected["capture"]):
            raise ValueError(f"capture name mismatch: {capture}")
        if not capture.is_file() or file_hash(capture) != digest:
            raise ValueError(f"capture hash mismatch: {capture}")
        result.append({"capture": capture.name, "path": str(capture), "sha256": digest})
    if len(result) != int(selection["summary"]["capture_count"]):
        raise ValueError("selection capture count drift")
    if len({row["sha256"] for row in result}) != len(result):
        raise ValueError("selection contains duplicate capture hashes")
    return sorted(result, key=lambda row: row["sha256"])


def static_action_maps(path: Path) -> tuple[
    dict[tuple[int, int], dict[str, Any]],
    dict[tuple[int, int], dict[str, Any]],
]:
    """Return unique skill actions and the complete target ACT inventory."""
    catalog = json.loads(path.read_text(encoding="utf-8-sig"))
    by_name: dict[tuple[int, str], list[Any]] = defaultdict(list)
    for spec in SUMMON_SELF_ATTACK_SPECS.values():
        if int(spec.template_id) not in TARGET_TEMPLATES:
            continue
        for name in spec.actions:
            by_name[(int(spec.template_id), str(name).casefold())].append(spec)
    result: dict[tuple[int, int], dict[str, Any]] = {}
    metadata: dict[tuple[int, int], dict[str, Any]] = {}
    for entity in catalog.get("templates", ()):
        template = int(entity.get("template", 0) or 0)
        if template not in TARGET_TEMPLATES:
            continue
        for action in entity.get("action_indices", ()):
            name = str(action.get("name", ""))
            metadata[(template, int(action["index"]))] = {
                "action_name": name,
                "damage_signals": int(action.get("damage_signals", 0) or 0),
                "projectile_ids": [int(value) for value in action.get("projectiles", ())],
            }
            matches = by_name.get((template, name.casefold()), ())
            skills = {canonical(spec.player_skill_id) for spec in matches}
            if len(skills) != 1:
                continue
            skill = next(iter(skills))
            result[(template, int(action["index"]))] = {
                "skill_id": skill,
                "skill_name": SKILL_NAMES.get(skill, ""),
                "action_name": name,
                "damage_signals": int(action.get("damage_signals", 0) or 0),
                "projectile_ids": sorted({
                    int(value)
                    for spec in matches
                    for value in spec.projectile_ids
                }),
                "static_proof_ids": sorted({str(spec.proof_id) for spec in matches}),
            }
    return result, metadata


STATIC_ACTIONS, ACTION_METADATA = static_action_maps(DEFAULT_STATIC)


@dataclass(frozen=True, slots=True)
class SelectorObservation:
    entity_id: int
    entity_generation: int
    entity_template_id: int
    birth_fingerprint: str
    object_id: int
    object_generation: int
    length: int
    action: int
    signal: int
    timestamp: float
    packet_serial: int
    record_offset: int
    wire_sha256: str
    retransmission: bool

    @property
    def order(self) -> tuple[int, int, int]:
        return (self.packet_serial, 0, self.record_offset)


@dataclass(frozen=True, slots=True)
class RegistrationObservation:
    entity_id: int
    entity_generation: int
    entity_template_id: int
    birth_fingerprint: str
    damage_object_id: int
    object_generation: int
    projectile_template_id: int
    parent_damage_object_id: int
    effect_resource_id: int
    child_sequence: int
    timestamp: float
    packet_serial: int
    record_offset: int
    registration_key_sha256: str
    ledger_accepted: bool
    ledger_canonical_skill_id: int
    ledger_object_generation: int
    packet_validated_subtype: bool
    action_qualified_subtype: bool
    parent_qualified_subtype: bool
    requires_root_action_proof: bool

    @property
    def order(self) -> tuple[int, int, int]:
        return (self.packet_serial, 0, self.record_offset)


def latest_before(rows: Iterable[Any], order: tuple[int, int, int]) -> Any | None:
    candidates = [row for row in rows if row.order < order]
    return max(candidates, key=lambda row: row.order) if candidates else None


def exact_life(
    decoder: object,
    entity_id: int,
    timestamp: float,
    order: tuple[int, int, int],
    capture: str,
) -> tuple[EntityLife | None, str]:
    ledger = decoder.entity_owner_evidence
    birth = ledger.births.get(entity_id)
    if birth is None:
        return None, "missing_current_birth"
    template, generation, _born = birth
    if int(template) not in TARGET_TEMPLATES:
        return None, "not_target_template"
    players = decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)
    binding = resolve_current_entity_owner(
        ledger,
        entity_id,
        int(template),
        timestamp,
        order=order,
        generation=int(generation),
        entity_templates=decoder.monster_entities,
        known_player_actor_ids=players,
    )
    if binding is None:
        return None, ledger.negative_state(
            entity_id, generation=int(generation), timestamp=timestamp
        ) or "owner_validation_failed"
    return EntityLife(
        capture=capture,
        entity_id=entity_id,
        generation=int(binding.generation),
        template_id=int(template),
        owner_id=int(binding.record.owner_id),
        birth_order=tuple(binding.birth_order),
        owner_order=tuple(binding.owner_order),
        birth_fingerprint=str(binding.birth_fingerprint),
        owner_fingerprint=str(binding.record.message_fingerprint),
        conflicted=bool(entity_id in ledger.conflicts or ledger.owner_revoked(entity_id)),
    ), "exact_current_birth_owner_generation"


def physical_for_event(event: CombatEvent, inventory: object) -> Any | None:
    rows = [
        row for row in inventory.damage_records
        if row.admissible_fields
        and row.family == "direct37"
        and int(row.record_offset) == int(event.wire_record_offset)
        and int(row.source_actor_id) == int(event.wire_source_actor_id)
        and int(row.target_actor_id) == int(event.wire_target_actor_id)
        and int(row.damage) == int(event.amount)
        and bool(row.critical) == bool(event.wire_critical)
        and int(row.damage_object_id or 0) == int(event.wire_damage_object_id)
    ]
    return rows[0] if len(rows) == 1 else None


def selector_candidate(observation: SelectorObservation) -> dict[str, Any] | None:
    exact = SELECTOR_SPECS.get((
        observation.entity_template_id,
        observation.length,
        observation.action,
        observation.signal,
    ))
    static = STATIC_ACTIONS.get((observation.entity_template_id, observation.action))
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    if exact is not None:
        candidates.append((canonical(int(exact[0])), "reviewed_exact_selector_spec", {
            "attack_skill_id": int(exact[1]),
            "projectile_template_id": int(exact[2]),
            "lifecycle_guard_seconds": float(exact[3]),
        }))
    if static is not None:
        candidates.append((int(static["skill_id"]), "client_ACT_action_unique", static))
    skills = {row[0] for row in candidates}
    if len(skills) != 1:
        return None
    skill = next(iter(skills))
    # Time is allowed only as an expiry/lifecycle veto for the pre-reviewed
    # exact selector.  It never selects the skill and is not emitted as proof.
    exact_expiry = float(exact[3]) if exact is not None else None
    return {
        "route": "entity_selector_action_object",
        "rule_key": (
            f"selector|{observation.entity_template_id}|{observation.length}|"
            f"{observation.action}|{observation.signal}"
        ),
        "skill_id": skill,
        "skill_name": SKILL_NAMES.get(skill, ""),
        "static_routes": [
            {"source": source, **detail} for _skill, source, detail in candidates
        ],
        "exact_expiry_seconds": exact_expiry,
    }


def registration_candidate(
    decoder: object,
    hit: PhysicalHit,
    observation: RegistrationObservation | None,
    timestamp: float,
) -> dict[str, Any] | None:
    if observation is None or not observation.ledger_accepted:
        return None
    if (
        observation.entity_generation != hit.generation
        or observation.entity_template_id != hit.template_id
        or observation.damage_object_id != hit.object_id
    ):
        return None
    attribution = decoder.damage_object_origins.attribute_direct37(
        source_actor_id=hit.entity_id,
        timestamp=timestamp,
        layout="direct37",
        payload=hit.wire,
        direction=hit.direction,
        damage=hit.damage,
        record_offset=hit.order[2],
        packet_serial=hit.order[0],
        _evidence_only=True,
    )
    if (
        attribution is None
        or not attribution.deterministically_validated
        or int(attribution.owner_actor_id) != hit.entity_id
        or int(attribution.damage_object_id) != hit.object_id
        or int(attribution.generation) != observation.ledger_object_generation
        or int(attribution.projectile_template_id) != observation.projectile_template_id
    ):
        return None
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(observation.projectile_template_id)
    if spec is None or canonical(int(spec.canonical_skill_id)) != canonical(int(attribution.skill_id)):
        return None
    skill = canonical(int(attribution.skill_id))
    return {
        "route": "entity_projectile_registration_object",
        "rule_key": f"registration|{hit.template_id}|{observation.projectile_template_id}",
        "skill_id": skill,
        "skill_name": SKILL_NAMES.get(skill, ""),
        "static_routes": [{
            "source": "client_Projectile_template",
            "projectile_template_id": observation.projectile_template_id,
            "spec_key": str(spec.key),
            "skill_ids": sorted(int(value) for value in spec.skill_ids),
            "packet_validated": bool(attribution.packet_validated),
            "client_act_validated": bool(attribution.client_act_validated),
            "action_qualified_validated": bool(attribution.action_qualified_validated),
            "parent_lineage_validated": bool(attribution.parent_lineage_validated),
            "packet_validated_subtype": bool(attribution.packet_validated_subtype),
        }],
        "exact_expiry_seconds": None,
    }


def audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    capture = Path(entry["path"])
    if file_hash(capture) != entry["sha256"]:
        raise ValueError(f"capture changed: {capture}")
    source = V576DiagnosticReplaySource(capture, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed_events
    original_registration = decoder.damage_object_origins.observe_registration
    selectors: dict[tuple[int, int, int], list[SelectorObservation]] = defaultdict(list)
    registrations: dict[tuple[int, int, int], list[RegistrationObservation]] = defaultdict(list)
    selector_generations: Counter[tuple[int, int, int]] = Counter()
    registration_generations: Counter[tuple[int, int, int]] = Counter()
    selector_last_identity: dict[tuple[int, int, int], tuple[Any, ...]] = {}
    registration_last_identity: dict[tuple[int, int, int], tuple[Any, ...]] = {}
    hits: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    def observe_registration(*args: Any, **kwargs: Any) -> Any:
        result = original_registration(*args, **kwargs)
        entity = int(kwargs.get("source_actor_id", 0) or 0)
        birth = decoder.entity_owner_evidence.births.get(entity)
        if birth is None or int(birth[0]) not in TARGET_TEMPLATES:
            return result
        object_id = int(kwargs.get("damage_object_id", 0) or 0)
        key = (entity, int(birth[1]), object_id)
        registration_key = str(kwargs.get("registration_key", "") or "")
        identity = (
            int(kwargs.get("projectile_template_id", 0) or 0),
            int(kwargs.get("parent_damage_object_id", 0) or 0),
            int(kwargs.get("effect_resource_id", 0) or 0),
            int(kwargs.get("child_sequence", 0) or 0),
            registration_key,
        )
        if registration_last_identity.get(key) != identity:
            registration_generations[key] += 1
            registration_last_identity[key] = identity
        birth_evidence = decoder.entity_owner_evidence.birth_evidence.get(entity, ((0, 0, 0), ""))
        row = RegistrationObservation(
            entity_id=entity,
            entity_generation=int(birth[1]),
            entity_template_id=int(birth[0]),
            birth_fingerprint=str(birth_evidence[1]),
            damage_object_id=object_id,
            object_generation=int(registration_generations[key]),
            projectile_template_id=int(kwargs.get("projectile_template_id", 0) or 0),
            parent_damage_object_id=int(kwargs.get("parent_damage_object_id", 0) or 0),
            effect_resource_id=int(kwargs.get("effect_resource_id", 0) or 0),
            child_sequence=int(kwargs.get("child_sequence", 0) or 0),
            timestamp=float(kwargs.get("timestamp", 0.0) or 0.0),
            packet_serial=int(kwargs.get("packet_serial", 0) or 0),
            record_offset=int(kwargs.get("record_offset", 0) or 0),
            registration_key_sha256=sha256(registration_key.encode("ascii", "ignore")).hexdigest()
            if registration_key else "",
            ledger_accepted=result is not None,
            ledger_canonical_skill_id=int(getattr(result, "canonical_skill_id", 0) or 0),
            ledger_object_generation=int(getattr(result, "generation", 0) or 0),
            packet_validated_subtype=bool(kwargs.get("packet_validated_subtype", False)),
            action_qualified_subtype=bool(kwargs.get("action_qualified_subtype", False)),
            parent_qualified_subtype=bool(kwargs.get("parent_qualified_subtype", False)),
            requires_root_action_proof=bool(kwargs.get("requires_root_action_proof", False)),
        )
        registrations[key].append(row)
        counts["raw_target_entity_registrations"] += 1
        counts["accepted_target_entity_registrations"] += int(row.ledger_accepted)
        return result

    decoder.damage_object_origins.observe_registration = observe_registration

    def feed(packet: object) -> list[object]:
        events = original_feed(packet)
        serial = int(decoder._damage_object_packet_serial)
        direction = packet.inferred_direction()
        if str(packet.transport).upper() == "UDP":
            for record in decode_selector_records(packet.payload, direction):
                birth = decoder.entity_owner_evidence.births.get(int(record.entity))
                if birth is None or int(birth[0]) not in TARGET_TEMPLATES:
                    continue
                key = (int(record.entity), int(birth[1]), int(record.object_id))
                identity = (int(record.length), int(record.action), int(record.signal), str(record.wire_hash))
                retransmission = selector_last_identity.get(key) == identity
                if not retransmission:
                    selector_generations[key] += 1
                    selector_last_identity[key] = identity
                birth_evidence = decoder.entity_owner_evidence.birth_evidence.get(
                    int(record.entity), ((0, 0, 0), "")
                )
                observation = SelectorObservation(
                    entity_id=int(record.entity),
                    entity_generation=int(birth[1]),
                    entity_template_id=int(birth[0]),
                    birth_fingerprint=str(birth_evidence[1]),
                    object_id=int(record.object_id),
                    object_generation=int(selector_generations[key]),
                    length=int(record.length),
                    action=int(record.action),
                    signal=int(record.signal),
                    timestamp=float(packet.timestamp_seconds),
                    packet_serial=serial,
                    record_offset=int(record.offset),
                    wire_sha256=str(record.wire_hash),
                    retransmission=retransmission,
                )
                selectors[key].append(observation)
                counts["target_entity_selector_rows"] += 1
                counts["target_entity_selector_retransmissions"] += int(retransmission)

        inventory = inspect_udp_damage_records(packet.payload, direction)
        seen_offsets: set[int] = set()
        for event in events:
            if (
                not isinstance(event, CombatEvent)
                or event.healing
                or int(event.amount) <= 0
                or "direct37" not in str(event.wire_damage_layout)
                or int(event.wire_record_offset or 0) in seen_offsets
            ):
                continue
            seen_offsets.add(int(event.wire_record_offset))
            if not inventory.complete:
                counts[f"physical_inventory_rejected:{inventory.rejection_reason or 'unknown'}"] += 1
                continue
            physical = physical_for_event(event, inventory)
            if physical is None:
                counts["physical_join_not_unique"] += 1
                continue
            entity = int(physical.source_actor_id)
            if not 0 < entity < 65536:
                continue
            order = (serial, 0, int(physical.record_offset))
            life, life_reason = exact_life(
                decoder, entity, float(packet.timestamp_seconds), order, capture.name
            )
            if life is None:
                counts[f"target_life_rejected:{life_reason}"] += 1
                continue
            owner_name, owner_job = decoder._actor_display_context(life.owner_id)
            if owner_job != TARGET_JOB:
                continue
            target = decoder.monster_entities.get(int(physical.target_actor_id))
            if target is None or int(physical.target_actor_id) in (
                decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)
            ):
                counts["target_not_independent_pve_entity"] += 1
                continue
            hit = PhysicalHit(
                capture=capture.name,
                physical_hit_id=f"{entry['sha256']}:{serial}:{physical.record_offset}",
                order=order,
                entity_id=entity,
                generation=life.generation,
                template_id=life.template_id,
                target_id=int(physical.target_actor_id),
                damage=int(physical.damage),
                object_id=int(physical.damage_object_id),
                result_group=int(physical.damage_result_group_id),
                wire=bytes(packet.payload),
                direction=direction,
            )
            slot = read_self_attack_slot(hit)
            if slot is None:
                counts["target_direct37_without_complete_self_slot_layout"] += 1
                continue
            slot_value = int(slot.monster_skill_id)
            self_spec = SUMMON_SELF_ATTACK_SPECS.get((life.template_id, slot_value))
            positive_control_skill = canonical(self_spec.player_skill_id) if self_spec else 0
            if slot_value == 0xFFFFFFFF:
                counts["ffff_target_hits"] += 1
                counts[f"ffff_template:{life.template_id}"] += 1
            else:
                counts["nonffff_target_hits"] += 1
                counts["mapped_positive_control_hits"] += int(positive_control_skill > 0)
                counts["unmapped_positive_self_slot_hits"] += int(
                    slot_value not in (0, 0xFFFFFFFF) and positive_control_skill == 0
                )

            selector = latest_before(
                selectors.get((entity, life.generation, hit.object_id), ()), order
            )
            selector_route = selector_candidate(selector) if selector is not None else None
            if selector_route is not None and selector_route["exact_expiry_seconds"] is not None:
                age = float(packet.timestamp_seconds) - selector.timestamp
                if not 0.0 <= age <= float(selector_route["exact_expiry_seconds"]):
                    selector_route = None
                    counts["selector_exact_lifecycle_expired"] += 1
            registration = latest_before(
                registrations.get((entity, life.generation, hit.object_id), ()), order
            )
            registration_route = registration_candidate(
                decoder, hit, registration, float(packet.timestamp_seconds)
            )
            routes = [row for row in (selector_route, registration_route) if row is not None]
            route_skills = sorted({int(row["skill_id"]) for row in routes})
            route_conflict = len(route_skills) > 1
            if route_conflict:
                counts["route_conflict_hits"] += 1
            if slot_value == 0xFFFFFFFF:
                if not routes:
                    counts["ffff_without_candidate_route"] += 1
                elif route_conflict:
                    counts["ffff_with_conflicting_routes"] += 1
                else:
                    counts["ffff_with_unique_candidate_route"] += 1
            for route in routes:
                if positive_control_skill > 0:
                    route["positive_control_matches"] = positive_control_skill == int(route["skill_id"])
                else:
                    route["positive_control_matches"] = None
            row = {
                "capture": capture.name,
                "capture_sha256": entry["sha256"],
                "physical_hit_id": hit.physical_hit_id,
                "packet_serial": serial,
                "record_offset": int(physical.record_offset),
                "wire_record_sha256": str(physical.wire_record_sha256),
                "owner_actor_id": f"0x{life.owner_id:08X}",
                "owner_name_audit_only": owner_name,
                "owner_job": owner_job,
                "entity_id": entity,
                "entity_template_id": life.template_id,
                "entity_template_name": TARGET_TEMPLATES[life.template_id],
                "entity_generation": life.generation,
                "birth_fingerprint": life.birth_fingerprint,
                "owner_fingerprint": life.owner_fingerprint,
                "damage_object_id": hit.object_id,
                "target_actor_id": hit.target_id,
                "self_attack_slot": slot_value,
                "self_attack_context_audit_only": int(slot.context_skill_id),
                "positive_control_skill_id": positive_control_skill,
                "positive_control_skill_name": SKILL_NAMES.get(positive_control_skill, ""),
                "selector": asdict(selector) if selector else None,
                "registration": asdict(registration) if registration else None,
                "candidate_routes": routes,
                "candidate_skill_ids": route_skills,
                "route_conflict": route_conflict,
                "baseline_skill_id_audit_only": parse_skill(event.skill_id),
                "baseline_skill_name_audit_only": str(event.skill_name or ""),
                "baseline_method_audit_only": str(event.attribution_method or ""),
                "damage_amount_audit_only": int(physical.damage),
                "result_group_audit_only": int(physical.damage_result_group_id),
                "critical_audit_only": bool(physical.critical),
                "positive_proof_uses_baseline_name": False,
                "positive_proof_uses_time_distance": False,
                "positive_proof_uses_damage_amount": False,
                "positive_proof_uses_result_group": False,
            }
            hits.append(row)
        return events

    decoder.feed_events = feed
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        decoder.feed_events = original_feed
        decoder.damage_object_origins.observe_registration = original_registration
        source.close()
    return {
        "capture": capture.name,
        "capture_path": str(capture),
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "hits": hits,
        "selectors": [asdict(row) for values in selectors.values() for row in values],
        "registrations": [asdict(row) for values in registrations.values() for row in values],
    }


def aggregate(results: list[dict[str, Any]], selection: Path) -> dict[str, Any]:
    hits = [row for result in results for row in result["hits"]]
    ffff = [row for row in hits if int(row["self_attack_slot"]) == 0xFFFFFFFF]
    controls = [row for row in hits if int(row["positive_control_skill_id"]) > 0]
    counts: Counter[str] = Counter()
    for result in results:
        counts.update(result["counts"])

    grouped: dict[str, dict[str, Any]] = {}
    for hit in hits:
        for route in hit["candidate_routes"]:
            key = str(route["rule_key"])
            group = grouped.setdefault(key, {
                "rule_key": key,
                "route": route["route"],
                "entity_template_id": int(hit["entity_template_id"]),
                "candidate_skill_id": int(route["skill_id"]),
                "candidate_skill_name": route["skill_name"],
                "static_routes": route["static_routes"],
                "ffff_hits": 0,
                "ffff_damage_audit_only": 0,
                "positive_control_hits": 0,
                "positive_control_matches": 0,
                "positive_control_conflicts": 0,
                "unmapped_nonempty_control_hits": 0,
                "same_hit_route_conflicts": 0,
                "captures": set(),
                "owners": set(),
                "entity_generations": set(),
                "objects": set(),
                "baseline_labels_audit_only": Counter(),
            })
            if group["candidate_skill_id"] != int(route["skill_id"]):
                group["same_hit_route_conflicts"] += 1
            is_ffff = int(hit["self_attack_slot"]) == 0xFFFFFFFF
            if is_ffff:
                group["ffff_hits"] += 1
                group["ffff_damage_audit_only"] += int(hit["damage_amount_audit_only"])
            elif int(hit["positive_control_skill_id"]) > 0:
                group["positive_control_hits"] += 1
                if int(hit["positive_control_skill_id"]) == int(route["skill_id"]):
                    group["positive_control_matches"] += 1
                else:
                    group["positive_control_conflicts"] += 1
            else:
                group["unmapped_nonempty_control_hits"] += 1
            group["same_hit_route_conflicts"] += int(bool(hit["route_conflict"]))
            group["captures"].add(hit["capture_sha256"])
            group["owners"].add(hit["owner_actor_id"])
            group["entity_generations"].add((hit["capture_sha256"], hit["entity_id"], hit["entity_generation"]))
            group["objects"].add((hit["capture_sha256"], hit["entity_id"], hit["entity_generation"], hit["damage_object_id"]))
            group["baseline_labels_audit_only"][str(hit["baseline_skill_id_audit_only"])] += 1

    rules: list[dict[str, Any]] = []
    for key, group in sorted(grouped.items()):
        conflicts = int(group["positive_control_conflicts"]) + int(group["same_hit_route_conflicts"])
        route = str(group["route"])
        static_proved = bool(group["static_routes"])
        ffff_hits = int(group["ffff_hits"])
        safe = bool(ffff_hits > 0 and static_proved and conflicts == 0)
        decision = (
            "production_candidate_zero_conflict"
            if safe
            else "positive_control_only"
            if ffff_hits == 0 and conflicts == 0
            else "withhold_conflict"
            if conflicts
            else "withhold_no_static_proof"
        )
        rules.append({
            "rule_key": key,
            "route": route,
            "entity_template_id": int(group["entity_template_id"]),
            "entity_template_name": TARGET_TEMPLATES[int(group["entity_template_id"])],
            "candidate_skill_id": int(group["candidate_skill_id"]),
            "candidate_skill_name": str(group["candidate_skill_name"]),
            "ffff_hits": ffff_hits,
            "ffff_damage_audit_only": int(group["ffff_damage_audit_only"]),
            "positive_control_hits": int(group["positive_control_hits"]),
            "positive_control_matches": int(group["positive_control_matches"]),
            "positive_control_conflicts": int(group["positive_control_conflicts"]),
            "unmapped_nonempty_control_hits": int(group["unmapped_nonempty_control_hits"]),
            "same_hit_route_conflicts": int(group["same_hit_route_conflicts"]),
            "distinct_captures": len(group["captures"]),
            "distinct_owners": len(group["owners"]),
            "distinct_entity_generations": len(group["entity_generations"]),
            "distinct_damage_objects": len(group["objects"]),
            "static_routes": group["static_routes"],
            "baseline_labels_audit_only": dict(group["baseline_labels_audit_only"]),
            "decision": decision,
            "production_safe_in_this_corpus": safe,
            "required_runtime_chain": [
                "current entity birth generation",
                "same-generation exact 34/2c owner",
                "selector/action or Projectile registration before Hit",
                "same full damage_object_id",
                "same direct37 physical row",
            ],
        })
    candidates = [row for row in rules if row["production_safe_in_this_corpus"]]

    unresolved_reasons: Counter[str] = Counter()
    unresolved_templates: Counter[str] = Counter()
    unresolved_objects: Counter[str] = Counter()
    unresolved_selector_shapes: Counter[tuple[int, int, int, int]] = Counter()
    for hit in ffff:
        if hit["candidate_skill_ids"] and not hit["route_conflict"]:
            continue
        if hit["route_conflict"]:
            reason = "conflicting_exact_routes"
        elif hit["selector"] is None and hit["registration"] is None:
            reason = "no_same_generation_selector_or_registration_for_damage_object"
        elif hit["selector"] is not None and hit["registration"] is None:
            selector = hit["selector"]
            unresolved_selector_shapes[(
                int(hit["entity_template_id"]), int(selector["length"]),
                int(selector["action"]), int(selector["signal"]),
            )] += 1
            reason = (
                "selector_invalidation_or_unsupported_shape"
                if int(selector["action"]) < 0 or int(selector["signal"]) < 0
                else "selector_action_not_uniquely_mapped_to_skill"
            )
        elif hit["registration"] is not None and hit["selector"] is None:
            registration = hit["registration"]
            reason = (
                "registration_rejected_by_current_object_ledger"
                if not registration["ledger_accepted"]
                else "registration_template_not_deterministically_validated_for_hit"
            )
        else:
            reason = "available_object_records_do_not_form_a_unique_skill_chain"
        unresolved_reasons[reason] += 1
        unresolved_templates[str(hit["entity_template_id"])] += 1
        unresolved_objects[
            f"{hit['entity_template_id']}|{hit['damage_object_id']}"
        ] += 1

    report = {
        "schema": SCHEMA,
        "scope": {
            "selection": str(selection),
            "selection_sha256": file_hash(selection),
            "capture_count": len(results),
            "capture_hashes": [
                {"capture": row["capture"], "sha256": row["capture_sha256"]}
                for row in results
            ],
            "job": TARGET_JOB,
            "entity_templates": TARGET_TEMPLATES,
            "target_layout": "direct37 self slot FFFFFFFF",
        },
        "policy": {
            "positive_chain": "current entity generation -> exact owner -> selector/action or child Projectile registration -> same full damage_object_id -> direct37",
            "audit_only": ["old skill/name", "time distance", "damage amount", "result group"],
            "unknown_first": "Every FFFFFFFF row starts unknown; old labels never vote for a candidate.",
            "time_semantics": "Only an independently reviewed finite object lifetime may veto a stale selector; time never chooses a skill.",
            "production_modified": False,
        },
        "counts": {
            **dict(counts),
            "audited_target_direct37_hits": len(hits),
            "ffff_target_hits": len(ffff),
            "mapped_positive_control_hits": len(controls),
            "ffff_unique_candidate_hits": sum(
                bool(row["candidate_skill_ids"]) and not row["route_conflict"] for row in ffff
            ),
            "ffff_unresolved_or_conflicting_hits": sum(
                not row["candidate_skill_ids"] or row["route_conflict"] for row in ffff
            ),
            "production_candidate_rule_count": len(candidates),
            "production_candidate_hit_count": sum(int(row["ffff_hits"]) for row in candidates),
        },
        "candidate_rules": candidates,
        "all_rule_evaluations": rules,
        "negative_evidence": {
            "unresolved_reasons": dict(unresolved_reasons.most_common()),
            "unresolved_by_entity_template": dict(unresolved_templates),
            "largest_unresolved_object_buckets": [
                {"template_and_object": key, "hits": value}
                for key, value in unresolved_objects.most_common(30)
            ],
            "unresolved_selector_shapes": [
                {
                    "entity_template_id": shape[0],
                    "selector_length": shape[1],
                    "action": shape[2],
                    "signal": shape[3],
                    "hits": number,
                    "client_act_metadata": ACTION_METADATA.get((shape[0], shape[2]), {}),
                    "decision": "not_a_skill_identity",
                }
                for shape, number in unresolved_selector_shapes.most_common()
            ],
            "target_entity_registration_rows": int(
                counts.get("raw_target_entity_registrations", 0)
            ),
            "next_field": (
                "Decode the entity's own server action-state record that carries the current ACT action/attack key "
                "and bind that state transition to the full damage_object_id (or decode an omitted child-object "
                "registration carrying parent object ID). The needed field must be generation-scoped and appear "
                "before direct37; m+114, prior labels and result-group IDs remain ineligible."
            ),
        },
        "invariants": {
            "production_changed": False,
            "physical_hits_created": 0,
            "physical_hits_removed": 0,
            "damage_changed": 0,
            "old_labels_used_as_positive_proof": False,
            "time_distance_used_as_positive_proof": False,
            "damage_amount_used_as_positive_proof": False,
            "result_group_used_as_positive_proof": False,
        },
    }
    report["report_sha256"] = sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return report


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(
    output: Path,
    report: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    hits = [row for result in results for row in result["hits"]]
    ffff = [row for row in hits if int(row["self_attack_slot"]) == 0xFFFFFFFF]
    selectors = [
        {"capture": result["capture"], "capture_sha256": result["capture_sha256"], **row}
        for result in results for row in result["selectors"]
    ]
    registrations = [
        {"capture": result["capture"], "capture_sha256": result["capture_sha256"], **row}
        for result in results for row in result["registrations"]
    ]
    (output / "REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "candidate_rules.json").write_text(
        json.dumps(report["candidate_rules"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "negative_evidence.json").write_text(
        json.dumps(report["negative_evidence"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "ffff_direct37_hits.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ffff)
        + ("\n" if ffff else ""),
        encoding="utf-8",
    )
    write_csv(output / "selector_inventory.csv", selectors, [
        "capture", "capture_sha256", "entity_id", "entity_generation",
        "entity_template_id", "birth_fingerprint", "object_id", "object_generation",
        "length", "action", "signal", "timestamp", "packet_serial", "record_offset",
        "wire_sha256", "retransmission",
    ])
    write_csv(output / "registration_inventory.csv", registrations, [
        "capture", "capture_sha256", "entity_id", "entity_generation",
        "entity_template_id", "birth_fingerprint", "damage_object_id", "object_generation",
        "projectile_template_id", "parent_damage_object_id", "effect_resource_id",
        "child_sequence", "timestamp", "packet_serial", "record_offset",
        "registration_key_sha256", "ledger_accepted", "ledger_canonical_skill_id",
        "ledger_object_generation", "packet_validated_subtype", "action_qualified_subtype",
        "parent_qualified_subtype", "requires_root_action_proof",
    ])
    validation = {
        "schema": SCHEMA,
        "passed": all((
            report["scope"]["capture_count"] == len(results),
            len({row["capture_sha256"] for row in results}) == len(results),
            not report["invariants"]["production_changed"],
            not report["invariants"]["old_labels_used_as_positive_proof"],
            not report["invariants"]["time_distance_used_as_positive_proof"],
            not report["invariants"]["damage_amount_used_as_positive_proof"],
            not report["invariants"]["result_group_used_as_positive_proof"],
            all(
                int(row["ffff_hits"]) > 0
                and int(row["positive_control_conflicts"]) == 0
                and int(row["same_hit_route_conflicts"]) == 0
                for row in report["candidate_rules"]
            ),
        )),
    }
    (output / "VALIDATION.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not validation["passed"]:
        raise AssertionError(validation)

    counts = report["counts"]
    lines = [
        "# 重炮手实体动作 / 子对象归因审计 v110",
        "",
        f"- 语料：{report['scope']['capture_count']} 份哈希固定的新黑龙经典/硬核抓包。",
        "- 范围：重炮手拥有的实体模板 32005/32008，优先审计 self slot=FFFFFFFF 的 direct37。",
        "- 旧技能名、时间距离、伤害金额、result group 均为 audit-only；未修改生产解析器。",
        "",
        "## 命中数",
        "",
        f"- 目标实体 direct37：{counts['audited_target_direct37_hits']:,}",
        f"- FFFFFFFF：{counts['ffff_target_hits']:,}",
        f"- 获得唯一对象链候选：{counts['ffff_unique_candidate_hits']:,}",
        f"- 仍未知或冲突：{counts['ffff_unresolved_or_conflicting_hits']:,}",
        f"- 零冲突可生产候选规则：{counts['production_candidate_rule_count']:,} 条，覆盖 {counts['production_candidate_hit_count']:,} Hit。",
        "",
        "## 候选规则",
        "",
    ]
    if report["candidate_rules"]:
        for row in report["candidate_rules"]:
            lines.append(
                f"- `{row['rule_key']}` → `{row['candidate_skill_id']} {row['candidate_skill_name']}`："
                f"FFFFFFFF {row['ffff_hits']:,} Hit，{row['distinct_captures']} 份抓包，"
                f"正控 {row['positive_control_matches']}/{row['positive_control_hits']}，冲突 0。"
            )
    else:
        lines.append("- 无。当前语料没有形成满足同代实体、同对象 ID、静态技能边与零冲突正控的规则。")
    lines += [
        "",
        "## 精确否定证据",
        "",
    ]
    for reason, number in report["negative_evidence"]["unresolved_reasons"].items():
        lines.append(f"- `{reason}`：{number:,} Hit。")
    lines.append(
        f"- 当前代次 32005/32008 实体自身的 Projectile/子对象注册："
        f"{report['negative_evidence']['target_entity_registration_rows']:,} 条。"
    )
    for row in report["negative_evidence"]["unresolved_selector_shapes"]:
        metadata = row["client_act_metadata"]
        lines.append(
            f"- selector `{row['entity_template_id']}/{row['selector_length']}/"
            f"{row['action']}/{row['signal']}`：{row['hits']:,} Hit；"
            f"ACT 动作为 `{metadata.get('action_name', '未知')}`，"
            f"damage signals={metadata.get('damage_signals', '未知')}，故不能作为技能身份。"
        )
    lines += [
        "",
        "## 下一字段",
        "",
        report["negative_evidence"]["next_field"],
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    selection = (args.selection or default_selection()).resolve()
    output = args.output.resolve()
    captures = selected_captures(selection)
    workers = max(1, min(int(args.workers), 8, len(captures)))
    results: list[dict[str, Any]] = []
    if workers == 1:
        for entry in captures:
            result = audit_capture(entry)
            results.append(result)
            print(f"COMPLETE {entry['capture']}: FFFF={result['counts'].get('ffff_target_hits', 0)}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(audit_capture, entry): entry for entry in captures}
            for future in as_completed(futures):
                entry = futures[future]
                result = future.result()
                results.append(result)
                print(f"COMPLETE {entry['capture']}: FFFF={result['counts'].get('ffff_target_hits', 0)}", flush=True)
    results.sort(key=lambda row: row["capture_sha256"])
    report = aggregate(results, selection)
    write_outputs(output, report, results)
    print(json.dumps({
        "output": str(output),
        "captures": len(results),
        "ffff_hits": report["counts"]["ffff_target_hits"],
        "candidate_rules": len(report["candidate_rules"]),
        "candidate_hits": report["counts"]["production_candidate_hit_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
