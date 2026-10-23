"""Read-only replay evidence audit for guardian poison announcements."""
import sys
import json
from pathlib import Path
from collections import Counter
from dataclasses import asdict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.boss_action_v2 import BossStructuredEventParser
from dragon_nest_builder.live_dps.voice_signals import VoiceSignalFeed
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body, _V576_BODY_XOR_KEY

counts = Counter()
rows = []
wire_matches = []
old_feed = _V576PacketEventDecoder.feed_events
def feed(self, packet):
    for phase in range(len(_V576_BODY_XOR_KEY)):
        body = _decode_v576_body(packet.payload, phase)
        offset = body.find((34023).to_bytes(4, 'little'), 9)
        if offset >= 0:
            wire_matches.append({'time': packet.timestamp_seconds, 'phase': phase,
                'offset': offset, 'length': len(body), 'nearby': body[max(9,offset-16):offset+24].hex(),
                'payload': packet.payload.hex(), 'direction': packet.inferred_direction(), 'transport': packet.transport,
                'relative_ms': round((packet.timestamp_seconds-(self.first_timestamp or packet.timestamp_seconds))*1000)})
    return old_feed(self, packet)
_V576PacketEventDecoder.feed_events = feed
original = BossStructuredEventParser.observe
def observe(self, evidence, now_ms):
    for item in evidence:
        counts[(item.template_id, item.source_name, item.evidence_kind, item.action_code, item.skill_index)] += 1
        if item.skill_index in (34023, 34024) or item.evidence_kind in ('protocol_action', 'structured_action'):
            rows.append(asdict(item))
    return original(self, evidence, now_ms)
BossStructuredEventParser.observe = observe
source = None
try:
    source = V576DiagnosticReplaySource(Path(sys.argv[1]), Path('dragon_nest_builder/data'))
    source._decoder.voice_signal_feed = VoiceSignalFeed()
    events = source.poll()
    print(json.dumps({'events': len(events), 'counts': [(k,v) for k,v in counts.items()],
                      'evidence': rows, 'voice': source._decoder.voice_signal_feed.snapshot(),
                      'wire_matches': wire_matches,
                      'maps': str(getattr(source._decoder, 'detected_map_history', ()))}, ensure_ascii=False))
finally:
    BossStructuredEventParser.observe = original
    _V576PacketEventDecoder.feed_events = old_feed
    if source:
        source.close()
