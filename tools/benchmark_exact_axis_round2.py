"""Offline pure-runtime probe; separate process per sample, no score uploads."""
import argparse
import cProfile
import gc
import hashlib
import json
import os
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DRAGON_NEST_NATIVE_NO_QT'] = '1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--defer-gc', action='store_true')
    parser.add_argument('--round2', choices=('0', '1'), default='0')
    parser.add_argument('--round3', choices=('0', '1'), default='0')
    args = parser.parse_args()
    os.environ['DN_SCORE_EXACT_ROUND2'] = args.round2
    os.environ['DN_SCORE_EXACT_ROUND3'] = args.round3
    def offline(event, arguments):
        if event == 'socket.connect':
            raise PermissionError('Offline timeline benchmark')
    sys.addaudithook(offline)
    from dragon_nest_builder.timeline_engine import TimelineSimulationSnapshot
    from dragon_nest_builder.timeline_parallel import _worker_owner
    with args.spec.open('rb') as handle:
        spec = pickle.load(handle)
    owner = _worker_owner(TimelineSimulationSnapshot.create(spec['build_override'], spec['effects']))
    owner._extreme_timeline_process_events = False
    profiler = cProfile.Profile() if args.profile else None
    gc_seconds = [0.0]
    gc_started = [0.0]
    def gc_callback(phase, info):
        if phase == 'start':
            gc_started[0] = time.perf_counter()
        else:
            gc_seconds[0] += time.perf_counter() - gc_started[0]
    gc.callbacks.append(gc_callback)
    started = time.perf_counter()
    if args.defer_gc:
        gc.disable()
    try:
        if profiler:
            result = profiler.runcall(owner._extreme_timeline_result_from_spec, spec)
        else:
            result = owner._extreme_timeline_result_from_spec(spec)
    finally:
        gc.enable()
    seconds = time.perf_counter() - started
    comparable = {k: v for k, v in result.items() if k not in ('_planner_settings', 'planner_refinement')}
    digest = hashlib.sha256(json.dumps(comparable, sort_keys=True, ensure_ascii=True, default=str).encode()).hexdigest()
    report = dict(seconds=seconds, gc_seconds=gc_seconds[0], digest=digest,
                  dps=result.get('dps'), segments=len(result.get('segments', [])),
                  round2=args.round2, round3=args.round3,
                  python_hash_seed=os.environ.get('PYTHONHASHSEED', 'random'),
                  defer_gc=args.defer_gc, result=result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, default=str), encoding='utf8')
    if profiler:
        profiler.dump_stats(str(args.profile))
    print(json.dumps({k: v for k, v in report.items() if k != 'result'}), flush=True)


if __name__ == '__main__':
    main()
