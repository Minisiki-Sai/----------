"""Hash-pinned static RTTI and bounded code-reference review; no CPU execution.

Owned outputs are damage_types*.json/md in the fixed v44 output directory.
Reads the original client as bytes, six verified decoded streams, their
manifest, postdecode_initial.json, and protocol source text for comparison.
Never loads a client/system DLL, calls a guest function, touches live memory,
replays captures, rewrites a PE, or changes any production attribution rule.
Run with Python 3.12 -B tools/review_client_damage_types_v44.py.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/client_protection_v44_20260908"
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
BASE = 0x140000000
NAMES = {".text": "text", ".rdata": "rdata", ".data": "data",
         ".pdata": "pdata", "_RDATA": "_RDATA", ".data0": "data0"}
LIMITS = dict(function_bytes=32768, function_instructions=8192,
              decoded_functions=640, total_function_bytes=8 * 1024 * 1024,
              references_per_target=6, vtable_entries=12, method_windows=72,
              context_instructions_before=4, context_instructions_after=8)
FOCUS = (
    "CDnProjectile", "CDnSkill", "CDnDamageBase", "MADamageBase",
    "MASingleDamage", "MAMultiDamage", "MASkillUser", "MAActorProp",
    "CDnActor", "CDnPlayerActor", "CDnLocalPlayerActor", "CDnMonsterActor",
    "CDnPetActor", "CDnSkillTask", "CDnDamageMng", "CDnDamageCount",
    "CDnTotalDamageMng", "CDnDamageReaction", "CDnChangeProjectileProcessor",
    "CDnSetProjectileFromSkillIDBlow", "CDnAddProjectileValidTimeBlow",
    "CDnDamageDuplicateBlow", "CDnDamageTransitionBlow", "CDnSummonChecker",
    "CDnChangeSummonMonsterAggroToSkillTargetBlow",
    "BubbleSystem::CDnUseSkillMessage", "BubbleSystem::CDnUseSkillHitMessage",
    "BubbleSystem::CDnHitByNormalAttackMessage", "BubbleSystem::CDnSkillHitByCriticalMessage",
    "CGameClientSession", "CClientSession", "CClientSocket", "CNetSocketRUDP",
    "CCryptoPacket", "CPacketCompressStream",
)
TYPE_PATTERN = re.compile(rb"\.\?A[VU][\x20-\x7e]{1,1000}?@@\x00")
DOMAIN = re.compile(r"Skill|Projectile|Damage|Attack|Actor|Entity|Summon|Owner|Packet|Socket|Session|Hit|CNet")
sys.path.insert(0, str(ROOT / ".tmp/reverse_tools_v39"))
import capstone
from capstone import x86_const as x86


def digest(data):
    return sha256(data).hexdigest()


def simple_name(decorated):
    match = re.fullmatch(r"\.\?A[VU]([A-Za-z_][A-Za-z_0-9]*(?:@[A-Za-z_][A-Za-z_0-9]*)*)@@", decorated)
    return "::".join(reversed(match[1].split("@"))) if match else None


class StaticView:
    def __init__(self):
        self.inputs = {}
        self.raw = self.load(CLIENT)
        if digest(self.raw) != EXPECTED:
            raise ValueError("original client hash mismatch")
        pe = struct.unpack_from("<I", self.raw, 0x3C)[0]
        if self.raw[:2] != b"MZ" or self.raw[pe:pe + 4] != b"PE\0\0":
            raise ValueError("invalid original PE header")
        opt = pe + 24
        machine, count = struct.unpack_from("<HH", self.raw, pe + 4)
        if machine != 0x8664 or struct.unpack_from("<H", self.raw, opt)[0] != 0x20B:
            raise ValueError("expected x64 PE32+")
        self.base = struct.unpack_from("<Q", self.raw, opt + 24)[0]
        self.size = struct.unpack_from("<I", self.raw, opt + 56)[0]
        if self.base != BASE:
            raise ValueError("unexpected preferred image base")
        section_table = opt + struct.unpack_from("<H", self.raw, pe + 20)[0]
        self.sections = []
        for i in range(count):
            at = section_table + i * 40
            virtual, rva, raw_size, raw_offset = struct.unpack_from("<IIII", self.raw, at + 8)
            self.sections.append(dict(name=self.raw[at:at + 8].rstrip(b"\0").decode("ascii"),
                                      rva=rva, virtual_size=virtual, raw_size=raw_size, raw_offset=raw_offset))
        manifest = json.loads(self.load(OUT / "segments_manifest.json"))
        self.manifest = manifest
        if manifest["client_sha256"] != EXPECTED or manifest["client_file_bytes"] != len(self.raw):
            raise ValueError("manifest provenance mismatch")
        if len(manifest["segments"]) != 6 or {s["section"] for s in manifest["segments"]} != set(NAMES):
            raise ValueError("expected six decoded streams")
        self.segments = {}
        self.streams = []
        for item in manifest["segments"]:
            name = item["section"]
            if item["path"] != f"segments/{NAMES[name]}.bin":
                raise ValueError("unexpected segment input path")
            data = self.load(OUT / item["path"])
            original = next(s for s in self.sections if s["name"] == name)
            if (len(data) != item["decoded_bytes"] or digest(data) != item["decoded_sha256"]
                    or item["destination_rva"] != original["rva"]
                    or item["virtual_size"] != original["virtual_size"]
                    or any(data[item["virtual_size"]:])):
                raise ValueError("decoded stream identity/mapping/padding mismatch")
            available = min(len(data), item["virtual_size"])
            self.segments[name] = (item["destination_rva"], data[:available])
            self.streams.append(dict(item, examined_available_bytes=available))
        if sum(s["decoded_bytes"] for s in self.streams) != manifest["total_decoded_bytes"]:
            raise ValueError("total stream size mismatch")
        prior = json.loads(self.load(OUT / "postdecode_initial.json"))
        if (prior["client_sha256"] != EXPECTED or
                prior["segment_manifest_sha256"] != self.inputs[str(OUT / "segments_manifest.json")]):
            raise ValueError("postdecode report provenance mismatch")
        rva, size = struct.unpack_from("<II", self.raw, opt + 112 + 3 * 8)
        if (rva, size) != (0x2DDD290, 563484):
            raise ValueError("unexpected original exception directory")
        directory = self.file_read(rva, size)
        self.functions = list(struct.iter_unpack("<III", directory))
        self.starts = [a for a, b, c in self.functions]
        if (len(self.functions) != 46957 or self.starts != sorted(self.starts)
                or not all(0 < a < b <= self.size and 0 < c < self.size for a, b, c in self.functions)
                or any(a[1] > b[0] for a, b in zip(self.functions, self.functions[1:]))):
            raise ValueError("exception function intervals are not valid sorted disjoint ranges")
        old = prior["exception_directory"]
        if (old["sha256"] != digest(directory) or old["rva"] != hex(rva)
                or old["size"] != size or old["record_count"] != len(self.functions)):
            raise ValueError("exception directory does not match initial report")
        self.exception = dict(rva=rva, size=size, sha256=digest(directory),
                              source="original file-backed .data2", valid_sorted_disjoint_ranges=len(self.functions),
                              source_file_offset=self.file_offset(rva, size))

    def load(self, path):
        raw = path.read_bytes()
        self.inputs[str(path)] = digest(raw)
        return raw

    def section(self, rva):
        return next((s for s in self.sections if s["rva"] <= rva < s["rva"] + max(s["virtual_size"], s["raw_size"])), None)

    def file_offset(self, rva, size):
        section = self.section(rva)
        if section is None or size < 0 or rva + size > section["rva"] + section["raw_size"]:
            raise ValueError("range is not wholly original-file-backed")
        at = section["raw_offset"] + rva - section["rva"]
        if at + size > len(self.raw):
            raise ValueError("original file range overrun")
        return at

    def file_read(self, rva, size):
        at = self.file_offset(rva, size)
        return self.raw[at:at + size]

    def read(self, rva, size):
        section = self.section(rva)
        if section and section["name"] in self.segments:
            base, data = self.segments[section["name"]]
            offset = rva - base
            if not 0 <= offset <= offset + size <= len(data):
                raise ValueError("unknown decoded bytes; zero filling is forbidden")
            return data[offset:offset + size]
        return self.file_read(rva, size)

    def function(self, rva):
        i = bisect_right(self.starts, rva) - 1
        return self.functions[i] if i >= 0 and rva < self.functions[i][1] else None

    def provenance(self, rva, size):
        section = self.section(rva)
        result = dict(rva=rva, section=section["name"] if section else None, byte_count=size)
        if section and section["name"] in self.segments:
            result.update(source=f"segments/{NAMES[section['name']]}.bin", segment_offset=rva-section["rva"])
        else:
            result.update(source="original client file", file_offset=self.file_offset(rva, size))
        return result


def extract_types(view):
    result, counts = {}, {}
    for section, (base, data) in view.segments.items():
        matches = list(TYPE_PATTERN.finditer(data))
        counts[section] = len(matches)
        for match in matches:
            offset = match.start() - 16
            if offset < 0:
                continue
            vptr, spare = struct.unpack_from("<QQ", data, offset)
            decorated = match.group()[:-1].decode("ascii")
            descriptor = base + offset
            vptr_section = view.section(vptr - BASE)
            result[descriptor] = dict(type_descriptor_rva=descriptor, name_rva=base+match.start(),
                decorated_name=decorated, simple_name=simple_name(decorated),
                header_bytes=data[offset:offset+16].hex(), type_info_vtable_va=vptr,
                spare=spare, header_consistent=(descriptor % 8 == 0 and spare == 0
                                              and vptr_section is not None and vptr_section["name"] == ".rdata"),
                provenance=view.provenance(descriptor, match.end()-offset),
                bytes_sha256=digest(data[offset:match.end()]))
    return result, counts


def hierarchy(view, rva, types):
    signature, attributes, count, array = struct.unpack("<IIII", view.read(rva, 16))
    if signature != 0 or attributes & ~7 or not 1 <= count <= 256:
        raise ValueError("implausible class hierarchy header")
    descriptors = struct.unpack("<" + "I" * count, view.read(array, count * 4))
    bases = []
    for address in descriptors:
        td, contained, mdisp, pdisp, vdisp, attrs = struct.unpack("<IIiiiI", view.read(address, 24))
        if td not in types or not types[td]["header_consistent"] or contained >= count:
            raise ValueError("base descriptor does not link to a validated type header")
        bases.append(dict(base_descriptor_rva=address, type_descriptor_rva=td,
                          name=types[td]["simple_name"] or types[td]["decorated_name"],
                          contained_base_count=contained, member_displacement=mdisp,
                          vbtable_displacement=pdisp, vbase_displacement=vdisp, attributes=attrs))
    return dict(rva=rva, signature=signature, attributes=attributes, base_count=count,
                base_array_rva=array, bases=bases)


def link_rtti(view, types, selected):
    base, data = view.segments[".rdata"]
    locators = defaultdict(list)
    hierarchy_cache = {}
    rejected = Counter()
    for match in re.finditer(b"\x01\x00\x00\x00", data):
        at = match.start()
        if at % 4 or at + 24 > len(data):
            continue
        signature, offset, cd_offset, td, chd, self_rva = struct.unpack_from("<6I", data, at)
        if td not in selected or self_rva != base + at:
            continue
        try:
            if chd not in hierarchy_cache:
                hierarchy_cache[chd] = hierarchy(view, chd, types)
            tree = hierarchy_cache[chd]
            if tree["bases"][0]["type_descriptor_rva"] != td:
                raise ValueError("hierarchy root disagrees with locator type")
        except (ValueError, struct.error) as exc:
            rejected[str(exc)] += 1
            continue
        locator = dict(rva=self_rva, signature=signature, subobject_offset=offset,
                       construction_displacement=cd_offset, type_descriptor_rva=td,
                       hierarchy_rva=chd, raw=data[at:at+24].hex(), vtables=[])
        needle = struct.pack("<Q", BASE + self_rva)
        for pointer in re.finditer(re.escape(needle), data):
            slot = pointer.start()
            if (base + slot) % 8:
                continue
            entries = []
            for i in range(LIMITS["vtable_entries"]):
                position = slot + 8 + i * 8
                if position + 8 > len(data):
                    break
                target = struct.unpack_from("<Q", data, position)[0] - BASE
                target_section = view.section(target)
                if target_section is None or target_section["name"] not in (".text", ".data0", ".data2"):
                    break
                function = view.function(target)
                entries.append(dict(slot=i, slot_rva=base+position, target_rva=target,
                                    target_section=target_section["name"],
                                    runtime_function=list(function) if function else None,
                                    exact_runtime_function_start=bool(function and function[0] == target)))
            if entries:
                locator["vtables"].append(dict(rva=base+slot+8, locator_pointer_rva=base+slot,
                                                  entries=entries, entry_limit=LIMITS["vtable_entries"],
                                                  truncated_or_exact_size_unknown=True))
        locators[td].append(locator)
    return locators, hierarchy_cache, dict(rejected)


def supporting_strings(view):
    result = []
    pattern = re.compile(r"attack.*result|result.*attack|damageobject|projectile|(?:Send|Recv|Process).*(?:Damage|Attack|Hit)", re.I)
    for section in (".rdata", ".data"):
        base, data = view.segments[section]
        for encoding, expression in (("ascii", rb"[\x20-\x7e]{5,512}"),
                                     ("utf-16le", rb"(?:[\x20-\x7e]\x00){5,512}")):
            width = 1 if encoding == "ascii" else 2
            for match in re.finditer(expression, data):
                text = match.group().decode(encoding)
                if (not pattern.search(text) or text.startswith(".?A")
                        or re.search(r"[\\/]|\.(?:act|dnt|dds|pak)\b|weapontable", text, re.I)):
                    continue
                result.append(dict(text=text, rva=base+match.start(), encoding=encoding,
                                   null_terminated=data[match.end():match.end()+width] == b"\0"*width,
                                   provenance=view.provenance(base+match.start(), len(match.group())),
                                   raw=match.group().hex()))
    return result


class CodeReview:
    def __init__(self, view):
        self.view = view
        self.dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.dis.detail = True
        self.cache, self.stats = {}, Counter()

    def decode(self, function):
        start, end, unwind = function
        if start in self.cache:
            return self.cache[start]
        if (len(self.cache) >= LIMITS["decoded_functions"]
                or self.stats["function_bytes"] + min(end-start, LIMITS["function_bytes"]) > LIMITS["total_function_bytes"]):
            self.stats["function_budget_skips"] += 1
            return []
        size = min(end - start, LIMITS["function_bytes"])
        body = self.view.read(start, size)
        instructions = list(self.dis.disasm(body, BASE+start, count=LIMITS["function_instructions"]))
        self.cache[start] = instructions
        self.stats["function_bytes"] += size
        self.stats["instructions"] += len(instructions)
        if not instructions or instructions[-1].address + instructions[-1].size < BASE + end:
            self.stats["incomplete_function_decodes"] += 1
        return instructions

    @staticmethod
    def row(ins):
        result = dict(rva=ins.address-BASE, bytes=ins.bytes.hex(), mnemonic=ins.mnemonic, operands=ins.op_str)
        rip = [ins.address+ins.size+op.mem.disp-BASE for op in ins.operands
               if op.type == x86.X86_OP_MEM and op.mem.base == x86.X86_REG_RIP]
        if rip:
            result["rip_relative_targets"] = rip
        if (ins.operands and ins.operands[0].type == x86.X86_OP_IMM
                and (ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP))):
            result["direct_branch_target"] = ins.operands[0].imm-BASE
        return result

    def references(self, targets):
        base, data = self.view.segments[".text"]
        references, counts, retained = [], Counter(), Counter()
        seen = set()
        # This is a candidate prefilter, not an instruction-boundary claim.
        # Cover RIP-relative LEA/MOV (REX or no REX), then verify each candidate
        # by decoding from an ORIGINAL exception-directory function boundary.
        expression = rb"[\x40-\x4f]?[\x8d\x8b\x89][\x05\x0d\x15\x1d\x25\x2d\x35\x3d][\x00-\xff]{4}"
        for match in re.finditer(expression, data):
            at = base + match.start()
            target = base + match.end() + struct.unpack_from("<i", match.group(), len(match.group())-4)[0]
            if target not in targets or (at, target) in seen:
                continue
            seen.add((at, target))
            counts[target] += 1
            if retained[target] >= LIMITS["references_per_target"]:
                self.stats["raw_candidate_output_limit_skips"] += 1
                continue
            function = self.view.function(at)
            if not function or not base <= function[0] < function[1] <= base+len(data):
                self.stats["candidate_without_available_text_runtime_range"] += 1
                continue
            instructions = self.decode(function)
            found = next((i for i, ins in enumerate(instructions) if ins.address == BASE+at), None)
            if found is None:
                self.stats["candidate_not_confirmed_at_instruction_boundary"] += 1
                continue
            row = self.row(instructions[found])
            if target not in row.get("rip_relative_targets", ()):
                self.stats["candidate_operand_mismatch"] += 1
                continue
            self.stats["verified_code_references"] += 1
            retained[target] += 1
            context = instructions[max(0, found-LIMITS["context_instructions_before"]):found+LIMITS["context_instructions_after"]+1]
            references.append(dict(target_rva=target, labels=targets[target], instruction=row,
                function_begin_rva=function[0], function_end_rva=function[1], unwind_rva=function[2],
                exception_record_rva=self.view.exception["rva"] + self.view.starts.index(function[0])*12,
                boundary_basis="linear decode from original RUNTIME_FUNCTION begin; no execution/reachability proof",
                context=[self.row(ins) for ins in context]))
        self.stats["raw_prefilter_candidates"] = sum(counts.values())
        return references, {hex(k): v for k, v in counts.items()}

    def method_windows(self, focus, by_name):
        result, seen = [], set()
        for name in focus:
            item = by_name.get(name)
            if not item:
                continue
            # Only primary vtables: secondary subobjects are documented above.
            for locator in item["locators"]:
                if locator["subobject_offset"]:
                    continue
                for table in locator["vtables"]:
                    for entry in table["entries"][:4]:
                        function = entry["runtime_function"]
                        if (not entry["exact_runtime_function_start"] or entry["target_section"] != ".text"
                                or entry["target_rva"] in seen or len(result) >= LIMITS["method_windows"]):
                            continue
                        seen.add(entry["target_rva"])
                        instructions = self.decode(tuple(function))
                        result.append(dict(type=name, vtable_rva=table["rva"], slot=entry["slot"],
                            function_begin_rva=function[0], function_end_rva=function[1],
                            instructions=[self.row(ins) for ins in instructions[:32]],
                            total_decoded_instructions=len(instructions),
                            caveat="vtable slot association only; method names and wire layouts are unproven"))
        return result


def focused_field_review(view, code, focus):
    """A fixed, reviewable handoff of observed instruction/data-flow evidence.

    Addresses were selected from RTTI-backed vtables and direct calls. Pin
    the relevant instructions as part of the report, rejecting stale ranges.
    No field is renamed to a packet field or a named-skill ID.
    """
    by_name = {item["simple_name"]: item for item in focus}
    contracts = (
        dict(id="P1", type="CDnChangeProjectileProcessor", function=0x29D280,
             evidence_rvas=[0x29D2C5, 0x29D2CC, 0x29D2CF, 0x29D2D2, 0x29D2D7, 0x29D2DB, 0x29D2E3],
             facts="Writes the RTTI-linked primary vtable at object+0; initializes dwords +0x30/+0x34 and qwords +0x38/+0x40/+0x48.",
             inference="Construction/initialization path for CDnChangeProjectileProcessor; +0x30/+0x34 meanings remain unknown."),
        dict(id="P2", type="CDnChangeProjectileProcessor", function=0x29D5E0,
             evidence_rvas=[0x29D5ED, 0x29D60A, 0x29D615, 0x29D61A, 0x29D65B, 0x29D65E,
                            0x29D664, 0x29D69B, 0x29D69E, 0x29D6A1, 0x29D6AB, 0x29D6AF],
             facts="Primary vtable slot 1: tests +0x38; passes 0x2110 to a direct call; calls 0xa8d190 with the returned pointer; saves that result at +0x38; passes dword +0x34 in EDX to 0xc6fae0 with the new object in RCX.",
             inference="A concrete processor-to-projectile construction/configuration path. +0x34 is a lookup/initialization input candidate, not a proven skill ID or wire template ID."),
        dict(id="P3", type="CDnProjectile", function=0xA8D190,
             evidence_rvas=[0xA8D1B2, 0xA8D1F5, 0xA8D1FB, 0xA8D202, 0xA8D20C, 0xA8D217, 0xA8D222],
             facts="The direct callee in P2 writes primary vtable 0xf28730 at object+0 and RTTI-linked secondary vtables at +0x28/+0x30/+0x1100, after a direct call to 0xc69eb0.",
             inference="RTTI plus actual vtable stores identify this as a CDnProjectile construction path. This establishes object type, not ownership or a UDP registration layout."),
        dict(id="P4", type="CDnProjectile", function=0xC6FAE0,
             evidence_rvas=[0xC6FB04, 0xC6FB06, 0xC6FB09, 0xC6FB1C, 0xC6FB28,
                            0xC6FB39, 0xC6FB42, 0xC6FB4F, 0xC6FB5B, 0xC6FB6D, 0xC6FB8F],
             facts="The next direct callee in P2 stores incoming EDX to object+0x1164, reuses it as a lookup argument, then writes lookup-derived values at +0x17b0 and +0x17bc (with additional field writes retained in JSON).",
             inference="Bounded configuration-field flow. The same function references _EquipType/_Durability/_SetItemID, suggesting an inherited weapon/item configuration path. Do not rename +0x1164 to projectile-template ID or skill ID; neither mapping is proved."),
        dict(id="P5", type="CDnSetProjectileFromSkillIDBlow", function=0x31C850,
             evidence_rvas=[0x31C87D, 0x31C882, 0x31C889, 0x31C890],
             facts="Writes this type's vtable 0xe95b68 and internal dword +0x40 = 0x28f.",
             inference="A named skill-to-projectile modifier exists; the stored value is an internal tag candidate. It is not a proven packet opcode, template ID or skill ID."),
        dict(id="P6", type="CDnAddProjectileValidTimeBlow", function=0x27D0C0,
             evidence_rvas=[0x27D0F9, 0x27D100, 0x27D10A, 0x27D10D, 0x27D11A, 0x27D121],
             facts="Writes this type's vtable 0xe801f8, internal dword +0x40 = 0x28b, converts XMM0 double to float, and stores the float at object+0x13c.",
             inference="Useful lifetime-modifier clue for expired-registration gaps; duration units and expiry behavior require additional evidence."),
        dict(id="N1", type="CGameClientSession", function=0xCB7510,
             evidence_rvas=[0xCB7539, 0xCB7543, 0xCB7546, 0xCB7549, 0xCB756E, 0xCB7582, 0xCB7586, 0xCB7589],
             facts="Primary vtable slot 1 saves incoming EDX/R8D/R9 and tests (saved EDX - 3) against 0x29; conditional branches leave this original runtime-function range.",
             inference="A bounded session dispatch candidate. No selector has been identified as an attack result, and no UDP opcode/field has been decoded here."),
    )
    checks = {0x29D65E: ("direct_branch_target", 0xA8D190),
              0x29D6A1: ("direct_branch_target", 0xC6FAE0),
              0xA8D1FB: ("rip_relative_targets", [0xF28730]),
              0x29D2C5: ("rip_relative_targets", [0xE84EC0]),
              0x31C882: ("rip_relative_targets", [0xE95B68]),
              0x27D0F9: ("rip_relative_targets", [0xE801F8])}
    results = []
    for contract in contracts:
        start = contract["function"]
        function = view.function(start)
        if not function or function[0] != start:
            raise ValueError("handoff entry is not an exact original exception-directory start")
        instructions = code.decode(function)
        rows = [code.row(ins) for ins in instructions]
        by_rva = {row["rva"]: row for row in rows}
        if not set(contract["evidence_rvas"]).issubset(by_rva):
            raise ValueError("handoff instructions are unavailable within original function range")
        for at in contract["evidence_rvas"]:
            if at in checks:
                key, expected = checks[at]
                if by_rva[at].get(key) != expected:
                    raise ValueError("handoff direct call/vtable target changed")
        item = by_name[contract["type"]]
        primary = [table for loc in item["locators"] if loc["subobject_offset"] == 0 for table in loc["vtables"]]
        associations = [dict(vtable_rva=table["rva"], slot=entry["slot"]) for table in primary
                        for entry in table["entries"] if entry["target_rva"] == start]
        literals = []
        for row in rows:
            for target in row.get("rip_relative_targets", ()):
                section = view.section(target)
                if not section or section["name"] != ".rdata":
                    continue
                raw = view.read(target, min(160, section["rva"]+section["virtual_size"]-target))
                for encoding, pattern in (("ascii", rb"[\x20-\x7e]{3,}\x00"),
                                           ("utf-16le", rb"(?:[\x20-\x7e]\x00){3,}\x00\x00")):
                    match = re.match(pattern, raw)
                    if match:
                        width = 1 if encoding == "ascii" else 2
                        value = match.group()[:-width].decode(encoding)
                        if not re.search(r"[\\/]|\.(?:pak|act|dnt)\b", value, re.I):
                            literals.append(dict(reference_rva=row["rva"], target_rva=target,
                                                 text=value, encoding=encoding, raw=match.group().hex()))
                        break
        results.append(dict(contract, function_begin_rva=start, function_end_rva=function[1],
            unwind_rva=function[2], exception_record_rva=view.exception["rva"]+bisect_right(view.starts,start)*12-12,
            function_bytes_sha256=digest(view.read(start, function[1]-start)),
            evidence_instructions=[by_rva[at] for at in contract["evidence_rvas"]],
            type_descriptor_rva=item["type_descriptor_rva"], vtable_slot_associations=associations,
            instructions=rows[:512], output_instruction_limit=512, decoded_instruction_count=len(rows),
            literal_references=literals,
            boundary="Exact original exception-directory interval, bounded static decoding; no execution or wire semantics proof."))
    return results


def non_destructor_review(view, code, focus):
    """Only selected vtable slots; no further RTTI inventory or global scan."""
    by_name = {item["simple_name"]: item for item in focus}

    def table_for(name, rva):
        for locator in by_name[name]["locators"]:
            for table in locator["vtables"]:
                if table["rva"] == rva:
                    return locator, table
        raise ValueError("selected method lacks RTTI-to-vtable evidence")

    def slot(name, table_rva, index, expected):
        locator, table = table_for(name, table_rva)
        pointer_rva = table_rva + index*8
        raw = view.read(pointer_rva, 8)
        target = struct.unpack("<Q", raw)[0] - BASE
        if target != expected:
            raise ValueError("selected vtable method changed")
        return dict(type=name, vtable_rva=table_rva, slot=index, slot_pointer_rva=pointer_rva,
                    slot_pointer_bytes=raw.hex(), target_rva=target, locator_rva=locator["rva"],
                    subobject_offset=locator["subobject_offset"])

    def bounded_function(start, evidence):
        function = view.function(start)
        if not function or function[0] != start:
            raise ValueError("selected non-destructor body has no exact original exception entry")
        rows = [code.row(ins) for ins in code.decode(function)]
        by_rva = {row["rva"]: row for row in rows}
        if not set(evidence).issubset(by_rva):
            raise ValueError("selected field evidence crossed available runtime-function bounds")
        return dict(function_begin_rva=start, function_end_rva=function[1], unwind_rva=function[2],
                    exception_record_rva=view.exception["rva"]+(bisect_right(view.starts,start)-1)*12,
                    bytes_sha256=digest(view.read(start, function[1]-start)),
                    evidence_instructions=[by_rva[at] for at in evidence],
                    instructions=rows[:384], instruction_limit=384,
                    decoded_instruction_count=len(rows))

    def exact_leaf(at, expected):
        raw = bytes.fromhex(expected)
        if view.function(at) is not None or view.read(at, len(raw)) != raw:
            raise ValueError("expected unlisted leaf/thunk bytes changed")
        rows = [code.row(ins) for ins in code.dis.disasm(raw, BASE+at)]
        if sum(len(bytes.fromhex(row["bytes"])) for row in rows) != len(raw):
            raise ValueError("leaf sample is not fully instruction-decodable")
        return dict(rva=at, bytes=raw.hex(), byte_count=len(raw), original_runtime_function=None,
                    instructions=rows,
                    boundary="No exception-directory entry at this pointer. Exact bounded leaf/thunk bytes only; no invented function interval.")

    cases = []
    cases.append(dict(id="V1", priority="high: direct object field accessor pair",
        associations=[slot("CDnProjectile", 0xF28730, 5, 0xA94C50), slot("CDnProjectile", 0xF28730, 6, 0xA91640)],
        leaf_samples=[exact_leaf(0xA94C50, "8991c01b0000c3"), exact_leaf(0xA91640, "8b81c01b0000c3")],
        functions=[], observed="Slot 5 stores EDX to [RCX+0x1bc0]; slot 6 returns that dword in EAX. Both end in RET without a deletion path.",
        next_step="Trace callers or indirect use of this exact setter/getter pair to identify what populates +0x1bc0. Its role as skill ID, object ID, template ID or flags is currently unknown."))
    cases.append(dict(id="V2", priority="high: concrete multi-damage entry selector/state update",
        associations=[slot("MAMultiDamage", 0xEC2548, 4, 0x56508C)],
        leaf_samples=[exact_leaf(0x56508C, "486341fc482bc8e9f8bde0ff")],
        functions=[bounded_function(0x370E90, [0x370EAE, 0x370EB1, 0x370F03, 0x370F11,
                    0x370F32, 0x370F3E, 0x370F58, 0x370F69, 0x370F70, 0x370FAD, 0x370FE6])],
        observed="Slot 4 adjusts RCX using [RCX-4], then jumps to 0x370e90. That body walks entries with stride 0x308, matches input R8D at entry+0x178, replaces entry+0x180 with input R9D, and branches on old/new signs to calls 0x371330/0x3719f0. This is state-processing code, not destructor-shaped code.",
        next_step="Trace the source of R8D/R9D and the virtual call through +0x318. This can clarify damage-part/entity state; it does not yet establish a Hit's skill or owner."))
    cases.append(dict(id="V3", priority="medium: projectile reference/lifecycle processing",
        associations=[slot("CDnProjectile", 0xF28730, 2, 0xA934D0)], leaf_samples=[],
        functions=[bounded_function(0xA934D0, [0xA93503, 0xA93506, 0xA93512, 0xA93525,
                    0xA93532, 0xA9354A, 0xA93556, 0xA9355D, 0xA935BB, 0xA935C7, 0xA935CE])],
        observed="Uses input RDX and stored +0x1c80 to compute a floating value at +0x1efc; processes reference pairs +0x1f60/+0x1f68 and +0x1ed8/+0x1ee0 through 0x272140. No constructor vtable store is used as binding evidence.",
        next_step="Follow initialization of these reference pairs and the +0x1c80 value for parent/target/lifetime clues; exact reference roles and time units remain unproved."))
    cases.append(dict(id="V4", priority="medium: projectile lifecycle membership",
        associations=[slot("CDnProjectile", 0xF28730, 3, 0xA94C60)], leaf_samples=[],
        functions=[bounded_function(0xA94C60, [0xA94C75, 0xA94C99, 0xA94CD8, 0xA94D00,
                    0xA94D05, 0xA94D24, 0xA94D27])],
        observed="Compares/writes byte +0x20 and removes/adds this object pointer in a global contiguous pointer collection. This ordinary state toggle is separate from the deleting-destructor slot.",
        next_step="Check caller timing for object activation/removal and reused-object handling. Collection membership is not wire registration or ownership proof."))
    set_values = struct.unpack("<32Q", view.read(0xE95B68, 32*8))
    time_values = struct.unpack("<32Q", view.read(0xE801F8, 32*8))
    same = [i for i in range(1, 32) if set_values[i] == time_values[i]]
    if same != list(range(1, 32)):
        raise ValueError("the bounded comparison of shared Blow methods changed")
    cases.append(dict(id="V5", priority="low as type-specific binding; shared state helpers only",
        associations=[slot("CDnSetProjectileFromSkillIDBlow", 0xE95B68, 22, 0x28F300),
                      slot("CDnSetProjectileFromSkillIDBlow", 0xE95B68, 24, 0x28F370)],
        leaf_samples=[], functions=[bounded_function(0x28F300, [0x28F306, 0x28F310]),
                                   bounded_function(0x28F370, [0x28F376, 0x28F37C, 0x28F37F, 0x28F389])],
        comparison=dict(other_type="CDnAddProjectileValidTimeBlow", other_vtable_rva=0xE801F8,
                        compared_slots=32, identical_slots=same,
                        set_slots=[value-BASE for value in set_values],
                        other_slots=[value-BASE for value in time_values]),
        observed="All inspected slots 1..31 are identical to CDnAddProjectileValidTimeBlow. Slot 24 copies [RDX+0x28] to this+0x68 and compares this+0xa0/+0x134. Both exception-directory fragments end before all outgoing paths are visible. Slot 1 (0x760c0) calls slot 0 with EDX=1, so it belongs to the deletion wrapper path, not skill binding.",
        next_step="The constructor's +0x40=0x28f distinguishes the type, but no unique non-destructor skill-binding method was found in this 32-slot prefix. Trace consumers of this internal tag; do not promote construction to successful skill binding."))
    cases.append(dict(id="V6", priority="low: no useful unique MASingleDamage non-destructor in inspected table",
        associations=[slot("MASingleDamage", 0xEC2518, 1, 0x46620),
                      slot("MASingleDamage", 0xEC2518, 2, 0x75F70),
                      slot("MASingleDamage", 0xEC2518, 3, 0xDD0EC0)],
        leaf_samples=[exact_leaf(0x46620, "c20000"), exact_leaf(0x75F70, "b8ffffffffc3")],
        functions=[bounded_function(0xDD0EC0, [0xDD0EC4, 0xDD0ECE, 0xDD0ED4, 0xDD0ED9])],
        observed="Slots 1/4 return immediately; slot 2 returns -1. Slot 3 is shared with MADamageBase/MAMultiDamage and leads to calls followed by INT3, so it is a failure-like helper rather than an identified damage/skill routine. Slot 0 is the deleting-destructor candidate and is not prioritized.",
        next_step="Prioritize MAMultiDamage slot 4 and concrete CDnProjectile accessors. Do not invent a unique MASingleDamage skill-binding method from these shared stubs."))
    return cases


def protocol_comparison(view):
    path = ROOT / "dragon_nest_builder/live_dps/protocol_v576.py"
    source = view.load(path).decode("utf-8-sig")
    lines = source.splitlines()
    names = ("decode_v576_damage_object_registration", "decode_v576_server_damage_object_registrations",
             "decode_v576_projectile_damage")
    result = []
    for name in names:
        start = next(i for i, line in enumerate(lines) if line.startswith("def " + name + "("))
        end = next((i for i in range(start+1, len(lines)) if lines[i].startswith("def ")), len(lines))
        selected = [dict(line=i+1, text=lines[i]) for i in range(start, min(end, start+130))
                    if i < start+20 or re.search(r"source_actor_id|damage_object_id|projectile_template_id|damage_result_group_id|parent_|marker_offset|primary_markers", lines[i])]
        result.append(dict(path=str(path), function=name, line=start+1, excerpts=selected,
                           status="existing parser comparison only; no client-symbol-to-wire mapping established"))
    return result


def markdown(report):
    summary = report["summary"]
    lines = ["# Static damage/projectile RTTI review", "",
        f"Client SHA-256: `{EXPECTED}`.", "",
        f"Found {summary['all_type_name_candidates']} RTTI name candidates; "
        f"{summary['focus_types_found']}/{summary['focus_types_requested']} requested domain types found. "
        f"{summary['validated_locators']} complete-object locators and {summary['vtable_candidates']} vtable candidates link to the selected types.", "",
        "These are hash-pinned static clues. No named-Hit mapping, measured coverage improvement, packet offset, or ownership rule is established by RTTI alone.", "",
        "## Non-destructor methods: prioritized handoff", "",
        "Slot indices below are zero-based. Construction and vtable writes are type evidence only, not proof that skill binding succeeded. Unlisted leaf/thunk samples are explicitly distinguished from real exception-directory function ranges.", ""]
    for item in report["non_destructor_review"]:
        lines += [f"### {item['id']}: {item['priority']}", ""]
        for association in item["associations"]:
            lines.append(f"- `{association['type']}` vtable `{association['vtable_rva']:#x}`, slot {association['slot']} -> `{association['target_rva']:#x}` (COL subobject offset `{association['subobject_offset']:#x}`).")
        lines += ["", "Observed: " + item["observed"], "", "Next bounded check: " + item["next_step"], ""]
        for sample in item["leaf_samples"]:
            lines += [f"Exact {sample['byte_count']}-byte leaf/thunk sample at `{sample['rva']:#x}`; **no exception-directory entry**, no claimed function range:", "", "```text"]
            lines += [f"{r['rva']:08x}  {r['bytes']:<24} {r['mnemonic']} {r['operands']}" for r in sample["instructions"]]
            lines += ["```", ""]
        for function in item["functions"]:
            lines += [f"Original exception range `{function['function_begin_rva']:#x}..{function['function_end_rva']:#x}`, record `{function['exception_record_rva']:#x}`:", "", "```text"]
            lines += [f"{r['rva']:08x}  {r['bytes']:<24} {r['mnemonic']} {r['operands']}" for r in function["evidence_instructions"]]
            lines += ["```", ""]
    lines += ["## Priority handoff: concrete functions and fields", "",
        "The strongest path is P2 -> P3 -> P4: a RTTI-linked processor method creates a RTTI-linked projectile, then copies the processor's +0x34 value into the projectile's +0x1164 configuration path. Start with those exact function ranges.", ""]
    for item in report["focused_field_review"]:
        lines += [f"### {item['id']}: {item['type']}", "",
                  f"Original runtime-function range: `{item['function_begin_rva']:#x}..{item['function_end_rva']:#x}`; exception record `{item['exception_record_rva']:#x}`; unwind `{item['unwind_rva']:#x}`.", "",
                  "Observed: " + item["facts"], "", "Interpretation/limit: " + item["inference"], "",
                  "```text"]
        for row in item["evidence_instructions"]:
            lines.append(f"{row['rva']:08x}  {row['bytes']:<24} {row['mnemonic']} {row['operands']}")
        lines += ["```", ""]
        if item["literal_references"]:
            lines.append("Literal property-name references in this bounded function: " + "; ".join(
                f"`{r['text']}` at `{r['target_rva']:#x}` (instruction `{r['reference_rva']:#x}`)"
                for r in item["literal_references"]) + ". These do not establish packet fields.")
            lines.append("")
    lines += ["## Selected types and concrete addresses", "",
        "| Type | Type descriptor RVA | Locators | Primary vtable(s) RVA | Verified static references |",
        "|---|---:|---:|---|---:|"]
    for item in report["focus_types"]:
        primary = [hex(v["rva"]) for loc in item["locators"] if loc["subobject_offset"] == 0 for v in loc["vtables"]]
        count = sum(any(label["type"] == item["simple_name"] for label in ref["labels"]) for ref in report["code_references"])
        lines.append(f"| {item['simple_name']} | `{item['type_descriptor_rva']:#x}` | {len(item['locators'])} | {', '.join(primary) or 'none recovered'} | {count} |")
    lines += ["", "## Inheritance evidence", "",
              "The following is a filtered flattened base-class list, not a claim of direct inheritance. PMD tuples are (mdisp, pdisp, vdisp). When pdisp is not -1 the virtual-base location requires a vbtable lookup; mdisp alone is not an object offset. None are packet field offsets.", ""]
    for name in ("CDnProjectile", "CDnSkill", "CDnDamageBase", "CDnActor", "CGameClientSession"):
        item = next((t for t in report["focus_types"] if t["simple_name"] == name), None)
        if not item or not item["locators"]:
            continue
        tree = report["hierarchies"][item["locators"][0]["hierarchy_rva"]]
        visible = [b for b in tree["bases"] if b["name"] in FOCUS or b["name"] in
                   ("CDnWeapon", "CDnItem", "CDnState", "MIInventoryItem", "GiveOwnerShip", "CTaskListener", "MessageListener")]
        lines.append(f"- `{name}`: " + "; ".join(
            f"`{b['name']}` PMD `({b['member_displacement']:#x}, {b['vbtable_displacement']}, {b['vbase_displacement']:#x})`"
            for b in visible) + ".")
    lines += ["", "## Bounded code reference examples", "",
              "Each instruction below was decoded from an original `.data2` exception-directory function start. Context bytes and all retained references are in the JSON. Names describe the target metadata, not an inferred function name.", ""]
    for name in FOCUS:
        refs = [r for r in report["code_references"] if any(label["type"] == name for label in r["labels"])]
        if not refs:
            continue
        lines.append(f"### {name}")
        lines.append("")
        for ref in refs[:2]:
            row = ref["instruction"]
            lines.append(f"- Function `{ref['function_begin_rva']:#x}..{ref['function_end_rva']:#x}`; reference `{row['rva']:#x}`: `{row['mnemonic']} {row['operands']}` -> `{ref['target_rva']:#x}`.")
        lines.append("")
    lines += ["## Scope and limitations", ""] + ["- " + note for note in report["limitations"]]
    return "\n".join(lines) + "\n"


def analyze():
    started = time.monotonic()
    view = StaticView()
    view.load(Path(__file__).resolve())
    types, counts = extract_types(view)
    selected = {rva: item for rva, item in types.items() if item["simple_name"] in FOCUS and item["header_consistent"]}
    locators, trees, rejected = link_rtti(view, types, selected)
    focus = [dict(selected[rva], locators=locators[rva]) for name in FOCUS
             for rva in selected if selected[rva]["simple_name"] == name]
    strings = supporting_strings(view)
    targets = defaultdict(list)
    for item in focus:
        name = item["simple_name"]
        targets[item["type_descriptor_rva"]].append(dict(type=name, kind="type_descriptor"))
        for locator in item["locators"]:
            for table in locator["vtables"]:
                targets[table["rva"]].append(dict(type=name, kind="vtable", subobject_offset=locator["subobject_offset"]))
    for item in strings:
        targets[item["rva"]].append(dict(type=item["text"], kind="supporting_string"))
    print(json.dumps(dict(stage="rtti_linked", all_names=len(types), focus_types=len(focus),
                          locators=sum(map(len, locators.values())), code_targets=len(targets))), flush=True)
    code = CodeReview(view)
    field_review = focused_field_review(view, code, focus)
    ordinary_methods = non_destructor_review(view, code, focus)
    windows = code.method_windows(FOCUS, {t["simple_name"]: t for t in focus})
    references, candidate_counts = code.references(targets)
    comparisons = protocol_comparison(view)
    domain = [t for t in types.values() if DOMAIN.search(t["decorated_name"])
              and "?$" not in t["decorated_name"] and not re.search(r"Dlg|Button|CashItem|MessageBox|LifeSkill", t["decorated_name"])]
    report = dict(client_sha256=EXPECTED, inputs=view.inputs, segment_provenance=view.streams,
        exception_directory=view.exception, capstone_version=capstone.__version__, limits=LIMITS,
        summary=dict(all_type_name_candidates=len(types), type_name_counts=counts,
                     domain_type_candidates=len(domain), focus_types_requested=len(FOCUS), focus_types_found=len(focus),
                     validated_locators=sum(len(t["locators"]) for t in focus),
                     vtable_candidates=sum(len(loc["vtables"]) for t in focus for loc in t["locators"]),
                     retained_code_references=len(references), method_windows=len(windows)),
        focus_types=focus, domain_type_inventory=domain,
        requested_types_not_found=[name for name in FOCUS if not any(t["simple_name"] == name for t in focus)],
        hierarchies=trees, rejected_locator_candidates=rejected,
        supporting_strings=strings, code_references=references, raw_candidate_counts=candidate_counts,
        code_review_stats=dict(code.stats, decoded_functions=len(code.cache)), method_windows=windows,
        protocol_comparison=comparisons, focused_field_review=field_review,
        non_destructor_review=ordinary_methods,
        attack_network_result_status="No selected RTTI type explicitly names an attack-network-result record. BubbleSystem hit messages and CGameClientSession are real type names but do not establish UDP semantics.",
        limitations=[
            "No native client/DLL or guest code was executed; no live memory, syscall, replay, game modification or production changes occurred.",
            "All six stream hashes and the source client hash are verified. Manifest final_section_semantics_verified remains false; later fixups and runtime object semantics are not certified.",
            "Missing .data virtual bytes are not fabricated. Original file-backed .data2 supplies the exception directory; decoded .pdata is not reinterpreted as that directory.",
            "RTTI linking requires a consistent TypeDescriptor header, x64 COL signature/self RVA, root type, bounded hierarchy and base descriptors; vtable extent is only a bounded candidate prefix.",
            "Code prefilter scans decoded .text for RIP-relative LEA/MOV targeting selected type descriptors, vtables and domain strings; direct immediates, other opcodes, indirect flows and other sections are not exhaustively searched.",
            "Code references are instruction-aligned by bounded linear decoding from original exception ranges. This is not a reachable control-flow graph or an execution trace; leaf functions without exception ranges are omitted.",
            "Vtable slots are not given invented method names. Subobject displacements are not wire offsets; shared implementations and adjustment thunks may occur.",
            "Existing protocol excerpts are comparison anchors only. No skill/template/object ownership mapping, packet opcode, field layout, or named-Hit coverage gain is inferred from a type name.",
            "Resource and PAK investigation is excluded from this task.",
        ])
    changed = [path for path, expected in view.inputs.items() if digest(Path(path).read_bytes()) != expected]
    if changed:
        raise ValueError("inputs changed during static review: " + repr(changed))
    report["elapsed_seconds"] = time.monotonic()-started
    report["changed_inputs"] = changed
    return report


if __name__ == "__main__":
    result = analyze()
    for suffix, body in (("json", json.dumps(result, ensure_ascii=False, indent=2)+"\n"),
                         ("md", markdown(result))):
        target = OUT / ("damage_types." + suffix)
        target.write_text(body, encoding="utf-8")
    print(json.dumps(dict(summary=result["summary"], code_review_stats=result["code_review_stats"],
                          seconds=result["elapsed_seconds"], reports=["damage_types.json", "damage_types.md"])), flush=True)
