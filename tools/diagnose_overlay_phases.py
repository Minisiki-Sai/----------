from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.models import BossStatusEvent
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def _row_dict(row: object) -> dict[str, object]:
    return dataclasses.asdict(row)  # type: ignore[arg-type]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("usage: diagnose_overlay_phases.py CAPTURE.dpscap")
    capture = Path(sys.argv[1])
    events = V576DiagnosticReplaySource(
        capture,
        Path("dragon_nest_builder/data"),
        {},
    ).poll()
    events.sort(key=lambda event: event.timestamp_ms)
    samples = (
        (1400.0, "千夫长"),
        (1500.0, "千夫长"),
        (1600.0, "千夫长"),
        (1700.0, "千夫长"),
        (1765.0, "红龙佩达"),
        (1800.0, "红龙佩达"),
        (1900.0, "红龙佩达"),
        (2000.0, "红龙佩达"),
        (2200.0, "红龙佩达"),
        (2500.0, "红龙佩达"),
        (3000.0, "红龙佩达"),
        (4000.0, "红龙佩达"),
        (4700.0, "红龙佩达"),
    )
    aggregator = CombatAggregator()
    event_index = 0
    results: list[dict[str, object]] = []
    for second, phase in samples:
        current_ms = round(second * 1000)
        while event_index < len(events) and events[event_index].timestamp_ms <= current_ms:
            aggregator.ingest(events[event_index])
            event_index += 1
        snapshot = aggregator.snapshot(
            current_ms,
            include_events=False,
            live_clock=True,
        )
        all_effects = aggregator._effect_snapshots(
            aggregator._boss_effects,
            current_ms,
            preserve_parameters=False,
            group_by_target=True,
        )
        all_active = [row for row in all_effects if row.active]
        selected_active = [row for row in snapshot.boss_effects if row.active]
        damage_targets = [_row_dict(row) for row in snapshot.damage_targets]
        overlay = NativeSimulatorCore._overlay_debuff_rows(
            [_row_dict(row) for row in snapshot.boss_effects],
            second,
            damage_targets,
        )
        recent_statuses = [
            event
            for event in events[max(0, event_index - 5000) : event_index]
            if isinstance(event, BossStatusEvent)
            and current_ms - 5000 <= event.timestamp_ms <= current_ms
        ]
        results.append(
            {
                "time": second,
                "phase": phase,
                "recent_status_events_5s": len(recent_statuses),
                "all_active_effects": len(all_active),
                "all_active_targets": sorted({row.target_id for row in all_active}),
                "all_active_keys": sorted({row.key for row in all_active}),
                "selected_active_effects": len(selected_active),
                "selected_active_targets": sorted(
                    {row.target_id for row in selected_active}
                ),
                "selected_active_keys": sorted({row.key for row in selected_active}),
                "overlay_rows": overlay,
                "active_player_buffs": sum(
                    1 for row in snapshot.player_buffs if row.active
                ),
                "damage_target_ids": [row.target_id for row in snapshot.damage_targets],
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
