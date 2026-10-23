from __future__ import annotations

import argparse
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
from tools.benchmark_timeline_optimizer import _prepare_editor, _timeline_score  # noqa: E402


def _configure(editor: Any, enabled: bool, production_stack: bool = False) -> None:
    editor._skill_burst_collect_baseline_diagnostics = True
    editor._skill_burst_disable_hybrid_optimizer = not production_stack
    editor._skill_burst_disable_local_optimizer = not production_stack
    if production_stack and enabled:
        editor.__dict__.pop("_skill_burst_force_conservative_1_5", None)
        editor.__dict__.pop("_skill_burst_force_refactored_baseline", None)
    else:
        editor._skill_burst_force_conservative_1_5 = enabled
    editor.__dict__.pop("_skill_burst_force_local_optimizer", None)


def _baseline_diagnostics(segments: list[dict[str, Any]]) -> dict[str, Any]:
    by_skill: dict[str, dict[str, Any]] = {}
    for segment in segments:
        detail = segment.get("baseline_rank")
        if not isinstance(detail, dict) or detail.get("policy") != "conservative_1_5":
            continue
        legacy = max(0.0, float(detail.get("legacy_rank_dpa") or 0.0))
        package = max(0.0, float(detail.get("combo_package_dpa") or 0.0))
        if package <= legacy + 1e-9:
            continue
        skill = norm(segment.get("skill"))
        row = by_skill.setdefault(skill, {
            "count": 0,
            "max_ratio": 0.0,
            "paths": {},
        })
        row["count"] += 1
        row["max_ratio"] = max(row["max_ratio"], package / legacy if legacy > 1e-9 else 0.0)
        path = " -> ".join(str(value) for value in detail.get("combo_path", ()))
        row["paths"][path] = int(row["paths"].get(path, 0)) + 1
    return by_skill


def _run_long(
    editor: Any,
    metrics: list[dict[str, Any]],
    window: float,
    enabled: bool,
    production_stack: bool = False,
) -> dict[str, Any]:
    _configure(editor, enabled, production_stack)
    editor._skill_burst_schedule_override = editor._skill_long_schedule(window)
    editor.__dict__.pop("_skill_burst_mode_override", None)
    started = time.perf_counter()
    try:
        segments, generated_window = editor._update_skill_burst_sim_preview(metrics, render_ui=False)
    finally:
        editor.__dict__.pop("_skill_burst_schedule_override", None)
    score = _timeline_score(segments, generated_window)
    return {
        "score": score,
        "average": score / max(1e-9, generated_window),
        "segments": sum(1 for item in segments if not item.get("timeline_hidden")),
        "window": generated_window,
        "elapsed": time.perf_counter() - started,
        "baseline_diagnostics": _baseline_diagnostics(segments),
    }


def _run_team(
    editor: Any,
    metrics: list[dict[str, Any]],
    context: dict[str, Any],
    enabled: bool,
    production_stack: bool = False,
) -> dict[str, Any]:
    _configure(editor, enabled, production_stack)
    started = time.perf_counter()
    result = editor._team_practical_simulate(metrics, context)
    segments = list(result.get("segments", []))
    window = float(result.get("window", 0.0))
    score = _timeline_score(segments, window)
    return {
        "score": score,
        "average": score / max(1e-9, window),
        "segments": sum(1 for item in segments if not item.get("timeline_hidden")),
        "window": window,
        "elapsed": time.perf_counter() - started,
        "baseline_diagnostics": _baseline_diagnostics(segments),
    }


def _gain(before: float, after: float) -> float:
    return (after / before - 1.0) * 100.0 if before > 1e-9 else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Scheme 1 and conservative Scheme 1.5 schedulers.")
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--job", default="")
    parser.add_argument("--team-limit", type=int, default=-1, help="Limit team trials; 0 runs long-axis only.")
    parser.add_argument("--production-stack", action="store_true", help="Keep production hybrid/local optimizers enabled.")
    parser.add_argument("--teams", type=Path, default=ROOT / "outputs" / "benchmarks" / "timeline_optimizer_four_teams_summary.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    source = json.loads(args.teams.read_text(encoding="utf-8"))
    source_by_job = {row["second_job"]: row for row in source["rows"]}
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    jobs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second and second != "无"
    ]
    if args.job:
        jobs = [item for item in jobs if norm(item[1]) == norm(args.job)]
    indexed = [
        (index, first, second)
        for index, (first, second) in enumerate(jobs)
        if index % max(1, args.shards) == args.shard
    ]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for done, (index, first, second) in enumerate(indexed, 1):
        try:
            started = time.perf_counter()
            editor, metrics = _prepare_editor(app, store, calculator, first, second)
            source_row = source_by_job[second]
            # Match the previous long-axis benchmark state, including output曜.
            first_team = list(source_row["trials"][0]["team"])
            editor._apply_team_members(first_team)
            editor._auto_populate_team_effects_from_members(schedule=False)
            app.processEvents()
            editor._team_practical_runtime_context(metrics, args.window)
            long_before = _run_long(editor, metrics, args.window, False, args.production_stack)
            long_after = _run_long(editor, metrics, args.window, True, args.production_stack)

            trials: list[dict[str, Any]] = []
            trials_source = list(source_row["trials"])
            if args.team_limit >= 0:
                trials_source = trials_source[:args.team_limit]
            for trial in trials_source:
                team = list(trial["team"])
                editor._apply_team_members(team)
                editor._auto_populate_team_effects_from_members(schedule=False)
                app.processEvents()
                context = editor._team_practical_runtime_context(metrics, args.window)
                before = _run_team(editor, metrics, context, False, args.production_stack)
                after = _run_team(editor, metrics, context, True, args.production_stack)
                trials.append({
                    "seed": trial["seed"],
                    "team": team,
                    "before": before,
                    "after": after,
                    "gain_percent": _gain(before["score"], after["score"]),
                })
            row = {
                "index": index,
                "first_job": first,
                "second_job": second,
                "metric_count": len(metrics),
                "long_before": long_before,
                "long_after": long_after,
                "long_gain_percent": _gain(long_before["score"], long_after["score"]),
                "team_trials": trials,
                "total_elapsed": time.perf_counter() - started,
            }
            rows.append(row)
            gains = [trial["gain_percent"] for trial in trials]
            team_summary = (
                f"team mean {sum(gains) / len(gains):+.4f}% / "
                f"range {min(gains):+.4f}%..{max(gains):+.4f}%"
                if gains else "team skipped"
            )
            print(
                f"[{done}/{len(indexed)}] {second}: long {row['long_gain_percent']:+.4f}% / "
                f"{team_summary} / {row['total_elapsed']:.2f}s",
                flush=True,
            )
            editor.deleteLater()
            app.processEvents()
        except Exception as exc:
            errors.append({"first_job": first, "second_job": second, "error": repr(exc)})
            print(f"[{done}/{len(indexed)}] {second}: ERROR {exc!r}", flush=True)

    payload = {
        "window": args.window,
        "shard": args.shard,
        "shards": args.shards,
        "rows": rows,
        "errors": errors,
    }
    output = args.output or ROOT / "outputs" / "benchmarks" / f"baseline_refactor_shard_{args.shard}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output}", flush=True)


if __name__ == "__main__":
    main()
