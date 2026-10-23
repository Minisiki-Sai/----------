from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


ACTION_MARKERS = (
    b"\x0e\x23\x00",
    b"\x0e\x1f\x00",
    b"\x12\x24\x00",
    b"\x12\x20\x00",
)


@dataclass(slots=True)
class WireRow:
    capture: str
    timestamp: float
    source_actor_id: int
    skill_id: int
    layout: str
    signature: str
    payload_length: int
    marker_offset: int
    body: bytes
    assigned_method: str = ""
    assigned_skill_id: int = 0
    packet_ordinal: int = 0


def _damage_body(packet: object, decoded: object) -> bytes | None:
    payload = bytes(getattr(packet, "payload", b""))
    marker_offset = int(getattr(decoded, "record_offset", 0) or 0)
    source_actor_id = int(getattr(decoded, "source_actor_id", 0) or 0)
    damage = int(getattr(decoded, "damage", 0) or 0)
    marker = b"\x08\x77\x00" if "direct41" in str(
        getattr(decoded, "layout", "")
    ) else b"\x08\x73\x00"
    damage_relative = 23 if marker == b"\x08\x77\x00" else 19
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        if (
            marker_offset >= 0
            and marker_offset + damage_relative + 8 <= len(body)
            and body[marker_offset : marker_offset + 3] == marker
            and int.from_bytes(
                body[marker_offset + 8 : marker_offset + 12], "little"
            )
            == source_actor_id
            and int.from_bytes(
                body[
                    marker_offset + damage_relative :
                    marker_offset + damage_relative + 8
                ],
                "little",
            )
            == damage
        ):
            return body
    return None


def _action_body(packet: object, decoded: object) -> tuple[bytes, int] | None:
    payload = bytes(getattr(packet, "payload", b""))
    actor_id = int(getattr(decoded, "source_actor_id", 0) or 0)
    skill_id = int(getattr(decoded, "skill_id", 0) or 0)
    if actor_id <= 0 or skill_id <= 0:
        return None
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        for marker in ACTION_MARKERS:
            offset = body.find(marker, 9)
            while offset >= 0 and offset + 7 <= len(body):
                candidate_skill = int.from_bytes(
                    body[offset + 3 : offset + 7], "little"
                )
                marker_actor = (
                    int.from_bytes(body[offset - 4 : offset], "little")
                    if offset >= 4
                    else 0
                )
                fixed_actor = int.from_bytes(body[10:14], "little")
                if candidate_skill == skill_id and actor_id in {
                    marker_actor,
                    fixed_actor,
                }:
                    return body, offset
                offset = body.find(marker, offset + 1)
    return None


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed
    actions: list[WireRow] = []
    exact_damage: list[WireRow] = []
    nonexact_damage: list[WireRow] = []
    packet_ordinal = 0

    def feed(packet, packet_features=None):
        nonlocal packet_ordinal
        packet_ordinal += 1
        event = original_feed(packet, packet_features)
        decoded = decoder.last_decoded_message
        if decoded is None:
            return event
        layout = str(getattr(decoded, "layout", ""))
        if getattr(decoded, "kind", "") in {"skill_cast", "skill_action"}:
            resolved = _action_body(packet, decoded)
            if resolved is not None:
                body, marker_offset = resolved
                actions.append(
                    WireRow(
                        capture=str(path),
                        timestamp=float(packet.timestamp_seconds),
                        source_actor_id=int(decoded.source_actor_id or 0),
                        skill_id=int(decoded.skill_id or 0),
                        layout=layout,
                        signature=bytes(packet.payload[5:9]).hex(),
                        payload_length=len(packet.payload),
                        marker_offset=marker_offset,
                        body=body,
                        packet_ordinal=packet_ordinal,
                    )
                )
        elif (
            isinstance(event, CombatEvent)
            and not event.healing
            and "direct37" in layout
        ):
            body = _damage_body(packet, decoded)
            if body is not None:
                try:
                    assigned_skill = int(event.skill_id)
                except ValueError:
                    assigned_skill = 0
                row = WireRow(
                    capture=str(path),
                    timestamp=float(packet.timestamp_seconds),
                    source_actor_id=int(decoded.source_actor_id or 0),
                    skill_id=int(decoded.skill_id or 0),
                    layout=layout,
                    signature=bytes(packet.payload[5:9]).hex(),
                    payload_length=len(packet.payload),
                    marker_offset=int(getattr(decoded, "record_offset", 0) or 0),
                    body=body,
                    assigned_method=str(event.attribution_method),
                    assigned_skill_id=assigned_skill,
                    packet_ordinal=packet_ordinal,
                )
                if bool(getattr(decoded, "skill_id_exact", False)):
                    exact_damage.append(row)
                else:
                    nonexact_damage.append(row)
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": str(path),
        "actions": actions,
        "exact_damage": exact_damage,
        "nonexact_damage": nonexact_damage,
    }


