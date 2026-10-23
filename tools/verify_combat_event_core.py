from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.event_index import event_index_for
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def _monotonic(rows: tuple[object, ...], field: str) -> bool:
    values = [float(getattr(row, field)) for row in rows]
    return all(left <= right for left, right in zip(values, values[1:]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._score_sync.enqueue = lambda *_args, **_kwargs: None
    score_engine = TeamScoreEngine(store)
    reports: list[dict[str, object]] = []
    failed = False
    for capture in args.captures:
        started = time.perf_counter()
        replay = core.load_replay(
            str(capture.resolve()), lambda _value, _message: None
        )
        snapshot = core._replays[str(replay["sourceHash"])]
        index = event_index_for(snapshot)
        timeline = tuple(
            core.score_theory_planner.score_cast_events(snapshot)
        )
        output_windows = score_engine._output_windows(snapshot)
        timestamp_rows = [
            ("combat", index.combat_events, "timestamp_ms"),
            ("incoming", index.incoming_damage_events, "timestamp_ms"),
            ("cast", index.skill_cast_events, "timestamp_ms"),
            ("boss_action", index.boss_action_events, "timestamp_ms"),
            ("boss_effect", index.boss_effect_events, "timestamp_seconds"),
            ("player_buff", index.player_buff_events, "timestamp_seconds"),
            ("lifecycle", index.player_lifecycle_events, "timestamp_ms"),
        ]
        monotonic = {
            name: _monotonic(rows, field)
            for name, rows, field in timestamp_rows
        }
        timeline_monotonic = _monotonic(timeline, "timestamp_ms")
        non_negative = all(
            float(getattr(row, field)) >= 0
            for _name, rows, field in timestamp_rows
            for row in rows
        ) and all(row.timestamp_ms >= 0 for row in timeline)
        recording_end_ms = index.recording_end_ms
        timeline_in_bounds = all(
            row.timestamp_ms <= recording_end_ms for row in timeline
        )
        digest = hashlib.sha256(
            "\n".join(
                f"{row.timestamp_ms}|{row.source_id}|{row.skill_id}|{row.skill_name}"
                for row in timeline
            ).encode("utf-8")
        ).hexdigest()
        report = {
            "file": capture.name,
            "decodeSeconds": time.perf_counter() - started,
            "currentSeconds": index.current_ms / 1000.0,
            "recordingEndSeconds": recording_end_ms / 1000.0,
            "counts": {
                "combat": len(index.combat_events),
                "incoming": len(index.incoming_damage_events),
                "casts": len(index.skill_cast_events),
                "timeline": len(timeline),
                "bossEffects": len(index.boss_effect_events),
                "playerBuffs": len(index.player_buff_events),
                "healthTracks": len(index.health_tracks_by_target),
            },
            "outputWindows": output_windows,
            "timelineFirstSeconds": (
                timeline[0].timestamp_ms / 1000.0 if timeline else None
            ),
            "timelineLastSeconds": (
                timeline[-1].timestamp_ms / 1000.0 if timeline else None
            ),
            "timelineDigest": digest,
            "monotonic": monotonic,
            "timelineMonotonic": timeline_monotonic,
            "nonNegative": non_negative,
            "timelineInBounds": timeline_in_bounds,
        }
        reports.append(report)
        if not (
            all(monotonic.values())
            and timeline_monotonic
            and non_negative
            and timeline_in_bounds
        ):
            failed = True
        print(json.dumps(report, ensure_ascii=False), flush=True)

    rendered = json.dumps(reports, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
