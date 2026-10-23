from __future__ import annotations

import argparse
import faulthandler
import json
import os
import pickle
import sys
import threading
import time
from pathlib import Path


os.environ.setdefault("DRAGON_NEST_NATIVE_NO_QT", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore
from dragon_nest_builder.timeline_parallel import (
    last_team_score_player_diagnostics,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--no-score-cache", action="store_true")
    parser.add_argument(
        "--stack-every",
        type=float,
        default=0.0,
        help="Periodically dump Python stacks while profiling a stalled score.",
    )
    parser.add_argument("--inspect-theory-payloads", action="store_true")
    parser.add_argument("--profile-one-job", default="")
    parser.add_argument("--time-one-job", default="")
    parser.add_argument("--profile-output", type=Path, default=Path("tmp_timeline_axis.prof"))
    parser.add_argument("--profile-seconds", type=float, default=0.0)
    args = parser.parse_args()

    if args.stack_every > 0:
        faulthandler.dump_traceback_later(args.stack_every, repeat=True)

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._score_sync.enqueue = lambda *_args, **_kwargs: None
    if args.no_score_cache:
        core._team_score_disk_cache.load = lambda _key: None
        core._team_score_disk_cache.save = lambda *_args, **_kwargs: None
    if args.inspect_theory_payloads or args.profile_one_job or args.time_one_job:
        original_runtime = core.score_theory_planner.timeline_service.extreme_runtime

        def inspect_parallel(task_specs, *parallel_args, **parallel_kwargs):
            sizes = [len(pickle.dumps(spec, protocol=pickle.HIGHEST_PROTOCOL)) for spec in task_specs]
            snapshot_payloads = []
            for spec in task_specs:
                context = spec.get("score_exclusion_context") or {}
                reference = context.get("snapshot")
                snapshot_payloads.append(len(getattr(reference, "payload", b"")))
            print(
                "theory_spec_payloads="
                f"count:{len(sizes)} total:{sum(sizes)} min:{min(sizes, default=0)} "
                f"max:{max(sizes, default=0)} snapshot_payloads:{snapshot_payloads} "
                f"jobs:{json.dumps([str(spec.get('job', '')) for spec in task_specs], ensure_ascii=True)}",
                flush=True,
            )
            print(
                "theory_spec_modes="
                + json.dumps(
                    [
                        {
                            "job": str(spec.get("job", "")),
                            "window": spec.get("window"),
                            "score_refinement": spec.get("score_refinement"),
                            "fast_long_timeline": spec.get("fast_long_timeline"),
                            "long_sim_planner": spec.get("long_sim_planner"),
                            "effects": len(spec.get("effects", ())),
                        }
                        for spec in task_specs
                    ],
                    ensure_ascii=True,
                ),
                flush=True,
            )
            selected_job = args.profile_one_job or args.time_one_job
            if selected_job:
                target = next(
                    (
                        spec for spec in task_specs
                        if str(spec.get("job", "")) == selected_job
                    ),
                    None,
                )
                if target is None:
                    raise SystemExit(87)
                if args.time_one_job:
                    axis_started = time.perf_counter()
                    result = nonlocal_runtime[
                        "value"
                    ]._extreme_timeline_result_from_spec(target)
                    print(
                        f"timed_job={selected_job} "
                        f"seconds={time.perf_counter() - axis_started:.6f} "
                        f"dps={result.get('dps') if isinstance(result, dict) else None}",
                        flush=True,
                    )
                    raise SystemExit(85)
                import cProfile

                profiler = cProfile.Profile()
                args.profile_output.parent.mkdir(parents=True, exist_ok=True)
                if args.profile_seconds > 0:
                    def snapshot_and_exit() -> None:
                        time.sleep(args.profile_seconds)
                        profiler.dump_stats(str(args.profile_output))
                        os._exit(88)

                    threading.Thread(
                        target=snapshot_and_exit,
                        name="ScoreProfileCutoff",
                        daemon=True,
                    ).start()
                profiler.runcall(
                    nonlocal_runtime["value"]._extreme_timeline_result_from_spec,
                    target,
                )
                profiler.dump_stats(str(args.profile_output))
            raise SystemExit(86)

        def inspect_runtime(build):
            runtime = original_runtime(build)
            nonlocal_runtime["value"] = runtime
            runtime._extreme_timeline_tasks_parallel = inspect_parallel
            return runtime

        nonlocal_runtime = {}
        core.score_theory_planner.timeline_service.extreme_runtime = inspect_runtime
    started = time.perf_counter()
    last_progress: tuple[int, str] | None = None

    def report_progress(value: int, label: str) -> None:
        nonlocal last_progress
        row = (int(value), str(label))
        if row == last_progress:
            return
        last_progress = row
        print(
            f"progress={row[0]:3d}% elapsed={time.perf_counter() - started:8.3f}s "
            f"{row[1]}",
            flush=True,
        )

    replay = core.load_replay(str(args.capture), report_progress)
    print(
        f"load={time.perf_counter() - started:.3f}s "
        f"cache={bool(replay.get('versionCache', {}).get('replayHit'))}",
        flush=True,
    )
    payload = {
        "sourceHash": replay["sourceHash"],
        "sourceFile": str(args.capture),
        "bossPreset": "红龙佩达",
    }
    for index in range(max(1, args.repeat)):
        started = time.perf_counter()
        last_progress = None
        result = core.score_replay(payload, report_progress)
        print(
            f"score_{index + 1}={time.perf_counter() - started:.3f}s "
            f"cache={bool(result.get('diagnostics', {}).get('versionCacheHit'))} "
            f"rows={len(result.get('rows', []))} "
            f"score_diagnostics={result.get('diagnostics', {})} "
            f"theory_diagnostics={result.get('theoryDiagnostics', {})} "
            f"player_diagnostics={last_team_score_player_diagnostics()}",
            flush=True,
        )
    if args.stack_every > 0:
        faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
