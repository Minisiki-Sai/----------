"""Full uncached replay score with an offline-only curve support hook."""
import argparse
import dataclasses
import json
import pickle
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bench_real_support_schedule_20261012 import baseline
from curve_support_experiment_20261013 import optimize

def main():
    p=argparse.ArgumentParser()
    p.add_argument('capture',type=Path)
    p.add_argument('--before',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
    original=TeamScoreEngine._build_support_counterfactual_context
    reports=[]; seen=set()
    def curve_context(self,snapshot,windows,targets=None,plans=None):
        context=original(self,snapshot,windows,targets,plans)
        if plans and id(plans) not in seen:
            seen.add(id(plans))
            report=optimize(self,snapshot,windows,targets,plans,context)
            reports.append(report)
            print('CURVE_OPTIMIZER',json.dumps(report,ensure_ascii=False),flush=True)
        return context
    TeamScoreEngine._build_support_counterfactual_context=curve_context
    def finish(core,data,engine):
        before=pickle.loads(a.before.read_bytes())
        old={r.source_id:r for r in before['rows']}
        rows=[]; actual_changes=[]; damage_changes=[]
        for row in data['rows']:
            previous=old[row.source_id]
            details=[]
            old_details={(d.skill_name,d.effect_key):d for d in previous.support_details}
            for detail in row.support_details:
                former=old_details.get((detail.skill_name,detail.effect_key))
                if former is None: continue
                for field in ('actual_contribution','actual_coverage','actual_intervals',
                              'actual_display_intervals','effective_damage_coverage'):
                    if getattr(former,field)!=getattr(detail,field):
                        actual_changes.append([row.source_id,detail.skill_name,field])
                if former.theory_contribution!=detail.theory_contribution:
                    details.append(dict(skill=detail.skill_name,effect=detail.effect_key,
                        actual_gain=detail.actual_contribution,
                        before_theory_gain=former.theory_contribution,
                        after_theory_gain=detail.theory_contribution,
                        before_completion=former.support_benefit_completion,
                        after_completion=detail.support_benefit_completion))
            old_damage=[s for s in before['plans'][row.source_id].get('segments',[]) if s.get('kind')=='damage']
            new_damage=[s for s in data['plans'][row.source_id].get('segments',[]) if s.get('kind')=='damage']
            if old_damage!=new_damage: damage_changes.append(row.source_id)
            rows.append(dict(source_id=row.source_id,name=row.name,job=row.job,
                before_support=previous.support_score,after_support=row.support_score,
                before_output=previous.output_score,after_output=row.output_score,
                before_total=previous.total_score,after_total=row.total_score,details=details))
        report=dict(capture=data['capture'],before_score_seconds=before['score_seconds'],
            after_score_seconds=data['score_seconds'],difference_seconds=data['score_seconds']-before['score_seconds'],
            before_replay_seconds=before['replay_seconds'],after_replay_seconds=data['replay_seconds'],
            optimizer_reports=reports,players=rows,actual_changes=actual_changes,
            damage_axis_changes=damage_changes,extra_theory_axes=0,
            production_enabled=False,uploaded=False,
            limitations=['Single uncached before/after run; timings have machine-load variance.',
                         'Theory curve screens; unchanged scorer prices both schedules on its shared observed damage basis.',
                         'Only independent Ghost Soul/Grudge Statue buffs enabled in this first experiment.',
                         'No invented cast space: attacks frozen, free-slot placement only; no global optimum claim.',
                         'No damage-axis repricing or output-score change claimed: this trial modifies auxiliary timing only.'])
        target=a.output.with_name(a.output.stem+'_comparison.json')
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        assert not actual_changes, actual_changes
        assert not damage_changes, damage_changes
        print('COMPARISON',json.dumps({k:report[k] for k in ('before_score_seconds','after_score_seconds','difference_seconds','players')},ensure_ascii=False),flush=True)
    try:
        baseline(a.capture,a.output,after_score=finish)
    finally:
        TeamScoreEngine._build_support_counterfactual_context=original

if __name__=='__main__': main()
