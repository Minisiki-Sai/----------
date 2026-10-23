from __future__ import annotations

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
from dragon_nest_builder.data_loader import DataStore, as_number  # noqa: E402
from tools.benchmark_timeline_optimizer import _prepare_editor, _timeline_score  # noqa: E402


JOBS = ["\u70c8", "\u5f71", "\u5200\u950b\u821e\u8005", "\u7075\u9b42\u821e\u8005"]
TEAM = [
    "\u70c8",
    "\u5f71",
    "\u5200\u950b\u821e\u8005",
    "\u7075\u9b42\u821e\u8005",
    "\u5723\u5f92",
    "\u51b0\u7075",
    "\u65f6\u7a7a\u9886\u4e3b",
    "\u673a\u68b0\u5927\u5e08",
]
BLOCKED = [
    {"start": 60.0, "end": 90.0},
    {"start": 180.0, "end": 215.0},
    {"start": 360.0, "end": 400.0},
    {"start": 520.0, "end": 545.0},
]


def score_rows(rows: list[dict[str, Any]], window: float) -> float:
    return _timeline_score(rows, window)


def gain(old: float, new: float) -> float:
    return (new / old - 1.0) * 100.0 if old > 1e-9 else 0.0


def set_scheduler(editor: Any, enabled: bool) -> None:
    editor._skill_burst_disable_short_personal_burst_scheduler = not enabled
    editor._skill_burst_new_scheme_1_result_cache = {}
    editor._skill_burst_local_optimizer_cache = {}
    editor._team_practical_simulation_cache = {}
    editor._extreme_timeline_result_cache = {}
    editor.__dict__.pop("_timeline_pure_result_cache", None)
    editor.__dict__.pop("_extreme_parallel_snapshot", None)


def run_long(editor: Any, metrics: list[dict[str, Any]], enabled: bool) -> dict[str, float]:
    set_scheduler(editor, enabled)
    editor._skill_burst_schedule_override = editor._skill_long_schedule(125.0)
    started = time.perf_counter()
    try:
        segments, window = editor._update_skill_burst_sim_preview(metrics, render_ui=False)
    finally:
        editor.__dict__.pop("_skill_burst_schedule_override", None)
    return {
        "score": score_rows(segments, window),
        "elapsed": time.perf_counter() - started,
    }


def run_personal_team(
    editor: Any,
    metrics: list[dict[str, Any]],
    enabled: bool,
) -> dict[str, float]:
    set_scheduler(editor, enabled)
    editor._apply_team_members(TEAM)
    editor._auto_populate_team_effects_from_members(schedule=False)
    QApplication.processEvents()
    context = editor._team_practical_runtime_context(metrics, 125.0)
    started = time.perf_counter()
    result = editor._team_practical_simulate(metrics, context)
    window = max(0.0, as_number(result.get("window")))
    return {
        "score": score_rows(list(result.get("segments", [])), window),
        "elapsed": time.perf_counter() - started,
    }


def run_extreme(
    editor: Any,
    effects: list[dict[str, Any]],
    job: str,
    enabled: bool,
    *,
    window: float,
    blocked: list[dict[str, float]] | None = None,
) -> dict[str, float]:
    set_scheduler(editor, enabled)
    started = time.perf_counter()
    result = editor._extreme_team_timeline_result(
        job,
        TEAM,
        effects,
        1.0,
        window,
        include_display_windows=False,
        blocked_intervals=copy.deepcopy(blocked or []),
        fast_long_timeline=bool(blocked),
    )
    return {
        "score": max(0.0, as_number(result.get("total"))),
        "elapsed": time.perf_counter() - started,
    }


def paired(old: dict[str, float], new: dict[str, float]) -> dict[str, float]:
    return {
        "old_score": old["score"],
        "new_score": new["score"],
        "gain_percent": gain(old["score"], new["score"]),
        "old_elapsed": old["elapsed"],
        "new_elapsed": new["elapsed"],
    }


