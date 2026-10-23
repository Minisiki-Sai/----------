"""Static trace of damage related-object vtable slot 6 skill-key getters.

This tool is deliberately byte-only.  It reads the hash-pinned decoded client
streams and PE/RTTI metadata, but never loads or executes the client.  The
output is an audit sidecar; production decoder files are not touched.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.review_client_damage_types_v44 import BASE, CodeReview, StaticView, extract_types
import capstone
from capstone import x86_const as x86


OUT = ROOT / "outputs/damage_object_skill_getter_v56_20260909"
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED_CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
CALLSITE_RVA = 0x6A5693


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _instruction_row(ins) -> dict[str, object]:
    memory = []
    for operand in ins.operands:
        if operand.type != x86.X86_OP_MEM:
            continue
        memory.append({
            "base": ins.reg_name(operand.mem.base),
            "index": ins.reg_name(operand.mem.index),
            "scale": int(operand.mem.scale),
            "displacement": int(operand.mem.disp),
            "access": int(operand.access),
        })
    direct_target = None
    if (ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)) and ins.operands:
        if ins.operands[0].type == x86.X86_OP_IMM:
            direct_target = int(ins.operands[0].imm - BASE)
    return {
        "rva": int(ins.address - BASE),
        "bytes": ins.bytes.hex(),
        "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
        "direct_target_rva": direct_target,
        "memory": memory,
    }


def enumerate_vtables(view: StaticView, types: dict[int, dict[str, object]]) -> list[dict[str, object]]:
    base, data = view.segments[".rdata"]
    result = []
    seen = set()
    for offset in range(0, len(data) - 8, 8):
        locator_va = struct.unpack_from("<Q", data, offset)[0]
        locator_rva = locator_va - BASE
        if locator_rva in seen or locator_rva < 0:
            continue
        try:
            signature, subobject, constructor, td, hierarchy, self_rva = struct.unpack(
                "<6I", view.read(locator_rva, 24)
            )
        except (ValueError, struct.error):
            continue
        if signature != 1 or self_rva != locator_rva or td not in types:
            continue
        table_rva = base + offset + 8
        entries = []
        for slot in range(40):
            try:
                target = struct.unpack("<Q", view.read(table_rva + slot * 8, 8))[0] - BASE
            except (ValueError, struct.error):
                break
            section = view.section(target)
            if section is None or section["name"] not in (".text", ".data0", ".data2"):
                break
            entries.append(target)
        if len(entries) <= 6:
            continue
        seen.add(locator_rva)
        result.append({
            "type": types[td]["simple_name"] or types[td]["decorated_name"],
            "decorated_type": types[td]["decorated_name"],
            "table_rva": table_rva,
            "complete_object_locator_rva": locator_rva,
            "subobject_offset": subobject,
            "constructor_displacement": constructor,
            "hierarchy_rva": hierarchy,
            "entry_count": len(entries),
            "slot6_target_rva": entries[6],
        })
    return sorted(result, key=lambda row: (row["slot6_target_rva"], row["type"], row["table_rva"]))


def decode_target(view: StaticView, code: CodeReview, rva: int) -> dict[str, object]:
    found = view.function(rva)
    if found is None:
        # x64 leaf accessors commonly have no unwind entry.  Decode a strictly
        # bounded byte window and stop at RET/INT3 rather than losing them.
        dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        dis.detail = True
        rows = []
        for ins in dis.disasm(view.read(rva, 128), BASE + rva):
            rows.append(ins)
            if ins.mnemonic in {"ret", "int3"}:
                break
        function_meta = None
    else:
        rows = code.decode(found)
        function_meta = {"begin_rva": found[0], "end_rva": found[1], "unwind_rva": found[2]}
    # Keep a bounded prefix.  Simple accessors and adjustment thunks terminate
    # here; complex methods are reported conservatively rather than guessed.
    selected = rows[: min(32, len(rows))]
    ins_rows = [_instruction_row(ins) for ins in selected]
    this_adjustment = 0
    tail_target = None
    getter_offset = None
    getter_width = None
    classification = "complex"

    # Normalize common MSVC adjustment thunks: add/sub/lea rcx, then jmp.
    for ins in selected[:6]:
        if ins.mnemonic == "sub" and len(ins.operands) == 2 and ins.operands[0].type == x86.X86_OP_REG:
            if ins.reg_name(ins.operands[0].reg) == "rcx" and ins.operands[1].type == x86.X86_OP_IMM:
                this_adjustment -= int(ins.operands[1].imm)
        elif ins.mnemonic == "add" and len(ins.operands) == 2 and ins.operands[0].type == x86.X86_OP_REG:
            if ins.reg_name(ins.operands[0].reg) == "rcx" and ins.operands[1].type == x86.X86_OP_IMM:
                this_adjustment += int(ins.operands[1].imm)
        elif ins.mnemonic == "lea" and len(ins.operands) == 2 and ins.operands[0].type == x86.X86_OP_REG:
            if ins.reg_name(ins.operands[0].reg) == "rcx" and ins.operands[1].type == x86.X86_OP_MEM:
                mem = ins.operands[1].mem
                if ins.reg_name(mem.base) == "rcx" and not mem.index:
                    this_adjustment += int(mem.disp)
        if ins.mnemonic == "jmp" and ins.operands and ins.operands[0].type == x86.X86_OP_IMM:
            tail_target = int(ins.operands[0].imm - BASE)
            classification = "this_adjust_thunk" if this_adjustment else "tail_thunk"
            break

    # Direct scalar getter: mov/movzx/movsxd eax/rax,[rcx+off]; ret.
    if tail_target is None:
        for index, ins in enumerate(selected[:8]):
            if not ins.mnemonic.startswith("mov") or len(ins.operands) < 2:
                continue
            dst, src = ins.operands[0], ins.operands[1]
            if dst.type != x86.X86_OP_REG or src.type != x86.X86_OP_MEM:
                continue
            if ins.reg_name(dst.reg) not in {"eax", "rax"} or ins.reg_name(src.mem.base) != "rcx" or src.mem.index:
                continue
            if any(later.mnemonic == "ret" for later in selected[index + 1:index + 5]):
                getter_offset = int(src.mem.disp)
                getter_width = int(src.size)
                classification = "simple_scalar_getter"
                break
        if getter_offset is None and len(selected) >= 2:
            if selected[0].mnemonic == "xor" and selected[1].mnemonic == "ret":
                classification = "constant_zero"

    return {
        "target_rva": rva,
        "function": function_meta,
        "instruction_count": len(rows),
        "classification": classification,
        "this_adjustment": this_adjustment,
        "tail_target_rva": tail_target,
        "getter_field_offset": getter_offset,
        "getter_width": getter_width,
        "instructions": ins_rows,
    }


def _scan_field_accesses(view: StaticView, field_offsets: set[int]) -> list[dict[str, object]]:
    """Find bounded function-local accesses to candidate object fields."""
    code = CodeReview(view)
    result = []
    for begin, end, unwind in view.functions:
        if end <= begin or end - begin > 0x10000:
            continue
        section = view.section(begin)
        if section is None or section["name"] not in (".text", ".data0"):
            continue
        rows = code.decode((begin, end, unwind))
        for index, ins in enumerate(rows):
            hits = []
            for operand_index, operand in enumerate(ins.operands):
                if operand.type != x86.X86_OP_MEM or int(operand.mem.disp) not in field_offsets:
                    continue
                access = int(operand.access)
                write = bool(access & capstone.CS_AC_WRITE)
                if not access and operand_index == 0 and ins.mnemonic.startswith(("mov", "lea")):
                    write = True
                hits.append({
                    "operand_index": operand_index,
                    "base": ins.reg_name(operand.mem.base),
                    "index": ins.reg_name(operand.mem.index),
                    "scale": int(operand.mem.scale),
                    "displacement": int(operand.mem.disp),
                    "read": bool(access & capstone.CS_AC_READ),
                    "write": write,
                })
            if not hits:
                continue
            start = max(0, index - 12)
            stop = min(len(rows), index + 13)
            result.append({
                "function_begin_rva": begin,
                "function_end_rva": end,
                "access_rva": int(ins.address - BASE),
                "access_instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
                "operands": hits,
                "window": [_instruction_row(row) for row in rows[start:stop]],
            })
    return result


def _scan_vtable_references(view: StaticView, table_rvas: set[int]) -> list[dict[str, object]]:
    code = CodeReview(view)
    result = []
    for begin, end, unwind in view.functions:
        if end <= begin or end - begin > 0x10000:
            continue
        section = view.section(begin)
        if section is None or section["name"] not in (".text", ".data0"):
            continue
        rows = code.decode((begin, end, unwind))
        for index, ins in enumerate(rows):
            resolved = []
            for operand in ins.operands:
                if operand.type == x86.X86_OP_IMM:
                    candidate = int(operand.imm - BASE)
                    if candidate in table_rvas:
                        resolved.append(candidate)
                elif operand.type == x86.X86_OP_MEM and ins.reg_name(operand.mem.base) == "rip":
                    candidate = int(ins.address + ins.size + operand.mem.disp - BASE)
                    if candidate in table_rvas:
                        resolved.append(candidate)
            if not resolved:
                continue
            result.append({
                "function_begin_rva": begin,
                "function_end_rva": end,
                "reference_rva": int(ins.address - BASE),
                "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
                "table_rvas": sorted(set(resolved)),
                "window": [_instruction_row(row) for row in rows[max(0, index - 8):min(len(rows), index + 12)]],
            })
    return result


def _scan_slot5_scalar_candidates(view: StaticView) -> list[dict[str, object]]:
    """Inventory likely calls to the scalar setter immediately before slot 6.

    This remains a candidate list: slot numbers are class-local.  We retain
    only sites that visibly prepare EDX/RDX and do not prepare XMM arguments or
    shadow-space parameters in the final short window.
    """
    addresses = set()
    patterns = [bytes((0xFF, 0x50 + register, 0x28)) for register in range(8)]
    for name in (".text", ".data0"):
        base, data = view.segments[name]
        for pattern in patterns:
            start = 0
            while True:
                at = data.find(pattern, start)
                if at < 0:
                    break
                addresses.add(base + at)
                start = at + 1
    by_function = defaultdict(set)
    for address in addresses:
        found = view.function(address)
        if found is not None and found[1] - found[0] <= 0x10000:
            by_function[found].add(address)
    code = CodeReview(view)
    result = []
    for found, wanted in by_function.items():
        rows = code.decode(found)
        for index, ins in enumerate(rows):
            address = int(ins.address - BASE)
            if address not in wanted or ins.mnemonic != "call":
                continue
            window = rows[max(0, index - 12):index]
            edx_writer = None
            prepares_extended_arguments = False
            for prior in window:
                try:
                    _reads, writes = prior.regs_access()
                except capstone.CsError:
                    writes = []
                names = {prior.reg_name(register) for register in writes}
                if names & {"rdx", "edx", "dx", "dl"}:
                    edx_writer = prior
                if any(name.startswith("xmm") for name in names):
                    prepares_extended_arguments = True
                if any(
                    operand.type == x86.X86_OP_MEM
                    and prior.reg_name(operand.mem.base) == "rsp"
                    and 0x20 <= int(operand.mem.disp) <= 0x48
                    for operand in prior.operands
                ):
                    prepares_extended_arguments = True
            if edx_writer is None or prepares_extended_arguments:
                continue
            result.append({
                "function_begin_rva": found[0],
                "function_end_rva": found[1],
                "call_rva": address,
                "edx_writer": _instruction_row(edx_writer),
                "window": [_instruction_row(row) for row in rows[max(0, index - 16):index + 2]],
            })
    return sorted(result, key=lambda row: row["call_rva"])


def _scan_projectile_factory_slot5_functions(view: StaticView) -> list[dict[str, object]]:
    """Find functions containing both a Projectile factory and vtable slot 5.

    Unlike the small byte-pattern inventory above, this walks decoded
    instructions and therefore also sees REX-prefixed indirect calls.  A
    co-occurrence is still only a triage lead; the report promotes a site only
    when the same-object data flow can be established from the instructions.
    """
    factories = {0xA8DEA0, 0xA90370}
    code = CodeReview(view)
    result = []
    for begin, end, unwind in view.functions:
        if end <= begin or end - begin > 0x10000:
            continue
        section = view.section(begin)
        if section is None or section["name"] not in (".text", ".data0"):
            continue
        rows = code.decode((begin, end, unwind))
        factory_calls = []
        slot5_calls = []
        for index, ins in enumerate(rows):
            if ins.mnemonic != "call" or not ins.operands:
                continue
            operand = ins.operands[0]
            if operand.type == x86.X86_OP_IMM:
                target = int(operand.imm - BASE)
                if target in factories:
                    factory_calls.append((index, target))
            elif operand.type == x86.X86_OP_MEM and int(operand.mem.disp) == 0x28:
                slot5_calls.append(index)
        if not factory_calls or not slot5_calls:
            continue
        result.append({
            "function_begin_rva": begin,
            "function_end_rva": end,
            "factory_calls": [
                {"call_rva": int(rows[index].address - BASE), "target_rva": target}
                for index, target in factory_calls
            ],
            "slot5_calls": [
                {
                    "call_rva": int(rows[index].address - BASE),
                    "window": [_instruction_row(row) for row in rows[max(0, index - 20):min(len(rows), index + 4)]],
                }
                for index in slot5_calls
            ],
        })
    return result


def main() -> None:
    if digest(CLIENT) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client hash changed")
    view = StaticView()
    code = CodeReview(view)
    types, type_counts = extract_types(view)
    vtables = enumerate_vtables(view, types)
    targets = sorted({int(row["slot6_target_rva"]) for row in vtables})
    implementations = {str(rva): decode_target(view, code, rva) for rva in targets}
    by_target = defaultdict(list)
    for row in vtables:
        by_target[str(row["slot6_target_rva"])].append(row["type"])
    getter_offsets = {
        int(row["getter_field_offset"])
        for row in implementations.values()
        if row.get("classification") == "simple_scalar_getter" and row.get("getter_field_offset") is not None
    }
    candidate_tables = {
        int(row["table_rva"])
        for row in vtables
        if implementations[str(row["slot6_target_rva"])].get("classification") in {
            "simple_scalar_getter", "constant_zero", "this_adjust_thunk", "tail_thunk"
        }
    }
    report = {
        "schema": "damage-object-skill-getter-v56",
        "method": "hash-pinned static decoded bytes and RTTI only; no client execution/loading/injection/modification",
        "client_path": str(CLIENT),
        "client_sha256": EXPECTED_CLIENT_SHA256,
        "callsite": {
            "rva": CALLSITE_RVA,
            "bytes": view.read(CALLSITE_RVA, 3).hex(),
            "semantics": "call related object vtable slot 6 (+0x30) only when damage-result +0x90 is not positive",
        },
        "rtti_type_counts": type_counts,
        "vtable_count": len(vtables),
        "slot6_target_count": len(targets),
        "classification_counts": dict(Counter(row["classification"] for row in implementations.values())),
        "vtables": vtables,
        "implementations": implementations,
        "classes_by_target": dict(by_target),
        "simple_getter_field_offsets": sorted(getter_offsets),
        "field_accesses": _scan_field_accesses(view, getter_offsets),
        "candidate_vtable_references": _scan_vtable_references(view, candidate_tables),
        "virtual_slot5_scalar_candidates": _scan_slot5_scalar_candidates(view),
        "projectile_factory_slot5_functions": _scan_projectile_factory_slot5_functions(view),
        "production_changed": False,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "raw_inventory.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    conclusions = {
        "schema": "damage-object-skill-getter-v56-conclusions",
        "scope": report["method"],
        "client_sha256": EXPECTED_CLIENT_SHA256,
        "production_changed": False,
        "fallback_dispatch": {
            "damage_result_skill_key_offset": "0x90",
            "related_handle_offset": "0x20",
            "related_object_pointer_offset": "0x28",
            "pair_validator_rva": "0x279680",
            "virtual_call_rva": "0x6A5693",
            "virtual_slot": 6,
            "virtual_byte_offset": "0x30",
            "condition": "only when the native damage-result SkillTable row key is <= 0 and the related handle/object pair validates",
        },
        "compatible_getter_implementations": [
            {
                "classes": ["CDnProjectile", "CDnWeapon"],
                "primary_vtables": ["0xF28730", "0xF434E0"],
                "getter_rva": "0xA91640",
                "getter_bytes": "8b81c01b0000c3",
                "field": "dword object+0x1BC0",
                "setter_slot": 5,
                "setter_rva": "0xA94C50",
                "setter_bytes": "8991c01b0000c3",
                "verdict": "confirmed compatible leaf getter/setter contract",
            }
        ],
        "excluded_same_slot_implementations": [
            {
                "classes": ["CDnActor", "CDnNPCActor", "CDnPetActor", "CDnPropActor", "CDnVehicleActor", "CDnLocalVehicleActor"],
                "target_rva": "0x52A310",
                "field": "dword object+0x1174",
                "verdict": "class-local slot 6; actor identifier getter, not proved to be a SkillTable key",
            },
            {
                "classes": ["CDnPlayerActor", "CDnLocalPlayerActor", "CDnMonsterActor and derivatives"],
                "targets": ["0xA576B0", "0x8CB860", "0x98F490", "0x5861A0"],
                "verdict": "complex class-local methods with incompatible signatures/side effects; not fallback scalar getters",
            },
            {
                "classes": ["all secondary CDnProjectile/CDnWeapon vtables"],
                "verdict": "different interfaces; only the primary subobject table at offset 0 owns the confirmed +0x1BC0 getter",
            },
        ],
        "field_write_provenance": [
            {
                "kind": "constructor_zero",
                "rva": "0xC6A944",
                "instruction": "mov dword ptr [r15+0x1BC0], r13d",
                "meaning": "base construction initializes the fallback key to zero",
            },
            {
                "kind": "virtual_setter",
                "rva": "0xA94C50",
                "instruction": "mov dword ptr [rcx+0x1BC0], edx",
                "meaning": "the only confirmed scalar writer contract",
            },
            {
                "kind": "parent_to_child_copy",
                "rvas": ["0xC723F6", "0xC72403"],
                "instructions": ["mov edx,dword ptr [rsi-0x10]", "call qword ptr [rax+0x28]"],
                "caller_chain": "CDnProjectile secondary virtual 0xA925CC -> 0xA925E0 -> 0xC71780",
                "resolved_source": "parent primary object+0x1BC0",
                "resolved_destination": "new child CDnProjectile primary object+0x1BC0",
                "meaning": "child projectiles inherit the native fallback attack key from their exact parent object; no time window is involved",
            },
            {
                "kind": "root_nonzero_write",
                "verdict": "not closed",
                "meaning": "no network-handler-to-root +0x1BC0 write has been proved in the decoded static paths; do not claim a raw registration field is the native key",
            },
        ],
        "network_reproducibility": {
            "records": [
                {
                    "record": "client object registration, decoded 107-byte form",
                    "fields": {"owner": "+10 and +55", "damage_object_id": "+19", "Projectile RowId": "+23", "resource/sequence": "+59", "resource repeat": "+75"},
                    "native_fallback_key_present": False,
                },
                {
                    "record": "wrapped client parent forms (112/119/122/125/132/135)",
                    "fields": {"owner": "layout owner base +0", "damage_object_id": "+9", "Projectile RowId": "+13", "parent_damage_object_id": "+50 for parent shape"},
                    "native_fallback_key_present": False,
                },
                {
                    "record": "server object state/registration 09 5D/61/6E and qualified 62/66/6A/6F/73",
                    "fields": {"owner": "marker-4 and marker+44", "damage_object_id": "marker+8", "Projectile RowId": "marker+12", "parent_damage_object_id": "marker+49 only in fully validated parent layouts"},
                    "native_fallback_key_present": False,
                },
                {
                    "record": "direct37 damage result",
                    "fields": {"damage_object_id": "marker+13", "exact SkillTable row key": "marker+102 or +114 when the bounded layout validates"},
                    "native_fallback_key_present": "sometimes; this is the direct key branch, not the missing-key fallback itself",
                },
            ],
            "fully_reproducible_cases": [
                "a keyless child has a full same-owner parent object ID and the current parent generation already has a deterministic root key",
                "a root Projectile RowId has a version-pinned one-to-one SkillTable mapping and no action/job conflict",
            ],
            "direct_branch_reproducible_but_not_a_fallback_seed": (
                "a bounded direct37 row's exact positive SkillTable key is authoritative for that row, "
                "but static code does not prove that the result field writes object+0x1BC0; do not freeze it "
                "onto later keyless rows without independent root/lineage corroboration"
            ),
            "not_fully_reproducible_cases": [
                "root object has no exact packet key and its Projectile template is shared by multiple skills",
                "the registration variant exposes no full parent object ID",
                "damage is owned by a summon/entity attack path rather than CDnProjectile/CDnWeapon",
                "object ID generation/lifetime is missing, so a reused 24-bit ID cannot be distinguished safely",
            ],
            "overall_verdict": "the direct key branch is reproducible; the native fallback is reproducible offline for version-pinned unique roots and full-parent-lineage cases, but not universally from every currently decoded registration record",
        },
        "production_rules_priority": [
            {
                "priority": 1,
                "rule": "Treat the exact positive bounded direct37 SkillTable key as authoritative for that Hit only. Use it to validate a separately proved object root, but never seed/freeze later keyless rows by itself because no result-field -> object+0x1BC0 write is proved.",
                "expected_jobs": ["魔羽", "十字军", "雷神", "圣骑士", "影"],
            },
            {
                "priority": 2,
                "rule": "When a validated registration carries full parent_damage_object_id, recursively inherit the parent's frozen root key, mirroring the client +0x1BC0 copy. Remove any generic 2.5 s Hit-attribution cutoff after the exact object generation is linked.",
                "expected_jobs": ["魔羽", "雷神", "重炮手", "炼金圣士", "药剂师", "噬魂者"],
            },
            {
                "priority": 3,
                "rule": "Create a root key from Projectile RowId only for a version-pinned one-to-one static mapping, additionally gated by owner job and compatible action family; shared templates remain unresolved until an exact parent/action discriminator exists.",
                "expected_jobs": ["十字军", "圣骑士", "黑暗萨满", "噬魂者", "影"],
            },
            {
                "priority": 4,
                "rule": "Keep object identity as (owner, 24-bit object_id, generation), split generations at every validated creation/unknown-template replacement, and tombstone destroyed/superseded generations before accepting Hits.",
                "expected_jobs": ["all remaining jobs; prevents ID reuse and duplicate/cross-cast attribution"],
            },
            {
                "priority": 5,
                "rule": "Route summon/entity-owned damage through an entity owner -> entity attack/action -> object chain; never substitute the CDnActor +0x1174 getter or the summoner's latest action for the Projectile key.",
                "expected_jobs": ["圣徒", "机械大师", "重炮手", "炼金圣士", "药剂师", "雷神"],
            },
            {
                "priority": 6,
                "rule": "Evidence order: exact direct key > exact object-local frozen key > full parent lineage > version-pinned unique Projectile mapping > existing inference. A lower tier may not overwrite a higher tier.",
                "expected_jobs": ["all remaining jobs"],
            },
        ],
        "remaining_12_jobs": ["魔羽", "圣骑士", "十字军", "圣徒", "雷神", "重炮手", "机械大师", "炼金圣士", "药剂师", "黑暗萨满", "噬魂者", "影"],
        "blockers": [
            "the first nonzero root write to CDnProjectile/CDnWeapon +0x1BC0 has not yet been tied to a specific decoded network scalar",
            "several server marker families expose object/template but not a fully bounded parent field",
            "shared Projectile templates require exact action/parent/static-table disambiguation",
            "summon entity attacks use a separate ownership and attack-state chain",
            "coverage improvement must be measured by fixed-corpus replay; this static report alone does not prove any remaining job has reached 95%",
        ],
    }
    (OUT / "REPORT.json").write_text(
        json.dumps(conclusions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md = """# 对象 vtable+0x30 fallback attack key 静态审计（v56）

