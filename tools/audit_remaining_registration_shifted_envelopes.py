"""Audit marker-less shifted registration records without attributing by time.

Uses existing missing-pair reports for impact and raw fixed direct37 slots for
truth. Structural anchors require repeated owner and repeated effect resource.
Runs raw replays serially; no production source is modified.
"""
from __future__ import annotations
import argparse
import bisect
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.audit_remaining_registration_server_variants import (
    DATA_DIR, V576DiagnosticReplaySource, _decode_v576_body,
    _v576_candidate_xor_phases, decode_v576_embedded_direct_damage_events,
    load_templates, u32, hashes,
)


def audit(report_path, templates):
    report = json.loads(report_path.read_text(encoding='utf-8'))
    path = Path(report['capture'])
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    skills = frozenset(int(s) for s in source._decoder.damage_skill_ids)
    records, hits = [], []
    packet_index = 0
    origin = None
    gaps = {(r['owner'], r['obj']): r for r in report['missing_pair_counts']}

    def feed(packet):
        nonlocal packet_index, origin
        packet_index += 1
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction())
        timestamp = float(packet.timestamp_seconds)
        if origin is None:
            origin = timestamp
        for hit in decode_v576_embedded_direct_damage_events(payload, None, skills):
            if int(hit.source_actor_id or 0) <= 0 or int(hit.damage_object_id or 0) <= 0:
                continue
            hits.append({'p': packet_index, 'm': int(hit.record_offset), 't': timestamp,
                         'owner': int(hit.source_actor_id), 'obj': int(hit.damage_object_id),
                         'skill': int(hit.skill_id) if hit.skill_id_exact else 0,
                         'damage': int(hit.damage or 0), 'direction': direction})
        # Original base record owner=10,length=107, or that exact tail with
        # +7/+10 envelope. Extended 112-byte base contains a candidate parent.
        for offset in (10, 17, 20):
            if len(payload) - offset not in (97, 102):
                continue
            seen = set()
            for phase in _v576_candidate_xor_phases(payload):
                b = _decode_v576_body(payload, phase)
                owner, obj, template = u32(b, offset), u32(b, offset+9), u32(b, offset+13)
                if owner < 0x01000000 or owner != u32(b, offset+45):
                    continue
                if b[offset+4:offset+9] != b'\0'*5 or not 0 < obj <= 0xffffff or template not in templates:
                    continue
                word = u32(b, offset+49)
                resource, sequence = word & 0xffff, word >> 16
                resource_repeat = u32(b, offset+65)
                parent = u32(b, offset+50)
                base = len(payload)-offset == 97 and resource > 0 and resource_repeat == resource
                extended = (len(payload)-offset == 102 and b[offset+49] == 0 and
                            0 < parent <= 0xffffff and parent != obj and
                            b[offset+58:offset+66] == b'\xff'*8)
                if not base and not extended:
                    continue
                key = (owner,obj,template)
                if key in seen:
                    continue
                seen.add(key)
                records.append({'p':packet_index,'t':timestamp, 'capture_seconds': round(timestamp-origin,6),
                                'owner':owner,'obj':obj,'template':template,'act':templates[template],
                                'offset':offset,'length':len(payload),'phase':int(phase),
                                'direction':direction,'signature':payload[5:9].hex(),
                                'kind':'repeated_resource' if base else 'extended_parent_candidate',
                                'resource':resource if base else None,'sequence':sequence if base else None,
                                'parent_candidate':parent if extended else None,
                                'normalized_tail_hex':b[offset:].hex(' '),
                                'decoded_payload_hex':b.hex(' '), 'wire_payload_hex':payload.hex(' ')})
        return ()

    source._decoder.feed_events = feed
    try:
        source.poll()
    finally:
        source.close()
    by_key=defaultdict(list)
    for r in records:
        by_key[(r['owner'],r['obj'])].append(r)
    hs_by_key=defaultdict(list)
    for h in hits:
        hs_by_key[(h['owner'],h['obj'])].append(h)
    # Identical tails under distinct wrappers are duplicates, not independent
    # lifecycle or skill anchors. Generation still must be fresh <=8s.
    instances=[]
    for key,rs in by_key.items():
        rs.sort(key=lambda r:r['p'])
        generations=[]
        for r in rs:
            if (generations and r['normalized_tail_hex']==generations[-1]['normalized_tail_hex']
                    and 0 <= r['t']-generations[-1]['t'] <= 0.25):
                generations[-1]['replicas'].append({k:r[k] for k in ['p','offset','length','phase','direction','signature']})
                continue
            generations.append({**r,'replicas':[]})
        positions=[r['p'] for r in generations]
        assigned=defaultdict(list)
        stale=Counter()
        for h in hs_by_key.get(key,()):
            idx=bisect.bisect_right(positions,h['p'])-1
            if idx<0:continue
            if not 0 <= h['t']-generations[idx]['t'] <= 8:
                stale[idx]+=1
                continue
            assigned[idx].append(h)
        for idx,r in enumerate(generations):
            active=assigned[idx]
            truth=Counter(h['skill'] for h in active if h['skill'])
            raw_gap=gaps.get(key)
            # Pair impact is an upper bound unless each missing event is
            # compared directly; retain the v2 exact event example for proof.
            example=raw_gap['example'] if raw_gap else None
            example_matches=bool(example and any(h['p']==example['p'] and h['damage']==example['damage'] for h in active))
            instances.append({**r,'fresh_raw_hit_rows':len(active),
                              'stale_raw_hit_rows_rejected':stale[idx],
                              'fixed_slot_skill_counts':dict(truth),
                              'fresh_hit_examples':active[:3],
                              'fixed_slot_examples':[h for h in active if h['skill']][:3],
                              'gap_pair_hits_upper_bound':raw_gap['hits'] if raw_gap else 0,
                              'gap_example_fresh_exact_match':example_matches,
                              'gap_example':example})
    grouped=defaultdict(list)
    for r in instances:grouped[(r['kind'],r['template'])].append(r)
    rows=[]
    for (kind,template),rs in grouped.items():
        truth=sum((Counter(r['fixed_slot_skill_counts']) for r in rs),Counter())
        if not any(r['fresh_raw_hit_rows'] for r in rs):continue
        rows.append({'kind':kind,'template':template,'act':templates[template],
                     'registrations':len(rs),'fresh_raw_hit_rows':sum(r['fresh_raw_hit_rows'] for r in rs),
                     'truth':dict(truth),'instance_conflicts':sum(len(r['fixed_slot_skill_counts'])>1 for r in rs),
                     'fresh_gap_pairs':len({(r['owner'],r['obj']) for r in rs if r['gap_example_fresh_exact_match']}),
                     'gap_hits_pair_upper_bound':sum(r['gap_pair_hits_upper_bound'] for r in rs if r['gap_example_fresh_exact_match']),
                     'offsets':dict(Counter(r['offset'] for r in rs)),
                     'directions':dict(Counter(r['direction'] for r in rs))})
    # Parent is only a candidate field until exact parent's independently
    # anchored identity and child provenance both agree. Never infer by age.
    parent_checks=[]
    for r in instances:
        if not r['parent_candidate']:continue
        parent_rs=[p for p in instances if p['owner']==r['owner'] and p['obj']==r['parent_candidate'] and p['p']<r['p'] and 0<=r['t']-p['t']<=8]
        parent_checks.append({'p':r['p'],'owner':r['owner'],'obj':r['obj'],'template':r['template'],
                              'parent_candidate':r['parent_candidate'],
                              'parent_records':[{'p':p['p'],'template':p['template'],'truth':p['fixed_slot_skill_counts']} for p in parent_rs],
                              'child_truth':r['fixed_slot_skill_counts']})
    return {'capture':str(path),'packets':packet_index,'raw_hits':len(hits),
            'layout_rows':rows,'instances':instances,'parent_checks':parent_checks,
            'source_impact_report':str(report_path),
            'policy':'fixed slots only; identical wrapper copies collapse; <=8s object freshness is a rejection guard, never action inference'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('reports',nargs='+',type=Path)
    p.add_argument('--output-dir',type=Path,default=ROOT/'outputs/audit_remaining_registration_shifted_20260907')
    p.add_argument('--resource-root',type=Path,default=Path(r'E:\ext0811.rar\Resource00\Resource00\resource'))
    a=p.parse_args();sys.stdout.reconfigure(encoding='utf-8');a.output_dir.mkdir(parents=True,exist_ok=True)
    templates=load_templates(a.resource_root)
    for path in a.reports:
        out=a.output_dir/path.name
        if out.exists():print('CACHED',path.stem,flush=True);continue
        before=hashes();print('START',path.stem,flush=True)
        d=audit(path,templates);d['core_hashes_before']=before;d['core_hashes_after']=hashes()
        out.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
        print('DONE',path.stem,'registrations',len(d['instances']), 'rows',d['layout_rows'],flush=True)


if __name__=='__main__':main()
