from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore  # noqa: E402
from tools.benchmark_timeline_optimizer import _prepare_editor  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Limit the candidate job pool for A/B checks")
    parser.add_argument("--no-prefilter", action="store_true")
    parser.add_argument("--no-roles", action="store_true")
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor, _metrics = _prepare_editor(app, store, calculator, "剑圣", "剑皇")
    all_jobs = [job for job in editor._team_job_options() if job and job != "无"]
    jobs = all_jobs[: args.limit] if args.limit > 0 else all_jobs
    if "剑皇" not in jobs:
        jobs.insert(0, "剑皇")
    editor._team_job_options = lambda: ["无", *jobs]
    editor._extreme_disable_static_prefilter = bool(args.no_prefilter)
    editor._show_extreme_team_dialog = lambda _jobs, _current: {
        "hand_multipliers": {job: 1.0 for job in jobs},
        "team_mode": "不限",
        "boss": "无",
        "require_tank": not args.no_roles,
        "require_healer": not args.no_roles,
        "replacement_mode": "快速",
        "replacement_candidates": 0,
        "fixed_jobs": [],
    }
    captured: dict[str, Any] = {}

    def capture_result(team, total_score, *_args, **_kwargs) -> None:
        captured["team"] = list(team)
        captured["score"] = float(total_score)

    editor._show_extreme_team_result_dialog = capture_result
    original_warning = QMessageBox.warning
    original_information = QMessageBox.information
    QMessageBox.warning = lambda *_args, **_kwargs: QMessageBox.Ok
    QMessageBox.information = lambda *_args, **_kwargs: QMessageBox.Ok
    started = time.perf_counter()
    try:
        editor.auto_extreme_team()
    finally:
        QMessageBox.warning = original_warning
        QMessageBox.information = original_information
    elapsed = time.perf_counter() - started
    print({
        "elapsed": elapsed,
        "candidate_jobs": len(jobs),
        "prefilter": not args.no_prefilter,
        "team": captured.get("team"),
        "score": captured.get("score"),
        "diagnostics": getattr(editor, "_extreme_search_diagnostics", {}),
    })
    editor.deleteLater()


if __name__ == "__main__":
    main()
