"""Compare complete saved outputs, retaining every cast and timing run."""
from pathlib import Path
import json, hashlib, statistics
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/verification/hit_matching_fix_20261020'

def key(r):
    return r['source_name'],r['job'],r['skill_id'],r['cast_timestamp_ms']

def ledger(r):
    return sorted((x['actual_hit'],x['actual_time_seconds'],x['damage'],x['critical'])
                  for x in r['rows'] if x['actual_hit'] is not None)

def summary(r):
    return dict(time=r['cast_timestamp_ms'],job=r['job'],skill=r['skill_name'],
        summary=r['summary'],realization=r['realization'],
        procs=len(r['firestorm_proc_extras']),window_distance_ms=r['window_distance_mean']*1000)

def main():
    runs={mode:[json.loads((OUT/f'{mode}_{i}.json').read_text('utf8')) for i in (1,2,3)]
          for mode in ('before','corrected','fast')}
    fingerprints={mode:[hashlib.sha256(json.dumps(r['rows'],ensure_ascii=False,
        sort_keys=True).encode()).hexdigest() for r in values]for mode,values in runs.items()}
    assert len(set(fingerprints['corrected']+fingerprints['fast']))==1
    assert len(set(fingerprints['before']))==1
    old={key(r):r for r in runs['before'][0]['rows']}
    new={key(r):r for r in runs['fast'][0]['rows']}
    assert old.keys()==new.keys()
    improvements=defaultdict(list);worsened=[];weight_changes=[];assignment_changes=0
    for k,b in old.items():
        a=new[k]
        assert ledger(a)==ledger(b),k
        for rowset in (a,b):
            native=[x['viewer_hit']for x in rowset['rows'] if x['viewer_hit'] is not None
                    and x['actual_hit'] is not None and not x['viewer_optional']]
            assert len(native)==len(set(native)),k
        before=[(r['actual_hit'],r['viewer_hit'],r['viewer_weight'])for r in b['rows']]
        after=[(r['actual_hit'],r['viewer_hit'],r['viewer_weight'])for r in a['rows']]
        assignment_changes+=before!=after
        delta=b['window_distance_mean']-a['window_distance_mean']
        if delta>.1:improvements[k[1],b['skill_name']].append((k[3],delta*1000))
        if delta<-.1:worsened.append(dict(key=k,before=summary(b),after=summary(a)))
        if abs(a['realization']['realized_weight']-b['realization']['realized_weight'])>1e-8:
            weight_changes.append(dict(key=k,before=summary(b),after=summary(a)))
    times={m:dict(runs=[r['matching_seconds'] for r in rr],
                 median=statistics.median(r['matching_seconds']for r in rr))
           for m,rr in runs.items()}
    result=dict(casts=len(new),jobs=len(set(k[1]for k in new)),
        skills=len(set((k[1],k[2])for k in new)),timing=times,fingerprints=fingerprints,
        exact_optimized_results=True,damage_ledger_unchanged=True,
        assignment_changes=assignment_changes,
        improved_over_100ms=[dict(job=j,skill=s,count=len(v),casts=v)
                            for (j,s),v in improvements.items()],
        worsened_over_100ms=worsened,weight_changes=weight_changes,
        firestorm=[dict(before=summary(old[k]),after=summary(new[k])) for k in new if k[2]=='2215'],
        brand=[summary(new[k])for k in new if k[2]=='5216'and k[3]in(26753,82106,306976)])
    (OUT/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in result.items()if k not in ('fingerprints','worsened_over_100ms','weight_changes','firestorm','brand','improved_over_100ms')},ensure_ascii=False,indent=2))
    print('improved',sum(r['count']for r in result['improved_over_100ms']),'skill groups',len(improvements))
    for r in result['firestorm']:
        print('fire',r['after']['time'],[(x,r[x]['procs'],r[x]['summary']['missing'],
              r[x]['realization']['raw_multiplier_rate_percent'])for x in ('before','after')])
    print('weight changes',len(weight_changes),'worsened',len(worsened))

if __name__=='__main__':main()
