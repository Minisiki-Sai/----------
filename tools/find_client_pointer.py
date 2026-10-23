"""Find little-endian absolute/RVA pointers in a reconstructed image."""

from __future__ import annotations

import argparse
from pathlib import Path


IMAGE_BASE = 0x140000000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("target", type=lambda value: int(value, 0))
    parser.add_argument("--window", type=int, default=48)
    args = parser.parse_args()
    data = args.image.read_bytes()
    values = {
        (args.target, 4),
        (args.target, 8),
        (IMAGE_BASE + args.target, 8),
    }
    for value, width in sorted(values):
        needle = value.to_bytes(width, "little")
        offset = 0
        while (offset := data.find(needle, offset)) >= 0:
            start = max(0, offset - args.window)
            end = min(len(data), offset + 8 + args.window)
            print(
                f"value={value:#x} width={width} offset={offset:#x} "
                f"window={data[start:end].hex().upper()}"
            )
            offset += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
