"""Read-only target-complete creator census in the old unpacked resource set.

Not a runtime map. A file-count manifest alone is not a semantic inventory.
Every literal target selects a full typed parse; incoming names are searched
in a separate pass only after all creator names have been discovered.
"""
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full
from tools.build_live_dps_monster_catalog import parse_dnt

RESOURCE = Path('E:/ext0811.rar/Resource00/Resource00/resource')
OUT = ROOT / 'outputs/magic_grass_census_v38_20260908'
TARGETS = frozenset({32012, 32017})


def scan():
    manifest, errors, creators, namespace_candidates = [], [], [], []
    paths = sorted(RESOURCE.rglob('*.act'))
    literals = [struct.pack('<I', t) for t in TARGETS]
    parsed = {}
    for path in paths:
        raw = path.read_bytes()
        name = path.relative_to(RESOURCE).as_posix()
        selected = any(k in raw for k in literals)
        manifest.append(dict(path=name, sha256=sha256(raw).hexdigest(), size=len(raw), selected=selected))
        if not selected:
            continue
        try:
            full = parsed[name] = parse_full(path)
        except Exception as exc:
            errors.append(dict(path=name, error=repr(exc)))
            continue
        for action in full['actions']:
            for signal in action['signals']:
                if signal['u32'][0] not in TARGETS:
                    continue
                row = dict(path=name, action=action['name'], next_action=action['next_action'],
                    template=signal['u32'][0], type=signal['type'], offset=signal['offset'],
                    strings=signal['strings'])
                (creators if signal['type'] in (48, 126) else namespace_candidates).append(row)
    creator_names = {r['action'] for r in creators}
    names = [s.encode('utf-8') for s in creator_names]
    incoming, definitions = [], []
    # New unexpected creator names are included; no preselected name whitelist.
    for path in paths:
        raw = path.read_bytes()
        if not any(s in raw for s in names):
            continue
        name = path.relative_to(RESOURCE).as_posix()
        try:
            full = parsed.get(name) or parse_full(path)
        except Exception as exc:
            errors.append(dict(path=name, error=repr(exc), phase='incoming'))
            continue
        for action in full['actions']:
            if action['name'] in creator_names:
                definitions.append(dict(path=name, name=action['name'], offset=action['offset']))
            if action['next_action'] in creator_names:
                incoming.append(dict(path=name, source=action['name'], target=action['next_action'],
                    kind='next_action', offset=action['offset']))
            for signal in action['signals']:
                for slot, value in signal['strings']:
                    if value in creator_names:
                        incoming.append(dict(path=name, source=action['name'], target=value,
                            kind='string_semantics_unresolved', type=signal['type'],
                            offset=signal['offset'], slot=slot))

    table_manifest, bindings, table_refs, skills, parents = [], [], [], {}, defaultdict(set)
    def table(path):
        raw = path.read_bytes()
        rows = parse_dnt(path)
        table_manifest.append(dict(path=path.name, size=len(raw), sha256=sha256(raw).hexdigest(), rows=len(rows)))
        return rows

    ext = RESOURCE / 'ext'
    wanted = set()
    for path in sorted(ext.glob('*skillleveltable*.dnt')):
        for row in table(path):
            refs = {k: v for k, v in row.items() if isinstance(v, str)
                    and any(n in v for n in creator_names)}
            if not refs:
                continue
            process = {n for k, v in refs.items() if k.startswith('_ProcessParam')
                       for n in v.split(';') if n in creator_names}
            table_refs.append(dict(table=path.name, row=row, references=refs))
            for action in process:
                bindings.append(dict(table=path.name, action=action, row_id=row['RowId'],
                    skill=row.get('_SkillIndex'), apply_type=row.get('_ApplyType')))
                wanted.add(row.get('_SkillIndex'))
    for path in sorted(ext.glob('skilltable*.dnt')):
        for row in table(path):
            if row['RowId'] in wanted:
                skills.setdefault(row['RowId'], []).append(dict(table=path.name, row=row))
                if row.get('_BaseSkillID', 0) > 0:
                    parents[row['RowId']].add(row['_BaseSkillID'])
    for row in table(ext / 'runeresonatetable.dnt'):
        for i in range(1, 6):
            child, parent = row.get(f'_changeSkill{i}', 0), row.get(f'_baseSkill{i}', 0)
            if child > 0 and parent > 0:
                parents[child].add(parent)
    for row in table(ext / 'masteryskillleveltable.dnt'):
        child, parent = row.get('_SkillShowID', 0), row.get('_SkillID', 0)
        if child > 0 and parent > 0 and child != parent:
            parents[child].add(parent)
    def roots(skill, seen=frozenset()):
        if skill in seen:
            raise ValueError(f'parent cycle {skill}')
        ps = parents.get(skill)
        return set().union(*(roots(p, seen | {skill}) for p in ps)) if ps else {skill}
    for row in creators:
        matches = [b for b in bindings if b['action'] == row['action']]
        # A generic action name such as Hit has many unrelated definitions.
        # Retain candidates for discovery, never present a global name match
        # as a path-scoped skill binding (e.g. weapon/Hit != monster/Hit).
        row['unscoped_name_bindings'] = matches
        row['name_candidate_families'] = sorted({r for b in matches for r in roots(b['skill'])})
        row['binding_requires_actor_or_projectile_path'] = True
        row['incoming'] = [x for x in incoming if x['target'] == row['action']]
        row['same_name_definitions'] = [x for x in definitions if x['name'] == row['action']]
    # A script-level numeric creator or invocation is an unresolved alternative
    # until its semantics are examined, even when the typed ACT census is clean.
    lua_manifest, lua_refs = [], []
    pattern = re.compile(r'(?<!\d)(32012|32017)(?!\d)|' + '|'.join(re.escape(x) for x in creator_names))
    for path in sorted((RESOURCE / 'script').rglob('*.lua')):
        raw = path.read_bytes()
        name = path.relative_to(RESOURCE).as_posix()
        lua_manifest.append(dict(path=name, sha256=sha256(raw).hexdigest(), size=len(raw)))
        selected = [dict(line=i, text=line) for i, line in enumerate(raw.decode('utf-8-sig', errors='replace').splitlines(), 1)
                    if pattern.search(line)]
        if selected:
            lua_refs.append(dict(path=name, matches=selected))
    return dict(targets=sorted(TARGETS), manifest=manifest, parsed_count=len(parsed),
        errors=errors, creators=creators, namespace_candidates=namespace_candidates,
        incoming=incoming, definitions=definitions, table_manifest=table_manifest,
        table_references=table_refs, selected_skills=skills, lua_manifest=lua_manifest,
        lua_references=lua_refs, runtime_changes=False, confirmed_hits_added=0,
        scope='Target-complete literal+typed creator inventory and incoming candidates; resource routing model only, not engine native completeness.')


if __name__ == '__main__':
    result = scan()
    OUT.mkdir(exist_ok=True)
    (OUT/'census.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(dict(files=len(result['manifest']), parsed=result['parsed_count'], errors=result['errors'],
        creators=[{k:r[k] for k in ('path','action','template','type','offset')} for r in result['creators']],
        lua_reference_files=len(result['lua_references'])), ensure_ascii=False))
