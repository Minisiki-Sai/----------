"""Build exact-ID/root-action proofs from the bounded raw extraction."""
from __future__ import annotations
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from tools.build_dungeon_map_catalog import parse_dnt
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
OUT=ROOT/'outputs/audit_remaining_registration_ffff_raw_20260907'


def u32(b,p):return int.from_bytes(b[p:p+4],'little')


def walk(b,start):
    pos=start;rows=[]
    while pos+7<=len(b):
        owner=u32(b,pos);op=b[pos+4];size=int.from_bytes(b[pos+5:pos+7],'little')
        end=pos+7+size
        if owner in (0,0xffffffff) or end>len(b):return None
        rows.append(dict(owner=owner,marker=pos+4,operation=op,size=size,end=end));pos=end
    return rows if pos==len(b) else None


def raw_parent(d,r):
    b=bytes.fromhex(d['packets'][r['fixture']]['plaintext_by_phase'][str(r['phase'])])
    if r['kind']=='server09':
        m=r['marker'];size=int.from_bytes(b[m+1:m+3],'little')
        if size not in (98,102) or m+3+size>len(b):return None
        tail=b[m-4:m]+b[m+3:m+3+size]
        required=1;suffix_at=70
    else:
        tail=b[r['owner_offset']:];required=3 if len(tail)==115 else 1;suffix_at=83 if len(tail)==115 else 70
        if len(tail) not in (102,115):return None
    owner,obj,tpl=u32(tail,0),u32(tail,9),u32(tail,13)
    pid,branch,suffix=u32(tail,50),u32(tail,54),u32(tail,suffix_at)
    if not (owner==u32(tail,45) and tail[4:9]==bytes(5) and tail[49]==0
            and 0<pid<=0xffffff and pid!=obj and branch&0xffff==required
            and tail[58:66]==b'\xff'*8 and 0<suffix<=0xffff):return None
    return dict(owner=owner,obj=obj,template=tpl,parent=pid,branch=branch&0xffff,sequence=branch>>16,
                suffix=suffix,tail_hex=tail.hex(),tail_length=len(tail))


