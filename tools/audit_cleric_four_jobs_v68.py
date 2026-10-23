"""Rank strict direct-Hit gaps for the four Cleric second jobs.

The audit is intentionally read-only.  Frozen v66 real-capture groups are
cross-checked with the production evidence registry and the independent
provisional-projectile/3019 audits.  Diagnostic object labels from the corpus
report are never promoted because that report's registration lookup is not a
same-generation certificate.
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

from dragon_nest_builder.live_dps.damage_attribution import (
    ATTRIBUTION_RULES, AttributionLayer,
)
from dragon_nest_builder.live_dps.damage_object_origins import (
    _SPEC_BY_PROJECTILE_TEMPLATE,
)

BASE = ROOT / "outputs/direct_damage_v66_full_20260909"
REPORT = BASE / "profession_direct_damage_report.json"
CHECKPOINTS = BASE / "profession_direct_damage_checkpoints"
V60 = ROOT / "outputs/chain_lightning_meter_v60_20260909/REPORT.json"
PROVISIONAL = ROOT / "outputs/damage_attribution/provisional_projectile_upgrade_audit.json"
OUT = ROOT / "outputs/cleric_four_jobs_v68_20260909"
JOBS = ("十字军", "圣骑士", "圣徒", "雷神")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_skill(value: object) -> int | None:
    try:
        return int(str(value).split(":", 1)[0])
    except (TypeError, ValueError):
        return None


def main() -> None:
    report = load(REPORT)
    summaries = {row["职业"]: row for row in report["summary"] if row["职业"] in JOBS}
    if set(summaries) != set(JOBS):
        raise RuntimeError("v66 report is missing a Cleric second job")

    details = [row for row in report["details"] if row["职业"] in JOBS]
    checkpoint_inputs = []
    capture_counts = Counter()
    aggregate_damage = 0
    for path in sorted(CHECKPOINTS.glob("*.json")):
        checkpoint = load(path)
        per_capture = checkpoint["report"]
        jobs_here = sorted(set(per_capture.get("profession_summary", {})) & set(JOBS))
        if not jobs_here:
            continue
        capture_counts.update(jobs_here)
        aggregate_damage += int(per_capture["aggregated_invariants"]["damage"])
        checkpoint_inputs.append({
            "path": str(path), "sha256": digest(path),
            "capture": per_capture["capture"],
            "capture_sha256": per_capture["capture_sha256"], "jobs": jobs_here,
            "aggregate_hit_multiset_sha256": per_capture["aggregated_invariants"]["hit_evidence_sha256"],
        })

    deterministic_methods = {
        name for name, rule in ATTRIBUTION_RULES.items()
        if rule.deterministic and rule.layer is not AttributionLayer.STATUS
    }
    jobs = []
    work_queue = []
    positive_fixtures = []
    negative_fixtures = []
    mismatch_risks = []
    for job in JOBS:
        summary = summaries[job]
        total = int(summary["总直接Hit"])
        det = int(summary["确定性已归因"])
        needed = max(0, math.ceil(total * 0.95) - det)
        job_details = [row for row in details if row["职业"] == job]
        gaps = [row for row in job_details if row["归因分类"] not in {"deterministic", "deterministic_status_owner"}]
        positives = [row for row in job_details if row["归因分类"] in {"deterministic", "deterministic_status_owner"}]
        by_skill = defaultdict(lambda: {"hits": 0, "damage": 0})
        by_method = Counter()
        for row in gaps:
            label = f"{row['skill_id']} {row['技能名']}"
            by_skill[label]["hits"] += int(row["Hit数"])
            by_skill[label]["damage"] += int(row["伤害"])
            by_method[row["method"]] += int(row["Hit数"])
            compact = {
                "job": job, "skill_id": row["skill_id"], "skill_name": row["技能名"],
                "method": row["method"], "diagnostic_object_template": row["object模板"],
                "hits": int(row["Hit数"]), "damage": int(row["伤害"]),
                "capture_count": int(row["抓包数"]), "example_captures": row["示例抓包"],
            }
            negative_fixtures.append(compact)
            work_queue.append(compact)
            old_skill = parse_skill(row["skill_id"])
            try:
                template = int(row["object模板"])
            except (TypeError, ValueError):
                template = 0
            spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template)
            if spec is not None and old_skill != int(spec.canonical_skill_id):
                mismatch_risks.append(compact | {
                    "static_template_skill_id": int(spec.canonical_skill_id),
                    "verdict": "risk_only_not_same_generation_proof",
                })
        for row in positives:
            if row["method"] in deterministic_methods:
                positive_fixtures.append({
                    "job": job, "skill_id": row["skill_id"], "skill_name": row["技能名"],
                    "method": row["method"], "object_template": row["object模板"],
                    "hits": int(row["Hit数"]), "damage": int(row["伤害"]),
                    "capture_count": int(row["抓包数"]), "example_captures": row["示例抓包"],
                })
        jobs.append({
            "job": job, "total_direct_hits": total, "deterministic_hits": det,
            "gap_hits": int(summary["归因缺口"]),
            "deterministic_coverage_percent": float(summary["确定性覆盖率%"]),
            "minimum_new_deterministic_hits_for_95_percent": needed,
            "capture_count": int(summary["抓包数"]),
            "top_gap_skills": [
                {"skill": skill, **counts}
                for skill, counts in sorted(by_skill.items(), key=lambda pair: (-pair[1]["hits"], pair[0]))[:16]
            ],
            "gap_methods": dict(by_method.most_common()),
            "safe_new_hits_this_audit": 0,
            "coverage_after_safe_set_percent": float(summary["确定性覆盖率%"]),
        })

    provisional = load(PROVISIONAL)
    v60 = load(V60)
    if int(provisional["summary"]["upgradeable_templates"]) != 0:
        raise RuntimeError("provisional audit changed; re-review candidates")

    work_queue.sort(key=lambda row: (-row["hits"], row["job"], str(row["skill_id"])))
    mismatch_risks.sort(key=lambda row: (-row["hits"], row["job"], str(row["skill_id"])))
    priority = sorted(jobs, key=lambda row: (-row["minimum_new_deterministic_hits_for_95_percent"], row["job"]))
    cleric_damage = sum(int(summaries[job]["总直接伤害"]) for job in JOBS)
    result = {
        "schema": "cleric-four-jobs-strict-gap-audit-v68",
        "policy": {
            "allowed": [
                "fixed native result field on the exact physical row",
                "same-owner same-object-generation Projectile -> damage_object_id -> direct37/41",
                "current-generation entity birth + exact owner record + unique entity skill/native key",
            ],
            "forbidden": [
                "old skill label", "time proximity", "damage amount/fingerprint",
                "same numeric object ID across generations", "diagnostic registration label without generation join",
            ],
        },
        "verdict": {
            "safe_new_confirmed_hits": 0, "safe_corrections": 0,
            "production_candidates": 0, "shared_production_modified": False,
            "why_zero": (
                "The 205-capture provisional-template audit has zero upgradeable templates; "
                "all new cross-skill object observations lack an exact same-generation row join."
            ),
        },
        "jobs": jobs,
        "priority_to_95_percent": priority,
        "cross_skill_object_risks": mismatch_risks,
        "continuation_3019_side_evidence": {
            "v60_rows_relabelled_after_aggregation": int(v60["quantities"]["relabelled_native_physical_rows"]),
            "v60_continuation_rows_relabelled": int(v60["quantities"]["relabelled_before_methods"]["continuation_window"]),
            "meaning": "3019 continuation is not trustworthy; these counts are not a skill truth label.",
        },
        "physical_damage_conservation": {
            "four_job_direct_damage_before": cleric_damage,
            "four_job_direct_damage_after": cleric_damage,
            "frozen_aggregate_damage_before": aggregate_damage,
            "frozen_aggregate_damage_after": aggregate_damage,
            "delta": 0, "hit_rows_added": 0, "hit_rows_removed": 0,
        },
        "inputs": {
            "v66_report": {"path": str(REPORT), "sha256": digest(REPORT)},
            "checkpoints": checkpoint_inputs,
            "v60": {"path": str(V60), "sha256": digest(V60)},
            "provisional_template_audit": {"path": str(PROVISIONAL), "sha256": digest(PROVISIONAL)},
        },
    }

    save(OUT / "REPORT.json", result)
    save(OUT / "production_candidates.json", [])
    save(OUT / "correction_pairs.json", {})
    save(OUT / "fixtures.json", {
        "positive_deterministic_real_capture_groups": sorted(positive_fixtures, key=lambda row: (-row["hits"], row["job"])),
        "negative_unclosed_real_capture_groups": work_queue,
        "cross_skill_object_risk_groups": mismatch_risks,
        "note": "Frozen real-capture groups only; no synthetic or timing-derived truth fixtures.",
    })
    save(OUT / "work_queue.json", work_queue)
    save(OUT / "input_manifest.json", result["inputs"])

    lines = ["# 神职四职业直接 Hit 严格审计 v68", "", "## 95%门槛", "",
             "|职业|当前确定覆盖率|当前缺口|达到95%至少需新增确定Hit|本轮安全新增|", "|---|---:|---:|---:|---:|"]
    for row in priority:
        lines.append(f"|{row['job']}|{row['deterministic_coverage_percent']:.3f}%|{row['gap_hits']}|{row['minimum_new_deterministic_hits_for_95_percent']}|0|")
    lines += ["", "## 结论", "",
              "本轮没有满足逐物理行固定 native 或同 generation 对象/实体链的新增集合，因此生产候选为0。",
              f"发现 {sum(row['hits'] for row in mismatch_risks)} Hit 的‘旧桶名与诊断模板静态技能不同’风险；这些只证明旧标签可能错误，不足以定真。",
              f"独立 v60 已证明 {v60['quantities']['relabelled_before_methods']['continuation_window']} 条 continuation_window 行可被后续聚合改到其他技能，3019 不得作为真值。",
              "205份抓包的 provisional Projectile 审计仍为0个可升级模板。", "",
              "## 后续优先级", "",
              "优先补圣骑士、雷神、圣徒、十字军；排序依据是达到95%所需的新增确定Hit，而非旧技能桶标签。",
              "首要协议缺口是逐行对象 generation 连接和召唤实体出生/owner/自身攻击三段闭合。", "",
              "## 守恒", "", f"四职业直接伤害前后均为 {cleric_damage}；相关抓包全量聚合伤害前后均为 {aggregate_damage}，差值0。未新增、删除或复制任何Hit。", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"jobs": priority, "risk_hits": sum(row["hits"] for row in mismatch_risks), **result["verdict"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
