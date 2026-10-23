"""Independent bounded wire audit. No runtime owner decoder or replay import.

Only the frozen v24 transport demultiplexer and hit reader are shared. Births,
XOR, 34/2c fields and structural actions are decoded here from raw bytes.
Action proximity/class uniqueness are context, NEVER ownership proof.
"""
from __future__ import annotations
import argparse
import ast
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')
MARKER = bytes.fromhex('342c00')
ACTIONS = tuple(bytes.fromhex(x) for x in ('0e2300', '0e1f00', '122400', '122000', '031400'))
HEADER = struct.Struct('<dBII')


def xor_body(wire, phase):
    return wire[:9] + bytes(v ^ KEY[(i-phase) % 13] for i, v in enumerate(wire[9:], 9))


def marker_pattern(markers):
    return re.compile(b'|'.join(re.escape(bytes(v ^ KEY[(k+j) % 13]
        for j, v in enumerate(m))) for m in markers for k in range(13)))


def read_frames(path, start, seconds):
    with path.open('rb') as stream:
        assert stream.read(6) == b'DNDP1\0'
        first, n = None, 0
        while header := stream.read(17):
            assert len(header) == 17
            offset = stream.tell()-17
            timestamp, adapter, original, size = HEADER.unpack(header)
            first = timestamp if first is None else first
            relative = timestamp-first
            n += 1
            if relative > start+seconds:
                break
            if relative < start:
                stream.seek(size, 1)
                continue
            raw = stream.read(size)
            assert len(raw) == size
            yield n, offset, timestamp, relative, original, raw


def frozen_protocol():
    path = ROOT/'outputs/damage_attribution_candidate_v24_20260907/protocol_v576.py'
    name = 'dragon_nest_builder.live_dps._independent_owner_frozen'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, hashlib.sha256(path.read_bytes()).hexdigest()


def static_template_mapping():
    path = ROOT/'dragon_nest_builder/live_dps/sources.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name) and n.target.id == '_SKILL_OBJECT_TEMPLATE_SKILL_IDS')
    ns = {}
    exec(compile(ast.Module([node], []), '<read-static-mapping>', 'exec'), ns)
    return ns['_SKILL_OBJECT_TEMPLATE_SKILL_IDS']


