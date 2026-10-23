"""Print schema and selected rows from unpacked Dragon Nest DNT tables."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def read_table(path: Path) -> tuple[list[tuple[str, int]], list[dict[str, object]]]:
    data = path.read_bytes()
    offset = 4
    column_count = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    row_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    fields: list[tuple[str, int]] = []
    for _ in range(column_count):
        size = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        name = data[offset : offset + size].decode("ascii")
        offset += size
        field_type = data[offset]
        offset += 1
        fields.append((name, field_type))
    rows: list[dict[str, object]] = []
    for _ in range(row_count):
        row: dict[str, object] = {
            "RowId": struct.unpack_from("<I", data, offset)[0]
        }
        offset += 4
        for name, field_type in fields:
            if field_type == 1:
                size = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                raw = data[offset : offset + size]
                offset += size
                value: object = raw.decode("utf-8", errors="replace")
            elif field_type in (2, 3, 4):
                value = struct.unpack_from("<i", data, offset)[0]
                offset += 4
            elif field_type == 5:
                value = struct.unpack_from("<f", data, offset)[0]
                offset += 4
            elif field_type == 6:
                value = struct.unpack_from("<Q", data, offset)[0]
                offset += 8
            else:
                raise ValueError(f"unsupported field type {field_type}")
            row[name] = value
        rows.append(row)
    # Current client tables can carry a fixed Pascal-string terminator after
    # the declared rows.  Accept only the observed exact marker; any other
    # remainder still means the schema was misread.
    if offset != len(data) and data[offset:] != b"\x05THEND":
        raise ValueError(f"size mismatch: {offset} != {len(data)}")
    return fields, rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("tables", nargs="+")
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument("--contains", default="")
    parser.add_argument("--row-id", type=int, action="append", default=[])
    parser.add_argument("--fields", default="")
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
    )
    args = parser.parse_args()
    for table_name in args.tables:
        for path in args.root.rglob(table_name):
            if path.stat().st_size < 10:
                continue
            fields, rows = read_table(path)
            print(
                f"{table_name} | {path} | size={path.stat().st_size} "
                f"rows={len(rows)} cols={len(fields)}"
            )
            print("  " + ", ".join(f"{name}:{kind}" for name, kind in fields))
            selected = rows
            if args.row_id:
                selected = [
                    row for row in selected if int(row["RowId"]) in args.row_id
                ]
            if args.contains:
                needle = args.contains.casefold()
                selected = [
                    row
                    for row in rows
                    if needle in repr(row).casefold()
                ]
            for condition in args.where:
                field, separator, expected = condition.partition("=")
                if not separator:
                    raise ValueError(f"invalid --where condition: {condition}")
                selected = [
                    row
                    for row in selected
                    if str(row.get(field)) == expected
                ]
            field_filter = [
                value.strip() for value in args.fields.split(",") if value.strip()
            ]
            for row in selected[: max(0, args.rows)]:
                if field_filter:
                    row = {
                        key: row.get(key)
                        for key in ["RowId", *field_filter]
                    }
                print(f"  {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
