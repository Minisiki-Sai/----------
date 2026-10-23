"""Fixed 35-capture, two-worker shadow validation; no scoring or uploads."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor,as_completed
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.replay_native_wire_state_v47 import run,OUT

MANIFEST=ROOT/'outputs/named_hit_coverage_20260908/after_v2_35/profession_direct_damage_report.json'
BASELINE=ROOT/'outputs/magma_runtime_v37_20260908/acceptance.json'


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,
        default=lambda x:sorted(x) if isinstance(x,(set,frozenset)) else str(x)),encoding='utf-8')


def worker(entry):
    path=Path(entry['capture_path'])
    destination=OUT/(path.stem+'_replay.json')
    result=None
    if destination.is_file():
        saved=json.loads(destination.read_text('utf-8'))
        dependencies=saved.get('source_sha256',{})
        if (dependencies and saved.get('capture_sha256')==entry['capture_sha256'] and
                all(Path(f).is_file() and sha256(Path(f).read_bytes()).hexdigest()==h for f,h in dependencies.items())):
            actual=sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(1024*1024):actual.update(chunk)
            if actual.hexdigest()==entry['capture_sha256']:result=saved
    if result is None:
        log=io.StringIO()
        with redirect_stdout(log):result=run(path)
    if result['capture_sha256']!=entry['capture_sha256']:
        raise ValueError('capture hash differs from fixed cohort: '+str(path))
    write(destination,result)
    summary={key:result[key] for key in ('capture','capture_sha256','counts','elapsed_seconds',
        'physical_amount_before','physical_amount_after','physical_fields_unchanged','state_peaks',
        'object_resolution_status','rejected_numeric_fields','framing_rejections')}
    summary.update(report=str(destination),report_sha256=sha256(destination.read_bytes()).hexdigest(),
        candidate_counts=result['additional_prefix_candidates']['counts'],
        candidate_configs=result['additional_prefix_candidates']['by_config'])
    return summary


def main():
    started=time.perf_counter();raw=MANIFEST.read_bytes();manifest=json.loads(raw)
    entries=manifest['capture_manifest']
    if len(entries)!=35 or len({x['capture_sha256'] for x in entries})!=35:raise ValueError('wrong fixed cohort')
    OUT.mkdir(parents=True,exist_ok=True)
    completed=[];failures=[]
    with ProcessPoolExecutor(max_workers=2,max_tasks_per_child=1) as pool:
        futures={pool.submit(worker,e):e for e in entries}
        for future in as_completed(futures):
            entry=futures[future]
            try:
                result=future.result();completed.append(result)
                print(json.dumps(dict(done=len(completed),failed=len(failures),capture=Path(result['capture']).name,
                    physical_rows=result['counts']['compared_physical_damage_records'],unchanged=result['physical_fields_unchanged']),ensure_ascii=False),flush=True)
            except Exception as exc:
                failure=dict(capture=entry['capture_path'],error=repr(exc));failures.append(failure)
                print(json.dumps(failure,ensure_ascii=False),flush=True)
            write(OUT/'corpus_progress.json',dict(completed=completed,failures=failures,total=35))
    counts=Counter();candidates=Counter();configs=Counter();amounts=[0,0]
    for result in completed:
        counts.update(result['counts']);candidates.update(result['candidate_counts']);configs.update(result['candidate_configs'])
        amounts[0]+=result['physical_amount_before'];amounts[1]+=result['physical_amount_after']
    prior=BASELINE.read_bytes();baseline=json.loads(prior)
    summary=dict(schema='fixed-native-corpus-v47',manifest=str(MANIFEST),manifest_sha256=sha256(raw).hexdigest(),
        successful=len(completed),failed=failures,counts=dict(counts),amounts=amounts,
        all_physical_fields_unchanged=len(completed)==35 and not failures and all(r['physical_fields_unchanged'] for r in completed),
        candidate_counts=dict(candidates),candidate_configs=dict(configs),elapsed_seconds=time.perf_counter()-started,
        reports=sorted(completed,key=lambda x:x['capture']),production_changed=False,new_confirmed_skill_hits=0,
        certification_baseline=dict(path=str(BASELINE),sha256=sha256(prior).hexdigest(),passed_jobs=baseline['passed_jobs'],
             jobs=[dict(job=j['job'],percent=j['after']['named_coverage_percent'],met=j['named_target_met']) for j in baseline['jobs']],
             note='Historical certified v37 baseline only. Shadow object joins are not new confirmed skill Hits and do not change these percentages.'),
        limitations=['Strict native reader comparison for framed direct37/41 numeric rows only.',
             'Not a full decoder/aggregator/score regression; unknown directions, other record kinds and unframed packets remain outside field comparison.',
             'No skill certificate is produced by the shadow state. Per-profession95% completion is not claimed.'])
    write(OUT/'corpus_summary.json',summary)
    print(json.dumps(dict(successful=len(completed),failed=len(failures),all_unchanged=summary['all_physical_fields_unchanged'],
        physical_rows=counts['compared_physical_damage_records'],candidates=dict(candidates),seconds=summary['elapsed_seconds']),ensure_ascii=False),flush=True)
    if not summary['all_physical_fields_unchanged']:sys.exit(1)


if __name__=='__main__':main()
