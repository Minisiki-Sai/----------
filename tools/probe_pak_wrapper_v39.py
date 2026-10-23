"""Bounded, offline PAK-body diagnostics. No executable/dump/process access.

Only reports are written, under outputs/pak_wrapper_v39_20260908. No plaintext
is exported. A valid codec candidate is not a validated skill/damage link.
Run with Python -B to avoid creating bytecode outside the allowed output area.
"""
from __future__ import annotations

import argparse
import bz2
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import lzma
import math
from pathlib import Path
import re
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/pak_wrapper_v39_20260908"
CLIENT = Path("D:/DragonNest/Reborn")
OLD = Path("E:/ext0811.rar/Resource00/Resource00/resource")
INDEX = ROOT / "outputs/client_resources_v37_20260908/index_review.json"
SOURCE = "https://vincentzhang96.github.io/DragonNestFileFormats/files/pak/"
MAGIC = b"EyedentityGames Packing File 0.1\0"
MAX_BODY = 8 * 1024**2
MAX_RAW = 16 * 1024**2
MAX_DIRECTORY = 64 * 1024**2
MAX_TOTAL_DIRECTORY = 128 * 1024**2
MAX_TOTAL_BYTES = 64 * 1024**2
MAX_ATTEMPTS = 160
MAX_SIGNATURES = 32
COMMON_OFFSETS = (0, 1, 2, 4, 8, 12, 16, 20, 24, 28, 32, 48, 64,
                  80, 96, 112, 128, 256, 512, 1024)
PERIODS = (1, 2, 4, 8, 16)
EXTRA_SUFFIXES = (".act", ".lua", ".dnt", ".dds", ".tga", ".png", ".jpg", ".ogg", ".wav")


