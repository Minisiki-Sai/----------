"""Validate the second guard in an isolated function copy, using saved raw rows."""
from __future__ import annotations
import dataclasses,inspect,json,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.protocol_catalogs import _load_damage_skill_ids,_load_skill_catalog
from tools.audit_remaining_lineage_status_continuation_probe import limited_alias,signature,boundary

def main():
    start=signature();damage=_load_damage_skill_ids(str(DATA_DIR.resolve()));catalog=_load_skill_catalog(str(DATA_DIR.resolve()))
    source=inspect.getsource(p.decode_v576_compound_damage)
    old='if family == "direct37-status-continuation":'
    assert source.count(old)==1
    private=dict(vars(p));private['_direct_result_skill_id']=limited_alias
    exec(compile(source.replace(old,'if family == "direct37-status-continuation" and exact_skill_id is None:'),'<isolated-stage2-probe>','exec'),private)
    isolated=private['decode_v576_compound_damage']
    report=json.loads((ROOT/'outputs/damage_attribution/audit_remaining_lineage_status_continuation_probe_v14.json').read_text(encoding='utf8'))
    rows=[];counts=Counter();skills=Counter()
    for row in report['changed_records']:
        raw=bytes.fromhex(row['payload_hex'])
        # These selected records are confirmed server-side direct37 primaries.
        out=isolated(raw,'server_to_client',damage)
        assert out is not None
        result=dataclasses.asdict(out);before=row['old'];one=row['new']
        stable=all(before[k]==result[k] for k in ('kind','damage','critical','source_actor_id','target_actor_id','damage_object_id','damage_result_group_id','record_offset'))
        counts['rows']+=1;counts['core_fields_equal' if stable else 'CORE_FIELD_CHANGE']+=1
        expected=row['unbounded_exact']
        counts['exact_restored' if out.skill_id_exact and out.skill_id==expected else 'RESTORE_FAILURE']+=1
        skills[str(out.skill_id)]+=1
        rows.append({'capture':row['capture'],'frame':row['frame'],'file_offset':row['file_offset'],'timestamp':row['timestamp'],'subpayload_offset':row['subpayload_offset'],'record_offset':before['record_offset'],'source':before['source_actor_id'],'target':before['target_actor_id'],'amount':before['damage'],'old_skill_id':before['skill_id'],'family_only_skill_id':one['skill_id'],'family_only_exact':one['skill_id_exact'],'stage2_skill_id':out.skill_id,'stage2_name':catalog.get(out.skill_id,('',))[0],'stage2_exact':out.skill_id_exact,'core_fields_equal':stable,'fields':row['fields'],'boundary':row['boundary']})
    # Synthetic boundary tests are explicitly not capture-derived evidence.
    tests=[]
    b=bytearray(200);at=18;b[at:at+3]=b'\x08\x73\0';b[at+102:at+106]=(2651).to_bytes(4,'little')
    tests.append({'test':'complete_record','passed':limited_alias(bytes(b),damage,'direct37-status-continuation',at)==2651})
    nxt=110;b[nxt-4:nxt]=(55).to_bytes(4,'little');b[nxt:nxt+3]=b'\x08\x73\0';b[nxt+8:nxt+12]=(0x4e123456).to_bytes(4,'little');b[nxt+19:nxt+27]=(123456).to_bytes(8,'little')
    tests.append({'test':'fixed_slot_crosses_next_damage_record','boundary':boundary(bytes(b),at),'passed':limited_alias(bytes(b),damage,'direct37-status-continuation',at) is None})
    b=bytearray(200);b[at+102:at+106]=(2651).to_bytes(4,'little');nxt=110;b[nxt-4:nxt]=(55).to_bytes(4,'little');b[nxt:nxt+3]=b'\x13\x4a\0';b[nxt+3:nxt+7]=(0x4e123456).to_bytes(4,'little')
    tests.append({'test':'fixed_slot_crosses_next_status_record','boundary':boundary(bytes(b),at),'passed':limited_alias(bytes(b),damage,'direct37-status-continuation',at) is None})
    end=signature();out=ROOT/'outputs/damage_attribution/audit_remaining_lineage_status_continuation_stage2_v14.json'
    out.write_text(json.dumps({'policy':'Two independent diagnostic changes: family alias with bounded fixed slots; guard prevents clearing a proved exact slot. Private function namespace, no protocol monkeypatch or runtime writes.','signature_start':start,'signature_end':end,'runtime_unchanged':start==end,'stage1_scope':report['totals'],'counts':dict(counts),'skill_counts':dict(skills),'boundary_tests':tests,'records':rows},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(out),'counts':dict(counts),'skills':dict(skills),'tests':tests,'runtime_unchanged':start==end},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
