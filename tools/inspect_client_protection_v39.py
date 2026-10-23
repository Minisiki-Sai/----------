"""Static PE and existing crash-file module metadata; never executes game code.

No process handles, DLL loading, token/heap scanning or player-data exports.
Only module headers/section extent metadata from the saved dump are examined.
"""
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.tmp/reverse_tools_v39'))
import capstone
import pefile

CLIENT=Path('D:/DragonNest/Reborn/dragonnest_reborn.exe')
DUMP=Path('D:/DragonNest/Reborn/DN501_DX11_2026_07_04-23_37_04_44.dmp')
OUT=ROOT/'outputs/client_protection_v39_20260908'


def pe_static():
    raw=CLIENT.read_bytes()
    pe=pefile.PE(data=raw)
    base=pe.OPTIONAL_HEADER.ImageBase
    entry=pe.OPTIONAL_HEADER.AddressOfEntryPoint
    dis=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
    def decode(rva,count=80):
        section=pe.get_section_by_rva(rva)
        if section is None or rva-section.VirtualAddress>=section.SizeOfRawData:
            return []
        body=pe.get_data(rva,min(512,section.VirtualAddress+section.SizeOfRawData-rva))
        return [dict(rva=i.address-base,size=i.size,bytes=i.bytes.hex(),op=i.mnemonic,args=i.op_str)
                for i in dis.disasm(body,base+rva,count=count)]
    points=[entry]
    if pe.get_data(entry,1)==b'\xe8':
        target=entry+5+struct.unpack('<i',pe.get_data(entry+1,4))[0]
        points.append(target)
    imports=[]
    for mod in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
        name=mod.dll.decode('ascii')
        imports.append(dict(module=name,functions=[dict(name=i.name.decode('ascii') if i.name else None,
            ordinal=i.ordinal,rva=i.address-base) for i in mod.imports]))
    return dict(sha256=sha256(raw).hexdigest(),size=len(raw),timestamp=pe.FILE_HEADER.TimeDateStamp,
        image_size=pe.OPTIONAL_HEADER.SizeOfImage,entry_rva=entry,
        code_windows={hex(p):decode(p) for p in points},imports=imports,
        disassembler_version=capstone.__version__,executed=False)


class SavedDump:
    def __init__(self,path):
        self.file=path.open('rb');self.size=path.stat().st_size
        header=self.read(0,32)
        sig,version,count,directory,check,ts,flags=struct.unpack('<IIIIIIQ',header)
        assert sig==0x504d444d and count<100
        self.streams=[struct.unpack('<III',self.read(directory+i*12,12)) for i in range(count)]
        self.ranges=[];self.modules=[]
        for kind,size,offset in self.streams:
            if kind==4:
                n=struct.unpack('<I',self.read(offset,4))[0]
                assert n<1000 and 4+n*108==size
                for i in range(n):
                    row=self.read(offset+4+i*108,108)
                    base,span,checksum,timestamp,nr=struct.unpack_from('<QIIII',row)
                    length=struct.unpack('<I',self.read(nr,4))[0]
                    assert length<4096
                    name=self.read(nr+4,length).decode('utf-16-le').replace('\\','/').split('/')[-1]
                    self.modules.append(dict(name=name,base=base,size=span,timestamp=timestamp))
            elif kind==5:
                n=struct.unpack('<I',self.read(offset,4))[0]
                assert n<100000 and 4+n*16==size
                for i in range(n):
                    start,length,rva=struct.unpack('<QII',self.read(offset+4+i*16,16))
                    assert rva+length<=self.size
                    self.ranges.append((start,start+length,rva))
            elif kind==9:
                n,rva=struct.unpack('<QQ',self.read(offset,16))
                assert n<100000 and 16+n*16==size
                for i in range(n):
                    start,length=struct.unpack('<QQ',self.read(offset+16+i*16,16))
                    assert rva+length<=self.size
                    self.ranges.append((start,start+length,rva));rva+=length
        self.ranges.sort()

    def read(self,offset,length):
        assert 0<=offset<=self.size and 0<=length<=self.size-offset
        self.file.seek(offset);raw=self.file.read(length)
        assert len(raw)==length
        return raw

    def module_read(self,module,offset,length):
        assert 0<=offset<module['size'] and 0<=length<=module['size']-offset
        start=module['base']+offset;end=start+length
        parts=[]
        while start<end:
            choices=[r for r in self.ranges if r[0]<=start<r[1]]
            if len(choices)!=1:return None
            lo,hi,rva=choices[0];n=min(end,hi)-start
            parts.append(self.read(rva+start-lo,n));start+=n
        return b''.join(parts)

    def module_metadata(self,name):
        matches=[m for m in self.modules if m['name'].lower()==name.lower()]
        assert len(matches)==1
        m=matches[0];end=m['base']+m['size']
        spans=[dict(module_rva=max(lo,m['base'])-m['base'],size=min(hi,end)-max(lo,m['base']))
               for lo,hi,rva in self.ranges if lo<end and hi>m['base']]
        result=dict(**m,saved_spans=spans,saved_bytes=sum(r['size'] for r in spans))
        dos=self.module_read(m,0,64)
        result['dos_header_available']=bool(dos and dos[:2]==b'MZ')
        if not result['dos_header_available']:return result
        nt=struct.unpack_from('<I',dos,60)[0]
        fixed=self.module_read(m,nt,24)
        assert fixed and fixed[:4]==b'PE\0\0'
        machine,n,ts,pointer,symbols,optlen,flags=struct.unpack_from('<HHIIIHH',fixed,4)
        assert 0<n<100 and optlen<1024
        optional=self.module_read(m,nt+24,optlen)
        assert optional
        sections=[]
        for i in range(n):
            s=self.module_read(m,nt+24+optlen+i*40,40)
            assert s
            label=s[:8].rstrip(b'\0').decode('ascii',errors='replace')
            virtual,rva,rawsize,rawoff=struct.unpack_from('<IIII',s,8)
            present=sum(max(0,min(rva+virtual,x['module_rva']+x['size'])-max(rva,x['module_rva'])) for x in spans)
            sections.append(dict(name=label,rva=rva,virtual_size=virtual,disk_raw_size=rawsize,disk_raw_offset=rawoff,saved_bytes=present))
        result.update(machine=machine,header_timestamp=ts,sections=sections)
        return result


if __name__=='__main__':
    pe=pe_static();saved=SavedDump(DUMP)
    try:md=saved.module_metadata(CLIENT.name)
    finally:saved.file.close()
    result=dict(current_pe=pe,old_dump_main_module=md,
        same_build_metadata=pe['timestamp']==md['timestamp'] and pe['image_size']==md['size'],
        scope='Read-only static instruction decoding and saved module metadata. No live process, execution, DLL loading or heap/player-data search.')
    OUT.mkdir(exist_ok=True)
    (OUT/'static_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(dict(entry_windows=pe['code_windows'],crypto_imports=[x for x in pe['imports'] if 'crypto' in x['module'].lower()],dump=md,same_build=result['same_build_metadata']),ensure_ascii=False))
