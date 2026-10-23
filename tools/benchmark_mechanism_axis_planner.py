from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from dragon_nest_builder.mixins.extreme_engine import (  # noqa: E402
    FAST_MECHANISM_UNIFIED_POLICY,
)
from dragon_nest_builder.timeline_parallel import reset_timeline_process_pool  # noqa: E402
from tools.benchmark_parallel_extreme_timelines import DEFAULT_TEAM  # noqa: E402
from tools.benchmark_timeline_optimizer import _prepare_editor  # noqa: E402


DEFAULT_BLOCKED = [
    {"start": 60.0, "end": 90.0},
    {"start": 180.0, "end": 215.0},
    {"start": 360.0, "end": 400.0},
    {"start": 520.0, "end": 545.0},
]


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    skills: dict[str, dict[str, float]] = defaultdict(
        lambda: {"casts": 0.0, "score": 0.0, "time": 0.0}
    )
    for row in result.get("segments", []):
        if row.get("timeline_hidden") or norm(row.get("kind")) == "invalid":
            continue
        skill = norm(row.get("skill")) or norm(row.get("kind"))
        values = skills[skill]
        values["casts"] += 1
        values["score"] += max(0.0, as_number(row.get("effective_score")))
        values["time"] += max(
            0.0,
            as_number(row.get("end")) - as_number(row.get("start")),
        )
    return {
        "total": as_number(result.get("total")),
        "segments": len(result.get("segments", [])),
        "skills": {
            key: {
                "casts": int(value["casts"]),
                "score": round(value["score"], 6),
                "time": round(value["time"], 6),
            }
            for key, value in sorted(skills.items())
        },
        "casts": [
            {
                "skill": norm(row.get("skill")),
                "kind": norm(row.get("kind")),
                "start": round(as_number(row.get("start")), 5),
                "end": round(as_number(row.get("end")), 5),
                "score": round(max(0.0, as_number(row.get("effective_score"))), 5),
            }
            for row in result.get("segments", [])
            if not row.get("timeline_hidden") and norm(row.get("kind")) != "invalid"
        ],
        "support_selections": dict(Counter(
            norm(row.get("selected"))
            for row in result.get("planner_trace", [])
            if row.get("type") == "support" and norm(row.get("selected"))
        )),
        "trace": result.get("planner_trace", []),
        "quality": result.get("planner_quality", {}),
    }


