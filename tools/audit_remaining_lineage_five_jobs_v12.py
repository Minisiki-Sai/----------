"""Read-only, single-process remaining lineage audit for five professions.

This tool records protocol/static evidence; current skill buckets are diagnostic
labels only. It never promotes nearest actions or edits production mappings.
"""
from __future__ import annotations

import argparse
import bisect
import dataclasses
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from tools.audit_direct_damage_by_profession import _scope_reason, _strict_classification, _analysis_signature

JOBS = frozenset({'时空领主', '噬魂者', '黑暗萨满', '圣骑士', '十字军'})


def actor(value):
    try:
        text = str(value)
        if '0x' in text:
            return int(text[text.index('0x'):].split('@')[0].split(':')[0], 16)
        return int(value)
    except (ValueError, TypeError):
        return 0


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=lambda x: sorted(x) if isinstance(x, (set, frozenset)) else str(x))


def audit(path: Path):
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed_events
    original_registration = decoder.damage_object_origins.observe_registration
    accepted = defaultdict(list)
    candidates = defaultdict(list)
    wire = defaultdict(list)
    serial = 0
    current_bodies = {}
    original_signature = _analysis_signature()

    def observe_registration(**kwargs):
        result = original_registration(**kwargs)
        row = dict(kwargs)
        row['audit_packet_serial'] = serial
        off = int(kwargs.get('record_offset') or 0)
        row['origin'] = dataclasses.asdict(result) if result is not None else None
        owner = int(kwargs['source_actor_id'])
        obj = int(kwargs['damage_object_id'])
        for phase, body in current_bodies.items():
            if off >= 4 and int.from_bytes(body[off-4:off], 'little') == owner:
                row['phase'] = phase
                row['prefix_hex'] = body[max(0,off-4):off+64].hex(' ')
                break
        accepted[(owner, obj)].append(row)
        return result

    def feed(packet):
        nonlocal serial, current_bodies
        serial += 1
        payload = bytes(packet.payload)
        direction = packet.inferred_direction()
        timestamp = packet.timestamp_seconds
        current_bodies = {}
        if packet.transport.upper() == 'UDP':
            current_bodies = {ph: _decode_v576_body(payload, ph) for ph in _v576_candidate_xor_phases(payload)}
            seen = set()
            for phase, body in current_bodies.items():
                offset = body.find(b'\x09', 9)
                while offset >= 0:
                    if offset >= 4 and offset + 48 <= len(body) and body[offset+2:offset+8] == b'\0'*6:
                        owner = int.from_bytes(body[offset-4:offset], 'little')
                        obj = int.from_bytes(body[offset+8:offset+12], 'little')
                        template = int.from_bytes(body[offset+12:offset+16], 'little')
                        repeated = int.from_bytes(body[offset+44:offset+48], 'little')
                        key = (owner,obj,template,offset)
                        if owner > 0 and repeated == owner and 0 < obj <= 0x00FFFFFF and 0 < template <= 0x7fffffff and key not in seen:
                            seen.add(key)
                            candidates[(owner,obj)].append({'timestamp':timestamp,'packet_serial':serial,'offset':offset,'marker':body[offset:offset+2].hex(' '),'template':template,'phase':phase,'direction':direction,'prefix_hex':body[offset-4:offset+64].hex(' ')})
                    offset = body.find(b'\x09',offset+1)
            raw_hits = decode_v576_embedded_direct_damage_events(payload, direction, decoder.damage_skill_ids)
            for h in raw_hits:
                key = (timestamp,int(h.source_actor_id or 0),int(h.target_actor_id or 0),int(h.damage or 0),int(h.damage_object_id or 0),int(h.damage_result_group_id or 0))
                row = dataclasses.asdict(h)
                row['packet_serial'] = serial
                row['direction'] = direction
                row['timestamp'] = timestamp
                row['packet_length'] = len(payload)
                off = int(h.record_offset)
                for phase,body in current_bodies.items():
                    if body[off:off+3] == b'\x08\x73\0' and int.from_bytes(body[off+8:off+12],'little') == h.source_actor_id:
                        row['phase'] = phase
                        row['record_hex'] = body[max(0,off-4):off+132].hex(' ')
                        # Short field list preserves byte positions for follow-up.
                        row['prefix_u32'] = {str(p):int.from_bytes(body[off+p:off+p+4],'little') for p in (-4,4,8,13,27,31,36,40,44,48,52,56,60,64,68,72,76,80,84,88,92,96,100,102,106,110,114) if off+p>=0 and off+p+4<=len(body)}
                        break
                wire[key].append(row)
        return original_feed(packet)

    decoder.damage_object_origins.observe_registration = observe_registration
    decoder.feed_events = feed
    try:
        events = source.poll()
    finally:
        source.close()
    origin = float(decoder.first_timestamp or 0)
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    final = aggregator.snapshot(include_events=True).combat_events
    wire_relative = defaultdict(list)
    for k,rows in wire.items():
        wire_relative[(max(0,round((k[0]-origin)*1000)), *k[1:])].extend(rows)
    def previous(rows, ts, packet_serial, offset, accepted=False):
        # Actual stream order matters for registration/damage in one datagram.
        eligible = [r for r in rows if r['timestamp'] < ts or (r['timestamp'] == ts and (r.get('audit_packet_serial',r.get('packet_serial',0)) < packet_serial or (r.get('audit_packet_serial',r.get('packet_serial',0)) == packet_serial and r.get('record_offset',r.get('offset',0)) <= offset)))]
        return eligible[-1] if eligible else None
    summaries = defaultdict(Counter)
    groups = Counter()
    records = []
    actor_jobs = {}
    for e in final:
        if e.job not in JOBS or _scope_reason(e) != 'included_positive_direct_hit':
            continue
        cl = _strict_classification(e)
        owner=actor(e.source_id); obj=int(e.wire_damage_object_id or 0)
        actor_jobs[str(owner)] = e.job
        key=(int(e.timestamp_ms),owner,actor(e.target_id),int(e.amount),obj,int(e.wire_result_group_id or 0))
        raws = wire_relative.get(key,[])
        raw=raws.pop(0) if raws else None
        ts=raw['timestamp'] if raw else origin+e.timestamp_ms/1000
        ps=raw['packet_serial'] if raw else serial+1
        off=raw['record_offset'] if raw else 0xffffff
        candidate = previous(candidates.get((owner,obj),[]),ts,ps,off)
        registered = previous(accepted.get((owner,obj),[]),ts,ps,off)
        summaries[e.job][cl] += 1
        summaries[e.job]['damage'] += int(e.amount)
        groups[(e.job,cl,str(e.skill_id),str(e.skill_name),obj, candidate['marker'] if candidate else '', candidate['template'] if candidate else 0, bool(raw and raw['skill_id_exact']))] += 1
        if cl != 'deterministic' or (raw and raw['skill_id_exact']):
            records.append({'job':e.job,'classification':cl,'time_ms':e.timestamp_ms,'owner':owner,'target':actor(e.target_id),'amount':e.amount,'current_skill_id':str(e.skill_id),'current_skill_name':e.skill_name,'method':e.attribution_method,'raw_method':e.raw_attribution_method,'object_id':obj,'raw':raw,'candidate':candidate,'accepted':registered})
    target_owners = set(map(int,actor_jobs))
    return {'schema_version':1,'capture':str(path),'capture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'analysis_signature':original_signature,'signature_unchanged_during_replay':original_signature == _analysis_signature(),'origin':origin,'packet_count':serial,'actor_jobs':actor_jobs,'summary':dict(summaries),'groups':[dict(zip(('job','classification','current_skill_id','current_skill_name','object_id','marker','template','exact'),k),hits=v) for k,v in groups.most_common()],'records':records,'registrations':[dict(r,owner=owner,object_id=obj) for (owner,obj),rs in candidates.items() if owner in target_owners for r in rs], 'accepted_registrations':[dict(r,owner=owner,object_id=obj) for (owner,obj),rs in accepted.items() if owner in target_owners for r in rs]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('capture',type=Path)
    parser.add_argument('--tag',required=True)
    args=parser.parse_args()
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.tag):
        parser.error('tag must be ASCII alphanumeric/underscore/hyphen')
    result=audit(args.capture)
    output=ROOT/'outputs/damage_attribution'/f'audit_remaining_lineage_five_jobs_v12_{args.tag}.json'
    output.write_text(dump(result),encoding='utf-8')
    print(dump({'output':str(output),'summary':result['summary'],'records':len(result['records']),'signature_unchanged':result['signature_unchanged_during_replay']}),flush=True)


if __name__=='__main__':
    main()
