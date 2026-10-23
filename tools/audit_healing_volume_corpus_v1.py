"""Read cached replays and compare cast-count versus HP-volume healer rates.

This audit is deliberately read-only: it neither refreshes nor deletes replay
caches and never uploads scores.  Each row uses the full recorded interval;
official Boss-scoped scores may use a narrower evaluation window.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import pickle
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.scoring import HEALER_JOBS, TeamScoreEngine, data_job_for, event_platform_for


def _cached_aggregator(capture: Path, cache_root: Path):
    candidates = []
    for manifest_path in cache_root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if Path(str(manifest.get("sourcePath", ""))).resolve() != capture.resolve():
                continue
            if int(manifest.get("sourceSize", 0)) != capture.stat().st_size:
                continue
            for path in manifest_path.parent.glob("*.replaycache"):
                with path.open("rb") as stream:
                    if stream.read(6) != b"DNRC2\x00":
                        continue
                    header_size = struct.unpack("<I", stream.read(4))[0]
                    header = json.loads(stream.read(header_size))
                if header.get("capture_hash") == manifest.get("captureHash"):
                    candidates.append((int(header.get("created_at", 0)), path, header))
        except (OSError, ValueError, TypeError, json.JSONDecodeError, struct.error):
            continue
    for _created, path, header in sorted(candidates, reverse=True):
        try:
            with path.open("rb") as stream:
                if stream.read(6) != b"DNRC2\x00":
                    continue
                header_size = struct.unpack("<I", stream.read(4))[0]
                stream.seek(10 + header_size)
                payload = zlib.decompress(stream.read())
            if hashlib.sha256(payload).hexdigest() != header.get("payload_sha256"):
                continue
            return pickle.loads(payload)
        except (OSError, ValueError, TypeError, EOFError, pickle.PickleError, zlib.error):
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument(
        "--cache-root", type=Path,
        default=Path.home() / "Documents" / "DragonNestDpsCapture" / "replay_cache",
    )
    args = parser.parse_args()
    store = DataStore.load()
    engine = TeamScoreEngine(store)
    for capture in args.captures:
        aggregator = _cached_aggregator(capture, args.cache_root)
        if aggregator is None:
            print(json.dumps({"capture": capture.name, "error": "no matching replay cache"}, ensure_ascii=True), flush=True)
            continue
        # An older replay cache predates this empty settlement index.  It is
        # unrelated to HP/healing and can be initialized for a read-only audit.
        settlement = getattr(aggregator, "_settlement_ledger", None)
        if settlement is not None and not hasattr(settlement, "_terminal_boss_ends"):
            settlement._terminal_boss_ends = {}
        if not hasattr(aggregator, "_shock_damage_identity_parameters"):
            aggregator._shock_damage_identity_parameters = []
        try:
            snapshot = aggregator.snapshot(include_events=True)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            print(json.dumps({"capture": capture.name, "error": f"incompatible replay cache: {exc}"}, ensure_ascii=True), flush=True)
            continue
        platform = event_platform_for(snapshot)
        # Snapshot.elapsed_seconds is a duration; event timestamps are on the
        # capture's absolute clock and may start thousands of seconds later.
        clock_end_ms = max(
            [1000, *(row.timestamp_ms for row in platform.event_index.healing_events),
             *(row.timestamp_ms for row in platform.event_index.incoming_damage_events)],
        )
        windows = ((0.0, clock_end_ms / 1000.0 + 1.0),)
        eligible_count = 0
        for player in snapshot.players:
            job = data_job_for(player.job)
            if job not in HEALER_JOBS:
                continue
            if job == "药剂师" and not engine._alchemist_used_sacrifice(snapshot, player.source_id):
                continue
            if job == "曜" and engine._yao_meditation_coverage(snapshot, player.source_id, windows) < 0.30:
                continue
            eligible_count += 1
            result = engine._healing_completion(snapshot, player, job, windows)
            detail = result.rescue_detail
            print(json.dumps({
                "capture": capture.name,
                "seconds": round(snapshot.elapsed_seconds, 1),
                "player": player.name,
                "job": player.job,
                "source_id": player.source_id,
                "cast_rate": round(result.cast_completion, 2),
                "cast_fraction": f"{result.meaningful_casts}/{result.evaluated_casts}",
                "hp_volume_rate": round(result.effective_realization, 2),
                "effective_healing": result.effective_healing,
                "healable_loss": round(detail["healing_volume_healable_loss"]),
                "team_matched_healing": round(detail["healing_volume_matched_team"]),
                "team_unmet_healing": round(detail["healing_volume_unmet_team"]),
                "shared_unmet_healing": round(detail["healing_volume_shared_unmet"]),
                "method": detail["healing_volume_method"],
                "rescue_penalty": round(float(detail.get("penalty", 0)), 2),
                "healer_duty_completion": round(result.score, 2),
            }, ensure_ascii=True), flush=True)
        if not eligible_count:
            print(json.dumps({"capture": capture.name, "note": "no eligible healing-duty player"}, ensure_ascii=True), flush=True)
        del aggregator, snapshot
        gc.collect()


if __name__ == "__main__":
    main()
