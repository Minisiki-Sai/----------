"""Audit packet-observed healing-prohibition statuses in DPS captures."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.models import SkillParameterEvent
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.healing_exclusions import healing_block_intervals
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.event_platform import event_platform_for


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args()
    store = DataStore.load() if args.score else None
    for path in args.captures:
        source = V576DiagnosticReplaySource(path, DATA_DIR, {})
        try:
            events = source.poll()
        finally:
            source.close()
        statuses = [row for row in events if isinstance(row, SkillParameterEvent)]
        blocked = [row for row in statuses if row.effect_class_id == 275]
        aggregator = CombatAggregator()
        for event in events:
            aggregator.ingest(event)
        snapshot = aggregator.snapshot(include_events=True)
        party = {player.source_id for player in snapshot.players}
        intervals = healing_block_intervals(snapshot.skill_parameter_events, party)
        print(path.name, "events", len(events), "statuses", len(statuses), "heal_block", len(blocked))
        print("party", [(row.source_id, row.name, row.job) for row in snapshot.players])
        print("applied", [(target, len(rows), round(sum(end-start for start, end in rows), 3), rows[:5]) for target, rows in intervals.items()])
        platform = event_platform_for(snapshot)
        healed_while_blocked = [row for row in platform.event_index.healing_events
                                if any(start <= row.timestamp_ms/1000 < end
                                       for start, end in intervals.get(row.target_id, ()))]
        deaths_while_blocked = [row for row in platform.ordered_player_lifecycle_events
                                if not row.active and any(start <= row.timestamp_ms/1000 < end
                                       for start, end in intervals.get(row.target_id, ()))]
        revives_while_blocked = [row for row in platform.ordered_player_lifecycle_events
                                 if row.active and any(start <= row.timestamp_ms/1000 < end
                                        for start, end in intervals.get(row.target_id, ()))]
        print("healed_while_blocked", len(healed_while_blocked),
              [(round(row.timestamp_ms/1000,3), row.target_id, row.skill_name, row.amount)
               for row in healed_while_blocked[:20]])
        print("deaths_while_blocked", len(deaths_while_blocked),
              [(round(row.timestamp_ms/1000,3), row.target_id) for row in deaths_while_blocked[:20]])
        print("revives_while_blocked", len(revives_while_blocked),
              [(round(row.timestamp_ms/1000,3), row.target_id) for row in revives_while_blocked[:20]])
        print("classes", Counter(row.effect_class_id for row in statuses).most_common(20))
        print("skill_ids", Counter((row.skill_id, row.raw_skill_id, row.skill_name, row.effect_code, row.duration_ms) for row in blocked).most_common(25))
        print("target_ids", Counter(row.target_id for row in blocked).most_common(20))
        print("sample", [(round(row.timestamp_ms/1000, 3), row.source_id, row.target_id, row.skill_id, row.raw_skill_id, row.effect_code, row.parameters, row.duration_ms, row.effect_instance_id) for row in blocked[:30]])
        if store is not None:
            for player in snapshot.players:
                if player.job not in {"圣徒", "雷神", "曜", "药剂师"}:
                    continue
                windows = ((0.0, snapshot.elapsed_seconds),)
                with patch('dragon_nest_builder.live_dps.healing_exclusions.healing_block_intervals', return_value={}):
                    baseline = TeamScoreEngine(store)._healing_completion(snapshot, player, player.job, windows)
                current = TeamScoreEngine(store)._healing_completion(snapshot, player, player.job, windows)
                print("score", player.name, player.job,
                      "baseline", (round(baseline.score, 3), baseline.rescue_detail['excluded_heal_block_count']),
                      "current", (round(current.score, 3), current.rescue_detail['excluded_heal_block_count']))


if __name__ == "__main__":
    main()