def run_axis(
    editor: Any,
    job: str,
    team: list[str],
    effects: list[dict[str, Any]],
    window: float,
    blocked: list[dict[str, float]],
) -> tuple[dict[str, Any], float]:
    editor._extreme_timeline_result_cache = {}
    started = time.perf_counter()
    result = editor._extreme_team_timeline_result(
        job,
        team,
        effects,
        1.0,
        window,
        include_display_windows=False,
        blocked_intervals=blocked,
        fast_long_timeline=True,
    )
    return result, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=float, default=600.0)
    parser.add_argument("--jobs", nargs="*", default=[])
    parser.add_argument("--all-jobs", action="store_true")
    parser.add_argument("--trace", nargs="*", default=[])
    defaults = FAST_MECHANISM_UNIFIED_POLICY
    parser.add_argument("--debt-weight", type=float, default=defaults["ready_debt_weight"])
    parser.add_argument("--debt-dpa-floor", type=float, default=defaults["ready_debt_dpa_floor"])
    parser.add_argument("--debt-cycle-floor", type=float, default=defaults["ready_debt_cycle_floor"])
    parser.add_argument("--override-ratio", type=float, default=defaults["override_ratio"])
    parser.add_argument("--ambiguity", type=float, default=defaults["ambiguity_ratio"])
    parser.add_argument("--support-horizon", type=float, default=defaults["support_horizon"])
    parser.add_argument("--state-support-horizon", type=float, default=defaults["state_support_horizon"])
    parser.add_argument("--support-candidate-limit", type=int, default=defaults["support_candidate_limit"])
    parser.add_argument("--displacement-weight", type=float, default=defaults["displacement_weight"])
    parser.add_argument("--short-displacement-weight", type=float, default=defaults["short_displacement_weight"])
    parser.add_argument("--boundary-urgency-weight", type=float, default=defaults["boundary_urgency_weight"])
    parser.add_argument("--boundary-urgency-span", type=float, default=defaults["boundary_urgency_span"])
    parser.add_argument("--refinement", action="store_true")
    parser.add_argument("--refinement-seconds", type=float, default=0.12)
    parser.add_argument("--refinement-evaluations", type=int, default=4)
    parser.add_argument("--cycle-deadline-weight", type=float, default=0.0)
    parser.add_argument("--cycle-deadline-delay", type=float, default=2.0)
    parser.add_argument("--opening-setup", action="store_true")
    parser.add_argument(
        "--no-pair-planning",
        action="store_false",
        dest="pair_planning",
    )
    parser.set_defaults(pair_planning=defaults["pair_planning"])
    parser.add_argument("--profile", nargs=5, type=float, default=defaults["profile"])
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument(
        "--parallel-repeats",
        type=int,
        default=1,
        help="Reuse a warmed worker pool and average this many alternating runs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "benchmarks" / "mechanism_axis_comparison.json",
    )
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor, _metrics = _prepare_editor(app, store, calculator, "剑圣", "剑皇")
    if args.all_jobs:
        all_jobs = [
            norm(second)
            for first in store.first_jobs()
            for second in store.second_jobs(first)
            if norm(second)
        ]
    else:
        all_jobs = DEFAULT_TEAM if not args.jobs else [norm(job) for job in args.jobs]
    team = list(dict.fromkeys([*DEFAULT_TEAM, *all_jobs]))[:8]
    effects = editor._extreme_team_effects_for_jobs(team)
    editor._skill_burst_new_scheme_1_trace = [] if args.trace else None
    editor._skill_burst_new_scheme_1_trace_support_skills = {
        norm(skill) for skill in args.trace if norm(skill)
    }
    editor._skill_burst_fast_mechanism_ready_debt_weight = args.debt_weight
    editor._skill_burst_fast_mechanism_ready_debt_dpa_floor = args.debt_dpa_floor
    editor._skill_burst_fast_mechanism_ready_debt_cycle_floor = args.debt_cycle_floor
    editor._skill_burst_fast_mechanism_override_ratio = args.override_ratio
    editor._skill_burst_fast_mechanism_ambiguity_ratio = args.ambiguity
    editor._skill_burst_fast_mechanism_support_horizon = args.support_horizon
    editor._skill_burst_fast_mechanism_state_support_horizon = args.state_support_horizon
    editor._skill_burst_fast_mechanism_support_candidate_limit = args.support_candidate_limit
    editor._skill_burst_fast_mechanism_displacement_weight = args.displacement_weight
    editor._skill_burst_fast_mechanism_short_displacement_weight = args.short_displacement_weight
    editor._skill_burst_fast_mechanism_pair_planning = args.pair_planning
    editor._skill_burst_fast_mechanism_boundary_urgency_weight = args.boundary_urgency_weight
    editor._skill_burst_fast_mechanism_boundary_urgency_span = args.boundary_urgency_span
    editor._skill_burst_fast_mechanism_refinement = args.refinement
    editor._skill_burst_fast_mechanism_refinement_seconds = args.refinement_seconds
    editor._skill_burst_fast_mechanism_refinement_evaluations = args.refinement_evaluations
    editor._skill_burst_fast_mechanism_cycle_deadline_weight = args.cycle_deadline_weight
    editor._skill_burst_fast_mechanism_cycle_deadline_delay = args.cycle_deadline_delay
    editor._skill_burst_fast_mechanism_opening_setup = args.opening_setup
    editor._skill_burst_fast_mechanism_profile = tuple(args.profile)

    planner_class = type(editor)
    original_enabled = planner_class._skill_burst_new_scheme_1_enabled
    rows = []
    try:
        if args.parallel:
            specs = [
                {
                    "job": job,
                    "team": team,
                    "effects": effects,
                    "hand_multiplier": 1.0,
                    "window": args.window,
                    "include_display_windows": False,
                    "blocked_intervals": DEFAULT_BLOCKED,
                    "fast_long_timeline": True,
                }
                for job in all_jobs
            ]

            def run_parallel(
                use_legacy: bool,
                marker: int | None = None,
            ) -> tuple[list[dict[str, Any]], float]:
                editor._skill_burst_fast_mechanism_use_legacy_diagnostic = use_legacy
                editor.__dict__.pop("_extreme_parallel_snapshot", None)
                marked_specs = specs
                if marker is not None:
                    marked_specs = copy.deepcopy(specs)
                    for spec in marked_specs:
                        spec["blocked_intervals"] = [
                            *spec["blocked_intervals"],
                            {
                                "start": args.window + 100.0 + marker,
                                "end": args.window + 100.001 + marker,
                            },
                        ]
                started = time.perf_counter()
                results = editor._extreme_timeline_tasks_parallel(marked_specs)
                return results, time.perf_counter() - started

            repeats = max(1, int(args.parallel_repeats))
            if repeats == 1:
                reset_timeline_process_pool()
                legacy_results, legacy_wall = run_parallel(True)
                reset_timeline_process_pool()
                pure_results, pure_wall = run_parallel(False)
            else:
                reset_timeline_process_pool()
                run_parallel(True, -2)
                run_parallel(False, -1)
                measured: dict[str, list[float]] = {"legacy": [], "pure": []}
                result_sets: dict[str, list[dict[str, Any]]] = {}
                for repeat in range(repeats):
                    modes = (False, True) if repeat % 2 == 0 else (True, False)
                    for use_legacy in modes:
                        label = "legacy" if use_legacy else "pure"
                        results, elapsed = run_parallel(
                            use_legacy,
                            repeat * 2 + int(use_legacy),
                        )
                        result_sets[label] = results
                        measured[label].append(elapsed)
                legacy_results = result_sets["legacy"]
                pure_results = result_sets["pure"]
                legacy_wall = sum(measured["legacy"]) / repeats
                pure_wall = sum(measured["pure"]) / repeats
            for job, legacy, pure in zip(all_jobs, legacy_results, pure_results):
                legacy_summary = summarize(legacy)
                pure_summary = summarize(pure)
                old_total = legacy_summary["total"]
                rows.append({
                    "job": job,
                    "legacy_elapsed": 0.0,
                    "pure_elapsed": 0.0,
                    "time_change_percent": 0.0,
                    "gain_percent": (
                        (pure_summary["total"] / old_total - 1.0) * 100
                        if old_total > 1e-9
                        else 0.0
                    ),
                    "legacy": legacy_summary,
                    "pure": pure_summary,
                })
            for row in rows:
                print(f"{row['job']}: output {row['gain_percent']:+.3f}%", flush=True)
            parallel_times = {"legacy": legacy_wall, "pure": pure_wall}
        else:
            parallel_times = {}
        for job in ([] if args.parallel else all_jobs):
            planner_class._skill_burst_new_scheme_1_enabled = lambda self: False
            legacy, legacy_elapsed = run_axis(
                editor,
                job,
                team,
                effects,
                args.window,
                DEFAULT_BLOCKED,
            )
            planner_class._skill_burst_new_scheme_1_enabled = original_enabled
            pure, pure_elapsed = run_axis(
                editor,
                job,
                team,
                effects,
                args.window,
                DEFAULT_BLOCKED,
            )
            legacy_summary = summarize(legacy)
            pure_summary = summarize(pure)
            old_total = legacy_summary["total"]
            rows.append({
                "job": job,
                "legacy_elapsed": legacy_elapsed,
                "pure_elapsed": pure_elapsed,
                "time_change_percent": (
                    (pure_elapsed / legacy_elapsed - 1.0) * 100
                    if legacy_elapsed > 1e-9
                    else 0.0
                ),
                "gain_percent": (
                    (pure_summary["total"] / old_total - 1.0) * 100
                    if old_total > 1e-9
                    else 0.0
                ),
                "legacy": legacy_summary,
                "pure": pure_summary,
            })
            print(
                f"{job}: output {rows[-1]['gain_percent']:+.3f}% | "
                f"time {rows[-1]['time_change_percent']:+.1f}%",
                flush=True,
            )
    finally:
        planner_class._skill_burst_new_scheme_1_enabled = original_enabled
        reset_timeline_process_pool()
        editor.deleteLater()
        app.processEvents()

    payload = {
        "window": args.window,
        "blocked": DEFAULT_BLOCKED,
        "team": team,
        "legacy_elapsed": sum(row["legacy_elapsed"] for row in rows),
        "pure_elapsed": sum(row["pure_elapsed"] for row in rows),
        "legacy_total": sum(row["legacy"]["total"] for row in rows),
        "pure_total": sum(row["pure"]["total"] for row in rows),
        "parallel_times": parallel_times,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output, flush=True)


if __name__ == "__main__":
    main()
