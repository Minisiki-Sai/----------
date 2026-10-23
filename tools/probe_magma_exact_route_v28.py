"""Bounded Magma Wall EX audit. Stdlib only; no replay/runtime imports/workers.

Reads six pre-existing small indexes sequentially, seeks selected raw frames,
and reads ACT action spans at offsets supplied by the prior closure. Outputs
are restricted to outputs/magma_exact_route_v28_20260908. Never infers a
creation edge from proximity, m114, names, compact action bytes or birth count.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import ipaddress
import json
from pathlib import Path
import struct
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/magma_exact_route_v28_20260908'
OLD = ROOT / 'outputs/v26_magmawall_family_closure_20260908'
INDEX = ROOT / 'outputs/v25_entity_owner_independent'
RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
INDEX_NAMES = ('0821硬核04_0_180_v2.json', '0822硬核噬魂_0_180.json',
               '0826硬核曜_0_180.json', '0830星期天_210_390.json',
               '0831黑硬曜_0_180.json', '20260822YEP黑硬曜_0_180.json')
KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')
FULL = {bytes.fromhex(s) for s in ('0e2300', '0e1f00', '122400', '122000')}
STATS = Counter()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load(path):
    # These six indexes are <5 MB each. Do not load capture streams/catalogs.
    if path.stat().st_size > 8_000_000:
        raise ValueError(f'input exceeds bounded JSON size: {path}')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(name, value):
    assert Path(name).name == name and name.endswith('.json')
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def u32(b, p):
    return struct.unpack_from('<I', b, p)[0]


def xor(b, phase):
    return b[:9]+bytes(v ^ KEY[(i-phase) % 13] for i, v in enumerate(b[9:], 9))


def short(row):
    return {k: row[k] for k in ('frame', 'message', 'file_offset', 'time', 'direction',
            'entity', 'template', 'generation', 'actor', 'skill', 'marker', 'compact',
            'kind', 'variant', 'owner', 'source', 'target', 'damage') if k in row}


def reopen(capture, row):
    """One exact indexed frame, no scanning or stream demultiplexer."""
    with Path(capture).open('rb') as f:
        f.seek(row['file_offset'])
        header = f.read(17)
        timestamp, adapter, original, size = struct.unpack('<dBII', header)
        assert 42 <= size <= 65577
        frame = f.read(size)
    STATS['raw_frame_seeks'] += 1
    STATS['raw_bytes_read'] += 17+size
    assert timestamp == row['time'] and size == len(frame)
    assert frame[12:14] == bytes.fromhex('0800') and frame[14] >> 4 == 4
    ihl = (frame[14] & 15)*4
    assert ihl >= 20 and frame[23] == 17
    assert 14+struct.unpack_from('>H', frame, 16)[0] == size
    assert struct.unpack_from('>H', frame, 20)[0] & 0x3fff == 0
    udp = 14+ihl
    length = struct.unpack_from('>H', frame, udp+4)[0]
    assert length >= 8 and udp+length == size
    wire = frame[udp+8:]
    assert row['message'] == 0 and wire.hex() == row['wire']
    assert struct.unpack_from('<H', wire, 7)[0]+3 == len(wire)
    src, dst = (ipaddress.IPv4Address(frame[p:p+4]) for p in (26, 30))
    direction = 'server_to_client' if src.is_global and dst.is_private else 'client_to_server'
    assert direction == row['direction']
    return dict(short(row), raw_frame=frame.hex(), raw_frame_sha256=sha(frame),
                wire=wire.hex(), wire_sha256=sha(wire), original=original,
                adapter=adapter, independently_reopened=True)


def owner_fields(wire, entity):
    matches = []
    for phase in range(13):
        b = xor(wire, phase)
        pos = 14
        while pos+7 <= len(b):
            m = pos+4
            end = m+3+struct.unpack_from('<H', b, m+1)[0]
            if end > len(b):
                break
            if b[m:m+3] == bytes.fromhex('342c00') and u32(b, m-4) == entity:
                owner, repeat = u32(b, m+3), u32(b, m+43)
                if 0x1000000 <= owner < 0xffffffff and owner == repeat and b[m+7] == 1:
                    matches.append(dict(entity=entity, owner=owner, repeat=repeat,
                        observer=u32(b, 10), marker=m, phase=phase, plaintext=b.hex()))
            pos = end
    assert len(matches) == 1
    return matches[0]


def hit_fields(row):
    wire = bytes.fromhex(row['wire'])
    matches = []
    for phase in range(13):
        b = xor(wire, phase)
        # Selected exact physical record must match existing source/target/damage.
        for m in range(18, len(b)-117):
            if b[m:m+3] != bytes.fromhex('087300'):
                continue
            source, target = u32(b, m+8), u32(b, m-4)
            damage = struct.unpack_from('<Q', b, m+19)[0]
            if (source, target, damage) != (row['source'], row['target'], row['damage']):
                continue
            if b[m+12] != 0 or b[m+106:m+110] != bytes(4):
                continue
            matches.append(dict(source=source, target=target, damage=damage,
                marker=m, phase=phase, monster_skill=u32(b, m+102),
                context_m114=u32(b, m+114), critical=bool(b[m+35] & 1),
                object=u32(b, m+13), result_group=u32(b, m+4), plaintext=b.hex()))
    assert len(matches) == 1
    return matches[0]


def action_fields(row):
    wire = bytes.fromhex(row['wire'])
    b = xor(wire, row['phase'])
    m = row['marker']
    if row.get('layout') == 'cast45':
        assert len(wire) == 45 and wire[5:9] == bytes.fromhex('87062a00')
        actor, skill = u32(b, 10), u32(b, 14)
        record = b
    else:
        marker = b[m:m+3]
        end = m+3+struct.unpack_from('<H', b, m+1)[0]
        assert marker in FULL | {bytes.fromhex('031400')} and end <= len(b)
        actor = u32(b, m-4)
        skill = b[m+3] if row['compact'] else u32(b, m+3)
        record = b[m-4:end]
    assert actor == row['actor'] and skill == row['skill'] and record.hex() == row['record']
    return dict(actor=actor, skill=skill, compact=row['compact'], marker=m,
                phase=row['phase'], record=record.hex(), plaintext=b.hex())


class ActReader:
    """Small sequential reader opened ONLY at a previously recorded action."""
    def __init__(self, stream):
        self.f = stream

    def take(self, n):
        assert 0 <= n <= 1_048_576
        b = self.f.read(n)
        assert len(b) == n
        STATS['act_point_bytes_read'] += n
        return b

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def count(self):
        n = self.i32()
        assert 0 <= n <= 65536
        return n

    def string(self):
        b = self.take(self.count()).rstrip(b'\0')
        for encoding in ('utf-8', 'gb18030', 'latin-1'):
            try:
                return b.decode(encoding)
            except UnicodeDecodeError:
                pass


def act_action(path, offset):
    with path.open('rb') as f:
        r = ActReader(f)
        assert r.take(32).startswith(b'Eternity Action File')
        version, count = r.i32(), r.count()
        assert version in range(1, 7) and count > 0
        f.seek(offset)
        action = dict(offset=offset, name=r.string(), animation=r.string(), frames=r.i32(),
                      next_action=r.string(), blend=r.i32(), next_frame=r.i32())
        r.take({1: 0, 2: 4, 3: 8, 4: 9, 5: 13, 6: 14}[version])
        signals = []
        for _ in range(r.count()):
            s = dict(offset=f.tell(), type=r.i32(), start=r.i32(), end=r.i32(), order=r.i32())
            s['u32'] = list(struct.unpack('<64I', r.take(256)))
            s['tables'] = {}
            for width, label in ((8, 'vec2'), (12, 'vec3'), (16, 'vec4')):
                n = r.count()
                indexes = [r.i32() for _ in range(n)]
                values = [list(struct.unpack('<'+'f'*(width//4), r.take(width))) for _ in range(n)]
                if n:
                    s['tables'][label] = [[k, v] for k, v in zip(indexes, values)]
            indexes = [r.i32() for _ in range(r.count())]
            s['strings'] = [[k, r.string()] for k in indexes]
            if version >= 6:
                r.take(1)
            signals.append(s)
        action.update(signals=signals, end_offset=f.tell())
        f.seek(offset)
        action['span_sha256'] = sha(r.take(action['end_offset']-offset))
        return action


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while b := f.read(262144):
            h.update(b)
            STATS['resource_hash_bytes_read'] += len(b)
    return h.hexdigest()


def static():
    old_act, tables, ai = (load(OLD / n) for n in ('act_closure.json', 'table_closure.json', 'ai_closure.json'))
    roots = old_act['root_definitions']
    entity_path = 'char/monster/ac_magmawall/academic_magmawall_ex.act'
    selected = [(r['path'], r['action']) for r in roots]
    selected += [(entity_path, a) for a in old_act['closure'][entity_path]['actions']]
    verified_actions = []
    for rel, expected in selected:
        a = act_action(RESOURCE / rel, expected['offset'])
        assert a['name'] == expected['name'] and a['frames'] == expected['frames']
        assert a['next_action'] == expected['next_action']
        assert [s for s in a['signals'] if s['type'] in (4, 15, 48, 126)] == expected['exits']
        assert [s for s in a['signals'] if any(v for _, v in s['strings'])] == expected['string_signals']
        verified_actions.append(dict(path=rel, **a))
    selected_tables = {'skillleveltable_characteracademic.dnt', 'skillleveltable_rune.dnt',
        'skillleveltable_masteryskill.dnt', 'skilltable_character.dnt', 'skilltable_rune.dnt',
        'runeresonatetable.dnt', 'masteryskillleveltable.dnt'}
    manifest = [r for r in old_act['referenced_manifest'] if r['path'] in {p for p, _ in selected}]
    manifest += [r for r in tables['manifest'] if Path(r['path']).name in selected_tables]
    manifest += [r for r in ai['referenced_manifest'] if r['path'] in ai['assigned_ai']]
    verified_files = []
    for r in manifest:
        path = RESOURCE / r['path']
        digest = file_hash(path)
        assert digest == r['sha256'], ('resource drift', r['path'])
        verified_files.append(dict(path=r['path'], size=path.stat().st_size, sha256=digest))
    relevant_levels = [r for r in tables['levels'] if r['row'].get('_ProcessParam1') in
                       {'Skill_MagmaWall', 'Skill_MagmaWall_EX', 'Skill_MagmaWall_EX_Clone[1]'}]
    paths_by_action = {a['name']: a for a in verified_actions if a['path'] != entity_path}
    routes = []
    for entry in relevant_levels:
        row = entry['row']
        a = paths_by_action[row['_ProcessParam1']]
        routes.append(dict(table=entry['table'], row_id=row['RowId'], skill=row['_SkillIndex'],
            level=row['_SkillLevel'], apply_type=row['_ApplyType'], action=a['name'], path=a['path'],
            action_offset=a['offset'], generators=[dict(offset=s['offset'], type=s['type'],
                template=s['u32'][0]) for s in a['signals'] if s['type'] == 48]))
    result = dict(schema='magma-exact-v28-static', verified_files=verified_files,
        reused_closure_hashes={n: sha((OLD/n).read_bytes()) for n in
                              ('act_closure.json', 'table_closure.json', 'ai_closure.json', 'proposal.py')},
        exact_actions=verified_actions, dnt_routes=routes,
        base_edges=[r for r in tables['skill_rows'] if r['row']['RowId'] in (4410, 4505, 291223)],
        rune_edges=tables['rune_links'], mastery_edges=tables['mastery_links'],
        skill_tree=[r for r in tables['skill_tree'] if r.get('_SkillTableID') in (4410, 4505)],
        reused_global_facts=dict(manifest_count=old_act['manifest_count'], parsed_count=old_act['parsed_count'],
            parsed_errors=old_act['parsed_errors'], clone_inbound=old_act['root_action_inbound'],
            installed_skill_tables=tables['installed_skill_tables'], ai=ai['assigned_ai']),
        scope='Old global closure reused, NOT rescanned. 12 exact ACT actions re-read; selected resource hashes checked.',
        counters=dict(STATS))
    save('static_evidence.json', result)
    print(json.dumps(dict(actions=len(verified_actions), resource_files=len(verified_files),
        bindings=dict(Counter((r['action'] for r in routes))),
        roots=[dict(action=a['name'], offset=a['offset'], end=a['end_offset'],
            births=[dict(offset=s['offset'], template=s['u32'][0]) for s in a['signals'] if s['type'] == 48])
            for a in verified_actions[:3]], counters=dict(STATS)), ensure_ascii=True))


def record_chains(wire):
    """Only complete length-delimited chains beginning at the known envelope.

    Unknown record types stay unknown. A byte marker alone is never accepted
    as an action. Some datagrams use other envelopes: record them as unparsed.
    """
    matches = []
    for phase in range(13):
        b = xor(wire, phase)
        pos, records = 14, []
        while pos+7 <= len(b):
            m = pos+4
            size = struct.unpack_from('<H', b, m+1)[0]
            end = m+3+size
            if size < 1 or end > len(b):
                break
            records.append(dict(actor=u32(b, pos), marker=m, type=b[m], length=size,
                first_u32=u32(b, m+3) if size >= 4 else None, record=b[pos:end].hex()))
            pos = end
        if records and pos == len(b):
            matches.append(dict(phase=phase, records=records, plaintext=b.hex()))
    return matches


def contexts():
    data = load(OUT / 'dynamic_evidence.json')
    results = []
    for s in data['samples']:
        start = min(a['anchor']['file_offset'] for a in s['prior_actions'])
        end = s['anchors']['hit']['file_offset']
        first_frame = min(a['anchor']['frame'] for a in s['prior_actions'])
        assert end-start <= 65536
        owner, entity = s['owner_fields']['owner'], s['anchors']['birth']['entity']
        rows, observed = [], Counter()
        with Path(s['capture']).open('rb') as f:
            f.seek(start)
            n = 0
            while f.tell() <= end:
                assert n < 256
                offset = f.tell()
                header = f.read(17)
                timestamp, adapter, original, size = struct.unpack('<dBII', header)
                assert 0 < size <= 65577 and f.tell()+size-start <= 131072
                raw = f.read(size)
                assert len(raw) == size
                STATS['context_raw_bytes_read'] += size+17
                entry = dict(frame=first_frame+n, file_offset=offset, time=timestamp,
                    raw_frame_sha256=sha(raw), frame_size=size)
                n += 1
                if raw[12:14] != bytes.fromhex('0800') or raw[14] >> 4 != 4 or raw[23] != 17:
                    entry['status'] = 'not_plain_ipv4_udp'
                    rows.append(entry)
                    continue
                udp = 14+(raw[14] & 15)*4
                ip_end = struct.unpack_from('>H', raw, 16)[0]+14
                if (udp+8 > ip_end or ip_end > len(raw)
                        or struct.unpack_from('>H', raw, 20)[0] & 0x3fff
                        or udp+struct.unpack_from('>H', raw, udp+4)[0] != ip_end):
                    entry['status'] = 'unsupported_transport_bounds_or_fragment'
                    rows.append(entry)
                    continue
                entry['ethernet_padding_hex'] = raw[ip_end:].hex()
                wire = raw[udp+8:ip_end]
                entry.update(wire=wire.hex(), wire_sha256=sha(wire))
                if len(wire) < 9 or struct.unpack_from('<H', wire, 7)[0]+3 != len(wire):
                    entry['status'] = 'unsupported_message_envelope'
                    rows.append(entry)
                    continue
                entry['opcode'] = wire[5:9].hex()
                observed[entry['opcode']] += 1
                src, dst = (ipaddress.IPv4Address(raw[p:p+4]) for p in (26, 30))
                entry['direction'] = 'server_to_client' if src.is_global and dst.is_private else 'client_to_server'
                if len(wire) == 88 and wire[5:9] == bytes.fromhex('06275500'):
                    b = xor(wire, 5)
                    entry.update(status='spawn88', entity=u32(b, 10), template=u32(b, 18), plaintext=b.hex())
                elif len(wire) == 45 and wire[5:9] == bytes.fromhex('87062a00'):
                    b = xor(wire, 6)
                    entry.update(status='cast45', actor=u32(b, 10), skill=u32(b, 14), plaintext=b.hex())
                else:
                    chains = record_chains(wire)
                    if len(chains) == 1:
                        entry.update(status='complete_record_chain', **chains[0])
                    else:
                        entry.update(status='unsupported_or_ambiguous_chain', chain_candidates=len(chains))
                rows.append(entry)
        selected = []
        for r in rows:
            for a in r.get('records', []):
                if a['actor'] in (owner, entity):
                    selected.append(dict(frame=r['frame'], file_offset=r['file_offset'], phase=r['phase'], **a))
        results.append(dict(index=s['index'], capture=s['capture'], start_offset=start, end_offset=end,
            frames=len(rows), bytes_read=sum(r['frame_size']+17 for r in rows), owner=owner, entity=entity,
            status_counts=dict(Counter(r['status'] for r in rows)), opcode_counts=dict(observed),
            selected_records=selected, raw_frames=rows,
            limitation='Unrecognized envelopes/records retained, never claimed absent or interpreted as creation links.'))
    save('bounded_context_evidence.json', dict(schema='magma-exact-v28-context', windows=results,
        caps=dict(max_frames_per_window=256, max_start_end_byte_gap=65536), counters=dict(STATS)))
    print(json.dumps([dict(index=r['index'], frames=r['frames'], bytes_read=r['bytes_read'],
        statuses=r['status_counts'], selected=r['selected_records']) for r in results], ensure_ascii=True))


def extract_ex_roots(wire, direction):
    """Reusable root-only proposal, deliberately incapable of confirming Hits.

    0e/23 m+21 is the independently used enhancement field in the existing
    Shaman/Illusion reader. Here only the observed 4410+4505 pair is accepted.
    Rune and other envelopes stay disabled until they have their own raw cases.
    """
    if (direction != 'server_to_client' or len(wire) < 21
            or struct.unpack_from('<H', wire, 7)[0]+3 != len(wire)):
        return []
    chains = record_chains(wire)
    if len(chains) != 1:
        return []
    chain = chains[0]
    b = bytes.fromhex(chain['plaintext'])
    if not 0 < u32(b, 10) < 0xffffffff:
        return []
    if any(not 0 < r['actor'] < 0xffffffff for r in chain['records']):
        return []
    roots = []
    for r in chain['records']:
        m = r['marker']
        if (r['type'], r['length']) != (14, 35):
            continue
        if (u32(b, m+3), u32(b, m+21)) != (4410, 4505):
            continue
        roots.append(dict(owner=r['actor'], raw_skill=4410, enhancement=4505,
            marker=m, phase=chain['phase'], skill_byte_offset=m+3,
            enhancement_byte_offset=m+21, level=u32(b, m+7), record=r['record'],
            wire_sha256=sha(wire), canonical_family=4410,
            action='Skill_MagmaWall_EX', static_generators=[127425, 131921, 134590, 134895],
            template=32022, scope='player_action_root_only', confirms_entity_or_hit=False))
    return roots


def roots():
    inventory_data = load(OUT/'dynamic_evidence.json')
    positives, controls, relation_checks, unselected = [], [], [], []
    for summary in inventory_data['windows']:
        name = summary['index']
        d = load(INDEX/name)
        if summary['hits']:
            keys = {(r['frame'], r['marker']) for r in summary['family_owner_actions']}
            for a in d['actions']:
                if (a['frame'], a['marker']) not in keys:
                    continue
                anchor = reopen(d['capture'], a)
                found = extract_ex_roots(bytes.fromhex(a['wire']), a['direction'])
                found = [r for r in found if r['marker'] == a['marker'] and r['owner'] == a['actor']]
                if len(found) == 1:
                    positives.append(dict(index=name, capture=d['capture'], anchor=anchor, root=found[0]))
                else:
                    b = xor(bytes.fromhex(a['wire']), a['phase'])
                    unselected.append(dict(index=name, anchor=anchor, fields=action_fields(a),
                        marker_hex=b[a['marker']:a['marker']+3].hex(),
                        candidate_m21=u32(b, a['marker']+21),
                        complete_chains=len(record_chains(bytes.fromhex(a['wire'])))))
        # One real ordinary-wall control from each already indexed capture.
        h = next((h for h in d['hits'] if h.get('template') == 32015), None)
        if h is not None:
            birth = next(b for b in d['spawns'] if b['entity'] == h['source']
                         and b['generation'] == h['generation'])
            owner = next(o for o in d['records'] if o['entity'] == h['source']
                         and o['generation'] == h['generation'] and o['repeated'] and o['tag'] == 1)
            actions = [a for a in d['actions'] if a['actor'] == owner['owner'] and a['skill'] == 4410
                       and not a['compact'] and a['frame'] < birth['frame']]
            if actions:
                a = actions[-1]
                anchor = reopen(d['capture'], a)
                b = xor(bytes.fromhex(a['wire']), a['phase'])
                m = a['marker']
                controls.append(dict(index=name, capture=d['capture'], anchor=anchor,
                    fields=action_fields(a), enhancement=u32(b, m+21),
                    extracted_roots=extract_ex_roots(bytes.fromhex(a['wire']), a['direction']),
                    context_birth=short(birth), warning='Ordinary-wall context only, not a causal proof'))
        del d
    for sample in inventory_data['samples']:
        root, = (p for p in positives if p['index'] == sample['index'] and p['anchor']['frame'] ==
                 sample['prior_actions'][0]['anchor']['frame'])
        b = bytes.fromhex(sample['birth_plaintext'])
        action = bytes.fromhex(root['root']['record'])
        # Merely record literal observations; no semantics are assigned to +14.
        values = [dict(label=label, value=value, byte_matches=[i for i in range(len(b)-3)
                       if b[i:i+4] == struct.pack('<I', value)]) for label, value in
                  [('owner', root['root']['owner']), ('base', 4410), ('enhancement', 4505)]]
        relation_checks.append(dict(index=sample['index'],
            root_frame=root['anchor']['frame'], birth_frame=sample['anchors']['birth']['frame'],
            root_before_birth=root['anchor']['frame'] < sample['anchors']['birth']['frame'],
            owner_matches_repeated_342c=root['root']['owner'] == sample['owner_fields']['owner'],
            birth_literal_checks_not_semantic_proof=values,
            birth_offset14_u32=u32(b, 14), birth_offset22_u32=u32(b, 22),
            birth_offset14_literal_in_root=[i for i in range(len(action)-3) if action[i:i+4] == b[14:18]],
            verified_creator_instance_or_action_signal_link=None,
            hit_confirmed=False, blocker='EX root is proved; child-specific link selecting this creator over orphan Clone is not proved'))
    result = dict(schema='magma-exact-v28-root', positives=positives, real_controls=controls,
        unselected_family_actions=unselected,
        birth_relation_checks=relation_checks,
        semantic_reference=dict(path='dragon_nest_builder/live_dps/damage_object_v25_shaman_illusion.py',
            lines=[130, 131, 133], role='Existing 0e23 m+21 enhancement grammar; not imported or executed'),
        counters=dict(STATS), confirmed_hits=0)
    save('root_selector_evidence.json', result)
    print(json.dumps(dict(positive_roots=len(positives), unselected=unselected,
        pairs=dict(Counter(f"{p['root']['raw_skill']}+{p['root']['enhancement']}" for p in positives)),
        controls=[dict(index=c['index'], frame=c['anchor']['frame'], enhancement=c['enhancement'],
                       accepted=bool(c['extracted_roots'])) for c in controls],
        relation_checks=relation_checks, counters=dict(STATS)), ensure_ascii=True))


def inventory():
    reports, samples, physical_hits = [], [], []
    hashes = {}
    for name in INDEX_NAMES:
        path = INDEX / name
        hashes[name] = sha(path.read_bytes())
        d = load(path)
        births = [r for r in d['spawns'] if r['template'] == 32022 and not r.get('duplicate_spawn')]
        lives = {(r['entity'], r['generation']) for r in births}
        hits = [r for r in d['hits'] if r.get('template') == 32022]
        owners = [r for r in d['records'] if (r['entity'], r.get('generation')) in lives
                  and r.get('repeated') and r.get('tag') == 1]
        owner_ids = {r['owner'] for r in owners}
        own_actions = [r for r in d['actions'] if (r['actor'], r.get('generation')) in lives]
        player_actions = [r for r in d['actions'] if r['actor'] in owner_ids and not r['compact']]
        wall_actions = [r for r in player_actions if r['skill'] in (4410, 4505, 291122, 291223, 10024410)]
        selected = []
        # Evidence candidates, explicitly not causal associations.
        for b in births:
            o = [r for r in owners if (r['entity'], r['generation']) == (b['entity'], b['generation'])]
            before = [r for r in wall_actions if r['frame'] < b['frame'] and any(r['actor'] == v['owner'] for v in o)]
            selected.append(dict(birth=short(b), nearest_prior_root=short(before[-1]) if before else None,
                warning='Index-order proximity only, NOT a creation link'))
        fields = [hit_fields(h) for h in hits]
        physical_hits.extend(dict(index=name, locator=short(h), wire_sha256=sha(bytes.fromhex(h['wire'])),
            physical={k: f[k] for k in ('source', 'target', 'damage', 'marker', 'critical', 'object', 'result_group')},
            monster_skill=f['monster_skill'], hit_confirmed=False) for h, f in zip(hits, fields))
        reports.append(dict(index=name, capture=d['capture'], births=len(births), hits=len(hits),
            damage=sum(h['damage'] for h in hits), self_slot_counts=dict(Counter(str(f['monster_skill']) for f in fields)),
            context_m114_counts_not_evidence=dict(Counter(str(f['context_m114']) for f in fields)),
            entity_actions=[short(a) for a in own_actions],
            complete_owner_action_skill_counts=dict(Counter(str(a['skill']) for a in player_actions)),
            family_owner_actions=[short(a) for a in wall_actions], birth_context_candidates=selected))
        if hits:
            # Existing first physical Hit per capture; inspect only its life and
            # closest full prior owner action. No claim that this causes birth.
            h = hits[0]
            b, = (r for r in births if (r['entity'], r['generation']) == (h['source'], h['generation']))
            os = [r for r in owners if (r['entity'], r['generation']) == (b['entity'], b['generation'])
                  and b['frame'] <= r['frame'] <= h['frame']]
            assert len({o['owner'] for o in os}) == 1
            o = os[0]
            candidates = [a for a in player_actions if a['actor'] == o['owner'] and a['frame'] < b['frame']]
            family = [a for a in wall_actions if a['actor'] == o['owner'] and a['frame'] < b['frame']]
            pick = {(a['frame'], a['marker']): a for a in candidates[-1:]+family[-1:]}
            raws = {k: reopen(d['capture'], v) for k, v in (('birth', b), ('owner', o), ('hit', h))}
            bw = bytes.fromhex(raws['birth']['wire'])
            assert len(bw) == 88 and bw[5:9] == bytes.fromhex('06275500')
            plain = xor(bw, 5)
            assert u32(plain, 10) == b['entity'] and u32(plain, 18) == 32022
            samples.append(dict(index=name, capture=d['capture'], anchors=raws,
                owner_fields=owner_fields(bytes.fromhex(o['wire']), b['entity']), hit_fields=hit_fields(h),
                birth_plaintext=plain.hex(),
                birth_u32_grid=[dict(offset=p, hex=plain[p:p+4].hex(), value=u32(plain, p)) for p in range(10, 85, 4)],
                prior_actions=[dict(anchor=reopen(d['capture'], a), fields=action_fields(a),
                    reason='candidate only; no explicit action-instance to birth identity') for a in pick.values()]))
        del d
    result = dict(schema='magma-exact-v28-inventory', hashes=hashes, windows=reports, samples=samples,
                  existing_physical_hits=physical_hits,
                  counters=dict(STATS), confirmed_hits=0, scope='Six pre-existing indexes, not current coverage denominator')
    save('dynamic_evidence.json', result)
    print(json.dumps(dict(windows=[{k: r[k] for k in ('index', 'births', 'hits', 'damage', 'self_slot_counts',
          'entity_actions', 'complete_owner_action_skill_counts')} for r in reports],
          samples=[dict(index=s['index'], birth=s['anchors']['birth']['frame'],
          hit=s['anchors']['hit']['frame'], birth_grid=s['birth_u32_grid'],
          actions=[dict(frame=a['anchor']['frame'], actor=a['fields']['actor'], skill=a['fields']['skill'])
                   for a in s['prior_actions']]) for s in samples], counters=dict(STATS)), ensure_ascii=True))


def verify():
    roots_data = load(OUT/'root_selector_evidence.json')
    static_data = load(OUT/'static_evidence.json')
    dynamic_data = load(OUT/'dynamic_evidence.json')
    tests = []

    class EvidenceChecks(unittest.TestCase):
        def test_real_positive_roots(self):
            self.assertEqual(len(roots_data['positives']), 13)
            for p in roots_data['positives']:
                found = extract_ex_roots(bytes.fromhex(p['anchor']['wire']), p['anchor']['direction'])
                self.assertIn(p['root'], found)
                self.assertFalse(p['root']['confirms_entity_or_hit'])

        def test_real_non_ex_controls(self):
            self.assertEqual(len(roots_data['real_controls']), 2)
            for p in roots_data['real_controls']:
                self.assertEqual(p['enhancement'], 0)
                self.assertFalse(extract_ex_roots(bytes.fromhex(p['anchor']['wire']), 'server_to_client'))

        def test_real_unapproved_0e1f(self):
            rows = roots_data['unselected_family_actions']
            self.assertEqual(len(rows), 3)
            for p in rows:
                self.assertEqual(p['marker_hex'], '0e1f00')
                self.assertEqual(p['candidate_m21'], 4505)
                self.assertFalse(extract_ex_roots(bytes.fromhex(p['anchor']['wire']), 'server_to_client'))

        def test_mutation_guards(self):
            for p in roots_data['positives'][:2]:
                wire = bytes.fromhex(p['anchor']['wire'])
                phase, m = p['root']['phase'], p['root']['marker']
                self.assertFalse(extract_ex_roots(wire, 'client_to_server'))
                self.assertFalse(extract_ex_roots(wire, None))
                self.assertFalse(extract_ex_roots(wire[:-1], 'server_to_client'))
                # Encrypt each mutated plaintext with the same phase. m114 is
                # absent from this input: the root reader never sees a Hit.
                for label, pos, value in [('wrong_base', m+3, 4407), ('no_ex', m+21, 0),
                                          ('wrong_ex', m+21, 4503), ('invalid_actor', m-4, 0)]:
                    b = bytearray(xor(wire, phase))
                    struct.pack_into('<I', b, pos, value)
                    self.assertFalse(extract_ex_roots(xor(bytes(b), phase), 'server_to_client'), label)
                b = bytearray(xor(wire, phase))
                struct.pack_into('<H', b, m+1, 34)
                self.assertFalse(extract_ex_roots(xor(bytes(b), phase), 'server_to_client'))
                b = bytearray(xor(wire, phase))
                struct.pack_into('<I', b, m+21, 0)
                struct.pack_into('<I', b, m+25, 4505)
                self.assertFalse(extract_ex_roots(xor(bytes(b), phase), 'server_to_client'))

        def test_static_routes_are_not_template_whitelist(self):
            ex = [r for r in static_data['dnt_routes'] if r['skill'] == 4505 and r['apply_type'] == 0]
            self.assertEqual(len(ex), 1)
            self.assertEqual(ex[0]['row_id'], 72949)
            self.assertEqual(ex[0]['generators'], [dict(offset=o, type=48, template=32022)
                                                  for o in (127425, 131921, 134590, 134895)])
            base = [r for r in static_data['dnt_routes'] if r['skill'] == 4410]
            self.assertTrue(base)
            self.assertEqual({g['template'] for r in base for g in r['generators']}, {32015})
            self.assertFalse(any(r['action'] == 'Skill_MagmaWall_EX_Clone[1]' for r in static_data['dnt_routes']))

        def test_existing_physical_hits_stay_hold(self):
            rows = dynamic_data['existing_physical_hits']
            self.assertEqual(len(rows), 97)
            self.assertEqual(sum(r['physical']['damage'] for r in rows), 42755491)
            self.assertEqual({r['monster_skill'] for r in rows}, {0xffffffff})
            self.assertFalse(any(r['hit_confirmed'] for r in rows))
            self.assertTrue(all(not r['entity_actions'] for r in dynamic_data['windows']))
            self.assertTrue(all(r['verified_creator_instance_or_action_signal_link'] is None
                                for r in roots_data['birth_relation_checks']))

    class Results(unittest.TestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            tests.append(dict(test=test._testMethodName, status='passed'))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EvidenceChecks)
    result = Results()
    suite.run(result)
    assert result.wasSuccessful(), result.errors+result.failures
    proposal = dict(schema='magma-exact-route-v28-proposal', production_changed=False,
        whole_template_32022_enabled=False, direct_hit_upgrade_enabled=False,
        confirmed_new_hits=0, accepted_direct_hit_routes=[],
        root_only_extractor=dict(path='tools/probe_magma_exact_route_v28.py', function='extract_ex_roots',
            enabled_for_diagnostic_only=True, server_record='0e2300', base_offset='m+3',
            enhancement_offset='m+21', accepted_raw_pair=[4410, 4505], real_positive_records=13,
            canonical_family=4410, template_generated_by_static_action=32022),
        unapproved_formats=[dict(marker='0e1f00', real_records=3, candidate_m21=4505)],
        missing_evidence=['Independent child-specific creator/action/signal link for current birth',
            'Alternatively independently proven Clone entry association closing all creators'],
        integration_contract=['Extract and retain full root bytes/phase/marker and resource certificate only.',
            'Do not modify the template whitelist, m102 whitelist, physical Hit admission or skill labels.',
            'A future accepted link must carry the same birth fingerprint/entity/generation/owner and an independently verified creator selector.',
            'At Hit time still require Main current owner/lifecycle and PvE/admission guards; no cached owner, cross-life reuse or cast proximity substitute.',
            'If the birth link or static certificate is missing/ambiguous/conflicting, keep nondeterministic.',
            'Only after that missing link is independently evidenced should a narrow annotator be proposed; no wider replay is authorized here.'],
        rejected_shortcuts=['m114', 'legacy_name', 'time_or_distance_only', 'four_births_vs_three',
            'same_owner_only', 'clone_suffix_or_similar_bytes', 'spawn_opaque_integer_as_ACT_ordinal'],
        scope='Limited independent evidence handoff; no production replay, no integration or coverage increase claimed')
    save('proposal.json', proposal)
    files = ['dynamic_evidence.json', 'static_evidence.json', 'bounded_context_evidence.json',
             'root_selector_evidence.json', 'proposal.json']
    save('verification.json', dict(tests_run=result.testsRun, failures=0, errors=0, tests=tests,
        script_sha256=sha(Path(__file__).read_bytes()), evidence_hashes={n: sha((OUT/n).read_bytes()) for n in files},
        coverage_increment=0, limitation='Root/parser/static checks only; no Main owner/lifecycle tests or replay claimed'))
    print(json.dumps(dict(tests_run=result.testsRun, failures=0, errors=0, root_records=13,
                          confirmed_new_hits=0), ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inventory', 'static', 'contexts', 'roots', 'verify'])
    args = parser.parse_args()
    {'inventory': inventory, 'static': static, 'contexts': contexts, 'roots': roots, 'verify': verify}[args.mode]()
