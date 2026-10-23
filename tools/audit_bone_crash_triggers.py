"""Read-only audit for Bone Crash's target-debuff/team-hit trigger model."""

from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    audit_additional_damage_shadow,
    _is_additional_candidate,
    _is_factual_trigger,
)
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


def audit(path: Path) -> None:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    events: list[CombatEvent] = []
    try:
        for batch in source.iter_batches():
            events.extend(event for event in batch if isinstance(event, CombatEvent))
    finally:
        source.close()

    direct_by_target: dict[str, list[tuple[int, int, CombatEvent]]] = defaultdict(list)
    for index, event in enumerate(events):
        if _is_factual_trigger(event):
            direct_by_target[event.target_id].append((event.timestamp_ms, index, event))
    direct_times = {}
    for target_id, rows in direct_by_target.items():
        rows.sort(key=lambda row: (row[0], row[1]))
        direct_times[target_id] = [row[0] for row in rows]

    extras = [
        (index, event)
        for index, event in enumerate(events)
        if _is_additional_candidate(event) and event.amount == 7_000
    ]
    summary = Counter()
    owners = Counter()
    triggers = Counter()
    examples = []
    for event_index, event in extras:
        rows = direct_by_target.get(event.target_id, ())
        stamps = direct_times.get(event.target_id, ())
        active_lo = bisect_left(stamps, event.timestamp_ms - 15_000)
        active_hi = bisect_right(stamps, event.timestamp_ms)
        active = any(
            trigger.skill_id == "503"
            for _stamp, _index, trigger in rows[active_lo:active_hi]
        )
        if not active:
            summary["outside_target_debuff"] += 1
            continue
        summary["inside_target_debuff"] += 1

        lo = bisect_left(stamps, event.timestamp_ms - 120)
        hi = bisect_right(stamps, event.timestamp_ms + 5)
        matches = list(rows[lo:hi])
        if not matches:
            summary["no_nearby_team_hit"] += 1
            continue
        summary["has_nearby_team_hit"] += 1
        matches.sort(
            key=lambda row: (abs(event.timestamp_ms - row[0]), row[1])
        )
        nearest_stamp, _nearest_index, nearest = matches[0]
        owners[(event.source_id, event.source_name, event.job)] += 1
        triggers[(nearest.source_id, nearest.source_name, nearest.job, nearest.skill_name)] += 1
        if nearest.source_id == event.source_id:
            summary["nearest_same_source"] += 1
        else:
            summary["nearest_teammate_source"] += 1
            if len(examples) < 20:
                examples.append(
                    (
                        event.timestamp_ms,
                        event.source_name,
                        nearest.source_name,
                        nearest.job,
                        nearest.skill_id,
                        nearest.skill_name,
                        event.timestamp_ms - nearest_stamp,
                        len(matches),
                    )
                )

    print(f"capture={path}")
    print(f"combat_events={len(events)} fixed_7000_status_rows={len(extras)}")
    print("summary=", dict(summary))
    print("effect_owners=")
    for key, count in owners.most_common(20):
        print(count, key)
    print("nearest_trigger_skills=")
    for key, count in triggers.most_common(30):
        print(count, key)
    print("cross_source_examples=(time, effect_owner, hitter, job, skill_id, skill, delta_ms, candidates)")
    for row in examples:
        print(row)
    jobs_by_source = {
        event.source_id: event.job for event in events if event.source_id and event.job
    }
    attributions = audit_additional_damage_shadow(events, jobs_by_source)
    bone_rows = [row for row in attributions if row.effect_skill_id == "503"]
    print("current_bone_attribution_statuses=", dict(Counter(row.status for row in bone_rows)))
    print(
        "current_bone_trigger_owners=",
        dict(
            Counter(
                "same_source"
                if row.trigger_event_index >= 0
                and events[row.trigger_event_index].source_id
                == events[row.event_index].source_id
                else "teammate_source"
                if row.trigger_event_index >= 0
                else "effect_only"
                for row in bone_rows
            )
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    audit(args.capture)


if __name__ == "__main__":
    main()
