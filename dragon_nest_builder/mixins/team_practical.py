from __future__ import annotations

from ..shared import *
from ..shared import _ExtremeTimelineProxy

BuildEditor = None


class TeamPracticalMixin:
    def _team_practical_sim_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        defaults = self.build.get("team_sim", {}) if isinstance(self.build.get("team_sim", {}), dict) else {}

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(QLabel("模拟窗口"))
        self.team_practical_window_spin = QDoubleSpinBox()
        self.team_practical_window_spin.setRange(30.0, 600.0)
        self.team_practical_window_spin.setDecimals(1)
        self.team_practical_window_spin.setSuffix("s")
        linked_window = (
            self.skill_long_window_spin.value()
            if isinstance(getattr(self, "skill_long_window_spin", None), QDoubleSpinBox)
            else as_number(defaults.get("practical_window"), 180.0)
        )
        self.team_practical_window_spin.setValue(max(30.0, min(600.0, linked_window)))
        self.team_practical_window_spin.setFixedWidth(90)
        self.team_practical_window_spin.setReadOnly(True)
        self.team_practical_window_spin.setToolTip("与技能模拟 → 长时间实战模拟的模拟窗口同步")
        controls.addWidget(self.team_practical_window_spin)
        controls.addWidget(QLabel("与长时间实战模拟同步"))
        if isinstance(getattr(self, "skill_long_window_spin", None), QDoubleSpinBox):
            self.skill_long_window_spin.valueChanged.connect(self._sync_team_practical_window_from_skill)
        controls.addWidget(QLabel("时间轴缩放"))
        self.team_practical_zoom_slider = QSlider(Qt.Horizontal)
        self.team_practical_zoom_slider.setRange(10, 80)
        self.team_practical_zoom_slider.setValue(30)
        self.team_practical_zoom_slider.setMinimumWidth(180)
        controls.addWidget(self.team_practical_zoom_slider, 1)
        self.team_practical_zoom_value = QLabel(f"{self.team_practical_zoom_slider.value()} px/s")
        self.team_practical_zoom_value.setMinimumWidth(58)
        self.team_practical_zoom_slider.valueChanged.connect(
            lambda value: self.team_practical_zoom_value.setText(f"{value} px/s")
        )
        controls.addWidget(self.team_practical_zoom_value)
        detail_button = app_button("配队详情")
        detail_button.setToolTip("逐项移除队员或 Buff，并按当前时间轴重新计算实际损失")
        detail_button.clicked.connect(self._show_team_practical_detail_dialog)
        controls.addWidget(detail_button)
        restore_button = app_button("恢复自动轴", primary=True)
        restore_button.clicked.connect(self._team_practical_restore_auto)
        controls.addWidget(restore_button)
        layout.addLayout(controls)

        hint = QLabel(
            "技能轴使用配装1当前职业、当前加点与技能数据；队友之间各自独立排技能，同一队员的主动效果按施法时间依次释放。"
            "精神爆发、时间加速与鸡尾酒同步长时间实战模拟；九天之舞二阶按每次15秒团队增益排入时间轴。"
        )
        hint.setObjectName("inlineHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.team_practical_total_label = QLabel("个人配队实战模拟等待计算")
        self.team_practical_total_label.setTextFormat(Qt.RichText)
        self.team_practical_total_label.setAlignment(Qt.AlignCenter)
        self.team_practical_total_label.setWordWrap(True)
        self.team_practical_total_label.setMinimumHeight(62)
        self.team_practical_total_label.setStyleSheet(
            "QLabel { background:#effaf6; border:1px solid #77c9ad; border-radius:6px; "
            "color:#166b54; font-size:15px; font-weight:700; padding:6px 12px; }"
        )
        layout.addWidget(self.team_practical_total_label)

        self.team_practical_timeline = SkillBurstTimelineWidget(
            editable=True,
            accent="#13795b",
            single_track=False,
            pixels_per_second=30,
        )
        self.team_practical_timeline.sequenceMoveRequested.connect(self._team_practical_move_segment)
        self.team_practical_timeline.segmentSelected.connect(self._team_practical_select_segment)
        self.team_practical_zoom_slider.valueChanged.connect(self.team_practical_timeline.set_pixels_per_second)
        self._team_practical_manual_sequence = copy.deepcopy(defaults.get("practical_manual_sequence"))
        if not isinstance(self._team_practical_manual_sequence, list):
            self._team_practical_manual_sequence = None
        self._team_practical_manual_job = norm(defaults.get("practical_manual_job")) or norm(self.second_job.currentText())
        self._team_practical_auto_segments: list[dict[str, Any]] = []
        self._team_practical_last_segments: list[dict[str, Any]] = []
        self._team_practical_selected_index = -1
        self.team_practical_scroll = QScrollArea()
        self.team_practical_scroll.setWidgetResizable(False)
        self.team_practical_scroll.setFrameShape(QFrame.NoFrame)
        self.team_practical_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.team_practical_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.team_practical_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.team_practical_scroll.horizontalScrollBar().setMinimumHeight(16)
        self.team_practical_scroll.setStyleSheet(
            "QScrollBar:horizontal { height: 16px; margin: 1px 0; background: #e8eef2; }"
            "QScrollBar::handle:horizontal { min-width: 42px; background: #8aa7b3; border-radius: 5px; }"
            "QScrollBar::handle:horizontal:hover { background: #5f8796; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        )
        self.team_practical_scroll.setWidget(self.team_practical_timeline)
        self.team_practical_scroll.setMinimumHeight(330)
        layout.addWidget(self.team_practical_scroll, 3)

        analysis = QSplitter(Qt.Horizontal)
        contribution_box = QGroupBox("技能输出占比")
        contribution_layout = QVBoxLayout(contribution_box)
        self.team_practical_contribution_table = AppTableWidget(0, 4)
        self.team_practical_contribution_table.setHorizontalHeaderLabels(["技能", "次数", "总输出", "占比"])
        self.team_practical_contribution_table.verticalHeader().setVisible(False)
        self._configure_table(self.team_practical_contribution_table, [145, 58, 112, 72])
        contribution_layout.addWidget(self.team_practical_contribution_table)
        analysis.addWidget(contribution_box)

        detail_box = QGroupBox("技能列表 / 具体时刻")
        detail_layout = QVBoxLayout(detail_box)
        self.team_practical_detail_table = AppTableWidget(0, 7)
        self.team_practical_detail_table.setHorizontalHeaderLabels(
            ["序号", "类型", "技能", "开始", "结束", "基础百分比", "计队友Buff输出/DPA"]
        )
        self.team_practical_detail_table.verticalHeader().setVisible(False)
        self._configure_table(self.team_practical_detail_table, [48, 82, 150, 70, 70, 112, 185])
        self.team_practical_detail_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        detail_layout.addWidget(self.team_practical_detail_table)
        analysis.addWidget(detail_box)
        analysis.setStretchFactor(0, 1)
        analysis.setStretchFactor(1, 2)
        analysis.setMinimumHeight(285)
        layout.addWidget(analysis, 2)
        return page

    def _team_practical_selected_effects(
        self,
        metrics: Iterable[dict[str, Any]] | None = None,
        *,
        excluded_provider: str = "",
        excluded_effect: tuple[str, str] | None = None,
        optimize_aura: bool = True,
    ) -> list[dict[str, Any]]:
        if not hasattr(self, "team_effect_table"):
            return []
        raw_selected_effects = self._selected_team_effects(use_base_cooldowns=False, dedup=False)
        excluded_provider_key = norm(excluded_provider)
        excluded_effect_key = (
            (norm(excluded_effect[0]), self._team_effect_skill_name(excluded_effect[1]))
            if excluded_effect is not None
            else None
        )
        if excluded_provider_key:
            raw_selected_effects = [
                effect
                for effect in raw_selected_effects
                if norm(effect.get("provider")) != excluded_provider_key
            ]
        if excluded_effect_key is not None:
            raw_selected_effects = [
                effect
                for effect in raw_selected_effects
                if (
                    norm(effect.get("provider")),
                    self._team_effect_skill_name(effect.get("skill")),
                ) != excluded_effect_key
            ]
        raw_effects = self._team_effects_with_auto_backups(raw_selected_effects)
        applied_cdr_skills = self._team_applied_cdr_skills()
        if optimize_aura:
            raw_effects, _aura_choices = self._best_team_aura_effects(raw_effects, applied_cdr_skills)
        current_job = norm(self.second_job.currentText())
        current_provider = ""
        if hasattr(self, "team_member_table"):
            for row in range(TEAM_MEMBER_ROWS):
                row_job = self.combo_text(self.team_member_table, row, 1)
                if self._team_real_job(row_job) == current_job:
                    current_provider = f"队员{row + 1} {row_job}"
                    break
        self._team_practical_current_provider = current_provider
        teammate_effects = [
            effect
            for effect in raw_effects
            if (
                not current_provider
                or norm(effect.get("provider")) != norm(current_provider)
                or is_acceleration_resistance_skill(
                    self._team_effect_skill_name(effect.get("skill"))
                )
            )
        ]
        relevant_resists = self._skill_burst_relevant_resist_attrs(metrics or [])
        elemental_resists = {"减火抗%", "减水抗%", "减光抗%", "减暗抗%"}
        attack_attr = norm(self._job_profile().get("attack_attr"))
        irrelevant_amp_attrs: set[str] = set()
        if attack_attr == "物攻":
            irrelevant_amp_attrs = {"魔法增伤%", "魔法伤害增加%"}
        elif attack_attr == "魔攻":
            irrelevant_amp_attrs = {"物理增伤%", "物理伤害增加%"}
        prepared: list[dict[str, Any]] = []
        for effect in self._dedup_team_effects(teammate_effects):
            copied = copy.deepcopy(effect)
            row = copied.get("row")
            if isinstance(row, int) and row >= 0:
                base_cooldown = self._team_cooldown_base(row)
            else:
                timing = self._team_effect_timing(
                    copied.get("provider", ""),
                    copied.get("skill", ""),
                    copied.get("level", ""),
                )
                base_cooldown = as_number(timing.get("cooldown"), copied.get("cooldown"))
            copied["cooldown"] = max(0.0, base_cooldown)
            copied["coverage"] = self._team_effect_coverage(copied["cooldown"], copied.get("duration", 0))
            display_attrs = dict(copied.get("attrs", {}))
            had_irrelevant_amp = any(norm(key) in irrelevant_amp_attrs for key in display_attrs)
            display_attrs = {
                key: value
                for key, value in display_attrs.items()
                if norm(key) not in irrelevant_amp_attrs
            }
            had_elemental_resist = any(norm(key) in elemental_resists for key in display_attrs)
            for key in list(display_attrs):
                if norm(key) in elemental_resists and norm(key) not in relevant_resists:
                    display_attrs.pop(key, None)
            if (had_elemental_resist or had_irrelevant_amp) and not display_attrs:
                continue
            skill_name = self._team_effect_skill_name(copied.get("skill"))
            attrs = dict(display_attrs)
            cdr = max(0.0, as_number(attrs.get("冷却缩减%")))
            if cdr > 0:
                attrs.pop("冷却缩减%", None)
            if is_acceleration_resistance_skill(skill_name):
                attrs.pop("动作速度%", None)
                attrs.pop("攻速%", None)
            copied["display_attrs"] = display_attrs
            copied["attrs"] = attrs
            prepared.append(copied)
        return prepared

    @staticmethod
    def _team_practical_effect_type(effect: dict[str, Any]) -> tuple[str, str]:
        label = norm(effect.get("skill"))
        prefix = label.split("｜", 1)[0] if "｜" in label else ""
        attrs = {norm(key) for key, value in effect.get("display_attrs", effect.get("attrs", {})).items() if abs(as_number(value)) > 1e-9}
        if prefix == "增伤debuff" or any("增伤" in key for key in attrs):
            return "增伤Debuff", "team_amp"
        if prefix == "减抗debuff" or any(key.startswith("减") and key.endswith("抗%") and "致命" not in key for key in attrs):
            return "减抗Debuff", "team_resist"
        if prefix == "减防debuff" or any(key in {"减防御%", "减防御力%"} for key in attrs):
            return "减防Debuff", "team_defense"
        if prefix == "减致命抗debuff" or any("减致命" in key and "抵抗" in key for key in attrs):
            return "减致抗Debuff", "team_crit_resist"
        if prefix == "叠冰debuff" or any("冰冻层" in key for key in attrs):
            return "叠冰Debuff", "team_freeze"
        return "团队Buff", "team_buff"

    def _team_practical_is_continuous_effect(self, effect: dict[str, Any]) -> bool:
        skill = self._team_effect_skill_name(effect.get("skill"))
        cooldown = max(0.0, as_number(effect.get("cooldown")))
        duration = max(0.0, as_number(effect.get("duration")))
        _effect_type, effect_category = self._team_practical_effect_type(effect)
        # Debuffs require a real cast even when duration fully covers cooldown.
        # Only true buffs and auras may be treated as pre-applied continuous effects.
        if effect_category != "team_buff":
            return False
        # Cocktail is maintained permanently in combat.  Its table cooldown
        # and 150-second duration are implementation data, not a recurring
        # cast that should be delayed behind the Alchemist's other support
        # skills.  Treating it as intermittent made the first -12% CDR window
        # start tens of seconds late and materially understated its replanned
        # team-DPS value.
        if skill == "鸡尾酒":
            return True
        # Aura data has no cast cooldown or duration because it remains active
        # until the player switches it off.
        if skill in TEAM_AURA_OPTIONS or "光环" in skill:
            return True
        return (
            bool(effect.get("permanent"))
            or cooldown <= 0 and duration <= 0
            or cooldown > 0 and duration >= cooldown - 1e-9
        )

    @staticmethod
    def _team_practical_effect_is_stackable(effect: dict[str, Any]) -> bool:
        """Return whether another provider's copy may coexist numerically.

        The practical-team templates predate the packet effect catalog and do
        not all carry an explicit ``stackable`` flag.  Keep the small set of
        packet-confirmed stacking effects here as a compatibility fallback so
        duplicate-provider filtering never turns freeze layers or independent
        defence/resistance reductions into highest-value-only effects.
        """

        skill = BuildEditor._team_effect_skill_name(effect.get("skill"))
        if BuildEditor._skill_burst_skill_key(skill) in {
            "致命狂镰",
            "惩罚",
            "铁壁后缀",
            "帷幕后缀",
        }:
            return False
        if bool(effect.get("stackable")):
            return True
        attrs = {
            norm(key)
            for key, value in effect.get(
                "display_attrs", effect.get("attrs", {})
            ).items()
            if abs(as_number(value)) > 1e-9
        }
        return (
            "冰冻层" in attrs
            or skill
            in {
                "C2H5OH",
                "神圣大地",
            }
        )

    def _team_practical_effect_windows(
        self,
        effects: list[dict[str, Any]],
        window: float,
        *,
        include_special_cdr: bool = False,
        first_cast_times: dict[Any, float] | None = None,
        cast_schedule: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
        strict_cast_schedule: bool | None = None,
    ) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        horizon = max(1.0, as_number(window, 180.0))
        initial_starts: dict[str, float] = {}
        provider_initial_starts: dict[tuple[str, str], float] = {}
        for raw_key, raw_start in (first_cast_times or {}).items():
            start = max(0.0, as_number(raw_start))
            if isinstance(raw_key, tuple) and len(raw_key) == 2:
                provider = norm(raw_key[0])
                skill = self._team_effect_skill_name(raw_key[1])
                if provider and skill:
                    provider_initial_starts[(provider, self._skill_burst_skill_key(skill))] = start
                continue
            skill = self._team_effect_skill_name(raw_key)
            if skill:
                initial_starts[skill] = start
        special_cdr = {"鸡尾酒"}
        if strict_cast_schedule is None:
            strict_cast_schedule = cast_schedule is not None
        normalized_schedule = {
            (norm(provider), self._skill_burst_skill_key(skill)): [
                (max(0.0, as_number(start)), max(max(0.0, as_number(start)), as_number(end)))
                for start, end in casts
            ]
            for (provider, skill), casts in (cast_schedule or {}).items()
        }
        continuous_effects: list[dict[str, Any]] = []
        active_by_provider: dict[str, list[dict[str, Any]]] = {}

        def append_timed_window(
            effect: dict[str, Any],
            cast_start: float,
            cast_end: float,
            cooldown: float,
            duration: float,
            effect_starts_after_cast: bool = False,
        ) -> None:
            if cast_start >= horizon - 1e-6:
                return
            skill = self._team_effect_skill_name(effect.get("skill"))
            display_attrs = self._team_practical_display_attrs(effect)
            attrs_text = attr_summary(display_attrs, limit=999)
            _effect_type, effect_category = self._team_practical_effect_type(effect)
            if self._skill_burst_skill_key(skill) == "痊愈之手":
                # The relic starts refreshing its team buff after the summon
                # animation finishes.  Starting at cast_start made an 18-second
                # relic appear to last for 18 seconds plus its cast time.
                effect_starts_after_cast = True
            effect_start, raw_effect_end, refresh_tail = (
                team_effect_application_window_for_attrs(
                    skill,
                    effect.get("attrs", display_attrs),
                    cast_start,
                    cast_end,
                    duration,
                    starts_on_cast=not (
                        effect_starts_after_cast and effect_category == "team_buff"
                    ),
                )
            )
            effect_end = min(horizon, raw_effect_end)
            window_row = {
                "name": "｜".join(filter(None, [skill, attrs_text])),
                "start": effect_start,
                "end": effect_end,
                "cast_start": cast_start,
                "cast_end": min(horizon, cast_end),
                "color": SKILL_BURST_EFFECT_COLORS[effect_category],
                "detail": (
                    f"施法 {max(0.0, cast_end - cast_start):g}s；"
                    f"{effect_start:.1f}s生效，{effect_end:.1f}s结束；"
                    + (
                        f"单次持续{duration:g}s，连续命中刷新约{refresh_tail:g}s；"
                        if refresh_tail > 0
                        else ""
                    )
                    + f"原始CD {cooldown:g}s；{attrs_text}"
                ),
                "category": effect_category,
                "source": skill,
                "team_effect": effect,
                "stackable": self._team_practical_effect_is_stackable(effect),
                "refresh_tail": refresh_tail,
            }
            if self._skill_burst_skill_key(skill) == "痊愈之手":
                window_row["refresh_interval"] = 2.0
                window_row["display_refresh_pulses"] = True
                window_row["collapse_refresh_display"] = True
                window_row["refresh_effect_duration"] = duration
                window_row["refresh_source_end"] = effect_end
                window_row["display_horizon"] = horizon
                level = norm(effect.get("level"))
                level_text = f"Lv{level}；" if level else ""
                window_row["detail"] = (
                    f"{level_text}圣物存在{duration:g}s；每2s刷新；"
                    f"每次Buff持续{duration:g}s；{attrs_text}"
                )
            windows.extend(self._skill_burst_expand_refresh_display_windows(window_row))

        for effect in effects:
            skill = self._team_effect_skill_name(effect.get("skill"))
            if (
                skill in special_cdr
                or is_acceleration_resistance_skill(skill)
            ) and not include_special_cdr:
                continue
            if "_score_recipient_active_intervals" in effect:
                # Team-score replay supplies the status intervals observed on
                # this exact recipient.  Do not rebuild them from the
                # provider's cast or turn continuous auras into full-fight
                # effects: being cast does not prove that a distant teammate
                # received the Buff.
                display_attrs = self._team_practical_display_attrs(effect)
                attrs_text = attr_summary(display_attrs, limit=999)
                for raw_start, raw_end in effect.get(
                    "_score_recipient_active_intervals", ()
                ):
                    start = max(0.0, as_number(raw_start))
                    end = min(horizon, max(start, as_number(raw_end)))
                    if end <= start + 1e-6:
                        continue
                    windows.append({
                        "name": "｜".join(filter(None, [skill, attrs_text])),
                        "start": start,
                        "end": end,
                        "cast_start": start,
                        "cast_end": start,
                        "color": SKILL_BURST_EFFECT_COLORS["team_buff"],
                        "detail": (
                            f"接收者实测 {start:.1f}s-{end:.1f}s；{attrs_text}"
                        ),
                        "category": "team_buff",
                        "source": skill,
                        "team_effect": effect,
                        "stackable": self._team_practical_effect_is_stackable(effect),
                        "recipient_observed": True,
                    })
                continue
            if skill == "阿兹那之息":
                # The permanent rabbit refreshes a 30-second team buff every
                # five seconds. One union window avoids stacking six overlapping
                # refreshes while keeping it in the team-buff lane.
                activation_rule = self.store.buff_activation_rule("通用", skill, "团队buff")
                refresh_interval = max(0.01, as_number(activation_rule.get("refresh_interval"), 5.0))
                duration = max(0.0, as_number(effect.get("duration"), 30.0)) or 30.0
                display_attrs = self._team_practical_display_attrs(effect)
                attrs_text = attr_summary(display_attrs, limit=999)
                windows.append({
                    "name": "｜".join(filter(None, [skill, attrs_text])),
                    "start": 0.0,
                    "end": horizon,
                    "cast_start": 0.0,
                    "cast_end": 0.0,
                    "color": SKILL_BURST_EFFECT_COLORS["team_buff"],
                    "detail": f"每{refresh_interval:g}s刷新；单次持续{duration:g}s；{attrs_text}",
                    "category": "team_buff",
                    "source": skill,
                    "team_effect": effect,
                    "refresh_interval": refresh_interval,
                })
                continue
            cooldown = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            provider = norm(effect.get("provider")) or "通用"
            if "打坐曜" in provider and skill == "查克拉治愈术":
                # Treat the sitting action as the source of truth below. It is
                # continuous only while the player is actually sitting.
                continuous_effects.append(effect)
            elif self._team_practical_is_continuous_effect(effect):
                continuous_effects.append(effect)
            elif bool(effect.get("full_time_equivalent")) and skill != "查克拉治愈术":
                display_attrs = self._team_practical_display_attrs(effect)
                attrs_text = attr_summary(display_attrs, limit=999)
                window_row = {
                    "name": "｜".join(filter(None, [skill, attrs_text])),
                    "start": 0.0,
                    "end": horizon,
                    "color": SKILL_BURST_EFFECT_COLORS["team_buff"],
                    "detail": f"全程等效；{attrs_text}",
                    "category": "team_buff",
                    "source": skill,
                    "team_effect": effect,
                }
                if skill == "查克拉治愈术":
                    window_row["refresh_interval"] = 0.5
                windows.append(window_row)
            elif duration > 0 and cooldown > 0:
                scheduled_casts = normalized_schedule.get((provider, self._skill_burst_skill_key(skill)))
                if not scheduled_casts and re.search(r"\s*EX$", skill, flags=re.IGNORECASE):
                    base_skill = re.sub(r"\s*EX$", "", skill, flags=re.IGNORECASE)
                    scheduled_casts = normalized_schedule.get(
                        (provider, self._skill_burst_skill_key(base_skill))
                    )
                if scheduled_casts:
                    for cast_start, cast_end in scheduled_casts:
                        append_timed_window(
                            effect,
                            cast_start,
                            cast_end,
                            cooldown,
                            duration,
                            effect_starts_after_cast=True,
                        )
                elif not strict_cast_schedule:
                    active_by_provider.setdefault(provider, []).append(effect)
            elif duration > 0 and not strict_cast_schedule:
                cast_start = min(
                    horizon,
                    provider_initial_starts.get(
                        (provider, self._skill_burst_skill_key(skill)),
                        initial_starts.get(skill, 0.0),
                    ),
                )
                cast_time = self._team_practical_effect_cast_time(effect)
                append_timed_window(
                    effect,
                    cast_start,
                    min(horizon, cast_start + cast_time),
                    0.0,
                    duration,
                    effect_starts_after_cast=True,
                )

        for provider, provider_effects in active_by_provider.items():
            next_ready = {
                index: min(
                    horizon,
                    provider_initial_starts.get(
                        (
                            provider,
                            self._skill_burst_skill_key(self._team_effect_skill_name(effect.get("skill"))),
                        ),
                        initial_starts.get(self._team_effect_skill_name(effect.get("skill")), 0.0),
                    ),
                )
                for index, effect in enumerate(provider_effects)
            }
            cursor = 0.0
            guard = 0
            while cursor < horizon - 1e-6 and guard < 10000:
                guard += 1
                ready = [index for index, value in next_ready.items() if value <= cursor + 1e-6]
                if not ready:
                    cursor = min(next_ready.values(), default=horizon)
                    continue
                index = min(
                    ready,
                    key=lambda item: (
                        next_ready[item],
                        0
                        if (
                            (
                                provider,
                                self._skill_burst_skill_key(
                                    self._team_effect_skill_name(provider_effects[item].get("skill"))
                                ),
                            )
                            in provider_initial_starts
                            or self._team_effect_skill_name(provider_effects[item].get("skill")) in initial_starts
                        )
                        else 1,
                        as_number(provider_effects[item].get("row"), 9999),
                        self._team_effect_skill_name(provider_effects[item].get("skill")),
                    ),
                )
                effect = provider_effects[index]
                skill = self._team_effect_skill_name(effect.get("skill"))
                cooldown = max(0.01, as_number(effect.get("cooldown")))
                duration = max(0.0, as_number(effect.get("duration")))
                cast_time = self._team_practical_effect_cast_time(effect)
                cast_start = cursor
                cast_end = min(horizon, cast_start + cast_time)
                append_timed_window(effect, cast_start, cast_end, cooldown, duration)
                next_ready[index] = cast_start + cooldown
                cursor = max(cast_end, cast_start + 0.01)

        yao_action_casts: dict[str, list[tuple[float, float]]] = {}
        yao_sitting_casts: dict[str, list[tuple[float, float]]] = {}
        for (provider, skill_key), casts in normalized_schedule.items():
            if "打坐曜" not in provider:
                continue
            if skill_key == "查克拉治愈术":
                yao_sitting_casts.setdefault(provider, []).extend(casts)
                continue
            yao_action_casts.setdefault(provider, []).extend(casts)
        for window_row in windows:
            effect = window_row.get("team_effect", {})
            provider = norm(effect.get("provider"))
            if "打坐曜" in provider:
                cast = (
                    as_number(window_row.get("cast_start")),
                    as_number(window_row.get("cast_end")),
                )
                if cast not in yao_action_casts.setdefault(provider, []):
                    yao_action_casts[provider].append(cast)

        for effect in continuous_effects:
            provider = norm(effect.get("provider")) or "通用"
            skill = self._team_effect_skill_name(effect.get("skill"))
            intervals = [(0.0, horizon)]
            if "打坐曜" in provider and skill == "查克拉治愈术":
                sitting = yao_sitting_casts.get(provider, [])
                if sitting:
                    intervals = [
                        (
                            max(0.0, start),
                            min(horizon, end + YAO_MEDITATION_LINGER_SECONDS),
                        )
                        for start, end in sitting
                        if end > start + 1e-6
                    ]
                else:
                    intervals = []
                merged_intervals: list[tuple[float, float]] = []
                for start, end in sorted(intervals):
                    if merged_intervals and start <= merged_intervals[-1][1] + 1e-6:
                        merged_intervals[-1] = (merged_intervals[-1][0], max(merged_intervals[-1][1], end))
                    else:
                        merged_intervals.append((start, end))
                intervals = merged_intervals
            display_attrs = self._team_practical_display_attrs(effect)
            attrs_text = attr_summary(display_attrs, limit=999)
            for start, end in intervals:
                if end <= start + 1e-6:
                    continue
                category = "team_buff" if skill == "查克拉治愈术" else "team_continuous"
                window_row = {
                    "name": "｜".join(filter(None, [skill, attrs_text])),
                    "start": start,
                    "end": end,
                    "color": SKILL_BURST_EFFECT_COLORS[category],
                    "detail": f"{start:.1f}s-{end:.1f}s；{attrs_text}",
                    "category": category,
                    "source": skill,
                    "team_effect": effect,
                }
                if skill == "查克拉治愈术":
                    window_row["refresh_interval"] = 0.5
                windows.append(window_row)
        from ..live_dps.support_boss_scope import clip_effect_windows
        return clip_effect_windows(windows)

    def _team_practical_effect_cast_time(self, effect: dict[str, Any]) -> float:
        provider_job = self._team_provider_job(effect.get("provider", ""))
        skill = self._team_effect_skill_name(effect.get("skill"))
        timing = self.store.skill_cast_time(skill, [provider_job])
        cast_time = max(0.0, as_number(timing.get("cast_time")))
        if cast_time <= 0:
            cast_time = max(0.0, as_number(timing.get("quick_time")))
        if cast_time <= 0:
            return 0.5
        if bool(timing.get("fixed")):
            return cast_time
        return max(0.05, self.store.effective_skill_cast_time(timing))

    def _team_practical_effect_affects_output(self, effect: dict[str, Any]) -> bool:
        skill = self._team_effect_skill_name(effect.get("skill"))
        if skill in {"精神爆发", "时间加速", "鸡尾酒", "九天之舞2阶", "奉献乌鸦"}:
            return True
        offensive_tokens = (
            "力量", "敏捷", "智慧", "物攻", "魔攻", "攻击", "火攻", "水攻", "光攻", "暗攻", "最终", "致命", "暴击", "爆伤",
            "增伤", "额外伤害", "减防", "减致命", "减火抗", "减水抗", "减光抗", "减暗抗", "减全抗",
            "冷却缩减", "动作速度", "攻速", "冰冻层",
        )
        return any(
            any(token in norm(attr) for token in offensive_tokens) and abs(as_number(value)) > 1e-9
            for attr, value in effect.get("attrs", {}).items()
        )

    def _team_practical_display_attrs(self, effect: dict[str, Any]) -> dict[str, float]:
        attrs = dict(effect.get("display_attrs", effect.get("attrs", {})))
        return self._team_display_attrs(attrs)

    def _team_practical_display_effect_windows(
        self,
        team_windows: list[dict[str, Any]],
        own_windows: list[dict[str, Any]],
        window: float,
        current_provider: str = "",
        relevant_resists: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        display: list[dict[str, Any]] = []
        horizon = max(0.0, as_number(window))
        provider_key = norm(current_provider)
        declared_team_buffs = {
            self._skill_burst_skill_key(row.get("技能"))
            for row in self.store.tables.get("buff", [])
            if norm(row.get("类别")) == "团队buff"
        }
        # These two effects are team-wide even when the provider's own action
        # window is the only metadata available to the display layer.
        declared_team_buffs.update({"精神爆发", "时间加速"})
        team_categories_by_skill: dict[str, list[tuple[str, set[str]]]] = {}
        for row in team_windows:
            effect = row.get("team_effect", {})
            skill_key = self._skill_burst_skill_key(self._team_effect_skill_name(effect.get("skill")))
            if not skill_key:
                continue
            attrs = effect.get("display_attrs") or effect.get("attrs") or {}
            attr_keys = {
                norm(key) for key, value in attrs.items()
                if norm(key) and not norm(key).startswith("__") and abs(as_number(value)) > 1e-9
            }
            team_categories_by_skill.setdefault(skill_key, []).append(
                (self._team_practical_effect_type(effect)[1], attr_keys)
            )
        personal_team_windows = [
            row
            for row in team_windows
            if provider_key and norm(row.get("team_effect", {}).get("provider")) == provider_key
            and norm(row.get("category")) != "team_continuous"
        ]
        if personal_team_windows:
            team_windows = [row for row in team_windows if row not in personal_team_windows]
        full_continuous = [
            row
            for row in team_windows
            if norm(row.get("category")) == "team_continuous"
            and as_number(row.get("start")) <= 1e-6
            and as_number(row.get("end")) >= horizon - 1e-6
            and self._team_effect_skill_name(row.get("team_effect", {}).get("skill")) != "查克拉治愈术"
        ]
        for useful, category, label in (
            (True, "team_continuous", "常驻"),
            (False, "team_no_gain", "无提升"),
        ):
            group = [
                row
                for row in full_continuous
                if self._team_practical_effect_affects_output(row.get("team_effect", {})) == useful
            ]
            if not group:
                continue
            grouped_names: dict[str, list[str]] = {}
            group_order: dict[str, int] = {}
            for row in group:
                effect = row.get("team_effect", {})
                effect_type, effect_category = self._team_practical_effect_type(effect)
                skill = self._team_effect_skill_name(effect.get("skill"))
                attrs_text = attr_summary(self._team_practical_display_attrs(effect), limit=999)
                item_text = skill + (f"（{attrs_text}）" if attrs_text else "")
                if item_text and item_text not in grouped_names.setdefault(effect_type, []):
                    grouped_names[effect_type].append(item_text)
                group_order[effect_type] = SKILL_BURST_EFFECT_CATEGORY_ORDER.get(effect_category, 99)
            group_parts = [
                f"{effect_type}：{'、'.join(grouped_names[effect_type])}"
                for effect_type in sorted(grouped_names, key=lambda key: (group_order.get(key, 99), key))
            ]
            combined_text = "；".join(group_parts)
            display.append({
                "name": label + "｜" + combined_text,
                "start": 0.0,
                "end": horizon,
                "color": SKILL_BURST_EFFECT_COLORS[category],
                "detail": combined_text or "同一大框汇总全部真正覆盖整个窗口的效果",
                "category": category,
                "source": "常驻Buff" if useful else "无提升Buff",
            })

        for row in team_windows:
            if row in full_continuous:
                continue
            copied = dict(row)
            effect = copied.get("team_effect", {})
            useful = self._team_practical_effect_affects_output(effect)
            _effect_type, effect_category = self._team_practical_effect_type(effect)
            copied["category"] = effect_category if useful else "team_no_gain"
            copied["color"] = SKILL_BURST_EFFECT_COLORS[copied["category"]]
            if not useful:
                copied["source"] = "无提升Buff"
            display.append(copied)

        own_window_keys = {
            (
                self._skill_burst_skill_key(row.get("source")),
                round(as_number(row.get("start")), 4),
                round(as_number(row.get("end")), 4),
            )
            for row in own_windows
        }
        for row in personal_team_windows:
            key = (
                self._skill_burst_skill_key(row.get("source")),
                round(as_number(row.get("start")), 4),
                round(as_number(row.get("end")), 4),
            )
            if key in own_window_keys:
                continue
            copied = dict(row)
            _effect_type, effect_category = self._team_practical_effect_type(copied.get("team_effect", {}))
            copied["category"] = effect_category
            copied["color"] = SKILL_BURST_EFFECT_COLORS[copied["category"]]
            display.append(copied)

        for row in own_windows:
            copied = dict(row)
            own_category = norm(row.get("category"))
            attrs_text = norm(row.get("attrs_text"))
            source_skill = self._skill_burst_skill_key(row.get("source"))
            own_attrs = row.get("display_attrs") or row.get("attrs") or {}
            own_attr_keys = {
                norm(key) for key, value in own_attrs.items()
                if norm(key) and not norm(key).startswith("__") and abs(as_number(value)) > 1e-9
            }
            source_categories = team_categories_by_skill.get(
                self._skill_burst_skill_key(row.get("source")), []
            )
            source_category = next(
                (
                    category for category, effect_attr_keys in source_categories
                    if not own_attr_keys or not effect_attr_keys or own_attr_keys & effect_attr_keys
                ),
                "",
            )
            if source_skill in {"精神爆发", "时间加速"}:
                display_category = "team_buff"
            elif source_category:
                display_category = source_category
            elif own_category == "resist" or "减抗" in attrs_text:
                display_category = "team_resist"
            elif own_category == "amp" or "增伤" in attrs_text:
                display_category = "team_amp"
            elif own_category == "freeze" or "冰冻层" in attrs_text:
                display_category = "team_freeze"
            elif "减致命" in attrs_text:
                display_category = "team_crit_resist"
            elif "减防" in attrs_text:
                display_category = "team_defense"
            elif source_skill in declared_team_buffs:
                display_category = "team_buff"
            else:
                display_category = "team_personal"
            copied["category"] = display_category
            copied["color"] = SKILL_BURST_EFFECT_COLORS[copied["category"]]
            display.append(copied)
        if relevant_resists is None:
            relevant_resists = self._skill_burst_relevant_resist_attrs(
                getattr(self, "_skill_burst_candidate_metrics", [])
            )
        display = self._skill_burst_collapse_team_resist_effects(display, relevant_resists)
        display = self._skill_burst_collapse_crit_resist_effects(display)
        display = self._skill_burst_collapse_exclusive_amp_effects(display)
        return self._skill_burst_merge_display_effects(display)

    def _team_practical_normalized_effect_state(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], tuple[Any, ...]]:
        prepared = getattr(self, "_team_practical_prepared_effect_state", None)
        if (
            isinstance(prepared, tuple)
            and len(prepared) == 2
            and prepared[0] is effects
        ):
            return effects, prepared[1]
        normalized = self._dedup_team_effects(effects)
        signature = tuple(sorted(
            (
                norm(effect.get("provider")),
                self._team_effect_skill_name(effect.get("skill")),
                tuple(sorted((norm(key), round(as_number(value), 6)) for key, value in effect.get("attrs", {}).items())),
            )
            for effect in normalized
        ))
        return normalized, signature

    def _team_practical_panel_multiplier(self, effects: list[dict[str, Any]]) -> float:
        if not effects:
            return 1.0
        effects, signature = self._team_practical_normalized_effect_state(effects)
        cache = getattr(self, "_team_practical_panel_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._team_practical_panel_cache = cache
        if signature in cache:
            return max(0.0, as_number(cache[signature], 1.0))

        ignored = {
            "冷却缩减%", "动作速度%", "攻速%",
            "减火抗%", "减水抗%", "减光抗%", "减暗抗%", "减全抗%",
            # Neutral attack is applied per skill by the element layer, not
            # to a whole profession (which can also have elemental skills).
            "无属性%",
        }
        instantaneous: list[dict[str, Any]] = []
        for index, effect in enumerate(effects):
            attrs = {
                norm(key): as_number(value)
                for key, value in effect.get("attrs", {}).items()
                if norm(key) not in ignored and abs(as_number(value)) > 1e-9
            }
            if not attrs:
                continue
            instantaneous.append({
                **effect,
                "row": index,
                "attrs": attrs,
                "cooldown": 0.0,
                "duration": 1.0,
                "coverage": 1.0,
                "effective_attrs": attrs,
            })
        if not instantaneous:
            cache[signature] = 1.0
            raw_cache = getattr(self, "_team_practical_raw_attack_cache", None)
            if not isinstance(raw_cache, dict):
                raw_cache = {}
                self._team_practical_raw_attack_cache = raw_cache
            raw_cache[signature] = (1.0, 1.0)
            return 1.0
        base_summary, _base_attrs = self._team_summary_with_effects([])
        linked_summary, _linked_attrs = self._team_summary_with_effects(instantaneous)
        base = max(0.0, as_number(base_summary.get("effective_panel")))
        linked = max(0.0, as_number(linked_summary.get("effective_panel")))
        multiplier = linked / base if base > 0 else 1.0
        cache[signature] = multiplier
        # The ignored CDR/action-speed/resist attributes do not change the raw
        # physical or magic attack values. Reuse this calculation when the
        # timeline asks for those multipliers immediately afterwards.
        raw_cache = getattr(self, "_team_practical_raw_attack_cache", None)
        if not isinstance(raw_cache, dict):
            raw_cache = {}
            self._team_practical_raw_attack_cache = raw_cache
        base_physical = max(0.0, as_number(base_summary.get("physical_average")))
        base_magic = max(0.0, as_number(base_summary.get("magic_average")))
        raw_cache[signature] = (
            max(0.0, as_number(linked_summary.get("physical_average"))) / base_physical
            if base_physical > 0 else 1.0,
            max(0.0, as_number(linked_summary.get("magic_average"))) / base_magic
            if base_magic > 0 else 1.0,
        )
        return max(0.0, multiplier)

    def _team_practical_attack_panel_multipliers(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[float, float]:
        if not effects:
            return 1.0, 1.0
        effects, signature = self._team_practical_normalized_effect_state(effects)
        cache = getattr(self, "_team_practical_attack_panel_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._team_practical_attack_panel_cache = cache
        if signature in cache:
            physical, magic = cache[signature]
            return max(0.0, as_number(physical, 1.0)), max(0.0, as_number(magic, 1.0))

        # The complete panel calculation records these exact raw attack ratios
        # for the same normalized state. Reuse them instead of invoking the
        # calculator twice again for the Paladin split-panel path.
        overall_ratio = self._team_practical_panel_multiplier(effects)
        raw_cache = getattr(self, "_team_practical_raw_attack_cache", None)
        cached_raw = (
            raw_cache.get(signature)
            if isinstance(raw_cache, dict)
            else None
        )
        if isinstance(cached_raw, tuple) and len(cached_raw) == 2:
            physical_ratio = max(0.0, as_number(cached_raw[0], 1.0))
            magic_ratio = max(0.0, as_number(cached_raw[1], 1.0))
        else:
            physical_ratio, magic_ratio = (
                self._team_practical_raw_attack_multipliers(effects)
            )
        common_ratio = overall_ratio / physical_ratio if physical_ratio > 0 else overall_ratio
        result = (physical_ratio * common_ratio, magic_ratio * common_ratio)
        cache[signature] = result
        return result

    def _team_practical_raw_attack_multipliers(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[float, float]:
        """Return only physical/magic attack changes, excluding combat multipliers."""
        if not effects:
            return 1.0, 1.0
        effects, signature = self._team_practical_normalized_effect_state(effects)
        cache = getattr(self, "_team_practical_raw_attack_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._team_practical_raw_attack_cache = cache
        if signature in cache:
            physical, magic = cache[signature]
            return max(0.0, as_number(physical, 1.0)), max(0.0, as_number(magic, 1.0))

        base_summary, _base_attrs = self._team_summary_with_effects([])
        linked_summary, _linked_attrs = self._team_summary_with_effects(effects)
        base_physical = max(0.0, as_number(base_summary.get("physical_average")))
        base_magic = max(0.0, as_number(base_summary.get("magic_average")))
        result = (
            max(0.0, as_number(linked_summary.get("physical_average"))) / base_physical
            if base_physical > 0 else 1.0,
            max(0.0, as_number(linked_summary.get("magic_average"))) / base_magic
            if base_magic > 0 else 1.0,
        )
        cache[signature] = result
        return result

    def _team_practical_direct_attack_multipliers(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[float, float]:
        """Return direct attack buffs after removing all four-stat effects."""
        direct_effects: list[dict[str, Any]] = []
        for effect in effects:
            attrs = {
                norm(key): as_number(value)
                for key, value in effect.get("attrs", {}).items()
                if norm(key) not in SKILL_PERIODIC_IGNORED_DYNAMIC_STAT_ATTRS
                and abs(as_number(value)) > 1e-9
            }
            if not attrs:
                continue
            direct_effects.append({
                **effect,
                "attrs": attrs,
                "effective_attrs": attrs,
            })
        return self._team_practical_raw_attack_multipliers(direct_effects)

    def _team_practical_timeline_loss_data(
        self,
        metrics: list[dict[str, Any]],
        full_context: dict[str, Any],
        full_result: dict[str, Any],
        baseline_result: dict[str, Any],
    ) -> tuple[dict[str, float], dict[str, float], list[list[str]], list[dict[str, Any]]]:
        window = max(30.0, as_number(full_result.get("window"), 180.0))
        manual_sequence = self._team_practical_manual_sequence
        full_stats = self._team_practical_result_stats(full_result)
        baseline_stats = self._team_practical_result_stats(baseline_result)
        current_provider = norm(full_context.get("provider"))

        providers: list[tuple[str, str]] = []
        for row in range(TEAM_MEMBER_ROWS):
            job = self.combo_text(self.team_member_table, row, 1)
            if not job or job == "无":
                continue
            provider = f"队员{row + 1} {job}"
            if norm(provider) == current_provider:
                continue
            providers.append((provider, job))

        effect_options: list[dict[str, Any]] = []
        seen_effects: set[tuple[str, str]] = set()
        for effect in full_context.get("effects", []):
            provider = norm(effect.get("provider"))
            skill = self._team_effect_skill_name(effect.get("skill"))
            key = (provider, skill)
            if not provider or not skill or key in seen_effects:
                continue
            seen_effects.add(key)
            effect_type, _category = self._team_practical_effect_type(effect)
            cooldown = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            if self._team_practical_is_continuous_effect(effect):
                coverage_text = "常驻"
            elif cooldown > 0 and duration > 0:
                coverage_text = f"{min(100.0, duration / cooldown * 100.0):.1f}%"
            else:
                coverage_text = "-"
            job = self._team_provider_job(provider)
            effect_options.append({
                "provider": provider,
                "skill": skill,
                "type": effect_type,
                "job": job,
                "coverage": coverage_text,
                "label": f"{effect_type}｜{skill}（{job}）",
            })

        progress = QProgressDialog(
            "正在按时间轴计算队员移除损失...",
            "",
            0,
            max(1, len(providers)),
            self.window(),
        )
        progress.setWindowTitle("计算配队实战损失")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        def loss_values(result: dict[str, Any]) -> tuple[float, float, float, float]:
            stats = self._team_practical_result_stats(result)
            loss_percent = (
                (full_stats["total"] - stats["total"]) / full_stats["total"] * 100.0
                if full_stats["total"] > 0
                else 0.0
            )
            dps_loss = full_stats["dps"] - stats["dps"]
            return stats["average"], stats["dps"], loss_percent, dps_loss

        member_rows: list[list[str]] = []
        active_providers = {
            norm(effect.get("provider"))
            for effect in full_context.get("effects", [])
            if norm(effect.get("provider"))
        }
        step = 0
        try:
            for provider, job in providers:
                progress.setLabelText(f"正在计算移除 {job} 后的时间轴...")
                QApplication.processEvents()
                if provider in active_providers:
                    context = self._team_practical_runtime_context(
                        metrics,
                        window,
                        excluded_provider=provider,
                        optimize_aura=False,
                    )
                    result = self._team_practical_cached_simulate(metrics, context, manual_sequence)
                else:
                    result = full_result
                average, dps, loss_percent, dps_loss = loss_values(result)
                member_rows.append([
                    provider,
                    f"{self._percent_text(average)}/s",
                    f"{number_text(dps, integer=True)}/s",
                    f"{loss_percent:+.2f}%",
                    f"{dps_loss:+,.0f}",
                ])
                step += 1
                progress.setValue(step)
        finally:
            progress.close()

        member_rows.sort(key=lambda row: as_number(row[3].replace("%", "")), reverse=True)
        effect_options.sort(key=lambda item: (norm(item.get("type")), norm(item.get("job")), norm(item.get("skill"))))
        return full_stats, baseline_stats, member_rows, effect_options

    def _show_team_practical_detail_dialog(self) -> None:
        if not isinstance(getattr(self, "_team_practical_last_result", None), dict):
            self.refresh()
        old_dialog = getattr(self, "_team_practical_detail_dialog", None)
        if isinstance(old_dialog, QDialog):
            old_dialog.close()
            old_dialog.deleteLater()

        dialog = QDialog(self.window())
        dialog.setWindowTitle("当前配队详情")
        dialog.resize(980, 700)
        dialog.setMinimumSize(820, 580)
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self._team_practical_detail_dialog = dialog
        dialog.destroyed.connect(lambda: setattr(self, "_team_practical_detail_dialog", None))

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        title = QLabel("当前配队的时间轴移除损失")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        explanation = QLabel(
            "这里不读取配队设置页的静态贡献。每一项都会从当前配队中移除，再按相同模拟窗口、当前加点和技能轴重新计算；"
            "移除损失% =（完整配队输出 - 移除后输出）÷ 完整配队输出。"
        )
        explanation.setObjectName("inlineHint")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        metrics = copy.deepcopy(getattr(self, "_team_practical_last_metrics", []))
        window = max(
            30.0,
            self.team_practical_window_spin.value()
            if isinstance(getattr(self, "team_practical_window_spin", None), QDoubleSpinBox)
            else 180.0,
        )
        cached_context = getattr(self, "_team_practical_last_context", None)
        full_context = copy.deepcopy(
            cached_context
            if isinstance(cached_context, dict)
            else self._team_practical_runtime_context(metrics, window)
        )
        cached_result = getattr(self, "_team_practical_last_result", None)
        full_result = copy.deepcopy(
            cached_result
            if isinstance(cached_result, dict)
            else self._team_practical_cached_simulate(metrics, full_context, self._team_practical_manual_sequence)
        )
        cached_baseline = getattr(self, "_team_practical_last_baseline_result", None)
        baseline_result = copy.deepcopy(
            cached_baseline
            if isinstance(cached_baseline, dict)
            else self._team_practical_cached_simulate(
                metrics,
                {**full_context, "windows": []},
                self._team_practical_manual_sequence,
            )
        )
        full_stats, baseline_stats, member_rows, effect_options = self._team_practical_timeline_loss_data(
            metrics,
            full_context,
            full_result,
            baseline_result,
        )

        total_gain = (
            (full_stats["total"] / baseline_stats["total"] - 1.0) * 100.0
            if baseline_stats["total"] > 0
            else 0.0
        )
        total_loss = (
            (full_stats["total"] - baseline_stats["total"]) / full_stats["total"] * 100.0
            if full_stats["total"] > 0
            else 0.0
        )
        overview_rows = [
            ["模拟窗口", f"{full_stats['window']:g}s"],
            ["未联动平均输出", f"{self._percent_text(baseline_stats['average'])}/s"],
            ["配队联动平均输出", f"{self._percent_text(full_stats['average'])}/s"],
            ["配队实战DPS", f"{number_text(full_stats['dps'], integer=True)}/s"],
            ["相对未联动提升", f"{total_gain:+.2f}%"],
            ["移除全部队友效果损失", f"{total_loss:+.2f}%"],
        ]
        overview_table = self._team_detail_text_table(["时间轴项目", "计算结果"], overview_rows, [240, 260], 1)
        overview_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        overview_table.setMaximumHeight(6 * EDIT_ROW_HEIGHT + 42)
        layout.addWidget(overview_table)

        member_box = QGroupBox(f"队员移除损失（{len(member_rows)}）")
        member_layout = QVBoxLayout(member_box)
        member_layout.setContentsMargins(8, 10, 8, 8)
        member_table = self._team_detail_text_table(
            ["移除队员", "移除后平均输出", "移除后DPS", "时间轴损失%", "DPS损失"],
            member_rows,
            [180, 180, 150, 140, 140],
            1,
        )
        member_layout.addWidget(member_table)
        layout.addWidget(member_box, 1)

        effect_box = QGroupBox("单个 Buff / 减益移除损失（按需计算）")
        effect_layout = QHBoxLayout(effect_box)
        effect_layout.setContentsMargins(8, 10, 8, 8)
        effect_combo = QComboBox()
        effect_combo.setEditable(False)
        effect_combo.setMinimumHeight(EDIT_ROW_HEIGHT)
        effect_combo.setMinimumWidth(360)
        for option in effect_options:
            effect_combo.addItem(norm(option.get("label")), option)
        effect_combo.view().setMinimumWidth(option_width([item.get("label") for item in effect_options], 360, 620))
        calculate_button = QPushButton("计算移除损失")
        calculate_button.setMinimumHeight(EDIT_ROW_HEIGHT)
        effect_result = QLabel("选择一个效果后点击计算。")
        effect_result.setObjectName("inlineHint")
        effect_result.setWordWrap(True)
        effect_layout.addWidget(effect_combo, 1)
        effect_layout.addWidget(calculate_button)
        effect_layout.addWidget(effect_result, 2)
        layout.addWidget(effect_box)

        def calculate_selected_effect_loss() -> None:
            option = effect_combo.currentData()
            if not isinstance(option, dict):
                effect_result.setText("当前配队没有可计算的 Buff 或减益。")
                return
            progress = QProgressDialog("正在重新计算移除后的时间轴...", "", 0, 0, dialog)
            progress.setWindowTitle("计算单个效果损失")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.NonModal)
            progress.setMinimumDuration(0)
            progress.show()
            QApplication.processEvents()
            try:
                reduced_context = self._team_practical_runtime_context(
                    metrics,
                    max(30.0, as_number(full_result.get("window"), window)),
                    excluded_effect=(norm(option.get("provider")), norm(option.get("skill"))),
                    optimize_aura=False,
                )
                reduced_result = self._team_practical_cached_simulate(
                    metrics,
                    reduced_context,
                    self._team_practical_manual_sequence,
                )
                reduced_stats = self._team_practical_result_stats(reduced_result)
                loss_percent = (
                    (full_stats["total"] - reduced_stats["total"]) / full_stats["total"] * 100.0
                    if full_stats["total"] > 0
                    else 0.0
                )
                dps_loss = full_stats["dps"] - reduced_stats["dps"]
                effect_result.setText(
                    f"{option.get('type')}｜{option.get('skill')}｜覆盖 {option.get('coverage')}　"
                    f"移除后 {self._percent_text(reduced_stats['average'])}/s，"
                    f"DPS {number_text(reduced_stats['dps'], integer=True)}/s　"
                    f"损失 {loss_percent:+.2f}% / {dps_loss:+,.0f} DPS"
                )
            finally:
                progress.close()

        calculate_button.clicked.connect(calculate_selected_effect_loss)
        if not effect_options:
            effect_combo.setEnabled(False)
            calculate_button.setEnabled(False)
            effect_result.setText("当前配队没有可计算的 Buff 或减益。")

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.button(QDialogButtonBox.Close).setText("关闭")
        close_buttons.rejected.connect(dialog.close)
        layout.addWidget(close_buttons)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _team_practical_restore_auto(self) -> None:
        self._team_practical_manual_sequence = None
        self._team_practical_selected_index = -1
        self.schedule_refresh()

    def _team_practical_move_segment(self, from_index: int, to_index: int) -> None:
        sequence = [
            norm(segment.get("skill"))
            for segment in getattr(self, "_team_practical_last_segments", [])
            if norm(segment.get("skill")) and not segment.get("timeline_hidden")
        ]
        if not (0 <= from_index < len(sequence) and 0 <= to_index < len(sequence)):
            return
        skill = sequence.pop(from_index)
        sequence.insert(to_index, skill)
        self._team_practical_manual_sequence = sequence
        self._team_practical_selected_index = to_index
        self.schedule_refresh()

    def _team_practical_select_segment(self, index: int) -> None:
        self._team_practical_selected_index = index
        table = getattr(self, "team_practical_detail_table", None)
        if isinstance(table, QTableWidget) and 0 <= index < table.rowCount():
            table.setCurrentCell(index, 2)

    def _team_practical_detail_combo_changed(self, row: int, text: str) -> None:
        sequence = [
            norm(segment.get("skill"))
            for segment in getattr(self, "_team_practical_last_segments", [])
            if norm(segment.get("skill")) and not segment.get("timeline_hidden")
        ]
        if not (0 <= row < len(sequence)):
            return
        choice = norm(text)
        if choice == "← 上移一位" and row > 0:
            sequence[row - 1], sequence[row] = sequence[row], sequence[row - 1]
            self._team_practical_selected_index = row - 1
        elif choice == "→ 下移一位" and row + 1 < len(sequence):
            sequence[row + 1], sequence[row] = sequence[row], sequence[row + 1]
            self._team_practical_selected_index = row + 1
        elif choice == "删除此项":
            sequence.pop(row)
            self._team_practical_selected_index = min(row, len(sequence) - 1)
        elif choice and choice != sequence[row]:
            sequence[row] = choice
            self._team_practical_selected_index = row
        else:
            return
        self._team_practical_manual_sequence = sequence
        self.schedule_refresh()

    def _team_practical_special_schedule(
        self,
        effects: list[dict[str, Any]],
        window: float,
    ) -> dict[str, Any]:
        skills = {self._team_effect_skill_name(effect.get("skill")) for effect in effects}
        burst_intervals: list[tuple[float, float]] = []
        if "精神爆发" in skills:
            burst_intervals = self._skill_long_burst_intervals_for_window(window)
        accel_intervals: list[tuple[float, float]] = []
        if "时间加速" in skills:
            accel_intervals = self._skill_long_accel_intervals_for_window(window)
        return {
            "window": window,
            "burst": burst_intervals,
            "accel": accel_intervals,
            "cocktail": "鸡尾酒" in skills,
        }

    def _team_practical_runtime_context(
        self,
        metrics: list[dict[str, Any]],
        window: float,
        *,
        excluded_provider: str = "",
        excluded_effect: tuple[str, str] | None = None,
        optimize_aura: bool = True,
    ) -> dict[str, Any]:
        team_effects = self._team_practical_selected_effects(
            metrics,
            excluded_provider=excluded_provider,
            excluded_effect=excluded_effect,
            optimize_aura=optimize_aura,
        )
        return self._team_practical_context_from_effects(team_effects, window)

    def _team_practical_context_from_effects(
        self,
        team_effects: list[dict[str, Any]],
        window: float,
    ) -> dict[str, Any]:
        duplicate_build_buffs = self._team_duplicate_build_buff_names(team_effects, self.collect_build())
        schedule = self._team_practical_special_schedule(team_effects, window)
        team_windows = self._team_practical_effect_windows(team_effects, window)
        for effect in team_effects:
            skill = self._team_effect_skill_name(effect.get("skill"))
            display_attrs = self._team_practical_display_attrs(effect)
            if skill not in {"精神爆发", "时间加速", "鸡尾酒"} or not display_attrs:
                continue
            if skill == "精神爆发":
                intervals = schedule["burst"]
            elif skill == "时间加速":
                intervals = schedule["accel"]
            else:
                intervals = [(0.0, window)] if schedule["cocktail"] else []
            attrs_text = attr_summary(display_attrs, limit=999)
            _effect_type, effect_category = self._team_practical_effect_type(effect)
            for start, end in intervals:
                team_windows.append({
                    "name": "｜".join(filter(None, [skill, attrs_text])),
                    "start": start,
                    "end": end,
                    "color": SKILL_BURST_EFFECT_COLORS[effect_category],
                    "detail": f"与长时间实战模拟同步；{attrs_text}",
                    "category": effect_category,
                    "source": skill,
                    "team_effect": effect,
                })
        current_provider = norm(getattr(self, "_team_practical_current_provider", ""))
        own_special_skills = {
            self._team_effect_skill_name(effect.get("skill"))
            for effect in team_effects
            if norm(effect.get("provider")) == current_provider
            and self._team_effect_skill_name(effect.get("skill"))
            in {"精神爆发", "时间加速"}
        }
        support_initial_times = {
            skill: intervals[0][0]
            for skill, intervals in (
                ("精神爆发", schedule["burst"]),
                ("时间加速", schedule["accel"]),
            )
            if skill in own_special_skills and intervals
        }
        scheduled_own_special_skills = set(support_initial_times)
        return {
            "effects": team_effects,
            "schedule": schedule,
            "windows": team_windows,
            "provider": current_provider,
            "duplicate_build_buffs": duplicate_build_buffs,
            "support_initial_times": support_initial_times,
            "pinned_special_skill_keys": scheduled_own_special_skills,
        }

    def _team_practical_build_buff_replacement_multiplier(self, duplicate_names: set[str]) -> float:
        if not duplicate_names:
            return 1.0
        original_summary = getattr(self, "_skill_burst_base_summary", {})
        original_panel = max(0.0, as_number(original_summary.get("effective_panel")))
        if original_panel <= 0:
            return 1.0
        try:
            build = self.collect_build()
            build["buffs"] = [
                item
                for item in build.get("buffs", [])
                if self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill")))
                not in duplicate_names
            ]
            _rows, clean_summary = self.calculator.calculate(build)
            clean_summary = self._team_apply_current_mixed_element_panel(dict(clean_summary), {}, build)
            return max(0.0, as_number(clean_summary.get("effective_panel")) / original_panel)
        except Exception:
            return 1.0

    def _team_practical_simulate_legacy(
        self,
        metrics: list[dict[str, Any]],
        context: dict[str, Any],
        manual_sequence: list[str] | None = None,
    ) -> dict[str, Any]:
        sentinel = object()
        saved_schedule = getattr(self, "_skill_burst_schedule_override", sentinel)
        saved_external = getattr(self, "_skill_burst_external_team_effect_windows", sentinel)
        saved_provider = getattr(self, "_skill_burst_current_provider_override", sentinel)
        saved_duplicate_buffs = getattr(self, "_team_practical_duplicate_build_buffs", sentinel)
        saved_base_replacement = getattr(self, "_skill_burst_team_base_replacement_multiplier", sentinel)
        saved_summary_cache_active = getattr(self, "_team_summary_effect_cache_active", sentinel)
        saved_summary_cache = getattr(self, "_team_summary_effect_cache", sentinel)
        saved_static_signature = getattr(self, "_team_summary_static_build_signature", sentinel)
        saved_support_initial_times = getattr(self, "_skill_burst_support_initial_times", sentinel)
        saved_pinned_special = getattr(self, "_skill_burst_pinned_special_skill_keys", sentinel)
        saved_periodic = copy.deepcopy(getattr(self, "_skill_burst_periodic_buffs", []))
        saved_candidates = copy.deepcopy(getattr(self, "_skill_burst_candidate_metrics", []))
        self._team_practical_panel_cache = {}
        self._team_practical_attack_panel_cache = {}
        self._team_practical_raw_attack_cache = {}
        self._team_summary_effect_cache_active = True
        self._team_summary_effect_cache = {}
        self._team_summary_static_build_signature = self.calculator._static_build_signature(self.build)
        manual_issues: list[str] = []
        try:
            duplicate_build_buffs = set(context.get("duplicate_build_buffs", set()))
            self._team_practical_duplicate_build_buffs = duplicate_build_buffs
            self._skill_burst_team_base_replacement_multiplier = (
                self._team_practical_build_buff_replacement_multiplier(duplicate_build_buffs)
            )
            self._skill_burst_schedule_override = context["schedule"]
            self._skill_burst_external_team_effect_windows = context["windows"]
            self._skill_burst_current_provider_override = context["provider"]
            self._skill_burst_support_initial_times = copy.deepcopy(
                context.get("support_initial_times", {})
            )
            self._skill_burst_pinned_special_skill_keys = set(
                context.get("pinned_special_skill_keys", set())
            )
            auto_segments, generated_window = self._update_skill_burst_sim_preview(metrics, render_ui=False)
            all_learned_metrics = self._skill_burst_all_learned_metrics(
                getattr(self, "_skill_burst_base_summary", {}),
                metrics,
            )
            metrics_by_skill = {
                self._skill_burst_skill_key(metric.get("skill")): metric
                for metric in all_learned_metrics
            }
            support_actions = self._skill_burst_support_actions(metrics_by_skill)
            auto_sequence = [
                norm(segment.get("skill"))
                for segment in auto_segments
                if norm(segment.get("skill")) and not segment.get("timeline_hidden")
            ]
            requested_sequence = [norm(skill) for skill in (manual_sequence or []) if norm(skill)]
            if not requested_sequence or requested_sequence == auto_sequence:
                segments = copy.deepcopy(auto_segments)
            else:
                segments, manual_issues = self._skill_burst_replay_manual_sequence(
                    requested_sequence,
                    all_learned_metrics,
                    support_actions,
                    generated_window,
                )
                segments.extend(
                    copy.deepcopy(segment)
                    for segment in auto_segments
                    if segment.get("timeline_hidden")
                )
                self._skill_burst_apply_periodic_total_damage_scores(segments, support_actions, generated_window)
                self._skill_burst_attach_mixed_attack_damage(segments)
            own_windows = self._skill_burst_effect_windows(segments, support_actions, generated_window)
        finally:
            if saved_schedule is sentinel:
                self.__dict__.pop("_skill_burst_schedule_override", None)
            else:
                self._skill_burst_schedule_override = saved_schedule
            if saved_external is sentinel:
                self.__dict__.pop("_skill_burst_external_team_effect_windows", None)
            else:
                self._skill_burst_external_team_effect_windows = saved_external
            if saved_provider is sentinel:
                self.__dict__.pop("_skill_burst_current_provider_override", None)
            else:
                self._skill_burst_current_provider_override = saved_provider
            if saved_duplicate_buffs is sentinel:
                self.__dict__.pop("_team_practical_duplicate_build_buffs", None)
            else:
                self._team_practical_duplicate_build_buffs = saved_duplicate_buffs
            if saved_base_replacement is sentinel:
                self.__dict__.pop("_skill_burst_team_base_replacement_multiplier", None)
            else:
                self._skill_burst_team_base_replacement_multiplier = saved_base_replacement
            if saved_summary_cache_active is sentinel:
                self.__dict__.pop("_team_summary_effect_cache_active", None)
            else:
                self._team_summary_effect_cache_active = saved_summary_cache_active
            if saved_summary_cache is sentinel:
                self.__dict__.pop("_team_summary_effect_cache", None)
            else:
                self._team_summary_effect_cache = saved_summary_cache
            if saved_static_signature is sentinel:
                self.__dict__.pop("_team_summary_static_build_signature", None)
            else:
                self._team_summary_static_build_signature = saved_static_signature
            if saved_support_initial_times is sentinel:
                self.__dict__.pop("_skill_burst_support_initial_times", None)
            else:
                self._skill_burst_support_initial_times = saved_support_initial_times
            if saved_pinned_special is sentinel:
                self.__dict__.pop("_skill_burst_pinned_special_skill_keys", None)
            else:
                self._skill_burst_pinned_special_skill_keys = saved_pinned_special
            self._skill_burst_periodic_buffs = saved_periodic
            self._skill_burst_candidate_metrics = saved_candidates
        return {
            "auto_segments": auto_segments,
            "segments": segments,
            "window": generated_window,
            "all_metrics": all_learned_metrics,
            "support_actions": support_actions,
            "candidate_metrics": copy.deepcopy(
                getattr(self, "_skill_burst_calculated_candidate_metrics", [])
            ),
            "available_skills": copy.deepcopy(
                getattr(self, "_skill_burst_calculated_available_skills", [])
            ),
            "own_windows": own_windows,
            "generated_effect_windows": copy.deepcopy(
                getattr(self, "_skill_burst_generated_effect_windows", [])
            ),
            "periodic_buffs": copy.deepcopy(
                getattr(self, "_skill_burst_periodic_buffs", [])
            ),
            "manual_issues": manual_issues,
        }

    def _team_practical_simulate(
        self,
        metrics: list[dict[str, Any]],
        context: dict[str, Any],
        manual_sequence: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._team_practical_simulate_pure(
                metrics,
                context,
                manual_sequence,
            )
        except Exception as exc:
            # Release builds retain the proven stateful implementation as a
            # last-resort guard. It is never used when the pure engine succeeds.
            self._timeline_engine_last_fallback = f"{type(exc).__name__}: {exc}"
            return self._team_practical_simulate_legacy(
                metrics,
                context,
                manual_sequence,
            )

    def _team_practical_simulate_pure(
        self,
        metrics: list[dict[str, Any]],
        context: dict[str, Any],
        manual_sequence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run personal-team and long timelines on an isolated data runtime."""
        runtime_effects = (
            self._selected_team_effects(use_base_cooldowns=True, dedup=True)
            if hasattr(self, "team_effect_table")
            else list(context.get("effects", []))
        )
        runtime = _ExtremeTimelineProxy(
            self,
            self.collect_build(),
            copy.deepcopy(runtime_effects),
            extreme_mode=False,
        )
        runtime._skill_burst_base_summary = copy.deepcopy(
            getattr(self, "_skill_burst_base_summary", {})
        )
        runtime._team_practical_panel_cache = {}
        runtime._team_practical_attack_panel_cache = {}
        runtime._team_practical_raw_attack_cache = {}
        runtime._team_summary_effect_cache_active = True
        runtime._team_summary_effect_cache = {}
        runtime._team_summary_static_build_signature = self.calculator._static_build_signature(
            runtime.build
        )
        duplicate_build_buffs = set(context.get("duplicate_build_buffs", set()))
        runtime._team_practical_duplicate_build_buffs = duplicate_build_buffs
        runtime._skill_burst_team_base_replacement_multiplier = (
            runtime._team_practical_build_buff_replacement_multiplier(
                duplicate_build_buffs
            )
        )
        runtime._skill_burst_schedule_override = copy.deepcopy(context["schedule"])
        runtime._skill_burst_external_team_effect_windows = copy.deepcopy(
            context["windows"]
        )
        runtime._skill_burst_current_provider_override = norm(context["provider"])
        runtime._skill_burst_support_initial_times = copy.deepcopy(
            context.get("support_initial_times", {})
        )
        runtime._skill_burst_pinned_special_skill_keys = set(
            context.get("pinned_special_skill_keys", set())
        )

        auto_segments, generated_window = runtime._update_skill_burst_sim_preview(
            copy.deepcopy(metrics),
            render_ui=False,
        )
        all_learned_metrics = runtime._skill_burst_all_learned_metrics(
            runtime._skill_burst_base_summary,
            copy.deepcopy(metrics),
        )
        metrics_by_skill = {
            runtime._skill_burst_skill_key(metric.get("skill")): metric
            for metric in all_learned_metrics
        }
        support_actions = runtime._skill_burst_support_actions(metrics_by_skill)
        auto_sequence = [
            norm(segment.get("skill"))
            for segment in auto_segments
            if norm(segment.get("skill")) and not segment.get("timeline_hidden")
        ]
        requested_sequence = [
            norm(skill) for skill in (manual_sequence or []) if norm(skill)
        ]
        manual_issues: list[str] = []
        if not requested_sequence or requested_sequence == auto_sequence:
            segments = copy.deepcopy(auto_segments)
        else:
            segments, manual_issues = runtime._skill_burst_replay_manual_sequence(
                requested_sequence,
                all_learned_metrics,
                support_actions,
                generated_window,
            )
            segments.extend(
                copy.deepcopy(segment)
                for segment in auto_segments
                if segment.get("timeline_hidden")
            )
            runtime._skill_burst_apply_periodic_total_damage_scores(
                segments,
                support_actions,
                generated_window,
            )
            runtime._skill_burst_attach_mixed_attack_damage(segments)
        own_windows = runtime._skill_burst_effect_windows(
            segments,
            support_actions,
            generated_window,
        )
        return {
            "auto_segments": auto_segments,
            "segments": segments,
            "window": generated_window,
            "all_metrics": all_learned_metrics,
            "support_actions": support_actions,
            "candidate_metrics": copy.deepcopy(
                getattr(runtime, "_skill_burst_calculated_candidate_metrics", [])
            ),
            "available_skills": copy.deepcopy(
                getattr(runtime, "_skill_burst_calculated_available_skills", [])
            ),
            "own_windows": own_windows,
            "generated_effect_windows": copy.deepcopy(
                getattr(runtime, "_skill_burst_generated_effect_windows", [])
            ),
            "periodic_buffs": copy.deepcopy(
                getattr(runtime, "_skill_burst_periodic_buffs", [])
            ),
            "manual_issues": manual_issues,
            "snapshot_fingerprint": runtime.snapshot_fingerprint,
        }

    @staticmethod
    def _team_practical_cache_value(value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (str(key), BuildEditor._team_practical_cache_value(item))
                    for key, item in value.items()
                )
            )
        if isinstance(value, (list, tuple)):
            return tuple(BuildEditor._team_practical_cache_value(item) for item in value)
        if isinstance(value, set):
            return tuple(sorted(BuildEditor._team_practical_cache_value(item) for item in value))
        if isinstance(value, float):
            return round(value, 8)
        if value is None or isinstance(value, (str, int, bool)):
            return value
        return repr(value)

    def _team_practical_simulation_key(
        self,
        metrics: list[dict[str, Any]],
        context: dict[str, Any],
        manual_sequence: list[str] | None,
    ) -> Any:
        return self._team_practical_cache_value({
            "job": norm(self.second_job.currentText()),
            "summary": getattr(self, "_skill_burst_base_summary", {}),
            "skill_levels": self._skill_build_current_levels(),
            "mastery_levels": self._skill_mastery_current_levels(),
            "skill_glyphs": self._selected_skill_glyphs(),
            "metrics": metrics,
            "context": context,
            "manual_sequence": manual_sequence or [],
        })

    def _team_practical_cached_simulate(
        self,
        metrics: list[dict[str, Any]],
        context: dict[str, Any],
        manual_sequence: list[str] | None = None,
    ) -> dict[str, Any]:
        key = self._team_practical_simulation_key(metrics, context, manual_sequence)
        cache = getattr(self, "_team_practical_simulation_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._team_practical_simulation_cache = cache
        cached = cache.get(key)
        if isinstance(cached, dict):
            return copy.deepcopy(cached)
        result = self._team_practical_simulate(metrics, context, manual_sequence)
        cache[key] = copy.deepcopy(result)
        while len(cache) > 16:
            cache.pop(next(iter(cache)))
        return result

    def _team_practical_expected_panel(self) -> float:
        summary = getattr(self, "_skill_burst_base_summary", {})
        effective_panel = max(0.0, as_number(summary.get("effective_panel")))
        if effective_panel <= 0:
            return 0.0
        physical = max(0.0, as_number(summary.get("physical_average")))
        magic = max(0.0, as_number(summary.get("magic_average")))
        weighted_profile = self._skill_weighted_panel_profile()
        if weighted_profile:
            skill_panel = max(0.0, self._skill_weighted_panel_value(summary, weighted_profile))
        else:
            skill_panel = magic if self._job_profile().get("attack_attr") == "魔攻" else physical

        effective_row = self.calculator.effective.get(norm(self.second_job.currentText()), {})
        raw_total = 0.0
        weight_total = 0.0
        for index in range(1, 4):
            attack = norm(effective_row.get(f"攻击{index}"))
            weight = max(0.0, as_number(effective_row.get(f"权重{index}")))
            fixed = as_number(effective_row.get(f"固定{index}"))
            if not attack or weight <= 0:
                continue
            panel = (
                physical
                if attack == "物攻"
                else magic
                if attack == "魔攻"
                else physical + magic
                if attack == "双攻"
                else 0.0
            )
            raw_total += panel * weight / 100.0 + fixed
            weight_total += weight
        job_attack_panel = raw_total / (weight_total / 100.0) if weight_total > 0 else skill_panel

        mixed_weights = self._team_current_mixed_element_weights(summary)
        if mixed_weights:
            weight_total = sum(max(0.0, as_number(weight)) for weight in mixed_weights.values())
            element_multiplier = (
                sum(
                    max(0.0, as_number(weight))
                    * self._skill_burst_element_multiplier(element, {})
                    for element, weight in mixed_weights.items()
                )
                / weight_total
                if weight_total > 0
                else 1.0
            )
        else:
            element_attr = norm(self._job_profile().get("element_attr"))
            element = {
                "火攻%": "火",
                "水攻%": "水",
                "光攻%": "光",
                "暗攻%": "暗",
            }.get(element_attr, "")
            element_multiplier = self._skill_burst_element_multiplier(element, {})
        combat_multiplier_without_element = (
            effective_panel / max(1e-9, job_attack_panel * element_multiplier)
        )
        return max(0.0, skill_panel * combat_multiplier_without_element)

    def _team_practical_result_stats(self, result: dict[str, Any]) -> dict[str, float]:
        window = max(1e-9, as_number(result.get("window")))
        total = sum(
            max(0.0, as_number(segment.get("effective_score")))
            for segment in result.get("segments", [])
        )
        average = total / window
        return {
            "window": window,
            "total": total,
            "average": average,
            "dps": self._team_practical_expected_panel() * average / 100.0,
        }

    def _update_team_practical_sim_preview(self, metrics: list[dict[str, Any]]) -> None:
        timeline = getattr(self, "team_practical_timeline", None)
        table = getattr(self, "team_practical_detail_table", None)
        tabs = getattr(self, "team_sim_tabs", None)
        if not isinstance(timeline, SkillBurstTimelineWidget) or not isinstance(table, QTableWidget):
            return
        if isinstance(tabs, QTabWidget) and tabs.currentIndex() != 1:
            return

        window_spin = getattr(self, "team_practical_window_spin", None)
        window = max(30.0, window_spin.value() if isinstance(window_spin, QDoubleSpinBox) else 180.0)
        context = self._team_practical_runtime_context(metrics, window)
        current_job = norm(self.second_job.currentText())
        if self._team_practical_manual_job != current_job:
            self._team_practical_manual_sequence = None
            self._team_practical_manual_job = current_job
        result = self._team_practical_cached_simulate(metrics, context, self._team_practical_manual_sequence)
        baseline_context = {**context, "windows": []}
        baseline_result = self._team_practical_cached_simulate(
            metrics,
            baseline_context,
            self._team_practical_manual_sequence,
        )
        self._team_practical_last_metrics = copy.deepcopy(metrics)
        self._team_practical_last_context = copy.deepcopy(context)
        self._team_practical_last_result = copy.deepcopy(result)
        self._team_practical_last_baseline_result = copy.deepcopy(baseline_result)
        team_windows = context["windows"]
        auto_segments = result["auto_segments"]
        segments = result["segments"]
        generated_window = result["window"]
        all_learned_metrics = result["all_metrics"]
        support_actions = result["support_actions"]
        own_windows = result["own_windows"]
        manual_issues = result["manual_issues"]
        self._team_practical_available_skills = unique(
            [
                self._skill_burst_skill_key(metric.get("skill"))
                for metric in all_learned_metrics
                if self._skill_burst_skill_key(metric.get("skill"))
            ]
            + [
                self._skill_burst_skill_key(action.get("skill"))
                for action in support_actions
                if self._skill_burst_skill_key(action.get("skill"))
            ]
            + ([self._skill_burst_reload_skill()] if self._skill_burst_reload_available() else [])
        )

        self._team_practical_auto_segments = copy.deepcopy(auto_segments)
        self._team_practical_last_segments = copy.deepcopy(segments)
        visible_auto = [segment for segment in auto_segments if not segment.get("timeline_hidden")]
        visible_segments = [segment for segment in segments if not segment.get("timeline_hidden")]
        timeline.set_comparison_timeline(
            generated_window,
            visible_auto,
            visible_segments,
            "个人技能自动轴 / 自选轴；下方常驻与间断轨道来自当前配队。",
        )
        timeline.set_effect_windows(
            self._team_practical_display_effect_windows(
                team_windows,
                own_windows,
                generated_window,
                norm(context.get("provider")),
            )
        )
        timeline.set_selected_index(self._team_practical_selected_index)
        timeline.adjustSize()
        self.team_practical_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        QTimer.singleShot(0, lambda: self.team_practical_scroll.verticalScrollBar().setValue(0))

        total = sum(max(0.0, as_number(segment.get("effective_score"))) for segment in segments)
        raw_total = sum(max(0.0, as_number(segment.get("raw_effective_score"))) for segment in segments)
        unlinked_total = sum(
            max(0.0, as_number(segment.get("effective_score")))
            for segment in baseline_result["segments"]
        )
        average = total / generated_window if generated_window > 0 else 0.0
        unlinked_average = unlinked_total / generated_window if generated_window > 0 else 0.0
        linked_channel_averages = self._skill_burst_channel_totals(segments, divisor=generated_window)
        unlinked_channel_averages = self._skill_burst_channel_totals(
            baseline_result["segments"],
            divisor=generated_window,
        )
        practical_dps = self._team_practical_expected_panel() * average / 100.0
        ignored_buff_note = ""
        duplicate_build_buffs = sorted(context.get("duplicate_build_buffs", set()))
        if duplicate_build_buffs:
            ignored_buff_note = (
                '<span style="color:#b45309;">&nbsp;|&nbsp;已停用配装同名Buff：'
                + "、".join(duplicate_build_buffs)
                + "</span>"
            )
        self.team_practical_total_label.setText(
            '<span style="color:#1e293b; font-weight:700;">个人配队实战平均输出：</span>'
            f'<span style="color:#64748b;">未联动：</span>{self._skill_burst_channels_html(unlinked_channel_averages)}；'
            f'<span style="color:#64748b;">平均 {self._percent_text(unlinked_average)}/s</span><br>'
            f'<span style="color:#166b54; font-weight:700;">配队联动：</span>{self._skill_burst_channels_html(linked_channel_averages)}；'
            f'<span style="color:#166b54; font-weight:700;">平均 {self._percent_text(average)}/s</span>'
            f'<span style="color:#0369a1; font-weight:700;">&nbsp;|&nbsp;配队实战DPS {number_text(practical_dps, integer=True)}/s</span>'
            f'<span style="color:#64748b;">&nbsp;|&nbsp;纯技能百分比 {self._percent_text(raw_total)}</span>'
            + ignored_buff_note
            + (f'<span style="color:#b45309;">&nbsp;|&nbsp;{manual_issues[0]}</span>' if manual_issues else "")
        )
        self.team_practical_total_label.setToolTip(
            "配队实战DPS = 剥离基础属性攻击倍率后的等效伤害面板 × 时间轴计入Buff/属性攻的平均输出%。"
            "属性攻只在时间轴百分比中计算一次。"
        )

        contribution_table = getattr(self, "team_practical_contribution_table", None)
        if isinstance(contribution_table, QTableWidget):
            mixed_damage = norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
            contribution_table.setHorizontalHeaderLabels(
                ["技能", "次数", "总伤害", "占比"]
                if mixed_damage
                else ["技能", "次数", "总输出", "占比"]
            )
            contributions: dict[str, dict[str, float]] = {}
            for segment in segments:
                output = max(
                    0.0,
                    as_number(
                        segment.get("actual_damage")
                        if mixed_damage
                        else segment.get("effective_score")
                    ),
                )
                if output <= 0:
                    continue
                skill = norm(segment.get("skill")) or "未知技能"
                current = contributions.setdefault(skill, {"count": 0.0, "output": 0.0})
                current["count"] += 1
                current["output"] += output
            rows = sorted(contributions.items(), key=lambda item: item[1]["output"], reverse=True)
            contribution_table.setRowCount(len(rows))
            for row, (skill, values) in enumerate(rows):
                contribution_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                self._set_summary_item(contribution_table, row, 0, skill)
                skill_item = contribution_table.item(row, 0)
                if skill_item is not None:
                    skill_item.setBackground(SkillBurstTimelineWidget.skill_cell_color(skill))
                self._set_summary_item(contribution_table, row, 1, str(int(values["count"])), align_right=True)
                self._set_summary_item(
                    contribution_table,
                    row,
                    2,
                    self._compact_damage_text(values["output"])
                    if mixed_damage
                    else self._percent_text(values["output"]),
                    align_right=True,
                )
                contribution_total = sum(values["output"] for _skill, values in rows)
                ratio = values["output"] / contribution_total * 100 if contribution_total > 0 else 0.0
                self._set_summary_item(contribution_table, row, 3, f"{ratio:.2f}%", align_right=True)

        table.setRowCount(len(segments))
        sequence_row = 0
        for row, segment in enumerate(segments):
            table.setRowHeight(row, EDIT_ROW_HEIGHT)
            start = max(0.0, as_number(segment.get("start")))
            end = max(start, as_number(segment.get("end")))
            output = max(0.0, as_number(segment.get("effective_score")))
            raw_output = max(0.0, as_number(segment.get("raw_effective_score")))
            dpa = output / max(1e-9, end - start) if output > 0 and end > start else 0.0
            self._set_summary_item(table, row, 0, str(row + 1), align_right=True)
            self._set_summary_item(table, row, 1, norm(segment.get("type")))
            skill = norm(segment.get("skill"))
            table.removeCellWidget(row, 2)
            if segment.get("timeline_hidden") or not skill:
                self._set_summary_item(table, row, 2, skill)
                skill_item = table.item(row, 2)
                if skill_item is not None:
                    skill_item.setBackground(SkillBurstTimelineWidget.skill_cell_color(skill))
            else:
                choices = unique([
                    skill,
                    "← 上移一位",
                    "→ 下移一位",
                    "删除此项",
                    *getattr(self, "_team_practical_available_skills", []),
                ])
                combo = Combo(choices, skill, min_width=110, contents_length=8)
                combo.setEditable(False)
                combo.view().setMinimumWidth(option_width(choices, 150, 360))
                color = SkillBurstTimelineWidget.skill_cell_color(skill).name()
                combo.setStyleSheet(
                    f"QComboBox {{ background:{color}; border:0; padding:0 22px 0 6px; }}"
                    "QComboBox::drop-down { width:20px; border:0; }"
                )
                edit_row = sequence_row
                combo.currentTextChanged.connect(
                    lambda value, target_row=edit_row: self._team_practical_detail_combo_changed(target_row, value)
                )
                table.setCellWidget(row, 2, combo)
                sequence_row += 1
            self._set_summary_item(table, row, 3, f"{start:.2f}s", align_right=True)
            self._set_summary_item(table, row, 4, f"{end:.2f}s", align_right=True)
            self._set_summary_item(table, row, 5, self._percent_text(raw_output) if raw_output > 0 else "-", align_right=True)
            if as_number(segment.get("actual_damage")) > 0:
                output_text = self._compact_damage_text(segment.get("actual_damage"))
                if as_number(segment.get("actual_dpa")) > 0:
                    output_text += f" / {self._compact_damage_text(segment.get('actual_dpa'))}/s"
            else:
                output_text = self._percent_text(output) if output > 0 else "-"
                if dpa > 0:
                    output_text += f" / {self._percent_text(dpa)}/s"
            self._set_summary_item(table, row, 6, output_text, align_right=True)
            detail_item = table.item(row, 6)
            if detail_item is not None and norm(segment.get("detail")):
                detail_item.setToolTip(norm(segment.get("detail")))
        if 0 <= self._team_practical_selected_index < len(visible_segments):
            table.setCurrentCell(self._team_practical_selected_index, 2)
