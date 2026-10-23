"""Read-only full replay A/B of the aura recognition repair.

Legacy behavior is reconstructed ONLY in this diagnostic process. Captures,
production sources, installed apps and cloud scores are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import inspect
import json
from pathlib import Path
import sys
import textwrap
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import prebattle_state, protocol_v576, replay_normalization
from dragon_nest_builder.live_dps import score_support_counterfactual as support
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.aura_state import AURA_SKILL_KEYS
from dragon_nest_builder.live_dps.models import BossStatusEvent, CombatEvent
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, DataStore, _coverage_in_windows
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.v576 import core_decoder, status_decoder


@contextmanager
def legacy_aura_behavior():
    """Undo all aura-specific recognition/projection changes, not just ends.

    Keeping the surrounding parser the same isolates this fix from unrelated
    releases. Strict source anchors fail closed if production code changes.
    Imported aliases keep working because only function code objects change.
    """
    undo = []

    def global_value(module, key, value):
        old = getattr(module, key)
        setattr(module, key, value)
        undo.append(lambda: setattr(module, key, old))

    def function_code(function, replacements):
        source = textwrap.dedent(inspect.getsource(function))
        for old, new in replacements:
            assert source.count(old) == 1, (function.__name__, old)
            source = source.replace(old, new)
        namespace = dict(function.__globals__)
        exec(compile(source, "<legacy-aura-diagnostic>", "exec"), namespace)
        old_code = function.__code__
        function.__code__ = namespace[function.__name__].__code__
        undo.append(lambda: setattr(function, "__code__", old_code))

    try:
        # Enables old cast fan-out/backfill, removes aura-only filtering and
        # instance/class fields; other permanent Buffs keep their original path.
        global_value(status_decoder, "AURA_SKILL_IDS", frozenset())
        global_value(core_decoder, "AURA_SKILL_IDS", frozenset())
        global_value(prebattle_state, "AURA_SKILL_KEYS", frozenset())
        global_value(core_decoder, "decode_aura_instance_ends", lambda *_: ())
        global_value(support, "is_aura_damage_series", lambda _: True)
        global_value(support, "is_inferred_aura_row", lambda _: False)
        import dragon_nest_builder.live_dps.aggregator as aggregate_module
        global_value(aggregate_module, "is_inferred_aura_row", lambda _: False)
        function_code(CombatAggregator._confirm_player_status_identity, [
            ("    if (not actor_id or actor_id in self._confirmed_player_ids", "    return\n    if (not actor_id or actor_id in self._confirmed_player_ids"),
        ])
        function_code(core_decoder.V576DecoderCoreMixin.feed_events, [
            ('''    if (self.boss_actor_id in (self.skill_actor_ids | self.local_actor_ids)
        and self.boss_actor_id not in self.monster_entities):
        self.boss_actor_id = None  # Clear stale guesses even on identity-only packets.
''', ""),
            ('''    for status in statuses:
        if (is_aura_application(status)
            and status.source_actor_id not in self.monster_entities
            and status.target_actor_id not in self.monster_entities):
            self.packet_aura_player_ids.update((status.source_actor_id, status.target_actor_id))
    player_actors |= self.packet_aura_player_ids - self.monster_entities.keys()
    if self.boss_actor_id in player_actors:
        # A provisional status-only target must not override proven players.
        self.boss_actor_id = None
''', ""),
            ('''            and status.skill_id not in AURA_SKILL_IDS
            and _summon_skill_key(_skill_display_name(self.skill_catalog, status.skill_id)) not in self.team_buff_skill_names
''', ""),
            ('''                "player"
                if status.target_actor_id in player_actors
                else "boss"
''', '''                "boss"
'''),
            ('''                else ""
            )
        )
        if not target_scope:''', '''                else "player"
                if status.target_actor_id in player_actors
                else ""
            )
        )
        if not target_scope:'''),
        ])
        function_code(protocol_v576.decode_v576_status_events, [
            ("            or skill_id in {2213, 2214, 3211, 3213}\n", ""),
        ])
        function_code(replay_normalization._supplement_player_buffs_from_casts, [
            ('''            "火焰光环",
            "寒冰光环",
            "元素光环",
            "判定光环",
''', ""),
        ])
        yield
    finally:
        for revert in reversed(undo):
            revert()


def replay(path):
    start = perf_counter()
    source = V576DiagnosticReplaySource(path, ROOT / "dragon_nest_builder/data")
    try:
        events = source.poll()
    finally:
        source.close()
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
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    operations = Counter((e.skill_id, e.operation, e.target_scope) for e in events
                         if isinstance(e, BossStatusEvent) and e.skill_id in AURA_SKILL_KEYS)
    return snapshot, {
        "seconds": round(perf_counter() - start, 3),
        "combat_count": count, "wire_decoded_damage": damage, "wire_decoded_healing": healing,
        "combat_sha256": digest.hexdigest(), "aggregated_total_damage": snapshot.total_damage,
        "aura_operations": {str(k): v for k, v in operations.items()},
        "cast_inferred_aura_count": sum(isinstance(e, BossStatusEvent)
            and e.skill_id in AURA_SKILL_KEYS and e.effect_code == "cast_inferred_permanent" for e in events),
    }


def coverage(snapshot, windows, legacy):
    # Reuse the real scoring receive-interval construction (including magnitude
    # selection and mutually exclusive paladin aura grouping). No theory plan,
    # counterfactual DPS simulation or damage-weighted completion is involved.
    engine = TeamScoreEngine(DataStore.load())
    context = engine._build_support_counterfactual_context(snapshot, windows)
    players = {p.source_id: p for p in snapshot.players}
    allowed = {"圣骑互斥光环", "火焰光环", "寒冰光环"}
    result = {}
    for instance in context.effects:
        if instance.expectation.skill_key not in allowed:
            continue
        recipients = dict(instance.recipient_intervals)
        # Exact old/new scoring denominator policy. Also save a frozen-roster
        # metric to distinguish recognition changes from denominator changes.
        party = tuple(recipients) if legacy else tuple(players)
        per_player = {pid: _coverage_in_windows(recipients.get(pid, ()), windows)
                      for pid in players}
        actual = sum(_coverage_in_windows(recipients.get(pid, ()), windows)
                     for pid in party) / len(party) if party else 0.0
        source = players.get(instance.source_id)
        key = instance.source_id + ":" + instance.expectation.skill_key
        result[key] = {
            "provider_id": instance.source_id, "provider_name": source.name if source else "",
            "provider_job": source.job if source else "",
            "skill": instance.expectation.skill_key,
            "score_time_coverage_percent": actual * 100,
            "common_roster_time_coverage_percent": sum(per_player.values()) * 100 / max(1, len(players)),
            "recipient_denominator_count": len(party),
            "recipients": [{"id": pid, "name": players[pid].name, "job": players[pid].job,
                            "coverage_percent": cov * 100, "intervals": recipients.get(pid, ())}
                           for pid, cov in per_player.items()],
        }
    return result


def compare(path):
    print(json.dumps({"progress": path.name, "stage": "fixed replay"}, ensure_ascii=False), flush=True)
    fixed, fixed_meta = replay(path)
    windows = TeamScoreEngine._output_windows(fixed)
    fixed_rows = coverage(fixed, windows, False)
    print(json.dumps({"progress": path.name, "stage": "legacy replay", "fixed_seconds": fixed_meta["seconds"]}, ensure_ascii=False), flush=True)
    with legacy_aura_behavior():
        legacy, legacy_meta = replay(path)
        legacy_rows = coverage(legacy, windows, True)
    rows = []
    for key in sorted(set(fixed_rows) | set(legacy_rows)):
        before, after = legacy_rows.get(key), fixed_rows.get(key)
        rows.append({"key": key, "before": before, "after": after,
                     "change_percentage_points": (after["score_time_coverage_percent"] if after else 0)
                     - (before["score_time_coverage_percent"] if before else 0)})
    return {
        "capture": path.name, "capture_size_bytes": path.stat().st_size,
        "comparison": "reconstructed pre-aura-fix logic versus current fix; same surrounding parser",
        "denominator": "same fixed replay Boss effective-output windows in both arms; scoring equal-recipient time coverage",
        "effective_output_seconds": sum(b-a for a, b in windows), "effective_output_windows": windows,
        "before": legacy_meta, "after": fixed_meta,
        "combat_identical": legacy_meta["combat_sha256"] == fixed_meta["combat_sha256"],
        "roster": [{"id": p.source_id, "name": p.name, "job": p.job} for p in fixed.players],
        "coverage": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--check-patches", action="store_true")
    args = parser.parse_args()
    if args.check_patches:
        with legacy_aura_behavior():
            print("Legacy reconstruction anchors verified; production files unchanged.")
    else:
        for capture in args.captures:
            print(json.dumps(compare(capture), ensure_ascii=False), flush=True)
