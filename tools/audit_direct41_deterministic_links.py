from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_status_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.v576.core_decoder import (  # noqa: E402
    _canonical_live_skill_id,
)
from tools.audit_direct_damage_release_gate import _classification  # noqa: E402


SCHEMA_VERSION = 1
DIRECT41_MARKER = b"\x08\x77\x00"
DIRECT41_DAMAGE_RELATIVE_OFFSET = 23
EXISTING_PACKET_SKILL_OFFSET = 106
EXISTING_PACKET_SKILL_END = 110
SEARCH_RELATIVE_MIN = -16
SEARCH_RELATIVE_MAX = 117
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")
INVALID_KEYS = frozenset({0, 1, 0xFFFFFFFF})
ACCEPT_MIN_VALIDATION_ROWS = 50
ACCEPT_MIN_VALIDATION_CAPTURES = 3
ACCEPT_MIN_DISTINCT_VALUES = 8


def _overlaps_existing_truth_slot(candidate_start: int, width: int = 4) -> bool:
    candidate_end = candidate_start + width
    return (
        candidate_start < EXISTING_PACKET_SKILL_END
        and candidate_end > EXISTING_PACKET_SKILL_OFFSET
    )


@dataclass(slots=True)
class Direct41Row:
    packet_ordinal: int
    timestamp: float
    source_actor_id: int
    target_actor_id: int
    damage: int
    layout: str
    signature: str
    payload_length: int
    xor_phase: int
    marker_offset: int
    body: bytes
    exact: bool
    truth_skill_id: int
    raw_packet_skill_id: int
    assigned_skill_id: int
    assigned_method: str
    blocking: bool
    same_packet_status_skills: tuple[int, ...] = ()
    same_packet_status_instance_ids: tuple[int, ...] = ()

    @property
    def variant(self) -> str:
        return "player" if "direct41-player" in self.layout else "ordinary"


@dataclass(slots=True)
class RunState:
    value: int
    exact_skills: Counter[int] = field(default_factory=Counter)
    exact_rows: int = 0
    nonexact_rows: int = 0
    blocking_rows: int = 0
    old_assignment_matches: int = 0
    first_packet: int = 0
    last_packet: int = 0


def _counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in sorted(
            counter.items(), key=lambda item: (-item[1], str(item[0]))
        )
    }


def _safe_int(value: object) -> int:
    try:
        return int(str(value or "").strip(), 0)
    except (TypeError, ValueError):
        return 0


def _canonical_skill(value: object) -> int:
    skill_id = _safe_int(value)
    if skill_id <= 0:
        return 0
    return int(_canonical_live_skill_id(skill_id))


def _analysis_signature() -> dict[str, Any]:
    relatives = (
        "dragon_nest_builder/live_dps/protocol_v576.py",
        "dragon_nest_builder/live_dps/v576/core_decoder.py",
        "dragon_nest_builder/live_dps/damage_object_origins.py",
        "dragon_nest_builder/data/live_dps_profiles/v576.json",
        "tools/audit_direct41_deterministic_links.py",
    )
    files: dict[str, str] = {}
    combined = hashlib.sha256()
    for relative in relatives:
        path = ROOT / relative
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[relative] = digest
        combined.update(relative.encode("utf-8"))
        combined.update(digest.encode("ascii"))
    return {"sha256": combined.hexdigest(), "files": files}


def _direct_body(
    packet: object, decoded: object
) -> tuple[int, bytes] | None:
    payload = bytes(getattr(packet, "payload", b""))
    marker_offset = int(getattr(decoded, "record_offset", -1) or 0)
    source_actor_id = int(getattr(decoded, "source_actor_id", 0) or 0)
    damage = int(getattr(decoded, "damage", 0) or 0)
    for xor_phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, xor_phase)
        if (
            marker_offset >= 0
            and marker_offset + DIRECT41_DAMAGE_RELATIVE_OFFSET + 8
            <= len(body)
            and body[marker_offset : marker_offset + 3] == DIRECT41_MARKER
            and U32.unpack_from(body, marker_offset + 8)[0] == source_actor_id
            and U64.unpack_from(
                body, marker_offset + DIRECT41_DAMAGE_RELATIVE_OFFSET
            )[0]
            == damage
        ):
            return int(xor_phase), body
    return None


def _read_u32(row: Direct41Row, relative_offset: int) -> int | None:
    absolute = row.marker_offset + relative_offset
    if absolute < 0 or absolute + 4 > len(row.body):
        return None
    return U32.unpack_from(row.body, absolute)[0]


def _add_example(
    examples: list[dict[str, Any]], row: Direct41Row, **extra: Any
) -> None:
    if len(examples) >= 8:
        return
    examples.append(
        {
            "packet": row.packet_ordinal,
            "timestamp": round(row.timestamp, 6),
            "layout": row.layout,
            "source_actor_id": f"0x{row.source_actor_id:08X}",
            "target_actor_id": f"0x{row.target_actor_id:08X}",
            "damage": row.damage,
            "truth_skill_id": row.truth_skill_id,
            "assigned_skill_id": row.assigned_skill_id,
            "assigned_method": row.assigned_method,
            **extra,
        }
    )


def _finalize_run(
    stats: dict[str, Any],
    state: RunState,
    *,
    capture: str,
    relative_offset: int,
    scope: str,
) -> None:
    if state.exact_rows >= 2:
        stats["validation_runs"] += 1
        stats["validation_rows"] += state.exact_rows
        if len(state.exact_skills) == 1:
            stats["consistent_validation_rows"] += state.exact_rows
        else:
            stats["conflicting_runs"] += 1
            stats["conflict_rows"] += state.exact_rows
            if len(stats["conflict_examples"]) < 8:
                stats["conflict_examples"].append(
                    {
                        "capture": capture,
                        "scope": scope,
                        "relative_offset": relative_offset,
                        "value": state.value,
                        "skills": _counter_dict(state.exact_skills),
                        "first_packet": state.first_packet,
                        "last_packet": state.last_packet,
                    }
                )
    if state.nonexact_rows <= 0:
        return
    if len(state.exact_skills) == 1:
        recovered_skill = next(iter(state.exact_skills))
        stats["recoverable_nonexact"] += state.nonexact_rows
        stats["recoverable_blocking"] += state.blocking_rows
        stats["old_assignment_matches"] += state.old_assignment_matches
        stats["recovered_skills"][recovered_skill] += state.nonexact_rows
    elif len(state.exact_skills) > 1:
        stats["ambiguous_nonexact"] += state.nonexact_rows
    else:
        stats["orphan_nonexact"] += state.nonexact_rows


