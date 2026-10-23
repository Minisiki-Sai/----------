"""Extract monster-card portraits from the unpacked Dragon Nest resources.

The card table stores a stable icon index for every monster card.  The icon
itself is a 50 x 50 cell in ``ui/mainbar/itemicon700.dds``; rarity frames are
drawn separately by the game UI and therefore are intentionally not baked
into the exported PNG files.

This script reads the simulator's name-keyed map so adding a card to the
catalog cannot shift the images that follow it.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESOURCE_ROOT = Path(
    os.environ.get(
        "DRAGON_NEST_RESOURCE_ROOT",
        r"E:\ext0811.rar\Resource00\Resource00\resource",
    )
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "frontend" / "public" / "card-icons"
CARD_ICON_MAP = PROJECT_ROOT / "frontend" / "src" / "data" / "cardIconMap.ts"

CARD_ATLAS_NAME = "itemicon700.dds"
CARD_ICON_BASE = 139800
CELL_SIZE = 50
ATLAS_COLUMNS = 10


def parse_dnt(path: Path) -> list[dict[str, object]]:
    data = path.read_bytes()
    offset = 4
    column_count = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    row_count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    fields: list[tuple[str, int]] = []

    for _ in range(column_count):
        name_length = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        name = data[offset : offset + name_length].decode("ascii")
        offset += name_length
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
                value_length = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                value: object = data[offset : offset + value_length].decode(
                    "utf-8", errors="replace"
                )
                offset += value_length
            elif field_type in (2, 3, 4):
                value = struct.unpack_from("<i", data, offset)[0]
                offset += 4
            elif field_type == 5:
                value = struct.unpack_from("<f", data, offset)[0]
                offset += 4
            else:
                raise ValueError(f"Unknown DNT field type {field_type}: {name}")
            row[name] = value
        rows.append(row)
    return rows


def load_uistring(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        int(match.group(1)): match.group(2).replace("\\n", "\n")
        for match in re.finditer(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>',
            text,
            re.S,
        )
    }


def load_frontend_icon_map(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    pairs = re.findall(r'^\s*"([^"]+)"\s*:\s*"([^"]+\.png)"', text, re.M)
    if not pairs:
        raise ValueError(f"No card icon entries found in {path}")
    return dict(pairs)


def card_name(row: dict[str, object], ui: dict[int, str]) -> str:
    # Every active monster-card row uses 怪物卡片-{0}; the first name
    # parameter is the stable localized monster name used by the simulator.
    ids = re.findall(r"\{(\d+)\}", str(row.get("_NameIDParam") or ""))
    return ui.get(int(ids[0]), "").strip() if ids else ""


def build_card_icons(resource_root: Path, output_dir: Path) -> list[dict[str, object]]:
    table_path = resource_root / "ext" / "itemtable_card.dnt"
    string_path = resource_root / "uistring" / "uistring0818.xml"
    atlas_path = resource_root / "ui" / "mainbar" / CARD_ATLAS_NAME
    for path in (table_path, string_path, atlas_path, CARD_ICON_MAP):
        if not path.is_file():
            raise FileNotFoundError(path)

    ui = load_uistring(string_path)
    frontend_icons = load_frontend_icon_map(CARD_ICON_MAP)
    card_rows = parse_dnt(table_path)

    icon_by_name: dict[str, int] = {}
    ranks_by_name: dict[str, set[int]] = {}
    for row in card_rows:
        if int(row.get("_Type") or 0) != 312 or int(row.get("_isUse") or 0) != 1:
            continue
        name = card_name(row, ui)
        if not name:
            continue
        icon_index = int(row.get("_IconImageIndex") or 0)
        previous = icon_by_name.setdefault(name, icon_index)
        if previous != icon_index:
            raise ValueError(
                f"Card {name!r} has conflicting icon indexes: {previous}, {icon_index}"
            )
        ranks_by_name.setdefault(name, set()).add(int(row.get("_Rank") or 0))

    missing = sorted(set(frontend_icons) - set(icon_by_name))
    if missing:
        raise ValueError("Cards missing from itemtable_card.dnt: " + ", ".join(missing))

    atlas = Image.open(atlas_path).convert("RGBA")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []

    for name, filename in frontend_icons.items():
        icon_index = icon_by_name[name]
        cell_index = icon_index - CARD_ICON_BASE
        if cell_index < 0:
            raise ValueError(f"Card {name!r} has invalid icon index {icon_index}")
        column = cell_index % ATLAS_COLUMNS
        row = cell_index // ATLAS_COLUMNS
        left = column * CELL_SIZE
        top = row * CELL_SIZE
        box = (left, top, left + CELL_SIZE, top + CELL_SIZE)
        if box[2] > atlas.width or box[3] > atlas.height:
            raise ValueError(
                f"Card {name!r} icon {icon_index} falls outside {atlas.size}"
            )
        portrait = atlas.crop(box)
        if portrait.getbbox() is None:
            raise ValueError(f"Card {name!r} icon {icon_index} is empty")
        portrait.save(output_dir / filename, "PNG", optimize=True)
        manifest.append(
            {
                "name": name,
                "file": filename,
                "icon_index": icon_index,
                "atlas_cell": cell_index,
                "ranks": sorted(ranks_by_name.get(name, set())),
            }
        )

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource-root",
        type=Path,
        default=DEFAULT_RESOURCE_ROOT,
        help="Unpacked resource directory containing ext/, ui/ and uistring/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Destination for generated card PNG files.",
    )
    args = parser.parse_args()
    manifest = build_card_icons(args.resource_root, args.output_dir)
    unique_icons = len({int(entry["icon_index"]) for entry in manifest})
    incomplete = [entry["name"] for entry in manifest if entry["ranks"] != [1, 2, 3, 4, 5]]
    if incomplete:
        raise ValueError("Cards missing one or more C/B/A/S/L rows: " + ", ".join(incomplete))
    print(
        f"Extracted {len(manifest)} named card images "
        f"({unique_icons} unique portraits) to {args.output_dir}"
    )


if __name__ == "__main__":
    main()
