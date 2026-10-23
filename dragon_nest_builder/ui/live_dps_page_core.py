from __future__ import annotations

import ctypes
import copy
import hashlib
import json
import math
import os
import re
import socket
import threading
import time
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..hp_conditional import compact_boss_hp_segments
from ..live_dps.aggregator import CombatAggregator
from ..live_dps.boss_hp import (
    normalize_boss_hp_name,
    resolve_fixed_boss_hp_profile,
)
from ..live_dps.boss_recognizer import BossRecognizer
from ..live_dps.collector import LiveDpsCollector
from ..live_dps.event_platform import event_platform_for
from ..live_dps.cast_timing import LiveCastTimingResolver
from ..live_dps.damage_ledger import (
    is_disallowed_damage_derived_cast,
    requires_action_packet_for_cast,
)
from ..live_dps.critical_stats import is_critical_rate_sample
from ..live_dps.scoring import (
    PlayerScore,
    ScoreTimelineCast,
    TeamScoreEngine,
    _YAO_MEDITATION_ROLE_THRESHOLD,
    _coverage_in_windows,
    _snapshot_recording_end_seconds,
    canonical_skill,
    yao_rotation_variant,
)
from ..live_dps.models import (
    BossActionEvent,
    BossEffectSnapshot,
    BossEffectViewSnapshot,
    BossHpTrackSnapshot,
    CombatEvent,
    CombatSnapshot,
    PartyMemberEvent,
    PlayerBuffSeriesSnapshot,
    PlayerInspectEvent,
    SkillCastEvent,
    StatusEventSnapshot,
)
from ..live_dps.sources import (
    JsonlReplaySource,
    V576AutoCaptureProbe,
    V576DiagnosticReplaySource,
    V576PcapReplaySource,
)
from ..live_dps.npcap import find_npcap_dll
from ..calculator import BuildCalculator
from ..data_loader import DataStore, as_number, norm
from ..profile_panel import ProfilePanelCalculator
from ..shared import (
    BOSS_PRESETS,
    SkillBurstTimelineWidget,
    attr_number_text,
    load_skill_build_defaults,
    resource_path,
)
from ..timeline_parallel import warm_timeline_process_pool
from .team_score_sync_dialog import (
    SupabaseScoreSyncDialog,
    TeamScoreSyncJob,
    load_team_score_sync_config,
    start_team_score_queue_sync_thread,
    start_team_score_sync_thread,
)
from ..live_dps.team_score_sync import TeamScoreExportRow, score_export_job
from ..live_dps.team_score_cache import TeamScoreDiskCache, team_score_cache_key
from ..live_dps.replay_cache import (
    ReplayCacheIdentity,
    ReplayDiskCache,
    replay_cache_identity,
)


def _compact_number(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.0f}"


def _nice_axis_max(value: float) -> float:
    """Round an axis ceiling upward to a stable, readable value."""
    value = max(1.0, float(value))
    exponent = math.floor(math.log10(value))
    scale = 10.0**exponent
    fraction = value / scale
    step = next(
        candidate
        for candidate in (1.0, 2.0, 2.5, 5.0, 10.0)
        if fraction <= candidate
    )
    return step * scale


_TEAM_SCORE_AURA_SKILL_KEYS = frozenset(
    {canonical_skill("元素光环"), canonical_skill("判定光环")}
)
_TEAM_SCORE_AURA_GROUP_KEY = canonical_skill("圣骑互斥光环")
_TEAM_SCORE_DEFAULT_BOSS_PRESET = "红龙佩达"
_TEAM_SCORE_PREP_CONTEXT_SECONDS = 3.0
# Team value remains a strong near-tie preference in the theoretical-axis
# scheduler. Full weight made the local greedy planner over-trade personal
# damage in multi-capture A/B tests; final score composition remains separate.
_TEAM_SCORE_OUTPUT_TEAM_OBJECTIVE_WEIGHT = 0.30
_TEAM_SCORE_DEFAULT_EXCLUDED_GLYPHS = frozenset(
    {(canonical_skill("幽灵之魂"), "增益持续")}
)

_TERMINAL_BOSS_COMPLETION_MOVEMENT_SKILLS = frozenset(
    canonical_skill(skill)
    for skill in ("翻滚", "冲锋", "闪现", "疾闪", "滑步", "三连斩")
)


from ..live_dps.theory_plan_builder import (
    TheoryPlanBuilderMixin,
    _team_score_glyph_effect_key,
    _team_score_glyph_row_key,
    _team_score_apply_boss_preset,
    _team_score_support_effect_group,
    _team_score_team_dps,
    _team_score_joint_objective_profiles,
    _team_score_scale_objective_profiles,
    _team_score_standalone_gain_percent,
    _team_score_effect_changes_rotation,
    _team_score_effects_with_required_cdr_glyphs,
    _team_score_merge_intervals,
    _team_score_recorded_cdr_cast_schedule,
    _team_score_recorded_rotation_schedule,
    _team_score_recorded_effect_cast_schedule,
    _team_score_active_windows_to_cast_schedule,
    bind_ui_application as _bind_theory_ui_application,
)
_bind_theory_ui_application(QApplication)





def _team_score_boss_name_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", norm(value).casefold())


def _team_score_match_boss_preset(value: Any) -> str:
    key = _team_score_boss_name_key(value)
    if not key:
        return ""
    exact = {
        _team_score_boss_name_key(name): name for name in BOSS_PRESETS
    }
    if key in exact:
        return exact[key]
    matches = [
        name
        for name in BOSS_PRESETS
        if min(len(key), len(_team_score_boss_name_key(name))) >= 4
        and (
            key in _team_score_boss_name_key(name)
            or _team_score_boss_name_key(name) in key
        )
    ]
    return max(matches, key=lambda name: len(_team_score_boss_name_key(name))) if matches else ""












def _team_score_removal_loss_percent(full_dps: float, reduced_dps: float) -> float:
    if full_dps <= 1e-9:
        return 0.0
    return max(0.0, (full_dps - reduced_dps) / full_dps * 100.0)




def _team_score_plan_dps_through(
    result: dict[str, Any], horizon: float
) -> float:
    """Return the plan's DPS over its opening horizon without replanning it."""

    plan_window = max(0.0, as_number(result.get("window")))
    sample_window = min(plan_window, max(0.0, horizon))
    if sample_window <= 1e-9 or sample_window >= plan_window - 1e-6:
        return max(0.0, as_number(result.get("dps")))
    total = 0.0
    for segment in result.get("segments", ()):
        if not isinstance(segment, dict):
            continue
        start = max(0.0, as_number(segment.get("start")))
        if start >= sample_window - 1e-9:
            continue
        end = max(start, as_number(segment.get("end"), start))
        score = max(0.0, as_number(segment.get("effective_score")))
        if end > sample_window + 1e-9 and end > start + 1e-9:
            score *= max(0.0, sample_window - start) / (end - start)
        total += score
    damage_panel = max(0.0, as_number(result.get("damage_panel")))
    hand = max(0.0, as_number(result.get("hand_multiplier"), 1.0))
    return damage_panel * (total / sample_window) / 100.0 * hand








def _team_score_interval_overlap(
    start: float,
    end: float,
    windows: list[tuple[float, float]] | tuple[tuple[float, float], ...],
) -> float:
    return sum(
        max(0.0, min(end, window_end) - max(start, window_start))
        for window_start, window_end in windows
        if window_end > start + 1e-9 and window_start < end - 1e-9
    )










