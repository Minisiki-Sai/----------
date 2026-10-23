"""Raw, stateless investigation of missing object registrations, not attribution.

Uses the Hit only to select a bounded physical packet window. Old skill labels
are diagnostic labels, never proof of an object's template or skill.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p


def probe(diagnostic, output):
    data = json.loads(diagnostic.read_text(encoding='utf-8'))
    cases = []
    selected = {'no_matching_registration', 'registration_not_admitted_or_pruned',
                'object_only_provisional_lineage', 'object_expired'}
    for row in data['rows']:
        if row['reason'] not in selected or row['hits'] < 15:
            continue
        for ex in row['examples'][:1]:
            obj = ex['state']['object']
            if not 0 < obj < 0xffffff:
                continue
            cases.append(dict(job=row['job'], old_skill=row['skill'], hits=row['hits'],
                reason=row['reason'], anchor=ex, matches=[]))
    header = struct.Struct('<dBII')
    count = 0
    with Path(data['capture']).open('rb') as stream:
        assert stream.read(6) == b'DNDP1\0'
        while raw_header := stream.read(header.size):
            assert len(raw_header) == header.size
            offset = stream.tell() - header.size
            timestamp, adapter, original, length = header.unpack(raw_header)
            raw = stream.read(length)
            assert len(raw) == length
            count += 1
            windows = [c for c in cases if c['anchor']['time'] - 20 <= timestamp <= c['anchor']['time'] + .05]
            if not windows:
                continue
            packet = p.decode_captured_frame(timestamp, raw, original)
            if packet is None or packet.transport.upper() != 'UDP':
                continue
            for index, wire in enumerate(p.split_v576_payload(packet.payload)):
                for phase in range(13):
                    body = p._decode_v576_body_uncached(wire, phase)
                    for case in windows:
                        state = case['anchor']['state']
                        owner, obj = state['wire_source'], state['object']
                        ob = obj.to_bytes(4, 'little')
                        if ob not in body[9:]:
                            continue
                        positions = []
                        pos = 9
                        while (pos := body.find(ob, pos)) >= 0:
                            positions.append(pos)
                            pos += 1
                        owner_positions = []
                        pos = 9
                        while (pos := body.find(owner.to_bytes(4, 'little'), pos)) >= 0:
                            owner_positions.append(pos)
                            pos += 1
                        case['matches'].append(dict(t=timestamp, frame=count, file_offset=offset,
                            message=index, phase=phase, direction=packet.inferred_direction(),
                            source=f'{packet.source_ip}:{packet.source_port}',
                            target=f'{packet.destination_ip}:{packet.destination_port}',
                            object_positions=positions, owner_positions=owner_positions,
                            payload_sha256=hashlib.sha256(wire).hexdigest(), payload_hex=wire.hex(),
                            decoded_hex=body.hex()))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(capture=data['capture'], physical_frames=count,
        cases=cases), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(cases=len(cases), matches=sum(len(c['matches']) for c in cases),
        output=str(output)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('diagnostic', type=Path)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    probe(args.diagnostic, args.output)
