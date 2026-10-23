"""Audit raw Boss 0e16 records without the production skill whitelist."""
import json
import sys
from pathlib import Path
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body, _V576_BODY_XOR_KEY, _v576_wire_may_contain_marker

def audit(path):
    original = _V576PacketEventDecoder.feed_events
    rows = []
    marker_packets = 0
    def feed(self, packet):
        nonlocal marker_packets
        events = original(self, packet)
        marker = b'\x0e\x16\x00'
        if packet.transport.upper() != 'UDP' or packet.inferred_direction() != 'server_to_client':
            return events
        if not _v576_wire_may_contain_marker(packet.payload, (marker,), start_offset=13):
            return events
        marker_packets += 1
        candidates = {}
        for phase in range(len(_V576_BODY_XOR_KEY)):
            body = _decode_v576_body(packet.payload, phase)
            offset = body.find(marker, 13)
            found = []
            while offset >= 0:
                actor = int.from_bytes(body[offset-4:offset], 'little')
                template = self.monster_entities.get(actor)
                if template is not None and template.boss and offset+7 <= len(body):
                    skill = int.from_bytes(body[offset+3:offset+7], 'little')
                    found.append({'ms':round((packet.timestamp_seconds-(self.first_timestamp or packet.timestamp_seconds))*1000),
                        'actor':actor,'template':template.template_id,'boss':template.name,'skill':skill,
                        'name':self.boss_skill_catalog.get(skill,''),'phase':phase,
                        'mode':body[offset+7] if offset+7<len(body) else None,
                        'bytes_from_marker':len(body)-offset,'offset':offset,
                        'record':body[offset-4:offset+25].hex(),
                        'catalogued':skill in self.boss_skill_catalog})
                offset = body.find(marker,offset+1)
            if found:
                candidates[phase] = found
        if len(candidates)==1:
            rows.extend(next(iter(candidates.values())))
        return events
    _V576PacketEventDecoder.feed_events = feed
    source = None
    try:
        source = V576DiagnosticReplaySource(Path(path),Path('dragon_nest_builder/data'))
        source.poll()
        counts = Counter((r['template'],r['boss'],r['skill'],r['name'],r['mode'],r['catalogued']) for r in rows)
        report = {'capture':str(path),'marker_packets':marker_packets,'boss_records':len(rows),
            'skills':[dict(template=k[0],boss=k[1],skill=k[2],name=k[3],mode=k[4],catalogued=k[5],records=v) for k,v in counts.items()],
            'rows':rows}
        out=Path('outputs/verification') / (Path(path).stem+'_boss_0e16_audit.json')
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False),flush=True)
    finally:
        _V576PacketEventDecoder.feed_events = original
        if source:
            source.close()

if __name__ == '__main__':
    for path in sys.argv[1:]:
        audit(path)
