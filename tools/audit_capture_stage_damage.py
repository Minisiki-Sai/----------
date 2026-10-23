"""Read-only audit of stage targets and independent TCP cumulative counters.

The counter is a candidate server cumulative total, NOT an invented damage
event. Never use it to pad missing skill hits or assume it equals UI DPS.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_capture_damage_and_latency import load_cached_aggregator
from tools.analyze_npcap_trace import read_trace
from tools.audit_s2c_2210_counters import _parse_capture
from tools.audit_full_tcp_key_table import load_full_table
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame, split_v576_payload, decode_v576_monster_spawn
from dragon_nest_builder.live_dps.models import BossSpawnEvent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('capture', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    agg = load_cached_aggregator(args.capture.resolve())
    if agg is None:
        raise SystemExit('Decode/cache the capture first')
    events = tuple(agg._display_combat_events)
    decoded_births = [e for e in agg._source_events if isinstance(e, BossSpawnEvent)]
    if not decoded_births:
        decoded_births = [BossSpawnEvent(t.first_ms,t.target_id,t.target_name,t.target_job,t.max_hp)
            for t in agg._boss_hp_ledger._tracks]
    raw_births = []
    for timestamp, adapter, original, frame in read_trace(args.capture):
        if adapter >= 250:
            continue
        packet = decode_captured_frame(timestamp, frame, original)
        if packet is None or packet.transport.upper() != 'UDP':
            continue
        for wire in split_v576_payload(packet.payload):
            birth = decode_v576_monster_spawn(wire, packet.inferred_direction())
            if birth:
                raw_births.append({'time': timestamp, 'actor': birth.actor_id, 'template': birth.template_id})
    # Independently align UDP spawn timestamps with their decoded event; the
    # file may start with older warm-up evidence, so first physical frame is
    # not a reliable event-time origin.
    origins = []
    for birth in decoded_births:
        try:
            actor, template = birth.target_id.split('@', 1)
            actor = int(actor, 16)
            template = int(template)
        except ValueError:
            continue
        candidates = [r for r in raw_births if (r['actor'], r['template']) == (actor, template)]
        if len(candidates) == 1:
            origins.append(candidates[0]['time'] - birth.timestamp_ms / 1000)
    counters, counter_origin = _parse_capture(args.capture, load_full_table(ROOT/'outputs/client_unpack_with_keys/tcp_key_table_v576.json'))
    origin = sorted(origins)[len(origins)//2] if origins else counter_origin
    if origin is None:
        raise SystemExit('No logical packets')
    if origins and max(origins)-min(origins) > 0.01:
        raise SystemExit('Spawn origin alignment is inconsistent')
    by_name = defaultdict(list)
    for row in counters:
        by_name[row.name].append((round((row.timestamp_seconds-origin)*1000), row.values['u64_76']))
    by_target = defaultdict(list)
    for event in events:
        if not event.healing:
            by_target[event.target_id].append(event)
    stages = []
    # Full phase windows leave no assumptions about overkill/shared HP.
    for name, start, stop in (
        ('百夫长', 0, 250000), ('军团长',250000, 620000),
        ('惩罚者',620000,960000), ('千夫长',960000,1170000),
        ('红龙',1170000,2020000),
    ):
        targets = Counter()
        for event in events:
            if not event.healing and start <= event.timestamp_ms < stop:
                targets[(event.target_id,event.target_name)] += event.amount
        comparisons = []
        for character, rows in by_name.items():
            before = [r for r in rows if r[0] <= start]
            after = [r for r in rows if r[0] <= stop]
            if before and after:
                comparisons.append({'name':character, 'start_sample':before[-1], 'end_sample':after[-1],
                    'counter_delta':after[-1][1]-before[-1][1], 'act_damage':sum(e.amount for e in events
                        if not e.healing and e.source_name == character and before[-1][0] < e.timestamp_ms <= after[-1][0])})
        stages.append({'name':name,'window_ms':[start,stop],
            'targets':[{'id':key[0],'name':key[1],'damage':amount} for key,amount in targets.most_common()],
            'counter_comparison':comparisons})
    report = {'origin':origin,'origin_method':'spawn alignment' if origins else 'minimum logical packet timestamp',
        'origin_spread_ms':(max(origins)-min(origins))*1000 if origins else None,
        'decoded_births':[asdict(r) for r in decoded_births],
        'raw_births':[{**r,'elapsed_ms':round((r['time']-origin)*1000)} for r in raw_births],
        'counter_endpoints':{name:{'first':rows[0], 'last':rows[-1], 'rows':len(rows)} for name,rows in by_name.items()},
        'stages':stages,
        'punisher_late_hits':[asdict(e) for e in by_target['0x0000006C@503652'] if e.timestamp_ms>960000]}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'origin':origin, 'spread_ms':report['origin_spread_ms'], 'counter_endpoints':report['counter_endpoints'],
        'late_hits':len(report['punisher_late_hits']), 'births':len(raw_births)},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
