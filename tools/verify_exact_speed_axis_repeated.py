"""Short-interval round-three diagnostic, separate from full-capture results."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

from verify_exact_speed_repeated import Job, K, w, checked, save, tree_hash, digest_file, emit, system_sample


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--full-directory', required=True, type=Path)
    p.add_argument('--spec', required=True, type=Path)
    args = p.parse_args()
    base = args.full_directory.resolve()
    assert json.loads((base / 'complete.json').read_text('utf8'))['runs'] == 12
    folder = base / 'axis_repeat'
    folder.mkdir(exist_ok=False)
    frozen = folder / 'frozen_source'
    shutil.copytree(base / 'frozen_source', frozen)
    source_tool = Path(__file__).with_name('benchmark_exact_axis_round2.py')
    shutil.copy2(source_tool, frozen / 'tools' / source_tool.name)
    spec = folder / 'spec.pkl'
    shutil.copy2(args.spec, spec)
    files = tree_hash(frozen)
    orders = ['CD', 'DC', 'CD', 'DC']
    save(folder / 'manifest.json', {'orders': orders, 'files': files, 'spec_sha256': digest_file(spec),
         'hash_seed': 0, 'first_round': True, 'round2': True, 'round3': 'C=off,D=on',
         'scope': 'one exact timeline spec, not full-score speedup'})
    env = dict(os.environ, PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1',
               PYTHONIOENCODING='utf-8', DN_SCORE_EXACT_STATE_REUSE='1',
               DN_SCORE_EXACT_CANDIDATE_REUSE='1', DN_SCORE_EXACT_DECODE_REUSE='1',
               DN_HIT_EXACT_ASSIGNMENT_FAST='1')
    results = []
    for block, order in enumerate(orders, 1):
        for mode in order:
            stem = f'{len(results)+1:02d}_{mode}'
            output = folder / f'{stem}.json'
            job = Job()
            try:
                with (folder / f'{stem}.log').open('w', encoding='utf8') as log:
                    command = [sys.executable, '-X', 'utf8', '-B', str(frozen / 'tools' / source_tool.name),
                               '--spec', str(spec), '--output', str(output), '--round2', '1',
                               '--round3', '1' if mode == 'D' else '0']
                    proc = subprocess.Popen(command, env=env, cwd=frozen, stdout=log, stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
                    checked(K.AssignProcessToJobObject(job.handle, w.HANDLE(proc._handle)))
                    while proc.poll() is None:
                        time.sleep(1)
                    accounting = job.sample()
            finally:
                job.close()
            if proc.returncode:
                raise RuntimeError(f'Axis run failed: {stem}')
            report = json.loads(output.read_text('utf8'))
            results.append({'block': block, 'mode': mode, 'file': output.name,
                            **{k: report[k] for k in ('seconds', 'gc_seconds', 'digest', 'segments', 'dps')},
                            'accounting': accounting, 'system': system_sample()})
            save(folder / 'runs.json', results)
            emit(results[-1])
    medians = {m: statistics.median(r['seconds'] for r in results if r['mode'] == m) for m in 'CD'}
    pairs = []
    for block in range(1, 5):
        rows = {r['mode']: r for r in results if r['block'] == block}
        pairs.append({'block': block, 'before': rows['C']['seconds'], 'after': rows['D']['seconds'],
                      'reduction_percent': 100 * (1 - rows['D']['seconds'] / rows['C']['seconds'])})
    analysis = {'medians': medians, 'reduction_percent': 100 * (1 - medians['D'] / medians['C']),
                'pairs': pairs, 'identical': len({r['digest'] for r in results}) == 1,
                'inputs_unchanged': tree_hash(frozen) == files, 'runs': results}
    save(folder / 'analysis.json', analysis)
    emit(analysis)


if __name__ == '__main__':
    main()
