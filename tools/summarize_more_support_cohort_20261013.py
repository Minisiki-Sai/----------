"""Summarize complete, paired, locally measured score runs only."""
import json
from pathlib import Path
from bench_more_support_cohort_20261013 import CASES, ROOT


def main():
    cases = []
    for key, capture, job in CASES:
        path = ROOT/f'work/cohort5_{key}_after_20261013_comparison.json'
        whole = json.loads(path.read_text(encoding='utf8'))
        exp = whole['experiments'][0]
        report = exp['report']
        player = next(p for p in whole['players'] if p['job'] == job)
        selected = next((c for c in report['candidates'] if c['name']==report['selected']), None)
        row = dict(capture=capture, job=job, name=player['name'],
            before_seconds=whole['before_score_seconds'],after_seconds=whole['after_score_seconds'],
            full_score_delta_seconds=whole['difference_seconds'],
            optimizer_extra_seconds=exp['elapsed_seconds'],
            before_replay_seconds=whole['before_replay_seconds'],
            after_replay_seconds=whole['after_replay_seconds'],
            before_support=player['before_support'],after_support=player['after_support'],
            support_delta=player['after_support']-player['before_support'],
            before_output=player['before_output'],after_output=player['after_output'],
            selected=report['selected'],joint_delta_pp=report['joint_delta_pp'],
            accepted_owner_damage_change_percent=selected['owner_change_percent'] if selected else 0.,
            support_cast_counts_before=report['baseline_support_cast_counts'],
            support_cast_counts_after=selected['support_cast_counts'] if selected else report['baseline_support_cast_counts'],
            details=player['details'],
            candidates=[{k:c.get(k) for k in (
                'name','valid','reason','candidate_seconds','native_seconds',
                'owner_change_percent','joint_delta_pp','support_score')}
                for c in report['candidates']],
            actual_unchanged=whole['actual_unchanged'])
        cases.append(row)
    result = dict(cases=cases, production_enabled=False, uploaded=False,
        scoring_preset='Default scoring equipment and 红龙佩达 boss preset, identical in both arms.',
        notes=[
            'Each capture has a complete uncached baseline and complete experimental score.',
            'Replay/loading time is excluded from score time and listed separately.',
            'One sequential run per arm; full-score differences also contain runtime/load variance.',
            'Optimizer time includes candidate screening, one native owner replan and scoring checks, including rejected candidates.',
            'Other seven axes and accelerator timings are fixed; observed contributions must remain identical.',
            'Candidate is accepted only if modeled owner-plus-team gain improves; lower auxiliary score is not the selection criterion.',
            'Five-second peak search is not a global optimum. No live application or cloud scores changed.',
        ])
    target = ROOT/'work/新增四抓包_5秒高峰_完整评分对照_20261013.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
