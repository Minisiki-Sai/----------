"""Additional diagnostic evidence and counterfactuals, not runtime patches."""
from audit_hit_matching_20261020 import *
from collections import Counter, defaultdict

def raw():
    dest = OUT / 'decoded.pkl'
    if dest.exists():
        return pickle.loads(dest.read_bytes())
    from dragon_nest_builder.data_loader import DATA_DIR
    from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
    source = V576DiagnosticReplaySource(CAP, DATA_DIR)
    try:
        events = source.poll()
    finally:
        source.close()
    dest.write_bytes(pickle.dumps(events, protocol=5))
    return events

def probe():
    events = raw()
    obs, _, _, speeds = pickle.loads((OUT/'observations.pkl').read_bytes())
    who = {o.skill_id:o.source_id for o in obs if str(o.skill_id) in {'2215','5216'}}
    selected = [e for e in events if (
        getattr(e,'source_id',None) in who.values() and
        (str(getattr(e,'skill_id','')) in {'2301','2215','5216','5405'} or
         type(e).__name__=='BossStatusEvent'))]
    (OUT/'source_events.json').write_text(json.dumps([dict(type=type(e).__name__, **asdict(e))
        for e in selected],ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    from act_hit_timeline_comparator import stage_estimator as stage
    old_order = stage.prefer_packet_order
    repo = repository()
    comparisons = []
    for o in obs:
        if str(o.skill_id) != '2215':
            continue
        speed = speeds.get((o.source_id,o.cast_timestamp_ms),0.)
        variants = variants_for_observation(repo,o,speed)
        try:
            stage.prefer_packet_order = lambda result,*a,**kw: result
            best,_ = best_comparison(variants,o.hit_times,o.hit_amounts,o.critical_flags,
                external_speed_percent=speed,observation=o)
        finally:
            stage.prefer_packet_order = old_order
        comparisons.append(comparison_to_dict(best,o))
    (OUT/'fire_without_order_postpass.json').write_text(
        json.dumps(comparisons,ensure_ascii=False,indent=2),encoding='utf-8')
    print('raw events',len(events),'selected events',len(selected),flush=True)

if __name__=='__main__':
    probe()
