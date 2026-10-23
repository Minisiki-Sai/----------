from __future__ import annotations

import argparse
import cProfile
import os
import pstats
import sys
import time
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, norm  # noqa: E402
from tools.benchmark_timeline_optimizer import (  # noqa: E402
    DEFAULT_SEED,
    _make_team,
    _prepare_editor,
    _timeline_score,
)


def _configure(editor: Any, mode: str) -> None:
    editor._skill_burst_disable_new_scheme_1 = mode == "stable"
    editor._skill_burst_enable_new_scheme_1_quality_floor = mode == "guarded"
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


def _profile_call(
    name: str,
    call: Callable[[], tuple[list[dict[str, Any]], float]],
    output_dir: Path,
) -> dict[str, Any]:
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    segments, window = call()
    profiler.disable()
    elapsed = time.perf_counter() - started
    profile_path = output_dir / f"{name}.prof"
    text_path = output_dir / f"{name}.txt"
    profiler.dump_stats(profile_path)
    with text_path.open("w", encoding="utf-8") as handle:
        stats = pstats.Stats(profiler, stream=handle)
        stats.strip_dirs().sort_stats("cumulative").print_stats(45)
    score = _timeline_score(segments, window)
    result = {
        "name": name,
        "elapsed": elapsed,
        "score": score,
        "average": score / max(1e-9, window),
        "segments": len([row for row in segments if not row.get("timeline_hidden")]),
        "window": window,
        "profile": str(profile_path),
        "text": str(text_path),
    }
    print(result, flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-index", type=int, default=22)
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--modes",
        default="stable,pure_new,guarded",
        help="Comma-separated modes to profile.",
    )
    parser.add_argument(
        "--scenarios",
        default="long,team",
        help="Comma-separated scenarios to profile.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs" / "benchmarks" / "pure_scheme_1_profiles",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    pairs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second
    ]
    first_job, second_job = pairs[args.job_index]
    all_jobs = [second for _first, second in pairs]
    print(f"preparing {second_job}", flush=True)
    editor, metrics = _prepare_editor(
        app,
        store,
        calculator,
        first_job,
        second_job,
    )
    team = _make_team(all_jobs, second_job, args.job_index, args.seed)
    _configure(editor, "stable")
    editor._apply_team_members(team)
    editor._auto_populate_team_effects_from_members(schedule=False)
    app.processEvents()
    context = editor._team_practical_runtime_context(metrics, args.window)

    def long_call(mode: str) -> tuple[list[dict[str, Any]], float]:
        _configure(editor, mode)
        editor._skill_burst_schedule_override = editor._skill_long_schedule(args.window)
        editor.__dict__.pop("_skill_burst_mode_override", None)
        try:
            return editor._update_skill_burst_sim_preview(metrics, render_ui=False)
        finally:
            editor.__dict__.pop("_skill_burst_schedule_override", None)

    def team_call(mode: str) -> tuple[list[dict[str, Any]], float]:
        _configure(editor, mode)
        result = editor._team_practical_simulate(metrics, context)
        return list(result.get("segments", [])), float(result.get("window", 0.0))

    modes = [item.strip() for item in args.modes.split(",") if item.strip()]
    scenarios = {item.strip() for item in args.scenarios.split(",") if item.strip()}
    if "long" in scenarios:
        for mode in modes:
            _profile_call(
                f"{args.job_index}_{norm(second_job)}_long_{mode}",
                lambda selected=mode: long_call(selected),
                args.output_dir,
            )
    if "team" in scenarios:
        for mode in modes:
            _profile_call(
                f"{args.job_index}_{norm(second_job)}_team_{mode}",
                lambda selected=mode: team_call(selected),
                args.output_dir,
            )


if __name__ == "__main__":
    main()
