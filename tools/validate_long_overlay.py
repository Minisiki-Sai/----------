from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def validate(path: Path, core: NativeSimulatorCore) -> dict[str, object]:
    started = time.perf_counter()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    events = sorted(source.poll(), key=lambda row: row.timestamp_ms)
    decoder = getattr(source, "_decoder", None)
    actor_id = int(getattr(decoder, "current_self_actor_id", 0) or 0)
    self_source_id = f"0x{actor_id:08X}" if actor_id else ""
    extent = max((row.timestamp_ms for row in events), default=0) / 1000.0
    sample_step = 15.0 if extent <= 1_200 else 30.0
    sample_times = [round(index * sample_step, 3) for index in range(int(extent // sample_step) + 1)]
    if not sample_times or sample_times[-1] < extent:
        sample_times.append(extent)

    aggregator = CombatAggregator()
    event_index = 0
    clock_errors: list[float] = []
    finite_buff_violations: list[dict[str, object]] = []
    finite_debuff_violations: list[dict[str, object]] = []
    visible_buff_samples = 0
    visible_debuff_samples = 0
    late_visible_samples = 0
    snapshot_build_ms: list[float] = []
    snapshot_build_samples: list[tuple[float, float]] = []
    for second in sample_times:
        current_ms = round(second * 1000)
        while event_index < len(events) and events[event_index].timestamp_ms <= current_ms:
            aggregator.ingest(events[event_index])
            event_index += 1
        projection_started = time.perf_counter()
        payload = aggregator.overlay_snapshot(current_ms)
        projection_ms = (time.perf_counter() - projection_started) * 1000.0
        snapshot_build_ms.append(projection_ms)
        snapshot_build_samples.append((second, projection_ms))
        payload["selfSourceId"] = self_source_id
        core.live_capture = SimpleNamespace(
            overlay_status=lambda payload=payload: {
                "state": "running",
                "status": "long replay validation",
                "overlaySnapshot": payload,
                "overlaySnapshotPublishedMonotonic": time.monotonic(),
                "overlaySnapshotRevision": payload.get("revision", 0),
            }
        )
        overlay = core.buff_overlay_status()
        overlay_second = float(overlay.get("elapsedSeconds") or 0.0)
        clock_errors.append(abs(overlay_second - second))
        buffs = overlay.get("buffs", [])
        debuffs = overlay.get("debuffs", [])
        visible_buff_samples += int(bool(buffs))
        visible_debuff_samples += int(bool(debuffs))
        if second >= extent * 0.75 and (buffs or debuffs):
            late_visible_samples += 1
        for row in buffs:
            if row.get("permanent"):
                continue
            start = float(row.get("start_seconds") or 0.0)
            end = float(row.get("end_seconds") or 0.0)
            if start > overlay_second + 0.051 or end <= overlay_second:
                finite_buff_violations.append(
                    {"sample": second, "start": start, "end": end, "skill": row.get("skill_name")}
                )
        for row in debuffs:
            end = float(row.get("end_seconds") or 0.0)
            if end and end <= overlay_second:
                finite_debuff_violations.append(
                    {"sample": second, "end": end, "label": row.get("label")}
                )
    source.close()
    ordered_snapshot_ms = sorted(snapshot_build_ms)

    def percentile(fraction: float) -> float:
        if not ordered_snapshot_ms:
            return 0.0
        index = round((len(ordered_snapshot_ms) - 1) * fraction)
        return ordered_snapshot_ms[index]

    return {
        "file": str(path),
        "duration_seconds": round(extent, 3),
        "event_count": len(events),
        "sample_count": len(sample_times),
        "max_clock_error_seconds": round(max(clock_errors, default=0.0), 6),
        "finite_buff_violations": finite_buff_violations[:10],
        "finite_debuff_violations": finite_debuff_violations[:10],
        "visible_buff_samples": visible_buff_samples,
        "visible_debuff_samples": visible_debuff_samples,
        "late_visible_samples": late_visible_samples,
        "overlay_snapshot_avg_ms": round(
            sum(snapshot_build_ms) / max(1, len(snapshot_build_ms)), 3
        ),
        "overlay_snapshot_p50_ms": round(percentile(0.50), 3),
        "overlay_snapshot_p95_ms": round(percentile(0.95), 3),
        "overlay_snapshot_max_ms": round(max(snapshot_build_ms, default=0.0), 3),
        "overlay_snapshot_slowest": [
            {"second": second, "milliseconds": round(milliseconds, 3)}
            for second, milliseconds in sorted(
                snapshot_build_samples,
                key=lambda item: item[1],
                reverse=True,
            )[:3]
        ],
        "validation_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    args = parser.parse_args()
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    live_capture = core.live_capture
    try:
        rows = [validate(path.resolve(), core) for path in args.captures]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        core.live_capture = live_capture
        core.shutdown()


if __name__ == "__main__":
    main()
