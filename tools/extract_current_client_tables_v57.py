"""Current-client PAK key/material static recovery audit (v57).

Only disk bytes and previously hash-validated decoded sections are read.  The
client is never started, loaded, injected, emulated, or modified.  Production
code is not imported or changed.  Plaintext is written only after the current
PAK identity, wrapper, AES, zlib and strict DNT/ACT parsers all agree.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys
import time
import zlib

import numpy as np

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tools.extract_current_client_tables_v56 as v56
from tools.review_client_damage_types_v44 import StaticView, BASE, capstone, x86


OUT = ROOT / "outputs/current_client_tables_v57_20260909"
EXPECTED_CLIENT_SHA256 = v56.EXPECTED_CLIENT_SHA256
HEADER_SELECTOR = 0x907CAF84
SELECTOR_MAP_GLOBAL = 0x12B4C20
MARKER_GLOBAL = 0x1159F40
CONSTRUCTOR = 0x125910
MAX_EXTERNAL_STATIC_FILE = 64 * 1024 * 1024
BATCH = 65536


class StaticRecoveryError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise StaticRecoveryError(message)


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def current_static_sources(view: StaticView) -> list[dict]:
    """Return current-client bytes to scan for a contiguous 32-byte material.

    Data-like sections are checked at every byte.  Native/protected code is
    checked at four-byte starts: immediate fragments are not promoted to a
    contiguous std::string material without constructor data-flow proof.
    """
    rows = []
    for name, (rva, data) in view.segments.items():
        stride = 1 if name in (".rdata", ".data", "_RDATA") else 4
        rows.append({"source": f"decoded/{name}", "rva": rva, "data": data, "stride": stride})
    decoded_names = set(view.segments)
    for section in view.sections:
        if section["name"] in decoded_names or not section["raw_size"]:
            continue
        data = view.file_read(section["rva"], section["raw_size"])
        stride = 1 if section["name"] in (".data1", ".data2", ".rsrc", ".reloc") else 4
        rows.append({
            "source": f"original/{section['name']}",
            "rva": section["rva"],
            "data": data,
            "stride": stride,
        })
    return rows


SBOX = np.frombuffer(v56.SBOX, dtype=np.uint8)
INV_SBOX = np.frombuffer(v56.INV_SBOX, dtype=np.uint8)


def _gf_table(multiplier: int) -> np.ndarray:
    return np.array([v56.gf_mul(value, multiplier) for value in range(256)], dtype=np.uint8)


GF9 = _gf_table(9)
GF11 = _gf_table(11)
GF13 = _gf_table(13)
GF14 = _gf_table(14)
INV_SHIFT = np.array([
    4 * ((column - row) % 4) + row
    for column in range(4)
    for row in range(4)
], dtype=np.intp)
RCON = (1, 2, 4, 8, 16, 32, 64)


def expand_aes256_many(keys: np.ndarray) -> np.ndarray:
    require(keys.ndim == 2 and keys.shape[1] == 32 and keys.dtype == np.uint8,
            "vector AES keys must be Nx32 uint8")
    words = np.empty((keys.shape[0], 60, 4), dtype=np.uint8)
    words[:, :8, :] = keys.reshape((-1, 8, 4))
    rcon_index = 0
    for index in range(8, 60):
        temp = words[:, index - 1, :].copy()
        if index % 8 == 0:
            temp = SBOX[temp[:, (1, 2, 3, 0)]]
            temp[:, 0] ^= RCON[rcon_index]
            rcon_index += 1
        elif index % 8 == 4:
            temp = SBOX[temp]
        words[:, index, :] = words[:, index - 8, :] ^ temp
    return words.reshape((-1, 15, 16))


def _inverse_mix_columns(state: np.ndarray) -> np.ndarray:
    mixed = np.empty_like(state)
    for column in range(4):
        base = 4 * column
        a0, a1, a2, a3 = (state[:, base + index] for index in range(4))
        mixed[:, base] = GF14[a0] ^ GF11[a1] ^ GF13[a2] ^ GF9[a3]
        mixed[:, base + 1] = GF9[a0] ^ GF14[a1] ^ GF11[a2] ^ GF13[a3]
        mixed[:, base + 2] = GF13[a0] ^ GF9[a1] ^ GF14[a2] ^ GF11[a3]
        mixed[:, base + 3] = GF11[a0] ^ GF13[a1] ^ GF9[a2] ^ GF14[a3]
    return mixed


def aes256_decrypt_block_many(ciphertext: bytes, keys: np.ndarray) -> np.ndarray:
    require(len(ciphertext) == 16, "vector AES ciphertext must be one block")
    round_keys = expand_aes256_many(keys)
    state = np.broadcast_to(np.frombuffer(ciphertext, dtype=np.uint8), (keys.shape[0], 16)).copy()
    state ^= round_keys[:, 14, :]
    for round_index in range(13, 0, -1):
        state = state[:, INV_SHIFT]
        state = INV_SBOX[state]
        state ^= round_keys[:, round_index, :]
        state = _inverse_mix_columns(state)
    state = state[:, INV_SHIFT]
    state = INV_SBOX[state]
    state ^= round_keys[:, 0, :]
    return state


def vector_aes_self_test() -> dict:
    key = np.arange(32, dtype=np.uint8).reshape((1, 32))
    cipher = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")
    expected = bytes.fromhex("00112233445566778899aabbccddeeff")
    actual = aes256_decrypt_block_many(cipher, key)[0].tobytes()
    require(actual == expected, "vector AES-256 FIPS-197 self-test failed")
    return {"vector": "FIPS-197 AES-256", "passed": True, "plaintext": actual.hex()}


def _valid_wrapper_rows(plain: np.ndarray) -> np.ndarray:
    marker = np.all((plain[:, :7] >= 0x20) & (plain[:, :7] <= 0x7E), axis=1)
    result = np.zeros(plain.shape[0], dtype=bool)
    for index in np.flatnonzero(marker):
        ok, _shape = v56.header_shape(plain[index].tobytes())
        result[index] = ok
    return np.flatnonzero(result)


def scan_contiguous_header_materials(sources: list[dict], ciphertexts: list[tuple[str, bytes]]) -> dict:
    require(len(ciphertexts) >= 3, "three independent current wrappers required")
    first_cipher = ciphertexts[0][1]
    matches = []
    checked = 0
    started = time.monotonic()
    for source in sources:
        data = np.frombuffer(source["data"], dtype=np.uint8)
        if data.size < 32:
            continue
        windows = np.lib.stride_tricks.sliding_window_view(data, 32)[::source["stride"]]
        for begin in range(0, windows.shape[0], BATCH):
            keys = np.ascontiguousarray(windows[begin:begin + BATCH])
            plain = aes256_decrypt_block_many(first_cipher, keys)
            candidates = _valid_wrapper_rows(plain)
            for local in candidates:
                key = keys[local].tobytes()
                decoded = []
                for name, ciphertext in ciphertexts:
                    header = v56.aes256_ecb_decrypt(ciphertext, key)
                    valid, shape = v56.header_shape(header)
                    decoded.append({"name": name, "valid": valid, "plain_hex": header.hex(), "shape": shape})
                if all(item["valid"] for item in decoded) and len({item["shape"]["marker_hex"] for item in decoded}) == 1:
                    at = (begin + int(local)) * source["stride"]
                    matches.append({
                        "source": source["source"],
                        "rva": source["rva"] + at,
                        "material_hex": key.hex(),
                        "material_sha256": digest(key),
                        "samples": decoded,
                    })
            checked += keys.shape[0]
    unique = {}
    for row in matches:
        unique.setdefault(row["material_hex"], row)
    return {
        "candidate_windows_checked": checked,
        "source_count": len(sources),
        "sources": [{k: v for k, v in row.items() if k != "data"} | {"bytes": len(row["data"])} for row in sources],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "matches": list(unique.values()),
        "unique_match_count": len(unique),
        "boundary": (
            "Every byte start in data-like decoded/file-backed sections and every fourth byte in code-like sections was checked. "
            "Split, transformed, heap-built, or unmaterialized-BSS strings are outside this contiguous-material audit."
        ),
    }


def collect_external_string_materials() -> tuple[list[dict], dict]:
    rows = []
    seen = set()
    files = []
    allowed = {".exe", ".dll", ".cfg", ".json"}
    for path in sorted(v56.CLIENT_ROOT.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        size = path.stat().st_size
        if not 0 < size <= MAX_EXTERNAL_STATIC_FILE:
            continue
        raw = path.read_bytes()
        files.append({"path": str(path), "bytes": len(raw), "sha256": digest(raw)})
        for match in re.finditer(rb"[\x20-\x7e]{32,256}\x00", raw):
            material = match.group()[:32]
            if material in seen:
                continue
            seen.add(material)
            rows.append({
                "path": str(path),
                "file_offset": match.start(),
                "material_hex": material.hex(),
                "material_sha256": digest(material),
                "preview": material.decode("ascii", "replace"),
            })
    return rows, {"files": files, "candidate_count": len(rows)}


def test_external_materials(candidates: list[dict], ciphertexts: list[tuple[str, bytes]]) -> dict:
    accepted = []
    for row in candidates:
        key = bytes.fromhex(row["material_hex"])
        decoded = []
        for name, ciphertext in ciphertexts:
            header = v56.aes256_ecb_decrypt(ciphertext, key)
            valid, shape = v56.header_shape(header)
            decoded.append({"name": name, "valid": valid, "plain_hex": header.hex(), "shape": shape})
        if all(item["valid"] for item in decoded) and len({item["shape"]["marker_hex"] for item in decoded}) == 1:
            accepted.append({"candidate": row, "samples": decoded})
    return {"candidate_count": len(candidates), "matches": accepted, "unique_match_count": len(accepted)}


def target_wrapper_samples(selected: list[dict]) -> list[tuple[str, bytes]]:
    rows = []
    for row in selected:
        if row["name"] not in v56.EXACT_TABLES or row["indexed_size"] < 32:
            continue
        with (v56.CLIENT_ROOT / row["archive"]).open("rb") as stream:
            stream.seek(row["body_offset"])
            rows.append((row["name"], stream.read(16)))
        if len(rows) == 3:
            break
    require(len(rows) == 3 and all(len(body) == 16 for _name, body in rows), "wrapper samples unavailable")
    return rows


def classify_global_references(view: StaticView) -> dict:
    targets = {SELECTOR_MAP_GLOBAL: "selector_map", MARKER_GLOBAL: "marker_string"}
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
            for ordinal, operand in enumerate(ins.operands):
                if operand.type != x86.X86_OP_MEM or operand.mem.base != x86.X86_REG_RIP:
                    continue
                target = rva + ins.size + operand.mem.disp
                if target not in targets:
                    continue
                access = []
                if operand.access & capstone.CS_AC_READ:
                    access.append("read")
                if operand.access & capstone.CS_AC_WRITE:
                    access.append("write")
                rows.append({
                    "function_begin": begin,
                    "instruction_rva": rva,
                    "instruction_bytes": ins.bytes.hex(),
                    "instruction": f"{ins.mnemonic} {ins.op_str}".strip(),
                    "operand": ordinal,
                    "access": access or ["capstone_unspecified"],
                    "target": targets[target],
                    "target_rva": target,
                })
    return {
        "references": rows,
        "counts": dict(sorted(Counter(
            f"{row['target']}:{'+'.join(row['access'])}" for row in rows
        ).items())),
        "constructor_direct_reference_found": any(row["function_begin"] == CONSTRUCTOR for row in rows),
        "interpretation": (
            "No direct constructor reference does not mean no write: protected control flow may synthesize the address. "
            "Only ordinary RIP-relative references in validated exception ranges are classified."
        ),
    }


def constructor_static_route(view: StaticView) -> dict:
    base = v56.audit_selector_constructor(view)
    raw = view.read(CONSTRUCTOR, base["constructor_bytes"])
    # Bytewise direct-call encodings are candidates only; junk/data can contain E8.
    candidates = []
    for offset in range(len(raw) - 4):
        if raw[offset] != 0xE8:
            continue
        displacement = struct.unpack_from("<i", raw, offset + 1)[0]
        target = CONSTRUCTOR + offset + 5 + displacement
        section = view.section(target)
        if section is None:
            continue
        candidates.append({
            "instruction_rva": CONSTRUCTOR + offset,
            "target_rva": target,
            "target_section": section["name"],
            "candidate_only": True,
        })
    base["bytewise_direct_call_candidates"] = candidates
    base["exact_static_blocker"] = (
        "After the 86-byte ordinary prefix, protected call/return fragments alter RSP and saved return addresses. "
        "The selector-map address is synthesized rather than present as a normal RIP-relative operand. "
        "Without statically reconstructing that transformed stack/control state, node writes and split constants cannot be proven."
    )
    return base


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    require(v56.digest_file(v56.CLIENT) == EXPECTED_CLIENT_SHA256, "current client identity changed")
    view = StaticView()
    entries, manifests = v56.read_pak_directories()
    selected, selected_acts, act_inventory = v56.inventory(entries)
    v56.read_target_bodies(selected)
    v56.read_target_bodies(selected_acts)
    samples = target_wrapper_samples(selected)

    vector_test = vector_aes_self_test()
    sources = current_static_sources(view)
    contiguous = scan_contiguous_header_materials(sources, samples)
    external_candidates, external_inventory = collect_external_string_materials()
    external_audit = test_external_materials(external_candidates, samples)
    references = classify_global_references(view)
    constructor = constructor_static_route(view)

    header_matches = contiguous["matches"] + external_audit["matches"]
    # A body key cannot be selected or strictly tested until the wrapper yields
    # its selector.  Do not substitute HEADER_SELECTOR or any guessed key.
    extraction = []
    strict_current_plaintext = False
    report = {
        "schema": "current-client-tables-static-v57",
        "safety": {
            "static_offline_read_only": True,
            "client_executed": False,
            "client_loaded": False,
            "client_emulated": False,
            "client_injected": False,
            "client_modified": False,
            "production_modified": False,
        },
        "client": {
            "path": str(v56.CLIENT),
            "bytes": v56.CLIENT.stat().st_size,
            "sha256": EXPECTED_CLIENT_SHA256,
        },
        "pak_census": {
            "archive_count": len(manifests),
            "entry_count": len(entries),
            "directory_hashes": [
                {"archive": row["archive"], "bytes": row["bytes"], "directory_sha256": row["directory_sha256"]}
                for row in manifests
            ],
        },
        "prioritized_current_targets": {
            "dnt": selected,
            "act": selected_acts,
            "act_inventory": act_inventory,
        },
        "aes_vector_self_test": vector_test,
        "wrapper_samples": [{"name": name, "ciphertext_hex": body.hex(), "sha256": digest(body)} for name, body in samples],
        "contiguous_current_client_material_audit": contiguous,
        "external_current_installation_string_audit": external_inventory | {"result": external_audit},
        "global_reference_audit": references,
        "selector_constructor_audit": constructor,
        "extraction": extraction,
        "status": {
            "strict_current_plaintext_extracted": strict_current_plaintext,
            "current_tables_extracted": 0,
            "runeresonate_closed": False,
            "skilltable_closed": False,
            "projectile_closed": False,
            "act_closed": False,
            "header_material_matches": len(header_matches),
            "exact_blocker": (
                "The current selector-map constructor 0x125910 materializes the marker and key strings through protected "
                "stack/control transformations. No valid contiguous 32-byte current static material decrypts three independent "
                "wrapper headers, and no ordinary static write exposes the constructed values."
            ),
            "shortest_static_path": [
                "Recover the protected saved-return/RSP transformation beginning at 0x125910 and its data-record operands.",
                "Resolve the synthesized destination writes to marker RVA 0x1159F40 and selector-map global RVA 0x12B4C20.",
                "Reconstruct the exact std::string node values at node+0x28 for selector 0x907CAF84 and each wrapper selector.",
                "Replay AES-256-ECB, signed final-byte removal and zlib; accept DNT/ACT only after exact +260 length and full parser consumption.",
            ],
        },
    }
    json_path = OUT / "current_client_tables_v57.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 当前安装客户端 PAK/DNT 静态提取 v57",
        "",
        "本轮只读取当前客户端、当前 Resource*.pak 与已按哈希验证的静态解码段；没有执行、加载、模拟、注入或修改客户端，也没有修改生产代码。",
        "",
        "## 结论",
        "",
        f"- 当前客户端 SHA-256：`{EXPECTED_CLIENT_SHA256}`。",
        f"- 当前 21 个 PAK、{len(entries):,} 个目录项再次通过身份和边界核验。",
        f"- 对当前静态段的 {contiguous['candidate_windows_checked']:,} 个连续32字节窗口进行 AES-256 首块检查；有效材料 {contiguous['unique_match_count']} 个。",
        f"- 对当前安装目录 {len(external_inventory['files'])} 个 EXE/DLL/配置文件中的 {external_inventory['candidate_count']:,} 个32字节字符串材料进行独立检查；有效材料 {external_audit['unique_match_count']} 个。",
        "- 严格解出的当前同版 RuneResonate/SkillTable/Projectile/ACT：**0 个**。",
        "",
        "## `0x125910` 静态还原结果",
        "",
        f"- 初始化注册链仍严格成立：`{constructor['proven_chain']}`。",
        f"- 构造函数共 {constructor['constructor_bytes']} 字节，SHA-256 `{constructor['constructor_sha256']}`。",
        f"- 普通线性反汇编只闭合前 {constructor['linear_disassembly_prefix_bytes']} 字节；剩余 {constructor['linear_disassembly_unresolved_bytes']} 字节进入保护式 call/return、RSP 和保存返回地址变换。",
        f"- 普通 RIP 相对引用中，构造函数直接引用 `0x12B4C20/0x1159F40`：{'是' if references['constructor_direct_reference_found'] else '否'}。地址由保护路径合成，不能把析构函数的清零写入误判为初始化内容。",
        "",
        "## 明确阻断点",
        "",
        "`0x125910` 没有以普通指令直接携带完整 marker/材料；保护片段会改变 RSP 和已保存返回地址，把控制流转移到 `.data0/.data2`。",
        "当前静态字节中没有任何一个连续32字节候选能同时把三份独立当前 wrapper 首块解成“统一7字节 marker + 十六进制 selector”。",
        "因此无法证明首块材料，也无法取得正文 selector；继续尝试正文密钥会变成猜测，本轮在这里停止，不输出伪明文。",
        "",
        "## 最短静态路径",
        "",
        "1. 从 `0x125910` 开始还原保护片段对保存返回地址/RSP 的常量变换，恢复真实基本块顺序。",
        "2. 定位合成后的 `0x1159F40` marker 写入和 `0x12B4C20` selector 树节点写入。",
        "3. 读取 node+0x28 的准确 std::string：先取 selector `0x907CAF84` 的首块材料，再取 wrapper 中 selector 对应的正文材料。",
        "4. 回放 AES-256-ECB → 有符号尾长 → zlib，并同时通过 `+260` 长度、zlib EOF、DNT/ACT 全字节消费后才能导出。",
        "",
        "在禁止执行、加载、模拟和进程内存读取的边界下，第1步是当前唯一剩余路径；若无法静态恢复这组保护变换，就没有更短且可靠的替代路径。",
        "",
        "## 当前目标身份",
        "",
        "|目标|PAK|+256密文长度|+260声明明文|密文正文 SHA-256|",
        "|---|---|---:|---:|---|",
    ]
    for row in selected + selected_acts:
        lines.append(f"|{row['name']}|{row['archive']}|{row['indexed_size']}|{row['decoded_size']}|`{row['body_sha256']}`|")
    lines += [
        "",
        "## 产物",
        "",
        "- `current_client_tables_v57.json`：完整静态扫描范围、候选计数、构造函数证据、当前目标正文哈希和阻断点。",
        "- `extracted/`：仅在严格闭合时创建；本轮没有满足条件，因此没有该目录。",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
