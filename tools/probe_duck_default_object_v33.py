"""Find raw occurrences of one unjoined duck object, not by old skill name."""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from tools.probe_compact_entity_attack_selectors import walk


def main():
    out=ROOT/'outputs/compact_rejection_v33_20260908'
    d=json.loads((out/'0821硬核04.json').read_text('utf-8'))
    e=next(r for r in d['rows']if r['reason']=='registration_missing')['examples'][0]
    event=e['event'];obj=event['wire_damage_object_id'];entity=event['wire_source_actor_id']
    key=bytes.fromhex('c8bfcda4943f2642fd8a723474');needle=obj.to_bytes(4,'little')
    tokens={bytes(b^key[(phase+i)%13]for i,b in enumerate(needle))for phase in range(13)}
    matches=[];frame_num=0;limit=e['time']+5
    with Path(d['capture']).open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        while head:=f.read(17):
            frame_num+=1;offset=f.tell()-17;t,adapter,original,n=struct.unpack('<dBII',head)
            if t>limit:break
            raw=f.read(n)
            if not any(token in raw for token in tokens):continue
            packet=p.decode_captured_frame(t,raw,original)
            if packet is None or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                parsed=walk(wire)
                if not parsed:continue
                phase,body,records=parsed
                positions=[i for i in range(9,len(body)-3)if body[i:i+4]==needle]
                if not positions:continue
                matches.append(dict(time=t,delta=t-e['time'],frame=frame_num,file_offset=offset,
                    message=mi,phase=phase,direction=packet.inferred_direction(),positions=positions,
                    records=records,wire=wire.hex(),plain=body.hex(),wire_sha256=sha256(wire).hexdigest()))
    result=dict(capture=d['capture'],entity=entity,object=obj,anchor=e['time'],
        scope='all physically framed UDP from recording start to first anchor+5s; no confirmed gain',matches=matches)
    (out/'duck_default_object_occurrences.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print([{k:v for k,v in r.items()if k not in('wire','plain','wire_sha256')}for r in matches])


if __name__=='__main__':main()
