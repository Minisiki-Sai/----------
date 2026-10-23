"""Freeze the two typed ACT lifetimes and representative original frames."""
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_attack_instance_catalog import parse_act
from tools.build_dungeon_map_catalog import parse_dnt, load_uistring


def main():
    res = Path('E:/ext0811.rar/Resource00/Resource00/resource')
    out = ROOT / 'outputs/umbra_lifetime_v31_20260908'
    source = [(5717,6401,'assassin','char/player/assassin/assassin_skill_2st_raven.act',
               'Skill_Umbra','0807_100s.json'),
              (5508,5218,'kali','char/player/kali/kali_skill_1st_scremer.act',
               'Skill_GrudgeFormation','0817_grudge_1800s.json')]
    certificate, cases = [], []
    for template, skill, family, actfile, action, evidence in source:
        table = res / f'ext/skillleveltable_character{family}.dnt'
        rows = [r for r in parse_dnt(table) if r['_SkillIndex'] == skill and r['_ApplyType'] == 0]
        assert rows and all(r['_ProcessParam1'] == action for r in rows)
        matched = [a for a in parse_act(res / actfile).actions if a.name == action]
        assert len(matched) == 1
        signals = [s for s in matched[0].signals if s.signal_type == 15
                   and int.from_bytes(s.payload[:4], 'little') == template]
        assert len(signals) == 1 and len(signals[0].payload) == 256
        s = signals[0]
        assert int.from_bytes(s.payload[80:84], 'little') == 10000
        raw = json.loads((out/evidence).read_text('utf-8'))
        assert sha256(Path(raw['capture']).read_bytes()).hexdigest() == raw['capture_sha256']
        life = next(l for l in raw['lives'] if l['registration']['projectile_template_id'] == template
                    and not l['later_registrations'] and 'invalidated_by' not in l
                    and any(8 < h['age'] < 10 for h in l['hits']))
        certificate.append(dict(template=template, skill=skill, seconds=10, act=actfile,
            action=action, signal_type=15, signal_order=s.order, signal_start=s.start_frame,
            payload=s.payload.hex(), byte_offset=80,
            skill_level_row_ids=[r['RowId'] for r in rows],
            hashes={str(p.relative_to(res)):sha256(p.read_bytes()).hexdigest()
                    for p in (table,res/actfile)},
            raw_source=evidence, raw_source_sha256=sha256((out/evidence).read_bytes()).hexdigest()))
        cases.append(dict(capture=raw['capture'], capture_sha256=raw['capture_sha256'],
                          template=template, skill=skill, life=life))
    uis = res / 'uistring/uistring0818.xml'
    text = load_uistring(uis)[1000064321]
    assert '持续时间10秒' in text
    result=dict(policy='typed10s evidence for post-admission-only lookup of named real Hits at age8-10; primary admission stays8s; no lifecycle or Hit extension',
                certificate=certificate, umbra_text=text,
                uistring_sha256=sha256(uis.read_bytes()).hexdigest(), cases=cases)
    encoded=json.dumps(result,ensure_ascii=False,indent=2)
    (ROOT/'tests/data/field_object_lifetime_v31.json').write_text(encoded,encoding='utf-8')
    (out/'certificate.json').write_text(encoded,encoding='utf-8')
    print(json.dumps([dict(template=c['template'], raw_hit_ages=[round(h['age'],3)
                          for h in c['life']['hits']]) for c in cases],ensure_ascii=False))


if __name__ == '__main__':main()
