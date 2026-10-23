from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import pickle
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--dump-specs", type=Path)
    parser.add_argument("--no-score-cache", action="store_true")
    parser.add_argument(
        "--team-objective-weight",
        type=float,
        default=None,
        help="Diagnostic override for the output planner's full-team marginal objective weight.",
    )
    parser.add_argument(
        "--glyph-overrides-json",
        default="",
        help="Optional JSON object mapping jobs to four score glyph rows.",
    )
    args = parser.parse_args()

    if args.team_objective_weight is not None:
        from dragon_nest_builder.live_dps import theory_plan_builder

        theory_plan_builder._TEAM_SCORE_OUTPUT_TEAM_OBJECTIVE_WEIGHT = max(
            0.0,
            float(args.team_objective_weight),
        )

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    # Verification must be deterministic and local. A configured cloud score
    # endpoint can otherwise keep this one-shot diagnostic alive while its
    # offline queue retries an upload.
    core._score_sync.enqueue = lambda *_args, **_kwargs: None
    if args.build:
        core.set_build("build1", json.loads(args.build.read_text(encoding="utf-8")))
    progress_rows: list[dict[str, object]] = []
    captured_specs: list[dict[str, object]] = []
    captured_timings: list[dict[str, object]] = []
    captured_support_benchmarks: dict[str, float] = {}
    original_score = TeamScoreEngine.score

    def capture_score(self, *score_args, **score_kwargs):
        result = original_score(self, *score_args, **score_kwargs)
        captured_support_benchmarks.update(
            getattr(self, "_score_runtime_support_benchmarks", {})
        )
        return result

    TeamScoreEngine.score = capture_score
    if args.dump_specs or args.no_score_cache:
        # A diagnostic spec dump must execute the planner. A completed score
        # cache contains rendered rows only and therefore cannot expose the
        # exact planner inputs needed for A/B analysis.
        core._team_score_disk_cache.load = lambda _key: None
        core._team_score_disk_cache.save = lambda *_args, **_kwargs: None
    if args.dump_specs:
        runtime_type = type(core.score_theory_planner.timeline_service.extreme_runtime(core.get_build("build1")))
        original_parallel = core.score_theory_planner.timeline_service._source_type()._extreme_timeline_tasks_parallel

        def capture_parallel(self, task_specs, progress_callback=None, **kwargs):
            captured_specs.extend(task_specs)
            return original_parallel(
                self,
                task_specs,
                progress_callback=progress_callback,
                **kwargs,
            )

        runtime_type._extreme_timeline_tasks_parallel = capture_parallel
        original_timing = core.score_theory_planner._team_score_localized_timing

        def capture_timing(output_windows, effect_cast_schedule):
            result = original_timing(output_windows, effect_cast_schedule)
            captured_timings.append({
                "output_windows": output_windows,
                "effect_cast_schedule": effect_cast_schedule,
                "result": result,
            })
            return result

        core.score_theory_planner._team_score_localized_timing = capture_timing

    def progress(value: int, message: str) -> None:
        progress_rows.append({"value": int(value), "message": str(message)})
        print(f"[{int(value):3d}%] {message}", flush=True)

    started = time.perf_counter()
    replay = core.load_replay(str(args.capture.resolve()), progress)
    decode_seconds = time.perf_counter() - started
    started = time.perf_counter()
    score_payload = {
        "sourceHash": replay["sourceHash"],
        "sourceFile": replay["sourceFile"],
    }
    if args.glyph_overrides_json:
        glyph_overrides = json.loads(args.glyph_overrides_json)
        if not isinstance(glyph_overrides, dict):
            raise ValueError("--glyph-overrides-json must decode to an object")
        score_payload["glyphOverrides"] = glyph_overrides
    score = core.score_replay(score_payload, progress)
    plans = {
        str(source_id): {
            "total": plan.get("total"),
            "raw_total": plan.get("raw_total"),
            "average": plan.get("average"),
            "dps": plan.get("dps"),
            "_score_skill_damages": plan.get("_score_skill_damages", {}),
            "_score_skill_powers": plan.get("_score_skill_powers", {}),
            "_score_skill_components": plan.get("_score_skill_components", {}),
            "_score_glyphs": plan.get("_score_glyphs", []),
            "_score_glyph_source": plan.get("_score_glyph_source", ""),
            "summary": plan.get("summary", {}),
            "segments": [
                {
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "skill": row.get("skill"),
                    "kind": row.get("kind"),
                    "type": row.get("type"),
                    "raw_effective_score": row.get("raw_effective_score"),
                    "effective_score": row.get("effective_score"),
                    "score_actual_damage": row.get("_score_actual_damage"),
                    "detail": row.get("detail"),
                }
                for row in plan.get("segments", ())
                if isinstance(row, dict)
            ],
            "effect_windows": plan.get("effect_windows", []),
            "external_windows": plan.get("external_windows", []),
        }
        for source_id, plan in core.score_theory_planner._team_score_theory_plans.items()
        if isinstance(plan, dict)
    }
    linked_timing_diagnostics = {}
    for source_id, plan in core.score_theory_planner._team_score_theory_plans.items():
        if not isinstance(plan, dict):
            continue
        replan = plan.get("_support_counterfactual_replan", {})
        if not isinstance(replan, dict):
            continue
        timing_rows = replan.get("timing_ablation", {})
        if not isinstance(timing_rows, dict) or not timing_rows:
            continue
        linked_timing_diagnostics[str(source_id)] = {
            "counterfactual_schedule_policy": replan.get(
                "counterfactual_schedule_policy"
            ),
            "linked_contribution_included_in_score": replan.get(
                "linked_contribution_included_in_score", False
            ),
            "skills": {
                str(skill): {
                    key: row.get(key)
                    for key in (
                        "actual_direct_marginal_dps",
                        "actual_linked_marginal_dps",
                        "actual_total_marginal_dps",
                        "theory_direct_marginal_dps",
                        "theory_linked_marginal_dps",
                        "theory_total_marginal_dps",
                        "linked_absent_schedule",
                        "linked_candidate_schedule",
                    )
                }
                for skill, row in timing_rows.items()
                if isinstance(row, dict)
            },
        }
    report = {
        "decodeSeconds": decode_seconds,
        "scoreSeconds": time.perf_counter() - started,
        "score": score,
        "supportBenchmarks": captured_support_benchmarks,
        "scoreRowsDetailed": [
            asdict(row)
            for row in core._score_results.get(score.get("scoreKey", ""), ())
        ],
        "plans": plans,
        "linkedTimingDiagnostics": linked_timing_diagnostics,
        "theoryDiagnostics": getattr(
            core.score_theory_planner, "_team_score_theory_diagnostics", {}
        ),
        "progress": progress_rows,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.dump_specs:
        args.dump_specs.parent.mkdir(parents=True, exist_ok=True)
        source_hash = score.get("sourceHash", "")
        snapshot = core._replays.get(source_hash)
        score_axis = (
            core.score_theory_planner.score_cast_events(snapshot)
            if snapshot is not None
            else ()
        )
        args.dump_specs.write_bytes(pickle.dumps({
            "specs": captured_specs,
            "timings": captured_timings,
            "score_axis": score_axis,
        }))
    print(json.dumps({
        "decodeSeconds": report["decodeSeconds"],
        "scoreSeconds": report["scoreSeconds"],
        "theoryPlanner": score.get("theoryPlanner"),
        "theoryPlannerError": score.get("theoryPlannerError"),
        "rows": score.get("rows", []),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if score.get("theoryPlanner") == "v401-full-team-score" else 1


if __name__ == "__main__":
    raise SystemExit(main())
