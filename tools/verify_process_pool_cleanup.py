from __future__ import annotations

import ctypes
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.timeline_parallel import (
    _timeline_worker_warmup,
    reset_timeline_process_pool,
    timeline_process_pool_status,
    timeline_process_pool,
)


def process_exists(pid: int) -> bool:
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not process:
        return False
    ctypes.windll.kernel32.CloseHandle(process)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--idle",
        action="store_true",
        help="wait for the configured idle timeout instead of explicit reset",
    )
    args = parser.parse_args()
    executor = timeline_process_pool(2)
    if executor is None:
        raise RuntimeError("timeline process pool is disabled")
    pids = sorted(
        {
            future.result(timeout=30.0)
            for future in (
                executor.submit(_timeline_worker_warmup, 0.5),
                executor.submit(_timeline_worker_warmup, 0.5),
            )
        }
    )
    if args.idle:
        deadline = (
            time.monotonic()
            + float(timeline_process_pool_status()["idleTimeoutSeconds"])
            + 4.0
        )
        while time.monotonic() < deadline:
            if not timeline_process_pool_status()["active"]:
                break
            time.sleep(0.05)
    else:
        reset_timeline_process_pool()
        deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and any(process_exists(pid) for pid in pids):
        time.sleep(0.05)
    remaining = [pid for pid in pids if process_exists(pid)]
    print(json.dumps({
        "workers": pids,
        "remaining": remaining,
        "idle": args.idle,
        "pool": timeline_process_pool_status(),
    }))
    if remaining:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
