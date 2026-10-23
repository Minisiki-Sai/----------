from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime
from html import escape
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.boss_recognizer import BossEncounter, BossRecognizer
from dragon_nest_builder.live_dps.models import CombatEvent, CombatSnapshot
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.replay_cache import replay_cache_identity
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


_ANONYMOUS_NAME = re.compile(r"^(?:角色\s*)?0x[0-9a-f]+$", re.IGNORECASE)

# A death is often the end of a longer mechanic chain. Keep enough history to
# show what led to it and enough aftermath to cover revive/recovery casts.
DEFAULT_BEFORE_SECONDS = 30.0
DEFAULT_AFTER_SECONDS = 20.0


def _seconds(value: object) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _clock(value: object) -> str:
    total_ms = max(0, round(_seconds(value) * 1000.0))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _delta_clock(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}{abs(value):.3f}s"


def _safe_name(value: object, fallback: str = "未知") -> str:
    text = str(value or "").strip()
    if not text or _ANONYMOUS_NAME.match(text):
        return fallback
    return text


def _file_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    return cleaned[:80] or "unknown"


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _identity(snapshot: CombatSnapshot, actor_id: str) -> dict[str, object]:
    result: dict[str, object] = {
        "actor_id": actor_id,
        "name": "",
        "job": "",
        "level": 0,
        "identity_source": "combat_context",
        "identity_confidence": 0.0,
        "identity_layout": "",
    }
    for player in snapshot.players:
        if player.source_id != actor_id:
            continue
        result["name"] = _safe_name(player.name, "")
        result["job"] = str(player.job or "").strip()
    for row in snapshot.player_health_tracks:
        if row.target_id != actor_id:
            continue
        if not result["name"]:
            result["name"] = _safe_name(row.target_name, "")
        if not result["job"]:
            result["job"] = str(row.target_job or "").strip()
    candidates = sorted(
        (row for row in snapshot.party_members if row.actor_id == actor_id),
        key=lambda row: (row.confidence, bool(row.name), bool(row.job)),
        reverse=True,
    )
    if candidates:
        row = candidates[0]
        reliable_name = _safe_name(row.name, "")
        if reliable_name:
            result["name"] = reliable_name
        if row.job:
            result["job"] = row.job
        result.update(
            {
                "level": max(0, int(row.level or 0)),
                "identity_source": "party_member_packet",
                "identity_confidence": max(0.0, min(1.0, float(row.confidence))),
                "identity_layout": row.source_layout,
            }
        )
    return result


def _death_events(snapshot: CombatSnapshot) -> list[CombatEvent]:
    rows = [
        row
        for row in snapshot.combat_events
        if str(row.attribution_method or "").casefold()
        == "player_death_hp_state_086f"
    ]
    return sorted(rows, key=lambda row: (row.timestamp_ms, row.target_id))


def _map_at(
    map_history: Iterable[dict[str, object]],
    timestamp_seconds: float,
) -> dict[str, object] | None:
    rows = sorted(
        (dict(row) for row in map_history if isinstance(row, dict)),
        key=lambda row: _seconds(row.get("observed_at")),
    )
    available = [
        row for row in rows if _seconds(row.get("observed_at")) <= timestamp_seconds + 0.001
    ]
    return available[-1] if available else (rows[0] if rows else None)


def _encounters_at(
    encounters: Iterable[BossEncounter],
    timestamp_seconds: float,
) -> list[BossEncounter]:
    active = [
        encounter
        for encounter in encounters
        if any(
            float(start) - 0.001 <= timestamp_seconds <= float(end) + 0.001
            for start, end in encounter.phase_windows
        )
    ]
    if active:
        return active
    nearby = [
        encounter
        for encounter in encounters
        if encounter.first_ms / 1000.0 - 3.0
        <= timestamp_seconds
        <= encounter.last_ms / 1000.0 + 3.0
    ]
    return nearby


