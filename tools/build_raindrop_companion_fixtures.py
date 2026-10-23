"""Keep newly supported companion envelopes plus the rejected original row."""
import json
from pathlib import Path
import struct
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dragon_nest_builder.live_dps import protocol_v576 as p


def main():
    out=ROOT/'outputs/compact_entity_selectors_20260908';cases=[]
    for name in ('shared_assassin_envelopes_0817.json','shared_rain_0816.json','shared_rain_0809.json'):
        data=json.loads((out/name).read_text('utf-8'))
        with Path(data['capture']).open('rb')as f:
            for row in data['rows']:
                if row['template']not in(5709,5737)or row['parent_read']:continue
                for e in row['examples']:
                    f.seek(e['file_offset']);t,_,original,n=struct.unpack('<dBII',f.read(17))
                    packet=p.decode_captured_frame(t,f.read(n),original)
                    assert packet and bytes.fromhex(e['wire_hex'])in p.split_v576_payload(packet.payload)
                    body=bytes.fromhex(e['decoded_hex']);m=e['reg']['record_offset']
                    fields=p._decode_v576_server_shared_lineage_parent_tail(body,m)
                    cases.append(dict(capture=data['capture'],wire=e['wire_hex'],t=t,file_offset=e['file_offset'],
                        marker=m,phase=e['reg']['xor_phase'],template=row['template'],
                        parent=int.from_bytes(body[m+49:m+53],'little'),accepted=fields is not None))
    assert sum(x['accepted']for x in cases)==293 and len(cases)==294
    accepted={(c['capture'],c['file_offset'],c['wire'])for c in cases if c['accepted']}
    chains=[]
    for suffix in ('0817','0816','0809'):
        d=json.loads((out/f'rain_candidates_{suffix}_v2.json').read_text('utf-8'))
        for template in (5709,5737):
            e=next(e for e in d['examples']if e['child']['registration']['projectile_template_id']==template
                and len(e['child']['root_actions'])==1 and
                (d['capture'],e['child']['file_offset'],e['child']['wire'])in accepted)
            child,hit=e['child'],e['hit'];parent=child['parent'];packets=[]
            with Path(d['capture']).open('rb')as f:
                for role,a in [('root',child['root_actions'][0]),('parent',parent),('child',child),('hit',hit)]:
                    f.seek(a['file_offset']);t,_,original,n=struct.unpack('<dBII',f.read(17))
                    packet=p.decode_captured_frame(t,f.read(n),original)
                    assert packet and t==a['t']and p.split_v576_payload(packet.payload)[a['message']].hex()==a['wire']
                    packets.append(dict(role=role,**{k:a[k]for k in ('t','frame','file_offset','message','wire','direction')}))
            body=p._decode_v576_body_uncached(bytes.fromhex(hit['wire']),hit['phase'])
            chains.append(dict(name=f'{suffix}-{template}',capture=d['capture'],packets=packets,
                child=child['registration'],parent=parent['registration'],
                expected=dict(owner=hit['source'],target=hit['target'],object=hit['object'],amount=hit['amount'],
                    result_group=hit['result_group'],critical=bool(body[hit['marker']+35]&1),
                    parent_id=child['parent_id'],parent_template=child['parent_template'],skill=6204)))
    (ROOT/'tests/data/raindrop_companions_raw.json').write_text(json.dumps(dict(cases=cases,chains=chains),ensure_ascii=False,indent=2),encoding='utf-8')
    print('accepted',293,'retained rejection',1,'raw chains',len(chains))


if __name__=='__main__':main()
