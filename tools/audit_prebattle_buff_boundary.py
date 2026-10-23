"""Read-only real-capture comparison of full and idle state observers."""
import argparse
from collections import Counter
from pathlib import Path
import sys
import time
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import V576TransportReassembler, decode_captured_frame
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger
from dragon_nest_builder.live_dps.models import BossStatusEvent
from dragon_nest_builder.live_dps.prebattle_state import PrebattleStateTracker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    full = _V576PacketEventDecoder(ROOT / "dragon_nest_builder" / "data")
    idle = _V576PacketEventDecoder(ROOT / "dragon_nest_builder" / "data")
    trackers = [PrebattleStateTracker(), PrebattleStateTracker()]
    totals = [Counter(), Counter()]
    costs = [0., 0.]
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    origin = None
    boundary = None
    count = 0
    samples = []
    for timestamp, adapter, length, frame in read_trace(args.capture):
        if adapter >= 250:
            continue
        packet = decode_captured_frame(timestamp, frame, length)
        if packet is None:
            continue
        if origin is None:
            origin = timestamp
        if timestamp > origin + args.seconds:
            break
        boundary = timestamp
        for logical in transport.feed(packet):
            if not ledger.accept(logical):
                continue
            count += 1
            packet_counts = []
            for i, observer in enumerate((full.feed_events, idle.feed_prebattle_events)):
                start = time.perf_counter()
                events = observer(logical)
                trackers[i].observe(events, logical.timestamp_seconds)
                costs[i] += time.perf_counter() - start
                totals[i].update(event.skill_name for event in events if isinstance(event, BossStatusEvent) and event.target_scope == "player")
                packet_counts.append(sum(isinstance(event, BossStatusEvent) and event.target_scope == "player" for event in events))
            if packet_counts[0] != packet_counts[1] and len(samples) < 8:
                samples.append(dict(time=logical.timestamp_seconds-origin, transport=logical.transport, length=len(logical.payload), prefix=logical.payload[:20].hex(), counts=packet_counts))
    states = [tracker.snapshot(boundary or 0) for tracker in trackers]
    keys = [set((row["target_id"], row["source_id"], row["skill_id"], row["effect_key"], row["duration_ms"]) for row in state["player_buffs"]) for state in states]
    print(json.dumps(dict(capture=args.capture.name, seconds=args.seconds, logical_packets=count,
        differing_packets=samples,
        parser_seconds=costs, buff_event_count=[sum(rows.values()) for rows in totals], active_buffs=[len(key) for key in keys],
        missing_idle_states=list(keys[0] - keys[1]), unexpected_idle_states=list(keys[1] - keys[0])), ensure_ascii=False))

if __name__ == "__main__":
    main()
