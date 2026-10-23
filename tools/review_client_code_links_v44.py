"""Read-only string/RIP-reference review of independently extracted segments.

Reads the supplied isolated .bin files and hash-pinned static evidence. The
original client file is read only to verify its PE headers/exception directory;
it is never loaded or executed. No CPU restore, PE output, fixups or parser
changes. Capstone is an offline instruction decoder only.
Owned writes: outputs/client_protection_v44_20260908/code_links*.json/md.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict, deque
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/client_protection_v44_20260908"
MANIFEST = OUT / "segments_manifest.json"
DESCRIPTORS = ROOT / "outputs/client_protection_v43_20260908/descriptors.json"
POSTDECODE = OUT / "postdecode_initial.json"
CRYPTO = OUT / "crypto_tables.json"
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED_CLIENT = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
NAMES = {".text": "text", ".rdata": "rdata", ".data": "data",
         ".pdata": "pdata", "_RDATA": "_RDATA", ".data0": "data0"}
KEYWORDS = {
    "pak_archive": r"\.pak\b|\bpak\b|pakfile|pakmanager|archive",
    "resource_template": r"\.(?:dnt|act|skill|projectile)\b|resource|filemanager|filesystem",
    "codec_crypto": r"compress|decompress|inflate|deflate|lzma|zlib|decrypt|encrypt|rijndael|\baes\b",
    "file_io": r"\b(?:CreateFile[AW]?|ReadFile|SetFilePointer(?:Ex)?|GetFileSize(?:Ex)?|CreateFileMapping[AW]?|MapViewOfFile|UnmapViewOfFile|CryptDecrypt|BCryptDecrypt)\b",
}
PATTERNS = {key: re.compile(value, re.I) for key, value in KEYWORDS.items()}


def digest(data):
    return sha256(data).hexdigest()


def load_segments():
    manifest_bytes, descriptor_bytes = MANIFEST.read_bytes(), DESCRIPTORS.read_bytes()
    manifest, prior = json.loads(manifest_bytes), json.loads(descriptor_bytes)
    if manifest["client_sha256"] != EXPECTED_CLIENT or prior["client"]["sha256"] != EXPECTED_CLIENT:
        raise ValueError("source client identity mismatch")
    if manifest["descriptor_report_sha256"] != digest(descriptor_bytes):
        raise ValueError("descriptor report changed after extraction")
    table = prior["table_candidates"]
    if manifest["table_sha256"] != table["table_sha256"] or manifest["table_rva"] != int(table["table_rva"], 16):
        raise ValueError("descriptor table identity mismatch")
    if len(manifest["segments"]) != 6 or {s["section"] for s in manifest["segments"]} != set(NAMES):
        raise ValueError("expected exactly six isolated segments")
    records = {r["destination_section"]: r for r in table["records"]}
    segments, provenance, inputs = {}, [], {str(MANIFEST): digest(manifest_bytes), str(DESCRIPTORS): digest(descriptor_bytes)}
    for item in manifest["segments"]:
        name = item["section"]
        path = "segments/" + NAMES[name] + ".bin"
        if item["path"] != path:
            raise ValueError("segment path is not the fixed isolated .bin path")
        body = (OUT / path).read_bytes()
        record = records[name]
        decoder = record["decoder"]
        if (len(body) != item["decoded_bytes"] or digest(body) != item["decoded_sha256"] or
                len(body) != decoder["decoded_bytes"] or digest(body) != decoder["decoded_sha256"] or
                item["source_rva"] != int(record["source_rva"], 16) or
                item["destination_rva"] != int(record["destination_rva"], 16) or
                item["compressed_bytes"] != decoder["compressed_consumed"] or
                item["compressed_sha256"] != record["compressed_sha256"] or
                not decoder["eof"] or decoder["error"] is not None):
            raise ValueError("segment does not match manifest and independent EOS evidence")
        size = item["virtual_size"]
        if any(body[size:]) or item["not_decoded_virtual_bytes"] != max(0, size-len(body)):
            raise ValueError("padding/unknown-byte scope mismatch")
        prefix = item["native_verified_prefix_bytes"]
        if prefix and (prefix > len(body) or digest(body[:prefix]) != item["native_prefix_sha256"]):
            raise ValueError("native-checked output prefix identity mismatch")
        inputs[str(OUT / path)] = digest(body)
        segments[name] = dict(item, body=body[:min(size, len(body))])
        provenance.append(dict(item, examined_available_bytes=min(size, len(body))))
    if sum(s["decoded_bytes"] for s in provenance) != manifest["total_decoded_bytes"]:
        raise ValueError("total decoded size mismatch")
    return segments, provenance, inputs, prior


def rank_string(text):
    score = 0
    for expression, weight in ((r"\.pak\b|\bpak\b|pakfile|pakmanager", 200),
                               (r"decrypt|decompress|uncompress|encrypt", 120),
                               (r"\.(?:act|dnt|skill|projectile)\b", 75),
                               (r"resource|archive|filemanager", 40),
                               (r"zlib|inflate|deflate|lzma", 25)):
        if re.search(expression, text, re.I):
            score += weight
    if text.startswith(".?AV") or "::" in text or re.match(r"[A-Za-z_][A-Za-z_0-9]{5,}$", text):
        score += 15
    return score


def extract_strings(segments):
    strings, totals = [], {}
    for section in (".rdata", ".text"):
        segment = segments[section]
        data = segment["body"]
        stats = Counter()
        for encoding, expression in (("ascii", rb"[\x20-\x7e]{5,}"),
                                      ("utf-16le-ascii", rb"(?:[\x20-\x7e]\x00){5,}")):
            for match in re.finditer(expression, data):
                stats[encoding + "_runs"] += 1
                raw = match.group()
                text = raw.decode("ascii" if encoding == "ascii" else "utf-16le")
                categories = [name for name, pattern in PATTERNS.items() if pattern.search(text)]
                if not categories:
                    continue
                # Retain exact bytes/hash/length even for an unusually long run.
                offset = match.start()
                width = 1 if encoding == "ascii" else 2
                terminated = data[match.end():match.end()+width] == b"\0"*width
                strings.append(dict(id=f"S{len(strings):05}", section=section, segment_offset=offset,
                    rva=segment["destination_rva"]+offset, byte_length=len(raw), encoding=encoding,
                    null_terminated=terminated, text=text, bytes_sha256=digest(raw),
                    categories=categories, relevance_score=rank_string(text)))
                stats[encoding + "_matching_runs"] += 1
        totals[section] = dict(stats)
    return strings, totals


def load_ranges(prior, inputs):
    """Use the actual PE exception directory, never the .pdata candidate."""
    post_bytes = POSTDECODE.read_bytes()
    post = json.loads(post_bytes)
    if post["segment_manifest_sha256"] != inputs[str(MANIFEST)] or post["descriptor_report_sha256"] != inputs[str(DESCRIPTORS)]:
        raise ValueError("postdecode input provenance differs from this review")
    inputs[str(POSTDECODE)] = digest(post_bytes)
    raw = CLIENT.read_bytes()
    if digest(raw) != EXPECTED_CLIENT or post["client_sha256"] != EXPECTED_CLIENT:
        raise ValueError("original-file/postdecode identity mismatch")
    inputs[str(CLIENT)] = digest(raw)
    pe = struct.unpack_from("<I", raw, 60)[0]
    if raw[:2] != b"MZ" or raw[pe:pe+4] != b"PE\0\0":
        raise ValueError("invalid static PE header")
    machine, count = struct.unpack_from("<HH", raw, pe+4)
    opt_size = struct.unpack_from("<H", raw, pe+20)[0]
    opt = pe+24
    if machine != 0x8664 or struct.unpack_from("<H", raw, opt)[0] != 0x20b:
        raise ValueError("expected x64 PE32+")
    image_base = struct.unpack_from("<Q", raw, opt+24)[0]
    image_size = struct.unpack_from("<I", raw, opt+56)[0]
    rva, length = struct.unpack_from("<II", raw, opt+112+3*8)
    sections = []
    for index in range(count):
        at = opt+opt_size+40*index
        name = raw[at:at+8].rstrip(b"\0").decode("ascii")
        virtual_size, start, raw_size, raw_offset = struct.unpack_from("<IIII", raw, at+8)
        sections.append(dict(name=name, rva=start, virtual_size=virtual_size, raw_size=raw_size, raw_offset=raw_offset))
    section = next(s for s in sections if s["rva"] <= rva and rva+length <= s["rva"]+s["raw_size"])
    offset = section["raw_offset"]+rva-section["rva"]
    body = raw[offset:offset+length]
    evidence = post["exception_directory"]
    if (rva != int(evidence["rva"], 16) or length != evidence["size"] or
            digest(body) != evidence["sha256"] or length % 12 or section["name"] != ".data2"):
        raise ValueError("exception directory does not match independently reported original-file bytes")
    ranges = []
    for index, (begin, end, unwind) in enumerate(struct.iter_unpack("<III", body)):
        if not 0 < begin < end <= image_size or not 0 < unwind < image_size:
            raise ValueError("invalid exception range")
        owner = next((s["name"] for s in sections if s["rva"] <= begin < s["rva"]+s["virtual_size"]), None)
        ranges.append(dict(index=index, begin=begin, end=end, unwind_rva=unwind, section=owner,
                           record_rva=rva+12*index, record_file_offset=offset+12*index,
                           record_bytes=body[index*12:index*12+12].hex()))
    if len(ranges) != evidence["record_count"] or any(a["begin"] > b["begin"] for a, b in zip(ranges, ranges[1:])):
        raise ValueError("exception range count/order mismatch")
    return ranges, dict(evidence, file_offset=offset, image_base=image_base, image_size=image_size,
                        source="original PE directory #3, independently hash checked"), image_base


def available(segments, rva, size=1):
    for segment in segments.values():
        offset = rva-segment["destination_rva"]
        if 0 <= offset and offset+size <= len(segment["body"]):
            return segment, offset
    return None, None


def extra_targets(segments, strings, image_base, inputs):
    targets = [dict(item, kind="resource_string") for item in strings]
    if CRYPTO.exists():
        crypto_bytes = CRYPTO.read_bytes()
        crypto = json.loads(crypto_bytes)
        if crypto["client_sha256"] != EXPECTED_CLIENT or crypto["manifest_sha256"] != inputs[str(MANIFEST)]:
            raise ValueError("crypto-table provenance mismatch")
        inputs[str(CRYPTO)] = digest(crypto_bytes)
        for item in crypto["exact_table_matches"]:
            segment, at = available(segments, item["rva"], item["bytes"])
            if segment is None or digest(segment["body"][at:at+item["bytes"]]) != item["sha256"]:
                raise ValueError("published crypto-table match differs from isolated bytes")
            targets.append(dict(id="C_"+item["table"], kind="exact_public_cipher_table",
                section=item["section"], rva=item["rva"], byte_length=item["bytes"],
                text=item["table"], bytes_sha256=item["sha256"], categories=["cipher_table"],
                relevance_score=150, definition_evidence=str(CRYPTO.relative_to(ROOT))))
    # The restored .rdata begins with zero-extended name RVAs. This is reported
    # only as pointer-like byte evidence, not as resolved/bound Windows imports.
    data = segments[".rdata"]["body"]
    rbase = segments[".rdata"]["destination_rva"]
    for at in range(0, len(data)-7, 8):
        value = struct.unpack_from("<Q", data, at)[0]
        destination = value-image_base if value >= image_base else value
        for add in (0, 2):
            position = destination+add-rbase
            if not 0 <= position < len(data):
                continue
            match = re.match(rb"[A-Za-z_][A-Za-z_0-9@]{4,100}\x00", data[position:position+102])
            if not match:
                continue
            name = match.group()[:-1].decode("ascii")
            if not (PATTERNS["file_io"].search(name) or re.search(r"inflate|uncompress|decrypt", name, re.I)):
                continue
            targets.append(dict(id=f"P_{rbase+at:x}", kind="pointer_like_name_slot", section=".rdata",
                rva=rbase+at, byte_length=8, text=name, categories=["file_io_slot"], relevance_score=60,
                bytes=data[at:at+8].hex(), stored_qword=hex(value), name_rva=destination+add,
                name_prefix_bytes=add, name_bytes=match.group().hex(),
                caveat="Name-RVA-shaped contents; import binding/loader semantics are not established."))
            break
    return targets


def direct_target(operand):
    return int(operand, 16) if re.fullmatch(r"0x[0-9a-f]+", operand) else None


def reachable_rows(rows, begin, end):
    by_address = {row[0]: row for row in rows}
    pending, reached, unresolved = [begin], set(), set()
    while pending:
        address = pending.pop()
        while address not in reached and begin <= address < end:
            row = by_address.get(address)
            if row is None:
                unresolved.add(address)
                break
            reached.add(address)
            rva, size, mnemonic, operand = row
            if mnemonic.startswith(("ret", "iret")) or mnemonic in ("ud2", "int3", "hlt"):
                break
            if mnemonic.startswith("j") or mnemonic.startswith("loop"):
                target = direct_target(operand)
                if target is not None and begin <= target < end:
                    pending.append(target)
                if mnemonic == "jmp":
                    break
            address += size
    return reached, sorted(unresolved)


def analyze_links(segments, ranges, strings, image_base, inputs):
    sys.path.insert(0, str(ROOT / ".tmp/reverse_tools_v39"))
    import capstone
    from capstone import x86_const as x86
    lite = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    detail = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    detail.detail = True
    targets = extra_targets(segments, strings, image_base, inputs)
    targets.sort(key=lambda item: item["rva"])
    starts = [item["rva"] for item in targets]
    by_id = {item["id"]: item for item in targets}
    refs, functions, edges = [], {}, defaultdict(list)
    stats = Counter()
    intervals = [item for item in ranges if item["section"] == ".text"]
    interval_by_begin = {item["begin"]: item for item in ranges}
    rip_expression = re.compile(r"\[rip(?:\s*([+-])\s*(0x[0-9a-f]+|[0-9]+))?\]")

    def locate_target(address):
        index = bisect_right(starts, address)-1
        if index >= 0:
            target = targets[index]
            if address < target["rva"]+target["byte_length"]:
                return target
        return None

    def describe(row, segment):
        rva, size, mnemonic, operand = row
        at = rva-segment["destination_rva"]
        result = dict(rva=rva, bytes=segment["body"][at:at+size].hex(), instruction=(mnemonic+" "+operand).strip())
        target = direct_target(operand)
        if (mnemonic == "call" or mnemonic.startswith("j")) and target is not None:
            result["direct_target_rva"] = target
            result["target_is_exception_entry"] = target in interval_by_begin
        return result

    for number, anchor in enumerate(intervals):
        begin, end = anchor["begin"], anchor["end"]
        segment, at = available(segments, begin, end-begin)
        if segment is None:
            stats["ranges_not_wholly_available"] += 1
            continue
        body = segment["body"][at:at+end-begin]
        rows = list(lite.disasm_lite(body, begin))
        reached, unresolved = reachable_rows(rows, begin, end)
        stats["ranges_scanned"] += 1
        stats["decoded_instructions"] += len(rows)
        stats["entry_reachable_instructions"] += len(reached)
        consumed = sum(row[1] for row in rows)
        if consumed != len(body):
            stats["ranges_with_undecoded_suffix"] += 1
            stats["undecoded_suffix_bytes"] += len(body)-consumed
        if unresolved:
            stats["ranges_with_unresolved_internal_targets"] += 1
        found = []
        for index, row in enumerate(rows):
            rva, size, mnemonic, operand = row
            if rva not in reached:
                continue
            immediate = direct_target(operand)
            if mnemonic in ("call", "jmp") and immediate is not None and not begin <= immediate < end:
                item = describe(row, segment)
                item["kind"] = "call" if mnemonic == "call" else "tail_jump"
                edges[begin].append(item)
            match = rip_expression.search(operand)
            if not match:
                continue
            displacement = int(match.group(2), 0) if match.group(2) else 0
            if match.group(1) == "-":
                displacement = -displacement
            address = rva+size+displacement
            target = locate_target(address)
            if target is None:
                continue
            stats["target_operand_candidates"] += 1
            instruction = next(detail.disasm(body[rva-begin:rva-begin+size], rva, count=1), None)
            operands = [op for op in instruction.operands if op.type == x86.X86_OP_MEM and op.mem.base == x86.X86_REG_RIP] if instruction else []
            if not operands or instruction.size != size or any(rva+size+op.mem.disp != address for op in operands):
                raise ValueError("independent detailed RIP operand verification failed")
            disp_offset, disp_size = instruction.disp_offset, instruction.disp_size
            encoded_disp = bytes(instruction.bytes)[disp_offset:disp_offset+disp_size]
            if disp_size != 4 or int.from_bytes(encoded_disp, "little", signed=True) != displacement:
                raise ValueError("RIP displacement is not reproduced by the recorded instruction bytes")
            ref = dict(id=f"R{len(refs):05}", target_id=target["id"], function_begin=begin,
                instruction_rva=rva, instruction_size=size, instruction_bytes=bytes(instruction.bytes).hex(),
                instruction=(mnemonic+" "+operand).strip(), displacement=displacement,
                displacement_offset=disp_offset, displacement_bytes=encoded_disp.hex(),
                next_instruction_rva=rva+size, target_rva=address, target_byte_offset=address-target["rva"],
                in_native_checked_output_prefix=rva+size <= segment["destination_rva"]+segment["native_verified_prefix_bytes"],
                context=[describe(context, segment) for context in rows[max(0, index-4):index+9]])
            refs.append(ref)
            found.append(ref["id"])
        if found:
            unwind_segment, unwind_at = available(segments, anchor["unwind_rva"] & ~1, 4)
            functions[begin] = dict(anchor, code_sha256=digest(body), code_bytes=len(body),
                decoded_instructions=len(rows), reachable_instructions=len(reached),
                undecoded_suffix_bytes=len(body)-consumed, unresolved_internal_targets=unresolved,
                unwind_header=unwind_segment["body"][unwind_at:unwind_at+4].hex() if unwind_segment else None,
                reference_ids=found, direct_transfers=edges[begin])
        if number and number % 10000 == 0:
            print(json.dumps(dict(stage="exception_anchored_decode", ranges=number, references=len(refs))), flush=True)

    by_function = defaultdict(list)
    for ref in refs:
        by_function[ref["function_begin"]].append(ref)
    cipher_functions = {ref["function_begin"] for ref in refs if by_id[ref["target_id"]]["kind"] == "exact_public_cipher_table"}
    pak_functions = {ref["function_begin"] for ref in refs if "pak_archive" in by_id[ref["target_id"]]["categories"]}
    callers = defaultdict(list)
    for begin, transfers in edges.items():
        for transfer in transfers:
            destination = transfer["direct_target_rva"]
            if destination in interval_by_begin:
                callers[destination].append(dict(caller_begin=begin, **transfer))
    for begin, item in functions.items():
        item["direct_callers"] = callers.get(begin, [])
    paths = []
    for source in sorted(pak_functions):
        pending = deque([(source, [])])
        visited = {source}
        while pending and len(visited) <= 20000:
            begin, path = pending.popleft()
            if begin in cipher_functions:
                paths.append(dict(source_function=source, cipher_function=begin, edges=path))
                continue
            if len(path) >= 6:
                continue
            for edge in edges.get(begin, []):
                destination = edge["direct_target_rva"]
                if destination in interval_by_begin and destination not in visited:
                    visited.add(destination)
                    pending.append((destination, path+[dict(caller_begin=begin, **edge)]))
    return dict(capstone_version=capstone.__version__, targets=targets, references=refs,
        functions=sorted(functions.values(), key=lambda f: f["begin"]), scan_counts=dict(stats),
        pak_functions=sorted(pak_functions), cipher_table_functions=sorted(cipher_functions),
        direct_pak_to_cipher_paths=paths, search_bounds=dict(max_call_edges=6, max_visited_functions_per_source=20000),
        boundary_method="Start at each original exception-table .text BeginAddress. Decode within EndAddress without skip-data/resynchronization. Follow both direct conditional arms; stop at returns/indirect jumps; exclude instructions not reached by that bounded direct-flow graph.")


def markdown(report):
    lines = ["# Static decoded-segment code links", "",
             "Phase: " + report["phase"], "",
             "No executable image was produced or run. Segment addresses are descriptor RVAs.", "",
             "## Input scope", "", "| Segment | RVA | Available bytes | SHA-256 |", "|---|---:|---:|---|"]
    for item in report["segments"]:
        lines.append(f"| {item['section']} | `{item['destination_rva']:#x}` | {item['examined_available_bytes']} | `{item['decoded_sha256']}` |")
    links = report.get("code_links")
    if links:
        targets = {target["id"]: target for target in links["targets"]}
        references = {ref["id"]: ref for ref in links["references"]}
        functions = links["functions"]
        def priority(function):
            return max(targets[references[key]["target_id"]]["relevance_score"] for key in function["reference_ids"])
        ordered = sorted(functions, key=lambda f: (-priority(f), f["begin"]))
        lines += ["", "## Anchored results", "",
                  f"Scanned {links['scan_counts'].get('ranges_scanned', 0)} original exception-directory .text ranges; "
                  f"{len(references)} verified RIP operands refer to selected resource strings/name slots or exact public cipher tables, "
                  f"in {len(functions)} ranges.", "",
                  links["boundary_method"], "",
                  "All instruction addresses below are RVAs. For each RIP operand, `instruction RVA + size + signed disp32 = target RVA`; "
                  "JSON includes the displacement bytes and the exact original exception-table record.", "",
                  "| Function Begin–End | Evidence labels | RIP refs | Direct callers |", "|---|---|---:|---:|"]
        for function in ordered[:50]:
            labels = list(dict.fromkeys(targets[references[key]["target_id"]]["text"] for key in function["reference_ids"]))
            label = "; ".join(value.replace("|", "\\|").replace("`", "'")[:100] for value in labels[:4])
            lines.append(f"| `{function['begin']:#x}–{function['end']:#x}` | {label} | {len(function['reference_ids'])} | {len(function['direct_callers'])} |")
        lines += ["", "## Resource-to-cipher path limit", "",
                  f"PAK/archive-labelled ranges: {', '.join(hex(x) for x in links['pak_functions']) or 'none'}. "
                  f"Exact S-box reference ranges: {', '.join(hex(x) for x in links['cipher_table_functions']) or 'none'}.", "",
                  f"Direct, exact-exception-entry paths of at most six edges: {len(links['direct_pak_to_cipher_paths'])}. "
                  "Indirect calls are not expanded. No path in this bounded graph is not proof of absence; a path is not proof of input/dataflow or successful PAK decryption."]
        for path in links["direct_pak_to_cipher_paths"][:12]:
            lines.append("- " + " → ".join([hex(path["source_function"])] + [hex(e["direct_target_rva"]) for e in path["edges"]]))
        lines += ["", "## Reviewable instruction evidence", "",
                  "Top 18 anchored ranges are expanded here; every selected range/reference remains in JSON. "
                  "Context may span conditional arms and is not a claim of one observed execution trace."]
        for function in ordered[:18]:
            lines += ["", f"### `{function['begin']:#x}–{function['end']:#x}`", "",
                      f"Original table record `{function['record_rva']:#x}`: `{function['record_bytes']}`. "
                      f"Unwind RVA `{function['unwind_rva']:#x}`, header `{function['unwind_header']}`. "
                      f"Code SHA-256 `{function['code_sha256']}`.", ""]
            candidates = sorted((references[key] for key in function["reference_ids"]),
                                key=lambda ref: (-targets[ref["target_id"]]["relevance_score"], ref["instruction_rva"]))
            for ref in candidates[:3]:
                target = targets[ref["target_id"]]
                label = target["text"].replace("`", "'")[:180]
                lines += [f"- `{ref['id']}`: `{label}` at `{target['rva']:#x}`; target offset `{ref['target_byte_offset']:#x}`. "
                          f"Operand: `{ref['instruction_rva']:#x} + {ref['instruction_size']} + ({ref['displacement']}) = {ref['target_rva']:#x}`.", "",
                          "```text"]
                for row in ref["context"]:
                    lines.append(f"{row['rva']:08x}  {row['bytes']:<30}  {row['instruction']}")
                lines += ["```", ""]
            if function["direct_callers"]:
                lines.append("Direct caller evidence (first 12):")
                for edge in function["direct_callers"][:12]:
                    lines.append(f"- caller `{edge['caller_begin']:#x}`, instruction `{edge['rva']:#x}` `{edge['bytes']}` → `{edge['direct_target_rva']:#x}`.")
    lines += ["", "## Matching string inventory", "", f"Matching runs: {len(report['strings'])}.", "",
              "| RVA | Segment offset | Categories | Text |", "|---|---:|---|---|"]
    for item in sorted(report["strings"], key=lambda s: (-s["relevance_score"], s["rva"]))[:150]:
        text = item["text"].replace("|", "\\|").replace("`", "'")[:240]
        lines.append(f"| `{item['rva']:#x}` | `{item['segment_offset']:#x}` | {', '.join(item['categories'])} | `{text}` |")
    lines += ["", "## Limits", ""] + ["- " + note for note in report["limitations"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    script_path = Path(__file__).resolve()
    script_hash = digest(script_path.read_bytes())
    segments, provenance, inputs, prior = load_segments()
    strings, totals = extract_strings(segments)
    print(json.dumps(dict(stage="strings", matching_runs=len(strings), totals=totals)), flush=True)
    report = dict(phase="string_inventory", script_sha256=script_hash, client_sha256=EXPECTED_CLIENT,
        inputs=inputs, segments=provenance, string_scan_counts=totals, keyword_patterns=KEYWORDS,
        strings=strings, limitations=[
            "Strings are byte runs, not proof that a function or game object uses them.",
            "Only the .text first 2293760 output bytes have the supplied native-prefix comparison; this does not prove executed code boundaries.",
            "Five other segments have independent static LZMA EOS/hash verification only; final fixups/section semantics are unverified.",
            "No missing .data bytes are filled. Function ranges use original PE exception directory #3 in .data2, not the decoded .pdata candidate.",
            "ASCII and ASCII-bearing UTF-16LE runs are searched; this is not an exhaustive Unicode string scan.",
            "No packet offset, ACT field layout, damage ownership or skill attribution is promoted from a name alone.",
        ])
    if not args.inventory_only:
        ranges, directory, image_base = load_ranges(prior, inputs)
        print(json.dumps(dict(stage="anchored_ranges", count=len(ranges), table_sha256=directory["sha256"])), flush=True)
        report["exception_directory"] = directory
        report["code_links"] = analyze_links(segments, ranges, strings, image_base, inputs)
        report["phase"] = "exception_anchored_resource_reference_review"
        report["limitations"] += [
            "The range table is original file-backed metadata; its record boundaries do not prove final restored code semantics or observed execution.",
            "Only .text exception ranges are scanned. Decode failures stop that range; no unaligned resynchronization, gap sweep, .pdata guessing or new cipher search is performed.",
            "Direct call paths are address/control-flow evidence only. Indirect calls, runtime import binding and buffer/key dataflow remain unresolved.",
            "Exact S-box references identify candidate cipher code, not PAK cipher use, a key/mode, or recovered resource plaintext.",
            "PNG/zlib diagnostics may belong to image handling and are not classified as PAK decompression without a connecting resource path.",
        ]
    report["elapsed_seconds"] = time.monotonic()-started
    report["changed_inputs"] = [path for path, value in inputs.items() if digest(Path(path).read_bytes()) != value]
    report["script_changed"] = digest(script_path.read_bytes()) != script_hash
    if report["changed_inputs"] or report["script_changed"]:
        raise ValueError("source files changed during review")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "code_links.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", "utf-8")
    (OUT / "code_links.md").write_text(markdown(report), "utf-8")
    print(json.dumps(dict(report="code_links.json", phase=report["phase"], seconds=report["elapsed_seconds"])), flush=True)


if __name__ == "__main__":
    sys.exit(main())
