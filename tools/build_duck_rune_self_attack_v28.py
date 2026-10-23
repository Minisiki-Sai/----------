"""Independent installed-AI/rune/process/ACT proof for explicit duck skill IDs."""
from hashlib import sha256
from pathlib import Path
import json
import re
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
from tools.probe_compact_entity_attack_selectors import walk
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.entity_owner_evidence import decode_entity_owner_records

R=Path('E:/ext0811.rar/Resource00/Resource00/resource')


def build():
    hashes={}
    def table(name):
        path=R/'ext'/name;hashes[path.relative_to(R).as_posix()]=sha256(path.read_bytes()).hexdigest()
        return parse_dnt(path)
    monster=next(r for r in table('monstertable_quest.dnt')if r['RowId']==32005)
    installed=next(r for r in table('monsterskilltable.dnt')if r['RowId']==monster['_SkillTable'])
    pool={v for k,v in installed.items()if k.startswith('_SkillIndex')and v>0}
    skills={r['RowId']:r for r in table('skilltable_rune.dnt')}
    player=next(r for r in table('skilltable_character.dnt')if r['RowId']==4217)
    assert player['_NeedJob']>0
    edges=[r for r in table('runeresonatetable.dnt')if r.get('_baseSkill1')==4217 and r.get('_changeSkill1')==293119]
    assert edges
    levels=table('skillleveltable_rune.dnt')
    actor=next(r for r in table('actortable.dnt')if r['RowId']==monster['_ActorTableID'])
    file=next(r for r in table('filetable.dnt')if r['RowId']==actor['_ActName'])
    suffix=file['_FileName'].replace('\\','/').lower()
    paths=[path for path in R.rglob(suffix.rsplit('/',1)[-1])if path.as_posix().lower().endswith('/'+suffix)]
    assert len(paths)==1
    path=paths[0]
    parsed=parse_full(path);hashes[path.relative_to(R).as_posix()]=sha256(path.read_bytes()).hexdigest()
    actions={a['name']:a for a in parsed['actions']}
    ai_path=R/'script/monsterai'/monster['_CustomAI'].lower()
    hashes[ai_path.relative_to(R).as_posix()]=sha256(ai_path.read_bytes()).hexdigest()
    ai=re.sub(r'--\[\[.*?\]\]','',ai_path.read_bytes().decode('latin1'),flags=re.S)
    lines=[line.split('--',1)[0]for line in ai.splitlines()]
    proofs=[];rejected=[]
    for sid in range(261101,261118):
        assert sid in pool and skills[sid]['_NeedJob']==0
        matches=[line for line in lines if re.search(rf'\bskill_index\s*=\s*{sid}\b',line)]
        assert len(matches)==1
        control=re.search(r'checkpassiveruneskill\s*=\s*"(\d+),(\d+),(\d+)"',matches[0])
        assert control and tuple(map(int,control.groups()))==(4217,293119,sid-261100)
        normal=[r for r in levels if r['_SkillIndex']==sid and r['_ApplyType']==0]
        assert len(normal)==1
        names=tuple(normal[0][f'_ProcessParam{i}']for i in (1,2,3))
        if any(not n for n in names):
            rejected.append(dict(monster_skill=sid,reason='missing explicit phase, not filled from neighbours',level=normal[0]))
            continue
        assert names==('Attack_Shock_Start_Mutant','Attack_Shock_Mutant_Loop','Attack_Shock_Mutant_End')
        selected=[actions[n]for n in names]
        signals=[dict(action=a['name'],offset=s['offset'],type=s['type'],u32=s['u32'],strings=s['strings'])
            for a in selected for s in a['signals']if s['type']in(4,15,48,126)]
        assert any(s['type']==4 for s in signals)
        assert not any(s['type']in(15,48,126)for s in signals)
        proofs.append(dict(template=32005,monster_skill=sid,player_skill=4217,mode='pve',
            installed_table_id=installed['RowId'],ai_line=matches[0],rune_edges=edges,level=normal[0],
            actions=names,signals=signals,skill_row=skills[sid]))
    cert=dict(kind='explicit own-skill -> installed AI passive-rune condition -> DNT rune base -> exact ACT phases',
        hashes=hashes,monster=monster,actor=actor,file=file,player=player,proofs=proofs,rejected=rejected)
    cert['sha256']=sha256(json.dumps(cert,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return cert


def raw_examples(capture):
    births={};owners={};examples=[];seen=set();frame=0
    with capture.open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        while head:=f.read(17):
            frame+=1;offset=f.tell()-17;t,_,original,n=struct.unpack('<dBII',head)
            packet=p.decode_captured_frame(t,f.read(n),original)
            if packet is None or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                a=dict(time=t,frame=frame,file_offset=offset,message=mi,wire=wire.hex())
                born=p.decode_v576_monster_spawn(wire,None)
                if born:
                    births[born.actor_id]=dict(a,template=born.template_id);owners.pop(born.actor_id,None)
                for owner in decode_entity_owner_records(wire):owners[owner.entity_id]=dict(a,owner=owner.owner_id)
                if not p._v576_wire_may_contain_marker(wire,(b'\x08\x73\0',)):continue
                decoded=walk(wire)
                if decoded is None:continue
                phase,body,records=decoded;u32=lambda at:int.from_bytes(body[at:at+4],'little')
                for target,m,op,size in records:
                    if op!=8 or size!=115 or u32(m+102)!=261108:continue
                    entity=u32(m+8);birth=births.get(entity);owner=owners.get(entity)
                    if not birth or birth['template']!=32005 or not owner or entity in seen:continue
                    assert birth['frame']<=owner['frame']<=frame
                    examples.append(dict(capture=str(capture),template=32005,birth=birth,owner=owner,
                        hit=dict(a,source=entity,target=target,marker=m,phase=phase,object=u32(m+13),
                            result_group=u32(m+4),amount=int.from_bytes(body[m+19:m+27],'little'))))
                    seen.add(entity)
                    if len(examples)==3:return examples
    return examples


def main():
    proof=build()
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    (out/'duck_rune_certificate.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    cases=raw_examples(Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/0808星期天台风.dpscap'))
    assert len(cases)==3
    (ROOT/'tests/data/duck_rune_self_skill_raw.json').write_text(json.dumps(dict(certificate=proof['sha256'],cases=cases),ensure_ascii=False,indent=2),encoding='utf-8')
    print(proof['sha256'],len(proof['proofs']),'static skills',len(cases),'raw lives')


if __name__=='__main__':main()
