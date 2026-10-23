"""Cross-capture structural validation only; never installs a TCP key.

Recovered 22/10 names/numbers are not object-registration evidence. Check
independent already-decoded frames for the same names and keep literal object
matches explicitly separate from validated skill links (always zero here).
"""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.analyze_npcap_trace import read_trace
from tools.analyze_hub_transition_evidence import _tcp_plaintext
from dragon_nest_builder.live_dps.protocol_v576 import V576TransportReassembler,decode_captured_frame
from dragon_nest_builder.live_dps.tcp_profile_codec import decrypt_tcp_profile_body


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    hypotheses=json.loads((out/'tcp111_key_hypotheses.json').read_text('utf-8'))['zero_block_candidates']
    assert len(hypotheses)==1
    key=tuple(hypotheses[0]['key']);reports=[]
    for tag in('0821','0812'):
        d=json.loads((ROOT/f'outputs/named_hit_chain_followup_20260908/diagnostic_{tag}.json').read_text('utf-8'))
        objects={e['state']['object']for r in d['rows']for e in r['examples']if r['reason']=='no_matching_registration'}
        assembler=V576TransportReassembler();counts=Counter();names=Counter();identities={};ordinary=[];matches=Counter();examples=[]
        def inspect(packet):
            if packet.transport!='TCP':return
            if packet.inferred_direction()!='server_to_client'or len(packet.payload)!=111:
                plain=_tcp_plaintext(packet)
                if plain is not None:ordinary.append((plain[2:4].hex(),plain))
                return
            plain=decrypt_tcp_profile_body(packet.payload[3:],key,1)
            if int.from_bytes(plain[:2],'little')!=108:
                counts['invalid_length']+=1;return
            opcode=plain[2:4].hex();counts[opcode]+=1
            if opcode=='2210':
                raw=plain[8:48].split(b'\0',1)[0]
                try:name=raw.decode('utf-8')
                except UnicodeDecodeError:counts['invalid_name_utf8']+=1;return
                if not name or any(ord(ch)<32 for ch in name):counts['invalid_name_text']+=1;return
                names[name]+=1;identities.setdefault(int.from_bytes(plain[4:8],'little'),set()).add(name)
                for obj in objects:
                    if obj.to_bytes(4,'little')in plain:matches[obj]+=1
                if len(examples)<3:examples.append(dict(time=packet.timestamp_seconds,wire=packet.payload.hex(),plain=plain.hex()))
        for t,adapter,original,frame in read_trace(Path(d['capture'])):
            if adapter in(254,255):continue
            p=decode_captured_frame(t,frame,original)
            if p is None or p.transport!='TCP':continue
            for packet in assembler.feed(p):inspect(packet)
        for packet in assembler.flush():inspect(packet)
        corroboration={name:dict(Counter(op for op,plain in ordinary if name.encode('utf-8')in plain))for name in names}
        reports.append(dict(capture=d['capture'],counts=dict(counts),names=dict(names),
            short_id_names={k:sorted(v)for k,v in identities.items()},
            independent_known_envelope_name_matches=corroboration,literal_object_matches=dict(matches),
            confirmed_skill_links=0,examples=examples))
        print(json.dumps({k:v for k,v in reports[-1].items()if k!='examples'},ensure_ascii=False),flush=True)
    (out/'tcp111_cross_capture_validation.json').write_text(json.dumps(dict(scope='Offline decode only, no accepted skill links or runtime key changes',
        reports=reports),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
