"""Read-only full replay audit: preserve all evidence and immediate voice events."""
import sys
import json
import time
from pathlib import Path
from dataclasses import asdict
from collections import Counter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder.live_dps.capture_sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.boss_action_v2 import BossStructuredEventParser
from dragon_nest_builder.live_dps.voice_signals import VoiceSignalFeed
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body, _V576_BODY_XOR_KEY, _v576_wire_may_contain_marker

def main():
    evidence, signals = [], []
    wire = []
    slam_damage = []
    original_feed = _V576PacketEventDecoder.feed_events
    def feed(self, packet):
        marker = b'\x0e\x16\x00'
        if packet.transport.upper() == 'UDP' and packet.inferred_direction() == 'server_to_client' and _v576_wire_may_contain_marker(packet.payload,(marker,),start_offset=13):
            for phase in range(len(_V576_BODY_XOR_KEY)):
                body = _decode_v576_body(packet.payload,phase)
                offset = body.find(marker,13)
                while offset>=0:
                    if offset+25<=len(body):
                        skill=int.from_bytes(body[offset+3:offset+7],'little')
                        if 10000<=skill<=900000:
                            wire.append({'timestamp':round((packet.timestamp_seconds-(self.first_timestamp or packet.timestamp_seconds))*1000),
                                'actor':hex(int.from_bytes(body[offset-4:offset],'little')),'skill':skill,'mode':body[offset+7],
                                'phase':phase,'record':body[offset-4:offset+25].hex()})
                    offset=body.find(marker,offset+1)
        events = original_feed(self,packet)
        stamp = round((packet.timestamp_seconds-(self.first_timestamp or packet.timestamp_seconds))*1000)
        decoded = self.last_decoded_message
        if (decoded is not None and decoded.kind == 'damage_hit'
                and any(t <= stamp <= t+6500 for t in (174153,265955,358549,453211,557761))):
            slam_damage.append(dict(timestamp=stamp, **asdict(decoded)))
        return events
    _V576PacketEventDecoder.feed_events=feed
    original_observe = BossStructuredEventParser.observe
    original_publish = VoiceSignalFeed.publish
    def observe(self, rows, now):
        evidence.extend(asdict(row) for row in rows)
        return original_observe(self, rows, now)
    def publish(self, row):
        before = self._sequence
        original_publish(self, row)
        if self._sequence != before:
            signals.append(dict(row))
    BossStructuredEventParser.observe = observe
    VoiceSignalFeed.publish = publish
    source = None
    start = time.perf_counter()
    try:
        source = V576DiagnosticReplaySource(Path(sys.argv[1]), Path('dragon_nest_builder/data'))
        source._decoder.voice_signal_feed = VoiceSignalFeed()
        events = source.poll()
        hp = [asdict(e) for e in events if hasattr(e, 'current_hp')]
        targets={}
        for e in events:
            target=getattr(e,'target_id','')
            if '@' in target:
                item=targets.setdefault(target,{'name':getattr(e,'target_name',''),'max_hp':0,'damage':0,'first':e.timestamp_ms,'last':e.timestamp_ms})
                item['max_hp']=max(item['max_hp'],getattr(e,'target_max_hp',0))
                item['last']=max(item['last'],e.timestamp_ms)
                if type(e).__name__=='CombatEvent': item['damage']+=e.amount
        result = {'path':sys.argv[1], 'seconds':time.perf_counter()-start,
                  'events':dict(Counter(type(e).__name__ for e in events)),
                  'evidence':evidence, 'signals':signals, 'hp':hp,
                  'decoder_keys':list(vars(source._decoder)),
                  'maps':str(getattr(source._decoder,'detected_map_history',()))}
        result.update(wire=wire,targets=targets,slam_damage=slam_damage,entities={str(k):asdict(v) for k,v in source._decoder.monster_entities.items()})
        out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('outputs/verification/sea_voice_1018_audit.json')
        out.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in {'evidence','signals','hp','decoder_keys','wire','entities','targets','slam_damage'}},ensure_ascii=False))
        print('evidence',len(evidence),'signals',len(signals),'hp',len(hp),flush=True)
    finally:
        BossStructuredEventParser.observe = original_observe
        VoiceSignalFeed.publish = original_publish
        _V576PacketEventDecoder.feed_events=original_feed
        if source: source.close()

if __name__ == '__main__': main()
