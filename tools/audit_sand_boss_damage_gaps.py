from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.event_ledger import V576EventLedger  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.profile import ClientProfile  # noqa: E402
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


SAND_BOSS_TEMPLATES = frozenset(range(4_150_050, 4_150_073))


def _template_from_target_id(target_id: str) -> int:
    try:
        return int(str(target_id).rsplit("@", 1)[1])
    except (IndexError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    profile = ClientProfile.load(DATA_DIR / "live_dps_profiles" / "v576.json")
    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, profile.known_players)
    try:
        decoded = source.poll()
        decoder = source._decoder
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in decoded:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)

    physical_damage = Counter()
    physical_layouts = Counter()
    unsupported_type08 = Counter()
    unsupported_examples: list[dict[str, object]] = []
    ledger = V576EventLedger()
    origin = float(getattr(decoder, "first_timestamp", 0.0) or 0.0)
    source._pipeline_cancel.clear()
    for _timestamp, _adapter, packet, _position, _total in source._read_predecoded_frames():
        if packet is None or str(getattr(packet, "transport", "")).upper() != "UDP":
            continue
        if not ledger.accept(packet):
            continue
        inventory = inspect_udp_damage_records(
            packet.payload, packet.inferred_direction()
        )
        if not inventory.complete:
            continue
        elapsed_ms = round((float(packet.timestamp_seconds) - origin) * 1000.0)
        for row in inventory.damage_records:
            if row.target_actor_id not in {0x57, 0x58}:
                continue
            key = f"0x{row.target_actor_id:08X}"
            physical_damage[(key, "hits")] += 1
            physical_damage[(key, "damage")] += int(row.damage)
            physical_layouts[(key, f"0x{row.declared_length:02X}")] += 1
        for record in inventory.typed_records:
            if record.record_type != 8 or record.declared_length in {0x73, 0x75, 0x77, 0x79}:
                continue
            target_actor_id = int.from_bytes(record.record_bytes[:4], "little")
            if target_actor_id not in {0x57, 0x58}:
                continue
            key = f"0x{target_actor_id:08X}"
            unsupported_type08[(key, f"0x{record.declared_length:02X}")] += 1
            if len(unsupported_examples) < 50:
                unsupported_examples.append(
                    {
                        "elapsed_ms": elapsed_ms,
                        "target_actor_id": key,
                        "declared_length": record.declared_length,
                        "record_hex": record.record_bytes.hex(),
                    }
                )

    raw_rows: dict[str, list[CombatEvent]] = defaultdict(list)
    for event in decoded:
        if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
            continue
        template = _template_from_target_id(event.target_id)
        if template in SAND_BOSS_TEMPLATES:
            raw_rows[event.target_id].append(event)

    rows: dict[str, list[CombatEvent]] = defaultdict(list)
    for event in snapshot.combat_events:
        if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
            continue
        template = _template_from_target_id(event.target_id)
        if template in SAND_BOSS_TEMPLATES:
            rows[event.target_id].append(event)

    targets: list[dict[str, object]] = []
    for target_id, events in sorted(
        rows.items(), key=lambda item: min(row.timestamp_ms for row in item[1])
    ):
        target = next(
            (row for row in snapshot.damage_targets if row.target_id == target_id),
            None,
        )
        targets.append(
            {
                "target_id": target_id,
                "target_name": events[0].target_name,
                "template_id": _template_from_target_id(target_id),
                "damage": sum(row.amount for row in events),
                "hits": len(events),
                "first_ms": min(row.timestamp_ms for row in events),
                "last_ms": max(row.timestamp_ms for row in events),
                "max_hp": int(target.target_max_hp if target is not None else 0),
                "overkill": (
                    sum(row.amount for row in events)
                    - int(target.target_max_hp if target is not None else 0)
                ),
                "layouts": dict(
                    Counter(row.wire_damage_layout or "<none>" for row in events)
                ),
                "layout_damage": dict(
                    Counter(
                        {
                            layout: sum(
                                row.amount
                                for row in events
                                if (row.wire_damage_layout or "<none>") == layout
                            )
                            for layout in {
                                row.wire_damage_layout or "<none>" for row in events
                            }
                        }
                    )
                ),
                "wire_targets": dict(
                    Counter(f"0x{row.wire_target_actor_id:08X}" for row in events)
                ),
                "sources": dict(Counter(row.source_id for row in events)),
            }
        )

    hp_tracks = []
    for track in snapshot.boss_hp_tracks:
        template = _template_from_target_id(track.target_id)
        if template not in SAND_BOSS_TEMPLATES:
            continue
        hp_tracks.append(asdict(track))

    entity_templates = getattr(decoder, "_entity_templates", {})
    entity_generations = getattr(decoder, "_entity_generations", {})
    result = {
        "capture": str(args.capture),
        "nest": getattr(decoder, "detected_nest_name", ""),
        "decoded_event_count": len(decoded),
        "combat_event_count": len(snapshot.combat_events),
        "targets": targets,
        "raw_targets": [
            {
                "target_id": target_id,
                "target_name": events[0].target_name,
                "template_id": _template_from_target_id(target_id),
                "damage": sum(row.amount for row in events),
                "hits": len(events),
                "first_ms": min(row.timestamp_ms for row in events),
                "last_ms": max(row.timestamp_ms for row in events),
                "layouts": dict(
                    Counter(row.wire_damage_layout or "<none>" for row in events)
                ),
                "sources": dict(Counter(row.source_id for row in events)),
            }
            for target_id, events in sorted(
                raw_rows.items(),
                key=lambda item: min(row.timestamp_ms for row in item[1]),
            )
        ],
        "boss_hp_tracks": hp_tracks,
        "decoder_entities": [
            {
                "runtime_id": f"0x{int(runtime_id):08X}",
                "template_id": int(template_id),
                "generation": int(entity_generations.get(runtime_id, 0)),
            }
            for runtime_id, template_id in sorted(entity_templates.items())
            if int(template_id) in SAND_BOSS_TEMPLATES
        ],
        "stage_event_types": dict(
            Counter(
                type(event).__name__
                for event in decoded
                if 200_000 <= int(getattr(event, "timestamp_ms", -1)) <= 250_000
            )
        ),
        "boss_end_events": [
            {
                "type": type(event).__name__,
                **(
                    asdict(event)
                    if is_dataclass(event)
                    else {"repr": repr(event)}
                ),
            }
            for event in decoded
            if 240_000 <= int(getattr(event, "timestamp_ms", -1)) <= 247_000
            and (
                _template_from_target_id(getattr(event, "target_id", ""))
                in {4_150_056, 4_150_057}
                or int(getattr(event, "actor_id", 0) or 0) in {0x57, 0x58}
                or int(getattr(event, "wire_target_actor_id", 0) or 0)
                in {0x57, 0x58}
            )
        ],
        "physical_inventory": {
            "targets": {
                target: {
                    "hits": int(physical_damage[(target, "hits")]),
                    "damage": int(physical_damage[(target, "damage")]),
                    "layouts": {
                        layout: int(count)
                        for (row_target, layout), count in physical_layouts.items()
                        if row_target == target
                    },
                    "unsupported_type08": {
                        layout: int(count)
                        for (row_target, layout), count in unsupported_type08.items()
                        if row_target == target
                    },
                }
                for target in ("0x00000057", "0x00000058")
            },
            "unsupported_examples": unsupported_examples,
        },
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if args.compact:
        compact = {
            "capture": result["capture"],
            "nest": result["nest"],
            "targets": result["targets"],
            "raw_targets": result["raw_targets"],
            "boss_hp_tracks": [
                {
                    "target_id": row["target_id"],
                    "target_name": row["target_name"],
                    "max_hp": row["max_hp"],
                    "current_hp": row["current_hp"],
                    "max_hp_source": row.get("max_hp_source", row.get("confidence", "")),
                    "point_count": len(row["points"]),
                    "tracked_damage": sum(point["damage"] for point in row["points"]),
                    "tracked_healing": sum(point["healing"] for point in row["points"]),
                }
                for row in hp_tracks
            ],
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
