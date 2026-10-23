"""Alternating frozen-before/corrected/optimized comparisons of one raw replay."""
from audit_hit_matching_20261020 import *
import argparse, importlib.util, hashlib, os
from dataclasses import asdict

DEST=ROOT/'outputs/verification/hit_matching_fix_20261020'
def load_module(name,file):
    spec=importlib.util.spec_from_file_location(name,file)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

def run(mode,repeat):
    from act_hit_timeline_comparator import core,stage_estimator as stage
    from skill_hit_tool import confirmed_components as components
    old_core=load_module('act_hit_timeline_comparator._audit_old_core',DEST/'baseline/core.py')
    old_stage=load_module('act_hit_timeline_comparator._audit_old_stage',DEST/'baseline/stage_estimator.py')
    old_components=load_module('skill_hit_tool._audit_old_components',DEST/'baseline/confirmed_components.py')
    if mode=='before':
        stage.fit=old_stage.fit
        stage.infer_comparison=old_stage.infer_comparison
        components.infer_confirmed=old_components.infer_confirmed
        components.annotate_components=old_components.annotate_components
        compare=old_core.best_comparison
    else:
        compare=core.best_comparison
    os.environ['DN_HIT_EXACT_FIT_REUSE']='1'if mode=='fast'else'0'
    repo=repository()
    obs,_,_,speeds=pickle.loads((OUT/'observations.pkl').read_bytes())
    events=pickle.loads((OUT/'decoded.pkl').read_bytes())
    start=time.perf_counter()
    obs=components.annotate_components(obs,events)
    annotation_seconds=time.perf_counter()-start
    rows=[]
    start=time.perf_counter()
    for o in obs:
        if record_kind(o)!='施放' or not o.hit_times:continue
        speed=speeds.get((o.source_id,o.cast_timestamp_ms),0.)
        vv=variants_for_observation(repo,o,speed,condition_choice='自动/无条件选择',include_optional=False)
        vv=select_cast_routes(vv,o,obs,speeds)
        best,_=compare(vv,o.hit_times,o.hit_amounts,o.critical_flags,
                       external_speed_percent=speed,observation=o)
        if best is None:continue
        d=core.comparison_to_dict(best,o)
        times=[]
        for r in best.rows:
            h=r.expected
            if h and not h.optional and h.start is not None and r.actual_time is not None:
                times.append(max(h.start-r.actual_time,r.actual_time-(h.end if h.end is not None else h.start),0))
        d['window_distance_mean']=sum(times)/max(1,len(times))
        d['firestorm_proc_extras']=o.analysis_metadata.get('firestorm_proc_extras',{})
        rows.append(d)
    elapsed=time.perf_counter()-start
    path=DEST/f'{mode}_{repeat}.json'
    path.write_text(json.dumps(dict(mode=mode,repeat=repeat,annotation_seconds=annotation_seconds,
        matching_seconds=elapsed,rows=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    digest=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    print(json.dumps(dict(mode=mode,repeat=repeat,casts=len(rows),annotation_seconds=annotation_seconds,
        matching_seconds=elapsed,results_sha256=digest),ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['before','corrected','fast'])
    parser.add_argument('--repeat',default=1,type=int);parser.add_argument('--profile',action='store_true')
    args=parser.parse_args()
    if args.profile:
        import cProfile
        profiler=cProfile.Profile();profiler.enable()
        run(args.mode,args.repeat)
        profiler.disable();profiler.dump_stats(str(DEST/f'{args.mode}.prof'))
    else:run(args.mode,args.repeat)
