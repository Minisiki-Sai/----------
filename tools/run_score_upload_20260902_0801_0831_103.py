from __future__ import annotations

import multiprocessing
import os
import re
import sys
from pathlib import Path

# Set every version/concurrency option before importing the scoring runtime.
os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.51")
os.environ.setdefault("DRAGON_NEST_TIMELINE_WORKERS", "16")
os.environ.setdefault("DRAGON_NEST_SCORE_PHASE_TIMEOUT_SECONDS", "600")
os.environ.setdefault("DRAGON_NEST_BATCH_USE_REPLAY_CACHE", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.batch_score_all_captures_v420 as batch
from dragon_nest_builder.live_dps.team_score_sync import TeamScoreSyncStore


CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
OUTPUT_ROOT = batch.ROOT / "outputs" / "v451_score_batch_20260902_0801_0831_103"
_DATE_PREFIX = re.compile(
    r"^(?:"
    r"08(?:0[1-9]|[12][0-9]|3[01])|"
    r"202608(?:0[1-9]|[12][0-9]|3[01])|"
    r"2026-8-(?:0?[1-9]|[12][0-9]|3[01])"
    r")"
)


class _SelectedCaptureSet:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths

    def glob(self, pattern: str) -> list[Path]:
        return list(self.paths) if pattern == "*.dpscap" else []

    def __str__(self) -> str:
        return "0801-0831：08开头94份 + 2026开头9份"


def _selected_paths() -> tuple[Path, ...]:
    selected = tuple(sorted(
        (
            path
            for path in CAPTURE_ROOT.glob("*.dpscap")
            if _DATE_PREFIX.match(path.stem)
        ),
        key=lambda path: path.name,
    ))
    short_names = sum(path.name.startswith("08") for path in selected)
    long_names = sum(path.name.startswith("2026") for path in selected)
    if len(selected) != 103 or short_names != 94 or long_names != 9:
        raise RuntimeError(
            "目标抓包数量变化："
            f"总数 {len(selected)}，08开头 {short_names}，2026开头 {long_names}"
        )
    return selected


def main() -> None:
    selected = _selected_paths()
    batch.CAPTURE_DIR = _SelectedCaptureSet(selected)
    batch.OUTPUT_DIR = OUTPUT_ROOT
    batch.RESULT_DIR = OUTPUT_ROOT / "capture_results"
    batch.INDEX_PATH = OUTPUT_ROOT / "batch_index.json"
    sync_database = OUTPUT_ROOT / "selected_score_sync.sqlite3"
    batch.TeamScoreSyncStore = lambda: TeamScoreSyncStore(sync_database)
    batch.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
