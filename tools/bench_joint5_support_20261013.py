"""Sequential uncached whole-score A/B, with one bounded owner replan.

Offline only: baseline() disables uploads and score cache reads/writes.
Replays are timed separately. No other player's native axis is regenerated
by the delay optimizer. Each arm still performs the normal full team score.
"""
import argparse
import copy
import json
import pickle
import time
from pathlib import Path

from bench_real_support_schedule_20261012 import baseline
from probe_blade_joint_delay_20261013 import run


def compare(capture, before_path, output, job):
    if not before_path.exists():
        baseline(capture, before_path)
    before = pickle.loads(before_path.with_suffix('.pickle').read_bytes())
    assert Path(before['capture']).resolve() == capture.resolve()
    experiments = []
    seen = set()

    def transform(core, engine, snapshot, windows, targets, plans, context):
        if id(plans) in seen:
            return
        seen.add(id(plans))
        config = {k:copy.deepcopy(getattr(engine,k)) for k in before['engine_config']}
        data = dict(snapshot=snapshot, windows=windows, targets=targets,
                    context=context, engine_config=config, plans=plans,
                    specs=core.score_theory_planner._team_score_replan_specs_by_source,
                    capture=str(capture))
        player = next(p for p in snapshot.players if p.job == job)
        source = player.source_id
        # Check the normal pipeline produced the same axis in both arms.
        signature = lambda p: [(s.get('skill'),s.get('kind'),s.get('start'),s.get('end'),s.get('effective_score')) for s in p['segments']]
        assert signature(plans[source]) == signature(before['plans'][source]), 'A/B owner baseline drift'
        assert all(signature(p) == signature(before['plans'][s]) for s,p in plans.items()), 'A/B teammate baseline drift'
        saved_actual = [(e.source_id,e.timestamp_ms,e.amount) for e in context.team_damage_events]
        started = time.perf_counter()
        report, picked = run(None, output.with_name(output.stem+'_optimizer.json'),
                             job=job, five_only=True, data=data,
                             verify_baseline=False, reset_pool=False)
        elapsed = time.perf_counter()-started
        assert saved_actual == [(e.source_id,e.timestamp_ms,e.amount) for e in context.team_damage_events]
        picked = dict(picked)  # Rejected candidate returns the baseline object.
        plans[source].clear()
        plans[source].update(picked)
        experiments.append(dict(elapsed_seconds=elapsed, report=report))

    def finish(core, data, engine):
        assert len(experiments) == 1, 'optimizer must run exactly once per whole score'
        old = {r.source_id:r for r in before['rows']}
        players = []
        for row in data['rows']:
            prev = old[row.source_id]
            if row.job == job:
                assert abs(row.support_score-experiments[0]['report']['after']['support_score']) < 1e-7, 'final score differs from candidate evaluation'
            old_details = {(d.skill_name,d.effect_key):d for d in prev.support_details}
            for detail in row.support_details:
                former = old_details[(detail.skill_name,detail.effect_key)]
                for field in ('actual_contribution','actual_coverage','actual_intervals','effective_damage_coverage'):
                    assert getattr(detail,field) == getattr(former,field), (row.name,detail.skill_name,field)
            players.append(dict(name=row.name,job=row.job,
                before_support=prev.support_score,after_support=row.support_score,
                before_output=prev.output_score,after_output=row.output_score,
                details=[dict(skill=d.skill_name,before_completion=old_details[(d.skill_name,d.effect_key)].support_benefit_completion,
                              after_completion=d.support_benefit_completion,
                              before_theory=old_details[(d.skill_name,d.effect_key)].theory_contribution,
                              after_theory=d.theory_contribution,actual=d.actual_contribution)
                         for d in row.support_details] if row.job == job else []))
        report = dict(capture=str(capture),job=job,before_score_seconds=before['score_seconds'],
            after_score_seconds=data['score_seconds'],difference_seconds=data['score_seconds']-before['score_seconds'],
            before_replay_seconds=before['replay_seconds'],after_replay_seconds=data['replay_seconds'],
            experiments=experiments,players=players,actual_unchanged=True,
            production_enabled=False,uploaded=False,
            timing_note='Sequential single-run uncached score A/B; replay separate; machine load/warm runtime can change non-optimizer time.')
        target = output.with_name(output.stem+'_comparison.json')
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print('COMPARISON', json.dumps({k:report[k] for k in ('before_score_seconds','after_score_seconds','difference_seconds')},ensure_ascii=False), flush=True)

    baseline(capture,output,after_score=finish,transform_plans=transform)


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('capture',type=Path)
    p.add_argument('--before',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--job',choices=('刀锋舞者','噬魂者'),required=True)
    a=p.parse_args()
    compare(a.capture,a.before,a.output,a.job)
