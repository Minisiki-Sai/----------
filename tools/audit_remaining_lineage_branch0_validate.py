"""Qualify cached complete raw windows, not inferred labels or fixed slots."""
from __future__ import annotations
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_remaining_lineage_branch0_patch import OUT, BASE, EXPECTED_BASE_SHA, isolated, emit_patch


def u(b,p):return int.from_bytes(b[p:p+4],'little')
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(n,x):(OUT/n).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def main():
    raw=read(OUT/'raw_windows.json'); p=isolated('_branch0_qualified',True);old=isolated('_branch0_baseline')
    assert raw['protocol_sha256']==EXPECTED_BASE_SHA
    cases=[];all_rows=[];counts=Counter();omitted=[]
    for c in raw['cases']:
        owner=c['owner']; regs=c['registrations']
        parent_rows=[r for r in regs if r['obj']==c['parent']]
        assert len(parent_rows)==1 and parent_rows[0]['template']==2022
        parent=parent_rows[0];pt=parent['t'];pm=parent['m'];pw=bytes.fromhex(c['packets'][parent['fixture']]['payload_hex'])
        parent_phases=[]
        for ph in range(13):
            b=p._decode_v576_body_uncached(pw,ph)
            if b[pm:pm+3]==b'\x09\x61\0' and u(b,pm-4)==u(b,pm+44)==owner and b[pm+3:pm+8]==bytes(5) and u(b,pm+8)==c['parent'] and u(b,pm+12)==2022:
                parent_phases.append(ph)
        assert parent_phases==[parent['phase']]
        roots=[a for a in c['actions'] if a['decoded']['skill_id'] in (1208,1352) and 0<=pt-a['t']<=2.5]
        assert len(roots)==1
        root=roots[0]; rw=bytes.fromhex(c['packets'][root['fixture']]['payload_hex']);root_anchors=[]
        for ph in range(13):
            b=p._decode_v576_body_uncached(rw,ph)
            for at in range(14,len(b)-7):
                if u(b,at-4)==owner and b[at:at+3] in (b'\x0e\x23\0',b'\x12\x24\0') and u(b,at+3)==1208:
                    root_anchors.append(dict(phase=ph,marker=at,skill_offset=at+3,skill=1208))
        assert len(root_anchors)==1
        concurrent=[a for a in c['actions'] if a['decoded']['skill_id']==1353 and pt<a['t']<min(r['child']['t'] for r in c['cached'])]
        assert len(concurrent)==1
        relevant={c['parent']}|{r['child']['obj'] for r in c['cached']}
        # Retain every source registration, including unknown templates, and
        # require a sole current record for each selected ID in the whole window.
        for obj in relevant:
            assert len([r for r in regs if r['obj']==obj])==1,(c['id'],obj)
        assert not c['lifecycle']
        bad=[r for r in c['raw_registration_candidates'] if r['obj'] in relevant and not r['guard']]
        assert not bad
        tcp=Counter();expected_seq={}
        for f in c['non_udp_frames']:
            frame=bytes.fromhex(f['frame_hex']);packet=p.decode_captured_frame(f['t'],frame,f['original'])
            if packet is None or packet.transport!='TCP':continue
            assert not packet.tcp_flags & 7, 'SYN/FIN/RST in window'
            key=(packet.source_ip,packet.source_port,packet.destination_ip,packet.destination_port)
            if packet.payload:
                if key in expected_seq:
                    delta=packet.tcp_sequence-expected_seq[key]
                    tcp['sequence_gaps']+=delta>0;tcp['retransmission_or_overlap']+=delta<0
                expected_seq[key]=max(expected_seq.get(key,0),packet.tcp_sequence+len(packet.payload))
                tcp['data_segments']+=1
        assert not tcp['sequence_gaps']
        proved=[]
        for cached in c['cached']:
            oid=cached['child']['obj'];child=next(r for r in regs if r['obj']==oid)
            meta=c['packets'][child['fixture']];wire=bytes.fromhex(meta['payload_hex']);m=child['m']
            parsed=p.decode_v576_server_damage_object_registrations(wire,None,(2023,),include_unmapped=True)
            chosen=[r for r in parsed if r.source_actor_id==owner and r.damage_object_id==oid]
            assert len(chosen)==1
            row=chosen[0]
            if not row.parent_damage_object_id:
                omitted.append(dict(case=c['id'],child=oid,fixture=child['fixture'],length=meta['length'],marker=m,
                                    reason='outside separately validated119/224 branch0 envelopes'))
                continue
            assert row.parent_damage_object_id==c['parent'] and row.projectile_template_id==2023
            phases=[ph for ph in range(13) if p._decode_v576_server_branch0_parent_tail(p._decode_v576_body_uncached(wire,ph),m)]
            assert phases==[child['phase']]
            hit=[h for h in c['hits'] if h['decoded']['damage_object_id']==oid and any(
                 h['t']==x['t'] and h['decoded']['damage']==x['damage'] for x in cached['cached_hit_observations'])]
            assert len(hit)==1
            h=hit[0];assert len(h['phases'])==1
            assert parent['order']<child['order']<h['order']
            assert 0<=h['t']-parent['t']<8
            x=dict(case=c['id'],owner=owner,parent=c['parent'],child=oid,
                   parent_fixture=parent['fixture'],root_fixture=root['fixture'],root_anchor=root_anchors[0],
                   concurrent_fixture=concurrent[0]['fixture'],child_fixture=child['fixture'],hit_fixture=h['fixture'],
                   marker=m,phase=child['phase'],registration=asdict(row),hit=h,
                   qualified=True,raw_fixed_context_not_root_truth=h['decoded']['skill_id'])
            proved.append(x);all_rows.append(x)
        # Select3 different roots/parents: two double records + one single.
        select_id={102375:102405,100513:100533,108833:108854}[c['parent']]
        proof=next(r for r in proved if r['child']==select_id)
        keys={proof[k]:k for k in ('root_fixture','parent_fixture','concurrent_fixture','child_fixture','hit_fixture')}
        packets=[{**c['packets'][k], 'fixture':k,'role':role} for k,role in keys.items()]
        packets.sort(key=lambda r:(r['frame'],r['message']))
        h=proof['hit']; hd=h['decoded'];body=p._decode_v576_body_uncached(bytes.fromhex(c['packets'][h['fixture']]['payload_hex']),h['phases'][0]);hm=hd['record_offset']
        expected=dict(owner=owner,parent_id=c['parent'],object_id=select_id,skill_id=1208,
                      parent_template=2022,child_template=2023,target=hd['target_actor_id'],damage=hd['damage'],
                      critical=hd['critical'],result_group=hd['damage_result_group_id'],fixed_context=1353,
                      hit_marker=hm,child_marker=proof['marker'],child_phase=proof['phase'])
        assert u(body,hm-4)==expected['target'] and u(body,hm+13)==select_id
        assert int.from_bytes(body[hm+19:hm+27],'little')==expected['damage'] and bool(body[hm+35]&1)==expected['critical']
        cases.append(dict(name=c['id'],packets=packets,expected=expected,proof=proof,
                          complete_window_counts=c['counts'],tcp_continuity=dict(tcp),
                          all_relevant_registrations=[r for r in regs if r['obj'] in relevant]))
        # Every raw window packet: additive metadata only, no new prefix rows.
        for meta in c['packets'].values():
            wire=bytes.fromhex(meta['payload_hex'])
            before=old.decode_v576_server_damage_object_registrations(wire,None,(),include_unmapped=True)
            after=p.decode_v576_server_damage_object_registrations(wire,None,(),include_unmapped=True)
            assert len(before)==len(after)
            for a,b in zip(before,after):
                aa,bb=asdict(a),asdict(b)
                if aa!=bb:
                    assert not a.parent_damage_object_id and b.parent_damage_object_id and b.projectile_template_id==2023
                    for k in ('parent_damage_object_id','effect_resource_id','child_sequence','registration_key','layout'):bb[k]=aa[k]
                    assert aa==bb
                    counts['rows_add_parent_metadata']+=1
            counts['all_raw_window_messages_compared']+=1
    validation=dict(qualified_distinct_parent_cases=len(cases),qualified_disputed_hits=len(all_rows),
        qualified_disputed_damage=sum(r['hit']['decoded']['damage'] for r in all_rows),
        raw_phase_and_record_checks_passed=True,omitted=omitted,**counts,
        caveat='Root association is a unique prior real1208 action within2.5s of its typed2022 root; no wire action-object nonce is claimed. Child uses explicitfullparentID, never nearest action.',
        raw_sha256=hashlib.sha256((OUT/'raw_windows.json').read_bytes()).hexdigest())
    write('validation.json',validation);write('real_cases.json',dict(cases=cases,all_qualified=all_rows,capture_hashes=raw['captures']))
    emit_patch()
    print(json.dumps(validation,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