def audit(path, start, seconds):
    proto, digest = frozen_protocol()
    pattern = marker_pattern((MARKER, *ACTIONS))
    spawns, records, actions, hits = [], [], [], []
    births, generations = {}, collections.Counter()
    counts = collections.Counter()
    prefix_hash = hashlib.sha256()
    for n, offset, timestamp, relative, original, raw in read_frames(path, start, seconds):
        counts['physical_frames'] += 1
        prefix_hash.update(HEADER.pack(timestamp, 0, original, len(raw)))
        prefix_hash.update(raw)
        transport = proto.decode_captured_frame(timestamp, raw, original)
        if not transport or transport.transport != 'UDP':
            continue
        direction = transport.inferred_direction()
        for mi, wire in enumerate(proto.split_v576_payload(transport.payload)):
            counts['udp_messages'] += 1
            anchor = dict(frame=n, message=mi, file_offset=offset, time=timestamp,
                          relative=round(relative, 6), direction=direction)
            if len(wire) == 45 and wire[5:9] == bytes.fromhex('87062a00'):
                b = xor_body(wire, 6)
                actions.append(dict(anchor, actor=int.from_bytes(b[10:14], 'little'),
                    skill=int.from_bytes(b[14:18], 'little'), phase=6, marker=14,
                    compact=False, variant=None, kind=None, generation=None,
                    template=None, record=b.hex(), wire=wire.hex(), layout='cast45'))
            if len(wire) == 88 and wire[5:9] == bytes.fromhex('06275500'):
                b = xor_body(wire, 5)
                entity, template = int.from_bytes(b[10:14], 'little'), int.from_bytes(b[18:22], 'little')
                previous = births.get(entity)
                duplicate = bool(previous and previous['wire'] == wire.hex())
                if not duplicate:
                    generations[entity] += 1
                birth = dict(anchor, entity=entity, template=template, generation=generations[entity],
                             wire=wire.hex(), plaintext=b.hex(), duplicate_spawn=duplicate)
                spawns.append(birth)
                if not duplicate:
                    births[entity] = birth
            if pattern.search(wire, 9):
                for phase in range(13):
                    b = xor_body(wire, phase)
                    observer = int.from_bytes(b[10:14], 'little')
                    at = b.find(MARKER, 14)
                    while at >= 0:
                        if at+47 <= len(b):
                            entity = int.from_bytes(b[at-4:at], 'little')
                            owner = int.from_bytes(b[at+3:at+7], 'little')
                            repeat = int.from_bytes(b[at+43:at+47], 'little')
                            birth = births.get(entity)
                            records.append(dict(anchor, phase=phase, marker=at, entity=entity,
                                owner=owner, repeat=repeat, observer=observer, tag=b[at+7],
                                repeated=owner == repeat, owner_hex=f'0x{owner:08X}',
                                observer_hex=f'0x{observer:08X}', template=birth['template'] if birth else None,
                                generation=birth['generation'] if birth else None,
                                birth_frame=birth['frame'] if birth else None,
                                age=round(timestamp-birth['time'], 6) if birth else None,
                                resources=[int.from_bytes(b[at+k:at+k+8], 'little') for k in (8, 16, 24, 32)],
                                wire=wire.hex(), plaintext=b.hex(), frame_hex=raw.hex(),
                                original=original))
                        at = b.find(MARKER, at+1)
                    for marker in ACTIONS:
                        at = b.find(marker, 14)
                        while at >= 0:
                            end = at+3+int.from_bytes(marker[1:], 'little')
                            actor = int.from_bytes(b[at-4:at], 'little')
                            compact = marker == bytes.fromhex('031400')
                            skill = b[at+3] if compact and at+9 <= len(b) else int.from_bytes(b[at+3:at+7], 'little')
                            if end <= len(b) and 0 < actor < 0xffffffff and 0 < skill < 0x10000000:
                                birth = births.get(actor)
                                actions.append(dict(anchor, actor=actor, skill=skill, phase=phase,
                                    marker=at, compact=compact,
                                    variant=int.from_bytes(b[at+4:at+6], 'little') if compact else None,
                                    kind=b[at+6] if compact else None,
                                    generation=birth['generation'] if birth else None,
                                    template=birth['template'] if birth else None,
                                    record=b[at-4:end].hex(), wire=wire.hex()))
                            at = b.find(marker, at+1)
            for h in proto.decode_v576_embedded_direct_damage_events(wire, None):
                birth = births.get(h.source_actor_id)
                hits.append(dict(anchor, source=h.source_actor_id, target=h.target_actor_id,
                    skill=h.skill_id, damage=h.damage, template=birth['template'] if birth else None,
                    generation=birth['generation'] if birth else None, wire=wire.hex()))
    mapping = static_template_mapping()
    valid = [r for r in records if r['repeated'] and r['tag'] == 1
             and 0 < r['entity'] < 0x10000 and 0x1000000 <= r['owner'] < 0xffffffff]
    by_template, by_generation = collections.defaultdict(set), collections.defaultdict(set)
    for r in valid:
        by_template[r['template']].add(r['owner'])
        by_generation[(r['entity'], r['generation'])].add(r['owner'])
    summary = dict(counts=counts, births=len(spawns), raw_marker_records=len(records),
        repeated_owner_records=len(valid), owner_not_observer=sum(r['owner'] != r['observer'] for r in valid),
        unspawned_records=sum(r['birth_frame'] is None for r in valid),
        boss_template_records=sum((r['template'] or 0) >= 100000 for r in valid),
        entity_reuse={str(k): v for k, v in generations.items() if v > 1},
        duplicate_spawns=sum(r['duplicate_spawn'] for r in spawns),
        contradictory_generation_owners={str(k): sorted(v) for k, v in by_generation.items() if len(v)>1},
        template_owners={str(k): [f'0x{x:08X}' for x in sorted(v)] for k,v in by_template.items()},
        record_tags=dict(collections.Counter(str(r['tag']) for r in records)),
        action_rows=len(actions), hit_rows=len(hits))
    return dict(capture=str(path.resolve()), start=start, seconds=seconds,
        protocol_sha256=digest, normalized_window_sha256=prefix_hash.hexdigest(),
        policy='Independent extraction; repeated token is candidate owner, class/time adjacency never proof.',
        summary=summary, static_template_mapping=mapping, spawns=spawns, records=records,
        actions=actions, hits=hits)


