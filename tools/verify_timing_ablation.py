"""Offline scoring audit: reuse A/N/T for old/new attribution, never upload.

The second score reuses the same selected/exclusion-adjusted plans and global
profession benchmarks. Report its time separately; it is not production work.
"""
from __future__ import annotations
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-boss", action="store_true", help="Bound the audit to the first Boss in this real capture")
    args = parser.parse_args()
    from dragon_nest_builder import v410_native_core as native
    from dragon_nest_builder.native import replay_score_service as replay_service
    from dragon_nest_builder.live_dps.theory_plan_builder import TheoryPlanBuilderMixin
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder import timeline_parallel as pool

    native.V410ScoreSyncManager = lambda: SimpleNamespace(status=lambda: {}, close=lambda: None)
    replay_service.record_usage_event = lambda *a, **k: None
    pool._set_worker_process_low_priority()
    store = DataStore.load()
    core = native.NativeSimulatorCore(store, BuildCalculator(store))
    core._queue_replay_score_sync = lambda *a, **k: None
    core._team_score_disk_cache = SimpleNamespace(load=lambda *a: None, save=lambda *a: None, discard=lambda *a: None)
    core._replay_disk_cache.save = lambda *a, **k: None
    legacy_metadata = {}
    original_metadata = TheoryPlanBuilderMixin._team_score_support_replan_metadata

    def metadata(owner, **kw):
        if kw.get("precomputed_timing_ablations"):
            legacy_kw = dict(kw); legacy_kw["precomputed_timing_ablations"] = None
            legacy_metadata.update(original_metadata(owner, **legacy_kw))
        return original_metadata(owner, **kw)

    TheoryPlanBuilderMixin._team_score_support_replan_metadata = metadata
    original_score = TeamScoreEngine.score
    comparison = {}

    def score(engine, *a, **kw):
        rows = original_score(engine, *a, **kw)
        if not legacy_metadata or not kw.get("theory_plans"):
            return rows
        legacy_plans = copy.deepcopy(kw["theory_plans"])
        for source, plan in legacy_plans.items():
            if source in legacy_metadata:
                plan["_support_counterfactual_replan"] = legacy_metadata[source]
        legacy_kw = dict(kw, theory_plans=legacy_plans)
        legacy_kw["precomputed_support_benchmarks"] = engine._score_runtime_support_benchmarks
        started = time.perf_counter()
        legacy = original_score(engine, *a, **legacy_kw)
        comparison.update(legacy_score_seconds=time.perf_counter() - started,
                          old_rows=[asdict(row) for row in legacy], new_rows=[asdict(row) for row in rows])
        return rows

    TeamScoreEngine.score = score
    started = time.perf_counter()
    try:
        replay = core.load_replay(str(args.capture), lambda n, s: print("LOAD", n, s, flush=True) if n % 10 == 0 else None)
        load_seconds = time.perf_counter() - started
        payload = {"sourceHash": replay["sourceHash"], "sourceFile": str(args.capture), "bossPreset": "红龙佩达"}
        scope = "full_capture"
        if args.first_boss:
            from dragon_nest_builder.live_dps.boss_scope import _boss_statistics_groups
            groups = _boss_statistics_groups(core._replays[replay["sourceHash"]])
            first = min(groups, key=lambda group: group.first_ms)
            payload["bossTargetIds"] = sorted(first.target_ids)
            scope = first.name
            print("SCOPE", scope, "seconds", (first.last_ms - first.first_ms) / 1000, flush=True)
        started = time.perf_counter()
        result = core.score_replay(payload,
                                   lambda n, s: print("SCORE", n, s, flush=True))
        elapsed = time.perf_counter() - started
        if result.get("theoryPlannerError") or result.get("theoryPlanner") != "v401-full-team-score":
            raise RuntimeError(result.get("theoryPlannerError") or str(result.get("theoryPlanner")))
        if not comparison:
            raise RuntimeError("未生成完整的新旧辅助分对照")
        old = {row["source_id"]: row for row in comparison["old_rows"]}
        for row in comparison["new_rows"]:
            assert row["theory_damage_completion"] == old[row["source_id"]]["theory_damage_completion"]
            assert row["survival_penalty"] == old[row["source_id"]]["survival_penalty"]
        report = {"capture": str(args.capture), "scope": scope, "load_seconds": load_seconds,
                  "production_score_seconds": elapsed - comparison["legacy_score_seconds"],
                  "pool": pool.timeline_process_pool_status(), "diagnostics": result.get("theoryDiagnostics"),
                  "output_completion_and_survival_unchanged": True, **comparison,
                  "legacy_metadata": legacy_metadata,
                  "new_metadata": {source: plan.get("_support_counterfactual_replan", {})
                                   for source, plan in core.score_theory_planner._team_score_theory_plans.items()}}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("AUDIT_OK", args.output, "seconds", report["production_score_seconds"], flush=True)
    finally:
        TheoryPlanBuilderMixin._team_score_support_replan_metadata = original_metadata
        TeamScoreEngine.score = original_score
        core.shutdown()
        pool.reset_timeline_process_pool()


if __name__ == "__main__":
    main()
