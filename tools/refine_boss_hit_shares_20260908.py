"""Estimate within-cast hit weights from repeated combat observations.

Separately fit each recording/player. A per-cast log intercept removes the
overall damage scale; a jointly estimated critical term avoids assuming x2.
These are empirical estimates, not proof of client panel coefficients.
"""
from collections import defaultdict, Counter
import argparse
import gzip
import json
from pathlib import Path
import statistics as st
import numpy as np

def fit_profile(rows,n):
    if len(rows)<3: return None
    if n==1: return {'shares':[1.0],'critical_multiplier':None,'residual':0.0}
    a=np.array([r['hit_amounts'] for r in rows],dtype=float)
    c=np.array([r['critical_flags'] for r in rows],dtype=float)
    if np.any(a<=0): return None
    y=np.log(a); y=(y-y.mean(axis=1,keepdims=True)).ravel()
    h=np.eye(n)[:,:n-1]; h=h-h.mean(axis=0,keepdims=True)
    x=np.tile(h,(len(rows),1))
    crit=(c-c.mean(axis=1,keepdims=True)).ravel()
    has_mixed=np.max(np.abs(crit))>1e-8
    if has_mixed: x=np.column_stack((x,crit))
    if np.linalg.matrix_rank(x)<x.shape[1]: return None
    weights=np.ones(len(y))
    for _ in range(7):
        root=np.sqrt(weights)
        beta=np.linalg.lstsq(x*root[:,None],y*root,rcond=None)[0]
        residual=y-x@beta
        scale=max(0.015,1.4826*np.median(np.abs(residual-np.median(residual))))
        weights=np.minimum(1.0,1.5*scale/np.maximum(np.abs(residual),1e-12))
    critical=float(np.exp(beta[-1])) if has_mixed else None
    if critical is not None and not 1.1<critical<5: return None
    error=float(np.median(np.abs(residual)))
    if error>0.20: return None
    coef=np.exp(np.r_[beta[:n-1],0]); coef/=coef.sum()
    return {'shares':coef.tolist(),'critical_multiplier':critical,'residual':error}

def self_test():
    rng=np.random.default_rng(233)
    rows=[]
    for i in range(80):
        flags=rng.random(3)<.7
        amounts=np.array([.30,.30,.40])*float(rng.uniform(10000,500000))*np.where(flags,2.5,1)
        rows.append({'critical_flags':flags.tolist(),'hit_amounts':amounts.tolist()})
    out=fit_profile(rows,3)
    assert max(abs(x-y) for x,y in zip(out['shares'],[.3,.3,.4]))<1e-8
    assert abs(out['critical_multiplier']-2.5)<1e-8
    assert fit_profile([{'critical_flags':[True,False,True],'hit_amounts':[30,15,40]}]*10,3) is None
    out=fit_profile([{'critical_flags':[True]*3,'hit_amounts':[30,30,40]}]*10,3)
    assert max(abs(x-y) for x,y in zip(out['shares'],[.3,.3,.4]))<1e-8

