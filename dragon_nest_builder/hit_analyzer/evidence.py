from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..data_loader import norm
from ..live_dps.damage_attribution import (
    AttributionLayer,
    attribution_layer,
    is_deterministic_attribution,
)
from ..live_dps.models import CombatEvent


# Evidence tiers describe the link between one factual damage record and the
# player-facing skill shown in the report.  They are deliberately separate
# from ``has_real_hit_evidence``: a packet can prove that a real game hit
# happened without proving which skill owns that hit.
EVIDENCE_RANK = {"U": 0, "D": 1, "C": 2, "B": 3, "A": 4}


@dataclass(frozen=True, slots=True)
class HitSkillEvidence:
    tier: str
    label: str
    real_hit: bool | None
    skill_link_supported: bool
    circular: bool = False


@dataclass(frozen=True, slots=True)
class TargetTrackSelection:
    hits: tuple[CombatEvent, ...]
    target_count: int
    target_hit_counts: tuple[tuple[int, int], ...]
    target_consistency: float
    selection_reason: str


_EXPLICIT_SKILL_METHODS = frozenset(
    {
        "packet_skill_id",
        "damage_object_projectile_link",
    }
)
_DETERMINISTIC_ENTITY_METHODS = frozenset(
    {
        "skill_object_owner",
        "summon_owner",
        "supplemental_summon_owner",
        "retroactive_permanent_summon_owner",
    }
)
_DEPRECATED_RESULT_GROUP_METHODS = frozenset(
    {"direct_result_group_link", "direct_result_group_run_link"}
)
_CORROBORATED_SKILL_METHODS = frozenset(
    {"appended_status_first_hit", "derived_combo_basic_attack"}
)
_CONTEXTUAL_SKILL_METHODS = frozenset(
    {
        "continuation_window",
        "unique_active_cast",
        "sustained_cast_window",
        "periodic_cast_fallback",
        "periodic_status",
        "periodic_status_packet",
        "status_damage_packet",
        "refreshable_pulsing_aura",
        "supplemental_cast_owner",
        "supplemental_continuation",
        "supplemental_same_packet",
        "action_free_special_attack",
    }
)
_CIRCULAR_SKILL_METHODS = frozenset(
    {
        "learned_cast_profile",
        "latest_profiled_direct_cast",
        "action_hit_timing_fallback",
        "cast_instance_match",
    }
)


def classify_hit_skill_evidence(event: CombatEvent) -> HitSkillEvidence:
    """Classify skill ownership without confusing it with factual damage.

    Learned profiles and recent-cast fallbacks are useful in the live meter,
    but must not train the next profile generation.  Keeping them as tier D
    discovery evidence breaks that self-confirming loop.
    """

    method = norm(event.attribution_method).casefold()
    raw_method = norm(event.raw_attribution_method).casefold()
    real_hit = event.has_real_hit_evidence
    if real_hit is False:
        return HitSkillEvidence(
            tier="U",
            label="非Hit伤害/状态脉冲",
            real_hit=False,
            skill_link_supported=False,
        )
    skill_id = norm(event.skill_id).casefold()
    skill_name = norm(event.skill_name)
    if (
        method.startswith(("unresolved", "rejected", "v2_unresolved"))
        or skill_id in {"-1", "unknown", "unresolved"}
        or skill_name in {"待识别伤害", "未学习伤害", "unknown"}
        or (not skill_id and not skill_name)
    ):
        label = "真实Hit，技能归属未确定" if real_hit is True else "技能归属未确定"
        return HitSkillEvidence("U", label, real_hit, False)
    if method in _DEPRECATED_RESULT_GROUP_METHODS:
        return HitSkillEvidence(
            "D",
            "结果组时序继承不能证明技能归属",
            real_hit,
            False,
        )
    if method in _EXPLICIT_SKILL_METHODS or is_deterministic_attribution(method):
        return HitSkillEvidence("A", "包内技能/对象唯一映射", real_hit, True)
    if (
        method in _DETERMINISTIC_ENTITY_METHODS
        or attribution_layer(method) is AttributionLayer.STATUS
        or method in _CORROBORATED_SKILL_METHODS
    ):
        return HitSkillEvidence("B", "动作或实体关系佐证", real_hit, True)
    if method == "direct_hit_overflow":
        circular = raw_method in _CIRCULAR_SKILL_METHODS
        return HitSkillEvidence(
            "D" if circular else "C",
            "真实Hit，但技能归属来自溢出规则",
            real_hit,
            not circular,
            circular=circular,
        )
    if method in _CIRCULAR_SKILL_METHODS:
        return HitSkillEvidence(
            "D",
            "依赖既有技能模板/跨技能重配",
            real_hit,
            False,
            circular=True,
        )
    if method in _CONTEXTUAL_SKILL_METHODS:
        return HitSkillEvidence("C", "时序/状态上下文推断", real_hit, True)
    if method in {"recorded", "direct"}:
        # Synthetic tests and old serialized observations use ``recorded``.
        # Legacy rows without the explicit real-hit bit remain discovery-only.
        if real_hit is True:
            return HitSkillEvidence("B", "已记录真实Hit", True, True)
        return HitSkillEvidence("C", "旧记录，缺少Hit证据位", real_hit, True)
    if method:
        return HitSkillEvidence("C", f"其他归因:{method}", real_hit, True)
    return HitSkillEvidence("U", "缺少技能归因来源", real_hit, False)


