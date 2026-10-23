from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SECOND_JOBS = (
    "剑皇",
    "月之领主",
    "狂战士",
    "毁灭者",
    "火舞",
    "冰灵",
    "时空领主",
    "黑暗女王",
    "狙翎",
    "魔羽",
    "影舞者",
    "风行者",
    "圣骑士",
    "十字军",
    "圣徒",
    "雷神",
    "重炮手",
    "机械大师",
    "炼金圣士",
    "药剂师",
    "刀锋舞者",
    "灵魂舞者",
    "影",
    "烈",
    "黑暗萨满",
    "噬魂者",
    "曜",
    "暗",
    "黑暗复仇者",
    "银色猎人",
)
DIRECT_FAMILIES = frozenset(
    {"direct37", "direct41", "other_direct_layout", "unmapped_real_hit_layout"}
)
SUPPLEMENTAL_INFERENCE_METHODS = frozenset(
    {"supplemental_cast_owner", "supplemental_continuation", "supplemental_same_packet"}
)
UNRESOLVED_METHODS = frozenset(
    {
        "unresolved",
        "unresolved_transformed_base_id",
        "rejected_transformed_base_id",
        "bundled_direct_unresolved",
    }
)
BASIC_NAMES = frozenset(
    {"普通攻击", "基本攻击", "基础攻击", "平砍", "待识别伤害", "未识别伤害", "未学习伤害"}
)


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json_counter(value: Counter[str] | dict[str, int]) -> str:
    rows = value.most_common() if isinstance(value, Counter) else value.items()
    return json.dumps(dict(rows), ensure_ascii=False)


def _numeric_skill(skill_id: object) -> bool:
    text = str(skill_id or "").strip()
    return text.isdigit() and int(text) > 0


def _basic_or_unknown(skill_id: object, skill_name: object) -> bool:
    token = str(skill_id or "").strip().casefold()
    name = str(skill_name or "").strip()
    return (
        token in {"", "-1", "0", "unknown", "unresolved", "basic_attack"}
        or token.endswith(":basic_attack")
        or name in BASIC_NAMES
    )


def _blocker_semantics(row: dict[str, Any]) -> tuple[str, str]:
    families = Counter({str(k): int(v) for k, v in row.get("families", {}).items()})
    direct_hits = sum(families[family] for family in DIRECT_FAMILIES)
    if _basic_or_unknown(row.get("skill_id"), row.get("skill_name")):
        return "普通攻击/未识别直接命中", "缺技能身份，不能用时间近邻补名"
    if _numeric_skill(row.get("skill_id")) and direct_hits > 0:
        return "真正直接技能伤害阻断", "有直接协议族命中，但技能归属仅为推断"
    if families["status_or_periodic"]:
        return "状态/持续伤害边界项", "gate 与协议族语义不一致，需要先确认布局"
    return "未确定语义阻断", "现有聚合证据不足"


