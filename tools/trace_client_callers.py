"""List direct callers of a target RVA in the reconstructed client image."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from pathlib import Path

import capstone
from capstone import x86_const as x86
import pefile


IMAGE_BASE = 0x140000000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("pe", type=Path)
    parser.add_argument("target", type=lambda value: int(value, 0))
    args = parser.parse_args()
    image = args.image.read_bytes()
    pe = pefile.PE(str(args.pe), fast_load=False)
    functions = sorted(
        (int(row.struct.BeginAddress), int(row.struct.EndAddress))
        for row in pe.DIRECTORY_ENTRY_EXCEPTION
    )
    starts = [start for start, _end in functions]
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    count = 0
    for start, end in functions:
        if not 0 <= start < end <= len(image):
            continue
        for instruction in decoder.disasm(image[start:end], IMAGE_BASE + start):
            if instruction.mnemonic != "call" or not instruction.operands:
                continue
            operand = instruction.operands[0]
            if operand.type != x86.X86_OP_IMM:
                continue
            target = int(operand.imm) - IMAGE_BASE
            if target != args.target:
                continue
            index = bisect_right(starts, instruction.address - IMAGE_BASE) - 1
            function = functions[index] if index >= 0 else (start, end)
            print(
                f"call={instruction.address - IMAGE_BASE:#x} "
                f"function={function[0]:#x}-{function[1]:#x}"
            )
            count += 1
    print(f"caller_count={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
