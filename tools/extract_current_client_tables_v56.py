"""Extract hash-pinned current-client PAK tables without executing the client.

This is an evidence sidecar.  It reads the installed client, the already decoded
static streams, and Resource*.pak as bytes.  It never loads or executes the game
and never changes production code.  Plaintext is exported only after the PAK
wrapper, exact decoded length, zlib EOF, and strict DNT structure all validate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys
import zlib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.review_client_damage_types_v44 import StaticView, BASE, capstone, x86

OUT = ROOT / "outputs/current_client_tables_v56_20260909"
CLIENT_ROOT = Path(r"D:\DragonNest\Reborn")
CLIENT = CLIENT_ROOT / "dragonnest_reborn.exe"
OLD_STRUCTURE_ROOT = Path(r"E:\ext0811.rar\Resource00\Resource00\resource\ext")
EXPECTED_CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
INDEX_REPORT = ROOT / "outputs/client_resources_v37_20260908/index_review.json"
PAK_MAGIC = b"EyedentityGames Packing File 0.1\0"
MAX_DIRECTORY = 64 * 1024 * 1024
MAX_TABLE = 16 * 1024 * 1024

EXACT_TABLES = {
    "resource/ext/runeresonatetable.dnt",
    "resource/ext/skilltable_character.dnt",
    "resource/ext/weapontable_projectile.dnt",
    "resource/ext/summonskilltable.dnt",
    "resource/ext/monsterskilltable.dnt",
    "resource/ext/filetable.dnt",
}
PREFIX_TABLES = ("resource/ext/skillleveltable_character",)
ACT_HINTS = (
    "resource/char/player/archer/",
    "resource/char/player/soceress/",
    "resource/char/player/cleric/",
    "resource/char/player/academic/",
    "resource/char/player/kali/",
    "resource/char/player/assassin/",
    "resource/char/player/warrior/",
    "resource/char/monster/",
)
EXACT_ACTS = {
    "resource/char/monster/ac_magmawall/academic_magmawall.act",
    "resource/char/monster/ac_magmawall/academic_magmawall_ex.act",
    "resource/char/player/academic/academic_skill_2st_adept.act",
    "resource/weapon/soceress_gravityball_ex.act",
    "resource/weapon/soceress_raisegravity_ex.act",
    "resource/weapon/soceress_summonblackhole_ex_pve.act",
    "resource/weapon/silverhunter/hunter_arrowstorm.act",
    "resource/weapon/silverhunter/hunter_arrowstorm_main.act",
}


class EvidenceError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def read_exact(stream, count: int) -> bytes:
    require(0 <= count <= MAX_DIRECTORY, "read exceeds static audit bound")
    data = stream.read(count)
    require(len(data) == count, "short read")
    return data


def normalize_name(raw: bytes) -> str:
    require(b"\0" in raw, "unterminated PAK name")
    value = raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
    value = value.replace("\\", "/").lstrip("/").lower()
    require(value and ":" not in value and all(p not in ("", ".", "..") for p in value.split("/")),
            "unsafe PAK name")
    return value


def parse_directory_entry(raw: bytes, archive: str, directory_offset: int, ordinal: int) -> dict:
    require(len(raw) == 316, "PAK directory entry size changed")
    name = normalize_name(raw[:256])
    indexed_size, decoded_size, stored_size, body_offset, flags = struct.unpack_from("<5I", raw, 256)
    require(1024 <= body_offset < directory_offset, "body offset outside PAK body")
    require(0 <= indexed_size <= stored_size <= directory_offset - body_offset,
            "PAK body sizes are inconsistent")
    return {
        "archive": archive,
        "ordinal": ordinal,
        "name": name,
        "indexed_size": indexed_size,
        "decoded_size": decoded_size,
        "stored_size": stored_size,
        "body_offset": body_offset,
        "flags": flags,
        "directory_entry_sha256": digest(raw),
    }


def load_expected_manifest() -> dict[str, dict]:
    raw = INDEX_REPORT.read_bytes()
    report = json.loads(raw)
    require(len(report.get("manifest", [])) == 21, "prior PAK manifest count changed")
    return {row["archive"]: row for row in report["manifest"]}


def read_pak_directories() -> tuple[list[dict], list[dict]]:
    expected = load_expected_manifest()
    entries: list[dict] = []
    manifests: list[dict] = []
    for path in sorted(CLIENT_ROOT.glob("Resource*.pak"), key=lambda p: p.name.casefold()):
        require(path.name in expected, f"new or untracked PAK: {path.name}")
        meta = expected[path.name]
        size = path.stat().st_size
        require(size == meta["archive_size"], f"PAK length changed: {path.name}")
        with path.open("rb") as stream:
            header = read_exact(stream, 1024)
            require(header.startswith(PAK_MAGIC), f"PAK magic changed: {path.name}")
            version, count, start = struct.unpack_from("<3I", header, 256)
            require(version == 11 and count == meta["entries"] and start == meta["directory_offset"],
                    f"PAK header/index changed: {path.name}")
            require(0 < count * 316 <= MAX_DIRECTORY and start + count * 316 == size,
                    f"PAK directory extent invalid: {path.name}")
            stream.seek(start)
            raw_directory = read_exact(stream, count * 316)
        require(digest(raw_directory) == meta["directory_sha256"],
                f"PAK directory hash changed: {path.name}")
        manifests.append({
            "archive": path.name,
            "bytes": size,
            "last_write_time_ns": path.stat().st_mtime_ns,
            "header_sha256": digest(header),
            "version": version,
            "entry_count": count,
            "directory_offset": start,
            "directory_bytes": len(raw_directory),
            "directory_sha256": digest(raw_directory),
            "prior_directory_sha256": meta["directory_sha256"],
        })
        for ordinal, at in enumerate(range(0, len(raw_directory), 316)):
            entries.append(parse_directory_entry(raw_directory[at:at + 316], path.name, start, ordinal))
    require(len(manifests) == 21 and len(entries) == 239278, "current PAK census changed")
    return entries, manifests


def is_target(row: dict) -> bool:
    name = row["name"]
    return name in EXACT_TABLES or any(name.startswith(p) and name.endswith(".dnt") for p in PREFIX_TABLES)


def inventory(entries: list[dict]) -> tuple[list[dict], list[dict], dict]:
    selected = [dict(row) for row in entries if is_target(row)]
    acts = [row for row in entries if row["name"].endswith(".act")]
    hinted = [row for row in acts if row["name"].startswith(ACT_HINTS)]
    selected_acts = [dict(row) for row in acts if row["name"] in EXACT_ACTS]
    duplicates = defaultdict(list)
    for row in selected:
        duplicates[row["name"]].append(row["archive"])
    return selected, selected_acts, {
        "all_act_entries": len(acts),
        "player_or_monster_act_entries": len(hinted),
        "act_archives": dict(sorted(Counter(row["archive"] for row in hinted).items())),
        "representative_current_entries": selected_acts,
        "missing_representative_paths": sorted(EXACT_ACTS - {row["name"] for row in selected_acts}),
        "duplicate_target_paths": {k: v for k, v in sorted(duplicates.items()) if len(v) > 1},
    }


def hash_target_archives(rows: list[dict]) -> list[dict]:
    results = []
    for archive in sorted({row["archive"] for row in rows}, key=str.casefold):
        path = CLIENT_ROOT / archive
        results.append({
            "archive": archive,
            "bytes": path.stat().st_size,
            "sha256": digest_file(path),
        })
    return results


def read_target_bodies(selected: list[dict]) -> None:
    grouped = defaultdict(list)
    for row in selected:
        grouped[row["archive"]].append(row)
    for archive, rows in grouped.items():
        path = CLIENT_ROOT / archive
        with path.open("rb") as stream:
            for row in rows:
                require(0 < row["indexed_size"] <= MAX_TABLE, "target body exceeds audit bound")
                stream.seek(row["body_offset"])
                body = read_exact(stream, row["indexed_size"])
                row["body_sha256"] = digest(body)
                row["body_prefix_hex"] = body[:32].hex()
                row["wrapper_ciphertext_sha256"] = digest(body[:16]) if len(body) >= 16 else None
                row["encrypted_payload_bytes"] = max(0, len(body) - 16)
                row["encrypted_payload_block_aligned"] = len(body) >= 16 and (len(body) - 16) % 16 == 0


# Minimal independent AES-256 ECB decryption.  The self-test below is mandatory.
SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
INV_SBOX = bytes.fromhex(
    "52096ad53036a538bf40a39e81f3d7fb7ce339829b2fff87348e4344c4dee9cb"
    "547b9432a6c2233dee4c950b42fac34e082ea16628d924b2765ba2496d8bd125"
    "72f8f66486689816d4a45ccc5d65b6926c704850fdedb9da5e154657a78d9d84"
    "90d8ab008cbcd30af7e45805b8b34506d02c1e8fca3f0f02c1afbd0301138a6b"
    "3a9111414f67dcea97f2cfcef0b4e67396ac7422e7ad3585e2f937e81c75df6e"
    "47f11a711d29c5896fb7620eaa18be1bfc563e4bc6d279209adbc0fe78cd5af4"
    "1fdda8338807c731b11210592780ec5f60517fa919b54a0d2de57a9f93c99cef"
    "a0e03b4dae2af5b0c8ebbb3c83539961172b047eba77d626e169146355210c7d"
)


def gf_mul(a: int, b: int) -> int:
    value = 0
    for _ in range(8):
        if b & 1:
            value ^= a
        a = ((a << 1) ^ (0x11B if a & 0x80 else 0)) & 0xFF
        b >>= 1
    return value


def expand_aes256(key: bytes) -> list[bytes]:
    require(len(key) == 32, "AES-256 material must be 32 bytes")
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    rcon = 1
    while len(words) < 60:
        temp = words[-1][:]
        index = len(words)
        if index % 8 == 0:
            temp = [SBOX[temp[1]], SBOX[temp[2]], SBOX[temp[3]], SBOX[temp[0]]]
            temp[0] ^= rcon
            rcon = gf_mul(rcon, 2)
        elif index % 8 == 4:
            temp = [SBOX[x] for x in temp]
        words.append([a ^ b for a, b in zip(words[index - 8], temp)])
    return [bytes(sum(words[i:i + 4], [])) for i in range(0, 60, 4)]


def aes256_decrypt_block(block: bytes, round_keys: list[bytes]) -> bytes:
    require(len(block) == 16 and len(round_keys) == 15, "invalid AES block/schedule")
    state = [a ^ b for a, b in zip(block, round_keys[14])]
    for round_index in range(13, 0, -1):
        old = state[:]
        for row in range(4):
            for col in range(4):
                state[4 * col + row] = old[4 * ((col - row) % 4) + row]
        state = [INV_SBOX[x] for x in state]
        state = [a ^ b for a, b in zip(state, round_keys[round_index])]
        for col in range(4):
            a0, a1, a2, a3 = state[4 * col:4 * col + 4]
            state[4 * col:4 * col + 4] = [
                gf_mul(a0, 14) ^ gf_mul(a1, 11) ^ gf_mul(a2, 13) ^ gf_mul(a3, 9),
                gf_mul(a0, 9) ^ gf_mul(a1, 14) ^ gf_mul(a2, 11) ^ gf_mul(a3, 13),
                gf_mul(a0, 13) ^ gf_mul(a1, 9) ^ gf_mul(a2, 14) ^ gf_mul(a3, 11),
                gf_mul(a0, 11) ^ gf_mul(a1, 13) ^ gf_mul(a2, 9) ^ gf_mul(a3, 14),
            ]
    old = state[:]
    for row in range(4):
        for col in range(4):
            state[4 * col + row] = old[4 * ((col - row) % 4) + row]
    state = [INV_SBOX[x] for x in state]
    return bytes(a ^ b for a, b in zip(state, round_keys[0]))


def aes256_ecb_decrypt(data: bytes, material: bytes) -> bytes:
    require(data and len(data) % 16 == 0, "AES input not block aligned")
    keys = expand_aes256(material)
    return b"".join(aes256_decrypt_block(data[i:i + 16], keys) for i in range(0, len(data), 16))


def aes_self_test() -> dict:
    key = bytes(range(32))
    cipher = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    expected = bytes.fromhex("00112233445566778899aabbccddeeff")
    actual = aes256_ecb_decrypt(cipher, key)
    require(actual == expected, "AES-256 FIPS vector failed")
    return {"vector": "FIPS-197 AES-256", "passed": True, "plaintext": actual.hex()}


def static_material_candidates(view: StaticView) -> tuple[list[dict], dict]:
    """Collect only current-client static string material; no dictionary guessing."""
    rows: list[dict] = []
    seen: set[bytes] = set()
    sources = [(name, base, data) for name, (base, data) in view.segments.items()
               if name in (".rdata", ".data", ".data0")]
    # Original file-backed data2 can hold startup metadata that is not in decoded streams.
    data2 = next(s for s in view.sections if s["name"] == ".data2")
    sources.append((".data2/original", data2["rva"], view.file_read(data2["rva"], data2["raw_size"])))
    for source, base, data in sources:
        for match in re.finditer(rb"[\x20-\x7e]{32,96}\x00", data):
            value = match.group()[:-1]
            variants = [("exact", value)] if len(value) == 32 else [("prefix32", value[:32])]
            if len(value) > 32:
                variants.append(("suffix32", value[-32:]))
            for kind, material in variants:
                if material in seen:
                    continue
                seen.add(material)
                rows.append({
                    "source": source,
                    "rva": base + match.start(),
                    "string_bytes": len(value),
                    "variant": kind,
                    "material_hex": material.hex(),
                    "material_sha256": digest(material),
                    "preview": material.decode("ascii", "replace"),
                })
    return rows, {"source_count": len(sources), "candidate_count": len(rows)}


def header_shape(plain: bytes) -> tuple[bool, dict]:
    if len(plain) != 16:
        return False, {}
    marker = plain[:7]
    tail = plain[7:]
    marker_ok = all(0x20 <= x <= 0x7E for x in marker)
    stripped = tail.rstrip(b"\0")
    selector_ok = bool(stripped) and len(stripped) <= 9 and all(chr(x) in "0123456789abcdefABCDEF" for x in stripped)
    return marker_ok and selector_ok, {
        "marker_hex": marker.hex(),
        "marker_ascii": marker.decode("ascii", "replace"),
        "selector_text": stripped.decode("ascii", "replace"),
    }


def test_static_materials(selected: list[dict], candidates: list[dict]) -> dict:
    # Use three unrelated current target bodies.  A candidate must produce the same
    # seven-byte marker and a valid hexadecimal selector in all of them.
    sample = []
    for row in selected:
        if row["indexed_size"] >= 32 and row["name"] in EXACT_TABLES:
            with (CLIENT_ROOT / row["archive"]).open("rb") as stream:
                stream.seek(row["body_offset"])
                sample.append((row, read_exact(stream, 16)))
        if len(sample) == 3:
            break
    require(len(sample) == 3, "not enough independent PAK wrapper samples")
    matches = []
    for candidate in candidates:
        material = bytes.fromhex(candidate["material_hex"])
        decoded = []
        for row, cipher in sample:
            plain = aes256_ecb_decrypt(cipher, material)
            ok, shape = header_shape(plain)
            decoded.append({"name": row["name"], "plain_hex": plain.hex(), "shape": shape, "valid": ok})
        if all(x["valid"] for x in decoded) and len({x["shape"]["marker_hex"] for x in decoded}) == 1:
            matches.append({"candidate": candidate, "samples": decoded})
    return {
        "sample_count": len(sample),
        "sample_paths": [row["name"] for row, _ in sample],
        "candidate_count": len(candidates),
        "matching_header_materials": matches,
        "closed": len(matches) == 1,
    }


def strict_zlib(data: bytes, expected_size: int) -> bytes:
    obj = zlib.decompressobj(wbits=15)
    out = obj.decompress(data, expected_size + 1)
    out += obj.flush()
    require(obj.eof and not obj.unused_data and not obj.unconsumed_tail, "zlib stream not exact EOF")
    require(len(out) == expected_size, "decoded length differs from PAK directory")
    return out


def validate_dnt(raw: bytes) -> dict:
    require(10 <= len(raw) <= MAX_TABLE, "DNT byte length invalid")
    magic = raw[:4]
    require(magic == b"\0\0\0\0", "DNT reserved header changed")
    offset = 4
    column_count = struct.unpack_from("<H", raw, offset)[0]
    offset += 2
    row_count = struct.unpack_from("<I", raw, offset)[0]
    offset += 4
    require(0 < column_count <= 2048 and 0 <= row_count <= 2_000_000, "DNT dimensions invalid")
    fields = []
    for _ in range(column_count):
        require(offset + 3 <= len(raw), "truncated DNT field")
        size = struct.unpack_from("<H", raw, offset)[0]
        offset += 2
        require(0 < size <= 512 and offset + size + 1 <= len(raw), "DNT field name length invalid")
        name_raw = raw[offset:offset + size]
        offset += size
        name = name_raw.decode("ascii")
        kind = raw[offset]
        offset += 1
        require(kind in (1, 2, 3, 4, 5), "unsupported DNT field kind")
        fields.append((name, kind))
    schema_end = offset
    row_ids = []
    for _ in range(row_count):
        require(offset + 4 <= len(raw), "truncated DNT row id")
        row_ids.append(struct.unpack_from("<I", raw, offset)[0])
        offset += 4
        for _name, kind in fields:
            if kind == 1:
                require(offset + 2 <= len(raw), "truncated DNT string length")
                size = struct.unpack_from("<H", raw, offset)[0]
                offset += 2
                require(offset + size <= len(raw), "truncated DNT string")
                offset += size
            else:
                require(offset + 4 <= len(raw), "truncated DNT scalar")
                offset += 4
    require(offset == len(raw), "DNT has trailing or unparsed bytes")
    return {
        "magic_hex": magic.hex(),
        "bytes": len(raw),
        "column_count": column_count,
        "row_count": row_count,
        "field_header_sha256": digest(raw[4:schema_end]),
        "fields": [{"name": n, "kind": k} for n, k in fields],
        "row_id_count": len(row_ids),
        "unique_row_ids": len(set(row_ids)),
        "first_row_ids": row_ids[:8],
        "last_row_ids": row_ids[-8:],
        "strict_length_consumed": offset,
    }


def old_structure_reference(selected: list[dict]) -> list[dict]:
    """Validate old plaintext only as a schema reference, never as current content."""
    current_lengths = {row["name"].rsplit("/", 1)[-1]: row["decoded_size"] for row in selected}
    rows = []
    for name, current_length in sorted(current_lengths.items()):
        path = OLD_STRUCTURE_ROOT / name
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            structure = validate_dnt(raw)
            status = "strictly_valid_old_structure_reference"
        except (EvidenceError, UnicodeDecodeError, struct.error) as exc:
            structure = None
            status = f"old_reference_invalid:{type(exc).__name__}"
        rows.append({
            "name": name,
            "source_snapshot": "0811 old plaintext; structure reference only",
            "bytes": len(raw),
            "sha256": digest(raw),
            "current_declared_plaintext_bytes": current_length,
            "same_length_as_current": len(raw) == current_length,
            "identity_proven": False,
            "status": status,
            "structure": structure,
        })
    return rows


def try_extract(selected: list[dict], header_audit: dict, candidates: list[dict]) -> list[dict]:
    results = []
    if not header_audit["closed"]:
        return results
    header = header_audit["matching_header_materials"][0]
    header_material = bytes.fromhex(header["candidate"]["material_hex"])
    marker = bytes.fromhex(header["samples"][0]["shape"]["marker_hex"])
    by_material = [(bytes.fromhex(row["material_hex"]), row) for row in candidates]
    for row in selected:
        with (CLIENT_ROOT / row["archive"]).open("rb") as stream:
            stream.seek(row["body_offset"])
            body = read_exact(stream, row["indexed_size"])
        head = aes256_ecb_decrypt(body[:16], header_material)
        ok, shape = header_shape(head)
        if not ok or head[:7] != marker:
            results.append({"name": row["name"], "status": "wrapper_header_failed"})
            continue
        selector = int(shape["selector_text"], 16)
        accepted = []
        encrypted = body[16:]
        for material, evidence in by_material:
            if not encrypted or len(encrypted) % 16:
                continue
            plain = aes256_ecb_decrypt(encrypted, material)
            adjusted = len(plain) - (plain[-1] if len(plain) > 16 and plain[-1] < 128 else plain[-1] - 256)
            if not 0 < adjusted <= len(plain):
                continue
            try:
                decoded = strict_zlib(plain[:adjusted], row["decoded_size"])
                dnt = validate_dnt(decoded)
            except (EvidenceError, zlib.error, UnicodeDecodeError, struct.error):
                continue
            accepted.append((material, evidence, decoded, dnt))
        if len(accepted) != 1:
            results.append({
                "name": row["name"],
                "archive": row["archive"],
                "selector": selector,
                "status": "body_material_not_unique",
                "accepted_candidates": len(accepted),
            })
            continue
        material, evidence, decoded, dnt = accepted[0]
        output_name = row["name"].rsplit("/", 1)[-1]
        target = OUT / "extracted" / output_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(decoded)
        results.append({
            "name": row["name"],
            "archive": row["archive"],
            "selector": selector,
            "status": "extracted_and_strictly_validated",
            "material_sha256": digest(material),
            "material_static_source": evidence,
            "plaintext_sha256": digest(decoded),
            "output": str(target.relative_to(ROOT)).replace("\\", "/"),
            "dnt": dnt,
        })
    return results


def scan_static_xrefs(view: StaticView) -> dict:
    targets = {
        0x12B4C20: "selector_map_pointer_global",
        0x1159F40: "wrapper_marker_string_global",
        0xE60480: "selector_map_destructor_begin",
        0xE60500: "marker_string_destructor_begin",
    }
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    rows = []
    for begin, end, _unwind in view.functions:
        section = view.section(begin)
        if section is None or section["name"] not in (".text", ".data0", ".data2"):
            continue
        try:
            raw = view.read(begin, end - begin)
        except ValueError:
            continue
        for ins in decoder.disasm(raw, BASE + begin):
            rva = ins.address - BASE
            for operand in ins.operands:
                target = None
                kind = None
                if operand.type == x86.X86_OP_MEM and operand.mem.base == x86.X86_REG_RIP:
                    target = rva + ins.size + operand.mem.disp
                    kind = "rip_memory"
                elif operand.type == x86.X86_OP_IMM and (
                        ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP)):
                    target = operand.imm - BASE
                    kind = "direct_control"
                if target in targets:
                    rows.append({
                        "function_begin": begin,
                        "instruction_rva": rva,
                        "instruction_bytes": ins.bytes.hex(),
                        "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
                        "reference_kind": kind,
                        "target_rva": target,
                        "target_name": targets[target],
                    })
    return {
        "targets": [{"rva": k, "name": v} for k, v in targets.items()],
        "references": rows,
        "counts": dict(sorted(Counter(row["target_name"] for row in rows).items())),
        "boundary": "Original exception ranges and current decoded streams only; no gap resynchronization.",
    }


def audit_selector_constructor(view: StaticView) -> dict:
    registration_begin = 0x26CC0
    registration_bytes = 0x19
    constructor_begin = 0x125910
    constructor_bytes = 0x70C
    registration = view.read(registration_begin, registration_bytes)
    constructor = view.read(constructor_begin, constructor_bytes)
    require(digest(registration) == "908a712f3b8c236eb61087e1ef0ed9566f03fd19c27f0feae4caf8a37b1bc6ac",
            "selector constructor registration bytes changed")
    require(digest(constructor) == "1b61fec69869bcd1824dda5bc3d76beb6b54ad56894ba41dbcc3dcdf06ab47c9",
            "selector constructor body bytes changed")
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    registration_instructions = list(decoder.disasm(registration, BASE + registration_begin))
    linear_prefix = list(decoder.disasm(constructor, BASE + constructor_begin))
    decoded_prefix_bytes = 0
    if linear_prefix:
        decoded_prefix_bytes = linear_prefix[-1].address + linear_prefix[-1].size - (BASE + constructor_begin)
    return {
        "registration_rva": registration_begin,
        "registration_bytes": len(registration),
        "registration_sha256": digest(registration),
        "registration_instructions": [
            {"rva": ins.address - BASE, "bytes": ins.bytes.hex(),
             "instruction": f"{ins.mnemonic} {ins.op_str}".strip()}
            for ins in registration_instructions
        ],
        "proven_chain": "0x26CC0 -> call 0x125910 -> register destructor 0xE60480 via 0xCED80C",
        "constructor_rva": constructor_begin,
        "constructor_bytes": len(constructor),
        "constructor_sha256": digest(constructor),
        "linear_disassembly_prefix_bytes": decoded_prefix_bytes,
        "linear_disassembly_unresolved_bytes": len(constructor) - decoded_prefix_bytes,
        "linear_prefix_instructions": [
            {"rva": ins.address - BASE, "bytes": ins.bytes.hex(),
             "instruction": f"{ins.mnemonic} {ins.op_str}".strip()}
            for ins in linear_prefix[:16]
        ],
        "boundary": (
            "The constructor is protected/flattened after its short linear prefix. Static recovery must reconstruct "
            "its transformed control/data records; no client execution or emulation was used."
        ),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    require(digest_file(CLIENT) == EXPECTED_CLIENT_SHA256, "installed client hash changed")
    view = StaticView()
    entries, manifests = read_pak_directories()
    selected, selected_acts, act_inventory = inventory(entries)
    read_target_bodies(selected)
    read_target_bodies(selected_acts)
    archive_hashes = hash_target_archives(selected + selected_acts)
    candidates, candidate_summary = static_material_candidates(view)
    aes_test = aes_self_test()
    header_audit = test_static_materials(selected, candidates)
    extraction = try_extract(selected, header_audit, candidates)
    xrefs = scan_static_xrefs(view)
    constructor_audit = audit_selector_constructor(view)
    old_reference = old_structure_reference(selected)

    by_name = defaultdict(list)
    for row in selected:
        by_name[row["name"]].append(row)
    missing = sorted(EXACT_TABLES - set(by_name))
    report = {
        "schema": "current-client-tables-static-v56",
        "safety": {
            "static_offline_read_only": True,
            "client_executed": False,
            "client_loaded": False,
            "client_injected": False,
            "client_modified": False,
            "production_modified": False,
        },
        "client": {
            "path": str(CLIENT),
            "bytes": CLIENT.stat().st_size,
            "sha256": EXPECTED_CLIENT_SHA256,
            "decoded_segments_manifest_sha256": digest((ROOT / "outputs/client_protection_v44_20260908/segments_manifest.json").read_bytes()),
        },
        "pak_census": {
            "archive_count": len(manifests),
            "entry_count": len(entries),
            "manifests": manifests,
            "target_archive_whole_file_hashes": archive_hashes,
        },
        "target_inventory": {
            "target_entries": selected,
            "missing_exact_paths": missing,
            "act": act_inventory,
        },
        "pak_reader_contract": {
            "directory_entry_bytes": 316,
            "body_read_field": "+256 indexed_size",
            "decoded_capacity_field": "+260 decoded_size",
            "body_offset_field": "+268 body_offset",
            "wrapper_bytes": 16,
            "cipher": "AES-256 ECB",
            "codec": "zlib-wrapped DEFLATE, wbits=15",
            "strict_acceptance": "same current client/PAK, unique static material, wrapper marker+hex selector, exact zlib EOF, exact decoded length, strict DNT full consumption",
        },
        "aes_self_test": aes_test,
        "static_material_candidates": candidate_summary,
        "header_material_audit": header_audit,
        "static_xrefs": xrefs,
        "selector_constructor_audit": constructor_audit,
        "old_0811_structure_reference": {
            "used_as_current_content": False,
            "identity_proven_for_any_table": False,
            "tables": old_reference,
        },
        "extraction": extraction,
        "status": {},
    }
    extracted = [row for row in extraction if row["status"] == "extracted_and_strictly_validated"]
    report["status"] = {
        "current_tables_extracted": len(extracted),
        "same_version_plaintext_proven": bool(extracted),
        "current_directory_and_encrypted_body_identity_proven": True,
        "skilltable_closed": any(row["name"] == "resource/ext/skilltable_character.dnt" for row in extracted),
        "runeresonate_closed": any(row["name"] == "resource/ext/runeresonatetable.dnt" for row in extracted),
        "skilllevel_closed": any(row["name"].startswith(PREFIX_TABLES) for row in extracted),
        "projectile_closed": any(row["name"] == "resource/ext/weapontable_projectile.dnt" for row in extracted),
        "blocking_step": None if extracted else (
            "statically deobfuscate current constructor RVA 0x125910 (or its protected startup data record) to recover "
            "the selector-map nodes, seven-byte wrapper marker, and two selected 32-byte current-version materials"
        ),
        "shortest_verifiable_path": (
            "statically reconstruct protected constructor RVA 0x125910, proven by 0x26CC0 registration to initialize the "
            "RVA 0x12B4C20 selector map (or recover the startup data record that supplies its nodes), "
            "then replay the proven AES-256-ECB + signed-tail + zlib chain; accept only with exact directory length, zlib EOF, "
            "strict DNT full-byte consumption, and current PAK body hash"
        ),
        "shortest_verifiable_path_steps": [
            "Recover transformed control/data records used by current constructor RVA 0x125910.",
            "Materialize the seven-byte wrapper marker and selector-map nodes written to RVA 0x12B4C20, including the header and selector-matched 32-byte materials.",
            "Replay the already-proven AES-256-ECB -> signed-tail -> zlib chain and accept only exact +260 length, zlib EOF, zero DNT reserved header, and full row/column consumption.",
        ],
    }
    json_path = OUT / "current_client_tables_v56.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 当前安装客户端 PAK/DNT 静态提取 v56",
        "",
        "全程只读取当前客户端、已解码静态段和 Resource*.pak 字节；没有执行、加载、注入或修改游戏，也没有修改生产代码。",
        "",
        "## 结论",
        "",
        f"- 当前客户端 SHA-256：`{EXPECTED_CLIENT_SHA256}`。",
        f"- 重新核验 {len(manifests)} 个 PAK、{len(entries):,} 个目录项；目录版本、长度、表头、范围和既有目录 SHA-256 全部一致。",
        f"- 定位目标 DNT {len(selected)} 个目录项；当前静态候选材料 {len(candidates)} 个；通过三份独立 wrapper 首块一致性检查的材料 {len(header_audit['matching_header_materials'])} 个。",
        f"- 严格提取并验证的当前同版 DNT：{len(extracted)} 个。",
        f"- 目标涉及 {len(archive_hashes)} 个 PAK，已额外计算整包 SHA-256；代表 ACT 目录项 {len(selected_acts)} 个。",
        "",
    ]
    if extracted:
        lines += ["## 已闭合的同版表", "", "|表|PAK|行数|列数|明文 SHA-256|", "|---|---|---:|---:|---|"]
        for row in extracted:
            lines.append(f"|{row['name']}|{row['archive']}|{row['dnt']['row_count']}|{row['dnt']['column_count']}|`{row['plaintext_sha256']}`|")
    else:
        lines += [
            "## 尚未闭合的原因",
            "",
            "AES-256 ECB 与 zlib-wrapped DEFLATE 的代码路径已经由当前客户端静态指令闭合；目录项 `+256/+260/+268` 的读取含义也已验证。",
            "当前唯一缺口仍是运行时构造的 `selector → 32字节材料` 有序表（全局 RVA `0x12B4C20`）以及由它初始化的首块/正文材料。",
            "本轮进一步证明初始化入口为 `0x26CC0 → call 0x125910 → 注册析构函数 0xE60480`；两段当前客户端字节分别已固定 SHA-256。`0x125910` 主体受保护控制流影响，普通线性反汇编不能还原其节点写入。",
            "本轮只使用当前客户端中的静态字符串作为候选，未发现能同时解开三份独立 wrapper 首块的唯一材料，因此没有输出伪明文，也没有把旧 0811 DNT 当作当前表。",
            "",
            "最短可验证路径：静态还原 `0x125910` 的变换控制流/数据记录，或直接提取其保护启动记录中向 `0x12B4C20` 提供的节点；取得7字节 marker 与两条32字节材料后，立即按已证明的 AES→有符号尾长→zlib 链回放。",
            "验收必须同时满足：当前 PAK 正文哈希、统一7字节 marker、十六进制 selector、zlib 完整 EOF、输出长度等于目录 `+260`、DNT 全字节消费及合理行列数。",
            "",
        ]
    lines += [
        "## 当前版本目标目录项",
        "",
        "|表|PAK|+256读取长度|+260声明明文|正文 SHA-256|",
        "|---|---|---:|---:|---|",
    ]
    for row in selected:
        lines.append(f"|{row['name']}|{row['archive']}|{row['indexed_size']}|{row['decoded_size']}|`{row['body_sha256']}`|")
    lines += [
        "",
        "## ACT 边界",
        "",
        f"当前 PAK 目录中共有 {act_inventory['all_act_entries']:,} 个 ACT 条目，其中玩家/怪物目录候选 {act_inventory['player_or_monster_act_entries']:,} 个。",
        f"已对 {len(selected_acts)} 个当前版代表 ACT（岩浆墙、炼金技能集、黑洞/上升重力、箭雨风暴）固定目录字段与加密正文 SHA-256。",
        "在正文材料闭合之前，只能证明这些是当前 PAK 的同版目录项，不能把旧 ACT 内容冒充当前内容；因此本轮没有用旧 ACT 生成新技能归因。",
        "",
        "|ACT|PAK|+256读取长度|+260声明明文|正文 SHA-256|",
        "|---|---|---:|---:|---|",
    ]
    for row in selected_acts:
        lines.append(f"|{row['name']}|{row['archive']}|{row['indexed_size']}|{row['decoded_size']}|`{row['body_sha256']}`|")
    lines += [
        "",
        "## 旧 0811 结构参照边界",
        "",
        "旧明文仅用于验证 DNT 解析器的字段/行结构，不参与当前内容导出，也不证明与当前版本相同。即便长度相同，也没有同版身份结论。",
        "当前同版正文仍是密文，因此当前版的字段表头、行数和列数尚不能核验；这里只能核验目录声明长度与密文正文哈希。",
        "",
        "|表|当前声明明文长度|旧0811长度|长度相同|旧结构（行×列）|旧明文 SHA-256|",
        "|---|---:|---:|---|---:|---|",
        "",
    ]
    for row in old_reference:
        structure = row.get("structure") or {}
        shape = f"{structure.get('row_count', '?')}×{structure.get('column_count', '?')}"
        lines.append(
            f"|{row['name']}|{row['current_declared_plaintext_bytes']}|{row['bytes']}|"
            f"{'是（仍不证明同版）' if row['same_length_as_current'] else '否'}|{shape}|`{row['sha256']}`|"
        )
    lines += [
        "",
        "## 最短静态路径",
        "",
        "1. 还原当前构造函数 `0x125910` 使用的变换控制流/数据记录。",
        "2. 得到写入 `0x12B4C20` 的 selector 节点、7字节 marker、首块与正文两条32字节材料。",
        "3. 回放已闭合的 AES-256-ECB → 有符号尾长 → zlib；仅在 `+260` 长度、zlib EOF、DNT 零保留头和全字节消费同时成立时导出。",
        "",
        "若第1步无法从静态字节还原，当前限制下没有更短且不靠猜测的路径；这就是本轮明确阻断点。",
        "",
        "## 产物",
        "",
        "- `current_client_tables_v56.json`：完整 PAK 清单、目录项、正文哈希、静态候选、xref 和严格验收状态。",
        "- `extracted/`：只在全部严格校验通过时创建；当前没有通过就不会生成表文件。",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
