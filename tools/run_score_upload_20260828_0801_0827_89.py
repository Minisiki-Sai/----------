from __future__ import annotations

import multiprocessing
import os
import re
import sys
from pathlib import Path

# Set the application version before importing the batch module. The native
# core reads this value at import time and must use the current v4.41 rules.
os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.41")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.batch_score_all_captures_v420 as batch
from dragon_nest_builder.live_dps.team_score_sync import TeamScoreSyncStore


CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
OUTPUT_ROOT = batch.ROOT / "outputs" / "v441_score_batch_20260828_0801_0827_89"
SPECIAL_ROOT = CAPTURE_ROOT / "20260808硬核第一把"
_MAIN_CAPTURE_NAME_PATTERN = re.compile(
    r"^(?:08(?:0[1-9]|1[0-9]|2[0-7])|"
    r"202608(?:0[1-9]|1[0-9]|2[0-7])|"
    r"2026-8-(?:0?[1-9]|1[0-9]|2[0-7]))"
)


class _SelectedCaptureSet:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths

    def glob(self, pattern: str) -> list[Path]:
        if pattern != "*.dpscap":
            return []
        return list(self.paths)

    def __str__(self) -> str:
        return "0801-0827 主目录84份 + 20260808硬核第一把5份"


def _selected_paths() -> tuple[Path, ...]:
    main = sorted(
        (
            path
            for path in CAPTURE_ROOT.glob("*.dpscap")
            if _MAIN_CAPTURE_NAME_PATTERN.match(path.stem)
        ),
        key=lambda path: path.name,
    )
    extra = sorted(SPECIAL_ROOT.glob("*.dpscap"), key=lambda path: path.name)
    if len(main) != 84 or len(extra) != 5:
        raise RuntimeError(
            f"目标抓包数量变化：主目录 {len(main)}，附加目录 {len(extra)}"
        )
    return tuple(main + extra)


def main() -> None:
    selected = _selected_paths()
    result_dir = OUTPUT_ROOT / "capture_results"
    sync_database = OUTPUT_ROOT / "selected_score_sync.sqlite3"

    batch.CAPTURE_DIR = _SelectedCaptureSet(selected)
    batch.OUTPUT_DIR = OUTPUT_ROOT
    batch.RESULT_DIR = result_dir
    batch.INDEX_PATH = OUTPUT_ROOT / "batch_index.json"
    # Use an isolated queue so this run cannot mix with a previous batch.
    batch.TeamScoreSyncStore = lambda: TeamScoreSyncStore(sync_database)
    batch.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
