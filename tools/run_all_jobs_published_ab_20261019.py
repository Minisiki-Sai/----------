"""Isolated published-engine A/B runner for profession-coverage extension."""
from __future__ import annotations
import argparse,concurrent.futures,json,os,subprocess,sys,time
from pathlib import Path
from run_published_comparison_batch_20261019 import ROOT,ENGINES,CAP,sha
OUT=ROOT/'outputs/verification/all_jobs_published_ab_20261019'

def one(task):
    case,name,version=task;ident=f'{case}_v{version}'
    out=OUT/f'{ident}.json';capture=CAP/name
    if out.exists():
        done=json.loads(out.read_text(encoding='utf-8'))
        if done.get('status')=='ok' and done.get('captureSha256')==sha(capture) and done.get('score',{}).get('theoryPlanner')=='v401-full-team-score':
            print('REUSE '+ident,flush=True);return True
    env=os.environ.copy();env.update(AB_ENGINE_ROOT=str(ENGINES[version]),AB_STATE_ROOT=str(OUT/'state'/ident),DRAGON_NEST_TIMELINE_WORKERS='4')
    print('START '+ident+' '+name,flush=True);started=time.monotonic()
    with (OUT/f'{ident}.log').open('w',encoding='utf-8') as log:
        proc=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'tools/compare_published_scores_20261019.py'),str(capture),'--output',str(out),'--version',version],env=env,stdout=log,stderr=subprocess.STDOUT)
    print('END '+ident+f' exit={proc.returncode} seconds={time.monotonic()-started:.1f}',flush=True)
    return proc.returncode==0

def main():
    p=argparse.ArgumentParser();p.add_argument('--cases',type=Path,required=True);p.add_argument('--workers',type=int,default=2);p.add_argument('--after-cases',type=Path);args=p.parse_args()
    OUT.mkdir(exist_ok=True);cases=json.loads(args.cases.read_text(encoding='utf-8'))
    if args.after_cases:
        predecessors=json.loads(args.after_cases.read_text(encoding='utf-8'))
        print('Waiting for preceding A/B cases; no extra scorers started.',flush=True)
        while not all((OUT/f"{x['id']}_v{v}.json").exists() and not (OUT/f"{x['id']}_v{v}.running.lock").exists() for x in predecessors for v in ('4.78','4.85')):
            time.sleep(5)
    tasks=[(x['id'],x['file'],v) for x in cases for v in ('4.78','4.85')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:result=list(pool.map(one,tasks))
    return 0 if all(result) else 1
if __name__=='__main__':raise SystemExit(main())
