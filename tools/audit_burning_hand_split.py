"""Read-only evidence audit for separating Burning Hand proc Hits."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.models import (
    BossStatusEvent,
    CombatEvent,
    SkillCastEvent,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource


FIRE_SKILL_IDS = frozenset(
    {
        "2002", "2202", "2205", "2206", "2207", "2208", "2213",
        "2215", "2217", "2220", "2302", "2303", "2304", "2305", "2306",
    }
)


def audit(path: Path, output: Path | None) -> dict[str, object]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    combat: list[CombatEvent] = []
    statuses: list[BossStatusEvent] = []
    casts: list[SkillCastEvent] = []
    try:
        for batch in source.iter_batches():
            for event in batch:
                if isinstance(event, CombatEvent):
                    combat.append(event)
                elif isinstance(event, BossStatusEvent):
                    statuses.append(event)
                elif isinstance(event, SkillCastEvent):
                    casts.append(event)
    finally:
        source.close()

    fire_sources = {
        event.source_id
        for event in combat
        if event.job == "火舞" and event.source_id
    }
    fire_events = [event for event in combat if event.source_id in fire_sources]
    all_methods = Counter(
        (event.attribution_method, event.wire_damage_layout)
        for event in fire_events if not event.healing and event.amount > 0
    )
    all_skills = Counter(
        (event.skill_id, event.skill_name, event.attribution_method)
        for event in fire_events if not event.healing and event.amount > 0
    )
    subtype0_events = [
        event
        for event in fire_events
        if "subtype0" in str(event.wire_damage_layout or "")
    ]
    fire_direct = [
        event
        for event in fire_events
        if (
            event.skill_id in FIRE_SKILL_IDS
            and not event.healing
            and event.amount > 0
            and event.has_real_hit_evidence is True
        )
    ]
    same_time_groups: dict[
        tuple[str, str, int, int], list[CombatEvent]
    ] = defaultdict(list)
    for event in fire_direct:
        same_time_groups[
            (
                event.skill_id, event.target_id, event.timestamp_ms,
                event.wire_result_group_id,
            )
        ].append(event)
    duplicate_groups = [
        events for events in same_time_groups.values() if len(events) > 1
    ]
    cast_counts = Counter(
        (event.skill_id, event.skill_name)
        for event in casts
        if event.source_id in fire_sources and event.skill_id in FIRE_SKILL_IDS
    )
    interesting = [
        event
        for event in fire_events
        if (
            event.skill_id in FIRE_SKILL_IDS
            and (
                event.attribution_method == "supplemental_same_packet"
                or "status-damage-subtype0" in event.wire_damage_layout
                or event.raw_skill_id not in {"", event.skill_id}
            )
        )
    ]
    by_signature = Counter(
        (
            event.skill_id,
            event.skill_name,
            event.attribution_method,
            event.wire_damage_layout,
            event.raw_skill_id,
            event.raw_attribution_method,
            bool(event.has_real_hit_evidence),
        )
        for event in interesting
    )
    by_skill = defaultdict(lambda: Counter(events=0, damage=0))
    for event in interesting:
        by_skill[(event.skill_id, event.skill_name)]["events"] += 1
        by_skill[(event.skill_id, event.skill_name)]["damage"] += event.amount

    burn_statuses = [
        event
        for event in statuses
        if any(
            token in (
                event.skill_name,
                event.effect_key,
                event.effect_label,
                event.status_kind,
            )
            for token in ("烧伤", "灼烧")
        )
    ]
    payload = {
        "capture": str(path),
        "combatEvents": len(combat),
        "fireSources": sorted(fire_sources),
        "fireEvents": len(fire_events),
        "fireDamage": sum(event.amount for event in fire_events if not event.healing),
        "interestingEvents": len(interesting),
        "interestingDamage": sum(event.amount for event in interesting),
        "interestingShareOfFireDamage": (
            sum(event.amount for event in interesting)
            / max(1, sum(event.amount for event in fire_events if not event.healing))
        ),
        "allMethods": [
            {"method": key[0], "wireLayout": key[1], "count": count}
            for key, count in all_methods.most_common()
        ],
        "allSkills": [
            {
                "skillId": key[0], "skillName": key[1],
                "method": key[2], "count": count,
            }
            for key, count in all_skills.most_common()
        ],
        "subtype0Samples": [asdict(event) for event in subtype0_events[:200]],
        "fireDirectEvents": len(fire_direct),
        "castCounts": [
            {"skillId": key[0], "skillName": key[1], "count": count}
            for key, count in cast_counts.most_common()
        ],
        "duplicateGroupSizes": dict(Counter(len(rows) for rows in duplicate_groups)),
        "duplicateGroupSamples": [
            [asdict(event) for event in rows]
            for rows in duplicate_groups[:100]
        ],
        "fireDirectSamples": [asdict(event) for event in fire_direct[:500]],
        "bySkill": [
            {
                "skillId": skill_id,
                "skillName": skill_name,
                **counts,
            }
            for (skill_id, skill_name), counts in sorted(
                by_skill.items(), key=lambda item: item[1]["damage"], reverse=True
            )
        ],
        "signatures": [
            {
                "skillId": key[0],
                "skillName": key[1],
                "method": key[2],
                "wireLayout": key[3],
                "rawSkillId": key[4],
                "rawMethod": key[5],
                "realHit": key[6],
                "count": count,
            }
            for key, count in by_signature.most_common()
        ],
        "samples": [asdict(event) for event in interesting[:200]],
        "burnStatuses": [asdict(event) for event in burn_statuses[:200]],
        "fireCasts": [
            asdict(event)
            for event in casts
            if event.source_id in fire_sources and event.skill_id in FIRE_SKILL_IDS
        ][:200],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = audit(args.capture, args.output)
    print(json.dumps({key: payload[key] for key in (
        "capture", "combatEvents", "fireSources", "fireEvents", "fireDamage",
        "interestingEvents", "interestingDamage", "interestingShareOfFireDamage",
        "bySkill", "signatures", "allMethods", "allSkills", "fireDirectEvents",
        "castCounts", "duplicateGroupSizes",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
