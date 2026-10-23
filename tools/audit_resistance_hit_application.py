from __future__ import annotations

import sys
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine, canonical_skill
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.live_dps.score_support_counterfactual import _hit_assumed_resistance_intervals
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def main(path: str) -> None:
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._queue_replay_score_sync = lambda *args, **kwargs: None
    replay = core.load_replay(path, lambda *args: None)
    snapshot = core._replays[replay["sourceHash"]]
    engine = TeamScoreEngine(store)
    boss_ids = frozenset(
        target_id for encounter in BossRecognizer().recognize_snapshot(snapshot)
        for target_id in encounter.target_ids
    )
    target_skills = {
        canonical_skill(name)
        for name in ("灵巧猎手", "地狱火海", "狂暴之爪", "灵魂之门")
    }
    casts = defaultdict(list)
    for row in replay.get("skill_cast_events", ()):
        if canonical_skill(row.get("skill_name", "")) in target_skills:
            casts[(row.get("source_id"), canonical_skill(row.get("skill_name", "")))].append(row)
    hits = defaultdict(list)
    for row in replay.get("combat_events", ()):
        if canonical_skill(row.get("skill_name", "")) in target_skills:
            hits[(row.get("source_id"), canonical_skill(row.get("skill_name", "")))].append(row)
    for key in sorted(set(casts) | set(hits)):
        rows = hits.get(key, ())
        real = [r for r in rows if r.get("has_real_hit_evidence") is True and r.get("amount", 0) > 0]
        print("SOURCE/SKILL", key, "casts", len(casts.get(key, ())), "hits", len(rows), "real", len(real))
        print("  cast samples", [(r.get("timestamp_ms"), r.get("skill_id")) for r in casts.get(key, ())[:5]])
        print("  hit targets", Counter((r.get("target_id"), r.get("target_job")) for r in real).most_common(8))
        print("  real samples", [(r.get("timestamp_ms"), r.get("skill_id"), r.get("target_id")) for r in real[:8]])
        job = next((r.get("job") for r in rows if r.get("job")), "")
        expected = [
            row for row in engine._support_expectations(job)
            if row.skill_key == key[1] and row.kind == "debuff"
        ]
        print("  expectations", [(r.skill_name, r.effect_key, r.duration) for r in expected])
        for row in expected:
            if row.effect_key.startswith("减") and row.effect_key.endswith("抗%"):
                intervals = _hit_assumed_resistance_intervals(
                    snapshot, key[0], row.skill_key, row.duration,
                    snapshot.elapsed_seconds, boss_ids or None,
                )
                print("  hit assumed", len(intervals), intervals[:4])
    print("ENCOUNTERS", [(r.get("name"), r.get("target_ids")) for r in replay.get("boss_encounters", ())])
    for skill in sorted(target_skills):
        effect_rows = [
            row for row in snapshot.boss_effect_events
            if canonical_skill(row.skill_name) == skill
        ]
        print("EFFECT COUNTS", skill, Counter((row.effect_code, row.key) for row in effect_rows))
        for row in effect_rows[:10]:
            print("EFFECT", row.timestamp_seconds, row.source_id, row.skill_name, row.key, row.target_id, row.operation, row.effect_code, row.value, row.duration_seconds, row.parameters)
        for code in sorted({row.effect_code for row in effect_rows}):
            sample = next(row for row in effect_rows if row.effect_code == code and row.key.endswith("抗%")) if any(row.effect_code == code and row.key.endswith("抗%") for row in effect_rows) else next(row for row in effect_rows if row.effect_code == code)
            print("CODE SAMPLE", skill, code, sample.timestamp_seconds, sample.key, sample.value, sample.duration_seconds, sample.parameters)
    door_key = canonical_skill("灵魂之门")
    door_casts = [
        row for (_source, skill), rows in casts.items()
        if skill == door_key for row in rows
    ]
    door_statuses = [
        row for row in snapshot.boss_effect_events
        if canonical_skill(row.skill_name) == door_key
        and row.effect_code == "134800"
    ]
    door_delays = []
    for cast in door_casts:
        cast_time = cast.get("timestamp_ms", 0) / 1000.0
        application = next(
            (
                row.timestamp_seconds for row in door_statuses
                if row.source_id == cast.get("source_id")
                and 0 <= row.timestamp_seconds - cast_time < 2.0
            ),
            None,
        )
        if application is not None:
            door_delays.append(round(application - cast_time, 3))
    print(
        "DOOR CAST TO STATUS", len(door_delays),
        "median", statistics.median(door_delays) if door_delays else None,
        "samples", door_delays[:12],
    )
    windows = engine._output_windows(snapshot, None)
    context = engine._build_support_counterfactual_context(
        snapshot, windows, None,
    )
    for identity, intervals in sorted(context.intervals_by_identity.items()):
        if identity[1] in target_skills and identity[2].endswith("抗%"):
            print("SCORE EFFECT", identity, context.effective_state_by_identity.get(identity),
                  len(intervals), intervals[:4],
                  context.effective_intervals_by_identity.get(identity, ())[:4])


if __name__ == "__main__":
    main(sys.argv[1])
