"""Dump integer words from one RVA in a reconstructed memory image."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("offset", type=lambda value: int(value, 0))
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--width", type=int, choices=(1, 2, 4, 8), default=8)
    args = parser.parse_args()
    data = args.image.read_bytes()
    for index in range(args.count):
        offset = args.offset + index * args.width
        raw = data[offset : offset + args.width]
        if len(raw) < args.width:
            break
        print(
            f"{offset:#010x} {raw.hex().upper():<{args.width * 2}} "
            f"{int.from_bytes(raw, 'little'):#x}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
