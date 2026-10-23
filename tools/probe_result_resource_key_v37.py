"""Discovery: do hit records carry the ACT resource selector from 09?

Domains are owner-scoped exact 09 typed boundaries. Controls are fixed own
skill slots or explicitly labelled near-object template discovery controls,
never prior displayed/inferred names. Neither is promoted by this probe.
"""
from collections import Counter,defaultdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.damage_object_origins import _SPEC_BY_PROJECTILE_TEMPLATE
from dragon_nest_builder.live_dps.replay_normalization import _canonical_live_skill_id
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from tools.probe_compact_entity_attack_selectors import walk


def main(path,seconds=180):
    domains=defaultdict(set);reg_examples={};hits=[];counts=Counter();start=None;frame=0;origins={}
    with path.open('rb') as f:
        assert f.read(6)==b'DNDP1\0'
        while head:=f.read(17):
            frame+=1;offset=f.tell()-17;t,_,original,n=struct.unpack('<dBII',head)
            if start is None:start=t
            if t>start+seconds:break
            packet=p.decode_captured_frame(t,f.read(n),original)
            if packet is None or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                walked=walk(wire)
                if walked is None:continue
                phase,body,records=walked
                for owner,m,op,length in records:
                    if op!=9 or length not in (0x5d,0x61) or owner<0x1000000:continue
                    u32=lambda rel:int.from_bytes(body[m+rel:m+rel+4],'little')
                    resource=u32(48)&65535
                    if (body[m+3:m+8]!=bytes(5) or u32(44)!=owner or not 0<resource<65535
                            or resource!=u32(64) or body[m+52:m+60]!=b'\xff'*8):continue
                    spec=_SPEC_BY_PROJECTILE_TEMPLATE.get(u32(12))
                    if spec is None:continue
                    skill=int(_canonical_live_skill_id(spec.canonical_skill_id))
                    domains[owner,resource].add(skill);counts['registrations']+=1
                    origins[owner,u32(8)]=(t,resource,skill)
                    reg_examples.setdefault((owner,resource,skill),dict(frame=frame,time=t,file_offset=offset,
                        message=mi,marker=m,object=u32(8),template=u32(12),resource=resource,
                        skill=skill,wire=wire.hex(),wire_sha256=sha256(wire).hexdigest()))
                if not any(op==8 and length in (0x73,0x77)for _,_,op,length in records):continue
                inv=inspect_udp_damage_records(wire,packet.inferred_direction())
                if not inv.complete:continue
                for r in inv.damage_records:
                    if not r.admissible_fields or r.source_actor_id<0x1000000:continue
                    own=dict(r.raw_skill_slots).get(102 if r.family=='direct37' else 106)
                    exact=int(_canonical_live_skill_id(own)) if own and own<100000 else 0
                    registered=origins.get((r.source_actor_id,r.damage_object_id))
                    control_source='fixed_own_slot' if exact else 'none'
                    # Discovery control only. A near complete same-owner object
                    # registration supplies a template, not a production proof.
                    if not exact and registered and 0<=t-registered[0]<=.5:
                        exact=registered[2];control_source='near_object_template_discovery_control'
                        counts['near_registration_controls']+=1
                    hits.append(dict(owner=r.source_actor_id,family=r.family,exact=exact,raw=r.record_bytes,
                        frame=frame,offset=offset,time=t,marker=r.record_offset,object=r.damage_object_id,
                        control_source=control_source))
    stats=defaultdict(Counter);skills=defaultdict(set);counterexamples={}
    for hit in hits:
        raw=hit['raw'];excluded=[(102,106),(114,118)]if hit['family']=='direct37' else [(106,110)]
        for width in (1,2,4):
            for rel in range(3,len(raw)-4-width+1):
                if any(rel<b and rel+width>a for a,b in excluded):continue
                value=int.from_bytes(raw[rel+4:rel+4+width],'little')
                domain=domains.get((hit['owner'],value))
                if not domain or len(domain)!=1:continue
                candidate=next(iter(domain));key=(hit['family'],width,rel)
                if hit['exact']:
                    stats[key]['support']+=1;skills[key].add(hit['exact'])
                    if candidate!=hit['exact']:
                        stats[key]['conflicts']+=1
                        counterexamples.setdefault(key,dict(frame=hit['frame'],marker=hit['marker'],owner=hit['owner'],
                            value=value,predicted=candidate,exact=hit['exact'],
                            control_source=hit['control_source']))
                else:stats[key]['unknown_candidates']+=1
    rows=[dict(family=k[0],width=k[1],offset=k[2],**v,distinct_exact_skills=len(skills[k]),
        counterexample=counterexamples.get(k))for k,v in stats.items()]
    eligible=[r for r in rows if r.get('support',0)>=30 and not r.get('conflicts')and r['distinct_exact_skills']>=3 and r.get('unknown_candidates',0)>0]
    out=ROOT/'outputs/result_resource_key_v37_20260908';out.mkdir(exist_ok=True)
    result=dict(capture=str(path),seconds=seconds,counts=dict(counts),raw_hits=len(hits),
        domain_rows=[dict(owner=k[0],resource=k[1],skills=sorted(v))for k,v in domains.items()],
        registration_examples=list(reg_examples.values()),candidates=rows,eligible_discovery_only=eligible,
        new_confirmed_hits=0,production_changed=False)
    (out/(path.stem+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(capture=path.name,raw_hits=len(hits),counts=dict(counts),domains=len(domains),
        eligible=eligible,best=sorted([r for r in rows if r.get('support',0)>=30],
        key=lambda r:r.get('conflicts',0)/r['support'])[:5]),ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]))
