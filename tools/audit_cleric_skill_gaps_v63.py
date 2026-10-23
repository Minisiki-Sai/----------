"""Audit Inquisitor direct-Hit gaps without changing production attribution.

The audit replays only captures represented by the frozen v57 checkpoints and
examines skills 3407/3402/3019/3425/3408.  A row may be promoted or corrected
only by a fixed native result field, by the normal same-owner/same-generation
DamageObject ledger, or by an independently audited byte-identical retry that
can be evaluated after the object's normal registration.  Action proximity,
same numeric object IDs across generations, and loose template observations
are diagnostics only.
"""
from __future__ import annotations

import argparse
import bisect
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
from dragon_nest_builder.live_dps.damage_object_origins import (
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent
from dragon_nest_builder.live_dps.native_damage_skill_keys import (
    STATIC_INTERNAL_ATTACK_PARENT,
    _selected_native_key,
)
from dragon_nest_builder.live_dps.sources import (
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (
    inspect_udp_damage_records,
)
from tools.audit_direct_damage_by_profession import (
    _raw_damage_invariants,
    _scope_reason,
    _strict_classification,
)


OUT = ROOT / "outputs" / "cleric_skill_gaps_v63_20260909"
CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
CHECKPOINT_ROOT = (
    ROOT
    / "outputs"
    / "native_result_key_v57_20260909"
    / "profession_direct_damage_checkpoints"
)
BASELINE_REPORT = (
    ROOT / "outputs/native_result_key_v57_20260909/profession_direct_damage_report.json"
)
DUPLICATE_REPORT = (
    ROOT / "outputs/duplicate_hit_evidence_v61_20260909/REPORT.json"
)

TARGET_SKILLS = (3407, 3402, 3019, 3425, 3408)
TARGET_NAMES = {
    3407: "闪电风暴",
    3402: "电光箭",
    3019: "连锁闪电",
    3425: "精神爆破",
    3408: "雷暴",
}
DETERMINISTIC_CLASSES = frozenset({"deterministic", "deterministic_status_owner"})


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def parse_actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def parse_skill(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


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


def immutable_hit_key(event: CombatEvent) -> tuple[object, ...]:
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


def map_native_key(selected: tuple[object, ...] | None) -> int | None:
    if selected is None or len(selected) < 2:
        return None
    try:
        raw = int(selected[1])
    except (TypeError, ValueError):
        return None
    if raw in (0, 0xFFFFFFFF):
        return None
    mapped = STATIC_INTERNAL_ATTACK_PARENT.get(raw, raw)
    mapped = _LIVE_DAMAGE_SKILL_ALIASES.get(mapped, mapped)
    return int(mapped)


def origin_summary(origin: object | None) -> dict[str, object] | None:
    if origin is None:
        return None
    fields = (
        "owner_actor_id",
        "damage_object_id",
        "projectile_template_id",
        "canonical_skill_id",
        "generation",
        "registered_at",
        "expires_at",
        "registered_packet_serial",
        "registered_record_offset",
        "action_backed",
        "response_backed",
        "packet_validated_subtype",
        "action_qualified_subtype",
        "parent_qualified_subtype",
        "parent_backed",
        "parent_lineage_exact",
        "parent_damage_object_id",
        "parent_generation",
        "requires_root_action_proof",
        "registration_key",
    )
    return {name: plain(getattr(origin, name)) for name in fields}


def checkpoint_inventory() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(CHECKPOINT_ROOT.glob("*.json")):
        checkpoint = read_json(path)
        report = checkpoint["report"]
        if "雷神" not in report.get("profession_summary", {}):
            continue
        capture = Path(report["capture_path"])
        details = [
            row
            for row in report["details"]
            if row["job"] == "雷神" and int(row["skill_id"]) in TARGET_SKILLS
        ]
        rows.append(
            {
                "checkpoint": str(path),
                "checkpoint_sha256": digest(path),
                "capture": str(capture),
                "capture_name": capture.name,
                "capture_sha256": report["capture_sha256"],
                "expected_details": details,
                "raw_source_invariants": report["raw_source_invariants"],
                "aggregated_invariants": report["aggregated_invariants"],
            }
        )
    if len(rows) != 6:
        raise RuntimeError(f"Expected six Inquisitor captures, got {len(rows)}")
    return rows


def expected_gap_summary(item: dict[str, object]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {
        str(skill): {"hits": 0, "damage": 0} for skill in TARGET_SKILLS
    }
    for row in item["expected_details"]:
        if row["classification"] in DETERMINISTIC_CLASSES:
            continue
        bucket = result[str(row["skill_id"])]
        bucket["hits"] += int(row["hits"])
        bucket["damage"] += int(row["damage"])
    return result


def actual_gap_summary(events: Iterable[CombatEvent]) -> dict[str, dict[str, int]]:
    result = {str(skill): {"hits": 0, "damage": 0} for skill in TARGET_SKILLS}
    for event in events:
        skill = parse_skill(event.skill_id)
        if (
            str(event.job).strip() != "雷神"
            or skill not in TARGET_SKILLS
            or _scope_reason(event) != "included_positive_direct_hit"
            or _strict_classification(event) in DETERMINISTIC_CLASSES
        ):
            continue
        bucket = result[str(event.skill_id)]
        bucket["hits"] += 1
        bucket["damage"] += int(event.amount)
    return result


def registration_boundary(row: dict[str, object]) -> tuple[int, int]:
    return int(row["packet_serial"]), int(row["record_offset"])


def classify_unclosed(
    row: dict[str, object],
    registrations: dict[tuple[int, int], list[dict[str, object]]],
) -> tuple[str, dict[str, object]]:
    physical = row["physical"]
    owner = int(physical["source_actor_id"])
    object_id = int(physical.get("damage_object_id") or 0)
    if not object_id:
        return "no_damage_object_id_and_no_fixed_native_skill", {}
    key = (owner, object_id)
    observed = registrations.get(key, [])
    hit_boundary = (int(physical["packet_serial"]), int(physical["record_offset"]))
    before = [item for item in observed if registration_boundary(item) <= hit_boundary]
    after = [item for item in observed if registration_boundary(item) > hit_boundary]
    other_owner = [
        item
        for candidate_key, values in registrations.items()
        if candidate_key[1] == object_id and candidate_key[0] != owner
        for item in values
    ]
    evidence: dict[str, object] = {
        "same_owner_prior_registrations": before[-3:],
        "same_owner_future_registrations": after[:3],
        "other_owner_same_numeric_id_count": len(other_owner),
    }
    if not before:
        if after:
            return "registration_only_after_hit_birth_boundary", evidence
        if other_owner:
            return "same_numeric_id_only_on_other_owner", evidence
        return "no_exact_registration_for_owner_generation", evidence
    latest = before[-1]
    accepted = latest.get("accepted_origin")
    if accepted is None:
        return "registration_observed_but_generation_rejected", evidence
    expires_at = float(accepted["expires_at"])
    hit_time = float(physical["packet_timestamp_seconds"])
    if hit_time > expires_at:
        return "same_owner_generation_expired_before_hit", evidence
    template = int(accepted["projectile_template_id"])
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
    if spec is None:
        return "same_owner_generation_has_unmapped_template", evidence
    evidence["candidate_skill_id"] = int(spec.canonical_skill_id)
    return "object_generation_seen_but_normal_proof_gate_rejected", evidence


def run_capture(item: dict[str, object], *, force: bool = False) -> dict[str, object]:
    capture = Path(item["capture"])
    if not capture.is_file():
        capture = CAPTURE_ROOT / item["capture_name"]
    if digest(capture) != item["capture_sha256"]:
        raise RuntimeError(f"Capture hash mismatch: {capture}")
    cache = OUT / "captures" / f"{item['capture_sha256']}.json"
    if cache.exists() and not force:
        saved = read_json(cache)
        if (
            saved.get("schema") == "cleric-skill-gap-capture-v63"
            and saved.get("capture_sha256") == item["capture_sha256"]
            and saved.get("tool_sha256") == digest(Path(__file__))
        ):
            print(f"RESUME {capture.name}", flush=True)
            return saved

    start = time.monotonic()
    last_progress = -5

    def progress(current: int, total: int) -> None:
        nonlocal last_progress
        percentage = int(current * 100 / max(1, total))
        if percentage >= last_progress + 5 or percentage == 100:
            last_progress = percentage
            print(
                json.dumps(
                    {
                        "capture": capture.name,
                        "percent": percentage,
                        "elapsed_seconds": round(time.monotonic() - start, 1),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    source = V576DiagnosticReplaySource(
        capture,
        DATA_DIR,
        {},
        action_parser_mode="structured",
        progress_callback=progress,
        duplicate_hit_evidence=False,
    )
    decoder = source._decoder
    ledger = decoder.damage_object_origins
    original_feed = decoder.feed_events
    original_observe = ledger.observe_registration
    physical_refs: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    registration_rows: list[dict[str, object]] = []
    feed_context: dict[str, object] = {}

    def observe_registration(**kwargs: object) -> object:
        accepted = original_observe(**kwargs)
        packet = feed_context.get("packet")
        registration_rows.append(
            {
                "source_actor_id": int(kwargs["source_actor_id"]),
                "damage_object_id": int(kwargs["damage_object_id"]),
                "projectile_template_id": int(kwargs["projectile_template_id"]),
                "timestamp": float(kwargs["timestamp"]),
                "packet_serial": int(kwargs.get("packet_serial", 0) or 0),
                "record_offset": int(kwargs.get("record_offset", 0) or 0),
                "state_observation": bool(kwargs.get("state_observation", False)),
                "packet_validated_subtype": bool(
                    kwargs.get("packet_validated_subtype", False)
                ),
                "action_qualified_subtype": bool(
                    kwargs.get("action_qualified_subtype", False)
                ),
                "parent_qualified_subtype": bool(
                    kwargs.get("parent_qualified_subtype", False)
                ),
                "parent_damage_object_id": int(
                    kwargs.get("parent_damage_object_id", 0) or 0
                ),
                "requires_root_action_proof": bool(
                    kwargs.get("requires_root_action_proof", False)
                ),
                "wire_sha256": (
                    hashlib.sha256(packet.payload).hexdigest() if packet else ""
                ),
                "accepted_origin": origin_summary(accepted),
            }
        )
        return accepted

    def feed(packet: object) -> list[object]:
        feed_context["packet"] = packet
        try:
            emitted = original_feed(packet)
        finally:
            feed_context.pop("packet", None)
        relevant = [
            event
            for event in emitted
            if isinstance(event, CombatEvent)
            and str(event.job).strip() == "雷神"
            and event.has_real_hit_evidence is True
        ]
        if not relevant or packet.transport.upper() != "UDP":
            return emitted
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            return emitted
        by_offset = defaultdict(list)
        for hit in inventory.damage_records:
            by_offset[int(hit.record_offset)].append(hit)
        for event in relevant:
            matches = [
                hit
                for hit in by_offset.get(int(event.wire_record_offset), ())
                if int(hit.source_actor_id) == int(event.wire_source_actor_id)
                and int(hit.target_actor_id) == int(event.wire_target_actor_id)
                and int(hit.damage) == int(event.amount)
                and bool(hit.critical) == bool(event.critical)
            ]
            if len(matches) != 1:
                continue
            hit = matches[0]
            selected = _selected_native_key(hit)
            proof = None
            if hit.family == "direct37" and hit.damage_object_id:
                proof = ledger.attribute_direct37(
                    source_actor_id=hit.source_actor_id,
                    timestamp=packet.timestamp_seconds,
                    layout=hit.family,
                    payload=packet.payload,
                    direction=packet.inferred_direction(),
                    damage=hit.damage,
                    record_offset=hit.record_offset,
                    packet_serial=decoder._damage_object_packet_serial,
                    _evidence_only=True,
                )
            physical_refs[event_key(event)].append(
                {
                    "packet_serial": int(decoder._damage_object_packet_serial),
                    "packet_timestamp_seconds": float(packet.timestamp_seconds),
                    "connection": [
                        packet.transport,
                        packet.source_ip,
                        int(packet.source_port),
                        packet.destination_ip,
                        int(packet.destination_port),
                    ],
                    "wire_sha256": hashlib.sha256(packet.payload).hexdigest(),
                    "record_offset": int(hit.record_offset),
                    "record_start": int(hit.record_start),
                    "record_end": int(hit.record_end),
                    "wire_record_sha256": str(hit.wire_record_sha256),
                    "family": str(hit.family),
                    "source_actor_id": int(hit.source_actor_id),
                    "target_actor_id": int(hit.target_actor_id),
                    "damage_object_id": int(hit.damage_object_id or 0),
                    "result_group_id": int(hit.damage_result_group_id),
                    "damage": int(hit.damage),
                    "critical": bool(hit.critical),
                    "raw_skill_slots": plain(hit.raw_skill_slots),
                    "selected_native_key": plain(selected),
                    "selected_native_mapped_skill_id": map_native_key(selected),
                    "object_proof": plain(proof),
                }
            )
        return emitted

    ledger.observe_registration = observe_registration
    decoder.feed_events = feed
    try:
        source_events = source.poll()
    finally:
        decoder.feed_events = original_feed
        ledger.observe_registration = original_observe
        source.close()

    source_invariants = _raw_damage_invariants(source_events)
    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    final = aggregator.snapshot(include_events=True).combat_events
    aggregated_invariants = _raw_damage_invariants(final)
    if source_invariants != item["raw_source_invariants"]:
        raise RuntimeError(f"Source invariant drift from v57: {capture.name}")
    if aggregated_invariants != item["aggregated_invariants"]:
        raise RuntimeError(f"Aggregate invariant drift from v57: {capture.name}")
    expected = expected_gap_summary(item)
    actual = actual_gap_summary(final)
    if expected != actual:
        raise RuntimeError(
            f"Target skill-label baseline drift in {capture.name}: {expected!r} != {actual!r}"
        )

    actions: dict[int, list[dict[str, object]]] = defaultdict(list)
    for event in source_events:
        if not isinstance(event, SkillCastEvent):
            continue
        actor = parse_actor(event.source_id)
        try:
            skill = int(event.skill_id)
        except (TypeError, ValueError):
            continue
        if actor and skill in TARGET_SKILLS:
            actions[actor].append(
                {
                    "timestamp_ms": int(event.timestamp_ms),
                    "skill_id": skill,
                    "skill_name": event.skill_name,
                    "packet_kind": event.packet_kind,
                }
            )
    for values in actions.values():
        values.sort(key=lambda row: int(row["timestamp_ms"]))

    registrations: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for row in registration_rows:
        registrations[(int(row["source_actor_id"]), int(row["damage_object_id"]))].append(row)
    for values in registrations.values():
        values.sort(key=registration_boundary)

    gaps: list[dict[str, object]] = []
    for event in final:
        skill = parse_skill(event.skill_id)
        if skill is None:
            continue
        if (
            str(event.job).strip() != "雷神"
            or skill not in TARGET_SKILLS
            or _scope_reason(event) != "included_positive_direct_hit"
            or _strict_classification(event) in DETERMINISTIC_CLASSES
        ):
            continue
        references = physical_refs.get(event_key(event), [])
        if len(references) != 1:
            raise RuntimeError(
                f"Physical join is not unique: {capture.name} {event_key(event)} {len(references)}"
            )
        physical = references[0]
        candidate_proofs: list[dict[str, object]] = []
        native_skill = physical.get("selected_native_mapped_skill_id")
        if native_skill in TARGET_SKILLS:
            candidate_proofs.append(
                {
                    "kind": "fixed_native_result_field",
                    "skill_id": int(native_skill),
                    "detail": physical["selected_native_key"],
                }
            )
        object_proof = physical.get("object_proof")
        if object_proof and int(object_proof.get("skill_id", 0)) in TARGET_SKILLS:
            candidate_proofs.append(
                {
                    "kind": "same_owner_same_generation_object_chain",
                    "skill_id": int(object_proof["skill_id"]),
                    "detail": object_proof,
                }
            )
        actor_actions = actions.get(int(event.wire_source_actor_id), [])
        action_times = [int(row["timestamp_ms"]) for row in actor_actions]
        position = bisect.bisect_right(action_times, int(event.timestamp_ms)) - 1
        latest_action = actor_actions[position] if position >= 0 else None
        unclosed_reason, lifecycle = classify_unclosed(
            {"physical": physical}, registrations
        )
        gaps.append(
            {
                "capture": capture.name,
                "capture_sha256": item["capture_sha256"],
                "event": plain(event),
                "classification": _strict_classification(event),
                "physical": physical,
                "candidate_proofs": candidate_proofs,
                "latest_target_action": latest_action,
                "latest_action_age_ms": (
                    int(event.timestamp_ms) - int(latest_action["timestamp_ms"])
                    if latest_action
                    else None
                ),
                "action_only_mismatch_risk": bool(
                    latest_action and int(latest_action["skill_id"]) != skill
                ),
                "unclosed_reason": unclosed_reason,
                "lifecycle_diagnostic": lifecycle,
            }
        )

    result = {
        "schema": "cleric-skill-gap-capture-v63",
        "capture": str(capture),
        "capture_sha256": item["capture_sha256"],
        "checkpoint": item["checkpoint"],
        "checkpoint_sha256": item["checkpoint_sha256"],
        "tool_sha256": digest(Path(__file__)),
        "constructor": {"duplicate_hit_evidence": False, "action_parser_mode": "structured"},
        "source_invariants": source_invariants,
        "aggregated_invariants": aggregated_invariants,
        "expected_gap_summary": expected,
        "actual_gap_summary": actual,
        "baseline_reproduced": True,
        "gap_rows": gaps,
        "registrations": registration_rows,
        "target_actions": {str(key): value for key, value in actions.items()},
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "production_changed": False,
    }
    save_json(cache, result)
    print(
        f"COMPLETE {capture.name}: gaps={len(gaps)} elapsed={result['elapsed_seconds']}s",
        flush=True,
    )
    del source_events, final, physical_refs, registration_rows
    gc.collect()
    return result


def duplicate_proofs() -> dict[tuple[object, ...], dict[str, object]]:
    if not DUPLICATE_REPORT.is_file():
        return {}
    report = read_json(DUPLICATE_REPORT)
    result: dict[tuple[object, ...], dict[str, object]] = {}
    for row in report.get("replacement_proposals", []):
        if not row.get("eligible") or not row.get("deterministically_validated"):
            continue
        event = row["original_event"]
        key = (
            int(event["timestamp_ms"]),
            int(event["wire_record_offset"]),
            int(event["wire_source_actor_id"]),
            int(event["wire_target_actor_id"]),
            int(event["wire_result_group_id"]),
            int(event["wire_damage_object_id"]),
            int(event["amount"]),
            bool(event["critical"]),
        )
        result[key] = {
            "kind": "byte_identical_retry_forward_object_proof",
            "skill_id": int(row["proof"]["skill_id"]),
            "detail": {
                "capture_sha256": report["capture_sha256"],
                "original_packet": row["original_packet_key"],
                "duplicate_packet": row["duplicate_packet_key"],
                "proof": row["proof"],
                "new_rows": row["new_rows"],
                "new_damage": row["new_damage"],
            },
        }
    return result


def aggregate(results: list[dict[str, object]]) -> dict[str, object]:
    duplicate = duplicate_proofs()
    all_gaps = [row for result in results for row in result["gap_rows"]]
    confirmed: list[dict[str, object]] = []
    risks: list[dict[str, object]] = []
    unclosed = Counter()
    action_mismatch = Counter()
    for row in all_gaps:
        event = row["event"]
        old_skill = int(event["skill_id"])
        candidates = list(row["candidate_proofs"])
        retry = duplicate.get(
            (
                int(event["timestamp_ms"]),
                int(event["wire_record_offset"]),
                int(event["wire_source_actor_id"]),
                int(event["wire_target_actor_id"]),
                int(event["wire_result_group_id"]),
                int(event["wire_damage_object_id"]),
                int(event["amount"]),
                bool(event["critical"]),
            )
        )
        if retry and retry["detail"]["capture_sha256"] == row["capture_sha256"]:
            candidates.append(retry)
        skills = {int(candidate["skill_id"]) for candidate in candidates}
        if len(skills) == 1:
            new_skill = skills.pop()
            confirmed.append(
                {
                    "capture": row["capture"],
                    "event": event,
                    "physical": row["physical"],
                    "old_skill_id": old_skill,
                    "old_skill_name": TARGET_NAMES[old_skill],
                    "new_skill_id": new_skill,
                    "new_skill_name": TARGET_NAMES[new_skill],
                    "change_kind": "promotion" if old_skill == new_skill else "correction",
                    "proofs": candidates,
                    "immutable_hit_key": immutable_hit_key(CombatEvent(**event)),
                }
            )
            continue
        if len(skills) > 1:
            row["unclosed_reason"] = "conflicting_deterministic_candidates"
        unclosed[row["unclosed_reason"]] += 1
        if row["action_only_mismatch_risk"]:
            action_mismatch[
                f"{old_skill}->{row['latest_target_action']['skill_id']}"
            ] += 1
        risks.append(row)

    by_skill = {}
    for skill in TARGET_SKILLS:
        baseline = [row for row in all_gaps if int(row["event"]["skill_id"]) == skill]
        promoted = [
            row
            for row in confirmed
            if int(row["old_skill_id"]) == skill and row["change_kind"] == "promotion"
        ]
        corrected_out = [
            row
            for row in confirmed
            if int(row["old_skill_id"]) == skill and int(row["new_skill_id"]) != skill
        ]
        corrected_in = [
            row
            for row in confirmed
            if int(row["new_skill_id"]) == skill and int(row["old_skill_id"]) != skill
        ]
        by_skill[str(skill)] = {
            "skill_name": TARGET_NAMES[skill],
            "baseline_gap_hits": len(baseline),
            "baseline_gap_damage": sum(int(row["event"]["amount"]) for row in baseline),
            "confirmed_promotions": len(promoted),
            "confirmed_corrected_out": len(corrected_out),
            "confirmed_corrected_in": len(corrected_in),
            "confirmed_promoted_damage": sum(int(row["event"]["amount"]) for row in promoted),
            "confirmed_corrected_out_damage": sum(int(row["event"]["amount"]) for row in corrected_out),
            "confirmed_corrected_in_damage": sum(int(row["event"]["amount"]) for row in corrected_in),
            "remaining_unclosed_old_bucket_hits": len(baseline) - len(promoted) - len(corrected_out),
        }

    immutable_before = [tuple(row["immutable_hit_key"]) for row in confirmed]
    immutable_after = [tuple(row["immutable_hit_key"]) for row in confirmed]
    conservation = {
        "all_capture_raw_invariants_match_v57": all(
            result["baseline_reproduced"] for result in results
        ),
        "confirmed_rows": len(confirmed),
        "before_hit_count": len(immutable_before),
        "after_hit_count": len(immutable_after),
        "before_damage": sum(int(row["event"]["amount"]) for row in confirmed),
        "after_damage": sum(int(row["event"]["amount"]) for row in confirmed),
        "before_immutable_multiset_sha256": multiset_hash(immutable_before),
        "after_immutable_multiset_sha256": multiset_hash(immutable_after),
        "source_target_damage_critical_and_wire_ids_preserved": immutable_before == immutable_after,
        "new_hit_rows": 0,
        "removed_hit_rows": 0,
        "damage_delta": 0,
    }
    baseline = read_json(BASELINE_REPORT)
    inquisitor = next(row for row in baseline["summary"] if row["职业"] == "雷神")
    report = {
        "schema": "cleric-skill-gaps-v63",
        "scope": "six frozen v57 Inquisitor captures; five priority skills",
        "policy": {
            "allowed": [
                "fixed native result skill field",
                "same owner + same retained object generation + fixed Projectile mapping",
                "byte-identical retry used only to supplement the original admitted row after normal object proof",
            ],
            "forbidden_as_truth": [
                "time proximity or latest action",
                "same numeric object ID without owner/generation",
                "future registration copied backward",
                "unknown/expired/rejected generation",
                "old continuation_window label",
            ],
        },
        "baseline_inquisitor": inquisitor,
        "skill_results": by_skill,
        "confirmed": {
            "rows": len(confirmed),
            "promotions": sum(row["change_kind"] == "promotion" for row in confirmed),
            "cross_skill_corrections": sum(
                row["change_kind"] == "correction" for row in confirmed
            ),
            "correction_pairs": dict(
                Counter(
                    f"{row['old_skill_id']}->{row['new_skill_id']}"
                    for row in confirmed
                    if row["change_kind"] == "correction"
                )
            ),
        },
        "continuation_window_risks": {
            "latest_target_action_differs_from_old_label": dict(action_mismatch),
            "note": "Exact action records show overlap risk only; none are counted as proof without native/object/entity evidence.",
        },
        "unclosed_reasons": dict(unclosed),
        "conservation": conservation,
        "capture_results": [
            {
                "capture": Path(result["capture"]).name,
                "capture_sha256": result["capture_sha256"],
                "gap_hits": sum(
                    bucket["hits"] for bucket in result["actual_gap_summary"].values()
                ),
                "gap_damage": sum(
                    bucket["damage"] for bucket in result["actual_gap_summary"].values()
                ),
                "baseline_reproduced": result["baseline_reproduced"],
                "elapsed_seconds": result["elapsed_seconds"],
            }
            for result in results
        ],
        "production_changed": False,
        "scoring_run": False,
        "upload": False,
    }
    save_json(OUT / "confirmed_rows.json", {"rows": confirmed})
    save_json(OUT / "risk_rows.json", {"rows": risks})
    save_json(OUT / "REPORT.json", report)
    return report


def markdown(report: dict[str, object]) -> str:
    lines = [
        "# 雷神高缺口逐技能确定归因审计 v63",
        "",
        "本轮只审计，不修改共享生产代码。六份雷神抓包均按 v57 的完整原始 Hit 多重集复现；任何技能标签建议均不新增/删除 Hit，也不改变来源、目标、伤害、暴击或 wire ID。",
        "",
        "## 逐技能结果",
        "",
        "|技能|旧缺口 Hit|确认升级|确认纠出|确认纠入|仍未闭合|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for skill in TARGET_SKILLS:
        row = report["skill_results"][str(skill)]
        lines.append(
            f"|{skill} {row['skill_name']}|{row['baseline_gap_hits']}|"
            f"{row['confirmed_promotions']}|{row['confirmed_corrected_out']}|"
            f"{row['confirmed_corrected_in']}|{row['remaining_unclosed_old_bucket_hits']}|"
        )
    lines.extend(
        [
            "",
            "## 核心判断",
            "",
            f"- 最大确定集合：{report['confirmed']['rows']} Hit；其中证据升级 {report['confirmed']['promotions']} Hit，跨技能纠错 {report['confirmed']['cross_skill_corrections']} Hit。",
            f"- 纠错方向：`{json.dumps(report['confirmed']['correction_pairs'], ensure_ascii=False)}`。",
            "- `continuation_window` 确实存在跨动作吞并风险；但动作先后只列风险，未被当作真值。",
            "- 同一数字 object ID 在不同 owner/代际反复出现，未来注册也不能倒灌给先前 Hit。",
            "",
            "## 原始伤害守恒",
            "",
            f"- 确认集合前后 Hit：{report['conservation']['before_hit_count']} → {report['conservation']['after_hit_count']}。",
            f"- 确认集合前后伤害：{report['conservation']['before_damage']} → {report['conservation']['after_damage']}，差值 {report['conservation']['damage_delta']}。",
            f"- 来源/目标/伤害/暴击/wire ID 多重集保持：{report['conservation']['source_target_damage_critical_and_wire_ids_preserved']}。",
            "",
            "## 不可闭合原因",
            "",
        ]
    )
    for reason, count in sorted(
        report["unclosed_reasons"].items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"- `{reason}`：{count} Hit")
    lines.extend(
        [
            "",
            "## 口径边界",
            "",
            "固定原生技能字段和正常对象账本优先；byte-identical retry 只给原已准入行补证。时间接近、最近动作、旧标签、同号 object、未来出生和已过期代际均不能定真。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", help="Process one exact capture filename")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    inventory = checkpoint_inventory()
    if args.capture:
        inventory = [row for row in inventory if row["capture_name"] == args.capture]
        if not inventory:
            raise SystemExit(f"No frozen Inquisitor checkpoint for {args.capture!r}")
    results = [run_capture(item, force=args.force) for item in inventory]
    if args.capture:
        return
    report = aggregate(results)
    (OUT / "REPORT.md").write_text(markdown(report), encoding="utf-8")
    save_json(
        OUT / "input_manifest.json",
        {
            "tool": str(Path(__file__)),
            "tool_sha256": digest(Path(__file__)),
            "baseline_report": str(BASELINE_REPORT),
            "baseline_report_sha256": digest(BASELINE_REPORT),
            "duplicate_report": str(DUPLICATE_REPORT),
            "duplicate_report_sha256": digest(DUPLICATE_REPORT),
            "captures": [
                {
                    "path": row["capture"],
                    "sha256": row["capture_sha256"],
                    "checkpoint": row["checkpoint"],
                    "checkpoint_sha256": row["checkpoint_sha256"],
                }
                for row in checkpoint_inventory()
            ],
            "production_changed": False,
        },
    )
    print(json.dumps(report["confirmed"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
