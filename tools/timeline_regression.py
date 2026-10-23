from __future__ import annotations

"""Read-only legacy/evidence timeline regression runner for ``.dpscap`` files.

The runner deliberately does not import the application runtime until network
and upload guards are active.  Each capture is decoded exactly once, ingested
into one ``CombatAggregator``, and the resulting immutable snapshot is passed
to both timeline modes.

This tool expects the evidence-aware API below to exist in ``cast_timeline``::

    _timeline_events_with_damage_fallback(
        cast_events,
        combat_events,
        store,
        *,
        mode="legacy" | "evidence",
        diagnostics=mutable_mapping,
    )

Older builds are detected before any capture is decoded and fail with an
actionable interface error instead of silently comparing the same algorithm.
"""

import argparse
import errno
import http.client
import importlib
import inspect
import json
import math
import os
import socket
import sys
import time
import traceback
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


REPORT_SCHEMA = "dragon-nest-timeline-regression-v1"
RECOVERY_MATCH_TOLERANCE_MS = 750
DEFAULT_PATTERN = "*.dpscap"
_RECOVERY_KIND_TOKENS = (
    "inferred",
    "recovered",
    "recovery",
    "fallback",
)
_RECOVERY_KIND_EXACT = frozenset(
    {
        "damage_inferred",
        "shared_timeline_damage",
        "timeline_evidence",
        "evidence",
    }
)


class TimelineInterfaceError(RuntimeError):
    """Raised when the runtime cannot perform an honest mode comparison."""


class NetworkDisabledError(OSError):
    """Raised if regression code unexpectedly attempts network access."""


@dataclass(frozen=True, slots=True)
class TimelinePoint:
    timestamp_ms: int
    source_id: str
    source_name: str
    job: str
    skill_id: str
    skill_name: str
    skill_key: str
    packet_kind: str
    origin_type: str
    reliable_action: bool
    cluster_hits: int = 0
    cluster_damage: int = 0
    cluster_healing: int = 0

    @property
    def actor_skill_key(self) -> tuple[str, str, str]:
        return (self.source_id, self.job, self.skill_key)

    @property
    def skill_group_key(self) -> tuple[str, str]:
        return (self.job, self.skill_key)


@dataclass(frozen=True, slots=True)
class RuntimeBindings:
    data_dir: Path
    data_store_type: type[Any]
    aggregator_type: type[Any]
    replay_source_type: type[Any]
    client_profile_type: type[Any]
    combat_event_type: type[Any]
    cast_event_type: type[Any]
    timeline_function: Callable[..., Any]
    timeline_signature: str
    skill_family: Callable[[Any], str]
    authoritative_cast: Callable[[Any], bool]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _configure_offline_environment() -> None:
    """Override known side-effect toggles before importing project modules."""

    forced_values = {
        "PYTHONIOENCODING": "utf-8",
        "DRAGON_NEST_NATIVE_NO_QT": "1",
        "DRAGON_NEST_OFFLINE": "1",
        "DRAGON_NEST_DISABLE_NETWORK": "1",
        "DRAGON_NEST_DISABLE_UPLOAD": "1",
        "DRAGON_NEST_BATCH_UPLOAD_EACH": "0",
        "DRAGON_NEST_BATCH_USE_REPLAY_CACHE": "0",
        "DRAGON_NEST_DISABLE_REPLAY_CACHE": "1",
        "DRAGON_NEST_DISABLE_SCORE_CACHE": "1",
        "DRAGON_NEST_SCORE_CACHE_DISABLED": "1",
        "DRAGON_NEST_ANALYTICS_DISABLED": "1",
        "DRAGON_NEST_USAGE_UPLOAD_DISABLED": "1",
        "DRAGON_NEST_CRASH_UPLOAD_DISABLED": "1",
        "DRAGON_NEST_DPSCAP_UPLOAD": "0",
        "TEAM_SCORE_UPLOAD_TOKEN": "",
        "CLOUDBASE_ENV_ID": "",
        "CLOUDBASE_APIKEY": "",
        "CLOUDBASE_API_KEY": "",
        "SUPABASE_URL": "",
        "SUPABASE_ANON_KEY": "",
        "SUPABASE_SERVICE_ROLE_KEY": "",
    }
    for key, value in forced_values.items():
        os.environ[key] = value
    # ``PYTHONDONTWRITEBYTECODE`` is normally consumed at interpreter start;
    # set the runtime flag as well because project imports happen below.
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _install_network_guard() -> None:
    """Deny socket and standard-library HTTP access for the whole process."""

    original_socket = socket.socket

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise NetworkDisabledError(
            errno.ENETUNREACH,
            "timeline regression is offline; network access is forbidden",
        )

    class OfflineSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, *_args: Any, **_kwargs: Any) -> Any:
            return blocked()

        def connect_ex(self, *_args: Any, **_kwargs: Any) -> int:
            blocked()
            return errno.ENETUNREACH

        def sendto(self, *_args: Any, **_kwargs: Any) -> int:
            blocked()
            return 0

    socket.socket = OfflineSocket  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    urllib.request.urlopen = blocked  # type: ignore[assignment]
    http.client.HTTPConnection.connect = blocked  # type: ignore[assignment]
    http.client.HTTPSConnection.connect = blocked  # type: ignore[assignment]


def _install_runtime_write_guards() -> None:
    """Prevent decoder key discovery from updating the user's key cache."""

    try:
        codec = importlib.import_module(
            "dragon_nest_builder.live_dps.tcp_profile_codec"
        )
    except ModuleNotFoundError:
        return

    def no_write(*_args: Any, **_kwargs: Any) -> None:
        return None

    # Both names are private by design and are the only persistence points in
    # this decoder module. In-memory key learning is retained for the current
    # replay, while the user's registry remains untouched.
    if hasattr(codec, "_write_user_registry"):
        codec._write_user_registry = no_write
    if hasattr(codec, "_remember_unknown_shape"):
        codec._remember_unknown_shape = no_write


