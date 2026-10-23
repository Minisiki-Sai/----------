"""Cheap wire-token prefilter, then independent complete typed-record audit.

No replay state, no owner or skill attribution. A slot match is only a raw
anchor for subsequent entity/generation proof. All 13 XOR rotations allowed.
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
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame,split_v576_payload
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    report=json.loads((out/'composed36_after_kali.json').read_text('utf-8'))
    captures=[r for r in report['capture_manifest']
              if {'机械大师','重炮手'} & r['profession_summary'].keys()]
    key=bytes.fromhex('c8bfcda4943f2642fd8a723474')
    raw=(261101).to_bytes(4,'little')
    tokens={bytes(b^key[(phase+i)%13]for i,b in enumerate(raw))for phase in range(13)}
    results=[]
    for capture in captures:
        path=Path(capture['capture_path']);counts=Counter();matches=[]
        digest=sha256()
        with path.open('rb')as f:
            header=f.read(6);assert header==b'DNDP1\0';digest.update(header)
            while h:=f.read(17):
                digest.update(h); assert len(h)==17
                offset=f.tell()-17;t,adapter,original,n=struct.unpack('<dBII',h)
                frame=f.read(n);digest.update(frame);assert len(frame)==n
                counts['physical_frames']+=1
                if not any(token in frame for token in tokens):continue
                counts['prefilter_frames']+=1
                packet=decode_captured_frame(t,frame,original)
                if packet is None or packet.transport!='UDP':continue
                for mi,wire in enumerate(split_v576_payload(packet.payload)):
                    inv=inspect_udp_damage_records(wire,packet.inferred_direction())
                    if not inv.complete:continue
                    for r in inv.damage_records:
                        if r.family!='direct37' or dict(r.raw_skill_slots).get(102)!=261101:continue
                        counts['exact_self_slot_records']+=1
                        if len(matches)<25:
                            matches.append(dict(time=t,file_offset=offset,frame=counts['physical_frames'],
                                message=mi,direction=packet.inferred_direction(),wire=wire.hex(),
                                wire_sha256=sha256(wire).hexdigest(),source=r.source_actor_id,
                                target=r.target_actor_id,object=r.damage_object_id,amount=r.damage,
                                record_offset=r.record_offset,slots=r.raw_skill_slots,
                                admissible_fields=r.admissible_fields))
        assert digest.hexdigest()==capture['capture_sha256']
        results.append(dict(capture=str(path),capture_sha256=digest.hexdigest(),counts=dict(counts),matches=matches))
        print(path.name,dict(counts),flush=True)
    result=dict(scope='fixed-corpus captures with engineer jobs; raw wire anchors, no confirmed gain',
                confirmed_gain=None,captures=results)
    target=ROOT/'outputs/duck_261101_v32_20260908/fixed_corpus_slot_scan.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
