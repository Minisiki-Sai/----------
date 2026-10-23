"""Local, read-only decomposition of the reproduced score; never uploads."""
import json
import pickle
from pathlib import Path
from collections import defaultdict
from dragon_nest_builder.live_dps.scoring import canonical_skill, _merge_intervals, _intersect_intervals, _interval_duration
from dragon_nest_builder.live_dps.event_platform import event_platform_for

ROOT = Path('outputs/verification/crusader_completion_20261019')
data = pickle.loads((ROOT/'1011硬核bluemoon十字.pkl').read_bytes())
row = next(r for r in data['rows'] if r.job == '十字军')
plan = data['plans'][row.source_id]
cloud = json.loads((ROOT/'cloud.json').read_text(encoding='utf-8'))
uploaded = next(r for r in cloud if r['summary']['id'] == 30250)
offset = plan['_score_time_offset']
pre = {(canonical_skill(c['skill']), round((c['start']+offset)*1000)): c['_score_actual_damage']
       for c in plan['_score_actual_cast_segments'][row.source_id]}
totals = defaultdict(float)
unmatched = []
for c in row.actual_timeline:
    if c.power <= 0:
        continue
    key = (canonical_skill(c.skill_name), round(c.seconds*1000))
    if key in pre:
        totals[key[0]] += pre[key]
    else:
        unmatched.append(dict(skill=key[0], seconds=c.seconds, power=c.power))
realizations = {s['skill']: s for s in uploaded['score_detail']['damage_overview']['realizations']}
skills = []
for s in row.skill_details:
    if s.theory_count <= 0 or s.actual_count <= 0:
        continue
    rate = realizations.get(s.skill_name, {})
    before = totals[s.skill_name]
    skills.append(dict(skill=s.skill_name, actual_count=s.actual_count, theory_count=s.theory_count,
        single_cast_display_rate=rate.get('multiplier_boss_rate'),
        actual_damage=s.actual_power, theory_damage=s.theory_power, completion=s.completion,
        count_ratio=s.actual_count/s.theory_count,
        average_actual_vs_theory=s.actual_power/s.actual_count/(s.theory_power/s.theory_count),
        before_v2_actual_damage=before,
        v2_net_factor=s.actual_power/before if before else None,
        before_v2_per_cast_vs_theory=before/s.actual_count/(s.theory_power/s.theory_count)))
platform = event_platform_for(data['snapshot'])
coverage = []
for skill in ('审判力量', '神圣化身'):
    observed = _merge_intervals([iv for b in platform.player_buff_series
        if b.target_id == row.source_id and canonical_skill(b.skill_name) == skill for iv in b.intervals])
    theory = _merge_intervals([(b['start']+offset,b['end']+offset) for b in plan['effect_windows']
        if canonical_skill(b.get('source','')) == skill])
    coverage.append(dict(skill=skill,
        actual_coverage=_interval_duration(_intersect_intervals(observed,row.output_windows))/_interval_duration(row.output_windows),
        theory_coverage=_interval_duration(_intersect_intervals(theory,row.output_windows))/_interval_duration(row.output_windows),
        actual_windows=observed, theory_windows=theory))
result = dict(source=data['source'],output_score=row.output_score,cloud_output_score=uploaded['summary']['output_score'],
    exact_output_match=row.output_score==uploaded['summary']['output_score'], skills=skills,
    unmatched_pre_v2_casts=unmatched, self_buff_coverage=coverage,
    note='Before-V2 totals exclude already-zero final rows. V2 net factor includes Boss calibration, missing/unknown evidence fallback and native realization; it is not the equal-weight UI realization mean.')
assert result['exact_output_match']
for s in skills:
    if s['before_v2_actual_damage']:
        reconstructed = s['count_ratio']*s['v2_net_factor']*s['before_v2_per_cast_vs_theory']*100
        assert abs(reconstructed-s['actual_damage']/s['theory_damage']*100)<1e-8
result['second_record'] = next(r for r in cloud if r['summary']['id']==25236)['summary']
(ROOT/'breakdown.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
for s in skills:
    print(s['skill'], 'display',round(s['single_cast_display_rate'] or 0,2),'count',s['actual_count'],s['theory_count'],
          'completion',round(s['completion'],2),'preV2_M',round(s['before_v2_actual_damage']/1e6,2),
          'V2factor',round(s['v2_net_factor'] or 0,4),'preV2_percast',round(s['before_v2_per_cast_vs_theory'],4))
print('unmatched',len(unmatched))
print('coverage',[(r['skill'],r['actual_coverage'],r['theory_coverage'])for r in coverage])
