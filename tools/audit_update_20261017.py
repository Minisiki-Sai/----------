"""Read-only client evidence; emit a compact, reproducible update inventory."""
from pathlib import Path
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_dungeon_map_catalog import parse_dnt, load_uistring
ROOT = Path(__file__).resolve().parents[1]
CLIENT = Path(r'D:\DragonNest\Newborn\Resource_unpacked_all_20260916_update')
UI = load_uistring(CLIENT/'Resource15/resource/uistring/uistring.xml')
def table(name):
    paths = list(CLIENT.glob(f'Resource*/resource/ext/{name}.dnt'))
    assert len(paths) == 1, paths
    return parse_dnt(paths[0])
def useful(row):
    return {k:v for k,v in row.items() if v not in (0,-1,'','0','-1',0.0)}
names = ['灵魂之力','刀锋之力','致命一击掌握','魔法爆发','体能暴涨','机械时刻','摄魂怪置换','生理感觉','心理感觉','魔法攻击掌握','物理攻击掌握','时间掌控者','力量掌握','连击','践踏EX','屠戮盛宴EX','旋转冲击EX','飞行冲击EX']
skills = table('skilltable_character')
selected = [r for r in skills if any(UI.get(r.get('_SkillNameID'), '').replace(' ', '').startswith(n) for n in names)]
print('skill name fields', list(skills[0])[:25])
if not selected:
    selected = [r for r in skills if any(UI.get(r.get('_NameID'), '').replace(' ', '').startswith(n) for n in names)]
result = {'skills': [{**useful(r), 'name': UI.get(r.get('_SkillNameID'), UI.get(r.get('_NameID'), ''))} for r in selected],
          'resonance': table('resonancetable')}
items = table('itemtable_cash')
rings = [r for r in items if '梦魇主宰的戒指' in UI.get(r.get('_NameID'), '')]
result['rings'] = [useful(r) for r in rings]
enchants = table('enchanttable')
print('enchant fields', list(enchants[0]))
result['ring_enchants'] = [useful(r) for r in enchants if r.get('_EnchantID') in {v['_EnchantID'] for v in rings}]
result['ui_neutral'] = {str(k):v for k,v in UI.items() if '无属性攻击' in v and len(v)<2200}
result['ui_changes'] = {str(k):v for k,v in UI.items() if ('怒气' in v and ('8' in v or '500' in v)) or ('连击' in v and '0.3' in v)}
out = ROOT/'work/update_20261017'
out.mkdir(parents=True, exist_ok=True)
(out/'client-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('skills',[(r['RowId'],r['name']) for r in result['skills']])
print('rings',result['rings'])
print('ring enchant count',len(result['ring_enchants']))
print('resonance',[(r['_EquipLevel'],r['_EquipQuality'],r['_NeedStrength'],[(r[f'_State{i}'],r[f'_State{i}Value']) for i in range(1,11) if r[f'_State{i}']>=0]) for r in result['resonance'] if r['_EquipLevel']>=60])
