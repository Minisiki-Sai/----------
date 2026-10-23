from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.audit_corpus_direct_damage_blockers import (  # noqa: E402
    STATUS_METHODS,
    _event_key,
    _layout_family,
    _relaxed_key,
    _unique_counter_value,
)
from tools.audit_direct_damage_release_gate import _classification  # noqa: E402


SCHEMA_VERSION = 2
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
SUPPLEMENTAL_METHODS = frozenset(
    {
        "supplemental_cast_owner",
        "supplemental_continuation",
        "supplemental_same_packet",
        "supplemental_summon_owner",
    }
)
PERIODIC_OR_STATUS_METHODS = frozenset(
    {
        "periodic_cast_fallback",
        "periodic_status",
        "periodic_status_packet",
        "status_damage_packet",
        "status_damage_skill",
    }
)
UNRESOLVED_METHODS = frozenset(
    {
        "unresolved",
        "unresolved_transformed_base_id",
        "rejected_transformed_base_id",
        "bundled_direct_unresolved",
    }
)
HEURISTIC_METHODS = frozenset(
    {
        "latest_profiled_direct_cast",
        "cast_instance_match",
        "continuation_window",
        "learned_cast_profile",
        "unique_active_cast",
        "appended_status_first_hit",
        "embedded_single_candidate",
        "sustained_cast_window",
        "compound_candidate_profile",
        "compound_candidate_recent_cast",
    }
)
BASIC_ATTACK_NAMES = frozenset(
    {
        "普通攻击",
        "基本攻击",
        "基础攻击",
        "平砍",
        "待识别伤害",
        "未识别伤害",
        "未学习伤害",
    }
)
UNKNOWN_SKILL_IDS = frozenset({"", "-1", "0", "unknown", "unresolved"})


def _text(value: object) -> str:
    return str(value or "").strip()


def _is_numeric_skill(value: object) -> bool:
    token = _text(value).casefold()
    return token.isdigit() and int(token) > 0


def _is_basic_or_unknown(skill_id: object, skill_name: object) -> bool:
    token = _text(skill_id).casefold()
    name = _text(skill_name)
    return (
        token in UNKNOWN_SKILL_IDS
        or token.endswith(":basic_attack")
        or token == "basic_attack"
        or name in BASIC_ATTACK_NAMES
    )


def _semantic_class(
    event: CombatEvent,
    gate_class: str,
    family: str,
) -> tuple[str, str, str]:
    """Return semantic class, review confidence and concise reason.

    This deliberately separates protocol evidence from gameplay semantics.
    A direct-wire row attributed by timing is still a direct-damage blocker;
    a status tick can be factual damage while remaining outside that gate.
    """

    skill_id = _text(event.skill_id).casefold()
    method = _text(event.attribution_method).casefold()
    numeric_skill = _is_numeric_skill(skill_id)
    basic_or_unknown = _is_basic_or_unknown(skill_id, event.skill_name)

    if gate_class == "blocking_unproven_direct_hit":
        if basic_or_unknown:
            return (
                "ordinary_attack_or_unresolved_blocker",
                "high",
                "真实命中存在，但没有可审计的技能身份；不可拿时间近邻补技能名",
            )
        if family == "status_or_periodic":
            return (
                "periodic_or_status_borderline_blocker",
                "medium",
                "事件被 gate 视为真实命中，但协议族更像状态/持续伤害，需先确认布局",
            )
        if numeric_skill:
            return (
                "true_direct_skill_damage_blocker",
                "high",
                "普通技能身份且有真实命中，但当前技能归属仅为推断或未解",
            )
        return (
            "unresolved_semantic_blocker",
            "low",
            "技能标识不是普通数值技能，现有证据不足以确认语义",
        )

    if gate_class == "deferred_additional_or_nonhit":
        if (
            skill_id.startswith("periodic:")
            or method in PERIODIC_OR_STATUS_METHODS
            or family == "status_or_periodic"
        ):
            return (
                "allowed_deferred_periodic_or_status",
                "high",
                "持续状态/状态伤害不作为一次直接技能命中证明",
            )
        if skill_id.startswith("supplemental:") or method == "direct_hit_overflow":
            return (
                "allowed_deferred_additional_trigger",
                "high",
                "明确额外伤害桶或超出技能固定直击预算的附加结果",
            )
        if int(event.amount) <= 0:
            return (
                "allowed_deferred_nonhit_or_zero",
                "high",
                "没有正伤害，不属于直接伤害归因目标",
            )
        if method == "supplemental_summon_owner" and numeric_skill:
            return (
                "misdeferred_persistent_entity_damage_risk",
                "medium",
                "已知召唤实体所有者的正伤害被 evidence-bit 先行规则排除，不能等同板子附伤",
            )
        if (
            numeric_skill
            and method in SUPPLEMENTAL_METHODS
            and family in DIRECT_FAMILIES
        ):
            return (
                "suspected_direct_skill_misdeferred",
                "high",
                "普通数值技能的正伤害仅因 supplemental/no-real-hit 标记被排除，缺少语义证明",
            )
        if numeric_skill and family in DIRECT_FAMILIES:
            return (
                "suspected_direct_skill_misdeferred",
                "medium",
                "direct37/direct41 正伤害带普通技能身份，但被 deferred 规则提前排除",
            )
        if basic_or_unknown:
            return (
                "allowed_deferred_nonhit_or_unknown",
                "medium",
                "没有可靠技能身份且没有直接命中证据",
            )
        if numeric_skill and method in SUPPLEMENTAL_METHODS:
            return (
                "deferred_numeric_damage_needs_protocol_review",
                "medium",
                "正伤害被临时挂到数值技能，但原始布局未证明 direct37/direct41，不能直接计作误排",
            )
        return (
            "deferred_semantics_unproven",
            "low",
            "当前 deferred 只说明证据不足，尚不能证明它一定是附加伤害",
        )

    return ("outside_review_gate", "high", "已被当前 gate 作为确定性或非战斗事件处理")


