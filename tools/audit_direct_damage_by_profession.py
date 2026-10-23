from __future__ import annotations

import argparse
import bisect
import csv
import copy
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    AttributionLayer,
    attribution_layer,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.audit_direct_damage_release_gate import (  # noqa: E402
    MIXED_OWNER_METHODS,
    _classification as release_gate_classification,
    _is_explicitly_deferred_additional,
)


SCHEMA_VERSION = 1
DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "damage_attribution"

DETERMINISTIC_GATE_CLASSES = frozenset(
    {
        "deterministic",
        "deterministic_status_owner",
    }
)
EXPLICIT_ADDITIONAL_SKILL_IDS = frozenset({"3551", "6009"})
EXPLICIT_ADDITIONAL_SKILL_NAMES = frozenset({"冲击流", "突袭"})
UNKNOWN_SKILL_IDS = frozenset({"", "-1", "4294967295", "0xffffffff"})
UNKNOWN_SKILL_NAMES = frozenset(
    {"", "待识别伤害", "未知伤害", "普通/特殊攻击"}
)
SIGNATURE_FILES = (
    "dragon_nest_builder/live_dps/capture_sources.py",
    "dragon_nest_builder/live_dps/replay_duplicate_hit_evidence.py",
    "tools/audit_damage_attribution_paired.py",
    "tools/audit_direct_damage_release_gate.py",
    "dragon_nest_builder/data_loader.py",
    "dragon_nest_builder/live_dps/aggregator.py",
    "dragon_nest_builder/live_dps/models.py",
    "dragon_nest_builder/live_dps/replay_normalization.py",
    "dragon_nest_builder/live_dps/protocol_catalogs.py",
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_ledger.py",
    "dragon_nest_builder/live_dps/skill_attribution_v2.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/player_root_action_objects.py",
    "dragon_nest_builder/live_dps/generated_player_root_action_signal_specs_v101.py",
    "dragon_nest_builder/live_dps/entity_owner_evidence.py",
    "dragon_nest_builder/live_dps/entity_damage_owner.py",
    "dragon_nest_builder/live_dps/udp_damage_identity.py",
    "dragon_nest_builder/live_dps/udp_damage_records.py",
    "dragon_nest_builder/live_dps/damage_wire_evidence.py",
    "dragon_nest_builder/live_dps/native_damage_skill_keys.py",
    "dragon_nest_builder/live_dps/late_field_hit_evidence_v32.py",
    "dragon_nest_builder/live_dps/autonomous_attack_families.py",
    "dragon_nest_builder/live_dps/duck_rune_self_attack_v28.py",
    "dragon_nest_builder/live_dps/compact_entity_object_evidence.py",
    "dragon_nest_builder/live_dps/pve_scene_evidence.py",
    "dragon_nest_builder/live_dps/summon_attack_runtime.py",
    "dragon_nest_builder/live_dps/summon_self_attack_evidence_v26.py",
    "dragon_nest_builder/live_dps/generated_summon_self_attack_specs_v26.py",
    "dragon_nest_builder/live_dps/generated_summon_self_attack_specs_v100.py",
    "dragon_nest_builder/live_dps/generated_mirror_self_attack_specs_v26.py",
    "dragon_nest_builder/live_dps/damage_object_v25_shaman_illusion.py",
    "dragon_nest_builder/live_dps/generated_attack_instance_specs_v576.py",
    "dragon_nest_builder/live_dps/sources.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/v576/actor_state.py",
    "dragon_nest_builder/live_dps/v576/profile_decoder.py",
    "dragon_nest_builder/live_dps/v576/combat_decoder.py",
    "dragon_nest_builder/live_dps/v576/status_decoder.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/data/live_dps_profiles/v576.json",
    "dragon_nest_builder/data/modular/manifest.json",
    "dragon_nest_builder/data/modular/tables/character_base.json",
    "dragon_nest_builder/data/modular/tables/job_map.json",
    "dragon_nest_builder/data/modular/tables/skill_buff.json",
)
SIGNATURE_DATA_GLOBS = (
    "dragon_nest_builder/data/live_dps_profiles/*.json",
    "dragon_nest_builder/data/live_dps_profiles/*.json.gz",
    "dragon_nest_builder/data/modular/tables/skills/skill_damage_tiny_jobs/*.json",
    "dragon_nest_builder/data/modular/tables/skills/dngamer_skill_sim_reborn_split/jobs/*.json",
)
RECLASSIFICATION_POLICY = "mixed_owner_methods_to_nondeterministic_v1"


