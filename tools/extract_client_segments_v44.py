"""Static, hash-pinned decompression into non-executable research segments.

This does not rebuild/load a PE, resolve imports, perform post-decode fixups,
execute guest code, or change an ACT parser. Unknown bytes remain unknown.
"""
from hashlib import sha256
import json
import lzma
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.review_client_stream_descriptors_v43 import (
    CLIENT, EXPECTED_CLIENT_SHA256, pe_sections, read_rva,
)

PRIOR = ROOT / "outputs/client_protection_v43_20260908"
OUT = ROOT / "outputs/client_protection_v44_20260908"
TABLE_RVA = 0x236902C
TABLE_SHA256 = "ca275c71ec6b22aeb02a691d13ce21da339ba58b9ae55c1f3590010a3aef2c0e"
MAX_DECODED = 32 * 1024 * 1024
EXPECTED = (
    (".text", 0x240C386, 0x1000),
    (".rdata", 0x21AFBBC, 0xE6D000),
    (".data", 0x235758C, 0x1132000),
    (".pdata", 0x22C76A0, 0x12FD000),
    ("_RDATA", 0x22977AC, 0x1384000),
    (".data0", 0x1B9094E, 0x1385000),
)


def digest(data):
    return sha256(data).hexdigest()


def decode_exact(data, output_limit=MAX_DECODED):
    """Decode precisely one stream; fail on truncation, trailing bytes, or cap."""
    if not 0 < output_limit <= MAX_DECODED:
        raise ValueError("invalid output budget")
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
        dict(id=lzma.FILTER_LZMA1, lc=3, lp=0, pb=2, dict_size=32*1024*1024)
    ])
    output = bytearray()
    supplied = data
    while True:
        # One extra byte distinguishes an exact-sized output from truncation.
        block = decoder.decompress(supplied, max_length=min(65536, output_limit+1-len(output)))
        supplied = b""
        output.extend(block)
        if len(output) > output_limit:
            raise ValueError("output budget exceeded")
        if decoder.eof:
            if decoder.unused_data:
                raise ValueError("input includes bytes after stream EOS")
            return bytes(output)
        if decoder.needs_input:
            raise ValueError("truncated stream, no EOS")
        if not block:
            raise ValueError("decoder made no progress")


def safe_artifact(path, body):
    # Deterministic research outputs are never silently replaced with other data.
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError(f"existing artifact differs: {path.name}")
        return
    path.write_bytes(body)


def prepare(raw, prior):
    if digest(raw) != EXPECTED_CLIENT_SHA256 or prior["client"]["sha256"] != EXPECTED_CLIENT_SHA256:
        raise ValueError("client identity changed")
    sections = pe_sections(raw)
    table = read_rva(raw, sections, TABLE_RVA, 8+6*8)
    if digest(table) != TABLE_SHA256:
        raise ValueError("descriptor table identity changed")
    records = prior["table_candidates"]["records"]
    if len(records) != 6:
        raise ValueError("unexpected descriptor count")
    planned = []
    intervals = []
    for index, ((name, source, target), evidence) in enumerate(zip(EXPECTED, records)):
        if struct.unpack_from("<II", table, 8+8*index) != (source, target):
            raise ValueError("descriptor source/destination mismatch")
        section = next(s for s in sections if s["name"] == name and s["rva"] == target)
        if (evidence["destination_section"], int(evidence["source_rva"], 16),
            int(evidence["destination_rva"], 16)) != (name, source, target):
            raise ValueError("prior evidence descriptor mismatch")
        result = evidence["decoder"]
        if not result["eof"] or result["error"] is not None:
            raise ValueError("prior decode did not reach EOS")
        count = result["compressed_consumed"]
        if not isinstance(count, int) or not 1 <= count <= len(raw):
            raise ValueError("invalid compressed length")
        compressed = read_rva(raw, sections, source, count)
        if digest(compressed) != evidence["compressed_sha256"]:
            raise ValueError("compressed input mismatch")
        decoded = decode_exact(compressed)
        if len(decoded) != result["decoded_bytes"] or digest(decoded) != result["decoded_sha256"]:
            raise ValueError("independent output mismatch")
        size = section["virtual_size"]
        extra = decoded[size:]
        if extra and any(extra):
            raise ValueError("nonzero output beyond declared virtual section")
        if len(decoded) > ((size+4095)&~4095):
            raise ValueError("stream overlaps next section page")
        output_name = name.lstrip(".")+".bin"
        item = dict(section=name, source_rva=source, destination_rva=target,
            compressed_bytes=count, compressed_sha256=digest(compressed),
            decoded_bytes=len(decoded), decoded_sha256=digest(decoded),
            virtual_size=size, zero_padding_bytes=len(extra),
            not_decoded_virtual_bytes=max(0,size-len(decoded)),
            path="segments/"+output_name, final_section_semantics_verified=False,
            native_verified_prefix_bytes=0)
        if name == ".text":
            proof = prior["confirmed_text_stream"]
            length = proof["native_prefix_bytes"]
            if digest(decoded[:length]) != proof["native_prefix_sha256"]:
                raise ValueError("original-instruction native prefix mismatch")
            item["native_verified_prefix_bytes"] = length
            item["native_prefix_sha256"] = proof["native_prefix_sha256"]
        planned.append((item, decoded))
        intervals.append((source, source+count))
    intervals.sort()
    if any(hi > lo2 for (_,hi),(lo2,_) in zip(intervals,intervals[1:])):
        raise ValueError("overlapping compressed streams")
    return planned


def main():
    raw = CLIENT.read_bytes()
    evidence_bytes = (PRIOR / "descriptors.json").read_bytes()
    prior = json.loads(evidence_bytes)
    planned = prepare(raw, prior)
    # No artifact is written until every stream passes its independent checks.
    if digest(CLIENT.read_bytes()) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client changed during static analysis")
    (OUT / "segments").mkdir(parents=True, exist_ok=True)
    for item, body in planned:
        safe_artifact(OUT / item["path"], body)
    manifest = dict(client_sha256=EXPECTED_CLIENT_SHA256, client_file_bytes=len(raw),
        descriptor_report_sha256=digest(evidence_bytes), table_rva=TABLE_RVA,
        table_sha256=TABLE_SHA256, script_sha256=digest(Path(__file__).read_bytes()),
        total_decoded_bytes=sum(len(body) for _,body in planned),
        segments=[item for item,_ in planned],
        limitations=["Raw segment candidates, not an executable PE or complete client unpack.",
            "Only the .text native prefix is cross-validated against original guest instructions.",
            "No implicit zero-fill claim for missing .data virtual bytes.",
            "Later fixups/transforms, import bindings and game-object semantics remain unverified.",
            "No production attribution/scoring changes or measured job-coverage increase."])
    safe_artifact(OUT / "segments_manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
