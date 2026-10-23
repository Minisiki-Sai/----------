"""Offline, deterministic axis profiler; never uploads or changes scoring options."""
import argparse
import cProfile
import gc
import copy
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main():
    p = argparse.ArgumentParser()
    p.add_argument('job')
    p.add_argument('output', type=Path)
    p.add_argument('--profile', action='store_true')
    p.add_argument('--gc-threshold', type=int, default=0)
    p.add_argument('--gc-freeze', action='store_true')
    a = p.parse_args()
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService
    data = pickle.loads(Path('work/support_schedule_1003_baseline_20261012.pickle').read_bytes())
    spec = next(copy.deepcopy(s) for s in data['specs'].values() if s['job'] == a.job)
    store = DataStore.load()
    runtime = LegacyTimelineService(store, BuildCalculator(store)).extreme_runtime(copy.deepcopy(store.defaults['build1']))
    profile = cProfile.Profile()
    original_thresholds = gc.get_threshold()
    if a.gc_threshold:
        gc.set_threshold(a.gc_threshold, *original_thresholds[1:])
    if a.gc_freeze:
        gc.collect()
        gc.freeze()
    gc_before = gc.get_stats()
    gc_started = [0.0]
    gc_seconds = [0.0, 0.0, 0.0]
    def on_gc(phase, info):
        if phase == 'start':
            gc_started[0] = time.perf_counter()
        else:
            gc_seconds[info['generation']] += time.perf_counter() - gc_started[0]
    gc.callbacks.append(on_gc)
    start = time.perf_counter()
    if a.profile:
        result = profile.runcall(runtime._extreme_timeline_result_from_spec, spec)
        profile.dump_stats(str(a.output.with_suffix('.prof')))
    else:
        result = runtime._extreme_timeline_result_from_spec(spec)
    elapsed = time.perf_counter() - start
    a.output.write_bytes(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
    gc_after = gc.get_stats()
    gc.callbacks.remove(on_gc)
    gc.set_threshold(*original_thresholds)
    print({'job': a.job, 'seconds': elapsed, 'total': result.get('total'), 'segments': len(result.get('segments', [])),
           'gc_collections': [after['collections'] - before['collections'] for before, after in zip(gc_before, gc_after)],
           'gc_seconds': gc_seconds}, flush=True)

if __name__ == '__main__':
    main()
