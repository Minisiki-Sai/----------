"""Bounded offline key hypotheses for previously undecoded 111-byte frames.

No key registry/runtime writes. Tail-zero is only a hypothesis. Every proposed
key is checked on all held-out headers and independent known actor IDs; neither
matching lengths nor a high zero-byte ratio alone is treated as protocol proof.
"""
from collections import Counter
import struct
import json
from pathlib import Path
import sys
from z3 import BitVec,BitVecVal,Extract,Solver,sat,Or

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.recover_tcp_key_from_headers import _read_server_payloads,_decrypt_block
from dragon_nest_builder.live_dps.tcp_profile_codec import decrypt_tcp_profile_body


def main():
    d=json.loads((ROOT/'outputs/named_hit_chain_followup_20260908/diagnostic_0821.json').read_text('utf-8'))
    actors={e['state']['wire_source']for r in d['rows']for e in r['examples']if e['state']['wire_source']>0x1000000}
    wires=_read_server_payloads((Path(d['capture']),),111)
    bodies=list(dict.fromkeys(w[3:]for w in wires))
    headers=list(dict.fromkeys(b[:8]for b in bodies))
    tails=Counter(b[-4:]for b in bodies)
    out=[]
    # A repeated encrypted all-zero block is a second, independent hypothesis;
    # unlike header-only solver models, validate actors/structures afterwards.
    blocks=Counter(b[i:i+8]for b in bodies for i in range(8,104,8))
    zero_candidates=[]
    for block,count in blocks.most_common(8):
        left,right=struct.unpack('<II',block)
        mix=((((left<<4)&0xffffffff)^(left>>5))+left)&0xffffffff
        key=(left,0,0,((right^mix)-0x9e3779b9)&0xffffffff)
        if not all(int.from_bytes(decrypt_tcp_profile_body(h,key,1)[:2],'little')==108 for h in headers):continue
        plains=[decrypt_tcp_profile_body(b,key,1)for b in bodies]
        found=Counter(a for p in plains for a in actors if a.to_bytes(4,'little')in p)
        row=dict(hypothesis='repeated zero block',cipher_block=block.hex(),block_count=count,key=key,
            headers_valid=len(plains),known_actors=dict(found),
            opcodes=dict(Counter(p[2:4].hex()for p in plains)),samples=[p.hex()for p in plains[:5]])
        zero_candidates.append(row)
        print('ZERO',json.dumps(row),flush=True)
    print('frames',len(wires),'unique bodies',len(bodies),'headers',len(headers),
        'tails',[(k.hex(),v)for k,v in tails.most_common(4)],flush=True)
    for tail,n in tails.most_common(3):
        k0=int.from_bytes(tail,'little');k3=BitVec('gap_tcp_111_k3',32)
        keys=[BitVecVal(k0,32),BitVecVal(0,32),BitVecVal(0,32),k3]
        solver=Solver();solver.set(timeout=10000)
        for h in headers[:32]:solver.add(Extract(15,0,_decrypt_block(h,keys,1)[0])==108)
        state=solver.check();row=dict(tail_zero_hypothesis=tail.hex(),tail_count=n,solver=str(state),candidates=[])
        for _ in range(4):
            if state!=sat:break
            value=solver.model().eval(k3).as_long();key=(k0,0,0,value)
            plains=[decrypt_tcp_profile_body(b,key,1)for b in bodies]
            valid=[p for p in plains if int.from_bytes(p[:2],'little')==108]
            found=Counter();positions=Counter()
            for p in valid:
                for a in actors:
                    at=p.find(a.to_bytes(4,'little'))
                    if at>=0:found[a]+=1;positions[at]+=1
            candidate=dict(key=key,headers_valid=len(valid),total=len(plains),
                opcodes=dict(Counter(p[2:4].hex()for p in valid)),known_actors=dict(found),
                actor_positions=dict(positions),samples=[p.hex()for p in valid[:6]])
            row['candidates'].append(candidate)
            print(json.dumps({k:v for k,v in candidate.items()if k!='samples'}),flush=True)
            solver.add(k3!=value);state=solver.check()
        out.append(row)
    path=ROOT/'outputs/compact_entity_selectors_20260908/tcp111_key_hypotheses.json'
    path.write_text(json.dumps(dict(scope='offline hypotheses, not accepted keys',capture=d['capture'],rows=out,
        zero_block_candidates=zero_candidates),indent=2),encoding='utf-8')
    print(path,flush=True)


if __name__=='__main__':main()
