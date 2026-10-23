"""Replay a prior offline checkpoint to the first text byte, observing metadata."""
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.continue_client_v42 import OUT,restore,encode,v39
from tools.client_checksum_model_v41 import install_checksum_accelerator
from tools.client_file_checksum_v42 import FileChecksumController

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--metadata-body',action='store_true');args=parser.parse_args()
    digest='4f35b611a65533d5d2eccbe2a7cc7e28e68a223559458b4603141b8d04afc5b0'
    e=restore(v39.CLIENT.read_bytes(),(OUT/'previous_resumable.zip').read_bytes(),digest)
    a=install_checksum_accelerator(e);b=FileChecksumController(e)
    records=[]
    seen=set()
    def read(uc,access,address,size,value,user):
        if args.metadata_body and size not in (4,8):return
        if len(records)>=(4096 if args.metadata_body else 256):return
        ip=uc.reg_read(v39.x86.UC_X86_REG_RIP)
        key=(address,size,ip)
        if args.metadata_body and key in seen:return
        seen.add(key)
        ins=next(e.dis.disasm(bytes(uc.mem_read(ip,15)),ip,count=1),None)
        tail=[]
        for rva,width in list(e.tail)[-12:]:
            i=next(e.dis.disasm(bytes(uc.mem_read(e.base+rva,width)),e.base+rva,count=1),None)
            tail.append(dict(rva=rva,instruction=(i.mnemonic+' '+i.op_str) if i else None))
        records.append(dict(rva=address-e.base,size=size,value=bytes(uc.mem_read(address,size)).hex(),
            instruction=(ins.mnemonic+' '+ins.op_str) if ins else None,instruction_rva=ip-e.base,tail=tail))
    def write(uc,access,address,size,value,user):
        e.stop('first_text_decode_write',address=hex(address),size=size,value=hex(value))
    e.uc.hook_add(v39.unicorn.UC_HOOK_MEM_READ,read,
        begin=e.base+(0x1b58000 if args.metadata_body else 0x240c000),
        end=e.base+(0x2e66bff if args.metadata_body else 0x240c38a))
    e.uc.hook_add(v39.unicorn.UC_HOOK_MEM_WRITE,write,begin=e.base+0x1000,end=e.base+0xe6cffb)
    report=e.run(resume=True)
    report.update(metadata_reads=records,checksum_model=a.report(),file_checksum_model=b.report())
    output=ROOT/'outputs/client_protection_v43_20260908'
    blob=encode(e)
    stem='first_text_body' if args.metadata_body else 'first_text'
    (output/(stem+'_snapshot.zip')).write_bytes(blob)
    report['snapshot_sha256']=sha256(blob).hexdigest()
    report['stack_qwords']=[dict(offset=i,value=hex(e.u64(e.uc.reg_read(v39.x86.UC_X86_REG_RSP)+i))) for i in range(0,0x200,8)]
    (output/(stem+'_trace.json')).write_text(json.dumps(report,indent=2),'utf-8')
    print(json.dumps(dict(stop=report['stop'],instructions=e.count,metadata_reads=len(records),snapshot_sha256=report['snapshot_sha256'])))
