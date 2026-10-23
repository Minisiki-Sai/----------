"""Read-only variable-envelope survey: exact repeated-owner registration only.

Does not assign any skill, not a production decoder. Searches ALL offsets,
not just the six known lengths, retaining guard failures for diagnosis.
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_remaining_registration_packet_decoder import u32, decode_candidate_registration
from tools.audit_remaining_registration_server_variants import DATA_DIR, hashes
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body, _v576_candidate_xor_phases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture',type=Path);p.add_argument('--owner',type=int,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--packets',type=int,nargs='*',default=[])
    a=p.parse_args();sys.stdout.reconfigure(encoding='utf-8')
    src=V576DiagnosticReplaySource(a.capture,DATA_DIR,{})
    token=a.owner.to_bytes(4,'little'); serial=0;counts=Counter();examples=defaultdict(list);selected=[]
    before=hashes();rows=[]
    def feed(packet):
        nonlocal serial
        serial+=1;payload=bytes(packet.payload)
        if serial in a.packets:
            selected.append(dict(p=serial,t=packet.timestamp_seconds,wire=payload.hex(),
                                 decoded={str(ph):_decode_v576_body(payload,ph).hex() for ph in _v576_candidate_xor_phases(payload)}))
        if str(packet.transport).upper()!='UDP':return ()
        for phase in _v576_candidate_xor_phases(payload):
            b=_decode_v576_body(payload,phase);o=b.find(token,9)
            while o>=0:
                if (o+49<=len(b) and b[o+4:o+9]==bytes(5)
                        and u32(b,o+45)==a.owner and 0<u32(b,o+9)<=0xffffff
                        and 0<u32(b,o+13)<=0x7fffffff):
                    tpl=u32(b,o+13);kind='typed_owner_repeat'
                    key=f'len={len(b)} O={o} phase={phase} template={tpl}'
                    counts[key]+=1
                    r=dict(p=serial,t=packet.timestamp_seconds,owner=a.owner,obj=u32(b,o+9),template=tpl,
                           length=len(b),offset=o,phase=phase,direction=packet.inferred_direction(),
                           accepted_six_layouts=decode_candidate_registration(payload) is not None,
                           resource_word=u32(b,o+49),repeated_resource=u32(b,o+65),
                           candidate_parent=u32(b,o+50),parent_branch_word=u32(b,o+54),
                           candidate_parent_resource=u32(b,o+70),
                           ff_base=b[o+53:o+61].hex(),ff_parent=b[o+58:o+66].hex(),
                           tail_hex=b[o:].hex(),wire_hex=payload.hex(),decoded_hex=b.hex())
                    rows.append(r)
                    if len(examples[key])<2:examples[key].append(r)
                o=b.find(token,o+1)
        return ()
    src._decoder.feed_events=feed
    try:src.poll()
    finally:src.close()
    after=hashes();result=dict(capture=str(a.capture),owner=a.owner,packets=serial,counts=counts,
                             examples=examples,selected_packets=selected,rows=rows,
                             core_before=before,core_after=after,core_unchanged=before==after)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(capture=str(a.capture),counts=counts,core_unchanged=before==after),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