def _load_runtime(project_root: Path) -> RuntimeBindings:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    timeline_module = importlib.import_module(
        "dragon_nest_builder.live_dps.cast_timeline"
    )
    timeline_function = getattr(
        timeline_module, "_timeline_events_with_damage_fallback", None
    )
    if not callable(timeline_function):
        raise TimelineInterfaceError(
            "未找到 cast_timeline._timeline_events_with_damage_fallback；"
            "没有解码任何抓包。"
        )

    signature = inspect.signature(timeline_function)
    missing = [
        name for name in ("mode", "diagnostics") if name not in signature.parameters
    ]
    if missing:
        required = (
            "_timeline_events_with_damage_fallback(cast_events, combat_events, "
            "store, *, mode='legacy'|'evidence', diagnostics=...)"
        )
        raise TimelineInterfaceError(
            "当前 cast_timeline 尚未提供可比较的 legacy/evidence 接口。"
            f"实际签名：{signature}；缺少参数：{', '.join(missing)}；"
            f"需要签名：{required}。为避免假回归，工具已在解码前停止。"
        )
    try:
        signature.bind((), (), None, mode="legacy", diagnostics={})
        signature.bind((), (), None, mode="evidence", diagnostics={})
    except TypeError as exc:
        raise TimelineInterfaceError(
            "时间轴接口虽然声明了 mode/diagnostics，但不能按预期调用。"
            f"实际签名：{signature}；绑定错误：{exc}。没有解码任何抓包。"
        ) from exc

    data_loader = importlib.import_module("dragon_nest_builder.data_loader")
    aggregator_module = importlib.import_module(
        "dragon_nest_builder.live_dps.aggregator"
    )
    source_module = importlib.import_module(
        "dragon_nest_builder.live_dps.capture_sources"
    )
    profile_module = importlib.import_module(
        "dragon_nest_builder.live_dps.profile"
    )
    models_module = importlib.import_module("dragon_nest_builder.live_dps.models")
    _install_runtime_write_guards()

    family_function = getattr(timeline_module, "_timeline_skill_family", None)
    if not callable(family_function):
        family_function = lambda event: (
            str(getattr(event, "skill_name", "") or "").strip()
            or str(getattr(event, "skill_id", "") or "").strip()
            or "unknown"
        )
    authoritative_cast = getattr(
        importlib.import_module("dragon_nest_builder.live_dps.timeline_evidence"),
        "is_authoritative_timeline_cast",
        None,
    )
    if not callable(authoritative_cast):
        authoritative_cast = lambda event: not _is_recovery_kind(
            str(getattr(event, "packet_kind", "") or "")
        )

    return RuntimeBindings(
        data_dir=Path(data_loader.DATA_DIR),
        data_store_type=data_loader.DataStore,
        aggregator_type=aggregator_module.CombatAggregator,
        replay_source_type=source_module.V576DiagnosticReplaySource,
        client_profile_type=profile_module.ClientProfile,
        combat_event_type=models_module.CombatEvent,
        cast_event_type=models_module.SkillCastEvent,
        timeline_function=timeline_function,
        timeline_signature=str(signature),
        skill_family=family_function,
        authoritative_cast=authoritative_cast,
    )


def _discover_captures(
    inputs: Sequence[Path], pattern: str, limit: int | None
) -> list[Path]:
    found: dict[str, Path] = {}
    for raw_path in inputs:
        path = raw_path.expanduser().resolve()
        if path.is_file():
            if path.suffix.casefold() != ".dpscap":
                raise ValueError(f"不是 .dpscap 文件：{path}")
            found[str(path).casefold()] = path
            continue
        if not path.exists():
            raise FileNotFoundError(f"输入不存在：{path}")
        if not path.is_dir():
            raise ValueError(f"输入既不是文件也不是目录：{path}")
        for candidate in path.rglob(pattern):
            if candidate.is_file() and candidate.suffix.casefold() == ".dpscap":
                resolved = candidate.resolve()
                found[str(resolved).casefold()] = resolved

    captures = sorted(found.values(), key=lambda item: str(item).casefold())
    if limit is not None:
        captures = captures[:limit]
    return captures


def _is_recovery_kind(packet_kind: str) -> bool:
    kind = str(packet_kind or "").strip().casefold().replace("-", "_")
    if kind in _RECOVERY_KIND_EXACT or kind.startswith("evidence_"):
        return True
    return any(token in kind for token in _RECOVERY_KIND_TOKENS)


def _safe_skill_family(runtime: RuntimeBindings, event: Any) -> str:
    try:
        value = str(runtime.skill_family(event) or "").strip()
    except Exception:
        value = ""
    return (
        value
        or str(getattr(event, "skill_name", "") or "").strip()
        or str(getattr(event, "skill_id", "") or "").strip()
        or "unknown"
    )


def _point_from_cast(runtime: RuntimeBindings, event: Any) -> TimelinePoint:
    packet_kind = str(getattr(event, "packet_kind", "skill_cast") or "skill_cast")
    return TimelinePoint(
        timestamp_ms=max(0, int(getattr(event, "timestamp_ms", 0) or 0)),
        source_id=str(getattr(event, "source_id", "") or "").strip(),
        source_name=str(getattr(event, "source_name", "") or "").strip(),
        job=str(getattr(event, "job", "") or "").strip(),
        skill_id=str(getattr(event, "skill_id", "") or "").strip(),
        skill_name=str(getattr(event, "skill_name", "") or "").strip(),
        skill_key=_safe_skill_family(runtime, event),
        packet_kind=packet_kind,
        origin_type="cast",
        reliable_action=not _is_recovery_kind(packet_kind),
    )


