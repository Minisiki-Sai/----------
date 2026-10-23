from __future__ import annotations

from ..shared import *
from ..background_priority import score_checkpoint

BuildEditor = None


class TimelinePresenterMixin:
    @staticmethod
    def _timeline_dispose_cell_widget(
        table: QTableWidget,
        row: int,
        column: int,
    ) -> None:
        widget = table.cellWidget(row, column)
        if widget is None:
            return
        table.removeCellWidget(row, column)
        widget.deleteLater()
        QApplication.sendPostedEvents(widget, QEvent.DeferredDelete)

    def _timeline_detail_combo(
        self,
        table: QTableWidget,
        row: int,
        choices: list[str],
        skill: str,
        *,
        kind: str,
        target_row: int,
        min_width: int,
        max_width: int = 0,
    ) -> Combo:
        combo = table.cellWidget(row, 2)
        if not isinstance(combo, Combo) or combo.property("timeline_kind") != kind:
            self._timeline_dispose_cell_widget(table, row, 2)
            combo = Combo(choices, skill, min_width=min_width, contents_length=8)
            combo.setEditable(False)
            combo.setProperty("timeline_kind", kind)
            combo.currentTextChanged.connect(
                lambda text, widget=combo: self._timeline_detail_combo_changed(widget, text)
            )
            table.setCellWidget(row, 2, combo)
            combo._timeline_choices_signature = tuple(choices)
        else:
            combo.blockSignals(True)
            signature = tuple(choices)
            if getattr(combo, "_timeline_choices_signature", ()) != signature:
                combo.clear()
                combo.addItems(choices)
                combo._timeline_choices_signature = signature
            if combo.currentText() != skill:
                combo.setCurrentText(skill)
            combo.blockSignals(False)
        combo.setProperty("timeline_target_row", int(target_row))
        if max_width > 0:
            combo.setMaximumWidth(max_width)
        combo.view().setMinimumWidth(option_width(choices, 150, 360))
        color = SkillBurstTimelineWidget.skill_cell_color(skill).name()
        combo.setStyleSheet(
            f"QComboBox {{ background:{color}; border:0; padding:0 22px 0 6px; }}"
            "QComboBox::drop-down { width:20px; border:0; }"
        )
        return combo

    def _timeline_detail_combo_changed(self, combo: Combo, text: str) -> None:
        target_row = int(as_number(combo.property("timeline_target_row"), -1))
        if target_row < 0:
            return
        if combo.property("timeline_kind") == "burst":
            self._skill_burst_detail_combo_changed(target_row, text)
        else:
            self._skill_long_detail_combo_changed(target_row, text)

    def _render_skill_burst_analysis(self, segments: list[dict[str, Any]], window: float) -> None:
        contribution_table = self.skill_burst_contribution_table
        detail_table = self.skill_burst_detail_table
        mixed_damage = norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
        contribution_table.setHorizontalHeaderLabels(
            ["技能", "次数", "基础伤害", "总伤害", "占比"]
            if mixed_damage
            else ["技能", "次数", "基础百分比", "总输出", "占比"]
        )
        included = [
            segment
            for segment in segments
            if as_number(segment.get("start")) < window - 1e-6
            and (
                as_number(segment.get("effective_score")) > 0
                or as_number(segment.get("raw_effective_score")) > 0
            )
        ]
        total_output = sum(
            as_number(segment.get("actual_damage"))
            if mixed_damage
            else as_number(segment.get("effective_score"))
            for segment in included
        )
        contributions: dict[str, dict[str, float]] = {}
        for segment in included:
            skill = norm(segment.get("skill")) or "未知技能"
            current = contributions.setdefault(skill, {"count": 0.0, "raw_output": 0.0, "output": 0.0})
            current["count"] += 1
            current["raw_output"] += as_number(
                segment.get("raw_actual_damage")
                if mixed_damage
                else segment.get("raw_effective_score")
            )
            current["output"] += as_number(
                segment.get("actual_damage")
                if mixed_damage
                else segment.get("effective_score")
            )
        contribution_rows = sorted(
            contributions.items(),
            key=lambda item: item[1]["output"],
            reverse=True,
        )
        contribution_table.setRowCount(len(contribution_rows))
        for row, (skill, values) in enumerate(contribution_rows):
            contribution_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self._set_summary_item(contribution_table, row, 0, skill)
            skill_item = contribution_table.item(row, 0)
            if skill_item is not None:
                skill_item.setBackground(SkillBurstTimelineWidget.skill_cell_color(skill))
            self._set_summary_item(contribution_table, row, 1, str(int(values["count"])), align_right=True)
            raw_text = self._compact_damage_text(values["raw_output"]) if mixed_damage else self._percent_text(values["raw_output"])
            output_text = self._compact_damage_text(values["output"]) if mixed_damage else self._percent_text(values["output"])
            self._set_summary_item(contribution_table, row, 2, raw_text, align_right=True)
            self._set_summary_item(contribution_table, row, 3, output_text, align_right=True)
            share = values["output"] / total_output * 100 if total_output > 0 else 0.0
            self._set_summary_item(contribution_table, row, 4, f"{share:.1f}%", align_right=True)

        for stale_row in range(len(segments), detail_table.rowCount()):
            self._timeline_dispose_cell_widget(detail_table, stale_row, 2)
        detail_table.setRowCount(len(segments))
        sequence_row = 0
        sequence_index_by_row: list[int] = []
        for row, segment in enumerate(segments):
            detail_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            start = as_number(segment.get("start"))
            end = as_number(segment.get("end"))
            skill = norm(segment.get("skill"))
            self._set_summary_item(detail_table, row, 0, str(row + 1), align_right=True)
            self._set_summary_item(detail_table, row, 1, segment.get("type", ""))
            if segment.get("timeline_hidden") or not skill:
                self._timeline_dispose_cell_widget(detail_table, row, 2)
                sequence_index_by_row.append(-1)
                self._set_summary_item(detail_table, row, 2, skill)
                skill_item = detail_table.item(row, 2)
                if skill_item is not None:
                    skill_item.setBackground(SkillBurstTimelineWidget.skill_cell_color(skill))
            else:
                sequence_index_by_row.append(sequence_row)
                choices = unique([
                    skill,
                    "↑ 上移一位",
                    "↓ 下移一位",
                    "删除此项",
                    *getattr(self, "_skill_burst_available_skills", []),
                ])
                edit_row = sequence_row
                self._timeline_detail_combo(
                    detail_table,
                    row,
                    choices,
                    skill,
                    kind="burst",
                    target_row=edit_row,
                    min_width=94,
                    max_width=180,
                )
                sequence_row += 1
            self._set_summary_item(detail_table, row, 3, f"{start:.2f}s", align_right=True)
            self._set_summary_item(detail_table, row, 4, f"{end:.2f}s", align_right=True)
            self._set_summary_item(detail_table, row, 5, f"{max(0.0, end - start):.2f}s", align_right=True)
            raw_output = as_number(segment.get("raw_effective_score"))
            self._set_summary_item(
                detail_table,
                row,
                6,
                self._percent_text(raw_output) if raw_output > 0 else "-",
                align_right=True,
            )
            output = as_number(segment.get("effective_score"))
            cast_time = max(0.0, end - start)
            dpa = as_number(segment.get("dpa"))
            if dpa <= 0 and output > 0 and cast_time > 0:
                dpa = output / cast_time
            output_dpa_text = "-"
            if as_number(segment.get("actual_damage")) > 0:
                output_dpa_text = self._compact_damage_text(segment.get("actual_damage"))
                if as_number(segment.get("actual_dpa")) > 0:
                    output_dpa_text += f" / {self._compact_damage_text(segment.get('actual_dpa'))}/s"
            elif output > 0:
                output_dpa_text = self._percent_text(output)
                if dpa > 0:
                    output_dpa_text += f" / {self._percent_text(dpa)}/s"
            self._set_summary_item(
                detail_table,
                row,
                7,
                output_dpa_text,
                align_right=True,
            )
        detail_table._sequence_index_by_row = sequence_index_by_row
        if self._skill_burst_selected_index >= 0:
            selected_row = self._timeline_table_row_for_sequence_index(
                detail_table, self._skill_burst_selected_index
            )
            if 0 <= selected_row < detail_table.rowCount():
                detail_table.setCurrentCell(selected_row, 2)

    def _render_skill_burst_segments(
        self,
        segments: list[dict[str, Any]],
        window: float,
        note: str,
        effect_windows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._skill_burst_last_segments = copy.deepcopy(segments)
        self._skill_burst_selected_index = min(self._skill_burst_selected_index, len(segments) - 1)
        visible_auto_segments = [
            segment for segment in self._skill_burst_auto_segments if not segment.get("timeline_hidden")
        ]
        visible_segments = [segment for segment in segments if not segment.get("timeline_hidden")]
        self.skill_burst_timeline.set_comparison_timeline(
            window,
            visible_auto_segments,
            visible_segments,
            note,
        )
        self.skill_burst_timeline.set_effect_windows(effect_windows or [])
        self.skill_burst_timeline.set_selected_index(self._skill_burst_selected_index)
        self.skill_burst_total_label.setText(self._skill_burst_axis_total_text("自选轴", segments, window))
        self.skill_burst_total_label.setToolTip(self._skill_burst_total_text(segments, window))
        self._render_skill_burst_analysis(segments, window)

    def _update_skill_burst_average_output_table(self, metrics: list[dict[str, Any]]) -> None:
        table = getattr(self, "skill_burst_average_table", None)
        if not isinstance(table, QTableWidget):
            return
        tabs = getattr(self, "skill_result_tabs", None)
        if isinstance(tabs, QTabWidget) and tabs.currentIndex() != 2:
            return
        scenarios = (
            ("实战模拟", frozenset({"practical"})),
            ("鸡尾酒", frozenset({"cocktail"})),
            ("精神爆发", frozenset({"burst"})),
            ("精神爆发+鸡尾酒", frozenset({"burst", "cocktail"})),
            ("时间加速", frozenset({"accel"})),
            ("时间加速+精神爆发", frozenset({"accel", "burst"})),
        )
        metric_key = tuple(
            (
                norm(metric.get("skill")),
                int(as_number(metric.get("level"))),
                round(as_number(metric.get("base_cooldown")), 5),
                round(as_number(metric.get("raw_score")), 5),
                round(as_number(metric.get("effective_score")), 5),
                norm(metric.get("element")),
                round(as_number(metric.get("cast_time")), 5),
            )
            for metric in metrics
        )
        skill_level_key = tuple(
            sorted(
                (skill_id, int(spin.value()))
                for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items()
                if isinstance(spin, QSpinBox)
            )
        )
        mastery_key = tuple(
            sorted(
                (skill_id, int(spin.value()))
                for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items()
                if isinstance(spin, QSpinBox)
            )
        )
        summary_key = tuple(
            sorted(
                (norm(key), round(as_number(value), 5))
                for key, value in getattr(self, "_skill_burst_base_summary", {}).items()
                if isinstance(value, (int, float))
            )
        )
        team_effect_key = ""
        if hasattr(self, "team_effect_table"):
            team_effect_key = repr(
                self._selected_team_effects(use_base_cooldowns=True, dedup=True)
            )
        cache_key = (
            norm(self.second_job.currentText()),
            metric_key,
            skill_level_key,
            mastery_key,
            summary_key,
            team_effect_key,
        )
        cached = getattr(self, "_skill_burst_average_output_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == cache_key:
            averages = list(cached[1])
        else:
            averages = []
            sentinel = object()
            saved_override = getattr(self, "_skill_burst_mode_override", sentinel)
            saved_periodic = copy.deepcopy(getattr(self, "_skill_burst_periodic_buffs", []))
            saved_candidates = copy.deepcopy(getattr(self, "_skill_burst_candidate_metrics", []))
            try:
                for mode_name, enabled_modes in scenarios:
                    self._skill_burst_mode_override = enabled_modes
                    segments, window = self._update_skill_burst_sim_preview(metrics, render_ui=False)
                    total = sum(
                        max(0.0, as_number(segment.get("effective_score")))
                        for segment in segments
                        if as_number(segment.get("start")) < window - 1e-6
                    )
                    averages.append((mode_name, total / window if window > 0 else 0.0))
            finally:
                if saved_override is sentinel:
                    self.__dict__.pop("_skill_burst_mode_override", None)
                else:
                    self._skill_burst_mode_override = saved_override
                self._skill_burst_periodic_buffs = saved_periodic
                self._skill_burst_candidate_metrics = saved_candidates
            self._skill_burst_average_output_cache = (cache_key, tuple(averages))

        practical_average = averages[0][1] if averages else 0.0
        table.setRowCount(len(averages))
        for row, (mode_name, average) in enumerate(averages):
            table.setRowHeight(row, EDIT_ROW_HEIGHT)
            self._set_summary_item(table, row, 0, mode_name)
            self._set_summary_item(
                table,
                row,
                1,
                f"{self._percent_text(average)}/s" if average > 0 else "-",
                align_right=True,
            )
            if row == 0:
                improvement_text = "基准"
            elif practical_average > 0:
                improvement = (average / practical_average - 1.0) * 100.0
                improvement_text = f"{improvement:+.2f}%"
            else:
                improvement_text = "-"
            self._set_summary_item(table, row, 2, improvement_text, align_right=True)

    def _update_skill_long_sim_preview(self, metrics: list[dict[str, Any]]) -> None:
        timeline = getattr(self, "skill_long_timeline", None)
        table = getattr(self, "skill_long_detail_table", None)
        tabs = getattr(self, "skill_result_tabs", None)
        if not isinstance(timeline, SkillBurstTimelineWidget) or not isinstance(table, QTableWidget):
            return
        if isinstance(tabs, QTabWidget) and tabs.currentIndex() != 3:
            return

        long_window = self._skill_long_window_seconds()
        schedule = self._skill_long_schedule(long_window)
        team_link_requested = bool(
            isinstance(getattr(self, "skill_long_team_link_check", None), QCheckBox)
            and self.skill_long_team_link_check.isChecked()
        )
        team_context = self._team_practical_runtime_context(metrics, long_window) if team_link_requested else None
        if team_context is not None:
            schedule = team_context["schedule"]
        current_job = norm(self.second_job.currentText())
        if self._skill_long_manual_job != current_job:
            self._skill_long_manual_sequence = None
            self._skill_long_manual_job = current_job
        base_context = {
            "effects": (
                self._selected_team_effects(
                    use_base_cooldowns=True,
                    dedup=True,
                )
                if hasattr(self, "team_effect_table")
                else []
            ),
            "schedule": copy.deepcopy(schedule),
            "windows": [],
            "provider": "",
            "duplicate_build_buffs": set(),
        }
        pure_result = self._team_practical_cached_simulate(
            metrics,
            base_context,
            self._skill_long_manual_sequence,
        )
        auto_segments = copy.deepcopy(pure_result["auto_segments"])
        segments = copy.deepcopy(pure_result["segments"])
        window = max(0.0, as_number(pure_result["window"]))
        all_learned_metrics = copy.deepcopy(pure_result["all_metrics"])
        support_actions = copy.deepcopy(pure_result["support_actions"])
        manual_issues = list(pure_result.get("manual_issues", []))
        effect_windows = copy.deepcopy(pure_result["own_windows"])
        self._skill_burst_periodic_buffs = copy.deepcopy(
            pure_result.get("periodic_buffs", [])
        )
        self._skill_long_candidate_metrics = copy.deepcopy(
            pure_result.get("candidate_metrics", [])
        )
        self._skill_long_last_all_metrics = all_learned_metrics
        self._skill_long_last_support_actions = support_actions
        self._skill_long_last_window = window
        self._skill_long_available_skills = list(
            pure_result.get("available_skills", [])
        )
        auto_sequence = [
            norm(segment.get("skill"))
            for segment in auto_segments
            if norm(segment.get("skill")) and not segment.get("timeline_hidden")
        ]
        manual_matches_auto = (
            self._skill_long_manual_sequence is None
            or [
                norm(skill)
                for skill in self._skill_long_manual_sequence
                if norm(skill)
            ]
            == auto_sequence
        )

        self._skill_long_auto_segments = copy.deepcopy(auto_segments)
        self._skill_long_last_segments = copy.deepcopy(segments)
        visible_auto_segments = [segment for segment in auto_segments if not segment.get("timeline_hidden")]
        visible_segments = [segment for segment in segments if not segment.get("timeline_hidden")]
        timeline.set_comparison_timeline(
            window,
            visible_auto_segments,
            visible_segments,
            f"{window:g} 秒长时间实战轴；可拖动自选轴，或在下方技能列中调整顺序。",
        )
        timeline.set_effect_windows(effect_windows)
        timeline.set_selected_index(self._skill_long_selected_index)
        QTimer.singleShot(0, lambda: self.skill_long_scroll.verticalScrollBar().setValue(0))
        total = sum(max(0.0, as_number(segment.get("effective_score"))) for segment in segments)
        average = total / window if window > 0 else 0.0
        channel_averages = self._skill_burst_channel_totals(segments, divisor=window)
        auto_total = sum(max(0.0, as_number(segment.get("effective_score"))) for segment in auto_segments)
        auto_average = auto_total / window if window > 0 else 0.0
        auto_channel_averages = self._skill_burst_channel_totals(auto_segments, divisor=window)
        mixed_damage = norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
        displayed_total = (
            sum(max(0.0, as_number(segment.get("actual_damage"))) for segment in segments)
            if mixed_damage else total
        )
        displayed_average = displayed_total / window if window > 0 else 0.0
        displayed_auto_total = (
            sum(max(0.0, as_number(segment.get("actual_damage"))) for segment in auto_segments)
            if mixed_damage else auto_total
        )
        displayed_auto_average = displayed_auto_total / window if window > 0 else 0.0
        team_link_active = bool(team_context and team_context["effects"])
        linked_result = (
            self._team_practical_cached_simulate(metrics, team_context, self._skill_long_manual_sequence)
            if team_link_active and team_context is not None
            else None
        )
        linked_total = (
            sum(
                max(0.0, as_number(segment.get("effective_score")))
                for segment in linked_result["segments"]
            )
            if linked_result is not None
            else total
        )
        linked_average = linked_total / window if window > 0 else 0.0
        displayed_linked_total = (
            sum(
                max(0.0, as_number(segment.get("actual_damage")))
                for segment in linked_result["segments"]
            )
            if mixed_damage and linked_result is not None
            else displayed_total if mixed_damage
            else linked_total
        )
        displayed_linked_average = displayed_linked_total / window if window > 0 else 0.0
        linked_channel_averages = (
            self._skill_burst_channel_totals(linked_result["segments"], divisor=window)
            if linked_result is not None
            else channel_averages
        )
        gain_percent = (
            (displayed_linked_total / displayed_total - 1.0) * 100.0
            if displayed_total > 0 else 0.0
        )
        if team_link_active:
            if mixed_damage:
                link_html = (
                    '<br>'
                    '<span style="color:#0369a1; font-weight:700;">配队联动：</span>'
                    f'<span style="color:#0369a1; font-weight:700;">总伤害 {self._compact_damage_text(displayed_linked_total)}</span>'
                    f'<span style="color:#7c3aed; font-weight:700;">&nbsp;|&nbsp;提升 {gain_percent:+.2f}%</span>'
                    f'<span style="color:#64748b;">&nbsp;|&nbsp;联动DPS {self._compact_damage_text(displayed_linked_average)}/s</span>'
                )
            else:
                link_html = (
                    '<br>'
                    f'<span style="color:#0369a1; font-weight:700;">配队联动：</span>{self._skill_burst_channels_html(linked_channel_averages)}；'
                    f'<span style="color:#0369a1; font-weight:700;">总计 {self._percent_text(linked_total)}</span>'
                    f'<span style="color:#7c3aed; font-weight:700;">&nbsp;|&nbsp;提升 {gain_percent:+.2f}%</span>'
                    f'<span style="color:#64748b;">&nbsp;|&nbsp;联动DPS {self._percent_text(linked_average)}/s</span>'
                )
        elif team_link_requested:
            link_html = '<span style="color:#b45309;">&nbsp;|&nbsp;当前没有可应用的队友效果</span>'
        else:
            link_html = ""
        auto_html = ""
        if not manual_matches_auto:
            if mixed_damage:
                auto_html = (
                    '<span style="color:#1d4ed8; font-weight:700;">自动轴平均输出：</span>'
                    f'<span style="color:#047857; font-weight:700;">总伤害 {self._compact_damage_text(displayed_auto_total)}</span>'
                    f'<span style="color:#64748b;">&nbsp;|&nbsp;DPS {self._compact_damage_text(displayed_auto_average)}/s</span><br>'
                )
            else:
                auto_html = (
                    '<span style="color:#1d4ed8; font-weight:700;">自动轴平均输出：</span>'
                    f'{self._skill_burst_channels_html(auto_channel_averages)}；'
                    f'<span style="color:#047857; font-weight:700;">计Buff总 {self._percent_text(auto_total)}</span>'
                    f'<span style="color:#64748b;">&nbsp;|&nbsp;DPS {self._percent_text(auto_average)}/s</span><br>'
                )
        if mixed_damage:
            long_total_html = (
                '<span style="color:#1e293b; font-weight:700;">长时间自选轴平均输出：</span>'
                f'<span style="color:#047857; font-weight:700;">总伤害 {self._compact_damage_text(displayed_total)}</span>'
                f'<span style="color:#64748b;">&nbsp;|&nbsp;未联动DPS {self._compact_damage_text(displayed_average)}/s</span>'
            )
        else:
            long_total_html = (
                '<span style="color:#1e293b; font-weight:700;">长时间自选轴平均输出：</span>'
                f'<span style="color:#64748b;">未联动：</span>{self._skill_burst_channels_html(channel_averages)}；'
                f'<span style="color:#047857; font-weight:700;">计Buff总 {self._percent_text(total)}</span>'
                f'<span style="color:#64748b;">&nbsp;|&nbsp;未联动DPS {self._percent_text(average)}/s</span>'
            )
        self.skill_long_total_label.setText(
            auto_html
            + long_total_html
            + link_html
            + (
                f'<span style="color:#b45309;">&nbsp;|&nbsp;{manual_issues[0]}</span>'
                if manual_issues
                else ""
            )
        )

        contribution_table = getattr(self, "skill_long_contribution_table", None)
        if isinstance(contribution_table, QTableWidget):
            contribution_table.setHorizontalHeaderLabels(
                ["技能", "次数", "总伤害", "伤害比例"]
                if mixed_damage
                else ["技能", "次数", "总输出", "伤害比例"]
            )
            contributions: dict[str, dict[str, float]] = {}
            for segment in segments:
                output = max(0.0, as_number(
                    segment.get("actual_damage")
                    if mixed_damage
                    else segment.get("effective_score")
                ))
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
                ratio = values["output"] / displayed_total * 100 if displayed_total > 0 else 0.0
                self._set_summary_item(contribution_table, row, 3, f"{ratio:.2f}%", align_right=True)

        table.setHorizontalHeaderLabels(
            ["序号", "类型", "技能", "开始", "结束", "基础伤害", "单次输出/DPA"]
            if mixed_damage
            else ["序号", "类型", "技能", "开始", "结束", "基础百分比", "单次输出/DPA"]
        )
        for stale_row in range(len(segments), table.rowCount()):
            self._timeline_dispose_cell_widget(table, stale_row, 2)
        table.setRowCount(len(segments))
        sequence_row = 0
        sequence_index_by_row: list[int] = []
        for row, segment in enumerate(segments):
            table.setRowHeight(row, EDIT_ROW_HEIGHT)
            start = max(0.0, as_number(segment.get("start")))
            end = max(start, as_number(segment.get("end")))
            output = max(0.0, as_number(segment.get("effective_score")))
            raw_output = max(0.0, as_number(segment.get("raw_effective_score")))
            dpa = max(0.0, as_number(segment.get("dpa")))
            self._set_summary_item(table, row, 0, str(row + 1), align_right=True)
            self._set_summary_item(table, row, 1, segment.get("type", ""))
            skill = norm(segment.get("skill"))
            if segment.get("timeline_hidden") or not skill:
                self._timeline_dispose_cell_widget(table, row, 2)
                sequence_index_by_row.append(-1)
                self._set_summary_item(table, row, 2, skill)
                skill_item = table.item(row, 2)
                if skill_item is not None:
                    skill_item.setBackground(SkillBurstTimelineWidget.skill_cell_color(skill))
            else:
                sequence_index_by_row.append(sequence_row)
                choices = unique([
                    skill,
                    "↑ 上移一位",
                    "↓ 下移一位",
                    "删除此项",
                    *self._skill_long_available_skills,
                ])
                edit_row = sequence_row
                self._timeline_detail_combo(
                    table,
                    row,
                    choices,
                    skill,
                    kind="long",
                    target_row=edit_row,
                    min_width=102,
                )
                sequence_row += 1
            self._set_summary_item(table, row, 3, f"{start:.2f}s", align_right=True)
            self._set_summary_item(table, row, 4, f"{end:.2f}s", align_right=True)
            raw_display = max(0.0, as_number(
                segment.get("raw_actual_damage")
                if mixed_damage
                else raw_output
            ))
            self._set_summary_item(
                table,
                row,
                5,
                self._compact_damage_text(raw_display)
                if mixed_damage and raw_display > 0
                else self._percent_text(raw_display) if raw_display > 0
                else "-",
                align_right=True,
            )
            if as_number(segment.get("actual_damage")) > 0:
                output_text = self._compact_damage_text(segment.get("actual_damage"))
                if as_number(segment.get("actual_dpa")) > 0:
                    output_text += f" / {self._compact_damage_text(segment.get('actual_dpa'))}/s"
            else:
                output_text = self._percent_text(output) if output > 0 else "-"
                if output > 0 and dpa > 0:
                    output_text += f" / {self._percent_text(dpa)}/s"
            self._set_summary_item(table, row, 6, output_text, align_right=True)
        table._sequence_index_by_row = sequence_index_by_row
        if self._skill_long_selected_index >= 0:
            selected_row = self._timeline_table_row_for_sequence_index(
                table, self._skill_long_selected_index
            )
            if 0 <= selected_row < table.rowCount():
                table.setCurrentCell(selected_row, 2)
        self._render_skill_long_candidates(
            list(getattr(self, "_skill_long_candidate_metrics", []))
        )

    def _update_skill_burst_sim_preview(
        self,
        metrics: list[dict[str, Any]],
        *,
        render_ui: bool = True,
    ) -> tuple[list[dict[str, Any]], float]:
        if getattr(self, "_timeline_is_pure_runtime", False):
            return self._calculate_skill_burst_sim_preview_data(
                metrics,
                render_ui=render_ui,
            )
        instance_algorithm_override = any(
            name in self.__dict__
            for name in (
                "_skill_burst_all_learned_metrics",
                "_skill_burst_support_actions",
                "_skill_burst_candidate_pool",
            )
        )
        if not render_ui and instance_algorithm_override:
            # Kept as an explicit test/debug seam. Production paths always use
            # the immutable runtime below.
            return self._calculate_skill_burst_sim_preview_data(
                metrics,
                render_ui=False,
            )
        window = self._skill_burst_sim_window_seconds()
        if window <= 0:
            if not render_ui:
                return [], 0.0
            return self._calculate_skill_burst_sim_preview_data(
                metrics,
                render_ui=True,
            )

        current_job = norm(self.second_job.currentText())
        if render_ui and self._skill_burst_manual_job != current_job:
            self._skill_burst_manual_sequence = None
            self._skill_burst_manual_job = current_job
        try:
            result = self._calculate_skill_burst_pure_result(
                metrics,
                manual_sequence=(
                    self._skill_burst_manual_sequence if render_ui else None
                ),
            )
        except Exception as exc:
            self._timeline_engine_last_fallback = f"{type(exc).__name__}: {exc}"
            return self._calculate_skill_burst_sim_preview_data(
                metrics,
                render_ui=render_ui,
            )
        if not render_ui:
            return (
                copy.deepcopy(result.get("auto_segments", [])),
                max(0.0, as_number(result.get("window"))),
            )
        return self._render_skill_burst_pure_result(metrics, result)

    def _calculate_skill_burst_pure_result(
        self,
        metrics: list[dict[str, Any]],
        *,
        manual_sequence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create one immutable input snapshot and run the data-only engine."""
        schedule_override = getattr(self, "_skill_burst_schedule_override", None)
        mode_override = getattr(self, "_skill_burst_mode_override", None)
        if mode_override is None and not isinstance(schedule_override, dict):
            mode_override = frozenset(
                mode
                for mode in ("practical", "cocktail", "burst", "accel")
                if self._skill_burst_sim_enabled(mode)
            )
        runtime_values = {
            "_skill_burst_schedule_override": copy.deepcopy(schedule_override),
            "_skill_burst_mode_override": copy.deepcopy(mode_override),
            "_timeline_metrics_input": copy.deepcopy(metrics),
            "_timeline_base_summary_input": copy.deepcopy(
                getattr(self, "_skill_burst_base_summary", {})
            ),
            "_timeline_manual_sequence_input": copy.deepcopy(manual_sequence),
        }
        from ..shared import _ExtremeTimelineProxy

        selected_effects = (
            self._selected_team_effects(use_base_cooldowns=True, dedup=True)
            if hasattr(self, "team_effect_table")
            else []
        )
        runtime = _ExtremeTimelineProxy(
            self,
            self.collect_build(),
            copy.deepcopy(selected_effects),
            runtime_values=runtime_values,
            extreme_mode=False,
        )
        cache = getattr(self, "_timeline_pure_result_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._timeline_pure_result_cache = cache
        cached = cache.get(runtime.snapshot_fingerprint)
        if hasattr(cached, "materialize"):
            return cached.materialize()
        calculation = runtime.calculate_timeline(
            copy.deepcopy(metrics),
            copy.deepcopy(getattr(self, "_skill_burst_base_summary", {})),
            manual_sequence=copy.deepcopy(manual_sequence),
        )
        cache[runtime.snapshot_fingerprint] = calculation
        while len(cache) > 12:
            cache.pop(next(iter(cache)))
        return calculation.materialize()

    def _render_skill_burst_pure_result(
        self,
        metrics: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], float]:
        """Render an immutable engine result without recalculating the axis."""
        auto_segments = copy.deepcopy(result.get("auto_segments", []))
        segments = copy.deepcopy(result.get("segments", auto_segments))
        window = max(0.0, as_number(result.get("window")))
        all_metrics = copy.deepcopy(result.get("all_metrics", []))
        support_actions = copy.deepcopy(result.get("support_actions", []))
        candidate_metrics = copy.deepcopy(result.get("candidate_metrics", []))
        self._skill_burst_last_all_metrics = all_metrics
        self._skill_burst_last_support_actions = support_actions
        self._skill_burst_last_window = window
        self._skill_burst_periodic_buffs = copy.deepcopy(
            result.get("periodic_buffs", [])
        )
        self._skill_burst_generated_effect_windows = copy.deepcopy(
            result.get("generated_effect_windows", [])
        )
        for name, value in result.get("planner_state", {}).items():
            setattr(self, name, copy.deepcopy(value))

        self._skill_burst_available_skills = list(
            result.get("available_skills", [])
        )
        add_current = norm(self.skill_burst_add_combo.currentText())
        self.skill_burst_add_combo.blockSignals(True)
        self.skill_burst_add_combo.clear()
        self.skill_burst_add_combo.addItems(
            ["选择技能", *self._skill_burst_available_skills]
        )
        self.skill_burst_add_combo.setCurrentText(
            add_current
            if add_current in self._skill_burst_available_skills
            else "选择技能"
        )
        self.skill_burst_add_combo.blockSignals(False)
        self._render_skill_burst_candidates(candidate_metrics)

        self._skill_burst_auto_segments = auto_segments
        self.skill_burst_auto_total_label.setText(
            self._skill_burst_axis_total_text("自动轴", auto_segments, window)
        )
        self.skill_burst_auto_total_label.setToolTip(
            self._skill_burst_total_text(auto_segments, window)
        )
        manual_note = norm(result.get("note"))
        if self._skill_burst_manual_sequence is not None:
            manual_note += "；手动编排中，可拖动技能块或在技能列表的下拉框中调整。"
            manual_issues = list(result.get("manual_issues", []))
            if manual_issues:
                manual_note += f"；{manual_issues[0]}"
        else:
            manual_note += "；当前跟随自动轴，首次调整后转为独立自选轴。"
        self._render_skill_burst_segments(
            segments,
            window,
            manual_note,
            copy.deepcopy(result.get("effect_windows", [])),
        )
        self._update_skill_burst_average_output_table(metrics)
        return auto_segments, window

    def _calculate_skill_burst_sim_preview_data(
        self,
        metrics: list[dict[str, Any]],
        *,
        render_ui: bool = True,
    ) -> tuple[list[dict[str, Any]], float]:
        if not hasattr(self, "skill_burst_timeline"):
            return [], 0.0
        window = self._skill_burst_sim_window_seconds()
        if window <= 0:
            if not render_ui:
                return [], 0.0
            placeholder_window = SKILL_BURST_SIM_DURATIONS["精神爆发"]
            note = "请勾选精神爆发、时间加速、鸡尾酒或实战模拟后生成时间轴。"
            self._skill_burst_auto_segments = []
            self.skill_burst_auto_total_label.setText(
                self._skill_burst_axis_total_text("自动轴", [], placeholder_window)
            )
            self._render_skill_burst_segments([], placeholder_window, note)
            self._render_skill_burst_candidates([])
            self._update_skill_burst_average_output_table(metrics)
            return [], 0.0

        new_scheme_1_cache_key = None
        if (
            self._skill_burst_new_scheme_1_enabled()
            and not getattr(self, "_skill_burst_new_scheme_1_quality_guard_active", False)
        ):
            new_scheme_1_cache_key = self._skill_burst_new_scheme_1_cache_key(
                metrics,
                window,
            )
            may_reuse_visible_result = (
                not render_ui
                or (
                    getattr(self, "_skill_burst_new_scheme_1_last_render_key", None)
                    == new_scheme_1_cache_key
                    and self._skill_burst_manual_sequence is None
                )
            )
            if may_reuse_visible_result:
                cached_result = self._skill_burst_new_scheme_1_cached_result(
                    new_scheme_1_cache_key
                )
                if cached_result is not None:
                    if render_ui:
                        cached_segments, cached_window = cached_result
                        self._skill_burst_auto_segments = copy.deepcopy(cached_segments)
                        self.skill_burst_auto_total_label.setText(
                            self._skill_burst_axis_total_text(
                                "自动轴",
                                cached_segments,
                                cached_window,
                            )
                        )
                        self.skill_burst_auto_total_label.setToolTip(
                            self._skill_burst_total_text(
                                cached_segments,
                                cached_window,
                            )
                        )
                        self._render_skill_burst_segments(
                            cached_segments,
                            cached_window,
                            "已恢复当前模式的缓存时间轴。",
                            effect_windows=copy.deepcopy(
                                getattr(self, "_skill_burst_generated_effect_windows", [])
                            ),
                        )
                        self._render_skill_burst_candidates(
                            list(getattr(self, "_skill_burst_candidate_metrics", []))
                        )
                        self._update_skill_burst_average_output_table(metrics)
                    return cached_result

        self._skill_burst_periodic_buffs = []
        self._skill_burst_active_attrs_cache = {}
        self._skill_burst_cooldown_ready_cache = {}
        self._skill_burst_new_scheme_1_metric_value_cache = {}
        self._skill_burst_new_scheme_1_state_attrs_cache = {}
        self._skill_burst_fast_action_attrs_cache = {}
        all_learned_metrics = self._skill_burst_all_learned_metrics(
            getattr(self, "_skill_burst_base_summary", {}),
            metrics,
        )
        candidate_metrics = self._skill_burst_candidate_pool(all_learned_metrics)
        self._skill_burst_candidate_metrics = candidate_metrics
        raw_candidates = [
            metric
            for metric in candidate_metrics
            if as_number(metric.get("effective_score")) > 0
            and self._skill_burst_skill_key(metric.get("skill")) not in SKILL_BURST_SIM_PASSIVE_SUMMONS
            and self._skill_burst_skill_key(metric.get("skill")) != self._skill_burst_reload_skill()
        ]
        current_job = norm(self.second_job.currentText())
        auto_excludes = {
            self._skill_burst_skill_key(skill)
            for skill in SKILL_BURST_AUTO_EXCLUDES_BY_JOB.get(current_job, set())
        }
        if auto_excludes:
            raw_candidates = [
                metric
                for metric in raw_candidates
                if self._skill_burst_skill_key(metric.get("skill")) not in auto_excludes
            ]
        unused_shared_dance = {
            "刀锋舞者": "幻影之舞",
            "灵魂舞者": "弗里兹科之舞",
        }.get(current_job, "")
        if unused_shared_dance:
            raw_candidates = [
                metric for metric in raw_candidates
                if self._skill_burst_skill_key(metric.get("skill")) != unused_shared_dance
            ]
        if self._skill_burst_uses_fury():
            existing_raw_keys = {
                self._skill_burst_skill_key(metric.get("skill"))
                for metric in raw_candidates
            }
            raw_candidates.extend(
                metric
                for metric in all_learned_metrics
                if self._skill_burst_skill_key(metric.get("skill")) in SKILL_BURST_FURY_GENERATORS
                and self._skill_burst_skill_key(metric.get("skill")) not in existing_raw_keys
                and as_number(metric.get("effective_score")) > 0
            )
        candidates_by_skill: dict[str, dict[str, Any]] = {}
        for metric in raw_candidates:
            skill_key = self._skill_burst_skill_key(metric.get("skill"))
            if not skill_key:
                continue
            current_metric = candidates_by_skill.get(skill_key)
            if current_metric is None or self._skill_burst_metric_score(metric) > self._skill_burst_metric_score(current_metric):
                candidates_by_skill[skill_key] = metric
        candidates = self._skill_burst_mark_timeline_fillers(
            list(candidates_by_skill.values())
        )
        metrics_by_skill = {
            self._skill_burst_skill_key(metric.get("skill")): metric
            for metric in all_learned_metrics
        }
        support_actions = self._skill_burst_support_actions(metrics_by_skill)
        non_damaging_support_keys = {
            self._skill_burst_skill_key(action.get("skill"))
            for action in support_actions
            if action.get("non_damaging_support")
        }
        if non_damaging_support_keys:
            candidates = [
                metric
                for metric in candidates
                if self._skill_burst_skill_key(metric.get("skill"))
                not in non_damaging_support_keys
            ]
            candidate_metrics = [
                metric
                for metric in candidate_metrics
                if self._skill_burst_skill_key(metric.get("skill"))
                not in non_damaging_support_keys
            ]
            self._skill_burst_candidate_metrics = candidate_metrics
        new_scheme_1 = self._skill_burst_new_scheme_1_enabled()
        self._skill_burst_new_scheme_1_plan_queue = []
        self._skill_burst_new_scheme_1_planning_stats = {
            "decisions": 0,
            "ambiguous_decisions": 0,
            "rollout_roots": 0,
            "pair_roots": 0,
        }
        allowed_skill_keys = getattr(self, "_skill_burst_allowed_skill_keys", None)
        if isinstance(allowed_skill_keys, set):
            allowed_skill_keys = {
                self._skill_burst_skill_key(skill) for skill in allowed_skill_keys
            }
            candidates = [
                metric
                for metric in candidates
                if self._skill_burst_skill_key(metric.get("skill")) in allowed_skill_keys
            ]
            candidate_metrics = [
                metric
                for metric in candidate_metrics
                if self._skill_burst_skill_key(metric.get("skill")) in allowed_skill_keys
            ]
            support_actions = [
                action
                for action in support_actions
                if self._skill_burst_skill_key(action.get("skill")) in allowed_skill_keys
            ]
        excluded_skill_keys = {
            self._skill_burst_skill_key(skill)
            for skill in getattr(self, "_skill_burst_excluded_skill_keys", set())
            if self._skill_burst_skill_key(skill)
        }
        if excluded_skill_keys:
            candidates = [
                metric
                for metric in candidates
                if self._skill_burst_skill_key(metric.get("skill"))
                not in excluded_skill_keys
            ]
            candidate_metrics = [
                metric
                for metric in candidate_metrics
                if self._skill_burst_skill_key(metric.get("skill"))
                not in excluded_skill_keys
            ]
            support_actions = [
                action
                for action in support_actions
                if self._skill_burst_skill_key(action.get("skill"))
                not in excluded_skill_keys
            ]
        calculated_available_skills = unique(
            [self._skill_burst_skill_key(metric.get("skill")) for metric in candidates]
            + [self._skill_burst_skill_key(metric.get("skill")) for metric in candidate_metrics]
            + [
                self._skill_burst_skill_key(action.get("skill"))
                for action in support_actions
                if not action.get("triggered_only")
            ]
            + ([self._skill_burst_reload_skill()] if self._skill_burst_reload_available() else [])
        )
        self._skill_burst_calculated_candidate_metrics = copy.deepcopy(candidate_metrics)
        self._skill_burst_calculated_available_skills = calculated_available_skills
        if render_ui:
            self._skill_burst_last_all_metrics = copy.deepcopy(all_learned_metrics)
            self._skill_burst_last_support_actions = copy.deepcopy(support_actions)
            self._skill_burst_last_window = window
        if render_ui and self._skill_burst_manual_job != current_job:
            self._skill_burst_manual_sequence = None
            self._skill_burst_manual_job = current_job
        if render_ui:
            self._skill_burst_available_skills = calculated_available_skills
            add_current = norm(self.skill_burst_add_combo.currentText())
            self.skill_burst_add_combo.blockSignals(True)
            self.skill_burst_add_combo.clear()
            self.skill_burst_add_combo.addItems(["选择技能", *self._skill_burst_available_skills])
            self.skill_burst_add_combo.setCurrentText(add_current if add_current in self._skill_burst_available_skills else "选择技能")
            self.skill_burst_add_combo.blockSignals(False)
            self._render_skill_burst_candidates(candidate_metrics)
        damage_available_at = {self._skill_burst_skill_key(metric.get("skill")): 0.0 for metric in candidates}
        damage_row_by_skill = {self._skill_burst_skill_key(metric.get("skill")): self._skill_burst_skill_key(metric.get("skill")) for metric in candidates}
        damage_base_cooldown_by_skill = {
            self._skill_burst_skill_key(metric.get("skill")): max(0.0, as_number(metric.get("base_cooldown")))
            for metric in candidates
        }
        charged_skill_maxes = {
            skill: count
            for skill, count in self._skill_burst_charged_skill_maxes().items()
            if skill in damage_available_at
        }
        charged_skill_charges = dict(charged_skill_maxes)
        charged_skill_recharge_at: dict[str, float | None] = {skill: None for skill in charged_skill_maxes}
        support_available_at = {
            index: max(0.0, as_number(action.get("initial_available_at")))
            for index, action in enumerate(support_actions)
        }
        for action in support_actions:
            if not action.get("pinned_cast_schedule"):
                continue
            action_key = self._skill_burst_skill_key(action.get("skill"))
            if action_key in damage_available_at:
                damage_available_at[action_key] = max(
                    damage_available_at[action_key],
                    max(0.0, as_number(action.get("initial_available_at"))),
                )
        support_expires_at = {index: 0.0 for index, _action in enumerate(support_actions)}
        summon_active_until: dict[str, list[float]] = {}
        summon_removal_schedule = self._skill_burst_summon_removal_schedule()
        summon_removal_cursor = 0
        ammo_rule = self._skill_burst_ammo_rule()
        ammo = 0
        ammo_gain = max(1, int(as_number(ammo_rule.get("gain"), 1))) if ammo_rule else 0
        ammo_max = max(ammo_gain, int(as_number(ammo_rule.get("max"), 0))) if ammo_rule else 0
        reload_skill = self._skill_burst_reload_skill()
        reload_available_at = 0.0
        reload_base_cooldown = self._skill_burst_reload_base_cooldown() if self._skill_burst_reload_available() else 0.0
        previous_skill = ""
        fury = 0
        fury_expires_at = 0.0
        guardian_phantoms = 0
        used_skill_keys: set[str] = set()
        deferred_state_expiries: dict[tuple[int, int], int] = {}
        passive_cooldown_sources = {
            source_skill: config
            for source_skill, config in self._skill_burst_passive_cooldown_sources().items()
            if not config.get("cast_driven")
        }
        passive_cooldown_next_at = {
            source_skill: self._skill_burst_passive_cooldown_source_interval(source_skill, 0.0)
            for source_skill in passive_cooldown_sources
        }
        segments: list[dict[str, Any]] = []
        current = 0.0
        guard = 0
        guard_limit = max(80, int(window * 12))
        while current < window - 1e-6 and guard < guard_limit:
            score_checkpoint()
            guard += 1
            if (
                not render_ui
                and guard % 4 == 0
                and getattr(self, "_skill_burst_background_process_events", False)
            ):
                now = time.perf_counter()
                last_yield = as_number(
                    getattr(self, "_skill_burst_background_last_yield", 0.0)
                )
                if now - last_yield >= 0.5:
                    self._skill_burst_background_last_yield = now
                    callback = getattr(
                        self,
                        "_skill_burst_background_progress_callback",
                        None,
                    )
                    if callable(callback):
                        callback(current, window)
            blocked_range = self._skill_burst_blocked_range_at(current)
            if blocked_range is not None:
                from ..fury_planner import prepare_fury_in_downtime
                prepared = prepare_fury_in_downtime(self, locals())
                if prepared is not None:
                    current, fury, fury_expires_at, previous_skill = prepared
                    continue
                current = min(window, blocked_range[1])
                previous_skill = ""
                continue
            # An action that starts before a mechanism may finish normally.
            # New actions do not start inside the stop interval, while damage
            # falling inside that interval is settled separately as zero.
            action_window_end = window
            # Captured Spirit Boost/Time Acceleration casts are part of the
            # same-environment score axis. Do not start an ordinary action that
            # would run across their recorded cast time; reserve that point in
            # the rotation, then let the mandatory support action take it.
            pinned_support_start = self._skill_burst_next_pinned_support_start(
                support_actions,
                current,
            )
            if pinned_support_start is not None:
                action_window_end = min(action_window_end, pinned_support_start)
            if fury > 0 and fury_expires_at <= current + 1e-6:
                fury = 0
            self._skill_burst_prune_summon_state(summon_active_until, current)
            summon_removal_cursor, removed_summons_at_current = (
                self._skill_burst_apply_summon_removals(
                    summon_active_until,
                    current,
                    summon_removal_cursor,
                    summon_removal_schedule,
                )
            )
            if removed_summons_at_current:
                # Receding-horizon choices are cached between decisions. A
                # factual summon death changes legal capacity and relic-count
                # state, so no decision planned before it may be reused.
                self._skill_burst_new_scheme_1_plan_queue = []
            active_attrs = self._skill_burst_active_attrs(support_actions, support_expires_at, current)
            continuous_support_deadlines = []
            for index, action in enumerate(support_actions):
                if not action.get("continuous_uptime"):
                    continue
                expires_at = as_number(support_expires_at.get(index))
                if expires_at <= current + 1e-6:
                    continue
                cast_time = self._skill_burst_support_cast_time(action, current, active_attrs)
                latest_start = expires_at
                if not self._skill_burst_action_starts_on_cast(action):
                    latest_start -= max(0.0, cast_time)
                if as_number(support_available_at.get(index)) <= latest_start + 1e-6:
                    continuous_support_deadlines.append(max(current, latest_start))
            if continuous_support_deadlines:
                # Reserve the latest safe refresh start for a hard continuous
                # buff. Its replacement must finish applying no later than the
                # prior copy expires.
                action_window_end = min(action_window_end, min(continuous_support_deadlines))
            if norm(self.second_job.currentText()) == "机械大师":
                mode_index = next(
                    (
                        index for index, action in enumerate(support_actions)
                        if self._skill_burst_skill_key(action.get("skill")) == "机械模式"
                    ),
                    None,
                )
                normal_summon_count = sum(
                    len(summon_active_until.get(skill, []))
                    for skill in SKILL_BURST_ENGINEER_SUMMONS
                )
                if (
                    mode_index is not None
                    and support_expires_at.get(mode_index, 0.0) > current + 1e-6
                    and normal_summon_count <= 0
                ):
                    support_expires_at[mode_index] = current
                    segments.append({
                        "start": current,
                        "end": current,
                        "skill": "取消机械模式",
                        "type": "光环切换",
                        "kind": "state",
                        "dpa": 0.0,
                        "effective_score": 0.0,
                        "raw_effective_score": 0.0,
                        "element": "",
                        "timeline_hidden": True,
                        "detail": "普通召唤塔结束，瞬间取消光环，不占用施法时间",
                    })
                    active_attrs = self._skill_burst_active_attrs(support_actions, support_expires_at, current)
            if self._skill_burst_uses_fury():
                active_attrs[SKILL_BURST_FURY_MARKER] = float(fury)
            if self._skill_burst_uses_guardian_phantoms():
                active_attrs[SKILL_BURST_GUARDIAN_PHANTOM_MARKER] = float(guardian_phantoms)
            self._skill_burst_attach_summon_state(active_attrs, summon_active_until, current)
            planning_attrs = active_attrs
            if getattr(self, "_skill_burst_stable_external_rotation", False):
                planning_attrs = self._skill_burst_active_attrs(
                    support_actions,
                    support_expires_at,
                    current,
                    rotation_planning=True,
                )
                if self._skill_burst_uses_fury():
                    planning_attrs[SKILL_BURST_FURY_MARKER] = float(fury)
                if self._skill_burst_uses_guardian_phantoms():
                    planning_attrs[SKILL_BURST_GUARDIAN_PHANTOM_MARKER] = float(guardian_phantoms)
                self._skill_burst_attach_summon_state(
                    planning_attrs,
                    summon_active_until,
                    current,
                )
            snapshot_reservation_at = 0.0
            snapshot_refresh_due_now = False
            for snapshot_metric in candidates:
                snapshot_skill = self._skill_burst_skill_key(snapshot_metric.get("skill"))
                if not self._skill_burst_is_refreshable_periodic_damage(snapshot_skill):
                    continue
                active_until, previous_snapshot = self._skill_burst_refreshable_periodic_active_info(
                    segments, snapshot_skill, current
                )
                if active_until <= current + 1e-6:
                    continue
                boost_end = self._skill_burst_snapshot_boost_end(
                    support_actions, support_expires_at, current
                )
                snapshot_cast = self._skill_burst_scheduled_cast_time(
                    snapshot_skill, current, previous_skill, active_attrs
                )
                if boost_end > current + 1e-6:
                    snapshot_cast = max(
                        snapshot_cast,
                        self._skill_burst_scheduled_cast_time(
                            snapshot_skill, boost_end - 0.01, previous_skill, active_attrs
                        ),
                    )
                reserve_at = boost_end - snapshot_cast - 0.01
                # A cast may start immediately before a Boss stop interval and
                # finish inside it. Reserve that last legal start when the
                # snapshot boost would otherwise expire during the stop.
                next_block_start = self._skill_burst_next_block_start(current, window)
                if next_block_start < boost_end - 1e-6:
                    reserve_at = min(reserve_at, next_block_start - 0.01)
                reserve_attrs = self._skill_burst_active_attrs(
                    support_actions,
                    support_expires_at,
                    max(current, reserve_at),
                )
                reserved_snapshot = (
                    self._skill_burst_refreshable_periodic_snapshot_attack(
                        snapshot_metric,
                        reserve_attrs,
                    )
                )
                expected_next_snapshot = (
                    self._skill_burst_refreshable_periodic_expected_next_attack(
                        snapshot_metric,
                        support_actions,
                        support_expires_at,
                        active_until,
                        window,
                    )
                )
                previous_cast_start = max(
                    (
                        as_number(row.get("start"))
                        for row in segments
                        if self._skill_burst_skill_key(row.get("skill")) == snapshot_skill
                    ),
                    default=current,
                )
                natural_ready_at = self._skill_burst_cooldown_ready_at(
                    snapshot_skill,
                    as_number(snapshot_metric.get("base_cooldown")),
                    previous_cast_start,
                    active_attrs,
                )
                if (
                    snapshot_cast > 0
                    and boost_end > current + snapshot_cast + 1e-6
                    and boost_end < active_until - 1e-6
                    and min(
                        damage_available_at.get(snapshot_skill, window + 1.0),
                        natural_ready_at,
                    ) <= reserve_at + 1e-6
                    and reserved_snapshot >= previous_snapshot * 0.995
                    and reserved_snapshot > expected_next_snapshot * 1.001
                ):
                    if reserve_at <= current + 0.05:
                        snapshot_refresh_due_now = True
                    else:
                        snapshot_reservation_at = (
                            reserve_at
                            if snapshot_reservation_at <= 0
                            else min(snapshot_reservation_at, reserve_at)
                        )
            for action_index, snapshot_action in enumerate(support_actions):
                snapshot_skill = self._skill_burst_skill_key(snapshot_action.get("skill"))
                if not self._skill_burst_is_refreshable_periodic_damage(snapshot_skill):
                    continue
                active_until = max(0.0, as_number(support_expires_at.get(action_index)))
                boost_end = self._skill_burst_snapshot_boost_end(
                    support_actions, support_expires_at, current
                )
                snapshot_cast = self._skill_burst_support_cast_time(
                    snapshot_action, current, active_attrs
                )
                reserve_at = boost_end - snapshot_cast - 0.01
                if (
                    active_until > boost_end + 1e-6
                    and boost_end > current + snapshot_cast + 1e-6
                    and support_available_at.get(action_index, window + 1.0) <= reserve_at + 1e-6
                    and display_value(active_attrs, "__爆发buff__神圣化身") > 0
                ):
                    if reserve_at <= current + 0.05:
                        snapshot_refresh_due_now = True
                    else:
                        snapshot_reservation_at = (
                            reserve_at
                            if snapshot_reservation_at <= 0
                            else min(snapshot_reservation_at, reserve_at)
                        )
            for skill_key, max_charges in charged_skill_maxes.items():
                next_recharge = charged_skill_recharge_at.get(skill_key)
                while (
                    next_recharge is not None
                    and next_recharge <= current + 1e-6
                    and charged_skill_charges.get(skill_key, 0) < max_charges
                ):
                    charged_skill_charges[skill_key] = min(max_charges, charged_skill_charges.get(skill_key, 0) + 1)
                    if charged_skill_charges[skill_key] >= max_charges:
                        next_recharge = None
                        break
                    recharge_interval = self._skill_burst_effective_cooldown(
                        skill_key,
                        damage_base_cooldown_by_skill.get(skill_key, 0.0),
                        next_recharge,
                        active_attrs,
                    )
                    next_recharge = next_recharge + recharge_interval if recharge_interval > 0 else None
                charged_skill_recharge_at[skill_key] = next_recharge
                if charged_skill_charges.get(skill_key, 0) > 0:
                    damage_available_at[skill_key] = min(damage_available_at.get(skill_key, current), current)
            for source_skill in list(passive_cooldown_next_at):
                trigger_guard = 0
                while passive_cooldown_next_at.get(source_skill, window + 1.0) <= current + 1e-6 and trigger_guard < 20:
                    trigger_at = passive_cooldown_next_at[source_skill]
                    self._skill_burst_apply_cooldown_rule_source(
                        source_skill,
                        trigger_at,
                        damage_available_at,
                        damage_base_cooldown_by_skill,
                        active_attrs,
                        support_actions,
                        support_available_at,
                    )
                    interval = self._skill_burst_passive_cooldown_source_interval(
                        source_skill, trigger_at, active_attrs
                    )
                    if interval <= 0:
                        passive_cooldown_next_at[source_skill] = window + 1.0
                        break
                    passive_cooldown_next_at[source_skill] = trigger_at + interval
                    trigger_guard += 1
            active_freeze_layers = len({
                self._skill_burst_freeze_source(action)
                for index, action in enumerate(support_actions)
                if self._skill_burst_action_has_freeze(action)
                and support_expires_at.get(index, 0.0) > current + 1e-6
            })
            forced_combo_ready = self._skill_burst_has_ready_combo_followup(
                previous_skill,
                candidates,
                damage_available_at,
                current,
            )
            forced_support_followups = self._skill_burst_forced_combo_followups(previous_skill) & {
                self._skill_burst_skill_key(action.get("skill"))
                for action in support_actions
            }
            # Damage rows and support/debuff rows share one combo graph.  A
            # skill such as 旋风斩 is present in both groups; deciding support
            # first used to bypass ``combos.priority`` and could turn
            # 重锤→半月斩→旋风斩 into 重锤→旋风斩 unconditionally.  Resolve the
            # next ready follow-up across both groups before either scheduler
            # gets to consume it.
            combo_priority = SKILL_BURST_COMBO_PRIORITY_BY_JOB.get(
                norm(self.second_job.currentText()), {}
            ).get(self._skill_burst_skill_key(previous_skill), ())
            if combo_priority:
                forced_followups = self._skill_burst_forced_combo_followups(previous_skill)
                ready_damage_followups = {
                    self._skill_burst_skill_key(metric.get("skill"))
                    for metric in candidates
                    if self._skill_burst_skill_key(metric.get("skill")) in forced_followups
                    and damage_available_at.get(
                        self._skill_burst_skill_key(metric.get("skill")), 0.0
                    ) <= current + 1e-6
                    and self._skill_burst_predecessor_satisfied(
                        previous_skill, metric.get("skill", "")
                    )
                    and self._skill_burst_state_requirement_satisfied(
                        metric.get("skill", ""), active_attrs
                    )
                }
                ready_support_followups = {
                    self._skill_burst_skill_key(action.get("skill"))
                    for index, action in enumerate(support_actions)
                    if not action.get("triggered_only")
                    and self._skill_burst_skill_key(action.get("skill")) in forced_followups
                    and support_available_at.get(index, 0.0) <= current + 1e-6
                    and self._skill_burst_predecessor_satisfied(
                        previous_skill, action.get("skill", "")
                    )
                }
                ready_followups = ready_damage_followups | ready_support_followups
                preferred_followup = next(
                    (
                        self._skill_burst_skill_key(skill)
                        for skill in combo_priority
                        if self._skill_burst_skill_key(skill) in ready_followups
                    ),
                    "",
                )
                if preferred_followup:
                    forced_combo_ready = preferred_followup in ready_damage_followups
                    forced_support_followups &= {preferred_followup}
            best_damage_dpa = 0.0
            best_damage_cast_time = 0.0
            longest_ready_damage_cast = 0.0
            for metric in candidates:
                skill_key = self._skill_burst_skill_key(metric.get("skill"))
                if damage_available_at.get(skill_key, 0.0) > current + 1e-6:
                    continue
                resistance_until = (
                    self._skill_burst_acceleration_resistance_active_until(
                        skill_key, current
                    )
                )
                if resistance_until > current + 1e-6:
                    damage_available_at[skill_key] = max(
                        damage_available_at.get(skill_key, 0.0),
                        resistance_until,
                    )
                    continue
                if not self._skill_burst_predecessor_satisfied(previous_skill, metric.get("skill", "")):
                    continue
                if not self._skill_burst_state_requirement_satisfied(metric.get("skill", ""), active_attrs):
                    continue
                summon_limit = self._skill_burst_summon_limit(skill_key)
                if summon_limit and len(summon_active_until.get(skill_key, [])) >= summon_limit[0]:
                    continue
                if self._skill_burst_shared_summon_limit_reached(skill_key, summon_active_until, current):
                    continue
                cast_time = self._skill_burst_scheduled_cast_time(
                    metric.get("skill", ""), current, previous_skill, active_attrs
                )
                if current + cast_time > action_window_end + 1e-6:
                    continue
                if not self._skill_burst_required_followup_ready(
                    metric.get("skill", ""),
                    candidates,
                    damage_available_at,
                    current + max(0.0, cast_time),
                    action_window_end,
                    active_attrs,
                ):
                    continue
                if cast_time > 0:
                    longest_ready_damage_cast = max(longest_ready_damage_cast, cast_time)
                    candidate_dpa = self._skill_burst_window_metric_score(
                        metric,
                        self._skill_burst_attrs_for_cast(planning_attrs, metric.get("skill")),
                        current,
                        window,
                        cast_time,
                    ) / cast_time
                    if candidate_dpa > best_damage_dpa + 1e-9:
                        best_damage_dpa = candidate_dpa
                        best_damage_cast_time = cast_time
            ready_support = []
            summon_refresh_support = []
            opening_support = self._skill_burst_next_opening_support(
                support_actions,
                used_skill_keys,
                support_available_at,
                current,
            )
            for index, action in enumerate(support_actions):
                action_key = self._skill_burst_skill_key(action.get("skill"))
                if action.get("triggered_only"):
                    continue
                # Once the opener has started, complete the pair before any
                # other upkeep/pinned support can interrupt it.
                if (self._skill_burst_skill_key(previous_skill) == "九天之舞"
                        and action_key != "九天之舞2阶"):
                    continue
                if (action_key == "九天之舞" and
                        self._skill_burst_dance_pair_ready_at(
                            action_key, support_actions, support_available_at
                        ) > current + 1e-6):
                    continue
                resistance_until = (
                    self._skill_burst_acceleration_resistance_active_until(
                        action_key, current
                    )
                )
                if resistance_until > current + 1e-6:
                    support_available_at[index] = max(
                        support_available_at.get(index, 0.0),
                        resistance_until,
                    )
                    if action_key in damage_available_at:
                        damage_available_at[action_key] = max(
                            damage_available_at.get(action_key, 0.0),
                            resistance_until,
                        )
                    continue
                if snapshot_refresh_due_now and not self._skill_burst_is_refreshable_periodic_damage(action_key):
                    continue
                cast_time = self._skill_burst_support_cast_time(action, current, active_attrs)
                expires_at = support_expires_at.get(index, 0.0)
                mandatory_due = bool(
                    action.get("mandatory_uptime")
                    and expires_at <= current + max(0.0, cast_time) + longest_ready_damage_cast + 1e-6
                )
                pinned_schedule_due = bool(
                    action.get("pinned_cast_schedule")
                    and support_available_at.get(index, 0.0) <= current + 1e-6
                )
                # A required/forced follow-up is one atomic combo package.
                # Even a mandatory-upkeep buff must wait until the follow-up
                # finishes; inserting it here invalidates the shortened cast
                # and can also make the follow-up unavailable altogether.
                if (
                    forced_combo_ready
                    and not forced_support_followups
                    and not pinned_schedule_due
                ):
                    continue
                if support_available_at.get(index, 0.0) > current + 1e-6:
                    continue
                if (
                    not action.get("pinned_cast_schedule")
                    and not action.get("stackable_summon")
                    and max(0.0, as_number(action.get("duration"))) > 0
                ):
                    starts_on_cast = self._skill_burst_action_starts_on_cast(action)
                    effect_at = current if starts_on_cast else current + cast_time
                    external_until = self._skill_burst_external_same_effect_active_until(
                        action_key, current
                    )
                    if external_until <= current + 1e-6:
                        external_until = self._skill_burst_external_same_effect_active_until(
                            action_key, effect_at
                        )
                    if external_until > effect_at + 1e-6:
                        # Same-name team buffs do not stack. Pre-cast a buff whose
                        # effect starts after the action, otherwise wait until the
                        # teammate's copy has actually expired.
                        delayed_start = (
                            external_until
                            if starts_on_cast
                            else max(current, external_until - cast_time)
                        )
                        if delayed_start > current + 0.05:
                            support_available_at[index] = max(
                                support_available_at.get(index, 0.0),
                                delayed_start,
                            )
                            continue
                state_dependents = {
                    self._skill_burst_skill_key(skill)
                    for skill in action.get("state_dependents", ())
                    if self._skill_burst_skill_key(skill)
                }
                if state_dependents and not opening_support and expires_at <= current + 1e-6:
                    support_dependent_skills = {
                        self._skill_burst_skill_key(other_action.get("skill"))
                        for other_index, other_action in enumerate(support_actions)
                        if other_index != index
                        and self._skill_burst_skill_key(other_action.get("skill")) in state_dependents
                    }
                    dependent_ready_times = [
                        max(current, as_number(damage_available_at.get(skill, current)))
                        for skill in state_dependents
                        if skill in damage_row_by_skill
                        and skill not in support_dependent_skills
                    ]
                    dependent_ready_times.extend(
                        max(
                            current,
                            as_number(support_available_at.get(other_index, current)),
                            as_number(support_expires_at.get(other_index, current))
                            - max(
                                0.0,
                                self._skill_burst_support_cast_time(
                                    other_action,
                                    current,
                                    active_attrs,
                                ),
                            ),
                        )
                        for other_index, other_action in enumerate(support_actions)
                        if other_index != index
                        and self._skill_burst_skill_key(other_action.get("skill")) in support_dependent_skills
                    )
                    next_dependent = min(dependent_ready_times, default=current)
                    delayed_start = max(current, next_dependent - max(0.0, cast_time))
                    expiry_key = (index, int(round(expires_at * 1000)))
                    allowed_damage_deferrals = max(
                        0,
                        int(as_number(action.get("defer_ready_damage_count"))),
                    )
                    if (
                        deferred_state_expiries.get(expiry_key, 0) < allowed_damage_deferrals
                        and best_damage_cast_time > 0
                    ):
                        deferred_state_expiries[expiry_key] = deferred_state_expiries.get(expiry_key, 0) + 1
                        support_available_at[index] = current + best_damage_cast_time
                        continue
                    if delayed_start > current + 0.05:
                        support_available_at[index] = delayed_start
                        continue
                if action.get("stackable_summon"):
                    # Each ready cast creates another independently expiring
                    # relic; an existing copy must not suppress the next one.
                    mandatory_due = True
                current_job = norm(self.second_job.currentText())
                if current_job in {"重炮手", "机械大师"} and action_key == "重磅鸭":
                    active_ducks = len(summon_active_until.get(action_key, []))
                    if active_ducks < self._skill_burst_heavy_duck_target_count():
                        mandatory_due = True
                if current_job in {"重炮手", "机械大师"} and action_key == "工程领域":
                    normal_summon_count = sum(
                        len(summon_active_until.get(skill, []))
                        for skill in SKILL_BURST_ENGINEER_SUMMONS
                    )
                    required_summons = 3 if current_job == "机械大师" else 1
                    if normal_summon_count < required_summons:
                        continue
                    if expires_at <= current + max(0.0, cast_time) + 1e-6:
                        mandatory_due = True
                if current_job == "机械大师":
                    normal_summon_count = sum(
                        len(summon_active_until.get(skill, []))
                        for skill in SKILL_BURST_ENGINEER_SUMMONS
                    )
                    mode_active = display_value(active_attrs, SKILL_BURST_MECHANIC_MODE_MARKER) > 0
                    field_active = display_value(active_attrs, SKILL_BURST_ENGINEERING_FIELD_MARKER) > 0
                    if action_key == "工程领域" and (mode_active or normal_summon_count < 2):
                        continue
                    if action_key == "机械模式" and (normal_summon_count < 3 or not field_active):
                        continue
                if (
                    current_job == "圣骑士"
                    and action_key == "挑衅"
                    and display_value(active_attrs, "__爆发buff__神圣化身") > 0
                ):
                    # 挑衅在化身前预铺；化身期间优先打伤害，结束后再补仇恨。
                    continue
                if forced_support_followups and action_key not in forced_support_followups and not mandatory_due:
                    continue
                is_opening_support = bool(
                    opening_support and action_key == opening_support
                )
                if opening_support and not is_opening_support and not pinned_schedule_due:
                    action_attrs = action.get("attrs", {})
                    setup_candidate = bool(
                        new_scheme_1
                        and action.get("periodic_buff")
                        and action_attrs
                        and not (
                            isinstance(action.get("metric"), dict)
                            and max(
                                0.0,
                                as_number(action.get("metric", {}).get("effective_score")),
                                as_number(action.get("effective_score")),
                            )
                            > 1e-6
                        )
                        and not action.get("state_only")
                        and not action.get("state_dependents")
                        and not any(
                            norm(key) in {"冷却缩减%", "动作速度%"}
                            and as_number(value) > 0
                            for key, value in action_attrs.items()
                        )
                    )
                    if not setup_candidate:
                        continue
                if not self._skill_burst_predecessor_satisfied(previous_skill, action.get("skill", "")):
                    continue
                if not self._skill_burst_state_requirement_satisfied(action.get("skill", ""), active_attrs):
                    continue
                if cast_time <= 0:
                    continue
                summon_refresh_fallback = False
                if self._skill_burst_summon_capacity_full(
                    action_key,
                    summon_active_until,
                    current,
                ):
                    refresh_ready_at = self._skill_burst_summon_refresh_ready_at(
                        action_key,
                        summon_active_until,
                        current,
                        cast_time,
                    )
                    if refresh_ready_at is None or refresh_ready_at > current + 1e-6:
                        continue
                    summon_refresh_fallback = True
                    mandatory_due = False
                if (
                    current + cast_time > action_window_end + 1e-6
                    and not (action.get("continuous_uptime") and mandatory_due)
                ):
                    continue
                if (
                    snapshot_reservation_at > current + 1e-6
                    and current + cast_time > snapshot_reservation_at + 1e-6
                    and not self._skill_burst_is_refreshable_periodic_damage(action_key)
                ):
                    continue
                if action_key == "九天之舞" and not self._skill_burst_dance_pair_fits(
                    support_actions, support_available_at, current,
                    current + cast_time, min(
                        action_window_end,
                        self._skill_burst_next_block_start(current, window),
                        snapshot_reservation_at if snapshot_reservation_at > current + 1e-6 else window,
                    ),
                    active_attrs,
                ):
                    continue
                if action_key != "九天之舞" and not self._skill_burst_required_followup_ready(
                    action.get("skill", ""),
                    candidates,
                    damage_available_at,
                    current + cast_time,
                    action_window_end,
                    active_attrs,
                ):
                    continue
                apply_at = current + cast_time
                refresh_judgement = self._skill_burst_should_refresh_judgement_snapshot(
                    action,
                    expires_at,
                    apply_at,
                    active_attrs,
                )
                refresh_periodic_snapshot = bool(
                    snapshot_refresh_due_now
                    and self._skill_burst_is_refreshable_periodic_damage(action_key)
                )
                action_metric = action.get("metric")
                damaging_effect_recast = bool(
                    expires_at > apply_at + 1e-6
                    and isinstance(action_metric, dict)
                    and max(
                        0.0,
                        as_number(action_metric.get("effective_score")),
                        as_number(action.get("effective_score")),
                    )
                    > 1e-6
                    and not action.get("non_damaging_support")
                    and not action.get("periodic_buff")
                    and not action.get("stackable_summon")
                )
                if refresh_judgement and expires_at > apply_at + 1e-6:
                    boost_end = self._skill_burst_snapshot_boost_end(
                        support_actions,
                        support_expires_at,
                        current,
                        exclude_skill=action.get("skill", ""),
                    )
                    refresh_deadline = min(
                        value
                        for value in (expires_at, boost_end)
                        if value > current + cast_time + 1e-6
                    ) if any(
                        value > current + cast_time + 1e-6
                        for value in (expires_at, boost_end)
                    ) else 0.0
                    delayed_start = max(current, refresh_deadline - cast_time - 0.01)
                    if delayed_start > current + 0.05:
                        support_available_at[index] = max(
                            support_available_at.get(index, 0.0),
                            delayed_start,
                        )
                        continue
                if (
                    expires_at > apply_at + 1e-6
                    and not refresh_judgement
                    and not refresh_periodic_snapshot
                    and not mandatory_due
                    and not action.get("stackable_summon")
                    and not damaging_effect_recast
                ):
                    continue
                if self._skill_burst_action_has_freeze(action):
                    active_sources = {
                        self._skill_burst_freeze_source(active_action)
                        for active_index, active_action in enumerate(support_actions)
                        if active_index != index
                        and self._skill_burst_action_has_freeze(active_action)
                        and support_expires_at.get(active_index, 0.0) > apply_at + 1e-6
                    }
                    action_source = self._skill_burst_freeze_source(action)
                    if len(active_sources) >= 2 and action_source not in active_sources:
                        continue
                if self._skill_burst_action_blocked_by_active_resist(action, support_actions, support_expires_at, apply_at):
                    continue
                if (
                    not new_scheme_1
                    and
                    not mandatory_due
                    and
                    self._skill_burst_action_is_low_priority_support(action)
                    and self._skill_burst_has_ready_combo_followup(previous_skill, candidates, damage_available_at, current)
                ):
                    continue
                new_scheme_mandatory = bool(
                    mandatory_due
                    or pinned_schedule_due
                    or refresh_periodic_snapshot
                    or is_opening_support
                    or refresh_judgement
                )
                support_dpa = (
                    self._skill_burst_support_opportunity_dpa(
                        action,
                        current,
                        active_attrs,
                        candidates,
                        window,
                    )
                    if not new_scheme_1 or new_scheme_mandatory
                    else 0.0
                )
                if refresh_judgement:
                    support_dpa = max(support_dpa, best_damage_dpa * 1.25)
                minimum_ratio = (
                    1.0
                    if action_key in self._skill_burst_optional_combo_followups(previous_skill)
                    else 0.85
                )
                if (
                    not new_scheme_1
                    and not mandatory_due
                    and not opening_support
                    and not forced_support_followups
                    and best_damage_dpa > 0
                    and support_dpa < best_damage_dpa * minimum_ratio
                ):
                    continue
                support_item = (
                    0
                    if (
                        mandatory_due
                        or pinned_schedule_due
                        or refresh_periodic_snapshot
                        or is_opening_support
                        or (new_scheme_1 and refresh_judgement)
                    )
                    else 1,
                    -self._skill_burst_resist_priority(action),
                    -support_dpa,
                    action.get("order", index),
                    index,
                    action,
                )
                (
                    summon_refresh_support
                    if summon_refresh_fallback
                    else ready_support
                ).append(support_item)
            if (
                not ready_support
                and best_damage_dpa <= 1e-9
                and summon_refresh_support
            ):
                ready_support = summon_refresh_support
            selected_support = None
            trace = getattr(self, "_skill_burst_new_scheme_1_trace", None)
            traced_support_skills = {
                self._skill_burst_skill_key(skill)
                for skill in getattr(
                    self,
                    "_skill_burst_new_scheme_1_trace_support_skills",
                    (),
                )
                if self._skill_burst_skill_key(skill)
            }
            if isinstance(trace, list) and traced_support_skills:
                trace.append({
                    "type": "support_state",
                    "time": round(current, 5),
                    "actions": [
                        {
                            "skill": self._skill_burst_skill_key(action.get("skill")),
                            "available": round(
                                as_number(support_available_at.get(index)),
                                5,
                            ),
                            "expires": round(
                                as_number(support_expires_at.get(index)),
                                5,
                            ),
                            "ready": any(
                                int(item[4]) == index for item in ready_support
                            ),
                        }
                        for index, action in enumerate(support_actions)
                        if self._skill_burst_skill_key(action.get("skill"))
                        in traced_support_skills
                    ],
                })
            if ready_support:
                selected_support = (
                    ready_support[0]
                    if self._skill_burst_skill_key(previous_skill) == "九天之舞"
                    else
                    self._skill_burst_new_scheme_1_choose_support(
                        ready_support,
                        candidates,
                        support_actions,
                        support_available_at,
                        support_expires_at,
                        active_attrs,
                        damage_available_at,
                        current,
                        window,
                        previous_skill,
                        best_damage_dpa,
                    )
                    if new_scheme_1
                    else sorted(
                        ready_support,
                        key=lambda item: (item[0], item[1], item[2], item[3], item[4]),
                    )[0]
                )
            if selected_support is not None:
                _mandatory, _negative_resist, _negative_dpa, _order, support_index, action = selected_support
                action_key = self._skill_burst_skill_key(action.get("skill"))
                cast_time = self._skill_burst_support_cast_time(action, current, active_attrs)
                if cast_time > 0 and current < window - 1e-6:
                    start = current
                    end = current + cast_time
                    support_dpa = self._skill_burst_support_dpa(action, start, active_attrs, window)
                    metric = action.get("metric")
                    scoring_attrs = self._skill_burst_attrs_for_cast(
                        self._skill_burst_attrs_during_action(active_attrs, action),
                        action.get("skill"),
                    )
                    support_score = (
                        self._skill_burst_window_metric_score(metric, scoring_attrs, start, window, cast_time)
                        if isinstance(metric, dict)
                        else max(0.0, as_number(action.get("effective_score")))
                    )
                    support_raw_score = (
                        self._skill_burst_window_raw_metric_score(metric, scoring_attrs, start, window, cast_time)
                        if isinstance(metric, dict)
                        else 0.0
                    )
                    periodic_metric = None
                    if (
                        isinstance(metric, dict)
                        and self._skill_periodic_total_damage_cycle(action.get("skill", "")) > 0
                    ):
                        periodic_metric = copy.deepcopy(metric)
                    segments.append({
                        "start": start,
                        "end": end,
                        "skill": action.get("skill", ""),
                        "type": action.get("type", "前置"),
                        "kind": "support",
                        "dpa": support_dpa,
                        "effective_score": support_score,
                        "raw_effective_score": support_raw_score,
                        "channel_scores": self._skill_burst_scaled_channel_scores(metric, support_raw_score),
                        "periodic_metric": periodic_metric,
                        "element": norm((action.get("metric") or {}).get("element")) if isinstance(action.get("metric"), dict) else "",
                        "cdr": self._skill_burst_sim_cdr_percent_at(start, active_attrs),
                    })
                    if action_key == "机械模式":
                        moment_index = next(
                            (
                                index for index, other in enumerate(support_actions)
                                if self._skill_burst_skill_key(other.get("skill")) == "机械时刻"
                            ),
                            None,
                        )
                        segments[-1]["mechanic_moment_triggered"] = bool(
                            moment_index is not None
                            and support_available_at.get(moment_index, 0.0) <= start + 1e-6
                        )
                    self._skill_burst_apply_shared_skill_use(
                        action.get("skill", ""),
                        start,
                        as_number(action.get("damage_base_cooldown"), action.get("base_cooldown")),
                        support_actions,
                        support_available_at,
                        support_expires_at,
                        damage_available_at,
                        damage_row_by_skill,
                        damage_base_cooldown_by_skill,
                        active_attrs,
                        cast_end=end,
                        charged_counts=charged_skill_charges,
                        charged_maxes=charged_skill_maxes,
                        charged_recharge_at=charged_skill_recharge_at,
                    )
                    if action_key in charged_skill_charges:
                        # Charged skills can also enter through their support or
                        # debuff action. That cast consumes the same stock as the
                        # damage candidate; otherwise the opening support cast is
                        # followed by every initial damage charge as an extra cast.
                        if charged_skill_charges.get(action_key, 0) > 0:
                            charged_skill_charges[action_key] -= 1
                        charge_base_cooldown = max(
                            as_number(action.get("damage_base_cooldown")),
                            as_number(action.get("base_cooldown")),
                            as_number((action.get("metric") or {}).get("base_cooldown"))
                            if isinstance(action.get("metric"), dict)
                            else 0.0,
                        )
                        charge_cooldown = self._skill_burst_effective_cooldown(
                            action.get("skill", ""),
                            charge_base_cooldown,
                            start,
                            active_attrs,
                        )
                        if (
                            charged_skill_recharge_at.get(action_key) is None
                            and charged_skill_charges.get(action_key, 0)
                            < charged_skill_maxes.get(action_key, 0)
                            and charge_cooldown > 0
                        ):
                            charged_skill_recharge_at[action_key] = (
                                start + charge_cooldown
                            )
                        next_charge = (
                            start
                            if charged_skill_charges.get(action_key, 0) > 0
                            else as_number(
                                charged_skill_recharge_at.get(action_key),
                                start + max(0.0, charge_cooldown),
                            )
                        )
                        damage_available_at[action_key] = next_charge
                        support_available_at[support_index] = next_charge
                    burning_triggers = self._skill_burst_cast_driven_passive_trigger_count(
                        action.get("skill", ""),
                        start,
                    )
                    if burning_triggers > 1e-9:
                        self._skill_burst_apply_cooldown_rule_source(
                            "灼烧之手",
                            end,
                            damage_available_at,
                            damage_base_cooldown_by_skill,
                            active_attrs,
                            support_actions,
                            support_available_at,
                            trigger_count=burning_triggers,
                        )
                    fury, _boosted = self._skill_burst_apply_fury_use(fury, action.get("skill", ""))
                    if self._skill_burst_fury_gain(action.get("skill", "")) > 0:
                        fury_expires_at = end + 60.0
                    guardian_phantoms, _guardian_used = self._skill_burst_apply_guardian_phantom_use(
                        guardian_phantoms,
                        action.get("skill", ""),
                    )
                    action_key = self._skill_burst_skill_key(action.get("skill"))
                    used_skill_keys.add(action_key)
                    summon_limit = self._skill_burst_summon_limit(action_key)
                    if summon_limit:
                        removed_summons = self._skill_burst_register_summon(
                            action_key,
                            summon_active_until,
                            end + summon_limit[1],
                            end,
                        )
                        if removed_summons:
                            segments[-1]["detail"] = "空档提前续召，替换最早召唤物"
                    previous_skill = norm(action.get("skill", ""))
                    current = end
                    continue
                support_available_at[support_index] = window

            ready: list[tuple[float, float, float, float, dict[str, Any]]] = []
            summon_refresh_ready: list[tuple[float, float, float, float, dict[str, Any]]] = []
            ammo_blocked: list[tuple[float, float, float, float, dict[str, Any]]] = []
            for index, metric in enumerate(candidates):
                row_key = self._skill_burst_skill_key(metric.get("skill"))
                if not (row_key in charged_skill_charges and charged_skill_charges.get(row_key, 0) > 0) and damage_available_at.get(row_key, 0.0) > current + 1e-6:
                    continue
                predecessor_ok = self._skill_burst_predecessor_satisfied(previous_skill, metric.get("skill", ""))
                if not predecessor_ok:
                    continue
                if not self._skill_burst_state_requirement_satisfied(metric.get("skill", ""), active_attrs):
                    continue
                summon_capacity_full = self._skill_burst_summon_capacity_full(
                    row_key,
                    summon_active_until,
                    current,
                )
                cast_time = self._skill_burst_scheduled_cast_time(
                    metric.get("skill", ""), current, previous_skill, active_attrs
                )
                if cast_time <= 0:
                    continue
                package_reservation_at = self._skill_burst_dpa_package_reservation_at(
                    metric,
                    candidates,
                    damage_available_at,
                    active_attrs,
                    current,
                    action_window_end,
                    cast_time,
                )
                if (
                    package_reservation_at is not None
                    and package_reservation_at > current + 1e-6
                ):
                    damage_available_at[row_key] = max(
                        as_number(damage_available_at.get(row_key)),
                        package_reservation_at,
                    )
                    continue
                if summon_capacity_full:
                    refresh_ready_at = self._skill_burst_summon_refresh_ready_at(
                        row_key,
                        summon_active_until,
                        current,
                        cast_time,
                    )
                    if refresh_ready_at is None or refresh_ready_at > current + 1e-6:
                        continue
                if current + cast_time > action_window_end + 1e-6:
                    continue
                if (
                    snapshot_reservation_at > current + 1e-6
                    and current + cast_time > snapshot_reservation_at + 1e-6
                    and not self._skill_burst_is_refreshable_periodic_damage(row_key)
                ):
                    continue
                snapshot_refresh_due = False
                if self._skill_burst_is_refreshable_periodic_damage(metric.get("skill", "")):
                    had_previous_periodic_cast = any(
                        self._skill_burst_skill_key(segment.get("skill")) == row_key
                        for segment in reversed(segments)
                    )
                    active_until, previous_snapshot = self._skill_burst_refreshable_periodic_active_info(
                        segments,
                        metric.get("skill", ""),
                        current,
                    )
                    if active_until > current + 1e-6:
                        current_snapshot = (
                            self._skill_burst_refreshable_periodic_snapshot_attack(
                                metric,
                                active_attrs,
                            )
                        )
                        boost_end = self._skill_burst_snapshot_boost_end(
                            support_actions,
                            support_expires_at,
                            current,
                        )
                        expected_next_snapshot = (
                            self._skill_burst_refreshable_periodic_expected_next_attack(
                                metric,
                                support_actions,
                                support_expires_at,
                                active_until,
                                window,
                            )
                        )
                        can_extend_high_snapshot = bool(
                            boost_end > current + cast_time + 1e-6
                            and boost_end < active_until - 1e-6
                            and current_snapshot >= previous_snapshot * 0.995
                            and current_snapshot > expected_next_snapshot * 1.001
                        )
                        if current_snapshot <= previous_snapshot * 1.001 and not can_extend_high_snapshot:
                            damage_available_at[row_key] = max(
                                damage_available_at.get(row_key, 0.0),
                                max(current, active_until - cast_time),
                            )
                            continue
                        # The running effect is the first deadline. Missing it
                        # by a few milliseconds must not defer the refresh to a
                        # much later snapshot-buff boundary.
                        delayed_start = (
                            self._skill_burst_refreshable_periodic_delayed_start(
                                metric.get("skill", ""),
                                current,
                                cast_time,
                                previous_skill,
                                active_attrs,
                                active_until,
                                boost_end,
                            )
                        )
                        next_block_start = self._skill_burst_next_block_start(
                            current,
                            window,
                        )
                        if next_block_start < boost_end - 1e-6:
                            delayed_start = min(
                                delayed_start,
                                max(current, next_block_start - 0.01),
                            )
                        if delayed_start > current + 0.05:
                            damage_available_at[row_key] = max(
                                damage_available_at.get(row_key, 0.0),
                                delayed_start,
                            )
                            continue
                        snapshot_refresh_due = can_extend_high_snapshot or current_snapshot > previous_snapshot * 1.001
                    elif had_previous_periodic_cast:
                        # Once a refreshable periodic effect has expired, every
                        # further action delays all of its pulses. Treat the
                        # ready recast as an uptime deadline instead of letting
                        # ordinary short-CD skills starve it indefinitely.
                        # The first cast still follows the normal opening and
                        # snapshot setup rules (for example Avatar first).
                        snapshot_refresh_due = True
                if not self._skill_burst_required_followup_ready(
                    metric.get("skill", ""),
                    candidates,
                    damage_available_at,
                    current + cast_time,
                    action_window_end,
                    active_attrs,
                ):
                    continue
                cast_attrs = self._skill_burst_attrs_for_cast(planning_attrs, metric.get("skill"))
                score = self._skill_burst_window_metric_score(
                    metric,
                    cast_attrs,
                    current,
                    window,
                    cast_time,
                )
                dpa = score / cast_time
                if new_scheme_1:
                    adjusted_dpa, wait_until = self._skill_burst_new_scheme_1_opportunity_score(
                        metric,
                        candidates,
                        damage_available_at,
                        damage_base_cooldown_by_skill,
                        support_actions,
                        support_available_at,
                        support_expires_at,
                        planning_attrs,
                        current,
                        window,
                        cast_time,
                        dpa,
                        score,
                        charged_skill_charges,
                        charged_skill_maxes,
                    )
                    trace = getattr(self, "_skill_burst_new_scheme_1_trace", None)
                    if isinstance(trace, list):
                        trace.append({
                            "type": "opportunity",
                            "time": round(current, 5),
                            "skill": row_key,
                            "direct": dpa,
                            "adjusted": adjusted_dpa,
                            "wait_until": wait_until,
                            "ready_during_cast": sorted(
                                (
                                    self._skill_burst_skill_key(other.get("skill")),
                                    round(as_number(damage_available_at.get(
                                        self._skill_burst_skill_key(other.get("skill"))
                                    )), 5),
                                )
                                for other in candidates
                                if current + 1e-6
                                < as_number(damage_available_at.get(
                                    self._skill_burst_skill_key(other.get("skill"))
                                ))
                                < current + cast_time - 1e-6
                            ),
                        })
                    baseline_rank = {
                        "policy": "new_scheme_1",
                        "direct_dpa": dpa,
                        "opportunity_dpa": adjusted_dpa,
                    }
                else:
                    adjusted_dpa, wait_until, baseline_rank = self._skill_burst_baseline_damage_rank(
                        metric,
                        candidates,
                        damage_available_at,
                        damage_base_cooldown_by_skill,
                        support_actions,
                        support_available_at,
                        support_expires_at,
                        planning_attrs,
                        current,
                        window,
                        cast_time,
                        dpa,
                        score,
                        previous_skill,
                    )
                if snapshot_refresh_due:
                    # Reserve the final cast slot before a high-panel snapshot
                    # disappears; ordinary DPA competition must not push it past
                    # the avatar/STR-INT boost boundary.
                    adjusted_dpa *= 1000.0
                item = (adjusted_dpa, dpa, -cast_time, wait_until, metric)
                if getattr(self, "_skill_burst_collect_baseline_diagnostics", False):
                    metric["baseline_rank"] = baseline_rank
                else:
                    metric.pop("baseline_rank", None)
                ammo_cost = self._skill_burst_ammo_cost(row_key)
                if ammo_cost > ammo:
                    ammo_blocked.append(item)
                    continue
                if summon_capacity_full:
                    summon_refresh_ready.append(item)
                else:
                    ready.append(item)
            if reload_skill and reload_base_cooldown > 0 and ammo < ammo_max and reload_available_at <= current + 1e-6:
                reload_metric = metrics_by_skill.get(
                    self._skill_burst_skill_key(reload_skill)
                )
                cast_time = self._skill_burst_reload_cast_time(current, active_attrs)
                if cast_time > 0 and current + cast_time <= action_window_end + 1e-6:
                    start = current
                    end = current + cast_time
                    ammo = min(ammo_max, ammo + ammo_gain)
                    damage_values = self._skill_burst_reload_damage_values(
                        reload_metric, active_attrs, start, window, cast_time
                    )
                    reload_cooldown = self._skill_burst_effective_cooldown(
                        reload_skill, reload_base_cooldown, start, active_attrs
                    )
                    reload_available_at = start + max(0.0, reload_cooldown)
                    segments.append({
                        "start": start,
                        "end": end,
                        "skill": reload_skill,
                        "type": "上弹/伤害",
                        "kind": "ammo",
                        **damage_values,
                        "cdr": self._skill_burst_sim_cdr_percent_at(start, active_attrs),
                        "detail": f"弹药 {ammo}/{ammo_max}",
                    })
                    previous_skill = reload_skill
                    current = end
                    continue
            if not ready and summon_refresh_ready:
                # Re-summoning a still-active instance is an idle-slot action,
                # never a competitor to an ordinary ready damage skill.
                ready = summon_refresh_ready
            if not ready:
                future_times = []
                if summon_removal_cursor < len(summon_removal_schedule):
                    next_removal = summon_removal_schedule[summon_removal_cursor][0]
                    if next_removal > current + 1e-6:
                        future_times.append(next_removal)
                if action_window_end > current + 1e-6:
                    future_times.append(action_window_end)
                if snapshot_reservation_at > current + 1e-6:
                    future_times.append(snapshot_reservation_at)
                future_times.extend(time for time in damage_available_at.values() if time > current + 1e-6)
                future_times.extend(time for time in passive_cooldown_next_at.values() if time > current + 1e-6)
                future_times.extend(
                    time
                    for time in charged_skill_recharge_at.values()
                    if time is not None and time > current + 1e-6
                )
                future_times.extend(
                    expiry
                    for expiry_times in summon_active_until.values()
                    for expiry in expiry_times
                    if expiry > current + 1e-6
                )
                for metric in candidates:
                    skill_key = self._skill_burst_skill_key(metric.get("skill"))
                    if not self._skill_burst_summon_capacity_full(
                        skill_key,
                        summon_active_until,
                        current,
                    ):
                        continue
                    cast_time = self._skill_burst_scheduled_cast_time(
                        metric.get("skill", ""),
                        current,
                        previous_skill,
                        active_attrs,
                    )
                    refresh_at = self._skill_burst_summon_refresh_ready_at(
                        skill_key,
                        summon_active_until,
                        current,
                        cast_time,
                    )
                    if refresh_at is not None:
                        next_summon = max(
                            refresh_at,
                            as_number(damage_available_at.get(skill_key)),
                        )
                        if next_summon > current + 1e-6:
                            future_times.append(next_summon)
                if ammo_blocked and reload_skill and reload_base_cooldown > 0 and reload_available_at > current + 1e-6:
                    future_times.append(reload_available_at)
                for index, action in enumerate(support_actions):
                    support_cast = self._skill_burst_support_cast_time(action, current, active_attrs)
                    next_support = max(support_available_at.get(index, 0.0), support_expires_at.get(index, 0.0) - support_cast)
                    if next_support > current + 1e-6:
                        future_times.append(next_support)
                    action_key = self._skill_burst_skill_key(action.get("skill"))
                    if self._skill_burst_summon_capacity_full(
                        action_key,
                        summon_active_until,
                        current,
                    ):
                        refresh_at = self._skill_burst_summon_refresh_ready_at(
                            action_key,
                            summon_active_until,
                            current,
                            support_cast,
                        )
                        if refresh_at is not None:
                            next_refresh = max(
                                refresh_at,
                                as_number(support_available_at.get(index)),
                            )
                            if next_refresh > current + 1e-6:
                                future_times.append(next_refresh)
                next_ready = min(future_times or [window])
                if next_ready >= window - 1e-6:
                    break
                current = next_ready
                previous_skill = ""
                self._skill_burst_new_scheme_1_plan_queue = []
                continue
            # Some high-panel skills have a destructive side effect.  For 雷神,
            # 雷暴 clears all shock debuffs, so it is an idle-slot filler rather
            # than a competitor to any ordinary ready damage skill.
            ready = self._skill_burst_defer_idle_only_ready(ready)
            if norm(self.second_job.currentText()) == "机械大师":
                mode_active = display_value(active_attrs, SKILL_BURST_MECHANIC_MODE_MARKER) > 0
                normal_summon_count = sum(
                    len(summon_active_until.get(skill, []))
                    for skill in SKILL_BURST_ENGINEER_SUMMONS
                )
                if not mode_active and normal_summon_count < 3:
                    summon_ready = [
                        item for item in ready
                        if self._skill_burst_skill_key(item[4].get("skill")) in SKILL_BURST_ENGINEER_SUMMONS
                    ]
                    if summon_ready:
                        ready = summon_ready
            guardian_spenders = [
                item
                for item in ready
                if self._skill_burst_guardian_phantom_cost(item[4].get("skill", "")) > 0
            ]
            if guardian_spenders:
                # 幻影上限只有2层；满层后优先消费，避免生成技能持续浪费资源。
                ready = guardian_spenders
            if snapshot_refresh_due_now:
                refresh_ready = [
                    item
                    for item in ready
                    if self._skill_burst_is_refreshable_periodic_damage(item[4].get("skill", ""))
                ]
                if refresh_ready:
                    ready = refresh_ready
            followups = self._skill_burst_forced_combo_followups(previous_skill)
            if followups:
                combo_ready = [item for item in ready if self._skill_burst_skill_key(item[4].get("skill")) in followups]
                if combo_ready:
                    ready = combo_ready
                    priority = SKILL_BURST_COMBO_PRIORITY_BY_JOB.get(
                        norm(self.second_job.currentText()), {}
                    ).get(self._skill_burst_skill_key(previous_skill), ())
                    for preferred_skill in priority:
                        preferred_ready = [
                            item
                            for item in ready
                            if self._skill_burst_skill_key(item[4].get("skill"))
                            == self._skill_burst_skill_key(preferred_skill)
                        ]
                        if preferred_ready:
                            ready = preferred_ready
                            break
            if not followups:
                ready_deadlines = getattr(
                    self,
                    "_skill_burst_ready_deadlines_by_job",
                    SKILL_BURST_READY_DEADLINES_BY_JOB,
                )
                job_ready_deadlines = (
                    ready_deadlines.get(norm(self.second_job.currentText()), {})
                    if isinstance(ready_deadlines, dict)
                    else {}
                )
                overdue_ready = [
                    item
                    for item in ready
                    if (
                        self._skill_burst_skill_key(item[4].get("skill"))
                        in job_ready_deadlines
                        and current
                        - as_number(
                            damage_available_at.get(
                                self._skill_burst_skill_key(item[4].get("skill"))
                            )
                        )
                        >= max(
                            0.0,
                            as_number(
                                job_ready_deadlines.get(
                                    self._skill_burst_skill_key(item[4].get("skill"))
                                )
                            ),
                        )
                        - 1e-6
                    )
                ]
                if overdue_ready:
                    ready = overdue_ready
            if not new_scheme_1:
                primary_ready = [
                    item
                    for item in ready
                    if not item[4].get("timeline_filler")
                    and item[0] > 0
                ]
                if primary_ready:
                    ready = primary_ready
            if not new_scheme_1 and norm(self.second_job.currentText()) == "黑暗萨满":
                ready_keys = {
                    self._skill_burst_skill_key(item[4].get("skill"))
                    for item in ready
                }
                if {"束缚之爪", "闪电之爪"}.issubset(ready_keys):
                    # 束缚之爪会同时推进闪电之爪、幽灵之怒和复仇之手的
                    # 冷却；两者同时可用时先释放束缚，整体循环收益更高。
                    ready = [
                        item
                        for item in ready
                        if self._skill_burst_skill_key(item[4].get("skill")) == "束缚之爪"
                    ]
            optimizer_enabled = self._skill_burst_optimizer_profile()[0] > 0
            if new_scheme_1:
                ready = self._skill_burst_new_scheme_1_rank_ready(
                    ready,
                    candidates,
                    damage_available_at,
                    damage_base_cooldown_by_skill,
                    support_actions,
                    support_available_at,
                    support_expires_at,
                    planning_attrs,
                    current,
                    window,
                    previous_skill,
                    charged_skill_charges,
                    charged_skill_maxes,
                    charged_skill_recharge_at,
                    ammo,
                    fury,
                    guardian_phantoms,
                )
            else:
                ready = self._skill_burst_rank_ready_with_lookahead(
                    ready,
                    candidates,
                    damage_available_at,
                    damage_base_cooldown_by_skill,
                    support_actions,
                    support_available_at,
                    support_expires_at,
                    planning_attrs,
                    current,
                    window,
                    previous_skill,
                    charged_skill_charges,
                    ammo,
                )
            if not new_scheme_1 and norm(self.second_job.currentText()) == "月之领主" and not followups and not optimizer_enabled:
                highest_score = max((item[0] for item in ready), default=0.0)
                near_best = [item for item in ready if item[0] >= highest_score * 0.98 - 1e-9]
                if len(near_best) > 1:
                    # Entering a stance also commits the following left/right-key chain.
                    # When immediate gains are nearly tied, use the shorter action first
                    # so the fixed burst window does not lose a later high-value cast.
                    ready = near_best
                    ready.sort(reverse=True, key=lambda item: (item[2], item[0], item[1]))
                else:
                    ready.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
            else:
                ready.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
            adjusted_dpa, dpa, _negative_cast, wait_until, chosen = ready[0]
            if adjusted_dpa <= 0 and wait_until > current + 1e-6 and wait_until < window - 1e-6:
                current = wait_until
                previous_skill = ""
                self._skill_burst_new_scheme_1_plan_queue = []
                continue
            skill = norm(chosen.get("skill"))
            if self._skill_burst_skill_key(skill) in SKILL_BURST_SIM_PASSIVE_SUMMONS:
                row_key = self._skill_burst_skill_key(skill)
                damage_available_at[row_key] = window
                continue
            ammo_cost = self._skill_burst_ammo_cost(skill)
            if ammo_cost > 0:
                ammo = max(0, ammo - ammo_cost)
            cast_time = self._skill_burst_scheduled_cast_time(
                skill, current, previous_skill, active_attrs
            )
            start = current
            end = current + cast_time
            cast_attrs = self._skill_burst_attrs_for_cast(active_attrs, skill)
            matching_support = next(
                (
                    action
                    for action in support_actions
                    if self._skill_burst_skill_key(action.get("skill"))
                    == self._skill_burst_skill_key(skill)
                ),
                None,
            )
            if (
                matching_support is not None
                and self._skill_burst_action_starts_on_cast(matching_support)
            ):
                cast_attrs = self._skill_burst_attrs_during_action(
                    cast_attrs,
                    matching_support,
                )
            raw_window_score = self._skill_burst_window_raw_metric_score(
                chosen,
                cast_attrs,
                start,
                window,
                cast_time,
            )
            dpa = self._skill_burst_window_metric_score(
                chosen,
                cast_attrs,
                start,
                window,
                cast_time,
            ) / max(1e-9, cast_time)
            periodic_metric = None
            if self._skill_periodic_total_damage_cycle(skill) > 0:
                periodic_metric = copy.deepcopy(chosen)
            full_snapshot_score = (
                self._skill_burst_full_metric_score(chosen, cast_attrs, start)
                if self._skill_burst_is_refreshable_periodic_damage(skill)
                else 0.0
            )
            snapshot_attack_multiplier = (
                self._skill_burst_refreshable_periodic_snapshot_attack(
                    chosen,
                    cast_attrs,
                )
                if self._skill_burst_is_refreshable_periodic_damage(skill)
                else 0.0
            )
            baseline_diagnostics = (
                {"baseline_rank": copy.deepcopy(chosen.get("baseline_rank", {}))}
                if getattr(self, "_skill_burst_collect_baseline_diagnostics", False)
                else {}
            )
            segments.append({
                "start": start,
                "end": end,
                "skill": skill,
                "type": "伤害",
                "kind": "damage",
                "dpa": dpa,
                "effective_score": self._skill_burst_window_metric_score(
                    chosen,
                    cast_attrs,
                    start,
                    window,
                    cast_time,
                ),
                "raw_effective_score": raw_window_score,
                "channel_scores": self._skill_burst_scaled_channel_scores(chosen, raw_window_score),
                "snapshot_full_score": full_snapshot_score,
                "snapshot_attack_multiplier": snapshot_attack_multiplier,
                "periodic_metric": periodic_metric,
                "element": norm(chosen.get("element")),
                "cdr": self._skill_burst_sim_cdr_percent_at(start, active_attrs),
                "action_speed_percent": self._skill_burst_sim_action_speed_at(
                    start, cast_attrs, skill=skill
                ),
                "action_speed_output_multiplier": (
                    self._skill_burst_action_output_multiplier(
                        skill,
                        start,
                        cast_attrs,
                    )
                ),
                "detail": self._skill_burst_dynamic_detail(skill, start, active_attrs),
                "optimizer_sequence": tuple(chosen.get("optimizer_sequence", ())),
                "optimizer_dpa": max(0.0, as_number(chosen.get("optimizer_dpa"))),
                "new_scheme_1_sequence": tuple(chosen.get("new_scheme_1_sequence", ())),
                "new_scheme_1_rank": max(0.0, as_number(chosen.get("new_scheme_1_rank"))),
                **baseline_diagnostics,
            })
            cooldown = self._skill_burst_effective_cooldown(
                skill, as_number(chosen.get("base_cooldown")), start, active_attrs
            )
            segments[-1]["cooldown_ready_at"] = start + max(0.0, cooldown)
            row_key = self._skill_burst_skill_key(skill)
            if row_key in charged_skill_charges:
                if charged_skill_charges.get(row_key, 0) > 0:
                    charged_skill_charges[row_key] -= 1
                if charged_skill_recharge_at.get(row_key) is None and charged_skill_charges.get(row_key, 0) < charged_skill_maxes.get(row_key, 0) and cooldown > 0:
                    charged_skill_recharge_at[row_key] = start + cooldown
                damage_available_at[row_key] = start if charged_skill_charges.get(row_key, 0) > 0 else as_number(charged_skill_recharge_at.get(row_key), start + max(0.0, cooldown))
            else:
                damage_available_at[row_key] = start + max(0.0, cooldown)
            self._skill_burst_apply_shared_skill_use(
                skill,
                start,
                as_number(chosen.get("base_cooldown")),
                support_actions,
                support_available_at,
                support_expires_at,
                damage_available_at,
                damage_row_by_skill,
                damage_base_cooldown_by_skill,
                active_attrs,
                cast_end=end,
                charged_counts=charged_skill_charges,
                charged_maxes=charged_skill_maxes,
                charged_recharge_at=charged_skill_recharge_at,
            )
            burning_triggers = self._skill_burst_cast_driven_passive_trigger_count(
                skill,
                start,
            )
            if burning_triggers > 1e-9:
                self._skill_burst_apply_cooldown_rule_source(
                    "灼烧之手",
                    end,
                    damage_available_at,
                    damage_base_cooldown_by_skill,
                    active_attrs,
                    support_actions,
                    support_available_at,
                    trigger_count=burning_triggers,
                )
            # Shared cooldown rules (including an EX self refund) may change
            # the ready time calculated above. Keep diagnostics and hover
            # details aligned with the runtime state that the planner uses.
            if row_key in damage_available_at:
                segments[-1]["cooldown_ready_at"] = as_number(
                    damage_available_at.get(row_key),
                    segments[-1].get("cooldown_ready_at"),
                )
            fury, fury_boosted = self._skill_burst_apply_fury_use(fury, skill)
            if self._skill_burst_uses_fury() and row_key == '旋转冲击':
                segments[-1]['display_name'] = '真旋转冲击' if fury_boosted else '旋转冲击（普通）' 
            if self._skill_burst_fury_gain(skill) > 0:
                fury_expires_at = end + 60.0
            guardian_phantoms, guardian_used = self._skill_burst_apply_guardian_phantom_use(guardian_phantoms, skill)
            used_skill_keys.add(row_key)
            if self._skill_burst_uses_fury():
                fury_cost, _fury_multiplier = self._skill_burst_fury_consumer(skill)
                fury_gain = self._skill_burst_fury_gain(skill)
                fury_detail = (
                    f"怒气强化{'生效' if fury_boosted else '未生效'}，剩余{fury}"
                    if fury_cost > 0
                    else f"获得{fury_gain}怒气，当前{fury}"
                    if fury_gain > 0
                    else ""
                )
                segments[-1]["detail"] = "；".join(filter(None, [
                    norm(segments[-1].get("detail")),
                    fury_detail,
                ]))
            if self._skill_burst_uses_guardian_phantoms():
                guardian_cost = self._skill_burst_guardian_phantom_cost(skill)
                guardian_gain = self._skill_burst_guardian_phantom_gain(skill)
                guardian_detail = (
                    f"消耗{guardian_cost}幻影，剩余{guardian_phantoms}"
                    if guardian_cost > 0 and guardian_used
                    else f"获得{guardian_gain}幻影，当前{guardian_phantoms}"
                    if guardian_gain > 0
                    else ""
                )
                segments[-1]["detail"] = "；".join(filter(None, [
                    norm(segments[-1].get("detail")),
                    guardian_detail,
                ]))
            summon_limit = self._skill_burst_summon_limit(row_key)
            if summon_limit:
                removed_summons = self._skill_burst_register_summon(
                    row_key,
                    summon_active_until,
                    end + summon_limit[1],
                    end,
                )
                if removed_summons:
                    segments[-1]["detail"] = "；".join(filter(None, [
                        norm(segments[-1].get("detail")),
                        "空档提前续召，替换最早召唤物",
                    ]))
            elif row_key in SKILL_BURST_ENGINEER_SUMMONS:
                removed_summons = self._skill_burst_register_summon(
                    row_key,
                    summon_active_until,
                    end + 30.0,
                    end,
                )
                if removed_summons:
                    segments[-1]["detail"] = "；".join(filter(None, [
                        norm(segments[-1].get("detail")),
                        "空档提前续召，替换最早召唤物",
                    ]))
            if row_key in {"炮塔", "加特林机枪塔"}:
                shared_count = sum(
                    len(summon_active_until.get(group_skill, []))
                    for group_skill in ("炮塔", "加特林机枪塔")
                )
                if shared_count < 2:
                    # 两个共享栏位允许同名塔；第一个召唤后立即开放同技能的第二次召唤。
                    damage_available_at[row_key] = start
            if row_key in charged_skill_charges:
                damage_available_at[row_key] = (
                    start
                    if charged_skill_charges.get(row_key, 0) > 0
                    else as_number(charged_skill_recharge_at.get(row_key), start + max(0.0, cooldown))
                )
            previous_skill = skill
            current = end

        if self._team_real_job(current_job) in {"曜", "暗"}:
            azuna_metric = metrics_by_skill.get("阿兹那之约")
            learned_level = self._skill_build_level_for_burst_sim("阿兹那之约")
            output_yao = "输出曜" in norm(getattr(self, "_skill_burst_current_provider_override", ""))
            if azuna_metric and ((learned_level is not None and learned_level > 0) or output_yao):
                cycle = max(
                    1.0,
                    self._skill_periodic_total_damage_cycle("阿兹那之约")
                    or as_number(azuna_metric.get("base_cooldown"), 20.0),
                )
                effective_per_second = max(0.0, as_number(azuna_metric.get("effective_score"))) / cycle
                raw_per_second = max(0.0, as_number(azuna_metric.get("raw_score"))) / cycle
                segments.append({
                    "start": 0.0,
                    "end": window,
                    "skill": "阿兹那之约",
                    "type": "常驻召唤",
                    "kind": "passive",
                    "dpa": effective_per_second,
                    "effective_score": effective_per_second * window,
                    "raw_effective_score": raw_per_second * window,
                    "channel_scores": self._skill_burst_scaled_channel_scores(
                        azuna_metric,
                        raw_per_second * window,
                    ),
                    "periodic_metric": copy.deepcopy(azuna_metric),
                    "element": norm(azuna_metric.get("element")),
                    "cdr": 0.0,
                    "timeline_hidden": True,
                    "detail": f"不占用施法时间，按 {cycle:g}s 完整伤害循环持续折算",
                })

        self._skill_burst_apply_refreshable_periodic_scores(segments, window)
        self._skill_burst_apply_periodic_total_damage_scores(segments, support_actions, window)
        self._skill_burst_attach_mixed_attack_damage(segments)
        segments = self._skill_burst_improve_conservative_1_6_timeline(
            segments,
            all_learned_metrics,
            support_actions,
            window,
        )
        segments = self._skill_burst_improve_baseline_timeline(
            segments,
            all_learned_metrics,
            support_actions,
            window,
            candidates,
        )
        generated_effect_windows = self._skill_burst_effect_windows(segments, support_actions, window)
        self._skill_burst_fill_sitting_yao_idle(segments, window)
        self._skill_burst_generated_effect_windows = copy.deepcopy(generated_effect_windows)
        cdr_bits = []
        if self._skill_burst_sim_enabled("practical"):
            cdr_bits.append(f"实战模拟{SKILL_BURST_PRACTICAL_WINDOW:g}s：基础0%减CD")
        if self._skill_burst_sim_enabled("burst"):
            cdr_bits.append("精神爆发14s：-40%CD，动作速度+40%")
        if self._skill_burst_sim_enabled("accel"):
            cdr_bits.append("时间加速15s：-70%CD")
        if self._skill_burst_sim_enabled("cocktail"):
            cdr_bits.append("鸡尾酒常驻：-12%CD")
        support_text = f"；循环buff/减抗/增伤 {len(support_actions)} 个" if support_actions else "；未读取到循环辅助技能"
        note = "；".join(cdr_bits) + support_text + "；前置到期优先补，其余按DPA填充。"
        if norm(self.second_job.currentText()) in {"炼金圣士", "药剂师"}:
            note += "；火→冰戳刺按冰层全轴增益与占用施法时间的净收益动态取舍。"
        self._skill_burst_generated_note = note
        auto_segments = copy.deepcopy(segments)
        primary_ambiguity_ratio = max(
            0.0,
            min(
                1.0,
                as_number(
                    getattr(self, "_skill_burst_new_scheme_1_ambiguity_ratio", 0.90),
                    0.90,
                ),
            ),
        )
        if (
            new_scheme_1
            and window >= 120.0
            and abs(primary_ambiguity_ratio - 0.90) <= 1e-9
            and not getattr(self, "_skill_burst_new_scheme_1_multistart_active", False)
            and not getattr(self, "_skill_burst_complete_default_rotation", False)
            and getattr(self, "_skill_burst_enable_new_scheme_1_multistart", True)
        ):
            primary_score = self._skill_burst_local_timeline_score(
                auto_segments,
                window,
            )
            primary_effect_windows = copy.deepcopy(
                self._skill_burst_generated_effect_windows
            )
            primary_periodic_buffs = copy.deepcopy(self._skill_burst_periodic_buffs)
            primary_refinement = copy.deepcopy(
                getattr(self, "_skill_burst_local_optimizer_last_stats", {})
            )
            primary_planning = copy.deepcopy(
                getattr(self, "_skill_burst_new_scheme_1_planning_stats", {})
            )
            primary_occupied = sum(
                max(
                    0.0,
                    min(window, as_number(segment.get("end")))
                    - max(0.0, as_number(segment.get("start"))),
                )
                for segment in auto_segments
                if not segment.get("timeline_hidden")
                and norm(segment.get("kind")) != "invalid"
            )
            primary_idle = max(0.0, window - min(window, primary_occupied))
            alternate_ratio = 0.75 if primary_idle >= 12.0 else 0.98
            had_ratio = "_skill_burst_new_scheme_1_ambiguity_ratio" in self.__dict__
            previous_ratio = getattr(
                self,
                "_skill_burst_new_scheme_1_ambiguity_ratio",
                0.90,
            )
            self._skill_burst_new_scheme_1_multistart_active = True
            self._skill_burst_new_scheme_1_ambiguity_ratio = alternate_ratio
            alternate_started = time.perf_counter()
            try:
                alternate_segments, alternate_window = self._calculate_skill_burst_sim_preview_data(
                    metrics,
                    render_ui=False,
                )
            finally:
                self._skill_burst_new_scheme_1_multistart_active = False
                if had_ratio:
                    self._skill_burst_new_scheme_1_ambiguity_ratio = previous_ratio
                else:
                    self.__dict__.pop(
                        "_skill_burst_new_scheme_1_ambiguity_ratio",
                        None,
                    )
            alternate_elapsed = time.perf_counter() - alternate_started
            alternate_score = self._skill_burst_local_timeline_score(
                alternate_segments,
                alternate_window,
            )
            alternate_effect_windows = copy.deepcopy(
                self._skill_burst_generated_effect_windows
            )
            alternate_periodic_buffs = copy.deepcopy(self._skill_burst_periodic_buffs)
            alternate_refinement = copy.deepcopy(
                getattr(self, "_skill_burst_local_optimizer_last_stats", {})
            )
            alternate_planning = copy.deepcopy(
                getattr(self, "_skill_burst_new_scheme_1_planning_stats", {})
            )
            use_alternate = alternate_score > primary_score + 1e-6
            if use_alternate:
                auto_segments = copy.deepcopy(alternate_segments)
                window = alternate_window
                self._skill_burst_generated_effect_windows = alternate_effect_windows
                self._skill_burst_periodic_buffs = alternate_periodic_buffs
                self._skill_burst_local_optimizer_last_stats = alternate_refinement
                self._skill_burst_new_scheme_1_planning_stats = alternate_planning
            else:
                self._skill_burst_generated_effect_windows = primary_effect_windows
                self._skill_burst_periodic_buffs = primary_periodic_buffs
                self._skill_burst_local_optimizer_last_stats = primary_refinement
                self._skill_burst_new_scheme_1_planning_stats = primary_planning
            self._skill_burst_new_scheme_1_last_multistart = {
                "selected": "conservative" if use_alternate else "balanced",
                "alternate_ratio": alternate_ratio,
                "balanced_idle": primary_idle,
                "balanced_score": primary_score,
                "conservative_score": alternate_score,
                "gain": max(primary_score, alternate_score) - primary_score,
                "alternate_elapsed": alternate_elapsed,
            }
        if (
            new_scheme_1
            and not getattr(self, "_skill_burst_new_scheme_1_quality_guard_active", False)
            and getattr(self, "_skill_burst_enable_new_scheme_1_quality_floor", False)
        ):
            new_effect_windows = copy.deepcopy(self._skill_burst_generated_effect_windows)
            new_periodic_buffs = copy.deepcopy(self._skill_burst_periodic_buffs)
            new_score = sum(
                max(0.0, as_number(segment.get("effective_score")))
                for segment in auto_segments
                if norm(segment.get("kind")) != "invalid"
                and as_number(segment.get("start")) < window - 1e-6
            )
            had_disable_flag = "_skill_burst_disable_new_scheme_1" in self.__dict__
            previous_disable_flag = getattr(
                self,
                "_skill_burst_disable_new_scheme_1",
                False,
            )
            self._skill_burst_new_scheme_1_quality_guard_active = True
            self._skill_burst_disable_new_scheme_1 = True
            baseline_started = time.perf_counter()
            try:
                baseline_segments, baseline_window = self._calculate_skill_burst_sim_preview_data(
                    metrics,
                    render_ui=False,
                )
            finally:
                self._skill_burst_new_scheme_1_quality_guard_active = False
                if had_disable_flag:
                    self._skill_burst_disable_new_scheme_1 = previous_disable_flag
                else:
                    self.__dict__.pop("_skill_burst_disable_new_scheme_1", None)
            baseline_elapsed = time.perf_counter() - baseline_started
            baseline_score = sum(
                max(0.0, as_number(segment.get("effective_score")))
                for segment in baseline_segments
                if norm(segment.get("kind")) != "invalid"
                and as_number(segment.get("start")) < baseline_window - 1e-6
            )
            use_new_scheme = new_score + 1e-6 >= baseline_score
            if use_new_scheme:
                self._skill_burst_generated_effect_windows = new_effect_windows
                self._skill_burst_periodic_buffs = new_periodic_buffs
            else:
                auto_segments = copy.deepcopy(baseline_segments)
                window = baseline_window
            self._skill_burst_new_scheme_1_last_quality = {
                "selected": "new_scheme_1" if use_new_scheme else "stable_floor",
                "new_score": new_score,
                "stable_score": baseline_score,
                "gain_percent": (
                    (new_score / baseline_score - 1.0) * 100.0
                    if baseline_score > 1e-9
                    else 0.0
                ),
                "floor_elapsed": baseline_elapsed,
            }
        active_modes = frozenset(
            mode
            for mode in ("practical", "cocktail", "burst", "accel")
            if self._skill_burst_sim_enabled(mode)
        )
        if (
            new_scheme_1
            and "cocktail" in active_modes
            and active_modes.intersection({"burst", "accel"})
            and window <= 30.0 + 1e-6
            and not isinstance(getattr(self, "_skill_burst_schedule_override", None), dict)
            and not getattr(self, "_skill_burst_cdr_quality_floor_active", False)
        ):
            # Additional cooldown reduction expands the legal schedule; it can
            # never make the previous schedule invalid or reduce the optimum.
            # Keep the lower-CD search honest when its short horizon happens to
            # consume a combo skill too early and loses a stronger tail chain.
            current_segments = copy.deepcopy(auto_segments)
            current_window = window
            current_score = self._skill_burst_local_timeline_score(
                current_segments,
                current_window,
            )
            current_effect_windows = copy.deepcopy(
                getattr(self, "_skill_burst_generated_effect_windows", [])
            )
            current_periodic_buffs = copy.deepcopy(
                getattr(self, "_skill_burst_periodic_buffs", [])
            )
            current_refinement = copy.deepcopy(
                getattr(self, "_skill_burst_local_optimizer_last_stats", {})
            )
            current_planning = copy.deepcopy(
                getattr(self, "_skill_burst_new_scheme_1_planning_stats", {})
            )
            current_quality = copy.deepcopy(
                getattr(self, "_skill_burst_new_scheme_1_last_quality", {})
            )
            sentinel = object()
            saved_mode_override = getattr(self, "_skill_burst_mode_override", sentinel)
            self._skill_burst_cdr_quality_floor_active = True
            self._skill_burst_mode_override = frozenset(active_modes - {"cocktail"})
            floor_started = time.perf_counter()
            try:
                floor_segments, floor_window = self._calculate_skill_burst_sim_preview_data(
                    metrics,
                    render_ui=False,
                )
            finally:
                self._skill_burst_cdr_quality_floor_active = False
                if saved_mode_override is sentinel:
                    self.__dict__.pop("_skill_burst_mode_override", None)
                else:
                    self._skill_burst_mode_override = saved_mode_override
            floor_elapsed = time.perf_counter() - floor_started
            floor_score = self._skill_burst_local_timeline_score(
                floor_segments,
                floor_window,
            )
            use_floor = floor_score > current_score + 1e-6
            if use_floor:
                auto_segments = copy.deepcopy(floor_segments)
                window = floor_window
            else:
                auto_segments = current_segments
                window = current_window
                self._skill_burst_generated_effect_windows = current_effect_windows
                self._skill_burst_periodic_buffs = current_periodic_buffs
                self._skill_burst_local_optimizer_last_stats = current_refinement
                self._skill_burst_new_scheme_1_planning_stats = current_planning
            self._skill_burst_new_scheme_1_last_quality = current_quality
            self._skill_burst_cdr_quality_floor_last = {
                "selected": "reduced_cooldown" if not use_floor else "monotonic_floor",
                "current_score": current_score,
                "floor_score": floor_score,
                "gain": max(current_score, floor_score) - current_score,
                "elapsed": floor_elapsed,
            }
            self._skill_burst_generated_note = note
        if (
            new_scheme_1
            and not getattr(
                self,
                "_skill_burst_new_scheme_1_multistart_active",
                False,
            )
        ):
            auto_segments = self._skill_burst_improve_short_personal_burst_timeline(
                auto_segments,
                all_learned_metrics,
                support_actions,
                window,
                candidates,
            )
            auto_segments = self._skill_burst_improve_opening_periodic_timeline(
                auto_segments,
                all_learned_metrics,
                support_actions,
                window,
            )
        joint_support_order_already_applied = False
        if (
            new_scheme_1
            and self._skill_burst_uses_dynamic_support_order()
            and not getattr(
                self,
                "_skill_burst_team_opening_quality_guard_active",
                False,
            )
            and not (
                getattr(
                    self,
                    "_skill_burst_disable_team_objective_opening_order",
                    False,
                )
                and getattr(
                    self,
                    "_skill_burst_disable_team_objective_pair_order",
                    False,
                )
            )
        ):
            # A local two-action coverage gain can phase-shift the complete
            # rotation and lose a high-value cast at the far boundary.  The
            # absence of an early visible reorder does not prove equivalence:
            # two ready support actions can diverge again in a later cooldown
            # cycle.  Validate both personal and weighted-team proposals
            # against the established ordering with the same complete-axis
            # objective that is displayed/scored.
            dynamic_segments = copy.deepcopy(auto_segments)
            dynamic_window = window
            dynamic_score = self._skill_burst_local_timeline_score(
                dynamic_segments,
                dynamic_window,
            )
            dynamic_effect_windows = copy.deepcopy(
                self._skill_burst_effect_windows(
                    dynamic_segments,
                    support_actions,
                    dynamic_window,
                )
            )
            dynamic_periodic_buffs = copy.deepcopy(self._skill_burst_periodic_buffs)
            dynamic_refinement = copy.deepcopy(
                getattr(self, "_skill_burst_local_optimizer_last_stats", {})
            )
            dynamic_planning = copy.deepcopy(
                getattr(self, "_skill_burst_new_scheme_1_planning_stats", {})
            )
            dynamic_multistart = copy.deepcopy(
                getattr(self, "_skill_burst_new_scheme_1_last_multistart", {})
            )
            previous_opening_disable = getattr(
                self,
                "_skill_burst_disable_team_objective_opening_order",
                False,
            )
            previous_pair_disable = getattr(
                self,
                "_skill_burst_disable_team_objective_pair_order",
                False,
            )
            self._skill_burst_team_opening_quality_guard_active = True
            self._skill_burst_disable_team_objective_opening_order = True
            self._skill_burst_disable_team_objective_pair_order = True
            baseline_started = time.perf_counter()
            try:
                baseline_segments, baseline_window = (
                    self._calculate_skill_burst_sim_preview_data(
                        metrics,
                        render_ui=False,
                    )
                )
                baseline_joint_support_quality = copy.deepcopy(
                    getattr(
                        self,
                        "_skill_burst_joint_support_order_last_quality",
                        {},
                    )
                )
            finally:
                self._skill_burst_team_opening_quality_guard_active = False
                self._skill_burst_disable_team_objective_opening_order = (
                    previous_opening_disable
                )
                self._skill_burst_disable_team_objective_pair_order = (
                    previous_pair_disable
                )
            baseline_elapsed = time.perf_counter() - baseline_started
            baseline_score = self._skill_burst_local_timeline_score(
                baseline_segments,
                baseline_window,
            )
            baseline_effect_windows = copy.deepcopy(self._skill_burst_generated_effect_windows)
            baseline_periodic_buffs = copy.deepcopy(self._skill_burst_periodic_buffs)
            comparison = self._skill_burst_support_order_comparison(
                dynamic_segments, baseline_segments, support_actions,
                dynamic_window, baseline_window,
            )
            dynamic_joint_support_quality = {}
            if comparison["owner_floor_satisfied"]:
                # A changed tail which already loses owner damage is rejected
                # by the existing output floor; do not replay that long losing
                # rotation again. Eligible orders receive the SAME bounded
                # exact-replay pass before comparing their joint objective.
                self._skill_burst_periodic_buffs = dynamic_periodic_buffs
                dynamic_segments = self._skill_burst_improve_joint_support_order(
                    dynamic_segments, all_learned_metrics, support_actions, dynamic_window,
                )
                dynamic_joint_support_quality = copy.deepcopy(
                    getattr(self, "_skill_burst_joint_support_order_last_quality", {})
                )
                dynamic_score = self._skill_burst_local_timeline_score(dynamic_segments, dynamic_window)
                dynamic_effect_windows = copy.deepcopy(self._skill_burst_effect_windows(
                    dynamic_segments, support_actions, dynamic_window,
                ))
            comparison = self._skill_burst_support_order_comparison(
                dynamic_segments, baseline_segments, support_actions,
                dynamic_window, baseline_window,
            )
            dynamic_joint_score = comparison["dynamic_joint_score"]
            baseline_joint_score = comparison["stable_joint_score"]
            use_dynamic = comparison["use_dynamic"]
            # Both candidates have already received the exact ordering pass.
            joint_support_order_already_applied = True
            if use_dynamic:
                auto_segments = dynamic_segments
                window = dynamic_window
                self._skill_burst_generated_effect_windows = dynamic_effect_windows
                self._skill_burst_periodic_buffs = dynamic_periodic_buffs
                self._skill_burst_local_optimizer_last_stats = dynamic_refinement
                self._skill_burst_new_scheme_1_planning_stats = dynamic_planning
                self._skill_burst_new_scheme_1_last_multistart = dynamic_multistart
                self._skill_burst_joint_support_order_last_quality = (
                    dynamic_joint_support_quality
                )
            else:
                auto_segments = copy.deepcopy(baseline_segments)
                window = baseline_window
                self._skill_burst_generated_effect_windows = baseline_effect_windows
                self._skill_burst_periodic_buffs = baseline_periodic_buffs
                # The recursive stable-order calculation already searched
                # these exact segments for a better joint-support ordering.
                # Do not replay the same full-axis proposals a second time.
                joint_support_order_already_applied = True
                self._skill_burst_joint_support_order_last_quality = (
                    baseline_joint_support_quality
                )
            self._skill_burst_team_opening_last_quality = {
                "selected": "dynamic" if use_dynamic else "stable_order",
                "dynamic_score": dynamic_score,
                "stable_score": baseline_score,
                "dynamic_joint_score": dynamic_joint_score,
                "stable_joint_score": baseline_joint_score,
                "objective": "owner_plus_weighted_team",
                "same_rotation_tail": comparison["same_rotation_tail"],
                "owner_floor_satisfied": comparison["owner_floor_satisfied"],
                "gain": max(dynamic_joint_score, baseline_joint_score) - baseline_joint_score,
                "owner_delta": (dynamic_score - baseline_score) if use_dynamic else 0.0,
                "baseline_elapsed": baseline_elapsed,
                "stable_joint_support_quality": (
                    baseline_joint_support_quality
                ),
                "dynamic_opening": [
                    self._skill_burst_skill_key(row.get("skill"))
                    for row in dynamic_segments[:8]
                    if self._skill_burst_skill_key(row.get("skill"))
                ],
                "stable_opening": [
                    self._skill_burst_skill_key(row.get("skill"))
                    for row in baseline_segments[:8]
                    if self._skill_burst_skill_key(row.get("skill"))
                ],
            }
        if new_scheme_1 and not joint_support_order_already_applied:
            auto_segments = self._skill_burst_improve_joint_support_order(
                auto_segments,
                all_learned_metrics,
                support_actions,
                window,
            )
        generated_effect_windows = self._skill_burst_effect_windows(
            auto_segments,
            support_actions,
            window,
        )
        self._skill_burst_generated_effect_windows = copy.deepcopy(
            generated_effect_windows
        )
        if new_scheme_1_cache_key is not None:
            self._skill_burst_new_scheme_1_store_result(
                new_scheme_1_cache_key,
                auto_segments,
                window,
            )
        if not render_ui:
            return auto_segments, window
        self._skill_burst_auto_segments = auto_segments
        self.skill_burst_auto_total_label.setText(
            self._skill_burst_axis_total_text("自动轴", auto_segments, window)
        )
        self.skill_burst_auto_total_label.setToolTip(self._skill_burst_total_text(auto_segments, window))
        manual_note = note
        if self._skill_burst_manual_sequence is not None:
            manual_sequence = [norm(skill) for skill in self._skill_burst_manual_sequence if norm(skill)]
            auto_sequence = [
                norm(segment.get("skill"))
                for segment in auto_segments
                if norm(segment.get("skill")) and not segment.get("timeline_hidden")
            ]
            if manual_sequence == auto_sequence:
                # Returning every block to the automatic order must also restore the
                # automatic scheduler's exact cooldown and combo timings.
                manual_segments = copy.deepcopy(auto_segments)
                manual_issues = []
                manual_note += "；当前顺序与自动轴一致，已精确校准全部时间。"
            else:
                manual_segments, manual_issues = self._skill_burst_replay_manual_sequence(
                    manual_sequence,
                    all_learned_metrics,
                    support_actions,
                    window,
                )
                manual_segments.extend(
                    copy.deepcopy(segment)
                    for segment in auto_segments
                    if segment.get("timeline_hidden")
                )
                self._skill_burst_apply_periodic_total_damage_scores(manual_segments, support_actions, window)
                self._skill_burst_attach_mixed_attack_damage(manual_segments)
            manual_note += "；手动编排中，可拖动技能块或在技能列表的下拉框中调整。"
            if manual_issues:
                manual_note += f"；{manual_issues[0]}"
        else:
            manual_segments = copy.deepcopy(auto_segments)
            manual_note += "；当前跟随自动轴，首次调整后转为独立自选轴。"
        manual_effect_windows = self._skill_burst_effect_windows(manual_segments, support_actions, window)
        self._render_skill_burst_segments(
            manual_segments,
            window,
            manual_note,
            manual_effect_windows,
        )
        self._update_skill_burst_average_output_table(metrics)
        if new_scheme_1_cache_key is not None:
            self._skill_burst_new_scheme_1_last_render_key = new_scheme_1_cache_key
        return auto_segments, window
