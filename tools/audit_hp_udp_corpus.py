"""Diagnostic only: boundary-validated signed resource records and HP anchors."""
import json, struct, sys
from collections import Counter, defaultdict
from pathlib import Path
from dragon_nest_builder.live_dps.sources import decode_captured_frame, V576TransportReassembler
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

def audit(path):
    transport = V576TransportReassembler()
    rows = defaultdict(list)
    types = Counter()
    start = None
    with path.open('rb') as f:
        assert f.read(6) == b'DNDP1\0'
        while header := f.read(17):
            ts, adapter, orig, size = struct.unpack('<dBII', header)
            frame = f.read(size)
            if adapter >= 250: continue
            packet = decode_captured_frame(ts, frame, orig)
            if packet is None: continue
            if start is None: start = ts
            for p in transport.feed(packet):
                if p.transport.upper() != 'UDP' or p.inferred_direction() != 'server_to_client': continue
                inv = inspect_udp_damage_records(p.payload, p.inferred_direction())
                if inv.xor_phase is None: continue
                for r in inv.typed_records:
                    b = r.record_bytes
                    actor = int.from_bytes(b[:4], 'little')
                    types[f'{r.record_type:02x}/{r.declared_length}'] += 1
                    if r.record_type == 0x1c and r.declared_length == 27:
                        rows[actor].append([ts-start, 'delta', b[7], int.from_bytes(b[8:16], 'little', signed=True), b[20:].hex(), hex(int.from_bytes(b[16:20], 'little'))])
                    elif r.record_type == 8 and r.declared_length == 111:
                        hp = int.from_bytes(b[31:39], 'little', signed=True)
                        if -50000000 <= hp <= 50000000:
                            rows[actor].append([ts-start, 'hp', hp])
    # HP-state-bearing actors only; identity still requires a party roster.
    counts = Counter(); matches = Counter(); examples = []; ticks = []
    actors = 0
    for actor, events in rows.items():
        if actor <= 0x00FFFFFF: continue
        if not any(e[1] == 'hp' for e in events): continue
        actors += 1; previous = None; pending = []
        for e in events:
            if e[1] == 'delta':
                key = f'{e[2]:02x}/{e[4]}/' + ('plus' if e[3]>0 else 'minus' if e[3]<0 else 'zero')
                counts[key] += 1
                if e[4].startswith('0001'):
                    pending.append(e)
                    if e[3] == -10000: ticks.append([round(e[0],6),hex(actor),e[2],e[4],e[5]])
            else:
                if previous and 0.05 < e[0]-previous[0] < 5 and pending:
                    net = sum(d[3] for d in pending)
                    if net != 0 and net == e[2]-previous[2]:
                        for d in pending: matches[f'{d[2]:02x}/'+('plus' if d[3]>0 else 'minus')] += 1
                        if len(examples)<20: examples.append({'actor':hex(actor),'before':previous,'after':e,'deltas':pending[:]})
                previous = e; pending = []
    return dict(file=path.name, hp_state_actors=actors, counts=dict(counts), matched_delta_records=dict(matches), examples=examples, ticks=ticks, types=dict(types))

if __name__ == '__main__':
    base = Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
    dest = Path('outputs/verification/hp_udp_corpus'); dest.mkdir(parents=True, exist_ok=True)
    for name in sys.argv[1:]:
        result = audit(base/name)
        (dest/(Path(name).stem+'.json')).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k not in ('examples','ticks','types','counts')}|{'raw_10000':len(result['ticks'])},ensure_ascii=False),flush=True)
