"""Observe loader descriptor/property reads in an existing isolated guest.

Uses unchanged, regression-tested guest contracts and checksum equivalents.
Does not substitute a decompressor, patch a check, or change any checkpoint.
Stops at the first native .text write, with no host API forwarding.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.continue_client_v42 import OUT as SNAPSHOTS, restore, v39
from tools.client_checksum_model_v41 import install_checksum_accelerator
from tools.client_file_checksum_v42 import FileChecksumController

OUT = ROOT / "outputs/client_protection_v44_20260908"
SNAPSHOT_SHA = "4f35b611a65533d5d2eccbe2a7cc7e28e68a223559458b4603141b8d04afc5b0"


def main():
    raw = v39.CLIENT.read_bytes()
    e = restore(raw,(SNAPSHOTS / "previous_resumable.zip").read_bytes(),SNAPSHOT_SHA)
    initial_instructions = e.count
    first = install_checksum_accelerator(e)
    second = FileChecksumController(e)
    observations = []
    budget_reached = []

    def record_read(uc,access,address,size,value,label):
        if len(observations) >= 256:
            if not budget_reached:
                budget_reached.append(True)
            return
        ip = uc.reg_read(v39.x86.UC_X86_REG_RIP)
        instruction = next(e.dis.disasm(bytes(uc.mem_read(ip,15)),ip,count=1),None)
        registers = {name:hex(uc.reg_read(getattr(v39.x86,"UC_X86_REG_"+name)))
                     for name in ("RCX","RDX","R8","R9","R13","R15","RSP")}
        observations.append(dict(label=label,rva=address-e.base,bytes=size,
            value=bytes(uc.mem_read(address,size)).hex(),instruction_rva=ip-e.base,
            instruction=(instruction.mnemonic+" "+instruction.op_str) if instruction else None,
            registers=registers,native_instruction=e.count))

    for label,rva,size in (("stream_descriptor_table",0x236902C,56),
                           ("lzma_properties_candidate",0x23B1686,5),
                           ("text_stream_start",0x240C386,5)):
        e.uc.hook_add(v39.unicorn.UC_HOOK_MEM_READ,record_read,user_data=label,
            begin=e.base+rva,end=e.base+rva+size-1)

    def stop_write(uc,access,address,size,value,user):
        e.stop("first_text_decode_write",address=hex(address),size=size,value=hex(value))

    e.uc.hook_add(v39.unicorn.UC_HOOK_MEM_WRITE,stop_write,
        begin=e.base+0x1000,end=e.base+0xE6CFFF)
    report = e.run(resume=True)
    report.update(input_snapshot_sha256=SNAPSHOT_SHA,
        input_client_sha256=sha256(raw).hexdigest(),
        script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        instructions_this_replay=e.count-initial_instructions,
        observations=observations,observation_budget_reached=bool(budget_reached),
        checksum_model=first.report(),file_checksum_model=second.report(),
        note="Actual original-instruction property/descriptor reads in isolated guest; no complete decoder replacement, no new checkpoint, no production parser change.")
    if sha256(v39.CLIENT.read_bytes()).hexdigest() != sha256(raw).hexdigest():
        raise ValueError("client changed during isolated replay")
    (OUT / "properties_trace.json").write_text(json.dumps(report,indent=2),"utf-8")
    print(json.dumps(dict(stop=report["stop"],instructions_this_replay=report["instructions_this_replay"],
        observations=observations,observation_budget_reached=bool(budget_reached)),indent=2))


if __name__ == "__main__":
    main()
