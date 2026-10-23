"""Audit fixed direct37/direct41 fields against existing gaps; no replay or edits.

Existing labels select/report gaps only and are never evidence for new mappings.
"""
from __future__ import annotations
import argparse,dataclasses,hashlib,json,struct,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog,_load_damage_skill_ids
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame,decode_v576_payload,_decode_v576_body,_v576_candidate_xor_phases,_is_valid_v576_damage_amount

SIGNATURE_PATHS=['dragon_nest_builder/live_dps/protocol_v576.py','dragon_nest_builder/live_dps/v576/core_decoder.py','dragon_nest_builder/live_dps/damage_object_origins.py','dragon_nest_builder/live_dps/generated_attack_instance_specs_v576.py']
def signature():return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SIGNATURE_PATHS}

def audit(tag):
    baseline=json.loads((ROOT/f'outputs/damage_attribution/audit_remaining_lineage_five_jobs_v12_{tag}.json').read_text(encoding='utf8'))
    catalog=_load_skill_catalog(str(DATA_DIR.resolve()));allskills=frozenset(catalog);damage=_load_damage_skill_ids(str(DATA_DIR.resolve()))
    owners=set(map(int,baseline['actor_jobs']));gaps=[r for r in baseline['records'] if r['classification']!='deterministic']
    keys=defaultdict(list)
    for i,r in enumerate(gaps):keys[(r['time_ms'],r['owner'],r['target'],r['amount'])].append(i)
    origin=baseline['origin']; rows=[];matched=set();direct41=Counter();layoutstats=Counter();slot=Counter();exact_by_skill=Counter();examples=[];seen=set();index=0;sig=signature()
    by_ms=defaultdict(list);other_primary=[]
    for i,r in enumerate(gaps):by_ms[r['time_ms']].append(i)
    with Path(baseline['capture']).open('rb') as h:
        assert h.read(6)==b'DNDP1\0'
        while header:=h.read(17):
            if len(header)<17:break
            index+=1;file_offset=h.tell()-17;t,adapter,original,length=struct.unpack('<dBII',header);data=h.read(length)
            if len(data)!=length:break
            p=decode_captured_frame(t,data,original)
            if p is None or p.transport.upper()!='UDP':continue
            ms=max(0,round((t-origin)*1000)); primary=None
            if ms in by_ms:
                primary=decode_v576_payload(p.payload,p.inferred_direction(),damage,allskills,owners)
                if primary and primary.kind=='damage_hit':
                    for i in by_ms[ms]:
                        g=gaps[i]
                        if g['owner']==primary.source_actor_id and g['amount']==primary.damage and g['target'] in {primary.target_actor_id,primary.alternate_target_actor_id}:
                            other_primary.append({'gap_index':i,'job':g['job'],'old_skill_id_diagnostic_only':g['current_skill_id'],'old_method':g['method'],'time_ms':ms,'timestamp':t,'frame':index,'file_offset':file_offset,'primary':dataclasses.asdict(primary),'payload_hex':p.payload.hex(),'decoded_phases':{str(ph):_decode_v576_body(p.payload,ph).hex() for ph in _v576_candidate_xor_phases(p.payload)}})
            for phase in _v576_candidate_xor_phases(p.payload):
                b=_decode_v576_body(p.payload,phase)
                all_markers=[]
                for magic,family,delta in [(b'\x08\x73\0','direct37',19),(b'\x08\x77\0','direct41',23)]:
                    at=b.find(magic,9)
                    while at>=0:
                        if at>=4 and at+delta+8<=len(b):
                            owner=int.from_bytes(b[at+8:at+12],'little');target=int.from_bytes(b[at-4:at],'little');amount=int.from_bytes(b[at+delta:at+delta+8],'little')
                            if owner in owners and 0<target<=0xffffff and _is_valid_v576_damage_amount(amount):all_markers.append((at,family,delta,owner,target,amount))
                        at=b.find(magic,at+1)
                all_markers.sort()
                for j,(at,family,delta,owner,target,amount) in enumerate(all_markers):
                    result_group=int.from_bytes(b[at+4:at+8],'little');obj=int.from_bytes(b[at+13:at+17],'little') if family=='direct37' else 0
                    # Preserve events sharing timestamp/amount by protocol result group.
                    identity=(t,owner,target,amount,family,result_group,obj,at)
                    if identity in seen:continue
                    seen.add(identity)
                    stop=all_markers[j+1][0]-4 if j+1<len(all_markers) else len(b)
                    fixed={str(rel):int.from_bytes(b[at+rel:at+rel+4],'little') for rel in ((102,114) if family=='direct37' else (106,)) if at+rel+4<=stop}
                    values={v for v in fixed.values() if v in damage};exact=next(iter(values)) if len(values)==1 else 0
                    # The record is structurally matched, not inferred from a current label.
                    matches=[i for i in keys.get((ms,owner,target,amount),[]) if family=='direct41' or gaps[i]['object_id']==obj]
                    if family=='direct41':
                        direct41['rows']+=1;direct41['fixed_skill_present' if exact else 'fixed_skill_absent']+=1
                        if exact:exact_by_skill[exact]+=1
                        for v in fixed.values():slot[str(v)]+=1
                    if not matches:continue
                    if primary is None:primary=decode_v576_payload(p.payload,p.inferred_direction(),damage,allskills,owners)
                    phit=primary is not None and primary.kind=='damage_hit' and primary.source_actor_id==owner and primary.damage==amount and primary.record_offset==at
                    layout=primary.layout if phit else 'primary_not_same_result'
                    layoutstats[(family,layout,'fixed_exact' if exact else 'no_fixed')]+=len(matches)
                    for i in matches:
                        matched.add(i)
                        fields={str(rel):int.from_bytes(b[at+rel:at+rel+4],'little') for rel in range(-4,141) if at+rel>=0 and at+rel+4<=stop}
                        rows.append({'gap_index':i,'job':gaps[i]['job'],'classification':gaps[i]['classification'],'old_skill_id_diagnostic_only':gaps[i]['current_skill_id'],'old_method':gaps[i]['method'],'time_ms':ms,'timestamp':t,'frame':index,'file_offset':file_offset,'family':family,'phase':phase,'record_offset':at,'record_bound':stop,'source':owner,'target':target,'damage':amount,'object_id':obj,'result_group':result_group,'fixed':fixed,'exact_skill':exact,'primary':dataclasses.asdict(primary) if primary else None,'primary_same_result':phit,'fields_u32':fields,'record_hex':b[max(0,at-4):min(stop,at+160)].hex(),'payload_hex':p.payload.hex() if exact or len(examples)<16 else None})
                    if len(examples)<16:examples.append(index)
    other_primary=[r for r in other_primary if r['gap_index'] not in matched]
    result={'capture':baseline['capture'],'tag':tag,'signature_start':sig,'signature_end':signature(),'signature_unchanged':sig==signature(),'baseline_gap_count':len(gaps),'baseline_gap_matched':len(matched),'baseline_gap_unmatched':len(gaps)-len(matched),'unmatched_by_job_skill':dict(Counter((f"{r['job']}|{r['current_skill_id']}") for i,r in enumerate(gaps) if i not in matched)),'raw_direct41_assigned_owners':dict(direct41),'direct41_exact_by_skill':dict(exact_by_skill),'direct41_slot_values':dict(slot),'gap_layouts':[{'family':k[0],'layout':k[1],'evidence':k[2],'hits':v} for k,v in layoutstats.most_common()],'gap_records':rows,'other_primary_records':other_primary,'other_primary_layouts':dict(Counter(r['primary']['layout'] for r in other_primary))}
    out=ROOT/f'outputs/damage_attribution/audit_remaining_lineage_direct_fields_v14_{tag}.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('gap_records','other_primary_records','signature_start','signature_end','direct41_slot_values','direct41_exact_by_skill','unmatched_by_job_skill')},ensure_ascii=False),flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('tags',nargs='+',choices=['timelord','souleater','darksummoner','clerics']);args=a.parse_args()
    for tag in args.tags:audit(tag)
