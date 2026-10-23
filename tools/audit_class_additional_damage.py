from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.player_identity import resolve_player_identities
from dragon_nest_builder.live_dps.profile import ClientProfile


STATUS_METHODS = {
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
FOCUS_JOBS = {"噬魂者", "狙翎", "剑皇", "狂战士"}
EXPECTED_PROC_RATIOS = {"噬魂者": 0.10, "狙翎": 0.30}


def _is_additional(row) -> bool:
    return bool(
        not row.healing
        and row.amount > 0
        and (
            row.attribution_method in STATUS_METHODS
            or row.skill_id.startswith(("supplemental:", "periodic:"))
        )
    )


def _is_direct(row) -> bool:
    return bool(
        not row.healing
        and row.amount > 0
        and row.has_real_hit_evidence is True
        and not _is_additional(row)
        and row.skill_name not in {"", "待识别伤害", "额外伤害"}
    )


def _round_ratio(extra: int, direct: int) -> str:
    if direct <= 0:
        return ""
    ratio = extra / direct
    return f"{ratio:.4f}"


def analyze(path: Path) -> dict[str, object]:
    store = DataStore.load()
    profile = ClientProfile.load(DATA_DIR / "live_dps_profiles" / "v576.json")
    source = V576DiagnosticReplaySource(path, DATA_DIR, profile.known_players)
    try:
        decoded_events = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in decoded_events:
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
    identity_by_source = {
        row.source_id: {
            "name": row.character_name or row.source_id,
            "job": row.job,
            "profileId": row.profile_id,
        }
        for row in identities
    }
    for row in snapshot.players:
        identity_by_source.setdefault(
            row.source_id,
            {"name": row.name or row.source_id, "job": row.job, "profileId": ""},
        )

    rows = tuple(snapshot.combat_events)
    direct_by_source: dict[str, list] = defaultdict(list)
    direct_times: dict[str, list[int]] = {}
    for row in rows:
        if _is_direct(row):
            direct_by_source[row.source_id].append(row)
    for source_id, direct_rows in direct_by_source.items():
        direct_rows.sort(key=lambda row: row.timestamp_ms)
        direct_times[source_id] = [row.timestamp_ms for row in direct_rows]

    reports: list[dict[str, object]] = []
    for source_id, identity in sorted(identity_by_source.items()):
        job = identity.get("job", "")
        if job not in FOCUS_JOBS:
            continue
        additional = [
            row for row in rows if row.source_id == source_id and _is_additional(row)
        ]
        methods = Counter((row.attribution_method for row in additional))
        method_damage = Counter()
        displayed_owner = Counter()
        displayed_owner_damage = Counter()
        raw_owner = Counter()
        raw_owner_damage = Counter()
        wire_layouts = Counter()
        trigger_grade = Counter()
        grade_owner = Counter()
        grade_owner_damage = Counter()
        amounts = Counter()
        ratios = Counter()
        nearest_skill = Counter()
        samples: list[dict[str, object]] = []
        expected_ratio = EXPECTED_PROC_RATIOS.get(job)
        ratio_match_rows = 0
        ratio_unique_skill_rows = 0
        ratio_skill = Counter()
        direct_rows = direct_by_source.get(source_id, [])
        times = direct_times.get(source_id, [])
        for row in additional:
            method_damage[row.attribution_method] += row.amount
            owner = (row.skill_id, row.skill_name)
            displayed_owner[owner] += 1
            displayed_owner_damage[owner] += row.amount
            raw = (row.raw_skill_id, row.raw_skill_name, row.raw_attribution_method)
            raw_owner[raw] += 1
            raw_owner_damage[raw] += row.amount
            wire_layouts[row.wire_damage_layout] += 1
            grade = (
                "exact"
                if row.trigger_attribution_confidence >= 0.95
                else "candidate"
                if row.trigger_skill_id or row.trigger_skill_name
                else "unlinked"
            )
            trigger_grade[grade] += 1
            grade_owner[(grade, row.skill_id, row.skill_name)] += 1
            grade_owner_damage[(grade, row.skill_id, row.skill_name)] += row.amount
            amounts[row.amount] += 1
            if not times:
                continue
            lo = bisect_left(times, row.timestamp_ms - 120)
            hi = bisect_right(times, row.timestamp_ms + 5)
            candidates = direct_rows[lo:hi]
            if not candidates:
                continue
            if (
                expected_ratio is not None
                and row.wire_damage_layout.startswith("s2c-udp-status-damage-")
            ):
                matched = [
                    hit
                    for hit in candidates
                    if abs(row.amount - hit.amount * expected_ratio)
                    <= max(10.0, row.amount * 0.005)
                ]
                if matched:
                    ratio_match_rows += 1
                    identities = {(hit.skill_id, hit.skill_name) for hit in matched}
                    if len(identities) == 1:
                        ratio_unique_skill_rows += 1
                        ratio_skill[next(iter(identities))] += 1
            nearest = min(
                candidates,
                key=lambda hit: (
                    abs(hit.timestamp_ms - row.timestamp_ms),
                    hit.timestamp_ms > row.timestamp_ms,
                ),
            )
            nearest_skill[(nearest.skill_id, nearest.skill_name)] += 1
            ratios[_round_ratio(row.amount, nearest.amount)] += 1
            if len(samples) < 40:
                samples.append(
                    {
                        "t": row.timestamp_ms,
                        "extra": row.amount,
                        "method": row.attribution_method,
                        "displayedOwner": row.skill_name,
                        "rawSkillId": row.raw_skill_id,
                        "wireLayout": row.wire_damage_layout,
                        "wireCritical": row.wire_critical,
                        "nearestDt": row.timestamp_ms - nearest.timestamp_ms,
                        "nearestSkill": nearest.skill_name,
                        "nearestDamage": nearest.amount,
                        "nearestCritical": nearest.critical,
                        "ratio": _round_ratio(row.amount, nearest.amount),
                        "sameTarget": row.target_id == nearest.target_id,
                    }
                )

        reports.append(
            {
                "sourceId": source_id,
                **identity,
                "additionalHits": len(additional),
                "additionalDamage": sum(row.amount for row in additional),
                "grade": dict(trigger_grade),
                "methods": [
                    {"method": key, "hits": hits, "damage": method_damage[key]}
                    for key, hits in methods.most_common()
                ],
                "gradeOwners": [
                    {
                        "grade": key[0],
                        "skillId": key[1],
                        "skillName": key[2],
                        "hits": hits,
                        "damage": grade_owner_damage[key],
                    }
                    for key, hits in grade_owner.most_common(60)
                ],
                "amounts": [
                    {"amount": amount, "hits": hits}
                    for amount, hits in amounts.most_common(30)
                ],
                "displayedOwners": [
                    {
                        "skillId": key[0],
                        "skillName": key[1],
                        "hits": hits,
                        "damage": displayed_owner_damage[key],
                    }
                    for key, hits in displayed_owner.most_common(30)
                ],
                "rawOwners": [
                    {
                        "rawSkillId": key[0],
                        "rawSkillName": key[1],
                        "rawMethod": key[2],
                        "hits": hits,
                        "damage": raw_owner_damage[key],
                    }
                    for key, hits in raw_owner.most_common(20)
                ],
                "wireLayouts": dict(wire_layouts),
                "nearestDirectSkills": [
                    {"skillId": key[0], "skillName": key[1], "hits": hits}
                    for key, hits in nearest_skill.most_common(30)
                ],
                "expectedRatioAudit": {
                    "ratio": expected_ratio,
                    "matchedRows": ratio_match_rows,
                    "uniqueSkillRows": ratio_unique_skill_rows,
                    "skills": [
                        {"skillId": key[0], "skillName": key[1], "hits": hits}
                        for key, hits in ratio_skill.most_common(40)
                    ],
                },
                "nearestRatios": [
                    {"ratio": ratio, "hits": hits}
                    for ratio, hits in ratios.most_common(40)
                ],
                "samples": samples,
            }
        )

    return {
        "capture": str(path),
        "decodedEventCount": len(decoded_events),
        "combatEventCount": len(rows),
        "identities": identity_by_source,
        "players": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.capture)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
