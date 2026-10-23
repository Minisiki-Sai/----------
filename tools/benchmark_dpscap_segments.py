from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.dpscap_segments import (  # noqa: E402
    load_dpscap_segments,
    remove_dpscap_segments,
    split_dpscap,
)


def _load(path: Path, workers: int) -> dict[str, object]:
    previous_workers = os.environ.get("DRAGON_NEST_DPSCAP_READ_WORKERS")
    previous_pipeline = os.environ.get("DRAGON_NEST_REPLAY_PIPELINE")
    os.environ["DRAGON_NEST_DPSCAP_READ_WORKERS"] = str(max(1, workers))
    os.environ["DRAGON_NEST_REPLAY_PIPELINE"] = "serial"
    started = time.perf_counter()
    try:
        source = V576DiagnosticReplaySource(
            path,
            ROOT / "dragon_nest_builder" / "data",
            {},
        )
        events = source.poll()
        stats = dict(source.replay_pipeline_stats)
    finally:
        if previous_workers is None:
            os.environ.pop("DRAGON_NEST_DPSCAP_READ_WORKERS", None)
        else:
            os.environ["DRAGON_NEST_DPSCAP_READ_WORKERS"] = previous_workers
        if previous_pipeline is None:
            os.environ.pop("DRAGON_NEST_REPLAY_PIPELINE", None)
        else:
            os.environ["DRAGON_NEST_REPLAY_PIPELINE"] = previous_pipeline
    return {
        "wallMs": round((time.perf_counter() - started) * 1000.0, 3),
        "eventCount": len(events),
        "pipeline": stats,
    }


def benchmark(
    source: Path,
    *,
    segment_bytes: int,
    run_replay: bool,
) -> dict[str, object]:
    source = source.resolve()
    with tempfile.TemporaryDirectory(prefix="dn-dpscap-segment-bench-") as raw:
        directory = Path(raw)
        copy_path = directory / source.name
        copy_started = time.perf_counter()
        shutil.copy2(source, copy_path)
        copy_ms = (time.perf_counter() - copy_started) * 1000.0
        baseline = _load(copy_path, 1) if run_replay else None
        split_started = time.perf_counter()
        segments = split_dpscap(
            copy_path,
            segment_bytes=segment_bytes,
            minimum_bytes=1,
        )
        split_wall_ms = (time.perf_counter() - split_started) * 1000.0
        segmented = _load(copy_path, 2) if run_replay else None
        loaded = load_dpscap_segments(copy_path)
        result = {
            "capture": str(source),
            "sourceBytes": source.stat().st_size,
            "copyMs": round(copy_ms, 3),
            "segmentTargetBytes": segment_bytes,
            "segmentCount": len(segments.paths) if segments else 0,
            "splitWallMs": round(split_wall_ms, 3),
            "splitRecordedMs": (
                round(segments.split_elapsed_ms, 3) if segments else 0.0
            ),
            "manifestValid": loaded is not None,
            "baseline": baseline,
            "segmented2Workers": segmented,
        }
        if baseline and segmented:
            baseline_ms = float(baseline["wallMs"])
            segmented_ms = float(segmented["wallMs"])
            result["loadSpeedupPercent"] = round(
                (baseline_ms - segmented_ms) / baseline_ms * 100.0,
                3,
            ) if baseline_ms > 0 else 0.0
        # Explicitly remove the generated sibling before TemporaryDirectory
        # cleanup so the same guarded production path is exercised.
        remove_dpscap_segments(copy_path)
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--segment-mib", type=int, default=16)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [
        benchmark(
            capture,
            segment_bytes=max(1, args.segment_mib) * 1024 * 1024,
            run_replay=args.replay,
        )
        for capture in args.captures
    ]
    payload = {
        "segmentMiB": max(1, args.segment_mib),
        "replayMeasured": bool(args.replay),
        "rows": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
