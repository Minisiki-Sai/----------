"""Bounded canonical-family review, stdout only; never a production mapper.

Two fixed ACTs, five fixed DNTs, one assigned AI, existing small JSON evidence.
No enumeration, replay, networking, subprocesses, capture opens, or file writes.
Unknown ACT slots and m114 are retained as observations, not given semantics.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OLD = 'outputs/v26_magmawall_family_closure_20260908/'
WALL = 'char/monster/ac_magmawall/academic_magmawall_ex.act'
ADEPT = 'char/player/academic/academic_skill_2st_adept.act'
NORMAL = 'Skill_MagmaWall_EX'
CLONE = 'Skill_MagmaWall_EX_Clone[1]'
TABLES = ('skilltable_character.dnt', 'skillleveltable_characteracademic.dnt',
          'monstertable_quest.dnt', 'monsterskilltable.dnt',
          'monsterskilltable_transform.dnt')
KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')


class EvidenceError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise EvidenceError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Reader:
    def __init__(self, raw):
        self.raw, self.pos = raw, 0

    def take(self, n):
        require(0 <= n <= len(self.raw) - self.pos, f'truncated at {self.pos}')
        start = self.pos
        self.pos += n
        return self.raw[start:self.pos]

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def count(self):
        n = self.number('i')
        require(0 <= n <= 65536, f'invalid count {n}')
        return n

    def string(self):
        raw = self.take(self.count()).rstrip(b'\0')
        for encoding in ('utf-8', 'gb18030', 'latin-1'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                pass


def parse_act(raw):
    r = Reader(raw)
    require(r.take(32).startswith(b'Eternity Action File'), 'ACT signature')
    version = r.number('i')
    require(version in range(1, 7), 'unsupported ACT version')
    actions = []
    for _ in range(r.count()):
        a = dict(offset=r.pos, name=r.string(), animation=r.string(),
                 frames=r.number('i'), next_action=r.string(),
                 blend=r.number('i'), next_frame=r.number('i'))
        a['opaque_settings'] = r.take({1: 0, 2: 4, 3: 8, 4: 9, 5: 13, 6: 14}[version]).hex()
        a['signals'] = []
        for _ in range(r.count()):
            s = dict(offset=r.pos, type=r.number('i'), start=r.number('i'),
                     end=r.number('i'), order=r.number('i'))
            s['u32'] = list(struct.unpack('<64I', r.take(256)))
            s['tables'] = {}
            for width, label in ((2, 'vec2'), (3, 'vec3'), (4, 'vec4')):
                indexes = [r.number('i') for _ in range(r.count())]
                if indexes:
                    s['tables'][label] = [[i, list(struct.unpack('<'+'f'*width,
                                                        r.take(4*width)))] for i in indexes]
            indexes = [r.number('i') for _ in range(r.count())]
            s['strings'] = [[i, r.string()] for i in indexes]
            s['tail_u8'] = r.number('B') if version >= 6 else None
            s['end_offset'] = r.pos
            a['signals'].append(s)
        a['end_offset'] = r.pos
        a['span_sha256'] = sha(raw[a['offset']:r.pos])
        actions.append(a)
    require(r.pos == len(raw), 'ACT trailing bytes')
    require(len({a['name'] for a in actions}) == len(actions), 'duplicate action names')
    return actions


def parse_dnt(raw, wanted):
    """Read every cell to EOF, retain only selected rows and exact cell offsets."""
    r = Reader(raw)
    r.take(4)
    column_count, row_count = r.number('H'), r.number('I')
    require(column_count < 2000 and row_count < 1000000, 'DNT dimensions')
    columns = [(r.take(r.number('H')).decode('ascii'), r.number('B'))
               for _ in range(column_count)]
    selected = []
    for _ in range(row_count):
        offsets = {'RowId': r.pos}
        row = {'RowId': r.number('I')}
        for name, kind in columns:
            offsets[name] = r.pos
            if kind == 1:
                value = r.take(r.number('H')).decode('utf-8', errors='replace')
            elif kind in (2, 3, 4, 5):
                value = r.number('i' if kind in (2, 3) else 'f')
            else:
                raise EvidenceError(f'unknown DNT type {kind}')
            row[name] = value
        if row['RowId'] in wanted:
            selected.append(dict(row=row, offsets=offsets))
    require(r.pos == len(raw), 'DNT trailing bytes')
    return selected


def xor(raw, phase):
    return raw[:9] + bytes(v ^ KEY[(i-phase) % 13] for i, v in enumerate(raw[9:], 9))


def records(wire):
    require(len(wire) >= 21 and int.from_bytes(wire[7:9], 'little') + 3 == len(wire),
            'outer length')
    alternatives = []
    for phase in range(13):
        raw = xor(wire, phase)
        r, found = 14, []
        while r + 7 <= len(raw):
            m = r + 4
            size = int.from_bytes(raw[m+1:m+3], 'little')
            end = m + 3 + size
            if not size or end > len(raw):
                break
            found.append((m, raw[m], size))
            r = end
        if found and r == len(raw):
            alternatives.append((phase, raw, found))
    require(len(alternatives) == 1, 'nonunique/incomplete record chain')
    return alternatives[0]


def u32(raw, off):
    return struct.unpack_from('<I', raw, off)[0]


def direct_fields(row):
    require(row['direction'] == 'server_to_client', 'not server')
    phase, raw, chain = records(bytes.fromhex(row['wire']))
    found = []
    for m, opcode, size in chain:
        if (opcode, size) != (8, 115):
            continue
        if (u32(raw, m+8), u32(raw, m-4), struct.unpack_from('<Q', raw, m+19)[0]) != (
                row['source'], row['target'], row['damage']):
            continue
        require(raw[m+12] == 0 and raw[m+106:m+110] == bytes(4), 'not direct37')
        found.append(dict(phase=phase, marker=m, m102=u32(raw, m+102),
                          m114=u32(raw, m+114), source=row['source'],
                          target=row['target'], damage=row['damage']))
    require(len(found) == 1, 'no unique physical hit')
    return found[0]


def declared_family(act, tables, template):
    """Conditional graph proof, NOT proof of engine graph completeness.

