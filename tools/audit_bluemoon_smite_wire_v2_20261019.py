"""Independent exhaustive wire census and per-hit evidence export; no score writes."""
import dataclasses as dc
import hashlib
import json
import pickle
from collections import Counter
from pathlib import Path
from tools.analyze_npcap_trace import read_trace
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body_uncached, decode_captured_frame, split_v576_payload,
    decode_v576_status_events,
)

ROOT = Path('outputs/verification/bluemoon_smite_deep_20261019')
OLD = Path('outputs/verification/crusader_completion_20261019')
ACTOR = 0x4D78152C


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    cache = pickle.loads((OLD/'1011硬核bluemoon十字.pkl').read_bytes())
    snapshot = cache['snapshot']
    cap = Path(cache['source'])
    hit_hashes = {e.wire_packet_sha256 for e in snapshot.combat_events
                  if e.source_id == f'0x{ACTOR:08X}' and e.skill_id == '3203'
                  and e.attribution_method == 'packet_skill_id'}
    candidates, statuses, actor_packets, hit_packets = [], [], [], []
    counts = Counter()
    needle = (3351).to_bytes(4, 'little')
    actor = ACTOR.to_bytes(4, 'little')
    origin = None
    for index, (stamp, adapter, original, frame) in enumerate(read_trace(cap)):
        counts['frames'] += 1
        if adapter >= 251:
            counts['metadata_frames'] += 1
            continue
        if origin is None:
            origin = stamp
        packet = decode_captured_frame(stamp, frame, original)
        if packet is None:
            counts['undecoded_frames'] += 1
            continue
        counts[packet.transport] += 1
        if packet.transport.upper() != 'UDP':
            continue
        t = stamp-origin
        chunks = list(split_v576_payload(packet.payload))
        # Inspect unsplit bytes too whenever framing returns different messages.
        wires = [(i, w) for i, w in enumerate(chunks)]
        if packet.payload not in chunks:
            wires.append((-1, packet.payload))
            counts['unsplit_extra_scans'] += 1
        for message, wire in wires:
            counts['udp_messages' if message >= 0 else 'udp_unsplit'] += 1
            digest = hashlib.sha256(wire).hexdigest()
            direction = packet.inferred_direction()
            meta = dict(t=t, frame=index, message=message, direction=direction,
                        wire_sha256=digest, length=len(wire))
            selected_phase = (11-wire[5]) % 13 if len(wire) > 5 else None
            if digest in hit_hashes:
                hit_packets.append(dict(**meta, wire_hex=wire.hex(), frame_hex=frame.hex()))
            for phase in range(13):
                b = _decode_v576_body_uncached(wire, phase)
                if phase == selected_phase and actor in b[9:]:
                    actor_packets.append(dict(**meta, phase=phase, body_hex=b.hex()))
                pos = b.find(needle, 9)
                while pos >= 0:
                    candidates.append(dict(**meta, phase=phase, selected=phase==selected_phase,
                        offset=pos, context=b[max(9,pos-24):pos+64].hex()))
                    pos = b.find(needle,pos+1)
                # Independent record scanner: deliberately no direction, duration,
                # known-skill, source or parameter-size<=256 filtering.
                ff = b.find(b'\xff'*4, 9)
                while ff >= 0:
                    p = ff+4
                    ff = b.find(b'\xff'*4,ff+1)
                    if p < 25 or p+20 > len(b):
                        continue
                    code = b[p-12:p-9]
                    if len(code)!=3 or code[0]!=0x13 or code[2]!=0:
                        continue
                    u = lambda q: int.from_bytes(b[q:q+4], 'little')
                    skill, cls, target, source = u(p),u(p+8),u(p-16),u(p-9)
                    if skill != 3351 and cls not in (164,165):
                        continue
                    size = u(p+16)
                    raw = b[p+20:min(len(b),p+20+size)]
                    decoded = decode_v576_status_events(wire,direction,known_skill_ids={3351})
                    any_direction = decode_v576_status_events(wire,None,known_skill_ids={3351})
                    statuses.append(dict(**meta,phase=phase,selected=phase==selected_phase,
                        offset=p,skill=skill,effect_class=cls,code=code.hex(),target=target,
                        source=source,duration_ms=u(p+12),size=size,
                        parameters=raw.decode('utf8',errors='replace'),
                        current_parser_accepts=any(e.record_offset==p and e.skill_id==skill for e in decoded),
                        parser_accepts_without_direction=any(e.record_offset==p and e.skill_id==skill for e in any_direction),
                        body_hex=b.hex(),wire_hex=wire.hex()))
        if index and index % 30000 == 0:
            print('frames',index,'time',round(t,1),'statuses',len(statuses),flush=True)
    result=dict(capture=str(cap),sha256=hashlib.sha256(cap.read_bytes()).hexdigest(),
                counts=dict(counts),candidates=candidates,statuses=statuses,
                matched_hit_hashes=len({r['wire_sha256'] for r in hit_packets}),
                expected_hit_hashes=len(hit_hashes))
    (ROOT/'wire_census.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (ROOT/'actor_packets.json').write_text(json.dumps(actor_packets,ensure_ascii=False),encoding='utf8')
    (ROOT/'smite_wire_packets.json').write_text(json.dumps(hit_packets,ensure_ascii=False),encoding='utf8')
    print(json.dumps(dict(counts=counts,candidates=len(candidates),statuses=len(statuses),
        signatures=Counter(str((r['skill'],r['effect_class'],r['duration_ms'],r['parameters'],r['current_parser_accepts'])) for r in statuses),
        actor_packets=len(actor_packets),hit_hashes=result['matched_hit_hashes']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
