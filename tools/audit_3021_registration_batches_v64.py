"""Audit Projectile-3021 creation/state records and exact-parent siblings.

This is deliberately read-only: it wraps the in-memory origin ledger used by
diagnostic replay and records the arguments/result of every 3021 registration.
It does not promote a skill, change a Hit, or use damage/time similarity as
skill truth.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


def _audit(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    ledger = source._decoder.damage_object_origins
    original_register = ledger.observe_registration
    original_hit = ledger.attribute_direct37
    registrations: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []

    def register(**kwargs: Any):
        result = original_register(**kwargs)
        if int(kwargs.get("projectile_template_id", 0)) == 3021:
            registrations.append({
                "timestamp": round(float(kwargs["timestamp"]), 6),
                "packet_serial": int(kwargs.get("packet_serial", 0)),
                "record_offset": int(kwargs.get("record_offset", 0)),
                "owner": int(kwargs["source_actor_id"]),
                "object": int(kwargs["damage_object_id"]),
                "effect_resource": int(kwargs.get("effect_resource_id", 0)),
                "child_sequence": int(kwargs.get("child_sequence", 0)),
                "explicit_parent": int(kwargs.get("parent_damage_object_id", 0)),
                "state_observation": bool(kwargs.get("state_observation", False)),
                "parent_qualified_subtype": bool(
                    kwargs.get("parent_qualified_subtype", False)
                ),
                "packet_validated_subtype": bool(
                    kwargs.get("packet_validated_subtype", False)
                ),
                "action_qualified_subtype": bool(
                    kwargs.get("action_qualified_subtype", False)
                ),
                "registration_key_present": bool(kwargs.get("registration_key", "")),
                "accepted": result is not None,
                "generation": int(result.generation) if result else 0,
                "skill": int(result.canonical_skill_id) if result else 0,
                "parent_backed": bool(result.parent_backed) if result else False,
                "parent_object": int(result.parent_damage_object_id) if result else 0,
                "parent_generation": int(result.parent_generation) if result else 0,
                "parent_lineage_exact": bool(result.parent_lineage_exact) if result else False,
                "parent_root_action_epoch": (
                    int(result.parent_root_action_epoch_id) if result else 0
                ),
            })
        return result

    def hit(**kwargs: Any):
        result = original_hit(**kwargs)
        if result is not None and int(result.projectile_template_id) == 3021:
            origin, _ = ledger._origin_at(
                (int(result.owner_actor_id), int(result.damage_object_id)),
                float(kwargs["timestamp"]),
                int(kwargs.get("packet_serial", 0)),
                int(kwargs.get("record_offset", 0)),
            )
            if origin is not None:
                hits.append({
                    "timestamp": round(float(kwargs["timestamp"]), 6),
                    "packet_serial": int(kwargs.get("packet_serial", 0)),
                    "record_offset": int(kwargs.get("record_offset", 0)),
                    "owner": int(result.owner_actor_id),
                    "object": int(result.damage_object_id),
                    "generation": int(result.generation),
                    "damage": int(kwargs["damage"]),
                    "skill": int(result.skill_id),
                    "deterministic": bool(result.deterministically_validated),
                    "parent_object": int(origin.parent_damage_object_id),
                    "parent_generation": int(origin.parent_generation),
                    "parent_lineage_exact": bool(origin.parent_lineage_exact),
                    "effect_resource": int(origin.effect_resource_id),
                    "child_sequence": int(origin.child_sequence),
                })
        return result

    ledger.observe_registration = register
    ledger.attribute_direct37 = hit
    try:
        source.poll()
    finally:
        source.close()

    exact_by_parent: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in registrations:
        if row["accepted"] and row["parent_lineage_exact"]:
            exact_by_parent[(row["owner"], row["parent_object"], row["parent_generation"])].append(row)

    witness_counts = Counter()
    for row in hits:
        if row["deterministic"]:
            continue
        witnesses = exact_by_parent.get(
            (row["owner"], row["parent_object"], row["parent_generation"]), ()
        )
        prior = [w for w in witnesses if (
            w["packet_serial"], w["record_offset"]
        ) <= (row["packet_serial"], row["record_offset"])]
        before_birth = [w for w in prior if w["timestamp"] <= row["timestamp"]]
        same_resource = [w for w in before_birth if (
            w["effect_resource"] == row["effect_resource"]
        )]
        sequential = [w for w in before_birth if (
            abs(w["child_sequence"] - row["child_sequence"]) <= 1
        )]
        witness_counts[(bool(before_birth), bool(same_resource), bool(sequential))] += 1
        row["prior_exact_siblings"] = len(before_birth)
        row["same_resource_exact_siblings"] = len(same_resource)
        row["adjacent_sequence_exact_siblings"] = len(sequential)

    return {
        "capture": str(path),
        "registrations": registrations,
        "hits": hits,
        "summary": {
            "registrations": len(registrations),
            "accepted_registrations": sum(r["accepted"] for r in registrations),
            "exact_parent_registrations": sum(
                r["accepted"] and r["parent_lineage_exact"] for r in registrations
            ),
            "accepted_hits": len(hits),
            "provisional_hits": sum(not r["deterministic"] for r in hits),
            "provisional_witness_groups": [
                {
                    "has_prior_exact_sibling": key[0],
                    "same_resource": key[1],
                    "adjacent_sequence": key[2],
                    "hits": count,
                }
                for key, count in witness_counts.most_common()
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [_audit(path) for path in args.captures]
    result = {
        "schema": "projectile-3021-registration-batches-v64",
        "policy": "read-only; exact parent identity only; no proximity skill inference",
        "captures": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
