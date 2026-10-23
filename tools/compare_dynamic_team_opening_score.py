from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from dragon_nest_builder.v410_native_core import NativeSimulatorCore  # noqa: E402


class _DisabledDiskCache:
    def load(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def save(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def discard(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _opening(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "start": round(as_number(row.get("start")), 4),
            "skill": norm(row.get("skill")),
            "kind": norm(row.get("kind")),
        }
        for row in plan.get("segments", ())
        if isinstance(row, dict)
        and not row.get("timeline_hidden")
        and norm(row.get("kind")) != "invalid"
    ][:12]


def _score(
    core: NativeSimulatorCore,
    capture: Path,
    source_hash: str,
    *,
    legacy: bool,
) -> dict[str, Any]:
    planner = core.score_theory_planner
    planner._skill_burst_disable_team_objective_opening_order = legacy
    planner._skill_burst_disable_team_objective_pair_order = legacy
    planner.invalidate_score_cache()
    core._score_keys_by_replay.pop(source_hash, None)
    core._score_results.clear()
    core._score_benchmark_nests.clear()
    label = "legacy" if legacy else "dynamic"
    started = time.perf_counter()

    def progress(value: int | float, message: str, *_args: Any) -> None:
        print(f"[{label} {float(value):5.1f}%] {message}", flush=True)

    result = core.score_replay(
        {
            "sourceHash": source_hash,
            "sourceFile": str(capture),
            "bossTargetIds": [],
            "bossPreset": "红龙佩达",
            "glyphOverrides": {},
        },
        progress,
    )
    score_key = str(result.get("scoreKey") or "")
    rows = [asdict(row) for row in core._score_results.get(score_key, ())]
    plans = copy.deepcopy(planner._team_score_theory_plans)
    return {
        "seconds": time.perf_counter() - started,
        "score_key": score_key,
        "rows": rows,
        "plans": {
            source_id: {
                "job": norm(plan.get("job")),
                "total": as_number(plan.get("total")),
                "opening": _opening(plan),
            }
            for source_id, plan in plans.items()
            if isinstance(plan, dict)
        },
        "planner": result.get("theoryPlanner"),
        "planner_error": result.get("theoryPlannerError"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    capture = args.capture.resolve()
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._queue_replay_score_sync = lambda *_args, **_kwargs: None
    core._team_score_disk_cache = _DisabledDiskCache()
    replay_started = time.perf_counter()
    replay = core.load_replay(
        str(capture),
        lambda value, message, *_args: print(
            f"[replay {float(value):5.1f}%] {message}",
            flush=True,
        ),
    )
    replay_seconds = time.perf_counter() - replay_started
    source_hash = str(replay["sourceHash"])
    legacy = _score(core, capture, source_hash, legacy=True)
    dynamic = _score(core, capture, source_hash, legacy=False)

    old_by_source = {
        str(row.get("source_id") or ""): row for row in legacy["rows"]
    }
    comparisons = []
    for new in dynamic["rows"]:
        source_id = str(new.get("source_id") or "")
        old = old_by_source.get(source_id)
        if old is None:
            continue
        comparisons.append({
            "source_id": source_id,
            "name": str(new.get("name") or old.get("name") or ""),
            "job": str(new.get("job") or old.get("job") or ""),
            "legacy_output_score": as_number(old.get("output_score")),
            "dynamic_output_score": as_number(new.get("output_score")),
            "output_score_delta": (
                as_number(new.get("output_score"))
                - as_number(old.get("output_score"))
            ),
            "legacy_support_score": as_number(old.get("support_score")),
            "dynamic_support_score": as_number(new.get("support_score")),
            "support_score_delta": (
                as_number(new.get("support_score"))
                - as_number(old.get("support_score"))
            ),
            "legacy_total_score": as_number(old.get("total_score")),
            "dynamic_total_score": as_number(new.get("total_score")),
            "total_score_delta": (
                as_number(new.get("total_score"))
                - as_number(old.get("total_score"))
            ),
            "legacy_theory_completion": as_number(
                old.get("theory_damage_completion")
            ),
            "dynamic_theory_completion": as_number(
                new.get("theory_damage_completion")
            ),
            "legacy_plan": legacy["plans"].get(source_id, {}),
            "dynamic_plan": dynamic["plans"].get(source_id, {}),
        })

    payload = {
        "capture": str(capture),
        "source_hash": source_hash,
        "replay_seconds": replay_seconds,
        "legacy_seconds": legacy["seconds"],
        "dynamic_seconds": dynamic["seconds"],
        "comparisons": comparisons,
        "legacy_planner_error": legacy["planner_error"],
        "dynamic_planner_error": dynamic["planner_error"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
