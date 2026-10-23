from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import struct
import sys
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_damage_object_registration,
    decode_v576_embedded_direct_damage_events,
    decode_v576_server_damage_object_registrations,
    decode_v576_status_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.audit_direct_damage_release_gate import _classification  # noqa: E402


SCHEMA_VERSION = 2
MARKER = b"\x08\x73\x00"
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")
SEARCH_MIN = -16
SEARCH_MAX = 180
MIN_EXACT_ROWS = 100
MIN_CAPTURES = 3
MIN_ACTORS = 3
MIN_TRUTH_SKILLS = 3
MIN_OBJECT_EXACT_ROWS = 30
MIN_VALIDATION_DIRECT_ROWS = 30
MIN_VALIDATION_OBJECT_ROWS = 10

# A four-byte candidate is rejected if any byte overlaps one of these fields.
# Result group and damage are forbidden evidence. Actor/target/object fields
# are audited only through their semantic connection path, never as skill IDs.
FORBIDDEN_DIRECT_FIELD_RANGES = (
    (-4, 0, "target_actor_id"),
    (4, 8, "result_group_id"),
    (8, 12, "source_actor_id"),
    (13, 17, "known_damage_object_id"),
    (19, 27, "damage_amount"),
    (102, 106, "existing_skill_truth_102"),
    (114, 118, "existing_skill_truth_114"),
)
TRUTH_RANGES = ((102, 106), (114, 118))
KNOWN_OBJECT_RANGE = (13, 17)


@dataclass(slots=True)
class RawRow:
    marker: int
    next_marker: int
    body: bytes
    signature: str
    payload_length: int
    timestamp: float
    source_actor_id: int
    target_actor_id: int
    observer_actor_id: int
    damage_object_id: int
    damage: int
    exact: bool
    truth_skill_id: int
    blocking: bool
    assigned_method: str
    matched_event: bool


def _overlaps(start: int, width: int, ranges: Iterable[tuple[int, int]]) -> bool:
    end = start + width
    return any(start < stop and end > begin for begin, stop in ranges)


def _forbidden_direct_field(relative: int) -> str:
    end = relative + 4
    for begin, stop, reason in FORBIDDEN_DIRECT_FIELD_RANGES:
        if relative < stop and end > begin:
            return reason
    return ""


def _actor_id(value: object) -> int:
    try:
        return int(str(value or "0"), 16)
    except (TypeError, ValueError):
        return 0


def _counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in sorted(
            counter.items(), key=lambda item: (-item[1], str(item[0]))
        )
    }


def _analysis_signature() -> dict[str, Any]:
    relative_paths = (
        "tools/audit_direct37_variable_branches.py",
        "dragon_nest_builder/live_dps/protocol_v576.py",
        "dragon_nest_builder/live_dps/v576/core_decoder.py",
        "dragon_nest_builder/live_dps/damage_attribution.py",
        "dragon_nest_builder/data/live_dps_profiles/v576.json",
    )
    combined = hashlib.sha256()
    files: dict[str, str] = {}
    for relative in relative_paths:
        path = ROOT / relative
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[relative] = digest
        combined.update(relative.encode("utf-8"))
        combined.update(digest.encode("ascii"))
    return {"sha256": combined.hexdigest(), "files": files}


def _body_for_rows(payload: bytes, rows: tuple[object, ...]) -> bytes | None:
    if not rows:
        return None
    first = rows[0]
    marker = int(getattr(first, "record_offset", -1) or 0)
    source = int(getattr(first, "source_actor_id", 0) or 0)
    damage = int(getattr(first, "damage", 0) or 0)
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        if marker < 0 or marker + 27 > len(body):
            continue
        if body[marker : marker + 3] != MARKER:
            continue
        if U32.unpack_from(body, marker + 8)[0] != source:
            continue
        if U64.unpack_from(body, marker + 19)[0] != damage:
            continue
        return body
    return None


def _relation(value: int, source: int, observer: int) -> str:
    if value == source:
        return "source"
    if value == observer:
        return "observer"
    if value == 0:
        return "zero"
    return "short" if value <= 0x00FFFFFF else "long"


