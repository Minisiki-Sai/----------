"""Single-process bounded raw summon action/object discovery, no replay mutation."""
from __future__ import annotations
import argparse
import collections
import dataclasses
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_entity_owner_342c_validation import (
    read_frames, frozen_protocol, xor_body, marker_pattern,
)

TARGETS = frozenset({32005,32008,*range(33607,33615)})
MARKERS = (bytes.fromhex('2404'), bytes.fromhex('031400'),bytes.fromhex('051a00'),
           bytes.fromhex('0e2300'), bytes.fromhex('0e1f00'),
           bytes.fromhex('122400'), bytes.fromhex('122000'))
REG_MARKERS = tuple(bytes((9, length, 0)) for length in
                    (0x5d,0x61,0x6e,0x62,0x66,0x6a,0x6f,0x73))


def scan(capture, start, seconds):
    proto,digest = frozen_protocol()
    pattern = marker_pattern((*MARKERS,*REG_MARKERS))
    births, all_births, generations = {}, [], collections.Counter()
    counts = collections.Counter()
    registrations, actions, literal, hits, player_regs = [], [], [], [], []
    for frame,offset,t,relative,original,raw in read_frames(capture,start,seconds):
        counts['physical_frames'] += 1
        packet = proto.decode_captured_frame(t,raw,original)
        if not packet or packet.transport != 'UDP':
            continue
        for mi,wire in enumerate(proto.split_v576_payload(packet.payload)):
            counts['udp_messages'] += 1
            meta = dict(frame=frame,message=mi,file_offset=offset,t=t,relative=relative,
                        direction=packet.inferred_direction())
            def anchor(body,phase,at):
                return dict(meta,phase=phase,record_offset=at,wire=wire.hex(),
                    plaintext=body.hex(),raw_frame_sha256=hashlib.sha256(raw).hexdigest())
            if len(wire)==88 and wire[5:9]==bytes.fromhex('06275500'):
                b=xor_body(wire,5)
                entity=int.from_bytes(b[10:14],'little')
                template=int.from_bytes(b[18:22],'little')
                if entity not in births or births[entity]['wire']!=wire.hex():
                    generations[entity]+=1
                    birth=dict(anchor(b,5,10),entity=entity,template=template,
                               generation=generations[entity])
                    births[entity]=birth
                    all_births.append(birth)
            typed=list(proto.decode_v576_server_damage_object_registrations(wire,None,include_unmapped=True))
            client=proto.decode_v576_damage_object_registration(wire,None)
            if client:
                typed.append(client)
            for r in typed:
                if r.source_actor_id>=65536:
                    player_regs.append(dict(anchor(xor_body(wire,r.xor_phase),r.xor_phase,r.record_offset),
                        entity=r.source_actor_id,object_id=r.damage_object_id,projectile=r.projectile_template_id,
                        parent_object=r.parent_damage_object_id,layout=r.layout,registration_key=r.registration_key))
            accepted={(r.xor_phase,r.record_offset,r.source_actor_id,r.damage_object_id):r for r in typed}
            if pattern.search(wire,9):
                for phase in range(13):
                    b=xor_body(wire,phase)
                    def u32(i): return int.from_bytes(b[i:i+4],'little')
                    for marker in MARKERS:
                        m=b.find(marker,14)
                        while m>=0:
                            entity=u32(m-4)
                            birth=births.get(entity)
                            if birth and 0<entity<65536:
                                r=dict(anchor(b,phase,m),entity=entity,template=birth['template'],
                                    generation=birth['generation'],marker=marker.hex(),
                                    next16=b[m+len(marker):m+len(marker)+16].hex(),
                                    u32_after3=u32(m+3))
                                (literal if marker==MARKERS[0] else actions).append(r)
                            m=b.find(marker,m+1)
                    for marker in REG_MARKERS:
                        m=b.find(marker,14)
                        while m>=0:
                            entity=u32(m-4)
                            if (m+48<=len(b) and 0<entity<65536 and u32(m+44)==entity
                                    and b[m+3:m+8]==bytes(5) and 0<u32(m+8)<=0xffffff
                                    and 0<u32(m+12)<=0x7fffffff):
                                birth=births.get(entity)
                                public=accepted.get((phase,m,entity,u32(m+8)))
                                registrations.append(dict(anchor(b,phase,m),entity=entity,
                                    template=birth['template'] if birth else None,
                                    generation=birth['generation'] if birth else None,
                                    marker=marker.hex(),object_id=u32(m+8),projectile=u32(m+12),
                                    audited_parser_accepted=public is not None,
                                    parent_object=public.parent_damage_object_id if public else 0,
                                    registration_key=public.registration_key if public else '',
                                    birth_frame=birth['frame'] if birth else None))
                            m=b.find(marker,m+1)
            for h in proto.decode_v576_embedded_direct_damage_events(wire,None):
                if 0<(h.source_actor_id or 0)<65536:
                    birth=births.get(h.source_actor_id)
                    hits.append(dict(meta,entity=h.source_actor_id,
                        template=birth['template'] if birth else None,
                        generation=birth['generation'] if birth else None,
                        object_id=h.damage_object_id,target=h.target_actor_id,damage=h.damage,
                        result_group=h.damage_result_group_id,record_offset=h.record_offset,
                        skill=h.skill_id,exact_skill=h.skill_id_exact,wire=wire.hex(),
                        physical_hit_id=f'{capture.name}:{offset}:{mi}:{h.record_offset}',
                        raw_frame_sha256=hashlib.sha256(raw).hexdigest()))
            for r in typed:
                if r.source_actor_id<65536 and r.layout.startswith('c2s') and len(wire)==107:
                    birth=births.get(r.source_actor_id)
                    registrations.append(dict(anchor(xor_body(wire,r.xor_phase),r.xor_phase,r.record_offset),
                        entity=r.source_actor_id,template=birth['template'] if birth else None,
                        generation=birth['generation'] if birth else None,marker='client107',
                        object_id=r.damage_object_id,projectile=r.projectile_template_id,
                        audited_parser_accepted=True,parent_object=r.parent_damage_object_id,
                        registration_key=r.registration_key,birth_frame=birth['frame'] if birth else None))
    target_regs=[r for r in registrations if r['template'] in TARGETS]
    target_hits=[r for r in hits if r['template'] in TARGETS]
    target_objects={r['object_id'] for r in target_hits}
    matched_regs=[r for r in player_regs if r['object_id'] in target_objects]
    summary=dict(counts=counts,entity_owned_registrations=len(registrations),
        target_registrations=len(target_regs),literal_2404_known_entity=len(literal),
        target_literal_2404=sum(r['template'] in TARGETS for r in literal),
        registrations_by_template=dict(collections.Counter(str(r['template']) for r in registrations)),
        target_projectiles=dict(collections.Counter(str((r['template'],r['projectile'],r['marker'],r['audited_parser_accepted'])) for r in target_regs)),
        target_hit_rows=len(target_hits),target_hit_damage=sum(h['damage'] for h in target_hits),
        target_hits_by_template=dict(collections.Counter(str(h['template']) for h in target_hits)))
    summary['player_registrations_matching_target_hit_objects']=len(matched_regs)
    summary['matching_player_reg_projectiles']=dict(collections.Counter(str((r['projectile'],r['entity'])) for r in matched_regs))
    return dict(capture=str(capture),start=start,seconds=seconds,protocol_sha256=digest,
        policy='2404 is a literal observation, not a pre-certified action opcode. Short-owner registration is not player ownership.',
        summary=summary,births=all_births,registrations=registrations,actions=actions,
        literal_2404=literal,hits=hits,player_registrations_matching_hit_objects=matched_regs)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture',type=Path)
    ap.add_argument('--start',type=float,default=0)
    ap.add_argument('--seconds',type=float,default=180)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists(): raise FileExistsError(args.output)
    result=scan(args.capture,args.start,args.seconds)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(output=str(args.output),summary=result['summary']),ensure_ascii=False))


if __name__=='__main__':main()