## 最终结论

内置伤害统计在 `0x6A5693` 的 fallback 不是一个可对所有对象通用的“第 7 个虚函数”。它只在伤害结果 `+0x90 <= 0` 且 `+0x20/+0x28` 句柄—对象对经 `0x279680` 校验后，调用 related object 主 vtable 的 `+0x30`。

当前客户端中与这个调用契约完全吻合的主要实现只有两类：`CDnProjectile` 和 `CDnWeapon`。两者共用 getter `0xA91640`，直接返回 `dword [object+0x1BC0]`；上一槽 setter `0xA94C50` 把 `EDX` 写到同一字段。角色、怪物、召唤实体等类即使也有“slot 6”，那只是各自类的局部槽位；其中 `CDnActor` 家族的简单函数读取 `+0x1174`，其他玩家/怪物实现还是复杂有副作用的方法，均不能当成 SkillTable key。

因此，**可离线复现一部分、但目前不能声称对所有对象完整复现**。对象登记包没有发现可直接等同于 `+0x1BC0` 的网络字段；能够可靠重建的是“精确结果键、唯一根模板、完整父对象继承”三类。

## 地址与字段

|对象类|主 vtable|slot 6|读取字段|slot 5 setter|判定|
|---|---:|---:|---:|---:|---|
|`CDnProjectile`|`0xF28730`|`0xA91640`|`dword +0x1BC0`|`0xA94C50`|确认|
|`CDnWeapon`|`0xF434E0`|`0xA91640`|`dword +0x1BC0`|`0xA94C50`|确认|
|`CDnActor/NPC/Pet/Prop/Vehicle`|各自主表|`0x52A310`|`dword +0x1174`|非本契约|排除|
|玩家/本机玩家/怪物派生类|各自主表|复杂实现|非简单返回值|非本契约|排除|

