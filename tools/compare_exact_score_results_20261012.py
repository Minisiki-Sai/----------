"""Compare saved full-score benchmarks without rounding any score value."""
import argparse
import dataclasses
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def differences(a, b, path=''):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys(), key=str):
            if key == 'baseline_elapsed':  # diagnostic wall time, not scoring
                continue
            if key not in a or key not in b:
                yield path + '/' + str(key)
            else:
                yield from differences(a[key], b[key], path + '/' + str(key))
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            yield path + '/length'
        for i, (x, y) in enumerate(zip(a, b)):
            yield from differences(x, y, path + '/' + str(i))
    elif a != b:
        yield path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('before', type=Path)
    p.add_argument('after', type=Path)
    p.add_argument('report', type=Path)
    a = p.parse_args()
    before, after = (pickle.loads(path.read_bytes()) for path in (a.before, a.after))
    players_before = {r.source_id: dataclasses.asdict(r) for r in before['rows']}
    players_after = {r.source_id: dataclasses.asdict(r) for r in after['rows']}
    changed = list(differences(players_before, players_after))
    plan_changed = list(differences(before['plans'], after['plans']))
    score_fields = ('grade', 'total_score', 'output_score', 'support_score',
                    'specialty_score', 'survival_penalty', 'actual_standard_power',
                    'theory_standard_power')
    scores_equal = all(
        players_before[source][key] == row[key]
        for source, row in players_after.items() for key in score_fields
    ) and players_before.keys() == players_after.keys()
    float_deltas = []
    for path in changed:
        left, right = players_before, players_after
        for part in path.lstrip('/').split('/'):
            left = left[int(part)] if isinstance(left, (list, tuple)) else left[part]
            right = right[int(part)] if isinstance(right, (list, tuple)) else right[part]
        if isinstance(left, float) and isinstance(right, float):
            float_deltas.append(dict(path=path, before=left, after=right, delta=abs(left-right)))
    result = dict(
        before_seconds=before['score_seconds'], after_seconds=after['score_seconds'],
        saved_seconds=before['score_seconds'] - after['score_seconds'],
        saved_percent=100 * (1 - after['score_seconds'] / before['score_seconds']),
        before_replay_seconds=before['replay_seconds'], after_replay_seconds=after['replay_seconds'],
        players=len(players_after), exact_player_results_equal=not changed,
        exact_scores_and_standard_power_equal=scores_equal,
        float_detail_differences=float_deltas,
        notes=[
            'Wall-clock comparison is a single before/after run, not a multi-run median.',
            'Replay/load time is reported separately and excluded from scoring time.',
            'Only diagnostic baseline_elapsed fields are excluded from the recursive theory-plan comparison.',
            'Tests are run separately; this file reports measured times and data comparisons only.',
        ],
        changed_player_fields=changed, changed_plan_fields=plan_changed,
        players_summary=[dict(name=r.name, job=r.job, output_score=r.output_score,
            support_score=r.support_score, total_score=r.total_score) for r in after['rows']],
    )
    a.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if changed else 0


if __name__ == '__main__':
    raise SystemExit(main())
