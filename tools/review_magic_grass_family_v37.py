"""Finite, read-only MagicGrass resource/old-index audit; never a runtime policy.

Only explicitly named DNT/ACT/AI files and six existing indexes are read.
Raw evidence is reopened at indexed offsets, never replayed or searched.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
from tools.probe_entity_creator_slot_v34 import (
    NAMES, anchor, order, reopen, verified_birth, verified_owner,
)
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OUT = ROOT / 'outputs/magic_grass_family_v37_20260908'
INDEX = ROOT / 'outputs/v25_entity_owner_independent'
TARGETS = (32012, 32017, 32018)
DNT_NAMES = (
    'monstertable_quest', 'actortable', 'filetable', 'monsterskilltable',
    'summonskilltable', 'skilltable_character', 'skilltable_rune',
    'skilltable_corerune', 'skillleveltable_characteracademic',
    'skillleveltable_rune', 'skillleveltable_corerune', 'runeresonatetable',
)
PLAYER_ACTS = (
    'char/player/academic/academic_skill_1st_alchemist.act',
    'char/player/academic/academic_skill_2st_adept.act',
    'char/player/academic/academic_skill_2st_physician.act',
)
SLIME_ACT = 'char/monster/slime/slime.act'


def stamp(path, manifest):
    if path.stat().st_size > 8_000_000:
        raise ValueError(f'bounded input too large: {path}')
    raw = path.read_bytes()
    manifest[str(path)] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
    return raw


def parse_ai(text):
    """Only numeric calls/action names in active Lua text; comments cannot add edges."""
    rows = []
    for line, raw in enumerate(text.splitlines(), 1):
        code = raw.split('--', 1)[0]
        skill = re.search(r'\bskill_index\s*=\s*(\d+)', code)
        action = re.search(r'\baction_name\s*=\s*"([^"]+)"', code)
        wait = re.search(r'\bwaitorder\s*=\s*(\d+)', code)
        if skill or action:
            rows.append({'line': line, 'code': code.strip(),
                         'skill': int(skill[1]) if skill else None,
                         'action': action[1] if action else None,
                         'waitorder': int(wait[1]) if wait else None})
    return rows


def action_tokens(row):
    return {v for k, val in row.items() if k.startswith('_ProcessParam')
            for v in str(val).split(';') if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9\[\]]*', v)}


def compact_action(action):
    return {**{k: action[k] for k in ('offset', 'name', 'next_action')},
            'damage_offsets': [s['offset'] for s in action['signals'] if s['type'] == 4],
            'projectile_offsets': [s['offset'] for s in action['signals'] if s['type'] == 15],
            'signal_types': dict(Counter(str(s['type']) for s in action['signals'])),
            'explicit_act_strings': [dict(offset=s['offset'], type=s['type'],
                                          strings=[v for _, v in s['strings'] if v.lower().endswith('.act')])
                                     for s in action['signals']
                                     if any(v.lower().endswith('.act') for _, v in s['strings'])]}


def closure(actions, seeds):
    """Only explicit next_action is a control edge; arbitrary ACT strings are not."""
    pending, visited, missing = list(seeds), set(), set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name not in actions:
            missing.add(name)
            continue
        visited.add(name)
        nxt = actions[name]['next_action']
        if nxt:
            pending.append(nxt)
    return {'actions': sorted(visited), 'missing': sorted(missing),
            'damage_actions': sorted(x for x in visited if actions[x]['damage_offsets']),
            'projectile_actions': sorted(x for x in visited if actions[x]['projectile_offsets'])}


def family_for(skill, skill_rows, resonance):
    """Exact numeric parent edges only. No names, aliases, cooldown groups or timing."""
    parents = {x[f'_baseSkill{i}'] for x in resonance for i in range(1, 6)
               if x.get(f'_changeSkill{i}') == skill and x.get(f'_baseSkill{i}', 0) > 0}
    base = skill_rows.get(skill, {}).get('_BaseSkillID', 0)
    if base:
        parents.add(base)
    return sorted(parents or {skill})


def command_route(player, level, call, installed, actor, action):
    """A name-compatible level row is insufficient without the command processor."""
    try:
        command = int(level.get('_EffectClassValue1', ''))
    except ValueError:
        return False
    return bool(player.get('_NeedJob', 0) > 0 and player.get('_EffectClass1') == 215
                and str(level.get('_CanUseParam1')) == str(actor)
                and command == call.get('skill') == call.get('waitorder')
                and command in installed and action['damage_offsets'])


def independent_attack_check(skill, skill_rows, resonance, summon_rows):
    """Check numeric self-skill parent edges, never reverse an entity creator edge."""
    matched = []
    for row in summon_rows:
        parts = str(row['_MonsterSkill']).split(',')
        if not 1 <= len(parts) <= 2 or not all(p.strip().isdigit() for p in parts):
            raise ValueError('unreviewed summonskilltable range syntax')
        lo, hi = int(parts[0]), int(parts[-1])
        if lo > hi:
            raise ValueError('reversed summonskilltable range')
        if lo <= skill <= hi:
            matched.append(row)
    base = skill_rows.get(skill, {}).get('_BaseSkillID', 0)
    edges = [x for x in resonance if any(x.get(f'_changeSkill{i}') == skill for i in range(1, 6))]
    parents = {x['_SkillIndex'] for x in matched}
    if base:
        parents.add(base)
    parents.update(x[f'_baseSkill{i}'] for x in edges for i in range(1, 6)
                   if x.get(f'_changeSkill{i}') == skill and x.get(f'_baseSkill{i}', 0) > 0)
    families = sorted({f for p in parents for f in family_for(p, skill_rows, resonance)
                       if skill_rows.get(f, {}).get('_NeedJob', 0) > 0})
    return {'monster_skill': skill, 'base_skill_id': base, 'summon_rows': matched,
            'resonance_rows': edges, 'independent_player_families': families,
            'creator_edges_used': False,
            'status': 'numeric_parent_found' if families else 'no_independent_numeric_parent_in_reviewed_tables'}


def finalize_resource_canonical(resources):
    """Separate creator candidates from a current-attack canonical certificate."""
    skills = {x['RowId']: x for x in resources['selected_skill_rows']}
    checks = {ms: independent_attack_check(ms, skills, resources['resonance_edges'], resources['summon_rows'])
              for ms in sorted({x['monster_skill'] for x in resources['pairs'] if x['mode'] == 'autonomous'})}
    resources['independent_self_skill_checks'] = list(checks.values())
    for pair in resources['pairs']:
        if pair['mode'] == 'autonomous':
            pair['creator_candidate_families'] = sorted({f for c in resources['creators']
                if c['template'] == pair['template'] for f in c['families']})
            pair['independent_canonical_families'] = checks[pair['monster_skill']]['independent_player_families']
            pair['bounded_families'] = pair['independent_canonical_families']
            pair['canonical_basis'] = 'independent_numeric_parent' if pair['bounded_families'] else 'creator_only_not_certificate'
        else:
            pair['independent_canonical_families'] = pair['bounded_families']
            pair['canonical_basis'] = 'player_command_numeric_chain' if pair['bounded_families'] else 'unclosed_command'
    return resources


def classify_pair(resources, template, monster_skill):
    rows = [x for x in resources['pairs']
            if (x['template'], x['monster_skill']) == (template, monster_skill)]
    if monster_skill in (0, 0xffffffff) or not rows:
        return {'family': None, 'reason': 'no_exact_positive_resource_pair', 'runtime_promote': False}
    families = sorted({f for x in rows for f in x.get('independent_canonical_families', [])})
    creator_only = any(x.get('mode') == 'autonomous' for x in rows) and not families
    return {'family': families[0] if len(families) == 1 else None,
            'creator_candidates': sorted({f for x in rows for f in x.get('creator_candidate_families', [])}),
            'reason': ('creator_only_no_independent_canonical_chain' if creator_only else
                       'bounded_resource_pair_only' if len(families) == 1 else 'unclosed_resource_pair'),
            'runtime_promote': False}


def resource_audit(manifest):
    tables = {}
    for name in DNT_NAMES:
        path = RESOURCE / 'ext' / f'{name}.dnt'
        stamp(path, manifest)
        tables[name] = parse_dnt(path)
    skills = {x['RowId']: dict(x, table=name) for name in
              ('skilltable_character', 'skilltable_rune', 'skilltable_corerune') for x in tables[name]}
    levels = [dict(x, table=name) for name in ('skillleveltable_characteracademic',
              'skillleveltable_rune', 'skillleveltable_corerune') for x in tables[name]]
    resonance = tables['runeresonatetable']
    monster = {x['RowId']: x for x in tables['monstertable_quest'] if x['RowId'] in TARGETS}
    actors = {x['RowId']: x for x in tables['actortable']}
    files = {x['RowId']: x for x in tables['filetable']}
    installs = {x['RowId']: x for x in tables['monsterskilltable']}
    stamp(RESOURCE / SLIME_ACT, manifest)
    parsed = parse_full(RESOURCE / SLIME_ACT)
    actions = {x['name']: compact_action(x) for x in parsed['actions']}
    creators, inbound, command_actions = [], [], []
    levels_by_skill = defaultdict(list)
    command_levels = defaultdict(list)
    for lv in levels:
        levels_by_skill[lv['_SkillIndex']].append(lv)
        if skills.get(lv['_SkillIndex'], {}).get('_EffectClass1') == 215:
            val = str(lv.get('_EffectClassValue1', ''))
            if val.isdigit():
                command_levels[int(val)].append(lv)
    command_action_names = {n for xs in command_levels.values() for lv in xs for n in action_tokens(lv)}
    for rel in PLAYER_ACTS:
        stamp(RESOURCE / rel, manifest)
        parsed_player = parse_full(RESOURCE / rel)
        rootnames = set()
        for a in parsed_player['actions']:
            if a['name'] in command_action_names:
                command_actions.append({'act': rel, **compact_action(a)})
            for s in a['signals']:
                if s['type'] not in (48, 126) or s['u32'][0] not in TARGETS:
                    continue
                rootnames.add(a['name'])
                matching = [x for x in levels if a['name'] in action_tokens(x)
                            and skills.get(x['_SkillIndex'], {}).get('_NeedJob', 0) > 0]
                creators.append({'act': rel, 'action': a['name'], 'action_offset': a['offset'],
                                 'signal_offset': s['offset'], 'signal_type': s['type'],
                                 'template': s['u32'][0], 'level_rows': matching,
                                 'families': sorted({f for x in matching for f in
                                                   family_for(x['_SkillIndex'], skills, resonance)})})
        for a in parsed_player['actions']:
            if a['next_action'] in rootnames:
                inbound.append({'act': rel, 'from': a['name'], 'to': a['next_action'], 'kind': 'next_action'})
            for s in a['signals']:
                for slot, val in s['strings']:
                    if val in rootnames:
                        inbound.append({'act': rel, 'from': a['name'], 'to': val,
                                        'kind': 'untyped_string_not_control_proof',
                                        'signal_type': s['type'], 'offset': s['offset'], 'slot': slot})
    templates, pairs, command_proofs = [], [], []
    relevant_skills = {4404, 4405, 30810, 30811, 30812}
    for tid in TARGETS:
        m = monster[tid]
        a = actors[m['_ActorTableID']]
        f = files[a['_ActName']]
        assert f['_FileName'].lower() == Path(SLIME_ACT).name
        path = RESOURCE / 'script/monsterai' / m['_CustomAI'].lower()
        ai = parse_ai(stamp(path, manifest).decode('utf-8', errors='replace'))
        installed = {v for k, v in installs[m['_SkillTable']].items()
                     if k.startswith('_SkillIndex') and v > 0}
        roots = [x for x in creators if x['template'] == tid]
        families = sorted({f for x in roots for f in x['families']})
        seeds = {x['action'] for x in ai if x['action']}
        for call in ai:
            ms = call['skill']
            if not ms or ms not in installed:
                continue
            relevant_skills.add(ms)
            lv = levels_by_skill[ms]
            names = sorted({n for x in lv for n in action_tokens(x) if n in actions})
            # AI is the PvE resource selected by these monster rows. Retain PvP mappings,
            # but never use them as an extra PvE entry point.
            pve_names = sorted({n for x in lv if x.get('_ApplyType') == 0
                                for n in action_tokens(x) if n in actions})
            if call['waitorder'] is None:
                seeds.update(pve_names)
            routes = []
            for pl in command_levels[ms]:
                ps = skills.get(pl['_SkillIndex'], {})
                for name in pve_names:
                    if command_route(ps, pl, call, installed, m['_ActorTableID'], actions[name]):
                        relevant_skills.add(pl['_SkillIndex'])
                        routes.append({'player_skill': pl['_SkillIndex'], 'level_row': pl,
                                       'skill_table': ps['table'], 'effect_class': ps['_EffectClass1'],
                                       'families': family_for(pl['_SkillIndex'], skills, resonance),
                                       'action': name, 'damage_offsets': actions[name]['damage_offsets']})
            if routes:
                command_proofs.append({'template': tid, 'monster_skill': ms, 'ai': call, 'routes': routes})
            damaging = any(actions[n]['damage_offsets'] for n in pve_names)
            bounded = families if call['waitorder'] is None and damaging else sorted(
                {f for r in routes for f in r['families']})
            pairs.append({'template': tid, 'monster_skill': ms, 'ai': call, 'level_rows': lv,
                          'actions': names, 'pve_actions': pve_names,
                          'damage_offsets': {n: actions[n]['damage_offsets'] for n in names},
                          'mode': 'autonomous' if call['waitorder'] is None else 'waitorder',
                          'bounded_families': bounded, 'runtime_promote': False})
        walk = closure(actions, seeds)
        templates.append({'template': tid, 'monster_row': m, 'actor_row': a, 'file_row': f,
                          'ai_path': str(path), 'ai': ai, 'installed_skills': sorted(installed),
                          'skilltable_row': m['_SkillTable'],
                          'uninstalled_ai_skills': sorted({x['skill'] for x in ai if x['skill']} - installed),
                          'creator_families': families, 'unresolved_creator_actions':
                          [x['action'] for x in roots if not x['families']],
                          'autonomous_next_action_closure': walk,
                          'damage_actions_not_in_autonomous_closure': sorted(
                              n for n, x in actions.items() if x['damage_offsets'] and n not in walk['actions']),
                          'whole_template_single_family': False})
    selected_levels = [x for x in levels if x['_SkillIndex'] in relevant_skills]
    anomalies = [x for x in levels if 'Skill_Sacrifice' in action_tokens(x)
                 and skills.get(x['_SkillIndex'], {}).get('_NeedJob', 0) > 0
                 and skills[x['_SkillIndex']].get('_EffectClass1') != 215]
    relevant_skills.update(x['_SkillIndex'] for x in anomalies)
    relevant_skills.update(x['_SkillIndex'] for c in creators for x in c['level_rows'])
    return finalize_resource_canonical({'templates': templates, 'pairs': pairs, 'command_proofs': command_proofs,
            'creators': creators, 'creator_inbound': inbound, 'command_player_actions': command_actions,
            'slime_act': SLIME_ACT,
            'slime_actions': list(actions.values()), 'selected_level_rows': selected_levels,
            'selected_skill_rows': [skills[x] for x in sorted(relevant_skills) if x in skills],
            'resonance_edges': [x for x in resonance if any(x.get(f'_changeSkill{i}') in relevant_skills
                                                           for i in range(1, 6))],
            'summon_rows': tables['summonskilltable'], 'resource_anomalies': anomalies,
            'global_creator_exhaustiveness': False,
            'scope': {'dnt_files': list(DNT_NAMES), 'player_acts': list(PLAYER_ACTS),
                      'monster_acts': [SLIME_ACT], 'ai_modes': ['monster-row-selected PvE'],
                      'not_assumed': ['effect ACT strings are damage/control edges',
                                     'unmapped NoHuanYing creators have family 4405',
                                     'legacy aliases or element names establish canonical family']}})


def index_audit(manifest, resources):
    windows, hits, raw_points = [], [], []
    visited_raw, seen = set(), set()
    for name in NAMES:
        data = json.loads(stamp(INDEX / name, manifest).decode('utf-8-sig'))
        births = {(x['entity'], x['generation']): x for x in reversed(data['spawns'])
                  if x['template'] in TARGETS and not x.get('duplicate_spawn') and verified_birth(x)}
        owners = defaultdict(list)
        for o in data['records']:
            if o.get('template') in TARGETS and verified_owner(o):
                owners[(o['entity'], o['generation'])].append(o)
        counts = Counter()
        for h in data['hits']:
            if h.get('template') not in TARGETS:
                continue
            counts['old_target_hit_rows'] += 1
            b = births.get((h['source'], h['generation']))
            os = owners.get((h['source'], h['generation']), [])
            os = [o for o in os if b and o['template'] == b['template'] and order(b) < order(o) < order(h)]
            if not os or len({o['owner'] for o in os}) != 1:
                counts['unverified_birth_owner'] += 1
                continue
            o = min(os, key=order)
            inv = inspect_udp_damage_records(bytes.fromhex(h['wire']), h['direction'])
            matches = [r for r in inv.damage_records if r.admissible_fields and r.family == 'direct37'
                       and (r.source_actor_id, r.target_actor_id, r.damage) == (h['source'], h['target'], h['damage'])]
            if not inv.complete or len(matches) != 1:
                counts['not_unique_current_typed_direct37'] += 1
                continue
            r = matches[0]
            key = (data['capture'], h['file_offset'], h['message'], r.record_offset)
            if key in seen:
                counts['duplicate_index_projection'] += 1
                continue
            seen.add(key)
            slots = dict(r.raw_skill_slots)
            row = {**h, 'index': name, 'capture': data['capture'], 'owner': o['owner'],
                   'birth': anchor(b), 'owner_record': anchor(o), 'marker': r.record_offset,
                   'xor_phase': r.xor_phase, 'm102': slots[102], 'm114': slots[114],
                   'object': r.damage_object_id, 'wire_record_sha256': r.wire_record_sha256,
                   'pair_review': classify_pair(resources, h['template'], slots[102])}
            hits.append(row)
            counts[f"{h['template']}/m102={slots[102]}/m114={slots[114]}"] += 1
            # At most 80 exact-point reads; no scan, no whole-capture hashing.
            for point in (b, o, h):
                pk = (data['capture'], point['file_offset'])
                if pk not in visited_raw:
                    if len(visited_raw) >= 80:
                        raise ValueError('raw point budget exceeded')
                    result = reopen(data['capture'], point)
                    raw_points.append({k: v for k, v in result.items() if k not in ('wire', 'physical_frame')})
                    visited_raw.add(pk)
        windows.append({'index': name, 'capture': data['capture'], 'counts': dict(counts)})
    return {'windows': windows, 'hits': hits, 'physical_points': raw_points,
            'statistics': {'verified_hit_records': len(hits),
                           'templates': dict(Counter(str(x['template']) for x in hits)),
                           'm102': dict(Counter(str(x['m102']) for x in hits)),
                           'raw_point_reads': len(raw_points),
                           'raw_bytes_read': sum(x['bytes_read'] for x in raw_points)}}


def old_audit(manifest):
    results = []
    for rel in ('outputs/v26_summon_attack_chain/static_catalog.json',
                'outputs/v25_entity_owner_independent/static_attack_proof_ai.json',
                'outputs/v25_entity_owner_independent/static_attack_proof_expanded.json'):
        d = json.loads(stamp(ROOT / rel, manifest).decode('utf-8-sig'))
        results.append({'path': rel, 'policy': d.get('policy'),
                        'counts': {k: len(d.get(k, [])) for k in ('templates', 'accepted', 'rejected')},
                        'target_rows': {k: [x for x in d.get(k, []) if x.get('template') in TARGETS]
                                        for k in ('templates', 'accepted', 'rejected')}})
    return results


def audit():
    manifest = {}
    resources = resource_audit(manifest)
    indexed = index_audit(manifest, resources)
    old = old_audit(manifest)
    for rel in ('tools/review_magic_grass_family_v37.py', 'tools/build_live_dps_monster_catalog.py',
                'tools/audit_remaining_lineage_five_jobs_act_transitions.py',
                'tools/build_attack_instance_catalog.py', 'tools/probe_entity_creator_slot_v34.py',
                'dragon_nest_builder/live_dps/udp_damage_records.py'):
        stamp(ROOT / rel, manifest)
    return {'schema': 'magic_grass_family_v37', 'inputs': manifest, 'resources': resources,
            'old_windows': indexed, 'prior_static_audits': old,
            'decision': {'whole_template_4405': False, 'runtime_changes': False,
                         'negative_proof': '4404 -> actor 5027/order 260801 -> AI waitorder -> Attack_Explosion/type4',
                         'normal_pair_scope': 'six AI-enabled attacks verified; 4405 is creator-only, NOT an independent attack certificate',
                         'independent_normal_pair_certificates': 0,
                         'full_canonical_closure': False,
                         'blocks': ['30810/30811/30812 have no independent numeric player-family edge in reviewed tables',
                                    'NoHuanYing creator root not resolved in finite level/ACT scope',
                                    'all ACT damage actions are not equivalent to AI-enabled actions',
                                    'positive self-skill pair is absent for FFFFFFFF rows']}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write', action='store_true', help='write evidence only in the assigned output directory')
    ap.add_argument('--finalize-existing', action='store_true',
                    help='correct canonical verdicts using saved evidence only; no resource/capture replay')
    args = ap.parse_args()
    if args.finalize_existing:
        result = json.loads((OUT / 'evidence.json').read_text(encoding='utf-8'))
        finalize_resource_canonical(result['resources'])
        for hit in result['old_windows']['hits']:
            hit['pair_review'] = classify_pair(result['resources'], hit['template'], hit['m102'])
        result['decision']['normal_pair_scope'] = 'six AI-enabled attacks verified; 4405 is creator-only, NOT an independent attack certificate'
        result['decision']['independent_normal_pair_certificates'] = 0
        block = '30810/30811/30812 have no independent numeric player-family edge in reviewed tables'
        if block not in result['decision']['blocks']:
            result['decision']['blocks'].insert(0, block)
        result['canonical_finalization'] = {'uses_existing_evidence_only': True, 'raw_reads_this_revision': 0,
                                          'supersedes': 'creator-derived family=4405 in original six normal pair verdicts'}
        stamp(ROOT / 'tools/review_magic_grass_family_v37.py', result['inputs'])
    else:
        result = audit()
    if args.write:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / 'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'decision': result['decision'], 'statistics': result['old_windows']['statistics'],
                      'input_files': len(result['inputs'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
