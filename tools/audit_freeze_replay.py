from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import statistics
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def rounded(value: object, digits: int = 6) -> float:
    try:
        return round(float(value or 0.0), digits)
    except (TypeError, ValueError):
        return 0.0


def merge_intervals(rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(rows):
        if end <= start:
            continue
        if not merged or start > merged[-1][1] + 1e-9:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def freeze_group(row: dict[str, object]) -> str:
    job = str(row.get("job") or "")
    skill = str(row.get("skill_name") or "").replace("EX", "")
    if job in {"炼金圣士", "药剂师"} and skill in {"冰光束", "寒冰戳刺"}:
        return "炼金冰冻"
    return skill


def packet_natural_layer_intervals(
    events: list[dict[str, object]], target_ids: set[str]
) -> dict[int, list[tuple[float, float]]]:
    by_target_group: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for row in events:
        if row.get("operation", "apply") == "remove":
            continue
        target_id = str(row.get("target_id") or "")
        if target_id not in target_ids:
            continue
        start = rounded(row.get("timestamp_seconds"), 6)
        end = start + rounded(row.get("duration_seconds"), 6)
        if end > start:
            by_target_group[(target_id, freeze_group(row))].append((start, end))

    result: dict[int, list[tuple[float, float]]] = {1: [], 2: []}
    target_groups: dict[str, list[list[tuple[float, float]]]] = defaultdict(list)
    for (target_id, _group), intervals in by_target_group.items():
        target_groups[target_id].append(merge_intervals(intervals))
    for groups in target_groups.values():
        changes: dict[float, int] = defaultdict(int)
        for intervals in groups:
            for start, end in intervals:
                changes[start] += 1
                changes[end] -= 1
        active = 0
        previous: float | None = None
        for timestamp in sorted(changes):
            if previous is not None and timestamp > previous:
                if active >= 1:
                    result[1].append((previous, timestamp))
                if active >= 2:
                    result[2].append((previous, timestamp))
            active += changes[timestamp]
            previous = timestamp
    return {layer: merge_intervals(rows) for layer, rows in result.items()}


def covered_seconds(
    intervals: list[tuple[float, float]], output_windows: list[list[float]]
) -> float:
    return sum(
        max(0.0, min(end, window_end) - max(start, window_start))
        for start, end in intervals
        for window_start, window_end in output_windows
    )


def audit_one(core: NativeSimulatorCore, path: Path) -> dict[str, object]:
    progress_state = {"value": 0, "message": ""}

    def progress(value: int, message: str) -> None:
        progress_state.update(value=int(value), message=str(message))

    payload = core.load_replay(str(path.resolve()), progress)
    players = [
        {
            "source_id": row.get("source_id", ""),
            "name": row.get("name", ""),
            "job": row.get("job", ""),
        }
        for row in payload.get("players", ())
        if isinstance(row, dict)
    ]
    freeze_events = [
        row
        for row in payload.get("boss_effect_events", ())
        if isinstance(row, dict) and row.get("key") == "冰冻层"
    ]
    freeze_tracks = [
        row
        for row in payload.get("boss_effects", ())
        if isinstance(row, dict) and row.get("key") == "冰冻层"
    ]

    grouped_events: dict[tuple[str, ...], list[dict[str, object]]] = defaultdict(list)
    for row in freeze_events:
        grouped_events[
            (
                str(row.get("job") or ""),
                str(row.get("source_id") or ""),
                str(row.get("skill_id") or ""),
                str(row.get("skill_name") or ""),
            )
        ].append(row)

    applications = []
    for (job, source_id, skill_id, skill_name), rows in sorted(grouped_events.items()):
        applies = [row for row in rows if row.get("operation", "apply") != "remove"]
        removes = [row for row in rows if row.get("operation") == "remove"]
        durations = [rounded(row.get("duration_seconds"), 3) for row in applies]
        applications.append(
            {
                "job": job,
                "source_id": source_id,
                "skill_id": skill_id,
                "skill": skill_name,
                "apply_count": len(applies),
                "remove_count": len(removes),
                "inferred_hit_shatter_count": sum(
                    "Hit 碎冰" in str(row.get("label") or "") for row in removes
                ),
                "duration_seconds": {
                    "min": min(durations, default=0.0),
                    "max": max(durations, default=0.0),
                    "values": sorted(set(durations)),
                },
                "duration_sources": dict(
                    Counter(str(row.get("duration_source") or "") for row in applies)
                ),
                "value_sources": dict(
                    Counter(str(row.get("value_source") or "") for row in applies)
                ),
                "effect_codes": sorted(
                    {str(row.get("effect_code") or "") for row in applies}
                ),
                "parameters": sorted(
                    {str(row.get("parameters") or "") for row in applies}
                ),
                "targets": sorted({str(row.get("target_id") or "") for row in rows}),
                "first_apply": min(
                    (rounded(row.get("timestamp_seconds"), 3) for row in applies),
                    default=0.0,
                ),
                "last_apply": max(
                    (rounded(row.get("timestamp_seconds"), 3) for row in applies),
                    default=0.0,
                ),
            }
        )

    tracks = []
    for row in freeze_tracks:
        intervals = [
            [rounded(interval[0], 3), rounded(interval[1], 3)]
            for interval in row.get("intervals", ())
            if isinstance(interval, (list, tuple)) and len(interval) >= 2
        ]
        tracks.append(
            {
                "target_id": row.get("target_id", ""),
                "target_name": row.get("target_name", ""),
                "job": row.get("job", ""),
                "source_id": row.get("source_id", ""),
                "skill_id": row.get("skill_id", ""),
                "skill": row.get("skill_name", ""),
                "refresh_count": row.get("refresh_count", 0),
                "uptime_seconds": rounded(row.get("uptime_seconds"), 3),
                "coverage": rounded(row.get("coverage")),
                "duration_source": row.get("duration_source", ""),
                "intervals": intervals,
            }
        )

    encounters = []
    for view in payload.get("boss_effect_views", ()):
        if not isinstance(view, dict):
            continue
        layer_rows = {
            int(row.get("layer", 0)): row
            for row in view.get("freeze_layers", ())
            if isinstance(row, dict)
        }
        one = layer_rows.get(1, {})
        two = layer_rows.get(2, {})
        output_windows = view.get("output_windows", [])
        natural = packet_natural_layer_intervals(
            freeze_events,
            {str(value) for value in view.get("target_ids", ())},
        )
        natural_one_seconds = covered_seconds(natural[1], output_windows)
        natural_two_seconds = covered_seconds(natural[2], output_windows)
        output_seconds = rounded(view.get("output_seconds"), 3)
        target_ids = {str(value) for value in view.get("target_ids", ())}
        encounter_freeze_events = [
            row for row in freeze_events
            if str(row.get("target_id") or "") in target_ids
        ]
        last_apply: dict[tuple[str, str, str], float] = {}
        shatter_lifetimes: list[float] = []
        explicit_removals = 0
        for row in sorted(
            encounter_freeze_events,
            key=lambda item: rounded(item.get("timestamp_seconds"), 6),
        ):
            key = (
                str(row.get("target_id") or ""),
                str(row.get("source_id") or ""),
                str(row.get("skill_id") or ""),
            )
            timestamp = rounded(row.get("timestamp_seconds"), 6)
            if row.get("operation", "apply") != "remove":
                last_apply[key] = timestamp
                continue
            if "Hit 碎冰" in str(row.get("label") or ""):
                if key in last_apply:
                    shatter_lifetimes.append(timestamp - last_apply[key])
            else:
                explicit_removals += 1
        real_hits = [
            row for row in payload.get("combat_events", ())
            if isinstance(row, dict)
            and str(row.get("target_id") or "") in target_ids
            and bool(row.get("has_real_hit_evidence"))
        ]
        encounters.append(
            {
                "name": view.get("name", ""),
                "target_ids": view.get("target_ids", []),
                "output_seconds": output_seconds,
                "one_layer_covered_seconds": rounded(one.get("covered_seconds"), 3),
                "one_layer_coverage": rounded(one.get("coverage")),
                "two_layer_covered_seconds": rounded(two.get("covered_seconds"), 3),
                "two_layer_coverage": rounded(two.get("coverage")),
                "packet_natural_one_layer_covered_seconds": rounded(
                    natural_one_seconds, 3
                ),
                "packet_natural_one_layer_coverage": rounded(
                    natural_one_seconds / output_seconds
                    if output_seconds > 1e-9 else 0.0
                ),
                "packet_natural_two_layer_covered_seconds": rounded(
                    natural_two_seconds, 3
                ),
                "packet_natural_two_layer_coverage": rounded(
                    natural_two_seconds / output_seconds
                    if output_seconds > 1e-9 else 0.0
                ),
                "two_layer_loss_from_inferred_shatter_seconds": rounded(
                    natural_two_seconds - rounded(two.get("covered_seconds")), 3
                ),
                "freeze_apply_events": sum(
                    row.get("operation", "apply") != "remove"
                    for row in encounter_freeze_events
                ),
                "inferred_hit_shatters": len(shatter_lifetimes),
                "server_explicit_removals": explicit_removals,
                "shatter_seconds_after_last_refresh": {
                    "min": rounded(min(shatter_lifetimes, default=0.0), 3),
                    "median": rounded(
                        statistics.median(shatter_lifetimes)
                        if shatter_lifetimes else 0.0,
                        3,
                    ),
                    "max": rounded(max(shatter_lifetimes, default=0.0), 3),
                },
                "real_hit_events": len(real_hits),
                "real_hits_per_output_second": rounded(
                    len(real_hits) / output_seconds
                    if output_seconds > 1e-9 else 0.0,
                    3,
                ),
                "one_layer_intervals": one.get("intervals", []),
                "two_layer_intervals": two.get("intervals", []),
            }
        )

    return {
        "capture": str(path.resolve()),
        "source_hash": payload.get("sourceHash", ""),
        "version_cache": payload.get("versionCache", {}),
        "elapsed_seconds": rounded(payload.get("elapsed_seconds"), 3),
        "players": players,
        "has_ice_spirit": any(row["job"] == "冰灵" for row in players),
        "has_alchemist": any(row["job"] == "炼金圣士" for row in players),
        "freeze_event_count": len(freeze_events),
        "freeze_track_count": len(freeze_tracks),
        "applications": applications,
        "tracks": tracks,
        "encounters": encounters,
        "progress": progress_state,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    report = {"captures": [audit_one(core, path) for path in args.captures]}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
