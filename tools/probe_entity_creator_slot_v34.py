"""Read-only m114 audit: six existing indexes, existing static/raw evidence only.

No runtime imports, resource scans, capture replay, or production writes.
The tail slot is an observation, never a creator-skill certificate by itself.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import ipaddress
import io
import json
from pathlib import Path
import struct
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/entity_creator_slot_v34_20260908'
INDEX = ROOT / 'outputs/v25_entity_owner_independent'
NAMES = ('0821硬核04_0_180_v2.json', '0822硬核噬魂_0_180.json',
         '0826硬核曜_0_180.json', '0830星期天_210_390.json',
         '0831黑硬曜_0_180.json', '20260822YEP黑硬曜_0_180.json')
KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')
INPUTS = {}


def load(path):
    limit = 12_000_000 if path.parent == OUT else 8_000_000
    if path.stat().st_size > limit:
        raise ValueError(f'bounded input too large: {path}')
    raw = path.read_bytes()
    INPUTS[str(path.relative_to(ROOT))] = dict(bytes=len(raw), sha256=sha256(raw).hexdigest())
    return json.loads(raw.decode('utf-8-sig'))


def save(name, data):
    if Path(name).name != name or not name.endswith('.json'):
        raise ValueError('output must be a JSON basename')
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def u32(b, offset):
    return struct.unpack_from('<I', b, offset)[0]


def xor(b, phase):
    return b[:9] + bytes(x ^ KEY[(i-phase) % 13] for i, x in enumerate(b[9:], 9))


def chains(wire):
    """Only full, length-delimited standard envelopes; no marker searching."""
    if len(wire) < 21 or int.from_bytes(wire[7:9], 'little') + 3 != len(wire):
        return []
    matches = []
    for phase in range(13):
        b = xor(wire, phase)
        pos, records = 14, []
        while pos + 7 <= len(b):
            m = pos + 4
            size = int.from_bytes(b[m+1:m+3], 'little')
            end = m + 3 + size
            if size < 1 or end > len(b):
                break
            records.append(dict(actor=u32(b, pos), marker=m, opcode=b[m], size=size))
            pos = end
        if records and pos == len(b):
            matches.append((phase, b, records))
    return matches


def direct_slots(row):
    if row['direction'] != 'server_to_client':
        return None, 'not_server'
    options = chains(bytes.fromhex(row['wire']))
    if len(options) != 1:
        return None, 'nonunique_or_incomplete_chain'
    phase, b, records = options[0]
    found = []
    for rec in records:
        m = rec['marker']
        if rec['opcode'] != 8 or rec['size'] != 115:
            continue
        if (u32(b, m+8), rec['actor'], struct.unpack_from('<Q', b, m+19)[0]) != (
                row['source'], row['target'], row['damage']):
            continue
        if b[m+12] != 0 or b[m+106:m+110] != bytes(4):
            continue
        found.append(dict(marker=m, phase=phase, m102=u32(b, m+102), m114=u32(b, m+114),
                          object=u32(b, m+13), result_group=u32(b, m+4),
                          critical=bool(b[m+35] & 1), record=b[m-4:m+118].hex()))
    if len(found) != 1:
        return None, 'no_unique_direct37_for_index_row'
    return found[0], 'ok'


def order(row):
    return row['frame'], row['message'], row.get('marker', 0)


def anchor(row):
    keys = ('frame', 'message', 'file_offset', 'time', 'direction', 'entity', 'template',
            'generation', 'actor', 'skill', 'compact', 'marker', 'phase', 'wire')
    return {k: row[k] for k in keys if k in row}


def location(row):
    return {k: v for k, v in anchor(row).items() if k != 'wire'}


def verified_birth(row):
    w = bytes.fromhex(row['wire'])
    if row['direction'] != 'server_to_client' or len(w) != 88 or w[5:9].hex() != '06275500':
        return False
    b = xor(w, 5)
    return row['entity'] > 0 and (u32(b, 10), u32(b, 18)) == (row['entity'], row['template'])


def verified_owner(row):
    if row['direction'] != 'server_to_client' or row['entity'] <= 0:
        return False
    options = chains(bytes.fromhex(row['wire']))
    if len(options) != 1:
        return False
    _, b, records = options[0]
    return any(r['marker'] == row['marker'] and r['opcode'] == 52 and r['size'] == 44
               and r['actor'] == row['entity'] and b[r['marker']+7] == 1
               and 0x1000000 <= row['owner'] < 0xffffffff
               and u32(b, r['marker']+3) == row['owner'] == u32(b, r['marker']+43)
               for r in records)


def verified_action(row):
    if row['direction'] != 'server_to_client' or row.get('compact'):
        return None
    options = chains(bytes.fromhex(row['wire']))
    if len(options) != 1:
        return None
    _, b, records = options[0]
    for r in records:
        m = r['marker']
        if m == row['marker'] and (r['opcode'], r['size']) in ((14, 35), (14, 31), (18, 36), (18, 32)):
            if (r['actor'], u32(b, m+3)) == (row['actor'], row['skill']):
                return dict(anchor(row), opcode=r['opcode'], size=r['size'],
                            m21=u32(b, m+21), record=b[m-4:m+3+r['size']].hex())
    return None


def summarize(rows):
    lives = defaultdict(list)
    for r in rows:
        lives[(r['capture'], r['source'], r['generation'])].append(r)
    return dict(hits=len(rows), damage=sum(r['damage'] for r in rows), lives=len(lives),
                multihit_lives=sum(len(v) > 1 for v in lives.values()),
                multiple_distinct_wire_marker_lives=sum(len({(h['wire_sha256'], h['marker']) for h in v}) > 1 for v in lives.values()),
                distinct_wire_marker_records=len({(r['capture'], r['wire_sha256'], r['marker']) for r in rows}),
                changing_m114_lives=sum(len({h['m114'] for h in v}) > 1 for v in lives.values()),
                changing_valid_m102_lives=sum(len({h['m102'] for h in v if h['m102'] not in (0, 0xffffffff)}) > 1 for v in lives.values()),
                m114=dict(Counter(str(r['m114']) for r in rows)),
                m102=dict(Counter(str(r['m102']) for r in rows)))


def audit():
    catalog = load(ROOT / 'outputs/v26_summon_attack_chain/static_catalog.json')
    decisions = load(INDEX / 'safe_template_decisions.json')
    specs = {(s['template'], s['monster_skill']): s for s in catalog['accepted']}
    safe = {r['template']: r for r in decisions['safe_requires']}
    rows, life_rows, excluded, index_stats = [], [], [], []
    for name in NAMES:
        data = load(INDEX / name)
        capture = data['capture']
        births = {}
        for b in data['spawns']:
            if verified_birth(b) and not b.get('duplicate_spawn'):
                births.setdefault((b['entity'], b['generation']), b)
        owners = defaultdict(list)
        for o in data['records']:
            if verified_owner(o):
                owners[(o['entity'], o['generation'])].append(o)
        actions = defaultdict(list)
        for a in data['actions']:
            if (v := verified_action(a)) is not None:
                v['capture'] = capture
                actions[a['actor']].append(v)
        lives = {}
        for key, birth in births.items():
            os = owners.get(key, [])
            valid = [o for o in os if o['template'] == birth['template'] and order(o) > order(birth)]
            if not valid or len({o['owner'] for o in os}) != 1:
                continue
            owner = min(valid, key=order)
            lives[key] = dict(capture=capture, entity=key[0], generation=key[1],
                              template=birth['template'], owner=owner['owner'],
                              birth=anchor(birth), owner_record=anchor(owner), hits=[],
                              actions=actions.get(key[0], []), owner_actions=actions.get(owner['owner'], []))
        counts = Counter(index_hit_rows=len(data['hits']), bound_lives=len(lives))
        seen = set()
        for h in data['hits']:
            life = lives.get((h['source'], h['generation']))
            if life is None or h['template'] != life['template']:
                counts['not_bound_source_life'] += 1
                continue
            if order(h) <= order(life['owner_record']):
                counts['before_owner'] += 1
                continue
            slot, reason = direct_slots(h)
            if slot is None:
                counts[reason] += 1
                excluded.append(dict(index=name, **anchor(h), source=h['source'], reason=reason))
                continue
            hit_id = (h['file_offset'], h['message'], slot['marker'])
            if hit_id in seen:
                counts['duplicate_index_same_physical_record'] += 1
                continue
            seen.add(hit_id)
            spec = specs.get((h['template'], slot['m102']))
            r = dict(capture=capture, index=name, **anchor(h), source=h['source'], target=h['target'],
                     damage=h['damage'], owner=life['owner'], **slot,
                     self_attack_player=spec['canonical_player_skill'] if spec else None,
                     self_attack_proof_id=spec['proof_id'] if spec else None,
                     safe_autonomous_family=safe.get(h['template'], {}).get('player_skill'),
                     wire_sha256=sha256(bytes.fromhex(h['wire'])).hexdigest())
            prior = [a for a in life['owner_actions'] if order(life['birth']) < order(a) < order(r)]
            r['latest_observed_owner_action_since_birth'] = location(prior[-1]) if prior else None
            r['owner_action_different_from_tail_since_birth'] = [
                location(a) for a in prior if a['skill'] != r['m114']][-1:]
            rows.append(r)
            life['hits'].append(r)
            counts['eligible_direct37'] += 1
        for life in lives.values():
            if not life['hits']:
                continue
            tail = life['hits'][0]['m114']
            life['prior_owner_tail_actions'] = [a for a in life['owner_actions']
                if a['skill'] == tail and order(a) < order(life['birth'])][-1:]
            before_birth = [a for a in life['owner_actions'] if order(a) < order(life['birth'])]
            life['latest_observed_owner_action_before_birth'] = before_birth[-1] if before_birth else None
            tail_root = life['prior_owner_tail_actions']
            life['intervening_actions_before_birth'] = [a for a in before_birth
                if tail_root and order(a) > order(tail_root[0])]
            life['owner_actions'] = [a for a in life['owner_actions']
                                     if order(life['birth']) < order(a) < order(life['hits'][-1])]
            life['actions'] = [a for a in life['actions']
                               if order(life['birth']) < order(a) <= order(life['hits'][-1])]
            life_rows.append(life)
        index_stats.append(dict(index=name, capture=capture, **dict(counts)))
    by_template = {str(t): summarize([r for r in rows if r['template'] == t])
                   for t in sorted({r['template'] for r in rows})}
    transitions = []
    for life in life_rows:
        for before, after in zip(life['hits'], life['hits'][1:]):
            if before['m114'] != after['m114']:
                transitions.append(dict(capture=life['capture'], entity=life['entity'],
                    generation=life['generation'], template=life['template'], owner=life['owner'],
                    before=before, after=after,
                    intervening_owner_actions=[a for a in life['owner_actions']
                                                if order(before) < order(a) < order(after)],
                    intervening_self_actions=[a for a in life['actions']
                                               if order(before) < order(a) < order(after)]))
    exact = [r for r in rows if r['self_attack_player'] is not None]
    family = [r for r in rows if r['safe_autonomous_family'] is not None]
    safe_exact = [r for r in family if r['m102'] in safe[r['template']]['autonomous_monster_skills']]
    magma = [r for r in rows if r['template'] == 32022]
    stats = dict(all_bound_direct37=summarize(rows), by_index=index_stats, by_template=by_template,
                 exact_self_attack=summarize(exact), safe_family=summarize(family),
                 safe_family_with_explicit_monster_slot=summarize(safe_exact),
                 zero_tail=summarize([r for r in rows if r['m114'] == 0]),
                 exact_tail_equals_attack=sum(r['m114'] == r['self_attack_player'] for r in exact),
                 exact_tail_differs_attack=sum(r['m114'] != r['self_attack_player'] for r in exact),
                 safe_tail_equals_family=sum(r['m114'] == r['safe_autonomous_family'] for r in family),
                 safe_tail_differs_family=sum(r['m114'] != r['safe_autonomous_family'] for r in family),
                 different_owner_action_since_birth_hits=sum(bool(r['owner_action_different_from_tail_since_birth']) for r in rows),
                 latest_owner_action_differs_tail_hits=sum(
                     bool(r['latest_observed_owner_action_since_birth']) and
                     r['latest_observed_owner_action_since_birth']['skill'] != r['m114'] for r in rows),
                 magma_latest_owner_action_differs_tail_hits=sum(
                     bool(r['latest_observed_owner_action_since_birth']) and
                     r['latest_observed_owner_action_since_birth']['skill'] != r['m114'] for r in magma),
                 magma_observed_hit_age_range=[min(r['time']-l['birth']['time'] for l in life_rows if l['template'] == 32022 for r in l['hits']),
                                               max(r['time']-l['birth']['time'] for l in life_rows if l['template'] == 32022 for r in l['hits'])],
                 templates_with_multiple_nonzero_tails=[t for t, v in by_template.items()
                     if len([s for s in v['m114'] if s not in ('0', '4294967295')]) > 1],
                 nonzero_tail_differs_latest_observed_action_before_birth_lives=sum(
                     l['hits'][0]['m114'] != 0 and bool(l['latest_observed_owner_action_before_birth'])
                     and l['latest_observed_owner_action_before_birth']['skill'] != l['hits'][0]['m114'] for l in life_rows),
                 transitions=len(transitions))
    save('index_evidence.json', dict(schema='entity-creator-slot-v34.1', inputs=INPUTS,
        scope='six fixed indexes; physical direct37 rows; generations are indexed birth generations, not proven despawn lives',
        statistics=stats, lives=life_rows, transitions=transitions, excluded=excluded,
        accepted_attack_specs=[s for s in catalog['accepted'] if any(
            r['self_attack_proof_id'] == s['proof_id'] for r in exact)]))
    save('statistics.json', stats)
    print(json.dumps(stats, ensure_ascii=False))


def reopen(capture, row):
    """One exact physical frame, strict Ethernet/IPv4/UDP checks; no scanning."""
    with Path(capture).open('rb') as stream:
        stream.seek(row['file_offset'])
        header = stream.read(17)
        timestamp, adapter, original, size = struct.unpack('<dBII', header)
        if not 42 <= size <= 65577:
            raise ValueError('unexpected indexed frame size')
        raw = stream.read(size)
    assert timestamp == row['time'] and len(raw) == size
    assert raw[12:14] == bytes.fromhex('0800') and raw[14] >> 4 == 4 and raw[23] == 17
    ihl = (raw[14] & 15) * 4
    assert ihl >= 20 and struct.unpack_from('>H', raw, 20)[0] & 0x3fff == 0
    assert struct.unpack_from('>H', raw, 16)[0] + 14 == size
    udp = 14 + ihl
    assert udp + struct.unpack_from('>H', raw, udp+4)[0] == size
    assert row['message'] == 0 and raw[udp+8:] == bytes.fromhex(row['wire'])
    src, dst = (ipaddress.IPv4Address(raw[p:p+4]) for p in (26, 30))
    assert src.is_global and dst.is_private and row['direction'] == 'server_to_client'
    return dict(capture=capture, **anchor(row), bytes_read=17+size, original=original,
                adapter=adapter, physical_frame_sha256=sha256(raw).hexdigest(),
                wire_sha256=sha256(raw[udp+8:]).hexdigest(), physical_frame=raw.hex())


def points():
    data = load(OUT / 'index_evidence.json')
    selected = [
        ('duck_attack_switch', '0826硬核曜.dpscap', 61),
        ('alfredo_attack_switch', '0826硬核曜.dpscap', 57),
        ('shadow_attack_switch', '0831黑硬曜.dpscap', 161),
        ('duck_zero_tail', '0826硬核曜.dpscap', 56),
        ('saw_zero_tail', '0821硬核04.dpscap', 89),
        ('cannon_zero_tail', '0821硬核04.dpscap', 79),
        ('saw_nonzero_tail', '0821硬核04.dpscap', 72),
        ('lightning_creator_family', '20260822YEP黑硬曜.dpscap', 137),
        ('magma_owner_cast_changed_A', '0821硬核04.dpscap', 70),
        ('magma_owner_cast_changed_B', '20260822YEP黑硬曜.dpscap', 61),
    ]
    cases, anchors = [], {}

    def add(capture, r):
        key = (capture, r['file_offset'], r['message'])
        anchors.setdefault(key, r)
        return dict(capture=capture, **location(r))

    for label, capture_name, entity in selected:
        life = next(l for l in data['lives'] if Path(l['capture']).name == capture_name and l['entity'] == entity)
        chosen = [life['hits'][0], life['hits'][-1]]
        by_slot = {}
        for h in life['hits']:
            by_slot.setdefault(h['m102'], h)
        chosen += list(by_slot.values())
        chosen = list({(h['file_offset'], h['marker']): h for h in chosen}.values())
        chosen.sort(key=order)
        before = [life['latest_observed_owner_action_before_birth']] if life['latest_observed_owner_action_before_birth'] else []
        for r in [life['birth'], life['owner_record'], *life['prior_owner_tail_actions'], *before, *chosen]:
            add(life['capture'], r)
        owner_action_refs = []
        for h in chosen:
            latest = h['latest_observed_owner_action_since_birth']
            if latest:
                action = next(a for a in life['owner_actions'] if order(a) == order(latest))
                owner_action_refs.append(add(life['capture'], action))
        cases.append(dict(label=label, capture=life['capture'], entity=entity, template=life['template'],
            generation=life['generation'], owner=life['owner'],
            birth=location(life['birth']), owner_record=location(life['owner_record']),
            all_hits=len(life['hits']), observed_last_hit_age=life['hits'][-1]['time']-life['birth']['time'],
            m114=sorted({h['m114'] for h in life['hits']}),
            m102=sorted({h['m102'] for h in life['hits']}),
            matching_tail_action_before_birth=[location(a) for a in life['prior_owner_tail_actions']],
            latest_observed_owner_action_before_birth=location(before[0]) if before else None,
            self_attack_skills=sorted({h['self_attack_player'] for h in life['hits'] if h['self_attack_player'] is not None}),
            owner_actions_observed_between_birth_last_hit=len(life['owner_actions']),
            selected_hits=chosen, selected_owner_actions=owner_action_refs))
    assert len(anchors) <= 90
    raw = [reopen(capture, r) for (capture, _, _), r in anchors.items()]

    fixtures = load(ROOT / 'tests/data/summon_attack_v26_raw.json')
    fixture_rows = []
    for case in fixtures['cases']:
        h = case['hit']
        row = dict(wire=h['wire'], direction=h['direction'], source=h['entity_id'],
                   target=h['target_id'], damage=h['damage'])
        fields, reason = direct_slots(row)
        assert reason == 'ok' and fields['m102'] == case['expected_monster_skill']
        fixture_rows.append(dict(name=case['name'], source=h['entity_id'], template=h['template_id'],
            capture=h['capture'], anchor=case['raw_anchor'], m102=fields['m102'], m114=fields['m114'],
            expected_self_attack=case['expected_skill'], evidence_source='existing real raw fixture; not added to six-index denominator'))

    lifecycle = load(ROOT / 'outputs/magma_burst_lifecycle_v34_20260908/evidence.json')
    lifecycle_rows = []
    for life in lifecycle:
        hits, candidates, owner_actions = [], [], []
        for r in life['rows']:
            for h in r.get('hits', []):
                if h['source'] != life['entity'] or h['family'] != 'direct37':
                    continue
                fields, reason = direct_slots(dict(r, source=h['source'], target=h['target'], damage=h['amount']))
                assert reason == 'ok'
                hits.append(dict(location(r), age=r['age'], **fields, amount=h['amount']))
            for rec in r.get('records', []):
                if rec['actor'] == life['entity'] and rec['type'] == 27 and rec['length'] == 2:
                    candidates.append(dict(location(r), age=r['age'], record=rec['record'],
                        semantics='same-entity candidate end marker; despawn/removal is NOT established'))
                if rec['actor'] == life['owner'] and (rec['type'], rec['length']) in ((14, 31), (14, 35), (18, 32), (18, 36)):
                    a = verified_action(dict(r, actor=rec['actor'], skill=rec['first_u32'],
                                             marker=rec['marker'], compact=False))
                    if a:
                        owner_actions.append(dict(location(a), age=r['age'], m21=a['m21']))
        lifecycle_rows.append(dict(capture=life['capture'], entity=life['entity'], hits=hits,
                                   owner_actions=owner_actions, candidate_end_markers=candidates))
    decisions = load(INDEX / 'safe_template_decisions.json')
    selected_static = []
    for t in decisions['resource_proof']['templates']:
        if t['template'] not in (30001, 30501, 32004, 32011, 33503):
            continue
        selected_static.append(dict(template=t['template'], ai=t['ai_path'], skills=[
            dict(monster_skill=s['monster_skill'], explicit_player_skill_ids=s['explicit_player_skill_ids'],
                 mapping_pair_endpoint_rows=s['mapping_pair_endpoint_rows'], level_rows=s['level_rows'], actions=s['actions'])
            for s in t['skills'] if s['monster_skill'] in (30612, 30616, 30807, 30808, 30823, 30824)]))
    magma_static = load(ROOT / 'outputs/magma_exact_route_v28_20260908/static_evidence.json')
    root_routes = []
    for skill in (4410, 4505, 291223):
        routes = [r for r in magma_static['dnt_routes'] if r['skill'] == skill and r['apply_type'] == 0]
        if routes:
            root_routes.append(routes[0])
    own_actions = [a for a in magma_static['exact_actions'] if a['path'].startswith('char/monster/')]
    magma_summary = dict(dnt_routes=root_routes, base_edges=magma_static['base_edges'],
        clone_actions=[dict(path=a['path'], name=a['name'], offset=a['offset'],
            span_sha256=a['span_sha256'], typed48=[dict(offset=s['offset'], template=s['u32'][0])
                for s in a['signals'] if s['type'] == 48])
            for a in magma_static['exact_actions'] if a['path'].startswith('char/player/') and 'Clone' in a['name']],
        own_action_count=len(own_actions), own_signal_counts=dict(Counter(str(s['type']) for a in own_actions for s in a['signals'])),
        own_action_summaries=[dict(name=a['name'], offset=a['offset'], next_action=a['next_action'],
                                  span_sha256=a['span_sha256']) for a in own_actions],
        existing_ai=magma_static['reused_global_facts']['ai'],
        clone_inbound=magma_static['reused_global_facts']['clone_inbound'])
    save('point_evidence.json', dict(inputs=INPUTS, cases=cases, raw_frames=raw,
        raw_io=dict(seeks=len(raw), bytes_read=sum(r['bytes_read'] for r in raw), replay=False,
                    full_capture_or_resource_scan=False),
        fixture_checks=fixture_rows, magma_lifecycle=lifecycle_rows,
        parent_act_edges=decisions['parent_act_edges'], selected_static_attack_routes=selected_static,
        magma_static_summary=magma_summary,
        caveat='static certificates reused from existing evidence; resources not rescanned or rehashed'))
    for c in cases:
        print(json.dumps(dict(label=c['label'], capture=Path(c['capture']).name, entity=c['entity'],
            template=c['template'], birth=c['birth']['frame'], n=c['all_hits'], m114=c['m114'],
            m102=c['m102'], attacks=c['self_attack_skills'],
            selected_hits=[(h['frame'], h['file_offset'], h['m102'], h['m114']) for h in c['selected_hits']],
            prior=c['matching_tail_action_before_birth'], owner_actions=c['selected_owner_actions']), ensure_ascii=False))
    print(json.dumps(dict(raw_seeks=len(raw), raw_bytes=sum(r['bytes_read'] for r in raw),
                         lifecycle=lifecycle_rows), ensure_ascii=False))


def assess_magma(life, hit, *, selected_root, static_base_family,
                 externally_commandable=False):
    """A reviewable annotation policy, NOT a production hit acceptance rule.

    selected_root is independently decoded, but mere earlier order does not bind
    its invocation to this birth. This function therefore never emits strict
    attribution or an exact creator-action certificate, even for positive input.
    """
    decision = dict(status='insufficient', creator_family_candidate=None,
                    supports_exact_creator_action=False, supports_exact_ex_variant=False,
                    strict_hit_upgrade=False, evidence_level='none')
    if not life or externally_commandable or life.get('conflicted') or life.get('retired'):
        return dict(decision, reason='missing_or_incompatible_life')
    if (life['capture'], life['entity'], life['template'], life['generation']) != (
            hit['capture'], hit['source'], hit['template'], hit['generation']):
        return dict(decision, reason='life_mismatch')
    birth, owner = life['birth'], life['owner_record']
    for r in (birth, owner):
        if (r['entity'], r['template'], r['generation']) != (life['entity'], life['template'], life['generation']):
            return dict(decision, reason='birth_owner_life_mismatch')
    if not verified_birth(birth) or not verified_owner(dict(owner, entity=life['entity'], owner=life['owner'])):
        return dict(decision, reason='birth_owner_not_verified')
    if not order(birth) < order(owner) < order(hit) or hit['owner'] != life['owner']:
        return dict(decision, reason='birth_owner_order_mismatch')
    fields, reason = direct_slots(hit)
    if reason != 'ok' or hit['template'] != 32022 or fields['m102'] != 0xffffffff or fields['m114'] != 4410:
        return dict(decision, reason='not_observed_magma_layout_and_slot_profile')
    if hit['source'] == hit['target'] or hit['damage'] <= 0:
        return dict(decision, reason='not_positive_direct_enemy_result')
    # Empirical same-generation support only; this is not a causal proof.
    peer_fields = [direct_slots(h)[0] for h in life['hits']]
    if not peer_fields or any(p is None or p['m114'] != 4410 for p in peer_fields):
        return dict(decision, reason='tail_missing_or_inconsistent_in_observed_generation')
    root = verified_action(selected_root) if selected_root else None
    if (root is None or selected_root.get('capture') != life['capture']
            or (root['actor'], root['skill'], root['m21']) != (life['owner'], 4410, 4505)
            or (root['opcode'], root['size']) != (14, 35) or order(root) >= order(birth)
            or static_base_family != 4410):
        return dict(decision, reason='missing_independent_root_or_static_family_corroboration')
    return dict(decision, status='conditional_family_corroboration', creator_family_candidate=4410,
                evidence_level='empirical_packet_tail_plus_independent_family_evidence',
                reason='stable_entity_origin_context_supported; exact_invocation_and_variant_unresolved')


def proposal():
    data = load(OUT / 'index_evidence.json')
    cases = []
    for capture_name, entity in [('0821硬核04.dpscap', 70), ('20260822YEP黑硬曜.dpscap', 61)]:
        life = next(l for l in data['lives'] if Path(l['capture']).name == capture_name and l['entity'] == entity)
        root = life['prior_owner_tail_actions'][0]
        for hit in life['hits']:
            cases.append(dict(capture=life['capture'], entity=entity, frame=hit['frame'],
                decision=assess_magma(life, hit, selected_root=root, static_base_family=4410)))
    save('proposal.json', dict(
        verdict='m114 may support a conditional creator BASE-family observation; not a standalone exact creator-skill certificate',
        production_modified=False, confirmed_new_hits=0, strict_hit_upgrade_enabled=False,
        field_semantics='consistent with retained entity creation/origin context; setter, inheritance, and reset semantics remain unproven',
        clone_argument=dict(
            conditional_support='If nonzero m114 is independently established as causal origin base family for the current birth, per-hit family attribution does not require global exclusion of every Clone.',
            exact_root_limit='m1144410 cannot exclude a Clone within family4410 and cannot select exact normal EX4505.',
            current_identifiability_gap='All21 observed templates have at most one nonzero tail; optional template label / inherited attribution context still fits the observations. No same-template distinct-creator contrast or setter/copy data flow is present.',
            counterexample_status='No observed wrong-nonzero-creator counterexample was found; the alternative model is not claimed to be the actual implementation.',
            canonical_only_limit='For canonical4410 the relevant missing fact is causal family semantics, not exact EX action selection. Do not impose exact EX selection when only canonical family is required.'),
        not_supported=['always newest owner cast', 'always current entity attack', 'nonzero for every created entity',
                       '4410 selects normal EX4505', 'short duration proves creation causality', '1b02 proves despawn'],
        conditional_contract=['complete server direct37 with physical fields matching',
            'independently observed spawn88 and full repeated342c owner in the same capture/entity/generation',
            'template32022, m102FFFFFFFF, m1144410, positive non-self result',
            'no observed same-generation tail conflict and no conflicting owner/retirement state',
            'independent complete normal EX root4410+4505 and existing static base-family4410 evidence',
            'short burst profile is supportive context only; no 2-second expiry inferred from1b02',
            'emit only creation-family corroboration; do not change exact creator action, hit count, skill casts or strict coverage'],
        live_integration_requirements=['wire retransmission and actual reuse/scene epochs need production lifecycle guards',
            'candidate1b02 cannot supply retirement semantics',
            'new nonzero conflicting m114 or externally controlled damage branch invalidates transfer of this policy'],
        alternatives=['audit complete m114 source/copy/set/reset data flow against controllable summons and fresh creations',
            'same template with independently proven distinct creator base families: does nonzero m114 change with the creator or stay a template label?',
            'find per-birth action/signal/parent key linking to selected typed48',
            'resolve Clone[1] entry or prove all reachable damage-producing creator entries share canonical4410',
            'controlled differential capture: base4410, normal EX4505, repeated same-skill creates, intervening other cast, commandable summon'],
        sample_decisions=cases))
    print(json.dumps(cases, ensure_ascii=False))


def verify():
    sys.path.insert(0, str(ROOT))
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_entity_creator_slot_v34.py')
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    files = [ROOT / 'tools/probe_entity_creator_slot_v34.py', ROOT / 'tests/test_entity_creator_slot_v34.py',
             *(OUT / name for name in ('index_evidence.json', 'statistics.json', 'point_evidence.json', 'proposal.json', 'REPORT.md'))]
    hashes = {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}
    save('verification.json', dict(tests_run=result.testsRun, successful=result.wasSuccessful(),
        failures=len(result.failures), errors=len(result.errors), details=stream.getvalue(), file_sha256=hashes,
        semantics='real-evidence regressions plus synthetic rejection tests; not a universal field-semantics proof',
        production_tests_run=False, heavy_replay_run=False))
    print(stream.getvalue())
    if not result.wasSuccessful():
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['audit', 'points', 'proposal', 'verify'])
    args = parser.parse_args()
    if args.mode == 'audit':
        audit()
    elif args.mode == 'points':
        points()
    elif args.mode == 'proposal':
        proposal()
    elif args.mode == 'verify':
        verify()


if __name__ == '__main__':
    main()
