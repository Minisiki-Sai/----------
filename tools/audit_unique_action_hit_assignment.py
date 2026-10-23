from __future__ import annotations

import argparse
import json
import math
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore  # noqa: E402
from dragon_nest_builder.live_dps.damage_ledger import skill_family  # noqa: E402
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
    SkillCastEvent,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


SCHEMA_VERSION = 1
_ACTION_KINDS = frozenset(
    {"skill_action", "compact_skill_input", "transformed_basic_attack"}
)


def _configured_damage_skill_ids() -> frozenset[int]:
    store = DataStore.load()
    result: set[int] = set()
    for path in sorted(store.skill_damage_dir.glob("job_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for row in payload.get("n", ()):
            if not isinstance(row, list) or not row:
                continue
            try:
                result.add(int(row[0]))
            except (TypeError, ValueError):
                continue
    return frozenset(result)


_DAMAGE_SKILL_IDS = _configured_damage_skill_ids()


def _family(skill_id: object, skill_name: object) -> str:
    return skill_family(str(skill_id or ""), str(skill_name or ""))


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


@dataclass(frozen=True, slots=True)
class CaptureRows:
    path: Path
    casts: tuple[SkillCastEvent, ...]
    truth_hits: tuple[CombatEvent, ...]


@dataclass(frozen=True, slots=True)
class TimingProfile:
    family: str
    samples: int
    captures: int
    actors: int
    minimum_ms: int
    maximum_ms: int


def _load(path: Path) -> CaptureRows:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    try:
        events = source.poll()
    finally:
        source.close()
    casts = tuple(
        row
        for row in events
        if isinstance(row, SkillCastEvent)
        and str(row.packet_kind or "").casefold() in _ACTION_KINDS
    )
    truth_hits = tuple(
        row
        for row in events
        if isinstance(row, CombatEvent)
        and not row.healing
        and row.amount > 0
        and row.has_real_hit_evidence is True
        and str(row.attribution_method or "").casefold() == "packet_skill_id"
    )
    return CaptureRows(path=path, casts=casts, truth_hits=truth_hits)


def _latest_matching_delay(
    hit: CombatEvent,
    casts_by_actor_family: dict[tuple[str, str], tuple[SkillCastEvent, ...]],
    starts_by_actor_family: dict[tuple[str, str], tuple[int, ...]],
    maximum_delay_ms: int,
) -> int | None:
    key = (hit.source_id, _family(hit.skill_id, hit.skill_name))
    starts = starts_by_actor_family.get(key, ())
    if not starts:
        return None
    right = bisect_right(starts, hit.timestamp_ms)
    if right <= 0:
        return None
    cast = casts_by_actor_family[key][right - 1]
    delay = hit.timestamp_ms - cast.timestamp_ms
    if delay < 0 or delay > maximum_delay_ms:
        return None
    return delay


def _indexed_casts(
    rows: Iterable[SkillCastEvent],
) -> tuple[
    dict[tuple[str, str], tuple[SkillCastEvent, ...]],
    dict[tuple[str, str], tuple[int, ...]],
    dict[str, tuple[SkillCastEvent, ...]],
    dict[str, tuple[int, ...]],
]:
    grouped: dict[tuple[str, str], list[SkillCastEvent]] = defaultdict(list)
    actors: dict[str, list[SkillCastEvent]] = defaultdict(list)
    for cast in rows:
        grouped[(cast.source_id, _family(cast.skill_id, cast.skill_name))].append(
            cast
        )
        actors[cast.source_id].append(cast)
    for values in grouped.values():
        values.sort(key=lambda row: row.timestamp_ms)
    for values in actors.values():
        values.sort(key=lambda row: row.timestamp_ms)
    grouped_rows = {key: tuple(values) for key, values in grouped.items()}
    actor_rows = {key: tuple(values) for key, values in actors.items()}
    return (
        grouped_rows,
        {
            key: tuple(row.timestamp_ms for row in values)
            for key, values in grouped_rows.items()
        },
        actor_rows,
        {
            key: tuple(row.timestamp_ms for row in values)
            for key, values in actor_rows.items()
        },
    )


def _train(
    captures: list[CaptureRows],
    *,
    maximum_delay_ms: int,
    minimum_samples: int,
    minimum_captures: int,
) -> dict[str, TimingProfile]:
    delays: dict[str, list[int]] = defaultdict(list)
    files: dict[str, set[str]] = defaultdict(set)
    actors: dict[str, set[str]] = defaultdict(set)
    for capture in captures:
        grouped, starts, _, _ = _indexed_casts(capture.casts)
        for hit in capture.truth_hits:
            delay = _latest_matching_delay(
                hit, grouped, starts, maximum_delay_ms
            )
            if delay is None:
                continue
            family = _family(hit.skill_id, hit.skill_name)
            delays[family].append(delay)
            files[family].add(str(capture.path))
            actors[family].add(f"{capture.path}|{hit.source_id}")

    profiles: dict[str, TimingProfile] = {}
    for family, values in delays.items():
        if len(values) < minimum_samples or len(files[family]) < minimum_captures:
            continue
        # Quantiles reject a stale previous-cast tail. The padding accounts
        # for capture timestamp granularity; it is deliberately small so the
        # audit cannot manufacture uniqueness with a broad nearest-cast rule.
        lower = max(0, _percentile(values, 0.01) - 40)
        upper = min(maximum_delay_ms, _percentile(values, 0.99) + 40)
        profiles[family] = TimingProfile(
            family=family,
            samples=len(values),
            captures=len(files[family]),
            actors=len(actors[family]),
            minimum_ms=lower,
            maximum_ms=max(lower, upper),
        )
    return profiles


def _predict(
    hit: CombatEvent,
    profiles: dict[str, TimingProfile],
    casts_by_actor: dict[str, tuple[SkillCastEvent, ...]],
    starts_by_actor: dict[str, tuple[int, ...]],
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    casts = casts_by_actor.get(hit.source_id, ())
    starts = starts_by_actor.get(hit.source_id, ())
    if not casts:
        return None, (), ()
    max_window = max(
        (profile.maximum_ms for profile in profiles.values()), default=0
    )
    left_time = hit.timestamp_ms - max_window
    left = bisect_right(starts, left_time - 1)
    right = bisect_right(starts, hit.timestamp_ms)
    candidates: set[str] = set()
    unknown_damage_actions: set[str] = set()
    for cast in casts[left:right]:
        family = _family(cast.skill_id, cast.skill_name)
        profile = profiles.get(family)
        if profile is None:
            try:
                configured_damage = int(cast.skill_id) in _DAMAGE_SKILL_IDS
            except (TypeError, ValueError):
                configured_damage = False
            if configured_damage:
                unknown_damage_actions.add(family)
            continue
        delay = hit.timestamp_ms - cast.timestamp_ms
        if profile.minimum_ms <= delay <= profile.maximum_ms:
            candidates.add(family)
    ordered = tuple(sorted(candidates))
    unknown = tuple(sorted(unknown_damage_actions))
    if len(ordered) != 1 or unknown:
        return None, ordered, unknown
    return ordered[0], ordered, unknown


def _evaluate_fold(
    training: list[CaptureRows],
    test: CaptureRows,
    *,
    maximum_delay_ms: int,
    minimum_samples: int,
    minimum_captures: int,
) -> dict[str, object]:
    profiles = _train(
        training,
        maximum_delay_ms=maximum_delay_ms,
        minimum_samples=minimum_samples,
        minimum_captures=minimum_captures,
    )
    _, _, actors, starts = _indexed_casts(test.casts)
    outcomes: Counter[str] = Counter()
    wrong: Counter[tuple[str, str]] = Counter()
    unique_by_true: Counter[str] = Counter()
    true_total: Counter[str] = Counter()
    candidate_sizes: Counter[int] = Counter()
    unknown_veto_hits = 0
    wrong_examples: list[dict[str, object]] = []
    for hit in test.truth_hits:
        truth = _family(hit.skill_id, hit.skill_name)
        true_total[truth] += 1
        predicted, candidates, unknown = _predict(hit, profiles, actors, starts)
        candidate_sizes[len(candidates)] += 1
        if unknown:
            unknown_veto_hits += 1
        if predicted is None:
            outcomes["ambiguous_or_uncovered"] += 1
        elif predicted == truth:
            outcomes["correct_unique"] += 1
            unique_by_true[truth] += 1
        else:
            outcomes["wrong_unique"] += 1
            wrong[(truth, predicted)] += 1
            if len(wrong_examples) < 100:
                actor_casts = actors.get(hit.source_id, ())
                actor_starts = starts.get(hit.source_id, ())
                left = bisect_right(actor_starts, hit.timestamp_ms - maximum_delay_ms - 1)
                right = bisect_right(actor_starts, hit.timestamp_ms)
                wrong_examples.append(
                    {
                        "timestamp_ms": hit.timestamp_ms,
                        "source_id": hit.source_id,
                        "job": hit.job,
                        "truth": truth,
                        "predicted": predicted,
                        "nearby_actions": [
                            {
                                "delay_ms": hit.timestamp_ms - cast.timestamp_ms,
                                "family": _family(cast.skill_id, cast.skill_name),
                                "skill_id": cast.skill_id,
                                "packet_kind": cast.packet_kind,
                                "profile": (
                                    None
                                    if profiles.get(_family(cast.skill_id, cast.skill_name)) is None
                                    else {
                                        "min": profiles[_family(cast.skill_id, cast.skill_name)].minimum_ms,
                                        "max": profiles[_family(cast.skill_id, cast.skill_name)].maximum_ms,
                                    }
                                ),
                            }
                            for cast in actor_casts[left:right]
                        ],
                    }
                )
    return {
        "capture": str(test.path),
        "truth_hits": len(test.truth_hits),
        "profiles": len(profiles),
        "outcomes": dict(outcomes),
        "candidate_sizes": {str(key): value for key, value in sorted(candidate_sizes.items())},
        "unknown_damage_action_veto_hits": unknown_veto_hits,
        "wrong_pairs": [
            {"truth": key[0], "predicted": key[1], "hits": count}
            for key, count in wrong.most_common(50)
        ],
        "wrong_examples": wrong_examples,
        "coverage_by_truth": [
            {
                "family": family,
                "truth_hits": count,
                "correct_unique": unique_by_true[family],
            }
            for family, count in true_total.most_common()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Leave-one-capture-out audit of action + learned Hit-window "
            "assignment after hiding exact packet skill IDs."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--maximum-delay-ms", type=int, default=20_000)
    parser.add_argument("--minimum-samples", type=int, default=20)
    parser.add_argument("--minimum-captures", type=int, default=2)
    args = parser.parse_args()

    captures = [_load(path.resolve()) for path in args.captures]
    reports = [
        _evaluate_fold(
            [row for index, row in enumerate(captures) if index != test_index],
            test,
            maximum_delay_ms=max(1, args.maximum_delay_ms),
            minimum_samples=max(1, args.minimum_samples),
            minimum_captures=max(1, args.minimum_captures),
        )
        for test_index, test in enumerate(captures)
    ]
    outcomes: Counter[str] = Counter()
    for report in reports:
        outcomes.update(report["outcomes"])
    result = {
        "schema_version": SCHEMA_VERSION,
        "method": "leave_one_capture_out_unique_action_hit_window",
        "policy": {
            "truth": "packet_skill_id direct real-Hit only",
            "skill_id_hidden_during_prediction": True,
            "nearest_candidate_forced": False,
            "ambiguous_candidates_fail_closed": True,
            "maximum_delay_ms": max(1, args.maximum_delay_ms),
            "minimum_samples": max(1, args.minimum_samples),
            "minimum_captures": max(1, args.minimum_captures),
        },
        "captures": len(captures),
        "truth_hits": sum(len(row.truth_hits) for row in captures),
        "outcomes": dict(outcomes),
        "precision_percent": round(
            outcomes["correct_unique"]
            / max(1, outcomes["correct_unique"] + outcomes["wrong_unique"])
            * 100.0,
            6,
        ),
        "coverage_percent": round(
            outcomes["correct_unique"]
            / max(1, sum(outcomes.values()))
            * 100.0,
            6,
        ),
        "release_candidate": outcomes["wrong_unique"] == 0,
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: result[key] for key in ("captures", "truth_hits", "outcomes", "precision_percent", "coverage_percent", "release_candidate")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
