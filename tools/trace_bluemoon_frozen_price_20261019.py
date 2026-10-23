"""Capture one offline frozen-engine spec and trace Smite valuation inputs."""
import os, sys, json, pickle, copy, time, ast, types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION=sys.argv[1]
OUT=ROOT/'outputs/verification/bluemoon_price_cause_20261019'
OUT.mkdir(parents=True,exist_ok=True)
ENGINE=ROOT/('release_v4.78/build-dist/DragonNestEngine/_internal' if VERSION=='4.78' else 'outputs/build_v485_20261019_004033/engine_dist/DragonNestEngine/_internal')
sys.path.insert(0,str(ENGINE));sys._MEIPASS=str(ENGINE);os.chdir(ENGINE)
os.environ['DRAGON_NEST_NATIVE_NO_QT']='1'
def offline(event,args):
    if event=='socket.connect':raise PermissionError('offline diagnosis')
sys.addaudithook(offline)
from dragon_nest_builder.live_dps import v410_score_sync as sync
sync.schedule_usage_ping=lambda *a,**k:None
sync.V410ScoreSyncManager.enqueue=lambda *a,**k:None
sync.V410ScoreSyncManager._start_benchmark_prefetch=lambda *a,**k:None
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.shared import _ExtremeTimelineProxy
store=DataStore.load();core=NativeSimulatorCore(store,BuildCalculator(store))
planner=core.score_theory_planner
data=pickle.loads((ROOT/'outputs/verification/crusader_completion_20261019/1011硬核bluemoon十字.pkl').read_bytes())
snapshot=data['snapshot']
if VERSION=='4.85':
    planner._score_v2_context={
        'actors':{k:v.get('_score_v2',{}).get('profiles',{}) for k,v in data['plans'].items()},
        'player_output_windows':{k:v.get('_score_v2',{}).get('player_output_windows',[]) for k,v in data['plans'].items()},
        'support_boss_scope':data['plans']['0x4D78152C']['_score_v2']['support_boss_scope'],
    }
runtime=planner.timeline_service.extreme_runtime(copy.deepcopy(store.defaults['build1']))
planner.timeline_editor=runtime
cache=OUT/f'spec_scoped_{VERSION}.pkl'
if cache.exists():
    spec=pickle.loads(cache.read_bytes())
else:
    specs=[]
    class Captured(BaseException):pass
    def capture(self,rows,*a,**k):
        specs.extend(copy.deepcopy(rows));raise Captured()
    typ=type(planner.timeline_editor)
    typ._extreme_timeline_tasks_parallel=capture
    ids=frozenset(t for e in BossRecognizer().recognize_snapshot(snapshot) for t in e.target_ids)
    try:planner.build(snapshot,copy.deepcopy(store.defaults['build1']),boss_target_ids=ids,
                      boss_preset_name='黑龙硬核',capture_hint='offline cached snapshot',glyph_overrides={},
                      inferred_glyphs_by_source={k:v.get('_score_glyphs',[]) for k,v in data['plans'].items()},
                      progress=lambda *a:print(*a,flush=True))
    except Captured:pass
    print('captured',len(specs),flush=True)
    spec=next(x for x in specs if x['job']=='十字军')
    cache.write_bytes(pickle.dumps(spec))
print('spec ready',list(spec),flush=True)
if '--capture-only' in sys.argv:os._exit(0)
published=json.loads((ROOT/f'outputs/verification/published_478_485_20261019/black_1011_v{VERSION}.json').read_text(encoding='utf-8'))
published_row=next(r for r in published['scoreRowsDetailed'] if r['name']=='BlueMoon')
metadata={k:copy.deepcopy(v) for k,v in spec['build_override'].items() if k.startswith('_')}
spec['build_override']={**copy.deepcopy(published_row['scoring_equipment']['build']),**metadata}
from dragon_nest_builder.live_dps.theory_plan_builder import _team_score_without_damage_glyphs
spec['build_override']=_team_score_without_damage_glyphs(spec['build_override'])
if VERSION=='4.85':
    actual_plan=data['plans']['0x4D78152C']
    spec['build_override']['_score_v2_profiles']=copy.deepcopy(actual_plan['_score_v2']['profiles'])
    spec['build_override']['_score_v2_time_offset']=actual_plan['_score_time_offset']
    spec['build_override']['_score_rule_version']=actual_plan['_score_v2']['version']
