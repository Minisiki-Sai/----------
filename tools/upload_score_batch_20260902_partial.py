from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.supabase_score_sync import (
    TeamScoreSupabaseSyncService,
)
from dragon_nest_builder.live_dps.team_score_sync import (
    CURRENT_SCORE_SCHEMA_VERSION,
    TeamScoreSyncStore,
)
from dragon_nest_builder.live_dps.v410_score_sync import (
    load_v410_score_sync_config,
)
from tools.batch_score_all_captures_v420 import export_rows


OUTPUT_ROOT = ROOT / "outputs" / "v451_score_batch_20260902_0801_0831_103"
INDEX_PATH = OUTPUT_ROOT / "batch_index.json"
DATABASE_PATH = OUTPUT_ROOT / "selected_score_sync.sqlite3"
RESULT_PATH = OUTPUT_ROOT / "partial_upload_result.json"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def main() -> None:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    index_rows = list(index.get("rows") or [])

    staged = 0
    score_rows = 0
    with TeamScoreSyncStore(DATABASE_PATH) as store:
        for item in index_rows:
            result_path = Path(str(item.get("result_file") or ""))
            if not result_path.is_file():
                continue
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("status") != "scored":
                continue
            rows = export_rows(result)
            if not rows:
                continue
            store.record_capture(
                Path(result["canonical_file"]),
                str(result.get("nest") or "未识别巢穴"),
                rows,
                force_supabase_sync=True,
            )
            staged += 1
            score_rows += len(rows)
        pending_before = store.pending_supabase_count()

    config = load_v410_score_sync_config()
    if not config.ready:
        raise RuntimeError("CloudBase 评分上传配置未就绪")

    service = TeamScoreSupabaseSyncService(
        config.endpoint,
        config.upload_token,
        config.installation_id,
        timeout_seconds=30.0,
    )
    service_version = service.health_check()
    with TeamScoreSyncStore(DATABASE_PATH) as store:
        sync_result = service.sync_store(
            store,
            batch_size=10,
            max_batches=1000,
            upload_capture_files=False,
        )
        pending_after = store.pending_supabase_count()

    payload = {
        "status": "uploaded" if pending_after == 0 else "partial",
        "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
        "service_version": service_version,
        "indexed_captures": len(index_rows),
        "staged_captures": staged,
        "score_rows": score_rows,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "upload_capture_files": False,
        "result": {
            key: getattr(sync_result, key)
            for key in sync_result.__dataclass_fields__
        },
    }
    atomic_json(RESULT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if pending_after:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