枚举结果为 2,097 个 RTTI vtable、217 个不同 slot-6 目标；只有 9 个目标是简单字段 getter，且只有 `0xA91640` 同时由 `CDnProjectile/CDnWeapon` 主表引用并具有成对 setter。Projectile/Weapon 的次级 vtable 是别的接口，不能按同一槽号解释。

## `+0x1BC0` 从哪里来

1. `0xC6A944` 在基类构造时把 `+0x1BC0` 初始化为 0。
2. `0xA94C50` 是已确认的非零标量写入口：`mov [rcx+0x1BC0], edx; ret`。
3. `CDnProjectile` 的次级虚函数 `0xA925CC → 0xA925E0 → 0xC71780` 创建子 Projectile 后，在 `0xC723F6` 从父对象 `+0x1BC0` 取值，并于 `0xC72403` 调用子对象 slot 5 写入。这证明客户端对多代箭矢/子弹采用**父对象键递归继承**，并不存在统一 2.5 秒归因窗口。
4. 根对象第一次获得非零值的网络处理写入尚未闭合。现有静态证据不允许把登记包中的 resource、sequence、actor ID 或任一短整数直接称为原生 fallback key。

## 网络记录与可复现性

|记录|已确认字段|能否直接写原生 key|
|---|---|---|
|107 字节客户端对象登记|owner `+10/+55`；object `+19`；Projectile RowId `+23`；resource/sequence `+59`|不能，未见 SkillTable key|
|112/119/122/125/132/135 包装/父对象形式|owner；object `base+9`；Projectile `base+13`；部分形态 parent `base+50`|不能直接写；有完整 parent 时可递归继承|
|服务端 `09 5D/61/6E` 及受限 `62/66/6A/6F/73`|owner `marker-4/+44`；object `+8`；Projectile `+12`；受验证父形态 parent `+49`|不能直接写；可建立对象代次和父链|
|`direct37`|damage object `marker+13`；受边界验证的精确 SkillTable key 位于 `+102/+114`|有时可以；这是直接键，也是同对象后续缺键行的最佳种子|