def _new_run_stats() -> dict[str, Any]:
    return {
        "validation_runs": 0,
        "validation_rows": 0,
        "consistent_validation_rows": 0,
        "conflicting_runs": 0,
        "conflict_rows": 0,
        "recoverable_nonexact": 0,
        "recoverable_blocking": 0,
        "ambiguous_nonexact": 0,
        "orphan_nonexact": 0,
        "old_assignment_matches": 0,
        "recovered_skills": Counter(),
        "distinct_values": set(),
        "conflict_examples": [],
    }


def _domain_maps(
    registrations: list[tuple[int, int, int]],
    status_rows: list[tuple[int, int, int, int, int]],
    direct37_rows: list[tuple[int, int, int, int]],
) -> dict[str, dict[tuple[int, int], set[int]]]:
    result: dict[str, dict[tuple[int, int], set[int]]] = {
        "registered_damage_object_id": defaultdict(set),
        "status_effect_instance_id": defaultdict(set),
        "status_skill_level_row_id": defaultdict(set),
        "status_effect_class_id": defaultdict(set),
        "direct37_result_group_id": defaultdict(set),
        "direct37_damage_object_id": defaultdict(set),
    }
    for actor_id, object_id, skill_id in registrations:
        if actor_id > 0 and object_id > 0 and skill_id > 0:
            result["registered_damage_object_id"][(actor_id, object_id)].add(
                skill_id
            )
    for actor_id, skill_id, instance_id, level_id, class_id in status_rows:
        for name, value in (
            ("status_effect_instance_id", instance_id),
            ("status_skill_level_row_id", level_id),
            ("status_effect_class_id", class_id),
        ):
            if actor_id > 0 and value > 0 and skill_id > 0:
                result[name][(actor_id, value)].add(skill_id)
    for actor_id, skill_id, result_group_id, object_id in direct37_rows:
        for name, value in (
            ("direct37_result_group_id", result_group_id),
            ("direct37_damage_object_id", object_id),
        ):
            if actor_id > 0 and value > 0 and skill_id > 0:
                result[name][(actor_id, value)].add(skill_id)
    return result


