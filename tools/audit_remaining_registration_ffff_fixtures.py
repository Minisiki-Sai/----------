"""Reduce saved raw windows to six unmodified root/parent/child/hit fixtures."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'outputs/audit_remaining_registration_ffff_raw_20260907'
OUT=ROOT/'outputs/audit_remaining_registration_ffff_final_20260907'

def main():
    proofs=json.loads((RAW/'proofs.json').read_text(encoding='utf-8'));cases=[]
    for label in ('shadow','soul','ice'):
        d=json.loads((RAW/f'{label}.json').read_text(encoding='utf-8'));packets=d['packets']
        selectors=({'shadow':[(5709,'server09',102),(5737,'server09',102),(5709,'server09',98)],
                    'soul':[(11076,'typed',125)],'ice':[(3021,'typed',112),(3021,'server09',224)]})[label]
        for tpl,kind,length in selectors:
            rows=[]
            for r in proofs['reports'][label]['rows']:
                reg=r['registration'];p=packets[reg['fixture']]
                if not r['qualified'] or not r['hits'] or reg['template']!=tpl or reg['kind']!=kind or p['direction'] is None:continue
                if label=='shadow':
                    if int.from_bytes(bytes.fromhex(p['plaintext_by_phase'][str(reg['phase'])])[reg['marker']+1:reg['marker']+3],'little')!=length:continue
                elif reg['packet_length']!=length:continue
                rows.append(r)
            assert rows,(label,tpl,kind,length)
            # Prefer a bundled direct37 fixture, avoiding the separate legacy
            # 148-byte primary-target bug (documented, not fixed by this patch).
            if label=='soul':rows=[r for r in rows if any(h['record_offset']>18 for h in r['hits'])]
            r=rows[0];reg=r['registration'];fields=r['fields']
            root=next(a for a in r['root_action_epochs'][0] if packets[a['fixture']]['direction'] is not None)
            parent_candidates=[p for p in d['registrations'] if p['obj']==fields['parent'] and p['owner']==fields['owner']
                and p['template']==r['parent']['template'] and abs(p['timestamp']-r['parent']['timestamp'])<0.01
                and packets[p['fixture']]['direction'] is not None]
            parent=parent_candidates[0] if parent_candidates else r['parent']
            hit=next(h for h in r['hits'] if packets[h['fixture']]['direction'] is not None
                     and (label!='soul' or h['record_offset']>18))
            selected=[]
            for role,event in [('root',root),('parent',parent),('child',reg),('hit',hit)]:
                selected.append(dict(role=role,fixture=event['fixture'],**packets[event['fixture']]))
            extra=[]
            if label=='shadow':
                wrong=next(a for a in d['actions'] if a['skill_id']==6212 and packets[a['fixture']]['direction'] is not None)
                extra=[dict(role='wrong_root',fixture=wrong['fixture'],**packets[wrong['fixture']])]
            cases.append(dict(name=f'{label}_{kind}_{length}_{tpl}',capture=d['capture'],capture_sha256=d['capture_sha256'],
                packets=selected,wrong_root_packets=extra,child_registration=reg,
                expected=dict(owner=fields['owner'],object_id=fields['obj'],template=tpl,parent_id=fields['parent'],
                    parent_template=parent['template'],skill=root['skill_id'],target=hit['target_actor_id'],
                    damage=hit['damage'],critical=hit['critical'],result_group=hit['damage_result_group_id'],
                    phase=reg['phase'],suffix=65535,sequence=fields['sequence'],fingerprint=fields['tail_hex']),
                original_events=dict(root=root,parent=parent,child=reg,hit=hit)))
        del d
    target=ROOT/'tests/data/damage_object_captured_ffff_parent_chain.json'
    target.write_text(json.dumps(dict(cases=cases,static_evidence=dict(soul=proofs['soul_static'],
        earlier_ice_shadow_resource_report='audit_remaining_registration_3021_context_20260907/act_dnt_evidence.json'),
        note='Original full frames and payloads; four packets/case; no invented root/Hit or ledger seeding.'),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(path=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        cases=[c['name'] for c in cases]),ensure_ascii=False))

if __name__=='__main__':main()
