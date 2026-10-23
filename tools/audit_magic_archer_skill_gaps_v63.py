"""Audit Magic Archer named direct-Hit gaps against exact projectile evidence.

This is an offline audit.  It does not patch production mappings and never
uses a recent-action/time window as positive skill evidence.  The positive
joins are limited to either a fixed skill slot in the physical direct37 row,
or the same full actor ID + damage_object_id generation carried by a complete
typed-09 object record whose Projectile template has an audited client ACT
mapping.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import struct
import sys
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.generated_attack_instance_specs_v576 import (  # noqa: E402
    CLIENT_ACT_GENERATED_PROJECTILE_CHILD_EDGES,
)
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
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
    decode_captured_frame,
    decode_v576_damage_object_registration,
    decode_v576_server_damage_object_registrations,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _raw_damage_invariants,
    _scope_reason,
    _strict_classification,
)


MAGIC = b"DNDP1\0"
FRAME_HEADER = struct.Struct("<dBII")
SKIP_ADAPTERS = frozenset(
    {IDENTITY_TRACE_ADAPTER_INDEX, IDENTITY_METADATA_ADAPTER_INDEX,
     SESSION_EVIDENCE_ADAPTER_INDEX}
)
DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_CAPTURES = (
    "0808星期天黑典翻车.dpscap",
    "0825黑硬女王.dpscap",
    "2026-8-12黑龙经典.dpscap",
    "0817黑典噬魂.dpscap",
    "0809黑典翻车.dpscap",
    "20260822YEP黑硬药剂.dpscap",
)
TARGET_SKILLS = {
    1209: "迅捷箭",
    1210: "追风箭",
    1208: "集中射击",
    1027: "魔法箭矢",
    1001: "二连射",
    1203: "追踪箭",
    1215: "瞄准",
}
EXPECTED_BASELINE_GAPS = {
    1209: 445, 1210: 351, 1208: 342, 1027: 259,
    1001: 184, 1203: 156, 1215: 345,
}
PRIMARY_09_SUBTYPES = frozenset({0x5D, 0x61, 0x6E})
STATIC_SPEC = {int(row.projectile_template_id): row for row in _DAMAGE_OBJECT_SPECS}
STATIC_VALIDATED = (
    frozenset(CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS)
    | frozenset(PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS)
)
PARENTS_BY_CHILD: dict[int, frozenset[int]] = {}
for _parent_template, _child_templates in CLIENT_ACT_GENERATED_PROJECTILE_CHILD_EDGES:
    for _child_template in _child_templates:
        PARENTS_BY_CHILD[_child_template] = frozenset(
            {*PARENTS_BY_CHILD.get(_child_template, ()), int(_parent_template)}
        )

# The client ACT audit resolves Projectile 92 to Ar_Crossbow_D01.act.  Corpus
# fixed-slot rows conflict across named skills, therefore it is deterministic
# only as a crossbow weapon-normal-attack family, never as one named skill.
CROSSBOW_NORMAL_ATTACK_TEMPLATE_ID = 92
CROSSBOW_NORMAL_ATTACK_ID = "basic_attack:crossbow"
CROSSBOW_NORMAL_ATTACK_NAME = "弩系普通攻击"
KNOWN_SKILLS = frozenset(_load_damage_skill_ids(str(DATA_DIR)))
SKILL_CATALOG = _load_skill_catalog(str(DATA_DIR))


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _skill_name(skill_id: int) -> str:
    row = SKILL_CATALOG.get(int(skill_id))
    return str(row[0]) if row else TARGET_SKILLS.get(skill_id, "")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class Typed09:
    capture: str
    packet_index: int
    timestamp_us: int
    owner_actor_id: int
    object_id: int
    template_id: int
    subtype: int
    declared_length: int
    record_offset: int
    accepted_by_current_decoder: bool
    parent_object_id: int
    source_layout: str
    wire_sha256: str
    record_sha256: str
    record_hex: str

    @property
    def position(self) -> tuple[int, int]:
        return self.packet_index, self.record_offset


@dataclass(frozen=True, slots=True)
class RawHit:
    capture: str
    packet_index: int
    timestamp_us: int
    source_actor_id: int
    target_actor_id: int
    object_id: int
    damage: int
    critical: bool
    result_group_id: int
    record_offset: int
    raw_skill_slots: tuple[tuple[int, int], ...]
    wire_sha256: str
    record_sha256: str
    record_hex: str

    @property
    def position(self) -> tuple[int, int]:
        return self.packet_index, self.record_offset


def _iter_packets(path: Path) -> Iterable[Any]:
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
                raise ValueError(f"truncated frame header: {path}")
            timestamp, adapter, original_length, captured_length = FRAME_HEADER.unpack(header)
            if captured_length > 16 * 1024 * 1024:
                raise ValueError(f"abnormal captured frame: {path}")
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                raise ValueError(f"truncated frame body: {path}")
            if adapter in SKIP_ADAPTERS:
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


def _decoded_registrations(
    payload: bytes, direction: str | None,
) -> dict[tuple[int, int, int, int], Any]:
    rows = []
    local = decode_v576_damage_object_registration(payload, direction)
    if local is not None:
        rows.append(local)
    rows.extend(decode_v576_server_damage_object_registrations(
        payload, direction, include_unmapped=True,
    ))
    return {
        (int(row.source_actor_id), int(row.damage_object_id),
         int(row.projectile_template_id), int(row.record_offset)): row
        for row in rows
    }


def _typed09_from_record(
    *, capture: str, packet_index: int, timestamp_us: int,
    typed: Any, accepted: dict[tuple[int, int, int, int], Any], wire_sha256: str,
) -> Typed09 | None:
    raw = bytes(typed.record_bytes)
    # record_bytes starts with the four-byte actor prefix, then type/length.
    if typed.record_type != 9 or len(raw) < 52:
        return None
    owner = _u32(raw, 0)
    if raw[7:12] != bytes(5) or _u32(raw, 48) != owner:
        return None
    object_id, template_id = _u32(raw, 12), _u32(raw, 16)
    if (owner in (0, 0xFFFFFFFF) or not 0 < object_id <= 0xFFFFFF
            or not 0 < template_id <= 0x7FFFFFFF):
        return None
    subtype = int(typed.declared_length)
    marker_offset = int(typed.record_offset)
    key = (owner, object_id, template_id, marker_offset)
    decoded = accepted.get(key)
    # Parent metadata is accepted only when the existing strict protocol
    # decoder validated the complete layout and phase.  Do not independently
    # guess an offset from a partial record.
    parent = int(decoded.parent_damage_object_id) if decoded is not None else 0
    source_layout = str(decoded.layout) if decoded is not None else "raw-complete-typed09"
    # 2023 uses the audited branch-0 shared-arrow tail in both 09/62 and
    # 09/66 complete typed records.  Validate every structural guard on the
    # concrete record before exposing its full parent ID.  This adds metadata
    # to the audit only; production decoder permissions remain unchanged.
    if not parent and template_id == 2023 and subtype in (0x62, 0x66) and len(raw) >= 77:
        candidate, branch, suffix = _u32(raw, 53), _u32(raw, 57), _u32(raw, 73)
        if (
            raw[52] == 0
            and 0 < candidate <= 0xFFFFFF and candidate != object_id
            and branch & 0xFFFF == 0 and branch >> 16 <= 5
            and raw[61:69] == b"\xff" * 8
            and 0 < suffix <= 0xFFFF
        ):
            parent = candidate
            source_layout = f"audit-complete-typed09-{subtype:02x}-branch0-parent"
    return Typed09(
        capture=capture, packet_index=packet_index, timestamp_us=timestamp_us,
        owner_actor_id=owner, object_id=object_id, template_id=template_id,
        subtype=subtype, declared_length=subtype, record_offset=marker_offset,
        accepted_by_current_decoder=decoded is not None,
        parent_object_id=parent,
        source_layout=source_layout,
        wire_sha256=wire_sha256,
        record_sha256=typed.wire_record_sha256, record_hex=raw.hex(),
    )


def _other_registration_rows(
    *, capture: str, packet_index: int, timestamp_us: int, payload: bytes,
    decoded: dict[tuple[int, int, int, int], Any], typed_keys: set[tuple[int, int, int, int]],
    wire_sha256: str,
) -> list[Typed09]:
    """Expose accepted client/wrapped creation layouts omitted by typed09 inventory."""
    rows: list[Typed09] = []
    payload_hash = hashlib.sha256(payload).hexdigest()
    for key, row in decoded.items():
        if key in typed_keys:
            continue
        rows.append(Typed09(
            capture=capture, packet_index=packet_index, timestamp_us=timestamp_us,
            owner_actor_id=int(row.source_actor_id), object_id=int(row.damage_object_id),
            template_id=int(row.projectile_template_id), subtype=-1,
            declared_length=len(payload), record_offset=int(row.record_offset),
            accepted_by_current_decoder=True,
            parent_object_id=int(row.parent_damage_object_id or 0),
            source_layout=str(row.layout), wire_sha256=wire_sha256,
            record_sha256=payload_hash, record_hex=payload.hex(),
        ))
    return rows


def _scan_wire(path: Path) -> dict[str, Any]:
    objects: list[Typed09] = []
    hits: list[RawHit] = []
    counters: Counter[str] = Counter()
    packet_index = 0
    first_timestamp_us = 0
    for packet_index, packet in enumerate(_iter_packets(path), 1):
        timestamp_us = round(float(packet.timestamp_seconds) * 1_000_000)
        if not first_timestamp_us:
            first_timestamp_us = timestamp_us
        if packet.transport != "UDP":
            counters["non_udp_logical_packets"] += 1
            continue
        direction = packet.inferred_direction()
        inventory = inspect_udp_damage_records(bytes(packet.payload), direction)
        if not inventory.complete:
            counters[f"inventory_rejected:{inventory.rejection_reason}"] += 1
            continue
        counters["complete_typed_messages"] += 1
        payload = bytes(packet.payload)
        accepted = _decoded_registrations(payload, direction)
        typed_keys: set[tuple[int, int, int, int]] = set()
        for typed in inventory.typed_records:
            row = _typed09_from_record(
                capture=path.name, packet_index=packet_index,
                timestamp_us=timestamp_us, typed=typed, accepted=accepted,
                wire_sha256=inventory.wire_sha256,
            )
            if row is not None:
                objects.append(row)
                typed_keys.add((row.owner_actor_id, row.object_id, row.template_id,
                                row.record_offset))
                counters[f"typed09:{row.subtype}"] += 1
                if not row.accepted_by_current_decoder:
                    counters[f"typed09_unaccepted:{row.subtype}"] += 1
        for row in _other_registration_rows(
            capture=path.name, packet_index=packet_index, timestamp_us=timestamp_us,
            payload=payload, decoded=accepted, typed_keys=typed_keys,
            wire_sha256=inventory.wire_sha256,
        ):
            objects.append(row)
            counters[f"other_registration:{row.source_layout}"] += 1
        for row in inventory.damage_records:
            if row.family != "direct37" or not row.admissible_fields:
                continue
            hits.append(RawHit(
                capture=path.name, packet_index=packet_index,
                timestamp_us=timestamp_us, source_actor_id=int(row.source_actor_id),
                target_actor_id=int(row.target_actor_id),
                object_id=int(row.damage_object_id or 0), damage=int(row.damage),
                critical=bool(row.critical), result_group_id=int(row.damage_result_group_id),
                record_offset=int(row.record_offset), raw_skill_slots=tuple(row.raw_skill_slots),
                wire_sha256=row.wire_sha256, record_sha256=row.wire_record_sha256,
                record_hex=row.record_bytes.hex(),
            ))
            counters["direct37"] += 1
    return {
        "objects": objects, "hits": hits, "counts": dict(counters),
        "packet_count": packet_index, "first_timestamp_us": first_timestamp_us,
    }


def _event_key(event: CombatEvent) -> tuple[int, int, int, int, bool, int, int, int]:
    return (
        int(event.timestamp_ms),
        int(event.wire_source_actor_id or _actor(event.source_id)),
        int(event.wire_target_actor_id or _actor(event.target_id)),
        int(event.amount), bool(event.wire_critical if event.wire_critical is not None else event.critical),
        int(event.wire_result_group_id or 0), int(event.wire_damage_object_id or 0),
        int(event.wire_record_offset or 0),
    )


def _raw_hit_key(hit: RawHit, origin_us: int) -> tuple[int, int, int, int, bool, int, int, int]:
    return (
        round((hit.timestamp_us - origin_us) / 1000.0),
        hit.source_actor_id, hit.target_actor_id, hit.damage,
        hit.critical, hit.result_group_id, hit.object_id, hit.record_offset,
    )


def _decode_events(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    try:
        source_events = source.poll()
        origin_us = round(float(source._decoder.first_timestamp or 0.0) * 1_000_000)
    finally:
        source.close()
    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    events = list(aggregator.snapshot(include_events=True).combat_events)
    return {
        "source_events": source_events, "events": events, "origin_us": origin_us,
        "raw_source_invariants": _raw_damage_invariants(source_events),
        "aggregated_invariants": _raw_damage_invariants(events),
    }


def _valid_fixed_skill(hit: RawHit | None) -> tuple[int, int]:
    if hit is None:
        return 0, -1
    values = [(offset, value) for offset, value in hit.raw_skill_slots
              if value not in (0, 0xFFFFFFFF) and value in KNOWN_SKILLS]
    unique = {value for _, value in values}
    if len(unique) != 1:
        return 0, -1
    value = next(iter(unique))
    return value, next(offset for offset, candidate in values if candidate == value)


def _mapping_for_template(template_id: int) -> tuple[int, frozenset[int]]:
    spec = STATIC_SPEC.get(int(template_id))
    if spec is None:
        return 0, frozenset()
    return int(spec.canonical_skill_id), frozenset(map(int, spec.skill_ids))


def _exact_parent_row(
    child: Typed09,
    objects_by_key: dict[tuple[int, int], list[Typed09]],
    positions: dict[tuple[int, int], list[tuple[int, int]]],
) -> Typed09 | None:
    """Resolve the child's explicit full parent ID to its prior generation."""
    if child.parent_object_id <= 0:
        return None
    key = (child.owner_actor_id, child.parent_object_id)
    rows = objects_by_key.get(key, ())
    if not rows:
        return None
    index = bisect.bisect_left(positions[key], child.position) - 1
    return rows[index] if index >= 0 else None


