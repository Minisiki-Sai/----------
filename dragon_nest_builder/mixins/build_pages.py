from __future__ import annotations

from ..shared import *


class BuildPagesMixin:
    def _equipment_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "装备与首饰",
            "设置角色与目标环境，并在同一页完成装备、强化、附魔和首饰搭配。",
        )
        top_panels = QHBoxLayout()
        top_panels.setSpacing(10)
        role_box = QGroupBox("角色")
        role_box.setObjectName("buildCompactSection")
        role_form = QGridLayout(role_box)
        role_form.setContentsMargins(10, 8, 10, 8)
        role_form.setHorizontalSpacing(8)
        role_form.setVerticalSpacing(4)
        role_box.setMinimumHeight(70)
        monster_box = QGroupBox("怪物属性")
        monster_box.setObjectName("buildCompactSection")
        monster_form = QGridLayout(monster_box)
        monster_form.setContentsMargins(10, 8, 10, 8)
        monster_form.setHorizontalSpacing(8)
        monster_form.setVerticalSpacing(5)
        monster_box.setMinimumHeight(82)
        first_job_values = self.store.first_jobs()
        self.first_job = Combo(
            first_job_values,
            self.build.get("first_job"),
            self._job_changed,
            min_width=option_width(first_job_values, 96, 146),
            contents_length=0,
        )
        second_job_values = self.store.second_jobs(self.first_job.currentText())
        self.second_job = Combo(
            second_job_values,
            self.build.get("second_job"),
            self._second_job_changed,
            min_width=option_width(second_job_values, 106, 156),
            contents_length=0,
        )
        self.crit_resist = QSpinBox()
        self.crit_resist.setRange(0, 999999)
        self.crit_resist.setFixedWidth(78)
        self.crit_resist.setValue(int(self.build.get("crit_resist_value", self.build.get("crit_resist", 0)) or 0))
        self.crit_resist.valueChanged.connect(self.schedule_refresh)
        self.crit_resist_rate = QLabel("0%")
        self.crit_resist_rate.setObjectName("inlineValue")
        self.crit_resist_rate.setMinimumWidth(54)
        self.crit_resist_rate.setAlignment(Qt.AlignCenter)
        self.element_resists = self._initial_element_resists()
        self.element_resist_mode = Combo(
            ["全部", "火", "水", "光", "暗"],
            self.build.get("element_resist_mode", "全部"),
            self._element_resist_mode_changed,
            min_width=60,
            contents_length=0,
            object_name="elementResistModeCombo",
        )
        self.element_resist = QDoubleSpinBox()
        self.element_resist.setRange(-100, 100)
        self.element_resist.setDecimals(1)
        self.element_resist.setSingleStep(1)
        self.element_resist.setSuffix("%")
        self.element_resist.setFixedHeight(EDIT_ROW_HEIGHT)
        self.element_resist.setFixedWidth(68)
        self.element_resist.setValue(self._current_element_resist_value(self.element_resist_mode.currentText()))
        self.element_resist.valueChanged.connect(self._element_resist_value_changed)
        self.monster_defense = QSpinBox()
        self.monster_defense.setRange(0, 999999999)
        self.monster_defense.setFixedWidth(78)
        self.monster_defense.setValue(int(self.build.get("monster_defense", 0) or 0))
        self.monster_defense.valueChanged.connect(self._monster_defense_changed)
        self.monster_defense_rate = QDoubleSpinBox()
        self.monster_defense_rate.setRange(0, 100)
        self.monster_defense_rate.setDecimals(1)
        self.monster_defense_rate.setSingleStep(1)
        self.monster_defense_rate.setSuffix("%")
        self.monster_defense_rate.setFixedHeight(EDIT_ROW_HEIGHT)
        self.monster_defense_rate.setFixedWidth(68)
        self.monster_defense_rate.setReadOnly(True)
        self.monster_defense_rate.setValue(BuildCalculator.defense_percent(self.monster_defense.value()))
        self.boss_combo = Combo(["无"] + list(BOSS_PRESETS), self.build.get("boss") or "无", self._boss_changed, min_width=128, contents_length=0)
        for col, (label_text, widget) in enumerate([
            ("一转", self.first_job),
            ("二转", self.second_job),
        ]):
            label = QLabel(label_text)
            label.setObjectName("inlineLabel")
            role_form.addWidget(label, 0, col * 2)
            role_form.addWidget(widget, 0, col * 2 + 1)
        role_form.setColumnStretch(4, 1)
        monster_items: list[tuple[str, QWidget]] = [
            ("Boss", self.boss_combo),
            ("致命抵抗", self.crit_resist),
            ("致命抵抗率", self.crit_resist_rate),
        ]
        for col, (label_text, widget) in enumerate(monster_items):
            label = QLabel(label_text)
            label.setObjectName("inlineLabel")
            monster_form.addWidget(label, 0, col * 2)
            monster_form.addWidget(widget, 0, col * 2 + 1)
        label = QLabel("属性抵抗")
        label.setObjectName("inlineLabel")
        monster_form.addWidget(label, 1, 0)
        monster_form.addWidget(self.element_resist_mode, 1, 1)
        monster_form.addWidget(self.element_resist, 1, 2)
        col = 3
        for label_text, widget in [
            ("防御", self.monster_defense),
            ("防御率", self.monster_defense_rate),
        ]:
            label = QLabel(label_text)
            label.setObjectName("inlineLabel")
            monster_form.addWidget(label, 1, col)
            monster_form.addWidget(widget, 1, col + 1)
            col += 2
        monster_form.setColumnStretch(col, 1)
        toolbar, auto_row = self._build_toolbar()
        auto_level_label = QLabel("一键配装")
        auto_level_label.setObjectName("inlineLabel")
        self.auto_equip_level = Combo(list(AUTO_EQUIP_TARGETS), "70", self._auto_equip_level_changed, min_width=78, align=Qt.AlignCenter, contents_length=0)
        initial_targets = self._auto_equip_target_options(self.auto_equip_level.currentText())
        self.auto_equip_target = Combo(initial_targets, self._default_auto_equip_target(self.auto_equip_level.currentText()), self.schedule_refresh, min_width=132, contents_length=0)
        auto_equip_button = app_button("应用", primary=True)
        auto_equip_button.setFixedHeight(EDIT_ROW_HEIGHT)
        auto_equip_button.clicked.connect(self.apply_auto_equip_selection)
        auto_row.addWidget(auto_level_label)
        auto_row.addWidget(self.auto_equip_level)
        auto_row.addWidget(self.auto_equip_target)
        auto_row.addWidget(auto_equip_button)
        if self.name == "配装1":
            self.profile_sync_button = app_button("从游戏识别")
            self.profile_sync_button.setFixedHeight(EDIT_ROW_HEIGHT)
            self.profile_sync_button.setToolTip(
                "在角色选择界面开启识别，再进入角色；自动读取装备、时装、刻印、纹章、石板、卡片和掌握之力"
            )
            self.profile_sync_button.clicked.connect(self.profile_sync_requested.emit)
            auto_row.addWidget(self.profile_sync_button)
        auto_row.addStretch(1)
        top_panels.addWidget(role_box, 1)
        top_panels.addWidget(monster_box, 2)
        layout.addLayout(top_panels)
        layout.addWidget(toolbar)

        self.equipment_table = AppTableWidget(len(EQUIPMENT_SLOTS) * 2 + 1, 8)
        self._mark_build_table(self.equipment_table)
        self.equipment_table.setHorizontalHeaderLabels(["装备名称", "后缀", "强化", "附魔1", "附魔2", "附魔3", "附魔4", "附魔5"])
        self.equipment_table.setVerticalHeaderLabels([label for slot in EQUIPMENT_SLOTS for label in (slot, "具体加成")] + ["套装属性"])
        defaults = self.build.get("equipment", [])
        for index, slot in enumerate(EQUIPMENT_SLOTS):
            row = index * 2
            detail_row = row + 1
            item = defaults[index] if index < len(defaults) else {"slot": slot}
            name_values = self.store.equipment_names(self.first_job.currentText(), self.second_job.currentText(), slot)
            self.equipment_table.setCellWidget(row, 0, Combo(name_values, item.get("name"), lambda _text, r=row: self._equipment_name_changed(r), min_width=option_width(name_values, 190, 310)))
            suffix_values = self.store.suffix_names(self.first_job.currentText(), self.second_job.currentText(), item.get("name", ""))
            self.equipment_table.setCellWidget(row, 1, Combo(suffix_values, item.get("suffix") or "无", self.schedule_refresh, min_width=option_width(suffix_values, 100, 160)))
            spin = QSpinBox()
            spin.setRange(0, 25)
            spin.setValue(int(item.get("enhance") or 0))
            spin.valueChanged.connect(self.schedule_refresh)
            self.equipment_table.setCellWidget(row, 2, spin)
            enchants = self.store.enchant_names(index >= 5)
            for col in range(3, 8):
                current = item.get("enchants", [])[col - 3] if col - 3 < len(item.get("enchants", [])) else "无"
                self.equipment_table.setCellWidget(row, col, Combo(enchants, current or "无", self.schedule_refresh, min_width=option_width(enchants, 96, 136)))
            self.equipment_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self.equipment_table.setItem(detail_row, 0, QTableWidgetItem(""))
            self.equipment_table.setSpan(detail_row, 0, 1, 8)
            self.equipment_table.setRowHeight(detail_row, 40)
        self.equipment_set_row = len(EQUIPMENT_SLOTS) * 2
        self.equipment_table.setItem(self.equipment_set_row, 0, QTableWidgetItem(""))
        self.equipment_table.setSpan(self.equipment_set_row, 0, 1, 8)
        self.equipment_table.setRowHeight(self.equipment_set_row, 40)
        self._configure_table(self.equipment_table, [220, 120, 58, 112, 112, 112, 112, 112])
        self._adapt_equipment_column_widths()
        self.equipment_table.setMinimumHeight(500)
        equipment_box, equipment_layout = self._build_section(
            "装备",
            "每件装备下方直接展示具体加成，末行汇总当前套装效果。",
        )
        equipment_layout.addWidget(self.equipment_table)
        layout.addWidget(equipment_box)

        self.jewelry_table = AppTableWidget(len(JEWELRY_SLOTS) * 2 + 1, 7)
        self._mark_build_table(self.jewelry_table)
        self.jewelry_table.setHorizontalHeaderLabels(["饰品名称", "变体", "附魔1", "附魔2", "附魔3", "附魔4", "附魔5"])
        self.jewelry_table.setVerticalHeaderLabels([label for slot in JEWELRY_SLOTS for label in (slot, "具体加成")] + ["套装属性"])
        defaults = self.build.get("jewelry", [])
        for index, slot in enumerate(JEWELRY_SLOTS):
            row = index * 2
            detail_row = row + 1
            display_slot = "戒指" if slot.startswith("戒指") else slot
            item = defaults[index] if index < len(defaults) else {"slot": display_slot}
            jewelry_names = self.store.jewelry_names(display_slot)
            self.jewelry_table.setCellWidget(row, 0, Combo(jewelry_names, item.get("name"), lambda _text, r=row: self._jewelry_name_changed(r), min_width=option_width(jewelry_names, 190, 310)))
            variant_text = self.store.jewelry_variant_label(item.get("variant") or "无")
            variant_values = self.store.jewelry_variants(display_slot, item.get("name", ""))
            self.jewelry_table.setCellWidget(row, 1, Combo(variant_values, variant_text, self.schedule_refresh, min_width=option_width(variant_values, 90, 130)))
            for col in range(2, 7):
                current = item.get("enchants", [])[col - 2] if col - 2 < len(item.get("enchants", [])) else "无"
                enchant_values = self.store.enchant_names(True) if display_slot == "戒指" else self.store.enchant_names(False)
                self.jewelry_table.setCellWidget(row, col, Combo(enchant_values, current or "无", self.schedule_refresh, min_width=option_width(enchant_values, 96, 136)))
            self.jewelry_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self.jewelry_table.setItem(detail_row, 0, QTableWidgetItem(""))
            self.jewelry_table.setSpan(detail_row, 0, 1, 7)
            self.jewelry_table.setRowHeight(detail_row, 40)
        self.jewelry_set_row = len(JEWELRY_SLOTS) * 2
        self.jewelry_table.setItem(self.jewelry_set_row, 0, QTableWidgetItem(""))
        self.jewelry_table.setSpan(self.jewelry_set_row, 0, 1, 7)
        self.jewelry_table.setRowHeight(self.jewelry_set_row, 40)
        self._configure_table(self.jewelry_table, [220, 110, 112, 112, 112, 112, 112])
        self._adapt_jewelry_column_widths()
        self.jewelry_table.setMinimumHeight(310)
        jewelry_box, jewelry_layout = self._build_section(
            "首饰",
            "首饰变体、附魔与套装加成集中显示，戒指仍按各自槽位独立保存。",
        )
        jewelry_layout.addWidget(self.jewelry_table)
        layout.addWidget(jewelry_box)
        layout.addStretch(1)
        return self._scroll(page, horizontal=False)

    def _engraving_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        auto_row = QHBoxLayout()
        auto_row.addWidget(QLabel("统一范围"))
        self.auto_engraving_target = Combo(["全部", "防具", "武器", "饰品", "三件套"], "全部", self.apply_auto_engraving_grade, min_width=88, contents_length=0)
        auto_row.addWidget(self.auto_engraving_target)
        auto_row.addWidget(QLabel("统一刻印等级"))
        self.auto_engraving_grade = Combo(self.store.engraving_grades(), "无", self.apply_auto_engraving_grade, min_width=88, contents_length=0)
        auto_row.addWidget(self.auto_engraving_grade)
        auto_row.addWidget(QLabel("统一额外属性"))
        self.auto_engraving_extra = Combo(self.store.engraving_extra_options(), "无", self.apply_auto_engraving_extra, min_width=130, contents_length=0)
        auto_row.addWidget(self.auto_engraving_extra)
        auto_row.addWidget(QLabel("统一额外档位"))
        self.auto_engraving_tier = TierChoice("S低档", self.apply_auto_engraving_tier)
        auto_row.addWidget(self.auto_engraving_tier)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)
        engraving_defaults = {norm(item.get("slot")): item for item in self.build.get("engravings", []) if isinstance(item, dict)}
        engraving_widths = [96, 180, 150, 110, 520]
        engraving_extra_options = self.store.engraving_extra_options()
        self.engraving_table = AppTableWidget(len(ENGRAVING_SLOTS), 5)
        self.engraving_table.setHorizontalHeaderLabels(["刻印等级", "数值", "额外属性", "额外档位", "具体加成"])
        self.engraving_table.setVerticalHeaderLabels(ENGRAVING_SLOTS)
        self.engraving_table.verticalHeader().setMinimumWidth(82)
        for row, slot in enumerate(ENGRAVING_SLOTS):
            item = engraving_defaults.get(slot, {})
            kind = self._engraving_main_kind(slot)
            grade = self._engraving_grade_current(item)
            value_options = self.store.engraving_main_value_options(kind, grade)
            self.engraving_table.setCellWidget(row, 0, Combo(self.store.engraving_grades(), grade, lambda _text, r=row: self._engraving_grade_changed(r), min_width=engraving_widths[0], contents_length=0))
            self.engraving_table.setCellWidget(row, 1, EngravingValueSlider(value_options, self._engraving_option_current(value_options, self._engraving_main_saved_value(slot, item), item.get("value_rank")), self.schedule_refresh))
            extra_text = self._engraving_extra_display(item.get("extra"))
            extra = extra_text if extra_text in engraving_extra_options else "无"
            self.engraving_table.setCellWidget(row, 2, Combo(engraving_extra_options, extra, lambda _text, r=row: self._engraving_extra_changed(r), min_width=engraving_widths[2], contents_length=0))
            self.engraving_table.setCellWidget(row, 3, TierChoice(self._engraving_extra_tier_current(item), self.schedule_refresh))
            self.engraving_table.setItem(row, 4, QTableWidgetItem(""))
            self.engraving_table.setRowHeight(row, 32)
        self._configure_table(self.engraving_table, engraving_widths)
        self.engraving_table.verticalHeader().setDefaultSectionSize(32)
        for col in (0, 2):
            self._fit_combo_column_to_cells(self.engraving_table, col)
        layout.addWidget(self.engraving_table, 1)
        layout.addWidget(QLabel("刻印加成合计"))
        self.engraving_total_table = AppTableWidget(1, 6)
        self.engraving_total_table.setHorizontalHeaderLabels(["属性", "数据", "属性", "数据", "属性", "数据"])
        self.engraving_total_table.verticalHeader().setVisible(False)
        self.engraving_total_table.setMaximumHeight(118)
        self._configure_table(self.engraving_total_table, [82, 90, 82, 90, 82, 90])
        layout.addWidget(self.engraving_total_table)
        return page

    def _glyph_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "纹章配置",
            "分别配置 A 纹章与 S 纹章，具体加成和合计结果会随选择实时更新。",
        )
        toolbar, auto_row = self._build_toolbar()
        glyph_button = app_button("一键纹章", primary=True)
        glyph_button.clicked.connect(self.auto_glyphs)
        auto_row.addWidget(glyph_button)
        auto_row.addWidget(QLabel("A额外属性"))
        self.auto_glyph_a_extra = Combo(self.store.glyph_a_extra(), "无", self.apply_auto_glyph_a_extra, min_width=110, contents_length=0)
        auto_row.addWidget(self.auto_glyph_a_extra)
        auto_row.addStretch(1)
        layout.addWidget(toolbar)

        self.glyph_a_table = AppTableWidget(11, 4)
        self._mark_build_table(self.glyph_a_table)
        self.glyph_a_table.setHorizontalHeaderLabels(["等级", "纹章名称", "额外属性", "具体加成"])
        defaults = self.build.get("glyph_a", [])
        glyph_a_extra_options = self.store.glyph_a_extra()
        for row in range(11):
            item = defaults[row] if row < len(defaults) else {}
            self.glyph_a_table.setCellWidget(row, 0, Combo(self.store.glyph_a_levels(), item.get("level"), self.schedule_refresh, min_width=90, align=Qt.AlignCenter))
            self.glyph_a_table.setCellWidget(row, 1, Combo(self.store.glyph_a_names(), item.get("name"), lambda _text, r=row: self._glyph_a_name_changed(r), min_width=210))
            extra = item.get("extra") if item.get("extra") in glyph_a_extra_options else "无"
            self.glyph_a_table.setCellWidget(row, 2, Combo(glyph_a_extra_options, extra, self.schedule_refresh, min_width=110))
            self.glyph_a_table.setItem(row, 3, QTableWidgetItem(""))
        self._refresh_glyph_a_name_options()
        self._configure_table(self.glyph_a_table, [92, 220, 120, 240])
        self.glyph_a_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._fit_table_height(self.glyph_a_table)
        glyph_a_box, glyph_a_layout = self._build_section(
            "A 纹章",
            "11 个槽位按等级、名称和额外属性配置；重复与职业限制仍沿用原规则。",
        )
        glyph_a_layout.addWidget(self.glyph_a_table)
        layout.addWidget(glyph_a_box)

        self.glyph_s_table = AppTableWidget(4, 8)
        self._mark_build_table(self.glyph_s_table)
        self.glyph_s_table.setHorizontalHeaderLabels(["等级", "纹章名称", "基础属性1", "档位1", "基础属性2", "档位2", "额外属性", "具体加成"])
        defaults = self.build.get("glyph_s", [])
        for row in range(4):
            item = defaults[row] if row < len(defaults) else {}
            name = norm(item.get("name"))
            glyph_s_extra_options = self.store.glyph_s_extra_options(name)
            self.glyph_s_table.setCellWidget(row, 0, Combo(self.store.glyph_s_levels(), item.get("level"), self.schedule_refresh, min_width=90, align=Qt.AlignCenter))
            self.glyph_s_table.setCellWidget(row, 1, Combo(self.store.glyph_s_names(), name, self.schedule_refresh, min_width=190))
            self.glyph_s_table.setCellWidget(row, 2, Combo(self.store.glyph_s_base_options(name), item.get("base_attr1") or "无", self.schedule_refresh, min_width=96))
            self.glyph_s_table.setCellWidget(row, 3, Combo(self.store.glyph_s_ranks(), item.get("rank1") or "无", self.schedule_refresh, min_width=70))
            self.glyph_s_table.setCellWidget(row, 4, Combo(self.store.glyph_s_base_options(name), item.get("base_attr2") or "无", self.schedule_refresh, min_width=96))
            self.glyph_s_table.setCellWidget(row, 5, Combo(self.store.glyph_s_ranks(), item.get("rank2") or "无", self.schedule_refresh, min_width=70))
            extra = item.get("extra") if item.get("extra") in glyph_s_extra_options else "无"
            self.glyph_s_table.setCellWidget(row, 6, Combo(glyph_s_extra_options, extra, self.schedule_refresh, min_width=110))
            self.glyph_s_table.setItem(row, 7, QTableWidgetItem(""))
        self._configure_table(self.glyph_s_table, [92, 200, 104, 74, 104, 74, 120, 240])
        for row in range(self.glyph_s_table.rowCount()):
            self.glyph_s_table.setRowHeight(row, EDIT_ROW_HEIGHT)
        self.glyph_s_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.glyph_s_table.setFixedHeight(EDIT_ROW_HEIGHT * (self.glyph_s_table.rowCount() + 1) + 4)
        glyph_s_box, glyph_s_layout = self._build_section(
            "S 纹章",
            "基础属性和对应档位成组排列，末列汇总单块纹章的实际加成。",
        )
        glyph_s_layout.addWidget(self.glyph_s_table)
        layout.addWidget(glyph_s_box)

        self.glyph_total_table = AppTableWidget(0, 6)
        self._mark_build_table(self.glyph_total_table)
        self.glyph_total_table.setHorizontalHeaderLabels(["属性", "数据", "属性", "数据", "属性", "数据"])
        self.glyph_total_table.verticalHeader().setVisible(False)
        self._configure_table(self.glyph_total_table, [82, 90, 82, 90, 82, 90])
        glyph_total_box, glyph_total_layout = self._build_section("纹章总属性")
        glyph_total_layout.addWidget(self.glyph_total_table)
        layout.addWidget(glyph_total_box)
        layout.addStretch(1)
        return self._scroll(page, horizontal=False)

    def _rune_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "石板配置",
            "普通石板、源铸转换与强化效果分区呈现，所有档位始终紧邻对应属性。",
        )
        toolbar, auto_row = self._build_toolbar()
        rune_button = app_button("一键石板", primary=True)
        rune_button.clicked.connect(self.auto_runes)
        auto_row.addWidget(rune_button)
        auto_row.addStretch(1)
        layout.addWidget(toolbar)

        rune_box, rune_layout = self._build_section(
            "普通石板",
            "每块石板分两行显示：上行属性 1–3，下行属性 4–6；每个属性右侧即为对应档位。",
        )

        # Six attribute/rank pairs used to occupy thirteen columns. In the
        # normal editor width the latter rank columns therefore lived behind a
        # horizontal scrollbar and looked as if they had disappeared. Keep
        # the same six-entry data model, but render three pairs on each of two
        # rows so every tier remains visible beside its attribute.
        self.rune_table = AppTableWidget(12, 7)
        self._mark_build_table(self.rune_table)
        self.rune_table.setHorizontalHeaderLabels(
            ["等级", "属性", "档位", "属性", "档位", "属性", "档位"]
        )
        rune_row_labels: list[str] = []
        for index in range(4):
            rune_name = f"{'攻击' if index < 2 else '防御'}{index % 2 + 1}"
            rune_row_labels.extend(
                [f"{rune_name} 1–3", f"{rune_name} 4–6", "具体加成"]
            )
        self.rune_table.setVerticalHeaderLabels(rune_row_labels)
        self.rune_table.verticalHeader().setMinimumWidth(92)
        defaults = self.build.get("normal_runes", [])
        for index in range(4):
            row = self._rune_level_row(index)
            detail_row = self._rune_detail_row(index)
            rune_type = "攻击" if index < 2 else "防御"
            item = defaults[index] if index < len(defaults) else {"type": rune_type}
            self.rune_table.setCellWidget(row, 0, Combo(self.store.rune_levels(), item.get("level"), self.schedule_refresh, min_width=86, align=Qt.AlignCenter, contents_length=0))
            continuation = QTableWidgetItem("同上")
            continuation.setTextAlignment(Qt.AlignCenter)
            continuation.setFlags(continuation.flags() & ~Qt.ItemIsEditable)
            self.rune_table.setItem(row + 1, 0, continuation)
            entries = item.get("entries", [])
            for i in range(6):
                entry = entries[i] if i < len(entries) else {}
                entry_row, attr_column, rank_column = self._rune_entry_cell(index, i)
                self.rune_table.setCellWidget(entry_row, attr_column, Combo(self.store.rune_attrs(rune_type), entry.get("attr") or "无", self.schedule_refresh, min_width=112, contents_length=0))
                self.rune_table.setCellWidget(entry_row, rank_column, Combo(self.store.rune_ranks(), entry.get("rank") or "无", self.schedule_refresh, min_width=66, align=Qt.AlignCenter, contents_length=0))
            self.rune_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self.rune_table.setRowHeight(row + 1, EDIT_ROW_HEIGHT)
            self.rune_table.setItem(detail_row, 0, QTableWidgetItem(""))
            self.rune_table.setSpan(detail_row, 0, 1, 7)
            self.rune_table.setRowHeight(detail_row, 28)
        self._configure_table(self.rune_table, [88] + [128, 72] * 3)
        self.rune_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.rune_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fit_rune_table_height()
        rune_layout.addWidget(self.rune_table)
        layout.addWidget(rune_box)

        core = self.build.get("core_rune", {})
        core_box = QGroupBox("源铸石板")
        core_box.setObjectName("buildSection")
        grid = QGridLayout(core_box)
        self.core_level = Combo(self.store.core_levels(), core.get("level"), self.schedule_refresh)
        self.core_conversion = Combo(self.store.core_conversions(), core.get("conversion"), self._core_conversion_changed)
        conversion_current = self.core_conversion.currentText() or core.get("conversion")
        ratio_values = self.store.core_ratio_ranks(conversion_current)
        coeff_values = self.store.core_coeff_ranks(conversion_current)
        ratio_current = core.get("ratio_rank") if norm(core.get("ratio_rank")) in set(ratio_values) else self._highest_number_option(ratio_values)
        coeff_current = core.get("coeff_rank") if norm(core.get("coeff_rank")) in set(coeff_values) else self._highest_number_option(coeff_values)
        self.core_ratio = Combo(ratio_values, ratio_current, self.schedule_refresh)
        self.core_coeff = Combo(coeff_values, coeff_current, self.schedule_refresh)
        self.core_enhance = QSpinBox()
        self.core_enhance.setRange(0, 25)
        self.core_enhance.setValue(int(core.get("enhance") or 0))
        self.core_enhance.valueChanged.connect(self.schedule_refresh)
        for col, (label, widget) in enumerate([("等级", self.core_level), ("转换", self.core_conversion), ("转换比例", self.core_ratio), ("转换系数", self.core_coeff), ("强化", self.core_enhance)]):
            grid.addWidget(QLabel(label), 0, col)
            grid.addWidget(widget, 1, col)
        self.core_preview = QLabel("")
        self.core_preview.setObjectName("buildValuePreview")
        self.core_preview.setWordWrap(True)
        grid.addWidget(QLabel("转换结果"), 2, 0)
        grid.addWidget(self.core_preview, 2, 1, 1, 4)
        layout.addWidget(core_box)

        self.rune_total_table = AppTableWidget(0, 2)
        self._mark_build_table(self.rune_total_table)
        self.rune_total_table.setHorizontalHeaderLabels(["石板总属性", "合计"])
        self.rune_total_table.verticalHeader().setVisible(False)
        self._configure_table(self.rune_total_table, [140, 140])
        self.rune_total_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        rune_total_box, rune_total_layout = self._build_section("石板总属性")
        rune_total_layout.addWidget(self.rune_total_table)
        layout.addWidget(rune_total_box)
        layout.addStretch(1)
        return self._scroll(page, horizontal=False)

    def _card_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        grade_row = QHBoxLayout()
        self.card_select_all = QCheckBox("一键全选")
        self.card_select_all.stateChanged.connect(self.set_all_card_selected)
        grade_row.addWidget(self.card_select_all)
        grade_row.addWidget(QLabel("选中卡片统一设置为"))
        for grade in ("L", "S", "A", "无"):
            button = app_button(grade, primary=grade == "L")
            button.setObjectName({"L": "gradeLButton", "S": "gradeSButton", "A": "gradeAButton"}.get(grade, "gradeNoneButton"))
            self._apply_grade_button_style(button, grade)
            button.clicked.connect(lambda _checked=False, g=grade: self.set_selected_card_grade(g))
            grade_row.addWidget(button)
        grade_row.addSpacing(12)
        grade_row.addWidget(QLabel("按卡片等级一键设置"))
        self.card_level_grade = Combo(GRADE_OPTIONS, "L", min_width=72)
        grade_row.addWidget(self.card_level_grade)
        for level in (40, 50, 60, 70):
            count = sum(self.store.card_level(name) == level for name in self.store.card_names())
            button = app_button(f"{level}级 ({count})")
            button.setToolTip(f"将全部 {level} 级卡片设置为所选品质")
            button.clicked.connect(lambda _checked=False, value=level: self.set_card_level_grade(value))
            grade_row.addWidget(button)
        grade_row.addStretch(1)
        layout.addLayout(grade_row)

        content = QHBoxLayout()
        content.setSpacing(10)
        card_box = QGroupBox("卡片列表")
        card_layout = QVBoxLayout(card_box)
        card_layout.setContentsMargins(7, 8, 7, 7)
        card_layout.setSpacing(5)

        names = self.store.card_names()
        self.card_table = AppTableWidget(len(names), 2 + len(CARD_PREVIEW_ATTRS))
        self.card_table.setHorizontalHeaderLabels(["选择", "等级"] + CARD_PREVIEW_ATTRS)
        self.card_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.card_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        defaults = self.build.get("card_grades", [])
        default_by_name = {
            norm(item.get("name")): item
            for item in defaults
            if isinstance(item, dict) and norm(item.get("name"))
        }
        row_headers: list[str] = []
        for row, name in enumerate(names):
            item = default_by_name.get(norm(name), {})
            row_headers.append(name)
            check_item = QTableWidgetItem("")
            check_item.setFlags(check_item.flags() | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setTextAlignment(Qt.AlignCenter)
            self.card_table.setItem(row, 0, check_item)
            self.card_table.setCellWidget(row, 1, Combo(GRADE_OPTIONS, item.get("grade") or "无", self.schedule_refresh, min_width=80))
            for col in range(2, 2 + len(CARD_PREVIEW_ATTRS)):
                self.card_table.setItem(row, col, QTableWidgetItem(""))
        self.card_table.setVerticalHeaderLabels(row_headers)
        self.card_table.verticalHeader().setMinimumWidth(170)
        self._configure_table(self.card_table, [48, 72] + [66] * len(CARD_PREVIEW_ATTRS))
        self.card_table.itemChanged.connect(self._card_selection_item_changed)
        self.card_table.setVisible(False)
        card_layout.addWidget(self.card_table)

        self.card_grid_checks: list[QCheckBox] = []
        self.card_grid_grades: list[QComboBox] = []
        self.card_grid_details: list[QLabel] = []
        self.card_pages = QTabWidget()
        self.card_pages.setObjectName("cardPages")
        cards_per_page = 16
        page_starts = list(range(0, min(len(names), cards_per_page * 4), cards_per_page))
        if len(names) > cards_per_page * 4:
            page_starts.append(cards_per_page * 4)
        for page_index, page_start in enumerate(page_starts):
            page_end = page_start + cards_per_page if page_index < 4 else len(names)
            grid_page = QWidget()
            grid = QGridLayout(grid_page)
            grid.setContentsMargins(6, 6, 6, 6)
            grid.setHorizontalSpacing(7)
            grid.setVerticalSpacing(7)
            for offset, name in enumerate(names[page_start:page_end]):
                row = page_start + offset
                card = QFrame()
                card.setObjectName("cardTile")
                tile = QVBoxLayout(card)
                tile.setContentsMargins(8, 7, 8, 7)
                tile.setSpacing(4)

                check = QCheckBox(name)
                check.setObjectName("cardTileName")
                check.setToolTip(name)
                check.stateChanged.connect(
                    lambda state, card_row=row: self._set_card_grid_checked(card_row, state)
                )
                tile.addWidget(check)

                meta = QHBoxLayout()
                level = self.store.card_level(name)
                level_label = QLabel(f"{level}级" if level else "未知等级")
                level_label.setObjectName("cardTileLevel")
                meta.addWidget(level_label)
                meta.addStretch(1)
                grade = Combo(
                    GRADE_OPTIONS,
                    default_by_name.get(norm(name), {}).get("grade") or "无",
                    min_width=66,
                    contents_length=0,
                )
                grade.currentTextChanged.connect(
                    lambda value, card_row=row: self._card_grid_grade_changed(card_row, value)
                )
                meta.addWidget(grade)
                tile.addLayout(meta)

                detail = QLabel("")
                detail.setObjectName("cardTileDetail")
                detail.setWordWrap(True)
                detail.setMinimumHeight(30)
                tile.addWidget(detail)
                grid.addWidget(card, offset // 4, offset % 4)
                self.card_grid_checks.append(check)
                self.card_grid_grades.append(grade)
                self.card_grid_details.append(detail)
            for column in range(4):
                grid.setColumnStretch(column, 1)
            grid.setRowStretch((page_end - page_start + 3) // 4, 1)
            page_scroll = QScrollArea()
            page_scroll.setWidgetResizable(True)
            page_scroll.setFrameShape(QFrame.NoFrame)
            page_scroll.setWidget(grid_page)
            self.card_pages.addTab(page_scroll, str(page_index + 1))
        self.card_pages.setMinimumHeight(535)
        self.card_pages.setStyleSheet(
            "QFrame#cardTile { border: 1px solid #c9dfe3; border-radius: 5px; background: #fbfdfd; }"
            "QFrame#cardTile:hover { border-color: #1594a4; background: #f1f9fa; }"
            "QCheckBox#cardTileName { font-weight: 700; color: #243b43; }"
            "QLabel#cardTileLevel { color: #168595; font-size: 11px; font-weight: 700; }"
            "QLabel#cardTileDetail { color: #60747b; font-size: 10px; }"
        )
        card_layout.addWidget(self.card_pages)
        content.addWidget(card_box, 3)

        total_box = QGroupBox("卡片总属性")
        total_layout = QVBoxLayout(total_box)
        total_layout.setContentsMargins(7, 8, 7, 7)
        total_layout.setSpacing(5)
        self.card_total_table = AppTableWidget(len(CARD_PREVIEW_ATTRS), 4)
        self.card_total_table.setHorizontalHeaderLabels(["卡片总属性", "初始合计", "掌握之力等级", "加成后合计"])
        self.card_total_table.verticalHeader().setVisible(False)
        bonus_defaults = self.build.get("card_bonus_levels", {})
        for row, attr in enumerate(CARD_PREVIEW_ATTRS):
            self.card_total_table.setItem(row, 0, self._item(attr))
            self.card_total_table.setItem(row, 1, QTableWidgetItem(""))
            spin = QSpinBox()
            spin.setRange(0, 0 if attr == "致命伤害" else 25)
            spin.setValue(0 if attr == "致命伤害" else int(bonus_defaults.get(attr, 0) or 0))
            if attr == "致命伤害":
                spin.setEnabled(False)
                spin.setToolTip("爆伤不受卡片掌握等级加成")
            spin.valueChanged.connect(self.schedule_refresh)
            self.card_total_table.setCellWidget(row, 2, spin)
            self.card_total_table.setItem(row, 3, QTableWidgetItem(""))
        self._configure_table(self.card_total_table, [88, 92, 82, 98])
        self.card_total_table.setMinimumWidth(390)
        self.card_total_table.setMinimumHeight(390)
        self.card_total_table.setSizeAdjustPolicy(QTableWidget.AdjustToContents)
        total_layout.addWidget(self.card_total_table)
        content.addWidget(total_box, 2)
        layout.addLayout(content, 1)
        return self._scroll(page)

    def _talisman_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "70级强化护符",
            "200%、175%、125%、100%槽位各两个；倍率同时作用于主属性和B护符额外属性。",
        )
        box, box_layout = self._build_section(
            "护符槽位",
            "C品质使用次级护符；B品质最终护符包含次级、中级、高级三种。",
        )
        self.talisman_table = AppTableWidget(len(TALISMAN_SLOTS), 5)
        self.talisman_table.setHorizontalHeaderLabels(
            ["槽位倍率", "品质", "护符", "额外属性", "实际加成"]
        )
        self.talisman_table.verticalHeader().setVisible(False)
        defaults = {
            norm(item.get("slot")): item
            for item in self.build.get("talismans", [])
            if isinstance(item, dict)
        }
        for row, slot in enumerate(TALISMAN_SLOTS):
            item = defaults.get(slot, {})
            grade_value = norm(item.get("grade")) or "无"
            name_value = norm(item.get("name")) or "无"
            extra_value = norm(item.get("extra")) or "无"
            self.talisman_table.setItem(row, 0, self._item(slot))
            grade = Combo(self.store.talisman_grades(), grade_value, min_width=76)
            name = Combo(self.store.talisman_names(grade_value), name_value, min_width=220)
            extra = Combo(
                self.store.talisman_extra_options(grade_value, name_value),
                extra_value,
                min_width=150,
            )
            self.talisman_table.setCellWidget(row, 1, grade)
            self.talisman_table.setCellWidget(row, 2, name)
            self.talisman_table.setCellWidget(row, 3, extra)
            self.talisman_table.setItem(row, 4, QTableWidgetItem(""))
            grade.currentTextChanged.connect(
                lambda _text, talisman_row=row: self._talisman_grade_changed(talisman_row)
            )
            name.currentTextChanged.connect(
                lambda _text, talisman_row=row: self._talisman_name_changed(talisman_row)
            )
            extra.currentTextChanged.connect(self.schedule_refresh)
        self._mark_build_table(self.talisman_table)
        self._configure_table(self.talisman_table, [108, 78, 250, 175, 310])
        self.talisman_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.talisman_table.verticalHeader().setDefaultSectionSize(34)
        self._fit_table_height(self.talisman_table)
        box_layout.addWidget(self.talisman_table)
        layout.addWidget(box)
        layout.addStretch(1)
        return self._scroll(page, horizontal=False)

    def _misc_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "其他与技能 Buff",
            "集中管理收集、称号、专精、时装与技能 Buff；完整面板已移至独立的“完整属性”页。",
        )
        toolbar, auto_row = self._build_toolbar()
        misc_button = app_button("一键全选", primary=True)
        misc_button.clicked.connect(self.auto_misc)
        auto_row.addWidget(misc_button)
        select_buffs_button = app_button("一键选择buff")
        select_buffs_button.clicked.connect(self.auto_current_job_buffs)
        auto_row.addWidget(select_buffs_button)
        clear_foreign_buffs_button = app_button("一键清除buff")
        clear_foreign_buffs_button.clicked.connect(self.clear_non_current_job_buffs)
        auto_row.addWidget(clear_foreign_buffs_button)
        auto_row.addStretch(1)
        layout.addWidget(toolbar)

        top = QHBoxLayout()
        top.setSpacing(10)
        box = QGroupBox("收集 / 称号")
        box.setObjectName("buildSection")
        form = QFormLayout(box)
        form.setContentsMargins(10, 12, 10, 10)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(7)
        self.collection_level = QSpinBox()
        self.collection_level.setRange(0, 42)
        self.collection_level.setValue(int(self.build.get("collection_level") or 0))
        self.collection_level.valueChanged.connect(self.schedule_refresh)
        self.title_combo = Combo(self.store.titles(), self.build.get("title"), self.schedule_refresh)
        self.collection_preview = QLabel("")
        self.collection_preview.setObjectName("buildValuePreview")
        self.collection_preview.setWordWrap(True)
        self.title_preview = QLabel("")
        self.title_preview.setObjectName("buildValuePreview")
        self.title_preview.setWordWrap(True)
        form.addRow("收集等级", self.collection_level)
        form.addRow("收集加成", self.collection_preview)
        form.addRow("称号", self.title_combo)
        form.addRow("称号加成", self.title_preview)
        top.addWidget(box, 1)

        self.mastery_table = AppTableWidget(len(MASTERY_NAMES), 3)
        self._mark_build_table(self.mastery_table)
        self.mastery_table.setHorizontalHeaderLabels(["专精项目", "等级", "具体加成"])
        self.mastery_table.verticalHeader().setVisible(False)
        defaults = {norm(item.get("name")): item.get("level") for item in self.build.get("mastery", [])}
        for row, name in enumerate(MASTERY_NAMES):
            self.mastery_table.setItem(row, 0, self._item(name))
            spin = QSpinBox()
            spin.setRange(0, 10)
            spin.setValue(int(defaults.get(name) or 0))
            spin.valueChanged.connect(self.schedule_refresh)
            self.mastery_table.setCellWidget(row, 1, spin)
            self.mastery_table.setItem(row, 2, QTableWidgetItem(""))
        self._configure_table(self.mastery_table, [180, 80, 260])
        self.mastery_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._fit_table_height(self.mastery_table)
        mastery_box = QGroupBox("专精")
        mastery_box.setObjectName("buildSection")
        mastery_layout = QVBoxLayout(mastery_box)
        mastery_layout.setContentsMargins(10, 12, 10, 10)
        mastery_layout.setSpacing(7)
        mastery_hint = QLabel("等级变化会立即计入属性；职业默认专精仍由现有配装规则决定。")
        mastery_hint.setObjectName("buildSectionHint")
        mastery_hint.setWordWrap(True)
        mastery_layout.addWidget(mastery_hint)
        mastery_layout.addWidget(self.mastery_table)
        top.addWidget(mastery_box, 2)
        layout.addLayout(top)

        fashion_box = QGroupBox("时装")
        fashion_box.setObjectName("buildSection")
        fashion_layout = QVBoxLayout(fashion_box)
        fashion_layout.setContentsMargins(10, 12, 10, 10)
        fashion_layout.setSpacing(7)
        fashion_hint = QLabel("按部位选择当前时装，具体加成在同一行显示，方便快速核对遗漏。")
        fashion_hint.setObjectName("buildSectionHint")
        fashion_hint.setWordWrap(True)
        fashion_layout.addWidget(fashion_hint)

        self.fashion_table = AppTableWidget(len(FASHION_SLOTS), 3)
        self._mark_build_table(self.fashion_table)
        self.fashion_table.setHorizontalHeaderLabels(["部位", "当前选择", "具体加成"])
        self.fashion_table.verticalHeader().setVisible(False)
        defaults = {norm(item.get("slot")): item.get("name") for item in self.build.get("fashion", [])}
        fashion_option_cache: dict[str, list[str]] = {}
        for row, slot in enumerate(FASHION_SLOTS):
            self.fashion_table.setItem(row, 0, self._item(slot))
            option_slot = "戒指" if slot.startswith("戒指") else slot
            options = self.store.fashion_options(option_slot)
            fashion_option_cache[slot] = options
            width = option_width(options, 96, 190)
            self.fashion_table.setCellWidget(row, 1, Combo(options, defaults.get(slot) or defaults.get(option_slot) or "无", self.schedule_refresh, min_width=width, contents_length=0))
            self.fashion_table.setItem(row, 2, QTableWidgetItem(""))
        fashion_select_width = max(option_width(values, 96, 190) for values in fashion_option_cache.values())
        self._configure_table(self.fashion_table, [58, fashion_select_width, 210])
        self.fashion_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._fit_table_height(self.fashion_table)
        fashion_layout.addWidget(self.fashion_table)
        layout.addWidget(fashion_box)

        buff_box = QGroupBox("技能 Buff")
        buff_box.setObjectName("buildSection")
        buff_layout = QVBoxLayout(buff_box)
        buff_layout.setContentsMargins(10, 12, 10, 10)
        buff_layout.setSpacing(7)
        buff_hint = QLabel("这里只配置参与面板计算的技能 Buff；技能等级与具体加成会联动刷新。")
        buff_hint.setObjectName("buildSectionHint")
        buff_hint.setWordWrap(True)
        buff_layout.addWidget(buff_hint)
        self.buff_table = AppTableWidget(10, 3)
        self._mark_build_table(self.buff_table)
        self.buff_table.setHorizontalHeaderLabels(["技能 Buff", "等级", "具体加成"])
        defaults = self.build.get("buffs", [])
        skills = self.store.skill_names(self.first_job.currentText(), self.second_job.currentText())
        buff_skill_width = option_width(skills, 110, 220)
        for row in range(10):
            item = defaults[row] if row < len(defaults) else {}
            current_skill = self.store.skill_display_name(item.get("skill") or "无")
            level_options = self.store.skill_levels(current_skill)
            self.buff_table.setCellWidget(row, 0, Combo(skills, current_skill, lambda _text, r=row: self._buff_skill_changed(r), min_width=buff_skill_width, contents_length=0))
            self.buff_table.setCellWidget(row, 1, Combo(level_options, item.get("level") or "无", self.schedule_refresh, min_width=option_width(level_options, 68, 96), contents_length=0))
            self.buff_table.setItem(row, 2, QTableWidgetItem(""))
        self._configure_table(self.buff_table, [buff_skill_width, 92, 210])
        self.buff_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._fit_table_height(self.buff_table)
        buff_layout.addWidget(self.buff_table)
        self._adapt_misc_column_widths()
        layout.addWidget(buff_box)
        layout.addStretch(1)
        return self._scroll(page, horizontal=False)
