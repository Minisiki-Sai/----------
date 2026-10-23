"""Disassemble one RVA range in the reconstructed Dragon Nest image."""

from __future__ import annotations

import argparse
from pathlib import Path

import capstone
from capstone import x86_const as x86


IMAGE_BASE = 0x140000000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("start", type=lambda value: int(value, 0))
    parser.add_argument("end", type=lambda value: int(value, 0))
    args = parser.parse_args()
    image = args.image.read_bytes()
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    for instruction in decoder.disasm(
        image[args.start : args.end], IMAGE_BASE + args.start
    ):
        annotation = ""
        for operand in instruction.operands:
            if operand.type != x86.X86_OP_MEM or operand.mem.base != x86.X86_REG_RIP:
                continue
            target = (
                instruction.address
                + instruction.size
                + operand.mem.disp
                - IMAGE_BASE
            )
            if not 0 <= target < len(image):
                continue
            raw = image[target : target + 96]
            preview = ""
            try:
                preview = raw.split(b"\0", 1)[0].decode("ascii")
            except UnicodeDecodeError:
                pass
            if preview and all(31 < ord(char) < 127 for char in preview):
                annotation = f" ; {target:#x} {preview!r}"
            else:
                annotation = f" ; target={target:#x}"
        print(
            f"{instruction.address - IMAGE_BASE:#010x}  "
            f"{instruction.mnemonic:8} {instruction.op_str}{annotation}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
