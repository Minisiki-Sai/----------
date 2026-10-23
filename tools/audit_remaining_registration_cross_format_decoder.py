"""Closed, format-based DIAGNOSTIC candidates found in cross-job raw scans.

Not imported by runtime. New non-FFFF reference and type3 parent branches are
opt-in. Resource suffix FFFF is ALWAYS rejected here. 127/130 are not admitted:
no real fixtures were found. No template/skill-specific decision exists.
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.audit_remaining_registration_packet_decoder import u32
from dragon_nest_builder.live_dps.protocol_v576 import _decode_v576_body

# Only real, observed format/offset/phase combinations.
FORMATS={120:(10,12,'resource',13),112:(10,11,'parent3',0),
         125:(10,11,'parent3',13),132:(17,5,'parent3',13),135:(20,8,'parent3',13)}


def decode_cross_format(payload, *, known_templates=None, allow_parent_type3=False,
                        allow_non_ff_reference=False):
    fmt=FORMATS.get(len(payload))
    if fmt is None:return None
    o,expected_phase,kind,extra=fmt
    if kind=='parent3' and not allow_parent_type3:return None
    found=[]
    for phase in range(13):
        b=_decode_v576_body(payload,phase);tail=b[o:]
        owner,obj,tpl=u32(tail,0),u32(tail,9),u32(tail,13)
        if (not 0<owner<0xffffffff or owner!=u32(tail,45)
            or tail[4:9]!=bytes(5) or not 0<obj<=0xffffff
            or not 0<tpl<=0x7fffffff):continue
        if known_templates is not None and tpl not in known_templates:continue
        parent=None;reference=None;reference_kind=None
        if kind=='resource':
            word=u32(tail,49);resource=word&0xffff;sequence=word>>16
            if tail[53:61]!=b'\xff'*8 or not 0<resource<65535 or u32(tail,78)!=resource:continue
            suffix=resource;reference=u32(tail,70)
        else:
            parent=u32(tail,50);branch=u32(tail,54)
            if (tail[49]!=0 or not 0<parent<=0xffffff or parent==obj
                or branch&0xffff!=3 or tail[58:66]!=b'\xff'*8):continue
            suffix=u32(tail,70+extra);sequence=branch>>16;resource=None
            if not 0<suffix<65535:continue
            if extra:reference=u32(tail,75)
        if reference is not None:
            if reference==0xffffffff:reference_kind='FFFFFFFF'
            elif not allow_non_ff_reference:continue
            elif reference==owner:reference_kind='exact_owner_reference'
            elif 0<reference<=0xffffff:reference_kind='bounded_entity_reference_candidate'
            else:continue
        found.append(dict(length=len(payload),offset=o,phase=phase,kind=kind,extra_bytes=extra,
                          owner=owner,obj=obj,template=tpl,parent_id=parent,sequence=sequence,
                          resource_head=resource,resource_suffix=suffix,
                          opaque_reference=reference,reference_kind=reference_kind,
                          normalized_tail_hex=tail.hex()))
    if len(found)!=1 or found[0]['phase']!=expected_phase:return None
    return found[0]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reports',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();sys.stdout.reconfigure(encoding='utf8')
    counts=Counter();negative=Counter();seeds={};fixtures=[]
    for path in sorted(a.reports.glob('*.json')):
        doc=json.loads(path.read_text(encoding='utf8'))
        for r in doc.get('rows',[]):
            payload=bytes.fromhex(r['wire_hex'])
            candidate=decode_cross_format(payload,allow_parent_type3=True,allow_non_ff_reference=True)
            if candidate is None:continue
            assert (candidate['owner'],candidate['obj'],candidate['template'])==(r['owner'],r['obj'],r['template'])
            key=f"{path.stem}:L{r['length']}:T{r['template']}:{candidate['reference_kind']}"
            counts[key]+=1
            mode=(candidate['length'],candidate['kind'],candidate['reference_kind'])
            seeds.setdefault(mode,(r,payload,candidate,path.stem))
    for mode,(r,payload,c,name) in seeds.items():
        o=c['offset'];phase=c['phase'];b=_decode_v576_body(payload,phase)
        tests=[('five_zero',o+4),('owner_repeat',o+45),('8FF',o+(53 if c['kind']=='resource' else 58))]
        if c['kind']=='resource':tests.append(('resource_repeat',o+78))
        else:tests.extend([('parent_tag',o+49),('parent_type3',o+54)])
        for label,at in tests:
            bad=bytearray(b);bad[at]^=1
            assert decode_cross_format(_decode_v576_body(bytes(bad),phase),allow_parent_type3=True,allow_non_ff_reference=True) is None
            negative[label]+=1
        suffix=o+(78 if c['kind']=='resource' else 70+c['extra_bytes'])
        bad=bytearray(b);bad[suffix:suffix+4]=(65535).to_bytes(4,'little')
        assert decode_cross_format(_decode_v576_body(bytes(bad),phase),allow_parent_type3=True,allow_non_ff_reference=True) is None
        negative['FFFF_suffix_rejected']+=1
        assert decode_cross_format(payload+b'\0',allow_parent_type3=True,allow_non_ff_reference=True) is None
        assert decode_cross_format(_decode_v576_body(b,(phase+1)%13),allow_parent_type3=True,allow_non_ff_reference=True) is None
        negative['length_and_phase']+=1
        if c['kind']=='parent3':assert decode_cross_format(payload,allow_non_ff_reference=True) is None
        if c['reference_kind'] not in (None,'FFFFFFFF'):
            assert decode_cross_format(payload,allow_parent_type3=True) is None
        fixtures.append(dict(capture=name,p=r['p'],wire_hex=payload.hex(),expected=c))
    result=dict(policy='observed closed formats; resourceFFFF never admitted; parent/nonFFFF reference opt-in; identities only, no skill assignment',
                accepted_counts=counts,negative_tests=negative,fixtures=fixtures,
                unobserved_formats_not_admitted=[127,130])
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(accepted_counts=counts,negative_tests=negative,fixture_count=len(fixtures)),ensure_ascii=False))


if __name__=='__main__':main()