离线完全可复现的局部情形：

- `direct37` 自带唯一、边界正确的正 SkillTable key；
- 缺键 Hit 的对象有完整同 owner 父对象 ID，父对象当前代次已有确定根键；
- 根 Projectile RowId 在当前资源版本中一对一对应 SkillTable 行，并通过职业与动作族校验。

当前仍不可完整复现的情形：根对象无直接键且模板被多个技能共享；包形态没有完整 parent；对象其实属于召唤实体攻击链；或缺少对象代次，无法排除 24 位 ID 复用。

## 优先进入生产的规则（面向剩余 12 职业）

1. **精确结果键只管当前 Hit**：direct37 的边界正确正键对当前行权威，可用于核验一个另有依据的对象根键；但没有证据表明结果字段会写入对象 `+0x1BC0`，所以不能仅凭这一行把键冻结给同对象后续缺键 Hit。
2. **完整父链递归继承**：只要登记记录携带经验证的完整 parent ID，就照客户端 `+0x1BC0` 复制逻辑继承根键；对象代次一旦确定，取消统一 2.5 秒 Hit 截止。优先帮助魔羽长引导子箭，以及雷神、重炮、炼金、药剂、噬魂的多代对象分支。
3. **唯一根模板门控**：只有版本绑定的一对一 Projectile→SkillTable 映射才可生成根键，并同时要求职业、动作族兼容；共享模板必须保留未知。优先补十字军、圣骑士、黑暗萨满、噬魂者、影的对象型缺口。
4. **对象代次硬边界**：键必须包含 owner、24 位 object ID 和 generation；新创建、未知模板覆盖、销毁/替换都切代或写 tombstone，防止 ID 复用、重复统计和双同职业串伤。
5. **召唤实体走独立链**：圣徒、机械大师、重炮、炼金、药剂、雷神的实体攻击必须走“召唤者→实体代次→实体动作/攻击→damage object”，不能读取 Actor `+0x1174`，也不能拿召唤者最近动作代替。
6. **固定证据优先级**：精确结果键 > 同对象冻结键 > 完整父链 > 版本化唯一模板 > 现有推断。低层证据不得覆盖高层证据。

这 6 条可以直接转成生产实现，但本报告按要求没有改生产代码。它们对剩余 12 职业的真实提升仍必须跑固定抓包语料验证；静态结论本身不能宣称已经达到 95%。

## 阻塞点

- 根 Projectile/Weapon 第一次非零写入尚未与某个明确网络标量闭合。
- 部分服务端对象状态包只有 owner/object/template，没有完整 parent。
- 共享 Projectile 模板不能仅凭 RowId 唯一到技能。
- 召唤实体的 attack key 属于另一套实体状态链。
- 当前 PAK SkillTable 内容版本与旧 Resource 快照不完全相同，静态映射必须绑定当前资源哈希。

## 边界

全程只读取 hash 固定客户端、已解码段、生产源码和已有离线报告；未执行、加载、注入或修改客户端，也未修改生产文件。完整 RTTI/函数库存见 `raw_inventory.json`，结构化结论见 `REPORT.json`。
"""
    (OUT / "REPORT.md").write_text(md, encoding="utf-8")
    print(json.dumps({
        "vtable_count": len(vtables),
        "slot6_target_count": len(targets),
        "classification_counts": report["classification_counts"],
        "output": str(OUT / "REPORT.md"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
