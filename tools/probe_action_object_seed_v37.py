"""Inspect action/registration records for a possible explicit object seed.

Numeric proximity is discovery only, never a proof or runtime mapping.
"""
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from tools.probe_compact_entity_attack_selectors import walk


def main(profile):
    data=json.loads(profile.read_text('utf-8'));owner=data['owner'];limit=data['limit']
    records=[];unknown=[];counts=Counter();frame=0
    with Path(data['capture']).open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        while head:=f.read(17):
            frame+=1;offset=f.tell()-17;t,_,original,n=struct.unpack('<dBII',head)
            if t>limit:break
            raw=f.read(n);packet=p.decode_captured_frame(t,raw,original)
            if not packet or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                parsed=walk(wire)
                if not parsed:
                    counts['unparsed']+=1;continue
                phase,body,items=parsed
                for actor,m,op,length in items:
                    if actor!=owner:continue
                    counts[f'{op:02x}/{length:04x}']+=1
                    if op not in (1,2,3,9,14,18):continue
                    record=body[m-4:m+3+length]
                    nearby=[dict(relative=i-4,value=int.from_bytes(record[i:i+4],'little'))
                        for i in range(7,len(record)-3) if 95000<=int.from_bytes(record[i:i+4],'little')<=110000]
                    record_row=dict(time=t,frame=frame,file_offset=offset,message=mi,phase=phase,
                        owner=actor,op=op,length=length,marker=m,record=record.hex(),numeric_seed_candidates=nearby,
                        direction=packet.inferred_direction(),wire=wire.hex(),wire_sha256=sha256(wire).hexdigest())
                    if op==9:
                        record_row['parsed_registrations']=[asdict(r)for r in p.decode_v576_server_damage_object_registrations(wire,packet.inferred_direction(),include_unmapped=True)if r.record_offset==m]
                    records.append(record_row)
    out=ROOT/'outputs/action_object_seed_v37_20260908';out.mkdir(exist_ok=True)
    target=out/(profile.stem+'.json')
    target.write_text(json.dumps(dict(capture=data['capture'],owner=owner,limit=limit,counts=dict(counts),records=records,
        object_anchors=data['examples'],scope='bounded discovery; no runtime or confirmed-coverage changes'),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(path=str(target),counts=dict(counts),rows=len(records),
        action_seeds=[dict(frame=r['frame'],op=r['op'],candidates=r['numeric_seed_candidates'])for r in records if r['op']!=9 and r['numeric_seed_candidates']]),ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]))
