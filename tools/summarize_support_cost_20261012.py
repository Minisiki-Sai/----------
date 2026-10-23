"""Assemble measured offline cost facts without presenting a new score."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(name): return json.loads((ROOT/'work'/name).read_text(encoding='utf8'))
def main():
    stem='support_schedule_1003_baseline_20261012'
    base=read(stem+'.json')
    direct=read(stem+'_direct_candidates.json')
    timing=read(stem+'_timing_candidates.json')
    assert timing['timing_pool_status']['workers']==8, 'Compare with the 8-worker baseline only'
    extra=direct['filtered_search_seconds']+timing['timing_total_seconds']
    result=dict(capture=base['capture'],score_baseline_seconds=base['score_seconds'],replay_seconds_excluded=base['replay_seconds'],damage_events=base['damage_events'],direct_skills=[x['skill'] for x in direct['direct']],direct_offsets_seconds=[-5,-2,0,2,5],direct_full_scan_seconds=direct['full_sweep_search_seconds'],direct_filtered_cached_seconds=direct['filtered_search_seconds'],direct_max_error_percent_points=max(x['max_error_percent_points'] for x in direct['direct']),timing_skills=timing['timing_units'],timing_offsets_seconds=[-2,2],timing_candidate_axes=timing['timing_axis_count'],timing_candidate_seconds=timing['timing_total_seconds'],extra_search_cost_seconds=extra,sequential_baseline_plus_search_seconds=base['score_seconds']+extra,extra_fraction_of_baseline=extra/base['score_seconds'],limitations=[
        'One measured run of this capture on this machine, not a cross-capture mean or service guarantee.',
        'Bounded offsets of recorded schedules, not per-cast global search or a finished optimizer.',
        'Direct-effect candidate costs use exact final benefit calculation but do not re-simulate casting displacement, cooldown feasibility, or producer output.',
        'Timing effects use the captured scoring-preparation specifications and full recipient replanning; do not compare these pre-exclusion axes against final post-exclusion output totals.',
        'The baseline-plus-search sum is accounting of measured stages, not an end-to-end run of a changed production scoring pipeline.',
        'The filtered direct pass reuses the baseline and warmed state caches; it was checked against every full-scan candidate.',
        'No cloud writes, installer edits, production algorithm edits, or formal score replacements.'
    ])
    nine=read('dancer_axis_comparison_20261012.json')['nine_timing_sensitivity']
    result['nine_fixed_axis_sensitivity']={str(hp):next(x['total'] for x in nine if x['hypothetical_constant_hp']==hp and x['variant']=='actual')/next(x['total'] for x in nine if x['hypothetical_constant_hp']==hp and x['variant']=='theory')-1 for hp in (75,50)}
    result['nine_limitations']='Cloud compact timeline only; current-model direct-damage subtotal, fixed attack order, hypothetical constant Boss HP, no full summon integration, no CD/action displacement replan. Not the historical full-fight score correction.'
    result['timing_empty_units_excluded']=timing['empty_timing_units_excluded']
    result['limitations'].append('Scorching Flame has no available cast schedule in these preparation inputs. Empty shifts are excluded, not presented as a measured Scorching Flame optimization.')
    result['parallel_workers']=8
    path=ROOT/'work/support_schedule_cost_summary_20261012.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
