"""Read-only raw-wire versus snapshot audit. Does not score or upload."""
import dataclasses
import hashlib
import json
import pickle
from collections import Counter
from pathlib import Path

from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body, decode_captured_frame, split_v576_payload,
    decode_v576_payload, decode_v576_status_events, decode_v576_player_health,
)
from dragon_nest_builder.live_dps.scoring import _merge_intervals, _intersect_intervals, _interval_duration

ROOT = Path('outputs/verification/crusader_completion_20261019')
CAP = Path(r'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics\1011硬核bluemoon十字.dpscap')
ACTOR = 0x4D78152C
SKILL = 3351


def audit():
    p = pickle.loads((ROOT/'1011硬核bluemoon十字.pkl').read_bytes())
    s = p['snapshot']
    score = next(r for r in p['rows'] if r.source_id == f'0x{ACTOR:08X}')
    raw_status, raw_cast, raw_candidates, health, structural_casts = [], [], [], [], []
    origin = None
    counts = Counter()
    needle = SKILL.to_bytes(4, 'little')
    for frame_index, (stamp, adapter, original, frame) in enumerate(read_trace(CAP)):
        if adapter >= 251:
            continue
        if origin is None:
            origin = stamp
        packet = decode_captured_frame(stamp, frame, original)
        if packet is None or packet.transport.upper() != 'UDP':
            continue
        t = stamp-origin
        for message, wire in enumerate(split_v576_payload(packet.payload)):
            counts['udp_messages'] += 1
            matched = []
            # Exhaustive phases independently check the optimized phase selector.
            for phase in range(13):
                body = _decode_v576_body(wire, phase)
                pos = body.find(needle, 10)
                while pos >= 0:
                    matched.append((phase, pos))
                    raw_candidates.append(dict(t=t, frame=frame_index, message=message,
                        phase=phase, offset=pos, direction=packet.inferred_direction(),
                        context=body[max(10,pos-16):pos+40].hex()))
                    is_cast = ((pos == 14 and int.from_bytes(body[10:14], 'little') == ACTOR)
                        or (pos == 21 and body[18:21] == bytes.fromhex('0e1f00')
                            and int.from_bytes(body[14:18], 'little') == ACTOR))
                    if is_cast and packet.inferred_direction() == 'client_to_server':
                        structural_casts.append(dict(t=t,frame=frame_index,phase=phase,offset=pos))
                    if pos >= 26 and body[pos-4:pos] == b'\xff'*4 and body[pos-12:pos-9] == bytes.fromhex('134700'):
                        target = int.from_bytes(body[pos-16:pos-12], 'little')
                        if target == ACTOR:
                            n = int.from_bytes(body[pos+16:pos+20], 'little')
                            raw_status.append(dict(t=t, frame=frame_index, message=message,
                                wire_sha256=hashlib.sha256(wire).hexdigest(),
                                phase=phase, offset=pos, target=target,
                                source=int.from_bytes(body[pos-9:pos-5], 'little'),
                                effect_class=int.from_bytes(body[pos+8:pos+12], 'little'),
                                duration_ms=int.from_bytes(body[pos+12:pos+16], 'little'),
                                parameters=body[pos+20:pos+20+n].decode('utf8',errors='replace')))
                    pos = body.find(needle,pos+1)
            if matched:
                event = decode_v576_payload(wire, packet.inferred_direction(), known_skill_ids={SKILL})
                if event and event.skill_id == SKILL:
                    raw_cast.append(dict(t=t, frame=frame_index, message=message, event=dataclasses.asdict(event)))
            if 708.1 < t < 713.3:
                for event in decode_v576_player_health(wire, packet.inferred_direction(), known_player_actor_ids={ACTOR}):
                    if event.actor_id == ACTOR:
                        health.append(dict(t=t, frame=frame_index, event=dataclasses.asdict(event)))
    actor = f'0x{ACTOR:08X}'
    casts = [e.timestamp_ms/1000 for e in s.skill_cast_events if e.source_id == actor and e.skill_id == str(SKILL)]
    applies = [dataclasses.asdict(e) for e in s.player_buff_events if e.target_id==actor and e.skill_id==str(SKILL)]
    intervals = _merge_intervals([iv for b in s.player_buff_series if b.target_id==actor and b.skill_id==str(SKILL) for iv in b.intervals])
    received = _merge_intervals([(e['timestamp_seconds'],e['timestamp_seconds']+e['duration_seconds']) for e in applies])
    duration = _interval_duration(score.output_windows)
    coverage = lambda iv: _interval_duration(_intersect_intervals(iv,score.output_windows))/duration
    gaps = []
    for left,right in zip(intervals,intervals[1:]):
        start,end = left[1],right[0]
        gaps.append(dict(start=start,end=end,seconds=end-start,
            effective_seconds=_interval_duration(_intersect_intervals([(start,end)],score.output_windows))))
    report = dict(capture=str(CAP),counts=dict(counts),casts=casts,applies=applies,
        raw_status=raw_status,raw_cast=raw_cast,raw_candidates=raw_candidates,
        structural_casts=structural_casts,
        health_at_early_end=health,intervals=intervals,gaps=gaps,
        effective_seconds=duration,observed_coverage=coverage(intervals),
        coverage_ignoring_death=coverage(received))
    assert len(structural_casts)==len(casts)==18
    assert all(any(abs(c['t']-t)<.002 for t in casts) for c in structural_casts)
    # A retransmitted first application carries the exact same datagram.
    unique_status={(x['wire_sha256'],x['offset'],x['phase']):x for x in raw_status}
    assert len(unique_status)==36
    assert {x['duration_ms'] for x in raw_status}=={40000}
    assert {x['parameters'] for x in raw_status}=={'0.44'}
    report['unique_raw_status_count']=len(unique_status)
    report['cast_buff_pairs']=[dict(cast=t,buff=min(applies,key=lambda e:abs(e['timestamp_seconds']-t))['timestamp_seconds']) for t in casts]
    (ROOT/'judgement_wire_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ['counts','casts','intervals','gaps','effective_seconds','observed_coverage','coverage_ignoring_death']},ensure_ascii=False))
    print('raw_status',len(raw_status),'raw_cast',len(raw_cast),'raw_candidates',len(raw_candidates),'health',len(health))


if __name__ == '__main__':
    audit()
