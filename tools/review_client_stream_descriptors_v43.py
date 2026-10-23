"""Independent static descriptor research on a hash-pinned client byte file.

No Unicorn, DLL loader, process API, snapshot restore, or executable output.
Only this script and descriptors*.json reports are owned by this investigation.
The already exhausted global '45 00' scan is deliberately not repeated.
"""
import argparse
from collections import Counter
from hashlib import sha256
import io
import json
import lzma
from pathlib import Path
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/client_protection_v43_20260908"
CLIENT = Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")
EXPECTED_CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"


def identity(data):
    return sha256(data).hexdigest()


def pe_sections(raw):
    pe = struct.unpack_from("<I", raw, 0x3C)[0]
    if raw[:2] != b"MZ" or raw[pe:pe+4] != b"PE\0\0":
        raise ValueError("not a static PE file")
    machine, count = struct.unpack_from("<HH", raw, pe+4)
    optional_size = struct.unpack_from("<H", raw, pe+20)[0]
    optional = pe+24
    if machine != 0x8664 or struct.unpack_from("<H", raw, optional)[0] != 0x20B:
        raise ValueError("expected x64 PE32+")
    result = []
    for i in range(count):
        at = optional+optional_size+i*40
        name = raw[at:at+8].rstrip(b"\0").decode("ascii")
        size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", raw, at+8)
        flags = struct.unpack_from("<I", raw, at+36)[0]
        if raw_offset+raw_size > len(raw):
            raise ValueError("section raw extent exceeds file")
        result.append(dict(name=name, rva=rva, virtual_size=size,
                           raw_offset=raw_offset, raw_size=raw_size, characteristics=hex(flags)))
    return result


def file_offset(sections, rva, length=1):
    for s in sections:
        relative = rva-s["rva"]
        if 0 <= relative and relative+length <= s["raw_size"]:
            return s["raw_offset"]+relative
    raise ValueError(f"RVA {rva:#x}+{length:#x} is not completely file-backed")


def mapped_rva(sections, offset):
    for s in sections:
        if s["raw_offset"] <= offset < s["raw_offset"]+s["raw_size"]:
            return s["rva"]+offset-s["raw_offset"]
    return None


def read_rva(raw, sections, rva, length):
    at = file_offset(sections, rva, length)
    return raw[at:at+length]


def window(raw, sections, rva, length):
    data = read_rva(raw, sections, rva, length)
    return dict(rva=hex(rva), file_offset=hex(file_offset(sections, rva)), length=length,
                rows=[dict(rva=hex(rva+i), hex=data[i:i+16].hex(" ")) for i in range(0, length, 16)])


def marker_runs(raw, sections, marker, excluded):
    cursor = 0
    count = 0
    inside = 0
    runs = []
    while True:
        at = raw.find(marker, cursor)
        if at < 0:
            break
        cursor = at+1
        count += 1
        rva = mapped_rva(sections, at)
        if rva is not None and excluded[0] <= rva < excluded[1]:
            inside += 1
            continue
        if runs and runs[-1]["last_file_offset"]+len(marker) == at:
            runs[-1]["last_file_offset"] = at
            runs[-1]["repetitions"] += 1
        else:
            runs.append(dict(file_offset=at, rva=rva, last_file_offset=at, repetitions=1))
    return dict(marker=marker.hex(), total_occurrences=count, inside_known_stream=inside,
                outside_runs=runs)