def _point_from_combat_cluster(
    runtime: RuntimeBindings, cluster: Sequence[Any]
) -> TimelinePoint:
    first = cluster[0]
    methods = sorted(
        {
            str(getattr(event, "attribution_method", "recorded") or "recorded")
            for event in cluster
        }
    )
    return TimelinePoint(
        timestamp_ms=max(0, int(getattr(first, "timestamp_ms", 0) or 0)),
        source_id=str(getattr(first, "source_id", "") or "").strip(),
        source_name=str(getattr(first, "source_name", "") or "").strip(),
        job=str(getattr(first, "job", "") or "").strip(),
        skill_id=str(getattr(first, "skill_id", "") or "").strip(),
        skill_name=str(getattr(first, "skill_name", "") or "").strip(),
        skill_key=_safe_skill_family(runtime, first),
        packet_kind="combat:" + "+".join(methods),
        origin_type="damage_cluster",
        reliable_action=False,
        cluster_hits=len(cluster),
        cluster_damage=sum(
            int(getattr(event, "amount", 0) or 0)
            for event in cluster
            if not bool(getattr(event, "healing", False))
        ),
        cluster_healing=sum(
            int(getattr(event, "amount", 0) or 0)
            for event in cluster
            if bool(getattr(event, "healing", False))
        ),
    )


def _project_timeline(
    runtime: RuntimeBindings, events: Iterable[Any]
) -> tuple[TimelinePoint, ...]:
    casts: list[TimelinePoint] = []
    combat_by_key: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for event in events:
        if isinstance(event, runtime.cast_event_type):
            casts.append(_point_from_cast(runtime, event))
            continue
        if isinstance(event, runtime.combat_event_type):
            key = (
                str(getattr(event, "source_id", "") or "").strip(),
                str(getattr(event, "job", "") or "").strip(),
                _safe_skill_family(runtime, event),
            )
            combat_by_key[key].append(event)
            continue
        raise TimelineInterfaceError(
            "时间轴模式返回了不支持的事件类型："
            f"{type(event).__module__}.{type(event).__qualname__}"
        )

    clusters: list[TimelinePoint] = []
    for rows in combat_by_key.values():
        current: list[Any] = []
        for event in sorted(rows, key=lambda row: int(row.timestamp_ms)):
            timestamp_ms = int(event.timestamp_ms)
            if current and timestamp_ms - int(current[-1].timestamp_ms) > 750:
                clusters.append(_point_from_combat_cluster(runtime, current))
                current = []
            current.append(event)
        if current:
            clusters.append(_point_from_combat_cluster(runtime, current))

    return tuple(
        sorted(
            (*casts, *clusters),
            key=lambda row: (
                row.timestamp_ms,
                row.source_id,
                row.job,
                row.skill_key,
                row.packet_kind,
            ),
        )
    )


