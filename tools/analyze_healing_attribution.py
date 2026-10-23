from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.healing_attribution import (
    UNKNOWN_HEALING_SOURCE_ID,
)


def analyze_capture(path: Path) -> dict[str, object]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    decode_started = time.perf_counter()
    try:
        events = source.poll()
        decode_seconds = time.perf_counter() - decode_started
        aggregate_started = time.perf_counter()
        for event in events:
            aggregator.ingest(event)
        aggregate_seconds = time.perf_counter() - aggregate_started
    finally:
        source.close()
    snapshot_started = time.perf_counter()
    snapshot = aggregator.snapshot(include_events=True)
    snapshot_seconds = time.perf_counter() - snapshot_started

    by_skill: dict[tuple[str, str, str], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0]
    )
    by_target: dict[tuple[str, str], int] = defaultdict(int)
    for event in snapshot.combat_events:
        if not event.healing:
            continue
        skill_key = (
            event.source_id,
            event.source_name,
            event.skill_name,
        )
        by_skill[skill_key][0] += event.amount
        by_skill[skill_key][1] += 1
        by_skill[skill_key][2] += event.attribution_confidence
        by_target[(event.target_id, event.target_name)] += event.amount

    skill_rows = []
    for (source_id, source_name, skill_name), values in sorted(
        by_skill.items(),
        key=lambda row: row[1][0],
        reverse=True,
    ):
        amount, count, confidence_total = values
        skill_rows.append(
            {
                "source_id": source_id,
                "source_name": source_name,
                "skill_name": skill_name,
                "amount": round(amount),
                "event_count": round(count),
                "average_confidence": round(
                    confidence_total / max(1.0, count), 4
                ),
                "unattributed": source_id == UNKNOWN_HEALING_SOURCE_ID,
            }
        )

    factual_total = int(snapshot.factual_healing_total)
    attributed_total = int(snapshot.attributed_healing_total)
    unattributed_total = int(snapshot.unattributed_healing_total)
    return {
        "capture": str(path.resolve()),
        "decode_seconds": round(decode_seconds, 4),
        "aggregate_seconds": round(aggregate_seconds, 4),
        "snapshot_seconds": round(snapshot_seconds, 4),
        "elapsed_seconds": snapshot.elapsed_seconds,
        "raw_packet_total": snapshot.raw_healing_total,
        "factual_healing_total": factual_total,
        "attributed_healing_total": attributed_total,
        "unattributed_healing_total": unattributed_total,
        "suppressed_overheal_total": snapshot.suppressed_overheal_total,
        "balanced": factual_total == attributed_total + unattributed_total,
        "matched_event_count": snapshot.healing_matched_event_count,
        "unattributed_event_count": snapshot.healing_unattributed_event_count,
        "ambiguous_event_count": snapshot.healing_ambiguous_event_count,
        "methods": dict(snapshot.healing_attribution_methods),
        "skills": skill_rows,
        "targets": [
            {
                "target_id": target_id,
                "target_name": target_name,
                "amount": amount,
            }
            for (target_id, target_name), amount in sorted(
                by_target.items(), key=lambda row: row[1], reverse=True
            )
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="审计独立 HealingAttributionLedger 的金额守恒和技能归因"
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    payload = [analyze_capture(path) for path in args.captures]
    print(
        json.dumps(
            payload[0] if len(payload) == 1 else payload,
            ensure_ascii=False,
            indent=None if args.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