def _risk_class(
    event: CombatEvent,
    gate_class: str,
    semantic_class: str,
) -> tuple[str, str]:
    method = _text(event.attribution_method).casefold()
    if method == "damage_object_projectile_link_provisional":
        return (
            "critical_provisional_template_wrong_skill",
            "对象所有者可追踪，但 Projectile→技能边未通过独立固定字段验证，可能稳定归错技能",
        )
    if semantic_class in {
        "suspected_direct_skill_misdeferred",
        "misdeferred_persistent_entity_damage_risk",
    }:
        return (
            "high_direct_damage_excluded_by_deferred",
            "正伤害可能属于直接技能/实体伤害，却没有进入直接伤害 release gate",
        )
    if gate_class == "blocking_unproven_direct_hit" and method in HEURISTIC_METHODS:
        return (
            "high_heuristic_wrong_skill",
            "当前界面已有技能名，但依据是时间窗、学习画像或候选排序，重叠施放时可能归错",
        )
    if gate_class == "blocking_unproven_direct_hit" and method in UNRESOLVED_METHODS:
        return (
            "high_missing_attribution",
            "真实直接命中仍无技能归属",
        )
    if gate_class == "blocking_unproven_direct_hit":
        return (
            "medium_unproven_skill_assignment",
            "真实直接命中的技能归属未达到确定性证据门槛",
        )
    return ("low_or_outside_direct_gate", "不属于本次直接伤害高风险项")


def _row_key(
    job: str,
    skill_id: str,
    skill_name: str,
    gate_class: str,
    semantic_class: str,
    family: str,
    method: str,
    risk_class: str,
) -> str:
    return "\x1f".join(
        (
            job,
            skill_id,
            skill_name,
            gate_class,
            semantic_class,
            family,
            method,
            risk_class,
        )
    )


def _decode_row_key(value: str) -> dict[str, str]:
    (
        job,
        skill_id,
        skill_name,
        gate_class,
        semantic_class,
        family,
        method,
        risk_class,
    ) = value.split("\x1f")
    return {
        "job": job,
        "skill_id": skill_id,
        "skill_name": skill_name,
        "gate_class": gate_class,
        "semantic_class": semantic_class,
        "protocol_family": family,
        "evidence_method": method,
        "risk_class": risk_class,
    }


