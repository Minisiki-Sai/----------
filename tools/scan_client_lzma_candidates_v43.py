"""Bounded search for the experimentally observed 45 00 stream prefix.

Decoded lengths are candidate destination clues, not an ownership certificate.
Never constructs or executes an unpacked executable.
"""
from hashlib import sha256
import argparse
import json
import lzma
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.emulate_client_sections_v40 import v39
OUT=ROOT/'outputs/client_protection_v43_20260908'

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--range-prefix',action='store_true');args=parser.parse_args()
    evidence=json.loads((OUT/'whole_stream_probe.json').read_text('utf-8'))
    raw=v39.CLIENT.read_bytes()
    if sha256(raw).hexdigest()!=evidence['client_sha256']:
        raise SystemExit('changed client')
    pe,image=v39.checked_image(raw)
    section=next(s for s in pe.sections if s.Name.rstrip(b'\0')==b'.data2')
    body=image[section.VirtualAddress:section.VirtualAddress+section.SizeOfRawData]
    excluded=(evidence['stream_rva']-1,evidence['stream_rva']+evidence['compressed_consumed'])
    positions=[];cursor=0
    while True:
        at=body.find(b'\x00' if args.range_prefix else b'\x45\x00',cursor)
        if at<0:break
        cursor=at+1;rva=section.VirtualAddress+at
        source=at if args.range_prefix else at+1
        if args.range_prefix and (source+5>len(body) or body[source+1]>=128 or body[source:source+5]==bytes(5)):
            continue
        if not excluded[0]<=rva<excluded[1]:positions.append(section.VirtualAddress+source)
    if len(positions)>(100000 if args.range_prefix else 1024):
        raise SystemExit('candidate count exceeds narrow prefix budget')
    accepted=[];rejected=0
    for index,start in enumerate(positions):
        decoder=lzma.LZMADecompressor(format=lzma.FORMAT_RAW,filters=[dict(id=lzma.FILTER_LZMA1,dict_size=32*1024*1024,lc=3,lp=0,pb=2)])
        data=image[start:section.VirtualAddress+section.SizeOfRawData]
        result=bytearray();supplied=data
        try:
            while len(result)<32*1024*1024:
                block=decoder.decompress(supplied,max_length=min(65536,32*1024*1024-len(result)))
                supplied=b'';result.extend(block)
                if decoder.eof or decoder.needs_input or not block:break
        except lzma.LZMAError:
            rejected+=1;continue
        if not decoder.eof or len(result)<128:
            rejected+=1;continue
        matches=[]
        for s in pe.sections:
            n=max(s.Misc_VirtualSize,s.SizeOfRawData)
            if len(result) in (n,(n+3)//4*4,(n+15)//16*16,v39.align(n)):
                matches.append(s.Name.rstrip(b'\0').decode())
        record=dict(stream_rva=start,decoded_bytes=len(result),compressed_consumed=len(data)-len(decoder.unused_data),
                    decoded_sha256=sha256(result).hexdigest(),section_length_candidates=matches,first64=result[:64].hex(),
                    artifact=f'stream_{start:08x}.bin')
        (OUT/record['artifact']).write_bytes(result)
        accepted.append(record)
        print(json.dumps(dict(found=record),ensure_ascii=False),flush=True)
    report=dict(client_sha256=evidence['client_sha256'],prefix='zero plus legal initial literal range (excluding all-zero code)' if args.range_prefix else '45 00',candidates=len(positions),rejected=rejected,
                streams=accepted,caveat='Valid LZMA EOS and matching output lengths do not prove loader destination; require table/native cross-check.')
    (OUT/('stream_range_candidates.json' if args.range_prefix else 'stream_candidates.json')).write_text(json.dumps(report,indent=2),'utf-8')
    print(json.dumps(report,indent=2))
