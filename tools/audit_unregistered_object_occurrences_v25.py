"""Locate full object tokens in raw UDP; co-occurrences are discovery only."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('inventory',type=Path)
    ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args()
    if args.output.exists(): raise RuntimeError('Existing evidence')
    from dragon_nest_builder.live_dps import protocol_v576 as p
    from tools.analyze_npcap_trace import read_trace
    doc=json.loads(args.inventory.read_text(encoding='utf-8'))
    capture=Path(doc['capture'])
    assert hashlib.sha256(capture.read_bytes()).hexdigest()==doc['capture_sha256']
    selected=[r for r in doc['rows'] if r['job'] in ('圣骑士','魔羽') and r['method']!='damage_object_projectile_link_provisional'][:10]
    queries={}
    for r in selected:
        x=r['examples'][0]; h=x['raw_hit']; oid=h['damage_object_id']; who=h['source_actor_id']
        queries[(who,oid)]=dict(job=r['job'],diagnostic_skill=r['skill'],time=x['time'],samples=[],count=0)
    tokens={oid:oid.to_bytes(4,'little') for _,oid in queries}
    pattern=p._v576_wire_marker_pattern(tuple(tokens.values()))
    for frame,(ts,adapter,original,raw) in enumerate(read_trace(capture),1):
        pkt=p.decode_captured_frame(ts,raw,original)
        if not pkt or pkt.transport!='UDP':continue
        for mi,wire in enumerate(p.split_v576_payload(pkt.payload)):
            if not pattern.search(wire,9):continue
            for phase in range(13):
                body=p._decode_v576_body(wire,phase)
                for (owner,oid),q in queries.items():
                    start=body.find(tokens[oid],9)
                    if start<0:continue
                    q['count']+=1
                    if len(q['samples'])>=80:continue
                    positions=[]
                    while start>=0:
                        positions.append(start); start=body.find(tokens[oid],start+1)
                    q['samples'].append(dict(frame=frame,message=mi,time=ts,delta=ts-q['time'],
                        phase=phase,offsets=positions,owner_present=owner.to_bytes(4,'little') in body,
                        direction=pkt.inferred_direction(),wire=wire.hex(),plain=body.hex()))
    out=dict(capture=str(capture),capture_sha256=doc['capture_sha256'],
             policy='Token occurrence only, never proof of skill identity.',
             queries=[dict(owner=k[0],object=k[1],**v) for k,v in queries.items()])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    for q in out['queries']:
        print(q['job'],q['diagnostic_skill'],q['object'],q['count'],
              [(s['frame'],round(s['delta'],3),s['offsets'],s['owner_present']) for s in q['samples'] if s['owner_present']])


if __name__=='__main__':main()
