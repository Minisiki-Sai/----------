"""Exact public AES-table inventory; presence alone does not prove PAK usage.

Read isolated, hash-pinned decoded segments. No key search, host API, client
execution, decryption of user traffic, or changes to the guest checkpoint.
"""
from hashlib import sha256
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/client_protection_v44_20260908"
SOURCE = "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.197-upd1.pdf"


def multiply(a,b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = ((a << 1) ^ (0x11B if a & 0x80 else 0)) & 255
        b >>= 1
    return result


def power(a,n):
    result = 1
    while n:
        if n & 1:
            result = multiply(result,a)
        a = multiply(a,a)
        n >>= 1
    return result


def tables():
    sub = []
    for a in range(256):
        inverse = power(a,254) if a else 0
        value = inverse ^ 0x63
        for shift in range(1,5):
            value ^= ((inverse << shift) | (inverse >> (8-shift))) & 255
        sub.append(value)
    inv = [0]*256
    for index,value in enumerate(sub):
        inv[value] = index
    if bytes(sub[:16]).hex() != "637c777bf26b6fc53001672bfed7ab76":
        raise ValueError("FIPS S-box first row mismatch")
    if bytes(inv[:16]).hex() != "52096ad53036a538bf40a39e81f3d7fb":
        raise ValueError("FIPS inverse S-box first row mismatch")
    result = {"sbox":bytes(sub), "inverse_sbox":bytes(inv)}
    # Common table layouts derived from SubBytes and MixColumns, all rotations.
    for name,box,coefficients in (("enc",sub,(2,1,1,3)),("dec",inv,(14,9,13,11))):
        for rotation in range(4):
            rows = []
            for value in box:
                row = [multiply(value,c) for c in coefficients]
                row = row[rotation:]+row[:rotation]
                rows.extend(row)
            data = bytes(rows)
            result[f"{name}_mix_rotate{rotation}"] = data
            result[f"{name}_mix_rotate{rotation}_swap32"] = b"".join(data[i:i+4][::-1] for i in range(0,len(data),4))
    # Some implementations store each substitution value in one little-endian word.
    result["sbox_u32le"] = struct.pack("<256I",*sub)
    result["inverse_sbox_u32le"] = struct.pack("<256I",*inv)
    return result


def analyze():
    manifest_raw = (OUT / "segments_manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    catalog = tables()
    hits = []
    for section in manifest["segments"]:
        body = (OUT / section["path"]).read_bytes()
        if sha256(body).hexdigest() != section["decoded_sha256"]:
            raise ValueError("segment identity mismatch")
        for name,needle in catalog.items():
            start = 0
            count = 0
            while True:
                at = body.find(needle,start)
                if at < 0:
                    break
                count += 1
                if count > 32:
                    raise ValueError("table instance budget exceeded")
                hits.append(dict(table=name,section=section["section"],
                    rva=section["destination_rva"]+at,bytes=len(needle),
                    sha256=sha256(needle).hexdigest()))
                start = at+1
    report = dict(client_sha256=manifest["client_sha256"],
        manifest_sha256=sha256(manifest_raw).hexdigest(),
        script_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        definition_source=SOURCE,exact_table_matches=hits,table_forms_checked=len(catalog),
        conclusion="Exact public-table matches identify static cipher implementation material only; they do not establish resource usage, a key, cipher mode, or successful PAK decoding.")
    (OUT / "crypto_tables.json").write_text(json.dumps(report,indent=2),"utf-8")
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    analyze()
