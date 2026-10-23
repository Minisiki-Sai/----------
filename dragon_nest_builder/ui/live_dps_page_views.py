from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *
from ..live_dps.critical_stats import gameplay_hit_count

class LiveDpsViewsMixin:
    def _fill_timeline(
        self,
        snapshot: CombatSnapshot,
        elapsed_seconds: float,
    ) -> None:
        platform = event_platform_for(snapshot)
        source_events = (platform.skill_cast_events, platform.combat_events)
        self._advance_view_progress(
            "timeline", 48, "正在匹配技能施放与延迟命中记录..."
        )
        timeline_events, fallback_count = self._timeline_events_for_snapshot(
            snapshot
        )
        timeline_groups = _boss_statistics_groups(snapshot)
        phase_windows = tuple(
            window
            for group in timeline_groups
            for window in group.phase_windows
        )
        timeline_target_ids = frozenset(
            target_id
            for group in timeline_groups
            for target_id in group.target_ids
        )
        output_windows = self.score_engine._output_windows(
            snapshot, timeline_target_ids or None
        )
        timeline_start, timeline_end = _timeline_display_bounds(
            snapshot, output_windows or phase_windows
        )
        timeline_origin_ms = max(0, round(timeline_start * 1000.0))
        timeline_events = tuple(
            event
            for event in timeline_events
            if event.timestamp_ms >= timeline_origin_ms
        )
        timeline_elapsed = max(0.0, timeline_end - timeline_start)
        self._advance_view_progress(
            "timeline", 70, "技能归因完成，正在生成玩家与技能筛选..."
        )
        self._timeline_using_casts = bool(platform.skill_cast_events)
        self._timeline_damage_fallback_count = fallback_count
        self._timeline_events = timeline_events
        player_details = self._player_display_details(snapshot)
        self._timeline_player_details = player_details
        self.team_skill_timeline.set_player_details(player_details)
        self._timeline_target_details = self._boss_target_details(
            snapshot, timeline_events, player_details
        )
        self.team_skill_timeline.set_target_details(
            self._timeline_target_details
        )
        player_signature = tuple(
            (source_id, name, job)
            for source_id, (name, job) in player_details.items()
        )
        skill_signature = tuple(
            sorted(
                {
                    (event.source_id, _timeline_skill_key(event), event.skill_name)
                    for event in timeline_events
                }
            )
        )
        player_signature_changed = (
            player_signature != self._timeline_player_signature
        )
        if player_signature_changed:
            selected_player = str(self.timeline_player_combo.currentData() or "")
            self.timeline_player_combo.blockSignals(True)
            try:
                self.timeline_player_combo.clear()
                self.timeline_player_combo.addItem("全部玩家", "")
                selected_index = 0
                for index, (source_id, name, job) in enumerate(
                    player_signature, start=1
                ):
                    label = f"{name} · {job}" if job else name
                    self.timeline_player_combo.addItem(label, source_id)
                    if source_id == selected_player:
                        selected_index = index
                self.timeline_player_combo.setCurrentIndex(selected_index)
            finally:
                self.timeline_player_combo.blockSignals(False)
            self._timeline_player_signature = player_signature
        selected_player = str(self.timeline_player_combo.currentData() or "")
        selection_has_skills = any(
            not selected_player or event.source_id == selected_player
            for event in timeline_events
        )
        skill_combo_incomplete = (
            self.timeline_skill_combo.count() <= 1 and selection_has_skills
        )
        if (
            skill_signature != self._timeline_skill_signature
            or player_signature_changed
            or skill_combo_incomplete
        ):
            self._rebuild_timeline_skill_combo()
            self._timeline_skill_signature = skill_signature
        self.team_skill_timeline.set_filters(
            str(self.timeline_player_combo.currentData() or ""),
            self.timeline_skill_combo.selected_values(),
        )
        # Every replay axis shares the decoder's capture-relative zero.
        self.team_skill_timeline.set_events(
            timeline_events,
            timeline_elapsed,
            origin_ms=timeline_origin_ms,
        )
        self._advance_view_progress(
            "timeline", 94, "正在更新可视时间轴与当前窗口事件表..."
        )


    def _timeline_player_changed(self) -> None:
        self._rebuild_timeline_skill_combo()
        self._timeline_filter_changed()


    def _rebuild_timeline_skill_combo(self) -> None:
        selected_player = str(self.timeline_player_combo.currentData() or "")
        skills: dict[str, str] = {}
        for event in self._timeline_events:
            if selected_player and event.source_id != selected_player:
                continue
            key = _timeline_skill_key(event)
            skills.setdefault(key, _timeline_skill_display_name(event) or key)
        self.timeline_skill_combo.set_options(
            sorted(skills.items(), key=lambda item: item[1])
        )


    def _timeline_filter_changed(self) -> None:
        player_id = str(self.timeline_player_combo.currentData() or "")
        self.team_skill_timeline.set_filters(
            player_id, self.timeline_skill_combo.selected_values()
        )
        self._update_timeline_summary()
        self._fill_timeline_event_table()


    def _timeline_view_changed(
        self, start_seconds: float, maximum_start: float, window_seconds: float
    ) -> None:
        self.timeline_scrollbar.blockSignals(True)
        self.timeline_scrollbar.setRange(0, max(0, round(maximum_start * 10)))
        self.timeline_scrollbar.setPageStep(max(1, round(window_seconds * 10)))
        self.timeline_scrollbar.setValue(round(start_seconds * 10))
        self.timeline_scrollbar.blockSignals(False)
        extent = self.team_skill_timeline._extent_seconds
        visible_end = min(extent, start_seconds + window_seconds)
        self.timeline_range_start.setText(f"{start_seconds:.1f}s")
        self.timeline_range_end.setText(f"总计 {extent:.1f}s")
        self.timeline_scrollbar.setAccessibleName(
            f"全战斗时间轴，当前显示 {start_seconds:.1f} 到 {visible_end:.1f} 秒，"
            f"总时长 {extent:.1f} 秒"
        )
        self._update_timeline_summary()
        self._fill_timeline_event_table()


    def _timeline_scroll_changed(self, value: int) -> None:
        self.team_skill_timeline.set_view_start(value / 10.0)


    def _update_timeline_summary(self) -> None:
        events = self.team_skill_timeline.filtered_events()
        start = self.team_skill_timeline._view_start
        end = start + self.team_skill_timeline._window_seconds
        player_count = len({event.source_id for event in events})
        skill_count = len({_timeline_skill_key(event) for event in events})
        if self._timeline_using_casts and self._timeline_damage_fallback_count:
            record_label = "施法+命中补全"
        elif self._timeline_using_casts:
            record_label = "施法"
        else:
            record_label = "命中"
        self.timeline_summary.setText(
            f"{player_count} 名玩家 · {skill_count} 个技能 · {len(events)} 条"
            f"{record_label}记录"
            f"  |  当前 {start:.1f}-{end:.1f}s"
        )


    def _fill_timeline_event_table(self) -> None:
        events = list(self.team_skill_timeline.visible_events())
        truncated = len(events) > 1500
        if truncated:
            events = events[-1500:]
        origin_ms = self.team_skill_timeline._origin_ms
        self.timeline_events_table.setUpdatesEnabled(False)
        self.timeline_events_table.setRowCount(len(events))
        for row_index, event in enumerate(events):
            seconds = max(0.0, (event.timestamp_ms - origin_ms) / 1000.0)
            if isinstance(event, SkillCastEvent):
                amount_text = "施法"
                critical_text = "-"
                attribution_text = event.packet_kind
                target_text = "-"
            else:
                amount_text = (
                    ("治疗 " if event.healing else "")
                    + _compact_number(event.amount)
                )
                critical_text = (
                    "是" if event.critical else "否"
                ) if is_critical_rate_sample(event) else "-"
                attribution_text = event.attribution_method
                target_text = self._timeline_target_details.get(
                    event.target_id,
                    (
                        f"其他目标（{event.target_id}）"
                        if event.target_id
                        else "未提供"
                    ),
                )
            values = [
                f"{seconds:.3f}s",
                self._timeline_player_details.get(
                    event.source_id, (event.source_name, event.job)
                )[0],
                self._timeline_player_details.get(
                    event.source_id, (event.source_name, event.job)
                )[1],
                _timeline_skill_display_name(event),
                target_text,
                amount_text,
                critical_text,
                attribution_text,
            ]
            color = TeamSkillTimeline._skill_color(_timeline_skill_key(event))
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (1, 2, 3, 4, 7)
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                if column == 3:
                    item.setBackground(color.lighter(138))
                    item.setForeground(QColor("#0f172a"))
                self.timeline_events_table.setItem(row_index, column, item)
        self.timeline_events_table.setUpdatesEnabled(True)
        if truncated:
            self.timeline_events_table.setToolTip("当前窗口记录较多，表格显示最近 1500 条；时间轴仍保留全部记录。")
        else:
            self.timeline_events_table.setToolTip("")


    def _boss_effect_curve_selection_changed(self, _state: int | None = None) -> None:
        self.boss_effect_curve.set_visible_keys(
            {
                key
                for key, check in self._boss_curve_checks.items()
                if check.isChecked()
            }
        )


    @staticmethod
    def _set_curve_filter_panel_visible(
        panel: QWidget, button: QToolButton, visible: bool
    ) -> None:
        panel.setVisible(visible)
        button.setArrowType(Qt.RightArrow if visible else Qt.LeftArrow)
        button.setToolTip("收起筛选" if visible else "展开筛选")


    def _boss_stats_effect_curve_selection_changed(
        self, _state: int | None = None
    ) -> None:
        self.boss_stats_effect_curve.set_visible_keys(
            {
                key
                for key, check in self._boss_stats_curve_checks.items()
                if check.isChecked()
            }
        )


    def _boss_stats_buff_filter_changed(
        self, _index: int | None = None
    ) -> None:
        self._rendered_view_revisions.pop((1, 3), None)
        self._begin_view_progress(1, "正在应用实时增益筛选...")
        self._schedule_ui_refresh()


    def _boss_detail_tab_changed(self, index: int) -> None:
        if index != 1 or self._latest_snapshot is None:
            return
        self._set_view_progress(
            "statistics", 8, "正在生成当前时间窗口的 Boss 状态明细..."
        )
        self._schedule_boss_detail_refresh()


    def _boss_effect_overview_target_changed(
        self, _index: int | None = None
    ) -> None:
        self._rendered_view_revisions.pop((0, 1), None)
        if self.live_dps_tabs.currentIndex() == 0:
            self._begin_view_progress(0, "正在汇总所选 Boss 的状态覆盖率...")
        self._schedule_ui_refresh()


    def _sync_boss_effect_overview_target_control(
        self,
        groups: tuple[_BossStatisticsGroup, ...],
        display_names: dict[str, str],
    ) -> tuple[_BossStatisticsGroup, ...]:
        signature = tuple(
            (
                group.identity,
                group.name,
                group.first_ms,
                tuple(sorted(group.target_ids)),
            )
            for group in groups
        )
        current = str(
            self.boss_effect_overview_target_combo.currentData() or "__all__"
        )
        if signature != self._boss_effect_overview_target_signature:
            group_map: dict[str, _BossStatisticsGroup] = {}
            options: list[tuple[str, str]] = [("全部Boss", "__all__")]
            for index, group in enumerate(groups, start=1):
                key = f"boss:{group.identity[0]}:{group.identity[1]}"
                if key in group_map:
                    key = f"{key}:{index}"
                group_map[key] = group
                label = next(
                    (
                        display_names[target_id]
                        for target_id in sorted(group.target_ids)
                        if target_id in display_names
                    ),
                    group.name,
                )
                options.append((label, key))

            self._boss_effect_overview_groups_by_key = group_map
            if current != "__all__" and current not in group_map:
                current = "__all__"
            combo = self.boss_effect_overview_target_combo
            combo.blockSignals(True)
            combo.clear()
            for label, key in options:
                combo.addItem(label, key)
            selected_index = combo.findData(current)
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.setEnabled(bool(groups))
            combo.blockSignals(False)
            self._boss_effect_overview_target_signature = signature

        selected_key = str(
            self.boss_effect_overview_target_combo.currentData() or "__all__"
        )
        if selected_key == "__all__":
            return groups
        selected = self._boss_effect_overview_groups_by_key.get(selected_key)
        return (selected,) if selected is not None else groups


    def _player_buff_detail_tab_changed(self, index: int) -> None:
        if index != 1 or self._latest_snapshot is None:
            return
        self._set_view_progress(
            "statistics", 8, "正在生成当前时点的全队增益明细..."
        )
        self._schedule_player_buff_detail_refresh()


    def _schedule_boss_detail_refresh(self) -> None:
        self._detail_refresh_generation += 1
        generation = self._detail_refresh_generation

        def refresh() -> None:
            if (
                generation != self._detail_refresh_generation
                or self._latest_snapshot is None
                or self.boss_detail_tabs.currentIndex() != 1
            ):
                return
            player_details = self._player_display_details(self._latest_snapshot)
            target_details = self._boss_target_details(
                self._latest_snapshot,
                event_platform_for(self._latest_snapshot).combat_events,
                player_details,
            )
            self._fill_boss_effect_details(
                self._latest_snapshot, player_details, target_details
            )
            self._finish_view_progress("statistics")

        QTimer.singleShot(0, refresh)


    def _schedule_player_buff_detail_refresh(self) -> None:
        self._detail_refresh_generation += 1
        generation = self._detail_refresh_generation

        def refresh() -> None:
            if (
                generation != self._detail_refresh_generation
                or self._latest_snapshot is None
                or self.player_buff_tabs.currentIndex() != 1
            ):
                return
            self._fill_player_buff_details(
                self._latest_snapshot,
                self._player_display_details(self._latest_snapshot),
                str(self.player_buff_target_combo.currentData() or ""),
                str(self.player_buff_type_combo.currentData() or ""),
                str(self.player_buff_target_job_combo.currentData() or ""),
                str(self.player_buff_source_job_combo.currentData() or ""),
            )
            self._finish_view_progress("statistics")

        QTimer.singleShot(0, refresh)


    def _player_buff_filter_changed(self, _index: int | None = None) -> None:
        self._rendered_view_revisions.pop((0, 2), None)
        if self.live_dps_tabs.currentIndex() == 0:
            self._begin_view_progress(0, "正在应用全队增益筛选...")
        self._schedule_ui_refresh()


    @staticmethod
    def _player_buff_filter_matches(
        row: BossEffectSnapshot | PlayerBuffSeriesSnapshot | StatusEventSnapshot,
        target_filter: str,
        type_filter: str,
        target_job_filter: str = "",
        source_job_filter: str = "",
    ) -> bool:
        if target_filter and getattr(row, "target_id", "") != target_filter:
            return False
        if target_job_filter and getattr(row, "target_job", "") != target_job_filter:
            return False
        if source_job_filter and getattr(row, "job", "") != source_job_filter:
            return False
        if type_filter.startswith("kind:"):
            return getattr(row, "status_kind", "") == type_filter.removeprefix(
                "kind:"
            )
        if type_filter.startswith("key:"):
            row_key = getattr(row, "key", "")
            return row_key == type_filter.removeprefix("key:")
        if type_filter.startswith("skill:"):
            return _effect_skill_key(
                getattr(row, "skill_name", "") or getattr(row, "skill_id", "")
            ) == type_filter.removeprefix("skill:")
        return True


    def _sync_player_buff_filter_controls(
        self,
        visible_series: tuple[PlayerBuffSeriesSnapshot, ...],
        player_details: dict[str, tuple[str, str]],
    ) -> None:
        current_target = str(self.player_buff_target_combo.currentData() or "")
        current_target_job = str(
            self.player_buff_target_job_combo.currentData() or ""
        )
        current_source_job = str(
            self.player_buff_source_job_combo.currentData() or ""
        )
        targets: dict[str, tuple[str, str]] = dict(player_details)
        for row in visible_series:
            targets.setdefault(
                row.target_id,
                (row.target_name or row.target_id, row.target_job),
            )
        target_items = [("全队", "")]
        target_items.extend(
            (
                f"{name}（{job}）" if job else name,
                target_id,
            )
            for target_id, (name, job) in sorted(
                targets.items(), key=lambda item: (item[1][0], item[1][1])
            )
        )

        current_type = str(self.player_buff_type_combo.currentData() or "")
        kinds = sorted(
            {row.status_kind for row in visible_series if row.status_kind}
        )
        labels_by_key: dict[str, str] = {}
        labels_by_skill: dict[str, str] = {}
        for row in visible_series:
            if row.key:
                labels_by_key.setdefault(row.key, row.label or row.key)
            skill_key = _effect_skill_key(row.skill_name or row.skill_id)
            if skill_key:
                labels_by_skill.setdefault(
                    skill_key, row.skill_name or str(row.skill_id)
                )
        type_items = [("全部增益", "")]
        type_items.extend((f"类别｜{kind}", f"kind:{kind}") for kind in kinds)
        type_items.extend(
            (f"Buff｜{label}", f"skill:{key}")
            for key, label in sorted(
                labels_by_skill.items(), key=lambda item: (item[1], item[0])
            )
        )
        type_items.extend(
            (f"效果｜{label}", f"key:{key}")
            for key, label in sorted(
                labels_by_key.items(), key=lambda item: (item[1], item[0])
            )
        )
        target_jobs = sorted(
            {
                row.target_job
                for row in visible_series
                if row.target_job
            }
        )
        source_jobs = sorted(
            {row.job for row in visible_series if row.job}
        )
        target_job_items = [("全部目标职业", "")]
        target_job_items.extend((job, job) for job in target_jobs)
        source_job_items = [("全部来源职业", "")]
        source_job_items.extend((job, job) for job in source_jobs)
        options_signature = (
            tuple(target_items),
            tuple(target_job_items),
            tuple(source_job_items),
            tuple(type_items),
        )
        if options_signature == self._player_buff_filter_options_signature:
            return
        self._player_buff_filter_options_signature = options_signature

        for combo, items, selected in (
            (self.player_buff_target_combo, target_items, current_target),
            (
                self.player_buff_target_job_combo,
                target_job_items,
                current_target_job,
            ),
            (
                self.player_buff_source_job_combo,
                source_job_items,
                current_source_job,
            ),
            (self.player_buff_type_combo, type_items, current_type),
        ):
            combo.blockSignals(True)
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.blockSignals(False)


    def _sync_boss_stats_buff_filter_controls(
        self,
        rows: tuple[PlayerBuffSeriesSnapshot, ...],
        player_details: dict[str, tuple[str, str]],
    ) -> None:
        current_target = str(self.boss_stats_buff_target_combo.currentData() or "")
        current_target_job = str(
            self.boss_stats_buff_target_job_combo.currentData() or ""
        )
        current_source_job = str(
            self.boss_stats_buff_source_job_combo.currentData() or ""
        )
        current_type = str(self.boss_stats_buff_type_combo.currentData() or "")

        targets: dict[str, tuple[str, str]] = {}
        for row in rows:
            targets.setdefault(
                row.target_id,
                player_details.get(
                    row.target_id,
                    (row.target_name or row.target_id, row.target_job),
                ),
            )
        target_items = [("全队", "")]
        target_items.extend(
            (
                f"{name}（{job}）" if job else name,
                target_id,
            )
            for target_id, (name, job) in sorted(
                targets.items(), key=lambda item: (item[1][0], item[1][1])
            )
        )
        target_job_items = [("全部目标职业", "")]
        target_job_items.extend(
            (job, job) for job in sorted({row.target_job for row in rows if row.target_job})
        )
        source_job_items = [("全部来源职业", "")]
        source_job_items.extend(
            (job, job) for job in sorted({row.job for row in rows if row.job})
        )

        kinds = sorted({row.status_kind for row in rows if row.status_kind})
        labels_by_key: dict[str, str] = {}
        labels_by_skill: dict[str, str] = {}
        for row in rows:
            if row.key:
                labels_by_key.setdefault(row.key, row.label or row.key)
            skill_key = _effect_skill_key(row.skill_name or row.skill_id)
            if skill_key:
                labels_by_skill.setdefault(
                    skill_key, row.skill_name or str(row.skill_id)
                )
        type_items = [("全部增益", "")]
        type_items.extend((f"类别｜{kind}", f"kind:{kind}") for kind in kinds)
        type_items.extend(
            (f"Buff｜{label}", f"skill:{key}")
            for key, label in sorted(
                labels_by_skill.items(), key=lambda item: (item[1], item[0])
            )
        )
        type_items.extend(
            (f"效果｜{label}", f"key:{key}")
            for key, label in sorted(
                labels_by_key.items(), key=lambda item: (item[1], item[0])
            )
        )
        options_signature = (
            tuple(target_items),
            tuple(target_job_items),
            tuple(source_job_items),
            tuple(type_items),
        )
        if options_signature == self._boss_stats_buff_filter_options_signature:
            return
        self._boss_stats_buff_filter_options_signature = options_signature

        for combo, items, selected in (
            (self.boss_stats_buff_target_combo, target_items, current_target),
            (
                self.boss_stats_buff_target_job_combo,
                target_job_items,
                current_target_job,
            ),
            (
                self.boss_stats_buff_source_job_combo,
                source_job_items,
                current_source_job,
            ),
            (self.boss_stats_buff_type_combo, type_items, current_type),
        ):
            combo.blockSignals(True)
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.blockSignals(False)


    @staticmethod
    def _status_value_text(value: float | None, parameters: str) -> str:
        if value is not None:
            return f"{value:g}%"
        return parameters or "-"


    def _player_buff_effect_text(
        self, effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot
    ) -> str:
        cache_key = (
            _effect_skill_key(effect.skill_name),
            effect.label,
            effect.value,
            getattr(effect, "parameters", ""),
        )
        cached = self._live_buff_effect_cache.get(cache_key)
        if cached is None:
            cached = _live_buff_display_text(
                effect,
                self._live_buff_rows_by_skill.get(cache_key[0], []),
            )
            self._live_buff_effect_cache[cache_key] = cached
        return cached


    def _player_buff_value_text(
        self, effect: BossEffectSnapshot | PlayerBuffSeriesSnapshot
    ) -> str:
        return _live_buff_validated_value_text(
            effect,
            self._live_buff_rows_by_skill.get(
                _effect_skill_key(effect.skill_name), []
            ),
        )


    def _boss_effect_output_windows(
        self,
        snapshot: CombatSnapshot,
        groups: tuple[_BossStatisticsGroup, ...],
    ) -> dict[str, tuple[tuple[float, float], ...]]:
        """Cache scoring-compatible output windows for each Boss entity."""

        revision = self.collector.aggregator.revision
        signature = tuple(
            (group.identity, tuple(sorted(group.target_ids)))
            for group in groups
        )
        if (
            revision == self._boss_effect_output_windows_revision
            and signature == self._boss_effect_output_windows_signature
        ):
            return self._boss_effect_output_windows_by_target

        windows_by_target: dict[
            str, tuple[tuple[float, float], ...]
        ] = {}
        for group in groups:
            windows = self.score_engine._output_windows(
                snapshot, group.target_ids
            )
            for target_id in group.target_ids:
                windows_by_target[target_id] = windows

        self._boss_effect_output_windows_revision = revision
        self._boss_effect_output_windows_signature = signature
        self._boss_effect_output_windows_by_target = windows_by_target
        return windows_by_target


    def _boss_freeze_summary_rows(
        self,
        groups: tuple[_BossStatisticsGroup, ...],
        current_seconds: float,
    ) -> tuple[BossEffectSnapshot, ...]:
        """Build cumulative one-layer and two-layer freeze coverage rows."""

        revision = self.collector.aggregator.revision
        signature = (
            round(max(0.0, float(current_seconds)), 3),
            tuple(
                (group.identity, tuple(sorted(group.target_ids)))
                for group in groups
            ),
        )
        if (
            revision == self._boss_freeze_rows_revision
            and signature == self._boss_freeze_rows_signature
        ):
            return self._boss_freeze_rows

        rows: list[BossEffectSnapshot] = []
        current_ms = round(max(0.0, float(current_seconds)) * 1000)
        for group in groups:
            target_id = next(iter(sorted(group.target_ids)), "")
            if not target_id:
                continue
            effect_view = self.collector.aggregator.boss_effect_view(
                group.target_ids, current_ms
            )
            freeze_points = dict(effect_view.series).get("冰冻层", ())
            for layer in (1, 2):
                intervals = _effect_series_threshold_intervals(
                    freeze_points,
                    float(layer),
                    current_seconds,
                )
                rows.append(
                    BossEffectSnapshot(
                        key=f"__freeze_layer_{layer}_coverage__",
                        label=f"{layer}层冰覆盖率",
                        source_id="",
                        source_name="全队",
                        job="全队",
                        skill_id="",
                        skill_name="全部叠冰技能",
                        value=None,
                        status_kind="叠冰汇总",
                        effect_code="freeze_layer_coverage",
                        parameters=(
                            "至少1层（含2层）"
                            if layer == 1
                            else "达到2层"
                        ),
                        data_source="服务器叠冰状态曲线",
                        start_seconds=(
                            intervals[0][0]
                            if intervals
                            else group.first_ms / 1000.0
                        ),
                        end_seconds=(
                            intervals[-1][1]
                            if intervals
                            else group.last_ms / 1000.0
                        ),
                        last_refresh_seconds=(
                            intervals[-1][0]
                            if intervals
                            else group.first_ms / 1000.0
                        ),
                        refresh_count=len(intervals),
                        active=any(
                            start <= current_seconds < end
                            for start, end in intervals
                        ),
                        uptime_seconds=sum(
                            max(0.0, end - start)
                            for start, end in intervals
                        ),
                        coverage=0.0,
                        target_id=target_id,
                        target_name=group.name,
                        target_job="Boss",
                        intervals=intervals,
                    )
                )

        self._boss_freeze_rows_revision = revision
        self._boss_freeze_rows_signature = signature
        self._boss_freeze_rows = tuple(rows)
        return self._boss_freeze_rows


    def _fill_boss_effects(
        self,
        snapshot: CombatSnapshot,
        current_seconds: float,
    ) -> None:
        platform = event_platform_for(snapshot)
        summary = dict(platform.boss_effect_summary)
        parts = [
            f"火 -{summary.get('减火抗%', 0):g}%",
            f"水 -{summary.get('减水抗%', 0):g}%",
            f"光 -{summary.get('减光抗%', 0):g}%",
            f"暗 -{summary.get('减暗抗%', 0):g}%",
            f"通用增伤 {summary.get('增伤%', 0):g}%",
            f"物理合计 {summary.get('物理增伤合计%', 0):g}%",
            f"魔法合计 {summary.get('魔法增伤合计%', 0):g}%",
            f"减防 {summary.get('减防御%', 0):g}%",
            f"减致抗 {summary.get('减致命抵抗%', 0):g}%",
            f"减攻击 {summary.get('减攻击%', 0):g}%",
            f"叠冰状态 {summary.get('冰冻层', 0):g}层",
        ]
        self.boss_effect_summary.setText("  |  ".join(parts))
        self.boss_effect_curve.set_series(
            platform.boss_effect_series, current_seconds
        )
        player_details = self._player_display_details(snapshot)
        target_details = self._boss_target_details(
            snapshot, event_platform_for(snapshot).combat_events, player_details
        )
        boss_groups = _boss_statistics_groups(snapshot)
        boss_target_display_names = _boss_group_target_display_names(boss_groups)
        selected_overview_groups = (
            self._sync_boss_effect_overview_target_control(
                boss_groups, boss_target_display_names
            )
        )
        output_windows_by_target = self._boss_effect_output_windows(
            snapshot, boss_groups
        )
        freeze_summary_rows = self._boss_freeze_summary_rows(
            boss_groups, current_seconds
        )
        self.boss_damage_targets_table.setRowCount(len(boss_groups))
        for row_index, group in enumerate(boss_groups):
            values = [
                group.name,
                _compact_number(group.damage),
                (
                    f"{group.damage / platform.total_damage * 100:.1f}%"
                    if platform.total_damage
                    else "0.0%"
                ),
                str(group.hits),
            ]
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column == 0
                    else Qt.AlignCenter
                )
                self.boss_damage_targets_table.setItem(
                    row_index, column, self._item(value, align)
                )
        overview_items = _boss_effect_overview_items(
            (*event_platform_for(snapshot).boss_effects, *freeze_summary_rows),
            selected_overview_groups,
            output_windows_by_target,
            str(
                self.boss_effect_overview_target_combo.currentText()
                or "全部Boss"
            ),
        )
        self.boss_effects_table.setUpdatesEnabled(False)
        self.boss_effects_table.setRowCount(len(overview_items))
        for row_index, overview_item in enumerate(overview_items):
            effect = overview_item.effect
            freeze_summary = effect.effect_code == "freeze_layer_coverage"
            value_text = self._status_value_text(effect.value, effect.parameters)
            values = [
                effect.target_name or "未识别Boss",
                effect.status_kind,
                effect.label,
                effect.skill_name,
                effect.job or "未识别职业",
                value_text,
                str(effect.refresh_count),
                effect.effective_state or ("有效" if effect.active else "已结束"),
                f"{overview_item.coverage * 100:.1f}%",
            ]
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (0, 1, 2, 3, 4, 5)
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                item.setToolTip(str(value))
                if column == 7 and effect.effective_reason:
                    item.setToolTip(effect.effective_reason)
                if column == 8:
                    item.setToolTip(
                        "仅统计统一效果结算后、该 Boss 可输出区间内的有效覆盖率；"
                        f"包内观察覆盖率为 {effect.coverage * 100:.1f}%"
                    )
                if freeze_summary:
                    item.setBackground(QColor("#dceff8"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif effect.effective_state in {"申请被拒绝", "同组受压制"}:
                    item.setForeground(QColor("#b26a2e"))
                elif effect.active:
                    item.setBackground(QColor("#e4f4ec"))
                else:
                    item.setForeground(QColor("#78909c"))
                self.boss_effects_table.setItem(row_index, column, item)
        self.boss_effects_table.setUpdatesEnabled(True)
        self._resize_boss_effect_overview_columns()

        if self.boss_detail_tabs.currentIndex() == 1:
            self._schedule_boss_detail_refresh()


    def _fill_boss_effect_details(
        self,
        snapshot: CombatSnapshot,
        player_details: dict[str, tuple[str, str]],
        target_details: dict[str, str],
    ) -> None:
        window_start = self.boss_effect_curve._view_start
        window_end = window_start + self.boss_effect_curve._window_seconds
        visible_events = [
            effect
            for effect in event_platform_for(snapshot).boss_effect_events
            if window_start <= effect.timestamp_seconds <= window_end
        ]
        self.boss_effect_details_table.setUpdatesEnabled(False)
        self.boss_effect_details_table.setSortingEnabled(False)
        self.boss_effect_details_table.setRowCount(len(visible_events))
        for row_index, effect in enumerate(visible_events):
            source_details = player_details.get(effect.source_id)
            source_display = (
                source_details[0]
                if source_details is not None
                else (effect.source_name or effect.job or "未识别角色")
            )
            values = [
                f"{effect.timestamp_seconds:.3f}s",
                target_details.get(
                    effect.target_id,
                    effect.target_name or effect.target_id or "未识别Boss",
                ),
                effect.effective_state or ("生效中" if effect.active else "已结束"),
                effect.status_kind,
                effect.label,
                effect.skill_name,
                source_display,
                self._status_value_text(effect.value, effect.parameters),
                f"{effect.duration_seconds:.1f}s",
                f"{effect.end_seconds:.3f}s",
            ]
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (1, 3, 4, 5, 6, 7)
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                if column == 2 and effect.effective_reason:
                    item.setToolTip(effect.effective_reason)
                if effect.active:
                    item.setBackground(QColor("#e4f4ec"))
                else:
                    item.setForeground(QColor("#78909c"))
                self.boss_effect_details_table.setItem(row_index, column, item)
        self.boss_effect_details_table.setUpdatesEnabled(True)
        self.boss_effect_details_table.setToolTip(
            "状态明细与上方曲线当前可视时间段同步。"
        )


    def _fill_player_buffs(
        self,
        snapshot: CombatSnapshot,
        current_seconds: float,
        player_details: dict[str, tuple[str, str]],
    ) -> None:
        visible_series = _merge_player_buff_series_rows(
            tuple(
                row
                for row in event_platform_for(snapshot).player_buff_series
                if _is_catalogued_player_buff(row, self._live_buff_rows_by_skill)
            ),
            self._live_buff_rows_by_skill,
        )
        self._sync_player_buff_filter_controls(visible_series, player_details)
        target_filter = str(self.player_buff_target_combo.currentData() or "")
        type_filter = str(self.player_buff_type_combo.currentData() or "")
        target_job_filter = str(
            self.player_buff_target_job_combo.currentData() or ""
        )
        source_job_filter = str(
            self.player_buff_source_job_combo.currentData() or ""
        )
        merged_buffs = _merge_player_buff_effect_rows(
            tuple(
                effect
                for effect in event_platform_for(snapshot).player_buffs
                if _is_catalogued_player_buff(
                    effect, self._live_buff_rows_by_skill
                )
            ),
            self._live_buff_rows_by_skill,
        )
        filtered_buffs = [
            effect
            for effect in merged_buffs
            if self._player_buff_filter_matches(
                effect,
                target_filter,
                type_filter,
                target_job_filter,
                source_job_filter,
            )
        ]
        filtered_buffs.sort(
            key=lambda effect: (
                effect.target_job
                or player_details.get(effect.target_id, ("", ""))[1],
                _player_buff_category_rank(
                    self._player_buff_effect_text(effect), effect.status_kind
                ),
                self._player_buff_effect_text(effect),
                effect.skill_name,
                effect.job or effect.source_name,
                effect.target_name or effect.target_id,
            )
        )
        points, curve_label, curve_unit = _player_buff_curve_points(
            visible_series,
            target_filter,
            type_filter,
            current_seconds,
            target_job_filter,
            source_job_filter,
        )
        self.player_buff_curve.set_buff_series(
            points, current_seconds, curve_label, curve_unit
        )
        self.player_buffs_table.setUpdatesEnabled(False)
        self.player_buffs_table.setRowCount(len(filtered_buffs))
        for row_index, effect in enumerate(filtered_buffs):
            target_details = player_details.get(effect.target_id)
            target_display = (
                target_details[0]
                if target_details is not None
                else (effect.target_name or effect.target_job or "未识别角色")
            )
            effect_text = self._player_buff_effect_text(effect)
            values = [
                target_display,
                effect.target_job or (target_details[1] if target_details else ""),
                effect.status_kind,
                effect_text,
                effect.skill_name,
                effect.job or effect.source_name,
                f"{effect.coverage * 100:.1f}%",
            ]
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (0, 1, 2, 3, 4, 5)
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                item.setToolTip(str(value))
                if effect.active:
                    item.setBackground(QColor("#e7f3ea"))
                else:
                    item.setForeground(QColor("#78909c"))
                self.player_buffs_table.setItem(row_index, column, item)
        self.player_buffs_table.setUpdatesEnabled(True)
        self._resize_player_buff_overview_columns()

        if self.player_buff_tabs.currentIndex() == 1:
            self._schedule_player_buff_detail_refresh()


    def _fill_player_buff_details(
        self,
        snapshot: CombatSnapshot,
        player_details: dict[str, tuple[str, str]],
        target_filter: str,
        type_filter: str,
        target_job_filter: str,
        source_job_filter: str,
    ) -> None:
        selected_seconds = (
            self._latest_effect_seconds
            if self._player_buff_selected_seconds is None
            else self._player_buff_selected_seconds
        )
        active_rows: list[
            tuple[PlayerBuffSeriesSnapshot, float, float]
        ] = []
        visible_series = _merge_player_buff_series_rows(
            tuple(
                row
                for row in event_platform_for(snapshot).player_buff_series
                if _is_catalogued_player_buff(row, self._live_buff_rows_by_skill)
            ),
            self._live_buff_rows_by_skill,
        )
        for effect in visible_series:
            if not self._player_buff_filter_matches(
                effect,
                target_filter,
                type_filter,
                target_job_filter,
                source_job_filter,
            ):
                continue
            matching = [
                (start, end)
                for start, end in effect.intervals
                if start <= selected_seconds < end
            ]
            if matching:
                start, end = max(matching, key=lambda interval: interval[0])
                active_rows.append((effect, start, end))
        active_rows.sort(
            key=lambda row: (
                _player_buff_category_rank(
                    self._player_buff_effect_text(row[0]), row[0].status_kind
                ),
                self._player_buff_effect_text(row[0]),
                row[0].job or row[0].source_name,
                row[0].target_job,
                row[0].target_name,
            )
        )
        self.player_buff_time_label.setText(
            f"明细时点：{selected_seconds:.3f}s"
            if self._player_buff_selected_seconds is not None
            else f"明细时点：最新（{selected_seconds:.3f}s）"
        )
        self.player_buff_details_table.setUpdatesEnabled(False)
        self.player_buff_details_table.setSortingEnabled(False)
        self.player_buff_details_table.setRowCount(len(active_rows))
        for row_index, (effect, start_seconds, end_seconds) in enumerate(active_rows):
            target_details = player_details.get(effect.target_id)
            source_details = player_details.get(effect.source_id)
            target_display = (
                target_details[0]
                if target_details is not None
                else (effect.target_name or effect.target_job or "未识别角色")
            )
            source_display = (
                source_details[0]
                if source_details is not None
                else (effect.source_name or effect.job or "未识别角色")
            )
            values = [
                f"{selected_seconds:.3f}s",
                target_display,
                effect.target_job or (target_details[1] if target_details else ""),
                effect.status_kind,
                self._player_buff_effect_text(effect),
                effect.skill_name,
                source_display,
                self._player_buff_value_text(effect),
                f"{start_seconds:.3f}-{end_seconds:.3f}s",
                f"{max(0.0, end_seconds - selected_seconds):.3f}s",
            ]
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (1, 2, 3, 4, 5, 6, 7)
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                item.setBackground(QColor("#e7f3ea"))
                self.player_buff_details_table.setItem(row_index, column, item)
        self.player_buff_details_table.setUpdatesEnabled(True)


    def _fill_players(self, snapshot: CombatSnapshot) -> None:
        selected = self._selected_source_id
        platform = event_platform_for(snapshot)
        if not selected and platform.players:
            selected = platform.players[0].source_id
            self._selected_source_id = selected
        player_details = self._player_display_details(snapshot)
        displayed_total_damage = sum(
            max(0, player.damage) for player in platform.players
        )
        self.players_table.blockSignals(True)
        self.players_table.setRowCount(len(platform.players))
        for row_index, player in enumerate(platform.players):
            display_name, display_job = player_details.get(
                player.source_id, (player.name, player.job)
            )
            values = [
                str(row_index + 1),
                display_name,
                display_job,
                _compact_number(player.damage),
                (
                    f"{player.damage / displayed_total_damage * 100:.1f}%"
                    if displayed_total_damage > 0
                    else "0.0%"
                ),
                _compact_number(player.dps),
                f"{player.critical_rate * 100:.1f}%",
                _compact_number(player.healing),
                _compact_number(player.incoming_damage),
                f"{player.active_seconds:.1f}s",
            ]
            for column, value in enumerate(values):
                align = Qt.AlignLeft | Qt.AlignVCenter if column in (1, 2) else Qt.AlignCenter
                item = self._item(value, align)
                item.setData(Qt.UserRole, player.source_id)
                if column == 6 and player.critical_samples is not None:
                    item.setToolTip(
                        "仅按具备真实命中证据的 "
                        f"{player.critical_samples} 次伤害判定；"
                        "额外伤害、状态伤害和周期伤害不参与暴击率。"
                    )
                self.players_table.setItem(row_index, column, item)
            if player.source_id == selected:
                self.players_table.selectRow(row_index)
        self.players_table.blockSignals(False)


    def _player_changed(self) -> None:
        rows = self.players_table.selectionModel().selectedRows()
        if rows:
            item = self.players_table.item(rows[0].row(), 0)
            self._selected_source_id = str(item.data(Qt.UserRole) or "") if item else ""
        capture_elapsed = (
            max(0.0, time.time() - self._capture_started_at)
            if self._capture_active and self._capture_started_at
            else None
        )
        snapshot = self.collector.snapshot(
            capture_elapsed,
            include_events=False,
            live_clock=self._capture_active,
        )
        curve_elapsed = max(
            event_platform_for(snapshot).elapsed_seconds,
            capture_elapsed or 0.0,
        )
        self._fill_damage_curve(snapshot, curve_elapsed)
        self._fill_skills(snapshot)


    def _fill_damage_curve(
        self, snapshot: CombatSnapshot, elapsed_seconds: float
    ) -> None:
        if event_platform_for(snapshot).combat_events:
            revision = self.collector.aggregator.revision
            if revision != self._boss_stats_groups_revision:
                self._boss_stats_groups = _boss_statistics_groups(snapshot)
                self._boss_stats_groups_revision = revision
            groups = self._boss_stats_groups
            self._damage_curve_boss_phases = tuple(
                (group.name, group.phase_windows) for group in groups
            )
        groups = self._boss_stats_groups
        if groups and (
            self._boss_stats_groups_revision
            == self.collector.aggregator.revision
        ):
            self._ensure_boss_hp_context(snapshot, groups)
            full_extent = max(
                elapsed_seconds,
                max(
                    (
                        end
                        for group in groups
                        for _start, end in group.phase_windows
                    ),
                    default=0.0,
                ),
            )
            full_windows = ((0.0, full_extent),) if full_extent > 0 else ()
            self.curve.set_boss_hp_series(
                _compress_boss_hp_tracks_to_windows(
                    event_platform_for(snapshot).boss_hp_tracks,
                    groups,
                    full_windows,
                    infer_from_damage=True,
                    completed_group_identities=self._completed_boss_group_keys,
                    fixed_boss_hp_references=self._fixed_boss_hp_references,
                )
            )
        else:
            self.curve.set_boss_hp_series(())
        self.curve.set_boss_phases(self._damage_curve_boss_phases)
        player = next(
            (
                row
                for row in event_platform_for(snapshot).players
                if row.source_id == self._selected_source_id
            ),
            None,
        )
        if player is None:
            self.curve_tabs.setTabText(0, "伤害曲线 · 全队")
            self.curve.set_named_series(
                (("全队", event_platform_for(snapshot).damage_series),),
                elapsed_seconds,
            )
            return
        display_name = self._player_display_details(snapshot).get(
            player.source_id, (player.name, player.job)
        )[0]
        self.curve_tabs.setTabText(0, f"伤害曲线 · {display_name}")
        self.curve.set_named_series(
            ((display_name, player.damage_series),), elapsed_seconds
        )


    def _fill_skills(self, snapshot: CombatSnapshot) -> None:
        player = next(
            (
                row
                for row in event_platform_for(snapshot).players
                if row.source_id == self._selected_source_id
            ),
            None,
        )
        display_name = (
            self._player_display_details(snapshot).get(
                player.source_id, (player.name, player.job)
            )[0]
            if player
            else ""
        )
        self.skill_title.setText(f"技能构成 · {display_name}" if player else "技能构成")
        skills = player.skills if player else ()
        self.skills_table.setRowCount(len(skills))
        for row_index, skill in enumerate(skills):
            gameplay_hits = gameplay_hit_count(skill)
            critical_samples = (
                gameplay_hits
                if skill.critical_samples is None
                else skill.critical_samples
            )
            critical_rate = (
                skill.critical_hits / critical_samples
                if critical_samples
                else 0.0
            )
            cast_count = max(0, int(skill.cast_count or 0))
            average_hits = gameplay_hits / cast_count if cast_count else None
            values = [
                skill.name,
                _compact_number(skill.damage),
                f"{skill.share * 100:.1f}%",
                str(cast_count) if cast_count else "—",
                str(gameplay_hits),
                (
                    f"{average_hits:.2f}".rstrip("0").rstrip(".")
                    if average_hits is not None
                    else "—"
                ),
                str(skill.critical_hits),
                f"{critical_rate * 100:.1f}%",
                _compact_number(skill.healing),
            ]
            for column, value in enumerate(values):
                align = Qt.AlignLeft | Qt.AlignVCenter if column == 0 else Qt.AlignCenter
                item = self._item(value, align)
                if column in (3, 5):
                    if cast_count:
                        item.setToolTip(
                            f"实际施放证据：{skill.direct_cast_count} 次；"
                            f"补全/推断：{skill.inferred_cast_count} 次"
                        )
                    else:
                        item.setToolTip(
                            "没有可靠的独立施放边界；召唤物、被动与持续领域"
                            "不会用 Hit 数伪造释放次数。"
                        )
                if column == 4 and skill.hits != gameplay_hits:
                    item.setToolTip(
                        f"实际 Hit {gameplay_hits}；伤害记录 {skill.hits} 条。"
                        "附加／状态伤害计入伤害额，不重复计为技能 Hit。"
                    )
                if column == 7 and skill.critical_samples is not None:
                    item.setToolTip(
                        "暴击率分母为具备真实命中证据的 "
                        f"{critical_samples} 次伤害判定；"
                        "附加／状态伤害记录不重复计入命中。"
                    )
                self.skills_table.setItem(row_index, column, item)
