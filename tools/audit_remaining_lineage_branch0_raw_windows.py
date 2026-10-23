"""Stream two DNDP1 files; retain three complete small windows, no source replay.

Only the frozen v22 stateless protocol is imported, under a private module name.
No score, combat/source instance, runtime mutation, or inferred-skill truth.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/damage_attribution/branch0_raw_windows_20260907"
CACHE = ROOT / "outputs/damage_attribution/branch0_cached_structure_20260907/cached_evidence.json"
CAP = Path(r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics")
PROTO = ROOT / "outputs/damage_attribution_candidate_v22_20260907/protocol_v576.py"
HDR = struct.Struct("<dBII")


def read(p): return json.loads(p.read_text(encoding="utf-8"))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def u(b, at): return int.from_bytes(b[at:at+4], "little") if 0 <= at <= len(b)-4 else 0
def write(name, obj): (OUT/name).write_text(json.dumps(obj, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def load_protocol():
    spec = importlib.util.spec_from_file_location("_branch0_frozen_v22_protocol", PROTO)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def skill_ids():
    result = set()
    for p in (ROOT/'dragon_nest_builder/data/modular/tables/skills/skill_damage_tiny_jobs').glob('job_*.json'):
        result.update(int(r[0]) for r in read(p).get('n',[]) if len(r)>1)
    for p in (ROOT/'dragon_nest_builder/data/modular/tables/skills/dngamer_skill_sim_reborn_split/jobs').glob('*.json'):
        for section in read(p).get('tree',{}).get('tree',[]):
            for r in section.get('skill',[]):
                v = r.get('_SkillTableID') or r.get('_SkillID')
                if v: result.add(int(v))
    assert {1208,1352,1353} <= result
    return frozenset(result)


def main():
    if OUT.exists(): raise SystemExit("Refusing to overwrite existing raw extraction")
    OUT.mkdir(parents=True)
    p = load_protocol(); skills = skill_ids(); baseline_hash = sha(PROTO)
    cached = read(CACHE); groups = defaultdict(list)
    for c in cached['conflict_records']:
        groups[(c['capture'],c['child']['owner'],c['fields']['parent_id'])].append(c)
    cases = []
    for (name,owner,parent), rows in groups.items():
        cases.append(dict(id=f'{name}_{parent}',capture=name,owner=owner,parent=parent,
                          start=min(r['parent']['t'] for r in rows)-6,
                          stop=max(h['t'] for r in rows for h in r['cached_hit_observations'])+2,
                          cached=rows,packets={},non_udp_frames=[],actions=[],registrations=[],
                          raw_registration_candidates=[],hits=[],lifecycle=[],counts=Counter()))
    captures = []
    for name in dict.fromkeys(c['capture'] for c in cases):
        selected = [c for c in cases if c['capture']==name]
        path = CAP/(name+'.dpscap'); digest=hashlib.sha256(); count=0
        with path.open('rb') as f:
            magic=f.read(6); assert magic==b'DNDP1\0';digest.update(magic)
            while head:=f.read(HDR.size):
                assert len(head)==HDR.size
                offset=f.tell()-HDR.size; t,adapter,original,size=HDR.unpack(head)
                raw=f.read(size);assert len(raw)==size;count+=1
                digest.update(head);digest.update(raw)
                windows=[c for c in selected if c['start']<=t<=c['stop']]
                if not windows: continue
                packet=p.decode_captured_frame(t,raw,original)
                for c in windows:
                    c['counts']['physical_frames']+=1
                    if packet is None or packet.transport.upper()!='UDP':
                        c['non_udp_frames'].append(dict(frame=count,file_offset=offset,t=t,adapter=adapter,
                            original=original,frame_hex=raw.hex(),transport=packet.transport if packet else None))
                        continue
                    for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                        key=f'f{count}_m{mi}'; direction=packet.inferred_direction()
                        meta=dict(frame=count,message=mi,file_offset=offset,t=t,adapter=adapter,
                                  direction=direction,length=len(wire),payload_sha256=hashlib.sha256(wire).hexdigest(),
                                  payload_hex=wire.hex(),signature=wire[5:9].hex())
                        c['packets'][key]=meta;c['counts']['udp_messages']+=1
                        owner=c['owner'];ob=owner.to_bytes(4,'little')
                        decoded={phase:p._decode_v576_body_uncached(wire,phase) for phase in range(13)}
                        phases={ph:b for ph,b in decoded.items() if ob in b[9:]}
                        if not phases: continue
                        c['counts']['owner_messages']+=1
                        meta['owner_phases']=list(phases)
                        for ph,b in phases.items():
                            m=9
                            while (m:=b.find(b'\x09',m))>=0:
                                if m>=4 and m+48<=len(b) and u(b,m-4)==owner and u(b,m+44)==owner:
                                    row=dict(fixture=key,t=t,order=[count,mi,m],phase=ph,m=m,
                                             marker=b[m:m+3].hex(),owner=owner,obj=u(b,m+8),template=u(b,m+12),
                                             guard=b[m+2:m+8]==bytes(6) and 0<u(b,m+8)<=0xffffff and 0<u(b,m+12)<=0x7fffffff,
                                             decoded_hex=b.hex(),kind='server09')
                                    c['raw_registration_candidates'].append(row)
                                    if row['guard']: c['registrations'].append(row)
                                m+=1
                        typed=p.decode_v576_damage_object_registration(wire,None)
                        if typed and typed.source_actor_id==owner:
                            c['registrations'].append(dict(fixture=key,t=t,order=[count,mi,typed.record_offset],
                                phase=typed.xor_phase,m=typed.record_offset,owner=owner,obj=typed.damage_object_id,
                                template=typed.projectile_template_id,parent=typed.parent_damage_object_id,
                                kind='typed',decoded=asdict(typed),decoded_hex=decoded[typed.xor_phase].hex()))
                        msgs=[]
                        primary=p.decode_v576_payload(wire,direction,skills,skills,(owner,))
                        if primary and primary.kind in ('skill_cast','skill_action') and primary.source_actor_id==owner:
                            msgs.append(primary)
                        msgs.extend(p.decode_v576_structured_actions(wire,direction,skills,(owner,)))
                        seen=set()
                        for a in msgs:
                            if a.source_actor_id!=owner or (a.skill_id,a.kind) in seen:continue
                            seen.add((a.skill_id,a.kind))
                            c['actions'].append(dict(fixture=key,t=t,order=[count,mi,0],decoded=asdict(a)))
                        for hit in p.decode_v576_embedded_direct_damage_events(wire,None,skills):
                            if hit.source_actor_id!=owner:continue
                            m=hit.record_offset
                            hit_phases=[ph for ph,b in phases.items() if b[m:m+3]==b'\x08\x73\0' and u(b,m+8)==owner and u(b,m+13)==hit.damage_object_id]
                            c['hits'].append(dict(fixture=key,t=t,order=[count,mi,m],decoded=asdict(hit),phases=hit_phases))
                        for life in p.decode_v576_player_lifecycle(wire,direction,(owner,)):
                            c['lifecycle'].append(dict(fixture=key,t=t,decoded=asdict(life)))
        actual=digest.hexdigest();expected=selected[0]['cached'][0]['capture_sha256'];assert actual==expected,(name,actual,expected)
        captures.append(dict(path=str(path),capture_sha256=actual,physical_frames=count))
        print(json.dumps(dict(capture=name,frames=count,windows=[dict(id=c['id'],**c['counts'],actions=len(c['actions']),registrations=len(c['registrations']),hits=len(c['hits'])) for c in selected]),ensure_ascii=False),flush=True)
    assert sha(PROTO)==baseline_hash,'Frozen protocol changed externally while extracting'
    write('raw_windows.json',dict(captures=captures,protocol_path=str(PROTO),protocol_sha256=baseline_hash,
        skill_ids=sorted(skills),cases=cases,scope='one sequential DNDP1 stream per capture; retain complete UDP windows and all non-UDP raw frames; no source replay or scoring'))
    print('saved',OUT/'raw_windows.json',flush=True)


if __name__=='__main__': main()
