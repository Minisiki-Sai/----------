"""Reproduce common damaging plate mappings from exact client DNT/ACT joins.

Read-only: it does not import generated proposals into production. Shared
configurations stay ambiguous even when both skills have the same UI name.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_dungeon_map_catalog import parse_dnt
from tools.build_attack_instance_catalog import (
    _is_action_key, _is_player_combat_act_path, _iter_level_rows,
    _unique_action_continuations, parse_act,
)


def audit(resource_root: Path) -> dict:
    ext = resource_root / 'ext'
    inputs = {}
    errors = []

    def record(path):
        inputs[str(path.relative_to(resource_root)).replace('\\', '/')] = sha256(path.read_bytes()).hexdigest()

    skill_paths = [p for p in sorted(ext.glob('skilltable_character*.dnt')) if p.stat().st_size]
    skills = {int(r['RowId']): r for p in skill_paths for r in parse_dnt(p)}
    for p in skill_paths: record(p)
    common = {k: r for k, r in skills.items()
              if 80000 <= k < 81000 and int(r.get('_NeedJob', -1)) == 0 and r.get('_NameID')}
    keys = defaultdict(set)
    bykey = defaultdict(set)
    for p in sorted(ext.glob('skillleveltable_character*.dnt')): record(p)
    for r in _iter_level_rows(ext):
        sid = int(r.get('_SkillIndex', 0) or 0)
        if sid not in skills or int(r.get('_ApplyType', 0) or 0): continue
        for i in range(1, 11):
            for key in str(r.get(f'_ProcessParam{i}', '') or '').split(';'):
                key = key.strip()
                if _is_action_key(key):
                    keys[sid].add(key)
                    bykey[key.casefold()].add(sid)
    projectile_rows = parse_dnt(ext / 'weapontable_projectile.dnt')
    file_rows = parse_dnt(ext / 'filetable.dnt')
    for p in (ext / 'weapontable_projectile.dnt', ext / 'filetable.dnt'): record(p)
    pids = {int(r['RowId']) for r in projectile_rows}
    files = {int(r['RowId']): str(r.get('_FileName', '') or '') for r in file_rows}
    ui = resource_root / 'uistring/uistring0818.xml'
    record(ui)
    names = {int(e.attrib['mid']): ''.join(e.itertext()).strip()
             for e in ET.parse(ui).iter() if e.attrib.get('mid', '').isdigit()}
    matched = defaultdict(list)
    all_typed = defaultdict(set)
    player_root = resource_root / 'char/player'
    for path in sorted(player_root.rglob('*.act')):
        if not _is_player_combat_act_path(path, player_root): continue
        record(path)
        try: parsed = parse_act(path)
        except Exception as exc:
            errors.append({'file': str(path.relative_to(resource_root)), 'error': str(exc)})
            continue
        continuations = _unique_action_continuations(parsed, bykey)
        for action in parsed.actions:
            owners = bykey.get(action.name.casefold(), set())
            for signal in action.signals:
                if signal.signal_type == 15:
                    pid = int.from_bytes(signal.payload[:4], 'little')
                    if pid in pids: all_typed[pid].update(owners)
            for sid in owners & common.keys():
                for part in continuations.get(action.name.casefold(), (action,)):
                    sigs = []
                    for signal in part.signals:
                        if signal.signal_type not in {4, 15}: continue
                        pid = int.from_bytes(signal.payload[:4], 'little') if signal.signal_type == 15 else 0
                        if signal.signal_type == 15 and pid not in pids: continue
                        sigs.append({'type': signal.signal_type, 'projectile': pid, 'order': signal.order})
                    if sigs:
                        matched[sid].append({'file': str(path.relative_to(resource_root)).replace('\\', '/'),
                                             'entry': action.name, 'action': part.name, 'signals': sigs})
    edges = defaultdict(set)
    for row in projectile_rows:
        pid = int(row['RowId'])
        filename = files.get(int(row.get('_ActName', 0) or 0), '')
        path = resource_root / 'weapon' / filename
        if not filename or not path.is_file(): continue
        record(path)
        try: parsed = parse_act(path)
        except Exception as exc:
            errors.append({'file': str(path.relative_to(resource_root)), 'error': str(exc)})
            continue
        for action in parsed.actions:
            for signal in action.signals:
                if signal.signal_type == 15:
                    child = int.from_bytes(signal.payload[:4], 'little')
                    if child in pids and child != pid: edges[pid].add(child)
    plates = []
    candidates = defaultdict(set)
    for sid, row in sorted(common.items()):
        roots = {s['projectile'] for action in matched[sid] for s in action['signals'] if s['type'] == 15}
        seen = set(roots)
        pending = list(roots)
        while pending:
            parent = pending.pop()
            for child in edges[parent] - seen:
                seen.add(child)
                pending.append(child)
        for pid in seen: candidates[pid].add(sid)
        plates.append({'id': sid, 'name_id': int(row['_NameID']), 'name': names.get(int(row['_NameID']), ''),
                       'keys': sorted(keys[sid]), 'matches': matched[sid], 'typed_projectiles': sorted(seen)})
    # Include foreign skills using an identical typed root configuration.
    for pid in candidates: candidates[pid].update(all_typed[pid])
    return {'schema': 'common-plate-static-graph-v1', 'resource_root': str(resource_root.resolve()),
            'raw_source_sha256': dict(sorted(inputs.items())), 'plates': plates,
            'projectile_child_edges': {str(k): sorted(v) for k, v in edges.items() if k in candidates},
            'root_projectile_all_skill_candidates': {str(k): sorted(v) for k, v in sorted(candidates.items())},
            'unique_projectiles': {str(k): next(iter(v)) for k, v in sorted(candidates.items()) if len(v) == 1},
            'ambiguous_projectiles': {str(k): sorted(v) for k, v in sorted(candidates.items()) if len(v) > 1},
            'parse_errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-root', type=Path, default=Path('E:/ext0811.rar/Resource00/Resource00/resource'))
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/common_plate_static_graph.json')
    args = parser.parse_args()
    result = audit(args.resource_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'output': str(args.output.resolve()), 'damaging_plates': sum(bool(p['matches']) for p in result['plates']),
                      'unique_projectiles': len(result['unique_projectiles']),
                      'ambiguous_projectiles': result['ambiguous_projectiles'], 'hashed_inputs': len(result['raw_source_sha256'])}, ensure_ascii=True))


if __name__ == '__main__': main()
