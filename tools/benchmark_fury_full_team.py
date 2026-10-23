"""Local A/B of the complete native score_replay path; never upload or capture."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import pickle
import struct
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.dont_write_bytecode = True


def source_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'dragon_nest_builder').rglob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('old', 'new'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--warm-only', action='store_true')
    args = parser.parse_args()
    from dragon_nest_builder import v410_native_core as native
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.live_dps import theory_plan_builder as theory
    from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
    from dragon_nest_builder.live_dps import score_hit_evidence as hit
    from dragon_nest_builder.timeline_parallel import configured_formal_score_workers, formal_score_worker_budget

    # Suppress unrelated maintenance, live acquisition and all cloud activity.
    native.schedule_history_maintenance = lambda: None
    native.NativeLiveCapture = lambda *a, **kw: SimpleNamespace(close=lambda: None)
    native.V410ScoreSyncManager = lambda: SimpleNamespace(
        status=lambda: {}, close=lambda: None, enqueue=lambda *a, **kw: None)
    if args.mode == 'old':
        TeamScoreEngine.castability_by_source = lambda self, snapshot: {}
        original_build = theory._team_score_without_damage_glyphs

        def old_build(build):
            result = original_build(build)
            if isinstance(result, dict):
                result['_fury_key_node_search'] = False
                result.pop('_score_castability_windows', None)
                result.pop('_score_uncastable_windows', None)
            return result

        theory._team_score_without_damage_glyphs = old_build

    capture = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/1018黑典冰灵.dpscap')
    cache = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/replay_cache/1018黑典冰灵--c91e20ea0e40--66b999d8/474613a2ba7f8117a44a8c35f4967486613f05d01b447e16ae2a8b6fdfafe4e6.replaycache')
    source_before = source_digest()
    raw = cache.read_bytes()
    assert raw[:6] == b'DNRC2\x00'
    size = struct.unpack_from('<I', raw, 6)[0]
    header = json.loads(raw[10:10+size])
    data = zlib.decompress(raw[10+size:])
    assert hashlib.sha256(data).hexdigest() == header['payload_sha256']
    snapshot = pickle.loads(data).snapshot(include_events=True)
    content_hash = hashlib.sha256(capture.read_bytes()).hexdigest()
    store = DataStore.load()
    core = native.NativeSimulatorCore(store, BuildCalculator(store))
    core._team_score_disk_cache.load = lambda *a, **kw: None
    core._team_score_disk_cache.save = lambda *a, **kw: None
    core._queue_replay_score_sync = lambda *a, **kw: None
    core._replays[content_hash] = snapshot
    core._replay_sources[content_hash] = capture
    encounters = core._score_encounters(content_hash, snapshot)
    targets = frozenset(t for e in encounters for t in e.target_ids)
    # Same warmed Hit-input state in BOTH arms, but no theory/final-score cache.
    warming = time.perf_counter()
    hit_inputs = []
    for group in (targets, *(frozenset(e.target_ids) for e in encounters)):
        _, key, diagnostic = hit.load_report(capture, group, store,
            lambda p, m: print('WARM ' + m, flush=True), snapshot=snapshot)
        hit_inputs.append({'key': key, **diagnostic})
    warm_seconds = time.perf_counter() - warming
    from dragon_nest_builder.live_dps.hit_parallel import shutdown as shutdown_hit_workers
    shutdown_hit_workers()
    hit._PREPARED.clear()
    gc.collect()
    if args.warm_only:
        print(json.dumps(dict(warm_seconds=warm_seconds, hit_inputs=hit_inputs)), flush=True)
        core.shutdown()
        return
    # Fix parallel budget for comparability; preserve normal worker machinery.
    workers = min(4, configured_formal_score_workers(12))
    progress_rows = []
    started = time.perf_counter()
    last = [None, 0.]

    def progress(value, message):
        elapsed = time.perf_counter() - started
        progress_rows.append(dict(seconds=elapsed, value=value, message=message))
        if value != last[0] or elapsed - last[1] >= 15:
            print(f'{args.mode} {elapsed:.2f}s [{value}%] {message}', flush=True)
            last[:] = [value, elapsed]

    try:
        with formal_score_worker_budget(workers):
            score = core.score_replay(dict(sourceHash=content_hash,
                sourceFile=str(capture), localComparison=True), progress)
        seconds = time.perf_counter() - started
        plans = core.score_theory_planner._team_score_theory_plans
        report = dict(mode=args.mode, score_seconds=seconds, workers=workers,
            capture=str(capture), capture_hash=content_hash,
            snapshot_hash=header['payload_sha256'], source_before=source_before,
            source_after=source_digest(), hit_warm_seconds=warm_seconds,
            hit_inputs=hit_inputs, score=score, progress=progress_rows,
            theory_diagnostics=core.score_theory_planner._team_score_theory_diagnostics,
            plans={source: dict(job=p.get('job'), total=p.get('total'),
                dps=p.get('dps'), preparations=sum(s.get('kind') == 'prep'
                for s in p.get('segments', ()))) for source, p in plans.items()})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), 'utf-8')
        print(json.dumps(dict(mode=args.mode, seconds=seconds, workers=workers,
            players=len(score.get('rows', ())), planner=score.get('theoryPlanner'),
            source_unchanged=report['source_before']==report['source_after']), ensure_ascii=False), flush=True)
        assert score.get('theoryPlanner') == 'v401-full-team-score', score.get('theoryPlannerError')
        assert len(score.get('rows', ())) == 8
    finally:
        core.shutdown()


if __name__ == '__main__':
    main()
