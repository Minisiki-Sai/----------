from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from ..live_dps.critical_stats import is_critical_rate_sample



from ..live_dps.cast_timeline import (
    _TimelineOccurrence,
    _LiveCastTimingResolver,
    _timeline_skill_key,
    _timeline_skill_name,
    _timeline_skill_display_name,
    _timeline_skill_family,
    _is_timeline_periodic_damage,
    _timeline_damage_hit_clusters,
    _timeline_missing_combo_hits,
    _timeline_unmatched_damage_hits,
    _timeline_inferred_casts_from_damage,
    _timeline_events_with_damage_fallback,
    _boss_timeline_events_for_windows,
    _build_timeline_occurrences,
    _timeline_score_cast_events,
)















_TIMELINE_HIT_CLUSTER_GAP_MS = 750
_TIMELINE_HIT_TO_LATE_CAST_MS = 750
_TIMELINE_MIN_CAST_DAMAGE_WINDOW_MS = 3000
_BOSS_TIMELINE_CONTEXT_SECONDS = 30.0
_BOSS_TIMELINE_PREP_SECONDS = 3.0
_TIMELINE_NON_ACTION_DAMAGE_METHODS = {
    "cast_instance_match",
    "continuation_window",
    "healing_cast",
    "learned_cast_profile",
    "supplemental_cast_owner",
    "supplemental_same_packet",
    "summon_owner",
    "sustained_cast_window",
    "status_damage_packet",
    "appended_status_first_hit",
    "unique_active_cast",
}


















