"""Offline inventory of native kind8 variable-length records; emits no Hits.

The class/read-plan candidates are explicitly hypotheses. Matching a suffix
length does not prove the source's runtime state, ownership or skill.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.audit_native_damage_v46 import parse_record
from tools.trace_native_damage_suffix_v48 import compatible_plans
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

HEADER=struct.Struct('<dBII')
OUT=ROOT/'outputs/native_wire_v47_20260909'


def inspect_record(record):
    """Physical common fields + compatible conditional plans, not attribution."""
    prefix=parse_record(record)
    plans=compatible_plans(record,'CDnActor')
    return dict(body_length=len(record)-7,target=int.from_bytes(record[:4],'little'),
        source_kind=prefix.source_kind,source=prefix.source_id,
        object_namespace='uid64' if prefix.uid_branch else 'index32',
        object_reference=prefix.weapon_uid if prefix.uid_branch else prefix.indexed_object_id,
        receiver_action=prefix.receiver_action_index,
        first_i64=prefix.damage_i64,second_i64=prefix.second_i64,
        result_flags=prefix.result_flags_i8,
        common_cc=record[4+prefix.field_offsets['common_15']],
        prefix_consumed=prefix.consumed,suffix_length=len(bytes.fromhex(prefix.suffix_raw)),
        plans=plans,receiver_class_assumption='One of five statically reviewed +290 no-op implementations; not established from this row.',
        skill_certified=False,hit_emitted=False)


def run(path):
    start=time.perf_counter();before=path.stat();file_hash=sha256()
    deps=[Path(__file__),ROOT/'tools/audit_native_damage_v46.py',
        ROOT/'tools/trace_native_damage_suffix_v48.py',
        ROOT/'dragon_nest_builder/live_dps/protocol_v576.py',
        ROOT/'dragon_nest_builder/live_dps/udp_damage_records.py']
    source_hashes={str(p):sha256(p.read_bytes()).hexdigest() for p in deps}
    counts=Counter();groups=Counter();failures=Counter();examples=[];example_counts=Counter()
    with path.open('rb') as stream:
        magic=stream.read(6);file_hash.update(magic)
        if magic!=b'DNDP1\0':raise ValueError('DNDP1 required')
        while head:=stream.read(HEADER.size):
            offset=stream.tell()-len(head)
            if len(head)!=HEADER.size:raise ValueError('short header')
            stamp,adapter,original,length=HEADER.unpack(head)
            if length>16*1024*1024:raise ValueError('oversized frame')
            blob=stream.read(length)
            if len(blob)!=length:raise ValueError('short frame')
            file_hash.update(head);file_hash.update(blob);counts['frames']+=1
            packet=protocol.decode_captured_frame(stamp,blob,original)
            if not packet or packet.transport!='UDP':continue
            direction=packet.inferred_direction()
            if direction!='server_to_client':continue
            for mi,wire in enumerate(protocol.split_v576_payload(packet.payload)):
                counts['server_messages']+=1
                inventory=inspect_udp_damage_records(wire,direction)
                if not inventory.complete:
                    counts['unframed_messages']+=1;continue
                for row in inventory.typed_records:
                    if row.record_type!=8:continue
                    counts['all_kind8']+=1
                    if row.declared_length in (115,119):
                        counts['legacy_direct_lengths']+=1;continue
                    counts['variable_kind8']+=1
                    try:parsed=inspect_record(row.record_bytes)
                    except ValueError as exc:
                        failures[str(exc)]+=1;continue
                    amount=parsed['first_i64'];sign='positive' if amount>0 else ('negative' if amount<0 else 'zero')
                    tier='below100000' if parsed['source']<100000 else 'at_least100000'
                    group=f"len={row.declared_length}/source_kind={parsed['source_kind']}/source={tier}/cc={parsed['common_cc']}/amount={sign}/plans={len(parsed['plans'])}"
                    groups[group]+=1;counts['compatible_suffix_rows']+=bool(parsed['plans'])
                    if amount>0:counts['positive_first_i64']+=1
                    if example_counts[group]<4 and len(examples)<80:
                        example_counts[group]+=1
                        examples.append(dict(frame=counts['frames'],file_offset=offset,message=mi,
                            time=stamp,marker=row.record_offset,wire_sha256=sha256(wire).hexdigest(),
                            record_sha256=sha256(row.record_bytes).hexdigest(),record_hex=row.record_bytes.hex(),
                            group=group,fields=parsed))
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('capture changed')
    if any(sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in source_hashes.items()):raise ValueError('reader changed')
    return dict(schema='variable-kind8-native-audit-v48',capture=str(path),capture_sha256=file_hash.hexdigest(),
        source_sha256=source_hashes,counts=dict(counts),groups=dict(groups),failures=dict(failures),examples=examples,
        elapsed_seconds=time.perf_counter()-start,production_changed=False,new_confirmed_hits=0,
        limitations=['Only exact top-level framed records, not marker search.',
            'Other kind8 is not automatically HP-only: first and second signed values retained independently.',
            'A compatible suffix plan is not a certified runtime predicate, source ownership, skill or PvE direction.',
            'No changes to existing health, damage, critical, score or aggregation output.'])


if __name__=='__main__':
    args=argparse.ArgumentParser();args.add_argument('captures',nargs='+');options=args.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    for name in options.captures:
        report=run(Path(name));target=OUT/(Path(name).stem+'_variable_kind8.json')
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(capture=name,counts=report['counts'],groups=report['groups'],failures=report['failures'],seconds=report['elapsed_seconds']),ensure_ascii=False),flush=True)
