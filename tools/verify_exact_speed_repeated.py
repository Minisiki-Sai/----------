"""Sequential, frozen-source repeated scoring experiment; never uploads.

Four cumulative switch configurations, three predeclared balanced blocks.
Each run owns a Windows Job Object so its workers cannot leak into later runs.
Existing user processes are only observed, never stopped or reprioritized.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MODES = {'A': ('0', '0', '0'), 'B': ('1', '0', '0'),
         'C': ('1', '1', '0'), 'D': ('1', '1', '1')}
ORDERS = ['ABDC', 'CDBA', 'BACD']


class Accounting(c.Structure):
    _fields_ = [(s, c.c_longlong) for s in
                ('user', 'kernel', 'period_user', 'period_kernel')] + [
        ('page_faults', w.DWORD), ('total_processes', w.DWORD),
        ('active_processes', w.DWORD), ('terminated_processes', w.DWORD)]


class BasicLimit(c.Structure):
    _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong),
                ('flags', w.DWORD), ('min_ws', c.c_size_t), ('max_ws', c.c_size_t),
                ('active_limit', w.DWORD), ('affinity', c.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class IO(c.Structure):
    _fields_ = [(s, c.c_ulonglong) for s in
                ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimit(c.Structure):
    _fields_ = [('basic', BasicLimit), ('io', IO), ('process_memory', c.c_size_t),
                ('job_memory', c.c_size_t), ('peak_process_memory', c.c_size_t),
                ('peak_job_memory', c.c_size_t)]


class Memory(c.Structure):
    _fields_ = [('length', w.DWORD), ('load', w.DWORD)] + [
        (s, c.c_ulonglong) for s in ('total', 'available', 'page_total', 'page_available',
                                     'virtual_total', 'virtual_available', 'extended')]


K = c.WinDLL('kernel32', use_last_error=True)
K.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
K.CreateJobObjectW.restype = w.HANDLE
K.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
K.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p]
K.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
K.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
K.CloseHandle.argtypes = [w.HANDLE]
K.GetSystemTimes.argtypes = [c.POINTER(w.FILETIME)] * 3
K.GlobalMemoryStatusEx.argtypes = [c.POINTER(Memory)]


def checked(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


def system_sample():
    vals = [w.FILETIME() for _ in range(3)]
    checked(K.GetSystemTimes(*(c.byref(x) for x in vals)))
    numbers = [((x.dwHighDateTime << 32) | x.dwLowDateTime) / 1e7 for x in vals]
    mem = Memory(length=c.sizeof(Memory))
    checked(K.GlobalMemoryStatusEx(c.byref(mem)))
    return {'time': time.time(), 'idle': numbers[0], 'kernel': numbers[1],
            'user': numbers[2], 'available_mb': mem.available / 1048576,
            'memory_load_percent': mem.load}


class Job:
    def __init__(self):
        self.handle = K.CreateJobObjectW(None, None)
        checked(self.handle)
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE, only our assigned child tree.
        checked(K.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)))

    def sample(self):
        info, limits = Accounting(), ExtendedLimit()
        checked(K.QueryInformationJobObject(self.handle, 1, c.byref(info), c.sizeof(info), None))
        checked(K.QueryInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits), None))
        return {'cpu_seconds': (info.user + info.kernel) / 1e7,
                'active_processes': info.active_processes,
                'total_processes': info.total_processes,
                'peak_commit_mb': limits.peak_job_memory / 1048576}

    def close(self):
        checked(K.TerminateJobObject(self.handle, 0))
        checked(K.CloseHandle(self.handle))


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


def digest_file(p):
    with p.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree_hash(root):
    return {str(p.relative_to(root)): digest_file(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def freeze(destination):
    destination.mkdir()
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')
    for name in ['dragon_nest_builder', 'third_party/skill_hit_tool/app']:
        shutil.copytree(ROOT / name, destination / name, ignore=ignore)
    for p in ROOT.glob('*.py'):
        shutil.copy2(p, destination / p.name)
    (destination / 'tools').mkdir()
    shutil.copy2(ROOT / 'tools/benchmark_exact_score_20261019.py', destination / 'tools')


def run_one(index, block, mode, args, frozen):
    stem = f'{index:02d}_block{block}_{mode}'
    target = args.output / f'{stem}.json'
    log_path = args.output / f'{stem}.log'
    fast, r2, r3 = MODES[mode]
    env = dict(os.environ, PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1',
               PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    command = [sys.executable, '-X', 'utf8', '-B',
               str(frozen / 'tools/benchmark_exact_score_20261019.py'),
               '--capture', str(args.capture), '--output', str(target),
               '--fast', fast, '--round2', r2, '--round3', r3]
    emit({'event': 'start', 'index': index, 'block': block, 'mode': mode, 'flags': MODES[mode]})
    job = Job()
    samples = []
    started = time.monotonic()
    try:
        with log_path.open('w', encoding='utf8') as log:
            proc = subprocess.Popen(command, cwd=frozen, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            checked(K.AssignProcessToJobObject(job.handle, w.HANDLE(proc._handle)))
            last_notice = 0
            while True:
                samples.append(system_sample() | job.sample())
                elapsed = time.monotonic() - started
                if elapsed - last_notice >= 30:
                    last_notice = elapsed
                    with log_path.open(encoding='utf8', errors='replace') as handle:
                        recent = handle.readlines()[-1:] or ['']
                    emit({'event': 'progress', 'index': index, 'mode': mode,
                          'elapsed': round(elapsed, 1), 'recent': recent[0].strip()[:600],
                          'resources': samples[-1]})
                if proc.poll() is not None:
                    break
                time.sleep(2)
            result_code = proc.wait()
            accounting = job.sample()
    finally:
        job.close()
        save(args.output / f'{stem}_telemetry.json', samples)
    if result_code or not target.exists():
        raise RuntimeError(f'{stem} failed with {result_code}; see {log_path}')
    report = json.loads(target.read_text('utf8'))
    if report.get('status') != 'ok':
        raise RuntimeError(f'{stem} score failed')
    system_cpu = []
    for old, new in zip(samples, samples[1:]):
        total = new['kernel'] + new['user'] - old['kernel'] - old['user']
        if total > 0:
            system_cpu.append(100 * (1 - (new['idle'] - old['idle']) / total))
    summary = {'index': index, 'block': block, 'mode': mode, 'file': target.name,
               **{k: report[k] for k in ('init_seconds', 'decode_seconds', 'score_seconds', 'elapsed_seconds')},
               'parallel_seconds': report['theory_diagnostics'].get('parallel_seconds'),
               'task_count': report['theory_diagnostics'].get('task_count'),
               'accounting': accounting, 'system_cpu_median_percent': statistics.median(system_cpu),
               'system_cpu_peak_percent': max(system_cpu),
               'minimum_available_mb': min(s['available_mb'] for s in samples)}
    save(args.output / f'{stem}_summary.json', summary)
    emit({'event': 'finished', **summary})
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.capture = args.capture.resolve(strict=True)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    frozen = args.output / 'frozen_source'
    freeze(frozen)
    manifest = tree_hash(frozen)
    capture_hash = digest_file(args.capture)
    save(args.output / 'manifest.json', {'files': manifest, 'capture_sha256': capture_hash,
         'capture': str(args.capture), 'python': sys.version, 'executable': sys.executable,
         'logical_cpus': os.cpu_count(), 'orders': ORDERS, 'modes': MODES,
         'hash_seed': 0, 'workers': 4, 'network': 'disabled in each benchmark',
         'cache_policy': 'new process and empty independent evidence directory; OS caches not flushed',
         'initial_system': system_sample()})
    # Reading the manifest and capture also gives every mode a comparable warm file cache.
    summaries = []
    for block, order in enumerate(ORDERS, 1):
        for mode in order:
            if tree_hash(frozen) != manifest or digest_file(args.capture) != capture_hash:
                raise RuntimeError('Frozen inputs changed; refuse to mix timings')
            summaries.append(run_one(len(summaries) + 1, block, mode, args, frozen))
            save(args.output / 'runs.json', summaries)
    save(args.output / 'complete.json', {'runs': len(summaries), 'inputs_unchanged': tree_hash(frozen) == manifest,
                                        'finished_at': time.time()})
    emit({'event': 'complete', 'runs': len(summaries)})


if __name__ == '__main__':
    main()