class MultiSelectSkillFilter(QToolButton):
    """Compact checkbox menu used by every live skill timeline."""

    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._options: list[tuple[str, str]] = []
        self._selected: set[str] = set()
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setMinimumWidth(165)
        self.setMaximumWidth(260)
        self._refresh_text()

    def selected_values(self) -> frozenset[str]:
        return frozenset(self._selected)

    def set_options(self, options: list[tuple[str, str]]) -> None:
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for value, label in options:
            key = str(value or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append((key, str(label or key)))
        self._options = normalized
        self._selected.intersection_update(seen)
        self._rebuild_menu()
        self._refresh_text()

    def clear_selection(self) -> None:
        if not self._selected:
            return
        self._selected.clear()
        self._sync_checks()
        self._refresh_text()
        self.selectionChanged.emit()

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        host = QWidget(self._menu)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        header = QHBoxLayout()
        all_button = QPushButton("全部技能")
        all_button.setFixedHeight(26)
        all_button.clicked.connect(self.clear_selection)
        header.addWidget(all_button)
        header.addStretch(1)
        header.addWidget(QLabel("可多选"))
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(220)
        scroll.setMaximumHeight(320)
        options_host = QWidget()
        options_layout = QVBoxLayout(options_host)
        options_layout.setContentsMargins(2, 2, 2, 2)
        options_layout.setSpacing(2)
        for value, label in self._options:
            checkbox = QCheckBox(label)
            checkbox.setProperty("skillFilterValue", value)
            checkbox.setChecked(value in self._selected)
            checkbox.toggled.connect(
                lambda checked, key=value: self._option_toggled(key, checked)
            )
            options_layout.addWidget(checkbox)
        if not self._options:
            empty = QLabel("当前没有可筛选技能")
            empty.setStyleSheet("color:#718087; padding:6px;")
            options_layout.addWidget(empty)
        options_layout.addStretch(1)
        scroll.setWidget(options_host)
        layout.addWidget(scroll)
        done_button = QPushButton("完成")
        done_button.setFixedHeight(26)
        done_button.clicked.connect(self._menu.close)
        layout.addWidget(done_button)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(host)
        self._menu.addAction(action)

    def _option_toggled(self, value: str, checked: bool) -> None:
        if checked:
            self._selected.add(value)
        else:
            self._selected.discard(value)
        self._refresh_text()
        self.selectionChanged.emit()

    def _sync_checks(self) -> None:
        for checkbox in self._menu.findChildren(QCheckBox):
            value = str(checkbox.property("skillFilterValue") or "")
            checkbox.blockSignals(True)
            checkbox.setChecked(value in self._selected)
            checkbox.blockSignals(False)

    def _refresh_text(self) -> None:
        if not self._selected:
            self.setText("全部技能")
            self.setToolTip("未勾选技能时显示全部；可同时勾选多个技能")
            return
        labels = [
            label for value, label in self._options if value in self._selected
        ]
        self.setText(labels[0] if len(labels) == 1 else f"已选 {len(labels)} 项")
        self.setToolTip("、".join(labels))


class TeamSkillTimeline(QWidget):
    """A pannable, player-laned view of server-confirmed skill damage events."""

    MAX_WINDOW_SECONDS = 600.0
    viewChanged = Signal(float, float, float)
    timeHovered = Signal(float)
    intervalSelectionChanged = Signal(float, float, str)

    def __init__(
        self, store: DataStore | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._timing_resolver = _LiveCastTimingResolver(store)
        self._events: tuple[CombatEvent | SkillCastEvent, ...] = ()
        self._occurrences: list[_TimelineOccurrence] = []
        self._latest_occurrence_by_skill: dict[
            tuple[str, str], _TimelineOccurrence
        ] = {}
        self._origin_ms = 0
        self._event_extent_seconds = 0.0
        self._extent_seconds = 0.0
        self._window_seconds = 30.0
        self._pixels_per_second = 30.0
        self._view_start = 0.0
        self._follow_latest = True
        self._player_filter = ""
        self._skill_filters: frozenset[str] = frozenset()
        self._filtered_events_cache: tuple[
            CombatEvent | SkillCastEvent, ...
        ] | None = None
        self._filtered_event_timestamps: tuple[int, ...] = ()
        self._filtered_occurrences_cache: list[_TimelineOccurrence] | None = None
        self._dragging = False
        self._drag_origin_x = 0.0
        self._drag_origin_start = 0.0
        self._drag_distance = 0.0
        self._hover_position: QPointF | None = None
        self._range_selection_enabled = False
        self._selecting_range = False
        self._selection_anchor_seconds = 0.0
        self._selection_range: tuple[float, float] | None = None
        self._marker_rects: list[tuple[QRectF, _TimelineOccurrence]] = []
        self._player_details: dict[str, tuple[str, str]] = {}
        self._target_details: dict[str, str] = {}
        self.setMinimumHeight(320)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "鼠标悬停查看技能详情；拖动平移；Shift+拖动选择统计区间；双击返回最新记录"
        )

    @staticmethod
    def _skill_color(skill_key: str) -> QColor:
        return SkillBurstTimelineWidget.skill_color(skill_key)

    def set_events(
        self,
        events: tuple[CombatEvent | SkillCastEvent, ...],
        elapsed_seconds: float = 0.0,
        *,
        origin_ms: int | None = None,
    ) -> None:
        previous_events = self._events
        previous_origin = self._origin_ms
        new_origin = (
            max(0, int(origin_ms))
            if origin_ms is not None
            else min((event.timestamp_ms for event in events), default=0)
        )
        can_append = (
            len(events) >= len(previous_events)
            and new_origin == previous_origin
            and (
                not previous_events
                or events[len(previous_events) - 1] == previous_events[-1]
            )
        )
        self._events = events
        self._origin_ms = new_origin
        self._invalidate_filter_cache()
        if not can_append:
            self._occurrences = _build_timeline_occurrences(
                events, self._origin_ms, self._timing_resolver
            )
            self._latest_occurrence_by_skill = {
                (row.event.source_id, _timeline_skill_key(row.event)): row
                for row in self._occurrences
            }
        elif len(events) > len(previous_events):
            for event in events[len(previous_events) :]:
                self._append_event_occurrence(event)
        latest_event = max(
            ((event.timestamp_ms - self._origin_ms) / 1000.0 for event in events),
            default=0.0,
        )
        latest_occurrence_end = max(
            (row.end_seconds for row in self._occurrences), default=0.0
        )
        self._event_extent_seconds = max(0.0, latest_event, latest_occurrence_end)
        self._extent_seconds = max(
            self._event_extent_seconds,
            float(elapsed_seconds),
        )
        self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self.maximum_view_start()
        else:
            self._view_start = min(self._view_start, self.maximum_view_start())
        self._emit_view_changed()
        self.update()

    def set_elapsed_seconds(self, elapsed_seconds: float) -> None:
        extent_seconds = max(self._event_extent_seconds, float(elapsed_seconds))
        if abs(extent_seconds - self._extent_seconds) <= 1e-6:
            return
        self._extent_seconds = extent_seconds
        self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self.maximum_view_start()
        else:
            self._view_start = min(self._view_start, self.maximum_view_start())
        self._emit_view_changed()
        self.update()

    def _append_event_occurrence(
        self, event: CombatEvent | SkillCastEvent
    ) -> None:
        seconds = max(0.0, (event.timestamp_ms - self._origin_ms) / 1000.0)
        if isinstance(event, SkillCastEvent):
            self._occurrences.append(
                self._timing_resolver.occurrence(event, seconds)
            )
            return
        key = (event.source_id, _timeline_skill_key(event))
        current = self._latest_occurrence_by_skill.get(key)
        if current is not None and seconds - current.end_seconds <= 0.75:
            current.end_seconds = max(current.end_seconds, seconds)
            current.damage += 0 if event.healing else event.amount
            current.healing += event.amount if event.healing else 0
            current.hits += 1
            current.critical_hits += int(
                is_critical_rate_sample(event) and event.critical
            )
            return
        current = _TimelineOccurrence(
            event=event,
            start_seconds=seconds,
            end_seconds=seconds,
            damage=0 if event.healing else event.amount,
            healing=event.amount if event.healing else 0,
            hits=1,
            critical_hits=int(
                is_critical_rate_sample(event) and event.critical
            ),
        )
        self._latest_occurrence_by_skill[key] = current
        self._occurrences.append(current)

    def set_filters(
        self,
        player_id: str = "",
        skill_keys: str | set[str] | frozenset[str] = frozenset(),
    ) -> None:
        normalized = (
            frozenset({skill_keys})
            if isinstance(skill_keys, str) and skill_keys
            else frozenset(skill_keys)
            if not isinstance(skill_keys, str)
            else frozenset()
        )
        if self._player_filter == player_id and self._skill_filters == normalized:
            return
        self._player_filter = player_id
        self._skill_filters = normalized
        self._invalidate_filter_cache()
        self.update()

    def _invalidate_filter_cache(self) -> None:
        self._filtered_events_cache = None
        self._filtered_event_timestamps = ()
        self._filtered_occurrences_cache = None

    def set_player_details(
        self, details: dict[str, tuple[str, str]]
    ) -> None:
        self._player_details = dict(details)
        self.update()

    def set_target_details(self, details: dict[str, str]) -> None:
        self._target_details = dict(details)

    def player_display(
        self, source_id: str, fallback_name: str, fallback_job: str
    ) -> tuple[str, str]:
        name, job = self._player_details.get(
            source_id, (fallback_name, fallback_job)
        )
        return name or fallback_name or source_id, job or fallback_job

    def _update_window_seconds(self) -> None:
        chart_width = max(1.0, self._chart_rect().width())
        self._window_seconds = min(
            self.MAX_WINDOW_SECONDS,
            max(5.0, chart_width / max(1.0, self._pixels_per_second)),
        )

    def set_pixels_per_second(self, value: int | float) -> None:
        previous_center = self._view_start + self._window_seconds / 2
        was_following = self._follow_latest
        self._pixels_per_second = max(2.0, min(45.0, float(value)))
        self._update_window_seconds()
        if was_following:
            self._view_start = self.maximum_view_start()
        else:
            self._view_start = max(
                0.0,
                min(
                    previous_center - self._window_seconds / 2,
                    self.maximum_view_start(),
                ),
            )
        self._emit_view_changed()
        self.update()

    def resizeEvent(self, event) -> None:
        was_following = self._follow_latest
        self._update_window_seconds()
        self._view_start = (
            self.maximum_view_start()
            if was_following
            else min(self._view_start, self.maximum_view_start())
        )
        self._emit_view_changed()
        super().resizeEvent(event)

    def filtered_events(self) -> tuple[CombatEvent | SkillCastEvent, ...]:
        if self._filtered_events_cache is None:
            self._filtered_events_cache = tuple(
                event
                for event in self._events
                if (
                    not self._player_filter
                    or event.source_id == self._player_filter
                )
                and (
                    not self._skill_filters
                    or _timeline_skill_key(event) in self._skill_filters
                )
            )
            self._filtered_event_timestamps = tuple(
                event.timestamp_ms for event in self._filtered_events_cache
            )
        return self._filtered_events_cache

    def visible_events(self) -> tuple[CombatEvent | SkillCastEvent, ...]:
        start_ms = self._origin_ms + round(self._view_start * 1000)
        end_ms = start_ms + round(self._window_seconds * 1000)
        events = self.filtered_events()
        start_index = bisect_left(self._filtered_event_timestamps, start_ms)
        end_index = bisect_right(self._filtered_event_timestamps, end_ms)
        return events[start_index:end_index]

    def maximum_view_start(self) -> float:
        return max(0.0, self._extent_seconds - self._window_seconds)

    def set_view_start(self, value: float) -> None:
        maximum = self.maximum_view_start()
        self._view_start = max(0.0, min(float(value), maximum))
        self._follow_latest = maximum - self._view_start <= 0.05
        self._emit_view_changed()
        self.update()

    def _emit_view_changed(self) -> None:
        self.viewChanged.emit(
            self._view_start,
            self.maximum_view_start(),
            self._window_seconds,
        )

    def _chart_rect(self) -> QRectF:
        return QRectF(
            148,
            42,
            max(1, self.width() - 160),
            max(1, self.height() - 68),
        )

    def set_range_selection_enabled(self, enabled: bool) -> None:
        self._range_selection_enabled = bool(enabled)
        self._selecting_range = False
        self.update()

    def clear_interval_selection(self) -> None:
        self._selection_range = None
        self.intervalSelectionChanged.emit(0.0, 0.0, "区间：未选择")
        self.update()

    def _seconds_at_x(self, x: float) -> float:
        chart = self._chart_rect()
        ratio = max(0.0, min(1.0, (float(x) - chart.left()) / chart.width()))
        return max(
            0.0,
            min(self._extent_seconds, self._view_start + ratio * self._window_seconds),
        )

    def _selection_summary(self) -> str:
        if self._selection_range is None:
            return "区间：未选择"
        start, end = sorted(self._selection_range)
        duration = max(0.0, end - start)
        rows = [
            row
            for row in self._filtered_occurrences()
            if start <= row.start_seconds <= end
        ]
        damage = sum(row.damage for row in rows)
        healing = sum(row.healing for row in rows)
        hits = sum(row.hits for row in rows)
        return (
            f"区间 {start:.2f}s - {end:.2f}s（{duration:.2f}s）  "
            f"技能 {len(rows)}  Hit {hits}  伤害 {_compact_number(damage)}  "
            f"治疗 {_compact_number(healing)}"
        )

    def _emit_interval_selection(self) -> None:
        if self._selection_range is None:
            self.intervalSelectionChanged.emit(0.0, 0.0, "区间：未选择")
            return
        start, end = sorted(self._selection_range)
        self.intervalSelectionChanged.emit(start, end, self._selection_summary())

    def _draw_interval_selection(self, painter: QPainter, chart: QRectF) -> None:
        if self._selection_range is None:
            return
        start, end = sorted(self._selection_range)
        visible_start = max(start, self._view_start)
        visible_end = min(end, self._view_start + self._window_seconds)
        if visible_end <= visible_start:
            return
        left = chart.left() + chart.width() * (
            (visible_start - self._view_start) / self._window_seconds
        )
        right = chart.left() + chart.width() * (
            (visible_end - self._view_start) / self._window_seconds
        )
        fill = QColor("#0f8fa6")
        fill.setAlpha(36)
        painter.fillRect(QRectF(left, chart.top(), right - left, chart.height()), fill)
        painter.setPen(QPen(QColor("#0f8fa6"), 1, Qt.DashLine))
        painter.drawLine(left, chart.top(), left, chart.bottom())
        painter.drawLine(right, chart.top(), right, chart.bottom())
        if right - left >= 100.0:
            summary_rect = QRectF(
                left + 4.0,
                chart.top() + 4.0,
                right - left - 8.0,
                20.0,
            )
            painter.fillRect(summary_rect, QColor("#e7f5f7"))
            painter.setPen(QColor("#0f6575"))
            painter.drawText(
                summary_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                painter.fontMetrics().elidedText(
                    self._selection_summary(),
                    Qt.ElideRight,
                    max(1, int(summary_rect.width() - 8.0)),
                ),
            )

    def _draw_crosshair(self, painter: QPainter, chart: QRectF) -> None:
        if self._hover_position is None or not chart.contains(self._hover_position):
            return
        x = max(chart.left(), min(chart.right(), self._hover_position.x()))
        seconds = self._seconds_at_x(x)
        painter.setPen(QPen(QColor("#0f8fa6"), 1, Qt.DashLine))
        painter.drawLine(x, chart.top(), x, chart.bottom())
        label_rect = QRectF(
            max(chart.left(), min(chart.right() - 64.0, x - 32.0)),
            chart.top() - 25.0,
            64.0,
            20.0,
        )
        painter.fillRect(label_rect, QColor("#e7f5f7"))
        painter.setPen(QColor("#0f6575"))
        painter.drawText(label_rect, Qt.AlignCenter, f"{seconds:.2f}s")

    def _grid_step(self) -> int:
        minimum_seconds = 54.0 / max(1.0, self._pixels_per_second)
        for candidate in (1, 2, 5, 10, 20, 30, 60, 120):
            if candidate >= minimum_seconds:
                return candidate
        return 120

    def _filtered_occurrences(self) -> list[_TimelineOccurrence]:
        if self._filtered_occurrences_cache is None:
            self._filtered_occurrences_cache = [
                row
                for row in self._occurrences
                if (
                    not self._player_filter
                    or row.event.source_id == self._player_filter
                )
                and (
                    not self._skill_filters
                    or _timeline_skill_key(row.event) in self._skill_filters
                )
            ]
        return self._filtered_occurrences_cache

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._chart_rect().contains(event.position()):
            if self._range_selection_enabled or bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                seconds = self._seconds_at_x(event.position().x())
                self._selecting_range = True
                self._selection_anchor_seconds = seconds
                self._selection_range = (seconds, seconds)
                self.setCursor(Qt.CrossCursor)
                QToolTip.hideText()
                self.update()
                event.accept()
                return
            self._dragging = True
            self._drag_origin_x = event.position().x()
            self._drag_origin_start = self._view_start
            self._drag_distance = 0.0
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def _occurrence_at(self, position) -> tuple[QRectF, _TimelineOccurrence] | None:
        for rect, occurrence in reversed(self._marker_rects):
            if rect.adjusted(-2, -2, 2, 2).contains(position):
                return rect, occurrence
        return None

    def _occurrence_tooltip_text(self, occurrence: _TimelineOccurrence) -> str:
        event_row = occurrence.event
        if isinstance(event_row, SkillCastEvent):
            speed_detail = (
                f"，动作速度 +{occurrence.action_speed_percent:g}%"
                if occurrence.action_speed_percent > 0
                else ""
            )
            detail = (
                f"施法 {occurrence.cast_time:.3f}s{speed_detail}"
                f"  |  {event_row.packet_kind}"
            )
        else:
            amount = occurrence.healing if event_row.healing else occurrence.damage
            kind = "治疗" if event_row.healing else "伤害"
            target_detail = self._target_details.get(
                event_row.target_id,
                (
                    f"其他目标（{event_row.target_id}）"
                    if event_row.target_id
                    else "未提供目标"
                ),
            )
            detail = (
                f"{kind} {_compact_number(amount)}  |  {occurrence.hits} hit"
                f"  |  暴击 {occurrence.critical_hits}\n"
                f"命中：{target_detail}\n"
                f"归属：{event_row.attribution_method or 'recorded'}"
            )
        display_name, display_job = self.player_display(
            event_row.source_id, event_row.source_name, event_row.job
        )
        return (
            f"{occurrence.start_seconds:.3f}s  {display_name}"
            f"（{display_job or '未知职业'}）\n"
            f"{event_row.skill_name}  |  {detail}"
        )

    def _show_occurrence_tooltip(self, position, global_position) -> bool:
        marker = self._occurrence_at(position)
        if marker is None:
            return False
        _rect, occurrence = marker
        QToolTip.showText(
            global_position,
            self._occurrence_tooltip_text(occurrence),
            self,
        )
        return True

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            if self._show_occurrence_tooltip(event.pos(), event.globalPos()):
                event.accept()
                return True
            if self._chart_rect().contains(event.pos()):
                QToolTip.showText(
                    event.globalPos(),
                    "拖动平移；Shift+拖动选择统计区间；双击返回最新记录",
                    self,
                )
                event.accept()
                return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:
        if self._selecting_range:
            seconds = self._seconds_at_x(event.position().x())
            self._selection_range = tuple(
                sorted((self._selection_anchor_seconds, seconds))
            )
            self._hover_position = QPointF(event.position())
            self._emit_interval_selection()
            QToolTip.showText(
                event.globalPosition().toPoint(), self._selection_summary(), self
            )
            self.update()
            event.accept()
            return
        if self._dragging:
            self._drag_distance = max(
                self._drag_distance,
                abs(event.position().x() - self._drag_origin_x),
            )
            delta = (
                (event.position().x() - self._drag_origin_x)
                * self._window_seconds
                / max(1.0, self._chart_rect().width())
            )
            self.set_view_start(self._drag_origin_start - delta)
            event.accept()
            return
        if self._chart_rect().contains(event.position()):
            self._hover_position = QPointF(event.position())
            self.timeHovered.emit(self._seconds_at_x(event.position().x()))
            self.update()
        else:
            self._hover_position = None
        if self._show_occurrence_tooltip(
            event.position(), event.globalPosition().toPoint()
        ):
            event.accept()
            return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._selecting_range and event.button() == Qt.MouseButton.LeftButton:
            self._selecting_range = False
            seconds = self._seconds_at_x(event.position().x())
            self._selection_range = tuple(
                sorted((self._selection_anchor_seconds, seconds))
            )
            self._emit_interval_selection()
            QToolTip.showText(
                event.globalPosition().toPoint(), self._selection_summary(), self
            )
            self.setCursor(
                Qt.CrossCursor
                if self._range_selection_enabled
                else Qt.OpenHandCursor
            )
            self.update()
            event.accept()
            return
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._selecting_range:
            self._hover_position = None
            QToolTip.hideText()
            self.update()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._follow_latest = True
            self.set_view_start(self.maximum_view_start())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f8fafc"))
        chart = self._chart_rect()
        occurrences = self._filtered_occurrences()
        player_order: list[str] = []
        player_names: dict[str, str] = {}
        for row in occurrences:
            if row.event.source_id not in player_names:
                player_order.append(row.event.source_id)
                player_names[row.event.source_id] = self.player_display(
                    row.event.source_id, row.event.source_name, row.event.job
                )[0]
        self._marker_rects = []
        if not player_order:
            painter.setPen(QColor("#64748b"))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无符合筛选条件的技能记录")
            self._draw_interval_selection(painter, chart)
            self._draw_crosshair(painter, chart)
            return

        lane_height = chart.height() / max(1, len(player_order))
        lane_index = {source_id: index for index, source_id in enumerate(player_order)}
        window_start = self._view_start
        window_end = window_start + self._window_seconds
        grid_step = self._grid_step()
        first_grid = int(window_start // grid_step) * grid_step
        painter.setPen(QPen(QColor("#94a3b8"), 1))
        painter.drawLine(chart.left(), chart.top(), chart.right(), chart.top())
        for second in range(first_grid, int(window_end) + grid_step, grid_step):
            if second < window_start - 1e-6 or second > window_end + 1e-6:
                continue
            x = chart.left() + chart.width() * (second - window_start) / self._window_seconds
            painter.setPen(QPen(QColor("#d7e0ea"), 1))
            painter.drawLine(x, chart.top(), x, chart.bottom())
            painter.setPen(QPen(QColor("#94a3b8"), 1))
            painter.drawLine(x, chart.top() - 5, x, chart.top() + 5)
            painter.setPen(QColor("#475569"))
            painter.drawText(
                QRectF(x - 24, 13, 48, 20),
                Qt.AlignCenter,
                f"{second}s",
            )

        painter.setPen(QColor("#334155"))
        painter.drawText(
            QRectF(12, chart.top() - 11, 124, 22),
            Qt.AlignRight | Qt.AlignVCenter,
            "玩家 / 职业",
        )
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawLine(chart.left() - 1, chart.top(), chart.left() - 1, chart.bottom())

        for index, source_id in enumerate(player_order):
            top = chart.top() + index * lane_height
            lane_fill = QColor("#ffffff" if index % 2 == 0 else "#f6f9fb")
            painter.fillRect(
                QRectF(chart.left(), top, chart.width(), lane_height),
                lane_fill,
            )
            painter.setPen(QColor("#e2e8f0"))
            painter.drawLine(chart.left(), top + lane_height, chart.right(), top + lane_height)
            player_name, player_job = self.player_display(source_id, "", "")
            label = player_name
            if player_job:
                label = f"{player_name} · {player_job}"
            label = painter.fontMetrics().elidedText(label, Qt.ElideLeft, 132)
            painter.setPen(QColor("#334155"))
            painter.drawText(
                QRectF(8, top, 128, lane_height),
                Qt.AlignRight | Qt.AlignVCenter,
                label,
            )

        self._draw_interval_selection(painter, chart)

        painter.save()
        painter.setClipRect(chart)
        for row in occurrences:
            if row.end_seconds < window_start or row.start_seconds > window_end:
                continue
            index = lane_index[row.event.source_id]
            x = chart.left() + chart.width() * (row.start_seconds - window_start) / self._window_seconds
            end_x = chart.left() + chart.width() * (row.end_seconds - window_start) / self._window_seconds
            width = max(10.0, end_x - x)
            block_height = max(14.0, min(28.0, lane_height - 8.0))
            top = chart.top() + index * lane_height + (lane_height - block_height) / 2
            rect = QRectF(x, top, width, block_height)
            color = self._skill_color(_timeline_skill_key(row.event))
            painter.setPen(QPen(color.darker(130), 1))
            painter.setBrush(color.lighter(112))
            painter.drawRoundedRect(rect, 5, 5)
            self._marker_rects.append((rect, row))
            if rect.width() >= 26:
                label = painter.fontMetrics().elidedText(
                    _timeline_skill_display_name(row.event),
                    Qt.ElideRight,
                    max(8, int(rect.width() - 10)),
                )
                painter.setPen(QColor("#0f172a"))
                painter.drawText(
                    rect.adjusted(5, 0, -5, 0),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )
        painter.restore()
        self._draw_crosshair(painter, chart)

        painter.setPen(QColor("#64748b"))
        painter.drawText(
            QRectF(chart.left(), chart.bottom() + 5, 100, 18),
            Qt.AlignLeft,
            f"窗口 {window_start:.1f}s",
        )
        painter.drawText(
            QRectF(chart.right() - 100, chart.bottom() + 5, 100, 18),
            Qt.AlignRight,
            f"{window_end:.1f}s",
        )


__all__ = tuple(name for name in globals() if not name.startswith("__"))
