"""Compare independent wire current HP with client HP/difficulty candidates.

Never use BossHealthEvent.target_max_hp: that field is currently derived from
the old reference table and would make this audit circular.
"""
import json, re, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_dungeon_map_catalog import parse_dnt, find_resource_file
from tools.build_live_dps_monster_catalog import localized_name

ROOT = Path(r'D:\DragonNest\Newborn\Resource_unpacked_all_20260916_update')
OUT = Path('outputs/verification/nest_hp_audit')

def client_catalog():
    text = find_resource_file(ROOT, 'resource/uistring/uistring.xml').read_text(encoding='utf-8')
    strings = {int(k): v for k, v in re.findall(r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>', text, re.S)}
    weights = defaultdict(list)
    for file in ('monsterweighttable_nest.dnt', 'monsterweighttable.dnt', 'monsterweighttable_stage.dnt'):
        for row in parse_dnt(find_resource_file(ROOT, 'resource/ext/' + file)):
            weights[int(row['_MonsterTableIndex'])].append(dict(table=file, row=row['RowId'],
                difficulty=row['_Difficulty'], multiplier=row['_HPWeight'], ai=row.get('_CustomAI', '')))
    catalog = {}
    for file in ('monstertable.dnt', 'monstertable_nest.dnt', 'monstertable_nest70.dnt'):
        for row in parse_dnt(find_resource_file(ROOT, 'resource/ext/' + file)):
            if row['_HP'] <= 0: continue
            tid = row['RowId']
            catalog[tid] = dict(template=tid, name=localized_name(row,strings), base_hp=row['_HP'],
                table=file, ai=row.get('_CustomAI'), boss=row.get('_BossKillCheck'), grade=row.get('_Grade'),
                candidates=[dict(w, hp=int(row['_HP'] * w['multiplier'])) for w in weights[tid]])
    return catalog

def audit(path, catalog):
    from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
    from dragon_nest_builder.live_dps.models import BossHealthEvent, CombatEvent
    from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
    from dragon_nest_builder.live_dps.boss_health_wire import decode_boss_health
    wire = defaultdict(list)
    original = _V576PacketEventDecoder.feed_events
    def feed(decoder, packet):
        result = original(decoder, packet)
        if packet.transport.upper() == 'UDP':
            for hp in decode_boss_health(packet.payload, packet.inferred_direction()):
                template = decoder.monster_entities.get(hp.actor_id)
                if template is not None:
                    wire[decoder._monster_event_id(hp.actor_id)].append(hp.current_hp)
        return result
    _V576PacketEventDecoder.feed_events = feed
    source = None
    try:
        source = V576DiagnosticReplaySource(path, Path('dragon_nest_builder/data'))
        events = source.poll()
        rows = {}
        for e in events:
            if not isinstance(e, (BossHealthEvent, CombatEvent)): continue
            target = e.target_id
            tid = target.rsplit('@',1)[-1]
            if not tid.isdigit() or int(tid) not in catalog: continue
            ref = catalog[int(tid)]
            if not ref['boss'] and (ref['grade'] or 0) < 3: continue
            row = rows.setdefault(target, dict(target=target, template=int(tid), name=ref['name'],
                max_observed_current_hp=0, hp_samples=0, damage=0))
            if isinstance(e, BossHealthEvent) and e.current_hp is not None:
                row['max_observed_current_hp'] = max(row['max_observed_current_hp'], e.current_hp)
                row['hp_samples'] += 1
            elif isinstance(e, CombatEvent) and not e.healing:
                row['damage'] += e.amount
        for row in rows.values():
            samples = wire.get(row['target'], ())
            if samples:
                row['max_observed_current_hp'] = max(samples)
                row['min_observed_current_hp'] = min(samples)
                row['first_observed_current_hp'] = samples[0]
                row['last_observed_current_hp'] = samples[-1]
                row['hp_samples'] = len(samples)
            current = row['max_observed_current_hp']
            row['candidates'] = sorted(catalog[row['template']]['candidates'], key=lambda w:abs(w['hp']-current))
            row['near_max_candidates'] = [w for w in row['candidates'] if current and 0 <= w['hp']-current <= max(2,w['hp']*.005)]
        result = dict(capture=str(path), rows=list(rows.values()))
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/(path.stem+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(capture=path.name, rows=[{k:v for k,v in r.items() if k not in ('candidates',)} for r in rows.values() if r['hp_samples']]),ensure_ascii=False),flush=True)
    finally:
        _V576PacketEventDecoder.feed_events = original
        if source: source.close()

if __name__ == '__main__':
    catalog = client_catalog()
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'client_candidates.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
    for file in sys.argv[1:]: audit(Path(file), catalog)
