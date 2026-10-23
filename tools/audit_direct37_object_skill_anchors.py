from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


ANCHOR_METHODS = frozenset(
    {
        "packet_skill_id",
        "direct_result_group_link",
        "damage_object_projectile_link",
    }
)


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    try:
        events = source.poll()
    finally:
        source.close()

    rows_by_object: dict[tuple[str, int], list[CombatEvent]] = defaultdict(list)
    for event in events:
        if (
            not isinstance(event, CombatEvent)
            or event.healing
            or event.wire_damage_object_id <= 0
            or event.has_real_hit_evidence is not True
        ):
            continue
        rows_by_object[(event.source_id, event.wire_damage_object_id)].append(
            event
        )

    anchored_objects = 0
    conflicted_objects = 0
    reusable_anchored_objects = 0
    reusable_hits = 0
    reusable_amount = 0
    unresolved_reusable_hits = 0
    unresolved_reusable_amount = 0
    blocker_methods: Counter[str] = Counter()
    anchor_methods: Counter[str] = Counter()
    skill_support: Counter[tuple[str, str]] = Counter()
    conflict_examples: list[dict[str, Any]] = []
    reusable_examples: list[dict[str, Any]] = []

    for (source_id, object_id), rows in rows_by_object.items():
        anchors = [
            row
            for row in rows
            if str(row.attribution_method or "").strip().casefold()
            in ANCHOR_METHODS
        ]
        identities = {(row.skill_id, row.skill_name) for row in anchors}
        if not identities:
            continue
        anchored_objects += 1
        for anchor in anchors:
            anchor_methods[
                str(anchor.attribution_method or "").strip().casefold()
            ] += 1
        if len(identities) != 1:
            conflicted_objects += 1
            if len(conflict_examples) < 30:
                conflict_examples.append(
                    {
                        "source_id": source_id,
                        "object_id": object_id,
                        "identities": sorted(
                            [list(identity) for identity in identities]
                        ),
                        "timestamps_ms": [row.timestamp_ms for row in rows],
                    }
                )
            continue

        skill_id, skill_name = next(iter(identities))
        candidates = [
            row
            for row in rows
            if not is_deterministic_attribution(row.attribution_method)
        ]
        if not candidates:
            continue
        reusable_anchored_objects += 1
        skill_support[(str(skill_id), str(skill_name))] += len(candidates)
        for row in candidates:
            reusable_hits += 1
            reusable_amount += row.amount
            method = str(row.attribution_method or "").strip().casefold()
            blocker_methods[method] += 1
            if row.skill_id in {"", "0", "unresolved"} or method in {
                "unresolved",
                "latest_profiled_direct_cast",
                "cast_instance_match",
                "continuation_window",
                "learned_cast_profile",
                "awaiting_damage_object_projectile_link",
            }:
                unresolved_reusable_hits += 1
                unresolved_reusable_amount += row.amount
        if len(reusable_examples) < 40:
            reusable_examples.append(
                {
                    "source_id": source_id,
                    "object_id": object_id,
                    "skill_id": str(skill_id),
                    "skill_name": str(skill_name),
                    "first_ms": min(row.timestamp_ms for row in rows),
                    "last_ms": max(row.timestamp_ms for row in rows),
                    "hits": len(rows),
                    "anchors": len(anchors),
                    "reusable_hits": len(candidates),
                    "candidate_methods": dict(
                        Counter(
                            str(row.attribution_method or "")
                            .strip()
                            .casefold()
                            for row in candidates
                        )
                    ),
                }
            )

    total_hits = sum(len(rows) for rows in rows_by_object.values())
    return {
        "capture": str(path),
        "decoded_events": len(events),
        "direct37_object_hits": total_hits,
        "direct37_objects": len(rows_by_object),
        "anchored_objects": anchored_objects,
        "conflicted_objects": conflicted_objects,
        "reusable_anchored_objects": reusable_anchored_objects,
        "reusable_hits": reusable_hits,
        "reusable_amount": reusable_amount,
        "unresolved_reusable_hits": unresolved_reusable_hits,
        "unresolved_reusable_amount": unresolved_reusable_amount,
        "anchor_methods": dict(anchor_methods.most_common()),
        "blocker_methods": dict(blocker_methods.most_common()),
        "top_reusable_skills": [
            {"skill_id": key[0], "skill_name": key[1], "hits": count}
            for key, count in skill_support.most_common(30)
        ],
        "conflict_examples": conflict_examples,
        "reusable_examples": reusable_examples,
    }


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    blocker_methods: Counter[str] = Counter()
    anchor_methods: Counter[str] = Counter()
    conflicts: list[dict[str, Any]] = []
    for report in reports:
        for key in (
            "decoded_events",
            "direct37_object_hits",
            "direct37_objects",
            "anchored_objects",
            "conflicted_objects",
            "reusable_anchored_objects",
            "reusable_hits",
            "reusable_amount",
            "unresolved_reusable_hits",
            "unresolved_reusable_amount",
        ):
            totals[key] += int(report[key])
        blocker_methods.update(report["blocker_methods"])
        anchor_methods.update(report["anchor_methods"])
        for example in report["conflict_examples"]:
            if len(conflicts) >= 50:
                break
            conflicts.append({"capture": report["capture"], **example})
    return {
        **dict(totals),
        "conflict_rate_percent": round(
            totals["conflicted_objects"]
            / max(1, totals["anchored_objects"])
            * 100.0,
            8,
        ),
        "anchor_methods": dict(anchor_methods.most_common()),
        "blocker_methods": dict(blocker_methods.most_common()),
        "conflict_examples": conflicts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit whether an exact direct37 skill anchor can safely label "
            "other rows carrying the same source-actor + damage-object ID."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path.resolve()) for path in args.captures]
    result = {
        "schema_version": 1,
        "policy": (
            "Only exact packet/result-group/projectile anchors participate. "
            "Actor and full damage-object ID are both required; any object "
            "with multiple exact identities fails closed."
        ),
        "summary": summarize(reports),
        "reports": reports,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
