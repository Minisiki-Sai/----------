"""Local-only Blade Dancer timing experiment on a saved full-score bundle.

No upload or production monkeypatch. Damage/haste axes remain fixed. Nine
Heavens' two stages move together; orphan stage-one casts remain occupied.
"""
import argparse
import copy
import json
import pickle
import sys
import time
from bisect import bisect_left
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from curve_support_experiment_20261013 import free_slots,merged,replacement_map,replace_support,optimize

def pair_stages(segments):
    first=sorted((s for s in segments if s.get('skill')=='九天之舞' and s.get('kind')=='support'),key=lambda s:s['start'])
    second=sorted((s for s in segments if s.get('skill')=='九天之舞2阶' and s.get('kind')=='support'),key=lambda s:s['start'])
    used=set(); pairs=[]
    for s in second:
        eligible=[(i,p) for i,p in enumerate(first) if i not in used
                  and p['end']-1e-8<=s['start']<=p['end']+5+1e-8]
        if not eligible: raise ValueError('Stage two without a valid stage-one prerequisite')
        i,p=max(eligible,key=lambda x:x[1]['end']); used.add(i); pairs.append((p,s))
    return pairs,[p for i,p in enumerate(first) if i not in used]

def candidate_block_offsets(a,b,slots,event_times,duration,delay=10):
    length=b-a; options={0.0}
    for x,y in slots:
        lo=max(a,x); hi=min(a+delay,y-length)
        if hi<lo: continue
        values={lo,hi}
        left=bisect_left(event_times,lo+length)
        right=bisect_left(event_times,hi+length+duration+1e-8)
        for t in event_times[left:right]:
            values.update((t-length,t-length-duration+1e-6))
        options.update(t-a for t in values if lo<=t<=hi)
    return sorted(options)

