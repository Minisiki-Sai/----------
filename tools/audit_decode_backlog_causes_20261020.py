"""Read-only replay profiling, not a live latency reproduction."""
import cProfile
import json
import pstats
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder

OUT = ROOT / "outputs/verification/decode_backlog_causes_20261020"

def top(profile):
    stats = pstats.Stats(profile)
    return [dict(file=k[0], line=k[1], function=k[2], calls=v[1],
                 own_seconds=v[2], cumulative_seconds=v[3])
            for k,v in sorted(stats.stats.items(), key=lambda kv:-kv[1][3])[:45]]

def run(path):
    profiler = cProfile.Profile()
    packets = Counter()
    original = _V576PacketEventDecoder.feed_events
    def feed(self, packet):
        packets[int(packet.timestamp_seconds)] += 1
        return profiler.runcall(original, self, packet)
    _V576PacketEventDecoder.feed_events = feed
    source = V576DiagnosticReplaySource(path, ROOT/"dragon_nest_builder/data")
    try:
        start = time.perf_counter()
        events = source.poll()
        decode_seconds = time.perf_counter()-start
    finally:
        source.close()
        _V576PacketEventDecoder.feed_events = original
    agg = CombatAggregator()
    ingest_ms = []
    live_snapshots = []
    for i in range(0, len(events), 512):
        started = time.perf_counter()
        for event in events[i:i+512]:
            agg.ingest(event)
        ingest_ms.append((time.perf_counter()-started)*1000)
        if i // 512 % 12 == 11 or i + 512 >= len(events):
            wall, cpu = time.perf_counter(), time.thread_time()
            agg.snapshot(effect_current_ms=agg._last_ms, include_events=False, live_clock=True)
            live_snapshots.append(dict(events=i+len(events[i:i+512]), time_ms=agg._last_ms,
                                       wall_ms=(time.perf_counter()-wall)*1000,
                                       cpu_ms=(time.thread_time()-cpu)*1000))
    snapshots = []
    snapshot_profiler = cProfile.Profile()
    for _ in range(4):
        wall, cpu = time.perf_counter(), time.thread_time()
        snapshot_profiler.runcall(agg.snapshot, include_events=False,
                                 effect_current_ms=agg._last_ms, live_clock=True)
        snapshots.append(dict(wall_ms=(time.perf_counter()-wall)*1000,
                              cpu_ms=(time.thread_time()-cpu)*1000))
    result = dict(file=path.name, warning="cProfile inflates wall time. No game, live queue or concurrent UI here.",
                  replay_seconds_profiled=decode_seconds, events=len(events),
                  packets=sum(packets.values()), peak_packets_per_second=max(packets.values()),
                  ingest_batches_ms=ingest_ms, aggregate_snapshot_profiled_ms=snapshots,
                  aggregate_snapshots_unprofiled=live_snapshots,
                  decoder_functions=top(profiler), snapshot_functions=top(snapshot_profiler))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/f"{path.stem}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.name, len(events), decode_seconds, snapshots, flush=True)

if __name__ == "__main__":
    run(Path(sys.argv[1]))
