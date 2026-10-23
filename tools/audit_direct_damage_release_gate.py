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
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    AttributionLayer,
    attribution_layer,
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


SCHEMA_VERSION = 3

# These labels mix explicit ownership with recent-caster/profile inference.
# They cannot independently certify ownership. Keep their hits in scope and
# retain the runtime display; only the audit classification is downgraded.
MIXED_OWNER_METHODS = frozenset(
    {
        "summon_owner",
        "supplemental_summon_owner",
        "retroactive_permanent_summon_owner",
        "skill_object_owner",
    }
)


def _is_explicitly_deferred_additional(event: CombatEvent) -> bool:
    skill_id = str(event.skill_id or "").strip().casefold()
    method = str(event.attribution_method or "").strip().casefold()
    return (
        skill_id.startswith(("supplemental:", "periodic:"))
        or method == "direct_hit_overflow"
    )


def _classification(event: CombatEvent) -> str:
    method = str(event.attribution_method or "unresolved").strip().casefold()
    if event.healing or event.source_id == "__incoming_damage__":
        return "not_outgoing_damage"
    if int(event.amount or 0) <= 0:
        return "nonhit_or_legacy_context"
    if _is_explicitly_deferred_additional(event):
        return "deferred_additional_or_nonhit"
    layer = attribution_layer(method)
    if is_deterministic_attribution(method):
        return "deterministic"
    if layer is AttributionLayer.STATUS:
        return "deterministic_status_owner"
    # A positive outgoing damage event with a normal skill ID is part of the
    # direct-damage completeness gate even when the old decoder could not set
    # ``has_real_hit_evidence``.  That flag describes missing evidence; it is
    # not proof that the damage is a supplemental proc and must not hide a
    # direct skill or summon hit from the audit.
    return "blocking_unproven_direct_hit"


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    try:
        events = source.poll()
    finally:
        source.close()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)

    classifications: Counter[str] = Counter()
    classification_damage: Counter[str] = Counter()
    blockers_by_method: Counter[str] = Counter()
    blocker_damage_by_method: Counter[str] = Counter()
    blockers_by_job: Counter[str] = Counter()
    blocker_damage_by_job: Counter[str] = Counter()
    blockers_by_skill: dict[tuple[str, str, str], Counter[str]] = defaultdict(
        Counter
    )
    total_outgoing_hits = 0
    total_outgoing_damage = 0
    for event in snapshot.combat_events:
        classification = _classification(event)
        if classification == "not_outgoing_damage":
            continue
        amount = max(0, int(event.amount))
        total_outgoing_hits += 1
        total_outgoing_damage += amount
        classifications[classification] += 1
        classification_damage[classification] += amount
        if classification != "blocking_unproven_direct_hit":
            continue
        method = str(event.attribution_method or "unresolved").strip().casefold()
        job = str(event.job or "未知职业").strip() or "未知职业"
        skill_id = str(event.skill_id or "-1").strip() or "-1"
        skill_name = str(event.skill_name or "待识别伤害").strip() or "待识别伤害"
        blockers_by_method[method] += 1
        blocker_damage_by_method[method] += amount
        blockers_by_job[job] += 1
        blocker_damage_by_job[job] += amount
        blockers_by_skill[(job, skill_id, skill_name)][method] += 1

    blocking_hits = classifications["blocking_unproven_direct_hit"]
    blocking_damage = classification_damage["blocking_unproven_direct_hit"]
    return {
        "capture": str(path),
        "event_count": len(events),
        "total_outgoing_hits": total_outgoing_hits,
        "total_outgoing_damage": total_outgoing_damage,
        "classification_hits": dict(classifications.most_common()),
        "classification_damage": dict(classification_damage.most_common()),
        "blocking_hits": blocking_hits,
        "blocking_damage": blocking_damage,
        "direct_attribution_coverage_percent": round(
            (total_outgoing_hits - blocking_hits)
            / max(1, total_outgoing_hits)
            * 100.0,
            6,
        ),
        "blocking_methods": {
            method: {
                "hits": hits,
                "damage": blocker_damage_by_method[method],
            }
            for method, hits in blockers_by_method.most_common()
        },
        "blocking_jobs": {
            job: {
                "hits": hits,
                "damage": blocker_damage_by_job[job],
            }
            for job, hits in blockers_by_job.most_common()
        },
        "blocking_skills": [
            {
                "job": key[0],
                "skill_id": key[1],
                "skill_name": key[2],
                "hits": sum(methods.values()),
                "methods": dict(methods.most_common()),
            }
            for key, methods in sorted(
                blockers_by_skill.items(),
                key=lambda row: (-sum(row[1].values()), row[0]),
            )
        ],
        "release_gate_passed": blocking_hits == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed audit for every observed direct outgoing damage hit."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path.resolve()) for path in args.captures]
    total_hits = sum(row["total_outgoing_hits"] for row in reports)
    total_damage = sum(row["total_outgoing_damage"] for row in reports)
    blocking_hits = sum(row["blocking_hits"] for row in reports)
    blocking_damage = sum(row["blocking_damage"] for row in reports)
    result = {
        "schema_version": SCHEMA_VERSION,
        "policy": {
            "scope": "positive observed outgoing damage",
            "requirement": (
                "Every positive normal-skill damage row must have a deterministic "
                "fixed packet field, packet-validated object bridge, entity owner "
                "or status owner. Result groups and timing inference never pass. "
                "Only explicit supplemental:/periodic: buckets and "
                "direct_hit_overflow remain outside this release gate."
            ),
        },
        "captures": len(reports),
        "total_outgoing_hits": total_hits,
        "total_outgoing_damage": total_damage,
        "blocking_hits": blocking_hits,
        "blocking_damage": blocking_damage,
        "coverage_percent": round(
            (total_hits - blocking_hits) / max(1, total_hits) * 100.0,
            6,
        ),
        "release_gate_passed": blocking_hits == 0,
        "reports": reports,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["release_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
