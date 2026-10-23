from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    audit_additional_damage_shadow,
)
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile


INTERESTING_JOBS = frozenset({"雷神", "圣骑士"})
INTERESTING_NAMES = ("触电", "雷电冲击", "冲击流", "雷暴之息")
INTERESTING_METHODS = frozenset(
    {
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
        "packet_entity_status_damage",
        "supplemental_cast_owner",
        "supplemental_same_packet",
        "direct_hit_overflow",
    }
)


def _interesting(event) -> bool:
    text = " ".join(
        (
            str(event.skill_name or ""),
            str(event.raw_skill_name or ""),
            str(event.trigger_skill_name or ""),
        )
    )
    return (
        any(name in text for name in INTERESTING_NAMES)
        or str(event.attribution_method or "") in INTERESTING_METHODS
        or int(event.wire_status_skill_id or 0) in {3202, 3351, 3355, 3551, 3557}
    )


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
    source_jobs = {
        str(player.source_id): str(player.job or "") for player in snapshot.players
    }
    shadow_rows = audit_additional_damage_shadow(
        events,
        source_jobs,
        tuple(snapshot.skill_cast_events or ()),
        tuple(snapshot.boss_effects or ()),
        tuple(snapshot.player_buffs or ()),
        tuple(snapshot.skill_parameter_events or ()),
    )
    shadow_by_event = {row.event_index: row for row in shadow_rows}

    result: dict[str, object] = {
        "capture": str(path),
        "players": [],
    }
    for source_id, job in source_jobs.items():
        if job not in INTERESTING_JOBS:
            continue
        player = next(p for p in snapshot.players if str(p.source_id) == source_id)
        source_events = [
            (index, event)
            for index, event in enumerate(events)
            if event.source_id == source_id and _interesting(event)
        ]
        effect_groups: Counter[tuple[object, ...]] = Counter()
        effect_damage: Counter[tuple[object, ...]] = Counter()
        effect_samples: defaultdict[
            tuple[object, ...], list[dict[str, object]]
        ] = defaultdict(list)
        for index, event in enumerate(events):
            if event.source_id != source_id or index not in shadow_by_event:
                continue
            audit = shadow_by_event[index]
            if not audit.effect_skill_id:
                continue
            key = (
                audit.status,
                audit.effect_skill_id,
                audit.effect_name,
                event.skill_id,
                event.skill_name,
                event.attribution_method,
                event.wire_damage_layout,
                audit.trigger_skill_id,
                audit.trigger_skill_name,
            )
            effect_groups[key] += 1
            effect_damage[key] += int(event.amount)
            if len(effect_samples[key]) < 5:
                effect_samples[key].append(
                    {
                        "eventIndex": index,
                        "timestampMs": event.timestamp_ms,
                        "amount": event.amount,
                        "critical": event.critical,
                        "targetId": event.target_id,
                        "wireResultGroupId": event.wire_result_group_id,
                        "wireRecordOffset": event.wire_record_offset,
                        "reason": audit.reason,
                    }
                )
        grouped: Counter[tuple[object, ...]] = Counter()
        damage: Counter[tuple[object, ...]] = Counter()
        samples: defaultdict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
        for index, event in source_events:
            key = (
                event.skill_id,
                event.skill_name,
                event.raw_skill_id,
                event.raw_skill_name,
                event.attribution_method,
                event.wire_status_skill_id,
                event.wire_damage_layout,
                event.entity_attack_skill_id,
            )
            grouped[key] += 1
            damage[key] += int(event.amount)
            if len(samples[key]) < 5:
                row = asdict(event)
                row["event_index"] = index
                samples[key].append(row)

        casts = [
            asdict(cast)
            for cast in tuple(snapshot.skill_cast_events or ())
            if cast.source_id == source_id
            and any(name in str(cast.skill_name or "") for name in INTERESTING_NAMES)
        ]
        result["players"].append(
            {
                "sourceId": source_id,
                "name": player.name,
                "job": job,
                "casts": casts,
                "groups": [
                    {
                        "skillId": key[0],
                        "skillName": key[1],
                        "rawSkillId": key[2],
                        "rawSkillName": key[3],
                        "method": key[4],
                        "wireStatusSkillId": key[5],
                        "wireLayout": key[6],
                        "entityAttackSkillId": key[7],
                        "hits": grouped[key],
                        "damage": damage[key],
                        "samples": samples[key],
                    }
                    for key in sorted(
                        grouped,
                        key=lambda item: (-damage[item], str(item)),
                    )
                ],
                "effectAssignments": [
                    {
                        "status": key[0],
                        "effectSkillId": key[1],
                        "effectName": key[2],
                        "damageSkillId": key[3],
                        "damageSkillName": key[4],
                        "method": key[5],
                        "wireLayout": key[6],
                        "triggerSkillId": key[7],
                        "triggerSkillName": key[8],
                        "hits": effect_groups[key],
                        "damage": effect_damage[key],
                        "samples": effect_samples[key],
                    }
                    for key in sorted(
                        effect_groups,
                        key=lambda item: (-effect_damage[item], str(item)),
                    )
                ],
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
