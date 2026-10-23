from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger  # noqa: E402
from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_damage_skill_ids,
    _load_skill_catalog,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_captured_frame,
    decode_v576_damage_object_registration,
    decode_v576_status_events,
    decode_v576_structured_actions,
)


MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
SKIPPED_ADAPTERS = frozenset(
    {
        IDENTITY_TRACE_ADAPTER_INDEX,
        IDENTITY_METADATA_ADAPTER_INDEX,
        SESSION_EVIDENCE_ADAPTER_INDEX,
    }
)
ACTION_MARKERS = (
    b"\x0e\x23\x00",
    b"\x0e\x1f\x00",
    b"\x12\x24\x00",
    b"\x12\x20\x00",
)
OBJECT_MARKERS = tuple(
    bytes((0x09, value, 0x00))
    for value in (0x5D, 0x61, 0x6E, 0x62, 0x66, 0x6A, 0x73, 0x6F)
)
RESPONSE_CODES = frozenset({"134a00", "134e00", "134700"})
WIDTHS = (2, 3, 4)

# The explicit Magic Archer skill set is used only to label the application
# side of the audit.  It is never used to select a field or pair packets.
MAGIC_ARCHER_SKILL_IDS = frozenset(
    {
        1001,
        1003,
        1017,
        1021,
        1027,
        1201,
        1202,
        1203,
        1205,
        1207,
        1208,
        1209,
        1210,
        1211,
        1215,
        1217,
        1220,
        1221,
        1302,
        1304,
        1306,
        1352,
        1353,
        1354,
        1355,
        1356,
    }
)
MAGIC_ARCHER_TEMPLATES = frozenset(
    int(spec.projectile_template_id)
    for spec in _DAMAGE_OBJECT_SPECS
    if int(spec.canonical_skill_id) in MAGIC_ARCHER_SKILL_IDS
    or bool(set(spec.skill_ids) & MAGIC_ARCHER_SKILL_IDS)
)
SKILL_CATALOG = _load_skill_catalog(str(DATA_DIR))


@dataclass(frozen=True, slots=True)
class ChildRegistration:
    packet_index: int
    timestamp_ms: int
    actor_id: int
    object_id: int
    template_id: int
    effect_resource_id: int
    child_sequence: int
    transport: str
    tcp_sequence: int | None
    body: bytes


@dataclass(frozen=True, slots=True)
class ParentRecord:
    packet_index: int
    timestamp_ms: int
    actor_id: int
    skill_id: int
    kind: str
    marker: str
    anchor: int
    transport: str
    tcp_sequence: int | None
    body: bytes
    object_id: int = 0
    template_id: int = 0


def _skill_name(skill_id: int) -> str:
    row = SKILL_CATALOG.get(int(skill_id))
    return str(row[0]) if row else ""


def _iter_offsets(body: bytes, marker: bytes, start: int = 9) -> Iterable[int]:
    offset = body.find(marker, start)
    while offset >= 0:
        yield offset
        offset = body.find(marker, offset + 1)


def _iter_logical_packets(path: Path) -> Iterable[Any]:
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"not a DNDP1 capture: {path}")
        while True:
            header = handle.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = FRAME_HEADER.unpack(
                header
            )
            if captured_length > 16 * 1024 * 1024:
                raise ValueError(f"abnormal frame length in {path}")
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter in SKIPPED_ADAPTERS:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical in transport.feed(packet):
                if ledger.accept(logical):
                    yield logical
    for logical in transport.flush():
        if ledger.accept(logical):
            yield logical


def _safe_template_map(provenance_path: Path) -> dict[int, int]:
    raw = json.loads(provenance_path.read_text(encoding="utf-8"))
    result: dict[int, int] = {}
    for row in raw.get("mappings", ()):
        packet = row.get("packet_skill_id", {})
        counts = {
            int(key): int(value)
            for key, value in packet.get("support_skill_counts", {}).items()
        }
        if (
            row.get("classification") == "safe"
            and bool(packet.get("zero_conflict"))
            and int(packet.get("conflict_hits", 0)) == 0
            and int(packet.get("support_hits", 0)) >= 30
            and len(counts) == 1
        ):
            result[int(row["projectile_template_id"])] = next(iter(counts))
    return result


def _index_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    templates: Counter[int] = Counter()
    actors: Counter[int] = Counter()
    packet_count = 0
    udp = 0
    tcp = 0
    for packet_count, packet in enumerate(_iter_logical_packets(path), start=1):
        registration = decode_v576_damage_object_registration(
            bytes(packet.payload), packet.inferred_direction()
        )
        if registration is None:
            continue
        templates[int(registration.projectile_template_id)] += 1
        actors[int(registration.source_actor_id)] += 1
        if str(packet.transport).casefold() == "udp":
            udp += 1
        else:
            tcp += 1
    return {
        "capture": str(path),
        "packet_count": packet_count,
        "registration_count": sum(templates.values()),
        "templates": {str(key): value for key, value in templates.most_common()},
        "actors": {f"0x{key:08X}": value for key, value in actors.most_common()},
        "udp_registrations": udp,
        "tcp_registrations": tcp,
    }


