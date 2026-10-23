"""Postprocess saved bytes only. No capture reads and no runtime edits."""
from __future__ import annotations
import bisect
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from tools.audit_remaining_registration_3021_context import OUT, sha, hashes, u32
from tools.audit_remaining_registration_packet_decoder import _fields
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
from tools.build_dungeon_map_catalog import parse_dnt
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body_uncached


def parent_tail(decoded, marker):
    """Diagnostic only: 105-byte server09 record -> 102-byte known parent tail.

    Does NOT create/attribute an object. Does NOT accept branch0 or resourceFFFF.
    Caller must validate the phase and actual owner/object generation separately.
    """
    if marker < 13 or marker+101 > len(decoded):
        return None
    if decoded[marker:marker+3] not in (b'\x09\x62\0', b'\x09\x66\0'):
        return None
    tail = decoded[marker-4:marker] + decoded[marker+3:marker+101]
    row = _fields(tail, 0, 'parent', None)
    if not row or not 0 < row['effect_resource_id'] < 0xffff:
        return None
    return row


def write(name, data):
    (OUT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def static_resources():
    base = Path(r'E:\ext0811.rar\Resource00\Resource00\resource')
    files = {r['RowId']:r['_FileName'] for r in parse_dnt(base/'ext/filetable.dnt')}
    weapons = {r['RowId']:r for r in parse_dnt(base/'ext/weapontable_projectile.dnt')}
    paths = [base/'ext/filetable.dnt', base/'ext/weapontable_projectile.dnt',
             base/'ext/skilltable_character.dnt']
    skill_rows = {r['RowId']:r for r in parse_dnt(paths[-1])}
    all_signals, roots, level_rows = {}, {}, {}
    configurations = [
        ('soceress', 'char/player/soceress/soceress_skill_2st_elestra.act',2353,'Skill_IcicleInjection'),
        ('soceress', 'char/player/soceress/soceress_skill_1st_elementallord.act',2207,'Skill_FireBird'),
        ('archer', 'char/player/archer/archer_skill_2st_artillery.act',1352,'Skill_ExtensionArrow_EX'),
        ('archer', 'char/player/archer/archer_skill_2st_artillery.act',1353,'Skill_DetonationgArrow'),
        ('assassin', 'char/player/assassin/assassin_skill_1st_chaser.act',6204,'Skill_Raindrop'),
        ('assassin', 'char/player/assassin/assassin_skill_1st_chaser.act',6212,'Skill_CripplePunisher')]
    for job, relative, skill, action in configurations:
        path = base/relative; paths.append(path)
        parsed = parse_full(path)
        matching = [a for a in parsed['actions'] if a['name']==action]
        assert len(matching)==1
        a = matching[0]
        roots[str(skill)] = dict(file=relative, file_sha256=sha(path), action=action,
            signal15=[{'offset':s['offset'],'payload_offset':s['offset']+16,
                       'template':s['u32'][0],'start':s['start'],'end':s['end']}
                      for s in a['signals'] if s['type']==15])
        lp=base/f'ext/skillleveltable_character{job}.dnt';paths.append(lp)
        levels=[r for r in parse_dnt(lp) if r['_SkillIndex']==skill]
        level_rows[str(skill)] = [{k:v for k,v in r.items() if k in
            ('RowId','_SkillIndex','_SkillLevel','_ApplyType','_ProcessParam1','_ProcessParam2','_ProcessParam3')}
            for r in levels]
    for tpl in (3021,3023,3001,2022,2023,2025,5708,5709,5737,5732):
        path=base/'weapon'/files[weapons[tpl]['_ActName']]
        if not path.exists():
            all_signals[str(tpl)] = dict(weapon_row=weapons[tpl], act_file=str(path),
                missing_resource=True, note='DNT filename reference only; no ACT contents available; no typed edge inferred.')
            continue
        paths.append(path)
        parsed=parse_full(path)
        all_signals[str(tpl)] = dict(weapon_row=weapons[tpl],act_file=str(path),act_sha256=sha(path),
            signals=[dict(action=a['name'],**s) for a in parsed['actions'] for s in a['signals'] if s['type'] in (4,15)],
            numeric_2207_occurrences=[dict(action=a['name'],signal_offset=s['offset'],signal_type=s['type'],u32_index=i)
                for a in parsed['actions'] for s in a['signals'] for i,v in enumerate(s['u32']) if v==2207])
    return dict(resource_root=str(base),source_hashes={str(p):sha(p) for p in set(paths)},
        skill_rows={str(k):v for k,v in skill_rows.items() if k in (2207,2353,1352,1353,6204,6212)},
        skill_level_action_keys=level_rows,root_actions=roots,projectile_acts=all_signals)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    before=hashes()
    ck=json.loads((OUT/'scan_checkpoint.json').read_text(encoding='utf-8'))
    scan=json.loads((OUT/'scan.json').read_text(encoding='utf-8'))
    oldpath=ROOT/'outputs/audit_remaining_registration_exact_parent_20260907_v3/0905黑龙经典冰灵.json'
    old=json.loads(oldpath.read_text(encoding='utf-8'))
    gs=ck['generations'];byobj=defaultdict(list)
    for g in gs:byobj[g['obj']].append(g)
    fixtures=json.loads((OUT/'fixtures.json').read_text(encoding='utf-8'))
    rows=[];negative=[];raw_same_phase_checks=0
    for h in scan['disputed_hits']:
        g=gs[h['fresh_generation_id']];f=ck['packets'][g['fixture']];b=bytes.fromhex(f['decoded_hex']);m=g['m']
        if g['kind']=='server09':
            accepted=[]
            for phase in range(13):
                r=parent_tail(_decode_v576_body_uncached(bytes.fromhex(f['payload_hex']),phase),m)
                if r:accepted.append((phase,r))
            assert len(accepted)==1 and accepted[0][0]==f['phase']
            raw_same_phase_checks+=1;r=accepted[0][1]
            # Prefix-only corruption must not keep a creation proof.
            for label,at,value in [('owner_repeat',m+44,0),('zero_guard',m+3,1),
                                   ('branch_low',m+53,2),('eight_ff',m+57,0),
                                   ('zero_parent',m+49,0),('resource_ffff',m+69,0xffff)]:
                bad=bytearray(b)
                if label in ('zero_parent','resource_ffff'):bad[at:at+4]=value.to_bytes(4,'little')
                else:bad[at]=value
                assert parent_tail(bytes(bad),m) is None,label
                if len(negative)<6:
                    negative.append(dict(name=label,source_fixture=g['fixture'],marker=m,
                        synthetic_mutation=True,decoded_hex=bytes(bad).hex(),expected_accept=False))
        else:
            r=_fields(b[m+1:],0,'parent',None)
            assert r and 0<r['effect_resource_id']<0xffff
        assert r['source_actor_id']==1337770664 and r['damage_object_id']==g['obj'] and r['projectile_template_id']==3021
        prior=[p for p in byobj[r['parent_object_id_candidate']] if p['order']<g['order']]
        parent=prior[-1] if prior else None
        current=bool(parent and parent['template']==3023
            and (parent['superseded_at'] is None or h['hit_order']<parent['superseded_at'])
            and (g['superseded_at'] is None or h['hit_order']<g['superseded_at'])
            and 0<=g['t']-parent['t']<=8 and 0<=h['t']-g['t']<=8)
        epochs=[a for a in scan['casts'] if parent and a['skill_id']==2353 and 0<=parent['t']-a['t']<=2.5]
        row=dict(cached_logical_hit=h['p'],physical=h['physical'],damage=h['damage'],fixed_raw_skill_id=2207,
            child_obj=g['obj'],child_generation=g['generation'],child_fixture=g['fixture'],hit_fixture=h['fixture'],
            evidence=r,parent_fixture=parent['fixture'] if parent else None,
            parent_generation=parent['generation'] if parent else None,
            parent_age_seconds=round(g['t']-parent['t'],6) if parent else None,
            full_current_parent=current,root_action_epochs=[a['epoch'] for a in epochs],
            qualified=current and len(epochs)==1,server_tail=g['kind']=='server09')
        rows.append(row)
        if parent:fixtures['records'][parent['fixture']]=ck['packets'][parent['fixture']]
        for a in epochs:fixtures['records'][a['fixture']]=ck['packets'][a['fixture']]
    assert all(r['qualified'] for r in rows)
    # Review the stated136 client->server hypothesis using the original whole
    # capture generation cache, retaining full owner in every object key.
    bykey=defaultdict(list)
    for g in old['generations']:bykey[(g['owner'],g['obj'])].append(g)
    state_summary={};state_examples=[]
    for tpl in (3021,3023):
        creations=[g for g in old['generations'] if g['template']==tpl and g['kind']!='server09']
        counts=Counter(client_records=len(creations))
        for g in creations:
            counts['resource_ffff_rejected']+=g['resource']==0xffff
            for s in bykey[(g['owner'],g['obj'])]:
                if s['id']==g['id']:continue
                age=s['t']-g['t']
                if 0<=age<=8:
                    counts['next_registration_within8']+=1
                    counts['same_template_server_within8']+=s['kind']=='server09' and s['template']==tpl
                    state_examples.append(dict(creation=g['p'],state=s['p'],age=age,owner=g['owner'],obj=g['obj']))
            counts['merged_replicas']+=len(g['replicas'])
        state_summary[str(tpl)]=dict(counts)
    general=[]
    for name,tpl in [('影识别错误',5709),('0808星期天黑典翻车',2023),('0825经典圣徒',2023)]:
        path=ROOT/f'outputs/audit_remaining_registration_server_20260907_v2/{name}.json'
        d=json.loads(path.read_text(encoding='utf-8'))
        all_rows=[r for r in d['strict_instances'] if r['template']==tpl]
        selected=[r for r in all_rows if r['fixed_slot_anchors']]
        examples=[];stats=Counter(registrations=len(all_rows),anchor_registrations=len(selected))
        for r in selected:
            # These are retained decoded contexts, NOT newly decrypted frames.
            b=bytes.fromhex(r['context_hex']);m=r['m']-r['context_start']
            assert m>=4 and m+101<=len(b)
            owner=u32(b,m-4);obj=u32(b,m+8);template=u32(b,m+12)
            assert (owner,obj,template)==(r['owner'],r['obj'],tpl)
            p=u32(b,m+49);branch=u32(b,m+53);resource=u32(b,m+69)
            stats['anchor_rows']+=sum(r['fixed_slot_anchors'].values())
            stats['resource_ffff_registrations']+=resource==0xffff
            stats['branch0_registrations']+=(branch&0xffff)==0
            examples.append(dict(logical_packet=r['p'],owner=owner,obj=obj,template=tpl,
                marker=r['marker'],candidate_full_parent_id=p,branch=branch,resource=resource,
                strict_branch1_nonffff_accept=parent_tail(b,m) is not None,
                context_start=r['context_start'],context_hex=r['context_hex'],anchor_examples=r['anchor_examples']))
        general.append(dict(capture=d.get('capture'),capture_sha256=d.get('capture_sha256'),
            source_report=str(path),source_report_sha256=sha(path),template=tpl,
            statistics=dict(stats),examples=examples,
            verdict=('Structurally analogous parent context but all retained6212 conflicts haveFFFF resource; strict subset is not demonstrated.' if tpl==5709 else
                     'Static2022->2023 differs from1353->2025. Retained conflict contexts havebranch0, not auditedbranch1. Same semantic risk, no unconditional generalization or new parent decoder permission.')))
    static=static_resources()
    summary=dict(qualified_hits=len(rows),qualified_damage=sum(r['damage'] for r in rows),
        client_parent_hits=sum(not r['server_tail'] for r in rows),
        server_parent_hits=sum(r['server_tail'] for r in rows),
        distinct_parent_generations=len({(r['evidence']['parent_object_id_candidate'],r['parent_generation']) for r in rows}),
        distinct_root_action_epochs=len({r['root_action_epochs'][0] for r in rows}),
        server_unique_phase_checks=raw_same_phase_checks,negative_guard_checks=raw_same_phase_checks*6,
        runtime_unchanged=hashes()==before)
    final=dict(capture_sha256=scan['capture_sha256'],summary=summary,
        supersedes='scan.json counts of2 exposed parents:65 additional rawserver tails carry fullparentID. scan.json is retained as initial production-decoder baseline, not final raw evidence.',
        layout={'server_marker':'09 62 00 / 09 66 00','record_bytes':105,
                'owner':'m-4, repeatedm+44','five_zero':'m+3..m+7','object':'m+8 u32LE','template':'m+12 u32LE',
                'parent_zero':'m+48=0','full_parent':'m+49 u32LE','branch_sequence':'m+53 u32LE, low16=1',
                'eight_ff':'m+57..m+64','resource':'m+69 u32LE,0<r<FFFF',
                'normalization':'decoded[m-4:m]+decoded[m+3:m+101] yields102bytes identical field positions to112packet owner-tail'},
        disputed_hits=rows,state_observation_review=dict(source=str(oldpath),source_sha256=sha(oldpath),
            counts=state_summary,examples=state_examples,
            verdict='No raw136client->later sameowner/object registration within8s observed. Do not call state refresh the proven0905 cause. Creation-vs-observation split is a reasonable invariant, but needs a concrete positive instance; preserve expiry, revoke dependencies and mismatch invalidation.'),
        generalization=general,runtime_before=before,runtime_after=hashes())
    fixtures['final_parent_evidence']=rows
    fixtures['negative_guard_fixtures']=negative
    fixtures['summary']=summary
    write('evidence.json',final)
    write('act_dnt_evidence.json',static)
    write('fixtures_final.json',fixtures)
    write('validation.json',dict(summary=summary,all67_parent_current_epoch=True,
        original_capture_not_reopened=True,static_chains={k:v['signal15'] for k,v in static['root_actions'].items()},
        source_hashes={'scan_checkpoint.json':sha(OUT/'scan_checkpoint.json'),'fixtures_final.json':sha(OUT/'fixtures_final.json'),
                       'act_dnt_evidence.json':sha(OUT/'act_dnt_evidence.json')}))
    print(json.dumps(dict(summary=summary,state_observation_review=state_summary,
                         generalization=[{'template':r['template'],**r['statistics']} for r in general]),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
