"""Profile one production replay score and retain linked-support diagnostics.

This is an offline diagnostic: replay decoding may use its version cache, the
team-score cache is disabled, and cloud synchronization is replaced by a no-op.
Run conditional and legacy modes in separate processes for an honest wall-time
comparison without sharing theory-plan or worker state between measurements.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _DisabledDiskCache:
    def load(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def discard(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--mode", choices=("conditional", "legacy"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from dragon_nest_builder import timeline_parallel as pool
    from dragon_nest_builder import v410_native_core as native
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.native import replay_score_service

    native.V410ScoreSyncManager = lambda: SimpleNamespace(
        status=lambda: {}, close=lambda: None,
    )
    replay_score_service.record_usage_event = lambda *_args, **_kwargs: None
    pool._set_worker_process_low_priority()

    capture = args.capture.expanduser().resolve()
    store = DataStore.load()
    core = native.NativeSimulatorCore(store, BuildCalculator(store))
    core._queue_replay_score_sync = lambda *_args, **_kwargs: None
    core._team_score_disk_cache = _DisabledDiskCache()
    # Keep replay-cache reads because scoring is the measurement target, but
    # do not mutate the user's replay cache during this diagnostic.
    core._replay_disk_cache.save = lambda *_args, **_kwargs: None
    progress_rows: list[dict[str, Any]] = []

    try:
        load_started = time.perf_counter()
        replay = core.load_replay(str(capture), lambda *_args: None)
        replay_seconds = time.perf_counter() - load_started
        source_hash = str(replay["sourceHash"])

        core.score_theory_planner._team_score_timing_ablation_mode = args.mode
        score_started = time.perf_counter()

        def progress(value: int, message: str) -> None:
            elapsed = time.perf_counter() - score_started
            progress_rows.append({
                "seconds": round(elapsed, 6),
                "progress": int(value),
                "message": str(message),
            })
            if value in {4, 46, 51, 52, 68, 72, 77, 79, 80, 92, 96}:
                print(f"{elapsed:8.3f}s {value:3d}% {message}", flush=True)

        result = core.score_replay(
            {
                "sourceHash": source_hash,
                "sourceFile": str(capture),
                "bossPreset": "红龙佩达",
            },
            progress,
        )
        score_seconds = time.perf_counter() - score_started
        if result.get("theoryPlanner") != "v401-full-team-score":
            raise RuntimeError(
                str(result.get("theoryPlannerError") or result.get("theoryPlanner"))
            )

        score_key = str(result.get("scoreKey") or "")
        rows = tuple(core._score_results.get(score_key, ()))
        theory_diagnostics = copy.deepcopy(
            core.score_theory_planner._team_score_theory_diagnostics
        )
        plans = core.score_theory_planner._team_score_theory_plans
        payload = {
            "capture": str(capture),
            "capture_bytes": capture.stat().st_size,
            "source_hash": source_hash,
            "mode": args.mode,
            "replay_seconds": replay_seconds,
            "score_seconds": score_seconds,
            "pool": pool.timeline_process_pool_status(),
            "theory_diagnostics": theory_diagnostics,
            "support_replans": {
                str(source_id): copy.deepcopy(
                    plan.get("_support_counterfactual_replan", {})
                )
                for source_id, plan in plans.items()
                if isinstance(plan, dict)
                and isinstance(plan.get("_support_counterfactual_replan"), dict)
            },
            "players": [
                {
                    "source_id": row.source_id,
                    "name": row.name,
                    "job": row.rotation_job or row.job,
                    "total_score": row.total_score,
                    "support_score": row.support_score,
                    "support_weight": row.support_weight,
                    "support_contribution": row.support_contribution,
                    "contribution_completion": row.contribution_completion,
                    "support_details": [asdict(detail) for detail in row.support_details],
                }
                for row in rows
            ],
            "progress": progress_rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        timing = theory_diagnostics.get("timing_ablation", {})
        print(
            "PROFILE_OK "
            f"mode={args.mode} score={score_seconds:.3f}s "
            f"timing={float(timing.get('seconds') or 0.0):.3f}s "
            f"tasks={int(timing.get('task_count') or 0)} "
            f"output={args.output.resolve()}",
            flush=True,
        )
        return 0
    finally:
        core.shutdown()
        pool.reset_timeline_process_pool()


if __name__ == "__main__":
    raise SystemExit(main())
