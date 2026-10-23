"""Check typed creation lifetimes before considering any expired-hit claim."""
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_attack_instance_catalog import parse_act
from tools.build_dungeon_map_catalog import parse_dnt
R=Path('E:/ext0811.rar/Resource00/Resource00/resource')


def main():
    result=[];files=[];errors=[]
    targets={'kali':{5507:5212,5520:5204},'cleric':{4024:3407,4018:3408,4011:3425,63:3019,47:3401}}
    for family,templates in targets.items():
        rows=list(parse_dnt(R/f'ext/skillleveltable_character{family}.dnt'))
        roots={skill:{row.get('_ProcessParam1') for row in rows if row.get('_SkillIndex')==skill and row.get('_ApplyType')==0}for skill in templates.values()}
        for path in sorted((R/f'char/player/{family}').glob('*.act')):
            files.append(dict(path=str(path),sha256=sha256(path.read_bytes()).hexdigest()))
            try:act=parse_act(path)
            except Exception as exc:
                errors.append(dict(path=str(path),error=str(exc)));continue
            for action in act.actions:
                for signal in action.signals:
                    if signal.signal_type!=15 or len(signal.payload)<84:continue
                    template=int.from_bytes(signal.payload[:4],'little')
                    if template not in templates:continue
                    result.append(dict(template=template,skill=templates[template],act=str(path),action=action.name,
                        dnt_direct_root=action.name in roots[templates[template]],root_names=sorted(x for x in roots[templates[template]]if x),
                        signal_order=signal.order,signal_start=signal.start_frame,
                        declared_ms_at_80=int.from_bytes(signal.payload[80:84],'little'),payload=signal.payload.hex()))
    out=ROOT/'outputs/gap_probe_v36_20260908';out.mkdir(exist_ok=True)
    (out/'expired_lifetime_static.json').write_text(json.dumps(dict(files=files,errors=errors,candidates=result,
        scope='typed creator lifetime diagnostic, no new runtime mapping/lifetime changes'),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in r.items()if k not in ('payload','act','root_names')}for r in result],ensure_ascii=False))


if __name__=='__main__':main()
