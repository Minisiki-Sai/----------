"""Single-process read-only family alias experiment on raw representative captures.

Monkeypatches one function only inside this diagnostic process, restoring it for
each before/after comparison. Never imports the scoring engine or writes runtime.
"""
from __future__ import annotations
import dataclasses,hashlib,json,struct,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog,_load_damage_skill_ids
from tools.audit_remaining_lineage_direct_fields_v14_readonly import signature

ORIGINAL=p._direct_result_skill_id
TARGET_FAMILY='direct37-status-continuation'

def boundary(body,at):
    # End at a validated following damage record, or conservatively at a
    # following status response. This never adds a link or searches by time.
    candidates=[]
    for marker,delta in [(b'\x08\x73\0',19),(b'\x08\x77\0',23)]:
        k=body.find(marker,at+36)
        while k>=0:
            if k>=4 and k+delta+8<=len(body):
                src=int.from_bytes(body[k+8:k+12],'little');dst=int.from_bytes(body[k-4:k],'little');amount=int.from_bytes(body[k+delta:k+delta+8],'little')
                if src>0 and dst>0 and p._is_valid_v576_damage_amount(amount):candidates.append(k-4)
            k=body.find(marker,k+1)
    for suffix in range(0x47,0x4f):
        marker=bytes([0x13,suffix,0]);k=body.find(marker,at+36)
        while k>=0:
            if k>=4 and k+10<=len(body) and int.from_bytes(body[k-4:k],'little')>0 and int.from_bytes(body[k+3:k+7],'little')>0:
                candidates.append(k-4)
            k=body.find(marker,k+1)
    return min(candidates,default=len(body))

def limited_alias(body,known,family,at):
    if family!=TARGET_FAMILY:return ORIGINAL(body,known,family,at)
    stop=boundary(body,at)
    return ORIGINAL(body[:stop],known,'direct37',at)

def one_decode(payload,direction,damage,actions,patched):
    p._direct_result_skill_id=limited_alias if patched else ORIGINAL
    try:return p.decode_v576_payload(payload,direction,damage,actions,None)
    finally:p._direct_result_skill_id=ORIGINAL

def asdict(value):return dataclasses.asdict(value) if value is not None else None

def inspect(payload,direction,damage,actions):
    old=one_decode(payload,direction,damage,actions,False)
    if old is None or TARGET_FAMILY not in old.layout:return None
    new=one_decode(payload,direction,damage,actions,True)
    anchor_body=None;phase_found=None
    at=int(old.record_offset or 0)
    for phase in p._v576_candidate_xor_phases(payload):
        b=p._decode_v576_body(payload,phase)
        if b[at:at+3]==b'\x08\x73\0' and int.from_bytes(b[at+8:at+12],'little')==old.source_actor_id and int.from_bytes(b[at+19:at+27],'little')==old.damage:
            anchor_body=b;phase_found=phase;break
    unbounded=ORIGINAL(anchor_body,damage,'direct37',at) if anchor_body else None
    stop=boundary(anchor_body,at) if anchor_body else None
    return {'old':asdict(old),'new':asdict(new),'changed':old!=new,'unbounded_exact':unbounded,'boundary':stop,'boundary_rejected':bool(unbounded and new and not new.skill_id_exact),'phase':phase_found,'fields':{str(rel):int.from_bytes(anchor_body[at+rel:at+rel+4],'little') for rel in (102,114) if anchor_body and at+rel+4<=len(anchor_body)},'decoded_hex':anchor_body.hex() if anchor_body else None}

