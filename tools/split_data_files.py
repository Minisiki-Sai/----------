from __future__ import annotations

import json
import shutil
from collections import OrderedDict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "dragon_nest_builder" / "data" / "dragon_nest_data.json"
OUT_DIR = ROOT / "dragon_nest_builder" / "data" / "modular"


def norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def clean_number(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def add_attr(target: dict[str, Any], attr: Any, value: Any) -> None:
    name = norm(attr)
    if not name or value in (None, ""):
        return
    target[name] = clean_number(value)


def safe_name(value: Any) -> str:
    text = norm(value) or "未分类"
    for char in '<>:"/\\|?*':
        text = text.replace(char, "_")
    return text


def nested(root: dict[str, Any], *keys: Any) -> dict[str, Any]:
    current = root
    for key in keys:
        text = norm(key)
        current = current.setdefault(text, OrderedDict())
    return current


def dump_json(relative_path: str, value: Any) -> None:
    path = OUT_DIR / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def compact_equipment_base(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        item = nested(out, row.get("职业"), row.get("等级")).setdefault(norm(row.get("装备名称")), OrderedDict())
        item.setdefault("部位", row.get("部位"))
        item.setdefault("套装ID", row.get("套装ID") or 0)
        attrs = item.setdefault("属性", OrderedDict())
        add_attr(attrs, row.get("属性名称"), row.get("属性数值"))
    return out


def compact_equipment_enhance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, row.get("职业"), row.get("等级"), row.get("装备名称"), row.get("强化等级"))
        add_attr(attrs, row.get("属性名称"), row.get("属性数值"))
    return out


def compact_equipment_suffix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, row.get("职业"), row.get("等级"), row.get("装备名称"), row.get("后缀名称"))
        add_attr(attrs, row.get("属性名称"), row.get("属性数值"))
    return out


def compact_enchant(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, row.get("附魔类型"), row.get("附魔名称"))
        add_attr(attrs, row.get("属性名称"), row.get("倍率"))
    return out


def compact_jewelry(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, row.get("饰品类别"), row.get("等级"), row.get("饰品名称"), row.get("变体"))
        add_attr(attrs, row.get("属性名称"), row.get("属性数值"))
    return out


def compact_set_bonus(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, row.get("套装ID"), row.get("件数"))
        add_attr(attrs, row.get("属性名称"), row.get("属性数值"))
    return out


def compact_card_lookup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        name, grade = norm(row.get("key")).rsplit("|", 1)
        attrs = nested(out, name, grade)
        for attr, value in row.items():
            if attr != "key" and value not in (None, "", 0):
                attrs[attr] = clean_number(value)
    return out


def compact_card_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        entries = nested(out, row.get("卡片名称"), row.get("等级")).setdefault("条目", [])
        entries.append({
            "数值类型": row.get("数值类型"),
            "数值": clean_number(row.get("数值")),
            "查询编号": row.get("查询编号"),
            "K博士": row.get("K博士"),
        })
    return out


def compact_simple_attrs(rows: list[dict[str, Any]], *keys: str, attr_field: str = "属性", value_field: str = "数值") -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, *(row.get(key) for key in keys))
        add_attr(attrs, row.get(attr_field), row.get(value_field))
    return out


