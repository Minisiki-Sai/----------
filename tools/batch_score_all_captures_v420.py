from __future__ import annotations

import hashlib
import json
import gc
import os
import sys
import time
import dataclasses
from dataclasses import replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DRAGON_NEST_APP_VERSION", "4.20")

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.supabase_score_sync import (
    TeamScoreSupabaseSyncService,
)
from dragon_nest_builder.live_dps.team_score_sync import (
    CURRENT_SCORE_SCHEMA_VERSION,
    TeamScoreExportRow,
    TeamScoreSyncStore,
)
from dragon_nest_builder.live_dps.v410_score_sync import (
    load_v410_score_sync_config,
    resolve_score_nest,
)
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


_score_details_for_cloud = TeamScoreSyncStore._score_details


CAPTURE_DIR = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
OUTPUT_DIR = ROOT / "outputs" / "v420_score_batch_20260819"
RESULT_DIR = OUTPUT_DIR / "capture_results"
INDEX_PATH = OUTPUT_DIR / "batch_index.json"


class _BatchReplayCacheDisabled:
    """Avoid doubling large replay memory while processing a one-shot batch."""

    def load(self, identity: object) -> None:
        return None

    def save(self, identity: object, aggregator: object) -> None:
        return None


class _BatchScoreCacheDisabled:
    """Force every selected capture through the current scoring rules."""

    def load(self, key: str) -> None:
        return None

    def save(self, key: str, rows: object, glyphs: object) -> None:
        return None

    def discard(self, key: str) -> None:
        return None


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def compact_detail(detail: dict[str, Any]) -> dict[str, Any]:
    # Use the exact current production projection. It keeps all score details
    # shown by history pages and encodes actual/theory/Buff timelines into the
    # bounded compact payload used by CloudBase.
    return _score_details_for_cloud(detail)


def replay_metadata(replay: dict[str, Any]) -> dict[str, Any]:
    players = []
    for player in replay.get("players", []):
        if not isinstance(player, dict):
            continue
        players.append(
            {
                key: player.get(key)
                for key in (
                    "source_id",
                    "name",
                    "job",
                    "damage",
                    "healing",
                    "dps",
                    "hits",
                    "critical_hits",
                    "critical_rate",
                    "active_seconds",
                    "incoming_damage",
                )
            }
        )
    encounters = []
    for encounter in replay.get("boss_encounters", []):
        if not isinstance(encounter, dict):
            continue
        encounters.append(
            {
                key: encounter.get(key)
                for key in (
                    "name",
                    "max_hp",
                    "target_ids",
                    "damage",
                    "hits",
                    "critical_hits",
                    "first_ms",
                    "last_ms",
                    "phase_windows",
                    "confidence",
                    "key",
                    "base_stats",
                )
            }
        )
    return {
        "elapsed_seconds": replay.get("elapsed_seconds", 0.0),
        "dps_elapsed_seconds": replay.get("dps_elapsed_seconds", 0.0),
        "total_damage": replay.get("total_damage", 0),
        "total_healing": replay.get("total_healing", 0),
        "raw_damage_total": replay.get("raw_damage_total", 0),
        "attributed_damage_total": replay.get("attributed_damage_total", 0),
        "unresolved_damage_total": replay.get("unresolved_damage_total", 0),
        "inferred_cast_count": replay.get("inferred_cast_count", 0),
        "reassigned_hit_count": replay.get("reassigned_hit_count", 0),
        "version_cache": replay.get("versionCache", {}),
        "players": players,
        "boss_encounters": encounters,
    }


