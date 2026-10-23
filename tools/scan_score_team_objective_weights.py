from __future__ import annotations

import argparse
import copy
import json
import pickle
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, as_number, norm
from dragon_nest_builder.timeline_parallel import reset_timeline_process_pool
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
from dragon_nest_builder.live_dps.scoring import canonical_skill


def _base_specs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one visible baseline theory-axis spec per unique job."""

    selected: dict[str, dict[str, Any]] = {}
    for raw in payload.get("specs", ()):
        if not isinstance(raw, dict):
            continue
        job = norm(raw.get("job"))
        if (
            not job
            or job in selected
            or not raw.get("long_sim_planner")
            or raw.get("score_refinement")
            or raw.get("excluded_skill_keys")
            or not raw.get("include_display_windows", True)
            or not isinstance(raw.get("team_objective_profile"), dict)
        ):
            continue
        selected[job] = copy.deepcopy(raw)
    return list(selected.values())


def _scaled_spec(
    spec: dict[str, Any],
    target_weight: float,
    captured_weight: float,
) -> dict[str, Any]:
    variant = copy.deepcopy(spec)
    profile = variant.get("team_objective_profile")
    if not isinstance(profile, dict):
        return variant
    scale = max(0.0, target_weight) / max(1e-12, captured_weight)
    for member in profile.get("members", ()):
        if isinstance(member, dict):
            member["weight"] = max(0.0, as_number(member.get("weight"))) * scale
    profile["other_damage"] = max(0.0, as_number(profile.get("other_damage"))) * scale
    return variant


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    segments = [row for row in result.get("segments", ()) if isinstance(row, dict)]
    counts: Counter[str] = Counter()
    support_counts: Counter[str] = Counter()
    damage: Counter[str] = Counter()
    for row in segments:
        skill = norm(row.get("skill"))
        if not skill:
            continue
        score = max(0.0, as_number(row.get("effective_score")))
        counts[skill] += 1
        damage[skill] += score
        if norm(row.get("kind")) == "support" or score <= 1e-9:
            support_counts[skill] += 1
    return {
        "total": max(0.0, as_number(result.get("total"))),
        "dps": max(0.0, as_number(result.get("dps"))),
        "segments": len(segments),
        "counts": dict(sorted(counts.items())),
        "supportCounts": dict(sorted(support_counts.items())),
        "damage": dict(sorted(damage.items())),
    }


def _theoretical_cast_schedule(
    specs: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Replace recorded intermittent casts with the selected theory casts.

    The normal score planner evaluates each owner against the captured team
    environment.  This diagnostic schedule is used by an optional second pass
    so every recipient sees the support casts selected by its teammates at the
    tested objective weight.
    """

    if not specs:
        return {}
    schedule = copy.deepcopy(specs[0].get("cast_schedule") or {})
    result_by_job = {
        norm(spec.get("job")): result
        for spec, result in zip(specs, results)
        if isinstance(result, dict)
    }
    team = [norm(job) for job in specs[0].get("team", ())]
    provider_jobs = {
        f"队员{index + 1} {job}": job
        for index, job in enumerate(team)
        if job
    }
    for key in list(schedule):
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        provider, scheduled_skill = key
        job = provider_jobs.get(norm(provider), "")
        result = result_by_job.get(job)
        if not isinstance(result, dict):
            continue
        skill_key = canonical_skill(scheduled_skill)
        schedule[key] = [
            (max(0.0, as_number(row.get("start"))), max(0.0, as_number(row.get("end"))))
            for row in result.get("segments", ())
            if isinstance(row, dict)
            and canonical_skill(row.get("skill")) == skill_key
            and as_number(row.get("end")) > as_number(row.get("start")) + 1e-9
        ]
    return schedule


def _count_differences(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, int]:
    current_counts = current.get("counts", {})
    baseline_counts = baseline.get("counts", {})
    skills = set(current_counts) | set(baseline_counts)
    return {
        skill: int(current_counts.get(skill, 0)) - int(baseline_counts.get(skill, 0))
        for skill in sorted(skills)
        if int(current_counts.get(skill, 0)) != int(baseline_counts.get(skill, 0))
    }


