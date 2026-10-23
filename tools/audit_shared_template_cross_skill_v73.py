#!/usr/bin/env python3
"""Strict, read-only cross-skill audit for shared damage templates.

The report intentionally separates an *audited candidate* from a production
rule.  In particular, a target-complete incoming-creator census is required
before a static resource route is allowed to become a global runtime mapping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "outputs" / "under95_focus_v68_20260909" / "partials"

# Derived only from the finite positive resource routes in MagicGrass v38/v38
# scope.  It is deliberately not a whole-template 4405 rule.
STATIC_SELF_SLOT_CANDIDATES = {
    32012: {30810: 4405},
    32017: {30810: 4405, 30811: 4405},
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def stamp(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def is_current_entity_native(row: dict[str, Any], expected_slots: set[int]) -> bool:
    """Accept only a current owner generation and its native entity self slot."""
    native = row.get("primary90")
    return (
        row.get("entity_template") in STATIC_SELF_SLOT_CANDIDATES
        and native in expected_slots
        and row.get("self_skill") == native
        and row.get("owner_generation_current") is True
        and row.get("owner_generation_proof") == "34/2c"
    )


def fixture(row: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "physical_id", "capture", "capture_sha256", "packet_serial", "record_offset",
        "timestamp_ms", "job", "wire_source_actor_id", "owner_actor_id", "entity_template",
        "primary90", "self_skill", "owner_generation_current", "owner_generation_proof",
        "skill_id", "skill_name", "method", "deterministic",
    )
    return {key: row.get(key) for key in keep}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "outputs" / "shared_template_cross_skill_v73_20260910",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    inputs = {
        "magic_grass_scope": ROOT / "outputs" / "magic_grass_scope_v38_20260908" / "REPORT.md",
        "magic_grass_routes": ROOT / "outputs" / "magic_grass_census_v38_20260908" / "projectile_review.json",
        "v71": ROOT / "outputs" / "summon_root_generation_v71_20260909" / "REPORT.json",
        "registration": ROOT / "outputs" / "audit_remaining_registration_packet_20260907" / "handoff.json",
        "dark": ROOT / "outputs" / "dark_shaman_gaps_v65_20260909" / "report.json",
    }
    if not PARTIALS.is_dir() or any(not path.exists() for path in inputs.values()):
        raise SystemExit("required frozen audit input is missing")

    all_rows: list[dict[str, Any]] = []
    partial_hashes = []
    for path in sorted(PARTIALS.glob("*.json")):
        document = load(path)
        if not document.get("rows"):
            continue
        partial_hashes.append(stamp(path))
        all_rows.extend(document["rows"])

    # The current-generation / native-self-slot proof is intentionally much
    # narrower than "entity_template == X".
    target_entities = {32012, 32017, 32018}
    physician = [r for r in all_rows if r.get("entity_template") in target_entities]
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in physician:
        grouped[row["entity_template"]].append(row)

    physician_results = []
    fixtures: dict[str, list[dict[str, Any]]] = {}
    for template in sorted(target_entities):
        rows = grouped[template]
        candidate_slots = STATIC_SELF_SLOT_CANDIDATES.get(template, {})
        native = [r for r in rows if is_current_entity_native(r, set(candidate_slots))]
        no_native = [r for r in rows if r not in native]
        classes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in native:
            canonical = candidate_slots[row["primary90"]]
            if row.get("skill_id") != canonical:
                classes["cross_skill_correction_candidate"].append(row)
            elif row.get("deterministic"):
                classes["already_native_confirmed"].append(row)
            else:
                classes["closeable_current_label"].append(row)
        labels = Counter((r.get("skill_id"), r.get("skill_name")) for r in rows)
        result = {
            "template_id": template,
            "all_seen_hits": len(rows),
            "native_current_generation_hits": len(native),
            "no_native_or_missing_static_route_hits": len(no_native),
            "static_native_slot_to_skill": candidate_slots or None,
            "strict_closeable_hits": len(classes["closeable_current_label"]),
            "strict_correctable_hits": len(classes["cross_skill_correction_candidate"]),
            "already_confirmed_hits": len(classes["already_native_confirmed"]),
            "old_label_distribution": [
                {"skill_id": skill, "skill_name": name, "hits": count}
                for (skill, name), count in labels.most_common()
            ],
            "production_verdict": (
                "CANDIDATE_ONLY: finite positive resource routes support the listed self-slot pairs, but v38 explicitly lacks a target-complete incoming creator census."
                if candidate_slots else
                "WITHHOLD: no static player creator chain for this template was proved."
            ),
        }
        physician_results.append(result)
        for label, rows_for_label in classes.items():
            fixtures[f"{template}:{label}"] = [fixture(row) for row in rows_for_label[:8]]
        fixtures[f"{template}:not_promoted"] = [fixture(row) for row in no_native[:8]]

    # These shared projectile families have no raw native self slot in the
    # frozen partials.  Count them independently to make it impossible for a
    # caller to mistake current old labels for validated cross-skill evidence.
    object_targets = (5520, 11075, 5709, 5737)
    object_results = []
    for template in object_targets:
        rows = [
            row for row in all_rows
            if row.get("origin_template") == template or row.get("player_object_template") == template
        ]
        labels = Counter((r.get("skill_id"), r.get("skill_name")) for r in rows)
        native_key_rows = [r for r in rows if r.get("primary90") not in (None, 0, 0xFFFFFFFF)]
        object_results.append(
            {
                "template_id": template,
                "observed_rows": len(rows),
                "old_label_distribution": [
                    {"skill_id": skill, "skill_name": name, "hits": count}
                    for (skill, name), count in labels.most_common()
                ],
                "native_skill_key_hits": len(native_key_rows),
                "strict_closeable_hits": 0,
                "strict_correctable_hits": 0,
                "production_verdict": "WITHHOLD: no same-generation native key/self slot establishes the final player skill; old labels are not proof.",
            }
        )
        fixtures[f"{template}:representative"] = [fixture(row) for row in rows[:8]]

    dark = load(inputs["dark"])
    dark_projection = dark.get("projection", {})
    registration = load(inputs["registration"])
    v71 = load(inputs["v71"])
    route_doc = load(inputs["magic_grass_routes"])
    projection_physical_ids = sorted(
        fixture_row["physical_id"]
        for values in fixtures.values()
        for fixture_row in values
        if fixture_row.get("physical_id")
    )
    report = {
        "schema": "shared-template-cross-skill-v73",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read-only frozen partial audit; no packet replay; no production import",
        "production_changed": False,
        "strict_policy": {
            "required": [
                "current owner/entity generation",
                "native entity self slot or native player/object key",
                "static route for that exact template+slot pair",
                "same physical direct37 source",
            ],
            "forbidden": ["old label", "cast/action time window", "damage amount", "result group", "same-job cast"],
        },
        "physician": physician_results,
        "shared_projectile_families": object_results,
        "shadow_parent_generation_context": {
            "exact_generation_ACT_supported": registration.get("shadow_parent_status", {}).get("exact_generation_ACT_supported"),
            "raw_direct37": registration.get("shadow_parent_raw_hits"),
            "universal_parent_skill_inheritance": False,
            "why": "the retained 3023->3021 fixed-slot conflict forbids universal parent-skill inheritance",
        },
        "dark_shaman": {
            "baseline_hits": dark_projection.get("baseline_total_direct_hits"),
            "gap_hits": dark_projection.get("target_shortfall"),
            "strict_closeable_hits": 0,
            "strict_correctable_hits": 0,
            "verdict": "WITHHOLD: no additional same-generation native config/self-slot chain in the frozen evidence.",
        },
        "production_rules": [],
        "candidate_rules_not_promoted": [
            {
                "template": 32012,
                "self_skill": 30810,
                "canonical_skill": 4405,
                "reason": "needs target-complete incoming-creator census before promotion",
            },
            {
                "template": 32017,
                "self_skill": [30810, 30811],
                "canonical_skill": 4405,
                "reason": "needs target-complete incoming-creator census before promotion",
            },
        ],
        "conservation": {
            "replayed_hits": 0,
            "physical_events_mutated": 0,
            "projected_fixture_physical_id_count": len(projection_physical_ids),
            "projected_fixture_digest": hashlib.sha256("\n".join(projection_physical_ids).encode()).hexdigest(),
            "source_v71_invariants": v71.get("invariants"),
            "source_dark_physical_conservation": dark.get("physical_conservation"),
        },
        "inputs": {name: stamp(path) for name, path in inputs.items()} | {"frozen_partials": partial_hashes},
        "static_resource_summary": {
            "positive_projectile_routes": route_doc.get("positive_projectile_routes"),
            "remaining_gaps": route_doc.get("remaining"),
        },
    }
    report["report_sha256"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    validation = {
        "schema": "shared-template-cross-skill-v73-validation",
        "production_changed": False,
        "replayed_hits": 0,
        "physician_candidate_corrections": sum(row["strict_correctable_hits"] for row in physician_results),
        "shared_projectile_corrections": 0,
        "dark_shaman_corrections": 0,
        "report_sha256": report["report_sha256"],
    }
    md = [
        "# 共享模板跨技能纠错审计 v73", "",
        "本报告不把旧技能名当作证据，未修改生产规则。", "",
        "## 可纠错候选", "",
        "|模板|原生自攻击键|现有 Hit|可闭合|可纠错|是否上线|",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for row in physician_results:
        slots = ", ".join(map(str, (row["static_native_slot_to_skill"] or {}).keys())) or "—"
        md.append(
            f"|{row['template_id']}|{slots}|{row['all_seen_hits']}|{row['strict_closeable_hits']}|{row['strict_correctable_hits']}|{row['production_verdict']}|"
        )
    md += ["", "## 仍严格暂缓", "", "|模板|观察 Hit|可纠错 Hit|原因|", "|---:|---:|---:|---|"]
    for row in object_results:
        md.append(f"|{row['template_id']}|{row['observed_rows']}|0|缺少当前代独立原生技能键/自攻击槽；旧标签不参与结论。|")
    md += [
        f"|黑暗萨满缺口|{report['dark_shaman']['baseline_hits']} 总直伤|0|无新增独立原生链。|",
        "", "## 守恒", "",
        "本轮只投影标签变化：未重放抓包，未写入生产，因此物理 Hit、伤害、来源、目标、暴击、评分和时间轴均未改变。",
    ]
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "real_fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
