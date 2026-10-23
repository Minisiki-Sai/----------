from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "dungeon_map_catalog_v576.json"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "assets"
    / "movement_maps"
)


def _default_grid_roots() -> list[Path]:
    unpacked = Path(r"E:\ext0811.rar")
    roots = [
        unpacked / "ext0811" / f"Resource{index:02d}" / "mapdata" / "grid"
        for index in range(20, -1, -1)
    ]
    roots.append(unpacked / "Resource00" / "Resource00" / "mapdata" / "grid")
    return roots


def _grid_roots(unpacked_roots: list[Path]) -> list[Path]:
    if not unpacked_roots:
        return [path for path in _default_grid_roots() if path.is_dir()]
    found: list[Path] = []
    for root in unpacked_roots:
        candidates = [root]
        candidates.extend(
            sorted(root.glob("Resource*/mapdata/grid"), reverse=True)
        )
        candidates.extend(
            sorted(root.glob("ext0811/Resource*/mapdata/grid"), reverse=True)
        )
        candidates.extend(root.glob("Resource00/Resource00/mapdata/grid"))
        for candidate in candidates:
            if candidate.is_dir() and candidate not in found:
                found.append(candidate)
    return found


def _index_grid_directories(roots: list[Path]) -> dict[str, tuple[Path, Path]]:
    layered: dict[str, dict[str, Path]] = {}
    for root in roots:
        for directory in root.iterdir():
            if not directory.is_dir():
                continue
            row = layered.setdefault(directory.name.casefold(), {})
            dds_path = _source_dds(directory)
            gridinfo_path = directory / "gridinfo.ini"
            if dds_path is not None and "dds" not in row:
                row["dds"] = dds_path
            if gridinfo_path.is_file() and "gridinfo" not in row:
                row["gridinfo"] = gridinfo_path
    return {
        key: (row["dds"], row["gridinfo"])
        for key, row in layered.items()
        if "dds" in row and "gridinfo" in row
    }


def _read_grid_metadata(path: Path) -> tuple[float, float, float] | None:
    try:
        raw = path.read_bytes()
        chunk_x, chunk_y, cells_x, cells_y, cell_size = struct.unpack_from(
            "<5I", raw, 64
        )
    except (OSError, struct.error):
        return None
    if not all((chunk_x, chunk_y, cells_x, cells_y, cell_size)):
        return None
    return (
        float(chunk_x * cells_x * cell_size * 2),
        float(chunk_y * cells_y * cell_size * 2),
        float(cell_size),
    )


def _source_dds(directory: Path) -> Path | None:
    exact = directory / f"{directory.name}.dds"
    if exact.is_file():
        return exact
    candidates = sorted(directory.glob("*.dds"))
    return candidates[0] if candidates else None


def _asset_file_name(grid_directory: str, source: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", grid_directory.casefold()).strip("-")
    slug = (slug or "map")[:72]
    digest = hashlib.sha1(str(source).casefold().encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}.webp"


def build(catalog_path: Path, output: Path, roots: list[Path]) -> dict[str, object]:
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    maps = payload.get("maps", []) if isinstance(payload, dict) else []
    index = _index_grid_directories(roots)
    output.mkdir(parents=True, exist_ok=True)
    converted: dict[Path, dict[str, object]] = {}
    manifest_maps: dict[str, dict[str, object]] = {}
    missing: list[dict[str, object]] = []

    for row in maps:
        if not isinstance(row, dict):
            continue
        try:
            map_id = int(row.get("map_id") or 0)
        except (TypeError, ValueError):
            continue
        if map_id <= 0:
            continue
        selected_dds: Path | None = None
        selected_gridinfo: Path | None = None
        selected_grid = ""
        for grid_directory in row.get("grid_directories", ()):
            selected_grid = str(grid_directory).strip()
            candidate = index.get(selected_grid.casefold())
            if candidate is not None:
                selected_dds, selected_gridinfo = candidate
                break
        if selected_dds is None or selected_gridinfo is None:
            missing.append(
                {
                    "map_id": map_id,
                    "name": str(row.get("display_name") or ""),
                    "grid_directories": row.get("grid_directories", []),
                }
            )
            continue
        dds_path = selected_dds
        metadata = _read_grid_metadata(selected_gridinfo)
        if metadata is None:
            missing.append(
                {
                    "map_id": map_id,
                    "name": str(row.get("display_name") or ""),
                    "grid_directories": row.get("grid_directories", []),
                }
            )
            continue
        resolved_dds = dds_path.resolve()
        asset_row = converted.get(resolved_dds)
        if asset_row is None:
            with Image.open(dds_path) as source_image:
                image = source_image.convert("RGBA")
                file_name = _asset_file_name(selected_grid, resolved_dds)
                image.save(
                    output / file_name,
                    format="WEBP",
                    lossless=True,
                    method=6,
                    exact=True,
                )
                asset_row = {
                    "file": file_name,
                    "grid_directory": selected_grid,
                    "image_width": image.width,
                    "image_height": image.height,
                    "world_width": metadata[0],
                    "world_height": metadata[1],
                    "cell_size": metadata[2],
                }
            converted[resolved_dds] = asset_row
        manifest_maps[str(map_id)] = dict(asset_row)

    manifest = {
        "schema": "dragon-nest-movement-map-assets-v2",
        "catalog": catalog_path.name,
        "map_count": len(manifest_maps),
        "asset_count": len(converted),
        "maps": manifest_maps,
        "missing": missing,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build compact X/Y movement-map assets from unpacked DDS grids."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--unpacked-root",
        action="append",
        type=Path,
        default=[],
        help="Unpacked resource root or mapdata/grid directory (repeatable).",
    )
    args = parser.parse_args()
    roots = _grid_roots(args.unpacked_root)
    if not roots:
        raise SystemExit("No unpacked mapdata/grid directories were found.")
    manifest = build(args.catalog, args.output, roots)
    print(
        f"movement maps: {manifest['map_count']} ids, "
        f"{manifest['asset_count']} unique assets, "
        f"{len(manifest['missing'])} missing"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
