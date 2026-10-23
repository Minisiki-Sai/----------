from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import struct
from collections import defaultdict
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNPACK_ROOT = Path(
    os.environ.get(
        "DN_UNPACK_ROOT",
        r"E:\ext0811.rar\Resource00\Resource00",
    )
)
DEFAULT_JSON_OUTPUT = (
    PROJECT_ROOT
    / "dragon_nest_builder"
    / "data"
    / "live_dps_profiles"
    / "dungeon_map_catalog_v576.json"
)
DEFAULT_CSV_OUTPUT = (
    PROJECT_ROOT / "docs" / "dungeon_map_catalog_named_v576.csv"
)
DEFAULT_MARKDOWN_OUTPUT = PROJECT_ROOT / "docs" / "dungeon_map_catalog_v576.md"


MAP_SUBTYPE_LABELS = {
    0: "普通副本",
    1: "巢穴",
    4: "活动副本",
    5: "活动区域",
    6: "混沌细缝",
    7: "黑暗关卡",
    8: "巢穴练习/英雄模式",
    10: "地狱副本",
    11: "龙之远征队",
    12: "防御战",
    13: "修炼场",
    17: "无限战场",
    21: "龙渊幽薮",
    100: "竞技场",
}


def parse_dnt(path: Path) -> list[dict[str, object]]:
    """Read the DNT layout used by the V576 Chinese client.

    Type 6 is an eight-byte integer in the current Chinese client tables.  A
    number of older local tools only understood field types 1-5 and silently
    could not read newer tables such as ``stageentertable.dnt``.
    """

    data = path.read_bytes()
    offset = 4
    if len(data) < 10:
        raise ValueError(f"DNT header is truncated: {path}")
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
                value = data[offset : offset + value_length].decode(
                    "utf-8", errors="replace"
                )
                offset += value_length
            elif field_type in (2, 3):
                value = struct.unpack_from("<i", data, offset)[0]
                offset += 4
            elif field_type in (4, 5):
                value = struct.unpack_from("<f", data, offset)[0]
                offset += 4
            elif field_type == 6:
                value = struct.unpack_from("<Q", data, offset)[0]
                offset += 8
            else:
                raise ValueError(
                    f"Unknown DNT field type {field_type}: {path.name}/{name}"
                )
            row[name] = value
        rows.append(row)
    # Some client revisions align DNT payloads with a short zero-filled tail.
    # Treat only all-zero trailing bytes as padding; nonzero leftovers still
    # indicate a schema/layout mismatch and must fail loudly.
    trailing = data[offset:]
    if trailing and any(trailing) and trailing != b"\x05THEND":
        raise ValueError(
            f"DNT length mismatch for {path}: parsed={offset}, actual={len(data)}"
        )
    return rows


def _split_resource_files(unpack_root: Path, relative_path: str) -> list[Path]:
    """Return non-empty files from a ResourceNN-style unpack directory."""

    return [
        path
        for path in unpack_root.glob(f"Resource*/{relative_path}")
        if path.is_file() and path.stat().st_size > 0
    ]


def find_resource_file(unpack_root: Path, relative_path: str) -> Path:
    """Resolve a resource from either a merged or split PAK extraction."""

    merged = unpack_root / relative_path
    if merged.is_file() and merged.stat().st_size > 0:
        return merged
    candidates = sorted(
        _split_resource_files(unpack_root, relative_path),
        key=lambda path: (path.stat().st_mtime_ns, path.stat().st_size, path.name),
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"No extracted resource {relative_path!r} under {unpack_root}"
        )
    return candidates[0]