def _team_score_event_snapshot_for_refresh(
    latest_snapshot: CombatSnapshot | None,
    cached_event_snapshot: CombatSnapshot | None,
    cached_event_revision: int,
    current_revision: int,
    snapshot_loader: Any,
) -> tuple[CombatSnapshot | None, bool]:
    """Return an event-complete snapshot for a score-only refresh.

    Boss selector changes are handled by a timer rather than the normal tab
    refresh path. ``_latest_snapshot`` may therefore be a lightweight summary
    produced by another statistics view, which has player totals but no casts
    or damage events. Reusing it makes every selected-Boss axis appear empty.
    """

    if (
        cached_event_snapshot is not None
        and cached_event_revision == current_revision
    ):
        return cached_event_snapshot, False
    if latest_snapshot is not None and (
        event_platform_for(latest_snapshot).combat_events
        or event_platform_for(latest_snapshot).skill_cast_events
        or event_platform_for(latest_snapshot).boss_effect_events
    ):
        return latest_snapshot, True
    if callable(snapshot_loader):
        return snapshot_loader(), True
    return latest_snapshot, False


def _team_score_optimized_cdr_cast_schedule(
    effects: list[dict[str, Any]],
    output_windows: tuple[tuple[float, float], ...],
    window: float,
    *,
    effect_skill_name: Any = norm,
    grid_seconds: float = 0.5,
) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Place CDR buffs over active Boss windows without a costly timeline search."""

    if window <= 0 or not output_windows:
        return {}
    active = _team_score_merge_intervals(
        [
            (max(0.0, start), min(window, end))
            for start, end in output_windows
            if end > 0 and start < window
        ]
    )
    if not active:
        return {}

    cdr_skills = {canonical_skill("精神爆发"), canonical_skill("时间加速")}
    grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for effect in effects:
        skill_name = effect_skill_name(effect.get("skill"))
        skill_key = canonical_skill(skill_name)
        provider = norm(effect.get("provider"))
        if not provider or skill_key not in cdr_skills:
            continue
        if not _team_score_effect_changes_rotation(effect):
            continue
        cooldown = max(0.0, as_number(effect.get("cooldown")))
        duration = max(0.0, as_number(effect.get("duration")))
        if cooldown <= 0 or duration <= 0:
            continue
        previous = grouped[skill_key].get(provider)
        if previous is None:
            grouped[skill_key][provider] = {
                "cooldown": cooldown,
                "duration": duration,
            }
        else:
            previous["cooldown"] = min(previous["cooldown"], cooldown)
            previous["duration"] = max(previous["duration"], duration)

    step = max(0.1, float(grid_seconds))
    schedule: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for skill_key, provider_map in grouped.items():
        covered: list[tuple[float, float]] = []
        providers = sorted(
            provider_map.items(),
            key=lambda row: (
                -(row[1]["duration"] / max(row[1]["cooldown"], 1e-9)),
                row[0],
            ),
        )
        for provider, timing in providers:
            cooldown = timing["cooldown"]
            duration = timing["duration"]
            candidate_values = {
                round(index * step, 6)
                for index in range(int(window / step) + 1)
            }
            for start, end in active:
                candidate_values.add(round(start, 6))
                candidate_values.add(round(max(0.0, end - duration), 6))
            candidates = sorted(
                value for value in candidate_values if 0 <= value < window - 1e-9
            )
            if not candidates:
                continue
            values = [
                max(
                    0.0,
                    _team_score_interval_overlap(start, start + duration, active)
                    - _team_score_interval_overlap(start, start + duration, covered),
                )
                for start in candidates
            ]
            count = len(candidates)
            best = [0.0] * (count + 1)
            take = [False] * count
            next_index = [count] * count
            for index in range(count - 1, -1, -1):
                next_row = bisect_left(
                    candidates, candidates[index] + cooldown - 1e-9, index + 1
                )
                next_index[index] = next_row
                chosen = values[index] + best[next_row]
                skipped = best[index + 1]
                if values[index] > 1e-9 and chosen >= skipped - 1e-9:
                    best[index] = chosen
                    take[index] = True
                else:
                    best[index] = skipped

            intervals: list[tuple[float, float]] = []
            index = 0
            while index < count:
                if take[index]:
                    start = candidates[index]
                    intervals.append((start, min(window, start + duration)))
                    index = next_index[index]
                else:
                    index += 1
            if intervals:
                schedule[(provider, skill_key)] = intervals
                covered = _team_score_merge_intervals([*covered, *intervals])
    return schedule


def _windows_process_id(process_name: str) -> int:
    """Find a process without spawning PowerShell or requesting process access."""
    if os.name != "nt":
        return 0

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return 0
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.casefold() == process_name.casefold():
                    return int(entry.th32ProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return 0


def _windows_udp_ports_for_pid(pid: int) -> list[int]:
    """Read the IPv4 UDP endpoint table through the documented IP Helper API."""
    if os.name != "nt" or pid <= 0:
        return []

    class UdpRowOwnerPid(ctypes.Structure):
        _fields_ = [
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphlpapi.GetExtendedUdpTable
    get_table.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.BOOL,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    get_table.restype = wintypes.DWORD
    size = wintypes.ULONG(0)
    # AF_INET + UDP_TABLE_OWNER_PID. The first call obtains the buffer size.
    result = get_table(None, ctypes.byref(size), False, 2, 1, 0)
    if result not in (0, 122) or size.value < ctypes.sizeof(wintypes.DWORD):
        return []
    buffer = ctypes.create_string_buffer(size.value)
    if get_table(buffer, ctypes.byref(size), False, 2, 1, 0) != 0:
        return []
    count = wintypes.DWORD.from_buffer_copy(buffer.raw[:4]).value
    row_size = ctypes.sizeof(UdpRowOwnerPid)
    ports: set[int] = set()
    for index in range(count):
        offset = 4 + index * row_size
        if offset + row_size > size.value:
            break
        row = UdpRowOwnerPid.from_buffer_copy(buffer.raw[offset : offset + row_size])
        if int(row.dwOwningPid) == pid:
            port = socket.ntohs(int(row.dwLocalPort) & 0xFFFF)
            if port:
                ports.add(port)
    return sorted(ports)


def _windows_tcp_ports_for_pid(pid: int) -> list[int]:
    """Read active IPv4 TCP endpoints owned by a process via IP Helper."""
    if os.name != "nt" or pid <= 0:
        return []

    class TcpRowOwnerPid(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.ULONG),
        wintypes.BOOL,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    get_table.restype = wintypes.DWORD
    size = wintypes.ULONG(0)
    # AF_INET + TCP_TABLE_OWNER_PID_ALL.
    result = get_table(None, ctypes.byref(size), False, 2, 5, 0)
    if result not in (0, 122) or size.value < ctypes.sizeof(wintypes.DWORD):
        return []
    buffer = ctypes.create_string_buffer(size.value)
    if get_table(buffer, ctypes.byref(size), False, 2, 5, 0) != 0:
        return []
    count = wintypes.DWORD.from_buffer_copy(buffer.raw[:4]).value
    row_size = ctypes.sizeof(TcpRowOwnerPid)
    ports: set[int] = set()
    for index in range(count):
        offset = 4 + index * row_size
        if offset + row_size > size.value:
            break
        row = TcpRowOwnerPid.from_buffer_copy(buffer.raw[offset : offset + row_size])
        if int(row.dwOwningPid) != pid:
            continue
        port = socket.ntohs(int(row.dwLocalPort) & 0xFFFF)
        if port:
            ports.add(port)
    return sorted(ports)


def _effect_skill_key(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    text = re.sub(r"EX(?:Lv\d+)?$", "", text, flags=re.IGNORECASE)
    return norm(text)


def _friendly_buff_attribute(value: object) -> str:
    text = str(value or "").strip()
    aliases = {
        "物攻%": "物攻",
        "魔攻%": "魔攻",
        "致命面板%": "致命率",
        "眩晕面板%": "眩晕率",
        "最终%": "最终",
        "所有属性攻%": "全属性攻",
    }
    if text in aliases:
        return aliases[text]
    if text.endswith("面板%"):
        return f"{text[:-3]}率"
    if text.endswith("%"):
        return text[:-1]
    return text


@lru_cache(maxsize=1)
def _live_buff_display_catalog() -> dict[str, dict[str, str]]:
    path = resource_path(
        Path(
            "dragon_nest_builder/data/live_dps_profiles/"
            "player_buff_display_catalog_v576.json"
        )
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = payload.get("buffs", []) if isinstance(payload, dict) else []
    catalog: dict[str, dict[str, str]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        skill = str(raw.get("skill", "")).strip()
        display = str(raw.get("display", "")).strip()
        if not skill or not display:
            continue
        catalog[_effect_skill_key(skill)] = {
            "skill": skill,
            "display": display,
            "category": str(raw.get("category", "")).strip() or "其他增益",
            "scope": str(raw.get("scope", "")).strip() or "personal",
        }
    return catalog


def _live_buff_catalog_value(
    buff_rows: list[dict[str, object]], field: str
) -> str:
    return next(
        (
            str(row.get(field, "")).strip()
            for row in buff_rows
            if str(row.get(field, "")).strip()
        ),
        "",
    )


def _live_buff_candidate_value(
    effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot,
) -> float | None:
    candidate_value = effect.value
    if candidate_value is not None:
        return float(candidate_value)
    parameter_text = str(getattr(effect, "parameters", "") or "").strip()
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", parameter_text):
        return as_number(parameter_text)
    return None


def _live_buff_value_is_trusted(
    effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot,
    buff_rows: list[dict[str, object]],
) -> bool:
    candidate_value = _live_buff_candidate_value(effect)
    if candidate_value is None:
        return False
    known_values = [
        as_number(row.get("表格数值"))
        for row in buff_rows
        if row.get("表格数值") not in (None, "")
    ]
    return any(abs(value - candidate_value) <= 1e-6 for value in known_values)


def _live_buff_effect_name(
    effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot,
    buff_rows: list[dict[str, object]],
) -> str:
    """Resolve a packet buff into the actual stat shown in the overview."""
    explicit_display = _live_buff_catalog_value(buff_rows, "实时显示")
    if explicit_display:
        return explicit_display
    candidate_value = _live_buff_candidate_value(effect)

    skill_key = _effect_skill_key(effect.skill_name)
    attributes: list[str] = []
    all_skill_attributes: list[str] = []
    for row in buff_rows:
        if _effect_skill_key(row.get("技能")) != skill_key:
            continue
        attribute = _friendly_buff_attribute(row.get("属性"))
        if attribute and attribute not in all_skill_attributes:
            all_skill_attributes.append(attribute)
        if candidate_value is not None and abs(
            as_number(row.get("表格数值")) - float(candidate_value)
        ) > 1e-6:
            continue
        if attribute and attribute not in attributes:
            attributes.append(attribute)
    if attributes:
        return "、".join(attributes)
    if len(all_skill_attributes) == 1:
        return all_skill_attributes[0]

    label = str(effect.label or "").strip()
    for suffix in ("增益状态", "增益", "状态"):
        if label.endswith(suffix):
            label = label[: -len(suffix)].strip()
            break
    return label or "未识别属性"


def _live_buff_display_text(
    effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot,
    buff_rows: list[dict[str, object]],
) -> str:
    effect_text = _live_buff_effect_name(effect, buff_rows)
    if _live_buff_catalog_value(buff_rows, "实时显示"):
        return effect_text
    if not _live_buff_value_is_trusted(effect, buff_rows):
        return effect_text
    candidate_value = _live_buff_candidate_value(effect)
    return _combined_buff_effect_text(effect_text, f"{candidate_value:g}%")


def _live_buff_validated_value_text(
    effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot,
    buff_rows: list[dict[str, object]],
) -> str:
    if _live_buff_catalog_value(buff_rows, "实时显示"):
        return "-"
    if not _live_buff_value_is_trusted(effect, buff_rows):
        return "-"
    candidate_value = _live_buff_candidate_value(effect)
    return f"{candidate_value:g}%"


def _combined_buff_effect_text(effect_text: object, value_text: object) -> str:
    """Combine the buff attribute and its packet value for compact tables."""
    effect = str(effect_text or "").strip() or "未识别属性"
    value = str(value_text or "").strip()
    if not value or value == "-" or value in effect:
        return effect
    return f"{effect} {value}"


def _is_catalogued_player_buff(
    effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot | StatusEventSnapshot,
    rows_by_skill: dict[str, list[dict[str, object]]],
) -> bool:
    """Only expose buffs that are part of the simulator-owned buff table."""
    return bool(rows_by_skill.get(_effect_skill_key(effect.skill_name)))


def _is_anonymous_player_name(name: str, source_id: str = "") -> bool:
    text = str(name or "").strip()
    if not text or text == str(source_id or "").strip():
        return True
    compact = re.sub(r"\s+", "", text).lower()
    return bool(re.fullmatch(r"(?:角色)?0x[0-9a-f]+", compact))


def _profession_player_display_details(
    ordered_ids: list[str],
    jobs_by_source: dict[str, str],
    stable_order_by_job: dict[str, list[str]],
) -> dict[str, tuple[str, str]]:
    """Use professions as player labels and number duplicate professions."""

    unique_ids = list(dict.fromkeys(source_id for source_id in ordered_ids if source_id))
    ids_by_job: dict[str, list[str]] = defaultdict(list)
    for source_id in unique_ids:
        job = str(jobs_by_source.get(source_id, "") or "").strip()
        label = job or "未知职业"
        ids_by_job[label].append(source_id)
        stable_order = stable_order_by_job.setdefault(label, [])
        if source_id not in stable_order:
            stable_order.append(source_id)

    resolved: dict[str, tuple[str, str]] = {}
    for source_id in unique_ids:
        job = str(jobs_by_source.get(source_id, "") or "").strip()
        label = job or "未知职业"
        same_job_ids = ids_by_job[label]
        display_name = label
        if len(same_job_ids) > 1:
            stable_order = stable_order_by_job[label]
            current_order = [
                actor_id for actor_id in stable_order if actor_id in same_job_ids
            ]
            display_name = f"{label}{current_order.index(source_id) + 1}"
        resolved[source_id] = (display_name, job)
    return resolved


from ..live_dps.boss_scope import (
    _BossStatisticsGroup,
    _merge_seconds_windows,
    _boss_statistics_groups,
    _compress_events_to_windows,
    _compress_boss_hp_tracks_to_windows,
    _completed_boss_group_identities,
    _events_in_windows,
)





@lru_cache(maxsize=1)
def _boss_template_identity_catalog() -> dict[int, tuple[str, int]]:
    path = resource_path(
        Path(
            "dragon_nest_builder/data/live_dps_profiles/"
            "monster_templates_v576.json"
        )
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    templates = payload.get("templates", {}) if isinstance(payload, dict) else {}
    return {
        int(template_id): (str(row.get("name", "")).strip(), int(row.get("hp", 0)))
        for template_id, row in templates.items()
        if isinstance(row, dict) and row.get("boss") and str(template_id).isdigit()
    }


def _infer_preceding_boss_identity(
    following_bucket: dict[str, object],
) -> tuple[str, int]:
    following_ids = set(following_bucket.get("target_ids", ()))
    following_template_ids = [
        int(target_id.rsplit("@", 1)[1])
        for target_id in following_ids
        if "@" in target_id and target_id.rsplit("@", 1)[1].isdigit()
    ]
    if not following_template_ids:
        return "", 0
    following_template_id = min(following_template_ids)
    following_hp = max(0, int(following_bucket.get("max_hp", 0)))
    candidates = [
        (template_id, name, hp)
        for template_id, (name, hp) in _boss_template_identity_catalog().items()
        if template_id < following_template_id
        and (not following_hp or hp == following_hp)
    ]
    if not candidates:
        return "", 0
    _template_id, name, hp = max(candidates, key=lambda row: row[0])
    return name, hp


def _legacy_boss_statistics_groups(
    snapshot: CombatSnapshot,
) -> tuple[_BossStatisticsGroup, ...]:
    """Group transient actors into at most eight named Boss encounters."""
    target_context: dict[str, tuple[str, str, int]] = {}
    platform = event_platform_for(snapshot)
    for target in platform.damage_targets:
        if target.target_id and not target.target_id.startswith("__"):
            target_context[target.target_id] = (
                target.target_name.strip(),
                target.target_job.strip(),
                max(0, int(target.target_max_hp)),
            )
    for effect in (*platform.boss_effects, *platform.boss_effect_events):
        if not effect.target_id:
            continue
        known_name, known_job, known_hp = target_context.get(
            effect.target_id, ("", "", 0)
        )
        target_context[effect.target_id] = (
            effect.target_name.strip() or known_name,
            effect.target_job.strip() or known_job,
            known_hp,
        )
    for event in platform.combat_events:
        if not event.target_id:
            continue
        known_name, known_job, known_hp = target_context.get(
            event.target_id, ("", "", 0)
        )
        target_context[event.target_id] = (
            event.target_name.strip() or known_name,
            event.target_job.strip() or known_job,
            event.target_max_hp or known_hp,
        )

    accumulators: dict[tuple[str, str], dict[str, object]] = {}
    for event in platform.combat_events:
        if event.healing or not event.target_id or event.amount <= 0:
            continue
        context_name, context_job, context_hp = target_context.get(
            event.target_id, ("", "", 0)
        )
        target_name = event.target_name.strip() or context_name
        target_job = event.target_job.strip() or context_job
        target_max_hp = event.target_max_hp or context_hp
        identity = CombatAggregator._target_identity_key(
            event.target_id, target_name, target_max_hp
        )
        bucket = accumulators.setdefault(
            identity,
            {
                "name": target_name,
                "max_hp": target_max_hp,
                "target_ids": set(),
                "damage": 0,
                "hits": 0,
                "critical_hits": 0,
                "critical_samples": 0,
                "first_ms": event.timestamp_ms,
                "last_ms": event.timestamp_ms,
                "series": defaultdict(int),
                "actor_bounds": {},
                "confirmed_boss": False,
            },
        )
        bucket["name"] = target_name or bucket["name"]
        bucket["max_hp"] = target_max_hp or bucket["max_hp"]
        bucket["target_ids"].add(event.target_id)
        bucket["damage"] += event.amount
        bucket["hits"] += 1
        if is_critical_rate_sample(event):
            bucket["critical_samples"] += 1
            bucket["critical_hits"] += int(event.critical)
        bucket["first_ms"] = min(bucket["first_ms"], event.timestamp_ms)
        bucket["last_ms"] = max(bucket["last_ms"], event.timestamp_ms)
        bucket["series"][event.timestamp_ms // 1000] += event.amount
        bounds = bucket["actor_bounds"].setdefault(
            event.target_id, [event.timestamp_ms, event.timestamp_ms]
        )
        bounds[0] = min(bounds[0], event.timestamp_ms)
        bounds[1] = max(bounds[1], event.timestamp_ms)
        if target_job.partition(" ")[0].casefold() == "boss":
            bucket["confirmed_boss"] = True

    # Older capture versions did not attach target_job to every damage hit.
    # Damage-target context still confirms those identities when available.
    for target in platform.damage_targets:
        identity = CombatAggregator._target_identity_key(
            target.target_id, target.target_name, target.target_max_hp
        )
        bucket = accumulators.get(identity)
        if bucket is not None and (
            target.target_job.strip().partition(" ")[0].casefold() == "boss"
        ):
            bucket["confirmed_boss"] = True

    # Status-only packets can use a different transient actor ID from the
    # corresponding damage packet.  Attach them by Boss name (and HP when it
    # is available) so selecting one Boss keeps every phase/status entity.
    for target_id, (target_name, target_job, target_max_hp) in target_context.items():
        if not target_id or not target_name:
            continue
        matching = [
            bucket
            for bucket in accumulators.values()
            if str(bucket["name"]).strip().casefold() == target_name.casefold()
            and (
                not target_max_hp
                or not int(bucket["max_hp"])
                or int(bucket["max_hp"]) == target_max_hp
            )
        ]
        for bucket in matching:
            bucket["target_ids"].add(target_id)
            if target_job.partition(" ")[0].casefold() == "boss":
                bucket["confirmed_boss"] = True

    confirmed_candidates = [
        (identity, bucket)
        for identity, bucket in accumulators.items()
        if bucket["confirmed_boss"]
    ]
    source_ids = {event.source_id for event in platform.combat_events}
    confirmed_candidates = sorted(
        confirmed_candidates,
        key=lambda item: int(item[1]["first_ms"]),
    )
    anonymous_before_first: list[
        tuple[tuple[str, str], dict[str, object]]
    ] = []
    first_confirmed_ms = (
        int(confirmed_candidates[0][1]["first_ms"])
        if confirmed_candidates
        else None
    )
    for identity, bucket in accumulators.items():
        if bucket["confirmed_boss"] or bucket["name"]:
            continue
        target_ids = set(bucket["target_ids"])
        if target_ids & source_ids or int(bucket["hits"]) < 3:
            continue
        first_ms = int(bucket["first_ms"])
        last_ms = int(bucket["last_ms"])
        if (
            first_confirmed_ms is not None
            and last_ms + 5_000 >= first_confirmed_ms
        ):
            continue
        anonymous_before_first.append((identity, bucket))

    inferred_candidates: list[
        tuple[tuple[str, str], dict[str, object]]
    ] = []
    if confirmed_candidates and anonymous_before_first:
        _old_identity, inferred_bucket = max(
            anonymous_before_first,
            key=lambda item: (int(item[1]["damage"]), int(item[1]["hits"])),
        )
        inferred_name, inferred_hp = _infer_preceding_boss_identity(
            confirmed_candidates[0][1]
        )
        if inferred_name:
            inferred_bucket["name"] = inferred_name
            inferred_bucket["max_hp"] = inferred_hp
        inferred_id = next(iter(inferred_bucket["target_ids"]), "")
        inferred_identity = CombatAggregator._target_identity_key(
            inferred_id,
            str(inferred_bucket["name"]),
            int(inferred_bucket["max_hp"]),
        )
        inferred_candidates.append((inferred_identity, inferred_bucket))
    elif not confirmed_candidates:
        inferred_candidates = sorted(
            accumulators.items(),
            key=lambda item: int(item[1]["damage"]),
            reverse=True,
        )[:8]
    candidates = sorted(
        [*confirmed_candidates, *inferred_candidates],
        key=lambda item: (
            int(item[1]["first_ms"]),
            -int(item[1]["damage"]),
        ),
    )[:8]

    groups: list[_BossStatisticsGroup] = []
    for identity, bucket in candidates:
        actor_windows = [
            (
                max(0.0, (bounds[0] - 10_000) / 1000.0),
                (bounds[1] + 2_000) / 1000.0,
            )
            for bounds in bucket["actor_bounds"].values()
        ]
        name = str(bucket["name"] or "").strip()
        groups.append(
            _BossStatisticsGroup(
                identity=identity,
                name=name or f"未识别 Boss（第{len(groups) + 1}关）",
                max_hp=max(0, int(bucket["max_hp"])),
                target_ids=frozenset(bucket["target_ids"]),
                damage=int(bucket["damage"]),
                hits=int(bucket["hits"]),
                critical_hits=int(bucket["critical_hits"]),
                first_ms=int(bucket["first_ms"]),
                last_ms=int(bucket["last_ms"]),
                damage_series=tuple(sorted(bucket["series"].items())),
                phase_windows=_merge_seconds_windows(actor_windows),
                critical_samples=int(bucket["critical_samples"]),
            )
        )
    groups.sort(key=lambda group: (group.first_ms, -group.damage, group.name))
    return tuple(groups)






def _timeline_display_bounds(
    snapshot: CombatSnapshot,
    phase_windows: tuple[tuple[float, float], ...] = (),
) -> tuple[float, float]:
    """Return capture-clock bounds shared by every replay timeline."""

    del phase_windows
    return 0.0, max(0.0, _snapshot_recording_end_seconds(snapshot))


def _compress_ranges_to_windows(
    ranges: tuple[tuple[float, float], ...],
    windows: tuple[tuple[float, float], ...],
    *,
    preserve_clock: bool = False,
) -> tuple[tuple[tuple[float, float], ...], float]:
    """Clip ranges to Boss windows, optionally retaining capture seconds."""
    merged_windows = _merge_seconds_windows(list(windows))
    if not merged_windows:
        return (), 0.0
    offsets: list[tuple[float, float, float]] = []
    elapsed = 0.0
    for start, end in merged_windows:
        offsets.append((start, end, start if preserve_clock else elapsed))
        elapsed += end - start

    mapped: list[tuple[float, float]] = []
    for range_start, range_end in ranges:
        start_value = max(0.0, float(range_start))
        end_value = max(start_value, float(range_end))
        for window_start, window_end, offset in offsets:
            if end_value <= start_value + 1e-9:
                if window_start <= start_value <= window_end:
                    point = offset + start_value - window_start
                    mapped.append((point, point))
                continue
            start = max(start_value, window_start)
            end = min(end_value, window_end)
            if start < end:
                mapped.append(
                    (
                        offset + start - window_start,
                        offset + end - window_start,
                    )
                )
    return tuple(mapped), (
        merged_windows[-1][1] if preserve_clock else elapsed
    )


def _map_score_seconds_to_display(
    seconds: float,
    windows: tuple[tuple[float, float], ...],
) -> float | None:
    """Keep one score timestamp on the capture clock when it is in scope."""
    value = max(0.0, float(seconds))
    if not windows:
        return value
    mapped, _elapsed = _compress_ranges_to_windows(
        ((value, value),), windows, preserve_clock=True
    )
    return mapped[0][0] if mapped else None


def _team_score_dynamic_output_points(
    score: PlayerScore,
    duration: float,
    display_windows: tuple[tuple[float, float], ...] = (),
) -> tuple[tuple[float, float], ...]:
    """Return cumulative output profit/loss against the full theoretical total."""

    def timed_power(
        rows: tuple[ScoreTimelineCast, ...],
    ) -> list[tuple[float, float, bool]]:
        result: list[tuple[float, float, bool]] = []
        for row in rows:
            if row.power <= 0:
                continue
            mapped = _map_score_seconds_to_display(row.seconds, display_windows)
            if mapped is None:
                continue
            result.append((mapped, max(0.0, row.power), row.in_burst_window))
        return result

    actual = timed_power(score.actual_timeline)
    theory = timed_power(score.theory_timeline)
    actual_raw_total = sum(power for _seconds, power, _burst in actual)
    theory_raw_total = sum(power for _seconds, power, _burst in theory)
    actual_scale = (
        score.actual_standard_power / actual_raw_total
        if actual_raw_total > 1e-9
        else 0.0
    )
    theory_scale = (
        score.theory_standard_power / theory_raw_total
        if theory_raw_total > 1e-9
        else 0.0
    )
    actual = [
        (seconds, power * actual_scale, burst)
        for seconds, power, burst in actual
    ]
    theory = [
        (seconds, power * theory_scale, burst)
        for seconds, power, burst in theory
    ]

    changes: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for seconds, power, _in_burst in actual:
        changes[seconds][0] += power
    for seconds, power, _in_burst in theory:
        changes[seconds][1] += power

    full_theory_total = max(0.0, float(score.theory_standard_power))
    actual_power = theory_power = 0.0
    points: list[tuple[float, float]] = [(0.0, 0.0)]
    for seconds in sorted(changes):
        actual_delta, theory_delta = changes[seconds]
        actual_power += actual_delta
        theory_power += theory_delta
        points.append(
            (
                max(0.0, seconds),
                (
                    (actual_power - theory_power)
                    / full_theory_total
                    * 100.0
                    if full_theory_total > 1e-9
                    else 0.0
                ),
            )
        )

    final_seconds = max(0.0, float(duration))
    final_point = (
        final_seconds,
        (
            (score.actual_standard_power - score.theory_standard_power)
            / full_theory_total
            * 100.0
            if full_theory_total > 1e-9
            else 0.0
        ),
    )
    if not points or abs(points[-1][0] - final_seconds) > 1e-6:
        points.append(final_point)
    else:
        points[-1] = final_point
    return tuple(points)


def _tight_curve_bounds(
    points: tuple[tuple[float, float], ...],
    *,
    padding_ratio: float = 0.08,
    minimum_padding: float = 0.5,
) -> tuple[float, float]:
    """Fit an asymmetric axis around the complete curve while retaining zero."""
    values = [float(value) for _seconds, value in points]
    raw_minimum = min([0.0, *values])
    raw_maximum = max([0.0, *values])
    span = max(1.0, raw_maximum - raw_minimum)
    padding = max(float(minimum_padding), span * float(padding_ratio))
    minimum = math.floor((raw_minimum - padding) * 10.0) / 10.0
    maximum = math.ceil((raw_maximum + padding) * 10.0) / 10.0
    if maximum <= minimum + 1e-9:
        return minimum - 0.5, maximum + 0.5
    return minimum, maximum


def _team_score_effect_category(kind: Any, effect_key: Any) -> tuple[str, str]:
    text = f"{norm(kind)} {norm(effect_key)}"
    if "冰冻层" in text or "叠冰" in text:
        return "team_freeze", "#35a9c9"
    if "致命" in text and ("抵抗" in text or "抗" in text):
        return "team_crit_resist", "#c7873f"
    if "防御" in text or "减防" in text:
        return "team_defense", "#a8734d"
    if "减" in text and "抗" in text:
        return "team_resist", "#f59e0b"
    if "增伤" in text or "易伤" in text:
        return "team_amp", "#ef7f5a"
    if "buff" in text.casefold() or "增益" in text:
        return "team_buff", "#46a77a"
    return "state", "#8b75d7"


_TEAM_SCORE_OUTPUT_EXTRA_ELEMENTS: dict[str, frozenset[str]] = {
    "烈": frozenset({"火", "暗"}),
    "冰灵": frozenset({"水", "火"}),
    "炼金圣士": frozenset({"水", "火"}),
    "药剂师": frozenset({"水", "火"}),
    "暗": frozenset({"光", "暗"}),
    "曜": frozenset({"光", "暗"}),
    "输出曜": frozenset({"光", "暗"}),
    "打坐曜": frozenset({"光", "暗"}),
}
_TEAM_SCORE_OUTPUT_MIXED_ATTACK_JOBS = frozenset({"圣骑士", "十字军"})


def _team_score_output_profile_channels(
    job: Any, profile: dict[str, float] | None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return the attack/element channels that can improve one player's damage."""

    profile = profile or {}
    job_name = norm(job)
    attacks = {
        label
        for label, key in (("物理", "physical"), ("魔法", "magical"))
        if as_number(profile.get(key)) > 1e-6
    }
    if job_name in _TEAM_SCORE_OUTPUT_MIXED_ATTACK_JOBS:
        attacks.update({"物理", "魔法"})
    if not attacks:
        attacks.add("物理")

    elements = {
        label
        for label, key in (
            ("火", "fire"),
            ("水", "water"),
            ("光", "light"),
            ("暗", "dark"),
        )
        if as_number(profile.get(key)) > 1e-6
    }
    elements.update(_TEAM_SCORE_OUTPUT_EXTRA_ELEMENTS.get(job_name, ()))
    return frozenset(attacks), frozenset(elements)