def _audit_capture(path_text: str, source_name: str) -> dict[str, Any]:
    path = Path(path_text)
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    primary_layout_by_id: dict[int, str] = {}
    primary_layout_by_key: dict[tuple[object, ...], Counter[str]] = defaultdict(
        Counter
    )
    primary_layout_by_time_amount: dict[
        tuple[int, int], Counter[str]
    ] = defaultdict(Counter)
    original_feed = decoder.feed

    def feed(packet, packet_features=None):
        event = original_feed(packet, packet_features)
        if isinstance(event, CombatEvent):
            layout = str(getattr(decoder.last_decoded_message, "layout", "") or "")
            primary_layout_by_id[id(event)] = layout
            primary_layout_by_key[_event_key(event)][layout] += 1
            primary_layout_by_time_amount[
                (int(event.timestamp_ms), int(event.amount))
            ][layout] += 1
        return event

    decoder.feed = feed
    try:
        source_events = source.poll()
    finally:
        source.close()

    raw_layout_full: dict[tuple[object, ...], Counter[str]] = defaultdict(Counter)
    raw_layout_relaxed: dict[tuple[object, ...], Counter[str]] = defaultdict(Counter)
    for event in source_events:
        if not isinstance(event, CombatEvent):
            continue
        layout = primary_layout_by_id.get(id(event), "")
        if not layout:
            layout = _unique_counter_value(primary_layout_by_key.get(_event_key(event)))
        if not layout and (
            event.wire_result_group_id > 0 or event.wire_damage_object_id > 0
        ):
            layout = "direct37-metadata-fallback"
        if not layout and _text(event.attribution_method).casefold() in STATUS_METHODS:
            layout = "status-method-fallback"
        raw_layout_full[_event_key(event)][layout] += 1
        raw_layout_relaxed[_relaxed_key(event)][layout] += 1

    aggregator = CombatAggregator()
    for event in source_events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)

    rows: dict[str, list[int]] = {}
    class_reasons: dict[str, str] = {}
    risk_reasons: dict[str, str] = {}
    for event in snapshot.combat_events:
        gate_class = _classification(event)
        if gate_class not in {
            "blocking_unproven_direct_hit",
            "deferred_additional_or_nonhit",
        }:
            continue
        layout = _unique_counter_value(raw_layout_full.get(_event_key(event, raw=True)))
        if not layout:
            layout = _unique_counter_value(raw_layout_relaxed.get(_relaxed_key(event)))
        if not layout:
            layout = _unique_counter_value(
                primary_layout_by_time_amount.get(
                    (int(event.timestamp_ms), int(event.amount))
                )
            )
        if not layout and (
            event.wire_result_group_id > 0 or event.wire_damage_object_id > 0
        ):
            layout = "direct37-metadata-fallback"
        if not layout and _text(event.attribution_method).casefold() in STATUS_METHODS:
            layout = "status-method-fallback"
        family = _layout_family(layout, event)
        semantic_class, _confidence, semantic_reason = _semantic_class(
            event, gate_class, family
        )
        risk_class, risk_reason = _risk_class(event, gate_class, semantic_class)
        class_reasons[semantic_class] = semantic_reason
        risk_reasons[risk_class] = risk_reason
        key = _row_key(
            _text(event.job) or "未知职业",
            _text(event.skill_id),
            _text(event.skill_name),
            gate_class,
            semantic_class,
            family,
            _text(event.attribution_method).casefold() or "unresolved",
            risk_class,
        )
        value = rows.setdefault(key, [0, 0])
        value[0] += 1
        value[1] += max(0, int(event.amount))

    return {
        "capture": source_name,
        "path": str(path),
        "rows": [
            {**_decode_row_key(key), "hits": value[0], "damage": value[1]}
            for key, value in rows.items()
        ],
        "class_reasons": class_reasons,
        "risk_reasons": risk_reasons,
    }


