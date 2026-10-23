"""Offline end-to-end COST prototype, not a production schedule optimizer.

Runs a complete uncached score, then screens immediate/nearby-peak schedules,
evaluates direct support candidates and validates all recipients ONCE. Recorded
haste cast intents are immutable. Candidate screening is approximate; no new
score is uploaded or claimed to be a fully calibrated replacement algorithm.
"""
import argparse
import copy
import hashlib
import json
import statistics
import sys
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench_real_support_schedule_20261012 import baseline

TIMING={'时间加速','精神爆发','鸡尾酒','灼热火焰'}

def digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()

def experiment(core,data,engine,score_call,output):
    from dragon_nest_builder.live_dps.theory_plan_builder import (
        _team_score_theory_damage_objective, _team_score_apply_fixed_axis_damage_glyphs)
    from dragon_nest_builder.mixins.skill_timeline_schedule import SkillTimelineScheduleMixin
    started=time.perf_counter()
    stages={}
    plans=data['plans']; specs=data['specs']; context=data['context']
    offset=float(next(iter(plans.values())).get('_score_time_offset',0))
    horizon=float(next(iter(specs.values()))['window'])
    windows=tuple((max(0,a-offset),b-offset) for a,b in data['windows'] if b>offset)
    weights,streams=_team_score_theory_damage_objective(plans,data['snapshot'].players,windows)
    events=[]
    for source,rows in streams.items():
        total=sum(v for _,v in rows)
        scale=weights[source]/max(total,1e-9)
        events.extend((t,v*scale) for t,v in rows)
    events.sort(); times=[t for t,_ in events]; prefix=[0.0]
    for _,value in events: prefix.append(prefix[-1]+value)
    def value(start,duration):
        return prefix[bisect_left(times,start+duration)]-prefix[bisect_left(times,start)]
    def next_output(t):
        return next((max(t,a) for a,b in windows if b>t+1e-8),horizon)

    class Cooldown(SkillTimelineScheduleMixin):
        # Targeted prototype: generic Soul Eater support CD, no reset/charges.
        def _skill_burst_mode_boundary_points(self): return ()
        def _skill_burst_sim_cdr_percent_at(self,seconds,attrs): return 0.0
        def _skill_burst_skill_key(self,skill): return skill
        def _skill_summon_min_cooldown(self,skill): return None
        def _skill_burst_is_refreshable_periodic_damage(self,skill): return False
        def _skill_glyph_effect_value(self,skill,effect):
            return max((float(g['value']) for g in self.glyphs
                        if g.get('skill')==skill and g.get('effect')==effect),default=0.0)

    grouped=defaultdict(list)
    for effect in context.effects:
        e=effect.expectation
        if e.skill_key in {'幽灵之魂','怨恨之像'} and effect.intervals:
            grouped[(effect.source_id,e.skill_key)].append(effect)
    selected={}; details=[]
    for (source,skill),effects in grouped.items():
        plan=plans[source]; spec=specs[source]
        provider=spec['provider_override']
        segments=[s for s in plan['segments'] if s.get('skill')==skill and s.get('kind')!='invalid']
        cast_time=statistics.median(float(s['end'])-float(s['start']) for s in segments)
        e=effects[0].expectation
        glyphs=plan.get('_score_glyphs',[])
        duration=e.duration*(1+max((float(g['value']) for g in glyphs if g.get('skill')==skill
                                  and g.get('effect')=='增益持续时间增加'),default=0)/100)
        cd=Cooldown(); cd.glyphs=glyphs
        cdr=[]
        for row in plan.get('external_windows',[]):
            effect=row.get('team_effect',{})
            amount=float(effect.get('attrs',{}).get('冷却缩减%',0))
            if amount:
                cdr.append((row['start'],row['end'],amount,
                            str((effect.get('provider'),row.get('source')))))
        attrs={'__cdr_windows__':cdr}
        def schedule(delayed):
            rows=[]; t=next_output(0); probe_count=0
            while t+cast_time<horizon-1e-7:
                chosen=t
                if delayed:
                    upper=max(t,min(horizon-cast_time,t+10.0))
                    choices={t,upper}
                    left=bisect_left(times,t+cast_time)
                    right=bisect_right(times,upper+cast_time+duration)
                    for event_t in times[left:right]:
                        for candidate in (event_t-cast_time,event_t-duration-cast_time+1e-6):
                            if t<=candidate<=upper and next_output(candidate)==candidate:
                                choices.add(candidate)
                    chosen=max(choices,key=lambda x:(value(x+cast_time,duration),-x))
                    probe_count+=len(choices)
                rows.append((chosen,chosen+cast_time))
                t=next_output(max(chosen+cast_time,
                    cd._skill_burst_cooldown_ready_at(skill,e.cooldown,chosen,attrs)))
            return rows,probe_count
        immediate,_=schedule(False); peaks,nprobes=schedule(True)
        # Whole-fight quick check includes any casts lost by delaying.
        values=[sum(value(b,duration) for a,b in casts) for casts in (immediate,peaks)]
        choose_peak=values[1]>values[0]+1e-9
        chosen=peaks if choose_peak else immediate
        selected[(provider,skill)]=chosen
        details.append(dict(source=source,provider=provider,skill=skill,duration=duration,
            immediate_casts=len(immediate),peak_casts=len(peaks),peak_probes=nprobes,
            chosen='peak' if choose_peak else 'immediate',
            immediate=immediate,peaks=peaks,screen_values=values))
    stages['screen_and_cd_seconds']=time.perf_counter()-started
    print('SCREEN',stages['screen_and_cd_seconds'],flush=True)

    # Full final-support evaluator, twice per skill, no recipient-axis reruns.
    t=time.perf_counter()
    for row in details:
        effects=grouped[(row['source'],row['skill'])]
        gains=[]
        for casts in (row['immediate'],row['peaks']):
            replacements={(e.expectation.skill_key,e.expectation.effect_key,e.expectation.kind):
                tuple((b+offset,min(horizon+offset,b+offset+row['duration'])) for a,b in casts)
                for e in effects}
            _,effect_gains=engine._counterfactual_support_contributions(context,
                replacement_source_id=row['source'],replacement_intervals=replacements)
            gains.append(sum(effect_gains.get(engine._support_effect_identity(row['source'],e.expectation),0)
                             for e in effects))
        row['exact_observed_stream_candidate_gains']=gains
        # Keep theory-stream choice: observed damage is only an evaluator cost probe,
        # not allowed to set a theoretical ceiling from a poor captured rotation.
    stages['four_full_support_evaluations_seconds']=time.perf_counter()-t
    print('CANDIDATES',stages['four_full_support_evaluations_seconds'],flush=True)

    t=time.perf_counter(); tasks=[]; sources=[]; haste_hashes={}
    for source,base in specs.items():
        spec=copy.deepcopy(base)
        before={k:v for k,v in spec.get('cast_schedule',{}).items() if k[1] in TIMING}
        for key,casts in selected.items(): spec['cast_schedule'][key]=casts
        after={k:v for k,v in spec.get('cast_schedule',{}).items() if k[1] in TIMING}
        assert before==after
        haste_hashes[source]=digest(before)
        spec['excluded_skill_keys']=plans[source].get('_score_excluded_skill_keys',[])
        tasks.append(spec); sources.append(source)
    runtime=core.score_theory_planner.timeline_editor
    from dragon_nest_builder import timeline_parallel as pool
    from fixed_haste_probe_worker import calculate_task
    # The mixin binds the imported worker function in its own global namespace.
    namespace=runtime._extreme_timeline_tasks_parallel.__func__.__globals__
    original_task=namespace['calculate_extreme_timeline_task']
    namespace['calculate_extreme_timeline_task']=calculate_task
    try:
        results=runtime._extreme_timeline_tasks_parallel(tasks,
            progress_callback=lambda done,index,job:print(f'VALIDATE {done}/{len(tasks)} {job}',flush=True))
    finally:
        namespace['calculate_extreme_timeline_task']=original_task
    stages['one_final_eight_axis_batch_seconds']=time.perf_counter()-t
    merged=dict(plans)
    for source,result in zip(sources,results):
        assert '_diagnostic_own_support_pins' in result, 'Diagnostic worker was bypassed'
        for row in details:
            if row['source']==source:
                actual=[s for s in result.get('segments',[]) if s.get('skill')==row['skill']
                        and s.get('kind')=='support']
                row['validated_owner_cast_count']=len(actual)
                row['validated_owner_first_casts']=[(s['start'],s['end']) for s in actual[:5]]
        _team_score_apply_fixed_axis_damage_glyphs(result,plans[source].get('_score_glyphs',[]))
        merged[source]={**plans[source],**result}
    # Final scoring cost, same captured options. Timing-ablation metadata is held
    # fixed for this cost probe; these experimental numeric scores are not shipped.
    t=time.perf_counter()
    args,kwargs=score_call
    kwargs=dict(kwargs); kwargs['theory_plans']=merged
    kwargs['progress_callback']=lambda *a:None
    engine.score(*args,**kwargs)
    stages['one_final_score_seconds']=time.perf_counter()-t
    added=time.perf_counter()-started
    report=dict(capture=data['capture'],baseline_score_seconds=data['score_seconds'],
        replay_seconds_excluded=data['replay_seconds'],added_seconds=added,
        measured_sequential_score_plus_experiment_seconds=data['score_seconds']+added,
        added_percent=added/data['score_seconds']*100,stages=stages,
        players=len(specs),damage_events=len(context.team_damage_events),theory_events=len(events),
        selected_skills=details,haste_schedule_sha256=haste_hashes,
        haste_cast_intents_unchanged=True,candidate_axis_reruns=0,final_axis_reruns=len(tasks),
        max_delay_seconds=10,production_changes=False,uploaded=False,
        own_short_support_cast_pinning=True,
        limitations=[
            'Single complete capture/run; no cross-capture or repeated median guarantee.',
            'Cost prototype: theory-damage-prefix screening is approximate, not a certified maximum.',
            'Two direct skills present; no Nine Heavens Dance in this party.',
            'Generic cooldown integration/glyphs used; exact final engine validates actions.',
            'Candidate exact evaluator uses observed damage for COST only; does not decide theory ceiling.',
            'Timing inputs and ablation metadata fixed; experimental scores are not production replacements.',
            'Sequential additive experiment includes a redundant final score; integration may reuse one pass.',
            'Final validation reruns eight recipients once, not once for every candidate.',
        ])
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('DONE',json.dumps({k:report[k] for k in ('baseline_score_seconds','added_seconds','stages')},ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('capture',type=Path); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse-baseline',type=Path)
    a=p.parse_args()
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
    if a.reuse_baseline:
        import pickle
        from types import SimpleNamespace
        from dragon_nest_builder.data_loader import DataStore
        from dragon_nest_builder.calculator import BuildCalculator
        from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
        from dragon_nest_builder import timeline_parallel as pool
        data=pickle.loads(a.reuse_baseline.read_bytes())
        store=DataStore.load(); engine=TeamScoreEngine(store)
        for key,value in data['engine_config'].items(): setattr(engine,key,value)
        runtime=LegacyTimelineService(store,BuildCalculator(store)).extreme_runtime(copy.deepcopy(store.defaults['build1']))
        core=SimpleNamespace(score_theory_planner=SimpleNamespace(timeline_editor=runtime))
        args=(data['snapshot'],)
        kwargs=dict(boss_target_ids=data['targets'],theory_plans=data['plans'],
                    actual_timeline_events=data['snapshot'].skill_cast_events,allow_parallel=True)
        try:
            with pool.formal_score_worker_budget(8):
                executor=pool.timeline_process_pool(8)
                warm=[executor.submit(pool._timeline_worker_warmup,0.25) for _ in range(8)]
                for future in warm: future.result()
                print('WORKERS_WARM',pool.timeline_process_pool_status(),flush=True)
                experiment(core,data,engine,(args,kwargs),a.output)
        finally:
            pool.reset_timeline_process_pool()
        return
    original=TeamScoreEngine.score; calls=[]
    def remember(self,*args,**kwargs):
        calls.append((args,dict(kwargs)))
        return original(self,*args,**kwargs)
    TeamScoreEngine.score=remember
    try:
        baseline(a.capture,a.output.with_name(a.output.stem+'_baseline.json'),
            after_score=lambda core,data,engine:experiment(core,data,engine,calls[-1],a.output))
    finally:
        TeamScoreEngine.score=original

if __name__=='__main__': main()
