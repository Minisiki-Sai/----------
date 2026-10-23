"""Static-only audit of the built-in damage meter's SkillTable lookup.

This sidecar verifies decoded client instructions, parses an old extracted
Resource snapshot, and inventories the installed client's PAK directories.
It never executes or loads the client, reads process memory, decrypts a PAK,
or changes production files.  Outputs are confined to the fixed audit folder.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import html
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.inspect_installed_resources_v37 import index as index_pak
from tools.review_client_damage_types_v44 import BASE, CodeReview, StaticView
from tools.trace_projectile_config_v45 import parse_dnt


OUT = ROOT / "outputs/builtin_skilltable_mapping_v55_20260909"
RESOURCE = Path("E:/ext0811.rar/Resource00/Resource00/resource/ext")
UISTRING = Path("E:/ext0811.rar/Resource00/Resource00/resource/uistring/uistring0818.xml")
CLIENT_DIR = Path("D:/DragonNest/Reborn")
EXPECTED_CLIENT_SHA256 = "d2a09aa5ef8c73d1cfaeae95850ce282b0be6aae979b8aa4817348491e907d61"
EXPECTED_UISTRING_SHA256 = "fd513b0bc6545aa383456d566695dcaa03e66ab984f68336ebded1c60ac914ff"

IDENTITY_FIELDS = (
    "_NameID", "_StaticName", "_BaseSkillID", "_GlobalSkillGroup", "_NeedJob"
)

# Exact bytes are deliberately pinned.  A client change must fail this audit,
# not silently produce a mapping from a different implementation.
ANCHORS = (
    (0x6A5646, "418bb790000000", "damage result +0x90 is the initial row key"),
    (0x6A5693, "ff5030", "fallback object virtual +0x30 supplies a row key"),
    (0x6A639F, "ba0c000000", "registry type 12 selected in actor-aware branch"),
    (0x6A63AB, "e8c0a3caff", "actor-aware branch calls table selector 0x350770"),
    (0x6A63FE, "e80dc13a00", "optional player-state row-key remap 0xA52510"),
    (0x6A640B, "0f45d6", "successful remap conditionally replaces raw key"),
    (0x6A640E, "4c8d05cb6f7e00", "column literal is _NameID"),
    (0x6A6418, "e843f5b8ff", "actor-aware branch calls column getter 0x235960"),
    (0x6A6504, "83be9000000000", "direct branch checks damage result +0x90"),
    (0x6A6531, "ba0c000000", "direct branch also selects registry type 12"),
    (0x6A653D, "e82ea2caff", "direct branch calls table selector 0x350770"),
    (0x6A654E, "8b9690000000", "direct branch passes +0x90 as row key"),
    (0x6A6557, "e804f4b8ff", "direct branch calls column getter 0x235960"),
    (0x6A6564, "448b00", "returned cell payload is a 32-bit NameID"),
    (0x35AF53, "4c8d054ed5b300", "registry descriptor points at SkillTable"),
    (0x35AF62, "418d510c", "SkillTable registry key is 12"),
    (0x35AF66, "e8f544ffff", "descriptor is registered by 0x34F460"),
    (0x350791, "41395820", "registry tree node key is at +0x20"),
    (0x3507BA, "488b4828", "registry tree node table pointer is at +0x28"),
    (0x3507E7, "e804ebffff", "missing table is lazily loaded through 0x34F2F0"),
    (0x235966, "4c8b5170", "table row tree is at +0x70"),
    (0x235980, "39501c", "row-tree node key is at +0x1c"),
    (0x2359AE, "418b5920", "row-tree node base cell index is at +0x20"),
    (0x2359BA, "e841fdffff", "column name is resolved by 0x235700"),
    (0x2359CB, "488b9780000000", "cell vector begin is table +0x80"),
    (0x2359DC, "488b8788000000", "cell vector end is table +0x88"),
    (0x2359F5, "488d0c40", "cell index is multiplied by three"),
    (0x2359F9, "488d048a", "cell address uses a 12-byte stride"),
    (0x235859, "483b7760", "column-name tree is table +0x60"),
    (0x23585F, "448b6640", "column node index is at +0x40"),
    (0x234A8D, "41b804000000", "loader reads a four-byte row ID"),
    (0x234A9B, "ff5040", "stream virtual +0x40 performs the row-ID read"),
    (0x234C2F, "8955e0", "row ID is packed for row-tree insertion"),
    (0x234C32, "894de4", "base cell index is packed beside row ID"),
    (0x234C7C, "3b511c", "existing row ID is compared before insertion"),
    (0x234C7F, "7d5f", "equal IDs skip new-node insertion: first occurrence wins"),
    (0x234C9D, "b928000000", "row-tree node allocation size is 0x28"),
    (0x234CAC, "4889481c", "packed row ID/base index is stored at node +0x1c"),
    (0xA52518, "448bf2", "player remapper preserves input row key in r14d"),
    (0xA52710, "443930", "player remapper compares pair[0] to input row key"),
    (0xA52720, "448b6004", "player remapper returns pair[1] as replacement key"),
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def instruction_at(view: StaticView, code: CodeReview, rva: int) -> dict[str, object]:
    function = view.function(rva)
    if function is None:
        raise ValueError(f"no exception function for {rva:#x}")
    ins = next((item for item in code.decode(function) if item.address - BASE == rva), None)
    if ins is None:
        raise ValueError(f"instruction boundary not decoded at {rva:#x}")
    row = code.row(ins)
    row["instruction"] = f"{ins.mnemonic} {ins.op_str}".strip()
    row["function_begin_rva"] = function[0]
    row["function_end_rva"] = function[1]
    return row


def static_evidence() -> dict[str, object]:
    view = StaticView()
    if digest(Path("D:/DragonNest/Reborn/dragonnest_reborn.exe")) != EXPECTED_CLIENT_SHA256:
        raise ValueError("client hash changed")
    code = CodeReview(view)
    rows = []
    for rva, expected, conclusion in ANCHORS:
        row = instruction_at(view, code, rva)
        if row["bytes"] != expected:
            raise ValueError(f"instruction bytes changed at {rva:#x}: {row['bytes']}")
        row["conclusion"] = conclusion
        rows.append(row)
    literals = []
    for rva, value in ((0xE8D3E0, "_NameID"), (0xE984A8, "SkillTable")):
        raw = value.encode("ascii") + b"\0"
        if view.read(rva, len(raw)) != raw:
            raise ValueError(f"literal changed at {rva:#x}")
        literals.append({"rva": rva, "value": value, "bytes": raw.hex()})
    return {
        "client_sha256": EXPECTED_CLIENT_SHA256,
        "instructions": rows,
        "literals": literals,
        "proved_layout": {
            "registry_tree": "manager+0x08; node key +0x20; table pointer +0x28",
            "table": "column tree +0x60; row tree +0x70; cell begin/end +0x80/+0x88",
            "row_node": "row key +0x1c; base cell index +0x20; allocation size 0x28",
            "column_node": "name storage starts +0x20; zero-based column index +0x40",
            "cell": "12-byte variant; getter returns its address; NameID payload read as dword",
        },
    }


MESSAGE_RE = re.compile(
    r'<message\s+mid="(\d+)"><!\[CDATA\[(.*?)\]\]></message>', re.DOTALL
)
TAG_RE = re.compile(r"<[^>]+>")


def load_ui_strings() -> tuple[dict[int, str], dict[int, list[str]], dict[str, object]]:
    if digest(UISTRING) != EXPECTED_UISTRING_SHA256:
        raise ValueError("UIString snapshot hash changed")
    text = UISTRING.read_text(encoding="utf-8")
    values: dict[int, str] = {}
    duplicates: dict[int, list[str]] = defaultdict(list)
    count = 0
    for match in MESSAGE_RE.finditer(text):
        count += 1
        key = int(match.group(1))
        value = html.unescape(TAG_RE.sub("", match.group(2))).strip()
        if key in values:
            duplicates[key].append(value)
        else:
            values[key] = value
    if count != 234326:
        raise ValueError(f"unexpected UIString record count: {count}")
    return values, dict(duplicates), {
        "path": str(UISTRING), "sha256": EXPECTED_UISTRING_SHA256,
        "records": count, "unique_ids": len(values), "duplicate_ids": len(duplicates),
    }


def plain_cells(row: dict[str, object]) -> dict[str, object]:
    return {name: cell["value"] for name, cell in row["cells"].items()}


def different_fields(left: dict[str, object], right: dict[str, object]) -> dict[str, list[object]]:
    keys = sorted(set(left) | set(right))
    return {key: [left.get(key), right.get(key)] for key in keys if left.get(key) != right.get(key)}


def old_snapshot_mapping() -> tuple[dict[str, object], dict[str, object]]:
    ui, ui_duplicates, ui_meta = load_ui_strings()
    paths = sorted(RESOURCE.glob("skilltable*.dnt"), key=lambda p: p.name.lower())
    if len(paths) != 15:
        raise ValueError(f"expected 15 SkillTable shards, got {len(paths)}")
    occurrences: dict[int, list[dict[str, object]]] = defaultdict(list)
    files = []
    schema = None
    total_rows = 0
    for path in paths:
        raw = path.read_bytes()
        table = parse_dnt(raw)
        names = [field["name"] for field in table["fields"]]
        if schema is None:
            schema = names
        elif names != schema:
            raise ValueError(f"SkillTable schema mismatch: {path.name}")
        total_rows += table["row_count"]
        nonzero_name_ids = 0
        resolved_names = 0
        for ordinal, row in enumerate(table["rows"]):
            cells = plain_cells(row)
            raw_key = int(cells["RowId"])
            name_id = int(cells["_NameID"])
            display_name = ui.get(name_id, "") if name_id else ""
            nonzero_name_ids += bool(name_id)
            resolved_names += bool(display_name)
            occurrence = {
                "source": path.name.lower(),
                "ordinal": ordinal,
                "row_offset": row["offset"],
                "name_id": name_id,
                "display_name": display_name,
                "identity": {field: cells.get(field) for field in IDENTITY_FIELDS},
                "all_fields": cells,
            }
            occurrences[raw_key].append(occurrence)
        files.append({
            "name": path.name.lower(), "bytes": len(raw), "sha256": sha256(raw).hexdigest(),
            "rows": table["row_count"], "columns_excluding_row_id": len(table["fields"]),
            "nonzero_name_id_rows": nonzero_name_ids, "localized_name_rows": resolved_names,
            "duplicate_row_ids_within_file": table["duplicate_row_ids"],
        })

    mapping: dict[str, object] = {}
    duplicates = []
    full_row_conflicts = 0
    identity_conflicts = 0
    cross_shard_duplicates = 0
    for raw_key, rows in sorted(occurrences.items()):
        selected = rows[0]
        mapping[str(raw_key)] = {
            "row_id": raw_key,
            "name_id": selected["name_id"],
            "display_name": selected["display_name"],
            "static_name": selected["identity"]["_StaticName"],
            "base_skill_id": selected["identity"]["_BaseSkillID"],
            "global_skill_group": selected["identity"]["_GlobalSkillGroup"],
            "need_job": selected["identity"]["_NeedJob"],
            "source": selected["source"],
            "ordinal": selected["ordinal"],
            "row_offset": selected["row_offset"],
            "duplicate_count": len(rows),
        }
        if len(rows) <= 1:
            continue
        cross_shard_duplicates += len({row["source"] for row in rows}) > 1
        comparisons = []
        for row in rows[1:]:
            identity_diff = different_fields(selected["identity"], row["identity"])
            full_diff = different_fields(selected["all_fields"], row["all_fields"])
            identity_conflicts += bool(identity_diff)
            full_row_conflicts += bool(full_diff)
            comparisons.append({
                "source": row["source"], "ordinal": row["ordinal"],
                "identity_differences": identity_diff, "all_field_differences": full_diff,
            })
        duplicates.append({
            "row_id": raw_key,
            "selected_first": {key: value for key, value in selected.items() if key != "all_fields"},
            "other_occurrences": comparisons,
        })

    unique = len(occurrences)
    nonzero = sum(bool(row["name_id"]) for row in mapping.values())
    localized = sum(bool(row["display_name"]) for row in mapping.values())
    missing_localization = sorted(
        {row["name_id"] for row in mapping.values() if row["name_id"] and not row["display_name"]}
    )
    if (total_rows, unique, nonzero, localized) != (12444, 12442, 5359, 5355):
        raise ValueError((total_rows, unique, nonzero, localized))
    name_id_column = next(
        ((index, field) for index, field in enumerate(table["fields"]) if field["name"] == "_NameID"),
        None,
    )
    if name_id_column is None or name_id_column[0] != 0 or name_id_column[1]["kind"] != 3:
        raise ValueError(f"unexpected _NameID schema: {name_id_column}")
    if sorted(int(row["row_id"]) for row in duplicates) != [30633, 30634]:
        raise ValueError("unexpected duplicate SkillTable keys")
    sample = mapping.get("1353")
    if not sample or sample["name_id"] != 1000022271 or sample["display_name"] != "爆炸箭矢":
        raise ValueError(f"raw key 1353 sample mismatch: {sample}")
    summary = {
        "resource_root": str(RESOURCE),
        "table_shards": len(files),
        "columns_excluding_row_id": len(schema or []),
        "name_id_column": {
            "zero_based_declared_index": name_id_column[0],
            "dnt_kind": name_id_column[1]["kind"],
            "runtime_cell_offset_from_row_base": name_id_column[0] + 1,
            "interpretation": "signed 32-bit integer NameID; RowId occupies the implicit first cell",
        },
        "total_rows": total_rows,
        "unique_raw_keys": unique,
        "strictly_single_occurrence_keys": sum(len(rows) == 1 for rows in occurrences.values()),
        "duplicate_raw_keys": len(duplicates),
        "cross_shard_duplicate_keys": cross_shard_duplicates,
        "identity_conflict_comparisons": identity_conflicts,
        "full_row_conflict_comparisons": full_row_conflicts,
        "nonzero_name_id_keys": nonzero,
        "localized_name_keys": localized,
        "zero_name_id_keys": unique - nonzero,
        "nonzero_name_id_missing_localization_keys": nonzero - localized,
        "missing_localization_name_ids": missing_localization,
        "ui_strings": ui_meta,
        "ui_string_duplicate_ids": ui_duplicates,
        "files": files,
        "duplicates": duplicates,
        "sample_1353": sample,
    }
    return mapping, summary


def installed_pak_census(old_summary: dict[str, object]) -> dict[str, object]:
    old_by_name = {row["name"]: row for row in old_summary["files"]}
    entries = []
    archives = []
    for archive in sorted(CLIENT_DIR.glob("Resource[0-9][0-9].pak")):
        rows, meta = index_pak(archive)
        archives.append(meta)
        for row in rows:
            basename = Path(row["name"]).name.lower()
            if not basename.startswith("skilltable") or not basename.endswith(".dnt"):
                continue
            old = old_by_name.get(basename)
            entries.append({
                "archive": archive.name,
                **row,
                "old_snapshot_bytes": old["bytes"] if old else None,
                "declared_length_matches_old": bool(old and row["length"] == old["bytes"]),
                "plaintext_hash_verified": False,
            })
    basenames = [Path(row["name"]).name.lower() for row in entries]
    if len(entries) != 15 or set(basenames) != set(old_by_name):
        raise ValueError("installed SkillTable shard census changed")
    character = next(row for row in entries if Path(row["name"]).name.lower() == "skilltable_character.dnt")
    if character["length"] != 472652 or character["old_snapshot_bytes"] != 472902:
        raise ValueError("expected current/old character table length evidence changed")
    return {
        "client_resource_root": str(CLIENT_DIR),
        "archives_read": len(archives),
        "skilltable_entries": entries,
        "skilltable_entry_count": len(entries),
        "declared_length_matches_old_count": sum(row["declared_length_matches_old"] for row in entries),
        "plaintext_hash_verified_count": 0,
        "known_version_difference": {
            "name": "skilltable_character.dnt",
            "current_declared_bytes": character["length"],
            "old_snapshot_bytes": character["old_snapshot_bytes"],
            "delta": character["length"] - character["old_snapshot_bytes"],
        },
        "limitation": (
            "The selected installed PAK bodies remain wrapped/encrypted. Directory metadata is valid, "
            "but no current SkillTable plaintext hash is claimed."
        ),
    }


def markdown(report: dict[str, object]) -> str:
    old = report["old_snapshot"]
    current = report["installed_client_resources"]
    sample = old["sample_1353"]
    duplicate_lines = []
    for item in old["duplicates"]:
        diffs = item["other_occurrences"][0]["all_field_differences"]
        rendered = "、".join(f"`{key}` {values[0]}→{values[1]}" for key, values in diffs.items())
        duplicate_lines.append(
            f"|{item['row_id']}|{item['selected_first']['display_name']}|同一分表重复；{rendered}|"
        )
    key_instructions = {
        0x6A5646, 0x6A63AB, 0x6A63FE, 0x6A640B, 0x6A6418,
        0x6A6504, 0x6A653D, 0x6A654E, 0x6A6557, 0x6A6564,
        0x35AF53, 0x35AF62, 0x35AF66, 0x350791, 0x3507BA,
        0x235966, 0x235980, 0x2359AE, 0x2359BA, 0x2359CB,
        0x2359DC, 0x2359F5, 0x2359F9, 0x235859, 0x23585F,
        0x234A8D, 0x234A9B, 0x234C7C, 0x234C7F, 0x234CAC,
        0xA52518, 0xA52710, 0xA52720,
    }
    rows = [row for row in report["static_evidence"]["instructions"] if row["rva"] in key_instructions]
    lines = [
        "# 客户端内置统计器 SkillTable 映射审计 v55",
        "",
        "## 结论",
        "",
        "`damage result +0x90` 不是需要再猜一层的临时索引：在内置统计器的直接分支中，它就是 registry type 12（`SkillTable`）的行键。统计器以该键调用 `0x235960(table, row_key, \"_NameID\")`，读取返回单元的 32 位值作为最终 `_NameID`。因此离线映射的主来源是全部 `SkillTable*.dnt` 的隐式 `RowId` 列，而不是 ACT 自建技能字典。",
        "",
        "存在一个必须单独保留的演员相关分支：玩家对象满足条件时，`0xA52510` 可把输入键替换为运行时二元组中的第二个键，再查同一张 SkillTable。静态证据证明了它是 `pair[0] → pair[1]` 替换，但尚未证明这些二元组的业务名称和完整构造来源。",
        "",
        "## 地址级证据",
        "",
        "|RVA|指令|结论|",
        "|---:|---|---|",
    ]
    lines.extend(
        f"|`0x{row['rva']:X}`|`{row['instruction']}`|{row['conclusion']}|" for row in rows
    )
    lines += [
        "",
        "已同时校验只读字面量：`0xE8D3E0 = _NameID`，`0xE984A8 = SkillTable`。完整锚点及机器码见 `static_evidence.json`。",
        "",
        "## 真实表结构",
        "",
        "|对象|静态确认布局|",
        "|---|---|",
        "|registry manager|已加载表树在 `+0x08`；节点 key 在 `+0x20`，表指针在 `+0x28`|",
        "|SkillTable|列名树 `+0x60`；行树 `+0x70`；cell vector begin/end 为 `+0x80/+0x88`|",
        "|row node|`+0x1C` 为 32 位 RowId，`+0x20` 为 base-cell index，节点大小 `0x28`|",
        "|column node|字符串对象从 `+0x20` 开始，零基列序号在 `+0x40`|",
        "|cell|步长 12 字节；本路径把返回 cell 的首 4 字节读作 NameID|",
        "",
        "15 个分表具有同一套 62 列声明；`_NameID` 是声明列 0、DNT kind 3（32 位整数）。`0x235960` 计算 `base_cell_index + column_index + 1`，其中 `+1` 跳过每行隐式的 `RowId` 单元，因此 `_NameID` 正好是 RowId 后的第一个数据单元。",
        "",
        "加载器先登记 `type 12 → SkillTable`，随后按 `SkillTable*.dnt` 通配符合并分表。重复 RowId 的插入路径为“已存在则跳过”，所以运行时采用首次出现记录。",
        "",
        "## 离线映射结果",
        "",
        f"旧 Resource 快照包含 {old['table_shards']} 个分表、{old['total_rows']:,} 行、{old['unique_raw_keys']:,} 个不同 raw key。",
        "",
        f"- {old['strictly_single_occurrence_keys']:,} 个键只出现一次。",
        f"- {old['nonzero_name_id_keys']:,} 个键有非零 `_NameID`；其中 {old['localized_name_keys']:,} 个能在该快照 UIString 中解析出名称。",
        f"- {old['zero_name_id_keys']:,} 个键的 `_NameID=0`，无法仅靠该列得到显示名。",
        f"- {old['nonzero_name_id_missing_localization_keys']} 个非零 NameID 在该 UIString 快照中缺失：`{', '.join(map(str, old['missing_localization_name_ids']))}`。",
        f"- 示例唯一映射：`1353 → RowId {sample['row_id']} → NameID {sample['name_id']} → {sample['display_name']}`；StaticName=`{sample['static_name']}`，来源 `{sample['source']}` 第 {sample['ordinal']} 行（零基）、文件偏移 `0x{sample['row_offset']:X}`。",
        "",
        "这里没有另一个被统计器读取的“最终 SkillID”列：raw key 本身就是 SkillTable RowId；`_BaseSkillID` 与 `_GlobalSkillGroup` 虽在表中，但这条显示路径没有访问它们。",
        "",
        "### 重复键与冲突",
        "",
        "|RowId|名称|差异|",
        "|---:|---|---|",
        *duplicate_lines,
        "",
        f"只有 {old['duplicate_raw_keys']} 个重复 RowId，均位于同一 character 分表；它们的 NameID/名称相同，因此 raw key→NameID/名称没有冲突，但完整技能行有差异。跨分表重复键为 {old['cross_shard_duplicate_keys']}。",
        "",
        "## 与当前安装客户端的版本关系",
        "",
        f"当前 PAK 目录能定位到同名的 {current['skilltable_entry_count']} 个 SkillTable 分表，但当前封装正文尚未得到可校验明文，当前版本内容哈希验证数为 {current['plaintext_hash_verified_count']}。",
        f"已确认 `skilltable_character.dnt` 当前声明长度为 {current['known_version_difference']['current_declared_bytes']:,} 字节，旧快照为 {current['known_version_difference']['old_snapshot_bytes']:,} 字节，差 {current['known_version_difference']['delta']:,} 字节。这足以证明至少该分表不是同一内容版本。",
        "",
        "## 能否用于生产",
        "",
        "映射机制可以用于生产；本次旧快照生成的具体映射不能直接作为当前客户端的权威全集。推荐生产实现为：",
        "",
        "1. 从与客户端版本一致的 15 个 `SkillTable*.dnt` 离线生成 `RowId → NameID/名称`，并绑定资源清单哈希。",
        "2. 运行时对 `+0x90` 做精确 RowId 查找；未知键保持未知，不跨版本猜测。",
        "3. 复现或观测 `0xA52510` 的玩家状态 remap 后再查表，否则无法与内置统计器在该分支完全一致。",
        "4. `_NameID=0` 的 7,083 个键需其他语义表或上下文，不能伪造名称。",
        "",
        "当前可以安全用于验证解析器结构、回归样例和旧资源版本；在取得当前 PAK 的严格明文或同版本 Resource 导出前，不应把这 12,442 项直接合入生产。",
        "",
        "## 边界",
        "",
        "全程只读取客户端文件、已解码代码段、PAK 目录和旧 Resource 快照；未执行、加载、注入或修改游戏，也未修改任何生产文件。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    evidence = static_evidence()
    mapping, old = old_snapshot_mapping()
    current = installed_pak_census(old)
    production_ready = {
        "mapping_mechanism": True,
        "old_snapshot_as_current_authoritative_map": False,
        "blocking_reasons": [
            "installed SkillTable plaintext hashes are not verified",
            "skilltable_character.dnt declared length differs by 250 bytes",
            "actor-specific 0xA52510 pair-map construction remains unnamed/unmirrored",
            "7083 keys have _NameID=0 in the audited snapshot",
        ],
    }
    report = {
        "schema": "builtin-skilltable-mapping-v55",
        "method": "hash-pinned static bytes + strict offline DNT/UIString parsing + PAK directory census",
        "static_evidence": evidence,
        "old_snapshot": old,
        "installed_client_resources": current,
        "production_readiness": production_ready,
        "production_files_changed": False,
        "client_executed_or_loaded": False,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "skilltable_mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "static_evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "REPORT.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({
        "output": str(OUT),
        "total_rows": old["total_rows"],
        "unique_raw_keys": old["unique_raw_keys"],
        "localized_names": old["localized_name_keys"],
        "duplicate_raw_keys": old["duplicate_raw_keys"],
        "sample_1353": old["sample_1353"],
        "current_plaintext_verified": current["plaintext_hash_verified_count"],
        "production_ready_current_map": production_ready["old_snapshot_as_current_authoritative_map"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
