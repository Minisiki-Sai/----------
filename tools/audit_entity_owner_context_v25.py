"""Bounded raw discovery of entity/player co-occurrence; never owner proof.

Searches the actual wire around decoded births, including all 13 UDP XOR
phases. A co-occurrence can also be a target, observer, HP or status record;
the output explicitly does not promote it to an ownership relationship.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HEADER = struct.Struct('<dBII')


def frames(path, seconds):
    with path.open('rb') as stream:
        assert stream.read(6) == b'DNDP1\0'
        first = None
        n = 0
        while header := stream.read(HEADER.size):
            assert len(header) == HEADER.size
            offset = stream.tell() - HEADER.size
            timestamp, adapter, original, size = HEADER.unpack(header)
            raw = stream.read(size)
            assert len(raw) == size
            first = timestamp if first is None else first
            if timestamp - first > seconds:
                break
            n += 1
            yield n, offset, timestamp, adapter, original, raw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path)
    ap.add_argument('--seconds', type=float, default=180)
    ap.add_argument('--window', type=float, default=.75)
    ap.add_argument('--output', required=True, type=Path)
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing raw audit')
    module_path = ROOT / 'outputs/damage_attribution_candidate_v24_20260907/protocol_v576.py'
    spec = importlib.util.spec_from_file_location('dragon_nest_builder.live_dps._entity_audit_frozen_protocol', module_path)
    proto = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = proto
    spec.loader.exec_module(proto)
    spawns, actors = [], set()
    counts = collections.Counter()
    for frame, offset, timestamp, adapter, original, raw in frames(args.capture, args.seconds):
        counts['physical_frames'] += 1
        transport = proto.decode_captured_frame(timestamp, raw, original)
        if not transport or transport.transport != 'UDP':
            continue
        for mi, wire in enumerate(proto.split_v576_payload(transport.payload)):
            counts['udp_messages'] += 1
            spawn = proto.decode_v576_monster_spawn(wire, None)
            if spawn:
                spawns.append(dict(frame=frame, message=mi, offset=offset, time=timestamp,
                    entity=spawn.actor_id, template=spawn.template_id, wire=wire.hex(),
                    decoded=proto._decode_v576_body(wire, 5).hex()))
            for hit in proto.decode_v576_embedded_direct_damage_events(wire, None):
                if hit.source_actor_id and 0x1000000 <= hit.source_actor_id < 0xffffffff:
                    actors.add(hit.source_actor_id)
    print(json.dumps(dict(stage='births', counts=counts, spawns=len(spawns), actors=sorted(actors))), flush=True)
    times = [s['time'] for s in spawns]
    actor_tokens = {a.to_bytes(4, 'little'): a for a in actors}
    pattern = proto._v576_wire_marker_pattern(tuple(actor_tokens)) if actors else None
    rows, families = [], collections.Counter()
    examples = collections.Counter()
    for frame, offset, timestamp, adapter, original, raw in frames(args.capture, args.seconds):
        lo = bisect.bisect_left(times, timestamp - args.window)
        hi = bisect.bisect_right(times, timestamp + args.window)
        nearby = [s for s in spawns[lo:hi] if s['template'] < 100000]
        if not nearby:
            continue
        transport = proto.decode_captured_frame(timestamp, raw, original)
        if not transport:
            continue
        if transport.transport != 'UDP':
            counts['nearby_tcp_frames'] += 1
            continue
        for mi, wire in enumerate(proto.split_v576_payload(transport.payload)):
            if pattern is None or not pattern.search(wire, 9):
                continue
            for phase in range(13):
                decoded = proto._decode_v576_body(wire, phase)
                owners = [(a, at) for token, a in actor_tokens.items()
                          for at in range(9, len(decoded)-3) if decoded[at:at+4] == token]
                if not owners:
                    continue
                for birth in nearby:
                    token = birth['entity'].to_bytes(4, 'little')
                    positions = [at for at in range(9, len(decoded)-3) if decoded[at:at+4] == token]
                    if not positions:
                        continue
                    signature = wire[5:9].hex()
                    family = (len(wire), signature, phase,
                              tuple(at for _, at in owners), tuple(positions))
                    families[str(family)] += 1
                    entry = dict(frame=frame, message=mi, file_offset=offset,
                        timestamp=timestamp, birth_frame=birth['frame'], entity=birth['entity'],
                        template=birth['template'], age=timestamp-birth['time'],
                        length=len(wire), signature=signature, phase=phase,
                        player_tokens=owners, entity_offsets=positions,
                        direction=transport.inferred_direction())
                    if examples[family] < 3:
                        entry.update(wire=wire.hex(), decoded=decoded.hex(), frame_hex=raw.hex(),
                                     original=original, adapter=adapter)
                        examples[family] += 1
                    rows.append(entry)
    result = dict(capture=str(args.capture.resolve()), seconds=args.seconds, window=args.window,
        policy='Discovery only: player/entity co-occurrence is not ownership evidence.',
        protocol_sha256=hashlib.sha256(module_path.read_bytes()).hexdigest(),
        counts=counts, actors=sorted(actors), spawns=spawns,
        families=families, candidates=rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), candidates=len(rows), families=len(families),
                         counts=counts), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
