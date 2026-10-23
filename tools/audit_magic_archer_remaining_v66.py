"""Re-audit Magic Archer gaps with finite object generations.

This offline tool supersedes the v63 assumption that the last same-numbered
object row remains usable forever.  Positive attribution requires a fixed
direct37 skill key or an unexpired, same-owner, same-object generation with a
validated Projectile mapping.  Cast proximity and current inferred labels
are never positive evidence.  Production files are not changed.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    CLIENT_ACT_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PACKET_VALIDATED_DAMAGE_OBJECT_PROJECTILE_TEMPLATE_IDS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS,
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from tools.audit_direct_damage_by_profession import (  # noqa: E402
    _raw_damage_invariants, _scope_reason, _strict_classification,
)
from tools.audit_magic_archer_skill_gaps_v63 import (  # noqa: E402
    CROSSBOW_NORMAL_ATTACK_ID, CROSSBOW_NORMAL_ATTACK_NAME,
    CROSSBOW_NORMAL_ATTACK_TEMPLATE_ID, DEFAULT_CAPTURE_ROOT, DEFAULT_CAPTURES,
    PARENTS_BY_CHILD, PRIMARY_09_SUBTYPES, SKILL_CATALOG, STATIC_VALIDATED,
    RawHit, Typed09, _decode_events, _event_key, _exact_parent_row,
    _mapping_for_template, _raw_hit_key, _scan_wire, _sha, _skill_name,
    _valid_fixed_skill,
)

SCHEMA = "magic-archer-remaining-v66"
DEFAULT_BASELINE = ROOT / "outputs/direct_damage_v64_incremental_20260909/profession_direct_damage_report.json"
DEFAULT_OUTPUT = ROOT / "outputs/magic_archer_remaining_v66_20260909"
SPEC_BY_TEMPLATE = {int(row.projectile_template_id): row for row in _DAMAGE_OBJECT_SPECS}
ALLOWED_CATALOG_JOBS = frozenset({"魔羽", "狙翎"})


def _skill(value: object) -> int:
    try:
        return int(str(value or "0"), 0)
    except ValueError:
        return -1


def _locate(event: CombatEvent, queues: dict[tuple[int, ...], deque[RawHit]]) -> RawHit | None:
    key = _event_key(event)
    if queues.get(key):
        return queues[key].popleft()
    for delta in (-1, 1):
        adjacent = (key[0] + delta, *key[1:])
        if queues.get(adjacent):
            return queues[adjacent].popleft()
    return None


def _generation(hit: RawHit, rows, positions) -> Typed09 | None:
    key = (hit.source_actor_id, hit.object_id)
    candidates = rows.get(key, ())
    if not candidates:
        return None
    index = bisect.bisect_right(positions[key], hit.position) - 1
    return candidates[index] if index >= 0 else None


def _decision(hit: RawHit, row: Typed09 | None, rows, positions):
    fixed, offset = _valid_fixed_skill(hit)
    if fixed:
        return "fixed_skill_slot", fixed, {"fixed_offset": offset}
    if row is None:
        return "no_pre_hit_generation", 0, {}
    spec = SPEC_BY_TEMPLATE.get(row.template_id)
    age = (hit.timestamp_us - row.timestamp_us) / 1_000_000.0
    meta = {
        "template_id": row.template_id,
        "age_seconds": round(age, 6),
        "source_layout": row.source_layout,
        "accepted": row.accepted_by_current_decoder,
        "lifetime_seconds": spec.lifetime_seconds if spec else None,
    }
    if age < 0:
        return "future_generation", 0, meta
    if spec is None:
        return "unmapped_template", 0, meta
    if age > float(spec.lifetime_seconds):
        return "expired_generation", 0, meta
    if row.template_id == CROSSBOW_NORMAL_ATTACK_TEMPLATE_ID:
        if row.accepted_by_current_decoder or row.subtype in (0x66, 0x6A):
            return "crossbow_normal_attack", -92, meta
        return "unaccepted_creation_layout", 0, meta
    if row.template_id not in STATIC_VALIDATED:
        return "provisional_template", 0, meta
    if not (row.accepted_by_current_decoder or row.subtype in PRIMARY_09_SUBTYPES):
        return "unaccepted_creation_layout", 0, meta
    canonical, aliases = _mapping_for_template(row.template_id)
    if not canonical or canonical == 0xFFFFFFFF:
        return "unmapped_template", 0, meta
    catalog = SKILL_CATALOG.get(canonical)
    if catalog and str(catalog[1]).strip() not in ALLOWED_CATALOG_JOBS:
        meta["canonical_job"] = str(catalog[1])
        return "cross_job_template_conflict", 0, meta
    if row.template_id in PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS:
        parent = _exact_parent_row(row, rows, positions)
        allowed = PARENTS_BY_CHILD.get(row.template_id, frozenset())
        parent_spec = SPEC_BY_TEMPLATE.get(parent.template_id) if parent else None
        parent_age = (
            (hit.timestamp_us - parent.timestamp_us) / 1_000_000.0 if parent else None
        )
        parent_canonical, parent_aliases = (
            _mapping_for_template(parent.template_id) if parent else (0, frozenset())
        )
        meta.update({
            "parent_template_id": parent.template_id if parent else 0,
            "parent_age_seconds": round(parent_age, 6) if parent_age is not None else None,
            "parent_lifetime_seconds": parent_spec.lifetime_seconds if parent_spec else None,
        })
        if not (
            parent is not None and parent.template_id in allowed
            and parent.accepted_by_current_decoder
            and parent_spec is not None and parent_age is not None
            and 0 <= parent_age <= float(parent_spec.lifetime_seconds)
            and parent_canonical == canonical and aliases.intersection(parent_aliases)
        ):
            return "parent_generation_invalid", 0, meta
    return "live_exact_object_chain", canonical, meta


def audit_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    decoded, wire = _decode_events(path), _scan_wire(path)
    queues: dict[tuple[int, ...], deque[RawHit]] = defaultdict(deque)
    for hit in wire["hits"]:
        queues[_raw_hit_key(hit, decoded["origin_us"])].append(hit)
    rows: dict[tuple[int, int], list[Typed09]] = defaultdict(list)
    for item in wire["objects"]:
        rows[(item.owner_actor_id, item.object_id)].append(item)
    positions = {}
    for key, values in rows.items():
        values.sort(key=lambda item: item.position)
        positions[key] = [item.position for item in values]

    counts: dict[int, Counter[str]] = defaultdict(Counter)
    proposals: Counter[tuple[int, int]] = Counter()
    fixtures: list[dict[str, Any]] = []
    fixture_keys = set()
    shadow = {}
    for event in decoded["events"]:
        if not isinstance(event, CombatEvent) or str(event.job or "").strip() != "魔羽":
            continue
        if _scope_reason(event) != "included_positive_direct_hit":
            continue
        current = _skill(event.skill_id)
        # Sentinel IDs remain unattributed even if the method itself is an
        # exact object link.  Include them so Projectile 92 can be audited as
        # the crossbow normal-attack family instead of disappearing behind a
        # method-name-only deterministic classification.
        if (
            _strict_classification(event) == "deterministic"
            and current not in (-1, 0xFFFFFFFF)
        ):
            continue
        hit = _locate(event, queues)
        counts[current]["gap_hits"] += 1
        counts[current][f"old_method:{event.attribution_method or 'unresolved'}"] += 1
        if hit is None:
            counts[current]["unmatched_physical"] += 1
            continue
        row = _generation(hit, rows, positions)
        decision, proposed, meta = _decision(hit, row, rows, positions)
        if decision in {"fixed_skill_slot", "live_exact_object_chain"}:
            category = "closeable" if proposed == current else "correctable"
            shadow[id(event)] = (str(proposed), _skill_name(proposed))
            proposals[(current, proposed)] += 1
        elif decision == "crossbow_normal_attack":
            category = "correctable_to_crossbow_normal_attack"
            shadow[id(event)] = (CROSSBOW_NORMAL_ATTACK_ID, CROSSBOW_NORMAL_ATTACK_NAME)
            proposals[(current, -92)] += 1
        else:
            category = decision
        counts[current][category] += 1
        key = (current, category, proposed, int(meta.get("template_id", 0)))
        if key not in fixture_keys and len(fixtures) < 100:
            fixture_keys.add(key)
            fixtures.append({
                "capture": path.name,
                "current_skill_id": current,
                "current_skill_name": str(event.skill_name or ""),
                "old_method": str(event.attribution_method or ""),
                "decision": category,
                "proposed_skill_id": proposed,
                "metadata": meta,
                "physical_direct37": asdict(hit),
                "object_generation": asdict(row) if row else None,
            })
    shadow_events = [
        replace(event, skill_id=shadow[id(event)][0], skill_name=shadow[id(event)][1])
        if id(event) in shadow else event for event in decoded["events"]
    ]
    before, after = _raw_damage_invariants(decoded["events"]), _raw_damage_invariants(shadow_events)
    if before != after:
        raise AssertionError(f"physical conservation failed: {path.name}")
    return {
        "capture": str(path), "sha256": _sha(path),
        "counts": {str(k): dict(v) for k, v in counts.items()},
        "proposals": [[src, dst, value] for (src, dst), value in proposals.items()],
        "fixtures": fixtures,
        "raw_source_invariants": decoded["raw_source_invariants"],
        "aggregated_invariants": decoded["aggregated_invariants"],
        "shadow_invariants": after,
    }


def merge(reports: list[dict[str, Any]], baseline_path: Path) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    summary = next(row for row in baseline["summary"] if row["职业"] == "魔羽")
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    proposals = Counter()
    for report in reports:
        for skill, values in report["counts"].items():
            counts[int(skill)].update(values)
        for src, dst, value in report["proposals"]:
            proposals[(int(src), int(dst))] += int(value)
    rows = []
    for skill in sorted(counts, key=lambda value: (-counts[value]["gap_hits"], value)):
        row = counts[skill]
        accepted = row["closeable"] + row["correctable"] + row["correctable_to_crossbow_normal_attack"]
        rows.append({
            "skill_id": skill,
            "skill_name": _skill_name(skill) if skill not in (-1, 4294967295) else (
                "待识别伤害" if skill == -1 else "弩系普通攻击"
            ),
            "gap_hits": row["gap_hits"], "closeable_hits": row["closeable"],
            "correctable_hits": row["correctable"],
            "crossbow_normal_attack_hits": row["correctable_to_crossbow_normal_attack"],
            "still_unclosed_hits": row["gap_hits"] - accepted,
            "decision_counts": dict(row),
        })
    added = sum(row["closeable_hits"] + row["correctable_hits"] + row["crossbow_normal_attack_hits"] for row in rows)
    total, old = int(summary["总直接Hit"]), int(summary["确定性已归因"])
    return {
        "schema": SCHEMA,
        "policy": {
            "positive": "fixed skill slot or live same-generation Projectile/entity chain",
            "forbidden": ["2.5-second action window", "time proximity", "old inferred label", "damage magnitude"],
            "production_modified": False,
        },
        "baseline": summary,
        "projection": {
            "new_closeable_or_correctable_hits": added,
            "projected_deterministic_hits": old + added,
            "projected_coverage_percent": round((old + added) * 100 / total, 6),
            "remaining_gap_hits": total - old - added,
        },
        "per_skill": rows,
        "proposal_matrix": [
            {"from_skill_id": src, "to_skill_id": dst, "hits": value}
            for (src, dst), value in proposals.most_common()
        ],
        "captures": reports,
        "conservation": {
            "all_capture_shadow_multisets_equal": all(
                report["aggregated_invariants"] == report["shadow_invariants"] for report in reports
            )
        },
    }


def render(result: dict[str, Any]) -> str:
    p = result["projection"]
    lines = [
        "# 魔羽剩余缺口有限对象代次复核 v66", "",
        f"- 本轮可安全闭合/纠错：`{p['new_closeable_or_correctable_hits']}` Hit。",
        f"- 投影覆盖率：`{p['projected_coverage_percent']:.6f}%`；剩余 `{p['remaining_gap_hits']}` Hit。",
        f"- 物理多重集守恒：`{result['conservation']['all_capture_shadow_multisets_equal']}`。",
        "- 共享生产代码未修改；2.5 秒动作窗口不作为证据。", "",
        "|当前技能桶|缺口|闭合同技能|纠错为其他技能|纠错为弩系普攻|仍未闭合|", "|---|---:|---:|---:|---:|---:|",
    ]
    for row in result["per_skill"]:
        lines.append(
            f"|{row['skill_name']} ({row['skill_id']})|{row['gap_hits']}|{row['closeable_hits']}|"
            f"{row['correctable_hits']}|{row['crossbow_normal_attack_hits']}|{row['still_unclosed_hits']}|"
        )
    lines += ["", "完整对象代次、生命周期、direct37 fixture 与决策见 `report.json`。", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    captures = [str((DEFAULT_CAPTURE_ROOT / name).resolve()) for name in DEFAULT_CAPTURES]
    reports = []
    with ProcessPoolExecutor(max_workers=max(1, min(args.workers, len(captures)))) as pool:
        futures = {pool.submit(audit_capture, path): path for path in captures}
        for future in as_completed(futures):
            reports.append(future.result())
    reports.sort(key=lambda row: row["capture"])
    result = merge(reports, args.baseline.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "REPORT.md").write_text(render(result), encoding="utf-8")
    print(json.dumps(result["projection"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
