"""Compare exact optimizer results and timings; does not modify saved builds."""
from __future__ import annotations

import argparse
import copy
import cProfile
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.talisman_optimizer import optimize_talismans


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", nargs="+", default=["冰灵", "狙翎", "十字军"])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    store = DataStore.load()
    results = []
    for job in args.jobs:
        build = copy.deepcopy(store.defaults["build1"])
        first = next(first for first in store.first_jobs() if job in store.second_jobs(first))
        build.update(first_job=first, second_job=job)
        effective = BuildCalculator(store).effective[job]
        attacks = list(dict.fromkeys(effective[f"攻击{i}"] for i in range(1, 4)
            if effective.get(f"攻击{i}") in {"物攻", "魔攻"} and float(effective.get(f"权重{i}", 0)) > 0))
        main_stat = "力量" if first in {"剑圣", "贤者", "战神"} else "敏捷" if first in {"箭神", "游侠", "追击者", "舞者"} else "智力"
        calculator = BuildCalculator(store)
        for repeat in range(args.repeat):
            profiler = cProfile.Profile() if args.profile else None
            if profiler: profiler.enable()
            started = perf_counter()
            rows, info = optimize_talismans(calculator, build, {"main_stat": main_stat, "attack_attrs": attacks})
            if profiler: profiler.disable()
            result = {"job": job, "repeat": repeat, "seconds": perf_counter() - started, "rows": rows, **info}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if profiler: pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(22)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
