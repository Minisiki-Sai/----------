from __future__ import annotations

import json
from pathlib import Path

import dragon_nest_builder.live_dps.supabase_score_sync as score_sync
from dragon_nest_builder.live_dps.team_score_sync import (
    CURRENT_SCORE_SCHEMA_VERSION,
    TeamScoreSyncStore,
)
from dragon_nest_builder.live_dps.v410_score_sync import (
    load_v410_score_sync_config,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "outputs" / "v440_score_batch_20260827_0801_0826_84"
DATABASE_PATH = OUTPUT_ROOT / "selected_score_sync.sqlite3"
RESULT_PATH = OUTPUT_ROOT / "upload_compat_result.json"
COMPAT_SYNC_SCHEMA_VERSION = 5


class RecordingSyncService(score_sync.TeamScoreSupabaseSyncService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.service_version = "unknown"

    def _post(self, payload):
        result = super()._post(payload)
        self.service_version = str(result.get("service_version") or self.service_version)
        return result


def main() -> None:
    config = load_v410_score_sync_config()
    if not config.ready:
        raise RuntimeError("CloudBase 评分上传配置未就绪")

    # The deployed CloudBase function predates protocol 6. Protocol 5 still
    # carries all score rows and score_detail fields; only verified character
    # identity rows are a protocol-6 addition.
    score_sync.SUPABASE_SYNC_SCHEMA_VERSION = COMPAT_SYNC_SCHEMA_VERSION
    service = RecordingSyncService(
        config.endpoint,
        config.upload_token,
        config.installation_id,
        timeout_seconds=30.0,
    )
    with TeamScoreSyncStore(DATABASE_PATH) as store:
        pending_before = store.pending_supabase_count(
            COMPAT_SYNC_SCHEMA_VERSION,
            CURRENT_SCORE_SCHEMA_VERSION,
        )
        result = service.sync_store(
            store,
            batch_size=10,
            max_batches=1000,
            upload_capture_files=False,
        )
        pending_after = store.pending_supabase_count(
            COMPAT_SYNC_SCHEMA_VERSION,
            CURRENT_SCORE_SCHEMA_VERSION,
        )

    payload = {
        "status": "uploaded" if pending_after == 0 else "partial",
        "sync_schema_version": COMPAT_SYNC_SCHEMA_VERSION,
        "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
        "service_version": service.service_version,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "result": {
            key: getattr(result, key) for key in result.__dataclass_fields__
        },
    }
    RESULT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if pending_after:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