def run(bundle,output):
    from dragon_nest_builder.data_loader import DataStore,norm
    from dragon_nest_builder.shared import display_value
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine,canonical_skill
    from dragon_nest_builder.live_dps.theory_plan_builder import _team_score_theory_damage_objective
    from dragon_nest_builder.mixins.skill_timeline_schedule import SkillTimelineScheduleMixin,bind_host_globals
    bind_host_globals({'display_value':display_value,'norm':norm})
    data=pickle.loads(bundle.read_bytes()); store=DataStore.load(); engine=TeamScoreEngine(store)
    for k,v in data['engine_config'].items(): setattr(engine,k,v)
    player=next(p for p in data['snapshot'].players if p.job=='刀锋舞者'); source=player.source_id
    plans=copy.deepcopy(data['plans']); plan=plans[source]; context=data['context']
    offset=float(plan['_score_time_offset']); windows=data['windows']
    relative=tuple((max(0,a-offset),b-offset) for a,b in windows if b>offset)
    def score():
        return engine._support_scores(data['snapshot'],player,player.job,windows,
            theory_plan=plan,boss_target_ids=data['targets'],counterfactual_context=context)
    def detail_dict(details):
        return [dict(skill=d.skill_name,actual_gain=d.actual_contribution,theory_gain=d.theory_contribution,
            completion=d.support_benefit_completion,actual_coverage=d.actual_coverage,
            theory_coverage=d.theory_coverage,actual_damage_coverage=d.effective_damage_coverage,
            theory_damage_coverage=d.effective_damage_theory_coverage) for d in details]
    before=score(); old_row=next(r for r in data['rows'] if r.source_id==source)
    started=time.perf_counter()
    ghost=optimize(engine,data['snapshot'],windows,data['targets'],plans,context)
    pairs,orphans=pair_stages(plan['segments'])
    expectation=next(e.expectation for e in context.effects if e.source_id==source and e.expectation.skill_key=='九天之舞2阶')
    expectation=engine._support_expectations_with_glyphs((expectation,),plan)[0]
    weights,streams=_team_score_theory_damage_objective(plans,data['snapshot'].players,relative)
    events={}
    for recipient,rows in streams.items():
        total=sum(v for t,v in rows)
        for t,v in rows:
            gain=engine._configured_support_effect_gain_rate_for_recipient(expectation,recipient,t+offset,context.boss_hp_segments)
            events[t]=events.get(t,0)+v*weights[recipient]/max(total,1e-9)*gain
    times=sorted(events); prefix=[0.0]
    for t in times: prefix.append(prefix[-1]+events[t])
    def value(end): return prefix[bisect_left(times,end+expectation.duration)]-prefix[bisect_left(times,end)]
    cdr=[]; speed_edges=[]
    for w in plan.get('external_windows',[]):
        e=w.get('team_effect',{}); attrs=e.get('attrs',{})
        amount=float(attrs.get('冷却缩减%',0))
        if amount: cdr.append((w['start'],w['end'],amount,str((e.get('provider'),w.get('source')))))
        if any(('动作速度' in k or k=='攻速%') and float(v or 0) for k,v in attrs.items()):
            speed_edges.extend((w['start'],w['end']))
    class CD(SkillTimelineScheduleMixin):
        def _skill_burst_mode_boundary_points(self): return ()
        def _skill_burst_sim_cdr_percent_at(self,t,attrs): return 0.
        def _skill_burst_skill_key(self,skill): return skill
        def _skill_summon_min_cooldown(self,skill): return None
        def _skill_burst_is_refreshable_periodic_damage(self,skill): return False
        def _skill_glyph_effect_value(self,skill,effect): return 0.
    cd=CD(); attrs={'__cdr_windows__':cdr}
    def ready(t): return cd._skill_burst_cooldown_ready_at('九天之舞2阶',expectation.cooldown,t,attrs)
    proposed=[]; checks=[]
    all_first=sorted([p for p,s in pairs]+orphans,key=lambda s:s['start'])
    for i,(p,s) in enumerate(pairs):
        others=[x for x in plan['segments'] if x is not p and x is not s
                and not x.get('timeline_hidden') and x.get('kind')!='invalid']
        slots=free_slots([(x['start'],x['end']) for x in others],relative)
        options=candidate_block_offsets(p['start'],s['end'],slots,times,expectation.duration)
        next_one=next((x['start'] for x in all_first if x['start']>p['start']+1e-8),float('inf'))
        next_two=pairs[i+1][1]['start'] if i+1<len(pairs) else float('inf')
        prev_one=proposed[-1][0][0] if proposed else None
        prev_two=proposed[-1][1][0] if proposed else None
        valid=[0.]
        for delta in options:
            if delta<=1e-8: continue
            a=p['start']+delta; b=s['start']+delta; end=s['end']+delta
            if any(p['start']+1e-8<edge<end-1e-8 for edge in speed_edges): continue
            if ready(a)>next_one+1e-8 or ready(b)>next_two+1e-8: continue
            if prev_one is not None and (a<ready(prev_one)-1e-8 or b<ready(prev_two)-1e-8): continue
            valid.append(delta)
        delta=max(valid,key=lambda x:(value(s['end']+x),-x))
        proposed.append(((p['start']+delta,p['end']+delta),(s['start']+delta,s['end']+delta)))
        checks.append(dict(before_start=p['start']+offset,after_start=p['start']+delta+offset,
                           delay=delta,legal_delays=len(valid)-1))
    candidate=replace_support(plan,'九天之舞',sorted([pair[0] for pair in proposed]+[(p['start'],p['end']) for p in orphans]))
    candidate=replace_support(candidate,'九天之舞2阶',[pair[1] for pair in proposed])
    current=score(); mapping=replacement_map(current[2],context.effects,source,canonical_skill)
    def exact(m):
        _,parts=engine._counterfactual_support_contributions(context,replacement_source_id=source,replacement_intervals=m)
        return sum(parts.get((source,*k),0) for k in m)
    end=max(data['snapshot'].elapsed_seconds,data['snapshot'].dps_elapsed_seconds,data['snapshot'].boss_effect_current_seconds)
    intervals=engine._planned_support_intervals(candidate,expectation,end,engine._combat_modifier_windows(data['snapshot'],source))
    intervals=merged((max(a,x),min(b,y)) for a,b in intervals for x,y in windows if min(b,y)>max(a,x))
    trial={**mapping,(expectation.skill_key,expectation.effect_key,expectation.kind):tuple(intervals)}
    old_gain,new_gain=exact(mapping),exact(trial)
    accepted=new_gain>old_gain+1e-9
    if accepted: plan.update(candidate)
    after=score(); elapsed=time.perf_counter()-started
    # The production aggregate uses theoretical-gain weights. Check the baseline
    # against the saved full native result before reporting a projected score.
    def aggregate(details):
        rows=[d for d in details if d.included_in_score]
        return 100*sum(d.actual_contribution for d in rows)/sum(d.theory_contribution for d in rows)
    assert abs(aggregate(before[2])-old_row.support_score)<1e-7
    old_details={d.skill_name:d for d in before[2]}
    for d in after[2]:
        for field in ('actual_contribution','actual_intervals','actual_coverage','effective_damage_coverage'):
            assert getattr(d,field)==getattr(old_details[d.skill_name],field),(d.skill_name,field)
    for key,p in plans.items():
        assert [s for s in p['segments'] if s.get('kind')=='damage']==[s for s in data['plans'][key]['segments'] if s.get('kind')=='damage']
    report=dict(capture=data['capture'],source=source,name=player.name,
        before_support=old_row.support_score,after_support=aggregate(after[2]),
        before_details=detail_dict(before[2]),after_details=detail_dict(after[2]),
        ghost_experiment=ghost,nine_experiment=dict(pairs=len(pairs),orphans=len(orphans),
            orphan_starts=[p['start']+offset for p in orphans],moves=checks,
            before_provider_gain=old_gain,candidate_provider_gain=new_gain,accepted=accepted),
        optimization_and_support_evaluation_seconds=elapsed,baseline_full_score_seconds=data['score_seconds'],
        modified_full_score_seconds=None,extra_theory_axes=0,actual_unchanged=True,damage_actions_unchanged=True,
        production_enabled=False,uploaded=False,
        limitations=['Saved full-capture bundle; before aggregate verified against full-score row.',
            'Auxiliary-only experiment: damage/haste casts frozen, no output-axis repricing.',
            'Nine moves both stages in free slots, same speed regime, at most 10 seconds later.',
            'No end-to-end modified scoring-time claim; no guaranteed global optimum.'])
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('bundle',type=Path); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); run(a.bundle,a.output)