def _normalise_mode_result(result: Any, mode: str) -> tuple[tuple[Any, ...], int]:
    events: Any
    fallback_count: Any
    if isinstance(result, tuple) and len(result) == 2:
        events, fallback_count = result
    elif isinstance(result, Mapping):
        events = result.get("events")
        fallback_count = result.get(
            "fallback_count", result.get("fallbackCount", result.get("recovered_count", 0))
        )
    elif hasattr(result, "events"):
        events = result.events
        fallback_count = getattr(
            result,
            "fallback_count",
            getattr(result, "recovered_count", 0),
        )
    else:
        raise TimelineInterfaceError(
            f"{mode} 模式返回值不兼容：预期 (events, fallback_count)、"
            "含 events 的映射或含 events 属性的结果对象，"
            f"实际为 {type(result).__module__}.{type(result).__qualname__}。"
        )
    if events is None:
        raise TimelineInterfaceError(f"{mode} 模式返回值缺少 events。")
    try:
        event_tuple = tuple(events)
    except TypeError as exc:
        raise TimelineInterfaceError(f"{mode} 模式的 events 不可迭代：{exc}") from exc
    try:
        count = max(0, int(fallback_count or 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise TimelineInterfaceError(
            f"{mode} 模式的 fallback_count 不是整数：{fallback_count!r}"
        ) from exc
    return event_tuple, count


def _run_mode(
    runtime: RuntimeBindings,
    *,
    mode: str,
    cast_events: tuple[Any, ...],
    combat_events: tuple[Any, ...],
    store: Any,
) -> tuple[tuple[Any, ...], int, dict[str, Any], float]:
    diagnostics: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        result = runtime.timeline_function(
            cast_events,
            combat_events,
            store,
            mode=mode,
            diagnostics=diagnostics,
        )
    except TypeError as exc:
        raise TimelineInterfaceError(
            f"调用 {mode} 模式失败；实际签名为 {runtime.timeline_signature}。"
            "请确认 mode 与 diagnostics 是受支持的关键字参数，"
            f"且 diagnostics 接受可变字典。原始错误：{exc}"
        ) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    events, fallback_count = _normalise_mode_result(result, mode)
    return events, fallback_count, diagnostics, elapsed_ms


def _event_detail(point: TimelinePoint) -> dict[str, Any]:
    return {
        "timestampMs": point.timestamp_ms,
        "timestampSeconds": round(point.timestamp_ms / 1000.0, 3),
        "sourceId": point.source_id,
        "sourceName": point.source_name,
        "job": point.job,
        "skillId": point.skill_id,
        "skillName": point.skill_name,
        "skillKey": point.skill_key,
        "packetKind": point.packet_kind,
        "evidenceClass": "action" if point.reliable_action else "recovery",
        "originType": point.origin_type,
        "clusterHits": point.cluster_hits,
        "clusterDamage": point.cluster_damage,
        "clusterHealing": point.cluster_healing,
    }


def _metrics(matched: int, legacy_count: int, evidence_count: int) -> dict[str, Any]:
    precision = (
        matched / evidence_count
        if evidence_count
        else (1.0 if legacy_count == 0 else 0.0)
    )
    recall = (
        matched / legacy_count
        if legacy_count
        else (1.0 if evidence_count == 0 else 0.0)
    )
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "legacyCount": legacy_count,
        "evidenceCount": evidence_count,
        "matchedCount": matched,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _recovery_pairs_greedy(
    legacy: tuple[TimelinePoint, ...],
    evidence: tuple[TimelinePoint, ...],
    left_indices: Sequence[int],
    right_indices: Sequence[int],
) -> list[tuple[int, int]]:
    """Maximum-cardinality fallback for exceptionally large event groups.

    For equal-width timestamp intervals, pairing the earliest mutually
    feasible points maximises cardinality. The dynamic-programming path below
    is normally used because it additionally minimises total displacement.
    """

    ordered_left = sorted(left_indices, key=lambda idx: legacy[idx].timestamp_ms)
    ordered_right = sorted(right_indices, key=lambda idx: evidence[idx].timestamp_ms)
    pairs: list[tuple[int, int]] = []
    left_position = 0
    right_position = 0
    while left_position < len(ordered_left) and right_position < len(ordered_right):
        left_index = ordered_left[left_position]
        right_index = ordered_right[right_position]
        left_time = legacy[left_index].timestamp_ms
        right_time = evidence[right_index].timestamp_ms
        if right_time < left_time - RECOVERY_MATCH_TOLERANCE_MS:
            right_position += 1
            continue
        if left_time < right_time - RECOVERY_MATCH_TOLERANCE_MS:
            left_position += 1
            continue
        pairs.append((left_index, right_index))
        left_position += 1
        right_position += 1
    return pairs


def _recovery_pairs(
    legacy: tuple[TimelinePoint, ...],
    evidence: tuple[TimelinePoint, ...],
    left_indices: Sequence[int],
    right_indices: Sequence[int],
) -> list[tuple[int, int]]:
    """Match recoveries monotonically, maximising F1 then minimising movement."""

    ordered_left = sorted(left_indices, key=lambda idx: legacy[idx].timestamp_ms)
    ordered_right = sorted(right_indices, key=lambda idx: evidence[idx].timestamp_ms)
    left_count = len(ordered_left)
    right_count = len(ordered_right)
    if not left_count or not right_count:
        return []

    # One byte per DP cell plus two score rows keeps normal actor/skill groups
    # inexpensive. Very large pathological groups use the proven
    # maximum-cardinality linear fallback to avoid excessive memory.
    cell_count = (left_count + 1) * (right_count + 1)
    if cell_count > 4_000_000:
        return _recovery_pairs_greedy(
            legacy, evidence, ordered_left, ordered_right
        )

    width = right_count + 1
    # 1 = skip legacy point, 2 = skip evidence point, 3 = match.
    choices = bytearray(cell_count)
    previous_matches = [0] * width
    previous_costs = [0] * width

    def better(
        candidate_matches: int,
        candidate_cost: int,
        best_matches: int,
        best_cost: int,
    ) -> bool:
        return candidate_matches > best_matches or (
            candidate_matches == best_matches and candidate_cost < best_cost
        )

    for left_position, left_index in enumerate(ordered_left, start=1):
        current_matches = [0] * width
        current_costs = [0] * width
        left_time = legacy[left_index].timestamp_ms
        row_offset = left_position * width
        for right_position, right_index in enumerate(ordered_right, start=1):
            # Start with skipping the current legacy point.
            best_matches = previous_matches[right_position]
            best_cost = previous_costs[right_position]
            choice = 1

            # Or skip the current evidence point.
            candidate_matches = current_matches[right_position - 1]
            candidate_cost = current_costs[right_position - 1]
            if better(
                candidate_matches,
                candidate_cost,
                best_matches,
                best_cost,
            ):
                best_matches = candidate_matches
                best_cost = candidate_cost
                choice = 2

            right_time = evidence[right_index].timestamp_ms
            distance = abs(right_time - left_time)
            if distance <= RECOVERY_MATCH_TOLERANCE_MS:
                candidate_matches = previous_matches[right_position - 1] + 1
                candidate_cost = previous_costs[right_position - 1] + distance
                if better(
                    candidate_matches,
                    candidate_cost,
                    best_matches,
                    best_cost,
                ) or (
                    candidate_matches == best_matches
                    and candidate_cost == best_cost
                ):
                    # Prefer a concrete match when the objective is tied; this
                    # makes duplicate timestamps deterministic.
                    best_matches = candidate_matches
                    best_cost = candidate_cost
                    choice = 3

            current_matches[right_position] = best_matches
            current_costs[right_position] = best_cost
            choices[row_offset + right_position] = choice
        previous_matches = current_matches
        previous_costs = current_costs

    pairs: list[tuple[int, int]] = []
    left_position = left_count
    right_position = right_count
    while left_position > 0 and right_position > 0:
        choice = choices[left_position * width + right_position]
        if choice == 3:
            pairs.append(
                (
                    ordered_left[left_position - 1],
                    ordered_right[right_position - 1],
                )
            )
            left_position -= 1
            right_position -= 1
        elif choice == 2:
            right_position -= 1
        else:
            left_position -= 1
    pairs.reverse()
    return pairs


def _align_points(
    legacy: tuple[TimelinePoint, ...],
    evidence: tuple[TimelinePoint, ...],
) -> dict[str, Any]:
    left_by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    right_by_key: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, point in enumerate(legacy):
        left_by_key[point.actor_skill_key].append(index)
    for index, point in enumerate(evidence):
        right_by_key[point.actor_skill_key].append(index)

    matched_pairs: list[tuple[int, int, bool]] = []
    matched_left: set[int] = set()
    matched_right: set[int] = set()

    all_keys = sorted(set(left_by_key) | set(right_by_key))
    for key in all_keys:
        left_indices = left_by_key.get(key, [])
        right_indices = right_by_key.get(key, [])

        # Reliable action records are paired only at an identical timestamp.
        right_actions_by_time: dict[int, list[int]] = defaultdict(list)
        for right_index in right_indices:
            if evidence[right_index].reliable_action:
                right_actions_by_time[evidence[right_index].timestamp_ms].append(
                    right_index
                )
        for indices in right_actions_by_time.values():
            indices.sort(key=lambda idx: evidence[idx].packet_kind)
        for left_index in left_indices:
            left_point = legacy[left_index]
            if not left_point.reliable_action:
                continue
            candidates = right_actions_by_time.get(left_point.timestamp_ms, [])
            right_index = next(
                (idx for idx in candidates if idx not in matched_right), None
            )
            if right_index is None:
                continue
            matched_left.add(left_index)
            matched_right.add(right_index)
            matched_pairs.append((left_index, right_index, True))

        # Recovery events may move by at most 750 ms. The matcher maximises
        # pair count first, then minimises total displacement, so dense casts
        # cannot lose F1 merely because a locally nearest pair was chosen.
        left_recovery = [
            idx
            for idx in left_indices
            if idx not in matched_left and not legacy[idx].reliable_action
        ]
        right_recovery = [
            idx
            for idx in right_indices
            if idx not in matched_right and not evidence[idx].reliable_action
        ]
        for left_index, right_index in _recovery_pairs(
            legacy,
            evidence,
            left_recovery,
            right_recovery,
        ):
            matched_left.add(left_index)
            matched_right.add(right_index)
            matched_pairs.append((left_index, right_index, False))

    deleted_indices = [idx for idx in range(len(legacy)) if idx not in matched_left]
    added_indices = [idx for idx in range(len(evidence)) if idx not in matched_right]
    shifted_pairs = [
        (left_index, right_index)
        for left_index, right_index, is_action in matched_pairs
        if not is_action
        and legacy[left_index].timestamp_ms != evidence[right_index].timestamp_ms
    ]
    action_matched = sum(1 for _left, _right, action in matched_pairs if action)
    recovery_matched = len(matched_pairs) - action_matched

    per_actor: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key in all_keys:
        left_indices = left_by_key.get(key, [])
        right_indices = right_by_key.get(key, [])
        matched_for_key = [
            pair for pair in matched_pairs if legacy[pair[0]].actor_skill_key == key
        ]
        shifted_for_key = [
            (left, right)
            for left, right in shifted_pairs
            if legacy[left].actor_skill_key == key
        ]
        deltas = [
            evidence[right].timestamp_ms - legacy[left].timestamp_ms
            for left, right in shifted_for_key
        ]
        names = [
            point.source_name
            for point in (
                *(legacy[idx] for idx in left_indices),
                *(evidence[idx] for idx in right_indices),
            )
            if point.source_name
        ]
        skill_names = [
            point.skill_name
            for point in (
                *(legacy[idx] for idx in left_indices),
                *(evidence[idx] for idx in right_indices),
            )
            if point.skill_name
        ]
        row = {
            "sourceId": key[0],
            "sourceName": names[0] if names else "",
            "job": key[1],
            "skillKey": key[2],
            "skillName": skill_names[0] if skill_names else key[2],
            **_metrics(len(matched_for_key), len(left_indices), len(right_indices)),
            "actionMatched": sum(1 for _left, _right, action in matched_for_key if action),
            "recoveryMatched": sum(
                1 for _left, _right, action in matched_for_key if not action
            ),
            "added": sum(1 for idx in added_indices if evidence[idx].actor_skill_key == key),
            "deleted": sum(1 for idx in deleted_indices if legacy[idx].actor_skill_key == key),
            "shifted": len(shifted_for_key),
            "meanAbsoluteShiftMs": (
                round(sum(abs(delta) for delta in deltas) / len(deltas), 3)
                if deltas
                else 0.0
            ),
            "maxAbsoluteShiftMs": max((abs(delta) for delta in deltas), default=0),
        }
        per_actor[key] = row

    per_skill_accumulator: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "legacyCount": 0,
            "evidenceCount": 0,
            "matchedCount": 0,
            "actionMatched": 0,
            "recoveryMatched": 0,
            "added": 0,
            "deleted": 0,
            "shifted": 0,
            "shiftAbsTotal": 0,
            "maxAbsoluteShiftMs": 0,
            "skillName": "",
        }
    )
    for row in per_actor.values():
        key = (row["job"], row["skillKey"])
        target = per_skill_accumulator[key]
        for field_name in (
            "legacyCount",
            "evidenceCount",
            "matchedCount",
            "actionMatched",
            "recoveryMatched",
            "added",
            "deleted",
            "shifted",
        ):
            target[field_name] += int(row[field_name])
        target["shiftAbsTotal"] += float(row["meanAbsoluteShiftMs"]) * int(
            row["shifted"]
        )
        target["maxAbsoluteShiftMs"] = max(
            int(target["maxAbsoluteShiftMs"]), int(row["maxAbsoluteShiftMs"])
        )
        if not target["skillName"]:
            target["skillName"] = row["skillName"]

    by_skill: list[dict[str, Any]] = []
    for (job, skill_key), values in sorted(per_skill_accumulator.items()):
        shifted = int(values["shifted"])
        by_skill.append(
            {
                "job": job,
                "skillKey": skill_key,
                "skillName": values["skillName"] or skill_key,
                **_metrics(
                    int(values["matchedCount"]),
                    int(values["legacyCount"]),
                    int(values["evidenceCount"]),
                ),
                "actionMatched": int(values["actionMatched"]),
                "recoveryMatched": int(values["recoveryMatched"]),
                "added": int(values["added"]),
                "deleted": int(values["deleted"]),
                "shifted": shifted,
                "meanAbsoluteShiftMs": (
                    round(float(values["shiftAbsTotal"]) / shifted, 3)
                    if shifted
                    else 0.0
                ),
                "maxAbsoluteShiftMs": int(values["maxAbsoluteShiftMs"]),
            }
        )

    result = {
        **_metrics(len(matched_pairs), len(legacy), len(evidence)),
        "matchToleranceMs": RECOVERY_MATCH_TOLERANCE_MS,
        "actionMatchedExact": action_matched,
        "recoveryMatched": recovery_matched,
        "addedCount": len(added_indices),
        "deletedCount": len(deleted_indices),
        "shiftedCount": len(shifted_pairs),
        "added": [_event_detail(evidence[index]) for index in added_indices],
        "deleted": [_event_detail(legacy[index]) for index in deleted_indices],
        "shifted": [
            {
                "deltaMs": evidence[right].timestamp_ms - legacy[left].timestamp_ms,
                "absoluteDeltaMs": abs(
                    evidence[right].timestamp_ms - legacy[left].timestamp_ms
                ),
                "legacy": _event_detail(legacy[left]),
                "evidence": _event_detail(evidence[right]),
            }
            for left, right in shifted_pairs
        ],
        "byActorSkill": [per_actor[key] for key in sorted(per_actor)],
        "bySkill": by_skill,
    }
    return result


