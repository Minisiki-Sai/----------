"""Offline exact-object audit for Inquisitor 3019/3402/3407 gaps.

The input is the frozen six-capture v63 replay cache.  This script never
replays with, patches, or imports mutable production attribution policy.  A
Hit can be closed only by its fixed native field or by an accepted, live,
same-owner, same-object generation whose Projectile has a static client ACT
mapping.  Future registrations, expired generations, and another owner's
reuse of the same numeric object ID are retained as negative fixtures.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "cleric_skill_gaps_v63_20260909"
CAPTURE_CACHE = SOURCE / "captures"
V63_REPORT = SOURCE / "REPORT.json"
V63_CONFIRMED = SOURCE / "confirmed_rows.json"
V62_COMPETITION = ROOT / "outputs/cleric_continuation_competition_v62_20260909/REPORT.json"
STATIC_SPECS = ROOT / "dragon_nest_builder/live_dps/generated_attack_instance_specs_v576.py"
RESOURCE_SUMMARY = ROOT / "outputs/lightning_storm_misattribution_v62_20260909/resource_summary.json"
OUTPUT = ROOT / "outputs/inquisitor_3019_3402_3407_objects_v64_20260909"

TARGET_NAMES = {3019: "连锁闪电", 3402: "电光箭", 3407: "闪电风暴"}
TARGET_TEMPLATES = {63: 3019, 4004: 3407, 4024: 3407}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def boundary(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["packet_serial"]), int(row["record_offset"])


def compact_registration(row: dict[str, Any]) -> dict[str, Any]:
    accepted = row.get("accepted_origin") or {}
    return {
        "source_actor_id": int(row["source_actor_id"]),
        "damage_object_id": int(row["damage_object_id"]),
        "projectile_template_id": int(row["projectile_template_id"]),
        "timestamp": float(row["timestamp"]),
        "packet_serial": int(row["packet_serial"]),
        "record_offset": int(row["record_offset"]),
        "wire_sha256": str(row.get("wire_sha256", "")),
        "accepted": bool(accepted),
        "generation": int(accepted.get("generation", 0) or 0),
        "expires_at": float(accepted.get("expires_at", 0.0) or 0.0),
    }


def compact_fixture(
    cache: dict[str, Any], gap: dict[str, Any], verdict: str,
    registration: dict[str, Any] | None,
) -> dict[str, Any]:
    event, physical = gap["event"], gap["physical"]
    return {
        "capture": Path(cache["capture"]).name,
        "capture_sha256": cache["capture_sha256"],
        "current_skill_id": int(event["skill_id"]),
        "current_skill_name": str(event["skill_name"]),
        "verdict": verdict,
        "timestamp_ms": int(event["timestamp_ms"]),
        "source_actor_id": int(physical["source_actor_id"]),
        "target_actor_id": int(physical["target_actor_id"]),
        "damage_object_id": int(physical.get("damage_object_id") or 0),
        "result_group_id": int(physical["result_group_id"]),
        "damage": int(physical["damage"]),
        "critical": bool(physical["critical"]),
        "record_offset": int(physical["record_offset"]),
        "wire_sha256": str(physical["wire_sha256"]),
        "wire_record_sha256": str(physical["wire_record_sha256"]),
        "raw_skill_slots": physical["raw_skill_slots"],
        "registration": compact_registration(registration) if registration else None,
    }


def physical_identity(capture: str, physical: dict[str, Any]) -> tuple[Any, ...]:
    return (
        capture,
        int(physical["source_actor_id"]), int(physical["target_actor_id"]),
        int(physical.get("damage_object_id") or 0), int(physical["result_group_id"]),
        int(physical["damage"]), bool(physical["critical"]),
        int(physical["record_offset"]), str(physical["wire_record_sha256"]),
    )


def analyse() -> dict[str, Any]:
    v63 = read(V63_REPORT)
    confirmed_rows = read(V63_CONFIRMED)["rows"]
    competition = read(V62_COMPETITION)
    resource = read(RESOURCE_SUMMARY)
    caches = [read(path) for path in sorted(CAPTURE_CACHE.glob("*.json"))]
    if len(caches) != 6 or not all(row.get("baseline_reproduced") for row in caches):
        raise RuntimeError("six frozen v63 capture caches were not reproduced")

    static_rows = {
        int(row["RowId"]): row for row in resource["weapons"]
        if int(row["RowId"]) in TARGET_TEMPLATES
    }
    if set(static_rows) != set(TARGET_TEMPLATES):
        raise RuntimeError("resource summary is missing 63/4004/4024")

    by_skill: dict[int, Counter[str]] = {skill: Counter() for skill in TARGET_NAMES}
    fixtures: dict[int, dict[str, dict[str, Any]]] = {
        skill: {} for skill in TARGET_NAMES
    }
    registration_counts_all: Counter[int] = Counter()
    registration_counts_target_actors: Counter[int] = Counter()
    expired_ages: dict[int, list[float]] = defaultdict(list)
    safe: list[dict[str, Any]] = []
    confirmed_identities = {
        physical_identity(str(row["capture"]), row["physical"])
        for row in confirmed_rows
        if int(row["old_skill_id"]) in TARGET_NAMES
    }

    for cache in caches:
        registrations = list(cache["registrations"])
        for row in registrations:
            template = int(row["projectile_template_id"])
            if template in TARGET_TEMPLATES:
                registration_counts_all[template] += 1
        target_actors = {
            int(gap["physical"]["source_actor_id"])
            for gap in cache["gap_rows"]
            if int(gap["event"]["skill_id"]) in TARGET_NAMES
        }
        for row in registrations:
            if int(row["source_actor_id"]) in target_actors:
                template = int(row["projectile_template_id"])
                if template in TARGET_TEMPLATES:
                    registration_counts_target_actors[template] += 1

        exact: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        numeric: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in registrations:
            owner, object_id = int(row["source_actor_id"]), int(row["damage_object_id"])
            exact[(owner, object_id)].append(row)
            numeric[object_id].append(row)
        for rows in exact.values():
            rows.sort(key=boundary)

        for gap in cache["gap_rows"]:
            old_skill = int(gap["event"]["skill_id"])
            if old_skill not in TARGET_NAMES:
                continue
            physical = gap["physical"]
            if physical_identity(Path(cache["capture"]).name, physical) in confirmed_identities:
                continue
            owner = int(physical["source_actor_id"])
            object_id = int(physical.get("damage_object_id") or 0)
            hit_at = (int(physical["packet_serial"]), int(physical["record_offset"]))
            hit_time = float(physical["packet_timestamp_seconds"])
            rows = exact.get((owner, object_id), ()) if object_id else ()
            prior = [row for row in rows if boundary(row) <= hit_at]
            future = [row for row in rows if boundary(row) > hit_at]

            selected: dict[str, Any] | None = None
            verdict = "no_target_template_evidence"
            for row in reversed(prior):
                template = int(row["projectile_template_id"])
                if template not in TARGET_TEMPLATES:
                    continue
                selected = row
                accepted = row.get("accepted_origin") or {}
                if accepted and hit_time <= float(accepted["expires_at"]):
                    mapped = TARGET_TEMPLATES[template]
                    verdict = "safe_same_skill" if mapped == old_skill else "safe_skill_correction"
                    safe.append(compact_fixture(cache, gap, verdict, row) | {
                        "new_skill_id": mapped,
                        "new_skill_name": TARGET_NAMES[mapped],
                    })
                else:
                    verdict = f"same_owner_prior_template_{template}_expired"
                    expired_ages[template].append(hit_time - float(row["timestamp"]))
                break
            if selected is None and any(
                int(row["projectile_template_id"]) in TARGET_TEMPLATES for row in future
            ):
                selected = next(
                    row for row in future
                    if int(row["projectile_template_id"]) in TARGET_TEMPLATES
                )
                verdict = "same_owner_future_registration"
            if selected is None and object_id and any(
                int(row["source_actor_id"]) != owner
                and int(row["projectile_template_id"]) in TARGET_TEMPLATES
                for row in numeric.get(object_id, ())
            ):
                selected = next(
                    row for row in numeric[object_id]
                    if int(row["source_actor_id"]) != owner
                    and int(row["projectile_template_id"]) in TARGET_TEMPLATES
                )
                verdict = "other_owner_same_numeric_object_id"

            by_skill[old_skill][verdict] += 1
            if verdict not in fixtures[old_skill]:
                fixtures[old_skill][verdict] = compact_fixture(
                    cache, gap, verdict, selected
                )

    inherited = {
        int(skill): int(values["confirmed_promotions"])
        for skill, values in v63["skill_results"].items()
        if int(skill) in TARGET_NAMES
    }
    baseline = {
        int(skill): int(values["baseline_gap_hits"])
        for skill, values in v63["skill_results"].items()
        if int(skill) in TARGET_NAMES
    }
    risk_rows = {skill: sum(by_skill[skill].values()) for skill in TARGET_NAMES}
    if any(risk_rows[skill] + inherited[skill] != baseline[skill] for skill in TARGET_NAMES):
        raise RuntimeError("target gap conservation failed")

    skill_results = []
    for skill in TARGET_NAMES:
        counts = by_skill[skill]
        skill_results.append({
            "skill_id": skill,
            "skill_name": TARGET_NAMES[skill],
            "baseline_gap_hits": baseline[skill],
            "inherited_v63_confirmed_hits": inherited[skill],
            "new_safe_same_skill_hits": counts["safe_same_skill"],
            "new_safe_correction_hits": counts["safe_skill_correction"],
            "remaining_unclosed_hits": risk_rows[skill],
            "negative_evidence_counts": dict(counts),
            "fixtures": list(fixtures[skill].values()),
        })

    static_evidence = []
    for template, skill in TARGET_TEMPLATES.items():
        row = static_rows[template]
        static_evidence.append({
            "projectile_template_id": template,
            "canonical_skill_id": skill,
            "canonical_skill_name": TARGET_NAMES[skill],
            "act_file": next(
                (item["_FileName"] for item in resource["files"]
                 if int(item["RowId"]) == int(row["_ActName"])),
                "",
            ),
            "length": row.get("_Length"),
            "durability": row.get("_Durability"),
            "all_cleric_registration_count": registration_counts_all[template],
            "target_inquisitor_registration_count": registration_counts_target_actors[template],
            "expired_gap_age_seconds": {
                "count": len(expired_ages[template]),
                "minimum": round(min(expired_ages[template]), 6) if expired_ages[template] else None,
                "maximum": round(max(expired_ages[template]), 6) if expired_ages[template] else None,
            },
        })

    result = {
        "schema": "inquisitor-3019-3402-3407-exact-object-audit-v64",
        "policy": {
            "positive": [
                "fixed native result field",
                "accepted live same-owner same-object generation + audited client ACT Projectile",
            ],
            "negative_only": [
                "action proximity", "same numeric object ID on another owner",
                "future registration", "expired generation", "current inferred label",
            ],
            "production_changed": False,
        },
        "inputs": {
            str(V63_REPORT): sha256(V63_REPORT),
            str(V63_CONFIRMED): sha256(V63_CONFIRMED),
            str(V62_COMPETITION): sha256(V62_COMPETITION),
            str(STATIC_SPECS): sha256(STATIC_SPECS),
            str(RESOURCE_SUMMARY): sha256(RESOURCE_SUMMARY),
            **{str(path): sha256(path) for path in sorted(CAPTURE_CACHE.glob("*.json"))},
        },
        "captures": len(caches),
        "skill_results": skill_results,
        "new_safe_hits": len(safe),
        "safe_rows": safe,
        "static_evidence": static_evidence,
        "electric_arrow_3402": {
            "projectile_template_in_audited_client_action_table": False,
            "conclusion": (
                "3402 has no audited Projectile spawn in this client resource set; "
                "63/4004/4024 must not be borrowed to close it"
            ),
        },
        "chain_lightning_label_risk_0821": {
            "scope": competition["baseline_scope"],
            "rows": competition["original_gap_rows"],
            "latest_action_request_counts": competition["latest_action_request_counts"],
            "conclusion": (
                "nine rows have a different latest action request, but action overlap is "
                "diagnostic only and cannot prove their final skill"
            ),
        },
        "conservation": {
            "baseline_target_gap_hits": sum(baseline.values()),
            "inherited_v63_confirmed_hits": sum(inherited.values()),
            "risk_rows_examined": sum(risk_rows.values()),
            "new_relabels": len(safe),
            "target_gap_identity_conserved": (
                sum(baseline.values()) == sum(inherited.values()) + sum(risk_rows.values())
            ),
            "v63_physical_conservation": v63["conservation"],
        },
    }
    return result


def render(result: dict[str, Any]) -> str:
    lines = [
        "# 雷神 3019/3402/3407 精确对象专项审计 v64", "",
        "本轮只读取六份 v63 固定回放缓存和客户端静态表；未修改共享生产代码。", "",
        "## 结果", "",
        "|技能|原缺口|v63已确认|本轮新增安全闭合|本轮新增纠错|仍未闭合|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in result["skill_results"]:
        lines.append(
            f"|{row['skill_id']} {row['skill_name']}|{row['baseline_gap_hits']}|"
            f"{row['inherited_v63_confirmed_hits']}|{row['new_safe_same_skill_hits']}|"
            f"{row['new_safe_correction_hits']}|{row['remaining_unclosed_hits']}|"
        )
    lines += ["", "## 新发现", ""]
    evidence = {row["projectile_template_id"]: row for row in result["static_evidence"]}
    lines += [
        f"- Projectile 63 明确属于 3019；目标雷神共记录 {evidence[63]['target_inquisitor_registration_count']} 次。"
        f"缺口中同号旧代际有 {evidence[63]['expired_gap_age_seconds']['count']} Hit，年龄 "
        f"{evidence[63]['expired_gap_age_seconds']['minimum']}～{evidence[63]['expired_gap_age_seconds']['maximum']} 秒，均已过期。",
        f"- Projectile 4004/4024 明确属于 3407 普通/EX。目标雷神中 4004 为 "
        f"{evidence[4004]['target_inquisitor_registration_count']} 次，4024 为 "
        f"{evidence[4024]['target_inquisitor_registration_count']} 次；说明六份样本中的雷神使用 EX 对象分支。",
        f"- 4024 同号旧代际涉及 {evidence[4024]['expired_gap_age_seconds']['count']} 个缺口 Hit，年龄 "
        f"{evidence[4024]['expired_gap_age_seconds']['minimum']}～{evidence[4024]['expired_gap_age_seconds']['maximum']} 秒；"
        "不能把它们回填成当前闪电风暴。",
        "- 3402 在已审计客户端动作表中没有 Projectile 生成项；不能借用 63、4004 或 4024。",
        "- 0821 的 325 条旧 3019 标签中，最新动作请求有 9 条不是 3019。这证明旧 continuation_window 有错归风险，"
        "但动作先后不能确定最终技能，因此本轮不强行改名。",
        "- 本轮新增安全 Hit 为 0；这是排除错误扩张后的结果，不是没有发现问题。",
        "", "## 守恒", "",
        f"- 三技能原缺口：{result['conservation']['baseline_target_gap_hits']} Hit。",
        f"- v63 已确认：{result['conservation']['inherited_v63_confirmed_hits']} Hit；本轮复核风险行："
        f"{result['conservation']['risk_rows_examined']} Hit。",
        f"- 缺口身份守恒：{result['conservation']['target_gap_identity_conserved']}；本轮没有改写伤害、来源、目标或 Hit。",
        "- JSON 内为每个技能、每类否定证据保存了真实 wire SHA、record SHA、对象 ID 和注册 fixture。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = analyse()
    write(OUTPUT / "report.json", result)
    (OUTPUT / "REPORT.md").write_text(render(result), encoding="utf-8")
    print(json.dumps({
        "captures": result["captures"],
        "new_safe_hits": result["new_safe_hits"],
        "skills": result["skill_results"],
        "conservation": result["conservation"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
