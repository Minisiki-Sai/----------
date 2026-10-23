"""Validate only saved packet bytes against the private v22 protocol."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/audit_remaining_registration_server_parent_v22_20260907'
RAW=ROOT/'outputs/audit_remaining_registration_3021_context_20260907'


def load(p):return json.loads(p.read_text(encoding='utf-8'))


def module(version):
    path=ROOT/f'outputs/damage_attribution_candidate_{version}_20260907/protocol_v576.py'
    spec=importlib.util.spec_from_file_location(f'audit_protocol_{version}',path)
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m,path


def main():
    audit=load(OUT/'cached_audit.json');fx=load(RAW/'fixtures_final.json')
    ck=load(RAW/'scan_checkpoint.json')
    new,path=module('v22');old,_=module('v21')
    source_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    def decode(m,wire):return m.decode_v576_server_damage_object_registrations(wire,None,(3021,),include_unmapped=True)
    def exact(wire,oid):return [r for r in decode(new,wire) if r.source_actor_id==1337770664 and r.damage_object_id==oid and r.parent_damage_object_id]
    selected=[h for h in fx['final_parent_evidence'] if h['server_tail']]
    for h in selected:
        r=ck['packets'][h['child_fixture']];wire=bytes.fromhex(r['payload_hex'])
        rows=exact(wire,h['child_obj']);assert len(rows)==1
        decoded=rows[0];expected=h['evidence']
        assert decoded.parent_damage_object_id==expected['parent_object_id_candidate']
        assert decoded.effect_resource_id==expected['effect_resource_id']
        assert decoded.child_sequence==expected['child_sequence']
        assert decoded.registration_key==expected['normalized_tail_hex']
        assert decoded.xor_phase==r['phase']
        for direction in ('client_to_server','server_to_client',None):
            assert decoded in new.decode_v576_server_damage_object_registrations(wire,direction,(3021,),include_unmapped=True)
    # Every cached packet, not another raw scan. Assert no prefix identity is lost.
    counts=Counter();opaque_examples=[]
    for key,f in ck['packets'].items():
        wire=bytes.fromhex(f['payload_hex'])
        a=decode(old,wire);b=decode(new,wire)
        assert len(a)==len(b),(key,'row_count')
        for before,after in zip(a,b):
            da,db=asdict(before),asdict(after)
            if after.parent_damage_object_id:
                counts['upgraded_rows']+=1
                for name in ('parent_damage_object_id','effect_resource_id','child_sequence','registration_key','layout'):
                    db[name]=da[name]
            assert db==da,(key,da,db)
        counts['cached_packets']+=1
    # Three different parent generations, including mixed 1E+09 envelope.
    chosen=[audit['cases'][0],audit['cases'][1],next(c for c in audit['cases'] if c['expected']['packet_length']==152)]
    records={};cases=[];negatives=[];guard_count=0
    for c in chosen:
        row=c['expected'];key=row['fixture'];f=ck['packets'][key]
        b=bytes.fromhex(f['decoded_hex']);m=row['marker_offset'];oid=row['object_id'];phase=f['phase']
        mutations=[('five_zero',m+3,b'\1'),('repeat_owner',m+44,bytes(4)),
                   ('parent_zero_guard',m+48,b'\1'),('parent_zero',m+49,bytes(4)),
                   ('parent_self',m+49,oid.to_bytes(4,'little')),
                   ('parent_out_of_range',m+49,(0x1000000).to_bytes(4,'little')),
                   ('branch0',m+53,b'\0'),('branch3',m+53,b'\3'),
                   ('eight_ff',m+57,b'\0'),('suffix_zero',m+69,bytes(4)),
                   ('suffix_ffff',m+69,(0xffff).to_bytes(4,'little')),
                   ('marker0966',m+1,b'\x66')]
        if len(b)==152:mutations.append(('mixed_prefix_not_1e1a',18,b'\x1f'))
        else:mutations.append(('sibling_boundary_marker',123 if m==18 else 18,b'\x08'))
        for label,at,value in mutations:
            bad=bytearray(b);bad[at:at+len(value)]=value
            wire=new._decode_v576_body_uncached(bytes(bad),phase)
            assert not exact(wire,oid),(key,label)
            negatives.append(dict(source=key,label=label,synthetic_mutation=True,
                                  payload_hex=wire.hex(),source_actor_id=row['owner'],damage_object_id=oid,
                                  expected_no_full_parent=True))
            guard_count+=1
        for label,bad in [('truncated',b[:-1]),('extended',b+b'\0'),('shifted',b[:14]+b'\0'+b[14:])]:
            wire=new._decode_v576_body_uncached(bad,phase)
            assert not exact(wire,oid),(key,label)
            negatives.append(dict(source=key,label=label,synthetic_mutation=True,payload_hex=wire.hex(),
                                  source_actor_id=row['owner'],damage_object_id=oid,expected_no_full_parent=True))
            guard_count+=1
        # Opaque coordinate/tail changes do not change the decoded parent ID.
        for at in (m+16,m+35,m+75,m+86):
            bad=bytearray(b);bad[at]^=1
            got=exact(new._decode_v576_body_uncached(bytes(bad),phase),oid)
            assert len(got)==1 and got[0].parent_damage_object_id==row['parent_id']
            opaque_examples.append(dict(source=key,changed_offset=at,
                                        parent_unchanged=True,key_changes=got[0].registration_key!=row['normalized_tail']))
        p=c['parent_evidence'];keys={p[k] for k in ('child_fixture','parent_fixture','hit_fixture')}
        keys.update(a['fixture'] for a in c['real_root_actions'])
        records.update({k:ck['packets'].get(k,fx['records'].get(k)) for k in keys})
        cases.append(c)
    output=dict(capture_sha256=ck['capture_sha256'],protocol_sha256=source_hash,
                cases=cases,records=records,negative_cases=negatives,
                contract='Only 0962 branch1 in 224/152 envelopes. Full parent ID at marker+49. Key is a fingerprint, not a creation timestamp. Opaque fields cannot choose the parent.',
                negative_note='Legacy prefix-only observations may still be returned; mutations must not yield nonzero full-parent proof.')
    (OUT/'fixtures.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    validation=dict(selected_hits=len(selected),selected_damage=sum(h['damage'] for h in selected),
                    **counts,negative_guard_checks=guard_count,opaque_semantics_checks=len(opaque_examples),
                    opaque_examples=opaque_examples,protocol_sha256=source_hash,
                    capture_reopened=False,scoring_or_replay=False,
                    fixtures_sha256=hashlib.sha256((OUT/'fixtures.json').read_bytes()).hexdigest())
    (OUT/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in validation.items() if k!='opaque_examples'},ensure_ascii=False))


if __name__=='__main__':main()
