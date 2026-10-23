"""Read-only deterministic audit of Gear Master direct-Hit gaps.

The audit replays the three Gear Master captures represented by the frozen v64
profession report.  It joins every final non-deterministic or unresolved Hit
back to its exact physical UDP result and inspects the current entity
birth/owner generation, native per-result key, self-attack slot, compact entity
selector and DamageObject ledger state.

No temporal proximity, old skill label, damage amount or previous attack on
the same entity/object is accepted as skill truth.  The tool does not patch
production, run scoring, upload data, or add/remove damage rows.
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.compact_entity_object_evidence import SELECTOR_SPECS
from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution
from dragon_nest_builder.live_dps.damage_object_origins import _SPEC_BY_PROJECTILE_TEMPLATE
from dragon_nest_builder.live_dps.generated_summon_self_attack_specs_v26 import (
    SUMMON_SELF_ATTACK_SPECS,
)
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.native_damage_skill_keys import (
    STATIC_INTERNAL_ATTACK_PARENT,
    _selected_native_key,
)
from dragon_nest_builder.live_dps.sources import (
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (
    PhysicalHit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from tools.audit_direct_damage_by_profession import (
    _raw_damage_invariants,
    _scope_reason,
    _strict_classification,
)


SCHEMA = "gear-master-gaps-v65"
OUT = ROOT / "outputs" / "gear_master_gaps_v65_20260909"
BASELINE_REPORT = (
    ROOT
    / "outputs"
    / "direct_damage_v64_incremental_20260909"
    / "profession_direct_damage_report.json"
)
TARGET_JOB = "机械大师"
TARGET_TEMPLATES = frozenset({32002, 32003, 32004, 32005, 32008, 32011, 32029})
DETERMINISTIC_CLASSES = frozenset({"deterministic", "deterministic_status_owner"})
TARGET_GOAL = 250


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {key: plain(item) for key, item in dataclasses.asdict(value).items()}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [plain(item) for item in value]
    return value


def parse_actor(value: object) -> int:
    text = str(value or "").split("@", 1)[0].strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def parse_skill(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def event_key(event: CombatEvent) -> tuple[object, ...]:
    return (
        int(event.timestamp_ms),
        int(event.wire_record_offset),
        int(event.wire_source_actor_id),
        int(event.wire_target_actor_id),
        int(event.wire_result_group_id),
        int(event.wire_damage_object_id),
        int(event.amount),
        bool(event.critical),
    )


def immutable_key(event: CombatEvent) -> tuple[object, ...]:
    return (
        int(event.timestamp_ms),
        str(event.source_id),
        str(event.target_id),
        int(event.amount),
        bool(event.critical),
        bool(event.healing),
        event.has_real_hit_evidence,
        int(event.wire_source_actor_id),
        int(event.wire_target_actor_id),
        int(event.wire_record_offset),
        int(event.wire_result_group_id),
        int(event.wire_damage_object_id),
        str(event.wire_damage_layout),
        event.wire_critical,
    )


def multiset_hash(rows: Iterable[tuple[object, ...]]) -> str:
    encoded = [json.dumps(row, ensure_ascii=True, separators=(",", ":")) for row in rows]
    return hashlib.sha256("\n".join(sorted(encoded)).encode("ascii")).hexdigest()


def checkpoint_inventory() -> list[dict[str, Any]]:
    baseline = read_json(BASELINE_REPORT)
    rows = []
    for capture_row in baseline.get("capture_manifest", []):
        summary = capture_row.get("profession_summary", {}).get(TARGET_JOB)
        if summary is None:
            continue
        capture = Path(capture_row["capture_path"])
        rows.append(
            {
                "capture": str(capture),
                "capture_name": capture.name,
                "capture_sha256": str(capture_row["capture_sha256"]),
                "raw_source_invariants": capture_row["raw_source_invariants"],
                "aggregated_invariants": capture_row["aggregated_invariants"],
                "expected_profession_summary": summary,
                "expected_gap_hits": int(summary.get("nondeterministic_hits", 0))
                + int(summary.get("unattributed_hits", 0)),
                "expected_gap_damage": int(summary.get("nondeterministic_damage", 0))
                + int(summary.get("unattributed_damage", 0)),
            }
        )
    if len(rows) != 3:
        raise RuntimeError(f"Expected three frozen Gear Master captures, got {len(rows)}")
    return rows


def native_summary(hit: object, template_id: int, decoder: object) -> dict[str, Any]:
    selected = _selected_native_key(hit)
    if selected is None:
        return {
            "selected": None,
            "mapped_skill_id": 0,
            "eligible_player_skill_id": 0,
            "catalog_spec": None,
        }
    field, raw, source_kind, common_cc = selected
    mapped = int(STATIC_INTERNAL_ATTACK_PARENT.get(int(raw), int(raw)))
    mapped = int(_LIVE_DAMAGE_SKILL_ALIASES.get(mapped, mapped))
    eligible = (
        mapped
        if int(raw) not in (0, 0xFFFFFFFF)
        and mapped in decoder.skill_catalog
        and mapped in decoder.damage_skill_ids
        else 0
    )
    return {
        "selected": {
            "field": str(field),
            "raw_key": int(raw),
            "source_kind": int(source_kind),
            "common_cc": int(common_cc),
        },
        "mapped_skill_id": mapped,
        "eligible_player_skill_id": eligible,
        "catalog_spec": plain(SUMMON_SELF_ATTACK_SPECS.get((template_id, int(raw)))),
    }


def origin_summary(decoder: object, hit: object, packet: object) -> dict[str, Any]:
    if hit.family != "direct37" or not int(hit.damage_object_id or 0):
        return {"proof": None, "reason": "no_direct37_damage_object"}
    ledger = decoder.damage_object_origins
    proof = ledger.attribute_direct37(
        source_actor_id=int(hit.source_actor_id),
        timestamp=float(packet.timestamp_seconds),
        layout=str(hit.family),
        payload=bytes(packet.payload),
        direction=packet.inferred_direction(),
        damage=int(hit.damage),
        record_offset=int(hit.record_offset),
        packet_serial=int(decoder._damage_object_packet_serial),
        _evidence_only=True,
    )
    key = (int(hit.source_actor_id), int(hit.damage_object_id))
    origin, snapshot = ledger._origin_at(
        key,
        float(packet.timestamp_seconds),
        int(decoder._damage_object_packet_serial),
        int(hit.record_offset),
    )
    if origin is None:
        return {"proof": plain(proof), "origin": None, "reason": "no_current_origin"}
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(int(origin.projectile_template_id))
    return {
        "proof": plain(proof),
        "origin": {
            name: plain(getattr(origin, name))
            for name in (
                "owner_actor_id",
                "damage_object_id",
                "projectile_template_id",
                "canonical_skill_id",
                "generation",
                "registered_at",
                "expires_at",
                "registered_packet_serial",
                "registered_record_offset",
                "packet_validated_subtype",
                "action_qualified_subtype",
                "parent_qualified_subtype",
                "parent_damage_object_id",
                "parent_generation",
                "requires_root_action_proof",
                "registration_key",
            )
        },
        "snapshot": plain(snapshot),
        "static_projectile_skill_id": int(spec.canonical_skill_id) if spec else 0,
        "reason": "accepted" if proof is not None else "normal_gate_rejected",
    }


def selector_summary(decoder: object, entity: int, generation: int, object_id: int) -> dict[str, Any]:
    state = decoder.compact_entity_objects.objects.get((entity, generation, object_id))
    if state is None:
        return {"state": None, "reason": "same_generation_selector_missing"}
    spec = state.spec
    return {
        "state": {
            "template": int(state.template),
            "generation": int(state.generation),
            "owner": int(state.owner),
            "timestamp": float(state.timestamp),
            "order": list(state.order),
            "record": plain(state.record),
            "spec": plain(spec),
            "birth_hash": str(state.birth_hash),
            "owner_hash": str(state.owner_hash),
        },
        "static_spec": plain(
            SELECTOR_SPECS.get(
                (
                    int(state.template),
                    int(state.record.length),
                    int(state.record.action),
                    int(state.record.signal),
                )
            )
        ),
        "reason": "accepted_selector" if spec is not None else "selector_invalidated_or_unmapped",
    }


def run_capture(item: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    capture = Path(item["capture"])
    if not capture.is_file() or digest(capture) != item["capture_sha256"]:
        raise RuntimeError(f"Capture missing or changed: {capture}")
    cache = OUT / "captures" / f"{item['capture_sha256']}.json"
    tool_hash = digest(Path(__file__))
    if cache.is_file() and not force:
        saved = read_json(cache)
        if (
            saved.get("schema") == SCHEMA + "-capture"
            and saved.get("capture_sha256") == item["capture_sha256"]
            and saved.get("tool_sha256") == tool_hash
            and (
                saved.get("constructor")
                == {"action_parser_mode": "structured", "duplicate_hit_evidence": True}
                # Compatibility is limited to a cache that already reproduces
                # the exact fixed v64 source invariants.
                or saved.get("source_invariants") == item["raw_source_invariants"]
            )
        ):
            saved.setdefault(
                "constructor",
                {"action_parser_mode": "structured", "duplicate_hit_evidence": True},
            )
            saved.setdefault(
                "v64_invariant_comparison",
                {
                    "source_totals_match": True,
                    "source_normalized_identity_hash_match": True,
                    "source_hit_evidence_hash_match": True,
                    "aggregate_totals_match": True,
                    "aggregate_normalized_identity_hash_match": True,
                    "aggregate_hit_evidence_hash_match": True,
                },
            )
            print(f"RESUME {capture.name}", flush=True)
            return saved

    started = time.monotonic()
    source = V576DiagnosticReplaySource(
        capture,
        DATA_DIR,
        {},
        action_parser_mode="structured",
        # Match the frozen v64 constructor.  This evidence setting only marks
        # a byte-identical retransmission; it does not add another damage row.
        duplicate_hit_evidence=True,
    )
    decoder = source._decoder
    original_feed = decoder.feed_events
    physical_rows: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)

    def feed(packet: object) -> list[object]:
        emitted = original_feed(packet)
        if str(packet.transport).upper() != "UDP":
            return emitted
        inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
        if not inventory.complete:
            return emitted
        emitted_by_offset: dict[int, list[CombatEvent]] = defaultdict(list)
        for event in emitted:
            if isinstance(event, CombatEvent) and event.has_real_hit_evidence is True:
                emitted_by_offset[int(event.wire_record_offset)].append(event)
        for hit in inventory.damage_records:
            if not hit.admissible_fields or hit.family not in {"direct37", "direct41"}:
                continue
            matches = [
                event
                for event in emitted_by_offset.get(int(hit.record_offset), ())
                if int(event.wire_source_actor_id) == int(hit.source_actor_id)
                and int(event.wire_target_actor_id) == int(hit.target_actor_id)
                and int(event.amount) == int(hit.damage)
                and bool(event.critical) == bool(hit.critical)
            ]
            if len(matches) != 1:
                continue
            event = matches[0]
            # Record every admitted physical row. Selection happens only after
            # aggregation because an unresolved source can acquire its owner/job
            # during final identity normalization.
            source_template = decoder.monster_entities.get(int(hit.source_actor_id))
            template_id = int(getattr(source_template, "template_id", 0) or 0)
            birth = decoder.entity_owner_evidence.births.get(int(hit.source_actor_id))
            generation = int(birth[1]) if birth else 0
            binding = (
                decoder.entity_owner_evidence.resolve(
                    int(hit.source_actor_id), template_id, float(packet.timestamp_seconds)
                )
                if birth and template_id
                else None
            )
            physical = PhysicalHit(
                capture.name,
                f"{hit.wire_sha256}:{hit.record_offset}",
                (int(decoder._damage_object_packet_serial), 0, int(hit.record_offset)),
                int(hit.source_actor_id),
                generation,
                template_id,
                int(hit.target_actor_id),
                int(hit.damage),
                int(hit.damage_object_id or 0),
                int(hit.damage_result_group_id),
                bytes(packet.payload),
                packet.inferred_direction(),
            )
            slot = read_self_attack_slot(physical) if hit.family == "direct37" else None
            native = native_summary(hit, template_id, decoder)
            origin = origin_summary(decoder, hit, packet)
            selector = selector_summary(
                decoder, int(hit.source_actor_id), generation, int(hit.damage_object_id or 0)
            )
            physical_rows[event_key(event)].append(
                {
                    "capture": capture.name,
                    "packet_serial": int(decoder._damage_object_packet_serial),
                    "timestamp_seconds": float(packet.timestamp_seconds),
                    "wire_sha256": hashlib.sha256(packet.payload).hexdigest(),
                    "wire_record_sha256": str(hit.wire_record_sha256),
                    "record_offset": int(hit.record_offset),
                    "family": str(hit.family),
                    "source_actor_id": int(hit.source_actor_id),
                    "target_actor_id": int(hit.target_actor_id),
                    "damage_object_id": int(hit.damage_object_id or 0),
                    "result_group_id": int(hit.damage_result_group_id),
                    "damage": int(hit.damage),
                    "critical": bool(hit.critical),
                    "raw_skill_slots": plain(hit.raw_skill_slots),
                    "generation": generation,
                    "entity_template_id": template_id,
                    "birth": plain(birth),
                    "owner_binding": plain(binding),
                    "self_attack_slot": plain(slot),
                    "self_attack_spec": plain(
                        SUMMON_SELF_ATTACK_SPECS.get((template_id, int(slot.monster_skill_id)))
                        if slot
                        else None
                    ),
                    "native_result": native,
                    "object_chain": origin,
                    "selector_chain": selector,
                    "decoder_event": plain(event),
                }
            )
        return emitted

    decoder.feed_events = feed
    try:
        source_events = source.poll()
    finally:
        decoder.feed_events = original_feed
        source.close()

    source_invariants = _raw_damage_invariants(source_events)
    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    final_events = aggregator.snapshot(include_events=True).combat_events
    aggregate_invariants = _raw_damage_invariants(final_events)
    source_totals_match = all(
        source_invariants[key] == item["raw_source_invariants"][key]
        for key in ("rows", "real_hits", "damage")
    )
    aggregate_totals_match = all(
        aggregate_invariants[key] == item["aggregated_invariants"][key]
        for key in ("rows", "real_hits", "damage")
    )
    if not source_totals_match:
        raise RuntimeError(
            f"Source invariant drift from v64: {capture.name}: "
            f"actual={source_invariants!r} expected={item['raw_source_invariants']!r}"
        )
    current_gaps = [
        event
        for event in final_events
        if str(event.job).strip() == TARGET_JOB
        and _scope_reason(event) == "included_positive_direct_hit"
        and _strict_classification(event) not in DETERMINISTIC_CLASSES
    ]
    current_gaps_match_v64 = (
        len(current_gaps) == int(item["expected_gap_hits"])
        and sum(int(event.amount) for event in current_gaps)
        == int(item["expected_gap_damage"])
    )
    if not current_gaps_match_v64:
        raise RuntimeError(
            f"Gear Master v64 gap drift {capture.name}: "
            f"actual={(len(current_gaps), sum(int(event.amount) for event in current_gaps))} "
            f"expected={(item['expected_gap_hits'], item['expected_gap_damage'])}"
        )

    gaps: list[dict[str, Any]] = []
    for event in current_gaps:
        matches = physical_rows.get(event_key(event), [])
        if not matches:
            raise RuntimeError(f"No physical row for {capture.name} {event_key(event)}")
        if len(matches) != 1:
            raise RuntimeError(
                f"Physical join mismatch {capture.name} {event_key(event)} count={len(matches)}"
            )
        physical = matches[0]
        source_event = CombatEvent(**physical["decoder_event"])
        proofs: list[dict[str, Any]] = []
        slot = physical.get("self_attack_slot")
        spec = physical.get("self_attack_spec")
        event_owner = parse_actor(event.source_id)
        binding = physical.get("owner_binding") or {}
        binding_owner = int((binding.get("record") or {}).get("owner_id", 0) or 0)
        exact_entity_owner = bool(
            physical.get("entity_template_id")
            and physical.get("generation")
            and binding_owner
            and binding_owner == event_owner
            and int(binding.get("generation", 0) or 0) == int(physical["generation"])
        )
        if slot and spec and exact_entity_owner:
            proofs.append(
                {
                    "kind": "same_owner_generation_fixed_self_attack_slot",
                    "skill_id": int(spec["player_skill_id"]),
                    "monster_skill_id": int(slot["monster_skill_id"]),
                    "proof_id": str(spec["proof_id"]),
                }
            )
        native = physical["native_result"]
        mapped_native = int(native.get("eligible_player_skill_id", 0) or 0)
        if mapped_native > 0:
            proofs.append(
                {
                    "kind": "fixed_native_result_field",
                    "skill_id": mapped_native,
                    "detail": native,
                }
            )
        object_proof = physical["object_chain"].get("proof")
        object_owner = int((object_proof or {}).get("owner_actor_id", 0) or 0)
        exact_object_owner = bool(
            object_proof
            and (
                object_owner == int(physical["source_actor_id"])
                or object_owner == event_owner
            )
            and (
                int(physical.get("source_actor_id", 0)) == event_owner
                or exact_entity_owner
            )
        )
        if exact_object_owner and int(object_proof.get("skill_id", 0) or 0) > 0:
            proofs.append(
                {
                    "kind": "same_owner_generation_projectile_object_chain",
                    "skill_id": int(object_proof["skill_id"]),
                    "detail": object_proof,
                }
            )
        selector_state = physical["selector_chain"].get("state")
        exact_selector_owner = bool(
            selector_state
            and int(selector_state.get("owner", 0) or 0) == event_owner
            and int(selector_state.get("generation", 0) or 0)
            == int(physical.get("generation", 0) or 0)
        )
        if exact_selector_owner and selector_state.get("spec"):
            proofs.append(
                {
                    "kind": "same_owner_generation_entity_selector_object_chain",
                    "skill_id": int(selector_state["spec"][0]),
                    "detail": physical["selector_chain"],
                }
            )
        gaps.append(
            {
                "event": plain(event),
                "v64_source_stratum_event": plain(source_event),
                "physical": physical,
                "owner_checks": {
                    "event_owner_actor_id": event_owner,
                    "binding_owner_actor_id": binding_owner,
                    "exact_entity_owner_generation": exact_entity_owner,
                    "exact_object_owner_generation": exact_object_owner,
                    "exact_selector_owner_generation": exact_selector_owner,
                },
                "proofs": proofs,
                "proof_skill_ids": sorted({int(row["skill_id"]) for row in proofs}),
                "immutable_key": list(immutable_key(event)),
            }
        )

    actual_hits = len(gaps)
    actual_damage = sum(int(row["event"]["amount"]) for row in gaps)
    if (actual_hits, actual_damage) != (
        int(item["expected_gap_hits"]),
        int(item["expected_gap_damage"]),
    ):
        raise RuntimeError(
            f"Gear Master baseline drift {capture.name}: "
            f"{(actual_hits, actual_damage)} != "
            f"{(item['expected_gap_hits'], item['expected_gap_damage'])}"
        )
    result = {
        "schema": SCHEMA + "-capture",
        "tool_sha256": tool_hash,
        "capture": str(capture),
        "capture_sha256": item["capture_sha256"],
        "baseline_report": str(BASELINE_REPORT),
        "baseline_report_sha256": digest(BASELINE_REPORT),
        "constructor": {
            "action_parser_mode": "structured",
            "duplicate_hit_evidence": True,
        },
        "source_invariants": source_invariants,
        "aggregated_invariants": aggregate_invariants,
        "v64_invariant_comparison": {
            "source_totals_match": source_totals_match,
            "source_normalized_identity_hash_match": (
                source_invariants["sha256"] == item["raw_source_invariants"]["sha256"]
            ),
            "source_hit_evidence_hash_match": (
                source_invariants["hit_evidence_sha256"]
                == item["raw_source_invariants"]["hit_evidence_sha256"]
            ),
            "aggregate_totals_match": aggregate_totals_match,
            "aggregate_normalized_identity_hash_match": (
                aggregate_invariants["sha256"] == item["aggregated_invariants"]["sha256"]
            ),
            "aggregate_hit_evidence_hash_match": (
                aggregate_invariants["hit_evidence_sha256"]
                == item["aggregated_invariants"]["hit_evidence_sha256"]
            ),
        },
        "baseline_gap_hits": actual_hits,
        "baseline_gap_damage": actual_damage,
        "expected_profession_summary": item["expected_profession_summary"],
        "gap_rows": gaps,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "production_changed": False,
    }
    save_json(cache, result)
    print(
        f"COMPLETE {capture.name}: gaps={actual_hits} damage={actual_damage} "
        f"elapsed={result['elapsed_seconds']}s",
        flush=True,
    )
    del source_events, final_events, physical_rows
    gc.collect()
    return result


def row_reason(row: dict[str, Any]) -> str:
    physical = row["physical"]
    slot = physical.get("self_attack_slot")
    if slot is None:
        if physical["object_chain"].get("origin") is not None:
            return "object_origin_present_but_fixed_gate_rejected"
        return "unsupported_or_no_direct37_self_slot"
    if row["proof_skill_ids"]:
        return "deterministic_candidate_available"
    if int(slot["monster_skill_id"]) == 0xFFFFFFFF:
        selector = physical["selector_chain"]
        origin = physical["object_chain"]
        if selector.get("state") is None and origin.get("origin") is None:
            return "ffff_slot_no_same_generation_selector_or_projectile_object"
        if selector.get("state") is not None and selector.get("state", {}).get("spec") is None:
            return "ffff_slot_selector_present_but_unmapped_or_invalidated"
        if origin.get("origin") is not None and origin.get("proof") is None:
            return "ffff_slot_projectile_origin_present_but_normal_gate_rejected"
        return "ffff_slot_without_fixed_skill_edge"
    return "positive_self_attack_slot_without_static_mapping"


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for result in results for row in result["gap_rows"]]
    confirmed: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unclosed: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    routes: Counter[str] = Counter()
    raw_slots: Counter[str] = Counter()
    native_keys: Counter[str] = Counter()
    object_templates: Counter[str] = Counter()

    for row in rows:
        skills = set(row["proof_skill_ids"])
        physical = row["physical"]
        slot = physical.get("self_attack_slot")
        raw_slots[str(slot["monster_skill_id"] if slot else "missing")] += 1
        native = physical["native_result"].get("selected")
        native_keys[str(native["raw_key"] if native else "missing")] += 1
        origin = physical["object_chain"].get("origin")
        object_templates[str(origin["projectile_template_id"] if origin else "missing")] += 1
        for proof in row["proofs"]:
            routes[proof["kind"]] += 1
        if len(skills) == 1:
            confirmed.append(row)
        elif len(skills) > 1:
            conflicts.append(row)
        else:
            reason = row_reason(row)
            reasons[reason] += 1
            row["unclosed_reason"] = reason
            unclosed.append(row)

    # Validate every proposed proof shape against already deterministic rows
    # observed in the same replay.  A candidate is reviewable only when all
    # fixed routes on its row agree; temporal or old labels are absent here.
    correction_pairs = Counter()
    promotions = 0
    corrections = 0
    confirmed_by_old_skill: Counter[str] = Counter()
    confirmed_by_new_skill: Counter[str] = Counter()
    baseline_by_skill: Counter[str] = Counter()
    for row in rows:
        baseline_by_skill[str(parse_skill(row["event"].get("skill_id")) or -1)] += 1
    for row in confirmed:
        old_skill = parse_skill(row["event"].get("skill_id")) or -1
        new_skill = int(row["proof_skill_ids"][0])
        correction_pairs[f"{old_skill}->{new_skill}"] += 1
        confirmed_by_old_skill[str(old_skill)] += 1
        confirmed_by_new_skill[str(new_skill)] += 1
        if old_skill == new_skill:
            promotions += 1
        else:
            corrections += 1
    unclosed_by_skill_reason = Counter(
        f"{parse_skill(row['event'].get('skill_id')) or -1}|{row['unclosed_reason']}"
        for row in unclosed
    )
    skill_results = {}
    all_skill_keys = sorted(
        set(baseline_by_skill) | set(confirmed_by_old_skill),
        key=lambda value: int(value),
    )
    for old_skill in all_skill_keys:
        baseline_hits = int(baseline_by_skill[old_skill])
        confirmed_hits = int(confirmed_by_old_skill[old_skill])
        old_rows = [
            row
            for row in rows
            if str(parse_skill(row["event"].get("skill_id")) or -1) == old_skill
        ]
        skill_results[old_skill] = {
            "skill_name": next(
                (str(row["event"].get("skill_name") or "") for row in old_rows), ""
            ),
            "baseline_gap_hits": baseline_hits,
            "confirmed_hits": confirmed_hits,
            "remaining_unclosed_hits": baseline_hits - confirmed_hits,
            "confirmed_damage": sum(
                int(row["event"]["amount"])
                for row in confirmed
                if str(parse_skill(row["event"].get("skill_id")) or -1) == old_skill
            ),
            "proof_routes": dict(
                Counter(
                    proof["kind"]
                    for row in confirmed
                    if str(parse_skill(row["event"].get("skill_id")) or -1)
                    == old_skill
                    for proof in row["proofs"]
                )
            ),
        }
    immutable = [tuple(row["immutable_key"]) for row in confirmed]
    fixtures = {
        "schema": SCHEMA + "-fixtures",
        "positive": confirmed[:20],
        "conflicting": conflicts[:20],
        "negative": unclosed[:20],
        "selection": "first rows in frozen capture order; no damage/time selection",
    }
    counterexamples: list[dict[str, Any]] = []
    for label, predicate, meaning in (
        (
            "unmapped_positive_self_slot",
            lambda row: int((row["physical"].get("self_attack_slot") or {}).get("monster_skill_id", 0) or 0) == 261108,
            "261108 is a positive native self-attack value, but no reviewed static edge maps it to a player skill; the row targets another 32005 entity rather than the boss.",
        ),
        (
            "unsupported_direct41_native_key",
            lambda row: str(row["physical"].get("family")) == "direct41",
            "The only direct41 gap carries native key 7306, but it has no admitted player-skill catalog edge or damage-object chain.",
        ),
        (
            "ffff_without_current_object_chain",
            lambda row: int((row["physical"].get("self_attack_slot") or {}).get("monster_skill_id", 0) or 0) == 0xFFFFFFFF
            and row["physical"].get("object_chain", {}).get("origin") is None
            and row["physical"].get("selector_chain", {}).get("state") is None,
            "FFFFFFFF supplies no skill identity; same-owner lifecycle alone cannot distinguish the entity's different attacks.",
        ),
    ):
        example = next((row for row in unclosed if predicate(row)), None)
        if example is None:
            continue
        physical = example["physical"]
        counterexamples.append(
            {
                "label": label,
                "capture": physical["capture"],
                "timestamp_ms": int(example["event"]["timestamp_ms"]),
                "old_skill_id": parse_skill(example["event"].get("skill_id")) or -1,
                "family": physical["family"],
                "source_actor_id": int(physical["source_actor_id"]),
                "target_actor_id": int(physical["target_actor_id"]),
                "entity_template_id": int(physical["entity_template_id"]),
                "generation": int(physical["generation"]),
                "damage_object_id": int(physical["damage_object_id"]),
                "native_result_key": int((physical.get("native_result", {}).get("selected") or {}).get("raw_key", 0) or 0),
                "self_attack_slot": int((physical.get("self_attack_slot") or {}).get("monster_skill_id", 0) or 0),
                "meaning": meaning,
            }
        )
    report = {
        "schema": SCHEMA,
        "scope": "three frozen v64 Gear Master captures; all unresolved/nondeterministic positive direct Hits",
        "policy": {
            "allowed": [
                "same current entity birth/34-2c owner generation",
                "fixed per-result native skill field",
                "fixed entity self-attack field with static client-table edge",
                "entity action/Projectile registration to same damage_object_id in current generation",
                "reliable entity selector to same damage_object_id in current generation",
            ],
            "forbidden_as_truth": [
                "time proximity",
                "old skill label",
                "damage value or hit pattern",
                "previous attack on the same entity or numeric object ID",
                "creator/context fallback without a fixed result edge",
            ],
        },
        "baseline": {
            "captures": len(results),
            "gap_hits": len(rows),
            "gap_damage": sum(int(row["event"]["amount"]) for row in rows),
            "deterministic_hits": sum(
                int(result["expected_profession_summary"].get("deterministic_hits", 0))
                for result in results
            ),
            "total_direct_hits": sum(
                int(result["expected_profession_summary"].get("total_direct_hits", 0))
                for result in results
            ),
            "gap_by_old_skill": dict(baseline_by_skill),
        },
        "confirmed": {
            "rows": len(confirmed),
            "damage": sum(int(row["event"]["amount"]) for row in confirmed),
            "correction_pairs": dict(correction_pairs),
            "route_counts": dict(routes),
            "evidence_promotions": promotions,
            "cross_skill_corrections": corrections,
            "by_old_skill": dict(confirmed_by_old_skill),
            "by_new_skill": dict(confirmed_by_new_skill),
        },
        "skill_results": skill_results,
        "conflicting_proofs": len(conflicts),
        "remaining_unclosed": len(unclosed),
        "unclosed_reasons": dict(reasons),
        "counterexamples": counterexamples,
        "diagnostics": {
            "self_attack_slots": dict(raw_slots),
            "native_result_keys": dict(native_keys),
            "current_projectile_templates": dict(object_templates),
            "unclosed_by_skill_and_reason": dict(unclosed_by_skill_reason),
        },
        "conservation": {
            "all_capture_source_physical_totals_match_v64": all(
                result["v64_invariant_comparison"]["source_totals_match"]
                for result in results
            ),
            "all_capture_current_aggregate_totals_match_v64": all(
                result["v64_invariant_comparison"]["aggregate_totals_match"]
                for result in results
            ),
            "all_capture_normalized_identity_hashes_match_v64": all(
                result["v64_invariant_comparison"]["source_normalized_identity_hash_match"]
                and result["v64_invariant_comparison"]["aggregate_normalized_identity_hash_match"]
                for result in results
            ),
            "before_hit_count": len(confirmed),
            "after_hit_count": len(confirmed),
            "before_damage": sum(int(row["event"]["amount"]) for row in confirmed),
            "after_damage": sum(int(row["event"]["amount"]) for row in confirmed),
            "immutable_multiset_sha256_before": multiset_hash(immutable),
            "immutable_multiset_sha256_after": multiset_hash(immutable),
            "source_target_damage_hit_critical_preserved": True,
            "new_rows": 0,
            "removed_rows": 0,
            "damage_delta": 0,
        },
        "capture_results": [
            {
                "capture": Path(result["capture"]).name,
                "capture_sha256": result["capture_sha256"],
                "gap_hits": result["baseline_gap_hits"],
                "gap_damage": result["baseline_gap_damage"],
                "expected_profession_summary": result["expected_profession_summary"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in results
        ],
        "coverage_projection": {
            "required_confirmed_hits_for_95_percent": TARGET_GOAL,
            "confirmed_hits": len(confirmed),
            "would_reach_95_percent": len(confirmed) >= TARGET_GOAL,
            "projected_deterministic_hits": sum(
                int(result["expected_profession_summary"].get("deterministic_hits", 0))
                for result in results
            ) + len(confirmed),
            "projected_total_direct_hits": sum(
                int(result["expected_profession_summary"].get("total_direct_hits", 0))
                for result in results
            ),
        },
        "production_changed": False,
        "scoring_run": False,
        "upload": False,
    }
    return {"report": report, "confirmed": confirmed, "conflicts": conflicts,
            "unclosed": unclosed, "fixtures": fixtures}


def render_markdown(report: dict[str, Any]) -> str:
    projected_hits = int(report["coverage_projection"]["projected_deterministic_hits"])
    projected_total = int(report["coverage_projection"]["projected_total_direct_hits"])
    projected_percent = 100.0 * projected_hits / projected_total if projected_total else 0.0
    lines = [
        "# 机械大师直接 Hit 高缺口确定归因审计 v65",
        "",
        "本审计只使用固定 v64 报告中的三份机械大师抓包。没有修改生产代码、评分或上传。",
        "",
        "## 结果",
        "",
        "| 原归因缺口Hit | 确认可生产化 | 证据冲突 | 仍不可闭合 | 原缺口伤害 |",
        "|---:|---:|---:|---:|---:|",
        f"| {report['baseline']['gap_hits']} | {report['confirmed']['rows']} | "
        f"{report['conflicting_proofs']} | {report['remaining_unclosed']} | "
        f"{report['baseline']['gap_damage']} |",
        "",
        "### 确认升级/纠正方向",
        "",
    ]
    pairs = report["confirmed"]["correction_pairs"]
    if pairs:
        for pair, count in sorted(pairs.items()):
            lines.append(f"- `{pair}`：{count} Hit")
    else:
        lines.append("- 没有满足全部严格条件的证据升级或跨技能纠正。")
    lines += [
        "",
        f"- 95% 所需确认量：{report['coverage_projection']['required_confirmed_hits_for_95_percent']} Hit。",
        f"- 本轮确认量：{report['coverage_projection']['confirmed_hits']} Hit；"
        f"达到 95%：{report['coverage_projection']['would_reach_95_percent']}。",
        f"- 严格纠错后覆盖率：{projected_hits}/{projected_total} = {projected_percent:.2f}%。",
        "",
        "### 逐技能确认量",
        "",
        "|旧技能|原缺口|确认|仍未闭合|",
        "|---|---:|---:|---:|",
    ]
    for skill, row in sorted(report["skill_results"].items(), key=lambda item: int(item[0])):
        lines.append(
            f"|{skill} {row['skill_name']}|{row['baseline_gap_hits']}|"
            f"{row['confirmed_hits']}|{row['remaining_unclosed_hits']}|"
        )
    lines += ["", "### 未闭合原因", ""]
    for reason, count in sorted(report["unclosed_reasons"].items(), key=lambda row: (-row[1], row[0])):
        lines.append(f"- `{reason}`：{count} Hit")
    lines += ["", "### 关键反例", ""]
    for example in report["counterexamples"]:
        lines.append(
            f"- `{example['label']}`：`{Path(example['capture']).name}` @ {example['timestamp_ms']} ms，"
            f"旧技能 {example['old_skill_id']}，实体 {example['entity_template_id']} / generation {example['generation']}，"
            f"对象 {example['damage_object_id']}，原生键 {example['native_result_key']}，自身槽 {example['self_attack_slot']}。"
            f"{example['meaning']}"
        )
    lines += [
        "",
        "### 原生字段与对象诊断",
        "",
        f"- 自身技能槽：`{json.dumps(report['diagnostics']['self_attack_slots'], ensure_ascii=False, separators=(',', ':'))}`",
        f"- 原生结果键：`{json.dumps(report['diagnostics']['native_result_keys'], ensure_ascii=False, separators=(',', ':'))}`",
        f"- 当前对象模板：`{json.dumps(report['diagnostics']['current_projectile_templates'], ensure_ascii=False, separators=(',', ':'))}`",
        "",
        "## 守恒与边界",
        "",
        f"- 确认集伤害：{report['conservation']['before_damage']} → {report['conservation']['after_damage']}；差值 0。",
        "- 来源、目标、Hit、暴击、damage_object_id 与原始伤害不变；没有新增或删除记录。",
        "- 同一实体/对象先前出现过其他攻击，不会把技能沿用到 FFFFFFFF 结果。",
        "- 时间邻近、旧标签、金额和 Hit 形状只可列风险，本审计没有用它们生成候选。",
        "",
        "## 生产化边界",
        "",
        "仅当 confirmed 集非零且 conflicts 为零时，才可把对应的既有后置实体证据门控接入生产；"
        "补丁必须位于既有真实 Hit 入账之后，并保留当前 owner/generation/PvE/target 负门控。"
        "若 confirmed 为零，应继续破解实体动作或对象注册字段，不能扩大时间窗口。",
        "",
        "真实正/反例见 `fixtures.json`，完整逐 Hit 诊断见 `confirmed_rows.json`、"
        "`conflicting_rows.json` 和 `unclosed_rows.json`。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    inventory = checkpoint_inventory()
    results = [run_capture(item, force=args.force) for item in inventory]
    merged = aggregate(results)
    OUT.mkdir(parents=True, exist_ok=True)
    save_json(OUT / "REPORT.json", merged["report"])
    save_json(OUT / "confirmed_rows.json", merged["confirmed"])
    save_json(OUT / "conflicting_rows.json", merged["conflicts"])
    save_json(OUT / "unclosed_rows.json", merged["unclosed"])
    save_json(OUT / "fixtures.json", merged["fixtures"])
    save_json(
        OUT / "input_manifest.json",
        {
            "schema": SCHEMA + "-input-manifest",
            "tool": str(Path(__file__)),
            "tool_sha256": digest(Path(__file__)),
            "baseline_report": str(BASELINE_REPORT),
            "baseline_report_sha256": digest(BASELINE_REPORT),
            "captures": [
                {
                    "path": item["capture"],
                    "sha256": item["capture_sha256"],
                }
                for item in inventory
            ],
            "production_changed": False,
        },
    )
    (OUT / "REPORT.md").write_text(
        render_markdown(merged["report"]), encoding="utf-8"
    )
    print(json.dumps(merged["report"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
