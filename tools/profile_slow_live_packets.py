"""Locate slow ordered decoder calls; optionally profile previously located indices."""
import argparse
import cProfile
import gc
import json
import pstats
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture")
    parser.add_argument("output")
    parser.add_argument("--indices", default="")
    args = parser.parse_args()
    selected = {int(n) for n in args.indices.split(",") if n}
    original = _V576PacketEventDecoder.feed_events
    count = 0
    slow = []
    gc_start = [0.]
    gc_total = [0.]
    gc_records = []
    def gc_callback(phase, info):
        if phase == "start":
            gc_start[0] = time.perf_counter()
        else:
            duration = time.perf_counter() - gc_start[0]
            gc_total[0] += duration
            if duration >= .01:
                gc_records.append(dict(info, duration_ms=duration*1000, packet_index=count))
    def feed(self, packet):
        nonlocal count
        count += 1
        before_gc = gc_total[0]
        profile = cProfile.Profile() if count in selected else None
        wall, cpu = time.perf_counter(), time.thread_time()
        if profile:
            profile.enable()
        result = original(self, packet)
        if profile:
            profile.disable()
        wall_ms = (time.perf_counter()-wall)*1000
        cpu_ms = (time.thread_time()-cpu)*1000
        if wall_ms >= 30 or profile:
            row = {"index": count, "wall_ms": wall_ms, "thread_cpu_ms": cpu_ms,
                   "gc_ms": (gc_total[0]-before_gc)*1000,
                   "transport": packet.transport, "length": len(packet.payload),
                   "timestamp": packet.timestamp_seconds, "signature": packet.payload[5:9].hex()}
            if profile:
                stats = pstats.Stats(profile)
                rows = sorted(stats.stats.items(), key=lambda pair: pair[1][3], reverse=True)[:20]
                row["functions"] = [{"file": key[0], "line": key[1], "name": key[2],
                                    "calls": value[1], "self_s": value[2], "cumulative_s": value[3]}
                                   for key, value in rows]
            slow.append(row)
        return result
    started = time.perf_counter()
    _V576PacketEventDecoder.feed_events = feed
    gc.callbacks.append(gc_callback)
    source = None
    try:
        source = V576DiagnosticReplaySource(Path(args.capture), Path("dragon_nest_builder/data"))
        events = source.poll()
        result = {"capture": args.capture, "elapsed_s": time.perf_counter()-started,
                  "packets": count, "events": len(events), "gc_pauses": gc_records,
                  "slow_packets": sorted(slow, key=lambda r:r["wall_ms"], reverse=True)}
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output":args.output, "elapsed_s":result["elapsed_s"],
                          "slow_count":len(slow), "gc_pauses":gc_records,
                          "top":result["slow_packets"][:5]}, ensure_ascii=False), flush=True)
    finally:
        _V576PacketEventDecoder.feed_events = original
        gc.callbacks.remove(gc_callback)
        if source:
            source.close()

if __name__ == "__main__":
    main()
