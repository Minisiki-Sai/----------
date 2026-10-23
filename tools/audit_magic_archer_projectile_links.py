from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
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
    decode_v576_embedded_direct_damage_events,
    decode_v576_server_damage_object_registrations,
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

# Magic Archer base/advanced damage skills observed in the current corpus.
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

MAGIC_ARCHER_SPECS = tuple(
    spec
    for spec in _DAMAGE_OBJECT_SPECS
    if spec.canonical_skill_id in MAGIC_ARCHER_SKILL_IDS
    or bool(spec.skill_ids & MAGIC_ARCHER_SKILL_IDS)
)
TEMPLATE_IDS = frozenset(spec.projectile_template_id for spec in MAGIC_ARCHER_SPECS)
SPEC_BY_TEMPLATE = {spec.projectile_template_id: spec for spec in MAGIC_ARCHER_SPECS}
SKILL_CATALOG = _load_skill_catalog(str(DATA_DIR))
FIELD_RESPONSE_CODES = frozenset({"134a00", "134e00", "134700"})
SERVER_OBJECT_MARKERS = (
    b"\x09\x5d\x00",
    b"\x09\x61\x00",
    b"\x09\x6e\x00",
    b"\x09\x6a\x00",
)
DIRECT37_MARKER = b"\x08\x73\x00"
# Time is never positive skill evidence.  This upper bound only rejects an
# old object generation when a reused runtime ID was not preceded by a
# decodable replacement registration.  All accepted joins still require the
# exact full owner_actor_id + damage_object_id pair.
MAX_REGISTRATION_GENERATION_AGE_MS = 30_000


def _skill_name(skill_id: int) -> str:
    row = SKILL_CATALOG.get(int(skill_id))
    return str(row[0]) if row else ""


@dataclass(frozen=True, slots=True)
class Registration:
    packet_index: int
    timestamp_ms: int
    owner_actor_id: int
    object_id: int
    template_id: int
    source_layout: str
    effect_resource_id: int
    child_sequence: int
    direction: str
    signature: str
    xor_phase: int
    record_offset: int


@dataclass(frozen=True, slots=True)
class Action:
    packet_index: int
    timestamp_ms: int
    owner_actor_id: int
    skill_id: int
    kind: str


@dataclass(frozen=True, slots=True)
class Response:
    packet_index: int
    timestamp_ms: int
    owner_actor_id: int
    skill_id: int
    effect_code: str
    xor_phase: int
    record_offset: int
    effect_instance_id: int
    skill_level_row_id: int
    effect_class_id: int


@dataclass(frozen=True, slots=True)
class DirectHit:
    packet_index: int
    timestamp_ms: int
    source_actor_id: int
    target_actor_id: int
    object_id: int
    damage: int
    skill_id: int
    skill_id_exact: bool
    direction: str
    signature: str
    xor_phase: int
    record_offset: int
    skill_offset: int


def _server_registration_wire_position(
    payload: bytes,
    *,
    owner_actor_id: int,
    object_id: int,
    template_id: int,
) -> tuple[int, int]:
    for xor_phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, xor_phase)
        for marker in SERVER_OBJECT_MARKERS:
            offset = body.find(marker, 9)
            while offset >= 0:
                if (
                    offset >= 4
                    and offset + 48 <= len(body)
                    and int.from_bytes(body[offset - 4 : offset], "little")
                    == owner_actor_id
                    and int.from_bytes(body[offset + 8 : offset + 12], "little")
                    == object_id
                    and int.from_bytes(body[offset + 12 : offset + 16], "little")
                    == template_id
                    and int.from_bytes(body[offset + 44 : offset + 48], "little")
                    == owner_actor_id
                ):
                    return xor_phase, offset
                offset = body.find(marker, offset + 1)
    return -1, -1


