"""Inspect Grudge Formation cooldown-glyph evidence in a replay."""

from pathlib import Path
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DataStore, DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.combat_glyph_inference import (
    _candidate_rows, _event_matches, _external_cdr_intervals, _cooldown_rows,
)
from dragon_nest_builder.live_dps.timeline_evidence import is_authoritative_timeline_cast
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine


def main(path: Path) -> None:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    try:
        for batch in source.iter_batches():
            for event in batch:
                aggregator.ingest(event)
    finally:
        source.close()
    snapshot = aggregator.snapshot(include_events=True)
    store = DataStore.load()
    for player in snapshot.players:
        if '噬魂' not in player.job:
            continue
        candidates = [row for row in _candidate_rows(store, player.job)
                      if '怨恨之像' in row['skill'] and row['effect'] == '冷却缩减']
        casts = tuple(row for row in snapshot.skill_cast_events if row.source_id == player.source_id)
        print('player', player.name, player.source_id, 'base_cd',
              store.skill_reference_cooldown(player.job, '怨恨之像', 0.0))
        print('candidates', candidates)
        print('cdr_intervals', _external_cdr_intervals(snapshot, player.source_id))
        modifiers = TeamScoreEngine._combat_modifier_windows(snapshot, player.source_id)
        print('modifier_windows', modifiers)
        parameters = [row for row in snapshot.skill_parameter_events
                      if row.source_id == player.source_id
                      and (row.skill_id == '5218' or row.raw_skill_id == '5218')]
        print('skill_parameters', len(parameters), [
            (row.timestamp_ms / 1000, row.effect_class_id, row.effect_code,
             row.parameters, row.duration_ms) for row in parameters[:12]])
        at_cast = [row for row in parameters if any(
            abs(row.timestamp_ms / 1000 - cast.timestamp_ms / 1000) < 0.35
            for cast in casts if cast.skill_id == '5218')]
        print('parameter_types_all', Counter(
            (row.effect_class_id, row.effect_code, row.parameters, row.duration_ms)
            for row in parameters).most_common(25))
        print('parameter_types_at_cast', Counter(
            (row.effect_class_id, row.effect_code, row.parameters, row.duration_ms)
            for row in at_cast).most_common(25))
        for candidate in candidates:
            matched = sorted({row.timestamp_ms/1000 for row in casts
                              if is_authoritative_timeline_cast(row) and _event_matches(candidate, row)})
            print('casts', candidate['skill_id'], matched)
            print('gaps', [round(right-left, 3) for left, right in zip(matched, matched[1:])])
        if candidates:
            base = store.skill_reference_cooldown(player.job, '怨恨之像', 0.0)
            for start, end in zip(matched, matched[1:]):
                horizon = max(snapshot.elapsed_seconds, end + base * 2)
                no_plate = TeamScoreEngine._dynamic_cooldown_ready_time(start, base, modifiers, horizon)
                high_plate = TeamScoreEngine._dynamic_cooldown_ready_time(start, base * 0.88, modifiers, horizon)
                print('ready_pair', round(start, 3), round(end, 3),
                      'no_plate', round(no_plate, 3), 'high_plate', round(high_plate, 3),
                      'observed_minus_no_plate', round(end - no_plate, 3),
                      'observed_minus_high_plate', round(end - high_plate, 3))
        print('inferred', _cooldown_rows(store, snapshot, player.source_id, player.job, candidates, casts))
        all_cd = {}
        for candidate in _candidate_rows(store, player.job):
            if candidate['effect'] != '冷却缩减':
                continue
            key = candidate['skill_key']
            if key not in all_cd or candidate['value'] > all_cd[key]['value']:
                all_cd[key] = candidate
        for candidate in all_cd.values():
            base = store.skill_reference_cooldown(player.job, candidate['skill'], 0.0)
            if base <= 1:
                continue
            times = sorted({cast.timestamp_ms/1000 for cast in casts
                            if is_authoritative_timeline_cast(cast) and _event_matches(candidate, cast)})
            if len(times) < 3:
                continue
            impossible_no = near_high = high_violation = 0
            for start, end in zip(times, times[1:]):
                horizon = max(snapshot.elapsed_seconds, end + base * 2)
                ready_no = TeamScoreEngine._dynamic_cooldown_ready_time(start, base, modifiers, horizon)
                ready_high = TeamScoreEngine._dynamic_cooldown_ready_time(
                    start, base * (1-candidate['value']/100), modifiers, horizon)
                if end < ready_no - 0.75:
                    impossible_no += 1
                    if abs(end-ready_high) <= 0.75:
                        near_high += 1
                if end < ready_high - 0.75:
                    high_violation += 1
            if impossible_no:
                print('cd_candidate', candidate['skill'], 'casts', len(times),
                      'impossible_no', impossible_no, 'near_high', near_high,
                      'high_violation', high_violation)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
