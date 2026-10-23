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
    _is_additional_candidate,
    _is_proc_trigger,
    _critical_proven,
    _ratio_error,
    SHADOW_EFFECT_RULES,
    audit_additional_damage_shadow,
)
from dragon_nest_builder.live_dps.additional_damage_composition import (
    _composition_identity,
)
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    is_intrinsic_additional_damage_event,
    is_native_supplemental_damage_event,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile


APPLIED = {"当前已正确识别", "影子归因确定", "效果来源已识别"}


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
    queen_ids = {
        source_id for source_id, job in jobs.items() if job == "黑暗女王"
    }
    audits = audit_additional_damage_shadow(
        events,
        jobs,
        tuple(snapshot.skill_cast_events or ()),
        tuple(snapshot.boss_effects or ()),
        tuple(snapshot.player_buffs or ()),
        tuple(snapshot.skill_parameter_events or ()),
    )
    audit_by_index = {row.event_index: row for row in audits}
    rule = next(row for row in SHADOW_EFFECT_RULES if row.effect_skill_id == "2557")

    trigger_rows: dict[tuple[str, str], list[tuple[int, int, object]]] = defaultdict(list)
    for index, event in enumerate(events):
        if (
            event.source_id in queen_ids
            and event.skill_id in rule.trigger_skill_ids
            and _is_proc_trigger(event)
        ):
            trigger_rows[(event.source_id, event.target_id)].append(
                (event.timestamp_ms, index, event)
            )
    trigger_times = {
        key: [row[0] for row in rows]
        for key, rows in trigger_rows.items()
    }

    candidates: list[dict[str, object]] = []
    trigger_candidate_counts: Counter[int] = Counter()
    for index, event in enumerate(events):
        if event.source_id not in queen_ids or not _is_additional_candidate(event):
            continue
        key = (event.source_id, event.target_id)
        rows = trigger_rows.get(key, ())
        stamps = trigger_times.get(key, ())
        lo = bisect_left(stamps, event.timestamp_ms - _BEFORE_MS)
        hi = bisect_right(stamps, event.timestamp_ms + _AFTER_MS)
        matches = []
        for timestamp_ms, trigger_index, trigger in rows[lo:hi]:
            error = _ratio_error(event.amount, trigger.amount, rule)
            if error is None:
                continue
            matches.append((error, abs(event.timestamp_ms - timestamp_ms), trigger_index, trigger))
            trigger_candidate_counts[trigger_index] += 1
        if not matches:
            continue
        audit = audit_by_index.get(index)
        matches.sort(key=lambda item: (item[0], item[1], item[2]))
        candidates.append(
            {
                "eventIndex": index,
                "timestampMs": event.timestamp_ms,
                "amount": event.amount,
                "skillId": event.skill_id,
                "skillName": event.skill_name,
                "method": event.attribution_method,
                "layout": event.wire_damage_layout,
                "matchCount": len(matches),
                "matchedTriggerIndexes": [item[2] for item in matches],
                "matchedTriggerSkills": sorted(
                    {f"{item[3].skill_id}:{item[3].skill_name}" for item in matches}
                ),
                "auditStatus": audit.status if audit else "",
                "auditEffectId": audit.effect_skill_id if audit else "",
                "auditTriggerIndex": audit.trigger_event_index if audit else -1,
                "auditReason": audit.reason if audit else "",
            }
        )

    source_events = [event for event in events if event.source_id in queen_ids]
    extra_events = [event for event in source_events if _is_additional_candidate(event)]
    current = [
        row for row in audits
        if row.effect_skill_id == "2557" and row.status in APPLIED
    ]
    unresolved_candidates = [
        row for row in candidates
        if row["auditEffectId"] != "2557" or row["auditStatus"] not in APPLIED
    ]
    candidate_indexes = {int(row["eventIndex"]) for row in candidates}
    all_damage_rows: dict[
        tuple[str, str], list[tuple[int, int, object]]
    ] = defaultdict(list)
    for damage_index, damage_event in enumerate(events):
        if (
            damage_event.source_id in queen_ids
            and not damage_event.healing
            and damage_event.amount > 0
        ):
            all_damage_rows[(damage_event.source_id, damage_event.target_id)].append(
                (damage_event.timestamp_ms, damage_index, damage_event)
            )
    all_damage_times = {
        key: [row[0] for row in rows] for key, rows in all_damage_rows.items()
    }
    non_candidates: list[dict[str, object]] = []
    extended_window_counts: Counter[str] = Counter()
    any_damage_match_counts: Counter[str] = Counter()
    any_damage_match_triggers: Counter[str] = Counter()
    for index, event in enumerate(events):
        if (
            event.source_id not in queen_ids
            or not _is_additional_candidate(event)
            or index in candidate_indexes
        ):
            continue
        key = (event.source_id, event.target_id)
        rows = trigger_rows.get(key, ())
        exact_within: int | None = None
        best_extended = None
        for timestamp_ms, trigger_index, trigger in rows:
            delta_ms = abs(event.timestamp_ms - timestamp_ms)
            if delta_ms > 1000:
                continue
            error = _ratio_error(event.amount, trigger.amount, rule)
            if error is None:
                continue
            match = (delta_ms, error, trigger_index, trigger)
            if best_extended is None or match[:3] < best_extended[:3]:
                best_extended = match
            for window_ms in (120, 250, 500, 1000):
                if delta_ms <= window_ms:
                    exact_within = window_ms
                    break
            if exact_within is not None:
                break
        audit = audit_by_index.get(index)
        all_rows = all_damage_rows.get(key, ())
        all_stamps = all_damage_times.get(key, ())
        all_lo = bisect_left(all_stamps, event.timestamp_ms - 1000)
        all_hi = bisect_right(all_stamps, event.timestamp_ms + 1000)
        any_matches = []
        for timestamp_ms, trigger_index, trigger in all_rows[all_lo:all_hi]:
            if trigger_index == index or trigger.amount <= 0:
                continue
            error = _ratio_error(event.amount, trigger.amount, rule)
            if error is None:
                continue
            any_matches.append(
                (abs(event.timestamp_ms - timestamp_ms), error, trigger_index, trigger)
            )
        any_matches.sort(key=lambda item: (item[0], item[1], item[2]))
        any_best = any_matches[0] if any_matches else None
        if any_best is None:
            any_bucket = "no_any_damage_30pct_within_1000ms"
        else:
            any_delta = int(any_best[0])
            any_window = next(
                window for window in (120, 250, 500, 1000) if any_delta <= window
            )
            any_bucket = f"any_damage_30pct_within_{any_window}ms"
            any_damage_match_triggers[
                f"{any_best[3].attribution_method}|"
                f"{any_best[3].skill_id}:{any_best[3].skill_name}"
            ] += 1
        any_damage_match_counts[any_bucket] += 1
        if exact_within is None:
            extended_bucket = "no_exact_30pct_within_1000ms"
        else:
            extended_bucket = f"exact_30pct_within_{exact_within}ms"
        extended_window_counts[extended_bucket] += 1
        non_candidates.append(
            {
                "eventIndex": index,
                "timestampMs": event.timestamp_ms,
                "amount": event.amount,
                "skillId": event.skill_id,
                "skillName": event.skill_name,
                "targetId": event.target_id,
                "method": event.attribution_method,
                "layout": event.wire_damage_layout,
                "hasRealHitEvidence": bool(event.has_real_hit_evidence),
                "nativeSupplemental": is_native_supplemental_damage_event(event),
                "intrinsicAdditional": is_intrinsic_additional_damage_event(event),
                "compositionIdentity": _composition_identity(event),
                "auditStatus": audit.status if audit else "",
                "auditEffectId": audit.effect_skill_id if audit else "",
                "auditEffectName": audit.effect_name if audit else "",
                "extendedBucket": extended_bucket,
                "extendedTriggerIndex": best_extended[2] if best_extended else -1,
                "extendedTriggerSkill": (
                    f"{best_extended[3].skill_id}:{best_extended[3].skill_name}"
                    if best_extended else ""
                ),
                "extendedDeltaMs": best_extended[0] if best_extended else None,
                "anyDamageBucket": any_bucket,
                "anyDamageTriggerIndex": any_best[2] if any_best else -1,
                "anyDamageTriggerSkill": (
                    f"{any_best[3].skill_id}:{any_best[3].skill_name}"
                    if any_best else ""
                ),
                "anyDamageTriggerMethod": (
                    str(any_best[3].attribution_method) if any_best else ""
                ),
                "anyDamageDeltaMs": any_best[0] if any_best else None,
            }
        )
    named_switch_rows = []
    critical_adjusted_counts: Counter[str] = Counter()
    ratio_family_counts: Counter[str] = Counter()
    critical_adjusted_examples: list[dict[str, object]] = []
    for index, event in enumerate(events):
        if not (
            event.source_id in queen_ids
            and event.attribution_method == "supplemental_cast_owner"
            and event.skill_id == "2553"
        ):
            continue
        key = (event.source_id, event.target_id)
        rows = trigger_rows.get(key, ())
        stamps = trigger_times.get(key, ())
        lo = bisect_left(stamps, event.timestamp_ms - _BEFORE_MS)
        hi = bisect_right(stamps, event.timestamp_ms + _AFTER_MS)
        possible = []
        ratio_family_possible = []
        for timestamp_ms, trigger_index, trigger in rows[lo:hi]:
            if trigger_index == index or trigger.amount <= 0:
                continue
            observed = event.amount / trigger.amount
            expected = 0.30
            if _critical_proven(event) and not _critical_proven(trigger):
                expected = 0.60
            elif not _critical_proven(event) and _critical_proven(trigger):
                expected = 0.15
            relative_error = abs(observed - expected) / expected
            possible.append(
                (
                    relative_error,
                    abs(event.timestamp_ms - timestamp_ms),
                    trigger_index,
                    trigger,
                    observed,
                    expected,
                )
            )
            for family_ratio in (0.15, 0.30, 0.60):
                ratio_family_possible.append(
                    (
                        abs(observed - family_ratio) / family_ratio,
                        abs(event.timestamp_ms - timestamp_ms),
                        trigger_index,
                        trigger,
                        observed,
                        family_ratio,
                    )
                )
        possible.sort(key=lambda item: (item[0], item[1], item[2]))
        ratio_family_possible.sort(key=lambda item: (item[0], item[1], item[2]))
        best = possible[0] if possible else None
        family_best = ratio_family_possible[0] if ratio_family_possible else None
        if best is None:
            bucket = "no_nearby_trigger"
        elif best[0] <= 0.001:
            bucket = f"matched_{best[5]:.2f}"
        else:
            bucket = "ratio_mismatch"
        critical_adjusted_counts[bucket] += 1
        if family_best is None:
            family_bucket = "no_nearby_trigger"
        elif family_best[0] <= 0.001:
            family_bucket = f"matched_{family_best[5]:.2f}"
        else:
            family_bucket = "ratio_mismatch"
        ratio_family_counts[family_bucket] += 1
        row = {
            "eventIndex": index,
            "timestampMs": event.timestamp_ms,
            "amount": event.amount,
            "critical": _critical_proven(event),
            "bucket": bucket,
            "bestRelativeError": best[0] if best else None,
            "bestDeltaMs": best[1] if best else None,
            "triggerIndex": best[2] if best else -1,
            "triggerSkill": (
                f"{best[3].skill_id}:{best[3].skill_name}" if best else ""
            ),
            "triggerMethod": str(best[3].attribution_method) if best else "",
            "triggerDamage": best[3].amount if best else 0,
            "triggerCritical": _critical_proven(best[3]) if best else False,
            "observedRatio": best[4] if best else None,
            "expectedRatio": best[5] if best else None,
            "ratioFamilyBucket": family_bucket,
            "ratioFamilyObserved": family_best[4] if family_best else None,
            "ratioFamilyExpected": family_best[5] if family_best else None,
        }
        named_switch_rows.append(row)
        if bucket != "matched_0.30" and len(critical_adjusted_examples) < 100:
            critical_adjusted_examples.append(row)
    return {
        "capture": str(path),
        "queenIds": sorted(queen_ids),
        "triggerHits": {
            "total": sum(len(rows) for rows in trigger_rows.values()),
            "bySkill": dict(Counter(
                f"{event.skill_id}:{event.skill_name}"
                for rows in trigger_rows.values()
                for _timestamp, _index, event in rows
            )),
        },
        "additionalRows": {
            "total": len(extra_events),
            "byMethod": dict(Counter(str(event.attribution_method) for event in extra_events)),
            "bySkill": dict(Counter(f"{event.skill_id}:{event.skill_name}" for event in extra_events)),
        },
        "gravityRatioCandidates": {
            "rows": len(candidates),
            "damage": sum(int(row["amount"]) for row in candidates),
            "byMethod": dict(Counter(str(row["method"]) for row in candidates)),
            "bySkill": dict(Counter(
                f"{row['skillId']}:{row['skillName']}" for row in candidates
            )),
            "uniqueTriggerSlots": len(trigger_candidate_counts),
            "currentlyAppliedRows": len(current),
            "currentlyAppliedDamage": sum(events[row.event_index].amount for row in current),
            "unresolvedRows": len(unresolved_candidates),
            "unresolvedDamage": sum(int(row["amount"]) for row in unresolved_candidates),
            "unresolvedStatus": dict(Counter(str(row["auditStatus"]) for row in unresolved_candidates)),
            "unresolvedMethod": dict(Counter(str(row["method"]) for row in unresolved_candidates)),
        },
        "namedGravitySwitchSupplemental": {
            "rows": len(named_switch_rows),
            "criticalAdjustedBuckets": dict(critical_adjusted_counts),
            "ratioFamilyBuckets": dict(ratio_family_counts),
            "examples": critical_adjusted_examples,
        },
        "remainingAdditionalRows": {
            "rows": len(non_candidates),
            "damage": sum(int(row["amount"]) for row in non_candidates),
            "byMethod": dict(Counter(str(row["method"]) for row in non_candidates)),
            "bySkill": dict(Counter(
                f"{row['skillId']}:{row['skillName']}" for row in non_candidates
            )),
            "byMethodAndSkill": dict(Counter(
                f"{row['method']}|{row['skillId']}:{row['skillName']}"
                for row in non_candidates
            )),
            "byLayout": dict(Counter(str(row["layout"]) for row in non_candidates)),
            "nativeSupplementalRows": sum(bool(row["nativeSupplemental"]) for row in non_candidates),
            "intrinsicAdditionalRows": sum(bool(row["intrinsicAdditional"]) for row in non_candidates),
            "hasRealHitEvidenceRows": sum(bool(row["hasRealHitEvidence"]) for row in non_candidates),
            "auditStatus": dict(Counter(str(row["auditStatus"]) for row in non_candidates)),
            "auditEffects": dict(Counter(
                f"{row['auditEffectId']}:{row['auditEffectName']}"
                for row in non_candidates
            )),
            "extendedWindow": dict(extended_window_counts),
            "anyDamageWindow": dict(any_damage_match_counts),
            "anyDamageTriggerRoutes": dict(any_damage_match_triggers),
            "examples": non_candidates[:200],
        },
        "unresolvedExamples": unresolved_candidates[:100],
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
