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
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    SHADOW_EFFECT_RULES,
    _build_activation_index,
    _build_effect_interval_index,
    _build_target_status_index,
    _activation_matches,
    _critical_proven,
    _is_additional_candidate,
    _is_proc_trigger,
    _ratio_error,
    _target_status_condition,
    audit_additional_damage_shadow,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile


APPLIED = frozenset({"当前已正确识别", "影子归因确定", "效果来源已识别"})


def _sum(rows, key: str) -> int:
    return sum(int(row.get(key, 0) or 0) for row in rows)


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
    casts = tuple(snapshot.skill_cast_events or ())
    player_effects = tuple(snapshot.player_buffs or ())
    boss_effects = tuple(snapshot.boss_effects or ())
    jobs = {str(player.source_id): str(player.job or "") for player in snapshot.players}
    names = {str(player.source_id): str(player.name or "") for player in snapshot.players}

    pain_rule = next(rule for rule in SHADOW_EFFECT_RULES if rule.effect_skill_id == "5407")
    raid_rule = next(rule for rule in SHADOW_EFFECT_RULES if rule.effect_skill_id == "6009")
    status_index = _build_target_status_index(boss_effects, events)
    activation_index = _build_activation_index(casts)
    interval_index = _build_effect_interval_index(player_effects)
    audits = audit_additional_damage_shadow(
        events,
        jobs,
        casts,
        boss_effects,
        player_effects,
        tuple(snapshot.skill_parameter_events or ()),
    )
    audit_by_event = {row.event_index: row for row in audits}

    result: list[dict[str, object]] = []
    for source_id, job in jobs.items():
        if job != "噬魂者":
            continue
        triggers: list[dict[str, object]] = []
        for index, event in enumerate(events):
            if event.source_id != source_id or not _is_proc_trigger(event):
                continue
            curse_ok, curse_note = _target_status_condition(pain_rule, event, status_index)
            raid_active = _activation_matches(
                raid_rule, event, activation_index, interval_index
            )
            triggers.append(
                {
                    "index": index,
                    "timestampMs": event.timestamp_ms,
                    "targetId": event.target_id,
                    "skillId": event.skill_id,
                    "skillName": event.skill_name,
                    "damage": event.amount,
                    "method": event.attribution_method,
                    "layout": event.wire_damage_layout,
                    "recordOffset": event.wire_record_offset,
                    "wireEntityId": event.wire_entity_id,
                    "entityOwnerEvidenceId": event.entity_owner_evidence_id,
                    "critical": _critical_proven(event),
                    "curse": curse_ok,
                    "curseNote": curse_note,
                    "raidActive": raid_active,
                }
            )

        applied = [
            row
            for row in audits
            if events[row.event_index].source_id == source_id
            and row.status in APPLIED
            and row.effect_skill_id in {"5407", "6009"}
        ]
        pain_rows = [row for row in applied if row.effect_skill_id == "5407"]
        raid_rows = [row for row in applied if row.effect_skill_id == "6009"]
        pain_trigger_indexes = {
            row.trigger_event_index for row in pain_rows if row.trigger_event_index >= 0
        }
        raid_trigger_indexes = {
            row.trigger_event_index for row in raid_rows if row.trigger_event_index >= 0
        }

        curse_triggers = [row for row in triggers if row["curse"]]
        critical_curse = [row for row in curse_triggers if row["critical"]]
        noncritical_curse = [row for row in curse_triggers if not row["critical"]]
        missing = [
            row for row in curse_triggers if int(row["index"]) not in pain_trigger_indexes
        ]
        missing_reasons: Counter[str] = Counter()
        missing_damage: Counter[str] = Counter()
        missing_critical: Counter[str] = Counter()
        missing_examples: list[dict[str, object]] = []
        for trigger in missing:
            trigger_index = int(trigger["index"])
            trigger_event = events[trigger_index]
            candidates: list[tuple[int, object]] = []
            for event_index, event in enumerate(events):
                if event.source_id != source_id or event.target_id != trigger_event.target_id:
                    continue
                if not _is_additional_candidate(event):
                    continue
                delta = event.timestamp_ms - trigger_event.timestamp_ms
                if not 0 <= delta <= 120:
                    continue
                if _ratio_error(event.amount, trigger_event.amount, pain_rule) is None:
                    continue
                candidates.append((event_index, event))
            if not candidates:
                reason = "wire_row_missing"
            else:
                labels = []
                for event_index, _event in candidates:
                    audit = audit_by_event.get(event_index)
                    if audit is None:
                        labels.append("not_audited")
                    elif audit.effect_skill_id:
                        labels.append(f"allocated:{audit.effect_skill_id}:{audit.effect_name}")
                    else:
                        labels.append(f"{audit.status}:unknown")
                reason = "|".join(sorted(set(labels)))
            missing_reasons[reason] += 1
            missing_damage[reason] += int(trigger["damage"])
            missing_critical[f"{reason}|critical={bool(trigger['critical'])}"] += 1
            if len(missing_examples) < 40:
                missing_examples.append(
                    {
                        **trigger,
                        "reason": reason,
                        "candidateEventIndexes": [index for index, _event in candidates],
                    }
                )

        all_player_damage = sum(
            event.amount
            for event in events
            if event.source_id == source_id and not event.healing
        )
        skill_rows: list[dict[str, object]] = []
        for skill_name in sorted({str(row["skillName"]) for row in curse_triggers}):
            subset = [row for row in curse_triggers if row["skillName"] == skill_name]
            skill_rows.append(
                {
                    "skillName": skill_name,
                    "hits": len(subset),
                    "damage": _sum(subset, "damage"),
                    "criticalHits": sum(bool(row["critical"]) for row in subset),
                    "ownedEntityHits": sum(
                        bool(row["wireEntityId"] and row["entityOwnerEvidenceId"])
                        for row in subset
                    ),
                    "painLinkedHits": sum(
                        int(row["index"]) in pain_trigger_indexes for row in subset
                    ),
                    "painLinkedDamage": sum(
                        int(row["damage"])
                        for row in subset
                        if int(row["index"]) in pain_trigger_indexes
                    ),
                    "raidLinkedHits": sum(
                        int(row["index"]) in raid_trigger_indexes for row in subset
                    ),
                }
            )
        skill_rows.sort(key=lambda row: int(row["damage"]), reverse=True)
        result.append(
            {
                "sourceId": source_id,
                "name": names.get(source_id, ""),
                "totalDamage": all_player_damage,
                "triggers": {
                    "hits": len(triggers),
                    "damage": _sum(triggers, "damage"),
                    "criticalHits": sum(bool(row["critical"]) for row in triggers),
                    "criticalDamage": sum(
                        int(row["damage"]) for row in triggers if row["critical"]
                    ),
                },
                "curseEligible": {
                    "hits": len(curse_triggers),
                    "damage": _sum(curse_triggers, "damage"),
                    "expectedPainDamage10Pct": round(_sum(curse_triggers, "damage") * 0.10),
                    "criticalHits": len(critical_curse),
                    "criticalDamage": _sum(critical_curse, "damage"),
                    "noncriticalHits": len(noncritical_curse),
                    "noncriticalDamage": _sum(noncritical_curse, "damage"),
                },
                "observed": {
                    "painHits": len(pain_rows),
                    "painDamage": sum(events[row.event_index].amount for row in pain_rows),
                    "painLinkedTriggerHits": len(pain_trigger_indexes),
                    "raidHits": len(raid_rows),
                    "raidDamage": sum(events[row.event_index].amount for row in raid_rows),
                    "raidLinkedTriggerHits": len(raid_trigger_indexes),
                    "painLinkedCriticalHits": sum(
                        _critical_proven(events[index])
                        for index in pain_trigger_indexes
                    ),
                    "painLinkedNoncriticalHits": sum(
                        not _critical_proven(events[index])
                        for index in pain_trigger_indexes
                    ),
                },
                "raidEligible": {
                    "hits": sum(
                        bool(row["raidActive"] and row["critical"])
                        for row in triggers
                    ),
                    "damage": sum(
                        int(row["damage"])
                        for row in triggers
                        if row["raidActive"] and row["critical"]
                    ),
                },
                "missingPainTriggers": {
                    "hits": len(missing),
                    "damage": _sum(missing, "damage"),
                    "expectedPainDamage10Pct": round(_sum(missing, "damage") * 0.10),
                    "reasonHits": dict(missing_reasons.most_common()),
                    "reasonTriggerDamage": dict(missing_damage.most_common()),
                    "reasonCriticalHits": dict(missing_critical.most_common()),
                    "examples": missing_examples,
                },
                "statusCoverage": {
                    "channelProvided": status_index.channel_provided,
                    "coveredTargets": sorted(status_index.covered_targets),
                    "intervalKeys": [
                        {"targetId": target_id, "label": label, "intervals": intervals}
                        for (target_id, label), intervals in status_index.intervals.items()
                        if "诅咒" in label
                    ],
                },
                "raidIntervals": [
                    {"skillId": skill_id, "targetId": target_id, "intervals": intervals}
                    for (skill_id, target_id), intervals in interval_index.intervals.items()
                    if skill_id == "6009" and target_id == source_id
                ],
                "skills": skill_rows,
            }
        )
    return {"capture": str(path), "players": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", required=True, type=Path)
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
