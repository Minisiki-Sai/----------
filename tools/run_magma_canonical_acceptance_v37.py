"""Freeze and fully replay a fixed corpus; never score, upload or reuse caches.

Only the post-admission autonomous-family module may differ in the paired
runtime. Both sides use current other work, not an older scoring snapshot.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/magma_runtime_v37_20260908'
BASE = ROOT / 'outputs/compact_entity_selectors_20260908/after_late_field36_final/profession_direct_damage_report.json'


def read(path):
    return json.loads(path.read_text('utf-8'))


def freeze(side):
    sys.path.insert(0, str(ROOT))
    from tools.freeze_udp_audit_runtime_v27 import freeze as package
    destination = OUT / side
    package(ROOT, destination)
    # Audit dependencies are frozen too, including their resource readers.
    for p in (ROOT / 'tools').glob('*.py'):
        shutil.copyfile(p, destination / 'tools' / p.name)
    hashes = {p.relative_to(destination).as_posix(): sha256(p.read_bytes()).hexdigest()
              for top in ('dragon_nest_builder', 'tools')
              for p in (destination / top).rglob('*') if p.is_file()}
    (destination / 'frozen_manifest.json').write_text(json.dumps(hashes, sort_keys=True), 'utf-8')
    print(json.dumps(dict(side=side, files=len(hashes))))


def worker(side, capture):
    runtime = OUT / side
    output = OUT / (side + '_results') / (sha256(capture.encode()).hexdigest() + '.json')
    if output.exists():
        previous = read(output)
        frozen = read(runtime / 'frozen_manifest.json')
        expected = {k: v for k, v in frozen.items()
                    if k.startswith('dragon_nest_builder/') and k.endswith('.py')}
        assert previous['runtime_py_hashes'] == expected, 'different runtime checkpoint'
        assert previous['capture_path'] == capture
        assert previous['capture_sha256'] == sha256(Path(capture).read_bytes()).hexdigest()
        return dict(side=side, capture=capture, path=str(output), resumed=True)
    # One fresh interpreter per capture releases all decoder memory afterwards.
    command = [sys.executable, '-B', str(runtime / 'tools/audit_self_target_pair_v28.py'),
               str(runtime), capture, str(output)]
    result = subprocess.run(command, cwd=runtime, capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stderr[-8000:])
    return dict(side=side, capture=capture, path=str(output))


def run(sides, names):
    manifest = read(BASE)['capture_manifest']
    captures = [r['capture_path'] for r in manifest
                if not names or Path(r['capture_path']).stem in names]
    if names:
        assert len(captures) == len(names), (names, captures)
    tasks = [(side, capture) for capture in captures for side in sides]
    failures = []
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(worker, *task): task for task in tasks}
        for future in as_completed(futures):
            try:
                print(json.dumps(future.result(), ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append(dict(task=futures[future], error=str(exc)))
                print(json.dumps(failures[-1], ensure_ascii=False), flush=True)
    assert not failures, failures


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('freeze', 'run'))
    ap.add_argument('--sides', nargs='+', choices=('before', 'after'), required=True)
    ap.add_argument('--names', nargs='*')
    args = ap.parse_args()
    if args.action == 'freeze':
        for side in args.sides:
            freeze(side)
    else:
        run(args.sides, set(args.names or ()))
