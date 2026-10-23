"""Fresh replay plus short real-data level integration probes; never upload."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DataStore, DATA_DIR
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.combat_skill_levels import infer_combat_skill_levels, apply_combat_levels_to_build
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, canonical_skill


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('capture',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    store=DataStore.load()
    calculator=BuildCalculator(store)
    source=V576DiagnosticReplaySource(args.capture,DATA_DIR,{})
    agg=CombatAggregator()
    started=tick=time.monotonic()
    try:
        for batch in source.iter_batches():
            for event in batch:
                agg.ingest(event)
            if time.monotonic()-tick>20:
                print('replay seconds',round(time.monotonic()-started,1),flush=True)
                tick=time.monotonic()
    finally:
        source.close()
    snapshot=agg.snapshot(include_events=True)
    facts=(snapshot.total_damage, tuple((p.source_id,p.damage) for p in snapshot.players),
           tuple(snapshot.combat_events), tuple(snapshot.skill_cast_events))
    reports=infer_combat_skill_levels(snapshot,store)
    service=LegacyTimelineService(store,calculator)
    engine=TeamScoreEngine(store)
    root=service.extreme_runtime({'first_job':'弓箭手','second_job':'狙翎'})
    results=[]
    # Bounded 30-second simulations test the exact production axis path. They
    # are not full-raid scores and must not be reported as such.
    for p in snapshot.players:
        report=reports.get(p.source_id)
        if not report:
            continue
        original=root._extreme_team_score_build_for_job(p.job)
        original['skill_glyphs']={'rows':[]}
        changed=apply_combat_levels_to_build(original,report)
        pair=[]
        for label,build in [('baseline',original),('combat_levels',changed)]:
            runtime=service.extreme_runtime(build)
            result=runtime._extreme_team_timeline_result(job=p.job,team=[p.job],effects=[],
                window=30,build_override=build,long_sim_planner=True,score_refinement=False,
                same_environment_theory=True)
            if not result.get('_score_skill_damages') or not result.get('segments'):
                raise AssertionError(f'No real axis: {p.job} {label}')
            pair.append({'variant':label,'damage':result['total'],
                         'skill_damages':result['_score_skill_damages'],
                         'segments':result['segments']})
        learned=engine._combat_learned_skills(snapshot,p.source_id,p.job)
        profiles=engine._job_base_profiles(p.job,learned)
        actual_profiles=engine._profiles_with_standardized_skill_powers(profiles,
            {'_score_skill_damages':pair[1]['skill_damages']})
        powers={canonical_skill(k):v for k,v in pair[1]['skill_damages'].items()}
        matched=0
        for profile in actual_profiles:
            if profile.key in powers:
                assert abs(profile.power-powers[profile.key])<1e-6
                matched+=1
        results.append({'source_id':p.source_id,'name':p.name,'job':p.job,
                        'shared_actual_theory_skill_powers_checked':matched,'probes':pair})
        print(json.dumps({'job':p.job,'shared_skill_values':matched,'probe_damage':[x['damage'] for x in pair]},ensure_ascii=False),flush=True)
    assert facts == (snapshot.total_damage, tuple((p.source_id,p.damage) for p in snapshot.players),
                     tuple(snapshot.combat_events), tuple(snapshot.skill_cast_events))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'capture':str(args.capture),'elapsed_seconds':time.monotonic()-started,
        'full_recording_total_damage':snapshot.total_damage,'raw_facts_unchanged':True,
        'scope':'Fresh full replay; 30-second per-player level integration probes, not full-raid scoring',
        'reports':reports,'axis_probes':results},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print('verified',args.output,flush=True)


if __name__=='__main__':
    main()
