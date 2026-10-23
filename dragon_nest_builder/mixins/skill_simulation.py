from __future__ import annotations

from ..shared import *
from .skill_timeline_baseline_policy import SkillTimelineBaselinePolicyMixin
from .skill_timeline_engine import SkillTimelineEngineMixin
from .skill_timeline_local_optimizer import SkillTimelineLocalOptimizerMixin
from .skill_timeline_optimizer import SkillTimelineOptimizerMixin
from .skill_timeline_policy_1_6 import SkillTimelinePolicy16Mixin
from .skill_timeline_scheme_1 import SkillTimelineScheme1Mixin
from .skill_timeline_support import SkillTimelineSupportMixin
from .skill_timeline_ui import SkillTimelineUiMixin
from . import skill_timeline_engine as _engine_module
from . import skill_timeline_support as _support_module
from . import skill_timeline_ui as _ui_module


class SkillSimulationMixin(
    SkillTimelineUiMixin,
    SkillTimelineScheme1Mixin,
    SkillTimelinePolicy16Mixin,
    SkillTimelineLocalOptimizerMixin,
    SkillTimelineOptimizerMixin,
    SkillTimelineBaselinePolicyMixin,
    SkillTimelineSupportMixin,
    SkillTimelineEngineMixin,
):
    def _skill_sim_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        defaults = self.build.get("skill_sim", [])
        if isinstance(defaults, dict):
            defaults = [defaults]
        if not isinstance(defaults, list):
            defaults = []
        settings = self.build.get("skill_sim_settings", {}) if isinstance(self.build.get("skill_sim_settings", {}), dict) else {}
        initial_cdr_percent = self._skill_cdr_percent_from_settings(settings)

        self.skill_sim_table = AppTableWidget(SKILL_SIM_ROWS + 1, SKILL_SIM_COL_COUNT)
        self.skill_sim_table.setHorizontalHeaderLabels(["技能", "等级", "所需等级", "冷却(s)", "倍率", "灼烧手", "技能数值", "等效倍率", "DPS", "施法时间(s)", "DPA"])
        skills = self.store.skill_damage_names(self.second_job.currentText())
        for row in range(SKILL_SIM_ROWS):
            item = defaults[row] if row < len(defaults) and isinstance(defaults[row], dict) else {}
            skill_name = item.get("skill") or "无"
            if "ex" in norm(skill_name).lower():
                skill_name = "无"
            self.skill_sim_table.setCellWidget(row, 0, Combo(skills, skill_name, lambda _text, r=row: self._skill_sim_skill_changed(r), min_width=168, contents_length=0))
            self.skill_sim_table.setCellWidget(
                row,
                1,
                Combo(
                    self.store.skill_damage_levels(self.second_job.currentText(), skill_name),
                    item.get("level") or "无",
                    lambda _text, r=row: self._skill_sim_level_changed(r),
                    min_width=42,
                    align=Qt.AlignCenter,
                    contents_length=0,
                    object_name="skillLevelCombo",
                ),
            )
            for col in (2, 5, 6, 7, 8, SKILL_SIM_COL_CAST_TIME, SKILL_SIM_COL_DPA):
                self.skill_sim_table.setItem(row, col, QTableWidgetItem(""))
            cooldown = QDoubleSpinBox()
            cooldown.setRange(0, 999)
            cooldown.setDecimals(2)
            cooldown.setSingleStep(0.1)
            cooldown.setFixedHeight(EDIT_ROW_HEIGHT)
            level_data = self.store.skill_damage_level_data(self.second_job.currentText(), skill_name, item.get("level") or "无")
            cooldown_default = self._skill_adjusted_cooldown(skill_name, level_data) if level_data else as_number(item.get("cooldown"))
            fixed_cooldown = self._skill_fixed_cooldown(skill_name)
            cooldown.setProperty("base_cooldown", float(cooldown_default or 0))
            cooldown.setValue(
                fixed_cooldown
                if fixed_cooldown is not None
                else self._skill_effective_cooldown(skill_name, cooldown_default, initial_cdr_percent)
            )
            cooldown.setReadOnly(fixed_cooldown is not None)
            cooldown.valueChanged.connect(lambda value, r=row: self._skill_cooldown_changed(r, value))
            self.skill_sim_table.setCellWidget(row, 3, cooldown)
            multiplier = QDoubleSpinBox()
            multiplier.setRange(0, 999)
            multiplier.setDecimals(2)
            multiplier.setSingleStep(0.1)
            multiplier.setFixedHeight(EDIT_ROW_HEIGHT)
            multiplier.setValue(self._skill_adjusted_multiplier(skill_name, item.get("level") or "无"))
            multiplier.valueChanged.connect(self.schedule_refresh)
            self.skill_sim_table.setCellWidget(row, 4, multiplier)
        total_row = SKILL_SIM_ROWS
        self.skill_sim_table.setItem(total_row, 0, self._item("DPS总和"))
        self.skill_sim_table.setSpan(total_row, 0, 1, 6)
        self.skill_sim_table.setItem(total_row, 6, QTableWidgetItem(""))
        self.skill_sim_table.setSpan(total_row, 6, 1, SKILL_SIM_COL_COUNT - 6)
        self._configure_table(self.skill_sim_table, [168, 52, 70, 68, 58, 68, 190, 180, 150, 92, 150])
        skill_header = self.skill_sim_table.horizontalHeader()
        for col in (6, 7, 8, SKILL_SIM_COL_DPA):
            skill_header.setSectionResizeMode(col, QHeaderView.Stretch)
        self.skill_sim_table.setColumnHidden(SKILL_SIM_COL_DPA, True)
        for row in range(self.skill_sim_table.rowCount()):
            self.skill_sim_table.setRowHeight(row, EDIT_ROW_HEIGHT)
        self.skill_sim_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.skill_sim_table.setFixedHeight(EDIT_ROW_HEIGHT * (self.skill_sim_table.rowCount() + 1) + 4)
        self._sync_skill_burning_hand_column()

        self.skill_dpa_table = AppTableWidget(SKILL_SIM_ROWS + 1, 6)
        self.skill_dpa_table.setHorizontalHeaderLabels(["技能", "等级", "施法时间(s)", "技能数值", "等效倍率", "DPA"])
        self.skill_dpa_table.verticalHeader().setVisible(False)
        for row in range(SKILL_SIM_ROWS):
            self.skill_dpa_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            for col in range(self.skill_dpa_table.columnCount()):
                self.skill_dpa_table.setItem(row, col, QTableWidgetItem(""))
        self.skill_dpa_table.setItem(SKILL_SIM_ROWS, 0, self._item("DPA参考"))
        self.skill_dpa_table.setSpan(SKILL_SIM_ROWS, 0, 1, 3)
        self.skill_dpa_table.setItem(SKILL_SIM_ROWS, 3, QTableWidgetItem(""))
        self.skill_dpa_table.setSpan(SKILL_SIM_ROWS, 3, 1, 3)
        self._configure_table(self.skill_dpa_table, [168, 52, 92, 240, 220, 180])
        self.skill_dpa_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.skill_dpa_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.skill_dpa_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.skill_dpa_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.skill_dpa_table.setFixedHeight(EDIT_ROW_HEIGHT * (self.skill_dpa_table.rowCount() + 1) + 4)

        self.skill_burst_timeline = SkillBurstTimelineWidget(
            editable=True,
            accent="#0f766e",
            pixels_per_second=30,
        )
        self.skill_burst_timeline.sequenceMoveRequested.connect(self._skill_burst_move_segment)
        self.skill_burst_timeline.segmentSelected.connect(self._skill_burst_select_segment)
        self.skill_burst_scroll = QScrollArea()
        self.skill_burst_scroll.setWidgetResizable(False)
        self.skill_burst_scroll.setFrameShape(QFrame.NoFrame)
        self.skill_burst_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.skill_burst_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.skill_burst_scroll.setWidget(self.skill_burst_timeline)
        self.skill_burst_scroll.setMinimumHeight(270)
        saved_burst_sequence = settings.get("burst_manual_sequence")
        saved_burst_job = norm(settings.get("burst_manual_job"))
        current_burst_job = norm(self.second_job.currentText())
        self._skill_burst_manual_sequence = (
            [norm(skill) for skill in saved_burst_sequence if norm(skill)]
            if isinstance(saved_burst_sequence, list) and saved_burst_job == current_burst_job
            else None
        )
        self._skill_burst_manual_job = current_burst_job
        self._skill_burst_last_segments: list[dict[str, Any]] = []
        self._skill_burst_auto_segments: list[dict[str, Any]] = []
        self._skill_burst_available_skills: list[str] = []
        self._skill_burst_candidate_metrics: list[dict[str, Any]] = []
        self._skill_burst_last_all_metrics: list[dict[str, Any]] = []
        self._skill_burst_last_support_actions: list[dict[str, Any]] = []
        self._skill_burst_last_window = 0.0
        self._skill_burst_selected_index = -1
        self.skill_burst_auto_total_label = QLabel("自动轴 0%")
        self.skill_burst_auto_total_label.setObjectName("skillBurstAxisTotal")
        self.skill_burst_auto_total_label.setTextFormat(Qt.RichText)
        self.skill_burst_auto_total_label.setAlignment(Qt.AlignCenter)
        self.skill_burst_auto_total_label.setWordWrap(True)
        self.skill_burst_auto_total_label.setMinimumHeight(64)
        self.skill_burst_auto_total_label.setStyleSheet(
            "QLabel { background:#eff6ff; border:1px solid #93c5fd; border-radius:6px; "
            "color:#1d4ed8; font-size:16px; font-weight:700; padding:6px 12px; }"
        )
        self.skill_burst_total_label = QLabel("总技能百分比\n0%")
        self.skill_burst_total_label.setObjectName("skillBurstTotalLabel")
        self.skill_burst_total_label.setTextFormat(Qt.RichText)
        self.skill_burst_total_label.setAlignment(Qt.AlignCenter)
        self.skill_burst_total_label.setWordWrap(True)
        self.skill_burst_total_label.setMinimumHeight(64)
        self.skill_burst_total_label.setStyleSheet(
            "QLabel { background:#ecfdf5; border:1px solid #6ee7b7; border-radius:6px; "
            "color:#047857; font-size:16px; font-weight:700; padding:6px 12px; }"
        )
        burst_controls = QHBoxLayout()
        self.skill_burst_auto_button = app_button("恢复自动轴", primary=True)
        self.skill_burst_auto_button.clicked.connect(self._skill_burst_restore_auto)
        self.skill_burst_sim_burst_check = QCheckBox("精神爆发")
        self.skill_burst_sim_accel_check = QCheckBox("时间加速")
        self.skill_burst_sim_cocktail_check = QCheckBox("鸡尾酒")
        self.skill_burst_sim_practical_check = QCheckBox("实战模拟")
        has_saved_burst_sim_mode = any(
            key in settings
            for key in (
                "burst_sim_burst",
                "burst_sim_accel",
                "burst_sim_cocktail",
                "burst_sim_practical",
            )
        )
        has_enabled_burst_sim_mode = any(
            bool(settings.get(key))
            for key in (
                "burst_sim_burst",
                "burst_sim_accel",
                "burst_sim_cocktail",
                "burst_sim_practical",
            )
        )
        for checkbox, key in (
            (self.skill_burst_sim_burst_check, "burst_sim_burst"),
            (self.skill_burst_sim_accel_check, "burst_sim_accel"),
            (self.skill_burst_sim_cocktail_check, "burst_sim_cocktail"),
            (self.skill_burst_sim_practical_check, "burst_sim_practical"),
        ):
            checkbox.setChecked(
                bool(settings.get(
                    key,
                    key == "burst_sim_practical" and not has_saved_burst_sim_mode,
                ))
                or (key == "burst_sim_practical" and has_saved_burst_sim_mode and not has_enabled_burst_sim_mode)
            )
            checkbox.stateChanged.connect(
                lambda state, mode=key.removeprefix("burst_sim_"): self._skill_burst_mode_toggled(mode, state)
            )
            burst_controls.addWidget(checkbox)
        if self.skill_burst_sim_practical_check.isChecked():
            for checkbox in (
                self.skill_burst_sim_burst_check,
                self.skill_burst_sim_accel_check,
                self.skill_burst_sim_cocktail_check,
            ):
                checkbox.setChecked(False)
        burst_controls.addWidget(self.skill_burst_auto_button)
        burst_controls.addWidget(QLabel("先铺减抗/增伤，持续到期后优先补；其余时间按DPA排入技能"))
        burst_controls.addStretch(1)

        # Kept as hidden compatibility controls for saved/manual sequences. Timeline
        # editing itself now lives in the skill column of the detail table.
        self.skill_burst_add_combo = Combo(["选择技能"], "选择技能", min_width=150)
        self.skill_burst_add_button = app_button("添加")
        self.skill_burst_up_button = app_button("上移")
        self.skill_burst_down_button = app_button("下移")
        self.skill_burst_remove_button = app_button("删除")
        self.skill_burst_add_button.clicked.connect(self._skill_burst_add_segment)
        self.skill_burst_up_button.clicked.connect(lambda: self._skill_burst_move_selected(-1))
        self.skill_burst_down_button.clicked.connect(lambda: self._skill_burst_move_selected(1))
        self.skill_burst_remove_button.clicked.connect(self._skill_burst_remove_selected)

        self.skill_burst_candidate_widget = QWidget()
        self.skill_burst_candidate_layout = QHBoxLayout(self.skill_burst_candidate_widget)
        self.skill_burst_candidate_layout.setContentsMargins(3, 1, 3, 1)
        self.skill_burst_candidate_layout.setSpacing(4)
        self.skill_burst_candidate_layout.addWidget(QLabel("暂无备选技能"))
        self.skill_burst_candidate_layout.addStretch(1)
        self.skill_burst_candidate_scroll = QScrollArea()
        self.skill_burst_candidate_scroll.setWidgetResizable(True)
        self.skill_burst_candidate_scroll.setFrameShape(QFrame.NoFrame)
        self.skill_burst_candidate_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.skill_burst_candidate_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.skill_burst_candidate_scroll.setFixedHeight(54)
        self.skill_burst_candidate_scroll.setWidget(self.skill_burst_candidate_widget)

        axis_group = QGroupBox("自动轴 / 自选轴")
        axis_group.setObjectName("burstAxisGroup")
        axis_layout = QVBoxLayout(axis_group)
        axis_layout.setContentsMargins(8, 6, 8, 6)
        axis_layout.setSpacing(4)
        axis_totals = QHBoxLayout()
        axis_totals.setSpacing(8)
        axis_totals.addWidget(self.skill_burst_auto_total_label, 1)
        axis_totals.addWidget(self.skill_burst_total_label, 1)
        axis_totals.addWidget(QLabel("时间轴缩放"))
        self.skill_burst_zoom_slider = QSlider(Qt.Horizontal)
        self.skill_burst_zoom_slider.setRange(10, 80)
        self.skill_burst_zoom_slider.setValue(30)
        self.skill_burst_zoom_slider.setFixedWidth(150)
        self.skill_burst_zoom_slider.setToolTip("调整每秒占用的横向宽度")
        self.skill_burst_zoom_slider.valueChanged.connect(self.skill_burst_timeline.set_pixels_per_second)
        axis_totals.addWidget(self.skill_burst_zoom_slider)
        self.skill_burst_zoom_value = QLabel(f"{self.skill_burst_zoom_slider.value()} px/s")
        self.skill_burst_zoom_value.setMinimumWidth(64)
        self.skill_burst_zoom_slider.valueChanged.connect(
            lambda value: self.skill_burst_zoom_value.setText(f"{value} px/s")
        )
        axis_totals.addWidget(self.skill_burst_zoom_value)
        axis_layout.addLayout(axis_totals)
        axis_layout.addWidget(self.skill_burst_scroll)
        axis_layout.addWidget(QLabel("高DPA备选（保留已学习伤害技能和大招）"))
        axis_layout.addWidget(self.skill_burst_candidate_scroll)
        axis_style = (
            "QGroupBox#burstAxisGroup {"
            "border:1px solid #cbd5e1; border-radius:6px; margin-top:8px; padding-top:5px; background:#ffffff;}"
            "QGroupBox#burstAxisGroup::title {"
            "subcontrol-origin:margin; left:10px; padding:0 6px; color:#334155; font-weight:600;}"
        )
        axis_group.setStyleSheet(axis_style)

        self.skill_burst_contribution_table = AppTableWidget(0, 5)
        self.skill_burst_contribution_table.setHorizontalHeaderLabels(
            ["技能", "次数", "基础百分比", "总输出", "占比"]
        )
        self.skill_burst_contribution_table.verticalHeader().setVisible(False)
        self._configure_table(self.skill_burst_contribution_table, [92, 42, 104, 96, 50])
        self.skill_burst_contribution_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for column in (0, 1, 3, 4):
            self.skill_burst_contribution_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
        self.skill_burst_contribution_table.setSelectionMode(QAbstractItemView.NoSelection)

        self.skill_burst_detail_table = TimelineSequenceTable(0, 8)
        self.skill_burst_detail_table.setHorizontalHeaderLabels(
            ["序号", "类型", "技能", "开始", "结束", "施法", "基础百分比", "单次输出/DPA"]
        )
        self.skill_burst_detail_table.verticalHeader().setVisible(False)
        self._configure_table(self.skill_burst_detail_table, [40, 68, 104, 52, 52, 52, 126, 210])
        for column in (0, 1, 2, 3, 4, 5, 6):
            self.skill_burst_detail_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
        self.skill_burst_detail_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.skill_burst_detail_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.skill_burst_detail_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.skill_burst_detail_table.cellClicked.connect(self._skill_burst_detail_selected)
        self.skill_burst_detail_table.rowMoveRequested.connect(self._skill_burst_detail_row_moved)

        self.skill_burst_average_table = AppTableWidget(6, 3)
        self.skill_burst_average_table.setHorizontalHeaderLabels(
            ["模式", "平均输出", "提升率"]
        )
        self.skill_burst_average_table.verticalHeader().setVisible(False)
        self._configure_table(self.skill_burst_average_table, [128, 90, 64])
        self.skill_burst_average_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.skill_burst_average_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.skill_burst_average_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.skill_burst_average_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.skill_burst_average_table.setToolTip("平均输出 = 当前模式时间轴总百分比 / 模拟时间")
        for row, mode_name in enumerate(
            ("实战模拟", "鸡尾酒", "精神爆发", "精神爆发+鸡尾酒", "时间加速", "时间加速+精神爆发")
        ):
            self.skill_burst_average_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self._set_summary_item(self.skill_burst_average_table, row, 0, mode_name)
            self._set_summary_item(self.skill_burst_average_table, row, 1, "-", align_right=True)
            self._set_summary_item(self.skill_burst_average_table, row, 2, "-", align_right=True)

        contribution_group = QGroupBox("技能输出占比")
        contribution_layout = QVBoxLayout(contribution_group)
        contribution_layout.setContentsMargins(6, 8, 6, 6)
        contribution_layout.addWidget(self.skill_burst_contribution_table)
        contribution_group.setMinimumWidth(390)
        contribution_group.setMaximumWidth(470)

        detail_group = QGroupBox("技能列表 / 具体时刻")
        detail_layout = QVBoxLayout(detail_group)
        detail_layout.setContentsMargins(6, 8, 6, 6)
        detail_layout.addWidget(self.skill_burst_detail_table)
        detail_group.setMinimumWidth(540)

        average_group = QGroupBox("平均输出")
        average_layout = QVBoxLayout(average_group)
        average_layout.setContentsMargins(6, 8, 6, 6)
        average_layout.addWidget(self.skill_burst_average_table, 1)
        average_group.setMinimumWidth(290)
        average_group.setMaximumWidth(320)

        burst_analysis_splitter = QSplitter(Qt.Horizontal)
        burst_analysis_splitter.setChildrenCollapsible(False)
        burst_analysis_splitter.setHandleWidth(5)
        burst_analysis_splitter.addWidget(contribution_group)
        burst_analysis_splitter.addWidget(detail_group)
        burst_analysis_splitter.addWidget(average_group)
        burst_analysis_splitter.setStretchFactor(0, 0)
        burst_analysis_splitter.setStretchFactor(1, 1)
        burst_analysis_splitter.setStretchFactor(2, 0)
        burst_analysis_splitter.setSizes([420, 800, 300])
        burst_analysis_splitter.setMinimumHeight(232)

        def create_skill_description_table() -> AppTableWidget:
            table = AppTableWidget(SKILL_SIM_ROWS, 2)
            table.setHorizontalHeaderLabels(["技能序号", "技能描述"])
            table.verticalHeader().setVisible(False)
            self._configure_table(table, [70, 900])
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            for row in range(SKILL_SIM_ROWS):
                table.setRowHeight(row, EDIT_ROW_HEIGHT)
                index_item = self._item(str(row + 1))
                index_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, 0, index_item)
                table.setItem(row, 1, QTableWidgetItem(""))
            table.setFixedHeight(EDIT_ROW_HEIGHT * (SKILL_SIM_ROWS + 1) + 4)
            return table

        self.skill_desc_table = create_skill_description_table()
        self.skill_dpa_desc_table = create_skill_description_table()

        dps_page = QWidget()
        dps_layout = QVBoxLayout(dps_page)
        dps_layout.setContentsMargins(0, 0, 0, 0)
        dps_layout.addWidget(self.skill_sim_table)
        dps_layout.addWidget(self.skill_desc_table)
        dpa_page = QWidget()
        dpa_layout = QVBoxLayout(dpa_page)
        dpa_layout.setContentsMargins(0, 0, 0, 0)
        dpa_layout.addWidget(self.skill_dpa_table)
        dpa_layout.addWidget(self.skill_dpa_desc_table)
        burst_page = QWidget()
        burst_layout = QVBoxLayout(burst_page)
        burst_layout.setContentsMargins(0, 0, 0, 0)
        burst_layout.setSpacing(6)
        burst_layout.addLayout(burst_controls)
        burst_layout.addWidget(axis_group)
        burst_layout.addWidget(burst_analysis_splitter, 1)

        long_page = QWidget()
        long_layout = QVBoxLayout(long_page)
        long_layout.setContentsMargins(0, 0, 0, 0)
        long_layout.setSpacing(6)
        long_controls = QGridLayout()
        long_controls.setHorizontalSpacing(8)
        long_controls.setVerticalSpacing(4)
        long_controls.addWidget(QLabel("模拟窗口"), 0, 0)
        self.skill_long_window_spin = QDoubleSpinBox()
        self.skill_long_window_spin.setRange(30.0, 600.0)
        self.skill_long_window_spin.setDecimals(1)
        self.skill_long_window_spin.setSuffix("s")
        self.skill_long_window_spin.setValue(float(settings.get("long_window", SKILL_LONG_PRACTICAL_WINDOW) or SKILL_LONG_PRACTICAL_WINDOW))
        self.skill_long_window_spin.setFixedWidth(88)
        long_controls.addWidget(self.skill_long_window_spin, 0, 1)
        self.skill_long_cocktail_check = QCheckBox("鸡尾酒（常驻）")
        self.skill_long_cocktail_check.setChecked(bool(settings.get("long_cocktail", True)))
        long_controls.addWidget(self.skill_long_cocktail_check, 0, 2)
        self.skill_long_burst_checks: list[QCheckBox] = []
        self.skill_long_burst_starts: list[QDoubleSpinBox] = []
        self.skill_long_burst_start_base = QDoubleSpinBox()
        self.skill_long_burst_start_base.setRange(0.0, self.skill_long_window_spin.value())
        self.skill_long_burst_start_base.setDecimals(1)
        self.skill_long_burst_start_base.setSuffix("s")
        self.skill_long_burst_start_base.setValue(float(settings.get("long_burst_start", 0.0) or 0.0))
        self.skill_long_burst_start_base.setFixedWidth(82)
        self.skill_long_burst_interval = QDoubleSpinBox()
        self.skill_long_burst_interval.setRange(1.0, 300.0)
        self.skill_long_burst_interval.setDecimals(1)
        self.skill_long_burst_interval.setSuffix("s")
        self.skill_long_burst_interval.setValue(float(settings.get("long_burst_interval", 50.0) or 50.0))
        self.skill_long_burst_interval.setFixedWidth(82)
        self.skill_long_burst_selector = QComboBox()
        self.skill_long_burst_selector.setFixedWidth(112)
        self.skill_long_burst_adjust = QDoubleSpinBox()
        self.skill_long_burst_adjust.setRange(0.0, self.skill_long_window_spin.value())
        self.skill_long_burst_adjust.setDecimals(1)
        self.skill_long_burst_adjust.setSuffix("s")
        self.skill_long_burst_adjust.setFixedWidth(82)
        custom_burst_starts = settings.get("long_custom_burst_starts", {})
        self._skill_long_custom_burst_starts: dict[int, float] = (
            {int(index): float(value) for index, value in custom_burst_starts.items()}
            if isinstance(custom_burst_starts, dict)
            else {}
        )
        burst_duration = SKILL_BURST_SIM_DURATIONS["精神爆发"]
        burst_defaults = [0.0, 53.5, 107.0]
        for index, default_start in enumerate(burst_defaults):
            checkbox = QCheckBox(f"精神爆发{index + 1}")
            checkbox.setChecked(True)
            start_spin = QDoubleSpinBox()
            start_spin.setRange(0.0, SKILL_LONG_PRACTICAL_WINDOW - burst_duration)
            start_spin.setDecimals(1)
            start_spin.setSuffix("s")
            start_spin.setValue(default_start)
            start_spin.setFixedWidth(82)
            self.skill_long_burst_checks.append(checkbox)
            self.skill_long_burst_starts.append(start_spin)
            checkbox.hide()
            start_spin.hide()
        self.skill_long_burst_check = self.skill_long_burst_checks[0]
        self.skill_long_burst_start = self.skill_long_burst_starts[0]
        self.skill_long_accel_check = QCheckBox("时间加速")
        self.skill_long_accel_check.setChecked(True)
        self.skill_long_accel_start = QDoubleSpinBox()
        self.skill_long_accel_start.setRange(
            0.0,
            max(0.0, self.skill_long_window_spin.value() - SKILL_BURST_SIM_DURATIONS["时间加速"]),
        )
        self.skill_long_accel_start.setDecimals(1)
        self.skill_long_accel_start.setSuffix("s")
        self.skill_long_accel_start.setValue(float(settings.get("long_accel_start", 60.0) or 60.0))
        self.skill_long_accel_start.setFixedWidth(82)
        for widget in (
            self.skill_long_cocktail_check,
            self.skill_long_burst_start_base,
            self.skill_long_burst_interval,
            self.skill_long_burst_adjust,
            self.skill_long_accel_check,
            self.skill_long_accel_start,
        ):
            if isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.schedule_refresh)
            else:
                widget.valueChanged.connect(self.schedule_refresh)
        self.skill_long_burst_start_base.valueChanged.connect(self._skill_long_burst_pattern_changed)
        self.skill_long_burst_interval.valueChanged.connect(self._skill_long_burst_pattern_changed)
        self.skill_long_burst_selector.currentIndexChanged.connect(self._skill_long_burst_selector_changed)
        self.skill_long_burst_adjust.valueChanged.connect(self._skill_long_burst_adjust_changed)
        long_controls.addWidget(QLabel("起始爆发时间"), 1, 0)
        long_controls.addWidget(self.skill_long_burst_start_base, 1, 1)
        long_controls.addWidget(QLabel("爆发间隔"), 1, 2)
        long_controls.addWidget(self.skill_long_burst_interval, 1, 3)
        long_controls.addWidget(self.skill_long_burst_selector, 1, 4)
        long_controls.addWidget(self.skill_long_burst_adjust, 1, 5)
        long_controls.addWidget(self.skill_long_accel_check, 0, 3)
        long_controls.addWidget(self.skill_long_accel_start, 0, 4)
        long_controls.addWidget(QLabel("时间轴缩放"), 0, 5)
        self.skill_long_zoom_slider = QSlider(Qt.Horizontal)
        self.skill_long_zoom_slider.setRange(10, 80)
        self.skill_long_zoom_slider.setValue(30)
        self.skill_long_zoom_slider.setMinimumWidth(150)
        long_controls.addWidget(self.skill_long_zoom_slider, 0, 6)
        self.skill_long_zoom_value = QLabel(f"{self.skill_long_zoom_slider.value()} px/s")
        self.skill_long_zoom_value.setMinimumWidth(58)
        self.skill_long_zoom_slider.valueChanged.connect(
            lambda value: self.skill_long_zoom_value.setText(f"{value} px/s")
        )
        long_controls.addWidget(self.skill_long_zoom_value, 0, 7)
        self.skill_long_restore_button = app_button("恢复自动轴", primary=True)
        self.skill_long_restore_button.clicked.connect(self._skill_long_restore_auto)
        long_controls.addWidget(self.skill_long_restore_button, 0, 8)
        self.skill_long_team_link_check = QCheckBox("应用当前配队")
        self.skill_long_team_link_check.setChecked(bool(settings.get("long_team_link", False)))
        self.skill_long_team_link_check.setToolTip(
            "第一阶段联动：按配队页当前已启用的队友效果折算长时间实战输出；"
            "不改变技能轴和现有一键配队算法，取消勾选即恢复原计算。"
        )
        self.skill_long_team_link_check.stateChanged.connect(self.schedule_refresh)
        long_controls.addWidget(self.skill_long_team_link_check, 0, 9)
        long_controls.addWidget(
            QLabel("精神爆发默认从起始时间开始，并按爆发间隔自动铺满模拟窗口；选择某个精神爆发后可单独调整该次时间，后续爆发继续按间隔排序。时间加速按填写时间点释放，并每隔100s重复。"),
            2,
            0,
            1,
            10,
        )
        self.skill_long_window_spin.valueChanged.connect(self._skill_long_window_changed)
        self._skill_long_refresh_burst_selector()
        long_controls.setColumnStretch(6, 1)
        long_layout.addLayout(long_controls)

        self.skill_long_total_label = QLabel("长时间实战总输出 0%  |  平均 0%/s")
        self.skill_long_total_label.setTextFormat(Qt.RichText)
        self.skill_long_total_label.setAlignment(Qt.AlignCenter)
        self.skill_long_total_label.setMinimumHeight(42)
        self.skill_long_total_label.setStyleSheet(
            "QLabel { background:#f0fdfa; border:1px solid #5eead4; border-radius:6px; "
            "color:#0f766e; font-size:15px; font-weight:700; padding:6px 12px; }"
        )
        long_layout.addWidget(self.skill_long_total_label)

        self.skill_long_timeline = SkillBurstTimelineWidget(
            editable=True,
            accent="#0f766e",
            single_track=False,
            pixels_per_second=30,
        )
        self.skill_long_timeline.sequenceMoveRequested.connect(self._skill_long_move_segment)
        self.skill_long_timeline.segmentSelected.connect(self._skill_long_select_segment)
        self._skill_long_manual_sequence: list[str] | None = None
        self._skill_long_manual_job = norm(self.second_job.currentText())
        self._skill_long_auto_segments: list[dict[str, Any]] = []
        self._skill_long_last_segments: list[dict[str, Any]] = []
        self._skill_long_available_skills: list[str] = []
        self._skill_long_last_all_metrics: list[dict[str, Any]] = []
        self._skill_long_last_support_actions: list[dict[str, Any]] = []
        self._skill_long_candidate_metrics: list[dict[str, Any]] = []
        self._skill_long_last_window = 0.0
        self._skill_long_selected_index = -1
        self.skill_long_scroll = QScrollArea()
        self.skill_long_scroll.setWidgetResizable(False)
        self.skill_long_scroll.setFrameShape(QFrame.NoFrame)
        self.skill_long_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.skill_long_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.skill_long_scroll.setWidget(self.skill_long_timeline)
        self.skill_long_scroll.setMinimumHeight(246)
        self.skill_long_zoom_slider.valueChanged.connect(self.skill_long_timeline.set_pixels_per_second)
        long_layout.addWidget(self.skill_long_scroll)

        self.skill_long_candidate_widget = QWidget()
        self.skill_long_candidate_layout = QHBoxLayout(self.skill_long_candidate_widget)
        self.skill_long_candidate_layout.setContentsMargins(3, 1, 3, 1)
        self.skill_long_candidate_layout.setSpacing(4)
        self.skill_long_candidate_layout.addWidget(QLabel("暂无备选技能"))
        self.skill_long_candidate_layout.addStretch(1)
        self.skill_long_candidate_scroll = QScrollArea()
        self.skill_long_candidate_scroll.setWidgetResizable(True)
        self.skill_long_candidate_scroll.setFrameShape(QFrame.NoFrame)
        self.skill_long_candidate_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.skill_long_candidate_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.skill_long_candidate_scroll.setFixedHeight(54)
        self.skill_long_candidate_scroll.setWidget(self.skill_long_candidate_widget)
        long_layout.addWidget(QLabel("高DPA备选（按当前时刻的真实冷却排序）"))
        long_layout.addWidget(self.skill_long_candidate_scroll)

        self.skill_long_detail_table = TimelineSequenceTable(0, 7)
        self.skill_long_detail_table.setHorizontalHeaderLabels(
            ["序号", "类型", "技能", "开始", "结束", "基础百分比", "单次输出/DPA"]
        )
        self.skill_long_detail_table.verticalHeader().setVisible(False)
        self._configure_table(self.skill_long_detail_table, [44, 72, 112, 58, 58, 132, 220])
        for column in range(6):
            self.skill_long_detail_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
        self.skill_long_detail_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.skill_long_detail_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.skill_long_detail_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.skill_long_detail_table.cellClicked.connect(self._skill_long_detail_selected)
        self.skill_long_detail_table.rowMoveRequested.connect(self._skill_long_detail_row_moved)
        self.skill_long_contribution_table = AppTableWidget(0, 4)
        self.skill_long_contribution_table.setHorizontalHeaderLabels(["技能", "次数", "总伤害", "伤害比例"])
        self.skill_long_contribution_table.verticalHeader().setVisible(False)
        self._configure_table(self.skill_long_contribution_table, [112, 54, 104, 78])
        self.skill_long_contribution_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 4):
            self.skill_long_contribution_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Fixed)
        long_analysis_splitter = QSplitter(Qt.Horizontal)
        long_analysis_splitter.addWidget(self.skill_long_contribution_table)
        long_analysis_splitter.addWidget(self.skill_long_detail_table)
        long_analysis_splitter.setStretchFactor(0, 0)
        long_analysis_splitter.setStretchFactor(1, 1)
        long_analysis_splitter.setSizes([360, 980])
        long_layout.addWidget(long_analysis_splitter, 1)

        self.skill_result_tabs = QTabWidget()
        self.skill_result_tabs.addTab(dps_page, "DPS")
        self.skill_result_tabs.addTab(dpa_page, "DPA")
        self.skill_result_tabs.addTab(burst_page, "爆发/加速模拟")
        self.skill_result_tabs.addTab(long_page, "长时间实战模拟")
        self.skill_result_tabs.currentChanged.connect(lambda _index: self.schedule_refresh())

        header = QHBoxLayout()
        auto_button = app_button("一键模拟", primary=True)
        auto_button.clicked.connect(self.auto_fill_skill_sim)
        self.skill_burst_check = QCheckBox("爆发")
        self.skill_cocktail_check = QCheckBox("鸡尾酒")
        self.skill_accel_check = QCheckBox("加速")
        for checkbox, key in (
            (self.skill_burst_check, "burst"),
            (self.skill_cocktail_check, "cocktail"),
            (self.skill_accel_check, "accel"),
        ):
            checkbox.setChecked(bool(settings.get(key)))
            checkbox.stateChanged.connect(self._skill_cdr_changed)
        self.skill_custom_cdr = QDoubleSpinBox()
        self.skill_custom_cdr.setRange(0, 70)
        self.skill_custom_cdr.setDecimals(1)
        self.skill_custom_cdr.setSingleStep(1)
        self.skill_custom_cdr.setSuffix("%")
        self.skill_custom_cdr.setFixedHeight(EDIT_ROW_HEIGHT)
        self.skill_custom_cdr.setValue(float(settings.get("custom_cdr", 0) or 0))
        self.skill_custom_cdr.valueChanged.connect(self._skill_cdr_changed)
        header.addWidget(self.skill_burst_check)
        header.addWidget(self.skill_cocktail_check)
        header.addWidget(self.skill_accel_check)
        header.addWidget(QLabel("自定义冷却缩减"))
        header.addWidget(self.skill_custom_cdr)
        header.addWidget(auto_button)
        header_widget = QWidget()
        header_widget.setLayout(header)
        header.setContentsMargins(6, 0, 4, 0)
        header.setSpacing(6)
        self.skill_result_tabs.setCornerWidget(header_widget, Qt.TopRightCorner)
        layout.addWidget(self.skill_result_tabs)
        layout.addStretch(1)
        return page

    def _skill_sim_skill_changed(self, row: int, _text: str = "") -> None:
        if not hasattr(self, "skill_sim_table"):
            return
        skill_combo = self.skill_sim_table.cellWidget(row, 0)
        level_combo = self.skill_sim_table.cellWidget(row, 1)
        if isinstance(skill_combo, Combo) and isinstance(level_combo, Combo):
            if "ex" in norm(skill_combo.currentText()).lower():
                self._set_combo_current(skill_combo, "无")
            levels = self._skill_sim_available_levels(skill_combo.currentText())
            self._replace_combo_values(level_combo, levels, self._skill_default_level(skill_combo.currentText()))
        self._skill_sim_level_changed(row)

    def _skill_build_match_names_for_skill(self, skill: str) -> set[str]:
        entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
        names = {norm(skill)}
        if isinstance(entry, dict):
            names.add(norm(entry.get("name")))
            if entry.get("id") is not None:
                names.add(norm(entry.get("id")))
        alias_map = SKILL_BUILD_LEVEL_ALIASES_BY_JOB.get(norm(self.second_job.currentText()), {})
        for name in list(names):
            alias = norm(alias_map.get(name))
            if alias:
                names.add(alias)
        return {name for name in names if name}

    def _score_combat_skill_level(self, skill: str) -> int | None:
        from ..live_dps.scoring import canonical_skill
        report = getattr(self, 'build', {}).get('_score_combat_skill_levels', {})
        return report.get('levels', {}).get(canonical_skill(skill))

    def _skill_build_level_for_skill_name(self, skill: str) -> int | None:
        combat_level = self._score_combat_skill_level(skill)
        if combat_level is not None:
            return combat_level
        names = self._skill_build_match_names_for_skill(skill)
        tree = getattr(self, "skill_build_data", None)
        if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
            tree = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        default_levels = self._skill_build_payload_levels(self._skill_build_default_payload_for_current(), "skill_build")
        has_live_build = bool(getattr(self, "skill_build_level_spins", {}))
        for skill_id, skill_item in tree.get("skills", {}).items():
            if norm(skill_item.get("name")) in names or norm(skill_id) in names:
                spin = getattr(self, "skill_build_level_spins", {}).get(norm(skill_id))
                if isinstance(spin, QSpinBox):
                    return int(spin.value())
                if norm(skill_id) in default_levels:
                    return int(as_number(default_levels.get(norm(skill_id))))
                if not has_live_build and not default_levels:
                    return None
                return self._skill_build_free_default_level(skill_item)
        if has_live_build or default_levels:
            return 0
        return None

    def _skill_build_level_for_burst_sim(self, skill: str) -> int | None:
        current_level = self._skill_build_level_for_skill_name(skill)
        if current_level is not None:
            return current_level
        names = self._skill_build_match_names_for_skill(skill)
        default_levels = self._skill_build_payload_levels(self._skill_build_default_payload_for_current(), "skill_build")
        saved = self.build.get("skill_build", {}) if isinstance(self.build.get("skill_build", {}), dict) else {}
        saved_levels = saved.get("levels", {}) if isinstance(saved.get("levels", {}), dict) and norm(saved.get("second_job")) == norm(self.second_job.currentText()) else default_levels
        has_saved_filter = bool(saved_levels)
        tree = getattr(self, "skill_build_data", None)
        if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
            tree = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        for skill_id, skill_item in tree.get("skills", {}).items():
            if norm(skill_item.get("name")) in names or norm(skill_id) in names:
                if norm(skill_id) in saved_levels:
                    return int(as_number(saved_levels.get(norm(skill_id))))
                if not has_saved_filter and not getattr(self, "skill_build_level_spins", {}):
                    return None
                return self._skill_build_free_default_level(skill_item)
        return None

    def _skill_build_level_for_id(self, skill_id: str | int) -> int | None:
        key = norm(skill_id)
        spin = getattr(self, "skill_build_level_spins", {}).get(key)
        if isinstance(spin, QSpinBox):
            return int(spin.value())
        default_levels = self._skill_build_payload_levels(
            self._skill_build_default_payload_for_current(),
            "skill_build",
        )
        saved = self.build.get("skill_build", {}) if isinstance(self.build.get("skill_build", {}), dict) else {}
        saved_levels = (
            saved.get("levels", {})
            if isinstance(saved.get("levels", {}), dict)
            and norm(saved.get("second_job")) == norm(self.second_job.currentText())
            else default_levels
        )
        if key in saved_levels:
            return int(as_number(saved_levels.get(key)))
        tree = getattr(self, "skill_build_data", None)
        if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
            tree = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        skill_item = tree.get("skills", {}).get(key)
        if not isinstance(skill_item, dict):
            return None
        if not saved_levels and not getattr(self, "skill_build_level_spins", {}):
            return None
        return self._skill_build_free_default_level(skill_item)

    def _skill_sim_available_levels(self, skill: str) -> list[str]:
        combat_level = self._score_combat_skill_level(skill)
        if combat_level is not None:
            return [str(combat_level)]
        levels = self.store.skill_damage_levels(self.second_job.currentText(), skill)
        build_level = self._skill_build_level_for_skill_name(skill)
        if build_level is None:
            return levels
        filtered = [level for level in levels if int(as_number(level, -1)) <= build_level]
        return filtered or ["无"]

    def _skill_default_level(self, skill: str, level_cap: int = 70) -> str:
        combat_level = self._score_combat_skill_level(skill)
        if combat_level is not None:
            return str(combat_level)
        best_level = "无"
        best_key = (-1, -1.0)
        for level in self._skill_sim_available_levels(skill):
            level_data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, level)
            if not level_data:
                continue
            required_level = int(as_number(level_data[1]))
            if required_level > level_cap:
                continue
            key = (required_level, as_number(level))
            if key > best_key:
                best_key = key
                best_level = level
        return best_level

    def _skill_sim_level_changed(self, row: int, _text: str = "") -> None:
        if not hasattr(self, "skill_sim_table"):
            return
        skill = self.combo_text(self.skill_sim_table, row, 0)
        level_text = self.combo_text(self.skill_sim_table, row, 1)
        available_levels = self._skill_sim_available_levels(skill)
        if level_text not in set(available_levels):
            level_text = self._skill_default_level(skill)
            level_combo = self.skill_sim_table.cellWidget(row, 1)
            if isinstance(level_combo, Combo):
                self._replace_combo_values(level_combo, available_levels, level_text)
        level_data, effective_level, mastery_bonus = self._skill_sim_level_data_with_mastery(skill, level_text)
        cooldown = self.skill_sim_table.cellWidget(row, 3)
        if isinstance(cooldown, QDoubleSpinBox):
            base_cooldown = self._skill_adjusted_cooldown(skill, level_data) if level_data else 0
            fixed_cooldown = self._skill_fixed_cooldown(skill)
            special_cooldown = self._skill_special_effective_cooldown(skill)
            cooldown.blockSignals(True)
            cooldown.setProperty("base_cooldown", base_cooldown)
            cooldown.setValue(
                fixed_cooldown
                if fixed_cooldown is not None
                else special_cooldown
                if special_cooldown is not None
                else self._skill_effective_cooldown(skill, base_cooldown)
            )
            cooldown.setReadOnly(fixed_cooldown is not None or special_cooldown is not None)
            cooldown.blockSignals(False)
        multiplier = self.skill_sim_table.cellWidget(row, 4)
        if isinstance(multiplier, QDoubleSpinBox):
            multiplier.blockSignals(True)
            multiplier.setValue(self._skill_adjusted_multiplier(skill, effective_level if mastery_bonus else level_text))
            multiplier.blockSignals(False)
        self.schedule_refresh()

    def _skill_multiplier_override(self, skill: str, job: str | None = None) -> float | None:
        if self.build.get('_score_v2_profiles'):
            from ..live_dps.score_v2 import override_multiplier
            value = override_multiplier(self, skill, job)
            if value is not None:
                return value
        payload = self.build.get("skill_multiplier_overrides", {})
        if not isinstance(payload, dict):
            return None
        target_job = norm(job or self.second_job.currentText())
        if norm(payload.get("second_job")) != target_job:
            return None
        skill_key = self._skill_burst_skill_key(skill)
        for row in payload.get("rows", []) if isinstance(payload.get("rows"), list) else []:
            if not isinstance(row, dict) or self._skill_burst_skill_key(row.get("skill")) != skill_key:
                continue
            return max(0.0, as_number(row.get("value")))
        return None

    def _skill_adjusted_multiplier(self, skill: str, level: Any = None) -> float:
        override = self._skill_multiplier_override(skill)
        if override is not None:
            return override
        if (norm(self.second_job.currentText()) == '狂战士'
                and self._skill_burst_skill_key(skill) == '旋转冲击'
                and self._skill_build_level_for_id('505') == 0):
            return 1.0
        return self.store.skill_damage_multiplier(self.second_job.currentText(), skill, level)

    def _skill_sim_level_data_with_mastery(self, skill: str, level: Any) -> tuple[list[Any] | None, str, int]:
        combat_level = self._score_combat_skill_level(skill)
        if combat_level is not None:
            # Combat response is the effective level; do not add mastery twice.
            selected = str(combat_level)
            if combat_level <= 0:
                return None, selected, 0
            data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, selected, mode='normal')
            if data is None:
                data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, selected, mode='master')
            return data, selected, 0
        base_level = int(as_number(level, -1))
        if base_level <= 0:
            return None, norm(level), 0
        mastery_bonus = self._skill_mastery_bonus_for_skill(skill)
        effective_level = str(base_level + mastery_bonus)
        if mastery_bonus:
            data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, effective_level, mode="master")
            if data is not None:
                return data, effective_level, mastery_bonus
            data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, effective_level, mode="normal")
            if data is not None:
                return data, effective_level, mastery_bonus
        data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, str(base_level), mode="normal")
        if data is None:
            data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, str(base_level))
        return data, str(base_level), 0

    def _skill_burning_hand_mode(self) -> str:
        if getattr(self, "skill_accel_check", None) and self.skill_accel_check.isChecked():
            return "accel"
        burst = bool(getattr(self, "skill_burst_check", None) and self.skill_burst_check.isChecked())
        cocktail = bool(getattr(self, "skill_cocktail_check", None) and self.skill_cocktail_check.isChecked())
        if burst and cocktail:
            return "both"
        if burst:
            return "burst"
        if cocktail:
            return "cocktail"
        return "base"

    def _skill_burning_hand_bonus(self, skill: str) -> float:
        return self._skill_burning_hand_bonus_for_mode(skill, self._skill_burning_hand_mode())

    def _skill_burning_hand_bonus_for_mode(self, skill: str, mode: str) -> float:
        if norm(self.second_job.currentText()) != "火舞":
            return 0.0
        return self.store.skill_burning_hand_bonus(self.second_job.currentText(), skill, norm(mode) or "base")

    def _skill_burning_hand_mode_at(self, seconds: float) -> str:
        if self._skill_burst_mode_active_at("accel", seconds):
            return "accel"
        burst = self._skill_burst_mode_active_at("burst", seconds)
        cocktail = self._skill_burst_sim_enabled("cocktail")
        if burst and cocktail:
            return "both"
        if burst:
            return "burst"
        if cocktail:
            return "cocktail"
        return "base"

    def _skill_adjusted_cooldown(self, skill: str, level_data: list[Any] | None) -> float:
        adjustment = self.store.skill_damage_adjustment(self.second_job.currentText(), skill)
        if "cooldown" in adjustment:
            return max(0.0, as_number(adjustment.get("cooldown")))
        return self._skill_level_cooldown(level_data) if level_data else 0.0

    def _skill_fixed_cooldown(self, skill: str) -> float | None:
        entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
        skill_name = norm(entry.get("name") if entry else skill)
        value = SKILL_FIXED_COOLDOWNS.get(skill_name)
        return None if value is None else max(0.0, as_number(value))

    def _skill_summon_min_cooldown(self, skill: str) -> float | None:
        entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
        skill_name = norm(entry.get("name") if entry else skill)
        value = SKILL_SUMMON_MIN_COOLDOWNS.get(skill_name)
        return None if value is None else max(0.0, as_number(value))

    def _skill_special_effective_cooldown(
        self,
        skill: str,
        external_cdr_percent: float | None = None,
    ) -> float | None:
        job_name = norm(self.second_job.currentText())
        entry = self.store._skill_damage_entry(job_name, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        sources = SKILL_SIM_SPECIAL_COOLDOWN_SOURCES_BY_JOB.get(job_name, {}).get(skill_name)
        if not sources:
            return None
        cdr_percent = self._skill_cdr_percent() if external_cdr_percent is None else external_cdr_percent
        rates = 0.0
        for source_skill, fallback in sources:
            base = self.store.skill_reference_cooldown(job_name, source_skill, fallback)
            cooldown = max(0.0, as_number(base) * (1 - cdr_percent / 100))
            if cooldown <= 0:
                return None
            rates += 1 / cooldown
        return 1 / rates if rates > 0 else None

    def _skill_effective_cooldown(
        self,
        skill: str,
        base_cooldown: float,
        external_cdr_percent: float | None = None,
    ) -> float:
        entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
        skill_name = norm(entry.get("name") if entry else skill)
        cdr_percent = self._skill_cdr_percent() if external_cdr_percent is None else external_cdr_percent
        base_cooldown = max(0.0, as_number(base_cooldown)) * (
            1 - min(100.0, self._skill_glyph_effect_value(skill_name, "冷却缩减")) / 100
        )
        special_cooldown = self._skill_special_effective_cooldown(skill_name, cdr_percent)
        if special_cooldown is not None:
            cooldown = special_cooldown
        else:
            cooldown = self.skill_cooldown_engine.calculate(
                self.second_job.currentText(),
                skill_name,
                max(0.0, as_number(base_cooldown)),
                cdr_percent,
            )
        min_cooldown = self._skill_summon_min_cooldown(skill_name)
        if min_cooldown is not None:
            cooldown = max(cooldown, min_cooldown)
        return cooldown

    def _skill_cooldown_changed(self, row: int, value: float) -> None:
        cooldown = self.skill_sim_table.cellWidget(row, 3) if hasattr(self, "skill_sim_table") else None
        if isinstance(cooldown, QDoubleSpinBox):
            fixed_cooldown = self._skill_fixed_cooldown(self.combo_text(self.skill_sim_table, row, 0))
            special_cooldown = self._skill_special_effective_cooldown(self.combo_text(self.skill_sim_table, row, 0))
            if fixed_cooldown is not None:
                cooldown.blockSignals(True)
                cooldown.setProperty("base_cooldown", fixed_cooldown)
                cooldown.setValue(fixed_cooldown)
                cooldown.blockSignals(False)
                self.schedule_refresh()
                return
            if special_cooldown is not None:
                cooldown.blockSignals(True)
                cooldown.setValue(special_cooldown)
                cooldown.blockSignals(False)
                self.schedule_refresh()
                return
            summon_min_cooldown = self._skill_summon_min_cooldown(self.combo_text(self.skill_sim_table, row, 0))
            if summon_min_cooldown is not None and float(value) < summon_min_cooldown:
                cooldown.blockSignals(True)
                cooldown.setValue(summon_min_cooldown)
                cooldown.blockSignals(False)
                self.schedule_refresh()
                return
            entry = self.store._skill_damage_entry(
                self.second_job.currentText(),
                self.combo_text(self.skill_sim_table, row, 0),
            )
            skill_name = norm(entry.get("name") if entry else self.combo_text(self.skill_sim_table, row, 0))
            base = self.skill_cooldown_engine.infer_base_cooldown(
                self.second_job.currentText(),
                skill_name,
                float(value),
                self._skill_cdr_percent(),
            )
            cooldown.setProperty("base_cooldown", base)
        self.schedule_refresh()

    def _skill_cdr_changed(self, *_args) -> None:
        self._sync_skill_cooldown_displays()
        self.schedule_refresh()

    def _skill_cdr_percent(self) -> float:
        value = 0.0
        if getattr(self, "skill_burst_check", None) and self.skill_burst_check.isChecked():
            value += 40.0
        if getattr(self, "skill_cocktail_check", None) and self.skill_cocktail_check.isChecked():
            value += 12.0
        if getattr(self, "skill_accel_check", None) and self.skill_accel_check.isChecked():
            value += 70.0
        if getattr(self, "skill_custom_cdr", None):
            value += float(self.skill_custom_cdr.value())
        return min(70.0, max(0.0, value))

    @staticmethod
    def _skill_cdr_percent_from_settings(settings: dict[str, Any]) -> float:
        value = (40.0 if settings.get("burst") else 0.0) + (12.0 if settings.get("cocktail") else 0.0)
        value += 70.0 if settings.get("accel") else 0.0
        value += as_number(settings.get("custom_cdr"))
        return min(70.0, max(0.0, value))

    def _skill_action_speed_percent(self) -> float:
        value = 0.0
        if getattr(self, "skill_burst_check", None) and self.skill_burst_check.isChecked():
            value += 40.0
        return max(0.0, value)

    def _skill_cast_timing(self, skill: str) -> dict[str, Any]:
        second_job = self.second_job.currentText()
        first_job = self.first_job.currentText() if getattr(self, "first_job", None) else ""
        if (norm(second_job) == '狂战士' and self._skill_burst_skill_key(skill) == '旋转冲击'
                and self._skill_build_level_for_id('505') == 0):
            return self.store.skill_cast_time('旋转冲击', [first_job])
        cache_key = (norm(second_job), norm(first_job), norm(skill))
        cache = getattr(self, "_skill_cast_timing_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_cast_timing_cache = cache
        if cache_key in cache:
            return cache[cache_key]
        entry = self.store._skill_damage_entry(second_job, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        preferred_jobs = [second_job, first_job, self.store.job_family(second_job, first_job)]
        result = self.store.skill_cast_time(skill_name, preferred_jobs)
        cache[cache_key] = result
        return result

    def _skill_effective_cast_timing(self, skill: str, action_speed_percent: float | None = None) -> dict[str, float | str | bool]:
        timing = self._skill_cast_timing(skill)
        cast_time = as_number(timing.get("cast_time"))
        quick_time = as_number(timing.get("quick_time"))
        if cast_time <= 0 and quick_time <= 0:
            return {}

        fixed = bool(timing.get("fixed"))
        quick_fixed = bool(timing.get("quick_fixed"))
        fixed_duration_output = (
            self._skill_burst_skill_key(skill)
            in SKILL_FIXED_DURATION_ACTION_SPEED_OUTPUT_SKILLS
        )
        base_speed_percent = max(0.0, as_number(timing.get("speed_percent")))
        action_speed_percent = self._skill_action_speed_percent() if action_speed_percent is None else max(0.0, as_number(action_speed_percent))
        glyph_speed_percent = self._skill_glyph_action_speed(skill)
        total_external_speed = action_speed_percent + glyph_speed_percent
        speed_percent = base_speed_percent + total_external_speed
        timing_speed = total_external_speed

        # Only cache the pure calculation after resolving current skill,
        # glyphs and personal speed. UI edits still produce a different key.
        try:
            cache_key = (tuple(sorted(timing.items())), timing_speed, speed_percent, fixed_duration_output)
            hash(cache_key)
        except (TypeError, ValueError):
            cache_key = None
        cache = getattr(self, '_skill_effective_timing_values_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_effective_timing_values_cache = cache
        if cache_key is not None and cache_key in cache:
            return dict(cache[cache_key])

        effective_cast = self.store.effective_skill_cast_time(
            timing, timing_speed
        )
        effective_quick = self.store.effective_skill_cast_time(
            timing, timing_speed, quick=True
        )

        using_quick = quick_time > 0
        result = {
            "cast_time": effective_quick if using_quick else effective_cast,
            "quick_time": effective_quick,
            "full_cast_time": effective_cast,
            "base_cast_time": cast_time,
            "base_quick_time": quick_time,
            "speed_percent": speed_percent,
            "fixed": fixed_duration_output or (quick_fixed if using_quick else fixed),
            "using_quick": using_quick,
            "notes": norm(timing.get("notes")),
        }
        if cache_key is not None:
            if len(cache) >= 4096:
                cache.clear()
            cache[cache_key] = result
        return dict(result)

    def _skill_cast_time_text(self, skill: str) -> str:
        timing = self._skill_effective_cast_timing(skill)
        cast_time = as_number(timing.get("cast_time"))
        if cast_time <= 0:
            return ""
        text = f"{self._cooldown_text(cast_time)}s"
        if timing.get("using_quick"):
            full_cast_time = as_number(timing.get("full_cast_time"))
            text += " 快速衔接"
            if full_cast_time > cast_time + 1e-6:
                text += f"（完整{self._cooldown_text(full_cast_time)}s）"
        if timing.get("fixed"):
            text += " 固定"
        speed_percent = as_number(timing.get("speed_percent"))
        if speed_percent > 0 and not timing.get("fixed"):
            text += f"（动作速度+{self._cooldown_text(speed_percent)}%）"
        return text

    def _skill_base_cooldown(self, row: int) -> float:
        cooldown = self.skill_sim_table.cellWidget(row, 3) if hasattr(self, "skill_sim_table") else None
        if not isinstance(cooldown, QDoubleSpinBox):
            return 0.0
        fixed_cooldown = self._skill_fixed_cooldown(self.combo_text(self.skill_sim_table, row, 0))
        if fixed_cooldown is not None:
            return fixed_cooldown
        return as_number(cooldown.property("base_cooldown"), cooldown.value())

    def _skill_sim_auto_exclusive_group(self, skill: str) -> str:
        entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
        skill_name = norm(entry.get("name") if entry else skill)
        for index, group in enumerate(SKILL_SIM_AUTO_EXCLUSIVE_GROUPS):
            if skill_name in group:
                return f"group_{index}"
        return ""

    def _skill_sim_auto_duplicate_limit(self, skill: str) -> tuple[str, int]:
        entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
        skill_name = norm(entry.get("name") if entry else skill)
        config = SKILL_SIM_AUTO_DUPLICATE_LIMITS_BY_JOB.get(norm(self.second_job.currentText()))
        if not config:
            return "", 1
        group, limit = config
        return ("duplicate_limit", int(limit)) if skill_name in group else ("", 1)

    def _skill_sim_dps_multiplier(self, element: str, profile: dict[str, Any] | None = None) -> float:
        job_profile = profile or self._job_profile()
        return 0.5 if norm(job_profile.get("element_attr")) and not norm(element) else 1.0

    def _skill_adjusted_multiplier_for_job(self, job: str, skill: str, level: Any = None) -> float:
        override = self._skill_multiplier_override(skill, job)
        if override is not None:
            return override
        return self.store.skill_damage_multiplier(job, skill, level)

    def _skill_adjusted_cooldown_for_job(self, job: str, skill: str, level_data: list[Any] | None) -> float:
        adjustment = self.store.skill_damage_adjustment(job, skill)
        if "cooldown" in adjustment:
            return max(0.0, as_number(adjustment.get("cooldown")))
        return self._skill_level_cooldown(level_data) if level_data else 0.0

    def _skill_fixed_cooldown_for_job(self, job: str, skill: str) -> float | None:
        entry = self.store._skill_damage_entry(job, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        value = SKILL_FIXED_COOLDOWNS.get(skill_name)
        return None if value is None else max(0.0, as_number(value))

    def _skill_summon_min_cooldown_for_job(self, job: str, skill: str) -> float | None:
        entry = self.store._skill_damage_entry(job, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        value = SKILL_SUMMON_MIN_COOLDOWNS.get(skill_name)
        return None if value is None else max(0.0, as_number(value))

    def _skill_special_effective_cooldown_for_job(
        self,
        job: str,
        skill: str,
        external_cdr_percent: float = 0.0,
    ) -> float | None:
        job_name = norm(job)
        entry = self.store._skill_damage_entry(job_name, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        sources = SKILL_SIM_SPECIAL_COOLDOWN_SOURCES_BY_JOB.get(job_name, {}).get(skill_name)
        if not sources:
            return None
        rates = 0.0
        for source_skill, fallback in sources:
            base = self.store.skill_reference_cooldown(job_name, source_skill, fallback)
            cooldown = max(0.0, as_number(base) * (1 - external_cdr_percent / 100))
            if cooldown <= 0:
                return None
            rates += 1 / cooldown
        return 1 / rates if rates > 0 else None

    def _skill_effective_cooldown_for_job(
        self,
        job: str,
        skill: str,
        base_cooldown: float,
        external_cdr_percent: float = 0.0,
    ) -> float:
        entry = self.store._skill_damage_entry(job, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        special_cooldown = self._skill_special_effective_cooldown_for_job(job, skill_name, external_cdr_percent)
        if special_cooldown is not None:
            cooldown = special_cooldown
        else:
            cooldown = self.skill_cooldown_engine.calculate(
                job,
                skill_name,
                max(0.0, as_number(base_cooldown)),
                external_cdr_percent,
            )
        min_cooldown = self._skill_summon_min_cooldown_for_job(job, skill_name)
        if min_cooldown is not None:
            cooldown = max(cooldown, min_cooldown)
        return cooldown

    def _skill_sim_auto_exclusive_group_for_job(self, job: str, skill: str) -> str:
        entry = self.store._skill_damage_entry(job, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        for index, group in enumerate(SKILL_SIM_AUTO_EXCLUSIVE_GROUPS):
            if skill_name in group:
                return f"group_{index}"
        return ""

    def _skill_sim_auto_duplicate_limit_for_job(self, job: str, skill: str) -> tuple[str, int]:
        entry = self.store._skill_damage_entry(job, skill)
        skill_name = norm(entry.get("name") if entry else skill)
        config = SKILL_SIM_AUTO_DUPLICATE_LIMITS_BY_JOB.get(norm(job))
        if not config:
            return "", 1
        group, limit = config
        return ("duplicate_limit", int(limit)) if skill_name in group else ("", 1)

    def _skill_sim_force_include_candidates(
        self,
        job: str,
        selected: list[tuple[float, int, str, str]],
        candidates: list[tuple[float, int, str, str]],
        *,
        max_rows: int,
    ) -> list[tuple[float, int, str, str]]:
        forced_names = SKILL_SIM_AUTO_FORCE_INCLUDE_BY_JOB.get(norm(job), set())
        if not forced_names or max_rows <= 0:
            return selected[:max_rows]
        selected = selected[:max_rows]
        selected_names = {
            norm((self.store._skill_damage_entry(job, item[2]) or {}).get("name") or item[2])
            for item in selected
        }
        best_by_name: dict[str, tuple[float, int, str, str]] = {}
        for item in candidates:
            entry = self.store._skill_damage_entry(job, item[2])
            skill_name = norm(entry.get("name") if entry else item[2])
            if skill_name not in forced_names:
                continue
            current = best_by_name.get(skill_name)
            if current is None or item[:2] > current[:2]:
                best_by_name[skill_name] = item
        for skill_name in sorted(forced_names):
            if skill_name in selected_names or skill_name not in best_by_name:
                continue
            forced_item = best_by_name[skill_name]
            if len(selected) < max_rows:
                selected.append(forced_item)
            else:
                replace_index = next(
                    (
                        index
                        for index in range(len(selected) - 1, -1, -1)
                        if norm((self.store._skill_damage_entry(job, selected[index][2]) or {}).get("name") or selected[index][2])
                        not in forced_names
                    ),
                    len(selected) - 1,
                )
                selected[replace_index] = forced_item
            selected_names.add(skill_name)
        selected.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return selected[:max_rows]

    def _skill_dps_for_element_weight(
        self,
        job: str,
        skill: str,
        level: Any,
        summary: dict[str, float],
        base_element_percent: float,
        *,
        cooldown: float | None = None,
        multiplier: float | None = None,
        apply_element: bool = True,
    ) -> tuple[float, str]:
        job_name = self._team_real_job(job)
        level_data = self.store.skill_damage_level_data(job_name, skill, level)
        if not level_data:
            return 0.0, "无"
        base_cooldown = self._skill_adjusted_cooldown_for_job(job_name, skill, level_data)
        fixed_cooldown = self._skill_fixed_cooldown_for_job(job_name, skill)
        effective_cooldown = as_number(cooldown) if cooldown is not None else (
            fixed_cooldown
            if fixed_cooldown is not None
            else self._skill_effective_cooldown_for_job(job_name, skill, base_cooldown, 0.0)
        )
        if effective_cooldown <= 0:
            return 0.0, "无"
        profile = self._extreme_profile_for_job(job_name)
        preferred_type = "M" if profile.get("attack_attr") == "魔攻" else "P"
        display_multiplier = self._skill_adjusted_multiplier_for_job(job_name, skill, level) if multiplier is None else max(0.0, as_number(multiplier))
        score = self._skill_level_effective(
            level_data,
            summary,
            multiplier=display_multiplier,
            preferred_type=preferred_type,
            weighted_panel=None,
        ) / effective_cooldown
        element = norm(self.store.skill_damage_element(job_name, skill))
        if element in {"火", "水", "光", "暗"}:
            if apply_element:
                element_attack = max(base_element_percent, as_number(summary.get(f"{element}攻%")))
                score *= 1 + max(0.0, element_attack) / 100
            return max(0.0, score), element
        if element == '不适用':
            return max(0.0, score), element
        score *= self._skill_sim_dps_multiplier("", profile)
        if apply_element:
            score *= self._skill_element_display_multiplier('', summary)
        return max(0.0, score), "无"

    def _selected_skill_element_weights_for_job(
        self,
        job: str,
        summary: dict[str, float],
        base_element_percent: float,
    ) -> dict[str, float]:
        job = self._team_real_job(job)
        if not hasattr(self, "skill_sim_table") or norm(self.second_job.currentText()) != norm(job):
            return {}
        totals = {"光": 0.0, "暗": 0.0, "无": 0.0}
        has_skill = False
        for row in range(SKILL_SIM_ROWS):
            skill = self.combo_text(self.skill_sim_table, row, 0)
            level = self.combo_text(self.skill_sim_table, row, 1)
            if not skill or skill == "无" or not level or level == "无":
                continue
            cooldown = self.double_value(self.skill_sim_table, row, 3)
            multiplier = self.double_value(self.skill_sim_table, row, 4)
            score, element = self._skill_dps_for_element_weight(
                job,
                skill,
                level,
                summary,
                base_element_percent,
                cooldown=cooldown,
                multiplier=multiplier,
            )
            if score <= 0:
                continue
            key = element if element in {"光", "暗", "不适用"} else "无"
            totals[key] = totals.get(key, 0.0) + score
            has_skill = True
        if not has_skill:
            return {}
        total = sum(totals.values())
        return {key: value / total for key, value in totals.items() if total > 0 and value > 1e-9}

    def _selected_skill_rows_for_job(self, job: str) -> list[tuple[str, str, float | None, float | None]]:
        job = self._team_real_job(job)
        if not hasattr(self, "skill_sim_table") or norm(self.second_job.currentText()) != norm(job):
            return []
        rows: list[tuple[str, str, float | None, float | None]] = []
        for row in range(SKILL_SIM_ROWS):
            skill = self.combo_text(self.skill_sim_table, row, 0)
            level = self.combo_text(self.skill_sim_table, row, 1)
            if not skill or skill == "无" or not level or level == "无":
                continue
            rows.append((skill, level, self.double_value(self.skill_sim_table, row, 3), self.double_value(self.skill_sim_table, row, 4)))
        return rows

    def auto_fill_skill_sim(self) -> None:
        if not hasattr(self, "skill_sim_table"):
            return
        _rows, summary = self.calculator.calculate(self.collect_build())
        profile = self._job_profile()
        weighted_skill_panel = self._skill_weighted_panel_profile()
        preferred_type = None if weighted_skill_panel else ("M" if profile.get("attack_attr") == "魔攻" else "P")
        base_skill_names = self._shared_base_skill_names(profile)
        job_name = norm(self.second_job.currentText())
        skip_names = SKILL_SIM_AUTO_SKIP_BY_JOB.get(job_name, set())
        forced_last = SKILL_SIM_AUTO_FORCED_LAST_BY_JOB.get(job_name, "")
        primary_candidates: list[tuple[float, int, str, str]] = []
        fallback_candidates: list[tuple[float, int, str, str]] = []
        for skill in self.store.skill_damage_names(self.second_job.currentText()):
            if skill == "无":
                continue
            skill_entry = self.store._skill_damage_entry(self.second_job.currentText(), skill)
            skill_name = skill_entry.get("name", skill) if skill_entry else skill
            weighted_skill_panel = self._skill_weighted_panel_profile(skill)
            if skill_name in skip_names or skill_name == forced_last:
                continue
            if self._skill_build_level_for_skill_name(skill_name) == 0:
                continue
            best: tuple[float, int, str, str] | None = None
            for level in self._skill_sim_available_levels(skill):
                level_data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, level)
                if not level_data:
                    continue
                required_level = int(as_number(level_data[1]))
                if required_level > 70 and self._score_combat_skill_level(skill) is None:
                    continue
                base_cooldown = self._skill_adjusted_cooldown(skill, level_data)
                fixed_cooldown = self._skill_fixed_cooldown(skill)
                cooldown = fixed_cooldown if fixed_cooldown is not None else self._skill_effective_cooldown(skill, base_cooldown)
                if cooldown <= 0:
                    continue
                multiplier = self._skill_adjusted_multiplier(skill, level)
                score = self._skill_level_effective(
                    level_data,
                    summary,
                    multiplier=multiplier + self._skill_burning_hand_bonus(skill),
                    preferred_type=preferred_type,
                    weighted_panel=weighted_skill_panel,
                ) / cooldown
                element = self.store.skill_damage_element(self.second_job.currentText(), skill)
                score *= self._skill_sim_dps_multiplier(element, profile)
                if score <= 0:
                    continue
                item = (score, required_level, skill, level)
                if best is None or item[:2] > best[:2]:
                    best = item
            if best:
                duplicate_group, duplicate_limit = self._skill_sim_auto_duplicate_limit(skill)
                repeat = duplicate_limit if duplicate_group else 1
                if best[1] >= 15 and skill_name not in base_skill_names:
                    primary_candidates.extend([best] * repeat)
                else:
                    fallback_candidates.extend([best] * repeat)
        candidates = primary_candidates + fallback_candidates
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected_candidates: list[tuple[float, int, str, str]] = []
        used_groups: set[str] = set()
        duplicate_counts: dict[str, int] = {}
        for item in candidates:
            group = self._skill_sim_auto_exclusive_group(item[2])
            if group and group in used_groups:
                continue
            if group:
                used_groups.add(group)
            duplicate_group, duplicate_limit = self._skill_sim_auto_duplicate_limit(item[2])
            if duplicate_group:
                count = duplicate_counts.get(duplicate_group, 0)
                if count >= duplicate_limit:
                    continue
                duplicate_counts[duplicate_group] = count + 1
            selected_candidates.append(item)
            if len(selected_candidates) >= SKILL_SIM_ROWS - (1 if forced_last else 0):
                break
        selected_candidates = self._skill_sim_force_include_candidates(
            job_name,
            selected_candidates,
            candidates,
            max_rows=SKILL_SIM_ROWS - (1 if forced_last else 0),
        )
        if forced_last:
            forced_levels = self._skill_sim_available_levels(forced_last)
            forced_level = forced_levels[-1] if forced_levels else "无"
            forced_data = self.store.skill_damage_level_data(self.second_job.currentText(), forced_last, forced_level)
            forced_required = int(as_number(forced_data[1])) if forced_data else 0
            selected_candidates = selected_candidates[: SKILL_SIM_ROWS - 1]
            while len(selected_candidates) < SKILL_SIM_ROWS - 1:
                selected_candidates.append((0.0, 0, "无", "无"))
            selected_candidates.append((0.0, forced_required, forced_last, forced_level))
        for row in range(SKILL_SIM_ROWS):
            skill = selected_candidates[row][2] if row < len(selected_candidates) else "无"
            level = selected_candidates[row][3] if row < len(selected_candidates) else "无"
            skill_combo = self.skill_sim_table.cellWidget(row, 0)
            level_combo = self.skill_sim_table.cellWidget(row, 1)
            if isinstance(skill_combo, Combo):
                self._set_combo_current(skill_combo, skill)
            if isinstance(level_combo, Combo):
                levels = self._skill_sim_available_levels(skill)
                self._replace_combo_values(level_combo, levels, level)
            level_data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, level)
            cooldown = self.skill_sim_table.cellWidget(row, 3)
            if isinstance(cooldown, QDoubleSpinBox):
                base_cooldown = self._skill_adjusted_cooldown(skill, level_data) if level_data else 0
                fixed_cooldown = self._skill_fixed_cooldown(skill)
                special_cooldown = self._skill_special_effective_cooldown(skill)
                cooldown.blockSignals(True)
                cooldown.setProperty("base_cooldown", base_cooldown)
                cooldown.setValue(
                    fixed_cooldown
                    if fixed_cooldown is not None
                    else special_cooldown
                    if special_cooldown is not None
                    else self._skill_effective_cooldown(skill, base_cooldown)
                )
                cooldown.setReadOnly(fixed_cooldown is not None or special_cooldown is not None)
                cooldown.blockSignals(False)
            multiplier = self.skill_sim_table.cellWidget(row, 4)
            if isinstance(multiplier, QDoubleSpinBox):
                multiplier.blockSignals(True)
                multiplier.setValue(self._skill_adjusted_multiplier(skill, level))
                multiplier.blockSignals(False)
        self.refresh()

    def update_skill_sim_preview(self, summary: dict[str, float]) -> None:
        if not hasattr(self, "skill_sim_table"):
            return
        self._sync_skill_burning_hand_column()
        self._skill_burst_base_summary = dict(summary)
        dps_total = 0.0
        dpa_highest = 0.0
        burst_metrics: list[dict[str, Any]] = []
        weighted_skill_panel = self._skill_weighted_panel_profile()
        profile = self._job_profile()
        for row in range(SKILL_SIM_ROWS):
            skill = self.combo_text(self.skill_sim_table, row, 0)
            weighted_skill_panel = self._skill_weighted_panel_profile(skill)
            self._tint_skill_cell(self.skill_sim_table, row, 0, skill)
            level = self.combo_text(self.skill_sim_table, row, 1)
            burst_build_level = self._skill_build_level_for_burst_sim(skill)
            level_data, effective_level, mastery_bonus = self._skill_sim_level_data_with_mastery(skill, level)
            element = self.store.skill_damage_element(self.second_job.currentText(), skill)
            element_multiplier = self._skill_element_display_multiplier(element, summary)
            dps_multiplier = self._skill_sim_dps_multiplier(element, profile)
            element_suffix = '' if element == '不适用' else f'{element}属性' if element else '无属性'
            if not level_data:
                cooldown = self.skill_sim_table.cellWidget(row, 3)
                if isinstance(cooldown, QDoubleSpinBox):
                    cooldown.blockSignals(True)
                    cooldown.setProperty("base_cooldown", 0)
                    cooldown.setValue(0)
                    cooldown.blockSignals(False)
                for col in (2, 5, 6, 7, 8, SKILL_SIM_COL_CAST_TIME, SKILL_SIM_COL_DPA):
                    self._set_summary_item(self.skill_sim_table, row, col, "")
                if hasattr(self, "skill_dpa_table"):
                    for col in range(self.skill_dpa_table.columnCount()):
                        self._set_summary_item(self.skill_dpa_table, row, col, "")
                for desc_table_name in ("skill_desc_table", "skill_dpa_desc_table"):
                    desc_table = getattr(self, desc_table_name, None)
                    if isinstance(desc_table, QTableWidget):
                        self._set_summary_item(desc_table, row, 1, "")
                continue
            required_level = int(as_number(level_data[1]))
            cooldown = self.double_value(self.skill_sim_table, row, 3)
            multiplier = max(0.0, self.double_value(self.skill_sim_table, row, 4) or 0.0)
            burning_bonus = self._skill_burning_hand_bonus(skill)
            display_multiplier = multiplier + burning_bonus
            simulation_output_factor = 1.0
            if is_ring_strike(skill):
                action_factor = 1.0 + (
                    self._skill_action_speed_percent() + self._skill_glyph_action_speed(skill)
                ) / 100.0
                simulation_output_factor = ring_strike_output_multiplier(action_factor)
                display_multiplier *= simulation_output_factor
            glyph_damage_multiplier = self._skill_glyph_damage_multiplier(skill)
            damages = self._skill_level_damages(level_data)
            channel_preferred_type = "M" if profile.get("attack_attr") == "魔攻" else "P"
            burst_channel_scores = self._skill_level_channel_scores(
                level_data,
                summary,
                multiplier=display_multiplier,
                preferred_type=None if weighted_skill_panel else channel_preferred_type,
                weighted_panel=weighted_skill_panel,
                element=element,
            )
            burst_channel_scores = {
                channel: value * dps_multiplier * glyph_damage_multiplier
                for channel, value in burst_channel_scores.items()
                if value > 0
            }
            value_parts: list[str] = []
            effective_parts: list[str] = []
            dps_parts: list[str] = []
            dpa_parts: list[str] = []
            cast_timing = self._skill_effective_cast_timing(skill)
            cast_time = as_number(cast_timing.get("cast_time"))
            periodic_cycle = self._skill_periodic_total_damage_cycle(skill)
            periodic_dpa_note = self._skill_periodic_total_damage_dpa_note(skill, periodic_cycle)
            burst_raw_score = 0.0
            burst_effective_score = 0.0
            if weighted_skill_panel:
                for damage_type, percent, fixed in damages:
                    scaled_percent = percent * display_multiplier
                    scaled_fixed = fixed * display_multiplier
                    label = self._damage_type_label(damage_type)
                    if element_suffix:
                        label = f"{label}{element_suffix}"
                    value_text = f"{label} {self._raw_percent_text(scaled_percent)}+{int(scaled_fixed):,}"
                    if periodic_cycle > 0:
                        value_text += f"（{periodic_cycle:g}s总计）"
                    value_parts.append(value_text)
                effective = self._skill_level_effective(level_data, summary, multiplier=display_multiplier, weighted_panel=weighted_skill_panel)
                effective *= glyph_damage_multiplier
                label = f"{weighted_skill_panel['label']}{element_suffix}"
                effective_text = f"{label} {self._percent_text(effective)}"
                if element in {'火', '水', '光', '暗'} or abs(element_multiplier - 1.0) > 1e-9:
                    effective_text += f"（属性后 {self._percent_text(effective * element_multiplier)}）"
                if periodic_cycle > 0:
                    effective_text += f"（{periodic_cycle:g}s总计）"
                effective_parts.append(effective_text)
                burst_raw_score += effective * dps_multiplier
                burst_effective_score += effective * dps_multiplier * element_multiplier
                if cooldown > 0:
                    dps = effective / cooldown * dps_multiplier
                    dps_total += dps
                    dps_text = f"{label} {self._percent_text(dps)}/s"
                    if element in {'火', '水', '光', '暗'} or abs(element_multiplier - 1.0) > 1e-9:
                        dps_text += f"（属性后 {self._percent_text(dps * element_multiplier)}/s）"
                    dps_parts.append(dps_text)
                else:
                    dps_parts.append(f"{label} 无冷却")
                if periodic_cycle > 0:
                    dpa_parts.append(periodic_dpa_note)
                elif cast_time > 0:
                    dpa = effective / cast_time * dps_multiplier
                    dpa_text = f"{label} {self._percent_text(dpa)}/s"
                    if element in {'火', '水', '光', '暗'} or abs(element_multiplier - 1.0) > 1e-9:
                        dpa_text += f"（属性后 {self._percent_text(dpa * element_multiplier)}/s）"
                    dpa_parts.append(dpa_text)
                else:
                    dpa_parts.append(f"{label} 无施法时间")
            for damage_type, percent, fixed in ([] if weighted_skill_panel else damages):
                scaled_percent = percent * display_multiplier
                scaled_fixed = fixed * display_multiplier
                label = self._damage_type_label(damage_type)
                if element_suffix:
                    label = f"{label}{element_suffix}"
                value_text = f"{label} {self._raw_percent_text(scaled_percent)}+{int(scaled_fixed):,}"
                if periodic_cycle > 0:
                    value_text += f"（{periodic_cycle:g}s总计）"
                value_parts.append(value_text)
                panel = self._damage_panel_value(damage_type, summary)
                if panel > 0:
                    effective = (scaled_percent + scaled_fixed / panel * 100) * glyph_damage_multiplier
                    effective_text = f"{label} {self._percent_text(effective)}"
                    if element in {'火', '水', '光', '暗'} or abs(element_multiplier - 1.0) > 1e-9:
                        effective_text += f"（属性后 {self._percent_text(effective * element_multiplier)}）"
                    if periodic_cycle > 0:
                        effective_text += f"（{periodic_cycle:g}s总计）"
                    effective_parts.append(effective_text)
                    burst_raw_score += effective * dps_multiplier
                    burst_effective_score += effective * dps_multiplier * element_multiplier
                    if cooldown > 0:
                        dps = effective / cooldown * dps_multiplier
                        dps_total += dps
                        dps_text = f"{label} {self._percent_text(dps)}/s"
                        if element in {'火', '水', '光', '暗'} or abs(element_multiplier - 1.0) > 1e-9:
                            dps_text += f"（属性后 {self._percent_text(dps * element_multiplier)}/s）"
                        dps_parts.append(dps_text)
                    else:
                        dps_parts.append(f"{label} 无冷却")
                    if periodic_cycle > 0:
                        dpa_parts.append(periodic_dpa_note)
                    elif cast_time > 0:
                        dpa = effective / cast_time * dps_multiplier
                        dpa_text = f"{label} {self._percent_text(dpa)}/s"
                        if element in {'火', '水', '光', '暗'} or abs(element_multiplier - 1.0) > 1e-9:
                            dpa_text += f"（属性后 {self._percent_text(dpa * element_multiplier)}/s）"
                        dpa_parts.append(dpa_text)
                    else:
                        dpa_parts.append(f"{label} 无施法时间")
                else:
                    effective_parts.append(f"{label} 无法计算")
                    dps_parts.append(f"{label} 无法计算")
                    dpa_parts.append(f"{label} 无法计算")
            required_text = str(required_level)
            if mastery_bonus:
                required_text += f" / 专精+{mastery_bonus}=>Lv{effective_level}"
            self._set_summary_item(self.skill_sim_table, row, 2, required_text, align_right=True)
            burn_text = self._raw_percent_text(burning_bonus * 100) if burning_bonus > 0 else ""
            self._set_summary_item(self.skill_sim_table, row, 5, burn_text, align_right=True)
            self._set_summary_item(self.skill_sim_table, row, 6, "；".join(value_parts))
            self._set_summary_item(self.skill_sim_table, row, 7, "；".join(effective_parts))
            self._set_summary_item(self.skill_sim_table, row, 8, "；".join(dps_parts))
            self._set_summary_item(self.skill_sim_table, row, SKILL_SIM_COL_CAST_TIME, self._skill_cast_time_text(skill), align_right=True)
            self._set_summary_item(self.skill_sim_table, row, SKILL_SIM_COL_DPA, "；".join(dpa_parts))
            if hasattr(self, "skill_dpa_table"):
                self._set_summary_item(self.skill_dpa_table, row, 0, skill)
                self._tint_skill_cell(self.skill_dpa_table, row, 0, skill)
                self._set_summary_item(self.skill_dpa_table, row, 1, level, align_right=True)
                self._set_summary_item(self.skill_dpa_table, row, 2, self._skill_cast_time_text(skill), align_right=True)
                self._set_summary_item(self.skill_dpa_table, row, 3, "；".join(value_parts))
                self._set_summary_item(self.skill_dpa_table, row, 4, "；".join(effective_parts))
                self._set_summary_item(self.skill_dpa_table, row, 5, "；".join(dpa_parts))
            if periodic_cycle <= 0 and cast_time > 0 and burst_effective_score > 0 and (burst_build_level is None or burst_build_level > 0):
                dpa_highest = max(dpa_highest, burst_effective_score / cast_time)
                burst_metric = {
                    "row": row,
                    "skill": skill,
                    "level": level,
                    "base_cooldown": self._skill_burst_original_cooldown(skill, level_data, self._skill_base_cooldown(row)),
                    # The table settles speed for display; the timeline must
                    # retain the reference to avoid counting the bonus twice.
                    "raw_score": burst_raw_score / simulation_output_factor,
                    "effective_score": burst_effective_score / simulation_output_factor,
                    "element": norm(element),
                    "cast_time": cast_time,
                    "channel_scores": {key: value / simulation_output_factor for key, value in burst_channel_scores.items()},
                    "skill_glyph_damage_applied": True,
                    "base_multiplier": multiplier,
                    "burning_hand_bonus_applied": burning_bonus,
                }
                if norm(self.second_job.currentText()) in {"圣骑士", "十字军"}:
                    timeline_scores = self._skill_timeline_attack_scores(
                        level_data,
                        summary,
                        multiplier=display_multiplier * dps_multiplier * glyph_damage_multiplier,
                        preferred_type=channel_preferred_type,
                    )
                    burst_metric["timeline_attack_scores"] = timeline_scores
                    burst_metric["timeline_raw_score"] = sum(timeline_scores.values())
                burst_metrics.append(burst_metric)
            desc = self.store.skill_damage_description(self.second_job.currentText(), skill, effective_level if mastery_bonus else level)
            desc_one_line = "；".join(line for line in desc.splitlines() if line.strip())
            if mastery_bonus:
                desc_one_line = f"专精+{mastery_bonus}，按Lv{effective_level}计算；{desc_one_line}"
            if is_ring_strike(skill):
                count = ring_strike_default_hit_count(action_factor)
                desc_one_line += (
                    f"；默认参考{ring_strike_default_hit_count()}Hit，本次{count}Hit"
                    f"，结算{display_multiplier:g}倍；不是实际地形命中上限"
                )
            for desc_table_name in ("skill_desc_table", "skill_dpa_desc_table"):
                desc_table = getattr(self, desc_table_name, None)
                if isinstance(desc_table, QTableWidget):
                    self._set_summary_item(desc_table, row, 1, desc_one_line)
        expected_panel = self._skill_weighted_panel_value(summary, self._skill_weighted_panel_profile()) if self._skill_weighted_panel_profile() else as_number(summary.get("effective_panel"))
        expected_dps = expected_panel * dps_total / 100
        total_text = f"{self._percent_text(dps_total)}/s；期望DPS {number_text(expected_dps, integer=True)}/s"
        self._set_summary_item(self.skill_sim_table, SKILL_SIM_ROWS, 6, total_text, align_right=True)
        if hasattr(self, "skill_dpa_table"):
            self._set_summary_item(self.skill_dpa_table, SKILL_SIM_ROWS, 3, f"最高DPA {self._percent_text(dpa_highest)}/s；DPA用于爆发排序，不直接求和", align_right=True)
        self._skill_burst_base_summary = dict(summary)
        if getattr(self, "_skill_sim_ui_active", True):
            self._update_skill_burst_sim_preview(burst_metrics)
            self._update_skill_long_sim_preview(burst_metrics)
        if getattr(self, "_team_sim_ui_active", True):
            self._update_team_practical_sim_preview(burst_metrics)









    @staticmethod
    def _skill_periodic_total_damage_cycle(skill: str) -> float:
        return max(
            0.0,
            as_number(SKILL_PERIODIC_TOTAL_DAMAGE_CYCLES.get(BuildEditor._skill_burst_skill_key(skill))),
        )

    @staticmethod
    def _skill_periodic_total_damage_dpa_note(skill: str, cycle: float) -> str:
        skill_key = BuildEditor._skill_burst_skill_key(skill)
        if (
            skill_key in SKILL_SUMMON_MIN_COOLDOWNS
            or skill_key in SKILL_BURST_SIM_PASSIVE_SUMMONS
        ):
            return f"常驻召唤，按{cycle:g}s总伤害折算，不参与DPA排序"
        return f"周期伤害，按{cycle:g}s总伤害折算，不参与DPA排序"






    def _skill_build_entry_for_skill_name(self, skill: str) -> dict[str, Any]:
        names = self._skill_build_match_names_for_skill(skill)
        tree = getattr(self, "skill_build_data", None)
        if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
            tree = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        cache_key = (id(tree), norm(self.second_job.currentText()))
        cached = getattr(self, "_skill_build_entry_lookup_cache", None)
        if not isinstance(cached, tuple) or len(cached) != 2 or cached[0] != cache_key:
            lookup: dict[str, dict[str, Any]] = {}
            for skill_id, skill_item in tree.get("skills", {}).items():
                lookup[norm(skill_id)] = skill_item
                skill_name = norm(skill_item.get("name"))
                if skill_name:
                    lookup[skill_name] = skill_item
            cached = (cache_key, lookup)
            self._skill_build_entry_lookup_cache = cached
        lookup = cached[1]
        for name in names:
            if name in lookup:
                return lookup[name]
        return {}














































    def _skill_stance_action_rule(self, skill: str) -> dict[str, Any]:
        rules = SKILL_STANCE_ACTION_RULES_BY_JOB.get(norm(self.second_job.currentText()), {})
        rule = rules.get(self._skill_burst_skill_key(skill), {}) if isinstance(rules, dict) else {}
        return rule if isinstance(rule, dict) else {}



    def _skill_stance_action_count(
        self,
        skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> int:
        rule = self._skill_stance_action_rule(skill)
        base_count = max(0, int(as_number(rule.get("base_count"))))
        duration = max(0.0, as_number(rule.get("duration")))
        if base_count <= 0 or duration <= 0:
            return base_count
        minimum_speed = 0.0
        if (
            norm(self.second_job.currentText()) == "狙翎"
            and self._skill_burst_skill_key(skill) == "包围"
            and display_value(active_attrs or {}, "__爆发buff__精确瞄准") > 0
        ):
            minimum_speed = 30.0
        output_start = max(0.0, as_number(seconds)) + max(
            0.0, as_number(rule.get("startup_time"))
        )
        action_units = self._skill_burst_action_units(
            output_start,
            duration,
            active_attrs,
            minimum_speed,
            skill,
        )
        if norm(self.second_job.currentText())=='月之领主' and self._skill_burst_skill_key(skill)=='猛砍站姿':
            return max(8,int(8+5*(action_units/duration-1)+1e-9))
        return max(base_count, int(base_count * action_units / duration + 1e-9))

    def _skill_stance_action_multiplier(
        self,
        skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        rule = self._skill_stance_action_rule(skill)
        count = self._skill_stance_action_count(skill, seconds, active_attrs)
        return (
            count * max(0.0, as_number(rule.get("per_hit_multiplier"), 1.0))
            + max(0.0, as_number(rule.get("fixed_multiplier")))
        )

    def _skill_dark_suffer_projectile_count(
        self,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> int:
        # The 0.4s startup is fixed. Action speed only changes how many complete
        # sword waves fit in the following four-second output window.
        storage_start = max(0.0, as_number(seconds)) + SKILL_DARK_SUFFER_STARTUP_TIME
        accelerated_time = self._skill_burst_action_units(
            storage_start,
            SKILL_DARK_SUFFER_PROJECTILE_LIFETIME,
            active_attrs,
            skill="黑暗冲浪",
        )
        return max(1, int(accelerated_time / SKILL_DARK_SUFFER_SHOT_INTERVAL + 1e-9))

    def _skill_dark_suffer_multiplier(
        self,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        return (
            self._skill_dark_suffer_projectile_count(seconds, active_attrs)
            * SKILL_DARK_SUFFER_EXPLOSION_MULTIPLIER
        )























































































































    @staticmethod
    def _skill_level_damages(level_data: list[Any]) -> list[tuple[str, float, float]]:
        if len(level_data) >= 6:
            return [(str(level_data[3]), as_number(level_data[4]), as_number(level_data[5]))]
        if len(level_data) >= 4 and isinstance(level_data[3], list):
            return [(str(item[0]), as_number(item[1]), as_number(item[2])) for item in level_data[3] if len(item) >= 3]
        if len(level_data) >= 5:
            return [(str(level_data[2]), as_number(level_data[3]), as_number(level_data[4]))]
        if len(level_data) >= 3 and isinstance(level_data[2], list):
            return [(str(item[0]), as_number(item[1]), as_number(item[2])) for item in level_data[2] if len(item) >= 3]
        return []

    @staticmethod
    def _skill_level_cooldown(level_data: list[Any]) -> float:
        if len(level_data) >= 6 or (len(level_data) >= 4 and isinstance(level_data[3], list)):
            return as_number(level_data[2])
        return 0.0

    @staticmethod
    def _damage_type_label(damage_type: str) -> str:
        return {"P": "物理", "M": "魔法", "B": "物理/魔法", "U": "攻击力"}.get(damage_type, damage_type)

    @staticmethod
    def _damage_panel_value(damage_type: str, summary: dict[str, float]) -> float:
        physical = as_number(summary.get("physical_average"))
        magic = as_number(summary.get("magic_average"))
        if damage_type == "P":
            return physical
        if damage_type == "M":
            return magic
        if damage_type == "B":
            return (physical + magic) / 2
        return max(physical, magic)

    def _skill_weighted_panel_profile(self, skill: str = "") -> dict[str, Any] | None:
        job = norm(self.second_job.currentText())
        channel_profile = self.store.skill_damage_panel_profile(job, skill) if skill else None
        if channel_profile:
            return channel_profile
        if job == "十字军":
            return {"label": "物理+魔法", "mode": "individual_sum", "denominator": "average", "weights": {"P": 1.0, "M": 1.0, "B": 1.0, "U": 1.0}}
        if job == "圣骑士":
            return {"label": "物理+半魔", "mode": "individual_sum", "denominator": "physical", "weights": {"P": 1.0, "M": 0.5, "B": 1.0, "U": 1.0}}
        return None

    @staticmethod
    def _skill_weighted_panel_value(summary: dict[str, float], profile: dict[str, Any] | None) -> float:
        if not profile:
            return 0.0
        physical = as_number(summary.get("physical_average"))
        magic = as_number(summary.get("magic_average"))
        denominator = norm(profile.get("denominator"))
        if denominator == "physical":
            return max(0.0, physical)
        if denominator == "magic":
            return max(0.0, magic)
        if denominator == "average":
            return max(0.0, (physical + magic) / 2)
        return max(0.0, physical + magic)

    @classmethod
    def _skill_timeline_attack_scores(
        cls,
        level_data: list[Any],
        summary: dict[str, float],
        *,
        multiplier: float = 1.0,
        preferred_type: str | None = None,
    ) -> dict[str, float]:
        """Convert physical and magic damage to one real-damage timeline scale."""
        physical = max(0.0, as_number(summary.get("physical_average")))
        magic = max(0.0, as_number(summary.get("magic_average")))
        denominator = physical or magic
        if denominator <= 0:
            return {}

        scores: dict[str, float] = {}
        for damage_type, percent, fixed in cls._skill_level_damages(level_data):
            if damage_type == "P":
                panel, channel = physical, "物理"
            elif damage_type == "M":
                panel, channel = magic, "魔法"
            elif damage_type == "B":
                panel = (physical + magic) / 2
                channel = "魔法" if preferred_type == "M" else "物理"
            else:
                channel = "魔法" if preferred_type == "M" else "物理"
                panel = magic if channel == "魔法" else physical
            if panel <= 0:
                continue
            actual_damage = (panel * percent / 100 + fixed) * multiplier
            scores[channel] = scores.get(channel, 0.0) + actual_damage / denominator * 100
        return {channel: score for channel, score in scores.items() if score > 0}

    @classmethod
    def _skill_level_channel_scores(
        cls,
        level_data: list[Any],
        summary: dict[str, float],
        *,
        multiplier: float = 1.0,
        preferred_type: str | None = None,
        weighted_panel: dict[str, Any] | None = None,
        element: str = "",
    ) -> dict[str, float]:
        element_name = norm(element)
        element_channel = element_name if element_name in {"火", "水", "光", "暗"} else ""
        scores: dict[str, float] = {}

        def add_score(damage_type: str, value: float) -> None:
            if value <= 0:
                return
            if element_channel:
                scores[element_channel] = scores.get(element_channel, 0.0) + value
            elif damage_type == "P":
                scores["物理"] = scores.get("物理", 0.0) + value
            elif damage_type == "M":
                scores["魔法"] = scores.get("魔法", 0.0) + value
            elif damage_type == "B":
                scores["物理"] = scores.get("物理", 0.0) + value / 2
                scores["魔法"] = scores.get("魔法", 0.0) + value / 2
            else:
                channel = "魔法" if preferred_type == "M" else "物理"
                scores[channel] = scores.get(channel, 0.0) + value

        damages = cls._skill_level_damages(level_data)
        if weighted_panel:
            weights = weighted_panel.get("weights", {})
            if norm(weighted_panel.get("mode")) == "individual_sum":
                for damage_type, percent, fixed in damages:
                    panel = cls._damage_panel_value(damage_type, summary)
                    weight = as_number(weights.get(damage_type), 1.0)
                    if panel <= 0 or weight <= 0:
                        continue
                    add_score(
                        damage_type,
                        (percent * multiplier + fixed * multiplier / panel * 100) * weight,
                    )
                return scores
            total_panel = cls._skill_weighted_panel_value(summary, weighted_panel)
            if total_panel <= 0:
                return scores
            for damage_type, percent, fixed in damages:
                panel = cls._damage_panel_value(damage_type, summary)
                weight = as_number(weights.get(damage_type), 1.0)
                if panel <= 0 or weight <= 0:
                    continue
                add_score(
                    damage_type,
                    (panel * percent / 100 + fixed) * multiplier * weight / total_panel * 100,
                )
            return scores

        for damage_type, percent, fixed in damages:
            if preferred_type and damage_type not in {preferred_type, "B", "U"}:
                continue
            panel = cls._damage_panel_value(damage_type, summary)
            if panel <= 0:
                continue
            add_score(damage_type, percent * multiplier + fixed * multiplier / panel * 100)
        return scores

    @staticmethod
    def _skill_element_display_multiplier(element: str, summary: dict[str, float]) -> float:
        element_text = norm(element)
        if element_text == '不适用':
            return 1.0
        if element_text not in {"火", "水", "光", "暗"}:
            return max(0.0, 1 + as_number(summary.get('无属性%')) / 100)
        attack_percent = as_number(summary.get(f"{element_text}攻%"))
        resist_percent = as_number(summary.get(f"{element_text}有效抗%", summary.get(f"{element_text}抗%")))
        return max(0.0, (1 + attack_percent / 100) * (1 - resist_percent / 100))

    @staticmethod
    def _preferred_damage_type(summary: dict[str, float]) -> str:
        physical = as_number(summary.get("physical_average"))
        magic = as_number(summary.get("magic_average"))
        return "P" if physical >= magic else "M"

    @classmethod
    def _skill_level_effective(
        cls,
        level_data: list[Any],
        summary: dict[str, float],
        multiplier: float = 1.0,
        preferred_type: str | None = None,
        weighted_panel: dict[str, Any] | None = None,
    ) -> float:
        if weighted_panel:
            weights = weighted_panel.get("weights", {})
            if norm(weighted_panel.get("mode")) == "individual_sum":
                total = 0.0
                for damage_type, percent, fixed in cls._skill_level_damages(level_data):
                    panel = cls._damage_panel_value(damage_type, summary)
                    if panel <= 0:
                        continue
                    weight = as_number(weights.get(damage_type), 1.0)
                    if weight <= 0:
                        continue
                    total += (percent * multiplier + fixed * multiplier / panel * 100) * weight
                return total
            total_panel = cls._skill_weighted_panel_value(summary, weighted_panel)
            if total_panel <= 0:
                return 0.0
            damage_total = 0.0
            for damage_type, percent, fixed in cls._skill_level_damages(level_data):
                panel = cls._damage_panel_value(damage_type, summary)
                if panel <= 0:
                    continue
                weight = as_number(weights.get(damage_type), 1.0)
                if weight <= 0:
                    continue
                damage_total += (panel * percent / 100 + fixed) * multiplier * weight
            return damage_total / total_panel * 100
        total = 0.0
        for damage_type, percent, fixed in cls._skill_level_damages(level_data):
            if preferred_type and damage_type not in {preferred_type, "B", "U"}:
                continue
            panel = cls._damage_panel_value(damage_type, summary)
            if panel <= 0:
                continue
            total += percent * multiplier + fixed * multiplier / panel * 100
        return total

    @classmethod
    def _skill_level_dps(
        cls,
        level_data: list[Any],
        summary: dict[str, float],
        multiplier: float = 1.0,
        cooldown_override: float | None = None,
        preferred_type: str | None = None,
        weighted_panel: dict[str, Any] | None = None,
    ) -> float:
        cooldown = cls._skill_level_cooldown(level_data) if cooldown_override is None else cooldown_override
        if cooldown <= 0:
            return 0.0
        return cls._skill_level_effective(
            level_data,
            summary,
            multiplier=multiplier,
            preferred_type=preferred_type,
            weighted_panel=weighted_panel,
        ) / cooldown


def bind_build_editor(editor_type) -> None:
    global BuildEditor
    BuildEditor = editor_type
    _engine_module.bind_runtime_editor(editor_type)
    _support_module.BuildEditor = editor_type
    _ui_module.bind_build_editor(editor_type)
