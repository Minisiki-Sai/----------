"""Emit a reviewed apply_patch from current Reborn rows; never write catalogs."""
import difflib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_dungeon_map_catalog import find_resource_file, parse_dnt

resource = Path(r'D:\DragonNest\Reborn\Resource_unpacked_all')
rows = {r['RowId']: r for r in parse_dnt(find_resource_file(resource, 'resource/ext/monstertable_nest70.dnt'))}
weights = parse_dnt(find_resource_file(resource, 'resource/ext/monsterweighttable_nest.dnt'))
names = {4237012: '梦魇·地狱犬卡卡里', 4235453: '纵火恶魔福根的亡灵'}
base = ROOT / 'dragon_nest_builder/data/live_dps_profiles'
paths = [base/'monster_templates_v576.json', base/'nest_hp_audit_v650.json']
old = [p.read_text(encoding='utf-8') for p in paths]
if len(old[0].splitlines()) < 3:
    raise SystemExit('Format monster_templates_v576.json with json.tool --indent 2 first; compact it again after applying the patch.')
catalog, audit = map(json.loads, old)
profile = next(p for p in audit['profiles'] if p['label'] == '梦魇海龙')
for tid, name in names.items():
    row = rows[tid]
    selected = [w for w in weights if w['_MonsterTableIndex'] == tid and w['_Difficulty'] == 3]
    maxima = {int(row['_HP'] * w['_HPWeight']) for w in selected}
    assert len(maxima) == 1, (tid, selected)
    hp = maxima.pop()
    assert hp == (700000000 if tid == 4237012 else 80000000)
    value = dict(name=name, level=row['_Level'], hp=hp, base_hp=row['_HP'],
        hp_difficulty_multiplier=selected[0]['_HPWeight'],
        physical_defense=row['_DefP'], magic_defense=row['_DefM'],
        critical_resistance=row['_CriticalResistance'], actor_table_id=row['_ActorTableID'],
        boss=bool(row['_BossKillCheck']), source='Reborn Resource14 / monstertable_nest70.dnt',
        freeze_coefficient=round(row['_Freeze_Increase_Dmg_Ratio'], 4))
    for key, col in [('fire','_Fire_Def'),('ice','_Ice_Def'),('light','_Light_Def'),('dark','_Dark_Def')]:
        value[key+'_resistance'] = round(row[col]*100, 4)
    catalog['templates'][str(tid)] = value
    profile['entities'][str(tid)] = dict(name=name, full_hp=hp, source_table='monstertable_nest70.dnt',
        weight_rows=[dict(table='monsterweighttable_nest.dnt', row=w['RowId'], difficulty=3,
                         multiplier=w['_HPWeight'], hp=hp) for w in selected])
new = [json.dumps(catalog, ensure_ascii=False, indent=2) + '\n',
       json.dumps(audit, ensure_ascii=False, indent=2) + '\n']
print('*** Begin Patch')
for path, before, after in zip(paths, old, new):
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), n=3))
    if not diff: continue
    print('*** Update File: ' + str(path))
    for line in diff[2:]:
        print('@@' if line.startswith('@@') else line)
print('*** End Patch')
