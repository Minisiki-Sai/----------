"""Read-only compact entity object/ACT selector discovery. Never attribution.

Whole declared records and independent entity births select candidates. Their
ACT correspondence is diagnostic until current owner/lifecycle and exact hit
join are independently checked. Old displayed skill names are not imported.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict, OrderedDict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.entity_owner_evidence import decode_entity_owner_records
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full

R=Path('E:/ext0811.rar/Resource00/Resource00/resource')


def walk(wire):
    if len(wire)<21 or int.from_bytes(wire[7:9],'little')+3!=len(wire):return None
    candidates=[]
    for phase in range(13):
        body=p._decode_v576_body_uncached(wire,phase)
        observer=int.from_bytes(body[10:14],'little')
        if not 0x1000000<=observer<0xffffffff:continue
        cursor=14;records=[]
        while cursor+7<=len(body) and len(records)<128:
            m=cursor+4;size=int.from_bytes(body[m+1:m+3],'little');end=m+3+size
            if end>len(body):break
            actor=int.from_bytes(body[cursor:m],'little')
            if not 0<actor<0xffffffff:break
            records.append((actor,m,body[m],size));cursor=end
        if cursor==len(body):candidates.append((phase,body,records))
    return candidates[0] if len(candidates)==1 else None


class ResourceIndex:
    def __init__(self):
        self.monsters={};self.actors={};self.files={};self.paths=defaultdict(list);self.cache={}
        for path in (R/'ext').glob('monstertable*.dnt'):
            for row in parse_dnt(path):
                if '_ActorTableID'in row:self.monsters[row['RowId']]=row
        self.actors={r['RowId']:r for r in parse_dnt(R/'ext/actortable.dnt')}
        self.files={r['RowId']:r for r in parse_dnt(R/'ext/filetable.dnt')}
        for path in R.rglob('*.act'):self.paths[path.name.casefold()].append(path)

    def selector(self,template,action,signal):
        if template not in self.cache:
            monster=self.monsters.get(template,{});actor=self.actors.get(monster.get('_ActorTableID'),{})
            name=self.files.get(actor.get('_ActName'),{}).get('_FileName','')
            normalized=name.replace('\\','/').casefold()
            paths=self.paths.get(normalized.rsplit('/',1)[-1],[])
            if '/' in normalized:
                paths=[p for p in paths if p.as_posix().casefold().endswith('/'+normalized)]
            try:
                full=parse_full(paths[0]) if len(paths)==1 else None
                self.cache[template]=(full,dict(actor=monster.get('_ActorTableID'),act=name,
                    path=str(paths[0]) if len(paths)==1 else None,
                    sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths)==1 else None))
            except Exception as exc:self.cache[template]=(None,dict(error=str(exc),act=name))
        full,meta=self.cache[template]
        if full is None or not 0<=action<len(full['actions']):return dict(meta,match=False,reason='action_out_of_range')
        a=full['actions'][action]
        if not 0<=signal<len(a['signals']):return dict(meta,match=False,action_name=a['name'],reason='signal_out_of_range')
        s=a['signals'][signal]
        return dict(meta,match=s['type']in (4,15),action_name=a['name'],action_offset=a['offset'],
            signal_type=s['type'],signal_offset=s['offset'],signal_u32=s['u32'][:8],
            signal_strings=s['strings'],projectile=s['u32'][0]if s['type']==15 else None)


def probe(capture,output,resources):
    births={};owners={};generation=Counter();counts=Counter();examples=defaultdict(list);observations=OrderedDict();joins=Counter();joined=defaultdict(list)
    self_slots=defaultdict(Counter);delay_ranges={};other_lengths=Counter()
    with capture.open('rb')as f:
        assert f.read(6)==b'DNDP1\0'
        frame=0
        while head:=f.read(17):
            offset=f.tell()-17;t,adapter,original,size=struct.unpack('<dBII',head);raw=f.read(size);frame+=1
            packet=p.decode_captured_frame(t,raw,original)
            if not packet or packet.transport!='UDP':continue
            for message,wire in enumerate(p.split_v576_payload(packet.payload)):
                birth=p.decode_v576_monster_spawn(wire,None)
                if birth:
                    previous=births.get(birth.actor_id)
                    if previous is None or previous['template']!=birth.template_id or t-previous['t']>.3:
                        generation[birth.actor_id]+=1
                    births[birth.actor_id]=dict(template=birth.template_id,generation=generation[birth.actor_id],t=t,
                        file_offset=offset,frame=frame,message=message,wire=wire.hex())
                for owner in decode_entity_owner_records(wire):
                    owners[owner.entity_id]=dict(t=t,frame=frame,file_offset=offset,message=message,
                        wire=wire.hex(),owner=owner.owner_id,marker=owner.record_offset)
                markers=(b'\x09\x32\0',b'\x09\x29\0',b'\x08\x73\0')
                if not p._v576_wire_may_contain_marker(wire,markers):continue
                walked=walk(wire)
                if walked is None:continue
                phase,body,records=walked
                u32=lambda m:int.from_bytes(body[m:m+4],'little')
                for actor,m,op,length in records:
                    if op==9 and actor<65536 and length not in (41,50):
                        other_lengths[(births.get(actor,{}).get('template',0),length)]+=1
                    if op==9 and length in (41,50) and actor<65536:
                        obj,action,signal=u32(m+3),u32(m+34),u32(m+38)
                        birth=births.get(actor)
                        template=birth['template']if birth else 0
                        static=resources.selector(template,action,signal)if birth else {'match':False,'reason':'no_birth'}
                        key=(template,length,action,signal,static.get('match'),static.get('projectile'))
                        counts[key]+=1
                        evidence=dict(t=t,frame=frame,message=message,file_offset=offset,phase=phase,marker=m,
                            entity=actor,object=obj,birth=birth,owner=owners.get(actor),action=action,signal=signal,static=static,wire=wire.hex())
                        if len(examples[key])<2:examples[key].append(evidence)
                        observations[(actor,obj)]=(key,evidence)
                        observations.move_to_end((actor,obj))
                        while len(observations)>8192:observations.popitem(last=False)
                    if op==8 and length==115:
                        entity,obj=u32(m+8),u32(m+13)
                        found=observations.get((entity,obj));birth=births.get(entity)
                        if not found or not birth:continue
                        key,reg=found
                        if not reg['birth']or reg['birth']['generation']!=birth['generation']or not 0<=t-reg['t']<=8:continue
                        joins[key]+=1
                        self_slots[key][u32(m+102)]+=1
                        delay=t-reg['t'];lo,hi=delay_ranges.get(key,(delay,delay))
                        delay_ranges[key]=(min(lo,delay),max(hi,delay))
                        if len(joined[key])<2:
                            joined[key].append(dict(registration=reg,hit=dict(t=t,frame=frame,file_offset=offset,message=message,
                                phase=phase,marker=m,wire=wire.hex(),entity=entity,object=obj,target=actor,
                                amount=int.from_bytes(body[m+19:m+27],'little'),self_skill=u32(m+102))))
    rows=[dict(template=k[0],length=k[1],action=k[2],signal=k[3],act_match=k[4],projectile=k[5],
        registrations=v,joined_hit_candidates=joins[k],self_slots=dict(self_slots[k]),delays=delay_ranges.get(k),
        examples=examples[k],joins=joined[k])for k,v in counts.most_common()]
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(capture=str(capture),schema='compact-selector-discovery-only',rows=rows,
        other_entity_09_lengths=[dict(template=k[0],length=k[1],count=v)for k,v in other_lengths.most_common()]),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(capture=capture.name,registrations=sum(counts.values()),candidates=sum(joins.values()),
        groups=len(rows)),ensure_ascii=False),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('captures',nargs='+',type=Path);ap.add_argument('--output-dir',required=True,type=Path)
    args=ap.parse_args();resources=ResourceIndex()
    for capture in args.captures:probe(capture,args.output_dir/(capture.stem+'.json'),resources)
