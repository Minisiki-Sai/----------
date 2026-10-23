from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger  # noqa: E402
from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_damage_skill_ids,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _V576_EMBEDDED_ACTION_PHASE_BY_SIGNATURE,
    _decode_v576_body,
    _v576_wire_may_contain_marker,
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_damage_object_registration,
    decode_v576_server_damage_object_registrations,
    decode_v576_status_events,
    decode_v576_structured_actions,
)


PROVENANCE_JSON = (
    ROOT / "outputs/damage_attribution/projectile_mapping_provenance_audit.json"
)
DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_JSON = (
    ROOT / "outputs/damage_attribution/provisional_projectile_upgrade_audit.json"
)
DEFAULT_MARKDOWN = (
    ROOT / "outputs/damage_attribution/provisional_projectile_upgrade_audit.md"
)
DEFAULT_CSV = (
    ROOT / "outputs/damage_attribution/provisional_projectile_upgrade_templates.csv"
)
DEFAULT_CHECKPOINT_DIR = (
    ROOT / "outputs/damage_attribution/provisional_projectile_upgrade_checkpoints"
)

SCHEMA_VERSION = 3
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
OBJECT_MARKERS = (
    b"\x09\x5d\x00",
    b"\x09\x61\x00",
    b"\x09\x6e\x00",
    b"\x09\x6a\x00",
)
DAMAGE_MARKERS = (b"\x08\x73\x00", b"\x08\x77\x00")
STATUS_WIRE_SENTINEL = b"\xff\xff\xff\xff"
MIN_SHAPE_RECORDS = 30
MIN_SHAPE_TEMPLATES = 3
MIN_SHAPE_CAPTURES = 3
MIN_SHAPE_ACTORS = 3
MIN_UPGRADE_RECORDS = 30
MIN_UPGRADE_CAPTURES = 2
MIN_UPGRADE_ACTORS = 2
MAX_EXAMPLES = 6


@dataclass(frozen=True, slots=True)
class Registration:
    packet_index: int
    owner_actor_id: int
    object_id: int
    template_id: int
    source_layout: str


@dataclass(frozen=True, slots=True)
class CandidatePacket:
    packet_index: int
    direction: str
    signature: str
    payload: bytes


def _offsets(body: bytes, marker: bytes, start: int = 0) -> Iterable[int]:
    offset = body.find(marker, start)
    while offset >= 0:
        yield offset
        offset = body.find(marker, offset + 1)


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


