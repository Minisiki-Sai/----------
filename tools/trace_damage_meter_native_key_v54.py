"""Hash-pinned byte-only trace of the client damage meter's native key.

The report keeps short instruction windows around accesses to damage-result
``+0x90`` and ``+0xA4`` plus direct calls in those windows.  It never loads or
executes the game client and does not change production attribution code.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.review_client_damage_types_v44 import BASE, CodeReview, StaticView
from capstone import CS_GRP_CALL
from capstone import x86_const as x86


OUT = ROOT / "outputs/client_protection_v44_20260908"
EXPECTED_CLIENT = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
FUNCTIONS = (0x529380, 0x6A52E0, 0x99571C, 0x995E70, 0xAB7400)
FOCUS_OFFSETS = {0x20, 0x28, 0x30, 0x76, 0x90, 0x94, 0xA0, 0xA4}


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _ins_row(ins) -> dict[str, object]:
    direct_target = None
    if ins.group(CS_GRP_CALL) and ins.operands and ins.operands[0].type == x86.X86_OP_IMM:
        direct_target = int(ins.operands[0].imm - BASE)
    memory = []
    for operand in ins.operands:
        if operand.type != x86.X86_OP_MEM:
            continue
        memory.append(
            {
                "base": ins.reg_name(operand.mem.base),
                "index": ins.reg_name(operand.mem.index),
                "scale": int(operand.mem.scale),
                "displacement": int(operand.mem.disp),
                "focus": int(operand.mem.disp) in FOCUS_OFFSETS,
            }
        )
    return {
        "rva": int(ins.address - BASE),
        "bytes": ins.bytes.hex(),
        "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
        "direct_call_target_rva": direct_target,
        "memory": memory,
    }


def _trace_function(view: StaticView, requested_rva: int) -> dict[str, object]:
    found = view.function(requested_rva)
    if found is None:
        raise ValueError(f"no function for {requested_rva:#x}")
    rows = CodeReview(view).decode(found)
    focus_indices = []
    for index, ins in enumerate(rows):
        if any(
            operand.type == x86.X86_OP_MEM
            and int(operand.mem.disp) in FOCUS_OFFSETS
            for operand in ins.operands
        ):
            focus_indices.append(index)
    keep = set()
    for index in focus_indices:
        keep.update(range(max(0, index - 16), min(len(rows), index + 17)))
    # The meter is large.  Preserve its prologue and direct calls near each
    # field access without serialising thousands of unrelated instructions.
    keep.update(range(min(32, len(rows))))
    if requested_rva == 0xAB7400:
        keep.update(range(len(rows)))
    selected = [_ins_row(rows[index]) for index in sorted(keep)]
    return {
        "requested_rva": requested_rva,
        "begin_rva": int(found[0]),
        "end_rva": int(found[1]),
        "unwind_rva": int(found[2]),
        "instruction_count": len(rows),
        "focus_access_count": len(focus_indices),
        "selected_instructions": selected,
    }


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# 客户端内置伤害统计原生键追踪 v54",
        "",
        f"客户端 SHA-256：`{report['client_sha256']}`。",
        "",
        "只展示伤害结果关键字段附近的指令；地址均为 RVA。",
    ]
    for function in report["functions"]:
        lines += [
            "",
            f"## `{function['begin_rva']:#x}`–`{function['end_rva']:#x}`",
            "",
            "```text",
        ]
        for row in function["selected_instructions"]:
            lines.append(f"{row['rva']:08x}  {row['bytes']:<24}  {row['instruction']}")
        lines.append("```")
    lines += [
        "",
        "本报告只是静态数据流证据；只有与真实抓包固定字段和对象代次回放一致后，才允许进入生产解析器。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    view = StaticView()
    client_path = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
    if _digest(client_path) != EXPECTED_CLIENT:
        raise ValueError("client hash changed")
    report = {
        "schema": "damage-meter-native-key-v54",
        "method": "static decoded client bytes only; no execution; no production mutation",
        "client_path": str(client_path),
        "client_sha256": EXPECTED_CLIENT,
        "focus_offsets": sorted(FOCUS_OFFSETS),
        "functions": [_trace_function(view, rva) for rva in FUNCTIONS],
        "production_changed": False,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "damage_meter_native_key_v54.json"
    md_path = OUT / "damage_meter_native_key_v54.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