def _record_fixture(
    event: CombatEvent, hit: RawHit | None, obj: Typed09 | None,
    *, decision: str, proposed_skill_id: int | str, proposed_skill_name: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "capture": hit.capture if hit else "",
        "current": {
            "skill_id": int(str(event.skill_id or 0)) if str(event.skill_id or "").isdigit() else 0,
            "skill_name": str(event.skill_name or ""),
            "method": str(event.attribution_method or ""),
        },
        "decision": decision,
        "proposed_skill_id": proposed_skill_id,
        "proposed_skill_name": proposed_skill_name,
        "reason": reason,
        "physical_hit": (asdict(hit) if hit else None),
        "typed09_object": (asdict(obj) if obj else None),
    }


def _analyse_capture(path: Path) -> dict[str, Any]:
    decoded = _decode_events(path)
    wire = _scan_wire(path)
    raw_hits: list[RawHit] = wire["hits"]
    hit_queues: dict[tuple[int, ...], deque[RawHit]] = defaultdict(deque)
    for hit in raw_hits:
        hit_queues[_raw_hit_key(hit, int(decoded["origin_us"]))].append(hit)

    objects_by_key: dict[tuple[int, int], list[Typed09]] = defaultdict(list)
    for row in wire["objects"]:
        objects_by_key[(row.owner_actor_id, row.object_id)].append(row)
    positions: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for key, rows in objects_by_key.items():
        rows.sort(key=lambda row: row.position)
        positions[key] = [row.position for row in rows]

    per_skill: dict[int, Counter[str]] = defaultdict(Counter)
    fixtures: dict[int, list[dict[str, Any]]] = defaultdict(list)
    fixture_keys: dict[int, set[tuple[str, int, str]]] = defaultdict(set)
    subtype_rows: Counter[tuple[int, int, int, bool]] = Counter()
    unmatched_events = 0
    shadow_changes: list[tuple[CombatEvent, str, str]] = []
    target_gap_events = 0

    for event in decoded["events"]:
        if not isinstance(event, CombatEvent) or str(event.job or "").strip() != "魔羽":
            continue
        if _scope_reason(event) != "included_positive_direct_hit":
            continue
        try:
            skill_id = int(str(event.skill_id or 0), 0)
        except ValueError:
            skill_id = 0
        if skill_id not in TARGET_SKILLS or _strict_classification(event) == "deterministic":
            continue
        target_gap_events += 1
        counts = per_skill[skill_id]
        counts["gap_hits"] += 1
        counts["gap_damage"] += int(event.amount)
        counts[f"method:{event.attribution_method}"] += 1

        event_key = _event_key(event)
        queue = hit_queues.get(event_key)
        hit = queue.popleft() if queue else None
        if hit is None:
            # Capture timestamps are converted to integer milliseconds by the
            # decoder.  Check only the two adjacent millisecond buckets while
            # retaining every other physical field; this is a locator, never
            # positive skill evidence.
            for delta in (-1, 1):
                adjacent = (event_key[0] + delta, *event_key[1:])
                queue = hit_queues.get(adjacent)
                if queue:
                    hit = queue.popleft()
                    break
        if hit is None:
            unmatched_events += 1
            counts["unmatched_physical_row"] += 1
            continue

        fixed_skill, fixed_offset = _valid_fixed_skill(hit)
        object_row = None
        key = (hit.source_actor_id, hit.object_id)
        rows = objects_by_key.get(key, ())
        if rows:
            index = bisect.bisect_right(positions[key], hit.position) - 1
            if index >= 0:
                object_row = rows[index]

        decision = "unclosed"
        proposed: int | str = 0
        proposed_name = ""
        reason = "no prior complete typed09 object generation and no unique fixed skill slot"
        if fixed_skill > 0:
            proposed = fixed_skill
            proposed_name = _skill_name(fixed_skill)
            decision = "closeable" if fixed_skill == skill_id else "correctable"
            reason = f"physical direct37 fixed skill slot +{fixed_offset}"
        elif object_row is not None:
            canonical, aliases = _mapping_for_template(object_row.template_id)
            subtype_rows[(object_row.subtype, object_row.template_id, canonical,
                          object_row.accepted_by_current_decoder)] += 1
            counts["has_prior_typed09"] += 1
            counts[f"typed09_subtype:{object_row.subtype}"] += 1
            counts[f"typed09_template:{object_row.template_id}"] += 1
            if (
                object_row.template_id == CROSSBOW_NORMAL_ATTACK_TEMPLATE_ID
                and (
                    object_row.accepted_by_current_decoder
                    or object_row.subtype in (0x66, 0x6A)
                )
            ):
                proposed = CROSSBOW_NORMAL_ATTACK_ID
                proposed_name = CROSSBOW_NORMAL_ATTACK_NAME
                decision = "correctable_to_weapon_normal_attack"
                reason = (
                    "exact actor+object generation resolves Projectile 92; client ACT "
                    "identifies Ar_Crossbow_D01.act and corpus conflicts exclude a named skill"
                )
            elif canonical and object_row.template_id in STATIC_VALIDATED:
                if object_row.template_id in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS:
                    parent_row = _exact_parent_row(object_row, objects_by_key, positions)
                    allowed_parents = PARENTS_BY_CHILD.get(object_row.template_id, frozenset())
                    parent_canonical, parent_aliases = (
                        _mapping_for_template(parent_row.template_id) if parent_row else (0, frozenset())
                    )
                    if (
                        parent_row is not None
                        and parent_row.template_id in allowed_parents
                        and parent_row.accepted_by_current_decoder
                        and parent_canonical == canonical
                        and aliases.intersection(parent_aliases)
                    ):
                        proposed = canonical
                        proposed_name = _skill_name(canonical)
                        decision = "closeable" if canonical == skill_id else "correctable"
                        counts["parent_chain_closeable"] += 1
                        reason = (
                            "explicit full parent object ID + same-owner prior generation + "
                            f"client ACT edge {parent_row.template_id}->{object_row.template_id}"
                        )
                    else:
                        decision = "parent_lineage_required"
                        if object_row.parent_object_id <= 0:
                            parent_failure = "validated_record_has_no_parent_id"
                        elif parent_row is None:
                            parent_failure = "explicit_parent_generation_not_observed"
                        elif parent_row.template_id not in allowed_parents:
                            parent_failure = "parent_template_not_in_client_act_edge"
                        elif not parent_row.accepted_by_current_decoder:
                            parent_failure = "parent_generation_not_protocol_accepted"
                        else:
                            parent_failure = "parent_and_child_skill_family_conflict"
                        counts[f"parent_failure:{parent_failure}"] += 1
                        reason = (
                            "shared child Projectile lacks a complete exact parent generation chain: "
                            + parent_failure
                        )
                elif object_row.accepted_by_current_decoder or object_row.subtype in PRIMARY_09_SUBTYPES:
                    proposed = canonical
                    proposed_name = _skill_name(canonical)
                    decision = "closeable" if canonical == skill_id else "correctable"
                    reason = "exact actor+object generation and statically validated Projectile"
                else:
                    proposed = canonical
                    proposed_name = _skill_name(canonical)
                    decision = "new_subtype_candidate"
                    reason = "complete typed09 generation and static Projectile map; subtype is not yet admitted"
            elif canonical:
                decision = "template_provisional"
                proposed = canonical
                proposed_name = _skill_name(canonical)
                reason = "Projectile mapping exists but is not independently validated"
            else:
                decision = "template_unmapped"
                reason = "complete typed09 generation exposes an unknown Projectile template"

        counts[decision] += 1
        if proposed:
            counts[f"proposed_skill:{proposed}"] += 1
        if decision in {"closeable", "correctable", "correctable_to_weapon_normal_attack"}:
            shadow_changes.append((event, str(proposed), proposed_name))
        fixture_key = (
            decision, int(object_row.template_id if object_row else 0), str(proposed)
        )
        if hit is not None and len(fixtures[skill_id]) < 24 and fixture_key not in fixture_keys[skill_id]:
            fixture_keys[skill_id].add(fixture_key)
            fixtures[skill_id].append(_record_fixture(
                event, hit, object_row, decision=decision,
                proposed_skill_id=proposed, proposed_skill_name=proposed_name,
                reason=reason,
            ))

    # A shadow relabel changes only display skill identity.  Prove the exact
    # physical multiset before/after is conserved rather than comparing totals.
    changed_by_identity = {id(event): (skill, name) for event, skill, name in shadow_changes}
    shadow_events = [
        replace(event, skill_id=changed_by_identity[id(event)][0],
                skill_name=changed_by_identity[id(event)][1])
        if id(event) in changed_by_identity else event
        for event in decoded["events"]
    ]
    before = _raw_damage_invariants(decoded["events"])
    after = _raw_damage_invariants(shadow_events)
    if before != after:
        raise AssertionError(f"physical conservation failed for {path.name}")

    return {
        "capture": str(path), "capture_sha256": _sha(path),
        "wire_counts": wire["counts"], "packet_count": wire["packet_count"],
        "target_gap_events": target_gap_events,
        "unmatched_physical_rows": unmatched_events,
        "per_skill": {str(k): dict(v) for k, v in per_skill.items()},
        "fixtures": {str(k): v for k, v in fixtures.items()},
        "typed09_gap_pairs": [
            {"subtype": key[0], "template_id": key[1], "canonical_skill_id": key[2],
             "accepted_by_current_decoder": key[3], "hits": value}
            for key, value in subtype_rows.most_common()
        ],
        "raw_source_invariants": decoded["raw_source_invariants"],
        "aggregated_invariants": decoded["aggregated_invariants"],
        "shadow_relabel_invariants": after,
        "shadow_relabels": len(shadow_changes),
    }


