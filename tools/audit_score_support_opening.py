"""Replay captured scoring specs through the real data-only timeline runtime."""
from __future__ import annotations

import argparse
import copy
import inspect
import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("specs", type=Path)
    parser.add_argument("--jobs", nargs="*", default=["十字军"])
    parser.add_argument("--window", type=float)
    parser.add_argument("--all-jobs", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    store = DataStore.load()
    service = LegacyTimelineService(store, BuildCalculator(store))
    runtime = service.extreme_runtime(copy.deepcopy(store.defaults["build1"]))
    # Only trusted local diagnostic spec artifacts are accepted here.
    specs = pickle.loads(args.specs.read_bytes())
    if isinstance(specs, dict):
        specs = specs["specs"]
    if args.all_jobs:
        jobs = [job for first in store.first_jobs() for job in store.second_jobs(first)]
        base_team = ["十字军", "剑皇", "狙翎", "冰灵", "时空领主", "噬魂者", "圣徒", "毁灭者"]
        specs = []
        for job in jobs:
            team = base_team if job in base_team else [job, *base_team[:7]]
            specs.append({"job": job, "team": team, "effects": runtime._team_effects_for_jobs(team),
                          "window": args.window or 30, "long_sim_planner": True,
                          "score_refinement": False, "include_display_windows": True})
        args.jobs = jobs
    accepted = inspect.signature(runtime._extreme_team_timeline_result).parameters
    rows = []
    for raw in specs:
        if raw.get("job") not in args.jobs or raw.get("excluded_skill_keys"):
            continue
        spec = {k: copy.deepcopy(v) for k, v in raw.items() if k in accepted}
        if args.window:
            spec["window"] = args.window
        started = time.perf_counter()
        result = runtime._extreme_team_timeline_result(**spec)
        row = {
            "job": spec["job"], "window": spec["window"],
            "seconds": time.perf_counter() - started,
            "total": result.get("total"),
            "settings": result.get("_planner_settings", {}),
            "opening": [
                {key: x.get(key) for key in ("skill", "start", "end", "kind", "effective_score")}
                for x in result.get("segments", ())
                if not x.get("timeline_hidden") and x.get("kind") != "invalid"
            ][:15],
            "segments": result.get("segments", []),
        }
        rows.append(row)
        print(json.dumps({"job": row["job"], "seconds": round(row["seconds"], 3),
                          "total": row["total"], "opening": [x["skill"] for x in row["opening"][:5]]},
                         ensure_ascii=False), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
