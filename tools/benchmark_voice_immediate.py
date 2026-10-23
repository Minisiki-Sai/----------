"""Ordered real-capture replay: measure processing cost and old aggregation delay.

Does not measure Npcap, HTTP, WebView scheduling or audible speech startup.
"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.boss_action_v2 import BossStructuredEventParser
from dragon_nest_builder.live_dps.voice_signals import VoiceSignalFeed


def quantiles(values):
    values = sorted(values)
    if not values:
        return {}
    return {"count": len(values), "p50": values[int((len(values)-1)*.5)],
            "p95": values[int((len(values)-1)*.95)], "max": values[-1]}


def run(path):
    fast, old, costs, publish_costs = [], [], [], []
    current_packet = [0.]
    packet_started = [0.]
    virtual_end = [0.]
    virtual_wait = [0.]
    queue_waits = []
    timestamp_regressions = [0]
    latest_timestamp = [0.]
    original_feed = _V576PacketEventDecoder.feed_events
    original_observe = BossStructuredEventParser.observe
    original_publish = VoiceSignalFeed.publish
    def publish(self, row):
        before = self._sequence
        started = time.perf_counter()
        original_publish(self, row)
        publish_costs.append((time.perf_counter()-started)*1000)
        if self._sequence != before:
            fast.append(dict(row, packet=current_packet[0],
                             packet_to_publish_ms=(time.perf_counter()-packet_started[0])*1000,
                             simulated_queue_wait_ms=virtual_wait[0]))
    def feed(self, packet):
        current_packet[0] = packet.timestamp_seconds
        if packet.timestamp_seconds < latest_timestamp[0] - .001:
            timestamp_regressions[0] += 1
        latest_timestamp[0] = max(latest_timestamp[0], packet.timestamp_seconds)
        started = time.perf_counter()
        packet_started[0] = started
        virtual_wait[0] = max(0., virtual_end[0] - packet.timestamp_seconds)*1000
        queue_waits.append(virtual_wait[0])
        result = original_feed(self, packet)
        duration = time.perf_counter()-started
        costs.append(duration*1000)
        virtual_end[0] = max(virtual_end[0], packet.timestamp_seconds) + duration
        return result
    def observe(self, evidence, now_ms):
        result = original_observe(self, evidence, now_ms)
        for row in result:
            if row.skill_index:
                old.append((row.source_id, row.skill_index, now_ms, row.affected_target_ids))
        return result
    _V576PacketEventDecoder.feed_events = feed
    BossStructuredEventParser.observe = observe
    VoiceSignalFeed.publish = publish
    start = time.perf_counter()
    source = None
    try:
        source = V576DiagnosticReplaySource(Path(path), Path("dragon_nest_builder/data"))
        source._decoder.voice_signal_feed = VoiceSignalFeed()
        events = source.poll()
        delays = []
        for row in fast:
            recipients = {p["id"] for p in row.get("recipients", [])}
            candidates = [stamp-row["timestamp"] for actor, skill, stamp, targets in old
                          if actor == row["source"] and skill == row["skill"]
                          and recipients.issubset(targets) and 0 <= stamp-row["timestamp"] <= 15000]
            if candidates:
                delays.append(min(candidates))
        if timestamp_regressions[0]:
            # Replay may append recovered historical packets. They are not
            # fresh real-time arrivals and invalidate a queue simulation.
            for row in fast:
                row.pop("simulated_queue_wait_ms", None)
        print(json.dumps({"file": str(path), "replay_seconds": time.perf_counter()-start,
                          "events": len(events), "decode_packet_ms": quantiles(costs),
                          "signal_publish_ms": quantiles(publish_costs),
                          "timestamp_regressions": timestamp_regressions[0],
                          "simulated_queue_wait_ms": None if timestamp_regressions[0] else quantiles(queue_waits),
                          "old_aggregation_wait_ms": quantiles(delays),
                          "immediate_signals": fast}, ensure_ascii=False), flush=True)
    finally:
        if source:
            source.close()
        _V576PacketEventDecoder.feed_events = original_feed
        BossStructuredEventParser.observe = original_observe
        VoiceSignalFeed.publish = original_publish


for path in sys.argv[1:]:
    run(path)
