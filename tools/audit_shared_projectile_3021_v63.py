"""Audit every accepted Projectile-3021 Hit without changing attribution.

Template 3021 is shared by at least two runtime families.  This tool records
the exact evidence carried by each accepted generation so a provisional
display label is never promoted merely because it is common in the corpus.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


def _row(
    attr: Any,
    origin: Any,
    kwargs: dict[str, Any],
    native_slots: tuple[int, int] | None,
) -> dict[str, Any]:
    registration_key = str(origin.registration_key)
    return {
        "timestamp": round(float(kwargs["timestamp"]), 6),
        "packet_serial": int(kwargs.get("packet_serial", 0)),
        "record_offset": int(kwargs.get("record_offset", 0)),
        "owner": int(attr.owner_actor_id),
        "object": int(attr.damage_object_id),
        "generation": int(attr.generation),
        "damage": int(kwargs["damage"]),
        "skill": int(attr.skill_id),
        "deterministic": bool(attr.deterministically_validated),
        "action_backed": bool(attr.action_backed),
        "response_backed": bool(attr.response_backed),
        "packet_validated": bool(attr.packet_validated),
        "client_act_validated": bool(attr.client_act_validated),
        "action_qualified_validated": bool(attr.action_qualified_validated),
        "parent_lineage_validated": bool(attr.parent_lineage_validated),
        "packet_validated_subtype": bool(attr.packet_validated_subtype),
        "parent_action_lineage_validated": bool(
            attr.parent_action_lineage_validated
        ),
        "parent_root_action_epoch": int(attr.parent_root_action_epoch_id),
        "requires_root_action_proof": bool(attr.requires_root_action_proof),
        "registered_at": round(float(origin.registered_at), 6),
        "age_ms": round(
            (float(kwargs["timestamp"]) - float(origin.registered_at)) * 1000
        ),
        "registration_key_present": bool(registration_key),
        "registration_key_sha256": (
            hashlib.sha256(registration_key.encode("ascii")).hexdigest()
            if registration_key
            else ""
        ),
        "native_primary_raw": int(native_slots[0]) if native_slots else None,
        "native_fallback_raw": int(native_slots[1]) if native_slots else None,
        "effect_resource": int(origin.effect_resource_id),
        "child_sequence": int(origin.child_sequence),
        "parent_backed": bool(origin.parent_backed),
        "parent_template": int(origin.parent_projectile_template_id),
        "parent_object": int(origin.parent_damage_object_id),
        "parent_generation": int(origin.parent_generation),
        "parent_lineage_exact": bool(origin.parent_lineage_exact),
        "action_epoch": int(origin.action_epoch_id),
        "action_qualified_subtype": bool(origin.action_qualified_subtype),
        "parent_qualified_subtype": bool(origin.parent_qualified_subtype),
    }


def _audit(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    ledger = source._decoder.damage_object_origins
    original = ledger.attribute_direct37
    rows: list[dict[str, Any]] = []

    def wrapped(**kwargs: Any):
        attr = original(**kwargs)
        if (
            attr is not None
            and int(attr.projectile_template_id) == 3021
            and not kwargs.get("_evidence_only", False)
            and not kwargs.get("_post_admitted_late", False)
        ):
            origin, _snapshot = ledger._origin_at(  # audit-only exact lookup
                (int(attr.owner_actor_id), int(attr.damage_object_id)),
                float(kwargs["timestamp"]),
                int(kwargs.get("packet_serial", 0)),
                int(kwargs.get("record_offset", 0)),
            )
            if origin is None:
                raise AssertionError("accepted attribution lost its exact origin")
            native_slots = ledger._kali_11076_hit_slots(
                kwargs["payload"],
                int(attr.owner_actor_id),
                int(attr.damage_object_id),
                int(kwargs["damage"]),
                int(kwargs.get("record_offset", 0)),
            )
            rows.append(_row(attr, origin, kwargs, native_slots))
        return attr

    ledger.attribute_direct37 = wrapped
    try:
        events = source.poll()
    finally:
        source.close()

    emitted = Counter()
    for event in events:
        if (
            int(getattr(event, "wire_damage_object_id", 0) or 0) > 0
            and str(getattr(event, "attribution_method", ""))
            in {
                "damage_object_projectile_link",
                "damage_object_projectile_link_provisional",
            }
            and str(getattr(event, "skill_id", "")) in {"2205", "2207", "2353"}
        ):
            emitted[(
                str(event.skill_id), str(event.attribution_method)
            )] += 1

    dimensions = (
        "skill",
        "deterministic",
        "action_backed",
        "parent_backed",
        "parent_lineage_exact",
        "parent_template",
        "requires_root_action_proof",
        "parent_root_action_epoch",
        "registration_key_present",
        "native_primary_raw",
        "native_fallback_raw",
    )
    groups = Counter(tuple(row[name] for name in dimensions) for row in rows)
    return {
        "capture": str(path),
        "accepted_3021_hits": len(rows),
        "accepted_3021_damage": sum(int(row["damage"]) for row in rows),
        "emitted_selected_methods": {
            f"{skill}|{method}": count
            for (skill, method), count in sorted(emitted.items())
        },
        "dimensions": list(dimensions),
        "groups": [
            {**dict(zip(dimensions, key)), "hits": count}
            for key, count in groups.most_common()
        ],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [_audit(path) for path in args.captures]
    result = {
        "schema": "shared-projectile-3021-v63",
        "policy": (
            "read-only exact origin audit; proximity and current display labels "
            "are not deterministic skill truth"
        ),
        "captures": reports,
        "summary": {
            "captures": len(reports),
            "hits": sum(report["accepted_3021_hits"] for report in reports),
            "damage": sum(report["accepted_3021_damage"] for report in reports),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
