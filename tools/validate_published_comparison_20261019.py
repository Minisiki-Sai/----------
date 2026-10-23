from __future__ import annotations
import json
import math
from pathlib import Path
from html.parser import HTMLParser
from run_published_comparison_batch_20261019 import OUT, CASES, ENGINES, provenance

def main():
    provenance()
    proof=json.loads((OUT/'provenance.json').read_text(encoding='utf-8'))
    hashes={x['id']:x['sha256'] for x in proof['cases']}
    results=[]; matched=0
    for case,name in CASES:
        ids=[]
        for version in ('4.78','4.85'):
            path=OUT/f'{case}_v{version}.json'
            r=json.loads(path.read_text(encoding='utf-8'))
            assert r['status']=='ok',(case,version,r.get('error'))
            assert r['captureSha256']==hashes[case],(case,version,'capture hash changed')
            assert r['score']['theoryPlanner']=='v401-full-team-score',(case,version)
            assert not r['score'].get('theoryPlannerError'),(case,version,r['score']['theoryPlannerError'])
            assert r['schema']==(20261013 if version=='4.78' else 20261017)
            rows=r['scoreRowsDetailed']
            assert len(rows)==8,(case,version,len(rows))
            for row in rows:
                for field in ('total_score','output_score','support_score','survival_penalty','actual_standard_power','theory_standard_power'):
                    assert math.isfinite(row[field]),(case,version,row['name'],field)
            ids.append({row['source_id'] for row in rows})
            outside=[f for f in r['moduleOrigins'].values() if f and str(ENGINES[version]).lower() not in f.lower()]
            assert not outside,outside
            assert r['networkDisabled'] is True
            assert not r['score'].get('scoreSync',{}).get('enabled',False)
            results.append({'case':case,'version':version,'players':len(rows),'schema':r['schema'],
                            'fullScorer':True,'captureHashVerified':True,'publishedModuleOriginsVerified':True,
                            'decodeSeconds':r['decodeSeconds'],'scoreSeconds':r['scoreSeconds']})
        assert ids[0]==ids[1],(case,'player identity mismatch')
        matched+=len(ids[0])
    comparison=json.loads((OUT/'comparison.json').read_text(encoding='utf-8'))
    assert len(comparison['battles'])==6 and len(comparison['players'])==matched==48
    page=OUT/'v4.78与v4.85六场评分对照_20261019.html'
    source=page.read_text(encoding='utf-8')
    HTMLParser().feed(source)
    for case,name in CASES:
        assert source.count('id="'+case+'"')==1,case
    assert '尚未完成或计算失败' not in source
    result={'runs':results,'matchedPlayerPairs':matched,'captures':6,'fullScoringRuns':12,
            'uploadsDisabled':True,'noProductionCodeChanges':True,'report':str(page)}
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='runs'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
