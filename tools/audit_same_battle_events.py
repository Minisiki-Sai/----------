"""Compare real decoded Hits/casts without inventing missing damage.

--game-host is audit-only: inspect an already proven game-server stream in a
trace polluted with unrelated traffic. Original files are never rewritten.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent, BossSpawnEvent
from dragon_nest_builder.live_dps.protocol_v576 import PacketRecord


class AuditSource(V576DiagnosticReplaySource):
    game_host = ''

    def _read_predecoded_frames(self):
        for row in super()._read_predecoded_frames():
            packet = row[2]
            if (self.game_host and isinstance(packet, PacketRecord)
                    and self.game_host not in (packet.source_ip, packet.destination_ip)):
                continue
            yield row


def read_events(path, host):
    source = AuditSource(path, DATA_DIR, ClientProfile.load(DATA_DIR / 'live_dps_profiles/v576.json').known_players)
    source.game_host = host
    try:
        events = source.poll()
    finally:
        source.close()
    print(path.name, len(events), 'events', flush=True)
    hits = Counter()
    detail = {}
    casts = Counter()
    damages = Counter()
    cast_layouts = Counter()
    births = []
    for event in events:
        if isinstance(event, CombatEvent) and not event.healing:
            key = (event.wire_source_actor_id or event.source_id,
                   event.wire_target_actor_id or event.target_id.split('@')[0],
                   event.amount, event.critical, event.wire_damage_layout,
                   event.wire_status_skill_id)
            hits[key] += 1
            detail[key] = {'ms': event.timestamp_ms, 'name': event.source_name,
                           'skill': event.skill_name, 'target': event.target_name,
                           'amount': event.amount, 'layout': event.wire_damage_layout}
            damages[event.source_name] += event.amount
        elif isinstance(event, SkillCastEvent):
            casts[(event.source_name, event.skill_name)] += 1
            cast_layouts[(event.source_name, event.skill_name, event.packet_kind)] += 1
        elif isinstance(event, BossSpawnEvent):
            births.append(asdict(event))
    return {'path': str(path), 'auditGameHost': host, 'events': len(events),
            'rawDamage': sum(k[2]*v for k, v in hits.items()),
            'players': dict(damages),
            'castLayouts': [{'key': k, 'count': v} for k, v in cast_layouts.most_common()],
            'births': births,
            'pipeline': source.replay_pipeline_stats}, hits, detail, casts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('captures', nargs=2, type=Path)
    parser.add_argument('--game-host', default='')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    start = time.perf_counter()
    a, hits_a, detail_a, casts_a = read_events(args.captures[0], args.game_host)
    b, hits_b, detail_b, casts_b = read_events(args.captures[1], args.game_host)
    differences = []
    for label, counter, details in [('leftOnly', hits_a-hits_b, detail_a), ('rightOnly', hits_b-hits_a, detail_b)]:
        phases = defaultdict(lambda: Counter())
        for key, count in counter.items():
            row = details[key]
            phases[row['ms']//30000]['hits'] += count
            phases[row['ms']//30000]['damage'] += key[2]*count
        differences.append({'side': label, 'hits': sum(counter.values()),
                            'damage': sum(key[2]*count for key, count in counter.items()),
                            'phase30s': {k: dict(v) for k, v in sorted(phases.items())},
                            'samples': [{**details[key], 'count': count}
                                        for key, count in sorted(counter.items(), key=lambda kv: -kv[0][2]*kv[1])[:40]]})
    report = {'captures': [a, b], 'differences': differences,
              'castDifferences': [{'name': k[0], 'skill': k[1], 'left': casts_a[k], 'right': casts_b[k]}
                                  for k in sorted(casts_a.keys() | casts_b.keys()) if casts_a[k]!=casts_b[k]],
              'elapsedSeconds': round(time.perf_counter()-start, 3)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'captures': [{k:r[k] for k in ('path','events','rawDamage','players')} for r in [a,b]],
                      'differences': [{k:r[k] for k in ('side','hits','damage','phase30s')} for r in differences],
                      'castDifferences': report['castDifferences']}, ensure_ascii=False), flush=True)


if __name__=='__main__':
    main()
