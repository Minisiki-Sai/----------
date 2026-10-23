from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import struct
import time
from collections import Counter
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import pefile


CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
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


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_size_t),
        ("Reserved3", ctypes.c_void_p),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.restype = wintypes.DWORD
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
ntdll.NtQueryInformationProcess.restype = wintypes.LONG


def win_error(action: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{action}: {ctypes.FormatError(code).strip()}")


def align(value: int, unit: int) -> int:
    return (value + unit - 1) // unit * unit


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    return -sum((count / len(data)) * math.log2(count / len(data)) for count in counts.values())


def module_base(pid: int, expected_name: str, fallback: int) -> int:
    for _attempt in range(100):
        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
        )
        if snapshot != INVALID_HANDLE_VALUE:
            try:
                row = MODULEENTRY32W()
                row.dwSize = ctypes.sizeof(row)
                ok = kernel32.Module32FirstW(snapshot, ctypes.byref(row))
                while ok:
                    if row.szModule.casefold() == expected_name.casefold():
                        return ctypes.cast(row.modBaseAddr, ctypes.c_void_p).value or fallback
                    ok = kernel32.Module32NextW(snapshot, ctypes.byref(row))
            finally:
                kernel32.CloseHandle(snapshot)
        time.sleep(0.01)
    return fallback


def peb_image_base(handle: int, fallback: int) -> tuple[int, int]:
    """Read the image base from the x64 PEB before the first thread runs."""
    info = PROCESS_BASIC_INFORMATION()
    returned = wintypes.ULONG()
    status = ntdll.NtQueryInformationProcess(
        handle,
        0,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    )
    if status != 0 or not info.PebBaseAddress:
        return fallback, int(info.PebBaseAddress or 0)
    pointer = ctypes.c_uint64()
    read = ctypes.c_size_t()
    ok = kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(int(info.PebBaseAddress) + 0x10),
        ctypes.byref(pointer),
        ctypes.sizeof(pointer),
        ctypes.byref(read),
    )
    if not ok or read.value != ctypes.sizeof(pointer) or not pointer.value:
        return fallback, int(info.PebBaseAddress)
    return int(pointer.value), int(info.PebBaseAddress)


def read_exact(handle: int, address: int, size: int) -> bytes:
    output = bytearray(size)
    chunk_size = 64 * 1024
    for offset in range(0, size, chunk_size):
        count = min(chunk_size, size - offset)
        buffer = ctypes.create_string_buffer(count)
        read = ctypes.c_size_t()
        if kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(address + offset),
            buffer,
            count,
            ctypes.byref(read),
        ):
            output[offset : offset + read.value] = buffer.raw[: read.value]
    return bytes(output)


def section_snapshot(handle: int, base: int, pe: pefile.PE) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for section in pe.sections:
        name = section.Name.rstrip(b"\0").decode("latin1", errors="replace")
        size = min(int(section.Misc_VirtualSize), 256 * 1024)
        data = read_exact(handle, base + int(section.VirtualAddress), size)
        rows.append(
            {
                "name": name,
                "sample_size": len(data),
                "nonzero": sum(value != 0 for value in data),
                "entropy": round(entropy(data), 6),
                "sha256": hashlib.sha256(data).hexdigest().upper(),
            }
        )
    return rows


