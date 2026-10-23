"""One read-only DNDP1 scan; no combat replay, scoring, or runtime mutation.

Writes only this audit's new output directory. Fixed slots are observations,
never the truth used to select a parent or an action epoch.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import struct
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from dragon_nest_builder.live_dps.protocol_v576 import (
    _V576_BODY_XOR_KEY, _decode_v576_body_uncached,
    _v576_wire_marker_pattern, decode_captured_frame, split_v576_payload,
    decode_v576_damage_object_registration,
    decode_v576_embedded_direct_damage_events, decode_v576_payload,
)
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog

OUT = ROOT / 'outputs/audit_remaining_registration_3021_context_20260907'
CACHE = ROOT / 'outputs/audit_remaining_registration_exact_parent_20260907_v3/0905黑龙经典冰灵.json'
CAP = Path(r'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics\0905黑龙经典冰灵.dpscap')
OWNER = 1337770664
CORE = ('protocol_v576.py', 'damage_object_origins.py', 'v576/core_decoder.py',
        'generated_attack_instance_specs_v576.py')
HEADER = struct.Struct('<dBII')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes():
    return {p: sha(ROOT / 'dragon_nest_builder/live_dps' / p) for p in CORE}


def u32(b, p):
    return int.from_bytes(b[p:p+4], 'little') if 0 <= p <= len(b)-4 else 0


def hit_key(h):
    return (h['t'], h['obj'], h['target'], h['result_group'], h['damage'])


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if OUT.exists():
        raise SystemExit('Refusing to overwrite existing audit directory')
    before = hashes()
    cached = json.loads(CACHE.read_text(encoding='utf-8'))
    assert {g['scene'] for g in cached['generations']} == {0}
    origin = cached['origin']
    selected = []
    for g in cached['generations']:
        if g['template'] == 3021:
            for h in g['fixed_examples']:
                if h['fixed_skill'] == 2207:
                    selected.append({**h, 'cached_child_generation_id': g['id'],
                                     'cached_parent_binding': g['parent_binding']})
    assert len(selected) == 67
    wanted = {hit_key(h): h for h in selected}
    times = sorted({h['t'] for h in selected})
    ob = struct.pack('<I', OWNER)
    owner_pattern = _v576_wire_marker_pattern((ob,))
    key_starts = {
        bytes(v ^ _V576_BODY_XOR_KEY[(k+i) % 13] for i, v in enumerate(ob)): k
        for k in range(13)
    }
    skills = _load_skill_catalog(str(ROOT / 'dragon_nest_builder/data'))
    actions, generations, matches = [], [], []
    slots, recent = {}, {}
    packets, selected_packets = {}, {}
    counters = Counter()
    generation_counters = Counter()
    capture_digest = hashlib.sha256()
    last_progress = started = time.monotonic()

    def fixture(meta, wire, phase):
        digest = hashlib.sha256(wire).hexdigest()
        key = f"f{meta['frame']}_m{meta['message']}_p{phase}"
        if key not in packets:
            packets[key] = {**meta, 'phase': phase, 'payload_sha256': digest,
                            'payload_hex': wire.hex(),
                            'decoded_hex': _decode_v576_body_uncached(wire, phase).hex()}
        return key

    def register(row):
        obj, t = row['obj'], row['t']
        token = (obj, row['kind'], row['tail'])
        old_id = recent.get(token)
        if old_id is not None and abs(t-generations[old_id]['t']) <= .25:
            generations[old_id]['replicas'].append(row)
            counters['duplicate_tail'] += 1
            if slots.get(obj) != old_id:
                counters['old_replica_not_revived'] += 1
            return
        prev_id = slots.get(obj)
        prev = generations[prev_id] if prev_id is not None else None
        if (prev and abs(t-prev['t']) <= .25
                and {prev['kind'], row['kind']} == {'server09', 'typed'}
                and prev['prefix49'] == row['prefix49']):
            prev['replicas'].append(row)
            if row['parent'] and not prev['parent']:
                # Proof is available only from this observation onward.
                prev['parent'] = row['parent']
                prev['parent_observed_at'] = row['order']
                prev['parent_fixture'] = row['fixture']
                prev['parent_gen'] = slots.get(row['parent'])
            recent[token] = prev_id
            counters['exact49_typed_echo'] += 1
            return
        if prev:
            prev['superseded_at'] = row['order']
            prev['superseded_by_template'] = row['template']
        generation_counters[obj] += 1
        g = {**row, 'id': len(generations), 'generation': generation_counters[obj],
             'replicas': [], 'superseded_at': None,
             'parent_observed_at': row['order'] if row['parent'] else None,
             'parent_gen': slots.get(row['parent']) if row['parent'] else None}
        generations.append(g)
        slots[obj] = g['id']
        recent[token] = g['id']

    with CAP.open('rb') as f:
        magic = f.read(6)
        assert magic == b'DNDP1\0'
        capture_digest.update(magic)
        while header := f.read(HEADER.size):
            assert len(header) == HEADER.size
            offset = f.tell()-HEADER.size
            t, adapter, original, size = HEADER.unpack(header)
            frame = f.read(size)
            assert len(frame) == size
            capture_digest.update(header); capture_digest.update(frame)
            counters['frames'] += 1
            packet = decode_captured_frame(t, frame, original)
            if packet is None or packet.transport.upper() != 'UDP':
                continue
            counters['udp_frames'] += 1
            for mi, wire in enumerate(split_v576_payload(packet.payload)):
                counters['udp_messages'] += 1
                ms = list(owner_pattern.finditer(wire, 9))
                if not ms:
                    continue
                phases = sorted({(m.start()-key_starts[m.group()]) % 13 for m in ms})
                decoded = {p: _decode_v576_body_uncached(wire, p) for p in phases}
                decoded = {p: b for p, b in decoded.items() if ob in b[9:]}
                if not decoded:
                    continue
                counters['owner_messages'] += 1
                meta = dict(frame=counters['frames'], message=mi, file_offset=offset,
                            t=t, capture_seconds=round(t-origin, 6), adapter=adapter,
                            direction=packet.inferred_direction(), length=len(wire))
                rows = []
                # Includes unmapped templates: a reused unknown ID invalidates,
                # rather than preserving, the preceding generation.
                typed = decode_v576_damage_object_registration(wire, None)
                if typed and typed.source_actor_id == OWNER:
                    b = _decode_v576_body_uncached(wire, typed.xor_phase)
                    o = typed.record_offset+1
                    if b[o+4:o+9] == bytes(5) and u32(b, o) == u32(b, o+45) == OWNER:
                        rows.append(dict(obj=typed.damage_object_id,
                            template=typed.projectile_template_id, parent=typed.parent_damage_object_id,
                            kind='typed', m=typed.record_offset, phase=typed.xor_phase,
                            tail=b[o:].hex(), prefix49=b[o:o+49].hex()))
                for phase, b in decoded.items():
                    m = 9
                    while (m := b.find(b'\x09', m)) >= 0:
                        if (m >= 4 and m+48 <= len(b) and b[m+2:m+8] == bytes(6)
                                and u32(b, m-4) == u32(b, m+44) == OWNER
                                and 0 < u32(b, m+8) <= 0xffffff
                                and 0 < u32(b, m+12) <= 0x7fffffff):
                            rows.append(dict(obj=u32(b, m+8), template=u32(b, m+12),
                                parent=0, kind='server09', m=m, phase=phase,
                                marker=b[m:m+3].hex(), tail=b[m-4:m+48].hex(),
                                prefix49=(b[m-4:m]+b[m+3:m+48]).hex()))
                        m += 1
                    # Retain raw client inputs, not damage/status inferred casts.
                    if meta['direction'] == 'client_to_server':
                        is_cast_signature = (len(wire), wire[5:9]) in {
                            (45, bytes.fromhex('87062a00')), (63, bytes.fromhex('7cde3c00')),
                            (64, bytes.fromhex('18ba3d00')), (72, bytes.fromhex('97354500')),
                            (73, bytes.fromhex('fe5c4600')), (80, bytes.fromhex('288a4d00')),
                            (84, bytes.fromhex('e5a75100')), (91, bytes.fromhex('a7e55800')),
                            (97, bytes.fromhex('e5475e00')), (170, bytes.fromhex('d072a700'))}
                        if is_cast_signature and u32(b, 10) == OWNER:
                            msg = decode_v576_payload(wire, meta['direction'], known_action_skill_ids=skills)
                            if msg and msg.kind == 'skill_cast' and msg.source_actor_id == OWNER:
                                if msg.skill_id in (2353, 2207, 2205):
                                    actions.append({**meta, 'skill_id': msg.skill_id,
                                        'layout': msg.layout, 'phase': phase,
                                        'actor_offset': 10, 'skill_offset': 14 if len(wire)==45 else 21,
                                        'fixture': fixture(meta, wire, phase)})
                    # User called these "24 04". Keep literal observations
                    # separate; never invent an action opcode from that label.
                    if b'\x24\x04' in b[9:]:
                        counters['owner_decoded_payloads_with_literal_2404'] += 1
                at = bisect.bisect_left(times, t)
                near = at < len(times) and abs(times[at]-t) < 0.000001
                if not near and at:
                    near = abs(times[at-1]-t) < 0.000001
                hits = decode_v576_embedded_direct_damage_events(wire, None, skills) if near else ()
                events = [(r['m'], 0, r) for r in rows] + [(h.record_offset, 1, h) for h in hits]
                for m, typ, item in sorted(events, key=lambda x:(x[0],x[1])):
                    if typ == 0:
                        r = {**meta, **item, 'order': [meta['frame'], mi, m],
                             'fixture': fixture(meta, wire, item['phase'])}
                        register(r)
                        continue
                    if item.source_actor_id != OWNER:
                        continue
                    h = dict(t=t, obj=item.damage_object_id, target=item.target_actor_id,
                             result_group=item.damage_result_group_id, damage=item.damage)
                    k = hit_key(h)
                    if k not in wanted:
                        continue
                    # Exact values, exact object and exact time; not nearest Hit.
                    target = wanted[k]
                    phases_ok = [p for p,b in decoded.items() if b[m:m+3] == b'\x08\x73\0'
                                 and u32(b,m+8)==OWNER and u32(b,m+13)==h['obj']]
                    assert len(phases_ok) == 1
                    p = phases_ok[0]
                    fid = fixture(meta, wire, p)
                    selected_packets[fid] = packets[fid]
                    matches.append({**target, 'physical': meta, 'record_offset': m,
                                    'fixture': fid, 'fresh_generation_id': slots.get(h['obj']),
                                    'hit_order': [meta['frame'], mi, m]})
            if time.monotonic()-last_progress > 20:
                print(json.dumps({'frames': counters['frames'], 'seconds': round(time.monotonic()-started,2)},ensure_ascii=False),flush=True)
                last_progress = time.monotonic()

    # All full-capture raw records have been collected. Analyze without
    # reopening the capture or running a combat decoder/score engine.
    OUT.mkdir(parents=True)
    write('scan_checkpoint.json', dict(capture_sha256=capture_digest.hexdigest(),
        counters=dict(counters), casts=actions, generations=generations,
        matches=matches, packets=packets, runtime_before=before))
    unique = {}
    for h in matches:
        unique.setdefault(hit_key(h), h)
    assert set(unique) == set(wanted), (len(unique), len(wanted))
    casts = []
    for a in sorted(actions, key=lambda r:(r['frame'],r['message'])):
        prev = next((p for p in reversed(casts) if p['skill_id']==a['skill_id'] and abs(p['t']-a['t'])<=.12), None)
        if prev:
            prev.setdefault('replicas', []).append(a['fixture'])
        else:
            casts.append({**a, 'epoch': len(casts)+1})
    results = []
    for h in unique.values():
        g = generations[h['fresh_generation_id']] if h['fresh_generation_id'] is not None else None
        parent = generations[g['parent_gen']] if g and g['parent_gen'] is not None else None
        order = h['hit_order']
        exact = bool(g and parent and g['template']==3021 and parent['template']==3023
            and g['parent']==parent['obj'] and g['parent_observed_at']<=order
            and (g['superseded_at'] is None or order<g['superseded_at'])
            and (parent['superseded_at'] is None or order<parent['superseded_at'])
            and 0<=h['t']-g['t']<=8 and 0<=g['t']-parent['t']<=8)
        root_candidates = [a for a in casts if parent and a['skill_id']==2353
                           and 0<=parent['t']-a['t']<=2.5]
        concurrent = [a for a in casts if a['skill_id']==2207 and 0<=h['t']-a['t']<=2.5]
        own_skill_epoch = [a for a in casts if a['skill_id']==2353 and 0<=h['t']-a['t']<=8]
        result = {**h, 'current_child': ({k:g[k] for k in ('id','obj','template','generation','t','parent','parent_gen','fixture','superseded_at')} if g else None),
                  'current_parent': ({k:parent[k] for k in ('id','obj','template','generation','t','fixture','superseded_at')} if parent else None),
                  'complete_parent_current_generation': exact,
                  'root_action_epoch_candidates': [a['epoch'] for a in root_candidates],
                  'complete_parent_and_unique_root_action_epoch': exact and len(root_candidates)==1,
                  'nearby_2353_action_epochs_not_parent_proof': [a['epoch'] for a in own_skill_epoch],
                  'concurrent_2207_action_epochs_not_parent_proof': [a['epoch'] for a in concurrent]}
        results.append(result)
        if g:
            selected_packets[g['fixture']] = packets[g['fixture']]
            if g.get('parent_fixture'):
                selected_packets[g['parent_fixture']] = packets[g['parent_fixture']]
        if parent:
            selected_packets[parent['fixture']] = packets[parent['fixture']]
        for a in root_candidates + concurrent + own_skill_epoch:
            selected_packets[a['fixture']] = packets[a['fixture']]
    after = hashes()
    summary = dict(raw_scan_seconds=round(time.monotonic()-started,3), counters=dict(counters),
        cached_disputed_hits=len(selected), cached_disputed_damage=sum(h['damage'] for h in selected),
        reproduced_unique_hits=len(results), physical_matching_records=len(matches),
        explicit_current_parent_hits=sum(h['complete_parent_current_generation'] for h in results),
        explicit_parent_unique_root_epoch_hits=sum(h['complete_parent_and_unique_root_action_epoch'] for h in results),
        explicit_parent_unique_root_epoch_damage=sum(h['damage'] for h in results if h['complete_parent_and_unique_root_action_epoch']),
        no_explicit_parent_hits=sum(not h['complete_parent_current_generation'] for h in results),
        nearby_2353_epoch_hits=sum(bool(h['nearby_2353_action_epochs_not_parent_proof']) for h in results),
        nearby_2207_epoch_hits=sum(bool(h['concurrent_2207_action_epochs_not_parent_proof']) for h in results),
        cast_epoch_counts=dict(Counter(str(a['skill_id']) for a in casts)),
        registrations=len(generations), runtime_files_unchanged=before==after)
    write('scan.json', dict(capture=str(CAP), capture_sha256=capture_digest.hexdigest(), origin=origin,
        source_cache=str(CACHE), source_cache_sha256=sha(CACHE), summary=summary,
        scope='One full raw-frame scan, one owner; original 67 cached fixed2207/3021 rows, no scoring. Scene0 taken from whole-capture cached audit. Structural registration guards include unknown templates.',
        action_epoch_policy='Same-owner actual C2S2353 cast, unique prior root-family epoch within2.5s of registered3023. Temporal attachment is explicitly a guard, not a wire-carried action object ID. Never links child directly to nearest action.',
        runtime_before=before, runtime_after=after, disputed_hits=results, casts=casts))
    write('fixtures.json', dict(capture=str(CAP), capture_sha256=capture_digest.hexdigest(),
        records=selected_packets, expected_hits=results,
        expected_casts=[a for a in casts if a['fixture'] in selected_packets],
        note='Actual45/80-byte C2S action layouts; no unverified2404 opcode assertion. Logical cached p and physical frame IDs are distinct.'))
    write('registration_index.json', dict(capture_sha256=capture_digest.hexdigest(), generations=generations))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
