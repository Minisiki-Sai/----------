"""Offline production replay audit; prints results, never edits capture files."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.aura_state import AURA_SKILL_KEYS
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import BossStatusEvent, CombatEvent
from dragon_nest_builder.live_dps.v576 import core_decoder


def audit(path: Path, without_ends: bool = False) -> dict:
    timings = []
    original = core_decoder.decode_aura_instance_ends

    def measured(payload, direction):
        start = perf_counter()
        result = original(payload, direction)
        timings.append(perf_counter() - start)
        return () if without_ends else result

    core_decoder.decode_aura_instance_ends = measured
    start = perf_counter()
    source = V576DiagnosticReplaySource(path, Path(__file__).resolve().parents[1] / "dragon_nest_builder/data")
    try:
        events = source.poll()
        replay_seconds = perf_counter() - start
    finally:
        source.close()
        core_decoder.decode_aura_instance_ends = original
    digest = sha256()
    count = damage = healing = 0
    for event in events:
        if isinstance(event, CombatEvent):
            digest.update(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True).encode())
            count += 1
            if event.healing:
                healing += event.amount
            else:
                damage += event.amount
    auras = [e for e in events if isinstance(e, BossStatusEvent) and e.skill_id in AURA_SKILL_KEYS]
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    owner = "0x5054AEFC"
    owner_rows = [asdict(r) for r in snapshot.player_buff_series if r.target_id == owner and r.skill_id == "3213"]
    return {
        "capture": str(path), "without_instance_ends": without_ends,
        "replay_seconds": round(replay_seconds, 3), "event_count": len(events),
        "combat_count": count, "damage_sum": damage, "healing_sum": healing,
        "combat_sha256": digest.hexdigest(),
        "aura_operations": {str(k): v for k, v in Counter((e.skill_id, e.operation, e.target_scope) for e in auras).items()},
        "cast_inferred_aura_count": sum(e.effect_code == "cast_inferred_permanent" for e in auras),
        "end_decoder_calls": len(timings), "end_decoder_total_ms": round(sum(timings) * 1000, 3),
        "end_decoder_mean_us": round(sum(timings) * 1e6 / max(1, len(timings)), 3),
        "end_decoder_max_ms": round(max(timings, default=0) * 1000, 3),
        "BlueMoon_own_judgment_rows": owner_rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--without-ends", action="store_true")
    args = parser.parse_args()
    for path in args.captures:
        print(json.dumps(audit(path, args.without_ends), ensure_ascii=False), flush=True)