def _direct37_wire_position(
    payload: bytes,
    *,
    source_actor_id: int,
    object_id: int,
    damage: int,
    record_offset: int,
    skill_id: int,
) -> tuple[int, int]:
    for xor_phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, xor_phase)
        offsets = (
            (record_offset,)
            if record_offset >= 9
            else tuple(
                offset
                for offset in range(9, len(body) - 2)
                if body[offset : offset + 3] == DIRECT37_MARKER
            )
        )
        for offset in offsets:
            if (
                offset + 27 > len(body)
                or body[offset : offset + 3] != DIRECT37_MARKER
                or int.from_bytes(body[offset + 8 : offset + 12], "little")
                != source_actor_id
                or int.from_bytes(body[offset + 13 : offset + 17], "little")
                != object_id
                or int.from_bytes(body[offset + 19 : offset + 27], "little")
                != damage
            ):
                continue
            skill_offset = -1
            if skill_id > 0:
                next_marker = body.find(DIRECT37_MARKER, offset + 1)
                boundary = len(body) if next_marker < 0 else next_marker
                for relative in (102, 114):
                    candidate = offset + relative
                    if (
                        candidate + 4 <= boundary
                        and int.from_bytes(body[candidate : candidate + 4], "little")
                        == skill_id
                    ):
                        skill_offset = candidate
                        break
            return xor_phase, skill_offset
    return -1, -1


def _iter_logical_packets(path: Path) -> Iterable[Any]:
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError("not a DNDP1 capture")
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
                raise ValueError("abnormal frame length")
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


def _action_key(action: Action) -> tuple[int, int, int, int, str]:
    return (
        action.packet_index,
        action.timestamp_ms,
        action.owner_actor_id,
        action.skill_id,
        action.kind,
    )