def main() -> None:
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    job_pairs = {
        second: (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second in JOBS
    }
    rows: list[dict[str, Any]] = []
    extreme_editor = None
    try:
        first_job, second_job = job_pairs[JOBS[0]]
        extreme_editor, _metrics = _prepare_editor(
            app,
            store,
            calculator,
            first_job,
            second_job,
        )
        effects = extreme_editor._extreme_team_effects_for_jobs(TEAM)
        for index, job in enumerate(JOBS, 1):
            first_job, second_job = job_pairs[job]
            editor, metrics = _prepare_editor(
                app,
                store,
                calculator,
                first_job,
                second_job,
            )
            try:
                long_old = run_long(editor, metrics, False)
                long_new = run_long(editor, metrics, True)
                print(
                    f"[{index}/{len(JOBS)}] {job} long_125 "
                    f"{gain(long_old['score'], long_new['score']):+.3f}% "
                    f"({long_old['elapsed']:.2f}s/{long_new['elapsed']:.2f}s)",
                    flush=True,
                )
                team_old = run_personal_team(editor, metrics, False)
                team_new = run_personal_team(editor, metrics, True)
                print(
                    f"[{index}/{len(JOBS)}] {job} personal_team_125 "
                    f"{gain(team_old['score'], team_new['score']):+.3f}% "
                    f"({team_old['elapsed']:.2f}s/{team_new['elapsed']:.2f}s)",
                    flush=True,
                )
            finally:
                editor.deleteLater()
                app.processEvents()
            axis_old = run_extreme(
                extreme_editor,
                effects,
                job,
                False,
                window=125.0,
            )
            axis_new = run_extreme(
                extreme_editor,
                effects,
                job,
                True,
                window=125.0,
            )
            print(
                f"[{index}/{len(JOBS)}] {job} extreme_team_125 "
                f"{gain(axis_old['score'], axis_new['score']):+.3f}% "
                f"({axis_old['elapsed']:.2f}s/{axis_new['elapsed']:.2f}s)",
                flush=True,
            )
            custom_old = run_extreme(
                extreme_editor,
                effects,
                job,
                False,
                window=600.0,
                blocked=BLOCKED,
            )
            custom_new = run_extreme(
                extreme_editor,
                effects,
                job,
                True,
                window=600.0,
                blocked=BLOCKED,
            )
            print(
                f"[{index}/{len(JOBS)}] {job} custom_mechanism_600 "
                f"{gain(custom_old['score'], custom_new['score']):+.3f}% "
                f"({custom_old['elapsed']:.2f}s/{custom_new['elapsed']:.2f}s)",
                flush=True,
            )
            cases = {
                "long_125": paired(long_old, long_new),
                "personal_team_125": paired(team_old, team_new),
                "extreme_team_125": paired(axis_old, axis_new),
                "custom_mechanism_600": paired(custom_old, custom_new),
            }
            rows.append({"job": job, "cases": cases})
            print(
                f"[{index}/{len(JOBS)}] {job}: "
                + ", ".join(
                    f"{name} {value['gain_percent']:+.3f}%"
                    for name, value in cases.items()
                ),
                flush=True,
            )
    finally:
        if extreme_editor is not None:
            extreme_editor.deleteLater()
            app.processEvents()

    case_names = [
        "long_125",
        "personal_team_125",
        "extreme_team_125",
        "custom_mechanism_600",
    ]
    summary = {}
    for name in case_names:
        old_total = sum(row["cases"][name]["old_score"] for row in rows)
        new_total = sum(row["cases"][name]["new_score"] for row in rows)
        summary[name] = {
            "old_score": old_total,
            "new_score": new_total,
            "gain_percent": gain(old_total, new_total),
            "minimum_job_gain_percent": min(
                row["cases"][name]["gain_percent"] for row in rows
            ),
        }
    payload = {
        "jobs": JOBS,
        "team": TEAM,
        "blocked_intervals": BLOCKED,
        "rows": rows,
        "summary": summary,
    }
    output = ROOT / "outputs" / "benchmarks" / "short_personal_burst_scheduler.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved: {output}", flush=True)


if __name__ == "__main__":
    main()