def _team_score_output_effect_key_relevant(
    job: Any,
    profile: dict[str, float] | None,
    effect_key: Any,
) -> bool:
    attacks, elements = _team_score_output_profile_channels(job, profile)
    text = norm(effect_key)
    if not text:
        return False
    if "物攻" in text or "物理" in text:
        return "物理" in attacks
    if "魔攻" in text or "魔法" in text:
        return "魔法" in attacks
    mentioned_elements = {element for element in ("火", "水", "光", "暗") if element in text}
    if mentioned_elements and ("攻" in text or "减" in text and "抗" in text):
        return bool(mentioned_elements & elements)
    if "属性攻" in text:
        return bool(elements)
    return text in {
        "团队Buff",
        "双攻%",
        "四维%",
        "最终%",
        "致命%",
        "动作速度%",
        "冷却恢复%",
    }


def _team_score_output_buff_relevant(
    job: Any,
    profile: dict[str, float] | None,
    effect_text: Any,
    category_text: Any = "",
) -> bool:
    """Hide defensive or wrong-channel team buffs from an output timeline."""

    attacks, elements = _team_score_output_profile_channels(job, profile)
    text = f"{norm(category_text)} {norm(effect_text)}"
    if not text.strip():
        return False
    defensive_only_markers = (
        "防御增益",
        "减伤增益",
        "护盾增益",
        "治疗/减伤",
        "资源恢复",
        "格挡",
        "超级盔甲",
    )
    offensive_markers = (
        "攻击增益",
        "追加伤害",
        "冷却增益",
        "速度/冷却",
        "冷却/恢复",
        "属性攻击",
        "条件增伤",
        "最终",
        "致命",
        "动作速度",
        "技能CD",
        "冷却",
        "伤害",
        "力量",
        "敏捷",
        "智力",
    )
    if any(marker in text for marker in defensive_only_markers) and not any(
        marker in text for marker in offensive_markers
    ):
        return False

    mentioned_elements = {element for element in ("火", "水", "光", "暗") if element in text}
    if mentioned_elements and ("攻" in text or "属性攻击" in text):
        return bool(mentioned_elements & elements)
    has_physical = "物攻" in text or "物理攻击" in text
    has_magical = "魔攻" in text or "魔法攻击" in text
    if has_physical or has_magical:
        return (has_physical and "物理" in attacks) or (
            has_magical and "魔法" in attacks
        )
    return any(marker in text for marker in offensive_markers)