Normative model M: invocation starts at a DNT-bound action and may enter
another action only through a declared/validated resource edge. A stored
action with neither a root binding nor ANY recorded incoming candidate
cannot execute in M. Even one unresolved incoming candidate defeats this
shortcut; absence in a partial inventory never suffices.
"""
    require(act['manifest_count'] == 7776 and not act['parsed_errors']
            and not act['unresolved_attachments'], 'incomplete inherited closure')
    bindings, bases = {}, {}
    for item in tables['levels']:
        row = item['row']
        bindings.setdefault(row.get('_ProcessParam1'), set()).add(row['_SkillIndex'])
    for item in tables['skill_rows']:
        row = item['row']
        if row.get('_BaseSkillID', 0) > 0:
            bases.setdefault(row['RowId'], set()).add(row['_BaseSkillID'])
    for row in tables['rune_links']:
        for i in range(1, 6):
            if row.get(f'_changeSkill{i}', 0) > 0:
                bases.setdefault(row[f'_changeSkill{i}'], set()).add(row[f'_baseSkill{i}'])
    for row in tables['mastery_links']:
        bases.setdefault(row['_SkillShowID'], set()).add(row['_SkillID'])

    def canonical(skill, seen=frozenset()):
        require(skill not in seen, 'canonical cycle')
        if skill == 4410 or skill not in bases:
            return {skill}
        return set().union(*(canonical(s, seen | {skill}) for s in bases[skill]))

    creators, all_families, unresolved = [], set(), []
    for item in act['incoming']:
        signal = item['signal']
        if signal['type'] != 48 or signal['u32'][0] != template:
            continue
        name = item['action']
        skills = bindings.get(name, set())
        inbound = [e for e in act['root_action_inbound'] if e['target'] == name]
        families = set().union(*(canonical(s) for s in skills)) if skills else set()
        # Inbound entries are candidates with unproved semantics; do not silently
        # label them same-family or ignore them even for an already bound action.
        if inbound:
            unresolved.append(name)
        all_families.update(families)
        creators.append(dict(action=name, offset=signal['offset'], bound_skills=sorted(skills),
                families=sorted(families), inbound_candidates=len(inbound),
                excluded_as_unreachable_in_M=not skills and not inbound))
    require(creators, 'no positive creator witness')
    return dict(template=template, model='M_explicit_resource_declared_routing',
                model_is_assumption=True, ready_in_M=all_families == {4410} and not unresolved,
                canonical_families=sorted(all_families), unresolved=sorted(set(unresolved)),
                creators=creators, establishes_m114_semantics=False,
                establishes_engine_global_completeness=False)


def analyze():
    inputs = {}

    def read(path, limit=5000000):
        require(path.stat().st_size <= limit, 'bounded input too large')
        raw = path.read_bytes()
        inputs[str(path)] = dict(bytes=len(raw), sha256=sha(raw))
        return raw

    def load(name):
        return json.loads(read(ROOT / name).decode('utf-8-sig'))

    act_old = load(OLD + 'act_closure.json')
    dnt_old = load(OLD + 'table_closure.json')
    ai_old = load(OLD + 'ai_closure.json')
    static = load('outputs/magma_exact_route_v28_20260908/static_evidence.json')
    points = load('outputs/entity_creator_slot_v34_20260908/point_evidence.json')
    stats = load('outputs/entity_creator_slot_v34_20260908/statistics.json')
    roots = load('outputs/magma_exact_route_v28_20260908/root_selector_evidence.json')
    flags = load('outputs/magma_signal_flags_v36_20260908/evidence.json')
    index = load('outputs/v25_entity_owner_independent/0821硬核04_0_180_v2.json')
    actions = {}
    manifests = {r['path']: r for r in act_old['referenced_manifest']}
    for path in (WALL, ADEPT):
        raw = read(RESOURCE / path)
        require(sha(raw) == manifests[path]['sha256'], f'ACT hash drift: {path}')
        actions[path] = parse_act(raw)
        old_spans = [a for a in static['exact_actions'] if a['path'] == path]
        for old in old_spans:
            new = next(a for a in actions[path] if a['name'] == old['name'])
            for key in ('offset', 'end_offset', 'span_sha256', 'next_action'):
                require(new[key] == old[key], f'ACT span drift: {key}')
            projection = [{k: s[k] for k in ('offset', 'type', 'start', 'end',
                          'order', 'u32', 'tables', 'strings')} for s in new['signals']]
            require(projection == old['signals'], 'ACT parser disagreement')
    wall = actions[WALL]
    counts = Counter(s['type'] for a in wall for s in a['signals'])
    require(len(wall) == 9 and counts[4] == 12, 'wall attack surface drift')
    require(not any(counts[k] for k in (15, 48, 126)), 'new outbound damage/entity route')
    generations = {}
    for name in (NORMAL, CLONE):
        a = next(a for a in actions[ADEPT] if a['name'] == name)
        generations[name] = [dict(offset=s['offset'], template=s['u32'][0],
                unknown_u32_24=s['u32'][24], unknown_tail=s['tail_u8'])
                for s in a['signals'] if s['type'] == 48]
        require(all(s['template'] == 32022 for s in generations[name]), 'generator target drift')
    dnt_manifests = {r['path']: r['sha256'] for r in dnt_old['manifest']}
    tables = {}
    wanted = {0, 4410, 4505, 4407, 4409, 32022, 72949, 72953, 26153}
    for name in TABLES:
        raw = read(RESOURCE / 'ext' / name)
        require(sha(raw) == dnt_manifests['ext/' + name], f'DNT drift: {name}')
        tables[name] = parse_dnt(raw, wanted)
    character = {r['row']['RowId']: r for r in tables['skilltable_character.dnt']}
    require(character[4505]['row']['_BaseSkillID'] == 4410, 'EX base changed')
    require(all(character[s]['row']['_BaseSkillID'] == 0 for s in (4410, 4407, 4409)),
            'canonical alternatives changed')
    monster = next(r['row'] for r in tables['monstertable_quest.dnt'] if r['row']['RowId'] == 32022)
    require(monster['_SkillTable'] == 0 and monster['_ActorTableID'] == 5034, 'wall DNT drift')
    require(not any(r['row']['RowId'] == 0 for n in TABLES[3:] for r in tables[n]),
            'installed skill table zero now exists')
    ai_path, ai_text = next(iter(ai_old['assigned_ai'].items()))
    require(read(RESOURCE / ai_path).decode('utf-8-sig') == ai_text, 'assigned AI drift')

    selected = []
    for case in points['cases']:
        for hit in case['selected_hits']:
            actual = direct_fields(hit)
            require((actual['m102'], actual['m114']) == (hit['m102'], hit['m114']), 'slot drift')
            selected.append(dict(case=case['label'], frame=hit['frame'],
                                 template=case['template'], **actual))
    root_facts = []
    for row in roots['positives']:
        expected = row['root']
        _, raw, chain = records(bytes.fromhex(row['anchor']['wire']))
        m = expected['marker']
        require((m, 14, 35) in chain, 'not full EX root')
        require((u32(raw, m-4), u32(raw, m+3), u32(raw, m+21)) ==
                (expected['owner'], 4410, 4505), 'root bytes drift')
        root_facts.append(dict(frame=row['anchor']['frame'], owner=expected['owner']))
    prior = next(a for a in index['actions'] if a['frame'] == 3932 and a['actor'] == 1298581862)
    _, raw, chain = records(bytes.fromhex(prior['wire']))
    require((prior['marker'], 18, 36) in chain and u32(raw, prior['marker']+3) == 4409,
            'foreign-family preceding action not supported')
    magma = [c for c in points['cases'] if c['template'] == 32022]
    identity = []
    for case in magma:
        def saved(ref):
            return next(r for r in points['raw_frames'] if r['capture'] == case['capture']
                        and r['file_offset'] == ref['file_offset'])
        birth, owner = saved(case['birth']), saved(case['owner_record'])
        w = bytes.fromhex(birth['wire'])
        require(len(w) == 88 and w[5:9].hex() == '06275500', 'birth format')
        plain = xor(w, 5)
        require((u32(plain, 10), u32(plain, 18)) == (case['entity'], 32022), 'birth identity')
        _, raw, chain = records(bytes.fromhex(owner['wire']))
        m = case['owner_record']['marker']
        require((m, 52, 44) in chain and raw[m+7] == 1, 'owner format')
        require(u32(raw, m-4) == case['entity'] and
                u32(raw, m+3) == case['owner'] == u32(raw, m+43), 'owner conflict')
        identity.append(dict(entity=case['entity'], owner=case['owner'],
                capture=case['capture'], birth_frame=birth['frame'],
                birth_opaque_u32_14=u32(plain, 14), owner_frame=owner['frame']))
    return dict(schema='magma-canonical-v37',
        verdict='CANONICAL_4410_PROVED_IN_EXPLICIT_RESOURCE_MODEL_M; M114_CAUSALITY_UNPROVEN',
        production_modified=False, confirmed_new_hits=0,
        scope=dict(act_files=2, act_bytes=sum(manifests[p]['size'] for p in (WALL, ADEPT)),
                   dnt_files=5, ai_files=1, capture_reads=0, resource_enumeration=False,
                   packet_source='existing saved wire evidence; no fresh capture read'),
        generators=generations, wall_attacks=[dict(name=a['name'], offset=a['offset'],
               typed4_offsets=[s['offset'] for s in a['signals'] if s['type'] == 4]) for a in wall],
        canonical_cells={str(s): dict(row_id=s, row_id_offset=character[s]['offsets']['RowId'],
               base=character[s]['row']['_BaseSkillID'],
               base_offset=character[s]['offsets']['_BaseSkillID']) for s in (4410, 4505, 4407, 4409)},
        identity=identity, decoded_selected_hits=selected, full_ex_roots=root_facts,
        reused_32022_statistics=stats['by_template']['32022'],
        reused_no_multinonzero_template=stats['templates_with_multiple_nonzero_tails'],
        unresolved_clone_incoming=act_old['root_action_inbound'],
        reused_v36_tail_semantics=flags['result']['tail_semantics'],
        equal_bar_resource_proofs=[declared_family(act_old, dnt_old, t) for t in (32015, 32022)],
        countermodel_seed=dict(frame=prior['frame'], offset=prior['file_offset'],
                owner=prior['actor'], canonical=4409, time=prior['time'],
                wire_sha256=sha(bytes.fromhex(prior['wire'])),
                status='REAL_PRECEDING_ACTION_ONLY; NO_PROVEN_EDGE_TO_CLONE',
                hypothetical_edge='delayed native/opaque dispatch to Clone typed48 at 140721',
                hypothetical_tail_rule='optional template origin label, independent of invocation family',
                note='Outside M: requires an undeclared invocation edge. Not a valid objection only to EX if M is accepted for normal wall.'),
        premises_not_established=['m114 positively attests THIS birth creator canonical family',
                'model M is complete for all engine/native dispatch; same limitation for 32015'],
        inputs=inputs)


if __name__ == '__main__':
    print(json.dumps(analyze(), ensure_ascii=False, indent=2))
