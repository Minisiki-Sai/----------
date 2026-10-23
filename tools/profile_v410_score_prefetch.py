from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v410_backend import Engine, _start_default_score_prefetch, _start_score_job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--open-after", type=float, default=30.0)
    args = parser.parse_args()

    engine = Engine()
    core = engine.native
    core._score_sync.enqueue = lambda *_args, **_kwargs: None
    core._team_score_disk_cache.load = lambda _key: None
    core._team_score_disk_cache.save = lambda *_args, **_kwargs: None

    replay = core.load_replay(
        str(args.capture.resolve()),
        lambda _value, _label: None,
    )
    prefetch_started = time.perf_counter()
    _start_default_score_prefetch(core, replay)
    prefetch_job_id = str(replay.get("scorePrefetchJobId") or "")
    if not prefetch_job_id:
        raise RuntimeError("score prefetch did not start")

    time.sleep(max(0.0, args.open_after))
    rating_opened = time.perf_counter()
    request = {
        "sourceHash": replay["sourceHash"],
        "bossTargetIds": [],
        "glyphOverrides": {},
    }
    joined_job_id = _start_score_job(core, request)
    while True:
        status = core.jobs.status(joined_job_id)
        if status.get("done"):
            break
        time.sleep(0.1)
    completed = time.perf_counter()
    if status.get("state") != "completed":
        raise RuntimeError(str(status.get("error") or status))

    print(
        f"same_job={joined_job_id == prefetch_job_id} "
        f"background_total={completed - prefetch_started:.3f}s "
        f"rating_wait={completed - rating_opened:.3f}s "
        f"opened_after={args.open_after:.3f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
