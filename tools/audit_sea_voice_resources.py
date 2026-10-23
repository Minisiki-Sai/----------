"""Extract relevant current client rows, without modifying production catalogs."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.build_live_dps_monster_catalog import parse_dnt
root=Path(r'D:\DragonNest\Reborn\Resource_unpacked_all')
def table(pattern):
    result={}
    for folder in sorted(root.glob('Resource[0-9]*'),key=lambda p:int(p.name.removeprefix('Resource'))):
        for path in (folder/'resource/ext').glob(pattern):
            if path.stat().st_size>10:
                for row in parse_dnt(path): result[row['RowId']]=dict(row,_source=str(path))
    return result
monsters={k:v for k,v in table('monstertable*.dnt').items() if 4237000<=k<=4237050 or k in (4235447,4235448,4235453)}
actors=table('actortable.dnt'); files=table('filetable.dnt'); skills=table('monsterskilltable.dnt')
for row in monsters.values():
    actor=actors.get(row.get('_ActorTableID'),{})
    row['actor']=actor
    row['act_file']=files.get(actor.get('_ActName'))
    row['skills']=skills.get(row.get('_SkillTable'))
levels=[v for v in table('skillleveltable_monster*.dnt').values() if 325400<=v.get('_SkillIndex',0)<=325470 or 770000<=v.get('_SkillIndex',0)<=770030 or 50300<=v.get('_SkillIndex',0)<=50330 or 50400<=v.get('_SkillIndex',0)<=50470]
out=Path('outputs/verification/sea_voice_client_rows.json')
out.write_text(json.dumps({'monsters':monsters,'levels':levels},ensure_ascii=False,indent=2),encoding='utf-8')
for k,v in monsters.items(): print(k,v.get('_NameID'),v.get('_CustomAI'),v.get('act_file',{}).get('_FileName'))
print('levels',len(levels))
