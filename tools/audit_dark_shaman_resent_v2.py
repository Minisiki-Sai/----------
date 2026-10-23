"""Audit Black Shaman 5207 generations against final V2 combat events.

The report records status and object generations as facts.  Timing fields are
diagnostic only; this tool never changes a skill identity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from tools.audit_direct_damage_by_profession import _scope_reason  # noqa: E402


FOCUS_JOB = "黑暗萨满"
FOCUS_SKILL_ID = 5207
PHASE_START = 0.25
PHASE_END = 0.80


def _actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _counter_rows(counter: Counter[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [
        {"key": list(key), "hits": int(value)}
        for key, value in counter.most_common()
    ]


def _player_root_generation(
    decoder: object,
    owner_actor_id: int,
    damage_object_id: int,
    event_timestamp_ms: int,
    origin: float,
) -> dict[str, int] | None:
    """Return the retained A90370 root facts for one object generation.

    These fields are diagnostic only.  A generic Kali root object (for
    example config rows 73000017/73000018) is deliberately not treated as a
    skill identity, and this lookup never changes the V2 decision.
    """
    ledger = getattr(decoder, "player_root_action_objects", None)
    generations = getattr(ledger, "_generations", {})
    rows = generations.get((int(owner_actor_id), int(damage_object_id)), ())
    if not rows:
        return None
    timestamp = float(origin) + int(event_timestamp_ms) / 1000.0
    candidates = [row for row in rows if float(row.timestamp) <= timestamp]
    if not candidates:
        return None
    row = max(candidates, key=lambda value: (float(value.timestamp), int(value.generation)))
    return {
        "generation": int(row.generation),
        "config_row": int(row.config_row),
        "action_index": int(row.action_index),
        "signal_key": int(row.signal_key),
    }


def analyze(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    field_generations: dict[str, object] = {}
    registrations: list[dict[str, Any]] = []

    original_status = decoder.field_effect_instances.observe_status

    def observe_status(**kwargs):
        generation = original_status(**kwargs)
        if generation is not None:
            field_generations[generation.evidence_id] = generation
        return generation

    decoder.field_effect_instances.observe_status = observe_status
    original_registration = decoder.damage_object_origins.observe_registration

    def observe_registration(**kwargs):
        generation = original_registration(**kwargs)
        if generation is not None:
            registrations.append(
                {
                    "actor": int(kwargs["source_actor_id"]),
                    "object": int(kwargs["damage_object_id"]),
                    "template": int(generation.projectile_template_id),
                    "generation": int(generation.generation),
                    "registered_at": float(generation.registered_at),
                    "expires_at": float(generation.expires_at),
                }
            )
        return generation

    decoder.damage_object_origins.observe_registration = observe_registration
    try:
        source_events = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    final_events = aggregator.snapshot(include_events=True).combat_events
    origin = float(decoder.first_timestamp or 0.0)
    puppet_actor_ids = {
        int(actor_id)
        for actor_id, template in decoder.monster_entities.items()
        if int(getattr(template, "template_id", 0) or 0) in {33500, 33501}
    }

    generations = []
    for generation in sorted(
        field_generations.values(), key=lambda value: value.applied_at
    ):
        generations.append(
            {
                **asdict(generation),
                "evidence_id": generation.evidence_id,
                "applied_ms": round((generation.applied_at - origin) * 1000),
                "expires_ms": round((generation.expires_at - origin) * 1000),
            }
        )

    registrations_by_key: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for registration in registrations:
        key = (registration["actor"], registration["object"])
        registrations_by_key.setdefault(key, []).append(registration)
    for values in registrations_by_key.values():
        values.sort(key=lambda value: value["registered_at"])

    scoped = [
        event
        for event in final_events
        if isinstance(event, CombatEvent)
        and event.job == FOCUS_JOB
        and _scope_reason(event) == "included_positive_direct_hit"
    ]
    rows: list[dict[str, Any]] = []
    for event in scoped:
        actor_id = _actor(event.source_id)
        active = [
            generation
            for generation in generations
            if generation["source_actor_id"] == actor_id
            and generation["applied_ms"] <= event.timestamp_ms
            <= generation["expires_ms"] + 500
        ]
        current = active[-1] if active else None
        elapsed_ms = (
            int(event.timestamp_ms) - int(current["applied_ms"])
            if current is not None
            else None
        )
        phase = (
            (elapsed_ms % 1000) / 1000.0
            if elapsed_ms is not None and elapsed_ms >= 0
            else None
        )
        in_phase = bool(
            phase is not None and PHASE_START <= phase <= PHASE_END
        )
        object_generation = None
        object_actor = int(event.wire_source_actor_id or actor_id)
        for candidate in registrations_by_key.get(
            (object_actor, int(event.wire_damage_object_id or 0)), ()
        ):
            timestamp = origin + event.timestamp_ms / 1000.0
            if candidate["registered_at"] <= timestamp <= candidate["expires_at"]:
                object_generation = candidate
        player_root = _player_root_generation(
            decoder,
            object_actor,
            int(event.wire_damage_object_id or 0),
            int(event.timestamp_ms),
            origin,
        )
        wire_target = int(event.wire_target_actor_id or 0)
        wire_alternate = int(
            getattr(event, "wire_alternate_target_actor_id", 0) or 0
        )
        rows.append(
            {
                "timestamp_ms": int(event.timestamp_ms),
                "source_id": event.source_id,
                "target_id": event.target_id,
                "skill_id": str(event.skill_id),
                "skill_name": event.skill_name,
                "method": event.attribution_method,
                "raw_skill_id": str(event.raw_skill_id),
                "raw_skill_name": event.raw_skill_name,
                "raw_method": event.raw_attribution_method,
                "amount": int(event.amount),
                "critical": bool(event.critical),
                "result_group": int(event.wire_result_group_id or 0),
                "damage_object_id": int(event.wire_damage_object_id or 0),
                "wire_source_actor_id": int(event.wire_source_actor_id or 0),
                "wire_target_actor_id": int(event.wire_target_actor_id or 0),
                "wire_alternate_target_actor_id": int(
                    getattr(event, "wire_alternate_target_actor_id", 0) or 0
                ),
                # A puppet can be a game-side redirect target without
                # producing a standalone damage row.  Preserve only the
                # literal wire actor evidence here; absence is not a negative
                # conclusion about the redirect mechanic.
                "wire_target_is_puppet": wire_target in puppet_actor_ids,
                "wire_alternate_target_is_puppet": (
                    wire_alternate in puppet_actor_ids
                ),
                "wire_record_offset": int(event.wire_record_offset or 0),
                "wire_layout": event.wire_damage_layout,
                "cast_instance_id": event.cast_instance_id,
                "field_instance_id": (
                    int(current["effect_instance_id"])
                    if current is not None else 0
                ),
                "field_evidence_id": (
                    str(current["evidence_id"]) if current is not None else ""
                ),
                "field_elapsed_ms": elapsed_ms,
                "field_phase": phase,
                "field_phase_match": in_phase,
                "object_template": (
                    int(object_generation["template"])
                    if object_generation is not None else 0
                ),
                "object_generation": (
                    int(object_generation["generation"])
                    if object_generation is not None else 0
                ),
                "player_root_generation": int(player_root["generation"])
                if player_root is not None else 0,
                "player_root_config_row": int(player_root["config_row"])
                if player_root is not None else 0,
                "player_root_action_index": int(player_root["action_index"])
                if player_root is not None else -1,
                "player_root_signal_key": int(player_root["signal_key"])
                if player_root is not None else -1,
            }
        )

    unknown = [row for row in rows if row["skill_id"] == "-1"]
    known_5207 = [row for row in rows if row["skill_id"] == str(FOCUS_SKILL_ID)]
    casts = [
        event
        for event in source_events
        if isinstance(event, SkillCastEvent)
        and event.job == FOCUS_JOB
        and str(event.skill_id) == str(FOCUS_SKILL_ID)
    ]
    unknown_labels: Counter[tuple[Any, ...]] = Counter(
        (
            row["raw_skill_id"],
            row["raw_skill_name"],
            row["raw_method"],
            row["field_phase_match"],
            row["object_template"],
        )
        for row in unknown
    )
    return {
        "schema": "dark-shaman-resent-v2-audit-v1",
        "policy": "facts plus timing diagnostics; no relabeling",
        "capture": str(path),
        "source_event_count": len(source_events),
        "final_event_count": len(final_events),
        "focus_cast_count": len(casts),
        "field_generation_count": len(generations),
        "scoped_hits": len(rows),
        "scoped_damage": sum(row["amount"] for row in rows),
        "known_5207_hits": len(known_5207),
        "known_5207_damage": sum(row["amount"] for row in known_5207),
        "unknown_hits": len(unknown),
        "unknown_damage": sum(row["amount"] for row in unknown),
        "unknown_with_active_field": sum(
            bool(row["field_instance_id"]) for row in unknown
        ),
        "unknown_with_field_phase_match": sum(
            bool(row["field_phase_match"]) for row in unknown
        ),
        "unknown_with_wire_alternate_target": sum(
            bool(row["wire_alternate_target_actor_id"]) for row in unknown
        ),
        "puppet_actor_ids": sorted(puppet_actor_ids),
        "unknown_with_wire_puppet_target": sum(
            bool(row["wire_target_is_puppet"])
            for row in unknown
        ),
        "unknown_with_wire_alternate_puppet_target": sum(
            bool(row["wire_alternate_target_is_puppet"])
            for row in unknown
        ),
        "unknown_with_player_root": sum(
            bool(row["player_root_generation"])
            for row in unknown
        ),
        "unknown_player_root_groups": _counter_rows(
            Counter(
                (
                    row["player_root_config_row"],
                    row["player_root_action_index"],
                    row["player_root_signal_key"],
                )
                for row in unknown
                if row["player_root_generation"]
            )
        ),
        "unknown_alternate_target_pairs": _counter_rows(
            Counter(
                (
                    row["wire_alternate_target_actor_id"],
                    row["wire_target_actor_id"],
                )
                for row in unknown
                if row["wire_alternate_target_actor_id"]
            )
        ),
        "unknown_label_groups": _counter_rows(unknown_labels),
        "field_generations": generations,
        "events": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = analyze(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(
        "hits", report["scoped_hits"],
        "5207", report["known_5207_hits"],
        "unknown", report["unknown_hits"],
        "unknown_phase", report["unknown_with_field_phase_match"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
