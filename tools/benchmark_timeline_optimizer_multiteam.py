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
    DEFAULT_SEED,
    _display_job,
    _gain_percent,
    _make_team,
    _prepare_editor,
    _run_team,
)


def benchmark_job_teams(
    app: QApplication,
    store: DataStore,
    calculator: BuildCalculator,
    first_job: str,
    second_job: str,
    all_jobs: list[str],
    index: int,
    seeds: list[int],
    window: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    editor, metrics = _prepare_editor(app, store, calculator, first_job, second_job)
    if not metrics:
        raise RuntimeError("技能模拟没有生成任何技能数据")

    trials: list[dict[str, Any]] = []
    for seed in seeds:
        team = _make_team(all_jobs, second_job, index, seed)
        editor._apply_team_members(team)
        editor._auto_populate_team_effects_from_members(schedule=False)
        app.processEvents()
        context = editor._team_practical_runtime_context(metrics, window)
        before = _run_team(editor, metrics, context, False)
        after = _run_team(editor, metrics, context, True)
        trials.append({
            "seed": seed,
            "team": team,
            "before": before,
            "after": after,
            "gain_percent": _gain_percent(before["score"], after["score"]),
        })

    row = {
        "index": index,
        "first_job": first_job,
        "second_job": second_job,
        "team_target": _display_job(second_job),
        "metric_count": len(metrics),
        "trials": trials,
        "total_elapsed": time.perf_counter() - started,
    }
    editor.deleteLater()
    app.processEvents()
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Run additional fixed random team timeline checks.")
    parser.add_argument("--seed-start", type=int, default=DEFAULT_SEED + 1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
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
        if second and second != "无"
    ]
    all_jobs = [second for _first, second in all_pairs]
    jobs = all_pairs
    if args.job:
        jobs = [item for item in jobs if norm(item[1]) == norm(args.job)]
    indexed_jobs = [
        (index, first, second)
        for index, (first, second) in enumerate(jobs)
        if index % max(1, args.shards) == args.shard
    ]
    seeds = [args.seed_start + offset for offset in range(max(1, args.repeats))]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for done, (index, first, second) in enumerate(indexed_jobs, 1):
        try:
            row = benchmark_job_teams(
                app,
                store,
                calculator,
                first,
                second,
                all_jobs,
                index,
                seeds,
                args.window,
            )
            rows.append(row)
            gains = [trial["gain_percent"] for trial in row["trials"]]
            print(
                f"[{done}/{len(indexed_jobs)}] {second}: "
                f"mean {sum(gains) / len(gains):+.4f}% / "
                f"range {min(gains):+.4f}%..{max(gains):+.4f}% / "
                f"{row['total_elapsed']:.2f}s",
                flush=True,
            )
        except Exception as exc:
            errors.append({"first_job": first, "second_job": second, "error": repr(exc)})
            print(f"[{done}/{len(indexed_jobs)}] {second}: ERROR {exc!r}", flush=True)

    payload = {
        "seed_start": args.seed_start,
        "seeds": seeds,
        "window": args.window,
        "shard": args.shard,
        "shards": args.shards,
        "rows": rows,
        "errors": errors,
    }
    output = args.output or ROOT / "outputs" / "benchmarks" / f"timeline_optimizer_multiteam_shard_{args.shard}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output}", flush=True)


if __name__ == "__main__":
    main()
