"""Re-read one actual additional-prefix window; no production promotion."""
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.replay_native_wire_state_v47 import HEADER,OUT
from tools.audit_projectile_stream_v45 import parse_prefix
from dragon_nest_builder.live_dps import protocol_v576 as p
from dragon_nest_builder.live_dps.protocol_catalogs import _load_skill_catalog,_load_damage_skill_ids
from dragon_nest_builder.data_loader import DATA_DIR
from dragon_nest_builder.live_dps.damage_object_origins import DamageObjectOriginLedger,_SPEC_BY_PROJECTILE_TEMPLATE
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records


def main():
    source=OUT/'0905黑龙经典冰灵_replay.json';raw=source.read_bytes();report=json.loads(raw)
    selected=next(e for e in report['additional_prefix_candidates']['examples'] if e['registration']['object']['config_row']==11056)
    reg=selected['registration'];owner=reg['object']['actor_id'];when=reg['time']
    catalog=_load_skill_catalog(str(DATA_DIR));skills=frozenset(catalog);damage=_load_damage_skill_ids(str(DATA_DIR))
    ledger=DamageObjectOriginLedger();control=DamageObjectOriginLedger();counts=Counter();rows=[];frame=0;serial=0;rawsize=0
    with Path(report['capture']).open('rb') as f:
        if f.read(6)!=b'DNDP1\0':raise ValueError('format')
        while head:=f.read(HEADER.size):
            if len(head)!=HEADER.size:raise ValueError('truncated header')
            t,adapter,original,length=HEADER.unpack(head);offset=f.tell()-HEADER.size;blob=f.read(length);frame+=1
            if len(blob)!=length:raise ValueError('truncated frame')
            if t<when-5:continue
            if t>when+5:break
            rawsize+=len(head)+length
            if rawsize>2*1024*1024:raise ValueError('bounded window exceeded')
            packet=p.decode_captured_frame(t,blob,original)
            if not packet or packet.transport!='UDP':continue
            for mi,wire in enumerate(p.split_v576_payload(packet.payload)):
                serial+=1;direction=packet.inferred_direction();proof=sha256(wire).hexdigest()
                loc=dict(frame=frame,file_offset=offset,message=mi,time=t,wire=wire.hex(),wire_sha256=proof)
                # Only real structured actions; primary inferred hits do not feed action history.
                actions=list(p.decode_v576_structured_actions(wire,direction,skills,{owner}))
                primary=p.decode_v576_payload(wire,direction,damage,skills,{owner})
                if primary and primary.kind=='skill_cast':actions.append(primary)
                own=[a for a in actions if a.source_actor_id==owner]
                for action in own:
                    ledger.observe_action(owner,action.skill_id,t)
                    counts['actual_action_rows']+=1
                    rows.append(dict(kind='action',**loc,decoded=asdict(action)))
                inv=inspect_udp_damage_records(wire,direction)
                if not inv.complete:continue
                for record in inv.typed_records:
                    if frame==reg['frame'] and mi==reg['message'] and record.record_offset==reg['marker']:
                        if proof!=reg['wire_sha256'] or record.record_bytes.hex()!=reg['record_hex']:raise ValueError('raw anchor changed')
                        parsed=parse_prefix(record.record_bytes,7,len(record.record_bytes))
                        if parsed.reference_branch!=0 or parsed.reference_id!=owner or parsed.config_row!=11056:raise ValueError('root context mismatch')
                        kwargs=dict(source_actor_id=owner,damage_object_id=parsed.object_index,projectile_template_id=parsed.config_row,
                            effect_resource_id=parsed.action_resource,child_sequence=parsed.child_sequence,timestamp=t,
                            packet_serial=serial,record_offset=record.record_offset,action_qualified_subtype=True,state_observation=True,
                            registration_key=record.record_bytes.hex())
                        live=ledger.observe_registration(**kwargs);negative=control.observe_registration(**kwargs)
                        rows.append(dict(kind='registration',**loc,native=asdict(parsed),model_origin=asdict(live) if live else None,
                                         no_action_control=asdict(negative) if negative else None))
                    hit=next((h for h in inv.damage_records if h.record_offset==record.record_offset),None)
                    if hit and hit.source_actor_id==owner and hit.damage_object_id==reg['object']['reference']:
                        kwargs=dict(source_actor_id=owner,timestamp=t,layout='server-direct37',payload=wire,direction=direction,
                            damage=hit.damage,record_offset=hit.record_offset,packet_serial=serial)
                        yes=ledger.attribute_direct37(**kwargs);no=control.attribute_direct37(**kwargs)
                        counts['matched_raw_damage_rows']+=1;counts['existing_gate_positive']+=bool(yes);counts['no_action_control_positive']+=bool(no)
                        rows.append(dict(kind='hit',**loc,record_offset=hit.record_offset,target=hit.target_actor_id,damage=hit.damage,
                            admitted_by_existing_policy=asdict(yes) if yes else None,no_action_control=asdict(no) if no else None))
    result=dict(schema='native-additional-prefix-candidate-v47',source=str(source),source_sha256=sha256(raw).hexdigest(),
        capture=report['capture'],capture_sha256=report['capture_sha256'],window=[when-5,when+5],physical_window_bytes=rawsize,
        counts=dict(counts),rows=rows,production_changed=False,new_confirmed_hits=0,
        scope='Existing lineage gate control using exact real wire. Not a new native ACT certificate or full accepted-event comparison.')
    target=OUT/'candidate_11056.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=lambda x:sorted(x) if isinstance(x,(set,frozenset)) else str(x)),encoding='utf-8')
    print(json.dumps(dict(counts=dict(counts),bytes=rawsize,rows=len(rows)),ensure_ascii=False))


if __name__=='__main__':main()