def _load_optional(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build(summary: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    blocker_skills = [
        row for row in summary.get("blocker_skills", []) if row.get("job") in SECOND_JOBS
    ]
    blockers_by_job_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in blocker_skills:
        blockers_by_job_skill[str(row["job"])].append(row)

    deferred_candidates: list[dict[str, Any]] = []
    deferred_by_job: dict[str, Counter[str]] = defaultdict(Counter)
    semantic_totals: dict[str, dict[str, Any]] = {
        "真正直接技能伤害阻断": {"hits": 0, "damage": 0, "precision": "exact"},
        "普通攻击/未识别直接命中": {"hits": 0, "damage": 0, "precision": "exact"},
        "允许暂缓：明确附加伤害桶": {"hits": 0, "damage": 0, "precision": "exact"},
        "允许暂缓：明确持续/状态伤害": {"hits": 0, "damage": 0, "precision": "exact"},
        "允许暂缓：零伤害/旧非命中上下文": {
            "hits": int(summary.get("classifications", {}).get("nonhit_or_legacy_context", 0)),
            "damage": int(summary.get("classification_damage", {}).get("nonhit_or_legacy_context", 0)),
            "precision": "exact",
        },
        "允许暂缓：动作无关/脉冲附伤": {
            "hits": 0,
            "damage_estimate": 0,
            "precision": "exact hits; mean-damage estimate",
        },
        "允许暂缓：数值技能上的状态伤害": {
            "hits": 0,
            "damage_estimate": 0,
            "precision": "exact hits; mean-damage estimate",
        },
        "需复核：数值技能 supplemental 记录总量": {
            "hits": 0,
            "damage_estimate": 0,
            "precision": "exact hits; mean-damage estimate",
        },
        "需复核：deferred 中的数值技能正伤害": {
            "hits_lower_bound": 0,
            "hits_upper_bound": 0,
            "damage_lower_estimate": 0,
            "damage_upper_estimate": 0,
            "precision": "Frechet hit bounds; damage estimate",
        },
    }
    for row in summary.get("current_skill_buckets", []):
        job = str(row.get("job", ""))
        skill_id = str(row.get("skill_id", ""))
        skill_name = str(row.get("skill_name", ""))
        if job not in SECOND_JOBS:
            continue
        methods = Counter({str(k): int(v) for k, v in row.get("methods", {}).items()})
        families = Counter({str(k): int(v) for k, v in row.get("families", {}).items()})
        if skill_id.casefold().startswith("supplemental:"):
            semantic_totals["允许暂缓：明确附加伤害桶"]["hits"] += int(
                row.get("hits", 0)
            )
            semantic_totals["允许暂缓：明确附加伤害桶"]["damage"] += int(
                row.get("damage", 0)
            )
            continue
        if skill_id.casefold().startswith("periodic:"):
            semantic_totals["允许暂缓：明确持续/状态伤害"]["hits"] += int(
                row.get("hits", 0)
            )
            semantic_totals["允许暂缓：明确持续/状态伤害"]["damage"] += int(
                row.get("damage", 0)
            )
            continue
        if not _numeric_skill(skill_id):
            continue
        mean_damage = int(row.get("damage", 0)) / max(1, int(row.get("hits", 0)))
        special_hits = methods["action_free_special_attack"] + methods[
            "refreshable_pulsing_aura"
        ]
        numeric_status_hits = sum(
            methods[method]
            for method in (
                "periodic_cast_fallback",
                "periodic_status",
                "periodic_status_packet",
                "status_damage_packet",
                "status_damage_skill",
            )
        )
        semantic_totals["允许暂缓：动作无关/脉冲附伤"]["hits"] += special_hits
        semantic_totals["允许暂缓：动作无关/脉冲附伤"][
            "damage_estimate"
        ] += round(mean_damage * special_hits)
        semantic_totals["允许暂缓：数值技能上的状态伤害"][
            "hits"
        ] += numeric_status_hits
        semantic_totals["允许暂缓：数值技能上的状态伤害"][
            "damage_estimate"
        ] += round(mean_damage * numeric_status_hits)
        inferred = sum(methods[method] for method in SUPPLEMENTAL_INFERENCE_METHODS)
        entity = methods["supplemental_summon_owner"]
        if inferred <= 0 and entity <= 0:
            continue
        total_hits = int(row.get("hits", 0))
        direct_family_hits = sum(families[family] for family in DIRECT_FAMILIES)
        # The corpus summary stores method and family marginals, not their
        # event-level cross table. Fréchet bounds are therefore the strongest
        # honest statement possible without replaying the capture.
        inferred_direct_min = max(0, inferred + direct_family_hits - total_hits)
        inferred_direct_max = min(inferred, direct_family_hits)
        risk_min = entity + inferred_direct_min
        risk_max = entity + inferred_direct_max
        semantic_totals["需复核：数值技能 supplemental 记录总量"][
            "hits"
        ] += inferred + entity
        semantic_totals["需复核：数值技能 supplemental 记录总量"][
            "damage_estimate"
        ] += round(mean_damage * (inferred + entity))
        candidate = {
            "job": job,
            "skill_id": skill_id,
            "skill_name": skill_name,
            "skill_total_hits": total_hits,
            "skill_total_damage": int(row.get("damage", 0)),
            "supplemental_inference_hits": inferred,
            "supplemental_entity_hits": entity,
            "direct_protocol_family_hits": direct_family_hits,
            "misdeferred_hit_lower_bound": risk_min,
            "misdeferred_hit_upper_bound": risk_max,
            "estimated_damage_lower_bound": round(mean_damage * risk_min),
            "estimated_damage_upper_bound": round(mean_damage * risk_max),
            "methods": _json_counter(methods),
            "protocol_families": _json_counter(families),
            "review": (
                "召唤/实体正伤害不应自动等同板子附伤"
                if entity
                else "普通数值技能正伤害被 supplemental 先行排除；需逐事件交叉确认"
            ),
        }
        deferred_candidates.append(candidate)
        deferred_by_job[job]["candidate_buckets"] += 1
        deferred_by_job[job]["hit_lower"] += risk_min
        deferred_by_job[job]["hit_upper"] += risk_max
        deferred_by_job[job]["damage_lower_est"] += candidate[
            "estimated_damage_lower_bound"
        ]
        deferred_by_job[job]["damage_upper_est"] += candidate[
            "estimated_damage_upper_bound"
        ]

    job_rows: list[dict[str, Any]] = []
    top_rows: list[dict[str, Any]] = []
    for job in SECOND_JOBS:
        skill_rows = blockers_by_job_skill.get(job, [])
        methods = Counter()
        families = Counter()
        direct_hits = 0
        unresolved_hits = 0
        heuristic_named_hits = 0
        for row in skill_rows:
            row_methods = Counter(
                {str(k): int(v) for k, v in row.get("methods", {}).items()}
            )
            row_families = Counter(
                {str(k): int(v) for k, v in row.get("families", {}).items()}
            )
            methods.update(row_methods)
            families.update(row_families)
            if not _basic_or_unknown(row.get("skill_id"), row.get("skill_name")):
                direct_hits += sum(row_families[family] for family in DIRECT_FAMILIES)
                heuristic_named_hits += sum(
                    count
                    for method, count in row_methods.items()
                    if method not in UNRESOLVED_METHODS
                )
            unresolved_hits += sum(row_methods[method] for method in UNRESOLVED_METHODS)
            semantic, _reason = _blocker_semantics(row)
            if semantic == "真正直接技能伤害阻断":
                semantic_totals[semantic]["hits"] += int(row.get("hits", 0))
                semantic_totals[semantic]["damage"] += int(row.get("damage", 0))
            elif semantic == "普通攻击/未识别直接命中":
                semantic_totals[semantic]["hits"] += int(row.get("hits", 0))
                semantic_totals[semantic]["damage"] += int(row.get("damage", 0))
        priority_family = max(families, key=families.get, default="-")
        deferred = deferred_by_job[job]
        blocker_hits = int(summary.get("blocker_jobs", {}).get(job, 0))
        blocker_damage = int(summary.get("blocker_job_damage", {}).get(job, 0))
        job_rows.append(
            {
                "job": job,
                "blocker_hits": blocker_hits,
                "blocker_damage": blocker_damage,
                "direct_protocol_blocker_hits": direct_hits,
                "heuristic_named_wrong_skill_risk_hits": heuristic_named_hits,
                "unresolved_missing_attribution_hits": unresolved_hits,
                "deferred_candidate_buckets": deferred["candidate_buckets"],
                "misdeferred_hit_lower_bound": deferred["hit_lower"],
                "misdeferred_hit_upper_bound": deferred["hit_upper"],
                "estimated_misdeferred_damage_lower": deferred["damage_lower_est"],
                "estimated_misdeferred_damage_upper": deferred["damage_upper_est"],
                "priority_protocol_family": priority_family,
                "blocker_evidence_methods": _json_counter(methods),
                "blocker_protocol_families": _json_counter(families),
                "risk_level": (
                    "critical"
                    if heuristic_named_hits or deferred["hit_upper"]
                    else "high"
                    if blocker_hits
                    else "low"
                ),
            }
        )
        ranked = sorted(
            skill_rows,
            key=lambda row: (-int(row.get("damage", 0)), -int(row.get("hits", 0))),
        )[:10]
        for rank, row in enumerate(ranked, 1):
            semantic, reason = _blocker_semantics(row)
            top_rows.append(
                {
                    "job": job,
                    "rank": rank,
                    "skill_id": row.get("skill_id", ""),
                    "skill_name": row.get("skill_name", ""),
                    "blocker_hits": int(row.get("hits", 0)),
                    "blocker_damage": int(row.get("damage", 0)),
                    "semantic_class": semantic,
                    "wrong_attribution_risk": reason,
                    "evidence_methods": _json_counter(row.get("methods", {})),
                    "protocol_families": _json_counter(row.get("families", {})),
                }
            )

    job_rows.sort(
        key=lambda row: (
            -row["blocker_damage"],
            -row["estimated_misdeferred_damage_upper"],
        )
    )
    deferred_candidates.sort(
        key=lambda row: (
            -row["estimated_damage_upper_bound"],
            -row["misdeferred_hit_upper_bound"],
        )
    )

    method_rows = [
        {
            "evidence_method": method,
            "blocker_hits": int(hits),
            "blocker_damage": int(summary.get("blocker_method_damage", {}).get(method, 0)),
            "risk": (
                "漏归因"
                if method in UNRESOLVED_METHODS
                else "错归因风险：已有技能名但证据不是确定性字段"
            ),
        }
        for method, hits in summary.get("blocker_methods", {}).items()
    ]
    family_rows = [
        {
            "protocol_family": family,
            "blocker_hits": int(hits),
            "blocker_damage": int(summary.get("blocker_family_damage", {}).get(family, 0)),
            "priority": (
                "P0"
                if family in {"direct37", "direct41"}
                else "P1"
                if family == "unmapped_real_hit_layout"
                else "P2"
            ),
            "review": (
                "继续寻找对象/动作/实体的固定协议键"
                if family == "direct37"
                else "固定 +106 已覆盖大多数；剩余分支不能用重叠字节循环证明"
                if family == "direct41"
                else "先确认是否真是状态/持续伤害"
                if family == "status_or_periodic"
                else "先解布局再谈技能归属"
            ),
        }
        for family, hits in summary.get("blocker_families", {}).items()
    ]
    risk_register = [
        {
            "priority": "P0",
            "risk": "时序推断稳定归错技能",
            "hits": sum(row["heuristic_named_wrong_skill_risk_hits"] for row in job_rows),
            "damage": sum(
                int(summary.get("blocker_method_damage", {}).get(method, 0))
                for method in summary.get("blocker_methods", {})
                if method not in UNRESOLVED_METHODS
            ),
            "evidence": "latest_profiled/cast_instance/continuation/learned 等均非唯一协议键",
        },
        {
            "priority": "P0",
            "risk": "deferred 可能误排直接技能或召唤实体伤害",
            "hits": sum(row["misdeferred_hit_upper_bound"] for row in job_rows),
            "damage": sum(row["estimated_misdeferred_damage_upper"] for row in job_rows),
            "evidence": "现成 summary 只有方法和协议族边际分布；报告给出严格上下界",
        },
        {
            "priority": "P0",
            "risk": "旧 object coverage 可能包含未验证模板→技能边",
            "hits": int(
                sum(
                    row.get("methods", {}).get("damage_object_projectile_link", 0)
                    for row in summary.get("current_skill_buckets", [])
                    if row.get("job") in SECOND_JOBS
                )
            ),
            "damage": 0,
            "evidence": "对象审计仅少数模板通过 packet anchor；生产代码现已标 provisional，但 corpus_201 是旧口径",
        },
        {
            "priority": "P1",
            "risk": "未识别命中",
            "hits": sum(row["unresolved_missing_attribution_hits"] for row in job_rows),
            "damage": sum(
                int(summary.get("blocker_method_damage", {}).get(method, 0))
                for method in UNRESOLVED_METHODS
            ),
            "evidence": "这类是漏归因，不应通过最近技能猜测来降低数量",
        },
    ]
    provenance_summary = {
        "templates": provenance.get("templates", provenance.get("aggregate", {})),
        "safe_template_ids": provenance.get("safe_template_ids", []),
        "unsafe_template_ids": provenance.get("unsafe_template_ids", []),
    }
    semantic_totals["需复核：deferred 中的数值技能正伤害"].update(
        {
            "hits_lower_bound": sum(
                row["misdeferred_hit_lower_bound"] for row in job_rows
            ),
            "hits_upper_bound": sum(
                row["misdeferred_hit_upper_bound"] for row in job_rows
            ),
            "damage_lower_estimate": sum(
                row["estimated_misdeferred_damage_lower"] for row in job_rows
            ),
            "damage_upper_estimate": sum(
                row["estimated_misdeferred_damage_upper"] for row in job_rows
            ),
        }
    )
    semantic_rows = [
        {"semantic_class": name, **values} for name, values in semantic_totals.items()
    ]
    return {
        "jobs": job_rows,
        "top10": top_rows,
        "methods": method_rows,
        "families": family_rows,
        "deferred_candidates": deferred_candidates,
        "risk_register": risk_register,
        "provenance_summary": provenance_summary,
        "semantic_classes": semantic_rows,
    }


def render(result: dict[str, Any], summary: dict[str, Any]) -> str:
    jobs = result["jobs"]
    lines = [
        "# corpus_201 直接伤害阻断语义复核（现成报告版）",
        "",
        f"- 数据源：{summary.get('captures', 0)} 份抓包、{summary.get('total_outgoing_hits', 0):,} 条对外伤害。",
        "- 本报告只读取现成 corpus_201 汇总，没有重放抓包，也没有修改生产代码。",
        "- blocker 的 hit、damage、技能、证据方法和协议族是原报告精确值。",
        "- deferred 没有逐事件的“方法×协议族”交叉表，因此使用数学上下界；estimated damage 仅用于排序，不当作事实值。",
        "",
        "## 核心结论",
        "",
        f"- 30 个二转职业共有 {sum(row['blocker_hits'] for row in jobs):,} 条 blocker，damage {sum(row['blocker_damage'] for row in jobs):,}。",
        f"- 其中 {sum(row['direct_protocol_blocker_hits'] for row in jobs):,} 条落在 direct37/direct41 等直接协议族。",
        f"- 已经显示某个技能名、但证据仍只是时序/画像推断的风险记录有 {sum(row['heuristic_named_wrong_skill_risk_hits'] for row in jobs):,} 条；这是错归因风险，不只是覆盖缺口。",
        f"- deferred 中疑似直接技能/召唤实体伤害的严格 hit 区间为 {sum(row['misdeferred_hit_lower_bound'] for row in jobs):,}–{sum(row['misdeferred_hit_upper_bound'] for row in jobs):,}。",
        "",
        "现有 deferred 规则确实存在把直接技能误排除的风险：`supplemental_summon_owner` 是持久实体伤害；普通数值技能 ID 的 `supplemental_cast_owner/continuation/same_packet` 也不能在没有协议语义证明时一概称为附加伤害。",
        "",
        "## 语义分类",
        "",
        "| 分类 | hit | damage | 精度 |",
        "|---|---:|---:|---|",
    ]
    for row in result["semantic_classes"]:
        hit_text = (
            f"{row['hits_lower_bound']:,}–{row['hits_upper_bound']:,}"
            if "hits_lower_bound" in row
            else f"{row.get('hits', 0):,}"
        )
        damage_text = (
            f"{row['damage_lower_estimate']:,}–{row['damage_upper_estimate']:,}"
            if "damage_lower_estimate" in row
            else f"~{row['damage_estimate']:,}"
            if "damage_estimate" in row
            else f"{row.get('damage', 0):,}"
        )
        lines.append(
            f"| {row['semantic_class']} | {hit_text} | {damage_text} | {row['precision']} |"
        )
    lines.extend(
        [
        "",
        "## 30 个二转职业",
        "",
        "| 职业 | blocker hit | blocker damage | 直接协议 hit | 已命名错归因风险 | deferred 误排区间 | 优先协议族 |",
        "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in jobs:
        lines.append(
            f"| {row['job']} | {row['blocker_hits']:,} | {row['blocker_damage']:,} | "
            f"{row['direct_protocol_blocker_hits']:,} | {row['heuristic_named_wrong_skill_risk_hits']:,} | "
            f"{row['misdeferred_hit_lower_bound']:,}–{row['misdeferred_hit_upper_bound']:,} | {row['priority_protocol_family']} |"
        )
    lines.extend(
        [
            "",
            "## 最值得破解的协议族",
            "",
            "| 优先级 | 协议族 | blocker hit | blocker damage | 下一步 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in result["families"]:
        lines.append(
            f"| {row['priority']} | {row['protocol_family']} | {row['blocker_hits']:,} | {row['blocker_damage']:,} | {row['review']} |"
        )
    lines.extend(
        [
            "",
            "## 错归因风险优先级",
            "",
            "| 优先级 | 风险 | hit/上界 | damage/估算上界 | 证据 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in result["risk_register"]:
        lines.append(
            f"| {row['priority']} | {row['risk']} | {row['hits']:,} | {row['damage']:,} | {row['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## 使用边界",
            "",
            "- 普通数值技能 + direct37/direct41 + 非确定性方法：按真正直接技能伤害 blocker 保留。",
            "- 明确 `supplemental:`、`periodic:`、`direct_hit_overflow`：允许暂缓，不可丢失事实 damage。",
            "- `supplemental_summon_owner`：作为实体伤害风险单列，不与板子附伤混为一类。",
            "- 普通攻击/待识别：不伪造技能名；先破解协议布局或固定所有权键。",
            "- 各职业前十技能、全部 deferred 候选及方法明细见配套 CSV。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT
        / "outputs/damage_attribution/corpus_201_direct_damage_audit_current/summary.json",
    )
    parser.add_argument(
        "--provenance",
        type=Path,
        default=ROOT / "outputs/damage_attribution/projectile_mapping_provenance_audit.json",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=ROOT / "outputs/damage_attribution/direct_blocker_semantic_review",
    )
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    provenance = _load_optional(args.provenance)
    result = build(summary, provenance)
    prefix = args.output_prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "mode": "existing_corpus_201_summary_only",
        "source_summary": str(args.summary.resolve()),
        "source_analysis_signature": summary.get("analysis_signature", {}),
        "limitations": [
            "deferred method×protocol-family cross table is unavailable in the existing summary",
            "deferred hit risk uses exact Frechet lower/upper bounds",
            "deferred damage bounds are mean-damage estimates for prioritization only",
            "the existing corpus predates provisional projectile-template reclassification",
        ],
        **result,
    }
    Path(f"{prefix}_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(Path(f"{prefix}_jobs.csv"), result["jobs"], list(result["jobs"][0]))
    _write_csv(
        Path(f"{prefix}_job_top10_skills.csv"),
        result["top10"],
        list(result["top10"][0]),
    )
    _write_csv(
        Path(f"{prefix}_evidence_methods.csv"),
        result["methods"],
        list(result["methods"][0]),
    )
    _write_csv(
        Path(f"{prefix}_protocol_families.csv"),
        result["families"],
        list(result["families"][0]),
    )
    _write_csv(
        Path(f"{prefix}_deferred_candidates.csv"),
        result["deferred_candidates"],
        list(result["deferred_candidates"][0]),
    )
    _write_csv(
        Path(f"{prefix}_risk_register.csv"),
        result["risk_register"],
        list(result["risk_register"][0]),
    )
    _write_csv(
        Path(f"{prefix}_semantic_classes.csv"),
        result["semantic_classes"],
        [
            "semantic_class",
            "hits",
            "damage",
            "hits_lower_bound",
            "hits_upper_bound",
            "damage_lower_estimate",
            "damage_upper_estimate",
            "damage_estimate",
            "precision",
        ],
    )
    Path(f"{prefix}_report.md").write_text(render(result, summary), encoding="utf-8")
    print(
        f"jobs={len(result['jobs'])} blockers={sum(row['blocker_hits'] for row in result['jobs'])} "
        f"deferred_upper={sum(row['misdeferred_hit_upper_bound'] for row in result['jobs'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
