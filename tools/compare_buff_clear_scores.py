from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


class _NoScoreDiskCache:
    def load(self, _key: str) -> None:
        return None

    def save(self, _key: str, _rows: object, _glyphs: object) -> None:
        return None

    def discard(self, _key: str) -> None:
        return None


def _clear_wave_count(events: tuple[object, ...]) -> int:
    times = sorted(
        {
            max(0.0, int(getattr(event, "timestamp_ms", 0) or 0) / 1000.0)
            for event in events
            if getattr(event, "affected_target_ids", ())
        }
    )
    waves: list[float] = []
    for seconds in times:
        if not waves or seconds - waves[-1] > 0.5:
            waves.append(seconds)
        else:
            waves[-1] = seconds
    return len(waves)


def _score_rows(
    core: NativeSimulatorCore,
    source_hash: str,
    snapshot: object,
    source_file: Path,
    *,
    keep_clear_events: bool,
) -> tuple[list[dict[str, Any]], float]:
    scored_snapshot = snapshot if keep_clear_events else replace(
        snapshot, player_buff_clear_events=()
    )
    with core._state_lock:
        core._replays[source_hash] = scored_snapshot
        core._score_keys_by_replay.pop(source_hash, None)
    core.score_theory_planner.invalidate_score_cache()

    last_progress = -1

    def progress(percent: int, message: str) -> None:
        nonlocal last_progress
        if percent >= last_progress + 10 or percent >= 96:
            last_progress = percent
            label = "after" if keep_clear_events else "before"
            print(
                f"[{source_file.name}] {label} {percent:>3}% {message}",
                flush=True,
            )

    started = time.perf_counter()
    result = core.score_replay(
        {
            "sourceHash": source_hash,
            "sourceFile": str(source_file),
            "bossPreset": "红龙佩达",
        },
        progress,
    )
    elapsed = time.perf_counter() - started
    rows = core._score_results[result["scoreKey"]]
    return [
        {
            "source_id": row.source_id,
            "name": row.name,
            "job": row.rotation_job or row.job,
            "total_score": row.total_score,
            "output_score": row.output_score,
            "support_score": row.support_score,
            "specialty_score": row.specialty_score,
            "survival_penalty": row.survival_penalty,
            "actual_standard_power": row.actual_standard_power,
            "theory_standard_power": row.theory_standard_power,
            "theory_damage_completion": row.theory_damage_completion,
            "burst_usage": row.burst_usage,
            "coverage_completion": row.coverage_completion,
            "contribution_completion": row.contribution_completion,
            "actual_casts": len(row.actual_timeline),
            "theory_casts": len(row.theory_timeline),
        }
        for row in rows
    ], elapsed


def _difference(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    old = {str(row["source_id"]): row for row in before}
    result: list[dict[str, Any]] = []
    for current in after:
        previous = old.get(str(current["source_id"]))
        if previous is None:
            continue
        row: dict[str, Any] = {
            "source_id": current["source_id"],
            "name": current["name"],
            "job": current["job"],
        }
        for key in (
            "total_score",
            "output_score",
            "support_score",
            "specialty_score",
            "survival_penalty",
            "actual_standard_power",
            "theory_standard_power",
            "theory_damage_completion",
            "burst_usage",
            "coverage_completion",
            "contribution_completion",
            "actual_casts",
            "theory_casts",
        ):
            before_value = previous.get(key)
            after_value = current.get(key)
            if isinstance(before_value, (int, float)) and isinstance(
                after_value, (int, float)
            ):
                row[f"before_{key}"] = before_value
                row[f"after_{key}"] = after_value
                row[f"delta_{key}"] = after_value - before_value
        result.append(row)
    return result


def compare(path: Path) -> dict[str, Any]:
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._team_score_disk_cache = _NoScoreDiskCache()
    core._queue_replay_score_sync = lambda *_args, **_kwargs: None
    try:
        loaded = core.load_replay(str(path), lambda *_args: None)
        source_hash = str(loaded["sourceHash"])
        snapshot = core._replays[source_hash]
        events = tuple(getattr(snapshot, "player_buff_clear_events", ()))
        before, before_seconds = _score_rows(
            core,
            source_hash,
            snapshot,
            path,
            keep_clear_events=False,
        )
        after, after_seconds = _score_rows(
            core,
            source_hash,
            snapshot,
            path,
            keep_clear_events=True,
        )
        return {
            "file": str(path),
            "clear_target_events": len(events),
            "clear_waves": _clear_wave_count(events),
            "before_seconds": before_seconds,
            "after_seconds": after_seconds,
            "players": _difference(before, after),
        }
    finally:
        core.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare team scores with and without explicit player-Buff clears."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the full JSON report to this file and print only a compact summary.",
    )
    args = parser.parse_args()
    result = [compare(path.expanduser().resolve()) for path in args.captures]
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for capture in result:
            deltas = [
                float(player.get("delta_total_score", 0.0) or 0.0)
                for player in capture["players"]
            ]
            mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
            print(
                f"SUMMARY {Path(capture['file']).name}: "
                f"events={capture['clear_target_events']} "
                f"waves={capture['clear_waves']} "
                f"mean_total_delta={mean_delta:+.4f} "
                f"range={min(deltas, default=0.0):+.4f}..{max(deltas, default=0.0):+.4f}",
                flush=True,
            )
        print(f"JSON_REPORT {output}", flush=True)
        return
    print("BUFF_CLEAR_SCORE_COMPARISON_JSON")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
