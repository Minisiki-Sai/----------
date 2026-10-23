from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import struct
import sys
import time
from collections import Counter, defaultdict
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MEM_COMMIT = 0x1000
MEM_IMAGE = 0x1000000
MEM_MAPPED = 0x40000
MEM_PRIVATE = 0x20000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _win_error(action: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{action}: {ctypes.FormatError(code).strip()}")


def find_process(name: str) -> int:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise _win_error("CreateToolhelp32Snapshot(process)")
    try:
        row = PROCESSENTRY32W()
        row.dwSize = ctypes.sizeof(row)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(row))
        while ok:
            if row.szExeFile.casefold() == name.casefold():
                return int(row.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(row))
    finally:
        kernel32.CloseHandle(snapshot)
    return 0


def main_module(pid: int, preferred_name: str) -> tuple[int, int, str]:
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot == INVALID_HANDLE_VALUE:
        raise _win_error("CreateToolhelp32Snapshot(module)")
    rows: list[tuple[int, int, str, str]] = []
    try:
        row = MODULEENTRY32W()
        row.dwSize = ctypes.sizeof(row)
        ok = kernel32.Module32FirstW(snapshot, ctypes.byref(row))
        while ok:
            rows.append(
                (
                    ctypes.cast(row.modBaseAddr, ctypes.c_void_p).value or 0,
                    int(row.modBaseSize),
                    str(row.szExePath),
                    str(row.szModule),
                )
            )
            ok = kernel32.Module32NextW(snapshot, ctypes.byref(row))
    finally:
        kernel32.CloseHandle(snapshot)
    if not rows:
        raise RuntimeError("未枚举到进程模块；客户端保护可能阻止了只读模块查询")
    return next(
        ((base, size, path) for base, size, path, module in rows if module.casefold() == preferred_name.casefold()),
        rows[0][:3],
    )


def read_memory(handle: int, address: int, size: int) -> bytes:
    if size <= 0:
        return b""
    output = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        handle, ctypes.c_void_p(address), output, size, ctypes.byref(read)
    ):
        return b""
    return output.raw[: read.value]


def readable_segments(handle: int, base: int, size: int) -> list[tuple[int, bytes, int]]:
    end = base + size
    address = base
    segments: list[tuple[int, bytes, int]] = []
    while address < end:
        mbi = MEMORY_BASIC_INFORMATION()
        if not kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
        ):
            address += 0x1000
            continue
        region_base = int(mbi.BaseAddress or address)
        region_end = region_base + int(mbi.RegionSize)
        start = max(address, region_base, base)
        stop = min(region_end, end)
        if (
            mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and (mbi.Protect & 0xFF) != PAGE_NOACCESS
            and stop > start
        ):
            cursor = start
            while cursor < stop:
                chunk_size = min(4 * 1024 * 1024, stop - cursor)
                data = read_memory(handle, cursor, chunk_size)
                if data:
                    segments.append((cursor, data, int(mbi.Protect)))
                cursor += chunk_size
        address = max(address + 0x1000, region_end)
    return segments


def all_readable_segments(
    handle: int, max_address: int = 0x00007FFFFFFFFFFF
) -> tuple[list[tuple[int, bytes, int]], dict[str, int]]:
    """Read every committed user-mode page without modifying the target.

    The unpacked key table may be copied from the image into a private heap, so
    limiting the scan to the PE image can miss it.  Failed/guard/no-access pages
    are skipped and large regions are read in bounded chunks.
    """
    address = 0
    segments: list[tuple[int, bytes, int]] = []
    stats = Counter()
    type_names = {
        MEM_IMAGE: "image",
        MEM_MAPPED: "mapped",
        MEM_PRIVATE: "private",
    }
    while address < max_address:
        mbi = MEMORY_BASIC_INFORMATION()
        if not kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
        ):
            address += 0x1000
            continue
        region_base = int(mbi.BaseAddress or address)
        region_size = int(mbi.RegionSize)
        region_end = region_base + max(region_size, 0x1000)
        if (
            mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and (mbi.Protect & 0xFF) != PAGE_NOACCESS
        ):
            cursor = region_base
            while cursor < region_end:
                chunk_size = min(4 * 1024 * 1024, region_end - cursor)
                data = read_memory(handle, cursor, chunk_size)
                if data:
                    segments.append((cursor, data, int(mbi.Protect)))
                    kind = type_names.get(int(mbi.Type), "other")
                    stats[f"{kind}_bytes"] += len(data)
                    stats[f"{kind}_segments"] += 1
                cursor += chunk_size
        address = max(address + 0x1000, region_end)
    return segments, dict(stats)


