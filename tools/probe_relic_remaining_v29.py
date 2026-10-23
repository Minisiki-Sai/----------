"""Bounded v29 relic audit: stdlib, exact tables/files, existing raw anchors.

Never imports production or a replay reader. Writes only the v29 output dir.
No resource glob/rglob, capture-wide scan, workers, cache or bytecode writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import struct
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'outputs/relic_remaining_v29_20260908'
OLD = ROOT/'outputs/v26_relic_wall_self_attack_20260908'
INVENTORY = ROOT/'outputs/compact_entity_selectors_20260908/self_slot_inventory.json'
RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
TARGETS = {30001, 30251, 30311, 30401, 30501}
KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')
STATS = Counter()
MANIFEST = {}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read(path, max_size=8_000_000):
    assert path.stat().st_size <= max_size, f'bounded input too large: {path}'
    b = path.read_bytes()
    MANIFEST[str(path)] = dict(size=len(b), sha256=sha(b))
    STATS['source_bytes'] += len(b)
    return b


def load(path):
    return json.loads(read(path).decode('utf-8-sig'))


def save(name, data):
    assert Path(name).name == name and name.endswith('.json')
    OUT.mkdir(exist_ok=True)
    (OUT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def u32(b, p):
    return struct.unpack_from('<I', b, p)[0]


def xor(wire, phase):
    return wire[:9]+bytes(v ^ KEY[(i-phase) % 13] for i, v in enumerate(wire[9:], 9))


class Reader:
    def __init__(self, b):
        self.b, self.p = b, 0

    def take(self, n):
        assert 0 <= n <= len(self.b)-self.p
        out = self.b[self.p:self.p+n]
        self.p += n
        return out

    def number(self, fmt):
        return struct.unpack('<'+fmt, self.take(struct.calcsize('<'+fmt)))[0]

    def count(self):
        n = self.number('i')
        assert 0 <= n <= 65536
        return n

    def string(self):
        raw = self.take(self.count()).rstrip(b'\0')
        for enc in ('utf-8', 'gb18030', 'latin1'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                pass


def dnt(name, predicate):
    b = read(RESOURCE/'ext'/name)
    r = Reader(b)
    r.take(4)
    columns, count = r.number('H'), r.number('I')
    fields = [(r.take(r.number('H')).decode('ascii'), r.number('B')) for _ in range(columns)]
    found = []
    for _ in range(count):
        start = r.p
        row = {'RowId': r.number('I')}
        for field, typ in fields:
            if typ == 1:
                value = r.take(r.number('H')).decode('utf-8', errors='replace')
            elif typ in (2, 3):
                value = r.number('i')
            elif typ in (4, 5):
                value = r.number('f')
            else:
                raise ValueError((name, typ))
            row[field] = value
        if predicate(row):
            found.append(dict(table=name, offset=start, end=r.p, row=row,
                              row_sha256=sha(b[start:r.p])))
    assert r.p == len(b), (name, 'trailing bytes', len(b)-r.p)
    return found


def act(rel):
    r = Reader(read(RESOURCE/rel))
    assert r.take(32).startswith(b'Eternity Action File')
    version, count = r.number('i'), r.count()
    actions = []
    for ordinal in range(count):
        a = dict(offset=r.p, ordinal=ordinal, name=r.string(), animation=r.string(),
            frames=r.number('i'), next_action=r.string(), blend=r.number('i'), next_frame=r.number('i'))
        r.take({1: 0, 2: 4, 3: 8, 4: 9, 5: 13, 6: 14}[version])
        signals = []
        for signal_ordinal in range(r.count()):
            s = dict(offset=r.p, ordinal=signal_ordinal, type=r.number('i'), start=r.number('i'),
                     end=r.number('i'), order=r.number('i'))
            s['u32'] = list(struct.unpack('<64I', r.take(256)))
            s['tables'] = {}
            for width, label in ((8, 'vec2'), (12, 'vec3'), (16, 'vec4')):
                n = r.count()
                indexes = [r.number('i') for _ in range(n)]
                values = [list(struct.unpack('<'+'f'*(width//4), r.take(width))) for _ in range(n)]
                if n:
                    s['tables'][label] = list(zip(indexes, values))
            indexes = [r.number('i') for _ in range(r.count())]
            s['strings'] = [[i, r.string()] for i in indexes]
            if version >= 6:
                r.take(1)
            signals.append(s)
        a.update(signals=signals, end_offset=r.p)
        actions.append(a)
    assert r.p == len(r.b)
    return dict(path=rel, version=version, actions=actions)


def static():
    old = load(OLD/'machine_map_extension.json')
    inventory = load(INVENTORY)
    inv = [r for r in inventory['rows'] if r['template'] in TARGETS]
    monsters = dnt('monstertable_quest.dnt', lambda r: r['RowId'] in TARGETS)
    assert len(monsters) == len(TARGETS)
    actor_ids = {m['row']['_ActorTableID'] for m in monsters}
    actors = dnt('actortable.dnt', lambda r: r['RowId'] in actor_ids)
    file_ids = {a['row']['_ActName'] for a in actors}
    files = dnt('filetable.dnt', lambda r: r['RowId'] in file_ids)
    table_ids = {m['row']['_SkillTable'] for m in monsters}
    slots = dnt('monsterskilltable.dnt', lambda r: r['RowId'] in table_ids)
    skills = {v for t in slots for k, v in t['row'].items() if k.startswith('_SkillIndex') and v > 0}
    templates = []
    for m in monsters:
        a, = (r for r in actors if r['row']['RowId'] == m['row']['_ActorTableID'])
        f, = (r for r in files if r['row']['RowId'] == a['row']['_ActName'])
        rel = f['row']['_FileName'].replace('\\', '/').lower()
        if '/' not in rel:
            rel = 'weapon/'+rel
        assert (RESOURCE/rel).is_file(), rel
        ai_rel = 'script/monsterai/'+m['row']['_CustomAI'].lower()
        source = read(RESOURCE/ai_rel).decode('utf-8-sig')
        code = '\n'.join(line.split('--', 1)[0] for line in source.splitlines())
        ai_skills = sorted({int(x) for x in re.findall(r'\bskill_index\s*=\s*(\d+)', code)})
        skills.update(ai_skills)
        templates.append(dict(template=m['row']['RowId'], monster=m, actor=a, file=f,
            ai_path=ai_rel, ai_source=source, ai_skills=ai_skills, act=act(rel)))
    # Explicit installed slots and actual AI numeric references only; no global
    # search for similar relic names. Include the player entries for exact DNT
    # normalization/processor inspection, not because m114 carried those IDs.
    requested = skills | {3409, 3502, 3412, 3422, 3423, 3503}
    skill_rows, level_rows = [], []
    for name in ('skilltable_character.dnt', 'skilltable_monster.dnt', 'skilltable_rune.dnt'):
        skill_rows.extend(dnt(name, lambda r: r['RowId'] in requested))
    for name in ('skillleveltable_charactercleric.dnt', 'skillleveltable_rune.dnt'):
        level_rows.extend(dnt(name, lambda r: r.get('_SkillIndex') in requested))
    summaries = []
    for t in templates:
        action_names = {a['name'] for a in t['act']['actions']}
        relevant = [r for r in level_rows if r['row'].get('_ProcessParam1') in action_names]
        summaries.append(dict(template=t['template'], ai=t['ai_skills'], act=t['act']['path'],
            bindings=[dict(table=r['table'], offset=r['offset'], row=r['row']['RowId'],
                skill=r['row']['_SkillIndex'], level=r['row'].get('_SkillLevel'),
                apply_type=r['row'].get('_ApplyType'), action=r['row'].get('_ProcessParam1')) for r in relevant],
            edges=[dict(action=a['name'], action_ordinal=a['ordinal'], signal_ordinal=s['ordinal'],
                offset=s['offset'], type=s['type'], first=s['u32'][0]) for a in t['act']['actions']
                for s in a['signals'] if s['type'] in (4, 15, 48, 126)]))
    save('static_evidence.json', dict(schema='relic-v29-static', inventory=inv,
        old_accepted=old['accepted'], installed_slots=slots, requested_skill_ids=sorted(requested),
        templates=templates, skill_rows=skill_rows, level_rows=level_rows,
        summaries=summaries, manifest=MANIFEST, counters=dict(STATS)))
    print(json.dumps(dict(summaries=[dict(template=t['template'], ai=t['ai'], act=t['act'],
        bindings=[dict(key=list(k), rows=v) for k,v in Counter((r['table'], r['skill'], r['action']) for r in t['bindings']).items()],
        edges=t['edges']) for t in summaries],
        missing_ai_levels=[s for t in templates for s in t['ai_skills'] if not any(r['row']['_SkillIndex']==s for r in level_rows)],
        files=len(MANIFEST), bytes=STATS['source_bytes']), ensure_ascii=True))


def records(wire, phase):
    b = xor(wire, phase)
    pos, out = 14, []
    while pos+7 <= len(b):
        m = pos+4
        size = struct.unpack_from('<H', b, m+1)[0]
        end = m+3+size
        if not size or end > len(b):
            return None
        out.append(dict(actor=u32(b, pos), marker=m, type=b[m], size=size, raw=b[pos:end].hex()))
        pos = end
    return out if pos == len(b) else None


def seek(capture, offset, expected_wire):
    with Path(capture).open('rb') as f:
        f.seek(offset)
        timestamp, adapter, original, size = struct.unpack('<dBII', f.read(17))
        assert 42 <= size <= 65577
        raw = f.read(size)
    STATS['frame_seeks'] += 1
    STATS['frame_bytes'] += size+17
    assert len(raw) == size and raw[12:14] == b'\x08\x00' and raw[14] >> 4 == 4 and raw[23] == 17
    ip_end = 14+struct.unpack_from('>H', raw, 16)[0]
    udp = 14+4*(raw[14]&15)
    assert udp+8 <= ip_end <= len(raw) and struct.unpack_from('>H', raw, 20)[0]&0x3fff == 0
    assert udp+struct.unpack_from('>H', raw, udp+4)[0] == ip_end
    wire = raw[udp+8:ip_end]
    assert wire.hex() == expected_wire
    assert struct.unpack_from('<H', wire, 7)[0]+3 == len(wire)
    src, dst = (ipaddress.IPv4Address(raw[p:p+4]) for p in (26, 30))
    assert src.is_global and dst.is_private
    return dict(time=timestamp, adapter=adapter, original=original, direction='server_to_client',
        file_offset=offset, raw_frame=raw.hex(), raw_frame_sha256=sha(raw), wire=wire.hex(),
        wire_sha256=sha(wire), independently_reopened=True)


def raw():
    inv = load(INVENTORY)
    old = load(OLD/'raw_evidence.json')
    rows = []
    for item in inv['rows']:
        if item['template'] not in TARGETS:
            continue
        e = item['example']
        anchor = seek(e['capture'], e['offset'], e['wire'])
        wire = bytes.fromhex(e['wire'])
        b, m = xor(wire, e['phase']), e['marker']
        chain = records(wire, e['phase'])
        assert chain is not None and any(r['marker'] == m and r['type'] == 8 and r['size'] == 115 for r in chain)
        fields = dict(marker=m, phase=e['phase'], source=u32(b,m+8), target=u32(b,m-4),
            damage=struct.unpack_from('<Q', b,m+19)[0], object=u32(b,m+13), result_group=u32(b,m+4),
            monster_skill=u32(b,m+102), context_m114_only=u32(b,m+114),
            own_slot_bytes=b[m+102:m+106].hex(), context_slot_bytes=b[m+114:m+118].hex(), plaintext=b.hex())
        assert fields['monster_skill'] == item['self_skill']
        rows.append(dict(template=item['template'], candidate_hits=item['candidate_hits'],
            mapped_flag_historical_only=item['mapped'], capture=e['capture'], frame=e['frame'],
            anchor=anchor, fields=fields, complete_records=chain))
    old_samples = [s for s in old['samples'] if s['template'] in TARGETS]
    old_rows = [r for r in old['rows'] if r['template'] in TARGETS]
    save('raw_evidence.json', dict(schema='relic-v29-raw', inventory_anchors=rows,
        prior_samples=old_samples, prior_indexed_rows=old_rows, manifest=MANIFEST, counters=dict(STATS),
        scope='Seven existing inventory representatives; old relic anchors copied, no capture replay'))
    print(json.dumps([dict(template=r['template'], hits=r['candidate_hits'], frame=r['frame'],
        file_offset=r['anchor']['file_offset'], fields=r['fields'],
        records=r['complete_records']) for r in rows], ensure_ascii=True))


def windows():
    old = load(OLD/'raw_evidence.json')
    seeds = []
    for s in old['samples']:
        if s['template'] == 30311:
            seeds.append(dict(template=30311, capture=s['capture'], birth=s['birth'],
                owner=s['owner_record'], hit=s['hit_anchor']))
    # One exact old index, already identified/hash-pinned by the old audit.
    path = ROOT/'outputs/v25_entity_owner_independent/0826硬核曜_0_180.json'
    index = load(path)
    assert MANIFEST[str(path)]['sha256'] == old['input_hashes'][str(path)]
    birth, = (b for b in index['spawns'] if b['template'] == 30251 and b['entity'] == 83
              and b['frame'] == 10724 and not b['duplicate_spawn'])
    owner, = (r for r in index['records'] if r['entity'] == 83 and r['generation'] == birth['generation']
              and r['repeated'] and r['tag'] == 1)
    hit, = (h for h in index['hits'] if h['frame'] == 10982 and h['source'] == 83)
    seeds.append(dict(template=30251, capture=index['capture'], birth=birth, owner=owner, hit=hit))
    compact = [a for a in index['actions'] if a.get('template') in (30251, 30311)]
    # Compact byte is retained as a negative mapping example, not an ACT index.
    compact_examples = []
    for tid in (30251, 30311):
        a = next((a for a in compact if a['template'] == tid), None)
        if a:
            compact_examples.append(dict(template=tid, frame=a['frame'], record=a['record'],
                skill_byte=a['skill'], compact=a['compact'],
                anchor=seek(index['capture'], a['file_offset'], a['wire'])))
    del index
    results = []
    for seed in seeds:
        start, end = seed['birth']['file_offset'], seed['hit']['file_offset']
        entity, player = seed['birth']['entity'], seed['owner']['owner']
        assert end-start <= 65536
        assert seed['hit']['frame']-seed['birth']['frame']+1 <= 320
        anchors = {k: seek(seed['capture'], seed[k]['file_offset'], seed[k]['wire']) for k in ('birth', 'owner', 'hit')}
        frames, selected, nine = [], [], []
        with Path(seed['capture']).open('rb') as f:
            f.seek(start)
            while f.tell() <= end:
                assert len(frames) < 320
                offset = f.tell()
                timestamp, adapter, original, size = struct.unpack('<dBII', f.read(17))
                assert 0 < size <= 65577
                raw_frame = f.read(size)
                assert len(raw_frame) == size
                STATS['window_bytes'] += size+17
                entry = dict(frame=seed['birth']['frame']+len(frames), file_offset=offset,
                    time=timestamp, size=size, raw_sha256=sha(raw_frame))
                if (len(raw_frame) < 42 or raw_frame[12:14] != b'\x08\x00'
                        or raw_frame[14] >> 4 != 4 or raw_frame[23] != 17):
                    entry['status'] = 'non_plain_ipv4_udp'
                    frames.append(entry)
                    continue
                udp = 14+4*(raw_frame[14]&15)
                ip_end = 14+struct.unpack_from('>H', raw_frame, 16)[0]
                if (udp+8 > ip_end or ip_end > size or struct.unpack_from('>H', raw_frame, 20)[0]&0x3fff
                        or udp+struct.unpack_from('>H', raw_frame, udp+4)[0] != ip_end):
                    entry['status'] = 'unsupported_transport'
                    frames.append(entry)
                    continue
                wire = raw_frame[udp+8:ip_end]
                entry.update(wire=wire.hex(), wire_sha256=sha(wire))
                src, dst = (ipaddress.IPv4Address(raw_frame[p:p+4]) for p in (26,30))
                direction = 'server_to_client' if src.is_global and dst.is_private else 'client_to_server'
                entry['direction'] = direction
                if len(wire) < 21 or struct.unpack_from('<H', wire, 7)[0]+3 != len(wire):
                    entry['status'] = 'unsupported_envelope'
                elif len(wire) == 88 and wire[5:9] == b'\x06\x27\x55\x00':
                    b = xor(wire, 5)
                    entry.update(status='spawn88', entity=u32(b,10), template=u32(b,18))
                else:
                    chains = [(p, rs) for p in range(13) if (rs := records(wire, p)) is not None]
                    if len(chains) != 1:
                        entry.update(status='unsupported_or_ambiguous_records', candidates=len(chains))
                    else:
                        phase, rs = chains[0]
                        entry.update(status='complete_chain', phase=phase, records=rs)
                        for r in rs:
                            tagged = dict(frame=entry['frame'], file_offset=offset, phase=phase,
                                          direction=direction, **r)
                            if r['actor'] in (entity, player):
                                selected.append(tagged)
                            if r['type'] == 9:
                                b = xor(wire, phase)
                                m = r['marker']
                                if r['size'] >= 16:
                                    tagged.update(object=u32(b,m+8), projectile_template=u32(b,m+12))
                                if r['size'] == 97:
                                    tagged.update(action_ordinal=struct.unpack_from('<H',b,m+48)[0],
                                        signal_ordinal=struct.unpack_from('<H',b,m+50)[0],
                                        repeated_action=u32(b,m+64), repeated_owner=u32(b,m+44))
                                nine.append(tagged)
                frames.append(entry)
        results.append(dict(template=seed['template'], capture=seed['capture'], entity=entity, owner=player,
            anchors=anchors, birth_frame=seed['birth']['frame'], hit_frame=seed['hit']['frame'],
            start_offset=start, end_offset=end, frame_count=len(frames),
            status_counts=dict(Counter(r['status'] for r in frames)), selected_records=selected,
            all_complete_09_records=nine, raw_frames=frames,
            limitation='Unknown envelopes remain unknown; no whole-capture absence claim.'))
    save('bounded_windows.json', dict(schema='relic-v29-windows', windows=results,
        compact_examples=compact_examples, manifest=MANIFEST, counters=dict(STATS),
        bounds=dict(max_windows=3,max_frames_per_window=320,max_offset_gap=65536)))
    print(json.dumps(dict(windows=[dict(template=w['template'], entity=w['entity'],
        birth_frame=w['birth_frame'], hit_frame=w['hit_frame'], frames=w['frame_count'],
        statuses=w['status_counts'], all09=len(w['all_complete_09_records']),
        own09=[r for r in w['all_complete_09_records'] if r['actor'] in (w['entity'],w['owner'])],
        self_actions=[r for r in w['selected_records'] if r['actor'] == w['entity'] and r['type'] in (3,14,18)])
        for w in results], compact=[dict(template=a['template'], frame=a['frame'],
        skill_byte=a['skill_byte'], record=a['record']) for a in compact_examples], counters=dict(STATS)),ensure_ascii=True))


def explicit_own_slot(wire, marker, phase, direction):
    """Diagnostic exact own-slot reader. Never maps a player skill or a Hit."""
    if (direction != 'server_to_client' or len(wire) < 21
            or struct.unpack_from('<H', wire, 7)[0]+3 != len(wire)):
        return None
    chain = records(wire, phase)
    if chain is None or not any(r['marker'] == marker and (r['type'],r['size']) == (8,115) for r in chain):
        return None
    b, m = xor(wire, phase), marker
    if b[m+12] != 0 or b[m+106:m+110] != bytes(4):
        return None
    value = u32(b,m+102)
    return value if value not in (0,65535,0xffffffff) else None


def finalize():
    s = load(OUT/'static_evidence.json')
    r = load(OUT/'raw_evidence.json')
    w = load(OUT/'bounded_windows.json')
    seen = {(a['template'],a['fields']['monster_skill']):a['candidate_hits'] for a in r['inventory_anchors']}
    candidates = []
    for t in s['summaries']:
        groups = {}
        for binding in t['bindings']:
            key = (binding['skill'], binding['action'], binding['table'])
            groups.setdefault(key, []).append(binding)
        for (skill, action, table), rows in groups.items():
            if table != 'skillleveltable_rune.dnt' and t['template'] not in (30251,30311):
                continue
            candidates.append(dict(template=t['template'], own_skill=skill, action=action,
                table=table, first_row=min(x['row'] for x in rows), binding_rows=rows,
                raw_observed_hits=seen.get((t['template'],skill),0),
                ai_explicit_call=skill in t['ai'], actor_act=t['act'],
                direct_signals=[e for e in t['edges'] if e['action'] == action and e['type'] == 4],
                player_skill=None, ready_for_hit_upgrade=False,
                status='static-entry-only; no observed exact self slot for this pair in the supplied inventory'))
    proposal = dict(schema='relic-remaining-v29-proposal', production_changed=False,
        accepted_new_pairs=[], accepted_projectile_bridges=[], whole_template_accepted=[],
        confirmed_new_hits=0, target_hold_counts={'30311':21,'30251':20},
        other_sentinel_hold_counts={'30401':2,'30501':19},
        statically_resolved_candidates=candidates,
        explicit_static_negative_examples=[
            dict(template=30311, installed_skill=292224, rune_row=29222401,
                 requested_action='Attack_Hold_Light', missing_in_assigned_actor_act=True),
            dict(template=30251, installed_skill=293131, rune_row=29313101,
                 requested_action='Cure_Mutation', missing_in_assigned_actor_act=True)],
        existing_positive_control=dict(template=30501, own_skill=30616, player_skill=3409,
            prior_proposal='outputs/v26_relic_wall_self_attack_20260908/machine_map_extension.json',
            new_coverage_claim=False),
        integration_contract=[
            'Merge the static DNT rows as diagnostic catalog data only; do not add an enabled attribution rule.',
            'Require an actual non-sentinel own m102 or an independently proved complete actor/action/signal/object bridge.',
            'm114 is creator context and cannot fill a missing own skill; opaque compact byte3 cannot choose an ACT action.',
            'A future own-skill rule needs exact installed skill plus actor-action proof, player-family normalization and actual raw case.',
            'A future projectile bridge needs matching entity/owner, exact typed15/48 namespace and source/action/signal mapping, full object identity and same generation.',
            'After original direct-Hit admission, require Main current owner/lifecycle/PvE guards; fail closed on missing/ambiguous/conflicting state.',
            'Do not change physical damage, source, target, Hit count or status/healing admission.'],
        limits='Specified historical six-window inventory; not current production coverage. No full replay or duck investigation.')
    tests = []

    class Checks(unittest.TestCase):
        def test_raw_positive_slots(self):
            counts = {}
            for a in r['inventory_anchors']:
                v = explicit_own_slot(bytes.fromhex(a['anchor']['wire']), a['fields']['marker'],
                    a['fields']['phase'], a['anchor']['direction'])
                if v is not None:
                    counts[(a['template'],v)] = a['candidate_hits']
            self.assertEqual(counts, {(30001,30612):131,(30401,30615):28,(30501,30616):875})

        def test_real_sentinels_rejected(self):
            negatives = [a for a in r['inventory_anchors'] if a['fields']['monster_skill'] == 0xffffffff]
            self.assertEqual({(a['template'],a['candidate_hits']) for a in negatives},
                             {(30311,21),(30251,20),(30401,2),(30501,19)})
            for a in negatives:
                self.assertIsNone(explicit_own_slot(bytes.fromhex(a['anchor']['wire']),
                    a['fields']['marker'],a['fields']['phase'],'server_to_client'))

        def test_context_not_own_skill_and_integrity_guards(self):
            a = next(a for a in r['inventory_anchors'] if a['fields']['monster_skill'] == 30616)
            wire = bytes.fromhex(a['anchor']['wire'])
            m, phase = a['fields']['marker'], a['fields']['phase']
            for context in (0,3422,3423,3503,0xffffffff):
                b = bytearray(xor(wire,phase))
                struct.pack_into('<I',b,m+114,context)
                self.assertEqual(explicit_own_slot(xor(b,phase),m,phase,'server_to_client'),30616)
            for own in (0,65535,0xffffffff):
                b = bytearray(xor(wire,phase))
                struct.pack_into('<I',b,m+102,own)
                struct.pack_into('<I',b,m+114,30616)
                self.assertIsNone(explicit_own_slot(xor(b,phase),m,phase,'server_to_client'))
            self.assertIsNone(explicit_own_slot(wire[:-1],m,phase,'server_to_client'))
            self.assertIsNone(explicit_own_slot(wire,m,phase,'client_to_server'))
            self.assertIsNone(explicit_own_slot(wire,m+1,phase,'server_to_client'))

        def test_rune_exact_entries_are_real_but_unobserved(self):
            expected={(30251,293129):('Cure',1688),(30251,293130):('Cure',1688),
                (30251,283001):('Cure_Shield',38558),(30251,283002):('Cure_Shield',38558),
                (30251,283003):('Cure',1688),(30251,283004):('Cure',1688),
                (30401,291172):('Attack',2329),(30001,293132):('Skill_Shoot',6763)}
            for pair,(action,offset) in expected.items():
                c, = (x for x in candidates if (x['template'],x['own_skill']) == pair)
                self.assertEqual(c['action'],action)
                self.assertIn(offset,[e['offset'] for e in c['direct_signals']])
                self.assertEqual(c['raw_observed_hits'],0)
                self.assertFalse(c['ready_for_hit_upgrade'])

        def test_installed_does_not_mean_action_exists(self):
            for tid,skill,name in ((30251,293131,'Cure_Mutation'),(30311,292224,'Attack_Hold_Light')):
                t, = (t for t in s['templates'] if t['template'] == tid)
                self.assertNotIn(name,[a['name'] for a in t['act']['actions']])
                self.assertTrue(any(row['row']['_SkillIndex'] == skill and row['row']['_ProcessParam1'] == name
                                    for row in s['level_rows']))

        def test_foreign09_not_relic_bridge(self):
            self.assertEqual([len(x['all_complete_09_records']) for x in w['windows']],[4,13,19])
            for x in w['windows']:
                self.assertFalse(any(a['actor'] in (x['entity'],x['owner']) for a in x['all_complete_09_records']))
            self.assertTrue(all(e['type'] == 4 for t in s['summaries'] for e in t['edges']))
            self.assertFalse(proposal['accepted_projectile_bridges'])

        def test_compact_not_act_ordinal(self):
            self.assertEqual(len(w['compact_examples']),2)
            for a in w['compact_examples']:
                self.assertTrue(a['compact'])
                self.assertEqual(a['skill_byte'],3)
                t, = (t for t in s['templates'] if t['template'] == a['template'])
                self.assertEqual(t['act']['actions'][3]['name'],'Stand')
            self.assertFalse(proposal['accepted_new_pairs'])

    class Result(unittest.TestResult):
        def addSuccess(self,test):
            super().addSuccess(test)
            tests.append(dict(test=test._testMethodName,status='passed'))

    result=Result()
    unittest.defaultTestLoader.loadTestsFromTestCase(Checks).run(result)
    assert result.wasSuccessful(), result.failures+result.errors
    # Check that all audited old inputs and exact resources still match. This
    # does not claim production remained unchanged while other agents work.
    manifests = {}
    for data in (s,r,w):
        manifests.update(data['manifest'])
    for path, info in manifests.items():
        assert sha(Path(path).read_bytes()) == info['sha256'], ('input drift',path)
    save('proposal.json',proposal)
    save('verification.json',dict(tests_run=result.testsRun,failures=0,errors=0,tests=tests,
        input_hashes_unchanged=True,checked_inputs=len(manifests),
        script_sha256=sha(Path(__file__).read_bytes()),
        evidence_hashes={name:sha((OUT/name).read_bytes()) for name in
            ('static_evidence.json','raw_evidence.json','bounded_windows.json','proposal.json')},
        confirmed_new_hits=0,scope='Independent raw/static checks only; no Main replay/lifecycle regression claimed'))
    print(json.dumps(dict(tests=result.testsRun,failures=0,static_candidates=len(candidates),
        accepted_new_pairs=0,accepted_projectile_bridges=0,confirmed_new_hits=0),ensure_ascii=True))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['static', 'raw', 'windows', 'finalize'])
    args = p.parse_args()
    {'static': static, 'raw': raw, 'windows': windows, 'finalize': finalize}[args.mode]()
