"""Reread saved raw anchors; fixtures contain original birth/owner/object/hit wires."""
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.probe_compact_entity_attack_selectors import ResourceIndex
from dragon_nest_builder.live_dps import protocol_v576 as p


def main():
    resources=ResourceIndex();cases=[];static=[]
    source=ROOT/'outputs/compact_entity_selectors_20260908/raw35'
    for path in sorted(source.glob('*.json')):
        doc=json.loads(path.read_text('utf-8'))
        for row in doc['rows']:
            if row['template']not in (32005,33608)or not row['joins']:continue
            pair=next((p for p in row['joins']if p['hit']['amount']>0),None)
            if pair is None:continue
            reg=pair['registration']
            if not reg['owner']:continue
            proof=resources.selector(row['template'],row['action'],row['signal'])
            assert proof['match']and proof['signal_type']==15
            anchors=[reg['birth'],reg['owner'],reg,pair['hit']]
            with Path(doc['capture']).open('rb')as f:
                for a in anchors:
                    f.seek(a['file_offset']);t,_,original,size=struct.unpack('<dBII',f.read(17))
                    packet=p.decode_captured_frame(t,f.read(size),original)
                    assert packet and list(p.split_v576_payload(packet.payload))[a['message']].hex()==a['wire']
            if len([c for c in cases if c['template']==row['template']])<4:
                cases.append(dict(capture=doc['capture'],template=row['template'],registration=reg,
                    hit=pair['hit'],static=proof))
            static.append(dict(capture=doc['capture'],template=row['template'],action=row['action'],
                signal=row['signal'],projectile=proof['projectile'],registrations=row['registrations'],
                candidate_hits=row['joined_hit_candidates'],self_slots=row['self_slots'],proof=proof))
    assert {c['template']for c in cases}=={32005,33608}
    out=ROOT/'tests/data/compact_entity_selectors_raw.json'
    out.write_text(json.dumps(dict(schema='actual-wire-reread-v1',cases=cases),ensure_ascii=False,indent=2),encoding='utf-8')
    (source.parent/'validated_static_anchors.json').write_text(json.dumps(static,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(cases=len(cases),anchors=len(static),fixture=str(out)),ensure_ascii=False))


if __name__=='__main__':main()
