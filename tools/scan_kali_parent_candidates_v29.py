"""Necessary-byte candidate superset, not a claim of decoded or confirmed Hit.

The new parser requires a literal four-byte header and a 152-byte message.
Search every capture byte (even non-UDP/TCP bytes) so absence cannot be caused
by a skipped decoder path. Direction is permissive here; positives still need
full replay. No absence result supplies skill evidence.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_kali_11076_parent_v28 as parse

def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    doc=json.loads((out/'after_rake36_frozen/profession_direct_damage_report.json').read_text('utf-8'))
    rows=[]
    for r in doc['capture_manifest']:
        data=Path(r['capture_path']).read_bytes()
        assert sha256(data).hexdigest()==r['capture_sha256']
        cursor=-1;candidates=[]
        while (cursor:=data.find(bytes.fromhex('ce6c9500'),cursor+1))>=0:
            if cursor<5:continue
            raw=data[cursor-5:cursor-5+152]
            reg=parse(raw,'client_to_server')
            if reg is not None:candidates.append(dict(file_offset=cursor-5,owner=reg.source_actor_id,
                object=reg.damage_object_id,parent=reg.parent_damage_object_id,wire_sha256=sha256(raw).hexdigest()))
        rows.append(dict(capture=r['capture_path'],sha256=r['capture_sha256'],candidate_superset=candidates))
    result={'scope':'necessary byte superset only; not packet direction or Hit confirmation',
        'parser_sha256':sha256((ROOT/'dragon_nest_builder/live_dps/protocol_v576.py').read_bytes()).hexdigest(),
        'captures':rows}
    (out/'kali_parent_candidate_superset.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({Path(r['capture']).name:len(r['candidate_superset'])for r in rows if r['candidate_superset']},ensure_ascii=False))

if __name__=='__main__':main()
