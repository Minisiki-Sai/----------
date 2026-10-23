"""List functions whose RIP-relative operands reference one target RVA."""

from __future__ import annotations

import argparse
from pathlib import Path

import capstone
from capstone import x86_const as x86
import pefile


IMAGE_BASE = 0x140000000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("pe", type=Path)
    parser.add_argument(
        "targets",
        nargs="+",
        type=lambda value: int(value, 0),
        help="one or more target RVAs",
    )
    args = parser.parse_args()
    image = args.image.read_bytes()
    pe = pefile.PE(str(args.pe), fast_load=False)
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    targets = set(args.targets)
    counts = {target: 0 for target in targets}
    for entry in pe.DIRECTORY_ENTRY_EXCEPTION:
        start = int(entry.struct.BeginAddress)
        end = int(entry.struct.EndAddress)
        if not 0 <= start < end <= len(image):
            continue
        for instruction in decoder.disasm(image[start:end], IMAGE_BASE + start):
            for operand in instruction.operands:
                if (
                    operand.type != x86.X86_OP_MEM
                    or operand.mem.base != x86.X86_REG_RIP
                ):
                    continue
                target = (
                    instruction.address
                    + instruction.size
                    + operand.mem.disp
                    - IMAGE_BASE
                )
                if target not in targets:
                    continue
                print(
                    f"target={target:#x} "
                    f"reference={instruction.address - IMAGE_BASE:#x} "
                    f"function={start:#x}-{end:#x} "
                    f"instruction={instruction.mnemonic} {instruction.op_str}"
                )
                counts[target] += 1
    for target in args.targets:
        print(f"target={target:#x} reference_count={counts[target]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
