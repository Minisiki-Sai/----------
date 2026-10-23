"""Read-only current-decoder, one-cast/one-Boss Hit evidence report.

No score/upload or production-template writes. Runtime amounts remain factual;
homogeneous-crit samples avoid an invented global critical multiplier.
"""
from __future__ import annotations
import argparse
from bisect import bisect_right, bisect_left
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics as st
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.data_loader import DataStore, DATA_DIR
from dragon_nest_builder.hit_analyzer.analyzer import SkillHitAnalyzer, SkillHitTemplateLibrary, skill_family
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent, BossStatusEvent, BossSpawnEvent
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer

def dump(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.name.endswith('.gz'):
        with gzip.open(path,'wt',encoding='utf8') as f: json.dump(data,f,ensure_ascii=False,separators=(',',':'))
    else: path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def code_signature():
    files=[*sorted((ROOT/'dragon_nest_builder/live_dps').rglob('*.py')),
           *sorted((ROOT/'dragon_nest_builder/hit_analyzer').glob('*.py')),
           *sorted((ROOT/'dragon_nest_builder/data/live_dps_profiles').glob('*.json')),
           ROOT/'dragon_nest_builder/data_loader.py',Path(__file__)]
    h=hashlib.sha256()
    for p in files: h.update(str(p.relative_to(ROOT)).encode()); h.update(p.read_bytes())
    return h.hexdigest()

def analyze_one(path_text, output_text, signature):
    path=Path(path_text); out=Path(output_text)
    if code_signature()!=signature: raise RuntimeError('analysis code changed before replay')
    t=time.monotonic(); store=DataStore.load(); analyzer=SkillHitAnalyzer(store)
    source=V576DiagnosticReplaySource(path,DATA_DIR,{})
    casts=[]; hits=[]; changes=defaultdict(set); spawn={}; total_events=0
    try:
        for batch in source.iter_batches():
            total_events+=len(batch)
            for e in batch:
                if isinstance(e,SkillCastEvent): casts.append(e)
                elif isinstance(e,CombatEvent) and not e.healing and e.amount>0 and not e.source_id.startswith('__'):
                    hits.append(e)
                elif isinstance(e,BossSpawnEvent): spawn[e.target_id]=e
                elif isinstance(e,BossStatusEvent):
                    changes[e.target_id].add(e.timestamp_ms)
                    if e.duration_ms>0: changes[e.target_id].add(e.timestamp_ms+e.duration_ms)
        end=source.capture_end_timestamp_ms
    finally: source.close()
    # Only use explicit/template Bosses or fixed named HP profiles in the main
    # result; inferred anonymous targets remain excluded, not silent Bosses.
    enriched=[replace(e,target_name=spawn[e.target_id].target_name or e.target_name,
                      target_job=spawn[e.target_id].target_job or e.target_job,
                      target_max_hp=spawn[e.target_id].target_max_hp or e.target_max_hp)
              if e.target_id in spawn else e for e in hits]
    bosses=BossRecognizer().recognize(target_rows=(),combat_events=enriched,source_ids={e.source_id for e in casts})
    boss_ids={target:b.name for b in bosses if b.confidence in {'confirmed','fixed_profile'} for target in b.target_ids}
    selected=[e for e in enriched if e.target_id in boss_ids]
    observations=analyzer.analyze_events(casts,selected,path.name,capture_end_ms=end)
    changes={k:sorted(v) for k,v in changes.items()}
    mapped=[]
    for row in observations:
        if not row.hit_count: continue
        record=row.to_mapping()
        target=row.hit_target_ids[0] if row.hit_target_ids else ''
        a=row.cast_timestamp_ms+round(row.hit_times[0]*1000)
        b=row.cast_timestamp_ms+round(row.hit_times[-1]*1000)
        boundaries=sorted(set(changes.get(row.source_id,()))|set(changes.get(target,())))
        record['boss']=boss_ids.get(target,'')
        record['status_change_inside_hits']=bisect_right(boundaries,b)-bisect_right(boundaries,a)>0
        mapped.append(record)
    result={'capture':str(path),'signature':signature,'seconds':time.monotonic()-t,
            'decoded_events':total_events,'damage_records':len(hits),
            'damage_amount':sum(e.amount for e in hits),'boss_damage_records':len(selected),
            'bosses':[{'name':b.name,'confidence':b.confidence,'targets':sorted(b.target_ids)} for b in bosses],
            'jobs':dict(Counter(e.job for e in hits)),
            'analysis':analyzer.last_diagnostics,'observations':mapped}
    if code_signature()!=signature: raise RuntimeError('analysis code changed during replay')
    dump(out,result)
    return {k:v for k,v in result.items() if k not in {'observations','analysis'}}|{'observations':len(mapped),'shard':str(out)}

def make_plan(capture_root):
    paths=list(capture_root.rglob('*.dpscap'))
    black=[p for p in paths if re.search(r'黑.*(?:典|硬)|硬.*黑',p.stem) and 'before-' not in p.name]
    audit=ROOT/'outputs/damage_attribution_final_20260907_v24_strict/profession_direct_damage_report.json'
    manifest=json.loads(audit.read_text(encoding='utf8'))['capture_manifest'] if audit.exists() else []
    byname={Path(r['capture_path']).name:r for r in manifest}
    store=DataStore.load(); jobs=[j for f in store.first_jobs() for j in store.second_jobs(f)]
    covered=set(j for p in black for j in byname.get(p.name,{}).get('profession_summary',{}))
    extra=[]
    while set(jobs)-covered:
        remaining=[p for p in paths if p not in black and p not in extra and p.name in byname]
        if not remaining: break
        best=max(remaining,key=lambda p:(len(set(byname[p.name].get('profession_summary',{}))-covered),-p.stat().st_size))
        new=set(byname[best.name].get('profession_summary',{}))-covered
        if not new: break
        extra.append(best); covered|=new
    selected=[]; duplicates=[]; seen={}
    for p in sorted(black+extra,key=lambda p:p.name):
        content,evidence=SkillHitTemplateLibrary.file_fingerprints(p)
        if content in seen:
            duplicates.append({'file':str(p),'duplicate_of':seen[content]}); continue
        seen[content]=str(p)
        selected.append({'path':str(p),'content':content,'evidence':evidence,'black_dragon_name_selection':p in black})
    return {'signature':code_signature(),'jobs':jobs,'captures':selected,'duplicates':duplicates,
            'supplementary_captures':[p.name for p in extra], 'historical_metadata_uncovered_jobs':sorted(set(jobs)-covered)}

def median_balanced(rows,getter):
    grouped=defaultdict(lambda:defaultdict(list))
    for r in rows: grouped[r['source_file']][r['source_id']].append(getter(r))
    return st.median(st.median(st.median(v) for v in actors.values()) for actors in grouped.values())

def summarize(out,plan):
    allrows=[]; files=[]; failures=[]
    for item in plan['captures']:
        p=out/'shards'/f"{item['content'][:24]}.json.gz"
        if not p.exists(): failures.append(item['path']); continue
        with gzip.open(p,'rt',encoding='utf8') as f:r=json.load(f)
        if r['signature']!=plan['signature']: raise RuntimeError('stale shard')
        files.append({k:v for k,v in r.items() if k not in {'observations','analysis'}})
        allrows.extend(r['observations'])
    groups=defaultdict(list)
    for r in allrows:
        if r['job'] in plan['jobs']: groups[(r['job'],r['skill_name'],r['skill_id'])].append(r)
    results=[]
    for (job,skill,sid),rows in sorted(groups.items()):
        persistent=any(r['persistent'] for r in rows)
        eligible=[r for r in rows if r['training_eligible'] and not r['persistent']]
        hist=Counter(len(r['hit_times']) for r in eligible)
        stable=[]
        for n,count in hist.items():
            full=[r for r in eligible if len(r['hit_times'])==n]
            if count>=3 and len({r['source_file'] for r in full})>=2 and count/len(eligible)>=0.05: stable.append(n)
        n=max(stable) if stable else (max(hist,key=lambda x:(hist[x],x)) if hist else None)
        full=[r for r in eligible if len(r['hit_times'])==n]
        clean=[r for r in full if len(set(r['critical_flags']))==1 and not r['status_change_inside_hits']]
        shares=[]; variability=None
        if clean:
            shares=[median_balanced(clean,lambda r,i=i:r['hit_amounts'][i]/sum(r['hit_amounts'])) for i in range(n)]
            total=sum(shares); shares=[x/total for x in shares]
            variability=st.median(sum(abs(a/sum(r['hit_amounts'])-s) for a,s in zip(r['hit_amounts'],shares))/2 for r in clean)
        distribution_stable=bool(shares and len(clean)>=3 and len({r['source_file'] for r in clean})>=2 and variability<=0.08 and stable)
        reasons=[]
        if persistent: reasons.append('含召唤/持续攻击；存续和攻击次数可变，不给固定总Hit')
        elif not eligible: reasons.append('缺少完整动作与可靠单目标技能归属样本')
        elif not stable: reasons.append('完整Hit上界尚未跨两份抓包重复验证')
        else: reasons.append('取跨抓包至少3次重复支持的最大Hit数；少Hit单列为未打满候选')
        if n and not clean: reasons.append('没有同一暴击状态且未见中途状态变化的完整样本，不能给逐Hit面板比例')
        elif clean and not distribution_stable: reasons.append('逐Hit实测样本不足或分布不稳定，不能确认为固定面板比例')
        elif distribution_stable: reasons.append('比例为同暴击状态、未见状态切换的实测近似，仍非拆包面板系数证明')
        examples=[]
        for r in (clean if clean else full)[:3]:
            examples.append({k:r[k] for k in ['source_file','source_name','source_id','cast_timestamp_ms','boss','hit_times','hit_amounts','critical_flags']})
        results.append({'job':job,'skill':skill,'skill_id':sid,'hit_count':None if persistent else n,
                        'hit_status':'重复支持上界' if stable and not persistent else '未确认',
                        'damage_shares':shares if distribution_stable else [],
                        'observed_candidate_shares':shares,'share_status':'稳定实测近似' if distribution_stable else '未确认',
                        'share_variability':variability,'samples':len(rows),'eligible':len(eligible),
                        'full_samples':len(full),'full_files':len({r['source_file'] for r in full}),
                        'clean_samples':len(clean),'clean_files':len({r['source_file'] for r in clean}),
                        'histogram':dict(sorted(hist.items())),'observed_max':max(len(r['hit_times']) for r in rows),
                        'persistent':persistent,'reason':'；'.join(reasons),'examples':examples})
    store=DataStore.load()
    present={(r['job'],r['skill']) for r in results}
    missing=[{'job':job,'skill':skill,'reason':'本批Boss样本没有该技能的同名变体命中记录'}
             for job in plan['jobs'] for skill in store.skill_damage_names(job,mode='both')
             if skill not in {'','无','-'} and (job,skill) not in present]
    payload={'plan':plan,'files':files,'failures':failures,'results':results,'missing':missing,
             'overview':{'captures':len(files),'jobs':len({r['job'] for r in results}),'observations':len(allrows),
                         'skill_variants':len(results),'supported_hit_skills':sum(r['hit_status']=='重复支持上界' for r in results),
                         'stable_share_skills':sum(bool(r['damage_shares']) for r in results)}}
    dump(out/'result.json',payload)
    print(json.dumps(payload['overview'],ensure_ascii=False),flush=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--workers',type=int,default=2); ap.add_argument('--limit',type=int,default=0); ap.add_argument('--summary-only',action='store_true')
    args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    if args.summary_only:
        plan=json.loads((args.out/'plan.json').read_text(encoding='utf8')); summarize(args.out,plan); return
    plan=make_plan(args.root)
    if args.limit: plan['captures']=plan['captures'][:args.limit]
    dump(args.out/'plan.json',plan)
    print(json.dumps({'captures':len(plan['captures']),'supplement':plan['supplementary_captures'],'missing':plan['historical_metadata_uncovered_jobs']},ensure_ascii=False),flush=True)
    pending=[]
    for item in plan['captures']:
        shard=args.out/'shards'/f"{item['content'][:24]}.json.gz"
        if shard.exists():
            with gzip.open(shard,'rt',encoding='utf8') as f:old=json.load(f)
            if old['signature']==plan['signature']: print('cached '+Path(item['path']).name,flush=True); continue
        pending.append((item,shard))
    with ProcessPoolExecutor(max_workers=max(1,min(4,args.workers)),max_tasks_per_child=1) as pool:
        tasks={pool.submit(analyze_one,item['path'],str(shard),plan['signature']):item for item,shard in pending}
        for future in as_completed(tasks):
            item=tasks[future]
            try:
                r=future.result(); print(json.dumps({'done':Path(item['path']).name,'seconds':round(r['seconds'],1),'observations':r['observations'],'bosses':len(r['bosses'])},ensure_ascii=False),flush=True)
            except Exception as exc: print(json.dumps({'error':item['path'],'detail':repr(exc)},ensure_ascii=False),flush=True)
    summarize(args.out,plan)

if __name__=='__main__': main()