def reconstruction(original: bytes, memory_image: bytes, pe: pefile.PE) -> bytes:
    file_alignment = int(pe.OPTIONAL_HEADER.FileAlignment)
    headers_size = int(pe.OPTIONAL_HEADER.SizeOfHeaders)
    output = bytearray(original[:headers_size])
    cursor = align(headers_size, file_alignment)
    if len(output) < cursor:
        output.extend(b"\0" * (cursor - len(output)))
    for section in pe.sections:
        virtual_size = int(section.Misc_VirtualSize)
        raw_size = align(virtual_size, file_alignment)
        virtual_address = int(section.VirtualAddress)
        data = memory_image[virtual_address : virtual_address + virtual_size]
        if len(output) < cursor + raw_size:
            output.extend(b"\0" * (cursor + raw_size - len(output)))
        output[cursor : cursor + len(data)] = data
        header_offset = int(section.get_file_offset())
        struct.pack_into("<I", output, header_offset + 16, raw_size)
        struct.pack_into("<I", output, header_offset + 20, cursor)
        cursor += raw_size
    # The Authenticode table is file-offset based and no longer valid after
    # reconstruction. Clearing only its directory entry keeps the dump honest.
    security = pe.OPTIONAL_HEADER.DATA_DIRECTORY[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]
    ]
    directory_offset = int(security.get_file_offset())
    struct.pack_into("<II", output, directory_offset, 0, 0)
    return bytes(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a local client child and reconstruct sections after self-unpack"
    )
    parser.add_argument(
        "executable",
        type=Path,
        nargs="?",
        default=Path(r"D:\DragonNest\Reborn\dragonnest_reborn.exe"),
    )
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/client_unpack"))
    parser.add_argument(
        "--key-table-pointer-rva",
        type=lambda value: int(value, 0),
        default=0x11AF568,
        help="RVA of the runtime pointer to the 256-entry TCP XTEA table",
    )
    parser.add_argument(
        "--udp-xor-key-rva",
        type=lambda value: int(value, 0),
        default=0x115E220,
        help="RVA of the current-client 13-byte UDP XOR table",
    )
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    executable = args.executable.resolve()
    original = executable.read_bytes()
    pe = pefile.PE(data=original)
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = STARTF_USESHOWWINDOW
    startup.wShowWindow = SW_HIDE
    process = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(f'"{executable}"')
    # The official launcher exposes GPKitClt64.dll from the install root via
    # its child environment. Reproduce only that ordinary DLL search path;
    # do not inject or substitute any module.
    child_environment = dict(os.environ)
    install_root = executable.parent.parent
    child_environment["PATH"] = (
        str(install_root)
        + os.pathsep
        + child_environment.get("PATH", "")
    )
    environment_block = ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in sorted(child_environment.items()))
        + "\0\0"
    )
    ok = kernel32.CreateProcessW(
        str(executable),
        command_line,
        None,
        None,
        False,
        CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        ctypes.cast(environment_block, ctypes.c_void_p),
        str(executable.parent),
        ctypes.byref(startup),
        ctypes.byref(process),
    )
    if not ok:
        raise win_error("CreateProcessW")
    spawned_pid = int(process.dwProcessId)
    terminated_by_tool = False
    try:
        base, peb_address = peb_image_base(
            process.hProcess,
            int(pe.OPTIONAL_HEADER.ImageBase),
        )
        before = section_snapshot(process.hProcess, base, pe)
        if kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
            raise win_error("ResumeThread")
        started = time.monotonic()
        snapshots: list[dict[str, object]] = []
        best_score = -1
        best_image = b""
        best_key_table = b""
        best_key_table_pointer = 0
        exit_code = wintypes.DWORD(STILL_ACTIVE)
        while time.monotonic() - started <= max(1.0, args.timeout):
            elapsed = time.monotonic() - started
            kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code))
            rows = section_snapshot(process.hProcess, base, pe)
            score = sum(
                int(row["nonzero"])
                for row in rows
                if row["name"] in {".text", ".rdata", ".data", ".pdata", ".data0"}
            )
            snapshots.append(
                {
                    "elapsed_seconds": round(elapsed, 3),
                    "exit_code": int(exit_code.value),
                    "score": score,
                    "sections": rows,
                }
            )
            if score > best_score:
                best_score = score
                best_image = read_exact(
                    process.hProcess,
                    base,
                    int(pe.OPTIONAL_HEADER.SizeOfImage),
                )
            pointer_bytes = read_exact(
                process.hProcess,
                base + int(args.key_table_pointer_rva),
                8,
            )
            key_table_pointer = struct.unpack("<Q", pointer_bytes)[0]
            if 0x10000 <= key_table_pointer <= 0x00007FFFFFFFFFFF:
                # The object has an eight-byte header followed by 256
                # consecutive 128-bit keys selected by the low byte of the
                # encrypted body length.
                candidate_table = read_exact(
                    process.hProcess,
                    key_table_pointer + 8,
                    256 * 16,
                )
                if sum(value != 0 for value in candidate_table) > sum(
                    value != 0 for value in best_key_table
                ):
                    best_key_table = candidate_table
                    best_key_table_pointer = key_table_pointer
            if exit_code.value != STILL_ACTIVE:
                break
            time.sleep(0.05 if elapsed < 2.0 else 0.2)
        kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code))
        if exit_code.value == STILL_ACTIVE:
            # Only the child created by this tool is stopped.
            kernel32.TerminateProcess(process.hProcess, 0)
            kernel32.WaitForSingleObject(process.hProcess, 5000)
            terminated_by_tool = True

        args.output_dir.mkdir(parents=True, exist_ok=True)
        memory_path = args.output_dir / "dragonnest_reborn.memory-image.bin"
        rebuilt_path = args.output_dir / "dragonnest_reborn.reconstructed.exe"
        report_path = args.output_dir / "unpack_report.json"
        key_table_path = args.output_dir / "tcp_key_table_v576.bin"
        key_table_json_path = args.output_dir / "tcp_key_table_v576.json"
        udp_key_json_path = args.output_dir / "udp_key_v576.json"
        memory_path.write_bytes(best_image)
        rebuilt = reconstruction(original, best_image, pe)
        rebuilt_path.write_bytes(rebuilt)
        key_table_path.write_bytes(best_key_table)
        key_rows = []
        if len(best_key_table) == 256 * 16:
            key_rows = [
                [
                    f"0x{word:08X}"
                    for word in struct.unpack_from("<4I", best_key_table, index * 16)
                ]
                for index in range(256)
            ]
        key_table_json_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol_version": "v576",
                    "source_sha256": hashlib.sha256(original).hexdigest().upper(),
                    "pointer_rva": f"0x{int(args.key_table_pointer_rva):08X}",
                    "runtime_pointer": f"0x{best_key_table_pointer:016X}",
                    "entry_count": len(key_rows),
                    "entry_size": 16,
                    "keys": {str(index): row for index, row in enumerate(key_rows)},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        udp_key_rva = int(args.udp_xor_key_rva)
        udp_key = best_image[udp_key_rva : udp_key_rva + 13]
        udp_inverse_table = best_image[udp_key_rva - 8 : udp_key_rva]
        udp_forward_table = best_image[udp_key_rva - 16 : udp_key_rva - 8]
        udp_key_json_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol_version": "v576",
                    "source_sha256": hashlib.sha256(original).hexdigest().upper(),
                    "source_kind": "current_client_runtime_image",
                    "key_rva": f"0x{udp_key_rva:08X}",
                    "key_length": len(udp_key),
                    "key_hex_client_order": udp_key.hex(),
                    "forward_bit_table_rva": f"0x{udp_key_rva - 16:08X}",
                    "forward_bit_table_hex": udp_forward_table.hex(),
                    "inverse_bit_table_rva": f"0x{udp_key_rva - 8:08X}",
                    "inverse_bit_table_hex": udp_inverse_table.hex(),
                    "decoder_rva": "0x00254220",
                    "encoder_rva": "0x00254380",
                    "modulus": 13,
                    "body_offset_in_inner_frame": 7,
                    "outer_prefix_bytes": 3,
                    "body_offset_on_wire": 10,
                    "derivation": (
                        "CNetSocketRUDP/CCryptoPacket RTTI and virtual-call chain; "
                        "decoder/encoder use the 0x4EC4EC4EC4EC4EC5 reciprocal "
                        "and multiply by 13 before indexing this table"
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report = {
            "schema_version": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": str(executable),
            "source_sha256": hashlib.sha256(original).hexdigest().upper(),
            "spawned_pid": spawned_pid,
            "module_base": f"0x{base:016X}",
            "peb_address": f"0x{peb_address:016X}",
            "image_size": int(pe.OPTIONAL_HEADER.SizeOfImage),
            "entrypoint_rva": f"0x{int(pe.OPTIONAL_HEADER.AddressOfEntryPoint):08X}",
            "initial_sections": before,
            "best_score": best_score,
            "final_exit_code": int(exit_code.value),
            "terminated_by_tool": terminated_by_tool,
            "snapshots": snapshots,
            "memory_image": str(memory_path.resolve()),
            "memory_sha256": hashlib.sha256(best_image).hexdigest().upper(),
            "reconstructed_image": str(rebuilt_path.resolve()),
            "reconstructed_sha256": hashlib.sha256(rebuilt).hexdigest().upper(),
            "key_table_pointer_rva": f"0x{int(args.key_table_pointer_rva):08X}",
            "key_table_runtime_pointer": f"0x{best_key_table_pointer:016X}",
            "key_table_bytes": len(best_key_table),
            "key_table": str(key_table_json_path.resolve()),
            "udp_xor_key_rva": f"0x{udp_key_rva:08X}",
            "udp_xor_key": str(udp_key_json_path.resolve()),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "pid": spawned_pid,
                    "exit_code": int(exit_code.value),
                    "terminated_by_tool": terminated_by_tool,
                    "best_score": best_score,
                    "snapshots": len(snapshots),
                    "key_table_bytes": len(best_key_table),
                    "key_table_pointer": f"0x{best_key_table_pointer:016X}",
                    "report": str(report_path.resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        kernel32.CloseHandle(process.hThread)
        kernel32.CloseHandle(process.hProcess)


if __name__ == "__main__":
    raise SystemExit(run())