def find_uistring(unpack_root: Path) -> Path:
    directories = [unpack_root / "resource" / "uistring"]
    directories.extend(
        path.parent
        for path in _split_resource_files(
            unpack_root, "resource/uistring/uistring*.xml"
        )
    )
    preferred = [directory / "uistring0818.xml" for directory in directories]
    preferred.extend(directory / "uistring.xml" for directory in directories)
    for path in preferred:
        if path.is_file() and path.stat().st_size > 0:
            return path
    candidates = sorted(
        {
            path
            for directory in directories
            for path in directory.glob("uistring*.xml")
            if path.is_file() and path.stat().st_size > 0
        },
        key=lambda path: (path.stat().st_mtime_ns, path.stat().st_size, path.name),
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No uistring XML found under {directory}")
    return candidates[0]


def load_uistring(path: Path) -> dict[int, str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return {
        int(match.group(1)): match.group(2).replace("\\n", "\n").strip()
        for match in re.finditer(
            r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>',
            text,
            re.S,
        )
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nonempty_tool_names(row: dict[str, object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for index in range(1, 11):
        value = str(row.get(f"_ToolName{index}") or "").strip()
        normalized = value.casefold()
        if value and normalized not in seen:
            result.append(value)
            seen.add(normalized)
    return result


def content_kind(map_type: int, subtype: int, allow_map_type: int) -> str:
    if map_type == 1:
        return "town"
    if map_type == 2:
        return "world_zone"
    if subtype in (1, 8):
        return "nest"
    if subtype in (17, 100) or allow_map_type == 8:
        return "pvp"
    return "dungeon"


def category_label(map_type: int, subtype: int, allow_map_type: int) -> str:
    if map_type == 1:
        return "城镇/大厅"
    if map_type == 2:
        return "世界区域/副本入口"
    if subtype in (17, 100) or allow_map_type == 8:
        return "竞技场/战场"
    return MAP_SUBTYPE_LABELS.get(
        subtype, f"其他战斗地图（子类型 {subtype}）"
    )


def build_stage_set_index(
    rows: Iterable[dict[str, object]], strings: dict[int, str]
) -> dict[int, list[dict[str, object]]]:
    result: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        stage_set = {
            "stage_set_id": int(row["RowId"]),
            "title": strings.get(int(row.get("_Title") or 0), "").strip(),
            "stage_set_type": int(row.get("_StageSetType") or 0),
        }
        for index in range(1, 6):
            map_id = int(row.get(f"_MapIndex{index}") or 0)
            if map_id > 0:
                result[map_id].append(stage_set)
    return result


def build_catalog(unpack_root: Path) -> dict[str, object]:
    map_table = find_resource_file(
        unpack_root, "resource/ext/maptable.dnt"
    )
    stage_set_table = find_resource_file(
        unpack_root, "resource/ext/stagesettable.dnt"
    )
    ui_path = find_uistring(unpack_root)
    strings = load_uistring(ui_path)
    stage_sets = build_stage_set_index(parse_dnt(stage_set_table), strings)

    grid_roots = [unpack_root / "mapdata" / "grid"]
    grid_roots.extend(unpack_root.glob("Resource*/mapdata/grid"))
    grid_names = {
        child.name.casefold(): child.name
        for grid_root in grid_roots
        if grid_root.is_dir()
        for child in grid_root.iterdir()
        if child.is_dir()
    }
    records: list[dict[str, object]] = []
    supported_rows = [
        row
        for row in parse_dnt(map_table)
        if int(row.get("_MapType") or 0) in {1, 2, 3}
    ]
    unnamed_map_ids: list[int] = []
    unnamed_map_ids_by_type: dict[int, list[int]] = {1: [], 2: [], 3: []}
    for row in supported_rows:
        map_type = int(row.get("_MapType") or 0)
        map_id = int(row["RowId"])
        subtype = int(row.get("_MapSubType") or 0)
        allow_map_type = int(row.get("_AllowMapType") or 0)
        name_string_id = int(row.get("_MapNameID") or 0)
        official_name = strings.get(name_string_id, "").strip()
        # Internal/test rows with MapNameID=0 do not have a player-facing
        # dungeon name. Keep them with a technical RowId label instead of
        # guessing a localized name from the editor resource directory.
        if not official_name:
            unnamed_map_ids.append(map_id)
            unnamed_map_ids_by_type.setdefault(map_type, []).append(map_id)
            pass
        tool_names = nonempty_tool_names(row)
        resolved_grid_names = [
            grid_names[name.casefold()]
            for name in tool_names
            if name.casefold() in grid_names
        ]
        display_name = official_name or f"地图 {map_id}（未命名）"
        name_source = "uistring" if official_name else "technical_map_id"
        records.append(
            {
                "map_id": map_id,
                "official_name": official_name,
                "display_name": display_name,
                "name_string_id": name_string_id,
                "name_source": name_source,
                "map_type": map_type,
                "map_subtype": subtype,
                "category": category_label(map_type, subtype, allow_map_type),
                "content_kind": content_kind(
                    map_type, subtype, allow_map_type
                ),
                # Preserve the raw DNT flag. It is not an availability flag:
                # Silver Crescent map 882 has value 0 but is entered from map
                # 881 in current captures and its grid resource is installed.
                "include_build_flag": bool(int(row.get("_IncludeBuild") or 0)),
                "enter_condition_table_id": int(
                    row.get("_EnterConditionTableID") or 0
                ),
                "allow_map_type": allow_map_type,
                "map_sid": int(row.get("_MapSid") or 0),
                "gate_map_ids": sorted(
                    {
                        int(row.get(f"_Gate{index}_MapIndex") or 0)
                        for index in range(1, 16)
                        if int(row.get(f"_Gate{index}_MapIndex") or 0) > 0
                    }
                ),
                "tool_names": tool_names,
                "grid_directories": resolved_grid_names,
                "grid_resource_found": bool(resolved_grid_names),
                "stage_sets": stage_sets.get(map_id, []),
            }
        )
    records.sort(key=lambda row: int(row["map_id"]))

    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    for record in records:
        # Unnamed internal maps must not be merged merely because their
        # official names are both empty.
        grouping_name = str(record["official_name"] or record["display_name"])
        key = (
            str(record["content_kind"]),
            str(record["category"]),
            grouping_name,
        )
        group = grouped.setdefault(
            key,
            {
                "official_name": str(record["official_name"]),
                "display_name": str(record["display_name"]),
                "category": str(record["category"]),
                "content_kind": str(record["content_kind"]),
                "map_subtypes": set(),
                "include_build_true_map_ids": [],
                "include_build_false_map_ids": [],
                "tool_names": set(),
            },
        )
        group["map_subtypes"].add(int(record["map_subtype"]))
        destination = (
            group["include_build_true_map_ids"]
            if record["include_build_flag"]
            else group["include_build_false_map_ids"]
        )
        destination.append(int(record["map_id"]))
        group["tool_names"].update(str(value) for value in record["tool_names"])

    groups: list[dict[str, object]] = []
    for group in grouped.values():
        include_true_ids = sorted(group["include_build_true_map_ids"])
        include_false_ids = sorted(group["include_build_false_map_ids"])
        groups.append(
            {
                **group,
                "map_subtypes": sorted(group["map_subtypes"]),
                "include_build_true_map_ids": include_true_ids,
                "include_build_false_map_ids": include_false_ids,
                "all_map_ids": sorted(include_true_ids + include_false_ids),
                "tool_names": sorted(group["tool_names"], key=str.casefold),
            }
        )
    groups.sort(
        key=lambda row: (
            str(row["content_kind"]),
            str(row["category"]),
            str(row["display_name"]),
        )
    )

    battle_records = [row for row in records if row["map_type"] == 3]
    include_true_records = [
        row for row in battle_records if row["include_build_flag"]
    ]
    return {
        "schema_version": 2,
        "game_protocol": "v576",
        "source": {
            "map_table": str(map_table),
            "map_table_sha256": sha256(map_table),
            "stage_set_table": str(stage_set_table),
            "stage_set_table_sha256": sha256(stage_set_table),
            "uistring": str(ui_path),
            "uistring_sha256": sha256(ui_path),
        },
        "statistics": {
            "named_map_records": sum(
                bool(str(row["official_name"] or "").strip())
                for row in records
            ),
            "all_map_records": len(records),
            "battle_map_records": len(battle_records),
            "town_map_records": sum(
                row["map_type"] == 1 for row in records
            ),
            "world_zone_map_records": sum(
                row["map_type"] == 2 for row in records
            ),
            "include_build_true_records": len(include_true_records),
            "include_build_false_records": (
                len(battle_records) - len(include_true_records)
            ),
            "include_build_true_official_names": len(
                {
                    str(row["official_name"])
                    for row in include_true_records
                    if row["official_name"]
                }
            ),
            "all_official_names": len(
                {
                    str(row["official_name"])
                    for row in battle_records
                    if row["official_name"]
                }
            ),
            "missing_name_records": len(unnamed_map_ids),
            "missing_name_map_ids": sorted(unnamed_map_ids),
            "missing_name_map_ids_by_type": {
                str(map_type): sorted(map_ids)
                for map_type, map_ids in unnamed_map_ids_by_type.items()
            },
        },
        "maps": records,
        "groups": groups,
    }


def write_csv(catalog: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "地图ID",
                "正式名称",
                "显示名称",
                "分类",
                "内容类型",
                "地图类型",
                "IncludeBuild原始标记",
                "地图子类型",
                "进入条件表ID",
                "Gate连接地图ID",
                "允许地图类型",
                "资源目录",
                "StageSet",
                "名称字符串ID",
            )
        )
        for row in catalog["maps"]:
            writer.writerow(
                (
                    row["map_id"],
                    row["official_name"],
                    row["display_name"],
                    row["category"],
                    row["content_kind"],
                    row["map_type"],
                    1 if row["include_build_flag"] else 0,
                    row["map_subtype"],
                    row["enter_condition_table_id"],
                    "; ".join(str(value) for value in row["gate_map_ids"]),
                    row["allow_map_type"],
                    "; ".join(row["tool_names"]),
                    "; ".join(
                        f"{item['stage_set_id']}:{item['title']}"
                        for item in row["stage_sets"]
                    ),
                    row["name_string_id"],
                )
            )


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_group_table(
    lines: list[str], groups: list[dict[str, object]]
) -> None:
    lines.extend(
        [
            "| 分类 | 正式副本/地图名称 | 全部地图 ID | IncludeBuild=1 | IncludeBuild=0 | 资源目录 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in groups:
        lines.append(
            "| "
            + " | ".join(
                (
                    markdown_cell(row["category"]),
                    markdown_cell(row["display_name"]),
                    ", ".join(str(value) for value in row["all_map_ids"]),
                    ", ".join(
                        str(value)
                        for value in row["include_build_true_map_ids"]
                    ),
                    ", ".join(
                        str(value)
                        for value in row["include_build_false_map_ids"]
                    ),
                    markdown_cell(", ".join(row["tool_names"])),
                )
            )
            + " |"
        )


def write_markdown(catalog: dict[str, object], output: Path) -> None:
    stats = catalog["statistics"]
    groups = catalog["groups"]
    lines = [
        "# V576 地图 ID 完整目录",
        "",
        "数据直接来自拆包 `resource/ext/maptable.dnt`，正式中文名称来自 "
        "`resource/uistring/uistring0818.xml`。`地图 ID` 即 `maptable.dnt` "
        "每行的 `RowId`。同一正式名称的多阶段地图已合并到同一行。",
        "",
        f"- 拆包中的全部 MapType=1/2/3 地图记录：{stats['all_map_records']}",
        f"- 其中有正式名称的地图记录：{stats['named_map_records']}",
        f"- 战斗地图记录：{stats['battle_map_records']}",
        f"- 城镇/大厅记录：{stats['town_map_records']}",
        f"- 世界区域/副本入口记录：{stats['world_zone_map_records']}",
        f"- 无正式名称、使用技术标签保留的记录：{stats['missing_name_records']}",
        f"- `IncludeBuild=1`：{stats['include_build_true_records']}",
        f"- `IncludeBuild=0`：{stats['include_build_false_records']}",
        f"- `IncludeBuild=1` 的正式名称：{stats['include_build_true_official_names']}",
        f"- 全部正式名称：{stats['all_official_names']}",
        "",
        "## 完整地图目录",
        "",
    ]
    write_group_table(lines, groups)
    lines.extend(
        [
            "",
            "## 拆包中没有正式名称的地图 ID",
            "",
            "这些行的 `MapNameID=0`，客户端没有可展示名称；目录使用“地图 ID（未命名）”技术标签，仅用于稳定识别和分层，不代表官方名称。",
            "",
            "- 城镇/大厅（MapType=1）："
            + ", ".join(
                str(value)
                for value in stats["missing_name_map_ids_by_type"]["1"]
            ),
            "- 世界区域/副本入口（MapType=2）："
            + ", ".join(
                str(value)
                for value in stats["missing_name_map_ids_by_type"]["2"]
            ),
            "- 战斗/副本地图（MapType=3）："
            + ", ".join(
                str(value)
                for value in stats["missing_name_map_ids_by_type"]["3"]
            ),
            "",
            "## 说明",
            "",
            "- `IncludeBuild` 仅原样保留拆包字段，不把 0 解释成停用。当前实测银色新月地图 882 的该值为 0，但它仍会作为 881 的后续阶段进入。",
            "- `MapNameID=0` 的内部/测试地图没有玩家可见的正式名称，因此只统计数量和 ID，不写入地图目录。",
            "- 竞技场和无限战场也属于 `MapType=3`，为保证目录完整而单独分类保留。",
            "- CSV 文件逐地图列出全部字段；JSON 文件供程序按地图 ID 直接查询。",
            "",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the V576 dungeon/nest map ID catalog from unpacked DNT data."
    )
    parser.add_argument("--unpack-root", type=Path, default=DEFAULT_UNPACK_ROOT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV_OUTPUT)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    catalog = build_catalog(args.unpack_root)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(catalog, args.csv_output)
    write_markdown(catalog, args.markdown_output)
    stats = catalog["statistics"]
    print(
        "wrote "
        f"{stats['all_map_records']} map records "
        f"{stats['battle_map_records']} battle-map records "
        f"(IncludeBuild=1: {stats['include_build_true_records']}, "
        f"IncludeBuild=0: {stats['include_build_false_records']})"
    )
    print(args.json_output)
    print(args.csv_output)
    print(args.markdown_output)


if __name__ == "__main__":
    main()
