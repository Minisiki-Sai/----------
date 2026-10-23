"""Explain every physical change; no coverage credit for unexplained loss.

A removed row is accepted only when an identical physical result already had
another correctly owned/targeted row, that exact row survives, and the complete
original typed wire independently proves the target. Time/amount/critical,
result group, full object/source, offset and real-hit flag never disappear.
"""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame, V576TransportReassembler
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.npcap import (
    IDENTITY_TRACE_ADAPTER_INDEX, IDENTITY_METADATA_ADAPTER_INDEX, SESSION_EVIDENCE_ADAPTER_INDEX)
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def origin(path):
    # Decoder zero is the FIRST ACCEPTED LOGICAL packet, not the first frame.
    # An initial incomplete TCP segment may be withheld by reassembly. The
    # evidence appendices intentionally never set battle zero either.
    transport, ledger = V576TransportReassembler(), V576EventLedger()
    with Path(path).open('rb') as f:
        assert f.read(6)==b'DNDP1\0'
        while h:=f.read(17):
            t,adapter,original,n=struct.unpack('<dBII',h)
            raw=f.read(n)
            if adapter in (IDENTITY_TRACE_ADAPTER_INDEX, IDENTITY_METADATA_ADAPTER_INDEX,
                           SESSION_EVIDENCE_ADAPTER_INDEX):
                continue
            packet=decode_captured_frame(t,raw,original)
            if packet is not None:
                for logical in transport.feed(packet):
                    if ledger.accept(logical):return logical.timestamp_seconds
    for logical in transport.flush():
        if ledger.accept(logical):return logical.timestamp_seconds
    raise AssertionError('no physical frame')


KEY=(0,3,4,5,6,7,9,10)
PHYSICAL=tuple(range(11))
def key(row):return tuple(row[i] for i in KEY)
def physical(row):return tuple(row[i] for i in PHYSICAL)


def compare(before,after):
    assert before['capture_path']==after['capture_path']
    assert before['capture_sha256']==after['capture_sha256']
    changed={k for k in before['runtime_py_hashes'].keys()|after['runtime_py_hashes'].keys()
        if before['runtime_py_hashes'].get(k)!=after['runtime_py_hashes'].get(k)}
    allowed={f'dragon_nest_builder/live_dps/{name}.py'for name in
        ('udp_damage_identity','autonomous_attack_families','duck_rune_self_attack_v28')}
    assert changed <= allowed, changed
    old_files,new_files=before['frozen_signature']['files'],after['frozen_signature']['files']
    for k in old_files.keys()|new_files.keys():
        if k.startswith('dragon_nest_builder/data/'):
            assert old_files.get(k)==new_files.get(k),('mixed data',k)
    t0=origin(after['capture_path'])
    witnesses=defaultdict(list)
    for w in after['target_correction_witnesses']:
        wire=bytes.fromhex(w['wire'])
        assert sha256(wire).hexdigest()==w['wire_sha256']
        inv=inspect_udp_damage_records(wire,'server_to_client')
        assert inv.complete and inv.xor_phase==w['phase']
        rows=[r for r in inv.damage_records if r.record_offset==w['marker']]
        assert len(rows)==1 and rows[0].admissible_fields
        r=rows[0];d=w['after']
        assert (r.target_actor_id,r.source_actor_id,r.damage,r.critical,r.damage_object_id,r.damage_result_group_id)==(
            d['target_actor_id'],d['source_actor_id'],d['damage'],d['critical'],d['damage_object_id'],d['damage_result_group_id'])
        k=(max(0,round((w['time']-t0)*1000)),r.damage,r.critical,r.damage_result_group_id,
            r.damage_object_id,r.source_actor_id,r.record_offset,True)
        witnesses[k].append((r.target_actor_id,w['wire_sha256']))
    report={'capture':after['capture'],'same_capture_bytes':True,
        'runtime_changes':sorted(changed),'data_identical':True,'checks':{}}
    for field in ('raw_physical_rows','aggregated_physical_rows'):
        old,new=defaultdict(list),defaultdict(list)
        for r in before[field]:old[key(r)].append(r)
        for r in after[field]:new[key(r)].append(r)
        unexplained=[];duplicates=[];corrections=[]
        for k in old.keys()|new.keys():
            a,b=old[k],new[k]
            if Counter(map(physical,a))==Counter(map(physical,b)):continue
            if len(a)==2 and len(b)==1:
                # Correct display owner AND target must already exist in old.
                good=[r for r in a if physical(r)==physical(b[0])]
                bad=[r for r in a if physical(r)!=physical(b[0])]
                proof=[p for p in witnesses[k]if p[0]==b[0][8]]
                if (len(good)==1 and len(bad)==1 and bad[0][7]==bad[0][8]
                        and b[0][8]!=b[0][7] and proof):
                    duplicates.append({'key':k,'removed':bad[0],'retained':b[0],
                        'wire_sha256':sorted({p[1]for p in proof})})
                    continue
            # A unique corrected target also requires an exact physical wire;
            # display owner cannot change in this separate, non-duplicate case.
            if len(a)==len(b)==1 and a[0][1]==b[0][1] and a[0][7]==a[0][8]:
                proof=[p for p in witnesses[k]if p[0]==b[0][8]]
                if proof and b[0][8]!=a[0][8]:
                    corrections.append({'key':k,'before':a[0],'after':b[0],
                        'wire_sha256':sorted({p[1]for p in proof})})
                    continue
            unexplained.append({'key':k,'before':a,'after':b,'wire_witnesses':witnesses[k]})
        removed=sum(r['removed'][3]for r in duplicates)
        report['checks'][field]={'before_rows':len(before[field]),'after_rows':len(after[field]),
            'unique_keys_before':len(old),'unique_keys_after':len(new),
            'unique_keys_identical':old.keys()==new.keys(),
            'duplicates_removed':len(duplicates),'duplicate_damage_removed':removed,
            'damage_change_percent':100*(sum(r[3]for r in after[field])-sum(r[3]for r in before[field]))/sum(r[3]for r in before[field]),
            'unique_target_corrections':len(corrections),'unexplained':unexplained,
            'duplicate_proofs':duplicates,'correction_proofs':corrections}
    report['all_changes_proven']=all(not r['unexplained']and r['unique_keys_identical']for r in report['checks'].values())
    report['profession_before']=before['profession_summary'];report['profession_after']=after['profession_summary']
    return report


if __name__=='__main__':
    paths=list(map(Path,sys.argv[1:]))
    result=compare(*[json.loads(p.read_text('utf-8'))for p in paths[:2]])
    paths[2].write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**{k:v for k,v in result.items()if k not in('checks','profession_before','profession_after')},
        'checks':{k:{f:v for f,v in r.items()if f not in('duplicate_proofs','correction_proofs','unexplained')}
            |{'unexplained_count':len(r['unexplained'])}for k,r in result['checks'].items()}},ensure_ascii=False))
    assert result['all_changes_proven'],'Unexplained physical changes; reject candidate.'
