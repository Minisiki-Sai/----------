from __future__ import annotations

import dataclasses
import json
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.v410_native_core import NativeSimulatorCore, _display_skill_key


def row_dict(row: object) -> dict[str, object]:
    return dataclasses.asdict(row)  # type: ignore[arg-type]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("usage: diagnose_overlay_survival_window.py CAPTURE.dpscap")
    capture = Path(sys.argv[1])
    source = V576DiagnosticReplaySource(
        capture,
        Path("dragon_nest_builder/data"),
        {},
    )
    events = sorted(source.poll(), key=lambda event: event.timestamp_ms)
    decoder = getattr(source, "_decoder", None)
    self_actor_id = int(getattr(decoder, "current_self_actor_id", 0) or 0)
    local_actor_ids = sorted(getattr(decoder, "local_actor_ids", set()) or set())
    self_source_id = f"0x{self_actor_id:08X}" if self_actor_id else ""

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    catalog = core._live_buff_catalog
    samples = (2400, 2450, 2500, 2550, 2600, 2650, 2700, 2750, 2780, 2800)
    aggregator = CombatAggregator()
    event_index = 0
    results: list[dict[str, object]] = []
    for second in samples:
        current_ms = second * 1000
        while event_index < len(events) and events[event_index].timestamp_ms <= current_ms:
            aggregator.ingest(events[event_index])
            event_index += 1
        snapshot = aggregator.snapshot(current_ms, include_events=False, live_clock=True)
        if not self_source_id:
            named_players = [
                row
                for row in snapshot.players
                if row.name and not row.name.startswith("角色 0x")
            ]
            if len(named_players) == 1:
                self_source_id = named_players[0].source_id
        player_buffs = [row_dict(row) for row in snapshot.player_buffs]
        active_buffs = [row for row in player_buffs if bool(row.get("active"))]
        eligible_buffs = [
            row
            for row in active_buffs
            if catalog.get(
                _display_skill_key(row.get("skill_name") or row.get("skill_id")),
                {},
            ).get("overlay") == "1"
        ]
        payload = {
            "elapsed_seconds": snapshot.elapsed_seconds,
            "boss_effect_current_seconds": snapshot.boss_effect_current_seconds,
            "players": [row_dict(row) for row in snapshot.players],
            "damage_targets": [row_dict(row) for row in snapshot.damage_targets],
            "boss_effects": [row_dict(row) for row in snapshot.boss_effects],
            "player_buffs": player_buffs,
            "player_health": [],
            "selfSourceId": self_source_id,
        }
        core.live_capture = SimpleNamespace(
            overlay_status=lambda payload=payload: {
                "state": "running",
                "status": "diagnostic",
                "overlaySnapshot": payload,
                "overlaySnapshotPublishedMonotonic": time.monotonic(),
            }
        )
        overlay = core.buff_overlay_status()
        results.append(
            {
                "time": second,
                "self_source_id": self_source_id,
                "active_buff_count": len(active_buffs),
                "eligible_buff_count": len(eligible_buffs),
                "eligible_self_buffs": sorted(
                    {
                        str(row.get("skill_name") or row.get("skill_id") or "")
                        for row in eligible_buffs
                        if str(row.get("target_id") or "") == self_source_id
                    }
                ),
                "overlay_self_buffs": [
                    [row.get("skill_name"), row.get("permanent")]
                    for row in overlay.get("buffs", [])
                    if row.get("target_id") == self_source_id
                ],
                "overlay_debuffs": [
                    [row.get("target_id"), row.get("label"), row.get("current_value")]
                    for row in overlay.get("debuffs", [])
                ],
            }
        )
    print(
        json.dumps(
            {
                "self_source_id": self_source_id,
                "local_actor_ids": [f"0x{value:08X}" for value in local_actor_ids],
                "samples": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