def _active_player_buffs(
    snapshot: CombatSnapshot,
    actor_id: str,
    timestamp_seconds: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for series in snapshot.player_buff_series:
        if series.target_id != actor_id:
            continue
        for start, end in series.intervals:
            if float(start) - 0.001 <= timestamp_seconds <= float(end) + 0.001:
                key = (
                    series.skill_name,
                    series.label,
                    series.source_id,
                    round(float(start), 3),
                    round(float(end), 3),
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "skill": series.skill_name or series.skill_id,
                        "effect": series.label or series.key,
                        "value": series.value,
                        "source_id": series.source_id,
                        "source_name": _safe_name(series.source_name, series.job or series.source_id),
                        "source_job": series.job,
                        "start_seconds": float(start),
                        "end_seconds": float(end),
                        "open_ended": bool(series.open_ended),
                    }
                )
    return sorted(rows, key=lambda row: (str(row["skill"]), float(row["start_seconds"])))


def _baseline(snapshot: CombatSnapshot, actor_id: str) -> dict[str, object]:
    for track in snapshot.player_health_tracks:
        if track.target_id == actor_id:
            return {
                "max_hp": max(0, int(track.max_hp)),
                "max_hp_source": track.max_hp_source,
                "exact_percent": bool(track.exact_percent),
                "state_source": track.state_source,
            }
    return {
        "max_hp": 0,
        "max_hp_source": "missing",
        "exact_percent": False,
        "state_source": "missing",
    }


def _raw_health_rows(
    aggregator: object,
    actor_id: str,
) -> list[object]:
    rows = [
        row
        for row in getattr(aggregator, "_player_health_events", ())
        if row.target_id == actor_id
        and row.packet_kind == "s2c-udp-player-hp-state-086f-v576"
        and row.current_hp >= 0
    ]
    # The decoder can publish several state transitions from different UDP
    # layouts at the same capture timestamp.  Their insertion order is the
    # only chronology preserved by the aggregator; packet length/phase is not
    # a sequence number and must never be used to reorder them.
    return sorted(rows, key=lambda row: row.timestamp_ms)


def _health_context(
    rows: list[object],
    death_ms: int,
    before_seconds: float,
    after_seconds: float,
    max_hp: int,
) -> dict[str, object]:
    exact_zeros = [
        row for row in rows if row.timestamp_ms == death_ms and row.current_hp == 0
    ]
    zero = exact_zeros[0] if exact_zeros else None
    zero_index = rows.index(zero) if zero is not None else len(rows)
    previous_positive = next(
        (row for row in reversed(rows[:zero_index]) if row.current_hp > 0), None
    )
    previous_timestamp_positive = next(
        (
            row
            for row in reversed(rows[:zero_index])
            if row.current_hp > 0 and row.timestamp_ms < death_ms
        ),
        None,
    )
    next_positive = next(
        (row for row in rows[zero_index + 1 :] if row.current_hp > 0),
        None,
    )
    start_ms = death_ms - round(max(0.0, before_seconds) * 1000)
    end_ms = death_ms + round(max(0.0, after_seconds) * 1000)
    selected: list[dict[str, object]] = []
    previous_key: tuple[int, int] | None = None
    previous_distinct_hp: int | None = None
    for row in rows:
        key = (row.timestamp_ms, row.current_hp)
        if key == previous_key:
            continue
        previous_key = key
        current_hp = int(row.current_hp)
        hp_change = (
            current_hp - previous_distinct_hp
            if previous_distinct_hp is not None
            else None
        )
        previous_distinct_hp = current_hp
        if not start_ms <= row.timestamp_ms <= end_ms:
            continue
        selected.append(
            {
                "timestamp_ms": row.timestamp_ms,
                "seconds": row.timestamp_ms / 1000.0,
                "relative_seconds": (row.timestamp_ms - death_ms) / 1000.0,
                "current_hp": current_hp,
                "hp_change": hp_change,
                "percent": (
                    max(0.0, min(100.0, current_hp / max_hp * 100.0))
                    if max_hp > 0
                    else None
                ),
                "packet_signature": row.packet_signature,
                "packet_length": int(row.packet_length or 0),
                "xor_phase": int(row.xor_phase or 0),
                "record_offset": int(row.record_offset or 0),
            }
        )

    def raw_row(row: object | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "timestamp_ms": row.timestamp_ms,
            "seconds": row.timestamp_ms / 1000.0,
            "current_hp": int(row.current_hp),
            "percent": (
                max(0.0, min(100.0, row.current_hp / max_hp * 100.0))
                if max_hp > 0
                else None
            ),
            "packet_signature": row.packet_signature,
            "packet_length": int(row.packet_length or 0),
            "xor_phase": int(row.xor_phase or 0),
            "record_offset": int(row.record_offset or 0),
        }

    return {
        "points": selected,
        "zero_packets": [raw_row(row) for row in exact_zeros],
        "previous_positive": raw_row(previous_positive),
        "previous_timestamp_positive": raw_row(previous_timestamp_positive),
        "next_positive": raw_row(next_positive),
    }


