from __future__ import annotations

from ..shared import *

BuildEditor = None


class TimelineEditorMixin:
    @staticmethod
    def _timeline_sequence_index_for_table_row(table: QTableWidget, row: int) -> int:
        mapping = getattr(table, "_sequence_index_by_row", [])
        if not isinstance(mapping, list) or not 0 <= row < len(mapping):
            return row
        mapped = int(as_number(mapping[row], -1))
        if mapped >= 0:
            return mapped
        for distance in range(1, len(mapping) + 1):
            for candidate in (row + distance, row - distance):
                if 0 <= candidate < len(mapping):
                    mapped = int(as_number(mapping[candidate], -1))
                    if mapped >= 0:
                        return mapped
        return -1

    @staticmethod
    def _timeline_table_row_for_sequence_index(table: QTableWidget, index: int) -> int:
        mapping = getattr(table, "_sequence_index_by_row", [])
        if isinstance(mapping, list):
            for row, mapped in enumerate(mapping):
                if int(as_number(mapped, -1)) == index:
                    return row
        return index

    def _skill_burst_sequence_for_editing(self) -> list[str]:
        if self._skill_burst_manual_sequence is not None:
            return list(self._skill_burst_manual_sequence)
        return [
            norm(segment.get("skill"))
            for segment in self._skill_burst_last_segments
            if norm(segment.get("skill")) and not segment.get("timeline_hidden")
        ]

    def _skill_burst_set_manual_sequence(self, sequence: list[str]) -> None:
        self._skill_burst_manual_job = norm(self.second_job.currentText())
        self._skill_burst_manual_sequence = [norm(skill) for skill in sequence if norm(skill)]
        self.refresh()

    def _skill_burst_select_segment(self, index: int) -> None:
        visible_segments = [
            row for row in self._skill_burst_last_segments
            if norm(row.get("skill")) and not row.get("timeline_hidden")
        ]
        self._skill_burst_selected_index = index if 0 <= index < len(visible_segments) else -1
        self.skill_burst_timeline.set_selected_index(self._skill_burst_selected_index)
        if self._skill_burst_selected_index >= 0 and hasattr(self, "skill_burst_detail_table"):
            table_row = self._timeline_table_row_for_sequence_index(
                self.skill_burst_detail_table,
                self._skill_burst_selected_index,
            )
            self.skill_burst_detail_table.setCurrentCell(table_row, 2)
        if hasattr(self, "skill_burst_candidate_layout"):
            self._render_skill_burst_candidates(
                list(getattr(self, "_skill_burst_candidate_metrics", []))
            )

    def _skill_burst_detail_selected(self, row: int, _column: int) -> None:
        self._skill_burst_select_segment(
            self._timeline_sequence_index_for_table_row(self.skill_burst_detail_table, row)
        )

    def _skill_burst_detail_row_moved(self, source_row: int, target_row: int) -> None:
        source = self._timeline_sequence_index_for_table_row(
            self.skill_burst_detail_table, source_row
        )
        target = self._timeline_sequence_index_for_table_row(
            self.skill_burst_detail_table, target_row
        )
        if source >= 0 and target >= 0:
            self._skill_burst_move_segment(source, target)

    def _skill_burst_selected_row(self) -> int:
        index = int(getattr(self, "_skill_burst_selected_index", -1))
        return index if 0 <= index < len(self._skill_burst_sequence_for_editing()) else -1

    def _skill_burst_manual_sequence_quality(
        self,
        sequence: list[str],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> tuple[int, float, float]:
        segments, issues = self._skill_burst_replay_manual_sequence(
            sequence,
            candidates,
            support_actions,
            window,
        )
        invalid_count = sum(1 for segment in segments if norm(segment.get("kind")) == "invalid")
        total_output = sum(
            max(0.0, as_number(segment.get("effective_score")))
            for segment in segments
            if as_number(segment.get("start")) < window - 1e-6
        )
        last_end = max(
            (
                min(window, max(0.0, as_number(segment.get("end"))))
                for segment in segments
                if as_number(segment.get("start")) < window - 1e-6
            ),
            default=0.0,
        )
        return invalid_count + len(issues), total_output, last_end

    @staticmethod
    def _skill_burst_sequence_quality_is_better(
        candidate: tuple[int, float, float],
        current: tuple[int, float, float],
    ) -> bool:
        if candidate[0] != current[0]:
            return candidate[0] < current[0]
        output_tolerance = max(0.01, abs(current[1]) * 1e-7)
        if candidate[1] > current[1] + output_tolerance:
            return True
        if abs(candidate[1] - current[1]) <= output_tolerance:
            return candidate[2] < current[2] - 1e-6
        return False

    def _skill_burst_optimize_dragged_sequence(
        self,
        sequence: list[str],
        anchor_index: int,
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        *,
        radius: int = 6,
        passes: int = 3,
    ) -> list[str]:
        """Repair a small area around a dragged block while keeping its requested slot."""
        if (
            len(sequence) < 3
            or not 0 <= anchor_index < len(sequence)
            or not candidates
            or window <= 0
        ):
            return list(sequence)
        best = list(sequence)
        quality_cache: dict[tuple[str, ...], tuple[int, float, float]] = {}

        def quality(value: list[str]) -> tuple[int, float, float]:
            key = tuple(value)
            if key not in quality_cache:
                quality_cache[key] = self._skill_burst_manual_sequence_quality(
                    value,
                    candidates,
                    support_actions,
                    window,
                )
            return quality_cache[key]

        best_quality = quality(best)
        local_indexes = [
            index
            for index in range(
                max(0, anchor_index - min(2, max(1, radius))),
                min(len(best), anchor_index + max(1, radius) + 1),
            )
            if index != anchor_index
        ]
        for _pass in range(max(1, passes)):
            round_best = best
            round_quality = best_quality
            for first, second in combinations(local_indexes, 2):
                candidate_sequence = list(best)
                candidate_sequence[first], candidate_sequence[second] = (
                    candidate_sequence[second],
                    candidate_sequence[first],
                )
                candidate_quality = quality(candidate_sequence)
                if self._skill_burst_sequence_quality_is_better(candidate_quality, round_quality):
                    round_best = candidate_sequence
                    round_quality = candidate_quality
            # Insertion moves compact a longer tail more effectively than swaps
            # after the user pins one block at an approximate timeline position.
            for source in local_indexes:
                for target in local_indexes:
                    if source == target or (source < anchor_index) != (target < anchor_index):
                        continue
                    candidate_sequence = list(best)
                    moved = candidate_sequence.pop(source)
                    candidate_sequence.insert(target, moved)
                    candidate_quality = quality(candidate_sequence)
                    if self._skill_burst_sequence_quality_is_better(candidate_quality, round_quality):
                        round_best = candidate_sequence
                        round_quality = candidate_quality
            if round_best == best:
                break
            best = round_best
            best_quality = round_quality
        return best

    def _skill_burst_move_segment(self, old_index: int, new_index: int) -> None:
        sequence = self._skill_burst_sequence_for_editing()
        if not (0 <= old_index < len(sequence) and 0 <= new_index < len(sequence)):
            return
        skill = sequence.pop(old_index)
        sequence.insert(new_index, skill)
        sequence = self._skill_burst_optimize_dragged_sequence(
            sequence,
            new_index,
            getattr(self, "_skill_burst_last_all_metrics", []),
            getattr(self, "_skill_burst_last_support_actions", []),
            as_number(getattr(self, "_skill_burst_last_window", 0.0)),
        )
        self._skill_burst_selected_index = new_index
        self._skill_burst_set_manual_sequence(sequence)

    def _skill_burst_move_selected(self, delta: int) -> None:
        row = self._skill_burst_selected_row()
        sequence = self._skill_burst_sequence_for_editing()
        target = row + delta
        if not (0 <= row < len(sequence) and 0 <= target < len(sequence)):
            return
        skill = sequence.pop(row)
        sequence.insert(target, skill)
        self._skill_burst_selected_index = target
        self._skill_burst_set_manual_sequence(sequence)

    def _skill_burst_replace_segment(self, row: int, skill: str) -> None:
        if not norm(skill):
            return
        sequence = self._skill_burst_sequence_for_editing()
        if not 0 <= row < len(sequence):
            return
        sequence[row] = norm(skill)
        self._skill_burst_set_manual_sequence(sequence)

    def _skill_burst_detail_combo_changed(self, row: int, text: str) -> None:
        choice = norm(text)
        if not choice:
            return
        if choice == "↑ 上移一位":
            self._skill_burst_selected_index = row
            self._skill_burst_move_selected(-1)
            return
        if choice == "↓ 下移一位":
            self._skill_burst_selected_index = row
            self._skill_burst_move_selected(1)
            return
        if choice == "删除此项":
            self._skill_burst_selected_index = row
            self._skill_burst_remove_selected()
            return
        sequence = self._skill_burst_sequence_for_editing()
        if 0 <= row < len(sequence) and choice != sequence[row]:
            self._skill_burst_replace_segment(row, choice)

    def _skill_burst_add_segment(self) -> None:
        skill = norm(self.skill_burst_add_combo.currentText())
        if not skill or skill == "选择技能":
            return
        self._skill_burst_add_named_skill(skill)

    def _skill_burst_add_named_skill(self, skill: str) -> None:
        skill = norm(skill)
        if not skill:
            return
        sequence = self._skill_burst_sequence_for_editing()
        row = self._skill_burst_selected_row()
        insert_at = row + 1 if 0 <= row < len(sequence) else len(sequence)
        sequence.insert(insert_at, skill)
        self._skill_burst_set_manual_sequence(sequence)

    def _skill_burst_candidate_reference_time(self) -> float:
        index = int(getattr(self, "_skill_burst_selected_index", -1))
        segments = [
            row for row in getattr(self, "_skill_burst_last_segments", [])
            if norm(row.get("skill")) and not row.get("timeline_hidden")
        ]
        if 0 <= index < len(segments):
            return max(0.0, as_number(segments[index].get("end")))
        return 0.0

    def _skill_burst_candidate_remaining_cooldown(
        self,
        metric: dict[str, Any],
        reference_time: float,
    ) -> float:
        skill = self._skill_burst_skill_key(metric.get("skill"))
        if not skill or self._skill_burst_ammo_cost(skill) > 0 or skill == "双飞腿":
            return 0.0
        previous = [
            segment
            for segment in getattr(self, "_skill_burst_last_segments", [])
            if self._skill_burst_skill_key(segment.get("skill")) == skill
            and as_number(segment.get("start")) <= reference_time + 1e-6
        ]
        if not previous:
            return 0.0
        last = max(previous, key=lambda segment: as_number(segment.get("start")))
        cast_start = max(0.0, as_number(last.get("start")))
        recorded_ready = as_number(last.get("cooldown_ready_at"), -1.0)
        if recorded_ready >= cast_start - 1e-6:
            return max(0.0, recorded_ready - reference_time)
        base_cooldown = max(0.0, as_number(metric.get("base_cooldown")))
        if base_cooldown <= 0:
            return 0.0
        ready_at = self._skill_burst_cooldown_ready_at(skill, base_cooldown, cast_start)
        return max(0.0, ready_at - reference_time)

    def _render_timeline_candidates(
        self,
        layout: QHBoxLayout,
        metrics: list[dict[str, Any]],
        segments: list[dict[str, Any]],
        reference_time: float,
        add_skill: Callable[[str], None],
    ) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
                QApplication.sendPostedEvents(widget, QEvent.DeferredDelete)
        if not metrics:
            layout.addWidget(QLabel("当前加点没有符合条件的备选技能"))
            layout.addStretch(1)
            return
        strongest_dpa = max(
            (self._skill_burst_candidate_metric_dpa(metric) for metric in metrics),
            default=0.0,
        )
        strongest_dps = max(
            (self._skill_burst_candidate_metric_dps(metric) for metric in metrics),
            default=0.0,
        )
        metrics = [
            metric
            for metric in metrics
            if metric.get("is_ultimate")
            or self._skill_burst_candidate_metric_dpa(metric) >= strongest_dpa * 0.2 - 1e-9
            or self._skill_burst_candidate_metric_dps(metric) >= strongest_dps * 0.2 - 1e-9
        ]
        saved_segments = getattr(self, "_skill_burst_last_segments", [])
        self._skill_burst_last_segments = segments
        try:
            ordered = [
                (
                    self._skill_burst_candidate_remaining_cooldown(metric, reference_time),
                    metric,
                )
                for metric in metrics
            ]
        finally:
            self._skill_burst_last_segments = saved_segments
        ordered.sort(key=lambda row: (
            row[0] > 1e-6,
            row[0],
            -self._skill_burst_candidate_metric_dpa(row[1]),
        ))
        for remaining, metric in ordered:
            skill = norm(metric.get("skill"))
            dpa = self._skill_burst_candidate_metric_dpa(metric)
            suffix = f"  CD {remaining:.1f}s" if remaining > 1e-6 else ""
            text = f"{skill}  {self._percent_text(dpa)}/s{suffix}"
            button = app_button(text)
            button.setFixedHeight(30)
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(text) + 26)
            color = SkillBurstTimelineWidget.skill_cell_color(skill)
            if remaining > 1e-6:
                button.setStyleSheet(
                    "QPushButton {"
                    f"background:rgba({color.red()},{color.green()},{color.blue()},105);"
                    f"color:#64748b; border:1px dashed {color.darker(125).name()}; }}"
                    f"QPushButton:hover {{ background:{color.lighter(112).name()}; color:#172033; }}"
                )
            else:
                button.setStyleSheet(
                    f"QPushButton {{ background:{color.name()}; color:#172033; border:1px solid {color.darker(112).name()}; }}"
                    f"QPushButton:hover {{ background:{color.lighter(108).name()}; }}"
                )
            button.setToolTip(
                f"点击添加到自选轴\n当前定位 {reference_time:.2f}s\n"
                + (f"剩余CD {remaining:.2f}s\n" if remaining > 1e-6 else "CD已恢复\n")
                + f"DPA {self._percent_text(dpa)}/s\n"
                f"DPS {self._percent_text(self._skill_burst_candidate_metric_dps(metric))}/s"
                + ("\n大招：始终保留" if metric.get("is_ultimate") else "")
            )
            button.clicked.connect(
                lambda _checked=False, value=skill: add_skill(value)
            )
            layout.addWidget(button)
        layout.addStretch(1)

    def _render_skill_burst_candidates(self, metrics: list[dict[str, Any]]) -> None:
        self._render_timeline_candidates(
            self.skill_burst_candidate_layout,
            metrics,
            list(getattr(self, "_skill_burst_last_segments", [])),
            self._skill_burst_candidate_reference_time(),
            self._skill_burst_add_named_skill,
        )

    def _skill_burst_remove_selected(self) -> None:
        row = self._skill_burst_selected_row()
        sequence = self._skill_burst_sequence_for_editing()
        if not 0 <= row < len(sequence):
            return
        sequence.pop(row)
        self._skill_burst_selected_index = min(row, len(sequence) - 1)
        self._skill_burst_set_manual_sequence(sequence)

    def _skill_burst_restore_auto(self) -> None:
        self._skill_burst_manual_sequence = None
        self._skill_burst_manual_job = norm(self.second_job.currentText())
        self._skill_burst_selected_index = -1
        self.refresh()

    def _skill_long_sequence_for_editing(self) -> list[str]:
        if self._skill_long_manual_sequence is not None:
            return list(self._skill_long_manual_sequence)
        return [
            norm(segment.get("skill"))
            for segment in self._skill_long_last_segments
            if norm(segment.get("skill")) and not segment.get("timeline_hidden")
        ]

    def _skill_long_window_seconds(self) -> float:
        spin = getattr(self, "skill_long_window_spin", None)
        return max(30.0, spin.value() if isinstance(spin, QDoubleSpinBox) else SKILL_LONG_PRACTICAL_WINDOW)

    def _skill_long_burst_starts_for_window(self, window: float) -> list[float]:
        duration = SKILL_BURST_SIM_DURATIONS["精神爆发"]
        horizon = max(0.0, as_number(window))
        if horizon <= 0:
            return []
        start_spin = getattr(self, "skill_long_burst_start_base", None)
        interval_spin = getattr(self, "skill_long_burst_interval", None)
        start = max(0.0, as_number(start_spin.value() if isinstance(start_spin, QDoubleSpinBox) else 0.0))
        interval = max(1.0, as_number(interval_spin.value() if isinstance(interval_spin, QDoubleSpinBox) else 50.0))
        custom = {
            int(index): max(0.0, as_number(value))
            for index, value in getattr(self, "_skill_long_custom_burst_starts", {}).items()
            if int(index) >= 0
        }
        starts: list[float] = []
        index = 0
        current = start
        guard = 0
        while current < horizon - 1e-6 and guard < 1000:
            guard += 1
            if index in custom:
                current = custom[index]
            if current < horizon - 1e-6:
                starts.append(max(0.0, current))
            index += 1
            current = current + interval
            if index in custom:
                current = custom[index]
        return starts

    def _skill_long_burst_intervals_for_window(self, window: float) -> list[tuple[float, float]]:
        duration = SKILL_BURST_SIM_DURATIONS["精神爆发"]
        horizon = max(0.0, as_number(window))
        return [
            (start, min(horizon, start + duration))
            for start in self._skill_long_burst_starts_for_window(horizon)
            if start < horizon - 1e-6
        ]

    def _skill_long_accel_intervals_for_window(self, window: float) -> list[tuple[float, float]]:
        if not (
            isinstance(getattr(self, "skill_long_accel_check", None), QCheckBox)
            and self.skill_long_accel_check.isChecked()
            and isinstance(getattr(self, "skill_long_accel_start", None), QDoubleSpinBox)
        ):
            return []
        duration = SKILL_BURST_SIM_DURATIONS["时间加速"]
        horizon = max(0.0, as_number(window))
        start = max(0.0, as_number(self.skill_long_accel_start.value()))
        intervals: list[tuple[float, float]] = []
        guard = 0
        while start < horizon - 1e-6 and guard < 1000:
            intervals.append((start, min(horizon, start + duration)))
            start += 100.0
            guard += 1
        return intervals

    def _skill_long_schedule(self, window: float | None = None) -> dict[str, Any]:
        long_window = self._skill_long_window_seconds() if window is None else max(30.0, as_number(window))
        return {
            "window": long_window,
            "burst": self._skill_long_burst_intervals_for_window(long_window),
            "accel": self._skill_long_accel_intervals_for_window(long_window),
            "cocktail": bool(
                isinstance(getattr(self, "skill_long_cocktail_check", None), QCheckBox)
                and self.skill_long_cocktail_check.isChecked()
            ),
        }

    def _skill_long_refresh_burst_selector(self) -> None:
        selector = getattr(self, "skill_long_burst_selector", None)
        adjust = getattr(self, "skill_long_burst_adjust", None)
        if not isinstance(selector, QComboBox) or not isinstance(adjust, QDoubleSpinBox):
            return
        starts = self._skill_long_burst_starts_for_window(self._skill_long_window_seconds())
        current_index = max(0, selector.currentIndex())
        selector.blockSignals(True)
        selector.clear()
        selector.addItems([f"精神爆发{index + 1}" for index in range(len(starts))] or ["无精神爆发"])
        selector.setCurrentIndex(min(current_index, max(0, selector.count() - 1)))
        selector.blockSignals(False)
        selected = selector.currentIndex()
        adjust.blockSignals(True)
        adjust.setRange(0.0, max(0.0, self._skill_long_window_seconds()))
        adjust.setEnabled(bool(starts))
        adjust.setValue(starts[selected] if 0 <= selected < len(starts) else 0.0)
        adjust.blockSignals(False)

    def _skill_long_burst_pattern_changed(self, _value: float) -> None:
        self._skill_long_custom_burst_starts = {}
        self._skill_long_refresh_burst_selector()
        self._skill_long_manual_sequence = None
        self.schedule_refresh()

    def _skill_long_burst_selector_changed(self, _index: int) -> None:
        self._skill_long_refresh_burst_selector()

    def _skill_long_burst_adjust_changed(self, value: float) -> None:
        selector = getattr(self, "skill_long_burst_selector", None)
        if not isinstance(selector, QComboBox):
            return
        index = selector.currentIndex()
        if index < 0:
            return
        self._skill_long_custom_burst_starts[index] = max(0.0, as_number(value))
        self._skill_long_refresh_burst_selector()
        selector.setCurrentIndex(index)
        self._skill_long_manual_sequence = None
        self.schedule_refresh()

    def _skill_long_window_changed(self, value: float) -> None:
        window = max(30.0, as_number(value, SKILL_LONG_PRACTICAL_WINDOW))
        accel_duration = SKILL_BURST_SIM_DURATIONS["时间加速"]
        accel_span = max(0.0, window - accel_duration)
        accel_spin = getattr(self, "skill_long_accel_start", None)
        if isinstance(accel_spin, QDoubleSpinBox):
            accel_spin.blockSignals(True)
            accel_spin.setRange(0.0, accel_span)
            accel_spin.setValue(accel_span / 2.0)
            accel_spin.blockSignals(False)
        burst_start_spin = getattr(self, "skill_long_burst_start_base", None)
        if isinstance(burst_start_spin, QDoubleSpinBox):
            burst_start_spin.blockSignals(True)
            burst_start_spin.setRange(0.0, max(0.0, window))
            burst_start_spin.blockSignals(False)
        burst_adjust = getattr(self, "skill_long_burst_adjust", None)
        if isinstance(burst_adjust, QDoubleSpinBox):
            burst_adjust.blockSignals(True)
            burst_adjust.setRange(0.0, max(0.0, window))
            burst_adjust.blockSignals(False)
        legacy_span = max(0.0, window - SKILL_BURST_SIM_DURATIONS["精神爆发"])
        legacy_count = len(getattr(self, "skill_long_burst_starts", []))
        for index, legacy_spin in enumerate(getattr(self, "skill_long_burst_starts", [])):
            if not isinstance(legacy_spin, QDoubleSpinBox):
                continue
            legacy_spin.blockSignals(True)
            legacy_spin.setRange(0.0, legacy_span)
            legacy_spin.setValue(
                legacy_span * index / max(1, legacy_count - 1)
            )
            legacy_spin.blockSignals(False)
        self._skill_long_custom_burst_starts = {
            index: value
            for index, value in getattr(self, "_skill_long_custom_burst_starts", {}).items()
            if as_number(value) < window - 1e-6
        }
        self._skill_long_refresh_burst_selector()
        self._skill_long_manual_sequence = None
        self._skill_long_selected_index = -1
        self.schedule_refresh()

    def _skill_long_set_manual_sequence(self, sequence: list[str]) -> None:
        self._skill_long_manual_job = norm(self.second_job.currentText())
        self._skill_long_manual_sequence = [norm(skill) for skill in sequence if norm(skill)]
        self.refresh()

    def _skill_long_select_segment(self, index: int) -> None:
        self._skill_long_selected_index = index if 0 <= index < len(self._skill_long_sequence_for_editing()) else -1
        self.skill_long_timeline.set_selected_index(self._skill_long_selected_index)
        if self._skill_long_selected_index >= 0:
            table = self.skill_long_detail_table
            table_row = self._timeline_table_row_for_sequence_index(
                table, self._skill_long_selected_index
            )
            table.setCurrentCell(table_row, 2)
            item = table.item(table_row, 0)
            if item is not None:
                table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        if hasattr(self, "skill_long_candidate_layout"):
            self._render_skill_long_candidates(
                list(getattr(self, "_skill_long_candidate_metrics", []))
            )

    def _skill_long_detail_selected(self, row: int, _column: int) -> None:
        self._skill_long_select_segment(
            self._timeline_sequence_index_for_table_row(self.skill_long_detail_table, row)
        )

    def _skill_long_detail_row_moved(self, source_row: int, target_row: int) -> None:
        source = self._timeline_sequence_index_for_table_row(
            self.skill_long_detail_table, source_row
        )
        target = self._timeline_sequence_index_for_table_row(
            self.skill_long_detail_table, target_row
        )
        if source >= 0 and target >= 0:
            self._skill_long_move_segment(source, target)

    def _skill_long_candidate_reference_time(self) -> float:
        index = int(getattr(self, "_skill_long_selected_index", -1))
        segments = [
            row for row in getattr(self, "_skill_long_last_segments", [])
            if norm(row.get("skill")) and not row.get("timeline_hidden")
        ]
        if 0 <= index < len(segments):
            return max(0.0, as_number(segments[index].get("end")))
        return 0.0

    def _skill_long_add_named_skill(self, skill: str) -> None:
        skill = norm(skill)
        if not skill:
            return
        sequence = self._skill_long_sequence_for_editing()
        row = int(getattr(self, "_skill_long_selected_index", -1))
        insert_at = row + 1 if 0 <= row < len(sequence) else len(sequence)
        sequence.insert(insert_at, skill)
        self._skill_long_selected_index = insert_at
        self._skill_long_set_manual_sequence(sequence)

    def _render_skill_long_candidates(self, metrics: list[dict[str, Any]]) -> None:
        self._render_timeline_candidates(
            self.skill_long_candidate_layout,
            metrics,
            list(getattr(self, "_skill_long_last_segments", [])),
            self._skill_long_candidate_reference_time(),
            self._skill_long_add_named_skill,
        )

    def _skill_long_move_segment(self, old_index: int, new_index: int) -> None:
        sequence = self._skill_long_sequence_for_editing()
        if not (0 <= old_index < len(sequence) and 0 <= new_index < len(sequence)):
            return
        skill = sequence.pop(old_index)
        sequence.insert(new_index, skill)
        sequence = self._skill_burst_optimize_dragged_sequence(
            sequence,
            new_index,
            getattr(self, "_skill_long_last_all_metrics", []),
            getattr(self, "_skill_long_last_support_actions", []),
            as_number(getattr(self, "_skill_long_last_window", 0.0)),
        )
        self._skill_long_selected_index = new_index
        self._skill_long_set_manual_sequence(sequence)

    def _skill_long_detail_combo_changed(self, row: int, text: str) -> None:
        choice = norm(text)
        sequence = self._skill_long_sequence_for_editing()
        if not 0 <= row < len(sequence):
            return
        if choice == "↑ 上移一位" and row > 0:
            skill = sequence.pop(row)
            sequence.insert(row - 1, skill)
            self._skill_long_selected_index = row - 1
        elif choice == "↓ 下移一位" and row + 1 < len(sequence):
            skill = sequence.pop(row)
            sequence.insert(row + 1, skill)
            self._skill_long_selected_index = row + 1
        elif choice == "删除此项":
            sequence.pop(row)
            self._skill_long_selected_index = min(row, len(sequence) - 1)
        elif choice and choice != sequence[row]:
            sequence[row] = choice
            self._skill_long_selected_index = row
        else:
            return
        self._skill_long_set_manual_sequence(sequence)

    def _skill_long_restore_auto(self) -> None:
        self._skill_long_manual_sequence = None
        self._skill_long_manual_job = norm(self.second_job.currentText())
        self._skill_long_selected_index = -1
        self.refresh()