def valid_record(r):
    return (r['repeated'] and r['tag'] == 1 and 0 < r['entity'] < 65536
            and 0x1000000 <= r['owner'] < 0xffffffff
            and 0x1000000 <= r['observer'] < 0xffffffff)


def resource_proof():
    from tools.build_live_dps_monster_catalog import parse_dnt
    from tools.build_attack_instance_catalog import parse_act
    resource = Path('E:/ext0811.rar/Resource00/Resource00/resource')
    ext = resource/'ext'
    hashes = {}

    def read(name):
        path = ext/name
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return parse_dnt(path)

    summons = read('summonskilltable.dnt')
    # A comma pair is retained verbatim; this audit does not assume a list vs
    # inclusive range. Only singleton rows form exact static skill mappings.
    exact = collections.defaultdict(set)
    for row in summons:
        if re.fullmatch(r'\d+', str(row['_MonsterSkill'])):
            exact[int(row['_MonsterSkill'])].add(row['_SkillIndex'])
    selected = {30001,30101,30151,30201,30251,30311,30401,30501,30601,31001,
                32004,32005,32008,32009,32011,32013,32022,32026,32029,
                33101,33500,33502,33503,33601,33608,33612}
    monsters = [r for r in read('monstertable_quest.dnt') if r['RowId'] in selected]
    actors = {r['RowId']: r for r in read('actortable.dnt')}
    files = {r['RowId']: r for r in read('filetable.dnt')}
    tables = {r['RowId']: r for r in read('monsterskilltable.dnt')}
    skill_ids = {v for r in monsters for k,v in tables.get(r['_SkillTable'], {}).items()
                 if k.startswith('_SkillIndex') and v > 0}
    levels = collections.defaultdict(list)
    for name in ('academic', 'assassin', 'cleric', 'kali', 'archer', 'soceress'):
        table = f'skillleveltable_character{name}.dnt'
        for r in read(table):
            if r['_SkillIndex'] in skill_ids:
                levels[r['_SkillIndex']].append(dict(table=table, row_id=r['RowId'],
                    skill=r['_SkillIndex'], level=r['_SkillLevel'],
                    action=r.get('_ProcessParam1', '')))
    act_index = collections.defaultdict(list)
    def basename(filename):
        return filename.replace('\\', '/').split('/')[-1].lower()
    wanted = {basename(files[actors[r['_ActorTableID']]['_ActName']]['_FileName']) for r in monsters}
    for path in resource.rglob('*.act'):
        if path.name.lower() in wanted:
            act_index[path.name.lower()].append(path)
    proofs = []
    for m in monsters:
        actor = actors[m['_ActorTableID']]
        filename = files[actor['_ActName']]['_FileName']
        paths = act_index[basename(filename)]
        normalized = filename.replace('\\', '/').lower()
        if '/' in normalized:
            paths = [p for p in paths if str(p.relative_to(resource)).replace('\\','/').lower().endswith('/'+normalized)
                     or str(p.relative_to(resource)).replace('\\','/').lower()==normalized]
        ai_path = resource/'script/monsterai'/m['_CustomAI'].lower()
        ai_lines = []
        if ai_path.exists():
            hashes[str(ai_path)] = hashlib.sha256(ai_path.read_bytes()).hexdigest()
            ai_lines = [dict(line=i, text=s.strip()) for i,s in
                enumerate(ai_path.read_text(encoding='utf-8', errors='replace').splitlines(), 1)
                if re.search(r'skill_index\s*=\s*\d+|OwnerFollow|action_name\s*=', s.split('--',1)[0])]
        acts, error = {}, None
        if len(paths) == 1:
            hashes[str(paths[0])] = hashlib.sha256(paths[0].read_bytes()).hexdigest()
            try:
                acts = {a.name: a for a in parse_act(paths[0]).actions}
            except ValueError as exc:
                error = str(exc)
        skill_proofs = []
        for k, sid in tables.get(m['_SkillTable'], {}).items():
            if not k.startswith('_SkillIndex') or sid <= 0:
                continue
            action_names = sorted({r['action'] for r in levels[sid] if r['action']})
            details = []
            for action in action_names:
                act = acts.get(action)
                details.append(dict(action=action, exact_act_action=act is not None,
                    direct_damage_signals=sum(s.signal_type == 4 for s in act.signals) if act else 0,
                    projectile_templates=sorted({int.from_bytes(s.payload[:4], 'little')
                        for s in act.signals if s.signal_type == 15}) if act else []))
            endpoints = [r for r in summons if ',' in str(r['_MonsterSkill'])
                         and sid in [int(n) for n in r['_MonsterSkill'].split(',')]]
            skill_proofs.append(dict(slot=k, monster_skill=sid,
                explicit_player_skill_ids=sorted(exact.get(sid, set())),
                mapping_pair_endpoint_rows=endpoints,
                level_rows=levels[sid], actions=details))
        ai_skills = sorted({int(n) for row in ai_lines for n in re.findall(
            r'(?<!lua_)skill_index\s*=\s*(\d+)', row['text'].split('--',1)[0])})
        ai_actions = sorted({n for row in ai_lines for n in re.findall(
            r'action_name\s*=\s*"([^"]+)"', row['text'].split('--',1)[0])})
        proofs.append(dict(template=m['RowId'], actor_table_id=m['_ActorTableID'],
            monster_skill_table=m['_SkillTable'], weapons=[m['_Weapon1Index'],m['_Weapon2Index']],
            actor_act_reference=filename, resolved_act_paths=[str(p) for p in paths],
            act_error=error, ai_path=str(ai_path), ai_evidence=ai_lines,
            ai_skill_ids=ai_skills, ai_action_names=ai_actions, skills=skill_proofs,
            act_damage_actions=[dict(action=a.name, direct_damage_signals=sum(s.signal_type==4 for s in a.signals),
                projectile_templates=sorted({int.from_bytes(s.payload[:4],'little') for s in a.signals if s.signal_type==15}))
                for a in acts.values() if any(s.signal_type in (4,15) for s in a.signals)]))
    return dict(resource_root=str(resource), hashes=hashes, summon_rows=summons,
        policy='Exact table references only; compact wire action bytes are NOT assumed to be ACT indices.',
        templates=proofs)