def _team_score_output_curve_specs(
    job: Any, profile: dict[str, float] | None,
) -> tuple[tuple[str, str, str], ...]:
    """Select only the real Boss-state curves that can affect this job."""

    attacks, elements = _team_score_output_profile_channels(job, profile)
    rows: list[tuple[str, str, str]] = []
    if "物理" in attacks:
        rows.append(("物理增伤合计%", "物理增伤", "#e56b61"))
    if "魔法" in attacks:
        rows.append(("魔法增伤合计%", "魔法增伤", "#b06cc7"))
    for element, color in (
        ("火", "#e66a42"),
        ("水", "#3196c7"),
        ("光", "#d4a72c"),
        ("暗", "#7656ad"),
    ):
        if element in elements:
            rows.append((f"减{element}抗", f"减{element}抗", color))
    rows.append(("减致命抵抗%", "减致命抗", "#c7873f"))
    if "物理" in attacks:
        rows.append(("减物理防御%", "减物理防御", "#9b6b43"))
    if "魔法" in attacks:
        rows.append(("减魔法防御%", "减魔法防御", "#6f789e"))
    return tuple(rows)


def _team_score_improvement_rows(
    score: PlayerScore,
) -> tuple[dict[str, object], ...]:
    """Turn one completed score into concrete, Boss-scoped improvement items."""

    def priority(value: float) -> str:
        if value >= 5.0:
            return "高"
        if value >= 2.0:
            return "中"
        return "低"

    output_rows: list[dict[str, object]] = []
    theory_total = max(1e-9, score.theory_standard_power)
    for detail in score.skill_details:
        if detail.excluded_from_score or detail.theory_power <= 0:
            continue
        missing_power = max(0.0, detail.theory_power - detail.actual_power)
        if missing_power <= max(1.0, detail.theory_power * 0.02):
            continue
        estimated_gain = missing_power / theory_total * 70.0
        missing_casts = max(0, detail.theory_count - detail.actual_count)
        if missing_casts > 0:
            advice = (
                f"在可输出区间内争取多释放 {missing_casts} 次；"
                "技能转好时优先避免被低收益动作挤占。"
            )
        elif detail.proportional_output and detail.duration_utilization < 0.98:
            advice = (
                f"减少提前中断，当前持续段只兑现 "
                f"{detail.duration_utilization * 100:.1f}%。"
            )
        else:
            advice = (
                "次数接近理论但标准伤害偏低；优先放进增伤、减抗、"
                "属性攻或动作加速窗口，并保证多段伤害完整命中。"
            )
        output_rows.append(
            {
                "direction": "输出分",
                "priority": priority(estimated_gain),
                "item": detail.skill_name,
                "current": (
                    f"{detail.actual_count}/{detail.theory_count}次；"
                    f"完成 {detail.completion:.1f}%"
                ),
                "advice": advice,
                "gain": f"输出分理论可追回约 +{estimated_gain:.2f}",
                "sort_value": estimated_gain,
            }
        )
    output_rows.sort(key=lambda row: -as_number(row.get("sort_value")))
    output_rows = output_rows[:6]

    burst_gap = max(0.0, 100.0 - score.burst_usage)
    if burst_gap > 0.5:
        weakest_windows = sorted(
            (row for row in score.burst_windows if row.completion < 98.0),
            key=lambda row: (row.completion, -row.theory_power),
        )[:3]
        window_text = "、".join(
            f"{row.label}({row.completion:.1f}%)" for row in weakest_windows
        ) or "精神爆发/时间加速窗口"
        estimated_gain = burst_gap * 0.30
        output_rows.append(
            {
                "direction": "输出分",
                "priority": priority(estimated_gain),
                "item": "爆发/加速窗口",
                "current": f"兑现率 {score.burst_usage:.1f}%",
                "advice": (
                    f"优先改善 {window_text}；把高标准伤害技能和可受益的"
                    "长CD技能移入窗口，减少窗口内空挡。"
                ),
                "gain": f"输出分理论可追回约 +{estimated_gain:.2f}",
                "sort_value": estimated_gain,
            }
        )
    output_rows.sort(key=lambda row: -as_number(row.get("sort_value")))

    support_rows: list[dict[str, object]] = []
    for detail in score.support_details:
        if not detail.included_in_score:
            continue
        contribution_completion = (
            detail.actual_contribution / detail.theory_contribution * 100.0
            if detail.theory_contribution > 1e-9
            else detail.completion
        )
        effective_completion = min(detail.completion, contribution_completion)
        weighted_gap = max(
            0.0,
            detail.score_weight * (100.0 - effective_completion) / 100.0,
        )
        if weighted_gap <= 0.2:
            continue
        actual_seconds = sum(
            max(0.0, end - start)
            for start, end in (
                detail.actual_display_intervals or detail.actual_intervals
            )
        )
        theory_seconds = sum(
            max(0.0, end - start)
            for start, end in (
                detail.theory_display_intervals or detail.theory_intervals
            )
        )
        if actual_seconds <= 1e-6:
            advice = (
                "所选 Boss 阶段没有形成有效覆盖；确认技能已学习并在"
                "Boss可输出后尽早施放。"
            )
        elif "冷却" in detail.effect_key or detail.skill_name in {
            "精神爆发",
            "时间加速",
            "鸡尾酒",
        }:
            advice = (
                "避开 Boss 即将不可输出的时段，并把窗口放在团队高伤技能"
                "集中转好的位置；同名效果不要重叠浪费。"
            )
        else:
            advice = (
                "按CD补齐断档，优先覆盖Boss实际受击区间；存在同名来源时"
                "错开施放，避免重复覆盖。"
            )
        support_rows.append(
            {
                "direction": "辅助分",
                "priority": priority(weighted_gap),
                "item": f"{detail.skill_name} · {detail.effect_key}",
                "current": (
                    f"覆盖 {detail.actual_coverage * 100:.1f}%/"
                    f"{detail.theory_coverage * 100:.1f}%；"
                    f"{actual_seconds:.1f}/{theory_seconds:.1f}s"
                ),
                "advice": advice,
                "gain": f"加权辅助缺口约 {weighted_gap:.2f}",
                "sort_value": weighted_gap,
            }
        )
    support_rows.sort(key=lambda row: -as_number(row.get("sort_value")))
    support_rows = support_rows[:8]

    rows = [*output_rows, *support_rows]
    if not rows:
        rows.append(
            {
                "direction": "综合",
                "priority": "低",
                "item": "当前未发现明显短板",
                "current": (
                    f"输出 {score.output_score:.1f} / 辅助 {score.support_score:.1f}"
                ),
                "advice": "当前已接近本职业在所选Boss范围内的理论轴表现。",
                "gain": "保持现有节奏",
                "sort_value": 0.0,
            }
        )
    return tuple(rows)


