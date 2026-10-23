"""Read-only capture replay; report confirmed Boss ends and acceleration states."""
import sys
import json
from pathlib import Path
from dataclasses import asdict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import BossHealthEvent, BossActionEvent
from dragon_nest_builder.live_dps.boss_hp import BossHpLedger

def audit(path):
    source = V576DiagnosticReplaySource(Path(path), Path('dragon_nest_builder/data'))
    try:
        events = source.poll()
        ledger = BossHpLedger(authoritative_max_hp=False)
        seen, ends, buffs = {}, [], {}
        for event in events:
            if isinstance(event, BossHealthEvent):
                seen[event.target_id] = {'name':event.target_name, 'last_hp':event.current_hp,
                                         'last_ms':event.timestamp_ms}
                ledger.ingest_health(event)
                if event.current_hp <= 0:
                    ends.append(asdict(event))
            if isinstance(event, BossActionEvent) and event.packet_kind == 'boss_completion_trigger':
                ends.append(asdict(event))
            skill = getattr(event, 'skill_name', '')
            if skill in {'灼热火焰', '精神爆发', '时间加速'} and hasattr(event, 'effect_key'):
                row = asdict(event)
                key = (type(event).__name__, skill, row.get('effect_key'), row.get('status_kind'), row.get('duration_ms'))
                if key not in buffs:
                    buffs[key] = {'count':0, 'sample':row}
                buffs[key]['count'] += 1
        return {'capture':str(path), 'boss_hp_entities':seen, 'confirmed_ends':ends,
                'ended_ids':ledger.ended_target_ids(), 'acceleration_states':list(buffs.values())}
    finally:
        source.close()

if __name__ == '__main__':
    reports=[]
    for path in sys.argv[1:]:
        row=audit(path)
        reports.append(row)
        print(json.dumps({'capture':path,'hp_entities':len(row['boss_hp_entities']),
                          'confirmed_ends':row['confirmed_ends'], 'acceleration_states':row['acceleration_states']},ensure_ascii=False),flush=True)
    Path('outputs/verification/overlay_lifecycle_audit.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