def _overlaps(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _semantic_exclusions(
    body: bytes,
    *,
    action_locations: Iterable[tuple[int, int, int]],
    statuses: Iterable[Any],
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for marker in OBJECT_MARKERS:
        for offset in _offsets(body, marker, 9):
            ranges.append((max(0, offset - 4), min(len(body), offset + 48)))
    # A direct result can repeat the object ID and may expose an exact skill.
    # Exclude the complete known result neighbourhood, not just the two fields,
    # so this audit cannot silently rediscover the existing object->direct hit
    # anchor under a different relative offset.
    for marker in DAMAGE_MARKERS:
        for offset in _offsets(body, marker, 9):
            ranges.append((max(0, offset - 24), min(len(body), offset + 132)))
    for offset, _actor_id, _skill_id in action_locations:
        ranges.append((max(0, offset - 4), min(len(body), offset)))
        ranges.append((offset + 3, min(len(body), offset + 7)))
    for status in statuses:
        skill_offset = int(status.record_offset)
        ranges.append((skill_offset, min(len(body), skill_offset + 4)))
    return ranges


def _record_fields(
    body: bytes,
    start: int,
    end: int,
    exclusions: Iterable[tuple[int, int]],
) -> Iterable[tuple[int, int]]:
    lower = max(0, start)
    upper = min(len(body), end)
    for offset in range(lower, max(lower, upper - 3)):
        if _overlaps(offset, offset + 4, exclusions):
            continue
        yield offset, int.from_bytes(body[offset : offset + 4], "little")


def _action_locations(
    packet: CandidatePacket,
    known_skill_ids: frozenset[int],
) -> tuple[int | None, bytes | None, list[tuple[int, int, int, bytes]]]:
    actions = decode_v576_structured_actions(
        packet.payload,
        packet.direction,
        known_skill_ids,
        (),
        allow_unknown_actor_ids=True,
    )
    if not actions:
        return None, None, []
    signature = packet.payload[5:9] if len(packet.payload) >= 9 else b""
    phase = _V576_EMBEDDED_ACTION_PHASE_BY_SIGNATURE.get(
        (packet.direction, signature)
    )
    if phase is None:
        return None, None, []
    body = _decode_v576_body(packet.payload, phase)
    expected = {
        (int(row.source_actor_id or 0), int(row.skill_id or 0)) for row in actions
    }
    found: list[tuple[int, int, int, bytes]] = []
    header_actor = int.from_bytes(body[10:14], "little") if len(body) >= 14 else 0
    for marker in ACTION_MARKERS:
        for offset in _offsets(body, marker, 14):
            if offset < 4 or offset + 7 > len(body):
                continue
            marker_actor = int.from_bytes(body[offset - 4 : offset], "little")
            skill_id = int.from_bytes(body[offset + 3 : offset + 7], "little")
            actor_candidates = (
                (marker_actor,)
                if packet.direction == "server_to_client"
                else (marker_actor, header_actor)
            )
            matching = [
                actor_id
                for actor_id in actor_candidates
                if (actor_id, skill_id) in expected
            ]
            if len(set(matching)) == 1:
                found.append((offset, matching[0], skill_id, marker))
    return phase, body, found


def _status_rows(
    packet: CandidatePacket,
    known_skill_ids: frozenset[int],
) -> tuple[tuple[Any, bytes], ...]:
    rows = decode_v576_status_events(
        packet.payload,
        packet.direction,
        known_skill_ids,
    )
    bodies: dict[int, bytes] = {}
    result: list[tuple[Any, bytes]] = []
    for row in rows:
        phase = int(row.xor_phase)
        body = bodies.setdefault(phase, _decode_v576_body(packet.payload, phase))
        result.append((row, body))
    return tuple(result)


def _observation_key(
    mode: str,
    shape: str,
    template_id: int,
    skill_id: int,
    actor_id: int,
) -> str:
    return "|".join(
        (mode, shape, str(template_id), str(skill_id), f"{actor_id:08X}")
    )


def _add_observation(
    aggregate: dict[str, dict[str, Any]],
    *,
    mode: str,
    shape: str,
    template_id: int,
    skill_id: int,
    actor_id: int,
    example: dict[str, Any],
) -> None:
    key = _observation_key(mode, shape, template_id, skill_id, actor_id)
    row = aggregate.setdefault(
        key,
        {
            "mode": mode,
            "shape": shape,
            "template_id": template_id,
            "skill_id": skill_id,
            "actor_id": actor_id,
            "records": 0,
            "examples": [],
        },
    )
    row["records"] += 1
    if len(row["examples"]) < MAX_EXAMPLES:
        row["examples"].append(example)


def _audit_capture(
    path_text: str,
    template_ids: tuple[int, ...],
) -> dict[str, Any]:
    path = Path(path_text)
    all_templates = frozenset(template_ids)
    known_skill_ids = _load_damage_skill_ids(str(DATA_DIR))
    registrations: list[Registration] = []
    candidate_packet_count = 0
    packet_count = 0

    for packet_count, packet in enumerate(_iter_logical_packets(path), start=1):
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction() or "unknown")
        local = decode_v576_damage_object_registration(payload, direction)
        if local is not None and int(local.projectile_template_id) in all_templates:
            registrations.append(
                Registration(
                    packet_index=packet_count,
                    owner_actor_id=int(local.source_actor_id),
                    object_id=int(local.damage_object_id),
                    template_id=int(local.projectile_template_id),
                    source_layout="c2s_107",
                )
            )
        for server in decode_v576_server_damage_object_registrations(
            payload,
            direction,
            all_templates,
            include_unmapped=False,
        ):
            registrations.append(
                Registration(
                    packet_index=packet_count,
                    owner_actor_id=int(server.source_actor_id),
                    object_id=int(server.damage_object_id),
                    template_id=int(server.projectile_template_id),
                    source_layout="s2c_object_state",
                )
            )
        if len(payload) >= 25 and _v576_wire_may_contain_marker(
            payload,
            ACTION_MARKERS + (STATUS_WIRE_SENTINEL,),
            start_offset=9,
        ):
            candidate_packet_count += 1

    actor_templates: dict[int, set[int]] = defaultdict(set)
    object_templates: dict[tuple[int, int], set[int]] = defaultdict(set)
    object_layouts: dict[tuple[int, int], Counter[str]] = defaultdict(Counter)
    for row in registrations:
        actor_templates[row.owner_actor_id].add(row.template_id)
        object_templates[(row.owner_actor_id, row.object_id)].add(row.template_id)
        object_layouts[(row.owner_actor_id, row.object_id)][row.source_layout] += 1
    unique_objects = {
        key: next(iter(values))
        for key, values in object_templates.items()
        if len(values) == 1
    }

    observations: dict[str, dict[str, Any]] = {}
    decoded_action_packets = 0
    decoded_status_records = 0
    # A second streaming pass keeps memory bounded.  Retaining every compound
    # action/status payload was faster on small samples but exceeded 2 GiB
    # across four large-capture workers.  The registration pass above is the
    # only state required to validate actor-scoped object IDs, so rereading the
    # capture preserves identical evidence without retaining raw packets.
    for second_packet_index, logical_packet in enumerate(
        _iter_logical_packets(path), start=1
    ):
        payload = bytes(logical_packet.payload)
        if len(payload) < 25 or not _v576_wire_may_contain_marker(
            payload,
            ACTION_MARKERS + (STATUS_WIRE_SENTINEL,),
            start_offset=9,
        ):
            continue
        direction = str(logical_packet.inferred_direction() or "unknown")
        packet = CandidatePacket(
            packet_index=second_packet_index,
            direction=direction,
            signature=(payload[5:9].hex() if len(payload) >= 9 else ""),
            payload=payload,
        )
        action_phase, action_body, actions = _action_locations(
            packet, known_skill_ids
        )
        statuses_with_body = _status_rows(packet, known_skill_ids)
        statuses_by_phase: dict[int, list[Any]] = defaultdict(list)
        for status, _body in statuses_with_body:
            statuses_by_phase[int(status.xor_phase)].append(status)
        decoded_status_records += len(statuses_with_body)

        if action_body is not None and action_phase is not None and actions:
            decoded_action_packets += 1
            simple_actions = [
                (offset, actor_id, skill_id)
                for offset, actor_id, skill_id, _marker in actions
            ]
            exclusions = _semantic_exclusions(
                action_body,
                action_locations=simple_actions,
                statuses=statuses_by_phase.get(action_phase, ()),
            )
            for offset, actor_id, skill_id, marker in actions:
                for field_offset, value in _record_fields(
                    action_body,
                    offset - 12,
                    offset + 96,
                    exclusions,
                ):
                    relative = field_offset - offset
                    shape = (
                        f"{packet.direction}:{packet.signature}:"
                        f"{marker.hex()}:rel{relative:+d}"
                    )
                    example = {
                        "packet_index": packet.packet_index,
                        "actor_id": f"0x{actor_id:08X}",
                        "skill_id": skill_id,
                        "field_offset": field_offset,
                        "relative_offset": relative,
                        "value": value,
                    }
                    if (
                        value in all_templates
                        and value in actor_templates.get(actor_id, ())
                    ):
                        _add_observation(
                            observations,
                            mode="action_numeric_template",
                            shape=shape,
                            template_id=value,
                            skill_id=skill_id,
                            actor_id=actor_id,
                            example=example,
                        )
                    linked_template = unique_objects.get((actor_id, value))
                    if linked_template is not None:
                        linked_example = dict(example)
                        linked_example["object_id"] = value
                        linked_example["registration_layouts"] = dict(
                            object_layouts[(actor_id, value)]
                        )
                        _add_observation(
                            observations,
                            mode="object_to_action_numeric_field",
                            shape=shape,
                            template_id=linked_template,
                            skill_id=skill_id,
                            actor_id=actor_id,
                            example=linked_example,
                        )

        # Status rows expose their exact phase and record offset.  Scan only
        # the current response record, not the surrounding packet, and exclude
        # all action/damage/registration fields from eligibility.
        for status, body in statuses_with_body:
            phase = int(status.xor_phase)
            action_locations_same_phase: list[tuple[int, int, int]] = []
            if action_phase == phase:
                action_locations_same_phase = [
                    (offset, actor_id, skill_id)
                    for offset, actor_id, skill_id, _marker in actions
                ]
            exclusions = _semantic_exclusions(
                body,
                action_locations=action_locations_same_phase,
                statuses=statuses_by_phase.get(phase, ()),
            )
            skill_offset = int(status.record_offset)
            parameter_bytes = str(status.parameters).encode("utf-8")
            record_end = skill_offset + 20 + len(parameter_bytes)
            if status.effect_instance_id is not None:
                record_end += 5
            actor_id = int(status.source_actor_id)
            skill_id = int(status.skill_id)
            for field_offset, value in _record_fields(
                body,
                skill_offset - 16,
                record_end,
                exclusions,
            ):
                relative = field_offset - skill_offset
                shape = f"{status.effect_code}:rel{relative:+d}"
                example = {
                    "packet_index": packet.packet_index,
                    "actor_id": f"0x{actor_id:08X}",
                    "target_actor_id": f"0x{int(status.target_actor_id):08X}",
                    "skill_id": skill_id,
                    "effect_code": status.effect_code,
                    "field_offset": field_offset,
                    "relative_offset": relative,
                    "value": value,
                }
                if (
                    value in all_templates
                    and value in actor_templates.get(actor_id, ())
                ):
                    _add_observation(
                        observations,
                        mode="response_numeric_template",
                        shape=shape,
                        template_id=value,
                        skill_id=skill_id,
                        actor_id=actor_id,
                        example=example,
                    )
                linked_template = unique_objects.get((actor_id, value))
                if linked_template is not None:
                    linked_example = dict(example)
                    linked_example["object_id"] = value
                    linked_example["registration_layouts"] = dict(
                        object_layouts[(actor_id, value)]
                    )
                    _add_observation(
                        observations,
                        mode="object_to_response_numeric_field",
                        shape=shape,
                        template_id=linked_template,
                        skill_id=skill_id,
                        actor_id=actor_id,
                        example=linked_example,
                    )

    return {
        "schema_version": SCHEMA_VERSION,
        "capture": str(path),
        "size_bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "logical_packets": packet_count,
        "registrations": len(registrations),
        "candidate_packets": candidate_packet_count,
        "decoded_action_packets": decoded_action_packets,
        "decoded_status_records": decoded_status_records,
        "ambiguous_object_keys": sum(
            len(values) != 1 for values in object_templates.values()
        ),
        "observations": list(observations.values()),
    }


def _checkpoint_path(checkpoint_dir: Path, capture: Path) -> Path:
    token = hashlib.sha256(str(capture.resolve()).encode("utf-8")).hexdigest()[:20]
    return checkpoint_dir / f"{token}.json"


def _load_checkpoint(checkpoint_dir: Path, capture: Path) -> dict[str, Any] | None:
    path = _checkpoint_path(checkpoint_dir, capture)
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stat = capture.stat()
    if (
        int(row.get("schema_version", -1)) != SCHEMA_VERSION
        or int(row.get("size_bytes", -1)) != stat.st_size
        or int(row.get("mtime_ns", -1)) != stat.st_mtime_ns
    ):
        return None
    return row


def _write_checkpoint(
    checkpoint_dir: Path,
    capture: Path,
    report: dict[str, Any],
) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    target = _checkpoint_path(checkpoint_dir, capture)
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(target)


def _family_map(provenance: dict[str, Any]) -> dict[int, frozenset[int]]:
    return {
        int(row["projectile_template_id"]): frozenset(
            {
                int(row["canonical_skill_id"]),
                *(int(value) for value in row["configured_skill_ids"]),
            }
        )
        for row in provenance["mappings"]
    }


def _aggregate_observations(
    reports: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for report in reports:
        capture = str(report["capture"])
        for source in report["observations"]:
            key = (
                str(source["mode"]),
                str(source["shape"]),
                int(source["template_id"]),
                int(source["skill_id"]),
            )
            target = merged.setdefault(
                key,
                {
                    "mode": key[0],
                    "shape": key[1],
                    "template_id": key[2],
                    "skill_id": key[3],
                    "records": 0,
                    "captures": set(),
                    "actors": set(),
                    "records_by_capture": Counter(),
                    "examples": [],
                },
            )
            count = int(source["records"])
            target["records"] += count
            target["captures"].add(capture)
            actor = f"{capture}|0x{int(source['actor_id']):08X}"
            target["actors"].add(actor)
            target["records_by_capture"][capture] += count
            for example in source.get("examples", ()):
                if len(target["examples"]) >= MAX_EXAMPLES:
                    break
                target["examples"].append({"capture": capture, **example})
    rows: list[dict[str, Any]] = []
    for row in merged.values():
        rows.append(
            {
                **row,
                "captures": sorted(row["captures"]),
                "actors": sorted(row["actors"]),
                "records_by_capture": dict(row["records_by_capture"]),
            }
        )
    rows.sort(
        key=lambda row: (
            row["mode"],
            row["shape"],
            row["template_id"],
            row["skill_id"],
        )
    )
    return rows


def _classify_shapes(
    observations: list[dict[str, Any]],
    *,
    packet_validated: frozenset[int],
    families: dict[int, frozenset[int]],
) -> tuple[list[dict[str, Any]], frozenset[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        if int(row["template_id"]) in packet_validated:
            grouped[(str(row["mode"]), str(row["shape"]))].append(row)
    results: list[dict[str, Any]] = []
    accepted: set[tuple[str, str]] = set()
    for key, rows in grouped.items():
        support = 0
        conflicts = 0
        templates: set[int] = set()
        captures: set[str] = set()
        actors: set[str] = set()
        conflict_skills: Counter[str] = Counter()
        for row in rows:
            template_id = int(row["template_id"])
            records = int(row["records"])
            templates.add(template_id)
            captures.update(row["captures"])
            actors.update(row["actors"])
            if int(row["skill_id"]) in families[template_id]:
                support += records
            else:
                conflicts += records
                conflict_skills[
                    f"{template_id}->{int(row['skill_id'])}"
                ] += records
        # Leave-one-capture-out is structural here: every held-out capture
        # must retain at least two other captures and two independently
        # validated templates proving the same field layout, with no conflict.
        loo_failures: list[str] = []
        for holdout in sorted(captures):
            training = [
                row for row in rows if any(cap != holdout for cap in row["captures"])
            ]
            training_captures = {
                cap
                for row in training
                for cap in row["captures"]
                if cap != holdout
            }
            training_templates = {int(row["template_id"]) for row in training}
            training_conflict = any(
                int(row["skill_id"]) not in families[int(row["template_id"])]
                for row in training
            )
            if (
                len(training_captures) < 2
                or len(training_templates) < 2
                or training_conflict
            ):
                loo_failures.append(holdout)
        passed = (
            support >= MIN_SHAPE_RECORDS
            and conflicts == 0
            and len(templates) >= MIN_SHAPE_TEMPLATES
            and len(captures) >= MIN_SHAPE_CAPTURES
            and len(actors) >= MIN_SHAPE_ACTORS
            and not loo_failures
        )
        if passed:
            accepted.add(key)
        results.append(
            {
                "mode": key[0],
                "shape": key[1],
                "support_records": support,
                "conflict_records": conflicts,
                "validated_template_count": len(templates),
                "capture_count": len(captures),
                "actor_count": len(actors),
                "leave_one_capture_out_passed": not loo_failures,
                "leave_one_capture_out_failures": loo_failures,
                "conflict_pairs": dict(conflict_skills),
                "accepted": passed,
            }
        )
    results.sort(
        key=lambda row: (
            not row["accepted"],
            row["conflict_records"],
            -row["support_records"],
            row["mode"],
            row["shape"],
        )
    )
    return results, frozenset(accepted)


def _loo_template(
    rows: list[dict[str, Any]],
    family: frozenset[int],
) -> tuple[bool, list[dict[str, Any]]]:
    captures = sorted({cap for row in rows for cap in row["captures"]})
    failures: list[dict[str, Any]] = []
    for holdout in captures:
        training_skills = {
            int(row["skill_id"])
            for row in rows
            if any(cap != holdout for cap in row["captures"])
        }
        holdout_skills = {
            int(row["skill_id"])
            for row in rows
            if holdout in row["captures"]
        }
        if (
            not training_skills
            or not holdout_skills
            or not training_skills.issubset(family)
            or not holdout_skills.issubset(family)
        ):
            failures.append(
                {
                    "holdout": holdout,
                    "training_skills": sorted(training_skills),
                    "holdout_skills": sorted(holdout_skills),
                }
            )
    return not failures, failures


def _classify_templates(
    provenance: dict[str, Any],
    observations: list[dict[str, Any]],
    accepted_shapes: frozenset[tuple[str, str]],
    families: dict[int, frozenset[int]],
) -> list[dict[str, Any]]:
    by_template: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_template[int(row["template_id"])].append(row)
    results: list[dict[str, Any]] = []
    for mapping in provenance["mappings"]:
        if mapping["classification"] != "needs_more_evidence":
            continue
        template_id = int(mapping["projectile_template_id"])
        family = families[template_id]
        all_rows = by_template.get(template_id, [])
        eligible = [
            row
            for row in all_rows
            if (str(row["mode"]), str(row["shape"])) in accepted_shapes
        ]
        support = sum(
            int(row["records"])
            for row in eligible
            if int(row["skill_id"]) in family
        )
        conflicts = sum(
            int(row["records"])
            for row in eligible
            if int(row["skill_id"]) not in family
        )
        captures = sorted({cap for row in eligible for cap in row["captures"]})
        actors = sorted({actor for row in eligible for actor in row["actors"]})
        loo_passed, loo_failures = _loo_template(eligible, family)
        modes = sorted({str(row["mode"]) for row in eligible})
        shapes = sorted(
            {f"{row['mode']}|{row['shape']}" for row in eligible}
        )
        raw_numeric_records = sum(int(row["records"]) for row in all_rows)
        raw_shapes = sorted(
            {f"{row['mode']}|{row['shape']}" for row in all_rows}
        )
        upgrade = (
            support >= MIN_UPGRADE_RECORDS
            and conflicts == 0
            and len(captures) >= MIN_UPGRADE_CAPTURES
            and len(actors) >= MIN_UPGRADE_ACTORS
            and loo_passed
            and bool(shapes)
        )
        reasons: list[str] = []
        if upgrade:
            reasons.append(
                "independent action/response numeric field or verifiable object entity chain"
            )
        else:
            if not all_rows:
                reasons.append("no independent numeric action/response/entity observation")
            elif not eligible:
                reasons.append(
                    "numeric co-occurrence exists, but its field layout was not independently validated by packet-validated templates"
                )
            if support < MIN_UPGRADE_RECORDS:
                reasons.append(
                    f"eligible support {support} < {MIN_UPGRADE_RECORDS} records"
                )
            if conflicts:
                reasons.append(f"{conflicts} cross-skill conflict records")
            if len(captures) < MIN_UPGRADE_CAPTURES:
                reasons.append(
                    f"eligible capture count {len(captures)} < {MIN_UPGRADE_CAPTURES}"
                )
            if len(actors) < MIN_UPGRADE_ACTORS:
                reasons.append(
                    f"eligible actor count {len(actors)} < {MIN_UPGRADE_ACTORS}"
                )
            if not loo_passed:
                reasons.append("leave-one-capture-out validation failed")
        evidence_rows = [
            {
                "mode": row["mode"],
                "shape": row["shape"],
                "skill_id": int(row["skill_id"]),
                "records": int(row["records"]),
                "captures": row["captures"],
                "actors": row["actors"],
                "examples": row["examples"],
            }
            for row in eligible
        ]
        results.append(
            {
                "projectile_template_id": template_id,
                "key": str(mapping["key"]),
                "canonical_skill_id": int(mapping["canonical_skill_id"]),
                "configured_skill_ids": sorted(family),
                "upgrade_to_packet_validated": upgrade,
                "eligible_support_records": support,
                "eligible_conflict_records": conflicts,
                "eligible_capture_count": len(captures),
                "eligible_actor_count": len(actors),
                "evidence_modes": modes,
                "validated_shapes": shapes,
                "leave_one_capture_out_passed": loo_passed,
                "leave_one_capture_out_failures": loo_failures,
                "raw_numeric_observation_records": raw_numeric_records,
                "raw_numeric_shapes": raw_shapes,
                "reasons": reasons,
                "evidence": evidence_rows,
            }
        )
    results.sort(
        key=lambda row: (
            not row["upgrade_to_packet_validated"],
            -row["eligible_support_records"],
            -row["raw_numeric_observation_records"],
            row["projectile_template_id"],
        )
    )
    return results


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# Provisional Projectile→skill 独立数值证据升级审计",
        "",
        f"生成时间：{result['generated_at']}",
        "",
        "## 结论",
        "",
        f"审计了 **{summary['provisional_templates']}** 个 provisional 模板、"
        f"{summary['captures_audited']}/{summary['captures_discovered']} 份抓包。",
        f"达到 packet-validated 严格门槛：**{summary['upgradeable_templates']}** 个；"
        f"仍不可升级：**{summary['not_upgradeable_templates']}** 个。",
        "",
        "本报告没有使用 ACT/技能中文或资源字符串相似、时间最近、结果组、伤害指纹，"
        "也没有把 direct37/41 exact skill 锚点本身重复包装成新证据。",
        "",
        "## 门槛",
        "",
        f"- 字段布局先由至少 {MIN_SHAPE_RECORDS} 条、"
        f"{MIN_SHAPE_TEMPLATES} 个既有 packet-validated 模板、"
        f"{MIN_SHAPE_CAPTURES} 份抓包和 {MIN_SHAPE_ACTORS} 个 actor 证明，且 0 冲突。",
        f"- provisional 模板需至少 {MIN_UPGRADE_RECORDS} 条独立记录、"
        f"{MIN_UPGRADE_CAPTURES} 份抓包、{MIN_UPGRADE_ACTORS} 个 actor、0 冲突，"
        "并通过逐抓包留一验证。",
        "- action/response 扫描排除了 actor/skill 固定字段、对象注册块和 direct37/41 结果块。",
        "- 对象实体链只接收同一抓包、同一 actor 下唯一绑定到一个模板的 object ID。",
        "",
        "## 可升级模板",
        "",
    ]
    upgradeable = [
        row for row in result["templates"] if row["upgrade_to_packet_validated"]
    ]
    if not upgradeable:
        lines.append(
            "**没有模板达到升级门槛。** 当前 121 个 provisional 映射仍不能因本轮数值审计升级。"
        )
    else:
        lines.extend(
            [
                "| Template | key | canonical skill | 记录 | 抓包 | actor | 证据 |",
                "|---:|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in upgradeable:
            lines.append(
                f"| {row['projectile_template_id']} | `{row['key']}` | "
                f"{row['canonical_skill_id']} | {row['eligible_support_records']} | "
                f"{row['eligible_capture_count']} | {row['eligible_actor_count']} | "
                f"{', '.join(row['validated_shapes'])} |"
            )
    lines.extend(
        [
            "",
            "## 已验证字段布局",
            "",
        ]
    )
    accepted_shapes = [row for row in result["field_shapes"] if row["accepted"]]
    if not accepted_shapes:
        lines.append("没有字段布局先通过独立校准门槛。")
    else:
        lines.extend(
            [
                "| 类型 | 字段布局 | 支持 | 冲突 | 模板 | 抓包 | actor | LOO |",
                "|---|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in accepted_shapes:
            lines.append(
                f"| {row['mode']} | `{row['shape']}` | {row['support_records']} | "
                f"{row['conflict_records']} | {row['validated_template_count']} | "
                f"{row['capture_count']} | {row['actor_count']} | "
                f"{'通过' if row['leave_one_capture_out_passed'] else '失败'} |"
            )
    lines.extend(
        [
            "",
            "## 未升级概览",
            "",
            "| Template | key | 合格支持 | 冲突 | 原始数值观察 | 原因 |",
            "|---:|---|---:|---:|---:|---|",
        ]
    )
    for row in result["templates"]:
        if row["upgrade_to_packet_validated"]:
            continue
        lines.append(
            f"| {row['projectile_template_id']} | `{row['key']}` | "
            f"{row['eligible_support_records']} | {row['eligible_conflict_records']} | "
            f"{row['raw_numeric_observation_records']} | "
            f"{'；'.join(row['reasons'])} |"
        )
    lines.extend(
        [
            "",
            "## 输出",
            "",
            f"- JSON：`{DEFAULT_JSON.resolve()}`",
            f"- CSV：`{DEFAULT_CSV.resolve()}`",
            f"- 审计工具：`{Path(__file__).resolve()}`",
            "",
            "本次未修改任何生产代码。",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "projectile_template_id",
        "key",
        "canonical_skill_id",
        "configured_skill_ids",
        "upgrade_to_packet_validated",
        "eligible_support_records",
        "eligible_conflict_records",
        "eligible_capture_count",
        "eligible_actor_count",
        "evidence_modes",
        "validated_shapes",
        "leave_one_capture_out_passed",
        "raw_numeric_observation_records",
        "reasons",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            rendered = dict(row)
            for field in (
                "configured_skill_ids",
                "evidence_modes",
                "validated_shapes",
                "reasons",
            ):
                rendered[field] = " | ".join(map(str, rendered[field]))
            writer.writerow({field: rendered[field] for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit provisional Projectile->skill mappings for independent packet-level "
            "numeric action/response or entity-chain evidence."
        )
    )
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--provenance-json", type=Path, default=PROVENANCE_JSON)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-captures", type=int)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    provenance = json.loads(args.provenance_json.read_text(encoding="utf-8"))
    families = _family_map(provenance)
    packet_validated = frozenset(
        int(row["projectile_template_id"])
        for row in provenance["mappings"]
        if row["classification"] == "safe"
    )
    provisional = frozenset(
        int(row["projectile_template_id"])
        for row in provenance["mappings"]
        if row["classification"] == "needs_more_evidence"
    )
    all_templates = tuple(sorted(packet_validated | provisional))
    capture_root = args.capture_root.resolve()
    captures = sorted(capture_root.rglob("*.dpscap"))
    if args.max_captures is not None:
        captures = captures[: max(0, int(args.max_captures))]
    if not captures:
        raise SystemExit(f"no captures found under {capture_root}")

    checkpoint_dir = args.checkpoint_dir.resolve()
    reports: list[dict[str, Any]] = []
    pending: list[Path] = []
    if not args.no_resume:
        for capture in captures:
            cached = _load_checkpoint(checkpoint_dir, capture)
            if cached is None:
                pending.append(capture)
            else:
                reports.append(cached)
    else:
        pending = list(captures)
    print(
        f"captures={len(captures)} resumed={len(reports)} pending={len(pending)}",
        flush=True,
    )

    errors: list[dict[str, str]] = []
    workers = max(1, int(args.workers))
    if pending and workers == 1:
        for completed, capture in enumerate(pending, start=1):
            try:
                report = _audit_capture(str(capture), all_templates)
                reports.append(report)
                _write_checkpoint(checkpoint_dir, capture, report)
            except Exception as exc:
                errors.append({"capture": str(capture), "error": repr(exc)})
            if completed % 5 == 0 or completed == len(pending):
                print(
                    f"audited_new={completed}/{len(pending)} total={len(reports)}/"
                    f"{len(captures)} errors={len(errors)}",
                    flush=True,
                )
    elif pending:
        # The low-memory two-pass worker is safe to reuse.  On Windows,
        # ProcessPoolExecutor can fail to replenish spawn workers after
        # max_tasks_per_child is reached, leaving the parent waiting forever;
        # keeping the bounded-memory workers alive avoids that deadlock.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_audit_capture, str(path), all_templates): path
                for path in pending
            }
            completed = 0
            for future in as_completed(futures):
                capture = futures[future]
                completed += 1
                try:
                    report = future.result()
                    reports.append(report)
                    _write_checkpoint(checkpoint_dir, capture, report)
                except Exception as exc:
                    errors.append({"capture": str(capture), "error": repr(exc)})
                if completed % 5 == 0 or completed == len(pending):
                    print(
                        f"audited_new={completed}/{len(pending)} total={len(reports)}/"
                        f"{len(captures)} errors={len(errors)}",
                        flush=True,
                    )

    reports.sort(key=lambda row: row["capture"])
    observations = _aggregate_observations(reports)
    shapes, accepted_shapes = _classify_shapes(
        observations,
        packet_validated=packet_validated,
        families=families,
    )
    template_results = _classify_templates(
        provenance,
        observations,
        accepted_shapes,
        families,
    )
    upgraded = [row for row in template_results if row["upgrade_to_packet_validated"]]
    china_tz = timezone(timedelta(hours=8))
    result = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(china_tz).isoformat(),
        "policy": {
            "allowed": [
                "exact action skill field plus independently calibrated numeric template field",
                "exact 13 4A/13 4E/13 47 status skill field plus independently calibrated numeric template field",
                "unique actor-scoped registration object ID repeated in a calibrated action/response numeric field",
            ],
            "forbidden": [
                "ACT/skill Chinese or resource string similarity",
                "nearest action in time",
                "direct result group",
                "damage fingerprint",
                "same-template direct37/direct41 exact packet skill anchor as upgrade evidence",
                "unvalidated same-packet co-occurrence",
            ],
            "shape_gate": {
                "minimum_records": MIN_SHAPE_RECORDS,
                "minimum_packet_validated_templates": MIN_SHAPE_TEMPLATES,
                "minimum_captures": MIN_SHAPE_CAPTURES,
                "minimum_actors": MIN_SHAPE_ACTORS,
                "conflicts": 0,
                "leave_one_capture_out": True,
            },
            "template_gate": {
                "minimum_records": MIN_UPGRADE_RECORDS,
                "minimum_captures": MIN_UPGRADE_CAPTURES,
                "minimum_actors": MIN_UPGRADE_ACTORS,
                "conflicts": 0,
                "leave_one_capture_out": True,
            },
        },
        "inputs": {
            "capture_root": str(capture_root),
            "provenance_json": str(args.provenance_json.resolve()),
            "checkpoint_dir": str(checkpoint_dir),
            "errors": errors,
        },
        "summary": {
            "provisional_templates": len(provisional),
            "packet_validated_calibration_templates": len(packet_validated),
            "captures_discovered": len(captures),
            "captures_audited": len(reports),
            "logical_packets": sum(int(row["logical_packets"]) for row in reports),
            "registrations": sum(int(row["registrations"]) for row in reports),
            "candidate_packets": sum(int(row["candidate_packets"]) for row in reports),
            "decoded_action_packets": sum(
                int(row["decoded_action_packets"]) for row in reports
            ),
            "decoded_status_records": sum(
                int(row["decoded_status_records"]) for row in reports
            ),
            "numeric_observation_groups": len(observations),
            "field_shapes_considered": len(shapes),
            "field_shapes_accepted": len(accepted_shapes),
            "upgradeable_templates": len(upgraded),
            "not_upgradeable_templates": len(template_results) - len(upgraded),
            "errors": len(errors),
        },
        "upgrades": [
            {
                "projectile_template_id": row["projectile_template_id"],
                "key": row["key"],
                "canonical_skill_id": row["canonical_skill_id"],
                "configured_skill_ids": row["configured_skill_ids"],
                "support_records": row["eligible_support_records"],
                "capture_count": row["eligible_capture_count"],
                "actor_count": row["eligible_actor_count"],
                "validated_shapes": row["validated_shapes"],
                "evidence": row["evidence"],
            }
            for row in upgraded
        ],
        "field_shapes": shapes,
        "templates": template_results,
        "observations": observations,
    }

    json_output = args.json_output.resolve()
    markdown_output = args.markdown_output.resolve()
    csv_output = args.csv_output.resolve()
    allowed = {DEFAULT_JSON.resolve(), DEFAULT_MARKDOWN.resolve(), DEFAULT_CSV.resolve()}
    if {json_output, markdown_output, csv_output} != allowed:
        raise SystemExit("outputs are restricted to the approved audit report paths")
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_output.write_text(_render_markdown(result), encoding="utf-8")
    _write_csv(csv_output, template_results)
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