def _branch_keys(row: RawRow) -> tuple[tuple[str, str], ...]:
    sentinel = row.body[row.marker + 16 : row.marker + 19].hex()
    outer_target = (
        U32.unpack_from(row.body, 14)[0] if len(row.body) >= 18 else 0
    )
    span = row.next_marker - row.marker
    structure = (
        f"sent={sentinel};src={'short' if row.source_actor_id <= 0x00FFFFFF else 'long'};"
        f"outer={_relation(outer_target, row.source_actor_id, row.observer_actor_id)};"
        f"target={_relation(row.target_actor_id, row.source_actor_id, row.observer_actor_id)};"
        f"span={span}"
    )
    wire_shape = (
        f"sig={row.signature};len={row.payload_length};m={row.marker};span={span};"
        f"sent={sentinel}"
    )
    return (
        ("global", "*"),
        ("structure", structure),
        ("wire_shape", wire_shape),
    )


def _candidate_offsets(row: RawRow) -> range:
    maximum = min(SEARCH_MAX, row.next_marker - row.marker - 4)
    return range(SEARCH_MIN, maximum + 1)


def _event_buckets(events: list[object]) -> dict[tuple[int, int, int], deque[CombatEvent]]:
    buckets: dict[tuple[int, int, int], deque[CombatEvent]] = defaultdict(deque)
    for event in events:
        if not isinstance(event, CombatEvent) or event.healing:
            continue
        key = (
            _actor_id(event.source_id),
            max(0, int(event.amount)),
            int(event.wire_damage_object_id or 0),
        )
        buckets[key].append(event)
    return buckets


def _new_stat() -> dict[str, Any]:
    return {
        "support": 0,
        "conflict": 0,
        "blocking": 0,
        "nonexact": 0,
        "actors": set(),
        "truth_skills": Counter(),
        "candidate_skills": Counter(),
        "examples": [],
    }


def _observe_direct_candidate(
    stats: dict[tuple[str, str, int], dict[str, Any]],
    key: tuple[str, str, int],
    row: RawRow,
    value: int,
    capture: str,
) -> None:
    stat = stats.setdefault(key, _new_stat())
    if row.exact:
        if value == row.truth_skill_id:
            stat["support"] += 1
        else:
            stat["conflict"] += 1
        stat["actors"].add(f"{row.source_actor_id:08X}")
        stat["truth_skills"][row.truth_skill_id] += 1
        stat["candidate_skills"][value] += 1
        if value != row.truth_skill_id and len(stat["examples"]) < 4:
            stat["examples"].append(
                {
                    "capture": capture,
                    "source_actor_id": f"0x{row.source_actor_id:08X}",
                    "truth_skill_id": row.truth_skill_id,
                    "candidate_skill_id": value,
                    "marker": row.marker,
                    "length": row.payload_length,
                }
            )
    else:
        stat["nonexact"] += 1
        if row.blocking:
            stat["blocking"] += 1


def _observe_link_candidate(
    stats: dict[tuple[Any, ...], dict[str, Any]],
    key: tuple[Any, ...],
    row: RawRow,
    linked_skill_id: int,
    capture: str,
) -> None:
    stat = stats.setdefault(key, _new_stat())
    if row.exact:
        if linked_skill_id == row.truth_skill_id:
            stat["support"] += 1
        else:
            stat["conflict"] += 1
        stat["actors"].add(f"{row.source_actor_id:08X}")
        stat["truth_skills"][row.truth_skill_id] += 1
        stat["candidate_skills"][linked_skill_id] += 1
        if linked_skill_id != row.truth_skill_id and len(stat["examples"]) < 4:
            stat["examples"].append(
                {
                    "capture": capture,
                    "source_actor_id": f"0x{row.source_actor_id:08X}",
                    "truth_skill_id": row.truth_skill_id,
                    "linked_skill_id": linked_skill_id,
                    "marker": row.marker,
                    "length": row.payload_length,
                }
            )
    else:
        stat["nonexact"] += 1
        if row.blocking:
            stat["blocking"] += 1


def _render_stats(stats: dict[tuple[Any, ...], dict[str, Any]], names: tuple[str, ...]) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for key, stat in stats.items():
        rendered.append(
            {
                **{name: value for name, value in zip(names, key)},
                "support": int(stat["support"]),
                "conflict": int(stat["conflict"]),
                "blocking": int(stat["blocking"]),
                "nonexact": int(stat["nonexact"]),
                "actors": sorted(stat["actors"]),
                "truth_skills": _counter_dict(stat["truth_skills"]),
                "candidate_skills": _counter_dict(stat["candidate_skills"]),
                "examples": stat["examples"],
            }
        )
    return rendered


