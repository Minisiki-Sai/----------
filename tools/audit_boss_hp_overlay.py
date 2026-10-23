"""Replay the production HP ledger / compact snapshot / party overlay projection."""
import sys
import json
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.models import BossHealthEvent, CombatEvent
from dragon_nest_builder.native.overlay_lifecycle import party_boss_health
from dragon_nest_builder.live_dps.nest_hp_reference import profiles

def audit(path):
    source=V576DiagnosticReplaySource(Path(path),Path('dragon_nest_builder/data'))
    try:
        events=source.poll()
        agg=CombatAggregator(authoritative_boss_max_hp=False)
        stats={}
        shown=defaultdict(lambda:{'samples':0,'unknown_current':0,'unknown_max':0,'over_max':0,'max_age_ms':0})
        def sample(t):
            snap=agg.overlay_snapshot(t,refresh_composition=False)
            rows=party_boss_health(snap,{p['source_id'] for p in snap['players']})
            for row in rows:
                s=shown[row['targetId']]; s['samples']+=1
                hp=row['currentHp']; maximum=row['maxHp']
                s['unknown_current']+=hp is None
                s['unknown_max']+=not maximum
                s['over_max']+=hp is not None and maximum is not None and hp>maximum
                s['max_age_ms']=max(s['max_age_ms'],t-round(row['updatedSeconds']*1000))
        last_sample=0
        for e in events:
            t=e.timestamp_ms
            if t-last_sample>=250:
                sample(t);last_sample=t
            agg.ingest(e)
            if isinstance(e,BossHealthEvent):
                s=stats.setdefault(e.target_id,{'name':e.target_name,'hp_count':0,'first_ms':t,'first_hp':e.current_hp,
                    'last_ms':t,'max_seen_hp':e.current_hp,'maxima':set(),'largest_gap_ms':0,'hp_increases':0,'previous_hp':e.current_hp})
                s['hp_count']+=1;s['maxima'].add(e.target_max_hp)
                s['largest_gap_ms']=max(s['largest_gap_ms'],t-s['last_ms'])
                s['last_ms']=t;s['last_hp']=e.current_hp
                s['max_seen_hp']=max(s['max_seen_hp'],e.current_hp)
                s['hp_increases']+=e.current_hp>s['previous_hp']
                s['previous_hp']=e.current_hp
        sample(max((e.timestamp_ms for e in events),default=0))
        for target,s in stats.items():
            s['maxima']=sorted(s['maxima']);s.pop('previous_hp')
            tid=target.rsplit('@',1)[-1]
            s['reference_candidates']={p['label']:p['entities'][tid]['full_hp'] for p in profiles() if tid in p['entities']}
            s['overlay']=dict(shown.pop(target,{}))
        return {'capture':str(path),'entities':stats,'overlay_without_hp_packets':dict(shown)}
    finally:source.close()

if __name__=='__main__':
    result=[]
    for path in sys.argv[1:]:
        report=audit(path);result.append(report)
        dest=Path('outputs/verification/boss_hp_overlay_audit.json')
        dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'capture':Path(path).name,'bosses':len(report['entities']),
            'unknown_max':[s['name'] for s in report['entities'].values() if 0 in s['maxima']],
            'over_max':[s['name'] for s in report['entities'].values() if s['overlay'].get('over_max')]},ensure_ascii=True),flush=True)
