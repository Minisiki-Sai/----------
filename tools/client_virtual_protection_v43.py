"""Logical PE page protections for an offline native-call model only.

Unicorn's original main-image mapping is an interpreter byte store. Its broad
permissions are not a Windows VirtualQuery result. Original guest protections
come from this PE's section flags; explicit modelled transitions are journalled.
No host memory protection API is called, and external guest mappings are excluded.
"""
import struct
from tools.emulate_client_sections_v40 import v39

LEGAL={1,2,4,0x10,0x20,0x40}


def original_protection(e,rva):
    if 0<=rva<v39.align(e.pe.OPTIONAL_HEADER.SizeOfHeaders):
        return 2
    for section in e.pe.sections:
        lo=section.VirtualAddress//v39.PAGE*v39.PAGE
        hi=v39.align(section.VirtualAddress+max(section.SizeOfRawData,section.Misc_VirtualSize))
        if lo<=rva<hi:
            flags=section.Characteristics
            read=bool(flags&0x40000000);write=bool(flags&0x80000000);execute=bool(flags&0x20000000)
            return (0x40 if write else 0x20 if read else 0x10) if execute else (4 if write else 2 if read else 1)
    raise ValueError('page has no PE section protection evidence')


def transition(e,address,size,new):
    if not isinstance(address,int) or not isinstance(size,int) or size<=0 or new not in LEGAL:
        raise ValueError('unsupported protection change')
    lo=address//v39.PAGE*v39.PAGE;hi=v39.align(address+size)
    if not e.base<=lo<hi<=e.base+e.size or hi-lo>32*1024*1024:
        raise ValueError('protection range is not within the guest PE')
    pages=list(range(lo-e.base,hi-e.base,v39.PAGE))
    # A genuine mapped page must exist; never create pages as a side effect.
    for page in pages:
        if not any(a<=e.base+page and e.base+page+v39.PAGE-1<=b for a,b,_ in e.uc.mem_regions()):
            raise ValueError('protection range contains an unmapped page')
        original_protection(e,page)
    old=e.page_protections.get(pages[0],original_protection(e,pages[0]))
    return lo,hi-lo,old,pages


def apply(e,args,record):
    process,baseptr,sizeptr,new,oldptr=args
    new &= 0xffffffff
    if process!=0xffffffffffffffff:
        raise ValueError('protection only applies to the offline current image')
    requested_base=e.u64(baseptr);requested_size=e.u64(sizeptr)
    lo,size,old,pages=transition(e,requested_base,requested_size,new)
    e.outputs([(baseptr,8),(sizeptr,8),(oldptr,4)])
    # Preserve interpreter byte-store permissions; maintain exact guest-visible
    # logical protections separately, including the original PE RX old value.
    e.guest_write(baseptr,struct.pack('<Q',lo))
    e.guest_write(sizeptr,struct.pack('<Q',size))
    e.guest_write(oldptr,struct.pack('<I',old))
    for page in pages:
        e.page_protections[page]=new
    record.update(handle='0x0',base=hex(lo),size=size,old_protection=old,new_protection=new,
                  source='PE section flags plus prior explicit transitions; offline byte-store permissions are separate')


def replay(e,call):
    lo,size,old,pages=transition(e,int(call['base'],16),call['size'],call['new_protection'])
    if hex(lo)!=call['base'] or size!=call['size'] or old!=call['old_protection']:
        raise ValueError('protection journal disagrees with PE/prior transitions')
    for page in pages:
        e.page_protections[page]=call['new_protection']
