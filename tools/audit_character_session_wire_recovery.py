"""Match recovered replay rows to independent signed HP wire records."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import gzip
from hashlib import sha256
import json
import struct
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_v576_status_damage_events, decode_v576_monster_spawn,
    decode_captured_frame,
    _decode_v576_body, _v576_candidate_xor_phases,
)
from dragon_nest_builder.live_dps.entity_owner_evidence import decode_entity_owner_records
from dragon_nest_builder.live_dps.dpscap_segments import load_dpscap_segments


def physical(row):
    return tuple(row.get(key) for key in (
        'timestamp_ms', 'wire_source_actor_id', 'wire_target_actor_id', 'amount',
        'critical', 'healing', 'wire_result_group_id', 'wire_damage_object_id',
        'wire_record_offset', 'wire_damage_layout'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with gzip.open(args.before, 'rt', encoding='utf8') as handle: before = json.load(handle)
    with gzip.open(args.after, 'rt', encoding='utf8') as handle: after = json.load(handle)
    old = Counter(map(physical, before['combat']))
    new = Counter(map(physical, after['combat']))
    extra = new - old
    additions = []
    for event in after['combat']:
        key = physical(event)
        if extra[key]:
            extra[key] -= 1
            additions.append(event)
    damage_wanted = Counter(physical(event) for event in additions if not event['healing'])
    healing_events = [event for event in additions if event['healing']]
    source = V576DiagnosticReplaySource(args.capture, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed_events
    births = {}
    owners = {}
    damages = []
    heals = defaultdict(list)
    serial = 0

    def frame(packet):
        return {'capture_packet_serial': serial, 'timestamp_seconds': packet.timestamp_seconds,
                'udp_payload_sha256': sha256(packet.payload).hexdigest(),
                'udp_payload_hex': packet.payload.hex(), 'payload_length': len(packet.payload),
                'direction': packet.inferred_direction(),
                'source_endpoint': [packet.source_ip, packet.source_port],
                'target_endpoint': [packet.destination_ip, packet.destination_port]}

    def feed(packet):
        nonlocal serial
        serial += 1
        direction = packet.inferred_direction()
        is_udp = packet.transport.upper() == 'UDP'
        raw = frame(packet) if is_udp else None
        if is_udp:
            birth = decode_v576_monster_spawn(packet.payload, direction, None)
            if birth is not None:
                births[raw['udp_payload_sha256']] = {**raw, 'decoded_spawn': asdict(birth)}
            for owner in decode_entity_owner_records(packet.payload, direction):
                owners[(owner.message_fingerprint, owner.record_offset)] = {
                    **raw, 'record': asdict(owner),
                    'owner_record_decoded_hex': owner.fingerprint,
                }
        original_feed(packet)
        if not is_udp or direction != 'server_to_client': return ()
        origin = decoder.first_timestamp or packet.timestamp_seconds
        timestamp = max(0, round((packet.timestamp_seconds - origin) * 1000))
        for decoded in decode_v576_status_damage_events(packet.payload, direction):
            key = (timestamp, decoded.source_actor_id, decoded.target_actor_id,
                   decoded.damage, bool(decoded.critical), False, 0, 0,
                   decoded.record_offset, decoded.layout)
            if not damage_wanted[key]: continue
            matches = []
            offset = decoded.record_offset
            for phase in _v576_candidate_xor_phases(packet.payload):
                body = _decode_v576_body(packet.payload, phase)
                if (body[offset:offset+4] == b'\x1c\x1b\x00\x04'
                        and int.from_bytes(body[offset-4:offset], 'little') == decoded.target_actor_id
                        and int.from_bytes(body[offset+4:offset+12], 'little', signed=True) == -decoded.damage
                        and int.from_bytes(body[offset+12:offset+16], 'little') == decoded.source_actor_id):
                    matches.append({'xor_phase': phase,
                                    'record_offset': offset,
                                    'decoded_typed_record_hex': body[offset-4:offset+30].hex()})
            binding = decoder.entity_owner_evidence.bindings.get(decoded.source_actor_id)
            damages.append({**raw, 'timestamp_ms': timestamp,
                'raw_source_entity_id': decoded.source_actor_id,
                'raw_target_actor_id': decoded.target_actor_id, 'signed_hp_delta': -decoded.damage,
                'wire_parse_candidates': matches,
                'current_generation_owner_binding': asdict(binding) if binding else None,
                'birth_frame': births.get(binding.birth_fingerprint) if binding else None,
                'owner_frame': owners.get((binding.record.message_fingerprint, binding.record.record_offset)) if binding else None})
            damage_wanted[key] -= 1
        # Positive subtype4 records carry a repeated target, not a healer.
        # Verify their amounts independently; do not certify cast-proximity
        # healer/skill labels from the replay.
        if any(event['timestamp_ms'] == timestamp for event in healing_events):
            for phase in _v576_candidate_xor_phases(packet.payload):
                body = _decode_v576_body(packet.payload, phase)
                offset = 14
                while (offset := body.find(b'\x1c\x1b\x00\x04', offset)) >= 0:
                    if offset+30 <= len(body):
                        target = int.from_bytes(body[offset-4:offset], 'little')
                        delta = int.from_bytes(body[offset+4:offset+12], 'little', signed=True)
                        repeated = int.from_bytes(body[offset+12:offset+16], 'little')
                        tail = body[offset+16:offset+30]
                        if (0 < delta <= 1_000_000_000_000 and target == repeated
                                and tail.hex() == '00010000000000010000ffffffff'):
                            heals[(timestamp, delta, target)].append({**raw,
                                'timestamp_ms': timestamp, 'record_offset': offset,
                                'xor_phase': phase, 'raw_target_actor_id': target,
                                'raw_repeated_target_actor_id': repeated,
                                'signed_hp_delta': delta,
                                'decoded_typed_record_hex': body[offset-4:offset+30].hex(),
                                'healer_identity_proved_by_this_row': False})
                    offset += 1
        return ()

    decoder.feed_events = feed
    try: source.poll()
    finally: source.close()
    aliases = {row['presentation_actor_id']: row['wire_actor_id']
               for row in getattr(decoder, 'replay_character_identity_aliases', ())}
    healing_matches = []
    for event in healing_events:
        shown_target = int(event['target_id'], 16)
        target = aliases.get(shown_target, shown_target)
        candidates = heals[(event['timestamp_ms'], event['amount'], target)]
        target_projection = 'same raw target actor'
        if not candidates:
            # Existing rejoin normalization can coalesce the two local actor
            # IDs (0x4E66B944/45) into one display character. Do not change or
            # manufacture a raw target: retain the independent positive row
            # and require a unique time+amount row for this display projection.
            candidates = [row for (time, amount, _raw_target), rows in heals.items()
                          if time == event['timestamp_ms'] and amount == event['amount']
                          for row in rows]
            target_projection = 'display target differs; exact raw target retained in candidates'
        healing_matches.append({'timestamp_ms': event['timestamp_ms'], 'amount': event['amount'],
            'display_target_actor_id': shown_target, 'session_alias_raw_actor_id': target,
            'target_projection': target_projection, 'candidates': candidates,
            'raw_positive_rows_same_time': [row for (time, _amount, _target), rows in heals.items()
                if time == event['timestamp_ms'] for row in rows],
            'raw_positive_input_rows': [row for (time, amount, raw_target), rows in heals.items()
                if time == event['timestamp_ms'] and raw_target == target and amount >= event['amount']
                for row in rows],
            'aggregate_healing_reconciliation_method': event['attribution_method'],
            'replay_healing_skill_label_excluded_from_proof': True})
    failures = []
    if sum(damage_wanted.values()): failures.append('missing raw damage records')
    for row in damages:
        binding = row['current_generation_owner_binding']
        if len(row['wire_parse_candidates']) != 1: failures.append('ambiguous raw status decode')
        if (row['raw_source_entity_id'] != 113 or binding is None
                or binding['template_id'] != 33601 or binding['record']['owner_id'] != 0x4E66B944
                or row['birth_frame'] is None or row['owner_frame'] is None):
            failures.append('missing exact birth or repeated 34 owner proof')
    report = {'schema': 'character-session-wire-recovery-v1',
        'capture': str(args.capture.resolve()), 'capture_sha256': sha256(args.capture.read_bytes()).hexdigest(),
        'removed_old_wire_rows': sum((old-new).values()),
        'recovered_damage_rows': len(damages), 'recovered_damage_amount': sum(-row['signed_hp_delta'] for row in damages),
        'recovered_healing_rows': len(healing_matches), 'recovered_healing_amount': sum(row['amount'] for row in healing_matches),
        'healing_rows_with_exact_raw_amount': sum(len(row['candidates']) == 1 for row in healing_matches),
        'healing_rows_with_unique_positive_raw_input': sum(len(row['raw_positive_input_rows']) == 1 for row in healing_matches),
        'positive_raw_healing_input_amount': sum(row['raw_positive_input_rows'][0]['signed_hp_delta'] for row in healing_matches if len(row['raw_positive_input_rows']) == 1),
        'healing_effective_amounts_not_claimed_as_raw_records': [
            {'timestamp_ms': row['timestamp_ms'], 'effective_amount': row['amount'],
             'aggregate_healing_reconciliation_method': row['aggregate_healing_reconciliation_method']}
            for row in healing_matches if len(row['candidates']) != 1],
        'unmatched_damage_rows': sum(damage_wanted.values()), 'failures': sorted(set(failures)),
        'damage_wire_matches': damages, 'healing_wire_matches': healing_matches}
    # Cross-reference the logical UDP proof with the exact original Ethernet
    # frame bytes and DNDP1 file offset. Repeated packets retain every physical
    # copy here, while the wire-result audit above counts the result once.
    references = defaultdict(list)
    def collect(value):
        if isinstance(value, dict):
            if 'udp_payload_sha256' in value: references[value['udp_payload_sha256']].append(value)
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
    collect(report)
    references = {digest: list({id(row): row for row in rows}.values())
                  for digest, rows in references.items()}
    header = struct.Struct('<dBII')
    for path in load_dpscap_segments(args.capture) or (args.capture,):
        with Path(path).open('rb', buffering=1024*1024) as handle:
            assert handle.read(6) == b'DNDP1\0'
            while True:
                offset = handle.tell()
                raw_header = handle.read(header.size)
                if not raw_header: break
                timestamp, adapter, original_length, captured_length = header.unpack(raw_header)
                captured_frame = handle.read(captured_length)
                if adapter >= 250: continue
                packet = decode_captured_frame(timestamp, captured_frame, original_length)
                if packet is None or packet.transport != 'UDP': continue
                payload_hash = sha256(packet.payload).hexdigest()
                for reference in references.get(payload_hash, ()):
                    if abs(timestamp-reference['timestamp_seconds']) > 0.000001: continue
                    reference.setdefault('original_captured_frames', []).append({
                        'capture_part':str(Path(path).resolve()), 'capture_record_file_offset':offset,
                        'adapter':adapter,'captured_frame_sha256':sha256(captured_frame).hexdigest(),
                        'captured_length':captured_length,'original_length':original_length})
    report['logical_udp_proofs_without_original_frame'] = sum(
        not reference.get('original_captured_frames')
        for rows in references.values() for reference in rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({key:value for key,value in report.items() if not key.endswith('_matches')}, ensure_ascii=True))


if __name__ == '__main__': main()