def guard_probes(rows, fixture_path):
    """Read a snapshot of Main's module solely as the system under test."""
    from dataclasses import replace
    path = ROOT/'dragon_nest_builder/live_dps/entity_owner_evidence.py'
    source = path.read_text(encoding='utf-8')
    name = 'dragon_nest_builder.live_dps._independent_owner_sut'
    import types
    sut = types.ModuleType(name)
    sys.modules[name] = sut
    exec(compile(source, str(path), 'exec'), sut.__dict__)
    sample = next(r for r in rows if r['frame'] == 1485 and r['entity'] == 57)
    wire, body = bytes.fromhex(sample['wire']), bytes.fromhex(sample['plaintext'])
    decode = sut.decode_entity_owner_records
    rec = decode(wire, 'server_to_client')[0]
    parser_cases = []

    def case(label, raw, direction, expected):
        found = decode(raw, direction)
        parser_cases.append(dict(name=label, wire=raw.hex(), direction=direction,
            expected_count=expected, actual_count=len(found), passed=len(found)==expected))

    case('captured_1485', wire, 'server_to_client', 1)
    case('explicit_client_rejected', wire, 'client_to_server', 0)
    case('truncated_repeat_rejected', wire[:-1], 'server_to_client', 0)
    for label, at, value in [('repeat_mismatch', 61, body[61]^1),
                              ('typed_tag_zero', 25, 0), ('record_length_bad', 19, 43)]:
        changed = bytearray(body)
        changed[at] = value
        case(label, xor_body(bytes(changed), sample['phase']), 'server_to_client', 0)
    changed = bytearray(body)
    changed[14:18] = b'\0'*4
    case('entity_zero', xor_body(bytes(changed), sample['phase']), 'server_to_client', 0)
    changed = bytearray(body)
    changed[21:25] = changed[61:65] = (rec.owner_id+1).to_bytes(4, 'little')
    case('contradictory_records_same_message', xor_body(body+bytes(changed[14:]), sample['phase']), 'server_to_client', 0)
    other = replace(rec, owner_id=rec.owner_id+1, fingerprint='synthetic-other-owner')
    results = []

    def probe(name, operations, expected):
        ledger = sut.EntityOwnerEvidenceLedger()
        for op, args in operations:
            getattr(ledger, op)(*args)
        found = ledger.resolve(57, 30501, 13.0)
        actual = found.record.owner_id if found else None
        results.append(dict(name=name, expected_owner=expected, actual_owner=actual,
                            passed=actual==expected, synthetic=True))

    birth = ('observe_birth', (57, 30501, 10.0))
    record = ('observe_record', (rec, 10.01))
    probe('missing_spawn', [record], None)
    probe('fresh_birth_record', [birth, record], rec.owner_id)
    probe('explicit_rebirth_clears_binding', [birth, record, ('observe_birth',(57,30501,11.0))], None)
    probe('retired_entity', [birth, record, ('retire',(57,))], None)
    probe('late_first_record', [birth,('observe_record',(rec,12.1))], None)
    probe('conflict_in_initialization', [birth,record,('observe_record',(other,10.02))], None)
    probe('conflicting_pending_before_birth', [('observe_record',(rec,9.96)),
        ('observe_record',(other,9.97)),birth], None)
    probe('conflict_after_initialization', [birth,record,('observe_record',(other,12.1))], None)
    probe('old_record_replayed_after_rebirth', [birth,record,('observe_birth',(57,30501,11.0)),
        ('observe_record',(rec,11.01))], None)
    # Expected fail-closed behavior is a recommendation, not a claim that the
    # current API carries enough sequence metadata to distinguish replays.
    fixture = dict(policy='Captured positive plus explicitly labeled synthetic negative/lifecycle probes.',
        source=dict(capture=sample.get('capture'),frame=1485,entity=57,template=30501,
                    owner='0x4D66BEEE',observer='0x5062BFBF'),
        positive_raw_record=sample, parser_cases=parser_cases, lifecycle_probes=results)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    if fixture_path.exists():
        raise FileExistsError(fixture_path)
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
    return dict(module_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest(),
        parser_cases=parser_cases, lifecycle_probes=results,
        warning='Lifecycle probes execute the frozen source snapshot, not a runtime integration.')


