from __future__ import annotations

from ..shared import *
import os

_EXACT_ROUND3 = os.environ.get('DN_SCORE_EXACT_ROUND3', '1') == '1'

BuildEditor = None


class TeamEffectsMixin:
    def _team_sim_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.team_sim_tabs = QTabWidget()
        self.team_setup_page = self._team_setup_page()
        self.team_practical_page = self._team_practical_sim_page()
        self.team_extreme_result_page = QWidget()
        extreme_result_layout = QVBoxLayout(self.team_extreme_result_page)
        extreme_result_layout.setContentsMargins(8, 8, 8, 8)
        extreme_result_hint = QLabel("完成一键极限优化配队后，结果和八人时间轴会显示在这里。")
        extreme_result_hint.setObjectName("inlineHint")
        extreme_result_hint.setAlignment(Qt.AlignCenter)
        extreme_result_layout.addStretch(1)
        extreme_result_layout.addWidget(extreme_result_hint)
        extreme_result_layout.addStretch(1)
        self.team_sim_tabs.addTab(self.team_setup_page, "配队设置与收益")
        self.team_sim_tabs.addTab(self.team_practical_page, "个人配队实战模拟")
        self.team_sim_tabs.addTab(self.team_extreme_result_page, "极限优化配队结果")
        self.team_sim_tabs.currentChanged.connect(lambda _index: self.schedule_refresh())
        layout.addWidget(self.team_sim_tabs)
        return page

    def _team_setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        defaults = self.build.get("team_sim", {}) if isinstance(self.build.get("team_sim", {}), dict) else {}
        member_defaults = defaults.get("members", []) if isinstance(defaults.get("members", []), list) else []
        effect_defaults = defaults.get("effects", []) if isinstance(defaults.get("effects", []), list) else []
        cdr_usage_defaults = self._team_cdr_usage_defaults(defaults)
        cdr_glyph_defaults = defaults.get("cdr_glyphs", {}) if isinstance(defaults.get("cdr_glyphs", {}), dict) else {}
        cdr_apply_defaults = defaults.get("cdr_applies", {}) if isinstance(defaults.get("cdr_applies", {}), dict) else {}
        self.team_extreme_coefficients = {
            norm(job): as_number(value)
            for job, value in (defaults.get("extreme_coefficients", {}) if isinstance(defaults.get("extreme_coefficients", {}), dict) else {}).items()
        }
        self.team_extreme_hand_multipliers = {
            norm(job): as_number(value, 1.0)
            for job, value in (defaults.get("extreme_hand_multipliers", {}) if isinstance(defaults.get("extreme_hand_multipliers", {}), dict) else {}).items()
        }
        self.team_extreme_mode = norm(defaults.get("extreme_team_mode")) or "不限"
        self.team_extreme_boss = norm(defaults.get("extreme_boss")) or "无"
        self.team_extreme_replacement_mode = norm(defaults.get("extreme_replacement_mode")) or "快速"
        self.team_extreme_replacement_candidates = max(0, int(as_number(defaults.get("extreme_replacement_candidates"), 2)))
        self.team_extreme_fixed_jobs = [
            norm(job)
            for job in (defaults.get("extreme_fixed_jobs", []) if isinstance(defaults.get("extreme_fixed_jobs", []), list) else [])
            if norm(job)
        ]
        self.team_extreme_fixed_explicit = bool(defaults.get("extreme_fixed_explicit", False))

        header = QHBoxLayout()
        header.addWidget(QLabel("配队模拟"))
        header.addWidget(QLabel("冰冻系数"))
        self.team_freeze_coeff = QDoubleSpinBox()
        self.team_freeze_coeff.setRange(0, 2)
        self.team_freeze_coeff.setDecimals(2)
        self.team_freeze_coeff.setSingleStep(0.05)
        self.team_freeze_coeff.setFixedHeight(EDIT_ROW_HEIGHT)
        self.team_freeze_coeff.setValue(float(max(0.0, min(2.0, as_number(defaults.get("freeze_coefficient"), DEFAULT_FREEZE_COEFFICIENT)))))
        self.team_freeze_coeff.valueChanged.connect(self.schedule_refresh)
        header.addWidget(self.team_freeze_coeff)
        aura_defaults = defaults.get("aura_choices", {}) if isinstance(defaults.get("aura_choices", {}), dict) else {}
        self.team_aura_choices: dict[str, Combo] = {}
        for aura_job, label_text in (("圣骑士", "圣骑光环"), ("十字军", "十字光环")):
            header.addWidget(QLabel(label_text))
            aura_combo = Combo(
                TEAM_AURA_OPTIONS,
                norm(aura_defaults.get(aura_job)) or TEAM_AURA_OPTIONS[0],
                lambda _text: self._team_aura_changed(),
                min_width=TEAM_AURA_COMBO_WIDTH,
                contents_length=0,
            )
            aura_combo.setFixedWidth(TEAM_AURA_COMBO_WIDTH)
            aura_combo.view().setFixedWidth(TEAM_AURA_COMBO_WIDTH)
            self.team_aura_choices[aura_job] = aura_combo
            header.addWidget(aura_combo)
        auto_team_button = app_button("一键配队", primary=True)
        auto_team_button.clicked.connect(self.auto_best_team)
        extreme_team_button = app_button("一键极限优化配队", primary=True)
        extreme_team_button.clicked.connect(self.auto_extreme_team)
        refresh_button = app_button("一键清空", primary=True)
        refresh_button.clicked.connect(self.clear_team_sim)
        header.addStretch(1)
        header.addWidget(auto_team_button)
        header.addWidget(extreme_team_button)
        header.addWidget(refresh_button)
        layout.addLayout(header)
        team_hint = QLabel("一键配队：以当前配装角色自身收益最大为目标。  一键极限优化配队：按转换装备与默认加点生成八人长时间实战轴，以全队DPS总和最高为目标；配装1职业默认固定但可取消，支持可选T/奶、固定队友和队伍类型偏好，打坐曜与输出曜可同时存在。")
        team_hint.setObjectName("inlineHint")
        team_hint.setWordWrap(True)
        layout.addWidget(team_hint)

        self.team_extreme_history_status_label = QLabel(
            norm(getattr(self, "_extreme_history_status_text", ""))
        )
        self.team_extreme_history_status_label.setObjectName("extremeHistoryStatus")
        self.team_extreme_history_status_label.setWordWrap(True)
        self.team_extreme_history_status_label.setVisible(
            bool(self.team_extreme_history_status_label.text())
        )
        layout.addWidget(self.team_extreme_history_status_label)
        if self.team_extreme_history_status_label.text():
            self._set_extreme_history_status(
                self.team_extreme_history_status_label.text(),
                "info",
            )

        top = QHBoxLayout()
        top.setSpacing(10)

        member_box = QGroupBox("队伍职业")
        member_layout = QVBoxLayout(member_box)
        self.team_member_table = AppTableWidget(TEAM_MEMBER_ROWS, 2)
        self.team_member_table.setHorizontalHeaderLabels(["位置", "职业"])
        self.team_member_table.verticalHeader().setVisible(False)
        job_options = self._team_job_options()
        for row in range(TEAM_MEMBER_ROWS):
            default_job = ""
            if row < len(member_defaults) and isinstance(member_defaults[row], dict):
                default_job = norm(member_defaults[row].get("job"))
            elif row == 0:
                default_job = self.second_job.currentText()
            if norm(default_job) == "曜":
                default_job = "打坐曜"
            self.team_member_table.setItem(row, 0, self._item(f"队员{row + 1}"))
            self.team_member_table.setCellWidget(
                row,
                1,
                Combo(job_options, default_job or "无", lambda _text, r=row: self._team_member_changed(r), min_width=118, contents_length=0),
            )
        self._configure_table(self.team_member_table, [64, 130])
        member_layout.addWidget(self.team_member_table)
        top.addWidget(member_box, 0)

        effect_box = QGroupBox("团队buff/减抗/增伤")
        effect_layout = QVBoxLayout(effect_box)
        effect_table_row = QHBoxLayout()
        effect_table_row.setContentsMargins(0, 0, 0, 0)
        effect_table_row.setSpacing(6)
        effect_action_col = QVBoxLayout()
        effect_action_col.setContentsMargins(0, 0, 0, 0)
        effect_action_col.setSpacing(6)
        for text, handler in [
            ("全启用", lambda _checked=False: self._set_all_team_effects_enabled(True)),
            ("全停用", lambda _checked=False: self._set_all_team_effects_enabled(False)),
            ("按贡献", self._sort_team_effect_table_by_contribution_action),
            ("按类型", self._sort_team_effect_table_by_type_action),
        ]:
            button = app_button(text)
            button.setObjectName("miniActionButton")
            button.setFixedHeight(30)
            button.setMinimumWidth(66)
            button.clicked.connect(handler)
            effect_action_col.addWidget(button)
        effect_action_col.addStretch(1)
        self.team_effect_table = AppTableWidget(TEAM_EFFECT_ROWS, 9)
        self.team_effect_table.setHorizontalHeaderLabels(["启用", "来源", "技能/减抗/增伤", "等级", "技能CD(s)", "Buff时间(s)", "具体加成", "贡献", "移除损失"])
        self.team_effect_table.verticalHeader().setVisible(False)
        provider_options = self._team_provider_options()
        for row in range(TEAM_EFFECT_ROWS):
            item = effect_defaults[row] if row < len(effect_defaults) and isinstance(effect_defaults[row], dict) else {}
            enabled = QTableWidgetItem("")
            enabled.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            enabled.setCheckState(Qt.Checked if item.get("enabled", True) and norm(item.get("skill")) not in {"", "无"} else Qt.Unchecked)
            self.team_effect_table.setItem(row, 0, enabled)
            provider = item.get("provider") or (provider_options[row + 1] if row < TEAM_MEMBER_ROWS and len(provider_options) > row + 1 else "通用")
            self.team_effect_table.setCellWidget(
                row,
                1,
                Combo(provider_options, provider, lambda _text, r=row: self._team_effect_provider_changed(r), min_width=122, contents_length=0),
            )
            effects = self._team_effect_options(provider)
            skill = item.get("skill") or "无"
            self.team_effect_table.setCellWidget(
                row,
                2,
                Combo(effects, skill, lambda _text, r=row: self._team_effect_skill_changed(r), min_width=176, contents_length=0),
            )
            levels = self._team_effect_levels(skill, provider)
            self.team_effect_table.setCellWidget(
                row,
                3,
                Combo(levels, item.get("level") or self._team_effect_default_level(skill, provider, levels), lambda _text, r=row: self._team_effect_level_changed(r), min_width=70, align=Qt.AlignCenter, contents_length=0),
            )
            timing = self._team_effect_timing(provider, skill, item.get("level"))
            base_cooldown = as_number(item.get("cooldown", timing.get("cooldown", 0)))
            cooldown = self._team_cooldown_spin(row, base_cooldown)
            duration = self._team_timing_spin(item.get("duration", timing.get("duration", 0)))
            self.team_effect_table.setCellWidget(row, 4, cooldown)
            self.team_effect_table.setCellWidget(row, 5, duration)
            self.team_effect_table.setItem(row, 6, QTableWidgetItem(""))
            self.team_effect_table.setItem(row, 7, QTableWidgetItem(""))
            self.team_effect_table.setItem(row, 8, QTableWidgetItem(""))
        self.team_effect_table.itemChanged.connect(self._team_effect_item_changed)
        self._configure_table(self.team_effect_table, [46, 126, 184, 74, 78, 88, 168, 116, 126])
        self.team_effect_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        for row in range(TEAM_EFFECT_ROWS):
            self.team_effect_table.setRowHeight(row, EDIT_ROW_HEIGHT)
        effect_table_row.addWidget(self.team_effect_table, 1)
        effect_table_row.addLayout(effect_action_col)
        effect_layout.addLayout(effect_table_row)
        self.team_cdr_usage_box = QGroupBox("-CD技能使用比例")
        cdr_usage_layout = QHBoxLayout(self.team_cdr_usage_box)
        cdr_usage_layout.setContentsMargins(10, 6, 10, 6)
        self.team_cdr_usage_widgets: dict[str, QWidget] = {}
        self.team_cdr_usage_spins: dict[str, QDoubleSpinBox] = {}
        self.team_cdr_glyph_checks: dict[str, QCheckBox] = {}
        self.team_cdr_apply_checks: dict[str, QCheckBox] = {}
        for skill_name in TEAM_CDR_USAGE_SKILLS:
            wrapper = QWidget()
            row_layout = QHBoxLayout(wrapper)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(QLabel(skill_name))
            spin = self._team_usage_spin(cdr_usage_defaults.get(skill_name, self._team_effect_usage_default(skill_name)))
            row_layout.addWidget(spin)
            cdr_usage_layout.addWidget(wrapper)
            self.team_cdr_usage_widgets[skill_name] = wrapper
            self.team_cdr_usage_spins[skill_name] = spin
        glyph_sep = QLabel("纹章")
        cdr_usage_layout.addWidget(glyph_sep)
        self.team_cdr_glyph_label = glyph_sep
        for skill_name, reduction in TEAM_CDR_GLYPH_REDUCTION.items():
            check = QCheckBox(f"{skill_name}-{reduction:g}%CD")
            check.setChecked(bool(cdr_glyph_defaults.get(skill_name, True)))
            check.stateChanged.connect(self.schedule_refresh)
            cdr_usage_layout.addWidget(check)
            self.team_cdr_glyph_checks[skill_name] = check
        apply_sep = QLabel("应用到技能CD")
        cdr_usage_layout.addWidget(apply_sep)
        self.team_cdr_apply_label = apply_sep
        for skill_name in TEAM_CDR_APPLY_SKILLS:
            check = QCheckBox(skill_name)
            check.setChecked(bool(cdr_apply_defaults.get(skill_name, False)))
            check.stateChanged.connect(self.schedule_refresh)
            cdr_usage_layout.addWidget(check)
            self.team_cdr_apply_checks[skill_name] = check
        cdr_usage_layout.addStretch(1)
        effect_layout.addWidget(self.team_cdr_usage_box)
        self._refresh_team_cdr_usage_visibility()
        top.addWidget(effect_box, 1)
        layout.addLayout(top, 1)
        if not effect_defaults:
            self._auto_populate_team_effects_from_members(schedule=False)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        summary_box = QGroupBox("队伍收益")
        summary_box.setMaximumWidth(320)
        summary_layout = QVBoxLayout(summary_box)
        self.team_summary_table = AppTableWidget(14, 2)
        self.team_summary_table.setHorizontalHeaderLabels(["项目", "数据"])
        self.team_summary_table.verticalHeader().setVisible(False)
        self._configure_table(self.team_summary_table, [98, 120])
        self.team_summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.team_summary_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.team_summary_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        summary_layout.addWidget(self.team_summary_table)
        bottom.addWidget(summary_box, 0)

        contribution_box = QGroupBox("职业 / 效果贡献")
        contribution_layout = QVBoxLayout(contribution_box)
        self.team_contribution_table = AppTableWidget(0, 6)
        self.team_contribution_table.setHorizontalHeaderLabels(["来源", "技能/减抗/增伤", "提升%", "提升面板", "移除损失", "加成"])
        self.team_contribution_table.verticalHeader().setVisible(False)
        self._configure_table(self.team_contribution_table, [100, 148, 76, 92, 128, 330])
        contribution_layout.addWidget(self.team_contribution_table)
        bottom.addWidget(contribution_box, 1)
        layout.addLayout(bottom, 1)
        return page

    def _set_extreme_history_status(self, text: str, status: str = "info") -> None:
        message = norm(text)
        self._extreme_history_status_text = message
        label = getattr(self, "team_extreme_history_status_label", None)
        if not isinstance(label, QLabel):
            return
        label.setText(message)
        label.setVisible(bool(message))
        colors = {
            "success": ("#ecfdf5", "#6ee7b7", "#047857"),
            "warning": ("#fffbeb", "#fcd34d", "#92400e"),
            "info": ("#eff6ff", "#93c5fd", "#1d4ed8"),
        }
        background, border, foreground = colors.get(status, colors["info"])
        label.setStyleSheet(
            f"background:{background}; border:1px solid {border}; color:{foreground}; "
            "padding:7px 10px; border-radius:6px; font-weight:600;"
        )

    def _team_job_options(self) -> list[str]:
        jobs = unique(row.get("二转职业") for row in self.store.tables["job_map"])
        options: list[str] = []
        for job in jobs:
            text = norm(job)
            if text == "曜":
                options.extend(["打坐曜", "输出曜"])
            elif text:
                options.append(text)
        return ["无"] + unique(options)

    @staticmethod
    def _team_real_job(job: str) -> str:
        text = norm(job)
        return "曜" if text in TEAM_YAO_VARIANTS else text

    @staticmethod
    def _team_real_job_set(jobs: list[str] | tuple[str, ...]) -> set[str]:
        return {BuildEditor._team_real_job(job) for job in jobs if BuildEditor._team_real_job(job)}

    @staticmethod
    def _team_unique_job(job: str) -> str:
        text = norm(job)
        if text == "曜":
            return "打坐曜"
        return text if text in TEAM_YAO_VARIANTS else BuildEditor._team_real_job(text)

    @staticmethod
    def _team_unique_job_set(jobs: list[str] | tuple[str, ...]) -> set[str]:
        return {BuildEditor._team_unique_job(job) for job in jobs if BuildEditor._team_unique_job(job)}

    @staticmethod
    def _team_has_real_duplicate(jobs: list[str] | tuple[str, ...]) -> bool:
        unique_jobs = [BuildEditor._team_unique_job(job) for job in jobs if BuildEditor._team_unique_job(job)]
        return len(set(unique_jobs)) != len(unique_jobs)

    def _team_provider_options(self) -> list[str]:
        options = ["通用"]
        if not hasattr(self, "team_member_table"):
            return options
        for row in range(TEAM_MEMBER_ROWS):
            job = self.combo_text(self.team_member_table, row, 1) or "无"
            options.append(f"队员{row + 1} {job}")
        return options

    def _team_provider_job(self, provider: str) -> str:
        text = norm(provider)
        match = re.match(r"队员(\d+)", text)
        if not match:
            return ""
        provider_job = re.sub(r"^队员\d+\s*", "", text).strip()
        if provider_job and provider_job != text and provider_job != "无":
            return self._team_real_job(provider_job)
        if not hasattr(self, "team_member_table"):
            return ""
        row = int(match.group(1)) - 1
        if 0 <= row < TEAM_MEMBER_ROWS:
            return self._team_real_job(self.combo_text(self.team_member_table, row, 1))
        return ""

    def _team_aura_changed(self) -> None:
        self.team_extreme_aura_choices = {}
        self.team_auto_aura_choices = {}
        self._auto_populate_team_effects_from_members(schedule=False)
        self.schedule_refresh()

    def _sync_team_current_member(self) -> None:
        if not hasattr(self, "team_member_table"):
            return
        combo = self.team_member_table.cellWidget(0, 1)
        if isinstance(combo, Combo):
            current_job = self.second_job.currentText() or "无"
            options = self._team_job_options()
            self._replace_combo_values(combo, options, current_job)
        if hasattr(self, "team_effect_table"):
            self._auto_populate_team_effects_from_members(schedule=False)

    def _team_aura_for_job(self, job: str) -> str:
        real_job = self._team_real_job(job)
        if real_job not in TEAM_AURA_JOBS:
            return ""
        extreme_choices = getattr(self, "team_extreme_aura_choices", {})
        if isinstance(extreme_choices, dict):
            choice = norm(extreme_choices.get(real_job))
            if choice in TEAM_AURA_OPTIONS:
                return choice
        auto_choices = getattr(self, "team_auto_aura_choices", {})
        if isinstance(auto_choices, dict):
            choice = norm(auto_choices.get(real_job))
            if choice in TEAM_AURA_OPTIONS:
                return choice
        choices = getattr(self, "team_aura_choices", {})
        combo = choices.get(real_job) if isinstance(choices, dict) else None
        if isinstance(combo, Combo):
            choice = norm(combo.currentText())
            if choice in TEAM_AURA_OPTIONS:
                return choice
        raw_team_sim = self.build.get("team_sim", {}) if isinstance(self.build.get("team_sim", {}), dict) else {}
        raw_choices = raw_team_sim.get("aura_choices", {}) if isinstance(raw_team_sim.get("aura_choices", {}), dict) else {}
        choice = norm(raw_choices.get(real_job))
        return choice if choice in TEAM_AURA_OPTIONS else TEAM_AURA_OPTIONS[0]

    def _team_skills_for_job(self, job: str) -> list[str]:
        raw_job = norm(job)
        real_job = self._team_real_job(job)
        raw_excludes = (
            set(TEAM_JOB_BUFF_VARIANT_EXCLUDES[raw_job])
            if raw_job in TEAM_JOB_BUFF_VARIANT_EXCLUDES
            else set(TEAM_JOB_BUFF_EXCLUDES.get(real_job, set()))
        )
        excluded_skills = {
            self._team_effect_skill_name(skill)
            for skill in raw_excludes
        }
        team_skills = [
            skill for skill in unique(
                self.store.team_buff_names_for_job(real_job)
                + TEAM_JOB_BUFFS.get(real_job, [])
                + TEAM_JOB_BUFF_VARIANT_ADDS.get(raw_job, [])
            )
            if self._team_effect_skill_name(skill) not in excluded_skills
        ]
        if real_job in TEAM_AURA_JOBS:
            aura = self._team_aura_for_job(real_job)
            team_skills = [skill for skill in team_skills if self._team_effect_skill_name(skill) not in TEAM_AURA_OPTIONS]
            if aura:
                team_skills.insert(0, aura)
        # Data-driven exclusive groups also apply to automatically assembled
        # team effects.  Without this pass the elemental fire/ice auras were
        # both injected into normal and extreme simulations even though the
        # build editor correctly treated them as mutually exclusive.
        selected_groups: dict[str, tuple[int, bool]] = {}
        selected_skills: list[str] = []
        for skill in unique(team_skills):
            skill_name = self._team_effect_skill_name(skill)
            rule = self.store.buff_activation_rule_for_job(
                real_job, skill_name, "团队buff"
            )
            group = norm(rule.get("exclusive_group"))
            if not group:
                selected_skills.append(skill)
                continue
            preferred_jobs = {
                norm(value) for value in rule.get("preferred_jobs", []) if norm(value)
            }
            preferred = real_job in preferred_jobs
            previous = selected_groups.get(group)
            if previous is None:
                selected_groups[group] = (len(selected_skills), preferred)
                selected_skills.append(skill)
            elif preferred and not previous[1]:
                selected_skills[previous[0]] = skill
                selected_groups[group] = (previous[0], True)
        return selected_skills

    def _team_skill_matches_current_build(self, job: str, skill: str) -> bool:
        if norm(self._team_real_job(job)) != norm(self.second_job.currentText()):
            return True
        build_level = self._skill_build_level_for_burst_sim(skill)
        return build_level is not None and build_level > 0

    def _set_team_aura_choices(self, choices: dict[str, str]) -> None:
        valid = {
            job: norm(choice)
            for job, choice in choices.items()
            if job in TEAM_AURA_JOBS and norm(choice) in TEAM_AURA_OPTIONS
        }
        if not valid:
            self.team_extreme_aura_choices = {}
            return
        self.team_extreme_aura_choices = dict(valid)
        for job, choice in valid.items():
            combo = getattr(self, "team_aura_choices", {}).get(job) if isinstance(getattr(self, "team_aura_choices", {}), dict) else None
            if isinstance(combo, Combo):
                combo.blockSignals(True)
                self._set_combo_current(combo, choice)
                combo.blockSignals(False)

    @staticmethod
    def _team_aura_key(choices: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((job, norm(choice)) for job, choice in choices.items() if job in TEAM_AURA_JOBS and norm(choice)))

    def _team_aura_combinations_for_jobs(self, jobs: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
        present = [job for job in sorted(TEAM_AURA_JOBS) if job in self._team_real_job_set(jobs)]
        if not present:
            return [{}]
        rows: list[dict[str, str]] = []
        for values in product(TEAM_AURA_OPTIONS, repeat=len(present)):
            rows.append(dict(zip(present, values)))
        return rows

    def _selected_team_jobs(self) -> list[str]:
        if not hasattr(self, "team_member_table"):
            return []
        return [
            self.combo_text(self.team_member_table, row, 1)
            for row in range(TEAM_MEMBER_ROWS)
            if self.combo_text(self.team_member_table, row, 1) and self.combo_text(self.team_member_table, row, 1) != "无"
        ]

    def _team_effect_with_skill(self, effect: dict[str, Any], skill_name: str) -> dict[str, Any]:
        provider = norm(effect.get("provider"))
        skill_label = self._team_effect_option_for_skill(self._team_effect_options(provider), skill_name)
        level_options = self._team_effect_levels(skill_label, provider)
        current_level = norm(effect.get("level"))
        level = current_level if current_level in {str(value) for value in level_options} else self._highest_number_option(level_options)
        attrs = self._team_effect_attrs(skill_label, level, provider)
        timing = self._team_effect_timing(provider, skill_label, level)
        cooldown = as_number(timing.get("cooldown"))
        duration = as_number(timing.get("duration"))
        return {
            **effect,
            "skill": skill_label,
            "level": level,
            "attrs": attrs,
            "cooldown": cooldown,
            "duration": duration,
            "usage_ratio": self._team_effect_usage_default(skill_label),
            "coverage": self._team_effect_coverage(cooldown, duration),
            "effective_attrs": self._team_effective_attrs(attrs, cooldown, duration),
            "manual": self._team_effect_skill_name(skill_label) in TEAM_MANUAL_DEBUFFS,
        }

    def _team_effects_for_aura_choices(self, effects: list[dict[str, Any]], aura_choices: dict[str, str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen_jobs: set[str] = set()
        for effect in effects:
            provider_job = self._team_provider_job(effect.get("provider", ""))
            skill_name = self._team_effect_skill_name(effect.get("skill"))
            if provider_job in TEAM_AURA_JOBS and skill_name in TEAM_AURA_OPTIONS:
                aura = norm(aura_choices.get(provider_job)) or TEAM_AURA_OPTIONS[0]
                out.append(self._team_effect_with_skill(effect, aura))
                seen_jobs.add(provider_job)
            else:
                out.append(effect)
        existing_providers = {norm(effect.get("provider")) for effect in out}
        for member_row, job in enumerate(self._selected_team_jobs()):
            real_job = self._team_real_job(job)
            if real_job not in TEAM_AURA_JOBS or real_job in seen_jobs:
                continue
            provider = f"队员{member_row + 1} {job}"
            if provider not in existing_providers:
                continue
            aura = norm(aura_choices.get(real_job)) or TEAM_AURA_OPTIONS[0]
            out.append(self._team_effect_with_skill({"row": None, "provider": provider, "skill": aura}, aura))
        return out

    def _best_team_aura_effects(
        self,
        raw_effects: list[dict[str, Any]],
        applied_cdr_skills: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        jobs = self._selected_team_jobs()
        combinations = self._team_aura_combinations_for_jobs(jobs)
        if len(combinations) <= 1:
            self.team_auto_aura_choices = {}
            return raw_effects, {}
        best_effects = raw_effects
        best_choices: dict[str, str] = {}
        best_score = -1.0
        for choices in combinations:
            candidate_raw = self._team_effects_for_aura_choices(raw_effects, choices)
            candidate_base = self._dedup_team_effects(candidate_raw)
            candidate_effects = self._team_effects_with_cdr_adjusted_cooldowns(candidate_base, applied_cdr_skills)
            summary, _attrs = self._team_summary_with_effects(candidate_effects)
            score = as_number(summary.get("effective_panel"))
            if score > best_score:
                best_score = score
                best_effects = candidate_raw
                best_choices = dict(choices)
        self.team_auto_aura_choices = dict(best_choices)
        self._sync_team_aura_choice_ui(best_choices)
        return best_effects, best_choices

    def _sync_team_aura_choice_ui(self, choices: dict[str, str]) -> None:
        if not choices:
            return
        for job, choice in choices.items():
            if job not in TEAM_AURA_JOBS or choice not in TEAM_AURA_OPTIONS:
                continue
            combo = getattr(self, "team_aura_choices", {}).get(job) if isinstance(getattr(self, "team_aura_choices", {}), dict) else None
            if isinstance(combo, Combo) and combo.currentText() != choice:
                combo.blockSignals(True)
                self._set_combo_current(combo, choice)
                combo.blockSignals(False)
        if not hasattr(self, "team_effect_table"):
            return
        self.team_effect_table.blockSignals(True)
        try:
            for row in range(self.team_effect_table.rowCount()):
                provider = self.combo_text(self.team_effect_table, row, 1)
                provider_job = self._team_provider_job(provider)
                choice = choices.get(provider_job)
                if not choice:
                    continue
                skill_name = self._team_effect_skill_name(self.combo_text(self.team_effect_table, row, 2))
                if skill_name not in TEAM_AURA_OPTIONS:
                    continue
                skill_combo = self.team_effect_table.cellWidget(row, 2)
                if isinstance(skill_combo, Combo):
                    self._replace_combo_values(skill_combo, self._team_effect_options(provider), self._team_effect_option_for_skill(self._team_effect_options(provider), choice))
                level_combo = self.team_effect_table.cellWidget(row, 3)
                levels = self._team_effect_levels(choice, provider)
                level_text = self._team_effect_default_level(choice, provider, levels)
                if isinstance(level_combo, Combo):
                    self._replace_combo_values(level_combo, levels, level_text)
                timing = self._team_effect_timing(provider, choice, level_text)
                cooldown = self.team_effect_table.cellWidget(row, 4)
                if isinstance(cooldown, QDoubleSpinBox):
                    cooldown.blockSignals(True)
                    cooldown.setProperty("base_cooldown", float(as_number(timing.get("cooldown"))))
                    cooldown.setValue(float(as_number(timing.get("cooldown"))))
                    cooldown.blockSignals(False)
                duration = self.team_effect_table.cellWidget(row, 5)
                if isinstance(duration, QDoubleSpinBox):
                    duration.blockSignals(True)
                    duration.setValue(float(as_number(timing.get("duration"))))
                    duration.blockSignals(False)
        finally:
            self.team_effect_table.blockSignals(False)

    def _team_job_attack_profile(self, job: str) -> dict[str, str]:
        job = self._team_real_job(job)
        if norm(job) == "曜":
            return {"attack": "物攻", "element": "光"}
        row = next((item for item in self.store.tables["effective"] if norm(item.get("职业")) == norm(job)), {})
        attack = norm(row.get("攻击1")) or "物攻"
        if attack == "双攻":
            attack = "物攻"
        if attack not in {"物攻", "魔攻"}:
            attack = "物攻"
        element = norm(row.get("属性1"))
        if element == "无":
            element = ""
        return {"attack": attack, "element": element}

    def _remove_timeline_owned_build_buffs(self, build: dict[str, Any], job: str) -> None:
        real_job = self._team_real_job(job)
        job_sources = set(self.store.job_buff_sources(real_job))
        kept: list[dict[str, Any]] = []
        for item in build.get("buffs", []):
            label = item.get("skill") if isinstance(item, dict) else ""
            row = self._buff_row_for_label(label)
            if row is None:
                kept.append(item)
                continue
            category = norm(row.get("类别"))
            source = norm(row.get("来源"))
            rule = self.store.buff_activation_rule(source, row.get("技能"), category)
            timeline_owned = (
                category == "个人buff"
                and source in job_sources
                and bool(rule)
                and norm(rule.get("mode")) != "permanent"
            )
            if not timeline_owned:
                kept.append(item)
        build["buffs"] = kept

    def _job_family_groups(self, jobs: list[str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for job in jobs:
            family = self.store.job_family(self._team_real_job(job), "") or "其他"
            groups.setdefault(family, []).append(job)
        return groups

    def auto_best_team(self) -> None:
        if getattr(self, "_team_calculation_active", False):
            return
        self._team_calculation_active = True
        try:
            self._auto_best_team_impl()
        finally:
            self._team_calculation_active = False

    def _auto_best_team_impl(self) -> None:
        if not hasattr(self, "team_member_table") or not hasattr(self, "team_effect_table"):
            return
        self.build = self.collect_build()
        _rows, base_summary = self.calculator.calculate(self.build)
        base_effective = as_number(base_summary.get("effective_panel"))
        jobs = [job for job in self._team_job_options() if job and job != "无"]
        current_job = self.second_job.currentText() if hasattr(self, "second_job") else ""
        if norm(current_job) == "曜":
            current_job = "打坐曜"
        selected: list[str] = [current_job] if current_job and current_job != "无" else []
        score_cache: dict[tuple[str, ...], float] = {}
        total_steps = max(1, len(jobs) * max(1, TEAM_MEMBER_ROWS - len(selected)) + len(jobs) * TEAM_MEMBER_ROWS * 2)
        progress = QProgressDialog("正在计算候选队伍...", "取消", 0, total_steps, self)
        progress.setWindowTitle("一键配队")
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress_value = 0
        cancelled = False

        def progress_step(text: str) -> bool:
            nonlocal progress_value, cancelled
            if cancelled:
                return False
            progress_value += 1
            if progress_value >= progress.maximum():
                progress.setMaximum(progress_value + 1)
            progress.setLabelText(text)
            progress.setValue(progress_value)
            QApplication.processEvents()
            cancelled = progress.wasCanceled()
            return not cancelled

        def score(candidate: list[str]) -> float:
            key = tuple(candidate)
            if key not in score_cache:
                score_cache[key] = self._team_score_for_jobs(candidate)
            return score_cache[key]

        best_score = score(selected) if selected else base_effective
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for _slot in range(len(selected), TEAM_MEMBER_ROWS):
                slot_best_job = ""
                slot_best_score = best_score
                for job in jobs:
                    if not progress_step(f"正在选择第 {len(selected) + 1} 个队员：测试 {job}"):
                        break
                    if self._team_unique_job(job) in self._team_unique_job_set(selected):
                        continue
                    candidate = selected + [job]
                    candidate_score = score(candidate)
                    if candidate_score > slot_best_score + 1e-6:
                        slot_best_score = candidate_score
                        slot_best_job = job
                if cancelled:
                    break
                if not slot_best_job or slot_best_score <= best_score + 1e-6:
                    break
                selected.append(slot_best_job)
                best_score = slot_best_score
            if not cancelled:
                fixed_count = 1 if selected and current_job and selected[0] == current_job else 0
                selected, best_score = self._optimize_team_selection(selected, jobs, best_score, fixed_count, score, progress_step)
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()

        if cancelled:
            return

        self.team_member_table.blockSignals(True)
        try:
            for row in range(TEAM_MEMBER_ROWS):
                widget = self.team_member_table.cellWidget(row, 1)
                if isinstance(widget, Combo):
                    self._set_combo_current(widget, selected[row] if row < len(selected) else "无")
        finally:
            self.team_member_table.blockSignals(False)

        self._auto_populate_team_effects_from_members(schedule=False)
        selected_cdr = self._selected_team_cdr_skills()
        for skill, check in getattr(self, "team_cdr_glyph_checks", {}).items():
            if isinstance(check, QCheckBox):
                check.blockSignals(True)
                check.setChecked(skill in selected_cdr)
                check.blockSignals(False)
        for skill, check in getattr(self, "team_cdr_apply_checks", {}).items():
            if isinstance(check, QCheckBox):
                check.blockSignals(True)
                check.setChecked(skill in selected_cdr)
                check.blockSignals(False)
        self._refresh_team_cdr_usage_visibility()
        base_effects = self._selected_team_effects(use_base_cooldowns=True)
        self._sync_team_effect_cooldowns(base_effects, self._team_applied_cdr_skills())
        self._request_team_effect_auto_sort()
        self.schedule_refresh()

        gain = best_score - base_effective
        gain_percent = gain / base_effective * 100 if base_effective > 0 else 0.0
        team_text = "、".join(selected) if selected else "无"
        QMessageBox.information(
            self,
            "一键配队",
            f"已生成当前配装的推荐队伍：\n{team_text}\n\n预计提升：{number_text(gain, integer=True)} 面板 / {gain_percent:.2f}%",
        )

    def _optimize_team_selection(
        self,
        selected: list[str],
        jobs: list[str],
        best_score: float,
        fixed_count: int = 0,
        score_func=None,
        progress_step=None,
    ) -> tuple[list[str], float]:
        if not selected or not jobs:
            return selected, best_score
        cache: dict[tuple[str, ...], float] = {tuple(selected): best_score}

        def score(candidate: list[str]) -> float:
            if score_func:
                return score_func(candidate)
            key = tuple(candidate)
            if key not in cache:
                cache[key] = self._team_score_for_jobs(candidate)
            return cache[key]

        improved = True
        passes = 0
        while improved and passes < 2:
            improved = False
            passes += 1
            for index in range(fixed_count, len(selected)):
                current = selected[index]
                slot_best_job = current
                slot_best_score = best_score
                for job in jobs:
                    if job == current:
                        continue
                    if self._team_unique_job(job) in self._team_unique_job_set(selected):
                        continue
                    if progress_step and not progress_step(f"正在优化第 {index + 1} 个队员：尝试 {job}"):
                        return selected, best_score
                    candidate = selected[:]
                    candidate[index] = job
                    candidate_score = score(candidate)
                    if candidate_score > slot_best_score + 1e-6:
                        slot_best_score = candidate_score
                        slot_best_job = job
                if slot_best_job != current:
                    selected[index] = slot_best_job
                    best_score = slot_best_score
                    improved = True

        return selected, best_score

    def _apply_team_members(self, selected: list[str]) -> None:
        self.team_member_table.blockSignals(True)
        try:
            for row in range(TEAM_MEMBER_ROWS):
                widget = self.team_member_table.cellWidget(row, 1)
                if isinstance(widget, Combo):
                    self._set_combo_current(widget, selected[row] if row < len(selected) else "无")
        finally:
            self.team_member_table.blockSignals(False)

    @staticmethod
    def _attr_cache_signature(attrs: dict[str, float]) -> tuple[tuple[str, float], ...]:
        return tuple(
            sorted(
                (norm(attr), round(as_number(value), 6))
                for attr, value in attrs.items()
                if abs(as_number(value)) > 1e-9
            )
        )

    def _team_score_for_jobs(self, jobs: list[str]) -> float:
        effects = self._team_effects_for_jobs(jobs)
        base_effects = self._team_effects_with_auto_cdr_glyphs(self._dedup_team_effects(effects))
        applied_cdr_skills = {
            self._team_effect_skill_name(effect.get("skill"))
            for effect in base_effects
            if self._team_effect_skill_name(effect.get("skill")) in TEAM_CDR_APPLY_SKILLS
        }
        adjusted = self._team_effects_with_cdr_adjusted_cooldowns(base_effects, applied_cdr_skills)
        summary, _attrs = self._team_summary_with_effects(adjusted)
        return as_number(summary.get("effective_panel"))

    def _team_effects_for_jobs(self, jobs: list[str]) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        for member_row, job in enumerate(jobs[:TEAM_MEMBER_ROWS]):
            if not job or job == "无":
                continue
            provider = f"队员{member_row + 1} {job}"
            team_skills = self._team_skills_for_job(job)
            for skill in team_skills:
                skill_name = self._team_effect_skill_name(skill)
                levels = self._team_effect_levels(skill_name, provider)
                level = self._team_effect_default_level(skill_name, provider, levels)
                attrs = self._team_effect_attrs(skill_name, level, provider)
                if not attrs:
                    continue
                timing = self._team_effect_timing(provider, skill_name, level)
                cooldown = as_number(timing.get("cooldown"))
                duration = as_number(timing.get("duration"))
                effects.append({
                    "row": None,
                    "provider": provider,
                    "skill": self._team_manual_effect_label(skill_name) if skill_name in TEAM_MANUAL_DEBUFFS else skill_name,
                    "level": level,
                    "attrs": attrs,
                    "cooldown": cooldown,
                    "duration": duration,
                    "usage_ratio": self._team_cdr_usage_ratio(skill_name),
                    "coverage": self._team_effect_coverage(cooldown, duration),
                    "effective_attrs": self._team_effective_attrs(attrs, cooldown, duration),
                    "manual": skill_name in TEAM_MANUAL_DEBUFFS,
                })
        return effects

    @classmethod
    def _team_effects_with_auto_cdr_glyphs(cls, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for effect in effects:
            skill = cls._team_effect_skill_name(effect.get("skill"))
            reduction = as_number(TEAM_CDR_GLYPH_REDUCTION.get(skill))
            if reduction <= 0:
                out.append(effect)
                continue
            cooldown = max(0.0, as_number(effect.get("cooldown")) * max(0.0, 1 - reduction / 100))
            copied = {**effect, "cooldown": cooldown}
            copied["coverage"] = cls._team_effect_coverage(cooldown, copied.get("duration", 0))
            copied["effective_attrs"] = cls._team_effective_attrs(copied.get("attrs", {}), cooldown, copied.get("duration", 0))
            out.append(copied)
        return out

    def _team_timing_spin(self, value: Any = 0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 9999)
        spin.setDecimals(1)
        spin.setSingleStep(1)
        spin.setFixedHeight(EDIT_ROW_HEIGHT)
        spin.setValue(float(as_number(value)))
        spin.valueChanged.connect(self.schedule_refresh)
        return spin

    def _team_cooldown_spin(self, row: int, value: Any = 0) -> QDoubleSpinBox:
        spin = self._team_timing_spin(value)
        with contextlib.suppress(TypeError, RuntimeError):
            spin.valueChanged.disconnect()
        spin.setProperty("base_cooldown", float(as_number(value)))
        spin.valueChanged.connect(lambda current, r=row: self._team_cooldown_changed(r, current))
        return spin

    def _team_cooldown_changed(self, row: int, value: float) -> None:
        if getattr(self, "_syncing_team_cooldowns", False):
            return
        table = getattr(self, "team_effect_table", None)
        if not isinstance(table, QTableWidget):
            self.schedule_refresh()
            return
        widget = table.cellWidget(row, 4)
        if isinstance(widget, QDoubleSpinBox):
            skill = self.combo_text(table, row, 2)
            skill_name = self._team_effect_skill_name(skill)
            if skill_name in TEAM_CDR_USAGE_SKILLS:
                factor = self._team_cdr_glyph_factor(skill_name)
            else:
                factor = self._team_applied_cdr_factor()
            base = float(value) / factor if factor > 0 else float(value)
            widget.setProperty("base_cooldown", base)
        self.schedule_refresh()

    def _team_usage_spin(self, value: Any = 100) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, 100)
        spin.setDecimals(0)
        spin.setSingleStep(5)
        spin.setSuffix("%")
        spin.setFixedHeight(EDIT_ROW_HEIGHT)
        spin.setValue(float(max(0.0, min(100.0, as_number(value, 100)))))
        spin.valueChanged.connect(self.schedule_refresh)
        return spin

    @staticmethod
    def _team_effect_usage_default(skill: Any) -> float:
        skill_name = BuildEditor._team_effect_skill_name(skill)
        if skill_name == "时间加速":
            return 60.0
        if skill_name == "精神爆发":
            return 80.0
        return 100.0

    @staticmethod
    def _team_effect_skill_name(skill: Any) -> str:
        text = skill if isinstance(skill, str) else norm(skill)
        return BuildEditor._team_effect_skill_name_cached(text)

    @staticmethod
    @lru_cache(maxsize=8192)
    def _team_effect_skill_name_cached(text: str) -> str:
        return text.strip().split("｜")[-1]

    @staticmethod
    def _team_manual_effect_category(skill: Any) -> str:
        attrs = TEAM_MANUAL_DEBUFFS.get(BuildEditor._team_effect_skill_name(skill), ({}, []))[0]
        keys = {norm(attr) for attr in attrs}
        if any(key in {"减致命抵抗%", "减致命一击抵抗%"} for key in keys):
            return "减致命抗debuff"
        if any(key in {"减防御%", "减防御力%"} for key in keys):
            return "减防debuff"
        if any(key.startswith("减") and key.endswith("抗%") for key in keys):
            return "减抗debuff"
        if any("额外系数" in key for key in keys):
            return "团队buff"
        if any("冰冻" in key for key in keys):
            return "叠冰debuff"
        if any("增伤" in key or "额外系数" in key for key in keys):
            return "增伤debuff"
        return "团队buff"

    @staticmethod
    def _team_manual_effect_label(skill: Any) -> str:
        skill_name = BuildEditor._team_effect_skill_name(skill)
        return f"{BuildEditor._team_manual_effect_category(skill_name)}｜{skill_name}"

    @staticmethod
    def _team_data_effect_label(row: dict[str, Any]) -> str:
        skill = norm(row.get("技能")) or BuildEditor._team_effect_skill_name(buff_label(row))
        category = norm(row.get("类别"))
        attr = norm(row.get("属性"))
        if attr in {"减致命抵抗%", "减致命一击抵抗%"}:
            prefix = "减致命抗debuff"
        elif attr in {"减防御%", "减防御力%"}:
            prefix = "减防debuff"
        elif attr.startswith("减") and attr.endswith("抗%"):
            prefix = "减抗debuff"
        elif "增伤" in attr:
            prefix = "增伤debuff"
        elif attr == "冰冻层":
            prefix = "叠冰debuff"
        elif category == "团队buff":
            prefix = "团队buff"
        else:
            prefix = category or "团队buff"
        return f"{prefix}｜{skill}"

    @staticmethod
    def _team_effect_level_cap(skill: Any) -> int:
        return int(as_number(TEAM_EFFECT_LEVEL_CAPS.get(BuildEditor._team_effect_skill_name(skill))))

    @staticmethod
    def _team_effect_max_required_level(skill: Any) -> float:
        return 80.0 if BuildEditor._team_effect_level_cap(skill) > 0 else 70.0

    def _team_cdr_usage_defaults(self, defaults: dict[str, Any]) -> dict[str, float]:
        values = {skill: self._team_effect_usage_default(skill) for skill in TEAM_CDR_USAGE_SKILLS}
        raw = defaults.get("cdr_usage")
        if isinstance(raw, dict):
            for skill in TEAM_CDR_USAGE_SKILLS:
                if skill in raw:
                    values[skill] = max(0.0, min(100.0, as_number(raw.get(skill), values[skill])))
        effects = defaults.get("effects", [])
        if isinstance(effects, list):
            for item in effects:
                if not isinstance(item, dict) or "usage_ratio" not in item:
                    continue
                skill = self._team_effect_skill_name(item.get("skill"))
                if skill in values:
                    values[skill] = max(0.0, min(100.0, as_number(item.get("usage_ratio"), values[skill])))
        return values

    def _team_cdr_usage_ratio(self, skill: Any) -> float:
        skill_name = self._team_effect_skill_name(skill)
        if skill_name not in TEAM_CDR_USAGE_SKILLS:
            return 100.0
        spin = getattr(self, "team_cdr_usage_spins", {}).get(skill_name)
        if isinstance(spin, QDoubleSpinBox):
            return float(spin.value())
        return self._team_effect_usage_default(skill_name)

    def _team_cdr_glyph_enabled(self, skill: Any) -> bool:
        skill_name = self._team_effect_skill_name(skill)
        check = getattr(self, "team_cdr_glyph_checks", {}).get(skill_name)
        return bool(isinstance(check, QCheckBox) and check.isChecked())

    def _team_cdr_glyph_factor(self, skill: Any) -> float:
        skill_name = self._team_effect_skill_name(skill)
        if not self._team_cdr_glyph_enabled(skill_name):
            return 1.0
        return max(0.0, 1.0 - as_number(TEAM_CDR_GLYPH_REDUCTION.get(skill_name)) / 100)

    def _team_cdr_apply_enabled(self, skill: Any) -> bool:
        skill_name = self._team_effect_skill_name(skill)
        check = getattr(self, "team_cdr_apply_checks", {}).get(skill_name)
        return bool(isinstance(check, QCheckBox) and check.isChecked())

    def _team_applied_cdr_skills(self) -> set[str]:
        selected = self._selected_team_cdr_skills() if hasattr(self, "team_effect_table") else set()
        return {
            skill
            for skill in TEAM_CDR_APPLY_SKILLS
            if skill in selected and self._team_cdr_apply_enabled(skill)
        }

    def _team_applied_cdr_factor(self) -> float:
        effects = self._selected_team_effects(use_base_cooldowns=True) if hasattr(self, "team_effect_table") else []
        return self._team_cdr_adjusted_cooldown(100.0, effects, self._team_applied_cdr_skills()) / 100.0

    @classmethod
    def _team_cdr_percent_for_skills(cls, effects: list[dict[str, Any]], skills: set[str]) -> float:
        if not skills:
            return 0.0
        by_skill: dict[str, float] = {}
        for effect in effects:
            skill = cls._team_effect_skill_name(effect.get("skill"))
            if skill not in skills:
                continue
            attrs = effect.get("attrs", {})
            value = max(0.0, as_number(attrs.get("冷却缩减%")))
            if value > 0:
                by_skill[skill] = max(by_skill.get(skill, 0.0), value)
        return max(0.0, min(70.0, sum(by_skill.values())))

    @classmethod
    def _team_cdr_effect_groups_for_skills(cls, effects: list[dict[str, Any]], skills: set[str]) -> list[dict[str, float]]:
        if not skills:
            return []
        groups: dict[tuple[str, float, float], dict[str, float]] = {}
        for effect in effects:
            skill = cls._team_effect_skill_name(effect.get("skill"))
            if skill not in skills:
                continue
            cdr = max(0.0, as_number(effect.get("attrs", {}).get("冷却缩减%")))
            if cdr <= 0:
                continue
            cooldown = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            if duration <= 0 and cooldown > 0:
                continue
            key = (skill, cdr, cooldown)
            group = groups.setdefault(key, {"cdr": cdr, "cooldown": cooldown, "duration": 0.0})
            used_duration = duration if cooldown <= 0 else min(duration, cooldown)
            if cooldown <= 0:
                group["duration"] = max(group["duration"], used_duration or 1.0)
            else:
                group["duration"] = min(cooldown, group["duration"] + used_duration)
        return list(groups.values())

    @classmethod
    def _team_cdr_adjusted_cooldown(cls, base_cooldown: float, effects: list[dict[str, Any]], applied_skills: set[str]) -> float:
        base = max(0.0, as_number(base_cooldown))
        if base <= 0 or not applied_skills:
            return base
        groups = cls._team_cdr_effect_groups_for_skills(effects, applied_skills)
        if not groups:
            return base

        always_cdr = 0.0
        periodic_groups: list[dict[str, float]] = []
        periods: list[int] = []
        for group in groups:
            cdr = max(0.0, as_number(group.get("cdr")))
            cooldown = max(0.0, as_number(group.get("cooldown")))
            duration = max(0.0, as_number(group.get("duration")))
            if cdr <= 0:
                continue
            if cooldown <= 0 or duration >= cooldown:
                always_cdr += cdr
                continue
            if duration <= 0:
                continue
            item = {"cdr": cdr, "cooldown": cooldown, "duration": duration}
            periodic_groups.append(item)
            periods.append(max(1, int(round(cooldown * 10))))

        if not periodic_groups:
            return base * max(0.3, 1.0 - min(70.0, always_cdr) / 100)

        horizon_ticks = periods[0]
        for period in periods[1:]:
            horizon_ticks = horizon_ticks * period // gcd(horizon_ticks, period)
            if horizon_ticks > 36000:
                horizon_ticks = 6000
                break

        intervals_by_group: list[tuple[float, list[tuple[int, int]]]] = []
        boundaries = {0, horizon_ticks}
        for group in periodic_groups:
            cd_ticks = max(1, int(round(group["cooldown"] * 10)))
            dur_ticks = max(1, int(round(min(group["duration"], group["cooldown"]) * 10)))
            intervals: list[tuple[int, int]] = []
            for start in range(0, horizon_ticks, cd_ticks):
                end = min(horizon_ticks, start + dur_ticks)
                if end <= start:
                    continue
                intervals.append((start, end))
                boundaries.add(start)
                boundaries.add(end)
            intervals_by_group.append((group["cdr"], intervals))

        ordered = sorted(boundaries)
        segments: list[tuple[float, float]] = []
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            mid = (start + end) / 2
            active_cdr = always_cdr
            for cdr, intervals in intervals_by_group:
                if any(left <= mid < right for left, right in intervals):
                    active_cdr += cdr
            cdr_percent = min(70.0, max(0.0, active_cdr))
            speed = 1.0 / max(0.3, 1.0 - cdr_percent / 100)
            segments.append(((end - start) / 10, speed))

        period_time = horizon_ticks / 10
        work_per_period = sum(duration * speed for duration, speed in segments)
        if work_per_period <= 0:
            return base
        average_speed = work_per_period / period_time
        if average_speed <= 0:
            return base
        return base / average_speed

    def _team_cooldown_base(self, row: int) -> float:
        widget = self.team_effect_table.cellWidget(row, 4) if hasattr(self, "team_effect_table") else None
        if not isinstance(widget, QDoubleSpinBox):
            return 0.0
        return as_number(widget.property("base_cooldown"), widget.value())

    def _team_effect_timing(self, provider: str, skill: str, level: Any = None) -> dict[str, Any]:
        provider_job = self._team_provider_job(provider)
        skill_tail = self._team_effect_skill_name(skill)
        level_cap = self._team_effect_level_cap(skill_tail)
        if level_cap > 0 and level in (None, "", "无"):
            level = level_cap
        max_required = self._team_effect_max_required_level(skill_tail)
        if self._team_combat_skill_level(skill_tail, provider) is not None:
            max_required = 150
        timing = self.store.skill_effect_timing(provider_job, skill_tail, level, max_required)
        if not as_number(timing.get("cooldown")) and not as_number(timing.get("duration")):
            timing = self.store.skill_effect_timing(provider_job, skill_tail, level, max_required)
        if skill_tail.upper().endswith("EX") and not as_number(timing.get("cooldown")):
            base_skill = re.sub(r"\s*EX$", "", skill_tail, flags=re.IGNORECASE)
            base_timing = self.store.skill_effect_timing(provider_job, base_skill, level, self._team_effect_max_required_level(base_skill))
            if as_number(base_timing.get("cooldown")):
                timing = {
                    **timing,
                    "cooldown": base_timing.get("cooldown"),
                    "duration": timing.get("duration") or base_timing.get("duration"),
                    "desc": timing.get("desc") or base_timing.get("desc", ""),
                }
        defaults = {
            "精神爆发": {"cooldown": 44, "duration": 14},
            "鸡尾酒": {"cooldown": 10, "duration": 150},
            "时间加速": {"cooldown": 100, "duration": 15},
            "瞄准": {"cooldown": 10, "duration": 60},
            "能量反射": {"cooldown": 0, "duration": 24},
            "天堂审判": {"cooldown": 125, "duration": 15},
            "精神爆破EX": {"cooldown": 10, "duration": 7},
            "冲击之手": {"cooldown": 22, "duration": 20},
            "掌握之手EX": {"cooldown": 22, "duration": 15},
            "黄昏之光": {"cooldown": 30, "duration": 10},
            "爆裂踢EX": {"cooldown": 25, "duration": 9},
            "冰刃EX": {"cooldown": 11, "duration": 7},
            "突袭": {"cooldown": 10, "duration": 300},
            "奉献乌鸦": {"cooldown": 90, "duration": 15},
            "九天之舞2阶": {"cooldown": 90, "duration": 15},
            "霜冻EX": {"cooldown": 18, "duration": 4},
            "寒冰戳刺EX": {"cooldown": 19, "duration": 3},
            "切骨之刺EX": {"cooldown": 13, "duration": 10},
            "旋风斩EX": {"cooldown": 13, "duration": 7},
            "火墙EX": {"cooldown": 17, "duration": 15},
            "重磅轰击": {"cooldown": 8, "duration": 12},
            "灵魂尖叫": {"cooldown": 40, "duration": 15},
            "致命狂镰": {"cooldown": 13, "duration": 8},
            "惩罚": {"cooldown": 24, "duration": 12},
            "致命狂镰EX": {"cooldown": 13, "duration": 8},
            "惩罚EX": {"cooldown": 24, "duration": 12},
        }.get(skill_tail, {})
        cooldown = as_number(timing.get("cooldown")) or as_number(defaults.get("cooldown"))
        duration = as_number(timing.get("duration")) or as_number(defaults.get("duration"))
        activation_rule = self.store.buff_activation_rule_for_job(
            provider_job, skill_tail, "个人buff"
        ) or self.store.buff_activation_rule_for_job(
            provider_job, skill_tail, "团队buff"
        )
        rule_cooldown = max(0.0, as_number(activation_rule.get("cooldown")))
        rule_duration = max(0.0, as_number(activation_rule.get("duration")))
        if rule_cooldown > 0:
            cooldown = rule_cooldown
        if rule_duration > 0:
            duration = rule_duration
        cooldown_multipliers = JOB_MECHANICS.job_value(
            provider_job, "team_effect_cooldown_multipliers", {}
        )
        if isinstance(cooldown_multipliers, dict) and skill_tail in cooldown_multipliers:
            cooldown *= max(0.0, as_number(cooldown_multipliers.get(skill_tail), 1.0))
        if skill_tail in TEAM_MANUAL_DEBUFFS:
            duration = self._team_effect_duration_from_desc(skill_tail, timing.get("desc", "")) or duration
        if skill_tail == "冰柱喷射" and duration > 0:
            duration += 5
        duration = team_effect_duration_override(skill_tail) or duration
        if skill_tail == "查克拉治愈术" and cooldown > 0:
            duration = cooldown
        return {
            "cooldown": cooldown,
            "duration": duration,
            "desc": timing.get("desc", ""),
        }

    def _team_effect_options(self, provider: str = "") -> list[str]:
        provider_job = self._team_provider_job(provider)
        rows: list[dict[str, Any]] = []
        manual_skills = set(TEAM_MANUAL_DEBUFFS)
        for row in self.store.tables["buff"]:
            category = norm(row.get("类别"))
            source = norm(row.get("来源"))
            attr = norm(row.get("属性"))
            skill = norm(row.get("技能"))
            is_resist_effect = attr.startswith("减") and attr.endswith("抗%")
            if skill in manual_skills:
                continue
            if category == "团队buff" or (provider_job and source == provider_job and category != "被动" and is_resist_effect):
                rows.append(row)
        options = unique([self._team_data_effect_label(row) for row in rows] + [self._team_manual_effect_label(skill) for skill in TEAM_MANUAL_DEBUFFS])
        return ["无"] + sorted(options, key=self._team_effect_option_sort_key)

    @staticmethod
    def _team_effect_option_sort_key(option: str) -> tuple[int, str, str]:
        category, _, skill = norm(option).partition("｜")
        order = {
            "团队buff": 0,
            "减抗debuff": 1,
            "减防debuff": 2,
            "减致命抗debuff": 3,
            "叠冰debuff": 4,
            "增伤debuff": 5,
        }.get(category, 9)
        return (order, category, skill)

    def _team_effect_levels(self, skill: str, provider: str = "") -> list[str]:
        text = self._team_effect_skill_name(skill)
        if text in TEAM_MANUAL_DEBUFFS:
            provider_job = self._team_provider_job(provider)
            levels = self.store.full_skill_levels(provider_job, text, self._team_effect_max_required_level(text))
            if text.upper().endswith("EX") and self._team_effect_level_cap(text) > 1:
                base_levels = self.store.full_skill_levels(provider_job, re.sub(r"\s*EX$", "", text, flags=re.IGNORECASE), self._team_effect_max_required_level(text))
                if base_levels:
                    levels = base_levels
            elif text.upper().endswith("EX") and not levels:
                levels = self.store.full_skill_levels(provider_job, re.sub(r"\s*EX$", "", text, flags=re.IGNORECASE), self._team_effect_max_required_level(text))
            if levels:
                return self._cap_level_options([str(row.get("_DisplayLevel")) for row in levels], text)
            return self._cap_level_options([str(value) for value in TEAM_MANUAL_DEBUFFS[text][1]], text)
        return self._cap_level_options(self.store.skill_levels(text), text)

    def _team_combat_skill_level(self, skill: str, provider: str = "") -> int | None:
        from ..live_dps.scoring import canonical_skill
        build = getattr(self, 'build', {})
        levels = build.get('_score_combat_levels_by_provider', {}).get(provider, {})
        return levels.get(canonical_skill(self._team_effect_skill_name(skill)))

    def _team_effect_learned_level(self, skill: str, provider: str = "") -> int:
        """Resolve one effect's level from the provider's current/default build."""
        combat_level = self._team_combat_skill_level(skill, provider)
        if combat_level is not None:
            return combat_level
        job = self._team_provider_job(provider)
        if not job and hasattr(self, "second_job"):
            job = self._team_real_job(self.second_job.currentText())
        job = self._team_real_job(job)
        if not job:
            return 0

        skill_name = self._team_effect_skill_name(skill)
        candidates = {norm(skill_name)}
        alias_map = SKILL_BUILD_LEVEL_ALIASES_BY_JOB.get(job, {})
        for name in tuple(candidates):
            alias = norm(alias_map.get(name))
            if alias:
                candidates.add(alias)

        tree = self.store.skill_build_tree(job, SKILL_BUILD_LEVEL_CAP)
        skills = tree.get("skills", {}) if isinstance(tree, dict) else {}
        defaults = load_skill_build_defaults().get(job, {})
        normal_levels = (
            (defaults.get("skill_build") or {}).get("levels", {})
            if isinstance(defaults, dict)
            else {}
        )
        mastery_levels = (
            (defaults.get("skill_mastery_build") or {}).get("levels", {})
            if isinstance(defaults, dict)
            else {}
        )

        current_job = ""
        if hasattr(self, "second_job"):
            current_job = self._team_real_job(self.second_job.currentText())
        if current_job == job:
            saved = self.build.get("skill_build", {}) if isinstance(self.build, dict) else {}
            saved_mastery = (
                self.build.get("skill_mastery_build", {})
                if isinstance(self.build, dict)
                else {}
            )
            if isinstance(saved, dict) and norm(saved.get("second_job")) == job:
                normal_levels = saved.get("levels", normal_levels)
            if isinstance(saved_mastery, dict):
                mastery_levels = saved_mastery.get("levels", mastery_levels)

        def level_for_names(names: set[str]) -> int:
            best = 0
            for skill_id, item in skills.items():
                if not isinstance(item, dict) or norm(item.get("name")) not in names:
                    continue
                key = norm(skill_id)
                normal_spin = getattr(self, "skill_build_level_spins", {}).get(key)
                mastery_spin = getattr(self, "skill_mastery_spins", {}).get(key)
                normal = (
                    int(normal_spin.value())
                    if isinstance(normal_spin, QSpinBox)
                    else int(as_number(normal_levels.get(key)))
                )
                mastery = (
                    int(mastery_spin.value())
                    if isinstance(mastery_spin, QSpinBox)
                    else int(as_number(mastery_levels.get(key)))
                )
                best = max(best, normal + mastery)
            return best

        learned = level_for_names(candidates)
        if re.search(r"\s*EX$", skill_name, flags=re.IGNORECASE):
            base_name = re.sub(r"\s*EX$", "", skill_name, flags=re.IGNORECASE)
            base_level = level_for_names({base_name})
            if self._team_effect_level_cap(skill_name) > 1:
                learned = base_level + learned if base_level > 0 else learned
            elif learned <= 0:
                learned = base_level
        return max(0, learned)

    def _team_effect_default_level(
        self,
        skill: str,
        provider: str = "",
        levels: list[str] | None = None,
    ) -> str:
        """Choose a valid classic-server level without reading the last data row."""
        skill_name = self._team_effect_skill_name(skill)
        combat_level = self._team_combat_skill_level(skill_name, provider)
        if combat_level is not None:
            return str(combat_level)
        options = list(levels) if levels is not None else self._team_effect_levels(skill_name, provider)
        numeric = sorted(
            {
                int(as_number(option))
                for option in options
                if int(as_number(option)) > 0
            }
        )
        if not numeric:
            return "无"

        explicit_cap = self._team_effect_level_cap(skill_name)
        learned = self._team_effect_learned_level(skill_name, provider)
        desired = learned or explicit_cap
        if explicit_cap > 0 and desired > 0:
            desired = min(desired, explicit_cap)
        if desired <= 0:
            job = self._team_provider_job(provider)
            if not job and hasattr(self, "second_job"):
                job = self._team_real_job(self.second_job.currentText())
            available = self.store.full_skill_levels(
                self._team_real_job(job), skill_name, SKILL_BUILD_LEVEL_CAP
            ) if job else []
            desired = max(
                (int(as_number(row.get("_DisplayLevel"))) for row in available),
                default=0,
            )
        if desired <= 0:
            desired = numeric[-1]
        return str(max((level for level in numeric if level <= desired), default=numeric[0]))

    def _team_manual_effect_applies(self, skill: str, provider: str = "") -> bool:
        allowed_jobs = TEAM_MANUAL_EFFECT_JOB_LIMITS.get(self._team_effect_skill_name(skill))
        if not allowed_jobs:
            return True
        provider_job = self._team_provider_job(provider) or norm(self.second_job.currentText())
        return provider_job in allowed_jobs

    def _team_effect_attrs(self, skill: str, level: Any, provider: str = "", include_personal_buffs: bool = False) -> dict[str, float]:
        raw_label = norm(skill)
        text = self._team_effect_skill_name(skill)
        combat_level = self._team_combat_skill_level(text, provider)
        if combat_level is not None and combat_level <= 0:
            return {}
        is_personal_label = "个人buff" in raw_label.split("｜")
        lookup_skill = raw_label if is_personal_label else text
        if text in TEAM_MANUAL_DEBUFFS and not is_personal_label:
            if not self._team_manual_effect_applies(text, provider):
                return {}
            out = self._buff_attrs_for_team_effect(
                text, level, include_personal_buffs, provider
            )
            parsed = self._team_effect_attrs_from_desc(provider, text, level)
            if parsed:
                out.update(parsed)
            attrs, _levels = TEAM_MANUAL_DEBUFFS[text]
            for attr, value in attrs.items():
                out.setdefault(attr, as_number(level) if abs(as_number(value)) < 1e-9 else as_number(value))
            return out
        out = self._buff_attrs_for_team_effect(
            lookup_skill, level, include_personal_buffs, provider
        )
        if not is_personal_label:
            parsed = self._team_effect_attrs_from_desc(provider, text, level)
            for attr, value in parsed.items():
                out.setdefault(attr, value)
        return out

    def _team_effects_with_auto_backups(self, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hasattr(self, "team_member_table") or not effects:
            return effects
        selected_skills = {self._team_effect_skill_name(effect.get("skill")) for effect in effects}
        existing = {
            (norm(effect.get("provider")), self._team_effect_skill_name(effect.get("skill")))
            for effect in effects
        }
        rows = list(effects)
        for member_row in range(TEAM_MEMBER_ROWS):
            job = self.combo_text(self.team_member_table, member_row, 1)
            if not job or job == "无":
                continue
            provider = f"队员{member_row + 1} {job}"
            team_skills = self._team_skills_for_job(job)
            for skill in team_skills:
                skill_name = self._team_effect_skill_name(skill)
                if skill_name not in selected_skills or (provider, skill_name) in existing:
                    continue
                levels = self._team_effect_levels(skill_name, provider)
                level = self._team_effect_default_level(skill_name, provider, levels)
                attrs = self._team_effect_attrs(skill_name, level, provider)
                if not attrs:
                    continue
                timing = self._team_effect_timing(provider, skill_name, level)
                cooldown = as_number(timing.get("cooldown"))
                duration = as_number(timing.get("duration"))
                if self._team_effect_coverage(cooldown, duration) < 1.0 - 1e-9:
                    continue
                rows.append({
                    "row": None,
                    "provider": provider,
                    "skill": self._team_manual_effect_label(skill_name) if skill_name in TEAM_MANUAL_DEBUFFS else skill_name,
                    "level": level,
                    "attrs": attrs,
                    "cooldown": cooldown,
                    "duration": duration,
                    "usage_ratio": self._team_effect_usage_default(skill_name),
                    "coverage": self._team_effect_coverage(cooldown, duration),
                    "effective_attrs": self._team_effective_attrs(attrs, cooldown, duration),
                    "manual": skill_name in TEAM_MANUAL_DEBUFFS,
                    "virtual_backup": True,
                })
                existing.add((provider, skill_name))
        return rows

    def _team_effect_attrs_from_desc(self, provider: str, skill: str, level: Any) -> dict[str, float]:
        timing = self._team_effect_timing(provider, skill, level)
        desc = norm(timing.get("desc"))
        attrs: dict[str, float] = {}
        if not desc:
            return attrs
        element_alias = {"火": "火", "冰": "水", "水": "水", "光": "光", "暗": "暗"}
        attr_by_element = {"火": "减火抗%", "水": "减水抗%", "光": "减光抗%", "暗": "减暗抗%"}
        for sentence in re.split(r"[。；\n|]", desc):
            if not any(word in sentence for word in ("降低", "减少", "下降")):
                continue
            if "所有" in sentence and "抵抗" in sentence:
                match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", sentence)
                if match:
                    attrs["减全抗%"] = max(attrs.get("减全抗%", 0.0), as_number(match.group(1)))
                continue
            if "抗" not in sentence and "抵抗" not in sentence:
                continue
            for raw_element, element in element_alias.items():
                amount = self._resist_percent_from_sentence(sentence, raw_element)
                if amount:
                    attr = attr_by_element[element]
                    attrs[attr] = max(attrs.get(attr, 0.0), amount)
        if "冷却时间" in desc and "减少" in desc:
            values = [as_number(value) for value in re.findall(r"(?:技能)?冷却时间(?:额外)?减少\s*([0-9]+(?:\.[0-9]+)?)\s*%", desc)]
            if values:
                attrs["冷却缩减%"] = max(values)
        if ("动作速度" in desc or "攻击速度" in desc) and ("提升" in desc or "增加" in desc):
            values = [as_number(value) for value in re.findall(r"(?:动作速度|攻击速度)[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)\s*%", desc)]
            if values:
                attrs["动作速度%"] = max(values)
        stat_match = re.search(r"力量[、,，]\s*敏捷[、,，]\s*智力(?:增加|提升)\s*([0-9]+(?:\.[0-9]+)?)\s*%", desc)
        if stat_match:
            value = as_number(stat_match.group(1))
            attrs["力量%"] = value
            attrs["敏捷%"] = value
            attrs["智力%"] = value
        phys_values = [as_number(value) for value in re.findall(r"(?:提高|增加|提升)\s*物理攻击力\s*([0-9]+(?:\.[0-9]+)?)\s*%", desc)]
        if phys_values:
            attrs["物攻%"] = max(phys_values)
        magic_values = [as_number(value) for value in re.findall(r"(?:提高|增加|提升)\s*魔法攻击力\s*([0-9]+(?:\.[0-9]+)?)\s*%", desc)]
        if magic_values:
            attrs["魔攻%"] = max(magic_values)
        light_values = [as_number(value) for value in re.findall(r"(?:提高|增加|提升)?\s*光属性攻击力(?:增加|提高|提升)?\s*([0-9]+(?:\.[0-9]+)?)\s*%", desc)]
        if light_values:
            attrs["光攻%"] = max(light_values)
        defaults = TEAM_MANUAL_DEBUFFS.get(self._team_effect_skill_name(skill), ({}, []))[0]
        if not attrs:
            return {attr: as_number(value) for attr, value in defaults.items()}
        for attr, value in defaults.items():
            attrs.setdefault(attr, as_number(value))
        return attrs

    @classmethod
    def _team_effect_duration_from_desc(cls, skill: str, desc: str) -> float:
        text = norm(desc)
        if not text:
            return 0.0
        attrs = TEAM_MANUAL_DEBUFFS.get(cls._team_effect_skill_name(skill), ({}, []))[0]
        effect_sentences = []
        for sentence in re.split(r"[。；\n|]", text):
            sentence = sentence.strip()
            if not sentence:
                continue
            for attr in attrs:
                key = norm(attr)
                if key.startswith("减") and "抗" in key and "抗" in sentence and any(word in sentence for word in ("降低", "减少", "下降")):
                    effect_sentences.append(sentence)
                elif key in {"减防御%", "减防御力%"} and "防御" in sentence and any(word in sentence for word in ("降低", "减少", "下降")):
                    effect_sentences.append(sentence)
                elif key in {"减致命抵抗%", "减致命一击抵抗%"} and "致命" in sentence and any(word in sentence for word in ("降低", "减少", "下降", "增加")):
                    effect_sentences.append(sentence)
                elif "增伤" in key and "伤害" in sentence and ("增加" in sentence or "受到" in sentence or "所受" in sentence):
                    effect_sentences.append(sentence)
                elif key == "冷却缩减%" and "冷却" in sentence:
                    effect_sentences.append(sentence)
                elif "冰冻" in key and any(word in sentence for word in ("冰冻", "冻结", "结冰", "冻僵")):
                    effect_sentences.append(sentence)
        for sentence in effect_sentences:
            value = cls._freeze_duration_from_text(sentence) if any(word in sentence for word in ("冰冻", "冻结", "结冰", "冻僵")) else cls._duration_from_text(sentence)
            if value > 0:
                return value
        return 0.0

    @staticmethod
    def _team_effect_option_for_skill(options: list[str], skill: str) -> str:
        selected = BuildEditor._team_effect_skill_name(skill)
        if not selected:
            return "无"
        for option in options:
            if norm(option) == selected:
                return option
        for option in options:
            tail = BuildEditor._team_effect_skill_name(option)
            if tail == selected:
                return option
        return BuildEditor._team_manual_effect_label(selected) if selected in TEAM_MANUAL_DEBUFFS else "无"

    def _team_member_changed(self, _row: int, _text: str = "") -> None:
        if not hasattr(self, "team_effect_table"):
            self.schedule_refresh()
            return
        self._request_team_effect_auto_sort()
        self._auto_populate_team_effects_from_members()

    def clear_team_sim(self) -> None:
        if not hasattr(self, "team_member_table") or not hasattr(self, "team_effect_table"):
            return
        self.team_member_table.blockSignals(True)
        for row in range(self.team_member_table.rowCount()):
            widget = self.team_member_table.cellWidget(row, 1)
            if isinstance(widget, Combo):
                widget.blockSignals(True)
                widget.setCurrentText("无")
                widget.blockSignals(False)
        self.team_member_table.blockSignals(False)
        self.team_effect_table.blockSignals(True)
        provider_options = self._team_provider_options()
        for row in range(self.team_effect_table.rowCount()):
            enabled = self.team_effect_table.item(row, 0)
            if enabled:
                enabled.setCheckState(Qt.Unchecked)
            provider = self.team_effect_table.cellWidget(row, 1)
            if isinstance(provider, Combo):
                provider.blockSignals(True)
                self._replace_combo_values(provider, provider_options, "通用")
                provider.blockSignals(False)
            skill = self.team_effect_table.cellWidget(row, 2)
            if isinstance(skill, Combo):
                skill.blockSignals(True)
                self._replace_combo_values(skill, self._team_effect_options("通用"), "无")
                skill.blockSignals(False)
            level = self.team_effect_table.cellWidget(row, 3)
            if isinstance(level, Combo):
                level.blockSignals(True)
                self._replace_combo_values(level, ["无"], "无")
                level.blockSignals(False)
            for col in (4, 5):
                spin = self.team_effect_table.cellWidget(row, col)
                if isinstance(spin, QDoubleSpinBox):
                    spin.blockSignals(True)
                    if col == 4:
                        spin.setProperty("base_cooldown", 0.0)
                    spin.setValue(0)
                    spin.blockSignals(False)
            for col in (6, 7, 8):
                self._set_summary_item(self.team_effect_table, row, col, "")
        self.team_effect_table.blockSignals(False)
        if hasattr(self, "team_cdr_usage_spins"):
            for skill, spin in self.team_cdr_usage_spins.items():
                spin.blockSignals(True)
                spin.setValue(self._team_effect_usage_default(skill))
                spin.blockSignals(False)
        if hasattr(self, "team_cdr_apply_checks"):
            for check in self.team_cdr_apply_checks.values():
                check.blockSignals(True)
                check.setChecked(False)
                check.blockSignals(False)
        if hasattr(self, "team_freeze_coeff") and isinstance(self.team_freeze_coeff, QDoubleSpinBox):
            self.team_freeze_coeff.blockSignals(True)
            self.team_freeze_coeff.setValue(DEFAULT_FREEZE_COEFFICIENT)
            self.team_freeze_coeff.blockSignals(False)
        self._refresh_team_cdr_usage_visibility()
        self.schedule_refresh()

    def _team_effect_row_states(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not hasattr(self, "team_effect_table"):
            return rows
        for row in range(self.team_effect_table.rowCount()):
            enabled = self.team_effect_table.item(row, 0)
            rows.append({
                "enabled": enabled.checkState() == Qt.Checked if enabled else False,
                "provider": self.combo_text(self.team_effect_table, row, 1) or "通用",
                "skill": self.combo_text(self.team_effect_table, row, 2) or "无",
                "level": self.combo_text(self.team_effect_table, row, 3) or "无",
                "cooldown": self._team_cooldown_base(row),
                "duration": self.double_value(self.team_effect_table, row, 5),
            })
        return rows

    @staticmethod
    def _team_effect_row_signature(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            bool(row.get("enabled")),
            norm(row.get("provider")),
            norm(row.get("skill")),
            norm(row.get("level")),
            round(as_number(row.get("cooldown")), 4),
            round(as_number(row.get("duration")), 4),
        )

    @staticmethod
    def _team_provider_sort_index(provider: str) -> int:
        match = re.match(r"队员(\d+)", norm(provider))
        if match:
            return int(match.group(1))
        return 900

    def _team_effect_provider_changed(self, row: int, _text: str = "", schedule: bool = True) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        provider = self.combo_text(self.team_effect_table, row, 1)
        skill_combo = self.team_effect_table.cellWidget(row, 2)
        if isinstance(skill_combo, Combo):
            self._replace_combo_values(skill_combo, self._team_effect_options(provider), skill_combo.currentText() or "无")
        self._team_effect_skill_changed(row, schedule=schedule)

    def _team_effect_skill_changed(self, row: int, _text: str = "", schedule: bool = True) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        skill = self.combo_text(self.team_effect_table, row, 2)
        provider = self.combo_text(self.team_effect_table, row, 1)
        level_combo = self.team_effect_table.cellWidget(row, 3)
        if isinstance(level_combo, Combo):
            levels = self._team_effect_levels(skill, provider)
            current = level_combo.currentText()
            if not current or current == "无" or current not in set(str(v) for v in levels):
                current = self._team_effect_default_level(skill, provider, levels)
            self._replace_combo_values(level_combo, levels, current)
        level_text = self.combo_text(self.team_effect_table, row, 3)
        timing = self._team_effect_timing(provider, skill, level_text)
        for col, key in ((4, "cooldown"), (5, "duration")):
            widget = self.team_effect_table.cellWidget(row, col)
            if isinstance(widget, QDoubleSpinBox):
                widget.blockSignals(True)
                value = float(as_number(timing.get(key, 0)))
                if col == 4:
                    widget.setProperty("base_cooldown", value)
                widget.setValue(value)
                widget.blockSignals(False)
        enabled = self.team_effect_table.item(row, 0)
        if enabled and skill and skill != "无" and enabled.checkState() != Qt.Checked:
            enabled.setCheckState(Qt.Checked)
        self._refresh_team_cdr_usage_visibility()
        if schedule:
            self._request_team_effect_auto_sort()
            self.schedule_refresh()

    def _team_effect_level_changed(self, row: int, _text: str = "", schedule: bool = True) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        skill = self.combo_text(self.team_effect_table, row, 2)
        provider = self.combo_text(self.team_effect_table, row, 1)
        level_text = self.combo_text(self.team_effect_table, row, 3)
        timing = self._team_effect_timing(provider, skill, level_text)
        cooldown = self.team_effect_table.cellWidget(row, 4)
        if isinstance(cooldown, QDoubleSpinBox):
            base_cooldown = float(as_number(timing.get("cooldown", 0)))
            cooldown.blockSignals(True)
            cooldown.setProperty("base_cooldown", base_cooldown)
            cooldown.setValue(base_cooldown)
            cooldown.blockSignals(False)
        duration = self.team_effect_table.cellWidget(row, 5)
        if isinstance(duration, QDoubleSpinBox):
            duration.blockSignals(True)
            duration.setValue(float(as_number(timing.get("duration", 0))))
            duration.blockSignals(False)
        if schedule:
            self._request_team_effect_auto_sort()
            self.schedule_refresh()

    def _team_effect_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self.schedule_refresh()

    @staticmethod
    def _team_detail_cell_text(table: QTableWidget, row: int, column: int) -> str:
        widget = table.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            return norm(widget.currentText())
        if isinstance(widget, QDoubleSpinBox):
            return f"{widget.value():g}"
        if isinstance(widget, QSpinBox):
            return str(widget.value())
        if isinstance(widget, QCheckBox):
            return "启用" if widget.isChecked() else "未启用"
        item = table.item(row, column)
        return norm(item.text()) if item is not None else ""

    def _team_detail_text_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        widths: list[int],
        stretch_column: int | None = None,
    ) -> AppTableWidget:
        table = AppTableWidget(len(rows), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._configure_table(table, widths)
        if stretch_column is not None:
            table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.Stretch)
        for row_index, values in enumerate(rows):
            table.setRowHeight(row_index, EDIT_ROW_HEIGHT)
            for column, value in enumerate(values):
                self._set_summary_item(table, row_index, column, value)
        return table

    def _sync_team_practical_window_from_skill(self, value: float) -> None:
        spin = getattr(self, "team_practical_window_spin", None)
        if isinstance(spin, QDoubleSpinBox):
            spin.blockSignals(True)
            spin.setValue(max(30.0, min(600.0, as_number(value, 180.0))))
            spin.blockSignals(False)
        self.schedule_refresh()

    def _team_duplicate_build_buff_names(
        self,
        effects: list[dict[str, Any]],
        build: dict[str, Any] | None = None,
    ) -> set[str]:
        source_build = build if isinstance(build, dict) else self.collect_build()
        build_buff_names = {
            self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill")))
            for item in source_build.get("buffs", [])
            if isinstance(item, dict) and self._team_effect_skill_name(item.get("skill"))
        }
        team_buff_names = {
            self._skill_burst_skill_key(self._team_effect_skill_name(effect.get("skill")))
            for effect in effects
            if self._team_effect_skill_name(effect.get("skill"))
        }
        return build_buff_names & team_buff_names

    def update_team_sim_preview(self, base_summary: dict[str, float]) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        if not getattr(self, "_team_sim_ui_active", True):
            return
        raw_base_effects = self._team_effects_with_auto_backups(
            self._selected_team_effects(use_base_cooldowns=True, dedup=False)
        )
        applied_cdr_skills = self._team_applied_cdr_skills()
        raw_base_effects, _aura_choices = self._best_team_aura_effects(raw_base_effects, applied_cdr_skills)
        base_effects = self._dedup_team_effects(raw_base_effects)
        self._sync_team_effect_cooldowns(base_effects, applied_cdr_skills)
        effects = self._team_effects_with_cdr_adjusted_cooldowns(base_effects, applied_cdr_skills)
        duplicate_build_buffs = self._team_duplicate_build_buff_names(effects, self.build)
        self._team_static_duplicate_build_buffs = set(duplicate_build_buffs)
        if duplicate_build_buffs:
            base_build = copy.deepcopy(self.build)
            base_build["buffs"] = [
                item
                for item in base_build.get("buffs", [])
                if self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill")))
                not in duplicate_build_buffs
            ]
            _base_rows, base_summary = self.calculator.calculate(base_build)
            base_summary = self._team_apply_current_mixed_element_panel(dict(base_summary), {}, base_build)
        else:
            base_summary = self._team_apply_current_mixed_element_panel(dict(base_summary), {}, self.build)
        base_effective = as_number(base_summary.get("effective_panel"))
        current_provider = ""
        current_job = norm(self.second_job.currentText())
        for member_row in range(TEAM_MEMBER_ROWS):
            member_job = self.combo_text(self.team_member_table, member_row, 1)
            if self._team_real_job(member_job) == current_job:
                current_provider = f"队员{member_row + 1} {member_job}"
                break
        personal_effects = [
            effect
            for effect in effects
            if current_provider and norm(effect.get("provider")) == norm(current_provider)
        ]
        personal_summary, _personal_attrs = self._team_summary_with_effects(personal_effects)
        personal_effective = as_number(personal_summary.get("effective_panel")) if personal_effects else base_effective
        full_summary, full_attrs = self._team_summary_with_effects(effects)
        full_effective = as_number(full_summary.get("effective_panel"))
        gain = full_effective - personal_effective
        gain_percent = gain / personal_effective * 100 if personal_effective > 0 else 0.0
        resist_bonus = self._team_resist_reduction_value(full_attrs)
        cdr_total = max(0.0, min(70.0, display_value(full_attrs, "冷却缩减%")))
        cdr_gain = as_number(full_summary.get("冷却缩减收益%"))
        freeze_gain = as_number(full_summary.get("叠冰增伤%"))
        freeze_layers = as_number(full_summary.get("平均冰冻层数"))
        amp_phys, amp_magic = amplification_channels(full_attrs)
        defense_phys, defense_magic = defense_reduction_channels(full_attrs)
        crit_resist_down = max(0.0, display_value(full_attrs, "减致命抵抗%"))
        summary_rows = [
            ("基础等效面板", number_text(base_effective, integer=True)),
            ("个人Buff后静态面板", number_text(personal_effective, integer=True)),
            ("队伍后等效面板", number_text(full_effective, integer=True)),
            ("静态队友提升面板", number_text(gain, integer=True)),
            ("静态队友提升%", f"{gain_percent:.2f}%"),
            ("减抗合计", f"{resist_bonus:.1f}%"),
            ("减防合计", f"物 {defense_phys:.1f}%，魔 {defense_magic:.1f}%"),
            ("减致命抵抗", f"{crit_resist_down:.1f}%"),
            ("-CD合计", f"{cdr_total:.1f}%"),
            ("-CD收益", f"{cdr_gain:.2f}%"),
            ("增伤合计", f"物 {amp_phys:.1f}%，魔 {amp_magic:.1f}%"),
            ("叠冰增伤", f"{freeze_gain:.2f}%（{freeze_layers:.2f}层）"),
            ("已启用效果", str(len(effects))),
            ("队伍职业数", str(sum(1 for row in range(TEAM_MEMBER_ROWS) if self.combo_text(self.team_member_table, row, 1) != "无"))),
        ]
        for row, (name, value) in enumerate(summary_rows):
            self.team_summary_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self.team_summary_table.setItem(row, 0, self._item(name))
            self._set_summary_item(self.team_summary_table, row, 1, value, align_right=True)
            if row in {3, 4}:
                tooltip = (
                    "静态理论值按各 Buff 的平均覆盖率与理想冷却收益合并，不扣除施法时间、空转和技能优先级冲突；"
                    "可实战提升请以“个人配队实战模拟”的时间轴结果为准。"
                )
                self.team_summary_table.item(row, 0).setToolTip(tooltip)
                self.team_summary_table.item(row, 1).setToolTip(tooltip)

        resist_allocations = self._team_resist_allocations(effects)
        amp_allocations = self._team_amp_contribution_allocations(effects, full_effective)
        freeze_allocations = self._team_freeze_contribution_allocations(effects, full_effective)
        non_resist_effects = self._team_effects_without_resist(effects)
        non_resist_summary: dict[str, float] = {}
        non_resist_effective = 0.0
        if resist_allocations:
            non_resist_summary, _non_resist_attrs = self._team_summary_with_effects(non_resist_effects)
            non_resist_effective = as_number(non_resist_summary.get("effective_panel"))

        contribution_rows: list[dict[str, Any]] = []
        for effect in effects:
            key = self._team_effect_key(effect)
            allocated_resist_attrs = resist_allocations.get(key, {})
            if allocated_resist_attrs:
                synthetic_effect = {
                    "row": effect.get("row"),
                    "provider": effect.get("provider"),
                    "skill": effect.get("skill"),
                    "level": effect.get("level"),
                    "attrs": allocated_resist_attrs,
                    "effective_attrs": allocated_resist_attrs,
                    "cooldown": 0,
                    "duration": 0,
                    "usage_ratio": 100,
                }
                with_allocated_summary, _allocated_attrs = self._team_summary_with_effects(non_resist_effects + [synthetic_effect])
                contribution = as_number(with_allocated_summary.get("effective_panel")) - non_resist_effective
            elif key in amp_allocations:
                contribution = amp_allocations[key]
            elif key in freeze_allocations:
                contribution = freeze_allocations[key]
            else:
                base_without = [item for item in base_effects if self._team_effect_key(item) != key]
                without = self._team_effects_with_cdr_adjusted_cooldowns(base_without, applied_cdr_skills)
                without_summary, _without_attrs = self._team_summary_with_effects(without)
                before_effective = as_number(without_summary.get("effective_panel"))
                contribution = full_effective - before_effective
            share_effects = self._team_contribution_share_effects(raw_base_effects, effect)
            contribution_share = contribution / max(1, len(share_effects))
            for share_effect in share_effects:
                share_key = self._team_effect_key(share_effect)
                loss_base_without = self._dedup_team_effects([
                    item for item in raw_base_effects
                    if self._team_effect_key(item) != share_key
                ])
                loss_without = self._team_effects_with_cdr_adjusted_cooldowns(loss_base_without, applied_cdr_skills)
                loss_summary, _loss_attrs = self._team_summary_with_effects(loss_without)
                loss_panel = full_effective - as_number(loss_summary.get("effective_panel"))
                contribution_percent = contribution_share / base_effective * 100 if base_effective > 0 else 0.0
                loss_percent = loss_panel / full_effective * 100 if full_effective > 0 else 0.0
                contribution_rows.append({
                    **share_effect,
                    "contribution": contribution_share,
                    "contribution_percent": contribution_percent,
                    "loss_percent": loss_percent,
                    "loss_panel": loss_panel,
                    "shared_contribution": len(share_effects) > 1,
                })
        self._last_team_contribution_rows = contribution_rows
        if getattr(self, "_team_auto_sort_after_refresh", False):
            self._team_auto_sort_after_refresh = False
            if self._sort_team_effect_table_by_contribution(contribution_rows):
                self.schedule_refresh()
                return

        provider_rows: list[dict[str, Any]] = []
        contribution_by_provider: dict[str, float] = {}
        attrs_by_provider: dict[str, dict[str, float]] = {}
        for row in contribution_rows:
            provider = norm(row.get("provider"))
            contribution_by_provider[provider] = contribution_by_provider.get(provider, 0.0) + as_number(row.get("contribution"))
            attrs_by_provider.setdefault(provider, {})
            self._merge_attrs(attrs_by_provider[provider], row.get("effective_attrs", row.get("attrs", {})))
        for provider in unique(effect.get("provider", "") for effect in raw_base_effects):
            loss_base_without = self._dedup_team_effects([effect for effect in raw_base_effects if effect.get("provider") != provider])
            loss_without = self._team_effects_with_cdr_adjusted_cooldowns(loss_base_without, applied_cdr_skills)
            loss_summary, _loss_attrs = self._team_summary_with_effects(loss_without)
            loss_panel = full_effective - as_number(loss_summary.get("effective_panel"))
            provider_key = norm(provider)
            contribution = contribution_by_provider.get(provider_key, 0.0)
            provider_rows.append({
                "provider": provider,
                "skill": "职业合计",
                "level": "",
                "attrs": attrs_by_provider.get(provider_key, {}),
                "contribution": contribution,
                "contribution_percent": contribution / base_effective * 100 if base_effective > 0 else 0.0,
                "loss_percent": loss_panel / full_effective * 100 if full_effective > 0 else 0.0,
                "loss_panel": loss_panel,
            })
        for row in provider_rows:
            row["display_group"] = 0
        for row in contribution_rows:
            row["display_group"] = 1
        display_rows = sorted(
            provider_rows + contribution_rows,
            key=lambda item: (
                as_number(item.get("contribution")) <= 1e-9,
                as_number(item.get("display_group"), 1),
                -as_number(item.get("contribution")),
            ),
        )
        self.team_contribution_table.setRowCount(max(1, len(display_rows)))
        if not display_rows:
            display_rows = [{"provider": "", "skill": "无", "level": "", "attrs": {}, "contribution": 0.0, "contribution_percent": 0.0, "loss_percent": 0.0, "loss_panel": 0.0}]
        for row, effect in enumerate(display_rows):
            self.team_contribution_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self.team_contribution_table.setItem(row, 0, self._item(effect.get("provider", "")))
            self.team_contribution_table.setItem(row, 1, self._item(effect.get("skill", "")))
            self._set_summary_item(self.team_contribution_table, row, 2, f"{effect.get('contribution_percent', 0.0):.2f}%", align_right=True)
            self._set_summary_item(self.team_contribution_table, row, 3, number_text(effect.get("contribution", 0.0), integer=True), align_right=True)
            loss_text = f"{effect.get('loss_percent', 0.0):.2f}% / {number_text(effect.get('loss_panel', 0.0), integer=True)}"
            self._set_summary_item(self.team_contribution_table, row, 4, loss_text, align_right=True)
            self._set_summary_item(self.team_contribution_table, row, 5, attr_summary(self._team_display_attrs(effect.get("attrs", {})), limit=999))

        row_contributions = {(effect.get("row"), effect.get("skill"), effect.get("level")): effect for effect in contribution_rows}
        for row in range(self.team_effect_table.rowCount()):
            skill = self.combo_text(self.team_effect_table, row, 2)
            level = self.combo_text(self.team_effect_table, row, 3)
            provider = self.combo_text(self.team_effect_table, row, 1)
            attrs = self._team_effect_attrs(skill, level, provider)
            cooldown = self.double_value(self.team_effect_table, row, 4)
            duration = self.double_value(self.team_effect_table, row, 5)
            coverage = self._team_effect_coverage(cooldown, duration)
            effective_attrs = self._team_effective_attrs(attrs, cooldown, duration)
            summary_text = attr_summary(self._team_display_attrs(attrs), limit=999)
            skill_tail = self._team_effect_skill_name(skill)
            if skill_tail in TEAM_CDR_USAGE_SKILLS and "冷却缩减%" in attrs:
                usage_ratio = self._team_cdr_usage_ratio(skill_tail)
                summary_text = f"{summary_text}；技能使用 {usage_ratio:.0f}%"
            self._set_summary_item(self.team_effect_table, row, 6, summary_text)
            detail = norm(self._team_effect_timing(provider, skill, level).get("desc"))
            if detail and self.team_effect_table.item(row, 6):
                self.team_effect_table.item(row, 6).setToolTip(detail)
            item = row_contributions.get((row, skill, level))
            if item:
                text = f"{item.get('contribution_percent', 0.0):.2f}% / {number_text(item.get('contribution', 0.0), integer=True)}"
                loss_text = f"{item.get('loss_percent', 0.0):.2f}% / {number_text(item.get('loss_panel', 0.0), integer=True)}"
            else:
                text = ""
                loss_text = ""
            self._set_summary_item(self.team_effect_table, row, 7, text, align_right=True)
            self._set_summary_item(self.team_effect_table, row, 8, loss_text, align_right=True)

    def _selected_team_effects(self, use_base_cooldowns: bool = False, dedup: bool = True) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        if not hasattr(self, "team_effect_table"):
            return effects
        for row in range(self.team_effect_table.rowCount()):
            enabled = self.team_effect_table.item(row, 0)
            if enabled is not None and enabled.checkState() != Qt.Checked:
                continue
            skill = self.combo_text(self.team_effect_table, row, 2)
            level = self.combo_text(self.team_effect_table, row, 3)
            if not skill or skill == "无" or not level or level == "无":
                continue
            provider = self.combo_text(self.team_effect_table, row, 1)
            attrs = self._team_effect_attrs(skill, level, provider)
            if not attrs:
                continue
            base_cooldown = self._team_cooldown_base(row)
            if use_base_cooldowns:
                cooldown = base_cooldown * self._team_cdr_glyph_factor(skill)
            else:
                cooldown = self.double_value(self.team_effect_table, row, 4)
            duration = self.double_value(self.team_effect_table, row, 5)
            usage_ratio = self._team_cdr_usage_ratio(skill)
            effects.append({
                "row": row,
                "provider": provider,
                "skill": skill,
                "level": level,
                "attrs": attrs,
                "cooldown": cooldown,
                "duration": duration,
                "usage_ratio": usage_ratio,
                "coverage": self._team_effect_coverage(cooldown, duration),
                "effective_attrs": self._team_effective_attrs(attrs, cooldown, duration),
                "manual": self._team_effect_skill_name(skill) in TEAM_MANUAL_DEBUFFS,
            })
        return self._dedup_team_effects(effects) if dedup else effects

    @classmethod
    def _team_effects_with_cdr_adjusted_cooldowns(cls, effects: list[dict[str, Any]], applied_skills: set[str] | None = None) -> list[dict[str, Any]]:
        applied = applied_skills or set()
        out: list[dict[str, Any]] = []
        adjusted_cooldowns: dict[float, float] = {}
        for effect in effects:
            attrs = effect.get("attrs", {})
            cooldown = as_number(effect.get("cooldown"))
            if not cls._team_effect_has_cdr(attrs):
                cooldown_key = round(cooldown, 6)
                if cooldown_key not in adjusted_cooldowns:
                    adjusted_cooldowns[cooldown_key] = cls._team_cdr_adjusted_cooldown(
                        cooldown, effects, applied
                    )
                cooldown = adjusted_cooldowns[cooldown_key]
            copied = {**effect, "cooldown": cooldown}
            copied["coverage"] = cls._team_effect_coverage(cooldown, copied.get("duration", 0))
            copied["effective_attrs"] = cls._team_effective_attrs(attrs, cooldown, copied.get("duration", 0))
            out.append(copied)
        return out

    @staticmethod
    def _team_effect_has_cdr(attrs: dict[str, Any]) -> bool:
        return as_number(attrs.get("冷却缩减%")) > 0

    def _sync_team_effect_cooldowns(self, effects: list[dict[str, Any]] | None = None, applied_skills: set[str] | None = None) -> None:
        if not hasattr(self, "team_effect_table"):
            return
        current_effects = effects or []
        applied = applied_skills or set()
        self._syncing_team_cooldowns = True
        try:
            for row in range(self.team_effect_table.rowCount()):
                widget = self.team_effect_table.cellWidget(row, 4)
                if not isinstance(widget, QDoubleSpinBox):
                    continue
                skill = self.combo_text(self.team_effect_table, row, 2)
                skill_name = self._team_effect_skill_name(skill)
                base = self._team_cooldown_base(row)
                if skill_name in TEAM_CDR_USAGE_SKILLS:
                    display = base * self._team_cdr_glyph_factor(skill_name)
                else:
                    display = self._team_cdr_adjusted_cooldown(base, current_effects, applied)
                widget.blockSignals(True)
                widget.setValue(float(display))
                widget.blockSignals(False)
        finally:
            self._syncing_team_cooldowns = False

    @staticmethod
    def _team_effect_coverage(cooldown: float, duration: float) -> float:
        cd = as_number(cooldown)
        dur = as_number(duration)
        return BuildEditor._team_effect_coverage_cached(cd, dur)

    @staticmethod
    @lru_cache(maxsize=4096)
    def _team_effect_coverage_cached(cd: float, dur: float) -> float:
        if cd <= 0:
            return 1.0
        if dur <= 0:
            return 0.0
        return max(0.0, min(1.0, dur / cd))

    def _team_freeze_coefficient(self) -> float:
        override = getattr(self, "_team_freeze_coefficient_override", None)
        if override is not None:
            return max(0.0, as_number(override, DEFAULT_FREEZE_COEFFICIENT))
        spin = getattr(self, "team_freeze_coeff", None)
        if isinstance(spin, QDoubleSpinBox):
            return max(0.0, as_number(spin.value(), DEFAULT_FREEZE_COEFFICIENT))
        return DEFAULT_FREEZE_COEFFICIENT

    def _team_freeze_profile(self, effects: list[dict[str, Any]]) -> dict[str, float]:
        ice_spirit_coverage = 0.0
        alchemist_coverage = 0.0
        for effect in effects:
            skill = self._team_effect_skill_name(effect.get("skill"))
            group = TEAM_FREEZE_SKILL_GROUPS.get(skill)
            if not group:
                continue
            coverage = self._team_effect_coverage(effect.get("cooldown", 0), effect.get("duration", 0))
            if group == "冰灵":
                ice_spirit_coverage += coverage
            elif group == "炼金":
                alchemist_coverage += coverage
        layers = min(2.0, max(0.0, ice_spirit_coverage) + min(1.0, max(0.0, alchemist_coverage)))
        bonus_percent = layers * self._team_freeze_coefficient() / 2 * 100
        return {"layers": layers, "bonus_percent": bonus_percent, "multiplier": 1.0 + bonus_percent / 100}

    @classmethod
    def _team_effective_attrs(cls, attrs: dict[str, float], cooldown: float, duration: float) -> dict[str, float]:
        coverage = cls._team_effect_coverage(cooldown, duration)
        out: dict[str, float] = {}
        for attr, value in attrs.items():
            amount = as_number(value)
            out[attr] = amount if norm(attr) == "冷却缩减%" else amount * coverage
        return out

    @staticmethod
    def _team_display_attrs(attrs: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for attr, value in attrs.items():
            key = norm(attr)
            if key == "眩晕面板%":
                continue
            if key == "生命高于30额外系数%":
                out["奉献乌鸦平均收益%"] = as_number(value) * 24.5
                continue
            if key == "生命低于50额外系数%":
                out["九天平均收益%"] = as_number(value) * 37.5
                continue
            out[attr] = value
        return out

    @staticmethod
    def _team_resist_attr_keys() -> set[str]:
        return {"减火抗%", "减水抗%", "减光抗%", "减暗抗%", "减全抗%"}

    @staticmethod
    def _team_amp_attr_keys() -> set[str]:
        return {"增伤%", "伤害增加%", "全增伤%", "物理增伤%", "物理伤害增加%", "魔法增伤%", "魔法伤害增加%"}

    @staticmethod
    def _team_freeze_attr_keys() -> set[str]:
        return {"冰冻层"}

    @staticmethod
    def _team_effect_key(effect: dict[str, Any]) -> tuple[Any, str, str]:
        return (effect.get("row"), BuildEditor._team_effect_skill_name(effect.get("skill")), norm(effect.get("level")))

    @staticmethod
    def _team_attr_signature(attrs: dict[str, Any]) -> tuple[tuple[str, float], ...]:
        return tuple(
            sorted(
                (norm(attr), round(as_number(value), 6))
                for attr, value in attrs.items()
                if abs(as_number(value)) > 1e-9
            )
        )

    @classmethod
    def _team_contribution_share_effects(cls, raw_effects: list[dict[str, Any]], effect: dict[str, Any]) -> list[dict[str, Any]]:
        skill = cls._team_effect_skill_name(effect.get("skill"))
        if not skill or skill == "无":
            return [effect]
        if cls._team_effect_coverage(effect.get("cooldown", 0), effect.get("duration", 0)) < 1.0 - 1e-9:
            return [effect]
        signature = cls._team_attr_signature(effect.get("attrs", {}))
        rows: list[dict[str, Any]] = []
        seen_providers: set[str] = set()
        for item in raw_effects:
            if cls._team_effect_skill_name(item.get("skill")) != skill:
                continue
            if cls._team_effect_coverage(item.get("cooldown", 0), item.get("duration", 0)) < 1.0 - 1e-9:
                continue
            if cls._team_attr_signature(item.get("attrs", {})) != signature:
                continue
            provider = norm(item.get("provider"))
            if provider in seen_providers:
                continue
            seen_providers.add(provider)
            rows.append(item)
        return rows if len(rows) > 1 else [effect]

    @classmethod
    def _team_effects_without_resist(cls, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        resist_attrs = cls._team_resist_attr_keys()
        rows: list[dict[str, Any]] = []
        for effect in effects:
            attrs = {norm(attr): value for attr, value in effect.get("attrs", {}).items() if norm(attr) not in resist_attrs}
            effective_attrs = {norm(attr): value for attr, value in effect.get("effective_attrs", {}).items() if norm(attr) not in resist_attrs}
            if not attrs and not effective_attrs:
                continue
            rows.append({**effect, "attrs": attrs, "effective_attrs": effective_attrs})
        return rows

    @classmethod
    def _team_effects_without_amp(cls, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        amp_attrs = cls._team_amp_attr_keys()
        rows: list[dict[str, Any]] = []
        for effect in effects:
            attrs = {norm(attr): value for attr, value in effect.get("attrs", {}).items() if norm(attr) not in amp_attrs}
            effective_attrs = {norm(attr): value for attr, value in effect.get("effective_attrs", {}).items() if norm(attr) not in amp_attrs}
            if not attrs and not effective_attrs:
                continue
            rows.append({**effect, "attrs": attrs, "effective_attrs": effective_attrs})
        return rows

    @classmethod
    def _team_effects_without_freeze(cls, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        freeze_attrs = cls._team_freeze_attr_keys()
        rows: list[dict[str, Any]] = []
        for effect in effects:
            attrs = {norm(attr): value for attr, value in effect.get("attrs", {}).items() if norm(attr) not in freeze_attrs}
            effective_attrs = {norm(attr): value for attr, value in effect.get("effective_attrs", {}).items() if norm(attr) not in freeze_attrs}
            if not attrs and not effective_attrs:
                continue
            rows.append({**effect, "attrs": attrs, "effective_attrs": effective_attrs})
        return rows

    @classmethod
    def _team_effect_amp_only(cls, effect: dict[str, Any]) -> dict[str, Any] | None:
        amp_attrs = cls._team_amp_attr_keys()
        attrs = {norm(attr): value for attr, value in effect.get("attrs", {}).items() if norm(attr) in amp_attrs}
        if not attrs:
            return None
        cooldown = effect.get("cooldown", 0)
        duration = effect.get("duration", 0)
        return {
            **effect,
            "attrs": attrs,
            "effective_attrs": cls._team_effective_attrs(attrs, cooldown, duration),
        }

    @classmethod
    def _team_effect_freeze_only(cls, effect: dict[str, Any]) -> dict[str, Any] | None:
        freeze_attrs = cls._team_freeze_attr_keys()
        attrs = {norm(attr): value for attr, value in effect.get("attrs", {}).items() if norm(attr) in freeze_attrs}
        if not attrs:
            return None
        cooldown = effect.get("cooldown", 0)
        duration = effect.get("duration", 0)
        return {
            **effect,
            "attrs": attrs,
            "effective_attrs": cls._team_effective_attrs(attrs, cooldown, duration),
        }

    def _team_amp_contribution_allocations(self, effects: list[dict[str, Any]], full_effective: float) -> dict[tuple[Any, str, str], float]:
        amp_effects = [effect for effect in effects if self._team_effect_amp_only(effect)]
        if not amp_effects:
            return {}
        non_amp_effects = self._team_effects_without_amp(effects)
        non_amp_summary, _attrs = self._team_summary_with_effects(non_amp_effects)
        non_amp_effective = as_number(non_amp_summary.get("effective_panel"))
        total_gain = max(0.0, as_number(full_effective) - non_amp_effective)
        if total_gain <= 1e-9:
            return {}
        weights: dict[tuple[Any, str, str], float] = {}
        for effect in amp_effects:
            amp_only = self._team_effect_amp_only(effect)
            if not amp_only:
                continue
            with_one_summary, _one_attrs = self._team_summary_with_effects(non_amp_effects + [amp_only])
            weight = max(0.0, as_number(with_one_summary.get("effective_panel")) - non_amp_effective)
            if weight > 1e-9:
                weights[self._team_effect_key(effect)] = weight
        weight_total = sum(weights.values())
        if weight_total <= 1e-9:
            return {}
        return {key: total_gain * weight / weight_total for key, weight in weights.items()}

    def _team_freeze_contribution_allocations(self, effects: list[dict[str, Any]], full_effective: float) -> dict[tuple[Any, str, str], float]:
        freeze_effects = [effect for effect in effects if self._team_effect_freeze_only(effect)]
        if not freeze_effects:
            return {}
        non_freeze_effects = self._team_effects_without_freeze(effects)
        non_freeze_summary, _attrs = self._team_summary_with_effects(non_freeze_effects)
        non_freeze_effective = as_number(non_freeze_summary.get("effective_panel"))
        total_gain = max(0.0, as_number(full_effective) - non_freeze_effective)
        if total_gain <= 1e-9:
            return {}
        weights: dict[tuple[Any, str, str], float] = {}
        for effect in freeze_effects:
            freeze_only = self._team_effect_freeze_only(effect)
            if not freeze_only:
                continue
            with_one_summary, _one_attrs = self._team_summary_with_effects(non_freeze_effects + [freeze_only])
            weight = max(0.0, as_number(with_one_summary.get("effective_panel")) - non_freeze_effective)
            if weight > 1e-9:
                weights[self._team_effect_key(effect)] = weight
        weight_total = sum(weights.values())
        if weight_total <= 1e-9:
            return {}
        return {key: total_gain * weight / weight_total for key, weight in weights.items()}

    def _team_current_mixed_element_weights(self, summary: dict[str, float]) -> dict[str, float]:
        if norm(self.build.get("second_job")) != "曜":
            return {}
        base_element_percent = max(as_number(summary.get(f"{element}攻%")) for element in ("火", "水", "光", "暗"))
        previous_base = getattr(self, "_extreme_base_element_percent_value", None)
        previous_resists = getattr(self, "_extreme_element_resists", None)
        self._extreme_base_element_percent_value = base_element_percent
        self._extreme_element_resists = {"火": 0.0, "水": 0.0, "光": 0.0, "暗": 0.0}
        try:
            profile = self._extreme_mixed_element_profile("曜", summary)
        finally:
            if previous_base is None:
                with contextlib.suppress(AttributeError):
                    delattr(self, "_extreme_base_element_percent_value")
            else:
                self._extreme_base_element_percent_value = previous_base
            if previous_resists is None:
                with contextlib.suppress(AttributeError):
                    delattr(self, "_extreme_element_resists")
            else:
                self._extreme_element_resists = previous_resists
        weights = profile.get("weights", {}) if isinstance(profile, dict) else {}
        return {
            element: max(0.0, as_number(weight))
            for element, weight in weights.items()
            if element in {"光", "暗", "无"} and as_number(weight) > 1e-9
        } or {"光": 0.5, "暗": 0.5}

    def _team_apply_current_mixed_element_panel(
        self,
        summary: dict[str, float],
        team_attrs: dict[str, float],
        build: dict[str, Any],
    ) -> dict[str, float]:
        weights = self._team_current_mixed_element_weights(summary)
        if not weights:
            return summary
        element_resists = self.calculator._element_resists(build)

        def element_multiplier(element: str) -> float:
            if element not in {"火", "水", "光", "暗"}:
                return 1.0
            attack = max(0.0, as_number(summary.get(f"{element}攻%")))
            resist_down = max(0.0, display_value(team_attrs, f"减{element}抗%") + display_value(team_attrs, "减全抗%"))
            effective_resist = as_number(element_resists.get(element, 0.0)) - resist_down
            return (1 + attack / 100) * max(0.0, 1 - effective_resist / 100)

        old_multiplier = max(0.01, element_multiplier("光"))
        mixed_multiplier = sum(max(0.0, as_number(weight)) * element_multiplier(element) for element, weight in weights.items())
        weight_total = sum(max(0.0, as_number(weight)) for weight in weights.values())
        if weight_total <= 1e-9 or mixed_multiplier <= 0:
            return summary
        adjusted = dict(summary)
        adjusted["effective_panel"] = as_number(summary.get("effective_panel")) / old_multiplier * (mixed_multiplier / weight_total)
        adjusted["曜光权重%"] = as_number(weights.get("光")) * 100 / weight_total
        adjusted["曜暗权重%"] = as_number(weights.get("暗")) * 100 / weight_total
        adjusted["曜无权重%"] = as_number(weights.get("无")) * 100 / weight_total
        return adjusted

    def _team_summary_with_effects(self, effects: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, float]]:
        application_roles = self._skill_burst_effect_application_roles(effects)
        signature = tuple(
            sorted(
                (
                    self._skill_burst_skill_key(
                        self._team_effect_skill_name(effect.get("skill"))
                    ),
                    tuple(
                        sorted(
                            (norm(key), round(as_number(value), 8))
                            for key, value in effect.get("attrs", {}).items()
                            if abs(as_number(value)) > 1e-9
                        )
                    ),
                    round(as_number(effect.get("cooldown")), 6),
                    round(as_number(effect.get("duration")), 6),
                    round(as_number(effect.get("usage_ratio"), 100.0), 6),
                    application_roles[index],
                )
                for index, effect in enumerate(effects)
            )
        )
        cache = None
        if getattr(self, "_team_summary_effect_cache_active", False):
            cache = getattr(self, "_team_summary_effect_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._team_summary_effect_cache = cache
            cached = cache.get(signature)
            if cached is not None:
                cached_summary, cached_attrs = cached
                return dict(cached_summary), dict(cached_attrs)

        # Only these two top-level fields are replaced below. The remaining
        # nested build data is read-only during calculation.
        build = dict(self.build)
        buffs = list(build.get("buffs", []))
        duplicate_names = {
            self._skill_burst_skill_key(self._team_effect_skill_name(effect.get("skill")))
            for effect in effects
            if self._team_effect_skill_name(effect.get("skill"))
        }
        duplicate_names.update(
            self._skill_burst_skill_key(skill)
            for skill in getattr(self, "_team_practical_duplicate_build_buffs", set())
            if self._skill_burst_skill_key(skill)
        )
        duplicate_names.update(
            self._skill_burst_skill_key(skill)
            for skill in getattr(self, "_team_static_duplicate_build_buffs", set())
            if self._skill_burst_skill_key(skill)
        )
        if duplicate_names:
            buffs = [
                item
                for item in buffs
                if self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill")))
                not in duplicate_names
            ]
        cached_merge = getattr(
            self, "_skill_burst_cached_merged_team_attrs", None
        )
        team_attrs = (
            cached_merge(effects)
            if callable(cached_merge)
            else self._merged_team_attrs(effects)
        )
        manual_attrs = dict(team_attrs)
        manual_attrs.pop("冷却缩减%", None)
        manual_attrs.pop("动作速度%", None)
        manual_attrs.pop("冰冻层", None)
        build["buffs"] = self._dedup_build_buffs(buffs)
        build["extra_buff_attrs"] = manual_attrs
        _rows, summary = self.calculator.calculate(
            build,
            static_signature=getattr(self, "_team_summary_static_build_signature", None),
            summary_only=True,
        )
        summary = dict(summary)
        summary = self._team_apply_current_mixed_element_panel(summary, team_attrs, build)
        self._apply_team_cdr_multiplier(summary, effects, team_attrs)
        freeze_profile = self._team_freeze_profile(effects)
        summary["effective_panel"] = as_number(summary.get("effective_panel")) * freeze_profile["multiplier"]
        summary["叠冰增伤%"] = freeze_profile["bonus_percent"]
        summary["平均冰冻层数"] = freeze_profile["layers"]
        if cache is not None:
            cache[signature] = (dict(summary), dict(team_attrs))
            while len(cache) > 16384:
                cache.pop(next(iter(cache)))
        return summary, team_attrs

    @staticmethod
    def _merged_team_attrs(effects: list[dict[str, Any]]) -> dict[str, float]:
        normal_attrs: dict[str, float] = {}
        resist_attrs = {"减火抗%", "减水抗%", "减光抗%", "减暗抗%", "减全抗%"}
        non_stack_attrs = {
            "减致命抵抗%": "减致命抵抗%",
            "减致命一击抵抗%": "减致命抵抗%",
            "减暴击抵抗%": "减致命抵抗%",
        }
        grouped_attrs: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for order, effect in enumerate(effects):
            prepared = effect.get('_exact_merge_projection') if _EXACT_ROUND3 else None
            if prepared is not None and prepared[0] == id(effect):
                for group_key, item in prepared[1]:
                    grouped_attrs.setdefault(group_key, []).append({**item, 'order': order})
                continue
            prepared_rows = []
            skill = BuildEditor._team_effect_skill_name(effect.get("skill"))
            exclusive_group = BuildEditor._skill_burst_exclusive_effect_group(
                skill
            )
            attrs = effect.get("attrs", {})
            coverage = BuildEditor._team_effect_coverage(
                effect.get("cooldown", 0), effect.get("duration", 0)
            ) * team_effect_proc_chance(skill)
            for attr, raw_value in attrs.items():
                key = norm(attr)
                if key in resist_attrs or key == "冷却缩减%":
                    continue
                lane_marker = ""
                defense_key = canonical_defense_reduction_key(key)
                if defense_key:
                    attr_key = defense_key
                    resolved_rule = effect_rule(
                        EffectInput(
                            key=attr_key,
                            value=as_number(raw_value),
                            skill_name=skill,
                            stack_group=norm(effect.get("stack_group")),
                            stackable=bool(effect.get("stackable")),
                        )
                    )
                    group_key = (
                        f"__effect_{resolved_rule.stack_group}__",
                        attr_key,
                    )
                    lane_marker = effect_lane_marker(resolved_rule)
                elif (
                    exclusive_group == "游侠物理增伤"
                    and key in {"增伤%", "物理增伤%"}
                ):
                    group_key = ("__exclusive_phys_amp__", "物理增伤%")
                    attr_key = "物理增伤%"
                elif (
                    exclusive_group == "团队攻击力增益"
                    and key in {"物攻%", "魔攻%"}
                ):
                    group_key = ("__exclusive_attack_buff__", key)
                    attr_key = key
                elif key in non_stack_attrs:
                    attr_key = non_stack_attrs[key]
                    resolved_rule = effect_rule(
                        EffectInput(
                            key=attr_key,
                            value=as_number(raw_value),
                            skill_name=skill,
                            stack_group=norm(effect.get("stack_group")),
                        )
                    )
                    group_key = (
                        f"__effect_{resolved_rule.stack_group}__",
                        attr_key,
                    )
                else:
                    group_key = (skill, key)
                    attr_key = key
                item = {
                    "attr": attr_key,
                    "value": as_number(raw_value),
                    "coverage": coverage,
                    "order": order,
                    "applied_at": effect.get("applied_at"),
                    "skill": skill,
                    "lane_marker": lane_marker,
                    "runtime_effect": bool(effect.get("_skill_burst_runtime_effect")),
                }
                grouped_attrs.setdefault(group_key, []).append(item)
                prepared_rows.append((group_key, item))
            # Only private immutable runtime rows qualify. A copied row has a
            # new id and is recompiled, including changed per-cast snapshots.
            # Source order is supplied anew above; it controls replacement ties.
            if _EXACT_ROUND3 and effect.get('_skill_burst_runtime_effect'):
                effect['_exact_merge_projection'] = (id(effect), tuple(prepared_rows))
        for (group, attr), items in grouped_attrs.items():
            if group == "__exclusive_phys_amp__" and any(
                item.get("applied_at") is not None for item in items
            ):
                latest = max(
                    items,
                    key=lambda row: (
                        as_number(row.get("applied_at"), -1.0),
                        as_number(row.get("order")),
                    ),
                )
                value = as_number(latest.get("value"))
            elif group == "__exclusive_attack_buff__" and any(
                item.get("applied_at") is not None for item in items
            ):
                # 特制强化药剂明确低于无情打击：两者重叠时无条件采用无情打击。
                preferred = [item for item in items if norm(item.get("skill")) == "无情打击"]
                value = as_number((preferred or items)[-1].get("value"))
            else:
                value = BuildEditor._ordered_attr_coverage_value(items)
            if abs(value) > 1e-9:
                normal_attrs[attr] = normal_attrs.get(attr, 0.0) + value
                lane_markers = {
                    norm(item.get("lane_marker"))
                    for item in items
                    if item.get("runtime_effect") and norm(item.get("lane_marker"))
                }
                for marker in lane_markers:
                    normal_attrs[marker] = value
        for attr in ("减防御%", "减物理防御%", "减魔法防御%"):
            if attr in normal_attrs:
                normal_attrs[attr] = min(
                    DEFENSE_REDUCTION_CAP_PERCENT,
                    max(0.0, normal_attrs[attr]),
                )
        for attr in ("攻击%", "双攻%", "物攻%", "魔攻%"):
            if attr in normal_attrs:
                normal_attrs[attr] = min(100.0, max(0.0, normal_attrs[attr]))
        normal_attrs.update(BuildEditor._team_resist_attrs(effects))
        return normal_attrs

    @staticmethod
    def _ordered_attr_coverage_value(items: list[dict[str, Any]]) -> float:
        # A single source needs no ordering; keep the same clamping and
        # zero-value semantics without allocating a sorted copy.
        if len(items) == 1:
            item = items[0]
            value = as_number(item.get("value"))
            coverage = max(0.0, min(1.0, as_number(item.get("coverage"))))
            return value * coverage if value != 0 and coverage > 0 else 0.0
        remaining = 1.0
        total = 0.0
        for item in sorted(items, key=lambda row: (-as_number(row.get("value")), as_number(row.get("order")))):
            if remaining <= 1e-9:
                break
            value = as_number(item.get("value"))
            coverage = max(0.0, min(1.0, as_number(item.get("coverage"))))
            if value == 0 or coverage <= 0:
                continue
            used = min(remaining, coverage)
            total += value * used
            remaining -= used
        return total

    @staticmethod
    def _team_resist_attrs(effects: list[dict[str, Any]]) -> dict[str, float]:
        allocations = BuildEditor._team_resist_allocations(effects)
        out: dict[str, float] = {}
        for attrs in allocations.values():
            for attr, value in attrs.items():
                out[attr] = out.get(attr, 0.0) + as_number(value)
        return {attr: value for attr, value in out.items() if abs(value) > 1e-9}

    @staticmethod
    def _team_resist_allocations(effects: list[dict[str, Any]]) -> dict[tuple[Any, str, str], dict[str, float]]:
        resist_map = {
            "减火抗%": "火",
            "减水抗%": "水",
            "减光抗%": "光",
            "减暗抗%": "暗",
            "减全抗%": "全",
        }
        items: list[dict[str, Any]] = []
        for order, effect in enumerate(effects):
            prepared = effect.get('_exact_resist_projection') if _EXACT_ROUND3 else None
            if prepared is not None and prepared[0] == id(effect):
                items.extend({**item, 'order': order} for item in prepared[1])
                continue
            attrs = effect.get("attrs", {})
            resist_entries: list[tuple[str, float]] = []
            for attr, value in attrs.items():
                key = norm(attr)
                if key not in resist_map:
                    continue
                amount = max(0.0, as_number(value))
                if amount > 0:
                    resist_entries.append((key, amount))
            if not resist_entries:
                if _EXACT_ROUND3 and effect.get('_skill_burst_runtime_effect'):
                    effect['_exact_resist_projection'] = (id(effect), ())
                continue
            skill = BuildEditor._team_effect_skill_name(effect.get("skill"))
            cd = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            proc_chance = team_effect_proc_chance(skill)
            coverage = BuildEditor._team_effect_coverage(cd, duration) * proc_chance
            effect_key = BuildEditor._team_effect_key(effect)
            stackable = bool(effect.get("stackable")) or skill in {"神圣大地", "C2H5OH"}
            stack_group = norm(effect.get("stack_group"))
            prepared_rows = []
            for key, amount in resist_entries:
                item = {
                    "skill": skill,
                    "attr": key,
                    "element": resist_map[key],
                    "value": amount,
                    "cooldown": cd,
                    "duration": duration,
                    "coverage": coverage,
                    "proc_chance": proc_chance,
                    "order": order,
                    "key": effect_key,
                    "stackable": stackable,
                    "stack_group": stack_group,
                }
                if _EXACT_ROUND3:
                    rule = effect_rule(EffectInput(key=key, value=amount,
                        skill_name=skill, stack_group=stack_group, stackable=stackable))
                    item['resolved_group'] = rule.stack_group
                    item['group_mode'] = rule.group_mode
                items.append(item)
                prepared_rows.append(item)
            if _EXACT_ROUND3 and effect.get('_skill_burst_runtime_effect'):
                effect['_exact_resist_projection'] = (id(effect), tuple(prepared_rows))
        if not items:
            return {}
        allocations: dict[tuple[Any, str, str], dict[str, float]] = {}
        for item in items:
            if _EXACT_ROUND3 and 'resolved_group' in item:
                continue
            rule = effect_rule(
                EffectInput(
                    key=item["attr"],
                    value=item["value"],
                    skill_name=item["skill"],
                    stack_group=item["stack_group"],
                    stackable=item["stackable"],
                )
            )
            item["resolved_group"] = rule.stack_group
            item["group_mode"] = rule.group_mode
        attr_by_element = {"火": "减火抗%", "水": "减水抗%", "光": "减光抗%", "暗": "减暗抗%"}
        for element, attr in attr_by_element.items():
            applicable = [
                item for item in items if item["element"] in {element, "全"}
            ]
            lanes: dict[str, list[dict[str, Any]]] = {}
            for item in applicable:
                lanes.setdefault(str(item["resolved_group"]), []).append(item)
            for lane_items in lanes.values():
                if lane_items[0]["group_mode"] == "additive":
                    selected = [
                        (item, min(1.0, as_number(item["coverage"])))
                        for item in lane_items
                    ]
                else:
                    selected = BuildEditor._ordered_resist_coverage(lane_items)
                for item, used_coverage in selected:
                    BuildEditor._add_resist_allocation(
                        allocations,
                        item["key"],
                        attr,
                        item["value"] * used_coverage,
                    )
        return allocations

    @staticmethod
    def _active_resist_values(active_items: list[dict[str, Any]]) -> dict[str, float]:
        attr_by_element = {
            "火": "减火抗%",
            "水": "减水抗%",
            "光": "减光抗%",
            "暗": "减暗抗%",
            "全": "减全抗%",
        }
        inputs = [
            EffectInput(
                key=attr_by_element.get(norm(item.get("element")), ""),
                value=as_number(item.get("value")),
                skill_name=norm(item.get("skill")),
                stack_group=norm(item.get("stack_group")),
                stackable=bool(item.get("stackable"))
                or norm(item.get("skill")) in {"神圣大地", "C2H5OH"},
            )
            for item in active_items
            if norm(item.get("element")) in attr_by_element
        ]
        values = EFFECT_STACKING_ENGINE.resolve_active(inputs, 0.0).values
        all_resist = as_number(values.get("减全抗%"))
        result = {
            element: max(0.0, as_number(values.get(attr)) - all_resist)
            for element, attr in attr_by_element.items()
            if element != "全"
        }
        result["全"] = all_resist
        return result

    @classmethod
    def _apply_team_cdr_multiplier(cls, summary: dict[str, float], effects: list[dict[str, Any]], attrs: dict[str, float]) -> None:
        profile = cls._team_cdr_profile(effects)
        multiplier = profile["multiplier"]
        summary["effective_panel"] = as_number(summary.get("effective_panel")) * multiplier
        summary["冷却缩减有效%"] = profile["equivalent_cdr"]
        summary["冷却缩减收益%"] = (multiplier - 1) * 100
        summary["冷却缩减倍率"] = multiplier
        attrs["冷却缩减%"] = profile["equivalent_cdr"]
        attrs["冷却缩减平均值%"] = profile["average_cdr"]

    @staticmethod
    def _team_cdr_profile(effects: list[dict[str, Any]]) -> dict[str, float]:
        cdr_groups: dict[tuple[str, float, float], dict[str, float]] = {}
        for effect in effects:
            attrs = effect.get("attrs", {})
            cdr = max(0.0, as_number(attrs.get("冷却缩减%")))
            if cdr <= 0:
                continue
            cd = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            usage_ratio = max(0.0, min(1.0, as_number(effect.get("usage_ratio"), 100) / 100))
            skill = BuildEditor._team_effect_skill_name(effect.get("skill"))
            key = (skill, cdr, cd)
            group = cdr_groups.setdefault(key, {"cdr": cdr, "cooldown": cd, "duration": 0.0, "usage_weight": 0.0})
            if cd <= 0:
                used_duration = duration if duration > 0 else 1.0
            else:
                used_duration = min(max(0.0, duration), cd)
            group["duration"] = min(cd, group["duration"] + used_duration) if cd > 0 else max(group["duration"], used_duration)
            group["usage_weight"] += used_duration * usage_ratio
        cdr_effects = []
        for group in cdr_groups.values():
            duration = group["duration"]
            usage_ratio = group["usage_weight"] / duration if duration > 0 else 1.0
            cdr_effects.append({
                "cdr": group["cdr"],
                "cooldown": group["cooldown"],
                "duration": duration,
                "usage_ratio": max(0.0, min(1.0, usage_ratio)),
            })
        if not cdr_effects:
            return {"multiplier": 1.0, "equivalent_cdr": 0.0, "average_cdr": 0.0}
        periods = []
        for item in cdr_effects:
            cd = item["cooldown"]
            duration = item["duration"]
            if cd > 0 and 0 < duration < cd:
                periods.append(max(1, int(round(cd * 10))))
        horizon_ticks = 6000
        if periods:
            horizon_ticks = periods[0]
            for period in periods[1:]:
                horizon_ticks = horizon_ticks * period // gcd(horizon_ticks, period)
                if horizon_ticks > 36000:
                    horizon_ticks = 6000
                    break
        boundaries = {0, horizon_ticks}
        periodic_items = []
        always_cdr = 0.0
        always_usage_values: list[tuple[float, float]] = []
        for item in cdr_effects:
            cd_ticks = int(round(item["cooldown"] * 10))
            dur_ticks = int(round(item["duration"] * 10))
            if cd_ticks <= 0:
                always_cdr += item["cdr"]
                always_usage_values.append((item["cdr"], item["usage_ratio"]))
                continue
            if dur_ticks <= 0:
                continue
            if dur_ticks >= cd_ticks:
                always_cdr += item["cdr"]
                always_usage_values.append((item["cdr"], item["usage_ratio"]))
                continue
            periodic_items.append((item["cdr"], cd_ticks, dur_ticks, item["usage_ratio"]))
            start = 0
            while start < horizon_ticks:
                boundaries.add(start)
                boundaries.add(min(horizon_ticks, start + dur_ticks))
                start += cd_ticks
        ordered = sorted(boundaries)
        weighted_multiplier = 0.0
        weighted_cdr = 0.0
        total_ticks = 0
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            middle = (start + end) / 2
            cdr = always_cdr
            active_usage_values = list(always_usage_values)
            for value, cd_ticks, dur_ticks, usage_ratio in periodic_items:
                if middle % cd_ticks < dur_ticks:
                    cdr += value
                    active_usage_values.append((value, usage_ratio))
            capped = max(0.0, min(70.0, cdr))
            if cdr > 0 and active_usage_values:
                usage_ratio = sum(value * ratio for value, ratio in active_usage_values) / cdr
            else:
                usage_ratio = 1.0
            ticks = end - start
            ideal_multiplier = 1 / max(0.3, 1 - capped / 100)
            weighted_multiplier += ticks * (1 + (ideal_multiplier - 1) * usage_ratio)
            weighted_cdr += ticks * capped
            total_ticks += ticks
        if total_ticks <= 0:
            return {"multiplier": 1.0, "equivalent_cdr": 0.0, "average_cdr": 0.0}
        multiplier = weighted_multiplier / total_ticks
        equivalent_cdr = max(0.0, min(70.0, (1 - 1 / multiplier) * 100))
        average_cdr = weighted_cdr / total_ticks
        return {"multiplier": multiplier, "equivalent_cdr": equivalent_cdr, "average_cdr": average_cdr}

    def _dedup_build_buffs(self, buffs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        group_keys: dict[str, str] = {}
        order: list[str] = []
        for item in buffs:
            skill = norm(item.get("skill"))
            if not skill or skill == "无":
                continue
            group = self._buff_exclusive_group_for_label(skill)
            key = f"group:{group}" if group else f"skill:{skill}"
            group_keys[key] = key
            if key not in order:
                order.append(key)
            level = as_number(item.get("level"))
            current = best.get(key)
            if current is None or level > as_number(current.get("level")):
                best[key] = {"skill": skill, "level": item.get("level")}
        return [best[key] for key in order if key in group_keys and key in best]

    def _team_resist_reduction_value(self, attrs: dict[str, float]) -> float:
        profile = self._job_profile()
        element_attr = norm(profile.get("element_attr"))
        attr = {
            "火攻%": "减火抗%",
            "水攻%": "减水抗%",
            "光攻%": "减光抗%",
            "暗攻%": "减暗抗%",
        }.get(element_attr)
        return max(0.0, display_value(attrs, "减全抗%") + (display_value(attrs, attr) if attr else 0.0))