def _event_row(row: object, death_ms: int) -> dict[str, object]:
    timestamp_ms = max(0, int(getattr(row, "timestamp_ms", 0) or 0))
    result = dict(_jsonable(row)) if is_dataclass(row) else dict(row)
    result["seconds"] = timestamp_ms / 1000.0
    result["relative_seconds"] = (timestamp_ms - death_ms) / 1000.0
    return result


def _markdown_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    materialized = [[str(cell) for cell in row] for row in rows]
    if not materialized:
        return "（无）"
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in materialized:
        lines.append(
            "| "
            + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in row)
            + " |"
        )
    return "\n".join(lines)


def _value_text(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _render_report(evidence: dict[str, object]) -> str:
    """Render the concise, player-facing view of one confirmed death.

    The adjacent evidence JSON deliberately keeps the richer protocol context
    for diagnosis.  The readable report is limited to the three things a
    player can act on: HP/damage, casts around the death, and received healing.
    """

    capture = Path(str(evidence["capture"]))
    identity = evidence["identity"]
    health = evidence["health"]
    baseline = evidence["baseline"]
    actor_name = str(identity["display_name"])
    job = str(identity.get("job") or "未知职业")
    death_seconds = float(evidence["death_seconds"])
    previous = health.get("previous_positive")
    revived = health.get("next_positive")
    max_hp = int(baseline.get("max_hp") or 0)
    health_points = sorted(
        (dict(row) for row in health.get("points", ())),
        key=lambda row: (int(row.get("timestamp_ms") or 0),),
    )
    incoming_damage = sorted(
        (dict(row) for row in evidence.get("incoming_damage", ())),
        key=lambda row: int(row.get("timestamp_ms") or 0),
    )
    actor_casts = sorted(
        (dict(row) for row in evidence.get("actor_casts", ())),
        key=lambda row: int(row.get("timestamp_ms") or 0),
    )
    healing = sorted(
        (dict(row) for row in evidence.get("healing", ())),
        key=lambda row: int(row.get("timestamp_ms") or 0),
    )
    previous_cast = evidence.get("previous_cast")
    next_cast = evidence.get("next_cast")
    before_seconds = evidence.get("before_seconds")
    after_seconds = evidence.get("after_seconds")
    report_before_seconds = (
        DEFAULT_BEFORE_SECONDS if before_seconds is None else float(before_seconds)
    )
    report_after_seconds = (
        DEFAULT_AFTER_SECONDS if after_seconds is None else float(after_seconds)
    )

    def stage_text(relative_seconds: object) -> str:
        relative = float(relative_seconds or 0.0)
        if abs(relative) <= 0.001:
            return "死亡时"
        return "死亡前" if relative < 0 else "死亡后"

    def hp_change_text(row: dict[str, object]) -> str:
        value = row.get("hp_change")
        if value is None:
            return "—"
        amount = int(value)
        if amount > 0:
            return f"+{amount:,}"
        return f"{amount:,}"

    def hp_state_text(row: dict[str, object]) -> str:
        current_hp = int(row.get("current_hp") or 0)
        timestamp_ms = int(row.get("timestamp_ms") or 0)
        if current_hp <= 0:
            return "死亡"
        if revived and timestamp_ms == int(revived.get("timestamp_ms") or -1):
            return "复活/恢复正血"
        change = row.get("hp_change")
        if change is None:
            return "状态同步"
        if int(change) < 0:
            return "HP下降"
        if int(change) > 0:
            return "HP恢复"
        return "状态同步"

    before_healing = [
        row for row in healing if float(row.get("relative_seconds") or 0.0) <= 0.001
    ]
    after_healing = [
        row for row in healing if float(row.get("relative_seconds") or 0.0) > 0.001
    ]
    lines = [
        f"# 死亡报告 · {actor_name}（{job}）",
        "",
        f"- 抓包：`{capture.name}`",
        f"- 死亡时间：**{_clock(death_seconds)}**（抓包开始为 0 秒）",
        f"- 角色：**{actor_name}** / {job}",
        (
            f"- 观察范围：死亡前 {report_before_seconds:g}s"
            f" ～ 死亡后 {report_after_seconds:g}s"
        ),
        "",
        "## HP轨迹及受伤情况",
        "",
        f"- 最大 HP：{max_hp:,}（来源：`{baseline.get('max_hp_source') or '未知'}`）",
    ]
    if previous:
        percent_text = (
            f"{float(previous['percent']):.2f}%"
            if previous.get("percent") is not None
            else "比例未知"
        )
        lines.append(
            f"- 死亡前最后正血：{_clock(previous['seconds'])}，"
            f"{int(previous.get('current_hp') or 0):,} HP（{percent_text}），"
            f"距死亡 {death_seconds - float(previous['seconds']):.3f}s。"
        )
    else:
        lines.append("- 死亡前没有可用的正血 HP 快照。")
    if revived:
        lines.append(
            f"- 死亡后首次正血：{_clock(revived['seconds'])}，"
            f"{int(revived['current_hp']):,} HP，"
            f"距死亡 {float(revived['seconds']) - death_seconds:.3f}s。"
        )
    else:
        lines.append("- 抓包后续没有可用的正血 HP 快照。")
    lines.extend(
        [
            "",
            "### HP轨迹",
            "",
            _markdown_table(
                ["相对死亡", "时间", "HP", "变化", "HP%", "状态"],
                (
                    (
                        _delta_clock(float(row.get("relative_seconds") or 0.0)),
                        _clock(row.get("seconds")),
                        f"{int(row.get('current_hp') or 0):,}",
                        hp_change_text(row),
                        (
                            f"{float(row['percent']):.2f}%"
                            if row.get("percent") is not None
                            else "—"
                        ),
                        hp_state_text(row),
                    )
                    for row in health_points
                ),
            ),
            "",
            "### 死亡前受伤情况",
            "",
            _markdown_table(
                ["相对死亡", "时间", "生命损失", "来源", "技能/类型"],
                (
                    (
                        _delta_clock(float(row.get("relative_seconds") or 0.0)),
                        _clock(row.get("seconds")),
                        f"{int(row.get('amount') or 0):,}",
                        _safe_name(
                            row.get("source_name"),
                            row.get("job") or row.get("source_id") or "未知",
                        ),
                        row.get("skill_name") or row.get("skill_id") or "未知",
                    )
                    for row in incoming_damage
                    if float(row.get("relative_seconds") or 0.0) <= 0.001
                ),
            ),
            "",
            "## 死亡前后技能情况",
            "",
        ]
    )
    if previous_cast:
        lines.append(
            f"- 死亡前最后技能：{_clock(previous_cast['seconds'])} "
            f"{previous_cast.get('skill_name') or previous_cast.get('skill_id') or '未知技能'}"
            f"（{_delta_clock(float(previous_cast['relative_seconds']))}）。"
        )
    else:
        lines.append("- 死亡前没有可用的本人技能记录。")
    if next_cast:
        lines.append(
            f"- 死亡后首次技能：{_clock(next_cast['seconds'])} "
            f"{next_cast.get('skill_name') or next_cast.get('skill_id') or '未知技能'}"
            f"（{_delta_clock(float(next_cast['relative_seconds']))}）。"
        )
    else:
        lines.append("- 死亡后没有新的本人技能记录。")
    lines.extend(
        [
            _markdown_table(
                ["阶段", "相对死亡", "时间", "技能"],
                (
                    (
                        stage_text(row.get("relative_seconds")),
                        _delta_clock(float(row.get("relative_seconds") or 0.0)),
                        _clock(row.get("seconds")),
                        row.get("skill_name") or row.get("skill_id") or "未知",
                    )
                    for row in actor_casts
                ),
            ),
            "",
            "## 治疗情况",
            "",
            f"- 死亡前窗口：{len(before_healing)} 次有效治疗，合计 "
            f"{sum(max(0, int(row.get('amount') or 0)) for row in before_healing):,}。",
            f"- 死亡后窗口：{len(after_healing)} 次有效治疗，合计 "
            f"{sum(max(0, int(row.get('amount') or 0)) for row in after_healing):,}。",
            "",
            _markdown_table(
                ["阶段", "相对死亡", "时间", "来源", "技能", "有效治疗"],
                (
                    (
                        stage_text(row.get("relative_seconds")),
                        _delta_clock(float(row.get("relative_seconds") or 0.0)),
                        _clock(row.get("seconds")),
                        _safe_name(row.get("source_name"), row.get("job") or row.get("source_id") or "未知"),
                        row.get("skill_name") or row.get("skill_id") or "未知",
                        f"{int(row.get('amount') or 0):,}",
                    )
                    for row in healing
                ),
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _render_html(markdown_text: str, title: str) -> str:
    # Keep the standalone HTML dependency-free.  The Markdown file remains the
    # canonical report; this compact renderer is only for convenient opening.
    body: list[str] = []
    in_list = False
    in_table = False
    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.startswith("| "):
            cells = [escape(cell.strip()) for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            if not in_table:
                if in_list:
                    body.append("</ul>")
                    in_list = False
                body.append("<table>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            body.append("<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("# "):
            body.append(f"<h1>{escape(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("### "):
            body.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.startswith("> "):
            body.append(f"<blockquote>{escape(line[2:])}</blockquote>")
        elif re.match(r"^\d+\. ", line):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{escape(re.sub(r'^\d+\. ', '', line))}</li>")
        elif line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{escape(line[2:])}</li>")
        elif not line:
            if in_list:
                body.append("</ul>")
                in_list = False
        else:
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<p>{escape(line)}</p>")
    if in_table:
        body.append("</table>")
    if in_list:
        body.append("</ul>")
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f3f7f7;color:#173238}}
main{{max-width:1180px;margin:24px auto;padding:30px 36px;background:white;border-radius:16px;box-shadow:0 8px 30px #17323818}}
h1{{color:#087f83;margin-top:0}} h2{{margin-top:30px;border-left:5px solid #13a3a7;padding-left:12px}}
h3{{margin:22px 0 8px;color:#24545a}}
blockquote{{background:#edf8f7;border-left:4px solid #13a3a7;margin:16px 0;padding:12px 16px}}
table{{width:100%;border-collapse:collapse;margin:12px 0 20px;font-size:14px}} th{{background:#dff1f0;text-align:left}}
th,td{{border:1px solid #cbdcdc;padding:8px 10px;vertical-align:top}} tr:nth-child(even){{background:#f7fbfb}}
li{{margin:6px 0}} code{{background:#edf2f2;padding:2px 5px;border-radius:4px}}
</style></head><body><main>{body}</main></body></html>""".format(
        title=escape(title), body="\n".join(body)
    )


def _build_evidence(
    capture: Path,
    source_hash: str,
    snapshot: CombatSnapshot,
    aggregator: object,
    map_history: tuple[dict[str, object], ...],
    encounters: tuple[BossEncounter, ...],
    death: CombatEvent,
    ordinal: int,
    actor_ordinal: int,
    before_seconds: float,
    after_seconds: float,
) -> dict[str, object]:
    death_ms = int(death.timestamp_ms)
    death_seconds = death_ms / 1000.0
    actor_id = str(death.target_id or death.source_id)
    identity = _identity(snapshot, actor_id)
    identity["name"] = identity.get("name") or death.target_name
    identity["job"] = identity.get("job") or death.target_job
    identity["display_name"] = _safe_name(
        identity.get("name"), identity.get("job") or actor_id
    )
    baseline = _baseline(snapshot, actor_id)
    raw_health = _raw_health_rows(aggregator, actor_id)
    health = _health_context(
        raw_health,
        death_ms,
        before_seconds,
        after_seconds,
        int(baseline["max_hp"]),
    )
    start_ms = death_ms - round(before_seconds * 1000)
    end_ms = death_ms + round(after_seconds * 1000)
    incoming = [
        _event_row(row, death_ms)
        for row in snapshot.incoming_damage_events
        if row.target_id == actor_id and start_ms <= row.timestamp_ms <= death_ms
    ]
    healing = [
        _event_row(row, death_ms)
        for row in snapshot.combat_events
        if row.healing
        and row.target_id == actor_id
        and start_ms <= row.timestamp_ms <= end_ms
    ]
    casts = [
        _event_row(row, death_ms)
        for row in snapshot.skill_cast_events
        if row.source_id == actor_id and start_ms <= row.timestamp_ms <= end_ms
    ]
    all_actor_casts = sorted(
        (
            row
            for row in snapshot.skill_cast_events
            if row.source_id == actor_id
        ),
        key=lambda row: row.timestamp_ms,
    )
    previous_cast = next(
        (row for row in reversed(all_actor_casts) if row.timestamp_ms <= death_ms),
        None,
    )
    next_cast = next(
        (row for row in all_actor_casts if row.timestamp_ms > death_ms),
        None,
    )
    actions = [
        _event_row(row, death_ms)
        for row in snapshot.boss_action_events
        if death_ms - 3_000 <= row.timestamp_ms <= death_ms + 3_000
    ]
    clears = [
        _event_row(row, death_ms)
        for row in snapshot.player_buff_clear_events
        if death_ms - 5_000 <= row.timestamp_ms <= death_ms + 5_000
        and (
            not row.affected_target_ids or actor_id in row.affected_target_ids
        )
    ]
    buffs = _active_player_buffs(snapshot, actor_id, death_seconds)
    map_row = _map_at(map_history, death_seconds)
    active_encounters = _encounters_at(encounters, death_seconds)
    nearby_deaths = [
        {
            "timestamp_ms": row.timestamp_ms,
            "seconds": row.timestamp_ms / 1000.0,
            "relative_seconds": (row.timestamp_ms - death_ms) / 1000.0,
            "target_id": row.target_id,
            "target_name": row.target_name,
            "target_job": row.target_job,
        }
        for row in _death_events(snapshot)
        if row is not death and abs(row.timestamp_ms - death_ms) <= 5_000
    ]
    return {
        "schema": "dragon-nest-death-report-v3",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "capture": str(capture),
        "capture_size": capture.stat().st_size,
        "source_hash": source_hash,
        "recording_seconds": snapshot.elapsed_seconds,
        "death_ordinal": ordinal,
        "actor_death_ordinal": actor_ordinal,
        "death_timestamp_ms": death_ms,
        "death_seconds": death_seconds,
        "before_seconds": before_seconds,
        "after_seconds": after_seconds,
        "death_evidence": _event_row(death, death_ms),
        "identity": identity,
        "baseline": baseline,
        "map": map_row,
        "boss_encounters": [_jsonable(row) for row in active_encounters],
        "health": health,
        "incoming_damage": incoming,
        "healing": healing,
        "actor_casts": casts,
        "previous_cast": (
            _event_row(previous_cast, death_ms) if previous_cast is not None else None
        ),
        "next_cast": (
            _event_row(next_cast, death_ms) if next_cast is not None else None
        ),
        "boss_actions": actions,
        "active_buffs": buffs,
        "buff_clears": clears,
        "nearby_deaths": nearby_deaths,
    }


def _load_cached_aggregator(core: NativeSimulatorCore, capture: Path) -> object:
    profile_path = DATA_DIR / "live_dps_profiles" / "v576.json"
    try:
        known_players = ClientProfile.load(profile_path).known_players
    except (OSError, ValueError, KeyError):
        known_players = {}
    identity = replay_cache_identity(capture, DATA_DIR, known_players)
    aggregator = core._replay_disk_cache.load(identity)
    if aggregator is None:
        raise RuntimeError(f"完整解析缓存不存在：{capture}")
    return aggregator


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate one evidence-backed Markdown/HTML report per confirmed player death."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--before", type=float, default=DEFAULT_BEFORE_SECONDS)
    parser.add_argument("--after", type=float, default=DEFAULT_AFTER_SECONDS)
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    manifest: list[dict[str, object]] = []
    report_index = 0
    for capture_value in args.captures:
        capture = capture_value.expanduser().resolve()
        replay = core.load_replay(str(capture), lambda *_args: None)
        source_hash = str(replay["sourceHash"])
        snapshot = core._replays[source_hash]
        aggregator = _load_cached_aggregator(core, capture)
        map_history = tuple(core._replay_map_history.get(source_hash, ()))
        encounters = tuple(
            core._score_encounters_by_replay.get(source_hash, ())
            or BossRecognizer().recognize_snapshot(snapshot)
        )
        per_actor_count: dict[str, int] = {}
        for death in _death_events(snapshot):
            report_index += 1
            actor_id = str(death.target_id or death.source_id)
            per_actor_count[actor_id] = per_actor_count.get(actor_id, 0) + 1
            evidence = _build_evidence(
                capture,
                source_hash,
                snapshot,
                aggregator,
                map_history,
                encounters,
                death,
                report_index,
                per_actor_count[actor_id],
                max(0.0, args.before),
                max(0.0, args.after),
            )
            identity = evidence["identity"]
            stem = "_".join(
                (
                    f"{report_index:02d}",
                    _file_component(capture.stem),
                    _file_component(_clock(evidence["death_seconds"]).replace(":", "m")),
                    _file_component(str(identity["display_name"])),
                    _file_component(str(identity.get("job") or "未知职业")),
                )
            )
            evidence_path = output / f"{stem}.evidence.json"
            markdown_path = output / f"{stem}.md"
            html_path = output / f"{stem}.html"
            evidence["evidence_filename"] = evidence_path.name
            markdown = _render_report(evidence)
            evidence_path.write_text(
                json.dumps(_jsonable(evidence), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            markdown_path.write_text(markdown, encoding="utf-8")
            html_path.write_text(
                _render_html(markdown, f"死亡报告 · {identity['display_name']}"),
                encoding="utf-8",
            )
            manifest.append(
                {
                    "capture": capture.name,
                    "death_seconds": evidence["death_seconds"],
                    "time": _clock(evidence["death_seconds"]),
                    "actor_id": actor_id,
                    "name": identity["display_name"],
                    "job": identity.get("job") or "未知职业",
                    "map": (evidence.get("map") or {}).get("nest_name")
                    or (evidence.get("map") or {}).get("map_name")
                    or "未识别",
                    "markdown": markdown_path.name,
                    "html": html_path.name,
                    "evidence": evidence_path.name,
                }
            )

    index_lines = [
        "# 最近抓包 · 独立死亡报告索引",
        "",
        "> 每次死亡独立成篇；正文只展示 HP与受伤、死亡前后技能、收到的治疗。",
        "",
        _markdown_table(
            ["#", "抓包", "时间", "角色", "职业", "报告"],
            (
                (
                    index + 1,
                    row["capture"],
                    row["time"],
                    row["name"],
                    row["job"],
                    f"[{row['markdown']}]({row['markdown']}) / [HTML]({row['html']})",
                )
                for index, row in enumerate(manifest)
            ),
        ),
        "",
        f"共生成 **{len(manifest)}** 份独立死亡报告。",
        "",
    ]
    index_markdown = "\n".join(index_lines)
    (output / "README.md").write_text(index_markdown, encoding="utf-8")
    (output / "index.html").write_text(
        _render_html(index_markdown, "最近抓包 · 独立死亡报告索引"),
        encoding="utf-8",
    )
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"reports": len(manifest), "output": str(output)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