def build_bundle(directory):
    filenames = ['0821硬核04_0_180_v2.json', '0830星期天_210_390.json',
                 '0831黑硬曜_0_180.json', '0822硬核噬魂_0_180.json',
                 '20260822YEP黑硬曜_0_180.json', '0826硬核曜_0_180.json']
    evidence, all_records, positives, missing = [], [], [], []
    for filename in filenames:
        doc = json.loads((directory/filename).read_text(encoding='utf-8'))
        valid = [r for r in doc['records'] if valid_record(r)]
        all_records.extend(dict(r, capture=doc['capture']) for r in doc['records'])
        owners = {r['owner'] for r in valid}
        by_life = {(r['entity'],r['generation']):r for r in valid}
        paired = [(h,by_life[(h['source'],h['generation'])]) for h in doc['hits']
                  if (h['source'],h['generation']) in by_life]
        owner_actions = {str(owner): sorted({a['skill'] for a in doc['actions']
                        if not a['compact'] and a['actor']==owner}) for owner in owners}
        owned_targets = collections.Counter((r['template'],h['target']) for h,r in paired)
        counts = dict(doc['summary'], owned_hit_records=len(paired),
            owner_equals_hit_target=sum(h['target']==r['owner'] for h,r in paired),
            owners_with_independent_player_actions=sum(bool(v) for v in owner_actions.values()),
            owner_count=len(owners),
            boss_births=sum(s['template']>=100000 for s in doc['spawns']),
            max_owner_initialization_age=max((r['age'] for r in valid if r['age'] is not None),default=0))
        evidence.append(dict(file=filename, capture=doc['capture'], summary=counts,
            owner_actions=owner_actions,
            owned_targets=[dict(template=k[0],target=k[1],rows=v) for k,v in owned_targets.items()]))
        for r in valid:
            if (r['template'] in (30501,30001,32009,32011,33503,33608,33612,31001)
                and not any(p['capture']==doc['capture'] and p['template']==r['template'] and p['owner']==r['owner'] for p in positives)):
                positives.append(dict(r,capture=doc['capture']))
        missing.append(dict(capture=doc['capture'], unspawned_hit_sources=dict(collections.Counter(
            str(h['source']) for h in doc['hits'] if 0<(h['source'] or 0)<65536 and h['template'] is None))))
    result = dict(policy='Ownership evidence and skill evidence are separate. No inferred player class or nearest cast is promoted.',
        captures=evidence, examples=positives, missing_spawn_hits=missing,
        static_proof=resource_proof(), guard_probes=guard_probes(all_records,
            ROOT/'tests/data/entity_owner_v25_independent.json'))
    output = directory/'validation_bundle.json'
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output), raw_owner_records=sum(e['summary']['repeated_owner_records'] for e in evidence),
        owned_hit_records=sum(e['summary']['owned_hit_records'] for e in evidence),
        guard_probes=result['guard_probes']['lifecycle_probes']),ensure_ascii=False))


