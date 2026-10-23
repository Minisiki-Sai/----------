"""Bounded review of saved FFFF examples; no decoder edits or raw capture IO."""
from __future__ import annotations
import json
import hashlib
import importlib.util
import sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/audit_remaining_registration_ffff_cached_20260907'


def load(relative):
    p=ROOT/'outputs'/relative
    return json.loads(p.read_text(encoding='utf-8')),dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


def u32(b,at):return int.from_bytes(b[at:at+4],'little')


def plain_parent(b,o,suffix_at,branch_kind):
    """Structural diagnostic only; sentinel allowed, never returns a skill."""
    if len(b)<o+suffix_at+4:return None
    owner,obj,tpl=u32(b,o),u32(b,o+9),u32(b,o+13)
    pid,branch,suffix=u32(b,o+50),u32(b,o+54),u32(b,o+suffix_at)
    if not (0<owner<0xffffffff and owner==u32(b,o+45) and b[o+4:o+9]==bytes(5)
            and 0<obj<=0xffffff and 0<tpl<=0x7fffffff
            and b[o+49]==0 and 0<pid<=0xffffff and pid!=obj
            and branch&0xffff==branch_kind and b[o+58:o+66]==b'\xff'*8
            and 0<suffix<=0xffff):return None
    return dict(owner=owner,obj=obj,template=tpl,parent_id=pid,suffix=suffix,sequence=branch>>16)


def preceding(gs,owner,obj,p,m):
    candidates=[g for g in gs if g['owner']==owner and g['obj']==obj and (g['p'],g['m'])<(p,m)]
    return max(candidates,key=lambda g:(g['p'],g['m'])) if candidates else None


def live_until(g,hit):
    stop=g.get('superseded_at',g.get('superseded'))
    return not stop or (hit['p'],hit['m'])<tuple(stop)


