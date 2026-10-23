"""Counterfactual disabling only the post-fit packet-order tie-breaker."""
from audit_hit_matching_20261020 import *
from act_hit_timeline_comparator import stage_estimator as stage

def distance(c):
    vals=[]
    for r in c.rows:
        h=r.expected
        if h and not h.optional and h.start is not None and r.actual_time is not None:
            vals.append(max(h.start-r.actual_time,r.actual_time-(h.end if h.end is not None else h.start),0))
    return dict(count=len(vals),mean=sum(vals)/max(1,len(vals)),maximum=max(vals,default=0))

def run():
    repo=repository()
    obs,_,_,speeds=pickle.loads((OUT/'observations.pkl').read_bytes())
    selected={'火焰风暴','怨恨之像','冰尖柱','黑暗神殿','火径','地狱火海','野兽之灵','召唤黑洞'}
    old=stage.prefer_packet_order
    rows=[]
    for o in obs:
        if o.skill_name not in selected or record_kind(o)!='施放' or not o.hit_times:
            continue
        speed=speeds.get((o.source_id,o.cast_timestamp_ms),0.)
        variants=variants_for_observation(repo,o,speed,condition_choice='自动/无条件选择',include_optional=False)
        variants=select_cast_routes(variants,o,obs,speeds)
        before,_=best_comparison(variants,o.hit_times,o.hit_amounts,o.critical_flags,external_speed_percent=speed,observation=o)
        try:
            stage.prefer_packet_order=lambda result,*a,**kw:result
            after,_=best_comparison(variants,o.hit_times,o.hit_amounts,o.critical_flags,external_speed_percent=speed,observation=o)
        finally:
            stage.prefer_packet_order=old
        if before is None or after is None:continue
        b,a=distance(before),distance(after)
        if b['mean']-a['mean']>.1:
            rows.append(dict(skill=o.skill_name,job=o.job,time=o.cast_timestamp_ms,
                before_distance=b,after_distance=a,
                before=comparison_to_dict(before,o),after=comparison_to_dict(after,o)))
    (OUT/'order_ab.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    from collections import Counter
    print('changed',len(rows),Counter(x['skill']for x in rows),flush=True)

if __name__=='__main__':run()