def _audit_capture(path_text: str, source_name: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed
    rows: list[Direct41Row] = []
    registrations: list[tuple[int, int, int]] = []
    status_rows: list[tuple[int, int, int, int, int]] = []
    direct37_rows: list[tuple[int, int, int, int]] = []
    packet_ordinal = 0
    body_failures = 0

    origin_ledger = decoder.damage_object_origins
    original_registration = origin_ledger.observe_registration
    original_response = origin_ledger.observe_response

    def observe_registration(**kwargs: Any):
        template_id = int(kwargs.get("projectile_template_id", 0) or 0)
        spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template_id)
        registrations.append(
            (
                int(kwargs.get("source_actor_id", 0) or 0),
                int(kwargs.get("damage_object_id", 0) or 0),
                int(spec.canonical_skill_id) if spec is not None else 0,
            )
        )
        return original_registration(**kwargs)

    def observe_response(**kwargs: Any):
        status_rows.append(
            (
                int(kwargs.get("source_actor_id", 0) or 0),
                _canonical_skill(kwargs.get("skill_id", 0)),
                int(kwargs.get("effect_instance_id", 0) or 0),
                int(kwargs.get("skill_level_row_id", 0) or 0),
                int(kwargs.get("effect_class_id", 0) or 0),
            )
        )
        return original_response(**kwargs)

    origin_ledger.observe_registration = observe_registration
    origin_ledger.observe_response = observe_response

    def feed(packet: object, packet_features: int | None = None):
        nonlocal packet_ordinal, body_failures
        packet_ordinal += 1
        event = original_feed(packet, packet_features)
        decoded = decoder.last_decoded_message
        if decoded is None or not isinstance(event, CombatEvent) or event.healing:
            return event
        layout = str(getattr(decoded, "layout", ""))
        if "direct37" in layout and bool(
            getattr(decoded, "skill_id_exact", False)
        ):
            direct37_rows.append(
                (
                    int(getattr(decoded, "source_actor_id", 0) or 0),
                    _canonical_skill(event.skill_id),
                    int(getattr(decoded, "damage_result_group_id", 0) or 0),
                    int(getattr(decoded, "damage_object_id", 0) or 0),
                )
            )
            return event
        if "direct41" not in layout:
            return event
        resolved = _direct_body(packet, decoded)
        if resolved is None:
            body_failures += 1
            return event
        xor_phase, body = resolved
        direction = packet.inferred_direction()
        statuses = decode_v576_status_events(
            bytes(getattr(packet, "payload", b"")),
            direction,
            decoder.status_known_skill_ids,
        )
        source_actor_id = int(getattr(decoded, "source_actor_id", 0) or 0)
        target_actor_id = int(getattr(decoded, "target_actor_id", 0) or 0)
        relevant_statuses = [
            status
            for status in statuses
            if int(status.source_actor_id) == source_actor_id
            and int(status.target_actor_id) in {0, target_actor_id}
        ]
        packet_skill_id = _canonical_skill(getattr(decoded, "skill_id", 0))
        assigned_skill_id = _canonical_skill(event.skill_id)
        # ``skill_id_exact`` proves only that marker+106 contained a catalogued
        # integer. A transformed/base sentinel can still be rejected by the
        # canonical event layer (observed once as raw ID 7). Such a row is not
        # usable ground truth: require the wire and emitted canonical skills to
        # be the same non-zero skill before admitting it to validation.
        exact = bool(
            getattr(decoded, "skill_id_exact", False)
            and packet_skill_id > 0
            and assigned_skill_id == packet_skill_id
        )
        truth_skill_id = packet_skill_id if exact else 0
        rows.append(
            Direct41Row(
                packet_ordinal=packet_ordinal,
                timestamp=float(getattr(packet, "timestamp_seconds", 0.0)),
                source_actor_id=source_actor_id,
                target_actor_id=target_actor_id,
                damage=int(getattr(decoded, "damage", 0) or 0),
                layout=layout,
                signature=bytes(getattr(packet, "payload", b""))[5:9].hex(),
                payload_length=len(bytes(getattr(packet, "payload", b""))),
                xor_phase=xor_phase,
                marker_offset=int(getattr(decoded, "record_offset", 0) or 0),
                body=body,
                exact=exact,
                truth_skill_id=truth_skill_id,
                raw_packet_skill_id=int(getattr(decoded, "skill_id", 0) or 0),
                assigned_skill_id=assigned_skill_id,
                assigned_method=str(event.attribution_method or "unresolved"),
                blocking=_classification(event)
                == "blocking_unproven_direct_hit",
                same_packet_status_skills=tuple(
                    sorted({_canonical_skill(status.skill_id) for status in relevant_statuses})
                ),
                same_packet_status_instance_ids=tuple(
                    sorted(
                        {
                            int(status.effect_instance_id)
                            for status in relevant_statuses
                            if int(status.effect_instance_id or 0) > 0
                        }
                    )
                ),
            )
        )
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()

    rows.sort(key=lambda row: row.packet_ordinal)
    domain_maps = _domain_maps(registrations, status_rows, direct37_rows)
    layout_counts: Counter[str] = Counter(row.layout for row in rows)
    exact_layout_counts: Counter[str] = Counter(
        row.layout for row in rows if row.exact
    )
    nonexact_layout_counts: Counter[str] = Counter(
        row.layout for row in rows if not row.exact
    )
    blocking_layout_counts: Counter[str] = Counter(
        row.layout for row in rows if row.blocking
    )
    lengths: Counter[str] = Counter(
        f"{row.layout}|{row.payload_length}" for row in rows
    )
    marker_offsets: Counter[str] = Counter(
        f"{row.layout}|{row.marker_offset}" for row in rows
    )
    methods: Counter[str] = Counter(
        row.assigned_method for row in rows if not row.exact
    )
    exact_rows = sum(row.exact for row in rows)
    nonexact_rows = len(rows) - exact_rows
    blocking_rows = sum(row.blocking for row in rows)
    packet_slot_catalogued = sum(
        (_read_u32(row, EXISTING_PACKET_SKILL_OFFSET) or 0)
        in decoder.damage_skill_ids
        for row in rows
    )
    packet_slot_truth_matches = sum(
        row.exact
        and _canonical_skill(_read_u32(row, EXISTING_PACKET_SKILL_OFFSET))
        == row.truth_skill_id
        for row in rows
    )
    packet_slot_mismatch_examples: list[dict[str, Any]] = []
    for row in rows:
        if not row.exact:
            continue
        slot_value = _read_u32(row, EXISTING_PACKET_SKILL_OFFSET)
        if _canonical_skill(slot_value) == row.truth_skill_id:
            continue
        _add_example(
            packet_slot_mismatch_examples,
            row,
            packet_slot_value=slot_value,
            raw_packet_skill_id=row.raw_packet_skill_id,
        )

    same_packet_status = {
        "exact_support": 0,
        "exact_conflicts": 0,
        "recoverable_nonexact": 0,
        "recoverable_blocking": 0,
        "ambiguous_nonexact": 0,
        "examples": [],
    }
    for row in rows:
        skills = {value for value in row.same_packet_status_skills if value > 0}
        if row.exact and skills:
            same_packet_status["exact_support"] += 1
            if skills != {row.truth_skill_id}:
                same_packet_status["exact_conflicts"] += 1
                _add_example(
                    same_packet_status["examples"],
                    row,
                    status_skills=sorted(skills),
                )
        elif not row.exact and len(skills) == 1:
            same_packet_status["recoverable_nonexact"] += 1
            same_packet_status["recoverable_blocking"] += int(row.blocking)
        elif not row.exact and len(skills) > 1:
            same_packet_status["ambiguous_nonexact"] += 1

    field_profiles: list[dict[str, Any]] = []
    run_results: list[dict[str, Any]] = []
    domain_results: list[dict[str, Any]] = []
    direct_field_results: list[dict[str, Any]] = []
    scopes = ("all", "ordinary", "player")

    for relative_offset in range(SEARCH_RELATIVE_MIN, SEARCH_RELATIVE_MAX + 1):
        profile = Counter()
        distinct_values: set[int] = set()
        run_stats = {scope: _new_run_stats() for scope in scopes}
        states: dict[str, dict[int, RunState]] = {
            scope: {} for scope in scopes
        }
        domain_stats: dict[str, Counter[str]] = {
            name: Counter() for name in domain_maps
        }
        domain_examples: dict[str, list[dict[str, Any]]] = {
            name: [] for name in domain_maps
        }
        direct_stats: Counter[str] = Counter()
        direct_examples: list[dict[str, Any]] = []

        def flush(scope: str, actor_id: int) -> None:
            state = states[scope].pop(actor_id, None)
            if state is None:
                return
            _finalize_run(
                run_stats[scope],
                state,
                capture=source_name,
                relative_offset=relative_offset,
                scope=scope,
            )

        for row in rows:
            value = _read_u32(row, relative_offset)
            overlaps_truth_slot = _overlaps_existing_truth_slot(relative_offset)
            if value is None:
                profile["missing"] += 1
            else:
                profile["present"] += 1
                profile["exact_present"] += int(row.exact)
                profile["nonexact_present"] += int(not row.exact)
                profile["zero"] += int(value == 0)
                profile["all_ones"] += int(value == 0xFFFFFFFF)
                profile["equals_source"] += int(value == row.source_actor_id)
                profile["equals_target"] += int(value == row.target_actor_id)
                profile["equals_damage_low32"] += int(
                    value == (row.damage & 0xFFFFFFFF)
                )
                profile["catalog_skill"] += int(value in decoder.damage_skill_ids)
                profile["truth_match"] += int(
                    row.exact and _canonical_skill(value) == row.truth_skill_id
                )
                if len(distinct_values) < 4096:
                    distinct_values.add(value)

            valid_key = (
                not overlaps_truth_slot
                and value is not None
                and value not in INVALID_KEYS
            )
            row_scopes = ("all", row.variant)
            for scope in row_scopes:
                if not valid_key:
                    flush(scope, row.source_actor_id)
                    continue
                assert value is not None
                state = states[scope].get(row.source_actor_id)
                if state is None or state.value != value:
                    flush(scope, row.source_actor_id)
                    state = RunState(
                        value=value,
                        first_packet=row.packet_ordinal,
                        last_packet=row.packet_ordinal,
                    )
                    states[scope][row.source_actor_id] = state
                    if len(run_stats[scope]["distinct_values"]) < 4096:
                        run_stats[scope]["distinct_values"].add(value)
                state.last_packet = row.packet_ordinal
                if row.exact:
                    state.exact_rows += 1
                    state.exact_skills[row.truth_skill_id] += 1
                else:
                    state.nonexact_rows += 1
                    state.blocking_rows += int(row.blocking)
                    if (
                        len(state.exact_skills) == 1
                        and row.assigned_skill_id in state.exact_skills
                    ):
                        state.old_assignment_matches += 1

            if value is None:
                continue
            if not overlaps_truth_slot and value in decoder.damage_skill_ids:
                candidate_skill = _canonical_skill(value)
                if row.exact:
                    direct_stats["exact_support"] += 1
                    if candidate_skill != row.truth_skill_id:
                        direct_stats["exact_conflicts"] += 1
                        _add_example(
                            direct_examples,
                            row,
                            value=value,
                            candidate_skill=candidate_skill,
                        )
                else:
                    direct_stats["recoverable_nonexact"] += 1
                    direct_stats["recoverable_blocking"] += int(row.blocking)
                    direct_stats["old_assignment_matches"] += int(
                        row.assigned_skill_id == candidate_skill
                    )

            if overlaps_truth_slot:
                continue
            for domain_name, domain_map in domain_maps.items():
                skills = domain_map.get((row.source_actor_id, value))
                if not skills:
                    continue
                stats = domain_stats[domain_name]
                if row.exact:
                    stats["exact_support"] += 1
                    if skills != {row.truth_skill_id}:
                        stats["exact_conflicts"] += 1
                        _add_example(
                            domain_examples[domain_name],
                            row,
                            value=value,
                            domain_skills=sorted(skills),
                        )
                elif len(skills) == 1:
                    recovered_skill = next(iter(skills))
                    stats["recoverable_nonexact"] += 1
                    stats["recoverable_blocking"] += int(row.blocking)
                    stats["old_assignment_matches"] += int(
                        row.assigned_skill_id == recovered_skill
                    )
                else:
                    stats["ambiguous_nonexact"] += 1

        for scope in scopes:
            for actor_id in list(states[scope]):
                flush(scope, actor_id)
            stats = run_stats[scope]
            run_results.append(
                {
                    "capture": source_name,
                    "scope": scope,
                    "relative_offset": relative_offset,
                    **{
                        key: int(value)
                        for key, value in stats.items()
                        if isinstance(value, int)
                    },
                    "distinct_values": len(stats["distinct_values"]),
                    "distinct_values_capped": len(stats["distinct_values"])
                    >= 4096,
                    "recovered_skills": _counter_dict(stats["recovered_skills"]),
                    "conflict_examples": stats["conflict_examples"],
                }
            )

        field_profiles.append(
            {
                "capture": source_name,
                "relative_offset": relative_offset,
                **{key: int(value) for key, value in profile.items()},
                "distinct_values": len(distinct_values),
                "distinct_values_capped": len(distinct_values) >= 4096,
                "overlaps_existing_truth_slot": _overlaps_existing_truth_slot(
                    relative_offset
                ),
            }
        )
        if direct_stats:
            direct_field_results.append(
                {
                    "capture": source_name,
                    "relative_offset": relative_offset,
                    **{key: int(value) for key, value in direct_stats.items()},
                    "examples": direct_examples,
                }
            )
        for domain_name, stats in domain_stats.items():
            if not stats:
                continue
            domain_results.append(
                {
                    "capture": source_name,
                    "domain": domain_name,
                    "relative_offset": relative_offset,
                    **{key: int(value) for key, value in stats.items()},
                    "examples": domain_examples[domain_name],
                }
            )

    byte_profiles: list[dict[str, Any]] = []
    relative_byte_min = min((-row.marker_offset for row in rows), default=0)
    relative_byte_max = max(
        (len(row.body) - row.marker_offset - 1 for row in rows), default=-1
    )
    for relative_offset in range(relative_byte_min, relative_byte_max + 1):
        counts: Counter[int] = Counter()
        exact_present = nonexact_present = 0
        for row in rows:
            absolute = row.marker_offset + relative_offset
            if absolute < 0 or absolute >= len(row.body):
                continue
            counts[row.body[absolute]] += 1
            exact_present += int(row.exact)
            nonexact_present += int(not row.exact)
        if not counts:
            continue
        byte_profiles.append(
            {
                "capture": source_name,
                "relative_offset": relative_offset,
                "present": sum(counts.values()),
                "exact_present": exact_present,
                "nonexact_present": nonexact_present,
                "distinct_values": len(counts),
                "zero": counts[0],
                "top_values": {
                    f"0x{value:02X}": count for value, count in counts.most_common(8)
                },
            }
        )

    samples = []
    for row in rows:
        if len(samples) >= 24:
            break
        if (not row.exact) or len(samples) < 8:
            samples.append(
                {
                    "exact": row.exact,
                    "blocking": row.blocking,
                    "packet": row.packet_ordinal,
                    "timestamp": round(row.timestamp, 6),
                    "layout": row.layout,
                    "signature": row.signature,
                    "payload_length": row.payload_length,
                    "xor_phase": row.xor_phase,
                    "marker_offset": row.marker_offset,
                    "source_actor_id": f"0x{row.source_actor_id:08X}",
                    "target_actor_id": f"0x{row.target_actor_id:08X}",
                    "damage": row.damage,
                    "truth_skill_id": row.truth_skill_id,
                    "raw_packet_skill_id": row.raw_packet_skill_id,
                    "assigned_skill_id": row.assigned_skill_id,
                    "assigned_method": row.assigned_method,
                    "same_packet_status_skills": list(
                        row.same_packet_status_skills
                    ),
                    "same_packet_status_instance_ids": list(
                        row.same_packet_status_instance_ids
                    ),
                    "decoded_body_hex": row.body.hex(),
                }
            )

    return {
        "capture": source_name,
        "path": str(path),
        "direct41_rows": len(rows),
        "exact_rows": exact_rows,
        "nonexact_rows": nonexact_rows,
        "blocking_rows": blocking_rows,
        "body_failures": body_failures,
        "packet_slot_catalogued": packet_slot_catalogued,
        "packet_slot_truth_matches": packet_slot_truth_matches,
        "packet_slot_mismatch_examples": packet_slot_mismatch_examples,
        "layouts": _counter_dict(layout_counts),
        "exact_layouts": _counter_dict(exact_layout_counts),
        "nonexact_layouts": _counter_dict(nonexact_layout_counts),
        "blocking_layouts": _counter_dict(blocking_layout_counts),
        "lengths": _counter_dict(lengths),
        "marker_offsets": _counter_dict(marker_offsets),
        "nonexact_methods": _counter_dict(methods),
        "registrations": len(registrations),
        "status_rows": len(status_rows),
        "exact_direct37_rows": len(direct37_rows),
        "same_packet_status": same_packet_status,
        "field_profiles": field_profiles,
        "byte_profiles": byte_profiles,
        "run_candidates": run_results,
        "domain_candidates": domain_results,
        "direct_field_candidates": direct_field_results,
        "samples": samples,
    }


