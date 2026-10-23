"""Read-only boundary-validated UDP inventory for known party actors."""
import json,struct
from pathlib import Path
from collections import Counter
from dragon_nest_builder.live_dps.sources import decode_captured_frame,V576TransportReassembler
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

root=Path('outputs/verification')
players={int(p['id'],16):p['job'] for p in json.loads((root/'sea_survival_1018.json').read_text(encoding='utf-8'))['players']}
path=Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics/20261018冰灵海龙第一把.dpscap')
rows=[]; types=Counter(); stats=Counter(); start=None
transport=V576TransportReassembler()
with path.open('rb') as f:
 assert f.read(6)==b'DNDP1\0'
 while header:=f.read(17):
  ts,adapter,orig,size=struct.unpack('<dBII',header);frame=f.read(size)
  if adapter>=250:continue
  packet=decode_captured_frame(ts,frame,orig)
  if packet is None:continue
  if start is None:start=ts
  for p in transport.feed(packet):
   if p.transport.upper()!='UDP' or p.inferred_direction()!='server_to_client':continue
   stats['udp']+=1
   inv=inspect_udp_damage_records(p.payload,p.inferred_direction())
   if inv.xor_phase is None:continue
   stats['validated']+=1
   for r in inv.typed_records:
    b=r.record_bytes;actor=int.from_bytes(b[:4],'little')
    if actor not in players:continue
    key=f'{r.record_type:02x}{r.declared_length:04x}'
    types[key]+=1
    if r.record_type in (8,0x1c,0x21) or (10000).to_bytes(4,'little') in b or (-10000).to_bytes(4,'little',signed=True) in b:
     rows.append({'t':round(ts-start,6),'actor':hex(actor),'job':players[actor],'key':key,'hex':b.hex(),'offset':r.record_start,'phase':inv.xor_phase})
result={'stats':dict(stats),'types':dict(types),'rows':rows}
(root/'player_hp_udp_inventory.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'stats':dict(stats),'types':dict(types),'rows':len(rows)},ensure_ascii=False),flush=True)
