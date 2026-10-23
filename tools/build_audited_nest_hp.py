"""Build template-scoped HP references; never derive maximum HP from damage."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_dungeon_map_catalog import find_resource_file, parse_dnt
LATEST = Path(r'D:\DragonNest\Reborn\Resource_unpacked_all')
latest_monsters = {r['RowId']:r for file in ('monstertable.dnt','monstertable_nest.dnt','monstertable_nest70.dnt')
                   for r in parse_dnt(find_resource_file(LATEST,'resource/ext/'+file))}
latest_weights = parse_dnt(find_resource_file(LATEST,'resource/ext/monsterweighttable_nest.dnt'))
catalog = json.loads((ROOT/'outputs/verification/nest_hp_audit/client_candidates.json').read_text(encoding='utf-8'))
# Weight row selections were compared against independent UDP current HP, not
# decoder target_max_hp (which used the old, circular name-based reference).
specs = [
 ('黑龙硬核', ['黑龙硬核','黑硬','硬核bluemoon'], 3, [4180002,4180003,4180004,4180005,4181001,4181005,4181006,4181007,4181008,4181009,4182001,4182002,4182003,4182004,4182005,4182006,4182007,4182008], True),
 ('沙龙硬核', ['沙龙硬核','沙硬'], 3, [4150050,4150051,4150054,4150055,4150056,4150057,4150058,4150059,4150064,4160001], True),
 ('黑龙经典', ['黑龙经典','黑典','黑经'], 0, [503611,503621,503639,503652,503661,503681,503682,503683,503696], True),
 ('黑龙123', ['黑龙1','黑龙2','黑龙3','黑龙123'], 2, [503611,503621,503639,503652,503661,503681,503682,503683,503696], True),
 ('沙龙经典', ['沙龙经典','沙典','沙经'], 0, [503001,503002,503015,503021,503031,503041,503051,503054,503055,503061,503062,503063,503064], True),
 ('沙龙123', ['沙龙1','沙龙2','沙龙3','沙龙123'], 2, [503001,503002,503015,503021,503031,503041,503051,503054,503055,503061,503062,503063,503064], False),
 ('守卫者巢穴', ['守卫'], 1, [503101,503121,503131,503141,503151], True),
 ('试炼巢穴', ['试炼'], 4, [503510,503511,503512,503513,503521,503531,503532,503551,503561], True),
 ('迷雾巢穴', ['迷雾'], 4, [503500,503501,503502,503496], True),
 ('绿龙经典', ['绿龙经典','绿典'], 0, [236028,236001,236010,236040,236015,236018,236048], True),
 ('绿龙硬核', ['绿龙硬核','绿硬'], 3, [237051,237056,237057], True),
 ('梦魇海龙', ['梦魇海龙'], 3, [4235447,4235448,4235453,4237012,4237003,4237011,4237023,4237034,4237035], False),
]
profiles = []
for label, hints, difficulty, ids, verified in specs:
    entities = {}
    for tid in ids:
        row = catalog.get(str(tid))
        if row is None and tid in (4237012,4235453):
            row = dict(name={4237012:'梦魇·地狱犬卡卡里',4235453:'纵火恶魔福根的亡灵'}[tid],
                       table='monstertable_nest70.dnt',candidates=[])
        if row is None:
            raise KeyError(tid)
        weights = [w for w in row['candidates'] if w['difficulty'] == difficulty]
        if label == '梦魇海龙':
            weights = [dict(table='monsterweighttable_nest.dnt',row=w['RowId'],difficulty=w['_Difficulty'],
                       multiplier=w['_HPWeight'],hp=int(latest_monsters[tid]['_HP']*w['_HPWeight']))
                       for w in latest_weights if w['_MonsterTableIndex']==tid and w['_Difficulty']==difficulty]
        if tid in (4237034,4237035):
            # Script-created phase-2 replacements inherit the sea-dragon scale.
            weights = [dict(hp=7120000000, source='nightmare_sea_dragon.PHASES', inherited=True)]
        values = {w['hp'] for w in weights}
        if len(values) != 1:
            raise ValueError((label, tid, weights))
        entities[str(tid)] = dict(name=row['name'], full_hp=values.pop(),
            source_table=row['table'], weight_rows=weights)
    profiles.append(dict(label=label,hints=hints,capture_checked=verified,entities=entities,
        source=str(LATEST) if label=='梦魇海龙' else 'Resource_unpacked_all_20260916_update'))
output = ROOT/'dragon_nest_builder/data/live_dps_profiles/nest_hp_audit_v650.json'
output.write_text(json.dumps(dict(source='Resource_unpacked_all_20260916_update',
    note='capture_checked validates weight scale, not every template or phase damage budget',
    profiles=profiles),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(output)
