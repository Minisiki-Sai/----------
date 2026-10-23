from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from tools.benchmark_timeline_optimizer import (  # noqa: E402
    DEFAULT_SEED,
    _make_team,
    _prepare_editor,
    _timeline_score,
)


SCHEMES = ("stable", "pure_new")
SHORT_MODES = {
    "burst": {"burst"},
    "accel": {"accel"},
    "burst_cocktail": {"burst", "cocktail"},
    "accel_burst": {"accel", "burst"},
}


def _configure(editor: Any, scheme: str) -> None:
    editor._skill_burst_disable_new_scheme_1 = scheme == "stable"
    editor._skill_burst_enable_new_scheme_1_quality_floor = False
    for attr in (
        "_skill_burst_force_conservative_1_5",
        "_skill_burst_force_conservative_1_6",
        "_skill_burst_disable_conservative_1_6",
        "_skill_burst_force_local_optimizer",
        "_skill_burst_disable_local_optimizer",
        "_skill_burst_disable_hybrid_optimizer",
    ):
        editor.__dict__.pop(attr, None)
    editor._skill_burst_new_scheme_1_result_cache = {}
    editor._skill_burst_local_optimizer_cache = {}
    editor._skill_burst_conservative_1_6_cache = {}
    editor._team_practical_simulation_cache = {}
    if bool(getattr(editor, "_skill_burst_benchmark_trace_enabled", False)):
        editor._skill_burst_new_scheme_1_trace = []


def _result(
    segments: list[dict[str, Any]],
    window: float,
    elapsed: float,
) -> dict[str, Any]:
    score = _timeline_score(segments, window)
    visible = [
        segment
        for segment in segments
        if not segment.get("timeline_hidden")
        and norm(segment.get("kind")) != "invalid"
    ]
    skill_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"casts": 0.0, "score": 0.0, "time": 0.0}
    )
    for segment in visible:
        skill = norm(segment.get("skill")) or norm(segment.get("kind")) or "未命名"
        row = skill_totals[skill]
        row["casts"] += 1
        row["score"] += max(0.0, as_number(segment.get("effective_score")))
        row["time"] += max(
            0.0,
            min(window, as_number(segment.get("end")))
            - max(0.0, as_number(segment.get("start"))),
        )
    return {
        "score": score,
        "average": score / max(1e-9, window),
        "window": window,
        "segments": len(visible),
        "elapsed": elapsed,
        "skills": sorted(
            (
                {
                    "skill": skill,
                    "casts": int(values["casts"]),
                    "score": values["score"],
                    "time": values["time"],
                }
                for skill, values in skill_totals.items()
            ),
            key=lambda row: (row["score"], row["casts"]),
            reverse=True,
        ),
        "opening": [
            {
                "start": round(float(segment.get("start", 0.0)), 4),
                "skill": norm(segment.get("skill")),
            }
            for segment in visible[:12]
        ],
    }


def _run_long(
    editor: Any,
    metrics: list[dict[str, Any]],
    window: float,
    scheme: str,
) -> dict[str, Any]:
    _configure(editor, scheme)
    editor._skill_burst_schedule_override = editor._skill_long_schedule(window)
    editor.__dict__.pop("_skill_burst_mode_override", None)
    started = time.perf_counter()
    try:
        segments, generated_window = editor._update_skill_burst_sim_preview(
            metrics,
            render_ui=False,
        )
    finally:
        editor.__dict__.pop("_skill_burst_schedule_override", None)
    result = _result(segments, generated_window, time.perf_counter() - started)
    result["refinement"] = dict(
        getattr(editor, "_skill_burst_local_optimizer_last_stats", {})
    )
    result["planning"] = dict(
        getattr(editor, "_skill_burst_new_scheme_1_planning_stats", {})
    )
    result["multistart"] = dict(
        getattr(editor, "_skill_burst_new_scheme_1_last_multistart", {})
    )
    return result


