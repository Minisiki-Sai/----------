"""Reproduce the exact root-key copy predicates using hash-pinned static bytes.

This evidence audit distinguishes local and remote player virtual methods.
It neither executes client code nor changes attribution.  It records the
root-copy and parent-copy branches so a wire replay can use their actual
preconditions instead of treating every last observed action as an object key.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.review_client_damage_types_v44 import BASE, CodeReview, StaticView

OUT = ROOT / "outputs/projectile_root_skill_v60_20260909"
INVENTORY = ROOT / "outputs/damage_object_skill_getter_v56_20260909/raw_inventory.json"
WINDOWS = {
    "root_copy_full_method": (0x378A30, 0x378F00),
    "child_attach_and_parent_copy": (0xC7238F, 0xC72406),
    "root_attach_call": (0xC720A0, 0xC720E0),
    "projectile_key_setter": (0xA94C50, 0xA94C57),
    "projectile_key_getter": (0xA91640, 0xA91647),
}
ANCHORS = {
    0x378E83: "mov rcx, qword ptr [rsi + 0x120]",
    0x378E8A: "cmp dword ptr [rcx + 0xe94], 9",
    0x378E91: "jg 0x140378ee4",
    0x378E96: "call qword ptr [rax + 0x70]",
    0x378E9B: "je 0x140378ee4",
    0x378EA7: "call qword ptr [rax + 0x308]",
    0x378EAF: "je 0x140378ee4",
    0x378EB1: "test r12, r12",
    0x378EB6: "lea rcx, [rsi + 0x10]",
    0x378EBA: "call 0x1403743e0",
    0x378EC8: "call 0x1402aa360",
    0x378ECD: "lea rcx, [rax + 0x20]",
    0x378ED4: "call qword ptr [rax + 0x10]",
    0x378ED7: "mov r8, qword ptr [r12]",
    0x378EDB: "mov edx, eax",
    0x378EDD: "mov rcx, r12",
    0x378EE0: "call qword ptr [r8 + 0x28]",
    0xC72397: "lea rcx, [rax + 0x6a0]",
    0xC723A4: "call qword ptr [rax + 0x30]",
    0xC723F6: "mov edx, dword ptr [rsi - 0x10]",
    0xC723FB: "jle 0x140c72406",
    0xC72403: "call qword ptr [rax + 0x28]",
}


def main() -> None:
    view = StaticView()
    code = CodeReview(view)
    raw_inventory = INVENTORY.read_bytes()
    inventory = json.loads(raw_inventory)
    if inventory["client_sha256"] != sha256(view.raw).hexdigest():
        raise ValueError("RTTI inventory belongs to another client")
    windows = {}
    for label, (begin, end) in WINDOWS.items():
        raw = view.read(begin, end - begin)
        rows = [code.row(i) for i in code.dis.disasm(raw, BASE + begin)]
        windows[label] = {"begin": begin, "end": end,
                          "sha256": sha256(raw).hexdigest(), "instructions": rows}
    lookup = {r["rva"]: f'{r["mnemonic"]} {r["operands"]}'.strip()
              for w in windows.values() for r in w["instructions"]}
    for rva, expected in ANCHORS.items():
        if lookup.get(rva) != expected:
            raise ValueError(f"instruction contract changed at {rva:#x}")
    if struct.unpack("<Q", view.read(0xE9E788 + 0x30, 8))[0] != BASE + 0x378A30:
        raise ValueError("MASkillUser virtual method changed")
    if view.read(0x9E940, 3) != bytes.fromhex("b001c3"):
        raise ValueError("true virtual method changed")
    if view.read(0x41E90, 3) != bytes.fromhex("32c0c3"):
        raise ValueError("false virtual method changed")
    if view.read(0xA94C50, 7) != bytes.fromhex("8991c01b0000c3"):
        raise ValueError("Projectile setter changed")
    predicates = []
    for cls in ("CDnActor", "CDnPlayerActor", "CDnLocalPlayerActor", "CDnMonsterActor"):
        tables = [t for t in inventory["vtables"]
                  if t["type"] == cls and t["subobject_offset"] == 0]
        if len(tables) != 1:
            raise ValueError(f"not one primary vtable: {cls}")
        table = tables[0]
        slots = {}
        for offset in (0x70, 0x308):
            target = struct.unpack("<Q", view.read(table["table_rva"] + offset, 8))[0] - BASE
            slots[hex(offset)] = {"target": hex(target),
                                 "constant": True if target == 0x9E940 else
                                 False if target == 0x41E90 else None}
        predicates.append({"class": cls, "vtable": hex(table["table_rva"]),
                           "slots": slots,
                           "both_virtual_gates_constant_true": all(
                               s["constant"] is True for s in slots.values())})
    report = {
        "schema": "projectile-root-skill-v60", "client_sha256": sha256(view.raw).hexdigest(),
        "inputs": {**view.inputs, str(INVENTORY): sha256(raw_inventory).hexdigest()},
        "instruction_assertions": len(ANCHORS), "windows": windows,
        "virtual_gate_evidence": predicates,
        "root_rule": "valid local player; actor+E94<=9; nonnull new Projectile; valid current action pair; copy action RowId into new object's +1BC0",
        "parent_rule": "after root attach, positive parent key overrides the child key through the same setter",
        "remote_player_rule": "remote CDnPlayerActor +308 returns false; this root-current-action copy is skipped",
        "wire_prerequisites": ["exact current action lifecycle", "exact new object index and generation",
                               "verified local/remote actor identity", "parent identity where present"],
        "production_changed": False, "new_confirmed_hits": 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 攻击对象的根技能写入：本机与队友分支", "",
             "客户端 MASkillUser 虚表 +0x30 指向 0x378A30。函数结尾读取当前动作的 SkillTable RowId，调用新攻击对象的 +0x28 setter，写入对象 +0x1BC0。",
             "", "该分支同时检查角色虚表 +0x70 与 +0x308。两个条件只有本机玩家类均固定为真；远端玩家 +0x308 固定为假。", "",
             "|角色类|+0x70|+0x308|这条根技能复制分支|", "|---|---|---|---|"]
    for p in predicates:
        vals = [str(p["slots"][hex(o)]["constant"]) for o in (0x70, 0x308)]
        lines.append(f'|{p["class"]}|{vals[0]}|{vals[1]}|{"还需其他条件" if p["both_virtual_gates_constant_true"] else "不满足"}|')
    lines += ["", "子对象创建后，0xC723F6–0xC72403 会再复制父对象的正技能键。因此子攻击可以跨越玩家后续动作，不能按 Hit 到达时的最后动作覆盖。", "",
              "连锁闪电专项的含义：需区分记录者自身和远端雷神。找到了内置统计的写入位置，不等于远端对象已在网络中传输了同样的技能键。缺少出生或当前动作状态时，仍不能升级为确定归因。", "",
              f"已校验 {len(ANCHORS)} 处关键指令、RTTI 主虚表及 true/false/setter 机器码；本报告新增确认 Hit 为 0，没有修改生产归因。", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output": str(OUT), "assertions": len(ANCHORS),
                      "local_only_root_copy": True, "new_confirmed_hits": 0}))


if __name__ == "__main__":
    main()
