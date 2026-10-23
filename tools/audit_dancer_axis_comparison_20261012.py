"""Read-only counterfactual using uploaded compact casts, NOT a full rescore."""
import copy
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


def main():
    detail = json.loads((ROOT/'work/nanako_20261003_201231_score_20261012.json').read_text(encoding='utf8'))[0]['score_detail']
    t = detail['compact_timeline']
    def decode(key):
        at = 0
        for row in t[key]:
            at += row[0]
            yield dict(start=at/t['q'], end=(at+row[1])/t['q'], skill=t['n'][row[2]], extra=row[3:])
    effects = list(decode('e'))
    burst = [(r['start'], r['end']) for r in effects if r['skill'].startswith('精神爆发 ·') and r['extra']==[0]]
    accel = [(r['start'], r['end']) for r in effects if r['skill'].startswith('时间加速 ·') and r['extra']==[0]]
    output, at = [], 0
    for row in t['o']:
        at += row[0]
        output.append((at/t['q'], (at+row[1])/t['q']))
    duration = t['d']/t['q']
    blocked, last = [], 0
    for a,b in output:
        if a > last: blocked.append((last,a))
        last = b
    if last < duration: blocked.append((last,duration))
    build = copy.deepcopy(detail['scoring_equipment']['build'])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    runtime = LegacyTimelineService(store,calculator)._runtime(build,dict(window=duration,mode='long',cocktail=False,blockedIntervals=blocked,burstIntervals=burst,accelIntervals=accel))
    runtime._skill_burst_schedule_override = dict(window=duration,burst=burst,accel=accel,cocktail=False)
    runtime._skill_burst_blocked_intervals = blocked
    _,summary = calculator.calculate(copy.deepcopy(build))
    runtime._skill_burst_base_summary = summary
    metrics = {}
    for m in runtime._skill_burst_all_learned_metrics(summary,[]):
        key=runtime._skill_burst_skill_key(m['skill'])
        if key not in metrics or runtime._skill_burst_candidate_metric_score(m)>runtime._skill_burst_candidate_metric_score(metrics[key]): metrics[key]=m
    actions = runtime._skill_burst_support_actions(metrics)
    actions_by_key = {runtime._skill_burst_skill_key(x['skill']):x for x in actions}
    result = dict(not_full_rescore=True,missing=['original packets','recipient Buff states','Boss HP','old algorithm runtime','recorded per-cast speed'],lanes={})
    # Fixed-output-axis ablation: move only the Nine Heavens buff intervals.
    # HP is deliberately constant in each scenario because this old cloud row
    # contains no HP curve. This is sensitivity, not a legal rotation/rescore.
    theory_segments=list(decode('t'))
    base_windows=runtime._skill_burst_timeline_attr_windows(theory_segments,actions,duration,include_periodic_display_attrs=False)
    nine_action=next(x for x in actions if x['skill']=='九天之舞2阶')
    without_nine=[x for x in base_windows if x['action']['skill']!='九天之舞2阶']
    result['nine_timing_sensitivity']=[]
    for hp in (100,75,50):
        runtime._skill_burst_boss_hp_segments=(((0.0,float(hp)),(duration,float(hp))),)
        for variant in ('none','theory','actual'):
            windows=list(without_nine)
            if variant!='none':
                flag=0 if variant=='actual' else 1
                windows.extend(dict(start=x['start'],end=x['end'],action=nine_action) for x in effects if x['skill']=='九天之舞2阶 · 团队Buff' and x['extra']==[flag])
            total=0.0
            covered=0
            for row in theory_segments:
                key=runtime._skill_burst_skill_key(row['skill'])
                if key not in metrics or not any(a<=row['start']<b for a,b in output): continue
                attrs=runtime._skill_burst_active_attrs_from_timeline_windows(windows,row['start'])
                attrs=runtime._skill_burst_attrs_during_action(attrs,actions_by_key.get(key))
                total+=runtime._skill_burst_window_metric_score(metrics[key],attrs,row['start'],duration,runtime._skill_burst_sim_cast_time(key,row['start'],attrs))
                covered+=bool(attrs.get('生命低于50额外系数%'))
            result['nine_timing_sensitivity'].append(dict(hypothetical_constant_hp=hp,variant=variant,total=total,covered_casts=covered))
    runtime._skill_burst_boss_hp_segments=None
    for lane in ('a','t'):
        segments = list(decode(lane))
        windows = runtime._skill_burst_timeline_attr_windows(segments,actions,duration,include_periodic_display_attrs=False)
        rows=[]
        start_time=time.perf_counter()
        for row in segments:
            key=runtime._skill_burst_skill_key(row['skill'])
            if key not in metrics: continue
            attrs=runtime._skill_burst_active_attrs_from_timeline_windows(windows,row['start'])
            attrs=runtime._skill_burst_attrs_during_action(attrs,actions_by_key.get(key))
            full=runtime._skill_burst_full_metric_score(metrics[key],attrs,row['start'])
            # Identical current model and duration rule for both uploaded lanes.
            cast_time=runtime._skill_burst_sim_cast_time(key,row['start'],attrs)
            clipped=runtime._skill_burst_prorate_window_score(full,metrics[key],attrs,row['start'],duration,cast_time)
            rows.append(dict(skill=key,start=row['start'],end=row['end'],full=full,clipped=clipped,model_cast_time=cast_time,attrs=attrs))
        totals=defaultdict(lambda:dict(count=0,full=0,clipped=0))
        for row in rows:
            if not any(a<=row['start']<b for a,b in output): continue
            for key in ('full','clipped'): totals[row['skill']][key]+=row[key]
            totals[row['skill']]['count']+=1
        wind=[r for r in rows if r['skill']=='烈风痴狂']
        delays=[]
        for prev,cur in zip(wind,wind[1:]):
            ready=runtime._skill_burst_cooldown_ready_at('烈风痴狂',metrics['烈风痴狂']['base_cooldown'],prev['start'],prev['attrs'])
            # Only delay in output-enabled time, excludes invulnerable/intermission time.
            delay=sum(max(0,min(cur['start'],b)-max(ready,a)) for a,b in output)
            delays.append(dict(start=cur['start'],prev=prev['start'],ready=ready,output_delay=delay,earlier_than_ready=cur['start']<ready-.03))
        result['lanes'][lane]=dict(totals=dict(totals),wind=wind,wind_base_cooldown=metrics['烈风痴狂']['base_cooldown'],wind_delays=delays,seconds=time.perf_counter()-start_time)
    path=ROOT/'work/dancer_axis_comparison_20261012.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    for lane,r in result['lanes'].items():
        print(lane,json.dumps(dict(total=sum(x['clipped'] for x in r['totals'].values()),skills=r['totals'],wind_delay=sum(x['output_delay'] for x in r['wind_delays']),early=sum(x['earlier_than_ready'] for x in r['wind_delays']),seconds=r['seconds']),ensure_ascii=False),flush=True)
    print(path)


if __name__=='__main__': main()
