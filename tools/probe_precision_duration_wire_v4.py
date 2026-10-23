"""Recover precision Buff evidence directly, without the gameplay/owner decoder."""
from dataclasses import asdict
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.protocol_v576 import (
    decode_captured_frame,decode_v576_status_events,V576TransportReassembler,_decode_v576_body,
)
from dragon_nest_builder.live_dps.npcap import (
    IDENTITY_METADATA_ADAPTER_INDEX,IDENTITY_TRACE_ADAPTER_INDEX,SESSION_EVIDENCE_ADAPTER_INDEX,
)
from tools.verify_combat_glyph_evidence_v4 import sha

FOLDER=Path(r'C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics')
OUT=ROOT/'outputs/combat_glyph_recheck_20260908/precision_raw_wire.json'
CASES=(('0905黑龙经典冰灵',0x4FBBCEF7,22400),('0830星期天',0x4E9A07E5,20000))


def main():
    if OUT.exists():raise FileExistsError(OUT)
    all_cases=[]
    for name,actor,duration in CASES:
        path=FOLDER/(name+'.dpscap')
        digest=sha(path)
        transport=V576TransportReassembler()
        rows=[]
        seen=set()
        origin=None
        frame_index=0
        with path.open('rb') as stream:
            assert stream.read(6)==b'DNDP1\0'
            while len(rows)<3:
                position=stream.tell()
                header=stream.read(17)
                if not header:break
                if len(header)!=17:raise ValueError('truncated frame header')
                ts,adapter,original,length=struct.unpack('<dBII',header)
                if length>16*1024*1024:raise ValueError('invalid frame length')
                frame=stream.read(length)
                if len(frame)!=length:raise ValueError('truncated frame')
                frame_index+=1
                if adapter in {IDENTITY_TRACE_ADAPTER_INDEX,IDENTITY_METADATA_ADAPTER_INDEX,SESSION_EVIDENCE_ADAPTER_INDEX}:continue
                if origin is None:origin=ts
                packet=decode_captured_frame(ts,frame,original)
                if packet is None:continue
                for msg in transport.feed(packet):
                    if msg.transport!='UDP' or msg.inferred_direction()!='server_to_client':continue
                    for status in decode_v576_status_events(msg.payload,'server_to_client',frozenset({1213})):
                        if status.skill_id!=1213 or status.source_actor_id!=actor or status.target_actor_id!=actor or status.effect_class_id!=25:continue
                        key=(msg.timestamp_seconds,msg.payload,status.record_offset)
                        if key in seen:continue
                        seen.add(key)
                        assert status.duration_ms==duration and status.skill_level_row_id==1073819462
                        decoded=_decode_v576_body(msg.payload,status.xor_phase)
                        off=status.record_offset
                        # Independent fixed-field reads of the selected wire record.
                        fields={'target':int.from_bytes(decoded[off-16:off-12],'little'),
                                'source':int.from_bytes(decoded[off-9:off-5],'little'),
                                'skill':int.from_bytes(decoded[off:off+4],'little'),
                                'level':int.from_bytes(decoded[off+4:off+8],'little'),
                                'class':int.from_bytes(decoded[off+8:off+12],'little'),
                                'duration':int.from_bytes(decoded[off+12:off+16],'little')}
                        assert fields=={'target':actor,'source':actor,'skill':1213,'level':1073819462,'class':25,'duration':duration}
                        rows.append({'frame_index':frame_index,'frame_file_offset':position,'timestamp':msg.timestamp_seconds,
                                     'relative_seconds':round(msg.timestamp_seconds-origin,6),'direction':'server_to_client',
                                     'payload':msg.payload.hex(),'decoded_record':decoded[off-16:off+24].hex(),
                                     'fields':fields,'status':asdict(status)})
        assert len(rows)>=3,(name,'insufficient wire controls')
        assert digest==sha(path)
        all_cases.append({'capture':str(path),'sha256':digest,'rows':rows[:3]})
    with OUT.open('x',encoding='utf-8') as out:
        json.dump({'scope':'Raw server UDP only, no equipment/profile parsing, no gameplay damage/owner inference.',
                   'captures':all_cases},out,ensure_ascii=False,indent=2)
    print(json.dumps([{'capture':c['capture'],'samples':[{'seconds':r['relative_seconds'],'fields':r['fields']} for r in c['rows']]} for c in all_cases],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
