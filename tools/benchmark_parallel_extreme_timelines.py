from __future__ import annotations

import argparse
import copy
import hashlib
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
from dragon_nest_builder.timeline_parallel import reset_timeline_process_pool  # noqa: E402
from tools.benchmark_timeline_optimizer import _prepare_editor  # noqa: E402


DEFAULT_TEAM = [
    "剑皇",
    "圣骑士",
    "圣徒",
    "冰灵",
    "时空领主",
    "影舞者",
    "噬魂者",
    "机械大师",
]


def axis_signature(result: dict[str, Any]) -> str:
    def number(value: Any) -> float:
        rounded = float(round(as_number(value), 8))
        return 0.0 if rounded == 0 else rounded

    canonical = [
        (
            norm(row.get("skill")),
            norm(row.get("kind")),
            number(row.get("start")),
            number(row.get("end")),
            number(row.get("effective_score")),
            number(row.get("actual_damage")),
        )
        for row in result.get("segments", [])
    ]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def first_axis_difference(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    left_rows = list(left.get("segments", []))
    right_rows = list(right.get("segments", []))
    for index in range(max(len(left_rows), len(right_rows))):
        old = left_rows[index] if index < len(left_rows) else None
        new = right_rows[index] if index < len(right_rows) else None
        old_key = (
            norm(old.get("skill")) if old else "",
            norm(old.get("kind")) if old else "",
            round(as_number(old.get("start")), 8) if old else -1,
            round(as_number(old.get("end")), 8) if old else -1,
            round(as_number(old.get("effective_score")), 8) if old else -1,
            round(as_number(old.get("raw_effective_score")), 8) if old else -1,
            round(as_number(old.get("actual_damage")), 8) if old else -1,
        )
        new_key = (
            norm(new.get("skill")) if new else "",
            norm(new.get("kind")) if new else "",
            round(as_number(new.get("start")), 8) if new else -1,
            round(as_number(new.get("end")), 8) if new else -1,
            round(as_number(new.get("effective_score")), 8) if new else -1,
            round(as_number(new.get("raw_effective_score")), 8) if new else -1,
            round(as_number(new.get("actual_damage")), 8) if new else -1,
        )
        if old_key != new_key:
            return {"index": index, "serial": old_key, "parallel": new_key}
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--window", type=float, default=125.0)
    parser.add_argument("--skip-serial", action="store_true")
    parser.add_argument("--fast-long", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor, _metrics = _prepare_editor(app, store, calculator, "剑圣", "剑皇")
    team = DEFAULT_TEAM[: max(1, min(8, args.jobs))]
    effects = editor._extreme_team_effects_for_jobs(team)
    specs = [
        {
            "job": job,
            "team": team,
            "effects": effects,
            "hand_multiplier": 1.0,
            "window": args.window,
            "include_display_windows": True,
            "fast_long_timeline": args.fast_long,
        }
        for job in team
    ]
    serial: list[dict[str, Any]] = []
    serial_elapsed = 0.0
    if not args.skip_serial:
        started = time.perf_counter()
        serial = [
            editor._extreme_team_timeline_result(**copy.deepcopy(spec))
            for spec in specs
        ]
        serial_elapsed = time.perf_counter() - started
    editor._extreme_timeline_result_cache = {}
    started = time.perf_counter()
    parallel = editor._extreme_timeline_tasks_parallel(specs)
    parallel_elapsed = time.perf_counter() - started
    exact = not serial or [axis_signature(row) for row in serial] == [
        axis_signature(row) for row in parallel
    ]
    serial_sync: list[dict[str, Any]] = []
    serial_sync_elapsed = 0.0
    parallel_sync_elapsed = 0.0
    parallel_sync: list[dict[str, Any]] = []
    if serial:
        cast_schedule = editor._extreme_timeline_cast_schedule(serial)
        sync_specs = [
            {**copy.deepcopy(spec), "cast_schedule": cast_schedule}
            for spec in specs
        ]
        started = time.perf_counter()
        serial_sync = [
            editor._extreme_team_timeline_result(**copy.deepcopy(spec))
            for spec in sync_specs
        ]
        serial_sync_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        parallel_sync = editor._extreme_timeline_tasks_parallel(sync_specs)
        parallel_sync_elapsed = time.perf_counter() - started
        exact = exact and [axis_signature(row) for row in serial_sync] == [
            axis_signature(row) for row in parallel_sync
        ]
    payload = {
        "team": team,
        "window": args.window,
        "exact": exact,
        "serial_elapsed": serial_elapsed,
        "parallel_elapsed": parallel_elapsed,
        "speedup": serial_elapsed / parallel_elapsed if serial_elapsed > 0 and parallel_elapsed > 0 else 0.0,
        "serial_sync_elapsed": serial_sync_elapsed,
        "parallel_sync_elapsed": parallel_sync_elapsed,
        "sync_speedup": serial_sync_elapsed / parallel_sync_elapsed if serial_sync_elapsed > 0 and parallel_sync_elapsed > 0 else 0.0,
        "fallback": norm(getattr(editor, "_extreme_parallel_last_fallback", "")),
        "serial_signatures": [axis_signature(row) for row in serial],
        "parallel_signatures": [axis_signature(row) for row in parallel],
        "comparisons": [
            {
                "job": job,
                "serial_total": as_number(old.get("total")),
                "parallel_total": as_number(new.get("total")),
                "serial_segments": len(old.get("segments", [])),
                "parallel_segments": len(new.get("segments", [])),
                "first_difference": first_axis_difference(old, new),
            }
            for job, old, new in zip(team, serial, parallel)
        ],
    }
    output = args.output or ROOT / "outputs" / "verification" / "parallel_extreme_timelines.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    reset_timeline_process_pool()
    editor.deleteLater()
    app.processEvents()
    raise SystemExit(0 if exact and not payload["fallback"] else 1)


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
