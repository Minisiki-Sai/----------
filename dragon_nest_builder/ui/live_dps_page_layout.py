from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *

class LiveDpsLayoutMixin:
    def __init__(
        self,
        store: DataStore | None = None,
        parent: QWidget | None = None,
        *,
        timeline_editor: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store or DataStore.load()
        self.timeline_editor = timeline_editor
        self._party_card_estimator = ProfilePanelCalculator(
            self.store, BuildCalculator(self.store)
        )
        self._live_buff_rows = list(self.store.tables.get("buff", []))
        self._live_buff_display_catalog = _live_buff_display_catalog()
        self._live_buff_rows.extend(
            {
                "技能": row["skill"],
                "实时显示": row["display"],
                "实时分类": row["category"],
                "实时范围": row["scope"],
            }
            for row in self._live_buff_display_catalog.values()
        )

        self._live_buff_rows_by_skill: dict[str, list[dict[str, object]]] = (
            defaultdict(list)
        )
        for row in self._live_buff_rows:
            self._live_buff_rows_by_skill[_effect_skill_key(row.get("技能"))].append(
                row
            )
        self._live_buff_effect_cache: dict[tuple[object, ...], str] = {}
        profile = resource_path(Path("dragon_nest_builder/data/live_dps_profiles/v576.json"))
        self.collector = LiveDpsCollector(profile)
        self._live_dps_settings = QSettings(
            "DragonNestBuildSimulator", "DragonNestBuildSimulator"
        )
        self.score_engine = TeamScoreEngine(self.store)
        self._team_score_disk_cache = TeamScoreDiskCache()
        self._team_score_rows: tuple[PlayerScore, ...] = ()
        self._team_score_revision = -1
        self._team_score_actual_axis_signature: tuple[object, ...] = ()
        self._team_score_theory_signature: tuple[object, ...] | None = None
        self._team_score_theory_plans: dict[str, dict[str, Any]] = {}
        self._team_score_theory_jobs: tuple[str, ...] = ()
        self._team_score_theory_built_at = 0.0
        self._team_score_theory_error = ""
        self._team_score_theory_plan_cache: dict[
            tuple[object, ...], dict[str, dict[str, Any]]
        ] = {}
        self._team_score_build_override_cache: dict[
            tuple[object, ...], dict[str, Any]
        ] = {}
        self._team_score_rows_cache: dict[
            tuple[object, ...], tuple[PlayerScore, ...]
        ] = {}
        self._team_score_hp_metric_cache_key: tuple[object, ...] | None = None
        self._team_score_hp_metric_segments: tuple[dict[str, object], ...] = ()
        self._team_score_glyph_overrides_by_job: dict[str, list[dict[str, Any]]] = {}
        self._team_score_glyph_loading = False
        self._team_score_glyph_pending_job = ""
        self._team_score_selected_source_id = ""
        self._team_score_selected_boss_key = "__all__"
        self._team_score_boss_options: dict[str, frozenset[str]] = {}
        self._team_score_boss_names: dict[str, str] = {}
        self._team_score_boss_windows: dict[
            str, tuple[tuple[float, float], ...]
        ] = {}
        self._team_score_boss_signature: tuple[object, ...] = ()
        self._team_score_selected_boss_preset_key = "__auto__"
        self._team_score_auto_boss_preset_name = _TEAM_SCORE_DEFAULT_BOSS_PRESET
        self._team_score_theory_scope: tuple[str, ...] = ()
        self._team_score_rendered_boss_key = ""
        self._team_score_rendered_boss_preset_name = ""
        self._team_score_refresh_running = False
        self._team_score_refresh_pending = False
        self._team_score_refresh_timer = QTimer(self)
        self._team_score_refresh_timer.setSingleShot(True)
        self._team_score_refresh_timer.setInterval(120)
        self._team_score_refresh_timer.timeout.connect(
            self._run_pending_team_score_refresh
        )
        self._selected_source_id = ""
        self._capture_active = False
        self._capture_started_at = 0.0
        self._capture_error_shown = False
        self._auto_capture_probe: V576AutoCaptureProbe | None = None
        self._auto_capture_next_check = 0.0
        self._auto_capture_paused_until = 0.0
        self._timeline_events: tuple[CombatEvent | SkillCastEvent, ...] = ()
        self._timeline_using_casts = False
        self._timeline_damage_fallback_count = 0
        self._timeline_player_signature: tuple[tuple[str, str, str], ...] = ()
        self._timeline_skill_signature: tuple[tuple[str, str, str], ...] = ()
        self._timeline_snapshot_event_sources: tuple[object, object] | None = None
        self._timeline_player_details: dict[str, tuple[str, str]] = {}
        self._timeline_target_details: dict[str, str] = {}
        self._profession_player_order: dict[str, list[str]] = {}
        self._boss_target_order: list[tuple[str, str]] = []
        self._last_ui_refresh_at = 0.0
        self._ui_refresh_pending = False
        self._ui_refresh_scheduled = False
        self._refresh_running = False
        self._rendered_view_revisions: dict[tuple[int, int], int] = {}
        self._view_progress_widgets: dict[
            str, tuple[QWidget, QLabel, QProgressBar]
        ] = {}
        self._v410_view_progress_loading = False
        self._v410_view_progress_value = 0
        self._v410_view_progress_label = ""
        self._v410_replay_loading = False
        self._summary_snapshot_revision = -1
        self._summary_snapshot: CombatSnapshot | None = None
        self._event_snapshot_revision = -1
        self._event_snapshot: CombatSnapshot | None = None
        self._latest_snapshot: CombatSnapshot | None = None
        self._latest_effect_seconds = 0.0
        self._player_buff_selected_seconds: float | None = None
        self._detail_refresh_generation = 0
        self._layout_finalize_scheduled = False
        self._timeline_worker_warmup_started = False
        self._player_buff_filter_options_signature: tuple[object, ...] = ()
        self._boss_stats_buff_filter_options_signature: tuple[object, ...] = ()
        self._boss_effect_overview_target_signature: tuple[object, ...] = ()
        self._boss_effect_overview_groups_by_key: dict[
            str, _BossStatisticsGroup
        ] = {}
        self._damage_curve_boss_phases: tuple[
            tuple[str, tuple[tuple[float, float], ...]], ...
        ] = ()
        self._boss_stats_groups: tuple[_BossStatisticsGroup, ...] = ()
        self._boss_stats_groups_revision = -1
        self._boss_hp_context_revision = -1
        self._completed_boss_group_keys: frozenset[tuple[str, str]] = frozenset()
        self._fixed_boss_hp_profile_label = ""
        self._fixed_boss_hp_references: dict[str, tuple[int, ...]] = {}
        self._boss_effect_output_windows_revision = -1
        self._boss_effect_output_windows_signature: tuple[object, ...] = ()
        self._boss_effect_output_windows_by_target: dict[
            str, tuple[tuple[float, float], ...]
        ] = {}
        self._boss_freeze_rows_revision = -1
        self._boss_freeze_rows_signature: tuple[object, ...] = ()
        self._boss_freeze_rows: tuple[BossEffectSnapshot, ...] = ()
        self._boss_stats_group_signature: tuple[object, ...] = ()
        self._boss_stats_selected_keys: set[tuple[str, str]] = set()
        self._boss_stats_selector_checks: dict[
            tuple[str, str], QCheckBox
        ] = {}
        self._boss_stats_realtime_seconds: float | None = None
        self._boss_stats_realtime_follow_latest = True
        self._boss_stats_pending_hover_seconds: float | None = None
        self._boss_stats_hover_update_scheduled = False
        self._boss_stats_realtime_windows: tuple[tuple[float, float], ...] = ()
        self._boss_stats_realtime_groups: tuple[_BossStatisticsGroup, ...] = ()
        self._boss_stats_realtime_hp_rows: tuple[
            tuple[
                str,
                str,
                tuple[tuple[tuple[float, float], ...], ...],
            ],
            ...,
        ] = ()
        self._boss_stats_timeline_events: tuple[
            CombatEvent | SkillCastEvent, ...
        ] = ()
        self._boss_stats_timeline_cache_key: tuple[object, ...] | None = None
        self._boss_stats_timeline_cache_value: tuple[
            tuple[CombatEvent | SkillCastEvent, ...], float
        ] = ((), 0.0)
        self._boss_stats_timeline_display_key: tuple[object, ...] | None = None
        self._boss_stats_timeline_table_signature: tuple[object, ...] | None = None
        self._boss_stats_player_details: dict[str, tuple[str, str]] = {}
        self._boss_stats_target_details: dict[str, str] = {}
        self._boss_stats_action_events: tuple[BossActionEvent, ...] = ()
        self._boss_stats_action_timeline_events: tuple[
            SkillCastEvent, ...
        ] = ()
        self._boss_stats_action_display_key: tuple[object, ...] | None = None
        self._boss_stats_action_table_signature: tuple[object, ...] | None = None
        self._replay_thread: QThread | None = None
        self._replay_worker: _ReplayLoadWorker | None = None
        self._replay_progress: QProgressDialog | None = None
        self._replay_cancel_event: threading.Event | None = None
        self._replay_loading_path: Path | None = None
        self._team_score_capture_path: Path | None = None
        self._team_score_sync_thread: QThread | None = None
        self._team_score_sync_worker: QObject | None = None
        self._team_score_sync_pending_job: TeamScoreSyncJob | None = None
        self._team_score_sync_dispatched_source = ""
        self._boss_stats_timeline_table_timer = QTimer(self)
        self._boss_stats_timeline_table_timer.setSingleShot(True)
        self._boss_stats_timeline_table_timer.setInterval(60)
        self._boss_stats_timeline_table_timer.timeout.connect(
            self._fill_boss_stats_timeline_table
        )
        self._boss_stats_action_table_timer = QTimer(self)
        self._boss_stats_action_table_timer.setSingleShot(True)
        self._boss_stats_action_table_timer.setInterval(60)
        self._boss_stats_action_table_timer.timeout.connect(
            self._fill_boss_stats_action_table
        )
        self._build_ui()
        self.live_dps_tabs.currentChanged.connect(self._live_dps_tab_changed)
        self.curve_tabs.currentChanged.connect(self._live_dps_curve_tab_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self.destroyed.connect(lambda *_args: self._shutdown_capture())
        self._refresh()


    def _create_view_progress(self, key: str) -> QWidget:
        host = QWidget()
        host.setObjectName("liveDpsViewProgress")
        progress_layout = QHBoxLayout(host)
        progress_layout.setContentsMargins(8, 4, 8, 4)
        progress_layout.setSpacing(10)
        label = QLabel("正在准备数据...")
        label.setMinimumWidth(210)
        label.setStyleSheet("color:#36556d; font-weight:600;")
        bar = QProgressBar()
        # Keep ten internal steps per displayed percentage.  Long-running
        # score stages can have dozens of exact axes inside a narrow visual
        # range; a 0..100 bar used to look frozen for several completed axes
        # even though the task counter was moving normally.
        bar.setRange(0, 1000)
        bar.setValue(0)
        bar.setTextVisible(True)
        bar.setFormat("%p%")
        bar.setMaximumHeight(18)
        progress_layout.addWidget(label)
        progress_layout.addWidget(bar, 1)
        host.setStyleSheet(
            "QWidget#liveDpsViewProgress {"
            "background:#eaf4f7; border:1px solid #bfd8e0; border-radius:4px;"
            "}"
        )
        host.hide()
        self._view_progress_widgets[key] = (host, label, bar)
        return host


    @staticmethod
    def _view_progress_key(tab_index: int) -> str:
        return {
            0: "statistics",
            1: "boss",
            2: "timeline",
            3: "score",
            4: "party_info",
        }.get(tab_index, "statistics")


    def _view_progress_label(self, tab_index: int) -> str:
        if tab_index == 0:
            labels = (
                "正在生成全场伤害统计与曲线...",
                "正在整理 Boss 实时状态...",
                "正在整理全队实时增益...",
            )
            return labels[min(max(0, self.curve_tabs.currentIndex()), 2)]
        if tab_index == 1:
            labels = (
                "正在统计 Boss 技能构成...",
                "正在生成 Boss 伤害曲线...",
                "正在整理 Boss 状态曲线与明细...",
                "正在整理 Boss 阶段实时增益...",
                "正在生成 Boss 全队技能时间轴...",
            )
            if self.boss_stats_tabs.currentIndex() == 5:
                return "正在生成 Boss 动作时间轴..."
            return labels[min(max(0, self.boss_stats_tabs.currentIndex()), 4)]
        if tab_index == 2:
            return "正在解析全队技能施放与命中记录..."
        if tab_index == 4:
            return "正在整理被动队伍身份与查看信息响应..."
        return "正在生成全队评分与理论时间轴..."


    def _set_view_progress(
        self,
        key: str,
        value: float,
        label_text: str,
    ) -> None:
        widgets = self._view_progress_widgets.get(key)
        if widgets is None:
            return
        host, label, bar = widgets
        self._v410_view_progress_loading = True
        logical_value = max(0.0, min(100.0, float(value)))
        self._v410_view_progress_value = logical_value
        self._v410_view_progress_label = label_text
        label.setText(label_text)
        bar.setValue(int(round(logical_value * 10.0)))
        host.show()
        host.repaint()
        label.repaint()
        bar.repaint()
        # Legacy scoring still contains synchronous orchestration around worker
        # processes. Yield briefly so the rest of the application remains usable.
        QApplication.processEvents()


    def _begin_view_progress(self, tab_index: int, label_text: str = "") -> None:
        active_key = self._view_progress_key(tab_index)
        for key, (host, _label, _bar) in self._view_progress_widgets.items():
            if key != active_key:
                host.hide()
        self._set_view_progress(
            active_key,
            3,
            label_text or self._view_progress_label(tab_index),
        )


    def _advance_view_progress(self, key: str, value: float, label_text: str) -> None:
        widgets = self._view_progress_widgets.get(key)
        if widgets is not None and widgets[0].isVisible():
            # Several score phases now overlap (baseline axes can enqueue
            # exact removal axes before the last baseline finishes).  A later
            # callback may therefore carry a numerically older phase value.
            # Progress is user-visible completed work and must never rewind;
            # a new operation still resets normally through
            # ``_begin_view_progress`` which calls ``_set_view_progress``
            # directly.
            current = max(
                0.0,
                float(getattr(self, "_v410_view_progress_value", 0) or 0),
            )
            self._set_view_progress(key, max(current, float(value)), label_text)


    def _finish_view_progress(self, key: str) -> None:
        widgets = self._view_progress_widgets.get(key)
        if widgets is None:
            return
        host, label, bar = widgets
        self._v410_view_progress_loading = False
        self._v410_view_progress_value = 100
        self._v410_view_progress_label = "计算完成"
        label.setText("计算完成")
        bar.setValue(bar.maximum())
        host.repaint()
        bar.repaint()

        def hide_if_complete() -> None:
            if bar.value() == bar.maximum():
                host.hide()

        QTimer.singleShot(650, hide_if_complete)


    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.status_label = QLabel("未连接")
        self.status_label.setObjectName("liveDpsStatus")
        self.capture_button = QPushButton("开始实时统计")
        self.capture_button.setToolTip(
            "只读捕获游戏网络数据；与游戏内占比对照时，请先同时重置两边统计"
        )
        self.capture_button.clicked.connect(self._toggle_capture)
        self.auto_capture_checkbox = QCheckBox("副本自动统计")
        self.auto_capture_checkbox.setToolTip(
            "低开销监听游戏网络活动；检测到配置表内的副本进图后才从进图边界开始统计"
        )
        self.auto_capture_checkbox.setChecked(
            bool(self._live_dps_settings.value(
                "live_dps/auto_capture_enabled", False, type=bool
            ))
        )
        self.auto_capture_checkbox.toggled.connect(
            self._auto_capture_enabled_changed
        )
        self.npcap_button = QPushButton()
        self.npcap_button.setToolTip(
            "检测实战统计所需的 Npcap；未安装时可选择安装包并启动安装"
        )
        self.npcap_button.clicked.connect(self._check_or_install_npcap)
        self.set_npcap_available(find_npcap_dll() is not None)
        self.replay_button = QPushButton("载入记录")
        self.replay_button.clicked.connect(self._load_replay)
        self.history_button = QPushButton("本地历史")
        self.history_button.setToolTip(
            "打开自动保存目录；完整事件、全职业汇总和技能汇总均保存在本机 SQLite 数据库中"
        )
        self.history_button.clicked.connect(self._open_history_folder)
        # Upload configuration lives in the main Settings menu. Keep the label
        # as an internal status sink so background upload reporting stays intact.
        self.score_sync_status_label = QLabel("", self)
        self.score_sync_status_label.setStyleSheet("color:#5f7688;")
        self.score_sync_status_label.setMaximumWidth(180)
        self.score_sync_status_label.hide()
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self._clear)
        self.total_label = QLabel("总伤害 0  |  0.0s")
        self.total_label.setObjectName("liveDpsTotal")
        toolbar.addWidget(self.status_label)
        toolbar.addWidget(self.capture_button)
        toolbar.addWidget(self.auto_capture_checkbox)
        toolbar.addWidget(self.npcap_button)
        toolbar.addWidget(self.replay_button)
        toolbar.addWidget(self.history_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.total_label)
        root.addLayout(toolbar)
        self._update_team_score_sync_status()
        QTimer.singleShot(1800, self._flush_team_score_offline_queue)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.players_table = QTableWidget(0, 10)
        self.players_table.setHorizontalHeaderLabels(
            [
                "排名",
                "角色",
                "职业",
                "总伤害",
                "占比",
                "DPS",
                "暴击率",
                "治疗",
                "承伤",
                "战斗时间",
            ]
        )
        self.players_table.horizontalHeaderItem(4).setToolTip(
            "该角色总伤害 / 当前统计角色总伤害"
        )
        self.players_table.horizontalHeaderItem(6).setToolTip(
            "按每个伤害 hit 统计：暴击 hit 数 / 总 hit 数"
        )
        self.players_table.horizontalHeaderItem(8).setToolTip(
            "角色在本次记录中实际收到的伤害总量；不计入输出、治疗或 DPS。"
        )
        self.players_table.verticalHeader().setVisible(False)
        self.players_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.players_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.players_table.itemSelectionChanged.connect(self._player_changed)
        header = self.players_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        left_layout.addWidget(self.players_table, 3)
        damage_curve_page = QWidget()
        damage_curve_layout = QVBoxLayout(damage_curve_page)
        damage_curve_layout.setContentsMargins(0, 0, 0, 0)
        damage_curve_layout.setSpacing(2)
        self.curve = BossDamageCurve()
        self.curve.viewChanged.connect(self._damage_curve_view_changed)
        damage_curve_layout.addWidget(self.curve, 1)
        damage_zoom_row = QHBoxLayout()
        damage_zoom_row.setContentsMargins(2, 0, 2, 0)
        self.damage_curve_zoom_slider = QSlider(Qt.Horizontal)
        self.damage_curve_zoom_slider.setRange(2, 45)
        self.damage_curve_zoom_slider.setValue(30)
        self.damage_curve_zoom_slider.setFixedWidth(180)
        self.damage_curve_zoom_value = QLabel("30 px/s")
        self.damage_curve_zoom_slider.valueChanged.connect(
            lambda value: self._curve_pixels_changed(
                self.curve, self.damage_curve_zoom_value, value
            )
        )
        damage_zoom_row.addWidget(QLabel("时间轴缩放"))
        damage_zoom_row.addWidget(self.damage_curve_zoom_slider)
        damage_zoom_row.addWidget(self.damage_curve_zoom_value)
        damage_zoom_row.addSpacing(14)
        damage_zoom_row.addWidget(QLabel("平滑"))
        self.damage_curve_smoothing = QComboBox()
        for label, seconds in (
            ("原始（1秒）", 1),
            ("3秒", 3),
            ("5秒", 5),
            ("10秒", 10),
        ):
            self.damage_curve_smoothing.addItem(label, seconds)
        self.damage_curve_smoothing.setCurrentIndex(2)
        self.damage_curve_smoothing.currentIndexChanged.connect(
            lambda _index: self.curve.set_smoothing_seconds(
                int(self.damage_curve_smoothing.currentData() or 5)
            )
        )
        damage_zoom_row.addWidget(self.damage_curve_smoothing)
        self.damage_curve_select_range = QCheckBox("区间选择")
        self.damage_curve_select_range.setToolTip(
            "启用后在曲线上拖动选择区间；关闭时拖动仍用于左右移动。"
        )
        self.damage_curve_select_range.toggled.connect(
            self.curve.set_range_selection_enabled
        )
        damage_zoom_row.addWidget(self.damage_curve_select_range)
        self.damage_curve_clear_range = QPushButton("清除区间")
        self.damage_curve_clear_range.clicked.connect(
            self.curve.clear_interval_selection
        )
        damage_zoom_row.addWidget(self.damage_curve_clear_range)
        damage_zoom_row.addSpacing(14)
        self.damage_curve_hp_check = QCheckBox("Boss剩余血量")
        self.damage_curve_hp_check.setChecked(True)
        self.damage_curve_hp_check.setToolTip(
            "显示主要 Boss 的剩余血量；优先使用服务器血量，已击杀记录按完整承伤归一化，"
            "未击杀记录按内置副本/难度固定血量计算。"
        )
        self.damage_curve_hp_check.toggled.connect(
            self.curve.set_boss_hp_visible
        )
        damage_zoom_row.addWidget(self.damage_curve_hp_check)
        damage_zoom_row.addStretch(1)
        damage_curve_layout.addLayout(damage_zoom_row)
        self.damage_curve_interval_summary = QLabel("区间：未选择")
        self.damage_curve_interval_summary.setStyleSheet(
            "color: #355866; padding: 2px 6px; background: #eef6f8;"
        )
        self.curve.intervalSelectionChanged.connect(
            lambda _start, _end, summary: self.damage_curve_interval_summary.setText(
                summary
            )
        )
        damage_curve_layout.addWidget(self.damage_curve_interval_summary)
        damage_curve_range = QHBoxLayout()
        damage_curve_range.setContentsMargins(2, 0, 2, 0)
        damage_curve_range.setSpacing(8)
        self.damage_curve_range_start = QLabel("0.0s")
        self.damage_curve_range_start.setMinimumWidth(58)
        self.damage_curve_range_start.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.damage_curve_scrollbar = QScrollBar(Qt.Horizontal)
        self.damage_curve_scrollbar.setMinimumHeight(20)
        self.damage_curve_scrollbar.setTracking(True)
        self.damage_curve_scrollbar.setSingleStep(10)
        self.damage_curve_scrollbar.setToolTip("拖动滑块查看完整战斗伤害曲线")
        self.damage_curve_scrollbar.setStyleSheet(
            "QScrollBar:horizontal { min-height: 18px; background: #e7edf3; }"
            "QScrollBar::handle:horizontal { min-width: 44px; background: #4d9aaa; border-radius: 4px; }"
            "QScrollBar::handle:horizontal:hover { background: #287f92; }"
        )
        self.damage_curve_scrollbar.valueChanged.connect(
            self._damage_curve_scroll_changed
        )
        self.damage_curve_range_end = QLabel("总计 0.0s")
        self.damage_curve_range_end.setMinimumWidth(88)
        damage_curve_range.addWidget(self.damage_curve_range_start)
        damage_curve_range.addWidget(self.damage_curve_scrollbar, 1)
        damage_curve_range.addWidget(self.damage_curve_range_end)
        damage_curve_layout.addLayout(damage_curve_range)
        self.curve_tabs = QTabWidget()
        self.curve_tabs.addTab(damage_curve_page, "伤害曲线")
        boss_page = QWidget()
        boss_layout = QVBoxLayout(boss_page)
        boss_layout.setContentsMargins(4, 6, 4, 4)
        boss_layout.setSpacing(6)
        self.boss_effect_summary = QLabel("暂无 Boss 实时状态记录")
        self.boss_effect_summary.setWordWrap(True)
        self.boss_effect_summary.setObjectName("liveDpsBossSummary")
        boss_curve_controls = QWidget()
        boss_curve_controls_layout = QGridLayout(boss_curve_controls)
        boss_curve_controls_layout.setContentsMargins(0, 0, 0, 0)
        boss_curve_controls_layout.setHorizontalSpacing(12)
        boss_curve_controls_layout.setVerticalSpacing(2)
        self._boss_curve_checks: dict[str, QCheckBox] = {}
        for index, (key, label, color, _freeze) in enumerate(
            BossEffectCurve.SERIES_META
        ):
            check = QCheckBox(label)
            check.setChecked(False)
            check.setStyleSheet(f"QCheckBox {{ color: {color}; }}")
            check.stateChanged.connect(self._boss_effect_curve_selection_changed)
            boss_curve_controls_layout.addWidget(check, index // 4, index % 4)
            self._boss_curve_checks[key] = check
        zoom_label = QLabel("时间轴缩放")
        self.boss_curve_zoom_slider = QSlider(Qt.Horizontal)
        self.boss_curve_zoom_slider.setRange(2, 45)
        self.boss_curve_zoom_slider.setValue(30)
        self.boss_curve_zoom_value = QLabel("30 px/s")
        zoom_row = (len(BossEffectCurve.SERIES_META) + 3) // 4
        boss_curve_controls_layout.addWidget(zoom_label, zoom_row, 0)
        boss_curve_controls_layout.addWidget(
            self.boss_curve_zoom_slider, zoom_row, 1, 1, 2
        )
        boss_curve_controls_layout.addWidget(
            self.boss_curve_zoom_value, zoom_row, 3
        )
        for column in range(4):
            boss_curve_controls_layout.setColumnStretch(column, 1)
        self.boss_effect_curve = BossEffectCurve()
        self.boss_effect_curve.set_visible_keys(set())
        self.boss_effect_curve.viewChanged.connect(
            self._boss_effect_curve_view_changed
        )
        self.boss_curve_zoom_slider.valueChanged.connect(
            lambda value: self._curve_pixels_changed(
                self.boss_effect_curve, self.boss_curve_zoom_value, value
            )
        )
        self.boss_effect_curve.set_pixels_per_second(30)
        self.boss_effect_scrollbar = QScrollBar(Qt.Horizontal)
        self.boss_effect_scrollbar.setTracking(True)
        self.boss_effect_scrollbar.valueChanged.connect(
            lambda value: self.boss_effect_curve.set_view_start(value / 10.0)
        )
        self.boss_effect_range_start = QLabel("0.0s")
        self.boss_effect_range_end = QLabel("总计 0.0s")

        self.boss_effects_table = QTableWidget(0, 9)
        self.boss_effects_table.setHorizontalHeaderLabels(
            [
                "目标Boss",
                "归类",
                "效果",
                "来源技能",
                "来源职业",
                "数值/参数",
                "刷新次数",
                "实际生效",
                "有效覆盖率",
            ]
        )
        self.boss_effects_table.verticalHeader().setVisible(False)
        self.boss_effects_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.boss_effects_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.boss_effects_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        effect_header = self.boss_effects_table.horizontalHeader()
        effect_header.setMinimumSectionSize(36)
        effect_header.setSectionResizeMode(QHeaderView.Interactive)
        self.boss_effects_table.setToolTip(
            "默认汇总全部主要 Boss；同一筛选范围内效果、数值、来源技能和来源职业相同的记录合并统计。"
        )
        self.boss_effect_details_table = QTableWidget(0, 10)
        self.boss_effect_details_table.setHorizontalHeaderLabels(
            [
                "时间",
                "目标Boss",
                "状态",
                "归类",
                "效果",
                "来源技能",
                "来源角色",
                "数值/参数",
                "持续",
                "预计结束",
            ]
        )
        self.boss_effect_details_table.verticalHeader().setVisible(False)
        self.boss_effect_details_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.boss_effect_details_table.setSelectionBehavior(QTableWidget.SelectRows)
        detail_header = self.boss_effect_details_table.horizontalHeader()
        detail_header.setSectionResizeMode(QHeaderView.Interactive)
        detail_header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.boss_effect_details_table.setToolTip(
            "按发生时间从上到下显示主要 Boss 的每一次施加或刷新；"
            "底层状态与总览仍保留整场数据。"
        )
        boss_layout.addWidget(self.boss_effect_summary)
        boss_layout.addWidget(boss_curve_controls)
        boss_layout.addWidget(self.boss_effect_curve)
        boss_range = QHBoxLayout()
        boss_range.addWidget(self.boss_effect_range_start)
        boss_range.addWidget(self.boss_effect_scrollbar, 1)
        boss_range.addWidget(self.boss_effect_range_end)
        boss_layout.addLayout(boss_range)
        self.boss_detail_tabs = QTabWidget()
        boss_effect_overview_page = QWidget()
        boss_effect_overview_layout = QVBoxLayout(boss_effect_overview_page)
        boss_effect_overview_layout.setContentsMargins(0, 0, 0, 0)
        boss_effect_overview_layout.setSpacing(4)
        boss_effect_overview_filter = QHBoxLayout()
        boss_effect_overview_filter.setContentsMargins(2, 0, 2, 0)
        boss_effect_overview_filter.addWidget(QLabel("目标Boss"))
        self.boss_effect_overview_target_combo = QComboBox()
        self.boss_effect_overview_target_combo.setMinimumWidth(240)
        self.boss_effect_overview_target_combo.addItem("全部Boss", "__all__")
        self.boss_effect_overview_target_combo.setToolTip(
            "选择全部 Boss 时，覆盖率按所有主要 Boss 的有效输出时间加权汇总。"
        )
        self.boss_effect_overview_target_combo.currentIndexChanged.connect(
            self._boss_effect_overview_target_changed
        )
        boss_effect_overview_filter.addWidget(
            self.boss_effect_overview_target_combo
        )
        boss_effect_overview_filter.addStretch(1)
        boss_effect_overview_layout.addLayout(boss_effect_overview_filter)
        boss_effect_overview_layout.addWidget(self.boss_effects_table, 1)
        self.boss_detail_tabs.addTab(boss_effect_overview_page, "状态总览")
        self.boss_detail_tabs.addTab(self.boss_effect_details_table, "状态明细")
        self.boss_detail_tabs.currentChanged.connect(self._boss_detail_tab_changed)
        self.boss_damage_targets_table = QTableWidget(0, 4)
        self.boss_damage_targets_table.setHorizontalHeaderLabels(
            ["目标Boss", "伤害", "占队伍伤害", "命中"]
        )
        self.boss_damage_targets_table.verticalHeader().setVisible(False)
        self.boss_damage_targets_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.boss_damage_targets_table.setSelectionBehavior(QTableWidget.SelectRows)
        boss_damage_header = self.boss_damage_targets_table.horizontalHeader()
        boss_damage_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        boss_damage_header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.boss_detail_tabs.addTab(self.boss_damage_targets_table, "Boss伤害")
        boss_layout.addWidget(self.boss_detail_tabs, 1)
        self.curve_tabs.addTab(boss_page, "Boss实时状态")

        buff_page = QWidget()
        buff_layout = QVBoxLayout(buff_page)
        buff_layout.setContentsMargins(4, 6, 4, 4)
        buff_layout.setSpacing(6)
        self.player_buff_summary = QLabel("暂无全队实时增益记录")
        self.player_buff_summary.setWordWrap(True)
        self.player_buff_summary.setObjectName("liveDpsBossSummary")
        buff_curve_controls = QGridLayout()
        buff_curve_controls.setHorizontalSpacing(8)
        buff_curve_controls.setVerticalSpacing(4)
        buff_curve_controls.addWidget(QLabel("队员"), 0, 0)
        self.player_buff_target_combo = QComboBox()
        self.player_buff_target_combo.setMinimumWidth(150)
        self.player_buff_target_combo.currentIndexChanged.connect(
            self._player_buff_filter_changed
        )
        buff_curve_controls.addWidget(self.player_buff_target_combo, 0, 1)
        buff_curve_controls.addWidget(QLabel("目标职业"), 0, 2)
        self.player_buff_target_job_combo = QComboBox()
        self.player_buff_target_job_combo.setMinimumWidth(108)
        self.player_buff_target_job_combo.currentIndexChanged.connect(
            self._player_buff_filter_changed
        )
        buff_curve_controls.addWidget(self.player_buff_target_job_combo, 0, 3)
        buff_curve_controls.addWidget(QLabel("来源职业"), 0, 4)
        self.player_buff_source_job_combo = QComboBox()
        self.player_buff_source_job_combo.setMinimumWidth(108)
        self.player_buff_source_job_combo.currentIndexChanged.connect(
            self._player_buff_filter_changed
        )
        buff_curve_controls.addWidget(self.player_buff_source_job_combo, 0, 5)
        buff_curve_controls.addWidget(QLabel("增益类型"), 1, 0)
        self.player_buff_type_combo = QComboBox()
        self.player_buff_type_combo.setMinimumWidth(180)
        self.player_buff_type_combo.currentIndexChanged.connect(
            self._player_buff_filter_changed
        )
        buff_curve_controls.addWidget(self.player_buff_type_combo, 1, 1, 1, 3)
        buff_curve_controls.addWidget(QLabel("时间轴缩放"), 1, 4)
        buff_zoom_box = QWidget()
        buff_zoom_layout = QHBoxLayout(buff_zoom_box)
        buff_zoom_layout.setContentsMargins(0, 0, 0, 0)
        self.player_buff_zoom_slider = QSlider(Qt.Horizontal)
        self.player_buff_zoom_slider.setRange(2, 45)
        self.player_buff_zoom_slider.setValue(30)
        self.player_buff_zoom_value = QLabel("30 px/s")
        buff_zoom_layout.addWidget(self.player_buff_zoom_slider, 1)
        buff_zoom_layout.addWidget(self.player_buff_zoom_value)
        buff_curve_controls.addWidget(buff_zoom_box, 1, 5)
        buff_curve_controls.setColumnStretch(1, 2)
        buff_curve_controls.setColumnStretch(3, 1)
        buff_curve_controls.setColumnStretch(5, 1)
        self.player_buff_curve = PlayerBuffCurve()
        self.player_buff_curve.viewChanged.connect(
            self._player_buff_curve_view_changed
        )
        self.player_buff_curve.timeSelected.connect(
            self._player_buff_time_selected
        )
        self.player_buff_zoom_slider.valueChanged.connect(
            lambda value: self._curve_pixels_changed(
                self.player_buff_curve, self.player_buff_zoom_value, value
            )
        )
        self.player_buff_curve.set_pixels_per_second(30)
        self.player_buff_scrollbar = QScrollBar(Qt.Horizontal)
        self.player_buff_scrollbar.setTracking(True)
        self.player_buff_scrollbar.valueChanged.connect(
            lambda value: self.player_buff_curve.set_view_start(value / 10.0)
        )
        self.player_buff_range_start = QLabel("0.0s")
        self.player_buff_range_end = QLabel("总计 0.0s")
        self.player_buff_time_label = QLabel("明细时点：最新")
        self.player_buff_latest_button = QPushButton("回到最新")
        self.player_buff_latest_button.clicked.connect(
            self._player_buff_return_latest
        )
        self.player_buffs_table = QTableWidget(0, 7)
        self.player_buffs_table.setHorizontalHeaderLabels(
            [
                "目标",
                "目标职业",
                "归类",
                "增益",
                "技能",
                "来源职业",
                "覆盖率",
            ]
        )
        self.player_buffs_table.verticalHeader().setVisible(False)
        self.player_buffs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.player_buffs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.player_buffs_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        buff_header = self.player_buffs_table.horizontalHeader()
        buff_header.setMinimumSectionSize(36)
        buff_header.setSectionResizeMode(QHeaderView.Interactive)
        self.player_buff_details_table = QTableWidget(0, 10)
        self.player_buff_details_table.setHorizontalHeaderLabels(
            [
                "时间",
                "目标",
                "目标职业",
                "归类",
                "增益",
                "来源技能",
                "来源角色",
                "数值/参数",
                "生效区间",
                "剩余时间",
            ]
        )
        self.player_buff_details_table.verticalHeader().setVisible(False)
        self.player_buff_details_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.player_buff_details_table.setSelectionBehavior(QTableWidget.SelectRows)
        buff_detail_header = self.player_buff_details_table.horizontalHeader()
        buff_detail_header.setSectionResizeMode(QHeaderView.Interactive)
        buff_detail_header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.player_buff_tabs = QTabWidget()
        self.player_buff_tabs.addTab(self.player_buffs_table, "增益总览")
        self.player_buff_tabs.addTab(self.player_buff_details_table, "增益明细")
        self.player_buff_tabs.currentChanged.connect(self._player_buff_detail_tab_changed)
        buff_layout.addWidget(self.player_buff_summary)
        self.player_buff_summary.hide()
        buff_layout.addLayout(buff_curve_controls)
        # Match the Boss-status page: keep the curve compact and let the
        # overview/detail table receive the remaining vertical space.
        buff_layout.addWidget(self.player_buff_curve)
        buff_range = QHBoxLayout()
        buff_range.addWidget(self.player_buff_range_start)
        buff_range.addWidget(self.player_buff_scrollbar, 1)
        buff_range.addWidget(self.player_buff_range_end)
        buff_range.addSpacing(12)
        buff_range.addWidget(self.player_buff_time_label)
        buff_range.addWidget(self.player_buff_latest_button)
        buff_layout.addLayout(buff_range)
        buff_layout.addWidget(self.player_buff_tabs, 1)
        self.curve_tabs.addTab(buff_page, "实时增益")
        left_layout.addWidget(self.curve_tabs, 2)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        self.skill_title = QLabel("技能构成")
        self.skill_title.setObjectName("sectionTitle")
        self.skills_table = QTableWidget(0, 9)
        self.skills_table.setHorizontalHeaderLabels(
            [
                "技能",
                "伤害",
                "占比",
                "释放次数",
                "命中",
                "全目标平均Hit",
                "暴击",
                "暴击率",
                "治疗",
            ]
        )
        self.skills_table.horizontalHeaderItem(3).setToolTip(
            "优先使用实际施放包；缺失时仅接受可验证的连招/动作补全"
        )
        self.skills_table.horizontalHeaderItem(5).setToolTip(
            "全目标真实Hit ÷ 释放次数；不能直接作为单目标理论Hit达成率。没有可靠施放边界时显示 —"
        )
        self.skills_table.horizontalHeaderItem(7).setToolTip(
            "按每个伤害 hit 统计：暴击 hit 数 / 总 hit 数"
        )
        self.skills_table.verticalHeader().setVisible(False)
        self.skills_table.setEditTriggers(QTableWidget.NoEditTriggers)
        skill_header = self.skills_table.horizontalHeader()
        skill_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        skill_header.setSectionResizeMode(0, QHeaderView.Stretch)
        right_layout.addWidget(self.skill_title)
        right_layout.addWidget(self.skills_table, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.statistics_splitter = splitter

        statistics_page = QWidget()
        statistics_layout = QVBoxLayout(statistics_page)
        statistics_layout.setContentsMargins(0, 0, 0, 0)
        statistics_layout.setSpacing(4)
        statistics_layout.addWidget(self._create_view_progress("statistics"))
        statistics_layout.addWidget(splitter)

        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        timeline_layout.setContentsMargins(4, 6, 4, 4)
        timeline_layout.setSpacing(6)
        timeline_layout.addWidget(self._create_view_progress("timeline"))
        timeline_controls = QHBoxLayout()
        timeline_controls.setSpacing(8)
        timeline_controls.addWidget(QLabel("玩家"))
        self.timeline_player_combo = QComboBox()
        self.timeline_player_combo.setMinimumWidth(145)
        self.timeline_player_combo.setMaximumWidth(220)
        self.timeline_player_combo.addItem("全部玩家", "")
        self.timeline_player_combo.currentIndexChanged.connect(
            self._timeline_player_changed
        )
        timeline_controls.addWidget(self.timeline_player_combo)
        timeline_controls.addWidget(QLabel("技能"))
        self.timeline_skill_combo = MultiSelectSkillFilter()
        self.timeline_skill_combo.selectionChanged.connect(
            self._timeline_filter_changed
        )
        timeline_controls.addWidget(self.timeline_skill_combo)
        timeline_controls.addWidget(QLabel("时间轴缩放"))
        self.timeline_zoom_slider = QSlider(Qt.Horizontal)
        self.timeline_zoom_slider.setRange(2, 45)
        self.timeline_zoom_slider.setValue(30)
        self.timeline_zoom_slider.setSingleStep(1)
        self.timeline_zoom_slider.setPageStep(5)
        self.timeline_zoom_slider.setFixedWidth(180)
        self.timeline_zoom_value = QLabel("30 px/s")
        self.timeline_zoom_value.setMinimumWidth(58)
        timeline_controls.addWidget(self.timeline_zoom_slider)
        timeline_controls.addWidget(self.timeline_zoom_value)
        self.timeline_summary = QLabel("暂无技能记录")
        self.timeline_summary.setObjectName("liveDpsTimelineSummary")
        timeline_controls.addStretch(1)
        timeline_controls.addWidget(self.timeline_summary)
        timeline_layout.addLayout(timeline_controls)

        timeline_canvas = QWidget()
        timeline_canvas.setObjectName("liveDpsTimelineCanvas")
        timeline_canvas_layout = QVBoxLayout(timeline_canvas)
        timeline_canvas_layout.setContentsMargins(0, 0, 0, 0)
        timeline_canvas_layout.setSpacing(2)
        self.team_skill_timeline = TeamSkillTimeline(self.store)
        self.team_skill_timeline.viewChanged.connect(self._timeline_view_changed)
        self.timeline_zoom_slider.valueChanged.connect(
            self._timeline_zoom_changed
        )
        timeline_canvas_layout.addWidget(self.team_skill_timeline, 1)
        timeline_range_layout = QHBoxLayout()
        timeline_range_layout.setContentsMargins(2, 0, 2, 0)
        timeline_range_layout.setSpacing(8)
        self.timeline_range_start = QLabel("0.0s")
        self.timeline_range_start.setMinimumWidth(58)
        self.timeline_range_start.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.timeline_scrollbar = QScrollBar(Qt.Horizontal)
        self.timeline_scrollbar.setMinimumHeight(20)
        self.timeline_scrollbar.setTracking(True)
        self.timeline_scrollbar.setSingleStep(10)
        self.timeline_scrollbar.setToolTip("拖动滑块查看本场战斗的任意历史时间段")
        self.timeline_scrollbar.setStyleSheet(
            "QScrollBar:horizontal { min-height: 18px; background: #e7edf3; }"
            "QScrollBar::handle:horizontal { min-width: 44px; background: #7398b8; border-radius: 4px; }"
            "QScrollBar::handle:horizontal:hover { background: #4f7ea6; }"
        )
        self.timeline_scrollbar.valueChanged.connect(self._timeline_scroll_changed)
        self.timeline_range_end = QLabel("总计 0.0s")
        self.timeline_range_end.setMinimumWidth(88)
        timeline_range_layout.addWidget(self.timeline_range_start)
        timeline_range_layout.addWidget(self.timeline_scrollbar, 1)
        timeline_range_layout.addWidget(self.timeline_range_end)
        timeline_canvas_layout.addLayout(timeline_range_layout)

        self.timeline_events_table = QTableWidget(0, 8)
        self.timeline_events_table.setHorizontalHeaderLabels(
            ["时间", "玩家", "职业", "技能", "命中目标", "事件/伤害", "暴击", "来源"]
        )
        self.timeline_events_table.verticalHeader().setVisible(False)
        self.timeline_events_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.timeline_events_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.timeline_events_table.setSelectionMode(QTableWidget.SingleSelection)
        self.timeline_events_table.setAlternatingRowColors(True)
        self.timeline_events_table.setShowGrid(False)
        self.timeline_events_table.verticalHeader().setDefaultSectionSize(28)
        timeline_header = self.timeline_events_table.horizontalHeader()
        timeline_header.setMinimumSectionSize(54)
        timeline_header.setDefaultSectionSize(92)
        timeline_header.setSectionResizeMode(QHeaderView.Interactive)
        timeline_header.setSectionResizeMode(3, QHeaderView.Stretch)
        timeline_header.setSectionResizeMode(7, QHeaderView.Stretch)
        self.timeline_events_table.setColumnWidth(0, 82)
        self.timeline_events_table.setColumnWidth(1, 130)
        self.timeline_events_table.setColumnWidth(2, 84)
        self.timeline_events_table.setColumnWidth(4, 126)
        self.timeline_events_table.setColumnWidth(5, 100)
        self.timeline_events_table.setColumnWidth(6, 62)

        timeline_splitter = QSplitter(Qt.Vertical)
        timeline_splitter.setChildrenCollapsible(False)
        timeline_splitter.addWidget(timeline_canvas)
        timeline_splitter.addWidget(self.timeline_events_table)
        timeline_splitter.setStretchFactor(0, 3)
        timeline_splitter.setStretchFactor(1, 2)
        timeline_splitter.setSizes([390, 230])
        timeline_layout.addWidget(timeline_splitter, 1)

        self.live_dps_tabs = QTabWidget()
        self.live_dps_tabs.addTab(statistics_page, "全场伤害统计")
        self.live_dps_tabs.addTab(self._build_boss_statistics_page(), "Boss统计")
        self.live_dps_tabs.addTab(timeline_page, "全队技能时间轴")
        self.team_score_tab_index = self.live_dps_tabs.addTab(
            self._build_team_score_page(), "全队评分"
        )
        self.party_info_tab_index = self.live_dps_tabs.addTab(
            self._build_party_info_page(), "队伍/查看信息"
        )
        root.addWidget(self.live_dps_tabs, 1)


    def _build_party_info_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._create_view_progress("party_info"))

        note = QLabel(
            "队员列表只显示实时ID和职业，不显示角色名。装备、刻印、纹章、石板与其他信息只使用被动捕获并已验证的 TCP/UDP 证据；"
            "物品名称、职业限制、基础属性、强化属性、需求等级和说明来自 2026-08-11 的 v576 拆包，"
            "强化等级与石板当前附加属性来自查看信息响应。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            "color:#526779; background:#edf6f8; border:1px solid #c8dfe5; "
            "border-radius:4px; padding:5px 8px;"
        )
        layout.addWidget(note)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        roster_panel = QWidget()
        roster_layout = QVBoxLayout(roster_panel)
        roster_layout.setContentsMargins(0, 0, 0, 0)
        roster_layout.setSpacing(4)
        roster_title = QLabel("队伍成员")
        roster_title.setObjectName("liveDpsBossSummary")
        roster_layout.addWidget(roster_title)
        self.party_roster_table = QTableWidget(0, 2)
        self.party_roster_table.setHorizontalHeaderLabels(["实时ID", "职业"])
        self.party_roster_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.party_roster_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.party_roster_table.setSelectionMode(QTableWidget.SingleSelection)
        self.party_roster_table.verticalHeader().setVisible(False)
        roster_header = self.party_roster_table.horizontalHeader()
        roster_header.setSectionResizeMode(0, QHeaderView.Stretch)
        roster_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.party_roster_table.itemSelectionChanged.connect(
            self._party_member_selection_changed
        )
        roster_layout.addWidget(self.party_roster_table, 1)

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(4, 0, 0, 0)
        detail_layout.setSpacing(4)
        self.party_detail_title = QLabel("请选择队员")
        self.party_detail_title.setObjectName("liveDpsBossSummary")
        detail_layout.addWidget(self.party_detail_title)

        self.party_profile_tabs = QTabWidget()
        self.party_equipment_table = self._new_party_detail_table(
            ["部位", "装备与属性"]
        )
        self.party_engraving_table = self._new_party_detail_table(
            ["部位", "刻印与属性"]
        )
        self.party_glyph_table = self._new_party_detail_table(
            ["纹章槽", "纹章与效果"]
        )
        self.party_plate_table = self._new_party_detail_table(
            ["石板槽", "石板与效果"]
        )
        self.party_card_page = QWidget()
        party_card_layout = QVBoxLayout(self.party_card_page)
        party_card_layout.setContentsMargins(0, 0, 0, 0)
        party_card_layout.setSpacing(5)
        self.party_card_status = QLabel(
            "卡片档案与装备档案分开下发；未完成独立解密时不会用默认卡片冒充实战数据。"
        )
        self.party_card_status.setWordWrap(True)
        self.party_card_status.setStyleSheet("color:#526779; padding:2px 4px;")
        party_card_layout.addWidget(self.party_card_status)
        self.party_card_mastery_table = self._new_party_detail_table(
            ["掌握之力", "卡片属性", "初始合计", "掌握等级", "加成后合计"]
        )
        self.party_card_mastery_table.setMaximumHeight(390)
        party_card_layout.addWidget(self.party_card_mastery_table)
        self.party_card_table = self._new_party_detail_table(
            ["槽位", "卡片", "品质", "卡片等级", "重新封印次数", "实际效果"]
        )
        party_card_layout.addWidget(self.party_card_table, 1)
        self.party_talisman_table = self._new_party_detail_table(
            ["护符槽位", "护符与实际属性"]
        )
        self.party_passive_buff_table = self._new_party_detail_table(
            ["技能 Buff", "等级", "实际效果"]
        )
        self.party_other_info_table = self._new_party_detail_table(
            ["项目", "实际内容"]
        )
        self.party_profile_tabs.addTab(self.party_equipment_table, "装备")
        self.party_profile_tabs.addTab(self.party_engraving_table, "刻印")
        self.party_profile_tabs.addTab(self.party_glyph_table, "纹章")
        self.party_profile_tabs.addTab(self.party_plate_table, "石板")
        self.party_profile_tabs.addTab(self.party_card_page, "卡片")
        self.party_profile_tabs.addTab(self.party_talisman_table, "护符")
        self.party_profile_tabs.addTab(self.party_passive_buff_table, "技能 Buff")
        self.party_profile_tabs.addTab(self.party_other_info_table, "其他信息")
        detail_layout.addWidget(self.party_profile_tabs, 1)

        splitter.addWidget(roster_panel)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1100])
        layout.addWidget(splitter, 1)

        self._party_info_members: tuple[PartyMemberEvent, ...] = ()
        self._party_info_profiles: tuple[PlayerInspectEvent, ...] = ()
        self._party_info_selected_actor_id = ""
        return page


    def _new_party_detail_table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setMinimumSectionSize(72)
        for column in range(max(0, len(headers) - 1)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        if headers:
            header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        return table


    def _build_team_score_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(8)
        layout.addWidget(self._create_view_progress("score"))

        intro = QLabel(
            "统一评分基准：70L默认装备与首饰、默认加点、全L卡片、一键Buff和可调整的4块技能纹章；"
            "实战与理论使用相同Boss参数及输出窗口。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("liveDpsTimelineSummary")
        intro.setStyleSheet(
            "color:#29485e; background:#edf6f8; border:1px solid #c8dfe5; "
            "border-radius:4px; padding:5px 8px;"
        )
        intro.setToolTip(
            "理论轴施放或实战UDP捕获到同名Buff时，以时间区间效果为准，不重复计算配装Buff。"
            "实战施法会折算为标准化技能输出，再与相同Buff及输出窗口下的理论轴比较。"
        )
        layout.addWidget(intro)

        formula = QLabel(
            "输出：标准伤害70% + 爆发/加速30%（兑现率计分上限100%）    |    辅助：反事实团队收益完成率    |    "
            "输出分/辅助分以100为理论基准、表现更高时可超过100    |    "
            "单项超过100的计分点会等额挤压另一项上限    |    "
            "输出职业辅助占比：按本角色计分明细中的反事实理论收益，对全职业最高值归一化0%~50%    |    "
            "奶/T：职责40% + 全职业归一化辅助占比 + 剩余输出占比    |    "
            "承伤扣分：非死亡扣分按max(10分, 扣分前总分×20%)封顶；T承伤按其他职业三分之一计算，死亡每次扣10分    |    "
            "等级：SSS≥90、SS≥85、S≥80、A≥75、B≥70、C≥65、D≥60、E≥50、F<50"
        )
        formula.setWordWrap(True)
        formula.setStyleSheet("color:#526779; padding:0 6px 2px 6px;")
        formula.setToolTip(
            "辅助分使用反事实团队收益完成率。减CD/动作速度效果在无时序增益基准上独立加入并重排八人理论轴；"
            "普通增伤按封顶池总移除损失守恒分摊。普通Buff/Debuff的实际与理论收益固定使用同一实战伤害流和"
            "同一队友效果环境，只替换当前职业的效果日程。"
            "辅助占比只使用明细中标记为参与计分的理论收益；未学习、未确认、EX覆盖的普通版及其他备选效果不会暗中增加权重。"
            "单项完成率低于5%时保留明细但不参与辅助评分。"
            "承伤扣分先将1秒内的实际生命损失合并为机制波，并分类为全队机制、点名机制、"
            "可规避单人伤害或证据不足。全队与点名机制按受击者最大生命归一化，以中位数作为机制基线，"
            "只处罚超出基线的生命损失；证据不足时保守降权。生命损失严重度最多扣20分。"
            "受击前3秒有输出、受击后1.5秒停手、4.5秒内恢复输出，且理论轴在受击附近存在动作时，才核算操作损失，最多扣8分；"
            "两项合计按max(10分, 扣分前总分×20%)封顶。确认死亡每次另扣5分且不占封顶；"
            "T的生命损失与操作损失按其他职业的三分之一计算，封顶同步按相同比例计算，确认死亡每次扣10分。"
        )
        layout.addWidget(formula)

        # Keep the roster and the selected player's breakdown side by side.
        # A vertical split leaves almost no height for the output/support tables
        # once their timelines are visible on common 1080p displays.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        self.team_score_splitter = splitter
        self.team_score_table = QTableWidget(0, 8)
        self.team_score_table.setHorizontalHeaderLabels(
            [
                "等级",
                "角色",
                "职业",
                "定位",
                "总分",
                "输出分",
                "辅助分",
                "特色分/\n承伤扣分",
            ]
        )
        self.team_score_table.verticalHeader().setVisible(False)
        self.team_score_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.team_score_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.team_score_table.setSelectionMode(QTableWidget.SingleSelection)
        self.team_score_table.setAlternatingRowColors(True)
        self.team_score_table.setMinimumWidth(680)
        self.team_score_table.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )
        self.team_score_table.verticalHeader().setDefaultSectionSize(30)
        self.team_score_table.itemSelectionChanged.connect(
            self._team_score_selection_changed
        )
        score_header = self.team_score_table.horizontalHeader()
        score_header.setMinimumSectionSize(54)
        score_header.setDefaultAlignment(Qt.AlignCenter)
        score_header.setFixedHeight(42)
        score_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        score_header.setSectionResizeMode(0, QHeaderView.Fixed)
        score_header.resizeSection(0, 58)
        score_header.setSectionResizeMode(1, QHeaderView.Stretch)
        score_header.setSectionResizeMode(2, QHeaderView.Stretch)
        score_header.setSectionResizeMode(3, QHeaderView.Stretch)
        for column in range(4, 7):
            score_header.setSectionResizeMode(column, QHeaderView.Fixed)
            score_header.resizeSection(column, 72)
        score_header.setSectionResizeMode(7, QHeaderView.Fixed)
        score_header.resizeSection(7, 142)
        roster_panel = QWidget()
        roster_panel.setMinimumWidth(680)
        roster_panel.setMaximumWidth(900)
        roster_layout = QVBoxLayout(roster_panel)
        roster_layout.setContentsMargins(0, 0, 0, 0)
        roster_layout.setSpacing(6)
        roster_layout.addWidget(self.team_score_table, 1)

        glyph_editor = QWidget()
        glyph_editor.setObjectName("teamScoreGlyphEditor")
        glyph_editor.setStyleSheet(
            "#teamScoreGlyphEditor { background:#edf6f8; border:1px solid #c8dfe5; "
            "border-radius:4px; }"
        )
        glyph_layout = QVBoxLayout(glyph_editor)
        glyph_layout.setContentsMargins(8, 6, 8, 7)
        glyph_layout.setSpacing(5)
        glyph_header = QHBoxLayout()
        self.team_score_glyph_editor_title = QLabel("评分技能纹章：请选择队员")
        self.team_score_glyph_editor_title.setStyleSheet(
            "font-weight:700; color:#29485e; border:none; background:transparent;"
        )
        glyph_header.addWidget(self.team_score_glyph_editor_title, 1)
        self.team_score_glyph_reset_button = QPushButton("恢复自动")
        self.team_score_glyph_reset_button.setToolTip(
            "恢复该职业自动选择的4块评分纹章；默认不会选择幽灵之魂的增益持续纹章。"
        )
        self.team_score_glyph_reset_button.clicked.connect(
            self._reset_team_score_glyphs
        )
        self.team_score_glyph_reset_button.setEnabled(False)
        glyph_header.addWidget(self.team_score_glyph_reset_button)
        self.team_score_glyph_apply_button = QPushButton("应用纹章")
        self.team_score_glyph_apply_button.clicked.connect(
            self._apply_team_score_glyphs
        )
        self.team_score_glyph_apply_button.setEnabled(False)
        glyph_header.addWidget(self.team_score_glyph_apply_button)
        glyph_layout.addLayout(glyph_header)
        glyph_grid = QGridLayout()
        glyph_grid.setContentsMargins(0, 0, 0, 0)
        glyph_grid.setHorizontalSpacing(8)
        glyph_grid.setVerticalSpacing(4)
        self.team_score_glyph_combos: list[QComboBox] = []
        for glyph_index in range(4):
            slot = QHBoxLayout()
            slot.setSpacing(4)
            slot_label = QLabel(f"{glyph_index + 1}")
            slot_label.setFixedWidth(14)
            slot_label.setAlignment(Qt.AlignCenter)
            slot_label.setStyleSheet("color:#526779; border:none; background:transparent;")
            combo = QComboBox()
            combo.setMinimumContentsLength(18)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.currentIndexChanged.connect(
                self._team_score_glyph_selection_changed
            )
            slot.addWidget(slot_label)
            slot.addWidget(combo, 1)
            glyph_grid.addLayout(slot, glyph_index // 2, glyph_index % 2)
            self.team_score_glyph_combos.append(combo)
        glyph_layout.addLayout(glyph_grid)
        self.team_score_glyph_editor_hint = QLabel(
            "修改后点击“应用纹章”统一重算，避免每个下拉操作都触发长轴计算。"
        )
        self.team_score_glyph_editor_hint.setWordWrap(True)
        self.team_score_glyph_editor_hint.setStyleSheet(
            "color:#62798a; border:none; background:transparent;"
        )
        glyph_layout.addWidget(self.team_score_glyph_editor_hint)
        roster_layout.addWidget(glyph_editor)
        splitter.addWidget(roster_panel)

        details = QWidget()
        details.setMinimumWidth(720)
        details.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(2, 4, 2, 2)
        details_layout.setSpacing(6)
        top_line = QHBoxLayout()
        top_line.setSpacing(8)
        self.team_score_grade = QLabel("-")
        self.team_score_grade.setAlignment(Qt.AlignCenter)
        self.team_score_grade.setFixedSize(88, 60)
        self.team_score_grade.setStyleSheet(
            "font-size:30px; font-weight:900; color:#0f7f95; "
            "background:#e8f6f8; border:1px solid #a9dce4; border-radius:6px;"
        )
        self.team_score_title = QLabel("请选择一名队员查看评分拆解")
        self.team_score_title.setWordWrap(True)
        self.team_score_title.setStyleSheet("font-size:15px; font-weight:700; color:#24384a;")
        top_line.addWidget(self.team_score_grade)
        top_line.addWidget(self.team_score_title, 1)
        top_line.addSpacing(8)
        boss_scope_label = QLabel("评分 Boss")
        boss_scope_label.setStyleSheet("color:#526779; font-weight:600;")
        self.team_score_boss_combo = QComboBox()
        self.team_score_boss_combo.setMinimumWidth(190)
        self.team_score_boss_combo.setMaximumWidth(280)
        self.team_score_boss_combo.addItem("全部主要 Boss", "__all__")
        self.team_score_boss_combo.currentIndexChanged.connect(
            self._team_score_boss_changed
        )
        top_line.addWidget(boss_scope_label)
        top_line.addWidget(self.team_score_boss_combo)
        boss_preset_label = QLabel("评分参数")
        boss_preset_label.setStyleSheet("color:#526779; font-weight:600;")
        self.team_score_boss_preset_combo = QComboBox()
        self.team_score_boss_preset_combo.setMinimumWidth(170)
        self.team_score_boss_preset_combo.setMaximumWidth(240)
        self.team_score_boss_preset_combo.addItem(
            f"自动：{_TEAM_SCORE_DEFAULT_BOSS_PRESET}", "__auto__"
        )
        for boss_name in BOSS_PRESETS:
            self.team_score_boss_preset_combo.addItem(boss_name, boss_name)
        self.team_score_boss_preset_combo.setToolTip(
            "选择配装模拟器中的 Boss 参数；自动模式优先匹配当前评分阶段，"
            "无法匹配时使用红龙佩达。"
        )
        self.team_score_boss_preset_combo.currentIndexChanged.connect(
            self._team_score_boss_preset_changed
        )
        top_line.addWidget(boss_preset_label)
        top_line.addWidget(self.team_score_boss_preset_combo)
        details_layout.addLayout(top_line)

        self.team_score_detail_tabs = QTabWidget()
        self.team_score_detail_tabs.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Expanding
        )
        self.team_score_detail_tabs.currentChanged.connect(
            self._ensure_team_score_splitter_layout
        )

        overview_page = QWidget()
        overview_layout = QVBoxLayout(overview_page)
        overview_layout.setContentsMargins(2, 6, 2, 2)
        self.team_score_detail_table = QTableWidget(0, 3)
        self.team_score_detail_table.setHorizontalHeaderLabels(
            ["评分项目", "得分/完成率", "口径"]
        )
        self.team_score_detail_table.verticalHeader().setVisible(False)
        self.team_score_detail_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.team_score_detail_table.setSelectionMode(QTableWidget.NoSelection)
        detail_header = self.team_score_detail_table.horizontalHeader()
        detail_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        detail_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        detail_header.setSectionResizeMode(2, QHeaderView.Stretch)
        overview_layout.addWidget(self.team_score_detail_table, 1)
        self.team_score_notes = QLabel("")
        self.team_score_notes.setWordWrap(True)
        self.team_score_notes.setStyleSheet("color:#9a5b17; padding:2px 4px;")
        overview_layout.addWidget(self.team_score_notes)
        self.team_score_detail_tabs.addTab(overview_page, "评分总览")

        output_timeline_page = QWidget()
        output_timeline_layout = QVBoxLayout(output_timeline_page)
        output_timeline_layout.setContentsMargins(2, 6, 2, 2)
        output_timeline_layout.setSpacing(6)
        output_toolbar = QHBoxLayout()
        self.team_score_output_summary = QLabel("实际轴与理论轴：-")
        self.team_score_output_summary.setObjectName("liveDpsTimelineSummary")
        output_toolbar.addWidget(self.team_score_output_summary, 1)
        output_toolbar.addWidget(QLabel("技能"))
        self.team_score_output_skill_filter = MultiSelectSkillFilter()
        self.team_score_output_skill_filter.selectionChanged.connect(
            self._team_score_output_skill_filter_changed
        )
        output_toolbar.addWidget(self.team_score_output_skill_filter)
        output_toolbar.addWidget(QLabel("缩放"))
        self.team_score_output_zoom = QSlider(Qt.Horizontal)
        self.team_score_output_zoom.setRange(10, 45)
        self.team_score_output_zoom.setValue(16)
        self.team_score_output_zoom.setFixedWidth(130)
        output_toolbar.addWidget(self.team_score_output_zoom)
        output_timeline_layout.addLayout(output_toolbar)
        self.team_score_output_timeline = SkillBurstTimelineWidget(
            editable=False, pixels_per_second=16
        )
        self.team_score_output_timeline.set_track_labels("理论轴", "实战轴")
        self.team_score_output_zoom.valueChanged.connect(
            self.team_score_output_timeline.set_pixels_per_second
        )
        self.team_score_output_scroll = QScrollArea()
        self.team_score_output_scroll.setWidgetResizable(False)
        self.team_score_output_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOn
        )
        self.team_score_output_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.team_score_output_scroll.setFrameShape(QFrame.NoFrame)
        self.team_score_output_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.team_score_output_scroll.horizontalScrollBar().setMinimumHeight(16)
        self.team_score_output_scroll.setStyleSheet(
            "QScrollBar:horizontal { height: 16px; margin: 1px 0; background: #e8eef2; }"
            "QScrollBar::handle:horizontal { min-width: 42px; background: #8aa7b3; border-radius: 5px; }"
            "QScrollBar::handle:horizontal:hover { background: #5f8796; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        )
        self.team_score_output_scroll.setMinimumHeight(360)
        self.team_score_output_scroll.setWidget(self.team_score_output_timeline)
        self.team_score_output_timeline.set_sticky_comparison_header(True)
        output_timeline_layout.addWidget(self.team_score_output_scroll, 1)
        self.team_score_detail_tabs.addTab(output_timeline_page, "输出时间轴")

        output_page = QWidget()
        output_layout = QVBoxLayout(output_page)
        output_layout.setContentsMargins(2, 6, 2, 2)
        output_layout.setSpacing(6)
        output_tables = QSplitter(Qt.Vertical)
        output_tables.setChildrenCollapsible(False)
        self.team_score_skill_table = QTableWidget(0, 9)
        self.team_score_skill_table.setHorizontalHeaderLabels(
            [
                "技能",
                "高价值",
                "实战次数",
                "理论次数",
                "实战标准伤害",
                "理论标准伤害",
                "持续利用",
                "完成率",
                "差距",
            ]
        )
        self.team_score_skill_table.verticalHeader().setVisible(False)
        self.team_score_skill_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.team_score_skill_table.setSelectionBehavior(QTableWidget.SelectRows)
        skill_header = self.team_score_skill_table.horizontalHeader()
        skill_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        skill_header.setSectionResizeMode(0, QHeaderView.Stretch)
        output_tables.addWidget(self.team_score_skill_table)

        self.team_score_window_table = QTableWidget(0, 6)
        self.team_score_window_table.setHorizontalHeaderLabels(
            ["爆发/Buff窗口", "时间", "实战输出", "理论输出", "利用率", "差距"]
        )
        self.team_score_window_table.verticalHeader().setVisible(False)
        self.team_score_window_table.setEditTriggers(QTableWidget.NoEditTriggers)
        window_header = self.team_score_window_table.horizontalHeader()
        window_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        window_header.setSectionResizeMode(0, QHeaderView.Stretch)
        output_tables.addWidget(self.team_score_window_table)
        output_tables.setSizes([230, 150])
        output_layout.addWidget(output_tables, 1)
        self.team_score_detail_tabs.addTab(output_page, "输出明细")

        support_page = QWidget()
        support_layout = QVBoxLayout(support_page)
        support_layout.setContentsMargins(2, 6, 2, 2)
        support_layout.setSpacing(6)
        support_top = QHBoxLayout()
        self.team_score_support_summary = QLabel(
            "选择下方效果，查看实际覆盖轴与理论覆盖轴。"
        )
        self.team_score_support_summary.setObjectName("liveDpsTimelineSummary")
        support_top.addWidget(self.team_score_support_summary, 1)
        support_top.addWidget(QLabel("缩放"))
        self.team_score_support_zoom = QSlider(Qt.Horizontal)
        self.team_score_support_zoom.setRange(10, 45)
        self.team_score_support_zoom.setValue(16)
        self.team_score_support_zoom.setFixedWidth(130)
        support_top.addWidget(self.team_score_support_zoom)
        support_layout.addLayout(support_top)
        self.team_score_support_timeline = SkillBurstTimelineWidget(
            editable=False, pixels_per_second=16
        )
        self.team_score_support_timeline.set_track_labels("理论轴", "实战轴")
        self.team_score_support_zoom.valueChanged.connect(
            self.team_score_support_timeline.set_pixels_per_second
        )
        self.team_score_support_scroll = QScrollArea()
        self.team_score_support_scroll.setWidgetResizable(False)
        self.team_score_support_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOn
        )
        self.team_score_support_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.team_score_support_scroll.setMinimumHeight(238)
        self.team_score_support_scroll.setWidget(self.team_score_support_timeline)
        self.team_score_support_timeline.set_sticky_comparison_header(True)
        support_layout.addWidget(self.team_score_support_scroll)
        self.team_score_support_table = QTableWidget(0, 11)
        self.team_score_support_table.setHorizontalHeaderLabels(
            [
                "来源技能",
                "类型",
                "效果",
                "数值",
                "实战覆盖率（减抗按命中）",
                "理论轴覆盖率",
                "有效伤害覆盖率（实际/理论）",
                "辅助收益完成率",
                "收益权重",
                "反事实归因实际收益%",
                "反事实归因理论收益%",
            ]
        )
        self.team_score_support_table.verticalHeader().setVisible(False)
        self.team_score_support_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.team_score_support_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.team_score_support_table.setSelectionMode(QTableWidget.SingleSelection)
        self.team_score_support_table.itemSelectionChanged.connect(
            self._team_score_support_selection_changed
        )
        support_header = self.team_score_support_table.horizontalHeader()
        support_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        support_header.setSectionResizeMode(0, QHeaderView.Stretch)
        support_layout.addWidget(self.team_score_support_table, 1)
        self.team_score_detail_tabs.addTab(support_page, "辅助明细")

        specialty_page = QWidget()
        specialty_layout = QVBoxLayout(specialty_page)
        specialty_layout.setContentsMargins(8, 10, 8, 8)
        specialty_layout.setSpacing(8)
        self.team_score_specialty_summary = QLabel("")
        self.team_score_specialty_summary.setWordWrap(True)
        self.team_score_specialty_summary.setObjectName("liveDpsTimelineSummary")
        specialty_layout.addWidget(self.team_score_specialty_summary)
        self.team_score_specialty_table = QTableWidget(0, 3)
        self.team_score_specialty_table.setHorizontalHeaderLabels(
            ["职业特色项目", "完成结果", "说明"]
        )
        self.team_score_specialty_table.verticalHeader().setVisible(False)
        self.team_score_specialty_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.team_score_specialty_table.setSelectionMode(QTableWidget.NoSelection)
        specialty_header = self.team_score_specialty_table.horizontalHeader()
        specialty_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        specialty_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        specialty_header.setSectionResizeMode(2, QHeaderView.Stretch)
        specialty_layout.addWidget(self.team_score_specialty_table, 1)
        self.team_score_specialty_tab_index = self.team_score_detail_tabs.addTab(
            specialty_page, "职业特色"
        )
        self.team_score_detail_tabs.setTabVisible(
            self.team_score_specialty_tab_index, False
        )

        improvement_page = QWidget()
        improvement_layout = QVBoxLayout(improvement_page)
        improvement_layout.setContentsMargins(8, 10, 8, 8)
        self.team_score_improvement_summary = QLabel("")
        self.team_score_improvement_summary.setWordWrap(True)
        self.team_score_improvement_summary.setObjectName("liveDpsTimelineSummary")
        improvement_layout.addWidget(self.team_score_improvement_summary)
        self.team_score_improvement_table = QTableWidget(0, 6)
        self.team_score_improvement_table.setHorizontalHeaderLabels(
            ["方向", "优先级", "改进项目", "当前表现", "具体建议", "预计改善"]
        )
        self.team_score_improvement_table.verticalHeader().setVisible(False)
        self.team_score_improvement_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.team_score_improvement_table.setSelectionMode(QTableWidget.NoSelection)
        improvement_header = self.team_score_improvement_table.horizontalHeader()
        improvement_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        improvement_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        improvement_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        improvement_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        improvement_header.setSectionResizeMode(4, QHeaderView.Stretch)
        improvement_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.team_score_improvement_table.setWordWrap(True)
        improvement_layout.addWidget(self.team_score_improvement_table, 1)
        self.team_score_detail_tabs.addTab(improvement_page, "提升分析")

        details_layout.addWidget(self.team_score_detail_tabs, 1)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([780, 1020])
        layout.addWidget(splitter, 1)
        QTimer.singleShot(0, self._ensure_team_score_splitter_layout)
        return page


    def _ensure_team_score_splitter_layout(self, _index: int = -1) -> None:
        """Keep the horizontal roster/detail split readable after tab changes."""
        splitter = getattr(self, "team_score_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        if total <= 0:
            return
        minimum_detail = min(720, max(420, total // 2))
        minimum_roster = min(680, max(420, total - minimum_detail))
        if sizes[0] >= minimum_roster:
            return
        target_roster = min(780, max(minimum_roster, total - minimum_detail))
        if target_roster > 0:
            splitter.setSizes([target_roster, max(1, total - target_roster)])


    def _build_boss_statistics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 6, 4, 4)
        layout.setSpacing(6)
        layout.addWidget(self._create_view_progress("boss"))

        self.boss_stats_overview_splitter = QSplitter(Qt.Horizontal)
        self.boss_stats_overview_splitter.setChildrenCollapsible(False)

        boss_list_panel = QWidget()
        boss_list_layout = QVBoxLayout(boss_list_panel)
        boss_list_layout.setContentsMargins(0, 0, 0, 0)
        boss_list_layout.setSpacing(4)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("选择 Boss"))
        self.boss_stats_select_all_button = QPushButton("全选")
        self.boss_stats_select_all_button.clicked.connect(
            lambda: self._set_all_boss_statistics_selected(True)
        )
        selector_row.addWidget(self.boss_stats_select_all_button)
        self.boss_stats_clear_button = QPushButton("清空")
        self.boss_stats_clear_button.clicked.connect(
            lambda: self._set_all_boss_statistics_selected(False)
        )
        selector_row.addWidget(self.boss_stats_clear_button)
        self.boss_stats_selection_label = QLabel("尚未识别 Boss")
        self.boss_stats_selection_label.setObjectName("liveDpsTimelineSummary")
        selector_row.addStretch(1)
        selector_row.addWidget(self.boss_stats_selection_label)
        boss_list_layout.addLayout(selector_row)

        selector_scroll = QScrollArea()
        selector_scroll.setWidgetResizable(True)
        selector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        selector_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        selector_scroll.setFixedHeight(52)
        self.boss_stats_selector_host = QWidget()
        self.boss_stats_selector_layout = QHBoxLayout(
            self.boss_stats_selector_host
        )
        self.boss_stats_selector_layout.setContentsMargins(6, 2, 6, 2)
        self.boss_stats_selector_layout.setSpacing(14)
        self.boss_stats_selector_layout.addStretch(1)
        selector_scroll.setWidget(self.boss_stats_selector_host)
        boss_list_layout.addWidget(selector_scroll)

        self.boss_stats_summary_table = QTableWidget(0, 7)
        self.boss_stats_summary_table.setHorizontalHeaderLabels(
            [
                "Boss",
                "输出总量",
                "DPS",
                "命中",
                "暴击率",
                "有效输出时间",
                "占全场",
            ]
        )
        self.boss_stats_summary_table.verticalHeader().setVisible(False)
        self.boss_stats_summary_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.boss_stats_summary_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.boss_stats_summary_table.setMaximumHeight(168)
        summary_header = self.boss_stats_summary_table.horizontalHeader()
        summary_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        summary_header.setSectionResizeMode(0, QHeaderView.Stretch)
        boss_list_layout.addWidget(self.boss_stats_summary_table, 1)
        self.boss_stats_overview_splitter.addWidget(boss_list_panel)

        realtime_panel = QWidget()
        realtime_layout = QVBoxLayout(realtime_panel)
        realtime_layout.setContentsMargins(0, 0, 0, 0)
        realtime_layout.setSpacing(4)
        realtime_header = QHBoxLayout()
        realtime_title = QLabel("所选时刻实时 Boss 状态")
        realtime_title.setStyleSheet("font-weight:600; color:#284b63;")
        realtime_header.addWidget(realtime_title)
        self.boss_stats_realtime_time_label = QLabel("时点：最新")
        self.boss_stats_realtime_time_label.setObjectName("liveDpsTimelineSummary")
        realtime_header.addWidget(self.boss_stats_realtime_time_label)
        realtime_header.addStretch(1)
        self.boss_stats_realtime_latest_button = QPushButton("返回最新")
        self.boss_stats_realtime_latest_button.setToolTip(
            "恢复跟随当前所选 Boss 范围的最后时刻"
        )
        self.boss_stats_realtime_latest_button.clicked.connect(
            self._boss_stats_realtime_follow_latest_clicked
        )
        realtime_header.addWidget(self.boss_stats_realtime_latest_button)
        realtime_layout.addLayout(realtime_header)
        self.boss_stats_realtime_table = QTableWidget(0, 8)
        self.boss_stats_realtime_table.setHorizontalHeaderLabels(
            [
                "Boss",
                "剩余血量",
                "增伤（通/物/魔）",
                "当前抗性（火/水/光/暗）",
                "当前防御（物/魔）",
                "当前致抗",
                "叠冰",
                "减攻击",
            ]
        )
        self.boss_stats_realtime_table.verticalHeader().setVisible(False)
        self.boss_stats_realtime_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.boss_stats_realtime_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )
        self.boss_stats_realtime_table.setWordWrap(False)
        realtime_table_header = self.boss_stats_realtime_table.horizontalHeader()
        realtime_table_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        realtime_table_header.setSectionResizeMode(0, QHeaderView.Stretch)
        realtime_table_header.setSectionResizeMode(3, QHeaderView.Stretch)
        realtime_layout.addWidget(self.boss_stats_realtime_table, 1)
        self.boss_stats_realtime_note = QLabel(
            "单击下方伤害曲线或 Boss 状态曲线选择时点；未匹配专属参数时按红龙佩达基准估算当前抗性、防御和致命抵抗。"
        )
        self.boss_stats_realtime_note.setWordWrap(True)
        self.boss_stats_realtime_note.setObjectName("liveDpsTimelineSummary")
        realtime_layout.addWidget(self.boss_stats_realtime_note)
        self.boss_stats_overview_splitter.addWidget(realtime_panel)
        self.boss_stats_overview_splitter.setStretchFactor(0, 5)
        self.boss_stats_overview_splitter.setStretchFactor(1, 7)
        self.boss_stats_overview_splitter.setSizes([720, 980])
        self.boss_stats_overview_splitter.setMinimumHeight(210)
        self.boss_stats_overview_splitter.setMaximumHeight(270)
        layout.addWidget(self.boss_stats_overview_splitter)

        self.boss_stats_tabs = QTabWidget()
        self.boss_stats_tabs.currentChanged.connect(
            self._boss_statistics_tab_changed
        )

        boss_skills_page = QWidget()
        boss_skills_layout = QVBoxLayout(boss_skills_page)
        boss_skills_layout.setContentsMargins(0, 0, 0, 0)
        boss_skills_controls = QHBoxLayout()
        boss_skills_controls.addWidget(QLabel("角色"))
        self.boss_stats_skills_player_combo = QComboBox()
        self.boss_stats_skills_player_combo.setMinimumWidth(220)
        self.boss_stats_skills_player_combo.addItem("全部角色（按角色统计）", "")
        self.boss_stats_skills_player_combo.currentIndexChanged.connect(
            self._boss_stats_content_filter_changed
        )
        boss_skills_controls.addWidget(self.boss_stats_skills_player_combo)
        boss_skills_controls.addStretch(1)
        boss_skills_layout.addLayout(boss_skills_controls)
        self.boss_stats_skills_table = QTableWidget(0, 11)
        self.boss_stats_skills_table.setHorizontalHeaderLabels(
            [
                "Boss",
                "角色",
                "职业",
                "技能",
                "伤害",
                "占该Boss",
                "释放次数",
                "命中",
                "平均Hit",
                "暴击",
                "暴击率",
            ]
        )
        self.boss_stats_skills_table.verticalHeader().setVisible(False)
        self.boss_stats_skills_table.setEditTriggers(QTableWidget.NoEditTriggers)
        boss_skills_header = self.boss_stats_skills_table.horizontalHeader()
        boss_skills_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        boss_skills_header.setSectionResizeMode(3, QHeaderView.Stretch)
        boss_skills_layout.addWidget(self.boss_stats_skills_table, 1)
        self.boss_stats_tabs.addTab(boss_skills_page, "技能构成")

        damage_curve_page = QWidget()
        damage_curve_layout = QVBoxLayout(damage_curve_page)
        damage_curve_layout.setContentsMargins(0, 0, 0, 0)
        self.boss_stats_damage_curve = BossDamageCurve()
        self.boss_stats_damage_curve.viewChanged.connect(
            self._boss_stats_damage_curve_view_changed
        )
        self.boss_stats_damage_curve.timeSelected.connect(
            self._boss_stats_time_selected
        )
        self.boss_stats_damage_curve.timeHovered.connect(
            self._boss_stats_time_hovered
        )
        damage_curve_layout.addWidget(self.boss_stats_damage_curve, 1)
        boss_damage_zoom = QHBoxLayout()
        self.boss_stats_damage_zoom = QSlider(Qt.Horizontal)
        self.boss_stats_damage_zoom.setRange(2, 45)
        self.boss_stats_damage_zoom.setValue(30)
        self.boss_stats_damage_zoom.setFixedWidth(180)
        self.boss_stats_damage_zoom_value = QLabel("30 px/s")
        self.boss_stats_damage_zoom.valueChanged.connect(
            lambda value: self._curve_pixels_changed(
                self.boss_stats_damage_curve,
                self.boss_stats_damage_zoom_value,
                value,
            )
        )
        boss_damage_zoom.addWidget(QLabel("时间轴缩放"))
        boss_damage_zoom.addWidget(self.boss_stats_damage_zoom)
        boss_damage_zoom.addWidget(self.boss_stats_damage_zoom_value)
        boss_damage_zoom.addSpacing(14)
        boss_damage_zoom.addWidget(QLabel("平滑"))
        self.boss_stats_damage_smoothing = QComboBox()
        for label, seconds in (
            ("原始（1秒）", 1),
            ("3秒", 3),
            ("5秒", 5),
            ("10秒", 10),
        ):
            self.boss_stats_damage_smoothing.addItem(label, seconds)
        self.boss_stats_damage_smoothing.setCurrentIndex(2)
        self.boss_stats_damage_smoothing.currentIndexChanged.connect(
            lambda _index: self.boss_stats_damage_curve.set_smoothing_seconds(
                int(self.boss_stats_damage_smoothing.currentData() or 5)
            )
        )
        boss_damage_zoom.addWidget(self.boss_stats_damage_smoothing)
        self.boss_stats_damage_select_range = QCheckBox("区间选择")
        self.boss_stats_damage_select_range.setToolTip(
            "启用后在曲线上拖动选择区间；关闭时拖动仍用于左右移动。"
        )
        self.boss_stats_damage_select_range.toggled.connect(
            self.boss_stats_damage_curve.set_range_selection_enabled
        )
        boss_damage_zoom.addWidget(self.boss_stats_damage_select_range)
        self.boss_stats_damage_clear_range = QPushButton("清除区间")
        self.boss_stats_damage_clear_range.clicked.connect(
            self.boss_stats_damage_curve.clear_interval_selection
        )
        boss_damage_zoom.addWidget(self.boss_stats_damage_clear_range)
        boss_damage_zoom.addSpacing(14)
        boss_damage_zoom.addWidget(QLabel("角色"))
        self.boss_stats_curve_player_combo = QComboBox()
        self.boss_stats_curve_player_combo.setMinimumWidth(220)
        self.boss_stats_curve_player_combo.addItem("全部角色", "")
        self.boss_stats_curve_player_combo.currentIndexChanged.connect(
            self._boss_stats_content_filter_changed
        )
        boss_damage_zoom.addWidget(self.boss_stats_curve_player_combo)
        boss_damage_zoom.addSpacing(14)
        self.boss_stats_hp_check = QCheckBox("Boss剩余血量")
        self.boss_stats_hp_check.setChecked(True)
        self.boss_stats_hp_check.setToolTip(
            "优先使用服务器血量；已击杀记录按完整承伤归一化，未击杀记录按内置副本/难度固定血量计算。"
            "无法可靠匹配固定血量时不显示伪精确曲线。"
        )
        self.boss_stats_hp_check.toggled.connect(
            self.boss_stats_damage_curve.set_boss_hp_visible
        )
        boss_damage_zoom.addWidget(self.boss_stats_hp_check)
        boss_damage_zoom.addStretch(1)
        damage_curve_layout.addLayout(boss_damage_zoom)
        self.boss_stats_damage_interval_summary = QLabel("区间：未选择")
        self.boss_stats_damage_interval_summary.setStyleSheet(
            "color: #355866; padding: 2px 6px; background: #eef6f8;"
        )
        self.boss_stats_damage_curve.intervalSelectionChanged.connect(
            lambda _start, _end, summary: self.boss_stats_damage_interval_summary.setText(
                summary
            )
        )
        damage_curve_layout.addWidget(self.boss_stats_damage_interval_summary)
        curve_range = QHBoxLayout()
        self.boss_stats_curve_start = QLabel("0.0s")
        self.boss_stats_curve_scrollbar = QScrollBar(Qt.Horizontal)
        self.boss_stats_curve_scrollbar.setMinimumHeight(20)
        self.boss_stats_curve_scrollbar.setTracking(True)
        self.boss_stats_curve_scrollbar.valueChanged.connect(
            lambda value: self.boss_stats_damage_curve.set_view_start(value / 10.0)
        )
        self.boss_stats_curve_end = QLabel("总计 0.0s")
        curve_range.addWidget(self.boss_stats_curve_start)
        curve_range.addWidget(self.boss_stats_curve_scrollbar, 1)
        curve_range.addWidget(self.boss_stats_curve_end)
        damage_curve_layout.addLayout(curve_range)
        self.boss_stats_tabs.addTab(damage_curve_page, "伤害曲线")

        status_page = QWidget()
        status_layout = QVBoxLayout(status_page)
        status_layout.setContentsMargins(0, 0, 0, 0)
        self.boss_stats_effect_summary = QLabel("暂无所选 Boss 状态")
        self.boss_stats_effect_summary.setWordWrap(True)
        self.boss_stats_effect_summary.setObjectName("liveDpsBossSummary")
        status_layout.addWidget(self.boss_stats_effect_summary)
        self.boss_stats_effect_curve = BossEffectCurve()
        self.boss_stats_effect_curve.set_pixels_per_second(30)
        self.boss_stats_effect_curve.viewChanged.connect(
            self._boss_stats_effect_curve_view_changed
        )
        self.boss_stats_effect_curve.timeSelected.connect(
            self._boss_stats_time_selected
        )
        self.boss_stats_effect_curve.set_visible_keys(set())
        status_curve_row = QHBoxLayout()
        status_curve_row.setContentsMargins(0, 0, 0, 0)
        status_curve_row.setSpacing(4)
        status_curve_row.addWidget(self.boss_stats_effect_curve, 1)
        self.boss_stats_effect_filter_toggle = QToolButton()
        self.boss_stats_effect_filter_toggle.setCheckable(True)
        self.boss_stats_effect_filter_toggle.setChecked(True)
        self.boss_stats_effect_filter_toggle.setArrowType(Qt.RightArrow)
        self.boss_stats_effect_filter_toggle.setToolTip("收起状态筛选")
        self.boss_stats_effect_filter_panel = QWidget()
        self.boss_stats_effect_filter_panel.setFixedWidth(210)
        self.boss_stats_effect_filter_panel.setStyleSheet(
            "QWidget { background: #f3f7f9; border: 1px solid #d6e2e7; }"
            "QCheckBox { border: none; background: transparent; }"
        )
        boss_stats_effect_filter_layout = QGridLayout(
            self.boss_stats_effect_filter_panel
        )
        boss_stats_effect_filter_layout.setContentsMargins(8, 8, 8, 8)
        boss_stats_effect_filter_layout.setHorizontalSpacing(6)
        boss_stats_effect_filter_layout.setVerticalSpacing(4)
        self._boss_stats_curve_checks: dict[str, QCheckBox] = {}
        for index, (key, label, color, _freeze) in enumerate(
            BossEffectCurve.SERIES_META
        ):
            check = QCheckBox(label)
            check.setChecked(False)
            check.setStyleSheet(
                f"QCheckBox {{ color: {color}; border: none; background: transparent; }}"
            )
            check.stateChanged.connect(
                self._boss_stats_effect_curve_selection_changed
            )
            boss_stats_effect_filter_layout.addWidget(
                check, index // 2, index % 2
            )
            self._boss_stats_curve_checks[key] = check
        boss_stats_effect_filter_layout.setRowStretch(
            (len(BossEffectCurve.SERIES_META) + 1) // 2, 1
        )
        self.boss_stats_effect_filter_toggle.toggled.connect(
            lambda visible: self._set_curve_filter_panel_visible(
                self.boss_stats_effect_filter_panel,
                self.boss_stats_effect_filter_toggle,
                visible,
            )
        )
        status_curve_row.addWidget(
            self.boss_stats_effect_filter_toggle, 0, Qt.AlignVCenter
        )
        status_curve_row.addWidget(self.boss_stats_effect_filter_panel)
        status_layout.addLayout(status_curve_row, 1)
        status_range = QHBoxLayout()
        self.boss_stats_effect_zoom = QSlider(Qt.Horizontal)
        self.boss_stats_effect_zoom.setRange(2, 45)
        self.boss_stats_effect_zoom.setValue(30)
        self.boss_stats_effect_zoom.setFixedWidth(160)
        self.boss_stats_effect_zoom_value = QLabel("30 px/s")
        self.boss_stats_effect_zoom.valueChanged.connect(
            lambda value: self._curve_pixels_changed(
                self.boss_stats_effect_curve,
                self.boss_stats_effect_zoom_value,
                value,
            )
        )
        self.boss_stats_effect_scrollbar = QScrollBar(Qt.Horizontal)
        self.boss_stats_effect_scrollbar.setTracking(True)
        self.boss_stats_effect_scrollbar.valueChanged.connect(
            lambda value: self.boss_stats_effect_curve.set_view_start(value / 10.0)
        )
        self.boss_stats_effect_range_start = QLabel("0.0s")
        self.boss_stats_effect_range_end = QLabel("总计 0.0s")
        status_range.addWidget(QLabel("缩放"))
        status_range.addWidget(self.boss_stats_effect_zoom)
        status_range.addWidget(self.boss_stats_effect_zoom_value)
        status_range.addWidget(self.boss_stats_effect_range_start)
        status_range.addWidget(self.boss_stats_effect_scrollbar, 1)
        status_range.addWidget(self.boss_stats_effect_range_end)
        status_layout.addLayout(status_range)
        self.boss_stats_effects_table = QTableWidget(0, 8)
        self.boss_stats_effects_table.setHorizontalHeaderLabels(
            [
                "目标Boss",
                "归类",
                "效果",
                "来源技能",
                "来源职业",
                "数值/参数",
                "刷新次数",
                "覆盖率",
            ]
        )
        self.boss_stats_effects_table.verticalHeader().setVisible(False)
        self.boss_stats_effects_table.setEditTriggers(QTableWidget.NoEditTriggers)
        effect_header = self.boss_stats_effects_table.horizontalHeader()
        effect_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        effect_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.boss_stats_effect_details_table = QTableWidget(0, 9)
        self.boss_stats_effect_details_table.setHorizontalHeaderLabels(
            [
                "时间",
                "目标Boss",
                "归类",
                "效果",
                "来源技能",
                "来源角色",
                "数值/参数",
                "持续",
                "预计结束",
            ]
        )
        self.boss_stats_effect_details_table.verticalHeader().setVisible(False)
        self.boss_stats_effect_details_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        detail_header = self.boss_stats_effect_details_table.horizontalHeader()
        detail_header.setSectionResizeMode(QHeaderView.Interactive)
        detail_header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.boss_stats_effect_tabs = QTabWidget()
        self.boss_stats_effect_tabs.addTab(self.boss_stats_effects_table, "状态总览")
        self.boss_stats_effect_tabs.addTab(self.boss_stats_effect_details_table, "状态明细")
        self.boss_stats_effect_tabs.currentChanged.connect(
            self._boss_stats_effect_detail_tab_changed
        )
        status_layout.addWidget(self.boss_stats_effect_tabs, 1)
        self.boss_stats_tabs.addTab(status_page, "Boss实时状态")

        buffs_page = QWidget()
        buffs_layout = QVBoxLayout(buffs_page)
        buffs_layout.setContentsMargins(0, 0, 0, 0)
        self.boss_stats_buff_summary = QLabel("暂无所选 Boss 阶段增益")
        self.boss_stats_buff_summary.setWordWrap(True)
        self.boss_stats_buff_summary.setObjectName("liveDpsBossSummary")
        buffs_layout.addWidget(self.boss_stats_buff_summary)
        self.boss_stats_buff_curve = PlayerBuffCurve()
        self.boss_stats_buff_curve.set_pixels_per_second(30)
        self.boss_stats_buff_curve.viewChanged.connect(
            self._boss_stats_buff_curve_view_changed
        )
        buff_curve_row = QHBoxLayout()
        buff_curve_row.setContentsMargins(0, 0, 0, 0)
        buff_curve_row.setSpacing(4)
        buff_curve_row.addWidget(self.boss_stats_buff_curve, 1)
        self.boss_stats_buff_filter_toggle = QToolButton()
        self.boss_stats_buff_filter_toggle.setCheckable(True)
        self.boss_stats_buff_filter_toggle.setChecked(True)
        self.boss_stats_buff_filter_toggle.setArrowType(Qt.RightArrow)
        self.boss_stats_buff_filter_toggle.setToolTip("收起增益筛选")
        self.boss_stats_buff_filter_panel = QWidget()
        self.boss_stats_buff_filter_panel.setFixedWidth(230)
        self.boss_stats_buff_filter_panel.setStyleSheet(
            "QWidget { background: #f3f7f9; border: 1px solid #d6e2e7; }"
            "QLabel, QComboBox { border: none; background: transparent; }"
            "QComboBox { background: white; border: 1px solid #cbd9df; padding: 2px; }"
        )
        boss_stats_buff_filter_layout = QGridLayout(
            self.boss_stats_buff_filter_panel
        )
        boss_stats_buff_filter_layout.setContentsMargins(8, 8, 8, 8)
        boss_stats_buff_filter_layout.setHorizontalSpacing(6)
        boss_stats_buff_filter_layout.setVerticalSpacing(6)
        self.boss_stats_buff_target_combo = QComboBox()
        self.boss_stats_buff_target_job_combo = QComboBox()
        self.boss_stats_buff_source_job_combo = QComboBox()
        self.boss_stats_buff_type_combo = QComboBox()
        for row, (label, combo) in enumerate(
            (
                ("队员", self.boss_stats_buff_target_combo),
                ("目标职业", self.boss_stats_buff_target_job_combo),
                ("来源职业", self.boss_stats_buff_source_job_combo),
                ("增益类型", self.boss_stats_buff_type_combo),
            )
        ):
            boss_stats_buff_filter_layout.addWidget(QLabel(label), row, 0)
            combo.setMinimumWidth(142)
            combo.currentIndexChanged.connect(
                self._boss_stats_buff_filter_changed
            )
            boss_stats_buff_filter_layout.addWidget(combo, row, 1)
        boss_stats_buff_filter_layout.setRowStretch(4, 1)
        self.boss_stats_buff_filter_toggle.toggled.connect(
            lambda visible: self._set_curve_filter_panel_visible(
                self.boss_stats_buff_filter_panel,
                self.boss_stats_buff_filter_toggle,
                visible,
            )
        )
        buff_curve_row.addWidget(
            self.boss_stats_buff_filter_toggle, 0, Qt.AlignVCenter
        )
        buff_curve_row.addWidget(self.boss_stats_buff_filter_panel)
        buffs_layout.addLayout(buff_curve_row, 1)
        buff_range = QHBoxLayout()
        self.boss_stats_buff_zoom = QSlider(Qt.Horizontal)
        self.boss_stats_buff_zoom.setRange(2, 45)
        self.boss_stats_buff_zoom.setValue(30)
        self.boss_stats_buff_zoom.setFixedWidth(160)
        self.boss_stats_buff_zoom_value = QLabel("30 px/s")
        self.boss_stats_buff_zoom.valueChanged.connect(
            lambda value: self._curve_pixels_changed(
                self.boss_stats_buff_curve,
                self.boss_stats_buff_zoom_value,
                value,
            )
        )
        self.boss_stats_buff_scrollbar = QScrollBar(Qt.Horizontal)
        self.boss_stats_buff_scrollbar.setTracking(True)
        self.boss_stats_buff_scrollbar.valueChanged.connect(
            lambda value: self.boss_stats_buff_curve.set_view_start(value / 10.0)
        )
        self.boss_stats_buff_range_start = QLabel("0.0s")
        self.boss_stats_buff_range_end = QLabel("总计 0.0s")
        buff_range.addWidget(QLabel("缩放"))
        buff_range.addWidget(self.boss_stats_buff_zoom)
        buff_range.addWidget(self.boss_stats_buff_zoom_value)
        buff_range.addWidget(self.boss_stats_buff_range_start)
        buff_range.addWidget(self.boss_stats_buff_scrollbar, 1)
        buff_range.addWidget(self.boss_stats_buff_range_end)
        buffs_layout.addLayout(buff_range)
        self.boss_stats_buffs_table = QTableWidget(0, 7)
        self.boss_stats_buffs_table.setHorizontalHeaderLabels(
            [
                "目标",
                "目标职业",
                "归类",
                "增益",
                "技能",
                "来源职业",
                "阶段覆盖率",
            ]
        )
        self.boss_stats_buffs_table.verticalHeader().setVisible(False)
        self.boss_stats_buffs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        buff_header = self.boss_stats_buffs_table.horizontalHeader()
        buff_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        buff_header.setSectionResizeMode(3, QHeaderView.Stretch)
        buffs_layout.addWidget(self.boss_stats_buffs_table, 1)
        self.boss_stats_tabs.addTab(buffs_page, "实时增益")

        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_controls = QHBoxLayout()
        timeline_controls.addWidget(QLabel("玩家"))
        self.boss_stats_timeline_player_combo = QComboBox()
        self.boss_stats_timeline_player_combo.addItem("全部玩家", "")
        self.boss_stats_timeline_player_combo.currentIndexChanged.connect(
            self._boss_stats_timeline_filter_changed
        )
        timeline_controls.addWidget(self.boss_stats_timeline_player_combo)
        timeline_controls.addWidget(QLabel("技能"))
        self.boss_stats_timeline_skill_combo = MultiSelectSkillFilter()
        self.boss_stats_timeline_skill_combo.selectionChanged.connect(
            self._boss_stats_timeline_filter_changed
        )
        timeline_controls.addWidget(self.boss_stats_timeline_skill_combo)
        timeline_controls.addWidget(QLabel("缩放"))
        self.boss_stats_timeline_zoom = QSlider(Qt.Horizontal)
        self.boss_stats_timeline_zoom.setRange(2, 45)
        self.boss_stats_timeline_zoom.setValue(30)
        self.boss_stats_timeline_zoom.setFixedWidth(180)
        self.boss_stats_timeline_zoom.valueChanged.connect(
            self._boss_stats_timeline_zoom_changed
        )
        timeline_controls.addWidget(self.boss_stats_timeline_zoom)
        self.boss_stats_timeline_zoom_label = QLabel("30 px/s")
        timeline_controls.addWidget(self.boss_stats_timeline_zoom_label)
        timeline_controls.addStretch(1)
        self.boss_stats_timeline_summary = QLabel("暂无技能记录")
        self.boss_stats_timeline_summary.setObjectName("liveDpsTimelineSummary")
        timeline_controls.addWidget(self.boss_stats_timeline_summary)
        timeline_layout.addLayout(timeline_controls)
        timeline_canvas = QWidget()
        timeline_canvas_layout = QVBoxLayout(timeline_canvas)
        timeline_canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.boss_stats_team_timeline = TeamSkillTimeline(self.store)
        self.boss_stats_team_timeline.viewChanged.connect(
            self._boss_stats_timeline_view_changed
        )
        timeline_canvas_layout.addWidget(self.boss_stats_team_timeline, 1)
        timeline_range = QHBoxLayout()
        self.boss_stats_timeline_start = QLabel("0.0s")
        self.boss_stats_timeline_scrollbar = QScrollBar(Qt.Horizontal)
        self.boss_stats_timeline_scrollbar.setMinimumHeight(20)
        self.boss_stats_timeline_scrollbar.valueChanged.connect(
            lambda value: self.boss_stats_team_timeline.set_view_start(value / 10.0)
        )
        self.boss_stats_timeline_end = QLabel("总计 0.0s")
        timeline_range.addWidget(self.boss_stats_timeline_start)
        timeline_range.addWidget(self.boss_stats_timeline_scrollbar, 1)
        timeline_range.addWidget(self.boss_stats_timeline_end)
        timeline_canvas_layout.addLayout(timeline_range)
        self.boss_stats_timeline_table = QTableWidget(0, 8)
        self.boss_stats_timeline_table.setHorizontalHeaderLabels(
            ["时间", "玩家", "职业", "技能", "命中目标", "事件/伤害", "暴击", "来源"]
        )
        self.boss_stats_timeline_table.verticalHeader().setVisible(False)
        self.boss_stats_timeline_table.setEditTriggers(QTableWidget.NoEditTriggers)
        timeline_table_header = self.boss_stats_timeline_table.horizontalHeader()
        timeline_table_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        timeline_table_header.setSectionResizeMode(3, QHeaderView.Stretch)
        timeline_splitter = QSplitter(Qt.Vertical)
        timeline_splitter.addWidget(timeline_canvas)
        timeline_splitter.addWidget(self.boss_stats_timeline_table)
        timeline_splitter.setStretchFactor(0, 3)
        timeline_splitter.setStretchFactor(1, 2)
        timeline_layout.addWidget(timeline_splitter, 1)
        self.boss_stats_tabs.addTab(timeline_page, "全队技能时间轴")

        boss_action_page = QWidget()
        boss_action_layout = QVBoxLayout(boss_action_page)
        boss_action_layout.setContentsMargins(0, 0, 0, 0)
        boss_action_controls = QHBoxLayout()
        boss_action_controls.addWidget(QLabel("动作"))
        self.boss_stats_action_combo = QComboBox()
        self.boss_stats_action_combo.setMinimumWidth(220)
        self.boss_stats_action_combo.addItem("全部动作", "")
        self.boss_stats_action_combo.currentIndexChanged.connect(
            self._boss_stats_action_filter_changed
        )
        boss_action_controls.addWidget(self.boss_stats_action_combo)
        boss_action_controls.addWidget(QLabel("缩放"))
        self.boss_stats_action_zoom = QSlider(Qt.Horizontal)
        self.boss_stats_action_zoom.setRange(2, 45)
        self.boss_stats_action_zoom.setValue(30)
        self.boss_stats_action_zoom.setFixedWidth(180)
        self.boss_stats_action_zoom.valueChanged.connect(
            self._boss_stats_action_zoom_changed
        )
        boss_action_controls.addWidget(self.boss_stats_action_zoom)
        self.boss_stats_action_zoom_label = QLabel("30 px/s")
        boss_action_controls.addWidget(self.boss_stats_action_zoom_label)
        boss_action_controls.addStretch(1)
        self.boss_stats_action_summary = QLabel("暂无 Boss 动作记录")
        self.boss_stats_action_summary.setObjectName("liveDpsTimelineSummary")
        boss_action_controls.addWidget(self.boss_stats_action_summary)
        boss_action_layout.addLayout(boss_action_controls)

        boss_action_canvas = QWidget()
        boss_action_canvas_layout = QVBoxLayout(boss_action_canvas)
        boss_action_canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.boss_stats_action_timeline = TeamSkillTimeline(self.store)
        self.boss_stats_action_timeline.viewChanged.connect(
            self._boss_stats_action_view_changed
        )
        boss_action_canvas_layout.addWidget(
            self.boss_stats_action_timeline, 1
        )
        boss_action_range = QHBoxLayout()
        self.boss_stats_action_start = QLabel("0.0s")
        self.boss_stats_action_scrollbar = QScrollBar(Qt.Horizontal)
        self.boss_stats_action_scrollbar.setMinimumHeight(20)
        self.boss_stats_action_scrollbar.valueChanged.connect(
            lambda value: self.boss_stats_action_timeline.set_view_start(
                value / 10.0
            )
        )
        self.boss_stats_action_end = QLabel("总计 0.0s")
        boss_action_range.addWidget(self.boss_stats_action_start)
        boss_action_range.addWidget(self.boss_stats_action_scrollbar, 1)
        boss_action_range.addWidget(self.boss_stats_action_end)
        boss_action_canvas_layout.addLayout(boss_action_range)

        self.boss_stats_action_table = QTableWidget(0, 9)
        self.boss_stats_action_table.setHorizontalHeaderLabels(
            [
                "时间",
                "Boss",
                "动作",
                "动作码",
                "变体",
                "动作类型",
                "名称状态",
                "包签名/长度",
                "解析证据",
            ]
        )
        self.boss_stats_action_table.verticalHeader().setVisible(False)
        self.boss_stats_action_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        action_table_header = self.boss_stats_action_table.horizontalHeader()
        action_table_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        action_table_header.setSectionResizeMode(2, QHeaderView.Stretch)
        action_table_header.setSectionResizeMode(8, QHeaderView.Stretch)
        boss_action_splitter = QSplitter(Qt.Vertical)
        boss_action_splitter.addWidget(boss_action_canvas)
        boss_action_splitter.addWidget(self.boss_stats_action_table)
        boss_action_splitter.setStretchFactor(0, 3)
        boss_action_splitter.setStretchFactor(1, 2)
        boss_action_layout.addWidget(boss_action_splitter, 1)
        self.boss_stats_tabs.addTab(boss_action_page, "Boss动作时间轴")

        layout.addWidget(self.boss_stats_tabs, 1)
        return page


    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        self._ui_refresh_pending = False
        self.ensure_visible_layout()


    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_layout_finalize()


    def ensure_visible_layout(self) -> None:
        """Settle nested tab geometry after an outer tab/maximize transition."""
        self._schedule_layout_finalize()
        # The outer QTabWidget and maximized main window complete layout in
        # separate event-loop passes. Re-run after both passes rather than
        # requiring a manual restore/maximize cycle from the user.
        QTimer.singleShot(80, self._schedule_layout_finalize)
        QTimer.singleShot(240, self._schedule_layout_finalize)
        QTimer.singleShot(500, self._schedule_layout_finalize)


    def _warm_team_score_workers(self) -> None:
        """Warm score workers only after capture, never while the game is live."""
        if self._timeline_worker_warmup_started:
            return
        self._timeline_worker_warmup_started = True
        warm_timeline_process_pool(16)


    def _schedule_layout_finalize(self) -> None:
        if self._layout_finalize_scheduled:
            return
        self._layout_finalize_scheduled = True
        QTimer.singleShot(0, self._finalize_visible_layout)


    def _finalize_visible_layout(self) -> None:
        self._layout_finalize_scheduled = False
        if not self.isVisible():
            return
        if self.layout() is not None:
            self.layout().invalidate()
            self.layout().activate()
        self.live_dps_tabs.updateGeometry()
        current_page = self.live_dps_tabs.currentWidget()
        if current_page is not None and current_page.layout() is not None:
            current_page.layout().invalidate()
            current_page.layout().activate()
            current_page.updateGeometry()
        if self.live_dps_tabs.currentIndex() == 0:
            left_panel = self.statistics_splitter.widget(0)
            if left_panel is not None and left_panel.layout() is not None:
                left_panel.layout().invalidate()
                left_panel.layout().activate()
                left_panel.updateGeometry()
            self.players_table.updateGeometry()
            curve_page = self.curve_tabs.currentWidget()
            if curve_page is not None and curve_page.layout() is not None:
                curve_page.layout().invalidate()
                curve_page.layout().activate()
                curve_page.updateGeometry()
            self.curve_tabs.updateGeometry()
            self.curve.refresh_geometry()
            self.statistics_splitter.updateGeometry()
            self.statistics_splitter.update()
        else:
            self.curve_tabs.updateGeometry()
            self.curve.updateGeometry()
            self.curve.update()
        if self.live_dps_tabs.currentIndex() == 1:
            self.boss_stats_tabs.updateGeometry()
            boss_page = self.boss_stats_tabs.currentWidget()
            if boss_page is not None and boss_page.layout() is not None:
                boss_page.layout().activate()
                boss_page.updateGeometry()
        # Propagate the new size hints through the outer application tabs. A
        # native maximize/restore used to be the only event that performed this
        # propagation after loading a replay.
        layout_chain: list[QWidget] = []
        widget: QWidget | None = self
        while widget is not None:
            layout_chain.append(widget)
            widget.updateGeometry()
            widget = widget.parentWidget()
        for widget in reversed(layout_chain):
            layout = widget.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
        self._resize_boss_effect_overview_columns()
        self._resize_player_buff_overview_columns()


    def _resize_boss_effect_overview_columns(self) -> None:
        table = self.boss_effects_table
        available = max(0, table.viewport().width() - 1)
        if available <= 0:
            return
        minimums = (105, 68, 100, 120, 76, 95, 60, 82, 70)
        weights = (0.15, 0.08, 0.14, 0.18, 0.10, 0.12, 0.06, 0.10, 0.07)
        minimum_total = sum(minimums)
        if available >= minimum_total:
            remaining = available - minimum_total
            widths = [
                minimum + int(remaining * weight)
                for minimum, weight in zip(minimums, weights)
            ]
        else:
            scale = available / minimum_total
            widths = [max(36, int(minimum * scale)) for minimum in minimums]
        difference = available - sum(widths)
        if difference >= 0:
            widths[3] += difference
        else:
            overflow = -difference
            for index in sorted(
                range(len(widths)), key=widths.__getitem__, reverse=True
            ):
                reduction = min(overflow, max(0, widths[index] - 36))
                widths[index] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)


    def _resize_player_buff_overview_columns(self) -> None:
        table = self.player_buffs_table
        available = max(0, table.viewport().width() - 1)
        if available <= 0:
            return
        # Fit all columns inside the viewport. The former fixed minimum could
        # make the total wider than the table while horizontal scrolling was
        # disabled, leaving the rightmost cells outside the visible area.
        minimums = (58, 58, 52, 110, 80, 62, 54)
        weights = (0.14, 0.10, 0.10, 0.30, 0.18, 0.10, 0.08)
        minimum_total = sum(minimums)
        if available >= minimum_total:
            remaining = available - minimum_total
            widths = [
                minimum + int(remaining * weight)
                for minimum, weight in zip(minimums, weights)
            ]
        else:
            scale = available / minimum_total
            widths = [max(36, int(minimum * scale)) for minimum in minimums]
        difference = available - sum(widths)
        if difference >= 0:
            widths[3] += difference
        else:
            overflow = -difference
            for index in sorted(
                range(len(widths)), key=widths.__getitem__, reverse=True
            ):
                reduction = min(overflow, max(0, widths[index] - 36))
                widths[index] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)


    def _live_dps_tab_changed(self, index: int) -> None:
        if self.collector.aggregator.revision > 0:
            self._begin_view_progress(index)
        self._schedule_ui_refresh()
        self.ensure_visible_layout()


    def _live_dps_curve_tab_changed(self, _index: int) -> None:
        if self.isVisible() and self.live_dps_tabs.currentIndex() == 0:
            if self.collector.aggregator.revision > 0:
                self._begin_view_progress(0)
            self._schedule_ui_refresh()
            self.ensure_visible_layout()


    def _boss_statistics_tab_changed(self, _index: int) -> None:
        if self.collector.aggregator.revision > 0:
            self._begin_view_progress(1)
        self._schedule_ui_refresh()


    def _boss_stats_effect_detail_tab_changed(self, index: int) -> None:
        # The overview and detail tables share the same top-level Boss tab.
        # Invalidate that cached view so the detail table is populated when it
        # is opened for the first time after loading a replay.
        self._rendered_view_revisions.pop((1, 2), None)
        if self.collector.aggregator.revision > 0:
            self._begin_view_progress(
                1,
                (
                    "正在生成 Boss 状态明细..."
                    if index == 1
                    else "正在生成 Boss 状态总览..."
                ),
            )
        self._schedule_ui_refresh()


    def _schedule_ui_refresh(self) -> None:
        self._ui_refresh_pending = True
        if not self.isVisible() or self._ui_refresh_scheduled:
            return
        self._ui_refresh_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_ui_refresh)


    def _run_scheduled_ui_refresh(self) -> None:
        self._ui_refresh_scheduled = False
        if not self.isVisible() or not self._ui_refresh_pending:
            return
        if self._refresh_running:
            self._schedule_ui_refresh()
            return
        self._ui_refresh_pending = False
        self._refresh()