def _action_signature(point: TimelinePoint) -> tuple[Any, ...]:
    return (
        point.source_id,
        point.job,
        point.skill_key,
        point.skill_id,
        point.timestamp_ms,
        point.packet_kind,
    )


def _action_counter(points: Iterable[TimelinePoint]) -> Counter[tuple[Any, ...]]:
    return Counter(
        _action_signature(point) for point in points if point.reliable_action
    )


def _render_action_counter(counter: Counter[tuple[Any, ...]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for signature, count in sorted(counter.items(), key=lambda row: row[0]):
        source_id, job, skill_key, skill_id, timestamp_ms, packet_kind = signature
        rows.append(
            {
                "count": count,
                "timestampMs": timestamp_ms,
                "sourceId": source_id,
                "job": job,
                "skillKey": skill_key,
                "skillId": skill_id,
                "packetKind": packet_kind,
            }
        )
    return rows


def _action_conservation(
    expected_points: tuple[TimelinePoint, ...],
    legacy_points: tuple[TimelinePoint, ...],
    evidence_points: tuple[TimelinePoint, ...],
) -> dict[str, Any]:
    expected = _action_counter(expected_points)
    legacy = _action_counter(legacy_points)
    evidence = _action_counter(evidence_points)
    legacy_missing = expected - legacy
    legacy_unexpected = legacy - expected
    evidence_missing = expected - evidence
    evidence_unexpected = evidence - expected
    return {
        "requiredTimestampToleranceMs": 0,
        "expectedCount": sum(expected.values()),
        "legacyCount": sum(legacy.values()),
        "evidenceCount": sum(evidence.values()),
        "legacyConserved": not legacy_missing and not legacy_unexpected,
        "evidenceConserved": not evidence_missing and not evidence_unexpected,
        "crossModeConserved": legacy == evidence,
        "allConserved": (
            not legacy_missing
            and not legacy_unexpected
            and not evidence_missing
            and not evidence_unexpected
            and legacy == evidence
        ),
        "legacyMissing": _render_action_counter(legacy_missing),
        "legacyUnexpected": _render_action_counter(legacy_unexpected),
        "evidenceMissing": _render_action_counter(evidence_missing),
        "evidenceUnexpected": _render_action_counter(evidence_unexpected),
    }


def _combat_totals(snapshot: Any, combat_events: tuple[Any, ...]) -> dict[str, int]:
    return {
        "combatEventCount": len(combat_events),
        "damageEventCount": sum(
            1 for event in combat_events if not bool(getattr(event, "healing", False))
        ),
        "healingEventCount": sum(
            1 for event in combat_events if bool(getattr(event, "healing", False))
        ),
        "damageAmount": sum(
            int(getattr(event, "amount", 0) or 0)
            for event in combat_events
            if not bool(getattr(event, "healing", False))
        ),
        "healingAmount": sum(
            int(getattr(event, "amount", 0) or 0)
            for event in combat_events
            if bool(getattr(event, "healing", False))
        ),
        "realHitEvidenceCount": sum(
            1
            for event in combat_events
            if getattr(event, "has_real_hit_evidence", None) is True
        ),
        "playerHitTotal": sum(
            int(getattr(player, "hits", 0) or 0)
            for player in getattr(snapshot, "players", ())
        ),
        "snapshotDamage": int(getattr(snapshot, "total_damage", 0) or 0),
        "snapshotHealing": int(getattr(snapshot, "total_healing", 0) or 0),
        "rawDamage": int(getattr(snapshot, "raw_damage_total", 0) or 0),
        "attributedDamage": int(
            getattr(snapshot, "attributed_damage_total", 0) or 0
        ),
        "rawHealing": int(getattr(snapshot, "raw_healing_total", 0) or 0),
        "factualHealing": int(
            getattr(snapshot, "factual_healing_total", 0) or 0
        ),
        "attributedHealing": int(
            getattr(snapshot, "attributed_healing_total", 0) or 0
        ),
    }


def _totals_delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    return {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in sorted(set(before) | set(after))
    }


def _conservation_report(
    before: Mapping[str, int],
    after_legacy: Mapping[str, int],
    after_evidence: Mapping[str, int],
) -> dict[str, Any]:
    legacy_delta = _totals_delta(before, after_legacy)
    evidence_delta = _totals_delta(before, after_evidence)
    cross_delta = _totals_delta(after_legacy, after_evidence)
    return {
        "baseline": dict(before),
        "legacyAfter": dict(after_legacy),
        "evidenceAfter": dict(after_evidence),
        "legacyDelta": legacy_delta,
        "evidenceDelta": evidence_delta,
        "crossModeDelta": cross_delta,
        "legacyConserved": all(value == 0 for value in legacy_delta.values()),
        "evidenceConserved": all(value == 0 for value in evidence_delta.values()),
        "crossModeConserved": all(value == 0 for value in cross_delta.values()),
        "allConserved": (
            all(value == 0 for value in legacy_delta.values())
            and all(value == 0 for value in evidence_delta.values())
            and all(value == 0 for value in cross_delta.values())
        ),
    }


def _json_safe(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 10:
        return "<max-depth>"
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        prefix = value[:64].hex().upper()
        suffix = "..." if len(value) > 64 else ""
        return {"bytes": prefix + suffix, "length": len(value)}
    if is_dataclass(value):
        return {
            field.name: _json_safe(getattr(value, field.name), _depth=_depth + 1)
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        rows = list(value)
        converted = [
            _json_safe(item, _depth=_depth + 1) for item in rows[:2000]
        ]
        if len(rows) > 2000:
            converted.append({"truncatedItems": len(rows) - 2000})
        return converted
    return repr(value)


def _known_players(runtime: RuntimeBindings) -> dict[str, dict[str, str]]:
    profile_path = runtime.data_dir / "live_dps_profiles" / "v576.json"
    try:
        return runtime.client_profile_type.load(profile_path).known_players
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _capture_metadata(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "name": path.name,
        "sizeBytes": stat.st_size,
        "modifiedAtUtc": datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat().replace("+00:00", "Z"),
    }


def _run_capture(
    runtime: RuntimeBindings,
    store: Any,
    known_players: dict[str, dict[str, str]],
    capture: Path,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    source = runtime.replay_source_type(capture, runtime.data_dir, known_players)
    aggregator = runtime.aggregator_type()
    decoded_event_count = 0
    decode_started = time.perf_counter()
    try:
        iterator = getattr(source, "iter_batches", None)
        if callable(iterator):
            for batch in iterator(batch_size=4096):
                decoded_event_count += len(batch)
                for event in batch:
                    aggregator.ingest(event)
        else:
            # Compatibility with an older replay source. ``poll`` is called
            # exactly once, so the capture is still decoded once.
            events = source.poll()
            decoded_event_count = len(events)
            for event in events:
                aggregator.ingest(event)
    finally:
        source.close()
    decode_aggregate_ms = (time.perf_counter() - decode_started) * 1000.0

    snapshot_started = time.perf_counter()
    snapshot = aggregator.snapshot(include_events=True)
    snapshot_ms = (time.perf_counter() - snapshot_started) * 1000.0
    # The aggregator may already run the evidence resolver for the normal app
    # snapshot.  Feed both projections the same factual action axis so a
    # pre-recovered event cannot leak into the legacy side and inflate parity.
    snapshot_cast_events = tuple(snapshot.skill_cast_events)
    cast_events = tuple(
        event for event in snapshot_cast_events if runtime.authoritative_cast(event)
    )
    combat_events = tuple(snapshot.combat_events)
    expected_action_points = tuple(
        point
        for point in (_point_from_cast(runtime, event) for event in cast_events)
        if point.reliable_action
    )
    totals_before = _combat_totals(snapshot, combat_events)

    legacy_events, legacy_fallback_count, legacy_diagnostics, legacy_ms = _run_mode(
        runtime,
        mode="legacy",
        cast_events=cast_events,
        combat_events=combat_events,
        store=store,
    )
    totals_after_legacy = _combat_totals(snapshot, tuple(snapshot.combat_events))
    evidence_events, evidence_fallback_count, evidence_diagnostics, evidence_ms = (
        _run_mode(
            runtime,
            mode="evidence",
            cast_events=cast_events,
            combat_events=combat_events,
            store=store,
        )
    )
    totals_after_evidence = _combat_totals(snapshot, tuple(snapshot.combat_events))

    projection_started = time.perf_counter()
    legacy_points = _project_timeline(runtime, legacy_events)
    evidence_points = _project_timeline(runtime, evidence_events)
    alignment = _align_points(legacy_points, evidence_points)
    action_conservation = _action_conservation(
        expected_action_points, legacy_points, evidence_points
    )
    totals_conservation = _conservation_report(
        totals_before, totals_after_legacy, totals_after_evidence
    )
    projection_alignment_ms = (time.perf_counter() - projection_started) * 1000.0

    return {
        **_capture_metadata(capture),
        "status": "ok",
        "decoded": {
            "sourceEventCount": decoded_event_count,
            "combatEventCount": len(combat_events),
            "skillCastEventCount": len(cast_events),
            "snapshotSkillCastEventCount": len(snapshot_cast_events),
            "preResolvedRecoveryCount": len(snapshot_cast_events) - len(cast_events),
            "playerCount": len(snapshot.players),
            "elapsedSeconds": round(float(snapshot.elapsed_seconds), 3),
        },
        "modes": {
            "legacy": {
                "rawTimelineEventCount": len(legacy_events),
                "projectedEventCount": len(legacy_points),
                "actionEventCount": sum(
                    1 for point in legacy_points if point.reliable_action
                ),
                "recoveryEventCount": sum(
                    1 for point in legacy_points if not point.reliable_action
                ),
                "fallbackCount": legacy_fallback_count,
                "diagnostics": _json_safe(legacy_diagnostics),
            },
            "evidence": {
                "rawTimelineEventCount": len(evidence_events),
                "projectedEventCount": len(evidence_points),
                "actionEventCount": sum(
                    1 for point in evidence_points if point.reliable_action
                ),
                "recoveryEventCount": sum(
                    1 for point in evidence_points if not point.reliable_action
                ),
                "fallbackCount": evidence_fallback_count,
                "diagnostics": _json_safe(evidence_diagnostics),
            },
        },
        "alignment": alignment,
        "actionConservation": action_conservation,
        "totalsConservation": totals_conservation,
        "timingMs": {
            "decodeAndAggregate": round(decode_aggregate_ms, 3),
            "snapshot": round(snapshot_ms, 3),
            "legacyTimeline": round(legacy_ms, 3),
            "evidenceTimeline": round(evidence_ms, 3),
            "projectionAndAlignment": round(projection_alignment_ms, 3),
            "total": round((time.perf_counter() - total_started) * 1000.0, 3),
        },
    }


def _error_capture(path: Path, exc: BaseException, elapsed_ms: float) -> dict[str, Any]:
    metadata: dict[str, Any]
    try:
        metadata = _capture_metadata(path)
    except OSError:
        metadata = {"path": str(path), "name": path.name}
    return {
        **metadata,
        "status": "error",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
        "timingMs": {"total": round(elapsed_ms, 3)},
    }


def _build_summary(captures: Sequence[Mapping[str, Any]], elapsed_ms: float) -> dict[str, Any]:
    successful = [row for row in captures if row.get("status") == "ok"]
    failed = [row for row in captures if row.get("status") != "ok"]
    legacy_count = sum(
        int(row["alignment"]["legacyCount"]) for row in successful
    )
    evidence_count = sum(
        int(row["alignment"]["evidenceCount"]) for row in successful
    )
    matched_count = sum(
        int(row["alignment"]["matchedCount"]) for row in successful
    )
    return {
        "captureCount": len(captures),
        "successfulCaptures": len(successful),
        "failedCaptures": len(failed),
        **_metrics(matched_count, legacy_count, evidence_count),
        "addedCount": sum(
            int(row["alignment"]["addedCount"]) for row in successful
        ),
        "deletedCount": sum(
            int(row["alignment"]["deletedCount"]) for row in successful
        ),
        "shiftedCount": sum(
            int(row["alignment"]["shiftedCount"]) for row in successful
        ),
        "actionConservedCaptures": sum(
            1 for row in successful if row["actionConservation"]["allConserved"]
        ),
        "totalsConservedCaptures": sum(
            1 for row in successful if row["totalsConservation"]["allConserved"]
        ),
        "elapsedMs": round(elapsed_ms, 3),
    }


def _base_report(
    *,
    project_root: Path,
    args: argparse.Namespace,
    captures: Sequence[Path],
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "generatedAtUtc": _utc_now(),
        "status": "initializing",
        "tool": {
            "path": str(Path(__file__).resolve()),
            "projectRoot": str(project_root),
            "python": sys.version,
        },
        "options": {
            "inputs": [str(Path(item).expanduser().resolve()) for item in args.inputs],
            "pattern": args.pattern,
            "limit": args.limit,
            "output": str(args.output.resolve()) if args.output else None,
            "recoveryMatchToleranceMs": RECOVERY_MATCH_TOLERANCE_MS,
        },
        "sideEffectPolicy": {
            "captureAccess": "read-only",
            "network": "blocked at socket and stdlib HTTP layers",
            "cloudUpload": "disabled",
            "scoreUpload": "disabled",
            "diagnosticUpload": "disabled",
            "analyticsUpload": "disabled",
            "replayCache": "not used",
            "scoreCache": "not used",
            "decoderUserKeyCacheWrites": "blocked",
        },
        "discoveredCaptures": len(captures),
        "captures": [],
    }


def _encode_report(report: Mapping[str, Any]) -> str:
    return json.dumps(
        _json_safe(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
        allow_nan=False,
    ) + "\n"


def _emit_report(report: Mapping[str, Any], output: Path | None) -> None:
    encoded = _encode_report(report)
    if output is None:
        sys.stdout.write(encoded)
        sys.stdout.flush()
        return
    destination = output.expanduser().resolve()
    if destination.suffix.casefold() == ".dpscap":
        raise ValueError("--output 不得覆盖 .dpscap 抓包文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, destination)
    print(f"JSON report: {destination}", file=sys.stderr, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读比较 .dpscap 时间轴 legacy/evidence 两种模式；"
            "不会评分、写缓存或访问网络。"
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="一个或多个 .dpscap 文件或目录（目录递归扫描）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON 报告路径；省略时将 JSON 写到标准输出",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="按路径排序、去重后最多处理多少份抓包",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"目录递归扫描使用的 glob（默认：{DEFAULT_PATTERN}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须是正整数")
    if not str(args.pattern or "").strip():
        parser.error("--pattern 不能为空")

    try:
        captures = _discover_captures(args.inputs, args.pattern, args.limit)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not captures:
        parser.error(
            f"没有找到符合 {args.pattern!r} 的 .dpscap 文件"
        )

    project_root = Path(__file__).resolve().parents[1]
    report = _base_report(project_root=project_root, args=args, captures=captures)
    started = time.perf_counter()

    _configure_offline_environment()
    _install_network_guard()
    try:
        runtime = _load_runtime(project_root)
    except TimelineInterfaceError as exc:
        report.update(
            {
                "status": "incompatible_interface",
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "summary": {
                    "captureCount": len(captures),
                    "successfulCaptures": 0,
                    "failedCaptures": 0,
                    "decodedCaptures": 0,
                    "elapsedMs": round((time.perf_counter() - started) * 1000.0, 3),
                },
            }
        )
        _emit_report(report, args.output)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2

    report["interface"] = {
        "function": "cast_timeline._timeline_events_with_damage_fallback",
        "signature": runtime.timeline_signature,
        "modes": ["legacy", "evidence"],
        "diagnostics": "mutable dict supplied independently to each mode",
    }

    store_started = time.perf_counter()
    store = runtime.data_store_type.load()
    store_load_ms = (time.perf_counter() - store_started) * 1000.0
    known_players = _known_players(runtime)

    rows: list[dict[str, Any]] = []
    for index, capture in enumerate(captures, start=1):
        capture_started = time.perf_counter()
        print(
            f"[{index}/{len(captures)}] {capture}",
            file=sys.stderr,
            flush=True,
        )
        try:
            row = _run_capture(runtime, store, known_players, capture)
        except Exception as exc:  # keep the remaining corpus auditable
            row = _error_capture(
                capture,
                exc,
                (time.perf_counter() - capture_started) * 1000.0,
            )
            print(
                f"  ERROR {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
        rows.append(row)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    report["captures"] = rows
    report["summary"] = _build_summary(rows, elapsed_ms)
    report["summary"]["dataStoreLoadMs"] = round(store_load_ms, 3)
    report["status"] = (
        "ok"
        if report["summary"]["failedCaptures"] == 0
        else "partial_failure"
    )
    _emit_report(report, args.output)
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
