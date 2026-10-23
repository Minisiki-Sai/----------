"""Audit Guardian aura and Lightning Impact periodic damage object chains.

This tool is deliberately evidence-only.  It records raw object registrations
for Projectile rows 4001, 4014 and 4019, then joins direct37 records by the
full owner/object namespace and packet boundary.  It never names a Hit from
cast proximity or damage amount.
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
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    direct37_damage_object_id,
)
from dragon_nest_builder.live_dps.models import SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


TEMPLATES = {
    4001: (3202, "雷电冲击持续电击（普通模板）"),
    4014: (3202, "雷电冲击持续电击（EX模板）"),
    4019: (3306, "神圣惩戒EX持续光圈"),
}
CAST_SKILLS = frozenset({3202, 3205, 3306, 3355})


def _boundary(timestamp: float, packet_serial: int, record_offset: int) -> tuple[float, int, int]:
    return float(timestamp), int(packet_serial), int(record_offset)


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    ledger = source._decoder.damage_object_origins
    original_registration = ledger.observe_registration
    original_direct37 = ledger.attribute_direct37
    histories: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    registrations: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []

    def observe_registration(**kwargs: Any):
        template = int(kwargs.get("projectile_template_id", 0) or 0)
        if template in TEMPLATES:
            row = {
                "owner": int(kwargs.get("source_actor_id", 0) or 0),
                "object": int(kwargs.get("damage_object_id", 0) or 0),
                "template": template,
                "timestamp": float(kwargs.get("timestamp", 0.0) or 0.0),
                "packet_serial": int(kwargs.get("packet_serial", 0) or 0),
                "record_offset": int(kwargs.get("record_offset", 0) or 0),
                "parent_object": int(kwargs.get("parent_damage_object_id", 0) or 0),
                "effect_resource": int(kwargs.get("effect_resource_id", 0) or 0),
                "child_sequence": int(kwargs.get("child_sequence", 0) or 0),
                "registration_key": str(kwargs.get("registration_key", "") or ""),
                "packet_validated_subtype": bool(kwargs.get("packet_validated_subtype", False)),
                "action_qualified_subtype": bool(kwargs.get("action_qualified_subtype", False)),
                "parent_qualified_subtype": bool(kwargs.get("parent_qualified_subtype", False)),
            }
            registrations.append(row)
            histories[(row["owner"], row["object"])].append(row)
        return original_registration(**kwargs)

    def attribute_direct37(**kwargs: Any):
        result = original_direct37(**kwargs)
        if kwargs.get("_evidence_only", False) or kwargs.get("_post_admitted_late", False):
            return result
        owner = int(kwargs.get("source_actor_id", 0) or 0)
        obj = direct37_damage_object_id(
            kwargs["payload"],
            kwargs.get("direction"),
            source_actor_id=owner,
            damage=int(kwargs.get("damage", 0) or 0),
            record_offset=int(kwargs.get("record_offset", 0) or 0),
        )
        if obj is None:
            return result
        hit_boundary = _boundary(
            float(kwargs.get("timestamp", 0.0) or 0.0),
            int(kwargs.get("packet_serial", 0) or 0),
            int(kwargs.get("record_offset", 0) or 0),
        )
        candidates = [
            row for row in histories.get((owner, int(obj)), ())
            if _boundary(row["timestamp"], row["packet_serial"], row["record_offset"])
            <= hit_boundary
        ]
        if candidates:
            registration = max(
                candidates,
                key=lambda row: _boundary(row["timestamp"], row["packet_serial"], row["record_offset"]),
            )
            hits.append(
                {
                    "template": registration["template"],
                    "owner": owner,
                    "object": int(obj),
                    "timestamp": hit_boundary[0],
                    "packet_serial": hit_boundary[1],
                    "record_offset": hit_boundary[2],
                    "damage": int(kwargs.get("damage", 0) or 0),
                    "layout": str(kwargs.get("layout", "") or ""),
                    "age": round(hit_boundary[0] - registration["timestamp"], 6),
                    "current_skill": int(result.skill_id) if result is not None else 0,
                    "current_template": int(result.projectile_template_id) if result is not None else 0,
                    "current_deterministic": bool(
                        result is not None and result.deterministically_validated
                    ),
                }
            )
        return result

    ledger.observe_registration = observe_registration
    ledger.attribute_direct37 = attribute_direct37
    try:
        events = source.poll()
    finally:
        source.close()

    casts = Counter()
    for event in events:
        if isinstance(event, SkillCastEvent):
            try:
                skill = int(event.skill_id)
            except (TypeError, ValueError):
                continue
            if skill in CAST_SKILLS:
                casts[(str(event.job), skill)] += 1

    reg_summary = Counter((r["template"], r["action_qualified_subtype"], r["parent_qualified_subtype"])
                          for r in registrations)
    hit_summary = Counter((h["template"], h["current_skill"], h["current_deterministic"])
                          for h in hits)
    parent_summary = Counter((r["template"], bool(r["parent_object"]), r["parent_object"] == r["object"])
                             for r in registrations)
    age_summary: dict[str, dict[str, float | int]] = {}
    for template in TEMPLATES:
        ages = sorted(float(h["age"]) for h in hits if h["template"] == template)
        if ages:
            age_summary[str(template)] = {
                "hits": len(ages),
                "min": ages[0],
                "median": ages[len(ages) // 2],
                "p95": ages[min(len(ages) - 1, int(len(ages) * 0.95))],
                "max": ages[-1],
            }
    return {
        "capture": str(path),
        "casts": [
            {"job": key[0], "skill": key[1], "count": count}
            for key, count in sorted(casts.items())
        ],
        "registration_summary": [
            {
                "template": key[0],
                "action_qualified_subtype": key[1],
                "parent_qualified_subtype": key[2],
                "count": count,
            }
            for key, count in sorted(reg_summary.items())
        ],
        "parent_summary": [
            {
                "template": key[0],
                "has_parent": key[1],
                "self_parent": key[2],
                "count": count,
            }
            for key, count in sorted(parent_summary.items())
        ],
        "hit_summary": [
            {
                "raw_template": key[0],
                "current_skill": key[1],
                "current_deterministic": key[2],
                "hits": count,
            }
            for key, count in sorted(hit_summary.items())
        ],
        "age_summary": age_summary,
        "registration_samples": registrations[:40],
        "hit_samples": hits[:40],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path) for path in args.captures]
    result = {
        "schema": "paladin-persistent-damage-v117",
        "templates": {
            str(key): {"canonical_skill": value[0], "label": value[1]}
            for key, value in TEMPLATES.items()
        },
        "captures": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output)
    for report in reports:
        print(Path(report["capture"]).name)
        print(json.dumps({
            "casts": report["casts"],
            "registrations": report["registration_summary"],
            "parents": report["parent_summary"],
            "hits": report["hit_summary"],
            "ages": report["age_summary"],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
