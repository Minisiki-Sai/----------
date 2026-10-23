"""Inspect a locally generated replay cache; never publish it as a new cache."""
import argparse
import hashlib
import json
import pickle
import struct
import zlib
from collections import Counter
from pathlib import Path

from dragon_nest_builder.live_dps.periodic_health_mechanism import sea_tick_ids
from dragon_nest_builder.live_dps.nightmare_sea_dragon import matches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('cache', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = args.cache.read_bytes()
    assert data[:6] == b'DNRC2\x00'
    size = struct.unpack_from('<I', data, 6)[0]
    meta = json.loads(data[10:10+size])
    raw = zlib.decompress(data[10+size:])
    assert hashlib.sha256(raw).hexdigest() == meta['payload_sha256']
    agg = pickle.loads(raw)  # Only trusted local replay artifacts.
    print('cache loaded', flush=True)
    agg._healing_attribution_revision = -1
    agg._normalized_attribution_revision = -1
    snapshot = agg.snapshot()
    heals = [e for e in snapshot.combat_events if e.healing]
    ids = sea_tick_ids(agg._incoming_damage_events, sea_present=matches(agg._authoritative_boss_ids))
    result = {
        'cache': str(args.cache),
        'decoded_fingerprint': meta['decoder_fingerprint'],
        'health_layouts': dict(Counter(e.packet_kind for e in agg._player_health_events)),
        'raw_periodic_loss_records': len(ids),
        'raw_10000_losses': sum(e.amount == 10000 for e in agg._incoming_damage_events),
        'canonical_periodic_loss_records': sum(e.skill_id == 'nightmare_sea_periodic_hp_loss' for e in snapshot.incoming_damage_events),
        'healing_methods': dict(Counter(e.attribution_method for e in heals)),
        'raw_healing_layouts': dict(Counter(e.raw_attribution_method for e in agg._raw_healing_events)),
        'ledger_healing_methods': agg._healing_attribution_result.method_counts,
        'raw_hp_counts': dict(Counter(('positive' if e.hp_delta > 0 else 'negative') for e in agg._player_health_events if e.hp_delta)),
        'system_restore_statuses': dict(Counter(str(e.raw_skill_id or e.skill_id) for e in agg._skill_parameter_events
                                               if str(e.raw_skill_id or e.skill_id) in {'34709','32100','325408'})),
        'players': [{
            'name': p.name, 'job': p.job,
            'mechanism_loss': sum(e.amount for e in snapshot.incoming_damage_events
                                  if e.target_id == p.source_id and e.skill_id == 'nightmare_sea_periodic_hp_loss'),
            'unknown_recovery': sum(e.amount for e in heals if e.target_id == p.source_id and e.source_id == 'healing:unknown'),
        } for p in snapshot.players],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text, flush=True)
    if args.output:
        args.output.write_text(text, encoding='utf-8')


if __name__ == '__main__':
    main()
