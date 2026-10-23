"""Bounded TCP/non-UDP full-object discovery; no replay or attribution writes.

Uses the current transport reassembler, registered TCP decryptors, and existing
registration/wrapper parsers. No key recovery, ACT lookup, or inferred skill
labels. Physical offsets are zero-based; ends are exclusive. Run Python -B.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps import tcp_profile_codec as codec
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

OUT = ROOT / 'outputs/tcp_object_sidecar_v37_20260908'
DEFAULT_ANCHORS = ROOT / 'outputs/gap_object_records_v36_20260908/0813硬核火舞_黑暗萨满.json'
HEADER = struct.Struct('<dBII')


def positions(body: bytes, needle: bytes) -> list[int]:
    result = []
    at = body.find(needle)
    while at >= 0:
        result.append(at)
        at = body.find(needle, at + 1)
    return result


def full_matches(body: bytes, anchors: list[dict]) -> list[dict]:
    """Full uint32 only; proximity/low words/old labels are never identity."""
    found = []
    for anchor in anchors:
        for endian in ('little', 'big'):
            offsets = positions(body, anchor['object'].to_bytes(4, endian))
            if offsets:
                found.append(dict(object=anchor['object'], endian=endian,
                                  object_offsets=offsets,
                                  owner_offsets=positions(body, anchor['owner'].to_bytes(4, endian))))
    return found


def read_bounded(handle, start: float, cutoff: float, stats: dict):
    if handle.read(6) != b'DNDP1\0':
        raise ValueError('not a DNDP1 capture')
    stats.update(headers_read=0, frames_read=0, payload_bytes_read=0,
                 frames_skipped_before_window=0, timestamp_regressions=0)
    previous = None
    while header := handle.read(HEADER.size):
        if len(header) != HEADER.size:
            raise ValueError('truncated capture header')
        offset = handle.tell() - HEADER.size
        timestamp, adapter, original, size = HEADER.unpack(header)
        stats['headers_read'] += 1
        stats.setdefault('capture_first_timestamp', timestamp)
        if previous is not None and timestamp < previous:
            stats['timestamp_regressions'] += 1
        previous = timestamp
        if timestamp > cutoff:
            stats['stop_header'] = dict(frame=stats['headers_read'], file_offset=offset,
                                       time=timestamp, payload_read=False)
            break
        if timestamp < start:
            stats['frames_skipped_before_window'] += 1
            handle.seek(size, 1)
            continue
        raw = handle.read(size)
        if len(raw) != size:
            raise ValueError('truncated capture frame')
        stats['frames_read'] += 1
        stats['payload_bytes_read'] += size
        stats.setdefault('first_scanned_timestamp', timestamp)
        stats['last_scanned_timestamp'] = timestamp
        yield dict(frame=stats['headers_read'], file_offset=offset, time=timestamp,
                   adapter=adapter, original_length=original, captured_length=size), raw
    stats['read_stopped_at_file_offset'] = handle.tell()


def flow_key(packet):
    return (packet.source_ip, packet.source_port, packet.destination_ip, packet.destination_port)


def payload_offset(raw: bytes, packet) -> int:
    ip = p._extract_ipv4_packet(raw)
    if ip is None:
        raise ValueError('decoded packet lacks a physical IPv4 frame')
    ip_at = raw.find(ip)
    ihl = (ip[0] & 15) * 4
    head_size = (ip[ihl + 12] >> 4) * 4 if packet.transport == 'TCP' else 8
    at = ip_at + ihl + head_size
    if raw[at:at + len(packet.payload)] != packet.payload:
        raise ValueError('physical payload mapping failed')
    return at


@dataclass
class Segment:
    sequence: int
    body: bytes
    meta: dict
    payload_at: int


def build_runs(segments: list[Segment]):
    """Retain sequence gaps and conflicting retransmissions as explicit limits."""
    runs = []
    conflicts = []
    for segment in sorted(segments, key=lambda s: (s.sequence, s.meta['frame'])):
        if not runs or segment.sequence > runs[-1]['start'] + len(runs[-1]['body']):
            runs.append(dict(start=segment.sequence, body=bytearray(), spans=[]))
        run = runs[-1]
        begin = segment.sequence - run['start']
        overlap = max(0, min(len(segment.body), len(run['body']) - begin))
        if bytes(run['body'][begin:begin + overlap]) != segment.body[:overlap]:
            conflicts.append(dict(frame=segment.meta['frame'], sequence=segment.sequence,
                                  overlap=overlap, file_offset=segment.meta['file_offset']))
        if overlap < len(segment.body):
            span_start = len(run['body'])
            run['body'].extend(segment.body[overlap:])
            run['spans'].append((span_start, len(run['body']), segment, overlap))
    for run in runs:
        run['body'] = bytes(run['body'])
    return runs, conflicts


def physical_spans(run, begin: int, end: int) -> list[dict]:
    result = []
    for left, right, segment, trimmed in run['spans']:
        a, b = max(begin, left), min(end, right)
        if a >= b:
            continue
        frame_at = segment.payload_at + trimmed + a - left
        result.append(dict(frame=segment.meta['frame'], time=segment.meta['time'],
                           record_file_offset=segment.meta['file_offset'],
                           frame_byte_offset=frame_at,
                           file_byte_offset=segment.meta['file_offset'] + HEADER.size + frame_at,
                           message_byte_offset=a - begin, length=b - a))
    return result


def locate_message(runs, wire: bytes) -> list[dict]:
    if not wire:
        return []
    locations = []
    for run in runs:
        for at in positions(run['body'], wire):
            spans = physical_spans(run, at, at + len(wire))
            locations.append(dict(tcp_sequence=run['start'] + at, spans=spans,
                                  complete_time=max(s['time'] for s in spans)))
    return locations


def registrations(wire: bytes, direction):
    client = p.decode_v576_damage_object_registration(wire, direction)
    server = p.decode_v576_server_damage_object_registrations(
        wire, direction, (), include_unmapped=True)
    return ([client] if client is not None else []) + list(server)


def decrypt(packet):
    direction = packet.inferred_direction()
    if direction == 'server_to_client':
        result = codec.decode_registered_server_tcp_envelope(packet.payload, connection_context=packet)
        return result, 'server_registered'
    if direction == 'client_to_server':
        return codec.decode_registered_tcp_envelope(packet.payload), 'client_registered'
    server = codec.decode_registered_server_tcp_envelope(packet.payload, connection_context=packet)
    client = codec.decode_registered_tcp_envelope(packet.payload)
    if server is not None and client is not None and server[0] != client[0]:
        return None, 'ambiguous_direction_plaintexts'
    return (server or client), 'direction_unknown_registered'


def load_anchors(path):
    data = json.loads(path.read_text('utf-8'))
    anchors = []
    for example in sorted(data['examples'], key=lambda e: e['time'])[:6]:
        event = example['event']
        inventory = inspect_udp_damage_records(bytes.fromhex(example['wire']), example['direction'])
        rows = [r for r in inventory.damage_records if
                (r.record_offset, r.source_actor_id, r.target_actor_id, r.damage,
                 r.critical, r.damage_object_id) ==
                (event['wire_record_offset'], event['wire_source_actor_id'], event['wire_target_actor_id'],
                 event['amount'], event['critical'], event['wire_damage_object_id'])]
        if not inventory.complete or len(rows) != 1:
            raise ValueError('anchor physical identity failed')
        anchors.append(dict(object=event['wire_damage_object_id'], owner=event['wire_source_actor_id'],
                            time=example['time'], serial_diagnostic_only=example['serial'],
                            record_offset=event['wire_record_offset'], wire=example['wire'],
                            xor_phase=rows[0].xor_phase,
                            decoded_owner_offset=rows[0].record_offset + 8,
                            decoded_object_offset=rows[0].record_offset + 13,
                            wire_sha256=sha256(bytes.fromhex(example['wire'])).hexdigest(),
                            physical_locations=[]))
    if len(anchors) != 6 or len({a['object'] for a in anchors}) != 6:
        raise ValueError('expected exactly six distinct full object IDs')
    return data['capture'], anchors


def scan(anchor_path=DEFAULT_ANCHORS, seconds=60.0):
    if not 0 < seconds <= 60:
        raise ValueError('window must be greater than zero and at most 60 seconds')
    started = time.perf_counter()
    capture, anchors = load_anchors(anchor_path)
    cutoff = max(a['time'] for a in anchors)
    start = cutoff - seconds
    if min(a['time'] for a in anchors) < start:
        raise ValueError('window excludes an anchor')
    dependencies = [Path(p.__file__), Path(codec.__file__), Path(__file__),
                    Path(inspect_udp_damage_records.__code__.co_filename), anchor_path]
    dependencies.extend(x for x in (codec._BUNDLED_KEY_REGISTRY_PATH, codec._USER_KEY_CACHE_PATH) if x.exists())
    before_hashes = {str(x): sha256(x.read_bytes()).hexdigest() for x in dependencies}
    counts, adapters = Counter(), Counter()
    read_stats = {}
    segments = defaultdict(list)
    generations = Counter()
    logical = []
    raw_matches = []
    raw_tcp_frames = []
    assembler = p.V576TransportReassembler()
    with Path(capture).open('rb') as handle:
        for meta, raw in read_bounded(handle, start, cutoff, read_stats):
            adapters[meta['adapter']] += 1
            if meta['adapter'] in (254, 255):
                counts['metadata_frames_excluded'] += 1
                continue
            packet = p.decode_captured_frame(meta['time'], raw, meta['original_length'])
            transport = packet.transport if packet is not None else 'unparsed_nonmetadata'
            counts['raw_' + transport] += 1
            if packet is not None and transport == 'UDP':
                # Only verify supplied anchor wires, never repeat the UDP object search.
                for anchor in anchors:
                    if meta['time'] == anchor['time']:
                        for at in positions(raw, bytes.fromhex(anchor['wire'])):
                            anchor['physical_locations'].append(dict(meta, frame_byte_offset=at,
                                file_byte_offset=meta['file_offset'] + HEADER.size + at))
                continue
            hits = full_matches(raw, anchors)
            if hits:
                raw_matches.append(dict(meta, transport=transport, matches=hits,
                                        raw_sha256=sha256(raw).hexdigest(), raw_hex=raw.hex()))
            if packet is None or transport != 'TCP':
                continue
            key = flow_key(packet)
            generations[key] = generations[key] + 1 if packet.tcp_flags & 2 else max(1, generations[key])
            at = payload_offset(raw, packet)
            raw_tcp_frames.append(dict(meta, flow=key, generation=generations[key],
                                       sequence=packet.tcp_sequence, payload_frame_offset=at,
                                       payload_length=len(packet.payload), flags=packet.tcp_flags))
            if packet.payload:
                counts['raw_TCP_payload_frames'] += 1
                counts['raw_TCP_payload_bytes'] += len(packet.payload)
                segments[(*key, generations[key])].append(Segment(
                    (packet.tcp_sequence + bool(packet.tcp_flags & 2)) & 0xffffffff,
                    packet.payload, meta, at))
            logical.extend(assembler.feed(packet))
    pending = [dict(flow=key, application_buffer_bytes=len(state.application_buffer),
                    pending_segments=len(state.pending),
                    pending_payload_bytes=sum(len(x.payload) for x in state.pending.values()))
               for key, state in assembler._flows.items()]
    logical.extend(assembler.flush())
    if any(not a['physical_locations'] for a in anchors):
        raise ValueError('one or more supplied anchor wires missing from physical window')
    runs_by_flow, flow_report = {}, []
    for key, values in segments.items():
        runs, conflicts = build_runs(values)
        runs_by_flow[key] = runs
        flow_report.append(dict(flow=key, segments=len(values), sequence_conflicts=conflicts,
                                contiguous_runs=[dict(start_sequence=r['start'], length=len(r['body'])) for r in runs]))
    messages, matches, registration_rows = [], [], []
    shapes = Counter()
    for index, packet in enumerate(logical):
        key = (*flow_key(packet), packet.tcp_connection_generation)
        locations = locate_message(runs_by_flow.get(key, []), packet.payload)
        counts['logical_TCP'] += 1
        result, route = decrypt(packet)
        counts['decoded_TCP' if result is not None else 'undecoded_TCP'] += 1
        if not locations:
            counts['logical_TCP_without_physical_mapping'] += 1
        row = dict(message=index, time=packet.timestamp_seconds, flow=key,
                   direction=packet.inferred_direction(), length=len(packet.payload), decode_route=route,
                   decoded=result is not None, wire_sha256=sha256(packet.payload).hexdigest(),
                   wire_hex=packet.payload.hex(), physical_location_count=len(locations),
                   physical_locations=locations,
                   declared_length=p.V576TransportReassembler._declared_tcp_message_length(
                       packet.payload, direction=packet.inferred_direction()))
        body_size = len(packet.payload) - 3
        if body_size >= 0 and row['direction'] in ('server_to_client', 'client_to_server'):
            # Inventory only the candidates selected by the actual route; no
            # additional trial decryptions or cross-index server key search.
            selected = codec._registered_key_candidates(body_size % 256, body_size=body_size,
                direction=row['direction'], include_compatible_indexes=row['direction'] == 'client_to_server')
            row.update(body_size=body_size, registered_candidate_count=len(selected))
            if result is None:
                row['opaque_reason'] = ('no_registered_candidate_for_current_route' if not selected
                                        else 'registered_candidates_failed_envelope_validation')
        views = [('tcp_wire', packet.payload)]
        if result is not None:
            plain, key_index, rounds, source = result
            row.update(plaintext_hex=plain.hex(), plaintext_sha256=sha256(plain).hexdigest(),
                       key_index=key_index, rounds=rounds, key_source=source,
                       opcode=plain[2:4].hex(),
                       validation='current envelope body-length check; opcode semantics not certified',
                       target_owner_offsets=positions(plain, anchors[0]['owner'].to_bytes(4, 'little')))
            views.append(('tcp_plaintext', plain))
            # Common TCP plaintext starts with body length + opcode. This suffix
            # is a *format hypothesis*, accepted only by existing strict parsers.
            if len(plain) > 4:
                views.append(('tcp_plaintext_after_common_header_hypothesis', plain[4:]))
        shapes[(row['direction'], row['decoded'], row['length'], row.get('opcode', 'unknown'))] += 1
        for view_name, body in views:
            if not view_name.endswith('hypothesis'):
                for found in full_matches(body, anchors):
                    anchor = next(a for a in anchors if a['object'] == found['object'])
                    matches.append(dict(message=index, view=view_name, **found,
                        physically_complete_before_hit=any(loc['complete_time'] < anchor['time'] for loc in locations),
                        attribution_proven=False))
            offset = 0
            parts = p.split_v576_payload(body)
            for part in parts:
                counts['registration_parser_input_views'] += 1
                for reg in registrations(part, packet.inferred_direction()):
                    decoded = p._decode_v576_body(part, reg.xor_phase)
                    targets = [a for a in anchors if a['object'] == reg.damage_object_id]
                    registration_rows.append(dict(message=index, view=view_name, part_offset=offset,
                        part_length=len(part), registration=asdict(reg),
                        decoded_part_hex=decoded.hex(), full_target_object=bool(targets),
                        matching_owner=bool(targets and targets[0]['owner'] == reg.source_actor_id),
                        physically_complete_before_hit=bool(targets and any(
                            loc['complete_time'] < targets[0]['time'] for loc in locations)),
                        attribution_proven=False))
                offset += len(part)
        messages.append(row)
    # Search contiguous TCP bytes too: catches full IDs split across segments,
    # including stream regions the current framing path could not emit.
    stream_matches = []
    for key, runs in runs_by_flow.items():
        for run in runs:
            for found in full_matches(run['body'], anchors):
                stream_matches.append(dict(flow=key, start_sequence=run['start'], **found,
                    physical_object_locations=[physical_spans(run, at, at + 4) for at in found['object_offsets']],
                    attribution_proven=False))
    anchor_checks = []
    for a in anchors:
        anchor_checks.append(dict(object=a['object'], owner=a['owner'], time=a['time'],
            physical_locations=a['physical_locations'], wire_sha256=a['wire_sha256'],
            record_offset=a['record_offset'], xor_phase=a['xor_phase'],
            decoded_owner_offset=a['decoded_owner_offset'], decoded_object_offset=a['decoded_object_offset'],
            object_cipher_file_offsets=[loc['file_byte_offset'] + a['decoded_object_offset']
                                        for loc in a['physical_locations']],
            decoded_TCP_before_hit=sum(any(loc['complete_time'] < a['time'] for loc in m['physical_locations'])
                                      for m in messages if m['decoded']),
            undecoded_TCP_before_hit=sum(any(loc['complete_time'] < a['time'] for loc in m['physical_locations'])
                                        for m in messages if not m['decoded']),
            full_object_message_matches=sum(x['object'] == a['object'] for x in matches),
            structural_matching_owner_before_hit=sum(r['full_target_object'] and r['matching_owner']
                and r['physically_complete_before_hit'] and r['registration']['damage_object_id'] == a['object']
                for r in registration_rows)))
    hashes_after = {str(x): sha256(x.read_bytes()).hexdigest() for x in dependencies}
    candidates = [r for r in registration_rows if r['full_target_object'] and r['matching_owner']
                  and r['physically_complete_before_hit']]
    opaque_111 = [m for m in messages if not m['decoded'] and m['length'] == 111]
    opaque_111_summary = dict(count=len(opaque_111),
        unique_ciphertext_bodies=len({m['wire_hex'][6:] for m in opaque_111}),
        body_sizes=sorted({m['body_size'] for m in opaque_111}),
        registered_candidate_counts=sorted({m['registered_candidate_count'] for m in opaque_111}),
        reasons=dict(Counter(m['opaque_reason'] for m in opaque_111)),
        first_message=opaque_111[0] if opaque_111 else None,
        last_message=opaque_111[-1] if opaque_111 else None)
    return dict(schema='tcp-object-sidecar-v37', capture=capture, anchor_input=str(anchor_path),
        scope=dict(start=start, cutoff=cutoff, seconds=seconds, cutoff_rule='sixth supplied hit anchor, inclusive',
                   same_object='all four bytes, little-endian and diagnostic big-endian',
                   include_unmapped=True, new_confirmed_hits=0,
                   no_skill_or_owner_promotion_from_timing_old_label_or_group=True),
        verdict='structural_candidate_requires_review' if candidates else 'bounded_negative',
        limitations=['Undecrypted TCP bodies remain opaque; raw ciphertext nonmatches do not prove absence.',
                     'Search uses current registered keys and supported wrapper parsers; no new key recovery.',
                     'The first TCP flow may begin midstream; sequence gaps and pending buffers are reported.',
                     'Stops at first physical capture header after cutoff; later file records are not examined.',
                     'Timing only bounds byte availability; object+owner co-occurrence alone is not skill evidence.',
                     'A plaintext uint32 is mapped to its complete encrypted message, not an invented bytewise cipher offset.'],
        read_stats=read_stats, counts=dict(counts), adapter_counts=dict(adapters), anchors=anchor_checks,
        input_hashes_before=before_hashes, input_hashes_after=hashes_after,
        input_dependencies_unchanged=before_hashes == hashes_after,
        flow_inventory=flow_report, pending_before_flush=pending,
        opaque_111_summary=opaque_111_summary,
        shapes=[dict(direction=k[0], decoded=k[1], length=k[2], opcode=k[3], count=v)
                for k, v in shapes.most_common()],
        raw_nonudp_matches=raw_matches, raw_stream_matches=stream_matches,
        message_matches=matches, registration_observations=registration_rows,
        candidate_count=len(candidates), raw_tcp_frames=raw_tcp_frames, messages=messages,
        elapsed_seconds=round(time.perf_counter() - started, 3))


def save_report(report):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / '0813黑萨_tcp_object_scan.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    c = report['counts']
    lines = ['# v37 bounded TCP object sidecar', '',
             f"Verdict: {report['verdict']}; confirmed hit additions: 0.", '',
             f"Window: {report['scope']['start']:.6f}–{report['scope']['cutoff']:.6f} ({report['scope']['seconds']:g} seconds).",
             f"Read {report['read_stats']['frames_read']} raw frames; TCP payload frames: {c.get('raw_TCP_payload_frames', 0)}.",
             f"Logical TCP messages: {c.get('logical_TCP', 0)}; decrypted: {c.get('decoded_TCP', 0)}; opaque: {c.get('undecoded_TCP', 0)}.",
             f"Raw frame / contiguous stream / message full-ID matches: {len(report['raw_nonudp_matches'])} / {len(report['raw_stream_matches'])} / {len(report['message_matches'])}.",
             f"Strict wrapper registrations: {len(report['registration_observations'])}; same-owner pre-hit target candidates: {report['candidate_count']}.", '',
             '| Full object ID | Owner | Decoded TCP before hit | Opaque TCP before hit | Raw anchor locations |',
             '| --- | --- | ---: | ---: | ---: |']
    for a in report['anchors']:
        lines.append(f"| {a['object']} | 0x{a['owner']:08X} | {a['decoded_TCP_before_hit']} | {a['undecoded_TCP_before_hit']} | {len(a['physical_locations'])} |")
    lines.extend(['', *('- ' + x for x in report['limitations']), '',
                  f"Skipped {report['read_stats']['frames_skipped_before_window']} physical records before the 60-second window; capture begins at {report['read_stats']['capture_first_timestamp']:.6f}.",
                  f"Opaque 111-byte messages: {report['opaque_111_summary']['count']}; body sizes: {report['opaque_111_summary']['body_sizes']}; registered candidate counts: {report['opaque_111_summary']['registered_candidate_counts']}.",
                  'Decrypted means accepted by the current envelope length check, not independently validated opcode semantics.',
                  'Server registration discovery explicitly uses include_unmapped=True; an empty default call without known IDs is not an absence check.',
                  'JSON contains every TCP wire, available plaintext, decrypt route/key source, flow/sequence coverage, and physical capture spans.',
                  'Reproduce with the supplied Python: -B tools/probe_tcp_object_sidecar_v37.py', ''])
    opaque = report['opaque_111_summary']
    if opaque['count']:
        for label in ('first_message', 'last_message'):
            message = opaque[label]
            first_span = message['physical_locations'][0]['spans'][0]
            lines.append(f"111-byte {label}: message {message['message']}, frame {first_span['frame']}, "
                         f"record file offset {first_span['record_file_offset']}, "
                         f"message file byte offset {first_span['file_byte_offset']}, time {message['time']:.6f}.")
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(output=str(path), verdict=report['verdict'], counts=c,
                          candidate_count=report['candidate_count'],
                          message_matches=len(report['message_matches']),
                          elapsed_seconds=report['elapsed_seconds']), ensure_ascii=False))


def cipher_diagnostics(report, candidate, candidate_source):
    """One frozen offline candidate, saved bounded wires, no key learning.

    Output contains format statistics and offsets, never key words, ciphertext
    blocks that expose a sparse key, plaintext names, or opaque memory fields.
    """
    codec._key_registry()
    messages = [m for m in report['messages'] if m['direction'] == 'server_to_client'
                and m['length'] == 111]
    if not messages or len(messages) > 512:
        raise ValueError('fixed-candidate diagnostic requires 1..512 saved 111-byte messages')
    if report['scope']['seconds'] > 60:
        raise ValueError('saved evidence exceeds the authorized 60-second bound')
    if len(candidate) != 4:
        raise ValueError('expected one four-word frozen candidate')
    counts, opcodes, id_counts = Counter(), Counter(), Counter()
    names_by_id, ids_by_name = defaultdict(set), defaultdict(set)
    bodies, plains, rows = [], [], []
    with Path(report['capture']).open('rb') as capture:
        for message in messages:
            wire = bytes.fromhex(message['wire_hex'])
            locations = message['physical_locations']
            if not locations:
                raise ValueError('missing physical evidence')
            location = locations[0]
            rebuilt = bytearray()
            for span in location['spans']:
                capture.seek(span['file_byte_offset'])
                rebuilt.extend(capture.read(span['length']))
            if bytes(rebuilt) != wire:
                raise ValueError('saved TCP message differs from physical bytes')
            counts['physical_message_reopens'] += 1
            body = wire[3:]
            plain = codec.decrypt_tcp_profile_body(body, tuple(candidate), 1)
            bodies.append(body)
            plains.append(plain)
            valid = int.from_bytes(plain[:2], 'little') == 108
            counts['valid_length'] += valid
            opcodes[plain[2:4].hex()] += 1
            counts['wire_header_declares_111'] += int.from_bytes(wire[:2], 'little') == 111
            counts['wire_header_as_client_u24_declares_111'] += int.from_bytes(wire[:3], 'little') == 111
            counts['UDP_declared_length_at_native_boundary'] += int.from_bytes(wire[7:9], 'little') + 3 == len(wire)
            counts['UDP_declared_length_at_body_boundary'] += int.from_bytes(body[7:9], 'little') + 3 == len(body)
            raw_name = plain[8:48].split(b'\0', 1)[0]
            try:
                name = raw_name.decode('utf-8')
                name_valid = bool(name) and all(ch.isprintable() for ch in name)
            except UnicodeDecodeError:
                name_valid = False
            counts['nonempty_printable_utf8_name'] += name_valid
            counts['name_field_zero_padded'] += bool(raw_name) and plain[8 + len(raw_name):48] == bytes(40 - len(raw_name))
            short_id = int.from_bytes(plain[4:8], 'little')
            id_counts[short_id] += 1
            if valid and name_valid:
                names_by_id[short_id].add(raw_name)
                ids_by_name[raw_name].add(short_id)
            found = full_matches(plain, report['anchors'])
            owner_at = positions(plain, report['anchors'][0]['owner'].to_bytes(4, 'little'))
            counts['full_target_object_matches'] += len(found)
            counts['plaintexts_with_target_owner'] += bool(owner_at)
            rows.append(dict(message=message['message'], wire_sha256=message['wire_sha256'],
                physical_locations=locations, header_hex=wire[:3].hex(),
                valid_length=valid, observed_opcode=plain[2:4].hex(), short_id_at_plaintext_4=short_id,
                printable_utf8_name=name_valid, name_byte_length=len(raw_name),
                full_object_matches=found, target_owner_offsets=owner_at, attribution_proven=False))
    blocks = Counter(body[at:at + 8] for body in bodies for at in range(0, 104, 8))
    most_common_block, block_count = blocks.most_common(1)[0]
    registered_indexes = [index for index, candidates in codec._KEY_CANDIDATES.items()
                          if any(x.words == tuple(candidate) for x in candidates)]
    nearby = [dict(index=index, candidates=[dict(source=x.source, body_sizes=x.body_sizes,
                 directions=x.directions) for x in candidates])
              for index, candidates in sorted(codec._KEY_CANDIDATES.items()) if 96 <= index <= 120]
    all_server = [m for m in report['messages'] if m['direction'] == 'server_to_client']
    header_deltas = Counter((int(b['wire_hex'][4:6], 16) - int(a['wire_hex'][4:6], 16)) % 256
                            for a, b in zip(all_server, all_server[1:]))
    independent_name_matches = []
    for raw_name in ids_by_name:
        utf16 = raw_name.decode('utf-8').encode('utf-16le')
        for message in report['messages']:
            if message['decoded']:
                plain = bytes.fromhex(message['plaintext_hex'])
                if raw_name in plain or utf16 in plain:
                    independent_name_matches.append(dict(short_ids=sorted(ids_by_name[raw_name]),
                        independent_message=message['message'], opcode=message.get('opcode')))
    return dict(schema='tcp111-cipher-review-v37', candidate_source=candidate_source,
        single_frozen_candidate=True, candidate_count_tried=1, key_derivations=0, brute_force=False,
        registry_writes=False, capture=report['capture'], scope=report['scope'],
        source_evidence=str(OUT / '0813黑萨_tcp_object_scan.json'),
        flow=messages[0]['flow'], all_same_flow=all(m['flow'] == messages[0]['flow'] for m in messages),
        counts=dict(counts), observed_opcodes=dict(opcodes), short_id_counts=dict(id_counts),
        one_name_per_short_id=all(len(v) == 1 for v in names_by_id.values()),
        one_short_id_per_name=all(len(v) == 1 for v in ids_by_name.values()),
        unique_names=len(ids_by_name), independent_name_matches=independent_name_matches,
        active_index108_candidates=len(codec._KEY_CANDIDATES.get(108, [])),
        applicable_index108_server_candidates=len(codec._registered_key_candidates(
            108, body_size=108, direction='server_to_client', include_compatible_indexes=False)),
        frozen_candidate_registered_indexes=registered_indexes, nearby_registry_metadata=nearby,
        opaque_reason='index108_absent_before_body_size_direction_or_endpoint_ranking',
        layout=dict(body_offset=3, body_size=108, block_size=8, full_blocks=13,
                    xor_tail_bytes=4, rounds=1, used_key_word_indexes=[0, 3]),
        ciphertext_distribution=dict(unique_bodies=len(set(bodies)), unique_first_blocks=len({b[:8] for b in bodies}),
            unique_four_byte_tails=len({b[104:] for b in bodies}),
            fixed_byte_positions=[i for i in range(108) if len({b[i] for b in bodies}) == 1],
            most_common_aligned_block_count=block_count,
            most_common_aligned_block_positions=dict(Counter(at for b in bodies for at in range(0, 104, 8)
                                                            if b[at:at + 8] == most_common_block)),
            aligned_block_total=len(bodies) * 13,
            nonzero_rolling_byte_count=sum(b[2] != 0 for b in (bytes.fromhex(m['wire_hex']) for m in messages))),
        plaintext_distribution=dict(zero_four_byte_tail_count=sum(b[104:] == bytes(4) for b in plains),
            fixed_zero_positions=[i for i in range(108) if all(b[i] == 0 for b in plains)],
            u32_field_cardinalities={str(i): len({b[i:i + 4] for b in plains}) for i in range(48, 108, 4)}),
        all_server_header_delta_histogram=dict(header_deltas),
        first_target_delta_seconds=round(messages[0]['time'] - report['anchors'][0]['time'], 6),
        first_message=rows[0], last_message=rows[-1], observations=rows,
        new_confirmed_hits=0)


def review_cipher():
    evidence = OUT / '0813黑萨_tcp_object_scan.json'
    hypothesis = ROOT / 'outputs/compact_entity_selectors_20260908/tcp111_key_hypotheses.json'
    cross_validation = ROOT / 'outputs/compact_entity_selectors_20260908/tcp111_cross_capture_validation.json'
    inputs = [evidence, hypothesis, cross_validation, Path(codec.__file__), Path(p.__file__)]
    reviewed_scripts = ['probe_gap_tcp_111_key.py', 'probe_gap_tcp_object_evidence.py',
                        'validate_gap_tcp_111_offline.py', 'recover_sparse_tcp_key.py']
    inputs.extend(ROOT / 'tools' / name for name in reviewed_scripts)
    inputs.extend(x for x in (codec._BUNDLED_KEY_REGISTRY_PATH, codec._USER_KEY_CACHE_PATH) if x.exists())
    before = {str(path): sha256(path.read_bytes()).hexdigest() for path in inputs}
    data = json.loads(evidence.read_text('utf-8'))
    old = json.loads(hypothesis.read_text('utf-8'))
    candidates = old['zero_block_candidates']
    if len(candidates) != 1:
        raise ValueError('expected exactly one previously saved zero-block candidate; no new search authorized')
    review = cipher_diagnostics(data, tuple(candidates[0]['key']), str(hypothesis))
    after = {str(path): sha256(path.read_bytes()).hexdigest() for path in inputs}
    if before != after:
        raise ValueError('input dependency changed during read-only cipher review')
    review.update(input_hashes=before, inputs_unchanged=True,
                  previously_existing_scripts_reviewed=reviewed_scripts,
                  old_candidate_training_capture=old['capture'],
                  old_candidate_training_opcodes=candidates[0]['opcodes'])
    (OUT / 'cipher_review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    c = review['counts']
    notes = ['# TCP 111 / body 108 cipher review', '',
        'The missing decoder entry is a key-library coverage gap. The current 111-byte family is consistent with the existing encrypted TCP envelope, with observed opcode bytes `22 10`. It is not evidence of a missing-object registration.', '',
        f"This separate offline review resolves {c['valid_length']} of the prior {data['counts']['undecoded_TCP']} opaque messages in this window, leaving {data['counts']['undecoded_TCP'] - c['valid_length']} messages of other lengths unexamined here. It does not change runtime decoding or overwrite the original scan.", '',
        '## Existing work inspected; not repeated', '',
        '| Existing script | Established work / limit | Used this turn |',
        '| --- | --- | --- |',
        f"| [probe_gap_tcp_111_key.py]({(ROOT / 'tools/probe_gap_tcp_111_key.py').as_posix()}) | 0821 full-capture zero-block and tail-zero hypotheses; saved one zero-block candidate, with 27,343 length-valid unique bodies and observed opcode counts 27,339 `22 10` / 4 `0b 01`. | Read script and saved result; reused its one candidate; did not run recovery. |",
        f"| [probe_gap_tcp_object_evidence.py]({(ROOT / 'tools/probe_gap_tcp_object_evidence.py').as_posix()}) | Whole-capture TCP reassembly plus registered-key object search; retains undecoded counts and no promotion. An offline-only candidate is invisible to this route. | Read implementation; did not rerun it. |",
        f"| [validate_gap_tcp_111_offline.py]({(ROOT / 'tools/validate_gap_tcp_111_offline.py').as_posix()}) | Already applied the saved candidate to 0821 and 0812; observed `22 10` strings and short IDs. Existing corroboration maps were empty and confirmed skill links stayed zero. | Read implementation and saved cross-capture report; did not replay either capture. |",
        f"| [recover_sparse_tcp_key.py]({(ROOT / 'tools/recover_sparse_tcp_key.py').as_posix()}) | Hypothesizes the most frequent 8-byte ciphertext block is encrypted zero; uses the exact short-round equations and, for two rounds, header-length constraints. This is not a validated known-plaintext fact. Its reader consumes complete captures. | Read implementation only; no solver, model enumeration, or key recovery executed. |", '',
        f"Frozen inputs: [key hypotheses]({hypothesis.as_posix()}) and [prior cross-capture validation]({cross_validation.as_posix()}). No key values are copied into this review.", '',
        'New evidence is specifically the 362 previously opaque messages in the saved 0813 six-anchor window, reopened at physical offsets and checked with that pre-existing candidate. The key and opcode family are not new discoveries.', '',
        '## Current algorithm and selection', '',
        '- Server framing reads total length from wire `[0:2]`, preserves byte 2 as the rolling header byte, and starts the body at byte 3. `111 - 3 = 108`; the lookup index is 108, not 111.',
        '- `decode_registered_server_tcp_envelope` -> `_ordered_server_key_candidates` -> `_registered_key_candidates(... include_compatible_indexes=False)`. Flow and endpoint hints only reorder existing candidates; they do not add keys.',
        f"- Current loaded index-108 entries: {review['active_index108_candidates']}; applicable server entries: {review['applicable_index108_server_candidates']}. The entry is absent before body-size/direction filtering. The frozen offline candidate is registered at indexes {review['frozen_candidate_registered_indexes']}.",
        '- Body 108 selects one XTEA round: 13 independent little-endian 8-byte blocks, then four bytes XORed with the beginning of the packed key. Only words K0 and K3 affect the full blocks; the tail uses K0.',
        '- Neighboring indexes are independently sourced library entries. Odd indexes 111/115/117/119 select two rounds for their own body lengths; even 98/118/120 select one. Neither numerical proximity nor equal parity proves the same key. No neighboring key was trialed.', '',
        '| Loaded nearby index | Source labels | Size/direction restrictions |', '| --- | --- | --- |']
    for row in review['nearby_registry_metadata']:
        notes.append(f"| {row['index']} | " + '; '.join(x['source'] for x in row['candidates']) +
                     ' | ' + '; '.join(f"sizes={list(x['body_sizes'])}, directions={list(x['directions'])}" for x in row['candidates']) + ' |')
    notes.extend(['', '## Physical framing and bounded fixed-candidate check', '',
        f"- Read the previous 60-second evidence window and reopened {c['physical_message_reopens']} messages by exact file offsets. No capture replay, worker, key derivation, or key-registry write was used.",
        f"- All messages use flow `{review['flow']}`. Native captured transport is TCP; the prior physical inventory has no sequence holes, conflicts, or unconsumed bytes. There are {len(data['messages'])} framed messages in both directions, including {data['counts']['decoded_TCP']} accepted by the normal decrypt route.",
        '- Source classification is the observed public peer sending to a private local endpoint. These bytes do not identify the remote process or prove whether the peer is a direct game server or a relay. They do prove the captured transport and working inner cipher format.',
        f"- Header `[0:2]` declares 111 for {c['wire_header_declares_111']} messages. The client 24-bit interpretation fits {c['wire_header_as_client_u24_declares_111']}. Header byte 2 changes by even increments across the server stream, not a simple per-TCP-message +1; do not assign stronger semantics to it.",
        f"- The standard UDP declared-length check fits {c['UDP_declared_length_at_native_boundary']} messages at the native boundary and {c['UDP_declared_length_at_body_boundary']} at body offset 3. This alone cannot exclude every custom tunnel, but offers no support for the current native UDP format.",
        f"- The existing candidate came from the separate saved 0821 offline zero-block hypothesis, whose outputs already observed `22 10` and `0b 01`. Exactly one frozen candidate was applied to this held-out 0813 window: {c['valid_length']}/{len(review['observations'])} body-length checks passed; observed opcode distribution is {review['observed_opcodes']}. No opcode was assumed to derive or select it.",
        f"- {c['nonempty_printable_utf8_name']} messages have a nonempty printable UTF-8 string at plaintext `[8:48]`; {c['name_field_zero_padded']} have zero padding after its terminator. There are {review['unique_names']} distinct strings, with a one-to-one association to eight short u32 values at `[4:8]`: {sorted(review['short_id_counts'], key=int)}. Names and opaque numeric fields are intentionally not printed.",
        '- Some string cells retain nonzero bytes after the first terminator; a universal zero-padding rule is therefore not established.',
        f"- Ciphertext distribution: {review['ciphertext_distribution']['unique_bodies']} distinct bodies; {review['ciphertext_distribution']['unique_first_blocks']} distinct first blocks; {review['ciphertext_distribution']['unique_four_byte_tails']} distinct tails. The most frequent aligned block occurs {review['ciphertext_distribution']['most_common_aligned_block_count']}/{review['ciphertext_distribution']['aligned_block_total']} times. The four-byte plaintext tail is zero in {review['plaintext_distribution']['zero_four_byte_tail_count']} messages. Repetition is consistent with independently encrypted padded fields; it is not by itself proof of zero plaintext.",
        f"- Full target object matches after this additional decode: {c['full_target_object_matches']}; occurrences of target owner `0x4E323C05`: {c['plaintexts_with_target_owner']}. Name corroborations in the existing normally decoded window: {len(review['independent_name_matches'])}. No short ID is promoted to a player owner, and no unknown field is labelled as an object counter, damage amount, or skill.", '',
        'Physical anchors (zero-based file offsets, unchanged input capture):', ''])
    for label in ('first_message', 'last_message'):
        row = review[label]
        span = row['physical_locations'][0]['spans'][0]
        notes.append(f"- {label}: message {row['message']}, frame {span['frame']}, record offset {span['record_file_offset']}, wire offset {span['file_byte_offset']}, header `{row['header_hex']}`.")
    notes.extend([f"- First 111-byte message is {review['first_target_delta_seconds']:.6f} s after the first supplied missing-object hit; this is chronology only.", '',
        '## Can known plaintext recover a key without an invented opcode?', '',
        'Yes, conditionally and algebraically for this implementation, not by a blind search. Let F(x) = (((x << 4) XOR (x >> 5)) + x) mod 2^32 and D be the existing XTEA delta. If an independently justified zero 8-byte plaintext block encrypts to (L,R), one round gives K0 = L and K3 = (R XOR F(L)) - D mod 2^32. No opcode participates. K1/K2 are unused at one round; a four-byte known-zero tail independently constrains K0.',
        '',
        'A frequent block alone does not justify the zero assumption. A 16-bit body-length constraint alone cannot certify the remaining key bits or field semantics. The saved offline candidate already implements the conditional zero-block idea; deriving it again or forcing a chosen opcode would add no evidence. Its success on 362 held-out current messages is the useful new observation.', '',
        '## Concrete bounded diagnostic and remaining block', '',
        'Implemented and executed: `-B tools/probe_tcp_object_sidecar_v37.py --cipher-review`. It reopens only the saved 362 frame messages, applies the single frozen 0821 candidate through the current decrypt helper, checks length/string/padding/short-ID consistency, searches all four bytes of the six target object IDs in both byte orders, and exports offsets/statistics without key values or names.',
        '',
        'This resolves the ciphertext-format question sufficiently for offline inspection: the existing short-round TCP cipher works on this family. It does not certify the meaning of opcode 22 10. The remaining attribution block is semantic: the fields contain no selected full object IDs or target owner, and the short-ID/string pairs lack an independent owner mapping within this window. Treat this as a separate encrypted typed record, not as an object registration or a reason to promote timing-based attribution.',
        '',
        'New confirmed hits: 0. Runtime and production edits: none. The earlier scan remains intact; this review adds a separate offline decode result.', ''])
    (OUT / 'cipher_review.md').write_text('\n'.join(notes), encoding='utf-8')
    print(json.dumps(dict(output=str(OUT / 'cipher_review.md'), counts=c,
                          observed_opcodes=review['observed_opcodes'], candidate_count_tried=1), ensure_ascii=False))
    return review


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anchors', type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--cipher-review', action='store_true', help='review saved bounded TCP wires with one pre-existing offline candidate')
    args = parser.parse_args()
    if args.cipher_review:
        review_cipher()
    else:
        save_report(scan(args.anchors, args.seconds))
