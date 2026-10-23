"""Inventory exact wire evidence at current unnamed-chain event exits.

Read-only hook. No attribution/admission changes, no inferred label used as
truth. Counts are BEFORE aggregation, not an accepted coverage increment.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from tools.audit_direct_damage_by_profession import _scope_reason,_strict_classification


def kind(obj):
    if obj is None:return 'none'
    if obj==0:return 'zero'
    if obj==0xffffffff:return 'sentinel_ffff'
    return 'numeric_object'


def main(path,origin_probe=False):
    source=V576DiagnosticReplaySource(path,ROOT/'dragon_nest_builder/data',{})
    decoder=source._decoder; original=decoder.feed_events
    counts=Counter();samples=defaultdict(list)
    serial=0;by_method=Counter()
    def observe(packet):
        nonlocal serial
        serial+=1
        events=original(packet)
        candidates=[e for e in events if isinstance(e,CombatEvent)
            and _scope_reason(e)=='included_positive_direct_hit'
            and _strict_classification(e)=='nondeterministic']
        if not candidates:return events
        inv=inspect_udp_damage_records(packet.payload,packet.inferred_direction()) if packet.transport=='UDP' else None
        for event in candidates:
            key=(event.wire_record_offset,event.wire_source_actor_id,event.wire_target_actor_id,
                 event.amount,event.critical,event.wire_result_group_id,event.wire_damage_object_id)
            matches=[h for h in inv.damage_records if h.physical_key==key] if inv and inv.complete else []
            # We also preserve positional matching to diagnose absent metadata;
            # this is not allowed to establish a skill or physical identity.
            positional=[h for h in inv.damage_records if h.record_offset==event.wire_record_offset] if inv and inv.complete else []
            slots=tuple(matches[0].raw_skill_slots) if len(matches)==1 else ()
            state=('exact_physical_match' if len(matches)==1 else
                'same_marker_mismatch' if positional else
                (inv.rejection_reason if inv and not inv.complete else 'not_in_current_packet'))
            origin=None; origin_state='not_requested'
            if origin_probe and 'direct37' in event.wire_damage_layout and kind(event.wire_damage_object_id)=='numeric_object':
                ledger=decoder.damage_object_origins
                origin,snapshot=ledger._origin_at((event.wire_source_actor_id,event.wire_damage_object_id),
                    packet.timestamp_seconds,decoder._damage_object_packet_serial,event.wire_record_offset)
                if origin is None:origin_state='no_registered_origin_at_hit'
                elif packet.timestamp_seconds>origin.expires_at:origin_state='expired_origin'
                else:origin_state='origin_present_other_guard'
            fields=(event.job,event.wire_damage_layout,kind(event.wire_damage_object_id),
                event.entity_template_id,state,slots,event.attribution_method,origin_state,
                origin.projectile_template_id if origin is not None else 0)
            counts[fields]+=1;by_method[event.attribution_method]+=1
            if len(samples[fields])<2:
                samples[fields].append(dict(serial=serial,time=packet.timestamp_seconds,transport=packet.transport,
                    direction=packet.inferred_direction(),wire=packet.payload.hex(),event=asdict(event),
                    origin=asdict(origin) if origin is not None else None,
                    positional=[dict(offset=h.record_offset,source=h.source_actor_id,target=h.target_actor_id,
                        damage=h.damage,critical=h.critical,group=h.damage_result_group_id,
                        object=h.damage_object_id,family=h.family,slots=h.raw_skill_slots)for h in positional]))
        return events
    decoder.feed_events=observe
    try:
        for _ in source.iter_batches():pass
    finally:source.close()
    rows=[dict(job=k[0],layout=k[1],object_kind=k[2],entity_template=k[3],wire_state=k[4],
        slots=k[5],method=k[6],origin_state=k[7],origin_template=k[8],count=n,examples=samples[k]) for k,n in counts.most_common()]
    result=dict(capture=str(path),capture_sha256=sha256(path.read_bytes()).hexdigest(),
        scope='current source exits PRE aggregation; diagnosis only, no promotions',
        methods=dict(by_method),rows=rows)
    out=ROOT/'outputs/named_gap_wire_v36_20260908';out.mkdir(exist_ok=True)
    (out/(path.stem+('_origins' if origin_probe else '')+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=list),encoding='utf-8')
    summary=Counter()
    for row in rows:summary[row['job'],row['object_kind'],row['wire_state']]+=row['count']
    print(json.dumps(dict(capture=path.name,groups=len(rows),summary=[dict(job=k[0],object_kind=k[1],
        wire_state=k[2],hits=n)for k,n in summary.most_common()]),ensure_ascii=False),flush=True)


if __name__=='__main__':main(Path(sys.argv[1]),'--origins' in sys.argv)