def _analysis_signature() -> dict[str, Any]:
    files: dict[str, str] = {}
    combined = hashlib.sha256()
    own_path = Path(__file__).resolve()
    paths = [own_path, *(ROOT / relative for relative in SIGNATURE_FILES)]
    paths.extend(path for pattern in SIGNATURE_DATA_GLOBS for path in sorted(ROOT.glob(pattern)))
    for path in dict.fromkeys(paths):
        if not path.is_file():
            continue
        relative = (
            str(path.relative_to(ROOT)).replace("\\", "/")
            if path != own_path
            else "tools/audit_direct_damage_by_profession.py"
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[relative] = digest
        combined.update(relative.encode("utf-8"))
        combined.update(b"\0")
        combined.update(digest.encode("ascii"))
        combined.update(b"\0")
    return {"sha256": combined.hexdigest(), "files": files}


def _second_jobs() -> list[str]:
    store = DataStore.load()
    jobs: list[str] = []
    for first_job in store.first_jobs():
        for job in store.second_jobs(first_job):
            if job and job not in jobs:
                jobs.append(job)
    if len(jobs) != 30:
        raise RuntimeError(f"二转职业目录应为30个，实际读取到{len(jobs)}个")
    return jobs


def _parse_actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _registration_for_event(
    registrations: dict[tuple[int, int], list[dict[str, int]]],
    event: CombatEvent,
) -> dict[str, int] | None:
    actor_id = _parse_actor(event.source_id)
    object_id = int(event.wire_damage_object_id or 0)
    if actor_id <= 0 or object_id <= 0:
        return None
    instance_id = str(event.cast_instance_id or "").strip()
    if not instance_id.casefold().startswith("damage-object:"):
        # Runtime object numbers are recycled.  A nearest historic
        # registration without the exact generation can put an unrelated
        # template beside a field/direct Hit and make the audit itself look
        # more certain than production.  Only generation-bound events may
        # display a concrete template.
        return None
    try:
        _prefix, owner_hex, instance_object_id, generation = instance_id.split(
            ":", 3
        )
        if int(owner_hex, 16) != actor_id or int(instance_object_id) != object_id:
            return None
        expected_generation = int(generation)
    except (TypeError, ValueError):
        return None
    rows = registrations.get((actor_id, object_id), ())
    if not rows:
        return None
    timestamps = [row["timestamp_ms"] for row in rows]
    index = bisect.bisect_right(timestamps, int(event.timestamp_ms)) - 1
    if index < 0:
        return None
    row = rows[index]
    if (
        int(row.get("generation", 0)) != expected_generation
        or int(event.timestamp_ms) > int(row.get("expires_at_ms", -1))
    ):
        return None
    return row


def _skill_identity_missing(event: CombatEvent) -> bool:
    skill_id = str(event.skill_id or "").strip().casefold()
    skill_name = str(event.skill_name or "").strip()
    if (
        skill_id in {"4294967295", "0xffffffff"}
        and skill_name == "弩系普通攻击"
        and str(event.attribution_method or "").strip().casefold()
        == "damage_object_projectile_link"
    ):
        # ``弩系普通攻击`` is emitted by the decoder only after template 92
        # has passed the exact actor + damage_object generation lookup.  The
        # replay aggregator may intentionally omit ``cast_instance_id`` from
        # its compact event copy, so that display-only omission must not turn
        # an otherwise exact object identity back into an unknown skill.
        return False
    return skill_id in UNKNOWN_SKILL_IDS or skill_name in UNKNOWN_SKILL_NAMES


def _scope_reason(event: CombatEvent) -> str:
    if event.healing:
        return "excluded_healing"
    if event.source_id == "__incoming_damage__":
        return "excluded_incoming_damage"
    if int(event.amount or 0) <= 0:
        return "excluded_non_positive"
    if _is_explicitly_deferred_additional(event):
        return "excluded_explicit_additional"

    skill_id = str(event.skill_id or "").strip().casefold()
    raw_skill_id = str(event.raw_skill_id or "").strip().casefold()
    skill_name = str(event.skill_name or "").strip()
    raw_skill_name = str(event.raw_skill_name or "").strip()
    if (
        skill_id.startswith(("supplemental:", "periodic:"))
        or raw_skill_id.startswith(("supplemental:", "periodic:"))
        or skill_id in EXPLICIT_ADDITIONAL_SKILL_IDS
        or raw_skill_id in EXPLICIT_ADDITIONAL_SKILL_IDS
        or skill_name in EXPLICIT_ADDITIONAL_SKILL_NAMES
        or raw_skill_name in EXPLICIT_ADDITIONAL_SKILL_NAMES
    ):
        return "excluded_explicit_additional"

    method = str(event.attribution_method or "unresolved").strip().casefold()
    if attribution_layer(method) is AttributionLayer.STATUS:
        return "excluded_status_or_periodic"
    if event.has_real_hit_evidence is not True:
        return "excluded_without_real_hit_evidence"
    return "included_positive_direct_hit"


def _strict_classification(event: CombatEvent) -> str:
    method = str(event.attribution_method or "unresolved").strip().casefold()
    layer = attribution_layer(method)
    gate_class = release_gate_classification(event)
    if gate_class in DETERMINISTIC_GATE_CLASSES and not _skill_identity_missing(event):
        return "deterministic"
    if layer is AttributionLayer.UNRESOLVED or _skill_identity_missing(event):
        return "unattributed"
    return "nondeterministic"


def _named_direct_candidate_class(event: CombatEvent) -> str | None:
    """Additional, broader gate: never hide named hits lacking hit evidence.

    The historic wire-hit scope is retained for like-for-like comparisons.
    This separate diagnostic also includes named direct_hit_overflow rows;
    they are unproven, not silently declared proc damage or deterministic.
    """
    if (event.healing or event.source_id == "__incoming_damage__"
            or int(event.amount or 0) <= 0 or _skill_identity_missing(event)):
        return None
    identities = (str(event.skill_id or "").strip().casefold(),
                  str(event.raw_skill_id or "").strip().casefold())
    names = (str(event.skill_name or "").strip(), str(event.raw_skill_name or "").strip())
    if (any(value.startswith(("supplemental:", "periodic:"))
            or value in EXPLICIT_ADDITIONAL_SKILL_IDS for value in identities)
            or any(value in EXPLICIT_ADDITIONAL_SKILL_NAMES for value in names)):
        return None
    method = str(event.attribution_method or "unresolved").strip().casefold()
    if attribution_layer(method) is AttributionLayer.STATUS:
        return None
    if method == "direct_hit_overflow" or event.has_real_hit_evidence is not True:
        return "unproven"
    return "deterministic" if _strict_classification(event) == "deterministic" else "unproven"


def _counter_json(counter: Counter[str], limit: int | None = None) -> str:
    rows = counter.most_common(limit)
    return json.dumps(dict(rows), ensure_ascii=False, separators=(",", ":"))


def _raw_damage_invariants(events: Iterable[Any]) -> dict[str, Any]:
    """Conservation independent of profession, skill name or attribution layer.

    Compare the exact multiset, not just totals: a missed Hit and a duplicate
    of equal damage must not silently cancel each other. Actor IDs here remain
    the decoder/aggregator IDs so an accidental owner change is also detected.
    """
    rows: list[str] = []
    total = real_hits = 0
    evidence_rows: list[str] = []
    for event in events:
        if not isinstance(event, CombatEvent) or event.healing:
            continue
        total += int(event.amount)
        real_hits += event.has_real_hit_evidence is True
        row = json.dumps((
            event.timestamp_ms, event.source_id, event.target_id, event.amount,
            event.critical,
            event.wire_result_group_id, event.wire_damage_object_id,
        ), ensure_ascii=True, separators=(",", ":"))
        rows.append(row)
        evidence_rows.append(row + str(event.has_real_hit_evidence))
    digest = hashlib.sha256()
    for row in sorted(rows):
        digest.update(row.encode("ascii"))
        digest.update(b"\n")
    return {"rows": len(rows), "real_hits": real_hits, "damage": total,
            "sha256": digest.hexdigest(),
            "hit_evidence_sha256": hashlib.sha256("\n".join(sorted(evidence_rows)).encode("ascii")).hexdigest()}


def _audit_capture(path_text: str, professions: tuple[str, ...]) -> dict[str, Any]:
    path = Path(path_text)
    profession_set = frozenset(professions)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    registrations_absolute: list[dict[str, int | float]] = []
    object_ledger = decoder.damage_object_origins
    original_registration = object_ledger.observe_registration

    def observe_registration(**kwargs):
        origin_row = original_registration(**kwargs)
        if origin_row is not None:
            registrations_absolute.append(
                {
                    "source_actor_id": int(kwargs["source_actor_id"]),
                    "damage_object_id": int(kwargs["damage_object_id"]),
                    "projectile_template_id": int(
                        origin_row.projectile_template_id
                    ),
                    "effect_resource_id": int(origin_row.effect_resource_id),
                    "child_sequence": int(origin_row.child_sequence),
                    "timestamp": float(origin_row.registered_at),
                    "expires_at": float(origin_row.expires_at),
                    "generation": int(origin_row.generation),
                }
            )
        return origin_row

    object_ledger.observe_registration = observe_registration
    try:
        source_events = source.poll()
    finally:
        source.close()

    origin = float(decoder.first_timestamp or 0.0)
    registrations: dict[tuple[int, int], list[dict[str, int]]] = defaultdict(list)
    for row in registrations_absolute:
        converted = {
            "source_actor_id": int(row["source_actor_id"]),
            "damage_object_id": int(row["damage_object_id"]),
            "projectile_template_id": int(row["projectile_template_id"]),
            "effect_resource_id": int(row["effect_resource_id"]),
            "child_sequence": int(row["child_sequence"]),
            "generation": int(row["generation"]),
            "timestamp_ms": max(
                0, round((float(row["timestamp"]) - origin) * 1000.0)
            ),
            "expires_at_ms": max(
                0, round((float(row["expires_at"]) - origin) * 1000.0)
            ),
        }
        registrations[
            (converted["source_actor_id"], converted["damage_object_id"])
        ].append(converted)
    for rows in registrations.values():
        rows.sort(key=lambda value: value["timestamp_ms"])

    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    events = aggregator.snapshot(include_events=True).combat_events

    excluded: Counter[str] = Counter()
    outside_professions: Counter[str] = Counter()
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    named_candidates: dict[str, Counter[str]] = defaultdict(Counter)
    skill_transitions: dict[
        tuple[str, str, str, str, str, str, str], Counter[str]
    ] = defaultdict(Counter)
    groups: dict[tuple[str, str, str, str, str, str], Counter[str]] = defaultdict(
        Counter
    )

    for event in events:
        candidate_job = str(event.job or "").strip() or "未知职业"
        candidate_class = _named_direct_candidate_class(event)
        if candidate_class is not None and candidate_job in profession_set:
            counts = named_candidates[candidate_job]
            counts["named_candidate_hits"] += 1
            counts[f"{candidate_class}_hits"] += 1
            counts["without_real_hit_evidence"] += event.has_real_hit_evidence is not True
            counts["direct_hit_overflow"] += event.attribution_method == "direct_hit_overflow"
        reason = _scope_reason(event)
        if reason != "included_positive_direct_hit":
            excluded[reason] += 1
            continue
        job = str(event.job or "").strip() or "未知职业"
        if job not in profession_set:
            outside_professions[job] += 1
            continue

        classification = _strict_classification(event)
        method = str(event.attribution_method or "unresolved").strip().casefold()
        skill_id = str(event.skill_id or "-1").strip() or "-1"
        skill_name = str(event.skill_name or "待识别伤害").strip() or "待识别伤害"
        registration = _registration_for_event(registrations, event)
        template = (
            str(registration["projectile_template_id"])
            if registration is not None
            else (f"实体模板:{event.entity_template_id}" if getattr(event, "entity_template_id", 0)
                  else ("<对象存在但模板未解析>" if event.wire_damage_object_id else ""))
        )
        amount = max(0, int(event.amount or 0))

        summary[job]["total_direct_hits"] += 1
        summary[job][f"{classification}_hits"] += 1
        summary[job]["total_direct_damage"] += amount
        summary[job][f"{classification}_damage"] += amount
        raw_skill_id = str(event.raw_skill_id or "").strip()
        raw_skill_name = str(event.raw_skill_name or "").strip()
        raw_method = str(event.raw_attribution_method or "").strip().casefold()
        if (
            classification == "deterministic"
            and raw_skill_id
            and raw_skill_name
            and (raw_skill_id != skill_id or raw_skill_name != skill_name)
        ):
            transition_key = (
                job,
                raw_skill_id,
                raw_skill_name,
                raw_method,
                skill_id,
                skill_name,
                method,
            )
            skill_transitions[transition_key]["hits"] += 1
            skill_transitions[transition_key]["damage"] += amount
        group_key = (
            job,
            classification,
            method,
            skill_id,
            skill_name,
            template,
        )
        groups[group_key]["hits"] += 1
        groups[group_key]["damage"] += amount

    detail_rows = []
    for key, counts in groups.items():
        detail_rows.append(
            {
                "job": key[0],
                "classification": key[1],
                "method": key[2],
                "skill_id": key[3],
                "skill_name": key[4],
                "projectile_template_id": key[5],
                "hits": int(counts["hits"]),
                "damage": int(counts["damage"]),
                "capture": path.name,
                "capture_path": str(path),
            }
        )
    return {
        "capture": path.name,
        "capture_path": str(path),
        "decoded_events": len(source_events),
        "profession_summary": {
            job: dict(counts) for job, counts in summary.items()
        },
        "named_direct_candidates": {job: dict(counts) for job, counts in named_candidates.items()},
        "details": detail_rows,
        "skill_transitions": [
            {
                "job": key[0],
                "raw_skill_id": key[1],
                "raw_skill_name": key[2],
                "raw_method": key[3],
                "skill_id": key[4],
                "skill_name": key[5],
                "method": key[6],
                "hits": int(counts["hits"]),
                "damage": int(counts["damage"]),
                "capture": path.name,
            }
            for key, counts in skill_transitions.items()
        ],
        "excluded": dict(excluded),
        "outside_professions": dict(outside_professions),
        "registrations": len(registrations_absolute),
        "capture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "raw_source_invariants": _raw_damage_invariants(source_events),
        "aggregated_invariants": _raw_damage_invariants(events),
    }


def _checkpoint_path(directory: Path, source_path: Path) -> Path:
    digest = hashlib.sha256(str(source_path.resolve()).encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _load_checkpoint(
    path: Path,
    *,
    source_path: Path,
    signature: dict[str, Any],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stat = source_path.stat()
    if (
        payload.get("postprocessing")
        or
        payload.get("source_path") != str(source_path.resolve())
        or payload.get("source_size") != stat.st_size
        or payload.get("source_mtime_ns") != stat.st_mtime_ns
        or payload.get("analysis_signature") != signature
    ):
        return None
    report = payload.get("report")
    return report if isinstance(report, dict) else None


def _write_checkpoint(
    path: Path,
    *,
    source_path: Path,
    signature: dict[str, Any],
    report: dict[str, Any],
) -> None:
    stat = source_path.stat()
    payload = {
        "source_path": str(source_path.resolve()),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "analysis_signature": signature,
        "report": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _discover_captures(values: Iterable[Path], capture_root: Path) -> list[Path]:
    candidates: list[Path] = []
    requested = list(values)
    if not requested:
        requested = [capture_root]
    for value in requested:
        path = value.expanduser().resolve()
        if path.is_dir():
            candidates.extend(path.rglob("*.dpscap"))
        elif path.is_file() and path.suffix.casefold() == ".dpscap":
            candidates.append(path)
    return sorted(set(path.resolve() for path in candidates), key=lambda row: str(row))


def _deduplicate_captures(paths: list[Path]) -> tuple[list[Path], list[dict[str, str]]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in paths:
        by_size[path.stat().st_size].append(path)
    kept: list[Path] = []
    duplicates: list[dict[str, str]] = []
    for same_size in by_size.values():
        if len(same_size) == 1:
            kept.append(same_size[0])
            continue
        digest_owner: dict[str, Path] = {}
        for path in same_size:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            owner = digest_owner.get(digest)
            if owner is None:
                digest_owner[digest] = path
                kept.append(path)
            else:
                duplicates.append({"duplicate": str(path), "kept": str(owner)})
    kept.sort(key=lambda row: str(row))
    duplicates.sort(key=lambda row: row["duplicate"])
    return kept, duplicates


def _merge_reports(
    reports: list[dict[str, Any]],
    professions: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    profession_counts: dict[str, Counter[str]] = {
        job: Counter() for job in professions
    }
    profession_files: dict[str, set[str]] = {job: set() for job in professions}
    detail_counts: dict[
        tuple[str, str, str, str, str, str], Counter[str]
    ] = defaultdict(Counter)
    detail_files: dict[tuple[str, str, str, str, str, str], Counter[str]] = (
        defaultdict(Counter)
    )
    excluded = Counter()
    outside_professions = Counter()
    decoded_events = 0
    registrations = 0
    transition_counts: dict[
        tuple[str, str, str, str, str, str, str], Counter[str]
    ] = defaultdict(Counter)
    transition_files: dict[
        tuple[str, str, str, str, str, str, str], set[str]
    ] = defaultdict(set)

    for report in reports:
        decoded_events += int(report.get("decoded_events", 0))
        registrations += int(report.get("registrations", 0))
        excluded.update(report.get("excluded", {}))
        outside_professions.update(report.get("outside_professions", {}))
        capture = str(report.get("capture", ""))
        for job, values in report.get("profession_summary", {}).items():
            if job not in profession_counts:
                continue
            profession_counts[job].update(
                {key: int(value) for key, value in values.items()}
            )
            if int(values.get("total_direct_hits", 0)) > 0:
                profession_files[job].add(capture)
        for row in report.get("details", []):
            key = (
                str(row.get("job", "")),
                str(row.get("classification", "")),
                str(row.get("method", "")),
                str(row.get("skill_id", "")),
                str(row.get("skill_name", "")),
                str(row.get("projectile_template_id", "")),
            )
            detail_counts[key]["hits"] += int(row.get("hits", 0))
            detail_counts[key]["damage"] += int(row.get("damage", 0))
            detail_files[key][capture] += int(row.get("hits", 0))
        for row in report.get("skill_transitions", []):
            key = (
                str(row.get("job", "")),
                str(row.get("raw_skill_id", "")),
                str(row.get("raw_skill_name", "")),
                str(row.get("raw_method", "")),
                str(row.get("skill_id", "")),
                str(row.get("skill_name", "")),
                str(row.get("method", "")),
            )
            transition_counts[key]["hits"] += int(row.get("hits", 0))
            transition_counts[key]["damage"] += int(row.get("damage", 0))
            transition_files[key].add(capture)

    detail_rows: list[dict[str, Any]] = []
    for key, counts in detail_counts.items():
        examples = [name for name, _count in detail_files[key].most_common(5)]
        detail_rows.append(
            {
                "职业": key[0],
                "归因分类": key[1],
                "method": key[2],
                "skill_id": key[3],
                "技能名": key[4],
                "object模板": key[5],
                "Hit数": int(counts["hits"]),
                "伤害": int(counts["damage"]),
                "抓包数": len(detail_files[key]),
                "示例抓包": " | ".join(examples),
            }
        )
    detail_rows.sort(
        key=lambda row: (
            professions.index(str(row["职业"])),
            {"unattributed": 0, "nondeterministic": 1, "deterministic": 2}.get(
                str(row["归因分类"]), 9
            ),
            -int(row["Hit数"]),
            str(row["method"]),
        )
    )

    detail_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in detail_rows:
        detail_by_job[str(row["职业"])].append(row)

    summary_rows: list[dict[str, Any]] = []
    for job in professions:
        counts = profession_counts[job]
        total = int(counts["total_direct_hits"])
        deterministic = int(counts["deterministic_hits"])
        unattributed = int(counts["unattributed_hits"])
        nondeterministic = int(counts["nondeterministic_hits"])
        gaps = unattributed + nondeterministic
        job_details = detail_by_job[job]
        methods = Counter()
        skills = Counter()
        templates = Counter()
        gap_captures = Counter()
        for row in job_details:
            if row["归因分类"] == "deterministic":
                continue
            methods[str(row["method"])] += int(row["Hit数"])
            skills[f"{row['skill_id']} {row['技能名']}"] += int(row["Hit数"])
            if row["object模板"]:
                templates[str(row["object模板"])] += int(row["Hit数"])
            for capture in str(row["示例抓包"]).split(" | "):
                if capture:
                    gap_captures[capture] += int(row["Hit数"])
        summary_rows.append(
            {
                "职业": job,
                "总直接Hit": total,
                "确定性已归因": deterministic,
                "未归因": unattributed,
                "非确定性归因": nondeterministic,
                "归因缺口": gaps,
                "确定性覆盖率%": round(deterministic / max(1, total) * 100.0, 6),
                "距离95%确定性Hit": max(
                    0,
                    ((total * 95 + 99) // 100) - deterministic,
                ),
                "已命名Hit确定性覆盖率%": round(
                    deterministic / max(1, deterministic + nondeterministic) * 100.0, 6
                ),
                "未归因率%": round(unattributed / max(1, total) * 100.0, 6),
                "非确定性率%": round(
                    nondeterministic / max(1, total) * 100.0, 6
                ),
                "总直接伤害": int(counts["total_direct_damage"]),
                "确定性伤害": int(counts["deterministic_damage"]),
                "确定性伤害覆盖率%": round(
                    int(counts["deterministic_damage"])
                    / max(1, int(counts["total_direct_damage"]))
                    * 100.0,
                    6,
                ),
                "缺口伤害": int(counts["unattributed_damage"])
                + int(counts["nondeterministic_damage"]),
                "抓包数": len(profession_files[job]),
                "主要缺口method": _counter_json(methods, 8),
                "主要缺口skill_id": _counter_json(skills, 8),
                "主要缺口object模板": _counter_json(templates, 8),
                "示例抓包": " | ".join(
                    name for name, _count in gap_captures.most_common(5)
                ),
            }
        )

    totals = Counter()
    for row in summary_rows:
        totals["total_direct_hits"] += int(row["总直接Hit"])
        totals["deterministic_hits"] += int(row["确定性已归因"])
        totals["unattributed_hits"] += int(row["未归因"])
        totals["nondeterministic_hits"] += int(row["非确定性归因"])
        totals["total_direct_damage"] += int(row["总直接伤害"])
        totals["deterministic_damage"] += int(row["确定性伤害"])
        totals["gap_damage"] += int(row["缺口伤害"])
    metadata = {
        "decoded_events": decoded_events,
        "registrations": registrations,
        "excluded": dict(excluded.most_common()),
        "outside_professions": dict(outside_professions.most_common()),
        "totals": dict(totals),
        "skill_transitions": [
            {
                "job": key[0],
                "raw_skill_id": key[1],
                "raw_skill_name": key[2],
                "raw_method": key[3],
                "skill_id": key[4],
                "skill_name": key[5],
                "method": key[6],
                "hits": int(counts["hits"]),
                "damage": int(counts["damage"]),
                "captures": sorted(transition_files[key]),
            }
            for key, counts in sorted(
                transition_counts.items(),
                key=lambda item: (-int(item[1]["hits"]), item[0]),
            )
        ],
    }
    return summary_rows, detail_rows, metadata


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _skill_transition_rows(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose exact cross-skill corrections as a first-class audit artifact."""

    rows: list[dict[str, Any]] = []
    for item in metadata.get("skill_transitions", ()):
        captures = item.get("captures", ())
        rows.append(
            {
                "职业": item.get("job", ""),
                "原skill_id": item.get("raw_skill_id", ""),
                "原技能名": item.get("raw_skill_name", ""),
                "原method": item.get("raw_method", ""),
                "确认skill_id": item.get("skill_id", ""),
                "确认技能名": item.get("skill_name", ""),
                "确认method": item.get("method", ""),
                "Hit数": int(item.get("hits", 0)),
                "伤害": int(item.get("damage", 0)),
                "抓包数": len(captures),
                "示例抓包": " | ".join(str(value) for value in captures[:5]),
            }
        )
    return rows


def _write_markdown(
    path: Path,
    *,
    capture_count: int,
    duplicate_count: int,
    failures: list[dict[str, str]],
    summary_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    postprocessing: dict[str, Any] | None = None,
) -> None:
    totals = metadata["totals"]
    total = int(totals.get("total_direct_hits", 0))
    deterministic = int(totals.get("deterministic_hits", 0))
    unattributed = int(totals.get("unattributed_hits", 0))
    nondeterministic = int(totals.get("nondeterministic_hits", 0))
    total_damage = int(totals.get("total_direct_damage", 0))
    deterministic_damage = int(totals.get("deterministic_damage", 0))
    lines = [
        "# 30个二转职业严格直接伤害归因缺口",
        "",
        "## 审计口径",
        "",
        "- 仅统计 `amount > 0` 且 `has_real_hit_evidence=True` 的对外直接 Hit。",
        "- 排除治疗、承伤、状态/周期伤害、`supplemental:/periodic:`、`direct_hit_overflow`，以及突袭/冲击流附加伤害。",
        "- 确定性只接受 release-gate 已认可的包内固定技能字段、已验证对象链和明确实体所有者。",
        "- result-group、时间窗口、最近动作、learned profile、候选唯一性等均记为非确定性归因。",
        "- summon_owner、supplemental_summon_owner、retroactive_permanent_summon_owner、skill_object_owner 混合了明确归属与推断，不能凭标签计为确定性；保留原 Hit 分母。",
        "- `未归因` 指技能身份为空/占位或 method 属于 unresolved；`非确定性归因` 指已有技能名但证据不足。",
        "",
        "## 总览",
        "",
        f"- 有效抓包：{capture_count}（跳过重复文件 {duplicate_count}，失败 {len(failures)}）",
        f"- 30职业正向直接 Hit：{total:,}",
        f"- 确定性已归因：{deterministic:,}（{deterministic / max(1, total) * 100:.2f}%）",
        f"- 确定性伤害金额：{deterministic_damage:,} / {total_damage:,}"
        f"（{deterministic_damage / max(1, total_damage) * 100:.2f}%）",
        f"- 未归因：{unattributed:,}（{unattributed / max(1, total) * 100:.2f}%）",
        f"- 非确定性归因：{nondeterministic:,}（{nondeterministic / max(1, total) * 100:.2f}%）",
        "",
        "| 职业 | 总直接Hit | 确定性 | 未归因 | 非确定性 | Hit覆盖率 | 伤害覆盖率 | 距离95%还差Hit | 抓包数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary_rows:
        lines.append(
            f"| {row['职业']} | {int(row['总直接Hit']):,} | "
            f"{int(row['确定性已归因']):,} | {int(row['未归因']):,} | "
            f"{int(row['非确定性归因']):,} | "
            f"{float(row['确定性覆盖率%']):.2f}% | "
            f"{float(row['确定性伤害覆盖率%']):.2f}% | "
            f"{int(row['距离95%确定性Hit']):,} | {int(row['抓包数'])} |"
        )

    transitions = _skill_transition_rows(metadata)
    lines.extend(
        [
            "",
            "## 已确认的跨技能纠正",
            "",
            "这里记录逐 Hit 的确定性证据把旧技能桶纠正到另一真实技能；"
            "同一旧名称可能流向多个真实技能，因此不能做全局名称替换。",
            "",
            "|职业|原技能|原方法|确认技能|确认方法|Hit|伤害|抓包数|",
            "|---|---|---|---|---|---:|---:|---:|",
        ]
    )
    if not transitions:
        lines.append("|无|-|-|-|-|0|0|0|")
    else:
        for item in transitions[:100]:
            lines.append(
                f"|{item['职业']}|{item['原技能名']} `{item['原skill_id']}`|"
                f"`{item['原method']}`|{item['确认技能名']} "
                f"`{item['确认skill_id']}`|`{item['确认method']}`|"
                f"{item['Hit数']:,}|{item['伤害']:,}|{item['抓包数']}|"
            )

    by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in detail_rows:
        if row["归因分类"] != "deterministic":
            by_job[str(row["职业"])].append(row)
    lines.extend(["", "## 各职业主要缺口", ""])
    for row in summary_rows:
        job = str(row["职业"])
        lines.extend(
            [
                f"### {job}",
                "",
                f"缺口 {int(row['归因缺口']):,} / {int(row['总直接Hit']):,} Hit；"
                f"确定性伤害覆盖 {float(row['确定性伤害覆盖率%']):.2f}%；"
                f"距离 95% Hit 覆盖还差 {int(row['距离95%确定性Hit']):,} Hit；"
                f"主要示例：{row['示例抓包'] or '无'}。",
                "",
                "| 分类 | method | skill_id | 当前技能名 | object模板 | Hit | 示例抓包 |",
                "|---|---|---:|---|---:|---:|---|",
            ]
        )
        candidates = sorted(
            by_job.get(job, []), key=lambda value: -int(value["Hit数"])
        )[:10]
        if not candidates:
            lines.append("| 无 | - | - | - | - | 0 | - |")
        else:
            for item in candidates:
                lines.append(
                    f"| {item['归因分类']} | {item['method']} | "
                    f"{item['skill_id']} | {item['技能名']} | "
                    f"{item['object模板'] or '-'} | {int(item['Hit数']):,} | "
                    f"{item['示例抓包']} |"
                )
        lines.append("")
    if failures:
        lines.extend(["## 失败文件", ""])
        for failure in failures:
            lines.append(f"- `{failure['path']}`：{failure['error']}")
        lines.append("")
    if postprocessing is not None:
        lines[2:2] = [
            "此报告仅对既有 checkpoint 的四类 owner method 重新分类，未重放抓包。",
            "原始/聚合物理摘要、技能显示、职业、Hit 范围与伤害数值均沿用源报告；不是新版 runtime 重算。",
            f"源报告：`{postprocessing['input_report']}`；完整来源指纹见 JSON 的 postprocessing。",
            "",
        ]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _summary_from_details(details: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    summary: dict[str, Counter[str]] = defaultdict(Counter)
    for row in details:
        classification = row["classification"]
        if classification not in {"deterministic", "nondeterministic", "unattributed"}:
            raise ValueError(f"Unsupported checkpoint classification: {classification}")
        hits, damage = int(row["hits"]), int(row["damage"])
        if hits <= 0 or damage <= 0:
            raise ValueError("Checkpoint details must contain positive direct Hit groups")
        counts = summary[row["job"]]
        counts["total_direct_hits"] += hits
        counts["total_direct_damage"] += damage
        counts[f"{classification}_hits"] += hits
        counts[f"{classification}_damage"] += damage
    return dict(summary)


def reclassify_checkpoint_report(
    report: dict[str, Any],
    *,
    source_analysis_signature: dict[str, Any] | None = None,
    source_gate_sha256: str | None = None,
) -> dict[str, Any]:
    """Downgrade only the four mixed-owner methods; never reconstruct events.

    Details already encode the old scope/actor/job/skill decisions. Reapplying
    runtime or scope rules here would silently change the baseline population.
    Accepts checkpoint['report'] and paired before/after dicts of that shape.
    Pass the old signature/gate hash when the dict has no provenance itself;
    an unavailable old gate is recorded as unknown, never inferred from the
    current file. All original module hashes/metadata are retained unchanged.
    """
    original = _summary_from_details(report["details"])
    expected = {job: Counter(values) for job, values in report["profession_summary"].items()}
    if original != expected:
        raise ValueError("Checkpoint details do not reconcile with profession_summary")
    result = copy.deepcopy(report)
    classification_signature = _classification_signature()
    previous = report.get("reclassification", {})
    if (previous.get("policy") == RECLASSIFICATION_POLICY
            and previous.get("classification_signature") == classification_signature
            and not any(row["classification"] == "deterministic"
                        and str(row["method"]).strip().casefold() in MIXED_OWNER_METHODS
                        for row in report["details"])):
        return result
    changed: dict[str, Counter[str]] = defaultdict(Counter)
    for row in result["details"]:
        method = str(row["method"]).strip().casefold()
        if method in MIXED_OWNER_METHODS and row["classification"] == "deterministic":
            row["classification"] = "nondeterministic"
            changed[method]["hits"] += int(row["hits"])
            changed[method]["damage"] += int(row["damage"])
    updated = _summary_from_details(result["details"])
    for job, counts in original.items():
        for key in ("total_direct_hits", "total_direct_damage", "unattributed_hits", "unattributed_damage"):
            if counts[key] != updated[job][key]:
                raise ValueError(f"Reclassification changed preserved counter: {job}/{key}")
    result["profession_summary"] = {job: dict(counts) for job, counts in updated.items()}
    source_signature = source_analysis_signature or report.get("analysis_signature")
    old_gate = source_gate_sha256 or (
        source_signature.get("files", {}).get("tools/audit_direct_damage_release_gate.py")
        if source_signature else None
    )
    result["reclassification"] = {
        "policy": RECLASSIFICATION_POLICY,
        "reason": "Mixed owner methods include recent-caster/profile inference; the label alone does not prove owner identity.",
        "old_gate_sha256": old_gate,
        "old_gate_hash_status": "available" if old_gate else "unavailable_in_source_report",
        "new_gate_sha256": classification_signature["files"]["audit_direct_damage_release_gate.py"],
        "source_analysis_signature": copy.deepcopy(source_signature),
        "classification_signature": classification_signature,
        "replayed": False,
        "scope_actor_job_skill_and_damage_unchanged": True,
        "changed_by_method": {method: dict(values) for method, values in sorted(changed.items())},
    }
    return result


# The same pure dict API is used for paired before.json/after.json reports.
reclassify_report = reclassify_checkpoint_report


def _classification_signature() -> dict[str, Any]:
    """Postprocessor identity, deliberately separate from the old decode hash."""
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__).resolve(), ROOT / "tools/audit_direct_damage_release_gate.py")
    }
    identity = {"policy": RECLASSIFICATION_POLICY, "methods": sorted(MIXED_OWNER_METHODS), "files": files}
    return {**identity, "sha256": hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()}


def reclassify_checkpoints(source_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Create a separately identified audit from saved checkpoints, with no replay.

    The source report's capture manifest is authoritative. Do not enumerate
    captures, stat/read .dpscap files, or relabel old results with today's
    runtime signature. Every consumed checkpoint must match that manifest.
    """
    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    if source_dir == output_dir or source_dir in output_dir.parents or output_dir in source_dir.parents:
        raise ValueError("Reclassification output must be separate from the source directory")
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite an existing output directory: {output_dir}")
    input_path = source_dir / "profession_direct_damage_report.json"
    input_bytes = input_path.read_bytes()
    baseline = json.loads(input_bytes)
    if baseline.get("postprocessing"):
        raise ValueError("Use an original replay report, not a postprocessed report")
    professions = baseline["professions"]
    if len(professions) != len(set(professions)):
        raise ValueError("Duplicate professions in source report")
    manifest = baseline["capture_manifest"]
    if len(manifest) != int(baseline["successful_captures"]):
        raise ValueError("Source capture manifest is incomplete")
    original_reports, reports, checkpoints, inputs = [], [], [], []
    changed: dict[str, Counter[str]] = defaultdict(Counter)
    seen: set[str] = set()
    classification_signature = _classification_signature()
    physical_keys = ("capture_sha256", "decoded_events", "raw_source_invariants", "aggregated_invariants")
    checkpoint_dir = source_dir / "profession_direct_damage_checkpoints"
    for entry in manifest:
        capture_path = entry["capture_path"]
        if capture_path in seen:
            raise ValueError(f"Duplicate capture in source manifest: {capture_path}")
        seen.add(capture_path)
        checkpoint_path = _checkpoint_path(checkpoint_dir, Path(capture_path))
        checkpoint_bytes = checkpoint_path.read_bytes()
        payload = json.loads(checkpoint_bytes)
        if payload.get("postprocessing") or payload["analysis_signature"] != baseline["analysis_signature"]:
            raise ValueError(f"Checkpoint has a different replay provenance: {checkpoint_path.name}")
        report = payload["report"]
        if payload["source_path"] != capture_path or report["capture_path"] != capture_path:
            raise ValueError(f"Checkpoint capture path mismatch: {checkpoint_path.name}")
        for key in (*physical_keys, "profession_summary"):
            if key not in entry or key not in report or entry[key] != report[key]:
                raise ValueError(f"Checkpoint/manifest mismatch: {checkpoint_path.name}/{key}")
        if not set(report["profession_summary"]).issubset(professions):
            raise ValueError(f"Unexpected profession in checkpoint: {checkpoint_path.name}")
        revised = reclassify_checkpoint_report(report, source_analysis_signature=payload["analysis_signature"])
        changes = revised["reclassification"]["changed_by_method"]
        for method, values in changes.items():
            changed[method].update(values)
        original_reports.append(report)
        reports.append(revised)
        checkpoint_input = {
            "path": str(checkpoint_path), "capture_path": capture_path,
            "sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
        }
        inputs.append(checkpoint_input)
        new_payload = {**payload, "report": revised, "classification_signature": classification_signature,
                       "postprocessing": {"policy": RECLASSIFICATION_POLICY, "input": checkpoint_input,
                                          "replayed": False, "changed_by_method": changes}}
        checkpoints.append((checkpoint_path.name, new_payload))
    if not reports:
        raise ValueError("Source report has no completed checkpoints")
    original_reports.sort(key=lambda row: row["capture_path"])
    reports.sort(key=lambda row: row["capture_path"])
    _, _, old_metadata = _merge_reports(original_reports, professions)
    for key, value in old_metadata.items():
        if value != baseline["metadata"].get(key):
            raise ValueError(f"Checkpoint totals do not reconcile with source report: {key}")
    summary, details, metadata = _merge_reports(reports, professions)
    provenance = {
        "policy": RECLASSIFICATION_POLICY,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_report": str(input_path),
        "input_report_sha256": hashlib.sha256(input_bytes).hexdigest(),
        "checkpoint_inputs": inputs,
        "replayed": False, "capture_files_opened": 0,
        "physical_summaries_preserved": len(reports),
        "scope_actor_job_skill_and_damage_unchanged": True,
        "changed_by_method": {method: dict(values) for method, values in sorted(changed.items())},
        "original_totals": old_metadata["totals"],
        "source_analysis_signature": baseline["analysis_signature"],
        "classification_signature": classification_signature,
        "old_gate_sha256": baseline["analysis_signature"].get("files", {}).get("tools/audit_direct_damage_release_gate.py"),
        "new_gate_sha256": classification_signature["files"]["audit_direct_damage_release_gate.py"],
        "reason": "Withdraw unconditional deterministic exemption for the four mixed owner methods; preserve the original replay population and physical summaries.",
    }
    result = copy.deepcopy(baseline)
    result.update({
        "schema_version": SCHEMA_VERSION,
        "result_origin": "checkpoint_method_reclassification",
        "classification_signature": classification_signature,
        "postprocessing": provenance,
        "summary": summary, "details": details, "metadata": metadata,
        "capture_manifest": [{key: report[key] for key in (
            "capture_path", *physical_keys, "profession_summary",
        )} for report in reports],
    })
    result["policy"] = {**result.get("policy", {}),
                        "mixed_owner_methods_are_nondeterministic": sorted(MIXED_OWNER_METHODS),
                        "postprocessing_scope": "Only deterministic mixed-owner groups downgraded; all other rows unchanged"}
    # Validate all source records before creating any output. These files keep
    # the old analysis signature plus explicit postprocessing metadata; the
    # normal replay checkpoint loader must never reuse them as a fresh replay.
    output_dir.mkdir(parents=True, exist_ok=False)
    migrated_dir = output_dir / "profession_direct_damage_checkpoints"
    migrated_dir.mkdir()
    for name, payload in checkpoints:
        (migrated_dir / name).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _write_csv(output_dir / "profession_direct_damage_summary.csv", summary, list(summary[0]))
    _write_csv(output_dir / "profession_direct_damage_details.csv", details, list(details[0]) if details else ["职业", "归因分类", "method", "Hit数", "伤害"])
    (output_dir / "profession_direct_damage_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    _write_markdown(output_dir / "profession_direct_damage_report.md",
                    capture_count=len(reports), duplicate_count=len(baseline.get("duplicates", [])),
                    failures=baseline.get("failures", []), summary_rows=summary, detail_rows=details,
                    metadata=metadata, postprocessing=provenance)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按30个二转职业审计正向直接Hit的严格技能归因缺口。"
    )
    parser.add_argument(
        "captures",
        nargs="*",
        type=Path,
        help=".dpscap 文件或目录；省略时扫描默认诊断目录。",
    )
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--reclassify-checkpoints-from", type=Path,
                        help="仅降级旧输出目录 checkpoint 中的四类混合 owner method；不读抓包、不重放。")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--no-checkpoints", action="store_true")
    args = parser.parse_args()

    if args.reclassify_checkpoints_from is not None:
        if args.captures:
            parser.error("checkpoint 重分类不能同时指定 captures")
        source_dir = args.reclassify_checkpoints_from.resolve()
        output_dir = args.output_dir or source_dir.with_name(source_dir.name + "_strict_owner_reclassified")
        try:
            result = reclassify_checkpoints(source_dir, output_dir)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(str(exc))
        print(json.dumps({"output_dir": str(output_dir.resolve()),
                          "captures": result["successful_captures"],
                          "replayed": False, "totals": result["metadata"]["totals"],
                          "changed_by_method": result["postprocessing"]["changed_by_method"]},
                         ensure_ascii=False), flush=True)
        return 0

    professions = _second_jobs()
    discovered = _discover_captures(args.captures, args.capture_root)
    captures, duplicates = _deduplicate_captures(discovered)
    if not captures:
        parser.error("没有找到 .dpscap 文件")

    output_dir = (args.output_dir or DEFAULT_OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_dir / "profession_direct_damage_checkpoints"
    signature = _analysis_signature()
    reports: list[dict[str, Any]] = []
    pending: list[Path] = []
    for path in captures:
        report = None
        if not args.no_checkpoints:
            report = _load_checkpoint(
                _checkpoint_path(checkpoint_dir, path),
                source_path=path,
                signature=signature,
            )
        if report is None:
            pending.append(path)
        else:
            reports.append(report)

    failures: list[dict[str, str]] = []
    workers = max(1, min(8, int(args.workers)))
    completed = len(reports)
    if pending:
        # Replay decoders retain sizeable per-capture protocol state.  Use
        # short executor batches instead of one long-lived pool: this releases
        # each worker after at most two captures without relying on
        # ``max_tasks_per_child`` (which can stall after the first recycle on
        # some Windows/Python 3.12 builds).
        batch_size = max(1, workers * 2)
        for batch_start in range(0, len(pending), batch_size):
            batch = pending[batch_start : batch_start + batch_size]
            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _audit_capture, str(path), tuple(professions)
                    ): path
                    for path in batch
                }
                for future in as_completed(futures):
                    path = futures[future]
                    try:
                        report = future.result()
                    except Exception as exc:  # noqa: BLE001 - audit must continue
                        failures.append({"path": str(path), "error": repr(exc)})
                    else:
                        reports.append(report)
                        if not args.no_checkpoints:
                            _write_checkpoint(
                                _checkpoint_path(checkpoint_dir, path),
                                source_path=path,
                                signature=signature,
                                report=report,
                            )
                    completed += 1
                    print(
                        f"[{completed}/{len(captures)}] {path.name}",
                        flush=True,
                    )

    reports.sort(key=lambda row: str(row.get("capture_path", "")))
    if _analysis_signature()["sha256"] != signature["sha256"]:
        raise RuntimeError("审计期间代码或规则数据发生变化，不能把混合输入的结果当作同一冻结版本。检查点已保留；请冻结输入后重新运行。")
    summary_rows, detail_rows, metadata = _merge_reports(reports, professions)
    summary_path = output_dir / "profession_direct_damage_summary.csv"
    detail_path = output_dir / "profession_direct_damage_details.csv"
    transition_path = output_dir / "profession_direct_damage_skill_transitions.csv"
    json_path = output_dir / "profession_direct_damage_report.json"
    markdown_path = output_dir / "profession_direct_damage_report.md"

    _write_csv(summary_path, summary_rows, list(summary_rows[0]))
    detail_fields = list(detail_rows[0]) if detail_rows else [
        "职业",
        "归因分类",
        "method",
        "skill_id",
        "技能名",
        "object模板",
        "Hit数",
        "伤害",
        "抓包数",
        "示例抓包",
    ]
    _write_csv(detail_path, detail_rows, detail_fields)
    transition_rows = _skill_transition_rows(metadata)
    transition_fields = list(transition_rows[0]) if transition_rows else [
        "职业",
        "原skill_id",
        "原技能名",
        "原method",
        "确认skill_id",
        "确认技能名",
        "确认method",
        "Hit数",
        "伤害",
        "抓包数",
        "示例抓包",
    ]
    _write_csv(transition_path, transition_rows, transition_fields)
    result = {
        "schema_version": SCHEMA_VERSION,
        "analysis_signature": signature,
        "policy": {
            "scope": "positive outgoing direct hits with real wire-hit evidence",
            "excluded": [
                "healing and incoming damage",
                "non-positive rows",
                "status/periodic damage",
                "supplemental:/periodic: buckets",
                "direct_hit_overflow",
                "突袭/冲击流 additional damage",
                "rows without has_real_hit_evidence=True",
            ],
            "deterministic": (
                "release-gate deterministic fixed packet field, packet-validated "
                "object bridge, or explicit entity owner only"
            ),
            "nondeterministic": (
                "named skill retained by inference such as result-group, timing "
                "window, recent action, learned profile, or candidate selection"
            ),
            "unattributed": "missing/placeholder skill identity or unresolved method",
            "mixed_owner_methods_are_nondeterministic": sorted(MIXED_OWNER_METHODS),
        },
        "discovered_captures": len(discovered),
        "unique_captures": len(captures),
        "successful_captures": len(reports),
        "duplicates": duplicates,
        "failures": failures,
        "professions": professions,
        "metadata": metadata,
        "summary": summary_rows,
        "details": detail_rows,
        "capture_manifest": [{key: report.get(key) for key in (
            "capture_path", "capture_sha256", "decoded_events",
            "raw_source_invariants", "aggregated_invariants", "profession_summary",
            "named_direct_candidates", "details", "skill_transitions",
        )} for report in reports],
    }
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(
        markdown_path,
        capture_count=len(reports),
        duplicate_count=len(duplicates),
        failures=failures,
        summary_rows=summary_rows,
        detail_rows=detail_rows,
        metadata=metadata,
    )

    total = int(metadata["totals"].get("total_direct_hits", 0))
    deterministic = int(metadata["totals"].get("deterministic_hits", 0))
    print(
        json.dumps(
            {
                "captures": len(reports),
                "failures": len(failures),
                "total_direct_hits": total,
                "deterministic_hits": deterministic,
                "coverage_percent": round(
                    deterministic / max(1, total) * 100.0, 6
                ),
                "summary": str(summary_path),
                "details": str(detail_path),
                "report": str(markdown_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
