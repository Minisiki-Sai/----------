"""Hash-pinned static trace of CDnProjectile's native meter key.

The client damage meter calls a related object's virtual slot +0x30 when the
damage result has no positive key.  CDnProjectile implements that slot as a
getter for dword +0x1BC0.  This tool finds every instruction-aligned access to
that field in original exception-directory functions and records bounded
context.  It never executes or modifies the client.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/client_protection_v44_20260908"
JSON_OUT = OUT / "projectile_meter_key_v51.json"
MD_OUT = OUT / "projectile_meter_key_v51.md"
EXPECTED_CLIENT = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
FIELD_OFFSET = 0x1BC0
GETTER_RVA = 0xA91640
SETTER_RVA = 0xA94C50

sys.path.insert(0, str(ROOT))
from tools.review_client_damage_types_v44 import BASE, StaticView  # noqa: E402
sys.path.insert(0, str(ROOT / ".tmp/reverse_tools_v39"))
import capstone  # noqa: E402
from capstone import x86_const as x86  # noqa: E402


def _row(ins: Any) -> dict[str, Any]:
    return {
        "rva": ins.address - BASE,
        "bytes": ins.bytes.hex(),
        "mnemonic": ins.mnemonic,
        "operands": ins.op_str,
    }


def _memory_access(ins: Any) -> tuple[bool, bool, list[str]]:
    reads = False
    writes = False
    bases: list[str] = []
    for operand in ins.operands:
        if operand.type != x86.X86_OP_MEM or int(operand.mem.disp) != FIELD_OFFSET:
            continue
        bases.append(ins.reg_name(operand.mem.base) if operand.mem.base else "")
        access = int(getattr(operand, "access", 0) or 0)
        reads = reads or bool(access & capstone.CS_AC_READ)
        writes = writes or bool(access & capstone.CS_AC_WRITE)
        # Older capstone builds may omit access metadata.  The first memory
        # operand of MOV is its destination; CMP/TEST never write it.
        if not access:
            if ins.mnemonic.startswith(("cmp", "test")):
                reads = True
            elif ins.mnemonic.startswith("mov") and ins.operands[0] is operand:
                writes = True
            else:
                reads = True
    return reads, writes, bases


def main() -> int:
    view = StaticView()
    if sha256(view.raw).hexdigest() != EXPECTED_CLIENT:
        raise ValueError("client hash mismatch")
    dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    dis.detail = True
    hits: list[dict[str, Any]] = []
    decoded_functions = 0
    decoded_bytes = 0
    for start, end, unwind in view.functions:
        size = end - start
        if size <= 0 or size > 0x20000:
            continue
        raw = view.read(start, size)
        instructions = list(dis.disasm(raw, BASE + start))
        if not instructions:
            continue
        decoded_functions += 1
        decoded_bytes += size
        for index, ins in enumerate(instructions):
            reads, writes, bases = _memory_access(ins)
            if not bases:
                continue
            context = instructions[max(0, index - 7) : min(len(instructions), index + 9)]
            hits.append(
                {
                    "function_begin_rva": start,
                    "function_end_rva": end,
                    "unwind_rva": unwind,
                    "function_bytes_sha256": sha256(raw).hexdigest(),
                    "access": _row(ins),
                    "reads": reads,
                    "writes": writes,
                    "base_registers": bases,
                    "context": [_row(item) for item in context],
                }
            )

    getter = view.read(GETTER_RVA, 7)
    setter = view.read(SETTER_RVA, 7)
    if getter != bytes.fromhex("8b81c01b0000c3"):
        raise ValueError("CDnProjectile +0x30 getter bytes changed")
    if setter != bytes.fromhex("8991c01b0000c3"):
        raise ValueError("CDnProjectile +0x28 setter bytes changed")
    counts = Counter(
        "read_write" if row["reads"] and row["writes"] else
        "write" if row["writes"] else "read"
        for row in hits
    )
    report = {
        "schema": "projectile-native-meter-key-v51",
        "client_sha256": EXPECTED_CLIENT,
        "method": "offline static instruction-aligned scan; no client execution",
        "proven_fact": {
            "class": "CDnProjectile",
            "vtable_rva": 0xF28730,
            "slot": 6,
            "slot_byte_offset": 0x30,
            "getter_rva": GETTER_RVA,
            "getter_bytes": getter.hex(),
            "setter_slot": 5,
            "setter_slot_byte_offset": 0x28,
            "setter_rva": SETTER_RVA,
            "setter_bytes": setter.hex(),
            "field_offset": FIELD_OFFSET,
        },
        "summary": {
            "runtime_functions": len(view.functions),
            "decoded_functions": decoded_functions,
            "decoded_bytes": decoded_bytes,
            "field_accesses": len(hits),
            "access_kinds": dict(counts),
        },
        "accesses": hits,
        "production_changed": False,
    }
    JSON_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# CDnProjectile 原生技能键静态追踪（v51）",
        "",
        f"客户端 SHA-256：`{EXPECTED_CLIENT}`。",
        "",
        "已确认 `CDnProjectile` 主 vtable 槽 5/6（字节偏移 `+0x28/+0x30`）分别调用 "
        f"`{SETTER_RVA:#x}` / `{GETTER_RVA:#x}`。前者以机器码 `{setter.hex()}` 写入对象 "
        f"dword `+0x1BC0`，后者以机器码 `{getter.hex()}` 读取同一字段。",
        "",
        f"扫描 {decoded_functions} 个原始异常目录函数，共找到 {len(hits)} 个对 `+0x1BC0` 的指令级访问：{dict(counts)}。",
        "",
        "|函数|访问位置|读/写|指令|",
        "|---:|---:|---|---|",
    ]
    for row in hits:
        kind = "读写" if row["reads"] and row["writes"] else "写" if row["writes"] else "读"
        access = row["access"]
        lines.append(
            f"|`{row['function_begin_rva']:#x}`|`{access['rva']:#x}`|{kind}|"
            f"`{access['mnemonic']} {access['operands']}`|"
        )
    lines += [
        "",
        "这些访问是后续复现客户端对象状态机的候选写入点；只有能从抓包字段或已确认的创建链复现的写入才会进入生产解码。",
    ]
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
