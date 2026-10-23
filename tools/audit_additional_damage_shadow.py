from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.additional_damage_shadow import (
    SHADOW_EFFECT_RULES,
    audit_additional_damage_shadow,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.player_identity import resolve_player_identities
from dragon_nest_builder.live_dps.profile import ClientProfile


_CLASSIC_LEVEL_CAP = 70
_PENDING_EFFECTS: dict[str, tuple[str, str]] = {
    "503": ("Debuff", "70级每Hit固定值，上限7000；需连骨命中目标的15秒窗口"),
    "3551": ("被动", "触电目标上30%概率触发，按80%魔攻结算，不是触发Hit的固定比例"),
    "6401": ("Debuff/领域", "目标抵抗减速时，按普通攻击30%结算"),
}


def _identity_map(snapshot) -> dict[str, dict[str, str]]:
    identities = resolve_player_identities(
        [
            {"source_id": row.source_id, "job": row.job, "name": row.name}
            for row in snapshot.players
        ],
        snapshot.party_members,
        snapshot.player_profiles,
    )
    result = {
        row.source_id: {
            "name": row.character_name or row.source_id,
            "job": row.job,
            "profileId": row.profile_id,
        }
        for row in identities
    }
    for row in snapshot.players:
        result.setdefault(
            row.source_id,
            {
                "name": row.name or row.source_id,
                "job": row.job,
                "profileId": "",
            },
        )
    return result


def _current_state(event) -> str:
    named_effect_ids = {rule.effect_skill_id for rule in SHADOW_EFFECT_RULES}
    if (
        event.trigger_skill_id
        and event.trigger_attribution_confidence >= 0.95
        and str(event.skill_id) in named_effect_ids
    ):
        return "效果来源和触发技能均已正确识别"
    if str(event.skill_id) in named_effect_ids:
        return "效果来源已识别，触发技能未确定"
    if event.trigger_skill_id and event.trigger_attribution_confidence >= 0.95:
        return "触发技能已识别，效果来源未单列"
    if event.trigger_skill_id:
        return "仅有低置信触发候选"
    return "当前未归因"


def analyze_capture(path: Path) -> dict[str, object]:
    store = DataStore.load()
    profile = ClientProfile.load(DATA_DIR / "live_dps_profiles" / "v576.json")
    source = V576DiagnosticReplaySource(path, DATA_DIR, profile.known_players)
    try:
        decoded_events = source.poll()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in decoded_events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    identities = _identity_map(snapshot)
    jobs_by_source = {
        source_id: str(identity.get("job") or "")
        for source_id, identity in identities.items()
    }
    events = tuple(snapshot.combat_events)
    audits = audit_additional_damage_shadow(
        events,
        jobs_by_source,
        tuple(snapshot.skill_cast_events or ()),
        tuple(snapshot.boss_effects or ()),
        tuple(snapshot.player_buffs or ()),
        tuple(snapshot.skill_parameter_events or ()),
    )

    details: list[dict[str, object]] = []
    for audit in audits:
        event = events[audit.event_index]
        identity = identities.get(
            event.source_id,
            {"name": event.source_name or event.source_id, "job": event.job, "profileId": ""},
        )
        trigger = (
            events[audit.trigger_event_index]
            if 0 <= audit.trigger_event_index < len(events)
            else None
        )
        details.append(
            {
                "capture": path.name,
                "timestampMs": event.timestamp_ms,
                "timestampSec": round(event.timestamp_ms / 1000.0, 3),
                "sourceId": event.source_id,
                "character": identity.get("name", ""),
                "job": identity.get("job", "") or event.job,
                "profileId": identity.get("profileId", ""),
                "targetId": event.target_id,
                "targetName": event.target_name,
                "extraDamage": event.amount,
                "currentSkillId": event.skill_id,
                "currentSkillName": event.skill_name,
                "currentMethod": event.attribution_method,
                "currentConfidence": event.attribution_confidence,
                "currentTriggerSkillId": event.trigger_skill_id,
                "currentTriggerSkillName": event.trigger_skill_name,
                "currentTriggerConfidence": event.trigger_attribution_confidence,
                "currentState": _current_state(event),
                "wireLayout": event.wire_damage_layout,
                "wireCritical": event.wire_critical,
                "wireStatusSkillId": event.wire_status_skill_id,
                "wireStatusTailHex": event.wire_status_tail_hex,
                "auditStatus": audit.status,
                "auditConfidence": audit.confidence,
                "effectType": audit.effect_type,
                "effectSkillId": audit.effect_skill_id,
                "effectName": audit.effect_name,
                "triggerSkillId": audit.trigger_skill_id,
                "triggerSkillName": audit.trigger_skill_name,
                "triggerTimestampMs": trigger.timestamp_ms if trigger else None,
                "triggerDamage": audit.trigger_damage,
                "deltaMs": audit.delta_ms,
                "observedRatio": audit.observed_ratio,
                "evidence": audit.evidence,
                "reason": audit.reason,
                "effectCandidates": list(audit.effect_candidates),
                "conditionNotes": list(audit.condition_notes),
            }
        )

    summary_counter: dict[tuple[str, str, str, str, str], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    for row in details:
        key = (
            str(row["capture"]),
            str(row["job"]),
            str(row["effectType"]),
            str(row["effectName"] or "未确定来源"),
            str(row["auditStatus"]),
        )
        summary_counter[key][0] += 1
        summary_counter[key][1] += int(row["extraDamage"])
    summary = [
        {
            "capture": key[0],
            "job": key[1],
            "effectType": key[2],
            "effectName": key[3],
            "auditStatus": key[4],
            "hits": values[0],
            "damage": values[1],
        }
        for key, values in sorted(summary_counter.items())
    ]
    return {
        "capture": str(path),
        "decodedEventCount": len(decoded_events),
        "combatEventCount": len(events),
        "identityCount": len(identities),
        "summary": summary,
        "details": details,
    }


def _plain_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def effect_candidate_inventory() -> list[dict[str, object]]:
    root = (
        DATA_DIR
        / "modular"
        / "tables"
        / "skills"
        / "dngamer_skill_sim_reborn_split"
        / "jobs"
    )
    enabled = {rule.effect_skill_id: rule for rule in SHADOW_EFFECT_RULES}
    candidates: dict[tuple[str, str], dict[str, object]] = {}
    terms = (
        "额外伤害",
        "附加伤害",
        "追加伤害",
        "额外的伤害",
        "额外产生的伤害",
        "产生额外",
    )
    for path in sorted(root.glob("job_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        tree = data.get("tree", {}).get("tree", [])
        job_name = str(tree[-1].get("job", {}).get("name", path.stem)) if tree else path.stem
        pve = data.get("skill_levels", {}).get("normal", {}).get("pve", {})
        for skill_id, levels in pve.items():
            valid = [
                row for row in levels
                if isinstance(row, dict)
                and int(row.get("_LevelLimit", 0) or 0) <= _CLASSIC_LEVEL_CAP
                and "-2146826273" not in str(row.get("desc", ""))
            ]
            if not valid:
                continue
            descriptions = [_plain_text(row.get("desc")) for row in valid]
            matching = [desc for desc in descriptions if any(term in desc for term in terms)]
            if not matching:
                continue
            skill = data.get("skills", {}).get(str(skill_id), {})
            rule = enabled.get(str(skill_id))
            selected = max(
                valid,
                key=lambda row: (
                    int(row.get("_LevelLimit", 0) or 0),
                    int(row.get("_SkillLevel", 0) or 0),
                ),
            )
            description = _plain_text(selected.get("desc"))
            if rule:
                candidate_class = f"{rule.effect_type}额外伤害"
                effect_type = rule.effect_type
                trial_state = "试用规则已启用"
            elif str(skill_id) in _PENDING_EFFECTS:
                pending_type, _pending_model = _PENDING_EFFECTS[str(skill_id)]
                candidate_class = f"{pending_type}额外伤害候选"
                effect_type = pending_type
                trial_state = "候选待建模"
            else:
                candidate_class = "主动技能分段或自身倍率"
                effect_type = "不纳入状态额外伤害审计"
                trial_state = "已排除正式归因试验"
            candidates[(job_name, str(skill_id))] = {
                "job": job_name,
                "skillId": str(skill_id),
                "skillName": skill.get("name", ""),
                "skillType": skill.get("_SkillType", ""),
                "dataLevel": selected.get("_SkillLevel", ""),
                "levelLimit": selected.get("_LevelLimit", ""),
                "description": description[:900],
                "candidateClass": candidate_class,
                "trialState": trial_state,
                "effectType": effect_type,
                "model": (
                    f"固定比例 {rule.fixed_ratio:.0%}"
                    if rule and rule.fixed_ratio
                    else f"固定值 {'/'.join(str(value) for value in rule.fixed_amounts)}"
                    if rule and rule.fixed_amounts
                    else "独立状态伤害签名+触发Hit时间窗口"
                    if rule and rule.match_any_amount
                    else f"0.9% 阶梯，最多 {rule.max_steps} 档"
                    if rule and rule.stepped_ratio
                    else _PENDING_EFFECTS[str(skill_id)][1]
                    if str(skill_id) in _PENDING_EFFECTS
                    else "需结合效果包/技能实例/伤害公式建立独立签名"
                ),
            }
    return list(candidates.values())


def job_candidate_coverage(
    candidates: list[dict[str, object]],
) -> list[dict[str, object]]:
    root = (
        DATA_DIR
        / "modular"
        / "tables"
        / "skills"
        / "dngamer_skill_sim_reborn_split"
        / "jobs"
    )
    jobs: set[str] = set()
    for path in sorted(root.glob("job_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        tree = data.get("tree", {}).get("tree", [])
        if tree:
            raw_job = tree[-1].get("job", path.stem)
            jobs.add(
                str(raw_job.get("name", path.stem))
                if isinstance(raw_job, dict)
                else str(raw_job)
            )
    by_job: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in candidates:
        by_job[str(row["job"])].append(row)
    rows: list[dict[str, object]] = []
    for job in sorted(jobs):
        found = by_job.get(job, [])
        eligible = [
            row for row in found
            if row["effectType"] != "不纳入状态额外伤害审计"
        ]
        enabled = [row for row in found if row["trialState"] == "试用规则已启用"]
        rows.append(
            {
                "job": job,
                "candidateCount": len(eligible),
                "enabledRuleCount": len(enabled),
                "candidates": "、".join(
                    f"{row['skillName']}({row['skillId']})" for row in eligible
                ),
                "conclusion": (
                    "已有试用规则"
                    if enabled
                    else "发现候选，仍需包内签名"
                    if eligible
                    else "拆包说明未发现独立Buff/被动/Debuff额外伤害"
                ),
            }
        )
    return rows


def effect_audit_summary(
    details: list[dict[str, object]],
    candidates: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Collapse duplicate job-table rows into one auditable effect row."""

    inventory: dict[str, dict[str, object]] = {}
    for row in candidates:
        if row["effectType"] == "不纳入状态额外伤害审计":
            continue
        skill_id = str(row["skillId"])
        item = inventory.setdefault(
            skill_id,
            {
                "effectSkillId": skill_id,
                "effectName": str(row["skillName"]),
                "effectType": str(row["effectType"]),
                "jobs": set(),
                "model": str(row["model"]),
                "trialState": str(row["trialState"]),
                "dataLevel": row.get("dataLevel", ""),
                "levelLimit": row.get("levelLimit", ""),
                "description": str(row["description"]),
            },
        )
        item["jobs"].add(str(row["job"]))

    status_order = (
        "当前已正确识别",
        "影子归因确定",
        "条件候选",
        "效果来源已识别",
        "仅候选",
        "效果来源冲突",
    )
    output: list[dict[str, object]] = []
    for skill_id, item in sorted(inventory.items(), key=lambda pair: int(pair[0])):
        selected: list[dict[str, object]] = []
        marker = f"{skill_id}:"
        for detail in details:
            if str(detail.get("effectSkillId") or "") == skill_id or any(
                str(value).startswith(marker)
                for value in detail.get("effectCandidates", [])
            ):
                selected.append(detail)
        counts = Counter(str(row.get("auditStatus") or "") for row in selected)
        damage_by_status = Counter()
        for row in selected:
            damage_by_status[str(row.get("auditStatus") or "")] += int(
                row.get("extraDamage", 0) or 0
            )
        trigger_names = Counter(
            str(row.get("triggerSkillName") or "")
            for row in selected
            if row.get("triggerSkillName")
        )
        captures = sorted({str(row.get("capture") or "") for row in selected})
        sample_jobs = sorted({str(row.get("job") or "") for row in selected if row.get("job")})
        if counts["当前已正确识别"]:
            conclusion = "正式算法已同时识别效果来源和触发技能"
        elif counts["影子归因确定"]:
            conclusion = "可稳定归因，已进入可切换的额外伤害归因视图"
        elif counts["条件候选"]:
            conclusion = "伤害公式已对上，但还缺状态/距离/属性等包内条件"
        elif counts["效果来源已识别"]:
            conclusion = "可确认效果来源并进入归因视图，触发技能仍保持未确定"
        elif selected:
            conclusion = "存在候选或同倍率冲突，不应自动写入技能构成"
        else:
            conclusion = "所选抓包中无可验证样本，或尚缺独立包签名/属性面板"
        result = {
            "effectSkillId": skill_id,
            "effectName": item["effectName"],
            "effectType": item["effectType"],
            "jobs": " / ".join(sorted(item["jobs"])),
            "model": item["model"],
            "dataLevel": item["dataLevel"],
            "levelLimit": item["levelLimit"],
            "currentCorrectHits": counts["当前已正确识别"],
            "shadowDefiniteHits": counts["影子归因确定"],
            "conditionCandidateHits": counts["条件候选"],
            "sourceOnlyHits": counts["效果来源已识别"],
            "candidateHits": counts["仅候选"],
            "conflictHits": counts["效果来源冲突"],
            "matchedDamage": sum(int(row.get("extraDamage", 0) or 0) for row in selected),
            "captureCount": len(captures),
            "sampleJobs": " / ".join(sample_jobs),
            "topTriggers": "、".join(name for name, _count in trigger_names.most_common(8)),
            "conclusion": conclusion,
        }
        for status in status_order:
            result[f"damage:{status}"] = damage_by_status[status]
        output.append(result)
    return output


def _csv_value(value: object) -> object:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fields})


def _write_markdown(
    path: Path,
    result: dict[str, object],
) -> None:
    lines = [
        "# 全职业 Buff / 被动 / Debuff 额外伤害影子审计",
        "",
        "> 本报告不替换正式技能归因，不改变总伤害、技能构成或评分。",
        "",
        "## 试用规则",
        "",
        "| 职业/范围 | 效果 | 类型 | 模型 | 附加条件 |",
        "|---|---|---|---|---|",
    ]
    for rule in SHADOW_EFFECT_RULES:
        scope = rule.job or "全队Buff"
        model = (
            f"固定 {rule.fixed_ratio:.0%}"
            if rule.fixed_ratio
            else f"固定值 {'/'.join(str(value) for value in rule.fixed_amounts)}"
            if rule.fixed_amounts
            else "状态伤害签名+时间窗口"
            if rule.match_any_amount
            else f"0.9%×档位（≤{rule.max_steps}）"
            if rule.stepped_ratio
            else "仅包内明确技能号"
        )
        conditions = "；".join(
            part for part in (
                "必须暴击" if rule.requires_critical else "",
                f"触发技能={','.join(rule.trigger_skill_ids)}" if rule.trigger_skill_ids else "",
                f"激活技能={','.join(rule.activation_skill_ids)}" if rule.activation_skill_ids else "",
                rule.condition_unverified,
                (
                    f"目标状态={'/'.join(rule.required_target_status_labels)}"
                    if rule.required_target_status_labels else ""
                ),
            ) if part
        )
        lines.append(
            f"| {scope} | {rule.effect_name}({rule.effect_skill_id}) | "
            f"{rule.effect_type} | {model} | {conditions or '无'} |"
        )
    lines.extend(
        [
            "",
            "## 全效果结论表",
            "",
            "| 效果 | 职业/范围 | 模型 | 正式已正确 | 影子确定 | 条件候选 | 冲突 | 结论 |",
            "|---|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in result["effectAudit"]:
        lines.append(
            f"| {row['effectName']}({row['effectSkillId']}) | {row['jobs']} | "
            f"{row['model']} | {row['currentCorrectHits']} | "
            f"{row['shadowDefiniteHits']} | {row['conditionCandidateHits']} | "
            f"{row['conflictHits']} | {row['conclusion']} |"
        )
    lines.extend(
        [
            "",
            "## 抓包审计汇总",
            "",
            "| 抓包 | 职业 | 效果类型 | 效果 | 结论 | Hit | 伤害 |",
            "|---|---|---|---|---|---:|---:|",
        ]
    )
    for row in result["summary"]:
        lines.append(
            f"| {row['capture']} | {row['job']} | {row['effectType']} | "
            f"{row['effectName']} | {row['auditStatus']} | {row['hits']} | "
            f"{int(row['damage']):,} |"
        )
    lines.extend(
        [
            "",
            "## 全职业拆包候选覆盖",
            "",
            "| 职业 | 候选数 | 已启用规则 | 候选效果 | 结论 |",
            "|---|---:|---:|---|---|",
        ]
    )
    for row in result["jobCoverage"]:
        lines.append(
            f"| {row['job']} | {row['candidateCount']} | "
            f"{row['enabledRuleCount']} | {row['candidates'] or '—'} | "
            f"{row['conclusion']} |"
        )
    lines.extend(
        [
            "",
            "## 结论口径",
            "",
            "- `当前已正确识别`：效果来源和触发技能都已有正式字段。",
            "- `影子归因确定`：只有一个效果规则和一个触发技能通过独立校验。",
            "- `条件候选`：倍率唯一，但诅咒、触电、距离等条件还没有包内证据。",
            "- `效果来源冲突`：两个同倍率效果同时可能存在，保持不归因。",
            "- `保留未归因`：不使用最近技能强猜。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the audit-only Buff/passive/Debuff extra-damage attribution pass."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--single-process", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    capture_rows: list[dict[str, object]] = []
    all_details: list[dict[str, object]] = []
    all_summary: list[dict[str, object]] = []
    if len(args.captures) > 1 and not args.single_process:
        # Decoder caches are intentionally large. Keeping many full captures
        # in one Python process made the audit grow past 1 GB and stall. Each
        # capture now gets an isolated process; only compact JSON rows are
        # merged in the parent. This path is audit-only and never runs inside
        # live ACT collection.
        with tempfile.TemporaryDirectory(prefix="dn-extra-damage-audit-") as tmp:
            temp_root = Path(tmp)
            for index, capture in enumerate(args.captures):
                child_output = temp_root / f"capture-{index:03d}.json"
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        str(capture),
                        "--output",
                        str(child_output),
                        "--single-process",
                    ],
                    cwd=str(ROOT),
                    check=True,
                )
                child = json.loads(child_output.read_text(encoding="utf-8"))
                capture_rows.extend(child.get("captures", []))
                all_details.extend(child.get("details", []))
                all_summary.extend(child.get("summary", []))
    else:
        capture_results = [analyze_capture(path) for path in args.captures]
        capture_rows = [
            {
                key: value
                for key, value in capture.items()
                if key not in {"details", "summary"}
            }
            for capture in capture_results
        ]
        all_details = [row for result in capture_results for row in result["details"]]
        all_summary = [row for result in capture_results for row in result["summary"]]
    candidates = effect_candidate_inventory()
    effect_audit = effect_audit_summary(all_details, candidates)
    result = {
        "format": "additional-damage-shadow-audit-v2",
        "productionAttributionChanged": False,
        "rules": [asdict(rule) for rule in SHADOW_EFFECT_RULES],
        "captures": capture_rows,
        "summary": all_summary,
        "details": all_details,
        "effectCandidates": candidates,
        "effectAudit": effect_audit,
        "jobCoverage": job_candidate_coverage(candidates),
        "statusTotals": dict(Counter(str(row["auditStatus"]) for row in all_details)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    stem = args.output.with_suffix("")
    summary_csv = stem.with_name(stem.name + "_summary.csv")
    details_csv = stem.with_name(stem.name + "_details.csv")
    coverage_csv = stem.with_name(stem.name + "_job_coverage.csv")
    effect_audit_csv = stem.with_name(stem.name + "_effect_audit.csv")
    markdown = stem.with_suffix(".md")
    _write_csv(summary_csv, all_summary)
    _write_csv(details_csv, all_details)
    _write_csv(coverage_csv, result["jobCoverage"])
    _write_csv(effect_audit_csv, effect_audit)
    _write_markdown(markdown, result)
    print(json.dumps({
        "output": str(args.output),
        "summaryCsv": str(summary_csv),
        "detailsCsv": str(details_csv),
        "jobCoverageCsv": str(coverage_csv),
        "effectAuditCsv": str(effect_audit_csv),
        "markdown": str(markdown),
        "captures": len(capture_rows),
        "details": len(all_details),
        "statusTotals": result["statusTotals"],
        "effectCandidates": len(result["effectCandidates"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
