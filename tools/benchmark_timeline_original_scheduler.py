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
from tools.benchmark_timeline_optimizer import (  # noqa: E402
    _prepare_editor,
    _timeline_score,
)


def _run_original_long(
    editor: Any,
    metrics: list[dict[str, Any]],
    window: float,
) -> dict[str, Any]:
    editor._skill_burst_disable_hybrid_optimizer = True
    editor._skill_burst_disable_local_optimizer = True
    editor.__dict__.pop("_skill_burst_force_local_optimizer", None)
    editor._skill_burst_local_optimizer_cache = {}
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
    }


def _run_original_team(
    editor: Any,
    metrics: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    editor._skill_burst_disable_hybrid_optimizer = True
    editor._skill_burst_disable_local_optimizer = True
    editor.__dict__.pop("_skill_burst_force_local_optimizer", None)
    editor._skill_burst_local_optimizer_cache = {}
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
        "effects": len(context.get("effects", [])),
        "effect_windows": len(context.get("windows", [])),
    }


def benchmark_job(
    app: QApplication,
    store: DataStore,
    calculator: BuildCalculator,
    first_job: str,
    second_job: str,
    index: int,
    teams: list[dict[str, Any]],
    window: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    editor, metrics = _prepare_editor(app, store, calculator, first_job, second_job)
    if not metrics:
        raise RuntimeError("技能模拟没有生成任何技能数据")

    long_result = _run_original_long(editor, metrics, window)
    team_trials: list[dict[str, Any]] = []
    for trial in teams:
        team = list(trial["team"])
        editor._apply_team_members(team)
        editor._auto_populate_team_effects_from_members(schedule=False)
        app.processEvents()
        context = editor._team_practical_runtime_context(metrics, window)
        team_trials.append({
            "seed": int(trial["seed"]),
            "team": team,
            "result": _run_original_team(editor, metrics, context),
        })

    row = {
        "index": index,
        "first_job": first_job,
        "second_job": second_job,
        "metric_count": len(metrics),
        "long": long_result,
        "team_trials": team_trials,
        "total_elapsed": time.perf_counter() - started,
    }
    editor.deleteLater()
    app.processEvents()
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the original deterministic scheduler.")
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--job", default="")
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
    indexed_jobs = [
        (index, first, second)
        for index, (first, second) in enumerate(jobs)
        if index % max(1, args.shards) == args.shard
    ]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for done, (index, first, second) in enumerate(indexed_jobs, 1):
        try:
            source_row = source_by_job[second]
            row = benchmark_job(
                app,
                store,
                calculator,
                first,
                second,
                index,
                source_row["trials"],
                args.window,
            )
            rows.append(row)
            team_seconds = sum(item["result"]["elapsed"] for item in row["team_trials"])
            print(
                f"[{done}/{len(indexed_jobs)}] {second}: "
                f"long {row['long']['elapsed']:.2f}s / teams {team_seconds:.2f}s / "
                f"total {row['total_elapsed']:.2f}s",
                flush=True,
            )
        except Exception as exc:
            errors.append({"first_job": first, "second_job": second, "error": repr(exc)})
            print(f"[{done}/{len(indexed_jobs)}] {second}: ERROR {exc!r}", flush=True)

    payload = {
        "window": args.window,
        "shard": args.shard,
        "shards": args.shards,
        "source_teams": str(args.teams),
        "rows": rows,
        "errors": errors,
    }
    output = args.output or ROOT / "outputs" / "benchmarks" / f"timeline_original_shard_{args.shard}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output}", flush=True)


if __name__ == "__main__":
    main()
