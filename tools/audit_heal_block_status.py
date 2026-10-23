"""Read-only audit of player-targeted status packets in a v576 capture."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.models import BossStatusEvent, SkillParameterEvent
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--min-seconds", type=float, default=0.0)
    args = parser.parse_args()
    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, {})
    try:
        events = source.poll()
    finally:
        source.close()
    rows = [
        event for event in events
        if isinstance(event, BossStatusEvent)
        and event.target_scope == "player"
        and event.operation == "apply"
        and event.timestamp_ms >= args.min_seconds * 1000
    ]
    print(f"capture={args.capture.name} events={len(events)} player_status={len(rows)}")
    print("status_kinds:", Counter(event.status_kind for event in rows))
    print("source_jobs:", Counter(event.job for event in rows).most_common(16))
    player_ids = {
        event.target_id for event in rows
        if event.status_kind == "团队增益"
    }
    raw_enemy = [
        event for event in events
        if isinstance(event, SkillParameterEvent)
        and event.timestamp_ms >= args.min_seconds * 1000
        and event.target_id in player_ids
        and event.source_id not in player_ids
        and event.source_id != event.target_id
        and event.duration_ms > 0
    ]
    print("raw_enemy_status_count:", len(raw_enemy))
    raw_groups = defaultdict(list)
    for event in raw_enemy:
        raw_groups[(
            event.skill_id, event.raw_skill_id, event.effect_class_id,
            event.effect_code, event.parameters, event.duration_ms,
            event.job,
        )].append(event)
    for key, matches in sorted(
        raw_groups.items(), key=lambda entry: (
            -len({event.target_id for event in entry[1]}),
            -len(entry[1]),
        )
    )[:80]:
        first = min(event.timestamp_ms for event in matches) / 1000
        last = max(event.timestamp_ms for event in matches) / 1000
        print(
            f"RAW {key!r} count={len(matches)} "
            f"targets={len({event.target_id for event in matches})} "
            f"T={first:.3f}-{last:.3f}s"
        )
    grouped = defaultdict(list)
    for event in rows:
        key = (
            event.skill_id, event.skill_name, event.effect_code,
            event.parameters, event.status_kind, event.source_id,
        )
        grouped[key].append(event)
    for key, matches in sorted(
        grouped.items(), key=lambda entry: (
            -len({event.target_id for event in entry[1]}),
            -len(entry[1]),
            entry[0][0],
        )
    )[:120]:
        ids = {event.target_id for event in matches}
        first = min(event.timestamp_ms for event in matches) / 1000
        last = max(event.timestamp_ms for event in matches) / 1000
        durations = sorted({event.duration_ms for event in matches})
        print(
            f"{key!r} count={len(matches)} targets={len(ids)} "
            f"T={first:.3f}-{last:.3f}s durations_ms={durations[:6]}"
        )


if __name__ == "__main__":
    main()
