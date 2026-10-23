"""Real-event backlog recovery experiment, NOT a live game/voice latency claim."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.catch_up import LiveCatchUpPolicy
from dragon_nest_builder.live_dps.history import LiveDpsHistoryStore
from dragon_nest_builder.live_dps.history_writer import AsyncLiveDpsHistoryStore

OUT = ROOT / "outputs/verification/history_catchup_20261020"
CAPTURES = Path.home() / "Documents/DragonNestDpsCapture/live_dps_diagnostics"


def percentile(values, p):
    return sorted(values)[min(len(values)-1, math.ceil(len(values)*p)-1)] if values else 0


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(
        dataclasses.asdict(snapshot), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode()).hexdigest()


def run(events, mode, *, blocked_seconds=0.0):
    """Start with a fully decoded queue, same ordered events and 512/256 batches.

    This explicitly isolates controller/statistics pacing. It cannot demonstrate
    improvement of raw decoder lag or audio output latency.
    """
    with tempfile.TemporaryDirectory(prefix="dn-history-ab-") as folder:
        path = Path(folder) / "history.sqlite3"
        history = (LiveDpsHistoryStore(path) if mode == "sync_fixed"
                   else AsyncLiveDpsHistoryStore(path))
        history.start_session("benchmark")
        history.flush()
        locked = threading.Event()
        def blocker():
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                locked.set()
                time.sleep(blocked_seconds)
                conn.rollback()
        locker = None
        agg = CombatAggregator()
        lock = threading.RLock()
        stop = threading.Event()
        waits = []
        def reader():
            # Measures availability of the same statistics lock, not full UI or
            # audio formatting. Keep workload identical across policies.
            while not stop.wait(.010):
                start = time.perf_counter()
                with lock:
                    _revision = agg.revision
                waits.append((time.perf_counter()-start)*1000)
        monitor = threading.Thread(target=reader)
        monitor.start()
        policy = LiveCatchUpPolicy(enabled=mode == "async_catchup")
        hold_ms = []
        pending_samples = []
        if blocked_seconds:
            locker = threading.Thread(target=blocker)
            locker.start()
            if not locked.wait(5):
                raise RuntimeError("Failed to acquire deliberate test database lock")
        start, cpu_start = time.perf_counter(), time.process_time()
        next_wait = .040
        position = 0
        first_applied_ms = None
        try:
            while position < len(events):
                time.sleep(next_wait)
                work_start = time.perf_counter()
                remaining = len(events)-position
                batch = events[position:position+(512 if remaining >= 512 else 256)]
                with lock:
                    held = time.perf_counter()
                    if mode == "sync_fixed":
                        history.append(batch)
                    for event in batch:
                        agg.ingest(event)
                    hold_ms.append((time.perf_counter()-held)*1000)
                if mode != "sync_fixed":
                    history.append(batch)
                position += len(batch)
                elapsed = time.perf_counter()-start
                if first_applied_ms is None:
                    first_applied_ms = elapsed*1000
                pending_samples.append((elapsed, len(events)-position))
                next_wait = policy.next_wait(
                    len(events)-position, time.perf_counter()-work_start,
                )
            drained = time.perf_counter()-start
            cpu = time.process_time()-cpu_start
        finally:
            stop.set()
            monitor.join()
            if locker:
                locker.join()
        snapshot = agg.snapshot(include_events=False)
        history.finish_session(snapshot)
        history.close()
        with closing(sqlite3.connect(path)) as conn:
            db_summary = conn.execute(
                "SELECT event_count,total_damage,total_healing,player_count FROM sessions"
            ).fetchone()
        return {
            "mode": mode, "blocked_seconds": blocked_seconds,
            "events": len(events), "drain_seconds": drained,
            "process_cpu_seconds": cpu, "first_batch_ms": first_applied_ms,
            "statistics_lock_max_ms": max(hold_ms),
            "statistics_lock_p99_ms": percentile(hold_ms, .99),
            "reader_wait_max_ms": max(waits, default=0),
            "reader_wait_p99_ms": percentile(waits, .99),
            "snapshot_sha256": fingerprint(snapshot),
            "database_summary": db_summary,
            "pending_samples": pending_samples,
            "writer": history.diagnostics() if hasattr(history, "diagnostics") else None,
        }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in sys.argv[1:] or ["第十一把", "第十四把", "第十九把"]:
        path = CAPTURES / f"1019噬魂竞速{name}.dpscap"
        print(f"Decoding {path.name}", flush=True)
        source = V576DiagnosticReplaySource(path, ROOT/"dragon_nest_builder/data")
        start = time.perf_counter()
        try:
            events = source.poll()
        finally:
            source.close()
        result = {
            "file": path.name, "decode_seconds": time.perf_counter()-start,
            "events": len(events),
            "scope": "预先解码后的事件队列清空实验；不包含游戏、原包解码竞争或音频播放",
            "runs": [],
        }
        # Symmetric order reduces warm-cache/temperature/order bias.
        for mode in ["sync_fixed", "async_fixed", "async_catchup",
                     "async_catchup", "async_fixed", "sync_fixed"]:
            row = run(events, mode)
            result["runs"].append(row)
            print(path.name, mode, round(row["drain_seconds"], 3),
                  row["snapshot_sha256"][:12], flush=True)
            (OUT/f"{path.stem}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        result["all_results_identical"] = (
            len({r["snapshot_sha256"] for r in result["runs"]}) == 1
            and len({tuple(r["database_summary"]) for r in result["runs"]}) == 1
        )
        if name == "第十一把":
            result["database_block_runs"] = [
                run(events[:4096], mode, blocked_seconds=1.0)
                for mode in ("sync_fixed", "async_fixed")
            ]
            # Backlog SIZES actually seen in historical diagnostics. Prefix
            # events are reused as workload, not a recreation of those moments.
            result["sampled_backlog_size_runs"] = [
                run(events[:count], mode)
                for count in (485, 1427, 3014)
                for mode in ("async_fixed", "async_catchup",
                             "async_catchup", "async_fixed")
            ]
        (OUT/f"{path.stem}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        assert result["all_results_identical"], "Snapshot/persistence diverged"


if __name__ == "__main__":
    main()