def main():
    reports={};all_pairs=Counter()
    for label,root_skill,parent_tpl,children in [('shadow',6204,5708,{5709,5737}),('soul',5217,11075,{11076}),('ice',2353,3023,{3021})]:
        d=json.loads((OUT/f'{label}.json').read_text(encoding='utf-8'));records=d['registrations'];packets=d['packets']
        selected_objects={r['fields'].get('obj') for r in d['selections']}
        if label=='shadow':selected_objects={r['fields']['obj'] for r in d['selections']}
        def order(r):
            p=packets[r['fixture']];return (p['frame'],p['message'],r.get('marker',r.get('owner_offset',1)-1))
        rows=[]
        for reg in records:
            extra_ice_server = label=='ice' and reg['kind']=='server09' and reg['packet_length']==224 and reg['template']==3021
            if (reg['obj'] not in selected_objects and not extra_ice_server) or reg['template'] not in children:continue
            fields=raw_parent(d,reg)
            if not fields or fields['suffix']!=65535:continue
            before=[r for r in records if r['owner']==fields['owner'] and r['obj']==fields['parent'] and order(r)<order(reg)]
            parent=max(before,key=order) if before else None
            actions=[a for a in d['actions'] if parent and a['source_actor_id']==fields['owner']
                     and a['skill_id']==root_skill and 0<=parent['timestamp']-a['timestamp']<=2.5]
            epochs=[]
            for a in sorted(actions,key=lambda a:a['timestamp']):
                if epochs and a['timestamp']-epochs[-1][0]['timestamp']<=0.25:epochs[-1].append(a)
                else:epochs.append([a])
            hits=[h for h in d['hits'] if h['source_actor_id']==fields['owner'] and h['damage_object_id']==fields['obj']
                  and 0<=h['timestamp']-reg['timestamp']<=8]
            eligible=[]
            for hit in hits:
                ho=(packets[hit['fixture']]['frame'],packets[hit['fixture']]['message'],hit['record_offset'])
                current=[r for r in records if r['owner']==fields['owner'] and r['obj']==fields['obj'] and order(r)<=ho]
                latest=max(current,key=order) if current else None
                parent_current=[r for r in records if r['owner']==fields['owner'] and r['obj']==fields['parent'] and order(r)<=ho]
                lp=max(parent_current,key=order) if parent_current else None
                if latest and lp and latest['template']==reg['template'] and lp['template']==parent_tpl:
                    lf=raw_parent(d,latest)
                    # Never revive A after an intervening unknown/B template.
                    parent_interval=[r for r in records if parent and r['owner']==fields['owner']
                        and r['obj']==fields['parent'] and order(parent)<=order(r)<=ho]
                    child_interval=[r for r in records if r['owner']==fields['owner']
                        and r['obj']==fields['obj'] and order(reg)<=order(r)<=ho]
                    uninterrupted_parent=all(r['template']==parent_tpl for r in parent_interval)
                    uninterrupted_child=all(r['template']==reg['template'] and
                        (raw_parent(d,r) is None or raw_parent(d,r)['parent']==fields['parent']) for r in child_interval)
                    if lf and lf['parent']==fields['parent'] and uninterrupted_parent and uninterrupted_child:eligible.append(hit)
            boundaries=[]
            if reg['kind']=='server09':
                b=bytes.fromhex(packets[reg['fixture']]['plaintext_by_phase'][str(reg['phase'])])
                for start in (14,21,24):
                    parsed=walk(b,start)
                    if parsed and any(r['marker']==reg['marker'] for r in parsed):boundaries.append(dict(start=start,records=parsed))
                assert len(boundaries)==1,(label,reg,boundaries)
                for r in boundaries[0]['records']:all_pairs[(r['operation'],r['size'])]+=1
            qualified=bool(parent and parent['template']==parent_tpl and len(epochs)==1 and 0<=reg['timestamp']-parent['timestamp']<=8)
            rows.append(dict(registration=reg,fields=fields,parent=parent,root_action_epochs=epochs,
                             qualified=qualified,hits=eligible,boundaries=boundaries))
        def identity(h):return (h['source_actor_id'],h['damage_object_id'],h['target_actor_id'],h['damage_result_group_id'],h['damage'],h['critical'])
        rawhits={identity(h):h for r in rows if r['qualified'] for h in r['hits']}
        reports[label]=dict(capture=d['capture'],capture_sha256=d['capture_sha256'],rows=rows,
            summary=dict(registrations=len(rows),qualified_registrations=sum(r['qualified'] for r in rows),
                unique_objects=len({r['fields']['obj'] for r in rows}),
                qualified_unique_hit_identities=len(rawhits),qualified_unique_damage=sum(h['damage'] for h in rawhits.values()),
                root_skills=sorted({a['skill_id'] for r in rows for e in r['root_action_epochs'] for a in e})))
    base=Path(r'E:\ext0811.rar\Resource00\Resource00\resource')
    weapons={r['RowId']:r for r in parse_dnt(base/'ext/weapontable_projectile.dnt')}
    files={r['RowId']:r['_FileName'] for r in parse_dnt(base/'ext/filetable.dnt')}
    skilltable={r['RowId']:r for r in parse_dnt(base/'ext/skilltable_character.dnt')}
    assert skilltable[5406]['_BaseSkillID']==5217
    skillrows=[r for r in parse_dnt(base/'ext/skillleveltable_characterkali.dnt') if r['_SkillIndex'] in (5217,5406)]
    action_keys={r.get('_ProcessParam1') for r in skillrows}
    roots=[]
    for rel in ('char/player/kali/kali_skill.act','char/player/kali/kali_skill_1st_scremer.act','char/player/kali/kali_skill_2st_souleater.act'):
        p=base/rel
        for a in parse_full(p)['actions']:
            if a['name'] in action_keys:
                for s in a['signals']:
                    if s['type']==15 and s['u32'][0]==11075:
                        roots.append(dict(file=rel,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),action=a['name'],signal=s))
    assert roots
    projectile={}
    for tpl in (11075,11076):
        p=base/'weapon'/files[weapons[tpl]['_ActName']]
        projectile[str(tpl)]=dict(weapon_row=weapons[tpl],file=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            signals=[dict(action=a['name'],**s) for a in parse_full(p)['actions'] for s in a['signals'] if s['type'] in (4,15)])
    assert any(s['type']==15 and s['u32'][0]==11076 for s in projectile['11075']['signals'])
    out=dict(reports=reports,observed_server_record_pairs=[dict(operation=k[0],size=k[1],count=v) for k,v in sorted(all_pairs.items())],
             soul_static=dict(root_skill=5217,ex_skill=5406,explicit_dnt_base_skill=skilltable[5406]['_BaseSkillID'],
                skill_table_rows=[skilltable[5217],skilltable[5406]],skill_level_keys=sorted(action_keys),roots=roots,projectiles=projectile),
             caveat='Dedup is raw result-identity grouping for these bounded windows, not UI cast count. Root grouping collapses <250ms relay/action echoes. No scoring or full-source replay.')
    (OUT/'proofs.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({label:r['summary'] for label,r in reports.items()},ensure_ascii=False))
    print(json.dumps(out['observed_server_record_pairs']))


if __name__=='__main__':main()
