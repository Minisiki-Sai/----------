"""Inspect factual HP collapse windows and healer duties in a capture."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.healer_rescue import audit_danger_rescue, rescue_episode_penalty
from dragon_nest_builder.live_dps.healing_exclusions import fatal_burst_intervals, healing_block_intervals
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.event_platform import event_platform_for


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    args = parser.parse_args()
    store = DataStore.load()
    for path in args.captures:
        source = V576DiagnosticReplaySource(path, DATA_DIR, {})
        try:
            events = source.poll()
        finally:
            source.close()
        aggregator = CombatAggregator()
        for event in events:
            aggregator.ingest(event)
        snapshot = aggregator.snapshot(include_events=True)
        platform = event_platform_for(snapshot)
        print(f"CAPTURE {path.name} events={len(events)} duration={snapshot.elapsed_seconds:.3f}s")
        print("PLAYERS", [(p.source_id, p.name, p.job) for p in snapshot.players])
        print("HEAL_EVENTS", len(platform.event_index.healing_events),
              Counter((e.source_id, e.skill_name) for e in platform.event_index.healing_events).most_common(25))
        print("HEAL_RANGE", [(source, min(e.timestamp_ms for e in platform.event_index.healing_events if e.source_id == source) / 1000,
                              max(e.timestamp_ms for e in platform.event_index.healing_events if e.source_id == source) / 1000)
                             for source in {e.source_id for e in platform.event_index.healing_events}][:12])
        print("CASTS", Counter((e.source_id, e.skill_name, e.packet_kind)
                               for e in snapshot.skill_cast_events).most_common(30))
        print("LIFECYCLE_DEATHS", [(e.target_name, e.target_id, e.timestamp_ms / 1000)
                                   for e in snapshot.player_lifecycle_events if not e.active][:60])
        print("INCOMING", len(platform.event_index.incoming_damage_events),
              "RAW_LOSS", sum(e.attribution_method == "incoming_hp_state"
                              for e in platform.event_index.incoming_damage_events))
        for track in snapshot.player_health_tracks:
            if not track.points or track.max_hp <= 0:
                continue
            points = sorted(track.points, key=lambda point: point.timestamp_seconds)
            print("TRACK", track.target_name, "points", len(points),
                  "range", (points[0].timestamp_seconds, points[-1].timestamp_seconds))
            fatal = []
            for index, point in enumerate(points):
                if point.current_hp > 0:
                    continue
                candidates = [previous for previous in points[:index]
                              if 0 < point.timestamp_seconds - previous.timestamp_seconds <= 1.0
                              and previous.current_hp > 0]
                if candidates:
                    first = max(candidates, key=lambda previous: previous.current_hp)
                    drop = (first.current_hp - point.current_hp) / track.max_hp
                    if drop >= 0.5:
                        fatal.append((round(first.timestamp_seconds, 3),
                                      round(point.timestamp_seconds, 3),
                                      round(drop * 100, 1), len(candidates)))
            if fatal:
                print("FATAL", track.target_name, track.target_id, track.state_source,
                      "exact", track.exact_percent, "max_hp", track.max_hp, fatal[:40])
        party = {p.source_id for p in snapshot.players}
        blocked = healing_block_intervals(snapshot.skill_parameter_events, party)
        burst = fatal_burst_intervals(snapshot.player_health_tracks,
                                      snapshot.player_lifecycle_events,
                                      snapshot.incoming_damage_events)
        print("HEAL_BLOCK", [(target, len(rows), round(sum(b-a for a,b in rows), 3))
                             for target, rows in blocked.items()])
        print("FATAL_BURST", burst)
        role_engine = TeamScoreEngine(store)
        healers = {p.source_id for p in snapshot.players
                   if ('圣徒' in p.job or '雷神' in p.job or '曜' in p.job
                       or ('药剂' in p.job and role_engine._alchemist_used_sacrifice(
                           snapshot, p.source_id)))}
        end_seconds = max(snapshot.elapsed_seconds,
                          *(track.points[-1].timestamp_seconds
                            for track in snapshot.player_health_tracks if track.points))
        rescue = audit_danger_rescue(snapshot.player_health_tracks,
                                     platform.event_index.healing_events,
                                     ((0.0, end_seconds),),
                                     party_ids=party, healer_ids=healers,
                                     danger_ratio=0.3,
                                     lifecycle=snapshot.player_lifecycle_events,
                                     incoming=snapshot.incoming_damage_events,
                                     heal_block_intervals=blocked,
                                     fatal_burst_windows=burst)
        for row in rescue.episodes:
            if row.end_reason == "death":
                print("DEATH_EPISODE", row.target_id,
                      round(row.start_seconds, 3), round(row.end_seconds, 3),
                      round(row.needed_percent, 2), row.estimated_hp)
        for player in snapshot.players:
            if player.source_id not in healers:
                continue
            with patch('dragon_nest_builder.live_dps.healing_exclusions.healing_block_intervals',
                       return_value={}), patch(
                       'dragon_nest_builder.live_dps.healing_exclusions.fatal_burst_intervals',
                       return_value={}):
                baseline = TeamScoreEngine(store)._healing_completion(
                    snapshot, player, player.job, ((0.0, end_seconds),))
            with patch('dragon_nest_builder.live_dps.healing_exclusions.fatal_burst_intervals',
                       return_value={}):
                block_only = TeamScoreEngine(store)._healing_completion(
                    snapshot, player, player.job, ((0.0, end_seconds),))
            result = TeamScoreEngine(store)._healing_completion(
                snapshot, player, player.job, ((0.0, end_seconds),))
            print("HEALER", player.name, player.job,
                  "support_alchemist", TeamScoreEngine(store)._alchemist_used_sacrifice(snapshot, player.source_id)
                  if '药剂' in player.job else None,
                  "effective", result.effective_realization,
                  "rescue", result.rescue_completion,
                  "score", result.score,
                  "rescue_penalty", result.rescue_detail["penalty"],
                  "excluded_short_fatal", result.rescue_detail["excluded_short_fatal_count"],
                  "evaluated_casts", result.evaluated_casts,
                  "meaningful_casts", result.meaningful_casts)
            print("COMPARISON", player.name,
                  "baseline", (round(baseline.effective_realization, 3), round(baseline.score, 3)),
                  "block_only", (round(block_only.effective_realization, 3), round(block_only.score, 3)),
                  "block_and_fatal", (round(result.effective_realization, 3), round(result.score, 3)),
                  "excluded_block", result.rescue_detail['excluded_heal_block_count'],
                  "excluded_fatal", result.rescue_detail['excluded_fatal_burst_count'])
            if player.job == '圣徒':
                casts = [event for event in snapshot.skill_cast_events
                         if event.source_id == player.source_id
                         and any(marker in event.skill_name for marker in ('治愈','痊愈'))
                         and event.packet_kind in ('skill_action','skill_cast')]
                heals = [event for event in platform.event_index.healing_events
                         if event.source_id == player.source_id]
                for cast_event in casts:
                    nearby = [(round((event.timestamp_ms - cast_event.timestamp_ms) / 1000, 3),
                               event.skill_name, event.amount, event.target_name)
                              for event in heals
                              if -0.3 <= (event.timestamp_ms - cast_event.timestamp_ms) / 1000 <= 8]
                    if not nearby:
                        deaths = [(event.target_name, round((event.timestamp_ms - cast_event.timestamp_ms) / 1000, 3))
                                  for event in snapshot.player_lifecycle_events
                                  if not event.active and -2 <= (event.timestamp_ms - cast_event.timestamp_ms) / 1000 <= 2]
                        print('NO_HEAL_NEAR_CAST', round(cast_event.timestamp_ms / 1000, 3),
                              cast_event.skill_name, deaths)
        for healer in healers:
            penalty = rescue_episode_penalty(rescue, healer, len(healers))
            print("RESCUE_DIAGNOSTIC", healer, penalty["scored_episode_count"],
                  penalty["excluded_short_fatal_count"], penalty["penalty"])


if __name__ == "__main__":
    main()
