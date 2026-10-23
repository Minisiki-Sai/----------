"""Inspect bounded byte windows in a pinned offline guest, not live memory."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.continue_client_v42 import OUT,restore,v39

if __name__=='__main__':
    identity=json.loads((OUT/'checkpoint_identity.json').read_text('utf-8'))
    e=restore(v39.CLIENT.read_bytes(),(OUT/'checkpoint.zip').read_bytes(),identity['sha256'])
    report=dict(snapshot=identity['sha256'],instructions=e.count,register_windows={})
    for name in ('R9','R13','R15','RSP'):
        value=e.uc.reg_read(getattr(v39.x86,'UC_X86_REG_'+name))
        if e.base+32<=value<e.base+e.size-128 or v39.STACK_BASE+32<=value<v39.STACK_BASE+v39.STACK_SIZE-128 or any(a['base']+32<=value<a['base']+a['size']-128 for a in e.allocations):
            report['register_windows'][name]=dict(address=hex(value),before32_and_after128=bytes(e.uc.mem_read(value-32,160)).hex())
    report['text_first128']=bytes(e.uc.mem_read(e.base+0x1000,128)).hex()
    report['guest_allocations']=e.allocations
    print(json.dumps(report,indent=2))
