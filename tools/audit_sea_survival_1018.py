"""Read-only score evidence audit; no rescoring/upload."""
import json
from pathlib import Path
from dataclasses import asdict
from collections import defaultdict,Counter
from tools.compare_hp_survival_shadow import load_snapshot
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.live_dps.event_platform import event_platform_for

path=Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/20261018冰灵海龙第一把.dpscap')
snapshot=load_snapshot(path)
platform=event_platform_for(snapshot)
encounters=tuple(BossRecognizer().recognize_snapshot(snapshot))
groups=defaultdict(list)
for e in platform.incoming_damage_events:
    groups[(e.skill_id,e.skill_name,str(e.attribution_method))].append(e)
result={'encounters':[asdict(e) for e in encounters], 'players':[{'id':p.source_id,'job':p.job} for p in snapshot.players],
 'incoming':[{'key':k,'count':len(es),'targets':dict(Counter(e.target_id for e in es)),
 'first':min(e.timestamp_ms for e in es)/1000,'last':max(e.timestamp_ms for e in es)/1000,
 'amounts':Counter(e.amount for e in es).most_common(8),
 'samples':[asdict(e) for e in es[:3]]} for k,es in sorted(groups.items(),key=lambda kv:-len(kv[1]))],
 'tracks':[{'id':t.target_id,'max_hp':t.max_hp,'points':[asdict(p) for p in t.points]} for t in snapshot.player_health_tracks]}
out=Path('outputs/verification/sea_survival_1018.json')
out.write_text(json.dumps(result,ensure_ascii=False,default=str),encoding='utf-8')
print('saved',out,'incoming',len(platform.incoming_damage_events),flush=True)
print(json.dumps(result['encounters'],ensure_ascii=False,default=str),flush=True)
for g in result['incoming'][:20]:print({k:v for k,v in g.items() if k!='samples'},flush=True)