def weakest_supported_tier(tiers: Iterable[str], default: str = "U") -> str:
    values = [tier for tier in tiers if tier in EVIDENCE_RANK]
    if not values:
        return default
    return min(values, key=lambda tier: EVIDENCE_RANK[tier])


def evidence_counts(events: Iterable[CombatEvent]) -> tuple[tuple[str, int], ...]:
    counts = Counter(classify_hit_skill_evidence(event).tier for event in events)
    return tuple(sorted(counts.items(), key=lambda item: -EVIDENCE_RANK[item[0]]))


def select_target_track(hits: Sequence[CombatEvent]) -> TargetTrackSelection:
    """Select one target track while preserving the multi-target audit trail.

    Hit count is a per-target property.  Counting every AoE target multiplies
    the result, while blindly choosing the largest damage target can select a
    partially hit add.  Prefer a target with an observed maximum-HP signal
    (normally the Boss), then the longest factual track and finally damage.
    The full per-target count distribution remains in the observation so low
    agreement cannot silently become a fixed-Hit template.
    """

    ordered = tuple(sorted(hits, key=lambda row: (row.timestamp_ms, row.target_id)))
    by_target: dict[str, list[CombatEvent]] = defaultdict(list)
    without_target: list[CombatEvent] = []
    for row in ordered:
        if row.target_id:
            by_target[row.target_id].append(row)
        else:
            without_target.append(row)
    if not by_target:
        return TargetTrackSelection(
            hits=ordered,
            target_count=1 if ordered else 0,
            target_hit_counts=((len(ordered), 1),) if ordered else (),
            target_consistency=1.0,
            selection_reason="伤害包未提供目标ID",
        )
    if len(by_target) == 1:
        rows = next(iter(by_target.values()))
        if without_target:
            histogram = Counter({len(rows): 1, len(without_target): 1})
            return TargetTrackSelection(
                hits=tuple(sorted(rows, key=lambda row: row.timestamp_ms)),
                target_count=2,
                target_hit_counts=tuple(sorted(histogram.items())),
                target_consistency=0.0,
                selection_reason="目标ID部分缺失，样本禁止训练",
            )
        return TargetTrackSelection(
            hits=tuple(rows),
            target_count=1,
            target_hit_counts=((len(rows), 1),),
            target_consistency=1.0,
            selection_reason="单目标轨迹",
        )

    def target_score(rows: list[CombatEvent]) -> tuple[int, int, int]:
        known_max_hp = max((row.target_max_hp for row in rows), default=0)
        return known_max_hp, len(rows), sum(max(0, row.amount) for row in rows)

    selected = max(by_target.values(), key=target_score)
    selected_count = len(selected)
    count_histogram = Counter(len(rows) for rows in by_target.values())
    consistency = count_histogram[selected_count] / max(1, len(by_target))
    if without_target:
        consistency = 0.0
    return TargetTrackSelection(
        hits=tuple(sorted(selected, key=lambda row: row.timestamp_ms)),
        target_count=len(by_target),
        target_hit_counts=tuple(sorted(count_histogram.items())),
        target_consistency=consistency,
        selection_reason=(
            "目标ID部分缺失，样本禁止训练"
            if without_target
            else (
                "优先最大HP目标，其次最长Hit轨迹和伤害"
                if any(
                    row.target_max_hp > 0
                    for rows in by_target.values()
                    for row in rows
                )
                else "缺少最大HP时选择最长Hit轨迹，其次伤害"
            )
        ),
    )
