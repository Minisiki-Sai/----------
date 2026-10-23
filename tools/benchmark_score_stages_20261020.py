"""Current cumulative code, offline full scoring with coarse stage timers.

Delegates to the existing isolated benchmark (no score/evidence cache reuse).
Timers surround calls, not every Python event. Nested spans must not be added
together. --output and capture arguments are those of the original benchmark.
"""
import functools
import hashlib
import json
import os
from pathlib import Path
import pickle
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DRAGON_NEST_NATIVE_NO_QT"] = "1"


def main():
    import benchmark_exact_score_20261019 as benchmark
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    stages_path = output.with_name(output.stem + "_stages.json")
    stages, stack = [], []
    start = time.perf_counter()

    def save():
        stages_path.write_text(json.dumps(stages, ensure_ascii=False, indent=2), "utf8")

    def wrap(owner, name, label):
        original = getattr(owner, name)

        @functools.wraps(original)
        def measured(*args, **kwargs):
            index = len(stages)
            row = dict(stage=label, parent=stack[-1] if stack else None,
                       start=time.perf_counter() - start)
            stages.append(row)
            stack.append(index)
            save()
            try:
                result = original(*args, **kwargs)
                if label == "score_replay" and isinstance(result, dict):
                    row["diagnostics"] = result.get("diagnostics", {})
                    row["theory_diagnostics"] = result.get("theoryDiagnostics", {})
                    from dragon_nest_builder.timeline_parallel import last_team_score_player_diagnostics
                    row["player_diagnostics"] = last_team_score_player_diagnostics()
                return result
            finally:
                row["seconds"] = time.perf_counter() - start - row["start"]
                stack.pop()
                save()
        setattr(owner, name, measured)

    from dragon_nest_builder.native import replay_score_service as service
    from dragon_nest_builder.live_dps import score_hit_evidence as evidence
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
    from dragon_nest_builder.live_dps.theory_planner import TeamScoreTheoryPlanner
    from dragon_nest_builder.v410_native_core import NativeSimulatorCore
    from dragon_nest_builder.live_dps.v410_score_sync import V410ScoreSyncManager
    wrap(NativeSimulatorCore, "load_replay", "load_replay")
    wrap(NativeSimulatorCore, "score_replay", "score_replay")
    wrap(evidence, "prepare_continuous_context", "hit_evidence")
    wrap(service, "infer_combat_skill_glyphs", "glyph_inference")
    wrap(TeamScoreTheoryPlanner, "build", "theory_build")
    wrap(TeamScoreTheoryPlanner, "rebuild_excluded_axes", "exclusion_rebuild")
    wrap(TeamScoreEngine, "score_exclusions", "exclusion_check")
    wrap(TeamScoreEngine, "score", "final_score")
    wrap(V410ScoreSyncManager, "status", "sync_status")
    wrap(NativeSimulatorCore, "_queue_replay_score_sync", "export_enqueue")
    manifest = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for directory in ("dragon_nest_builder", "third_party/skill_hit_tool/app")
        for p in (ROOT / directory).rglob("*.py")
        if "__pycache__" not in p.parts
    }
    output.with_name(output.stem + "_source.json").write_text(
        json.dumps(manifest, indent=2), "utf8")
    # Save the exact current task specifications, not an older version's axis.
    from dragon_nest_builder.mixins.extreme_engine import ExtremeEngineMixin
    original = ExtremeEngineMixin._extreme_timeline_tasks_parallel
    seen = set()
    axis_times = []

    @functools.wraps(original)
    def capture_specs(self, specs, *args, **kwargs):
        for spec in specs:
            job = str(spec.get("job", ""))
            if job not in seen:
                seen.add(job)
                folder = output.with_name(output.stem + "_specs")
                folder.mkdir(exist_ok=True)
                with (folder / f"{job}.pkl").open("wb") as stream:
                    pickle.dump(spec, stream, protocol=pickle.HIGHEST_PROTOCOL)
        result = original(self, specs, *args, **kwargs)
        for spec, value in zip(specs, result):
            if isinstance(value, dict) and "_parallel_profile" in value:
                axis_times.append(dict(token="initial", job=spec.get("job"),
                                       **value["_parallel_profile"]))
        for token, value in getattr(self, "_extreme_parallel_followup_results", {}).items():
            if isinstance(value, dict) and "_parallel_profile" in value:
                axis_times.append(dict(
                    token=token,
                    job=getattr(self, "_extreme_parallel_followup_jobs", {}).get(token),
                    **value["_parallel_profile"]))
        output.with_name(output.stem + "_axis_times.json").write_text(
            json.dumps(axis_times, ensure_ascii=False, indent=2), "utf8")
        return result
    ExtremeEngineMixin._extreme_timeline_tasks_parallel = capture_specs
    return benchmark.main()


if __name__ == "__main__":
    raise SystemExit(main())
