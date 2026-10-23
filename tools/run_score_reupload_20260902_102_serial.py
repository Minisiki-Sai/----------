from __future__ import annotations

import json
import multiprocessing
import os
import sys
from pathlib import Path


# Configure the production scorer before importing its runtime modules.
os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.51")
os.environ.setdefault("DRAGON_NEST_TIMELINE_WORKERS", "8")
os.environ.setdefault("DRAGON_NEST_SCORE_PHASE_TIMEOUT_SECONDS", "600")
os.environ.setdefault("DRAGON_NEST_BATCH_USE_REPLAY_CACHE", "1")
os.environ.setdefault("DRAGON_NEST_BATCH_UPLOAD_EACH", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.batch_score_all_captures_v420 as batch
from dragon_nest_builder.live_dps.team_score_sync import TeamScoreSyncStore


SOURCE_INDEX = (
    ROOT
    / "outputs"
    / "v451_score_batch_20260902_0801_0831_103"
    / "batch_index.json"
)
OUTPUT_ROOT = ROOT / "outputs" / "v451_score_reupload_20260902_102_serial"


class _SelectedCaptureSet:
    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths

    def glob(self, pattern: str) -> list[Path]:
        return list(self.paths) if pattern == "*.dpscap" else []

    def __str__(self) -> str:
        return "V4.51 上次批次中的102份唯一抓包（逐份评分并立即上传）"


def _selected_paths() -> tuple[Path, ...]:
    payload = json.loads(SOURCE_INDEX.read_text(encoding="utf-8"))
    rows = list(payload.get("rows") or [])
    paths = tuple(Path(str(row.get("canonical_file") or "")) for row in rows)
    missing = [str(path) for path in paths if not path.is_file()]
    if len(paths) != 102:
        raise RuntimeError(f"上次唯一抓包清单应为102份，当前为 {len(paths)} 份")
    if len(set(paths)) != 102:
        raise RuntimeError("上次清单包含重复的 canonical_file")
    if missing:
        raise RuntimeError(f"有 {len(missing)} 份抓包已不存在：{missing[:3]}")
    return paths


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
