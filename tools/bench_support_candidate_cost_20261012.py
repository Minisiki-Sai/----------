"""Synthetic one-effect benchmark; not production optimization or a SLA."""
import bisect
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine,_SupportExpectation,_SupportEffectInstance,_SupportCounterfactualContext

def main():
    engine=TeamScoreEngine(DataStore.load())
    n=30000
    events=tuple(CombatEvent(i*30,'dealer','Dealer','刀锋舞者','1','烈风痴狂',1000+(i%97)*10,target_id='boss',target_name='Boss') for i in range(n))
    expectation=_SupportExpectation(skill_key='幽灵之魂',skill_name='幽灵之魂',effect_key='四维%',theory=1,magnitude=60,kind='团队Buff',cooldown=60,duration=15)
    context=_SupportCounterfactualContext(team_damage_events=events,total_team_damage=sum(e.amount for e in events),effects=(_SupportEffectInstance(source_id='support',source_job='刀锋舞者',expectation=expectation,intervals=((0,900),)),),intervals_by_identity={})
    st=time.perf_counter()
    full=engine._counterfactual_support_contributions(context)[0]['support']
    preparation=time.perf_counter()-st
    identity=('幽灵之魂','四维%','团队Buff')
    candidates=[(i*8+.003,i*8+15.003) for i in range(100)]
    st=time.perf_counter()
    exact=[]
    for a,b in candidates[:10]:
        values=engine._counterfactual_support_contributions(context,replacement_source_id='support',replacement_intervals={identity:((a,b),)})[0]
        exact.append(values.get('support',0))
    exact_seconds=time.perf_counter()-st
    st=time.perf_counter()
    timestamps=[i*.03 for i in range(n)]
    prefix=[0.0]
    for value in context.baseline_damage_by_event: prefix.append(prefix[-1]+value)
    index_seconds=time.perf_counter()-st
    st=time.perf_counter()
    indexed=[full*(prefix[bisect.bisect_right(timestamps,b)]-prefix[bisect.bisect_left(timestamps,a)])/prefix[-1] for a,b in candidates]
    indexed_seconds=time.perf_counter()-st
    error=max(abs(a-b) for a,b in zip(exact,indexed))
    assert error<1e-8,(error,exact[:2],indexed[:2])
    result=dict(synthetic=True,scope='one constant effect, one recipient profile; no stacking changes, HP-dependent gain, or scheduling',events=n,baseline_preparation_seconds=preparation,full_evaluator_10_candidates_seconds=exact_seconds,prefix_build_seconds=index_seconds,prefix_100_candidates_seconds=indexed_seconds,max_error_percent_points=error)
    (ROOT/'work/support_candidate_cost_20261012.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
