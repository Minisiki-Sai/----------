"""Inspect terminal scene evidence without building combat/score snapshots."""
import json
import sys
from collections import Counter
from pathlib import Path
from tools.analyze_scene_transition_packets import _read_logical_packets, _tcp_plaintext
from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_v576_map_identity, decode_v576_scene_transition_ack,
)

for filename in sys.argv[1:]:
    path = Path(filename)
    packets = _read_logical_packets(path)
    origin = packets[0].timestamp_seconds
    end = packets[-1].timestamp_seconds
    rows = []
    counts = Counter()
    for packet in packets:
        t = packet.timestamp_seconds - origin
        m = decode_v576_map_identity(packet.payload, packet.inferred_direction(), connection_context=packet)
        ack = decode_v576_scene_transition_ack(packet.payload, packet.inferred_direction()) if packet.transport == 'UDP' else None
        body = _tcp_plaintext(packet)
        if packet.timestamp_seconds > end - 60:
            counts[(packet.transport, packet.inferred_direction(), len(packet.payload), body[2:4].hex() if body else packet.payload[5:9].hex())] += 1
        if m or ack or (packet.transport == 'TCP' and packet.timestamp_seconds > end - 30):
            rows.append(dict(t=round(t,3),transport=packet.transport,direction=packet.inferred_direction(),
                size=len(packet.payload),prefix=body[:64].hex() if body else '',
                map=dict(id=m.map_id,active=m.active,authoritative=m.authoritative) if m else None,
                ack=str(ack) if ack else None, wire=packet.payload.hex() if len(packet.payload)<60 else ''))
    out = Path('outputs/verification/sea_returns') / (path.stem+'.tail.json')
    out.write_text(json.dumps(dict(file=path.name,duration=end-origin,counts=[(str(k),v) for k,v in counts.most_common()],rows=rows),ensure_ascii=False,indent=2),encoding='utf-8')
    print(path.name, 'duration', end-origin, 'maps/acks', [r for r in rows if r['map'] or r['ack']], 'tail counts', counts.most_common(20),flush=True)
