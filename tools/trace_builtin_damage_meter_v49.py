"""Trace the client's built-in per-skill damage meter without executing it.

This is a hash-pinned, byte-only review of the decoded client streams.  It
records the proven call/data path and ranks functions which may construct the
damage-result record consumed by the meter.  It deliberately does not modify
the production decoder: a network offset is only safe after capture parity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.review_client_damage_types_v44 import (  # noqa: E402
    BASE,
    CodeReview,
    StaticView,
    extract_types,
)
import capstone  # noqa: E402
from capstone import x86_const as x86  # noqa: E402

OUT = ROOT / "outputs" / "native_wire_v49_20260909"
CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"

FUNCTIONS = {
    "damage_actor_callback": 0x529380,
    "damage_meter_dispatch": 0x6A52E0,
    "realtime_skill_aggregate": 0x6AAB40,
    "realtime_detail_populate": 0x4B0590,
    "realtime_detail_open": 0x6ABEB9,
    "prop_actor_callback": 0x98F000,
    "player_actor_callback": 0xA56AD0,
    "local_player_actor_callback": 0x8CB5A0,
}

PROPERTIES = {
    "_NameID": 0xE8D3E0,
    "_PDamage": 0xED8E68,
    "_MDamage": 0xED8E78,
    "_Element": 0xED8E88,
}

RECORD_FIELDS = {
    0x20: "related_object_ref_a",
    0x28: "related_object_ref_b",
    0x30: "damage_amount",
    0x76: "meter_eligibility_flag",
    0x90: "skill_or_attack_data_key",
    0x94: "damage_result_kind",
    0xA0: "downstream_meter_field_a0",
    0xA4: "downstream_meter_field_a4",
}


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _function_instructions(view: StaticView, start: int):
    function = view.function(start)
    if function is None or function[0] != start:
        raise ValueError(f"not an exact runtime function start: {start:#x}")
    code = CodeReview(view)
    rows = code.decode(function)
    if not rows:
        raise ValueError(f"failed to decode function {start:#x}")
    return function, rows


def _direct_targets(rows) -> list[dict]:
    result = []
    for ins in rows:
        if not (ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)):
            continue
        if not ins.operands or ins.operands[0].type != x86.X86_OP_IMM:
            continue
        result.append(
            {
                "instruction_rva": ins.address - BASE,
                "mnemonic": ins.mnemonic,
                "target_rva": ins.operands[0].imm - BASE,
                "bytes": ins.bytes.hex(),
            }
        )
    return result


def _memory_accesses(rows, base_register: int | None = None) -> list[dict]:
    result = []
    for ins in rows:
        for index, operand in enumerate(ins.operands):
            if operand.type != x86.X86_OP_MEM:
                continue
            if base_register is not None and operand.mem.base != base_register:
                continue
            displacement = operand.mem.disp
            if displacement not in RECORD_FIELDS:
                continue
            access = operand.access
            result.append(
                {
                    "instruction_rva": ins.address - BASE,
                    "mnemonic": ins.mnemonic,
                    "operands": ins.op_str,
                    "operand_index": index,
                    "base_register": ins.reg_name(operand.mem.base),
                    "displacement": displacement,
                    "field": RECORD_FIELDS[displacement],
                    "read": bool(access & capstone.CS_AC_READ),
                    "write": bool(access & capstone.CS_AC_WRITE),
                    "bytes": ins.bytes.hex(),
                }
            )
    return result


def _string_evidence(view: StaticView) -> list[dict]:
    result = []
    for text, rva in PROPERTIES.items():
        raw = view.read(rva, len(text) + 1)
        expected = text.encode("ascii") + b"\0"
        if raw != expected:
            raise ValueError(f"property string mismatch at {rva:#x}: {raw!r}")
        result.append(
            {
                "text": text,
                "rva": rva,
                "raw": raw.hex(),
                "sha256": _digest(raw),
                "provenance": view.provenance(rva, len(raw)),
            }
        )
    return result


def _callback_vtables(view: StaticView) -> list[dict]:
    """Locate verified actor vtable entries which route to the damage callback."""
    types, _ = extract_types(view)
    target_starts = {
        0x3119FC: "CDnActor adjustment thunk",
        0x53F8A0: "CDnPropActor adjustment thunk",
        0xA56AC0: "CDnPlayerActor adjustment thunk",
        0x8CB58C: "CDnLocalPlayerActor adjustment thunk",
    }
    result = []
    for section_name, (section_rva, data) in view.segments.items():
        if section_name != ".rdata":
            continue
        for offset in range(0, len(data) - 7, 8):
            value = struct.unpack_from("<Q", data, offset)[0]
            target = value - BASE
            if target not in target_starts:
                continue
            slot_rva = section_rva + offset
            candidates = []
            # A complete-object locator pointer immediately precedes a vtable.
            # Search a bounded prefix because this hit may be a later slot.
            for slot_index in range(64):
                table_rva = slot_rva - slot_index * 8
                try:
                    locator_rva = struct.unpack("<Q", view.read(table_rva - 8, 8))[0] - BASE
                    signature, subobject, constructor, td, hierarchy, self_rva = struct.unpack(
                        "<6I", view.read(locator_rva, 24)
                    )
                except (ValueError, struct.error):
                    continue
                if signature != 1 or self_rva != locator_rva or td not in types:
                    continue
                name = types[td]["simple_name"] or types[td]["decorated_name"]
                candidates.append(
                    {
                        "type": name,
                        "table_rva": table_rva,
                        "slot_index": slot_index,
                        "slot_rva": slot_rva,
                        "target_rva": target,
                        "target_label": target_starts[target],
                        "complete_object_locator_rva": locator_rva,
                        "subobject_offset": subobject,
                        "constructor_displacement": constructor,
                        "hierarchy_rva": hierarchy,
                    }
                )
            # The nearest validated locator is the owning vtable for this slot.
            if candidates:
                result.append(min(candidates, key=lambda item: item["slot_index"]))
    return sorted(result, key=lambda item: (item["type"], item["slot_rva"]))


def _candidate_record_writers(view: StaticView) -> list[dict]:
    """Rank exact runtime functions which write several damage-record fields.

    This is only a candidate list.  Register aliases and stack copies can evade
    it, while unrelated large structures can match.  No candidate is promoted
    to a wire parser without later data-flow and capture parity.
    """
    disassembler = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    disassembler.detail = True
    candidates = []
    available_sections = {".text", ".data0", ".data2"}
    for begin, end, unwind in view.functions:
        section = view.section(begin)
        if section is None or section["name"] not in available_sections:
            continue
        size = end - begin
        if size <= 0 or size > 0x10000:
            continue
        try:
            body = view.read(begin, size)
        except ValueError:
            continue
        by_base: dict[int, list[dict]] = defaultdict(list)
        for ins in disassembler.disasm(body, BASE + begin):
            for operand_index, operand in enumerate(ins.operands):
                if operand.type != x86.X86_OP_MEM or operand.mem.base == 0:
                    continue
                displacement = operand.mem.disp
                if displacement not in RECORD_FIELDS:
                    continue
                access = operand.access
                # Capstone does not annotate every legacy instruction access;
                # destination operand zero remains a conservative write hint.
                writes = bool(access & capstone.CS_AC_WRITE) or (
                    operand_index == 0 and ins.mnemonic.startswith(("mov", "lea"))
                )
                if not writes:
                    continue
                by_base[operand.mem.base].append(
                    {
                        "instruction_rva": ins.address - BASE,
                        "mnemonic": ins.mnemonic,
                        "operands": ins.op_str,
                        "base_register": ins.reg_name(operand.mem.base),
                        "displacement": displacement,
                        "field": RECORD_FIELDS[displacement],
                        "bytes": ins.bytes.hex(),
                    }
                )
        for register, rows in by_base.items():
            offsets = sorted({row["displacement"] for row in rows})
            if 0x90 not in offsets or 0x94 not in offsets or len(offsets) < 3:
                continue
            score = len(offsets) + int(0x30 in offsets) * 2 + int(0x76 in offsets)
            candidates.append(
                {
                    "function_begin_rva": begin,
                    "function_end_rva": end,
                    "unwind_rva": unwind,
                    "base_register": disassembler.reg_name(register),
                    "written_offsets": offsets,
                    "written_fields": [RECORD_FIELDS[offset] for offset in offsets],
                    "rank_score": score,
                    "writes": rows,
                }
            )
    return sorted(
        candidates,
        key=lambda item: (-item["rank_score"], -len(item["writes"]), item["function_begin_rva"]),
    )[:100]


def _review_functions(view: StaticView) -> dict:
    result = {}
    for label, start in FUNCTIONS.items():
        function, rows = _function_instructions(view, start)
        result[label] = {
            "begin_rva": function[0],
            "end_rva": function[1],
            "unwind_rva": function[2],
            "byte_count": function[1] - function[0],
            "bytes_sha256": _digest(view.read(function[0], function[1] - function[0])),
            "direct_branches": _direct_targets(rows),
            "record_field_accesses": _memory_accesses(rows),
        }
    return result


def _required_assertions(report: dict) -> None:
    functions = report["functions"]
    assert any(
        row["target_rva"] == FUNCTIONS["damage_meter_dispatch"]
        for row in functions["damage_actor_callback"]["direct_branches"]
    )
    assert any(
        row["target_rva"] == FUNCTIONS["realtime_skill_aggregate"]
        for row in functions["damage_meter_dispatch"]["direct_branches"]
    )
    dispatch_fields = {
        row["displacement"] for row in functions["damage_meter_dispatch"]["record_field_accesses"]
    }
    assert {0x20, 0x28, 0x30, 0x76, 0x90, 0x94, 0xA0}.issubset(dispatch_fields)
    assert {item["text"] for item in report["properties"]} == set(PROPERTIES)
    assert {item["type"] for item in report["callback_vtables"]} >= {
        "CDnActor",
        "CDnPropActor",
        "CDnPlayerActor",
        "CDnLocalPlayerActor",
    }


def analyze() -> dict:
    started = time.monotonic()
    view = StaticView()
    if _digest(view.raw) != CLIENT_SHA256:
        raise ValueError("client hash mismatch")
    report = {
        "schema": "dragon-nest-builtin-damage-meter-trace/v49",
        "client_path": str(view.CLIENT) if hasattr(view, "CLIENT") else "D:/DragonNest/Reborn/dragonnest_reborn.exe",
        "client_sha256": CLIENT_SHA256,
        "method": "hash-pinned static bytes only; no client or DLL execution",
        "functions": _review_functions(view),
        "properties": _string_evidence(view),
        "callback_vtables": _callback_vtables(view),
        "candidate_record_writers": _candidate_record_writers(view),
        "proven_chain": [
            "actor MADamageBase virtual callback receives a damage-result record",
            "0x529380 forwards the original record to 0x6a52e0",
            "0x6a52e0 reads damage at +0x30, result kind at +0x94 and initial skill/attack data key at +0x90",
            "when +0x90 is absent, 0x6a52e0 can recover an attack key from the related object/entity virtual method",
            "the resulting data-row key is queried for _NameID and localized to a skill display name",
            "0x6aab40 aggregates damage into the real-time meter by that exact display name",
        ],
        "not_yet_proven": [
            "which server UDP/TCP field or object registration writes damage-result +0x90",
            "whether current direct37 offsets +102/+114 and direct41 +106 are the same key on every result variant",
            "whether every summon/field/continuation result obtains +0x90 directly or through related-object fallback",
            "capture-level parity for a replacement attribution path",
        ],
        "production_changed": False,
    }
    _required_assertions(report)
    report["summary"] = {
        "function_count": len(report["functions"]),
        "callback_vtable_count": len(report["callback_vtables"]),
        "candidate_record_writer_count": len(report["candidate_record_writers"]),
        "network_skill_key_origin_proven": False,
        "safe_to_replace_production_attribution": False,
    }
    report["elapsed_seconds"] = time.monotonic() - started
    return report


def markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Client built-in damage meter trace (v49)",
        "",
        f"Client SHA-256: `{report['client_sha256']}`.",
        "",
        "## Proven result",
        "",
    ]
    lines += [f"{index}. {item}." for index, item in enumerate(report["proven_chain"], 1)]
    lines += [
        "",
        "This proves that the client does not build its per-skill meter from a 2.5-second timing guess. "
        "It carries a per-hit skill/attack data key in the damage-result record, with an entity fallback, "
        "then resolves `_NameID` and aggregates by the localized skill name.",
        "",
        "## Verified actor callback vtables",
        "",
        "| Type | subobject | slot | thunk |",
        "|---|---:|---:|---:|",
    ]
    for item in report["callback_vtables"]:
        lines.append(
            f"| {item['type']} | `{item['subobject_offset']:#x}` | {item['slot_index']} | `{item['target_rva']:#x}` |"
        )
    lines += [
        "",
        "## Candidate record writers",
        "",
        f"The bounded static scan retained {summary['candidate_record_writer_count']} candidates. "
        "These are leads, not wire-field claims.",
        "",
        "| Rank | Function | base | written fields |",
        "|---:|---:|---|---|",
    ]
    for index, item in enumerate(report["candidate_record_writers"][:20], 1):
        lines.append(
            f"| {index} | `{item['function_begin_rva']:#x}` | `{item['base_register']}` | "
            + ", ".join(f"`+{offset:#x}`" for offset in item["written_offsets"])
            + " |"
        )
    lines += ["", "## Remaining proof obligations", ""]
    lines += [f"- {item}." for item in report["not_yet_proven"]]
    lines += [
        "",
        "Production attribution is intentionally unchanged until the key's network/object origin is proven "
        "and replay shows identical raw source, target, amount, critical flag and total damage.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    report = analyze()
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "builtin_damage_meter.json"
    md_path = OUT / "builtin_damage_meter.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "elapsed_seconds": report["elapsed_seconds"],
                "outputs": [str(json_path), str(md_path)],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
