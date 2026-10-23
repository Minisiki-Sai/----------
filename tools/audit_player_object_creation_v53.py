"""Audit player object creation records against the native v576 client paths.

This is a read-only/offline acceptance tool.  It deliberately does not import
an alternate protocol decoder and never mutates production attribution state.
It checks three independently useful facts:

* complete typed ``0x0E``/``0x12`` action records and their first logical u32;
* ``kind 9`` records classified by receiver shape: player receivers use the
  A90370 stream while entity receivers may use the ``0x993D80`` compact path;
* ``kind 56`` A90370 streams and their explicit object/config/reference fields.

Every creation is retained as a separate owner+object generation.  Later
direct37 Hits are attached only to the latest preceding generation with the
same wire owner and object id.  Existing deterministic attribution is used
only as validation truth; it never selects a field or a candidate skill.

Run with Python 3.12 ``-B``.  At most two worker processes are permitted.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
from typing import Any, Iterable


sys.dont_write_bytecode = True
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
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    decode_v576_server_damage_object_registrations,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_native_meter_skill_key_v50 import (  # noqa: E402
    DEFAULT_CAPTURE_ROOT,
    DEFAULT_SELECTION,
    _capture_selection,
    _find_captures,
)


SCHEMA = "player-object-creation-audit-v53"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/player_object_creation_v53_20260909"
DEFAULT_JSON = DEFAULT_OUTPUT_DIR / "player_object_creation_v53.json"
DEFAULT_MD = DEFAULT_OUTPUT_DIR / "player_object_creation_v53.md"
PLAYER_ID_MIN = 0x01000000
OBJECT_ID_MAX = 0x00FFFFFF

# These methods do not use a current-action timing guess.  packet_skill_id is
# intentionally excluded because it may itself be the client's result key that
# object+0x1BC0 supplies to the built-in meter.
INDEPENDENT_TRUTH_METHODS = frozenset(
    {
        "damage_object_projectile_link",
        "packet_entity_skill_id",
        "packet_entity_projectile_selector",
        "packet_entity_self_attack",
        "packet_entity_template_owner",
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
    }
)

# Only these methods prove the requested parent/entity route itself.  Status
# ledgers remain valid independent truth for general validation, but must not
# be counted as an exact object-parent/entity link in the combination route.
EXACT_PARENT_ENTITY_METHODS = frozenset(
    {
        "damage_object_projectile_link",
        "packet_entity_skill_id",
        "packet_entity_projectile_selector",
        "packet_entity_self_attack",
        "packet_entity_template_owner",
    }
)

PRODUCTION_FILES = (
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_signature() -> dict[str, str]:
    return {name: _hash_file(ROOT / name) for name in PRODUCTION_FILES}


def _canonical(skill_id: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(skill_id), int(skill_id)))


def _positive_int(value: object) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return 0
    return result if result > 0 else 0


def _signed(raw: bytes) -> int:
    return int.from_bytes(raw, "little", signed=True)


@dataclass(frozen=True, slots=True)
class ActionFact:
    kind: int
    skill_id: int
    packet_index: int
    record_offset: int
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class Kind9Compact:
    object_id: int
    vector_raw: tuple[str, str, str, str]
    action_index_u32: int
    signal_key_u32: int
    extra_vector_flag: int
    extra_vector_raw: str | None
    consumed: int
    suffix_raw: str


@dataclass(frozen=True, slots=True)
class A90370Stream:
    opaque_seed_u32: int
    equipment_branch: int
    object_id: int
    config_row: int | None
    vector_raw: tuple[str, str, str, str]
    reference_branch_i8: int
    reference_id: int
    inventory_uid_branch: int | None
    inventory_uid: int | None
    indexed_item_reference: int | None
    action_index_i16: int
    signal_key_i8: int
    inline_definition_branch_i8: int
    consumed: int
    suffix_raw: str


def _take_reader(body: bytes):
    at = 0

    def take(count: int) -> bytes:
        nonlocal at
        if count < 0 or at + count > len(body):
            raise ValueError(f"truncated field at body+{at}, need {count}")
        value = body[at : at + count]
        at += count
        return value

    def position() -> int:
        return at

    return take, position


def _parse_kind9_compact(record: bytes) -> Kind9Compact:
    if (
        len(record) < 7
        or record[4] != 9
        or int.from_bytes(record[5:7], "little") + 7 != len(record)
    ):
        raise ValueError("not one complete kind9 record")
    body = record[7:]
    take, position = _take_reader(body)
    object_id = int.from_bytes(take(4), "little")
    vectors = tuple(take(size).hex() for size in (9, 6, 6, 6))
    action = int.from_bytes(take(4), "little")
    signal = int.from_bytes(take(4), "little")
    flag = take(1)[0]
    if flag not in (0, 1):
        raise ValueError(f"kind9 extra-vector flag is {flag}, expected 0/1")
    extra = take(6).hex() if flag else None
    consumed = position()
    return Kind9Compact(
        object_id,
        vectors,  # type: ignore[arg-type]
        action,
        signal,
        flag,
        extra,
        consumed,
        body[consumed:].hex(),
    )


def _parse_a90370(record: bytes) -> A90370Stream:
    if (
        len(record) < 7
        or record[4] not in (9, 56)
        or int.from_bytes(record[5:7], "little") + 7 != len(record)
    ):
        raise ValueError("not one complete kind9/kind56 record")
    body = record[7:]
    take, position = _take_reader(body)
    opaque = int.from_bytes(take(4), "little")
    equipment = take(1)[0]
    if equipment not in (0, 1):
        raise ValueError("equipment branch is not 0/1")
    object_id = int.from_bytes(take(4), "little")
    config = int.from_bytes(take(4), "little") if equipment == 0 else None
    vectors = tuple(take(size).hex() for size in (9, 6, 6, 6))
    reference_branch = _signed(take(1))
    if reference_branch not in (0, 1):
        raise ValueError("reference branch is not 0/1")
    reference_id = int.from_bytes(take(4), "little")
    uid_branch = uid = indexed = None
    if reference_branch == 1:
        uid_branch = take(1)[0]
        if uid_branch not in (0, 1):
            raise ValueError("uid branch is not 0/1")
        if uid_branch:
            uid = int.from_bytes(take(8), "little")
        else:
            indexed = int.from_bytes(take(4), "little")
    action = _signed(take(2))
    signal = _signed(take(1))
    inline = _signed(take(1))
    consumed = position()
    return A90370Stream(
        opaque,
        equipment,
        object_id,
        config,
        vectors,  # type: ignore[arg-type]
        reference_branch,
        reference_id,
        uid_branch,
        uid,
        indexed,
        action,
        signal,
        inline,
        consumed,
        body[consumed:].hex(),
    )


def _static_template_skills() -> dict[int, list[int]]:
    values: dict[int, set[int]] = defaultdict(set)
    for spec in _DAMAGE_OBJECT_SPECS:
        template = int(spec.projectile_template_id)
        skill = int(spec.canonical_skill_id)
        if template > 0 and skill > 0:
            values[template].add(_canonical(skill))
    return {key: sorted(rows) for key, rows in values.items()}


def _native_evidence() -> dict[str, Any]:
    """Pin only the static instructions required by this audit."""

    from tools.review_client_damage_types_v44 import (  # noqa: PLC0415
        BASE,
        CodeReview,
        StaticView,
        capstone,
    )

    view = StaticView()
    code = CodeReview(view)
    function = view.function(0x98F7D0)
    if function is None or function[0] != 0x98F7D0:
        raise ValueError("player network dispatcher boundary changed")
    dispatcher = [code.row(row) for row in code.decode(function)]
    by_rva = {row["rva"]: row for row in dispatcher}

    def rows_for(start: int) -> list[dict[str, Any]]:
        selected = view.function(start)
        if selected is None or selected[0] != start:
            raise ValueError(f"missing static function boundary {start:#x}")
        return [code.row(row) for row in code.decode(selected)]

    for start in (0x993D80, 0xA90370, 0xA4D4D0):
        for row in rows_for(start):
            by_rva[row["rva"]] = row
    for start, end in ((0x378EC4, 0x378EE4),):
        for insn in code.dis.disasm(view.read(start, end - start), BASE + start):
            by_rva[insn.address - BASE] = code.row(insn)

    expected: dict[int, tuple[str, str]] = {
        # kind 0x0E reads the first logical u32 and forwards it to player slot
        # 93 (+0x2E8).  0x12 uses the generic branch in this dispatcher, so it
        # is audited separately from, and never silently merged into, 0x0E.
        0x99077A: ("call", "0x14022f260"),
        0x9908A2: ("mov", "edx, dword ptr [rsp + 0x58]"),
        0x9908A6: ("call", "qword ptr [rax + 0x2e8]"),
        # Class-specific object-event dispatch.
        0x990F37: ("call", "0x140993d80"),
        0x990F67: ("call", "0x140a90370"),
        # kind9 compact reader and explicit registration.
        0x993DDE: ("call", "0x14022f260"),
        0x993DEC: ("mov", "r9d, 9"),
        0x993E60: ("lea", "rdx, [rsp + 0x44]"),
        0x993E74: ("lea", "rdx, [rsp + 0x48]"),
        0x994052: ("call", "0x140a8dea0"),
        0x99406A: ("call", "0x140c74540"),
        # kind56 A90370 reader, constructor and object-map registration.
        0xA9041B: ("call", "0x14022f260"),
        0xA9042E: ("call", "0x14022f260"),
        0xA90440: ("call", "0x14022f260"),
        0xA90459: ("call", "0x14022f260"),
        0xA90588: ("call", "0x14022f270"),
        0xA905A5: ("call", "0x14022f270"),
        0xA90E74: ("call", "0x140a8d190"),
        0xA90FBE: ("call", "0x140c74540"),
        # Current CDnSkill key -> projectile slot5 setter.  Slot5's exact leaf
        # and slot6's meter getter are pinned below as raw bytes.
        0x378EC4: ("lea", "rcx, [rsi + 0x10]"),
        0x378ED4: ("call", "qword ptr [rax + 0x10]"),
        0x378EE0: ("call", "qword ptr [r8 + 0x28]"),
        # Player slot93 resolves the skill row and updates MASkillUser +0x6A0.
        0xA4D507: ("add", "rcx, 0x6a0"),
        0xA4D513: ("call", "0x140376aa0"),
    }
    anchors = []
    for rva, pair in expected.items():
        row = by_rva.get(rva)
        if row is None or (row["mnemonic"], row["operands"]) != pair:
            raise ValueError(f"native anchor changed at {rva:#x}: {row}")
        anchors.append(row)

    indexes = view.read(0x991DB0, 100)
    targets = struct.unpack("<21I", view.read(0x991D5C, 84))
    dispatch = {kind: targets[indexes[kind - 1]] for kind in (9, 14, 18, 56)}
    if dispatch != {
        9: 0x990F2D,
        14: 0x990758,
        18: 0x991CB4,
        56: 0x990F41,
    }:
        raise ValueError(f"native dispatch table changed: {dispatch}")
    setter = view.read(0xA94C50, 7)
    getter = view.read(0xA91640, 7)
    if setter != bytes.fromhex("8991c01b0000c3"):
        raise ValueError("projectile +0x1BC0 setter changed")
    if getter != bytes.fromhex("8b81c01b0000c3"):
        raise ValueError("projectile +0x1BC0 getter changed")
    return {
        "client_sha256": sha256(view.raw).hexdigest(),
        "method": "static decoded client bytes only; no client execution",
        "dispatch": {f"0x{key:02X}": f"0x{value:X}" for key, value in dispatch.items()},
        "dispatcher_scope": (
            "0x98F7D0 is one receiver-class dispatcher, not a universal mapping "
            "from record kind to body layout"
        ),
        "anchors": anchors,
        "projectile_skill_field": {
            "offset": "object+0x1BC0",
            "setter_rva": "0xA94C50",
            "setter_bytes": setter.hex(),
            "getter_rva": "0xA91640",
            "getter_bytes": getter.hex(),
            "setter_vtable_slot": 5,
            "getter_vtable_slot": 6,
        },
        "important_boundary": (
            "The reviewed player dispatcher has a direct current-skill path for "
            "kind 0x0E; kind 0x12 reaches its generic branch.  Capture evidence "
            "therefore reports 0x0E, 0x12 and latest-of-either separately."
        ),
        "capstone_version": capstone.__version__,
    }


def _truth_event_map(events: Iterable[object]) -> dict[tuple[Any, ...], list[CombatEvent]]:
    result: dict[tuple[Any, ...], list[CombatEvent]] = defaultdict(list)
    for event in events:
        if (
            not isinstance(event, CombatEvent)
            or event.healing
            or int(event.amount) <= 0
            or int(event.wire_record_offset) <= 0
        ):
            continue
        family = "direct41" if "direct41" in event.wire_damage_layout else "direct37"
        result[
            (
                family,
                int(event.wire_record_offset),
                int(event.wire_source_actor_id),
                int(event.wire_target_actor_id),
                int(event.amount),
                bool(event.wire_critical),
            )
        ].append(event)
    return result


def _independent_truth(event: CombatEvent | None) -> int:
    if event is None:
        return 0
    method = str(event.attribution_method or "")
    skill = _positive_int(event.skill_id)
    if not skill:
        return 0
    if method in INDEPENDENT_TRUTH_METHODS:
        return _canonical(skill)
    if is_deterministic_attribution(method) and attribution_layer(method) in {
        AttributionLayer.OBJECT,
        AttributionLayer.ENTITY,
    }:
        return _canonical(skill)
    return 0


def _is_exact_parent_entity_method(method: str) -> bool:
    if method in EXACT_PARENT_ENTITY_METHODS:
        return True
    return is_deterministic_attribution(method) and attribution_layer(method) in {
        AttributionLayer.OBJECT,
        AttributionLayer.ENTITY,
    }


def _new_generation(
    *,
    capture: str,
    owner: int,
    object_id: int,
    generation: int,
    creation_kind: int,
    timestamp_ms: int,
    packet_index: int,
    record_offset: int,
    record_hash: str,
    packet_hash: str,
    parsed: dict[str, Any],
    current: dict[str, ActionFact | None],
    production_status: str,
    static_candidates: list[int],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "capture": capture,
        "owner_actor_id": owner,
        "owner": f"0x{owner:08X}",
        "damage_object_id": object_id,
        "generation": generation,
        "creation_kind": creation_kind,
        "creation_kind_hex": f"0x{creation_kind:02X}",
        "timestamp_ms": timestamp_ms,
        "packet_index": packet_index,
        "record_offset": record_offset,
        "wire_record_sha256": record_hash,
        "wire_packet_sha256": packet_hash,
        "production_status": production_status,
        "parsed": parsed,
        "static_template_skill_candidates": static_candidates,
        "candidate_skills": {},
        "hits": 0,
        "damage": 0,
        "truth_hits": 0,
        "named_hits": 0,
        "production_skill_ids": Counter(),
        "production_methods": Counter(),
        "truth_matches": Counter(),
        "truth_conflicts": Counter(),
        "truth_skill_ids": Counter(),
        "truth_methods": Counter(),
        "truth_method_skill_ids": Counter(),
        "jobs": Counter(),
    }
    for route, action in current.items():
        row["candidate_skills"][route] = (
            {
                "skill_id": action.skill_id,
                "kind": action.kind,
                "packet_index": action.packet_index,
                "record_offset": action.record_offset,
                "timestamp_ms": action.timestamp_ms,
                "age_ms": timestamp_ms - action.timestamp_ms,
            }
            if action is not None
            else None
        )
    return row


def _serialise_generation(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in (
        "truth_matches",
        "truth_conflicts",
        "truth_skill_ids",
        "truth_methods",
        "truth_method_skill_ids",
        "production_skill_ids",
        "production_methods",
        "jobs",
    ):
        result[key] = dict(row[key])
    return result


def _compact_generation(row: dict[str, Any]) -> dict[str, Any]:
    """Bounded evidence row; full-corpus completeness is committed by hash."""

    parsed = row["parsed"]
    return {
        "capture": row["capture"],
        "owner": row["owner"],
        "owner_primary_job": row.get("owner_primary_job", ""),
        "receiver_class_evidence": row.get("receiver_class_evidence", ""),
        "damage_object_id": row["damage_object_id"],
        "generation": row["generation"],
        "creation_kind_hex": row["creation_kind_hex"],
        "timestamp_ms": row["timestamp_ms"],
        "packet_index": row["packet_index"],
        "record_offset": row["record_offset"],
        "wire_record_sha256": row["wire_record_sha256"],
        "wire_packet_sha256": row["wire_packet_sha256"],
        "production_status": row["production_status"],
        "fields": {
            key: parsed.get(key)
            for key in (
                "record_header_hex",
                "declared_body_length",
                "opaque_seed_u32",
                "equipment_branch",
                "object_id",
                "config_row",
                "reference_branch_i8",
                "reference_id",
                "inventory_uid_branch",
                "inventory_uid",
                "indexed_item_reference",
                "action_index_i16",
                "signal_key_i8",
                "inline_definition_branch_i8",
                "consumed",
            )
        },
        "candidate_skills": row["candidate_skills"],
        "static_template_skill_candidates": row["static_template_skill_candidates"],
        "hits": row["hits"],
        "damage": row["damage"],
        "truth_hits": row["truth_hits"],
        "named_hits": row["named_hits"],
        "production_skill_ids": dict(row["production_skill_ids"]),
        "production_methods": dict(row["production_methods"]),
        "truth_skill_ids": dict(row["truth_skill_ids"]),
        "truth_methods": dict(row["truth_methods"]),
        "truth_method_skill_ids": dict(row["truth_method_skill_ids"]),
        "truth_matches": dict(row["truth_matches"]),
        "truth_conflicts": dict(row["truth_conflicts"]),
    }


def _capture_generation_summary(
    generations: list[dict[str, Any]],
) -> dict[str, Any]:
    route_rows: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    route_owners: dict[tuple[int, str], set[int]] = defaultdict(set)
    layouts: dict[int, Counter[str]] = defaultdict(Counter)
    fields: Counter[str] = Counter()
    conflict_causes: Counter[str] = Counter()
    conflict_dimensions: Counter[str] = Counter()
    conflict_pairs: Counter[str] = Counter()
    static_conflict_pairs: Counter[str] = Counter()
    static_conflict_dimensions: Counter[str] = Counter()
    unresolved_fallback_conflict_pairs: Counter[str] = Counter()
    unresolved_fallback_conflict_dimensions: Counter[str] = Counter()
    combination: Counter[str] = Counter()
    combination_by_job: dict[str, Counter[str]] = defaultdict(Counter)
    action_signal_groups: dict[str, dict[str, Any]] = {}
    digest = sha256()
    samples: dict[str, list[dict[str, Any]]] = {
        "production_missed": [],
        "current_skill_conflicts": [],
        "kind56": [],
        "linked_positive": [],
        "unconfirmed_receiver": [],
        "reused_owner_object": [],
        "unique_static_conflicts": [],
        "unresolved_fallback_conflicts": [],
    }
    sample_limits = {
        "production_missed": 25,
        "current_skill_conflicts": 50,
        "kind56": 25,
        "linked_positive": 25,
        "unconfirmed_receiver": 25,
        "reused_owner_object": 25,
        "unique_static_conflicts": 100,
        "unresolved_fallback_conflicts": 100,
    }

    def keep(bucket: str, row: dict[str, Any]) -> None:
        if len(samples[bucket]) < sample_limits[bucket]:
            samples[bucket].append(_compact_generation(row))

    for row in generations:
        kind = int(row["creation_kind"])
        parsed = row["parsed"]
        confirmed_player = (
            row.get("receiver_class_evidence") == "combat_event_job_player"
        )
        fields[f"kind:{kind}"] += 1
        for name in (
            "declared_body_length",
            "equipment_branch",
            "reference_branch_i8",
            "action_index_i16",
            "signal_key_i8",
            "inline_definition_branch_i8",
            "consumed",
        ):
            fields[f"kind:{kind}|{name}:{parsed.get(name)}"] += 1
        fields[
            f"kind:{kind}|root_self:{int(parsed.get('reference_branch_i8') == 0 and parsed.get('reference_id') == row['owner_actor_id'])}"
        ] += 1
        config = parsed.get("config_row")
        if config is not None:
            fields[f"kind:{kind}|config_row:{config}"] += 1
        suffix_hex = str(parsed.get("suffix_raw") or "")
        fields[f"kind:{kind}|suffix_bytes:{len(suffix_hex) // 2}"] += 1

        commitment = {
            "owner": row["owner_actor_id"],
            "object": row["damage_object_id"],
            "generation": row["generation"],
            "kind": kind,
            "record": row["wire_record_sha256"],
            "production": row["production_status"],
            "fields": {
                key: parsed.get(key)
                for key in (
                    "declared_body_length",
                    "equipment_branch",
                    "config_row",
                    "reference_branch_i8",
                    "reference_id",
                    "action_index_i16",
                    "signal_key_i8",
                    "inline_definition_branch_i8",
                )
            },
            "hits": row["hits"],
            "truth": dict(sorted(row["truth_skill_ids"].items())),
        }
        digest.update(
            json.dumps(commitment, sort_keys=True, separators=(",", ":")).encode()
        )

        if not confirmed_player:
            keep("unconfirmed_receiver", row)
            continue
        if int(row["generation"]) > 1:
            keep("reused_owner_object", row)
        layouts[kind][str(row["production_status"])] += 1
        if row["production_status"] != "matched_owner_object":
            keep("production_missed", row)
        if kind == 56:
            keep("kind56", row)
        if int(row["hits"]) and int(row["truth_hits"]):
            keep("linked_positive", row)
        if int(row["truth_conflicts"].get("latest_0e_or_12", 0)):
            keep("current_skill_conflicts", row)
            amount = int(row["truth_conflicts"]["latest_0e_or_12"])
            latest = row["candidate_skills"].get("latest_0e_or_12") or {}
            current_skill = _canonical(int(latest.get("skill_id") or 0))
            truth_skills = {
                _canonical(int(skill))
                for skill, count in row["truth_skill_ids"].items()
                if int(count) > 0
            }
            static_skills = {
                _canonical(int(skill))
                for skill in row["static_template_skill_candidates"]
            }
            if len(truth_skills) > 1:
                cause = "one_object_maps_to_multiple_truth_skills"
            elif truth_skills and truth_skills.issubset(static_skills):
                cause = "projectile_template_child_differs_from_current_action"
            else:
                cause = "current_action_is_not_final_damage_skill"
            conflict_causes[cause] += amount
            conflict_dimensions[
                "reused_numeric_object_id"
                if int(row["generation"]) > 1
                else "first_observed_numeric_object_id"
            ] += amount
            conflict_dimensions[
                "production_exact_match"
                if row["production_status"] == "matched_owner_object"
                else "production_record_classification_miss"
            ] += amount
            conflict_dimensions[f"record_kind_0x{kind:02X}"] += amount
            for truth in sorted(truth_skills):
                conflict_pairs[
                    f"current:{current_skill}|truth:{truth}|config:{parsed.get('config_row')}|kind:{kind}"
                ] += sum(
                    int(count)
                    for skill, count in row["truth_skill_ids"].items()
                    if _canonical(int(skill)) == truth
                )

        job = str(row.get("owner_primary_job") or "未识别职业")
        job_combo = combination_by_job[job]
        static_unique = sorted(
            {
                _canonical(int(skill))
                for skill in row["static_template_skill_candidates"]
                if int(skill) > 0
            }
        )
        truth_counts: Counter[int] = Counter()
        for skill, count in row["truth_skill_ids"].items():
            if int(count) > 0:
                truth_counts[_canonical(int(skill))] += int(count)
        exact_truth_counts: Counter[int] = Counter()
        for method_skill, count in row["truth_method_skill_ids"].items():
            method, skill_text = str(method_skill).rsplit("|", 1)
            if int(count) > 0 and _is_exact_parent_entity_method(method):
                exact_truth_counts[_canonical(int(skill_text))] += int(count)
        hits = int(row["hits"])
        named_hits = int(row["named_hits"])
        truth_hits = int(row["truth_hits"])
        potential_new = max(0, hits - named_hits)
        latest = row["candidate_skills"].get("latest_0e_or_12") or {}
        latest_skill = _canonical(int(latest.get("skill_id") or 0))
        if len(static_unique) == 1:
            candidate = static_unique[0]
            matches = int(truth_counts.get(candidate, 0))
            conflicts = max(0, truth_hits - matches)
            if conflicts:
                keep("unique_static_conflicts", row)
                truth_skills = [skill for skill, count in truth_counts.items() if count > 0]
                static_conflict_dimensions[
                    "one_generation_has_multiple_truth_skills"
                    if len(truth_skills) > 1
                    else "one_generation_has_single_truth_skill"
                ] += conflicts
                static_conflict_dimensions[
                    "reused_numeric_object_id"
                    if int(row["generation"]) > 1
                    else "first_observed_numeric_object_id"
                ] += conflicts
                static_conflict_dimensions[
                    "production_exact_match"
                    if row["production_status"] == "matched_owner_object"
                    else "production_record_classification_miss"
                ] += conflicts
                latest_relation = "latest_matches_neither"
                if latest_skill == candidate:
                    latest_relation = "latest_matches_static_candidate"
                elif latest_skill and latest_skill in truth_counts:
                    latest_relation = "latest_matches_truth"
                static_conflict_dimensions[latest_relation] += conflicts
                for truth_skill, count in truth_counts.items():
                    if truth_skill == candidate or count <= 0:
                        continue
                    static_conflict_pairs[
                        f"job:{job}|config:{parsed.get('config_row')}|"
                        f"static:{candidate}|truth:{truth_skill}|kind:{kind}"
                    ] += int(count)
            values = {
                "unique_static_generations": 1,
                "unique_static_hits": hits,
                "unique_static_truth_hits": truth_hits,
                "unique_static_truth_matches": matches,
                "unique_static_truth_conflicts": conflicts,
                "unique_static_potential_new_hits": potential_new,
            }
        else:
            exact_truth_hits = sum(exact_truth_counts.values())
            shadow_matches = (
                int(exact_truth_counts.get(latest_skill, 0)) if latest_skill else 0
            )
            shadow_conflicts = max(0, exact_truth_hits - shadow_matches)
            fallback_hits = max(0, hits - exact_truth_hits)
            if shadow_conflicts:
                keep("unresolved_fallback_conflicts", row)
                unresolved_fallback_conflict_dimensions[
                    "one_generation_has_multiple_exact_truth_skills"
                    if len(exact_truth_counts) > 1
                    else "one_generation_has_single_exact_truth_skill"
                ] += shadow_conflicts
                unresolved_fallback_conflict_dimensions[
                    "reused_numeric_object_id"
                    if int(row["generation"]) > 1
                    else "first_observed_numeric_object_id"
                ] += shadow_conflicts
                unresolved_fallback_conflict_dimensions[
                    "production_exact_match"
                    if row["production_status"] == "matched_owner_object"
                    else "production_record_classification_miss"
                ] += shadow_conflicts
                for truth_skill, count in exact_truth_counts.items():
                    if truth_skill == latest_skill or count <= 0:
                        continue
                    unresolved_fallback_conflict_pairs[
                        f"job:{job}|config:{parsed.get('config_row')}|"
                        f"latest:{latest_skill}|truth:{truth_skill}|kind:{kind}|"
                        f"action:{parsed.get('action_index_i16')}|"
                        f"signal:{parsed.get('signal_key_i8')}"
                    ] += int(count)
            values = {
                "template_unresolved_generations": 1,
                "exact_parent_entity_chain_hits": exact_truth_hits,
                "template_unresolved_fallback_hits": fallback_hits,
                "template_unresolved_fallback_shadow_truth_matches": shadow_matches,
                "template_unresolved_fallback_shadow_truth_conflicts": shadow_conflicts,
                "template_unresolved_potential_new_hits": potential_new,
            }
            parsed = row["parsed"]
            group_key = (
                f"job:{job}|kind:{kind}|config:{parsed.get('config_row')}|"
                f"action:{parsed.get('action_index_i16')}|"
                f"signal:{parsed.get('signal_key_i8')}"
            )
            group = action_signal_groups.setdefault(
                group_key,
                {
                    "job": job,
                    "kind": kind,
                    "config_row": parsed.get("config_row"),
                    "action_index_i16": parsed.get("action_index_i16"),
                    "signal_key_i8": parsed.get("signal_key_i8"),
                    "generations": 0,
                    "hits": 0,
                    "named_hits": 0,
                    "truth_hits": 0,
                    "truth_skill_ids": Counter(),
                    "latest_skill_ids": Counter(),
                    "owners": set(),
                },
            )
            group["generations"] += 1
            group["hits"] += hits
            group["named_hits"] += named_hits
            group["truth_hits"] += exact_truth_hits
            group["truth_skill_ids"].update(exact_truth_counts)
            if latest_skill:
                group["latest_skill_ids"][latest_skill] += hits
            group["owners"].add(int(row["owner_actor_id"]))
        combination.update(values)
        job_combo.update(values)
        for route, candidate in row["candidate_skills"].items():
            if not candidate:
                continue
            key = (kind, route)
            route_owners[key].add(int(row["owner_actor_id"]))
            route_rows[key]["candidate_generations"] += 1
            route_rows[key]["hits"] += int(row["hits"])
            route_rows[key]["truth_hits"] += int(row["truth_hits"])
            route_rows[key]["matches"] += int(row["truth_matches"].get(route, 0))
            route_rows[key]["conflicts"] += int(row["truth_conflicts"].get(route, 0))

    routes = []
    for (kind, route), counter in sorted(route_rows.items()):
        routes.append(
            {
                "creation_kind": kind,
                "current_skill_route": route,
                **dict(counter),
                "owners": sorted(route_owners[(kind, route)]),
            }
        )
    action_signal_rows = []
    for key, group in sorted(action_signal_groups.items()):
        action_signal_rows.append(
            {
                **{name: value for name, value in group.items() if name not in {
                    "truth_skill_ids", "latest_skill_ids", "owners"
                }},
                "key": key,
                "truth_skill_ids": {
                    str(skill): count
                    for skill, count in sorted(group["truth_skill_ids"].items())
                },
                "latest_skill_ids": {
                    str(skill): count
                    for skill, count in sorted(group["latest_skill_ids"].items())
                },
                "owners": sorted(group["owners"]),
            }
        )
    return {
        "generation_count": len(generations),
        "generation_commitment_sha256": digest.hexdigest(),
        "field_profile": dict(fields),
        "latest_conflict_causes": dict(conflict_causes),
        "latest_conflict_dimensions": dict(conflict_dimensions),
        "latest_conflict_pairs": dict(conflict_pairs),
        "unique_static_conflict_pairs": dict(static_conflict_pairs),
        "unique_static_conflict_dimensions": dict(static_conflict_dimensions),
        "unresolved_fallback_conflict_pairs": dict(
            unresolved_fallback_conflict_pairs
        ),
        "unresolved_fallback_conflict_dimensions": dict(
            unresolved_fallback_conflict_dimensions
        ),
        "combination_route": dict(combination),
        "combination_route_by_job": {
            job: dict(counter) for job, counter in sorted(combination_by_job.items())
        },
        "action_signal_groups": action_signal_rows,
        "production_parser_by_kind": {
            str(kind): dict(counter) for kind, counter in sorted(layouts.items())
        },
        "current_skill_routes": routes,
        "evidence_samples": samples,
    }


def _audit_capture(path: Path) -> dict[str, Any]:
    known_skills = frozenset(int(value) for value in _load_damage_skill_ids(str(DATA_DIR)))
    static_skills = _static_template_skills()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    capture_hash = _hash_file(path)
    counts: Counter[str] = Counter()
    parse_errors: Counter[str] = Counter()
    jobs_by_actor: dict[int, Counter[str]] = defaultdict(Counter)
    current_0e: dict[int, ActionFact] = {}
    current_12: dict[int, ActionFact] = {}
    current_any: dict[int, ActionFact] = {}
    generation_number: Counter[tuple[int, int]] = Counter()
    active: dict[tuple[int, int], dict[str, Any]] = {}
    generations: list[dict[str, Any]] = []
    all_a90370_pairs: set[tuple[int, int]] = set()
    root_a90370_pairs: set[tuple[int, int]] = set()
    production_pairs: set[tuple[int, int]] = set()
    unlinked_hits: list[dict[str, Any]] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        events = original(packet)
        for event in events:
            if isinstance(event, CombatEvent) and event.job:
                actor = int(event.wire_source_actor_id or 0)
                if actor >= PLAYER_ID_MIN:
                    jobs_by_actor[actor][str(event.job)] += 1
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            counts[f"unframed:{inventory.rejection_reason}"] += 1
            return ()
        counts["complete_typed_packets"] += 1
        truth_events = _truth_event_map(events)
        production_rows = decode_v576_server_damage_object_registrations(
            packet.payload,
            packet.inferred_direction(),
            include_unmapped=True,
        )
        production_by_marker: dict[int, list[object]] = defaultdict(list)
        for production in production_rows:
            production_by_marker[int(production.record_offset)].append(production)
            production_pairs.add(
                (int(production.source_actor_id), int(production.damage_object_id))
            )

        damage_by_marker = {
            int(row.record_offset): row for row in inventory.damage_records
        }
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        for typed in sorted(inventory.typed_records, key=lambda row: row.record_offset):
            record = bytes(typed.record_bytes)
            owner = int.from_bytes(record[:4], "little")
            kind = int(typed.record_type)
            marker = int(typed.record_offset)
            if kind in (0x0E, 0x12):
                counts[f"action_kind_{kind:02x}_records"] += 1
                if len(record) < 11:
                    counts[f"action_kind_{kind:02x}_truncated"] += 1
                    continue
                skill = int.from_bytes(record[7:11], "little")
                if owner < PLAYER_ID_MIN or skill not in known_skills:
                    counts[f"action_kind_{kind:02x}_invalid_owner_or_skill"] += 1
                    continue
                fact = ActionFact(kind, skill, packet_index, marker, timestamp_ms)
                if kind == 0x0E:
                    current_0e[owner] = fact
                else:
                    current_12[owner] = fact
                current_any[owner] = fact
                counts[f"action_kind_{kind:02x}_accepted"] += 1
                continue

            if kind in (9, 56):
                counts[f"kind_{kind}_records"] += 1
                compact = None
                compact_error = ""
                if kind == 9:
                    try:
                        compact = _parse_kind9_compact(record)
                        counts["kind_9_compact_shape"] += 1
                    except ValueError as exc:
                        compact_error = str(exc)
                        counts["kind_9_not_compact_shape"] += 1
                try:
                    parsed_object = _parse_a90370(record)
                    counts[f"kind_{kind}_a90370_shape"] += 1
                except ValueError as exc:
                    counts[f"kind_{kind}_not_a90370_shape"] += 1
                    # Compact kind9 belongs to the entity receiver path and is
                    # retained as a shape counter, not promoted to a player
                    # object generation.  Exact numeric branch values are not
                    # useful as separate error classes.
                    if compact is None:
                        parse_errors[f"kind{kind}:neither_a90370_nor_compact"] += 1
                        if compact_error:
                            parse_errors[f"kind{kind}:compact_shape_rejected"] += 1
                        parse_errors[f"kind{kind}:a90370:{exc}"] += 1
                    elif production_by_marker.get(marker):
                        counts["production_false_positive_on_compact_kind9"] += len(
                            production_by_marker[marker]
                        )
                    continue
                parsed = asdict(parsed_object)
                parsed["record_kind"] = kind
                parsed["declared_body_length"] = int.from_bytes(
                    record[5:7], "little"
                )
                parsed["record_header_hex"] = record[4:7].hex()
                object_id = int(parsed_object.object_id)
                if not 0 < object_id <= OBJECT_ID_MAX:
                    parse_errors[f"kind{kind}:object_out_of_range"] += 1
                    continue
                root_self_reference = (
                    int(parsed_object.reference_branch_i8) == 0
                    and int(parsed_object.reference_id) == owner
                )
                all_a90370_pairs.add((owner, object_id))
                if owner < PLAYER_ID_MIN or not root_self_reference:
                    counts[f"kind_{kind}_a90370_non_player_candidate"] += 1
                    continue
                root_a90370_pairs.add((owner, object_id))
                counts[f"player_kind_{kind}_records"] += 1
                same_marker = production_by_marker.get(marker, ())
                exact = [
                    row
                    for row in same_marker
                    if int(row.source_actor_id) == owner
                    and int(row.damage_object_id) == object_id
                ]
                if exact:
                    production_status = "matched_owner_object"
                elif same_marker:
                    production_status = "same_marker_wrong_owner_or_object"
                else:
                    production_status = "missed"
                counts[f"player_kind_{kind}_{production_status}"] += 1
                pair = (owner, object_id)
                generation_number[pair] += 1
                config = (
                    int(parsed_object.config_row or 0)
                    if isinstance(parsed_object, A90370Stream)
                    else 0
                )
                row = _new_generation(
                    capture=path.name,
                    owner=owner,
                    object_id=object_id,
                    generation=generation_number[pair],
                    creation_kind=kind,
                    timestamp_ms=timestamp_ms,
                    packet_index=packet_index,
                    record_offset=marker,
                    record_hash=typed.wire_record_sha256,
                    packet_hash=inventory.wire_sha256,
                    parsed=parsed,
                    current={
                        "kind0e": current_0e.get(owner),
                        "kind12": current_12.get(owner),
                        "latest_0e_or_12": current_any.get(owner),
                    },
                    production_status=production_status,
                    static_candidates=static_skills.get(config, []),
                )
                active[pair] = row
                generations.append(row)
                counts[f"player_kind_{kind}_parsed"] += 1
                continue

            if kind != 8:
                continue
            physical = damage_by_marker.get(marker)
            if (
                physical is None
                or physical.family != "direct37"
                or not physical.admissible_fields
                or int(physical.source_actor_id) < PLAYER_ID_MIN
                or not 0 < int(physical.damage_object_id or 0) <= OBJECT_ID_MAX
            ):
                continue
            pair = (
                int(physical.source_actor_id),
                int(physical.damage_object_id or 0),
            )
            generation = active.get(pair)
            if generation is None:
                counts["player_direct37_without_audited_creation"] += 1
                key = (
                    "direct37",
                    marker,
                    int(physical.source_actor_id),
                    int(physical.target_actor_id),
                    int(physical.damage),
                    bool(physical.critical),
                )
                candidates = truth_events.get(key, ())
                event = candidates[0] if len(candidates) == 1 else None
                unlinked_hits.append(
                    {
                        "owner_actor_id": pair[0],
                        "damage_object_id": pair[1],
                        "packet_index": packet_index,
                        "record_offset": marker,
                        "timestamp_ms": timestamp_ms,
                        "damage": int(physical.damage),
                        "truth_skill_id": _independent_truth(event),
                        "truth_method": str(event.attribution_method or "") if event else "",
                        "job": str(event.job or "") if event else "",
                    }
                )
                continue
            generation["hits"] += 1
            generation["damage"] += int(physical.damage)
            key = (
                "direct37",
                marker,
                int(physical.source_actor_id),
                int(physical.target_actor_id),
                int(physical.damage),
                bool(physical.critical),
            )
            candidates = truth_events.get(key, ())
            event = candidates[0] if len(candidates) == 1 else None
            if event is not None and event.job:
                generation["jobs"][str(event.job)] += 1
            named_skill = _positive_int(event.skill_id) if event is not None else 0
            if named_skill:
                generation["named_hits"] += 1
                generation["production_skill_ids"][str(_canonical(named_skill))] += 1
                generation["production_methods"][str(event.attribution_method or "")] += 1
            truth = _independent_truth(event)
            if not truth:
                continue
            generation["truth_hits"] += 1
            generation["truth_skill_ids"][str(truth)] += 1
            truth_method = str(event.attribution_method)
            generation["truth_methods"][truth_method] += 1
            generation["truth_method_skill_ids"][f"{truth_method}|{truth}"] += 1
            for route, candidate in generation["candidate_skills"].items():
                candidate_skill = int(candidate["skill_id"]) if candidate else 0
                if candidate_skill and _canonical(candidate_skill) == truth:
                    generation["truth_matches"][route] += 1
                elif candidate_skill:
                    generation["truth_conflicts"][route] += 1
        return ()

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    # Finish job labels after the complete capture has populated actor facts.
    for row in generations:
        actor_jobs = jobs_by_actor.get(int(row["owner_actor_id"]), Counter())
        row["owner_jobs"] = dict(actor_jobs)
        row["owner_primary_job"] = actor_jobs.most_common(1)[0][0] if actor_jobs else ""
        row["receiver_class_evidence"] = (
            "combat_event_job_player" if actor_jobs else "root_self_reference_only"
        )
        kind = int(row["creation_kind"])
        if actor_jobs:
            counts[f"confirmed_player_kind_{kind}_generations"] += 1
        else:
            counts[f"unconfirmed_receiver_kind_{kind}_generations"] += 1
        if int(row["hits"]) > 0:
            counts["generations_with_hits"] += 1
            if actor_jobs:
                counts["confirmed_player_generations_with_hits"] += 1
        if int(row["truth_hits"]) > 0:
            counts["generations_with_independent_truth"] += 1
    counts["logical_packets"] = packet_index
    counts["generations"] = len(generations)
    counts["owner_object_pairs"] = len(generation_number)
    counts["reused_owner_object_pairs"] = sum(
        amount > 1 for amount in generation_number.values()
    )
    unlinked_reasons: Counter[str] = Counter()
    unlinked_truth_skills: Counter[str] = Counter()
    unlinked_samples: list[dict[str, Any]] = []
    for hit in unlinked_hits:
        pair = (int(hit["owner_actor_id"]), int(hit["damage_object_id"]))
        if pair in root_a90370_pairs:
            reason = "root_a90370_exists_only_after_hit_or_was_superseded"
        elif pair in all_a90370_pairs:
            reason = "a90370_exists_but_receiver_or_reference_not_player_root"
        elif pair in production_pairs:
            reason = "production_only_registration_not_reproduced_by_a90370_parser"
        else:
            reason = "no_complete_a90370_registration_in_capture"
        unlinked_reasons[reason] += 1
        if int(hit["truth_skill_id"]):
            unlinked_truth_skills[str(hit["truth_skill_id"])] += 1
        if len(unlinked_samples) < 100:
            unlinked_samples.append(
                {
                    **hit,
                    "owner": f"0x{int(hit['owner_actor_id']):08X}",
                    "reason": reason,
                }
            )
    generation_summary = _capture_generation_summary(generations)
    return {
        "capture": path.name,
        "path": str(path),
        "capture_sha256": capture_hash,
        "counts": dict(counts),
        "parse_errors": dict(parse_errors),
        "unlinked_direct37": {
            "reasons": dict(unlinked_reasons),
            "independent_truth_skills": dict(unlinked_truth_skills),
            "samples": unlinked_samples,
        },
        **generation_summary,
    }


def _aggregate(captures: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    parse_errors: Counter[str] = Counter()
    routes: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    route_captures: dict[tuple[int, str], set[str]] = defaultdict(set)
    route_owners: dict[tuple[int, str], set[tuple[str, int]]] = defaultdict(set)
    layouts: dict[int, Counter[str]] = defaultdict(Counter)
    fields: Counter[str] = Counter()
    conflict_causes: Counter[str] = Counter()
    conflict_dimensions: Counter[str] = Counter()
    conflict_pairs: Counter[str] = Counter()
    static_conflict_pairs: Counter[str] = Counter()
    static_conflict_dimensions: Counter[str] = Counter()
    unresolved_fallback_conflict_pairs: Counter[str] = Counter()
    unresolved_fallback_conflict_dimensions: Counter[str] = Counter()
    evidence_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unlinked_reasons: Counter[str] = Counter()
    unlinked_truth_skills: Counter[str] = Counter()
    unlinked_samples: list[dict[str, Any]] = []
    combination: Counter[str] = Counter()
    combination_by_job: dict[str, Counter[str]] = defaultdict(Counter)
    action_signal_groups: dict[str, dict[str, Any]] = {}
    for capture in captures:
        totals.update(capture["counts"])
        parse_errors.update(capture["parse_errors"])
        fields.update(capture["field_profile"])
        conflict_causes.update(capture["latest_conflict_causes"])
        conflict_dimensions.update(capture["latest_conflict_dimensions"])
        conflict_pairs.update(capture["latest_conflict_pairs"])
        static_conflict_pairs.update(capture.get("unique_static_conflict_pairs", {}))
        static_conflict_dimensions.update(
            capture.get("unique_static_conflict_dimensions", {})
        )
        unresolved_fallback_conflict_pairs.update(
            capture.get("unresolved_fallback_conflict_pairs", {})
        )
        unresolved_fallback_conflict_dimensions.update(
            capture.get("unresolved_fallback_conflict_dimensions", {})
        )
        unlinked_reasons.update(capture["unlinked_direct37"]["reasons"])
        unlinked_truth_skills.update(
            capture["unlinked_direct37"]["independent_truth_skills"]
        )
        remaining_unlinked = 100 - len(unlinked_samples)
        if remaining_unlinked > 0:
            unlinked_samples.extend(
                capture["unlinked_direct37"]["samples"][:remaining_unlinked]
            )
        combination.update(capture["combination_route"])
        for job, counter in capture["combination_route_by_job"].items():
            combination_by_job[job].update(counter)
        for row in capture["action_signal_groups"]:
            key = str(row["key"])
            group = action_signal_groups.setdefault(
                key,
                {
                    name: row[name]
                    for name in (
                        "job",
                        "kind",
                        "config_row",
                        "action_index_i16",
                        "signal_key_i8",
                    )
                }
                | {
                    "generations": 0,
                    "hits": 0,
                    "named_hits": 0,
                    "truth_hits": 0,
                    "truth_skill_ids": Counter(),
                    "latest_skill_ids": Counter(),
                    "captures": set(),
                    "owners": set(),
                },
            )
            for name in ("generations", "hits", "named_hits", "truth_hits"):
                group[name] += int(row.get(name, 0))
            group["truth_skill_ids"].update(
                {int(skill): int(count) for skill, count in row["truth_skill_ids"].items()}
            )
            group["latest_skill_ids"].update(
                {int(skill): int(count) for skill, count in row["latest_skill_ids"].items()}
            )
            group["captures"].add(str(capture["capture"]))
            group["owners"].update(
                (str(capture["capture"]), int(owner)) for owner in row["owners"]
            )
        for kind_text, counter in capture["production_parser_by_kind"].items():
            layouts[int(kind_text)].update(counter)
        for bucket, rows in capture["evidence_samples"].items():
            remaining = 100 - len(evidence_samples[bucket])
            if remaining > 0:
                evidence_samples[bucket].extend(rows[:remaining])
        for row in capture["current_skill_routes"]:
            kind = int(row["creation_kind"])
            route = str(row["current_skill_route"])
            key = (kind, route)
            route_captures[key].add(str(capture["capture"]))
            route_owners[key].update(
                (str(capture["capture"]), int(owner)) for owner in row["owners"]
            )
            for name in (
                "candidate_generations",
                "hits",
                "truth_hits",
                "matches",
                "conflicts",
            ):
                routes[key][name] += int(row.get(name, 0))
    route_rows = []
    for key in sorted(routes):
        kind, route = key
        row = routes[key]
        verified = row["matches"] + row["conflicts"]
        zero_conflict = bool(verified and row["conflicts"] == 0)
        cross_capture = len(route_captures[key]) >= 2 and len(route_owners[key]) >= 2
        enough_support = verified >= 20
        route_rows.append(
            {
                "creation_kind": kind,
                "creation_kind_hex": f"0x{kind:02X}",
                "current_skill_route": route,
                **dict(row),
                "verified_precision_percent": round(
                    100.0 * row["matches"] / verified, 6
                )
                if verified
                else None,
                "captures": len(route_captures[key]),
                "owners": len(route_owners[key]),
                "zero_conflict": zero_conflict,
                "cross_capture": cross_capture,
                "enough_support": enough_support,
                "eligible_for_production_proposal": bool(
                    zero_conflict and cross_capture and enough_support
                ),
            }
        )
    proposals = [
        row for row in route_rows if row["eligible_for_production_proposal"]
    ]
    action_signal_rows = []
    action_signal_projection_by_job: dict[str, Counter[str]] = defaultdict(Counter)
    for key, group in sorted(action_signal_groups.items()):
        truth_skills = sorted(
            skill for skill, count in group["truth_skill_ids"].items() if count > 0
        )
        zero_conflict = len(truth_skills) == 1
        cross_capture = len(group["captures"]) >= 2 and len(group["owners"]) >= 2
        enough_support = int(group["truth_hits"]) >= 20
        eligible = zero_conflict and cross_capture and enough_support
        potential_new = max(0, int(group["hits"]) - int(group["named_hits"]))
        projected = potential_new if eligible else 0
        unresolved = potential_new - projected
        projection = action_signal_projection_by_job[str(group["job"])]
        projection["template_unresolved_hits"] += int(group["hits"])
        projection["action_signal_projected_new_hits"] += projected
        projection["action_signal_still_unresolved_hits"] += unresolved
        action_signal_rows.append(
            {
                **{
                    name: value
                    for name, value in group.items()
                    if name not in {
                        "truth_skill_ids",
                        "latest_skill_ids",
                        "captures",
                        "owners",
                    }
                },
                "key": key,
                "truth_skill_ids": {
                    str(skill): count
                    for skill, count in sorted(group["truth_skill_ids"].items())
                },
                "latest_skill_ids": {
                    str(skill): count
                    for skill, count in sorted(group["latest_skill_ids"].items())
                },
                "captures": sorted(group["captures"]),
                "owners": len(group["owners"]),
                "zero_truth_conflict": zero_conflict,
                "cross_capture": cross_capture,
                "enough_support": enough_support,
                "eligible_action_signal_resolution": eligible,
                "potential_new_hits": potential_new,
            }
        )
    action_signal_summary = Counter()
    for row in action_signal_rows:
        action_signal_summary["groups"] += 1
        action_signal_summary["truth_hits"] += int(row["truth_hits"])
        action_signal_summary["potential_new_hits"] += int(row["potential_new_hits"])
        if row["eligible_action_signal_resolution"]:
            action_signal_summary["eligible_groups"] += 1
            action_signal_summary["eligible_truth_hits"] += int(row["truth_hits"])
            action_signal_summary["eligible_potential_new_hits"] += int(
                row["potential_new_hits"]
            )
        elif int(row["truth_hits"]) and not row["zero_truth_conflict"]:
            action_signal_summary["conflicting_groups"] += 1
            action_signal_summary["conflicting_truth_hits"] += int(row["truth_hits"])
    combination_acceptance = {
        "unique_static_zero_conflict": int(
            combination.get("unique_static_truth_conflicts", 0)
        )
        == 0,
        "template_unresolved_latest_zero_conflict": int(
            combination.get(
                "template_unresolved_fallback_shadow_truth_conflicts", 0
            )
        )
        == 0,
        "action_signal_can_exactly_exclude_all_observed_conflicts": int(
            action_signal_summary.get("conflicting_groups", 0)
        )
        == 0,
    }
    combination_acceptance["safe_for_production"] = all(
        combination_acceptance.values()
    )
    return {
        "totals": dict(totals),
        "parse_errors": dict(parse_errors),
        "production_parser_by_kind": {
            f"0x{kind:02X}": dict(counter)
            for kind, counter in sorted(layouts.items())
        },
        "current_skill_validation": route_rows,
        "production_proposals": proposals,
        "field_profile": dict(fields),
        "latest_conflict_causes": dict(conflict_causes),
        "latest_conflict_dimensions": dict(conflict_dimensions),
        "latest_conflict_pairs": dict(conflict_pairs),
        "unique_static_conflict_pairs": dict(static_conflict_pairs),
        "unique_static_conflict_dimensions": dict(static_conflict_dimensions),
        "unresolved_fallback_conflict_pairs": dict(
            unresolved_fallback_conflict_pairs
        ),
        "unresolved_fallback_conflict_dimensions": dict(
            unresolved_fallback_conflict_dimensions
        ),
        "evidence_samples": dict(evidence_samples),
        "unlinked_direct37": {
            "reasons": dict(unlinked_reasons),
            "independent_truth_skills": dict(unlinked_truth_skills),
            "samples": unlinked_samples,
        },
        "combination_route": dict(combination),
        "combination_route_by_job": {
            job: dict(counter) for job, counter in sorted(combination_by_job.items())
        },
        "combination_acceptance": combination_acceptance,
        "action_signal_resolution": {
            "summary": dict(action_signal_summary),
            "by_job_projection": {
                job: dict(counter)
                for job, counter in sorted(action_signal_projection_by_job.items())
            },
            "groups": action_signal_rows,
        },
    }


FIELD_LAYOUTS = {
    "typed_record_envelope": [
        {"record_offset": "+0", "size": 4, "field": "owner_actor_id"},
        {"record_offset": "+4", "size": 1, "field": "kind"},
        {"record_offset": "+5", "size": 2, "field": "body_length_u16"},
        {"record_offset": "+7", "size": "body_length", "field": "logical_body"},
    ],
    "action_0e_or_12": [
        {
            "record_offset": "+7",
            "marker_offset": "+3",
            "body_offset": "+0",
            "size": 4,
            "field": "skill_id_u32",
        }
    ],
    "entity_kind9_993d80_compact": [
        {"record_offset": "+7", "body_offset": "+0", "size": 4, "field": "object_id"},
        {"record_offset": "+11", "body_offset": "+4", "size": 9, "field": "vector_1"},
        {"record_offset": "+20", "body_offset": "+13", "size": 6, "field": "vector_2"},
        {"record_offset": "+26", "body_offset": "+19", "size": 6, "field": "vector_3"},
        {"record_offset": "+32", "body_offset": "+25", "size": 6, "field": "vector_4"},
        {"record_offset": "+38", "body_offset": "+31", "size": 4, "field": "action_index_u32"},
        {"record_offset": "+42", "body_offset": "+35", "size": 4, "field": "signal_key_u32"},
        {"record_offset": "+46", "body_offset": "+39", "size": 1, "field": "extra_vector_flag"},
        {"record_offset": "+47", "body_offset": "+40", "size": "0_or_6", "field": "extra_vector"},
    ],
    "player_kind9_or_entity_kind56_a90370_equipment0": [
        {"record_offset": "+7", "body_offset": "+0", "size": 4, "field": "opaque_seed_u32"},
        {"record_offset": "+11", "body_offset": "+4", "size": 1, "field": "equipment_branch"},
        {"record_offset": "+12", "body_offset": "+5", "size": 4, "field": "object_id"},
        {"record_offset": "+16", "body_offset": "+9", "size": 4, "field": "config_row"},
        {"record_offset": "+20", "body_offset": "+13", "size": 9, "field": "vector_1"},
        {"record_offset": "+29", "body_offset": "+22", "size": 6, "field": "vector_2"},
        {"record_offset": "+35", "body_offset": "+28", "size": 6, "field": "vector_3"},
        {"record_offset": "+41", "body_offset": "+34", "size": 6, "field": "vector_4"},
        {"record_offset": "+47", "body_offset": "+40", "size": 1, "field": "reference_branch_i8"},
        {"record_offset": "+48", "body_offset": "+41", "size": 4, "field": "reference_id"},
        {"record_offset": "dynamic", "body_offset": "dynamic", "size": 2, "field": "action_index_i16"},
        {"record_offset": "dynamic", "body_offset": "dynamic", "size": 1, "field": "signal_key_i8"},
        {"record_offset": "dynamic", "body_offset": "dynamic", "size": 1, "field": "inline_definition_branch_i8"},
    ],
    "production_kind9_root_equipment0_projection": [
        {
            "marker_offset": "+8",
            "record_offset": "+12",
            "field": "assumed_object_id",
            "a90370_field": "object_id (correct only for A90370)",
            "compact_993d80_field": "inside vector_1",
        },
        {
            "marker_offset": "+12",
            "record_offset": "+16",
            "field": "assumed_config_row",
            "a90370_field": "config_row when equipment_branch=0",
            "compact_993d80_field": "inside vector_1",
        },
        {
            "marker_offset": "+44",
            "record_offset": "+48",
            "field": "assumed_repeated_owner",
            "a90370_field": "reference_id when reference_branch=0",
            "compact_993d80_field": "suffix/optional area",
        },
    ],
}


def _markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    totals = aggregate["totals"]
    lines = [
        "# 玩家对象创建链离线审计（v53）",
        "",
        "## 结论",
        "",
        f"固定抓包：{report['fixed_corpus']['capture_count']} 份；完整 typed 消息："
        f"{totals.get('complete_typed_packets', 0):,}。",
        "",
        "生产函数当前只扫描 `kind 9`，其 `marker+8/+12/+44` 投影"
        "与玩家接收者的 A90370（equipment=0、root self-reference）一致，"
        "并非天然把玩家 kind9 当成 compact；真正的风险是它没有显式"
        "接收者类别，只靠固定长度/固定分支投影筛选，同时完全不扫描"
        "同走 A90370 的 `kind 56`。实体接收者的 kind9 才是 0x993D80 compact。",
        "",
        "本工具没有修改生产代码。只有下表满足跨抓包、至少 20 个独立真值 Hit "
        "且零冲突的路线，才列为候选补丁。",
        "",
        "## 生产解析覆盖",
        "",
        "|记录类型|解析成功|生产精确匹配|生产同 marker 错对象|生产漏掉|",
        "|---|---:|---:|---:|---:|",
    ]
    by_kind = aggregate["production_parser_by_kind"]
    for kind in ("0x09", "0x38"):
        row = by_kind.get(kind, {})
        lines.append(
            f"|{kind}|{totals.get('player_kind_'+str(int(kind,16))+'_parsed',0):,}|"
            f"{row.get('matched_owner_object',0):,}|"
            f"{row.get('same_marker_wrong_owner_or_object',0):,}|"
            f"{row.get('missed',0):,}|"
        )
    lines.extend(
        [
            "",
            "## current skill 跨抓包验证",
            "",
            "|创建类型|current skill 来源|候选 generation|独立真值 Hit|匹配|冲突|精度|跨包|可提出生产补丁|",
            "|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in aggregate["current_skill_validation"]:
        precision = row["verified_precision_percent"]
        lines.append(
            f"|{row['creation_kind_hex']}|{row['current_skill_route']}|"
            f"{row['candidate_generations']:,}|{row['truth_hits']:,}|"
            f"{row['matches']:,}|{row['conflicts']:,}|"
            f"{precision if precision is not None else '-'}%|"
            f"{row['captures']}包/{row['owners']} owner|"
            f"{'是' if row['eligible_for_production_proposal'] else '否'}|"
        )
    lines.extend(
        [
            "",
            "## latest_0e_or_12 冲突归类",
            "",
        ]
    )
    conflict_causes = aggregate.get("latest_conflict_causes", {})
    if conflict_causes:
        for key, value in sorted(
            conflict_causes.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"- `{key}`：{value:,} 个独立真值 Hit")
        lines.extend(["", "交叉维度（不是互斥分类）：", ""])
        for key, value in sorted(
            aggregate.get("latest_conflict_dimensions", {}).items(),
            key=lambda item: (-item[1], item[0]),
        ):
            lines.append(f"- `{key}`：{value:,}")
        lines.extend(["", "主要 current→truth/config 组合：", ""])
        for key, value in sorted(
            aggregate.get("latest_conflict_pairs", {}).items(),
            key=lambda item: (-item[1], item[0]),
        )[:30]:
            lines.append(f"- `{key}`：{value:,}")
    else:
        lines.append("- 没有观察到冲突。")
    combo = aggregate.get("combination_route", {})
    signal = aggregate.get("action_signal_resolution", {})
    lines.extend(
        [
            "",
            "## 组合路线验收",
            "",
            "组合顺序：`unique static template → exact parent/entity chain "
            "→ latest_0e_or_12（仅 template unresolved 且无精确链时）`。",
            "",
            f"- unique static：{combo.get('unique_static_hits', 0):,} Hit；"
            f"独立真值匹配 {combo.get('unique_static_truth_matches', 0):,}、"
            f"冲突 {combo.get('unique_static_truth_conflicts', 0):,}；"
            f"相对当前命名结果的潜在新增 {combo.get('unique_static_potential_new_hits', 0):,} Hit。",
            f"- template unresolved：精确 parent/entity 链承接 "
            f"{combo.get('exact_parent_entity_chain_hits', 0):,} Hit；"
            f"剩余 fallback {combo.get('template_unresolved_fallback_hits', 0):,} Hit。",
            f"- unresolved 上的 latest 影子对照：匹配 "
            f"{combo.get('template_unresolved_fallback_shadow_truth_matches', 0):,}、"
            f"冲突 {combo.get('template_unresolved_fallback_shadow_truth_conflicts', 0):,}。"
            "这里是用精确链真值测试 fallback，不会在组合路线中覆盖精确链。",
            f"- action+signal：{signal.get('summary', {}).get('eligible_groups', 0):,}/"
            f"{signal.get('summary', {}).get('groups', 0):,} 组满足跨包、"
            f"至少20个真值 Hit、单一技能零冲突；可投影新增 "
            f"{signal.get('summary', {}).get('eligible_potential_new_hits', 0):,} Hit。",
            f"- 组合路线生产验收："
            f"{'通过' if aggregate.get('combination_acceptance', {}).get('safe_for_production') else '不通过'}。",
            "",
            "### unique static 冲突明细",
            "",
        ]
    )
    static_dimensions = aggregate.get("unique_static_conflict_dimensions", {})
    if static_dimensions:
        for key, value in sorted(
            static_dimensions.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"- `{key}`：{value:,}")
        lines.extend(["", "主要 static→truth/config 组合：", ""])
        for key, value in sorted(
            aggregate.get("unique_static_conflict_pairs", {}).items(),
            key=lambda item: (-item[1], item[0]),
        )[:40]:
            lines.append(f"- `{key}`：{value:,}")
    else:
        lines.append("- 没有观察到冲突。")
    lines.extend(["", "### template unresolved fallback 冲突明细", ""])
    fallback_dimensions = aggregate.get(
        "unresolved_fallback_conflict_dimensions", {}
    )
    if fallback_dimensions:
        for key, value in sorted(
            fallback_dimensions.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"- `{key}`：{value:,}")
        lines.extend(["", "latest→truth/action/signal 组合：", ""])
        for key, value in sorted(
            aggregate.get("unresolved_fallback_conflict_pairs", {}).items(),
            key=lambda item: (-item[1], item[0]),
        )[:40]:
            lines.append(f"- `{key}`：{value:,}")
    else:
        lines.append("- 没有观察到冲突。")
    conflicting_signal_groups = [
        row
        for row in signal.get("groups", [])
        if int(row.get("truth_hits", 0)) > 0
        and not row.get("zero_truth_conflict", False)
    ]
    lines.extend(["", "### action_index + signal 冲突组", ""])
    if conflicting_signal_groups:
        for row in conflicting_signal_groups:
            lines.append(
                f"- `{row['key']}`：真值 {row['truth_skill_ids']}，"
                f"{row['truth_hits']:,} Hit，{len(row['captures'])} 包/"
                f"{row['owners']} owner。"
            )
    else:
        lines.append("- 没有观察到冲突组。")
    lines.extend(
        [
            "",
            "### 逐职业投影",
            "",
            "|职业|静态模板Hit|静态真值匹配/冲突|静态潜在新增|精确链Hit|unresolved fallback|latest影子匹配/冲突|action+signal潜在新增/仍未解|",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    combo_jobs = aggregate.get("combination_route_by_job", {})
    signal_jobs = signal.get("by_job_projection", {})
    for job in sorted(set(combo_jobs) | set(signal_jobs)):
        row = combo_jobs.get(job, {})
        projected = signal_jobs.get(job, {})
        lines.append(
            f"|{job}|{row.get('unique_static_hits', 0):,}|"
            f"{row.get('unique_static_truth_matches', 0):,}/"
            f"{row.get('unique_static_truth_conflicts', 0):,}|"
            f"{row.get('unique_static_potential_new_hits', 0):,}|"
            f"{row.get('exact_parent_entity_chain_hits', 0):,}|"
            f"{row.get('template_unresolved_fallback_hits', 0):,}|"
            f"{row.get('template_unresolved_fallback_shadow_truth_matches', 0):,}/"
            f"{row.get('template_unresolved_fallback_shadow_truth_conflicts', 0):,}|"
            f"{projected.get('action_signal_projected_new_hits', 0):,}/"
            f"{projected.get('action_signal_still_unresolved_hits', 0):,}|"
        )
    lines.extend(["", "## direct37 未连接到玩家 A90370 generation", ""])
    unlinked = aggregate.get("unlinked_direct37", {})
    for key, value in sorted(
        unlinked.get("reasons", {}).items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"- `{key}`：{value:,} Hit")
    if not unlinked.get("reasons"):
        lines.append("- 0 Hit。")
    lines.extend(
        [
            "",
            "## 明确字段布局",
            "",
            "- 完整记录：`owner:u32 @ +0`、`kind:u8 @ +4`、"
            "`body_length:u16 @ +5`、逻辑正文从 `+7` 开始。",
            "- 实体接收者 kind9 / 0x993D80：`object @ body+0`；四段向量后，"
            "`action u32 @ body+31`、`signal u32 @ body+35`、"
            "`extra-vector flag @ body+39`。这里没有固定 `config_row`。",
            "- 玩家接收者 kind9，及相应实体 kind56 / A90370（equipment=0）："
            "`opaque seed @ body+0`、"
            "`equipment @ body+4`、`object @ body+5`、`config @ body+9`；"
            "四段向量后为 `reference branch @ body+40`、"
            "`reference @ body+41`，随后分支读取父对象引用，再读取"
            "有符号 action i16、signal i8、inline i8。",
            "- `0x0E` 与 `0x12` 都在报告中读取 `body+0` 作为候选技能；"
            "静态客户端已直接证明的是 0x0E 分支把该 u32 传给玩家"
            "虚表 `+0x2E8` 并更新 MASkillUser。0x12 在该分发表中走"
            "通用路径，因此必须看实包零冲突结果，不能与 0x0E 混称。",
            "",
            "## generation 规则",
            "",
            "每个完整 kind9/kind56 创建记录都开启新的 "
            "`(capture, owner, object_id, generation)`；后续 direct37 "
            "只连接到同 owner、同 object_id 的最近前置 generation。"
            "没有使用时间窗口、技能名、伤害值画像或最近动作猜测。",
            f"固定语料共提交 {totals.get('generations', 0):,} 个 generation；"
            "每份抓包在 JSON 中保存独立 SHA-256 commitment 与有界反例，"
            "避免用超大 JSON 代替完整扫描。",
            "",
            "JSON 保存每包 generation commitment、完整汇总、字段分布及有界"
            "正反例；每条记录都参与 commitment，但不重复输出全部原始明细。",
            "",
            "## 补丁门槛",
            "",
        ]
    )
    proposals = aggregate["production_proposals"]
    if proposals:
        for row in proposals:
            lines.append(
                f"- 候选：{row['creation_kind_hex']} / {row['current_skill_route']}，"
                f"{row['matches']:,} 个独立真值 Hit、{row['captures']} 份抓包、"
                f"0 冲突。仍需先补单元测试和物理伤害不变量，当前未改生产。"
            )
    else:
        lines.append(
            "- 当前没有路线同时满足跨抓包、足量独立真值和零冲突；"
            "因此不提出生产改动，也不把 current skill 直接写入伤害归因。"
        )
    if aggregate["parse_errors"]:
        lines.extend(["", "## 未解析形状", ""])
        for key, value in sorted(
            aggregate["parse_errors"].items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"- `{key}`：{value:,}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--capture", action="append", default=[])
    parser.add_argument("--merge-json", action="append", default=[])
    parser.add_argument(
        "--replace-duplicate-captures",
        action="store_true",
        help=(
            "when merging, allow a later hash-identical capture row to replace "
            "an earlier row; intended for targeted audit-detail refreshes"
        ),
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        raise ValueError("workers must be 1 or 2")
    selection = _capture_selection(args.selection)
    requested = set(str(value) for value in args.capture)
    captures = _find_captures(args.capture_root, selection, requested)
    if not captures:
        raise ValueError("no fixed hash-matched captures selected")
    before = _production_signature()
    native = _native_evidence()
    results: list[dict[str, Any]] = []
    if args.merge_json:
        if requested:
            raise ValueError("--merge-json cannot be combined with --capture")
        captures = _find_captures(args.capture_root, selection, set())
        expected = {path.name: _hash_file(path) for path in captures}
        merged: dict[str, dict[str, Any]] = {}
        for report_path_text in args.merge_json:
            report_path = Path(report_path_text)
            batch = json.loads(report_path.read_text(encoding="utf-8"))
            if batch.get("schema") != SCHEMA or batch.get("production_changed"):
                raise ValueError(f"invalid batch report: {report_path}")
            for row in batch.get("captures", ()):
                name = str(row.get("capture") or "")
                digest = str(row.get("capture_sha256") or "")
                if name not in expected or expected[name] != digest:
                    raise ValueError(f"batch capture identity mismatch: {name}")
                if name in merged:
                    if not args.replace_duplicate_captures:
                        raise ValueError(f"duplicate batch capture: {name}")
                merged[name] = row
        missing = sorted(set(expected) - set(merged))
        extra = sorted(set(merged) - set(expected))
        if missing or extra:
            raise ValueError(f"incomplete fixed corpus; missing={missing}, extra={extra}")
        results = [merged[name] for name in sorted(merged)]
    elif args.workers == 1 or len(captures) == 1:
        results = [_audit_capture(path) for path in captures]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_audit_capture, path): path for path in captures}
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda row: row["capture"])
    after = _production_signature()
    if before != after:
        raise ValueError("production files changed during read-only audit")
    aggregate = _aggregate(results)
    report = {
        "schema": SCHEMA,
        "method": "offline replay + static client bytes; no production mutation",
        "fixed_corpus": {
            "selection": str(args.selection),
            "selection_sha256": _hash_file(args.selection),
            "capture_root": str(args.capture_root),
            "capture_count": len(captures),
            "captures": [
                {"name": path.name, "sha256": _hash_file(path)} for path in captures
            ],
        },
        "native_evidence": native,
        "field_layouts": FIELD_LAYOUTS,
        "production_signature_before": before,
        "production_signature_after": after,
        "production_changed": False,
        "aggregate": aggregate,
        "captures": results,
        "limitations": [
            "No client/native code was executed and no live memory was read.",
            "A current-skill candidate is validation-only; a zero-conflict row is not yet a production change.",
            "Object generations are record generations. Explicit destruction/reset packets are outside this focused audit.",
            "Only complete length-walked server_to_client typed messages are admitted.",
            "packet_skill_id is excluded from independent truth to avoid circular object+0x1BC0 validation.",
        ],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    summary = {
        "captures": len(captures),
        "generations": aggregate["totals"].get("generations", 0),
        "player_kind9_parsed": aggregate["totals"].get("player_kind_9_parsed", 0),
        "player_kind56_parsed": aggregate["totals"].get("player_kind_56_parsed", 0),
        "production_proposals": len(aggregate["production_proposals"]),
        "json": str(args.output_json),
        "markdown": str(args.output_md),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