def _compress_boss_phases_to_windows(
    groups: tuple[_BossStatisticsGroup, ...],
    windows: tuple[tuple[float, float], ...],
    *,
    preserve_clock: bool = False,
) -> tuple[tuple[str, tuple[tuple[float, float], ...]], ...]:
    """Clip Boss phases, optionally retaining capture-relative seconds."""
    merged_windows = _merge_seconds_windows(list(windows))
    if not merged_windows:
        return ()
    offsets: list[tuple[float, float, float]] = []
    elapsed = 0.0
    for start, end in merged_windows:
        offsets.append((start, end, start if preserve_clock else elapsed))
        elapsed += end - start

    result: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    for group in groups:
        mapped: list[tuple[float, float]] = []
        for phase_start, phase_end in group.phase_windows:
            for window_start, window_end, offset in offsets:
                start = max(phase_start, window_start)
                end = min(phase_end, window_end)
                if start < end:
                    mapped.append(
                        (
                            offset + start - window_start,
                            offset + end - window_start,
                        )
                    )
        if mapped:
            result.append((group.name, _merge_seconds_windows(mapped)))
    return tuple(result)




def _source_seconds_from_compressed_windows(
    seconds: float,
    windows: tuple[tuple[float, float], ...],
) -> float | None:
    """Map one Boss-page clock position back to the capture clock."""
    merged = _merge_seconds_windows(list(windows))
    if not merged:
        return None
    selected = max(0.0, float(seconds))
    offset = 0.0
    for index, (start, end) in enumerate(merged):
        span = max(0.0, end - start)
        is_last = index == len(merged) - 1
        if selected < offset + span - 1e-9 or (
            is_last and selected <= offset + span + 1e-9
        ):
            return start + max(0.0, min(span, selected - offset))
        offset += span
    return merged[-1][1]