source=planner.timeline_service._source_type()
source_fix='--source-fix' in sys.argv
if source_fix:
    path=ROOT/'dragon_nest_builder/mixins/timeline_runtime.py'
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    methods=[n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body
             if isinstance(n,ast.FunctionDef) and n.name in (
                 '_skill_burst_replay_manual_sequence', '_skill_burst_replay_manual_sequence_with_actions')]
    ns=dict(source._skill_burst_replay_manual_sequence.__globals__)
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*methods],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),ns)
    for method in methods:setattr(source,method.name,ns[method.name])
snapshot_fix='--snapshot-fix' in sys.argv
if snapshot_fix:
    old_replay=source._skill_burst_replay_manual_sequence
    path=ROOT/'dragon_nest_builder/mixins/timeline_runtime.py'
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    node=next(n for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body
              if isinstance(n,ast.FunctionDef) and n.name=='_skill_burst_replay_manual_sequence')
    def fingerprint(c):
        return (c.co_code,c.co_names,c.co_varnames,c.co_freevars,c.co_cellvars,
                tuple(fingerprint(x) if isinstance(x,types.CodeType) else x for x in c.co_consts))
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
    ns=dict(old_replay.__globals__)
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),ns)
    assert fingerprint(ns[node.name].__code__)==fingerprint(old_replay.__code__), 'Current method differs from frozen method'
    text=ast.unparse(node)
    needle='scoring_attrs = self._skill_burst_attrs_during_action(active_attrs, action)'
    assert text.count(needle)==1
    text=text.replace(needle,needle+'\n        if action is not None:\n            self._skill_burst_snapshot_periodic_action(action, scoring_attrs)')
    exec('from __future__ import annotations\n'+text,ns)
    fixed=ns[node.name]
    def isolated_replay(self,sequence,candidates,support_actions,window,**kwargs):
        actions=copy.deepcopy(support_actions)
        for a in actions:
            if self._skill_burst_snapshot_rule(a) and 'base_active_multiplier' in a:
                a['active_multiplier']=a['base_active_multiplier']
                a['last_snapshot_multiplier']=a['base_active_multiplier']
        saved=self._skill_burst_periodic_buffs
        action_map={self._skill_burst_skill_key(a['skill']):a for a in actions}
        self._skill_burst_periodic_buffs=[action_map.get(self._skill_burst_skill_key(a['skill']),copy.deepcopy(a)) for a in saved]
        try:return fixed(self,sequence,candidates,actions,window,**kwargs)
        finally:self._skill_burst_periodic_buffs=saved
    source._skill_burst_replay_manual_sequence=isolated_replay
original=source._skill_burst_full_metric_score
trace=[]
def traced(self,metric,active_attrs=None,seconds=None):
    value=original(self,metric,active_attrs,seconds)
    if self._skill_burst_skill_key(metric.get('skill'))=='重击':
        trace.append({'t':seconds,'value':value,'attrs':copy.deepcopy(active_attrs),
                      'periodic':[{k:a.get(k) for k in ('skill','active_multiplier','base_active_multiplier','inactive_multiplier','buff_marker')}
                                  for a in getattr(self,'_skill_burst_periodic_buffs',[])],
                      'caller':sys._getframe(1).f_code.co_name})
    return value
source._skill_burst_full_metric_score=traced
started=time.perf_counter()
result=planner.timeline_editor._extreme_timeline_result_from_spec(spec)
(OUT/f'trace_{VERSION}{"_source_fix" if source_fix else "_snapshot_fix" if snapshot_fix else ""}.json').write_text(json.dumps({'version':VERSION,'snapshot_fix':snapshot_fix,'source_fix':source_fix,'seconds':time.perf_counter()-started,'result':result,'trace':trace},ensure_ascii=False,default=str),encoding='utf-8')
print('done',time.perf_counter()-started,len(trace),flush=True)
os._exit(0)
