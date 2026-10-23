from __future__ import annotations

from ..shared import *
from ..editor import BuildEditor


class ComparePage(QWidget):
    def __init__(self, editor1: BuildEditor, editor2: BuildEditor, calculator: BuildCalculator):
        super().__init__()
        self.editor1 = editor1
        self.editor2 = editor2
        self.calculator = calculator
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        button = app_button("刷新对比", primary=True)
        button.clicked.connect(self.refresh)
        header.addWidget(button)
        header.addStretch(1)
        layout.addLayout(header)

        content = QVBoxLayout()
        content.setSpacing(8)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        left_column = QVBoxLayout()
        left_column.setSpacing(8)

        key_box = QGroupBox("关键差异")
        key_box.setMaximumHeight(235)
        key_layout = QVBoxLayout(key_box)
        key_layout.setContentsMargins(7, 8, 7, 7)
        key_layout.setSpacing(5)
        self.key_table = AppTableWidget(0, 5)
        self.key_table.setHorizontalHeaderLabels(["项目", "配装1", "配装2", "差值(2-1)", "提升%"])
        BuildEditor._configure_table(self.key_table, [122, 112, 112, 122, 86])
        self._configure_compact_compare_table(self.key_table)
        self.key_table.setMinimumHeight(150)
        self.key_table.setMaximumHeight(185)
        key_layout.addWidget(self.key_table)
        left_column.addWidget(key_box)

        cost_box = QGroupBox("提升性价比")
        cost_box.setMinimumHeight(235)
        cost_box.setMaximumHeight(285)
        cost_layout = QVBoxLayout(cost_box)
        cost_layout.setContentsMargins(7, 8, 7, 7)
        cost_layout.setSpacing(5)
        cost_form = QHBoxLayout()
        cost_form.setSpacing(6)
        self.gold_cost = QDoubleSpinBox()
        self.gold_cost.setRange(0, 1_000_000_000)
        self.gold_cost.setDecimals(2)
        self.gold_cost.setSingleStep(1000)
        self.gold_cost.setSuffix(" 金币")
        self.gold_price = QDoubleSpinBox()
        self.gold_price.setRange(0, 1_000_000_000)
        self.gold_price.setDecimals(2)
        self.gold_price.setSingleStep(1000)
        self.gold_price.setSuffix(" 金币/R")
        self.gold_cost.valueChanged.connect(self.refresh)
        self.gold_price.valueChanged.connect(self.refresh)
        cost_form.addWidget(QLabel("金币成本"))
        cost_form.addWidget(self.gold_cost)
        cost_form.addWidget(QLabel("金币价格"))
        cost_form.addWidget(self.gold_price)
        cost_layout.addLayout(cost_form)
        self.cost_table = AppTableWidget(0, 3)
        self.cost_table.setHorizontalHeaderLabels(["项目", "金币", "RMB"])
        BuildEditor._configure_table(self.cost_table, [205, 135, 110])
        self._configure_compact_compare_table(self.cost_table)
        self.cost_table.setMinimumHeight(168)
        self.cost_table.setMaximumHeight(190)
        cost_layout.addWidget(self.cost_table)
        left_column.addWidget(cost_box)

        detail_box = QGroupBox("详细属性差异")
        detail_box.setMaximumHeight(515)
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(7, 8, 7, 7)
        detail_layout.setSpacing(5)
        self.detail_table = AppTableWidget(0, 5)
        self.detail_table.setHorizontalHeaderLabels(["属性", "配装1", "配装2", "差值(2-1)", "提升%"])
        BuildEditor._configure_table(self.detail_table, [110, 115, 115, 115, 80])
        self.detail_table.setMaximumHeight(410)
        detail_layout.addWidget(self.detail_table)

        top_row.addLayout(left_column, 3)
        top_row.addWidget(detail_box, 4)
        content.addLayout(top_row, 0)

        diff_box = QGroupBox("配装差异")
        diff_layout = QVBoxLayout(diff_box)
        diff_layout.setContentsMargins(7, 8, 7, 7)
        diff_layout.setSpacing(5)
        self.build_diff_table = AppTableWidget(0, 3)
        self.build_diff_table.setHorizontalHeaderLabels(["部位", "配装1", "配装2"])
        BuildEditor._configure_table(self.build_diff_table, [120, 430, 430])
        diff_header = self.build_diff_table.horizontalHeader()
        diff_header.setSectionResizeMode(0, QHeaderView.Fixed)
        diff_header.setSectionResizeMode(1, QHeaderView.Stretch)
        diff_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.build_diff_table.setMinimumHeight(300)
        diff_layout.addWidget(self.build_diff_table)
        content.addWidget(diff_box, 1)
        layout.addLayout(content, 1)
        self.refresh()

    @staticmethod
    def _configure_compact_compare_table(table: QTableWidget) -> None:
        table.horizontalHeader().setFixedHeight(26)
        table.verticalHeader().setDefaultSectionSize(24)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def refresh(self) -> None:
        build1 = self.editor1.collect_build()
        build2 = self.editor2.collect_build()
        rows1, sum1 = self.calculator.calculate(build1)
        rows2, sum2 = self.calculator.calculate(build2)
        key_values: list[tuple[str, float, float]] = [
            ("等效面板", sum1["effective_panel"], sum2["effective_panel"]),
            ("最终%", sum1["final_percent"], sum2["final_percent"]),
            ("致命%", sum1["crit_percent"], sum2["crit_percent"]),
            ("爆伤%", sum1["crit_damage_percent"], sum2["crit_damage_percent"]),
        ]
        by_attr1 = {row.attr: row for row in rows1}
        by_attr2 = {row.attr: row for row in rows2}
        detail_values: list[tuple[str, float, float]] = []
        for attr in DISPLAY_ATTRS:
            detail_values.append((
                attr,
                BuildEditor._display_row_values(by_attr1, attr)[-1],
                BuildEditor._display_row_values(by_attr2, attr)[-1],
            ))
        self._populate_compare_table(self.key_table, key_values)
        self._populate_compare_table(self.detail_table, detail_values)
        self._populate_cost_table(sum1["effective_panel"], sum2["effective_panel"])
        self._populate_build_diff_table(build1, build2)

    def _populate_compare_table(self, table: QTableWidget, values: list[tuple[str, float, float]]) -> None:
        table.setRowCount(len(values))
        for row, (name, v1, v2) in enumerate(values):
            diff = v2 - v1
            pct = diff / abs(v1) if v1 else 0
            if name in DISPLAY_ATTRS:
                row_values = [name, attr_number_text(name, v1), attr_number_text(name, v2), attr_number_text(name, diff), f"{pct:.2%}" if pct else ""]
            else:
                integer = name == "等效面板"
                row_values = [name, number_text(v1, integer=integer), number_text(v2, integer=integer), number_text(diff, integer=integer), f"{pct:.2%}" if pct else ""]
            for col, text in enumerate(row_values):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, col, item)

    def _populate_cost_table(self, panel1: float, panel2: float) -> None:
        panel_gain = panel2 - panel1
        panel_pct = panel_gain / panel1 * 100 if panel1 else 0.0
        gold = self.gold_cost.value()
        price = self.gold_price.value()
        rmb = gold / price if price > 0 else 0.0

        rows = [
            ("等效面板提升", number_text(panel_gain, integer=True), ""),
            ("等效面板提升%", f"{panel_pct:.2f}%" if panel_pct else "", ""),
            ("每1%提升成本", number_text(gold / panel_pct) if panel_pct > 0 else "", number_text(rmb / panel_pct) if panel_pct > 0 and rmb else ""),
            ("每1点等效面板提升成本", number_text(gold / panel_gain) if panel_gain > 0 else "", number_text(rmb / panel_gain) if panel_gain > 0 and rmb else ""),
        ]
        self.cost_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            self.cost_table.setRowHeight(row, 26)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.cost_table.setItem(row, col, item)

    def _populate_build_diff_table(self, build1: dict[str, Any], build2: dict[str, Any]) -> None:
        rows: list[tuple[str, str, str]] = []
        for index, slot in enumerate(EQUIPMENT_SLOTS):
            item1 = build1.get("equipment", [{}])[index] if index < len(build1.get("equipment", [])) else {}
            item2 = build2.get("equipment", [{}])[index] if index < len(build2.get("equipment", [])) else {}
            text1 = self._equipment_diff_text(item1)
            text2 = self._equipment_diff_text(item2)
            self._append_build_diff(rows, f"装备-{slot}", text1, text2)
        for index, slot in enumerate(JEWELRY_SLOTS):
            item1 = build1.get("jewelry", [{}])[index] if index < len(build1.get("jewelry", [])) else {}
            item2 = build2.get("jewelry", [{}])[index] if index < len(build2.get("jewelry", [])) else {}
            text1 = self._jewelry_diff_text(item1)
            text2 = self._jewelry_diff_text(item2)
            self._append_build_diff(rows, f"饰品-{slot}", text1, text2)
        engraving1 = {norm(item.get("slot")): item for item in build1.get("engravings", []) if isinstance(item, dict)}
        engraving2 = {norm(item.get("slot")): item for item in build2.get("engravings", []) if isinstance(item, dict)}
        for slot in ENGRAVING_SLOTS:
            self._append_build_diff(
                rows,
                f"刻印-{slot}",
                self._engraving_diff_text(engraving1.get(slot, {})),
                self._engraving_diff_text(engraving2.get(slot, {})),
            )
        for index in range(max(len(build1.get("glyph_a", [])), len(build2.get("glyph_a", [])))):
            item1 = self._list_item(build1.get("glyph_a", []), index)
            item2 = self._list_item(build2.get("glyph_a", []), index)
            self._append_build_diff(rows, f"A纹章{index + 1}", self._glyph_a_diff_text(item1), self._glyph_a_diff_text(item2))
        for index in range(max(len(build1.get("glyph_s", [])), len(build2.get("glyph_s", [])))):
            item1 = self._list_item(build1.get("glyph_s", []), index)
            item2 = self._list_item(build2.get("glyph_s", []), index)
            self._append_build_diff(rows, f"S纹章{index + 1}", self._glyph_s_diff_text(item1), self._glyph_s_diff_text(item2))
        for index in range(max(len(build1.get("normal_runes", [])), len(build2.get("normal_runes", [])))):
            item1 = self._list_item(build1.get("normal_runes", []), index)
            item2 = self._list_item(build2.get("normal_runes", []), index)
            label = f"普通石板{index + 1}"
            self._append_build_diff(rows, label, self._normal_rune_diff_text(item1), self._normal_rune_diff_text(item2))
        self._append_build_diff(rows, "源铸石板", self._core_rune_diff_text(build1.get("core_rune", {})), self._core_rune_diff_text(build2.get("core_rune", {})))
        for index in range(max(len(build1.get("card_grades", [])), len(build2.get("card_grades", [])))):
            item1 = self._list_item(build1.get("card_grades", []), index)
            item2 = self._list_item(build2.get("card_grades", []), index)
            name = norm(item1.get("name")) or norm(item2.get("name")) or f"卡片{index + 1}"
            self._append_build_diff(rows, f"卡片-{name}", norm(item1.get("grade")) or "无", norm(item2.get("grade")) or "无")
        for attr in CARD_PREVIEW_ATTRS:
            level1 = build1.get("card_bonus_levels", {}).get(attr, 0)
            level2 = build2.get("card_bonus_levels", {}).get(attr, 0)
            self._append_build_diff(rows, f"卡片加成-{attr}", str(level1), str(level2))
        talisman1 = {
            norm(item.get("slot")): item
            for item in build1.get("talismans", []) if isinstance(item, dict)
        }
        talisman2 = {
            norm(item.get("slot")): item
            for item in build2.get("talismans", []) if isinstance(item, dict)
        }
        for slot in TALISMAN_SLOTS:
            self._append_build_diff(
                rows,
                f"护符-{slot}",
                self._talisman_diff_text(talisman1.get(slot, {})),
                self._talisman_diff_text(talisman2.get(slot, {})),
            )
        self._append_build_diff(rows, "其他-收集等级", str(build1.get("collection_level", 0)), str(build2.get("collection_level", 0)))
        self._append_build_diff(rows, "其他-称号", norm(build1.get("title")) or "无", norm(build2.get("title")) or "无")
        self._append_build_diff(rows, "其他-致命抵抗", number_text(as_number(build1.get("crit_resist_value")), integer=True), number_text(as_number(build2.get("crit_resist_value")), integer=True))
        self._append_build_diff(rows, "其他-Boss", norm(build1.get("boss")) or "无", norm(build2.get("boss")) or "无")
        self._append_build_diff(rows, "其他-怪物防御", number_text(as_number(build1.get("monster_defense")), integer=True), number_text(as_number(build2.get("monster_defense")), integer=True))
        self._append_build_diff(rows, "其他-怪物防御率", f"{as_number(build1.get('monster_defense_rate')):.1f}%", f"{as_number(build2.get('monster_defense_rate')):.1f}%")
        self._append_build_diff(rows, "技能buff汇总", self._buffs_diff_summary(build1.get("buffs", [])), self._buffs_diff_summary(build2.get("buffs", [])))
        for index, name in enumerate(MASTERY_NAMES):
            item1 = self._list_item(build1.get("mastery", []), index)
            item2 = self._list_item(build2.get("mastery", []), index)
            self._append_build_diff(rows, f"专精-{name}", str(item1.get("level", 0)), str(item2.get("level", 0)))
        for index, slot in enumerate(FASHION_SLOTS):
            item1 = self._list_item(build1.get("fashion", []), index)
            item2 = self._list_item(build2.get("fashion", []), index)
            self._append_build_diff(rows, f"时装-{slot}", norm(item1.get("name")) or "无", norm(item2.get("name")) or "无")
        for index in range(max(len(build1.get("buffs", [])), len(build2.get("buffs", [])))):
            item1 = self._list_item(build1.get("buffs", []), index)
            item2 = self._list_item(build2.get("buffs", []), index)
            self._append_build_diff(rows, f"技能buff{index + 1}", self._buff_diff_text(item1), self._buff_diff_text(item2))
        for label, value1, value2 in self._free_input_diffs(build1.get("free_inputs", {}), build2.get("free_inputs", {})):
            self._append_build_diff(rows, label, value1, value2)
        if not rows:
            rows = [("无差异", "", "")]
        self.build_diff_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.build_diff_table.setItem(row, col, item)
            self.build_diff_table.setRowHeight(row, BuildEditor._detail_row_height("；".join(values), base=28, chars_per_line=140))

    @staticmethod
    def _append_build_diff(rows: list[tuple[str, str, str]], label: str, text1: Any, text2: Any) -> None:
        left = norm(text1) or "无"
        right = norm(text2) or "无"
        if left != right:
            rows.append((label, left, right))

    @staticmethod
    def _list_item(items: list[dict[str, Any]], index: int) -> dict[str, Any]:
        return items[index] if index < len(items) and isinstance(items[index], dict) else {}

    @staticmethod
    def _equipment_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("name")) or "无"]
        suffix = norm(item.get("suffix"))
        enhance = int(item.get("enhance") or 0)
        enchants = [norm(value) for value in item.get("enchants", []) if norm(value) and norm(value) != "无"]
        if suffix and suffix != "无":
            parts.append(suffix)
        if enhance:
            parts.append(f"+{enhance}")
        if enchants:
            parts.append("/".join(enchants))
        return "；".join(parts)

    @staticmethod
    def _glyph_a_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("name")) or "无"]
        level = norm(item.get("level"))
        extra = norm(item.get("extra"))
        if level and level != "无":
            parts.append(level)
        if extra and extra != "无":
            parts.append(f"额外:{extra}")
        return "；".join(parts)

    @staticmethod
    def _talisman_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("grade")) or "无", norm(item.get("name")) or "无"]
        extra = norm(item.get("extra"))
        if extra and extra != "无":
            parts.append(f"额外:{extra}")
        return "；".join(parts)

    @staticmethod
    def _glyph_s_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("name")) or "无"]
        level = norm(item.get("level"))
        base1 = norm(item.get("base_attr1"))
        rank1 = norm(item.get("rank1"))
        base2 = norm(item.get("base_attr2"))
        rank2 = norm(item.get("rank2"))
        extra = norm(item.get("extra"))
        if level and level != "无":
            parts.append(level)
        if base1 and base1 != "无":
            parts.append(f"{base1}{rank1}")
        if base2 and base2 != "无":
            parts.append(f"{base2}{rank2}")
        if extra and extra != "无":
            parts.append(f"额外:{extra}")
        return "；".join(parts)

    @staticmethod
    def _normal_rune_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("type")) or "无"]
        level = norm(item.get("level"))
        if level and level != "无":
            parts.append(level)
        entries = []
        for entry in item.get("entries", []):
            attr = norm(entry.get("attr"))
            rank = norm(entry.get("rank"))
            if attr and attr != "无":
                entries.append(f"{attr}{rank}")
        if entries:
            parts.append("/".join(entries))
        return "；".join(parts)

    @staticmethod
    def _core_rune_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("conversion")) or "无"]
        level = norm(item.get("level"))
        ratio = norm(item.get("ratio_rank"))
        coeff = norm(item.get("coeff_rank"))
        enhance = int(item.get("enhance") or 0)
        if level and level != "无":
            parts.append(level)
        if ratio:
            parts.append(f"比例{ratio}")
        if coeff:
            parts.append(f"系数{coeff}")
        if enhance:
            parts.append(f"强化+{enhance}")
        return "；".join(parts)

    @staticmethod
    def _buff_diff_text(item: dict[str, Any]) -> str:
        skill = norm(item.get("skill")) or "无"
        level = norm(item.get("level"))
        return f"{skill}；{level}" if level and level != "无" else skill

    @staticmethod
    def _buffs_diff_summary(items: list[dict[str, Any]]) -> str:
        parts = []
        for item in items:
            text = ComparePage._buff_diff_text(item)
            if text and text != "无":
                parts.append(text)
        return " / ".join(parts) if parts else "无"

    @staticmethod
    def _free_input_diffs(free1: dict[str, Any], free2: dict[str, Any]) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for group_key, group_label in (("percent", "自由百分比"), ("stats", "自由属性"), ("stat_percent", "自由属性百分比")):
            values1 = free1.get(group_key, {}) if isinstance(free1, dict) else {}
            values2 = free2.get(group_key, {}) if isinstance(free2, dict) else {}
            keys = sorted(set(values1) | set(values2), key=str)
            for key in keys:
                value1 = as_number(values1.get(key))
                value2 = as_number(values2.get(key))
                text1 = number_text(value1) if abs(value1) > 1e-9 else "0"
                text2 = number_text(value2) if abs(value2) > 1e-9 else "0"
                rows.append((f"{group_label}-{key}", text1, text2))
        return rows

    @staticmethod
    def _jewelry_diff_text(item: dict[str, Any]) -> str:
        parts = [norm(item.get("name")) or "无"]
        variant = norm(item.get("variant"))
        enchants = [norm(value) for value in item.get("enchants", []) if norm(value) and norm(value) != "无"]
        if variant and variant != "无":
            parts.append(variant)
        if enchants:
            parts.append("/".join(enchants))
        return "；".join(parts)

    @staticmethod
    def _engraving_diff_text(item: dict[str, Any]) -> str:
        grade = norm(item.get("grade"))
        if not grade or grade == "无":
            return "无"
        value_rank = norm(item.get("value_rank"))
        extra = norm(item.get("extra"))
        extra_tier = norm(item.get("extra_tier"))
        parts: list[str] = [grade]
        if value_rank and value_rank != "无":
            parts.append(value_rank)
        if extra and extra != "无":
            tier_text = f"({extra_tier})" if extra_tier and extra_tier != "无" else ""
            parts.append(f"额外:{extra}{tier_text}")
        return "；".join(parts) if parts else "无"