def _boss_hp_value_at_compressed_seconds(
    rows: tuple[
        tuple[
            str,
            str,
            tuple[tuple[tuple[float, float], ...], ...],
        ],
        ...,
    ],
    boss_name: str,
    seconds: float,
) -> tuple[float, str] | None:
    """Return the latest HP step for one named Boss on the compressed axis."""
    for name, confidence, segments in rows:
        if norm(name) != norm(boss_name):
            continue
        for points in segments:
            if (
                not points
                or seconds < points[0][0] - 1e-6
                or seconds > points[-1][0] + 1e-6
            ):
                continue
            times = tuple(point[0] for point in points)
            index = max(0, min(len(points) - 1, bisect_right(times, seconds) - 1))
            return float(points[index][1]), confidence
    return None


def _boss_realtime_metrics(
    summary: dict[str, float],
    boss_preset: dict[str, Any],
) -> dict[str, object]:
    """Apply live reductions to one Boss parameter preset."""
    element_base = as_number(boss_preset.get("element_resist"))
    defense_base = max(0.0, as_number(boss_preset.get("monster_defense")))
    critical_base = max(0.0, as_number(boss_preset.get("crit_resist")))
    resistances = {
        element: element_base
        - max(0.0, as_number(summary.get(f"减{element}抗%")))
        for element in ("火", "水", "光", "暗")
    }
    physical_defense_down = min(
        100.0, max(0.0, as_number(summary.get("减物理防御%")))
    )
    magical_defense_down = min(
        100.0, max(0.0, as_number(summary.get("减魔法防御%")))
    )
    critical_down = min(
        100.0, max(0.0, as_number(summary.get("减致命抵抗%")))
    )
    return {
        "universal_amp": max(0.0, as_number(summary.get("增伤%"))),
        "physical_amp": max(0.0, as_number(summary.get("物理增伤合计%"))),
        "magical_amp": max(0.0, as_number(summary.get("魔法增伤合计%"))),
        "resistances": resistances,
        "physical_defense": defense_base * (1.0 - physical_defense_down / 100.0),
        "magical_defense": defense_base * (1.0 - magical_defense_down / 100.0),
        "critical_resist": critical_base * (1.0 - critical_down / 100.0),
        "freeze_layers": min(2.0, max(0.0, as_number(summary.get("冰冻层")))),
        "attack_down": min(100.0, max(0.0, as_number(summary.get("减攻击%")))),
        "physical_defense_down": physical_defense_down,
        "magical_defense_down": magical_defense_down,
        "critical_resist_down": critical_down,
    }