def snapshot_evidence(raw, sections):
    """Read bytes from the supplied ZIP; never deserialize or restore CPU state."""
    trace_bytes = (OUT / "first_text_trace.json").read_bytes()
    trace = json.loads(trace_bytes)
    if trace["client_sha256"] != EXPECTED_CLIENT_SHA256:
        raise ValueError("first-text trace belongs to another client")
    blob = (OUT / "first_text_snapshot.zip").read_bytes()
    if identity(blob) != trace["snapshot_sha256"]:
        raise ValueError("first-text ZIP does not match the recorded snapshot hash")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        infos = archive.infolist()
        if len(infos) > 258 or sum(i.file_size for i in infos) > 256*1024*1024:
            raise ValueError("offline snapshot member budget")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["client_sha256"] != EXPECTED_CLIENT_SHA256:
            raise ValueError("offline memory manifest client mismatch")
        regions = manifest["regions"]
        cache = {}

        def memory(address, size):
            region = next((r for r in regions if r["base"] <= address and address+size <= r["base"]+r["size"]), None)
            if region is None:
                raise ValueError(f"unmapped saved bytes {address:#x}+{size:#x}")
            if region["name"] not in cache:
                data = archive.read(region["name"])
                if len(data) != region["size"] or identity(data) != region["sha256"]:
                    raise ValueError("offline memory member identity mismatch")
                cache[region["name"]] = data
            at = address-region["base"]
            return cache[region["name"]][at:at+size]

        allocations = manifest["state"]["allocations"]
        heaps = []
        for allocation in allocations:
            length = min(allocation["size"], 0x200)
            data = memory(allocation["base"], length)
            rows = []
            for i in range(0, length-7, 8):
                valid = all(any(lo <= j < hi for lo, hi in allocation["initialized"]) for j in range(i, i+8))
                value = struct.unpack_from("<Q", data, i)[0]
                rows.append(dict(offset=hex(i), bytes=data[i:i+8].hex(), initialized=valid,
                                 qword=hex(value), dwords=[hex(x) for x in struct.unpack_from("<II", data, i)]))
            heaps.append(dict(base=hex(allocation["base"]), allocation_bytes=allocation["size"],
                              initialized=allocation["initialized"], rows=rows))
        probability = next(a for a in allocations if a["base"] == int(trace["guest_registers"]["R15"], 16))
        probability_data = memory(probability["base"], probability["size"])
        probability_values = Counter(struct.unpack("<"+"H"*(len(probability_data)//2), probability_data))
        literal_offset = int(trace["guest_registers"]["RBX"],16)-probability["base"]
        probability_context = dict(allocation_base=hex(probability["base"]), allocation_bytes=probability["size"],
            rbx_offset=hex(literal_offset), words=probability["size"]//2,
            words_before_rbx=literal_offset//2, words_at_or_after_rbx=(probability["size"]-literal_offset)//2,
            u16_histogram={hex(k):v for k,v in probability_values.items()},
            evidence_instruction="first-text instruction tail: mov word ptr [rbx + rdx*2], ax",
            interpretation="Consistent with the supplied LZMA probability state; it does not identify other streams or destination descriptors.")
        rsp = int(trace["guest_registers"]["RSP"], 16)
        stack = []
        for item in trace["stack_qwords"]:
            address = rsp+item["offset"]
            actual = struct.unpack("<Q", memory(address, 8))[0]
            if actual != int(item["value"], 16):
                raise ValueError("trace stack and ZIP memory disagree")
            stack.append(dict(address=hex(address), offset=hex(item["offset"]), value=hex(actual)))
        pointed_windows = []
        addresses = [0x3001FF8D0, 0x3001FF8F8, 0x3001FF440]
        for address in addresses:
            data = memory(address, 0x100)
            pointed_windows.append(dict(address=hex(address), rows=[
                dict(address=hex(address+i), hex=data[i:i+16].hex(" "),
                     dwords=[hex(x) for x in struct.unpack_from("<IIII", data, i)]) for i in range(0, len(data), 16)]))
        # Look for source/output addresses and independently measured sizes in
        # saved stack/heaps. Every hit has its address, and is not a field claim.
        values = {"text_stream_rva": 0x240C386, "text_stream_va": 0x14240C386,
                  "text_destination_rva": 0x1000, "text_destination_va": 0x140001000,
                  "compressed_consumed": 4954015, "decoded_bytes": 15121920,
                  "text_virtual_size": 15121916, "stream_end_rva": 0x28C5B25}
        for section in sections[:6]:
            values[section["name"]+"_size"] = section["virtual_size"]
            values[section["name"]+"_rva"] = section["rva"]
        exact_hits = []
        targets = [(rsp, min(0x300200000-rsp, 0xC80), "active_stack")]
        targets += [(a["base"], a["size"], "heap") for a in allocations]
        for base, length, kind in targets:
            data = memory(base, length)
            for label, value in values.items():
                pattern = struct.pack("<Q" if value > 0xFFFFFFFF else "<I", value)
                cursor = 0
                hits = []
                while len(hits) < 32:
                    at = data.find(pattern, cursor)
                    if at < 0:
                        break
                    hits.append(hex(base+at)); cursor = at+1
                if hits:
                    exact_hits.append(dict(kind=kind, label=label, value=hex(value), addresses=hits,
                                           capped=len(hits) == 32))
        # Context-pointed windows in the file image, without assuming a table.
        image_windows = []
        for rva in (0x236902C, 0x227703B):
            data = memory(0x140000000+rva, 0x100)
            static = read_rva(raw, sections, rva, len(data))
            image_windows.append(dict(rva=hex(rva), equals_static=data == static,
                                      window=window(raw, sections, rva, len(data))))
    return dict(trace_sha256=identity(trace_bytes), snapshot_sha256=identity(blob),
                snapshot_cpu_restored=False, guest_executed=False,
                metadata_reads=[{k:v for k,v in r.items() if k != "tail"} for r in trace["metadata_reads"]],
                registers=trace["guest_registers"], heaps=heaps, stack=stack,
                probability_context=probability_context,
                pointed_stack_windows=pointed_windows, exact_value_hits=exact_hits,
                pointed_image_windows=image_windows,
                heap_caveat="Uninitialized saved bytes are not reliable guest values; rows mark their initialized status.")


def table_candidates(raw, sections, whole, decoded_text):
    """Only pointers from the trace-referenced table; no byte-prefix search."""
    table = 0x236902C
    header = read_rva(raw, sections, table, 8)
    records = []
    termination = None
    for index in range(16):
        record_rva = table+8+index*8
        source, destination = struct.unpack("<II", read_rva(raw, sections, record_rva, 8))
        section = next((s for s in sections if s["rva"] == destination), None)
        if section is None:
            termination = dict(rva=hex(record_rva), bytes=read_rva(raw, sections, record_rva, 8).hex(),
                               u32=[hex(source),hex(destination)], reason="next pair has no PE section-start destination")
            break
        record = dict(index=index, record_rva=hex(record_rva), record_bytes=read_rva(raw, sections, record_rva, 8).hex(),
                      source_rva=hex(source), destination_rva=hex(destination),
                      destination_section=section["name"] if section else None,
                      native_destination_observed=source == whole["stream_rva"])
        packed = next(s for s in sections if s["rva"] <= source < s["rva"]+s["raw_size"])
        record["source_file_offset"] = hex(file_offset(sections, source))
        record["byte_before_source"] = read_rva(raw, sections, source-1, 1).hex()
        record["source_first32"] = read_rva(raw, sections, source, 32).hex()
        if source == whole["stream_rva"]:
            result = decoded_text
            consumed = whole["compressed_consumed"]
            record["decoder"] = dict(reused="whole_stream_probe.json", new_decode=False,
                eof=whole["eof"], error=whole["error"], decoded_bytes=len(result),
                decoded_sha256=identity(result), compressed_consumed=consumed,
                stream_end_rva=hex(source+consumed), native_prefix_verified=True)
        else:
            data = read_rva(raw, sections, source, packed["rva"]+packed["raw_size"]-source)
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
                dict(id=lzma.FILTER_LZMA1, dict_size=32*1024*1024, lc=3, lp=0, pb=2)])
            result = bytearray()
            supplied = data
            error = None
            limit = 32*1024*1024
            try:
                while len(result) < limit:
                    block = decoder.decompress(supplied, max_length=min(65536, limit-len(result)))
                    supplied = b""
                    result.extend(block)
                    if decoder.eof or decoder.needs_input or not block:
                        break
            except lzma.LZMAError as exc:
                error = str(exc)
            consumed = len(data)-len(decoder.unused_data) if decoder.eof else None
            record["decoder"] = dict(filters="raw LZMA1 lc=3 lp=0 pb=2 dict=33554432", error=error,
                eof=decoder.eof, needs_input=decoder.needs_input, decoded_bytes=len(result),
                compressed_consumed=consumed, stream_end_rva=hex(source+consumed) if consumed is not None else None,
                decoded_sha256=identity(result), first64=result[:64].hex(), last32=result[-32:].hex(),
                declared_target_virtual_size=section["virtual_size"],
                equals_target_size=len(result) == section["virtual_size"],
                equals_target_size_rounded4=len(result) == ((section["virtual_size"]+3)&~3),
                native_prefix_verified=False)
        size = section["virtual_size"]
        record["length_relationship"] = dict(virtual_size=size, decoded_bytes=len(result),
            decoded_minus_virtual=len(result)-size, virtual_rounded_512=(size+511)&~511,
            equals_virtual_rounded_512=len(result) == ((size+511)&~511),
            all_extra_output_zero=not any(result[size:]) if len(result)>=size else None,
            extra_output_bytes=max(0,len(result)-size), unfilled_virtual_bytes=max(0,size-len(result)),
            ownership_note="Destination is an explicit table field. Length is measured at LZMA EOS, not inferred from the section size.")
        if consumed is not None:
            record["compressed_sha256"] = identity(read_rva(raw, sections, source, consumed))
        if section["name"] == ".pdata":
            triples = [struct.unpack_from("<III", result, at) for at in range(0, min(60,len(result)),12)]
            record["post_decode_caution"] = dict(first_five_u32_triples=[[hex(x) for x in triple] for triple in triples],
                conforms_to_plain_x64_runtime_function_at_start=all(a<b<0x2E6F000 and 0<c<0x2E6F000 for a,b,c in triples),
                conclusion="These first triples are not ordinary in-image RUNTIME_FUNCTION RVAs. Successful decompression does not prove final PE section semantics; later transforms may be needed.")
        records.append(record)
    if len(records) != 6:
        raise ValueError("observed static table extent changed")
    intervals = sorted((int(r["source_rva"],16),int(r["decoder"]["stream_end_rva"],16)) for r in records)
    if any(end > next_start for (_,end),(next_start,_) in zip(intervals,intervals[1:])):
        raise ValueError("decoded stream input extents overlap")
    table_data = read_rva(raw, sections, table, 8+8*len(records))
    return dict(table_rva=hex(table), evidence="first-text stack +0x50 contains VA 0x14236902c; bytes equal original static client",
                header_bytes=header.hex(), header_u32=[hex(x) for x in struct.unpack("<II", header)],
                header_semantics="Unresolved: do not assume that 5 is a count or that the first dword is a stream.",
                record_format="six observed little-endian {u32 source_rva,u32 destination_rva} pairs at table+8",
                table_bytes=table_data.hex(), table_sha256=identity(table_data),
                table_exact_occurrences_in_file=raw.count(table_data), termination=termination,
                all_six_target_distinct_pe_sections=len({r["destination_rva"] for r in records})==6,
                input_extents_nonoverlapping=True, records=records,
                section_descriptor_window=window(raw, sections, 0x227703B-0x60, 0xC0))


def length_table(raw, sections):
    base = 0x227703B
    records = []
    for i in range(6):
        data = read_rva(raw, sections, base+i*12, 12)
        target, size, characteristics = struct.unpack("<III", data)
        exact = next((s for s in sections if (s["rva"],s["virtual_size"],int(s["characteristics"],16)) ==
                      (target,size,characteristics)), None)
        container = next((s for s in sections if s["rva"] <= target and target+size <= s["rva"]+s["virtual_size"]), None)
        records.append(dict(record_rva=hex(base+i*12), bytes=data.hex(), destination_rva=hex(target),
            length=size, characteristics=hex(characteristics), exact_pe_section=exact["name"] if exact else None,
            containing_section=container["name"] if container else None,
            offset_in_containing_section=hex(target-container["rva"]) if container else None))
    return dict(table_rva=hex(base), trace_stack_offset="0xe8", record_bytes=12,
                format="little-endian {u32 destination_rva,u32 byte_length,u32 PE_characteristics}",
                records=records, exact_pe_section_matches=sum(r["exact_pe_section"] is not None for r in records),
                caveat="Five exact PE header triples establish the numeric correspondence. The sixth is a .data0 subrange with different flags; applying protections and its runtime role are not proven by static bytes alone.")


def analyze():
    raw = CLIENT.read_bytes()
    if identity(raw) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client identity mismatch; refusing another build")
    reports = {}
    report_identities = {}
    for name in ("lzma_probe.json", "whole_stream_probe.json", "stream_candidates.json"):
        data = (OUT / name).read_bytes()
        reports[name] = json.loads(data)
        report_identities[name] = identity(data)
        if reports[name]["client_sha256"] != EXPECTED_CLIENT_SHA256:
            raise ValueError("prior report client mismatch")
    evidence, whole = reports["lzma_probe.json"], reports["whole_stream_probe.json"]
    sections = pe_sections(raw)
    start = evidence["source_rva"]
    end = start+whole["compressed_consumed"]
    source = read_rva(raw, sections, start, evidence["source_bytes"])
    if identity(source) != evidence["source_sha256"]:
        raise ValueError("supplied stream is not the static source bytes")
    decoded = (OUT / "raw_stream_candidate.bin").read_bytes()
    text = (OUT / "text_candidate_lc3_lp0_pb2.bin").read_bytes()
    if identity(decoded) != whole["decoded_sha256"] or len(decoded) != whole["decoded_bytes"]:
        raise ValueError("whole-stream artifact changed or mismatched")
    if decoded[:len(text)] != text or identity(text) != evidence["accepted"][0]["decoded_sha256"]:
        raise ValueError("text candidate differs from whole-stream prefix")
    prefix = evidence["native_prefix_bytes"]
    if identity(decoded[:prefix]) != evidence["native_prefix_sha256"]:
        raise ValueError("recorded native-prefix hash mismatch")
    data2 = next(s for s in sections if s["name"] == ".data2")
    result = dict(
        analysis="static descriptor candidates; not runtime attribution",
        client=dict(path=str(CLIENT), sha256=identity(raw), bytes=len(raw)),
        script_sha256=identity(Path(__file__).read_bytes()), prior_report_sha256=report_identities,
        pe_sections=sections,
        confirmed_text_stream=dict(start_rva=hex(start), end_rva=hex(end), compressed_bytes=end-start,
            decoded_bytes=len(decoded), decoded_sha256=identity(decoded), text_bytes=len(text),
            padding_hex=decoded[len(text):].hex(), native_prefix_bytes=prefix,
            native_prefix_sha256=identity(decoded[:prefix]),
            native_prefix_note="Hash cross-check against supplied probe report, not new guest execution.",
            earlier_whole_report_native_prefix_bytes=whole["native_verified_prefix_bytes"]),
        windows=[window(raw, sections, start-0x100, 0x120),
                 window(raw, sections, end-0x40, 0x180),
                 window(raw, sections, data2["rva"], 0x100)],
        marker_runs=[marker_runs(raw, sections, bytes.fromhex(marker), (start,end))
                     for marker in ("f39b233e", "9b233e", "3a03c7")],
        first_text_context=snapshot_evidence(raw, sections),
        table_candidates=table_candidates(raw, sections, whole, decoded),
        length_and_characteristics_table=length_table(raw, sections),
        not_repeated=dict(scan="global 45 00", prior_candidates=reports["stream_candidates.json"]["candidates"],
                          prior_streams=reports["stream_candidates.json"]["streams"]),
        caveat="Repeated bytes, matching lengths and XOR suggestions alone do not establish descriptor semantics or destination ownership.",
    )
    result["findings"] = dict(
        established=["A trace-referenced plaintext table associates six source RVAs with six distinct PE section-start RVAs.",
            "The known text stream is reused; all five additional table-derived sources independently reach LZMA EOS.",
            "The six compressed extents are disjoint. No global 45 00 scan was repeated.",
            "A second trace-referenced table has five exact RVA/VirtualSize/Characteristics triples from the PE headers.",
            "The first-text saved probability allocation is 15980 bytes; RBX is R15+0xe6c (1846 words), leaving 6144 words."],
        not_established=["The first table's header dwords 0x23b1686 and 5 are not assigned semantics.",
            "No field XOR key is needed for the two plaintext tables; no XOR/header role was proven for neighboring 9b233e/3a03c7 patterns.",
            "Only text has the supplied native output-prefix verification. The five other streams are static decode results.",
            "The .data stream fills only 331776 of 1879176 virtual bytes. The remaining bytes cannot be declared restored.",
            "The .pdata decompression begins with values inconsistent with plain x64 RUNTIME_FUNCTION records; later processing remains unresolved."],
        next_native_checks=["Observe reads at 0x236902c..0x2369063 to establish table iteration/header use.",
            "Confirm destination and output extent when moving from text to table row 1 (.rdata).",
            "Observe use of 0x227703b..0x2277082 to distinguish protection-range metadata from other uses."],
        boundary="No model, runner, tests, checkpoints or game file changed; no guest/process/DLL execution; only descriptors JSON emitted.")
    if identity(CLIENT.read_bytes()) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client changed during read-only analysis")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="descriptors.json")
    parser.add_argument("--details", action="store_true", help="also print the complete evidence report")
    args = parser.parse_args()
    if not args.report.startswith("descriptors") or not args.report.endswith(".json") or Path(args.report).name != args.report:
        raise SystemExit("only descriptors*.json report files are permitted")
    result = analyze()
    output = OUT / args.report
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = dict(report=str(output), report_sha256=identity(output.read_bytes()),
                   client_sha256=result["client"]["sha256"],
                   streams=result["table_candidates"]["records"], findings=result["findings"])
    print(json.dumps(result if args.details else summary, indent=2, ensure_ascii=False))
