from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def _number(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1] + 1e-9:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _clip(
    intervals: list[tuple[float, float]], windows: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    return _merge(
        [
            (max(start, left), min(end, right))
            for start, end in intervals
            for left, right in windows
            if min(end, right) > max(start, left)
        ]
    )


def _layer_intervals(
    tracks: list[dict[str, object]],
    target_ids: set[str],
    jobs: set[str] | None = None,
) -> dict[int, list[tuple[float, float]]]:
    changes: dict[float, int] = defaultdict(int)
    for row in tracks:
        if str(row.get("target_id") or "") not in target_ids:
            continue
        row_jobs = {
            part.strip() for part in str(row.get("job") or "").split("/") if part.strip()
        }
        if jobs is not None and not row_jobs.intersection(jobs):
            continue
        for value in row.get("intervals", ()):
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            start, end = _number(value[0]), _number(value[1])
            if end > start:
                changes[start] += 1
                changes[end] -= 1

    layers: dict[int, list[tuple[float, float]]] = {1: [], 2: []}
    active = 0
    previous: float | None = None
    for timestamp in sorted(changes):
        if previous is not None and timestamp > previous:
            if active >= 1:
                layers[1].append((previous, timestamp))
            if active >= 2:
                layers[2].append((previous, timestamp))
        active += changes[timestamp]
        previous = timestamp
    return {layer: _merge(intervals) for layer, intervals in layers.items()}


def _event_layer_intervals(
    events: list[dict[str, object]],
    target_ids: set[str],
    jobs: set[str],
) -> dict[int, list[tuple[float, float]]]:
    active: dict[tuple[str, str], list[float]] = {}
    completed: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    selected = sorted(
        (
            row
            for row in events
            if str(row.get("target_id") or "") in target_ids
            and str(row.get("job") or "") in jobs
        ),
        key=lambda row: _number(row.get("timestamp_seconds")),
    )
    for row in selected:
        timestamp = _number(row.get("timestamp_seconds"))
        key = (
            str(row.get("target_id") or ""),
            str(row.get("skill_id") or row.get("skill_name") or ""),
        )
        current = active.get(key)
        if current is not None and timestamp >= current[1] - 1e-9:
            completed[key].append((current[0], current[1]))
            active.pop(key, None)
            current = None
        if row.get("operation", "apply") == "remove":
            if current is not None:
                completed[key].append((current[0], min(timestamp, current[1])))
                active.pop(key, None)
            continue
        duration = _number(row.get("duration_seconds"))
        if duration <= 0:
            continue
        if current is None:
            active[key] = [timestamp, timestamp + duration]
        else:
            current[1] = timestamp + duration
    for key, current in active.items():
        completed[key].append((current[0], current[1]))

    changes: dict[float, int] = defaultdict(int)
    for intervals in completed.values():
        for start, end in _merge(intervals):
            if end > start:
                changes[start] += 1
                changes[end] -= 1
    layers: dict[int, list[tuple[float, float]]] = {1: [], 2: []}
    count = 0
    previous: float | None = None
    for timestamp in sorted(changes):
        if previous is not None and timestamp > previous:
            if count >= 1:
                layers[1].append((previous, timestamp))
            if count >= 2:
                layers[2].append((previous, timestamp))
        count += changes[timestamp]
        previous = timestamp
    return {layer: _merge(intervals) for layer, intervals in layers.items()}


def _seconds(intervals: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in intervals)


def _state_blocks(
    one: list[tuple[float, float]],
    two: list[tuple[float, float]],
    windows: list[tuple[float, float]],
    block_seconds: float,
) -> list[tuple[float, float, float]]:
    blocks: list[tuple[float, float, float]] = []
    for window_start, window_end in windows:
        cursor = window_start
        while cursor < window_end - 1e-9:
            end = min(cursor + block_seconds, window_end)
            window = [(cursor, end)]
            one_seconds = _seconds(_clip(one, window))
            two_seconds = _seconds(_clip(two, window))
            blocks.append((end - cursor, one_seconds, two_seconds))
            cursor = end
    return blocks


def _bootstrap(
    blocks: list[tuple[float, float, float]],
    fight_seconds: float,
    iterations: int,
    seed: int,
) -> dict[str, float]:
    rng = random.Random(seed)
    results: list[float] = []
    for _ in range(iterations):
        elapsed = 0.0
        covered = 0.0
        while elapsed < fight_seconds - 1e-9:
            length, _one, two = rng.choice(blocks)
            used = min(length, fight_seconds - elapsed)
            covered += two * used / length
            elapsed += used
        results.append(covered / fight_seconds)
    results.sort()

    def percentile(fraction: float) -> float:
        return results[round((len(results) - 1) * fraction)]

    return {
        "mean": statistics.fmean(results),
        "p10": percentile(0.10),
        "p50": percentile(0.50),
        "p90": percentile(0.90),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--boss", default="天堂龙灾·佩达")
    parser.add_argument("--fight-seconds", type=float, default=300.0)
    parser.add_argument("--block-seconds", type=float, default=10.0)
    parser.add_argument("--iterations", type=int, default=50_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    captures: list[dict[str, object]] = []
    pooled_blocks: list[tuple[float, float, float]] = []
    ice_only_blocks: list[tuple[float, float, float]] = []
    totals = {"output": 0.0, "one": 0.0, "two": 0.0, "hits": 0.0}
    ice_only_totals = {"output": 0.0, "one": 0.0, "two": 0.0}

    for path in args.captures:
        payload = core.load_replay(str(path.resolve()), lambda _value, _message: None)
        tracks = [
            row
            for row in payload.get("boss_effects", ())
            if isinstance(row, dict) and row.get("key") == "冰冻层"
        ]
        freeze_events = [
            row
            for row in payload.get("boss_effect_events", ())
            if isinstance(row, dict) and row.get("key") == "冰冻层"
        ]
        view = next(
            row
            for row in payload.get("boss_effect_views", ())
            if isinstance(row, dict) and row.get("name") == args.boss
        )
        target_ids = {str(value) for value in view.get("target_ids", ())}
        windows = [
            (_number(value[0]), _number(value[1]))
            for value in view.get("output_windows", ())
            if isinstance(value, (list, tuple)) and len(value) >= 2
        ]
        layer_rows = {
            int(row.get("layer", 0)): row
            for row in view.get("freeze_layers", ())
            if isinstance(row, dict)
        }
        clipped_one = _clip(
            [
                (_number(value[0]), _number(value[1]))
                for value in layer_rows.get(1, {}).get("intervals", ())
                if isinstance(value, (list, tuple)) and len(value) >= 2
            ],
            windows,
        )
        clipped_two = _clip(
            [
                (_number(value[0]), _number(value[1]))
                for value in layer_rows.get(2, {}).get("intervals", ())
                if isinstance(value, (list, tuple)) and len(value) >= 2
            ],
            windows,
        )
        output_seconds = sum(end - start for start, end in windows)
        one_seconds = _seconds(clipped_one)
        two_seconds = _seconds(clipped_two)
        hits = sum(
            1
            for row in payload.get("combat_events", ())
            if isinstance(row, dict)
            and str(row.get("target_id") or "") in target_ids
            and bool(row.get("has_real_hit_evidence"))
        )
        blocks = _state_blocks(
            clipped_one, clipped_two, windows, max(1.0, args.block_seconds)
        )
        pooled_blocks.extend(blocks)
        ice_only_layers = _event_layer_intervals(
            freeze_events, target_ids, {"冰灵"}
        )
        ice_only_one = _clip(ice_only_layers[1], windows)
        ice_only_two = _clip(ice_only_layers[2], windows)
        ice_only_one_seconds = _seconds(ice_only_one)
        ice_only_two_seconds = _seconds(ice_only_two)
        ice_only_coverage = ice_only_two_seconds / output_seconds
        ice_only_totals["output"] += output_seconds
        ice_only_totals["one"] += ice_only_one_seconds
        ice_only_totals["two"] += ice_only_two_seconds
        ice_only_blocks.extend(
            _state_blocks(
                ice_only_one,
                ice_only_two,
                windows,
                max(1.0, args.block_seconds),
            )
        )
        totals["output"] += output_seconds
        totals["one"] += one_seconds
        totals["two"] += two_seconds
        totals["hits"] += hits
        captures.append(
            {
                "capture": str(path.resolve()),
                "output_seconds": round(output_seconds, 3),
                "one_or_more_coverage": round(one_seconds / output_seconds, 6),
                "two_layer_coverage": round(two_seconds / output_seconds, 6),
                "ice_spirit_only_two_layer_coverage": round(
                    ice_only_coverage, 6
                ),
                "real_hits_per_second": round(hits / output_seconds, 3),
            }
        )

    empirical = {
        "one_or_more_coverage": totals["one"] / totals["output"],
        "two_layer_coverage": totals["two"] / totals["output"],
        "exactly_one_layer_coverage": (totals["one"] - totals["two"])
        / totals["output"],
        "no_freeze_coverage": 1.0 - totals["one"] / totals["output"],
        "real_hits_per_second": totals["hits"] / totals["output"],
    }
    report = {
        "model": {
            "boss": args.boss,
            "fight_seconds": args.fight_seconds,
            "block_seconds": args.block_seconds,
            "iterations": args.iterations,
            "description": "从两份8人本实际冻结层时间序列抽取10秒块并有放回重采样；碎冰已经包含当前ACT按真实Hit流执行的100/200 Hit规则。",
        },
        "captures": captures,
        "pooled_empirical": {
            key: round(value, 6) for key, value in empirical.items()
        },
        "simulated_two_layer": {
            key: round(value, 6)
            for key, value in _bootstrap(
                pooled_blocks,
                args.fight_seconds,
                args.iterations,
                seed=20260914,
            ).items()
        },
        "ice_spirit_only": {
            "source_output_seconds": round(ice_only_totals["output"], 3),
            "pooled_empirical_one_or_more_coverage": round(
                ice_only_totals["one"] / ice_only_totals["output"], 6
            ),
            "pooled_empirical_two_layer_coverage": round(
                ice_only_totals["two"] / ice_only_totals["output"], 6
            ),
            "simulated_two_layer": {
                key: round(value, 6)
                for key, value in _bootstrap(
                    ice_only_blocks,
                    args.fight_seconds,
                    args.iterations,
                    seed=20260915,
                ).items()
            },
            "expected_two_layer_seconds_in_fight": round(
                ice_only_totals["two"]
                / ice_only_totals["output"]
                * args.fight_seconds,
                1,
            ),
        },
        "expected_seconds_in_fight": {
            "two_layers": round(empirical["two_layer_coverage"] * args.fight_seconds, 1),
            "exactly_one_layer": round(
                empirical["exactly_one_layer_coverage"] * args.fight_seconds, 1
            ),
            "no_freeze": round(empirical["no_freeze_coverage"] * args.fight_seconds, 1),
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
