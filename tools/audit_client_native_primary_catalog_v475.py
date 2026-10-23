"""Recheck pinned client tables, ACTs, native bytes and exact capture fixtures.

Read-only verification. This does not replay, emit damage or change labels.
Run: python tools/audit_client_native_primary_catalog_v475.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.build_dungeon_map_catalog import parse_dnt
from tools.build_attack_instance_catalog import parse_act
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame, split_v576_payload
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records
from dragon_nest_builder.live_dps.native_damage_skill_keys import _selected_native_key

EVIDENCE=ROOT/'outputs/verification/native_primary_catalog_v475/evidence.json'

def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main() -> None:
    evidence=json.loads(EVIDENCE.read_text(encoding='utf8'))
    image=Path(evidence['memory_path'])
    assert digest(image)==evidence['memory_sha256'], 'native image version differs'
    with image.open('rb') as stream:
        for offset, expected in evidence['native_anchors'].items():
            stream.seek(int(offset,16))
            assert stream.read(len(expected)//2).hex()==expected, offset
    ui=Path(evidence['ui']['path'])
    assert digest(ui)==evidence['ui']['sha256']
    labels={e.get('mid'):e.text or '' for e in ET.parse(ui).iter('message')}
    for mid,label in evidence['ui']['labels'].items():
        assert labels[mid]==label,(mid,label)
    for name,entry in evidence['tables'].items():
        path=Path(entry['path'])
        assert digest(path)==entry['sha256'],name
        rows={r['RowId']:r for r in parse_dnt(path)}
        for row in entry['rows']:
            assert rows[row['RowId']]==row,(name,row['RowId'])
    for entry in evidence['actions']:
        path=Path(entry['path'])
        assert digest(path)==entry['sha256'],str(path)
        action=next(a for a in parse_act(path).actions if a.name==entry['name'])
        for signal in entry['signals']:
            actual=action.signals[signal['index']]
            assert (actual.signal_type,actual.order,actual.payload.hex()) == (
                signal['type'],signal['order'],signal['payload'])
    verified=[]
    for fixture in evidence['wire_fixtures']:
        path=Path(fixture['capture']['path'])
        assert digest(path)==fixture['capture_sha256'],str(path)
        matches=[]
        when=fixture['origin']+fixture['timestamp_ms']/1000
        for timestamp,adapter,size,raw in read_trace(path):
            if abs(timestamp-when)>0.0021:continue
            packet=decode_captured_frame(timestamp,raw,size)
            if packet is None or packet.transport!='UDP':continue
            for wire in split_v576_payload(packet.payload):
                inventory=inspect_udp_damage_records(wire,packet.inferred_direction())
                if not inventory.complete:continue
                for record in inventory.damage_records:
                    if (record.wire_record_sha256!=fixture['record_sha256']
                            or record.record_offset!=fixture['event']['wire_record_offset']):
                        continue
                    assert record.record_bytes.hex()==fixture['raw_hex']
                    assert record.admissible_fields
                    assert _selected_native_key(record)==tuple(fixture['exact_selection'])
                    matches.append(record.wire_record_sha256)
        assert matches,('exact physical fixture not found',path)
        verified.append({'skill_id':fixture['exact_selection'][1],
                         'capture_sha256':fixture['capture_sha256'],
                         'record_sha256':fixture['record_sha256'],
                         'matching_physical_occurrences':len(matches)})
    output={'verified':True,'evidence_sha256':digest(EVIDENCE),
            'memory_sha256':evidence['memory_sha256'],'fixtures':verified}
    (EVIDENCE.parent/'verified.json').write_text(
        json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(output,ensure_ascii=True))

if __name__=='__main__':
    main()
