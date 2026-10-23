from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.benchmark_catalog import (
    BENCHMARK_CATALOG_SCHEMA,
    BENCHMARK_MANIFEST_SCHEMA,
)
from dragon_nest_builder.live_dps.supabase_score_sync import (
    TeamScoreSupabaseSyncService,
)
from dragon_nest_builder.live_dps.team_score_sync import (
    CURRENT_SCORE_SCHEMA_VERSION,
)
from dragon_nest_builder.live_dps.v410_score_sync import (
    load_v410_score_sync_config,
)


REMOTE_CATALOG_URL = (
    "https://dragonnest-d0gy32zvzd63b6c52-1473474041."
    "ap-shanghai.app.tcloudbase.com/team-score-sync?asset=benchmark-catalog"
)


def _write_json(path: Path, payload: object, *, compact: bool = False) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
    ) + "\n"
    raw = text.encode("utf-8")
    path.write_bytes(raw)
    return raw


def export(version: str) -> tuple[Path, int]:
    config = load_v410_score_sync_config()
    if not config.ready:
        raise SystemExit("评分上传配置尚未就绪，无法导出职业均值")
    service = TeamScoreSupabaseSyncService(
        config.endpoint,
        config.upload_token,
        config.installation_id,
        timeout_seconds=30.0,
    )
    rows = sorted(
        service.fetch_benchmarks(),
        key=lambda row: (
            str(row.get("nest") or ""),
            str(row.get("job") or ""),
        ),
    )
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    filename = f"team-score-benchmarks-{version}.json.gz"
    catalog = {
        "schema": BENCHMARK_CATALOG_SCHEMA,
        "version": version,
        "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "benchmark_count": len(rows),
        "benchmarks": rows,
    }

    publish_directory = ROOT / "team-score-benchmarks"
    raw_json = (
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    raw = gzip.compress(raw_json, compresslevel=9, mtime=0)
    (publish_directory / filename).parent.mkdir(parents=True, exist_ok=True)
    (publish_directory / filename).write_bytes(raw)
    manifest = {
        "schema": BENCHMARK_MANIFEST_SCHEMA,
        "version": version,
        "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "file": filename,
        "data_url": REMOTE_CATALOG_URL + f"&version={version}",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "compression": "gzip",
        "uncompressed_bytes": len(raw_json),
        "benchmark_count": len(rows),
        "cache_ttl_seconds": 18000,
    }
    _write_json(publish_directory / "manifest.json", manifest)

    destinations = (
        ROOT
        / "dragon_nest_builder"
        / "data"
        / "modular"
        / "team_score_benchmarks",
        ROOT / "cloudbase" / "functions" / "team-score-sync" / "data",
    )
    for destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / filename).write_bytes(raw)
        _write_json(destination / "manifest.json", manifest)
    return publish_directory / "manifest.json", len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export CloudBase team-score benchmarks as versioned static JSON"
    )
    parser.add_argument(
        "--version",
        default=(
            f"{CURRENT_SCORE_SCHEMA_VERSION}-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ),
    )
    args = parser.parse_args()
    manifest, count = export(str(args.version).strip())
    print(f"exported {count} benchmark rows")
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
