"""Compare a capture's damage, Boss identities and terminal wire evidence."""
from __future__ import annotations

import argparse
import cProfile
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import struct
import sys
import time
import pstats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.replay_cache import ReplayCacheIdentity, ReplayDiskCache
from dragon_nest_builder.live_dps.boss_recognizer import BossRecognizer
from dragon_nest_builder.live_dps.boss_hp import resolve_fixed_boss_hp_profile, normalize_boss_hp_name
from tools.analyze_npcap_trace import read_trace
from tools.audit_s2c_2210_counters import _parse_capture
from tools.audit_full_tcp_key_table import load_full_table


def load_cached_aggregator(path: Path):
    directory = path.parent.parent / 'replay_cache'
    for manifest_path in directory.glob('*/manifest.json'):
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('sourcePath') != str(path):
            continue
        for cache_path in manifest_path.parent.glob('*.replaycache'):
            with cache_path.open('rb') as handle:
                if handle.read(6) != b'DNRC2\x00':
                    continue
                size = struct.unpack('<I', handle.read(4))[0]
                header = json.loads(handle.read(size))
            identity = ReplayCacheIdentity(header['key'], header['capture_hash'], header['decoder_fingerprint'], str(path))
            agg = ReplayDiskCache(directory).load(identity)
            if agg:
                return agg
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('capture', type=Path)
    parser.add_argument('--cached', action='store_true', help='Audit the existing decoded baseline')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile-overlay', action='store_true')
    parser.add_argument('--benchmark-overlay', action='store_true')
    args = parser.parse_args()
    started = time.perf_counter()
    path = args.capture.resolve()
    agg = None
    if args.cached:
        agg = load_cached_aggregator(path)
        if agg:
            print('loaded trusted local baseline', flush=True)
    if agg is None:
        known = ClientProfile.load(DATA_DIR / 'live_dps_profiles/v576.json').known_players
        source = V576DiagnosticReplaySource(path, DATA_DIR, known)
        try:
            events = source.poll()
        finally:
            source.close()
        print('decoded', len(events), 'events', flush=True)
        agg = CombatAggregator()
        for event in events:
            agg.ingest(event)
    if args.profile_overlay:
        agg._attribution_input_signature = None
        profiler = cProfile.Profile()
        profiler.runcall(agg.overlay_snapshot, 2_020_000)
        pstats.Stats(profiler).sort_stats('cumulative').print_stats(25)
    benchmark = {}
    if args.benchmark_overlay:
        agg._attribution_input_signature = None
        tick = time.perf_counter()
        detailed = agg.overlay_snapshot(2_020_000)
        benchmark['detailed_ms'] = (time.perf_counter() - tick) * 1000
        samples = []
        for _ in range(20):
            tick = time.perf_counter()
            lightweight = agg.overlay_snapshot(2_020_000, refresh_composition=False)
            samples.append((time.perf_counter() - tick) * 1000)
        benchmark['lightweight_mean_ms'] = sum(samples) / len(samples)
        benchmark['lightweight_max_ms'] = max(samples)
        for key in ('total_damage', 'total_healing', 'raw_damage_total', 'damage_series', 'player_buffs', 'boss_effects', 'player_health'):
            assert detailed[key] == lightweight[key], key
        print('overlay benchmark', benchmark, flush=True)
    snapshot = agg.snapshot(include_events=True)
    print('snapshot ready', round(time.perf_counter()-started, 3), flush=True)
    groups = BossRecognizer().recognize_snapshot(snapshot)
    label, references = resolve_fixed_boss_hp_profile((g.name for g in groups), capture_hint=path.stem, target_ids=(t for g in groups for t in g.target_ids))
    bosses = []
    for g in groups:
        hp = references.get(normalize_boss_hp_name(g.name), ())
        bosses.append({'name':g.name, 'target_ids':sorted(g.target_ids), 'damage':g.damage, 'max_hp':sum(hp)//len(hp) if hp else g.max_hp, 'fixed_hp_values':hp, 'first_ms':g.first_ms,'last_ms':g.last_ms})
    event_damage = Counter()
    for e in snapshot.combat_events:
        if not e.healing:
            event_damage[(e.source_id,e.source_name,e.job,e.target_id,e.target_name,e.skill_name,e.wire_damage_layout)] += e.amount
    buckets = defaultdict(lambda: {'frames':0,'bytes':0,'adapters':Counter(),'flows':Counter()})
    origin = None
    end = 0
    for timestamp,adapter,length,frame in read_trace(path):
        if adapter >= 250:
            continue
        if origin is None:
            origin=timestamp
        at = max(0,timestamp-origin)
        end=max(end,at)
        row=buckets[int(at//60)]
        row['frames']+=1;row['bytes']+=len(frame);row['adapters'][adapter]+=1
    key_path=ROOT/'outputs/client_unpack_with_keys/tcp_key_table_v576.json'
    counter_rows,_ = _parse_capture(path,load_full_table(key_path)) if key_path.exists() else ([],None)
    report={'capture':str(path),'elapsed_ms':round((time.perf_counter()-started)*1000), 'frame_duration_s':end,'total_damage':snapshot.total_damage,'raw_damage':snapshot.raw_damage_total,'unresolved_damage':snapshot.unresolved_damage_total,'completion':asdict(snapshot.instance_completion) if is_dataclass(snapshot.instance_completion) else snapshot.instance_completion,'profile':label,'bosses':bosses,'players':[{'id':p.source_id,'name':p.name,'job':p.job,'damage':p.damage,'skills':[asdict(s) for s in p.skills]} for p in snapshot.players], 'targets':[asdict(t) for t in snapshot.damage_targets], 'minute_frames':{k:{'frames':v['frames'],'bytes':v['bytes'],'adapters':dict(v['adapters'])} for k,v in buckets.items()},'terminal_2210':[asdict(r) for r in counter_rows[-24:]], 'damage_buckets':[{'source':k[0],'name':k[1],'job':k[2],'target':k[3],'target_name':k[4],'skill':k[5],'kind':k[6],'damage':v} for k,v in event_damage.most_common()]}
    report['overlay_benchmark'] = benchmark
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('total_damage','raw_damage','completion','profile','bosses','terminal_2210')},ensure_ascii=False,default=str),flush=True)
    print(args.output,flush=True)


if __name__ == '__main__':
    main()
