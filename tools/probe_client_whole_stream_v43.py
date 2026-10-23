"""Bounded independent decompression of the already prefix-verified raw stream."""
from hashlib import sha256
import json
import lzma
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.emulate_client_sections_v40 import v39

OUT=ROOT/'outputs/client_protection_v43_20260908'

if __name__=='__main__':
    evidence=json.loads((OUT/'lzma_probe.json').read_text('utf-8'))
    if not evidence['accepted']:
        raise SystemExit('No native-prefix-verified stream candidate')
    raw=v39.CLIENT.read_bytes()
    if sha256(raw).hexdigest()!=evidence['client_sha256']:
        raise SystemExit('client changed')
    pe,image=v39.checked_image(raw)
    start=evidence['source_rva'];data=image[start:start+evidence['source_bytes']]
    if sha256(data).hexdigest()!=evidence['source_sha256']:
        raise SystemExit('guest input differed from original file; not a static stream')
    accepted=evidence['accepted'][0]
    decoder=lzma.LZMADecompressor(format=lzma.FORMAT_RAW,filters=[dict(id=lzma.FILTER_LZMA1,
        dict_size=accepted['dict_size'],lc=accepted['lc'],lp=accepted['lp'],pb=accepted['pb'])])
    result=bytearray();error=None;supplied=data;limit=len(image)-0x1000
    while len(result)<limit:
        try:
            block=decoder.decompress(supplied,max_length=min(65536,limit-len(result)))
            supplied=b'';result.extend(block)
            if decoder.eof or decoder.needs_input or not block:
                break
        except lzma.LZMAError as exc:
            error=str(exc);break
    prefix=evidence['native_prefix_bytes']
    if sha256(result[:prefix]).hexdigest()!=evidence['native_prefix_sha256']:
        raise SystemExit('native prefix mismatch')
    (OUT/'raw_stream_candidate.bin').write_bytes(result)
    boundaries=[]
    for section in pe.sections:
        offset=section.VirtualAddress-0x1000;size=max(section.Misc_VirtualSize,section.SizeOfRawData)
        if offset>=0 and offset+size<=len(result):
            body=result[offset:offset+size]
            boundaries.append(dict(section=section.Name.rstrip(b'\0').decode(),hypothetical_offset=offset,size=size,
                                   first64=body[:64].hex(),sha256=sha256(body).hexdigest()))
    report=dict(client_sha256=evidence['client_sha256'],stream_rva=start,decoded_bytes=len(result),
        eof=decoder.eof,needs_input=decoder.needs_input,error=error,
        unused_input_bytes=len(decoder.unused_data) if decoder.eof else None,
        compressed_consumed=len(data)-len(decoder.unused_data) if decoder.eof else None,
        decoded_sha256=sha256(result).hexdigest(),hypothetical_contiguous_image_sections=boundaries,
        native_verified_prefix_bytes=prefix,
        caveat='Only the prefix is natively verified; contiguous PE section layout beyond .text is a hypothesis until the loader destination table is decoded.')
    (OUT/'whole_stream_probe.json').write_text(json.dumps(report,indent=2),'utf-8')
    print(json.dumps(report,indent=2))
