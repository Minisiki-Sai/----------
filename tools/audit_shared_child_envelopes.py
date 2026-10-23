"""Inventory full shared-child envelopes independently of inferred skill names."""
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p


def main(capture,output,templates=(2023,3021,5709,5711,5737)):
    counts=Counter(); examples=defaultdict(list)
    with capture.open('rb') as f:
        assert f.read(6)==b'DNDP1\0'
        while head:=f.read(17):
            file_offset=f.tell()-17
            t,adapter,original,size=struct.unpack('<dBII',head)
            packet=p.decode_captured_frame(t,f.read(size),original)
            if not packet or packet.transport != 'UDP': continue
            for wire in p.split_v576_payload(packet.payload):
                for reg in p.decode_v576_server_damage_object_registrations(wire,None,set(templates)):
                    b=p._decode_v576_body_uncached(wire,reg.xor_phase)
                    cursor=14;records=[]
                    while cursor+7<=len(b):
                        m=cursor+4;length=int.from_bytes(b[m+1:m+3],'little')
                        records.append((b[m],length))
                        if m+3+length>len(b):break
                        cursor=m+3+length
                    m=reg.record_offset
                    branch=int.from_bytes(b[m+53:m+57],'little')
                    key=(reg.projectile_template_id, bool(reg.parent_damage_object_id),len(b),
                         b[5:9].hex(),tuple(records),cursor==len(b),branch&0xffff)
                    counts[key]+=1
                    if len(examples[key])<2:
                        examples[key].append(dict(t=t,file_offset=file_offset,wire_hex=wire.hex(),
                            decoded_hex=b.hex(),reg=asdict(reg)))
    rows=[dict(template=k[0],parent_read=k[1],length=k[2],signature=k[3],records=k[4],
        closed=k[5],branch=k[6],count=v,examples=examples[k])for k,v in counts.most_common()]
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(capture=str(capture),templates=list(templates),rows=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    totals=Counter()
    for r in rows:totals[(r['template'],r['parent_read'])]+=r['count']
    print(json.dumps(dict(capture=capture.name,groups=len(rows),counts=[
        dict(template=k[0],parent_read=k[1],n=v)for k,v in totals.items()],output=str(output)),ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('capture',type=Path);ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--templates',type=int,nargs='+',default=[2023,3021,5709,5711,5737])
    args=ap.parse_args();main(args.capture,args.output,args.templates)
