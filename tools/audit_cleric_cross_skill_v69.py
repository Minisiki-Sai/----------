"""Classify Cleric cross-skill and summon-child gap evidence.

No production attribution is changed.  The source is the frozen v66 corpus
plus v63 per-Hit negative reasons.  A diagnostic template attached by the
corpus reporter is explicitly not treated as a current generation join.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.damage_object_origins import _SPEC_BY_PROJECTILE_TEMPLATE

V66 = ROOT / "outputs/direct_damage_v66_full_20260909/profession_direct_damage_report.json"
V63 = ROOT / "outputs/cleric_skill_gaps_v63_20260909/REPORT.json"
V68 = ROOT / "outputs/cleric_four_jobs_v68_20260909/REPORT.json"
OUT = ROOT / "outputs/cleric_cross_skill_v69_20260909"
JOBS = ("十字军", "圣骑士", "圣徒", "雷神")
ENTITY_SKILLS = {
    30001: 3409, 30501: 3409,
    30152: 3403, 30251: 3422, 30311: 3423, 30401: 3412,
}
JOB_SKILLS = {
    "十字军": set(range(3000, 3600)), "圣骑士": set(range(3000, 3600)),
    "圣徒": set(range(3000, 3600)), "雷神": set(range(3000, 3600)),
}


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


def skill(value: object) -> int | None:
    try:
        return int(str(value).split(":", 1)[0])
    except (TypeError, ValueError):
        return None


def main() -> None:
    v66, v63, v68 = read(V66), read(V63), read(V68)
    rows = [row for row in v66["details"] if row["职业"] in JOBS and row["归因分类"] not in {"deterministic", "deterministic_status_owner"}]
    cross = []
    summon = []
    ordinary = []
    for row in rows:
        compact = {
            "job": row["职业"], "old_skill_id": row["skill_id"], "old_skill_name": row["技能名"],
            "method": row["method"], "diagnostic_template": row["object模板"],
            "hits": int(row["Hit数"]), "damage": int(row["伤害"]),
            "capture_count": int(row["抓包数"]), "example_captures": row["示例抓包"],
        }
        old = skill(row["skill_id"])
        template_text = str(row["object模板"])
        if template_text.startswith("实体模板:"):
            template = int(template_text.split(":", 1)[1])
            expected = ENTITY_SKILLS.get(template)
            summon.append(compact | {
                "entity_template_id": template, "static_unique_skill_id": expected,
                "same_skill_as_old_bucket": expected == old if expected is not None else None,
                "missing_proof": (
                    "current-generation entity birth + exact wire owner + unique entity self-attack/native key"
                ),
                "verdict": "unclosed_summon_child",
            })
            continue
        try:
            template = int(template_text)
        except ValueError:
            ordinary.append(compact)
            continue
        spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
        if spec is None or old == int(spec.canonical_skill_id):
            ordinary.append(compact)
            continue
        same_job = int(spec.canonical_skill_id) in JOB_SKILLS[row["职业"]]
        cross.append(compact | {
            "static_template_skill_id": int(spec.canonical_skill_id),
            "same_job_candidate": same_job,
            "verdict": (
                "risk_only_missing_same_generation_join" if same_job
                else "strict_negative_stale_or_foreign_numeric_object"
            ),
            "why_not_truth": (
                "The report's template is a nearest prior diagnostic registration, not the exact "
                "current generation selected by the decoder for this Hit."
            ),
        })

    cross.sort(key=lambda row: (-row["hits"], row["job"], str(row["old_skill_id"])))
    summon.sort(key=lambda row: (-row["hits"], row["job"], str(row["old_skill_id"])))
    ordinary.sort(key=lambda row: (-row["hits"], row["job"], str(row["old_skill_id"])))
    same_job_risks = [row for row in cross if row["same_job_candidate"]]
    strict_negatives = [row for row in cross if not row["same_job_candidate"]]

    # The four named relic families are the largest high-payoff entity gap.
    relic_skills = {3403, 3409, 3412, 3422, 3423}
    relic_gap_hits = sum(int(row["Hit数"]) for row in rows if row["职业"] == "圣徒" and skill(row["skill_id"]) in relic_skills)
    exact_entity_gap_hits = sum(row["hits"] for row in summon if row["static_unique_skill_id"] is not None)
    same_job_risk_hits = sum(row["hits"] for row in same_job_risks)

    summaries = {row["职业"]: row for row in v66["summary"] if row["职业"] in JOBS}
    thresholds = []
    for job in JOBS:
        row = summaries[job]
        required = max(0, math.ceil(int(row["总直接Hit"]) * 0.95) - int(row["确定性已归因"]))
        thresholds.append({"job": job, "required_for_95": required, "current_coverage": row["确定性覆盖率%"]})
    thresholds.sort(key=lambda row: -row["required_for_95"])

    result = {
        "schema": "cleric-cross-skill-strict-audit-v69",
        "verdict": {
            "safe_new_hits": 0, "safe_cross_skill_corrections": 0,
            "production_candidates": 0, "shared_production_modified": False,
            "same_job_cross_skill_risk_hits": same_job_risk_hits,
            "strict_negative_foreign_template_hits": sum(row["hits"] for row in strict_negatives),
            "exact_named_entity_template_gap_hits": exact_entity_gap_hits,
            "saint_relic_family_gap_hits": relic_gap_hits,
        },
        "thresholds": thresholds,
        "same_job_cross_skill_risks": same_job_risks,
        "strict_negative_foreign_template_rows": strict_negatives,
        "summon_child_gaps": summon,
        "ordinary_unclosed_groups": ordinary,
        "negative_reason_distribution_from_v63_inquisitor": v63["unclosed_reasons"],
        "conservation": {
            "four_job_direct_damage_before": sum(int(row["总直接伤害"]) for row in summaries.values()),
            "four_job_direct_damage_after": sum(int(row["总直接伤害"]) for row in summaries.values()),
            "delta": 0, "hits_added": 0, "hits_removed": 0,
        },
        "inputs": {
            "v66": {"path": str(V66), "sha256": sha(V66)},
            "v63": {"path": str(V63), "sha256": sha(V63)},
            "v68": {"path": str(V68), "sha256": sha(V68)},
        },
    }
    save(OUT / "REPORT.json", result)
    save(OUT / "production_candidates.json", [])
    save(OUT / "correction_pairs.json", {})
    save(OUT / "fixtures.json", {
        "strict_negative_foreign_template": strict_negatives,
        "same_job_missing_generation_negative": same_job_risks,
        "summon_child_missing_owner_or_self_attack_negative": summon,
        "positive_control": [
            {"method": "packet_entity_native_attack_key", "meaning": "entity generation, owner and own native attack all closed"},
            {"method": "packet_entity_template_owner", "meaning": "entity generation, owner and unique static template all closed"},
            {"method": "damage_object_projectile_link", "meaning": "same owner/object generation and validated Projectile template"},
        ],
    })
    save(OUT / "input_manifest.json", result["inputs"])

    lines = ["# 神职跨技能与召唤子技能严格审计 v69", "", "## 结论", "",
             "没有新增可生产候选。旧技能桶和诊断模板冲突不能越过同 generation 门槛。", "",
             f"- 同职业跨技能风险：{same_job_risk_hits} Hit。", f"- 外职业/陈旧数字对象严格负例：{sum(row['hits'] for row in strict_negatives)} Hit。",
             f"- 带已知实体模板但实体链未闭合的缺口：{exact_entity_gap_hits} Hit。", f"- 圣徒五种手技能总缺口：{relic_gap_hits} Hit。", "",
             "## 为什么仍不能改名", "",
             "报告中的 object模板 是审计器附加的历史登记诊断，不保证它与当前 Hit 属于同一 owner/object generation。",
             "召唤物旧桶同样只有技能名或缓存 owner；必须同时拿到当前代实体出生、线上的 owner 记录，以及实体自身攻击技能键。", "",
             "## 守恒", "", f"四职业直接伤害前后均为 {result['conservation']['four_job_direct_damage_before']}，差值0。", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result["verdict"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