def export_rows(result: dict[str, Any]) -> tuple[TeamScoreExportRow, ...]:
    rows = result.get("score_rows", [])
    details = {
        str(detail.get("source_id", "")): detail
        for detail in result.get("details", [])
        if isinstance(detail, dict)
    }
    identities = {
        str(source_id).upper(): identity
        for source_id, identity in dict(
            result.get("character_identities") or {}
        ).items()
        if isinstance(identity, dict)
    }
    actual_dps_by_source = {
        str(player.get("source_id") or "").upper(): float(player.get("dps") or 0.0)
        for player in dict(result.get("replay") or {}).get("players", [])
        if isinstance(player, dict) and player.get("source_id")
    }
    exports: list[TeamScoreExportRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        job = str(row.get("job") or "").strip()
        if not job or job == "未知职业":
            continue
        detail = details.get(str(row.get("sourceId", "")), {})
        exports.append(
            TeamScoreExportRow(
                job=job,
                total_score=float(row.get("totalScore") or 0.0),
                output_score=float(row.get("outputScore") or 0.0),
                support_score=float(row.get("supportScore") or 0.0),
                specialty_score=(
                    float(row["specialtyScore"])
                    if row.get("specialtyScore") is not None
                    else None
                ),
                survival_penalty=float(row.get("survivalPenalty") or 0.0),
                theory_damage_completion=float(
                    row.get("theoryDamageCompletion") or 0.0
                ),
                actual_dps=actual_dps_by_source.get(
                    str(row.get("sourceId", "")).upper()
                ),
                score_detail=detail,
                character_identity=identities.get(
                    str(row.get("sourceId", "")).upper()
                ),
            )
        )
    return tuple(exports)


def upload_capture_result(
    result: dict[str, Any],
    service: TeamScoreSupabaseSyncService,
    *,
    retry_count: int = 3,
) -> dict[str, Any]:
    """Stage and upload one scored capture before the batch continues.

    Large replay batches must not retain every capture until a final bulk sync.
    Keeping the local store transaction and remote request scoped to one result
    also gives interrupted runs an exact, per-capture resume boundary.
    """

    rows = export_rows(result)
    if not rows:
        raise RuntimeError("评分结果没有可上传的职业行")

    with TeamScoreSyncStore() as sync_store:
        sync_store.record_capture(
            Path(result["canonical_file"]),
            str(result.get("nest") or "未识别巢穴"),
            rows,
            force_supabase_sync=True,
        )
        pending_before = sync_store.pending_supabase_count()

    last_error: Exception | None = None
    sync_result = None
    pending_after = pending_before
    for attempt in range(1, max(1, int(retry_count)) + 1):
        try:
            with TeamScoreSyncStore() as sync_store:
                sync_result = service.sync_store(
                    sync_store,
                    batch_size=1,
                    max_batches=100,
                    upload_capture_files=False,
                )
                pending_after = sync_store.pending_supabase_count()
            if pending_after == 0:
                break
            raise RuntimeError(f"上传后仍有 {pending_after} 份评分待同步")
        except Exception as exc:
            last_error = exc
            if attempt >= max(1, int(retry_count)):
                raise
            time.sleep(min(4.0, float(2 ** (attempt - 1))))

    if sync_result is None:
        raise RuntimeError(f"上传没有返回结果：{last_error}")
    return {
        "status": "uploaded",
        "pending_before": pending_before,
        "pending_after": pending_after,
        "score_rows": len(rows),
        "result": {
            key: getattr(sync_result, key)
            for key in sync_result.__dataclass_fields__
        },
    }


def main() -> None:
    started = time.time()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(CAPTURE_DIR.glob("*.dpscap"), key=lambda p: (p.stat().st_size, p.name))
    grouped: dict[str, list[Path]] = {}
    print(f"HASHING {len(paths)} capture files", flush=True)
    for index, path in enumerate(paths, 1):
        digest = content_hash(path)
        grouped.setdefault(digest, []).append(path)
        print(f"HASH {index:03d}/{len(paths):03d} {path.name}", flush=True)

    store = DataStore.load()
    # Create one scoring core per uncached capture.  The replay/scoring core
    # intentionally keeps several in-memory snapshots for interactive page
    # switching; retaining one instance across a large batch causes those
    # caches and Python/native buffers to accumulate until the worker is killed
    # by the OS.  A per-capture core preserves the scoring path and results,
    # while allowing the previous capture to be reclaimed before the next one.
    unique_items = sorted(
        grouped.items(), key=lambda item: (item[1][0].stat().st_size, item[1][0].name)
    )
    index_rows: list[dict[str, Any]] = []
    upload_each = os.getenv("DRAGON_NEST_BATCH_UPLOAD_EACH", "0") == "1"
    upload_service: TeamScoreSupabaseSyncService | None = None
    upload_service_version = ""
    if upload_each:
        config = load_v410_score_sync_config()
        if not config.ready:
            raise RuntimeError("CloudBase 评分上传配置未就绪")
        upload_service = TeamScoreSupabaseSyncService(
            config.endpoint,
            config.upload_token,
            config.installation_id,
            timeout_seconds=30.0,
        )
        upload_service_version = upload_service.health_check()
        print(
            f"UPLOAD_MODE immediate service={upload_service_version}",
            flush=True,
        )

    for ordinal, (digest, duplicates) in enumerate(unique_items, 1):
        canonical = duplicates[0]
        result_path = RESULT_DIR / f"{digest}.json"
        if result_path.is_file():
            try:
                existing = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
            if int(existing.get("score_schema_version") or 0) == CURRENT_SCORE_SCHEMA_VERSION:
                existing["source_files"] = [str(path) for path in duplicates]
                if (
                    upload_each
                    and existing.get("status") == "scored"
                    and dict(existing.get("upload") or {}).get("status") != "uploaded"
                ):
                    assert upload_service is not None
                    existing["upload"] = upload_capture_result(
                        existing,
                        upload_service,
                    )
                    print(
                        f"UPLOADED {ordinal:03d}/{len(unique_items):03d} "
                        f"{canonical.name}",
                        flush=True,
                    )
                atomic_json(result_path, existing)
                index_rows.append(
                    {
                        "capture_hash": digest,
                        "canonical_file": str(canonical),
                        "source_files": [str(path) for path in duplicates],
                        "result_file": str(result_path),
                        "status": existing.get("status", "unknown"),
                        "nest": existing.get("nest", ""),
                        "score_rows": len(existing.get("score_rows", [])),
                        "resumed": True,
                    }
                )
                print(
                    f"RESUME {ordinal:03d}/{len(unique_items):03d} "
                    f"{canonical.name} [{existing.get('status', 'unknown')}]",
                    flush=True,
                )
                continue

        print(
            f"START {ordinal:03d}/{len(unique_items):03d} {canonical.name} "
            f"({canonical.stat().st_size / 1048576:.2f} MB)",
            flush=True,
        )
        last_progress = (-1, "")

        def progress(percent: int | float, message: str) -> None:
            nonlocal last_progress
            bucket = int(float(percent)) // 10
            text = str(message or "")
            state = (bucket, text)
            if state != last_progress:
                print(
                    f"PROGRESS {ordinal:03d} {float(percent):5.1f}% {text}",
                    flush=True,
                )
                last_progress = state

        result: dict[str, Any] = {
            "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
            "capture_hash": digest,
            "canonical_file": str(canonical),
            "source_files": [str(path) for path in duplicates],
            "file_size": canonical.stat().st_size,
            "modified_at": canonical.stat().st_mtime,
            "status": "failed",
            "error": "",
        }
        core: NativeSimulatorCore | None = None
        try:
            core = NativeSimulatorCore(store, BuildCalculator(store))
            # Batch upload is performed synchronously after every local result
            # is safe, so prevent the interactive auto-sync hook from running.
            core._queue_replay_score_sync = lambda *args, **kwargs: None
            # Parsing cache is safe because its identity includes the parser
            # fingerprint. Keep it enabled for the current batch when asked,
            # but never reuse a completed score: this run must apply the latest
            # formulas even when an older exact-result cache exists.
            if os.getenv("DRAGON_NEST_BATCH_USE_REPLAY_CACHE", "0") != "1":
                core._replay_disk_cache = _BatchReplayCacheDisabled()
            core._team_score_disk_cache = _BatchScoreCacheDisabled()
            replay = core.load_replay(str(canonical), progress)
            result["replay"] = replay_metadata(replay)
            valid_players = [
                player
                for player in result["replay"]["players"]
                if str(player.get("job") or "").strip() not in {"", "未知职业"}
                and int(player.get("damage") or 0) > 0
            ]
            if not valid_players or int(result["replay"].get("total_damage") or 0) <= 0:
                result["status"] = "unscored"
                result["error"] = "未形成可评分的已识别职业伤害记录"
                result["score_rows"] = []
                result["details"] = []
                result["nest"] = ""
            else:
                score = core.score_replay(
                    {
                        "sourceHash": replay["sourceHash"],
                        "sourceFile": str(canonical),
                        "bossTargetIds": [],
                        "bossPreset": "红龙佩达",
                        "glyphOverrides": {},
                    },
                    progress,
                )
                source_hash = str(replay["sourceHash"])
                score_key = str(score["scoreKey"])
                snapshot = core._replays.get(source_hash)
                raw_score_rows = tuple(core._score_results.get(score_key, ()))
                if snapshot is None or not raw_score_rows:
                    raise RuntimeError("评分完成后缺少可导出的完整结果")
                try:
                    score_cast_events = core.score_theory_planner.score_cast_events(
                        snapshot
                    )
                except Exception:
                    score_cast_events = snapshot.skill_cast_events
                score_snapshot = (
                    replace(snapshot, skill_cast_events=score_cast_events)
                    if score_cast_events
                    else snapshot
                )
                resolved_casts = tuple(core._skill_cast_payloads(score_snapshot))
                player_buff_rows = tuple(
                    dataclasses.asdict(buff)
                    for buff in score_snapshot.player_buff_series
                )
                details = []
                for raw_row in raw_score_rows:
                    source_id = str(raw_row.source_id).upper()
                    detail = dataclasses.asdict(raw_row)
                    detail["_compact_duration_seconds"] = float(
                        score_snapshot.elapsed_seconds
                    )
                    detail["_compact_actual_casts"] = [
                        cast
                        for cast in resolved_casts
                        if str(cast.get("source_id") or "").upper() == source_id
                    ]
                    detail["_compact_player_buffs"] = [
                        buff
                        for buff in player_buff_rows
                        if str(buff.get("target_id") or "").upper() == source_id
                    ]
                    details.append(compact_detail(detail))
                result["score_rows"] = score.get("rows", [])
                result["details"] = details
                result["character_identities"] = (
                    core._score_character_identities(score_snapshot)
                )
                encounters = core._score_encounters(source_hash, score_snapshot)
                map_ids, map_names = core._replay_map_evidence(source_hash)
                result["nest"] = resolve_score_nest(
                    (encounter.name for encounter in encounters),
                    capture_path=canonical,
                    target_ids=(
                        target_id
                        for encounter in encounters
                        for target_id in encounter.target_ids
                    ),
                    map_ids=map_ids,
                    map_names=map_names,
                )
                result["score_diagnostics"] = score.get("diagnostics", {})
                result["theory_planner"] = score.get("theoryPlanner", "")
                result["status"] = "scored" if details else "unscored"
                if not details:
                    result["error"] = "评分引擎没有生成角色评分"
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(f"ERROR {canonical.name}: {result['error']}", flush=True)
        finally:
            # Explicitly stop capture helpers, score-sync threads, jobs and the
            # process pool before the next large capture. Dropping the Python
            # reference alone leaves worker processes alive and lets memory
            # usage grow throughout a long batch.
            if core is not None:
                try:
                    core.shutdown()
                except Exception as exc:
                    print(
                        f"CLEANUP_WARNING {canonical.name}: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
            core = None
            gc.collect()
        if upload_each and result.get("status") == "scored":
            assert upload_service is not None
            try:
                result["upload"] = upload_capture_result(result, upload_service)
            except Exception as exc:
                result["upload"] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                atomic_json(result_path, result)
                atomic_json(
                    INDEX_PATH,
                    {
                        "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
                        "capture_directory": str(CAPTURE_DIR),
                        "file_count": len(paths),
                        "unique_capture_count": len(unique_items),
                        "rows": index_rows,
                        "stopped_on_upload_error": str(canonical),
                    },
                )
                raise RuntimeError(
                    f"{canonical.name} 上传失败，已停止后续评分：{exc}"
                ) from exc
            print(
                f"UPLOADED {ordinal:03d}/{len(unique_items):03d} {canonical.name}",
                flush=True,
            )
        result["processed_seconds"] = round(time.time() - started, 3)
        atomic_json(result_path, result)
        index_rows.append(
            {
                "capture_hash": digest,
                "canonical_file": str(canonical),
                "source_files": [str(path) for path in duplicates],
                "result_file": str(result_path),
                "status": result.get("status", "unknown"),
                "nest": result.get("nest", ""),
                "score_rows": len(result.get("score_rows", [])),
                "resumed": False,
            }
        )
        atomic_json(
            INDEX_PATH,
            {
                "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
                "capture_directory": str(CAPTURE_DIR),
                "file_count": len(paths),
                "unique_capture_count": len(unique_items),
                "rows": index_rows,
            },
        )
        print(
            f"DONE {ordinal:03d}/{len(unique_items):03d} {canonical.name} "
            f"[{result['status']}] rows={len(result.get('score_rows', []))}",
            flush=True,
        )

    if upload_each:
        uploaded = sum(
            1
            for row in index_rows
            if dict(
                json.loads(Path(row["result_file"]).read_text(encoding="utf-8")).get(
                    "upload"
                )
                or {}
            ).get("status")
            == "uploaded"
        )
        upload_payload: dict[str, Any] = {
            "status": "uploaded" if uploaded == len(unique_items) else "partial",
            "mode": "immediate_per_capture",
            "uploaded_captures": uploaded,
            "expected_captures": len(unique_items),
            "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
            "service_version": upload_service_version,
        }
    else:
        # Legacy mode: stage and upload the completed batch together.
        staged = 0
        with TeamScoreSyncStore() as sync_store:
            for row in index_rows:
                result_path = Path(row["result_file"])
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if result.get("status") != "scored":
                    continue
                rows = export_rows(result)
                if not rows:
                    continue
                sync_store.record_capture(
                    Path(result["canonical_file"]),
                    str(result.get("nest") or "未识别巢穴"),
                    rows,
                    force_supabase_sync=True,
                )
                staged += 1
            pending_before = sync_store.pending_supabase_count()

        upload_payload = {
            "staged_captures": staged,
            "pending_before": pending_before,
            "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
        }
        config = load_v410_score_sync_config()
        if config.ready:
            try:
                service = TeamScoreSupabaseSyncService(
                    config.endpoint,
                    config.upload_token,
                    config.installation_id,
                    timeout_seconds=30.0,
                )
                upload_payload["service_version"] = service.health_check()
                with TeamScoreSyncStore() as sync_store:
                    sync_result = service.sync_store(
                        sync_store,
                        batch_size=10,
                        max_batches=1000,
                        upload_capture_files=False,
                    )
                upload_payload["status"] = "uploaded"
                upload_payload["result"] = {
                    key: getattr(sync_result, key)
                    for key in sync_result.__dataclass_fields__
                }
            except Exception as exc:
                upload_payload["status"] = "failed"
                upload_payload["error"] = f"{type(exc).__name__}: {exc}"
        else:
            upload_payload["status"] = "not_configured"
            upload_payload["error"] = "Supabase 上传配置未就绪"

    atomic_json(OUTPUT_DIR / "upload_result.json", upload_payload)
    atomic_json(
        INDEX_PATH,
        {
            "score_schema_version": CURRENT_SCORE_SCHEMA_VERSION,
            "capture_directory": str(CAPTURE_DIR),
            "file_count": len(paths),
            "unique_capture_count": len(unique_items),
            "rows": index_rows,
            "upload": upload_payload,
            "elapsed_seconds": round(time.time() - started, 3),
        },
    )
    print(f"UPLOAD {json.dumps(upload_payload, ensure_ascii=False)}", flush=True)
    print(f"COMPLETE {INDEX_PATH}", flush=True)


if __name__ == "__main__":
    main()