def _run_short(
    editor: Any,
    metrics: list[dict[str, Any]],
    modes: set[str],
    scheme: str,
) -> dict[str, Any]:
    _configure(editor, scheme)
    editor._skill_burst_mode_override = set(modes)
    editor.__dict__.pop("_skill_burst_schedule_override", None)
    started = time.perf_counter()
    try:
        segments, generated_window = editor._update_skill_burst_sim_preview(
            metrics,
            render_ui=False,
        )
    finally:
        editor.__dict__.pop("_skill_burst_mode_override", None)
    result = _result(segments, generated_window, time.perf_counter() - started)
    trace = getattr(editor, "_skill_burst_new_scheme_1_trace", None)
    if isinstance(trace, list):
        result["trace"] = list(trace)
    return result


def _run_team(
    editor: Any,
    metrics: list[dict[str, Any]],
    context: dict[str, Any],
    scheme: str,
) -> dict[str, Any]:
    _configure(editor, scheme)
    started = time.perf_counter()
    result = editor._team_practical_simulate(metrics, context)
    summary = _result(
        list(result.get("segments", [])),
        float(result.get("window", 0.0)),
        time.perf_counter() - started,
    )
    trace = getattr(editor, "_skill_burst_new_scheme_1_trace", None)
    if isinstance(trace, list):
        summary["trace"] = list(trace)
    summary["refinement"] = dict(
        getattr(editor, "_skill_burst_local_optimizer_last_stats", {})
    )
    return summary


