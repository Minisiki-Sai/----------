"""Audit the current client's engraving instance-option resolution path.

This is a static, hash-reporting helper.  It never loads or executes the game
image; it disassembles the already reconstructed memory image and prints the
instructions that reference the two instance selector bytes.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from hashlib import sha256
from pathlib import Path

import capstone
from capstone import x86_const as x86
import pefile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "outputs" / "client_unpack_with_udp_key"
IMAGE_BASE = 0x140000000
INSTANCE_OFFSETS = {0x1213, 0x1216, 0x123B, 0x123E, 0x14D8}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--min-offset", type=lambda value: int(value, 0))
    parser.add_argument("--max-offset", type=lambda value: int(value, 0))
    args = parser.parse_args()

    image_path = args.root / "dragonnest_reborn.memory-image.bin"
    pe_path = args.root / "dragonnest_reborn.reconstructed.exe"
    image = image_path.read_bytes()
    pe = pefile.PE(str(pe_path), fast_load=False)
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True

    print(f"memory_image_sha256={digest(image_path)}")
    print(f"reconstructed_pe_sha256={digest(pe_path)}")
    functions = sorted(
        (
            int(entry.struct.BeginAddress),
            int(entry.struct.EndAddress),
        )
        for entry in pe.DIRECTORY_ENTRY_EXCEPTION
    )
    starts = [start for start, _end in functions]
    wanted_offsets = set(INSTANCE_OFFSETS)
    if args.min_offset is not None and args.max_offset is not None:
        wanted_offsets = set(range(args.min_offset, args.max_offset + 1))
    candidate_functions: set[tuple[int, int]] = set()
    for displacement in wanted_offsets:
        needle = displacement.to_bytes(4, "little")
        offset = 0
        while (offset := image.find(needle, offset)) >= 0:
            index = bisect_right(starts, offset) - 1
            if index >= 0:
                start, end = functions[index]
                if start <= offset < end:
                    candidate_functions.add((start, end))
            offset += 1

    rows: list[tuple[int, str, str, int, int, int]] = []
    for start, end in sorted(candidate_functions):
        if not 0 <= start < end <= len(image):
            continue
        for instruction in decoder.disasm(image[start:end], IMAGE_BASE + start):
            for operand in instruction.operands:
                if (
                    operand.type == x86.X86_OP_MEM
                    and operand.mem.disp in wanted_offsets
                ):
                    rows.append(
                        (
                            instruction.address - IMAGE_BASE,
                            instruction.mnemonic,
                            instruction.op_str,
                            operand.mem.disp,
                            start,
                            end,
                        )
                    )

    for address, mnemonic, operands, displacement, start, end in rows:
        print(
            f"{address:#010x} {mnemonic:8} {operands:48} "
            f"disp={displacement:#x} function={start:#x}-{end:#x}"
        )
    print(f"reference_count={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
