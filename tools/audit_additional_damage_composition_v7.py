from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.additional_damage_composition import (
    build_additional_damage_views,
)
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    audit_additional_damage_shadow,
    is_intrinsic_additional_damage_event,
    is_native_supplemental_damage_event,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile


_LEGACY_FOLDED_METHODS = frozenset(
    {"supplemental_cast_owner", "supplemental_same_packet", "direct_hit_overflow"}
)
_APPLIED = frozenset({"当前已正确识别", "影子归因确定", "效果来源已识别"})


def analyze(path: Path) -> dict[str, object]:
    DataStore.load()
    profile = ClientProfile.load(DATA_DIR / "live_dps_profiles" / "v576.json")
    source = V576DiagnosticReplaySource(path, DATA_DIR, profile.known_players)
    try:
        decoded = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in decoded:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    events = tuple(snapshot.combat_events or ())
    jobs = {str(player.source_id): str(player.job or "") for player in snapshot.players}
    audits = audit_additional_damage_shadow(
        events,
        jobs,
        tuple(snapshot.skill_cast_events or ()),
        tuple(snapshot.boss_effects or ()),
        tuple(snapshot.player_buffs or ()),
        tuple(snapshot.skill_parameter_events or ()),
    )
    audit_by_event = {row.event_index: row for row in audits}
    separated, attributed, summary = build_additional_damage_views(snapshot)

    players: list[dict[str, object]] = []
    for player in snapshot.players:
        source_id = str(player.source_id)
        source_events = [event for event in events if event.source_id == source_id]
        native = [event for event in source_events if is_native_supplemental_damage_event(event)]
        legacy = [
            event
            for event in source_events
            if str(event.attribution_method or "").casefold() in _LEGACY_FOLDED_METHODS
        ]
        intrinsic = [
            (index, event)
            for index, event in enumerate(events)
            if event.source_id == source_id and is_intrinsic_additional_damage_event(event)
        ]
        applied = [
            (index, event, audit_by_event[index])
            for index, event in enumerate(events)
            if event.source_id == source_id
            and index in audit_by_event
            and audit_by_event[index].status in _APPLIED
            and audit_by_event[index].effect_skill_id
        ]
        separated_rows = separated.get(source_id, ())
        attributed_rows = attributed.get(source_id, ())
        effect_rows = [
            row for row in separated_rows
            if str(row.get("skill_id") or "").startswith("additional:")
        ]
        effect_breakdown = Counter()
        for row in effect_rows:
            effect_breakdown[str(row.get("name") or row.get("skill_id"))] += int(
                row.get("damage", 0) or 0
            )
        unknown_rows = [
            row for row in effect_rows
            if str(row.get("skill_id") or "") == "additional:unknown"
        ]
        raw_damage = sum(int(row.damage) for row in player.skills)
        separated_damage = sum(int(row.get("damage", 0) or 0) for row in separated_rows)
        attributed_damage = sum(int(row.get("damage", 0) or 0) for row in attributed_rows)
        players.append(
            {
                "sourceId": source_id,
                "name": player.name,
                "job": player.job,
                "totalDamage": int(player.damage),
                "legacyBroadExtraHits": len(legacy),
                "legacyBroadExtraDamage": sum(event.amount for event in legacy),
                "recoveredNativeHits": len(native),
                "recoveredNativeDamage": sum(event.amount for event in native),
                "intrinsicExtraHits": len(intrinsic),
                "intrinsicExtraDamage": sum(event.amount for _index, event in intrinsic),
                "confirmedEffectHits": len(applied),
                "confirmedEffectDamage": sum(event.amount for _index, event, _audit in applied),
                "unknownExtraHits": sum(int(row.get("hits", 0) or 0) for row in unknown_rows),
                "unknownExtraDamage": sum(int(row.get("damage", 0) or 0) for row in unknown_rows),
                "effects": [
                    {"name": name, "damage": damage}
                    for name, damage in effect_breakdown.most_common()
                ],
                "damageConserved": (
                    raw_damage == int(player.damage)
                    and separated_damage == int(player.damage)
                    and attributed_damage == int(player.damage)
                ),
            }
        )
    return {
        "capture": str(path),
        "algorithmVersion": summary.get("algorithmVersion"),
        "players": players,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit v7 semantic extra-damage lanes and damage conservation."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"captures": [analyze(path) for path in args.captures]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