def main():
    ap=argparse.ArgumentParser();ap.add_argument('directory',type=Path);args=ap.parse_args()
    self_test()
    report=json.loads((args.directory/'result.json').read_text(encoding='utf8'))
    allrows=defaultdict(list)
    for item in report['plan']['captures']:
        p=args.directory/'shards'/f"{item['content'][:24]}.json.gz"
        if not p.exists(): continue
        with gzip.open(p,'rt',encoding='utf8') as f:r=json.load(f)
        for row in r['observations']:
            if row['training_eligible'] and not row['persistent']:
                allrows[(row['job'],row['skill_name'],row['skill_id'])].append(row)
    for result in report['results']:
        eligible=allrows[(result['job'],result['skill'],result['skill_id'])]
        hist=Counter(len(r['hit_times']) for r in eligible)
        # Rare full hits must not be rejected merely because their frequency
        # falls below 5%. Require independent repetition, not popularity.
        repeated=[n for n,count in hist.items() if count>=3 and
                  len({r['source_file'] for r in eligible if len(r['hit_times'])==n})>=2]
        n=max(repeated) if repeated and not result['persistent'] else None
        result['previous_frequency_gated_count']=result['hit_count']
        result['hit_count']=n
        result['hit_status']='重复支持上界' if n else '未确认'
        result['histogram']=dict(sorted(hist.items()))
        result['eligible']=len(eligible)
        full=[r for r in eligible if len(r['hit_times'])==n]
        result['full_samples']=len(full)
        result['full_files']=len({r['source_file'] for r in full})
        if n!=result['previous_frequency_gated_count']:
            # The former clean-sample check belongs to a different Hit count.
            result['damage_shares']=[]
            result['clean_samples']=0
            result['clean_files']=0
        result['homogeneous_crit_shares']=result['damage_shares']
        result['damage_shares']=[]
        result['fit_details']=[]
        if not n or result['hit_status']!='重复支持上界':continue
        groups=defaultdict(list)
        for row in full:groups[(row['source_file'],row['source_id'])].append(row)
        fits=[]
        for (capture,source),rows in groups.items():
            fit=fit_profile(rows,n)
            if fit: fits.append(fit|{'capture':capture,'source_id':source,'samples':len(rows)})
        result['fit_details']=fits
        files=defaultdict(list)
        for f in fits:files[f['capture']].append(f['shares'])
        if len(files)<2:
            result['share_status']='未确认'
            result['reason']='Hit数有跨包重复支持；逐Hit比例缺少可分离暴击和伤害倍率的跨包样本'
            continue
        byfile=[np.median(v,axis=0) for v in files.values()]
        shares=np.median(byfile,axis=0);shares/=shares.sum()
        deviation=st.median(float(np.sum(np.abs(v/v.sum()-shares))/2) for v in byfile)
        homogeneous=result['homogeneous_crit_shares']
        crosscheck=(float(np.sum(np.abs(np.array(homogeneous)-shares))/2) if homogeneous else None)
        result['fit_cross_file_deviation']=deviation
        result['fit_homogeneous_crosscheck']=crosscheck
        if deviation>.08 or (crosscheck is not None and crosscheck>.08):
            result['share_status']='分布不稳定'
            result['reason']='Hit数有跨包重复支持，但分布随抓包/施放状态明显改变，不能给唯一比例'
            continue
        result['damage_shares']=shares.tolist()
        result['share_status']='跨包校正估计'
        result['reason']='跨抓包校正暴击与整次伤害倍率；逐Hit比例是实战估计，不是面板系数证明'
        # Select an unmodified actual full-hit example close to the fitted
        # pattern after its own fit's critical correction. Never change damage.
        fitmap={(f['capture'],f['source_id']):f for f in fits}
        candidates=[]
        for row in full:
            fit=fitmap.get((row['source_file'],row['source_id']))
            if not fit:continue
            a=np.array(row['hit_amounts'],dtype=float)
            multiplier=fit['critical_multiplier']
            if multiplier:a=a/np.where(row['critical_flags'],multiplier,1)
            q=a/a.sum()
            candidates.append((float(np.sum(np.abs(q-shares))),row))
        result['examples']=[{k:r[k] for k in ['source_file','source_name','source_id','cast_timestamp_ms','boss','hit_times','hit_amounts','critical_flags']}
                            for _,r in sorted(candidates,key=lambda x:x[0])[:2]]
    for result in report['results']:
        n=result.get('hit_count')
        if n and result['hit_status']=='重复支持上界':
            larger={int(k):v for k,v in result['histogram'].items() if int(k)>n}
            if larger:
                result['reason']+=f'；另有更大Hit样本{sum(larger.values())}次（最大{max(larger)}），尚未稳定重复，需复核'
    report['overview']['estimated_share_skills']=sum(bool(r['damage_shares']) for r in report['results'])
    report['overview']['supported_hit_skills']=sum(r['hit_status']=='重复支持上界' for r in report['results'])
    report['estimation_method']='Within-cast log damage model; fit per capture/actor; robust residuals; cross-capture median; empirical not exact panel coefficients'
    (args.directory/'refined_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report['overview'],ensure_ascii=False))
    for r in report['results']:
        if r['skill'] in {'冰刃','冰刃EX','黑暗神殿','召唤黑洞','上升重力','聚能射击','箭雨风暴','月光碎片'}:
            print(json.dumps({k:r[k] for k in ['job','skill','hit_count','full_samples','full_files','damage_shares','share_status']},ensure_ascii=False))

if __name__=='__main__': main()
