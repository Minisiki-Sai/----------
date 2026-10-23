"""Physical frame anchors for the four selected audits; no runtime replay."""
from __future__ import annotations
import dataclasses,json,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog,_load_damage_skill_ids
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame,decode_v576_payload,decode_v576_structured_actions,_decode_v576_body,_v576_candidate_xor_phases

def main():
    catalog=_load_skill_catalog(str(DATA_DIR.resolve())); skills=frozenset(catalog)
    damage=_load_damage_skill_ids(str(DATA_DIR.resolve()))
    result=[]
    for tag in ('timelord','souleater','darksummoner','clerics'):
        audit=json.loads((ROOT/f'outputs/damage_attribution/audit_remaining_lineage_five_jobs_v12_{tag}.json').read_text(encoding='utf8'))
        interesting=[r for r in audit['records'] if r['classification']!='deterministic' and r['raw'] and r['raw']['skill_id_exact']]
        times={r['raw']['timestamp'] for r in interesting}
        regs=[r for r in audit['registrations'] if r['template'] in {11075,11076,5528,4015,60070}]
        # Three independent object cycles per template, plus every exact gap.
        seen={}; chosen=[]
        for r in regs:
            k=r['template'];seen[k]=seen.get(k,0)+1
            if seen[k]<=3:chosen.append(r);times.add(r['timestamp'])
        windows=[(t-4,t+0.05) for t in times];actors=set(map(int,audit['actor_jobs']))
        frames=[];actions=[];index=0
        with Path(audit['capture']).open('rb') as h:
            assert h.read(6)==b'DNDP1\0'
            while header:=h.read(17):
                if len(header)<17:break
                index+=1;offset=h.tell()-17;t,adapter,original,length=struct.unpack('<dBII',header);data=h.read(length)
                if len(data)!=length:break
                if not any(lo<=t<=hi for lo,hi in windows):continue
                packet=decode_captured_frame(t,data,original)
                if packet is None or packet.transport.upper()!='UDP':continue
                primary=decode_v576_payload(packet.payload,packet.inferred_direction(),damage,skills,actors)
                found=list(decode_v576_structured_actions(packet.payload,packet.inferred_direction(),skills,actors))
                if primary and primary.kind in {'skill_cast','skill_action'}:found.append(primary)
                for a in found:
                    if a.source_actor_id in actors:
                        actions.append({'frame_index':index,'file_offset':offset,'timestamp':t,'message':dataclasses.asdict(a)})
                if t in times:
                    frames.append({'frame_index':index,'file_offset':offset,'timestamp':t,'adapter':adapter,'direction':packet.inferred_direction(),'payload_hex':packet.payload.hex(),'primary':dataclasses.asdict(primary) if primary else None})
        result.append({'tag':tag,'capture':audit['capture'],'exact_gaps':interesting,'registration_samples':chosen,'frames':frames,'actions':actions})
    out=ROOT/'outputs/damage_attribution/audit_remaining_lineage_five_jobs_raw_anchors.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(out),'captures':[{'tag':x['tag'],'anchors':len(x['frames']),'actions':len(x['actions']),'exact_gaps':len(x['exact_gaps'])} for x in result]},ensure_ascii=False))

if __name__=='__main__':main()
