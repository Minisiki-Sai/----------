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
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from dragon_nest_builder.editor import BuildEditor  # noqa: E402
from dragon_nest_builder.timeline_parallel import reset_timeline_process_pool  # noqa: E402


def _all_jobs(store: DataStore) -> list[str]:
    return [
        second
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if norm(second)
    ]


def _specs(
    editor: BuildEditor,
    jobs: list[str],
    window: float,
    *,
    fast: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for job in jobs:
        team = [job]
        rows.append(
            {
                "job": job,
                "team": team,
                "effects": editor._extreme_team_effects_for_jobs(team),
                "hand_multiplier": 1.0,
                "window": window,
                "include_display_windows": False,
                "fast_long_timeline": fast,
            }
        )
    return rows


def _run(
    editor: BuildEditor,
    specs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    reset_timeline_process_pool()
    editor._extreme_timeline_result_cache = {}
    started = time.perf_counter()
    results = editor._extreme_timeline_tasks_parallel(specs)
    return results, time.perf_counter() - started


def _skill_summary(result: dict[str, Any], skill_name: str) -> dict[str, float]:
    rows = [
        row
        for row in result.get("segments", ())
        if norm(row.get("skill")).replace("EX", "") == skill_name
    ]
    return {
        "casts": len(rows),
        "score": sum(max(0.0, as_number(row.get("effective_score"))) for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=float, default=125.0)
    parser.add_argument("--jobs", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    selected = {norm(row) for row in args.jobs.split(",") if norm(row)}
    jobs = [job for job in _all_jobs(store) if not selected or norm(job) in selected]
    calculator = BuildCalculator(store)
    editor = BuildEditor(
        "score-planner-benchmark",
        store,
        calculator,
        store.defaults["build1"],
        standardize_default=True,
    )

    editor._skill_burst_fast_mechanism_refinement = False
    fast_results, fast_elapsed = _run(
        editor,
        _specs(editor, jobs, args.window, fast=True),
    )
    editor._skill_burst_fast_mechanism_refinement = True
    editor._skill_burst_fast_mechanism_refinement_seconds = 0.12
    editor._skill_burst_fast_mechanism_refinement_evaluations = 4
    refined_results, refined_elapsed = _run(
        editor,
        _specs(editor, jobs, args.window, fast=True),
    )
    editor._skill_burst_fast_mechanism_refinement = False
    full_results, full_elapsed = _run(
        editor,
        _specs(editor, jobs, args.window, fast=False),
    )
    serial_fast_elapsed = 0.0
    serial_full_elapsed = 0.0
    fast_specs = _specs(editor, jobs, args.window, fast=True)
    full_specs = _specs(editor, jobs, args.window, fast=False)
    editor._extreme_timeline_result_cache = {}
    for fast_spec, full_spec in zip(fast_specs, full_specs):
        started = time.perf_counter()
        editor._extreme_timeline_result_from_spec(fast_spec)
        serial_fast_elapsed += time.perf_counter() - started
        started = time.perf_counter()
        editor._extreme_timeline_result_from_spec(full_spec)
        serial_full_elapsed += time.perf_counter() - started
    rows: list[dict[str, Any]] = []
    for job, fast, refined, full in zip(
        jobs, fast_results, refined_results, full_results
    ):
        fast_total = max(0.0, as_number(fast.get("total")))
        refined_total = max(0.0, as_number(refined.get("total")))
        full_total = max(0.0, as_number(full.get("total")))
        rows.append(
            {
                "job": job,
                "fast_total": fast_total,
                "refined_total": refined_total,
                "full_total": full_total,
                "refined_gain_percent": (
                    (refined_total / fast_total - 1.0) * 100.0
                    if fast_total > 1e-9
                    else 0.0
                ),
                "full_gain_percent": (
                    (full_total / fast_total - 1.0) * 100.0
                    if fast_total > 1e-9
                    else 0.0
                ),
                "fast_segments": len(fast.get("segments", ())),
                "refined_segments": len(refined.get("segments", ())),
                "full_segments": len(full.get("segments", ())),
                "fast_holy_punishment": _skill_summary(fast, "神圣惩戒"),
                "refined_holy_punishment": _skill_summary(refined, "神圣惩戒"),
                "full_holy_punishment": _skill_summary(full, "神圣惩戒"),
            }
        )

    payload = {
        "window": args.window,
        "jobs": len(jobs),
        "fast_elapsed": fast_elapsed,
        "refined_elapsed": refined_elapsed,
        "full_elapsed": full_elapsed,
        "refined_time_ratio": (
            refined_elapsed / fast_elapsed if fast_elapsed > 0 else 0.0
        ),
        "full_time_ratio": full_elapsed / fast_elapsed if fast_elapsed > 0 else 0.0,
        "serial_fast_elapsed": serial_fast_elapsed,
        "serial_full_elapsed": serial_full_elapsed,
        "serial_full_time_ratio": (
            serial_full_elapsed / serial_fast_elapsed
            if serial_fast_elapsed > 0
            else 0.0
        ),
        "fast_total": sum(row["fast_total"] for row in rows),
        "refined_total": sum(row["refined_total"] for row in rows),
        "full_total": sum(row["full_total"] for row in rows),
        "refined_gain_percent": (
            sum(row["refined_total"] for row in rows)
            / sum(row["fast_total"] for row in rows)
            * 100.0
            - 100.0
            if sum(row["fast_total"] for row in rows) > 1e-9
            else 0.0
        ),
        "aggregate_gain_percent": (
            sum(row["full_total"] for row in rows)
            / sum(row["fast_total"] for row in rows)
            * 100.0
            - 100.0
            if sum(row["fast_total"] for row in rows) > 1e-9
            else 0.0
        ),
        "improved_jobs": sum(row["full_gain_percent"] > 0.01 for row in rows),
        "regressed_jobs": sum(row["full_gain_percent"] < -0.01 for row in rows),
        "refined_improved_jobs": sum(
            row["refined_gain_percent"] > 0.01 for row in rows
        ),
        "refined_regressed_jobs": sum(
            row["refined_gain_percent"] < -0.01 for row in rows
        ),
        "rows": sorted(rows, key=lambda row: row["full_gain_percent"], reverse=True),
    }
    output = args.output or (
        ROOT / "outputs" / "verification" / "score_fast_full_planners.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)

    reset_timeline_process_pool()
    editor.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
