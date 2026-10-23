from __future__ import annotations

import argparse
import cProfile
import os
import pstats
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
from dragon_nest_builder.app import TEAM_CDR_APPLY_SKILLS  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number  # noqa: E402
from tools.benchmark_timeline_optimizer import _prepare_editor  # noqa: E402


def adjusted_effects(editor: Any, job: str) -> list[dict[str, Any]]:
    effects = editor._team_effects_for_jobs([job])
    base = editor._team_effects_with_auto_cdr_glyphs(editor._dedup_team_effects(effects))
    applied = {
        editor._team_effect_skill_name(effect.get("skill"))
        for effect in base
        if editor._team_effect_skill_name(effect.get("skill")) in TEAM_CDR_APPLY_SKILLS
    }
    return editor._team_effects_with_cdr_adjusted_cooldowns(base, applied)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--profile-index", type=int, default=0)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    pairs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second
    ]
    editor, _metrics = _prepare_editor(app, store, calculator, *pairs[0])
    editor._reset_extreme_timeline_context("\u65e0")
    editor._skill_burst_enable_new_scheme_1_quality_floor = False

    total_started = time.perf_counter()
    for index, (_first, job) in enumerate(pairs[: max(1, args.count)], start=1):
        started = time.perf_counter()
        effects = adjusted_effects(editor, job)
        profiler = cProfile.Profile() if index == args.profile_index else None
        if profiler is not None:
            profiler.enable()
        try:
            result = editor._extreme_team_timeline_result(
                job,
                (job,),
                effects,
                1.0,
                args.window,
                include_display_windows=False,
            )
        finally:
            if profiler is not None:
                profiler.disable()
                output = ROOT / "outputs" / "benchmarks" / f"extreme_baseline_{index}_{job}.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("w", encoding="utf-8") as handle:
                    pstats.Stats(profiler, stream=handle).strip_dirs().sort_stats("cumulative").print_stats(50)
        elapsed = time.perf_counter() - started
        print(
            {
                "index": index,
                "job": job,
                "elapsed": elapsed,
                "dps": as_number(result.get("dps")),
                "segments": len(result.get("segments", [])),
            },
            flush=True,
        )
    print({"total": time.perf_counter() - total_started}, flush=True)


if __name__ == "__main__":
    main()
