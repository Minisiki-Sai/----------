"""Read-only S2C registration audit; independent fixed direct37 truth only.

One replay at a time. Does not monkey-patch attribution decisions or write any
production module. Packet numbers are 1-based decoder.feed_events calls; byte
offsets are zero-based in the XOR-decoded UDP payload, including its header.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from tools.audit_direct_damage_by_profession import _scope_reason, _strict_classification
from tools.audit_unknown_object_lifecycle_variants import _actor_id, _position_role
from tools.build_dungeon_map_catalog import parse_dnt

JOBS = {"魔羽", "雷神", "药剂师", "烈", "影", "暗"}
CORE_FILES = (
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/generated_attack_instance_specs_v576.py",
)


def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in CORE_FILES}


def u32(body, offset):
    return int.from_bytes(body[offset:offset + 4], "little") if 0 <= offset <= len(body) - 4 else 0


def occurrences(body, token, start=9):
    offset = body.find(token, start)
    while offset >= 0:
        yield offset
        offset = body.find(token, offset + 1)


def load_templates(resource):
    files = {int(r['RowId']): str(r.get('_FileName', '')) for r in parse_dnt(resource / 'ext/filetable.dnt')}
    return {int(r['RowId']): files.get(int(r.get('_ActName', 0) or 0), '')
            for r in parse_dnt(resource / 'ext/weapontable_projectile.dnt')}


def collect(path, templates, reverse_limit=80):
    started = time.monotonic()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    skills = frozenset(int(x) for x in decoder.damage_skill_ids)
    packet_index = 0
    hits = []
    records = []
    events = []
    exposed = defaultdict(list)
    original_registration = decoder.damage_object_origins.observe_registration
    directions = Counter()

    def observe_registration(**kw):
        exposed[(int(kw['source_actor_id']), int(kw['damage_object_id']))].append(
            (packet_index, float(kw['timestamp']), int(kw['projectile_template_id'])))
        return original_registration(**kw)

    decoder.damage_object_origins.observe_registration = observe_registration

    def feed(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction())
        directions[direction] += 1
        timestamp = float(packet.timestamp_seconds)
        seen = set()
        # Raw direct37 fixed result slots; no event attribution label is truth.
        if direction in ('server_to_client', 'client_to_server', 'None'):
            # Direction-less captures and accelerator relays are surveyed,
            # but their direction remains explicit in every truth record.
            for hit in decode_v576_embedded_direct_damage_events(payload, None, skills):
                owner, obj = int(hit.source_actor_id or 0), int(hit.damage_object_id or 0)
                if owner <= 0 or obj <= 0:
                    continue
                hits.append({'p': packet_index, 'm': int(hit.record_offset), 't': timestamp,
                             'owner': owner, 'obj': obj, 'damage': int(hit.damage or 0),
                             'skill': int(hit.skill_id) if hit.skill_id_exact else 0,
                             'direction': direction})
        # Survey every 09 xx 00, including unknown markers and failed repeats.
        # The latter are candidates only and never counted as proven layout.
        if direction in ('server_to_client', 'client_to_server', 'None'):
            for phase in _v576_candidate_xor_phases(payload):
                body = _decode_v576_body(payload, phase)
                for m in occurrences(body, b'\x09'):
                    if m < 4 or m + 16 > len(body) or body[m + 2] != 0:
                        continue
                    if body[m + 3:m + 8] != b'\0' * 5:
                        continue
                    owner, obj, template = u32(body, m - 4), u32(body, m + 8), u32(body, m + 12)
                    if owner < 0x01000000 or not 0 < obj <= 0xffffff:
                        continue
                    # Unknown template is retained only with an exact repeated owner.
                    repeat_ok = m + 48 <= len(body) and u32(body, m + 44) == owner
                    if template not in templates and not repeat_ok:
                        continue
                    key = (m, owner, obj, template)
                    if key in seen:
                        continue
                    seen.add(key)
                    records.append({'p': packet_index, 'm': m, 't': timestamp,
                                    'owner': owner, 'obj': obj, 'template': template,
                                    'marker': body[m:m + 3].hex(' ').upper(),
                                    'phase': int(phase), 'repeat_ok': repeat_ok, 'direction': direction,
                                    'repeat_offsets': [x - m for x in occurrences(body, owner.to_bytes(4, 'little'), max(9, m - 8)) if x < m + 100],
                                    'signature': payload[5:9].hex(), 'length': len(body),
                                    'context_start': max(0, m - 8),
                                    'context_hex': body[max(0, m - 8):m + 104].hex(' ')})
        emitted = original(packet)
        for event in emitted:
            if not isinstance(event, CombatEvent) or _scope_reason(event) != 'included_positive_direct_hit':
                continue
            if _strict_classification(event) == 'deterministic':
                continue
            obj = int(event.wire_damage_object_id or 0)
            owner = _actor_id(event.source_id)
            if obj <= 0 or owner <= 0:
                continue
            rows = exposed.get((owner, obj), ())
            registered = bool(rows and timestamp - rows[-1][1] <= 120)
            events.append({'p': packet_index, 'm': 1000000, 't': timestamp,
                           'owner': owner, 'obj': obj, 'damage': int(event.amount),
                           'job': str(event.job or ''), 'skill': str(event.skill_id),
                           'name': str(event.skill_name), 'method': str(event.attribution_method),
                           'registered': registered,
                           'production_template': rows[-1][2] if registered else None})
        return ()

    decoder.feed_events = feed
    try:
        source.poll()
        actor_jobs = {int(k): str(v[0]) for k, v in decoder.actor_jobs.items()}
    finally:
        source.close()
    for event in events:
        if event['job'] not in JOBS:
            event['job'] = actor_jobs.get(event['owner'], event['job'])
    gaps = [e for e in events if e['job'] in JOBS and not e['registered']]
    strict_by_key = defaultdict(list)
    relaxed_by_key = defaultdict(list)
    hits_by_key = defaultdict(list)
    gaps_by_key = defaultdict(list)
    for r in records:
        (strict_by_key if r['repeat_ok'] else relaxed_by_key)[(r['owner'], r['obj'])].append(r)
    for h in hits:
        hits_by_key[(h['owner'], h['obj'])].append(h)
    for g in gaps:
        gaps_by_key[(g['owner'], g['obj'])].append(g)
    instances = []
    matched_gap_ids = set()
    for key, rs in strict_by_key.items():
        rs.sort(key=lambda r: (r['p'], r['m']))
        positions = [(r['p'], r['m']) for r in rs]
        hs = hits_by_key.get(key, [])
        hpos = sorted((h['p'], h['m']) for h in hs)
        buckets = defaultdict(list)
        for h in hs:
            idx = bisect.bisect_right(positions, (h['p'], h['m'])) - 1
            if idx >= 0:
                buckets[idx].append(h)
        gap_buckets = defaultdict(list)
        for g in gaps_by_key.get(key, []):
            # Require an exact packet/owner/object/damage direct37 match to
            # count affected Hit; the old guessed skill never votes.
            exact_rows = [h for h in hs if h['p'] == g['p'] and h['damage'] == g['damage']]
            indices = {bisect.bisect_right(positions, (h['p'], h['m'])) - 1 for h in exact_rows}
            if len(indices) != 1 or next(iter(indices)) < 0:
                continue
            idx = next(iter(indices))
            gap_buckets[idx].append(g)
            matched_gap_ids.add(id(g))
        for idx, r in enumerate(rs):
            active = buckets[idx]
            gs = gap_buckets[idx]
            if not active and not gs:
                continue
            anchors = [h for h in active if h['skill']]
            counts = Counter(h['skill'] for h in anchors)
            same_key_skills = Counter(h['skill'] for h in hs if h['skill'])
            instances.append({**r, 'act': templates.get(r['template'], ''),
                              'sequence_role': _position_role((r['p'], r['m']), hpos),
                              'hit_rows': len(active), 'fixed_slot_anchors': dict(counts),
                              'instance_conflict': len(counts) > 1,
                              'whole_pair_skills': dict(same_key_skills),
                              'whole_pair_multiple_templates': len({x['template'] for x in rs}) > 1,
                              'gap_hits': len(gs), 'gap_damage': sum(g['damage'] for g in gs),
                              'max_gap_age_ms': max((round((g['t'] - r['t']) * 1000) for g in gs), default=0),
                              'gap_jobs': dict(Counter(g['job'] for g in gs)),
                              'gap_current_skills_not_truth': dict(Counter(g['name'] for g in gs)),
                              'gap_examples': gs[:3], 'anchor_examples': anchors[:3]})
    # Keep each unresolved pair only once. Reverse scan uses full owner+object,
    # never an object-only cross-player lookup or nearest action.
    missing = [g for g in gaps if id(g) not in matched_gap_ids]
    reverse_keys = []
    for job in sorted(JOBS):
        candidates = Counter((g['owner'], g['obj']) for g in missing if g['job'] == job)
        reverse_keys.extend(key for key, _ in candidates.most_common(reverse_limit))
    reverse = reverse_scan(path, set(reverse_keys), templates) if reverse_keys else []
    relaxed = []
    for key in set(gaps_by_key) | {k for k, hs in hits_by_key.items() if any(h['skill'] for h in hs)}:
        for r in relaxed_by_key.get(key, ()):
            later = [h for h in hits_by_key.get(key, ()) if h['p'] >= r['p']]
            relaxed.append({**r, 'act': templates.get(r['template'], ''),
                            'later_fixed_skill_ids': dict(Counter(h['skill'] for h in later if h['skill'])),
                            'gap_hits_for_pair_not_causal': len(gaps_by_key.get(key, ()))})
    return {'capture': str(path), 'capture_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'seconds': round(time.monotonic() - started, 3), 'packets': packet_index,
            'directions': dict(directions), 'actor_jobs': actor_jobs,
            'direct37_rows': len(hits), 'fixed_slot_anchor_rows': sum(bool(h['skill']) for h in hits),
            'gap_jobs': dict(Counter(g['job'] for g in gaps)),
            'gap_hits': len(gaps), 'strict_layout_gap_hits': len(matched_gap_ids),
            'strict_instances': instances, 'relaxed_records': relaxed,
            'reverse_probe_pairs': len(reverse_keys), 'reverse_rows': reverse,
            'unmatched_gap_examples': missing[:100],
            'missing_pair_counts': [{'owner': k[0], 'obj': k[1], 'hits': len(gs),
                                     'jobs': dict(Counter(g['job'] for g in gs)),
                                     'example': gs[0]}
                                    for k, gs in gaps_by_key.items() if any(id(g) not in matched_gap_ids for g in gs)]}


def reverse_scan(path, keys, templates):
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    by_owner = defaultdict(set)
    for owner, obj in keys:
        by_owner[owner].add(obj)
    pattern = re.compile(b'|'.join(re.escape(x.to_bytes(4, 'little')) for x in by_owner))
    rows = []
    seen_pairs = Counter()
    packet_index = 0

    def feed(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        if str(packet.inferred_direction()) not in ('server_to_client', 'client_to_server', 'None'):
            return ()
        seen = set()
        for phase in _v576_candidate_xor_phases(payload):
            body = _decode_v576_body(payload, phase)
            for match in pattern.finditer(body, 9):
                pos = match.start()
                owner = int.from_bytes(match.group(), 'little')
                for obj in by_owner[owner]:
                    token = obj.to_bytes(4, 'little')
                    start, stop = max(9, pos - 96), min(len(body), pos + 132)
                    for op in occurrences(body, token, start):
                        if op >= stop:
                            break
                        # Discard direct37 owner/object control; retain other
                        # records even in packets containing damage elsewhere.
                        if op == pos + 5 and pos >= 8 and body[pos-8:pos-5] == b'\x08\x73\0':
                            continue
                        markers = []
                        for m in range(max(9, min(pos, op)-8), min(len(body)-2, max(pos, op)+8)):
                            if body[m] in (8, 9, 14, 18, 19) and body[m + 2] == 0:
                                markers.append({'marker': body[m:m+3].hex(' ').upper(),
                                                'm': m, 'owner_rel': pos-m, 'obj_rel': op-m})
                        ident = (owner, obj, pos, op)
                        if ident in seen or seen_pairs[(owner, obj)] >= 40:
                            continue
                        seen.add(ident)
                        seen_pairs[(owner, obj)] += 1
                        template_fields = [(x-pos, u32(body,x)) for x in range(max(9,min(pos,op)-8), min(len(body)-3,max(pos,op)+48)) if u32(body,x) in templates]
                        rows.append({'p': packet_index, 't': float(packet.timestamp_seconds),
                                     'direction': str(packet.inferred_direction()),
                                     'phase': int(phase), 'signature': payload[5:9].hex(),
                                     'length': len(body), 'owner': owner, 'obj': obj,
                                     'owner_offset': pos, 'obj_offset': op,
                                     'markers': markers, 'template_fields_from_owner': template_fields,
                                     'context_start': start, 'context_hex': body[start:stop].hex(' ')})
        return ()

    source._decoder.feed_events = feed
    try:
        source.poll()
    finally:
        source.close()
    return rows


def aggregate(reports, prior_path):
    groups = defaultdict(list)
    for report in reports:
        for r in report['strict_instances']:
            groups[(r['marker'], r['template'])].append((report, r))
    prior = json.loads(prior_path.read_text(encoding='utf-8')) if prior_path.exists() else {}
    old = {(r['marker'] + ' 00', r['projectile_template_id']): r
           for r in prior.get('aggregate', {}).get('candidate_template_packet_skill_validation', [])}
    result = []
    for (marker, template), values in groups.items():
        truth = Counter()
        for _, r in values:
            truth.update({int(k): v for k, v in r['fixed_slot_anchors'].items()})
        if not truth and not any(r['gap_hits'] for _, r in values):
            continue
        anchor_captures = {rep['capture_sha256'] for rep, r in values if r['fixed_slot_anchors']}
        actors = {(rep['capture_sha256'], r['owner']) for rep, r in values if r['fixed_slot_anchors']}
        conflicts = sum(r['instance_conflict'] for _, r in values)
        prior_row = old.get((marker, template), {})
        prior_truth = prior_row.get('packet_skill_ids', {})
        different_prior = set(map(int, prior_truth)) - set(truth)
        if len(truth) > 1 or conflicts or len(prior_truth) > 1 or (truth and different_prior):
            verdict = 'reject_fixed_slot_conflict'
        elif not truth:
            verdict = 'unproven_no_independent_fixed_slot_anchor'
        elif sum(truth.values()) < 30 or len(anchor_captures) < 2 or len(actors) < 2:
            verdict = 'insufficient_independent_capture_support'
        else:
            verdict = 'fixed_slot_consistent_requires_static_resource_review'
        result.append({'marker': marker, 'template': template, 'act': values[0][1]['act'],
                       'verdict': verdict, 'fixed_slot_truth': dict(truth),
                       'fixed_slot_anchor_rows': sum(truth.values()),
                       'anchor_captures': len(anchor_captures), 'anchor_capture_actors': len(actors),
                       'conflicting_instances': conflicts, 'prior_205_truth': prior_truth,
                       'prior_205_instance_conflicts': prior_row.get('instance_conflicts', 0),
                       'active_raw_hits': sum(r['hit_rows'] for _, r in values),
                       'registration_directions': dict(Counter(r['direction'] for _, r in values)),
                       'gap_hits': sum(r['gap_hits'] for _, r in values),
                       'gap_jobs': dict(sum((Counter(r['gap_jobs']) for _, r in values), Counter())),
                       'gap_current_skills_not_truth': dict(sum((Counter(r['gap_current_skills_not_truth']) for _, r in values), Counter())),
                       'representative_gap_records': [{**r, 'capture': rep['capture']} for rep, r in values if r['gap_hits']][:5],
                       'representative_anchor_records': [{**r, 'capture': rep['capture']} for rep, r in values if r['fixed_slot_anchors']][:3]})
    result.sort(key=lambda r: (-r['gap_hits'], -r['fixed_slot_anchor_rows']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captures', nargs='*', type=Path)
    parser.add_argument('--resource-root', type=Path, default=Path(r'E:\ext0811.rar\Resource00\Resource00\resource'))
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs/audit_remaining_registration_server_20260907_v2')
    parser.add_argument('--summarize-only', action='store_true')
    parser.add_argument('--reverse-limit', type=int, default=40)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    before = hashes()
    templates = load_templates(args.resource_root) if not args.summarize_only else {}
    for path in args.captures:
        path = path.resolve()
        output = args.output_dir / (path.stem + '.json')
        if output.exists():
            print('CACHED', path.name, flush=True)
            continue
        print('START', path.name, flush=True)
        report = collect(path, templates, args.reverse_limit)
        report['core_hashes_before'] = before
        report['core_hashes_after'] = hashes()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('DONE', path.name, report['seconds'], 'gap', report['gap_hits'],
              'strict', report['strict_layout_gap_hits'], 'reverse', len(report['reverse_rows']), flush=True)
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in args.output_dir.glob('*.json') if p.name != 'summary.json']
    summary = {'policy': 'Only same capture owner+object and ordered lifecycle; fixed direct37 slots vote. Current names do not vote.',
               'captures': len(reports), 'gap_jobs': dict(sum((Counter(r['gap_jobs']) for r in reports), Counter())),
               'strict_layout_gap_hits': sum(r['strict_layout_gap_hits'] for r in reports),
               'candidate_mappings': aggregate(reports, ROOT / 'outputs/damage_attribution/unknown_object_lifecycle_variants_full_20260907.json'),
               'core_hashes_before': before, 'core_hashes_after': hashes()}
    (args.output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('SUMMARY', summary['captures'], summary['gap_jobs'], 'strict', summary['strict_layout_gap_hits'], flush=True)


if __name__ == '__main__':
    main()