def main():
    OUT.mkdir(exist_ok=True);sources=[]
    pp=ROOT/'outputs/damage_attribution_candidate_v22_20260907/protocol_v576.py'
    spec=importlib.util.spec_from_file_location('ffff_readonly_protocol',pp)
    protocol=importlib.util.module_from_spec(spec);sys.modules[spec.name]=protocol;spec.loader.exec_module(protocol)
    def unique_phase(wire,o,suffix_at,branch_kind):
        return [phase for phase in range(13) if plain_parent(protocol._decode_v576_body_uncached(wire,phase),o,suffix_at,branch_kind)]
    sh,meta=load('audit_remaining_registration_server_20260907_v2/影识别错误.json');sources.append(meta)
    se,meta=load('audit_remaining_registration_exact_parent_20260907_v3/影识别错误.json');sources.append(meta)
    ice,meta=load('audit_remaining_registration_exact_parent_20260907_v3/0905黑龙经典冰灵.json');sources.append(meta)
    ic,meta=load('audit_remaining_registration_3021_context_20260907/scan_checkpoint.json');sources.append(meta)
    soul,meta=load('audit_remaining_registration_cross_shape_links_20260907_v1/0817黑典噬魂.json');sources.append(meta)
    shadow=[]
    for r in sh['strict_instances']:
        if r['template'] not in (5709,5737) or not r['fixed_slot_anchors']:continue
        b=bytes.fromhex(r['context_hex']);m=r['m']-r['context_start']
        normal=b[m-4:m]+b[m+3:m+101]
        row=plain_parent(normal,0,70,1)
        assert row and row['suffix']==65535
        parent=preceding(se['generations'],row['owner'],row['parent_id'],r['p'],r['m'])
        examples=r['anchor_examples']
        child_current=[]
        for h in examples:
            cg=preceding(se['generations'],row['owner'],row['obj'],h['p'],h['m'])
            child_current.append(bool(cg and cg['template']==row['template'] and live_until(cg,h)))
        shadow.append(dict(registration_packet=r['p'],marker=r['marker'],packet_length=r['length'],
            fields=row,parent_found=parent is not None,parent_packet=parent['p'] if parent else None,
            parent_template=parent['template'] if parent else None,parent_generation=parent['generation'] if parent else None,
            parent_current_for_anchor_hits=bool(parent and all(live_until(parent,h) for h in examples)),
            parent_age_seconds=r['t']-parent['t'] if parent else None,
            current_child_for_anchor_hits=all(child_current),
            static_parent_skill=parent['static_skill'] if parent else None,
            actual_root_action_fixture_available=False,
            root_evidence_note='The inspected saved reports contain object/resource records but no retained real6204 cast payload for this epoch. static_skill is not an action.',
            anchor_hits=examples,decoded_child_context_hex=r['context_hex'],
            parent_raw_record=parent.get('decoded_record_hex') if parent else None))
    ice_rows=[]
    for g in ice['generations']:
        if g['template']!=3021 or g['kind']=='server09' or g['resource']!=65535:continue
        b=bytes.fromhex(g['decoded_payload_hex']);o=g['offset']
        row=plain_parent(b,o,70,1);assert row and g['length']==112
        phases=unique_phase(bytes.fromhex(g['wire_payload_hex']),o,70,1)
        assert phases==[g['phase']]
        parent=preceding(ice['generations'],row['owner'],row['parent_id'],g['p'],g['m'])
        actions=[a for a in ic['casts'] if parent and a['skill_id']==2353 and 0<=parent['t']-a['t']<=2.5]
        hits=g['hit_examples']
        ice_rows.append(dict(child_packet=g['p'],fields=row,parent_found=parent is not None,
            parent_template=parent['template'] if parent else None,parent_generation=parent['generation'] if parent else None,
            parent_packet=parent['p'] if parent else None,parent_age_seconds=g['t']-parent['t'] if parent else None,
            parent_current_for_retained_hits=bool(parent and all(live_until(parent,h) and live_until(g,h) for h in hits)),
            raw_hits=g['raw_hits'],retained_hit_count=len(hits),retained_hit_damage=sum(h['damage'] for h in hits),hit_examples=hits,
            unique_actual_root_action=len(actions)==1,real_root_actions=actions,
            independently_rechecked_unique_phase=phases,
            root_raw_payloads=[ic['packets'][a['fixture']] for a in actions],
            raw_child_wire_hex=g['wire_payload_hex'],parent_raw_record=parent.get('decoded_record_hex') if parent else None,
            parent_raw_wire_hex=parent.get('wire_payload_hex') if parent else None))
    soul_rows=[]
    gs=[*soul['typed_generations'],*soul['parent_generations']]
    for g in soul['typed_generations']:
        if g['length']!=125 or g.get('end_relative_suffix')!=65535:continue
        b=bytes.fromhex(g['tail_hex']);row=plain_parent(b,0,83,3);assert row
        phases=unique_phase(bytes.fromhex(g['wire_hex']),g['offset'],83,3)
        assert phases==[g['phase']]
        parent=preceding(gs,row['owner'],row['parent_id'],g['p'],g['m'])
        hits=g['hit_examples']
        soul_rows.append(dict(child_packet=g['p'],fields=row,parent_found=parent is not None,
            parent_template=parent['template'] if parent else None,parent_generation=parent['generation'] if parent else None,
            parent_packet=parent['p'] if parent else None,parent_age_seconds=g['t']-parent['t'] if parent else None,
            parent_current_for_retained_hits=bool(parent and all(live_until(parent,h) and live_until(g,h) for h in hits)),
            raw_hits=g['raw_hits'],retained_hit_count=len(hits),retained_hit_damage=sum(h['damage'] for h in hits),hit_examples=hits,
            actual_root_action_fixture_available=False,
            independently_rechecked_unique_phase=phases,
            raw_child_wire_hex=g['wire_hex'],parent_raw_record=parent.get('decoded_record_hex') if parent else None))
    stats={}
    for key,rows in [('shadow',shadow),('ice',ice_rows),('soul',soul_rows)]:
        stats[key]=dict(rows=len(rows),parents_found=sum(r['parent_found'] for r in rows),
                       parent_templates=dict(Counter(r['parent_template'] for r in rows)),
                       retained_hits=sum(len(r.get('anchor_hits',r.get('hit_examples',[]))) for r in rows),
                       retained_damage=sum(h['damage'] for r in rows for h in r.get('anchor_hits',r.get('hit_examples',[]))),
                       real_root_action_fixtures=sum(r.get('unique_actual_root_action',r.get('actual_root_action_fixture_available',False)) for r in rows))
    stats['shadow']['by_template']={str(t):dict(registrations=sum(r['fields']['template']==t for r in shadow),
        anchor_rows=sum(len(r['anchor_hits']) for r in shadow if r['fields']['template']==t)) for t in (5709,5737)}
    stats['shadow']['distinct_parent_owner_object_generations']=len({(r['fields']['owner'],r['fields']['parent_id'],r['parent_generation']) for r in shadow})
    stats['shadow']['all_current_children']=all(r['current_child_for_anchor_hits'] for r in shadow)
    stats['ice']['qualified_root_candidate_retained_hits']=sum(len(r['hit_examples']) for r in ice_rows if r['unique_actual_root_action'])
    stats['ice']['qualified_root_candidate_retained_damage']=sum(h['damage'] for r in ice_rows if r['unique_actual_root_action'] for h in r['hit_examples'])
    result=dict(sources=sources,summary=stats,shadow=shadow,ice=ice_rows,soul=soul_rows,
                limitation='No new raw IO. Cached hit examples are capped and may contain relay duplicates. This review does not establish all-hit conservation or global attribution accuracy.',
                runtime_modified=False)
    (OUT/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False))


if __name__=='__main__':main()