def audit_capture(path_text: str, source_name: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    field_stats: dict[tuple[str, str, int], dict[str, Any]] = {}
    object_stats: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    status_stats: dict[tuple[str, str, int], dict[str, Any]] = {}
    branch_counts: Counter[tuple[str, str]] = Counter()
    branch_exact: Counter[tuple[str, str]] = Counter()
    branch_blocking: Counter[tuple[str, str]] = Counter()
    methods: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    # object_id -> (Projectile template, packet ordinal).  A registration in
    # the current packet is not prior evidence and may not validate a new
    # direct37 link.
    active_objects: dict[int, dict[int, tuple[int, int, float]]] = defaultdict(dict)
    active_statuses: dict[int, dict[int, tuple[int, float]]] = defaultdict(dict)
    packet_ordinal = 0

    def feed_events(packet):
        nonlocal packet_ordinal
        packet_ordinal += 1
        payload = bytes(packet.payload)
        direction = packet.inferred_direction()

        local = decode_v576_damage_object_registration(payload, direction)
        registrations = (() if local is None else (local,)) + tuple(
            decode_v576_server_damage_object_registrations(
                payload, direction, (), include_unmapped=True
            )
        )
        for registration in registrations:
            active_objects[int(registration.source_actor_id)][
                int(registration.damage_object_id)
            ] = (
                int(registration.projectile_template_id),
                packet_ordinal,
                float(packet.timestamp_seconds),
            )

        decoded_rows = decode_v576_embedded_direct_damage_events(
            payload, direction, catalog
        )
        body = _body_for_rows(payload, decoded_rows)
        events = original_feed_events(packet)
        buckets = _event_buckets(events)
        if decoded_rows and body is None:
            totals["body_failures"] += len(decoded_rows)

        marker_offsets = sorted(
            int(getattr(item, "record_offset", -1) or 0)
            for item in decoded_rows
        )
        for decoded in decoded_rows:
            marker = int(getattr(decoded, "record_offset", -1) or 0)
            if body is None or marker < 4 or marker + 27 > len(body):
                continue
            next_marker = next(
                (value for value in marker_offsets if value > marker), len(body)
            )
            source_actor_id = int(decoded.source_actor_id or 0)
            damage = max(0, int(decoded.damage or 0))
            object_id = int(decoded.damage_object_id or 0)
            key = (source_actor_id, damage, object_id)
            event = buckets[key].popleft() if buckets.get(key) else None
            classification = _classification(event) if event is not None else "unmatched"
            matched_outgoing = classification not in {"unmatched", "not_outgoing_damage"}
            # Fixed-slot protocol truth is independent of the diagnostic
            # CombatEvent join.  Damage magnitude is used only to pair a row
            # with the current UI event for blocker/coverage reporting.
            exact = bool(decoded.skill_id_exact)
            truth = int(decoded.skill_id or 0) if exact else 0
            blocking = classification == "blocking_unproven_direct_hit"
            assigned_method = (
                str(event.attribution_method or "unresolved").strip().casefold()
                if event is not None
                else "unmatched"
            )
            row = RawRow(
                marker=marker,
                next_marker=next_marker,
                body=body,
                signature=payload[5:9].hex() if len(payload) >= 9 else "",
                payload_length=len(payload),
                timestamp=float(packet.timestamp_seconds),
                source_actor_id=source_actor_id,
                target_actor_id=int(decoded.target_actor_id or 0),
                observer_actor_id=int(decoded.observer_actor_id or 0),
                damage_object_id=object_id,
                damage=damage,
                exact=exact,
                truth_skill_id=truth,
                blocking=blocking,
                assigned_method=assigned_method,
                matched_event=event is not None,
            )
            totals["direct37_rows"] += 1
            totals["matched_events"] += int(event is not None)
            totals["matched_outgoing_rows"] += int(matched_outgoing)
            totals["exact_rows"] += int(exact)
            totals["nonexact_outgoing_rows"] += int(matched_outgoing and not exact)
            totals["blocking_rows"] += int(blocking)
            if blocking:
                methods[assigned_method] += 1

            branches = _branch_keys(row)
            for branch_key in branches:
                branch_counts[branch_key] += 1
                branch_exact[branch_key] += int(exact)
                branch_blocking[branch_key] += int(blocking)

            if not (exact or blocking):
                continue
            source_objects = active_objects.get(source_actor_id, {})
            now = row.timestamp
            source_statuses = active_statuses.get(source_actor_id, {})
            for instance_id, (_skill_id, expires_at) in tuple(source_statuses.items()):
                if expires_at < now:
                    source_statuses.pop(instance_id, None)

            for relative in _candidate_offsets(row):
                absolute = marker + relative
                if absolute < 0 or absolute + 4 > next_marker:
                    continue
                value = U32.unpack_from(body, absolute)[0]
                forbidden = _forbidden_direct_field(relative)
                for scope, branch in branches:
                    if not forbidden and value in catalog:
                        _observe_direct_candidate(
                            field_stats,
                            (scope, branch, relative),
                            row,
                            value,
                            source_name,
                        )
                    object_link = source_objects.get(value)
                    if (
                        forbidden in {"", "known_damage_object_id"}
                        and object_link is not None
                        and object_link[1] < packet_ordinal
                        and 0.0
                        <= row.timestamp - object_link[2]
                        <= 30.0
                    ):
                        template_id = object_link[0]
                        # +13 is emitted as a positive control, but can never
                        # become a *new* variable-branch key.
                        object_key = (scope, branch, relative, template_id)
                        stat = object_stats.setdefault(object_key, _new_stat())
                        if exact:
                            stat["support"] += 1
                            stat["actors"].add(f"{source_actor_id:08X}")
                            stat["truth_skills"][truth] += 1
                        else:
                            stat["nonexact"] += 1
                            if blocking:
                                stat["blocking"] += 1
                    status_link = source_statuses.get(value)
                    if not forbidden and status_link is not None:
                        _observe_link_candidate(
                            status_stats,
                            (scope, branch, relative),
                            row,
                            status_link[0],
                            source_name,
                        )

        # A status first seen in this packet is deliberately unavailable to
        # direct rows from the same packet. That prevents same-packet
        # co-occurrence from being mistaken for an ownership link.
        for status in decode_v576_status_events(
            payload, direction, decoder.status_known_skill_ids
        ):
            if status.effect_instance_id is None:
                continue
            duration = (
                float("inf") if status.permanent else status.duration_ms / 1000.0
            )
            active_statuses[int(status.source_actor_id)][
                int(status.effect_instance_id)
            ] = (int(status.skill_id), float(packet.timestamp_seconds) + duration)
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    branches = []
    for key, count in branch_counts.items():
        branches.append(
            {
                "scope": key[0],
                "branch": key[1],
                "rows": int(count),
                "exact": int(branch_exact[key]),
                "blocking": int(branch_blocking[key]),
            }
        )
    return {
        "capture": source_name,
        "path": str(path),
        "totals": _counter_dict(totals),
        "blocking_methods": _counter_dict(methods),
        "branches": branches,
        "direct_fields": _render_stats(
            field_stats, ("scope", "branch", "relative_offset")
        ),
        "object_links": _render_stats(
            object_stats,
            ("scope", "branch", "relative_offset", "projectile_template_id"),
        ),
        "status_links": _render_stats(
            status_stats, ("scope", "branch", "relative_offset")
        ),
    }


def _merge_rows(
    reports: list[dict[str, Any]],
    field_name: str,
    key_names: tuple[str, ...],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for report in reports:
        capture = report["capture"]
        for row in report[field_name]:
            key = tuple(row[name] for name in key_names)
            merged = grouped.setdefault(
                key,
                {
                    **{name: row[name] for name in key_names},
                    "support": 0,
                    "conflict": 0,
                    "blocking": 0,
                    "nonexact": 0,
                    "actors": set(),
                    "truth_skills": Counter(),
                    "candidate_skills": Counter(),
                    "captures": set(),
                    "per_capture": {},
                    "examples": [],
                },
            )
            for name in ("support", "conflict", "blocking", "nonexact"):
                merged[name] += int(row.get(name, 0) or 0)
            merged["actors"].update(row.get("actors", ()))
            merged["truth_skills"].update(
                {int(k): int(v) for k, v in row.get("truth_skills", {}).items()}
            )
            merged["candidate_skills"].update(
                {int(k): int(v) for k, v in row.get("candidate_skills", {}).items()}
            )
            validation = int(row.get("support", 0)) + int(row.get("conflict", 0))
            if validation > 0:
                merged["captures"].add(capture)
            merged["per_capture"][capture] = {
                "support": int(row.get("support", 0)),
                "conflict": int(row.get("conflict", 0)),
                "truth_skills": row.get("truth_skills", {}),
                "actors": tuple(row.get("actors", ())),
            }
            for example in row.get("examples", ()):
                if len(merged["examples"]) < 8:
                    merged["examples"].append(example)

    rendered: list[dict[str, Any]] = []
    for merged in grouped.values():
        candidate_key = tuple(merged[name] for name in key_names)
        truth_skills: Counter[int] = merged["truth_skills"]
        inferred_skill = truth_skills.most_common(1)[0][0] if truth_skills else 0
        if mode == "object":
            # Object candidates do not directly carry a skill. Their exact
            # rows must all prove the same skill across captures and actors.
            support = int(truth_skills.get(inferred_skill, 0))
            conflict = sum(truth_skills.values()) - support
        else:
            support = int(merged["support"])
            conflict = int(merged["conflict"])

        # Candidate discovery and validation are separated before looking at
        # support/conflict counts.  A key must pass on captures which did not
        # participate in its discovery; the previous global-then-LOO loop was
        # effectively redundant once global conflicts were already zero.
        ranked_captures = sorted(
            merged["captures"],
            key=lambda capture: hashlib.sha256(
                (repr(candidate_key) + "|" + capture).encode("utf-8")
            ).digest(),
        )
        validation_count = max(2, (len(ranked_captures) + 2) // 3)
        validation_captures = set(ranked_captures[:validation_count])
        discovery_captures = set(ranked_captures[validation_count:])

        def capture_truth(captures: set[str]) -> Counter[int]:
            result: Counter[int] = Counter()
            for capture in captures:
                result.update(
                    {
                        int(k): int(v)
                        for k, v in merged["per_capture"][capture][
                            "truth_skills"
                        ].items()
                    }
                )
            return result

        discovery_truth = capture_truth(discovery_captures)
        validation_truth = capture_truth(validation_captures)
        discovery_actors = {
            actor
            for capture in discovery_captures
            for actor in merged["per_capture"][capture]["actors"]
        }
        validation_actors = {
            actor
            for capture in validation_captures
            for actor in merged["per_capture"][capture]["actors"]
        }
        if mode == "object":
            discovery_skill = (
                next(iter(discovery_truth)) if len(discovery_truth) == 1 else 0
            )
            discovery_support = int(discovery_truth.get(discovery_skill, 0))
            discovery_conflict = sum(discovery_truth.values()) - discovery_support
            validation_support = int(validation_truth.get(discovery_skill, 0))
            validation_conflict = sum(validation_truth.values()) - validation_support
        else:
            discovery_skill = 0
            discovery_support = sum(
                int(merged["per_capture"][capture]["support"])
                for capture in discovery_captures
            )
            discovery_conflict = sum(
                int(merged["per_capture"][capture]["conflict"])
                for capture in discovery_captures
            )
            validation_support = sum(
                int(merged["per_capture"][capture]["support"])
                for capture in validation_captures
            )
            validation_conflict = sum(
                int(merged["per_capture"][capture]["conflict"])
                for capture in validation_captures
            )

        relative = int(merged.get("relative_offset", 0))
        positive_control = mode == "object" and _overlaps(
            relative, 4, (KNOWN_OBJECT_RANGE,)
        )
        minimum_rows = MIN_OBJECT_EXACT_ROWS if mode == "object" else MIN_EXACT_ROWS
        minimum_validation_rows = (
            MIN_VALIDATION_OBJECT_ROWS
            if mode == "object"
            else MIN_VALIDATION_DIRECT_ROWS
        )
        minimum_skills = 1 if mode == "object" else MIN_TRUTH_SKILLS
        accepted = (
            mode != "status"
            and discovery_support >= minimum_rows
            and validation_support >= minimum_validation_rows
            and discovery_conflict == 0
            and validation_conflict == 0
            and len(discovery_captures) >= MIN_CAPTURES
            and len(validation_captures) >= 2
            and len(discovery_actors) >= MIN_ACTORS
            and len(validation_actors) >= 2
            and len(discovery_truth) >= minimum_skills
            and int(merged["nonexact"]) > 0
            and not positive_control
        )
        rendered.append(
            {
                **{
                    key: value
                    for key, value in merged.items()
                    if key
                    not in {
                        "actors",
                        "truth_skills",
                        "candidate_skills",
                        "captures",
                        "per_capture",
                    }
                },
                "support": support,
                "conflict": conflict,
                "validation_rows": support + conflict,
                "validation_captures": len(merged["captures"]),
                "validation_capture_names": sorted(merged["captures"]),
                "actor_count": len(merged["actors"]),
                "truth_skills": _counter_dict(truth_skills),
                "candidate_skills": _counter_dict(merged["candidate_skills"]),
                "inferred_skill_id": discovery_skill if mode == "object" else 0,
                "positive_control": positive_control,
                "discovery_support": discovery_support,
                "discovery_conflict": discovery_conflict,
                "discovery_capture_names": sorted(discovery_captures),
                "discovery_actor_count": len(discovery_actors),
                "validation_support": validation_support,
                "validation_conflict": validation_conflict,
                "holdout_capture_names": sorted(validation_captures),
                "holdout_actor_count": len(validation_actors),
                "loo_folds": [],
                "loo_conflicts": validation_conflict,
                "accepted": accepted,
            }
        )
    rendered.sort(
        key=lambda row: (
            not bool(row["accepted"]),
            int(row["conflict"]),
            -int(row["blocking"]),
            -int(row["support"]),
            str(row.get("scope", "")),
            int(row.get("relative_offset", 0)),
        )
    )
    return rendered


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    methods: Counter[str] = Counter()
    branch_rows: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    branch_captures: dict[tuple[str, str], set[str]] = defaultdict(set)
    for report in reports:
        totals.update({k: int(v) for k, v in report["totals"].items()})
        methods.update(
            {k: int(v) for k, v in report["blocking_methods"].items()}
        )
        for row in report["branches"]:
            key = (str(row["scope"]), str(row["branch"]))
            branch_rows[key].update(
                {
                    "rows": int(row["rows"]),
                    "exact": int(row["exact"]),
                    "blocking": int(row["blocking"]),
                }
            )
            branch_captures[key].add(report["capture"])

    direct_fields = _merge_rows(
        reports,
        "direct_fields",
        ("scope", "branch", "relative_offset"),
        mode="direct",
    )
    object_links = _merge_rows(
        reports,
        "object_links",
        ("scope", "branch", "relative_offset", "projectile_template_id"),
        mode="object",
    )
    status_links = _merge_rows(
        reports,
        "status_links",
        ("scope", "branch", "relative_offset"),
        mode="status",
    )
    accepted = (
        [{"kind": "direct_skill_field", **row} for row in direct_fields if row["accepted"]]
        + [{"kind": "registered_object_link", **row} for row in object_links if row["accepted"]]
        + [{"kind": "prior_status_instance_link", **row} for row in status_links if row["accepted"]]
    )
    accepted.sort(key=lambda row: (-int(row["blocking"]), row["kind"]))
    primary = accepted[0] if accepted else None
    branches = [
        {
            "scope": key[0],
            "branch": key[1],
            "captures": len(branch_captures[key]),
            **{name: int(value) for name, value in values.items()},
        }
        for key, values in branch_rows.items()
    ]
    branches.sort(key=lambda row: (-row["blocking"], -row["rows"], row["scope"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_signature": _analysis_signature(),
        "policy": {
            "truth": "Only direct37 fixed result-vector skill IDs at D+102/D+114, bounded before the next direct37 marker; CombatEvent matching is diagnostic only.",
            "forbidden": [
                "nearest action/timing",
                "result group",
                "damage magnitude or fingerprint",
                "arbitrary same-packet skill co-occurrence",
                "candidate windows overlapping D+102/D+114",
            ],
            "direct_field_search": f"byte-wise u32 D{SEARCH_MIN:+d}..D+{SEARCH_MAX}; semantic actor/object/damage/result fields excluded",
            "object_search": "Only an explicit registration from a strictly earlier packet for the same source actor; known D+13 is a non-integrable positive control.",
            "status_search": "Diagnostic only. Earlier-packet status instances are reported but cannot set safe_to_integrate until explicit apply/remove lifecycle closure is proved.",
            "acceptance": {
                "direct_min_exact_rows": MIN_EXACT_ROWS,
                "object_min_exact_rows": MIN_OBJECT_EXACT_ROWS,
                "holdout_direct_min_exact_rows": MIN_VALIDATION_DIRECT_ROWS,
                "holdout_object_min_exact_rows": MIN_VALIDATION_OBJECT_ROWS,
                "discovery_min_captures": MIN_CAPTURES,
                "holdout_min_captures": 2,
                "discovery_min_actors": MIN_ACTORS,
                "holdout_min_actors": 2,
                "direct_min_truth_skills": MIN_TRUTH_SKILLS,
                "discovery_conflicts": 0,
                "holdout_conflicts": 0,
                "requires_nonexact_rows": True,
                "status_candidates_integrable": False,
            },
        },
        "captures": len(reports),
        "totals": _counter_dict(totals),
        "blocking_methods": _counter_dict(methods),
        "safe_to_integrate": bool(accepted),
        "primary_candidate": primary,
        "accepted_candidates": accepted,
        "new_blocking_hits_primary": int(primary["blocking"]) if primary else 0,
        "direct_fields": direct_fields,
        "object_links": object_links,
        "status_links": status_links,
        "branches": branches,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _report(result: dict[str, Any]) -> str:
    totals = result["totals"]
    lines = [
        "# direct37 可变分支确定性归因审计",
        "",
        f"- 抓包：{result['captures']} 份",
        f"- direct37 原始记录：{int(totals.get('direct37_rows', 0)):,}",
        f"- 匹配到出站伤害：{int(totals.get('matched_outgoing_rows', 0)):,}",
        f"- D+102/D+114 精确真值：{int(totals.get('exact_rows', 0)):,}",
        f"- 当前 blocking hits：{int(totals.get('blocking_rows', 0)):,}",
        f"- 可安全接入：`{str(result['safe_to_integrate']).lower()}`",
        f"- 主候选可新增覆盖：{result['new_blocking_hits_primary']:,}",
        "",
        "## 结论",
        "",
    ]
    if result["safe_to_integrate"]:
        row = result["primary_candidate"]
        lines.append(
            f"发现通过全部门槛的 `{row['kind']}` 候选：scope={row.get('scope')}、"
            f"offset={int(row.get('relative_offset', 0)):+d}，可新增覆盖 "
            f"{int(row['blocking']):,} 条 blocking hit。"
        )
    else:
        lines.append(
            "没有候选同时满足独立发现/保留验证、跨 actor、固定槽真值零冲突及未标记行覆盖，故 `safe_to_integrate=false`。"
        )
    lines.extend(
        [
            "",
            "## 最接近的直接字段候选",
            "",
            "| scope | offset | 真值支持 | 冲突 | 抓包 | actor | 技能 | blocking | 接入 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in result["direct_fields"][:20]:
        lines.append(
            f"| {row['scope']} | {int(row['relative_offset']):+d} | {int(row['support']):,} | "
            f"{int(row['conflict']):,} | {int(row['validation_captures'])} | "
            f"{int(row['actor_count'])} | {len(row['truth_skills'])} | "
            f"{int(row['blocking']):,} | {row['accepted']} |"
        )
    lines.extend(
        [
            "",
            "## 最接近的对象连接候选",
            "",
            "| scope | offset | Projectile | 真值支持 | 冲突 | 抓包 | actor | blocking | 对照 | 接入 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in result["object_links"][:20]:
        lines.append(
            f"| {row['scope']} | {int(row['relative_offset']):+d} | "
            f"{row['projectile_template_id']} | {int(row['support']):,} | "
            f"{int(row['conflict']):,} | {int(row['validation_captures'])} | "
            f"{int(row['actor_count'])} | {int(row['blocking']):,} | "
            f"{row['positive_control']} | {row['accepted']} |"
        )
    lines.extend(
        [
            "",
            "## blocking 分支",
            "",
            "| scope | 分支 | 抓包 | 记录 | 精确 | blocking |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in result["branches"][:30]:
        lines.append(
            f"| {row['scope']} | `{row['branch']}` | {row['captures']} | "
            f"{row['rows']:,} | {row['exact']:,} | {row['blocking']:,} |"
        )
    lines.extend(
        [
            "",
            "## 审计边界",
            "",
            "报文中的 damage 数值仅用于把原始记录与当前事件做审计计数配对，不参与技能归因候选。结果组完全没有进入任何候选；任意技能样整数的同包共现也没有进入候选。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit deterministic variable-branch fields/links for skill-less direct37 rows."
    )
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--checkpoint-dir", type=Path, default=None)
    args = parser.parse_args()

    capture_root = args.capture_root.resolve()
    corpus = json.loads(args.corpus.resolve().read_text(encoding="utf-8"))
    names = [str(row["source"]) for row in corpus["files"]]
    if args.limit > 0:
        names = names[: args.limit]
    paths = [(capture_root / name, name) for name in names]
    missing = [str(path) for path, _name in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"missing captures: {missing[:10]}")

    signature_before = _analysis_signature()
    checkpoint_dir = (
        args.checkpoint_dir.resolve()
        if args.checkpoint_dir is not None
        else args.output.resolve().with_name(args.output.name + "_checkpoints")
    )
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    pending: list[tuple[Path, str]] = []
    for path, name in paths:
        checkpoint_name = hashlib.sha256(name.encode("utf-8")).hexdigest() + ".json"
        checkpoint = checkpoint_dir / checkpoint_name
        loaded = None
        if checkpoint.is_file():
            try:
                candidate = json.loads(checkpoint.read_text(encoding="utf-8"))
                if (
                    candidate.get("source") == name
                    and candidate.get("analysis_signature") == signature_before["sha256"]
                ):
                    loaded = candidate.get("report")
            except (OSError, ValueError, TypeError):
                loaded = None
        if isinstance(loaded, dict):
            reports.append(loaded)
        else:
            pending.append((path, name))
    if reports:
        print(f"[resume] loaded={len(reports)} pending={len(pending)}", flush=True)
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(audit_capture, str(path), name): name
            for path, name in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            report = future.result()
            reports.append(report)
            checkpoint_name = (
                hashlib.sha256(report["capture"].encode("utf-8")).hexdigest()
                + ".json"
            )
            (checkpoint_dir / checkpoint_name).write_text(
                json.dumps(
                    {
                        "source": report["capture"],
                        "analysis_signature": signature_before["sha256"],
                        "report": report,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            totals = report["totals"]
            print(
                f"[{len(reports)}/{len(paths)}] {report['capture']}: "
                f"direct37={totals.get('direct37_rows', 0)} "
                f"exact={totals.get('exact_rows', 0)} "
                f"blocking={totals.get('blocking_rows', 0)}",
                flush=True,
            )
    if _analysis_signature() != signature_before:
        raise SystemExit("analysis inputs changed during audit; refusing mixed output")

    reports.sort(key=lambda row: row["capture"])
    result = summarize(reports)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output.with_suffix(".md").write_text(_report(result), encoding="utf-8")
    _write_csv(
        output.with_name(output.name + "_direct_fields.csv"),
        result["direct_fields"],
        (
            "accepted", "scope", "branch", "relative_offset", "support",
            "conflict", "validation_rows", "validation_captures", "actor_count",
            "discovery_support", "discovery_conflict", "discovery_actor_count",
            "validation_support", "validation_conflict", "holdout_actor_count",
            "blocking", "nonexact", "loo_conflicts",
        ),
    )
    _write_csv(
        output.with_name(output.name + "_object_links.csv"),
        result["object_links"],
        (
            "accepted", "positive_control", "scope", "branch", "relative_offset",
            "projectile_template_id", "inferred_skill_id", "support", "conflict",
            "validation_captures", "actor_count", "discovery_support",
            "discovery_conflict", "discovery_actor_count", "validation_support",
            "validation_conflict", "holdout_actor_count", "blocking", "nonexact",
            "loo_conflicts",
        ),
    )
    _write_csv(
        output.with_name(output.name + "_branches.csv"),
        result["branches"],
        ("scope", "branch", "captures", "rows", "exact", "blocking"),
    )
    print(json.dumps({
        "safe_to_integrate": result["safe_to_integrate"],
        "new_blocking_hits_primary": result["new_blocking_hits_primary"],
        "totals": result["totals"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
