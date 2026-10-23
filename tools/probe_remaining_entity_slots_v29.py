"""Read raw slots of remaining named entity Hits; diagnostics, never upgrades."""
from collections import Counter,defaultdict
from pathlib import Path
import json
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records

def main(report,output):
    doc=json.loads(report.read_text('utf-8'));wanted=defaultdict(list)
    for r in doc['raw_physical_rows']:
        if (r[10] is True and r[3]>0 and 0<(r[7]or 0)<65536
                and str(r[11]).isdigit() and int(r[11])>0
                and not is_deterministic_attribution(r[12])):
            wanted[r[0]].append(r)
    counts=Counter();samples=defaultdict(list);births={};matched=set();t0=None
    with Path(doc['capture_path']).open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        frame=0
        while head:=f.read(17):
            frame+=1;offset=f.tell()-17;t,_,original,size=struct.unpack('<dBII',head)
            packet=p.decode_captured_frame(t,f.read(size),original)
            if packet is None:continue
            if t0 is None:t0=t
            ms=max(0,round((t-t0)*1000))
            if packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                born=p.decode_v576_monster_spawn(wire,None)
                if born:births[born.actor_id]=dict(template=born.template_id,t=t,frame=frame,offset=offset)
                if ms not in wanted:continue
                inv=inspect_udp_damage_records(wire,packet.inferred_direction())
                if not inv.complete:continue
                body=p._decode_v576_body_uncached(wire,inv.xor_phase)
                for hit in inv.damage_records:
                    rows=[r for r in wanted[ms]if(r[3],r[4],r[5],r[6],r[7],r[8],r[9])==(
                        hit.damage,hit.critical,hit.damage_result_group_id,hit.damage_object_id,
                        hit.source_actor_id,hit.target_actor_id,hit.record_offset)]
                    for r in rows:
                        physical=(frame,mi,hit.record_offset)
                        if physical in matched:continue
                        matched.add(physical);m=hit.record_offset
                        slot=int.from_bytes(body[m+102:m+106],'little')if body[m:m+3]==b'\x08\x73\0'and m+118<=len(body)else -1
                        birth=births.get(hit.source_actor_id,{})
                        k=(birth.get('template',0),r[11],slot,r[12])
                        counts[k]+=1
                        if len(samples[k])<2:samples[k].append(dict(time=t,frame=frame,file_offset=offset,
                            message=mi,marker=m,phase=inv.xor_phase,direction=packet.inferred_direction(),
                            row=r,birth_observation_only=birth,wire=wire.hex()))
    result={'capture':doc['capture_path'],'scope':'remaining raw named entity-hit slot discovery, not a coverage report',
        'selected':sum(map(len,wanted.values())),'matched_physical':len(matched),
        'rows':[dict(template=k[0],old_skill_diagnostic=k[1],own_slot=k[2],old_method=k[3],
            count=v,examples=samples[k])for k,v in counts.most_common()]}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**{k:v for k,v in result.items()if k!='rows'},
        'rows':[{k:v for k,v in r.items()if k!='examples'}for r in result['rows']]},ensure_ascii=False))

if __name__=='__main__':main(*map(Path,sys.argv[1:]))
