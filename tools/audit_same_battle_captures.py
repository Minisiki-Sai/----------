"""Read-only raw inventory for independent captures of the same battle.

Capture clocks, observers and wire message IDs can differ between machines.
Never interpret file-size differences alone as packet loss, or pad Hit data
from TCP totals. This tool counts IP fragments and exact closed UDP bodies.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import blake2b
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    _extract_ipv4_packet, decode_captured_frame, split_v576_payload,
    decode_v576_monster_spawn,
)


def inventory(path: Path):
    flows = defaultdict(lambda: Counter())
    bodies = Counter()
    signatures = Counter()
    fragments = Counter()
    births = []
    metadata = []
    stats = Counter()
    origin = None
    for stamp, adapter, original, frame in read_trace(path):
        stats['frames'] += 1
        stats['bytes'] += len(frame)
        if adapter >= 251:
            stats['metadata_frames'] += 1
            if frame.startswith((b'DNPB1\0', b'DNPI1\0')):
                try:
                    payload = json.loads(frame[6:])
                    metadata.append({'adapter': adapter, 'keys': sorted(payload),
                                     'prebattle_state_keys': sorted(payload.get('prebattle_state', {}))})
                except (ValueError, TypeError):
                    pass
            continue
        if origin is None:
            origin = stamp
        stats['truncated_frames'] += int(len(frame) < original)
        ip = _extract_ipv4_packet(frame)
        if ip:
            fragment = int.from_bytes(ip[6:8], 'big')
            if fragment & 0x3fff:
                kind = 'first' if fragment & 0x1fff == 0 else 'continuation'
                key = ('.'.join(map(str, ip[12:16])), '.'.join(map(str, ip[16:20])), ip[9], kind)
                fragments[key] += 1
                stats['ip_fragment_frames'] += 1
        packet = decode_captured_frame(stamp, frame, original)
        if packet is None:
            stats['undecodable_frames'] += 1
            continue
        flow = (packet.transport, packet.source_ip, packet.source_port,
                packet.destination_ip, packet.destination_port, packet.inferred_direction())
        row = flows[flow]
        row['frames'] += 1
        row['bytes'] += len(frame)
        if packet.transport != 'UDP':
            continue
        payload = packet.payload
        closed = len(payload) >= 9 and int.from_bytes(payload[7:9], 'little') + 3 == len(payload)
        row['closed_udp'] += int(closed)
        if not closed:
            continue
        for logical in split_v576_payload(payload):
            signatures[(len(logical), logical[5:9].hex(), packet.inferred_direction())] += 1
            # Ignore per-recipient reliable-UDP IDs. Do not assume this is a
            # canonical Hit: actor/resource records can still differ normally.
            bodies[(len(logical), logical[5:9].hex(),
                    blake2b(logical[9:], digest_size=16).hexdigest())] += 1
            birth = decode_v576_monster_spawn(logical, packet.inferred_direction())
            if birth:
                births.append({'seconds': round(stamp-origin, 3), 'actor': birth.actor_id,
                               'template': birth.template_id})
    return {
        'capture': str(path), 'fileBytes': path.stat().st_size, 'stats': dict(stats),
        'flows': [{'flow': key, **dict(row)} for key, row in sorted(flows.items(), key=lambda kv: -kv[1]['bytes'])[:30]],
        'fragments': [{'key': key, 'count': count} for key, count in fragments.most_common(20)],
        'signatures': [{'key': key, 'count': count} for key, count in signatures.most_common()],
        'births': births, 'metadata': metadata,
    }, bodies


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('captures', nargs=2, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    start = time.perf_counter()
    left, a = inventory(args.captures[0])
    print('first inventory finished', flush=True)
    right, b = inventory(args.captures[1])
    only_a = Counter((key[:2] for key in a.keys() - b.keys()))
    only_b = Counter((key[:2] for key in b.keys() - a.keys()))
    report = {'captures': [left, right], 'elapsedSeconds': round(time.perf_counter()-start, 2),
              'closedBodies': {'left': len(a), 'right': len(b), 'shared': len(a.keys() & b.keys()),
                               'leftOnlyBySignature': [{'key': k, 'uniqueBodies': v} for k, v in only_a.most_common()],
                               'rightOnlyBySignature': [{'key': k, 'uniqueBodies': v} for k, v in only_b.most_common()]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'elapsedSeconds': report['elapsedSeconds'],
                      'captures': [{'fileBytes': r['fileBytes'], 'stats': r['stats'],
                                    'topFlows': r['flows'][:4], 'fragments': r['fragments']} for r in [left, right]],
                      'closedBodies': report['closedBodies']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
