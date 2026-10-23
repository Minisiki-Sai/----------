from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from bisect import bisect_right
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tmp" / "reverse_tools_v39"))

import capstone
import pefile
from capstone import x86_const as x86


NETWORK_IMPORTS = {
    "recv",
    "recvfrom",
    "send",
    "sendto",
    "socket",
    "connect",
}
FOCUS_RTTI = (
    ".?AVCNetSocketRUDP@@",
    ".?AVCCryptoPacket@@",
    ".?AVCClientSocket@@",
    ".?AVCGameClientSession@@",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def instruction_row(ins: capstone.CsInsn, image_base: int) -> dict[str, object]:
    return {
        "rva": f"0x{ins.address - image_base:08X}",
        "bytes": ins.bytes.hex(),
        "mnemonic": ins.mnemonic,
        "operands": ins.op_str,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline trace of the current client's UDP socket/cipher code."
    )
    parser.add_argument(
        "image",
        type=Path,
        nargs="?",
        default=Path(
            "outputs/client_unpack_with_keys/dragonnest_reborn.reconstructed.exe"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/client_unpack_with_keys/udp_cipher_trace.json"),
    )
    args = parser.parse_args()
    image = args.image.resolve()
    pe = pefile.PE(str(image), fast_load=False)
    image_base = int(pe.OPTIONAL_HEADER.ImageBase)
    image_size = int(pe.OPTIONAL_HEADER.SizeOfImage)

    imports: dict[int, str] = {}
    for descriptor in getattr(pe, "DIRECTORY_ENTRY_IMPORT", ()):
        dll = (descriptor.dll or b"").decode("latin1", errors="replace")
        for item in descriptor.imports:
            name = (item.name or b"").decode("latin1", errors="replace")
            if name.casefold() in NETWORK_IMPORTS:
                imports[int(item.address)] = f"{dll}!{name}"

    functions = sorted(
        (
            int(entry.struct.BeginAddress),
            int(entry.struct.EndAddress),
            int(entry.struct.UnwindData),
        )
        for entry in getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", ())
        if 0 < int(entry.struct.BeginAddress) < int(entry.struct.EndAddress) <= image_size
    )
    starts = [row[0] for row in functions]

    def owner(rva: int) -> tuple[int, int, int] | None:
        index = bisect_right(starts, int(rva)) - 1
        if index < 0:
            return None
        row = functions[index]
        return row if row[0] <= int(rva) < row[1] else None

    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    decoded: dict[int, list[capstone.CsInsn]] = {}

    def decode(row: tuple[int, int, int]) -> list[capstone.CsInsn]:
        start, end, _unwind = row
        if start in decoded:
            return decoded[start]
        body = pe.get_data(start, end - start)
        rows = list(decoder.disasm(body, image_base + start))
        decoded[start] = rows
        return rows

    socket_refs: list[dict[str, object]] = []
    direct_callers: dict[int, list[dict[str, object]]] = defaultdict(list)
    direct_edges: dict[int, set[int]] = defaultdict(set)
    crypto_signals: dict[int, Counter[str]] = defaultdict(Counter)

    # The exception directory supplies exact function boundaries. Decode each
    # range once; no capture bytes or existing ACT key are inputs to this pass.
    for number, function in enumerate(functions):
        rows = decode(function)
        begin = function[0]
        for index, ins in enumerate(rows):
            direct_target: int | None = None
            if (
                ins.operands
                and ins.operands[0].type == x86.X86_OP_IMM
                and (ins.group(capstone.CS_GRP_CALL) or ins.group(capstone.CS_GRP_JUMP))
            ):
                target_va = int(ins.operands[0].imm)
                if image_base <= target_va < image_base + image_size:
                    direct_target = target_va - image_base
                    target_owner = owner(direct_target)
                    if target_owner is not None and target_owner[0] == direct_target:
                        direct_edges[begin].add(direct_target)
                        direct_callers[direct_target].append(
                            {
                                "caller_rva": f"0x{begin:08X}",
                                "instruction": instruction_row(ins, image_base),
                            }
                        )

            for operand in ins.operands:
                if operand.type != x86.X86_OP_MEM or operand.mem.base != x86.X86_REG_RIP:
                    continue
                target_va = int(ins.address + ins.size + operand.mem.disp)
                label = imports.get(target_va)
                if label is None:
                    continue
                context = rows[max(0, index - 8) : index + 10]
                socket_refs.append(
                    {
                        "function_begin_rva": f"0x{begin:08X}",
                        "function_end_rva": f"0x{function[1]:08X}",
                        "import": label,
                        "instruction": instruction_row(ins, image_base),
                        "context": [instruction_row(row, image_base) for row in context],
                    }
                )

            mnemonic = ins.mnemonic.casefold()
            if mnemonic.startswith("xor"):
                crypto_signals[begin]["xor_instruction"] += 1
            if mnemonic in {"div", "idiv"}:
                crypto_signals[begin]["division_instruction"] += 1
            for operand in ins.operands:
                if operand.type != x86.X86_OP_IMM:
                    continue
                value = int(operand.imm) & 0xFFFFFFFFFFFFFFFF
                if value == 13:
                    crypto_signals[begin]["immediate_13"] += 1
                if value in {
                    0x4EC4EC4F,
                    0x9D89D89D,
                    0x4EC4EC4EC4EC4EC5,
                    0x9D89D89D89D89D8A,
                }:
                    crypto_signals[begin]["mod13_magic"] += 1
        if number and number % 10000 == 0:
            print(json.dumps({"functions": number, "socket_refs": len(socket_refs)}))

    socket_functions = {
        int(row["function_begin_rva"], 16) for row in socket_refs
    }
    # Retain a bounded reverse call graph around socket wrappers and a bounded
    # forward graph from their direct callers. The UDP transform can live in a
    # caller or in one helper below a caller, rather than in recvfrom/sendto.
    neighborhood = set(socket_functions)
    frontier = deque((value, 0, "backward") for value in socket_functions)
    while frontier:
        current, depth, _direction = frontier.popleft()
        if depth >= 5:
            continue
        for caller in direct_callers.get(current, ()):
            candidate = int(caller["caller_rva"], 16)
            if candidate not in neighborhood:
                neighborhood.add(candidate)
                frontier.append((candidate, depth + 1, "backward"))
    seed = tuple(neighborhood)
    frontier = deque((value, 0) for value in seed)
    while frontier:
        current, depth = frontier.popleft()
        if depth >= 3:
            continue
        for target in direct_edges.get(current, ()):
            if target not in neighborhood:
                neighborhood.add(target)
                frontier.append((target, depth + 1))

    candidates: list[dict[str, object]] = []
    for begin in sorted(neighborhood):
        function = owner(begin)
        if function is None:
            continue
        signals = crypto_signals.get(begin, Counter())
        if not signals and begin not in socket_functions:
            continue
        rows = decode(function)
        candidates.append(
            {
                "function_begin_rva": f"0x{begin:08X}",
                "function_end_rva": f"0x{function[1]:08X}",
                "signals": dict(signals),
                "socket_imports": sorted(
                    {
                        row["import"]
                        for row in socket_refs
                        if int(row["function_begin_rva"], 16) == begin
                    }
                ),
                "direct_callees": [f"0x{value:08X}" for value in sorted(direct_edges.get(begin, ()))],
                "direct_callers": direct_callers.get(begin, ()),
                "instructions": [instruction_row(row, image_base) for row in rows],
            }
        )

    raw = image.read_bytes()
    rtti = {}
    for name in FOCUS_RTTI:
        encoded = name.encode("ascii")
        offsets: list[str] = []
        start = 0
        while True:
            found = raw.find(encoded, start)
            if found < 0:
                break
            offsets.append(f"0x{found:08X}")
            start = found + 1
        rtti[name] = offsets

    report = {
        "schema_version": 1,
        "source": str(image),
        "source_sha256": sha256(image),
        "method": (
            "offline reconstructed-client exception-boundary disassembly; "
            "no packet captures and no ACT UDP key are consumed"
        ),
        "image_base": f"0x{image_base:016X}",
        "image_size": image_size,
        "runtime_function_count": len(functions),
        "network_imports": {f"0x{address:016X}": label for address, label in imports.items()},
        "socket_references": socket_refs,
        "candidate_function_count": len(candidates),
        "candidate_functions": candidates,
        "rtti_file_offsets": rtti,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "socket_references": len(socket_refs),
                "candidate_functions": len(candidates),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
