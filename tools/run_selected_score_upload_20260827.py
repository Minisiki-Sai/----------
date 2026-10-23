from __future__ import annotations

import multiprocessing
import os
import re
from pathlib import Path

import tools.batch_score_all_captures_v420 as batch
from dragon_nest_builder.live_dps.supabase_score_sync import (
    TeamScoreSupabaseSyncService,
)
from dragon_nest_builder.live_dps.team_score_sync import TeamScoreSyncStore


CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
OUTPUT_ROOT = (
    batch.ROOT / "outputs" / "v440_score_batch_20260827_0801_0826_84"
)
_CAPTURE_NAME_PATTERN = re.compile(
    r"^(?:08(?:0[1-9]|1[0-9]|2[0-6])|"
    r"202608(?:0[1-9]|1[0-9]|2[0-6])|"
    r"2026-8-(?:0?[1-9]|1[0-9]|2[0-6]))"
)


class _SelectedCaptureSet:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths

    def glob(self, pattern: str) -> list[Path]:
        if pattern != "*.dpscap":
            return []
        return list(self.paths)

    def __str__(self) -> str:
        return "0801-0826主目录79份 + 20260808硬核第一把5份"


class _ScoreOnlyCloudService(TeamScoreSupabaseSyncService):
    """Upload requested score rows without starting a 1.9 GiB raw-file batch."""

    def sync_store(self, store: TeamScoreSyncStore, **kwargs):
        kwargs["upload_capture_files"] = False
        return super().sync_store(store, **kwargs)


def _selected_paths() -> tuple[Path, ...]:
    main = sorted(
        (
            path
            for path in CAPTURE_ROOT.glob("*.dpscap")
            if _CAPTURE_NAME_PATTERN.match(path.stem)
        ),
        key=lambda path: path.name,
    )
    extra = sorted(
        (CAPTURE_ROOT / "20260808硬核第一把").glob("*.dpscap"),
        key=lambda path: path.name,
    )
    if len(main) != 79 or len(extra) != 5:
        raise RuntimeError(
            f"目标抓包数量变化：主目录 {len(main)}，附加目录 {len(extra)}"
        )
    return tuple(main + extra)


def main() -> None:
    os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.41")
    selected = _selected_paths()
    result_dir = OUTPUT_ROOT / "capture_results"
    sync_database = OUTPUT_ROOT / "selected_score_sync.sqlite3"

    batch.CAPTURE_DIR = _SelectedCaptureSet(selected)
    batch.OUTPUT_DIR = OUTPUT_ROOT
    batch.RESULT_DIR = result_dir
    batch.INDEX_PATH = OUTPUT_ROOT / "batch_index.json"
    batch.TeamScoreSyncStore = lambda: TeamScoreSyncStore(sync_database)
    batch.TeamScoreSupabaseSyncService = _ScoreOnlyCloudService
    batch.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
