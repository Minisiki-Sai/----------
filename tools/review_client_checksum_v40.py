"""Verify an observed bytewise checksum loop without skipping or patching it."""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.emulate_client_entry_v39 import checked_image, CLIENT


if __name__=='__main__':
    out=ROOT/'outputs/client_protection_v40_20260908'
    source=(out/'section_emulation_tracked.json').read_bytes()
    report=json.loads(source)
    raw=CLIENT.read_bytes()
    if sha256(raw).hexdigest()!=report['client_sha256']:
        raise ValueError('client identity mismatch')
    if any(s['name']!='.data1' for s in report['modified_sections']):
        raise ValueError('static table/loop identity requires revalidation after new writes')
    pe,image=checked_image(raw)
    base=pe.OPTIONAL_HEADER.ImageBase
    # Exact observed instruction boundary, after index masking and before lookup.
    pairs=[c for c in report['checkpoints'] if c['guest_registers']['RIP']=='0x142c07a81']
    checks=[]
    for first,second in zip(pairs,pairs[1:]):
        a={k:int(v,16) for k,v in first['guest_registers'].items()}
        b={k:int(v,16) for k,v in second['guest_registers'].items()}
        lo,hi=a['RDI']-base,b['RDI']-base
        table_rva=a['RAX']-base
        if a['RAX']!=b['RAX'] or a['R8']!=b['R8'] or a['RDI']+a['RDX']!=b['RDI']+b['RDX'] or not 0<=lo<hi<len(image) or hi-lo>8*1024*1024 or not 0<=table_rva<table_rva+1024<=len(image):
            continue
        table=struct.unpack_from('<256I',image,table_rva)
        state=a['RSI']&0xffffffff
        for value in image[lo:hi]:
            state=(state>>8)^table[(value^state)&255]^0xfb332a31
        checks.append(dict(first_segment=first['segment'],second_segment=second['segment'],
            bytes_checked=hi-lo,range_rva=[lo,hi], table_rva=table_rva,
            table_sha256=sha256(image[table_rva:table_rva+1024]).hexdigest(),
            computed_state=hex(state),observed_state=hex(b['RSI']&0xffffffff),
            matched=state==(b['RSI']&0xffffffff),
            remaining_length_consistent=a['RDX']-b['RDX']==hi-lo))
    result=dict(checkpoint_sha256=sha256(source).hexdigest(), checks=checks,
        interpretation='This replays the observed recurrence over unchanged static bytes, not an emulator fast-forward, protection patch or resource decryption.')
    (out/'checksum_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