def _merge_counter_mapping(
    target: Counter[str], mapping: dict[str, Any]
) -> None:
    for key, value in mapping.items():
        target[str(key)] += int(value)


def _merge_candidate_rows(
    reports: list[dict[str, Any]], field_name: str, key_names: tuple[str, ...]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    scalar_names = (
        "validation_runs",
        "validation_rows",
        "consistent_validation_rows",
        "conflicting_runs",
        "conflict_rows",
        "recoverable_nonexact",
        "recoverable_blocking",
        "ambiguous_nonexact",
        "orphan_nonexact",
        "old_assignment_matches",
        "exact_support",
        "exact_conflicts",
    )
    for report in reports:
        capture = report["capture"]
        for row in report[field_name]:
            key = tuple(row[name] for name in key_names)
            merged = grouped.setdefault(
                key,
                {
                    **{name: row[name] for name in key_names},
                    **{name: 0 for name in scalar_names},
                    "distinct_values": 0,
                    "validation_captures": set(),
                    "recovery_captures": set(),
                    "support_captures": set(),
                    "per_capture": {},
                    "conflict_examples": [],
                    "examples": [],
                    "recovered_skills": Counter(),
                },
            )
            for name in scalar_names:
                merged[name] += int(row.get(name, 0) or 0)
            merged["distinct_values"] += int(row.get("distinct_values", 0) or 0)
            if int(row.get("validation_rows", 0) or 0) > 0:
                merged["validation_captures"].add(capture)
            if int(row.get("recoverable_blocking", 0) or 0) > 0:
                merged["recovery_captures"].add(capture)
            if int(
                row.get("exact_support", row.get("validation_rows", 0)) or 0
            ) > 0:
                merged["support_captures"].add(capture)
            merged["per_capture"][capture] = {
                name: int(row.get(name, 0) or 0) for name in scalar_names
            }
            for skill_id, count in row.get("recovered_skills", {}).items():
                merged["recovered_skills"][int(skill_id)] += int(count)
            for name in ("conflict_examples", "examples"):
                for example in row.get(name, ()):
                    if len(merged[name]) < 12:
                        merged[name].append(example)

    result = []
    all_captures = {report["capture"] for report in reports}
    for merged in grouped.values():
        support_metric = (
            merged["validation_rows"]
            if merged["validation_rows"] > 0
            else merged["exact_support"]
        )
        conflict_metric = (
            merged["conflict_rows"]
            if merged["validation_rows"] > 0
            else merged["exact_conflicts"]
        )
        validation_capture_names = (
            merged["validation_captures"]
            if merged["validation_rows"] > 0
            else merged["support_captures"]
        )
        loo_folds = []
        for held_out in sorted(all_captures):
            held = merged["per_capture"].get(held_out, {})
            held_support = int(
                held.get(
                    "validation_rows"
                    if merged["validation_rows"] > 0
                    else "exact_support",
                    0,
                )
            )
            if held_support <= 0:
                continue
            train_captures = validation_capture_names - {held_out}
            train_conflicts = conflict_metric - int(
                held.get(
                    "conflict_rows"
                    if merged["validation_rows"] > 0
                    else "exact_conflicts",
                    0,
                )
            )
            if len(train_captures) < 2 or train_conflicts != 0:
                continue
            held_conflicts = int(
                held.get(
                    "conflict_rows"
                    if merged["validation_rows"] > 0
                    else "exact_conflicts",
                    0,
                )
            )
            loo_folds.append(
                {
                    "held_out": held_out,
                    "validation_rows": held_support,
                    "conflicts": held_conflicts,
                }
            )
        accepted = (
            support_metric >= ACCEPT_MIN_VALIDATION_ROWS
            and len(validation_capture_names) >= ACCEPT_MIN_VALIDATION_CAPTURES
            and conflict_metric == 0
            and merged["recoverable_blocking"] > 0
            and len(loo_folds) >= ACCEPT_MIN_VALIDATION_CAPTURES
            and sum(fold["conflicts"] for fold in loo_folds) == 0
            and (
                merged["distinct_values"] >= ACCEPT_MIN_DISTINCT_VALUES
                or field_name
                in {"domain_candidates", "direct_field_candidates"}
            )
        )
        result.append(
            {
                **{key: value for key, value in merged.items() if key not in {
                    "validation_captures",
                    "recovery_captures",
                    "support_captures",
                    "recovered_skills",
                }},
                "validation_captures": len(validation_capture_names),
                "validation_capture_names": sorted(validation_capture_names),
                "recovery_captures": len(merged["recovery_captures"]),
                "recovery_capture_names": sorted(merged["recovery_captures"]),
                "support_captures": len(merged["support_captures"]),
                "recovered_skills": _counter_dict(merged["recovered_skills"]),
                "loo_folds": loo_folds,
                "loo_validation_rows": sum(
                    fold["validation_rows"] for fold in loo_folds
                ),
                "loo_conflicts": sum(fold["conflicts"] for fold in loo_folds),
                "accepted": accepted,
            }
        )
    result.sort(
        key=lambda row: (
            not bool(row["accepted"]),
            -int(row["recoverable_blocking"]),
            -int(row.get("validation_rows", row.get("exact_support", 0))),
            tuple(str(row.get(name, "")) for name in key_names),
        )
    )
    return result


def _merge_field_profiles(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, Counter[str]] = defaultdict(Counter)
    capped: dict[int, bool] = defaultdict(bool)
    for report in reports:
        for row in report["field_profiles"]:
            offset = int(row["relative_offset"])
            for key, value in row.items():
                if key in {
                    "capture",
                    "relative_offset",
                    "distinct_values_capped",
                    "overlaps_existing_truth_slot",
                }:
                    continue
                grouped[offset][key] += int(value)
            capped[offset] = capped[offset] or bool(
                row.get("distinct_values_capped", False)
            )
    return [
        {
            "relative_offset": offset,
            **{key: int(value) for key, value in counter.items()},
            "distinct_values_capped_in_any_capture": capped[offset],
            "overlaps_existing_truth_slot": _overlaps_existing_truth_slot(offset),
        }
        for offset, counter in sorted(grouped.items())
    ]


def _merge_byte_profiles(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, Counter[str]] = defaultdict(Counter)
    top: dict[int, Counter[str]] = defaultdict(Counter)
    for report in reports:
        for row in report["byte_profiles"]:
            offset = int(row["relative_offset"])
            for key in ("present", "exact_present", "nonexact_present", "zero"):
                grouped[offset][key] += int(row.get(key, 0) or 0)
            for value, count in row.get("top_values", {}).items():
                top[offset][value] += int(count)
    return [
        {
            "relative_offset": offset,
            **{key: int(value) for key, value in grouped[offset].items()},
            "top_values": _counter_dict(top[offset]),
        }
        for offset in sorted(grouped)
    ]


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    layouts: Counter[str] = Counter()
    exact_layouts: Counter[str] = Counter()
    nonexact_layouts: Counter[str] = Counter()
    blocking_layouts: Counter[str] = Counter()
    lengths: Counter[str] = Counter()
    marker_offsets: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    status_totals = Counter()
    status_examples: list[dict[str, Any]] = []
    status_per_capture: dict[str, dict[str, int]] = {}
    for report in reports:
        for key in (
            "direct41_rows",
            "exact_rows",
            "nonexact_rows",
            "blocking_rows",
            "body_failures",
            "packet_slot_catalogued",
            "packet_slot_truth_matches",
            "registrations",
            "status_rows",
            "exact_direct37_rows",
        ):
            totals[key] += int(report.get(key, 0) or 0)
        for target, key in (
            (layouts, "layouts"),
            (exact_layouts, "exact_layouts"),
            (nonexact_layouts, "nonexact_layouts"),
            (blocking_layouts, "blocking_layouts"),
            (lengths, "lengths"),
            (marker_offsets, "marker_offsets"),
            (methods, "nonexact_methods"),
        ):
            _merge_counter_mapping(target, report.get(key, {}))
        status = report["same_packet_status"]
        status_per_capture[report["capture"]] = {
            key: int(value)
            for key, value in status.items()
            if key != "examples"
        }
        for key, value in status.items():
            if key == "examples":
                for example in value:
                    if len(status_examples) < 12:
                        status_examples.append(
                            {"capture": report["capture"], **example}
                        )
            else:
                status_totals[key] += int(value)

    run_candidates = _merge_candidate_rows(
        reports, "run_candidates", ("scope", "relative_offset")
    )
    domain_candidates = _merge_candidate_rows(
        reports, "domain_candidates", ("domain", "relative_offset")
    )
    direct_field_candidates = _merge_candidate_rows(
        reports, "direct_field_candidates", ("relative_offset",)
    )
    accepted = [
        {"kind": "record_run", **row}
        for row in run_candidates
        if row["accepted"]
    ] + [
        {"kind": "protocol_domain", **row}
        for row in domain_candidates
        if row["accepted"]
    ] + [
        {"kind": "direct_skill_field", **row}
        for row in direct_field_candidates
        if row["accepted"]
    ]
    status_support_captures = {
        capture
        for capture, row in status_per_capture.items()
        if row.get("exact_support", 0) > 0
    }
    status_loo_folds = []
    for held_out in sorted(status_support_captures):
        held = status_per_capture[held_out]
        train_captures = status_support_captures - {held_out}
        train_conflicts = (
            status_totals["exact_conflicts"]
            - held.get("exact_conflicts", 0)
        )
        if len(train_captures) < 2 or train_conflicts != 0:
            continue
        status_loo_folds.append(
            {
                "held_out": held_out,
                "validation_rows": held.get("exact_support", 0),
                "conflicts": held.get("exact_conflicts", 0),
            }
        )
    status_accepted = (
        status_totals["exact_support"] >= ACCEPT_MIN_VALIDATION_ROWS
        and status_totals["exact_conflicts"] == 0
        and status_totals["recoverable_blocking"] > 0
        and len(status_support_captures) >= ACCEPT_MIN_VALIDATION_CAPTURES
        and len(status_loo_folds) >= ACCEPT_MIN_VALIDATION_CAPTURES
        and sum(fold["conflicts"] for fold in status_loo_folds) == 0
    )
    if status_accepted:
        accepted.append(
            {
                "kind": "same_packet_status_skill",
                **{key: int(value) for key, value in status_totals.items()},
                "validation_captures": len(status_support_captures),
                "loo_folds": status_loo_folds,
                "loo_validation_rows": sum(
                    fold["validation_rows"] for fold in status_loo_folds
                ),
                "loo_conflicts": sum(
                    fold["conflicts"] for fold in status_loo_folds
                ),
                "accepted": True,
            }
        )
    accepted.sort(
        key=lambda row: (
            -int(row.get("recoverable_blocking", 0)),
            str(row.get("kind", "")),
            int(row.get("relative_offset", 0)),
        )
    )
    primary = accepted[0] if accepted else None
    recovered = int(primary.get("recoverable_blocking", 0)) if primary else 0
    total_rows = totals["direct41_rows"]
    exact_rows = totals["exact_rows"]
    blocking_rows = totals["blocking_rows"]
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_signature": _analysis_signature(),
        "policy": {
            "truth_source": (
                "existing direct41 packet_skill_id at marker+106; used only "
                "to validate other fields/links"
            ),
            "truth_slot_excluded_from_candidates": [
                EXISTING_PACKET_SKILL_OFFSET,
                EXISTING_PACKET_SKILL_END,
            ],
            "candidate_search": (
                "every little-endian u32 window from marker-16 through "
                "marker+117, plus registered object, exact direct37 result/object, "
                "status instance/SkillLevel/EffectClass and same-packet status domains"
            ),
            "run_lifecycle": (
                "per capture and source actor; a run ends only when the explicit "
                "candidate field changes or disappears"
            ),
            "acceptance": {
                "minimum_exact_validation_rows": ACCEPT_MIN_VALIDATION_ROWS,
                "minimum_validation_captures": ACCEPT_MIN_VALIDATION_CAPTURES,
                "minimum_distinct_values": ACCEPT_MIN_DISTINCT_VALUES,
                "required_conflicts": 0,
                "required_leave_one_capture_conflicts": 0,
                "requires_blocking_hit_recovery": True,
            },
            "not_used_as_truth": [
                "current inferred skill assignment on continuation rows",
                "nearest action in time",
                "damage magnitude/fingerprint",
                "skill-name similarity",
            ],
        },
        "captures": len(reports),
        **{key: int(value) for key, value in totals.items()},
        "existing_packet_skill_coverage_percent": round(
            exact_rows / max(1, total_rows) * 100.0, 6
        ),
        "blocking_recovered_by_primary": recovered,
        "blocking_remaining_after_primary": max(0, blocking_rows - recovered),
        "total_coverage_after_primary_percent": round(
            (exact_rows + recovered) / max(1, total_rows) * 100.0, 6
        ),
        "safe_to_integrate": bool(primary) and recovered > 0,
        "primary_candidate": primary,
        "accepted_candidates": accepted,
        "same_packet_status": {
            **{key: int(value) for key, value in status_totals.items()},
            "validation_captures": len(status_support_captures),
            "loo_folds": status_loo_folds,
            "loo_validation_rows": sum(
                fold["validation_rows"] for fold in status_loo_folds
            ),
            "loo_conflicts": sum(
                fold["conflicts"] for fold in status_loo_folds
            ),
            "accepted": status_accepted,
            "examples": status_examples,
        },
        "layouts": _counter_dict(layouts),
        "exact_layouts": _counter_dict(exact_layouts),
        "nonexact_layouts": _counter_dict(nonexact_layouts),
        "blocking_layouts": _counter_dict(blocking_layouts),
        "lengths": _counter_dict(lengths),
        "marker_offsets": _counter_dict(marker_offsets),
        "nonexact_methods": _counter_dict(methods),
        "field_profiles": _merge_field_profiles(reports),
        "byte_profiles": _merge_byte_profiles(reports),
        "run_candidates": run_candidates,
        "domain_candidates": domain_candidates,
        "direct_field_candidates": direct_field_candidates,
        "capture_reports": [
            {
                key: value
                for key, value in report.items()
                if key
                not in {
                    "field_profiles",
                    "byte_profiles",
                    "run_candidates",
                    "domain_candidates",
                    "direct_field_candidates",
                }
            }
            for report in sorted(reports, key=lambda row: row["capture"])
        ],
    }


