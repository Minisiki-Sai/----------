"""Raw 12-capture validation of short direct37 parser field omissions."""
from __future__ import annotations
import dataclasses,json,struct,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.protocol_catalogs import _load_damage_skill_ids,_load_skill_catalog
from tools.audit_remaining_lineage_status_continuation_probe import boundary,signature

def main():
    start=signature();damage=_load_damage_skill_ids(str(DATA_DIR.resolve()));catalog=_load_skill_catalog(str(DATA_DIR.resolve()))
    paths=[Path(x['path']) for x in json.loads((ROOT/'outputs/damage_attribution/direct41_deterministic_links_representative12_summary.json').read_text(encoding='utf8'))['capture_reports']]
    reports=[];anchors=[];changes=[]
    for path in paths:
        counts=Counter();skills=Counter();seen=set();frame=0
        with path.open('rb') as h:
            assert h.read(6)==b'DNDP1\0'
            while header:=h.read(17):
                if len(header)<17:break
                frame+=1;fileoffset=h.tell()-17;t,adapter,original,n=struct.unpack('<dBII',header);data=h.read(n)
                if len(data)!=n:break
                packet=p.decode_captured_frame(t,data,original)
                if packet is None or packet.transport.upper()!='UDP':continue
                suboff=0
                for raw in p.split_v576_payload(packet.payload):
                    so=suboff;suboff+=len(raw)
                    if (len(raw),raw[5:9]) not in {(148,b'\x92\x30\x91\0'),(171,b'\x28\x4a\xa8\0')}:continue
                    before=p.decode_v576_projectile_damage(raw,packet.inferred_direction())
                    if before is None:continue
                    phase=8 if len(raw)==148 else 10;b=p._decode_v576_body(raw,phase);at=before.record_offset
                    src=before.source_actor_id;dst=int.from_bytes(b[at-4:at],'little');obj=int.from_bytes(b[at+13:at+17],'little');group=int.from_bytes(b[at+4:at+8],'little')
                    key=(t,src,dst,group,obj,before.damage,at)
                    if key in seen:counts['duplicates']+=1;continue
                    seen.add(key);counts['short_primary_rows']+=1;counts[f'length_{len(raw)}']+=1
                    stop=boundary(b,at);fixed=p._direct_result_skill_id(b[:stop],damage,'direct37',at)
                    embedded=[r for r in p.decode_v576_embedded_direct_damage_events(raw,packet.inferred_direction(),damage) if r.record_offset==at and r.source_actor_id==src and r.damage==before.damage]
                    structural=0<dst<=0xffffff and dst!=src and 0<obj<=0xffffff
                    counts['valid_object_target_structure' if structural else 'STRUCTURAL_FAILURE']+=1
                    agreement=len(embedded)==1 and embedded[0].target_actor_id==dst and embedded[0].damage_object_id==obj and embedded[0].damage_result_group_id==group
                    counts['embedded_object_target_agreement' if agreement else 'EMBEDDED_STRUCTURE_MISMATCH']+=1
                    if fixed:
                        counts['fixed_skill_omitted']+=1;skills[str(fixed)]+=1
                        exact_agreement=len(embedded)==1 and embedded[0].skill_id_exact and embedded[0].skill_id==fixed
                        counts['embedded_exact_agreement' if exact_agreement else 'EMBEDDED_EXACT_MISMATCH']+=1
                        after=dataclasses.replace(before,target_actor_id=dst,damage_object_id=obj,damage_result_group_id=group,skill_id=fixed,skill_id_exact=True)
                        stable=all(getattr(before,k)==getattr(after,k) for k in ('damage','critical','source_actor_id','record_offset'))
                        counts['damage_crit_source_unchanged' if stable else 'CORE_CHANGE']+=1
                        row={'capture':path.name,'timestamp':t,'frame':frame,'file_offset':fileoffset,'subpayload_offset':so,'record_offset':at,'payload_length':len(raw),'source':src,'wire_target':dst,'old_target_result_group':before.target_actor_id,'object_id':obj,'result_group':group,'damage':before.damage,'fixed_skill':fixed,'skill_name':catalog.get(fixed,('',))[0],'fields':{str(rel):int.from_bytes(b[at+rel:at+rel+4],'little') for rel in (102,114) if at+rel+4<=len(b)},'boundary':stop,'embedded_agreement':exact_agreement,'payload_hex':raw.hex(),'decoded_hex':b.hex()}
                        changes.append(row)
                        if len(anchors)<20:anchors.append(row)
        report={'capture':path.name,'counts':dict(counts),'fixed_skill_counts':dict(skills)};reports.append(report);print(json.dumps(report,ensure_ascii=False),flush=True)
    total=Counter();skills=Counter()
    for r in reports:total.update(r['counts']);skills.update(r['fixed_skill_counts'])
    out=ROOT/'outputs/damage_attribution/audit_remaining_lineage_projectile_short_fields_v14.json'
    out.write_text(json.dumps({'policy':'Private dataclass comparison; raw positional fields only; no temporal filling; no production replay change. Object ledger already has raw object-ID fallback, so metadata restoration is not itself a coverage gain.','signature_start':start,'signature_end':signature(),'runtime_unchanged':start==signature(),'capture_reports':reports,'totals':dict(total),'skill_counts':dict(skills),'fixed_omission_records':changes},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(out),'totals':dict(total),'skills':dict(skills),'runtime_unchanged':start==signature()},ensure_ascii=False))

if __name__=='__main__':main()