def _audit_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    known_skill_ids = _load_damage_skill_ids(str(DATA_DIR))
    all_registrations: list[Registration] = []
    actions: list[Action] = []
    responses: list[Response] = []
    direct_hits: list[DirectHit] = []
    seen_registrations: set[tuple[int, int, int, int, str]] = set()
    seen_actions: set[tuple[int, int, int, int, str]] = set()
    seen_responses: set[tuple[int, int, int, int, str, int]] = set()
    seen_hits: set[tuple[int, int, int, int, int, int]] = set()
    packet_count = 0

    for packet_count, packet in enumerate(_iter_logical_packets(path), start=1):
        payload = bytes(packet.payload)
        inferred_direction = packet.inferred_direction()
        direction = str(inferred_direction or "unknown")
        signature = payload[5:9].hex() if len(payload) >= 9 else ""
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)

        local = decode_v576_damage_object_registration(payload, inferred_direction)
        if local is not None:
            row = Registration(
                packet_index=packet_count,
                timestamp_ms=timestamp_ms,
                owner_actor_id=int(local.source_actor_id),
                object_id=int(local.damage_object_id),
                template_id=int(local.projectile_template_id),
                source_layout="c2s_107",
                effect_resource_id=int(local.effect_resource_id),
                child_sequence=int(local.child_sequence),
                direction=direction,
                signature=signature,
                xor_phase=int(local.xor_phase),
                record_offset=19,
            )
            key = (
                row.packet_index,
                row.owner_actor_id,
                row.object_id,
                row.template_id,
                row.source_layout,
            )
            if key not in seen_registrations:
                seen_registrations.add(key)
                all_registrations.append(row)

        for server in decode_v576_server_damage_object_registrations(
            payload,
            inferred_direction,
            include_unmapped=True,
        ):
            xor_phase, record_offset = _server_registration_wire_position(
                payload,
                owner_actor_id=int(server.source_actor_id),
                object_id=int(server.damage_object_id),
                template_id=int(server.projectile_template_id),
            )
            row = Registration(
                packet_index=packet_count,
                timestamp_ms=timestamp_ms,
                owner_actor_id=int(server.source_actor_id),
                object_id=int(server.damage_object_id),
                template_id=int(server.projectile_template_id),
                source_layout="s2c_object_state",
                effect_resource_id=0,
                child_sequence=0,
                direction=direction,
                signature=signature,
                xor_phase=xor_phase,
                record_offset=record_offset,
            )
            key = (
                row.packet_index,
                row.owner_actor_id,
                row.object_id,
                row.template_id,
                row.source_layout,
            )
            if key not in seen_registrations:
                seen_registrations.add(key)
                all_registrations.append(row)

        for decoded in decode_v576_structured_actions(
            payload,
            inferred_direction,
            known_skill_ids,
            (),
            allow_unknown_actor_ids=True,
        ):
            actor_id = int(decoded.source_actor_id or 0)
            skill_id = int(decoded.skill_id or 0)
            if actor_id <= 0 or skill_id <= 0:
                continue
            action = Action(
                packet_index=packet_count,
                timestamp_ms=timestamp_ms,
                owner_actor_id=actor_id,
                skill_id=skill_id,
                kind=str(decoded.kind or ""),
            )
            key = _action_key(action)
            if key not in seen_actions:
                seen_actions.add(key)
                actions.append(action)

        for decoded in decode_v576_status_events(
            payload,
            inferred_direction,
            known_skill_ids,
        ):
            skill_id = int(decoded.skill_id or 0)
            source_actor_id = int(decoded.source_actor_id or 0)
            effect_code = str(decoded.effect_code or "").casefold()
            if (
                skill_id not in MAGIC_ARCHER_SKILL_IDS
                or source_actor_id <= 0
                or effect_code not in FIELD_RESPONSE_CODES
            ):
                continue
            response = Response(
                packet_index=packet_count,
                timestamp_ms=timestamp_ms,
                owner_actor_id=source_actor_id,
                skill_id=skill_id,
                effect_code=effect_code,
                xor_phase=int(decoded.xor_phase),
                record_offset=int(decoded.record_offset),
                effect_instance_id=int(decoded.effect_instance_id or 0),
                skill_level_row_id=int(decoded.skill_level_row_id or 0),
                effect_class_id=int(decoded.effect_class_id or 0),
            )
            key = (
                response.packet_index,
                response.owner_actor_id,
                response.skill_id,
                response.record_offset,
                response.effect_code,
                response.effect_instance_id,
            )
            if key not in seen_responses:
                seen_responses.add(key)
                responses.append(response)

        for decoded in decode_v576_embedded_direct_damage_events(
            payload,
            inferred_direction,
            known_skill_ids,
        ):
            source_actor_id = int(decoded.source_actor_id or 0)
            target_actor_id = int(decoded.target_actor_id or 0)
            object_id = int(decoded.damage_object_id or 0)
            damage = int(decoded.damage or 0)
            if source_actor_id <= 0 or object_id <= 0 or damage <= 0:
                continue
            skill_id = int(decoded.skill_id or 0)
            record_offset = int(decoded.record_offset or 0)
            xor_phase, skill_offset = _direct37_wire_position(
                payload,
                source_actor_id=source_actor_id,
                object_id=object_id,
                damage=damage,
                record_offset=record_offset,
                skill_id=skill_id if bool(decoded.skill_id_exact) else 0,
            )
            hit = DirectHit(
                packet_index=packet_count,
                timestamp_ms=timestamp_ms,
                source_actor_id=source_actor_id,
                target_actor_id=target_actor_id,
                object_id=object_id,
                damage=damage,
                skill_id=skill_id,
                skill_id_exact=bool(decoded.skill_id_exact),
                direction=direction,
                signature=signature,
                xor_phase=xor_phase,
                record_offset=record_offset,
                skill_offset=skill_offset,
            )
            key = (
                hit.packet_index,
                hit.record_offset,
                hit.source_actor_id,
                hit.target_actor_id,
                hit.object_id,
                hit.damage,
            )
            if key not in seen_hits:
                seen_hits.add(key)
                direct_hits.append(hit)

    actions_by_packet_actor: dict[tuple[int, int], list[Action]] = defaultdict(list)
    for action in actions:
        actions_by_packet_actor[(action.packet_index, action.owner_actor_id)].append(action)
    responses_by_packet_actor: dict[tuple[int, int], list[Response]] = defaultdict(list)
    for response in responses:
        responses_by_packet_actor[(response.packet_index, response.owner_actor_id)].append(response)

    registrations_by_key: dict[tuple[int, int], list[Registration]] = defaultdict(list)
    for registration in all_registrations:
        registrations_by_key[(registration.owner_actor_id, registration.object_id)].append(
            registration
        )
    for values in registrations_by_key.values():
        values.sort(key=lambda row: (row.packet_index, row.record_offset, row.template_id))

    template_rows: dict[int, dict[str, Any]] = {}

    def template_row(template_id: int) -> dict[str, Any]:
        return template_rows.setdefault(
            template_id,
            {
                "registrations": 0,
                "objects": set(),
                "actors": set(),
                "layouts": Counter(),
                "action_same_packet": Counter(),
                "response_same_packet": Counter(),
                "joined_hits": 0,
                "joined_damage": 0,
                "skillless_joined_hits": 0,
                "exact_skill_counts": Counter(),
                "exact_captures": set(),
                "exact_actors": set(),
                "ambiguous_generation_hits": 0,
                "stale_generation_hits": 0,
                "examples": {"support": [], "conflict": [], "skillless": []},
            },
        )

    for registration in all_registrations:
        if registration.template_id not in TEMPLATE_IDS:
            continue
        row = template_row(registration.template_id)
        row["registrations"] += 1
        row["objects"].add((registration.owner_actor_id, registration.object_id))
        row["actors"].add(registration.owner_actor_id)
        row["layouts"][registration.source_layout] += 1
        for action in actions_by_packet_actor.get(
            (registration.packet_index, registration.owner_actor_id), ()
        ):
            row["action_same_packet"][action.skill_id] += 1
        for response in responses_by_packet_actor.get(
            (registration.packet_index, registration.owner_actor_id), ()
        ):
            row["response_same_packet"][response.skill_id] += 1

    for hit in direct_hits:
        candidates = registrations_by_key.get(
            (hit.source_actor_id, hit.object_id), ()
        )
        if not candidates:
            continue
        hit_position = (hit.packet_index, hit.record_offset)
        eligible = [
            row
            for row in candidates
            if (row.packet_index, row.record_offset) <= hit_position
        ]
        if not eligible:
            continue
        latest_position = max(
            (row.packet_index, row.record_offset) for row in eligible
        )
        generation = [
            row
            for row in eligible
            if (row.packet_index, row.record_offset) == latest_position
        ]
        template_ids = {row.template_id for row in generation}
        target_templates = template_ids & TEMPLATE_IDS
        if len(template_ids) != 1:
            for template_id in target_templates:
                template_row(template_id)["ambiguous_generation_hits"] += 1
            continue
        template_id = next(iter(template_ids))
        if template_id not in TEMPLATE_IDS:
            continue
        registration = generation[0]
        row = template_row(template_id)
        generation_age_ms = hit.timestamp_ms - registration.timestamp_ms
        if (
            generation_age_ms < 0
            or generation_age_ms > MAX_REGISTRATION_GENERATION_AGE_MS
        ):
            row["stale_generation_hits"] += 1
            continue
        row["joined_hits"] += 1
        row["joined_damage"] += hit.damage
        expected = {
            int(SPEC_BY_TEMPLATE[template_id].canonical_skill_id),
            *map(int, SPEC_BY_TEMPLATE[template_id].skill_ids),
        }
        evidence = {
            "capture": str(path),
            "owner_actor_id": f"0x{hit.source_actor_id:08X}",
            "damage_object_id": hit.object_id,
            "projectile_template_id": template_id,
            "registration": {
                "packet_index": registration.packet_index,
                "record_offset": registration.record_offset,
                "xor_phase": registration.xor_phase,
                "direction": registration.direction,
                "signature": registration.signature,
                "layout": registration.source_layout,
            },
            "direct37": {
                "packet_index": hit.packet_index,
                "record_offset": hit.record_offset,
                "xor_phase": hit.xor_phase,
                "skill_offset": hit.skill_offset,
                "direction": hit.direction,
                "signature": hit.signature,
                "target_actor_id": hit.target_actor_id,
                "damage": hit.damage,
                "fixed_skill_id": hit.skill_id if hit.skill_id_exact else 0,
            },
        }
        if hit.skill_id_exact and hit.skill_id > 0:
            row["exact_skill_counts"][hit.skill_id] += 1
            row["exact_captures"].add(str(path))
            row["exact_actors"].add(hit.source_actor_id)
            bucket = "support" if hit.skill_id in expected else "conflict"
        else:
            row["skillless_joined_hits"] += 1
            bucket = "skillless"
        if len(row["examples"][bucket]) < 8:
            row["examples"][bucket].append(evidence)

    return {
        "capture": str(path),
        "packet_count": packet_count,
        "action_count": len(actions),
        "response_count": len(responses),
        "all_registration_count": len(all_registrations),
        "registration_count": sum(
            row["registrations"] for row in template_rows.values()
        ),
        "direct37_object_hit_count": len(direct_hits),
        "templates": {
            str(template_id): {
                "registrations": int(row["registrations"]),
                "object_count": len(row["objects"]),
                "actors": [f"0x{value:08X}" for value in sorted(row["actors"])],
                "source_layouts": dict(row["layouts"]),
                "same_packet_action_skill_counts": _counter_dict(
                    row["action_same_packet"]
                ),
                "same_packet_response_skill_counts": _counter_dict(
                    row["response_same_packet"]
                ),
                "joined_hits": int(row["joined_hits"]),
                "joined_damage": int(row["joined_damage"]),
                "skillless_joined_hits": int(row["skillless_joined_hits"]),
                "exact_skill_counts": _counter_dict(row["exact_skill_counts"]),
                "exact_captures": sorted(row["exact_captures"]),
                "exact_actors": [
                    f"0x{value:08X}" for value in sorted(row["exact_actors"])
                ],
                "ambiguous_generation_hits": int(row["ambiguous_generation_hits"]),
                "stale_generation_hits": int(row["stale_generation_hits"]),
                "examples": row["examples"],
            }
            for template_id, row in sorted(template_rows.items())
        },
    }


