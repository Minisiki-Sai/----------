"""Keep the OLD skill pool deliberately enabled to test the EX gate itself."""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('specs', type=Path)
    parser.add_argument('--job', default='冰灵')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.specs.open('rb') as handle:
        spec = next(s for s in pickle.load(handle)['specs'] if s['job'] == args.job)
    store = DataStore.load()
    rt = LegacyTimelineService(store, BuildCalculator(store)).extreme_runtime(spec['build_override'])
    report = spec['build_override']['_score_combat_skill_levels']
    method_source = rt._timeline_method_source_type
    original = method_source._skill_burst_support_actions
    actions_seen = []
    def audited(self, metrics):
        actions = original(self, metrics)
        if not actions_seen:
            actions_seen.extend({k: a.get(k) for k in ('skill', 'type', 'attrs', 'owner_resist_damage_weights')}
                                for a in actions)
        return actions
    method_source._skill_burst_support_actions = audited
    start = time.perf_counter()
    result = rt._extreme_timeline_result_from_spec(spec)
    elapsed = time.perf_counter() - start
    segments = result['segments']
    output = {'job': args.job, 'seconds': elapsed,
              'old_skill_levels_kept': report['levels'],
              'opening': [{k: s.get(k) for k in ('skill', 'start', 'end', 'kind', 'type')} for s in segments[:12]],
              'counts': dict(Counter(s['skill'] for s in segments)),
              'support_actions': actions_seen,
              'total': result.get('total'), 'dps': result.get('dps')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
