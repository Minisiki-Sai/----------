"""Retain original offsets and exact raw root/parent/child/hit regressions."""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p
from tools.build_live_dps_monster_catalog import parse_dnt
from tools.audit_remaining_lineage_five_jobs_act_transitions import parse_full


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908'
    r=Path('E:/ext0811.rar/Resource00/Resource00/resource')
    wp={x['RowId']:x for x in parse_dnt(r/'ext/weapontable_projectile.dnt')}
    files={x['RowId']:x for x in parse_dnt(r/'ext/filetable.dnt')}
    paths={x.name.casefold():x for x in r.rglob('*.act')}
    static=[]
    for template in (5707,5715):
        filename=files[wp[template]['_ActName']]['_FileName']
        path=paths[filename.replace('\\','/').split('/')[-1].casefold()]
        parsed=parse_full(path)
        exits=[dict(action=a['name'],offset=s['offset'],start=s['start'],end=s['end'])
            for a in parsed['actions']for s in a['signals']if s['type']==15 and s['u32'][0]==5711]
        assert exits
        static.append(dict(template=template,projectile_row=wp[template],file_row=files[wp[template]['_ActName']],
            path=str(path),sha256=sha256(path.read_bytes()).hexdigest(),exits=exits))
    cases=[];envelopes=[]
    for suffix,inventory in [('0817','shared_assassin_envelopes_0817.json'),('0816','shared_rake_0816.json'),('0809','shared_rake_0809.json')]:
        d=json.loads((out/f'rake_candidates_{suffix}.json').read_text('utf-8'))
        capture=Path(d['capture'])
        def verify(anchor):
            with capture.open('rb')as f:
                f.seek(anchor['file_offset']);t,_,original,n=struct.unpack('<dBII',f.read(17))
                packet=p.decode_captured_frame(t,f.read(n),original)
            wire=anchor.get('wire',anchor.get('wire_hex'))
            assert packet and t==anchor['t'] and bytes.fromhex(wire)in p.split_v576_payload(packet.payload)
        for template in (5707,5715):
            e=next(e for e in d['examples']if e['child']['parent_template']==template and len(e['child']['root_actions'])==1)
            child,hit=e['child'],e['hit'];root=child['root_actions'][0];parent=child['parent']
            packets=[]
            for role,a in [('root',root),('parent',parent),('child',child),('hit',hit)]:
                verify(a)
                packets.append(dict(role=role,t=a['t'],frame=a['frame'],file_offset=a['file_offset'],message=a['message'],
                    wire=a['wire'],direction=a['direction'],sha256=sha256(bytes.fromhex(a['wire'])).hexdigest()))
            body=p._decode_v576_body_uncached(bytes.fromhex(hit['wire']),hit['phase'])
            cases.append(dict(name=f'{suffix}-{template}',capture=str(capture),packets=packets,
                child=child['registration'],parent=parent['registration'],
                expected=dict(owner=hit['source'],target=hit['target'],object=hit['object'],amount=hit['amount'],
                    result_group=hit['result_group'],critical=bool(body[hit['marker']+35]&1),hit_marker=hit['marker'],
                    parent_id=child['parent_id'],parent_template=template,skill=6203)))
        inv=json.loads((out/inventory).read_text('utf-8'))
        for row in inv['rows']:
            if row['template']!=5711:continue
            for a in row['examples']:
                verify(a)
                envelopes.append(dict(capture=str(capture),t=a['t'],file_offset=a['file_offset'],wire=a['wire_hex'],
                    phase=a['reg']['xor_phase'],marker=a['reg']['record_offset'],
                    parent=int.from_bytes(bytes.fromhex(a['decoded_hex'])[a['reg']['record_offset']+49:a['reg']['record_offset']+53],'little')))
    data=dict(policy='Raw exact root/parent/child joins; no old skill labels; fixtures are not coverage totals',
        static=static,cases=cases,envelopes=envelopes)
    (ROOT/'tests/data/rake_branch3_raw.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('raw cases',len(cases),'envelopes',len(envelopes),'parent ACTs',len(static))


if __name__=='__main__':main()
