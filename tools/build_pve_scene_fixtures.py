"""Exact raw anchors for same-flow boss/nonboss scene proof tests."""
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps.sources import _V576PacketEventDecoder
from dragon_nest_builder.live_dps.nest_recognizer import resolve_nest_template
from dragon_nest_builder.live_dps.protocol_v576 import decode_captured_frame,split_v576_payload

def main():
    index=json.loads((ROOT/'outputs/v25_entity_owner_independent/0826硬核曜_0_180.json').read_text('utf-8'))
    host=_V576PacketEventDecoder(ROOT/'dragon_nest_builder/data')
    known=[r for r in index['spawns']if r['entity']>0 and r['template']in host.monster_templates]
    boss=next(r for r in known if host.monster_templates[r['template']].boss and resolve_nest_template(r['template']))
    target=next(r for r in known if r['template']>100000 and not host.monster_templates[r['template']].boss and r['frame']>boss['frame'])
    def reread(a):
        with Path(index['capture']).open('rb')as f:
            f.seek(a['file_offset']);t,_,original,size=struct.unpack('<dBII',f.read(17));packet=decode_captured_frame(t,f.read(size),original)
        wire=list(split_v576_payload(packet.payload))[a['message']]
        assert wire.hex()==a['wire']
        return dict(actor=a['entity'],template=a['template'],wire=wire.hex(),time=t,frame=a['frame'],offset=a['file_offset'],
            source_ip=packet.source_ip,source_port=packet.source_port,destination_ip=packet.destination_ip,destination_port=packet.destination_port)
    data=dict(capture=index['capture'],boss=reread(boss),target=reread(target))
    out=ROOT/'tests/data/pve_scene_raw.json';out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(out)

if __name__=='__main__':main()
