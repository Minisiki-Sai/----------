"""Read-only model audit from uploaded inputs; not a packet-based rescore."""
import copy
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, SkillProfile, _ObservedCast


def main():
    detail = json.loads((ROOT / 'work/nanako_20261003_201231_score_20261012.json').read_text(encoding='utf-8'))[0]['score_detail']
    timeline = detail['compact_timeline']
    def decode(lane):
        at = 0
        for row in timeline[lane]:
            at += row[0]
            yield {'start': at / timeline['q'], 'end': (at + row[1]) / timeline['q'],
                   'skill': timeline['n'][row[2]], 'extra': row[3:]}
    effects = list(decode('e'))
    burst = [(x['start'], x['end']) for x in effects if x['skill'].startswith('精神爆发 ·') and x['extra'] == [0]]
    accel = [(x['start'], x['end']) for x in effects if x['skill'].startswith('时间加速 ·') and x['extra'] == [0]]
    duration = timeline['d'] / timeline['q']
    output, at = [], 0
    for row in timeline['o']:
        at += row[0]
        output.append((at / timeline['q'], (at + row[1]) / timeline['q']))
    blocked, end = [], 0
    for a, b in output:
        if a > end:
            blocked.append((end, a))
        end = b
    build = copy.deepcopy(detail['scoring_equipment']['build'])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    service = LegacyTimelineService(store, calculator)
    runtime = service._runtime(build, {'window': duration, 'mode': 'long', 'cocktail': False,
                                      'blockedIntervals': blocked, 'burstIntervals': burst, 'accelIntervals': accel})
    runtime._skill_burst_schedule_override = {'window': duration, 'burst': burst, 'accel': accel, 'cocktail': False}
    _, summary = calculator.calculate(copy.deepcopy(build))
    runtime._skill_burst_base_summary = summary
    actions = runtime._skill_burst_support_actions({})
    dance = [x for x in actions if x['skill'] in ('九天之舞', '九天之舞2阶')]
    result = {'not_full_rescore': True, 'missing': ['per-hit damage', 'boss HP', 'full recipient status'],
              'dance_actions': [{k: x.get(k) for k in ('skill','base_cooldown','duration','attrs','active_multiplier')} for x in dance]}
    theory = list(decode('t'))
    result['dance_readiness'] = [
        {**x, 'ready_at_current_model': runtime._skill_burst_cooldown_ready_at(x['skill'], 90, x['start'], {})}
        for x in theory if x['skill'] in ('九天之舞','九天之舞2阶')]
    result['speed_probe'] = {}
    for speed in (0,40):
        persistent = runtime._skill_burst_replay_persistent_action_speed(speed, 55, {})
        result['speed_probe'][str(speed)] = {
            'current_replay': runtime._skill_burst_action_output_multiplier('烈风痴狂',55,{'动作速度%':persistent}),
            'without_observed_speed_dedup': runtime._skill_burst_action_output_multiplier('烈风痴狂',55,{'动作速度%':speed})}
    # Same cast and same window: isolate endpoint accounting from all Buffs.
    runtime._skill_burst_blocked_intervals = []
    runtime._skill_burst_schedule_override = {'window': 3, 'burst': [], 'accel': [], 'cocktail': False}
    profile = SkillProfile('烈风痴狂','烈风痴狂',100,22,4)
    actual = TeamScoreEngine._value_actual_casts(
        [_ObservedCast(1,'烈风痴狂','direct',4)],
        {'烈风痴狂':profile},((0,3),),'刀锋舞者')[0]
    theory = runtime._skill_burst_prorate_window_score(100,{'skill':'烈风痴狂'}, {},1,3,4)
    result['identical_cast_boundary_probe'] = {
        'cast_start':1, 'cast_duration':4, 'output_window':[0,3],
        'full_power':100, 'actual_power':actual.power, 'theory_power':theory,
        'actual_proportional':actual.proportional_output}
    runtime._skill_burst_blocked_intervals = blocked
    runtime._skill_burst_schedule_override = {'window': duration, 'burst': burst, 'accel': accel, 'cocktail': False}
    result['burst_alignment'] = []
    for skill in ('幽灵之魂 · 四维%','九天之舞2阶 · 团队Buff'):
        for lane in (0,1):
            intervals = [(x['start'],x['end']) for x in effects if x['skill']==skill and x['extra']==[lane]]
            overlaps = [sum(max(0,min(b,z)-max(a,s)) for a,b in intervals) for s,z in accel]
            result['burst_alignment'].append({'skill':skill, 'lane':lane, 'overlap_by_acceleration':overlaps,
                                               'total_overlap':sum(overlaps)})
    skill_rows = detail['skill_details']
    actual_power = sum(x['actual_power'] for x in skill_rows)
    theory_power = sum(x['theory_power'] for x in skill_rows)
    equal_per_cast = sum(x['actual_count']*x['theory_power']/x['theory_count'] for x in skill_rows if x['theory_count']>0)
    result['equal_per_cast_sensitivity_not_corrected_score'] = {
        'actual_power':actual_power,'theory_power':theory_power,'equal_per_cast_actual_power':equal_per_cast,
        'completion':equal_per_cast/theory_power*100,'output_with_original_full_burst_component':equal_per_cast/theory_power*70+30}
    print(json.dumps({k:v for k,v in result.items() if k!='dance_readiness'},ensure_ascii=False,indent=2),flush=True)
    if '--estimate-axis' in sys.argv:
        runtime._skill_burst_complete_default_rotation = True
        runtime._skill_burst_force_new_scheme_1 = True
        runtime._skill_burst_disable_new_scheme_1_refinement = True
        runtime._skill_burst_disable_conservative_1_6 = True
        runtime._skill_burst_skip_display_effect_windows = True
        calculated = runtime.calculate_timeline([],summary).materialize()
        rows = calculated['auto_segments']
        result['axis_estimate'] = {'counts':dict(Counter(runtime._skill_burst_skill_key(x.get('skill')) for x in rows if not x.get('timeline_hidden') and x.get('kind') not in ('invalid','wait'))),
                                  'segments': [{k:x.get(k) for k in ('start','end','skill','kind','effective_score')} for x in rows]}
        print(json.dumps(result['axis_estimate']['counts'],ensure_ascii=False),flush=True)
    target = ROOT/'work/nanako_dancer_model_audit_20261012.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(target),flush=True)


if __name__ == '__main__':
    main()