def _counter_dict(counter: Counter[int]) -> dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common()}


def _summarize(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for template_id in sorted(TEMPLATE_IDS):
        spec = SPEC_BY_TEMPLATE[template_id]
        registrations = 0
        objects = 0
        joined_hits = 0
        joined_damage = 0
        skillless_hits = 0
        ambiguous_hits = 0
        stale_hits = 0
        registration_captures: set[str] = set()
        registration_actor_keys: set[str] = set()
        exact_capture_keys: set[str] = set()
        exact_actor_keys: set[str] = set()
        layouts: Counter[str] = Counter()
        same_packet_actions: Counter[int] = Counter()
        same_packet_responses: Counter[int] = Counter()
        exact_skill_counts: Counter[int] = Counter()
        examples: dict[str, list[dict[str, Any]]] = {
            "support": [],
            "conflict": [],
            "skillless": [],
        }
        for report in reports:
            capture = str(report["capture"])
            row = report.get("templates", {}).get(str(template_id))
            if not row:
                continue
            registrations += int(row["registrations"])
            objects += int(row["object_count"])
            joined_hits += int(row["joined_hits"])
            joined_damage += int(row["joined_damage"])
            skillless_hits += int(row["skillless_joined_hits"])
            ambiguous_hits += int(row["ambiguous_generation_hits"])
            stale_hits += int(row["stale_generation_hits"])
            registration_captures.add(capture)
            for actor in row["actors"]:
                registration_actor_keys.add(f"{capture}|{actor}")
            layouts.update(row["source_layouts"])
            same_packet_actions.update(
                {int(key): int(value) for key, value in row["same_packet_action_skill_counts"].items()}
            )
            same_packet_responses.update(
                {int(key): int(value) for key, value in row["same_packet_response_skill_counts"].items()}
            )
            exact_skill_counts.update(
                {int(key): int(value) for key, value in row["exact_skill_counts"].items()}
            )
            for exact_capture in row["exact_captures"]:
                exact_capture_keys.add(str(exact_capture))
            for actor in row["exact_actors"]:
                exact_actor_keys.add(f"{capture}|{actor}")
            for bucket in examples:
                remaining = max(0, 12 - len(examples[bucket]))
                examples[bucket].extend(row["examples"][bucket][:remaining])

        expected_ids = {int(spec.canonical_skill_id), *map(int, spec.skill_ids)}
        support_counts = Counter(
            {key: value for key, value in exact_skill_counts.items() if key in expected_ids}
        )
        conflict_counts = Counter(
            {key: value for key, value in exact_skill_counts.items() if key not in expected_ids}
        )
        support_hits = sum(support_counts.values())
        conflict_hits = sum(conflict_counts.values())
        if conflict_hits:
            classification = "conflict"
            reason = "fixed-slot direct37 skill conflicts with configured family"
        elif (
            support_hits >= 30
            and len(exact_capture_keys) >= 2
            and len(exact_actor_keys) >= 2
        ):
            classification = "reliable_cross_capture"
            reason = "at least 30 fixed-slot anchors across >=2 captures and >=2 capture-actor sources"
        elif support_hits >= 30:
            classification = "packet_proven_single_source"
            reason = "fixed-slot anchors prove observed packets, but independent capture/actor replication is missing"
        elif support_hits > 0:
            classification = "insufficient_packet_anchors"
            reason = "fixed-slot packet anchors exist but are below the reliability gate"
        elif joined_hits > 0:
            classification = "object_chain_without_skill_anchor"
            reason = "registration->object->direct37 is exact, but the template-to-skill edge has no fixed-slot anchor"
        elif registrations > 0:
            classification = "registration_only"
            reason = "Projectile registration is visible but no direct37 object join was observed"
        else:
            classification = "not_observed"
            reason = "no matching Projectile registration in this capture set"
        result.append(
            {
                "template_id": template_id,
                "template_key": spec.key,
                "canonical_skill_id": int(spec.canonical_skill_id),
                "canonical_skill_name": _skill_name(spec.canonical_skill_id),
                "configured_skill_ids": sorted(int(value) for value in spec.skill_ids),
                "registrations": registrations,
                "registration_capture_count": len(registration_captures),
                "registration_actor_source_count": len(registration_actor_keys),
                "object_count": objects,
                "source_layouts": dict(layouts),
                "joined_direct37_hits": joined_hits,
                "joined_direct37_damage": joined_damage,
                "skillless_joined_hits": skillless_hits,
                "ambiguous_generation_hits": ambiguous_hits,
                "stale_generation_hits": stale_hits,
                "fixed_slot_support_hits": support_hits,
                "fixed_slot_conflict_hits": conflict_hits,
                "fixed_slot_support_skill_counts": _counter_dict(support_counts),
                "fixed_slot_conflict_skill_counts": _counter_dict(conflict_counts),
                "fixed_slot_all_skill_counts": _counter_dict(exact_skill_counts),
                "fixed_slot_capture_count": len(exact_capture_keys),
                "fixed_slot_actor_source_count": len(exact_actor_keys),
                "same_packet_action_skill_counts": _counter_dict(same_packet_actions),
                "same_packet_response_skill_counts": _counter_dict(same_packet_responses),
                "classification": classification,
                "classification_reason": reason,
                "full_action_object_chain_proven": False,
                "action_object_blocker": (
                    "No explicit parent action/transaction/object foreign key was found. "
                    "Same-packet and nearby actions/responses are diagnostic only."
                ),
                "evidence_examples": examples,
            }
        )
    return result


def _skill_summaries(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    skill_ids = set(MAGIC_ARCHER_SKILL_IDS)
    for spec in MAGIC_ARCHER_SPECS:
        skill_ids.add(int(spec.canonical_skill_id))
        skill_ids.update(map(int, spec.skill_ids))
    result: list[dict[str, Any]] = []
    for skill_id in sorted(skill_ids):
        related = [
            row
            for row in templates
            if skill_id == int(row["canonical_skill_id"])
            or skill_id in set(map(int, row["configured_skill_ids"]))
        ]
        exact_hits = sum(
            int(row["fixed_slot_all_skill_counts"].get(str(skill_id), 0))
            for row in related
        )
        states = {str(row["classification"]) for row in related}
        if exact_hits > 0 and "reliable_cross_capture" in states:
            status = "reliable_exact_skill"
        elif exact_hits > 0:
            status = "packet_exact_but_not_independently_replicated"
        elif related and any(
            str(row["classification"]) != "not_observed" for row in related
        ):
            status = "family_candidate_only"
        elif related:
            status = "configured_projectile_not_observed"
        else:
            status = "no_projectile_template_observed"
        result.append(
            {
                "skill_id": skill_id,
                "skill_name": _skill_name(skill_id),
                "status": status,
                "exact_fixed_slot_hits": exact_hits,
                "projectile_templates": [int(row["template_id"]) for row in related],
                "template_classifications": {
                    str(row["template_id"]): str(row["classification"])
                    for row in related
                },
            }
        )
    return result


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 魔羽 Projectile→技能归属专项审计",
        "",
        f"- 抓包：{result['captures']} 份",
        f"- 逻辑包：{result['packet_count']:,}",
        f"- 已解动作：{result['action_count']:,}",
        f"- 已解 13 4A/4E/47 响应：{result['response_count']:,}",
        f"- 魔羽相关对象注册：{result['registration_count']:,}",
        f"- 带对象 ID 的 direct37：{result['direct37_object_hit_count']:,}",
        "",
        "## 判定边界",
        "",
        "对象注册中的 `owner_actor_id + damage_object_id + Projectile template` 与 direct37 中的完整 `owner_actor_id + damage_object_id` 是逐包事实。",
        "只有 direct37 固定结果槽（marker+102/+114）中的技能 ID 可验证 Projectile→技能；result-group、时间接近、damage fingerprint、learned profile 均未参与。",
        "对象 ID 复用按同一抓包内最新完整注册代次处理；未知模板注册会覆盖旧代次。30 秒只用于拒绝陈旧代次，不作为正向技能证据。",
        "同包动作/响应也没有发现父事务外键，因此只作阻塞点记录，绝不升级为确定性动作→对象关系。",
        "",
        "## 模板汇总",
        "",
        "| Projectile | 技能族 | 注册 | 对象Hit/无技能槽Hit | 固定槽支持/冲突 | 文件/来源 | 结论 |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in sorted(
        (item for item in result["templates"] if int(item["registrations"]) > 0),
        key=lambda item: (-int(item["fixed_slot_support_hits"]), -int(item["joined_direct37_hits"])),
    ):
        lines.append(
            "| {template_id} (`{template_key}`) | {canonical_skill_name} ({canonical_skill_id}) | "
            "{registrations:,} | {joined_direct37_hits:,}/{skillless_joined_hits:,} | "
            "{fixed_slot_support_hits:,}/{fixed_slot_conflict_hits:,} | "
            "{fixed_slot_capture_count}/{fixed_slot_actor_source_count} | `{classification}` |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## 技能级结论",
            "",
            "| 技能 | 固定槽精确Hit | Projectile | 结论 |",
            "|---|---:|---|---|",
        ]
    )
    for row in result["skills"]:
        lines.append(
            f"| {row['skill_name'] or '未知'} ({row['skill_id']}) | "
            f"{int(row['exact_fixed_slot_hits']):,} | "
            f"{', '.join(map(str, row['projectile_templates'])) or '-'} | "
            f"`{row['status']}` |"
        )
    lines.extend(
        [
            "",
            "## 可复核证据位置",
            "",
            "以下位置均为逻辑包序号和解码后记录偏移；不使用时间邻近。",
            "",
        ]
    )
    for row in result["templates"]:
        examples = row["evidence_examples"]["support"]
        if not examples:
            continue
        lines.append(
            f"### Projectile {row['template_id']} → {row['canonical_skill_name']} ({row['canonical_skill_id']})"
        )
        lines.append("")
        for example in examples[:4]:
            registration = example["registration"]
            direct = example["direct37"]
            lines.append(
                "- `{capture}`：注册包 #{registration_packet} offset {registration_offset} "
                "(`{registration_layout}`, owner={owner}, object={object_id}) → "
                "direct37 包 #{direct_packet} offset {direct_offset}，固定技能槽 offset "
                "{skill_offset} = {skill_id}。".format(
                    capture=Path(example["capture"]).name,
                    registration_packet=registration["packet_index"],
                    registration_offset=registration["record_offset"],
                    registration_layout=registration["layout"],
                    owner=example["owner_actor_id"],
                    object_id=example["damage_object_id"],
                    direct_packet=direct["packet_index"],
                    direct_offset=direct["record_offset"],
                    skill_offset=direct["skill_offset"],
                    skill_id=direct["fixed_skill_id"],
                )
            )
        lines.append("")
    lines.extend(
        [
            "## 总结",
            "",
            "- 箭矢对象思路成立：对象注册→`damage_object_id`→direct37 已可逐 Hit 连接。",
            "- 但动作/响应→对象实例仍缺少显式父事务键；因此不能声称所有魔羽箭矢已经形成完整四段链。",
            "- 只有达到跨抓包门槛且固定槽零冲突的模板列为可靠；单来源大量一致样本单独标记，不与跨抓包可靠混淆。",
            "- 本轮没有修改任何生产映射。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit Magic Archer Projectile registrations against decoded actions "
            "without treating temporal proximity as deterministic proof."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    captures = [str(path.resolve()) for path in args.captures]
    reports: list[dict[str, Any]] = []
    worker_count = max(1, min(int(args.workers), 3, len(captures)))
    if worker_count == 1:
        for capture in captures:
            reports.append(_audit_capture(capture))
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(_audit_capture, capture): capture for capture in captures}
            for future in as_completed(futures):
                reports.append(future.result())
    reports.sort(key=lambda row: str(row["capture"]).casefold())

    templates = _summarize(reports)
    result = {
        "schema_version": 2,
        "policy": (
            "Only exact owner+damage_object_id registration generations and fixed-slot "
            "direct37 skill IDs establish packet evidence. Temporal proximity, damage "
            "fingerprints, result groups and learned profiles are excluded."
        ),
        "captures": len(reports),
        "packet_count": sum(int(row["packet_count"]) for row in reports),
        "action_count": sum(int(row["action_count"]) for row in reports),
        "response_count": sum(int(row["response_count"]) for row in reports),
        "registration_count": sum(int(row["registration_count"]) for row in reports),
        "direct37_object_hit_count": sum(
            int(row["direct37_object_hit_count"]) for row in reports
        ),
        "templates": templates,
        "skills": _skill_summaries(templates),
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".md").write_text(
        _render_markdown(result),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
