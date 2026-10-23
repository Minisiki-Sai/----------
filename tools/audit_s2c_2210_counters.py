"""Read-only semantic audit for the 108-byte S2C 22 10 message.

The message exposes a job code and fixed-width UTF-8 character name followed
by several numeric fields.  This tool compares every numeric field with the
same capture's decoded ACT combat facts.  It deliberately does not update any
production parser: a field is named only when its increments and terminal
values agree with independently decoded UDP evidence.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
    PartyMemberEvent,
    PlayerHealthEvent,
    PlayerLifecycleEvent,
)
from dragon_nest_builder.live_dps.profile import ClientProfile  # noqa: E402
from tools.audit_full_tcp_key_table import (  # noqa: E402
    envelope_direction,
    full_table_decode,
    iter_logical_packets,
    load_full_table,
)


FIELD_LAYOUT: tuple[tuple[str, int, int], ...] = (
    ("u64_52", 52, 8),
    ("u64_60", 60, 8),
    ("u64_68", 68, 8),
    ("u64_76", 76, 8),
    ("u64_84", 84, 8),
    ("u64_92", 92, 8),
    ("u64_100", 100, 8),
)

METRICS = (
    "outgoing_damage",
    "outgoing_records",
    "real_hits",
    "max_hit",
    "elapsed_dps",
    "incoming_damage",
    "incoming_records",
    "hp_state_loss",
    "hp_state_gain",
    "healing",
    "healing_records",
)


@dataclass(frozen=True, slots=True)
class Row2210:
    timestamp_seconds: float
    elapsed_ms: int
    job_code: int
    name: str
    values: dict[str, int]


def _decode_name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="strict").strip()


def _parse_capture(
    path: Path,
    keys: dict[int, tuple[int, int, int, int]],
) -> tuple[list[Row2210], float | None]:
    pending: list[tuple[float, int, str, dict[str, int]]] = []
    capture_origin: float | None = None
    for packet in iter_logical_packets(path):
        capture_origin = (
            packet.timestamp_seconds
            if capture_origin is None
            else min(capture_origin, packet.timestamp_seconds)
        )
        if packet.transport.upper() != "TCP":
            continue
        direction = envelope_direction(packet)
        if direction != "server_to_client":
            continue
        decoded = full_table_decode(packet.payload, direction, keys)
        if decoded is None:
            continue
        plaintext = decoded[0]
        if len(plaintext) != 108 or plaintext[2:4] != b"\x22\x10":
            continue
        try:
            name = _decode_name(plaintext[8:52])
        except UnicodeDecodeError:
            continue
        if not name:
            continue
        values = {
            label: int.from_bytes(plaintext[offset : offset + size], "little")
            for label, offset, size in FIELD_LAYOUT
        }
        pending.append(
            (
                packet.timestamp_seconds,
                int.from_bytes(plaintext[4:8], "little"),
                name,
                values,
            )
        )
    if capture_origin is None:
        return [], None
    rows = [
        Row2210(
            timestamp_seconds=timestamp,
            elapsed_ms=max(0, round((timestamp - capture_origin) * 1000.0)),
            job_code=job_code,
            name=name,
            values=values,
        )
        for timestamp, job_code, name, values in pending
    ]
    return rows, capture_origin


def _known_players() -> dict[str, dict[str, str]]:
    try:
        return ClientProfile.load(
            DATA_DIR / "live_dps_profiles" / "v576.json"
        ).known_players
    except (OSError, ValueError, KeyError):
        return {}


def _normalize_name(value: str) -> str:
    return str(value or "").strip().casefold()


def _event_name(event: CombatEvent, metric: str) -> str:
    if metric.startswith("incoming"):
        return str(event.target_name or "").strip()
    return str(event.source_name or "").strip()


def _metric_updates(
    events: Iterable[Any],
    evidence_events: Iterable[Any] = (),
) -> dict[str, dict[str, list[tuple[int, int]]]]:
    events = list(events)
    evidence_events = list(evidence_events)
    actor_names: dict[str, str] = {}
    for event in [*evidence_events, *events]:
        if isinstance(event, PartyMemberEvent):
            if event.actor_id and event.name:
                actor_names[str(event.actor_id).strip().casefold()] = event.name
        elif isinstance(event, PlayerLifecycleEvent):
            if event.target_id and event.target_name:
                actor_names[str(event.target_id).strip().casefold()] = event.target_name
        elif isinstance(event, PlayerHealthEvent):
            if event.target_id and event.target_name:
                actor_names[str(event.target_id).strip().casefold()] = event.target_name
        elif isinstance(event, CombatEvent):
            if event.source_id and event.source_name:
                actor_names[str(event.source_id).strip().casefold()] = event.source_name
            if event.target_id and event.target_name:
                actor_names[str(event.target_id).strip().casefold()] = event.target_name

    raw: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in events:
        if not isinstance(event, CombatEvent):
            continue
        if event.healing:
            name = _event_name(event, "healing")
            if name:
                key = _normalize_name(name)
                raw[key]["healing"].append((event.timestamp_ms, int(event.amount)))
                raw[key]["healing_records"].append((event.timestamp_ms, 1))
            continue
        if event.source_id == "__incoming_damage__" or str(
            event.attribution_method or ""
        ).startswith("incoming_"):
            name = _event_name(event, "incoming_damage") or actor_names.get(
                str(event.target_id or "").strip().casefold(), ""
            )
            if name:
                key = _normalize_name(name)
                raw[key]["incoming_damage"].append(
                    (event.timestamp_ms, int(event.amount))
                )
                raw[key]["incoming_records"].append((event.timestamp_ms, 1))
            continue
        name = _event_name(event, "outgoing_damage")
        if not name:
            continue
        key = _normalize_name(name)
        raw[key]["outgoing_damage"].append((event.timestamp_ms, int(event.amount)))
        raw[key]["outgoing_records"].append((event.timestamp_ms, 1))
        raw[key]["max_hit"].append((event.timestamp_ms, int(event.amount)))
        if event.has_real_hit_evidence is True:
            raw[key]["real_hits"].append((event.timestamp_ms, 1))

    health_by_target: dict[str, list[PlayerHealthEvent]] = defaultdict(list)
    for event in evidence_events:
        if (
            isinstance(event, PlayerHealthEvent)
            and event.current_hp >= 0
            and event.packet_kind == "s2c-udp-player-hp-state-086f-v576"
        ):
            health_by_target[str(event.target_id).strip().casefold()].append(event)
    for target_id, health_events in health_by_target.items():
        ordered = sorted(
            health_events,
            key=lambda event: (event.timestamp_ms, event.record_offset),
        )
        name = next(
            (event.target_name for event in reversed(ordered) if event.target_name),
            actor_names.get(target_id, ""),
        )
        if not name:
            continue
        key = _normalize_name(name)
        for previous, current in zip(ordered, ordered[1:]):
            if current.current_hp < previous.current_hp:
                raw[key]["hp_state_loss"].append(
                    (
                        current.timestamp_ms,
                        previous.current_hp - current.current_hp,
                    )
                )
            elif current.current_hp > previous.current_hp:
                raw[key]["hp_state_gain"].append(
                    (
                        current.timestamp_ms,
                        current.current_hp - previous.current_hp,
                    )
                )

    result: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for name, by_metric in raw.items():
        result[name] = {}
        for metric in METRICS:
            running = 0
            series: list[tuple[int, int]] = []
            if metric == "elapsed_dps":
                continue
            for timestamp_ms, increment in sorted(by_metric.get(metric, ())):
                if metric == "max_hit":
                    running = max(running, increment)
                else:
                    running += increment
                if series and series[-1][0] == timestamp_ms:
                    series[-1] = (timestamp_ms, running)
                else:
                    series.append((timestamp_ms, running))
            result[name][metric] = series
        result[name]["elapsed_dps"] = [
            (
                timestamp_ms,
                round(total / max(0.001, timestamp_ms / 1000.0)),
            )
            for timestamp_ms, total in result[name]["outgoing_damage"]
        ]
    return result


def _sample(series: list[tuple[int, int]], timestamp_ms: int) -> int:
    if not series:
        return 0
    index = bisect_right(series, (timestamp_ms, 2**127)) - 1
    return series[index][1] if index >= 0 else 0


def _pearson(left: list[int], right: list[int]) -> float | None:
    if len(left) < 3 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    try:
        value = statistics.correlation(left, right)
    except (statistics.StatisticsError, ValueError):
        return None
    return round(float(value), 6) if math.isfinite(value) else None


def _increments(values: list[int]) -> list[int]:
    return [current - previous for previous, current in zip(values, values[1:])]


def _series_summary(values: list[int]) -> dict[str, Any]:
    deltas = _increments(values)
    return {
        "first": values[0] if values else 0,
        "last": values[-1] if values else 0,
        "min": min(values, default=0),
        "max": max(values, default=0),
        "unique": len(set(values)),
        "positive_steps": sum(value > 0 for value in deltas),
        "zero_steps": sum(value == 0 for value in deltas),
        "negative_steps": sum(value < 0 for value in deltas),
        "largest_positive_step": max((value for value in deltas if value > 0), default=0),
        "largest_negative_step": min((value for value in deltas if value < 0), default=0),
    }


def _player_report(
    name: str,
    rows: list[Row2210],
    metrics: dict[str, list[tuple[int, int]]],
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda item: item.elapsed_ms)
    sampled = {
        metric: [_sample(metrics.get(metric, []), row.elapsed_ms) for row in rows]
        for metric in METRICS
    }
    fields: dict[str, Any] = {}
    for field, _offset, _size in FIELD_LAYOUT:
        values = [row.values[field] for row in rows]
        field_deltas = _increments(values)
        correlations: dict[str, Any] = {}
        for metric in METRICS:
            metric_values = sampled[metric]
            metric_deltas = _increments(metric_values)
            correlations[metric] = {
                "cumulative_pearson": _pearson(values, metric_values),
                "increment_pearson": _pearson(field_deltas, metric_deltas),
                "field_terminal_delta": values[-1] - values[0] if values else 0,
                "act_terminal_delta": (
                    metric_values[-1] - metric_values[0] if metric_values else 0
                ),
                "exact_increment_steps": sum(
                    left == right and left != 0
                    for left, right in zip(field_deltas, metric_deltas)
                ),
                "coincident_nonzero_steps": sum(
                    left != 0 and right != 0
                    for left, right in zip(field_deltas, metric_deltas)
                ),
            }
        fields[field] = {
            "summary": _series_summary(values),
            "correlations": correlations,
        }
    return {
        "name": name,
        "job_codes": dict(Counter(row.job_code for row in rows).most_common()),
        "message_count": len(rows),
        "first_elapsed_ms": rows[0].elapsed_ms if rows else 0,
        "last_elapsed_ms": rows[-1].elapsed_ms if rows else 0,
        "act_terminals": {
            metric: (series[-1][1] if series else 0)
            for metric, series in metrics.items()
        },
        "fields": fields,
    }


def audit_capture(
    path: Path,
    keys: dict[int, tuple[int, int, int, int]],
) -> dict[str, Any]:
    rows, wire_origin = _parse_capture(path, keys)
    source = V576DiagnosticReplaySource(path, DATA_DIR, _known_players())
    try:
        events = source.poll()
        replay_origin = source.capture_origin_timestamp_seconds
    finally:
        source.close()

    # Both readers use the first non-sidecar physical frame as zero.  Keep the
    # measured difference in the report so a future capture-format change
    # cannot silently turn a timing shift into a field correlation.
    origin_delta_ms = (
        round((float(wire_origin) - float(replay_origin)) * 1000.0)
        if wire_origin is not None and replay_origin is not None
        else None
    )
    if replay_origin is not None:
        rows = [
            Row2210(
                timestamp_seconds=row.timestamp_seconds,
                elapsed_ms=max(
                    0,
                    round(
                        (row.timestamp_seconds - float(replay_origin)) * 1000.0
                    ),
                ),
                job_code=row.job_code,
                name=row.name,
                values=row.values,
            )
            for row in rows
        ]
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    factual_events = list(snapshot.combat_events) + list(
        snapshot.incoming_damage_events
    )

    by_name: dict[str, list[Row2210]] = defaultdict(list)
    display_names: dict[str, str] = {}
    for row in rows:
        key = _normalize_name(row.name)
        by_name[key].append(row)
        display_names.setdefault(key, row.name)
    metrics = _metric_updates(factual_events, events)
    players = [
        _player_report(
            display_names[key],
            by_name[key],
            metrics.get(key, {metric: [] for metric in METRICS}),
        )
        for key in sorted(by_name)
    ]
    return {
        "capture": str(path),
        "message_count": len(rows),
        "player_count": len(players),
        "wire_origin_seconds": wire_origin,
        "replay_origin_seconds": replay_origin,
        "origin_delta_ms": origin_delta_ms,
        "decoded_event_count": len(events),
        "source_event_types": dict(
            Counter(type(event).__name__ for event in events).most_common()
        ),
        "snapshot_incoming_event_count": len(snapshot.incoming_damage_events),
        "snapshot_player_incoming_damage": {
            row.name: int(row.incoming_damage) for row in snapshot.players
        },
        "players": players,
    }


def _aggregate(reports: list[dict[str, Any]]) -> dict[str, Any]:
    field_metric: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"cumulative": [], "increment": []}
    )
    for report in reports:
        for player in report["players"]:
            for field, field_report in player["fields"].items():
                for metric, correlation in field_report["correlations"].items():
                    cumulative = correlation["cumulative_pearson"]
                    increment = correlation["increment_pearson"]
                    if cumulative is not None:
                        field_metric[(field, metric)]["cumulative"].append(cumulative)
                    if increment is not None:
                        field_metric[(field, metric)]["increment"].append(increment)
    rankings = []
    for (field, metric), values in field_metric.items():
        rankings.append(
            {
                "field": field,
                "metric": metric,
                "players_with_cumulative_correlation": len(values["cumulative"]),
                "median_cumulative_pearson": (
                    round(statistics.median(values["cumulative"]), 6)
                    if values["cumulative"]
                    else None
                ),
                "players_with_increment_correlation": len(values["increment"]),
                "median_increment_pearson": (
                    round(statistics.median(values["increment"]), 6)
                    if values["increment"]
                    else None
                ),
            }
        )
    rankings.sort(
        key=lambda row: (
            -abs(row["median_increment_pearson"] or 0.0),
            -abs(row["median_cumulative_pearson"] or 0.0),
            row["field"],
            row["metric"],
        )
    )
    return {
        "capture_count": len(reports),
        "message_count": sum(report["message_count"] for report in reports),
        "rankings": rankings,
    }


def _markdown(document: dict[str, Any]) -> str:
    lines = [
        "# S2C 22 10 / 108 字节字段对账",
        "",
        "该报告只做抓包与 ACT 独立解码结果的只读比较，不修改生产解析器。",
        "",
        "## 跨抓包相关性排名",
        "",
        "| 字段 | ACT 指标 | 累计相关玩家数 | 累计相关中位数 | 增量相关玩家数 | 增量相关中位数 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in document["aggregate"]["rankings"]:
        cumulative = row["median_cumulative_pearson"]
        increment = row["median_increment_pearson"]
        lines.append(
            f"| {row['field']} | {row['metric']} | "
            f"{row['players_with_cumulative_correlation']} | "
            f"{'' if cumulative is None else cumulative} | "
            f"{row['players_with_increment_correlation']} | "
            f"{'' if increment is None else increment} |"
        )
    lines.extend(["", "## 抓包摘要", ""])
    for report in document["captures"]:
        lines.append(
            f"- `{Path(report['capture']).name}`：22 10 共 "
            f"{report['message_count']:,} 条，{report['player_count']} 个名字，"
            f"时间零点差 {report['origin_delta_ms']} ms。"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--key-table",
        type=Path,
        default=ROOT / "outputs/client_unpack_with_keys/tcp_key_table_v576.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/s2c_2210_counter_audit.json",
    )
    args = parser.parse_args()
    keys = load_full_table(args.key_table.resolve())
    reports = [audit_capture(path.resolve(), keys) for path in args.captures]
    document = {
        "schema": "dragon-nest-s2c-2210-counter-audit/v1",
        "field_layout": [
            {"name": name, "offset": offset, "size": size}
            for name, offset, size in FIELD_LAYOUT
        ],
        "captures": reports,
        "aggregate": _aggregate(reports),
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output.with_suffix(".md").write_text(_markdown(document), encoding="utf-8")
    print(json.dumps(document["aggregate"], ensure_ascii=False, indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
