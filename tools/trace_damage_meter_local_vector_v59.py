"""Trace every static access to the damage-meter local 0x28-byte key vector.

This is a hash-pinned, read-only client audit.  It never loads or executes the
client.  The output is an evidence sidecar used to understand how the built-in
damage meter remembers a SkillTable RowId after the player has started another
action.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.review_client_damage_types_v44 import BASE, StaticView
import capstone
from capstone import x86_const as x86


OUT = ROOT / "outputs/chain_lightning_meter_v59_20260909"
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
FOCUS_DISPLACEMENTS = {0x650, 0x658, 0x660, 0x668, 0x670, 0x678}
METER_FUNCTION = 0x6A52E0


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _instruction_row(ins: capstone.CsInsn, start: int, end: int) -> dict[str, object]:
    memory = []
    for index, operand in enumerate(ins.operands):
        if operand.type != x86.X86_OP_MEM:
            continue
        access = []
        if operand.access & capstone.CS_AC_READ:
            access.append("read")
        if operand.access & capstone.CS_AC_WRITE:
            access.append("write")
        memory.append({
            "operand_index": index,
            "base": ins.reg_name(operand.mem.base) if operand.mem.base else None,
            "index": ins.reg_name(operand.mem.index) if operand.mem.index else None,
            "scale": operand.mem.scale,
            "displacement": operand.mem.disp,
            "access": access or ["unspecified"],
        })
    return {
        "function_start_rva": start,
        "function_end_rva": end,
        "rva": int(ins.address - BASE),
        "bytes": ins.bytes.hex(),
        "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
        "memory": memory,
    }


def _disassemble_function(view: StaticView, start: int, end: int) -> list[capstone.CsInsn]:
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    return list(decoder.disasm(view.read(start, end - start), BASE + start))


def _bounded_function(view: StaticView, start: int, end: int) -> list[dict[str, object]]:
    return [_instruction_row(ins, start, end) for ins in _disassemble_function(view, start, end)]


def main() -> None:
    if _digest(CLIENT) != EXPECTED:
        raise ValueError("client hash mismatch")
    view = StaticView()
    accesses: list[dict[str, object]] = []
    functions_examined = 0
    bytes_examined = 0
    for start, end, _unwind in view.functions:
        section = view.section(start)
        if section is None or section["name"] != ".text":
            continue
        if end > section["rva"] + section["virtual_size"]:
            continue
        try:
            instructions = _disassemble_function(view, start, end)
        except ValueError:
            continue
        functions_examined += 1
        bytes_examined += end - start
        for ins in instructions:
            for operand in ins.operands:
                if operand.type == x86.X86_OP_MEM and abs(operand.mem.disp) in FOCUS_DISPLACEMENTS:
                    accesses.append(_instruction_row(ins, start, end))
                    break

    related_functions = sorted({int(row["function_start_rva"]) for row in accesses})
    contexts = {}
    for start in related_functions:
        interval = view.function(start)
        if interval is None:
            continue
        rows = _bounded_function(view, interval[0], interval[1])
        focus_indexes = [
            i for i, row in enumerate(rows)
            if any(abs(int(mem["displacement"])) in FOCUS_DISPLACEMENTS for mem in row["memory"])
        ]
        selected = set()
        for index in focus_indexes:
            selected.update(range(max(0, index - 12), min(len(rows), index + 17)))
        contexts[hex(start)] = [rows[i] for i in sorted(selected)]

    meter_interval = view.function(METER_FUNCTION)
    meter_rows = []
    if meter_interval:
        meter_rows = _bounded_function(view, meter_interval[0], meter_interval[1])

    report = {
        "schema": "damage-meter-local-vector-v59",
        "scope": "static hash-pinned decoded client; no execution and no production mutation",
        "inputs": {
            "client": str(CLIENT),
            "client_sha256": EXPECTED,
            "decoded_segment_manifest_sha256": view.inputs.get(str(ROOT / "outputs/client_protection_v44_20260908/segments_manifest.json")),
        },
        "scan": {
            "functions_examined": functions_examined,
            "bytes_examined": bytes_examined,
            "focus_displacements": [hex(value) for value in sorted(FOCUS_DISPLACEMENTS)],
            "access_count": len(accesses),
            "function_count": len(related_functions),
            "instructions_by_displacement": dict(sorted(Counter(
                hex(abs(int(mem["displacement"])))
                for row in accesses for mem in row["memory"]
                if abs(int(mem["displacement"])) in FOCUS_DISPLACEMENTS
            ).items())),
        },
        "accesses": accesses,
        "contexts": contexts,
        "meter_function": {
            "requested_rva": hex(METER_FUNCTION),
            "interval": [hex(value) for value in meter_interval[:2]] if meter_interval else None,
            "instructions": meter_rows,
        },
        "limitations": [
            "A matching numeric displacement can belong to an unrelated class; object identity must be established from call graphs or constructor context.",
            "Static access discovery alone does not prove which network field populates a record.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "meter_local_vector_accesses.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(target), "scan": report["scan"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
