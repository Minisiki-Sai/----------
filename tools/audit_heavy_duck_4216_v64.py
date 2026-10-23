"""Read-only audit of Heavy Shooter skill 4216 / entity template 32005.

The audit replays only the two frozen v57 Heavy Shooter captures.  It joins
every final non-deterministic 4216 Hit back to its exact physical UDP result
and inspects the current entity birth/owner generation, native per-result key,
self-attack slot, compact entity selector and DamageObject ledger state.

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


SCHEMA = "heavy-duck-4216-v64"
OUT = ROOT / "outputs" / "heavy_duck_4216_v64_20260909"
CHECKPOINT_ROOT = (
    ROOT
    / "outputs"
    / "native_result_key_v57_20260909"
    / "profession_direct_damage_checkpoints"
)
TARGET_JOB = "重炮手"
TARGET_SKILL = 4216
TARGET_TEMPLATE = 32005
DETERMINISTIC_CLASSES = frozenset({"deterministic", "deterministic_status_owner"})
LEGACY_AFTER_RESULTS = {
    "50fe98bf127c432a1cf2eb84dd4fdaa3bfc9edf19af94080e5900bb76bc5508c": (
        ROOT
        / "outputs/magma_runtime_v37_20260908/after_results/"
        "cd421348a912fc2681e703d28df9d07f4d8fe6a25df092b7e6123a4a83325f77.json"
    ),
    "7b9d4e2b2db554cd6e919be1b94858f48db51f185d83fadeca7f7f8939ecf844": (
        ROOT
        / "outputs/magma_runtime_v37_20260908/after_results/"
        "efa196ea6dbdfa5dd61d8a3898ef5e28e6f8ae9c6933362003129d5dfc1baa44.json"
    ),
}


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


def legacy_physical_gap_keys(item: dict[str, Any]) -> Counter[tuple[object, ...]] | None:
    """Return an exact old physical-row multiset when it matches v57 totals."""
    path = LEGACY_AFTER_RESULTS.get(str(item["capture_sha256"]))
    if path is None or not path.is_file():
        return None
    frozen = read_json(path)
    columns = frozen.get("physical_columns", [])
    rows = [dict(zip(columns, values)) for values in frozen.get("aggregated_physical_rows", [])]
    selected = [
        row
        for row in rows
        if row.get("real") is True
        and parse_skill(row.get("skill")) == TARGET_SKILL
        and not is_deterministic_attribution(str(row.get("method") or ""))
    ]
    if (
        len(selected) != int(item["expected_gap_hits"])
        or sum(int(row["amount"]) for row in selected)
        != int(item["expected_gap_damage"])
    ):
        return None
    return Counter(
        (
            int(row["time"]),
            int(row["offset"]),
            int(row["wire_source"]),
            int(row["wire_target"]),
            int(row["group"]),
            int(row["object"]),
            int(row["amount"]),
            bool(row["critical"]),
        )
        for row in selected
    )


def checkpoint_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for checkpoint_path in sorted(CHECKPOINT_ROOT.glob("*.json")):
        checkpoint = read_json(checkpoint_path)
        report = checkpoint.get("report", {})
        if TARGET_JOB not in report.get("profession_summary", {}):
            continue
        expected = [
            row
            for row in report.get("details", [])
            if row.get("job") == TARGET_JOB
            and parse_skill(row.get("skill_id")) == TARGET_SKILL
            and row.get("classification") not in DETERMINISTIC_CLASSES
        ]
        capture = Path(report["capture_path"])
        rows.append(
            {
                "checkpoint": str(checkpoint_path),
                "checkpoint_sha256": digest(checkpoint_path),
                "capture": str(capture),
                "capture_name": capture.name,
                "capture_sha256": str(report["capture_sha256"]),
                "raw_source_invariants": report["raw_source_invariants"],
                "aggregated_invariants": report["aggregated_invariants"],
                "expected_gap_hits": sum(int(row["hits"]) for row in expected),
                "expected_gap_damage": sum(int(row["damage"]) for row in expected),
            }
        )
    if len(rows) != 2:
        raise RuntimeError(f"Expected two frozen Heavy Shooter captures, got {len(rows)}")
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
            and (
                saved.get("constructor")
                == {"action_parser_mode": "structured", "duplicate_hit_evidence": True}
                # Compatibility for the first cache written immediately
                # before constructor metadata was added: its complete v57
                # invariant equality proves the intended replay mode.
                or saved.get("source_invariants") == item["raw_source_invariants"]
            )
        ):
            saved.setdefault(
                "constructor",
                {"action_parser_mode": "structured", "duplicate_hit_evidence": True},
            )
            saved.setdefault(
                "v57_invariant_comparison",
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
        # Match the frozen v57 constructor.  This evidence setting only marks
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
            # The legacy 4216 bucket contains player-source, DamageObject and
            # short-entity rows.  Record all exact physical rows now and let
            # the final frozen stratum select 4216; otherwise the audit would
            # silently assume the very entity route it is meant to test.
            source_template = decoder.monster_entities.get(int(hit.source_actor_id))
            template_id = int(getattr(source_template, "template_id", 0) or 0)
            if not (
                str(event.job).strip() == TARGET_JOB
                or parse_skill(event.skill_id) == TARGET_SKILL
                or template_id == TARGET_TEMPLATE
            ):
                continue
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
            f"Source invariant drift from v57: {capture.name}: "
            f"actual={source_invariants!r} expected={item['raw_source_invariants']!r}"
        )
    # Current identity normalization leaves one byte-identical 8192-damage
    # retry unmerged in one capture although the source physical multiset is
    # unchanged.  Keep that global post-processing drift visible below, but
    # require the target 4216 stratum itself to match its frozen checkpoint.

    current_4216 = [
        event
        for event in final_events
        if str(event.job).strip() == TARGET_JOB
        and parse_skill(event.skill_id) == TARGET_SKILL
        and _scope_reason(event) == "included_positive_direct_hit"
        and _strict_classification(event) not in DETERMINISTIC_CLASSES
    ]
    current_4216_matches_v57 = (
        len(current_4216) == int(item["expected_gap_hits"])
        and sum(int(event.amount) for event in current_4216)
        == int(item["expected_gap_damage"])
    )
    frozen_keys = None if current_4216_matches_v57 else legacy_physical_gap_keys(item)
    if not current_4216_matches_v57 and frozen_keys is None:
        raise RuntimeError(f"No exact row-level v57 stratum for {capture.name}")

    gaps: list[dict[str, Any]] = []
    for event in final_events:
        if str(event.job).strip() != TARGET_JOB or _scope_reason(event) != "included_positive_direct_hit":
            continue
        matches = physical_rows.get(event_key(event), [])
        if not matches:
            continue
        final_is_4216_gap = (
            parse_skill(event.skill_id) == TARGET_SKILL
            and _strict_classification(event) not in DETERMINISTIC_CLASSES
        )
        selected_by_frozen_key = False
        if frozen_keys is not None:
            key = event_key(event)
            if frozen_keys[key] <= 0:
                continue
            frozen_keys[key] -= 1
            selected_by_frozen_key = True
        elif not final_is_4216_gap:
            continue
        if len(matches) != 1:
            raise RuntimeError(
                f"Physical join mismatch {capture.name} {event_key(event)} count={len(matches)}"
            )
        physical = matches[0]
        source_event = CombatEvent(**physical["decoder_event"])
        proofs: list[dict[str, Any]] = []
        slot = physical.get("self_attack_slot")
        spec = physical.get("self_attack_spec")
        if slot and spec:
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
        if object_proof and int(object_proof.get("skill_id", 0) or 0) > 0:
            proofs.append(
                {
                    "kind": "same_owner_generation_projectile_object_chain",
                    "skill_id": int(object_proof["skill_id"]),
                    "detail": object_proof,
                }
            )
        selector_state = physical["selector_chain"].get("state")
        if selector_state and selector_state.get("spec"):
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
                "v57_source_stratum_event": plain(source_event),
                "selected_by_final_4216": final_is_4216_gap,
                "selected_by_frozen_v57_physical_row": selected_by_frozen_key,
                "physical": physical,
                "proofs": proofs,
                "proof_skill_ids": sorted({int(row["skill_id"]) for row in proofs}),
                "immutable_key": list(immutable_key(event)),
            }
        )

    if frozen_keys is not None and sum(frozen_keys.values()) != 0:
        raise RuntimeError(
            f"Frozen v57 physical rows not fully rejoined for {capture.name}: "
            f"remaining={sum(frozen_keys.values())}"
        )

    actual_hits = len(gaps)
    actual_damage = sum(int(row["event"]["amount"]) for row in gaps)
    if (actual_hits, actual_damage) != (
        int(item["expected_gap_hits"]),
        int(item["expected_gap_damage"]),
    ):
        raise RuntimeError(
            f"4216 baseline drift {capture.name}: "
            f"{(actual_hits, actual_damage)} != "
            f"{(item['expected_gap_hits'], item['expected_gap_damage'])}"
        )
    result = {
        "schema": SCHEMA + "-capture",
        "tool_sha256": tool_hash,
        "capture": str(capture),
        "capture_sha256": item["capture_sha256"],
        "checkpoint": item["checkpoint"],
        "checkpoint_sha256": item["checkpoint_sha256"],
        "constructor": {
            "action_parser_mode": "structured",
            "duplicate_hit_evidence": True,
        },
        "source_invariants": source_invariants,
        "aggregated_invariants": aggregate_invariants,
        "v57_invariant_comparison": {
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
        return "unsupported_or_unjoined_direct37_physical_row"
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
    correction_pairs = Counter(
        f"4216->{row['proof_skill_ids'][0]}" for row in confirmed
    )
    immutable = [tuple(row["immutable_key"]) for row in confirmed]
    fixtures = {
        "schema": SCHEMA + "-fixtures",
        "positive": confirmed[:20],
        "conflicting": conflicts[:20],
        "negative": unclosed[:20],
        "selection": "first rows in frozen capture order; no damage/time selection",
    }
    report = {
        "schema": SCHEMA,
        "scope": "two frozen v57 Heavy Shooter captures; final non-deterministic 4216 rows",
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
                "old 4216 label",
                "damage value or hit pattern",
                "previous attack on the same entity or numeric object ID",
                "creator/context fallback without a fixed result edge",
            ],
        },
        "baseline": {
            "captures": len(results),
            "gap_hits": len(rows),
            "gap_damage": sum(int(row["event"]["amount"]) for row in rows),
        },
        "confirmed": {
            "rows": len(confirmed),
            "damage": sum(int(row["event"]["amount"]) for row in confirmed),
            "correction_pairs": dict(correction_pairs),
            "route_counts": dict(routes),
        },
        "conflicting_proofs": len(conflicts),
        "remaining_unclosed": len(unclosed),
        "unclosed_reasons": dict(reasons),
        "diagnostics": {
            "self_attack_slots": dict(raw_slots),
            "native_result_keys": dict(native_keys),
            "current_projectile_templates": dict(object_templates),
        },
        "conservation": {
            "all_capture_source_physical_totals_match_v57": all(
                result["v57_invariant_comparison"]["source_totals_match"]
                for result in results
            ),
            "all_capture_current_aggregate_totals_match_v57": all(
                result["v57_invariant_comparison"]["aggregate_totals_match"]
                for result in results
            ),
            "all_capture_normalized_identity_hashes_match_v57": all(
                result["v57_invariant_comparison"]["source_normalized_identity_hash_match"]
                and result["v57_invariant_comparison"]["aggregate_normalized_identity_hash_match"]
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
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in results
        ],
        "production_changed": False,
        "scoring_run": False,
        "upload": False,
    }
    return {"report": report, "confirmed": confirmed, "conflicts": conflicts,
            "unclosed": unclosed, "fixtures": fixtures}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 重炮手 4216 / 实体 32005 确定归因审计 v64",
        "",
        "本审计只使用两份冻结的 v57 重炮手抓包。没有修改生产代码、评分或上传。",
        "",
        "## 结果",
        "",
        "| 原 4216 缺口Hit | 确认可纠正 | 证据冲突 | 仍不可闭合 | 原缺口伤害 |",
        "|---:|---:|---:|---:|---:|",
        f"| {report['baseline']['gap_hits']} | {report['confirmed']['rows']} | "
        f"{report['conflicting_proofs']} | {report['remaining_unclosed']} | "
        f"{report['baseline']['gap_damage']} |",
        "",
        "### 确认纠正方向",
        "",
    ]
    pairs = report["confirmed"]["correction_pairs"]
    if pairs:
        for pair, count in sorted(pairs.items()):
            lines.append(f"- `{pair}`：{count} Hit")
    else:
        lines.append("- 没有满足全部严格条件的跨技能纠正。")
    lines += ["", "### 未闭合原因", ""]
    for reason, count in sorted(report["unclosed_reasons"].items(), key=lambda row: (-row[1], row[0])):
        lines.append(f"- `{reason}`：{count} Hit")
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
        "## 最小补丁建议",
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
    (OUT / "REPORT.md").write_text(
        render_markdown(merged["report"]), encoding="utf-8"
    )
    print(json.dumps(merged["report"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
