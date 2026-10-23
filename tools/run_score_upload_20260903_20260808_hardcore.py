from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


# Select production behavior before importing scoring modules.
os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.51")
os.environ.setdefault("DRAGON_NEST_TIMELINE_WORKERS", "8")
os.environ.setdefault("DRAGON_NEST_SCORE_PHASE_TIMEOUT_SECONDS", "900")
os.environ.setdefault("DRAGON_NEST_BATCH_USE_REPLAY_CACHE", "1")
os.environ.setdefault("DRAGON_NEST_BATCH_UPLOAD_EACH", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.batch_score_all_captures_v420 as batch
from dragon_nest_builder.live_dps.team_score_sync import TeamScoreSyncStore


SOURCE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
    r"\20260808硬核第一把"
)
OUTPUT_ROOT = ROOT / "outputs" / "v451_score_upload_20260903_20260808_hardcore_5"
EXPECTED_FILES = {
    "20260808硬核第一把.dpscap",
    "20260808硬核第二把.dpscap",
    "20260808硬核第三把.dpscap",
    "20260811硬核第一把.dpscap",
    "20260811硬核第二把.dpscap",
}


def _validate_source() -> None:
    actual = {path.name for path in SOURCE_ROOT.glob("*.dpscap")}
    if actual != EXPECTED_FILES:
        missing = sorted(EXPECTED_FILES - actual)
        extra = sorted(actual - EXPECTED_FILES)
        raise RuntimeError(
            f"抓包清单不是预期的 5 份；缺少={missing}，额外={extra}"
        )


def main() -> None:
    _validate_source()
    batch.CAPTURE_DIR = SOURCE_ROOT
    batch.OUTPUT_DIR = OUTPUT_ROOT
    batch.RESULT_DIR = OUTPUT_ROOT / "capture_results"
    batch.INDEX_PATH = OUTPUT_ROOT / "batch_index.json"
    sync_database = OUTPUT_ROOT / "selected_score_sync.sqlite3"
    batch.TeamScoreSyncStore = lambda: TeamScoreSyncStore(sync_database)
    batch.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
