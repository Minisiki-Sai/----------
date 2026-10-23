"""List PE exception-directory function ranges overlapping an RVA window."""

from __future__ import annotations

import argparse
from pathlib import Path

import pefile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pe", type=Path)
    parser.add_argument("start", type=lambda value: int(value, 0))
    parser.add_argument("end", type=lambda value: int(value, 0))
    args = parser.parse_args()
    pe = pefile.PE(str(args.pe), fast_load=False)
    for entry in pe.DIRECTORY_ENTRY_EXCEPTION:
        start = int(entry.struct.BeginAddress)
        end = int(entry.struct.EndAddress)
        if start < args.end and end > args.start:
            print(f"{start:#x}-{end:#x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
