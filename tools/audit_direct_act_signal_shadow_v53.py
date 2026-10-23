"""Shadow-audit client ACT type=4 direct-damage signal windows.

Candidate selection is deliberately independent from the existing damage
attribution result.  A candidate can only be produced by:

1. a reliable :class:`SkillCastEvent` for the same wire source actor; and
2. the Hit timestamp falling in an exact type=4 start/end frame interval from
   the extracted client ACT catalog for that same skill.

Existing inferred skill names and damage amounts are never read to choose a
candidate.  Independently deterministic object/entity/status attribution is
used after candidate selection only as truth.  This tool is offline/read-only
with respect to production code and score data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from hashlib import sha256
import json
import math
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
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
    _LIVE_DAMAGE_SKILL_ALIASES,
)


DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_CATALOG = ROOT / "outputs/damage_attribution/attack_instance_catalog_v576.json"
DEFAULT_JOB_MAP = ROOT / "dragon_nest_builder/data/modular/tables/job_map.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/direct_act_signal_shadow_v53_20260909"
DEFAULT_FOCUS_SKILLS = (3202, 3218, 3207, 3203, 3402, 5205, 5203, 5207, 5303, 6201)

# Compact input proves a button press, not that the accepted animation reached
# its damage frames. inferred_combo is synthetic.  Neither may create a v53
# candidate.
RELIABLE_CAST_KINDS = frozenset(
    {"skill_action", "skill_cast", "status_skill_action", "structured_multi_action"}
)
PRIMARY_CAST_KINDS = frozenset({"skill_action", "skill_cast"})

# Candidate construction never reads these labels.  ``packet_skill_id`` is an
# independent truth for this ACT-timing audit because v53 is not auditing the
# native result slot itself; it is testing a separate cast+ACT-time candidate.
INDEPENDENT_TRUTH_METHODS = frozenset(
    {
        "damage_object_projectile_link",
        "packet_skill_id",
        "packet_entity_skill_id",
        "packet_entity_projectile_selector",
        "packet_entity_self_attack",
        "packet_entity_template_owner",
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
    }
)

PRODUCTION_FILES = (
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/models.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/sources.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
)


@dataclass(frozen=True, slots=True)
class ActSpec:
    raw_skill_id: int
    canonical_skill_id: int
    name: str
    job: str
    intervals_ms: tuple[tuple[float, float], ...]

    @property
    def max_end_ms(self) -> float:
        return max((end for _start, end in self.intervals_ms), default=0.0)


@dataclass(frozen=True, slots=True)
class CastWindow:
    actor_id: int
    raw_skill_id: int
    canonical_skill_id: int
    timestamp_ms: int
    packet_kind: str
    event_job: str
    spec_job: str
    name: str
    intervals_ms: tuple[tuple[float, float], ...]
    sequence: int

    @property
    def max_end_ms(self) -> float:
        return max((end for _start, end in self.intervals_ms), default=0.0)


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    trim_frames: int = 0
    accepted_kinds: frozenset[str] = RELIABLE_CAST_KINDS
    require_single_cast: bool = False
    family: str = "both"
    object_rule: str = "any"


CONDITIONS = (
    Condition("exact_unique_skill"),
    Condition("exact_single_cast", require_single_cast=True),
    Condition("exact_primary_unique_skill", accepted_kinds=PRIMARY_CAST_KINDS),
    Condition(
        "exact_primary_single_cast",
        accepted_kinds=PRIMARY_CAST_KINDS,
        require_single_cast=True,
    ),
    Condition("inner_1f_unique_skill", trim_frames=1),
    Condition("inner_2f_unique_skill", trim_frames=2),
    Condition("exact_direct37_unique_skill", family="direct37"),
    Condition("exact_direct41_unique_skill", family="direct41"),
    Condition("exact_objectless_unique_skill", object_rule="objectless"),
    Condition("exact_objectful_unique_skill", object_rule="objectful"),
    Condition(
        "inner_1f_primary_single_cast",
        trim_frames=1,
        accepted_kinds=PRIMARY_CAST_KINDS,
        require_single_cast=True,
    ),
    Condition(
        "inner_2f_primary_single_cast",
        trim_frames=2,
        accepted_kinds=PRIMARY_CAST_KINDS,
        require_single_cast=True,
    ),
)


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _production_signature() -> dict[str, str]:
    return {name: _hash_file(ROOT / name) for name in PRODUCTION_FILES}


def _actor_id(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except ValueError:
        return 0


def _positive_skill(value: object) -> int:
    text = str(value or "").strip()
    if not text.isdecimal():
        return 0
    result = int(text)
    return result if result > 0 else 0


def _canonical(skill_id: int, canonical_by_raw: dict[int, int]) -> int:
    value = int(canonical_by_raw.get(skill_id, skill_id))
    return int(_LIVE_DAMAGE_SKILL_ALIASES.get(value, value))


def _merge_intervals(
    intervals: Iterable[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    rows = sorted((max(0.0, float(a)), max(0.0, float(b))) for a, b in intervals)
    merged: list[list[float]] = []
    for start, end in rows:
        if end < start:
            start, end = end, start
        if not merged or start > merged[-1][1] + 0.001:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((start, end) for start, end in merged)


def _load_specs(path: Path) -> tuple[dict[int, ActSpec], dict[int, int]]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    specs: dict[int, ActSpec] = {}
    canonical_by_raw: dict[int, int] = {}
    for row in catalog.get("skills", ()):
        raw_skill = int(row.get("skill_id", 0) or 0)
        canonical = int(row.get("canonical_skill_id", raw_skill) or raw_skill)
        if raw_skill <= 0:
            continue
        canonical_by_raw[raw_skill] = canonical
        segments = row.get("direct_damage_segments", ())
        intervals = _merge_intervals(
            (
                1000.0 * float(segment.get("start_frame", 0) or 0) / 60.0,
                1000.0 * float(segment.get("end_frame", 0) or 0) / 60.0,
            )
            for segment in segments
        )
        if not intervals:
            continue
        specs[raw_skill] = ActSpec(
            raw_skill_id=raw_skill,
            canonical_skill_id=canonical,
            name=str(row.get("name", "")),
            job=str(row.get("job", "")),
            intervals_ms=intervals,
        )
    return specs, canonical_by_raw


def _load_job_parents(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(second): str(row.get("一转分支", ""))
        for second, row in value.items()
        if str(second) and isinstance(row, dict)
    }


def _job_compatible(event_job: str, spec_job: str, parents: dict[str, str]) -> bool:
    """Reject cross-class numeric Skill-ID collisions using static job lineage."""
    event_job = str(event_job or "").strip()
    spec_job = str(spec_job or "").strip()
    if not event_job or not spec_job:
        return False
    if event_job == spec_job:
        return True
    spec_parent = parents.get(spec_job, "")
    if event_job == spec_parent:
        return True
    event_parent = parents.get(event_job, "")
    return bool(event_parent and spec_parent and event_parent == spec_parent)


def _selection(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    captures = [
        {"name": str(row["capture"]), "sha256": str(row["capture_sha256"])}
        for row in value["sources"]["selected_results"]
    ]
    under95 = {str(row["job"]): dict(row) for row in value.get("jobs", ())}
    if len(captures) != 35 or len(under95) != 11:
        raise ValueError(
            f"fixed audit contract changed: captures={len(captures)}, jobs={len(under95)}"
        )
    return captures, under95


def _find_captures(capture_root: Path, selected: Iterable[dict[str, str]]) -> list[Path]:
    wanted = {row["name"]: row["sha256"] for row in selected}
    found: dict[str, list[Path]] = defaultdict(list)
    for path in capture_root.rglob("*.dpscap"):
        if path.name in wanted:
            found[path.name].append(path)
    result: list[Path] = []
    for name, expected_hash in wanted.items():
        matches = [path for path in found.get(name, ()) if _hash_file(path) == expected_hash]
        if len(matches) != 1:
            raise ValueError(f"expected one hash-matched capture for {name}, got {len(matches)}")
        result.append(matches[0])
    return result


def _family(event: CombatEvent) -> str:
    layout = str(event.wire_damage_layout or "").casefold()
    if "direct37" in layout:
        return "direct37"
    if "direct41" in layout:
        return "direct41"
    return ""


def _trimmed_contains(cast: CastWindow, timestamp_ms: int, trim_frames: int) -> bool:
    age_ms = float(timestamp_ms - cast.timestamp_ms)
    trim_ms = 1000.0 * trim_frames / 60.0
    for start, end in cast.intervals_ms:
        left = start + trim_ms
        right = end - trim_ms
        if left > right:
            midpoint = (start + end) / 2.0
            left = right = midpoint
        if left - 0.001 <= age_ms <= right + 0.001:
            return True
    return False


def _candidate(
    *,
    condition: Condition,
    family: str,
    object_id: int,
    timestamp_ms: int,
    casts: Iterable[CastWindow],
) -> tuple[CastWindow | None, str, int]:
    if condition.family != "both" and condition.family != family:
        return None, "family_rejected", 0
    objectful = object_id not in {0, 0xFFFFFFFF}
    if condition.object_rule == "objectless" and objectful:
        return None, "object_scope_rejected", 0
    if condition.object_rule == "objectful" and not objectful:
        return None, "object_scope_rejected", 0
    matching = [
        cast
        for cast in casts
        if cast.packet_kind in condition.accepted_kinds
        and _trimmed_contains(cast, timestamp_ms, condition.trim_frames)
    ]
    if not matching:
        return None, "outside_act_signal", 0
    canonical_ids = {cast.canonical_skill_id for cast in matching}
    if len(canonical_ids) != 1:
        return None, "ambiguous_skills", len(matching)
    if condition.require_single_cast and len(matching) != 1:
        return None, "ambiguous_casts", len(matching)
    # Same-skill overlapping cast variants remain the same candidate for the
    # unique-skill condition.  The newest matching cast is retained only for
    # diagnostics; it does not alter the selected skill.
    return max(matching, key=lambda row: (row.timestamp_ms, row.sequence)), "candidate", len(matching)


def _wilson_lower(matches: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = matches / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return max(0.0, (centre - radius) / denominator)


def _percent(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 6) if denominator else 0.0


def _counter_row(counter: Counter[str]) -> dict[str, Any]:
    # JSON must contain explicit zeros.  A clean candidate can legitimately
    # have no ``truth_conflicts`` key in Counter, and report generation must
    # not turn that success case into a KeyError.
    values = dict(counter)
    for key in (
        "candidate_hits",
        "already_deterministic_hits",
        "potential_new_hits",
        "named_gap_hits",
        "unknown_gap_hits",
        "truth_hits",
        "truth_matches",
        "truth_conflicts",
        "unverified_hits",
        "truth_population",
        "truth_captured",
        "candidate_capture_count",
        "truth_capture_count",
    ):
        values.setdefault(key, 0)
    truth = int(values["truth_hits"])
    matches = int(values["truth_matches"])
    return {
        **values,
        "precision_percent": _percent(matches, truth),
        "wilson95_lower_percent": round(100.0 * _wilson_lower(matches, truth), 6),
        "truth_recall_percent": _percent(
            int(values["truth_captured"]), int(values["truth_population"])
        ),
    }


def audit_capture(
    path_text: str,
    catalog_text: str,
    job_map_text: str,
    focus_skills: tuple[int, ...],
) -> dict[str, Any]:
    path = Path(path_text)
    catalog_path = Path(catalog_text)
    specs, canonical_by_raw = _load_specs(catalog_path)
    job_parents = _load_job_parents(Path(job_map_text))
    canonical_specs: dict[int, ActSpec] = {}
    for spec in specs.values():
        canonical_specs.setdefault(_canonical(spec.canonical_skill_id, canonical_by_raw), spec)

    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    active: dict[int, deque[CastWindow]] = defaultdict(deque)
    sequence = 0
    packet_sequence = 0
    seen_hit_rows: set[tuple[int, int, int, int, int, str]] = set()
    summary: Counter[str] = Counter()
    cast_kinds: Counter[str] = Counter()
    condition_totals: dict[str, Counter[str]] = defaultdict(Counter)
    condition_jobs: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    condition_skills: dict[tuple[str, str, int], Counter[str]] = defaultdict(Counter)
    condition_layouts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    truth_population: Counter[tuple[str, int]] = Counter()
    truth_capture: dict[tuple[str, int], set[str]] = defaultdict(set)
    condition_truth_captured: Counter[tuple[str, str, int]] = Counter()
    candidate_capture: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    conflict_examples: list[dict[str, Any]] = []

    def observe_counter(
        counter: Counter[str],
        *,
        truth: int,
        candidate: int,
        deterministic: bool,
        assigned: int,
    ) -> None:
        counter["candidate_hits"] += 1
        if deterministic:
            counter["already_deterministic_hits"] += 1
        else:
            counter["potential_new_hits"] += 1
            if assigned:
                counter["named_gap_hits"] += 1
            else:
                counter["unknown_gap_hits"] += 1
        if truth:
            counter["truth_hits"] += 1
            if truth == candidate:
                counter["truth_matches"] += 1
            else:
                counter["truth_conflicts"] += 1
        else:
            counter["unverified_hits"] += 1

    def feed_events(packet):
        nonlocal sequence, packet_sequence
        packet_sequence += 1
        events = original(packet)
        for event in events:
            if isinstance(event, SkillCastEvent):
                summary["skill_cast_events"] += 1
                cast_kinds[str(event.packet_kind)] += 1
                if event.packet_kind not in RELIABLE_CAST_KINDS:
                    summary["rejected_cast_kind"] += 1
                    continue
                actor = _actor_id(event.source_id)
                raw_skill = _positive_skill(event.skill_id)
                spec = specs.get(raw_skill)
                if actor <= 0x00FFFFFF or spec is None:
                    summary["cast_without_direct_act_spec"] += 1
                    continue
                if not _job_compatible(str(event.job or ""), spec.job, job_parents):
                    summary["cast_job_incompatible_with_act_spec"] += 1
                    continue
                sequence += 1
                active[actor].append(
                    CastWindow(
                        actor_id=actor,
                        raw_skill_id=raw_skill,
                        canonical_skill_id=_canonical(spec.canonical_skill_id, canonical_by_raw),
                        timestamp_ms=int(event.timestamp_ms),
                        packet_kind=str(event.packet_kind),
                        event_job=str(event.job or ""),
                        spec_job=spec.job,
                        name=spec.name,
                        intervals_ms=spec.intervals_ms,
                        sequence=sequence,
                    )
                )
                summary["accepted_direct_act_casts"] += 1
                continue
            if not isinstance(event, CombatEvent) or event.healing:
                continue
            family = _family(event)
            if not family or event.has_real_hit_evidence is False:
                continue
            actor = int(event.wire_source_actor_id)
            if actor <= 0x00FFFFFF:
                summary["non_player_direct_hits"] += 1
                continue
            dedup_key = (
                packet_sequence,
                int(event.wire_record_offset),
                actor,
                int(event.wire_target_actor_id),
                int(event.wire_damage_object_id),
                family,
            )
            if dedup_key in seen_hit_rows:
                summary["duplicate_decoded_events"] += 1
                continue
            seen_hit_rows.add(dedup_key)
            summary["player_direct_hits"] += 1
            summary[f"player_{family}_hits"] += 1

            queue = active.get(actor)
            if queue is None:
                queue = deque()
            # Retain only casts whose latest exact ACT signal has not ended.
            # No inferred tail or generic action window is used.
            timestamp_ms = int(event.timestamp_ms)
            while queue and timestamp_ms > queue[0].timestamp_ms + queue[0].max_end_ms + 0.001:
                queue.popleft()

            # Build every candidate before reading the existing assigned skill
            # or attribution method.  This keeps truth physically incapable of
            # influencing candidate selection.
            selected_candidates: list[tuple[Condition, CastWindow, int]] = []
            for condition in CONDITIONS:
                candidate, reason, overlap = _candidate(
                    condition=condition,
                    family=family,
                    object_id=int(event.wire_damage_object_id),
                    timestamp_ms=timestamp_ms,
                    casts=queue,
                )
                totals = condition_totals[condition.name]
                totals[reason] += 1
                if candidate is None:
                    continue
                selected_candidates.append((condition, candidate, overlap))

            method = str(event.attribution_method or "").casefold()
            assigned = _positive_skill(event.skill_id)
            deterministic = is_deterministic_attribution(method)
            truth = (
                _canonical(assigned, canonical_by_raw)
                if method in INDEPENDENT_TRUTH_METHODS and assigned
                else 0
            )
            job = str(event.job or "未识别")
            if truth and truth in canonical_specs:
                truth_population[(job, truth)] += 1
                truth_capture[(job, truth)].add(path.name)

            for condition, candidate, overlap in selected_candidates:
                totals = condition_totals[condition.name]
                candidate_id = candidate.canonical_skill_id
                if overlap > 1:
                    totals["same_skill_overlapping_casts"] += 1
                observe_counter(
                    totals,
                    truth=truth,
                    candidate=candidate_id,
                    deterministic=deterministic,
                    assigned=assigned,
                )
                skill_counter = condition_skills[(condition.name, job, candidate_id)]
                observe_counter(
                    skill_counter,
                    truth=truth,
                    candidate=candidate_id,
                    deterministic=deterministic,
                    assigned=assigned,
                )
                observe_counter(
                    condition_jobs[(condition.name, job)],
                    truth=truth,
                    candidate=candidate_id,
                    deterministic=deterministic,
                    assigned=assigned,
                )
                observe_counter(
                    condition_layouts[(condition.name, family)],
                    truth=truth,
                    candidate=candidate_id,
                    deterministic=deterministic,
                    assigned=assigned,
                )
                candidate_capture[(condition.name, job, candidate_id)].add(path.name)
                if truth and truth == candidate_id:
                    condition_truth_captured[(condition.name, job, truth)] += 1
                elif truth and len(conflict_examples) < 80:
                    conflict_examples.append(
                        {
                            "capture": path.name,
                            "condition": condition.name,
                            "timestamp_ms": timestamp_ms,
                            "actor_id": f"0x{actor:08X}",
                            "family": family,
                            "damage_object_id": int(event.wire_damage_object_id),
                            "candidate_skill_id": candidate_id,
                            "candidate_skill_name": candidate.name,
                            "candidate_cast_kind": candidate.packet_kind,
                            "candidate_age_ms": timestamp_ms - candidate.timestamp_ms,
                            "truth_skill_id": truth,
                            "truth_method": method,
                        }
                    )
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    skill_rows: list[dict[str, Any]] = []
    for (condition, job, skill_id), counter in sorted(condition_skills.items()):
        counter["truth_population"] = truth_population[(job, skill_id)]
        counter["truth_captured"] = condition_truth_captured[(condition, job, skill_id)]
        counter["candidate_capture_count"] = len(candidate_capture[(condition, job, skill_id)])
        counter["truth_capture_count"] = len(truth_capture[(job, skill_id)])
        spec = canonical_specs.get(skill_id)
        skill_rows.append(
            {
                "condition": condition,
                "job": job,
                "skill_id": skill_id,
                "skill_name": spec.name if spec else "",
                "focus": skill_id in focus_skills,
                **_counter_row(counter),
            }
        )
    return {
        "capture": path.name,
        "capture_sha256": _hash_file(path),
        "summary": {**dict(summary), "cast_kinds": dict(cast_kinds)},
        "conditions": {
            name: _counter_row(counter)
            for name, counter in sorted(condition_totals.items())
        },
        "jobs": [
            {"condition": condition, "job": job, **_counter_row(counter)}
            for (condition, job), counter in sorted(condition_jobs.items())
        ],
        "skills": skill_rows,
        "layouts": [
            {"condition": condition, "family": family, **_counter_row(counter)}
            for (condition, family), counter in sorted(condition_layouts.items())
        ],
        "truth_population": [
            {
                "job": job,
                "skill_id": skill_id,
                "truth_population": count,
                "truth_capture_count": len(truth_capture[(job, skill_id)]),
            }
            for (job, skill_id), count in sorted(truth_population.items())
        ],
        "conflict_examples": conflict_examples,
    }


def _update_numeric(counter: Counter[str], values: dict[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(value, int) and key not in {
            "candidate_capture_count",
            "truth_capture_count",
        }:
            counter[key] += value


def aggregate(
    reports: list[dict[str, Any]],
    specs: dict[int, ActSpec],
    canonical_by_raw: dict[int, int],
    under95: dict[str, dict[str, Any]],
    focus_skills: tuple[int, ...],
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    cast_kinds: Counter[str] = Counter()
    conditions: dict[str, Counter[str]] = defaultdict(Counter)
    jobs: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    skills: dict[tuple[str, str, int], Counter[str]] = defaultdict(Counter)
    layouts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    truth_population: Counter[tuple[str, int]] = Counter()
    candidate_captures: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    truth_captures: dict[tuple[str, int], set[str]] = defaultdict(set)
    conflicts: list[dict[str, Any]] = []
    for report in reports:
        _update_numeric(totals, report["summary"])
        cast_kinds.update(report["summary"].get("cast_kinds", {}))
        for name, row in report["conditions"].items():
            _update_numeric(conditions[name], row)
        for row in report["jobs"]:
            _update_numeric(jobs[(row["condition"], row["job"])], row)
        for row in report["skills"]:
            key = (row["condition"], row["job"], int(row["skill_id"]))
            _update_numeric(skills[key], row)
            if int(row.get("candidate_hits", 0)):
                candidate_captures[key].add(report["capture"])
        for row in report["layouts"]:
            _update_numeric(layouts[(row["condition"], row["family"])], row)
        for row in report["truth_population"]:
            key = (row["job"], int(row["skill_id"]))
            truth_population[key] += int(row["truth_population"])
            truth_captures[key].add(report["capture"])
        for row in report["conflict_examples"]:
            if len(conflicts) < 300:
                conflicts.append(row)

    canonical_specs: dict[int, ActSpec] = {}
    for spec in specs.values():
        canonical_specs.setdefault(_canonical(spec.canonical_skill_id, canonical_by_raw), spec)
    skill_rows: list[dict[str, Any]] = []
    strict_candidates: list[dict[str, Any]] = []
    for (condition, job, skill_id), counter in sorted(skills.items()):
        counter["truth_population"] = truth_population[(job, skill_id)]
        counter["truth_captured"] = counter["truth_matches"]
        counter["candidate_capture_count"] = len(candidate_captures[(condition, job, skill_id)])
        counter["truth_capture_count"] = len(truth_captures[(job, skill_id)])
        spec = canonical_specs.get(skill_id)
        row = {
            "condition": condition,
            "job": job,
            "skill_id": skill_id,
            "skill_name": spec.name if spec else "",
            "focus": skill_id in focus_skills,
            **_counter_row(counter),
        }
        skill_rows.append(row)
        if (
            row["truth_conflicts"] == 0
            and row["truth_hits"] >= 73
            and row["wilson95_lower_percent"] >= 95.0
            and row["truth_capture_count"] >= 2
            and row["candidate_capture_count"] >= 2
            and row["potential_new_hits"] > 0
        ):
            strict_candidates.append(dict(row))

    strict_candidates.sort(
        key=lambda row: (
            -int(row["potential_new_hits"]),
            -int(row["truth_hits"]),
            str(row["job"]),
            int(row["skill_id"]),
            str(row["condition"]),
        )
    )
    under95_projection: list[dict[str, Any]] = []
    for condition in (row.name for row in CONDITIONS):
        for job, baseline in under95.items():
            accepted = [
                row
                for row in strict_candidates
                if row["condition"] == condition and row["job"] == job
            ]
            named_gap = sum(int(row.get("named_gap_hits", 0)) for row in accepted)
            unknown_gap = sum(int(row.get("unknown_gap_hits", 0)) for row in accepted)
            deterministic = int(baseline["deterministic_hits"])
            named_population = int(baseline["named_population_hits"])
            projected_numerator = deterministic + named_gap + unknown_gap
            projected_denominator = named_population + unknown_gap
            projected = _percent(projected_numerator, projected_denominator)
            remaining = max(
                0,
                math.ceil(0.95 * projected_denominator) - projected_numerator,
            )
            under95_projection.append(
                {
                    "condition": condition,
                    "job": job,
                    "baseline_named_coverage_percent": float(
                        baseline["named_coverage_percent"]
                    ),
                    "baseline_hits_needed_for_95": int(
                        baseline["hits_needed_for_95"]
                    ),
                    "accepted_skill_ids": [int(row["skill_id"]) for row in accepted],
                    "strict_named_gap_hits": named_gap,
                    "strict_unknown_gap_hits": unknown_gap,
                    "strict_total_new_hits": named_gap + unknown_gap,
                    "projected_named_coverage_percent": projected,
                    "remaining_hits_for_95": remaining,
                    "strict_rule_can_fill_to_95": projected >= 95.0,
                }
            )
    best_under95_projection: list[dict[str, Any]] = []
    for job in under95:
        rows = [row for row in under95_projection if row["job"] == job]
        best_under95_projection.append(
            max(
                rows,
                key=lambda row: (
                    float(row["projected_named_coverage_percent"]),
                    int(row["strict_total_new_hits"]),
                    str(row["condition"]),
                ),
            )
        )
    focus_rows: list[dict[str, Any]] = []
    for skill_id in focus_skills:
        canonical = _canonical(skill_id, canonical_by_raw)
        rows = [
            row for row in skill_rows
            if row["skill_id"] == canonical and row["condition"] == "exact_unique_skill"
        ]
        if rows:
            focus_rows.extend(rows)
        else:
            spec = canonical_specs.get(canonical)
            focus_rows.append(
                {
                    "condition": "exact_unique_skill",
                    "job": spec.job if spec else "",
                    "skill_id": canonical,
                    "skill_name": spec.name if spec else "",
                    "focus": True,
                    "candidate_hits": 0,
                    "truth_hits": 0,
                    "truth_matches": 0,
                    "truth_conflicts": 0,
                    "potential_new_hits": 0,
                    "truth_population": 0,
                    "truth_captured": 0,
                    "precision_percent": 0.0,
                    "wilson95_lower_percent": 0.0,
                    "truth_recall_percent": 0.0,
                }
            )
    return {
        "totals": {**dict(totals), "cast_kinds": dict(sorted(cast_kinds.items()))},
        "conditions": [
            {"condition": name, **_counter_row(counter)}
            for name, counter in sorted(conditions.items())
        ],
        "jobs": [
            {
                "condition": condition,
                "job": job,
                "under95_job": job in under95,
                **_counter_row(counter),
            }
            for (condition, job), counter in sorted(jobs.items())
        ],
        "skills": skill_rows,
        "focus_skills": focus_rows,
        "strict_zero_conflict_high_support": strict_candidates,
        "under95_projection_by_condition": under95_projection,
        "under95_best_strict_projection": best_under95_projection,
        "layouts": [
            {"condition": condition, "family": family, **_counter_row(counter)}
            for (condition, family), counter in sorted(layouts.items())
        ],
        "conflict_examples": conflicts,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    exact = next(
        row for row in summary["conditions"] if row["condition"] == "exact_unique_skill"
    )
    lines = [
        "# 客户端 ACT type=4 直接伤害段影子审计（v53）",
        "",
        "## 审计结论",
        "",
        f"固定回放 **{report['capture_count']}** 份抓包。基础严格条件产生 "
        f"**{int(exact.get('candidate_hits', 0)):,}** 个候选 Hit；其中有独立确定性真值 "
        f"**{int(exact.get('truth_hits', 0)):,}** 个，匹配 **{int(exact.get('truth_matches', 0)):,}**，"
        f"冲突 **{int(exact.get('truth_conflicts', 0)):,}**。精确率 "
        f"**{float(exact.get('precision_percent', 0)):.4f}%**，95% Wilson 下界 "
        f"**{float(exact.get('wilson95_lower_percent', 0)):.4f}%**。",
        "",
        "候选仅使用可靠 SkillCastEvent、角色运行 ID、客户端 ACT type=4 原始 start/end frame 和 Hit 时间。"
        "不读取旧推断技能名、旧技能标签或伤害数值；独立对象/实体/状态链只在候选形成后验真。",
        "",
        "## 条件总览",
        "",
        "|条件|候选Hit|真值Hit|匹配|冲突|精确率|Wilson下界|潜在新增Hit|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["conditions"]:
        lines.append(
            f"|{row['condition']}|{int(row.get('candidate_hits', 0)):,}|"
            f"{int(row.get('truth_hits', 0)):,}|{int(row.get('truth_matches', 0)):,}|"
            f"{int(row.get('truth_conflicts', 0)):,}|{float(row.get('precision_percent', 0)):.3f}%|"
            f"{float(row.get('wilson95_lower_percent', 0)):.3f}%|"
            f"{int(row.get('potential_new_hits', 0)):,}|"
        )
    lines += [
        "",
        "## 指定重点技能（基础严格条件）",
        "",
        "|职业|技能ID|技能|候选Hit|真值Hit|匹配|冲突|精确率|Wilson下界|真值召回|潜在新增Hit|",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["focus_skills"]:
        lines.append(
            f"|{row.get('job', '')}|{row['skill_id']}|{row.get('skill_name', '')}|"
            f"{int(row.get('candidate_hits', 0)):,}|{int(row.get('truth_hits', 0)):,}|"
            f"{int(row.get('truth_matches', 0)):,}|{int(row.get('truth_conflicts', 0)):,}|"
            f"{float(row.get('precision_percent', 0)):.3f}%|"
            f"{float(row.get('wilson95_lower_percent', 0)):.3f}%|"
            f"{float(row.get('truth_recall_percent', 0)):.3f}%|"
            f"{int(row.get('potential_new_hits', 0)):,}|"
        )
    lines += [
        "",
        "## 零冲突、高支持严格条件",
        "",
        "入选门槛：零冲突、独立真值 Hit≥73、95% Wilson 下界≥95%、至少2份抓包有真值与候选，且存在潜在新增 Hit。",
        "",
        "|条件|职业|技能ID|技能|真值Hit|Wilson下界|真值召回|潜在新增Hit|抓包数|",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    strict = summary["strict_zero_conflict_high_support"]
    if strict:
        for row in strict:
            lines.append(
                f"|{row['condition']}|{row['job']}|{row['skill_id']}|{row.get('skill_name', '')}|"
                f"{int(row.get('truth_hits', 0)):,}|{float(row.get('wilson95_lower_percent', 0)):.3f}%|"
                f"{float(row.get('truth_recall_percent', 0)):.3f}%|"
                f"{int(row.get('potential_new_hits', 0)):,}|"
                f"{int(row.get('candidate_capture_count', 0))}|"
            )
    else:
        lines.append("|—|—|—|没有条件达到严格门槛|0|0%|0%|0|0|")
    lines += [
        "",
        "## 11个低于95%职业：最佳严格投影",
        "",
        "只计入上表已通过零冲突、高支持门槛的技能。旧命名缺口被转为确定性时分母不变；旧未知 Hit 新增命名时同时增加分子和分母。",
        "",
        "|职业|基线|最佳条件|通过技能|严格新增Hit|投影覆盖|仍缺Hit|可补足95%|",
        "|---|---:|---|---|---:|---:|---:|---|",
    ]
    for row in summary["under95_best_strict_projection"]:
        skills = ", ".join(str(value) for value in row["accepted_skill_ids"]) or "—"
        lines.append(
            f"|{row['job']}|{float(row['baseline_named_coverage_percent']):.3f}%|"
            f"{row['condition']}|{skills}|{int(row['strict_total_new_hits']):,}|"
            f"{float(row['projected_named_coverage_percent']):.3f}%|"
            f"{int(row['remaining_hits_for_95']):,}|"
            f"{('是' if row['strict_rule_can_fill_to_95'] else '否')}|"
        )
    lines += [
        "",
        "## 边界",
        "",
        "- ACT 帧按客户端资源的 60 fps 原始区间换算；未加入统一 2.5 秒窗口、网络宽限或伤害指纹。",
        "- 同一时刻若命中两个不同技能的 type=4 区间，拒绝候选；`single_cast` 条件连同技能多次施放重叠也拒绝。",
        "- `compact_skill_input`、`inferred_combo` 不属于可靠施放证据。",
        "- 本报告是 shadow 验证，不会自动把潜在新增 Hit 写入生产结果。",
        "- 逐技能、逐职业、direct37/direct41 分层和冲突样例见 JSON。",
        "",
        f"生产代码哈希前后一致：**{report['production_changed'] is False}**。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--attack-catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--job-map", type=Path, default=DEFAULT_JOB_MAP)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--focus-skill", action="append", type=int, default=[])
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        parser.error("workers must be 1 or 2; v53 must not compete with the main audit")

    signature_before = _production_signature()
    selection_path = args.selection.resolve()
    catalog_path = args.attack_catalog.resolve()
    job_map_path = args.job_map.resolve()
    selected, under95 = _selection(selection_path)
    captures = _find_captures(args.capture_root.resolve(), selected)
    focus_skills = tuple(args.focus_skill) if args.focus_skill else DEFAULT_FOCUS_SKILLS
    specs, canonical_by_raw = _load_specs(catalog_path)

    output_dir = args.output_dir.resolve()
    cache_dir = output_dir / ".capture_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalog_hash = _hash_file(catalog_path)
    tool_hash = _hash_file(Path(__file__).resolve())
    reports: dict[str, dict[str, Any]] = {}
    pending: list[Path] = []
    for path in captures:
        capture_hash = _hash_file(path)
        cache_path = cache_dir / (
            f"{capture_hash}_{catalog_hash[:16]}_{tool_hash[:16]}.json"
        )
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("capture_sha256") == capture_hash:
                reports[path.name] = cached
                print(f"[cache] {path.name}", flush=True)
                continue
        pending.append(path)

    def save_cache(path: Path, result: dict[str, Any]) -> None:
        capture_hash = str(result["capture_sha256"])
        cache_path = cache_dir / (
            f"{capture_hash}_{catalog_hash[:16]}_{tool_hash[:16]}.json"
        )
        cache_path.write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    if args.workers == 1:
        for index, path in enumerate(pending, 1):
            result = audit_capture(
                str(path), str(catalog_path), str(job_map_path), focus_skills
            )
            reports[path.name] = result
            save_cache(path, result)
            print(f"[{index}/{len(pending)}] {path.name}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    audit_capture,
                    str(path),
                    str(catalog_path),
                    str(job_map_path),
                    focus_skills,
                ): path
                for path in pending
            }
            for index, future in enumerate(as_completed(futures), 1):
                path = futures[future]
                result = future.result()
                reports[path.name] = result
                save_cache(path, result)
                print(f"[{index}/{len(pending)}] {path.name}", flush=True)

    ordered = [reports[row["name"]] for row in selected]
    for expected, actual in zip(selected, ordered, strict=True):
        if expected["sha256"] != actual["capture_sha256"]:
            raise ValueError(f"capture changed during replay: {actual['capture']}")
    signature_after = _production_signature()
    if signature_after != signature_before:
        raise ValueError("production files changed during v53 shadow audit")

    report: dict[str, Any] = {
        "schema": "direct-act-type4-signal-shadow-v53",
        "method": "offline replay; exact client ACT type=4 frame windows only",
        "candidate_contract": {
            "inputs": [
                "reliable SkillCastEvent actor/skill/timestamp/packet_kind",
                "same-skill ACT type=4 exact start_frame/end_frame at 60 fps",
                "direct37/direct41 Hit timestamp and wire source actor",
            ],
            "forbidden_candidate_inputs": [
                "existing inferred skill name or skill label",
                "damage amount/value/fingerprint",
                "independent deterministic truth skill",
                "generic 2.5 second action window",
            ],
            "reliable_cast_kinds": sorted(RELIABLE_CAST_KINDS),
            "rejected_cast_kinds": ["compact_skill_input", "inferred_combo"],
            "ambiguity_policy": "reject when more than one canonical skill matches",
        },
        "truth_contract": {
            "methods": sorted(INDEPENDENT_TRUTH_METHODS),
            "use": "validation only after candidate selection",
            "excluded": ["all inference routes"],
        },
        "strict_acceptance_contract": {
            "truth_conflicts": 0,
            "minimum_truth_hits": 73,
            "minimum_wilson95_lower_percent": 95.0,
            "minimum_truth_capture_count": 2,
            "minimum_candidate_capture_count": 2,
            "requires_potential_new_hits": True,
        },
        "capture_count": len(ordered),
        "capture_root": str(args.capture_root.resolve()),
        "selection": str(selection_path),
        "selection_sha256": _hash_file(selection_path),
        "attack_catalog": str(catalog_path),
        "attack_catalog_sha256": catalog_hash,
        "job_map": str(job_map_path),
        "job_map_sha256": _hash_file(job_map_path),
        "focus_skills": list(focus_skills),
        "under95_jobs": list(under95),
        "workers": args.workers,
        "tool": str(Path(__file__).resolve()),
        "production_signature_before": signature_before,
        "production_signature_after": signature_after,
        "production_changed": False,
        "summary": aggregate(
            ordered, specs, canonical_by_raw, under95, focus_skills
        ),
        "captures": ordered,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "direct_act_signal_shadow_v53.json"
    md_path = output_dir / "direct_act_signal_shadow_v53.md"
    # Record the actual tool hash after source is complete and before outputs.
    report["tool_sha256"] = tool_hash
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"]["conditions"][0], ensure_ascii=False, indent=2))
    print(str(json_path))
    print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
