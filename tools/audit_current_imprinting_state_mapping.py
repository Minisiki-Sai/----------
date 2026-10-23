"""Audit current-client imprint templates and state dispatch targets.

This helper is intentionally read-only.  Tooltip observations are validation
fixtures only; they are never imported by the production decoder.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.dump_dnt_schema import read_table


DEFAULT_RESOURCE_ROOT = Path(r"D:\DragonNest\Reborn\Resource_unpacked_all")
DEFAULT_IMAGE = (
    PROJECT_ROOT
    / "outputs"
    / "client_unpack_with_udp_key"
    / "dragonnest_reborn.memory-image.bin"
)
TEMPLATE_IDS = (268420017, 268420347, 268420585, 268420587)
STATE_IDS = (29, 77, 78, 79, 103, 104)


def _state_columns(fields: list[tuple[str, int]]) -> list[str]:
    return [
        name
        for name, _kind in fields
        if name.startswith("_State")
        and not name.endswith(("Value", "_Min", "_Max", "_GenProb"))
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    args = parser.parse_args()

    print("[template sources]")
    for path in sorted(args.resource_root.rglob("itemtable*.dnt")):
        try:
            fields, rows = read_table(path)
        except (OSError, UnicodeDecodeError, ValueError, struct.error):
            continue
        matches = [row for row in rows if int(row["RowId"]) in TEMPLATE_IDS]
        for row in matches:
            print(
                f"{path}: RowId={row['RowId']} Type={row.get('_Type')} "
                f"TypeParam1={row.get('_TypeParam1')} "
                f"TypeParam2={row.get('_TypeParam2')} Rank={row.get('_Rank')}"
            )

    print("\n[state occurrences]")
    occurrences: dict[int, list[str]] = defaultdict(list)
    for path in sorted(args.resource_root.rglob("*.dnt")):
        try:
            fields, rows = read_table(path)
        except (OSError, UnicodeDecodeError, ValueError, struct.error):
            continue
        columns = _state_columns(fields)
        if not columns:
            continue
        for row in rows:
            for column in columns:
                try:
                    state_id = int(row.get(column) or -1)
                except (TypeError, ValueError):
                    continue
                if state_id not in STATE_IDS:
                    continue
                occurrences[state_id].append(
                    f"{path.name}:RowId={row['RowId']}:{column}"
                )
    for state_id in STATE_IDS:
        rows = occurrences[state_id]
        print(f"state={state_id} occurrences={len(rows)}")
        for row in rows[:20]:
            print(f"  {row}")

    image = args.image.read_bytes()
    print("\n[BuildState dispatch]")
    for state_id in STATE_IDS:
        dispatch_index = image[0x86A16C + state_id]
        target_rva = struct.unpack_from(
            "<i", image, 0x86A044 + dispatch_index * 4
        )[0]
        print(
            f"state={state_id} dispatch_index={dispatch_index} "
            f"target_rva={target_rva:#x}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
