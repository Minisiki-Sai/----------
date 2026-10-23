"""Export five already retained real payload windows into standalone test data."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'outputs/audit_remaining_registration_3021_context_20260907'
DEST=ROOT/'tests/data/damage_object_captured_parent_chain.json'


def main():
    fx=json.loads((RAW/'fixtures_final.json').read_text(encoding='utf-8'))
    records=fx['records'];cases=[]
    for child,shape in ((102046,'client112'),(102941,'client112'),
                        (104072,'server224'),(102317,'server224'),(102043,'server152')):
        p=next(h for h in fx['final_parent_evidence'] if h['child_obj']==child)
        h=next(h for h in fx['expected_hits'] if h['obj']==child and h['fixture']==p['hit_fixture'])
        pt=records[p['parent_fixture']]['t'];ht=records[p['hit_fixture']]['t']
        casts=[c for c in fx['expected_casts'] if (c['skill_id']==2353 and c['epoch'] in p['root_action_epochs'])
               or (c['skill_id']==2207 and pt<c['t']<ht)]
        assert sum(c['skill_id']==2353 for c in casts)==1
        keys={p['parent_fixture']:'parent_registration',p['child_fixture']:'child_registration',p['hit_fixture']:'hit'}
        keys.update({c['fixture']:f'real_action_{c["skill_id"]}' for c in casts})
        packets=[]
        for key,role in keys.items():
            r=records[key];wire=bytes.fromhex(r['payload_hex'])
            assert hashlib.sha256(wire).hexdigest()==r['payload_sha256']
            packets.append(dict(fixture_id=key,role=role,wire_hex=r['payload_hex'],
                payload_sha256=r['payload_sha256'],timestamp=r['t'],direction=r['direction'],
                original_frame=r['frame'],original_file_offset=r['file_offset'],phase=r['phase']))
        packets.sort(key=lambda r:(r['timestamp'],r['original_frame']))
        b=bytes.fromhex(records[p['hit_fixture']]['decoded_hex']);m=h['record_offset']
        assert b[m:m+3]==b'\x08\x73\0'
        u32=lambda at:int.from_bytes(b[at:at+4],'little')
        assert (u32(m+8),u32(m+13),u32(m-4),u32(m+4))==(h['owner'],child,h['target'],h['result_group'])
        assert int.from_bytes(b[m+19:m+27],'little')==h['damage']
        assert u32(m+102)==2207 or u32(m+114)==2207
        cases.append(dict(name=f'{shape}_{child}',packets=packets,
            expected=dict(owner=h['owner'],target=h['target'],damage=h['damage'],critical=bool(b[m+35]&1),
                result_group=h['result_group'],object_id=child,parent_id=p['evidence']['parent_object_id_candidate'],
                parent_template=3023,child_template=3021,skill_id=2353,fixed_packet_skill=2207,
                hit_record_offset=m),
            provenance=dict(parent_generation_in_original_scan=p['parent_generation'],
                root_action_epoch_in_original_scan=p['root_action_epochs'][0],
                note='Original full-capture generations/epochs are provenance only, not expected counters in a reduced replay.')))
    result=dict(capture='0905黑龙经典冰灵.dpscap',capture_sha256=fx['capture_sha256'],
                capture_origin_timestamp=1788595471.266909,
                protocol_payloads_are_unmodified=True,
                envelope_note='Only test IP/UDP/Ethernet envelopes are synthetic; no game payload, action or damage byte is rewritten.',
                cases=cases)
    if DEST.exists():raise SystemExit('Refusing to overwrite test data')
    DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(cases=len(cases),packets=sum(len(c['packets']) for c in cases),
                         sha256=hashlib.sha256(DEST.read_bytes()).hexdigest()),ensure_ascii=False))


if __name__=='__main__':main()
