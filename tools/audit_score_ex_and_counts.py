"""Read-only source-eligibility audit and historical count anomaly screening.

Historical cache rows are leads, not fresh replay results or proof of a bug.
Only read locally produced trusted score caches (pickle).
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import zlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import _builtin_skill_builds, canonical_skill
from dragon_nest_builder.shared import TEAM_MANUAL_DEBUFFS
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cache', type=Path, default=Path.home() / 'Documents/DragonNestDpsCapture/team_score_cache')
    args = p.parse_args()
    store = DataStore.load()
    svc = LegacyTimelineService(store, BuildCalculator(store))
    eligibility = []
    jobs = _builtin_skill_builds()
    for job, default in jobs.items():
        build = dict(default, second_job=job)
        # Reproduce the combat pricing name alias with default active levels;
        # this is a controlled model audit, not claimed capture evidence.
        levels = default.get('skill_build', {}).get('levels', {})
        tree = store.skill_build_tree(job, 70).get('skills', {})
        build['_score_combat_skill_levels'] = {'levels': {
            canonical_skill(item['name']): int(levels.get(str(sid), 0))
            for sid, item in tree.items() if int(item.get('skill_type', 0)) == 0}}
        rt = svc.extreme_runtime(build)
        for effect in TEAM_MANUAL_DEBUFFS:
            if not effect.upper().endswith('EX'):
                continue
            old = rt._skill_build_level_for_burst_sim(effect)
            new = rt._skill_burst_learned_ex_node_level(effect)
            if old and old > 0 and new == 0:
                eligibility.append({'job': job, 'effect': effect, 'old_alias_level': old,
                                    'exact_ex_node_level': new})
    candidates = defaultdict(list)
    seen = set()
    failures = []
    rows_read = 0
    files = sorted(args.cache.glob('*.scorecache'))
    for path in files:
        try:
            payload = pickle.loads(zlib.decompress(path.read_bytes()))
            for row in payload.get('rows', ()):
                rows_read += 1
                for detail in row.skill_details:
                    a, t = detail.actual_count, detail.theory_count
                    if (detail.excluded_from_score or a < 10 or t < 3
                            or a - t < 8 or a < t * 1.4):
                        continue
                    key = (row.job, detail.skill_name)
                    identity = (row.source_id, row.name, *key, a, t)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    candidates[key].append({'cache_file': path.name, 'name': row.name,
                                            'actual': a, 'theory': t, 'ratio': a / t})
        except Exception as exc:
            failures.append({'file': path.name, 'error': str(exc)})
    grouped = [{'job': job, 'skill': skill, 'examples': sorted(examples, key=lambda r: r['ratio'], reverse=True)}
               for (job, skill), examples in sorted(candidates.items())]
    result = {'scope': 'historical local score-cache screening; not fresh recomputation',
              'jobs_audited': len(jobs), 'invalid_ex_aliases': eligibility,
              'cache_files_read': len(files), 'score_rows_read': rows_read,
              'count_candidates': grouped, 'read_failures': failures}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('jobs', len(jobs), 'invalid aliases', len(eligibility), 'caches', len(files), 'rows', rows_read)
    print('EX', json.dumps(eligibility, ensure_ascii=False))
    for row in grouped:
        print(row['job'], row['skill'], len(row['examples']), row['examples'][0])
    print('read failures', len(failures))


if __name__ == '__main__':
    main()
