from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    _AFTER_MS,
    _BEFORE_MS,
    _critical_proven,
    _is_additional_candidate,
    _is_proc_trigger,
    _ratio_error,
    SHADOW_EFFECT_RULES,
    audit_additional_damage_shadow,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.models import BossStatusEvent


APPLIED = {"当前已正确识别", "影子归因确定", "效果来源已识别"}


def analyze(path: Path) -> dict[str, object]:
    DataStore.load()
    profile = ClientProfile.load(DATA_DIR / "live_dps_profiles" / "v576.json")
    source = V576DiagnosticReplaySource(path, DATA_DIR, profile.known_players)
    try:
        decoded = source.poll()
    finally:
        source.close()
    raid_status_events = [
        event for event in decoded
        if isinstance(event, BossStatusEvent)
        and (
            str(event.skill_id or "") == "6009"
            or str(event.effect_code or "").casefold() == "134800"
            or "突袭" in str(event.skill_name or "")
        )
    ]
    cocktail_status_events = [
        event for event in decoded
        if isinstance(event, BossStatusEvent)
        and (
            str(event.skill_id or "") == "4401"
            or "鸡尾酒" in str(event.skill_name or "")
        )
    ]
    aggregator = CombatAggregator()
    for event in decoded:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    events = tuple(snapshot.combat_events or ())
    jobs = {str(player.source_id): str(player.job or "") for player in snapshot.players}
    casts = tuple(snapshot.skill_cast_events or ())
    buffs = tuple(snapshot.player_buffs or ())
    audits = audit_additional_damage_shadow(
        events,
        jobs,
        casts,
        tuple(snapshot.boss_effects or ()),
        buffs,
        tuple(snapshot.skill_parameter_events or ()),
    )
    audit_by_index = {row.event_index: row for row in audits}
    raid_rule = next(row for row in SHADOW_EFFECT_RULES if row.effect_skill_id == "6009")

    buff_intervals: dict[str, list[tuple[float, float]]] = defaultdict(list)
    raid_buff_rows = []
    for row in buffs:
        if str(getattr(row, "skill_id", "") or "") != "6009":
            continue
        target_id = str(getattr(row, "target_id", "") or "")
        intervals = tuple(getattr(row, "intervals", ()) or ())
        if not intervals:
            intervals = ((
                float(getattr(row, "start_seconds", 0.0) or 0.0),
                float(getattr(row, "end_seconds", 0.0) or 0.0),
            ),)
        buff_intervals[target_id].extend(
            (float(start), float(end)) for start, end in intervals
        )
        raid_buff_rows.append(
            {
                "sourceId": str(getattr(row, "source_id", "") or ""),
                "targetId": target_id,
                "label": str(getattr(row, "label", "") or ""),
                "intervals": intervals,
            }
        )

    direct_by_key: dict[tuple[str, str], list[tuple[int, int, object]]] = defaultdict(list)
    for index, event in enumerate(events):
        if _is_proc_trigger(event) and _critical_proven(event):
            direct_by_key[(event.source_id, event.target_id)].append(
                (event.timestamp_ms, index, event)
            )
    direct_times = {key: [row[0] for row in rows] for key, rows in direct_by_key.items()}

    physical_candidates: dict[str, list[dict[str, object]]] = defaultdict(list)
    for index, event in enumerate(events):
        if not _is_additional_candidate(event):
            continue
        key = (event.source_id, event.target_id)
        rows = direct_by_key.get(key, ())
        stamps = direct_times.get(key, ())
        lo = bisect_left(stamps, event.timestamp_ms - _BEFORE_MS)
        hi = bisect_right(stamps, event.timestamp_ms + _AFTER_MS)
        matches = []
        for timestamp_ms, trigger_index, trigger in rows[lo:hi]:
            error = _ratio_error(event.amount, trigger.amount, raid_rule)
            if error is not None:
                matches.append(
                    (error, abs(event.timestamp_ms - timestamp_ms), trigger_index, trigger)
                )
        if not matches:
            continue
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        audit = audit_by_index.get(index)
        physical_candidates[event.source_id].append(
            {
                "eventIndex": index,
                "timestampMs": event.timestamp_ms,
                "amount": event.amount,
                "method": event.attribution_method,
                "skillId": event.skill_id,
                "skillName": event.skill_name,
                "triggerIndex": matches[0][2],
                "triggerSkillId": matches[0][3].skill_id,
                "triggerSkillName": matches[0][3].skill_name,
                "auditStatus": audit.status if audit else "",
                "auditEffectId": audit.effect_skill_id if audit else "",
                "auditEffectName": audit.effect_name if audit else "",
            }
        )

    raw_ten_percent: dict[str, list[dict[str, object]]] = defaultdict(list)
    for index, event in enumerate(events):
        if int(getattr(event, "amount", 0) or 0) <= 0:
            continue
        key = (event.source_id, event.target_id)
        rows = direct_by_key.get(key, ())
        stamps = direct_times.get(key, ())
        lo = bisect_left(stamps, event.timestamp_ms - _BEFORE_MS)
        hi = bisect_right(stamps, event.timestamp_ms + _AFTER_MS)
        matches = []
        for timestamp_ms, trigger_index, trigger in rows[lo:hi]:
            if trigger_index == index:
                continue
            error = _ratio_error(event.amount, trigger.amount, raid_rule)
            if error is not None:
                matches.append(
                    (error, abs(event.timestamp_ms - timestamp_ms), trigger_index, trigger)
                )
        if not matches:
            continue
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        raw_ten_percent[event.source_id].append(
            {
                "eventIndex": index,
                "timestampMs": event.timestamp_ms,
                "amount": event.amount,
                "method": event.attribution_method,
                "skillId": event.skill_id,
                "skillName": event.skill_name,
                "hasRealHitEvidence": bool(event.has_real_hit_evidence),
                "wireLayout": event.wire_damage_layout,
                "triggerIndex": matches[0][2],
                "triggerSkillId": matches[0][3].skill_id,
                "triggerSkillName": matches[0][3].skill_name,
            }
        )

    player_rows = []
    for player in snapshot.players:
        source_id = str(player.source_id)
        candidates = physical_candidates.get(source_id, ())
        raw_candidates = raw_ten_percent.get(source_id, ())
        applied = [
            row for row in candidates
            if row["auditEffectId"] == "6009" and row["auditStatus"] in APPLIED
        ]
        intervals = sorted(buff_intervals.get(source_id, ()))
        critical_hits = [
            event for event in events
            if event.source_id == source_id
            and _is_proc_trigger(event)
            and _critical_proven(event)
        ]
        critical_inside = [
            event for event in critical_hits
            if any(start * 1000 <= event.timestamp_ms <= end * 1000 for start, end in intervals)
        ]
        player_rows.append(
            {
                "sourceId": source_id,
                "name": player.name,
                "job": player.job,
                "damage": int(player.damage),
                "raidIntervals": intervals,
                "criticalHits": len(critical_hits),
                "criticalDamage": sum(event.amount for event in critical_hits),
                "criticalHitsInsideObservedRaid": len(critical_inside),
                "criticalDamageInsideObservedRaid": sum(event.amount for event in critical_inside),
                "physicalTenPercentCandidates": len(candidates),
                "physicalTenPercentDamage": sum(int(row["amount"]) for row in candidates),
                "rawTenPercentRows": len(raw_candidates),
                "rawTenPercentDamage": sum(int(row["amount"]) for row in raw_candidates),
                "rawTenPercentRoutes": dict(Counter(
                    f"{row['method']}:{row['skillId']}:{row['skillName']}"
                    for row in raw_candidates
                )),
                "currentRaidHits": len(applied),
                "currentRaidDamage": sum(int(row["amount"]) for row in applied),
                "candidateCurrentEffects": dict(Counter(
                    f"{row['auditEffectId']}:{row['auditEffectName']}:{row['auditStatus']}"
                    for row in candidates
                )),
            }
        )
    return {
        "capture": str(path),
        "raidCasts": [
            {
                "timestampMs": cast.timestamp_ms,
                "sourceId": cast.source_id,
                "sourceName": cast.source_name,
                "job": cast.job,
            }
            for cast in casts if str(cast.skill_id) == "6009"
        ],
        "allPlayerBuffRows": len(buffs),
        "raidBuffRows": raid_buff_rows,
        "raidStatusEvents": [
            {
                "timestampMs": row.timestamp_ms,
                "sourceId": row.source_id,
                "targetId": row.target_id,
                "skillId": row.skill_id,
                "skillName": row.skill_name,
                "scope": row.target_scope,
                "operation": row.operation,
                "effectCode": row.effect_code,
                "parameters": row.parameters,
                "durationMs": row.duration_ms,
                "value": row.value,
                "prebattleInherited": bool(row.prebattle_inherited),
            }
            for row in raid_status_events
        ],
        "cocktailCasts": [
            {
                "timestampMs": cast.timestamp_ms,
                "sourceId": cast.source_id,
                "sourceName": cast.source_name,
                "job": cast.job,
            }
            for cast in casts if str(cast.skill_id) == "4401"
        ],
        "cocktailStatusEvents": [
            {
                "timestampMs": row.timestamp_ms,
                "sourceId": row.source_id,
                "targetId": row.target_id,
                "operation": row.operation,
                "durationMs": row.duration_ms,
                "prebattleInherited": bool(row.prebattle_inherited),
                "parameters": row.parameters,
            }
            for row in cocktail_status_events
        ],
        "players": player_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
