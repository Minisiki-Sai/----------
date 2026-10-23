"""Exact interpreter for the separately observed raw-file checksum loop.

No check is bypassed: n-1 real bytes are evaluated, then the original last
iteration and all original success/failure decisions execute in Unicorn.
"""
from hashlib import sha256
import struct
from tools.emulate_client_sections_v40 import v39

ANCHOR_RVA=47209704
EXIT_RVA=44531047
LOOP_BLOCKS={
    47209704:bytes.fromhex('bb86ef1675410fb69c1a7a10e98ae99c5fe8ff'),
    45661335:bytes.fromhex('6881ef03cd688787aa2f33dffe4c240781e3ff000000c0742405a10f98442403418b1c9948ff4c240868b7719c3f0f843a131f00'),
    45661387:bytes.fromhex('c1ef08807424008d33fbe91a221a00'),
    47374068:bytes.fromhex('49ffc281f7a15714e7680bba332b48ffcae97309edff'),
    46131325:bytes.fromhex('e9d594e7ff'),
    44531031:bytes.fromhex('682ebf303b488d6424280f8581df2800'),
}


class FileChecksumController:
    def __init__(self,e,rva_shift=0):
        self.e=e;self.shift=rva_shift;self.records=[];self.rejections={};self.semantic_iterations=0
        address=e.base+ANCHOR_RVA+rva_shift
        if not e.base<=address<e.base+e.size:
            raise ValueError('file checksum anchor outside image')
        self.hook=e.uc.hook_add(v39.unicorn.UC_HOOK_CODE,self.on_code,begin=address,end=address)

    def reject(self,why):
        self.rejections[why]=self.rejections.get(why,0)+1
        return False

    def attempt(self):
        e=self.e;uc=e.uc
        read=lambda name:uc.reg_read(getattr(v39.x86,'UC_X86_REG_'+name))
        if read('RIP')!=e.base+ANCHOR_RVA+self.shift:
            return self.reject('not_at_anchor')
        n=read('RDX');source=read('R10');tableptr=read('R9');sp=read('RSP')
        if not 2<=n<=32*1024*1024 or len(self.records)>=1024:
            return self.reject('length_or_record_budget')
        in_image=e.base<=source<source+n<=e.base+e.size
        in_file=any(not v.get('unmapped',False) and v['base']<=source<source+n<=v['base']+len(e.file_bytes)
                    and v['source_sha256']==e.sha for v in getattr(e,'file_views',[]))
        if not (in_image or in_file) or not e.base<=tableptr<tableptr+1024<=e.base+e.size:
            return self.reject('unknown_input_or_table')
        if not v39.STACK_BASE<=sp-40<sp<=v39.STACK_BASE+v39.STACK_SIZE:
            return self.reject('scratch_stack_bounds')
        for rva,body in LOOP_BLOCKS.items():
            address=e.base+rva+self.shift
            if not e.base<=address<address+len(body)<=e.base+e.size or bytes(uc.mem_read(address,len(body)))!=body:
                return self.reject('loop_signature_changed')
        data=bytes(uc.mem_read(source,n-1))
        table_bytes=bytes(uc.mem_read(tableptr,1024));table=struct.unpack('<256I',table_bytes)
        initial=read('RDI');state=initial&0xffffffff
        for value in data:
            state=(state>>8)^table[(value^state)&255]^0xe71457a1
        uc.reg_write(v39.x86.UC_X86_REG_RDI,state)
        uc.reg_write(v39.x86.UC_X86_REG_R10,source+n-1)
        uc.reg_write(v39.x86.UC_X86_REG_RDX,1)
        self.semantic_iterations+=n-1
        self.records.append(dict(start_instruction=e.count,source=hex(source),length=n,
            evaluated_bytes=n-1,initial_state=hex(initial),before_last_state=hex(state),
            source_sha256=sha256(data).hexdigest(),table_sha256=sha256(table_bytes).hexdigest()))
        return True

    def on_code(self,uc,address,size,user):
        self.attempt()

    def report(self):
        return dict(evaluated_iterations=self.semantic_iterations,records=self.records,rejections=self.rejections,
                    original_last_iteration_and_comparison_preserved=True)
