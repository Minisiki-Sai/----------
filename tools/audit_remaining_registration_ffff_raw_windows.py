"""One streaming worker extracts bounded FFFF proof windows, without replay."""
from __future__ import annotations
import bisect
import hashlib
import importlib.util
import json
import struct
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog
OUT=ROOT/'outputs/audit_remaining_registration_ffff_raw_20260907'
CAPROOT=Path(r'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics')
HEADER=struct.Struct('<dBII')


def load(relative):return json.loads((ROOT/'outputs'/relative).read_text(encoding='utf-8'))
def u32(b,at):return int.from_bytes(b[at:at+4],'little')


def merge(ranges):
    out=[]
    for a,b in sorted(ranges):
        if out and a<=out[-1][1]:out[-1][1]=max(out[-1][1],b)
        else:out.append([a,b])
    return out


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if OUT.exists():raise SystemExit('Refusing to replace an existing raw extraction')
    OUT.mkdir()
    path=ROOT/'outputs/damage_attribution_candidate_v22_20260907/protocol_v576.py'
    protocol_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    spec=importlib.util.spec_from_file_location('ffff_stream_protocol',path)
    proto=importlib.util.module_from_spec(spec);sys.modules[spec.name]=proto;spec.loader.exec_module(proto)
    skills=frozenset(_load_skill_catalog(str(ROOT/'dragon_nest_builder/data')))
    review=load('audit_remaining_registration_ffff_cached_20260907/review.json')
    shadow=load('audit_remaining_registration_exact_parent_20260907_v3/影识别错误.json')
    soul=load('audit_remaining_registration_cross_shape_links_20260907_v1/0817黑典噬魂.json')
    ice=load('audit_remaining_registration_exact_parent_20260907_v3/0905黑龙经典冰灵.json')
    configs=[('shadow',shadow['capture'],1304482327,shadow['generations'],review['shadow']),
             ('soul',soul['capture'],1296954537,[*soul['typed_generations'],*soul['parent_generations']],review['soul']),
             ('ice',ice['capture'],1337770664,ice['generations'],[r for r in review['ice'] if r['unique_actual_root_action']])]
    for label,capture,owner,gs,selections in configs:
        parents={r['parent_packet'] for r in selections}
        ranges=[]
        for p in parents:
            g=next(g for g in gs if g['p']==p and g['owner']==owner)
            children=[r for r in selections if r['parent_packet']==p]
            hi=g['t']+12
            for child in children:
                hi=max(hi,max((h['t']+1 for h in child.get('anchor_hits',child.get('hit_examples',[]))),default=hi))
            ranges.append((g['t']-6,hi))
        ranges=merge(ranges);starts=[r[0] for r in ranges]
        owner_bytes=owner.to_bytes(4,'little');key=proto._V576_BODY_XOR_KEY
        pattern=proto._v576_wire_marker_pattern((owner_bytes,))
        key_starts={bytes(v^key[(k+i)%13] for i,v in enumerate(owner_bytes)):k for k in range(13)}
        packets={};actions=[];registrations=[];hits=[];digest=hashlib.sha256()
        counters=dict(physical_frames=0,window_frames=0,owner_messages=0)
        capture_path=CAPROOT/Path(capture).name
        started=last=time.monotonic()
        with capture_path.open('rb') as f:
            magic=f.read(6);assert magic==b'DNDP1\0';digest.update(magic)
            while header:=f.read(HEADER.size):
                assert len(header)==HEADER.size
                offset=f.tell()-HEADER.size;t,adapter,original,size=HEADER.unpack(header)
                frame=f.read(size);assert len(frame)==size;digest.update(header);digest.update(frame)
                counters['physical_frames']+=1
                wi=bisect.bisect_right(starts,t)-1
                if wi<0 or t>ranges[wi][1]:continue
                counters['window_frames']+=1
                pkt=proto.decode_captured_frame(t,frame,original)
                if not pkt or pkt.transport!='UDP':continue
                for mi,wire in enumerate(proto.split_v576_payload(pkt.payload)):
                    ms=list(pattern.finditer(wire,9))
                    if not ms:continue
                    phases=sorted({(m.start()-key_starts[m.group()])%13 for m in ms})
                    bodies={p:proto._decode_v576_body_uncached(wire,p) for p in phases}
                    bodies={p:b for p,b in bodies.items() if owner_bytes in b[9:]}
                    if not bodies:continue
                    counters['owner_messages']+=1
                    fid=f"f{counters['physical_frames']}_m{mi}"
                    direction=pkt.inferred_direction()
                    packets[fid]=dict(frame=counters['physical_frames'],message=mi,file_offset=offset,
                        timestamp=t,adapter=adapter,original_frame_length=original,direction=direction,
                        wire_hex=wire.hex(),wire_sha256=hashlib.sha256(wire).hexdigest(),
                        frame_hex=frame.hex(),frame_sha256=hashlib.sha256(frame).hexdigest(),
                        plaintext_by_phase={str(p):b.hex() for p,b in bodies.items()})
                    decoded_actions=[]
                    msg=proto.decode_v576_payload(wire,direction,skills,skills,(owner,))
                    if msg and msg.kind in ('skill_action','skill_cast') and msg.source_actor_id==owner:
                        decoded_actions.append(asdict(msg))
                    for d in (direction,) if direction else ('server_to_client','client_to_server'):
                        for action in proto.decode_v576_structured_actions(wire,d,skills,(owner,)):
                            if action.source_actor_id==owner:decoded_actions.append(asdict(action))
                    seen_action=set()
                    for action in decoded_actions:
                        token=(action['skill_id'],action.get('record_offset',0),action['kind'])
                        if token not in seen_action:
                            seen_action.add(token);actions.append(dict(fixture=fid,timestamp=t,**action))
                    for phase,b in bodies.items():
                        m=9
                        while (m:=b.find(b'\x09',m))>=0:
                            if (m>=13 and m+48<=len(b) and b[m+2:m+8]==bytes(6)
                                and u32(b,m-4)==u32(b,m+44)==owner
                                and 0<u32(b,m+8)<=0xffffff and 0<u32(b,m+12)<=0x7fffffff):
                                declared=int.from_bytes(b[m+1:m+3],'little')
                                registrations.append(dict(fixture=fid,timestamp=t,phase=phase,kind='server09',marker=m,
                                    owner=owner,obj=u32(b,m+8),template=u32(b,m+12),marker_hex=b[m:m+3].hex(),
                                    record_end=m+3+declared,packet_length=len(b),window=wi))
                            m+=1
                        for o in (10,17,20):
                            if (len(b)-o in (97,102,110,115) and o+49<=len(b)
                                and u32(b,o)==u32(b,o+45)==owner and b[o+4:o+9]==bytes(5)
                                and 0<u32(b,o+9)<=0xffffff and 0<u32(b,o+13)<=0x7fffffff):
                                registrations.append(dict(fixture=fid,timestamp=t,phase=phase,kind='typed',owner_offset=o,
                                    owner=owner,obj=u32(b,o+9),template=u32(b,o+13),packet_length=len(b),window=wi))
                    for hit in proto.decode_v576_embedded_direct_damage_events(wire,None,skills):
                        if hit.source_actor_id==owner:
                            hits.append(dict(fixture=fid,timestamp=t,**asdict(hit)))
                if time.monotonic()-last>15:
                    print(json.dumps(dict(label=label,seconds=round(time.monotonic()-started,2),**counters)),flush=True);last=time.monotonic()
        result=dict(capture=str(capture_path),capture_sha256=digest.hexdigest(),owner=owner,windows=ranges,
                    seconds=time.monotonic()-started,counters=counters,packets=packets,actions=actions,
                    registrations=registrations,hits=hits,selections=selections,protocol_sha256=protocol_hash)
        (OUT/f'{label}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(dict(label=label,seconds=round(result['seconds'],3),packets=len(packets),registrations=len(registrations),
            actions=len(actions),root_actions=sum(a['skill_id'] in (6204,5217,2353) for a in actions),hits=len(hits),sha256=digest.hexdigest())),flush=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==protocol_hash


if __name__=='__main__':main()
