"""Local-only scoring evidence for the split-Boss duty audit (no upload)."""
import argparse
import json
import pickle
import time
from dataclasses import asdict
from pathlib import Path

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('captures', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, default=Path('outputs/verification/sea_group_duties_20261018'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    core._score_sync.enqueue = lambda *args, **kwargs: None
    core._team_score_disk_cache.load = lambda key: None
    core._team_score_disk_cache.save = lambda *args, **kwargs: None
    for path in args.captures:
        artifact = args.output / (path.stem+'.pkl')
        if artifact.exists():
            print('already captured', path.name, flush=True)
            continue
        start = time.perf_counter()
        last = [-10]
        def progress(value, message):
            if value >= last[0]+10 or value>=98:
                print(path.name, round(time.perf_counter()-start,1), value, message, flush=True)
                last[0] = value
        replay = core.load_replay(str(path.resolve()), progress)
        last[0] = -10
        scored = core.score_replay({'sourceHash':replay['sourceHash'], 'sourceFile':str(path.resolve()),
                                    'localComparison':True}, progress)
        rows = core._score_results[scored['scoreKey']]
        plans = core.score_theory_planner._team_score_theory_plans
        snapshot = core._replays[replay['sourceHash']]
        payload = {'snapshot':snapshot, 'plans':plans, 'rows':rows, 'source':str(path), 'result':scored}
        artifact.write_bytes(pickle.dumps(payload, protocol=5))
        (args.output/(path.stem+'.json')).write_text(json.dumps({
            'seconds':time.perf_counter()-start, 'source':str(path),
            'rows':[asdict(row) for row in rows], 'result':scored,
        },ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print('SAVED', artifact, 'seconds', round(time.perf_counter()-start,1),flush=True)
        core._replays.clear()
        core._score_results.clear()


if __name__ == '__main__':
    main()
