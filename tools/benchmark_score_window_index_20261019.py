"""Offline exact kernel benchmark; never presented as an end-to-end speedup."""
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.score_burst import window_power

SOURCE = ROOT / 'outputs/verification/all_jobs_published_ab_20261019/detailed_attribution'
DEST = ROOT / 'outputs/verification/scoring_optimization_20261019'


def original(parts, windows):
    return sum(p for t, p in parts if t is not None
               and any(a <= t < b for a, b in windows))


def main():
    data = json.loads((SOURCE / 'attribution.json').read_text(encoding='utf-8'))
    tasks = []
    for player in data['players']:
        ledger = json.loads((SOURCE / player['cast_ledger']).read_text(encoding='utf-8'))
        parts = [(c['seconds'], c['new']['power']) for c in ledger if c.get('new')]
        diag = player['versions']['4.85']['burst_diagnostics']
        windows = [(w['start'], w['end']) for w in diag.get('actual_windows', [])]
        tasks.append((parts, windows))
    assert all(original(p, w) == window_power(p, w) for p, w in tasks)
    measurements = {'before': [], 'after': []}
    repeats = 20
    for trial in range(7):
        for name, func in ([('before', original), ('after', window_power)]
                           if trial % 2 == 0 else [('after', window_power), ('before', original)]):
            start = time.perf_counter()
            for _ in range(repeats):
                for parts, windows in tasks:
                    func(parts, windows)
            measurements[name].append((time.perf_counter() - start) / repeats)
    before, after = (statistics.median(measurements[k]) for k in ('before', 'after'))
    result = dict(scope='window membership kernel only, cast-start parts; not a full scoring rerun',
                  players=len(tasks), parts=sum(len(p) for p, _ in tasks),
                  identical_results=len(tasks), mismatches=0,
                  before_seconds=before, after_seconds=after,
                  speedup=before/after, reduction_percent=100*(1-after/before),
                  samples=measurements,
                  source_sha256=hashlib.sha256((SOURCE/'attribution.json').read_bytes()).hexdigest())
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST/'window_index_benchmark.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