def scan_dump(
    path: Path,
    weights: list[float],
    captured_weight: float,
    runtime: Any,
    coupled: bool,
) -> dict[str, Any]:
    payload = pickle.loads(path.read_bytes())
    specs = _base_specs(payload)
    if not specs:
        raise ValueError(f"No baseline planner specs found in {path}")
    team = [norm(job) for job in specs[0].get("team", ()) if norm(job)]
    team_counts = Counter(team)
    rows: dict[str, Any] = {}
    for target_weight in weights:
        variants = [
            _scaled_spec(spec, target_weight, captured_weight)
            for spec in specs
        ]
        runtime._extreme_timeline_result_cache = {}
        started = time.perf_counter()
        results = runtime._extreme_timeline_tasks_parallel(variants)
        elapsed = time.perf_counter() - started
        by_job = {
            norm(spec.get("job")): _result_summary(result)
            for spec, result in zip(specs, results)
            if isinstance(result, dict)
        }
        team_total = sum(
            as_number(by_job.get(job, {}).get("total")) * count
            for job, count in team_counts.items()
        )
        rows[f"{target_weight:g}"] = {
            "weight": target_weight,
            "seconds": elapsed,
            "teamPersonalDamageTotal": team_total,
            "jobs": by_job,
        }
        if coupled:
            theory_schedule = _theoretical_cast_schedule(variants, results)
            coupled_variants = []
            for variant in variants:
                coupled_variant = copy.deepcopy(variant)
                coupled_variant["cast_schedule"] = copy.deepcopy(theory_schedule)
                coupled_variants.append(coupled_variant)
            runtime._extreme_timeline_result_cache = {}
            coupled_started = time.perf_counter()
            coupled_results = runtime._extreme_timeline_tasks_parallel(coupled_variants)
            coupled_elapsed = time.perf_counter() - coupled_started
            coupled_by_job = {
                norm(spec.get("job")): _result_summary(result)
                for spec, result in zip(specs, coupled_results)
                if isinstance(result, dict)
            }
            rows[f"{target_weight:g}"]["coupledSeconds"] = coupled_elapsed
            rows[f"{target_weight:g}"]["coupledTeamDamageTotal"] = sum(
                as_number(coupled_by_job.get(job, {}).get("total")) * count
                for job, count in team_counts.items()
            )
            rows[f"{target_weight:g}"]["coupledJobs"] = coupled_by_job

    baseline_key = min(
        rows,
        key=lambda key: abs(as_number(rows[key].get("weight")) - captured_weight),
    )
    baseline = rows[baseline_key]
    baseline_total = max(1e-12, as_number(baseline.get("teamPersonalDamageTotal")))
    baseline_coupled_total = max(
        1e-12,
        as_number(baseline.get("coupledTeamDamageTotal")),
    )
    for row in rows.values():
        row["teamPersonalDamageDeltaPercent"] = (
            as_number(row.get("teamPersonalDamageTotal")) / baseline_total - 1.0
        ) * 100.0
        row["castChangesVsBaseline"] = {
            job: differences
            for job, result in row.get("jobs", {}).items()
            if (differences := _count_differences(
                result,
                baseline.get("jobs", {}).get(job, {}),
            ))
        }
        if coupled:
            row["coupledTeamDamageDeltaPercent"] = (
                as_number(row.get("coupledTeamDamageTotal"))
                / baseline_coupled_total
                - 1.0
            ) * 100.0
    return {
        "source": str(path.resolve()),
        "capturedWeight": captured_weight,
        "baselineKey": baseline_key,
        "team": team,
        "teamCounts": dict(team_counts),
        "weights": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_dumps", nargs="+", type=Path)
    parser.add_argument(
        "--weights",
        default="0,0.15,0.3,0.45,0.6,0.8,1",
        help="Comma-separated absolute full-team objective weights.",
    )
    parser.add_argument("--captured-weight", type=float, default=0.30)
    parser.add_argument(
        "--coupled",
        action="store_true",
        help="Run a second pass where all jobs receive the tested theory support schedules.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    weights = sorted({
        max(0.0, float(value.strip()))
        for value in args.weights.split(",")
        if value.strip()
    })
    if not weights:
        raise ValueError("At least one objective weight is required")

    store = DataStore.load()
    runtime = LegacyTimelineService(store, BuildCalculator(store)).extreme_runtime(
        copy.deepcopy(store.defaults["build1"])
    )
    report: dict[str, Any] = {
        "weights": weights,
        "capturedWeight": args.captured_weight,
        "captures": {},
    }
    try:
        for path in args.spec_dumps:
            print(f"Scanning {path.name}...", flush=True)
            report["captures"][path.stem] = scan_dump(
                path,
                weights,
                max(1e-12, args.captured_weight),
                runtime,
                args.coupled,
            )
    finally:
        reset_timeline_process_pool()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        name: {
            key: {
                "deltaPercent": row["teamPersonalDamageDeltaPercent"],
                "coupledDeltaPercent": row.get("coupledTeamDamageDeltaPercent"),
                "seconds": row["seconds"],
            }
            for key, row in capture["weights"].items()
        }
        for name, capture in report["captures"].items()
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
