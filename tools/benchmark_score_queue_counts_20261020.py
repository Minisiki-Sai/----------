"""Read-only evidence for history-JSON scans in a count-only status request."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from dragon_nest_builder.live_dps.team_score_sync import (
        TeamScoreSyncStore, default_team_score_sync_path)
    path = default_team_score_sync_path()
    results = []
    # No constructor on the old path: it performs schema work and may sanitize
    # data. Reproduce its expensive pure JSON transform, never execute updates.
    for _ in range(3):
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            started = time.perf_counter()
            rows = changed = 0
            for (raw,) in con.execute("SELECT score_detail_json FROM score_rows"):
                try:
                    decoded = json.loads(str(raw or "{}"))
                except (TypeError, ValueError):
                    decoded = {}
                sanitized = json.dumps(TeamScoreSyncStore._score_details(decoded),
                                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                rows += 1
                changed += sanitized != str(raw or "{}")
            old_scan = time.perf_counter() - started
        started = time.perf_counter()
        with TeamScoreSyncStore(path, read_only=True) as reader:
            counts = (reader.pending_supabase_count(), reader.pending_capture_file_count())
        results.append(dict(scan_seconds=old_scan, readonly_both_counts_seconds=time.perf_counter()-started,
                            rows=rows, rows_that_would_change=changed, counts=counts))
    output = Path("outputs/verification/score_stages_20261020/queue_counts.json")
    output.write_text(json.dumps(dict(read_only=True, database_bytes=path.stat().st_size, runs=results),
                                 ensure_ascii=False, indent=2), "utf8")
    print(output.read_text("utf8"))


if __name__ == "__main__":
    main()
