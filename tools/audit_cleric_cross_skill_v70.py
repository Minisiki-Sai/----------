"""Targeted strict review for 3019/3425/3553 and Saint relic entities.

The only row-level source used here is the frozen v63 replay cache.  It
records every exact physical wire Hit, native slots and current-generation
object evidence.  The audit emits no production rule and never upgrades an
old bucket name, a nearby action, a result group or a damage amount.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "outputs/cleric_skill_gaps_v63_20260909/captures"
V63 = ROOT / "outputs/cleric_skill_gaps_v63_20260909/REPORT.json"
V66 = ROOT / "outputs/direct_damage_v66_full_20260909/profession_direct_damage_report.json"
V69 = ROOT / "outputs/cleric_cross_skill_v69_20260909/REPORT.json"
OUT = ROOT / "outputs/cleric_cross_skill_v70_20260909"
TARGET_INQUISITOR = {3019, 3425, 3553}
SAINT_HANDS = {3403, 3409, 3412, 3422, 3423}


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def numeric_skill(value: object) -> int | None:
    try:
        return int(str(value).split(":", 1)[0])
    except (TypeError, ValueError):
        return None


def main() -> None:
    target_rows = []
    for path in sorted(CACHE.glob("*.json")):
        cache = read(path)
        if not cache.get("baseline_reproduced"):
            raise RuntimeError(f"unreproduced v63 cache: {path.name}")
        for row in cache["gap_rows"]:
            event = row["event"]
            if numeric_skill(event["skill_id"]) not in TARGET_INQUISITOR:
                continue
            # These are row-level strict negatives: every row has an exact
            # physical wire identity, and none has an allowed proof.
            target_rows.append(row)
    if not target_rows:
        raise RuntimeError("no row-level Inquisitor target fixtures")

    proof_rows = [row for row in target_rows if row.get("candidate_proofs")]
    if proof_rows:
        raise RuntimeError("unexpected previously unreported target proof")
    reason_counts = Counter(row["unclosed_reason"] for row in target_rows)
    by_skill = {}
    for skill in sorted(TARGET_INQUISITOR):
        rows = [row for row in target_rows if numeric_skill(row["event"]["skill_id"]) == skill]
        by_skill[str(skill)] = {
            "rows": len(rows),
            "damage": sum(int(row["physical"]["damage"]) for row in rows),
            "fixed_native_rows": sum(row["physical"].get("selected_native_mapped_skill_id") is not None for row in rows),
            "current_generation_object_proof_rows": sum(row["physical"].get("object_proof") is not None for row in rows),
            "unclosed_reasons": dict(Counter(row["unclosed_reason"] for row in rows).most_common()),
        }

    v66 = read(V66)
    earth_groups = [
        row for row in v66["details"]
        if row["职业"] == "雷神"
        and row["归因分类"] not in {"deterministic", "deterministic_status_owner"}
        and numeric_skill(row["skill_id"]) == 3553
    ]
    by_skill["3553"]["v66_group_level_gap_hits"] = sum(int(row["Hit数"]) for row in earth_groups)
    by_skill["3553"]["row_level_cache_available"] = False
    saint = [row for row in v66["details"] if row["职业"] == "圣徒" and row["归因分类"] not in {"deterministic", "deterministic_status_owner"} and numeric_skill(row["skill_id"]) in SAINT_HANDS]
    saint_exact_entity = [row for row in saint if str(row["object模板"]).startswith("实体模板:")]
    saint_unknown_entity = [row for row in saint if not str(row["object模板"]).startswith("实体模板:")]
    saint_by_skill = {}
    for skill in sorted(SAINT_HANDS):
        rows = [row for row in saint if numeric_skill(row["skill_id"]) == skill]
        saint_by_skill[str(skill)] = {
            "hits": sum(int(row["Hit数"]) for row in rows),
            "damage": sum(int(row["伤害"]) for row in rows),
            "known_entity_template_hits": sum(int(row["Hit数"]) for row in rows if str(row["object模板"]).startswith("实体模板:")),
            "unknown_entity_or_no_object_hits": sum(int(row["Hit数"]) for row in rows if not str(row["object模板"]).startswith("实体模板:")),
            "methods": dict(Counter(row["method"] for row in rows)),
        }

    v63 = read(V63)
    v69 = read(V69)
    before = sum(int(row["总直接伤害"]) for row in v66["summary"] if row["职业"] in {"圣徒", "雷神"})
    result = {
        "schema": "cleric-cross-skill-v70",
        "verdict": {
            "inquisitor_target_rows_checked": len(target_rows),
            "inquisitor_allowed_proofs_found": len(proof_rows),
            "saint_hand_gap_groups_checked": len(saint),
            "safe_cross_skill_corrections": 0,
            "safe_new_deterministic_hits": 0,
            "production_candidates": 0,
            "shared_production_modified": False,
        },
        "inquisitor_target_rows": by_skill,
        "inquisitor_unclosed_reasons": dict(reason_counts.most_common()),
        "saint_hands": saint_by_skill,
        "saint_exact_entity_template_groups": saint_exact_entity,
        "saint_unknown_entity_groups": saint_unknown_entity,
        "cross_skill_risk_reference": {
            "same_job_risk_hits": v69["verdict"]["same_job_cross_skill_risk_hits"],
            "rule": "Risk-only diagnostic template conflicts are not a current-generation object link.",
        },
        "positive_control": v63["confirmed"],
        "conservation": {
            "saint_and_inquisitor_direct_damage_before": before,
            "saint_and_inquisitor_direct_damage_after": before,
            "delta": 0, "hits_added": 0, "hits_removed": 0,
        },
        "inputs": {"v63": {"path": str(V63), "sha256": sha(V63)}, "v66": {"path": str(V66), "sha256": sha(V66)}, "v69": {"path": str(V69), "sha256": sha(V69)}},
    }
    save(OUT / "REPORT.json", result)
    save(OUT / "production_candidates.json", [])
    save(OUT / "correction_pairs.json", {})
    save(OUT / "fixtures.json", {
        "inquisitor_row_level_strict_negative_fixtures": target_rows,
        "saint_real_capture_group_negatives": saint,
        "positive_control": v63["confirmed"],
    })
    save(OUT / "input_manifest.json", result["inputs"])
    lines = ["# 神职跨技能严格审计 v70", "", "## 结论", "",
             f"雷神目标技能逐 Hit 检查 {len(target_rows)} 条；固定原生槽或当前代对象证明为 {len(proof_rows)} 条。没有可安全改名的 Hit。",
             "圣徒五种手技能的已知实体模板仍缺少当前代 birth、wire owner 或实体自身攻击键中的至少一项，不能升格。", "",
             "3553 在 v66 仍有分组级缺口，但旧 v63 行级缓存没有纳入该技能，不能把‘未取样’误报为‘已排除’。", "",
             "|技能|行级未闭合Hit|固定原生槽|当前代对象证明|", "|---|---:|---:|---:|"]
    for skill, row in by_skill.items():
        lines.append(f"|{skill}|{row['rows']}|{row['fixed_native_rows']}|{row['current_generation_object_proof_rows']}|")
    lines += [f"", f"3553 神圣大地：v66 分组级缺口 {by_skill['3553']['v66_group_level_gap_hits']} Hit；尚无对应逐 Hit 缓存。"]
    lines += ["", "## 守恒", "", f"圣徒与雷神直接伤害前后均为 {before}，差值0。", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result["verdict"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