class Invalid(ValueError):
    """Explicit validation failures remain active with python -O."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


def digest(data):
    return sha256(data).hexdigest()


@dataclass
class Budget:
    body: int = 0
    old: int = 0
    decoded: int = 0

    def charge(self, kind, amount):
        require(0 <= amount <= MAX_TOTAL_BYTES - getattr(self, kind),
                f"{kind}_total_budget")
        setattr(self, kind, getattr(self, kind) + amount)


def read_exact(stream, count):
    require(0 <= count <= MAX_DIRECTORY, "read_bound")
    data = stream.read(count)
    require(len(data) == count, "short_read")
    return data


def normalize_name(raw):
    require(b"\0" in raw, "unterminated_name")
    name = raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape").replace("\\", "/").lstrip("/")
    require(name and ":" not in name and all(p not in ("", ".", "..") for p in name.split("/")),
            "unsafe_resource_path")
    return name


def parse_entry(data, directory_offset, archive):
    require(len(data) == 316, "entry_size")
    name = normalize_name(data[:256])
    indexed, length, body, offset, flags = struct.unpack_from("<5I", data, 256)
    require(1024 <= offset < directory_offset and 0 < body <= directory_offset - offset,
            "entry_outside_body_region")
    return dict(archive=archive, name=name, indexed_size=indexed, length=length,
                compressed=body, offset=offset, flags=flags,
                special_metadata=indexed == length == 0)


def read_directory(stream, size, meta):
    """Check the supplied v37 census hash before trusting any directory bytes."""
    require(size == meta["archive_size"], "archive_size_changed")
    stream.seek(0)
    header = read_exact(stream, 1024)
    require(header.startswith(MAGIC), "archive_magic")
    version, count, start = struct.unpack_from("<3I", header, 256)
    require(version == meta["version"] == 11, "archive_version")
    require(0 < count and count * 316 <= MAX_DIRECTORY, "directory_budget")
    require(count == meta["entries"] and start == meta["directory_offset"], "directory_header_changed")
    require(1024 <= start and start + count * 316 == size, "directory_extent")
    stream.seek(start)
    raw = read_exact(stream, count * 316)
    require(digest(raw) == meta["directory_sha256"], "directory_checksum_changed")
    rows = []
    for position in range(0, len(raw), 316):
        row = parse_entry(raw[position:position + 316], start, meta["archive"])
        row["directory_offset"] = start + position
        rows.append(row)
    return rows


def confined_file(root, relative):
    root = root.resolve()
    target = (root / relative).resolve()
    require(target.is_relative_to(root) and target != root, "path_outside_root")
    return target


def old_path(name):
    require(name.startswith("resource/"), "old_reference_not_resource")
    return confined_file(OLD, name.removeprefix("resource/"))


def safe_extra(row):
    name = row["name"].lower()
    suffix = Path(name).suffix
    if not name.startswith("resource/") or not (128 <= row["compressed"] <= 128 * 1024
                                               and 0 < row["length"] <= 1024**2):
        return False
    if suffix in (".dds", ".tga", ".png", ".jpg", ".ogg", ".wav"):
        return True
    if suffix == ".act":
        return name.startswith(("resource/char/", "resource/weapon/"))
    if suffix == ".lua":
        return name.startswith("resource/script/monsterai/")
    if suffix == ".dnt":
        return name.startswith(("resource/ext/skill", "resource/ext/monster", "resource/ext/actor"))
    return False


def select_extras(rows, selected, maximum):
    require(type(maximum) is int and 0 <= maximum <= 32, "extra_entry_cap")
    used = {(r["archive"], r["directory_offset"]) for r in selected}
    buckets = defaultdict(list)
    for row in rows:
        if safe_extra(row) and (row["archive"], row["directory_offset"]) not in used:
            buckets[Path(row["name"]).suffix.lower()].append(row)
    # Four passes across asset kinds, each spread across different archives
    # when possible. Selection uses directory metadata only, no extra bodies.
    picked, per_kind_archives = [], defaultdict(set)
    while len(picked) < maximum:
        changed = False
        for suffix in EXTRA_SUFFIXES:
            options = buckets[suffix]
            if not options or len(picked) == maximum:
                continue
            index = next((i for i, r in enumerate(options)
                          if r["archive"] not in per_kind_archives[suffix]), 0)
            row = options.pop(index)
            picked.append(row)
            per_kind_archives[suffix].add(row["archive"])
            changed = True
        if not changed:
            break
    return picked


def decode_exact(payload, codec, expected, old_sha256=None, budget=None):
    """No trailing/padded/concatenated streams. Raw DEFLATE needs an old hash."""
    require(0 < len(payload) <= MAX_BODY, "compressed_size_bound")
    require(0 < expected <= MAX_RAW, "declared_length_bound")
    budget = budget if budget is not None else Budget()
    limit = min(expected + 1, MAX_TOTAL_BYTES - budget.decoded + 1)
    require(limit > 1, "decoded_total_budget")
    checksum = None
    try:
        if codec in ("zlib", "gzip", "raw_deflate"):
            stream = zlib.decompressobj({"zlib": 15, "gzip": 31, "raw_deflate": -15}[codec])
            raw = stream.decompress(payload, limit)
            tail = stream.unconsumed_tail
            checksum = {"zlib": "adler32", "gzip": "crc32+isize", "raw_deflate": None}[codec]
        elif codec == "bzip2":
            stream = bz2.BZ2Decompressor()
            raw = stream.decompress(payload, max_length=limit)
            tail = b""
            checksum = "bzip2_crc"
        elif codec == "xz":
            stream = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=32 * 1024**2)
            raw = stream.decompress(payload, max_length=limit)
            tail = b""
            checksum = {lzma.CHECK_CRC32: "xz_crc32", lzma.CHECK_CRC64: "xz_crc64",
                        lzma.CHECK_SHA256: "xz_sha256"}.get(stream.check)
        else:
            raise Invalid("unsupported_codec")
    except (zlib.error, OSError, EOFError, lzma.LZMAError) as exc:
        raise Invalid("codec_error") from exc
    budget.charge("decoded", len(raw))
    require(len(raw) <= expected, "output_exceeds_declared_length")
    require(stream.eof, "incomplete_stream")
    require(not stream.unused_data and not tail, "trailing_data")
    require(len(raw) == expected, "exact_length_mismatch")
    actual_sha256 = digest(raw)
    old_match = old_sha256 is not None and actual_sha256 == old_sha256
    require(checksum is not None or old_match, "missing_independent_checksum")
    return dict(codec=codec, decoded_length=len(raw), sha256=actual_sha256,
                crc32=f"{zlib.crc32(raw):08x}", adler32=f"{zlib.adler32(raw):08x}",
                verified_checksum=checksum or "exact_old_sha256",
                old_exact_match=old_match)


def signatures(body):
    """Only inspect the first 1024 bytes; a signature is not a decode result."""
    found = []
    magic = ((b"\x1f\x8b\x08", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\0", "xz"),
             (b"\x28\xb5\x2f\xfd", "zstd_unimplemented"), (b"\x04\x22\x4d\x18", "lz4_unimplemented"),
             (b"PK\x03\x04", "zip_unimplemented"), (b"DDS ", "dds_plain_signature"),
             (b"\x89PNG\r\n\x1a\n", "png_plain_signature"), (b"OggS", "ogg_plain_signature"))
    head = body[:1024]
    for i in range(len(head)):
        if i + 2 <= len(head):
            cmf, flg = head[i:i + 2]
            if cmf & 15 == 8 and cmf >> 4 <= 7 and (cmf * 256 + flg) % 31 == 0:
                found.append(dict(offset=i, codec="zlib", preset_dictionary=bool(flg & 32)))
        for prefix, codec in magic:
            if head.startswith(prefix, i):
                found.append(dict(offset=i, codec=codec))
        if len(found) >= MAX_SIGNATURES:
            return found[:MAX_SIGNATURES], True
    return found, False


def reference_variants(old):
    variants = [("stored", old)]
    for level in (1, 6, 9):
        variants.append((f"zlib_level_{level}", zlib.compress(old, level)))
    stream = zlib.compressobj(level=6, wbits=-15)
    variants.append(("raw_deflate_level_6", stream.compress(old) + stream.flush()))
    return variants


def size_hypothesis(row, compressed_length):
    """Observed size relation only; it does not identify padding or a cipher."""
    predicted = 16 + 16 * (compressed_length // 16 + 1)
    return dict(old_zlib_level1_length=compressed_length,
                formula="16 + 16 * (old_zlib_level1_length // 16 + 1)",
                predicted_indexed_size=predicted,
                indexed_size_matches=row["indexed_size"] == predicted,
                body_size_matches=row["compressed"] == predicted,
                indexed_minus_prediction=row["indexed_size"] - predicted,
                status="length-only hypothesis; no decoded-byte or algorithm validation")


def derive_simple_transform(encoded, known, operation, period):
    """Derive from 64 known bytes, then require >=32 held-out matching bytes.

    This is not a key search: no byte value is enumerated. A short repeating
    XOR/add mask is determined entirely from the supplied equivalent bytes.
    """
    require(operation in ("xor", "add") and period in PERIODS, "transform_domain")
    count = min(len(encoded), len(known), 192)
    if count < 96:
        return None
    delta = bytes((encoded[i] ^ known[i]) if operation == "xor"
                  else (encoded[i] - known[i]) & 255 for i in range(count))
    mask = delta[:period]
    if not any(mask) or any(delta[i] != mask[i % period] for i in range(64)):
        return None
    if any(delta[i] != mask[i % period] for i in range(64, count)):
        return None
    return mask


def apply_simple_transform(payload, operation, mask):
    require(operation in ("xor", "add") and len(mask) in PERIODS, "transform_domain")
    return bytes((value ^ mask[i % len(mask)]) if operation == "xor"
                 else (value - mask[i % len(mask)]) & 255 for i, value in enumerate(payload))


def probe_body(body, row, old=None, budget=None):
    require(len(body) == row["compressed"] and 0 < len(body) <= MAX_BODY, "body_length")
    budget = budget if budget is not None else Budget()
    expected = row["length"]
    old_hash = digest(old) if old is not None else None
    result = dict(**row, body_sha256=digest(body), prefix_hex=body[:32].hex(),
                  suffix_hex=body[-16:].hex(), old=None, accepted=[],
                  checksum_valid_but_unresolved_suffix=[], rejection_counts={}, attempts=0)
    counts = Counter(body[:4096])
    n = sum(counts.values())
    blocks = [body[i:i + 16] for i in range(0, len(body) - 15, 16)]
    result["structure"] = dict(body_mod16=len(body) % 16, offset_mod16=row["offset"] % 16,
                               indexed_mod16=row["indexed_size"] % 16,
                               body_minus_indexed=len(body) - row["indexed_size"],
                               entropy_first_4096=-sum((v / n) * math.log2(v / n) for v in counts.values()),
                               repeated_aligned_blocks=len(blocks) - len(set(blocks)))
    result["header_size_matches"] = [dict(offset=i, endian=endian, value=value)
        for i in range(0, min(64, len(body)) - 3, 4)
        for endian in ("little", "big")
        if (value := int.from_bytes(body[i:i + 4], endian)) in
        (row["length"], row["indexed_size"], row["compressed"])]
    sigs, capped = signatures(body)
    result["signatures"] = sigs
    result["signatures_capped"] = capped
    if old is not None:
        require(len(old) <= MAX_RAW, "old_length_bound")
        result["old"] = dict(length=len(old), sha256=old_hash, same_declared_length=len(old) == expected)
    if not 0 < expected <= MAX_RAW:
        result["rejection_counts"] = {"declared_length_bound": 1}
        return result
    ends = [len(body)]
    if 0 < row["indexed_size"] < len(body):
        ends.append(row["indexed_size"])
    candidates = {(offset, codec) for offset in COMMON_OFFSETS if offset < len(body)
                  for codec in ("zlib", "raw_deflate")}
    candidates.update((s["offset"], s["codec"]) for s in sigs
                      if s["codec"] in ("zlib", "gzip", "bzip2", "xz"))
    failures = Counter()

    def attempt(payload, codec, offset, end, transform=None):
        if result["attempts"] >= MAX_ATTEMPTS:
            failures["attempt_cap"] += 1
            return
        result["attempts"] += 1
        try:
            verified = decode_exact(payload, codec, expected, old_hash, budget)
        except Invalid as exc:
            failures[str(exc)] += 1
            return
        verified.update(offset=offset, end=end, skipped_prefix=offset,
                        outer_suffix_bytes=len(body) - end, transform=transform)
        target = "accepted" if end == len(body) else "checksum_valid_but_unresolved_suffix"
        result[target].append(verified)

    for end in ends:
        for offset, codec in sorted(candidates):
            if offset < end:
                attempt(body[offset:end], codec, offset, end)

    # Direct stored equivalence is accepted only for exact length and SHA-256.
    if old is not None and len(old) == expected:
        for offset in COMMON_OFFSETS:
            if len(body) - offset == expected and digest(body[offset:]) == old_hash:
                result["accepted"].append(dict(codec="stored", offset=offset, end=len(body),
                    skipped_prefix=offset, outer_suffix_bytes=0, transform=None,
                    decoded_length=expected, sha256=old_hash, verified_checksum="exact_old_sha256",
                    old_exact_match=True))

    transform_stats = dict(comparisons=0, heldout_supported=0, applied=0,
                           periods=list(PERIODS), training_bytes=64, minimum_heldout_bytes=32)
    result["simple_transforms"] = transform_stats
    if old is not None:
        variants = reference_variants(old)
        result["old"]["recompressed_candidates"] = [dict(codec=kind, length=len(value), sha256=digest(value))
                                                    for kind, value in variants]
        result["old"]["size_hypothesis"] = size_hypothesis(row, len(variants[1][1]))
        applied = set()
        for kind, known in variants:
            for offset in COMMON_OFFSETS:
                if len(body) - offset < 96:
                    continue
                sample = body[offset:offset + 192]
                for operation in ("xor", "add"):
                    for period in PERIODS:
                        transform_stats["comparisons"] += 1
                        mask = derive_simple_transform(sample, known, operation, period)
                        if mask is None:
                            continue
                        transform_stats["heldout_supported"] += 1
                        # No masks or recovered content are written to reports.
                        for end in ends:
                            key = (offset, end, operation, mask, kind)
                            if end <= offset or key in applied:
                                continue
                            applied.add(key)
                            transform_stats["applied"] += 1
                            payload = apply_simple_transform(body[offset:end], operation, mask)
                            description = dict(operation=operation, period=period, reference=kind,
                                               evidence="64-byte training plus >=32-byte held-out equality")
                            if kind == "stored":
                                if len(payload) == expected and digest(payload) == old_hash:
                                    target = "accepted" if end == len(body) else "checksum_valid_but_unresolved_suffix"
                                    result[target].append(dict(codec="stored", offset=offset, end=end,
                                        skipped_prefix=offset, outer_suffix_bytes=len(body) - end,
                                        transform=description, decoded_length=expected, sha256=old_hash,
                                        verified_checksum="exact_old_sha256", old_exact_match=True))
                                else:
                                    failures["transformed_stored_not_exact_old"] += 1
                            else:
                                codec = "zlib" if kind.startswith("zlib") else "raw_deflate"
                                attempt(payload, codec, offset, end, description)
    result["rejection_counts"] = dict(sorted(failures.items()))
    return result


def stamp(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def load_old(name, budget):
    path = old_path(name)
    if not path.is_file():
        return None, "missing"
    before = stamp(path)
    if not 0 < before[0] <= MAX_RAW:
        return None, "old_length_bound"
    budget.charge("old", before[0])
    with path.open("rb") as stream:
        raw = read_exact(stream, before[0])
        require(not stream.read(1), "old_grew")
    require(stamp(path) == before, "old_changed_during_read")
    return raw, "read_exact"


def run(max_extra=32):
    require(type(max_extra) is int and 0 <= max_extra <= 32, "extra_entry_cap")
    require(INDEX.stat().st_size <= 1024**2, "index_budget")
    with INDEX.open("rb") as stream:
        source = read_exact(stream, INDEX.stat().st_size)
    previous = json.loads(source)
    selected_input = previous["selected_directory"]
    manifest = previous["manifest"]
    require(len(selected_input) == 12 and len(manifest) == 21, "unexpected_v37_scope")
    require(sum(m["entries"] * 316 for m in manifest) <= MAX_TOTAL_DIRECTORY, "total_directory_budget")
    metadata, rows, stats, archives = {}, [], {}, set()
    for meta in manifest:
        name = meta["archive"]
        require(re.fullmatch(r"Resource[0-9]{2}\.pak", name) is not None and name not in archives,
                "archive_name_or_duplicate")
        archives.add(name)
        path = confined_file(CLIENT, name)
        before = stamp(path)
        with path.open("rb") as stream:
            indexed = read_directory(stream, before[0], meta)
        require(stamp(path) == before, "archive_changed_during_index")
        rows.extend(indexed)
        stats[name] = before
        metadata[name] = meta
    lookup = {(r["archive"], r["directory_offset"]): r for r in rows}
    selected = []
    for requested in selected_input:
        row = lookup.get((requested["archive"], requested["directory_offset"]))
        require(row is not None and all(row.get(k) == v for k, v in requested.items()), "selected_entry_changed")
        selected.append(row)
    require(len({(r["archive"], r["directory_offset"]) for r in selected}) == 12, "duplicate_selected")
    budget, results = Budget(), []

    def inspect(row, role):
        # Check row bounds again immediately before opening just this body.
        require(0 < row["compressed"] <= MAX_BODY, "body_read_bound")
        require(row["offset"] + row["compressed"] <= metadata[row["archive"]]["directory_offset"], "body_extent")
        path = confined_file(CLIENT, row["archive"])
        require(stamp(path) == stats[row["archive"]], "archive_changed_before_body")
        budget.charge("body", row["compressed"])
        with path.open("rb") as stream:
            stream.seek(row["offset"])
            body = read_exact(stream, row["compressed"])
        require(stamp(path) == stats[row["archive"]], "archive_changed_after_body")
        old, old_status = load_old(row["name"], budget)
        result = probe_body(body, row, old, budget)
        result.update(role=role, old_read_status=old_status)
        results.append(result)

    for row in selected:
        inspect(row, "selected")
    # Extra bodies are needed only when the original 12 remain unresolved.
    extras = select_extras(rows, selected, max_extra) if any(not r["accepted"] for r in results) else []
    for row in extras:
        inspect(row, "extra_non_sensitive_asset")
    for name in archives:
        require(stamp(confined_file(CLIENT, name)) == stats[name], "archive_changed_before_report")
    failures = Counter()
    groups = defaultdict(list)
    for result in results:
        failures.update(result["rejection_counts"])
        groups[result["prefix_hex"][:32]].append(dict(archive=result["archive"], name=result["name"],
                                                     role=result["role"]))
    summary = dict(archives_verified=len(manifest), directory_entries=len(rows),
        selected_bodies=12, extra_bodies=len(extras), body_bytes_read=budget.body,
        old_bytes_read=budget.old, decoded_bytes_returned=budget.decoded,
        directory_bytes_read=sum(m["entries"] * 316 + 1024 for m in manifest),
        codec_attempts=sum(r["attempts"] for r in results),
        accepted_selected=sum(bool(r["accepted"]) for r in results[:12]),
        accepted_extra=sum(bool(r["accepted"]) for r in results[12:]),
        checksum_valid_unresolved_suffix=sum(len(r["checksum_valid_but_unresolved_suffix"]) for r in results),
        transform_comparisons=sum(r.get("simple_transforms", {}).get("comparisons", 0) for r in results),
        transform_heldout_supported=sum(r.get("simple_transforms", {}).get("heldout_supported", 0) for r in results),
        old_present=sum(r["old"] is not None for r in results),
        old_length_matches=sum(bool(r["old"] and r["old"]["same_declared_length"]) for r in results),
        level1_size_formula_matches=sum(bool(r["old"] and r["old"].get("size_hypothesis", {}).get("indexed_size_matches")) for r in results),
        level1_size_formula_matches_selected=sum(bool(r["old"] and r["old"].get("size_hypothesis", {}).get("indexed_size_matches")) for r in results[:12]),
        rejection_counts=dict(failures),
        coverage=dict(previous="19/30", after="19/30", validated_new_skill_links=0))
    census = dict(special_metadata=sum(r["special_metadata"] for r in rows),
        unequal_size_fields=sum(r["indexed_size"] != r["compressed"] for r in rows),
        body_length_mod16=dict(Counter(str(r["compressed"] % 16) for r in rows)),
        selected_size_deltas=[r["compressed"] - r["indexed_size"] for r in selected])
    report = dict(schema="pak-wrapper-probe-v39", complete=True, source_index_sha256=digest(source),
        probe_script_sha256=digest(Path(__file__).read_bytes()),
        public_format_source=SOURCE,
        source_limit="Historical directory layout/DEFLATE description; current wrapper and +256/+264 divergence unexplained.",
        scope="Read-only PAK directories and <=44 asset bodies plus old equivalents. No PE, dump, DLL, process, network or client writes.",
        interpretation="Repeated prefixes/alignment/entropy do not identify an encryption algorithm. Candidate codec success is not a skill link.",
        bounds=dict(max_extra=32, body_bytes=MAX_BODY, raw_bytes=MAX_RAW,
                    each_total_body_old_decoded_bytes=MAX_TOTAL_BYTES, per_body_attempts=MAX_ATTEMPTS,
                    per_directory_bytes=MAX_DIRECTORY, total_directory_bytes=MAX_TOTAL_DIRECTORY,
                    signature_scan_bytes=1024, signature_cap=MAX_SIGNATURES,
                    xz_memory_bytes=32 * 1024**2, common_offsets=list(COMMON_OFFSETS)),
        acceptance="Exact declared +260 length, stream EOF, no inner trailing bytes, full outer suffix consumed, native checked checksum or exact old SHA-256. No plaintext exported.",
        limitations=["Only listed header offsets and signatures in the first 1024 bytes were tested.",
                     "The +256 field is tested as a secondary endpoint but never treated as authoritative; unexplained outer suffixes prevent acceptance.",
                     "Raw DEFLATE/stored candidates need whole-output old SHA-256 equality. Old versions can differ.",
                     "XOR/add periods 1,2,4,8,16 use known-byte derivation and held-out checks; no cryptographic key enumeration.",
                     "Recompression levels 1,6,9 are hypotheses; different compressor/version/settings remain possible.",
                     "No zstd/LZ4/ZIP or headerless LZMA implementation, dictionary search, padding removal, or exhaustive offset search.",
                     "SHA-256 hashes identify evidence; the directory hash does not independently authenticate PAK body contents."],
        next_step="Prioritize static resource-loader evidence for the observed +256 = 16 + 16 * (old zlib-level-1 length // 16 + 1) size relation, keeping its three exceptions. Request from main a bounded, independently evidenced resource read/decode call contract: actual use of +256/+260/+264, wrapper header length, codec ID and checksum placement. Replay only that contract against these hashed bodies with this verifier; generic crypto imports are insufficient. Do not expand resource sampling or change skill coverage without validated links.",
        summary=summary, directory_census=census, prefix_groups=dict(groups), entries=results)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "probe_review.json").write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", "utf-8")
    write_report(report)
    return summary


def write_report(report):
    s = report["summary"]
    lines = ["# PAK body wrapper probe v39", "",
        f"Verified {s['selected_bodies']} selected bodies and {s['extra_bodies']} additional non-sensitive asset bodies.",
        f"Accepted selected: {s['accepted_selected']}/12; accepted extra: {s['accepted_extra']}/{s['extra_bodies']}.",
        "Skill coverage remains 19/30; no new skill links were established.", "",
        f"Directory checksums matched for {s['archives_verified']} PAKs / {s['directory_entries']:,} entries.",
        f"Body bytes read: {s['body_bytes_read']:,}; old-reference bytes read: {s['old_bytes_read']:,}.",
        f"Codec attempts: {s['codec_attempts']}; known-byte transform comparisons: {s['transform_comparisons']}; held-out supported: {s['transform_heldout_supported']}.",
        f"Old references found: {s['old_present']}; exact declared-length matches: {s['old_length_matches']} (length equality alone is not plaintext identity).", "",
        "## Evidence", "",
        "|Prefix (16 bytes)|Selected|Extra|", "|---|---:|---:|"]
    for prefix, group in report["prefix_groups"].items():
        lines.append(f"|{prefix}|{sum(r['role'] == 'selected' for r in group)}|{sum(r['role'] != 'selected' for r in group)}|")
    lines += ["", "|Selected resource|+256|+260 declared|+264 body|Old length|Accepted|", "|---|---:|---:|---:|---:|---:|"]
    for r in report["entries"][:12]:
        lines.append(f"|{r['archive']}:{r['name']}|{r['indexed_size']}|{r['length']}|{r['compressed']}|{r['old']['length'] if r['old'] else 'missing'}|{len(r['accepted'])}|")
    lines += ["", f"A length-only hypothesis matches +256 for {s['level1_size_formula_matches']}/{s['old_present']} references, including {s['level1_size_formula_matches_selected']}/12 selected:",
              "", "`+256 = 16 + 16 * (len(zlib.compress(old_bytes, level=1)) // 16 + 1)`", "",
              "This is consistent with 16 bytes of fixed overhead plus 1–16 bytes of alignment overhead, but neither the placement/content of that overhead nor a cipher is established. No bytes passed decoding on this basis.", "",
              "|Formula exception|Observed +256|Predicted +256|Old/current lengths equal|", "|---|---:|---:|---|"]
    for r in report["entries"]:
        evidence = (r["old"] or {}).get("size_hypothesis")
        if evidence and not evidence["indexed_size_matches"]:
            lines.append(f"|{r['name']}|{r['indexed_size']}|{evidence['predicted_indexed_size']}|{r['old']['same_declared_length']}|")
    lines += ["", "The two size fields are preserved independently. Repeated prefixes, 16-byte lengths and sampled entropy do not establish AES or any other codec.",
              f"The [author's historical PAK format description]({SOURCE}) supports the directory layout and DEFLATE hypothesis, but does not document this wrapper.",
              "", "## Negative checks and limits", "",
              report["acceptance"], ""]
    lines += [f"- {item}" for item in report["limitations"]]
    lines += ["", "Failure counts: " + json.dumps(s["rejection_counts"], sort_keys=True) + ".", "",
              "## Next step", "", report["next_step"], "",
              "No runtime changes, client modifications, PE/dump inspection, process memory reads, game execution, DLL loading or uploads were performed.",
              "Machine-readable per-entry hashes, signatures, alignment observations and rejection counts: `probe_review.json`.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), "utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra", type=int, default=32, choices=range(33), metavar="0..32")
    args = parser.parse_args()
    print(json.dumps(run(args.extra), ensure_ascii=True, indent=2))
