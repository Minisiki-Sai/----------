"""Hash-pinned, bounded static post-decompression investigation.

Reads client bytes and the v43 descriptor report; never loads a guest DLL,
restores/emulates a CPU, patches checks, or emits a PE/image/decoded binary.
Capstone is used only as an offline instruction decoder. All new output is
restricted to postdecode*.json/md under the fixed v44 output directory.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tmp/reverse_tools_v39"))
import capstone
from capstone import x86_const as x86

CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
PRIOR = ROOT / "outputs/client_protection_v43_20260908/descriptors.json"
OUT = ROOT / "outputs/client_protection_v44_20260908"
BASE = 0x140000000
MASK = 0xFFFFFFFF


def digest(data):
    return sha256(data).hexdigest()


class StaticBytes:
    def __init__(self):
        self.raw = CLIENT.read_bytes()
        if digest(self.raw) != EXPECTED:
            raise ValueError("client identity mismatch")
        prior = PRIOR.read_bytes()
        self.prior_sha = digest(prior)
        self.prior = json.loads(prior)
        if self.prior["client"]["sha256"] != EXPECTED:
            raise ValueError("descriptor identity mismatch")
        self.sections = self.prior["pe_sections"]
        pe = struct.unpack_from("<I", self.raw, 60)[0]
        if self.raw[:2] != b"MZ" or self.raw[pe:pe+4] != b"PE\0\0":
            raise ValueError("invalid PE signature")
        opt = pe + 24
        if struct.unpack_from("<H", self.raw, opt)[0] != 0x20B:
            raise ValueError("not PE32+")
        self.base = struct.unpack_from("<Q", self.raw, opt+24)[0]
        self.image_size = struct.unpack_from("<I", self.raw, opt+56)[0]
        if self.base != BASE:
            raise ValueError("unexpected image base")
        self.entry = struct.unpack_from("<I", self.raw, opt+16)[0]
        self.directories = [struct.unpack_from("<II", self.raw, opt+112+8*i) for i in range(16)]
        self.decoded = {}
        self.streams = []
        manifest_bytes = (OUT / "segments_manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        self.manifest_sha = digest(manifest_bytes)
        if manifest["client_sha256"] != EXPECTED or manifest["descriptor_report_sha256"] != self.prior_sha:
            raise ValueError("segment manifest provenance mismatch")
        segment_names = {".text":"text.bin", ".rdata":"rdata.bin", ".data":"data.bin",
                         ".pdata":"pdata.bin", "_RDATA":"_RDATA.bin", ".data0":"data0.bin"}
        for record in self.prior["table_candidates"]["records"]:
            source = int(record["source_rva"], 16)
            target = int(record["destination_rva"], 16)
            packed = self.file_read(source, record["decoder"]["compressed_consumed"])
            if digest(packed) != record["compressed_sha256"]:
                raise ValueError("compressed bytes differ from descriptor evidence")
            item = next(m for m in manifest["segments"] if m["section"] == record["destination_section"])
            name = segment_names[record["destination_section"]]
            if item["path"] != "segments/"+name or item["destination_rva"] != target or item["source_rva"] != source:
                raise ValueError("segment manifest mapping mismatch")
            result = (OUT / "segments" / name).read_bytes()
            if digest(result) != record["decoder"]["decoded_sha256"] or digest(result) != item["decoded_sha256"] or len(result) != item["decoded_bytes"]:
                raise ValueError("segment output disagrees with independently verified EOS evidence")
            self.decoded[record["destination_section"]] = (target, result)
            self.streams.append(dict(section=record["destination_section"], source_rva=hex(source),
                target_rva=hex(target), decoded_bytes=len(result), sha256=digest(result)))
        self.dis = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        self.dis.detail = True

    def section(self, rva):
        return next((s for s in self.sections if s["rva"] <= rva < s["rva"]+max(s["virtual_size"], s["raw_size"])), None)

    def file_read(self, rva, size):
        s = self.section(rva)
        if s is None or rva+size > s["rva"]+s["raw_size"]:
            raise ValueError(f"not completely original-file-backed: {rva:#x}+{size:#x}")
        at = s["raw_offset"]+rva-s["rva"]
        return self.raw[at:at+size]

    def read(self, rva, size):
        s = self.section(rva)
        if s and s["name"] in self.decoded:
            base, data = self.decoded[s["name"]]
            at = rva-base
            if not 0 <= at <= at+size <= len(data):
                raise ValueError("unavailable decoded extent (no synthetic zero fill)")
            return data[at:at+size]
        return self.file_read(rva, size)

    def row(self, ins):
        row = dict(rva=hex(ins.address-BASE), bytes=ins.bytes.hex(), instruction=f"{ins.mnemonic} {ins.op_str}".strip())
        if ins.operands and ins.operands[0].type == x86.X86_OP_IMM and (ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)):
            row["direct_target_rva"] = hex(ins.operands[0].imm-BASE)
        references = []
        for op in ins.operands:
            if op.type == x86.X86_OP_MEM and op.mem.base == x86.X86_REG_RIP:
                references.append(hex(ins.address+ins.size+op.mem.disp-BASE))
        if references:
            row["rip_relative_rvas"] = references
        return row

    def window(self, rva, size=128, count=25):
        data = self.read(rva, size)
        return dict(rva=hex(rva), byte_count=size, raw=data.hex(),
                    instructions=[self.row(ins) for ins in self.dis.disasm(data, BASE+rva, count=count)],
                    caveat="Linear static decoding at this supplied boundary; not an execution trace.")


def entropy(data):
    return -sum((n/len(data))*math.log2(n/len(data)) for n in Counter(data).values()) if data else 0


def triple_score(words, image):
    triples = list(zip(words[::3], words[1::3], words[2::3]))
    valid = [(a,b,c) for a,b,c in triples if 0 < a < b <= image and 0 < c < image]
    return dict(records=len(triples), plausible_rva_triples=len(valid),
                first_five=[[hex(x) for x in t] for t in triples[:5]])


def pdata_statistics(view):
    base, data = view.decoded[".pdata"]
    size = next(s["virtual_size"] for s in view.sections if s["name"] == ".pdata")
    words = list(struct.unpack("<"+"I"*(size//4), data[:size]))
    cumulative = []
    acc = 0
    lanes = [0,0,0]
    lane_accum = []
    xor_previous = []
    previous = 0
    for i, value in enumerate(words):
        acc = (acc+value)&MASK
        cumulative.append(acc)
        lanes[i%3] = (lanes[i%3]+value)&MASK
        lane_accum.append(lanes[i%3])
        xor_previous.append(value ^ previous)
        previous = value
    return dict(rva=hex(base), meaningful_bytes=size, output_bytes=len(data),
        entropy_bits_per_byte=entropy(data[:size]),
        examples=[dict(offset=hex(at), bytes=data[at:at+64].hex()) for at in (0,64,256,4096,65536,size-64)],
        numeric_probes={"unchanged":triple_score(words,view.image_size),
            "u32_cumulative_sum":triple_score(cumulative,view.image_size),
            "three_independent_u32_cumulative_sums":triple_score(lane_accum,view.image_size),
            "xor_with_previous_stored_u32":triple_score(xor_previous,view.image_size)},
        caveat="These bounded no-key probes only test these exact formulas/alignment; failure does not rule out other transforms or identify encryption.")


def exception_directory(view):
    rva,size = view.directories[3]
    body = view.file_read(rva,size)
    triples = list(struct.iter_unpack("<III",body))
    starts = Counter()
    unwind = Counter()
    flags = Counter()
    examples = []
    invalid = []
    for i,(a,b,c) in enumerate(triples):
        section = view.section(a)
        starts[section["name"] if section else "outside"] += 1
        us = view.section(c)
        unwind[us["name"] if us else "outside"] += 1
        if not (0 < a < b <= view.image_size and 0 < c < view.image_size):
            invalid.append(i)
        try:
            head = view.read(c & ~1, 4)
            flags[f"version={head[0]&7},flags={head[0]>>3}"] += 1
        except ValueError:
            head = b""
        if i<5 or (len(examples)<14 and section and section["name"] not in [e["begin_section"] for e in examples]):
            examples.append(dict(index=i, record_rva=hex(rva+12*i), raw=body[12*i:12*i+12].hex(),
                begin=hex(a), end=hex(b), unwind_rva=hex(c), unwind_header=head.hex(),
                begin_section=section["name"] if section else None,
                code_first16=view.read(a,16).hex() if section else None))
    return dict(directory_index=3, rva=hex(rva), size=size, record_count=len(triples),
        section=view.section(rva)["name"], sha256=digest(body),
        begin_end_unwind_out_of_range=len(invalid), invalid_first_indices=invalid[:12],
        begin_rvas_sorted=all(a[0]<=b[0] for a,b in zip(triples,triples[1:])),
        begin_section_counts=dict(starts), unwind_section_counts=dict(unwind),
        unwind_header_counts=dict(flags), examples=examples,
        conclusion="The PE exception directory is original file-backed .data2, not decompressed .pdata; the section name alone cannot require .pdata to become RUNTIME_FUNCTION records.")


def string_features(data, base):
    patterns = {
        "protection_loader": rb"VMProtect|Themida|WinLicense|IsDebuggerPresent|NtProtectVirtualMemory|NtQueryInformationProcess|GetProcAddress|LoadLibrary|VirtualProtect|ntdll\.dll",
        "game_domain": rb"\b(?:DragonNest|Skill|Actor|Quest|Damage|Action|Character|Monster|Nexon|Eyedentity)\b",
        "compiler_runtime": rb"\.\?AV|\.\?AU|RTTI|std::|Microsoft Visual C\+\+|bad_alloc",
    }
    runs = list(re.finditer(rb"[\x20-\x7e]{8,}",data))
    found = {}
    for name,pattern in patterns.items():
        matches = [m for m in runs if re.search(pattern,m.group(),re.I)]
        found[name] = dict(count=len(matches), examples=[dict(rva=hex(base+m.start()),text=m.group()[:140].decode("ascii")) for m in matches[:12]])
    return dict(ascii_runs_at_least8=len(runs), ascii_run_bytes=sum(len(m.group()) for m in runs),
        printable_bytes=sum(32<=x<127 for x in data), entropy_bits_per_byte=entropy(data),
        groups=found, caveat="Strings are static content indicators only; neither ownership nor execution follows from a string hit.")


def c_string(view, rva, limit=256):
    data = view.file_read(rva,limit)
    end = data.find(b"\0")
    if end < 0:
        raise ValueError("bounded import name has no terminator")
    return data[:end].decode("ascii")


def properties_and_import_neighbor(view):
    table = int(view.prior["table_candidates"]["table_rva"],16)
    pointer,length = struct.unpack("<II",view.file_read(table,8))
    props = view.file_read(pointer,length)
    if length != 5 or props[0] >= 225:
        raise ValueError("observed five-byte property candidate changed")
    lc = props[0]%9
    rest = props[0]//9
    lp,pb = rest%5,rest//5
    dictionary = struct.unpack_from("<I",props,1)[0]
    matches = []
    module_counts = {}
    directory,size = view.directories[1]
    for at in range(directory,directory+size,20):
        oft,stamp,forward,name,iat = struct.unpack("<IIIII",view.file_read(at,20))
        if not any((oft,stamp,forward,name,iat)):
            break
        module = c_string(view,name)
        count = 0
        for i in range(8192):
            value = struct.unpack("<Q",view.file_read(oft+8*i,8))[0]
            if not value:
                break
            count += 1
            if value>>63:
                continue
            if pointer <= value < pointer+32:
                matches.append(dict(module=module, descriptor_rva=hex(at),
                    lookup_slot_rva=hex(oft+8*i), lookup_slot_bytes=view.file_read(oft+8*i,8).hex(),
                    iat_slot_rva=hex(iat+8*i), original_iat_bytes=view.file_read(iat+8*i,8).hex(),
                    import_by_name_rva=hex(value), hint=struct.unpack("<H",view.file_read(value,2))[0],
                    symbol=c_string(view,value+2), original_bytes=view.file_read(value,10).hex()))
        else:
            raise ValueError("import thunk budget exceeded")
        module_counts[module] = count
    expected_words = 0x736+(0x300<<(lc+lp))
    probability = view.prior["first_text_context"]["probability_context"]
    return dict(table_rva=hex(table), header_raw=view.file_read(table,8).hex(),
        first_word_rva=hex(pointer), second_word=length, pointed_first16=view.file_read(pointer,16).hex(),
        five_bytes=props.hex(), interpretation=dict(lc=lc,lp=lp,pb=pb,dictionary_bytes=dictionary,
            property_formula="property=(pb*5+lp)*9+lc", probability_words=expected_words,
            probability_allocation_bytes=expected_words*2),
        corroboration=dict(supplied_native_prefix_parameters_match=(lc,lp,pb)==(3,0,2),
            saved_probability_bytes=probability["allocation_bytes"],
            saved_probability_size_matches=expected_words*2==probability["allocation_bytes"],
            decoder_initialization_constant_evidence=[view.window(0x29db76e,31,20),view.window(0x29db7aa,5,1)],
            no_redecode_needed=True),
        interpretation_confidence="Strong structural hypothesis: header is {LZMA properties RVA, properties byte count}; not yet a trace of the actual property read/parser.",
        neighbor_import_matches=matches, import_module_count=len(module_counts), import_symbol_counts=module_counts,
        neighbor_conclusion="IsChild begins at properties+8 and has a real IMAGE_IMPORT_BY_NAME at properties+6. It is separate neighboring import data, not part of the five property bytes.",
        limitations=["16 MiB is the dictionary value encoded under this property interpretation, not a measured native allocation.",
            "Earlier 32 MiB static decode settings were sufficient and do not contradict this smaller property value.",
            "Do not parse the following IsChild bytes as LZMA-Alone uncompressed size or more descriptor fields."])


def relocation_code(view):
    # Each bounded window starts at an independently read instruction boundary.
    windows = [view.window(a,n,count=35) for a,n in (
        (0x29DCDD7,42),(0x29DCE01,33),(0x29DCE35,45),
        (0x29DCE76,75),(0x29DCEBD,55),(0x29DCEEE,60),(0x29DCF2D,58))]
    checks = {0x29DCDE7:"4c8bd1",0x29DCE76:"410fb711",0x29DCE9C:"80e20f",
              0x29DCEA0:"48c1e804",0x29DCECB:"440111",0x29DCEE6:"4c0111",
              0x29DCF0B:"660101",0x29DCF26:"66440111"}
    for rva,expected in checks.items():
        if view.file_read(rva,len(bytes.fromhex(expected))).hex() != expected:
            raise ValueError("post-processing opcode anchor mismatch")
    return dict(entry_rva="0x29dcdd7", last_ret_rva="0x29dcf76", windows=windows,
        parameter_dataflow={"RCX_at_entry":"copied to R10: addend (delta interpretation not proved at caller)",
            "EDX_at_entry":"zero-extended into EBX then added to entry R9: table RVA candidate",
            "R8D_at_entry":"copied into ESI: total table byte count", "R9_at_entry":"table base candidate",
            "fifth_stack_argument":"loaded into RDI: destination base candidate"},
        established_local_operations=["Block DWORD+0 contributes to destination address; DWORD+4 is block byte size and must be >=8.",
            "Number of entries is (block_size-8)>>1; entries are read as unsigned 16-bit words.",
            "type=entry&0xf; offset=entry>>4; address=destination_base+block_DWORD0+offset.",
            "Type 3 adds low32(R10) to a dword; type 10 adds R10 to a qword.",
            "Type 1 adds low16(arithmetic_shift_right(R10,16)) to a word; type 2 adds low16(R10) to a word.",
            "Other type values fall through without these writes. Entries advance by2; blocks advance by block byte size."],
        interpretation="Custom-packed base-relocation/fixup walker: strong static semantics, not observed invocation.",
        caveat="No guest instructions executed. Caller values, actual table supplied, ordering and addend remain unobserved. No check or branch was patched/skipped.")


def custom_relocation_tables(view):
    domains = [(name,base,data) for name,(base,data) in view.decoded.items()
               if name in (".rdata",".data",".pdata",".data0")]
    s = view.section(0x1B58000)
    domains.append((s["name"],s["rva"],view.file_read(s["rva"],s["raw_size"])))
    excluded = [(int(r["source_rva"],16),int(r["decoder"]["stream_end_rva"],16))
                for r in view.prior["table_candidates"]["records"]]
    results = []
    header_pattern = rb"\x00[\x00\x10\x20\x30\x40\x50\x60\x70\x80\x90\xa0\xb0\xc0\xd0\xe0\xf0][\x00-\xff][\x00-\x02]"
    for name,base,data in domains:
        skip = 0
        for match in re.finditer(header_pattern,data):
            initial = match.start()
            if initial<skip or (name==".data2" and any(a<=base+initial<b for a,b in excluded)):
                continue
            at,previous = initial,-1
            blocks,entries = [],[]
            for _ in range(4096):
                if at+8>len(data):
                    break
                page,size = struct.unpack_from("<II",data,at)
                if not (page%4096==0 and previous<page<view.image_size and 8<size<=8192 and size%2==0 and at+size<=len(data)):
                    break
                block_entries = list(struct.iter_unpack("<H",data[at+8:at+size]))
                if sum(e&15 in (0,1,2,3,10) for (e,) in block_entries)<len(block_entries)*0.95:
                    break
                blocks.append(dict(table_rva=hex(base+at), page_rva=hex(page), block_bytes=size))
                entries.extend((page,e,base+at+8+2*i) for i,(e,) in enumerate(block_entries))
                previous,at = page,at+size
            if len(blocks)<3 or len(entries)<24:
                continue
            skip = at
            types = Counter(e&15 for _,e,_ in entries)
            target_sections = Counter()
            values = Counter()
            examples,zero_examples = [],[]
            seen_sections = set()
            target_addresses = []
            for page,e,entry_rva in entries:
                typ = e&15
                if typ==0:
                    continue
                target = page+(e>>4)
                target_addresses.append(target)
                section = view.section(target)
                label = section["name"] if section else "outside"
                target_sections[label] += 1
                width = {1:2,2:2,3:4,10:8}[typ]
                try:
                    raw_value = view.read(target,width)
                    value = int.from_bytes(raw_value,"little")
                    classification = "in_image_absolute_va" if BASE<=value<BASE+view.image_size else "zero" if value==0 else "other"
                    values[classification] += 1
                except ValueError:
                    raw_value,value,classification = b"",None,"unavailable"
                    values[classification] += 1
                example = dict(entry_rva=hex(entry_rva), entry_bytes=struct.pack("<H",e).hex(),
                    encoded_word=hex(e), type=typ, page_rva=hex(page), offset=hex(e>>4), target_rva=hex(target),
                    target_section=label, existing_bytes=raw_value.hex(),
                    existing_value=hex(value) if value is not None else None, value_class=classification)
                if len(examples)<5 or label not in seen_sections:
                    examples.append(example)
                seen_sections.add(label)
                if classification=="zero" and len(zero_examples)<6:
                    zero_examples.append(example)
            body = data[initial:at]
            results.append(dict(section=name,rva=hex(base+initial),end_rva=hex(base+at),byte_length=len(body),
                sha256=digest(body),block_count=len(blocks),entry_count=len(entries),
                type_counts={hex(k):n for k,n in types.items()}, target_section_counts=dict(target_sections),
                existing_value_counts=dict(values), duplicate_nonpadding_targets=len(target_addresses)-len(set(target_addresses)),
                first_blocks=blocks[:4],last_blocks=blocks[-4:],first48=body[:48].hex(),last32=body[-32:].hex(),
                next24=data[at:at+24].hex(), examples=examples,zero_target_examples=zero_examples,
                all_blocks=blocks,
                conclusion="Long monotonic block chain, code-matching nibble layout and independently decoded pointer values strongly identify a custom fixup table. Actual caller/table binding is still unobserved."))
    return dict(search_scope=[dict(section=n,bytes=len(d)) for n,_,d in domains],
        skipped_compressed_streams=6, minimum_chain_blocks=3,minimum_chain_entries=24,
        max_blocks_per_candidate=4096,max_block_bytes=8192,
        global_45_prefix_scan=False,candidates=results,
        caveat="Format discovery and value validation only. Nothing is rebased, materialized as an image, or written back; missing .data bytes remain unavailable.")


def public_relocations(view):
    rva,size = view.directories[5]
    data = view.file_read(rva,size)
    at = 0
    counts,sections = Counter(),Counter()
    examples = []
    while at < len(data):
        page,span = struct.unpack_from("<II",data,at)
        if span<8 or span%2 or at+span>len(data):
            raise ValueError("invalid existing PE relocation directory")
        for (e,) in struct.iter_unpack("<H",data[at+8:at+span]):
            typ,offset = e>>12,e&0xFFF
            counts[typ] += 1
            if not typ:
                continue
            target = page+offset
            s = view.section(target)
            sections[s["name"] if s else "outside"] += 1
            if len(examples)<4:
                examples.append(dict(word=hex(e),type=typ,offset=hex(offset),target_rva=hex(target)))
        at += span
    return dict(rva=hex(rva),byte_length=size,type_counts={hex(k):n for k,n in counts.items()},
        target_section_counts=dict(sections),examples=examples,
        conclusion="This existing PE directory uses type=word>>12, offset=word&0xfff. It is distinct from the low-nibble-type custom table, and does not cover the decompressed .rdata/.data targets.")


def resource_data_leads(view):
    base,data = view.decoded[".rdata"]
    ascii_runs = list(re.finditer(rb"[\x20-\x7e]{6,}",data))
    act = [m for m in ascii_runs if m.group().endswith(b".act")]
    selected = [m for m in act if m.group() in (b"%s_skill.act",b"academic_skill_2st_adept.act",
                b"academic_skill_2st_gearmaster.act",b"warrior_skill_2st_gladiator.act")]
    wide = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}",data):
        text = m.group().decode("utf-16le")
        if re.search(r"(?i)(\.dnt\b|\[decompress\]|unuse_packing)",text):
            wide.append(dict(rva=hex(base+m.start()),text=text[:160]))
    return dict(section=".rdata",ascii_act_name_count=len(act),
        examples=[dict(rva=hex(base+m.start()),text=m.group().decode("ascii"),
            raw_with_terminator=data[m.start():m.end()+1].hex()) for m in selected],
        bounded_utf16_leads=wide[:20],
        conclusion="Resource-related names and a decompression diagnostic are present in decoded .rdata; these are static anchors for another agent, not evidence of successful PAK decryption or action parsing.",
        no_archive_reads=True,no_function_boundary_analysis_repeated=True)


def analyze():
    view = StaticBytes()
    result = dict(client_sha256=digest(view.raw), client_bytes=len(view.raw),
        script_sha256=digest(Path(__file__).read_bytes()), descriptor_report_sha256=view.prior_sha,
        segment_manifest_sha256=view.manifest_sha,
        capstone_version=capstone.__version__, streams=view.streams,
        pe_entry_rva=hex(view.entry), pe_directories=[dict(index=i,rva_or_file_offset=hex(a),size=b) for i,(a,b) in enumerate(view.directories)],
        pdata=pdata_statistics(view), exception_directory=exception_directory(view),
        section_features={name:string_features(data,base) for name,(base,data) in view.decoded.items() if name in (".text",".pdata",".data0")},
        data0_bounded_example=view.window(0x1385000,60,15),
        properties_header=properties_and_import_neighbor(view),
        fixup_walker=relocation_code(view),custom_fixup_tables=custom_relocation_tables(view),
        public_base_relocations=public_relocations(view),resource_data_leads=resource_data_leads(view),
        boundary="Static bytes and disassembly only; no guest/native game DLL execution, process access, CPU restore, check patches, executable output or core-model/checkpoint edits.")
    result["data0_assessment"] = dict(established=[
        "The actual exception directory includes 906 begin RVAs in .data0.",
        "At .data0 start the bytes modify stack state, restore flags and directly call two other .data0 addresses.",
        "Decoded .data0 contains MSVC runtime text and C++ RTTI; it is not uniformly opaque data."],
        hypothesis="Contains protection/mutated runtime code as well as compiled runtime metadata; a clean second game-code section is not established.",
        limitations="Obfuscation patterns do not identify a protector vendor or prove absence of game logic. Function-boundary/cross-link analysis is delegated by the user to another agent and not repeated here.")
    candidates = result["custom_fixup_tables"]["candidates"]
    if len(candidates)!=1 or candidates[0]["rva"]!="0x23da59a" or candidates[0]["byte_length"]!=0x2B894:
        raise ValueError("fixup discovery no longer supports the recorded conclusion")
    candidate = candidates[0]
    if candidate["block_count"]!=368 or candidate["type_counts"]!={"0xa":87496,"0x0":194} or candidate["existing_value_counts"]!={"in_image_absolute_va":87476,"zero":20}:
        raise ValueError("fixup target/count validation changed")
    if result["exception_directory"]["record_count"]!=46957 or not result["exception_directory"]["begin_rvas_sorted"] or result["exception_directory"]["begin_end_unwind_out_of_range"]:
        raise ValueError("exception-directory conclusion not supported")
    if result["properties_header"]["five_bytes"]!="5d00000001" or not result["properties_header"]["corroboration"]["saved_probability_size_matches"]:
        raise ValueError("property interpretation no longer has the stated corroboration")
    result["findings"] = dict(
        established=["The actual original-file exception directory already contains 46957 sorted in-range triples, including 46019 .text records.",
            "A static instruction slice implements a low-nibble-type/high-12-bit-offset additive fixup walker.",
            "A unique candidate in the stated scan scope at 0x23da59a spans 368 blocks / 0x2b894 bytes, with 87496 type10 entries and 194 type0 entries.",
            "All nonpadding targets are available decoded bytes: 87476 contain in-image absolute VAs and 20 contain actual stored zero qwords.",
            "The descriptor first word points to five bytes 5d00000001; real USER32!IsChild import data starts separately at +6."],
        hypotheses=["Descriptor header {properties_RVA,5} is strongly consistent with LZMA lc3/lp0/pb2 and a 16 MiB dictionary.",
            "The low-nibble table is the input of the identified walker; exact caller parameters and execution order still require observation.",
            ".data0 includes protection/runtime machinery; complete game/protector ownership remains unresolved."],
        next_checks=["Observe property reads at 0x23b1686..0x23b168a and table-header use without interpreting 5 as a section count.",
            "At 0x29dcdd7 capture RCX, EDX, R8D, R9 and stack arg5; test whether table RVA=0x23da59a and size=0x2b894.",
            "Observe a real write at 0x29dcee6 and its addend/destination; do not apply speculative fixups offline.",
            "Resource lead: UTF16 ' FAILED [decompress] (%08X%ls)' at 0xfab960; ACT name anchors are listed for the code-links agent."],
        not_claimed=["No .pdata exception-table reconstruction is required by the evidence.",
            "No complete restored client, working PE, PAK decryption, game-object attribution or test-coverage increase is claimed."])
    if digest(CLIENT.read_bytes()) != EXPECTED:
        raise ValueError("client changed during analysis")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="postdecode.json")
    args = parser.parse_args()
    if not re.fullmatch(r"postdecode[A-Za-z0-9_-]*\.json",args.report):
        raise SystemExit("only fixed-directory postdecode*.json output is permitted")
    result = analyze()
    OUT.mkdir(parents=True,exist_ok=True)
    target = OUT / args.report
    target.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(dict(report=str(target),sha256=digest(target.read_bytes()),
                         client_sha256=result["client_sha256"],findings=result["findings"]),indent=2))
