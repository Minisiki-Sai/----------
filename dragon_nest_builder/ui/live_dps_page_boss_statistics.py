from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *
from ..live_dps.critical_stats import is_critical_rate_sample

class LiveDpsBossStatisticsMixin:
    def _timeline_zoom_changed(self, value: int) -> None:
        self.timeline_zoom_value.setText(f"{value} px/s")
        self.team_skill_timeline.set_pixels_per_second(value)


    @staticmethod
    def _curve_pixels_changed(curve: QWidget, label: QLabel, value: int) -> None:
        label.setText(f"{value} px/s")
        curve.set_pixels_per_second(value)


    def _damage_curve_view_changed(
        self, start_seconds: float, maximum_start: float, window_seconds: float
    ) -> None:
        self.damage_curve_scrollbar.blockSignals(True)
        self.damage_curve_scrollbar.setRange(
            0, max(0, round(maximum_start * 10))
        )
        self.damage_curve_scrollbar.setPageStep(
            max(1, round(window_seconds * 10))
        )
        self.damage_curve_scrollbar.setValue(round(start_seconds * 10))
        self.damage_curve_scrollbar.blockSignals(False)
        extent = self.curve._extent_seconds
        self.damage_curve_range_start.setText(f"{start_seconds:.1f}s")
        self.damage_curve_range_end.setText(f"总计 {extent:.1f}s")


    def _damage_curve_scroll_changed(self, value: int) -> None:
        self.curve.set_view_start(value / 10.0)


    @staticmethod
    def _sync_curve_scrollbar(
        scrollbar: QScrollBar,
        start_label: QLabel,
        end_label: QLabel,
        start_seconds: float,
        maximum_start: float,
        window_seconds: float,
        extent_seconds: float,
    ) -> None:
        scrollbar.blockSignals(True)
        scrollbar.setRange(0, max(0, round(maximum_start * 10)))
        scrollbar.setPageStep(max(1, round(window_seconds * 10)))
        scrollbar.setValue(round(start_seconds * 10))
        scrollbar.blockSignals(False)
        start_label.setText(f"{start_seconds:.1f}s")
        end_label.setText(f"总计 {extent_seconds:.1f}s")


    def _boss_effect_curve_view_changed(
        self, start_seconds: float, maximum_start: float, window_seconds: float
    ) -> None:
        if not hasattr(self, "boss_effect_scrollbar"):
            return
        self._sync_curve_scrollbar(
            self.boss_effect_scrollbar,
            self.boss_effect_range_start,
            self.boss_effect_range_end,
            start_seconds,
            maximum_start,
            window_seconds,
            self.boss_effect_curve._extent_seconds,
        )
        if self.boss_detail_tabs.currentIndex() == 1:
            self._schedule_boss_detail_refresh()


    def _player_buff_curve_view_changed(
        self, start_seconds: float, maximum_start: float, window_seconds: float
    ) -> None:
        if not hasattr(self, "player_buff_scrollbar"):
            return
        self._sync_curve_scrollbar(
            self.player_buff_scrollbar,
            self.player_buff_range_start,
            self.player_buff_range_end,
            start_seconds,
            maximum_start,
            window_seconds,
            self.player_buff_curve._extent_seconds,
        )


    def _player_buff_time_selected(self, seconds: float) -> None:
        self._player_buff_selected_seconds = max(0.0, float(seconds))
        self.player_buff_time_label.setText(
            f"明细时点：{self._player_buff_selected_seconds:.3f}s"
        )
        self.player_buff_tabs.setCurrentIndex(1)
        self._schedule_player_buff_detail_refresh()


    def _player_buff_return_latest(self) -> None:
        self._player_buff_selected_seconds = None
        self.player_buff_curve._selected_seconds = None
        self.player_buff_curve.update()
        self.player_buff_time_label.setText("明细时点：最新")
        self._schedule_player_buff_detail_refresh()


    def _boss_stats_damage_curve_view_changed(
        self,
        start_seconds: float,
        maximum_start: float,
        window_seconds: float,
    ) -> None:
        self.boss_stats_curve_scrollbar.blockSignals(True)
        self.boss_stats_curve_scrollbar.setRange(
            0, max(0, round(maximum_start * 10))
        )
        self.boss_stats_curve_scrollbar.setPageStep(
            max(1, round(window_seconds * 10))
        )
        self.boss_stats_curve_scrollbar.setValue(round(start_seconds * 10))
        self.boss_stats_curve_scrollbar.blockSignals(False)
        self.boss_stats_curve_start.setText(f"{start_seconds:.1f}s")
        self.boss_stats_curve_end.setText(
            f"总计 {self.boss_stats_damage_curve._extent_seconds:.1f}s"
        )


    def _boss_stats_effect_curve_view_changed(
        self, start_seconds: float, maximum_start: float, window_seconds: float
    ) -> None:
        if not hasattr(self, "boss_stats_effect_scrollbar"):
            return
        self._sync_curve_scrollbar(
            self.boss_stats_effect_scrollbar,
            self.boss_stats_effect_range_start,
            self.boss_stats_effect_range_end,
            start_seconds,
            maximum_start,
            window_seconds,
            self.boss_stats_effect_curve._extent_seconds,
        )
        if self.boss_stats_effect_tabs.currentIndex() == 1:
            self._schedule_ui_refresh()


    def _boss_stats_buff_curve_view_changed(
        self, start_seconds: float, maximum_start: float, window_seconds: float
    ) -> None:
        if not hasattr(self, "boss_stats_buff_scrollbar"):
            return
        self._sync_curve_scrollbar(
            self.boss_stats_buff_scrollbar,
            self.boss_stats_buff_range_start,
            self.boss_stats_buff_range_end,
            start_seconds,
            maximum_start,
            window_seconds,
            self.boss_stats_buff_curve._extent_seconds,
        )


    def _set_boss_stats_realtime_seconds(self, seconds: float) -> None:
        self._boss_stats_realtime_follow_latest = False
        self._boss_stats_realtime_seconds = max(0.0, float(seconds))
        self.boss_stats_damage_curve.set_selected_seconds(
            self._boss_stats_realtime_seconds
        )
        self.boss_stats_effect_curve.set_selected_seconds(
            self._boss_stats_realtime_seconds
        )
        self._fill_boss_stats_realtime_table()


    def _boss_stats_time_selected(self, seconds: float) -> None:
        self._boss_stats_pending_hover_seconds = None
        self._set_boss_stats_realtime_seconds(seconds)


    def _boss_stats_time_hovered(self, seconds: float) -> None:
        self._boss_stats_pending_hover_seconds = max(0.0, float(seconds))
        if self._boss_stats_hover_update_scheduled:
            return
        self._boss_stats_hover_update_scheduled = True
        QTimer.singleShot(30, self._apply_boss_stats_hover_seconds)


    def _apply_boss_stats_hover_seconds(self) -> None:
        self._boss_stats_hover_update_scheduled = False
        seconds = self._boss_stats_pending_hover_seconds
        self._boss_stats_pending_hover_seconds = None
        if seconds is None:
            return
        if (
            not self._boss_stats_realtime_follow_latest
            and self._boss_stats_realtime_seconds is not None
            and abs(seconds - self._boss_stats_realtime_seconds) < 0.025
        ):
            return
        self._set_boss_stats_realtime_seconds(seconds)


    def _boss_stats_realtime_follow_latest_clicked(self) -> None:
        self._boss_stats_pending_hover_seconds = None
        self._boss_stats_realtime_follow_latest = True
        extent = sum(
            max(0.0, end - start)
            for start, end in self._boss_stats_realtime_windows
        )
        self._boss_stats_realtime_seconds = max(0.0, extent - 0.001)
        self.boss_stats_damage_curve.set_selected_seconds(
            self._boss_stats_realtime_seconds
        )
        self.boss_stats_effect_curve.set_selected_seconds(
            self._boss_stats_realtime_seconds
        )
        self._fill_boss_stats_realtime_table()


    def _fill_boss_stats_realtime_table(self) -> None:
        table = getattr(self, "boss_stats_realtime_table", None)
        if table is None:
            return
        windows = self._boss_stats_realtime_windows
        groups = self._boss_stats_realtime_groups
        if not windows or not groups:
            table.setRowCount(0)
            self.boss_stats_realtime_time_label.setText("时点：暂无")
            return
        extent = sum(max(0.0, end - start) for start, end in windows)
        selected_seconds = self._boss_stats_realtime_seconds
        if selected_seconds is None or self._boss_stats_realtime_follow_latest:
            selected_seconds = max(0.0, extent - 0.001)
            self._boss_stats_realtime_seconds = selected_seconds
        selected_seconds = max(0.0, min(float(selected_seconds), extent))
        source_seconds = _source_seconds_from_compressed_windows(
            selected_seconds, windows
        )
        if source_seconds is None:
            table.setRowCount(0)
            self.boss_stats_realtime_time_label.setText("时点：暂无")
            return
        self.boss_stats_realtime_time_label.setText(
            f"时点：轴 {selected_seconds:.1f}s / 实战 {source_seconds:.1f}s"
        )
        active_groups = tuple(
            group
            for group in groups
            if any(
                start - 1e-6 <= source_seconds <= end + 1e-6
                for start, end in group.phase_windows
            )
        )
        table.setUpdatesEnabled(False)
        table.setRowCount(len(active_groups))
        for row_index, group in enumerate(active_groups):
            summary = dict(
                self.collector.aggregator.boss_effect_summary_at(
                    group.target_ids,
                    round(source_seconds * 1000),
                )
            )
            matched_preset = _team_score_match_boss_preset(group.name)
            preset_name = matched_preset or _TEAM_SCORE_DEFAULT_BOSS_PRESET
            preset = BOSS_PRESETS[preset_name]
            metrics = _boss_realtime_metrics(summary, preset)
            resistances = metrics["resistances"]
            hp_value = _boss_hp_value_at_compressed_seconds(
                self._boss_stats_realtime_hp_rows,
                group.name,
                selected_seconds,
            )
            hp_text = f"{hp_value[0]:.2f}%" if hp_value else "--"
            values = [
                group.name,
                hp_text,
                (
                    f"{metrics['universal_amp']:g}% / "
                    f"{metrics['physical_amp']:g}% / "
                    f"{metrics['magical_amp']:g}%"
                ),
                " / ".join(
                    f"{element}{as_number(resistances[element]):g}%"
                    for element in ("火", "水", "光", "暗")
                ),
                (
                    f"{_compact_number(as_number(metrics['physical_defense']))} / "
                    f"{_compact_number(as_number(metrics['magical_defense']))}"
                ),
                _compact_number(as_number(metrics["critical_resist"])),
                f"{as_number(metrics['freeze_layers']):g}层",
                f"{as_number(metrics['attack_down']):g}%",
            ]
            base_tip = (
                f"基础参数：{preset_name}"
                + ("（名称匹配）" if matched_preset else "（默认估算基准）")
                + f"；基础四抗 {as_number(preset.get('element_resist')):g}%"
                + f"；基础防御 {int(as_number(preset.get('monster_defense')))}"
                + f"；基础致抗 {int(as_number(preset.get('crit_resist')))}"
            )
            column_tips = [
                f"实战时点 {source_seconds:.3f}s",
                (
                    "服务器记录"
                    if hp_value and hp_value[1] == "spawn_observed"
                    else "按拆包血量与服务器机制结算触发器计算"
                    if hp_value
                    and hp_value[1]
                    == "fixed_reference_with_mechanism_completion"
                    else "按固定 Boss 血量计算"
                    if hp_value and hp_value[1] == "fixed_reference"
                    else "按完整实战伤害推算"
                    if hp_value
                    else "该时点没有可用血量记录"
                ),
                "依次为通用增伤 / 物理总增伤 / 魔法总增伤；物理、魔法总增伤已包含通用增伤",
                base_tip
                + "；实时减抗："
                + " / ".join(
                    f"{element}{as_number(summary.get(f'减{element}抗%')):g}%"
                    for element in ("火", "水", "光", "暗")
                ),
                base_tip
                + f"；实时减物防 {as_number(metrics['physical_defense_down']):g}%"
                + f" / 减魔防 {as_number(metrics['magical_defense_down']):g}%",
                base_tip
                + f"；实时减致命抵抗 {as_number(metrics['critical_resist_down']):g}%",
                "该时点实时冰冻层数，最多2层",
                "该时点最高生效的减少攻击效果",
            ]
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (0, 3)
                    else Qt.AlignCenter,
                )
                item.setToolTip(column_tips[column])
                table.setItem(row_index, column, item)
        table.setUpdatesEnabled(True)
        if not active_groups:
            self.boss_stats_realtime_note.setText(
                "该时点不在所选主要 Boss 的有效阶段内。单击下方曲线中的 Boss 底色区域查看实时状态。"
            )
        else:
            self.boss_stats_realtime_note.setText(
                "单击下方伤害曲线或 Boss 状态曲线选择时点；抗性、防御和致命抵抗为基础参数扣除该时刻实时 Debuff 后的结果。"
            )


    def _sync_boss_statistics_selector(
        self, groups: tuple[_BossStatisticsGroup, ...]
    ) -> None:
        signature = tuple(
            (
                group.identity,
                group.name,
                group.max_hp,
                tuple(sorted(group.target_ids)),
            )
            for group in groups
        )
        if signature == self._boss_stats_group_signature:
            return
        first_population = not self._boss_stats_group_signature
        self._boss_stats_group_signature = signature
        available = {group.identity for group in groups}
        if first_population:
            self._boss_stats_selected_keys = set(available)
        else:
            self._boss_stats_selected_keys.intersection_update(available)

        while self.boss_stats_selector_layout.count():
            item = self.boss_stats_selector_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boss_stats_selector_checks.clear()
        for group in groups:
            check = QCheckBox(group.name)
            check.setChecked(group.identity in self._boss_stats_selected_keys)
            check.setToolTip(
                f"{group.name} · {_compact_number(group.damage)} · "
                f"最大生命 {_compact_number(group.max_hp) if group.max_hp else '未知'} · "
                f"{len(group.target_ids)} 个阶段实体"
            )
            check.toggled.connect(
                lambda checked, identity=group.identity: (
                    self._boss_statistics_selection_changed(identity, checked)
                )
            )
            self.boss_stats_selector_layout.addWidget(check)
            self._boss_stats_selector_checks[group.identity] = check
        self.boss_stats_selector_layout.addStretch(1)
        self._update_boss_statistics_selection_label()


    def _update_boss_statistics_selection_label(self) -> None:
        selected_count = sum(
            group.identity in self._boss_stats_selected_keys
            for group in self._boss_stats_groups
        )
        total_count = len(self._boss_stats_groups)
        self.boss_stats_selection_label.setText(
            f"已选择 {selected_count}/{total_count} 个 Boss"
            if total_count
            else "尚未识别 Boss"
        )


    def _boss_statistics_selection_changed(
        self, identity: tuple[str, str], checked: bool
    ) -> None:
        if checked:
            self._boss_stats_selected_keys.add(identity)
        else:
            self._boss_stats_selected_keys.discard(identity)
        self._update_boss_statistics_selection_label()
        for view_key in list(self._rendered_view_revisions):
            if view_key[0] == 1:
                self._rendered_view_revisions.pop(view_key, None)
        self._begin_view_progress(1, "正在按新的 Boss 范围重新统计...")
        self._schedule_ui_refresh()


    def _set_all_boss_statistics_selected(self, selected: bool) -> None:
        self._boss_stats_selected_keys = (
            {group.identity for group in self._boss_stats_groups}
            if selected
            else set()
        )
        for identity, check in self._boss_stats_selector_checks.items():
            check.blockSignals(True)
            check.setChecked(identity in self._boss_stats_selected_keys)
            check.blockSignals(False)
        self._update_boss_statistics_selection_label()
        for view_key in list(self._rendered_view_revisions):
            if view_key[0] == 1:
                self._rendered_view_revisions.pop(view_key, None)
        self._begin_view_progress(1, "正在按新的 Boss 范围重新统计...")
        self._schedule_ui_refresh()


    def _boss_stats_content_filter_changed(
        self, _index: int | None = None
    ) -> None:
        for view_key in list(self._rendered_view_revisions):
            if view_key[0] == 1:
                self._rendered_view_revisions.pop(view_key, None)
        self._begin_view_progress(1, self._view_progress_label(1))
        self._schedule_ui_refresh()


    def _sync_boss_stats_player_combo(
        self,
        combo: QComboBox,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
        all_label: str,
    ) -> None:
        selected = str(combo.currentData() or "")
        target_ids = {
            target_id for group in groups for target_id in group.target_ids
        }
        player_ids: list[str] = []
        for event in event_platform_for(snapshot).combat_events:
            if (
                event.healing
                or event.target_id not in target_ids
                or event.source_id in player_ids
            ):
                continue
            player_ids.append(event.source_id)
        player_details = self._player_display_details(snapshot)
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "")
        for source_id in player_ids:
            name, job = player_details.get(source_id, (source_id, ""))
            combo.addItem(f"{name} · {job}" if job else name, source_id)
        selected_index = combo.findData(selected)
        combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        combo.blockSignals(False)


    def _fill_boss_statistics_damage_curve(
        self,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
        elapsed_seconds: float,
    ) -> None:
        self._sync_boss_stats_player_combo(
            self.boss_stats_curve_player_combo,
            snapshot,
            groups,
            "全部角色",
        )
        selected_source = str(
            self.boss_stats_curve_player_combo.currentData() or ""
        )
        target_ids = {
            target_id for group in groups for target_id in group.target_ids
        }
        selected_windows = _merge_seconds_windows(
            [window for group in groups for window in group.phase_windows]
        )
        selected_damage_events = tuple(
            event
            for event in event_platform_for(snapshot).combat_events
            if not event.healing
            and event.target_id in target_ids
            and (not selected_source or event.source_id == selected_source)
        )
        compressed_events, compressed_seconds = _compress_events_to_windows(
            selected_damage_events,
            selected_windows,
            preserve_clock=True,
        )
        series_by_source: dict[str, dict[int, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for event in compressed_events:
            series_by_source[event.source_id][event.timestamp_ms // 1000] += (
                event.amount
            )
        player_details = self._player_display_details(snapshot)
        named_series = []
        for source_id, points in series_by_source.items():
            name, job = player_details.get(source_id, (source_id, ""))
            label = f"{name} · {job}" if job else name
            named_series.append(
                (label, tuple(sorted(points.items())))
            )
        named_series.sort(
            key=lambda item: -sum(value for _second, value in item[1])
        )
        self.boss_stats_damage_curve.set_named_series(
            tuple(named_series), max(elapsed_seconds, compressed_seconds)
        )
        self.boss_stats_damage_curve.set_boss_hp_series(
            self._boss_stats_realtime_hp_rows
        )
        self.boss_stats_damage_curve.set_boss_phases(
            _compress_boss_phases_to_windows(
                groups, selected_windows, preserve_clock=True
            )
        )


    def _ensure_boss_hp_context(
        self,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
    ) -> None:
        revision = self.collector.aggregator.revision
        if revision == self._boss_hp_context_revision:
            return
        (
            self._fixed_boss_hp_profile_label,
            self._fixed_boss_hp_references,
        ) = resolve_fixed_boss_hp_profile(
            (group.name for group in groups),
            capture_hint=self.collector.status,
            target_ids=(
                target_id
                for group in groups
                for target_id in group.target_ids
            ),
        )
        self._completed_boss_group_keys = _completed_boss_group_identities(
            groups,
            event_platform_for(snapshot).boss_hp_tracks,
            terminal_skill_casts=event_platform_for(snapshot).skill_cast_events,
            fixed_boss_hp_references=self._fixed_boss_hp_references,
        )
        self._boss_hp_context_revision = revision


    def _fill_boss_statistics(
        self,
        snapshot: CombatSnapshot,
        elapsed_seconds: float,
    ) -> None:
        revision = self.collector.aggregator.revision
        self._advance_view_progress(
            "boss", 46, "正在识别并合并主要 Boss 与阶段实体..."
        )
        if revision != self._boss_stats_groups_revision:
            self._boss_stats_groups = _boss_statistics_groups(snapshot)
            self._boss_stats_groups_revision = revision
        self._ensure_boss_hp_context(snapshot, self._boss_stats_groups)
        self._advance_view_progress(
            "boss", 58, "Boss 分组完成，正在应用所选 Boss 范围..."
        )
        self._sync_boss_statistics_selector(self._boss_stats_groups)
        selected_groups = tuple(
            group
            for group in self._boss_stats_groups
            if group.identity in self._boss_stats_selected_keys
        )
        selected_ids = frozenset(
            target_id
            for group in selected_groups
            for target_id in group.target_ids
        )
        selected_windows = _merge_seconds_windows(
            [window for group in selected_groups for window in group.phase_windows]
        )
        self._boss_stats_realtime_groups = selected_groups
        self._boss_stats_realtime_windows = selected_windows
        self._boss_stats_realtime_hp_rows = _compress_boss_hp_tracks_to_windows(
            event_platform_for(snapshot).boss_hp_tracks,
            selected_groups,
            selected_windows,
            infer_from_damage=True,
            completed_group_identities=self._completed_boss_group_keys,
            fixed_boss_hp_references=self._fixed_boss_hp_references,
            preserve_clock=True,
        )
        compressed_extent = max(
            (end for _start, end in selected_windows), default=0.0
        )
        if self._boss_stats_realtime_follow_latest:
            self._boss_stats_realtime_seconds = max(
                0.0, compressed_extent - 0.001
            )
        elif self._boss_stats_realtime_seconds is not None:
            self._boss_stats_realtime_seconds = min(
                max(0.0, self._boss_stats_realtime_seconds),
                compressed_extent,
            )
        self.boss_stats_damage_curve.set_selected_seconds(
            self._boss_stats_realtime_seconds
        )
        self.boss_stats_effect_curve.set_selected_seconds(
            self._boss_stats_realtime_seconds
        )
        self._fill_boss_stats_realtime_table()
        self._fill_boss_statistics_summary(snapshot, selected_groups)
        self._advance_view_progress(
            "boss", 68, self._view_progress_label(1)
        )
        active_page = self.boss_stats_tabs.currentIndex()
        if active_page == 0:
            self._fill_boss_statistics_skills(snapshot, selected_groups)
        elif active_page == 1:
            self._fill_boss_statistics_damage_curve(
                snapshot, selected_groups, elapsed_seconds
            )
        elif active_page == 2:
            source_current_seconds = max(
                elapsed_seconds,
                event_platform_for(snapshot).boss_effect_current_seconds,
            )
            effect_view = self.collector.aggregator.boss_effect_view(
                selected_ids,
                round(source_current_seconds * 1000),
            )
            effect_view, current_seconds = _compress_effect_view_to_windows(
                effect_view,
                selected_windows,
                preserve_clock=True,
            )
            self._fill_boss_statistics_effects(
                snapshot,
                effect_view,
                current_seconds,
                self._boss_effect_output_windows(
                    snapshot, selected_groups
                ),
                self._boss_freeze_summary_rows(
                    selected_groups, source_current_seconds
                ),
            )
        elif active_page == 3:
            self._fill_boss_statistics_buffs(
                snapshot, selected_windows, elapsed_seconds
            )
        elif active_page == 4:
            self._fill_boss_statistics_timeline(
                snapshot,
                selected_ids,
                selected_windows,
                elapsed_seconds,
            )
        elif active_page == 5:
            self._fill_boss_action_timeline(snapshot, selected_groups)
        self._advance_view_progress("boss", 94, "正在更新表格和时间轴显示...")


    def _fill_boss_statistics_summary(
        self,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
    ) -> None:
        self.boss_stats_summary_table.setRowCount(len(groups))
        for row_index, group in enumerate(groups):
            critical_samples = (
                group.hits
                if group.critical_samples is None
                else group.critical_samples
            )
            critical_rate = (
                group.critical_hits / critical_samples
                if critical_samples
                else 0.0
            )
            values = [
                group.name,
                _compact_number(group.damage),
                _compact_number(group.damage / group.active_seconds),
                str(group.hits),
                f"{critical_rate * 100:.1f}%",
                f"{group.active_seconds:.1f}s",
                (
                    f"{group.damage / event_platform_for(snapshot).total_damage * 100:.1f}%"
                    if event_platform_for(snapshot).total_damage
                    else "0.0%"
                ),
            ]
            for column, value in enumerate(values):
                self.boss_stats_summary_table.setItem(
                    row_index,
                    column,
                    self._item(
                        value,
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column == 0
                        else Qt.AlignCenter,
                    ),
                )


    def _fill_boss_statistics_skills(
        self,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
    ) -> None:
        self._sync_boss_stats_player_combo(
            self.boss_stats_skills_player_combo,
            snapshot,
            groups,
            "全部角色（按角色统计）",
        )
        selected_source = str(
            self.boss_stats_skills_player_combo.currentData() or ""
        )
        selected_skill_mode = bool(selected_source)
        self.boss_stats_skills_table.setHorizontalHeaderLabels(
            [
                "Boss",
                "角色",
                "职业",
                "技能" if selected_skill_mode else "统计项",
                "伤害",
                "占该角色" if selected_skill_mode else "占该Boss",
                "释放次数",
                "命中",
                "平均Hit",
                "暴击",
                "暴击率",
            ]
        )
        group_by_target = {
            target_id: group
            for group in groups
            for target_id in group.target_ids
        }
        totals: dict[tuple[object, ...], dict[str, object]] = {}
        player_boss_damage: Counter[tuple[tuple[str, str], str]] = Counter()
        boss_skill_casts: Counter[tuple[tuple[str, str], str, str]] = Counter()
        boss_skill_direct_casts: Counter[
            tuple[tuple[str, str], str, str]
        ] = Counter()
        boss_skill_inferred_casts: Counter[
            tuple[tuple[str, str], str, str]
        ] = Counter()
        if selected_skill_mode:
            for cast in event_platform_for(snapshot).skill_cast_events:
                if cast.source_id != selected_source:
                    continue
                cast_seconds = cast.timestamp_ms / 1000.0
                cast_skill_key = canonical_skill(
                    cast.skill_name or cast.skill_id
                )
                if not cast_skill_key:
                    continue
                packet_kind = str(cast.packet_kind or "").casefold()
                inferred = (
                    "inferred" in packet_kind or "recovered" in packet_kind
                )
                for group in groups:
                    if not any(
                        start - 0.001 <= cast_seconds <= end + 0.001
                        for start, end in group.phase_windows
                    ):
                        continue
                    cast_key = (
                        group.identity,
                        cast.source_id,
                        cast_skill_key,
                    )
                    boss_skill_casts[cast_key] += 1
                    if inferred:
                        boss_skill_inferred_casts[cast_key] += 1
                    else:
                        boss_skill_direct_casts[cast_key] += 1
        for event in event_platform_for(snapshot).combat_events:
            group = group_by_target.get(event.target_id)
            if (
                group is None
                or event.healing
                or (selected_source and event.source_id != selected_source)
            ):
                continue
            player_boss_damage[(group.identity, event.source_id)] += event.amount
            key = (group.identity, event.source_id)
            if selected_skill_mode:
                key += (event.skill_id or event.skill_name,)
            row = totals.setdefault(
                key,
                {
                    "boss": group.name,
                    "source_id": event.source_id,
                    "source_name": event.source_name,
                    "job": event.job,
                    "skill": (
                        event.skill_name or event.skill_id
                        if selected_skill_mode
                        else "角色合计"
                    ),
                    "skill_key": canonical_skill(
                        event.skill_name or event.skill_id
                    ),
                    "damage": 0,
                    "hits": 0,
                    "critical": 0,
                    "critical_samples": 0,
                    "boss_damage": group.damage,
                    "boss_order": group.first_ms,
                    "group_identity": group.identity,
                },
            )
            row["damage"] += event.amount
            row["hits"] += 1
            if is_critical_rate_sample(event):
                row["critical_samples"] += 1
                row["critical"] += int(event.critical)
        player_details = self._player_display_details(snapshot)
        rows = sorted(
            totals.values(),
            key=lambda row: (
                int(row["boss_order"]),
                -int(row["damage"]),
                str(row["skill"]),
            ),
        )
        self.boss_stats_skills_table.setUpdatesEnabled(False)
        self.boss_stats_skills_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            critical = int(row["critical"])
            critical_samples = int(row["critical_samples"])
            # A subtype4/passive result adds damage but is not another
            # game-facing Hit. The same independent-hit evidence used for
            # critical rolls supplies the per-skill Hit numerator.
            hits = critical_samples
            source_name, source_job = player_details.get(
                str(row["source_id"]),
                (str(row["source_name"]), str(row["job"])),
            )
            denominator = (
                player_boss_damage[
                    (row["group_identity"], str(row["source_id"]))
                ]
                if selected_skill_mode
                else int(row["boss_damage"])
            )
            cast_key = (
                row["group_identity"],
                str(row["source_id"]),
                str(row["skill_key"]),
            )
            cast_count = (
                int(boss_skill_casts[cast_key]) if selected_skill_mode else 0
            )
            values = [
                str(row["boss"]),
                source_name,
                source_job,
                str(row["skill"]),
                _compact_number(int(row["damage"])),
                (
                    f"{int(row['damage']) / denominator * 100:.1f}%"
                    if denominator
                    else "0.0%"
                ),
                str(cast_count) if cast_count else "—",
                str(hits),
                (
                    f"{hits / cast_count:.2f}".rstrip("0").rstrip(".")
                    if cast_count
                    else "—"
                ),
                str(critical),
                (
                    f"{critical / critical_samples * 100:.1f}%"
                    if critical_samples
                    else "0.0%"
                ),
            ]
            color = TeamSkillTimeline._skill_color(str(row["skill"]))
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (0, 1, 2, 3)
                    else Qt.AlignCenter,
                )
                if column == 3 and selected_skill_mode:
                    item.setBackground(color.lighter(138))
                if column in (6, 8) and selected_skill_mode:
                    if cast_count:
                        item.setToolTip(
                            "实际施放证据："
                            f"{boss_skill_direct_casts[cast_key]} 次；"
                            "补全/推断："
                            f"{boss_skill_inferred_casts[cast_key]} 次"
                        )
                    else:
                        item.setToolTip("没有可靠的独立施放边界。")
                if column == 7 and int(row["hits"]) != hits:
                    item.setToolTip(
                        f"实际 Hit {hits}；伤害记录 {int(row['hits'])} 条。"
                        "附加／状态伤害计入伤害额，不重复计为技能 Hit。"
                    )
                self.boss_stats_skills_table.setItem(row_index, column, item)
        self.boss_stats_skills_table.setUpdatesEnabled(True)


    def _fill_boss_statistics_effects(
        self,
        snapshot: CombatSnapshot,
        view: BossEffectViewSnapshot,
        current_seconds: float,
        output_windows_by_target: dict[
            str, tuple[tuple[float, float], ...]
        ],
        freeze_summary_rows: tuple[BossEffectSnapshot, ...],
    ) -> None:
        summary = dict(view.summary)
        self.boss_stats_effect_summary.setText(
            "  |  ".join(
                [
                    f"火 -{summary.get('减火抗%', 0):g}%",
                    f"水 -{summary.get('减水抗%', 0):g}%",
                    f"光 -{summary.get('减光抗%', 0):g}%",
                    f"暗 -{summary.get('减暗抗%', 0):g}%",
                    f"通用增伤 {summary.get('增伤%', 0):g}%",
                    f"减物防 {summary.get('减物理防御%', 0):g}%",
                    f"减魔防 {summary.get('减魔法防御%', 0):g}%",
                    f"减致抗 {summary.get('减致命抵抗%', 0):g}%",
                    f"减攻击 {summary.get('减攻击%', 0):g}%",
                    f"叠冰 {summary.get('冰冻层', 0):g}层",
                ]
            )
            if view.effects
            else "所选 Boss 暂无状态记录"
        )
        self.boss_stats_effect_curve.set_series(view.series, current_seconds)
        overview_rows = _boss_effect_overview_rows(
            (*view.effects, *freeze_summary_rows)
        )
        self.boss_stats_effects_table.setUpdatesEnabled(False)
        self.boss_stats_effects_table.setRowCount(len(overview_rows))
        for row_index, effect in enumerate(overview_rows):
            freeze_summary = effect.effect_code == "freeze_layer_coverage"
            values = [
                effect.target_name or "未识别Boss",
                effect.status_kind,
                effect.label,
                effect.skill_name,
                effect.job or "未识别职业",
                self._status_value_text(effect.value, effect.parameters),
                str(effect.refresh_count),
                (
                    f"{_boss_effect_output_coverage(effect, output_windows_by_target) * 100:.1f}%"
                ),
            ]
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column < 6
                    else Qt.AlignCenter,
                )
                item.setToolTip(str(value))
                if column == 7:
                    item.setToolTip("仅统计该 Boss 可输出区间内的覆盖率")
                if freeze_summary:
                    item.setBackground(QColor("#dceff8"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.boss_stats_effects_table.setItem(row_index, column, item)
        self.boss_stats_effects_table.setUpdatesEnabled(True)

        if self.boss_stats_effect_tabs.currentIndex() != 1:
            return
        player_details = self._player_display_details(snapshot)
        window_start = self.boss_stats_effect_curve._view_start
        window_end = window_start + self.boss_stats_effect_curve._window_seconds
        visible_events = [
            event
            for event in view.events
            if window_start <= event.timestamp_seconds <= window_end
        ][-1500:]
        self.boss_stats_effect_details_table.setUpdatesEnabled(False)
        self.boss_stats_effect_details_table.setRowCount(len(visible_events))
        for row_index, effect in enumerate(visible_events):
            source = player_details.get(
                effect.source_id,
                (effect.source_name or effect.job or "未识别角色", effect.job),
            )[0]
            values = [
                f"{effect.timestamp_seconds:.3f}s",
                effect.target_name or "未识别Boss",
                effect.status_kind,
                effect.label,
                effect.skill_name,
                source,
                self._status_value_text(effect.value, effect.parameters),
                f"{effect.duration_seconds:.1f}s",
                f"{effect.end_seconds:.3f}s",
            ]
            for column, value in enumerate(values):
                self.boss_stats_effect_details_table.setItem(
                    row_index,
                    column,
                    self._item(
                        value,
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column in (1, 2, 3, 4, 5, 6)
                        else Qt.AlignCenter,
                    ),
                )
        self.boss_stats_effect_details_table.setUpdatesEnabled(True)


    def _fill_boss_statistics_buffs(
        self,
        snapshot: CombatSnapshot,
        windows: tuple[tuple[float, float], ...],
        elapsed_seconds: float,
    ) -> None:
        output_windows = _player_output_windows_seconds(
            event_platform_for(snapshot).combat_events
        )
        visible_series = _merge_player_buff_series_rows(
            tuple(
                row
                for row in event_platform_for(snapshot).player_buff_series
                if _is_catalogued_player_buff(row, self._live_buff_rows_by_skill)
            ),
            self._live_buff_rows_by_skill,
        )
        clipped_rows = _clip_player_buff_series_to_windows(
            visible_series, windows, output_windows
        )
        compressed_rows, compressed_seconds = (
            _compress_player_buff_series_to_windows(
                clipped_rows, windows, preserve_clock=True
            )
        )
        player_details = self._player_display_details(snapshot)
        self._sync_boss_stats_buff_filter_controls(
            clipped_rows, player_details
        )
        target_filter = str(
            self.boss_stats_buff_target_combo.currentData() or ""
        )
        target_job_filter = str(
            self.boss_stats_buff_target_job_combo.currentData() or ""
        )
        source_job_filter = str(
            self.boss_stats_buff_source_job_combo.currentData() or ""
        )
        type_filter = str(
            self.boss_stats_buff_type_combo.currentData() or ""
        )
        filtered_rows = tuple(
            row
            for row in clipped_rows
            if self._player_buff_filter_matches(
                row,
                target_filter,
                type_filter,
                target_job_filter,
                source_job_filter,
            )
        )
        points, label, unit = _player_buff_curve_points(
            compressed_rows,
            target_filter,
            type_filter,
            compressed_seconds,
            target_job_filter,
            source_job_filter,
        )
        self.boss_stats_buff_curve.set_buff_series(
            points, compressed_seconds, label, unit
        )
        phase_duration = sum(end - start for start, end in windows)
        self.boss_stats_buff_summary.setText(
            f"所选 Boss 阶段 {phase_duration:.1f}s  |  "
            f"筛选后 {len(filtered_rows)} / {len(clipped_rows)} 项角色增益"
            if windows
            else "请选择至少一个 Boss"
        )
        rows = sorted(
            filtered_rows,
            key=lambda row: (
                _player_buff_category_rank(
                    self._player_buff_effect_text(row), row.status_kind
                ),
                row.job or row.source_name,
                row.skill_name,
                row.target_job,
                row.target_name,
            ),
        )
        self.boss_stats_buffs_table.setUpdatesEnabled(False)
        self.boss_stats_buffs_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            target_name, target_job = player_details.get(
                row.target_id,
                (row.target_name or "未识别角色", row.target_job),
            )
            uptime = sum(end - start for start, end in row.intervals)
            target_measurement_windows = _merge_seconds_windows(
                [
                    (max(start, boss_start), min(end, boss_end))
                    for start, end in output_windows.get(row.target_id, ())
                    for boss_start, boss_end in windows
                    if max(start, boss_start) < min(end, boss_end)
                ]
            )
            target_output_duration = sum(
                end - start for start, end in target_measurement_windows
            )
            effect_text = self._player_buff_effect_text(row)
            values = [
                target_name,
                target_job or row.target_job,
                row.status_kind,
                effect_text,
                row.skill_name,
                row.job or row.source_name,
                (
                    f"{uptime / target_output_duration * 100:.1f}%"
                    if target_output_duration
                    else "0.0%"
                ),
            ]
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column < 6
                    else Qt.AlignCenter,
                )
                item.setToolTip(str(value))
                self.boss_stats_buffs_table.setItem(row_index, column, item)
        self.boss_stats_buffs_table.setUpdatesEnabled(True)


    def _timeline_events_for_snapshot(
        self, snapshot: CombatSnapshot
    ) -> tuple[tuple[CombatEvent | SkillCastEvent, ...], int]:
        platform = event_platform_for(snapshot)
        source_events = (platform.skill_cast_events, platform.combat_events)
        if (
            self._timeline_snapshot_event_sources is not None
            and source_events[0] is self._timeline_snapshot_event_sources[0]
            and source_events[1] is self._timeline_snapshot_event_sources[1]
        ):
            baseline_events = self._timeline_events
            baseline_fallback_count = self._timeline_damage_fallback_count
        else:
            baseline_events, baseline_fallback_count = (
                _timeline_events_with_damage_fallback(
                    platform.skill_cast_events,
                    platform.combat_events,
                    getattr(self, "store", None),
                )
            )
        self._timeline_snapshot_event_sources = source_events
        self._timeline_events = baseline_events
        self._timeline_damage_fallback_count = baseline_fallback_count
        return baseline_events, baseline_fallback_count


    @staticmethod
    def _timestamp_in_windows(
        timestamp_ms: int,
        windows: tuple[tuple[float, float], ...],
    ) -> bool:
        seconds = timestamp_ms / 1000.0
        return any(start <= seconds <= end for start, end in windows)


    def _fill_boss_action_timeline(
        self,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
    ) -> None:
        selected_ids = frozenset(
            target_id for group in groups for target_id in group.target_ids
        )
        selected_events = tuple(
            event
            for event in event_platform_for(snapshot).boss_action_events
            if event.source_id in selected_ids
        )
        windows: list[tuple[float, float]] = []
        targets = {
            target.target_id: target
            for target in event_platform_for(snapshot).damage_targets
            if target.target_id in selected_ids
        }
        for target_id in selected_ids:
            target = targets.get(target_id)
            target_events = tuple(
                event
                for event in selected_events
                if event.source_id == target_id
            )
            starts = [event.timestamp_ms / 1000.0 for event in target_events]
            ends = list(starts)
            if target is not None:
                starts.append(target.first_ms / 1000.0)
                ends.append(target.last_ms / 1000.0)
            if starts:
                start = min(starts)
                end = max(ends)
                windows.append((start, max(start + 0.05, end)))
        if not windows:
            windows.extend(
                (group.first_ms / 1000.0, group.last_ms / 1000.0)
                for group in groups
                if group.last_ms >= group.first_ms
            )
        compressed_rows, compressed_seconds = _compress_events_to_windows(
            selected_events,
            tuple(windows),
            preserve_clock=True,
        )
        compressed_events = tuple(
            event
            for event in compressed_rows
            if isinstance(event, BossActionEvent)
        )
        display_key: tuple[object, ...] = (
            self.collector.aggregator.revision,
            tuple(sorted(selected_ids)),
            tuple(
                (
                    event.timestamp_ms,
                    event.end_timestamp_ms,
                    event.source_id,
                    event.skill_id,
                    event.evidence_count,
                )
                for event in compressed_events
            ),
        )
        if display_key == self._boss_stats_action_display_key:
            return
        self._boss_stats_action_display_key = display_key
        self._boss_stats_action_events = compressed_events
        self._boss_stats_action_table_signature = None
        self._boss_stats_action_timeline_events = tuple(
            SkillCastEvent(
                timestamp_ms=event.timestamp_ms,
                source_id=event.source_id,
                source_name=event.source_name,
                job=event.job,
                skill_id=event.skill_id,
                skill_name=event.skill_name,
                packet_kind=event.packet_kind,
            )
            for event in compressed_events
        )
        player_details = {
            event.source_id: (event.source_name, "Boss")
            for event in compressed_events
        }
        self.boss_stats_action_timeline.set_player_details(player_details)
        self.boss_stats_action_timeline.set_target_details({})
        selected_action = str(
            self.boss_stats_action_combo.currentData() or ""
        )
        actions = {
            event.skill_id: event.skill_name for event in compressed_events
        }
        self.boss_stats_action_combo.blockSignals(True)
        self.boss_stats_action_combo.clear()
        self.boss_stats_action_combo.addItem("全部动作", "")
        for skill_id, name in sorted(actions.items(), key=lambda row: row[1]):
            self.boss_stats_action_combo.addItem(name, skill_id)
        selected_index = self.boss_stats_action_combo.findData(selected_action)
        self.boss_stats_action_combo.setCurrentIndex(
            selected_index if selected_index >= 0 else 0
        )
        self.boss_stats_action_combo.blockSignals(False)
        self.boss_stats_action_timeline.set_filters(
            "", str(self.boss_stats_action_combo.currentData() or "")
        )
        self.boss_stats_action_timeline.set_events(
            self._boss_stats_action_timeline_events,
            max(event_platform_for(snapshot).elapsed_seconds, compressed_seconds),
            origin_ms=0,
        )
        if compressed_events and not self._capture_active:
            first_action_seconds = compressed_events[0].timestamp_ms / 1000.0
            self.boss_stats_action_timeline._follow_latest = False
            self.boss_stats_action_timeline.set_view_start(
                max(0.0, first_action_seconds - 2.0)
            )
        self._update_boss_stats_action_summary()
        self._fill_boss_stats_action_table()


    def _boss_stats_action_filter_changed(
        self, _index: int | None = None
    ) -> None:
        self.boss_stats_action_timeline.set_filters(
            "", str(self.boss_stats_action_combo.currentData() or "")
        )
        self._boss_stats_action_table_signature = None
        self._update_boss_stats_action_summary()
        self._fill_boss_stats_action_table()


    def _boss_stats_action_zoom_changed(self, value: int) -> None:
        self.boss_stats_action_zoom_label.setText(f"{value} px/s")
        self.boss_stats_action_timeline.set_pixels_per_second(value)


    def _boss_stats_action_view_changed(
        self,
        start_seconds: float,
        maximum_start: float,
        window_seconds: float,
    ) -> None:
        self.boss_stats_action_scrollbar.blockSignals(True)
        self.boss_stats_action_scrollbar.setRange(
            0, max(0, round(maximum_start * 10))
        )
        self.boss_stats_action_scrollbar.setPageStep(
            max(1, round(window_seconds * 10))
        )
        self.boss_stats_action_scrollbar.setValue(round(start_seconds * 10))
        self.boss_stats_action_scrollbar.blockSignals(False)
        self.boss_stats_action_start.setText(f"{start_seconds:.1f}s")
        self.boss_stats_action_end.setText(
            f"总计 {self.boss_stats_action_timeline._extent_seconds:.1f}s"
        )
        self._boss_stats_action_table_timer.start()


    def _update_boss_stats_action_summary(self) -> None:
        events = self.boss_stats_action_timeline.filtered_events()
        boss_count = len({event.source_id for event in events})
        action_count = len({_timeline_skill_key(event) for event in events})
        selected_action = str(
            self.boss_stats_action_combo.currentData() or ""
        )
        mapped_count = sum(
            event.mapped
            for event in self._boss_stats_action_events
            if not selected_action or event.skill_id == selected_action
        )
        self.boss_stats_action_summary.setText(
            f"{boss_count} 个 Boss · {action_count} 种动作 · "
            f"{len(events)} 条记录 · 已命名 {mapped_count} 条"
        )


    def _fill_boss_stats_action_table(self) -> None:
        visible = tuple(self.boss_stats_action_timeline.visible_events())[-1500:]
        metadata: dict[
            tuple[int, str, str], list[BossActionEvent]
        ] = defaultdict(list)
        for event in self._boss_stats_action_events:
            metadata[(event.timestamp_ms, event.source_id, event.skill_id)].append(
                event
            )
        rows: list[BossActionEvent] = []
        for event in visible:
            candidates = metadata.get(
                (event.timestamp_ms, event.source_id, event.skill_id), []
            )
            if candidates:
                rows.append(candidates.pop(0))
        signature: tuple[object, ...] = (
            str(self.boss_stats_action_combo.currentData() or ""),
            tuple(id(event) for event in rows),
        )
        if signature == self._boss_stats_action_table_signature:
            return
        self._boss_stats_action_table_signature = signature
        self.boss_stats_action_table.setUpdatesEnabled(False)
        self.boss_stats_action_table.setRowCount(len(rows))
        for row_index, event in enumerate(rows):
            evidence = (
                f"XOR相位 {event.xor_phase}，记录偏移 {event.record_offset}"
            )
            values = [
                f"{event.timestamp_ms / 1000.0:.3f}s",
                event.source_name,
                event.skill_name,
                str(event.action_code) if event.action_code > 0 else "-",
                str(event.variant_code),
                f"0x{event.action_kind:02X}",
                "已映射" if event.mapped else "待命名",
                f"{event.packet_signature} / {event.packet_length}B",
                evidence,
            ]
            evidence_labels = {
                "protocol_action": "\u56fa\u5b9a\u52a8\u4f5c\u5305",
                "structured_action": "\u53d8\u957f\u52a8\u4f5c\u5305",
                "status": "\u72b6\u6001",
                "hit": "\u547d\u4e2d",
            }
            evidence_parts = [
                evidence_labels.get(kind, kind)
                for kind in event.evidence_types
            ]
            if event.hit_count:
                evidence_parts.append(f"Hit {event.hit_count}")
            if event.status_count:
                evidence_parts.append(f"\u72b6\u6001 {event.status_count}")
            if event.skill_index:
                evidence_parts.append(f"skill_index {event.skill_index}")
            if event.end_timestamp_ms > event.timestamp_ms:
                evidence_parts.append(
                    f"\u6301\u7eed {(event.end_timestamp_ms - event.timestamp_ms) / 1000.0:.3f}s"
                )
            evidence_parts.append(
                f"XOR {event.xor_phase} / offset {event.record_offset}"
            )
            values[8] = "\uff0c".join(evidence_parts)
            color = TeamSkillTimeline._skill_color(event.skill_name)
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (1, 2, 7, 8)
                    else Qt.AlignCenter,
                )
                item.setToolTip(str(value))
                if column == 2:
                    item.setBackground(color.lighter(138))
                self.boss_stats_action_table.setItem(row_index, column, item)
        self.boss_stats_action_table.setUpdatesEnabled(True)


    def _fill_boss_statistics_timeline(
        self,
        snapshot: CombatSnapshot,
        target_ids: frozenset[str],
        windows: tuple[tuple[float, float], ...],
        elapsed_seconds: float,
    ) -> None:
        cache_key: tuple[object, ...] = (
            self.collector.aggregator.revision,
            tuple(sorted(target_ids)),
            tuple(
                (round(start, 3), round(end, 3))
                for start, end in windows
            ),
        )
        if cache_key != self._boss_stats_timeline_cache_key:
            # Damage fallback matching is the expensive part of a team axis.
            # Restrict it to the chosen Boss phases plus a small boundary
            # context instead of first resolving the entire replay.
            compressed_events, compressed_seconds = (
                _boss_timeline_events_for_windows(
                event_platform_for(snapshot).skill_cast_events,
                    event_platform_for(snapshot).combat_events,
                    target_ids,
                    windows,
                    self.store,
                )
            )
            self._boss_stats_timeline_cache_key = cache_key
            self._boss_stats_timeline_cache_value = (
                compressed_events,
                compressed_seconds,
            )
        else:
            compressed_events, compressed_seconds = (
                self._boss_stats_timeline_cache_value
            )

        if cache_key == self._boss_stats_timeline_display_key:
            return
        self._boss_stats_timeline_display_key = cache_key
        self._boss_stats_timeline_events = compressed_events
        self._boss_stats_timeline_table_signature = None
        player_details = self._player_display_details(snapshot)
        self._boss_stats_player_details = player_details
        target_details = self._boss_target_details(
            snapshot, compressed_events, player_details
        )
        self._boss_stats_target_details = target_details
        self.boss_stats_team_timeline.set_player_details(player_details)
        self.boss_stats_team_timeline.set_target_details(target_details)
        self._sync_boss_stats_timeline_filters(compressed_events, player_details)
        self.boss_stats_team_timeline.set_filters(
            str(self.boss_stats_timeline_player_combo.currentData() or ""),
            self.boss_stats_timeline_skill_combo.selected_values(),
        )
        self.boss_stats_team_timeline.set_events(
            compressed_events,
            max(
                event_platform_for(snapshot).elapsed_seconds,
                elapsed_seconds,
                compressed_seconds,
            ),
            origin_ms=0,
        )
        self._update_boss_stats_timeline_summary()
        self._fill_boss_stats_timeline_table()


    def _sync_boss_stats_timeline_filters(
        self,
        events: tuple[CombatEvent | SkillCastEvent, ...],
        player_details: dict[str, tuple[str, str]],
    ) -> None:
        selected_player = str(
            self.boss_stats_timeline_player_combo.currentData() or ""
        )
        player_ids = []
        for event in events:
            if event.source_id not in player_ids:
                player_ids.append(event.source_id)
        self.boss_stats_timeline_player_combo.blockSignals(True)
        self.boss_stats_timeline_player_combo.clear()
        self.boss_stats_timeline_player_combo.addItem("全部玩家", "")
        for source_id in player_ids:
            name, job = player_details.get(source_id, (source_id, ""))
            self.boss_stats_timeline_player_combo.addItem(
                f"{name} · {job}" if job else name,
                source_id,
            )
        index = self.boss_stats_timeline_player_combo.findData(selected_player)
        self.boss_stats_timeline_player_combo.setCurrentIndex(
            index if index >= 0 else 0
        )
        self.boss_stats_timeline_player_combo.blockSignals(False)
        self._rebuild_boss_stats_timeline_skill_combo()


    def _rebuild_boss_stats_timeline_skill_combo(self) -> None:
        selected_player = str(
            self.boss_stats_timeline_player_combo.currentData() or ""
        )
        skills: dict[str, str] = {}
        for event in self._boss_stats_timeline_events:
            if selected_player and event.source_id != selected_player:
                continue
            key = _timeline_skill_key(event)
            skills.setdefault(key, event.skill_name or key)
        self.boss_stats_timeline_skill_combo.set_options(
            sorted(skills.items(), key=lambda item: item[1])
        )


    def _boss_stats_timeline_filter_changed(
        self, _index: int | None = None
    ) -> None:
        if self.sender() is self.boss_stats_timeline_player_combo:
            self._rebuild_boss_stats_timeline_skill_combo()
        self.boss_stats_team_timeline.set_filters(
            str(self.boss_stats_timeline_player_combo.currentData() or ""),
            self.boss_stats_timeline_skill_combo.selected_values(),
        )
        self._boss_stats_timeline_table_signature = None
        self._update_boss_stats_timeline_summary()
        self._fill_boss_stats_timeline_table()


    def _boss_stats_timeline_zoom_changed(self, value: int) -> None:
        self.boss_stats_timeline_zoom_label.setText(f"{value} px/s")
        self.boss_stats_team_timeline.set_pixels_per_second(value)


    def _boss_stats_timeline_view_changed(
        self,
        start_seconds: float,
        maximum_start: float,
        window_seconds: float,
    ) -> None:
        self.boss_stats_timeline_scrollbar.blockSignals(True)
        self.boss_stats_timeline_scrollbar.setRange(
            0, max(0, round(maximum_start * 10))
        )
        self.boss_stats_timeline_scrollbar.setPageStep(
            max(1, round(window_seconds * 10))
        )
        self.boss_stats_timeline_scrollbar.setValue(round(start_seconds * 10))
        self.boss_stats_timeline_scrollbar.blockSignals(False)
        self.boss_stats_timeline_start.setText(f"{start_seconds:.1f}s")
        self.boss_stats_timeline_end.setText(
            f"总计 {self.boss_stats_team_timeline._extent_seconds:.1f}s"
        )
        # Panning can emit dozens of signals per second. Keep the canvas
        # immediate, but rebuild the QTableWidget once the drag settles.
        self._boss_stats_timeline_table_timer.start()


    def _update_boss_stats_timeline_summary(self) -> None:
        events = self.boss_stats_team_timeline.filtered_events()
        players = len({event.source_id for event in events})
        skills = len({_timeline_skill_key(event) for event in events})
        self.boss_stats_timeline_summary.setText(
            f"{players} 名玩家 · {skills} 个技能 · {len(events)} 条记录"
        )


    def _fill_boss_stats_timeline_table(self) -> None:
        events = tuple(self.boss_stats_team_timeline.visible_events())[-1500:]
        origin_ms = self.boss_stats_team_timeline._origin_ms
        signature: tuple[object, ...] = (
            origin_ms,
            str(self.boss_stats_timeline_player_combo.currentData() or ""),
            tuple(sorted(self.boss_stats_timeline_skill_combo.selected_values())),
            tuple(id(event) for event in events),
        )
        if signature == self._boss_stats_timeline_table_signature:
            return
        self._boss_stats_timeline_table_signature = signature
        self.boss_stats_timeline_table.setUpdatesEnabled(False)
        self.boss_stats_timeline_table.setRowCount(len(events))
        for row_index, event in enumerate(events):
            seconds = max(0.0, (event.timestamp_ms - origin_ms) / 1000.0)
            if isinstance(event, SkillCastEvent):
                target_text = "-"
                amount_text = "施法"
                critical_text = "-"
                source_text = event.packet_kind
            else:
                target_text = self._boss_stats_target_details.get(
                    event.target_id,
                    event.target_name or "其他目标",
                )
                amount_text = _compact_number(event.amount)
                critical_text = (
                    "是" if event.critical else "否"
                ) if is_critical_rate_sample(event) else "-"
                source_text = event.attribution_method
            name, job = self._boss_stats_player_details.get(
                event.source_id, (event.source_name, event.job)
            )
            values = [
                f"{seconds:.3f}s",
                name,
                job,
                event.skill_name,
                target_text,
                amount_text,
                critical_text,
                source_text,
            ]
            color = TeamSkillTimeline._skill_color(event.skill_name)
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (1, 2, 3, 4, 7)
                    else Qt.AlignCenter,
                )
                if column == 3:
                    item.setBackground(color.lighter(138))
                self.boss_stats_timeline_table.setItem(row_index, column, item)
        self.boss_stats_timeline_table.setUpdatesEnabled(True)
