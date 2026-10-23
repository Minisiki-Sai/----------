"""Independent length walk of retained original UDP bytes. No runtime imports.

Closure is only a syntactic observation. A whitelist can reject unknown record
forms, and each known form still needs its independent field/semantic guards.
"""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'outputs/damage_attribution/branch0_raw_windows_20260907/raw_windows.json'
OUT=ROOT/'outputs/damage_attribution/branch0_length_framing_20260907'
KEY=bytes.fromhex('c8bfcda4943f2642fd8a723474')
SUPPLIED={(0x09,93),(0x09,98),(0x0e,31),(0x10,21),(0x12,32),(0x46,13),(0x5b,4),(0x1e,26)}


def xor(wire,phase):
    b=bytearray(wire)
    for i in range(9,len(b)):b[i]^=KEY[(i-phase)%13]
    return bytes(b)


def walk(body):
    if len(body)<14:return [],'short_envelope'
    at=14; rows=[]
    while at<len(body):
        if at+7>len(body):return rows,'short_record_header'
        size=int.from_bytes(body[at+5:at+7],'little')
        end=at+7+size
        if end>len(body):return rows,'record_overrun'
        rows.append(dict(start=at,end=end,owner=int.from_bytes(body[at:at+4],'little'),
                         opcode=body[at+4],payload_len=size,marker=at+4))
        at=end
    return rows,None if rows else 'empty_record_list'


def strict_walk(body,allowed):
    rows,error=walk(body)
    if error:return [],error
    if any((r['opcode'],r['payload_len']) not in allowed for r in rows):return [],'unknown_opcode_length'
    return rows,None


