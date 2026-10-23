"""Investigate compressed/partial object IDs; never produce attribution.

The low 16 bits are NOT unique, even within a player's lifetime. This report
only helps locate unparsed records. Promotion requires a separate full chain.
"""
from collections import Counter
import argparse
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from tools.probe_compact_entity_attack_selectors import walk


def main(diagnostic,output):
    data=json.loads(diagnostic.read_text('utf-8'))
    cases=[dict(job=r['job'],label_diagnostic_only=r['skill'],anchor=r['examples'][0],matches=[])
        for r in data['rows']if r['reason']=='no_matching_registration'and r['hits']>=30][:12]
    with Path(data['capture']).open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        frame=0
        while head:=f.read(17):
            frame+=1;offset=f.tell()-17;t,adapter,original,n=struct.unpack('<dBII',head);raw=f.read(n)
            selected=[c for c in cases if c['anchor']['time']-3<=t<c['anchor']['time']]
            if not selected:continue
            packet=p.decode_captured_frame(t,raw,original)
            if packet is None or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                for ph in range(13):
                    b=p._decode_v576_body_uncached(wire,ph)
                    for c in selected:
                        owner=c['anchor']['state']['wire_source'];obj=c['anchor']['state']['object']
                        if owner.to_bytes(4,'little')not in b[9:]:continue
                        short=(obj&65535).to_bytes(2,'little')
                        positions=[i for i in range(10,len(b)-1)if b[i:i+2]==short]
                        if not positions or obj.to_bytes(4,'little')in b[9:]:continue
                        framed=walk(wire)
                        c['matches'].append(dict(time=t,delta=t-c['anchor']['time'],frame=frame,offset=offset,message=mi,
                            phase=ph,length=len(wire),short_positions=positions,
                            framed_records=framed[2]if framed and framed[0]==ph else None,
                            direction=packet.inferred_direction(),wire=wire.hex(),plain=b.hex()))
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(scope='Partial-object discovery only; not proof',cases=cases),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(job=c['job'],label=c['label_diagnostic_only'],matches=len(c['matches']))for c in cases],ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('diagnostic',type=Path)
    ap.add_argument('--output',required=True,type=Path);a=ap.parse_args();main(a.diagnostic,a.output)
