"""Bounded direct41 group regression fixtures. No capture replay/runtime edit.

The main task owns the protocol fix. This probe only copies selected existing
raw examples and reports constructors/identity paths losing marker+4. No new
CombatEvent schema, per-Hit instrumentation, object guess, or coverage gain.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps import sources
from dragon_nest_builder.live_dps import protocol_v576 as protocol
from dragon_nest_builder.live_dps.damage_wire_evidence import damage_wire_metadata
from dragon_nest_builder.live_dps.models import CombatEvent
from dragon_nest_builder.live_dps.replay_normalization import _canonicalize_live_message
from dragon_nest_builder.live_dps.udp_damage_identity import canonicalize_udp_damage_identity
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from dragon_nest_builder.live_dps.v576 import core_decoder

OUT = ROOT / 'outputs/metadata_paths_v36_20260908'
FIXTURES = OUT / 'direct41_fixtures.json'
PREFIX = 'dragon_nest_builder/live_dps/'
AUDITED = tuple(PREFIX + name for name in (
    'protocol_v576.py', 'udp_damage_identity.py', 'damage_wire_evidence.py',
    'v576/core_decoder.py', 'v576/combat_decoder.py', 'v576/status_decoder.py',
    'late_field_hit_evidence_v32.py', 'replay_normalization.py', 'models.py',
    'damage_ledger.py', 'capture_sources.py',
))
KEY = bytes.fromhex('c8bfcda4943f2642fd8a723474')
OWNER, TARGET, GROUP = 0x50112233, 46, 23372651


def source_hashes():
    return {p: sha256((ROOT / p).read_bytes()).hexdigest() for p in AUDITED}


def packet(wire, timestamp=100.0):
    return protocol.PacketRecord(timestamp, 'UDP', '118.25.23.98', '10.1.39.221',
        15161, 57027, wire, len(wire), len(wire), direction_hint='server_to_client')


def synthetic_wire(family='direct41', skill=1624, source=OWNER, group=GROUP):
    """Complete ordinary/player variant; numeric values are plumbing controls."""
    body = bytearray(140)
    body[5:7] = b'\x65\x67'
    struct.pack_into('<H', body, 7, len(body)-3)
    struct.pack_into('<I', body, 10, OWNER)
    struct.pack_into('<IBH', body, 14, TARGET, 8, 119)
    marker = 18
    struct.pack_into('<I', body, marker+4, group)
    struct.pack_into('<I', body, marker+8, source)
    # +13..+21 is opaque. Do not treat any part of it as an object ID.
    body[marker+13:marker+21] = bytes.fromhex('123456789abcdef0')
    body[marker+21:marker+23] = b'\xff\xff' if family == 'direct41' else b'\0\0'
    struct.pack_into('<Q', body, marker+23, 158800)
    body[marker+39] = 1
    struct.pack_into('<I', body, marker+106, skill)
    return bytes(body[:9]) + bytes(v ^ KEY[(i-1) % 13] for i, v in enumerate(body[9:], 9))


def read_row(wire, offset):
    inventory = inspect_udp_damage_records(wire, 'server_to_client')
    assert inventory.complete, inventory.rejection_reason
    row, = [r for r in inventory.damage_records if r.record_offset == offset and r.family == 'direct41']
    assert row.admissible_fields and row.damage_object_id is None
    return row


def decode_fixture(case):
    wire = bytes.fromhex(case['wire'])
    assert sha256(wire).hexdigest() == case['wire_sha256']
    decoded = protocol.decode_v576_compound_damage(wire, 'server_to_client', frozenset(case['catalog']))
    assert decoded is not None
    return wire, decoded, read_row(wire, case['record_offset'])


def empty_decoder(catalog):
    """Real core initializer; catalog loaders are replaced, no resource scan."""
    with ExitStack() as stack:
        for name in core_decoder.V576DecoderCoreMixin.__init__.__code__.co_names:
            if name.startswith(('_load_', 'load_inspect_')) and hasattr(core_decoder, name):
                value = frozenset() if name in ('_load_damage_skill_ids', '_load_team_buff_skill_names') else {}
                stack.enter_context(patch.object(core_decoder, name, return_value=value))
        decoder = sources._V576PacketEventDecoder(ROOT / '__v36_no_catalog_reads__')
    decoder.skill_catalog = {s: (f'synthetic {s}', 'synthetic job', 0) for s in catalog}
    decoder.damage_skill_ids = decoder.action_skill_ids = frozenset(catalog)
    decoder.status_known_skill_ids = decoder.damage_skill_ids
    decoder.known_actor_names[OWNER] = 'synthetic owner'
    decoder.skill_actor_ids.add(OWNER)
    decoder.confirmed_damage_target_ids.add(TARGET)
    decoder.first_timestamp = 99.0
    return decoder


def build_fixtures():
    fixtures = []
    seen = set()
    # Preserve the user-reported 23372651 independently of inferred labels.
    anchor_path = ROOT / 'outputs/named_gap_wire_v36_20260908/20260806YEP黑典时空.json'
    anchor_bytes = anchor_path.read_bytes()
    anchor_audit = json.loads(anchor_bytes)
    anchor = next(e for g in anchor_audit['rows'] for e in g['examples'] if e['serial'] == 2445)
    wire = bytes.fromhex(anchor['wire'])
    row = read_row(wire, anchor['event']['wire_record_offset'])
    decoded = protocol.decode_v576_compound_damage(wire, 'server_to_client', {1624})
    assert decoded is not None and decoded.record_offset == row.record_offset
    assert row.damage_result_group_id == 23372651
    fixtures.append(dict(id='shadow_reported:2445', source=str(anchor_path.relative_to(ROOT)),
        source_sha256=sha256(anchor_bytes).hexdigest(), wire=wire.hex(), wire_sha256=sha256(wire).hexdigest(),
        catalog=[1624], layout=decoded.layout, saved_layout=anchor['event']['wire_damage_layout'],
        record_offset=row.record_offset, expected_group=row.damage_result_group_id,
        saved_event=anchor['event'], kind='saved_raw_example'))
    for name in ('20260806YEP黑典时空', '0813硬核火舞', '0821黑典女王'):
        path = ROOT / f'outputs/named_gap_wire_v36_20260908/{name}.json'
        content = path.read_bytes()
        audit = json.loads(content)
        for group in audit['rows']:
            if 'direct41' not in group['layout'] or group['layout'] in seen:
                continue
            for sample in group['examples']:
                e = sample['event']
                wire = bytes.fromhex(sample['wire'])
                row = read_row(wire, e['wire_record_offset'])
                # An empty/nonmatching catalog preserves continuation branches;
                # the old label is used only to reproduce the saved decode path.
                catalogs = ({int(e['skill_id'])}, {1624}, {int(e['skill_id']), 1624})
                for catalog in catalogs:
                    decoded = protocol.decode_v576_compound_damage(wire, 'server_to_client', catalog)
                    if decoded is not None and decoded.layout == group['layout'] and decoded.record_offset == row.record_offset:
                        break
                else:
                    continue
                assert (row.source_actor_id, row.target_actor_id, row.damage, row.critical) == (
                    e['wire_source_actor_id'], e['wire_target_actor_id'], e['amount'], e['wire_critical'])
                fixtures.append(dict(id=f'{name}:{sample["serial"]}', source=str(path.relative_to(ROOT)),
                    source_sha256=sha256(content).hexdigest(), wire=wire.hex(), wire_sha256=sha256(wire).hexdigest(),
                    catalog=sorted(catalog), layout=decoded.layout, record_offset=row.record_offset,
                    expected_group=row.damage_result_group_id, saved_event=e, kind='saved_raw_example'))
                seen.add(group['layout'])
                break
    # Existing standalone status variant fixture: read only this AST constant.
    path = ROOT / 'tests/test_live_dps.py'
    tree = ast.parse(path.read_text('utf-8'))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == 'test_v576_decodes_straight_line_impact_status_hit_variant')
    assign = next(n for n in func.body if isinstance(n, ast.Assign) and n.targets[0].id == 'payload')
    wire = bytes.fromhex(ast.literal_eval(assign.value.args[0]))
    decoded = protocol.decode_v576_compound_damage(wire, 'server_to_client', {205, 6216})
    assert decoded is not None and decoded.layout == 's2c-udp-hit-effect-direct41-status-v576'
    # This legacy partial typed packet need not pass the complete inventory;
    # verify m+4 directly in the exact decoder-selected XOR phase instead.
    phases = [phase for phase in range(13) if protocol._decode_v576_body(wire, phase)[52:55] == b'\x08\x77\x00']
    assert len(phases) == 1
    body = protocol._decode_v576_body(wire, phases[0])
    expected = int.from_bytes(body[56:60], 'little')
    fixtures.append(dict(id='status174_existing_fixture', source='tests/test_live_dps.py',
        source_test=func.name, kind='legacy_raw_status_fixture', xor_phase=phases[0],
        wire=wire.hex(), wire_sha256=sha256(wire).hexdigest(), catalog=[205, 6216],
        layout=decoded.layout, record_offset=52, expected_group=expected))
    return fixtures


def load_fixtures():
    return json.loads(FIXTURES.read_text('utf-8'))['cases']


def constructor_locations():
    locations = []
    for name in ('protocol_v576.py', 'udp_damage_identity.py'):
        path = ROOT / (PREFIX + name)
        source = path.read_text('utf-8')
        tree = ast.parse(source)
        for function in [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {
            'decode_v576_compound_damage', 'decode_v576_embedded_direct41_damage_events',
            'canonicalize_udp_damage_identity'}]:
            for node in ast.walk(function):
                if isinstance(node, ast.keyword) and node.arg == 'damage_result_group_id':
                    locations.append(dict(file=PREFIX+name, function=function.name, line=node.lineno,
                        expression=ast.get_source_segment(source, node.value)))
                elif (function.name == 'decode_v576_embedded_direct41_damage_events'
                      and isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == 'DecodedV576Message'):
                    locations.append(dict(file=PREFIX+name, function=function.name, line=node.lineno,
                        supplies_group=any(k.arg == 'damage_result_group_id' for k in node.keywords)))
    return locations


def main():
    before = source_hashes()
    cases = build_fixtures()
    observations = []
    for case in cases:
        wire = bytes.fromhex(case['wire'])
        decoded = protocol.decode_v576_compound_damage(wire, 'server_to_client', frozenset(case['catalog']))
        normalized = _canonicalize_live_message(decoded)
        repaired = canonicalize_udp_damage_identity(packet(wire), normalized)
        observations.append(dict(id=case['id'], layout=decoded.layout, expected_m4=case['expected_group'],
            constructor_group=decoded.damage_result_group_id, normalized_group=normalized.damage_result_group_id,
            identity_group=repaired.damage_result_group_id,
            event_metadata_group=damage_wire_metadata(repaired)['wire_result_group_id']))
    after = source_hashes()
    assert before == after, 'Production changed concurrently; rerun bounded observation.'
    OUT.mkdir(parents=True, exist_ok=True)
    FIXTURES.write_text(json.dumps(dict(schema_version=36, cases=cases), ensure_ascii=False, indent=2), 'utf-8')
    report = dict(schema_version=36, scope='direct41 raw group only; no schema/runtime patch, no replay',
        source_sha256=before, production_sources_unchanged_during_probe=True,
        constructors=constructor_locations(), cases=observations,
        aliases=['direct41', 'direct41-player', 'direct41-status',
                 'direct41-continuation', 'direct41-player-continuation', 'direct41-status-continuation',
                 'direct41-bundled'],
        notes=['Compound group constructor filters only direct37; a future proposal must address all direct41 aliases.',
               'Embedded direct41 constructor omits group even though its existing row already supplies m+4.',
               'Identity guard currently rejects any existing direct41 group; final replace explicitly clears it.',
               'damage_wire_metadata copies supplied group correctly; None becomes zero. No helper expansion needed.',
               'Skill aliases preserve extra decoded fields via dataclasses.replace.',
               'Retaining raw m+4 as the existing group field is an unapproved semantic contract, not a proven safe fix.',
               'No early-return CombatEvent or wrong-current-packet path proven. Source uses original logical_packet.',
               'Raw skill/proof schema proposal withdrawn during main-agent scope/safety review.',
               'Current direct41 object/group absence is an intentional conservative policy, not a proven decoder fault.',
               'Group alone is not a skill/object link. No change to admission, Hit bits, score or UI.'],
        coverage_gain=0)
    (OUT / 'evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(dict(fixtures=len(cases), constructor_missing=sum(x['constructor_group'] != x['expected_m4']
        for x in observations), metadata_missing=sum(x['event_metadata_group'] != x['expected_m4']
        for x in observations), production_unchanged=True), ensure_ascii=True))


if __name__ == '__main__':
    main()
