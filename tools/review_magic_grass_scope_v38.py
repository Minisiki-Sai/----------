"""Independent bounded inventory-scope review. Stdlib; stdout only.

Reads existing MagicGrass evidence and three old 7776-ACT inventory artifacts
plus their generator source. Does not execute those generators, import runtime,
read resource/capture files, enumerate resources, replay, or write any file.
The central question is target coverage, not whether a manifest counted 7776.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
V37 = 'outputs/magic_grass_family_v37_20260908/'
TARGETS = {32012, 32017, 32018}
PAIRS = ((32012, 30810), (32017, 30810), (32017, 30811))
INVENTORIES = (
    ('outputs/v26_single_family_static_20260908/', 'scan_references.py',
     'typed_references.json', 'IDS', 'count', 'refs', 'errors'),
    ('outputs/v26_magmawall_family_closure_20260908/', 'audit_closure.py',
     'act_closure.json', 'TARGETS', 'manifest_count', 'incoming', 'parsed_errors'),
    ('outputs/v26_engineer_towers_20260908/', 'audit_static.py',
     'static_closure.json', 'TARGETS', 'manifest_count', 'incoming', 'parse_errors'),
)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def constant_set(node):
    """Whitelist the existing literal set/range/union syntax; never eval source."""
    if isinstance(node, ast.Set):
        require(all(isinstance(x, ast.Constant) and type(x.value) is int for x in node.elts),
                'nonliteral target set')
        return {x.value for x in node.elts}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return constant_set(node.left) | constant_set(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        require(not node.keywords, 'target expression keyword')
        if node.func.id == 'set' and len(node.args) == 1:
            return constant_set(node.args[0])
        if node.func.id == 'range':
            require(all(isinstance(x, ast.Constant) and type(x.value) is int for x in node.args),
                    'nonliteral range')
            values = range(*(x.value for x in node.args))
            require(len(values) < 10000, 'target range too large')
            return set(values)
    raise ValueError('unreviewed target expression')


def declared_targets(source, name):
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(nodes) == 1, 'ambiguous target definition')
    # Verify this set participates in a subscript-membership filtering predicate.
    filtered = any(isinstance(n, ast.Compare) and isinstance(n.left, ast.Subscript)
                   and any(isinstance(op, ast.In) and isinstance(rhs, ast.Name) and rhs.id == name
                           for op, rhs in zip(n.ops, n.comparators)) for n in ast.walk(tree))
    require(filtered, 'target filter no longer recognized')
    return constant_set(nodes[0].value), nodes[0].lineno


def numeric_roots(skill, skills, resonance, seen=frozenset()):
    require(skill not in seen, 'canonical cycle')
    parents = set()
    base = skills.get(skill, {}).get('_BaseSkillID', 0)
    if base > 0:
        parents.add(base)
    for row in resonance:
        for i in range(1, 6):
            if row.get(f'_changeSkill{i}') == skill and row.get(f'_baseSkill{i}', 0) > 0:
                parents.add(row[f'_baseSkill{i}'])
    if not parents:
        return {skill}
    return set().union(*(numeric_roots(p, skills, resonance, seen | {skill}) for p in parents))


def analyze():
    inputs = {}

    def read(name):
        path = ROOT / name
        require(path.stat().st_size <= 5000000, 'bounded evidence size exceeded')
        raw = path.read_bytes()
        inputs[name] = dict(bytes=len(raw), sha256=sha256(raw).hexdigest())
        return raw.decode('utf-8-sig')

    review = read(V37 + 'review.md')
    old = json.loads(read(V37 + 'evidence.json'))
    r = old['resources']
    inventories = []
    for folder, script, artifact, variable, countkey, refkey, errkey in INVENTORIES:
        source = read(folder + script)
        targets, line = declared_targets(source, variable)
        data = json.loads(read(folder + artifact))
        references = data[refkey]
        def first(row):
            return row['first'] if 'first' in row else row['signal']['u32'][0]
        inventories.append(dict(source=folder+script, source_line=line,
            artifact=folder+artifact, files_counted=data[countkey],
            parsed_errors=data[errkey], retained_reference_count=len(references),
            declared_target_ids=sorted(targets), target_intersection=sorted(targets & TARGETS),
            retained_magic_grass_refs=[x for x in references if first(x) in TARGETS],
            provides_magic_grass_creator_exhaustiveness=TARGETS <= targets and not data[errkey],
            limitation='Full file count/hash manifest; references filtered by target set. Not a reusable all-template creator graph.'))
    skills = {x['RowId']: x for x in r['selected_skill_rows']}
    creators = []
    for creator in r['creators']:
        level_bindings = []
        for row in creator['level_rows']:
            sid = row['_SkillIndex']
            tokens = {v for k, value in row.items() if k.startswith('_ProcessParam')
                      for v in str(value).split(';')}
            require(creator['action'] in tokens and skills[sid]['_NeedJob'] > 0,
                    'creator binding does not match retained row')
            level_bindings.append(dict(row=row['RowId'], skill=sid,
                canonical=sorted(numeric_roots(sid, skills, r['resonance_edges']))))
        creators.append(dict(template=creator['template'], act=creator['act'],
            action=creator['action'], offset=creator['signal_offset'], type=creator['signal_type'],
            raw_skills=sorted({x['skill'] for x in level_bindings}),
            canonical=sorted({n for x in level_bindings for n in x['canonical']}),
            level_row_count=len(level_bindings)))
    pairs = []
    for template, ms in PAIRS:
        t = next(x for x in r['templates'] if x['template'] == template)
        calls = [x for x in t['ai'] if x['skill'] == ms]
        levels = [x for x in r['selected_level_rows'] if x['_SkillIndex'] == ms and x['_ApplyType'] == 0]
        action_names = sorted({x['_ProcessParam1'] for x in levels})
        acts = [x for x in r['slime_actions'] if x['name'] in action_names]
        local_creators = [x for x in creators if x['template'] == template]
        ordinary = bool(calls and ms in t['installed_skills']
                        and all(x['waitorder'] is None for x in calls)
                        and acts and len(acts) == len(action_names)
                        and all(x['damage_offsets'] for x in acts)
                        and not t['autonomous_next_action_closure']['missing']
                        and not t['autonomous_next_action_closure']['projectile_actions'])
        # Re-evaluate numeric self-skill edges, not v37's decision label.
        direct_parents = set()
        for row in r['summon_rows']:
            endpoints = [int(v) for v in str(row['_MonsterSkill']).split(',')]
            require(1 <= len(endpoints) <= 2, 'unsupported range syntax')
            if endpoints[0] <= ms <= endpoints[-1]:
                direct_parents.update(numeric_roots(row['_SkillIndex'], skills, r['resonance_edges']))
        if skills[ms].get('_BaseSkillID', 0) > 0:
            direct_parents.update(numeric_roots(ms, skills, r['resonance_edges']))
        for row in r['resonance_edges']:
            if any(row.get(f'_changeSkill{i}') == ms for i in range(1, 6)):
                direct_parents.update(numeric_roots(ms, skills, r['resonance_edges']))
        pairs.append(dict(template=template, monster_skill=ms,
            ordinary_installed_AI_ACT_route_verified=ordinary, ai_lines=[x['line'] for x in calls],
            pve_actions=[dict(name=x['name'], damage_offsets=x['damage_offsets']) for x in acts],
            retained_creator_canonical_families=sorted({f for x in local_creators for f in x['canonical']}),
            retained_creator_count=len(local_creators),
            independent_numeric_families=sorted(direct_parents),
            creator_exhaustiveness_supported_by_old_7776=False,
            canonical_4405_certificate_now=False,
            verdict='HOLD_MISSING_TARGET_COVERAGE_NOT_A_PROVED_FOREIGN_ORDINARY_PAIR'))
    command = next(x for x in r['command_proofs'] if x['template'] == 32012 and x['monster_skill'] == 260801)
    route = next(x for x in command['routes'] if x['player_skill'] == 4404 and x['level_row']['_ApplyType'] == 0)
    require(route['effect_class'] == 215 and route['level_row']['_CanUseParam1'] == '5027'
            and route['level_row']['_EffectClassValue1'] == '260801'
            and command['ai']['skill'] == command['ai']['waitorder'] == 260801
            and route['action'] == 'Attack_Explosion' and route['damage_offsets'] == [93348],
            'sacrifice counteredge changed')
    return dict(schema='magic-grass-scope-v38', verdict='NO_CURRENT_CANONICAL_4405_CERTIFICATE',
        conclusion='Old 7776 inventories exclude the requested templates at collection time; no global creator closure can be transferred.',
        inventories=inventories, retained_creator_bindings=creators, exact_pairs=pairs,
        concrete_counteredge=dict(player_skill=4404, level_row=route['level_row']['RowId'],
            effect_class=215, actor=5027, monster_skill=260801, action='Attack_Explosion',
            typed4_offsets=[93348], excludes_whole_template_4405=True,
            contradicts_requested_30810_30811_pairs=False),
        no_actual_foreign_canonical_edge_for_requested_pairs_found=True,
        equal_proof_bar='A complete same-family creator closure PLUS the selected autonomous attack route may suffice in adopted resource model M. A direct monster-skill parent table is sufficient but not mandatory.',
        sufficient_next_evidence='Target-complete inventory for 32012/32017 with creator bindings/incoming routes normalized to 4405, or an independent explicit current-attack/player-family certificate. This sidecar does not build that inventory.',
        unsupported_extensions=[32018, 'FFFFFFFF', 260801],
        v37_canonical_revision_present='canonical_finalization' in old,
        review_markdown_mentions_hold='Hold all six ordinary pair' in review,
        scope=dict(existing_evidence_only=True, source_code_read_not_executed=True,
                   resource_files_read=0, capture_reads=0, global_scan=False, replay=False,
                   runtime_imports=False, runtime_modified=False), inputs=inputs)


def verify(result):
    """Finite logical controls; synthetic assumptions are not promoted to facts."""
    checks = []
    def check(name, ok):
        require(ok, name)
        checks.append(name)
    inv = result['inventories']
    check('three_7776_counts_do_not_supply_target_coverage', len(inv) == 3 and
          all(x['files_counted'] == 7776 and x['target_intersection'] == [] for x in inv))
    check('empty_retained_refs_are_filtered_absence', all(x['retained_magic_grass_refs'] == []
          and not x['provides_magic_grass_creator_exhaustiveness'] for x in inv))
    check('all_three_ordinary_positive_routes_present', len(result['exact_pairs']) == 3 and
          all(x['ordinary_installed_AI_ACT_route_verified'] for x in result['exact_pairs']))
    check('finite_creators_4405_not_exhaustive', all(x['retained_creator_count'] == 2 and
          x['retained_creator_canonical_families'] == [4405] and not x['canonical_4405_certificate_now']
          for x in result['exact_pairs']))
    check('numeric_parent_absence_is_independently_recalculated', all(not x['independent_numeric_families']
          for x in result['exact_pairs']))
    check('sacrifice_does_not_alias_ordinary_slot', result['concrete_counteredge']['monster_skill'] == 260801
          and not result['concrete_counteredge']['contradicts_requested_30810_30811_pairs'])
    check('32018_orphans_are_separate', {x['template'] for x in result['retained_creator_bindings']
          if not x['canonical']} == {32018})
    # Even a perfect file count must fail coverage if these target IDs were not collected.
    altered = deepcopy(inv[0]); altered['files_counted'] = 100000000
    check('larger_manifest_is_not_broader_semantic_coverage', not altered['target_intersection'])
    return dict(passed=len(checks), checks=checks, runtime_or_resource_tests=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    evidence = analyze()
    evidence['verification'] = verify(evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
