"""Exercise shipped planner methods without monkey patches or score uploads."""
import copy
import hashlib
import json
import pickle
import struct
import time
import zlib
from pathlib import Path

import benchmark_berserker_fury_side as util
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.castability import local_windows


def main():
    bundle = pickle.loads(Path('outputs/verification/ice1018_specs_before.pkl').read_bytes())
    original = next(s for s in bundle['specs'] if s['job'] == '狂战士')
    cache = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/replay_cache/'
        '1018黑典冰灵--c91e20ea0e40--66b999d8/'
        '474613a2ba7f8117a44a8c35f4967486613f05d01b447e16ae2a8b6fdfafe4e6.replaycache')
    b = cache.read_bytes(); n = struct.unpack_from('<I', b, 6)[0]
    header = json.loads(b[10:10+n]); data = zlib.decompress(b[10+n:])
    assert hashlib.sha256(data).hexdigest() == header['payload_sha256']
    snapshot = pickle.loads(data).snapshot(include_events=True)
    app = util.QApplication.instance() or util.QApplication([])
    store = util.DataStore.load(); calculator = util.BuildCalculator(store)
    engine = TeamScoreEngine(store)
    started = time.perf_counter()
    evidence = engine.castability_by_source(snapshot)['0x508C947F']
    report = dict(evidence_seconds=time.perf_counter()-started, runs=[])
    assert engine.castability_by_source(snapshot)['0x508C947F'] is evidence
    for scenario in ('125', 'capture'):
        for mode in ('old', 'gated', 'prep_gated'):
            spec = copy.deepcopy(original)
            if scenario == '125':
                spec.update(window=125., blocked_intervals=[], player_buff_clear_times=(), summon_removal_schedule=())
                spec.pop('boss_hp_segments', None)
            build = spec['build_override']
            build['_fury_key_node_search'] = mode != 'old'
            if mode == 'prep_gated':
                offset = build['_score_v2_time_offset']
                build['_score_castability_windows'] = local_windows(evidence.windows, offset, spec['window'])
                build['_score_uncastable_windows'] = local_windows(evidence.unavailable_windows, offset, spec['window'])
            editor = util.BuildEditor('production-fury-verification', store, calculator,
                                      store.defaults['build1'], standardize_default=True)
            began = time.perf_counter()
            result = editor._extreme_timeline_result_from_spec(spec)
            elapsed = time.perf_counter()-began
            preps = [s for s in result['segments'] if s.get('kind') == 'prep']
            assert all(s['effective_score'] == 0 and s['raw_effective_score'] == 0 for s in preps)
            assert all(any(a <= s['start'] < b and s['end'] <= b+1e-6 for a,b in spec['blocked_intervals']) for s in preps)
            counted = copy.deepcopy(result)
            for s in counted['segments']:
                if s.get('kind') == 'prep': s['kind'] = 'support'
            stats = util.stats_for(counted)  # Includes fury conservation audit.
            row = dict(scenario=scenario, mode=mode, seconds=elapsed,
                       preparations=len(preps), **stats)
            report['runs'].append(row)
            Path('outputs/verification/fury_production.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k:v for k,v in row.items() if k!='opening'}, ensure_ascii=False), flush=True)
            editor.deleteLater(); app.processEvents()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