def _candidate_label(candidate: dict[str, Any] | None) -> str:
    if not candidate:
        return "无"
    if candidate["kind"] == "record_run":
        return (
            f"记录内连续运行键 scope={candidate['scope']} "
            f"u32(marker{int(candidate['relative_offset']):+d})"
        )
    if candidate["kind"] == "protocol_domain":
        return (
            f"{candidate['domain']} at "
            f"u32(marker{int(candidate['relative_offset']):+d})"
        )
    if candidate["kind"] == "direct_skill_field":
        return f"直接技能字段 u32(marker{int(candidate['relative_offset']):+d})"
    return "同包唯一状态技能"


def _render_report(result: dict[str, Any]) -> str:
    primary = result["primary_candidate"]
    lines = [
        "# direct41 确定性连接审计",
        "",
        "## 结论",
        "",
        f"- 抓包：{result['captures']}",
        f"- direct41 记录：{result['direct41_rows']:,}",
        f"- 现有 `marker+106 packet_skill_id` 精确真值：{result['exact_rows']:,} "
        f"（{result['existing_packet_skill_coverage_percent']:.6f}%）",
        f"- 无精确槽记录：{result['nonexact_rows']:,}",
        f"- 当前阻断记录：{result['blocking_rows']:,}",
        f"- 通过门槛的新增候选：{len(result['accepted_candidates'])}",
        f"- 首选候选：{_candidate_label(primary)}",
        f"- 首选候选覆盖阻断记录：{result['blocking_recovered_by_primary']:,}",
        f"- 首选候选冲突：{int(primary.get('conflict_rows', primary.get('exact_conflicts', 0))) if primary else 0:,}",
        f"- 留一抓包冲突：{int(primary.get('loo_conflicts', 0)) if primary else 0:,}",
        f"- 剩余阻断：{result['blocking_remaining_after_primary']:,}",
        f"- 可否安全接入：{'可以，仅限首选候选已验证覆盖' if result['safe_to_integrate'] else '不可以'}",
        "",
        "`marker+106` 是本审计使用的既有精确真值，不参与候选竞争；所有与 "
        "`[+106,+110)` 重叠的 u32 窗口均被排除，避免循环证明。continuation "
        "当前推断技能、时间最近动作和伤害指纹均未作为真值。",
        "此前 `u32(marker+103)` 的 `[+103,+107)` 与真值槽在 byte `+106` "
        "相交，属于循环证明；旧结论作废，禁止接入。",
        "",
        "## 包布局",
        "",
        "| layout | 全部 | 精确槽 | 无精确槽 | 阻断 |",
        "|---|---:|---:|---:|---:|",
    ]
    layout_names = sorted(
        set(result["layouts"])
        | set(result["exact_layouts"])
        | set(result["nonexact_layouts"])
    )
    for layout in layout_names:
        lines.append(
            f"| {layout} | {result['layouts'].get(layout, 0):,} | "
            f"{result['exact_layouts'].get(layout, 0):,} | "
            f"{result['nonexact_layouts'].get(layout, 0):,} | "
            f"{result['blocking_layouts'].get(layout, 0):,} |"
        )
    lines.extend(
        [
            "",
            "## 通过零冲突门槛的候选",
            "",
            "| 候选 | 精确验证行 | 验证抓包 | 冲突 | LOO 行 | LOO 冲突 | 阻断覆盖 | 剩余 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for candidate in result["accepted_candidates"]:
        validation = int(
            candidate.get("validation_rows", candidate.get("exact_support", 0))
        )
        conflicts = int(
            candidate.get("conflict_rows", candidate.get("exact_conflicts", 0))
        )
        recovered = int(candidate.get("recoverable_blocking", 0))
        lines.append(
            f"| {_candidate_label(candidate)} | {validation:,} | "
            f"{int(candidate.get('validation_captures', 0)):,} | {conflicts:,} | "
            f"{int(candidate.get('loo_validation_rows', 0)):,} | "
            f"{int(candidate.get('loo_conflicts', 0)):,} | {recovered:,} | "
            f"{max(0, result['blocking_rows'] - recovered):,} |"
        )
    if not result["accepted_candidates"]:
        lines.append("| 无 | 0 | 0 | 0 | 0 | 0 | 0 | " f"{result['blocking_rows']:,} |")
    lines.extend(
        [
            "",
            "## 审计边界",
            "",
            "- 记录运行键按抓包、source actor 分区；只有字段值显式变化或字段消失才结束运行，不使用秒数窗口。",
            "- 对象、direct37 结果/对象、状态实例、SkillLevel 和 EffectClass 连接均使用完整整数，不做掩码或截断。",
            "- 留一抓包时，候选偏移/连接类型只由其他至少两份有支持的抓包成立后，才统计保留抓包精确真值；任何冲突都会拒绝候选。",
            "- `safe_to_integrate` 只针对首选候选实际覆盖的阻断行，不代表所有 continuation 已解决。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _flatten_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, key in (
        ("record_run", "run_candidates"),
        ("protocol_domain", "domain_candidates"),
        ("direct_skill_field", "direct_field_candidates"),
    ):
        for candidate in result[key]:
            if kind == "record_run":
                validation_rows = candidate.get("validation_rows", 0)
                conflicts = candidate.get("conflict_rows", 0)
            else:
                validation_rows = candidate.get("exact_support", 0)
                conflicts = candidate.get("exact_conflicts", 0)
            rows.append(
                {
                    "kind": kind,
                    "scope": candidate.get("scope", ""),
                    "domain": candidate.get("domain", ""),
                    "relative_offset": candidate.get("relative_offset", ""),
                    "overlaps_existing_truth_slot": (
                        _overlaps_existing_truth_slot(
                            int(candidate.get("relative_offset", 0))
                        )
                    ),
                    "accepted": candidate.get("accepted", False),
                    "validation_rows": validation_rows,
                    "validation_captures": candidate.get(
                        "validation_captures", 0
                    ),
                    "conflicts": conflicts,
                    "loo_validation_rows": candidate.get(
                        "loo_validation_rows", 0
                    ),
                    "loo_conflicts": candidate.get("loo_conflicts", 0),
                    "recoverable_nonexact": candidate.get(
                        "recoverable_nonexact", 0
                    ),
                    "recoverable_blocking": candidate.get(
                        "recoverable_blocking", 0
                    ),
                    "recovery_captures": candidate.get(
                        "recovery_captures", 0
                    ),
                    "ambiguous_nonexact": candidate.get(
                        "ambiguous_nonexact", 0
                    ),
                    "orphan_nonexact": candidate.get("orphan_nonexact", 0),
                    "distinct_values": candidate.get("distinct_values", 0),
                    "old_assignment_matches": candidate.get(
                        "old_assignment_matches", 0
                    ),
                }
            )
    rows.sort(
        key=lambda row: (
            not bool(row["accepted"]),
            -int(row["recoverable_blocking"]),
            -int(row["validation_rows"]),
            row["kind"],
            str(row["scope"]),
            str(row["domain"]),
            int(row["relative_offset"] or 0),
        )
    )
    return rows


def _resolve_paths(args: argparse.Namespace) -> list[tuple[Path, str]]:
    capture_root = args.capture_root.resolve()
    if args.captures:
        paths = []
        for supplied in args.captures:
            path = supplied if supplied.is_absolute() else capture_root / supplied
            paths.append((path.resolve(), str(supplied)))
    else:
        corpus = json.loads(args.corpus.resolve().read_text(encoding="utf-8"))
        paths = [
            ((capture_root / str(row["source"])).resolve(), str(row["source"]))
            for row in corpus["files"]
        ]
    if args.limit > 0:
        paths = paths[: args.limit]
    missing = [str(path) for path, _name in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"missing captures: {missing[:10]}")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit deterministic packet/object/status/transaction links for "
            "skill-less direct41 continuation records."
        )
    )
    parser.add_argument("capture_root", type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--corpus", type=Path)
    selection.add_argument("--captures", nargs="+", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    prefix = args.output_prefix.resolve()
    if not prefix.name.startswith("direct41_deterministic_links_"):
        raise SystemExit(
            "output prefix basename must start with direct41_deterministic_links_"
        )
    paths = _resolve_paths(args)
    reports: list[dict[str, Any]] = []
    if max(1, args.workers) == 1:
        for index, (path, name) in enumerate(paths, 1):
            report = _audit_capture(str(path), name)
            reports.append(report)
            print(
                f"[{index}/{len(paths)}] {name}: direct41={report['direct41_rows']} "
                f"exact={report['exact_rows']} blockers={report['blocking_rows']}",
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(_audit_capture, str(path), name): name
                for path, name in paths
            }
            for index, future in enumerate(as_completed(futures), 1):
                report = future.result()
                reports.append(report)
                print(
                    f"[{index}/{len(paths)}] {report['capture']}: "
                    f"direct41={report['direct41_rows']} "
                    f"exact={report['exact_rows']} blockers={report['blocking_rows']}",
                    flush=True,
                )

    result = summarize(reports)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    summary_path = prefix.with_name(prefix.name + "_summary.json")
    report_path = prefix.with_name(prefix.name + "_report.md")
    candidate_path = prefix.with_name(prefix.name + "_candidates.csv")
    field_path = prefix.with_name(prefix.name + "_field_profiles.csv")
    summary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.write_text(_render_report(result), encoding="utf-8")
    _write_csv(
        candidate_path,
        _flatten_candidates(result),
        [
            "kind",
            "scope",
            "domain",
            "relative_offset",
            "overlaps_existing_truth_slot",
            "accepted",
            "validation_rows",
            "validation_captures",
            "conflicts",
            "loo_validation_rows",
            "loo_conflicts",
            "recoverable_nonexact",
            "recoverable_blocking",
            "recovery_captures",
            "ambiguous_nonexact",
            "orphan_nonexact",
            "distinct_values",
            "old_assignment_matches",
        ],
    )
    _write_csv(
        field_path,
        result["field_profiles"],
        [
            "relative_offset",
            "present",
            "missing",
            "exact_present",
            "nonexact_present",
            "zero",
            "all_ones",
            "equals_source",
            "equals_target",
            "equals_damage_low32",
            "catalog_skill",
            "truth_match",
            "distinct_values",
            "distinct_values_capped_in_any_capture",
            "overlaps_existing_truth_slot",
        ],
    )
    print(
        f"[done] captures={result['captures']} direct41={result['direct41_rows']} "
        f"blockers={result['blocking_rows']} accepted={len(result['accepted_candidates'])} "
        f"safe={result['safe_to_integrate']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
