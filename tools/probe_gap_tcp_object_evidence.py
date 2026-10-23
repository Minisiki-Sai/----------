"""Read-only TCP search for UDP gap objects; matches are NOT skill evidence.

Reassemble the full capture before restricting times. Keep counts of undecoded
frames so an unsuccessful search cannot be misreported as protocol absence.
"""
from collections import Counter,defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame,V576TransportReassembler
from tools.analyze_hub_transition_evidence import _tcp_plaintext


def probe(diagnostic,output):
    d=json.loads(diagnostic.read_text('utf-8'))
    cases=[]
    for r in d['rows']:
        if r['reason']!='no_matching_registration' or r['hits']<15:continue
        for a in r['examples'][:1]:
            if not a['state']['object']:continue
            cases.append(dict(job=r['job'],old_label_diagnostic_only=r['skill'],
                time=a['time'],owner=a['state']['wire_source'],object=a['state']['object'],
                window_packets=0,window_decoded=0,matches=[]))
    assembler=V576TransportReassembler();counts=Counter();unknown=Counter()
    flows=defaultdict(Counter);bodies=defaultdict(set)
    def inspect(packet):
        if packet.transport!='TCP':return
        counts['logical_tcp']+=1
        plain=_tcp_plaintext(packet)
        flow=(packet.source_ip,packet.source_port,packet.destination_ip,packet.destination_port)
        flows[flow]['decoded'if plain is not None else'undecoded']+=1
        counts['decoded'if plain is not None else'undecoded']+=1
        windows=[r for r in cases if r['time']-30<=packet.timestamp_seconds<=r['time']+.05]
        if plain is None:
            unknown[(packet.inferred_direction(),len(packet.payload),packet.payload[3:8].hex())]+=1
            key=(*flow,len(packet.payload))
            if len(bodies[key])<65536:bodies[key].add(sha256_body(packet.payload))
        for r in windows:
            r['window_packets']+=1
            r['window_decoded']+=int(plain is not None)
        if plain is None:return
        for r in cases:
            if packet.timestamp_seconds>r['time']+.05:continue
            obj=r['object'].to_bytes(4,'little');owner=r['owner'].to_bytes(4,'little')
            positions=[i for i in range(len(plain)-3)if plain[i:i+4]==obj]
            if not positions:continue
            r['matches'].append(dict(time=packet.timestamp_seconds,delta=packet.timestamp_seconds-r['time'],
                object_positions=positions,owner_present=owner in plain,
                opcode=plain[2:4].hex(),length=len(plain),wire_sha256=hashlib.sha256(packet.payload).hexdigest(),
                plaintext=plain.hex(),wire=packet.payload.hex()))
    for time,adapter,original,frame in read_trace(Path(d['capture'])):
        if adapter in (254,255):continue
        packet=decode_captured_frame(time,frame,original)
        if packet is None or packet.transport!='TCP':continue
        for logical in assembler.feed(packet):inspect(logical)
    for logical in assembler.flush():inspect(logical)
    result=dict(capture=d['capture'],scope='Stateless TCP discovery, not confirmed attribution',counts=dict(counts),
        unknown_shapes=[dict(direction=k[0],length=k[1],body_prefix=k[2],count=v)for k,v in unknown.most_common()],
        flows=[dict(endpoints=k,counts=dict(v))for k,v in flows.items()],
        undecoded_body_cardinality=[dict(flow_length=k,unique_bodies=len(v))for k,v in bodies.items()],cases=cases)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(capture=d['capture'],counts=dict(counts),cases=len(cases),
        matches=sum(len(r['matches'])for r in cases),
        near_matches=sum(sum(abs(m['delta'])<30 for m in r['matches'])for r in cases)),ensure_ascii=False),flush=True)


def sha256_body(wire):return hashlib.sha256(wire[3:]).digest()


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('diagnostic',type=Path);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();probe(args.diagnostic,args.output)
