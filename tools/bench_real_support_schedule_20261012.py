"""Offline scoring baseline and exact final-evaluator timing experiment.

No cloud uploads, installer changes, or replacement of production score caches.
The dump is local trusted diagnostic input for further controlled ablations.
"""
import argparse
import copy
import hashlib
import json
import pickle
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class NoCache:
    def load(self,*a,**k): return None
    def save(self,*a,**k): pass
    def discard(self,*a,**k): pass

def baseline(capture,output,after_score=None,transform_plans=None):
    from dragon_nest_builder import v410_native_core as native,timeline_parallel as pool
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.native import replay_score_service
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
    native.V410ScoreSyncManager=lambda:SimpleNamespace(status=lambda:{},close=lambda:None)
    replay_score_service.record_usage_event=lambda *a,**k:None
    captured={}
    original=TeamScoreEngine._build_support_counterfactual_context
    def remember(self,snapshot,windows,targets=None,plans=None):
        context=original(self,snapshot,windows,targets,plans)
        if transform_plans is not None and plans:
            transform_plans(core,self,snapshot,windows,targets,plans,context)
        captured.update(snapshot=snapshot,windows=windows,targets=targets,context=context,engine=self)
        return context
    TeamScoreEngine._build_support_counterfactual_context=remember
    store=DataStore.load()
    core=native.NativeSimulatorCore(store,BuildCalculator(store))
    core._queue_replay_score_sync=lambda *a,**k:None
    core._team_score_disk_cache=NoCache()
    core._replay_disk_cache.save=lambda *a,**k:None
    try:
        st=time.perf_counter()
        replay=core.load_replay(str(capture),lambda *a:None)
        load_seconds=time.perf_counter()-st
        print(f'REPLAY {load_seconds:.3f}s',flush=True)
        st=time.perf_counter()
        last=[0.0]
        def progress(value,message,*args):
            now=time.perf_counter()
            if now-last[0]>10 or value>=99:
                print(f'{now-st:.2f}s {value}% {message}',flush=True)
                last[0]=now
        result=core.score_replay(dict(sourceHash=replay['sourceHash'],sourceFile=str(capture),bossPreset='红龙佩达'),progress)
        seconds=time.perf_counter()-st
        if result.get('theoryPlanner')!='v401-full-team-score': raise RuntimeError(result.get('theoryPlannerError',result.get('theoryPlanner')))
        planner=core.score_theory_planner
        rows=core._score_results[result['scoreKey']]
        # Do not pickle a bound engine or any UI/worker state.
        engine=captured.pop('engine')
        engine_config={k:copy.deepcopy(getattr(engine,k)) for k in ('_score_boss_name','_score_boss_defense','_score_boss_crit_resist','_score_boss_element_resist','_score_crit_profiles_by_source')}
        bundle=dict(captured,engine_config=engine_config,plans=copy.deepcopy(planner._team_score_theory_plans),specs=copy.deepcopy(planner._team_score_replan_specs_by_source),rows=rows,capture=str(capture),score_seconds=seconds,replay_seconds=load_seconds)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.with_suffix('.pickle').write_bytes(pickle.dumps(bundle,protocol=pickle.HIGHEST_PROTOCOL))
        report={k:bundle[k] for k in ('capture','score_seconds','replay_seconds')}
        report.update(players=[dict(name=r.name,job=r.job,source_id=r.source_id,output_score=r.output_score,support_score=r.support_score,support=[asdict(d) for d in r.support_details]) for r in rows],damage_events=len(captured['context'].team_damage_events),effects=[dict(source=e.source_id,job=e.source_job,skill=e.expectation.skill_name,effect=e.expectation.effect_key,duration=e.expectation.duration,cooldown=e.expectation.cooldown) for e in captured['context'].effects])
        report['final_score_diagnostics'] = getattr(engine, '_score_runtime_diagnostics', {})
        report['implementation_sha256'] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'dragon_nest_builder/mixins/skill_timeline_effects.py',
            'dragon_nest_builder/mixins/skill_timeline_support.py',
            'dragon_nest_builder/mixins/extreme_engine.py',
            'dragon_nest_builder/mixins/skill_timeline_state.py',
            'dragon_nest_builder/mixins/timeline_runtime.py',
            'dragon_nest_builder/effect_stacking.py',
            'dragon_nest_builder/mixins/team_effects.py',
            'dragon_nest_builder/timeline_parallel.py',
            'dragon_nest_builder/live_dps/theory_plan_builder.py',
        )}
        report['theory_diagnostics'] = copy.deepcopy(getattr(planner, '_team_score_theory_diagnostics', {}))
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(f'BASELINE_DONE {seconds:.3f}s {output}',flush=True)
        if after_score is not None:
            after_score(core,bundle,engine)
    finally:
        core.shutdown()
        pool.reset_timeline_process_pool()
        TeamScoreEngine._build_support_counterfactual_context=original

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('capture',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    baseline(a.capture.resolve(),a.output)