def _merge(reports: list[dict[str, Any]], baseline: dict[str, Any] | None) -> dict[str, Any]:
    per_skill: dict[int, Counter[str]] = defaultdict(Counter)
    fixtures: dict[int, list[dict[str, Any]]] = defaultdict(list)
    pair_files: dict[tuple[int, int, int, bool], set[str]] = defaultdict(set)
    pair_hits: Counter[tuple[int, int, int, bool]] = Counter()
    for report in reports:
        for skill, values in report["per_skill"].items():
            per_skill[int(skill)].update(values)
        for skill, values in report["fixtures"].items():
            fixtures[int(skill)].extend(values)
        for row in report["typed09_gap_pairs"]:
            key = (int(row["subtype"]), int(row["template_id"]),
                   int(row["canonical_skill_id"]), bool(row["accepted_by_current_decoder"]))
            pair_hits[key] += int(row["hits"])
            pair_files[key].add(report["capture"])

    baseline_by_capture = {
        str(Path(row["capture_path"]).resolve()).casefold(): row
        for row in (baseline or {}).get("capture_manifest", ())
    }
    baseline_checks = []
    for report in reports:
        old = baseline_by_capture.get(str(Path(report["capture"]).resolve()).casefold())
        baseline_checks.append({
            "capture": report["capture"], "baseline_found": old is not None,
            "capture_sha256_equal": bool(old and old.get("capture_sha256") == report["capture_sha256"]),
            "raw_source_invariants_equal": bool(old and old.get("raw_source_invariants") == report["raw_source_invariants"]),
            "aggregated_invariants_equal": bool(old and old.get("aggregated_invariants") == report["aggregated_invariants"]),
        })

    skill_rows = []
    for skill_id in TARGET_SKILLS:
        counts = per_skill[skill_id]
        closeable = int(counts["closeable"])
        correctable = int(counts["correctable"])
        normal_attack = int(counts["correctable_to_weapon_normal_attack"])
        candidates = int(counts["new_subtype_candidate"])
        unclosed = int(counts["unclosed"] + counts["template_unmapped"]
                       + counts["template_provisional"] + counts["parent_lineage_required"]
                       + counts["unmatched_physical_row"])
        proposals = {
            key.split(":", 1)[1]: value for key, value in counts.items()
            if key.startswith("proposed_skill:")
        }
        skill_rows.append({
            "skill_id": skill_id, "skill_name": TARGET_SKILLS[skill_id],
            "expected_v57_gap_hits": EXPECTED_BASELINE_GAPS[skill_id],
            "observed_current_gap_hits": int(counts["gap_hits"]),
            "closeable_same_skill_hits": closeable,
            "correctable_to_other_skill_hits": correctable,
            "correctable_to_weapon_normal_attack_hits": normal_attack,
            "exact_parent_chain_hits": int(counts["parent_chain_closeable"]),
            "new_typed09_subtype_candidate_hits": candidates,
            "still_unclosed_hits": unclosed,
            "proposed_skill_counts": proposals,
            "method_counts": {key.split(":", 1)[1]: value for key, value in counts.items()
                              if key.startswith("method:")},
            "template_counts": {key.split(":", 1)[1]: value for key, value in counts.items()
                                if key.startswith("typed09_template:")},
            "decision_counts": {
                key: value for key, value in counts.items()
                if key in {
                    "closeable", "correctable", "correctable_to_weapon_normal_attack",
                    "new_subtype_candidate", "parent_lineage_required",
                    "template_provisional", "template_unmapped", "unclosed",
                    "unmatched_physical_row",
                }
            },
            "parent_failure_counts": {
                key.split(":", 1)[1]: value for key, value in counts.items()
                if key.startswith("parent_failure:")
            },
            "fixtures": fixtures[skill_id][:24],
        })

    pair_rows = [
        {"subtype": key[0], "template_id": key[1], "canonical_skill_id": key[2],
         "canonical_skill_name": _skill_name(key[2]),
         "accepted_by_current_decoder": key[3], "gap_hits": count,
         "capture_count": len(pair_files[key]), "captures": sorted(pair_files[key])}
        for key, count in pair_hits.most_common()
    ]
    physical_before = Counter()
    physical_after = Counter()
    for report in reports:
        for key in ("rows", "real_hits", "damage"):
            physical_before[key] += int(report["aggregated_invariants"][key])
            physical_after[key] += int(report["shadow_relabel_invariants"][key])

    baseline_magic = next(
        (row for row in (baseline or {}).get("summary", ()) if row.get("职业") == "魔羽"),
        None,
    )
    exact_named_added = sum(
        row["closeable_same_skill_hits"] + row["correctable_to_other_skill_hits"]
        for row in skill_rows
    )
    normal_attack_added = sum(
        row["correctable_to_weapon_normal_attack_hits"] for row in skill_rows
    )
    coverage_projection = None
    if baseline_magic:
        total = int(baseline_magic["总直接Hit"])
        old_deterministic = int(baseline_magic["确定性已归因"])
        coverage_projection = {
            "baseline_total_direct_hits": total,
            "baseline_deterministic_hits": old_deterministic,
            "baseline_deterministic_coverage_percent": round(old_deterministic * 100 / total, 6),
            "new_exact_named_skill_hits_in_scope": exact_named_added,
            "new_crossbow_normal_attack_family_hits_in_scope": normal_attack_added,
            "projected_deterministic_hits_after_both": (
                old_deterministic + exact_named_added + normal_attack_added
            ),
            "projected_deterministic_coverage_percent_after_both": round(
                (old_deterministic + exact_named_added + normal_attack_added) * 100 / total, 6
            ),
            "remaining_global_gap_hits": (
                total - old_deterministic - exact_named_added - normal_attack_added
            ),
            "note": "projection only; shared production attribution is unchanged",
        }

    return {
        "schema": "magic-archer-skill-gaps-v63",
        "policy": {
            "positive_evidence": [
                "physical direct37 fixed skill slot",
                "same full actor ID + damage_object_id generation + validated Projectile template",
            ],
            "excluded_as_positive_evidence": [
                "2.5-second or any recent-action window", "damage amount similarity",
                "current inferred skill label", "result-group proximity",
            ],
            "production_changed": False,
        },
        "client_static_evidence": {
            "crossbow_normal_attack": {
                "projectile_template_id": CROSSBOW_NORMAL_ATTACK_TEMPLATE_ID,
                "parent_action_row_id": 268465691,
                "act_name": "Ar_Crossbow_D01.act",
                "classification": CROSSBOW_NORMAL_ATTACK_ID,
                "reason": (
                    "the client ACT identifies a weapon normal-attack family; fixed-slot "
                    "corpus conflicts prohibit assigning it to one named skill"
                ),
                "source": "outputs/audit_remaining_registration_handoff_20260907/handoff.json",
            },
            "concentrated_shot_child": {
                "parent_projectile_template_id": 2022,
                "child_projectile_template_id": 2023,
                "canonical_skill_id": 1208,
                "act_edge_source": (
                    "dragon_nest_builder/live_dps/"
                    "generated_attack_instance_specs_v576.py"
                ),
                "runtime_requirements": [
                    "same owner actor ID", "explicit full parent damage_object_id",
                    "prior live parent generation", "client ACT exact parent-child edge",
                ],
            },
        },
        "captures": len(reports),
        "coverage_projection": coverage_projection,
        "skills": skill_rows,
        "typed09_pairs_in_gap_hits": pair_rows,
        "baseline_checks": baseline_checks,
        "conservation": {
            "before": dict(physical_before), "after_shadow_relabel": dict(physical_after),
            "physical_rows_conserved": physical_before == physical_after,
            "per_capture_exact_multiset_conserved": all(
                row["aggregated_invariants"] == row["shadow_relabel_invariants"]
                for row in reports
            ),
        },
        "reports": reports,
    }


