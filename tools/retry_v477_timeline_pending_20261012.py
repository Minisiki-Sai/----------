"""Retry exactly the two human-authorized score uploads; never upload captures."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from dragon_nest_builder.live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from dragon_nest_builder.live_dps.team_score_sync import (
    CURRENT_SCORE_SCHEMA_VERSION, TeamScoreSyncStore, default_team_score_sync_path,
)
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config

TARGETS = {
    "ae821cc6595df321545b4a9250a9440f92bcf08eeb91cf3b96bb082d622d51f0": "1005bluemmon沙龙硬核",
    "6d5484d7f05a0b405771671a2fcdaabe7bc9d6e62d5c70607d88bd3c1459f596": "1011硬核bluemoon十字",
}
EXPECTED_SERVICE = "cloudbase-2.18.3-v477-timeline-aura-compat"
ROOT = Path(__file__).resolve().parents[1]


class SelectedQueue:
    def __init__(self, store):
        self.store = store

    def pending_supabase_captures(self, limit=1, **kwargs):
        return tuple(row for row in self.store.pending_supabase_captures(limit=10000, **kwargs)
                     if row.capture_hash in TARGETS)[:limit]

    def mark_supabase_synced(self, hashes, **kwargs):
        if not set(hashes) <= TARGETS.keys():
            raise RuntimeError("Upload acknowledgement escaped the selected captures")
        self.store.mark_supabase_synced(hashes, **kwargs)

    def mark_supabase_error(self, error, hashes):
        if not set(hashes) <= TARGETS.keys():
            raise RuntimeError("Upload error escaped the selected captures")
        self.store.mark_supabase_error(error, hashes)

    def pending_supabase_count(self, minimum_schema_version, score_schema_version):
        return len(self.pending_supabase_captures(limit=10000,
            minimum_schema_version=minimum_schema_version, score_schema_version=score_schema_version))

    def pending_capture_file_count(self):
        return self.store.pending_capture_file_count()


def main():
    if CURRENT_SCORE_SCHEMA_VERSION != 20261012:
        raise RuntimeError("Unexpected scoring algorithm")
    config = load_v410_score_sync_config()
    if not config.ready:
        raise RuntimeError("Score upload configuration is not ready")
    service = TeamScoreSupabaseSyncService(config.endpoint, config.upload_token,
        config.installation_id, timeout_seconds=120)
    version = service.health_check()
    if version != EXPECTED_SERVICE:
        raise RuntimeError(f"The compatible cloud deployment is not active: {version}")

    output = ROOT / "outputs" / "v477_timeline_aura_cloud_fix_20261012"
    output.mkdir(parents=True, exist_ok=True)
    backup = output / ("queue-backup-" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".sqlite3")
    source = sqlite3.connect(default_team_score_sync_path().as_uri() + "?mode=ro", uri=True)
    saved = sqlite3.connect(backup)
    try:
        source.backup(saved)
    finally:
        saved.close()
        source.close()
    with TeamScoreSyncStore() as store:
        queue = SelectedQueue(store)
        before = [row.capture_hash for row in queue.pending_supabase_captures(
            limit=10000, score_schema_version=CURRENT_SCORE_SCHEMA_VERSION)]
        result = service.sync_store(queue, batch_size=1, max_batches=2, upload_capture_files=False)
        states = [dict(zip(("capture_hash", "algorithm", "uploaded_algorithm", "status", "error"), row))
            for row in store._connection.execute(
                "SELECT capture_hash,score_schema_version,supabase_score_schema_version,"
                "supabase_sync_status,supabase_sync_error FROM captures WHERE capture_hash IN (?,?)",
                tuple(TARGETS))]
    report = {"service_version": version, "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
        "pending_before": before, "result": dataclasses.asdict(result), "states": states,
        "capture_files_uploaded": False, "installer_changed": False, "backup": str(backup)}
    (output / "retry-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if len(states) != 2 or any(row["status"] != "synced" or row["uploaded_algorithm"] != 20261012 for row in states):
        raise RuntimeError("Not all selected captures were acknowledged")


if __name__ == "__main__":
    main()
