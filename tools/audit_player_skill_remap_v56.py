"""Hash-pinned, read-only static/capture audit for the 0xA52510 remapper.

The client is opened only as bytes through the v44 StaticView.  This script
does not execute/load/inject/debug the PE, create a patched image, or write
production files.  Its only writes are JSON sidecars under the fixed v56
output directory.

Run:
    python -B tools/audit_player_skill_remap_v56.py
    python -B tools/audit_player_skill_remap_v56.py --skip-captures
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from review_client_damage_types_v44 import (  # noqa: E402
    BASE,
    StaticView,
    capstone,
    extract_types,
    x86,
)
from trace_projectile_config_v45 import parse_dnt  # noqa: E402
from dragon_nest_builder.live_dps import protocol_v576 as protocol  # noqa: E402


OUT = ROOT / "outputs/player_skill_remap_v56_20260909"
RESOURCE = Path(
    "E:/ext0811.rar/Resource00/Resource00/resource/ext/runeresonatetable.dnt"
)
FIXED_CAPTURE_MANIFEST = (
    ROOT / "outputs/compact_entity_selectors_20260908/final_cumulative35.json"
)
EXPECTED_CLIENT_SHA256 = (
    "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
)
EXPECTED_RESOURCE_SHA256 = (
    "ddb6498ab7de0b106f0d9353c372468ea6b6e548c4c04d90a9906e2fd4ba463f"
)
CAPTURE_HEADER = struct.Struct("<dBII")
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_CAPTURE_HIT_EXAMPLES = 300


# Exact instruction bytes, bound to EXPECTED_CLIENT_SHA256 and the six
# manifest-verified decoded streams.  The descriptions are interpretation;
# the bytes and decoded operands are emitted separately in evidence.json.
ANCHORS = {
    0x354D4D: ("488905c49ee300", "publish cache owner at RVA 0x118EC18"),
    0x354D5E: ("4c896808", "record vector begin = 0"),
    0x354D62: ("4c896810", "record vector end = 0"),
    0x354D66: ("4c896818", "record vector capacity = 0"),
    0x354D6D: ("e80e380000", "only verified direct call to 0x358580"),
    0x35DC61: ("4c8d0510c7b300", "runeresonatetable registration literal"),
    0x35DC70: ("ba5f010000", "registry key 0x15f"),
    0x35DC78: ("e8e317ffff", "registry registration call"),
    0x3585C0: ("488d7108", "owner +8 is vector<record>"),
    0x3585DC: ("ff10", "destroy one old record through vtable slot zero"),
    0x3585DE: ("4883c358", "record stride 0x58"),
    0x3585EA: ("48895e08", "outer vector end = begin"),
    0x3585EE: ("ba5f010000", "load registry key 0x15f"),
    0x3585FA: ("e87181ffff", "resolve source DNT table"),
    0x358A39: ("448b33", "read base skill"),
    0x358C4B: ("8b03", "read change skill"),
    0x358C55: ("4585f6", "only base skill is the insertion gate"),
    0x358C5A: ("448975b8", "pair[0] local write"),
    0x358C5E: ("8945bc", "pair[1] local write"),
    0x358C6B: ("49890424", "8-byte pair fast append"),
    0x358C85: ("e88665fcff", "8-byte pair grow/append"),
    0x358C91: ("4183fd14", "column suffix loop limit 20"),
    0x35915C: ("e88f18ffff", "copy local 0x58 record into cache"),
    0x359161: ("4883430858", "advance outer vector end by 0x58"),
    0x359173: ("e8589efeff", "outer record vector grow/append"),
    0x34AA3F: ("4883c228", "copy first vector from record +0x28"),
    0x34AA47: ("e8a4a8ffff", "deep-copy first vector"),
    0x34AA4D: ("488d5340", "copy second vector from record +0x40"),
    0x34AA55: ("e896a8ffff", "deep-copy second vector"),
    0x34CE69: ("48897b40", "clear second vector begin"),
    0x34CE75: ("488b4b28", "load remap vector begin for free"),
    0x34CEAF: ("48897b28", "clear remap vector begin"),
    0x34EEEF: ("e82cdfffff", "record destructor calls vector cleanup"),
    0x6A63C2: ("4c8d0d1740ab00", "CDnPlayerActor RTTI argument"),
    0x6A63C9: ("4c8d05f03fab00", "CDnActor RTTI argument"),
    0x6A63DA: ("e809bc7200", "runtime cast before remap"),
    0x6A63EE: ("e8fddc3a00", "call applicability gate 0xA540F0"),
    0x6A63FE: ("e80dc13a00", "call remapper 0xA52510"),
    0xA52560: ("498b85500b0000", "actor +0xb50 metadata"),
    0xA52567: ("48634818", "dynamic slot base from metadata +0x18"),
    0xA5256E: ("4a8b6c2908", "load slot object pointer"),
    0xA5266A: ("488d8d20130000", "part +0x1320 secondary subobject"),
    0xA52674: ("ff5010", "secondary vtable slot two getter"),
    0xA52677: ("488b0d9ac57300", "load global cache at RVA 0x118EC18"),
    0xA52691: ("44395108", "record +8 row-key comparison"),
    0xA526A2: ("4c8b4130", "remap vector end at record +0x30"),
    0xA526A6: ("488b4128", "remap vector begin at record +0x28"),
    0xA52710: ("443930", "compare pair[0] to raw skill key"),
    0xA52720: ("448b6004", "return pair[1]"),
    0xA54060: ("488b05518f7300", "environment object RVA 0x118cfb8"),
    0xA54067: ("448b8078040000", "environment code at +0x478"),
    0xA54085: ("448b8874040000", "environment code at +0x474"),
    0xA5408C: ("488b0585ab7300", "load global cache at RVA 0x118EC18"),
    0xA540B1: ("488b5048", "apply vector end at record +0x48"),
    0xA540C3: ("413bc9", "compare apply pair low32"),
    0xA540CC: ("413bc8", "compare apply pair high32"),
    0xA54E75: ("ba42000000", "feature/state gate number 0x42"),
    0xA54E7A: ("e80116b2ff", "invoke feature/state gate"),
    0x44C130: ("8b4164", "part subobject getter returns +0x64"),
    0x9F048E: ("89ae84130000", "part row-key initialization write"),
    0xB8DA1B: ("e8502d7cff", "setter resolves registry type 1"),
    0xB8DA2A: ("e871806aff", "setter validates incoming row key"),
    0xB8DA3C: ("89bb84130000", "part row-key setter write"),
    0xB8DA45: ("ff5030", "notify through primary vtable +0x30"),
}


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def file_digest(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    result = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            result.update(chunk)
    return result.hexdigest()


def instruction_at(view: StaticView, rva: int):
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    ins = next(decoder.disasm(view.read(rva, 16), BASE + rva), None)
    if ins is None or ins.address != BASE + rva:
        raise ValueError(f"cannot decode anchor {rva:#x}")
    return ins


def direct_calls_to(view: StaticView, target: int) -> list[int]:
    """Raw relative-call prefilter followed by instruction-boundary proof."""
    base, data = view.segments[".text"]
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    candidates: list[int] = []
    for match in re.finditer(b"\xe8", data):
        at = base + match.start()
        if match.start() + 5 > len(data):
            continue
        resolved = at + 5 + struct.unpack_from("<i", data, match.start() + 1)[0]
        if resolved != target:
            continue
        function = view.function(at)
        if function is None:
            continue
        rows = decoder.disasm(
            view.read(function[0], function[1] - function[0]), BASE + function[0]
        )
        if any(
            row.address == BASE + at
            and row.group(capstone.CS_GRP_CALL)
            and row.operands
            and row.operands[0].type == x86.X86_OP_IMM
            and row.operands[0].imm - BASE == target
            for row in rows
        ):
            candidates.append(at)
    return sorted(set(candidates))


def pointer_references(view: StaticView, target: int) -> list[dict[str, object]]:
    needle = struct.pack("<Q", BASE + target)
    result = []
    for section, (base, data) in view.segments.items():
        result.extend(
            {"section": section, "rva": base + match.start()}
            for match in re.finditer(re.escape(needle), data)
        )
    return result


def displacement_stores(view: StaticView, displacement: int) -> list[dict[str, object]]:
    """Find verified x64 instructions that store to [reg + displacement]."""
    base, data = view.segments[".text"]
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    functions: set[int] = set()
    stores: list[dict[str, object]] = []
    for match in re.finditer(re.escape(struct.pack("<I", displacement)), data):
        function = view.function(base + match.start())
        if function is None or function[0] in functions:
            continue
        functions.add(function[0])
        for ins in decoder.disasm(
            view.read(function[0], function[1] - function[0]), BASE + function[0]
        ):
            if not ins.operands or ins.operands[0].type != x86.X86_OP_MEM:
                continue
            mem = ins.operands[0].mem
            if mem.disp != displacement or ins.mnemonic in {"cmp", "test", "lea"}:
                continue
            stores.append(
                {
                    "rva": ins.address - BASE,
                    "bytes": ins.bytes.hex(),
                    "mnemonic": ins.mnemonic,
                    "operands": ins.op_str,
                    "function_begin_rva": function[0],
                    "function_end_rva": function[1],
                }
            )
    return stores


def c_string(view: StaticView, rva: int, budget: int = 128) -> str:
    raw = view.read(rva, budget)
    end = raw.find(b"\0")
    if end < 0:
        raise ValueError(f"unterminated string at {rva:#x}")
    return raw[:end].decode("ascii")


def build_static_evidence() -> tuple[dict[str, object], dict[str, object]]:
    view = StaticView()
    if digest(view.raw) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client hash changed after StaticView verification")

    anchors = []
    for rva, (expected, meaning) in ANCHORS.items():
        ins = instruction_at(view, rva)
        if ins.bytes.hex() != expected:
            raise ValueError(f"anchor bytes changed at {rva:#x}")
        anchors.append(
            {
                "rva": rva,
                "bytes": expected,
                "mnemonic": ins.mnemonic,
                "operands": ins.op_str,
                "meaning": meaning,
                "provenance": view.provenance(rva, len(ins.bytes)),
            }
        )

    if c_string(view, 0xE9A378) != "runeresonatetable":
        raise ValueError("RuneResonate registry literal changed")
    literal_locations: dict[str, list[int]] = {}
    for literal in (b"_baseSkill\0", b"_changeSkill\0", b"_ApplyMap\0"):
        found = []
        for _, (base, data) in view.segments.items():
            found.extend(base + m.start() for m in re.finditer(re.escape(literal), data))
        if not found:
            raise ValueError(f"missing literal {literal!r}")
        literal_locations[literal[:-1].decode("ascii")] = found

    types, _ = extract_types(view)
    rtti_checks = []
    for col_rva, vtable_rva, expected_name in (
        (0x101BF70, 0xF1D718, "CDnParts"),
        (0x10294D0, 0xF39C98, "CDnSimpleParts"),
    ):
        signature, offset, cd_offset, td, hierarchy, self_rva = struct.unpack(
            "<6I", view.read(col_rva, 24)
        )
        item = types.get(td)
        name = None if item is None else item.get("simple_name")
        slot_two = struct.unpack("<Q", view.read(vtable_rva + 16, 8))[0] - BASE
        locator_pointer = struct.unpack("<Q", view.read(vtable_rva - 8, 8))[0] - BASE
        if (
            signature != 1
            or offset != 0x1320
            or cd_offset != 0
            or self_rva != col_rva
            or name != expected_name
            or slot_two != 0x44C130
            or locator_pointer != col_rva
        ):
            raise ValueError(f"RTTI secondary-subobject binding changed at {col_rva:#x}")
        rtti_checks.append(
            {
                "class": name,
                "complete_object_locator_rva": col_rva,
                "subobject_offset": offset,
                "type_descriptor_rva": td,
                "hierarchy_rva": hierarchy,
                "vtable_rva": vtable_rva,
                "slot_two_target_rva": slot_two,
            }
        )

    setter_col = struct.unpack("<Q", view.read(0xF39A88 - 8, 8))[0] - BASE
    setter_values = struct.unpack("<6I", view.read(setter_col, 24))
    setter_type = types[setter_values[3]].get("simple_name")
    setter_slot_five = struct.unpack("<Q", view.read(0xF39A88 + 5 * 8, 8))[0] - BASE
    if setter_type != "CDnSimpleParts" or setter_slot_five != 0xB8D9F0:
        raise ValueError("CDnSimpleParts setter vtable binding changed")

    calls = direct_calls_to(view, 0x358580)
    if calls != [0x354D6D]:
        raise ValueError(f"unexpected direct builder call set: {calls!r}")
    pointer_refs = pointer_references(view, 0x358580)
    if pointer_refs:
        raise ValueError("unexpected decoded-stream pointer reference to builder")

    row_key_stores = displacement_stores(view, 0x1384)
    if [(row["rva"], row["bytes"]) for row in row_key_stores] != [
        (0x9F048E, "89ae84130000"),
        (0xB8DA3C, "89bb84130000"),
    ]:
        raise ValueError(f"part row-key store set changed: {row_key_stores!r}")

    resource_raw = RESOURCE.read_bytes()
    if digest(resource_raw) != EXPECTED_RESOURCE_SHA256:
        raise ValueError("old RuneResonateTable hash mismatch")
    dnt = parse_dnt(resource_raw)
    rows = []
    suffixes = []
    for row_index, row in enumerate(dnt["rows"]):
        cells = row["cells"]
        row_id = int(cells["RowId"]["value"])
        pairs = []
        for suffix in range(1, 21):
            base = int(cells.get(f"_baseSkill{suffix}", {"value": 0})["value"])
            change = int(cells.get(f"_changeSkill{suffix}", {"value": 0})["value"])
            if base:
                pairs.append([base, change])
                suffixes.append(suffix)
        apply_raw = str(cells.get("_ApplyMap", {"value": ""})["value"])
        apply_pairs = [
            [int(value) for value in token.split(",")]
            for token in apply_raw.split(";")
            if token
        ]
        if any(len(pair) != 2 for pair in apply_pairs):
            raise ValueError(f"invalid _ApplyMap at RowId {row_id}")
        rows.append(
            {
                "row_id": row_id,
                "row_index": row_index,
                "file_offset": row["offset"],
                "skill_pairs": pairs,
                "apply_pairs": apply_pairs,
                "apply_raw": apply_raw,
            }
        )
    row_ids = [row["row_id"] for row in rows]
    if len(rows) != 2912 or len(set(row_ids)) != 2912:
        raise ValueError("unexpected old RuneResonateTable row inventory")

    static = {
        "schema": "player-skill-remap-static-evidence-v56",
        "safety": {
            "client_executed_or_loaded": False,
            "client_modified": False,
            "production_files_modified": False,
            "analysis": "byte-only StaticView over original PE metadata and six hash-verified decoded streams",
        },
        "client": {
            "path": str(view.CLIENT) if hasattr(view, "CLIENT") else "D:/DragonNest/Reborn/dragonnest_reborn.exe",
            "sha256": EXPECTED_CLIENT_SHA256,
            "file_bytes": len(view.raw),
            "preferred_image_base": BASE,
        },
        "verified_inputs": view.inputs,
        "anchors": anchors,
        "literals": {
            "runeresonatetable": {"rva": 0xE9A378},
            **literal_locations,
        },
        "cache": {
            "global_pointer_rva": 0x118EC18,
            "owner_vector_offsets": {"begin": 8, "end": 16, "capacity": 24},
            "record_stride": 0x58,
            "record_row_key_offset": 8,
            "skill_pair_vector_offsets": {"begin": 0x28, "end": 0x30, "capacity": 0x38},
            "apply_pair_vector_offsets": {"begin": 0x40, "end": 0x48, "capacity": 0x50},
            "pair_stride": 8,
            "pair_layout": {"pair_0_base_skill_u32": 0, "pair_1_change_skill_u32": 4},
            "direct_builder_callers": calls,
            "decoded_pointer_references_to_builder": pointer_refs,
        },
        "actor": {
            "caller_cast_source_type": "CDnActor",
            "caller_cast_target_type": "CDnPlayerActor",
            "metadata_offset": 0xB50,
            "metadata_dynamic_base_dword_offset": 0x18,
            "candidate_slot_offsets": [0xC70, 0xC80, 0xC90, 0xCA0],
            "slot_stride": 16,
            "part_secondary_subobject_offset": 0x1320,
            "secondary_getter_slot": 2,
            "secondary_getter_target_rva": 0x44C130,
            "secondary_field_offset": 0x64,
            "outer_part_row_key_offset": 0x1384,
            "rtti_bindings": rtti_checks,
            "row_key_stores": row_key_stores,
            "setter": {
                "class": setter_type,
                "primary_vtable_rva": 0xF39A88,
                "slot": 5,
                "target_rva": setter_slot_five,
                "network_opcode_bound": False,
            },
        },
        "gates": {
            "feature_or_state_gate_number": 0x42,
            "environment_object_rva": 0x118CFB8,
            "environment_pair_offsets": [0x474, 0x478],
            "record_apply_pair_vector_offsets": [0x40, 0x48],
            "semantic_names_proven": False,
        },
        "evidence_limits": [
            "Decoded static streams do not prove runtime reachability.",
            "No second direct call does not exclude indirect calls or unavailable code.",
            "RTTI proves class/subobject binding, not an exported C++ method name.",
            "The old DNT snapshot proves structure/examples, not current-version content equality.",
            "No network opcode is bound to the +0x1384 setter in this audit.",
        ],
    }
    old_mapping = {
        "schema": "old-runeresonatetable-remap-snapshot-v56",
        "source": str(RESOURCE),
        "source_sha256": EXPECTED_RESOURCE_SHA256,
        "current_client_content_equality_proven": False,
        "row_count": len(rows),
        "unique_row_ids": len(set(row_ids)),
        "declared_pair_column_suffix_max": max(suffixes, default=0),
        "client_probe_suffix_max": 20,
        "rows": rows,
    }
    return static, old_mapping


def find_u32_ids(data: bytes, wanted: set[int]) -> list[tuple[int, int]]:
    """Return exact unaligned little-endian u32 hits for this fixed ID set."""
    if not wanted:
        return []
    high_bytes = {value >> 24 for value in wanted}
    if len(high_bytes) == 1:
        marker = bytes([next(iter(high_bytes))])
        found = []
        cursor = 3
        while True:
            high_at = data.find(marker, cursor)
            if high_at < 0:
                break
            offset = high_at - 3
            value = int.from_bytes(data[offset : offset + 4], "little")
            if value in wanted:
                found.append((offset, value))
            cursor = high_at + 1
        return found
    return [
        (offset, int.from_bytes(data[offset : offset + 4], "little"))
        for offset in range(max(0, len(data) - 3))
        if int.from_bytes(data[offset : offset + 4], "little") in wanted
    ]


def find_unknown_phase_v576_ids(
    wire: bytes, wanted: set[int]
) -> list[tuple[int, int, int]]:
    """Find IDs over all 13 XOR phases without materialising 13 message copies.

    Every wanted RowId has high byte 0x10.  The 13 key bytes are distinct, so
    a wire byte at the fourth ID position selects at most one candidate phase.
    Thirteen C-level ``bytes.find`` scans plus sparse four-byte validation are
    equivalent to decoding all phases, while avoiding millions of tiny
    bytearray/translate calls for unknown short messages.
    """
    key = protocol._V576_BODY_XOR_KEY
    if len(key) != 13 or len(set(key)) != len(key):
        raise ValueError("v576 XOR key assumption changed")
    if any(value >> 24 != 0x10 for value in wanted):
        raise ValueError("unknown-phase scanner requires one high byte 0x10")
    hits: list[tuple[int, int, int]] = []
    for key_index, key_byte in enumerate(key):
        marker = bytes([0x10 ^ key_byte])
        high_at = wire.find(marker, protocol._V576_BODY_OFFSET + 3)
        while high_at >= 0:
            offset = high_at - 3
            decoded = bytes(
                wire[offset + index] ^ key[(key_index - 3 + index) % len(key)]
                for index in range(4)
            )
            value = int.from_bytes(decoded, "little")
            if value in wanted:
                phase = (high_at - key_index) % len(key)
                # Independent four-byte replay of the documented transform.
                if protocol._decode_v576_body(wire, phase)[offset : offset + 4] != decoded:
                    raise ValueError("unknown-phase sparse decode disagreement")
                hits.append((offset, value, phase))
            high_at = wire.find(marker, high_at + 1)
    return sorted(hits)


def scan_fixed_captures(row_ids: set[int]) -> dict[str, object]:
    manifest_raw = FIXED_CAPTURE_MANIFEST.read_bytes()
    manifest = json.loads(manifest_raw)
    paths = [Path(item["capture"]) for item in manifest["capture_checks"]]
    if len(paths) != 35 or len(set(paths)) != 35:
        raise ValueError("fixed capture set is not exactly 35 unique paths")
    if any(value >> 24 != 0x10 for value in row_ids):
        raise ValueError("fixed RowId scanner high-byte assumption changed")

    started = time.perf_counter()
    totals = Counter()
    row_hit_counts = Counter()
    examples: list[dict[str, object]] = []
    capture_rows = []

    def retain_hits(
        hits: list[tuple[int, int]],
        *,
        capture_index: int,
        frame_index: int,
        file_offset: int,
        timestamp: float,
        direction: str | None,
        transport: str,
        layer: str,
        message_index: int | None,
        phase: int | None,
        phase_source: str | None,
        blob: bytes,
    ) -> None:
        if not hits:
            return
        totals[f"hits_{layer}"] += len(hits)
        totals[f"objects_with_hits_{layer}"] += 1
        for offset, row_id in hits:
            row_hit_counts[(layer, row_id)] += 1
            if len(examples) >= MAX_CAPTURE_HIT_EXAMPLES:
                continue
            left, right = max(0, offset - 16), min(len(blob), offset + 20)
            examples.append(
                {
                    "capture_index": capture_index,
                    "frame_index": frame_index,
                    "file_offset": file_offset,
                    "timestamp": timestamp,
                    "direction": direction,
                    "transport": transport,
                    "layer": layer,
                    "message_index": message_index,
                    "xor_phase": phase,
                    "phase_source": phase_source,
                    "payload_offset": offset,
                    "row_id": row_id,
                    "container_sha256": digest(blob),
                    "bounded_context_sha256": digest(blob[left:right]),
                    "bounded_context_start": left,
                    "bounded_context_bytes": right - left,
                }
            )

    for capture_index, path in enumerate(paths, 1):
        before = path.stat()
        capture_hash = sha256()
        per_capture = Counter()
        batch = bytearray()
        batch_starts: list[int] = []
        batch_messages: list[dict[str, object]] = []

        def flush_udp_batch() -> None:
            """Scan many short messages with 13 C-level marker passes."""
            if not batch_messages:
                return
            joined = bytes(batch)
            key = protocol._V576_BODY_XOR_KEY
            decoded_byte_two_values = {
                struct.pack("<I", value)[2] for value in row_ids
            }
            if decoded_byte_two_values != {0x00, 0x01, 0x99}:
                raise ValueError("old Rune RowId byte-two prefilter assumption changed")
            grouped: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for key_index, key_byte in enumerate(key):
                for decoded_byte_two in decoded_byte_two_values:
                    # RowId byte 2 followed by its fixed high byte 0x10.
                    marker = bytes(
                        [
                            decoded_byte_two ^ key[(key_index - 1) % len(key)],
                            0x10 ^ key_byte,
                        ]
                    )
                    marker_at = joined.find(marker, 2)
                    while marker_at >= 0:
                        high_at = marker_at + 1
                        offset = marker_at - 2
                        message_slot = bisect_right(batch_starts, offset) - 1
                        if message_slot >= 0:
                            meta = batch_messages[message_slot]
                            start = int(meta["start"])
                            end = int(meta["end"])
                            if start + protocol._V576_BODY_OFFSET <= offset and offset + 4 <= end:
                                local_high_at = high_at - start
                                phase = (local_high_at - key_index) % len(key)
                                expected_phase = meta["expected_phase"]
                                if expected_phase is None or phase == expected_phase:
                                    decoded_four = bytes(
                                        joined[offset + index]
                                        ^ key[(key_index - 3 + index) % len(key)]
                                        for index in range(4)
                                    )
                                    value = int.from_bytes(decoded_four, "little")
                                    if value in row_ids:
                                        grouped.setdefault((message_slot, phase), []).append(
                                            (offset - start, value)
                                        )
                        marker_at = joined.find(marker, marker_at + 1)

            for (message_slot, phase), hits in grouped.items():
                meta = batch_messages[message_slot]
                start, end = int(meta["start"]), int(meta["end"])
                wire = joined[start:end]
                decoded = protocol._decode_v576_body(wire, phase)
                # Recheck the sparse result against the complete documented transform.
                if any(
                    int.from_bytes(decoded[offset : offset + 4], "little") != row_id
                    for offset, row_id in hits
                ):
                    raise ValueError("batched sparse decode disagreement")
                per_capture["decoded_hits"] += len(hits)
                retain_hits(
                    hits,
                    capture_index=capture_index,
                    frame_index=int(meta["frame_index"]),
                    file_offset=int(meta["file_offset"]),
                    timestamp=float(meta["timestamp"]),
                    direction=meta["direction"],
                    transport="UDP",
                    layer="udp_v576_decoded_candidate",
                    message_index=int(meta["message_index"]),
                    phase=phase,
                    phase_source=str(meta["phase_source"]),
                    blob=decoded,
                )
            batch.clear()
            batch_starts.clear()
            batch_messages.clear()

        with path.open("rb") as stream:
            magic = stream.read(6)
            capture_hash.update(magic)
            if magic != b"DNDP1\0":
                raise ValueError(f"not a DNDP1 capture: {path}")
            frame_index = 0
            while header := stream.read(CAPTURE_HEADER.size):
                file_offset = stream.tell() - len(header)
                if len(header) != CAPTURE_HEADER.size:
                    raise ValueError(f"short frame header in {path}")
                timestamp, adapter, original_length, length = CAPTURE_HEADER.unpack(header)
                if length > MAX_FRAME_BYTES:
                    raise ValueError(f"oversized frame in {path}")
                raw = stream.read(length)
                if len(raw) != length:
                    raise ValueError(f"short frame body in {path}")
                capture_hash.update(header)
                capture_hash.update(raw)
                frame_index += 1
                totals["capture_records"] += 1
                packet = protocol.decode_captured_frame(timestamp, raw, original_length)
                if packet is None:
                    totals["undecoded_capture_records"] += 1
                    continue
                transport = packet.transport
                direction = packet.inferred_direction()
                totals[f"{transport.lower()}_packets"] += 1
                totals[f"{transport.lower()}_payload_bytes"] += len(packet.payload)
                raw_hits = find_u32_ids(packet.payload, row_ids)
                per_capture["raw_hits"] += len(raw_hits)
                retain_hits(
                    raw_hits,
                    capture_index=capture_index,
                    frame_index=frame_index,
                    file_offset=file_offset,
                    timestamp=timestamp,
                    direction=direction,
                    transport=transport,
                    layer="transport_payload_raw",
                    message_index=None,
                    phase=None,
                    phase_source=None,
                    blob=packet.payload,
                )
                if transport != "UDP":
                    continue
                for message_index, wire in enumerate(protocol.split_v576_payload(packet.payload)):
                    totals["udp_logical_messages"] += 1
                    totals["udp_logical_message_bytes"] += len(wire)
                    expected_phase = (
                        protocol._V576_XOR_PHASE_BY_SIGNATURE_BYTE.get(wire[5])
                        if len(wire) > 5
                        else None
                    )
                    phase_source = "signature" if expected_phase is not None else "exhaustive_13"
                    totals[f"phase_source_{phase_source}_messages"] += 1
                    totals[f"phase_source_{phase_source}_bytes"] += len(wire)
                    start = len(batch)
                    batch_starts.append(start)
                    batch.extend(wire)
                    end = len(batch)
                    # Three zero bytes prevent a real four-byte candidate from
                    # spanning adjacent messages; bounds are checked as well.
                    batch.extend(b"\0\0\0")
                    batch_messages.append(
                        {
                            "start": start,
                            "end": end,
                            "frame_index": frame_index,
                            "file_offset": file_offset,
                            "timestamp": timestamp,
                            "direction": direction,
                            "message_index": message_index,
                            "expected_phase": expected_phase,
                            "phase_source": phase_source,
                        }
                    )
                    if len(batch) >= 4 * 1024 * 1024 or len(batch_messages) >= 100_000:
                        flush_udp_batch()
            flush_udp_batch()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"capture changed while reading: {path}")
        capture_rows.append(
            {
                "index": capture_index,
                "path": str(path),
                "bytes": before.st_size,
                "sha256": capture_hash.hexdigest(),
                "raw_hits": per_capture["raw_hits"],
                "decoded_candidate_hits": per_capture["decoded_hits"],
            }
        )

    return {
        "schema": "fixed-capture-rune-rowid-scan-v56",
        "fixed_manifest": str(FIXED_CAPTURE_MANIFEST),
        "fixed_manifest_sha256": digest(manifest_raw),
        "capture_count": len(paths),
        "capture_bytes": sum(row["bytes"] for row in capture_rows),
        "row_id_source": str(RESOURCE),
        "row_id_source_sha256": EXPECTED_RESOURCE_SHA256,
        "row_id_count": len(row_ids),
        "scan": {
            "raw_layer": "exact unaligned little-endian u32 scan of decoded IP transport payloads",
            "udp_layer": "exact unaligned u32 scan after each signature-selected or all 13 candidate v576 XOR phases",
            "tcp_reassembly_or_application_decryption": False,
            "field_semantics_inferred_from_integer_hit": False,
            "examples_retained_limit": MAX_CAPTURE_HIT_EXAMPLES,
        },
        "totals": dict(totals),
        "row_hit_counts": [
            {"layer": layer, "row_id": row_id, "count": count}
            for (layer, row_id), count in sorted(row_hit_counts.items())
        ],
        "captures": capture_rows,
        "examples": examples,
        "elapsed_seconds": time.perf_counter() - started,
        "evidence_limits": [
            "The RowId set comes from an older resource snapshot, not proven current content.",
            "An exact integer occurrence is a candidate, not packet-field semantics.",
            "TCP application records are not reassembled or decrypted by this scanner.",
            "All 13 UDP XOR phases are candidate decodes when the signature is unknown; chance matches are expected.",
            "Absence cannot prove the server never sent equivalent state in another encoding, version, or omitted interval.",
        ],
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-captures", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    static, old_mapping = build_static_evidence()
    write_json(OUT / "static_evidence.json", static)
    write_json(OUT / "old_resource_remap.json", old_mapping)
    if not args.skip_captures:
        capture = scan_fixed_captures({row["row_id"] for row in old_mapping["rows"]})
        write_json(OUT / "capture_scan.json", capture)
    print(
        json.dumps(
            {
                "static_anchors": len(static["anchors"]),
                "old_rows": old_mapping["row_count"],
                "capture_scan": not args.skip_captures,
                "output": str(OUT),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
