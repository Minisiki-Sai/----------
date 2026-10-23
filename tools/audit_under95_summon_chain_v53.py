"""Read-only fixed-corpus audit for under-95 summon/entity damage chains.

Candidate skill identity is built only from packet/static evidence:

    entity birth -> explicit 34/2c owner generation ->
    exact entity self-attack slot OR exact selector/object/static ACT mapping OR
    packet-validated entity Projectile registration -> same-object direct37

Existing event skill names, damage amounts and timing proximity are never used
to construct a candidate.  They are retained only as frozen-baseline strata or
independent deterministic validation.  Production modules are not mutated.
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
import math
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.compact_entity_object_evidence import (  # noqa: E402
    SELECTOR_SPECS,
    decode_selector_records,
)
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.entity_damage_owner import (  # noqa: E402
    resolve_current_entity_owner,
)
from dragon_nest_builder.live_dps.generated_summon_self_attack_specs_v26 import (  # noqa: E402
    SUMMON_SELF_ATTACK_SPECS,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
    _SKILL_OBJECT_TEMPLATE_SKILL_IDS,
)
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    EntityLife,
    PhysicalHit,
    annotate_hit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


SCHEMA = "under95-summon-chain-v53"
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_STATIC = ROOT / "outputs/v26_summon_attack_chain/static_catalog.json"
DEFAULT_OUTPUT = ROOT / "outputs/under95_summon_chain_v53_20260909"
TARGET_JOBS = frozenset({"圣徒", "雷神", "重炮手", "机械大师", "药剂师", "黑暗萨满", "噬魂者"})
FOCUS_SKILLS = frozenset(
    {3403, 3409, 3412, 3422, 3423, 4216, 4417, 4415, 4405, 2304, 4551, 5214, 5401}
)
ACCEPTED_PLAYER_ACTION_KINDS = frozenset(
    {"skill_action", "skill_cast", "status_skill_action", "structured_multi_action"}
)
INDEPENDENT_TRUTH_METHODS = frozenset(
    {
        "damage_object_projectile_link",
        "packet_entity_skill_id",
        "packet_entity_self_attack",
        "packet_entity_template_owner",
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
    }
)
DEPENDENCIES = (
    "dragon_nest_builder/live_dps/compact_entity_object_evidence.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/entity_damage_owner.py",
    "dragon_nest_builder/live_dps/entity_owner_evidence.py",
    "dragon_nest_builder/live_dps/generated_summon_self_attack_specs_v26.py",
    "dragon_nest_builder/live_dps/summon_self_attack_evidence_v26.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "outputs/v26_summon_attack_chain/static_catalog.json",
)


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(value), int(value)))


def _skill(value: object) -> int:
    text = str(value or "").strip()
    return int(text) if text.isdecimal() and int(text) > 0 else 0


def _actor(value: object) -> int:
    text = str(value or "").split("@", 1)[0].strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except ValueError:
        return 0


def _selection(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    result_dir = ROOT / report["sources"]["after_results_dir"]
    captures: list[dict[str, str]] = []
    for row in report["sources"]["selected_results"]:
        frozen = json.loads((result_dir / row["result"]).read_text(encoding="utf-8"))
        captures.append(
            {
                "name": str(frozen["capture"]),
                "path": str(frozen["capture_path"]),
                "sha256": str(row["capture_sha256"]),
            }
        )
    if len(captures) != 35 or len({row["sha256"] for row in captures}) != 35:
        raise ValueError(f"fixed corpus changed: {len(captures)} captures")
    jobs = {str(row["job"]): dict(row) for row in report["jobs"]}
    missing = TARGET_JOBS - set(jobs)
    if missing:
        raise ValueError(f"target jobs absent from formal baseline: {sorted(missing)}")
    for row in captures:
        capture = Path(row["path"])
        if not capture.is_file() or _hash(capture) != row["sha256"]:
            raise ValueError(f"capture missing or hash changed: {capture}")
    return captures, jobs


def _static_selector_map(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    """Map (entity template, ACT index) only when static mapping is unique."""
    catalog = json.loads(path.read_text(encoding="utf-8"))
    accepted: dict[tuple[int, str], list[Any]] = defaultdict(list)
    for spec in SUMMON_SELF_ATTACK_SPECS.values():
        for action in spec.actions:
            accepted[(int(spec.template_id), str(action))].append(spec)
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for entity in catalog.get("templates", []):
        template = int(entity["template"])
        for action in entity.get("action_indices", []):
            if not int(action.get("damage_signals", 0) or 0) and not action.get("projectiles"):
                continue
            matches = accepted.get((template, str(action.get("name", ""))), ())
            skills = {_canonical(spec.player_skill_id) for spec in matches}
            proof_ids = sorted({str(spec.proof_id) for spec in matches})
            if len(skills) != 1 or not proof_ids:
                continue
            result[(template, int(action["index"]))] = {
                "skill_id": next(iter(skills)),
                "action_name": str(action["name"]),
                "damage_signals": int(action.get("damage_signals", 0) or 0),
                "projectiles": [int(v) for v in action.get("projectiles", ())],
                "proof_ids": proof_ids,
            }
    return result


STATIC_SELECTOR_MAP = _static_selector_map(DEFAULT_STATIC)


@dataclass(slots=True)
class SelectorState:
    entity: int
    template: int
    entity_generation: int
    object_id: int
    length: int
    action: int
    signal: int
    order: tuple[int, int, int]
    timestamp: float
    wire_hash: str
    birth_hash: str
    invalidated: bool = False
    repeats: int = 0


def _physical_for_event(event: CombatEvent, packet: object, inventory: object):
    rows = [
        row
        for row in inventory.damage_records
        if row.admissible_fields
        and row.family == "direct37"
        and row.record_offset == event.wire_record_offset
        and row.source_actor_id == event.wire_source_actor_id
        and row.target_actor_id == event.wire_target_actor_id
        and row.damage == event.amount
        and row.critical == bool(event.wire_critical)
    ]
    return rows[0] if len(rows) == 1 else None


def _origin_candidate(decoder: object, entity: int, object_id: int, timestamp: float,
                      serial: int, offset: int) -> tuple[int, dict[str, Any]] | None:
    ledger = decoder.damage_object_origins
    origin, snapshot = ledger._origin_at((entity, object_id), timestamp, serial, offset)
    if origin is None or origin.owner_actor_id != entity:
        return None
    if not (origin.registered_at <= timestamp <= origin.expires_at):
        return None
    invalidation = snapshot.invalidation if snapshot is not None else ledger._unknown_registration_invalidations.get((entity, object_id))
    if invalidation is not None and ledger._boundary_reached(invalidation, timestamp, serial, offset):
        return None
    revoked = ledger._revoked_origin_generations.get((entity, object_id, origin.generation))
    if revoked is not None and ledger._boundary_reached(revoked, timestamp, serial, offset):
        return None
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(origin.projectile_template_id)
    if spec is None or spec.require_action_evidence or origin.requires_root_action_proof:
        return None
    packet_static = (
        origin.packet_validated_subtype
        or origin.projectile_template_id in PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
    )
    client_static = (
        origin.projectile_template_id in CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS
        and not origin.action_qualified_subtype
        and not origin.parent_qualified_subtype
        and origin.projectile_template_id not in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS
    )
    if not (packet_static or client_static):
        return None
    return _canonical(spec.canonical_skill_id), {
        "projectile_template_id": int(origin.projectile_template_id),
        "object_generation": int(origin.generation),
        "registered_packet_serial": int(origin.registered_packet_serial),
        "registered_record_offset": int(origin.registered_record_offset),
        "registration_key": str(origin.registration_key),
        "packet_static": bool(packet_static),
        "client_static": bool(client_static),
    }


def _selector_candidate(state: SelectorState) -> tuple[int, dict[str, Any]] | None:
    if state.invalidated:
        return None
    exact = SELECTOR_SPECS.get((state.template, state.length, state.action, state.signal))
    candidates: list[tuple[int, dict[str, Any]]] = []
    if exact is not None:
        candidates.append(
            (
                _canonical(int(exact[0])),
                {
                    "source": "existing_exact_selector_spec",
                    "attack_skill_id": int(exact[1]),
                    "projectile_template_id": int(exact[2]),
                    "lifetime_metadata_seconds": float(exact[3]),
                },
            )
        )
    static = STATIC_SELECTOR_MAP.get((state.template, state.action))
    if static is not None:
        candidates.append((int(static["skill_id"]), {"source": "static_ACT_action_unique", **static}))
    skills = {row[0] for row in candidates}
    if len(skills) != 1:
        return None
    return next(iter(skills)), {
        "selector_length": state.length,
        "selector_action": state.action,
        "selector_signal": state.signal,
        "selector_wire_sha256": state.wire_hash,
        "static_routes": [row[1] for row in candidates],
    }


def _life(decoder: object, entity: int, timestamp: float, serial: int, offset: int,
          capture_name: str) -> tuple[EntityLife | None, str, object | None]:
    ledger = decoder.entity_owner_evidence
    birth = ledger.births.get(entity)
    if birth is None:
        return None, "missing_birth", None
    players = decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)
    binding = resolve_current_entity_owner(
        ledger,
        entity,
        birth[0],
        timestamp,
        order=(serial, 0, offset),
        generation=birth[1],
        entity_templates=decoder.monster_entities,
        known_player_actor_ids=players,
    )
    if binding is None:
        return None, ledger.negative_state(entity, generation=birth[1], timestamp=timestamp) or "owner_validation_failed", None
    return (
        EntityLife(
            capture=capture_name,
            entity_id=entity,
            generation=int(binding.generation),
            template_id=int(birth[0]),
            owner_id=int(binding.record.owner_id),
            birth_order=tuple(binding.birth_order),
            owner_order=tuple(binding.owner_order),
            birth_fingerprint=str(binding.birth_fingerprint),
            owner_fingerprint=str(binding.record.message_fingerprint),
            conflicted=bool(entity in ledger.conflicts or ledger.owner_revoked(entity)),
        ),
        "exact_owner_generation",
        binding,
    )


def _add_pattern(patterns: dict[str, Counter[str]], key: str, *, gap: bool, unknown: bool,
                 truth: bool, match: bool, old_match: bool, amount: int, owner: int,
                 capture: str) -> None:
    row = patterns[key]
    row["hits"] += 1
    row["damage"] += int(amount)
    row["gap_hits"] += int(gap)
    row["unknown_hits"] += int(unknown)
    row["truth_hits"] += int(truth)
    row["truth_matches"] += int(truth and match)
    row["truth_conflicts"] += int(truth and not match)
    row["old_label_matches"] += int(old_match)
    row[f"owner:{owner:08X}"] += 1
    row[f"capture:{capture}"] += 1


def audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    if _hash(path) != entry["sha256"]:
        raise ValueError(f"capture hash changed: {path}")
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    selectors: dict[tuple[int, int, int], SelectorState] = {}
    actions: dict[tuple[int, int], int] = Counter()
    counts: Counter[str] = Counter()
    gaps: Counter[str] = Counter()
    focus: dict[str, Counter[str]] = defaultdict(Counter)
    patterns: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add_example(kind: str, value: dict[str, Any]) -> None:
        if len(examples[kind]) < 12:
            examples[kind].append(value)

    def feed_events(packet):
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        direction = packet.inferred_direction()
        inventory = inspect_udp_damage_records(packet.payload, direction)

        # Keep every structurally decoded selector, including unsupported ones,
        # so a later namespace change can invalidate an earlier positive state.
        if str(packet.transport).upper() == "UDP":
            for record in decode_selector_records(packet.payload, direction):
                birth = decoder.entity_owner_evidence.births.get(record.entity)
                if birth is None:
                    counts["selector_without_birth"] += 1
                    continue
                key = (int(record.entity), int(birth[1]), int(record.object_id))
                previous = selectors.get(key)
                if record.action < 0 or record.signal < 0:
                    if previous is not None:
                        previous.invalidated = True
                    counts["selector_invalidation"] += 1
                    continue
                current = SelectorState(
                    entity=int(record.entity), template=int(birth[0]),
                    entity_generation=int(birth[1]), object_id=int(record.object_id),
                    length=int(record.length), action=int(record.action), signal=int(record.signal),
                    order=(serial, 0, int(record.offset)), timestamp=float(packet.timestamp_seconds),
                    wire_hash=str(record.wire_hash), birth_hash=str(decoder.entity_owner_evidence.birth_evidence.get(record.entity, ((0,0,0), ""))[1]),
                )
                if previous is None:
                    selectors[key] = current
                    counts["selector_births"] += 1
                elif (previous.template, previous.length, previous.action, previous.signal, previous.birth_hash) == (
                    current.template, current.length, current.action, current.signal, current.birth_hash
                ):
                    previous.repeats += 1
                    counts["selector_retransmissions"] += 1
                else:
                    previous.invalidated = True
                    selectors[key] = current
                    counts["selector_generation_change_without_object_lifecycle"] += 1

        seen: set[int] = set()
        for event in events:
            if isinstance(event, SkillCastEvent):
                owner = _actor(event.source_id)
                skill = _canonical(_skill(event.skill_id))
                if owner > 0x00FFFFFF and skill and event.packet_kind in ACCEPTED_PLAYER_ACTION_KINDS:
                    actions[(owner, skill)] += 1
                continue
            if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
                continue
            if "direct37" not in event.wire_damage_layout or event.wire_record_offset in seen:
                continue
            seen.add(int(event.wire_record_offset))
            counts["direct37_events"] += 1
            if not inventory.complete:
                counts[f"physical_join:{inventory.rejection_reason or 'incomplete'}"] += 1
                continue
            physical = _physical_for_event(event, packet, inventory)
            if physical is None:
                counts["physical_join:not_unique"] += 1
                continue
            entity = int(physical.source_actor_id)
            if not 0 < entity < 65536:
                continue
            counts["entity_direct37"] += 1
            life, owner_reason, binding = _life(
                decoder, entity, float(packet.timestamp_seconds), serial,
                int(physical.record_offset), path.name,
            )
            if life is None:
                gaps[owner_reason] += 1
                continue
            owner_name, owner_job = decoder._actor_display_context(life.owner_id)
            if owner_job not in TARGET_JOBS:
                continue
            counts["target_job_entity_direct37"] += 1
            target_template = decoder.monster_entities.get(int(physical.target_actor_id))
            if target_template is None or int(physical.target_actor_id) in (
                decoder.skill_actor_ids | decoder.local_actor_ids | set(decoder.known_actor_names)
            ):
                gaps["target_not_independently_known_pve_entity"] += 1
                continue

            hit = PhysicalHit(
                capture=path.name,
                physical_hit_id=f"{entry['sha256']}:{serial}:{physical.record_offset}",
                order=(serial, 0, int(physical.record_offset)), entity_id=entity,
                generation=life.generation, template_id=life.template_id,
                target_id=int(physical.target_actor_id), damage=int(physical.damage),
                object_id=int(physical.damage_object_id), result_group=int(physical.damage_result_group_id),
                wire=bytes(packet.payload), direction=direction,
            )
            slot = read_self_attack_slot(hit)
            decision = annotate_hit(hit, life, SUMMON_SELF_ATTACK_SPECS, encounter_mode="pve")
            candidates: list[tuple[str, int, dict[str, Any]]] = []
            if decision.evidence is not None:
                candidates.append(("self_attack_slot", _canonical(decision.evidence.player_skill_id), asdict(decision.evidence)))

            selector = selectors.get((entity, life.generation, int(physical.damage_object_id)))
            if selector is not None:
                if selector.birth_hash != life.birth_fingerprint:
                    selector.invalidated = True
                elif selector.order >= hit.order:
                    gaps["selector_not_before_hit"] += 1
                else:
                    selected = _selector_candidate(selector)
                    if selected is not None:
                        candidates.append(("entity_selector_object", selected[0], selected[1]))
            origin = _origin_candidate(
                decoder, entity, int(physical.damage_object_id), float(packet.timestamp_seconds),
                serial, int(physical.record_offset),
            )
            if origin is not None:
                candidates.append(("entity_projectile_object", origin[0], origin[1]))

            unique = {_canonical(row[1]) for row in candidates}
            old = _canonical(_skill(event.skill_id))
            method = str(event.attribution_method or "")
            deterministic = bool(is_deterministic_attribution(method) and old)
            if not candidates:
                if slot is None:
                    reason = "no_complete_self_attack_slot"
                elif slot.monster_skill_id in (0, 0xFFFFFFFF):
                    reason = "empty_self_attack_slot"
                elif (life.template_id, slot.monster_skill_id) not in SUMMON_SELF_ATTACK_SPECS:
                    reason = "self_attack_static_mapping_missing"
                    counts[
                        f"missing_self_slot:{owner_job}:{life.template_id}:"
                        f"{int(slot.monster_skill_id)}"
                    ] += 1
                elif selector is None:
                    reason = "selector_for_same_object_missing"
                elif selector.invalidated:
                    reason = "selector_object_namespace_invalidated"
                else:
                    reason = "selector_static_action_mapping_missing_or_ambiguous"
                gaps[reason] += 1
                if old in FOCUS_SKILLS:
                    focus[str(old)][f"gap:{reason}"] += 1
                continue
            if len(unique) != 1:
                gaps["candidate_routes_conflict"] += 1
                add_example("candidate_routes_conflict", {
                    "capture": path.name, "job": owner_job, "owner": f"0x{life.owner_id:08X}",
                    "entity": entity, "template": life.template_id, "generation": life.generation,
                    "object": int(physical.damage_object_id), "routes": candidates,
                })
                continue
            candidate = next(iter(unique))
            routes = sorted({row[0] for row in candidates})
            route_key = "+".join(routes)
            proof_shape = ";".join(
                f"{row[0]}:{row[2].get('projectile_template_id', row[2].get('monster_skill_id', row[2].get('selector_action', 0)))}"
                for row in candidates
            )
            pattern_key = f"{owner_job}|{candidate}|{life.template_id}|{route_key}|{proof_shape}"
            truth = bool(deterministic and method in INDEPENDENT_TRUTH_METHODS)
            gap = bool(not deterministic and old > 0)
            unknown = bool(not deterministic and old == 0)
            _add_pattern(
                patterns, pattern_key, gap=gap, unknown=unknown, truth=truth,
                match=old == candidate, old_match=old == candidate,
                amount=int(physical.damage), owner=life.owner_id, capture=path.name,
            )
            row = focus[str(candidate)]
            row["candidate_hits"] += 1
            row["candidate_damage"] += int(physical.damage)
            row[f"route:{route_key}"] += 1
            row["gap_hits"] += int(gap)
            row["unknown_hits"] += int(unknown)
            row["truth_hits"] += int(truth)
            row["truth_matches"] += int(truth and old == candidate)
            row["truth_conflicts"] += int(truth and old != candidate)
            row["old_label_conflicts"] += int(old > 0 and old != candidate)
            row["creation_family_unique"] += int(
                tuple(_canonical(v) for v in _SKILL_OBJECT_TEMPLATE_SKILL_IDS.get(life.template_id, ())) == (candidate,)
            )
            row["owner_creation_action_observed"] += int(actions.get((life.owner_id, candidate), 0) > 0)
            if candidate in FOCUS_SKILLS and (gap or unknown or truth and len(examples[f"skill:{candidate}"]) < 4):
                add_example(f"skill:{candidate}", {
                    "capture": path.name, "timestamp_ms": int(event.timestamp_ms),
                    "job": owner_job, "owner_name": owner_name,
                    "owner": f"0x{life.owner_id:08X}", "entity": entity,
                    "template": life.template_id, "entity_generation": life.generation,
                    "object": int(physical.damage_object_id), "target": int(physical.target_actor_id),
                    "amount": int(physical.damage), "candidate_skill": candidate,
                    "candidate_routes": candidates, "baseline_skill": old,
                    "baseline_method": method, "baseline_used_only_as_audit_stratum": True,
                    "owner_creation_action_observed_as_corroboration_only": bool(actions.get((life.owner_id, candidate), 0)),
                })
        return events

    decoder.feed_events = feed_events
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        source.close()
    return {
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "gaps": dict(gaps),
        "focus": {key: dict(value) for key, value in sorted(focus.items(), key=lambda row: int(row[0]))},
        "patterns": {key: dict(value) for key, value in sorted(patterns.items())},
        "examples": dict(examples),
    }


def _merge(results: list[dict[str, Any]], baseline: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    gaps: Counter[str] = Counter()
    focus: dict[str, Counter[str]] = defaultdict(Counter)
    patterns: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        counts.update(result["counts"])
        gaps.update(result["gaps"])
        for key, value in result["focus"].items():
            focus[key].update(value)
        for key, value in result["patterns"].items():
            patterns[key].update(value)
        for key, values in result["examples"].items():
            examples[key].extend(values[: max(0, 12 - len(examples[key]))])

    safe_patterns, rejected_patterns = [], []
    safe_gain: Counter[str] = Counter()
    for key, value in sorted(patterns.items()):
        owners = sum(1 for name in value if name.startswith("owner:"))
        captures = sum(1 for name in value if name.startswith("capture:"))
        truth_hits = int(value["truth_hits"])
        conflicts = int(value["truth_conflicts"])
        # Exact self-slot and packet-static object chains already carry their
        # own client/static certificate.  A newly derived selector mapping is
        # accepted only after at least five independent deterministic controls.
        self_or_object = "self_attack_slot" in key or "entity_projectile_object" in key
        enough_selector_controls = truth_hits >= 5 and int(value["truth_matches"]) == truth_hits
        safe = conflicts == 0 and (self_or_object or enough_selector_controls)
        candidate_gain = int(value["gap_hits"]) + int(value["unknown_hits"])
        decision = (
            "safe_new_gap_candidate"
            if safe and candidate_gain
            else "existing_deterministic_positive_control"
            if safe
            else "not_safe_for_production"
        )
        row = {
            "pattern": key,
            **{name: int(number) for name, number in value.items() if not name.startswith(("owner:", "capture:"))},
            "distinct_owners": owners,
            "distinct_captures": captures,
            "decision": decision,
            "decision_reason": (
                "exact certified self-attack/object chain and zero independent conflicts"
                if safe and self_or_object
                else "unique static selector chain has >=5 independent controls and zero conflicts"
                if safe
                else "independent deterministic conflict"
                if conflicts
                else "selector mapping lacks five independent deterministic controls"
            ),
        }
        (safe_patterns if safe else rejected_patterns).append(row)
        if safe and candidate_gain:
            job, skill, *_ = key.split("|", 3)
            if int(skill) in FOCUS_SKILLS:
                safe_gain[job] += candidate_gain

    job_projection = []
    for job in sorted(TARGET_JOBS):
        row = baseline[job]
        population = int(row["named_population_hits"])
        gain = int(safe_gain[job])
        named_gain = sum(
            int(value["gap_hits"])
            for key, value in patterns.items()
            if key.startswith(f"{job}|")
            and int(key.split("|", 3)[1]) in FOCUS_SKILLS
            and not int(value["truth_conflicts"])
            and ("self_attack_slot" in key or "entity_projectile_object" in key
                 or (int(value["truth_hits"]) >= 5 and int(value["truth_matches"]) == int(value["truth_hits"])))
        )
        unknown_gain = max(0, gain - named_gain)
        projected_denominator = population + unknown_gain
        projected = (
            100.0 * (int(row["deterministic_hits"]) + named_gain + unknown_gain)
            / projected_denominator
            if projected_denominator else 0.0
        )
        job_projection.append(
            {
                "job": job,
                "baseline_named_coverage_percent": float(row["named_coverage_percent"]),
                "baseline_hits_needed_for_95": int(row["hits_needed_for_95"]),
                "safe_focus_candidate_hits": gain,
                "safe_named_gap_hits": named_gain,
                "safe_previously_unnamed_hits": unknown_gain,
                "upper_projection_if_implemented_percent": round(projected, 6),
                "reaches_95_from_this_summon_special_only": projected >= 95.0,
            }
        )
    return {
        "schema": SCHEMA,
        "policy": {
            "candidate_identity": "packet/static chain only; existing skill/name/amount/timing never constructs a candidate",
            "required_chain": "birth -> exact 34/2c owner generation -> entity self-slot OR selector/object/static ACT OR packet-static entity Projectile -> same-object direct37",
            "forbidden": ["time proximity", "old skill name as proof", "creation/context slot as self-attack fallback", "cross-generation owner/object reuse"],
            "production_changed": False,
            "score_or_upload_run": False,
        },
        "fixed_corpus": {
            "captures": len(results),
            "hash_unique": len({row["capture_sha256"] for row in results}),
            "manifest": [{k: row[k] for k in ("capture", "capture_path", "capture_sha256")} for row in results],
        },
        "summary": {
            **dict(counts),
            "gap_reasons": dict(gaps.most_common()),
            "safe_pattern_count_including_positive_controls": len(safe_patterns),
            "safe_new_gap_pattern_count": sum(row["decision"] == "safe_new_gap_candidate" for row in safe_patterns),
            "rejected_pattern_count": len(rejected_patterns),
            "safe_focus_candidate_hits": sum(safe_gain.values()),
        },
        "jobs": job_projection,
        "focus_skills": [
            {"skill_id": int(key), **dict(value)}
            for key, value in sorted(focus.items(), key=lambda row: int(row[0]))
            if int(key) in FOCUS_SKILLS
        ],
        "safe_patterns": safe_patterns,
        "rejected_patterns": rejected_patterns,
        "examples": dict(examples),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 低于 95% 职业：召唤实体链专项审计 v53",
        "",
        f"- 固定语料：{report['fixed_corpus']['captures']} 份抓包，哈希唯一 {report['fixed_corpus']['hash_unique']} 份。",
        "- 本轮只读；未修改生产代码、评分与上传。",
        "- 候选技能只来自 birth、34/2c owner generation、实体自攻击槽、selector/object、客户端静态 ACT/Projectile 表与同对象 direct37。",
        "- 旧技能名、伤害数值和时间邻近只作审计分层，绝不生成候选。",
        "",
        "## 六职业结果",
        "",
        "| 职业 | 原命名覆盖率 | 距离95% | 安全候选Hit | 仅应用本专项的上限投影 | 达到95% |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for row in report["jobs"]:
        lines.append(
            f"| {row['job']} | {row['baseline_named_coverage_percent']:.4f}% | {row['baseline_hits_needed_for_95']} | "
            f"{row['safe_focus_candidate_hits']} | {row['upper_projection_if_implemented_percent']:.4f}% | "
            f"{'是' if row['reaches_95_from_this_summon_special_only'] else '否'} |"
        )
    lines += ["", "## 重点技能", "", "| 技能ID | 候选Hit | 缺口Hit | 未命名Hit | 独立真值冲突 | 主要路径 |", "|---:|---:|---:|---:|---:|---|"]
    for row in report["focus_skills"]:
        routes = ", ".join(
            f"{key[6:]}={value}" for key, value in row.items() if key.startswith("route:")
        ) or "—"
        lines.append(
            f"| {row['skill_id']} | {row.get('candidate_hits', 0)} | {row.get('gap_hits', 0)} | "
            f"{row.get('unknown_hits', 0)} | {row.get('truth_conflicts', 0)} | {routes} |"
        )
    lines += ["", "## 可安全补的模式", ""]
    if report["safe_patterns"]:
        for row in report["safe_patterns"]:
            lines.append(
                f"- `{row['pattern']}`：缺口 {row.get('gap_hits', 0)} Hit，未命名 {row.get('unknown_hits', 0)} Hit，"
                f"独立真值 {row.get('truth_hits', 0)} Hit / 冲突 {row.get('truth_conflicts', 0)}；{row['decision_reason']}。"
            )
    else:
        lines.append("- 没有发现满足本轮严格门槛、可直接进入生产的新增模式。")
    lines += ["", "## 主要阻塞原因", ""]
    for reason, hits in list(report["summary"].get("gap_reasons", {}).items())[:15]:
        lines.append(f"- `{reason}`：{hits} Hit")
    lines += [
        "",
        "## 结论边界",
        "",
        "本报告的覆盖率投影只是‘若实现这些已证实链路’的上限，不等于生产覆盖率已经改变。"
        "未通过的 selector、缺 owner generation、空 self-slot、共享召唤实体和仅有创建技能上下文的记录继续失败关闭。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    workers = max(1, min(2, int(args.workers)))
    captures, baseline = _selection(args.selection)
    output = args.output_dir
    partials = output / "partials"
    partials.mkdir(parents=True, exist_ok=True)
    source_signature = {
        name: _hash(ROOT / name) for name in DEPENDENCIES
    }
    source_signature[str(Path(__file__).relative_to(ROOT))] = _hash(Path(__file__))

    results: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []
    for entry in captures:
        partial = partials / f"{entry['sha256']}.json"
        if partial.is_file() and not args.force:
            saved = json.loads(partial.read_text(encoding="utf-8"))
            if saved.get("source_signature") == source_signature and saved.get("capture_sha256") == entry["sha256"]:
                results.append(saved["result"])
                continue
        pending.append(entry)
    if pending:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(audit_capture, entry): entry for entry in pending}
            for future in as_completed(futures):
                result = future.result()
                partial = partials / f"{result['capture_sha256']}.json"
                partial.write_text(
                    json.dumps({"source_signature": source_signature, "capture_sha256": result["capture_sha256"], "result": result}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                results.append(result)
                print(json.dumps({"done": result["capture"], "remaining": len(pending) - sum(1 for row in results if row["capture_sha256"] in {x["sha256"] for x in pending})}, ensure_ascii=False), flush=True)
    by_hash = {row["capture_sha256"]: row for row in results}
    ordered = [by_hash[row["sha256"]] for row in captures]
    report = _merge(ordered, baseline)
    report["source_signature"] = source_signature
    report["selection_sha256"] = _hash(args.selection)
    json_path = output / "audit_under95_summon_chain_v53.json"
    md_path = output / "audit_under95_summon_chain_v53.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": report["summary"], "jobs": report["jobs"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
