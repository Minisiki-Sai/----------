"""Test the observed range-decoder stream against bytes the guest REALLY wrote.

This does not replace guest execution or write a reconstructed game executable.
Candidates are accepted only after a long nonzero native-decoded prefix agrees.
"""
from hashlib import sha256
import json
import lzma
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.continue_client_v42 import OUT,restore,v39

if __name__=='__main__':
    identity=json.loads((OUT/'checkpoint_identity.json').read_text('utf-8'))
    e=restore(v39.CLIENT.read_bytes(),(OUT/'checkpoint.zip').read_bytes(),identity['sha256'])
    source_register=e.uc.reg_read(v39.x86.UC_X86_REG_R13)
    # Observed saved input base starts with 0x45 then a zero range-code prefix.
    # Test the adjacent zero-start stream; do not label 0x45 as a known property.
    source=source_register+1
    if not e.base<=source<e.base+e.size:
        raise SystemExit('Observed input cursor is not in the supplied image')
    section=next(s for s in e.pe.sections if s.VirtualAddress<=source-e.base<s.VirtualAddress+s.SizeOfRawData)
    end=e.base+section.VirtualAddress+section.SizeOfRawData
    data=bytes(e.uc.mem_read(source,end-source))
    text=next(s for s in e.pe.sections if s.Name.rstrip(b'\0')==b'.text')
    prefix_size=0
    for page in range(text.VirtualAddress,v39.align(text.VirtualAddress+text.Misc_VirtualSize),v39.PAGE):
        if e.writes.get(page,0)<v39.PAGE:
            break
        prefix_size+=v39.PAGE
    prefix_size=min(prefix_size,4*1024*1024)
    if prefix_size<65536:
        raise SystemExit('Not enough native-decoded complete text pages to validate')
    prefix=bytes(e.uc.mem_read(e.base+text.VirtualAddress,prefix_size))
    if sum(bool(x) for x in prefix)<prefix_size//4:
        raise SystemExit('Native validation prefix has insufficient content')
    trials=[];accepted=[]
    choices=[(3,0,2)]+[(lc,3-lc,pb) for lc in range(4) for pb in range(5) if (lc,3-lc,pb)!=(3,0,2)]
    for lc,lp,pb in choices:
        filters=[dict(id=lzma.FILTER_LZMA1,dict_size=32*1024*1024,lc=lc,lp=lp,pb=pb)]
        record=dict(lc=lc,lp=lp,pb=pb,dict_size=32*1024*1024)
        try:
            decoder=lzma.LZMADecompressor(format=lzma.FORMAT_RAW,filters=filters)
            result=decoder.decompress(data,max_length=1)
            for index in range(256):
                record['short_prefix_bytes']=len(result)
                record['short_prefix_hex']=result[:64].hex()
                record['short_prefix_match']=result==prefix[:len(result)]
                if not result or not record['short_prefix_match'] or decoder.eof:
                    break
                result+=decoder.decompress(b'',max_length=1)
            if not result or result!=prefix[:len(result)]:
                trials.append(record)
                continue
            result+=decoder.decompress(b'',max_length=prefix_size-len(result))
            record.update(length=len(result),prefix_match=result==prefix)
            if result==prefix:
                # Decode to the declared .text size only; do not infer the stream
                # boundary/other section ownership from this prefix match.
                result+=decoder.decompress(b'',max_length=text.Misc_VirtualSize-len(result))
                record.update(decoded_bytes=len(result),declared_text_size=text.Misc_VirtualSize,eof=decoder.eof,
                              needs_input=decoder.needs_input,decoded_sha256=sha256(result).hexdigest())
                try:
                    extra=decoder.decompress(b'',max_length=1)
                    record.update(next_output_byte=extra.hex(),eof_after_declared_size=decoder.eof,
                                  unused_input_bytes=len(decoder.unused_data) if decoder.eof else None)
                except lzma.LZMAError as exc:
                    record['boundary_error']=str(exc)
                artifact=ROOT/'outputs/client_protection_v43_20260908'
                artifact.mkdir(exist_ok=True)
                name=f'text_candidate_lc{lc}_lp{lp}_pb{pb}.bin'
                (artifact/name).write_bytes(result)
                record['artifact']=name
                accepted.append(record)
        except lzma.LZMAError as exc:
            record['error']=str(exc)
        trials.append(record)
        if accepted:
            break
    report=dict(snapshot_sha256=identity['sha256'],client_sha256=e.sha,source_rva=source-e.base,source_register_rva=source_register-e.base,
                source_bytes=len(data),source_sha256=sha256(data).hexdigest(),
                native_prefix_bytes=prefix_size,native_prefix_sha256=sha256(prefix).hexdigest(),
                trials=trials,accepted=accepted,
                caveat='Prefix equality validates this candidate against native guest writes. Full output and boundaries still require independent validation; no production changes.')
    out=ROOT/'outputs/client_protection_v43_20260908';out.mkdir(exist_ok=True)
    (out/'lzma_probe.json').write_text(json.dumps(report,indent=2),'utf-8')
    print(json.dumps(report,indent=2))
