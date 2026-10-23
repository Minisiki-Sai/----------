"""Read-only raw record inventory for post-boss resource reset investigation."""
import json,struct,sys
from pathlib import Path
from dragon_nest_builder.live_dps.sources import decode_captured_frame,V576TransportReassembler
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

base=Path('C:/Users/gcg59/Documents/DragonNestDpsCapture/live_dps_diagnostics')
for name in sys.argv[1:]:
    rows=[];start=None; focus_until=0; transport=V576TransportReassembler()
    with (base/name).open('rb') as f:
        assert f.read(6)==b'DNDP1\0'
        while header:=f.read(17):
            ts,adapter,orig,size=struct.unpack('<dBII',header);frame=f.read(size)
            if adapter>=250:continue
            packet=decode_captured_frame(ts,frame,orig)
            if packet is None:continue
            if start is None:start=ts
            for p in transport.feed(packet):
                if p.transport.upper()!='UDP' or p.inferred_direction()!='server_to_client':continue
                inv=inspect_udp_damage_records(p.payload,p.inferred_direction())
                if inv.xor_phase is None:continue
                for r in inv.typed_records:
                    b=r.record_bytes; actor=int.from_bytes(b[:4],'little')
                    focus = name == '1018黑典冰灵.dpscap' and any(abs(ts-start-t)<.12 for t in (544.411,914.661,1209.116))
                    reset_status = r.record_type == 0x13 and (int.from_bytes(b[16:20],'little') == 34709 or int.from_bytes(b[24:28],'little') in (16,18))
                    if reset_status and int.from_bytes(b[16:20],'little') in (34709,32100,33500,34247,325408,400534):
                        focus_until=ts+0.3
                    focus = focus or ts < focus_until
                    if focus or reset_status or r.record_type in (0x21,0x1c,0x05) or (r.record_type==8 and (actor <= 0x00ffffff or r.declared_length in (107,111))):
                        rows.append({'t':round(ts-start,6),'actor':hex(actor),'type':r.record_type,'length':r.declared_length,'hex':b.hex()})
    dest=Path('outputs/verification')/(Path(name).stem+'_reset_raw.json')
    dest.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
    print(name,len(rows),flush=True)