def _record_action_parents(
    payload: bytes,
    direction: str | None,
    known_skills: frozenset[int],
    packet_index: int,
    timestamp_ms: int,
    transport: str,
    tcp_sequence: int | None,
) -> list[ParentRecord]:
    decoded_rows = decode_v576_structured_actions(
        payload,
        direction,
        known_skills,
        (),
        allow_unknown_actor_ids=True,
    )
    wanted = {
        (int(row.source_actor_id or 0), int(row.skill_id or 0))
        for row in decoded_rows
        if int(row.source_actor_id or 0) > 0 and int(row.skill_id or 0) > 0
    }
    if not wanted:
        return []
    candidates: list[ParentRecord] = []
    seen: set[tuple[int, int, int, bytes]] = set()
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        header_actor = int.from_bytes(body[10:14], "little") if len(body) >= 14 else 0
        for marker in ACTION_MARKERS:
            for offset in _iter_offsets(body, marker, 14):
                if offset < 4 or offset + 7 > len(body):
                    continue
                marker_actor = int.from_bytes(body[offset - 4 : offset], "little")
                skill_id = int.from_bytes(body[offset + 3 : offset + 7], "little")
                actor_candidates = (
                    (marker_actor,)
                    if direction == "server_to_client"
                    else tuple(dict.fromkeys((marker_actor, header_actor)))
                )
                for actor_id in actor_candidates:
                    if (actor_id, skill_id) not in wanted:
                        continue
                    key = (phase, offset, actor_id, marker)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(
                        ParentRecord(
                            packet_index=packet_index,
                            timestamp_ms=timestamp_ms,
                            actor_id=actor_id,
                            skill_id=skill_id,
                            kind="action",
                            marker=marker.hex(" ").upper(),
                            anchor=offset,
                            transport=transport,
                            tcp_sequence=tcp_sequence,
                            body=body,
                        )
                    )
    # A decoded action whose wire record appears under multiple XOR phases is
    # not suitable for field discovery.  Keep only unambiguous packet records.
    grouped: dict[tuple[int, int, int], list[ParentRecord]] = defaultdict(list)
    for row in candidates:
        grouped[(row.packet_index, row.actor_id, row.skill_id)].append(row)
    result: list[ParentRecord] = []
    for rows in grouped.values():
        phases = {row.body for row in rows}
        if len(phases) == 1:
            result.extend(rows[:1])
        elif len({(row.anchor, row.marker) for row in rows}) == 1:
            # Same record found by equivalent candidate phase enumeration.
            result.append(rows[0])
    return result


def _record_response_parents(
    payload: bytes,
    direction: str | None,
    known_skills: frozenset[int],
    packet_index: int,
    timestamp_ms: int,
    transport: str,
    tcp_sequence: int | None,
) -> list[ParentRecord]:
    result: list[ParentRecord] = []
    seen: set[tuple[int, int, int, str]] = set()
    for row in decode_v576_status_events(payload, direction, known_skills):
        if str(row.effect_code).casefold() not in RESPONSE_CODES:
            continue
        actor_id = int(row.source_actor_id or 0)
        skill_id = int(row.skill_id or 0)
        if actor_id <= 0 or skill_id <= 0:
            continue
        body = _decode_v576_body(payload, int(row.xor_phase))
        key = (int(row.xor_phase), int(row.record_offset), actor_id, row.effect_code)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            ParentRecord(
                packet_index=packet_index,
                timestamp_ms=timestamp_ms,
                actor_id=actor_id,
                skill_id=skill_id,
                kind="response",
                marker=str(row.effect_code).upper(),
                anchor=int(row.record_offset),
                transport=transport,
                tcp_sequence=tcp_sequence,
                body=body,
            )
        )
    return result


def _record_object_parents(
    payload: bytes,
    direction: str | None,
    safe_map: dict[int, int],
    packet_index: int,
    timestamp_ms: int,
    transport: str,
    tcp_sequence: int | None,
) -> list[ParentRecord]:
    if direction not in {"server_to_client", "client_to_server", None}:
        return []
    result: list[ParentRecord] = []
    seen: set[tuple[int, int, int, int, bytes]] = set()
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        for marker in OBJECT_MARKERS:
            for offset in _iter_offsets(body, marker):
                if offset < 4 or offset + 48 > len(body):
                    continue
                actor_id = int.from_bytes(body[offset - 4 : offset], "little")
                object_id = int.from_bytes(body[offset + 8 : offset + 12], "little")
                template_id = int.from_bytes(body[offset + 12 : offset + 16], "little")
                repeated_actor = int.from_bytes(body[offset + 44 : offset + 48], "little")
                if (
                    actor_id < 0x01000000
                    or repeated_actor != actor_id
                    or body[offset + 3 : offset + 8] != b"\0" * 5
                    or not 1 <= object_id <= 0x00FFFFFF
                    or not 1 <= template_id <= 1_000_000
                ):
                    continue
                key = (phase, offset, actor_id, object_id, marker)
                if key in seen:
                    continue
                seen.add(key)
                result.append(
                    ParentRecord(
                        packet_index=packet_index,
                        timestamp_ms=timestamp_ms,
                        actor_id=actor_id,
                        # Unknown templates stay visible for structural
                        # parent/child-key falsification, but only a template
                        # independently proved by packet_skill_id receives a
                        # skill truth and can contribute promotion support.
                        skill_id=int(safe_map.get(template_id, 0)),
                        kind="object_state",
                        marker=marker.hex(" ").upper(),
                        anchor=offset,
                        transport=transport,
                        tcp_sequence=tcp_sequence,
                        body=body,
                        object_id=object_id,
                        template_id=template_id,
                    )
                )
    return result