def _compress_effect_view_to_windows(
    view: BossEffectViewSnapshot,
    windows: tuple[tuple[float, float], ...],
    *,
    preserve_clock: bool = False,
) -> tuple[BossEffectViewSnapshot, float]:
    """Clip Boss effects, optionally retaining capture-relative seconds."""
    merged = _merge_seconds_windows(list(windows))
    if not merged:
        return BossEffectViewSnapshot(), 0.0
    offsets: list[tuple[float, float, float]] = []
    elapsed = 0.0
    for start, end in merged:
        offsets.append((start, end, start if preserve_clock else elapsed))
        elapsed += end - start

    def mapped_time(seconds: float) -> float | None:
        for start, end, offset in offsets:
            if start <= seconds <= end:
                return offset + seconds - start
        return None

    compressed_events: list[StatusEventSnapshot] = []
    for event in view.events:
        mapped = mapped_time(event.timestamp_seconds)
        if mapped is None:
            continue
        source_window = next(
            row
            for row in offsets
            if row[0] <= event.timestamp_seconds <= row[1]
        )
        event_end = min(event.end_seconds, source_window[1])
        compressed_events.append(
            replace(
                event,
                timestamp_seconds=mapped,
                end_seconds=(
                    source_window[2] + event_end - source_window[0]
                ),
            )
        )

    compressed_series: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    for label, points in view.series:
        ordered = sorted(points)
        rebased: list[tuple[float, float]] = []
        for start, end, offset in offsets:
            previous_value = 0.0
            for second, value in ordered:
                if second > start:
                    break
                previous_value = value
            rebased.append((offset, previous_value))
            for second, value in ordered:
                if start < second <= end:
                    rebased.append((offset + second - start, value))
        compressed_series.append((label, tuple(rebased)))

    return (
        replace(
            view,
            series=tuple(compressed_series),
            events=tuple(compressed_events),
        ),
        merged[-1][1] if preserve_clock else elapsed,
    )


