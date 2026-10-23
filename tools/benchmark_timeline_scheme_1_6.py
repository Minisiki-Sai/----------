from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, norm  # noqa: E402
from tools.benchmark_timeline_optimizer import (  # noqa: E402
    _prepare_editor,
    _timeline_score,
)


TARGET_JOBS = (
    "圣骑士",
    "十字军",
    "噬魂者",
    "黑暗萨满",
    "刀锋舞者",
    "灵魂舞者",
    "剑皇",
)
SCHEMES = ("scheme_1", "scheme_3", "scheme_1_5", "scheme_1_6")
SHORT_MODES = {
    "burst": {"burst"},
    "accel": {"accel"},
    "burst_cocktail": {"burst", "cocktail"},
    "accel_burst": {"accel", "burst"},
}


def _configure(editor: Any, scheme: str) -> None:
    editor._skill_burst_disable_hybrid_optimizer = True
    editor._skill_burst_force_conservative_1_5 = scheme == "scheme_1_5"
    editor._skill_burst_force_conservative_1_6 = scheme == "scheme_1_6"
    editor._skill_burst_disable_conservative_1_6 = scheme != "scheme_1_6"
    editor._skill_burst_force_local_optimizer = scheme == "scheme_3"
    editor._skill_burst_disable_local_optimizer = scheme != "scheme_3"
    editor._skill_burst_local_optimizer_cache = {}
    editor._skill_burst_conservative_1_6_cache = {}


def _stats(editor: Any, scheme: str) -> dict[str, Any]:
    if scheme == "scheme_1_6":
        source = getattr(editor, "_skill_burst_conservative_1_6_last_stats", {})
    elif scheme == "scheme_3":
        source = getattr(editor, "_skill_burst_local_optimizer_last_stats", {})
    else:
        source = {}
    return {
        "enabled": bool(source.get("enabled")),
        "evaluations": int(source.get("evaluations", 0)),
        "accepted": int(source.get("accepted", 0)),
        "accepted_moves": list(source.get("accepted_moves", [])),
        "gain_percent": float(source.get("gain_percent", 0.0)),
        "elapsed": float(source.get("elapsed", 0.0)),
        "cached": bool(source.get("cached")),
    }


