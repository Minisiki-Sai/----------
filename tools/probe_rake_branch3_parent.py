"""Raw branch3 child/parent inventory; candidates are not confirmations."""
from collections import Counter, OrderedDict
from dataclasses import asdict
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from tools.probe_compact_entity_attack_selectors import walk


def main(capture,output,skill=6203,child_templates=(5711,),parent_templates=(5707,5715)):
    slots=OrderedDict(); actions=[]; children={}; counts=Counter(); examples=[]; sequences=Counter();known_actors=set()
    with capture.open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        frame=0
        while h:=f.read(17):
            offset=f.tell()-17;t,_,original,n=struct.unpack('<dBII',h);frame+=1
            packet=p.decode_captured_frame(t,f.read(n),original)
            if not packet or packet.transport!='UDP':continue
            for message,wire in enumerate(p.split_v576_payload(packet.payload)):
                anchor=dict(t=t,frame=frame,message=message,file_offset=offset,wire=wire.hex(),
                            direction=packet.inferred_direction())
                for a in p.decode_v576_structured_actions(wire,packet.inferred_direction(),
                        frozenset({skill}),known_actors,allow_unknown_actor_ids=True):
                    if a.kind=='skill_action':
                        actions=[v for v in actions if v['t']>=t-12]
                        actions.append(dict(anchor,action=asdict(a)))
                regs=p.decode_v576_server_damage_object_registrations(wire,None,(),include_unmapped=True)
                for r in regs:
                    known_actors.add(r.source_actor_id)
                    b=p._decode_v576_body_uncached(wire,r.xor_phase);m=r.record_offset
                    row=dict(anchor,registration=asdict(r))
                    if r.projectile_template_id in child_templates and b[m] == 9 and b[m+1] in(98,102,111,115):
                        u32=lambda at:int.from_bytes(b[at:at+4],'little')
                        parent=u32(m+49);branch=u32(m+53);sequences[(branch&65535,branch>>16)]+=1
                        previous=slots.get((r.source_actor_id,parent))
                        parent_tpl=previous['registration']['projectile_template_id']if previous else 0
                        age=t-previous['t']if previous else None
                        walked=walk(wire)
                        row.update(parent=previous,parent_id=parent,branch=branch,
                            age=age,parent_template=parent_tpl,closed=bool(walked),
                            root_actions=[v for v in actions if v['action']['source_actor_id']==r.source_actor_id
                                and previous and 0<=previous['t']-v['t']<=3],
                            tail=b[m+48:m+3+int.from_bytes(b[m+1:m+3],'little')].hex())
                        counts[(parent_tpl,'fresh'if age is not None and 0<=age<=8 else 'missing/stale',bool(walked))]+=1
                        children[r.source_actor_id,r.damage_object_id]=row
                    slots[r.source_actor_id,r.damage_object_id]=row
                    slots.move_to_end((r.source_actor_id,r.damage_object_id))
                    while len(slots)>8192:slots.popitem(last=False)
                if not p._v576_wire_may_contain_marker(wire,(b'\x08\x73\0',)):continue
                decoded=walk(wire)
                if decoded is None:continue
                phase,b,records=decoded;u32=lambda at:int.from_bytes(b[at:at+4],'little')
                for target,m,op,size in records:
                    if op!=8 or size!=115:continue
                    child=children.get((u32(m+8),u32(m+13)))
                    if not child or not 0<=t-child['t']<=8:continue
                    if len(examples)<120 and child['parent_template']in parent_templates:
                        examples.append(dict(child=child,hit=dict(anchor,phase=phase,marker=m,target=target,
                            amount=int.from_bytes(b[m+19:m+27],'little'),object=u32(m+13),source=u32(m+8),
                            self_slot=u32(m+102),context_slot=u32(m+114),result_group=u32(m+4))))
    result=dict(capture=str(capture),scope='diagnostic candidates; no confirmations',
        counts=[dict(template=k[0],age=k[1],closed=k[2],n=v)for k,v in counts.most_common()],
        sequences=[dict(branch=k[0],sequence=k[1],n=v)for k,v in sequences.most_common()],examples=examples)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items()if k!='examples'},ensure_ascii=False))
    print('candidate anchored hits',len(examples),'actions',[len(x['child']['root_actions'])for x in examples[:10]])


if __name__=='__main__':
    if len(sys.argv)>3 and sys.argv[3]=='rain':
        main(Path(sys.argv[1]),Path(sys.argv[2]),6204,(5709,5737),(5708,50005536))
    else:main(Path(sys.argv[1]),Path(sys.argv[2]))