def main():
    data=json.loads(SOURCE.read_text(encoding='utf-8'))
    counts=Counter(); supplied=Counter(); pairs=Counter(); samples={}; cases=[]; closed_packets=[]
    examples=[]; benchmark_records=[]
    for c in data['cases']:
        stats=Counter(); by_fixture={}
        for key,meta in c['packets'].items():
            wire=bytes.fromhex(meta['payload_hex']);assert hashlib.sha256(wire).hexdigest()==meta['payload_sha256']
            closed=[]
            for ph in range(13):
                rows,err=walk(xor(wire,ph))
                if err is None:closed.append((ph,rows))
            by_fixture[key]=closed
            stats['udp_messages']+=1
            stats['no_closed_phase']+=not closed
            stats['unique_closed_phase']+=len(closed)==1
            stats['multiple_closed_phases']+=len(closed)>1
            allowed_closed=[(ph,rs) for ph,rs in closed if all((r['opcode'],r['payload_len']) in SUPPLIED for r in rs)]
            stats['exact_supplied8_whitelist_single_phase']+=len(allowed_closed)==1
            if len(closed)>1:
                examples.append(dict(case=c['id'],fixture=key,wire_hex=wire.hex(),closed=closed,
                                     reason='length closure alone does not select unique phase'))
            if len(closed)==1:
                ph,rows=closed[0]
                closed_packets.append(dict(case=c['id'],fixture=key,phase=ph,length=len(wire),records=rows))
                for r in rows:
                    pair=(r['opcode'],r['payload_len']);label=f'{pair[0]:02X}/{pair[1]}'
                    pairs[label]+=1
                    if pair in SUPPLIED:supplied[label]+=1
                    samples.setdefault(label,dict(case=c['id'],fixture=key,phase=ph,wire_hex=wire.hex(),record=r,
                        payload_sha256=meta['payload_sha256'],semantic_status='framing observation only'))
        regs=[]
        for r in c['registrations']:
            if r['kind']!='server09':continue
            meta=c['packets'][r['fixture']];wire=bytes.fromhex(meta['payload_hex']);body=xor(wire,r['phase'])
            assert body.hex()==r['decoded_hex']
            rows,error=walk(body)
            exact=[v for v in rows if v['marker']==r['m']]
            assert error is None and len(exact)==1
            found=exact[0]
            assert found['owner']==r['owner'] and found['opcode']==9
            phases=[ph for ph,rs in by_fixture[r['fixture']] if any(v['marker']==r['m'] and v['owner']==r['owner'] and v['opcode']==9 for v in rs)]
            assert phases==[r['phase']]
            stats['server_registrations_checked']+=1
            stats['server_registration_boundary_matches']+=1
            regs.append(dict(fixture=r['fixture'],object_id=r['obj'],template=r['template'],phase=r['phase'],record=found))
        selected_keys={v for v in ('f119740_m0','f4632_m0','f135469_m0','f135633_m0','f120016_m0','f4861_m0','f135639_m0') if v in c['packets']}
        for key in selected_keys:
            meta=c['packets'][key];assert len(by_fixture[key])==1
            ph,rows=by_fixture[key][0]
            benchmark_records.append(dict(case=c['id'],fixture=key,wire_hex=meta['payload_hex'],phase=ph,
                                          records=rows,length=meta['length'],payload_sha256=meta['payload_sha256']))
        counts.update(stats);cases.append(dict(case=c['id'],counts=dict(stats),registration_records=regs))
    # Negative controls derived from original messages: they are labelled
    # mutations, never promoted to real packet examples or real field proof.
    negative=[];limits=[]
    for fixture in [r for r in benchmark_records if r['length'] in (119,224)]:
        body=xor(bytes.fromhex(fixture['wire_hex']),fixture['phase']);allowed={(r['opcode'],r['payload_len']) for r in fixture['records']}
        checks=[('truncated_byte',body[:-1]),('trailing_byte',body+b'\0')]
        for value in (0,1,97,99,0xffff):
            b=bytearray(body);b[19:21]=value.to_bytes(2,'little');checks.append((f'wrong_payload_length_{value}',bytes(b)))
        for label,b in checks:
            rs,err=strict_walk(b,allowed);assert err is not None,(fixture['fixture'],label)
            negative.append(dict(source=fixture['fixture'],mutation=label,synthetic=True,strict_rejection=err))
        b=bytearray(body);b[18]=0xfe
        rs,err=walk(bytes(b));assert err is None
        _,blocked=strict_walk(bytes(b),allowed);assert blocked=='unknown_opcode_length'
        negative.append(dict(source=fixture['fixture'],mutation='unknown_opcode_same_valid_length',synthetic=True,
                             framing_closes=True,strict_rejection=blocked))
        b=bytearray(body);b[34:37]=b'\x09\x62\0'
        rs,err=walk(bytes(b));assert err is None
        assert [v['start'] for v in rs]==[v['start'] for v in fixture['records']]
        negative.append(dict(source=fixture['fixture'],mutation='fake_marker_inside_opaque',synthetic=True,
                             framing_closes=True,no_extra_record=True))
        # Framing is not a checksum: cutting a WHOLE record can still close.
        if len(body)==224:
            rs,err=walk(body[:119]);assert err is None and len(rs)==1
            limits.append(dict(source=fixture['fixture'],mutation='remove_complete_last_record',synthetic=True,
                               framing_closes=True,requires='independent outer-message envelope/length integrity'))
        # Explicit LE16, not an opcode+low-byte heuristic. Synthetic only.
    le=bytearray(14+7+256);le[18]=0xfe;le[19:21]=b'\0\1'
    rs,err=walk(bytes(le));assert err is None and rs[0]['payload_len']==256
    negative.append(dict(mutation='synthetic_le16_256_positive_framing',synthetic=True,framing_closes=True,
                         strict_rejection=strict_walk(bytes(le),SUPPLIED)[1]))
    out=dict(source=str(SOURCE.relative_to(ROOT)),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
             grammar='offset14; [owner:u32LE][opcode:u8][payload_len:u16LE][payload]; end=start+7+length; require exact closure',
             scope='3 complete retained raw windows, all13 phases; no capture reopen, runtime imports or changes',
             counts=dict(counts),cases=cases,unique_phase_closed_pairs=dict(pairs),supplied8_pair_counts=dict(supplied),
             pair_examples=samples,closed_packets=closed_packets,real_boundary_fixtures=benchmark_records,
             negative_tests=negative,framing_limits=limits,multiple_phase_examples=examples,
             warning='Observed closure of an opcode/length pair does not decode its semantics or automatically whitelist it. 0966 remains framing-only.')
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(counts=dict(counts),pairs=dict(pairs),supplied8=dict(supplied),negative_tests=len(negative),limits=len(limits)),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
