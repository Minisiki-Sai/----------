"""Static identity-field/caller audit of hash-pinned decoded client bytes.

No client execution, process access, PE modification or production parser edit.
Matching displacement is a lead, not proof of a CDnProjectile receiver.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import struct
import sys
from hashlib import sha256

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.review_client_damage_types_v44 import StaticView, BASE, capstone, x86, CodeReview
from tools.review_client_code_links_v44 import reachable_rows

OUT = ROOT / "outputs/projectile_evidence_v45_20260909"
FIELD = 0x1BC0
METHODS = {0xA94C50:"projectile_slot5_setter",0xA91640:"projectile_slot6_getter",
           0xA8D190:"projectile_constructor",0xA90370:"projectile_stream_constructor"}


def raw_transfers(data, start, targets):
    """Overlapping byte prefilter; instruction alignment is verified later."""
    for match in re.finditer(rb"(?=([\xe8\xe9][\x00-\xff]{4}))", data):
        at = start + match.start()
        target = at + 5 + struct.unpack_from("<i", match[1], 1)[0]
        if target in targets:
            yield at, target


def candidate_ranges(view):
    start, data = view.segments[".text"]
    patterns = set()
    cursor = 0
    needle = struct.pack("<I",FIELD)
    while True:
        at = data.find(needle,cursor)
        if at < 0:
            break
        cursor = at+1
        function = view.function(start+at)
        if function:
            patterns.add(tuple(function))
    calls = []
    for at, target in raw_transfers(data, start, METHODS):
        function = view.function(at)
        calls.append(dict(rva=at,target=target,runtime_function=function))
        if function:
            patterns.add(tuple(function))
    if len(patterns)>256:
        raise ValueError("candidate function budget")
    return sorted(patterns),calls


def get_string(view,rva):
    section = view.section(rva)
    if not section or section["name"] not in (".rdata",".data"):
        return None
    length = min(160,section["rva"]+section["virtual_size"]-rva)
    try:
        data = view.read(rva,length)
    except ValueError:
        return None
    ascii_run = re.match(rb"[\x20-\x7e]{5,}\x00",data)
    if ascii_run:
        return ascii_run.group()[:-1].decode("ascii")
    wide = re.match(rb"(?:[\x20-\x7e]\x00){5,}\x00\x00",data)
    return wide.group()[:-2].decode("utf-16-le") if wide else None


def inspect(view,function):
    start,end,unwind = function
    if end-start>128*1024:
        raise ValueError("individual function budget")
    raw = view.read(start,end-start)
    decoder = capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
    decoder.detail = True
    instructions = list(decoder.disasm(raw,BASE+start))
    rows = [CodeReview.row(ins) for ins in instructions]
    lite = [(i.address-BASE,i.size,i.mnemonic,i.op_str) for i in instructions]
    # Reachability helper expects all direct branch targets expressed as RVAs.
    for n,ins in enumerate(instructions):
        if ins.operands and ins.operands[0].type == x86.X86_OP_IMM and (
            ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)):
            lite[n]=(ins.address-BASE,ins.size,ins.mnemonic,hex(ins.operands[0].imm-BASE))
    reached,unresolved=reachable_rows(lite,start,end)
    references=[];targets=[];strings=[]
    for n,ins in enumerate(instructions):
        row=rows[n]
        if ins.address-BASE not in reached:
            continue
        memories=[op for op in ins.operands if op.type==x86.X86_OP_MEM]
        if any(op.mem.disp==FIELD and op.mem.base!=x86.X86_REG_RIP for op in memories):
            references.append(dict(kind="same_numeric_displacement_receiver_unproved",instruction=row,
                memory=[dict(base=ins.reg_name(op.mem.base),index=ins.reg_name(op.mem.index),
                    scale=op.mem.scale,width=op.size,access=op.access) for op in memories if op.mem.disp==FIELD],
                context=rows[max(0,n-14):n+15]))
        if row.get("direct_branch_target") in METHODS:
            references.append(dict(kind="direct_typed_method_transfer",instruction=row,
                target_label=METHODS[row["direct_branch_target"]],context=rows[max(0,n-14):n+15]))
        if row.get("direct_branch_target") is not None and ins.mnemonic in ("call","jmp"):
            targets.append(dict(instruction_rva=row["rva"],target_rva=row["direct_branch_target"],
                kind=ins.mnemonic,target_range=view.function(row["direct_branch_target"])))
        for target in row.get("rip_relative_targets",()):
            text=get_string(view,target)
            if text:
                strings.append(dict(instruction_rva=row["rva"],string_rva=target,text=text))
    return dict(begin=start,end=end,unwind=unwind,code_sha256=sha256(raw).hexdigest(),
        decoded_bytes=sum(i.size for i in instructions),available_bytes=len(raw),
        unresolved_internal_targets=unresolved,reachable_instructions=len(reached),
        references=references,transfers=targets,strings=strings,instructions=rows)


def leaf_window(view, rva):
    """Read at a proven transfer target; never invent exception boundaries."""
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    raw = view.read(rva, 96)
    rows = []
    for ins in decoder.disasm(raw, BASE + rva):
        rows.append(CodeReview.row(ins))
        if ins.mnemonic in ("ret", "jmp", "int3", "ud2"):
            break
    return dict(rva=rva, window_bytes=96, window_sha256=sha256(raw).hexdigest(),
                known_exception_range=view.function(rva), instructions=rows,
                boundary="bounded linear window at exact transfer target, not a proven whole function")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--extra-rva",type=lambda s:int(s,0),action="append",default=[])
    parser.add_argument("--leaf-rva",type=lambda s:int(s,0),action="append",default=[])
    parser.add_argument("--report",default="identity.json")
    args=parser.parse_args()
    if len(args.extra_rva)>12 or len(args.leaf_rva)>12 or not re.fullmatch(r"identity[A-Za-z0-9_-]*\.json",args.report):
        raise ValueError("bounded arguments required")
    view=StaticView()
    functions,prefilter_calls=candidate_ranges(view)
    selected=set(functions)
    for rva in args.extra_rva:
        function=view.function(rva)
        if not function:
            raise ValueError(f"no known exception range: {rva:#x}")
        selected.add(tuple(function))
    if sum(end-start for start,end,_ in selected)>4*1024*1024:
        raise ValueError("total code budget")
    records=[inspect(view,function) for function in sorted(selected)]
    leaves=[leaf_window(view,rva) for rva in args.leaf_rva]
    changed=[path for path,h in view.inputs.items() if sha256(Path(path).read_bytes()).hexdigest()!=h]
    if changed:
        raise ValueError("inputs changed")
    report=dict(client_sha256=sha256(view.raw).hexdigest(),inputs=view.inputs,
        script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),field=FIELD,
        accessor_methods={hex(k):v for k,v in METHODS.items()},
        raw_call_candidates=prefilter_calls,extra_rvas=args.extra_rva,functions=records,
        leaf_windows=leaves,
        summary=dict(candidate_ranges=len(functions),inspected_ranges=len(records),
            references=sum(len(r["references"]) for r in records),
            ranges_with_refs=sum(bool(r["references"]) for r in records)),
        caveats=["Equal offsets can be unrelated object types; receiver and value flow must be proven.",
            "Exception ranges are original metadata, not all semantic function boundaries.",
            "Direct CFG reachability is not runtime execution or indirect call resolution.",
            "No object/skill/owner wire mapping or attribution certificate is generated."])
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/args.report).write_text(json.dumps(report,indent=2),"utf-8")
    print(json.dumps(report["summary"],indent=2))
    for item in records:
        if item["references"]:
            print(hex(item["begin"]),len(item["references"]),json.dumps(item["strings"][:3]))


if __name__=="__main__":
    main()
