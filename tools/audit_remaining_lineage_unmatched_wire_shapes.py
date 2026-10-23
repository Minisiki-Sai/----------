"""Inspect residual baseline gaps after UDP offload splitting, never guess skills."""
from __future__ import annotations
import dataclasses,json,struct,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog,_load_damage_skill_ids
from tools.audit_remaining_lineage_direct_fields_v14_readonly import signature

def main():
    sig=signature();catalog=_load_skill_catalog(str(DATA_DIR.resolve()));damage=_load_damage_skill_ids(str(DATA_DIR.resolve()));actions=frozenset(catalog);reports=[]
    for tag in ('timelord','souleater','clerics'):
        baseline=json.loads((ROOT/f'outputs/damage_attribution/audit_remaining_lineage_five_jobs_v12_{tag}.json').read_text(encoding='utf8'))
        already=json.loads((ROOT/f'outputs/damage_attribution/audit_remaining_lineage_direct_fields_v14_{tag}.json').read_text(encoding='utf8'))
        matched={r['gap_index'] for r in already['gap_records']};gaps=[r for r in baseline['records'] if r['classification']!='deterministic'];byms=defaultdict(list)
        for i,r in enumerate(gaps):
            if i not in matched:byms[r['time_ms']].append((i,r))
        observations=[];frame_no=0;near_examples=[];found=set();source_mismatch=[]
        with Path(baseline['capture']).open('rb') as h:
            assert h.read(6)==b'DNDP1\0'
            while head:=h.read(17):
                if len(head)<17:break
                frame_no+=1;offset=h.tell()-17;t,adapter,original,n=struct.unpack('<dBII',head);data=h.read(n)
                if len(data)!=n:break
                ms=max(0,round((t-baseline['origin'])*1000))
                if ms not in byms:continue
                packet=p.decode_captured_frame(t,data,original)
                if packet is None or packet.transport.upper()!='UDP':continue
                suboff=0
                for raw in p.split_v576_payload(packet.payload):
                    at=suboff;suboff+=len(raw);decoded=p.decode_v576_payload(raw,packet.inferred_direction(),damage,actions,None)
                    for i,g in byms[ms]:
                        anchor={'gap_index':i,'old_skill_diagnostic_only':g['current_skill_id'],'job':g['job'],'frame':frame_no,'file_offset':offset,'time_ms':ms,'timestamp':t,'subpayload_offset':at,'source_expected':g['owner'],'target_expected':g['target'],'amount_expected':g['amount'],'payload_length':len(raw),'signature':raw[5:9].hex(),'direction':packet.inferred_direction(),'primary':dataclasses.asdict(decoded) if decoded else None}
                        if decoded and decoded.kind=='damage_hit' and decoded.damage==g['amount']:
                            anchor['payload_hex']=raw.hex();anchor['phases']={str(ph):p._decode_v576_body(raw,ph).hex() for ph in p._v576_candidate_xor_phases(raw)}
                            if decoded.source_actor_id==g['owner']:
                                observations.append(anchor);found.add(i)
                            else:source_mismatch.append(anchor)
                        elif len(near_examples)<12 and g['current_skill_id'] in {'2406','5214','5401'}:
                            near_examples.append(dict(anchor,payload_hex=raw.hex()))
        report={'tag':tag,'capture':baseline['capture'],'unmatched_before':sum(map(len,byms.values())),'primary_amount_source_matches':len(found),'raw_layouts':dict(Counter(r['primary']['layout'] for r in observations)),'observations':observations,'source_mismatch':source_mismatch,'unmatched_packet_examples':near_examples}
        reports.append(report);print(json.dumps({k:v for k,v in report.items() if k not in {'observations','source_mismatch','unmatched_packet_examples'}},ensure_ascii=False),flush=True)
    out=ROOT/'outputs/damage_attribution/audit_remaining_lineage_unmatched_wire_shapes_v14.json'
    out.write_text(json.dumps({'signature_start':sig,'signature_end':signature(),'runtime_unchanged':sig==signature(),'reports':reports},ensure_ascii=False,indent=2),encoding='utf8')
    print(out)

if __name__=='__main__':main()