def _gain(stable: float, pure_new: float) -> float:
    return (pure_new / stable - 1.0) * 100.0 if stable > 1e-9 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the stable timeline with the independent Scheme 1."
    )
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--job", default="")
    parser.add_argument(
        "--ambiguity-ratio",
        type=float,
        default=None,
        help="Override the pure-new rollout ambiguity ratio; 0 disables ordinary near-tie rollouts.",
    )
    parser.add_argument(
        "--ready-debt-weight",
        type=float,
        default=None,
        help="Override the gradual ready-skill cycle-debt weight.",
    )
    parser.add_argument(
        "--ready-debt-dpa-floor",
        type=float,
        default=None,
        help="Minimum fraction of the best candidate DPA eligible for ready debt.",
    )
    parser.add_argument(
        "--refinement-seconds",
        type=float,
        default=None,
        help="Override the pure-new exact-replay refinement time budget.",
    )
    parser.add_argument(
        "--refinement-evaluations",
        type=int,
        default=None,
        help="Override the pure-new exact-replay refinement proposal limit.",
    )
    parser.add_argument(
        "--disable-refinement",
        action="store_true",
        help="Benchmark the pure scheduler without its bounded exact-replay refinement.",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Include pure-new scheduler decisions in short-case output.",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="Comma-separated subset: long, burst, accel, burst_cocktail, accel_burst, team",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    valid_cases = {"long", *SHORT_MODES, "team"}
    requested_cases = {
        norm(value)
        for value in str(args.cases).split(",")
        if norm(value)
    }
    unknown_cases = requested_cases - valid_cases
    if unknown_cases:
        parser.error(f"unknown cases: {', '.join(sorted(unknown_cases))}")
    selected_cases = requested_cases or valid_cases
    selected_jobs = {
        norm(value)
        for value in str(args.job).split(",")
        if norm(value)
    }

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    all_pairs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second
    ]
    all_jobs = [second for _first, second in all_pairs]
    selected = [
        (index, first, second)
        for index, (first, second) in enumerate(all_pairs)
        if index % max(1, args.shards) == args.shard
        and (not selected_jobs or norm(second) in selected_jobs)
    ]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for done, (index, first_job, second_job) in enumerate(selected, 1):
        editor = None
        job_started = time.perf_counter()
        try:
            editor, metrics = _prepare_editor(
                app,
                store,
                calculator,
                first_job,
                second_job,
            )
            editor._skill_burst_benchmark_trace_enabled = bool(args.trace)
            if args.refinement_seconds is not None:
                editor._skill_burst_new_scheme_1_refinement_seconds = max(
                    0.0,
                    args.refinement_seconds,
                )
            if args.refinement_evaluations is not None:
                editor._skill_burst_new_scheme_1_refinement_evaluations = max(
                    0,
                    args.refinement_evaluations,
                )
            if args.ambiguity_ratio is not None:
                editor._skill_burst_new_scheme_1_ambiguity_ratio = max(
                    0.0,
                    min(1.0, args.ambiguity_ratio),
                )
            if args.ready_debt_weight is not None:
                editor._skill_burst_new_scheme_1_ready_debt_weight = max(
                    0.0,
                    args.ready_debt_weight,
                )
            if args.ready_debt_dpa_floor is not None:
                editor._skill_burst_new_scheme_1_ready_debt_dpa_floor = max(
                    0.0,
                    args.ready_debt_dpa_floor,
                )
            editor._skill_burst_disable_new_scheme_1_refinement = bool(
                args.disable_refinement
            )
            long_results = (
                {
                    scheme: _run_long(editor, metrics, args.window, scheme)
                    for scheme in SCHEMES
                }
                if "long" in selected_cases
                else None
            )
            short_results = {
                mode: {
                    scheme: _run_short(editor, metrics, modes, scheme)
                    for scheme in SCHEMES
                }
                for mode, modes in SHORT_MODES.items()
                if mode in selected_cases
            }

            team: list[str] = []
            team_results = None
            if "team" in selected_cases:
                team = _make_team(all_jobs, second_job, index, args.seed)
                _configure(editor, "stable")
                editor._apply_team_members(team)
                editor._auto_populate_team_effects_from_members(schedule=False)
                app.processEvents()
                context = editor._team_practical_runtime_context(metrics, args.window)
                team_results = {
                    scheme: _run_team(editor, metrics, context, scheme)
                    for scheme in SCHEMES
                }

            cases = {
                **({"long": long_results} if long_results is not None else {}),
                **short_results,
                **({"team": team_results} if team_results is not None else {}),
            }
            gains = {
                name: _gain(result["stable"]["score"], result["pure_new"]["score"])
                for name, result in cases.items()
            }
            elapsed_ratios = {
                name: (
                    result["pure_new"]["elapsed"] / result["stable"]["elapsed"]
                    if result["stable"]["elapsed"] > 1e-9
                    else 0.0
                )
                for name, result in cases.items()
            }
            rows.append(
                {
                    "index": index,
                    "first_job": first_job,
                    "second_job": second_job,
                    "metric_count": len(metrics),
                    "team": team,
                    "cases": cases,
                    "gain_percent": gains,
                    "elapsed_ratio": elapsed_ratios,
                    "job_elapsed": time.perf_counter() - job_started,
                }
            )
            print(
                f"[{done}/{len(selected)}] {second_job}: "
                + ", ".join(f"{name} {gain:+.3f}%" for name, gain in gains.items()),
                flush=True,
            )
        except Exception as exc:
            errors.append(
                {
                    "index": str(index),
                    "first_job": first_job,
                    "second_job": second_job,
                    "error": repr(exc),
                }
            )
            print(f"[{done}/{len(selected)}] {second_job}: ERROR {exc!r}", flush=True)
        finally:
            if editor is not None:
                editor.deleteLater()
                app.processEvents()

    payload = {
        "window": args.window,
        "seed": args.seed,
        "shard": args.shard,
        "shards": args.shards,
        "schemes": list(SCHEMES),
        "cases": sorted(selected_cases),
        "rows": rows,
        "errors": errors,
    }
    output = args.output or (
        ROOT
        / "outputs"
        / "benchmarks"
        / f"pure_scheme_1_all_jobs_shard_{args.shard}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output}", flush=True)


if __name__ == "__main__":
    main()
