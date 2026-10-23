"""Offline scoring with unmodified published bytecode/data, in isolated homes.

Run each case in a fresh interpreter; never import the current working tree.
Only I/O/configuration/cache hooks are changed, not scoring or parsing methods.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import sys
import time
import traceback

ENGINE = Path(os.environ['AB_ENGINE_ROOT']).resolve()
STATE = Path(os.environ['AB_STATE_ROOT']).resolve()
STATE.mkdir(parents=True, exist_ok=True)
for name in ('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA'):
    os.environ[name] = str(STATE / name)
    Path(os.environ[name]).mkdir(parents=True, exist_ok=True)
os.environ['DRAGON_NEST_NATIVE_NO_QT'] = '1'
sys._MEIPASS = str(ENGINE)
os.chdir(ENGINE)

class PublishedPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in {'dragon_nest_builder', 'tools'}:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, [str(ENGINE)] if path is None else path)
        if spec is None:
            raise ImportError(f'Published package missing: {fullname}')
        return spec

sys.meta_path.insert(0, PublishedPackages())

def offline(event, args):
    if event == 'socket.connect':
        raise PermissionError('A/B diagnostic: network connections disabled')

sys.addaudithook(offline)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument('capture', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--version', required=True)
    p.add_argument('--decode-only', action='store_true')
    a = p.parse_args()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    # Allow the last case to overlap the other version's finished lane without
    # scoring it twice if the batch coordinator reaches it before it completes.
    lock_path = a.output.with_suffix('.running.lock')
    while True:
        if a.output.exists():
            try:
                existing=json.loads(a.output.read_text(encoding='utf-8'))
                if existing.get('status')=='ok' and existing.get('captureSha256')==digest(a.capture) and existing.get('version')==a.version:
                    print('Already completed by this diagnostic batch: '+str(a.output),flush=True)
                    return 0
            except (ValueError,OSError):
                pass
        try:
            fd=os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            os.write(fd,str(os.getpid()).encode('ascii'));os.close(fd)
            break
        except FileExistsError:
            time.sleep(2)
    from dragon_nest_builder.live_dps import v410_score_sync as sync
    sync.winreg = None
    original_config = sync.load_v410_score_sync_config
    sync.load_v410_score_sync_config = lambda: dataclasses.replace(original_config(), enabled=False)
    sync.schedule_usage_ping = lambda *args, **kwargs: None
    sync.V410ScoreSyncManager.enqueue = lambda *args, **kwargs: None
    sync.V410ScoreSyncManager._start_benchmark_prefetch = lambda *args, **kwargs: None
    from dragon_nest_builder.calculator import BuildCalculator
    from dragon_nest_builder.data_loader import DataStore
    from dragon_nest_builder.v410_native_core import NativeSimulatorCore
    from dragon_nest_builder.live_dps.team_score_sync import CURRENT_SCORE_SCHEMA_VERSION
    report = {'version': a.version, 'schema': CURRENT_SCORE_SCHEMA_VERSION,
              'engineRoot': str(ENGINE), 'capture': str(a.capture),
              'captureSha256': digest(a.capture), 'networkDisabled': True,
              'freshReplayAndScore': True, 'progress': []}
    started = time.perf_counter()
    def progress(value, message):
        row = {'seconds': round(time.perf_counter()-started, 2), 'value': value, 'message': message}
        report['progress'].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        store = DataStore.load()
        core = NativeSimulatorCore(store, BuildCalculator(store))
        for attr in ('_replay_disk_cache', '_team_score_disk_cache'):
            cache = getattr(core, attr, None)
            if cache:
                cache.load = lambda *args, **kwargs: None
                cache.save = lambda *args, **kwargs: None
        report['initSeconds'] = time.perf_counter()-started
        t = time.perf_counter()
        replay = core.load_replay(str(a.capture), progress)
        report['decodeSeconds'] = time.perf_counter()-t
        report['replay'] = replay
        # Persist decoding metadata even if an old scorer cannot handle a new nest.
        a.output.with_suffix('.replay.json').write_text(json.dumps(report, ensure_ascii=False, default=str), encoding='utf-8')
        if not a.decode_only:
            t = time.perf_counter()
            score = core.score_replay({'sourceHash': replay['sourceHash'], 'sourceFile': replay['sourceFile']}, progress)
            report['scoreSeconds'] = time.perf_counter()-t
            report['score'] = score
            report['scoreRowsDetailed'] = [dataclasses.asdict(row) for row in core._score_results.get(score.get('scoreKey',''), ())]
            report['theoryDiagnostics'] = getattr(core.score_theory_planner, '_team_score_theory_diagnostics', {})
            report['plans'] = {str(k): {key: value.get(key) for key in ('total','raw_total','average','dps','summary','segments','effect_windows','external_windows','_score_skill_damages','_score_skill_powers','_score_skill_components','_score_time_offset','_score_actual_cast_segments','_score_v2','_score_excluded_skill_keys')}
                               for k,value in core.score_theory_planner._team_score_theory_plans.items() if isinstance(value,dict)}
        report['moduleOrigins'] = {n: str(getattr(m,'__file__','')) for n,m in sys.modules.items() if n.startswith('dragon_nest_builder')}
        assert all(str(ENGINE).casefold() in f.casefold() for f in report['moduleOrigins'].values() if f)
        report['status'] = 'ok'
    except BaseException:
        report['status'] = 'error'
        report['error'] = traceback.format_exc()
        print(report['error'], flush=True)
    report['elapsedSeconds'] = time.perf_counter()-started
    a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    lock_path.unlink(missing_ok=True)
    print(json.dumps({k:report.get(k) for k in ('version','schema','status','elapsedSeconds','decodeSeconds','scoreSeconds')},ensure_ascii=False), flush=True)
    return 0 if report['status']=='ok' else 1

if __name__ == '__main__':
    raise SystemExit(main())