def registry_candidates(path: Path) -> dict[int, list[tuple[int, int, int, int]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[int, list[tuple[int, int, int, int]]] = {}
    for raw_index, row in payload.get("keys", {}).items():
        raw_candidates = [row] if "words" in row else row.get("candidates", [])
        parsed: list[tuple[int, int, int, int]] = []
        for candidate in raw_candidates:
            words = candidate.get("words", [])
            if len(words) != 4:
                continue
            parsed.append(
                tuple(
                    int(value, 0) if isinstance(value, str) else int(value)
                    for value in words
                )
            )
        if parsed:
            result[int(raw_index)] = parsed
    return result


def find_all(segments: list[tuple[int, bytes, int]], pattern: bytes, limit: int = 256) -> list[int]:
    hits: list[int] = []
    for base, data, _protect in segments:
        offset = 0
        while len(hits) < limit:
            found = data.find(pattern, offset)
            if found < 0:
                break
            hits.append(base + found)
            offset = found + 1
        if len(hits) >= limit:
            break
    return hits


def score_interleaved_tables(
    handle: int,
    segments: list[tuple[int, bytes, int]],
    known: dict[int, list[tuple[int, int, int, int]]],
) -> list[dict[str, object]]:
    bases: Counter[int] = Counter()
    anchors: defaultdict[int, list[tuple[int, int]]] = defaultdict(list)
    for index, rows in known.items():
        for candidate_index, words in enumerate(rows):
            # Sparse one-round recovery has zero placeholders for the unused
            # middle words; it is not an exact 128-bit table anchor.
            if not words[1] or not words[2]:
                continue
            for hit in find_all(segments, struct.pack("<4I", *words), limit=32):
                table_base = hit - index * 16
                bases[table_base] += 1
                anchors[table_base].append((index, candidate_index))
    results: list[dict[str, object]] = []
    for table_base, anchor_count in bases.most_common(32):
        raw = read_memory(handle, table_base, 256 * 16)
        if len(raw) != 256 * 16:
            continue
        exact = 0
        conflicts: list[int] = []
        for index, candidates in known.items():
            words = struct.unpack_from("<4I", raw, index * 16)
            complete = [row for row in candidates if row[1] and row[2]]
            if not complete:
                continue
            if words in complete:
                exact += 1
            else:
                conflicts.append(index)
        results.append(
            {
                "base": f"0x{table_base:016X}",
                "anchor_hits": anchor_count,
                "exact_known_matches": exact,
                "known_conflicts": conflicts,
                "high_bit_words": sum(
                    word >= 0x80000000 for word in struct.unpack("<1024I", raw)
                ),
                "all_words": [
                    [f"0x{word:08X}" for word in struct.unpack_from("<4I", raw, index * 16)]
                    for index in range(256)
                ],
            }
        )
    return sorted(
        results,
        key=lambda row: (
            int(row["exact_known_matches"]),
            int(row["anchor_hits"]),
            -len(row["known_conflicts"]),
        ),
        reverse=True,
    )


def constant_hits(segments: list[tuple[int, bytes, int]]) -> dict[str, list[str]]:
    patterns = {
        "xtea_delta_le": struct.pack("<I", 0x9E3779B9),
        "xtea_negative_delta_le": struct.pack("<I", 0x61C88647),
        "xtea_delta_be": struct.pack(">I", 0x9E3779B9),
    }
    return {
        label: [f"0x{address:016X}" for address in find_all(segments, pattern)]
        for label, pattern in patterns.items()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读扫描已完成自解包的 Dragon Nest 客户端密钥表"
    )
    parser.add_argument("--process", default="dragonnest_reborn.exe")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--wait", type=float, default=0.0, help="等待进程出现的秒数")
    parser.add_argument(
        "--scope",
        choices=("main", "all"),
        default="all",
        help="扫描主模块，或扫描全部可读内存（默认，包含私有堆）",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("dragon_nest_builder/data/live_dps_profiles/tcp_profile_keys_v576.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/v576_client_key_scan.json"),
    )
    return parser.parse_args()


def run() -> int:
    if sys.platform != "win32":
        raise RuntimeError("该提取器只支持 Windows")
    args = parse_args()
    deadline = time.monotonic() + max(0.0, args.wait)
    pid = int(args.pid or 0)
    while pid <= 0:
        pid = find_process(args.process)
        if pid > 0 or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if pid <= 0:
        print(f"未找到进程：{args.process}", file=sys.stderr)
        return 2

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        raise _win_error("OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ)")
    try:
        base, image_size, image_path = main_module(pid, args.process)
        if args.scope == "all":
            segments, memory_types = all_readable_segments(handle)
        else:
            segments = readable_segments(handle, base, image_size)
            memory_types = {"main_image_bytes": sum(len(data) for _base, data, _protect in segments)}
        known = registry_candidates(args.registry)
        tables = score_interleaved_tables(handle, segments, known)
        image_sha256 = ""
        image_file = Path(image_path)
        if image_file.is_file():
            image_sha256 = hashlib.sha256(image_file.read_bytes()).hexdigest().upper()
        report = {
            "schema_version": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "pid": pid,
            "process": args.process,
            "image_path": image_path,
            "image_sha256": image_sha256,
            "module_base": f"0x{base:016X}",
            "module_size": image_size,
            "readable_segments": len(segments),
            "readable_bytes": sum(len(data) for _base, data, _protect in segments),
            "scan_scope": args.scope,
            "memory_types": memory_types,
            "known_indexes": len(known),
            "constant_hits": constant_hits(segments),
            "candidate_tables": tables,
            "complete_table_found": bool(
                tables
                and int(tables[0]["exact_known_matches"]) >= 3
                and not tables[0]["known_conflicts"]
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        best = tables[0] if tables else None
        print(
            json.dumps(
                {
                    "pid": pid,
                    "image": image_path,
                    "readable_mb": round(report["readable_bytes"] / 1048576, 2),
                    "xtea_constants": {
                        key: len(value)
                        for key, value in report["constant_hits"].items()
                    },
                    "candidate_tables": len(tables),
                    "best": (
                        {
                            "base": best["base"],
                            "known_matches": best["exact_known_matches"],
                            "conflicts": best["known_conflicts"],
                        }
                        if best
                        else None
                    ),
                    "output": str(args.output.resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(run())
