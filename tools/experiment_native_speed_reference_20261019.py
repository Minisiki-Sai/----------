"""Frozen-axis sensitivity only, not a new full-party scoring run."""
from __future__ import annotations
import collections,json,os,statistics,time
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import score_v2 as v2
from dragon_nest_builder.live_dps.scoring import canonical_skill

SOURCE=ROOT/'outputs/verification/all_jobs_published_ab_20261019'
OLD=ROOT/'outputs/verification/published_478_485_20261019'
OUT=ROOT/'outputs/verification/scoring_optimization_20261019/accuracy_reference'

def load(p):return json.loads(p.read_text(encoding='utf-8'))

def main():
    started=time.perf_counter();os.environ['DN_SCORE_NATIVE_SPEED_REFERENCE']='1'
    data=load(SOURCE/'detailed_attribution/attribution.json');cases={};rows=[];cast_changes=[];reference_checks=[]
    for p in data['players']:
        case=p['case']
        if case not in cases:
            path=SOURCE/(case+'_v4.85.json')
            if not path.exists():path=OLD/(case+'_v4.85.json')
            cases[case]=load(path)
        full=cases[case]
        row=next(r for r in full['scoreRowsDetailed'] if r['source_id']==p['source_id'])
        rules={s['skill']:s['rule'] for s in p['skills'] if s.get('rule')}
        excluded={canonical_skill(s['skill_name']) for s in row['skill_details'] if s.get('excluded_from_score')}
        deltas=collections.defaultdict(float);cast_counts=collections.Counter()
        for c in row['theory_timeline']:
            name=canonical_skill(c['skill_name']);rule=rules.get(name)
            if name in excluded or not v2.uses_native_speed_reference(rule):continue
            old_speed=float(c.get('action_speed_output_multiplier',1.))
            new_speed=v2.speed_output_factor(rule,c.get('action_speed_percent',0),old_speed)
            change=c['power']*(new_speed/old_speed-1.) if old_speed>0 else 0.
            deltas[name]+=change;cast_counts[name]+=1
            if abs(change)>1e-7:
                cast_changes.append(dict(case=case,source_id=p['source_id'],job=p['job'],skill=name,
                    seconds=c['seconds'],speed=c.get('action_speed_percent',0),old_factor=old_speed,
                    native_factor=new_speed,old_power=c['power'],new_power=c['power']+change))
        old_t=row['theory_standard_power'];new_t=old_t+sum(deltas.values())
        old_c=row['theory_damage_completion'];new_c=100*row['actual_standard_power']/new_t if new_t>0 else 0.
        possible=[w for w in (0.,.3) if abs(row['output_score']-((1-w)*old_c+w*min(100,max(0,row['burst_usage']))))<1e-6]
        weight=possible[0] if possible else None
        # Freeze burst/support/role weights on purpose: this is a controlled
        # denominator sensitivity, not an invented full score or new plan.
        output=row['output_score']+(1-weight)*(new_c-old_c) if weight is not None else None
        total_delta=((min(100,max(0,output))-min(100,max(0,row['output_score'])))*row['output_weight']) if output is not None else None
        rows.append(dict(case=case,name=p['name'],job=p['job'],source_id=p['source_id'],
            affected=bool(deltas),old_theory=old_t,fixed_axis_theory=new_t,actual=row['actual_standard_power'],
            old_completion=old_c,fixed_axis_completion=new_c,old_output=row['output_score'],
            frozen_burst_output=output,frozen_other_total_delta=total_delta,
            skills=[dict(skill=s,theory_delta=d,theory_casts=cast_counts[s]) for s,d in deltas.items()]))
        for s,rule in rules.items():
            if not v2.uses_native_speed_reference(rule):continue
            casts=load(SOURCE/'detailed_attribution'/p['cast_ledger'])
            for c in casts:
                point=c.get('hit_evidence') or {}
                if c['skill']!=s or not point or not c.get('new'):continue
                speed=point.get('external_speed_percent',0.)
                ratio=v2.speed_output_factor(rule,speed,1.)
                reference_checks.append(dict(case=case,source_id=p['source_id'],job=p['job'],skill=s,
                    seconds=c['seconds'],speed=speed,observed_state_ratio=point.get('state_ratio'),
                    native_constant_speed_ratio=ratio,matched=abs(ratio-point.get('state_ratio',1))<1e-6))
    groups=[]
    for job in sorted({r['job'] for r in rows if r['affected']}):
        rr=[r for r in rows if r['job']==job and r['affected']]
        groups.append(dict(job=job,players=len(rr),completion_delta_min=min(r['fixed_axis_completion']-r['old_completion'] for r in rr),
            completion_delta_max=max(r['fixed_axis_completion']-r['old_completion'] for r in rr),
            frozen_total_delta_min=min(r['frozen_other_total_delta'] for r in rr),frozen_total_delta_max=max(r['frozen_other_total_delta'] for r in rr)))
    # Resolve the scalar-speed discrepancies from the stored, piecewise clock.
    # This is evidence verification only; do not silently use observed casts to
    # change a theoretical axis at different times.
    from dragon_nest_builder.live_dps.score_hit_evidence import repository
    from skill_hit_tool.speed_clock import CastSpeed
    mismatches=[r for r in reference_checks if not r['matched']]
    for case in {r['case'] for r in mismatches}:
        wanted={(r['source_id'],round(r['seconds']*1000)):r for r in mismatches if r['case']==case}
        for base in (SOURCE,OLD):
            folder=base/'state'/(case+'_v4.85')/'USERPROFILE/Documents/DragonNestDpsCapture/score_hit_evidence/evidence'
            for path in folder.glob('*.json'):
                for raw in load(path).get('cast_rows',[]):
                    r=wanted.get((raw.get('source_id'),raw.get('cast_timestamp_ms')))
                    if r is None or canonical_skill(raw.get('skill_name',''))!=r['skill'] or 'segmented_ratio' in r:continue
                    person=next(p for p in data['players'] if p['case']==case and p['source_id']==r['source_id'])
                    rule=next(s['rule'] for s in person['skills'] if s['skill']==r['skill'])
                    vv=repository().variants_for(r['job'],rule['skill_id'],r['skill'],CastSpeed(r['speed'],raw['action_speed_segments']))
                    vv=[v for v in vv if v.form==rule['reference_form']]
                    if len(vv)==1:
                        ratio=sum(h.weight or 0 for h in vv[0].hits if not h.optional)/rule['native_weight']
                        r.update(segmented_ratio=ratio,segmented_matches=abs(ratio-r['observed_state_ratio'])<1e-6,
                            speed_segments=raw['action_speed_segments'],evidence_file=str(path.relative_to(ROOT)))
    # Read actual raw Hit evidence rather than mistaking matched main-route
    # Hits for the number of all observed Hits (optional bonus included).
    evidence=SOURCE/'state/speed_0808_v4.85/USERPROFILE/Documents/DragonNestDpsCapture/score_hit_evidence/evidence'
    scythe={}
    for path in evidence.glob('*.json'):
        for r in load(path).get('cast_rows',[]):
            if r.get('source_id')=='0x5016AB52' and canonical_skill(r.get('skill_name',''))=='致命狂镰':
                key=(r['cast_timestamp_ms'],tuple(r.get('target_ids',[])))
                scythe[key]={k:r.get(k) for k in ('cast_timestamp_ms','actual_hits','matched_hits','expected_hits','expected_weight','realized_weight','raw_damage_records','target_ids','burst_parts','estimated')}
    result=dict(mode='frozen_v4.85_axis_denominator_sensitivity_only',baseline='Published v4.85 exported powers; current local native Hit resource. Not current-source full replanning.',
        actual_revaluation='On a constant-speed cast, new pre-Hit nominal/reference factor cancels in value_actual; actual damage does not get a free speed bonus.',
        limitations=['No axis rescheduling; same cast times/counts.','Burst/support/role weights frozen. Displayed total delta is isolated sensitivity, NOT a new final score.',
            'Integrated varying-speed casts use average speed in planner; per-Hit clock boundaries still need full replay validation.',
            'Native route model agreement proves internal consistency, not independently observed game truth.'],
        cases=len(cases),player_count=len(rows),affected_players=sum(r['affected'] for r in rows),changed_theory_casts=len(cast_changes),
        reference_checks=len(reference_checks),reference_check_matches=sum(r['matched'] for r in reference_checks),
        reference_mismatches=mismatches,segmented_reference_matches=sum(r.get('segmented_matches',False) for r in mismatches),groups=groups,players=rows,theory_cast_changes=cast_changes,
        scythe=dict(casts=len(scythe),extra_hit_casts=sum(r['actual_hits']>r['expected_hits'] for r in scythe.values()),
            example=next((r for r in scythe.values() if r['cast_timestamp_ms']==46672),None),casts_raw=list(scythe.values())),
        elapsed_seconds=time.perf_counter()-started)
    rejected_path=OUT/'native_speed_candidates_with_whirlwind.json'
    if rejected_path.exists():
        rejected=load(rejected_path)
        result['rejected_model_diagnostics']=dict(original_reference_checks=rejected['reference_checks'],
            original_reference_matches=rejected['reference_check_matches'],rejected=[['毁灭者','旋风'],['狂战士','旋风']],
            source='third_party/skill_hit_tool/app/act_hit_timeline_comparator/stage_estimator.py:220-279',
            reason='Observed extra compatible .038/.045 contacts are appended as nonoptional expected Hits; this makes expected/native include observed contact extensions, not only native acceleration.',
            action='Excluded both Whirlwind variants from the candidate implementation; no automatic extrapolation from observed extras.',
            rows=rejected['reference_mismatches'])
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'native_speed_experiment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k in {'cases','players','affected_players','changed_theory_casts','reference_checks','reference_check_matches','groups','elapsed_seconds'} and k!='players'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
