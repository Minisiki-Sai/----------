"""Audit the client current-skill state against physical direct-Hit rows.

The native client installs a skill in ``MASkillUser`` and copies that skill's
raw key into newly-created damage objects.  This audit replays the fixed
capture corpus and asks a narrower question before production integration:
when a player's current action has a client ACT type-4 damage segment or a
typed Projectile signal, how often does it agree with an independently named
Hit?  Existing inferred names and damage magnitudes never establish truth.

This is a shadow audit.  It does not relabel events or change score output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_ATTACK_CATALOG = ROOT / "outputs/damage_attribution/attack_instance_catalog_v576.json"
DEFAULT_OUTPUT = ROOT / "outputs/native_wire_v52_20260909/current_skill_hit_audit.json"


def _capture_selection(path: Path) -> list[dict[str, str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"name": str(row["capture"]), "sha256": str(row["capture_sha256"])}
        for row in value["sources"]["selected_results"]
    ]


def _find_captures(
    capture_root: Path, selection: Iterable[dict[str, str]], names: set[str]
) -> list[Path]:
    wanted = {
        row["name"]: row["sha256"]
        for row in selection
        if not names or row["name"] in names
    }
    found: dict[str, list[Path]] = defaultdict(list)
    for path in capture_root.rglob("*.dpscap"):
        if path.name in wanted:
            found[path.name].append(path)
    result: list[Path] = []
    for name, expected in wanted.items():
        matches = [
            path for path in found.get(name, ())
            if sha256(path.read_bytes()).hexdigest() == expected
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one hash-matched capture for {name}, got {len(matches)}")
        result.append(matches[0])
    if names - set(wanted):
        raise ValueError(f"capture names absent from fixed selection: {sorted(names-set(wanted))}")
    return result


def _actor_id(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return 0


def _skill_specs(path: Path) -> dict[int, dict[str, Any]]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    result: dict[int, dict[str, Any]] = {}
    for row in catalog.get("skills", ()):
        skill_id = int(row.get("skill_id", 0) or 0)
        actions = list(row.get("matched_actions", ()))
        if skill_id <= 0 or not actions:
            continue
        nominal_frames = max(
            (int(action.get("length_frames", 0) or 0) for action in actions),
            default=0,
        )
        direct_segments = tuple(row.get("direct_damage_segments", ()))
        projectile_templates = tuple(
            int(value) for value in row.get("projectile_template_ids", ())
            if int(value) > 0
        )
        result[skill_id] = {
            "skill_id": skill_id,
            "canonical_skill_id": int(row.get("canonical_skill_id", skill_id) or skill_id),
            "job": str(row.get("job", "")),
            "name": str(row.get("name", "")),
            "classification": str(row.get("classification", "")),
            "nominal_seconds": nominal_frames / 60.0,
            "direct_segments": direct_segments,
            "direct_intervals": tuple(
                (
                    float(segment.get("start_frame", 0) or 0) / 60.0,
                    float(segment.get("end_frame", 0) or 0) / 60.0,
                )
                for segment in direct_segments
            ),
            "projectile_signal_times": tuple(
                float(signal.get("start_frame", 0) or 0) / 60.0
                for action in actions
                for signal in action.get("signals", ())
                if int(signal.get("type", 0) or 0) == 15
            ),
            "projectile_templates": projectile_templates,
            "has_direct_segments": bool(direct_segments),
            "has_projectile_signals": bool(projectile_templates),
        }
    return result


def _bucket(age: float) -> str:
    for upper in (0.12, 0.25, 0.5, 1.0, 1.5, 2.5, 5.0, 10.0, 15.0):
        if age <= upper:
            return f"<={upper:g}s"
    return ">15s"


def _distance_bucket(distance: float) -> str:
    for upper in (0.02, 0.05, 0.08, 0.12, 0.2, 0.35, 0.5, 0.75, 1.0):
        if distance <= upper:
            return f"<={upper:g}s"
    return ">1s"


def _interval_distance(value: float, intervals: Iterable[tuple[float, float]]) -> float:
    distances = [
        0.0 if start <= value <= end else start - value if value < start else value - end
        for start, end in intervals
    ]
    return min(distances) if distances else 999.0


def _canonical(value: int, specs: dict[int, dict[str, Any]]) -> int:
    spec = specs.get(value)
    return int(spec["canonical_skill_id"]) if spec is not None else value


def audit_capture(path: Path, specs: dict[int, dict[str, Any]]) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    current: dict[int, tuple[int, int, str]] = {}
    seen_objects: set[tuple[int, int]] = set()
    object_bindings: dict[tuple[int, int], tuple[int, int, str, str]] = {}
    counts: Counter[str] = Counter()
    by_job: dict[str, Counter[str]] = defaultdict(Counter)
    by_skill: dict[str, Counter[str]] = defaultdict(Counter)
    by_class: dict[str, Counter[str]] = defaultdict(Counter)
    age_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    direct_signal_distances: dict[str, Counter[str]] = defaultdict(Counter)
    projectile_signal_distances: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []

    def feed_events(packet):
        events = original(packet)
        for event in events:
            if isinstance(event, SkillCastEvent):
                actor = _actor_id(event.source_id)
                skill = _actor_id(event.skill_id)
                if actor > 0 and skill in specs:
                    current[actor] = (
                        skill, int(event.timestamp_ms), str(event.packet_kind)
                    )
                continue
            if not isinstance(event, CombatEvent):
                continue
            if (
                event.healing or event.amount <= 0
                or "direct37" not in event.wire_damage_layout
                or event.wire_source_actor_id <= 0x00FFFFFF
                or event.wire_damage_object_id <= 0
            ):
                continue
            object_key = (
                int(event.wire_source_actor_id), int(event.wire_damage_object_id)
            )
            first_object_hit = object_key not in seen_objects
            seen_objects.add(object_key)
            state = current.get(event.wire_source_actor_id)
            if first_object_hit:
                counts["first_object_hits"] += 1
                if state is None:
                    counts["first_no_current_action"] += 1
                else:
                    candidate, started_ms, packet_kind = state
                    spec = specs[candidate]
                    age = (int(event.timestamp_ms) - started_ms) / 1000.0
                    # The native state changes on a new action.  A missing
                    # action-end packet must not make this shadow state
                    # immortal, so static ACT duration is a hard upper bound
                    # plus one 250-ms transport tick.
                    within = (
                        age >= -0.001
                        and age <= max(0.25, float(spec["nominal_seconds"]) + 0.25)
                    )
                    route = (
                        "hybrid" if spec["has_direct_segments"] and spec["has_projectile_signals"]
                        else "direct" if spec["has_direct_segments"]
                        else "projectile" if spec["has_projectile_signals"]
                        else "untyped"
                    )
                    if not within:
                        counts["first_outside_static_action"] += 1
                    elif route == "untyped":
                        counts["first_current_action_untyped"] += 1
                    else:
                        object_bindings[object_key] = (
                            candidate, started_ms, route, packet_kind
                        )
                        counts["bound_objects"] += 1
            binding = object_bindings.get(object_key)
            if binding is None:
                counts["unbound_hits"] += 1
                continue
            candidate, started_ms, route, packet_kind = binding
            spec = specs[candidate]
            age = (int(event.timestamp_ms) - started_ms) / 1000.0
            counts["eligible_hits"] += 1
            counts[f"eligible_{route}"] += 1
            counts[f"eligible_from_{packet_kind}"] += 1
            truth = _actor_id(event.skill_id)
            deterministic = bool(
                truth > 0 and is_deterministic_attribution(event.attribution_method)
            )
            matched = deterministic and _canonical(truth, specs) == _canonical(candidate, specs)
            conflicted = deterministic and not matched
            status = "match" if matched else "conflict" if conflicted else "unverified"
            counts[f"{status}_hits"] += 1
            if first_object_hit:
                counts[f"first_{status}_hits"] += 1
            by_job[str(event.job)][status] += 1
            by_skill[f"{candidate}|{spec['name']}|{event.job}"][status] += 1
            by_class[route][status] += 1
            by_class[f"packet:{packet_kind}"][status] += 1
            age_buckets[_bucket(age)][status] += 1
            if spec["direct_intervals"]:
                distance = _interval_distance(age, spec["direct_intervals"])
                direct_signal_distances[_distance_bucket(distance)][status] += 1
            if spec["projectile_signal_times"]:
                # Projectile impact cannot precede creation.  Distance is zero
                # after the nearest preceding spawn and otherwise measures how
                # early the Hit arrived.  Travel time is reported separately
                # through the ordinary age buckets.
                preceding = [
                    signal_time for signal_time in spec["projectile_signal_times"]
                    if signal_time <= age
                ]
                distance = 0.0 if preceding else min(spec["projectile_signal_times"]) - age
                projectile_signal_distances[_distance_bucket(max(0.0, distance))][status] += 1
            if conflicted and len(examples) < 200:
                examples.append({
                    "capture": path.name,
                    "timestamp_ms": int(event.timestamp_ms),
                    "actor": int(event.wire_source_actor_id),
                    "object": int(event.wire_damage_object_id),
                    "candidate_skill": candidate,
                    "truth_skill": truth,
                    "truth_method": str(event.attribution_method),
                    "age_seconds": round(age, 6),
                    "route": route,
                })
        return events

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return {
        "capture": path.name,
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "counts": dict(counts),
        "by_job": {key: dict(value) for key, value in sorted(by_job.items())},
        "by_skill": {key: dict(value) for key, value in sorted(by_skill.items())},
        "by_class": {key: dict(value) for key, value in sorted(by_class.items())},
        "age_buckets": {key: dict(value) for key, value in age_buckets.items()},
        "direct_signal_distances": {
            key: dict(value) for key, value in direct_signal_distances.items()
        },
        "projectile_signal_distances": {
            key: dict(value) for key, value in projectile_signal_distances.items()
        },
        "conflict_examples": examples,
    }


def _merge(reports: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    merged: dict[str, Counter[str]] = defaultdict(Counter)
    for report in reports:
        for label, values in report.get(key, {}).items():
            merged[label].update({str(k): int(v) for k, v in values.items()})
    return {label: dict(values) for label, values in sorted(merged.items())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--attack-catalog", type=Path, default=DEFAULT_ATTACK_CATALOG)
    parser.add_argument("--capture", action="append", default=[])
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    selected = _capture_selection(args.selection.resolve())
    if args.shard_index is not None or args.shard_count is not None:
        if (
            args.capture or args.shard_index is None or args.shard_count is None
            or args.shard_count <= 0 or not 0 <= args.shard_index < args.shard_count
        ):
            raise ValueError("valid shard-index/shard-count require no explicit --capture")
        selected = [
            row for index, row in enumerate(selected)
            if index % args.shard_count == args.shard_index
        ]
    paths = _find_captures(args.capture_root.resolve(), selected, set(args.capture))
    specs = _skill_specs(args.attack_catalog.resolve())
    reports = [audit_capture(path, specs) for path in paths]
    totals: Counter[str] = Counter()
    for report in reports:
        totals.update(report["counts"])
    verified = totals["match_hits"] + totals["conflict_hits"]
    report = {
        "schema": "client-current-skill-hit-shadow-v52",
        "method": "offline replay; actor current action plus exact ACT typed signals",
        "capture_count": len(reports),
        "summary": {
            **dict(totals),
            "verified_hits": verified,
            "precision_percent": 100.0 * totals["match_hits"] / verified if verified else None,
        },
        "by_job": _merge(reports, "by_job"),
        "by_skill": _merge(reports, "by_skill"),
        "by_class": _merge(reports, "by_class"),
        "age_buckets": _merge(reports, "age_buckets"),
        "direct_signal_distances": _merge(reports, "direct_signal_distances"),
        "projectile_signal_distances": _merge(reports, "projectile_signal_distances"),
        "captures": reports,
        "production_changed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