def _signature(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _checkpoint_path(directory: Path, path: Path) -> Path:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _load_checkpoint(path: Path, capture: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        payload.get("review_schema_version") != SCHEMA_VERSION
        or payload.get("capture_signature") != _signature(capture)
    ):
        return None
    report = payload.get("report")
    return report if isinstance(report, dict) else None


def _save_checkpoint(path: Path, capture: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "review_schema_version": SCHEMA_VERSION,
        "capture_signature": _signature(capture),
        "report": report,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sum_rows(
    reports: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    class_reasons: dict[str, str] = {}
    risk_reasons: dict[str, str] = {}
    for report in reports:
        class_reasons.update(report.get("class_reasons", {}))
        risk_reasons.update(report.get("risk_reasons", {}))
        seen: set[tuple[str, ...]] = set()
        for row in report["rows"]:
            key = (
                row["job"],
                row["skill_id"],
                row["skill_name"],
                row["gate_class"],
                row["semantic_class"],
                row["protocol_family"],
                row["evidence_method"],
                row["risk_class"],
            )
            target = merged.setdefault(
                key,
                {
                    **{field: row[field] for field in (
                        "job",
                        "skill_id",
                        "skill_name",
                        "gate_class",
                        "semantic_class",
                        "protocol_family",
                        "evidence_method",
                        "risk_class",
                    )},
                    "hits": 0,
                    "damage": 0,
                    "file_count": 0,
                },
            )
            target["hits"] += int(row["hits"])
            target["damage"] += int(row["damage"])
            if key not in seen:
                target["file_count"] += 1
                seen.add(key)
    return list(merged.values()), class_reasons, risk_reasons


def _counter_json(counter: Counter[str]) -> str:
    return json.dumps(dict(counter.most_common()), ensure_ascii=False)


def _build_outputs(
    reports: list[dict[str, Any]],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    rows, class_reasons, risk_reasons = _sum_rows(reports)
    jobs: list[dict[str, Any]] = []
    top_skills: list[dict[str, Any]] = []
    method_rows: list[dict[str, Any]] = []
    family_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    baseline_hits = baseline.get("blocker_jobs", {})
    baseline_damage = baseline.get("blocker_job_damage", {})

    for job in SECOND_JOBS:
        job_rows = [row for row in rows if row["job"] == job]
        blocker_rows = [
            row
            for row in job_rows
            if row["gate_class"] == "blocking_unproven_direct_hit"
        ]
        deferred_rows = [
            row
            for row in job_rows
            if row["gate_class"] == "deferred_additional_or_nonhit"
        ]
        direct_rows = [
            row
            for row in blocker_rows
            if row["semantic_class"] == "true_direct_skill_damage_blocker"
        ]
        deferred_risk = [
            row
            for row in deferred_rows
            if row["semantic_class"]
            in {
                "suspected_direct_skill_misdeferred",
                "misdeferred_persistent_entity_damage_risk",
            }
        ]
        wrong_skill_risk = [
            row
            for row in blocker_rows
            if row["risk_class"]
            in {
                "critical_provisional_template_wrong_skill",
                "high_heuristic_wrong_skill",
                "medium_unproven_skill_assignment",
            }
        ]
        missing = [
            row
            for row in blocker_rows
            if row["risk_class"] == "high_missing_attribution"
        ]
        methods = Counter()
        families = Counter()
        family_damage = Counter()
        for row in blocker_rows:
            methods[row["evidence_method"]] += row["hits"]
            families[row["protocol_family"]] += row["hits"]
            family_damage[row["protocol_family"]] += row["damage"]
        priority_family = max(
            families,
            key=lambda family: (family_damage[family], families[family]),
            default="-",
        )
        blocker_hits = sum(row["hits"] for row in blocker_rows)
        blocker_damage = sum(row["damage"] for row in blocker_rows)
        wrong_hits = sum(row["hits"] for row in wrong_skill_risk)
        wrong_damage = sum(row["damage"] for row in wrong_skill_risk)
        misdeferred_hits = sum(row["hits"] for row in deferred_risk)
        misdeferred_damage = sum(row["damage"] for row in deferred_risk)
        risk_score = wrong_damage + misdeferred_damage
        jobs.append(
            {
                "job": job,
                "baseline_blocker_hits": int(baseline_hits.get(job, 0)),
                "baseline_blocker_damage": int(baseline_damage.get(job, 0)),
                "review_blocker_hits": blocker_hits,
                "review_blocker_damage": blocker_damage,
                "true_direct_blocker_hits": sum(row["hits"] for row in direct_rows),
                "true_direct_blocker_damage": sum(
                    row["damage"] for row in direct_rows
                ),
                "wrong_skill_risk_hits": wrong_hits,
                "wrong_skill_risk_damage": wrong_damage,
                "missing_attribution_hits": sum(row["hits"] for row in missing),
                "missing_attribution_damage": sum(row["damage"] for row in missing),
                "deferred_review_hits": sum(row["hits"] for row in deferred_rows),
                "deferred_review_damage": sum(row["damage"] for row in deferred_rows),
                "suspected_misdeferred_direct_hits": misdeferred_hits,
                "suspected_misdeferred_direct_damage": misdeferred_damage,
                "risk_damage": risk_score,
                "risk_level": (
                    "critical"
                    if wrong_hits or misdeferred_hits
                    else "high"
                    if blocker_hits
                    else "low"
                ),
                "priority_protocol_family": priority_family,
                "blocker_methods": _counter_json(methods),
                "blocker_protocol_families": _counter_json(families),
            }
        )

        skill_groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in blocker_rows:
            key = (row["skill_id"], row["skill_name"])
            target = skill_groups.setdefault(
                key,
                {
                    "job": job,
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "hits": 0,
                    "damage": 0,
                    "methods": Counter(),
                    "families": Counter(),
                    "semantic_classes": Counter(),
                    "risk_classes": Counter(),
                },
            )
            target["hits"] += row["hits"]
            target["damage"] += row["damage"]
            target["methods"][row["evidence_method"]] += row["hits"]
            target["families"][row["protocol_family"]] += row["hits"]
            target["semantic_classes"][row["semantic_class"]] += row["hits"]
            target["risk_classes"][row["risk_class"]] += row["hits"]
        ranked = sorted(
            skill_groups.values(), key=lambda row: (-row["damage"], -row["hits"])
        )[:10]
        for rank, row in enumerate(ranked, 1):
            top_skills.append(
                {
                    "job": job,
                    "rank": rank,
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "blocker_hits": row["hits"],
                    "blocker_damage": row["damage"],
                    "semantic_classes": _counter_json(row["semantic_classes"]),
                    "risk_classes": _counter_json(row["risk_classes"]),
                    "evidence_methods": _counter_json(row["methods"]),
                    "protocol_families": _counter_json(row["families"]),
                }
            )

    method_aggregate: dict[tuple[str, str, str], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    family_aggregate: dict[tuple[str, str, str], list[int]] = defaultdict(
        lambda: [0, 0]
    )
    risk_aggregate: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        if row["job"] not in SECOND_JOBS:
            continue
        method_key = (
            row["gate_class"],
            row["semantic_class"],
            row["evidence_method"],
        )
        family_key = (
            row["gate_class"],
            row["semantic_class"],
            row["protocol_family"],
        )
        risk_key = (row["risk_class"], row["semantic_class"])
        method_aggregate[method_key][0] += row["hits"]
        method_aggregate[method_key][1] += row["damage"]
        family_aggregate[family_key][0] += row["hits"]
        family_aggregate[family_key][1] += row["damage"]
        risk_aggregate[risk_key][0] += row["hits"]
        risk_aggregate[risk_key][1] += row["damage"]
    for key, value in sorted(method_aggregate.items(), key=lambda item: -item[1][1]):
        method_rows.append(
            {
                "gate_class": key[0],
                "semantic_class": key[1],
                "evidence_method": key[2],
                "hits": value[0],
                "damage": value[1],
            }
        )
    for key, value in sorted(family_aggregate.items(), key=lambda item: -item[1][1]):
        family_rows.append(
            {
                "gate_class": key[0],
                "semantic_class": key[1],
                "protocol_family": key[2],
                "hits": value[0],
                "damage": value[1],
            }
        )
    for key, value in sorted(risk_aggregate.items(), key=lambda item: -item[1][1]):
        risk_rows.append(
            {
                "risk_class": key[0],
                "semantic_class": key[1],
                "hits": value[0],
                "damage": value[1],
                "reason": risk_reasons.get(key[0], ""),
            }
        )

    jobs.sort(key=lambda row: (-row["risk_damage"], -row["review_blocker_damage"]))
    totals = {
        "baseline_blocker_hits": sum(
            int(baseline_hits.get(job, 0)) for job in SECOND_JOBS
        ),
        "baseline_blocker_damage": sum(
            int(baseline_damage.get(job, 0)) for job in SECOND_JOBS
        ),
        "review_blocker_hits": sum(row["review_blocker_hits"] for row in jobs),
        "review_blocker_damage": sum(row["review_blocker_damage"] for row in jobs),
        "true_direct_blocker_hits": sum(
            row["true_direct_blocker_hits"] for row in jobs
        ),
        "true_direct_blocker_damage": sum(
            row["true_direct_blocker_damage"] for row in jobs
        ),
        "wrong_skill_risk_hits": sum(row["wrong_skill_risk_hits"] for row in jobs),
        "wrong_skill_risk_damage": sum(
            row["wrong_skill_risk_damage"] for row in jobs
        ),
        "suspected_misdeferred_direct_hits": sum(
            row["suspected_misdeferred_direct_hits"] for row in jobs
        ),
        "suspected_misdeferred_direct_damage": sum(
            row["suspected_misdeferred_direct_damage"] for row in jobs
        ),
    }
    return {
        "rows": rows,
        "jobs": jobs,
        "top_skills": top_skills,
        "methods": method_rows,
        "families": family_rows,
        "risks": risk_rows,
        "totals": totals,
        "semantic_reasons": class_reasons,
        "risk_reasons": risk_reasons,
    }


def _render_markdown(result: dict[str, Any], captures: int) -> str:
    totals = result["totals"]
    lines = [
        "# corpus_201 直接伤害阻断语义复核",
        "",
        f"- 复核抓包：{captures} 份",
        "- 范围：30 个二转职业；只审计直接伤害阻断与 deferred 误排风险",
        "- 原则：覆盖率不等于正确率；时序/画像推断已经填出的技能名仍记为错归因风险",
        "- 本工具与报告不修改生产代码。",
        "",
        "## 总结",
        "",
        f"- 旧 corpus_201 blocker：{totals['baseline_blocker_hits']:,} hit / {totals['baseline_blocker_damage']:,} damage。",
        f"- 当前严格复核 blocker：{totals['review_blocker_hits']:,} hit / {totals['review_blocker_damage']:,} damage。",
        f"- 其中普通数值技能的真正直接伤害阻断：{totals['true_direct_blocker_hits']:,} hit / {totals['true_direct_blocker_damage']:,} damage。",
        f"- 已显示技能名但存在错归因风险：{totals['wrong_skill_risk_hits']:,} hit / {totals['wrong_skill_risk_damage']:,} damage。",
        f"- 疑似被 deferred 误排的直接/实体伤害：{totals['suspected_misdeferred_direct_hits']:,} hit / {totals['suspected_misdeferred_direct_damage']:,} damage。",
        "",
        "`deferred` 只表示当前没有一次游戏命中的严格证据，不等于已经证明它是板子、触发或持续附伤。尤其是普通数值技能 ID + 正伤害 + `supplemental_*` 的记录，必须继续做协议语义确认。",
        "",
        "## 30 个二转职业（按错归因风险 damage 排序）",
        "",
        "| 职业 | blocker hit | blocker damage | 真直接 hit | 错归因风险 hit | deferred 疑似误排 hit | 优先协议族 | 风险 |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in result["jobs"]:
        lines.append(
            f"| {row['job']} | {row['review_blocker_hits']:,} | {row['review_blocker_damage']:,} | "
            f"{row['true_direct_blocker_hits']:,} | {row['wrong_skill_risk_hits']:,} | "
            f"{row['suspected_misdeferred_direct_hits']:,} | {row['priority_protocol_family']} | {row['risk_level']} |"
        )
    lines.extend(
        [
            "",
            "## 最值得破解的协议族",
            "",
            "| gate | 语义 | 协议族 | hit | damage |",
            "|---|---|---|---:|---:|",
        ]
    )
    for row in result["families"][:20]:
        lines.append(
            f"| {row['gate_class']} | {row['semantic_class']} | {row['protocol_family']} | {row['hits']:,} | {row['damage']:,} |"
        )
    lines.extend(
        [
            "",
            "## 错归因风险登记",
            "",
            "| 风险 | 语义 | hit | damage | 原因 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in result["risks"]:
        if row["risk_class"] == "low_or_outside_direct_gate":
            continue
        lines.append(
            f"| {row['risk_class']} | {row['semantic_class']} | {row['hits']:,} | {row['damage']:,} | {row['reason']} |"
        )
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "- `true_direct_skill_damage_blocker` 是需要继续破解的直接技能伤害，不应被改名为附伤来提升覆盖率。",
            "- `suspected_direct_skill_misdeferred` 是误排风险，不在没有独立协议字段前强行回填技能。",
            "- `periodic/status`、明确 `supplemental:` 与 `direct_hit_overflow` 可继续暂缓，但必须保留事实伤害总额。",
            "- 召唤/持久实体伤害单列；它们不是普通板子附伤，应该走实体所有权链审计。",
            "- 每职业前十技能、方法与协议族见 CSV；完整机器可读汇总见 JSON。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review corpus direct blockers and deferred semantic leakage."
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT
        / "outputs/damage_attribution/corpus_201_direct_damage_audit_current/summary.json",
    )
    parser.add_argument(
        "--output-prefix",
        type=Path,
        default=ROOT / "outputs/damage_attribution/direct_blocker_semantic_review",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "outputs/skill_attribution_corpus_v5/skill_attribution_corpus.json",
    )
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=Path(
            r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
        ),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    capture_rows = baseline.get("capture_reports", baseline.get("reports", []))
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    corpus_sizes = {
        str(row.get("source", "")): int(row.get("size_bytes", 0) or 0)
        for row in corpus.get("files", [])
    }
    actual_by_size: dict[int, list[Path]] = defaultdict(list)
    for path in args.capture_root.rglob("*.dpscap"):
        actual_by_size[path.stat().st_size].append(path)
    captures: list[tuple[Path, str]] = []
    used_paths: set[Path] = set()
    for row in capture_rows:
        source_name = str(row.get("capture") or Path(row.get("path", "")).name)
        path = Path(row.get("path", ""))
        if not path.is_file():
            size = corpus_sizes.get(source_name, 0)
            candidates = sorted(
                actual_by_size.get(size, ()),
                key=lambda item: (
                    len(item.relative_to(args.capture_root).parts),
                    len(item.name),
                    str(item),
                ),
            )
            path = next(
                (candidate for candidate in candidates if candidate not in used_paths),
                candidates[0] if candidates else path,
            )
        if path.is_file() and path not in used_paths:
            captures.append((path, path.name))
            used_paths.add(path)
    if args.limit > 0:
        captures = captures[: args.limit]
    if not captures:
        raise SystemExit("No source captures from baseline report are available.")

    prefix = args.output_prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = Path(f"{prefix}_checkpoints")
    reports: list[dict[str, Any]] = []
    pending: list[tuple[Path, str, Path]] = []
    for capture, source_name in captures:
        checkpoint = _checkpoint_path(checkpoint_dir, capture)
        report = _load_checkpoint(checkpoint, capture)
        if report is None:
            pending.append((capture, source_name, checkpoint))
        else:
            reports.append(report)
    print(
        f"[resume] loaded={len(reports)} pending={len(pending)} total={len(captures)}",
        flush=True,
    )
    workers = max(1, min(int(args.workers), 8))
    if pending:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_audit_capture, str(capture), source_name): (
                    capture,
                    source_name,
                    checkpoint,
                )
                for capture, source_name, checkpoint in pending
            }
            for future in as_completed(futures):
                capture, source_name, checkpoint = futures[future]
                report = future.result()
                reports.append(report)
                _save_checkpoint(checkpoint, capture, report)
                print(
                    f"[{len(reports)}/{len(captures)}] {source_name}: groups={len(report['rows'])}",
                    flush=True,
                )

    result = _build_outputs(reports, baseline)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "source_baseline": str(args.baseline.resolve()),
        "source_baseline_analysis_signature": baseline.get("analysis_signature", {}),
        "captures": len(reports),
        "second_jobs": list(SECOND_JOBS),
        "policy": {
            "focus": "semantic correctness and wrong-attribution risk, not coverage alone",
            "allowed_deferred": [
                "explicit supplemental trigger/plate damage",
                "periodic/status damage",
                "zero/non-hit evidence",
            ],
            "must_not_be_silently_deferred": [
                "positive direct37/direct41 rows with ordinary numeric skill IDs",
                "persistent summon/entity direct damage",
            ],
        },
        "totals": result["totals"],
        "jobs": result["jobs"],
        "semantic_reasons": result["semantic_reasons"],
        "risk_reasons": result["risk_reasons"],
        "risk_register": result["risks"],
        "protocol_families": result["families"],
        "evidence_methods": result["methods"],
    }
    Path(f"{prefix}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        Path(f"{prefix}_jobs.csv"),
        result["jobs"],
        list(result["jobs"][0]),
    )
    _write_csv(
        Path(f"{prefix}_job_top10_skills.csv"),
        result["top_skills"],
        list(result["top_skills"][0]),
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
        Path(f"{prefix}_risk_register.csv"),
        result["risks"],
        list(result["risks"][0]),
    )
    Path(f"{prefix}_report.md").write_text(
        _render_markdown(result, len(reports)), encoding="utf-8"
    )
    print(
        "[done] "
        f"blockers={result['totals']['review_blocker_hits']} "
        f"wrong_risk={result['totals']['wrong_skill_risk_hits']} "
        f"misdeferred={result['totals']['suspected_misdeferred_direct_hits']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
