"""Read-only exact byte inventory for independently observed gap objects."""
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


def main(profile,job,group=False):
    data=json.loads(profile.read_text('utf-8'))
    examples=[e for r in data['rows'] if r['job']==job and (group or r['object_kind']=='numeric_object') for e in r['examples']]
    examples=sorted(examples,key=lambda e:e['time'])[:6]
    objects={int(e['positional'][0]['group']) if group else e['event']['wire_damage_object_id'] for e in examples}
    owner=examples[0]['event']['wire_source_actor_id']
    limit=max(e['time'] for e in examples)+2
    key=bytes.fromhex('c8bfcda4943f2642fd8a723474')
    needles={o:o.to_bytes(4,'little') for o in objects}
    tokens={bytes(b^key[(phase+i)%13] for i,b in enumerate(needle)) for needle in needles.values() for phase in range(13)}
    matches=[];unknown=[];frame=0
    with Path(data['capture']).open('rb') as f:
        assert f.read(6)==b'DNDP1\0'
        while head:=f.read(17):
            frame+=1;offset=f.tell()-17;t,adapter,original,n=struct.unpack('<dBII',head)
            if t>limit:break
            raw=f.read(n)
            if not any(token in raw for token in tokens):continue
            packet=p.decode_captured_frame(t,raw,original)
            if packet is None or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                parsed=walk(wire)
                if not parsed:
                    unknown.append(dict(frame=frame,time=t,wire=wire.hex(),direction=packet.inferred_direction()))
                    continue
                phase,body,records=parsed
                positions={o:[i for i in range(9,len(body)-3) if body[i:i+4]==needle]for o,needle in needles.items()}
                positions={o:pos for o,pos in positions.items() if pos}
                if not positions:continue
                client=p.decode_v576_damage_object_registration(wire,packet.inferred_direction())
                server=p.decode_v576_server_damage_object_registrations(wire,packet.inferred_direction())
                matches.append(dict(time=t,frame=frame,file_offset=offset,message=mi,phase=phase,
                    direction=packet.inferred_direction(),positions=positions,records=records,
                    decoded_client=asdict(client)if client else None,decoded_server=[asdict(x)for x in server],
                    wire=wire.hex(),plain=body.hex(),wire_sha256=sha256(wire).hexdigest()))
    out=ROOT/'outputs/gap_object_records_v36_20260908';out.mkdir(exist_ok=True)
    target=out/(profile.stem+'_'+job+('_group' if group else '')+'.json')
    result=dict(capture=data['capture'],owner=owner,objects=sorted(objects),limit=limit,examples=examples,
        matches=matches,unparsed=unknown,scope='raw exact object bytes; no attribution promotions')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(path=str(target),matches=len(matches),unknown=len(unknown),objects=sorted(objects)),ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]),sys.argv[2],'--group' in sys.argv)
