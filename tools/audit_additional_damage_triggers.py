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
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.player_identity import resolve_player_identities
from dragon_nest_builder.live_dps.profile import ClientProfile


PROC_SKILLS = {"5118": "九天之舞2阶", "6219": "奉献乌鸦"}


def analyze(path: Path) -> dict[str, object]:
    store = DataStore.load()
    profile = ClientProfile.load(DATA_DIR / "live_dps_profiles" / "v576.json")
    source = V576DiagnosticReplaySource(path, DATA_DIR, profile.known_players)
    try:
        events = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    identities = resolve_player_identities(
        [
            {"source_id": row.source_id, "job": row.job, "name": row.name}
            for row in snapshot.players
        ],
        snapshot.party_members,
        snapshot.player_profiles,
    )
    names = {
        row.source_id: row.character_name or row.source_id
        for row in identities
    }

    rows = [
        row
        for row in snapshot.combat_events
        if row.skill_id in PROC_SKILLS and row.amount > 0 and not row.healing
    ]
    linked = [row for row in rows if row.trigger_skill_id]
    status_methods = {
        "status_damage_packet",
        "status_damage_skill",
        "periodic_status_packet",
        "packet_entity_status_damage",
        "supplemental_cast_owner",
        "supplemental_continuation",
        "supplemental_same_packet",
        "direct_hit_overflow",
        "refreshable_pulsing_aura",
    }
    all_additional = [
        row
        for row in snapshot.combat_events
        if not row.healing
        and row.amount > 0
        and (
            row.attribution_method in status_methods
            or row.skill_id.startswith(("supplemental:", "periodic:"))
        )
    ]
    additional_breakdown: Counter[tuple[str, str, str]] = Counter()
    additional_damage: Counter[tuple[str, str, str]] = Counter()
    trigger_breakdown: Counter[tuple[str, str, str, str]] = Counter()
    trigger_damage: Counter[tuple[str, str, str, str]] = Counter()
    for row in all_additional:
        key = (row.attribution_method, row.skill_id, row.skill_name)
        additional_breakdown[key] += 1
        additional_damage[key] += row.amount
        if row.trigger_skill_id or row.trigger_skill_name:
            trigger_key = (
                row.skill_name or row.skill_id,
                row.trigger_skill_name or row.trigger_skill_id,
                row.trigger_attribution_method,
                (
                    "exact"
                    if row.trigger_attribution_confidence >= 0.95
                    else "candidate"
                ),
            )
            trigger_breakdown[trigger_key] += 1
            trigger_damage[trigger_key] += row.amount
    breakdown: Counter[tuple[str, str, str, str]] = Counter()
    damage: Counter[tuple[str, str, str, str]] = Counter()
    for row in rows:
        key = (
            names.get(row.source_id, row.source_name or row.source_id),
            row.skill_name or PROC_SKILLS.get(row.skill_id, row.skill_id),
            row.trigger_skill_name or "未确定触发技能",
            row.trigger_attribution_method or "unlinked",
        )
        breakdown[key] += 1
        damage[key] += row.amount
    return {
        "capture": path.name,
        "events": len(rows),
        "linked": len(linked),
        "linkedRate": round(len(linked) / len(rows), 6) if rows else None,
        "damage": sum(row.amount for row in rows),
        "linkedDamage": sum(row.amount for row in linked),
        "allAdditionalEvents": len(all_additional),
        "allAdditionalDamage": sum(row.amount for row in all_additional),
        "attributedAdditionalEvents": sum(trigger_breakdown.values()),
        "attributedAdditionalDamage": sum(trigger_damage.values()),
        "triggerBreakdown": [
            {
                "originOrPacketOwner": key[0],
                "trigger": key[1],
                "method": key[2],
                "grade": key[3],
                "hits": hits,
                "damage": trigger_damage[key],
            }
            for key, hits in trigger_breakdown.most_common()
        ],
        "additionalBreakdown": [
            {
                "method": key[0],
                "skillId": key[1],
                "skillName": key[2],
                "hits": hits,
                "damage": additional_damage[key],
            }
            for key, hits in additional_breakdown.most_common()
        ],
        "breakdown": [
            {
                "character": key[0],
                "origin": key[1],
                "trigger": key[2],
                "method": key[3],
                "hits": hits,
                "damage": damage[key],
            }
            for key, hits in breakdown.most_common()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [analyze(path) for path in args.captures]
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
