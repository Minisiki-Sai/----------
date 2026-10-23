from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.v410_native_core import NativeLiveCapture


_MAGIC = b"DNDP1\x00"
_HEADER = struct.Struct("<dBII")


def raw_clock(path: Path) -> tuple[int, float]:
    count = 0
    first = 0.0
    last = 0.0
    with path.open("rb") as handle:
        if handle.read(len(_MAGIC)) != _MAGIC:
            raise ValueError(f"{path} is not a DNDP1 capture")
        while True:
            header = handle.read(_HEADER.size)
            if not header or len(header) != _HEADER.size:
                break
            timestamp, _adapter, _original, captured = _HEADER.unpack(header)
            if len(handle.read(captured)) != captured:
                break
            if count == 0:
                first = timestamp
            last = max(last, timestamp)
            count += 1
    return count, max(0.0, last - first)


def benchmark(path: Path, store: DataStore) -> dict[str, object]:
    total_started = time.perf_counter()
    raw_frames, raw_seconds = raw_clock(path)

    started = time.perf_counter()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    events = source.poll()
    decode_seconds = time.perf_counter() - started
    decoder = getattr(source, "_decoder", None)
    actor_id = int(getattr(decoder, "current_self_actor_id", 0) or 0)
    self_source_id = f"0x{actor_id:08X}" if actor_id else ""

    started = time.perf_counter()
    aggregator = CombatAggregator()
    for event in events:
        aggregator.ingest(event)
    ingest_seconds = time.perf_counter() - started

    started = time.perf_counter()
    snapshot = aggregator.snapshot(include_events=True)
    freeze_seconds = time.perf_counter() - started

    capture = NativeLiveCapture(
        DATA_DIR / "live_dps_profiles" / "v576.json",
        auto_start_monitor=False,
        timeline_store=store,
    )
    source_hash = f"benchmark:{path.name}"
    started = time.perf_counter()
    capture._publish_frozen_score_ready(
        snapshot, str(path), source_hash, "实测停止"
    )
    capture._start_final_page_projection(
        snapshot,
        str(path),
        source_hash,
        self_source_id,
        aggregator,
        "实测停止",
    )
    stop_visible_seconds = time.perf_counter() - started
    immediate = capture.status(include_snapshot=False)

    projection_started = time.perf_counter()
    thread = capture._final_projection_thread
    if thread is not None:
        thread.join(timeout=180.0)
    projection_seconds = time.perf_counter() - projection_started
    final = capture.status(include_snapshot=False)
    source.close()
    result = {
        "file": str(path),
        "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
        "raw_frames": raw_frames,
        "raw_clock_seconds": round(raw_seconds, 3),
        "event_count": len(events),
        "snapshot_elapsed_seconds": round(snapshot.elapsed_seconds, 3),
        "raw_vs_snapshot_seconds": round(raw_seconds - snapshot.elapsed_seconds, 3),
        "decode_seconds": round(decode_seconds, 3),
        "ingest_seconds": round(ingest_seconds, 3),
        "exact_snapshot_seconds": round(freeze_seconds, 3),
        "stop_visible_seconds": round(stop_visible_seconds, 4),
        "projection_background_seconds": round(projection_seconds, 3),
        "immediate_state": immediate.get("state"),
        "immediate_score_ready": immediate.get("scoreReady"),
        "immediate_page_ready": immediate.get("pageReady"),
        "final_page_ready": final.get("pageReady"),
        "final_error": final.get("error"),
        "total_seconds": round(time.perf_counter() - total_started, 3),
    }
    capture.close()
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    args = parser.parse_args()
    store = DataStore.load()
    rows = [benchmark(path.resolve(), store) for path in args.captures]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
