"""Pair only corpus captures with physical changes, with at most two workers.

Unchanged captures still require complete raw AND aggregated physical hashes
from the identical-byte full corpus. Existing paired results are reusable only
when their runtime/data and physical hashes agree with those accepted inputs.
"""
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.compare_self_target_pairs_v28 import compare

OUT=ROOT/'outputs/compact_entity_selectors_20260908'
PAIR=OUT/'self_target_pair'
RUNTIMES={'before':OUT/'rake_frozen_runtime/after','after':OUT/'self_target_frozen_after'}
RUNTIMES['after_kali']=OUT/'kali_parent_frozen_after'

def read(path):return json.loads(path.read_text('utf-8'))

def run(item):
    name,side,capture=item
    dest=PAIR/f'{name}_{side}.json'
    proc=subprocess.run([sys.executable,str(ROOT/'tools/audit_self_target_pair_v28.py'),
        str(RUNTIMES[side]),capture,str(dest)],cwd=ROOT,encoding='utf-8',errors='replace',
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode:raise RuntimeError(f'{name}/{side}: {proc.stderr[-3000:]}')
    print(f'paired replay ready: {name}/{side}',flush=True)
    return dest

def matches(pair,corpus,signature):
    if pair['capture_sha256']!=corpus['capture_sha256']:return False
    for key in ('raw_source_invariants','aggregated_invariants','profession_summary'):
        if pair[key]!=corpus[key]:return False
    return pair['frozen_signature']==signature

def main():
    docs={
        'before':read(OUT/'after_rake36_frozen/profession_direct_damage_report.json'),
        'after':read(OUT/'after_self_target36_frozen/profession_direct_damage_report.json')}
    reports={s:{r['capture_path']:r for r in d['capture_manifest']}for s,d in docs.items()}
    assert reports['before'].keys()==reports['after'].keys()
    assert not docs['before']['failures']and not docs['after']['failures']
    plans=[];changed=[];unchanged=[];available={}
    for file in PAIR.glob('*_before.json'):
        available[read(file)['capture_path']]=file.name.removesuffix('_before.json')
    for capture,a in reports['after'].items():
        b=reports['before'][capture]
        assert a['capture_sha256']==b['capture_sha256']
        if all(a[k]==b[k]for k in ('raw_source_invariants','aggregated_invariants')):
            unchanged.append(capture);continue
        name=available.get(capture,Path(capture).stem)
        changed.append((name,capture))
        for side in ('before','after'):
            file=PAIR/f'{name}_{side}.json'
            if not file.exists()or not matches(read(file),reports[side][capture],docs[side]['analysis_signature']):
                plans.append((name,side,capture))
    # Three possible v28 child-bearing captures are replayed against the last
    # hardening snapshot too, within this same two-worker budget. Their results
    # are separate from the self-target acceptance below.
    if '--kali-first' in sys.argv:
        scan=read(OUT/'kali_parent_candidate_superset.json')
        extra=[(Path(r['capture']).stem,'after_kali',r['capture'])for r in scan['captures']if r['candidate_superset']]
        plans=extra+plans
    print(f'{len(unchanged)} fully conserved; {len(changed)} need individual proof; {len(plans)} replays pending',flush=True)
    with ThreadPoolExecutor(max_workers=2)as pool:
        for task in as_completed([pool.submit(run,item)for item in plans]):task.result()
    results=[]
    for name,capture in changed:
        pair=[read(PAIR/f'{name}_{s}.json')for s in ('before','after')]
        for doc,side in zip(pair,('before','after')):
            assert matches(doc,reports[side][capture],docs[side]['analysis_signature'])
        result=compare(*pair)
        (PAIR/f'{name}_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append({'name':name,'capture':capture,'comparison':f'{name}_comparison.json',
            'all_changes_proven':result['all_changes_proven'],
            'checks':{k:{f:v for f,v in r.items()if f not in('unexplained','duplicate_proofs','correction_proofs')}
                |{'unexplained_count':len(r['unexplained'])}for k,r in result['checks'].items()}})
    summary={'captures':len(reports['after']),'identical_physical_hashes':unchanged,'paired':results,
        'all_accepted':all(r['all_changes_proven']for r in results),
        'runtime':{s:d['analysis_signature']['sha256']for s,d in docs.items()}}
    (OUT/'self_target_corpus_acceptance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items()if k not in('paired','identical_physical_hashes')},ensure_ascii=False))
    assert summary['all_accepted'],'Unexplained corpus changes remain; do not accept.'

if __name__=='__main__':main()
