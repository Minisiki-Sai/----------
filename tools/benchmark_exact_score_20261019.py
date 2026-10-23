"""Offline, fresh-current-code full score benchmark. No upload or cache reuse."""
import os, sys, json, time, dataclasses, argparse, traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DRAGON_NEST_NATIVE_NO_QT']='1'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--capture',required=True)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--fast',choices=['0','1'],default='0')
    p.add_argument('--round2',choices=['0','1'],default='0')
    p.add_argument('--round3',choices=['0','1'],default='0')
    a=p.parse_args(); a.output.parent.mkdir(parents=True,exist_ok=True)
    os.environ['DN_HIT_EXACT_ASSIGNMENT_FAST']=a.fast
    os.environ['DN_SCORE_EXACT_DECODE_REUSE']=a.fast
    os.environ['DN_SCORE_EXACT_CANDIDATE_REUSE']=a.fast
    os.environ['DN_SCORE_EXACT_STATE_REUSE']=a.fast
    os.environ['DN_SCORE_EXACT_ROUND2']=a.round2
    os.environ['DN_SCORE_EXACT_ROUND3']=a.round3
    os.environ['DN_SCORE_V2_HOME']=str(a.output.parent/(a.output.stem+'_evidence'))
    # Equal worker budget before/after; do not get a speedup just from more CPUs.
    os.environ['DRAGON_NEST_TIMELINE_WORKERS']='4'
    os.environ['DN_HIT_WORKERS']='4'
    def offline(event,args):
        if event=='socket.connect': raise PermissionError('Offline benchmark; upload disabled')
    sys.addaudithook(offline)
    from dragon_nest_builder.live_dps import v410_score_sync as sync
    sync.winreg=None
    config=sync.load_v410_score_sync_config
    sync.load_v410_score_sync_config=lambda:dataclasses.replace(config(),enabled=False)
    sync.schedule_usage_ping=lambda *a,**k:None
    sync.V410ScoreSyncManager.enqueue=lambda *a,**k:None
    sync.V410ScoreSyncManager._start_benchmark_prefetch=lambda *a,**k:None
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.v410_native_core import NativeSimulatorCore
    started=time.perf_counter(); report={'capture':a.capture,'fast':a.fast,'round2':a.round2,'round3':a.round3,'workers':4,'progress':[],'network_disabled':True,
                                      'python_hash_seed':os.environ.get('PYTHONHASHSEED', 'random')}
    def progress(value,message):
        row={'seconds':round(time.perf_counter()-started,3),'value':value,'message':message}
        report['progress'].append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    try:
        store=DataStore.load(); core=NativeSimulatorCore(store,BuildCalculator(store))
        for name in ('_replay_disk_cache','_team_score_disk_cache'):
            cache=getattr(core,name,None)
            if cache:
                cache.load=lambda *a,**k:None
                cache.save=lambda *a,**k:None
                cache.save_async=lambda *a,**k:None
        report['init_seconds']=time.perf_counter()-started
        t=time.perf_counter(); replay=core.load_replay(a.capture,progress)
        report['decode_seconds']=time.perf_counter()-t
        t=time.perf_counter(); score=core.score_replay({'sourceHash':replay['sourceHash'],'sourceFile':replay['sourceFile']},progress)
        report['score_seconds']=time.perf_counter()-t
        report['rows']=[dataclasses.asdict(r)for r in core._score_results.get(score.get('scoreKey',''),())]
        report['theory_diagnostics']=getattr(core.score_theory_planner,'_team_score_theory_diagnostics',{})
        report['plans']={str(k):{key:v.get(key)for key in ('segments','_score_skill_damages','_score_actual_cast_segments')}
                         for k,v in core.score_theory_planner._team_score_theory_plans.items()}
        report['status']='ok'
    except BaseException:
        report['status']='error';report['error']=traceback.format_exc();print(report['error'],flush=True)
    report['elapsed_seconds']=time.perf_counter()-started
    a.output.write_text(json.dumps(report,ensure_ascii=False,default=str),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items()if k not in ('progress','rows','plans','theory_diagnostics')},ensure_ascii=False),flush=True)
    return 0 if report['status']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())
