"""Accept the three affected full replays, not a window's apparent gain.

The other 33 captures retain the previous full-replay result and are labelled
as such. A necessary-byte superset scan is an impact bound, not hit evidence.
This tool deliberately does not pretend to have replayed those 33 again.
"""
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/compact_entity_selectors_20260908'
BEFORE = OUT / 'self_target_frozen_after'
AFTER = OUT / 'kali_parent_frozen_after'
ALLOWED = {f'dragon_nest_builder/live_dps/{n}.py'
           for n in ('protocol_v576', 'damage_object_origins')}


def read(path):
    return json.loads(path.read_text('utf-8'))


def hashes(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in (root / 'dragon_nest_builder').rglob('*')
            if p.is_file() and (p.suffix == '.py' or '/data/' in p.as_posix())
            and '__pycache__' not in p.parts}


def compare_capture(old, new):
    assert old['capture_path'] == new['capture_path']
    assert old['capture_sha256'] == new['capture_sha256']
    assert old['decoded_events'] == new['decoded_events']
    for key in ('raw_source_invariants', 'aggregated_invariants', 'excluded',
                'outside_professions'):
        assert old[key] == new[key], (old['capture_path'], key)
    assert old['profession_summary'].keys() == new['profession_summary'].keys()
    changes = []
    for job, b in old['profession_summary'].items():
        a = new['profession_summary'][job]
        for key in ('total_direct_hits', 'total_direct_damage',
                    'unattributed_hits', 'unattributed_damage'):
            assert b.get(key, 0) == a.get(key, 0), (job, key)
        hit_gain = a.get('deterministic_hits', 0) - b.get('deterministic_hits', 0)
        damage_gain = a.get('deterministic_damage', 0) - b.get('deterministic_damage', 0)
        assert hit_gain >= 0 and damage_gain >= 0
        assert hit_gain == b.get('nondeterministic_hits', 0) - a.get('nondeterministic_hits', 0)
        assert damage_gain == b.get('nondeterministic_damage', 0) - a.get('nondeterministic_damage', 0)
        if a != b:
            assert job == '噬魂者', job
            changes.append(dict(job=job, new_confirmed_hits=hit_gain,
                                reclassified_damage=damage_gain))
    return dict(capture=old['capture_path'], capture_sha256=old['capture_sha256'],
                raw_and_aggregated_physical_unchanged=True, changes=changes)


def main():
    base = read(OUT / 'after_self_target36_frozen/profession_direct_damage_report.json')
    assert base['successful_captures'] == 36 and not base['failures']
    h0, h1 = hashes(BEFORE), hashes(AFTER)
    changed = {k for k in h0.keys() | h1.keys() if h0.get(k) != h1.get(k)}
    assert changed == ALLOWED, changed
    for k, v in base['analysis_signature']['files'].items():
        assert sha256((BEFORE / k).read_bytes()).hexdigest() == v, k
    scan = read(OUT / 'kali_parent_candidate_superset.json')
    assert scan['parser_sha256'] == h1['dragon_nest_builder/live_dps/protocol_v576.py']
    records = {r['capture_path']: r for r in base['capture_manifest']}
    assert set(records) == {r['capture'] for r in scan['captures']}
    checkpoints = {}
    for file in (OUT / 'after_self_target36_frozen/profession_direct_damage_checkpoints').glob('*.json'):
        saved = read(file)
        assert saved['analysis_signature'] == base['analysis_signature']
        report = saved['report']
        path = report['capture_path']
        assert path in records
        assert all(report[k] == value for k, value in records[path].items())
        checkpoints[path] = report
    assert checkpoints.keys() == records.keys()
    checked, inherited, merged = [], [], []
    signatures = []
    for candidate in scan['captures']:
        path = candidate['capture']
        old = records[path]
        assert old['capture_sha256'] == candidate['sha256']
        if not candidate['candidate_superset']:
            inherited.append(path)
            merged.append({**old, 'validation_scope':
                'previous_full_replay_plus_zero_candidate_impact_scan'})
            continue
        name = Path(path).stem
        new = read(OUT / 'self_target_pair' / f'{name}_after_kali.json')
        for key, value in new['runtime_py_hashes'].items():
            assert h1[key] == value, key
        for key, value in new['frozen_signature']['files'].items():
            assert sha256((AFTER / key).read_bytes()).hexdigest() == value, key
        signatures.append(new['frozen_signature'])
        checked.append(compare_capture(checkpoints[path], new))
        # No huge physical lists, and no fabricated merged skill detail table.
        merged.append({k: new[k] for k in old.keys()} | {
            'validation_scope': 'full_replay_of_increment'})
    assert len(checked) == 3 and len(inherited) == 33
    assert all(s == signatures[0] for s in signatures)
    result = dict(schema_version=1, baseline_full_replays=36,
                  increment_full_replays=checked, inherited_baseline_results=inherited,
                  changed_runtime_files=sorted(changed), data_identical=True,
                  increment_signature=signatures[0],
                  new_confirmed_hits=sum(c['new_confirmed_hits'] for r in checked for c in r['changes']),
                  all_affected_captures_physically_conserved=True)
    (OUT / 'kali_increment_acceptance.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    composed = dict(schema_version=1, professions=base['professions'],
                    analysis_signature=signatures[0],
                    validation_scope='36 baseline replays; 3 incremental full replays; 33 impact-bounded inherited rows',
                    details=[], capture_manifest=merged)
    (OUT / 'composed36_after_kali.json').write_text(
        json.dumps(composed, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in
          ('increment_signature', 'inherited_baseline_results')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
