from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pickle
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
from tools.benchmark_timeline_optimizer import (  # noqa: E402
    DEFAULT_SEED,
    _make_team,
    _prepare_editor,
)


def _axis_signature(rows: list[dict[str, Any]]) -> str:
    canonical = [
        (
            norm(row.get("skill")),
            norm(row.get("kind")),
            round(as_number(row.get("start")), 8),
            round(as_number(row.get("end")), 8),
            round(as_number(row.get("effective_score")), 8),
            round(as_number(row.get("raw_effective_score")), 8),
            round(as_number(row.get("actual_damage")), 8),
        )
        for row in rows
    ]
    return hashlib.sha256(
        pickle.dumps(canonical, protocol=pickle.HIGHEST_PROTOCOL)
    ).hexdigest()


def _compare_axis(
    editor: Any,
    metrics: list[dict[str, Any]],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    editor._skill_burst_schedule_override = copy.deepcopy(schedule)
    editor.__dict__.pop("_skill_burst_mode_override", None)
    started = time.perf_counter()
    legacy, legacy_window = editor._calculate_skill_burst_sim_preview_data(
        copy.deepcopy(metrics),
        render_ui=False,
    )
    legacy_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    pure = editor._calculate_skill_burst_pure_result(
        copy.deepcopy(metrics),
        manual_sequence=None,
    )
    pure_elapsed = time.perf_counter() - started
    editor.__dict__.pop("_skill_burst_schedule_override", None)
    new_rows = list(pure.get("auto_segments", []))
    legacy_signature = _axis_signature(legacy)
    pure_signature = _axis_signature(new_rows)
    return {
        "exact": legacy_window == pure.get("window")
        and legacy_signature == pure_signature,
        "window": legacy_window,
        "legacy_signature": legacy_signature,
        "pure_signature": pure_signature,
        "legacy_segments": len(legacy),
        "pure_segments": len(new_rows),
        "legacy_total": sum(as_number(row.get("effective_score")) for row in legacy),
        "pure_total": sum(as_number(row.get("effective_score")) for row in new_rows),
        "legacy_elapsed": legacy_elapsed,
        "pure_elapsed": pure_elapsed,
        "fallback": norm(getattr(editor, "_timeline_engine_last_fallback", "")),
    }


def _compare_team(
    editor: Any,
    metrics: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    legacy = editor._team_practical_simulate_legacy(
        copy.deepcopy(metrics),
        copy.deepcopy(context),
    )
    legacy_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    pure = editor._team_practical_simulate_pure(
        copy.deepcopy(metrics),
        copy.deepcopy(context),
    )
    pure_elapsed = time.perf_counter() - started
    old_rows = list(legacy.get("segments", []))
    new_rows = list(pure.get("segments", []))
    old_signature = _axis_signature(old_rows)
    new_signature = _axis_signature(new_rows)
    return {
        "exact": legacy.get("window") == pure.get("window")
        and old_signature == new_signature,
        "legacy_signature": old_signature,
        "pure_signature": new_signature,
        "legacy_segments": len(old_rows),
        "pure_segments": len(new_rows),
        "legacy_total": sum(as_number(row.get("effective_score")) for row in old_rows),
        "pure_total": sum(as_number(row.get("effective_score")) for row in new_rows),
        "legacy_elapsed": legacy_elapsed,
        "pure_elapsed": pure_elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=float, default=125.0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--team", action="store_true")
    parser.add_argument("--job", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

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
        and (not norm(args.job) or norm(second) == norm(args.job))
    ]
    schedule = {
        "window": max(1.0, args.window),
        "burst": [(0.0, 18.0), (50.0, 68.0), (100.0, 118.0)],
        "accel": [(60.0, 75.0)],
        "cocktail": True,
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for done, (index, first, second) in enumerate(selected, 1):
        editor = None
        try:
            editor, metrics = _prepare_editor(
                app,
                store,
                calculator,
                first,
                second,
            )
            result = _compare_axis(editor, metrics, schedule)
            row = {
                "index": index,
                "first_job": first,
                "second_job": second,
                "metric_count": len(metrics),
                "axis": result,
            }
            if args.team:
                team = _make_team(all_jobs, second, index, DEFAULT_SEED)
                editor._apply_team_members(team)
                editor._auto_populate_team_effects_from_members(schedule=False)
                app.processEvents()
                context = editor._team_practical_runtime_context(metrics, args.window)
                row["team"] = team
                row["team_axis"] = _compare_team(editor, metrics, context)
            rows.append(row)
            team_text = (
                f", team={row['team_axis']['exact']}"
                if "team_axis" in row
                else ""
            )
            print(
                f"[{done}/{len(selected)}] {second}: axis={result['exact']}{team_text}",
                flush=True,
            )
        except Exception as exc:
            errors.append({
                "index": index,
                "first_job": first,
                "second_job": second,
                "error": repr(exc),
            })
            print(f"[{done}/{len(selected)}] {second}: ERROR {exc!r}", flush=True)
        finally:
            if editor is not None:
                editor.deleteLater()
                app.processEvents()

    payload = {
        "window": args.window,
        "shard": args.shard,
        "shards": args.shards,
        "exact": not errors
        and all(row["axis"]["exact"] for row in rows)
        and all(row.get("team_axis", {"exact": True})["exact"] for row in rows),
        "rows": rows,
        "errors": errors,
    }
    output = args.output or (
        ROOT
        / "outputs"
        / "verification"
        / f"pure_timeline_engine_shard_{args.shard}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"exact={payload['exact']} saved={output}", flush=True)
    raise SystemExit(0 if payload["exact"] else 1)


if __name__ == "__main__":
    main()
