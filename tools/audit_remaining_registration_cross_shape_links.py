"""Raw-only exact-ID checks for newly surveyed cross-profession shapes.

Uses retained raw prefix records, also observes every guarded server09 record
to invalidate reused slots. No scoring, nearest action, job or guessed name.
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_remaining_registration_server_variants import DATA_DIR,hashes
from tools.audit_remaining_registration_packet_decoder import u32
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.live_dps.protocol_v576 import (
    _decode_v576_body,_v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,decode_v576_scene_teardown,
)
from dragon_nest_builder.live_dps.damage_object_origins import (
    _SPEC_BY_PROJECTILE_TEMPLATE as SPECS,_PARENT_TEMPLATES_BY_CHILD as PARENTS,
    PARENT_LINEAGE_REQUIRED_PROJECTILE_TEMPLATE_IDS as SHARED,
)


def audit(report_path,output):
    doc=json.loads(report_path.read_text(encoding='utf8'));path=Path(doc['capture'])
    catalog=json.loads((ROOT/'outputs/damage_attribution/attack_instance_catalog_v576.json').read_text(encoding='utf8'))
    proven={r['projectile_template_id'] for r in catalog['projectile_evidence'] if r['deterministic_client_action_bridge']}
    bypacket=defaultdict(list)
    for r in doc['rows']:
        if r['phase_unique']:bypacket[r['p']].append(r)
    before=hashes();source=V576DiagnosticReplaySource(path,DATA_DIR,{})
    serial=0;scene=0;slots={};recent={};counters=Counter();gens=[];stats=Counter()
    def observe(r):
        key=(scene,r['owner'],r['obj']);token=(*key,r['kind'],r['tail_hex'])
        old=recent.get(token)
        if old is not None and abs(r['t']-gens[old]['t'])<=.25:
            stats['exact_replicas']+=1;return
        old=slots.get(key)
        if old is not None:gens[old]['superseded']=(r['p'],r['m'])
        counters[key]+=1
        spec=SPECS.get(r['template'])
        static=spec if spec and r['template'] in proven and r['template'] not in SHARED else None
        g={**r,'id':len(gens),'scene':scene,'generation':counters[key],
           'static_skill':static.canonical_skill_id if static else 0,
           'static_skill_ids':sorted(static.skill_ids) if static else [],
           'raw_hits':0,'target_hits':Counter(),'fixed':Counter(),'hit_examples':[],
           'fixed_examples':[],'superseded':None,'parent_binding':None}
        if r.get('parent_id'):
            pid=slots.get((scene,r['owner'],r['parent_id']))
            pg=gens[pid] if pid is not None else None
            bind={'parent_id':r['parent_id'],'branch_type':r['branch_type'],'status':'no_exact_parent'}
            if pg:
                age=r['t']-pg['t'];edge=pg['template'] in PARENTS.get(r['template'],())
                bind.update(parent_generation_id=pid,parent_packet=pg['p'],parent_generation=pg['generation'],
                            parent_template=pg['template'],parent_age=round(age,6),static_edge=edge,
                            parent_skill=pg['static_skill'],parent_skill_ids=pg['static_skill_ids'],
                            parent_fixed_before=dict(pg['fixed']))
                bind['status']=('stale_parent' if not 0<=age<=8 else 'edge_mismatch' if not edge
                                else 'parent_skill_unproven' if not pg['static_skill'] else 'exact_ACT_parent')
            g['parent_binding']=bind
        gens.append(g);slots[key]=g['id'];recent[token]=g['id']
    def feed(packet):
        nonlocal serial,scene
        serial+=1;t=float(packet.timestamp_seconds);payload=bytes(packet.payload)
        if str(packet.transport).upper()=='TCP':
            if decode_v576_scene_teardown(payload,packet.inferred_direction(),connection_context=packet):
                scene+=1;slots.clear();stats['scene_resets']+=1
            return ()
        if str(packet.transport).upper()!='UDP':return ()
        records=[]
        for cached in bypacket.get(serial,()):
            assert cached['wire_hex']==payload.hex()
            r={**cached,'kind':'typed','m':cached['offset']-1,'parent_id':None,
               'branch_type':None,'target_field_candidate':None}
            tail=bytes.fromhex(r['tail_hex'])
            if len(tail) in (102,115) and tail[49]==0 and tail[58:66]==b'\xff'*8:
                parent=u32(tail,50);branch=u32(tail,54)&0xffff
                if 0<parent<=0xffffff and parent!=r['obj'] and branch in (1,3):
                    r['parent_id']=parent;r['branch_type']=branch
            if len(tail)==110 and tail[53:61]==b'\xff'*8:
                r['target_field_candidate']=u32(tail,70)
            if len(tail)==115 and r['parent_id']:
                r['target_field_candidate']=u32(tail,75)
            records.append(r)
        seen=set()
        for phase in _v576_candidate_xor_phases(payload):
            b=_decode_v576_body(payload,phase);m=b.find(b'\x09',9)
            while m>=0:
                if m+48<=len(b) and b[m+2:m+8]==bytes(6):
                    owner,obj,tpl=u32(b,m-4),u32(b,m+8),u32(b,m+12)
                    if 0<owner<0xffffffff and owner==u32(b,m+44) and 0<obj<=0xffffff and 0<tpl<=0x7fffffff:
                        k=(m,owner,obj,tpl)
                        if k not in seen:
                            seen.add(k);records.append(dict(p=serial,m=m,t=t,owner=owner,obj=obj,template=tpl,
                                                             kind='server09',length=len(payload),offset=m-4,
                                                             phase=phase,tail_hex=b[m-4:m+48].hex(),
                                                             target_field_candidate=None,parent_id=None))
                m=b.find(b'\x09',m+1)
        hits=decode_v576_embedded_direct_damage_events(payload,None,source._decoder.damage_skill_ids)
        ordered=[(r['m'],0,r) for r in records]+[(h.record_offset,1,h) for h in hits]
        for _,typ,item in sorted(ordered,key=lambda x:(x[0],x[1])):
            if typ==0:observe(item);continue
            h=item;gid=slots.get((scene,int(h.source_actor_id or 0),int(h.damage_object_id or 0)))
            if gid is None:continue
            g=gens[gid]
            if not 0<=t-g['t']<=8:continue
            g['raw_hits']+=1;target=int(h.target_actor_id or 0);g['target_hits'][target]+=1
            hr=dict(p=serial,m=int(h.record_offset),t=t,target=target,damage=int(h.damage or 0),
                    owner=int(h.source_actor_id or 0),obj=int(h.damage_object_id or 0),
                    fixed_skill=int(h.skill_id) if h.skill_id_exact else 0)
            if len(g['hit_examples'])<3:g['hit_examples'].append(hr)
            if hr['fixed_skill']:
                g['fixed'][hr['fixed_skill']]+=1;g['fixed_examples'].append(hr)
        return ()
    source._decoder.feed_events=feed
    try:source.poll()
    finally:source.close()
    assert serial==doc['feed_packets']
    selected=[g for g in gens if g['kind']=='typed']
    groups=defaultdict(Counter)
    for g in selected:
        key=f"L{g['length']}/T{g['template']}/branch{g.get('branch_type')}/target{g['target_field_candidate']}"
        c=groups[key];c['generations']+=1;c['raw_hits']+=g['raw_hits'];c['fixed_slots']+=sum(g['fixed'].values())
        if g['target_field_candidate'] not in (None,0xffffffff):
            c['generations_with_hits']+=bool(g['raw_hits'])
            c['candidate_is_a_linked_hit_target']+=g['target_field_candidate'] in g['target_hits']
            c['hits_to_candidate']+=g['target_hits'].get(g['target_field_candidate'],0)
        bind=g['parent_binding']
        if bind:
            c['parent_'+bind['status']]+=1
            if bind['status']=='exact_ACT_parent':
                g['fixed_vs_parent_conflicts']=[h for h in g['fixed_examples'] if h['fixed_skill'] not in bind['parent_skill_ids']]
                c['fixed_vs_parent_conflicts']+=len(g['fixed_vs_parent_conflicts'])
        if g['static_skill']:
            g['fixed_vs_static_conflicts']=[h for h in g['fixed_examples'] if h['fixed_skill'] not in g['static_skill_ids']]
            c['fixed_vs_static_conflicts']+=len(g['fixed_vs_static_conflicts'])
    parent_ids={g['parent_binding']['parent_generation_id'] for g in selected
                if g['parent_binding'] and 'parent_generation_id' in g['parent_binding']}
    after=hashes();report=dict(capture=str(path),policy='raw only; observed slot generations including unknown server09 invalidation; no scoring or action guess',
                             feed_packets=serial,stats=stats,core_before=before,core_after=after,
                             core_unchanged=before==after,groups=groups,typed_generations=selected,
                             parent_generations=[gens[i] for i in sorted(parent_ids)])
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(capture=path.name,core_unchanged=before==after,
                         special_groups={k:v for k,v in groups.items() if not k.startswith('L107/')}),ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('reports',nargs='+',type=Path)
    p.add_argument('--output-dir',required=True,type=Path);a=p.parse_args()
    sys.stdout.reconfigure(encoding='utf8');a.output_dir.mkdir(parents=True,exist_ok=True)
    for path in a.reports:
        out=a.output_dir/path.name
        if out.exists():print('EXISTS',out,flush=True);continue
        print('START',path.stem,flush=True);audit(path,out)


if __name__=='__main__':main()
