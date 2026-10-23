"""Audit Thunder's Holy Ground / Grand Cross object attribution windows.

This is an evidence-only audit.  A hit is related to a sustained skill only
when its actor + damage_object generation resolves to projectile template
4009 (Holy Ground) or 47 (Grand Cross).  Cast-time proximity is intentionally
not used to rename a hit.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


TEMPLATES = {4009: (3553, "神圣大地"), 47: (3401, "圣光十字")}
DETERMINISTIC_METHODS = {
    "damage_object_projectile_link",
    "packet_skill_id",
    "packet_entity_native_attack_key",
    "packet_entity_template_owner",
    "player_root_config_object",
}


def _actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def audit(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    registrations_absolute: list[dict[str, Any]] = []
    original_registration = decoder.damage_object_origins.observe_registration

    def observe_registration(**kwargs):
        registrations_absolute.append(
            {
                "actor": int(kwargs["source_actor_id"]),
                "object": int(kwargs["damage_object_id"]),
                "template": int(kwargs["projectile_template_id"]),
                "timestamp": float(kwargs["timestamp"]),
            }
        )
        return original_registration(**kwargs)

    decoder.damage_object_origins.observe_registration = observe_registration
    try:
        events = source.poll()
    finally:
        source.close()

    origin = float(decoder.first_timestamp or 0.0)
    registrations: dict[tuple[int, int], list[dict[str, int]]] = defaultdict(list)
    for row in registrations_absolute:
        converted = {
            "template": int(row["template"]),
            "timestamp_ms": max(0, round((float(row["timestamp"]) - origin) * 1000.0)),
        }
        registrations[(int(row["actor"]), int(row["object"]))].append(converted)
    for rows in registrations.values():
        rows.sort(key=lambda item: item["timestamp_ms"])

    ages: dict[int, list[int]] = defaultdict(list)
    related: Counter[tuple[int, str, str]] = Counter()
    collisions: Counter[tuple[int, str, str]] = Counter()
    unresolved_with_known_template: Counter[tuple[int, str]] = Counter()
    examples: list[dict[str, Any]] = []

    for event in events:
        if not isinstance(event, CombatEvent) or event.healing or event.job != "雷神":
            continue
        actor = _actor(event.source_id)
        object_id = int(event.wire_damage_object_id or 0)
        rows = registrations.get((actor, object_id), ())
        if not rows:
            continue
        timestamps = [row["timestamp_ms"] for row in rows]
        index = bisect.bisect_right(timestamps, int(event.timestamp_ms)) - 1
        if index < 0:
            continue
        registration = rows[index]
        template = int(registration["template"])
        if template not in TEMPLATES:
            continue
        age = int(event.timestamp_ms) - int(registration["timestamp_ms"])
        if age < 0 or age > 120_000:
            continue
        expected_skill, _ = TEMPLATES[template]
        actual_skill = int(event.skill_id) if str(event.skill_id).isdigit() else -1
        method = str(event.attribution_method or "unresolved")
        ages[template].append(age)
        related[(template, str(actual_skill), method)] += 1
        if actual_skill != expected_skill:
            collisions[(template, str(actual_skill), method)] += 1
            if len(examples) < 60:
                examples.append(
                    {
                        "capture": path.name,
                        "timestamp_ms": int(event.timestamp_ms),
                        "age_ms": age,
                        "source_id": event.source_id,
                        "object_id": object_id,
                        "template": template,
                        "expected_skill": expected_skill,
                        "actual_skill": actual_skill,
                        "actual_name": event.skill_name,
                        "method": method,
                        "damage": int(event.amount),
                    }
                )
        if method not in DETERMINISTIC_METHODS:
            unresolved_with_known_template[(template, method)] += 1

    stats = []
    for template, (skill_id, skill_name) in TEMPLATES.items():
        values = ages.get(template, [])
        stats.append(
            {
                "template": template,
                "skill_id": skill_id,
                "skill_name": skill_name,
                "hits": len(values),
                "age_ms": {
                    "min": min(values) if values else 0,
                    "p50": _percentile(values, 0.50),
                    "p95": _percentile(values, 0.95),
                    "p99": _percentile(values, 0.99),
                    "max": max(values) if values else 0,
                    "over_5s": sum(value > 5_000 for value in values),
                    "over_8s": sum(value > 8_000 for value in values),
                    "over_10s": sum(value > 10_000 for value in values),
                },
            }
        )
    return {
        "capture": str(path),
        "stats": stats,
        "related_routes": [
            {"template": key[0], "skill": key[1], "method": key[2], "hits": count}
            for key, count in related.most_common()
        ],
        "collisions": [
            {"template": key[0], "actual_skill": key[1], "method": key[2], "hits": count}
            for key, count in collisions.most_common()
        ],
        "nondeterministic_with_known_template": [
            {"template": key[0], "method": key[1], "hits": count}
            for key, count in unresolved_with_known_template.most_common()
        ],
        "examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "schema": "thunder-sustained-object-audit-v122",
        "rule": "actor + damage_object generation + exact projectile template; no cast-time renaming",
        "captures": [audit(path) for path in args.captures],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    for capture in result["captures"]:
        print(Path(capture["capture"]).name)
        print(json.dumps(capture["stats"], ensure_ascii=False))
        print("collisions", json.dumps(capture["collisions"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
