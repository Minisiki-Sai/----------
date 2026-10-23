"""Audit stable native joint keys for the professions below 95% coverage.

This is an offline/read-only evidence tool.  It never changes the production
decoder and never uses a time-neighbourhood rule.  Complete typed damage rows
are joined by their physical wire identity.  Candidate keys combine the
client meter's native result fields with exact object/entity generation state:

* result primary90 / fallbackA4 (raw values are retained);
* player object configuration or current Projectile object template;
* current entity template and explicit owner-generation proof;
* the exact entity self-attack slot when the typed result carries one.

Generation numbers are evidence attached to examples, not cross-capture key
values.  A rule uses only the boolean guard that the referenced generation was
current at the hit boundary.  This prevents object-ID reuse from becoming a
false mapping while still allowing the same structural key to be compared in
different captures.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any, Iterable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import (  # noqa: E402
    PhysicalHit,
    read_self_attack_slot,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)
from tools.audit_native_meter_skill_key_v50 import (  # noqa: E402
    PACKET_DERIVED_METHODS,
    _native_result_fields,
)
from tools.audit_player_object_creation_v53 import (  # noqa: E402
    A90370Stream,
    OBJECT_ID_MAX,
    PLAYER_ID_MIN,
    _parse_a90370,
)


SCHEMA = "under95-native-joint-keys-v55"
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_RUNTIME = ROOT / "outputs/compact_entity_selectors_20260908/RUNTIME_RESULT.md"
DEFAULT_OUTPUT = ROOT / "outputs/under95_native_joint_keys_v55_20260909"

TARGET_JOBS = (
    "魔羽",
    "圣骑士",
    "十字军",
    "圣徒",
    "雷神",
    "重炮手",
    "机械大师",
    "炼金圣士",
    "药剂师",
    "黑暗萨满",
    "噬魂者",
    "影",
)

EMPTY_U32 = frozenset({0, 0xFFFFFFFF})
PRODUCTION_ROOTS = (
    ROOT / "dragon_nest_builder/live_dps",
    ROOT / "dragon_nest_builder/data_loader.py",
)

# Each entry contains at least one native field and/or one exact object/entity
# identity.  None contains timestamps, action ages or damage magnitudes.
KEY_VARIANTS: dict[str, tuple[str, ...]] = {
    "native+player_object_template": ("native", "player_object_template"),
    "primary90+player_object_template": ("primary90", "player_object_template"),
    "fallbackA4+player_object_template": ("fallbackA4", "player_object_template"),
    "native+origin_template": ("native", "origin_template"),
    "primary90+origin_template": ("primary90", "origin_template"),
    "fallbackA4+origin_template": ("fallbackA4", "origin_template"),
    "native+entity_template+owner_generation": (
        "native", "entity_template", "owner_generation",
    ),
    "primary90+entity_template+owner_generation": (
        "primary90", "entity_template", "owner_generation",
    ),
    "fallbackA4+entity_template+owner_generation": (
        "fallbackA4", "entity_template", "owner_generation",
    ),
    "native+entity_template+self_skill+owner_generation": (
        "native", "entity_template", "self_skill", "owner_generation",
    ),
    "primary90+entity_template+self_skill+owner_generation": (
        "primary90", "entity_template", "self_skill", "owner_generation",
    ),
    "fallbackA4+entity_template+self_skill+owner_generation": (
        "fallbackA4", "entity_template", "self_skill", "owner_generation",
    ),
    "entity_template+self_skill+owner_generation": (
        "entity_template", "self_skill", "owner_generation",
    ),
    "origin_template+object_generation": (
        "origin_template", "object_generation",
    ),
    "player_object_template+object_generation": (
        "player_object_template", "object_generation",
    ),
    "full_native_object_entity": (
        "native", "primary90", "fallbackA4", "player_object_template",
        "origin_template", "entity_template", "self_skill",
        "self_context", "owner_generation", "object_generation",
    ),
}


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_signature() -> dict[str, Any]:
    files: list[tuple[str, str]] = []
    for root in PRODUCTION_ROOTS:
        paths = root.rglob("*.py") if root.is_dir() else (root,)
        for path in paths:
            files.append((path.relative_to(ROOT).as_posix(), _hash_file(path)))
    files.sort()
    payload = "\n".join(f"{name}\0{digest}" for name, digest in files).encode()
    return {
        "file_count": len(files),
        "sha256": sha256(payload).hexdigest(),
    }


def _canonical(value: int) -> int:
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(int(value), int(value)))


def _positive(value: object) -> int:
    try:
        result = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    return result if result > 0 else 0


def _at_or_before(
    left_serial: int,
    left_offset: int,
    right_serial: int,
    right_offset: int,
) -> bool:
    """Compare physical record boundaries, including rows in one datagram.

    A player-object registration later in the same typed message cannot own a
    damage row that precedes it.  The production ledger has always enforced
    this boundary; the original v55 audit accidentally retained only the last
    object generation found after scanning the complete message and could
    therefore manufacture apparent cross-skill corrections.
    """
    return left_serial < right_serial or (
        left_serial == right_serial and left_offset <= right_offset
    )


def _actor(value: object) -> int:
    text = str(value or "").split("@", 1)[0].strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except ValueError:
        return 0


def _runtime_jobs(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "职业" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 6 or cells[0] not in TARGET_JOBS:
            continue
        confirmed, named = cells[4].split("/", 1)
        rows[cells[0]] = {
            "coverage_percent": float(cells[3].rstrip("%")),
            "confirmed_hits": int(confirmed),
            "named_hits": int(named),
            "hits_needed_for_95": int(cells[5]),
        }
    missing = set(TARGET_JOBS) - set(rows)
    if missing:
        raise ValueError(f"runtime report missing target jobs: {sorted(missing)}")
    return rows


def _selection(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    results_dir = ROOT / report["sources"]["after_results_dir"]
    captures: list[dict[str, str]] = []
    for selected in report["sources"]["selected_results"]:
        frozen_path = results_dir / selected["result"]
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        captures.append(
            {
                "name": str(frozen["capture"]),
                "path": str(frozen["capture_path"]),
                "sha256": str(selected["capture_sha256"]),
                "frozen_result": str(frozen_path),
                "frozen_result_sha256": _hash_file(frozen_path),
            }
        )
    if len(captures) != 35 or len({row["sha256"] for row in captures}) != 35:
        raise ValueError(f"fixed corpus changed: {len(captures)} captures")
    for row in captures:
        capture = Path(row["path"])
        if not capture.is_file() or _hash_file(capture) != row["sha256"]:
            raise ValueError(f"capture missing or hash changed: {capture}")
    return captures, report


def _event_map(events: Iterable[object]) -> dict[tuple[Any, ...], list[CombatEvent]]:
    result: dict[tuple[Any, ...], list[CombatEvent]] = defaultdict(list)
    for event in events:
        if (
            not isinstance(event, CombatEvent)
            or event.healing
            or event.amount <= 0
            or event.wire_record_offset <= 0
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


def _physical_event(physical: object, events: dict[tuple[Any, ...], list[CombatEvent]]):
    key = (
        str(physical.family),
        int(physical.record_offset),
        int(physical.source_actor_id),
        int(physical.target_actor_id),
        int(physical.damage),
        bool(physical.critical),
    )
    rows = events.get(key, ())
    return rows[0] if len(rows) == 1 else None


def _origin_state(decoder: object, physical: object, timestamp: float, serial: int):
    object_id = int(physical.damage_object_id or 0)
    source = int(physical.source_actor_id)
    if physical.family != "direct37" or not object_id or not source:
        return None, False
    ledger = decoder.damage_object_origins
    origin, snapshot = ledger._origin_at(
        (source, object_id), timestamp, serial, int(physical.record_offset)
    )
    if origin is None or timestamp > origin.expires_at:
        return None, False
    invalidation = (
        snapshot.invalidation
        if snapshot is not None
        else ledger._unknown_registration_invalidations.get((source, object_id))
    )
    if invalidation is not None and ledger._boundary_reached(
        invalidation, timestamp, serial, int(physical.record_offset)
    ):
        return origin, False
    revoked = ledger._revoked_origin_generations.get(
        (source, object_id, int(origin.generation))
    )
    if revoked is not None and ledger._boundary_reached(
        revoked, timestamp, serial, int(physical.record_offset)
    ):
        return origin, False
    return origin, True


def _owner_state(decoder: object, event: CombatEvent, source: int, timestamp: float):
    if source >= PLAYER_ID_MIN:
        # A player is the physical owner.  Do not present that fact as an
        # entity 34/2c owner-generation proof.
        return _actor(event.source_id) or source, 0, "player", False
    if not 0 < source < PLAYER_ID_MIN:
        return 0, 0, "invalid", False
    generation = 0
    proof = str(event.entity_owner_evidence_id or "")
    if proof.startswith("34:"):
        try:
            generation = int(proof.split(":", 2)[1])
        except (IndexError, ValueError):
            generation = 0
    birth = decoder.entity_owner_evidence.births.get(source)
    template = int(event.entity_template_id or (birth[0] if birth else 0))
    binding = (
        decoder.entity_owner_evidence.resolve(source, template, timestamp)
        if template
        else None
    )
    if binding is not None:
        current = generation in (0, int(binding.generation))
        return int(binding.record.owner_id), int(binding.generation), "34/2c", current
    return _actor(event.source_id), generation, "event_only" if generation else "missing", False


def _feature_values(row: dict[str, Any]) -> dict[str, str]:
    primary = int(row["primary90"])
    fallback = int(row["fallbackA4"])
    primary_value = str(primary) if row["primary90_read"] and primary not in EMPTY_U32 else ""
    fallback_value = str(fallback) if fallback not in EMPTY_U32 else ""
    native = f"primary90:{primary_value}" if primary_value else (
        f"fallbackA4:{fallback_value}" if fallback_value else ""
    )
    return {
        "native": native,
        "primary90": primary_value,
        "fallbackA4": fallback_value,
        "player_object_template": str(row["player_object_template"] or ""),
        "origin_template": str(row["origin_template"] or ""),
        "entity_template": str(row["entity_template"] or ""),
        "self_skill": str(row["self_skill"] or ""),
        "self_context": str(row["self_context"] or ""),
        "owner_generation": "current" if row["owner_generation_current"] else "",
        "object_generation": "current" if row["object_generation_current"] else "",
    }


def _joint_keys(row: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, str]]]:
    values = _feature_values(row)
    for variant, names in KEY_VARIANTS.items():
        selected = {name: values[name] for name in names}
        if any(not value for value in selected.values()):
            continue
        # A validity guard alone is not identity.  Require a raw/template/self key.
        if not any(name not in {"owner_generation", "object_generation"} for name in names):
            continue
        payload = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        yield variant, payload, selected


def _audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    if _hash_file(path) != entry["sha256"]:
        raise ValueError(f"capture hash changed: {path}")
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    active_player_objects: dict[tuple[int, int], list[dict[str, Any]]] = {}
    player_generation: Counter[tuple[int, int]] = Counter()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    def feed_events(packet):
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        inventory = inspect_udp_damage_records(packet.payload, packet.inferred_direction())
        if not inventory.complete:
            counts[f"unframed:{inventory.rejection_reason}"] += 1
            return events
        event_rows = _event_map(events)
        damage_by_offset = {
            int(row.record_offset): row
            for row in inventory.damage_records
            if row.admissible_fields
        }
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        for typed in sorted(inventory.typed_records, key=lambda value: value.record_offset):
            record = bytes(typed.record_bytes)
            owner = int.from_bytes(record[:4], "little") if len(record) >= 4 else 0
            kind = int(typed.record_type)
            marker = int(typed.record_offset)
            if kind in (9, 56):
                try:
                    parsed = _parse_a90370(record)
                except ValueError:
                    parsed = None
                if isinstance(parsed, A90370Stream):
                    object_id = int(parsed.object_id)
                    root_self = int(parsed.reference_branch_i8) == 0 and int(parsed.reference_id) == owner
                    if owner >= PLAYER_ID_MIN and root_self and 0 < object_id <= OBJECT_ID_MAX:
                        pair = (owner, object_id)
                        player_generation[pair] += 1
                        active_player_objects.setdefault(pair, []).append({
                            "generation": int(player_generation[pair]),
                            "template": int(parsed.config_row or 0),
                            "creation_kind": kind,
                            "creation_serial": serial,
                            "creation_offset": marker,
                            "creation_wire_sha256": str(typed.wire_record_sha256),
                        })
                continue
            if kind != 8:
                continue
            physical = damage_by_offset.get(marker)
            if physical is None:
                continue
            event = _physical_event(physical, event_rows)
            if event is None or str(event.job) not in TARGET_JOBS:
                continue
            if not bool(event.has_real_hit_evidence) or event.healing or event.amount <= 0:
                continue
            assigned = _canonical(_positive(event.skill_id))
            if assigned <= 0:
                continue
            native = _native_result_fields(physical)
            pair = (int(physical.source_actor_id), int(physical.damage_object_id or 0))
            player_object = next(
                (
                    candidate
                    for candidate in reversed(active_player_objects.get(pair, ()))
                    if _at_or_before(
                        int(candidate["creation_serial"]),
                        int(candidate["creation_offset"]),
                        serial,
                        marker,
                    )
                ),
                None,
            )
            origin, origin_current = _origin_state(
                decoder, physical, float(packet.timestamp_seconds), serial
            )
            owner_actor, owner_generation, owner_proof, owner_current = _owner_state(
                decoder,
                event,
                int(physical.source_actor_id),
                float(packet.timestamp_seconds),
            )
            entity_template = int(event.entity_template_id or 0)
            if not entity_template and int(physical.source_actor_id) < PLAYER_ID_MIN:
                birth = decoder.entity_owner_evidence.births.get(int(physical.source_actor_id))
                entity_template = int(birth[0]) if birth else 0
            self_slot = None
            if physical.family == "direct37" and 0 < int(physical.source_actor_id) < PLAYER_ID_MIN:
                hit = PhysicalHit(
                    capture=path.name,
                    physical_hit_id=(
                        f"{entry['sha256']}:{serial}:{marker}:"
                        f"{physical.wire_record_sha256}"
                    ),
                    order=(serial, 0, marker),
                    entity_id=int(physical.source_actor_id),
                    generation=int(owner_generation),
                    template_id=entity_template,
                    target_id=int(physical.target_actor_id),
                    damage=int(physical.damage),
                    object_id=int(physical.damage_object_id or 0),
                    result_group=int(physical.damage_result_group_id or 0),
                    wire=bytes(packet.payload),
                    direction=str(packet.inferred_direction()),
                )
                self_slot = read_self_attack_slot(hit)
            method = str(event.attribution_method or "")
            deterministic = bool(is_deterministic_attribution(method))
            independent = deterministic and method not in PACKET_DERIVED_METHODS
            actor_token = f"{entry['sha256']}:{owner_actor:08X}"
            row = {
                "physical_id": (
                    f"{entry['sha256']}:{serial}:{marker}:"
                    f"{physical.wire_record_sha256}"
                ),
                "capture": path.name,
                "capture_sha256": entry["sha256"],
                "packet_serial": serial,
                "record_offset": marker,
                "timestamp_ms": timestamp_ms,
                "family": str(physical.family),
                "source_class": "player" if int(physical.source_actor_id) >= PLAYER_ID_MIN else "entity",
                "wire_source_actor_id": int(physical.source_actor_id),
                "owner_actor_id": int(owner_actor),
                "actor_token": actor_token,
                "job": str(event.job),
                "skill_id": assigned,
                "skill_name": str(event.skill_name),
                "method": method,
                "deterministic": deterministic,
                "independent_deterministic": independent,
                "primary90": int(native["primary_raw"]),
                "primary90_read": bool(native["primary_read"]),
                "fallbackA4": int(native["fallback_raw"]),
                "damage_object_id": int(physical.damage_object_id or 0),
                "player_object_template": int(player_object["template"] if player_object else 0),
                "player_object_generation": int(player_object["generation"] if player_object else 0),
                "player_object_creation_kind": int(player_object["creation_kind"] if player_object else 0),
                "origin_template": int(origin.projectile_template_id if origin else 0),
                "origin_generation": int(origin.generation if origin else 0),
                "origin_skill_id_diagnostic": int(origin.canonical_skill_id if origin else 0),
                "entity_template": entity_template,
                "owner_generation": int(owner_generation),
                "owner_generation_proof": owner_proof,
                "owner_generation_current": bool(owner_current),
                "object_generation_current": bool(origin_current or player_object is not None),
                "self_skill": int(
                    self_slot.monster_skill_id
                    if self_slot and self_slot.monster_skill_id not in EMPTY_U32
                    else 0
                ),
                "self_context": int(
                    self_slot.context_skill_id
                    if self_slot and self_slot.context_skill_id not in EMPTY_U32
                    else 0
                ),
            }
            rows.append(row)
            counts["target_named_real_hits"] += 1
            counts["deterministic_hits"] += int(deterministic)
            counts["independent_deterministic_hits"] += int(independent)
            counts["gap_hits"] += int(not deterministic)
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": path.name,
        "capture_sha256": entry["sha256"],
        "counts": dict(counts),
        "rows": rows,
    }


def _new_bucket(
    job: str, variant: str, key_json: str, values: dict[str, str]
) -> dict[str, Any]:
    return {
        "job": job,
        "variant": variant,
        "key": json.loads(key_json),
        "truth_skills": Counter(),
        "truth_methods": Counter(),
        "truth_captures": set(),
        "truth_actors": set(),
        "gap_skills": Counter(),
        "gap_methods": Counter(),
        "gap_captures": set(),
        "gap_actors": set(),
        "gap_physical_ids": set(),
        "examples": [],
    }


def _serialise_rule(bucket: dict[str, Any]) -> dict[str, Any]:
    truth_skills = {int(key): int(value) for key, value in bucket["truth_skills"].items()}
    gap_skills = {int(key): int(value) for key, value in bucket["gap_skills"].items()}
    truth_hits = sum(truth_skills.values())
    canonical = next(iter(truth_skills), 0) if len(truth_skills) == 1 else 0
    truth_conflicts = truth_hits - truth_skills.get(canonical, 0) if canonical else truth_hits
    gap_conflicts = sum(value for key, value in gap_skills.items() if key != canonical)
    enough_cross_capture = len(bucket["truth_captures"]) >= 2
    enough_actors = len(bucket["truth_actors"]) >= 2
    accepted = bool(
        canonical
        and truth_hits >= 8
        and truth_conflicts == 0
        and gap_conflicts == 0
        and enough_cross_capture
        and enough_actors
    )
    reasons = []
    if not canonical:
        reasons.append("deterministic truth is not unique")
    if truth_hits < 8:
        reasons.append("deterministic truth Hit<8")
    if truth_conflicts:
        reasons.append("deterministic truth conflict")
    if gap_conflicts:
        reasons.append("existing named-gap conflict")
    if not enough_cross_capture:
        reasons.append("capture support<2")
    if not enough_actors:
        reasons.append("actor support<2")
    return {
        "job": bucket["job"],
        "variant": bucket["variant"],
        "key": bucket["key"],
        "canonical_skill_id": canonical,
        "truth_hits": truth_hits,
        "truth_skills": {str(key): value for key, value in sorted(truth_skills.items())},
        "truth_conflicts": truth_conflicts,
        "truth_methods": dict(sorted(bucket["truth_methods"].items())),
        "truth_capture_count": len(bucket["truth_captures"]),
        "truth_actor_count": len(bucket["truth_actors"]),
        "gap_hits": sum(gap_skills.values()),
        "gap_skills": {str(key): value for key, value in sorted(gap_skills.items())},
        "gap_conflicts": gap_conflicts,
        "gap_capture_count": len(bucket["gap_captures"]),
        "gap_actor_count": len(bucket["gap_actors"]),
        "gap_physical_ids": sorted(bucket["gap_physical_ids"]),
        "decision": "accepted" if accepted else "rejected",
        "reasons": reasons,
        "examples": bucket["examples"],
    }


def _aggregate(captures: list[dict[str, Any]], runtime: dict[str, dict[str, Any]]):
    all_rows = [row for capture in captures for row in capture["rows"]]
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    feature_counts: dict[tuple[str, int, str], Counter[str]] = defaultdict(Counter)
    gap_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skill_names: dict[tuple[str, int], str] = {}

    for row in all_rows:
        job = row["job"]
        skill = int(row["skill_id"])
        skill_names[(job, skill)] = row["skill_name"]
        scope = feature_counts[(job, skill, row["skill_name"])]
        scope["named_real_hits"] += 1
        scope["gap_hits"] += int(not row["deterministic"])
        scope["deterministic_hits"] += int(row["deterministic"])
        scope["primary90"] += int(row["primary90_read"] and row["primary90"] not in EMPTY_U32)
        scope["fallbackA4"] += int(row["fallbackA4"] not in EMPTY_U32)
        scope["player_object_template"] += int(bool(row["player_object_template"]))
        scope["origin_template"] += int(bool(row["origin_template"]))
        scope["entity_template"] += int(bool(row["entity_template"]))
        scope["owner_generation"] += int(bool(row["owner_generation_current"]))
        scope["self_skill"] += int(bool(row["self_skill"]))
        if not row["deterministic"]:
            scope["gap_primary90"] += int(
                row["primary90_read"] and row["primary90"] not in EMPTY_U32
            )
            scope["gap_fallbackA4"] += int(row["fallbackA4"] not in EMPTY_U32)
            scope["gap_player_object_template"] += int(bool(row["player_object_template"]))
            scope["gap_origin_template"] += int(bool(row["origin_template"]))
            scope["gap_entity_template"] += int(bool(row["entity_template"]))
            scope["gap_owner_generation"] += int(bool(row["owner_generation_current"]))
            scope["gap_self_skill"] += int(bool(row["self_skill"]))
            gap_rows[job].append(row)
        for variant, key_json, values in _joint_keys(row):
            bucket_key = (job, variant, key_json)
            bucket = buckets.setdefault(
                bucket_key, _new_bucket(job, variant, key_json, values)
            )
            if row["independent_deterministic"]:
                bucket["truth_skills"][skill] += 1
                bucket["truth_methods"][row["method"]] += 1
                bucket["truth_captures"].add(row["capture_sha256"])
                bucket["truth_actors"].add(row["actor_token"])
            if not row["deterministic"]:
                bucket["gap_skills"][skill] += 1
                bucket["gap_methods"][row["method"]] += 1
                bucket["gap_captures"].add(row["capture_sha256"])
                bucket["gap_actors"].add(row["actor_token"])
                bucket["gap_physical_ids"].add(row["physical_id"])
            if len(bucket["examples"]) < 4 and (
                row["independent_deterministic"] or not row["deterministic"]
            ):
                bucket["examples"].append(
                    {
                        key: row[key]
                        for key in (
                            "capture", "packet_serial", "record_offset",
                            "wire_source_actor_id", "owner_actor_id", "job",
                            "skill_id", "skill_name", "method", "primary90",
                            "primary90_read", "fallbackA4", "damage_object_id",
                            "player_object_template", "player_object_generation",
                            "origin_template", "origin_generation", "entity_template",
                            "owner_generation", "owner_generation_proof",
                            "self_skill", "self_context",
                        )
                    }
                )

    rules = [_serialise_rule(bucket) for bucket in buckets.values()]
    accepted = [row for row in rules if row["decision"] == "accepted"]
    accepted.sort(
        key=lambda row: (
            -int(row["gap_hits"]),
            -int(row["truth_capture_count"]),
            -int(row["truth_hits"]),
            -len(row["key"]),
            row["variant"],
            json.dumps(row["key"], sort_keys=True),
        )
    )

    # Rebuild the lookup with job scope: identical raw keys in different jobs
    # must never be silently merged.
    accepted_scoped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (job, variant, key_json), bucket in buckets.items():
        serialised = _serialise_rule(bucket)
        if serialised["decision"] == "accepted":
            accepted_scoped[(job, variant, key_json)] = serialised

    projected_ids: dict[str, set[str]] = defaultdict(set)
    projected_by_skill: dict[tuple[str, int], set[str]] = defaultdict(set)
    matching_rule_counts: Counter[tuple[str, int]] = Counter()
    for job, rows in gap_rows.items():
        for row in rows:
            for variant, key_json, _values in _joint_keys(row):
                rule = accepted_scoped.get((job, variant, key_json))
                if rule is None or int(rule["canonical_skill_id"]) != int(row["skill_id"]):
                    continue
                projected_ids[job].add(row["physical_id"])
                projected_by_skill[(job, int(row["skill_id"]))].add(row["physical_id"])
                matching_rule_counts[(job, int(row["skill_id"]))] += 1

    # Collapse equivalent accepted variants into a compact non-overlapping
    # proposal set. Prefer the more specific key and stronger corpus support.
    preferred = sorted(
        (row for row in accepted if row["gap_hits"] > 0),
        key=lambda row: (
            -len(row["key"]),
            -int(row["truth_capture_count"]),
            -int(row["truth_hits"]),
            row["variant"],
        ),
    )
    recommended: list[dict[str, Any]] = []
    recommended_seen: dict[str, set[str]] = defaultdict(set)
    for row in preferred:
        job = str(row["job"])
        physical = set(row["gap_physical_ids"])
        new_ids = physical - recommended_seen[job]
        if not new_ids:
            continue
        selected = dict(row)
        selected["new_unique_gap_hits"] = len(new_ids)
        selected["new_gap_physical_ids"] = sorted(new_ids)
        recommended.append(selected)
        recommended_seen[job].update(new_ids)
    recommended.sort(
        key=lambda row: (
            TARGET_JOBS.index(row["job"]),
            -int(row["new_unique_gap_hits"]),
            row["variant"],
        )
    )

    skill_rows: list[dict[str, Any]] = []
    for (job, skill, name), counts in sorted(feature_counts.items()):
        if counts["gap_hits"] <= 0:
            continue
        projected = len(projected_by_skill[(job, skill)])
        skill_rows.append(
            {
                "job": job,
                "skill_id": skill,
                "skill_name": name,
                **dict(counts),
                "accepted_projected_gap_hits": projected,
                "unresolved_gap_hits": max(0, int(counts["gap_hits"]) - projected),
                "matching_rule_applications": int(matching_rule_counts[(job, skill)]),
            }
        )

    jobs: list[dict[str, Any]] = []
    for job in TARGET_JOBS:
        base = runtime[job]
        audited_gap = sum(1 for row in gap_rows[job])
        projected = len(projected_ids[job])
        projected_confirmed = min(base["named_hits"], base["confirmed_hits"] + projected)
        projected_percent = 100.0 * projected_confirmed / max(1, base["named_hits"])
        jobs.append(
            {
                "job": job,
                **base,
                "audited_named_gap_hits": audited_gap,
                "accepted_projected_gap_hits": projected,
                "projected_coverage_percent_upper_bound": projected_percent,
                "would_reach_95_if_implemented": projected_percent >= 95.0,
            }
        )

    # Keep rejected output bounded but reproducible: include every conflicting
    # or cross-capture near miss with at least one gap hit.
    rejected = [
        row for row in rules
        if row["decision"] == "rejected"
        and row["gap_hits"] > 0
        and (row["truth_hits"] > 0 or row["truth_capture_count"] >= 2)
    ]
    rejected.sort(key=lambda row: (-row["gap_hits"], -row["truth_hits"], row["variant"]))

    return {
        "row_counts": {
            "target_named_real_hits": len(all_rows),
            "independent_deterministic_truth_hits": sum(
                int(row["independent_deterministic"]) for row in all_rows
            ),
            "named_gap_hits": sum(int(not row["deterministic"]) for row in all_rows),
        },
        "jobs": jobs,
        "gap_skills": skill_rows,
        "accepted_rules": accepted,
        "recommended_nonoverlapping_rules": recommended,
        "rejected_near_misses": rejected,
    }


def _percent(count: int, total: int) -> str:
    return f"{100.0 * count / total:.2f}%" if total else "—"


def _render_md(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# 低于 95% 职业原生联合键专项审计 v55",
        "",
        "- 固定语料：35 份抓包；逐份校验 SHA-256。",
        "- 本轮只新增审计工具与报告，生产代码哈希未改变。",
        "- 不使用 2.5 秒或任何时间窗口；不按伤害大小、Hit 间隔或旧技能轴邻近生成候选。",
        "- generation 的具体数字仅保存为证据，跨包规则只要求该代次在该 Hit 边界仍为 current。",
        "- “零冲突”指相对于固定语料中独立确定归因的技能标签零冲突，并同时要求现有缺口标签不反向冲突；它是候选生产规则，不等于已修改生产解析器。",
        "",
        "## 总览",
        "",
        f"- 目标职业命名真实 Hit：{aggregate['row_counts']['target_named_real_hits']:,}",
        f"- 独立确定真值 Hit：{aggregate['row_counts']['independent_deterministic_truth_hits']:,}",
        f"- 当前命名缺口 Hit：{aggregate['row_counts']['named_gap_hits']:,}",
        f"- 达标联合键：{len(aggregate['accepted_rules']):,}；折叠后的非重复推荐规则：{len(aggregate['recommended_nonoverlapping_rules']):,}",
        "",
        "|职业|当前覆盖|距95%|审计缺口Hit|联合键可覆盖Hit|仅应用本专项的上限投影|达到95%|",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in aggregate["jobs"]:
        lines.append(
            f"|{row['job']}|{row['coverage_percent']:.2f}%|{row['hits_needed_for_95']:,}|"
            f"{row['audited_named_gap_hits']:,}|{row['accepted_projected_gap_hits']:,}|"
            f"{row['projected_coverage_percent_upper_bound']:.2f}%|"
            f"{'是' if row['would_reach_95_if_implemented'] else '否'}|"
        )

    lines.extend(
        [
            "",
            "## 每个缺口技能的字段存在情况",
            "",
            "|职业|技能|缺口Hit|P90|A4|玩家对象模板|Projectile模板|实体模板|owner代次|self-skill|联合键覆盖|未解决|",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(
        aggregate["gap_skills"], key=lambda value: (TARGET_JOBS.index(value["job"]), -value["gap_hits"])
    ):
        total = int(row["gap_hits"])
        lines.append(
            f"|{row['job']}|{row['skill_id']} {row['skill_name']}|{row['gap_hits']:,}|"
            f"{_percent(row['gap_primary90'], total)}|{_percent(row['gap_fallbackA4'], total)}|"
            f"{_percent(row['gap_player_object_template'], total)}|{_percent(row['gap_origin_template'], total)}|"
            f"{_percent(row['gap_entity_template'], total)}|{_percent(row['gap_owner_generation'], total)}|"
            f"{_percent(row['gap_self_skill'], total)}|{row['accepted_projected_gap_hits']:,}|"
            f"{row['unresolved_gap_hits']:,}|"
        )

    lines.extend(["", "## 零冲突、跨至少 2 包且多角色的联合键", ""])
    accepted_with_gap = aggregate["recommended_nonoverlapping_rules"]
    if not accepted_with_gap:
        lines.append("本轮没有找到同时满足全部门槛且能覆盖现有缺口的联合键。")
    else:
        lines.extend(
            [
                "|职业/技能|联合键类型|联合键|真值Hit|跨包|多角色|可覆盖缺口Hit|",
                "|---|---|---|---:|---:|---:|---:|",
            ]
        )
        # Recover job from the examples because rules remain job-scoped in the
        # aggregate even though job is not part of the structural key.
        for row in accepted_with_gap:
            example = row["examples"][0] if row["examples"] else {}
            job = row["job"]
            skill = row["canonical_skill_id"]
            name = example.get("skill_name", "")
            key_text = ", ".join(f"{key}={value}" for key, value in row["key"].items())
            lines.append(
                f"|{job} / {skill} {name}|{row['variant']}|`{key_text}`|"
                f"{row['truth_hits']:,}|{row['truth_capture_count']}|{row['truth_actor_count']}|"
                f"{row['new_unique_gap_hits']:,}|"
            )

    reason_counts: Counter[str] = Counter()
    for row in aggregate["rejected_near_misses"]:
        for reason in row["reasons"]:
            reason_counts[reason] += int(row["gap_hits"])
    lines.extend(["", "## 未通过门槛的主要原因", ""])
    if reason_counts:
        for reason, count in reason_counts.most_common(20):
            lines.append(f"- {reason}：涉及候选缺口 {count:,} Hit（不同联合键可能重复计数）。")
    else:
        lines.append("- 无近似候选。")

    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- 本报告只证明固定 35 份语料中的结构稳定性；没有把任何规则写入生产解析器。",
            "- 同一缺口 Hit 可能匹配多个等价联合键，职业投影按 physical_id 去重，不能把规则表 Hit 直接相加。",
            "- `origin_skill_id_diagnostic` 只保存在 JSON 例证中，没有进入联合键，避免用生产归因结果反向证明自己。",
            "- 只有在后续独立生产回放仍保持来源角色、目标、伤害、暴击、对象 ID 和总伤害不变时，候选才适合接入。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--runtime-result", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("workers must be between 1 and 4")

    production_before = _tree_signature()
    captures, selection_report = _selection(args.selection)
    runtime = _runtime_jobs(args.runtime_result)
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_audit_capture, row): row for row in captures}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(f"[{index:02d}/{len(captures)}] {result['capture']}", flush=True)
    results.sort(key=lambda row: row["capture_sha256"])
    aggregate = _aggregate(results, runtime)
    production_after = _tree_signature()

    report = {
        "schema": SCHEMA,
        "scope": "fixed 35-capture offline audit; no production mutation; no time window",
        "acceptance_contract": {
            "deterministic_truth_conflicts": 0,
            "existing_gap_label_conflicts": 0,
            "minimum_truth_hits": 8,
            "minimum_capture_count": 2,
            "minimum_actor_count": 2,
            "generation_semantics": (
                "numeric generation is per-capture evidence; only current-at-hit is a key guard"
            ),
        },
        "inputs": {
            "runtime_result": str(args.runtime_result),
            "runtime_result_sha256": _hash_file(args.runtime_result),
            "selection": str(args.selection),
            "selection_sha256": _hash_file(args.selection),
            "native_correlations": str(
                ROOT / "outputs/native_wire_v50_20260909/native_meter_correlations_merged.json"
            ),
            "native_correlations_sha256": _hash_file(
                ROOT / "outputs/native_wire_v50_20260909/native_meter_correlations_merged.json"
            ),
            "capture_count": len(captures),
            "capture_manifest": captures,
            "selection_schema": selection_report.get("schema_version", ""),
        },
        "production_signature_before": production_before,
        "production_signature_after": production_after,
        "production_changed": production_before != production_after,
        "key_variants": {key: list(value) for key, value in KEY_VARIANTS.items()},
        "aggregate": aggregate,
        "capture_summaries": [
            {
                "capture": row["capture"],
                "capture_sha256": row["capture_sha256"],
                "counts": row["counts"],
                "row_commitment_sha256": sha256(
                    json.dumps(row["rows"], ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
            for row in results
        ],
    }
    if report["production_changed"]:
        raise RuntimeError("production Python tree changed during read-only audit")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "under95_native_joint_keys_v55.json"
    md_path = args.output_dir / "under95_native_joint_keys_v55.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_md(report), encoding="utf-8")
    print(json_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
