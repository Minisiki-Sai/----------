"""Replay the local 1009 capture; no capture or cloud score mutations."""
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.hit_analyzer import SkillHitAnalyzer


def main():
    path = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/1009竞速通关噬魂.dpscap')
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    aggregator = CombatAggregator()
    print('Replay started', flush=True)
    try:
        events = source.poll()
        for event in events:
            aggregator.ingest(event)
        snapshot = aggregator.snapshot(include_events=True)
    finally:
        source.close()
    ids = {r.source_id for r in snapshot.players if r.job in {'曜', '暗', '打坐曜', '输出曜'}}
    hits = [e for e in snapshot.combat_events if e.source_id in ids and not e.healing]
    casts = [e for e in snapshot.skill_cast_events if e.source_id in ids]
    buff_rows = [asdict(e) for e in snapshot.player_buff_series if e.target_id in ids]
    ring_hits = [e for e in hits if '圆环强击' in e.skill_name]
    ring_casts = [e for e in casts if '圆环强击' in e.skill_name]
    analyzer = SkillHitAnalyzer()
    observations = analyzer.analyze_events(casts, hits, str(path))
    rows = [asdict(r) for r in observations if '圆环强击' in r.skill_name]
    result = {'source_file': str(path), 'elapsed': snapshot.elapsed_seconds,
              'players': [asdict(r) for r in snapshot.players if r.source_id in ids],
              'ring_hits': [asdict(e) for e in ring_hits],
              'ring_casts': [asdict(e) for e in ring_casts],
              'buffs': buff_rows, 'observations': rows,
              'native_hit_count': sum(e.has_real_hit_evidence is True for e in ring_hits),
              'damage_records': len(ring_hits),
              'damage': sum(e.amount for e in ring_hits),
              'attributions': dict(Counter(e.attribution_method for e in ring_hits))}
    output = ROOT / 'work' / 'chakra_ring_1009_before.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'saved': str(output), 'players': [(r.name, r.job) for r in snapshot.players if r.source_id in ids],
                      'casts': len(ring_casts), 'hits': result['native_hit_count'], 'damage_records': len(ring_hits),
                      'damage': result['damage'], 'observations': [(r['cast_timestamp_ms'], len(r['hit_times'])) for r in rows]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
