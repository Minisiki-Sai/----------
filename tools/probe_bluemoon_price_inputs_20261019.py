"""Offline diagnostic. Frozen release imports, no score upload or production edits."""
import os
import sys
import json
import pickle
import copy
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = sys.argv[1]
OUT = ROOT / 'outputs/verification/bluemoon_price_cause_20261019'
OUT.mkdir(parents=True, exist_ok=True)
ENGINE = ROOT / ('release_v4.78/build-dist/DragonNestEngine/_internal' if VERSION == '4.78'
                 else 'outputs/build_v485_20261019_004033/engine_dist/DragonNestEngine/_internal')
sys.path.insert(0, str(ENGINE))
sys._MEIPASS = str(ENGINE)
os.environ['DRAGON_NEST_NATIVE_NO_QT'] = '1'
os.chdir(ENGINE)
def offline(event, args):
    if event == 'socket.connect':
        raise PermissionError('Offline diagnostic forbids network')
sys.addaudithook(offline)

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
from dragon_nest_builder.shared import _ExtremeTimelineProxy

full = json.loads((ROOT / f'outputs/verification/published_478_485_20261019/black_1011_v{VERSION}.json').read_text(encoding='utf-8'))
row = next(x for x in full['scoreRowsDetailed'] if x['name'] == 'BlueMoon')
plan = full['plans'][row['source_id']]
store = DataStore.load()
calc = BuildCalculator(store)
build = copy.deepcopy(row['scoring_equipment']['build'])
runtime = LegacyTimelineService(store, calc).extreme_runtime(build)
runtime._remove_timeline_owned_build_buffs(build, '十字军')
proxy = _ExtremeTimelineProxy(runtime, build, [])
_, summary = calc.calculate(build)
summary = proxy._team_apply_current_mixed_element_panel(dict(summary), {}, build)
proxy._skill_burst_base_summary = summary
proxy._skill_burst_periodic_buffs = []
proxy._skill_burst_external_team_effect_windows = copy.deepcopy(plan['external_windows'])
metrics = proxy._skill_burst_all_learned_metrics(summary, [])
by_skill = {proxy._skill_burst_skill_key(m['skill']):m for m in metrics}
actions = proxy._skill_burst_support_actions(by_skill)
windows = proxy._skill_burst_timeline_attr_windows(plan['segments'], actions, 1000,
                                                 include_periodic_display_attrs=False)
result = {'version':VERSION, 'engine':str(ENGINE), 'summary':summary,
          'metric':by_skill.get('重击'), 'actions':actions, 'rows':[]}
support = {proxy._skill_burst_skill_key(a['skill']):a for a in actions}
for seg in sorted(plan['segments'], key=lambda x:(x['start'],x['end'])):
    t=seg['start']
    attrs=proxy._skill_burst_active_attrs_from_timeline_windows(windows, t)
    action = support.get(proxy._skill_burst_skill_key(seg['skill']))
    attrs = proxy._skill_burst_attrs_during_action(attrs, action)
    if action is not None:
        proxy._skill_burst_snapshot_periodic_action(action, attrs)
    if seg['skill'] != '重击':
        continue
    power=proxy._skill_burst_full_metric_score(by_skill['重击'], attrs, t)
    result['rows'].append({'start':t,'saved_score':seg['effective_score'],
                           'recomputed_score':power,'attrs':attrs})
(OUT / f'probe_{VERSION}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps({'version':VERSION,'summary_matches':summary==plan['summary'],
                  'rows':[(r['start'],r['recomputed_score']/r['saved_score']) for r in result['rows'][:5]]},ensure_ascii=False))
