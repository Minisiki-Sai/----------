"""Streaming byte-only shadow replay. No scoring, UI, uploads, or CombatEvent.

Actor dispatch uses existing validated birth/owner/action observations, explicitly
reported as adapter evidence rather than independently proven native class state.
Raw damage rows are NOT accepted Hits. Only existing direct37/41 framed rows are
compared; other kind8 bodies (including health synchronization) stay opaque.
"""
import argparse
from collections import Counter, OrderedDict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.native_wire_state_v47 import NativeWireState
from tools.audit_native_damage_v46 import parse_record
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.entity_owner_evidence import EntityOwnerEvidenceLedger
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from dragon_nest_builder.live_dps.damage_object_origins import _SPEC_BY_PROJECTILE_TEMPLATE

HEADER=struct.Struct('<dBII')
OUT=ROOT/'outputs/native_wire_v47_20260909'


def run(path):
    started=time.perf_counter();info=path.stat();filehash=sha256()
    dependency_paths=[Path(__file__),ROOT/'tools/native_wire_state_v47.py',ROOT/'tools/audit_native_damage_v46.py',
        ROOT/'tools/audit_projectile_stream_v45.py',ROOT/'tools/trace_native_object_creation_v46.py',
        *[ROOT/'dragon_nest_builder/live_dps'/name for name in ('protocol_v576.py','udp_damage_records.py',
          'protocol_catalogs.py','entity_owner_evidence.py','damage_object_origins.py')]]
    dependencies={str(f):sha256(f.read_bytes()).hexdigest() for f in dependency_paths}
    state=NativeWireState(str(path))
    owners=EntityOwnerEvidenceLedger(max_entities=512)
    catalog=_load_skill_catalog(str(ROOT/'dragon_nest_builder/data'))
    knownskills=frozenset(catalog)
    count=Counter();kinds=Counter();failures=Counter();reasons=Counter();totals=[0,0]
    admitted_totals=[0,0];rejected_fields=Counter()
    statuses=Counter();configs=Counter();selectors=Counter();seenraw=OrderedDict()
    unique_totals=[0,0];unique_count=0;examples=[];mismatches=[];serial=0
    peakactors=peakobjects=peaktombs=0;lasttime=-float('inf')
    actor_facts=Counter();joinexamples=[]
    novel_objects=OrderedDict();novel_stats=Counter();novel_examples=[]
    novel_configs=Counter();novel_example_keys=Counter()
    contexts=OrderedDict();shared_history_filter=bytearray(1<<20)
    def peaks():
        nonlocal peakactors,peakobjects,peaktombs
        peakactors=max(peakactors,sum(len(c['state'].actors) for c in contexts.values()))
        peakobjects=max(peakobjects,sum(len(c['state'].objects) for c in contexts.values()))
        peaktombs=max(peaktombs,sum(len(c['state'].seen) for c in contexts.values()))
    with path.open('rb') as f:
        magic=f.read(6);filehash.update(magic)
        if magic!=b'DNDP1\0':raise ValueError('expected DNDP1 capture')
        while True:
            offset=f.tell();header=f.read(HEADER.size)
            if not header:break
            if len(header)!=HEADER.size:raise ValueError('truncated frame header')
            timestamp,adapter,original,length=HEADER.unpack(header)
            if length>16*1024*1024:raise ValueError('unexpected frame size')
            raw=f.read(length)
            if len(raw)!=length:raise ValueError('truncated frame')
            filehash.update(header);filehash.update(raw);count['physical_frames']+=1
            packet=p.decode_captured_frame(timestamp,raw,original)
            if packet is None or packet.transport!='UDP':
                count['not_udp']+=1;continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                serial+=1;count['udp_messages']+=1
                direction=packet.inferred_direction()
                if direction not in {'client_to_server','server_to_client'}:
                    count['direction_unknown']+=1;continue
                # Do not silently pool independent sockets. Endpoints are
                # normalized bidirectionally. Interleaved unrelated flows do
                # not erase each other; contexts are independently bounded.
                flow=tuple(sorted(((getattr(packet,'source_ip',getattr(packet,'src_ip','')),
                                    getattr(packet,'source_port',getattr(packet,'src_port',0))),
                                   (getattr(packet,'destination_ip',getattr(packet,'dst_ip','')),
                                    getattr(packet,'destination_port',getattr(packet,'dst_port',0))))))
                if flow not in contexts:
                    contexts[flow]=dict(state=NativeWireState(str(path)+':'+repr(flow),shared_history_filter=shared_history_filter),
                        owners=EntityOwnerEvidenceLedger(max_entities=512),novel=OrderedDict(),lasttime=-float('inf'),seenraw=OrderedDict())
                    count['flow_contexts_created']+=1
                    if len(contexts)>8:
                        contexts.popitem(last=False);count['flow_context_evictions']+=1
                contexts.move_to_end(flow)
                context=contexts[flow];state=context['state'];owners=context['owners'];novel_objects=context['novel']
                seenraw=context['seenraw']
                lasttime=context['lasttime']
                if timestamp<lasttime:
                    count['timestamp_regression_state_reset']+=1
                    state.reset_scene('clock-regression');state.last_time=-float('inf')
                    owners.reset();novel_objects.clear()
                lasttime=timestamp
                context['lasttime']=timestamp
                proof=sha256(wire).hexdigest()
                loc=dict(frame=count['physical_frames'],file_offset=offset,message=mi,time=timestamp,wire_sha256=proof)
                if direction=='server_to_client' and p.decode_v576_scene_transition_ack(wire,direction):
                    for ctx in contexts.values():
                        ctx['state'].reset_scene(proof);ctx['owners'].reset();ctx['novel'].clear()
                    count['scene_resets']+=1
                spawn=p.decode_v576_monster_spawn(wire,direction)
                if spawn and owners.observe_birth(spawn.actor_id,spawn.template_id,timestamp,proof,serial):
                    state.observe_actor(spawn.actor_id,'entity',(serial,0),timestamp,proof,template_id=spawn.template_id)
                    actor_facts['validated_server_entity_birth']+=1
                owners.observe_packet(wire,direction,timestamp,serial)
                actions=p.decode_v576_structured_actions(wire,direction,knownskills,
                            frozenset(state.actors),allow_unknown_actor_ids=True)
                # Legacy action API often omits offsets. Such actor context
                # becomes available only AFTER this message, never at byte0.
                pending=sorted(((a.record_offset if getattr(a,'record_offset',0)>0 else len(wire),a)
                                for a in actions),key=lambda pair:pair[0])
                def actions_through(bound):
                    while pending and pending[0][0]<=bound:
                        position,action=pending.pop(0);actor=action.source_actor_id
                        if actor and actor>=0x1000000 and actor not in state.actors:
                            state.observe_actor(actor,'player',(serial,position),timestamp,'action:'+proof,owner_id=actor)
                            actor_facts['existing_structured_action_adapter']+=1
                # Existing outer C2S framing locates stream; native prefix then reads it.
                if direction=='client_to_server':
                    reg=p.decode_v576_damage_object_registration(wire,direction)
                    if reg:
                        try:
                            actions_through(reg.record_offset)
                            plain=p._decode_v576_body(wire,reg.xor_phase)
                            obj=state.projectile_stream(reg.source_actor_id,plain[reg.record_offset+5:],
                                        (serial,reg.record_offset+1),timestamp,proof+':c2s')
                            count['selected_client_registration']+=1
                            if obj and (obj.reference!=reg.damage_object_id or obj.config_row!=reg.projectile_template_id):
                                count['client_prefix_differs_from_old_adapter']+=1
                        except ValueError as e:
                            failures['client:'+str(e)]+=1
                    actions_through(len(wire));peaks()
                    continue
                count['server_udp_messages']+=1
                inventory=inspect_udp_damage_records(wire,direction)
                if not inventory.complete:
                    reasons[inventory.rejection_reason]+=1;actions_through(len(wire));peaks();continue
                count['fully_framed_server_messages']+=1
                legacy_regs={(r.record_offset,r.source_actor_id,r.damage_object_id,r.projectile_template_id)
                    for r in p.decode_v576_server_damage_object_registrations(wire,direction,include_unmapped=True)} if any(
                        r.record_type in (9,56) for r in inventory.typed_records) else set()
                oldby={h.record_offset:h for h in inventory.damage_records}
                fresh=proof not in seenraw
                seenraw[proof]=None;seenraw.move_to_end(proof)
                if len(seenraw)>8192:seenraw.popitem(last=False)
                if not fresh:count['recent_exact_repeated_messages']+=1
                for row in inventory.typed_records:
                    kinds[f'{row.record_type:02x}/{row.declared_length}']+=1
                    actor=int.from_bytes(row.record_bytes[:4],'little');order=(serial,row.record_offset+1)
                    actions_through(row.record_offset)
                    if row.record_type in (9,56):
                        try:
                            obj=state.registration_record(row.record_bytes,order,timestamp,proof+':'+str(row.record_offset))
                            if obj:
                                count['native_registration_prefixes']+=1
                                selectors[f'{state.actors[actor].template_id}:{obj.action}:{obj.signal}:{obj.config_row}']+=1
                                if (obj.config_row is not None and obj.order==order and
                                        obj.proof==proof+':'+str(row.record_offset) and
                                        (row.record_offset,actor,obj.reference,obj.config_row) not in legacy_regs):
                                    novel_stats['additional_native_registration_prefixes']+=1
                                    novel_objects[obj.generation]=dict(**loc,marker=row.record_offset,record_hex=row.record_bytes.hex(),object=asdict(obj))
                                    while len(novel_objects)>4096:novel_objects.popitem(last=False)
                        except ValueError as e:
                            failures['server:'+str(e)]+=1
                    elif row.record_offset in oldby:
                        old=oldby[row.record_offset]
                        native=parse_record(row.record_bytes)
                        hit=state.damage_record(row.record_bytes,order,timestamp)
                        count['compared_physical_damage_records']+=1
                        # The old inventory preserves an unsigned uint64. Native
                        # uses signed int64; compare identical bits, not wraparound
                        # negatives as huge positive damage. Neither admits Hits.
                        amount_bits=native.damage_i64 & ((1<<64)-1)
                        totals[0]+=old.damage;totals[1]+=amount_bits
                        if old.admissible_fields:
                            count['numerically_admissible_damage_records']+=1
                            admitted_totals[0]+=old.damage;admitted_totals[1]+=native.damage_i64
                        else:
                            rejected_fields.update(old.rejection_reasons)
                        if native.damage_i64<0:count['native_signed_negative_rows']+=1
                        if fresh:
                            unique_count+=1;unique_totals[0]+=old.damage;unique_totals[1]+=amount_bits
                        statuses[hit.status]+=1
                        birth=owners.births.get(hit.source_id)
                        binding=owners.resolve(hit.source_id,birth[0],timestamp) if birth else None
                        if binding:count['explicit_entity_owner_at_damage']+=1
                        expectedref=old.damage_object_id if old.family=='direct37' else int.from_bytes(old.opaque_object_context,'little')
                        equal=(hit.source_id,hit.target_id,amount_bits,hit.critical,hit.reference)==(
                            old.source_actor_id,old.target_actor_id,old.damage,old.critical,expectedref)
                        if not equal:
                            count['physical_field_mismatches']+=1
                            if len(mismatches)<20:mismatches.append(dict(**loc,marker=row.record_offset,native=asdict(hit)))
                        if hit.status=='object_reference_resolved':
                            configs[str(hit.config_row)]+=1
                            count['root_context_known']+=int(hit.root_context_known)
                            if len(joinexamples)<20:joinexamples.append(dict(**loc,marker=row.record_offset,hit=asdict(hit),
                                  owner=binding.record.owner_id if binding else None))
                            if hit.object_generation in novel_objects and old.admissible_fields:
                                novel_stats['physical_joins_to_additional_prefixes']+=1
                                spec=_SPEC_BY_PROJECTILE_TEMPLATE.get(hit.config_row)
                                self_slot=dict(old.raw_skill_slots).get(102 if old.family=='direct37' else 106)
                                label='no_spec' if spec is None else ('agrees' if self_slot in spec.skill_ids else
                                    'missing_self_slot' if self_slot in (None,0,0xffffffff) else 'disagrees')
                                candidate_key=f'{hit.config_row}:{label}:root={hit.root_context_known}'
                                novel_configs[candidate_key]+=1
                                if spec:
                                    novel_stats['has_existing_config_spec']+=1
                                    if self_slot in spec.skill_ids:novel_stats['positive_self_slot_agrees_with_existing_spec']+=1
                                    elif self_slot not in (None,0,0xffffffff):novel_stats['self_slot_disagrees_with_existing_spec']+=1
                                if len(novel_examples)<192 and novel_example_keys[candidate_key]<3:
                                    novel_example_keys[candidate_key]+=1
                                    novel_examples.append(dict(registration=novel_objects[hit.object_generation],
                                        hit=dict(**loc,marker=row.record_offset,record_hex=row.record_bytes.hex(),fields=asdict(hit),raw_self_slot=self_slot),
                                        existing_spec=asdict(spec) if spec else None))
                        elif len(examples)<20:
                            examples.append(dict(**loc,marker=row.record_offset,hit=asdict(hit)))
                    elif row.record_type==8:
                        count['other_kind8_kept_opaque']+=1
                actions_through(len(wire));peaks()
            if count['physical_frames']%25000==0:
                print(json.dumps(dict(capture=path.name,frames=count['physical_frames'],damage=count['compared_physical_damage_records'],
                    resolved=statuses['object_reference_resolved'],elapsed=round(time.perf_counter()-started,2)),ensure_ascii=False),flush=True)
    after=path.stat()
    if (info.st_size,info.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('capture changed during read')
    if any(sha256(Path(f).read_bytes()).hexdigest()!=digest for f,digest in dependencies.items()):
        raise ValueError('reader source changed during shadow replay; result not publishable')
    return dict(schema='native-wire-shadow-replay-v47',capture=str(path),capture_sha256=filehash.hexdigest(),
        source_sha256=dependencies,
        bytes=info.st_size,counts=dict(count),elapsed_seconds=time.perf_counter()-started,
        raw_unsigned_bits_totals=totals,
        physical_amount_before=admitted_totals[0],physical_amount_after=admitted_totals[1],
        physical_fields_unchanged=not mismatches and totals[0]==totals[1] and admitted_totals[0]==admitted_totals[1],
        rejected_numeric_fields=dict(rejected_fields),
        recent_unique_message_control=dict(records=unique_count,totals=unique_totals,note='8192-message bounded exact-repeat control only; not game Hit deduplication'),
        framing_rejections=dict(reasons),native_reader_failures=dict(failures),record_kinds=dict(kinds),
        object_resolution_status=dict(statuses),resolved_configs=dict(configs),native_selectors=dict(selectors),
        actor_context_sources=dict(actor_facts),final_state=state.inventory_state(),
        connection_contexts=[c['state'].inventory_state() for c in contexts.values()],
        shared_replay_filter_bytes=len(shared_history_filter),
        additional_prefix_candidates=dict(counts=dict(novel_stats),by_config=dict(novel_configs),examples=novel_examples,
            note='Candidate diagnostic only. No completeness, accepted-Hit, same-skill or owner certification; all existing spec lineage/action gates still required.'),
        state_peaks=dict(actors=peakactors,objects=peakobjects,tombstones=peaktombs),
        mismatch_examples=mismatches,unresolved_examples=examples,resolved_examples=joinexamples,
        new_confirmed_skill_hits=0,production_changed=False,
        limitations=['Offline shadow only; physical records are not accepted player PvE Hits.',
          'Actor classes partly use the existing structured-action adapter, not a new native identity decoder.',
          'No TCP reassembly, inventory UID registration, native implicit creation, ACT lookup or suffix emulation.',
          'No exact same-owner skill label follows from a config row alone. Generic registrations with unknown context stay unresolved.',
          'Other type8 lengths, HP synchronization, incomplete messages and unknown directions are not silently included.'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('capture',type=Path);args=parser.parse_args()
    result=run(args.capture.resolve());OUT.mkdir(exist_ok=True,parents=True)
    result['script_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
    result['state_sha256']=sha256((ROOT/'tools/native_wire_state_v47.py').read_bytes()).hexdigest()
    target=OUT/(args.capture.stem+'_replay.json')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=lambda x:sorted(x) if isinstance(x,(set,frozenset)) else str(x)),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('capture','counts','object_resolution_status','physical_amount_before','physical_amount_after','physical_fields_unchanged','elapsed_seconds')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
