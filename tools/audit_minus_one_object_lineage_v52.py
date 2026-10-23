"""Audit skill-less native Hit rows through exact damage-object lineage.

This is an offline acceptance tool.  It never relabels a CombatEvent and does
not modify the production decoder.  The target is deliberately narrow:

* one of the eleven professions below 95% in the frozen v49 corpus;
* a named but non-deterministic physical Hit;
* the client's primary result skill key (+0x90) is read and equals -1;
* the physical Hit carries a positive damage_object_id.

For every exact ``(wire source actor, damage object)`` pair, the tool searches
backwards for the first complete object record.  A bare object-id occurrence,
time proximity, and the currently displayed skill name are never truth.  A
candidate record layout is accepted only when independent packet/object/entity
anchors resolve the same scoped layout to one canonical skill with zero
conflicts and sufficient cross-capture support.  Existing v50 +0xA4 acceptance
is evaluated separately so native fallback coverage is not double-counted as
new object-lineage coverage.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    AttributionLayer,
    attribution_layer,
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_damage_skill_ids,
    _load_skill_catalog,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_damage_object_registration,
    decode_v576_server_damage_object_registrations,
    decode_v576_status_events,
    decode_v576_structured_actions,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_magic_archer_projectile_links import (  # noqa: E402
    _iter_logical_packets,
)
from tools.audit_native_meter_skill_key_v50 import (  # noqa: E402
    DEFAULT_CAPTURE_ROOT,
    DEFAULT_SELECTION,
    _capture_selection,
    _find_captures,
    _native_result_fields,
)


SCHEMA = "minus-one-object-lineage-zero-conflict-v52"
UNDER_95_JOBS = frozenset(
    {
        "魔羽",
        "圣骑士",
        "十字军",
        "圣徒",
        "雷神",
        "重炮手",
        "机械大师",
        "药剂师",
        "黑暗萨满",
        "噬魂者",
        "影",
    }
)
EMPTY_NATIVE_VALUES = frozenset({0, 0xFFFFFFFF})
DEFAULT_NATIVE_ACCEPTANCE = (
    ROOT / "outputs/native_wire_v50_20260909/native_skill_key_acceptance_v50.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "outputs/native_wire_v52_20260909"
DEFAULT_JSON = DEFAULT_OUTPUT_DIR / "minus_one_object_lineage_acceptance_v52.json"
DEFAULT_MD = DEFAULT_OUTPUT_DIR / "minus_one_object_lineage_acceptance_v52.md"
MIN_HITS = 8
MIN_CAPTURES = 2
MIN_PAIRS = 2
MIN_OWNERS = 2


def _source_class(actor_id: int) -> str:
    if 0 < actor_id <= 0x00FFFFFF:
        return "entity"
    if 0x00FFFFFF < actor_id < 0xFFFFFFFF:
        return "player"
    return "invalid"


def _integer(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _pair_text(pair: tuple[int, int]) -> str:
    return f"0x{pair[0]:08X}:{pair[1]}"


def _position_before(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left < right


def _truth_method(method: str) -> bool:
    if method == "packet_skill_id":
        return True
    return bool(
        is_deterministic_attribution(method)
        and attribution_layer(method) in {AttributionLayer.OBJECT, AttributionLayer.ENTITY}
    )


def _physical_rows(packet: object) -> tuple[dict[tuple[Any, ...], list[object]], str]:
    inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
    if not inventory.complete:
        return {}, str(inventory.rejection_reason or "incomplete")
    rows: dict[tuple[Any, ...], list[object]] = defaultdict(list)
    for row in inventory.damage_records:
        if not row.admissible_fields:
            continue
        rows[
            (
                str(row.family),
                int(row.record_offset),
                int(row.source_actor_id),
                int(row.target_actor_id),
                int(row.damage),
                bool(row.critical),
            )
        ].append(row)
    return rows, ""


def _join_physical(event: CombatEvent, rows: dict[tuple[Any, ...], list[object]]):
    family = "direct41" if "direct41" in event.wire_damage_layout else "direct37"
    key = (
        family,
        int(event.wire_record_offset),
        int(event.wire_source_actor_id),
        int(event.wire_target_actor_id),
        int(event.amount),
        bool(event.wire_critical),
    )
    matches = rows.get(key, ())
    return matches[0] if len(matches) == 1 else None


def _new_pair(pair: tuple[int, int]) -> dict[str, Any]:
    return {
        "owner_actor_id": pair[0],
        "damage_object_id": pair[1],
        "first_hit_position": None,
        "jobs": Counter(),
        "source_shapes": Counter(),
        "target_hits": 0,
        "target_damage": 0,
        "target_current_skills": Counter(),
        "target_methods": Counter(),
        "target_native_shapes": Counter(),
        "anchor_hits": 0,
        "anchor_skills": Counter(),
        "anchor_methods": Counter(),
        # Kept only inside one worker.  _audit_capture reduces these physical
        # rows into registration generations before returning its report.
        "_hits": [],
    }


def _serialise_pair(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if key != "_hits"}
    for key in (
        "jobs",
        "source_shapes",
        "target_current_skills",
        "target_methods",
        "target_native_shapes",
        "anchor_skills",
        "anchor_methods",
    ):
        result[key] = dict(row[key])
    if result["first_hit_position"] is not None:
        result["first_hit_position"] = list(result["first_hit_position"])
    return result


def _collect_hit_pairs(path: Path) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[str, int]]:
    """Replay once, keeping only target and independent anchor pair summaries."""

    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    pair_rows: dict[tuple[int, int], dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        events = original(packet)
        physical_rows, reason = _physical_rows(packet)
        if reason:
            counts[f"physical_inventory_rejected:{reason}"] += 1
            return ()
        for event in events:
            if (
                not isinstance(event, CombatEvent)
                or event.healing
                or event.amount <= 0
                or event.job not in UNDER_95_JOBS
                or event.wire_record_offset <= 0
                or not (
                    "direct37" in event.wire_damage_layout
                    or "direct41" in event.wire_damage_layout
                )
            ):
                continue
            physical = _join_physical(event, physical_rows)
            if physical is None:
                counts["physical_join_failure"] += 1
                continue
            owner = int(physical.source_actor_id or 0)
            object_id = int(physical.damage_object_id or 0)
            if owner <= 0 or not 0 < object_id < 0xFFFFFFFF:
                continue
            assigned = _integer(event.skill_id)
            method = str(event.attribution_method or "")
            native = _native_result_fields(physical)
            source_class = _source_class(owner)
            position = (packet_index, int(physical.record_offset))
            is_target = bool(
                assigned > 0
                and not is_deterministic_attribution(method)
                and native["primary_read"]
                and int(native["primary_raw"]) == 0xFFFFFFFF
            )
            is_anchor = bool(assigned > 0 and _truth_method(method))
            if not is_target and not is_anchor:
                continue
            pair = (owner, object_id)
            row = pair_rows.setdefault(pair, _new_pair(pair))
            first = row["first_hit_position"]
            if first is None or position < first:
                row["first_hit_position"] = position
            row["jobs"][str(event.job)] += 1
            shape = "|".join(
                (
                    str(event.job),
                    str(physical.family),
                    str(native["source_kind"]),
                    source_class,
                )
            )
            row["source_shapes"][shape] += 1
            if is_target:
                row["target_hits"] += 1
                row["target_damage"] += int(event.amount)
                row["target_current_skills"][
                    f"{assigned}|{event.skill_name}"
                ] += 1
                row["target_methods"][method] += 1
                row["target_native_shapes"][
                    "|".join(
                        (
                            str(physical.family),
                            str(native["source_kind"]),
                            source_class,
                            str(event.job),
                            str(native["fallback_raw"]),
                        )
                    )
                ] += 1
            if is_anchor:
                row["anchor_hits"] += 1
                row["anchor_skills"][str(assigned)] += 1
                row["anchor_methods"][method] += 1
            row["_hits"].append(
                {
                    "position": position,
                    "job": str(event.job),
                    "family": str(physical.family),
                    "source_kind": int(native["source_kind"]),
                    "source_class": source_class,
                    "assigned_skill": assigned,
                    "skill_name": str(event.skill_name),
                    "method": method,
                    "amount": int(event.amount),
                    "is_target": is_target,
                    "is_anchor": is_anchor,
                    "fallback_raw": int(native["fallback_raw"]),
                }
            )
        # The decoder has already updated its state.  Avoid retaining a second
        # full event corpus in the replay source.
        return ()

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    counts["logical_packets"] = packet_index
    counts["pair_rows"] = len(pair_rows)
    counts["target_pairs"] = sum(row["target_hits"] > 0 for row in pair_rows.values())
    counts["target_hits"] = sum(int(row["target_hits"]) for row in pair_rows.values())
    counts["target_damage"] = sum(int(row["target_damage"]) for row in pair_rows.values())
    counts["anchor_pairs"] = sum(row["anchor_hits"] > 0 for row in pair_rows.values())
    return pair_rows, dict(counts)


def _framed_paths(body: bytes) -> list[tuple[int, list[tuple[int, int, int, int]]]]:
    """Return exact-closing [owner:u32, opcode:u8, len:u16, payload] walks."""

    paths: list[tuple[int, list[tuple[int, int, int, int]]]] = []
    for start in range(9, min(33, len(body))):
        position = start
        records: list[tuple[int, int, int, int]] = []
        while position + 7 <= len(body) and len(records) < 1024:
            owner = int.from_bytes(body[position : position + 4], "little")
            opcode = int(body[position + 4])
            length = int.from_bytes(body[position + 5 : position + 7], "little")
            end = position + 7 + length
            if length <= 0 or end > len(body):
                break
            records.append((position, end, owner, opcode))
            position = end
        if records and position == len(body):
            paths.append((start, records))
    return paths


def _template_candidates() -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for spec in _DAMAGE_OBJECT_SPECS:
        template = int(spec.projectile_template_id)
        canonical = int(spec.canonical_skill_id)
        if template > 0 and canonical > 0:
            result[template].add(canonical)
    return result


def _observe(
    observations: dict[tuple[int, int], list[dict[str, Any]]],
    pair_rows: dict[tuple[int, int], dict[str, Any]],
    pair: tuple[int, int],
    row: dict[str, Any],
) -> bool:
    position = (int(row["packet_index"]), int(row["record_offset"]))
    identity = (
        position,
        str(row["layout"]),
        int(row.get("template_id", 0)),
        bool(row.get("production_parsed", False)),
    )
    existing = observations[pair]
    if any(
        (
            (int(item["packet_index"]), int(item["record_offset"])),
            str(item["layout"]),
            int(item.get("template_id", 0)),
            bool(item.get("production_parsed", False)),
        )
        == identity
        for item in existing
    ):
        return False
    existing.append(row)
    existing.sort(
        key=lambda item: (
            int(item["packet_index"]),
            int(item["record_offset"]),
            0 if item.get("production_parsed") else 1,
            str(item["layout"]),
        )
    )
    return True


def _scan_pair_records(
    path: Path,
    pair_rows: dict[tuple[int, int], dict[str, Any]],
) -> tuple[dict[tuple[int, int], list[dict[str, Any]]], dict[tuple[int, int], dict[str, int]], int]:
    """Search complete packet bodies for exact owner+object structural records."""

    all_pairs = set(pair_rows)
    target_pairs = {
        pair for pair, row in pair_rows.items() if int(row["target_hits"]) > 0
    }
    owner_to_objects: dict[int, set[int]] = defaultdict(set)
    object_to_pairs: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for pair in all_pairs:
        owner_to_objects[pair[0]].add(pair[1])
        object_to_pairs[pair[1]].add(pair)
    object_ids = frozenset(object_to_pairs)
    known_skills = _load_damage_skill_ids(str(DATA_DIR))
    static_templates = _template_candidates()
    observations: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    diagnostics: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    packet_count = 0

    for packet_count, packet in enumerate(_iter_logical_packets(path), start=1):
        payload = bytes(packet.payload)
        direction_value = packet.inferred_direction()
        direction = str(direction_value or "unknown")
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        signature = payload[5:9].hex() if len(payload) >= 9 else ""

        production: dict[tuple[int, int, int, int], str] = {}
        local = decode_v576_damage_object_registration(payload, direction_value)
        if local is not None:
            production[
                (
                    int(local.source_actor_id),
                    int(local.damage_object_id),
                    int(local.projectile_template_id),
                    int(local.record_offset),
                )
            ] = str(local.layout)
        for server in decode_v576_server_damage_object_registrations(
            payload, direction_value, include_unmapped=True
        ):
            production[
                (
                    int(server.source_actor_id),
                    int(server.damage_object_id),
                    int(server.projectile_template_id),
                    int(server.record_offset),
                )
            ] = str(server.layout)

        touched_in_packet: set[tuple[int, int]] = set()
        phase_bodies: list[tuple[int, bytes]] = []
        seen_bodies: set[bytes] = set()
        for phase in _v576_candidate_xor_phases(payload):
            body = _decode_v576_body(payload, phase)
            if body in seen_bodies:
                continue
            seen_bodies.add(body)
            phase_bodies.append((int(phase), body))

        for phase, body in phase_bodies:
            structural_positions: set[tuple[tuple[int, int], int]] = set()

            marker_offset = body.find(b"\x09", 9)
            while marker_offset >= 0:
                if marker_offset >= 4 and marker_offset + 48 <= len(body):
                    declared = int.from_bytes(
                        body[marker_offset + 1 : marker_offset + 3], "little"
                    )
                    record_end = marker_offset + 3 + declared
                    owner = int.from_bytes(
                        body[marker_offset - 4 : marker_offset], "little"
                    )
                    object_id = int.from_bytes(
                        body[marker_offset + 8 : marker_offset + 12], "little"
                    )
                    pair = (owner, object_id)
                    repeated_owner = int.from_bytes(
                        body[marker_offset + 44 : marker_offset + 48], "little"
                    )
                    if pair in all_pairs:
                        diagnostics[pair]["strict09_pair_occurrences"] += 1
                        reasons = []
                        if record_end > len(body):
                            reasons.append("record_truncated")
                        if body[marker_offset + 3 : marker_offset + 8] != b"\0" * 5:
                            reasons.append("zero_guard_mismatch")
                        if repeated_owner != owner:
                            reasons.append("owner_repeat_mismatch")
                        if not 0 < object_id <= 0x00FFFFFF:
                            reasons.append("object_out_of_range")
                        template = int.from_bytes(
                            body[marker_offset + 12 : marker_offset + 16], "little"
                        )
                        if not 0 < template <= 0x7FFFFFFF:
                            reasons.append("template_out_of_range")
                        if reasons:
                            for reason in reasons:
                                diagnostics[pair][f"strict09_rejected:{reason}"] += 1
                        else:
                            parsed_layout = production.get(
                                (owner, object_id, template, marker_offset), ""
                            )
                            marker = body[
                                marker_offset : marker_offset + 3
                            ].hex(" ").upper()
                            layout = (
                                f"strict09|{direction}|{marker}|len={declared}|"
                                "owner=-4|object=+8|template=+12|owner2=+44"
                            )
                            context_start = max(0, marker_offset - 4)
                            context_stop = min(len(body), record_end, marker_offset + 64)
                            accepted = _observe(
                                observations,
                                pair_rows,
                                pair,
                                {
                                    "packet_index": packet_count,
                                    "record_offset": marker_offset,
                                    "timestamp_ms": timestamp_ms,
                                    "direction": direction,
                                    "signature": signature,
                                    "xor_phase": phase,
                                    "marker": marker,
                                    "layout": layout,
                                    "record_length": declared,
                                    "template_id": template,
                                    "static_skill_candidates": sorted(
                                        static_templates.get(template, ())
                                    ),
                                    "production_parsed": bool(parsed_layout),
                                    "production_layout": parsed_layout,
                                    "context_hex": body[
                                        context_start:context_stop
                                    ].hex(" "),
                                },
                            )
                            if accepted:
                                touched_in_packet.add(pair)
                                structural_positions.add((pair, marker_offset + 8))
                marker_offset = body.find(b"\x09", marker_offset + 1)

            paths = _framed_paths(body)
            path_count = len(paths)
            for path_start, records in paths:
                for start, end, owner, opcode in records:
                    if owner not in owner_to_objects:
                        continue
                    declared = end - start - 7
                    if opcode == 8 and declared in {0x73, 0x77}:
                        continue
                    marker = start + 4
                    record = body[start:end]
                    # Scan the bounded record once.  Iterating every object
                    # ever used by this owner makes long raids quadratic.
                    object_offsets: dict[int, list[int]] = defaultdict(list)
                    owned = owner_to_objects[owner]
                    for relative in range(7, max(7, len(record) - 3)):
                        candidate = int.from_bytes(
                            record[relative : relative + 4], "little"
                        )
                        if candidate in owned:
                            object_offsets[candidate].append(relative)
                    for object_id, offsets in object_offsets.items():
                        pair = (owner, object_id)
                        diagnostics[pair]["closed_tlv_pair_occurrences"] += 1
                        if path_count != 1:
                            diagnostics[pair]["closed_tlv_ambiguous_walk"] += 1
                            continue
                        if len(offsets) != 1:
                            diagnostics[pair]["closed_tlv_ambiguous_object_slot"] += 1
                            continue
                        absolute_object = start + offsets[0]
                        if (pair, absolute_object) in structural_positions:
                            continue
                        object_relative = absolute_object - marker
                        layout = (
                            f"closed_tlv|{direction}|op={opcode:02X}|len={declared}|"
                            f"owner=-4|object={object_relative:+d}"
                        )
                        accepted = _observe(
                            observations,
                            pair_rows,
                            pair,
                            {
                                "packet_index": packet_count,
                                "record_offset": marker,
                                "timestamp_ms": timestamp_ms,
                                "direction": direction,
                                "signature": signature,
                                "xor_phase": phase,
                                "marker": f"{opcode:02X} {declared & 255:02X} {declared >> 8:02X}",
                                "layout": layout,
                                "record_length": declared,
                                "template_id": 0,
                                "static_skill_candidates": [],
                                "production_parsed": False,
                                "production_layout": "",
                                "context_hex": record[:64].hex(" "),
                            },
                        )
                        if accepted:
                            touched_in_packet.add(pair)
                            structural_positions.add((pair, absolute_object))

            # Bare occurrences are diagnostics only.  They measure how much
            # evidence would have been fabricated by a global u32 search.
            seen_bare: set[tuple[tuple[int, int], int]] = set()
            for offset in range(0, max(0, len(body) - 3)):
                object_id = int.from_bytes(body[offset : offset + 4], "little")
                if object_id not in object_ids:
                    continue
                for pair in object_to_pairs[object_id]:
                    if pair not in target_pairs:
                        continue
                    first_hit = tuple(pair_rows[pair]["first_hit_position"])
                    if not _position_before((packet_count, offset), first_hit):
                        continue
                    if pair[0].to_bytes(4, "little") not in body:
                        continue
                    identity = (pair, offset)
                    if identity in seen_bare:
                        continue
                    seen_bare.add(identity)
                    diagnostics[pair]["bare_object_and_owner_same_body"] += 1
                    if identity not in structural_positions:
                        diagnostics[pair]["bare_not_structurally_bound"] += 1

        if touched_in_packet:
            actions: dict[int, set[int]] = defaultdict(set)
            statuses: dict[int, set[int]] = defaultdict(set)
            for decoded in decode_v576_structured_actions(
                payload,
                direction_value,
                known_skills,
                (),
                allow_unknown_actor_ids=True,
            ):
                actor = int(decoded.source_actor_id or 0)
                skill = int(decoded.skill_id or 0)
                if actor > 0 and skill > 0:
                    actions[actor].add(skill)
            for decoded in decode_v576_status_events(
                payload, direction_value, known_skills
            ):
                actor = int(decoded.source_actor_id or 0)
                skill = int(decoded.skill_id or 0)
                if actor > 0 and skill > 0:
                    statuses[actor].add(skill)
            for pair in touched_in_packet:
                for row in observations[pair]:
                    if int(row["packet_index"]) != packet_count:
                        continue
                    row["same_packet_action_skills"] = sorted(actions.get(pair[0], ()))
                    row["same_packet_status_skills"] = sorted(statuses.get(pair[0], ()))

    return observations, {pair: dict(row) for pair, row in diagnostics.items()}, packet_count


def _audit_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    pair_rows, replay_counts = _collect_hit_pairs(path)
    observations, diagnostics, scan_packets = _scan_pair_records(path, pair_rows)
    instances: list[dict[str, Any]] = []
    for pair, data in pair_rows.items():
        if not data["target_hits"] and not data["anchor_hits"]:
            continue
        pair_observations = sorted(
            observations.get(pair, ()),
            key=lambda row: (
                int(row["packet_index"]),
                int(row["record_offset"]),
                0 if row.get("production_parsed") else 1,
                str(row["layout"]),
            ),
        )
        positions = sorted(
            {
                (int(row["packet_index"]), int(row["record_offset"]))
                for row in pair_observations
            }
        )
        observations_by_position: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for observation in pair_observations:
            observations_by_position[
                (int(observation["packet_index"]), int(observation["record_offset"]))
            ].append(observation)
        generation_hits: dict[tuple[int, int] | None, list[dict[str, Any]]] = defaultdict(list)
        for hit in sorted(data["_hits"], key=lambda row: tuple(row["position"])):
            position = tuple(hit["position"])
            index = bisect.bisect_left(positions, position) - 1
            generation = positions[index] if index >= 0 else None
            generation_hits[generation].append(hit)

        for generation, hits in generation_hits.items():
            target_hits = [row for row in hits if row["is_target"]]
            anchor_hits = [row for row in hits if row["is_anchor"]]
            if not target_hits and not anchor_hits:
                continue
            generation_observations = (
                list(observations_by_position[generation]) if generation is not None else []
            )
            # Multiple decoded phases/layouts at one byte position are not a
            # unique instance record.  Prefer one production-decoded record;
            # otherwise keep all alternatives and let acceptance reject the
            # ambiguous generation rather than silently selecting one.
            production_rows = [
                row for row in generation_observations if row.get("production_parsed")
            ]
            if len(production_rows) == 1:
                generation_observations = production_rows
            instance = {
                "pair": _pair_text(pair),
                "owner_actor_id": pair[0],
                "damage_object_id": pair[1],
                "generation_position": list(generation) if generation is not None else None,
                "jobs": dict(Counter(row["job"] for row in hits)),
                "source_shapes": dict(
                    Counter(
                        "|".join(
                            (
                                row["job"],
                                row["family"],
                                str(row["source_kind"]),
                                row["source_class"],
                            )
                        )
                        for row in hits
                    )
                ),
                "target_hits": len(target_hits),
                "target_damage": sum(int(row["amount"]) for row in target_hits),
                "target_current_skills": dict(
                    Counter(
                        f"{row['assigned_skill']}|{row['skill_name']}" for row in target_hits
                    )
                ),
                "target_methods": dict(Counter(row["method"] for row in target_hits)),
                "target_native_shapes": dict(
                    Counter(
                        "|".join(
                            (
                                row["family"],
                                str(row["source_kind"]),
                                row["source_class"],
                                row["job"],
                                str(row["fallback_raw"]),
                            )
                        )
                        for row in target_hits
                    )
                ),
                "anchor_hits": len(anchor_hits),
                "anchor_skills": dict(
                    Counter(str(row["assigned_skill"]) for row in anchor_hits)
                ),
                "anchor_methods": dict(Counter(row["method"] for row in anchor_hits)),
                "observations": generation_observations,
                "ambiguous_generation_record": len(generation_observations) != 1,
                "diagnostics": diagnostics.get(pair, {}),
            }
            instances.append(instance)
    instances.sort(
        key=lambda row: (
            row["generation_position"] or [1 << 60, 0],
            row["owner_actor_id"],
            row["damage_object_id"],
        )
    )
    return {
        "capture": path.name,
        "capture_path": str(path),
        "capture_sha256": sha256(path.read_bytes()).hexdigest(),
        "replay_counts": replay_counts,
        "scan_logical_packets": scan_packets,
        "instances": instances,
    }


def _counter_values(counter: Counter[str]) -> dict[str, int]:
    return {key: int(value) for key, value in sorted(counter.items())}


def _scope_for(pair: dict[str, Any], observation: dict[str, Any]) -> tuple[str, ...] | None:
    shapes = Counter({str(k): int(v) for k, v in pair["source_shapes"].items()})
    if not shapes:
        return None
    shape = shapes.most_common(1)[0][0]
    job, family, source_kind, source_class = shape.split("|", 3)
    return (
        job,
        family,
        source_kind,
        source_class,
        str(observation["layout"]),
        str(int(observation.get("template_id", 0))),
    )


def _accepted_native_fallbacks(path: Path) -> set[tuple[str, str, int, str, str, int, int]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    accepted: set[tuple[str, str, int, str, str, int, int]] = set()
    for row in value.get("accepted_mappings", ()):
        if row.get("field") != "fallbackA4":
            continue
        accepted.add(
            (
                str(row["family"]),
                str(row["job"]),
                int(row["source_kind"]),
                str(row["source_class"]),
                "fallbackA4",
                int(row["raw_key"]),
                int(row["canonical_skill"]),
            )
        )
    return accepted


def _evaluate(
    captures: list[dict[str, Any]],
    native_acceptance: Path,
    *,
    min_hits: int,
    min_captures: int,
    min_pairs: int,
    min_owners: int,
) -> dict[str, Any]:
    catalog = _load_skill_catalog(str(DATA_DIR))
    native = _accepted_native_fallbacks(native_acceptance)
    evidence: dict[tuple[str, ...], dict[str, Any]] = {}
    target_instances: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()

    for capture in captures:
        capture_hash = str(capture["capture_sha256"])
        totals.update(
            {
                key: int(value)
                for key, value in capture["replay_counts"].items()
                if not key.startswith("physical_inventory_rejected:")
            }
        )
        for pair in capture["instances"]:
            first = list(pair.get("observations", ()))
            anchor_skills = {
                int(key) for key, value in pair["anchor_skills"].items() if int(value) > 0
            }
            for observation in first:
                scope = _scope_for(pair, observation)
                if scope is None:
                    continue
                row = evidence.setdefault(
                    scope,
                    {
                        "scope": scope,
                        "anchor_hits": 0,
                        "anchor_skills": Counter(),
                        "anchor_methods": Counter(),
                        "captures": set(),
                        "pairs": set(),
                        "owners": set(),
                        "static_skill_candidates": set(),
                        "production_parsed": False,
                        "action_skills": Counter(),
                        "status_skills": Counter(),
                        "examples": [],
                    },
                )
                row["static_skill_candidates"].update(
                    int(value) for value in observation.get("static_skill_candidates", ())
                )
                row["production_parsed"] = bool(
                    row["production_parsed"] or observation.get("production_parsed")
                )
                for skill in observation.get("same_packet_action_skills", ()):
                    row["action_skills"][str(skill)] += 1
                for skill in observation.get("same_packet_status_skills", ()):
                    row["status_skills"][str(skill)] += 1
                if anchor_skills:
                    row["anchor_hits"] += int(pair["anchor_hits"])
                    for skill, count in pair["anchor_skills"].items():
                        row["anchor_skills"][str(skill)] += int(count)
                    row["anchor_methods"].update(
                        {str(k): int(v) for k, v in pair["anchor_methods"].items()}
                    )
                    row["captures"].add(capture_hash)
                    row["pairs"].add((capture_hash, str(pair["pair"])))
                    row["owners"].add((capture_hash, int(pair["owner_actor_id"])))
                if len(row["examples"]) < 8:
                    row["examples"].append(
                        {
                            "capture": capture["capture"],
                            "pair": pair["pair"],
                            "target_hits": int(pair["target_hits"]),
                            "anchor_skills": sorted(anchor_skills),
                            "current_display_skills": pair["target_current_skills"],
                            "observation": observation,
                        }
                    )

            if int(pair["target_hits"]) <= 0:
                continue
            fallback_accepted: Counter[str] = Counter()
            for shape, count in pair["target_native_shapes"].items():
                family, source_kind, source_class, job, raw = shape.split("|", 4)
                matching = [
                    item
                    for item in native
                    if item[:6]
                    == (
                        family,
                        job,
                        int(source_kind),
                        source_class,
                        "fallbackA4",
                        int(raw),
                    )
                ]
                for item in matching:
                    fallback_accepted[str(item[6])] += int(count)
            target_instances.append(
                {
                    "capture": capture["capture"],
                    "capture_sha256": capture_hash,
                    "pair": pair["pair"],
                    "owner_actor_id": int(pair["owner_actor_id"]),
                    "damage_object_id": int(pair["damage_object_id"]),
                    "target_hits": int(pair["target_hits"]),
                    "target_damage": int(pair["target_damage"]),
                    "jobs": pair["jobs"],
                    "source_shapes": pair["source_shapes"],
                    "current_display_skills": pair["target_current_skills"],
                    "methods": pair["target_methods"],
                    "native_fallback_accepted_skills": dict(fallback_accepted),
                    "first_observations": first,
                    "ambiguous_generation_record": bool(
                        pair.get("ambiguous_generation_record")
                    ),
                    "diagnostics": pair.get("diagnostics", {}),
                }
            )

    accepted: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    decisions: dict[tuple[str, ...], dict[str, Any]] = {}
    for scope, raw in sorted(evidence.items()):
        anchor_skills = {int(key) for key in raw["anchor_skills"]}
        static_skills = set(raw["static_skill_candidates"])
        reasons: list[str] = []
        support: list[str] = []
        if len(anchor_skills) > 1:
            reasons.append("同一完整适用域被独立真值锚定到多个 canonical skill")
        if len(static_skills) > 1:
            reasons.append("同一 Projectile template 在静态链中对应多个 canonical skill")
        if anchor_skills and static_skills and anchor_skills != static_skills:
            reasons.append("抓包独立真值与客户端静态 Projectile 链冲突")
        canonical_candidates = anchor_skills or static_skills
        if not anchor_skills:
            support.append("没有独立 packet/object/entity 真值")
        if len(canonical_candidates) != 1:
            support.append("无法得到唯一 canonical skill")
        if int(raw["anchor_hits"]) < min_hits:
            support.append(f"独立真值 Hit<{min_hits}")
        if len(raw["captures"]) < min_captures:
            support.append(f"独立抓包<{min_captures}")
        if len(raw["pairs"]) < min_pairs:
            support.append(f"独立 owner+object 实例<{min_pairs}")
        if len(raw["owners"]) < min_owners:
            support.append(f"独立来源实例<{min_owners}")
        canonical = next(iter(canonical_candidates)) if len(canonical_candidates) == 1 else 0
        skill_row = catalog.get(canonical)
        result = {
            "job": scope[0],
            "family": scope[1],
            "source_kind": int(scope[2]),
            "source_class": scope[3],
            "layout": scope[4],
            "template_id": int(scope[5]),
            "canonical_skill": canonical,
            "skill_name": str(skill_row[0]) if skill_row else "",
            "anchor_hits": int(raw["anchor_hits"]),
            "anchor_skills": _counter_values(raw["anchor_skills"]),
            "anchor_methods": _counter_values(raw["anchor_methods"]),
            "capture_count": len(raw["captures"]),
            "pair_count": len(raw["pairs"]),
            "owner_count": len(raw["owners"]),
            "static_skill_candidates": sorted(static_skills),
            "production_parser_present": bool(raw["production_parsed"]),
            "same_packet_action_skills_diagnostic_only": _counter_values(
                raw["action_skills"]
            ),
            "same_packet_status_skills_diagnostic_only": _counter_values(
                raw["status_skills"]
            ),
            "examples": raw["examples"],
        }
        if reasons:
            result["decision"] = "conflict"
            result["reasons"] = reasons
            rejected.append(result)
        elif support:
            result["decision"] = "insufficient_support"
            result["reasons"] = support
            insufficient.append(result)
        else:
            result["decision"] = "accepted"
            result["reasons"] = []
            accepted.append(result)
        decisions[scope] = result

    status_counts: Counter[str] = Counter()
    by_job: dict[str, Counter[str]] = defaultdict(Counter)
    counterexamples: list[dict[str, Any]] = []
    for target in target_instances:
        hit_count = int(target["target_hits"])
        jobs = Counter({str(k): int(v) for k, v in target["jobs"].items()})
        job = jobs.most_common(1)[0][0] if jobs else ""
        by_job[job]["target_hits"] += hit_count
        by_job[job]["target_pairs"] += 1
        fallback_hits = sum(
            int(value) for value in target["native_fallback_accepted_skills"].values()
        )
        fallback_hits = min(fallback_hits, hit_count)
        if fallback_hits:
            status_counts["native_fallback_already_accepted_pairs"] += 1
            status_counts["native_fallback_already_accepted_hits"] += fallback_hits
            by_job[job]["native_fallback_hits"] += fallback_hits

        observations = target["first_observations"]
        if not observations:
            status = "no_prior_full_pair_record"
            decision = None
        elif target.get("ambiguous_generation_record"):
            status = "ambiguous_generation_record"
            decision = None
        else:
            scoped = [
                decisions.get(_scope_for(target, row))
                for row in observations
                if _scope_for(target, row) is not None
            ]
            scoped = [row for row in scoped if row is not None]
            accepted_rows = [row for row in scoped if row["decision"] == "accepted"]
            if any(bool(row.get("production_parsed")) for row in observations):
                status = "production_parser_present_downstream_gap"
            elif accepted_rows:
                status = "zero_conflict_validated"
            elif any(row["decision"] == "conflict" for row in scoped):
                status = "conflict"
            elif scoped:
                status = "insufficient_support"
            else:
                status = "unparsed_no_truth"
            decision = accepted_rows[0] if len(accepted_rows) == 1 else None

        uncovered_hits = max(0, hit_count - fallback_hits)
        status_counts[f"{status}_pairs"] += 1
        status_counts[f"{status}_hits"] += uncovered_hits
        by_job[job][f"{status}_pairs"] += 1
        by_job[job][f"{status}_hits"] += uncovered_hits
        target["classification"] = status
        target["object_candidate"] = (
            {
                "canonical_skill": int(decision["canonical_skill"]),
                "skill_name": str(decision["skill_name"]),
                "layout": str(decision["layout"]),
            }
            if decision is not None
            else None
        )
        if status == "zero_conflict_validated" and decision is not None and uncovered_hits:
            status_counts["new_zero_conflict_object_uplift_hits"] += uncovered_hits
            by_job[job]["new_zero_conflict_object_uplift_hits"] += uncovered_hits
            displayed = {
                int(str(key).split("|", 1)[0])
                for key in target["current_display_skills"]
            }
            predicted = int(decision["canonical_skill"])
            if displayed and displayed != {predicted}:
                counterexamples.append(
                    {
                        "capture": target["capture"],
                        "pair": target["pair"],
                        "job": job,
                        "predicted_from_accepted_object_chain": predicted,
                        "current_display_skills_diagnostic_only": sorted(displayed),
                        "hits": uncovered_hits,
                        "layout": decision["layout"],
                    }
                )

    return {
        "summary": {
            **dict(status_counts),
            "target_pairs": len(target_instances),
            "target_hits": sum(int(row["target_hits"]) for row in target_instances),
            "target_damage": sum(int(row["target_damage"]) for row in target_instances),
            "accepted_object_layout_scopes": len(accepted),
            "insufficient_object_layout_scopes": len(insufficient),
            "conflicting_object_layout_scopes": len(rejected),
        },
        "by_job": {key: dict(value) for key, value in sorted(by_job.items())},
        "accepted_object_layouts": accepted,
        "insufficient_support": insufficient,
        "conflicts": rejected,
        "current_name_counterexamples": counterexamples,
        "target_instances": target_instances,
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# raw -1 damage-object 实例链验收（v52）",
        "",
        "## 结论",
        "",
        (
            f"固定语料 {report['corpus']['capture_count']} 份，11 个未达 95% 职业中共找到 "
            f"{summary.get('target_hits', 0):,} 条 raw +0x90=-1、object>0 的 named inferred Hit，"
            f"涉及 {summary.get('target_pairs', 0):,} 个完整 owner+object 实例。"
        ),
        (
            f"其中既有 v50 +0xA4 零冲突映射已可覆盖 "
            f"{summary.get('native_fallback_already_accepted_hits', 0):,} Hit；"
            f"本次对象记录审计新增可验收 "
            f"{summary.get('new_zero_conflict_object_uplift_hits', 0):,} Hit。"
        ),
        (
            f"发现 {summary.get('accepted_object_layout_scopes', 0)} 个对象布局适用域达到零冲突门槛，"
            f"{summary.get('insufficient_object_layout_scopes', 0)} 个样本不足，"
            f"{summary.get('conflicting_object_layout_scopes', 0)} 个存在冲突。"
        ),
        "",
        "## 验收边界",
        "",
        "- 真值只接受已有 `packet_skill_id`，或对象/实体层的独立确定性归因。",
        "- 当前显示技能名、伤害数值、时间邻近、同包动作和状态只用于诊断，不参与证明。",
        "- 对象必须以完整 `wire source actor + damage_object_id` 匹配，并且创建/状态记录必须物理位于首次 Hit 之前。",
        "- 适用域包含职业、direct37/direct41、source kind、玩家/实体来源、marker/layout 和 Projectile template。",
        "- 裸 4 字节 object ID 即使与 owner 同包出现，也不构成对象链。",
        "- 本工具不修改正式解码器，也不改变任何伤害、来源、目标或现有技能构成。",
        "",
        "## 职业结果",
        "",
        "| 职业 | 目标 Hit | v50 +A4 已覆盖 | 新对象链可提升 | 已解析但下游缺口 | 无完整前置记录 | 样本不足 | 冲突 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for job in sorted(UNDER_95_JOBS):
        row = report["by_job"].get(job, {})
        lines.append(
            f"| {job} | {row.get('target_hits', 0):,} | "
            f"{row.get('native_fallback_hits', 0):,} | "
            f"{row.get('new_zero_conflict_object_uplift_hits', 0):,} | "
            f"{row.get('production_parser_present_downstream_gap_hits', 0):,} | "
            f"{row.get('no_prior_full_pair_record_hits', 0):,} | "
            f"{row.get('insufficient_support_hits', 0):,} | "
            f"{row.get('conflict_hits', 0):,} |"
        )

    lines.extend(
        [
            "",
            "## 达标对象布局",
            "",
            "| 职业 | 来源 | family | source kind | marker/layout | template | canonical | 技能 | 锚定 Hit | 抓包 | 实例 |",
            "|---|---|---|---:|---|---:|---:|---|---:|---:|---:|",
        ]
    )
    for row in report["accepted_object_layouts"]:
        lines.append(
            f"| {row['job']} | {row['source_class']} | {row['family']} | "
            f"{row['source_kind']} | {row['layout']} | {row['template_id']} | "
            f"{row['canonical_skill']} | {row['skill_name']} | {row['anchor_hits']:,} | "
            f"{row['capture_count']} | {row['pair_count']} |"
        )
    lines.extend(
        [
            "",
            "完整候选、反例、裸 object 偶遇计数、当前名称不一致样本及抓包哈希见同名 JSON。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--native-acceptance", type=Path, default=DEFAULT_NATIVE_ACCEPTANCE)
    parser.add_argument("--capture", action="append", default=[])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--min-hits", type=int, default=MIN_HITS)
    parser.add_argument("--min-captures", type=int, default=MIN_CAPTURES)
    parser.add_argument("--min-pairs", type=int, default=MIN_PAIRS)
    parser.add_argument("--min-owners", type=int, default=MIN_OWNERS)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    selected = _capture_selection(args.selection.resolve())
    paths = _find_captures(
        args.capture_root.resolve(), selected, set(args.capture)
    )
    captures: list[dict[str, Any]] = []
    if args.workers <= 1 or len(paths) <= 1:
        for path in paths:
            captures.append(_audit_capture(str(path)))
            print(f"audited {path.name}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            pending = {pool.submit(_audit_capture, str(path)): path for path in paths}
            for future in as_completed(pending):
                path = pending[future]
                captures.append(future.result())
                print(f"audited {path.name}", flush=True)
    captures.sort(key=lambda row: row["capture_sha256"])

    evaluated = _evaluate(
        captures,
        args.native_acceptance.resolve(),
        min_hits=args.min_hits,
        min_captures=args.min_captures,
        min_pairs=args.min_pairs,
        min_owners=args.min_owners,
    )
    report = {
        "schema": SCHEMA,
        "production_decoder_changed": False,
        "scope": sorted(UNDER_95_JOBS),
        "selection": str(args.selection.resolve()),
        "native_acceptance": str(args.native_acceptance.resolve()),
        "acceptance_contract": {
            "target": (
                "named non-deterministic physical Hit; primary +0x90 is read and -1; "
                "0 < damage_object_id < 0xffffffff"
            ),
            "truth": (
                "existing packet_skill_id or independently deterministic OBJECT/ENTITY attribution only"
            ),
            "excluded_as_truth": [
                "current displayed skill/name",
                "time proximity",
                "damage magnitude",
                "same-packet action/status without an independent object edge",
                "bare object-id occurrence",
            ],
            "scope_fields": [
                "job",
                "wire family",
                "source kind",
                "player/entity source",
                "record marker/layout",
                "Projectile template",
            ],
            "minimum_support": {
                "independent_hits": args.min_hits,
                "captures": args.min_captures,
                "owner_object_pairs": args.min_pairs,
                "source_instances": args.min_owners,
            },
        },
        "corpus": {
            "capture_count": len(captures),
            "capture_hashes": [row["capture_sha256"] for row in captures],
        },
        **evaluated,
        "captures": captures,
        "tool_inputs": {
            "this_tool_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "native_audit_tool_sha256": sha256(
                (ROOT / "tools/audit_native_meter_skill_key_v50.py").read_bytes()
            ).hexdigest(),
            "native_acceptance_sha256": sha256(
                args.native_acceptance.resolve().read_bytes()
            ).hexdigest(),
            "selection_sha256": sha256(args.selection.resolve().read_bytes()).hexdigest(),
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