def _latest_matching_action(
    actions: list[WireRow], damage: WireRow
) -> WireRow | None:
    for action in reversed(actions):
        age = damage.timestamp - action.timestamp
        if age < 0:
            continue
        if age > 12.0:
            break
        if (
            action.source_actor_id == damage.source_actor_id
            and action.skill_id == damage.skill_id
        ):
            return action
    return None


def _u32_fields(row: WireRow, *, relative: bool) -> dict[int, int]:
    fields: dict[int, int] = {}
    for offset in range(9, len(row.body) - 3):
        relative_offset = offset - row.marker_offset if relative else offset
        value = int.from_bytes(row.body[offset : offset + 4], "little")
        if value in {
            0,
            1,
            0xFFFFFFFF,
            row.source_actor_id,
            row.skill_id,
            row.assigned_skill_id,
        }:
            continue
        # Runtime cast/result tokens and table IDs observed in v576 fit in
        # 24 bits. Larger values here are predominantly coordinates/floats or
        # unaligned byte windows such as ``00 00 00 67``.
        if (
            value <= 0xFF
            or value > 0x00FFFFFF
            or value & 0xFF == 0
        ):
            continue
        fields[relative_offset] = value
    return fields


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[tuple[WireRow, WireRow]] = []
    all_nonexact: list[WireRow] = []
    for result in results:
        actions = sorted(result["actions"], key=lambda row: row.timestamp)
        all_nonexact.extend(result["nonexact_damage"])
        for damage in result["exact_damage"]:
            action = _latest_matching_action(actions, damage)
            if action is not None:
                pairs.append((action, damage))

    pair_support: Counter[tuple[str, str, int, int]] = Counter()
    pair_values: dict[tuple[str, str, int, int], set[int]] = defaultdict(set)
    pair_skills: dict[tuple[str, str, int, int], Counter[int]] = defaultdict(Counter)
    pair_captures: dict[tuple[str, str, int, int], set[str]] = defaultdict(set)
    pair_examples: dict[tuple[str, str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for action, damage in pairs:
        action_fields = _u32_fields(action, relative=True)
        damage_fields = _u32_fields(damage, relative=True)
        action_by_value: dict[int, list[int]] = defaultdict(list)
        for offset, value in action_fields.items():
            action_by_value[value].append(offset)
        for damage_offset, value in damage_fields.items():
            for action_offset in action_by_value.get(value, ()):
                key = (
                    action.layout,
                    f"{damage.signature}:{damage.payload_length}:{damage.layout}",
                    action_offset,
                    damage_offset,
                )
                pair_support[key] += 1
                pair_values[key].add(value)
                pair_skills[key][damage.skill_id] += 1
                pair_captures[key].add(damage.capture)
                if len(pair_examples[key]) < 3:
                    pair_examples[key].append(
                        {
                            "capture": damage.capture,
                            "timestamp": damage.timestamp,
                            "skill_id": damage.skill_id,
                            "value": value,
                            "action_marker_offset": action.marker_offset,
                            "damage_marker_offset": damage.marker_offset,
                            "action_hex": action.body.hex(),
                            "damage_hex": damage.body.hex(),
                        }
                    )

    candidates: list[dict[str, Any]] = []
    for key, support in pair_support.items():
        distinct_values = len(pair_values[key])
        captures = len(pair_captures[key])
        if support < 4 or distinct_values < 3:
            continue
        action_layout, damage_layout, action_offset, damage_offset = key
        candidates.append(
            {
                "action_layout": action_layout,
                "damage_layout": damage_layout,
                "action_relative_offset": action_offset,
                "damage_relative_offset": damage_offset,
                "pair_hits": support,
                "distinct_values": distinct_values,
                "captures": captures,
                "skills": dict(pair_skills[key].most_common()),
                "sample_values": sorted(pair_values[key])[:20],
                "examples": pair_examples[key],
            }
        )
    candidates.sort(
        key=lambda row: (
            -int(row["captures"]),
            -int(row["distinct_values"]),
            -int(row["pair_hits"]),
        )
    )

    # A second search compares skill-bearing and skill-less direct37 rows
    # from the same actor/cast. Some server result families omit the skill on
    # continuation hits; a changing result-batch key shared by both forms
    # would be a stronger join than proximity to the latest action.
    result_pair_support: Counter[tuple[str, str, int, int]] = Counter()
    result_pair_values: dict[
        tuple[str, str, int, int], set[int]
    ] = defaultdict(set)
    result_pair_skills: dict[
        tuple[str, str, int, int], Counter[int]
    ] = defaultdict(Counter)
    result_pair_captures: dict[
        tuple[str, str, int, int], set[str]
    ] = defaultdict(set)
    result_pair_examples: dict[
        tuple[str, str, int, int], list[dict[str, Any]]
    ] = defaultdict(list)

    for result in results:
        exact_by_actor: dict[int, list[WireRow]] = defaultdict(list)
        for row in result["exact_damage"]:
            exact_by_actor[row.source_actor_id].append(row)
        for rows in exact_by_actor.values():
            rows.sort(key=lambda row: row.timestamp)
        for continuation in result["nonexact_damage"]:
            if continuation.assigned_skill_id <= 0:
                continue
            candidates_for_actor = exact_by_actor.get(
                continuation.source_actor_id, ()
            )
            nearby = [
                row
                for row in candidates_for_actor
                if row.skill_id == continuation.assigned_skill_id
                and abs(row.timestamp - continuation.timestamp) <= 2.0
            ]
            if not nearby:
                continue
            exact = min(
                nearby,
                key=lambda row: abs(row.timestamp - continuation.timestamp),
            )
            exact_fields = _u32_fields(exact, relative=True)
            continuation_fields = _u32_fields(continuation, relative=True)
            exact_by_value: dict[int, list[int]] = defaultdict(list)
            for offset, value in exact_fields.items():
                exact_by_value[value].append(offset)
            for continuation_offset, value in continuation_fields.items():
                for exact_offset in exact_by_value.get(value, ()):
                    key = (
                        f"{exact.signature}:{exact.payload_length}:{exact.layout}",
                        (
                            f"{continuation.signature}:"
                            f"{continuation.payload_length}:"
                            f"{continuation.layout}"
                        ),
                        exact_offset,
                        continuation_offset,
                    )
                    result_pair_support[key] += 1
                    result_pair_values[key].add(value)
                    result_pair_skills[key][exact.skill_id] += 1
                    result_pair_captures[key].add(exact.capture)
                    if len(result_pair_examples[key]) < 3:
                        result_pair_examples[key].append(
                            {
                                "capture": exact.capture,
                                "skill_id": exact.skill_id,
                                "exact_timestamp": exact.timestamp,
                                "continuation_timestamp": continuation.timestamp,
                                "delta_ms": round(
                                    (continuation.timestamp - exact.timestamp)
                                    * 1000.0,
                                    3,
                                ),
                                "value": value,
                                "exact_hex": exact.body.hex(),
                                "continuation_hex": continuation.body.hex(),
                            }
                        )

    result_candidates: list[dict[str, Any]] = []
    for key, support in result_pair_support.items():
        values = result_pair_values[key]
        if support < 4 or len(values) < 3:
            continue
        exact_layout, continuation_layout, exact_offset, continuation_offset = key
        result_candidates.append(
            {
                "exact_layout": exact_layout,
                "continuation_layout": continuation_layout,
                "exact_relative_offset": exact_offset,
                "continuation_relative_offset": continuation_offset,
                "pair_hits": support,
                "distinct_values": len(values),
                "captures": len(result_pair_captures[key]),
                "skills": dict(result_pair_skills[key].most_common()),
                "sample_values": sorted(values)[:20],
                "examples": result_pair_examples[key],
            }
        )
    result_candidates.sort(
        key=lambda row: (
            -int(row["captures"]),
            -len(row["skills"]),
            -int(row["distinct_values"]),
            -int(row["pair_hits"]),
        )
    )

    # Targeted protocol audit for the two direct37 identifiers.  ``+4`` is a
    # short-lived result-group value and ``+13`` is the damage-object value.
    # Test full and masked forms separately instead of mining arbitrary byte
    # windows; a usable key must be pure on exact rows and must generalise
    # across captures without consulting the old inferred assignment.
    key_specs = {
        "result_group_u32": (4, 0xFFFFFFFF),
        "result_group_hi24": (4, 0xFFFFFF00),
        "result_group_hi16": (4, 0xFFFF0000),
        "damage_object_u32": (13, 0xFFFFFFFF),
        "damage_object_hi24": (13, 0xFFFFFF00),
        "damage_object_hi16": (13, 0xFFFF0000),
    }
    semantic_key_rows: list[dict[str, Any]] = []
    for spec_name, (relative_offset, mask) in key_specs.items():
        exact_map: dict[tuple[str, int, int], set[int]] = defaultdict(set)
        exact_support: Counter[tuple[str, int, int]] = Counter()
        exact_occurrences: dict[
            tuple[str, int, int], list[tuple[float, int]]
        ] = defaultdict(list)
        for result in results:
            for row in result["exact_damage"]:
                offset = row.marker_offset + relative_offset
                if offset < 0 or offset + 4 > len(row.body):
                    continue
                value = int.from_bytes(row.body[offset : offset + 4], "little") & mask
                if value == 0:
                    continue
                key = (row.capture, row.source_actor_id, value)
                exact_map[key].add(row.skill_id)
                exact_support[key] += 1
                exact_occurrences[key].append((row.timestamp, row.skill_id))
        pure_map = {
            key: next(iter(skills))
            for key, skills in exact_map.items()
            if len(skills) == 1
        }
        exact_conflicting_keys = sum(len(skills) > 1 for skills in exact_map.values())
        recoverable = same = changed = ambiguous = 0
        recovered_skills: Counter[int] = Counter()
        captures_recovered: set[str] = set()
        examples: list[dict[str, Any]] = []
        for result in results:
            for row in result["nonexact_damage"]:
                offset = row.marker_offset + relative_offset
                if offset < 0 or offset + 4 > len(row.body):
                    continue
                value = int.from_bytes(row.body[offset : offset + 4], "little") & mask
                if value == 0:
                    continue
                key = (row.capture, row.source_actor_id, value)
                skills = exact_map.get(key)
                if not skills:
                    continue
                if len(skills) != 1:
                    ambiguous += 1
                    continue
                skill_id = pure_map[key]
                recoverable += 1
                recovered_skills[skill_id] += 1
                captures_recovered.add(row.capture)
                if skill_id == row.assigned_skill_id:
                    same += 1
                else:
                    changed += 1
                    if len(examples) < 20:
                        examples.append(
                            {
                                "capture": row.capture,
                                "timestamp": row.timestamp,
                                "source_actor_id": row.source_actor_id,
                                "key": value,
                                "recovered_skill_id": skill_id,
                                "old_skill_id": row.assigned_skill_id,
                                "old_method": row.assigned_method,
                                "layout": row.layout,
                            }
                        )
        semantic_key_rows.append(
            {
                "key": spec_name,
                "relative_offset": relative_offset,
                "mask": f"0x{mask:08X}",
                "exact_keys": len(exact_map),
                "exact_pure_keys": len(pure_map),
                "exact_conflicting_keys": exact_conflicting_keys,
                "recoverable_nonexact": recoverable,
                "same_as_old": same,
                "changes_old": changed,
                "ambiguous_nonexact": ambiguous,
                "captures_recovered": len(captures_recovered),
                "recovered_skills": dict(recovered_skills.most_common()),
                "change_examples": examples,
                "temporal_windows": [],
            }
        )
        temporal_rows = semantic_key_rows[-1]["temporal_windows"]
        for window_ms in (1, 5, 20, 100, 500):
            temporal_recoverable = temporal_same = temporal_changed = 0
            temporal_ambiguous = 0
            temporal_skills: Counter[int] = Counter()
            temporal_captures: set[str] = set()
            for result in results:
                for row in result["nonexact_damage"]:
                    offset = row.marker_offset + relative_offset
                    if offset < 0 or offset + 4 > len(row.body):
                        continue
                    value = int.from_bytes(
                        row.body[offset : offset + 4], "little"
                    ) & mask
                    if value == 0:
                        continue
                    key = (row.capture, row.source_actor_id, value)
                    nearby = [
                        (timestamp, skill_id)
                        for timestamp, skill_id in exact_occurrences.get(key, ())
                        if abs(timestamp - row.timestamp) * 1000.0 <= window_ms
                    ]
                    nearby_skills = {skill_id for _timestamp, skill_id in nearby}
                    if not nearby_skills:
                        continue
                    if len(nearby_skills) != 1:
                        temporal_ambiguous += 1
                        continue
                    skill_id = next(iter(nearby_skills))
                    temporal_recoverable += 1
                    temporal_skills[skill_id] += 1
                    temporal_captures.add(row.capture)
                    if skill_id == row.assigned_skill_id:
                        temporal_same += 1
                    else:
                        temporal_changed += 1
            temporal_rows.append(
                {
                    "window_ms": window_ms,
                    "recoverable_nonexact": temporal_recoverable,
                    "same_as_old": temporal_same,
                    "changes_old": temporal_changed,
                    "ambiguous_nonexact": temporal_ambiguous,
                    "captures_recovered": len(temporal_captures),
                    "skills": dict(temporal_skills.most_common()),
                }
            )

    return {
        "captures": [result["capture"] for result in results],
        "actions": sum(len(result["actions"]) for result in results),
        "exact_direct37": sum(
            len(result["exact_damage"]) for result in results
        ),
        "nonexact_direct37": len(all_nonexact),
        "exact_rows_with_preceding_same_skill_action": len(pairs),
        "candidate_offset_pairs": candidates[:1000],
        "result_continuation_offset_pairs": result_candidates[:1000],
        "semantic_direct37_keys": semantic_key_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find a varying cast-instance key shared by action and direct37 rows."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(
        [audit_capture(path.resolve()) for path in args.captures]
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