def _overlaps(offset: int, width: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(offset < end and offset + width > start for start, end in ranges)


def _useful_value(value: int, width: int) -> bool:
    maximum = (1 << (width * 8)) - 1
    if value in {0, 1, maximum}:
        return False
    if width == 2:
        return value >= 16
    if width == 3:
        return value >= 256
    return value >= 256


def _child_fields(row: ChildRegistration) -> dict[tuple[str, int], int]:
    body = row.body
    result: dict[tuple[str, int], int] = {
        ("damage_object_id", 4): row.object_id,
        ("damage_object_id_minus_child_sequence", 4): (
            row.object_id - row.child_sequence
        ) & 0xFFFFFFFF,
        ("damage_object_id_plus_child_sequence", 4): (
            row.object_id + row.child_sequence
        ) & 0xFFFFFFFF,
        ("damage_object_high16", 2): (row.object_id >> 16) & 0xFFFF,
        ("effect_resource_id", 2): row.effect_resource_id,
        ("child_sequence", 2): row.child_sequence,
        ("resource_word", 4): (
            (row.child_sequence << 16) | row.effect_resource_id
        ),
    }
    excluded = (
        (5, 9),   # message signature
        (10, 14), # source actor
        (19, 23), # damage object
        (23, 27), # Projectile template
        (55, 59), # repeated actor
        (59, 63), # resource word
        (75, 79), # repeated effect resource
    )
    for width in WIDTHS:
        for offset in range(0, len(body) - width + 1):
            if _overlaps(offset, width, excluded):
                continue
            value = int.from_bytes(body[offset : offset + width], "little")
            if _useful_value(value, width):
                result[(f"body@{offset:+d}", width)] = value
    if row.tcp_sequence is not None:
        result[("tcp_sequence", 4)] = int(row.tcp_sequence) & 0xFFFFFFFF
    return result


def _parent_fields(row: ParentRecord) -> dict[tuple[str, int], int]:
    body = row.body
    anchor = row.anchor
    result: dict[tuple[str, int], int] = {}
    if row.object_id:
        result[("object_id", 4)] = row.object_id
        result[("object_high16", 2)] = (row.object_id >> 16) & 0xFFFF
    if row.tcp_sequence is not None:
        result[("tcp_sequence", 4)] = int(row.tcp_sequence) & 0xFFFFFFFF
    if row.kind == "action":
        excluded = (
            (5, 9),
            (10, 14),
            (anchor - 4, anchor),
            (anchor, anchor + 3),
            (anchor + 3, anchor + 7),
        )
        start = max(0, anchor - 32)
        end = min(len(body), anchor + 96)
    elif row.kind == "response":
        # Known response fields are deliberately excluded.  The parameter
        # tail remains searchable because it is the plausible location for a
        # runtime parent/effect instance identifier.
        excluded = (
            (5, 9),
            (anchor - 16, anchor - 12),
            (anchor - 12, anchor - 9),
            (anchor - 9, anchor - 5),
            (anchor - 4, anchor),
            (anchor, anchor + 20),
        )
        start = max(0, anchor - 24)
        end = min(len(body), anchor + 128)
    else:
        excluded = (
            (5, 9),
            (anchor - 4, anchor),
            (anchor, anchor + 8),
            (anchor + 8, anchor + 12),
            (anchor + 12, anchor + 16),
            (anchor + 44, anchor + 48),
        )
        start = max(0, anchor - 24)
        end = min(len(body), anchor + 112)
    for width in WIDTHS:
        for offset in range(start, end - width + 1):
            if _overlaps(offset, width, excluded):
                continue
            value = int.from_bytes(body[offset : offset + width], "little")
            if _useful_value(value, width):
                result[(f"rel@{offset - anchor:+d}", width)] = value
    return result


def _shape_text(shape: tuple[str, str, str, str, int]) -> str:
    parent_kind, marker, parent_field, child_field, width = shape
    return f"{parent_kind}|{marker}|{parent_field}|{child_field}|u{width * 8}"


def _analyze_capture(
    path_text: str,
    safe_map: dict[int, int],
) -> dict[str, Any]:
    path = Path(path_text)
    known_skills = _load_damage_skill_ids(str(DATA_DIR))
    children: list[ChildRegistration] = []
    parents: list[ParentRecord] = []
    packet_count = 0
    registration_packets_with_action = 0
    registration_packets_with_response = 0
    registration_packets_with_object_state = 0

    for packet_count, packet in enumerate(_iter_logical_packets(path), start=1):
        payload = bytes(packet.payload)
        direction = packet.inferred_direction()
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        transport = str(packet.transport).casefold()
        tcp_sequence = packet.tcp_sequence
        local = decode_v576_damage_object_registration(payload, direction)
        action_rows = _record_action_parents(
            payload,
            direction,
            known_skills,
            packet_count,
            timestamp_ms,
            transport,
            tcp_sequence,
        )
        response_rows = _record_response_parents(
            payload,
            direction,
            known_skills,
            packet_count,
            timestamp_ms,
            transport,
            tcp_sequence,
        )
        object_rows = _record_object_parents(
            payload,
            direction,
            safe_map,
            packet_count,
            timestamp_ms,
            transport,
            tcp_sequence,
        )
        parents.extend(action_rows)
        parents.extend(response_rows)
        parents.extend(object_rows)
        if local is None:
            continue
        body = _decode_v576_body(payload, int(local.xor_phase))
        children.append(
            ChildRegistration(
                packet_index=packet_count,
                timestamp_ms=timestamp_ms,
                actor_id=int(local.source_actor_id),
                object_id=int(local.damage_object_id),
                template_id=int(local.projectile_template_id),
                effect_resource_id=int(local.effect_resource_id),
                child_sequence=int(local.child_sequence),
                transport=transport,
                tcp_sequence=tcp_sequence,
                body=body,
            )
        )
        registration_packets_with_action += int(bool(action_rows))
        registration_packets_with_response += int(bool(response_rows))
        registration_packets_with_object_state += int(bool(object_rows))

    local_actors = {row.actor_id for row in children}
    parents = [row for row in parents if row.actor_id in local_actors]

    child_index: dict[tuple[int, int, int], list[tuple[int, str]]] = defaultdict(list)
    parent_index: dict[tuple[int, int, int], list[tuple[int, str]]] = defaultdict(list)
    child_fields_by_index: list[dict[tuple[str, int], int]] = []
    parent_fields_by_index: list[dict[tuple[str, int], int]] = []

    for index, row in enumerate(children):
        fields = _child_fields(row)
        child_fields_by_index.append(fields)
        for (field, width), value in fields.items():
            child_index[(row.actor_id, width, value)].append((index, field))
    for index, row in enumerate(parents):
        fields = _parent_fields(row)
        parent_fields_by_index.append(fields)
        for (field, width), value in fields.items():
            parent_index[(row.actor_id, width, value)].append((index, field))

    stats: dict[tuple[str, str, str, str, int], dict[str, Any]] = {}
    conflict_examples: list[dict[str, Any]] = []
    application_examples: list[dict[str, Any]] = []
    for value_key in set(child_index) & set(parent_index):
        _actor, width, value = value_key
        child_refs = child_index[value_key]
        parent_refs = parent_index[value_key]
        # Exact deterministic keys must select one record on each side.  A
        # field repeated inside one record is deduplicated by field label.
        by_child_field: dict[str, set[int]] = defaultdict(set)
        by_parent_shape: dict[tuple[str, str, str], set[int]] = defaultdict(set)
        for index, field in child_refs:
            by_child_field[field].add(index)
        for index, field in parent_refs:
            parent = parents[index]
            by_parent_shape[(parent.kind, parent.marker, field)].add(index)
        for child_field, child_ids in by_child_field.items():
            for (parent_kind, marker, parent_field), parent_ids in by_parent_shape.items():
                shape = (
                    parent_kind,
                    marker,
                    parent_field,
                    child_field,
                    width,
                )
                row_stats = stats.setdefault(
                    shape,
                    {
                        "shared_keys": 0,
                        "unique_links": 0,
                        "ambiguous_keys": 0,
                        "truth_support": 0,
                        "truth_conflicts": 0,
                        "unlabelled_links": 0,
                        "values": set(),
                        "parent_skills": Counter(),
                        "child_templates": Counter(),
                        "magic_links": 0,
                        "examples": [],
                    },
                )
                row_stats["shared_keys"] += 1
                row_stats["values"].add(value)
                if len(child_ids) != 1 or len(parent_ids) != 1:
                    row_stats["ambiguous_keys"] += 1
                    continue
                parent_id = next(iter(parent_ids))
                child_id = next(iter(child_ids))
                parent = parents[parent_id]
                child = children[child_id]
                row_stats["unique_links"] += 1
                if parent.skill_id > 0:
                    row_stats["parent_skills"][parent.skill_id] += 1
                row_stats["child_templates"][child.template_id] += 1
                child_skill = int(safe_map.get(child.template_id, 0))
                if child_skill and parent.skill_id > 0:
                    if child_skill == parent.skill_id:
                        row_stats["truth_support"] += 1
                    else:
                        row_stats["truth_conflicts"] += 1
                        if len(conflict_examples) < 80:
                            conflict_examples.append(
                                {
                                    "shape": _shape_text(shape),
                                    "value": value,
                                    "actor_id": f"0x{child.actor_id:08X}",
                                    "parent_packet": parent.packet_index,
                                    "parent_skill_id": parent.skill_id,
                                    "parent_skill_name": _skill_name(parent.skill_id),
                                    "child_packet": child.packet_index,
                                    "child_template_id": child.template_id,
                                    "child_truth_skill_id": child_skill,
                                    "child_truth_skill_name": _skill_name(child_skill),
                                }
                            )
                else:
                    row_stats["unlabelled_links"] += 1
                if (
                    child.template_id in MAGIC_ARCHER_TEMPLATES
                    or parent.skill_id in MAGIC_ARCHER_SKILL_IDS
                ):
                    row_stats["magic_links"] += 1
                    if len(application_examples) < 80:
                        application_examples.append(
                            {
                                "shape": _shape_text(shape),
                                "value": value,
                                "actor_id": f"0x{child.actor_id:08X}",
                                "parent_packet": parent.packet_index,
                                "parent_skill_id": parent.skill_id,
                                "parent_skill_name": _skill_name(parent.skill_id),
                                "child_packet": child.packet_index,
                                "child_template_id": child.template_id,
                                "child_object_id": child.object_id,
                                "effect_resource_id": child.effect_resource_id,
                                "child_sequence": child.child_sequence,
                                "child_truth_skill_id": child_skill,
                            }
                        )
                if len(row_stats["examples"]) < 3:
                    row_stats["examples"].append(
                        {
                            "value": value,
                            "parent_packet": parent.packet_index,
                            "parent_skill_id": parent.skill_id,
                            "child_packet": child.packet_index,
                            "child_template_id": child.template_id,
                            "child_truth_skill_id": child_skill,
                        }
                    )

    candidate_rows: list[dict[str, Any]] = []
    for shape, row in stats.items():
        candidate_rows.append(
            {
                "shape": _shape_text(shape),
                "parent_kind": shape[0],
                "marker": shape[1],
                "parent_field": shape[2],
                "child_field": shape[3],
                "width": shape[4],
                "shared_keys": int(row["shared_keys"]),
                "unique_links": int(row["unique_links"]),
                "ambiguous_keys": int(row["ambiguous_keys"]),
                "truth_support": int(row["truth_support"]),
                "truth_conflicts": int(row["truth_conflicts"]),
                "unlabelled_links": int(row["unlabelled_links"]),
                "distinct_values": len(row["values"]),
                "parent_skills": {
                    str(key): value for key, value in row["parent_skills"].most_common()
                },
                "child_templates": {
                    str(key): value for key, value in row["child_templates"].most_common()
                },
                "magic_links": int(row["magic_links"]),
                "examples": row["examples"],
            }
        )
    # One-capture accidental byte equalities are numerous. Return only rows
    # with at least two truth-labelled links, or a Magic Archer application,
    # plus the named fields needed for explicit hypothesis falsification.
    named = {
        "object_id",
        "object_high16",
        "damage_object_id",
        "damage_object_id_minus_child_sequence",
        "damage_object_id_plus_child_sequence",
        "damage_object_high16",
        "effect_resource_id",
        "child_sequence",
        "resource_word",
        "tcp_sequence",
    }
    candidate_rows = [
        row
        for row in candidate_rows
        if (
        int(row["unique_links"]) > 0
        and int(row["truth_support"]) + int(row["truth_conflicts"]) >= 2
        )
        or int(row["magic_links"]) > 0
        or row["parent_field"] in named
        or row["child_field"] in named
    ]
    candidate_rows.sort(
        key=lambda row: (
            int(row["truth_conflicts"]),
            int(row["ambiguous_keys"]),
            -int(row["truth_support"]),
            -int(row["distinct_values"]),
        )
    )

    child_templates = Counter(row.template_id for row in children)
    child_skills = Counter(
        safe_map[row.template_id]
        for row in children
        if row.template_id in safe_map
    )
    marker_counts = Counter(f"{row.kind}|{row.marker}" for row in parents)
    object_ranges: list[dict[str, Any]] = []
    for actor in sorted(local_actors):
        child_objects = sorted(row.object_id for row in children if row.actor_id == actor)
        parent_objects = sorted(
            row.object_id
            for row in parents
            if row.actor_id == actor and row.object_id > 0
        )
        child_set = set(child_objects)
        parent_set = set(parent_objects)
        object_ranges.append(
            {
                "actor_id": f"0x{actor:08X}",
                "child_count": len(child_objects),
                "child_min": min(child_objects) if child_objects else 0,
                "child_max": max(child_objects) if child_objects else 0,
                "parent_count": len(parent_objects),
                "parent_min": min(parent_objects) if parent_objects else 0,
                "parent_max": max(parent_objects) if parent_objects else 0,
                "exact_object_overlap": len(child_set & parent_set),
            }
        )

    return {
        "capture": str(path),
        "packet_count": packet_count,
        "children": len(children),
        "child_actors": [f"0x{value:08X}" for value in sorted(local_actors)],
        "child_templates": {str(key): value for key, value in child_templates.most_common()},
        "truth_child_count": sum(child_skills.values()),
        "truth_child_skills": {str(key): value for key, value in child_skills.most_common()},
        "magic_child_count": sum(
            count for key, count in child_templates.items() if key in MAGIC_ARCHER_TEMPLATES
        ),
        "parents": len(parents),
        "parent_markers": dict(marker_counts.most_common()),
        "registration_packets_with_action": registration_packets_with_action,
        "registration_packets_with_response": registration_packets_with_response,
        "registration_packets_with_object_state": registration_packets_with_object_state,
        "object_ranges": object_ranges,
        "candidates": candidate_rows,
        "conflict_examples": conflict_examples,
        "magic_application_examples": application_examples,
    }


def _merge_candidates(
    reports: list[dict[str, Any]],
    train_captures: set[str],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for report in reports:
        capture = str(report["capture"])
        split = "train" if capture in train_captures else "validation"
        for source in report.get("candidates", ()):
            row = merged.setdefault(
                source["shape"],
                {
                    key: source[key]
                    for key in (
                        "shape",
                        "parent_kind",
                        "marker",
                        "parent_field",
                        "child_field",
                        "width",
                    )
                }
                | {
                    "train": Counter(),
                    "validation": Counter(),
                    "captures": {"train": set(), "validation": set()},
                    "values": {"train": set(), "validation": set()},
                    "parent_skills": Counter(),
                    "child_templates": Counter(),
                    "magic_links": 0,
                    "examples": [],
                },
            )
            bucket: Counter[str] = row[split]
            for field in (
                "shared_keys",
                "unique_links",
                "ambiguous_keys",
                "truth_support",
                "truth_conflicts",
                "unlabelled_links",
                "distinct_values",
            ):
                bucket[field] += int(source.get(field, 0))
            row["captures"][split].add(capture)
            for key, value in source.get("parent_skills", {}).items():
                row["parent_skills"][int(key)] += int(value)
            for key, value in source.get("child_templates", {}).items():
                row["child_templates"][int(key)] += int(value)
            row["magic_links"] += int(source.get("magic_links", 0))
            if len(row["examples"]) < 8:
                row["examples"].extend(
                    {"capture": capture, **example}
                    for example in source.get("examples", ())[: 8 - len(row["examples"])]
                )

    result: list[dict[str, Any]] = []
    for row in merged.values():
        train = row["train"]
        validation = row["validation"]
        train_capture_count = len(row["captures"]["train"])
        validation_capture_count = len(row["captures"]["validation"])
        # A field is promotable only if it is discovered without looking at
        # validation captures and then independently remains exact there.
        promotable = (
            train["truth_support"] >= 10
            and train_capture_count >= 3
            and train["truth_conflicts"] == 0
            and train["ambiguous_keys"] == 0
            and validation["truth_support"] >= 5
            and validation_capture_count >= 2
            and validation["truth_conflicts"] == 0
            and validation["ambiguous_keys"] == 0
            and len(row["parent_skills"]) >= 2
            and len(row["child_templates"]) >= 2
        )
        result.append(
            {
                key: row[key]
                for key in (
                    "shape",
                    "parent_kind",
                    "marker",
                    "parent_field",
                    "child_field",
                    "width",
                )
            }
            | {
                "train": dict(train),
                "validation": dict(validation),
                "train_capture_count": train_capture_count,
                "validation_capture_count": validation_capture_count,
                "parent_skill_count": len(row["parent_skills"]),
                "parent_skills": {
                    str(key): value for key, value in row["parent_skills"].most_common()
                },
                "child_template_count": len(row["child_templates"]),
                "child_templates": {
                    str(key): value for key, value in row["child_templates"].most_common()
                },
                "magic_links": int(row["magic_links"]),
                "promotable": promotable,
                "examples": row["examples"],
            }
        )
    result.sort(
        key=lambda row: (
            not bool(row["promotable"]),
            int(row["train"].get("truth_conflicts", 0))
            + int(row["validation"].get("truth_conflicts", 0)),
            int(row["train"].get("ambiguous_keys", 0))
            + int(row["validation"].get("ambiguous_keys", 0)),
            -int(row["train"].get("truth_support", 0))
            - int(row["validation"].get("truth_support", 0)),
        )
    )
    return result


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# 魔羽动作/响应 → 本地 107-byte 子对象确定性键审计",
        "",
        "## 结论",
        "",
        result["conclusion"],
        "",
        "本报告的字段搜索不使用时间邻近、资源名、damage fingerprint、result-group 或 learned profile。时间戳只保留在原始记录定位中，不参与候选生成、选择或验证。",
        "",
        "## 覆盖",
        "",
        "| 指标 | 数量 |",
        "|---|---:|",
        f"| 扫描抓包 | {summary['indexed_captures']} |",
        f"| 含本地 107-byte 注册的抓包 | {summary['captures_with_local_registration']} |",
        f"| 深度训练/验证抓包 | {summary['audited_captures']} |",
        f"| 本地 107-byte 注册 | {summary['child_registrations']} |",
        f"| packet-validated 真值注册 | {summary['truth_child_registrations']} |",
        f"| 魔羽本机注册 | {summary['magic_child_registrations']} |",
        f"| 可升级确定性键 | {summary['promotable_key_count']} |",
        "",
        "## 107-byte 已知字段",
        "",
        "| 字段 | 解密后偏移 | 说明 |",
        "|---|---:|---|",
        "| source_actor_id | 10 / 55 | 两处必须相等 |",
        "| damage_object_id | 19 | direct37 的对象键 |",
        "| Projectile template | 23 | Projectile RowId |",
        "| effect_resource_id | 59 低 16 位 / 75 | 两处必须相等 |",
        "| child_sequence | 59 高 16 位 | 本地子序号 |",
        "",
        "上述字段及所有重叠字节窗口均从通用字段搜索中剔除；因此 offset 20 一类命中不再算新键。",
        "",
        "## 候选键验证",
        "",
    ]
    promotable = [row for row in result["candidate_keys"] if row["promotable"]]
    if promotable:
        lines.extend(
            [
                "| 键 | 训练支持/冲突 | 验证支持/冲突 | 技能/模板 | 结论 |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for row in promotable:
            lines.append(
                f"| `{row['shape']}` | {row['train'].get('truth_support', 0)}/{row['train'].get('truth_conflicts', 0)} | "
                f"{row['validation'].get('truth_support', 0)}/{row['validation'].get('truth_conflicts', 0)} | "
                f"{row['parent_skill_count']}/{row['child_template_count']} | 可升级 |"
            )
    else:
        lines.append("没有任何 action/response/object-state 字段键通过跨抓包零冲突、一对一、留出验证门槛。")

    lines.extend(
        [
            "",
            "## 重点假设结果",
            "",
            "| 检查项 | 结果 |",
            "|---|---|",
        ]
    )
    for row in result["hypothesis_results"]:
        lines.append(f"| {row['name']} | {row['result']} |")

    lines.extend(["", "## 精确反例", ""])
    counterexamples = result.get("counterexamples", ())
    if not counterexamples:
        lines.append("未产生可定位的真值冲突；当前阻塞来自键缺失或多重匹配。")
    else:
        for row in counterexamples[:20]:
            lines.append(
                "- `{capture}`：`{shape}` value={value}，父包 #{parent_packet} 指向 "
                "{parent_skill_id} {parent_skill_name}，子注册包 #{child_packet} 的 "
                "Projectile {child_template_id} 已由 packet 固定槽证明为 "
                "{child_truth_skill_id} {child_truth_skill_name}。".format(**row)
            )

    lines.extend(
        [
            "",
            "## 尚缺字段",
            "",
            "要把父动作/响应与 107-byte 子对象逐次连接，协议中还需要一个在两端重复的每次施放实例键：例如 cast_instance_id / parent_object_id / spawn_batch_id。它必须同时出现在动作或 13 4A/4E/47 响应与 107-byte 注册中，并且在同 actor 的重复施放间保持一对一。当前已解字段中没有通过该条件的键。",
            "",
            "UDP 没有 TCP sequence；抓到的 107-byte 注册均为 UDP，因此 `PacketRecord.tcp_sequence` 为空。若协议存在 UDP 批次号，只能位于尚未命名的应用层字段；本轮已对这些字段做字节级交叉验证，但没有找到跨抓包零冲突键。",
            "",
            "## 边界",
            "",
            "- `09 66` 只有通过 owner 重复、零守卫、object 与 Projectile 字段的完整记录才算对象状态；direct37 结果体内部出现的 `09 66` 字节常量不算父对象。",
            "- 对象编号区间重叠、相同高 16 位、`object±child_sequence` 只能作为待测数值关系；没有一对一与真值验证就不能用于生产归因。",
            "- 本轮没有修改生产解码器或生产映射。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Strictly audit whether action/response/object-state packets carry a "
            "deterministic key copied into the local 107-byte child registration."
        )
    )
    parser.add_argument("captures", nargs="*", type=Path)
    parser.add_argument("--capture-root", type=Path)
    parser.add_argument(
        "--provenance",
        type=Path,
        default=ROOT / "outputs" / "damage_attribution" / "projectile_mapping_provenance_audit.json",
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--reuse-index",
        type=Path,
        help="Reuse capture_index from a prior JSON report and skip the first pass.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    captures = [path.resolve() for path in args.captures]
    if args.capture_root:
        captures.extend(path.resolve() for path in args.capture_root.rglob("*.dpscap"))
    captures = sorted(set(captures), key=lambda path: str(path).casefold())
    if not captures:
        parser.error("no captures")
    workers = max(1, min(2, int(args.workers)))
    safe_map = _safe_template_map(args.provenance.resolve())

    indexed: list[dict[str, Any]] = []
    if args.reuse_index:
        prior = json.loads(args.reuse_index.resolve().read_text(encoding="utf-8"))
        allowed = {str(path).casefold() for path in captures}
        indexed = [
            row
            for row in prior.get("capture_index", ())
            if str(Path(row["capture"]).resolve()).casefold() in allowed
        ]
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_index_capture, str(path)): path for path in captures}
            for future in as_completed(futures):
                indexed.append(future.result())
    indexed.sort(key=lambda row: str(row["capture"]).casefold())

    # Deep audit every capture containing local registrations. This avoids a
    # filename/job selection bias while keeping captures without the proposed
    # child record out of the expensive field scan.
    audited_paths = [
        Path(row["capture"])
        for row in indexed
        if int(row["registration_count"]) > 0
    ]
    reports: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_analyze_capture, str(path), safe_map): path
            for path in audited_paths
        }
        for future in as_completed(futures):
            reports.append(future.result())
    reports.sort(key=lambda row: str(row["capture"]).casefold())

    # Alternating sorted captures gives a deterministic capture-level holdout.
    train_captures = {
        str(row["capture"])
        for index, row in enumerate(reports)
        if index % 2 == 0
    }
    candidates = _merge_candidates(reports, train_captures)
    promotable = [row for row in candidates if row["promotable"]]

    counterexamples: list[dict[str, Any]] = []
    for report in reports:
        for example in report.get("conflict_examples", ()):
            counterexamples.append(
                {"capture": report["capture"], **example}
            )
    counterexamples.sort(key=lambda row: (row["shape"], row["capture"]))

    registration_count = sum(int(row["children"]) for row in reports)
    truth_count = sum(int(row["truth_child_count"]) for row in reports)
    magic_count = sum(int(row["magic_child_count"]) for row in reports)
    exact_object_overlap = sum(
        int(item["exact_object_overlap"])
        for row in reports
        for item in row.get("object_ranges", ())
    )
    object_hypotheses = [
        row
        for row in candidates
        if row["parent_kind"] == "object_state"
        and (
            row["parent_field"] in {"object_id", "object_high16"}
            or row["child_field"].startswith("damage_object")
            or row["child_field"] == "child_sequence"
        )
    ]
    marker_0966_hypotheses = [
        row for row in candidates if row["marker"] == "09 66 00"
    ]
    effect_hypotheses = [
        row
        for row in candidates
        if row["child_field"] in {"effect_resource_id", "resource_word"}
    ]
    sequence_hypotheses = [
        row for row in candidates if row["child_field"] == "child_sequence"
    ]
    tcp_sequence_links = [
        row
        for row in candidates
        if row["parent_field"] == "tcp_sequence"
        or row["child_field"] == "tcp_sequence"
    ]

    conclusion = (
        f"发现 {len(promotable)} 个可升级键。"
        if promotable
        else (
            "未证明‘动作/响应携带父攻击实例，再由 107-byte 注册生成子对象’这一通用链。"
            "对象注册→damage_object_id→direct37 仍然可靠，但动作/响应→该注册之间缺少可重复的一对一实例键。"
        )
    )
    result = {
        "schema_version": 1,
        "policy": {
            "positive_evidence": (
                "exact integer equality or explicitly tested object arithmetic, "
                "unique within capture+actor, packet-validated child template truth"
            ),
            "forbidden_evidence": [
                "time proximity",
                "resource names",
                "damage fingerprint",
                "damage result group",
                "learned profile",
            ],
            "promotion_gate": (
                "training >=10 truth supports in >=3 captures; validation >=5 in >=2 "
                "captures; zero conflicts and zero ambiguous keys in both; >=2 skills "
                "and >=2 child templates"
            ),
        },
        "conclusion": conclusion,
        "summary": {
            "indexed_captures": len(indexed),
            "captures_with_local_registration": len(audited_paths),
            "audited_captures": len(reports),
            "safe_packet_validated_templates": len(safe_map),
            "child_registrations": registration_count,
            "truth_child_registrations": truth_count,
            "magic_child_registrations": magic_count,
            "candidate_key_count": len(candidates),
            "promotable_key_count": len(promotable),
            "exact_parent_child_object_id_overlap": exact_object_overlap,
        },
        "hypothesis_results": [
            {
                "name": "09 66 / 对象编号或对象基址",
                "result": (
                    f"09 66 字段候选 {len(marker_0966_hypotheses)}，可升级 "
                    f"{sum(int(row['promotable']) for row in marker_0966_hypotheses)}；"
                    f"全部对象编号关系候选 {len(object_hypotheses)}，可升级 "
                    f"{sum(int(row['promotable']) for row in object_hypotheses)}；"
                    f"完整 object ID 精确重叠 {exact_object_overlap} 次。"
                ),
            },
            {
                "name": "effect_resource_id / resource_word",
                "result": (
                    f"候选 {len(effect_hypotheses)}，可升级 "
                    f"{sum(int(row['promotable']) for row in effect_hypotheses)}。"
                ),
            },
            {
                "name": "child_sequence / object±child_sequence",
                "result": (
                    f"候选 {len(sequence_hypotheses)}，可升级 "
                    f"{sum(int(row['promotable']) for row in sequence_hypotheses)}。"
                ),
            },
            {
                "name": "UDP/TCP sequence 或同一批次",
                "result": (
                    "107-byte 注册为 UDP，协议记录没有 TCP sequence；"
                    f"跨端 sequence 候选 {len(tcp_sequence_links)}，可升级 0。"
                ),
            },
        ],
        "safe_template_skill_map": {
            str(key): value for key, value in sorted(safe_map.items())
        },
        "capture_index": indexed,
        "reports": reports,
        "candidate_keys": candidates[:2000],
        "promotable_keys": promotable,
        "counterexamples": counterexamples[:200],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(_render_markdown(result), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
