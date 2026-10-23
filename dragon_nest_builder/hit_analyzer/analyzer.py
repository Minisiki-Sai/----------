from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import struct
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..data_loader import DATA_DIR, DataStore, as_number, norm
from ..live_dps.damage_object_origins import damage_object_active_seconds
from ..live_dps.damage_ledger import CastInstance
from ..live_dps.derived_combo_actions import load_derived_combo_action_rules
from ..live_dps.models import CombatEvent, SkillCastEvent
from ..live_dps.npcap import (
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    PREBATTLE_STATE_ADAPTER_INDEX,
    PROFILE_IDENTITY_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from ..live_dps.sources import V576DiagnosticReplaySource
from .evidence import (
    EVIDENCE_RANK,
    classify_hit_skill_evidence,
    evidence_counts,
    select_target_track,
    weakest_supported_tier,
)


ANALYZER_VERSION = 5
MAX_OBSERVATIONS_PER_SKILL = 2_000
_NORMAL_MATCH_WINDOW_MS = 3_000
_HIT_BEFORE_CAST_TOLERANCE_MS = 150
_FALLBACK_CLUSTER_GAP_MS = 750
_CLEAR_LATEST_HIT_WINDOW_MS = 1_000
_CLEAR_LATEST_CAST_SEPARATION_MS = 750
_PERSISTENT_METHOD_MARKERS = (
    "periodic",
    "status_damage",
    "summon",
    "skill_object",
    "pulsing_aura",
)
_UNRESOLVED_SKILL_NAMES = frozenset({"", "待识别伤害", "未学习伤害", "额外伤害"})
_DAMAGE_DERIVED_CAST_KINDS = frozenset(
    {"damage_inferred", "shared_timeline_damage"}
)
_ACTION_DERIVED_CAST_KINDS = frozenset({"inferred_combo"})
_INFERRED_CAST_KINDS = (
    _DAMAGE_DERIVED_CAST_KINDS | _ACTION_DERIVED_CAST_KINDS
)
_STRONG_CAST_KINDS = frozenset(
    {"skill_action", "status_skill_action", "transformed_basic_attack"}
)
_DIRECT_CAST_KINDS = frozenset({"skill_cast", "compact_skill_input"})
_SUPPORTED_CAST_KINDS = frozenset({"structured_multi_action", "direct", "legacy"})
_CAPTURE_MAGIC = b"DNDP1\x00"
_CAPTURE_FRAME_HEADER = struct.Struct("<dBII")
_APPENDIX_ADAPTERS = frozenset(
    {
        IDENTITY_TRACE_ADAPTER_INDEX,
        IDENTITY_METADATA_ADAPTER_INDEX,
        PROFILE_IDENTITY_ADAPTER_INDEX,
        SESSION_EVIDENCE_ADAPTER_INDEX,
        PREBATTLE_STATE_ADAPTER_INDEX,
    }
)


def skill_family(skill_name: object, skill_id: object = "") -> str:
    name = re.sub(r"\s+", "", norm(skill_name)).upper()
    if name:
        name = re.sub(r"(?:EX)(?:等级\d+)?$", "", name)
        return re.sub(r"等级\d+$", "", name)
    return norm(skill_id) or "UNKNOWN"


def skill_variant(skill_name: object, skill_id: object = "") -> str:
    """Return the exact game-facing variant without merging normal and EX."""

    name = re.sub(r"\s+", "", norm(skill_name))
    return name or norm(skill_id) or "UNKNOWN"


def _skill_key(job: object, variant: object, skill_id: object = "") -> str:
    identity = norm(skill_id) or "NO_ID"
    return (
        f"{norm(job) or '未知职业'}|{norm(variant) or 'UNKNOWN'}|{identity}"
    )


def _cast_evidence(packet_kind: object) -> tuple[str, str, bool]:
    kind = norm(packet_kind).casefold()
    if kind in _STRONG_CAST_KINDS:
        return "A", "服务端动作", True
    if kind in _DIRECT_CAST_KINDS:
        return "B", "客户端动作+真实命中", True
    if kind in _SUPPORTED_CAST_KINDS:
        return "C", "结构化动作候选", False
    if kind in _ACTION_DERIVED_CAST_KINDS:
        return "C", "前后动作链确认的派生动作", False
    if kind in _DAMAGE_DERIVED_CAST_KINDS:
        return "D", "推断动作", False
    if kind:
        return "C", f"其他动作:{kind}", False
    return "D", "仅伤害聚类", False


def _is_persistent(event: CombatEvent) -> bool:
    method = norm(event.attribution_method).lower()
    skill_id = norm(event.skill_id).lower()
    return skill_id.startswith("periodic:") or any(
        marker in method for marker in _PERSISTENT_METHOD_MARKERS
    )


def _median(values: Iterable[float], default: float = 0.0) -> float:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    return float(statistics.median(rows)) if rows else default


def _quantile(values: Iterable[float], fraction: float, default: float = 0.0) -> float:
    rows = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not rows:
        return default
    if len(rows) == 1:
        return rows[0]
    position = max(0.0, min(1.0, fraction)) * (len(rows) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return rows[lower]
    ratio = position - lower
    return rows[lower] * (1.0 - ratio) + rows[upper] * ratio


@dataclass(frozen=True, slots=True)
class HitObservation:
    job: str
    skill_name: str
    skill_family: str
    skill_id: str
    source_id: str
    source_name: str
    source_file: str
    cast_timestamp_ms: int
    hit_times: tuple[float, ...]
    hit_amounts: tuple[int, ...]
    critical_flags: tuple[bool, ...]
    action_lock_seconds: float | None
    reference_cast_seconds: float | None
    direct_cast: bool
    persistent: bool
    damage_record_count: int = 0
    target_result_count: int = 0
    auxiliary_times: tuple[float, ...] = field(default_factory=tuple)
    auxiliary_amounts: tuple[int, ...] = field(default_factory=tuple)
    hit_target_ids: tuple[str, ...] = field(default_factory=tuple)
    hit_attribution_methods: tuple[str, ...] = field(default_factory=tuple)
    hit_raw_attribution_methods: tuple[str, ...] = field(default_factory=tuple)
    hit_evidence_labels: tuple[str, ...] = field(default_factory=tuple)
    hit_confidences: tuple[float, ...] = field(default_factory=tuple)
    # Preserve per-Hit object/instance evidence for downstream timeline
    # matching.  Several projectile skills create multiple objects whose
    # high/low multiplier stages interleave on the wire; packet order alone
    # cannot reconstruct the correct stage in that case.
    hit_cast_instance_ids: tuple[str, ...] = field(default_factory=tuple)
    hit_damage_object_ids: tuple[int, ...] = field(default_factory=tuple)
    attribution_methods: tuple[str, ...] = field(default_factory=tuple)
    cast_packet_kind: str = ""
    evidence_tier: str = "D"
    evidence_label: str = "仅伤害聚类"
    cast_evidence_tier: str = "D"
    hit_evidence_tiers: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    circular_hit_count: int = 0
    reliable_cast: bool = False
    complete: bool = True
    censor_reasons: tuple[str, ...] = field(default_factory=tuple)
    target_count: int = 1
    target_hit_counts: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    target_consistency: float = 1.0
    target_selection: str = ""
    auxiliary_event_count: int = 0
    legacy_hit_evidence_count: int = 0
    mean_attribution_confidence: float = 0.0
    template_eligible: bool = False
    training_eligible: bool = False
    analysis_metadata: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return _skill_key(
            self.job,
            skill_variant(self.skill_name, self.skill_id),
            self.skill_id,
        )

    @property
    def profile_key(self) -> str:
        channel = "persistent_cadence" if self.persistent else "fixed_hit"
        return f"{self.key}|{channel}"

    @property
    def hit_count(self) -> int:
        return len(self.hit_times)

    @property
    def auxiliary_pulse_count(self) -> int:
        return len(self.auxiliary_times)

    @property
    def first_hit_seconds(self) -> float | None:
        return self.hit_times[0] if self.hit_times else None

    @property
    def last_hit_seconds(self) -> float | None:
        return self.hit_times[-1] if self.hit_times else None

    @property
    def hit_span_seconds(self) -> float:
        if len(self.hit_times) < 2:
            return 0.0
        return max(0.0, self.hit_times[-1] - self.hit_times[0])

    @property
    def hit_intervals(self) -> tuple[float, ...]:
        return tuple(
            max(0.0, current - previous)
            for previous, current in zip(self.hit_times, self.hit_times[1:])
        )

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> "HitObservation":
        action_lock = value.get("action_lock_seconds")
        reference_cast = value.get("reference_cast_seconds")
        return cls(
            job=norm(value.get("job")),
            skill_name=norm(value.get("skill_name")),
            skill_family=norm(value.get("skill_family")),
            skill_id=norm(value.get("skill_id")),
            source_id=norm(value.get("source_id")),
            source_name=norm(value.get("source_name")),
            source_file=norm(value.get("source_file")),
            cast_timestamp_ms=max(0, int(value.get("cast_timestamp_ms", 0) or 0)),
            hit_times=tuple(float(item) for item in value.get("hit_times", []) or []),
            hit_amounts=tuple(int(item) for item in value.get("hit_amounts", []) or []),
            critical_flags=tuple(bool(item) for item in value.get("critical_flags", []) or []),
            auxiliary_times=tuple(
                float(item) for item in value.get("auxiliary_times", []) or []
            ),
            auxiliary_amounts=tuple(
                int(item) for item in value.get("auxiliary_amounts", []) or []
            ),
            action_lock_seconds=(
                float(action_lock) if action_lock not in (None, "") else None
            ),
            reference_cast_seconds=(
                float(reference_cast) if reference_cast not in (None, "") else None
            ),
            direct_cast=bool(value.get("direct_cast", False)),
            persistent=bool(value.get("persistent", False)),
            damage_record_count=max(
                0,
                int(
                    value.get(
                        "damage_record_count",
                        len(value.get("hit_times", []) or [])
                        + len(value.get("auxiliary_times", []) or []),
                    )
                    or 0
                ),
            ),
            target_result_count=max(
                0,
                int(
                    value.get(
                        "target_result_count",
                        len(value.get("hit_times", []) or []),
                    )
                    or 0
                ),
            ),
            hit_target_ids=tuple(
                norm(item) for item in value.get("hit_target_ids", []) or []
            ),
            hit_attribution_methods=tuple(
                norm(item)
                for item in value.get("hit_attribution_methods", []) or []
            ),
            hit_raw_attribution_methods=tuple(
                norm(item)
                for item in value.get("hit_raw_attribution_methods", []) or []
            ),
            hit_evidence_labels=tuple(
                norm(item) for item in value.get("hit_evidence_labels", []) or []
            ),
            hit_confidences=tuple(
                max(0.0, min(1.0, float(item)))
                for item in value.get("hit_confidences", []) or []
            ),
            hit_cast_instance_ids=tuple(
                norm(item)
                for item in value.get("hit_cast_instance_ids", []) or []
            ),
            hit_damage_object_ids=tuple(
                max(0, int(item or 0))
                for item in value.get("hit_damage_object_ids", []) or []
            ),
            attribution_methods=tuple(
                norm(item) for item in value.get("attribution_methods", []) or []
            ),
            cast_packet_kind=norm(value.get("cast_packet_kind")),
            evidence_tier=norm(value.get("evidence_tier")) or "D",
            evidence_label=norm(value.get("evidence_label")) or "仅伤害聚类",
            cast_evidence_tier=(
                norm(value.get("cast_evidence_tier"))
                or norm(value.get("evidence_tier"))
                or "D"
            ),
            hit_evidence_tiers=tuple(
                (norm(item[0]), int(item[1]))
                for item in value.get("hit_evidence_tiers", []) or []
                if isinstance(item, (list, tuple)) and len(item) >= 2
            ),
            circular_hit_count=max(
                0, int(value.get("circular_hit_count", 0) or 0)
            ),
            reliable_cast=bool(value.get("reliable_cast", value.get("direct_cast", False))),
            complete=bool(value.get("complete", True)),
            censor_reasons=tuple(
                norm(item) for item in value.get("censor_reasons", []) or []
            ),
            target_count=max(0, int(value.get("target_count", 1) or 0)),
            target_hit_counts=tuple(
                (int(item[0]), int(item[1]))
                for item in value.get("target_hit_counts", []) or []
                if isinstance(item, (list, tuple)) and len(item) >= 2
            ),
            target_consistency=max(
                0.0, min(1.0, float(value.get("target_consistency", 1.0) or 0.0))
            ),
            target_selection=norm(value.get("target_selection")),
            auxiliary_event_count=max(
                0, int(value.get("auxiliary_event_count", 0) or 0)
            ),
            legacy_hit_evidence_count=max(
                0, int(value.get("legacy_hit_evidence_count", 0) or 0)
            ),
            mean_attribution_confidence=max(
                0.0,
                min(1.0, float(value.get("mean_attribution_confidence", 0.0) or 0.0)),
            ),
            analysis_metadata=dict(value.get("analysis_metadata") or {}),
            template_eligible=bool(value.get("template_eligible", False)),
            training_eligible=bool(
                value.get("training_eligible", value.get("template_eligible", False))
            ),
        )


@dataclass(frozen=True, slots=True)
class HitPositionSummary:
    index: int
    time_seconds: float
    time_p10: float
    time_p90: float
    share: float
    share_p10: float
    share_p90: float
    samples: int
    interval_seconds: float | None = None
    interval_p10: float | None = None
    interval_p90: float | None = None


@dataclass(frozen=True, slots=True)
class SkillHitSummary:
    key: str
    job: str
    skill_name: str
    skill_family: str
    sample_count: int
    hit_count: int
    alternative_hit_counts: tuple[tuple[int, int], ...]
    hit_consistency: float
    direct_cast_ratio: float
    first_hit_seconds: float
    last_hit_seconds: float
    hit_span_seconds: float
    action_lock_seconds: float | None
    reference_cast_seconds: float | None
    persistent: bool
    confidence: float
    status: str
    positions: tuple[HitPositionSummary, ...]
    manual_hit_count: int | None = None
    manual_cast_seconds: float | None = None
    eligible_sample_count: int = 0
    file_count: int = 0
    actor_count: int = 0
    reliable_cast_ratio: float = 0.0
    strong_cast_ratio: float = 0.0
    complete_ratio: float = 0.0
    censored_sample_count: int = 0
    auxiliary_event_count: int = 0
    legacy_hit_evidence_count: int = 0
    evidence_tiers: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    selection_basis: str = ""
    skill_id: str = ""
    training_sample_count: int = 0
    discovery_sample_count: int = 0
    circular_hit_count: int = 0
    auxiliary_pulse_count: int = 0
    damage_record_count: int = 0
    target_result_count: int = 0
    auxiliary_interval_seconds: float | None = None
    target_consistency: float = 1.0
    cross_file_consistency: float = 0.0
    balanced_top_share: float = 0.0
    balanced_margin: float = 0.0
    hit_count_p10: float = 0.0
    hit_count_median: float = 0.0
    hit_count_p90: float = 0.0
    capture_balanced_hit_counts: tuple[tuple[int, float], ...] = field(
        default_factory=tuple
    )
    modal_hit_count: int = 0
    observed_max_hit_count: int = 0
    full_hit_sample_count: int = 0
    full_hit_file_count: int = 0
    full_hit_actor_count: int = 0
    full_hit_support_share: float = 0.0
    hit_count_selection: str = "capture_balanced_mode_fallback"
    hit_evidence_tiers: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    analysis_mode: str = "fixed_hit_candidate"

    @property
    def display_hit_count(self) -> int:
        return self.manual_hit_count or self.hit_count

    @property
    def display_cast_seconds(self) -> float | None:
        return (
            self.manual_cast_seconds
            if self.manual_cast_seconds is not None
            else self.action_lock_seconds
        )


def _critical_multiplier(observations: Sequence[HitObservation], hit_count: int) -> float:
    ratios: list[float] = []
    for index in range(hit_count):
        strata: dict[tuple[str, str], list[tuple[int, bool]]] = defaultdict(list)
        for row in observations:
            if (
                len(row.hit_amounts) == hit_count
                and len(row.critical_flags) > index
                and row.hit_amounts[index] > 0
            ):
                strata[(row.source_file, row.source_id)].append(
                    (row.hit_amounts[index], row.critical_flags[index])
                )
        for values in strata.values():
            normal = [amount for amount, critical in values if not critical]
            critical = [amount for amount, critical in values if critical]
            if normal and critical:
                ratios.append(_median(critical) / max(1.0, _median(normal)))
    ratio = _median(ratios, 2.0)
    return max(1.2, min(4.0, ratio))


def summarize_observations(
    observations: Sequence[HitObservation],
    manual: dict[str, object] | None = None,
) -> SkillHitSummary:
    if not observations:
        raise ValueError("至少需要一个技能样本")
    manual = manual or {}
    counts = Counter(row.hit_count for row in observations if row.hit_count > 0)
    persistent_mode = any(row.persistent for row in observations)
    training = [
        row
        for row in observations
        if row.training_eligible and row.hit_count > 0
    ]
    if persistent_mode:
        selection_rows = [
            row
            for row in observations
            if row.complete
            and row.reliable_cast
            and (row.hit_count > 0 or row.auxiliary_pulse_count > 0)
        ]
        if not selection_rows:
            selection_rows = [
                row
                for row in observations
                if row.hit_count > 0 or row.auxiliary_pulse_count > 0
            ]
        selection_basis = "持续场/召唤物：仅统计真实Hit与非Hit脉冲节奏"
    elif training:
        selection_rows = training
        selection_basis = "完整动作包＋非循环技能归属的真实Hit样本"
    else:
        selection_rows = [
            row
            for row in observations
            if row.reliable_cast
            and row.complete
            and row.hit_count > 0
            and row.circular_hit_count == 0
        ]
        if selection_rows:
            selection_basis = "可靠动作包＋上下文归属样本（只作候选）"
        else:
            selection_rows = [
                row
                for row in observations
                if row.complete and row.hit_count > 0
            ]
            if selection_rows:
                selection_basis = "完整伤害样本（低置信，仅供排查）"
            else:
                selection_rows = list(observations)
                selection_basis = "全部样本（仅供排查）"

    # A long recording can contain hundreds of casts while another recording
    # contains only one.  Let every independent capture contribute one vote,
    # then use the packet-evidence tier only inside that capture.  This avoids
    # one player/session dominating the recommended Hit count.
    tier_weight = {"A": 1.0, "B": 0.85, "C": 0.55, "D": 0.25}
    by_file: dict[str, dict[str, list[HitObservation]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for index, row in enumerate(selection_rows):
        file_key = row.source_file or f"<memory:{index}>"
        actor_key = row.source_id or "<unknown-actor>"
        by_file[file_key][actor_key].append(row)
    balanced_scores: dict[int, float] = defaultdict(float)
    local_file_modes: list[int] = []
    for actors in by_file.values():
        file_local: dict[int, float] = defaultdict(float)
        contributing_actors = 0
        for rows in actors.values():
            actor_local: dict[int, float] = defaultdict(float)
            for row in rows:
                if row.hit_count > 0:
                    actor_local[row.hit_count] += tier_weight.get(
                        row.evidence_tier, 0.25
                    )
            actor_total = sum(actor_local.values())
            if actor_total <= 0:
                continue
            contributing_actors += 1
            for hit_count, weight in actor_local.items():
                file_local[hit_count] += weight / actor_total
        if contributing_actors <= 0:
            continue
        for hit_count, weight in file_local.items():
            file_local[hit_count] = weight / contributing_actors
        file_total = sum(file_local.values())
        for hit_count, weight in file_local.items():
            balanced_scores[hit_count] += weight / file_total
        if file_local:
            local_file_modes.append(
                max(file_local, key=lambda value: (file_local[value], value))
            )
    if balanced_scores:
        modal_hit_count = max(
            balanced_scores,
            key=lambda value: (
                balanced_scores[value],
                sum(row.hit_count == value for row in selection_rows),
                value,
            ),
        )
    else:
        modal_hit_count = counts.most_common(1)[0][0] if counts else 0

    # Missing the edge of a moving Boss commonly produces fewer factual hits,
    # while a single-target cast should not produce more than its full pattern.
    # Select the largest independently repeated count, not the most common
    # partial count.  Isolated large values remain visible in the histogram but
    # cannot become the standard (they are usually merged casts or bad bounds).
    balanced_total = sum(balanced_scores.values())
    independently_supported_counts: list[int] = []
    for hit_count in sorted(balanced_scores):
        exact_rows = [row for row in selection_rows if row.hit_count == hit_count]
        exact_files = {row.source_file for row in exact_rows if row.source_file}
        exact_actors = {
            (row.source_file, row.source_id)
            for row in exact_rows
            if row.source_id
        }
        support_share = (
            balanced_scores[hit_count] / balanced_total
            if balanced_total > 0
            else 0.0
        )
        if (
            len(exact_rows) >= 3
            and (len(exact_files) >= 2 or len(exact_actors) >= 2)
            and support_share >= 0.05
        ):
            independently_supported_counts.append(hit_count)
    if independently_supported_counts and not persistent_mode:
        selected_hit_count = max(independently_supported_counts)
        hit_count_selection = "independently_supported_upper_envelope"
    else:
        selected_hit_count = modal_hit_count
        hit_count_selection = "capture_balanced_mode_fallback"

    representative = [
        row for row in selection_rows if row.hit_count == selected_hit_count
    ]
    if not representative:
        representative = [
            row for row in observations if row.hit_count == selected_hit_count
        ]
    if not representative:
        representative = list(selection_rows or observations)

    def capture_balanced_values(
        rows: Sequence[HitObservation],
        getter: Callable[[HitObservation], float | None],
    ) -> list[float]:
        grouped_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row_index, row in enumerate(rows):
            value = getter(row)
            if value is None or not math.isfinite(float(value)):
                continue
            file_key = row.source_file or f"<memory:{row_index}>"
            actor_key = row.source_id or "<unknown-actor>"
            grouped_values[file_key][actor_key].append(float(value))
        return [
            _median(
                _median(actor_values)
                for actor_values in actors.values()
                if actor_values
            )
            for actors in grouped_values.values()
            if actors
        ]

    crit_multiplier = _critical_multiplier(representative, selected_hit_count)
    normalized_shares: list[tuple[HitObservation, tuple[float, ...]]] = []
    for row in representative:
        corrected = [
            amount / crit_multiplier if critical else float(amount)
            for amount, critical in zip(row.hit_amounts, row.critical_flags)
        ]
        total = sum(corrected)
        normalized_shares.append(
            (
                row,
                tuple(amount / total for amount in corrected) if total > 0 else (),
            )
        )
    positions: list[HitPositionSummary] = []
    for index in range(selected_hit_count):
        times = capture_balanced_values(
            representative,
            lambda row, i=index: row.hit_times[i]
            if len(row.hit_times) > i
            else None,
        )
        share_by_row = {id(row): values for row, values in normalized_shares}
        shares = capture_balanced_values(
            representative,
            lambda row, i=index: share_by_row[id(row)][i]
            if len(share_by_row.get(id(row), ())) > i
            else None,
        )
        intervals = capture_balanced_values(
            representative,
            lambda row, i=index: (
                row.hit_times[i] - row.hit_times[i - 1]
                if i > 0 and len(row.hit_times) > i
                else None
            ),
        )
        positions.append(
            HitPositionSummary(
                index=index + 1,
                time_seconds=_median(times),
                time_p10=_quantile(times, 0.10),
                time_p90=_quantile(times, 0.90),
                share=_median(shares),
                share_p10=_quantile(shares, 0.10),
                share_p90=_quantile(shares, 0.90),
                samples=len(times),
                interval_seconds=_median(intervals) if intervals else None,
                interval_p10=_quantile(intervals, 0.10) if intervals else None,
                interval_p90=_quantile(intervals, 0.90) if intervals else None,
            )
        )
    share_total = sum(position.share for position in positions)
    if share_total > 0:
        positions = [
            replace(position, share=position.share / share_total)
            for position in positions
        ]
    consistency = (
        balanced_scores.get(selected_hit_count, 0.0) / balanced_total
        if balanced_total > 0
        else 0.0
    )
    direct_ratio = sum(row.direct_cast for row in observations) / max(1, len(observations))
    reliable_ratio = sum(row.reliable_cast for row in observations) / max(
        1, len(observations)
    )
    strong_ratio = sum(row.evidence_tier == "A" for row in observations) / max(
        1, len(observations)
    )
    complete_ratio = sum(row.complete for row in observations) / max(
        1, len(observations)
    )
    locks = capture_balanced_values(
        representative,
        lambda row: row.action_lock_seconds
        if row.action_lock_seconds is not None
        and 0 < row.action_lock_seconds <= 30
        else None,
    )
    reference = capture_balanced_values(
        observations,
        lambda row: row.reference_cast_seconds
        if row.reference_cast_seconds is not None and row.reference_cast_seconds > 0
        else None,
    )
    time_spreads = [max(0.0, pos.time_p90 - pos.time_p10) for pos in positions]
    typical_span = _median(
        capture_balanced_values(
            representative,
            lambda row: row.hit_span_seconds,
        )
    )
    stable_time = 1.0 - min(
        1.0, _median(time_spreads) / max(0.15, typical_span or 0.15)
    )
    sample_score = min(1.0, math.log2(len(selection_rows) + 1) / 5.0)
    file_count = len({row.source_file for row in selection_rows if row.source_file})
    if not file_count and selection_rows:
        file_count = 1
    file_score = min(1.0, math.log2(file_count + 1) / 3.0)
    cross_file_consistency = (
        sum(value == selected_hit_count for value in local_file_modes)
        / max(1, len(local_file_modes))
        if selected_hit_count > 0
        else 0.0
    )
    ordered_balanced_scores = sorted(balanced_scores.values(), reverse=True)
    balanced_top_share = consistency
    balanced_runner_up_share = (
        ordered_balanced_scores[1] / balanced_total
        if balanced_total > 0 and len(ordered_balanced_scores) > 1
        else 0.0
    )
    balanced_margin = max(0.0, balanced_top_share - balanced_runner_up_share)
    target_consistency = _median(
        (
            row.target_consistency
            for row in selection_rows
            if row.hit_count > 0
        ),
        1.0,
    )
    circular_hits = sum(row.circular_hit_count for row in observations)
    non_circular_ratio = 1.0 - min(
        1.0, circular_hits / max(1, sum(row.hit_count for row in observations))
    )
    evidence_score = _median(
        EVIDENCE_RANK.get(row.evidence_tier, 0) / EVIDENCE_RANK["A"]
        for row in selection_rows
        if row.hit_count > 0
    )
    confidence = 100.0 * (
        0.12 * sample_score
        + 0.18 * consistency
        + 0.12 * reliable_ratio
        + 0.10 * stable_time
        + 0.16 * file_score
        + 0.12 * cross_file_consistency
        + 0.08 * target_consistency
        + 0.12 * evidence_score
    )
    confidence *= non_circular_ratio
    if persistent_mode:
        confidence *= 0.85
    if not training and not persistent_mode:
        confidence = min(confidence, 60.0)
    if circular_hits:
        confidence = min(confidence, 45.0)
    if file_count <= 1:
        confidence = min(confidence, 72.0)
    manually_confirmed = bool(manual.get("confirmed", False))
    if manually_confirmed:
        status = "已人工确认"
    elif persistent_mode:
        status = "持续/召唤：仅统计Hit与脉冲节奏"
    elif circular_hits:
        status = "含循环归因：禁止训练"
    elif (
        consistency < 0.67
        or balanced_margin < 0.20
    ) and len(selection_rows) >= 5:
        status = "多形态/待拆分"
    elif (
        confidence >= 80
        and len(training) >= 15
        and file_count >= 3
        and len(
            {
                (row.source_file, row.source_id)
                for row in training
                if row.source_id
            }
        ) >= 2
        and cross_file_consistency >= 0.67
        and balanced_top_share >= 0.67
        and balanced_margin >= 0.20
    ):
        status = "高置信候选"
    elif len(selection_rows) >= 2:
        status = "待复核"
    else:
        status = "样本不足"
    first_hits = capture_balanced_values(
        representative,
        lambda row: row.first_hit_seconds,
    )
    last_hits = capture_balanced_values(
        representative,
        lambda row: row.last_hit_seconds,
    )
    manual_hit_count = int(manual.get("hit_count", 0) or 0) or None
    manual_cast_raw = manual.get("cast_seconds")
    manual_cast_seconds = (
        float(manual_cast_raw) if manual_cast_raw not in (None, "") else None
    )
    first = observations[0]
    display_names = Counter(row.skill_name for row in observations if row.skill_name)
    skill_name = display_names.most_common(1)[0][0] if display_names else first.skill_family
    skill_ids = Counter(row.skill_id for row in observations if row.skill_id)
    selected_hit_counts = [row.hit_count for row in selection_rows if row.hit_count > 0]
    auxiliary_intervals = [
        current - previous
        for row in selection_rows
        for previous, current in zip(row.auxiliary_times, row.auxiliary_times[1:])
        if current >= previous
    ]
    aggregate_hit_evidence: Counter[str] = Counter()
    for row in observations:
        aggregate_hit_evidence.update(dict(row.hit_evidence_tiers))
    return SkillHitSummary(
        key=first.profile_key,
        job=first.job or "未知职业",
        skill_name=skill_name,
        skill_family=first.skill_family,
        sample_count=len(observations),
        hit_count=selected_hit_count,
        alternative_hit_counts=tuple(sorted(counts.items())),
        hit_consistency=consistency,
        direct_cast_ratio=direct_ratio,
        first_hit_seconds=_median(first_hits),
        last_hit_seconds=_median(last_hits),
        hit_span_seconds=typical_span,
        action_lock_seconds=_quantile(locks, 0.20) if locks else None,
        reference_cast_seconds=_median(reference) if reference else None,
        persistent=persistent_mode,
        confidence=max(0.0, min(100.0, confidence)),
        status=status,
        positions=tuple(positions),
        manual_hit_count=manual_hit_count,
        manual_cast_seconds=manual_cast_seconds,
        eligible_sample_count=len(training),
        file_count=file_count,
        actor_count=len(
            {
                (row.source_file, row.source_id)
                for row in selection_rows
                if row.source_id
            }
        ),
        reliable_cast_ratio=reliable_ratio,
        strong_cast_ratio=strong_ratio,
        complete_ratio=complete_ratio,
        censored_sample_count=sum(not row.complete for row in observations),
        auxiliary_event_count=sum(row.auxiliary_event_count for row in observations),
        legacy_hit_evidence_count=sum(
            row.legacy_hit_evidence_count for row in observations
        ),
        evidence_tiers=tuple(
            sorted(Counter(row.evidence_tier for row in observations).items())
        ),
        selection_basis=(
            f"{selection_basis}；标准Hit按跨抓包/角色重复的完整命中上界选择"
            if hit_count_selection == "independently_supported_upper_envelope"
            else f"{selection_basis}；独立完整样本不足，暂用抓包平衡众数"
        ),
        skill_id=skill_ids.most_common(1)[0][0] if skill_ids else first.skill_id,
        training_sample_count=len(training),
        discovery_sample_count=max(0, len(observations) - len(training)),
        circular_hit_count=circular_hits,
        auxiliary_pulse_count=sum(row.auxiliary_pulse_count for row in observations),
        damage_record_count=sum(row.damage_record_count for row in observations),
        target_result_count=sum(row.target_result_count for row in observations),
        auxiliary_interval_seconds=(
            _median(auxiliary_intervals) if auxiliary_intervals else None
        ),
        target_consistency=target_consistency,
        cross_file_consistency=cross_file_consistency,
        balanced_top_share=balanced_top_share,
        balanced_margin=balanced_margin,
        hit_count_p10=_quantile(selected_hit_counts, 0.10),
        hit_count_median=_median(selected_hit_counts),
        hit_count_p90=_quantile(selected_hit_counts, 0.90),
        capture_balanced_hit_counts=tuple(
            sorted(
                (
                    hit_count,
                    weight / balanced_total if balanced_total > 0 else 0.0,
                )
                for hit_count, weight in balanced_scores.items()
            )
        ),
        modal_hit_count=modal_hit_count,
        observed_max_hit_count=max(selected_hit_counts, default=0),
        full_hit_sample_count=len(representative),
        full_hit_file_count=len(
            {row.source_file for row in representative if row.source_file}
        ) or (1 if representative else 0),
        full_hit_actor_count=len(
            {
                (row.source_file, row.source_id)
                for row in representative
                if row.source_id
            }
        ),
        full_hit_support_share=consistency,
        hit_count_selection=hit_count_selection,
        hit_evidence_tiers=tuple(
            sorted(
                aggregate_hit_evidence.items(),
                key=lambda item: -EVIDENCE_RANK.get(item[0], -1),
            )
        ),
        analysis_mode=(
            "persistent_cadence"
            if persistent_mode
            else ("fixed_hit_candidate" if training else "discovery_only")
        ),
    )


class SkillHitAnalyzer:
    def __init__(
        self,
        store: DataStore | None = None,
        data_root: Path | None = None,
    ) -> None:
        self.store = store
        self.data_root = Path(data_root or DATA_DIR)
        self._reference_cache: dict[tuple[str, str], float | None] = {}
        self._duration_cache: dict[tuple[str, str], float] = {}
        self._derived_combo_action_rules = load_derived_combo_action_rules(
            self.data_root
        )
        self.last_diagnostics: dict[str, object] = {}

    def _reference_cast_time(self, job: str, skill_name: str) -> float | None:
        key = (job, skill_name)
        if key in self._reference_cache:
            return self._reference_cache[key]
        result: float | None = None
        if self.store is not None:
            try:
                timing = self.store.skill_cast_time(skill_name, [job])
                result = next(
                    (
                        value
                        for value in (
                            as_number(timing.get("corrected_cast_time")),
                            as_number(timing.get("cast_time")),
                            as_number(timing.get("corrected_quick_time")),
                            as_number(timing.get("quick_time")),
                        )
                        if value > 0
                    ),
                    None,
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                result = None
        self._reference_cache[key] = result
        return result

    def _effect_duration(self, job: str, skill_name: str) -> float:
        key = (job, skill_name)
        if key in self._duration_cache:
            return self._duration_cache[key]
        duration = 0.0
        if self.store is not None:
            try:
                timing = self.store.skill_effect_timing(
                    job, skill_name, max_required_level=70
                )
                duration = max(0.0, as_number(timing.get("duration")))
            except (AttributeError, KeyError, TypeError, ValueError):
                duration = 0.0
        self._duration_cache[key] = duration
        return duration

    def _normal_window_ms(self, cast: SkillCastEvent) -> int:
        reference = self._reference_cast_time(cast.job, cast.skill_name) or 0.0
        return max(_NORMAL_MATCH_WINDOW_MS, int((reference + 1.0) * 1000))

    def _instance_link_window_ms(
        self,
        cast: SkillCastEvent,
        event: CombatEvent,
    ) -> int:
        """Use a decoder-owned instance's factual lifetime for delayed hits.

        A normal action window is intentionally short. Registered Projectile
        objects can keep producing direct37 rows after that window, and their
        decoder-provided damage-object ID is stronger evidence than a generic
        time match. The extension is available only to non-circular A/B
        evidence and therefore cannot make inferred damage self-train.
        """

        ordinary = self._normal_window_ms(cast)
        evidence = classify_hit_skill_evidence(event)
        if (
            not event.cast_instance_id
            or evidence.tier not in {"A", "B"}
            or not evidence.skill_link_supported
            or evidence.circular
        ):
            return ordinary
        active_seconds = damage_object_active_seconds(
            event.raw_skill_id or event.skill_id or cast.skill_id
        )
        if active_seconds is not None:
            return max(ordinary, round((active_seconds + 0.50) * 1000.0))
        # Object/projectile IDs are authoritative ownership links, but their
        # lifetime still follows the configured skill duration when present.
        duration = self._effect_duration(cast.job, cast.skill_name)
        if duration > 0.0:
            return max(ordinary, round((min(duration, 30.0) + 0.50) * 1000.0))
        return ordinary

    def _persistent_window_ms(self, cast: SkillCastEvent) -> int:
        duration = self._effect_duration(cast.job, cast.skill_name)
        return int(max(12.0, min(90.0, duration + 2.0)) * 1000)

    @staticmethod
    def _event_display_name(event: CombatEvent) -> str:
        current = norm(event.skill_name)
        raw = norm(event.raw_skill_name)
        if current in _UNRESOLVED_SKILL_NAMES and raw not in _UNRESOLVED_SKILL_NAMES:
            return raw
        return current or raw or "待识别伤害"

    @classmethod
    def _event_family(cls, event: CombatEvent) -> str:
        display = cls._event_display_name(event)
        skill_id = event.raw_skill_id or event.skill_id
        return skill_family(display, skill_id)

    @staticmethod
    def _is_resolved_name(value: object) -> bool:
        return norm(value) not in _UNRESOLVED_SKILL_NAMES

    @staticmethod
    def _cast_channel(packet_kind: object) -> str:
        kind = norm(packet_kind).casefold()
        if kind in _STRONG_CAST_KINDS or kind == "structured_multi_action":
            return "server"
        if kind in _DIRECT_CAST_KINDS:
            return "client"
        if kind in _INFERRED_CAST_KINDS:
            return "inferred"
        return "other"

    @classmethod
    def _same_cast_confirmation(
        cls,
        previous: CastInstance,
        current: SkillCastEvent,
    ) -> bool:
        delta = current.timestamp_ms - previous.start_ms
        if delta < 0 or delta > 250:
            return False
        old_channel = cls._cast_channel(previous.packet_kind)
        new_channel = cls._cast_channel(current.packet_kind)
        if delta <= 20 and norm(previous.skill_id) == norm(current.skill_id):
            return True
        # Client input and server action echoes are two observations of one
        # cast.  Two same-channel actions can be legitimate rapid casts and
        # must not be merged merely because they are close in time.
        return (
            old_channel != new_channel
            and "inferred" not in {old_channel, new_channel}
            and {old_channel, new_channel}.issubset({"client", "server", "other"})
        )

    def _virtual_instances(
        self, casts: Sequence[SkillCastEvent]
    ) -> list[CastInstance]:
        """Build deterministic instances for tests and legacy callers.

        Full `.dpscap` analysis uses the production attribution ledger.  This
        fallback deliberately mirrors its 200 ms action de-duplication so the
        public `analyze_events` API remains useful for synthetic tests.
        """

        result: list[CastInstance] = []
        last_index_by_key: dict[tuple[str, str], int] = {}
        ordinal_by_key: dict[tuple[str, str], int] = defaultdict(int)
        evidence_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
        for cast in sorted(casts, key=lambda row: (row.timestamp_ms, row.source_id)):
            family = skill_family(cast.skill_name, cast.skill_id)
            key = (cast.source_id, family)
            previous_index = last_index_by_key.get(key)
            if previous_index is not None:
                previous = result[previous_index]
                if self._same_cast_confirmation(previous, cast):
                    old_tier = _cast_evidence(previous.packet_kind)[0]
                    new_tier = _cast_evidence(cast.packet_kind)[0]
                    if evidence_rank.get(new_tier, 0) > evidence_rank.get(old_tier, 0):
                        result[previous_index] = CastInstance(
                            instance_id=previous.instance_id,
                            source_id=cast.source_id,
                            source_name=cast.source_name,
                            job=cast.job,
                            skill_id=cast.skill_id,
                            skill_name=cast.skill_name,
                            family=family,
                            start_ms=cast.timestamp_ms,
                            expected_end_ms=cast.timestamp_ms
                            + self._normal_window_ms(cast),
                            packet_kind=cast.packet_kind,
                            inferred=cast.packet_kind.casefold() in _INFERRED_CAST_KINDS,
                        )
                    continue
            ordinal_by_key[key] += 1
            window = self._normal_window_ms(cast)
            result.append(
                CastInstance(
                    instance_id=(
                        f"{cast.source_id}:{family}:{cast.timestamp_ms}:"
                        f"{ordinal_by_key[key]}"
                    ),
                    source_id=cast.source_id,
                    source_name=cast.source_name,
                    job=cast.job,
                    skill_id=cast.skill_id,
                    skill_name=cast.skill_name,
                    family=family,
                    start_ms=cast.timestamp_ms,
                    expected_end_ms=cast.timestamp_ms + window,
                    packet_kind=cast.packet_kind,
                    inferred=cast.packet_kind.casefold() in _INFERRED_CAST_KINDS,
                )
            )
            last_index_by_key[key] = len(result) - 1
        return result

    def _resolve_derived_combo_damage(
        self,
        casts: Sequence[SkillCastEvent],
        hits: Sequence[CombatEvent],
    ) -> tuple[list[CombatEvent], int]:
        """Attach a wire-level basic-attack Hit to a proven hidden action.

        Some continuation buttons do not publish an ordinary cast row.  For
        example, 飓风舞 → 踢飞 → 双飞腿 records the kick damage with the
        packet's basic/special-attack key.  The later 双飞腿 action proves the
        otherwise hidden kick.  Reassign only when the configured predecessor,
        synthetic intermediate action and exactly one packet basic-attack Hit
        all agree inside the narrow action window.  Ambiguous rows stay in the
        ordinary/special-attack bucket.
        """

        if not self._derived_combo_action_rules:
            return list(hits), 0
        casts_by_source: dict[str, list[SkillCastEvent]] = defaultdict(list)
        for cast_row in sorted(
            casts, key=lambda row: (row.source_id, row.timestamp_ms)
        ):
            casts_by_source[cast_row.source_id].append(cast_row)
        hit_rows = list(hits)
        used_hit_indexes: set[int] = set()
        replacements: dict[int, CombatEvent] = {}
        for source_id, source_casts in casts_by_source.items():
            concrete_casts = [
                row
                for row in source_casts
                if norm(row.packet_kind).casefold() != "inferred_combo"
            ]
            inferred_casts = [
                row
                for row in source_casts
                if norm(row.packet_kind).casefold() == "inferred_combo"
            ]
            history: list[tuple[int, float]] = []
            history_cursor = 0
            for trigger in concrete_casts:
                try:
                    trigger_skill_id = int(trigger.skill_id)
                except (TypeError, ValueError):
                    continue
                combo_rules = self._derived_combo_action_rules.get(
                    trigger_skill_id, ()
                )
                if not combo_rules:
                    continue
                while (
                    history_cursor < len(concrete_casts)
                    and concrete_casts[history_cursor].timestamp_ms
                    <= trigger.timestamp_ms
                ):
                    row = concrete_casts[history_cursor]
                    try:
                        row_skill_id = int(row.skill_id)
                    except (TypeError, ValueError):
                        history_cursor += 1
                        continue
                    history.append((row_skill_id, row.timestamp_ms / 1000.0))
                    history_cursor += 1
                for combo_rule in combo_rules:
                    predecessor_match = combo_rule.match_predecessor(
                        history,
                        trigger.timestamp_ms / 1000.0,
                    )
                    if predecessor_match is None:
                        continue
                    predecessor, _skill_id, _cast_time = predecessor_match
                    if (
                        predecessor.damage_owner
                        != "intermediate_packet_basic_attack_key"
                    ):
                        continue
                    expected_intermediate_ms = trigger.timestamp_ms - round(
                        combo_rule.anchor_before_trigger_seconds * 1000.0
                    )
                    inferred = min(
                        (
                            row
                            for row in inferred_casts
                            if row.skill_id == str(combo_rule.intermediate_skill_id)
                            and abs(
                                row.timestamp_ms - expected_intermediate_ms
                            )
                            <= 80
                        ),
                        key=lambda row: abs(
                            row.timestamp_ms - expected_intermediate_ms
                        ),
                        default=None,
                    )
                    if inferred is None:
                        continue
                    candidates = [
                        index
                        for index, event in enumerate(hit_rows)
                        if index not in used_hit_indexes
                        and event.source_id == source_id
                        and inferred.timestamp_ms - 20
                        <= event.timestamp_ms
                        <= trigger.timestamp_ms + 20
                        and (
                            (
                                norm(event.attribution_method).casefold()
                                == "packet_basic_attack_key"
                                and norm(event.skill_id).casefold()
                                in {"4294967295", "0xffffffff"}
                            )
                            or (
                                norm(event.raw_skill_id).casefold()
                                in {"4294967295", "0xffffffff"}
                                and "native_attack_result_sentinel"
                                in norm(event.raw_attribution_method).casefold()
                            )
                        )
                        and event.has_real_hit_evidence is True
                    ]
                    if len(candidates) != 1:
                        continue
                    index = candidates[0]
                    event = hit_rows[index]
                    replacements[index] = replace(
                        event,
                        job=inferred.job or event.job,
                        skill_id=str(combo_rule.intermediate_skill_id),
                        skill_name=(
                            inferred.skill_name
                            or combo_rule.intermediate_skill_name
                            or event.skill_name
                        ),
                        attribution_method="derived_combo_basic_attack",
                        raw_skill_id=event.raw_skill_id or event.skill_id,
                        raw_skill_name=event.raw_skill_name or event.skill_name,
                        raw_attribution_method=(
                            event.raw_attribution_method
                            or event.attribution_method
                        ),
                    )
                    used_hit_indexes.add(index)
        for index, event in replacements.items():
            hit_rows[index] = event
        return hit_rows, len(replacements)

    def analyze_dpscap(
        self,
        path: Path,
        progress_callback: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[HitObservation]:
        source = V576DiagnosticReplaySource(
            Path(path),
            self.data_root,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        try:
            events = source.poll()
            raw_capture_end_ms = source.capture_end_timestamp_ms
        finally:
            source.close()
        casts = tuple(event for event in events if isinstance(event, SkillCastEvent))
        hits = tuple(
            event
            for event in events
            if isinstance(event, CombatEvent) and not event.healing and event.amount > 0
        )
        capture_end_ms = max(
            raw_capture_end_ms,
            max((event.timestamp_ms for event in (*hits, *casts)), default=0),
        )
        observations = self.analyze_events(
            casts,
            hits,
            Path(path).name,
            capture_end_ms=capture_end_ms,
        )
        analysis_diagnostics = dict(self.last_diagnostics)
        attribution_methods = Counter(
            norm(event.attribution_method) or "<empty>" for event in hits
        )
        self.last_diagnostics = {
            **analysis_diagnostics,
            "source_file": Path(path).name,
            "decoded_events": len(events),
            "damage_events": len(hits),
            "cast_events": len(casts),
            "capture_end_ms": capture_end_ms,
            "cast_instances": len(self._virtual_instances(casts)),
            "production_ledger_reassignment_disabled": True,
            "reassigned_damage_events": analysis_diagnostics.get(
                "derived_combo_reassigned_hits", 0
            ),
            "real_hit_events": sum(
                event.has_real_hit_evidence is True for event in hits
            ),
            "legacy_hit_events": sum(
                event.has_real_hit_evidence is None for event in hits
            ),
            "auxiliary_damage_events": sum(
                event.has_real_hit_evidence is False for event in hits
            ),
            "circular_attribution_events": sum(
                classify_hit_skill_evidence(event).circular for event in hits
            ),
            "attribution_methods": dict(sorted(attribution_methods.items())),
            "observations": len(observations),
            "eligible_observations": sum(row.training_eligible for row in observations),
            "unassigned_real_hits": analysis_diagnostics.get(
                "unassigned_real_hits", 0
            ),
            "reliable_actions_without_real_hits": analysis_diagnostics.get(
                "reliable_actions_without_real_hits", 0
            ),
        }
        return observations

    def analyze_events(
        self,
        casts: Sequence[SkillCastEvent],
        hits: Sequence[CombatEvent],
        source_file: str = "",
        *,
        instances: Sequence[CastInstance] | None = None,
        capture_end_ms: int | None = None,
    ) -> list[HitObservation]:
        capture_end_supplied = capture_end_ms is not None
        hits, derived_combo_reassignments = self._resolve_derived_combo_damage(
            casts, hits
        )
        cast_instances = sorted(
            list(instances) if instances is not None else self._virtual_instances(casts),
            key=lambda row: (row.start_ms, row.source_id, row.family),
        )
        if capture_end_ms is None:
            capture_end_ms = max(
                [row.timestamp_ms for row in hits]
                + [row.start_ms for row in cast_instances],
                default=0,
            )

        by_instance_id = {row.instance_id: row for row in cast_instances}
        by_family: dict[tuple[str, str], list[CastInstance]] = defaultdict(list)
        starts_by_family: dict[tuple[str, str], list[int]] = defaultdict(list)
        by_source: dict[str, list[CastInstance]] = defaultdict(list)
        for row in cast_instances:
            family = skill_family(row.skill_name, row.skill_id)
            key = (row.source_id, family)
            by_family[key].append(row)
            starts_by_family[key].append(row.start_ms)
            by_source[row.source_id].append(row)

        next_family_start: dict[str, int] = {}
        for rows in by_family.values():
            for current, following in zip(rows, rows[1:]):
                next_family_start[current.instance_id] = following.start_ms
        next_action_start: dict[str, int] = {}
        for rows in by_source.values():
            reliable_rows = [
                row for row in rows if _cast_evidence(row.packet_kind)[2]
            ]
            for current, following in zip(reliable_rows, reliable_rows[1:]):
                next_action_start[current.instance_id] = following.start_ms

        assigned_real: dict[str, list[CombatEvent]] = defaultdict(list)
        assigned_auxiliary: dict[str, list[CombatEvent]] = defaultdict(list)
        unassigned_real: list[CombatEvent] = []
        unassigned_reasons: Counter[str] = Counter()
        ambiguous_instance_ids: set[str] = set()
        external_instance_bindings: dict[str, str] = {}

        def event_window(candidate: CastInstance, event: CombatEvent) -> int:
            synthetic_cast = SkillCastEvent(
                timestamp_ms=candidate.start_ms,
                source_id=candidate.source_id,
                source_name=candidate.source_name,
                job=candidate.job,
                skill_id=candidate.skill_id,
                skill_name=candidate.skill_name,
                packet_kind=candidate.packet_kind,
            )
            base_window = (
                self._persistent_window_ms(synthetic_cast)
                if _is_persistent(event)
                else self._instance_link_window_ms(synthetic_cast, event)
            )
            return base_window

        def valid_for_instance(
            candidate: CastInstance,
            event: CombatEvent,
            event_family: str,
        ) -> bool:
            delta = event.timestamp_ms - candidate.start_ms
            return bool(
                candidate.source_id == event.source_id
                and skill_family(candidate.skill_name, candidate.skill_id)
                == event_family
                and -_HIT_BEFORE_CAST_TOLERANCE_MS
                <= delta
                <= event_window(candidate, event)
            )

        def bind_external_instance(
            event: CombatEvent,
            event_family: str,
        ) -> CastInstance | None:
            """Bind decoder field/projectile IDs to one observed action.

            Decoder instance IDs and analyzer action IDs intentionally use
            different namespaces.  Rejecting every unknown decoder ID threw
            away the strongest ownership evidence and split one field cast
            into several damage-only clusters.  Bind once, validate on every
            later hit, and remain conservative when two candidate casts truly
            overlap.
            """

            evidence = classify_hit_skill_evidence(event)
            external_id = norm(event.cast_instance_id).casefold()
            if (
                not external_id.startswith(("damage-object:", "field:"))
                or evidence.tier not in {"A", "B"}
                or not evidence.skill_link_supported
                or evidence.circular
            ):
                return None
            bound_id = external_instance_bindings.get(event.cast_instance_id)
            if bound_id:
                bound = by_instance_id.get(bound_id)
                # The first row of a decoder-owned object generation has
                # already passed the strict action/family/time-window join.
                # Later rows carrying the same object ID + generation are
                # continuation Hits of that object, not new casts.  Reusing
                # the short action window here used to cut long channels in
                # half (for example 激光破碎: Hit 1-7 on the real cast,
                # Hit 8-12 in a fake damage-only cast).  Object lifetime and
                # ID recycling are enforced by the stateful decoder, whose
                # cast_instance_id contains the generation number; retain
                # only the immutable source/family/start invariants here.
                return (
                    bound
                    if bound is not None
                    and bound.source_id == event.source_id
                    and skill_family(bound.skill_name, bound.skill_id)
                    == event_family
                    and event.timestamp_ms
                    >= bound.start_ms - _HIT_BEFORE_CAST_TOLERANCE_MS
                    else None
                )
            key = (event.source_id, event_family)
            candidates = [
                row
                for row in by_family.get(key, ())
                if valid_for_instance(row, event, event_family)
            ]
            if not candidates:
                return None
            event_skill_ids = {
                norm(value)
                for value in (event.raw_skill_id, event.skill_id)
                if norm(value) not in {"", "0", "-1"}
            }
            exact = [
                row for row in candidates if norm(row.skill_id) in event_skill_ids
            ]
            if exact:
                candidates = exact
            prior = [row for row in candidates if row.start_ms <= event.timestamp_ms]
            if prior:
                candidates = prior
            candidates.sort(key=lambda row: row.start_ms)
            selected = candidates[-1]
            if len(candidates) >= 2:
                previous = candidates[-2]
                # Two nearly simultaneous same-skill actions cannot be
                # separated by a new object ID alone.  Leave both untrained.
                if selected.start_ms - previous.start_ms < 500:
                    return None
            external_instance_bindings[event.cast_instance_id] = selected.instance_id
            return selected

        def matching_instance(
            event: CombatEvent,
        ) -> tuple[CastInstance | None, str]:
            event_family = self._event_family(event)
            if event.cast_instance_id:
                direct = by_instance_id.get(event.cast_instance_id)
                if direct is not None and valid_for_instance(
                    direct, event, event_family
                ):
                    return direct, "cast_instance_id"
                external = bind_external_instance(event, event_family)
                if external is not None:
                    return external, "decoder_instance_id"
                # A wire/decoder-provided instance link that contradicts the
                # source, family or time window is stronger evidence of a bad
                # assignment than the generic fallback is of a good one.
                return None, "invalid_cast_instance_id"
            key = (event.source_id, event_family)
            candidates = by_family.get(key, ())
            timestamps = starts_by_family.get(key, ())
            if not candidates:
                return None, "no_same_family_cast"
            right = bisect_right(
                timestamps,
                event.timestamp_ms + _HIT_BEFORE_CAST_TOLERANCE_MS,
            )
            active: list[CastInstance] = []
            for candidate in reversed(candidates[:right]):
                delta = event.timestamp_ms - candidate.start_ms
                if delta > 90_000:
                    break
                if (
                    -_HIT_BEFORE_CAST_TOLERANCE_MS
                    <= delta
                    <= event_window(candidate, event)
                ):
                    active.append(candidate)
            if not active:
                return None, "outside_cast_window"
            active.sort(key=lambda row: row.start_ms)
            if _is_persistent(event):
                # Recasts refresh a persistent field.  This split is useful
                # for cadence reporting but is never promoted to fixed-Hit.
                return active[-1], "latest_persistent_refresh"
            if len(active) == 1:
                return active[0], "unique_same_family_cast"

            event_skill_ids = {
                norm(value)
                for value in (event.raw_skill_id, event.skill_id)
                if norm(value) not in {"", "0", "-1"}
            }
            exact = [
                row for row in active if norm(row.skill_id) in event_skill_ids
            ]
            if len(exact) == 1:
                return exact[0], "unique_exact_variant"
            # A fresh action can own its immediate result even while the broad
            # fallback window of an older same-family action is still open.
            # Require a large temporal separation so rapid double-casts remain
            # deliberately ambiguous instead of being stolen by "latest wins".
            past = [row for row in active if row.start_ms <= event.timestamp_ms]
            if len(past) >= 2:
                latest = past[-1]
                previous = past[-2]
                latest_delta = event.timestamp_ms - latest.start_ms
                if (
                    latest_delta <= _CLEAR_LATEST_HIT_WINDOW_MS
                    and latest.start_ms - previous.start_ms
                    >= _CLEAR_LATEST_CAST_SEPARATION_MS
                    and _cast_evidence(latest.packet_kind)[2]
                ):
                    return latest, "clear_latest_action"
            ambiguous_instance_ids.update(row.instance_id for row in active)
            return None, "overlapping_same_family_casts"

        for event in sorted(hits, key=lambda row: (row.timestamp_ms, row.source_id)):
            if event.healing or event.amount <= 0:
                continue
            matched, match_reason = matching_instance(event)
            if event.has_real_hit_evidence is False:
                if matched is not None:
                    assigned_auxiliary[matched.instance_id].append(event)
                continue
            if matched is None:
                unassigned_real.append(event)
                unassigned_reasons[match_reason] += 1
                continue
            assigned_real[matched.instance_id].append(event)

        observations: list[HitObservation] = []
        for instance in cast_instances:
            real_rows = sorted(
                assigned_real.get(instance.instance_id, ()),
                key=lambda row: row.timestamp_ms,
            )
            auxiliary_rows = sorted(
                assigned_auxiliary.get(instance.instance_id, ()),
                key=lambda row: row.timestamp_ms,
            )
            persistent = any(_is_persistent(row) for row in real_rows) or (
                not real_rows
                and any(_is_persistent(row) for row in auxiliary_rows)
            )
            packet_kind = norm(instance.packet_kind)
            tier, label, reliable = _cast_evidence(packet_kind)
            action_derived = (
                packet_kind.casefold() in _ACTION_DERIVED_CAST_KINDS
            )
            if (
                not real_rows
                and not (auxiliary_rows and persistent)
                and (
                    (not reliable and not action_derived)
                    or not capture_end_supplied
                )
            ):
                continue
            synthetic_cast = SkillCastEvent(
                timestamp_ms=instance.start_ms,
                source_id=instance.source_id,
                source_name=instance.source_name,
                job=instance.job,
                skill_id=instance.skill_id,
                skill_name=instance.skill_name,
                packet_kind=instance.packet_kind,
            )
            required_tail_ms = (
                self._persistent_window_ms(synthetic_cast)
                if persistent
                else self._normal_window_ms(synthetic_cast)
            )
            following = next_family_start.get(instance.instance_id)
            if following is not None:
                required_tail_ms = min(
                    required_tail_ms,
                    max(0, following - instance.start_ms),
                )
            censor_reasons: list[str] = []
            if capture_end_ms - instance.start_ms < required_tail_ms:
                censor_reasons.append("记录在完整归因窗结束前停止")
            if not real_rows:
                if action_derived:
                    censor_reasons.append("派生动作伤害沿用前置技能归属")
                else:
                    censor_reasons.append("可靠动作未观察到真实Hit")
            if any(
                row.timestamp_ms < instance.start_ms
                for row in (*real_rows, *auxiliary_rows)
            ):
                censor_reasons.append("首Hit早于动作锚点")
            if instance.instance_id in ambiguous_instance_ids:
                censor_reasons.append("同族施放窗口重叠，存在未归属Hit")
            if instance.inferred or packet_kind.casefold() in _INFERRED_CAST_KINDS:
                censor_reasons.append(
                    "由前后动作链确认的派生施放"
                    if action_derived
                    else "施放仅由算法推断"
                )
            action_ms = next_action_start.get(instance.instance_id)
            action_lock = (
                max(0.0, (action_ms - instance.start_ms) / 1000.0)
                if action_ms is not None
                else None
            )
            observations.append(
                self._observation_from_hits(
                    real_rows,
                    instance.start_ms,
                    instance.job,
                    instance.skill_name,
                    instance.skill_id,
                    instance.source_id,
                    instance.source_name,
                    source_file,
                    action_lock,
                    True,
                    cast_packet_kind=packet_kind,
                    evidence_tier=tier,
                    evidence_label=label,
                    reliable_cast=reliable,
                    censor_reasons=tuple(censor_reasons),
                    auxiliary_event_count=len(
                        auxiliary_rows
                    ),
                    auxiliary_hits=auxiliary_rows,
                    persistent=persistent,
                )
            )

        # Real hits without an action packet are retained as discovery evidence
        # only.  They never become production-ready Hit templates on their own.
        clusters: dict[tuple[str, str], list[list[CombatEvent]]] = defaultdict(list)
        for event in sorted(
            unassigned_real, key=lambda row: (row.source_id, row.timestamp_ms)
        ):
            display_name = self._event_display_name(event)
            if not self._is_resolved_name(display_name):
                continue
            key = (event.source_id, skill_variant(display_name, event.skill_id))
            rows = clusters[key]
            persistent = _is_persistent(event)
            gap = 2_000 if persistent else _FALLBACK_CLUSTER_GAP_MS
            pseudo_cast = SkillCastEvent(
                timestamp_ms=event.timestamp_ms,
                source_id=event.source_id,
                source_name=event.source_name,
                job=event.job,
                skill_id=event.raw_skill_id or event.skill_id,
                skill_name=display_name,
            )
            max_span = (
                self._persistent_window_ms(pseudo_cast)
                if persistent
                else self._normal_window_ms(pseudo_cast)
            )
            if (
                not rows
                or event.timestamp_ms - rows[-1][-1].timestamp_ms > gap
                or event.timestamp_ms - rows[-1][0].timestamp_ms > max_span
            ):
                rows.append([event])
            else:
                rows[-1].append(event)
        for grouped in clusters.values():
            for cluster in grouped:
                first = cluster[0]
                display_name = self._event_display_name(first)
                observations.append(
                    self._observation_from_hits(
                        cluster,
                        first.timestamp_ms,
                        first.job,
                        display_name,
                        first.raw_skill_id or first.skill_id,
                        first.source_id,
                        first.source_name,
                        source_file,
                        None,
                        False,
                        cast_packet_kind="",
                        evidence_tier="D",
                        evidence_label="仅真实伤害聚类",
                        reliable_cast=False,
                        censor_reasons=("缺少动作包",),
                        auxiliary_event_count=0,
                        persistent=any(_is_persistent(row) for row in cluster),
                    )
                )
        self.last_diagnostics = {
            **self.last_diagnostics,
            "unassigned_real_hits": len(unassigned_real),
            "derived_combo_reassigned_hits": derived_combo_reassignments,
            "unassigned_resolved_real_hits": sum(
                self._is_resolved_name(self._event_display_name(row))
                for row in unassigned_real
            ),
            "unassigned_unresolved_real_hits": sum(
                not self._is_resolved_name(self._event_display_name(row))
                for row in unassigned_real
            ),
            "unassigned_reasons": dict(sorted(unassigned_reasons.items())),
            "ambiguous_cast_instances": len(ambiguous_instance_ids),
            "real_hit_event_conservation": {
                "input": sum(row.has_real_hit_evidence is True for row in hits),
                "assigned": sum(
                    row.has_real_hit_evidence is True
                    for rows in assigned_real.values()
                    for row in rows
                ),
                "unassigned": sum(
                    row.has_real_hit_evidence is True for row in unassigned_real
                ),
            },
            "reliable_actions_without_real_hits": sum(
                _cast_evidence(row.packet_kind)[2]
                and self._is_resolved_name(row.skill_name)
                and not assigned_real.get(row.instance_id)
                for row in cast_instances
            ),
        }
        observations.sort(key=lambda row: (row.cast_timestamp_ms, row.source_id))
        return observations

    def _observation_from_hits(
        self,
        hits: Sequence[CombatEvent],
        cast_ms: int,
        job: str,
        skill_name: str,
        skill_id: str,
        source_id: str,
        source_name: str,
        source_file: str,
        action_lock: float | None,
        direct_cast: bool,
        *,
        cast_packet_kind: str = "",
        evidence_tier: str = "D",
        evidence_label: str = "仅伤害聚类",
        reliable_cast: bool = False,
        censor_reasons: tuple[str, ...] = (),
        auxiliary_event_count: int = 0,
        auxiliary_hits: Sequence[CombatEvent] = (),
        persistent: bool | None = None,
    ) -> HitObservation:
        target_selection = select_target_track(hits)
        ordered = list(target_selection.hits)
        ordered_auxiliary = sorted(auxiliary_hits, key=lambda row: row.timestamp_ms)
        basis = ordered[0] if ordered else (ordered_auxiliary[0] if ordered_auxiliary else None)
        display_name = skill_name or (basis.skill_name if basis is not None else "")
        display_job = job or (basis.job if basis is not None else "")
        is_persistent = (
            any(_is_persistent(row) for row in ordered)
            or (
                not ordered
                and any(_is_persistent(row) for row in ordered_auxiliary)
            )
            if persistent is None
            else bool(persistent)
        )
        complete = not censor_reasons
        resolved = self._is_resolved_name(display_name)
        hit_evidence = [classify_hit_skill_evidence(row) for row in ordered]
        hit_tier = weakest_supported_tier(
            (item.tier for item in hit_evidence),
            default="U",
        )
        combined_tier = weakest_supported_tier(
            (evidence_tier, hit_tier),
            default="U",
        )
        circular_hit_count = sum(item.circular for item in hit_evidence)
        supported_skill_links = bool(hit_evidence) and all(
            item.skill_link_supported and item.tier in {"A", "B"}
            for item in hit_evidence
        )
        explicit_real_hits = bool(ordered) and all(
            row.has_real_hit_evidence is True for row in ordered
        )
        training_eligible = (
            reliable_cast
            and complete
            and not is_persistent
            and resolved
            and explicit_real_hits
            and supported_skill_links
            and not circular_hit_count
            and target_selection.target_consistency >= 0.50
        )
        evidence_labels = sorted({item.label for item in hit_evidence})
        return HitObservation(
            job=display_job or "未知职业",
            skill_name=display_name or "待识别伤害",
            skill_family=skill_family(
                display_name,
                skill_id or (basis.skill_id if basis is not None else ""),
            ),
            skill_id=skill_id or (basis.skill_id if basis is not None else ""),
            source_id=source_id,
            source_name=source_name,
            source_file=source_file,
            cast_timestamp_ms=cast_ms,
            hit_times=tuple(
                (row.timestamp_ms - cast_ms) / 1000.0 for row in ordered
            ),
            hit_amounts=tuple(row.amount for row in ordered),
            critical_flags=tuple(row.critical for row in ordered),
            auxiliary_times=tuple(
                (row.timestamp_ms - cast_ms) / 1000.0 for row in ordered_auxiliary
            ),
            auxiliary_amounts=tuple(row.amount for row in ordered_auxiliary),
            action_lock_seconds=action_lock,
            reference_cast_seconds=self._reference_cast_time(display_job, display_name),
            direct_cast=direct_cast,
            persistent=is_persistent,
            damage_record_count=len(hits) + len(auxiliary_hits),
            target_result_count=len(hits),
            hit_target_ids=tuple(row.target_id for row in ordered),
            hit_attribution_methods=tuple(row.attribution_method for row in ordered),
            hit_raw_attribution_methods=tuple(
                row.raw_attribution_method for row in ordered
            ),
            hit_evidence_labels=tuple(item.label for item in hit_evidence),
            hit_confidences=tuple(row.attribution_confidence for row in ordered),
            hit_cast_instance_ids=tuple(row.cast_instance_id for row in ordered),
            hit_damage_object_ids=tuple(row.wire_damage_object_id for row in ordered),
            attribution_methods=tuple(sorted({row.attribution_method for row in ordered})),
            cast_packet_kind=cast_packet_kind,
            evidence_tier=combined_tier,
            evidence_label=(
                f"{evidence_label}; {' / '.join(evidence_labels)}"
                if evidence_labels
                else f"{evidence_label}; 无真实Hit"
            ),
            cast_evidence_tier=evidence_tier,
            hit_evidence_tiers=evidence_counts(ordered),
            circular_hit_count=circular_hit_count,
            reliable_cast=reliable_cast,
            complete=complete,
            censor_reasons=tuple(censor_reasons),
            target_count=target_selection.target_count,
            target_hit_counts=target_selection.target_hit_counts,
            target_consistency=target_selection.target_consistency,
            target_selection=target_selection.selection_reason,
            auxiliary_event_count=max(0, auxiliary_event_count),
            legacy_hit_evidence_count=sum(
                row.has_real_hit_evidence is None for row in ordered
            ),
            mean_attribution_confidence=_median(
                row.attribution_confidence for row in ordered
            ),
            template_eligible=training_eligible,
            training_eligible=training_eligible,
        )

    @staticmethod
    def _single_target_hits(hits: Sequence[CombatEvent]) -> list[CombatEvent]:
        """Avoid multiplying a skill's Hit count by the number of AoE targets."""
        return list(select_target_track(hits).hits)


class SkillHitTemplateLibrary:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.payload: dict[str, object] = {
            "version": ANALYZER_VERSION,
            "updated_at": "",
            "processed_files": {},
            "skills": {},
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return
        if isinstance(value, dict):
            loaded_version = int(value.get("version", 0) or 0)
            if loaded_version == ANALYZER_VERSION:
                self.payload.update(value)
            else:
                # Hit-evidence semantics changed in V2/V3.  Reusing legacy samples
                # would silently mix proc/DoT events and merged normal/EX
                # variants into the new model.  Preserve human confirmations,
                # but require captures to be reprocessed.
                migrated_skills: dict[str, dict[str, object]] = {}
                legacy_skills = value.get("skills", {})
                if isinstance(legacy_skills, dict):
                    for entry in legacy_skills.values():
                        if not isinstance(entry, dict):
                            continue
                        manual = entry.get("manual", {})
                        rows = entry.get("observations", [])
                        if not isinstance(manual, dict) or not manual:
                            continue
                        if not isinstance(rows, list) or not rows:
                            continue
                        first = next((row for row in rows if isinstance(row, dict)), None)
                        if first is None:
                            continue
                        key = (
                            _skill_key(
                                first.get("job"),
                                skill_variant(
                                    first.get("skill_name"), first.get("skill_id")
                                ),
                                first.get("skill_id"),
                            )
                            + "|fixed_hit"
                        )
                        migrated_skills[key] = {
                            "manual": dict(manual),
                            "observations": [],
                        }
                self.payload.update(
                    {
                        "version": ANALYZER_VERSION,
                        "updated_at": "",
                        "processed_files": {},
                        "skills": migrated_skills,
                        "migrated_from_version": loaded_version,
                        "legacy_observations_ignored": sum(
                            len(entry.get("observations", []))
                            for entry in (
                                value.get("skills", {}).values()
                                if isinstance(value.get("skills"), dict)
                                else ()
                            )
                            if isinstance(entry, dict)
                            and isinstance(entry.get("observations"), list)
                        ),
                    }
                )
        self.payload.setdefault("processed_files", {})
        self.payload.setdefault("skills", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.payload["version"] = ANALYZER_VERSION
        self.payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    @staticmethod
    def file_fingerprints(path: Path) -> tuple[str, str]:
        """Return stable combat-content and full-evidence identifiers.

        Saving reliable identity/session evidence can append metadata to an
        existing capture without changing its combat.  The combat identifier
        deliberately skips those appendix adapters, while the evidence
        revision includes them so a shard is invalidated when richer identity
        evidence is appended.  Neither identifier depends on analyzer code.
        """

        path = Path(path)
        combat_digest = hashlib.sha256(b"dpscap-combat-content-v1\0")
        evidence_digest = hashlib.sha256(b"dpscap-evidence-revision-v1\0")
        with path.open("rb") as handle:
            magic = handle.read(len(_CAPTURE_MAGIC))
            if magic != _CAPTURE_MAGIC:
                combat_digest.update(magic)
                evidence_digest.update(magic)
                while chunk := handle.read(1024 * 1024):
                    combat_digest.update(chunk)
                    evidence_digest.update(chunk)
                return combat_digest.hexdigest(), evidence_digest.hexdigest()
            combat_frames = 0
            evidence_frames = 0
            while True:
                header = handle.read(_CAPTURE_FRAME_HEADER.size)
                if not header:
                    break
                if len(header) != _CAPTURE_FRAME_HEADER.size:
                    for digest in (combat_digest, evidence_digest):
                        digest.update(b"<truncated-header>")
                        digest.update(header)
                    break
                _timestamp, adapter, _original_length, captured_length = (
                    _CAPTURE_FRAME_HEADER.unpack(header)
                )
                if captured_length > 16 * 1024 * 1024:
                    for digest in (combat_digest, evidence_digest):
                        digest.update(b"<invalid-frame>")
                        digest.update(header)
                    break
                frame = handle.read(captured_length)
                if len(frame) != captured_length:
                    for digest in (combat_digest, evidence_digest):
                        digest.update(b"<truncated-frame>")
                        digest.update(header)
                        digest.update(frame)
                    break
                evidence_digest.update(header)
                evidence_digest.update(frame)
                evidence_frames += 1
                if adapter in _APPENDIX_ADAPTERS:
                    continue
                combat_digest.update(header)
                combat_digest.update(frame)
                combat_frames += 1
            combat_digest.update(f"\0frames={combat_frames}".encode("ascii"))
            evidence_digest.update(f"\0frames={evidence_frames}".encode("ascii"))
        return combat_digest.hexdigest(), evidence_digest.hexdigest()

    @staticmethod
    def file_fingerprint(path: Path) -> str:
        return SkillHitTemplateLibrary.file_fingerprints(path)[0]

    @staticmethod
    def file_evidence_fingerprint(path: Path) -> str:
        return SkillHitTemplateLibrary.file_fingerprints(path)[1]

    def has_file(self, fingerprint: str) -> bool:
        processed = self.payload.get("processed_files", {})
        return isinstance(processed, dict) and fingerprint in processed

    def add_file(
        self,
        path: Path,
        fingerprint: str,
        observations: Sequence[HitObservation],
    ) -> None:
        # Retrying the same import must be a no-op.  Previously observations
        # were duplicated even though processed_files already contained it.
        if self.has_file(fingerprint):
            return
        skills = self.payload.setdefault("skills", {})
        if not isinstance(skills, dict):
            skills = {}
            self.payload["skills"] = skills
        grouped: dict[str, list[HitObservation]] = defaultdict(list)
        for row in observations:
            grouped[row.profile_key].append(row)
        for key, rows in grouped.items():
            entry = skills.setdefault(key, {"manual": {}, "observations": []})
            if not isinstance(entry, dict):
                entry = {"manual": {}, "observations": []}
                skills[key] = entry
            stored = entry.setdefault("observations", [])
            if not isinstance(stored, list):
                stored = []
            stored.extend(row.to_mapping() for row in rows)
            entry["observations"] = stored[-MAX_OBSERVATIONS_PER_SKILL:]
        processed = self.payload.setdefault("processed_files", {})
        if isinstance(processed, dict):
            processed[fingerprint] = {
                "path": str(Path(path)),
                "name": Path(path).name,
                "observations": len(observations),
                "analyzed_at": datetime.now().isoformat(timespec="seconds"),
            }
        self.save()

    def observations(self, key: str | None = None) -> list[HitObservation]:
        skills = self.payload.get("skills", {})
        if not isinstance(skills, dict):
            return []
        entries = [skills.get(key)] if key is not None else skills.values()
        result: list[HitObservation] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            stored = entry.get("observations", [])
            if not isinstance(stored, list):
                continue
            for row in stored:
                if isinstance(row, dict):
                    try:
                        result.append(HitObservation.from_mapping(row))
                    except (TypeError, ValueError):
                        continue
        return result

    def summaries(self) -> list[SkillHitSummary]:
        skills = self.payload.get("skills", {})
        if not isinstance(skills, dict):
            return []
        result: list[SkillHitSummary] = []
        for key, entry in skills.items():
            if not isinstance(entry, dict):
                continue
            rows = self.observations(str(key))
            if rows:
                result.append(summarize_observations(rows, entry.get("manual", {})))
        return sorted(result, key=lambda row: (row.job, row.skill_name))

    def set_manual(
        self,
        key: str,
        hit_count: int | None,
        cast_seconds: float | None,
        confirmed: bool = True,
    ) -> None:
        skills = self.payload.setdefault("skills", {})
        if not isinstance(skills, dict):
            return
        entry = skills.setdefault(key, {"manual": {}, "observations": []})
        if not isinstance(entry, dict):
            return
        entry["manual"] = {
            "hit_count": int(hit_count or 0),
            "cast_seconds": cast_seconds,
            "confirmed": bool(confirmed),
            "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.save()


def export_hit_workbook(
    path: Path,
    summaries: Sequence[SkillHitSummary],
    observations_by_key: dict[str, Sequence[HitObservation]],
) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "技能Hit模板"
    summary_headers = [
        "职业",
        "技能",
        "样本数",
        "推荐Hit数",
        "候选Hit分布",
        "Hit一致率",
        "首Hit(s)",
        "末Hit(s)",
        "出伤跨度(s)",
        "实测动作锁(s)",
        "资料施放时间(s)",
        "持续/召唤",
        "直接施法匹配率",
        "置信度",
        "状态",
        "Hit时间轴(s)",
        "每Hit占比",
    ]
    summary_sheet.append(summary_headers)
    for item in summaries:
        summary_sheet.append(
            [
                item.job,
                item.skill_name,
                item.sample_count,
                item.display_hit_count,
                ", ".join(f"{count}Hit×{samples}" for count, samples in item.alternative_hit_counts),
                item.hit_consistency,
                item.first_hit_seconds,
                item.last_hit_seconds,
                item.hit_span_seconds,
                item.display_cast_seconds,
                item.reference_cast_seconds,
                "是" if item.persistent else "否",
                item.direct_cast_ratio,
                item.confidence / 100.0,
                item.status,
                ", ".join(f"{row.time_seconds:.3f}" for row in item.positions),
                ", ".join(f"{row.share:.2%}" for row in item.positions),
            ]
        )

    hit_sheet = workbook.create_sheet("每Hit明细")
    hit_sheet.append(
        ["职业", "技能", "Hit序号", "中位时间(s)", "P10(s)", "P90(s)", "中位占比", "占比P10", "占比P90", "样本数"]
    )
    for item in summaries:
        for position in item.positions:
            hit_sheet.append(
                [
                    item.job,
                    item.skill_name,
                    position.index,
                    position.time_seconds,
                    position.time_p10,
                    position.time_p90,
                    position.share,
                    position.share_p10,
                    position.share_p90,
                    position.samples,
                ]
            )

    sample_sheet = workbook.create_sheet("施放样本")
    sample_sheet.append(
        ["职业", "技能", "记录", "角色", "施放时刻(s)", "Hit数", "首Hit(s)", "末Hit(s)", "动作锁(s)", "直接匹配", "持续/召唤", "归因方法"]
    )
    for item in summaries:
        for row in observations_by_key.get(item.key, ()):
            sample_sheet.append(
                [
                    row.job,
                    row.skill_name,
                    row.source_file,
                    row.source_name or row.source_id,
                    row.cast_timestamp_ms / 1000.0,
                    row.hit_count,
                    row.first_hit_seconds,
                    row.last_hit_seconds,
                    row.action_lock_seconds,
                    "是" if row.direct_cast else "否",
                    "是" if row.persistent else "否",
                    ", ".join(row.attribution_methods),
                ]
            )

    header_fill = PatternFill("solid", fgColor="D8EEF3")
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in range(1, sheet.max_column + 1):
            values = [norm(sheet.cell(row=row, column=column).value) for row in range(1, min(sheet.max_row, 120) + 1)]
            width = min(44, max(10, max((len(value) for value in values), default=8) + 2))
            sheet.column_dimensions[get_column_letter(column)].width = width
    for row in summary_sheet.iter_rows(min_row=2):
        for column in (6, 13, 14):
            row[column - 1].number_format = "0.0%"
    for row in hit_sheet.iter_rows(min_row=2):
        for column in (7, 8, 9):
            row[column - 1].number_format = "0.00%"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
