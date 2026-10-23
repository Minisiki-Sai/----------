"""Read-only replay/selection comparison; never computes/uploads team scores."""
import argparse
from collections import defaultdict
from copy import deepcopy
import gc
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.combat_glyph_inference import infer_combat_skill_glyphs
from dragon_nest_builder.live_dps.combat_skill_levels import infer_combat_skill_levels, apply_combat_levels_to_build
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.score_glyph_selection import prepare_score_glyphs
from dragon_nest_builder.live_dps.score_glyph_objective import captured_glyph_damage_context, glyph_profile_for_source
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.theory_plan_builder import (
    _team_score_joint_objective_profiles, _team_score_scale_objective_profiles,
    _TEAM_SCORE_OUTPUT_TEAM_OBJECTIVE_WEIGHT,
)
from dragon_nest_builder.mixins import skill_build
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


def inspect(path, jobs):
    started = time.perf_counter()
    store = DataStore.load()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    print(json.dumps({"start": path.name}, ensure_ascii=False), flush=True)
    try:
        for batch in source.iter_batches():
            for event in batch:
                aggregator.ingest(event)
    finally:
        source.close()
    snapshot = aggregator.snapshot(include_events=True)
    players = tuple(p for p in snapshot.players if p.job)
    targets = CombatAggregator._primary_boss_target_ids(list(snapshot.damage_targets))
    windows = TeamScoreEngine._output_windows(snapshot, targets)
    before_damage = sum(e.amount for e in snapshot.combat_events if not e.healing)
    build_context_started = time.perf_counter()
    context = captured_glyph_damage_context(snapshot, players, store, windows, targets)
    context_seconds = time.perf_counter() - build_context_started
    inferred, _ = infer_combat_skill_glyphs(snapshot, store)
    levels = infer_combat_skill_levels(snapshot, store)
    editor = LegacyTimelineService(store, BuildCalculator(store)).extreme_runtime(deepcopy(store.defaults["build1"]))
    profiles = _team_score_joint_objective_profiles(players, tuple(p.job for p in players),
        snapshot.combat_events, targets, windows, editor._extreme_profile_for_job,
        {"element_resist": 20, "monster_defense": 4950, "freeze_coefficient": .3})
    profiles = _team_score_scale_objective_profiles(profiles, _TEAM_SCORE_OUTPUT_TEAM_OBJECTIVE_WEIGHT)
    candidate_rows = []
    original = skill_build.select_skill_glyphs_two_stage
    def watch(rows, *args, **kwargs):
        candidate_rows.extend(deepcopy(rows))
        return original(rows, *args, **kwargs)
    skill_build.select_skill_glyphs_two_stage = watch
    result = []
    try:
        for player in players:
            if jobs and player.job not in jobs:
                continue
            build = apply_combat_levels_to_build(editor._extreme_team_score_build_for_job(player.job),
                                                 levels.get(player.source_id, {}))
            variants = {}
            for label, profile in (("job_weighted_no_overlap_control", profiles.get(player.job)),
                                    ("captured_skill_weighted", glyph_profile_for_source(context, player.source_id, profiles.get(player.job)))):
                candidate_rows.clear()
                tick = time.perf_counter()
                selected, _ = prepare_score_glyphs(editor, deepcopy(build), player.job,
                    inferred_rows=inferred.get(player.source_id, []), team_size=len(players), team_objective_profile=profile)
                variants[label] = dict(selected=selected, seconds=round(time.perf_counter()-tick, 4),
                    candidates=[r for r in candidate_rows if r.get("skill") in {"冰光束", "混合感染", "神圣大地", "痊愈之手"}],
                    top_candidates=sorted(candidate_rows, key=lambda r: r.get("local_score", 0), reverse=True)[:6])
            channels = defaultdict(float)
            for bucket in context["buckets"]:
                if bucket["source_id"] == player.source_id:
                    channels[bucket["element"] or "未知/无属性"] += bucket["damage"]
            result.append(dict(source_id=player.source_id, name=player.name, job=player.job,
                               damage_by_element=dict(channels), variants=variants))
    finally:
        skill_build.select_skill_glyphs_two_stage = original
    assert before_damage == sum(e.amount for e in snapshot.combat_events if not e.healing)
    team_channels = defaultdict(float)
    for bucket in context["buckets"]:
        team_channels[bucket["element"] or "未知/无属性"] += bucket["damage"]
    print(json.dumps(dict(capture=path.name, elapsed=round(time.perf_counter()-started, 3),
        total_damage_unchanged=before_damage, context_seconds=round(context_seconds, 4),
        bucket_count=len(context["buckets"]), effect_count=len(context["effects"]),
        team_damage_by_element=dict(team_channels), players=result), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--jobs", nargs="*", default=["炼金圣士", "药剂师", "雷神"])
    args = parser.parse_args()
    for path in args.captures:
        inspect(path, args.jobs)
        gc.collect()
