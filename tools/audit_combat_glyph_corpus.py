"""Read-only corpus audit for cooldown and action-speed skill-glyph evidence."""

from __future__ import annotations

import gc
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.combat_glyph_inference import (
    _candidate_rows, _event_matches, infer_combat_skill_glyphs,
)
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, data_job_for
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.timeline_evidence import is_authoritative_timeline_cast


def audit_capture(path: Path, store: DataStore) -> None:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    try:
        for batch in source.iter_batches():
            for event in batch:
                aggregator.ingest(event)
    finally:
        source.close()
    snapshot = aggregator.snapshot(include_events=True)
    inferred, _diagnostics = infer_combat_skill_glyphs(snapshot, store)
    casts_by_source = defaultdict(list)
    parameters_by_source = defaultdict(list)
    for event in snapshot.skill_cast_events:
        if is_authoritative_timeline_cast(event):
            casts_by_source[event.source_id].append(event)
    for event in snapshot.skill_parameter_events:
        parameters_by_source[event.source_id].append(event)
    print(f"CAPTURE\t{path.name}\tplayers={len(snapshot.players)}\tcasts={len(snapshot.skill_cast_events)}\tparameters={len(snapshot.skill_parameter_events)}", flush=True)
    for player in snapshot.players:
        source_id = player.source_id
        candidates = _candidate_rows(store, player.job)
        selected = inferred.get(source_id, [])
        speed_candidates = {}
        cd_candidates = {}
        for candidate in candidates:
            key = candidate['skill_key']
            if candidate['effect'] == '动作速度增加':
                if key not in speed_candidates or candidate['value'] > speed_candidates[key]['value']:
                    speed_candidates[key] = candidate
            if candidate['effect'] == '冷却缩减':
                if key not in cd_candidates or candidate['value'] > cd_candidates[key]['value']:
                    cd_candidates[key] = candidate
        speed_found = [row for row in selected if row['effect'] == '动作速度增加']
        speed_cast_unproven = []
        for candidate in speed_candidates.values():
            cast_count = sum(_event_matches(candidate, event) for event in casts_by_source[source_id])
            if cast_count >= 2 and not any(row['skill_key'] == candidate['skill_key'] for row in speed_found if 'skill_key' in row):
                if not any(row['skill'] == candidate['skill'] for row in speed_found):
                    speed_cast_unproven.append(f"{candidate['skill']}:{cast_count}")
        if speed_found or speed_cast_unproven:
            chakra = Counter((event.skill_level_row_id, event.effect_class_id, event.parameters)
                for event in parameters_by_source[source_id]
                if event.skill_id == '6302' and event.effect_class_id == 25)
            print('SPEED\t' + path.name + '\t' + player.job + '\t' + player.name +
                  '\tproved=' + ','.join(f"{row['skill']}:{row['value']}%:{row['evidence_count']}" for row in speed_found) +
                  '\tcast_unproven=' + ','.join(speed_cast_unproven) +
                  ('\tchakra_class25=' + str(chakra.most_common(5)) if chakra else ''), flush=True)
        modifiers = TeamScoreEngine._combat_modifier_windows(snapshot, source_id)
        for candidate in cd_candidates.values():
            base = store.skill_reference_cooldown(data_job_for(player.job), candidate['skill'], 0.0)
            if base <= 1:
                continue
            times = sorted({event.timestamp_ms / 1000 for event in casts_by_source[source_id]
                            if _event_matches(candidate, event)})
            if len(times) < 3:
                continue
            impossible_no = near_high = high_violation = 0
            best = []
            for start, end in zip(times, times[1:]):
                horizon = max(snapshot.elapsed_seconds, end + base * 2)
                ready_no = TeamScoreEngine._dynamic_cooldown_ready_time(start, base, modifiers, horizon)
                ready_high = TeamScoreEngine._dynamic_cooldown_ready_time(
                    start, base * (1 - candidate['value'] / 100), modifiers, horizon)
                margin = max(0.75, min(1.25, base * 0.015))
                if end < ready_no - margin:
                    impossible_no += 1
                    if abs(end - ready_high) <= margin:
                        near_high += 1
                        best.append((round(start, 3), round(end, 3), round(ready_no, 3), round(ready_high, 3)))
                if end < ready_high - margin:
                    high_violation += 1
            if impossible_no >= 2 or near_high >= 2:
                already = any(row['skill'] == candidate['skill'] and row['effect'] == '冷却缩减' for row in selected)
                other_glyph = [(row['effect'], row['value']) for row in selected
                               if row['skill'] == candidate['skill'] and row['effect'] != '冷却缩减']
                print('COOLDOWN\t' + path.name + '\t' + player.job + '\t' + player.name +
                      '\t' + candidate['skill'] + f"\tbase={base:g}\thigh={candidate['value']:g}%" +
                      f"\tcasts={len(times)}\tno_plate_early={impossible_no}\tnear_high={near_high}" +
                      f"\thigh_early={high_violation}\talready_inferred={already}" +
                      f"\tother_glyph={other_glyph}\texample={best[:2]}", flush=True)
    del snapshot, aggregator, source
    gc.collect()


def main() -> None:
    store = DataStore.load()
    for raw in sys.argv[1:]:
        audit_capture(Path(raw), store)


if __name__ == '__main__':
    main()