def main():
    start=signature();catalog=_load_skill_catalog(str(DATA_DIR.resolve()));actions=frozenset(catalog);damage=_load_damage_skill_ids(str(DATA_DIR.resolve()))
    old_reports=json.loads((ROOT/'outputs/damage_attribution/direct41_deterministic_links_representative12_summary.json').read_text(encoding='utf8'))
    paths=[Path(x['path']) for x in old_reports['capture_reports']]
    anchor_audit=json.loads((ROOT/'outputs/damage_attribution/audit_remaining_lineage_five_jobs_raw_anchors.json').read_text(encoding='utf8'))
    anchors=[]
    for report in anchor_audit:
        for gap in report['exact_gaps']:
            f=next(f for f in report['frames'] if f['timestamp']==gap['raw']['timestamp'])
            result=inspect(bytes.fromhex(f['payload_hex']),f['direction'],damage,actions)
            anchors.append({'capture':report['capture'],'frame':f['frame_index'],'file_offset':f['file_offset'],'time_ms':gap['time_ms'],'old_bucket_diagnostic_only':gap['current_skill_id'],'result':result})
    reports=[];all_changes=[];all_boundary=[]
    for path in paths:
        counts=Counter();skills=Counter();changes=[];rejects=[];seen=set();frame_no=0
        with path.open('rb') as h:
            assert h.read(6)==b'DNDP1\0'
            while header:=h.read(17):
                if len(header)<17:break
                frame_no+=1;file_offset=h.tell()-17;t,adapter,original,n=struct.unpack('<dBII',header);data=h.read(n)
                if len(data)!=n:break
                packet=p.decode_captured_frame(t,data,original)
                if packet is None or packet.transport.upper()!='UDP':continue
                suboffset=0
                for payload in p.split_v576_payload(packet.payload):
                    part_offset=suboffset;suboffset+=len(payload)
                    if not p._v576_wire_may_contain_marker(payload,(b'\x08\x73\0',),start_offset=9):continue
                    result=inspect(payload,packet.inferred_direction(),damage,actions)
                    if result is None:continue
                    a=result['old'];identity=(t,a['source_actor_id'],a['target_actor_id'],a['damage'],a['damage_object_id'],a['damage_result_group_id'],a['record_offset'])
                    if identity in seen:counts['duplicate_result_key']+=1;continue
                    seen.add(identity);counts['target_family_primary_rows']+=1
                    if result['new'] and result['new']['skill_id_exact']:counts['patched_exact_rows']+=1
                    if result['changed']:
                        b=result['new'];stable=all(a[k]==b[k] for k in ('kind','damage','critical','source_actor_id','target_actor_id','damage_object_id','damage_result_group_id','record_offset'))
                        counts['changed_rows']+=1;counts['unchanged_core_fields' if stable else 'CORE_FIELD_CHANGE']+=1
                        skills[str(b['skill_id'])]+=1
                        result['core_fields_equal']=stable
                        result.update({'capture':path.name,'timestamp':t,'frame':frame_no,'file_offset':file_offset,'subpayload_offset':part_offset,'payload_hex':payload.hex()})
                        changes.append(result)
                    if result['boundary_rejected']:
                        counts['boundary_rejected']+=1
                        if len(rejects)<12:rejects.append(dict(result,capture=path.name,timestamp=t,frame=frame_no,file_offset=file_offset,subpayload_offset=part_offset))
        report={'capture':path.name,'counts':dict(counts),'new_exact_skill_counts':dict(skills),'source_size':path.stat().st_size}
        reports.append(report);all_changes.extend(changes);all_boundary.extend(rejects)
        print(json.dumps(report,ensure_ascii=False),flush=True)
    end=signature();out=ROOT/'outputs/damage_attribution/audit_remaining_lineage_status_continuation_probe_v14.json'
    totals=Counter()
    for r in reports:totals.update(r['counts'])
    out.write_text(json.dumps({'policy':'raw message comparison only; no runtime edits; exactly one family alias with next-result/status-response boundary; labels are diagnostic only','runtime_signature_start':start,'runtime_signature_end':end,'runtime_unchanged':start==end,'anchors':anchors,'capture_reports':reports,'totals':dict(totals),'changed_records':all_changes,'boundary_rejection_examples':all_boundary},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(out),'totals':dict(totals),'runtime_unchanged':start==end},ensure_ascii=False))

if __name__=='__main__':main()
