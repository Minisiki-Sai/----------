"""Equivalent interpreter for one byte-signature-verified checksum loop.

Every input byte is still checksummed; no check/result/branch is patched. The
last iteration runs as original guest machine code to preserve flags, scratch
stack bytes and scratch registers. All other guest code uses normal emulation.
"""
from hashlib import sha256
import struct

from tools.emulate_client_sections_v40 import v39

ANCHOR_RVA=0x2c6d0fb
EXIT_RVA=0x2ce9492
LOOP_BLOCKS={
    0x2c6d0fb:bytes.fromhex('41bba40d8431460fb69c5fb8e4f79ce968a9f9ff'),
    0x2c07a77:bytes.fromhex('4433de4181e3ff00000068b42403f848ff4424000f895d500500468b1c980f859a421200'),
    0x2d2bd35:bytes.fromhex('c07c2406a6c1ee08f75c24014133f3e8fe88e2ff'),
    0x2b54647:bytes.fromhex('48ffc748c74424001d6837b066c1642409ee0f9b44240881f6312a33fb682b93918280642411ac48ffcae9827a1000'),
    0x2c5c0f8:bytes.fromhex('e98ad30800'),
    0x2ce9487:bytes.fromhex('488d6424180f85693cf8ff'),
}
MAX_LENGTH=32*1024*1024


class ChecksumController:
    def __init__(self,experiment,rva_shift=0):
        self.e=experiment
        self.shift=rva_shift
        self.records=[]
        self.rejections={}
        self.semantic_iterations=0
        address=experiment.base+ANCHOR_RVA+rva_shift
        if not experiment.base<=address<experiment.base+experiment.size:
            raise ValueError('checksum anchor outside guest image')
        self.hook=experiment.uc.hook_add(v39.unicorn.UC_HOOK_CODE,self.on_code,begin=address,end=address)

    def reject(self,reason):
        self.rejections[reason]=self.rejections.get(reason,0)+1
        return False

    def attempt(self):
        e=self.e;uc=e.uc
        read=lambda name:uc.reg_read(getattr(v39.x86,'UC_X86_REG_'+name))
        if read('RIP')!=e.base+ANCHOR_RVA+self.shift:
            return self.reject('not_at_verified_loop_entry')
        length=read('RDX')
        if not 2<=length<=MAX_LENGTH:
            return self.reject('outside_acceleration_length')
        if len(self.records)>=4096:
            return self.reject('model_record_budget')
        source,table_address,sp=read('RDI'),read('RAX'),read('RSP')
        if not e.base<=source<source+length<=e.base+e.size or not e.base<=table_address<table_address+1024<=e.base+e.size:
            return self.reject('input_or_table_not_wholly_in_image')
        if not v39.STACK_BASE<=sp-24<sp<=v39.STACK_BASE+v39.STACK_SIZE:
            return self.reject('scratch_stack_outside_synthetic_stack')
        # Byte-exact proof includes the obfuscated dummy-stack instructions and
        # both conditional branches; it is not a mnemonic/name/length heuristic.
        for rva,expected in LOOP_BLOCKS.items():
            address=e.base+rva+self.shift
            if not e.base<=address<address+len(expected)<=e.base+e.size or bytes(uc.mem_read(address,len(expected)))!=expected:
                return self.reject('loop_byte_signature_changed')
        table_bytes=bytes(uc.mem_read(table_address,1024))
        table=struct.unpack('<256I',table_bytes)
        data=bytes(uc.mem_read(source,length-1))
        original=read('RSI')
        state=original&0xffffffff
        for value in data:
            state=(state>>8)^table[(value^state)&255]^0xfb332a31
        # No success value is forced. Original last iteration and ALL subsequent
        # comparison/failure paths remain untouched in the guest instruction flow.
        uc.reg_write(v39.x86.UC_X86_REG_RSI,state)
        uc.reg_write(v39.x86.UC_X86_REG_RDI,source+length-1)
        uc.reg_write(v39.x86.UC_X86_REG_RDX,1)
        self.semantic_iterations+=length-1
        record=dict(start_instruction=e.count,source_rva=source-e.base,length=length,
            evaluated_bytes=length-1,initial_state=hex(original),before_last_state=hex(state),
            source_sha256=sha256(data).hexdigest(),table_sha256=sha256(table_bytes).hexdigest())
        self.records.append(record)
        return True

    def on_code(self,uc,address,size,user):
        self.attempt()

    def report(self):
        return dict(evaluated_iterations=self.semantic_iterations,records=self.records,rejections=self.rejections,
            semantics='Exact checksum recurrence; original last iteration and comparison paths still execute. Evaluated iterations are NOT counted as native instructions.')


def install_checksum_accelerator(experiment,rva_shift=0):
    return ChecksumController(experiment,rva_shift)