def _result(
    editor: Any,
    segments: list[dict[str, Any]],
    window: float,
    elapsed: float,
    scheme: str,
) -> dict[str, Any]:
    score = _timeline_score(segments, window)
    visible = [
        segment
        for segment in segments
        if not segment.get("timeline_hidden")
        and norm(segment.get("kind")) != "invalid"
    ]
    return {
        "score": score,
        "average": score / max(1e-9, window),
        "window": window,
        "segments": len(visible),
        "elapsed": elapsed,
        "optimizer": _stats(editor, scheme),
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
    return _result(
        editor,
        segments,
        generated_window,
        time.perf_counter() - started,
        scheme,
    )


def _run_short_mode(
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
    return _result(
        editor,
        segments,
        generated_window,
        time.perf_counter() - started,
        scheme,
    )


def _run_team(
    editor: Any,
    metrics: list[dict[str, Any]],
    context: dict[str, Any],
    scheme: str,
) -> dict[str, Any]:
    _configure(editor, scheme)
    started = time.perf_counter()
    result = editor._team_practical_simulate(metrics, context)
    segments = list(result.get("segments", []))
    window = float(result.get("window", 0.0))
    return _result(
        editor,
        segments,
        window,
        time.perf_counter() - started,
        scheme,
    )


def _gain(base: float, value: float) -> float:
    return (value / base - 1.0) * 100.0 if base > 1e-9 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare timeline Schemes 1, 3, 1.5 and conservative 1.6."
    )
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--job", default="")
    parser.add_argument("--team-limit", type=int, default=4)
    parser.add_argument(
        "--teams",
        type=Path,
        default=ROOT
        / "outputs"
        / "benchmarks"
        / "timeline_optimizer_four_teams_summary.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "outputs"
        / "benchmarks"
        / "timeline_scheme_1_6_comparison.json",
    )
    args = parser.parse_args()

    team_source = json.loads(args.teams.read_text(encoding="utf-8"))
    teams_by_job = {row["second_job"]: row for row in team_source["rows"]}
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    jobs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second in TARGET_JOBS
        and (not args.job or norm(second) == norm(args.job))
    ]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for done, (first_job, second_job) in enumerate(jobs, 1):
        editor = None
        started = time.perf_counter()
        try:
            editor, metrics = _prepare_editor(
                app,
                store,
                calculator,
                first_job,
                second_job,
            )
            trials = list(teams_by_job[second_job]["trials"])[: max(0, args.team_limit)]

            print(f"[{done}/{len(jobs)}] {second_job}: long-axis", flush=True)
            long_results = {
                scheme: _run_long(editor, metrics, args.window, scheme)
                for scheme in SCHEMES
            }
            print(f"[{done}/{len(jobs)}] {second_job}: short modes", flush=True)
            short_results = {
                mode_name: {
                    scheme: _run_short_mode(editor, metrics, modes, scheme)
                    for scheme in SCHEMES
                }
                for mode_name, modes in SHORT_MODES.items()
            }
            team_results = []
            for trial in trials:
                team = list(trial["team"])
                # UI signal processing while changing team members must not
                # inherit the previously benchmarked optimizer mode.
                _configure(editor, "scheme_1")
                editor._apply_team_members(team)
                editor._auto_populate_team_effects_from_members(schedule=False)
                print(
                    f"[{done}/{len(jobs)}] {second_job}: team seed {trial['seed']}",
                    flush=True,
                )
                context = editor._team_practical_runtime_context(metrics, args.window)
                results = {
                    scheme: _run_team(editor, metrics, context, scheme)
                    for scheme in SCHEMES
                }
                team_results.append({
                    "seed": trial["seed"],
                    "team": team,
                    "results": results,
                    "gain_vs_scheme_1": {
                        scheme: _gain(
                            results["scheme_1"]["score"],
                            results[scheme]["score"],
                        )
                        for scheme in SCHEMES[1:]
                    },
                })
            row = {
                "first_job": first_job,
                "second_job": second_job,
                "metric_count": len(metrics),
                "long": long_results,
                "long_gain_vs_scheme_1": {
                    scheme: _gain(
                        long_results["scheme_1"]["score"],
                        long_results[scheme]["score"],
                    )
                    for scheme in SCHEMES[1:]
                },
                "short": short_results,
                "short_gain_vs_scheme_1": {
                    mode_name: {
                        scheme: _gain(
                            results["scheme_1"]["score"],
                            results[scheme]["score"],
                        )
                        for scheme in SCHEMES[1:]
                    }
                    for mode_name, results in short_results.items()
                },
                "team_trials": team_results,
                "elapsed": time.perf_counter() - started,
            }
            rows.append(row)
            team_gains = [
                trial["gain_vs_scheme_1"]["scheme_1_6"]
                for trial in team_results
            ]
            mean_team = (
                sum(team_gains) / len(team_gains)
                if team_gains
                else 0.0
            )
            print(
                f"[{done}/{len(jobs)}] {second_job}: "
                f"long 1.6 {row['long_gain_vs_scheme_1']['scheme_1_6']:+.4f}% / "
                f"team 1.6 mean {mean_team:+.4f}% / "
                f"{row['elapsed']:.2f}s",
                flush=True,
            )
        except Exception as exc:
            errors.append({
                "first_job": first_job,
                "second_job": second_job,
                "error": repr(exc),
            })
            print(f"[{done}/{len(jobs)}] {second_job}: ERROR {exc!r}", flush=True)
        finally:
            if editor is not None:
                editor.deleteLater()
                app.processEvents()

    payload = {
        "window": args.window,
        "schemes": list(SCHEMES),
        "team_limit": args.team_limit,
        "rows": rows,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved: {args.output}", flush=True)


if __name__ == "__main__":
    main()
