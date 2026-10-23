"""Compare saved full-capture scores; never uploads or modifies recordings."""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.scoring import canonical_skill


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--specs', type=Path, required=True,
                        help='Trusted local score_v410_replay --dump-specs output')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding='utf-8'))
    after = json.loads(args.after.read_text(encoding='utf-8'))
    with args.specs.open('rb') as handle:
        specs = pickle.load(handle)['specs']
    reports = {report['source_id']: report for spec in specs
               if (report := spec.get('build_override', {}).get('_score_combat_skill_levels'))}
    old_rows = {r['source_id']: r for r in before['scoreRowsDetailed']}
    rows = []
    violations = []
    for row in after['scoreRowsDetailed']:
        source = row['source_id']
        old = old_rows[source]
        excluded = {canonical_skill(s['skill']) for s in reports[source]['skills']
                    if s['status'] == 'unused_theory_excluded'}
        scheduled = {canonical_skill(s['skill_name']) for s in row['theory_timeline']}
        invalid = sorted(excluded & scheduled)
        if invalid:
            violations.append({'source': source, 'skills': invalid})
        previous = {s['skill_name']: s for s in old['skill_details']}
        current = {s['skill_name']: s for s in row['skill_details']}
        rows.append({
            'source': source, 'name': row['name'], 'job': row['job'],
            'excluded': sorted(excluded), 'invalid_scheduled': invalid,
            'opening_before': [s['skill_name'] for s in old['theory_timeline'][:10]],
            'opening_after': [s['skill_name'] for s in row['theory_timeline'][:10]],
            'output_before': old['output_score'], 'output_after': row['output_score'],
            'skills': [{'skill': skill,
                        'actual_before': previous.get(skill, {}).get('actual_count', 0),
                        'actual_after': current.get(skill, {}).get('actual_count', 0),
                        'theory_before': previous.get(skill, {}).get('theory_count', 0),
                        'theory_after': current.get(skill, {}).get('theory_count', 0)}
                       for skill in sorted(previous.keys() | current.keys())],
        })
    result = {'before': str(args.before), 'after': str(args.after),
              'score_seconds_before': before['scoreSeconds'],
              'score_seconds_after': after['scoreSeconds'],
              'players': rows, 'violations': violations}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if violations:
        raise SystemExit('Unused active skills still present in theory axis')


if __name__ == '__main__':
    main()