def final_static_decisions(directory):
    from tools.build_live_dps_monster_catalog import parse_dnt
    from tools.build_attack_instance_catalog import parse_act
    resource = Path('E:/ext0811.rar/Resource00/Resource00/resource')
    ext = resource/'ext'
    proof = resource_proof()
    by_template = {r['template']:r for r in proof['templates']}
    safe = {30001:3409,30501:3409,32004:4209,32011:4351,32026:4351,
            33500:5014,33502:5216,33503:5216,33601:6318}
    # Exact resource references make the existing summon identity reviewable;
    # its attribution is accepted only after checking autonomous attack closure.
    references = {
        'char/player/cleric/cleric_skill_1st_priest.act': ['Skill_RelicOfLightning_Lv01'],
        'char/player/cleric/cleric_skill_2st_saint.act': ['Skill_RelicOfLightning_Lv01_EX','Skill_RelicOfLightningPVE_Lv01_EX'],
        'char/player/academic/academic_skill_1st_engineer.act': ['Skill_Summon_CannonTower'],
        'char/player/academic/academic_skill_2st_gearmaster.act': ['Skill_Summon_ElectricSawTower'],
        'char/player/assassin/assassin_skill_1st_bringer.act': ['Skill_PactOfAjna'],
        'weapon/academic_electricsawtower_capsule.act': ['Hit'],
        'weapon/academic_cannontower_capsule.act': ['Hit'],
    }
    parent_edges = []
    for relative,names in references.items():
        path = resource/relative
        proof['hashes'][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for action in parse_act(path).actions:
            if action.name in names:
                parent_edges.append(dict(path=str(path),action=action.name,
                    signals=[dict(type=s.signal_type, payload=s.payload.hex(),
                                  first_u32=int.from_bytes(s.payload[:4],'little'))
                             for s in action.signals if s.signal_type in (15,48,126)]))
    relevant_skills = set(safe.values()) | {3502,3503,30808,30616,30807}
    player_skills = [r for r in parse_dnt(ext/'skilltable_character.dnt') if r['RowId'] in relevant_skills]
    weapon_rows = [r for r in parse_dnt(ext/'weapontable_projectile.dnt') if r['RowId'] in (5029,5045,5052,5512,5532,5533)]
    wanted_files = {r['_ActName'] for r in weapon_rows}
    file_rows = [r for r in parse_dnt(ext/'filetable.dnt') if r['RowId'] in wanted_files]
    for name in ('skilltable_character.dnt','weapontable_projectile.dnt'):
        proof['hashes'][str(ext/name)] = hashlib.sha256((ext/name).read_bytes()).hexdigest()
    decisions = []
    for tid,sid in safe.items():
        t = by_template[tid]
        decisions.append(dict(template=tid,player_skill=sid,
            ownership_required='Observed birth generation plus full repeated 34/2c owner; no missing-spawn fallback.',
            hit_required='Exact damage source entity in same generation OR packet-confirmed entity->object->hit chain.',
            autonomous_monster_skills=t['ai_skill_ids'],
            creation_only_is_proof=False, attack_family_is_statically_single=True,
            raw_owner_seen_in_six_windows=tid in (30001,30501,32004,32011,33500,33503),
            proof_special_case='User-authorized aggregation of autonomous Azuna attacks to Pact 6318.' if tid==33601 else None))
    # All inherited templates not explicitly listed are closed by default.
    result = dict(safe_template_skill_map=safe, default_for_unlisted='do_not_upgrade',
        interpretation='Single autonomous damage family after AI/ACT audit, NOT whole-template creation attribution.',
        safe_requires=decisions,
        must_split_templates={
            '32005':dict(monster_to_player={'260401..260430':4217,'260501..260530':4218,'260601..260630':4219,'30809':4352},
                         evidence='monsterskilltable 951 + summonskilltable + active AI checkpassiveskill/waitorder; basic bite additionally unmapped'),
            '32008':dict(player_skills=[4212,4213,4214,4215,4217,4303],evidence='table953 + multiple explicit commands; cannot assign all to4015/4223'),
            '33607..33614':dict(evidence='table976 + distinct player attack families; actor shared with multiple weapon and skill actions'),
            '30101/30151/30201/30251/30311/30401_and_related_variants':dict(evidence='ACT has Skill_ShockofRelic; player skill3503 also names this command; AI-only uniqueness is insufficient')},
        held_templates={
            '30601':'AI293132 missing exact attack level/action proof in extracted resources; old3409 alias insufficient.',
            '32029':'AI293166 missing exact attack level/action proof; cannot inherit32011 result blindly.',
            '31001':'Provoke30801 and Boom30802 have distinct internal skills; only30802 has explicit1303 mapping; keep action-specific until all damage branch semantics are audited.',
            '32013/32022':'SkillTable0; direct AI/ACT attacks exist, but complete player-skill and mutation branches were not independently joined.',
            '33101':'AI30706 and ACT Activate family corroborate mirror, but mutation/element damage semantics not fully audited.',
            '30002..30020/30502..30520':'Same actor/AI but distinct SkillTable52..70; no automatic range expansion in this bounded validation.',
            '503142/503552/503555':'Boss-like numeric/template context not audited here; no inherited whitelist.'},
        parent_act_edges=parent_edges,player_skill_rows=player_skills,weapon_rows=weapon_rows,
        file_rows=file_rows,resource_proof=proof)
    output = directory/'safe_template_decisions.json'
    if output.exists():
        raise FileExistsError(output)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),safe_template_skill_map=safe),ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path, nargs='?')
    ap.add_argument('--bundle', type=Path)
    ap.add_argument('--static-decisions', type=Path)
    ap.add_argument('--start', type=float, default=0)
    ap.add_argument('--seconds', type=float, default=180)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    if args.static_decisions:
        final_static_decisions(args.static_decisions)
        return
    if args.bundle:
        build_bundle(args.bundle)
        return
    if not args.capture or not args.output:
        ap.error('capture and --output are required without --bundle')
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing independent audit')
    result = audit(args.capture, args.start, args.seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), summary=result['summary']), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