def _render(result: dict[str, Any]) -> str:
    lines = [
        "# 魔羽主要技能直接 Hit 缺口专项审计 v63", "",
        f"- 抓包：{result['captures']} 份", "- 生产代码：未修改",
        "- 正向证据只接受 direct37 固定技能槽，或完整角色 ID＋对象 ID＋已验证 Projectile 模板。",
        "- 2.5 秒及其他时间邻近规则完全不作为技能归因证据。", "",
        "## 技能结果", "",
        "| 当前标签 | v57缺口 | 本次复现 | 可直接闭合同技能 | 可纠正到别技能 | 应纠正为弩系普攻 | 其中父子链闭合 | 新typed09候选 | 仍未闭合 | 建议去向 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in result["skills"]:
        proposal_parts = []
        for skill, count in sorted(row["proposed_skill_counts"].items(),
                                   key=lambda item: -int(item[1])):
            if skill == CROSSBOW_NORMAL_ATTACK_ID:
                label = CROSSBOW_NORMAL_ATTACK_NAME
            else:
                label = f"{_skill_name(int(skill))}({skill})"
            proposal_parts.append(f"{label}={count}")
        proposals = ", ".join(proposal_parts) or "-"
        lines.append(
            f"| {row['skill_name']} ({row['skill_id']}) | {row['expected_v57_gap_hits']} | "
            f"{row['observed_current_gap_hits']} | {row['closeable_same_skill_hits']} | "
            f"{row['correctable_to_other_skill_hits']} | "
            f"{row['correctable_to_weapon_normal_attack_hits']} | "
            f"{row['exact_parent_chain_hits']} | {row['new_typed09_subtype_candidate_hits']} | "
            f"{row['still_unclosed_hits']} | {proposals} |"
        )
    lines.extend(["", "## typed09 缺口对象", "",
                  "| 子类型 | Projectile | 静态技能 | Hit | 文件 | 当前解析器接收 |",
                  "|---:|---:|---|---:|---:|---|"])
    for row in result["typed09_pairs_in_gap_hits"][:30]:
        lines.append(
            f"| 0x{row['subtype']:02X} | {row['template_id']} | "
            f"{row['canonical_skill_name']} ({row['canonical_skill_id']}) | "
            f"{row['gap_hits']} | {row['capture_count']} | "
            f"{'是' if row['accepted_by_current_decoder'] else '否'} |"
        )
    projection = result.get("coverage_projection") or {}
    if projection:
        lines.extend([
            "", "## 魔羽整体覆盖率投影", "",
            f"- v57 基线：{projection['baseline_deterministic_hits']} / "
            f"{projection['baseline_total_direct_hits']} = "
            f"{projection['baseline_deterministic_coverage_percent']:.6f}% 。",
            f"- 本专项新增具名技能确定 Hit：{projection['new_exact_named_skill_hits_in_scope']}。",
            f"- 本专项纠正为弩系普通攻击族：{projection['new_crossbow_normal_attack_family_hits_in_scope']}。",
            f"- 若生产后续采用全部已审计证据："
            f"{projection['projected_deterministic_hits_after_both']} / "
            f"{projection['baseline_total_direct_hits']} = "
            f"{projection['projected_deterministic_coverage_percent_after_both']:.6f}%；"
            f"仍有全局缺口 {projection['remaining_global_gap_hits']} Hit。",
            "- 这是离线影子投影，本次没有改动生产归因。",
        ])
    lines.extend(["", "## 守恒", "",
                  f"- 影子纠错前后物理行/Hit/伤害守恒：{result['conservation']['physical_rows_conserved']}。",
                  f"- 每份抓包的完整物理多重集守恒：{result['conservation']['per_capture_exact_multiset_conserved']}。",
                  "- 真实 fixture（包含原始 typed09/direct37 记录十六进制与 SHA-256）保存在 JSON 的每技能 fixtures 中。",
                  "", "## 判读", ""])
    aim = next(row for row in result["skills"] if row["skill_id"] == 1215)
    lines.append(
        f"- ‘瞄准’缺口中有 {aim['correctable_to_weapon_normal_attack_hits']} Hit "
        "被同对象链证明为弩系普通攻击；瞄准本身是非伤害技能，当前伤害标签不成立。"
    )
    lines.extend([
        "- `new_typed09_subtype_candidate` 表示对象结构和静态模板都已出现，但该 marker/长度组合尚未进入当前解析器；它不是时间推断，也不会在本报告中冒充已闭合。",
        "- 本报告只提供生产修复前的证据清单，没有修改共享归因实现。", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="*", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--baseline", type=Path,
        default=ROOT / "outputs" / "native_result_key_v57_20260909" /
        "profession_direct_damage_report.json",
    )
    args = parser.parse_args()
    captures = args.captures or [DEFAULT_CAPTURE_ROOT / name for name in DEFAULT_CAPTURES]
    missing = [str(path) for path in captures if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing captures: " + ", ".join(missing))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.is_file() else None
    reports = []
    for path in captures:
        print(f"audit {path.name}", flush=True)
        reports.append(_analyse_capture(path.resolve()))
    result = _merge(reports, baseline)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (args.output_dir / "REPORT.md").write_text(_render(result), encoding="utf-8")
    summary = {
        "captures": result["captures"],
        "skills": [{key: row[key] for key in (
            "skill_id", "skill_name", "observed_current_gap_hits",
            "closeable_same_skill_hits", "correctable_to_other_skill_hits",
            "correctable_to_weapon_normal_attack_hits", "exact_parent_chain_hits",
            "new_typed09_subtype_candidate_hits", "still_unclosed_hits",
        )} for row in result["skills"]],
        "conservation": result["conservation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
