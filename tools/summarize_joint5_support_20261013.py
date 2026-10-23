"""Collect measured A/B timing and fixed-input cohort projections."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'


def main():
    read = lambda name: json.loads((WORK/name).read_text(encoding='utf8'))
    whole = read('joint5_1004_after_20261013_comparison.json')
    diagnostics_before = read('joint5_1004_before_20261013.json')['final_score_diagnostics']
    diagnostics_after = read('joint5_1004_after_20261013.json')['final_score_diagnostics']
    cases = [whole['experiments'][0]['report'],
             read('joint5_1005_comparison_20261013.json'),
             read('joint5_1003_comparison_20261013.json')]
    rows = []
    for case in cases:
        before = {d['skill']:d for d in case['before']['details']}
        selected = next((c for c in case['candidates'] if c['name']==case['selected']), None)
        rows.append(dict(capture=Path(case['capture']).name, job=case['job'],name=case['name'],
            before_support=case['before']['support_score'],after_support=case['after']['support_score'],
            selected=case['selected'],joint_delta_pp=case['joint_delta_pp'],
            candidate_seconds=[c.get('candidate_seconds') for c in case['candidates']],
            owner_damage_change_percent=selected.get('owner_change_percent') if selected else 0,
            current_pairing_baseline_refreshed=case.get('refreshed_current_owner_baseline',False),
            details=[dict(skill=d['skill'],before_completion=before[d['skill']]['completion'],
                          after_completion=d['completion'],actual=d['actual'],
                          before_theory=before[d['skill']]['theory'],after_theory=d['theory'])
                     for d in case['after']['details']]))
    report = dict(full_score_timing=dict(
        before_seconds=whole['before_score_seconds'],after_seconds=whole['after_score_seconds'],
        optimizer_seconds=whole['experiments'][0]['elapsed_seconds'],
        before_replay_seconds=whole['before_replay_seconds'],after_replay_seconds=whole['after_replay_seconds'],
        before_final_stage=diagnostics_before,after_final_stage=diagnostics_after),
        cases=rows,production_enabled=False,uploaded=False,
        notes=['Single-run whole-score timings are not a speedup claim; optimizer cost is measured separately.',
               '1004 is a complete uncached score A/B. Other captures reuse saved full combat contexts and recompute owner axes/support scores.',
               'All arms use current Nine stage-one/stage-two pairing, unchanged captured facts and external haste.',
               'Limited peak candidate search; fixed teammate events, not a global coupled eight-player optimum.'])
    target=WORK/'附近5秒高峰_耗时与刀锋噬魂辅助分对照_20261013.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
