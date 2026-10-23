"""Bounded offline continuation with hash-pinned, atomically saved guest snapshots."""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import client_guest_snapshot_v41 as snapshots
from tools.emulate_client_sections_v40 import SectionExperiment,v39
from tools.client_checksum_model_v41 import install_checksum_accelerator

OUT=ROOT/'outputs/client_protection_v41_20260908'


def atomic(path,data):
    staging=path.with_name(path.name+'.next')
    staging.write_bytes(data)
    staging.replace(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume-sha256')
    parser.add_argument('--segments',type=int,choices=range(1,121),default=40)
    parser.add_argument('--accelerate-checksum',action='store_true')
    args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    path=OUT/'checkpoint.zip'
    if path.exists() and not args.resume_sha256:
        raise SystemExit('Existing checkpoint retained. Supply its independently recorded SHA-256 to resume.')
    source_paths=[Path(__file__),Path(snapshots.__file__),ROOT/'tools/emulate_client_sections_v40.py',Path(v39.__file__),ROOT/'tools/client_checksum_model_v41.py']
    source_hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in source_paths}
    raw=v39.CLIENT.read_bytes()
    if args.resume_sha256:
        experiment=snapshots.restore_snapshot(raw,path.read_bytes(),args.resume_sha256)
        if experiment.stop_reason['reason'] not in ('instruction_limit','timeout'):
            raise SystemExit('Snapshot retained at unresolved boundary; implement and validate that exact contract before continuing.')
    else:
        experiment=SectionExperiment(raw,instruction_limit=50000000,seconds=40,module_metadata=True,
            heap_handle=True,guest_heap=True,os_version=tuple(sys.getwindowsversion().platform_version),shared_version=True)
    checksum=install_checksum_accelerator(experiment) if args.accelerate_checksum else None
    history=[]
    previous=OUT/'progress.json'
    if args.resume_sha256 and previous.exists():
        history=json.loads(previous.read_text('utf-8')).get('checkpoints',[])
    for segment in range(args.segments):
        report=experiment.run(resume=bool(args.resume_sha256) or segment>0)
        report['checksum_model']=checksum.report() if checksum else None
        saved_at=time.monotonic()
        blob=snapshots.encode_snapshot(experiment)
        digest=sha256(blob).hexdigest()
        atomic(path,blob)
        info=dict(sha256=digest,size=len(blob),instructions=report['instructions'],stop=report['stop'],
            client_sha256=report['client_sha256'],source_hashes=source_hashes,
            source_changes=[p.name for p in source_paths if sha256(p.read_bytes()).hexdigest()!=source_hashes[p.name]],
            save_seconds=time.monotonic()-saved_at)
        atomic(OUT/'checkpoint_identity.json',json.dumps(info,ensure_ascii=False,indent=2).encode('utf-8'))
        checkpoint=dict(index=len(history)+1,instructions=report['instructions'],seconds=report['total_seconds'],
            stop=report['stop'],modified_sections=report['modified_sections'],section_calls=len(report['section_calls']),
            guest_registers=report['guest_registers'],snapshot=info)
        history.append(checkpoint)
        report.update(checkpoints=history,source_hashes=source_hashes,
            snapshot=info,resumed_from=args.resume_sha256)
        atomic(previous,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        print(json.dumps({k:checkpoint[k] for k in ('index','instructions','seconds','stop','modified_sections','section_calls')},ensure_ascii=False),flush=True)
        if report['stop']['reason'] not in ('instruction_limit','timeout') or report['error'] or info['source_changes']:
            break
    if sha256(v39.CLIENT.read_bytes()).hexdigest()!=sha256(raw).hexdigest():
        raise RuntimeError('Client changed externally; do not combine build evidence.')
