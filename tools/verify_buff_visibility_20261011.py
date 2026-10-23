"""Replay once; isolate old visibility cleanup in a diagnostic aggregator only."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.aura_state import AURA_SKILL_KEYS
from dragon_nest_builder.live_dps.models import CombatEvent, PlayerLifecycleEvent
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


def legacy_visibility_aggregator():
    agg = CombatAggregator()
    original_ingest = agg.ingest

    def close(self, target_id, timestamp_ms, *, visibility_only=False):
        changed = False
        for track in self._player_buffs.values():
            if track.target_id != target_id:
                continue
            if visibility_only and track.skill_id in AURA_SKILL_KEYS and track.effect_instance_id > 0:
                continue
            for interval in track.intervals:
                if interval[0] <= timestamp_ms < interval[1]:
                    interval[1] = timestamp_ms
                    changed = True
        if changed:
            self._overlay_player_buff_state_revision += 1
        return changed

    agg._close_player_buffs_at = MethodType(close, agg)

    def ingest(event):
        previous = (agg._player_lifecycle_active_by_target.get(event.target_id, True)
                    if isinstance(event, PlayerLifecycleEvent) else None)
        original_ingest(event)
        if isinstance(event, PlayerLifecycleEvent) and not event.active and previous:
            agg._player_buff_boundaries_by_target[event.target_id].append((event.timestamp_ms, "visibility"))
            agg._close_player_buffs_at(event.target_id, event.timestamp_ms, visibility_only=True)
            agg._summon_presence.remove_owner(event.target_id)

    agg.ingest = ingest
    return agg


def digest(events):
    h = sha256()
    count = 0
    for event in events:
        if isinstance(event, CombatEvent):
            h.update(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True).encode())
            count += 1
    return count, h.hexdigest()


def audit(path):
    print("Replay: " + path.name, flush=True)
    source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
    try:
        events = source.poll()
    finally:
        source.close()
    before = legacy_visibility_aggregator()
    after = CombatAggregator()
    for event in events:
        before.ingest(event)
        after.ingest(event)
    before_count, before_hash = digest(before._combat_events)
    after_count, after_hash = digest(after._combat_events)
    bs, fs = before.snapshot(), after.snapshot()
    assert (before_count, before_hash, bs.total_damage, bs.total_healing) == (
        after_count, after_hash, fs.total_damage, fs.total_healing)
    assert bs.player_buff_clear_events == fs.player_buff_clear_events
    sample = []
    wanted = {"无情打击", "鸡尾酒", "保护之盾", "光明之呼吸", "审判力量", "鹰怒"}
    # Compare real received intervals, not manufactured global team coverage.
    for event in events:
        if not isinstance(event, PlayerLifecycleEvent) or event.active:
            continue
        at = event.timestamp_ms + 100
        def active_rows(agg):
            return {r["skill_name"]: r["end_seconds"]
                    for r in agg._overlay_effect_rows(agg._player_buffs, at,
                        include_expired_latest=True, preserve_parameters=True)
                    if r["target_id"] == event.target_id and r["active"] and r["skill_name"] in wanted}
        old, fixed = active_rows(before), active_rows(after)
        retained = {name: end for name, end in fixed.items() if name not in old}
        if retained:
            sample.append(dict(t=event.timestamp_ms/1000, target=event.target_id,
                               name=event.target_name, job=event.target_job,
                               retained_received_buffs=retained))
    result = dict(capture=path.name, comparison="only legacy visibility cleanup is restored",
                  combat_count=after_count, combat_sha256=after_hash,
                  damage_before=bs.total_damage, damage_after=fs.total_damage,
                  damage_unchanged=True, clear_event_count=len(fs.player_buff_clear_events),
                  explicit_clear_events_unchanged=True, sample=sample)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = [audit(path) for path in args.captures]
    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
