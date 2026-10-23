"""Reinspect bounded v34 unknown chains with the full zero-length-aware walker."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def main():
    path=ROOT/'outputs/magma_burst_lifecycle_v34_20260908/evidence.json'
    source=json.loads(path.read_text('utf-8'))
    results=[]
    for case in source:
        reasons=Counter(); recovered=[]
        for row in case['unparsed']:
            wire=bytes.fromhex(row['wire'])
            inv=inspect_udp_damage_records(wire,row['direction'])
            reasons[inv.rejection_reason or 'complete']+=1
            if inv.complete:
                records=[dict(actor=int.from_bytes(r.record_bytes[:4],'little'),type=r.record_type,
                    length=r.declared_length,marker=r.record_offset,record=r.record_bytes.hex())
                    for r in inv.typed_records]
                relevant=[r for r in records if r['actor'] in (case['entity'],case['owner'])]
                recovered.append(dict(**row,phase=inv.xor_phase,records=records,relevant=relevant))
        results.append(dict(capture=case['capture'],entity=case['entity'],reasons=dict(reasons),
            recovered=recovered,new_confirmed_hits=0))
        print(json.dumps(dict(capture=case['capture'],reasons=dict(reasons),
            relevant=[dict(frame=r['frame'],records=r['relevant'])for r in recovered if r['relevant']]),ensure_ascii=False))
    out=ROOT/'outputs/magma_zero_companions_v35_20260908'
    out.mkdir(exist_ok=True)
    (out/'audit.json').write_text(json.dumps(dict(source_sha256=sha256(path.read_bytes()).hexdigest(),
        results=results),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