def _clip_player_buff_series_to_windows(
    rows: tuple[PlayerBuffSeriesSnapshot, ...],
    windows: tuple[tuple[float, float], ...],
    output_windows: dict[str, tuple[tuple[float, float], ...]] | None = None,
) -> tuple[PlayerBuffSeriesSnapshot, ...]:
    if not windows:
        return ()
    clipped_rows: list[PlayerBuffSeriesSnapshot] = []
    for row in rows:
        measurement_windows = windows
        if output_windows is not None:
            player_windows = output_windows.get(row.target_id, ())
            measurement_windows = _merge_seconds_windows(
                [
                    (max(start, boss_start), min(end, boss_end))
                    for start, end in player_windows
                    for boss_start, boss_end in windows
                    if max(start, boss_start) < min(end, boss_end)
                ]
            )
        clipped = _merge_seconds_windows(
            [
                (max(start, window_start), min(end, window_end))
                for start, end in row.intervals
                for window_start, window_end in measurement_windows
                if max(start, window_start) < min(end, window_end)
            ]
        )
        if clipped:
            clipped_rows.append(replace(row, intervals=clipped))
    return tuple(clipped_rows)


def _compress_player_buff_series_to_windows(
    rows: tuple[PlayerBuffSeriesSnapshot, ...],
    windows: tuple[tuple[float, float], ...],
    *,
    preserve_clock: bool = False,
) -> tuple[tuple[PlayerBuffSeriesSnapshot, ...], float]:
    """Clip player buffs, optionally retaining capture-relative seconds."""
    merged = _merge_seconds_windows(list(windows))
    if not merged:
        return (), 0.0
    offsets: list[tuple[float, float, float]] = []
    elapsed = 0.0
    for start, end in merged:
        offsets.append((start, end, start if preserve_clock else elapsed))
        elapsed += end - start

    compressed_rows: list[PlayerBuffSeriesSnapshot] = []
    for row in rows:
        mapped: list[tuple[float, float]] = []
        for interval_start, interval_end in row.intervals:
            for window_start, window_end, offset in offsets:
                start = max(interval_start, window_start)
                end = min(interval_end, window_end)
                if start < end:
                    mapped.append(
                        (
                            offset + start - window_start,
                            offset + end - window_start,
                        )
                    )
        mapped_intervals = _merge_seconds_windows(mapped)
        if mapped_intervals:
            compressed_rows.append(replace(row, intervals=mapped_intervals))
    return tuple(compressed_rows), (
        merged[-1][1] if preserve_clock else elapsed
    )


def _joined_buff_field(rows: list[object], field: str) -> str:
    values: list[str] = []
    for row in rows:
        value = str(getattr(row, field, "") or "").strip()
        if value and value not in values:
            values.append(value)
    return "、".join(values)


def _merged_buff_value(rows: list[object]) -> float | None:
    values = {
        float(value)
        for row in rows
        if (value := getattr(row, "value", None)) is not None
    }
    return next(iter(values)) if len(values) == 1 else None


def _merged_buff_category(
    skill_name: str,
    rows_by_skill: dict[str, list[dict[str, object]]],
    fallback: str,
) -> str:
    return (
        _live_buff_catalog_value(
            rows_by_skill.get(_effect_skill_key(skill_name), []), "实时分类"
        )
        or fallback
    )


def _merge_player_buff_effect_rows(
    rows: tuple[BossEffectSnapshot, ...],
    rows_by_skill: dict[str, list[dict[str, object]]],
) -> tuple[BossEffectSnapshot, ...]:
    """Collapse one skill's internal status codes into one target-side buff row."""
    grouped: dict[tuple[str, str], list[BossEffectSnapshot]] = defaultdict(list)
    for row in rows:
        skill_key = _effect_skill_key(row.skill_name or row.skill_id)
        grouped[(row.target_id, skill_key)].append(row)

    merged: list[BossEffectSnapshot] = []
    for group_rows in grouped.values():
        representative = group_rows[0]
        values = {
            str(row.parameters or "").strip()
            for row in group_rows
            if str(row.parameters or "").strip()
        }
        merged.append(
            replace(
                representative,
                source_id=_joined_buff_field(group_rows, "source_id"),
                source_name=_joined_buff_field(group_rows, "source_name"),
                job=_joined_buff_field(group_rows, "job"),
                value=_merged_buff_value(group_rows),
                status_kind=_merged_buff_category(
                    representative.skill_name,
                    rows_by_skill,
                    representative.status_kind,
                ),
                parameters=next(iter(values)) if len(values) == 1 else "",
                start_seconds=min(row.start_seconds for row in group_rows),
                end_seconds=max(row.end_seconds for row in group_rows),
                last_refresh_seconds=max(
                    row.last_refresh_seconds for row in group_rows
                ),
                refresh_count=max(row.refresh_count for row in group_rows),
                active=any(row.active for row in group_rows),
                uptime_seconds=max(row.uptime_seconds for row in group_rows),
                coverage=max(row.coverage for row in group_rows),
            )
        )
    return tuple(merged)


def _merge_player_buff_series_rows(
    rows: tuple[PlayerBuffSeriesSnapshot, ...],
    rows_by_skill: dict[str, list[dict[str, object]]],
) -> tuple[PlayerBuffSeriesSnapshot, ...]:
    """Union split packet tracks so one target and one skill form one series."""
    grouped: dict[tuple[str, str], list[PlayerBuffSeriesSnapshot]] = defaultdict(list)
    for row in rows:
        skill_key = _effect_skill_key(row.skill_name or row.skill_id)
        grouped[(row.target_id, skill_key)].append(row)

    merged: list[PlayerBuffSeriesSnapshot] = []
    for group_rows in grouped.values():
        representative = group_rows[0]
        merged.append(
            replace(
                representative,
                source_id=_joined_buff_field(group_rows, "source_id"),
                source_name=_joined_buff_field(group_rows, "source_name"),
                job=_joined_buff_field(group_rows, "job"),
                value=_merged_buff_value(group_rows),
                status_kind=_merged_buff_category(
                    representative.skill_name,
                    rows_by_skill,
                    representative.status_kind,
                ),
                intervals=_merge_seconds_windows(
                    [
                        interval
                        for row in group_rows
                        for interval in row.intervals
                    ]
                ),
            )
        )
    return tuple(merged)


def _player_output_windows_seconds(
    events: tuple[CombatEvent, ...],
) -> dict[str, tuple[tuple[float, float], ...]]:
    active_seconds: dict[str, set[int]] = defaultdict(set)
    for event in events:
        if event.healing or event.amount <= 0 or not event.source_id:
            continue
        active_seconds[event.source_id].add(max(0, event.timestamp_ms // 1000))
    return {
        source_id: _merge_seconds_windows(
            [(float(second), float(second + 1)) for second in sorted(seconds)]
        )
        for source_id, seconds in active_seconds.items()
    }




__all__ = tuple(name for name in globals() if not name.startswith("__"))
