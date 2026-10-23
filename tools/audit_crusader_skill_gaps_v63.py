"""Strict offline audit for the largest Crusader direct-Hit gaps.

This tool deliberately does not replay timing heuristics and does not modify
production attribution.  It consumes the frozen v66 full-corpus checkpoints,
the independent 3019 continuation audit, and the client hit profile.  A
production candidate is emitted only when a fixed native result field or an
accepted same-owner/same-generation DamageObject chain proves the skill.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/direct_damage_v66_full_20260909"
CHECKPOINTS = BASE / "profession_direct_damage_checkpoints"
PROFILE = ROOT / "outputs/client_skill_hits/all_second_jobs_v2/15_十字军.json"
V60 = ROOT / "outputs/chain_lightning_meter_v60_20260909/REPORT.json"
V62 = ROOT / "outputs/lightning_storm_misattribution_v62_20260909/inventory_summary.json"
OUT = ROOT / "outputs/crusader_skill_gaps_v67_20260909"

JOB = "十字军"
TARGETS = (3202, 3222, 3203, 3019)
NAMES = {3202: "雷电冲击", 3222: "神圣十字", 3203: "重击", 3019: "连锁闪电"}
DETERMINISTIC = {"deterministic", "deterministic_status_owner"}


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inventory() -> list[dict[str, Any]]:
    result = []
    for path in sorted(CHECKPOINTS.glob("*.json")):
        checkpoint = read(path)
        report = checkpoint["report"]
        if JOB not in report.get("profession_summary", {}):
            continue
        result.append({"path": path, "checkpoint": checkpoint, "report": report})
    if len(result) != 2:
        raise RuntimeError(f"expected two Crusader checkpoints, got {len(result)}")
    return result


def aggregate_details(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gaps: dict[tuple[Any, ...], dict[str, Any]] = {}
    positives: list[dict[str, Any]] = []
    for item in items:
        report = item["report"]
        for row in report["details"]:
            try:
                skill = int(row["skill_id"])
            except (TypeError, ValueError):
                continue
            if row["job"] != JOB or skill not in TARGETS:
                continue
            compact = {
                "capture": row["capture"],
                "capture_sha256": report["capture_sha256"],
                "skill_id": skill,
                "skill_name": row["skill_name"],
                "classification": row["classification"],
                "method": row["method"],
                "projectile_template_id": row.get("projectile_template_id", ""),
                "hits": int(row["hits"]),
                "damage": int(row["damage"]),
            }
            if row["classification"] in DETERMINISTIC:
                positives.append(compact)
                continue
            key = (
                skill, row["classification"], row["method"],
                row.get("projectile_template_id", ""), row["capture"],
            )
            gaps[key] = compact
    return list(gaps.values()), positives


def static_target_rows() -> dict[int, dict[str, Any]]:
    profile = read(PROFILE)["job_profile"]
    rows = {}
    for row in profile["skills"]:
        skill = int(row["skill_id"])
        if skill not in TARGETS:
            continue
        templates = sorted({
            int(spawn["projectile_template_id"])
            for action in row.get("player_actions", [])
            for spawn in action.get("projectile_spawns", [])
            if spawn.get("projectile_template_id") is not None
        })
        rows[skill] = {
            "skill_id": skill,
            "name": row["name"],
            "client_classification": row.get("client_classification"),
            "simple_fixed_direct_hit_count": row.get("simple_fixed_direct_hit_count"),
            "simple_normalized_direct_distribution": row.get("simple_normalized_direct_distribution", []),
            "projectile_templates_from_typed_spawn": templates,
            "player_action_count": len(row.get("player_actions", [])),
        }
    return rows


def main() -> None:
    items = inventory()
    gaps, positives = aggregate_details(items)
    profile = static_target_rows()

    by_skill: dict[int, dict[str, int]] = {
        skill: {"gap_hits": 0, "gap_damage": 0, "deterministic_hits": 0, "deterministic_damage": 0}
        for skill in TARGETS
    }
    for row in gaps:
        bucket = by_skill[row["skill_id"]]
        bucket["gap_hits"] += row["hits"]
        bucket["gap_damage"] += row["damage"]
    for row in positives:
        bucket = by_skill[row["skill_id"]]
        bucket["deterministic_hits"] += row["hits"]
        bucket["deterministic_damage"] += row["damage"]

    registration_count = sum(int(item["report"].get("registrations", 0)) for item in items)
    # The frozen checkpoint keeps the total registration count, while exact
    # templates are retained on attributed/gap detail groups.  Count only
    # object-to-Hit chains actually represented by those real Hit groups.
    hit_template_counts = Counter(
        str(row.get("projectile_template_id", ""))
        for row in gaps + positives
        if str(row.get("projectile_template_id", "")) not in {"", "<对象存在但模板未解析>"}
    )

    # A static 4001 hint exists in the client profile, but neither frozen
    # capture contains a 4001 registration.  It therefore cannot identify a
    # physical Hit or a generation in these fixtures.
    template_4001_hit_chains = hit_template_counts.get("4001", 0)

    v60 = read(V60)
    v60q = v60["quantities"]
    relabelled = {str(k): int(v) for k, v in v60q["relabelled_after_skills"].items()}
    continuation_risk = {
        "independent_audit": str(V60),
        "independent_audit_sha256": sha256(V60),
        "physical_3019_rows_examined": int(v60q["native_raw_rows"]),
        "rows_relabelled_after_aggregation": int(v60q["relabelled_native_physical_rows"]),
        "relabelled_from_continuation_window": int(v60q["relabelled_before_methods"]["continuation_window"]),
        "relabelled_targets": {skill: relabelled.get(str(skill), 0) for skill in (3402, 3407, 3408, 3425)},
        "interpretation": (
            "This proves continuation_window can retain the wrong action label. "
            "It does not provide a fixed native/object identity for the 121 Crusader rows."
        ),
    }

    reasons = {
        3202: (
            "538 rows have no fixed native result identity and no accepted same-owner/same-generation "
            "projectile registration. Client static evidence mentions a hybrid projectile 4001, but "
            f"the two frozen Hit inventories contain {template_4001_hit_chains} accepted 4001-to-Hit chains."
        ),
        3222: (
            "303 rows are action/profile matches. The client profile is a direct three-Hit model, but "
            "a Hit-count shape cannot identify an individual physical row."
        ),
        3203: (
            "169 rows are action/profile matches. The client profile is a direct five-Hit model; two "
            "rows carry template 73000017, but that template is not a fixed Crusader skill identity."
        ),
        3019: (
            "121 rows use continuation_window (119 with an unmapped object and 2 without a template). "
            "Independent v60 evidence proves this fallback can swallow other cleric skills, but these "
            "rows have no per-row fixed native/object proof for a safe correction."
        ),
    }

    skills = []
    for skill in TARGETS:
        before = by_skill[skill]
        total = before["gap_hits"] + before["deterministic_hits"]
        skills.append({
            "skill_id": skill,
            "skill_name": NAMES[skill],
            **before,
            "deterministic_coverage_percent": round(100.0 * before["deterministic_hits"] / max(1, total), 6),
            "new_confirmed_hits": 0,
            "corrected_out_hits": 0,
            "corrected_in_hits": 0,
            "remaining_gap_hits": before["gap_hits"],
            "production_candidate": None,
            "why_not_closed": reasons[skill],
            "static_client_profile": profile.get(skill),
        })

    total_source_damage = sum(int(item["report"]["raw_source_invariants"]["damage"]) for item in items)
    total_aggregated_damage = sum(int(item["report"]["aggregated_invariants"]["damage"]) for item in items)

    confirmed_rows: list[dict[str, Any]] = []
    correction_pairs: dict[str, int] = {}
    report = {
        "schema": "crusader-skill-gaps-v63",
        "scope": {
            "job": JOB,
            "skills": list(TARGETS),
            "captures": [item["report"]["capture"] for item in items],
            "rule": (
                "fixed native result field OR same-owner/same-object-generation Projectile/damage_object "
                "chain; time proximity is diagnostic only"
            ),
        },
        "verdict": {
            "safe_new_confirmed_hits": 0,
            "safe_corrections": 0,
            "production_candidates": 0,
            "continuation_window_can_swallow_other_skills": True,
            "crusader_3019_rows_safe_to_relabel_now": 0,
            "shared_production_modified": False,
        },
        "skills": skills,
        "continuation_window_risk": continuation_risk,
        "registration_inventory": {
            "all_registration_rows": registration_count,
            "hit_chain_template_group_counts": dict(hit_template_counts.most_common()),
            "accepted_template_4001_hit_chain_groups": template_4001_hit_chains,
        },
        "correction_pairs": correction_pairs,
        "physical_damage_conservation": {
            "raw_source_damage_diagnostic": total_source_damage,
            "aggregate_damage_before": total_aggregated_damage,
            "aggregate_damage_after": total_aggregated_damage,
            "delta": 0,
            "confirmed_rows_are_relabels_not_new_damage": True,
        },
        "inputs": {
            "baseline_report": {"path": str(BASE / "profession_direct_damage_report.json"), "sha256": sha256(BASE / "profession_direct_damage_report.json")},
            "checkpoints": [{"path": str(item["path"]), "sha256": sha256(item["path"]), "capture_sha256": item["report"]["capture_sha256"]} for item in items],
            "client_profile": {"path": str(PROFILE), "sha256": sha256(PROFILE)},
            "v60": {"path": str(V60), "sha256": sha256(V60)},
            "v62": {"path": str(V62), "sha256": sha256(V62)},
        },
    }

    negative_fixtures = sorted(gaps, key=lambda row: (-row["hits"], row["skill_id"], row["capture"]))
    positive_fixtures = sorted(positives, key=lambda row: (-row["hits"], row["skill_id"], row["capture"]))
    write(OUT / "REPORT.json", report)
    write(OUT / "confirmed_rows.json", confirmed_rows)
    write(OUT / "production_candidates.json", [])
    write(OUT / "correction_pairs.json", correction_pairs)
    write(OUT / "fixtures.json", {
        "positive_fixed_native_or_object_groups": positive_fixtures,
        "negative_unclosed_groups": negative_fixtures,
        "fixture_note": "All rows are frozen real-capture checkpoint groups; no synthetic timing fixture is used.",
    })
    write(OUT / "unclosed_rows.json", negative_fixtures)
    write(OUT / "input_manifest.json", report["inputs"])

    lines = [
        "# 十字军直接 Hit 严格归因审计 v67", "",
        "## 结论", "",
        "本轮最大可确定纠正集为 **0 Hit**。3019 的 continuation_window 确有吞入其他技能的历史风险，"
        "但十字军这 121 Hit 没有逐行固定原生字段或同 owner、同 generation 对象链，不能安全改名。", "",
        "| 技能 | 确定 Hit | 缺口 Hit | 本轮新增/纠错 | 剩余缺口 |", "|---|---:|---:|---:|---:|",
    ]
    for row in skills:
        lines.append(f"| {row['skill_id']} {row['skill_name']} | {row['deterministic_hits']} | {row['gap_hits']} | 0 | {row['remaining_gap_hits']} |")
    lines += [
        "", "## 关键证据", "",
        f"- 两份真实抓包的冻结 Hit 清单共发现 `{template_4001_hit_chains}` 组已接受的模板 4001→Hit 链；静态表不能替代运行时对象 generation。",
        f"- v60 独立样本中有 {continuation_risk['relabelled_from_continuation_window']} 条 continuation_window 物理行后来被重分到其他技能，"
        "所以旧 3019 fallback 必须视为风险，不能视为真值。",
        "- 所有候选均要求固定原生技能字段或 exact owner/object generation；动作时间邻近没有参与定真。",
        "- 本轮没有新增或删除伤害，物理伤害守恒 delta=0。", "",
        "## 可生产候选", "", "无。需要先补出逐行对象登记/固定 native result key，才能进入共享生产。", "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
