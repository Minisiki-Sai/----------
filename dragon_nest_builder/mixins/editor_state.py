from __future__ import annotations

from ..shared import *

BuildEditor = None


class EditorStateMixin:
    @staticmethod
    def _build_page_shell(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        """Create the shared visual shell used by build editor pages."""

        page = QWidget()
        page.setObjectName("buildEditorPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 12)
        layout.setSpacing(10)

        header = QFrame()
        header.setObjectName("buildPageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 9, 12, 9)
        header_layout.setSpacing(10)

        accent = QFrame()
        accent.setObjectName("buildPageAccent")
        accent.setFixedWidth(4)
        header_layout.addWidget(accent)

        copy_layout = QVBoxLayout()
        copy_layout.setContentsMargins(0, 0, 0, 0)
        copy_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("buildPageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("buildPageSubtitle")
        subtitle_label.setWordWrap(True)
        copy_layout.addWidget(title_label)
        copy_layout.addWidget(subtitle_label)
        header_layout.addLayout(copy_layout, 1)
        layout.addWidget(header)
        return page, layout

    @staticmethod
    def _build_toolbar() -> tuple[QFrame, QHBoxLayout]:
        toolbar = QFrame()
        toolbar.setObjectName("buildToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(10, 7, 10, 7)
        toolbar_layout.setSpacing(7)
        return toolbar, toolbar_layout

    @staticmethod
    def _build_section(title: str, subtitle: str = "") -> tuple[QGroupBox, QVBoxLayout]:
        box = QGroupBox(title)
        box.setObjectName("buildSection")
        section_layout = QVBoxLayout(box)
        section_layout.setContentsMargins(10, 12, 10, 10)
        section_layout.setSpacing(7)
        if subtitle:
            hint = QLabel(subtitle)
            hint.setObjectName("buildSectionHint")
            hint.setWordWrap(True)
            section_layout.addWidget(hint)
        return box, section_layout

    @staticmethod
    def _mark_build_table(table: QTableWidget) -> None:
        table.setObjectName("buildEditorTable")
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)

    @staticmethod
    def _fit_table_height(table: QTableWidget, *, extra: int = 4) -> None:
        """Show every row and let the outer page own vertical scrolling."""

        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(
            table.horizontalHeader().height()
            + sum(table.rowHeight(row) for row in range(table.rowCount()))
            + table.frameWidth() * 2
            + extra
        )

    def _free_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        free_defaults = self.build.get("free_inputs", {})
        percent_defaults = free_defaults.get("percent", {})
        stat_defaults = free_defaults.get("stats", {})
        stat_percent_defaults = free_defaults.get("stat_percent", {})

        button_row = QHBoxLayout()
        clear_button = app_button("一键归0")
        clear_button.clicked.connect(self.clear_free_inputs)
        button_row.addWidget(clear_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        percent_box = QGroupBox("百分比换算输入")
        percent_layout = QVBoxLayout(percent_box)
        self.free_percent_table = AppTableWidget(len(FREE_PERCENT_INPUTS), 2)
        self.free_percent_table.setHorizontalHeaderLabels(["项目", "输入值"])
        for row, (label, attr) in enumerate(FREE_PERCENT_INPUTS):
            self.free_percent_table.setItem(row, 0, self._item(label))
            spin = self._free_spin(percent_defaults.get(label, percent_defaults.get(attr, 0)), decimals=3, suffix=" %", maximum=10000)
            self.free_percent_table.setCellWidget(row, 1, spin)
        self._configure_table(self.free_percent_table, [120, 160])
        percent_layout.addWidget(self.free_percent_table)
        layout.addWidget(percent_box)

        lower = QHBoxLayout()
        lower.setSpacing(10)

        stat_box = QGroupBox("属性栏自由输入")
        stat_layout = QVBoxLayout(stat_box)
        self.free_stat_table = AppTableWidget(len(FREE_STAT_ATTRS), 2)
        self.free_stat_table.setHorizontalHeaderLabels(["属性", "输入值"])
        for row, attr in enumerate(FREE_STAT_ATTRS):
            self.free_stat_table.setItem(row, 0, self._item(attr))
            spin = self._free_spin(stat_defaults.get(attr, 0), decimals=2, maximum=1_000_000_000_000)
            self.free_stat_table.setCellWidget(row, 1, spin)
        self._configure_table(self.free_stat_table, [120, 180])
        stat_layout.addWidget(self.free_stat_table)
        lower.addWidget(stat_box, 1)

        stat_percent_box = QGroupBox("属性百分比自由输入")
        stat_percent_layout = QVBoxLayout(stat_percent_box)
        self.free_stat_percent_table = AppTableWidget(len(FREE_STAT_PERCENT_ATTRS), 2)
        self.free_stat_percent_table.setHorizontalHeaderLabels(["属性百分比", "输入值"])
        for row, attr in enumerate(FREE_STAT_PERCENT_ATTRS):
            self.free_stat_percent_table.setItem(row, 0, self._item(attr))
            spin = self._free_spin(stat_percent_defaults.get(attr, 0), decimals=3, suffix=" %", maximum=10000)
            self.free_stat_percent_table.setCellWidget(row, 1, spin)
        self._configure_table(self.free_stat_percent_table, [130, 180])
        stat_percent_layout.addWidget(self.free_stat_percent_table)
        lower.addWidget(stat_percent_box, 1)
        layout.addLayout(lower, 1)
        return self._scroll(page)

    def clear_free_inputs(self) -> None:
        for table in (self.free_percent_table, self.free_stat_table, self.free_stat_percent_table):
            for row in range(table.rowCount()):
                widget = table.cellWidget(row, 1)
                if isinstance(widget, QDoubleSpinBox):
                    widget.blockSignals(True)
                    widget.setValue(0)
                    widget.blockSignals(False)
        self.schedule_refresh()

    def _result_table(self) -> QWidget:
        page, layout = self._build_page_shell(
            "完整属性明细",
            "集中查看当前配装在未计入技能 Buff 与计入技能 Buff 后的全部面板属性。",
        )

        legend, legend_layout = self._build_toolbar()
        baseline_badge = QLabel("基础面板")
        baseline_badge.setObjectName("buildBaselineBadge")
        buffed_badge = QLabel("Buff 后面板")
        buffed_badge.setObjectName("buildBuffedBadge")
        legend_layout.addWidget(baseline_badge)
        legend_layout.addWidget(QLabel("两列使用同一属性口径，可直接横向比较变化"))
        legend_layout.addStretch(1)
        legend_layout.addWidget(buffed_badge)
        layout.addWidget(legend)

        panel, panel_layout = self._build_section(
            "面板属性对比",
            "百分比属性保留原有精度；数值随其他配装页的选择实时刷新。",
        )
        self.result_table = AppTableWidget(len(DISPLAY_ATTRS), 2)
        self.result_table.setHorizontalHeaderLabels(["未计入技能 Buff", "计入技能 Buff"])
        self.result_table.setVerticalHeaderLabels([
            "防御率" if attr == "防御率%" else "魔防率" if attr == "魔防率%" else attr
            for attr in DISPLAY_ATTRS
        ])
        self._mark_build_table(self.result_table)
        self.result_table.verticalHeader().setMinimumWidth(132)
        self._configure_table(self.result_table, [260, 260])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.verticalHeader().setDefaultSectionSize(28)
        self.result_table.verticalHeader().setMinimumSectionSize(26)
        for row in range(self.result_table.rowCount()):
            self.result_table.setRowHeight(row, 28)
        self.result_table.setMinimumWidth(560)
        self.result_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fit_table_height(self.result_table)
        panel_layout.addWidget(self.result_table)
        layout.addWidget(panel)
        layout.addStretch(1)
        return self._scroll(page, horizontal=False)

    @staticmethod
    def _scroll(widget: QWidget, horizontal: bool = True) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        if not horizontal:
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    @staticmethod
    def _item(text: Any) -> QTableWidgetItem:
        item = QTableWidgetItem("" if text is None else str(text))
        item.setToolTip(item.text())
        return item

    @staticmethod
    def _configure_table(table: QTableWidget, widths: list[int]) -> None:
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.verticalHeader().setDefaultSectionSize(EDIT_ROW_HEIGHT)
        table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        table.setContentsMargins(0, 0, 0, 0)
        table.setShowGrid(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setFixedHeight(26)
        header.setHighlightSections(False)
        table.verticalHeader().setHighlightSections(False)
        for col, width in enumerate(widths):
            table.setColumnWidth(col, width)

    @staticmethod
    def _rune_level_row(rune_index: int) -> int:
        """Return the first of the two compact selection rows for one rune."""

        return max(0, int(rune_index)) * 3

    @classmethod
    def _rune_detail_row(cls, rune_index: int) -> int:
        return cls._rune_level_row(rune_index) + 2

    @classmethod
    def _rune_entry_cell(
        cls,
        rune_index: int,
        entry_index: int,
    ) -> tuple[int, int, int]:
        """Map one of six rune entries onto a two-row, three-pair layout."""

        normalized_entry = max(0, min(5, int(entry_index)))
        row = cls._rune_level_row(rune_index) + normalized_entry // 3
        attr_column = 1 + (normalized_entry % 3) * 2
        return row, attr_column, attr_column + 1

    def _fit_rune_table_height(self) -> None:
        table = getattr(self, "rune_table", None)
        if table is None:
            return
        # Reserve the horizontal scrollbar's height even when it is currently
        # hidden. A later window shrink can then expose it without covering the
        # final stone's tier row.
        scroll_height = table.horizontalScrollBar().sizeHint().height()
        table.setFixedHeight(
            sum(table.rowHeight(row) for row in range(table.rowCount()))
            + table.horizontalHeader().height()
            + table.frameWidth() * 2
            + scroll_height
            + 4
        )

    def _adapt_equipment_column_widths(self) -> None:
        if not hasattr(self, "equipment_table"):
            return
        rows = list(range(0, len(EQUIPMENT_SLOTS) * 2, 2))
        widths = [
            self._combo_column_width(self.equipment_table, rows, 0, 190, 310),
            self._combo_column_width(self.equipment_table, rows, 1, 100, 160),
            58,
        ] + [self._combo_column_width(self.equipment_table, rows, col, 96, 136) for col in range(3, 8)]
        for col, width in enumerate(widths):
            self.equipment_table.setColumnWidth(col, width)

    def _adapt_jewelry_column_widths(self) -> None:
        if not hasattr(self, "jewelry_table"):
            return
        rows = list(range(0, len(JEWELRY_SLOTS) * 2, 2))
        widths = [
            self._combo_column_width(self.jewelry_table, rows, 0, 190, 310),
            self._combo_column_width(self.jewelry_table, rows, 1, 90, 130),
        ] + [self._combo_column_width(self.jewelry_table, rows, col, 96, 136) for col in range(2, 7)]
        for col, width in enumerate(widths):
            self.jewelry_table.setColumnWidth(col, width)

    def _adapt_misc_column_widths(self) -> None:
        if hasattr(self, "fashion_table"):
            fashion_rows = list(range(self.fashion_table.rowCount()))
            fashion_width = self._resize_combo_column(self.fashion_table, fashion_rows, 1, 96, 190)
            self.fashion_table.setColumnWidth(0, 58)
            self.fashion_table.setColumnWidth(2, 210)
        else:
            fashion_width = 190
        if hasattr(self, "buff_table"):
            buff_rows = list(range(self.buff_table.rowCount()))
            self._resize_combo_column(self.buff_table, buff_rows, 0, 110, 220)
            self._resize_combo_column(self.buff_table, buff_rows, 1, 68, 96)
            self.buff_table.setColumnWidth(2, 210)

    def _resize_combo_column(self, table: QTableWidget, rows: list[int], col: int, min_width: int, max_width: int) -> int:
        width = self._combo_column_width(table, rows, col, min_width, max_width)
        table.setColumnWidth(col, width)
        for row in rows:
            widget = table.cellWidget(row, col)
            if isinstance(widget, QComboBox):
                self._set_combo_content_width(widget, width)
        return width

    @staticmethod
    def _combo_column_width(table: QTableWidget, rows: list[int], col: int, min_width: int, max_width: int) -> int:
        values: list[str] = []
        for row in rows:
            widget = table.cellWidget(row, col)
            if isinstance(widget, QComboBox):
                values.extend(widget.itemText(index) for index in range(widget.count()))
                values.append(widget.currentText())
        return option_width(values, min_width, max_width)

    @staticmethod
    def _set_combo_content_width(combo: QComboBox, width: int) -> None:
        combo.setMinimumWidth(width)
        values = [combo.itemText(index) for index in range(combo.count())]
        values.append(combo.currentText())
        combo.view().setMinimumWidth(option_width(values, width, 520))

    @staticmethod
    def _fit_combo_column_to_cells(table: QTableWidget, col: int) -> None:
        width = table.columnWidth(col)
        for row in range(table.rowCount()):
            widget = table.cellWidget(row, col)
            if isinstance(widget, QComboBox):
                widget.setMinimumWidth(width)
                widget.resize(width, widget.height())
                values = [widget.itemText(index) for index in range(widget.count())]
                values.append(widget.currentText())
                widget.view().setMinimumWidth(option_width(values, width, 520))

    def _free_spin(self, value: Any = 0, decimals: int = 2, suffix: str = "", maximum: float = 1_000_000_000) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-maximum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(1)
        spin.setValue(float(value or 0))
        if suffix:
            spin.setSuffix(suffix)
        spin.valueChanged.connect(self.schedule_refresh)
        return spin

    def _percent_spin(self, value: Any = 0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 999)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix("%")
        spin.setFixedHeight(EDIT_ROW_HEIGHT)
        spin.setValue(float(value or 0))
        spin.valueChanged.connect(self.schedule_refresh)
        return spin

    @staticmethod
    def _engraving_main_kind(slot: str) -> str:
        if slot in {"头盔", "上装", "下装", "手套", "鞋子"}:
            return "防具双防"
        if slot in {"主手", "副手"}:
            return "武器双攻"
        if slot in {"项链", "耳环", "戒指1", "戒指2"}:
            return "饰品生命"
        return "三件套致命"

    def _engraving_secondary_value(
        self,
        slot: str,
        main_value: float,
        grade: str = "",
        value_rank: str = "",
    ) -> float:
        kind = self._engraving_main_kind(slot)
        main_options = self.store.engraving_main_value_options(kind, grade)
        selected = norm(value_rank)
        if selected not in main_options:
            selected = next((
                option for option in main_options
                if abs(self._engraving_option_value(option) - main_value) <= 1e-9
            ), "")
        if not selected or selected == "无":
            return 0.0
        index = main_options.index(selected)
        secondary_options = self.store.engraving_secondary_value_options(kind, grade)
        return (
            self._engraving_option_value(secondary_options[index])
            if index < len(secondary_options)
            else 0.0
        )

    def _engraving_grade_current(self, item: dict[str, Any]) -> str:
        grade = norm(item.get("grade"))
        if grade in {"B", "A", "S", "L"}:
            return grade
        for key in ("value_rank", "fixed1_rank", "fixed2_rank", "extra_rank"):
            text = norm(item.get(key))
            if text and text[0] in {"B", "A", "S", "L"}:
                return text[0]
        return "无"

    def _engraving_main_saved_value(self, slot: str, item: dict[str, Any]) -> float:
        if "main_value" in item:
            return as_number(item.get("main_value"))
        if slot in {"项链", "耳环", "戒指1", "戒指2", "翅膀", "尾巴", "印花"}:
            fixed2 = as_number(item.get("fixed2"))
            if fixed2:
                return fixed2
            return as_number(item.get("fixed1")) * 2
        return as_number(item.get("fixed1"))

    @staticmethod
    def _engraving_extra_tier_current(item: dict[str, Any]) -> str:
        rank = norm(item.get("extra_rank"))
        if rank and rank != "无":
            return rank.split()[0]
        tier = norm(item.get("extra_tier"))
        if tier in {"低档", "高档"}:
            grade = norm(item.get("extra_grade") or item.get("grade"))
            return f"{grade}{tier}" if grade in {"B", "A", "S", "L"} else tier
        return "无"

    def _engraving_extra_selection(
        self, table: QTableWidget, row: int
    ) -> tuple[str, str, float]:
        extra = self.combo_text(table, row, 2)
        widget = table.cellWidget(row, 3)
        rank = widget.currentText() if isinstance(widget, TierChoice) else "无"
        tier = "高档" if "高档" in rank else "低档" if "低档" in rank else "无"
        value = next((
            self._engraving_option_value(option)
            for option in self.store.engraving_extra_rank_options(extra)
            if norm(option).startswith(norm(rank))
        ), 0.0)
        return tier, rank, value

    @staticmethod
    def _engraving_extra_display(attr: Any) -> str:
        return {"魔法防御力%": "魔防%"}.get(norm(attr), norm(attr))

    @staticmethod
    def _engraving_option_value(text: Any) -> float:
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*%", norm(text))
        return float(match.group(1)) if match else as_number(text)

    def _engraving_option_current(self, options: list[str], value: Any = 0, saved_text: Any = "") -> str:
        saved = norm(saved_text)
        if saved in options:
            return saved
        number = as_number(value, default=float("nan"))
        if number == number:
            for option in options:
                if abs(self._engraving_option_value(option) - number) < 1e-9:
                    return option
        return "无"

    @staticmethod
    def _combo_with_detail(combo: Combo) -> tuple[QWidget, QLabel]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        detail = QLabel("")
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail.setStyleSheet("color: #555; font-size: 12px;")
        layout.addWidget(combo)
        layout.addWidget(detail)
        widget._combo = combo  # type: ignore[attr-defined]
        widget._detail = detail  # type: ignore[attr-defined]
        return widget, detail

    def _job_changed(self) -> None:
        current = self.second_job.currentText()
        self.second_job.clear()
        values = self.store.second_jobs(self.first_job.currentText())
        self.second_job.setMinimumWidth(option_width(values, 126, 196))
        self.second_job.addItems(values)
        if current and self.second_job.findText(current) >= 0:
            self.second_job.setCurrentText(current)
        self._refresh_equipment_option_lists()
        self._refresh_buff_option_lists()
        self._refresh_skill_build_options(use_default=True)
        self._refresh_skill_mastery_options(use_default=True)
        self._refresh_skill_sim_options()
        self._sync_team_current_member()
        self._apply_best_title_for_current_job()
        self.schedule_refresh()

    def _second_job_changed(self) -> None:
        self._refresh_equipment_option_lists()
        self._refresh_buff_option_lists()
        self._refresh_skill_build_options(use_default=True)
        self._refresh_skill_mastery_options(use_default=True)
        self._refresh_skill_sim_options()
        self._sync_team_current_member()
        self._apply_best_title_for_current_job()
        self.schedule_refresh()

    def _initial_element_resists(self) -> dict[str, float]:
        old_value = as_number(self.build.get("element_resist_percent", self.build.get("element_resist", 0)))
        values = {"火": old_value, "水": old_value, "光": old_value, "暗": old_value}
        raw = self.build.get("element_resists")
        if isinstance(raw, dict):
            for element in values:
                if element in raw:
                    values[element] = as_number(raw.get(element))
        return values

    def _current_element_resist_value(self, mode: str) -> float:
        mode_text = norm(mode)
        if mode_text in self.element_resists:
            return self.element_resists[mode_text]
        vals = list(self.element_resists.values())
        return vals[0] if vals and all(abs(value - vals[0]) < 1e-9 for value in vals) else 0.0

    def _element_resist_mode_changed(self, _text: str = "") -> None:
        if not hasattr(self, "element_resist"):
            return
        self.element_resist.blockSignals(True)
        self.element_resist.setValue(self._current_element_resist_value(self.element_resist_mode.currentText()))
        self.element_resist.blockSignals(False)
        self.schedule_refresh()

    def _element_resist_value_changed(self, value: float) -> None:
        mode = norm(self.element_resist_mode.currentText()) if hasattr(self, "element_resist_mode") else "全部"
        if mode in self.element_resists:
            self.element_resists[mode] = float(value)
        else:
            for element in self.element_resists:
                self.element_resists[element] = float(value)
        self.schedule_refresh()

    def _monster_defense_changed(self, value: int) -> None:
        if hasattr(self, "monster_defense_rate") and isinstance(self.monster_defense_rate, QDoubleSpinBox):
            self.monster_defense_rate.blockSignals(True)
            self.monster_defense_rate.setValue(BuildCalculator.defense_percent(value))
            self.monster_defense_rate.blockSignals(False)
        self.schedule_refresh()

    def _boss_changed(self, text: str = "") -> None:
        preset = BOSS_PRESETS.get(norm(text))
        if not preset:
            self.schedule_refresh()
            return
        self._set_spin_value(self.crit_resist, int(preset["crit_resist"]))
        for element in self.element_resists:
            self.element_resists[element] = float(preset["element_resist"])
        if isinstance(self.element_resist_mode, Combo):
            self._set_combo_current(self.element_resist_mode, "全部")
        self.element_resist.blockSignals(True)
        self.element_resist.setValue(float(preset["element_resist"]))
        self.element_resist.blockSignals(False)
        self._set_spin_value(self.monster_defense, int(preset["monster_defense"]))
        self._monster_defense_changed(int(preset["monster_defense"]))
        if hasattr(self, "team_freeze_coeff") and isinstance(self.team_freeze_coeff, QDoubleSpinBox):
            self.team_freeze_coeff.blockSignals(True)
            self.team_freeze_coeff.setValue(float(preset["freeze_coefficient"]))
            self.team_freeze_coeff.blockSignals(False)
        self.schedule_refresh()

    def _glyph_a_name_changed(self, row: int, _text: str = "") -> None:
        self._refresh_glyph_a_name_options()
        self._refresh_glyph_a_extra_options(row)
        self.schedule_refresh()

    def _refresh_glyph_a_name_options(self) -> None:
        if not hasattr(self, "glyph_a_table"):
            return
        all_names = self.store.glyph_a_names()
        names_without_none = [name for name in all_names if norm(name) != "无"]
        currents: list[str] = []
        seen: set[str] = set()
        for row in range(self.glyph_a_table.rowCount()):
            combo = self.glyph_a_table.cellWidget(row, 1)
            current = combo.currentText() if isinstance(combo, Combo) else "无"
            if norm(current) != "无":
                if current in seen:
                    current = "无"
                else:
                    seen.add(current)
            currents.append(current)
        for row, current in enumerate(currents):
            combo = self.glyph_a_table.cellWidget(row, 1)
            if not isinstance(combo, Combo):
                continue
            used_elsewhere = {name for index, name in enumerate(currents) if index != row and norm(name) != "无"}
            values = ["无"] + [name for name in names_without_none if name == current or name not in used_elsewhere]
            self._replace_combo_values(combo, values, current if current in values else "无")
            self._refresh_glyph_a_extra_options(row)

    @staticmethod
    def _glyph_a_forbidden_extra(name: str) -> str:
        text = norm(name)
        mapping = {
            "魔力": "魔攻",
            "破坏": "物攻",
            "巨熊": "力量",
            "疾风": "敏捷",
            "智慧": "智力",
            "健康": "体质",
            "生命": "HP",
            "法力": "MP",
            "铁壁": "防御",
            "帷幕": "魔防",
            "致命": "致命",
            "最终": "最终",
        }
        for keyword, attr in mapping.items():
            if keyword in text:
                return attr
        return ""

    def _glyph_a_extra_options_for_name(self, name: str) -> list[str]:
        forbidden = self._glyph_a_forbidden_extra(name)
        return [option for option in self.store.glyph_a_extra() if not forbidden or norm(option) != forbidden]

    def _refresh_glyph_a_extra_options(self, row: int) -> None:
        if not hasattr(self, "glyph_a_table") or row < 0 or row >= self.glyph_a_table.rowCount():
            return
        combo = self.glyph_a_table.cellWidget(row, 2)
        if not isinstance(combo, Combo):
            return
        name = self.combo_text(self.glyph_a_table, row, 1)
        values = self._glyph_a_extra_options_for_name(name)
        current = combo.currentText()
        if current not in values:
            current = "无"
        self._replace_combo_values(combo, values, current)

    def _equipment_name_changed(self, row: int) -> None:
        name = self.combo_text(self.equipment_table, row, 0)
        suffix = self.equipment_table.cellWidget(row, 1)
        if isinstance(suffix, Combo):
            self._replace_combo_values(
                suffix,
                self.store.suffix_names(self.first_job.currentText(), self.second_job.currentText(), name),
                suffix.currentText() or "无",
            )
        self._adapt_equipment_column_widths()
        self.schedule_refresh()

    def _jewelry_name_changed(self, row: int) -> None:
        display_slot = "戒指" if JEWELRY_SLOTS[row // 2].startswith("戒指") else JEWELRY_SLOTS[row // 2]
        name = self.combo_text(self.jewelry_table, row, 0)
        variant = self.jewelry_table.cellWidget(row, 1)
        if isinstance(variant, Combo):
            self._replace_combo_values(
                variant,
                self.store.jewelry_variants(display_slot, name),
                variant.currentText() or "无",
            )
        self._adapt_jewelry_column_widths()
        self.schedule_refresh()

    def _buff_skill_changed(self, row: int) -> None:
        skill = self.store.skill_display_name(self.combo_text(self.buff_table, row, 0))
        skill_combo = self.buff_table.cellWidget(row, 0)
        if isinstance(skill_combo, Combo) and skill_combo.currentText() != skill:
            self._set_combo_current(skill_combo, skill)
        level = self.buff_table.cellWidget(row, 1)
        if isinstance(level, Combo):
            levels = self.store.skill_levels(skill)
            current = level.currentText() or "无"
            if current not in set(str(value) for value in levels):
                provider = f"队员1 {self.second_job.currentText()}"
                current = self._team_effect_default_level(skill, provider, levels)
            self._replace_combo_values(level, levels, current)
        self._clear_exclusive_buff_conflicts(row, skill)
        self._adapt_misc_column_widths()
        self.schedule_refresh()

    def _buff_row_for_label(self, skill: str) -> dict[str, Any] | None:
        key = norm(skill)
        if not key or key == "无":
            return None
        cache = self.__dict__.setdefault("_buff_row_for_label_cache", {})
        if key in cache:
            return cache[key]
        for row in self.store.tables["buff"]:
            if key in buff_aliases(row):
                cache[key] = row
                return row
        cache[key] = None
        return None

    def _buff_exclusive_group_for_label(self, skill: str) -> str:
        row = self._buff_row_for_label(skill)
        return buff_exclusive_group(row) if row else ""

    def _filter_exclusive_buff_labels(self, labels: list[str]) -> list[str]:
        seen_groups: set[str] = set()
        out: list[str] = []
        for label in labels:
            group = self._buff_exclusive_group_for_label(label)
            if group:
                if group in seen_groups:
                    continue
                seen_groups.add(group)
            if label not in out:
                out.append(label)
        return out

    def _clear_exclusive_buff_conflicts(self, changed_row: int, skill: str) -> None:
        group = self._buff_exclusive_group_for_label(skill)
        if not group:
            return
        for row in range(self.buff_table.rowCount()):
            if row == changed_row:
                continue
            other_skill = self.combo_text(self.buff_table, row, 0)
            if self._buff_exclusive_group_for_label(other_skill) != group:
                continue
            skill_widget = self.buff_table.cellWidget(row, 0)
            if isinstance(skill_widget, Combo):
                self._set_combo_current(skill_widget, "无")
            level_widget = self.buff_table.cellWidget(row, 1)
            if isinstance(level_widget, Combo):
                self._replace_combo_values(level_widget, ["无"], "无")
            self._set_summary_item(self.buff_table, row, 2, "")

    def auto_current_job_buffs(self) -> None:
        if not hasattr(self, "buff_table"):
            return
        current_job = norm(self.second_job.currentText()) if hasattr(self, "second_job") else ""
        if current_job == "曜":
            current_job = "打坐曜"
        skill_labels: list[str] = []

        for row in self.store.tables["buff"]:
            if norm(row.get("来源")) == "通用" and norm(row.get("技能")) == "力量掌握":
                label = buff_display_label(row)
                if label not in skill_labels:
                    skill_labels.append(label)
                break

        if current_job:
            current_sources = set(
                self.store.job_buff_sources(
                    current_job,
                    self.first_job.currentText() if hasattr(self, "first_job") else "",
                )
            )
            for row in self.store.tables["buff"]:
                if norm(row.get("来源")) not in current_sources:
                    continue
                category = norm(row.get("类别"))
                if category == "个人buff":
                    rule = self.store.buff_activation_rule(row.get("来源"), row.get("技能"), category)
                    if rule and norm(rule.get("mode")) != "permanent":
                        continue
                label = buff_display_label(row)
                if label not in skill_labels:
                    skill_labels.append(label)
        preferred_aura = {"火舞": "火焰光环", "冰灵": "寒冰光环"}.get(current_job)
        if preferred_aura:
            skill_labels.sort(
                key=lambda label: 0
                if self._team_effect_skill_name(label) == preferred_aura
                else 1
            )
        skill_labels = self._filter_exclusive_buff_labels(skill_labels)

        skill_options = self.store.skill_names(self.first_job.currentText(), self.second_job.currentText())
        for row in range(self.buff_table.rowCount()):
            skill = skill_labels[row] if row < len(skill_labels) else "无"
            skill_widget = self.buff_table.cellWidget(row, 0)
            if isinstance(skill_widget, Combo):
                self._replace_combo_values(skill_widget, skill_options, skill)
            levels = self.store.skill_levels(skill)
            provider = f"队员1 {self.second_job.currentText()}"
            level = self._team_effect_default_level(skill, provider, levels)
            level_widget = self.buff_table.cellWidget(row, 1)
            if isinstance(level_widget, Combo):
                self._replace_combo_values(level_widget, levels, level)
            if skill == "无":
                self._set_summary_item(self.buff_table, row, 2, "")

        self._adapt_misc_column_widths()
        self.schedule_refresh()

    def clear_non_current_job_buffs(self) -> None:
        if not hasattr(self, "buff_table"):
            return
        for row in range(self.buff_table.rowCount()):
            skill = self.combo_text(self.buff_table, row, 0)
            if not skill or skill == "无" or self._is_current_job_buff(skill):
                continue
            skill_widget = self.buff_table.cellWidget(row, 0)
            if isinstance(skill_widget, Combo):
                self._set_combo_current(skill_widget, "无")
            level_widget = self.buff_table.cellWidget(row, 1)
            if isinstance(level_widget, Combo):
                self._replace_combo_values(level_widget, ["无"], "无")
            self._set_summary_item(self.buff_table, row, 2, "")
        self._adapt_misc_column_widths()
        self.schedule_refresh()

    def _is_current_job_buff(self, skill: str) -> bool:
        current_job = norm(self.second_job.currentText()) if hasattr(self, "second_job") else ""
        if not current_job:
            return False
        current_sources = set(
            self.store.job_buff_sources(
                current_job,
                self.first_job.currentText() if hasattr(self, "first_job") else "",
            )
        )
        key = norm(skill)
        for row in self.store.tables["buff"]:
            if key in buff_aliases(row) and norm(row.get("来源")) in current_sources:
                return True
        return False

    def _engraving_grade_changed(self, row: int, _text: str = "") -> None:
        if not hasattr(self, "engraving_table"):
            return
        value = self.engraving_table.cellWidget(row, 1)
        if isinstance(value, EngravingValueSlider):
            kind = self._engraving_main_kind(ENGRAVING_SLOTS[row])
            values = self.store.engraving_main_value_options(kind, self.combo_text(self.engraving_table, row, 0))
            value.set_values(values, value.currentText() if value.currentText() in values else "")
        self.schedule_refresh()

    def _engraving_extra_changed(self, row: int, _text: str = "") -> None:
        if not hasattr(self, "engraving_table"):
            return
        self.schedule_refresh()

    def apply_auto_glyph_a_extra(self, _text: str = "") -> None:
        if not hasattr(self, "glyph_a_table") or not hasattr(self, "auto_glyph_a_extra"):
            return
        value = self.auto_glyph_a_extra.currentText()
        for row in range(self.glyph_a_table.rowCount()):
            name = self.combo_text(self.glyph_a_table, row, 1)
            values = self._glyph_a_extra_options_for_name(name)
            self._replace_combo_values(self.glyph_a_table.cellWidget(row, 2), values, value if value in values else "无")
        self.schedule_refresh()

    def apply_auto_engraving_grade(self, _text: str = "") -> None:
        if not hasattr(self, "engraving_table") or not hasattr(self, "auto_engraving_grade"):
            return
        grade = self.auto_engraving_grade.currentText()
        for row in self._engraving_target_rows():
            self._set_combo_current(self.engraving_table.cellWidget(row, 0), grade)
            self._engraving_grade_changed(row)
        self.schedule_refresh()

    def apply_auto_engraving_extra(self, _text: str = "") -> None:
        if not hasattr(self, "engraving_table") or not hasattr(self, "auto_engraving_extra"):
            return
        extra = self.auto_engraving_extra.currentText()
        for row in range(self.engraving_table.rowCount()):
            self._set_combo_current(self.engraving_table.cellWidget(row, 2), extra)
            self._engraving_extra_changed(row)
        self.schedule_refresh()

    def apply_auto_engraving_tier(self, _text: str = "") -> None:
        if not hasattr(self, "engraving_table") or not hasattr(self, "auto_engraving_tier"):
            return
        tier = self.auto_engraving_tier.currentText()
        for row in range(self.engraving_table.rowCount()):
            widget = self.engraving_table.cellWidget(row, 3)
            if isinstance(widget, TierChoice):
                widget.setCurrentText(tier)
        self.schedule_refresh()

    def _engraving_target_rows(self) -> list[int]:
        target = self.auto_engraving_target.currentText() if hasattr(self, "auto_engraving_target") else "全部"
        groups = {
            "防具": {"头盔", "上装", "下装", "手套", "鞋子"},
            "武器": {"主手", "副手"},
            "饰品": {"项链", "耳环", "戒指1", "戒指2"},
            "三件套": {"翅膀", "尾巴", "印花"},
        }
        slots = groups.get(target)
        if not slots:
            return list(range(self.engraving_table.rowCount()))
        return [row for row, slot in enumerate(ENGRAVING_SLOTS) if slot in slots]

    @staticmethod
    def _attr_base_and_suffix(attr: str) -> tuple[str, str]:
        text = norm(attr)
        return (text[:-1], "%") if text.endswith("%") else (text, "")

    @staticmethod
    def _attack_attr_counterpart(attr: str, source_attack: str, target_attack: str) -> str:
        text = norm(attr)
        if source_attack == target_attack:
            return text
        pairs = [
            ("物攻", "魔攻"),
            ("物攻%", "魔攻%"),
            ("最小物攻", "最小魔攻"),
            ("最大物攻", "最大魔攻"),
            ("最小物攻%", "最小魔攻%"),
            ("最大物攻%", "最大魔攻%"),
        ]
        for physical, magic in pairs:
            if source_attack == "物攻" and target_attack == "魔攻" and text == physical:
                return magic
            if source_attack == "魔攻" and target_attack == "物攻" and text == magic:
                return physical
        return text

    @staticmethod
    def _glyph_keyword_for_attr(attr: str) -> str:
        return {
            "物攻": "破坏",
            "魔攻": "魔力",
            "力量": "巨熊",
            "敏捷": "疾风",
            "智力": "智慧",
            "体质": "健康",
        }.get(norm(attr), "")

    def _glyph_a_required_stat_keywords(self, profile: dict[str, Any]) -> list[str]:
        if norm(profile.get("first_job")) == "贤者":
            return ["疾风", "巨熊", "智慧"]
        return ["疾风", "智慧"] if profile.get("attack_attr") == "魔攻" else ["疾风", "巨熊"]

    def _reset_extreme_timeline_context(self, boss_name: str | None = None) -> None:
        """Rebuild extreme-team conversion caches from the currently edited build."""
        self.build = self.collect_build()
        selected_boss = norm(boss_name) or norm(self.build.get("boss")) or "无"
        base_build = self._extreme_base_build(self.build, selected_boss)
        context_signature = self._team_practical_cache_value({
            "base_build": base_build,
            "skill_build_defaults": load_skill_build_defaults(),
        })
        reuse_static_caches = (
            getattr(self, "_extreme_timeline_context_signature", None)
            == context_signature
        )
        reusable_cache_names = (
            "_extreme_personal_attrs_cache",
            "_extreme_personal_multiplier_cache",
            "_extreme_personal_breakdown_cache",
            "_extreme_converted_build_cache",
            "_extreme_skill_element_weight_cache",
            "_extreme_team_effect_template_cache",
            "_extreme_personal_buff_base_rows_cache",
            "_extreme_timeline_result_cache",
            "_extreme_timeline_seed_cache",
            "_extreme_timeline_panel_cache_pool",
        )
        preserved_caches = {
            name: getattr(self, name)
            for name in reusable_cache_names
            if reuse_static_caches and isinstance(getattr(self, name, None), dict)
        }
        _rows, base_summary = self.calculator.calculate(base_build)
        current_job = self._team_real_job(norm(self.build.get("second_job")))
        self._extreme_clean_base_build = copy.deepcopy(base_build)
        self._extreme_clean_base_summary = dict(base_summary)
        self._extreme_base_element_percent_value = self._extreme_base_element_percent(
            base_summary, current_job
        )
        self._extreme_element_resists = dict(
            base_build.get("element_resists", {"火": 0.0, "水": 0.0, "光": 0.0, "暗": 0.0})
        )
        self._extreme_monster_defense = as_number(base_build.get("monster_defense"))
        self._extreme_crit_resist_value = as_number(
            base_build.get("crit_resist_value", base_build.get("crit_resist", 0))
        )
        self._extreme_base_crit_percent = as_number(base_summary.get("crit_percent"))
        self._extreme_base_crit_damage_percent = as_number(base_summary.get("crit_damage_percent"))
        for name in reusable_cache_names:
            setattr(self, name, preserved_caches.get(name, {}))
        self._extreme_timeline_context_signature = context_signature

    @staticmethod
    def _cap_level_options(options: list[str], skill: Any) -> list[str]:
        cap = BuildEditor._team_effect_level_cap(skill)
        if cap <= 0:
            return options
        capped = [option for option in options if option == "无" or as_number(option, 9999) <= cap]
        numeric = [int(as_number(option)) for option in capped if as_number(option, default=float("nan")) == as_number(option, default=float("nan"))]
        if numeric and max(numeric) < cap:
            return [str(level) for level in range(1, cap + 1)]
        if capped:
            return capped
        return [str(level) for level in range(1, cap + 1)]

    def _refresh_team_cdr_usage_visibility(self) -> None:
        if not hasattr(self, "team_cdr_usage_box") or not hasattr(self, "team_effect_table"):
            return
        selected = self._selected_team_cdr_skills()
        for skill, widget in self.team_cdr_usage_widgets.items():
            widget.setVisible(skill in selected)
        glyph_visible = False
        for skill, check in getattr(self, "team_cdr_glyph_checks", {}).items():
            visible = skill in selected
            check.setVisible(visible)
            glyph_visible = glyph_visible or visible
        if hasattr(self, "team_cdr_glyph_label"):
            self.team_cdr_glyph_label.setVisible(glyph_visible)
        apply_visible = False
        for skill, check in getattr(self, "team_cdr_apply_checks", {}).items():
            visible = skill in selected
            check.setVisible(visible)
            apply_visible = apply_visible or visible
        if hasattr(self, "team_cdr_apply_label"):
            self.team_cdr_apply_label.setVisible(apply_visible)
        self.team_cdr_usage_box.setVisible(bool(selected))

    def _selected_team_cdr_skills(self) -> set[str]:
        selected: set[str] = set()
        if not hasattr(self, "team_effect_table"):
            return selected
        for row in range(self.team_effect_table.rowCount()):
            skill = self._team_effect_skill_name(self.combo_text(self.team_effect_table, row, 2))
            if skill in TEAM_CDR_USAGE_SKILLS:
                selected.add(skill)
        return selected

    @staticmethod
    def _generic_team_manual_effect(skill: str) -> bool:
        return False

    def _buff_attrs_for_team_effect(
        self,
        skill: str,
        level: Any,
        include_personal_buffs: bool = False,
        provider: str = "",
    ) -> dict[str, float]:
        bucket: dict[str, float] = {}
        provider_job = self._team_provider_job(provider) if provider else ""
        allowed_sources = (
            {*self.store.job_buff_sources(provider_job), "通用"}
            if provider_job
            else set()
        )
        for row in self.calculator._buff_rows(skill, level):
            category = norm(row.get("类别"))
            if allowed_sources and norm(row.get("来源")) not in allowed_sources:
                continue
            if category == "个人buff" and not include_personal_buffs:
                continue
            if category not in {"团队buff", "个人buff"} and not include_personal_buffs:
                continue
            attr = norm(row.get("属性"))
            if not attr:
                continue
            bucket[attr] = bucket.get(attr, 0.0) + as_number(row.get("表格数值"))
        if self._team_effect_skill_name(skill) == '战吼' and provider_job == '毁灭者':
            mastery = self._team_combat_skill_level('力量掌握', provider)
            if mastery is None or mastery > 0:
                bucket['无属性%'] = min(18.0, max(0.0, as_number(level)-1)*3)
        return bucket

    @staticmethod
    def _freeze_duration_from_text(text: str) -> float:
        num = r"([0-9]+(?:\.[0-9]+)?)(?:;[0-9]+-[0-9]+)?"
        patterns = [
            rf"(?:结冰|冻结|冰冻|冻僵)(?:状态)?(?:持续|时间)?\s*{num}\s*秒",
            rf"冻结状态持续\s*{num}\s*秒",
            rf"结冰时间\s*{num}\s*秒",
            rf"额外\s*{num}\s*秒(?:的)?(?:冰冻|冻结|结冰|冻僵)",
            rf"{num}\s*秒内被(?:冰冻|冻结|结冰|冻僵)",
            rf"{num}\s*秒(?:的)?(?:冰冻|冻结|结冰|冻僵)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return as_number(match.group(1))
        return 0.0

    @staticmethod
    def _duration_from_text(text: str) -> float:
        num = r"([0-9]+(?:\.[0-9]+)?)(?:;[0-9]+-[0-9]+)?"
        patterns = [
            rf"持续时间\s*{num}\s*秒",
            rf"持续\s*{num}\s*秒",
            rf"{num}\s*秒钟",
            rf"{num}\s*秒内",
            rf"{num}\s*秒",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return as_number(match.group(1))
        return 0.0

    @staticmethod
    def _resist_percent_from_sentence(sentence: str, raw_element: str) -> float:
        element_words = [f"{raw_element}属性"]
        if raw_element == "火":
            element_words.append("火焰属性")
        if not any(word in sentence for word in element_words):
            return 0.0
        num = r"([0-9]+(?:\.[0-9]+)?)"
        patterns = [
            rf"(?:{raw_element}属性|{raw_element}焰属性)?(?:抗性|抵抗力|抵抗)[^%]{{0,16}}(?:降低|减少|下降)[^0-9%]{{0,8}}{num}\s*%",
            rf"(?:降低|减少|下降)[^0-9%]{{0,8}}(?:目标)?{num}\s*%[^。\n|]{{0,12}}(?:{raw_element}属性|{raw_element}焰属性)(?:抗性|抵抗力|抵抗)",
        ]
        for pattern in patterns:
            match = re.search(pattern, sentence)
            if match:
                return as_number(match.group(1))
        return 0.0

    def _write_team_effect_rows(self, rows: list[dict[str, Any]]) -> None:
        provider_options = self._team_provider_options()
        self.team_effect_table.blockSignals(True)
        try:
            for row in range(self.team_effect_table.rowCount()):
                item = rows[row] if row < len(rows) else {}
                enabled = self.team_effect_table.item(row, 0)
                if enabled:
                    enabled.setCheckState(Qt.Checked if item.get("enabled") and self._team_effect_skill_name(item.get("skill")) != "无" else Qt.Unchecked)
                provider_text = item.get("provider") or (provider_options[row + 1] if row < TEAM_MEMBER_ROWS and len(provider_options) > row + 1 else "通用")
                provider = self.team_effect_table.cellWidget(row, 1)
                if isinstance(provider, Combo):
                    self._replace_combo_values(provider, provider_options, provider_text)
                effects = self._team_effect_options(provider_text)
                skill_text = self._team_effect_option_for_skill(effects, item.get("skill", "无"))
                skill = self.team_effect_table.cellWidget(row, 2)
                if isinstance(skill, Combo):
                    self._replace_combo_values(skill, effects, skill_text)
                levels = self._team_effect_levels(skill_text, provider_text)
                level_text = item.get("level") if item.get("level") in set(str(value) for value in levels) else self._team_effect_default_level(skill_text, provider_text, levels)
                level = self.team_effect_table.cellWidget(row, 3)
                if isinstance(level, Combo):
                    self._replace_combo_values(level, levels, level_text)
                cooldown = self.team_effect_table.cellWidget(row, 4)
                if isinstance(cooldown, QDoubleSpinBox):
                    cooldown.blockSignals(True)
                    value = float(as_number(item.get("cooldown")))
                    cooldown.setProperty("base_cooldown", value)
                    cooldown.setValue(value)
                    cooldown.blockSignals(False)
                duration = self.team_effect_table.cellWidget(row, 5)
                if isinstance(duration, QDoubleSpinBox):
                    duration.blockSignals(True)
                    duration.setValue(float(as_number(item.get("duration"))))
                    duration.blockSignals(False)
                for col in (6, 7, 8):
                    self._set_summary_item(self.team_effect_table, row, col, "", align_right=col in (7, 8))
        finally:
            self.team_effect_table.blockSignals(False)

    def _sort_team_effect_table_by_contribution(self, contribution_rows: list[dict[str, Any]]) -> bool:
        if getattr(self, "_sorting_team_effect_table", False) or not hasattr(self, "team_effect_table"):
            return False
        current_rows = self._team_effect_row_states()
        contribution_by_row = {
            int(as_number(row.get("row"), -1)): as_number(row.get("contribution"))
            for row in contribution_rows
            if as_number(row.get("row"), -1) >= 0
        }

        indexed_rows = list(enumerate(current_rows))

        def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            original_row, row = item
            skill = self._team_effect_skill_name(row.get("skill"))
            if not skill or skill == "无":
                return (2, 999, original_row)
            contribution = contribution_by_row.get(original_row, 0.0)
            contribution_group = 1 if abs(contribution) <= 1e-9 else 0
            return (contribution_group, self._team_provider_sort_index(row.get("provider", "")), original_row)

        sorted_rows = [row for _original, row in sorted(indexed_rows, key=sort_key)]
        if [self._team_effect_row_signature(row) for row in sorted_rows] == [self._team_effect_row_signature(row) for row in current_rows]:
            return False
        self._sorting_team_effect_table = True
        try:
            self._write_team_effect_rows(sorted_rows)
        finally:
            self._sorting_team_effect_table = False
        self._refresh_team_cdr_usage_visibility()
        return True

    def _request_team_effect_auto_sort(self) -> None:
        self._team_auto_sort_after_refresh = True

    def _set_all_team_effects_enabled(self, enabled: bool) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        self.team_effect_table.blockSignals(True)
        try:
            for row in range(self.team_effect_table.rowCount()):
                item = self.team_effect_table.item(row, 0)
                if item is None:
                    continue
                skill = self._team_effect_skill_name(self.combo_text(self.team_effect_table, row, 2))
                item.setCheckState(Qt.Checked if enabled and skill != "无" else Qt.Unchecked)
        finally:
            self.team_effect_table.blockSignals(False)
        self.schedule_refresh()

    def _sort_team_effect_table_by_contribution_action(self, _checked: bool = False) -> None:
        contribution_rows = getattr(self, "_last_team_contribution_rows", [])
        if self._sort_team_effect_table_by_contribution(contribution_rows):
            self.schedule_refresh()

    def _sort_team_effect_table_by_type_action(self, _checked: bool = False) -> None:
        if getattr(self, "_sorting_team_effect_table", False) or not hasattr(self, "team_effect_table"):
            return
        current_rows = self._team_effect_row_states()

        def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int]:
            original_row, row = item
            skill = self._team_effect_skill_name(row.get("skill"))
            if not skill or skill == "无":
                return (2, 9, 999, original_row)
            category_order = self._team_effect_option_sort_key(row.get("skill", ""))[0]
            enabled_group = 0 if row.get("enabled") else 1
            return (enabled_group, category_order, self._team_provider_sort_index(row.get("provider", "")), original_row)

        sorted_rows = [row for _original, row in sorted(enumerate(current_rows), key=sort_key)]
        if [self._team_effect_row_signature(row) for row in sorted_rows] == [self._team_effect_row_signature(row) for row in current_rows]:
            return
        self._sorting_team_effect_table = True
        try:
            self._write_team_effect_rows(sorted_rows)
        finally:
            self._sorting_team_effect_table = False
        self._refresh_team_cdr_usage_visibility()
        self.schedule_refresh()

    def _auto_populate_team_effects_from_members(self, schedule: bool = True) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        provider_options = self._team_provider_options()
        generated: list[dict[str, Any]] = []
        for member_row in range(TEAM_MEMBER_ROWS):
            job = self.combo_text(self.team_member_table, member_row, 1)
            if not job or job == "无":
                continue
            provider = f"队员{member_row + 1} {job}"
            team_skills = self._team_skills_for_job(job)
            for skill in team_skills:
                if not self._team_skill_matches_current_build(job, skill):
                    continue
                generated.append({"enabled": True, "provider": provider, "skill": skill})

        manual_rows: list[dict[str, Any]] = []
        for row in range(self.team_effect_table.rowCount()):
            provider_text = self.combo_text(self.team_effect_table, row, 1)
            if self._is_auto_team_provider(provider_text):
                continue
            skill = self.combo_text(self.team_effect_table, row, 2)
            skill_name = self._team_effect_skill_name(skill)
            if skill_name in TEAM_MANUAL_DEBUFFS:
                enabled = self.team_effect_table.item(row, 0)
                manual_rows.append({
                    "enabled": enabled.checkState() == Qt.Checked if enabled else True,
                    "provider": provider_text or "通用",
                    "skill": self._team_manual_effect_label(skill_name),
                    "level": self.combo_text(self.team_effect_table, row, 3),
                    "cooldown": self.double_value(self.team_effect_table, row, 4),
                    "duration": self.double_value(self.team_effect_table, row, 5),
                })

        rows = generated + manual_rows
        self.team_effect_table.blockSignals(True)
        for row in range(TEAM_EFFECT_ROWS):
            item = rows[row] if row < len(rows) else {}
            enabled = self.team_effect_table.item(row, 0)
            if enabled:
                enabled.setCheckState(Qt.Checked if item.get("enabled") and item.get("skill") else Qt.Unchecked)
            provider_text = item.get("provider") or (provider_options[row + 1] if row < TEAM_MEMBER_ROWS and len(provider_options) > row + 1 else "通用")
            provider = self.team_effect_table.cellWidget(row, 1)
            if isinstance(provider, Combo):
                self._replace_combo_values(provider, provider_options, provider_text)
            effects = self._team_effect_options(provider_text)
            skill_text = self._team_effect_option_for_skill(effects, item.get("skill", "无"))
            skill = self.team_effect_table.cellWidget(row, 2)
            if isinstance(skill, Combo):
                self._replace_combo_values(skill, effects, skill_text)
            levels = self._team_effect_levels(skill_text, provider_text)
            level_text = item.get("level") or self._team_effect_default_level(skill_text, provider_text, levels)
            level = self.team_effect_table.cellWidget(row, 3)
            if isinstance(level, Combo):
                self._replace_combo_values(level, levels, level_text)
            timing = self._team_effect_timing(provider_text, skill_text, level_text)
            cooldown = self.team_effect_table.cellWidget(row, 4)
            if isinstance(cooldown, QDoubleSpinBox):
                cooldown.blockSignals(True)
                base_cooldown = float(as_number(item.get("cooldown", timing.get("cooldown", 0))))
                cooldown.setProperty("base_cooldown", base_cooldown)
                cooldown.setValue(base_cooldown)
                cooldown.blockSignals(False)
            duration = self.team_effect_table.cellWidget(row, 5)
            if isinstance(duration, QDoubleSpinBox):
                duration.blockSignals(True)
                duration.setValue(float(as_number(item.get("duration", timing.get("duration", 0)))))
                duration.blockSignals(False)
            self._set_summary_item(self.team_effect_table, row, 6, "")
            self._set_summary_item(self.team_effect_table, row, 7, "", align_right=True)
            self._set_summary_item(self.team_effect_table, row, 8, "", align_right=True)
        self.team_effect_table.blockSignals(False)
        self._refresh_team_cdr_usage_visibility()
        if schedule:
            self._request_team_effect_auto_sort()
            self.schedule_refresh()

    @staticmethod
    def _is_auto_team_provider(provider: str) -> bool:
        return bool(re.match(r"^队员\d+\s+", norm(provider)))

    def _sync_skill_burning_hand_column(self) -> None:
        if hasattr(self, "skill_sim_table"):
            self.skill_sim_table.setColumnHidden(5, norm(self.second_job.currentText()) != "火舞")

    def _sync_skill_cooldown_displays(self) -> None:
        if not hasattr(self, "skill_sim_table"):
            return
        for row in range(SKILL_SIM_ROWS):
            cooldown = self.skill_sim_table.cellWidget(row, 3)
            if not isinstance(cooldown, QDoubleSpinBox):
                continue
            fixed_cooldown = self._skill_fixed_cooldown(self.combo_text(self.skill_sim_table, row, 0))
            base = as_number(cooldown.property("base_cooldown"), cooldown.value())
            cooldown.blockSignals(True)
            if fixed_cooldown is not None:
                cooldown.setProperty("base_cooldown", fixed_cooldown)
                cooldown.setValue(fixed_cooldown)
                cooldown.setReadOnly(True)
            else:
                special_cooldown = self._skill_special_effective_cooldown(self.combo_text(self.skill_sim_table, row, 0))
                cooldown.setReadOnly(special_cooldown is not None)
                cooldown.setValue(special_cooldown if special_cooldown is not None else self._skill_effective_cooldown(self.combo_text(self.skill_sim_table, row, 0), base))
            cooldown.blockSignals(False)

    def _refresh_skill_sim_options(self) -> None:
        if not hasattr(self, "skill_sim_table"):
            return
        self._sync_skill_burning_hand_column()
        skills = self.store.skill_damage_names(self.second_job.currentText())
        for row in range(SKILL_SIM_ROWS):
            skill_combo = self.skill_sim_table.cellWidget(row, 0)
            if isinstance(skill_combo, Combo):
                current = skill_combo.currentText() or "无"
                if (
                    ("ex" in norm(current).lower() and norm(current) not in SKILL_SIM_ALLOWED_EX_SKILLS)
                    or current not in skills
                    or self._skill_build_level_for_skill_name(current) == 0
                ):
                    current = "无"
                self._replace_combo_values(skill_combo, skills, current)
            self._skill_sim_skill_changed(row)

    def _shared_base_skill_names(self, profile: dict[str, Any]) -> set[str]:
        family = norm(profile.get("family"))
        jobs = [
            norm(row.get("二转职业"))
            for row in self.store.tables["job_map"]
            if norm(row.get("转换大职业")) == family
        ]
        if len(jobs) < 2:
            return set()
        counts: dict[str, int] = {}
        for job in jobs:
            for name in set(self.store.skill_damage_entry_names(job)):
                counts[name] = counts.get(name, 0) + 1
        threshold = max(2, (len(jobs) * 3 + 3) // 4)
        return {name for name, count in counts.items() if count >= threshold}

    def _auto_skill_dps_candidates_for_job(
        self,
        job: str,
        summary: dict[str, float],
    ) -> list[tuple[float, int, str, str]]:
        job = self._team_real_job(job)
        profile = self._extreme_profile_for_job(job)
        weighted_skill_panel = None
        preferred_type = "M" if profile.get("attack_attr") == "魔攻" else "P"
        base_skill_names = self._shared_base_skill_names(profile)
        job_name = norm(job)
        skip_names = SKILL_SIM_AUTO_SKIP_BY_JOB.get(job_name, set())
        forced_last = SKILL_SIM_AUTO_FORCED_LAST_BY_JOB.get(job_name, "")
        primary_candidates: list[tuple[float, int, str, str]] = []
        fallback_candidates: list[tuple[float, int, str, str]] = []
        for skill in self.store.skill_damage_names(job_name):
            weighted_skill_panel = self.store.skill_damage_panel_profile(job_name, skill)
            if skill == "无":
                continue
            skill_entry = self.store._skill_damage_entry(job_name, skill)
            skill_name = norm(skill_entry.get("name") if skill_entry else skill)
            if skill_name in skip_names or skill_name == forced_last:
                continue
            best: tuple[float, int, str, str] | None = None
            for level in self.store.skill_damage_levels(job_name, skill):
                level_data = self.store.skill_damage_level_data(job_name, skill, level)
                if not level_data:
                    continue
                required_level = int(as_number(level_data[1]))
                if required_level > 70:
                    continue
                base_cooldown = self._skill_adjusted_cooldown_for_job(job_name, skill, level_data)
                fixed_cooldown = self._skill_fixed_cooldown_for_job(job_name, skill)
                cooldown = fixed_cooldown if fixed_cooldown is not None else self._skill_effective_cooldown_for_job(job_name, skill, base_cooldown, 0.0)
                if cooldown <= 0:
                    continue
                multiplier = self._skill_adjusted_multiplier_for_job(job_name, skill, level)
                score = self._skill_level_effective(
                    level_data,
                    summary,
                    multiplier=multiplier,
                    preferred_type=preferred_type,
                    weighted_panel=weighted_skill_panel,
                ) / cooldown
                if score <= 0:
                    continue
                item = (score, required_level, skill, level)
                if best is None or item[:2] > best[:2]:
                    best = item
            if best:
                duplicate_group, duplicate_limit = self._skill_sim_auto_duplicate_limit_for_job(job_name, skill)
                repeat = duplicate_limit if duplicate_group else 1
                if best[1] >= 15 and skill_name not in base_skill_names:
                    primary_candidates.extend([best] * repeat)
                else:
                    fallback_candidates.extend([best] * repeat)
        candidates = primary_candidates + fallback_candidates
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected: list[tuple[float, int, str, str]] = []
        used_groups: set[str] = set()
        duplicate_counts: dict[str, int] = {}
        for item in candidates:
            group = self._skill_sim_auto_exclusive_group_for_job(job_name, item[2])
            if group and group in used_groups:
                continue
            if group:
                used_groups.add(group)
            duplicate_group, duplicate_limit = self._skill_sim_auto_duplicate_limit_for_job(job_name, item[2])
            if duplicate_group:
                count = duplicate_counts.get(duplicate_group, 0)
                if count >= duplicate_limit:
                    continue
                duplicate_counts[duplicate_group] = count + 1
            selected.append(item)
            if len(selected) >= SKILL_SIM_ROWS - (1 if forced_last else 0):
                break
        selected = self._skill_sim_force_include_candidates(
            job_name,
            selected,
            candidates,
            max_rows=SKILL_SIM_ROWS - (1 if forced_last else 0),
        )
        return selected

    def _refresh_buff_option_lists(self) -> None:
        if not hasattr(self, "buff_table"):
            return
        skills = self.store.skill_names(self.first_job.currentText(), self.second_job.currentText())
        for row in range(self.buff_table.rowCount()):
            skill = self.buff_table.cellWidget(row, 0)
            if isinstance(skill, Combo):
                current = self.store.skill_display_name(skill.currentText() or "无")
                self._replace_combo_values(skill, skills, current)
            self._buff_skill_changed(row)
        self._adapt_misc_column_widths()

    def _refresh_equipment_option_lists(self) -> None:
        if not hasattr(self, "equipment_table"):
            return
        for index, slot in enumerate(EQUIPMENT_SLOTS):
            row = index * 2
            name_combo = self.equipment_table.cellWidget(row, 0)
            if isinstance(name_combo, Combo):
                self._replace_combo_values(
                    name_combo,
                    self.store.equipment_names(self.first_job.currentText(), self.second_job.currentText(), slot),
                    name_combo.currentText(),
                )
            self._equipment_name_changed(row)
        self._adapt_equipment_column_widths()
        self._adapt_jewelry_column_widths()

    def _core_conversion_changed(self, _text: str = "") -> None:
        if not hasattr(self, "core_conversion"):
            return
        conversion = self.core_conversion.currentText()
        ratio_values = self.store.core_ratio_ranks(conversion)
        coeff_values = self.store.core_coeff_ranks(conversion)
        if hasattr(self, "core_ratio"):
            current = self.core_ratio.currentText()
            fallback = self._highest_number_option(ratio_values)
            self._replace_combo_values(self.core_ratio, ratio_values, current if current in set(ratio_values) else fallback)
        if hasattr(self, "core_coeff"):
            current = self.core_coeff.currentText()
            fallback = self._highest_number_option(coeff_values)
            self._replace_combo_values(self.core_coeff, coeff_values, current if current in set(coeff_values) else fallback)
        self.schedule_refresh()

    @staticmethod
    def _replace_combo_values(combo: Combo, values: list[Any], current: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems([str(v) for v in values if v not in (None, "")])
        if current and combo.findText(current) < 0:
            combo.insertItem(0, current)
        combo.setCurrentText(current or (str(values[0]) if values else ""))
        combo.setToolTip(combo.currentText())
        if hasattr(combo, "_show_text_start"):
            QTimer.singleShot(0, combo._show_text_start)
        popup_width = option_width([*values, current], combo.minimumWidth(), 520)
        combo.view().setMinimumWidth(popup_width)
        combo.blockSignals(False)

    def _talisman_grade_changed(self, row: int) -> None:
        grade = self.combo_text(self.talisman_table, row, 1) or "无"
        name = self.talisman_table.cellWidget(row, 2)
        extra = self.talisman_table.cellWidget(row, 3)
        if isinstance(name, Combo):
            self._replace_combo_values(name, self.store.talisman_names(grade), "无")
        if isinstance(extra, Combo):
            self._replace_combo_values(extra, ["无"], "无")
        self.schedule_refresh()

    def _talisman_name_changed(self, row: int) -> None:
        grade = self.combo_text(self.talisman_table, row, 1) or "无"
        name = self.combo_text(self.talisman_table, row, 2) or "无"
        extra = self.talisman_table.cellWidget(row, 3)
        if isinstance(extra, Combo):
            options = self.store.talisman_extra_options(grade, name)
            current = extra.currentText()
            self._replace_combo_values(
                extra, options, current if current in options else "无"
            )
        self.schedule_refresh()

    @staticmethod
    def _set_combo_current(combo: QComboBox | None, value: Any) -> None:
        if not isinstance(combo, QComboBox):
            return
        text = "" if value is None else str(value)
        combo.blockSignals(True)
        if text and combo.findText(text) < 0:
            combo.insertItem(0, text)
        combo.setCurrentText(text)
        combo.setToolTip(combo.currentText())
        if hasattr(combo, "_show_text_start"):
            QTimer.singleShot(0, combo._show_text_start)
        combo.blockSignals(False)

    @staticmethod
    def _set_spin_value(widget: QWidget | None, value: int) -> None:
        if isinstance(widget, QSpinBox):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)

    @staticmethod
    def _first_existing(options: list[str], choices: list[str], default: str = "无") -> str:
        option_set = set(options)
        for choice in choices:
            if choice in option_set:
                return choice
        return default if default in option_set else (options[0] if options else default)

    @staticmethod
    def _choose_by_keywords(options: list[str], groups: list[tuple[str, ...]], forbidden: tuple[str, ...] = ()) -> str:
        candidates = [option for option in options if option and option != "无"]
        for group in groups:
            for option in candidates:
                if all(keyword in option for keyword in group) and not any(keyword in option for keyword in forbidden):
                    return option
        return candidates[0] if candidates else "无"

    @staticmethod
    def _highest_number_option(options: list[str], default: str = "无") -> str:
        best = default
        best_value = -1.0
        for option in options:
            text = norm(option)
            if not text or text == "无":
                continue
            numbers = re.findall(r"\d+(?:\.\d+)?", text)
            value = float(numbers[-1]) if numbers else -1.0
            if value > best_value:
                best = text
                best_value = value
        return best

    def _job_profile(self) -> dict[str, Any]:
        first_job = self.first_job.currentText()
        second_job = self.second_job.currentText()
        cache_key = (first_job, second_job)
        cache = getattr(self, "_job_profile_exact_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._job_profile_exact_cache = cache
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return dict(cached)
        family = self.store.job_family(second_job, first_job)
        effective = next((row for row in self.store.tables["effective"] if norm(row.get("职业")) == norm(second_job)), {})
        raw_attack = norm(effective.get("攻击1")) or "物攻"
        attack_attrs = ["物攻", "魔攻"] if raw_attack == "双攻" else [raw_attack if raw_attack in {"物攻", "魔攻"} else "物攻"]
        attack_attr = attack_attrs[0]
        element = norm(effective.get("属性1"))
        element_attr = f"{element}攻%" if element and element != "无" else ""
        main_stat = self._best_source_stat(family, attack_attr)
        result = {
            "first_job": first_job,
            "second_job": second_job,
            "family": family,
            "attack_attr": attack_attr,
            "attack_attrs": attack_attrs,
            "element_attr": element_attr,
            "main_stat": main_stat,
        }
        cache[cache_key] = dict(result)
        return result

    def _best_source_stat(self, family: str, target_attr: str) -> str:
        cache_key = (norm(family), norm(target_attr))
        cache = getattr(self, "_best_source_stat_exact_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._best_source_stat_exact_cache = cache
        cached = cache.get(cache_key)
        if isinstance(cached, str):
            return cached
        best_stat = "智力" if target_attr == "魔攻" else "力量"
        best_ratio = -1.0
        for row in self.store.tables["conversion"]:
            if norm(row.get("一转职业")) != norm(family):
                continue
            source = norm(row.get("来源属性"))
            target = norm(row.get("目标属性"))
            if source not in SOURCE_ATTRS or target != target_attr:
                continue
            ratio = float(row.get("转换比例") or 0)
            if ratio > best_ratio:
                best_stat = source
                best_ratio = ratio
        cache[cache_key] = best_stat
        return best_stat

    def _suffix_choice(self, options: list[str], slot: str, profile: dict[str, Any]) -> str:
        attack_suffix = "魔力II" if profile["attack_attr"] == "魔攻" else "破坏II"
        stat_suffix = {
            "力量": "巨熊II",
            "敏捷": "疾风II",
            "智力": "智慧II",
            "体质": "生命II",
        }.get(profile["main_stat"], "生命II")
        if slot in {"主手", "副手"}:
            choices = [attack_suffix, stat_suffix, "致命II", "勇者II"]
        else:
            choices = [stat_suffix, "生命II", "致命II", attack_suffix]
        return self._first_existing(options, choices)

    def _best_enchant(self, options: list[str], attr: str) -> str:
        candidates = [option for option in options if option.startswith(attr)]
        return candidates[0] if candidates else "无"

    def _best_enchant_with_value(self, options: list[str], attr: str, value_text: str) -> str:
        candidates = [option for option in options if option.startswith(attr) and value_text in option]
        return candidates[0] if candidates else self._best_enchant(options, attr)

    @staticmethod
    def _auto_enchant_stat_attrs(profile: dict[str, Any]) -> list[str]:
        if norm(profile.get("first_job")) == "贤者":
            return ["力量", "智力"]
        return ["敏捷", "智力"] if profile.get("attack_attr") == "魔攻" else ["力量", "敏捷"]

    def _append_best_enchants(self, options: list[str], attrs: list[str], values: list[str] | None = None) -> list[str]:
        out: list[str] = []
        values = values or [""] * len(attrs)
        for attr, value in zip(attrs, values):
            if not attr:
                continue
            choice = self._best_enchant_with_value(options, attr, value) if value else self._best_enchant(options, attr)
            if choice != "无" and choice not in out:
                out.append(choice)
        return out

    def _enchant_plan(self, options: list[str], profile: dict[str, Any]) -> list[str]:
        out = self._append_best_enchants(options, [*self._auto_enchant_stat_attrs(profile), "HP"])
        return (out + ["无", "无", "无", "无", "无"])[:5]

    def _weapon_enchant_plan(self, options: list[str], profile: dict[str, Any]) -> list[str]:
        attack = profile["attack_attr"]
        element = profile["element_attr"].replace("%", "")
        elements = [element] if element else []
        if (
            norm(profile.get("first_job")) == "光明之怒"
            or norm(profile.get("second_job")) == "烈"
        ):
            elements = ["光攻", "暗攻"] if norm(profile.get("first_job")) == "光明之怒" else ["暗攻", "火攻"]
            return self._append_best_enchants(
                options,
                ["物攻", *elements, "力量", "敏捷"],
                ["2.88", "8", "8", "4.5", "4.5"],
            )
        if norm(profile.get("first_job")) == "炼金术士" or norm(profile.get("second_job")) in {"炼金圣士", "药剂师"}:
            elements = ["水攻", "火攻"]
        if norm(profile.get("second_job")) == "冰灵" and "火攻" not in elements:
            elements.append("火攻")
        out: list[str] = []
        if norm(profile.get("first_job")) == "贤者":
            if elements:
                out.extend(self._append_best_enchants(options, ["物攻", "魔攻", *elements], ["2.88", "2.88", *["8"] * len(elements)]))
            else:
                out.extend(self._append_best_enchants(options, ["物攻", "魔攻", "致命"], ["5.75", "5.75", "17"]))
            out.extend(self._append_best_enchants(options, ["力量", "智力"]))
            return (out + ["无", "无", "无", "无", "无"])[:5]
        if elements:
            out.extend(self._append_best_enchants(options, [attack, *elements], ["2.88", *["8"] * len(elements)]))
        else:
            out.extend(self._append_best_enchants(options, [attack, "致命"], ["5.75", "17"]))
        out.extend(self._append_best_enchants(options, self._auto_enchant_stat_attrs(profile)))
        return (out + ["无", "无", "无", "无", "无"])[:5]

    @staticmethod
    def _apply_grade_button_style(button: QPushButton, grade: str) -> None:
        styles = {
            "L": ("#b91c1c", "#991b1b", "#fff7ed"),
            "S": ("#2563eb", "#1d4ed8", "#eff6ff"),
            "A": ("#ea580c", "#c2410c", "#fff7ed"),
            "无": ("#64748b", "#475569", "#f8fafc"),
        }
        bg, border, fg = styles.get(grade, styles["无"])
        button.setStyleSheet(
            f"background: {bg}; border: 1px solid {border}; color: {fg}; "
            "font-weight: 800; border-radius: 7px; padding: 6px 13px;"
        )

    def _set_equipment_name(self, row: int, slot: str, name: str) -> None:
        name_combo = self.equipment_table.cellWidget(row, 0)
        if isinstance(name_combo, Combo):
            self._set_combo_current(name_combo, name)
        suffix_combo = self.equipment_table.cellWidget(row, 1)
        if isinstance(suffix_combo, Combo):
            self._replace_combo_values(
                suffix_combo,
                self.store.suffix_names(self.first_job.currentText(), self.second_job.currentText(), name),
                suffix_combo.currentText() or "无",
            )

    def _set_jewelry_name(self, row: int, slot: str, name: str) -> None:
        name_combo = self.jewelry_table.cellWidget(row, 0)
        if isinstance(name_combo, Combo):
            self._set_combo_current(name_combo, name)
        variant_combo = self.jewelry_table.cellWidget(row, 1)
        if isinstance(variant_combo, Combo):
            self._replace_combo_values(
                variant_combo,
                self.store.jewelry_variants(slot, name),
                variant_combo.currentText() or "无",
            )

    def _equipment_choice_for_tier(self, level: str, job: str, slot: str) -> str:
        return self.store.preferred_equipment_name(level, job, slot)

    def _jewelry_choice_for_tier(self, level: str, slot: str) -> str:
        names: list[str] = []
        for row in self.store.tables["jewelry"]:
            if norm(row.get("等级")) != level or norm(row.get("饰品类别")) != norm(slot):
                continue
            name = norm(row.get("饰品名称"))
            if name and name not in names:
                names.append(name)
        return names[0] if names else "无"

    @staticmethod
    def _auto_equip_target_options(level: str) -> list[str]:
        return [label for label, _kind, _tier in AUTO_EQUIP_TARGETS.get(norm(level), [])]

    @classmethod
    def _default_auto_equip_target(cls, level: str) -> str:
        values = cls._auto_equip_target_options(level)
        for keyword in ("L装备+首饰", "L装备", "S装备"):
            for value in values:
                if keyword in value:
                    return value
        return values[0] if values else "无"

    def _best_title_for_profile(
        self,
        profile: dict[str, Any],
        current: str = "",
    ) -> str:
        best_title = norm(current)
        best_score = float("-inf")
        for title in self.store.titles():
            score = self._score_attrs_for_profile(
                self.calculator.title_attrs(title), profile
            )
            if score > best_score:
                best_title = title
                best_score = score
        return best_title

    def _apply_best_title_for_current_job(self) -> None:
        if not hasattr(self, "title_combo"):
            return
        self._set_combo_current(
            self.title_combo,
            self._best_title_for_profile(
                self._job_profile(), self.title_combo.currentText()
            ),
        )

    def _standard_card_profile(self) -> tuple[list[dict[str, str]], dict[str, int]]:
        cards = [
            {"name": name, "grade": "L"}
            for name in self.store.card_names()
        ]
        bonus_levels = {
            attr: 0 if norm(attr) in {"致命伤害", "爆伤"} else 25
            for attr in CARD_PREVIEW_ATTRS
        }
        return cards, bonus_levels

    def _apply_standard_card_profile_to_ui(self) -> None:
        _cards, bonus_levels = self._standard_card_profile()
        for row in range(self.card_table.rowCount()):
            self._set_card_grade(row, "L")
        for row, attr in enumerate(CARD_PREVIEW_ATTRS):
            self._set_spin_value(
                self.card_total_table.cellWidget(row, 2),
                bonus_levels[attr],
            )

    def apply_standard_opening_build(self) -> None:
        """Apply the shared level-70 baseline used by downstream simulations."""
        self._defer_refresh = True
        try:
            self._set_combo_current(self.auto_equip_level, "70")
            self._auto_equip_level_changed("70")
            self._set_combo_current(self.auto_equip_target, "黑龙L装备+首饰")
            self.auto_equip("70L", "all")

            profile = self._job_profile()
            attack_extra = self._first_existing(
                self.store.glyph_a_extra(), [profile["attack_attr"]]
            )
            self._set_combo_current(self.auto_glyph_a_extra, attack_extra)
            self.auto_glyphs()
            self.auto_runes()
            self._apply_standard_card_profile_to_ui()
            self._apply_best_title_for_current_job()
            self.build = self.collect_build()
        finally:
            self._defer_refresh = False
        self.refresh()

    def _auto_equip_level_changed(self, level: str) -> None:
        if not hasattr(self, "auto_equip_target"):
            return
        values = self._auto_equip_target_options(level)
        current = self.auto_equip_target.currentText()
        self._replace_combo_values(self.auto_equip_target, values, current if current in values else self._default_auto_equip_target(level))

    @staticmethod
    def _auto_equip_target_config(level: str, target: str) -> tuple[str, str]:
        for label, kind, tier in AUTO_EQUIP_TARGETS.get(norm(level), []):
            if norm(label) == norm(target):
                return kind, tier
        return "", ""

    def apply_auto_equip_selection(self) -> None:
        level = self.auto_equip_level.currentText() if hasattr(self, "auto_equip_level") else "70"
        target = self.auto_equip_target.currentText() if hasattr(self, "auto_equip_target") else ""
        kind, tier = self._auto_equip_target_config(level, target)
        if not kind or not tier:
            QMessageBox.warning(self, "一键配装", "请选择有效的配装目标。")
            return
        self.auto_equip(tier, kind)

    def auto_equip(self, tier: str, target_kind: str = "all") -> None:
        profile = self._job_profile()
        dragon_keyword = "霸龙" if tier.startswith("50") else "赞龙" if tier.startswith("60") else "黑龙"
        if target_kind in {"all", "equipment"}:
            equipment_job = profile["family"] if tier == "70S" else profile["first_job"]
            for index, slot in enumerate(EQUIPMENT_SLOTS):
                row = index * 2
                names = self.store.equipment_names(profile["first_job"], profile["second_job"], slot)
                name = self._equipment_choice_for_tier(tier, equipment_job, slot)
                if name == "无":
                    name = self._choose_by_keywords(names, [(dragon_keyword,)])
                self._set_equipment_name(row, slot, name)
                suffix_values = self.store.suffix_names(profile["first_job"], profile["second_job"], name)
                self._set_combo_current(self.equipment_table.cellWidget(row, 1), self._suffix_choice(suffix_values, slot, profile))
                self._set_spin_value(self.equipment_table.cellWidget(row, 2), 13 if slot in {"主手", "副手"} else 10)
                enchant_options = self.store.enchant_names(index >= 5)
                enchants = self._weapon_enchant_plan(enchant_options, profile) if slot in {"主手", "副手"} else self._enchant_plan(enchant_options, profile)
                for col, enchant in enumerate(enchants, start=3):
                    self._set_combo_current(self.equipment_table.cellWidget(row, col), enchant)

        if target_kind in {"all", "jewelry"}:
            variant = {"力量": "力敏", "敏捷": "力敏", "智力": "敏智", "体质": "智体"}.get(profile["main_stat"], "力敏")
            jewelry_keywords = [(dragon_keyword, "讨伐队")] if tier.endswith("S") else [(dragon_keyword, "猎人")]
            for index, slot in enumerate(JEWELRY_SLOTS):
                row = index * 2
                display_slot = "戒指" if slot.startswith("戒指") else slot
                names = self.store.jewelry_names(display_slot)
                name = self._jewelry_choice_for_tier(tier, display_slot)
                if name == "无":
                    name = self._choose_by_keywords(names, jewelry_keywords)
                self._set_jewelry_name(row, display_slot, name)
                variants = self.store.jewelry_variants(display_slot, name)
                self._set_combo_current(self.jewelry_table.cellWidget(row, 1), self._first_existing(variants, [variant, "力敏", "敏智", "智体"]))
                enchant_options = self.store.enchant_names(True) if display_slot == "戒指" else self.store.enchant_names(False)
                enchants = self._weapon_enchant_plan(enchant_options, profile) if display_slot == "戒指" else self._enchant_plan(enchant_options, profile)
                for col, enchant in enumerate(enchants, start=2):
                    self._set_combo_current(self.jewelry_table.cellWidget(row, col), enchant)
        self._adapt_equipment_column_widths()
        self._adapt_jewelry_column_widths()
        self.refresh()

    def _standard_glyph_loadout(
        self,
        profile: dict[str, Any],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        attack_name = "魔力" if profile["attack_attr"] == "魔攻" else "破坏"
        if norm(profile.get("first_job")) == "贤者":
            stat_names = ["疾风", "巨熊", "智慧"]
        elif profile["attack_attr"] == "魔攻":
            stat_names = ["疾风", "智慧"]
        else:
            stat_names = ["疾风", "巨熊"]
        wanted = unique(["最终", attack_name, *stat_names, "致命", "生命", "健康", "铁壁", "帷幕", "不屈", "极限", "冲击"])
        level = self._first_existing(self.store.glyph_a_levels(), ["70A", "60A", "50A", "40A"])
        names = self.store.glyph_a_names()
        used: set[str] = set()
        glyph_a: list[dict[str, str]] = []
        for keyword in wanted[:11]:
            available = [name for name in names if name not in used]
            name = self._choose_by_keywords(available, [(keyword,)])
            used.add(name)
            extra_options = self._glyph_a_extra_options_for_name(name)
            extra = self._first_existing(
                extra_options,
                [norm(profile.get("attack_attr"))],
            )
            glyph_a.append({"level": level, "name": name, "extra": extra})

        s_level = self._first_existing(self.store.glyph_s_levels(), ["70S", "60S"])
        secondary_stat = "体质" if profile["main_stat"] != "体质" else "敏捷"
        other_attack = "魔攻" if profile["attack_attr"] == "物攻" else "物攻"
        s_plan = [
            ("攻击", {profile["attack_attr"]: "8", other_attack: "3"}),
            ("上升", {profile["main_stat"]: "9", secondary_stat: "3"}),
            ("根源", {"HP": "8", "MP": "3"}),
            ("抵抗", {"致命抵抗": "3", "硬直抵抗": "3"}),
        ]
        glyph_s: list[dict[str, str]] = []
        for name_key, preferred_ranks in s_plan:
            name = self._choose_by_keywords(self.store.glyph_s_names(), [(name_key,)])
            base_options = [option for option in self.store.glyph_s_base_options(name) if option and option != "无"][:4]
            row = {
                "level": s_level,
                "name": name,
                "extra": "无",
            }
            for index in range(1, 5):
                attr = base_options[index - 1] if index <= len(base_options) else "无"
                row[f"base_attr{index}"] = attr
                row[f"rank{index}"] = preferred_ranks.get(attr, "1") if attr != "无" else "无"
            glyph_s.append(row)
        return glyph_a, glyph_s

    def auto_glyphs(self) -> None:
        glyph_a, glyph_s = self._standard_glyph_loadout(self._job_profile())
        for row, item in enumerate(glyph_a[: self.glyph_a_table.rowCount()]):
            self._set_combo_current(self.glyph_a_table.cellWidget(row, 0), item["level"])
            self._set_combo_current(self.glyph_a_table.cellWidget(row, 1), item["name"])
            extra_options = self._glyph_a_extra_options_for_name(item["name"])
            self._replace_combo_values(
                self.glyph_a_table.cellWidget(row, 2),
                extra_options,
                item["extra"],
            )
        for row, item in enumerate(glyph_s[: self.glyph_s_table.rowCount()]):
            self._set_combo_current(self.glyph_s_table.cellWidget(row, 0), item["level"])
            self._set_combo_current(self.glyph_s_table.cellWidget(row, 1), item["name"])
            base_options = self.store.glyph_s_base_options(item["name"])
            for col, key in ((2, "base_attr1"), (4, "base_attr2")):
                self._replace_combo_values(
                    self.glyph_s_table.cellWidget(row, col),
                    base_options,
                    item[key],
                )
            for col, key in ((3, "rank1"), (5, "rank2"), (6, "extra")):
                self._set_combo_current(self.glyph_s_table.cellWidget(row, col), item[key])
        self._refresh_glyph_a_name_options()
        self.refresh()

    def _standard_rune_loadout(
        self,
        profile: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        level = self._first_existing(self.store.rune_levels(), ["70", "60", "50"])
        rank = self._highest_number_option(self.store.rune_ranks())
        attack_plans = [
            [profile["attack_attr"], profile["attack_attr"], profile["main_stat"], "最终", "最终"],
            [profile["attack_attr"], profile["attack_attr"], "致命伤害", "最终", "最终"],
        ]
        defense_plans = [
            ["HP", "HP", "体质", "防御", "魔防"],
            ["HP", "HP", "体质", "防御", "魔防"],
        ]
        normal_runes: list[dict[str, Any]] = []
        for index in range(4):
            rune_type = "攻击" if index < 2 else "防御"
            attrs = attack_plans[index] if rune_type == "攻击" else defense_plans[index - 2]
            options = self.store.rune_attrs(rune_type)
            entries: list[dict[str, str]] = []
            for entry_index in range(6):
                attr = attrs[entry_index] if entry_index < 5 else "无"
                attr = self._first_existing(options, [attr])
                entries.append({"attr": attr, "rank": rank if attr != "无" else "无"})
            normal_runes.append({"type": rune_type, "level": level, "entries": entries})

        target = profile["attack_attr"]
        if norm(profile.get("second_job")) == "十字军":
            conversion_choices = ["魔防→魔攻"]
        else:
            conversion_choices = ["致命→物攻"] if target == "物攻" else ["魔防→魔攻"]
        conversion = self._first_existing([item for item in self.store.core_conversions() if norm(item) != "无"], conversion_choices)
        ratio_values = self.store.core_ratio_ranks(conversion)
        coeff_values = self.store.core_coeff_ranks(conversion)
        core = {
            "level": self._first_existing(self.store.core_levels(), ["70", "60"]),
            "conversion": conversion,
            "ratio_rank": self._first_existing(
                ratio_values, ["20"], self._highest_number_option(ratio_values)
            ),
            "coeff_rank": self._highest_number_option(coeff_values),
            "enhance": 10,
        }
        return normal_runes, core

    def auto_runes(self) -> None:
        normal_runes, core = self._standard_rune_loadout(self._job_profile())
        for index, item in enumerate(normal_runes):
            row = self._rune_level_row(index)
            self._set_combo_current(self.rune_table.cellWidget(row, 0), item["level"])
            for entry_index, entry in enumerate(item["entries"]):
                entry_row, attr_column, rank_column = self._rune_entry_cell(
                    index, entry_index
                )
                self._set_combo_current(
                    self.rune_table.cellWidget(entry_row, attr_column),
                    entry["attr"],
                )
                self._set_combo_current(
                    self.rune_table.cellWidget(entry_row, rank_column),
                    entry["rank"],
                )
        self._set_combo_current(self.core_level, core["level"])
        self._set_combo_current(self.core_conversion, core["conversion"])
        self._core_conversion_changed()
        self._set_combo_current(self.core_ratio, core["ratio_rank"])
        self._set_combo_current(self.core_coeff, core["coeff_rank"])
        self._set_spin_value(self.core_enhance, core["enhance"])
        self.refresh()

    def set_selected_card_grade(self, grade: str) -> None:
        checked_rows = {
            row for row in range(self.card_table.rowCount())
            if self.card_table.item(row, 0) and self.card_table.item(row, 0).checkState() == Qt.Checked
        }
        rows = sorted(checked_rows or {index.row() for index in self.card_table.selectedIndexes()})
        if not rows and self.card_table.currentRow() >= 0:
            rows = [self.card_table.currentRow()]
        for row in rows:
            self._set_card_grade(row, grade)
            item = self.card_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Unchecked)
        self._sync_card_select_all()
        self.refresh()

    def set_card_level_grade(self, level: int) -> None:
        grade = self.card_level_grade.currentText() if hasattr(self, "card_level_grade") else "L"
        changed = False
        for row in range(self.card_table.rowCount()):
            if self.store.card_level(self.card_name(row)) != int(level):
                continue
            self._set_card_grade(row, grade)
            changed = True
        if changed:
            self.refresh()

    def set_all_card_selected(self, state: int) -> None:
        if not hasattr(self, "card_table"):
            return
        checked = (state.value if hasattr(state, "value") else int(state)) == Qt.CheckState.Checked.value
        self.card_table.blockSignals(True)
        for row in range(self.card_table.rowCount()):
            item = self.card_table.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            if row < len(getattr(self, "card_grid_checks", [])):
                grid_check = self.card_grid_checks[row]
                grid_check.blockSignals(True)
                grid_check.setChecked(checked)
                grid_check.blockSignals(False)
        self.card_table.blockSignals(False)
        self._sync_card_select_all()

    def _set_card_grade(self, row: int, grade: str) -> None:
        self._set_combo_current(self.card_table.cellWidget(row, 1), grade)
        if row < len(getattr(self, "card_grid_grades", [])):
            grid_grade = self.card_grid_grades[row]
            grid_grade.blockSignals(True)
            self._set_combo_current(grid_grade, grade)
            grid_grade.blockSignals(False)

    def _set_card_grid_checked(self, row: int, state: int) -> None:
        item = self.card_table.item(row, 0)
        if item is None:
            return
        checked = (state.value if hasattr(state, "value") else int(state)) == Qt.CheckState.Checked.value
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _card_grid_grade_changed(self, row: int, grade: str) -> None:
        hidden_grade = self.card_table.cellWidget(row, 1)
        hidden_grade.blockSignals(True)
        self._set_combo_current(hidden_grade, grade)
        hidden_grade.blockSignals(False)
        self.schedule_refresh()

    def _card_selection_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            if item.row() < len(getattr(self, "card_grid_checks", [])):
                grid_check = self.card_grid_checks[item.row()]
                grid_check.blockSignals(True)
                grid_check.setChecked(item.checkState() == Qt.Checked)
                grid_check.blockSignals(False)
            self._sync_card_select_all()

    def _sync_card_select_all(self) -> None:
        if not hasattr(self, "card_select_all") or not hasattr(self, "card_table"):
            return
        all_checked = self.card_table.rowCount() > 0 and all(
            self.card_table.item(row, 0) and self.card_table.item(row, 0).checkState() == Qt.Checked
            for row in range(self.card_table.rowCount())
        )
        self.card_select_all.blockSignals(True)
        self.card_select_all.setChecked(all_checked)
        self.card_select_all.blockSignals(False)

    def _score_attrs_for_profile(self, attrs: dict[str, float], profile: dict[str, Any]) -> float:
        score = 0.0
        attack = profile["attack_attr"]
        main_stat = profile["main_stat"]
        element = profile["element_attr"]
        for attr, value in attrs.items():
            if abs(value) < 1e-9:
                continue
            if attr in {attack, f"{attack}%"}:
                score += value * (120 if attr.endswith("%") else 1.0)
            elif attr in {"最小物攻", "最大物攻"} and attack == "物攻":
                score += value * 0.5
            elif attr in {"最小魔攻", "最大魔攻"} and attack == "魔攻":
                score += value * 0.5
            elif attr == main_stat:
                score += value * 0.8
            elif attr == f"{main_stat}%":
                score += value * 80
            elif attr == "最终":
                score += value * 5
            elif attr in {"致命", "致命伤害"}:
                score += value * 0.2
            elif attr in {"致命%", "致命伤害%"}:
                score += value * 80
            elif element and attr == element:
                score += value * 100
        return score

    def auto_misc(self) -> None:
        profile = self._job_profile()
        self.collection_level.setValue(33)

        best_title = self.title_combo.currentText()
        best_score = float("-inf")
        for title in self.store.titles():
            score = self._score_attrs_for_profile(self.calculator.title_attrs(title), profile)
            if score > best_score:
                best_title = title
                best_score = score
        self._set_combo_current(self.title_combo, best_title)

        for row in range(self.mastery_table.rowCount()):
            widget = self.mastery_table.cellWidget(row, 1)
            if isinstance(widget, QSpinBox):
                self._set_spin_value(widget, widget.maximum())

        for row, slot in enumerate(FASHION_SLOTS):
            option_slot = "戒指" if slot.startswith("戒指") else slot
            options = self.store.fashion_options(option_slot)
            if slot in {"主手", "副手", "翅膀", "尾巴", "印花"}:
                choice = self._choose_by_keywords(options, [("70",)])
            elif slot == "精灵":
                choice = max(
                    options,
                    key=lambda option: self._score_attrs_for_profile(
                        self.calculator.fashion_attrs(option), profile
                    ),
                    default="无",
                )
            else:
                choice = self._choose_by_keywords(options, [("A",), ("阿尔泰亚",)])
            self._set_combo_current(self.fashion_table.cellWidget(row, 1), choice)

        skill_labels: list[str] = []
        for row in self.store.tables["buff"]:
            if norm(row.get("来源")) == norm(profile["second_job"]):
                label = buff_display_label(row)
                if label not in skill_labels:
                    skill_labels.append(label)
        skill_labels = self._filter_exclusive_buff_labels(skill_labels)
        for row in range(self.buff_table.rowCount()):
            skill = skill_labels[row] if row < len(skill_labels) else "无"
            self._set_combo_current(self.buff_table.cellWidget(row, 0), skill)
            levels = self.store.skill_levels(skill)
            provider = f"队员1 {profile['second_job']}"
            level = self._team_effect_default_level(skill, provider, levels)
            level_widget = self.buff_table.cellWidget(row, 1)
            if isinstance(level_widget, Combo):
                self._replace_combo_values(level_widget, levels, level)
        self.refresh()

    def schedule_refresh(self) -> None:
        if getattr(self, "_defer_refresh", False):
            return
        if self.pending:
            return
        self.pending = True
        QTimer.singleShot(80, self.refresh)

    def refresh(self) -> None:
        if getattr(self, "_defer_refresh", False):
            return
        self.pending = False
        self.build = self.collect_build()
        rows, summary = self.calculator.calculate(self.build)
        self.summary_labels["effective_panel"].setText(number_text(summary["effective_panel"], integer=True))
        self.summary_labels["final_percent"].setText(f"{summary['final_percent']:.1f}")
        self.summary_labels["crit_percent"].setText(f"{summary['crit_percent']:.1f}")
        self.summary_labels["crit_damage_percent"].setText(f"{summary['crit_damage_percent']:.3f}")
        self.crit_resist_rate.setText(f"{summary.get('crit_resist_percent', 0):.1f}%")
        row_map = {row.attr: row for row in rows}
        for row_index, attr in enumerate(DISPLAY_ATTRS):
            all_values = self._display_row_values(row_map, attr)
            values = [all_values[-2], all_values[-1]]
            for col, value in enumerate(values):
                item = self.result_table.item(row_index, col)
                if item is None:
                    item = QTableWidgetItem()
                    self.result_table.setItem(row_index, col, item)
                item.setText(attr_number_text(attr, value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.update_equipment_preview()
        self.update_card_preview()
        self.update_talisman_preview()
        self.update_glyph_rune_preview()
        self.update_misc_preview()
        self.update_skill_build_preview(summary)
        self.update_skill_mastery_preview()
        self.update_skill_sim_preview(summary)
        self.update_team_sim_preview(summary)

    @staticmethod
    def _display_row_values(row_map: dict[str, ResultRow], attr: str) -> list[float]:
        fields = ["base", "equipment", "card", "talisman", "glyph", "normal_rune", "core_mastery", "actual", "converted", "buffed"]
        if attr == "物攻":
            return [
                (getattr(row_map.get("最小物攻", ResultRow("x")), field) + getattr(row_map.get("最大物攻", ResultRow("x")), field)) / 2
                for field in fields
            ]
        if attr == "魔攻":
            return [
                (getattr(row_map.get("最小魔攻", ResultRow("x")), field) + getattr(row_map.get("最大魔攻", ResultRow("x")), field)) / 2
                for field in fields
            ]
        row = row_map.get(attr, ResultRow(attr))
        return [getattr(row, field) for field in fields]

    def update_card_preview(self) -> None:
        totals = {attr: 0.0 for attr in CARD_PREVIEW_ATTRS}
        for row in range(self.card_table.rowCount()):
            name = self.card_name(row)
            grade = self.combo_text(self.card_table, row, 1) or "无"
            attrs = self.calculator.card_attrs(name, grade)
            detail_parts: list[str] = []
            for offset, attr in enumerate(CARD_PREVIEW_ATTRS, start=2):
                value = attrs.get(attr, 0.0)
                totals[attr] += value
                if abs(value) > 1e-9:
                    detail_parts.append(f"{attr} {attr_number_text(attr, value)}")
                item = self.card_table.item(row, offset)
                if item is None:
                    item = QTableWidgetItem()
                    self.card_table.setItem(row, offset, item)
                item.setText(attr_number_text(attr, value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if row < len(getattr(self, "card_grid_details", [])):
                detail = " / ".join(detail_parts) or "当前品质无属性"
                self.card_grid_details[row].setText(detail)
                self.card_grid_details[row].setToolTip(detail)
        for row, attr in enumerate(CARD_PREVIEW_ATTRS):
            item = self.card_total_table.item(row, 1)
            if item is None:
                item = QTableWidgetItem()
                self.card_total_table.setItem(row, 1, item)
            item.setText(attr_number_text(attr, totals[attr]))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            level = self.spin_value(self.card_total_table, row, 2)
            if attr == "致命伤害":
                level = 0
            boosted = totals[attr] * (1 + level * 0.05)
            boosted_item = self.card_total_table.item(row, 3)
            if boosted_item is None:
                boosted_item = QTableWidgetItem()
                self.card_total_table.setItem(row, 3, boosted_item)
            boosted_item.setText(attr_number_text(attr, boosted))
            boosted_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def update_talisman_preview(self) -> None:
        if not hasattr(self, "talisman_table"):
            return
        for row, slot in enumerate(TALISMAN_SLOTS):
            item = {
                "slot": slot,
                "grade": self.combo_text(self.talisman_table, row, 1),
                "name": self.combo_text(self.talisman_table, row, 2),
                "extra": self.combo_text(self.talisman_table, row, 3),
            }
            self._set_summary_item(
                self.talisman_table,
                row,
                4,
                attr_summary(self.calculator.talisman_attrs(item), limit=999),
            )

    def update_equipment_preview(self) -> None:
        for index, slot in enumerate(EQUIPMENT_SLOTS):
            row = index * 2
            item = {
                "slot": slot,
                "name": self.combo_text(self.equipment_table, row, 0),
                "suffix": self.combo_text(self.equipment_table, row, 1),
                "enhance": self.spin_value(self.equipment_table, row, 2),
                "enchants": [self.combo_text(self.equipment_table, row, col) for col in range(3, 8) if self.combo_text(self.equipment_table, row, col)],
            }
            attrs = self.calculator.equipment_attrs(item, self.first_job.currentText(), self.second_job.currentText())
            summary = attr_summary(attrs, limit=999)
            self._set_summary_item(self.equipment_table, row + 1, 0, summary)
            self.equipment_table.setRowHeight(row + 1, self._detail_row_height(summary, base=40, chars_per_line=110))
        equipment_build = [
            {
                "slot": slot,
                "name": self.combo_text(self.equipment_table, index * 2, 0),
                "suffix": self.combo_text(self.equipment_table, index * 2, 1),
                "enhance": self.spin_value(self.equipment_table, index * 2, 2),
                "enchants": [],
            }
            for index, slot in enumerate(EQUIPMENT_SLOTS)
        ]
        equipment_sets = self.calculator.equipment_set_bonus_attrs(equipment_build, self.first_job.currentText(), self.second_job.currentText())
        equipment_set_text = attr_summary(equipment_sets, limit=999)
        self._set_summary_item(self.equipment_table, self.equipment_set_row, 0, equipment_set_text)
        self.equipment_table.setRowHeight(self.equipment_set_row, self._detail_row_height(equipment_set_text, base=40, chars_per_line=110))
        for index, slot in enumerate(JEWELRY_SLOTS):
            row = index * 2
            display_slot = "戒指" if slot.startswith("戒指") else slot
            item = {
                "slot": display_slot,
                "name": self.combo_text(self.jewelry_table, row, 0),
                "variant": self.combo_text(self.jewelry_table, row, 1),
                "enchants": [self.combo_text(self.jewelry_table, row, col) for col in range(2, 7) if self.combo_text(self.jewelry_table, row, col)],
            }
            attrs = self.calculator.jewelry_attrs(item)
            summary = attr_summary(attrs, limit=999)
            self._set_summary_item(self.jewelry_table, row + 1, 0, summary)
            self.jewelry_table.setRowHeight(row + 1, self._detail_row_height(summary, base=40, chars_per_line=110))
        jewelry_build = [
            {
                "slot": "戒指" if slot.startswith("戒指") else slot,
                "name": self.combo_text(self.jewelry_table, index * 2, 0),
                "variant": self.combo_text(self.jewelry_table, index * 2, 1),
                "enchants": [],
            }
            for index, slot in enumerate(JEWELRY_SLOTS)
        ]
        jewelry_sets = self.calculator.jewelry_set_bonus_attrs(jewelry_build)
        jewelry_set_text = attr_summary(jewelry_sets, limit=999)
        self._set_summary_item(self.jewelry_table, self.jewelry_set_row, 0, jewelry_set_text)
        self.jewelry_table.setRowHeight(self.jewelry_set_row, self._detail_row_height(jewelry_set_text, base=40, chars_per_line=110))

    def update_glyph_rune_preview(self) -> None:
        glyph_total: dict[str, float] = {}
        for row in range(self.glyph_a_table.rowCount()):
            item = {
                "level": self.combo_text(self.glyph_a_table, row, 0),
                "name": self.combo_text(self.glyph_a_table, row, 1),
                "extra": self.combo_text(self.glyph_a_table, row, 2),
            }
            attrs = self.calculator.glyph_a_attrs(item)
            self._set_summary_item(self.glyph_a_table, row, 3, attr_summary(attrs))
            self._merge_attrs(glyph_total, attrs)
        for row in range(self.glyph_s_table.rowCount()):
            item = {
                "level": self.combo_text(self.glyph_s_table, row, 0),
                "name": self.combo_text(self.glyph_s_table, row, 1),
                "base_attr1": self.combo_text(self.glyph_s_table, row, 2),
                "rank1": self.combo_text(self.glyph_s_table, row, 3),
                "base_attr2": self.combo_text(self.glyph_s_table, row, 4),
                "rank2": self.combo_text(self.glyph_s_table, row, 5),
                "extra": self.combo_text(self.glyph_s_table, row, 6),
            }
            attrs = self.calculator.glyph_s_attrs(item)
            self._set_summary_item(self.glyph_s_table, row, 7, attr_summary(attrs))
            self._merge_attrs(glyph_total, attrs)

        rune_total: dict[str, float] = {}
        for index in range(4):
            row = self._rune_level_row(index)
            detail_row = self._rune_detail_row(index)
            entries = []
            for i in range(6):
                entry_row, attr_column, rank_column = self._rune_entry_cell(index, i)
                entries.append({"attr": self.combo_text(self.rune_table, entry_row, attr_column), "rank": self.combo_text(self.rune_table, entry_row, rank_column)})
            rune = {"type": "攻击" if index < 2 else "防御", "level": self.combo_text(self.rune_table, row, 0), "entries": entries}
            attrs = self.calculator.rune_attrs_for_item(rune, self.core_enhance.value())
            summary = attr_summary(attrs, limit=999)
            self._set_summary_item(self.rune_table, detail_row, 0, summary)
            self.rune_table.setRowHeight(detail_row, self._detail_row_height(summary, base=28, chars_per_line=130))
            self._merge_attrs(rune_total, attrs)

        self._fit_rune_table_height()

        self._populate_double_column_total_table(self.glyph_total_table, glyph_total)
        self._populate_nonzero_total_table(self.rune_total_table, rune_total)
        self._fit_table_height(self.glyph_total_table)
        self._fit_table_height(self.rune_total_table)

        preview = self.calculator.core_rune_preview(self.collect_build())
        if preview:
            cap_text = number_text(preview.get("max_deduct", 0), integer=True) or "无"
            boost_text = number_text(preview.get("normal_boost", 0)) or "0"
            self.core_preview.setText(
                f"扣除 {preview['source_attr']} {number_text(preview['deduct'], integer=True)}；"
                f"获得 {preview['target_attr']} {number_text(preview['gain'], integer=True)}；"
                f"扣除上限 {cap_text}；"
                f"普通石板强化 +{boost_text}%"
            )
        else:
            self.core_preview.setText("")

    def update_misc_preview(self) -> None:
        self.collection_preview.setText(attr_summary(self.calculator.collection_attrs(self.collection_level.value()), limit=999))
        self.title_preview.setText(attr_summary(self.calculator.title_attrs(self.title_combo.currentText()), limit=999))
        for row, name in enumerate(MASTERY_NAMES):
            attrs = self.calculator.mastery_attrs(name, self.spin_value(self.mastery_table, row, 1))
            self._set_summary_item(self.mastery_table, row, 2, attr_summary(attrs))
        for row in range(self.fashion_table.rowCount()):
            attrs = self.calculator.fashion_attrs(self.combo_text(self.fashion_table, row, 1))
            self._set_summary_item(self.fashion_table, row, 2, attr_summary(attrs))
        for row in range(self.buff_table.rowCount()):
            attrs = self.calculator.buff_attrs(self.combo_text(self.buff_table, row, 0), self.combo_text(self.buff_table, row, 1))
            self._set_summary_item(self.buff_table, row, 2, attr_summary(attrs))
        if hasattr(self, "engraving_table"):
            engraving_total: dict[str, float] = {}
            for row, slot in enumerate(ENGRAVING_SLOTS):
                main_value = self.engraving_value(self.engraving_table, row, 1)
                extra_tier, extra_rank, extra_value = self._engraving_extra_selection(
                    self.engraving_table, row
                )
                attrs = self.calculator.engraving_attrs({
                    "slot": slot,
                    "grade": self.combo_text(self.engraving_table, row, 0),
                    "main_value": main_value,
                    "secondary_value": self._engraving_secondary_value(
                        slot,
                        main_value,
                        self.combo_text(self.engraving_table, row, 0),
                        self.combo_text(self.engraving_table, row, 1),
                    ),
                    "extra": self.combo_text(self.engraving_table, row, 2),
                    "extra_tier": extra_tier,
                    "extra_rank": extra_rank,
                    "extra_value": extra_value,
                })
                self._set_summary_item(self.engraving_table, row, 4, attr_summary(attrs))
                self._merge_attrs(engraving_total, attrs)
            if hasattr(self, "engraving_total_table"):
                self._populate_double_column_total_table(self.engraving_total_table, engraving_total)

    @staticmethod
    def _merge_skill_burst_support_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order_keys: list[str] = []
        for action in actions:
            skill_key = BuildEditor._skill_burst_skill_key(action.get("skill"))
            if not skill_key:
                continue
            if skill_key not in merged:
                new_action = dict(action)
                new_action["skill"] = skill_key
                new_action["attrs"] = dict(action.get("attrs", {}))
                new_action["resist_attrs"] = dict(action.get("resist_attrs", {}))
                merged[skill_key] = new_action
                order_keys.append(skill_key)
                continue
            current = merged[skill_key]
            current["order"] = min(as_number(current.get("order")), as_number(action.get("order")))
            current["base_cooldown"] = max(as_number(current.get("base_cooldown")), as_number(action.get("base_cooldown")))
            current["duration"] = max(as_number(current.get("duration")), as_number(action.get("duration")))
            current["stackable_resist"] = bool(current.get("stackable_resist") or action.get("stackable_resist"))
            current["mandatory_uptime"] = bool(current.get("mandatory_uptime") or action.get("mandatory_uptime"))
            current["special_cdr"] = bool(
                current.get("special_cdr") or action.get("special_cdr")
            )
            current["pinned_cast_schedule"] = bool(
                current.get("pinned_cast_schedule")
                or action.get("pinned_cast_schedule")
            )
            resistance_cooldowns = [
                value
                for value in (
                    as_number(current.get("resistance_cooldown")),
                    as_number(action.get("resistance_cooldown")),
                )
                if value > 0
            ]
            if resistance_cooldowns:
                current["resistance_cooldown"] = min(resistance_cooldowns)
            if action.get("scheduled_casts"):
                current_casts = list(current.get("scheduled_casts", ()))
                current["scheduled_casts"] = sorted({
                    (
                        max(0.0, as_number(start)),
                        max(max(0.0, as_number(start)), as_number(end)),
                    )
                    for start, end in (*current_casts, *action.get("scheduled_casts", ()))
                })
            current["initial_available_at"] = min(
                as_number(current.get("initial_available_at"), 1.0e12),
                as_number(action.get("initial_available_at"), 1.0e12),
            )
            current["team_objective_effect"] = bool(
                current.get("team_objective_effect")
                or action.get("team_objective_effect")
            )
            current_objective_attrs = current.setdefault("team_objective_attrs", {})
            for attr, value in action.get("team_objective_attrs", {}).items():
                key = norm(attr)
                current_objective_attrs[key] = max(
                    as_number(current_objective_attrs.get(key)),
                    as_number(value),
                )
            for key in ("summon_state", "stackable_summon", "state_only"):
                if action.get(key) is not None:
                    current[key] = action.get(key)
            if action.get("periodic_buff"):
                current["periodic_buff"] = True
                for key in (
                    "buff_marker", "active_multiplier", "inactive_multiplier",
                    "element_scope", "skill_scope", "cast_skill", "display_name", "game_cooldown",
                    "triggered_only", "aura_toggle", "display_level",
                ):
                    if action.get(key) is not None:
                        current[key] = action.get(key)
                current_display_attrs = dict(current.get("display_attrs", {}))
                for attr, value in action.get("display_attrs", {}).items():
                    current_display_attrs[norm(attr)] = as_number(value)
                current["display_attrs"] = current_display_attrs
            tags = [part for part in norm(current.get("type")).split("/") if part]
            for part in norm(action.get("type")).split("/"):
                if part and part not in tags:
                    tags.append(part)
            current["type"] = "/".join(tags)
            for attr, value in action.get("attrs", {}).items():
                key = norm(attr)
                current_attrs = current.setdefault("attrs", {})
                current_attrs[key] = max(as_number(current_attrs.get(key)), as_number(value))
            for attr, value in action.get("resist_attrs", {}).items():
                key = norm(attr)
                current_resists = current.setdefault("resist_attrs", {})
                current_resists[key] = max(as_number(current_resists.get(key)), as_number(value))
            if not isinstance(current.get("metric"), dict) and isinstance(action.get("metric"), dict):
                current["metric"] = action.get("metric")
            current["effective_score"] = max(as_number(current.get("effective_score")), as_number(action.get("effective_score")))
            current["damage_base_cooldown"] = max(as_number(current.get("damage_base_cooldown")), as_number(action.get("damage_base_cooldown")))
        return sorted((merged[key] for key in order_keys), key=lambda action: as_number(action.get("order")))

    @staticmethod
    def _filter_skill_burst_support_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not actions:
            return []
        keep_indexes: set[int] = set()
        for index, action in enumerate(actions):
            attrs = action.get("attrs", {})
            has_resist = bool(action.get("resist_attrs"))
            has_amp = any(key in BuildEditor._team_amp_attr_keys() and abs(as_number(value)) > 1e-9 for key, value in attrs.items())
            has_freeze = any(key in BuildEditor._team_freeze_attr_keys() and abs(as_number(value)) > 1e-9 for key, value in attrs.items())
            has_critical_reduction = any(
                norm(key)
                in {"减致命抵抗%", "减致命一击抵抗%", "减暴击抵抗%"}
                and abs(as_number(value)) > 1e-9
                for key, value in attrs.items()
            )
            has_defense_reduction = any(
                canonical_defense_reduction_key(key)
                and abs(as_number(value)) > 1e-9
                for key, value in attrs.items()
            )
            has_guardian_phantom = BuildEditor._skill_burst_skill_key(action.get("skill")) in SKILL_BURST_GUARDIAN_PHANTOM_GENERATORS
            if (
                has_resist
                or has_amp
                or has_freeze
                or has_critical_reduction
                or has_defense_reduction
                or has_guardian_phantom
                or action.get("periodic_buff")
                or action.get("special_cdr")
                or action.get("summon_state")
            ):
                keep_indexes.add(index)
        return [action for index, action in enumerate(actions) if index in keep_indexes]

    @staticmethod
    def _dedup_team_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        full_coverage_winners: dict[str, tuple[tuple[str, str], int]] = {}
        for index, effect in enumerate(effects):
            key = BuildEditor._team_effect_skill_name(effect.get("skill"))
            if BuildEditor._team_practical_effect_is_stackable(effect):
                continue
            coverage = BuildEditor._team_effect_coverage(
                effect.get("cooldown", 0),
                effect.get("duration", 0),
            )
            if coverage < 1.0 - 1e-9:
                continue
            provider = norm(effect.get("provider"))
            provider_job = re.sub(r"^队员\d+\s*", "", provider).strip()
            candidate = ((provider_job, provider), index)
            current = full_coverage_winners.get(key)
            if current is None or candidate[0] < current[0]:
                full_coverage_winners[key] = candidate

        out: list[dict[str, Any]] = []
        for index, effect in enumerate(effects):
            key = BuildEditor._team_effect_skill_name(effect.get("skill"))
            if BuildEditor._team_practical_effect_is_stackable(effect):
                out.append(effect)
                continue
            coverage = BuildEditor._team_effect_coverage(effect.get("cooldown", 0), effect.get("duration", 0))
            if (
                coverage >= 1.0 - 1e-9
                and full_coverage_winners.get(key, (("", ""), -1))[1] != index
            ):
                continue
            out.append(effect)
        return out

    @staticmethod
    def _add_resist_allocation(
        allocations: dict[tuple[Any, str, str], dict[str, float]],
        key: tuple[Any, str, str],
        attr: str,
        value: float,
    ) -> None:
        if abs(value) <= 1e-9:
            return
        row = allocations.setdefault(key, {})
        row[attr] = row.get(attr, 0.0) + value

    @staticmethod
    def _ordered_resist_coverage(items: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
        if not items:
            return []
        if len(items) == 1:
            item = items[0]
            cooldown = as_number(item.get("cooldown"))
            coverage = BuildEditor._resist_item_coverage_for_order(
                item, cooldown if cooldown > 0 else 0.0
            )
            return [(item, min(1.0, coverage))] if coverage > 0 else []
        sorted_items = sorted(
            items,
            key=lambda item: (-as_number(item.get("value")), as_number(item.get("order")), as_number(item.get("row"))),
        )
        reference_period = next((as_number(item.get("cooldown")) for item in sorted_items if as_number(item.get("cooldown")) > 0), 0.0)
        remaining = 1.0
        allocations: list[tuple[dict[str, Any], float]] = []
        for item in sorted_items:
            if remaining <= 1e-9:
                break
            coverage = BuildEditor._resist_item_coverage_for_order(item, reference_period)
            if coverage <= 0:
                continue
            used = min(remaining, coverage)
            allocations.append((item, used))
            remaining -= used
        return allocations

    @staticmethod
    def _resist_item_coverage_for_order(item: dict[str, Any], reference_period: float) -> float:
        cd = max(0.0, as_number(item.get("cooldown")))
        duration = max(0.0, as_number(item.get("duration")))
        if cd <= 0:
            return 1.0
        if duration <= 0:
            return 0.0
        period = reference_period if reference_period > 0 else cd
        proc_chance = max(
            0.0,
            min(
                1.0,
                as_number(
                    item.get("proc_chance"),
                    team_effect_proc_chance(item.get("skill")),
                ),
            ),
        )
        return max(0.0, min(1.0, duration / period)) * proc_chance

    @staticmethod
    def _percent_text(value: float) -> str:
        return f"{int(round(value))}%" if abs(value - round(value)) < 0.05 else f"{value:.1f}%"

    @staticmethod
    def _compact_damage_text(value: float) -> str:
        number = max(0.0, as_number(value))
        if number >= 1_000_000:
            return f"{number / 1_000_000:.2f}M"
        if number >= 1_000:
            return f"{number / 1_000:.2f}K"
        return f"{number:.2f}"

    @staticmethod
    def _raw_percent_text(value: float) -> str:
        return f"{int(value)}%" if abs(value - int(value)) < 1e-9 else f"{value:g}%"

    @staticmethod
    def _cooldown_text(value: float) -> str:
        if abs(value) < 1e-9:
            return "0"
        return str(int(value)) if abs(value - int(value)) < 1e-9 else f"{value:g}"

    def _populate_nonzero_total_table(self, table: QTableWidget, totals: dict[str, float]) -> None:
        rows = [(attr, display_value(totals, attr)) for attr in DISPLAY_ATTRS]
        rows = [(attr, value) for attr, value in rows if abs(value) > 1e-9]
        table.setRowCount(len(rows))
        for row, (attr, value) in enumerate(rows):
            table.setRowHeight(row, EDIT_ROW_HEIGHT)
            table.setItem(row, 0, self._item(attr))
            self._set_summary_item(table, row, 1, attr_number_text(attr, value), align_right=True)

    def _populate_double_column_total_table(self, table: QTableWidget, totals: dict[str, float]) -> None:
        rows = [(attr, display_value(totals, attr)) for attr in DISPLAY_ATTRS]
        rows = [(attr, value) for attr, value in rows if abs(value) > 1e-9]
        if not rows:
            rows = [("无", 0.0)]
        group_count = 3
        row_count = max(1, (len(rows) + group_count - 1) // group_count)
        groups = [rows[index * row_count:(index + 1) * row_count] for index in range(group_count)]
        table.setRowCount(row_count)
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["属性", "数据", "属性", "数据", "属性", "数据"])
        for row in range(row_count):
            table.setRowHeight(row, EDIT_ROW_HEIGHT)
            for col in range(6):
                table.setItem(row, col, QTableWidgetItem(""))
            for group_index, group in enumerate(groups):
                if row >= len(group):
                    continue
                attr, value = group[row]
                attr_col = group_index * 2
                value_col = attr_col + 1
                table.setItem(row, attr_col, self._item(attr))
                self._set_summary_item(table, row, value_col, "" if attr == "无" else attr_number_text(attr, value), align_right=True)
        for col, width in enumerate([82, 90, 82, 90, 82, 90]):
            table.setColumnWidth(col, width)

    @staticmethod
    def _merge_attrs(target: dict[str, float], source: dict[str, float]) -> None:
        for attr, value in source.items():
            target[attr] = target.get(attr, 0.0) + value

    @staticmethod
    def _tint_skill_cell(table: QTableWidget, row: int, col: int, skill: str) -> None:
        skill_name = norm(skill)
        color = SkillBurstTimelineWidget.skill_cell_color(skill_name) if skill_name else QColor("#f7fbfc")
        widget = table.cellWidget(row, col)
        if isinstance(widget, QComboBox):
            widget.setStyleSheet(
                f"QComboBox {{ background-color:{color.name()}; color:#172033; }}"
            )
        item = table.item(row, col)
        if item is not None:
            item.setBackground(color)
            item.setForeground(QColor("#172033"))

    @staticmethod
    def _set_summary_item(table: QTableWidget, row: int, col: int, text: Any, align_right: bool = False) -> None:
        item = table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            table.setItem(row, col, item)
        display_text = "" if text is None else str(text)
        item.setText(display_text)
        item.setToolTip(display_text)
        if align_right:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        else:
            item.setForeground(QColor("#4d6073"))
            item.setBackground(QColor("#f7fbfc"))

    @staticmethod
    def _set_detail_label(label: QLabel, text: str) -> None:
        label.setText(text)
        label.setToolTip(text)

    @staticmethod
    def _detail_row_height(text: str, base: int = 82, chars_per_line: int = 38) -> int:
        if not text:
            return base
        lines = max(1, (text_units(text) + chars_per_line - 1) // chars_per_line)
        return max(base, 20 + lines * 14)

    def combo_text(self, table: QTableWidget, row: int, col: int) -> str:
        widget = table.cellWidget(row, col)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()
        if isinstance(widget, (EngravingValueSlider, TierChoice)):
            return widget.currentText().strip()
        combo = getattr(widget, "_combo", None)
        return combo.currentText().strip() if isinstance(combo, QComboBox) else ""

    def spin_value(self, table: QTableWidget, row: int, col: int) -> int:
        widget = table.cellWidget(row, col)
        return int(widget.value()) if isinstance(widget, QSpinBox) else 0

    def double_value(self, table: QTableWidget, row: int, col: int) -> float:
        widget = table.cellWidget(row, col)
        return float(widget.value()) if isinstance(widget, QDoubleSpinBox) else 0.0

    def engraving_value(self, table: QTableWidget, row: int, col: int) -> float:
        widget = table.cellWidget(row, col)
        if isinstance(widget, EngravingValueSlider):
            return self._engraving_option_value(widget.currentText())
        if isinstance(widget, QComboBox):
            return self._engraving_option_value(widget.currentText())
        if isinstance(widget, QDoubleSpinBox):
            return float(widget.value())
        return 0.0

    def card_name(self, row: int) -> str:
        item = self.card_table.verticalHeaderItem(row)
        return item.text().strip() if item else ""

    def collect_build(self) -> dict[str, Any]:
        existing_team_sim = self.build.get("team_sim", {}) if isinstance(self.build.get("team_sim", {}), dict) else {}
        build: dict[str, Any] = {
            "first_job": self.first_job.currentText(),
            "second_job": self.second_job.currentText(),
            "crit_resist_value": self.crit_resist.value(),
            "element_resist_mode": self.element_resist_mode.currentText() if hasattr(self, "element_resist_mode") else "全部",
            "element_resists": dict(getattr(self, "element_resists", {"火": 0.0, "水": 0.0, "光": 0.0, "暗": 0.0})),
            "element_resist_percent": float(self.element_resist.value()) if hasattr(self, "element_resist") else 0.0,
            "monster_defense": int(self.monster_defense.value()) if hasattr(self, "monster_defense") else 0,
            "monster_defense_rate": float(self.monster_defense_rate.value()) if hasattr(self, "monster_defense_rate") else 0.0,
            "boss": self.boss_combo.currentText() if hasattr(self, "boss_combo") else "无",
            "equipment": [],
            "jewelry": [],
            "glyph_a": [],
            "glyph_s": [],
            "normal_runes": [],
            "core_rune": {
                "level": self.core_level.currentText(),
                "conversion": self.core_conversion.currentText(),
                "ratio_rank": self.core_ratio.currentText(),
                "coeff_rank": self.core_coeff.currentText(),
                "enhance": self.core_enhance.value(),
            },
            "collection_level": self.collection_level.value(),
            "mastery": [],
            "title": self.title_combo.currentText(),
            "fashion": [],
            "buffs": [],
            "engravings": [],
            "skill_build": copy.deepcopy(self.build.get("skill_build", {})) if isinstance(self.build.get("skill_build", {}), dict) else {},
            "skill_mastery_build": copy.deepcopy(self.build.get("skill_mastery_build", {})) if isinstance(self.build.get("skill_mastery_build", {}), dict) else {},
            # The skill-build page is created lazily. Preserve a saved glyph setup
            # until its table exists, otherwise the first refresh after startup
            # silently discards it.
            "skill_glyphs": copy.deepcopy(self.build.get("skill_glyphs", {})) if isinstance(self.build.get("skill_glyphs", {}), dict) else {},
            "skill_multiplier_overrides": copy.deepcopy(self.build.get("skill_multiplier_overrides", {})) if isinstance(self.build.get("skill_multiplier_overrides", {}), dict) else {},
            "skill_sim": copy.deepcopy(self.build.get("skill_sim", {})) if isinstance(self.build.get("skill_sim", {}), (list, dict)) else {},
            "skill_sim_settings": copy.deepcopy(self.build.get("skill_sim_settings", {})) if isinstance(self.build.get("skill_sim_settings", {}), dict) else {},
            "team_sim": copy.deepcopy(existing_team_sim) if existing_team_sim else {"members": [], "effects": []},
            "card_grades": [],
            "card_bonus_levels": {},
            "talismans": [],
            "free_inputs": {"percent": {}, "stats": {}, "stat_percent": {}},
        }
        for index, slot in enumerate(EQUIPMENT_SLOTS):
            row = index * 2
            build["equipment"].append({
                "slot": slot,
                "name": self.combo_text(self.equipment_table, row, 0),
                "suffix": self.combo_text(self.equipment_table, row, 1),
                "enhance": self.spin_value(self.equipment_table, row, 2),
                "enchants": [self.combo_text(self.equipment_table, row, col) for col in range(3, 8) if self.combo_text(self.equipment_table, row, col)],
            })
        for index, slot in enumerate(JEWELRY_SLOTS):
            row = index * 2
            display_slot = "戒指" if slot.startswith("戒指") else slot
            build["jewelry"].append({
                "slot": display_slot,
                "name": self.combo_text(self.jewelry_table, row, 0),
                "variant": self.combo_text(self.jewelry_table, row, 1),
                "enchants": [self.combo_text(self.jewelry_table, row, col) for col in range(2, 7) if self.combo_text(self.jewelry_table, row, col)],
            })
        for row in range(11):
            build["glyph_a"].append({
                "level": self.combo_text(self.glyph_a_table, row, 0),
                "name": self.combo_text(self.glyph_a_table, row, 1),
                "extra": self.combo_text(self.glyph_a_table, row, 2),
            })
        for row in range(4):
            build["glyph_s"].append({
                "level": self.combo_text(self.glyph_s_table, row, 0),
                "name": self.combo_text(self.glyph_s_table, row, 1),
                "base_attr1": self.combo_text(self.glyph_s_table, row, 2),
                "rank1": self.combo_text(self.glyph_s_table, row, 3),
                "base_attr2": self.combo_text(self.glyph_s_table, row, 4),
                "rank2": self.combo_text(self.glyph_s_table, row, 5),
                "extra": self.combo_text(self.glyph_s_table, row, 6),
            })
        for row in range(4):
            table_row = self._rune_level_row(row)
            entries = []
            for i in range(6):
                entry_row, attr_column, rank_column = self._rune_entry_cell(row, i)
                entries.append({"attr": self.combo_text(self.rune_table, entry_row, attr_column), "rank": self.combo_text(self.rune_table, entry_row, rank_column)})
            build["normal_runes"].append({
                "type": "攻击" if row < 2 else "防御",
                "level": self.combo_text(self.rune_table, table_row, 0),
                "entries": entries,
            })
        for row, name in enumerate(MASTERY_NAMES):
            build["mastery"].append({"name": name, "level": self.spin_value(self.mastery_table, row, 1)})
        for row, slot in enumerate(FASHION_SLOTS):
            build["fashion"].append({"slot": slot, "name": self.combo_text(self.fashion_table, row, 1)})
        for row in range(10):
            build["buffs"].append({"skill": self.combo_text(self.buff_table, row, 0), "level": self.combo_text(self.buff_table, row, 1)})
        build["buffs"] = self._dedup_build_buffs(build["buffs"])
        if hasattr(self, "engraving_table"):
            for row, slot in enumerate(ENGRAVING_SLOTS):
                main_value = self.engraving_value(self.engraving_table, row, 1)
                extra_tier, extra_rank, extra_value = self._engraving_extra_selection(
                    self.engraving_table, row
                )
                build["engravings"].append({
                    "slot": slot,
                    "grade": self.combo_text(self.engraving_table, row, 0),
                    "value_rank": self.combo_text(self.engraving_table, row, 1),
                    "main_value": main_value,
                    "secondary_value": self._engraving_secondary_value(
                        slot,
                        main_value,
                        self.combo_text(self.engraving_table, row, 0),
                        self.combo_text(self.engraving_table, row, 1),
                    ),
                    "extra": self.combo_text(self.engraving_table, row, 2),
                    "extra_tier": extra_tier,
                    "extra_rank": extra_rank,
                    "extra_value": extra_value,
                })
        if hasattr(self, "skill_sim_table"):
            build["skill_sim"] = []
            for row in range(SKILL_SIM_ROWS):
                build["skill_sim"].append({
                    "skill": self.combo_text(self.skill_sim_table, row, 0),
                    "level": self.combo_text(self.skill_sim_table, row, 1),
                    "cooldown": self._skill_base_cooldown(row),
                    "multiplier": self.double_value(self.skill_sim_table, row, 4),
                })
            build["skill_sim_settings"] = {
                "burst": bool(getattr(self, "skill_burst_check", None) and self.skill_burst_check.isChecked()),
                "cocktail": bool(getattr(self, "skill_cocktail_check", None) and self.skill_cocktail_check.isChecked()),
                "accel": bool(getattr(self, "skill_accel_check", None) and self.skill_accel_check.isChecked()),
                "custom_cdr": float(self.skill_custom_cdr.value()) if getattr(self, "skill_custom_cdr", None) else 0.0,
                "burst_sim_burst": bool(getattr(self, "skill_burst_sim_burst_check", None) and self.skill_burst_sim_burst_check.isChecked()),
                "burst_sim_accel": bool(getattr(self, "skill_burst_sim_accel_check", None) and self.skill_burst_sim_accel_check.isChecked()),
                "burst_sim_cocktail": bool(getattr(self, "skill_burst_sim_cocktail_check", None) and self.skill_burst_sim_cocktail_check.isChecked()),
                "burst_sim_practical": bool(getattr(self, "skill_burst_sim_practical_check", None) and self.skill_burst_sim_practical_check.isChecked()),
                "long_team_link": bool(getattr(self, "skill_long_team_link_check", None) and self.skill_long_team_link_check.isChecked()),
                "long_window": float(self.skill_long_window_spin.value()) if isinstance(getattr(self, "skill_long_window_spin", None), QDoubleSpinBox) else SKILL_LONG_PRACTICAL_WINDOW,
                "long_cocktail": bool(getattr(self, "skill_long_cocktail_check", None) and self.skill_long_cocktail_check.isChecked()),
                "long_burst_start": float(self.skill_long_burst_start_base.value()) if isinstance(getattr(self, "skill_long_burst_start_base", None), QDoubleSpinBox) else 0.0,
                "long_burst_interval": float(self.skill_long_burst_interval.value()) if isinstance(getattr(self, "skill_long_burst_interval", None), QDoubleSpinBox) else 50.0,
                "long_custom_burst_starts": {
                    str(index): float(value)
                    for index, value in getattr(self, "_skill_long_custom_burst_starts", {}).items()
                },
                "long_accel_start": float(self.skill_long_accel_start.value()) if isinstance(getattr(self, "skill_long_accel_start", None), QDoubleSpinBox) else 60.0,
                "burst_manual_job": norm(getattr(self, "_skill_burst_manual_job", "")),
                "burst_manual_sequence": list(self._skill_burst_manual_sequence) if self._skill_burst_manual_sequence is not None else None,
            }
        if hasattr(self, "skill_build_level_spins"):
            valid_spins = {
                skill_id: spin
                for skill_id, spin in self.skill_build_level_spins.items()
                if isinstance(spin, QSpinBox) and qt_object_is_valid(spin)
            }
            self.skill_build_level_spins = valid_spins
            levels = {
                skill_id: int(spin.value())
                for skill_id, spin in valid_spins.items()
                if spin.value() > 0
            }
            if valid_spins:
                build["skill_build"] = {
                    "second_job": self.second_job.currentText(),
                    "level_cap": SKILL_BUILD_LEVEL_CAP,
                    "levels": levels,
                }
        if hasattr(self, "skill_mastery_spins"):
            valid_spins = {
                skill_id: spin
                for skill_id, spin in self.skill_mastery_spins.items()
                if isinstance(spin, QSpinBox) and qt_object_is_valid(spin)
            }
            self.skill_mastery_spins = valid_spins
            levels = {
                skill_id: int(spin.value())
                for skill_id, spin in valid_spins.items()
                if spin.value() > 0
            }
            if valid_spins:
                build["skill_mastery_build"] = {
                    "second_job": self.second_job.currentText(),
                    "level_cap": SKILL_BUILD_LEVEL_CAP,
                    "levels": levels,
                }
        if (
            qt_object_is_valid(getattr(self, "skill_glyph_table", None))
            and qt_object_is_valid(getattr(self, "skill_glyph_count_spin", None))
        ):
            build["skill_glyphs"] = {
                "second_job": self.second_job.currentText(),
                "count": int(getattr(self, "_skill_glyph_applied_count", self.skill_glyph_count_spin.value())),
                "rows": self._selected_skill_glyphs(),
            }
        if hasattr(self, "team_member_table"):
            build["team_sim"]["members"] = [
                {"job": self.combo_text(self.team_member_table, row, 1)}
                for row in range(TEAM_MEMBER_ROWS)
            ]
        if hasattr(self, "team_effect_table"):
            build["team_sim"]["effects"] = []
            for row in range(TEAM_EFFECT_ROWS):
                enabled = self.team_effect_table.item(row, 0)
                build["team_sim"]["effects"].append({
                    "enabled": enabled.checkState() == Qt.Checked if enabled else False,
                    "provider": self.combo_text(self.team_effect_table, row, 1),
                    "skill": self.combo_text(self.team_effect_table, row, 2),
                    "level": self.combo_text(self.team_effect_table, row, 3),
                    "cooldown": self._team_cooldown_base(row),
                    "duration": self.double_value(self.team_effect_table, row, 5),
                })
            build["team_sim"]["cdr_usage"] = {
                skill: float(spin.value())
                for skill, spin in getattr(self, "team_cdr_usage_spins", {}).items()
                if isinstance(spin, QDoubleSpinBox)
            }
            build["team_sim"]["cdr_glyphs"] = {
                skill: bool(check.isChecked())
                for skill, check in getattr(self, "team_cdr_glyph_checks", {}).items()
                if isinstance(check, QCheckBox)
            }
            build["team_sim"]["cdr_applies"] = {
                skill: bool(check.isChecked())
                for skill, check in getattr(self, "team_cdr_apply_checks", {}).items()
                if isinstance(check, QCheckBox)
            }
            if hasattr(self, "team_freeze_coeff") and isinstance(self.team_freeze_coeff, QDoubleSpinBox):
                build["team_sim"]["freeze_coefficient"] = float(self.team_freeze_coeff.value())
            if hasattr(self, "team_extreme_coefficients") and isinstance(self.team_extreme_coefficients, dict):
                build["team_sim"]["extreme_coefficients"] = {
                    norm(job): float(value)
                    for job, value in self.team_extreme_coefficients.items()
                    if norm(job)
                }
            if hasattr(self, "team_extreme_hand_multipliers") and isinstance(self.team_extreme_hand_multipliers, dict):
                build["team_sim"]["extreme_hand_multipliers"] = {
                    norm(job): float(value)
                    for job, value in self.team_extreme_hand_multipliers.items()
                    if norm(job)
                }
            if hasattr(self, "team_aura_choices") and isinstance(self.team_aura_choices, dict):
                build["team_sim"]["aura_choices"] = {
                    job: combo.currentText()
                    for job, combo in self.team_aura_choices.items()
                    if isinstance(combo, Combo)
                }
            if hasattr(self, "team_extreme_mode"):
                build["team_sim"]["extreme_team_mode"] = norm(self.team_extreme_mode) or "不限"
            if hasattr(self, "team_extreme_boss"):
                build["team_sim"]["extreme_boss"] = norm(self.team_extreme_boss) or "无"
            if hasattr(self, "team_extreme_require_tank"):
                build["team_sim"]["extreme_require_tank"] = bool(self.team_extreme_require_tank)
            if hasattr(self, "team_extreme_require_healer"):
                build["team_sim"]["extreme_require_healer"] = bool(self.team_extreme_require_healer)
            if hasattr(self, "team_extreme_replacement_mode"):
                mode_text = norm(self.team_extreme_replacement_mode) or "快速"
                build["team_sim"]["extreme_replacement_mode"] = mode_text if mode_text in {"快速", "完整"} else "快速"
            if hasattr(self, "team_extreme_replacement_candidates"):
                build["team_sim"]["extreme_replacement_candidates"] = max(0, int(as_number(self.team_extreme_replacement_candidates, 2)))
            if hasattr(self, "team_extreme_fixed_jobs"):
                build["team_sim"]["extreme_fixed_jobs"] = [norm(job) for job in self.team_extreme_fixed_jobs if norm(job)]
            if hasattr(self, "team_extreme_fixed_explicit"):
                build["team_sim"]["extreme_fixed_explicit"] = bool(self.team_extreme_fixed_explicit)
            if hasattr(self, "team_practical_window_spin") and isinstance(self.team_practical_window_spin, QDoubleSpinBox):
                build["team_sim"]["practical_window"] = float(self.team_practical_window_spin.value())
            build["team_sim"]["practical_manual_job"] = norm(getattr(self, "_team_practical_manual_job", ""))
            build["team_sim"]["practical_manual_sequence"] = (
                list(self._team_practical_manual_sequence)
                if isinstance(getattr(self, "_team_practical_manual_sequence", None), list)
                else None
            )
        for row in range(self.card_table.rowCount()):
            build["card_grades"].append({"name": self.card_name(row), "grade": self.combo_text(self.card_table, row, 1)})
        for row, attr in enumerate(CARD_PREVIEW_ATTRS):
            build["card_bonus_levels"][attr] = (
                0
                if attr == "致命伤害"
                else self.spin_value(self.card_total_table, row, 2)
            )
        for row, slot in enumerate(TALISMAN_SLOTS):
            build["talismans"].append({
                "slot": slot,
                "grade": self.combo_text(self.talisman_table, row, 1) or "无",
                "name": self.combo_text(self.talisman_table, row, 2) or "无",
                "extra": self.combo_text(self.talisman_table, row, 3) or "无",
            })
        for row, (label, _attr) in enumerate(FREE_PERCENT_INPUTS):
            value = self.double_value(self.free_percent_table, row, 1)
            if abs(value) > 1e-9:
                build["free_inputs"]["percent"][label] = value
        for row, attr in enumerate(FREE_STAT_ATTRS):
            value = self.double_value(self.free_stat_table, row, 1)
            if abs(value) > 1e-9:
                build["free_inputs"]["stats"][attr] = value
        for row, attr in enumerate(FREE_STAT_PERCENT_ATTRS):
            value = self.double_value(self.free_stat_percent_table, row, 1)
            if abs(value) > 1e-9:
                build["free_inputs"]["stat_percent"][attr] = value
        return build