def compact_glyph_s(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attrs = nested(out, row.get("等级"), row.get("纹章名称"), row.get("选择基础属性"), row.get("数值档位"))
        add_attr(attrs, row.get("属性"), row.get("数值"))
    return out


def compact_normal_rune(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        nested(out, row.get("等级"), row.get("石板类型/转换"), row.get("属性"))[norm(row.get("档位"))] = clean_number(row.get("数值"))
    return out


def compact_core_rune(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        item = nested(out, row.get("等级")).setdefault(norm(row.get("石板类型/转换")), OrderedDict())
        item.setdefault("扣除属性", row.get("扣除属性"))
        item.setdefault("获得属性", row.get("获得属性"))
        item.setdefault("转换比例", OrderedDict())[norm(row.get("比例档位"))] = clean_number(row.get("转换比例"))
        item.setdefault("转换系数", OrderedDict())[norm(row.get("系数档位"))] = clean_number(row.get("转换系数"))
        item.setdefault("最大扣除值", OrderedDict())[norm(row.get("比例档位"))] = clean_number(row.get("最大扣除值"))
    return out


def compact_character(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        item = nested(out, row.get("一转职业")).setdefault(norm(row.get("二转职业")), OrderedDict())
        add_attr(item, row.get("属性"), row.get("数值"))
    return out


def compact_conversion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        nested(out, row.get("一转职业"), row.get("来源属性"))[norm(row.get("目标属性"))] = clean_number(row.get("转换比例"))
    return out


def compact_job_map(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        out[norm(row.get("二转职业"))] = {
            "一转分支": row.get("一转分支"),
            "转换大职业": row.get("转换大职业"),
        }
    return out


def compact_effective(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        attacks = []
        for index in range(1, 4):
            attacks.append({
                "攻击": row.get(f"攻击{index}"),
                "属性": row.get(f"属性{index}"),
                "权重": clean_number(row.get(f"权重{index}")),
                "固定": clean_number(row.get(f"固定{index}")),
                "额外最终": clean_number(row.get(f"额外最终{index}")),
            })
        out[norm(row.get("职业"))] = attacks
    return out


def compact_buff(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = OrderedDict()
    for row in rows:
        key = norm(row.get("选择键")) or "|".join(part for part in (norm(row.get("类别")), norm(row.get("来源")), norm(row.get("技能"))) if part)
        item = out.setdefault(key, {
            "类别": row.get("类别"),
            "来源": row.get("来源"),
            "技能": row.get("技能"),
            "最大等级": row.get("最大等级"),
            "等级": OrderedDict(),
        })
        attrs = item["等级"].setdefault(norm(row.get("等级")), OrderedDict())
        add_attr(attrs, row.get("属性"), row.get("表格数值"))
    return out


def compact_auxiliary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    equipment_sets: dict[str, Any] = OrderedDict()
    jewelry_sets: dict[str, Any] = OrderedDict()
    for row in rows:
        if row.get("套装键") and row.get("套装ID"):
            equipment_sets.setdefault(norm(row.get("套装键")), clean_number(row.get("套装ID")))
        if row.get("饰品套装键") and row.get("饰品套装ID"):
            jewelry_sets.setdefault(norm(row.get("饰品套装键")), clean_number(row.get("饰品套装ID")))
    return {"装备套装": equipment_sets, "饰品套装": jewelry_sets}


def compact_table(table_name: str, rows: list[dict[str, Any]]) -> Any:
    if table_name == "base":
        return compact_equipment_base(rows)
    if table_name == "enhance":
        return compact_equipment_enhance(rows)
    if table_name == "suffix":
        return compact_equipment_suffix(rows)
    if table_name == "enchant":
        return compact_enchant(rows)
    if table_name == "jewelry":
        return compact_jewelry(rows)
    if table_name == "set_bonus":
        return compact_set_bonus(rows)
    if table_name == "card_lookup":
        return compact_card_lookup(rows)
    if table_name == "card_data":
        return compact_card_data(rows)
    if table_name == "glyph_a":
        return compact_simple_attrs(rows, "等级", "纹章名称")
    if table_name == "glyph_a_extra":
        return compact_simple_attrs(rows, "等级", "额外属性")
    if table_name == "glyph_s":
        return compact_glyph_s(rows)
    if table_name == "glyph_s_extra":
        return compact_simple_attrs(rows, "等级", "纹章名称", "选择额外属性")
    if table_name == "normal_rune":
        return compact_normal_rune(rows)
    if table_name == "core_rune":
        return compact_core_rune(rows)
    if table_name == "character":
        return compact_character(rows)
    if table_name == "collection":
        return compact_simple_attrs(rows, "等级")
    if table_name == "mastery":
        return compact_simple_attrs(rows, "专精", "等级")
    if table_name == "title":
        return compact_simple_attrs(rows, "称号")
    if table_name == "fashion":
        return compact_simple_attrs(rows, "部位", "时装")
    if table_name == "conversion":
        return compact_conversion(rows)
    if table_name == "job_map":
        return compact_job_map(rows)
    if table_name == "effective":
        return compact_effective(rows)
    if table_name == "buff":
        return compact_buff(rows)
    if table_name == "auxiliary":
        return compact_auxiliary(rows)
    raise KeyError(table_name)


def split_by(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = OrderedDict()
    for row in rows:
        grouped.setdefault(safe_name(row.get(field)), []).append(row)
    return grouped


def write_split_table(table_name: str, rows: list[dict[str, Any]], field: str, folder: str, prefix: str) -> list[str]:
    paths: list[str] = []
    for key, grouped_rows in split_by(rows, field).items():
        relative_path = f"{folder}/{prefix}_{key}.json"
        dump_json(relative_path, compact_table(table_name, grouped_rows))
        paths.append(relative_path)
    return paths


def write_readme() -> None:
    (OUT_DIR / "README.md").write_text(
        """# 数据文件说明

程序会优先读取本目录的 `manifest.json`，再按里面的路径加载各个小 JSON。
这里的 JSON 已经是紧凑结构，不再是原始 Excel 的一行一属性格式。

## 常用编辑位置

- 装备基础属性：`tables/equipment/base/equipment_base_职业.json`
- 装备强化：`tables/equipment/enhance/equipment_enhance_职业.json`
- 装备后缀：`tables/equipment/suffix/equipment_suffix_职业.json`
- 附魔：`tables/equipment_enchant.json`
- 饰品：`tables/jewelry/jewelry_部位.json`
- 套装属性：`tables/set_bonus.json`
- A 纹章：`tables/glyph_a_base.json`、`tables/glyph_a_extra.json`
- S 纹章：`tables/glyph_s_base.json`、`tables/glyph_s_extra.json`
- 普通石板：`tables/normal_rune.json`
- 源铸石板：`tables/core_rune.json`
- 卡片：`tables/card_data.json`、`tables/card_lookup.json`
- 收集等级：`tables/collection.json`
- 称号：`tables/title.json`
- 时装：`tables/fashion.json`
- 技能 buff：`tables/skill_buff.json`
- 职业基础属性：`tables/character_base.json`
- 职业映射：`tables/job_map.json`
- 属性转换：`tables/attribute_conversion.json`
- 等效面板权重：`tables/effective_panel.json`
- 下拉框列表：`lists/*.json`
- 默认配装：`defaults.json`
- 右侧属性显示顺序：`attribute_order.json`

## 例子

装备基础属性现在是：

```json
{
  "战士": {
    "70S": {
      "地狱犬头盔": {
        "部位": "头盔",
        "套装ID": 1000,
        "属性": {"防御": 123, "魔防": 456}
      }
    }
  }
}
```

源铸石板现在是：

```json
{
  "60": {
    "物攻→魔攻": {
      "扣除属性": "物攻",
      "获得属性": "魔攻",
      "转换比例": {"1": 5},
      "转换系数": {"1": 0.25},
      "最大扣除值": {"1": 20000}
    }
  }
}
```

## 更新流程

如果从 Excel 重新提取数据，运行：

```powershell
python tools\\extract_workbook_data.py
```

这个命令会先刷新上一层的 `dragon_nest_data.json`，再自动刷新本目录的小文件。

`dragon_nest_data.json` 是重新提取时的临时中间文件，程序正常运行和发布均不需要保留它。

网页技能原始数据重新抓取并按职业拆分后，运行：

```powershell
python tools\\compact_full_skill_runtime.py --install
```

该命令只保留模拟器实际读取的 PVE 技能树、等级、SP、CD、描述和元素字段。
""",
        encoding="utf-8",
    )


def main() -> None:
    if OUT_DIR.exists():
        resolved_out = OUT_DIR.resolve()
        resolved_data = (ROOT / "dragon_nest_builder" / "data").resolve()
        if resolved_data not in resolved_out.parents:
            raise RuntimeError(f"Refusing to clear unexpected directory: {resolved_out}")
        shutil.rmtree(OUT_DIR)

    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    tables = data["tables"]
    lists = data["lists"]

    manifest_tables: dict[str, str | list[str]] = {
        "base": write_split_table("base", tables["base"], "职业", "tables/equipment/base", "equipment_base"),
        "enhance": write_split_table("enhance", tables["enhance"], "职业", "tables/equipment/enhance", "equipment_enhance"),
        "suffix": write_split_table("suffix", tables["suffix"], "职业", "tables/equipment/suffix", "equipment_suffix"),
        "jewelry": write_split_table("jewelry", tables["jewelry"], "饰品类别", "tables/jewelry", "jewelry"),
    }

    simple_paths = {
        "enchant": "tables/equipment_enchant.json",
        "set_bonus": "tables/set_bonus.json",
        "card_lookup": "tables/card_lookup.json",
        "card_data": "tables/card_data.json",
        "glyph_a": "tables/glyph_a_base.json",
        "glyph_a_extra": "tables/glyph_a_extra.json",
        "glyph_s": "tables/glyph_s_base.json",
        "glyph_s_extra": "tables/glyph_s_extra.json",
        "character": "tables/character_base.json",
        "collection": "tables/collection.json",
        "mastery": "tables/mastery.json",
        "title": "tables/title.json",
        "fashion": "tables/fashion.json",
        "conversion": "tables/attribute_conversion.json",
        "job_map": "tables/job_map.json",
        "effective": "tables/effective_panel.json",
        "buff": "tables/skill_buff.json",
        "auxiliary": "tables/auxiliary_indexes.json",
    }
    for table_name, relative_path in simple_paths.items():
        dump_json(relative_path, compact_table(table_name, tables[table_name]))
        manifest_tables[table_name] = relative_path

    normal_rune_rows = [row for row in tables["rune"] if row.get("数据类型") == "普通"]
    core_rune_rows = [row for row in tables["rune"] if row.get("数据类型") == "特殊"]
    dump_json("tables/normal_rune.json", compact_table("normal_rune", normal_rune_rows))
    dump_json("tables/core_rune.json", compact_table("core_rune", core_rune_rows))
    manifest_tables["rune"] = ["tables/normal_rune.json", "tables/core_rune.json"]

    list_groups = {
        "lists/jobs.json": ["first_jobs"],
        "lists/glyph.json": [
            "glyph_a_levels",
            "glyph_a_names",
            "glyph_a_extra",
            "glyph_s_levels",
            "glyph_s_names",
            "glyph_s_values",
            "s_base_options",
            "s_extra_options",
        ],
        "lists/rune.json": ["rune_levels", "rune_attack_attrs", "rune_defense_attrs", "rune_ranks"],
        "lists/core_rune.json": ["core_levels", "core_minus_attrs", "core_plus_attrs", "core_ratio_ranks"],
        "lists/misc.json": ["titles", "fashion"],
    }
    list_files: list[str] = []
    for relative_path, keys in list_groups.items():
        dump_json(relative_path, {key: lists.get(key, [] if key != "fashion" else {}) for key in keys})
        list_files.append(relative_path)

    dump_json("attribute_order.json", data["attribute_order"])
    dump_json("defaults.json", data["defaults"])

    manifest = {
        "format": 2,
        "source": Path(str(data.get("source") or SOURCE)).name,
        "note": "程序运行时只读取本目录的紧凑 JSON；旧总表仅作为重新提取时的临时中间文件。",
        "tables": manifest_tables,
        "list_files": list_files,
        "attribute_order": "attribute_order.json",
        "defaults": "defaults.json",
    }
    dump_json("manifest.json", manifest)
    write_readme()


if __name__ == "__main__":
    main()
