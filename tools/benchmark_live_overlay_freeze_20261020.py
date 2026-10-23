"""Real capture equivalence and lock-cost experiment; not audible live latency."""
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource


def run(path):
    source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
    agg = CombatAggregator()
    samples = []
    events = 0
    started = time.perf_counter()
    try:
        for batch in source.iter_batches(2048):
            for event in batch:
                agg.ingest(event)
            events += len(batch)
            current_ms = int(agg._last_ms or 0)
            old_ms, freeze_ms, project_ms = [], [], []
            # Alternate order to avoid attributing warmed caches to one path.
            for repeat in range(4):
                def original():
                    begin = time.perf_counter()
                    result = agg.overlay_snapshot(current_ms, refresh_composition=False)
                    old_ms.append((time.perf_counter() - begin) * 1000)
                    return result
                def detached():
                    begin = time.perf_counter()
                    frozen = agg.freeze_live_overlay(current_ms)
                    freeze_ms.append((time.perf_counter() - begin) * 1000)
                    begin = time.perf_counter()
                    result = frozen.overlay_snapshot(current_ms, refresh_composition=False)
                    project_ms.append((time.perf_counter() - begin) * 1000)
                    return result
                if repeat % 2:
                    after, before = detached(), original()
                else:
                    before, after = original(), detached()
                if before != after:
                    for key in before.keys() | after.keys():
                        if before.get(key) != after.get(key):
                            print("DIFFERENCE", key, str(before.get(key))[:1600],
                                  str(after.get(key))[:1600], flush=True)
                    raise AssertionError((path.name, current_ms))
            samples.append(dict(time=current_ms / 1000, old_lock_ms=statistics.median(old_ms),
                new_lock_ms=statistics.median(freeze_ms), outside_ms=statistics.median(project_ms)))
    finally:
        source.close()
    result = dict(file=path.name, events=events, checks=len(samples)*4,
        exact_equal=True, elapsed=time.perf_counter()-started, samples=samples)
    for key in ("old_lock_ms", "new_lock_ms", "outside_ms"):
        result[key] = dict(median=statistics.median(x[key] for x in samples),
                          max=max(x[key] for x in samples))
    out = ROOT / "outputs/verification/live_latency_fix_20261020"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{path.stem}_overlay.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="samples"}, ensure_ascii=False), flush=True)


for argument in sys.argv[1:]:
    run(Path(argument))
