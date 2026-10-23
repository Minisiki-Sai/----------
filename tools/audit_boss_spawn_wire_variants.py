"""Read-only search for template IDs independently of the spawn parser."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_captured_frame, split_v576_payload, _v576_wire_may_contain_marker,
    _decode_v576_body, _V576_XOR_PHASE_BY_SIGNATURE_BYTE,
)


def scan(path, ids=None):
    ids = ids or [4180002, 4180003, 4180004, 4180005, 4180006]
    markers = tuple(t.to_bytes(4, 'little') for t in ids)
    rows = []
    counts = Counter()
    signatures = Counter()
    origin = None
    boundary = None
    for stamp, adapter, original, frame in read_trace(path):
        if adapter == 252 and frame.startswith(b'DNPB1\0'):
            boundary = json.loads(frame[6:]).get('boundary_timestamp')
        if adapter >= 250:
            continue
        if origin is None:
            origin = stamp
        packet = decode_captured_frame(stamp, frame, original)
        if packet is None or packet.transport != 'UDP' or packet.inferred_direction() != 'server_to_client':
            continue
        for payload in split_v576_payload(packet.payload):
            if len(payload) < 9:
                continue
            signatures[(len(payload), payload[5:9].hex())] += 1
            if not _v576_wire_may_contain_marker(payload, markers):
                continue
            phase = _V576_XOR_PHASE_BY_SIGNATURE_BYTE[payload[5]]
            plain = _decode_v576_body(payload, phase)
            for template, marker in zip(ids, markers):
                offset = plain.find(marker, 9)
                if offset < 0:
                    continue
                counts[template] += 1
                if counts[template] <= 12:
                    rows.append({'seconds': stamp-origin, 'boundarySeconds': stamp-boundary if boundary else None,
                                 'template': template, 'offset': offset, 'phase': phase,
                                 'length': len(payload), 'signature': payload[5:9].hex(),
                                 'actorAt10': int.from_bytes(plain[10:14], 'little'),
                                 'plaintext': plain.hex(), 'wire': payload.hex()})
    return {'capture': str(path), 'templateCounts': dict(counts), 'matches': rows,
            'signatures88': [{'key': k, 'count': v} for k,v in signatures.items() if k[0] == 88]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('captures', nargs=2, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--templates', nargs='+', type=int)
    args = parser.parse_args()
    results = [scan(p, args.templates) for p in args.captures]
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'capture': r['capture'], 'counts': r['templateCounts'],
                       'samples': [{k:v for k,v in x.items() if k not in ('wire','plaintext')} for x in r['matches'][:12]]}
                      for r in results], ensure_ascii=False))
