"""Audit exact NPC skill identity, without guessing its copied player attack.

262494 is explicitly present in the damage record's own-skill slot. The DNT
display-name key is the same as 6319, not 6309. We can identify the named
summon skill without inventing an EX -> Landing animation transition.
This certificate does NOT claim a Projectile/object link or copied subskill.
"""
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.probe_compact_entity_attack_selectors import walk
from dragon_nest_builder.live_dps import protocol_v576 as p

R=Path('E:/ext0811.rar/Resource00/Resource00/resource')


def main():
    hashes={}
    def table(name):
        path=R/'ext'/name;hashes[name]=sha256(path.read_bytes()).hexdigest()
        return parse_dnt(path)
    skills={r['RowId']:r for r in table('skilltable_character.dnt')}
    monsters={r['RowId']:r for r in table('monstertable_quest.dnt')}
    installed={r['RowId']:r for r in table('monsterskilltable.dnt')}
    levels=[r for r in table('skillleveltable_characterassassin.dnt')if r['_SkillIndex']==262494 and r['_ApplyType']==0]
    template=monsters[33608];skill=skills[262494];family=skills[6319]
    assert skill['_NeedJob']==0 and family['_NeedJob']>0
    assert skill['_NameID']==family['_NameID']
    assert [r['RowId']for r in skills.values()if r['_NeedJob']>0 and r['_NameID']==skill['_NameID']]==[6319]
    pool=installed[template['_SkillTable']]
    assert 262494 in [v for k,v in pool.items()if k.startswith('_SkillIndex')]
    ai=R/'script/monsterai'/template['_CustomAI'].lower()
    hashes[str(ai.relative_to(R))]=sha256(ai.read_bytes()).hexdigest()
    # Lua code keys are ASCII; preserve non-UTF8 comment bytes one-to-one.
    code=re.sub(r'--\[\[.*?\]\]','',ai.read_bytes().decode('latin1'),flags=re.S)
    calls=[line.split('--',1)[0]for line in code.splitlines()
        if re.search(r'\bskill_index\s*=\s*262494\b',line.split('--',1)[0])]
    assert calls and levels
    fixtures=[]
    for name in ('20260822YEP黑硬曜_0_180.json','0831黑硬曜_0_180.json'):
        d=json.loads((ROOT/'outputs/v25_entity_owner_independent'/name).read_text('utf-8'))
        for hit in d['hits']:
            if hit.get('template')!=33608:continue
            decoded=walk(bytes.fromhex(hit['wire']))
            if decoded is None:continue
            phase,body,rows=decoded;u32=lambda m:int.from_bytes(body[m:m+4],'little')
            found=[(actor,m)for actor,m,op,n in rows if op==8 and n==115
                and u32(m+8)==hit['source'] and u32(m+102)==262494
                and int.from_bytes(body[m+19:m+27],'little')>0]
            if not found:continue
            target,m=found[0]
            births=[b for b in d['spawns']if b['entity']==hit['source']and b['frame']<hit['frame']]
            if not births:continue
            birth=max(births,key=lambda b:(b['frame'],b['message']))
            owners=[o for o in d['records']if o['entity']==hit['source']and o['repeated']
                and birth['frame']<=o['frame']<hit['frame']]
            if not owners:continue
            owner=max(owners,key=lambda o:(o['frame'],o['message']))
            for anchor in (birth,owner,hit):
                with Path(d['capture']).open('rb')as f:
                    f.seek(anchor['file_offset']);t,_,original,n=struct.unpack('<dBII',f.read(17))
                    packet=p.decode_captured_frame(t,f.read(n),original)
                assert p.split_v576_payload(packet.payload)[anchor.get('message',0)].hex()==anchor['wire']
            fixtures.append(dict(capture=d['capture'],template=33608,birth=birth,owner=owner,
                hit=dict(hit,phase=phase,marker=m,target=target,object=u32(m+13),result_group=u32(m+4),
                    amount=int.from_bytes(body[m+19:m+27],'little'))))
            break
    assert len(fixtures)==2
    proof=dict(kind='raw exact own-skill ID -> DNT display identity; NOT copied attack/Projectile attribution',
        template=33608,self_skill=262494,display_family=6319,display_name_id=skill['_NameID'],
        monster_skill_row=skill,family_row=family,installed_table=pool,template_row=template,
        level_rows=levels,ai_calls=calls,source_hashes=hashes,
        no_claims=['copied player skill 6309','implicit Landing transition','new object evidence','new cast'])
    proof['certificate']=sha256(json.dumps(proof,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    (out/'shadow_direct_identity_certificate.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'tests/data/shadow_direct_identity_raw.json').write_text(json.dumps(dict(certificate=proof['certificate'],cases=fixtures),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(certificate=proof['certificate'],raw_captures=[x['capture']for x in fixtures]),ensure_ascii=False))


if __name__=='__main__':main()
