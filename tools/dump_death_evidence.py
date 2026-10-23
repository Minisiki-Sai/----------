from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def _event_row(event: object) -> dict[str, object]:
    row = asdict(event)
    row["seconds"] = round(int(row.get("timestamp_ms") or 0) / 1000.0, 3)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--radius", type=float, default=12.0)
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    args = parser.parse_args()

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    replay = core.load_replay(str(args.capture.resolve()), lambda *_args: None)
    snapshot = core._replays[str(replay["sourceHash"])]

    death_events = []
    for event in snapshot.combat_events:
        method = str(event.attribution_method or "").casefold()
        text = f"{event.skill_id} {event.skill_name}".casefold()
        if method in {"death", "player_death"} or "player_death" in method or "死亡" in text or "阵亡" in text:
            death_events.append(event)

    casts_by_actor: dict[str, list[object]] = {}
    for event in snapshot.skill_cast_events:
        casts_by_actor.setdefault(str(event.source_id), []).append(event)
    incoming_by_actor: dict[str, list[object]] = {}
    for event in snapshot.incoming_damage_events:
        incoming_by_actor.setdefault(str(event.target_id), []).append(event)

    details = []
    radius_ms = round(max(0.0, args.radius) * 1000)
    for death in death_events:
        actor_id = str(death.target_id or death.source_id)
        start_ms = death.timestamp_ms - radius_ms
        end_ms = death.timestamp_ms + radius_ms
        actor_casts = casts_by_actor.get(actor_id, ())
        prior_casts = [row for row in actor_casts if row.timestamp_ms <= death.timestamp_ms]
        later_casts = [row for row in actor_casts if row.timestamp_ms > death.timestamp_ms]
        details.append(
            {
                "death": _event_row(death),
                "previous_cast": _event_row(prior_casts[-1]) if prior_casts else None,
                "next_cast": _event_row(later_casts[0]) if later_casts else None,
                "nearby_casts": [
                    _event_row(row)
                    for row in actor_casts
                    if start_ms <= row.timestamp_ms <= end_ms
                ],
                "nearby_incoming": [
                    _event_row(row)
                    for row in incoming_by_actor.get(actor_id, ())
                    if start_ms <= row.timestamp_ms <= end_ms
                ],
            }
        )

    payload = {
        "capture": str(args.capture.resolve()),
        "players": [asdict(row) for row in snapshot.players],
        "death_events": details,
    }
    if args.start is not None and args.end is not None:
        start_ms = round(min(args.start, args.end) * 1000)
        end_ms = round(max(args.start, args.end) * 1000)
        payload["window"] = {
            "start": start_ms / 1000.0,
            "end": end_ms / 1000.0,
            "incoming": [
                _event_row(row)
                for row in snapshot.incoming_damage_events
                if start_ms <= row.timestamp_ms <= end_ms
            ],
            "casts": [
                _event_row(row)
                for row in snapshot.skill_cast_events
                if start_ms <= row.timestamp_ms <= end_ms
            ],
            "combat": [
                _event_row(row)
                for row in snapshot.combat_events
                if start_ms <= row.timestamp_ms <= end_ms
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"deaths": len(details), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
