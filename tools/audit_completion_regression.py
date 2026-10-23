"""Replay actual completion evidence; never infer clear from a file name/HP."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.settlement import SettlementEvent, SettlementLedger


def audit(path: Path, known: dict) -> dict:
    start = time.perf_counter()
    source = V576DiagnosticReplaySource(path, DATA_DIR, known)
    try:
        events = source.poll()
    finally:
        source.close()
    before, after = SettlementLedger(), SettlementLedger()
    counts = Counter()
    damage = 0
    end = 0
    rewards = []
    for event in events:
        end = max(end, int(getattr(event, "timestamp_ms", 0)))
        if isinstance(event, SettlementEvent):
            counts[event.source] += 1
            after.observe(event)
            if event.source != "s2c-tcp-verified-clear-reward-v576":
                before.observe(event)
            else:
                rewards.append({"time_ms": event.timestamp_ms, "map": event.trigger_map_id,
                                "boss": event.trigger_boss_id, "trigger": event.trigger_name})
        elif isinstance(event, CombatEvent) and not event.healing and event.amount:
            damage += event.amount
            if event.target_job.strip().partition(" ")[0].casefold() == "boss":
                for ledger in (before, after):
                    ledger.observe_boss_damage(event.timestamp_ms, event.source_id, event.target_id)
    # This is the decoded event sum BEFORE the aggregator's physical-record
    # deduplication; do not compare it with ACT's final/raw-ledger totals.
    return {"capture": path.name, "events": len(events), "event_damage_sum": damage,
            "seconds": round(time.perf_counter() - start, 3),
            "before_reward_fix": before.snapshot(current_timestamp_ms=end),
            "after": after.snapshot(current_timestamp_ms=end),
            "evidence_counts": dict(counts), "reward_evidence": rewards}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    known = ClientProfile.load(DATA_DIR / "live_dps_profiles/v576.json").known_players
    rows = []
    for path in args.captures:
        row = audit(path.resolve(), known)
        rows.append(row)
        print(json.dumps({"capture": row["capture"], "seconds": row["seconds"],
                          "before": row["before_reward_fix"]["status"],
                          "after": row["after"]["status"],
                          "reason": row["after"]["reason"]}, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
