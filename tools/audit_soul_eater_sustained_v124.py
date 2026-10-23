"""Generation-aware audit of Soul Eater sustained-hit fallbacks.

The old collision helper grouped every reuse of an object number together.
This audit instead joins each hit to the latest preceding actor + object
registration, so a later reuse cannot manufacture a cross-skill collision.
Timing is reported for diagnosis only and never changes a skill identity.
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
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


SPEC_BY_TEMPLATE = {
    int(spec.projectile_template_id): int(spec.canonical_skill_id)
    for spec in _DAMAGE_OBJECT_SPECS
}
LIFETIME_BY_TEMPLATE = {
    int(spec.projectile_template_id): float(spec.lifetime_seconds)
    for spec in _DAMAGE_OBJECT_SPECS
}
FOCUS_SKILLS = frozenset({5207, 5214, 5217})
WEAK_METHODS = frozenset(
    {
        "cast_instance_match",
        "continuation_window",
        "latest_profiled_direct_cast",
        "learned_cast_profile",
        "unique_active_cast",
        "sustained_cast_window",
        "damage_object_projectile_link_provisional",
    }
)


def _actor(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def audit(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    registrations_absolute: list[dict[str, Any]] = []
    original_registration = decoder.damage_object_origins.observe_registration
    original_feed_events = decoder.feed_events
    wire_context: dict[int, tuple[int, str]] = {}

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

    def feed_events(packet):
        emitted = tuple(original_feed_events(packet))
        signature = f"{len(packet.payload)}:{packet.payload[5:9].hex()}"
        for event in emitted:
            if isinstance(event, CombatEvent):
                wire_context[id(event)] = (len(packet.payload), signature)
        return emitted

    decoder.feed_events = feed_events
    try:
        events = source.poll()
    finally:
        source.close()

    origin = float(decoder.first_timestamp or 0.0)
    registrations: dict[tuple[int, int], list[dict[str, int]]] = defaultdict(list)
    for row in registrations_absolute:
        registrations[(int(row["actor"]), int(row["object"]))].append(
            {
                "timestamp_ms": max(
                    0, round((float(row["timestamp"]) - origin) * 1000.0)
                ),
                "template": int(row["template"]),
            }
        )
    for rows in registrations.values():
        rows.sort(key=lambda item: item["timestamp_ms"])

    casts: dict[tuple[int, int], list[int]] = defaultdict(list)
    for event in events:
        if not isinstance(event, SkillCastEvent) or event.job != "噬魂者":
            continue
        try:
            skill_id = int(event.skill_id)
        except (TypeError, ValueError):
            continue
        casts[(_actor(event.source_id), skill_id)].append(int(event.timestamp_ms))

    counts: Counter[tuple[str, int, int, int, str]] = Counter()
    all_weak_counts: Counter[tuple[int, int, int, str]] = Counter()
    all_weak_conflicts: Counter[tuple[int, int, int, str]] = Counter()
    conflicts: Counter[tuple[int, int, int, str]] = Counter()
    age_buckets: Counter[tuple[int, str]] = Counter()
    examples: list[dict[str, Any]] = []
    weak_signatures: Counter[tuple[int, str]] = Counter()
    deterministic_signatures: Counter[tuple[int, str, str]] = Counter()
    hits_per_cast: Counter[tuple[int, int, int]] = Counter()
    methods_per_cast: Counter[tuple[int, int, str]] = Counter()
    for event in events:
        if not isinstance(event, CombatEvent) or event.healing or event.job != "噬魂者":
            continue
        signature = wire_context.get(id(event), (0, "unknown"))[1]
        method = str(event.attribution_method or "")
        try:
            event_skill = int(event.skill_id)
        except (TypeError, ValueError):
            event_skill = -1
        if method == "continuation_window" and event_skill in FOCUS_SKILLS:
            weak_signatures[(event_skill, signature)] += 1
        elif method in {
            "damage_object_projectile_link",
            "packet_skill_id",
            "packet_entity_native_attack_key",
            "packet_entity_template_owner",
        }:
            deterministic_signatures[(event_skill, event.skill_name, signature)] += 1

        if event_skill in FOCUS_SKILLS:
            actor = _actor(event.source_id)
            cast_rows = casts.get((actor, event_skill), ())
            if cast_rows:
                index = bisect.bisect_right(
                    cast_rows, int(event.timestamp_ms)
                ) - 1
                if index >= 0:
                    cast_timestamp = int(cast_rows[index])
                    hits_per_cast[(event_skill, cast_timestamp, actor)] += 1
                    methods_per_cast[(event_skill, cast_timestamp, method)] += 1

        if method in WEAK_METHODS:
            actor = _actor(event.source_id)
            object_id = int(event.wire_damage_object_id or 0)
            template = 0
            rows = registrations.get((actor, object_id), ())
            if rows:
                timestamps = [row["timestamp_ms"] for row in rows]
                index = bisect.bisect_right(timestamps, int(event.timestamp_ms)) - 1
                if index >= 0:
                    row = rows[index]
                    age = int(event.timestamp_ms) - int(row["timestamp_ms"])
                    lifetime_ms = round(
                        LIFETIME_BY_TEMPLATE.get(int(row["template"]), 0.0)
                        * 1000.0
                    )
                    if 0 <= age <= lifetime_ms:
                        template = int(row["template"])
            mapped_skill = int(SPEC_BY_TEMPLATE.get(template, 0))
            all_weak_counts[(event_skill, template, mapped_skill, method)] += 1
            if mapped_skill and mapped_skill != event_skill:
                all_weak_conflicts[(event_skill, template, mapped_skill, method)] += 1

    for event in events:
        if not isinstance(event, CombatEvent) or event.healing or event.job != "噬魂者":
            continue
        try:
            skill_id = int(event.skill_id)
        except (TypeError, ValueError):
            continue
        if skill_id not in FOCUS_SKILLS:
            continue
        method = str(event.attribution_method or "")
        if method != "continuation_window":
            continue
        actor = _actor(event.source_id)
        object_id = int(event.wire_damage_object_id or 0)
        template = 0
        registration_age = -1
        rows = registrations.get((actor, object_id), ())
        if rows:
            timestamps = [row["timestamp_ms"] for row in rows]
            index = bisect.bisect_right(timestamps, int(event.timestamp_ms)) - 1
            if index >= 0:
                row = rows[index]
                registration_age = int(event.timestamp_ms) - int(row["timestamp_ms"])
                lifetime_ms = round(LIFETIME_BY_TEMPLATE.get(int(row["template"]), 0.0) * 1000.0)
                if 0 <= registration_age <= lifetime_ms:
                    template = int(row["template"])
        mapped_skill = int(SPEC_BY_TEMPLATE.get(template, 0))
        counts[(path.name, skill_id, template, mapped_skill, method)] += 1
        if mapped_skill and mapped_skill != skill_id:
            conflicts[(skill_id, template, mapped_skill, method)] += 1

        cast_rows = casts.get((actor, skill_id), ())
        cast_age = -1
        if cast_rows:
            index = bisect.bisect_right(cast_rows, int(event.timestamp_ms)) - 1
            if index >= 0:
                cast_age = int(event.timestamp_ms) - cast_rows[index]
        bucket = (
            "no_cast"
            if cast_age < 0
            else f"{(cast_age // 500) * 0.5:.1f}-{((cast_age // 500) + 1) * 0.5:.1f}s"
        )
        age_buckets[(skill_id, bucket)] += 1
        if len(examples) < 120 and (mapped_skill != skill_id or template == 0):
            examples.append(
                {
                    "capture": path.name,
                    "timestamp_ms": int(event.timestamp_ms),
                    "skill_id": skill_id,
                    "skill_name": event.skill_name,
                    "method": method,
                    "object_id": object_id,
                    "template": template,
                    "mapped_skill": mapped_skill,
                    "registration_age_ms": registration_age,
                    "cast_age_ms": cast_age,
                    "damage": int(event.amount),
                    "layout": event.wire_damage_layout,
                }
            )

    return {
        "capture": str(path),
        "continuation_rows": [
            {
                "skill_id": key[1],
                "template": key[2],
                "mapped_skill": key[3],
                "method": key[4],
                "hits": count,
            }
            for key, count in counts.most_common()
        ],
        "generation_exact_conflicts": [
            {
                "fallback_skill": key[0],
                "template": key[1],
                "object_skill": key[2],
                "method": key[3],
                "hits": count,
            }
            for key, count in conflicts.most_common()
        ],
        "all_weak_rows": [
            {
                "fallback_skill": key[0],
                "template": key[1],
                "object_skill": key[2],
                "method": key[3],
                "hits": count,
            }
            for key, count in all_weak_counts.most_common()
        ],
        "all_weak_generation_exact_conflicts": [
            {
                "fallback_skill": key[0],
                "template": key[1],
                "object_skill": key[2],
                "method": key[3],
                "hits": count,
            }
            for key, count in all_weak_conflicts.most_common()
        ],
        "cast_age_buckets": [
            {"skill_id": key[0], "age": key[1], "hits": count}
            for key, count in sorted(age_buckets.items())
        ],
        "weak_signatures": [
            {"skill_id": key[0], "signature": key[1], "hits": count}
            for key, count in weak_signatures.most_common()
        ],
        "same_signature_deterministic_skills": [
            {
                "skill_id": key[0], "skill_name": key[1],
                "signature": key[2], "hits": count,
            }
            for key, count in deterministic_signatures.most_common()
            if any(weak_signature == key[2] for _, weak_signature in weak_signatures)
        ],
        "cast_counts": [
            {
                "skill_id": skill_id,
                "casts": sum(len(rows) for (actor, sid), rows in casts.items() if sid == skill_id),
                "hit_count_distribution": dict(sorted(Counter(
                    count for (sid, _cast_at, _actor_id), count in hits_per_cast.items()
                    if sid == skill_id
                ).items())),
            }
            for skill_id in sorted(FOCUS_SKILLS)
        ],
        "per_cast_methods": [
            {
                "skill_id": key[0],
                "cast_timestamp_ms": key[1],
                "method": key[2],
                "hits": count,
            }
            for key, count in sorted(methods_per_cast.items())
        ],
        "examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [audit(path) for path in args.captures]
    result = {
        "schema": "soul-eater-sustained-audit-v124",
        "policy": "object generation first; timing is diagnostic only",
        "captures": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    for report in reports:
        print(Path(report["capture"]).name)
        print("rows", json.dumps(report["continuation_rows"], ensure_ascii=False))
        print("conflicts", json.dumps(report["generation_exact_conflicts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
