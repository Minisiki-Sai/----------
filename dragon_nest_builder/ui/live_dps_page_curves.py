from __future__ import annotations

from .live_dps_page_core import *

class _ReplayLoadWorker(QObject):
    progress = Signal(int, str)
    loaded = Signal(object, str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        path: Path,
        data_root: Path,
        known_players: dict[str, dict[str, str]],
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.path = path
        self.data_root = data_root
        self.known_players = known_players
        self.cancel_event = cancel_event

    def _decode_progress(self, processed: int, total: int) -> None:
        if self.cancel_event.is_set():
            raise InterruptedError("Replay loading was cancelled")
        percent = max(1, min(90, round(processed * 90 / max(1, total))))
        self.progress.emit(percent, f"正在读取并解码：{self.path.name}")

    @Slot()
    def run(self) -> None:
        source = None
        cache_identity: ReplayCacheIdentity | None = None
        try:
            suffix = self.path.suffix.lower()
            self.progress.emit(1, f"正在读取：{self.path.name}")
            if suffix == ".dpscap":
                self.progress.emit(2, "正在核对记录与解析器版本指纹...")
                try:
                    cache_identity = replay_cache_identity(
                        self.path,
                        self.data_root,
                        self.known_players,
                    )
                    cached_aggregator = ReplayDiskCache().load(cache_identity)
                except Exception:
                    cache_identity = None
                    cached_aggregator = None
                if cached_aggregator is not None:
                    if getattr(cached_aggregator, "_replay_snapshot", None) is None:
                        self.progress.emit(98, "正在生成完整统计快照...")
                        cached_aggregator._replay_snapshot = (
                            cached_aggregator.snapshot(include_events=True)
                        )
                        if cache_identity is not None:
                            ReplayDiskCache().save_async(
                                cache_identity, cached_aggregator
                            )
                    self.progress.emit(100, "版本指纹一致，已恢复解析缓存")
                    self.loaded.emit(
                        cached_aggregator,
                        f"已载入统计记录（解析缓存）：{self.path.name}",
                    )
                    return
                source = V576DiagnosticReplaySource(
                    self.path,
                    self.data_root,
                    self.known_players,
                    progress_callback=self._decode_progress,
                    cancel_check=self.cancel_event.is_set,
                )
                status = f"已载入统计记录：{self.path.name}"
            elif suffix == ".pcapng":
                source = V576PcapReplaySource(
                    self.path,
                    self.data_root,
                    self.known_players,
                )
                status = f"已载入抓包：{self.path.name}"
            else:
                source = JsonlReplaySource(self.path)
                status = f"已载入：{self.path.name}"

            if self.cancel_event.is_set():
                raise InterruptedError("Replay loading was cancelled")
            aggregator = CombatAggregator()
            if suffix == ".dpscap" and hasattr(source, "iter_batches"):
                aggregated_events = 0
                for batch in source.iter_batches():
                    if self.cancel_event.is_set():
                        raise InterruptedError("Replay loading was cancelled")
                    for event in batch:
                        aggregator.ingest(event)
                    aggregated_events += len(batch)
                    total_events = max(
                        1,
                        int(getattr(source, "total_event_count", 0)),
                    )
                    percent = 90 + min(
                        9,
                        round(aggregated_events * 9 / total_events),
                    )
                    self.progress.emit(
                        percent,
                        f"正在流式汇总：{aggregated_events:,}/{total_events:,} 条事件",
                    )
            else:
                events = source.poll()
                total_events = max(1, len(events))
                for index, event in enumerate(events, start=1):
                    if index % 1000 == 0:
                        if self.cancel_event.is_set():
                            raise InterruptedError("Replay loading was cancelled")
                        percent = 90 + min(9, round(index * 9 / total_events))
                        self.progress.emit(
                            percent,
                            f"正在汇总统计：{index:,}/{len(events):,} 条事件",
                        )
                    aggregator.ingest(event)
            self.progress.emit(98, "正在生成完整统计快照...")
            if getattr(aggregator, "_replay_snapshot", None) is None:
                aggregator._replay_snapshot = aggregator.snapshot(
                    include_events=True
                )
            if cache_identity is not None:
                ReplayDiskCache().save_async(cache_identity, aggregator)
                self.progress.emit(100, "载入完成，版本缓存正在后台保存")
            else:
                self.progress.emit(100, "载入完成")
            self.loaded.emit(aggregator, status)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if source is not None:
                source.close()


def _rolling_damage_series(
    series: tuple[tuple[int, int], ...],
    window_seconds: int = 5,
) -> tuple[tuple[int, float], ...]:
    """Return a causal rolling DPS series with an exact numeric meaning."""
    if not series:
        return ()
    window = max(1, int(window_seconds))
    values = {int(second): float(damage) for second, damage in series}
    last_second = max(values)
    running = 0.0
    result: list[tuple[int, float]] = []
    for second in range(0, last_second + 1):
        running += values.get(second, 0.0)
        expired = second - window
        if expired >= 0:
            running -= values.get(expired, 0.0)
        denominator = min(window, second + 1)
        result.append((second, running / denominator))
    return tuple(result)


class DamageCurve(QWidget):
    viewChanged = Signal(float, float, float)
    intervalSelectionChanged = Signal(float, float, str)
    AUTO_WINDOW_STEPS = (15.0, 30.0, 60.0, 90.0, 120.0, 180.0, 240.0, 300.0)
    MAX_WINDOW_SECONDS = AUTO_WINDOW_STEPS[-1]
    BOSS_PHASE_COLORS = (
        "#d8eef4",
        "#f8e8cf",
        "#dfeedd",
        "#f4dfe5",
        "#e7e1f4",
        "#dfe8f5",
        "#eee3d7",
        "#dcefeb",
    )
    BOSS_PHASE_BORDER_COLORS = (
        "#65a9ba",
        "#c89243",
        "#75a671",
        "#c27b8d",
        "#8e79b9",
        "#6f91bd",
        "#a78361",
        "#5d9f93",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: tuple[tuple[int, int], ...] = ()
        self._series_map: dict[int, float] = {}
        self._rolling_series: tuple[tuple[int, float], ...] = ()
        self._rolling_map: dict[int, float] = {}
        self._smoothing_seconds = 5
        self._global_max_damage = 1.0
        self._axis_max_damage = 1.0
        self._boss_phases: tuple[
            tuple[str, tuple[tuple[float, float], ...]], ...
        ] = ()
        self._extent_seconds = 0.0
        self._window_seconds = self.AUTO_WINDOW_STEPS[0]
        self._view_start = 0.0
        self._follow_latest = True
        self._pixels_per_second = 30.0
        self._dragging = False
        self._drag_origin_x = 0.0
        self._drag_origin_start = 0.0
        self._drag_distance = 0.0
        self._hover_position: QPointF | None = None
        self._range_selection_enabled = False
        self._selecting_range = False
        self._selection_anchor_seconds = 0.0
        self._selection_range: tuple[float, float] | None = None
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "鼠标悬停查看精确数值；拖动平移；Shift+拖动选择统计区间"
        )

    @classmethod
    def automatic_window_seconds(cls, elapsed_seconds: float) -> float:
        elapsed = max(0.0, float(elapsed_seconds))
        for window in cls.AUTO_WINDOW_STEPS:
            if elapsed <= window:
                return window
        return cls.MAX_WINDOW_SECONDS

    @staticmethod
    def _grid_step(window_seconds: float) -> int:
        if window_seconds <= 15:
            return 2
        if window_seconds <= 30:
            return 5
        if window_seconds <= 60:
            return 10
        if window_seconds <= 90:
            return 15
        if window_seconds <= 120:
            return 20
        if window_seconds <= 180:
            return 30
        return 60

    def set_series(
        self,
        series: tuple[tuple[int, int], ...],
        elapsed_seconds: float | None = None,
    ) -> None:
        self._series = series
        self._series_map = {
            int(second): float(damage) for second, damage in series
        }
        self._rebuild_rolling_series()
        latest_sample = float(series[-1][0]) if series else 0.0
        self._extent_seconds = max(
            latest_sample,
            max(0.0, float(elapsed_seconds or 0.0)),
        )
        self._update_window_seconds()
        if not series and self._extent_seconds <= 0:
            self._view_start = 0.0
            self._follow_latest = True
        elif self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        if self._selection_range is not None:
            self._emit_interval_selection()
        self.update()

    def _rebuild_rolling_series(self) -> None:
        self._rolling_series = _rolling_damage_series(
            self._series, self._smoothing_seconds
        )
        self._rolling_map = dict(self._rolling_series)
        self._global_max_damage = max(
            1.0,
            max((value for _second, value in self._rolling_series), default=0.0),
        )
        self._axis_max_damage = _nice_axis_max(self._global_max_damage)

    def set_smoothing_seconds(self, seconds: int) -> None:
        seconds = max(1, int(seconds))
        if seconds == self._smoothing_seconds:
            return
        self._smoothing_seconds = seconds
        self._rebuild_rolling_series()
        if self._selection_range is not None:
            self._emit_interval_selection()
        self.update()

    def smoothing_seconds(self) -> int:
        return self._smoothing_seconds

    def set_range_selection_enabled(self, enabled: bool) -> None:
        self._range_selection_enabled = bool(enabled)
        self._selecting_range = False
        self.setCursor(
            Qt.CursorShape.CrossCursor
            if self._range_selection_enabled
            else Qt.CursorShape.OpenHandCursor
        )

    def clear_interval_selection(self) -> None:
        self._selection_range = None
        self.intervalSelectionChanged.emit(0.0, 0.0, "区间：未选择")
        self.update()

    def _seconds_at_x(self, x: float) -> float:
        chart = self._chart_rect()
        ratio = max(0.0, min(1.0, (float(x) - chart.left()) / chart.width()))
        return max(
            0.0,
            min(
                self._extent_seconds,
                self._view_start + ratio * self._window_seconds,
            ),
        )

    def _selection_summary(self) -> str:
        if self._selection_range is None:
            return "区间：未选择"
        start, end = sorted(self._selection_range)
        duration = max(0.0, end - start)
        if duration <= 1e-6:
            return f"区间：{start:.2f}s"
        damage = 0.0
        for second, value in self._series_map.items():
            overlap = max(
                0.0,
                min(end, float(second) + 1.0) - max(start, float(second)),
            )
            damage += value * overlap
        first_second = max(0, int(math.floor(start)))
        last_second = max(first_second, int(math.ceil(end)))
        peak = max(
            (
                self._rolling_map.get(second, 0.0)
                for second in range(first_second, last_second + 1)
            ),
            default=0.0,
        )
        return (
            f"区间 {start:.2f}s - {end:.2f}s（{duration:.2f}s）  "
            f"总伤害 {_compact_number(damage)}  "
            f"平均DPS {_compact_number(damage / duration)}  "
            f"峰值{self._smoothing_seconds}秒DPS {_compact_number(peak)}"
        )

    def _emit_interval_selection(self) -> None:
        if self._selection_range is None:
            self.intervalSelectionChanged.emit(0.0, 0.0, "区间：未选择")
            return
        start, end = sorted(self._selection_range)
        self.intervalSelectionChanged.emit(start, end, self._selection_summary())

    def set_boss_phases(
        self,
        phases: tuple[
            tuple[str, tuple[tuple[float, float], ...]], ...
        ],
    ) -> None:
        self._boss_phases = phases
        self.update()

    def set_elapsed_seconds(self, elapsed_seconds: float) -> None:
        self.set_series(self._series, elapsed_seconds)

    def set_pixels_per_second(self, value: float) -> None:
        self._pixels_per_second = max(2.0, min(45.0, float(value)))
        self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        self.update()

    def _update_window_seconds(self) -> None:
        self._window_seconds = max(
            5.0, self._chart_rect().width() / self._pixels_per_second
        )

    def resizeEvent(self, event) -> None:
        self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        super().resizeEvent(event)

    def refresh_geometry(self) -> None:
        """Refresh chart metrics after a parent layout changes without a resize event."""
        self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        self.updateGeometry()
        self.update()

    def _chart_rect(self) -> QRectF:
        return QRectF(76, 28, max(1, self.width() - 90), max(1, self.height() - 56))

    def _max_view_start(self) -> float:
        return max(0.0, self._extent_seconds - self._window_seconds)

    def _set_view_start(self, value: float) -> None:
        maximum = self._max_view_start()
        self._view_start = max(0.0, min(float(value), maximum))
        self._follow_latest = maximum - self._view_start <= 0.05
        self._emit_view_changed()
        self.update()

    def set_view_start(self, value: float) -> None:
        self._set_view_start(value)

    def _emit_view_changed(self) -> None:
        self.viewChanged.emit(
            self._view_start,
            self._max_view_start(),
            self._window_seconds,
        )

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._chart_rect().contains(event.position())
        ):
            if self._range_selection_enabled or bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                seconds = self._seconds_at_x(event.position().x())
                self._selecting_range = True
                self._selection_anchor_seconds = seconds
                self._selection_range = (seconds, seconds)
                self.setCursor(Qt.CursorShape.CrossCursor)
                QToolTip.hideText()
                self.update()
                event.accept()
                return
            self._dragging = True
            self._drag_origin_x = event.globalPosition().x()
            self._drag_origin_start = self._view_start
            self._drag_distance = 0.0
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            QToolTip.hideText()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._selecting_range:
            seconds = self._seconds_at_x(event.position().x())
            self._selection_range = tuple(
                sorted((self._selection_anchor_seconds, seconds))
            )
            self._emit_interval_selection()
            self.update()
            event.accept()
            return
        if self._dragging:
            delta_x = event.globalPosition().x() - self._drag_origin_x
            self._drag_distance = max(self._drag_distance, abs(delta_x))
            delta_seconds = (
                delta_x
                * self._window_seconds
                / max(1.0, self._chart_rect().width())
            )
            self._set_view_start(self._drag_origin_start - delta_seconds)
            QToolTip.hideText()
            event.accept()
            return
        chart = self._chart_rect()
        if chart.contains(event.position()) and self._series_map:
            self._hover_position = QPointF(event.position())
            cursor_seconds = self._seconds_at_x(event.position().x())
            second = max(0, round(cursor_seconds))
            raw = self._series_map.get(second, 0.0)
            rolling = self._rolling_map.get(second, 0.0)
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{cursor_seconds:.2f}s\n本秒伤害：{_compact_number(raw)}\n"
                f"{self._smoothing_seconds}秒平滑DPS：{_compact_number(rolling)}",
                self,
            )
            self.update()
        else:
            self._hover_position = None
            QToolTip.hideText()
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_position = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def _draw_boss_phase_background(
        self, painter: QPainter, chart: QRectF
    ) -> None:
        if not self._boss_phases:
            return
        window_start = self._view_start
        window_end = window_start + self._window_seconds
        painter.save()
        painter.setClipRect(chart)
        for phase_index, (name, windows) in enumerate(self._boss_phases):
            fill_color = QColor(
                self.BOSS_PHASE_COLORS[
                    phase_index % len(self.BOSS_PHASE_COLORS)
                ]
            )
            fill_color.setAlpha(185)
            border_color = QColor(
                self.BOSS_PHASE_BORDER_COLORS[
                    phase_index % len(self.BOSS_PHASE_BORDER_COLORS)
                ]
            )
            border_color.setAlpha(190)
            for start, end in windows:
                visible_start = max(window_start, start)
                visible_end = min(window_end, end)
                if visible_start >= visible_end:
                    continue
                left = chart.left() + chart.width() * (
                    (visible_start - window_start) / self._window_seconds
                )
                right = chart.left() + chart.width() * (
                    (visible_end - window_start) / self._window_seconds
                )
                phase_rect = QRectF(
                    left,
                    chart.top(),
                    max(1.0, right - left),
                    chart.height(),
                )
                painter.fillRect(phase_rect, fill_color)
                painter.setPen(QPen(border_color, 1))
                painter.drawLine(left, chart.top(), left, chart.bottom())
                painter.drawLine(right, chart.top(), right, chart.bottom())
                if right - left >= 72:
                    painter.setPen(border_color.darker(125))
                    label = painter.fontMetrics().elidedText(
                        name, Qt.ElideRight, max(20, int(right - left - 12))
                    )
                    painter.drawText(
                        QRectF(left + 6, chart.top() + 5, right - left - 12, 18),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        label,
                    )
        painter.restore()

    def mouseReleaseEvent(self, event) -> None:
        if self._selecting_range and event.button() == Qt.MouseButton.LeftButton:
            self._selecting_range = False
            seconds = self._seconds_at_x(event.position().x())
            self._selection_range = tuple(
                sorted((self._selection_anchor_seconds, seconds))
            )
            if abs(self._selection_range[1] - self._selection_range[0]) <= 0.05:
                self._horizontal_drag_clicked(event)
            self._emit_interval_selection()
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            event.accept()
            return
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if self._drag_distance <= 4.0:
                self._horizontal_drag_clicked(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _horizontal_drag_clicked(self, _event) -> None:
        """Handle a short click without conflating it with horizontal panning."""

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._follow_latest = True
            self._view_start = self._max_view_start()
            self._emit_view_changed()
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _draw_left_axis(self, painter: QPainter, chart: QRectF) -> None:
        axis_max = max(1.0, self._axis_max_damage)
        for index in range(5):
            ratio = index / 4.0
            y = chart.bottom() - chart.height() * ratio
            painter.setPen(QPen(QColor("#dfe8ec"), 1))
            painter.drawLine(chart.left(), y, chart.right(), y)
            painter.setPen(QColor("#526d79"))
            painter.drawText(
                QRectF(2, y - 9, chart.left() - 8, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                _compact_number(axis_max * ratio),
            )
        painter.setPen(QColor("#526d79"))
        painter.drawText(
            QRectF(2, 2, chart.left() - 8, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            "DPS",
        )

    def _draw_time_axis(self, painter: QPainter, chart: QRectF) -> None:
        window_start = self._view_start
        window_end = window_start + self._window_seconds
        grid_step = self._grid_step(self._window_seconds)
        first_grid = (int(window_start) // grid_step + 1) * grid_step
        for second in range(first_grid, int(window_end) + 1, grid_step):
            if second >= window_end - 0.05:
                continue
            x = chart.left() + chart.width() * (
                (second - window_start) / self._window_seconds
            )
            painter.setPen(QPen(QColor("#e7eef1"), 1))
            painter.drawLine(x, chart.top(), x, chart.bottom())
            painter.setPen(QColor("#78909c"))
            painter.drawText(
                QRectF(x - 24, chart.bottom() + 4, 48, 18),
                Qt.AlignCenter,
                f"{second}s",
            )
        painter.setPen(QColor("#607d8b"))
        painter.drawText(
            QRectF(chart.left(), chart.bottom() + 4, 58, 18),
            Qt.AlignLeft,
            f"{window_start:.0f}s",
        )
        painter.drawText(
            QRectF(chart.right() - 58, chart.bottom() + 4, 58, 18),
            Qt.AlignRight,
            f"{window_end:.0f}s",
        )

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
        fill.setAlpha(35)
        painter.fillRect(
            QRectF(left, chart.top(), right - left, chart.height()), fill
        )
        painter.setPen(QPen(QColor("#0f7085"), 1.2, Qt.PenStyle.DashLine))
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
        y = max(chart.top(), min(chart.bottom(), self._hover_position.y()))
        painter.setPen(QPen(QColor("#334155"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(x, chart.top(), x, chart.bottom())
        painter.drawLine(chart.left(), y, chart.right(), y)
        seconds = self._seconds_at_x(x)
        value = self._axis_max_damage * (chart.bottom() - y) / chart.height()
        painter.fillRect(
            QRectF(x - 31, chart.bottom() - 18, 62, 18), QColor("#ffffff")
        )
        painter.fillRect(
            QRectF(2, y - 9, chart.left() - 8, 18), QColor("#ffffff")
        )
        painter.setPen(QColor("#1f3f4b"))
        painter.drawText(
            QRectF(x - 31, chart.bottom() - 18, 62, 18),
            Qt.AlignCenter,
            f"{seconds:.2f}s",
        )
        painter.drawText(
            QRectF(2, y - 9, chart.left() - 8, 18),
            Qt.AlignRight | Qt.AlignVCenter,
            _compact_number(value),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f8fbfc"))
        chart = self._chart_rect()
        self._draw_boss_phase_background(painter, chart)
        self._draw_interval_selection(painter, chart)
        self._draw_left_axis(painter, chart)
        self._draw_time_axis(painter, chart)
        painter.setPen(QPen(QColor("#d5e2e7"), 1))
        painter.drawLine(chart.bottomLeft(), chart.bottomRight())
        painter.drawLine(chart.topLeft(), chart.bottomLeft())

        window_start = self._view_start
        window_end = window_start + self._window_seconds
        if not self._series:
            painter.setPen(QColor("#78909c"))
            painter.drawText(chart, Qt.AlignCenter, "暂无战斗数据")
            self._draw_crosshair(painter, chart)
            return

        visible_seconds = range(int(window_start), int(window_end) + 1)
        visible_values = [
            (second, self._rolling_map.get(second, 0.0))
            for second in visible_seconds
        ]
        max_damage = self._axis_max_damage
        path = QPainterPath()
        for index, (second, damage) in enumerate(visible_values):
            x = chart.left() + chart.width() * (
                (second - window_start) / self._window_seconds
            )
            y = chart.bottom() - chart.height() * damage / max_damage
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.save()
        painter.setClipRect(chart)
        painter.setPen(QPen(QColor("#0f8fa6"), 2))
        painter.drawPath(path)
        painter.restore()
        self._draw_crosshair(painter, chart)
        painter.setPen(QColor("#607d8b"))
        painter.drawText(
            QRectF(chart.right() - 112, chart.top() + 3, 106, 18),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{self._smoothing_seconds}秒平滑DPS",
        )


class BossDamageCurve(DamageCurve):
    timeSelected = Signal(float)
    timeHovered = Signal(float)
    COLORS = (
        "#0f8fa6",
        "#d1495b",
        "#6a8e3a",
        "#8e5bb7",
        "#e67e22",
        "#3f6fa0",
        "#a05195",
        "#4f8f8b",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._named_series: tuple[
            tuple[str, tuple[tuple[int, int], ...]], ...
        ] = ()
        self._named_series_maps: tuple[dict[int, float], ...] = ()
        self._named_rolling_maps: tuple[dict[int, float], ...] = ()
        self._boss_hp_rows: tuple[
            tuple[
                str,
                str,
                tuple[
                    tuple[
                        tuple[tuple[float, float], ...],
                        tuple[float, ...],
                    ],
                    ...,
                ],
            ],
            ...,
        ] = ()
        self._boss_hp_visible = True
        self._selected_seconds: float | None = None
        self._last_hovered_seconds: float | None = None

    def _chart_rect(self) -> QRectF:
        return QRectF(
            76,
            28,
            max(1, self.width() - 132),
            max(1, self.height() - 56),
        )

    def _rebuild_named_rolling_series(self) -> None:
        self._named_rolling_maps = tuple(
            dict(_rolling_damage_series(series, self._smoothing_seconds))
            for _name, series in self._named_series
        )
        self._global_max_damage = max(
            1.0,
            max(
                (
                    value
                    for values in self._named_rolling_maps
                    for value in values.values()
                ),
                default=0.0,
            ),
        )
        self._axis_max_damage = _nice_axis_max(self._global_max_damage)

    def set_smoothing_seconds(self, seconds: int) -> None:
        previous = self._smoothing_seconds
        super().set_smoothing_seconds(seconds)
        if self._smoothing_seconds != previous:
            self._rebuild_named_rolling_series()
            self.update()

    def set_named_series(
        self,
        rows: tuple[tuple[str, tuple[tuple[int, int], ...]], ...],
        elapsed_seconds: float,
    ) -> None:
        self._named_series = rows
        combined: dict[int, int] = defaultdict(int)
        for _name, series in rows:
            for second, damage in series:
                combined[int(second)] += int(damage)
        super().set_series(tuple(sorted(combined.items())), elapsed_seconds)
        self._named_series_maps = tuple(
            {int(second): float(damage) for second, damage in series}
            for _name, series in rows
        )
        self._rebuild_named_rolling_series()

    def set_boss_hp_series(
        self,
        rows: tuple[
            tuple[
                str,
                str,
                tuple[tuple[tuple[float, float], ...], ...],
            ],
            ...,
        ],
    ) -> None:
        self._boss_hp_rows = tuple(
            (
                name,
                confidence,
                tuple(
                    (
                        tuple(points),
                        tuple(point[0] for point in points),
                    )
                    for points in segments
                    if points
                ),
            )
            for name, confidence, segments in rows
            if segments
        )
        self.update()

    def set_boss_hp_visible(self, visible: bool) -> None:
        self._boss_hp_visible = bool(visible)
        self.update()

    def set_selected_seconds(self, seconds: float | None) -> None:
        self._selected_seconds = (
            None if seconds is None else max(0.0, float(seconds))
        )
        self.update()

    def _horizontal_drag_clicked(self, event) -> None:
        chart = self._chart_rect()
        if chart.contains(event.position()):
            ratio = max(
                0.0,
                min(1.0, (event.position().x() - chart.left()) / chart.width()),
            )
            self._selected_seconds = max(
                0.0, self._view_start + ratio * self._window_seconds
            )
            self.timeSelected.emit(self._selected_seconds)
            self.update()

    def _boss_hp_values_at(self, seconds: float) -> tuple[tuple[str, str, float], ...]:
        if not self._boss_hp_visible:
            return ()
        values: list[tuple[str, str, float]] = []
        for name, confidence, segments in self._boss_hp_rows:
            for points, point_times in segments:
                if (
                    not points
                    or seconds < point_times[0] - 1e-6
                    or seconds > point_times[-1] + 1e-6
                ):
                    continue
                point_index = max(
                    0,
                    min(
                        len(points) - 1,
                        bisect_right(point_times, seconds) - 1,
                    ),
                )
                values.append((name, confidence, points[point_index][1]))
                break
        return tuple(values)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging or self._selecting_range:
            super().mouseMoveEvent(event)
            return
        chart = self._chart_rect()
        if chart.contains(event.position()) and (
            self._named_series or self._boss_hp_rows
        ):
            self._hover_position = QPointF(event.position())
            ratio = max(
                0.0,
                min(1.0, (event.position().x() - chart.left()) / chart.width()),
            )
            cursor_seconds = max(
                0.0,
                self._view_start + ratio * self._window_seconds,
            )
            if (
                self._last_hovered_seconds is None
                or abs(cursor_seconds - self._last_hovered_seconds) >= 0.025
            ):
                self._last_hovered_seconds = cursor_seconds
                self.timeHovered.emit(cursor_seconds)
            second = max(
                0,
                round(cursor_seconds),
            )
            lines = [f"{cursor_seconds:.1f}s"]
            for index, (name, _series) in enumerate(self._named_series):
                raw = self._named_series_maps[index].get(second, 0.0)
                rolling = self._named_rolling_maps[index].get(second, 0.0)
                lines.append(
                    f"{name}：{_compact_number(raw)} / "
                    f"{_compact_number(rolling)}（{self._smoothing_seconds}秒）"
                )
            for name, confidence, percent in self._boss_hp_values_at(
                cursor_seconds
            ):
                if confidence == "spawn_observed":
                    suffix = ""
                elif confidence == "fixed_reference_with_mechanism_completion":
                    suffix = "（固定血量；末端由服务器机制结算）"
                elif confidence == "fixed_reference":
                    suffix = "（按固定 Boss 血量计算）"
                elif confidence == "damage_normalized":
                    suffix = "（按实战伤害推算）"
                else:
                    suffix = "（推算）"
                lines.append(f"{name}剩余血量：{percent:.2f}%{suffix}")
            QToolTip.showText(
                event.globalPosition().toPoint(), "\n".join(lines), self
            )
            self.update()
            QWidget.mouseMoveEvent(self, event)
            return
        self._hover_position = None
        QToolTip.hideText()
        self.update()
        QWidget.mouseMoveEvent(self, event)

    def leaveEvent(self, event) -> None:
        self._last_hovered_seconds = None
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f8fbfc"))
        chart = self._chart_rect()
        self._draw_boss_phase_background(painter, chart)
        self._draw_interval_selection(painter, chart)
        self._draw_left_axis(painter, chart)
        self._draw_time_axis(painter, chart)
        painter.setPen(QPen(QColor("#d5e2e7"), 1))
        painter.drawLine(chart.bottomLeft(), chart.bottomRight())
        painter.drawLine(chart.topLeft(), chart.bottomLeft())
        if self._boss_hp_visible and self._boss_hp_rows:
            hp_grid_pen = QPen(QColor("#ead9c5"), 1)
            hp_grid_pen.setStyle(Qt.PenStyle.DashLine)
            for percent in (100.0, 75.0, 50.0, 25.0, 0.0):
                y = chart.bottom() - chart.height() * percent / 100.0
                painter.setPen(hp_grid_pen)
                painter.drawLine(chart.left(), y, chart.right(), y)
                painter.setPen(QColor("#9a6700"))
                painter.drawText(
                    QRectF(chart.right() + 5, y - 9, 44, 18),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    f"{percent:.0f}%",
                )
            painter.setPen(QPen(QColor("#d5e2e7"), 1))
            painter.drawLine(chart.topRight(), chart.bottomRight())

        window_start = self._view_start
        window_end = window_start + self._window_seconds
        if not self._named_series and not (
            self._boss_hp_visible and self._boss_hp_rows
        ):
            painter.setPen(QColor("#78909c"))
            painter.drawText(chart, Qt.AlignCenter, "请选择 Boss")
            return

        visible_seconds = range(int(window_start), int(window_end) + 1)
        series_maps = self._named_rolling_maps
        max_damage = self._axis_max_damage
        painter.save()
        painter.setClipRect(chart)
        for index, ((_name, _series), values) in enumerate(
            zip(self._named_series, series_maps)
        ):
            path = QPainterPath()
            for point_index, second in enumerate(visible_seconds):
                x = chart.left() + chart.width() * (
                    (second - window_start) / self._window_seconds
                )
                y = chart.bottom() - chart.height() * (
                    values.get(second, 0) / max_damage
                )
                if point_index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(QColor(self.COLORS[index % len(self.COLORS)]), 2))
            painter.drawPath(path)
        if self._boss_hp_visible:
            hp_pen = QPen(QColor("#d97706"), 2)
            for _name, confidence, segments in self._boss_hp_rows:
                if confidence == "spawn_observed":
                    hp_pen.setStyle(Qt.PenStyle.SolidLine)
                elif confidence in {
                    "fixed_reference",
                    "fixed_reference_with_mechanism_completion",
                }:
                    hp_pen.setStyle(Qt.PenStyle.DashDotLine)
                else:
                    hp_pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(hp_pen)
                for points, point_times in segments:
                    if (
                        not points
                        or point_times[-1] < window_start
                        or point_times[0] > window_end
                    ):
                        continue
                    left_index = max(
                        0,
                        bisect_right(point_times, window_start) - 1,
                    )
                    right_index = min(
                        len(points),
                        bisect_right(point_times, window_end) + 1,
                    )
                    visible_points = points[left_index:right_index]
                    if not visible_points:
                        continue
                    path = QPainterPath()
                    first_seconds, first_percent = visible_points[0]
                    first_x = chart.left() + chart.width() * (
                        (first_seconds - window_start) / self._window_seconds
                    )
                    previous_y = chart.bottom() - chart.height() * (
                        first_percent / 100.0
                    )
                    path.moveTo(first_x, previous_y)
                    for point_seconds, percent in visible_points[1:]:
                        x = chart.left() + chart.width() * (
                            (point_seconds - window_start)
                            / self._window_seconds
                        )
                        y = chart.bottom() - chart.height() * (percent / 100.0)
                        path.lineTo(x, previous_y)
                        path.lineTo(x, y)
                        previous_y = y
                    painter.drawPath(path)
        painter.restore()

        if self._selected_seconds is not None and (
            window_start
            <= self._selected_seconds
            <= window_end
        ):
            selected_x = chart.left() + chart.width() * (
                (self._selected_seconds - window_start) / self._window_seconds
            )
            painter.setPen(QPen(QColor("#1d4ed8"), 1.5, Qt.DashLine))
            painter.drawLine(selected_x, chart.top(), selected_x, chart.bottom())
            painter.setPen(QColor("#1d4ed8"))
            painter.drawText(
                QRectF(selected_x - 32, chart.top(), 64, 18),
                Qt.AlignCenter,
                f"{self._selected_seconds:.1f}s",
            )

        self._draw_crosshair(painter, chart)
        painter.setPen(QColor("#607d8b"))
        painter.drawText(
            QRectF(chart.right() - 112, chart.top() + 3, 106, 18),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{self._smoothing_seconds}秒平滑DPS",
        )
        if self._boss_hp_visible and self._boss_hp_rows:
            painter.fillRect(
                QRectF(chart.right() - 108, chart.top() + 23, 10, 10),
                QColor("#d97706"),
            )
            painter.setPen(QColor("#7a4b00"))
            painter.drawText(
                QRectF(chart.right() - 94, chart.top() + 20, 92, 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                "Boss剩余血量",
            )
        legend_x = chart.left() + 8
        legend_y = chart.top() + 5
        for index, (name, _series) in enumerate(self._named_series):
            color = QColor(self.COLORS[index % len(self.COLORS)])
            painter.fillRect(QRectF(legend_x, legend_y, 10, 10), color)
            painter.setPen(QColor("#334155"))
            label = painter.fontMetrics().elidedText(name, Qt.ElideRight, 110)
            painter.drawText(
                QRectF(legend_x + 14, legend_y - 3, 112, 16),
                Qt.AlignLeft | Qt.AlignVCenter,
                label,
            )
            legend_x += 134
            if legend_x + 120 > chart.right():
                legend_x = chart.left() + 8
                legend_y += 17


class BossEffectCurve(QWidget):
    viewChanged = Signal(float, float, float)
    timeSelected = Signal(float)
    timeHovered = Signal(float)
    intervalSelectionChanged = Signal(float, float, str)
    SERIES_META = (
        ("冰冻层", "叠冰", "#168aad", True),
        ("增伤%", "通用增伤", "#d1495b", False),
        ("物理增伤合计%", "物理总增伤", "#e67e22", False),
        ("魔法增伤合计%", "魔法总增伤", "#8e5bb7", False),
        ("减火抗%", "减火抗", "#df5b57", False),
        ("减水抗%", "减水抗", "#2b8cbe", False),
        ("减光抗%", "减光抗", "#d19a00", False),
        ("减暗抗%", "减暗抗", "#665191", False),
        ("减物理防御%", "减物防", "#526d82", False),
        ("减魔法防御%", "减魔防", "#4f8f8b", False),
        ("减致命抵抗%", "减致命抗", "#a05195", False),
        ("减攻击%", "减少攻击", "#7a6f5d", False),
    )
    META_BY_KEY = {
        key: (label, color, freeze)
        for key, label, color, freeze in SERIES_META
    }
    DEFAULT_KEYS = {
        "冰冻层",
        "增伤%",
        "减火抗%",
        "减水抗%",
        "减光抗%",
        "减暗抗%",
        "减物理防御%",
        "减魔法防御%",
        "减致命抵抗%",
        "减攻击%",
    }
    AUTO_WINDOW_STEPS = (15.0, 30.0, 60.0, 90.0, 120.0, 180.0, 300.0, 600.0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._series: dict[str, tuple[tuple[float, float], ...]] = {}
        self._visible_keys = set(self.DEFAULT_KEYS)
        self._extent_seconds = 0.0
        self._window_seconds = self.AUTO_WINDOW_STEPS[0]
        self._manual_window_seconds: float | None = None
        self._view_start = 0.0
        self._follow_latest = True
        self._pixels_per_second = 30.0
        self._selected_seconds: float | None = None
        self._dragging = False
        self._drag_origin_x = 0.0
        self._drag_origin_start = 0.0
        self._drag_distance = 0.0
        self._hover_position: QPointF | None = None
        self._range_selection_enabled = False
        self._selecting_range = False
        self._selection_anchor_seconds = 0.0
        self._selection_range: tuple[float, float] | None = None
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "鼠标悬停查看当前状态；拖动平移；Shift+拖动选择统计区间；双击返回最新时间段"
        )

    def set_visible_keys(self, keys: set[str]) -> None:
        self._visible_keys = set(keys)
        self.update()

    def set_selected_seconds(self, seconds: float | None) -> None:
        self._selected_seconds = (
            None if seconds is None else max(0.0, float(seconds))
        )
        self.update()

    def set_series(
        self,
        series: tuple[tuple[str, tuple[tuple[float, float], ...]], ...],
        elapsed_seconds: float,
    ) -> None:
        self._series = dict(series)
        self._extent_seconds = max(0.0, float(elapsed_seconds))
        if self._manual_window_seconds is None:
            self._update_window_seconds()
        else:
            self._window_seconds = self._manual_window_seconds
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        self.update()

    def set_window_seconds(self, seconds: float | None) -> None:
        self._manual_window_seconds = (
            None if seconds is None else max(5.0, float(seconds))
        )
        if self._manual_window_seconds is None:
            self._window_seconds = next(
                (
                    window
                    for window in self.AUTO_WINDOW_STEPS
                    if self._extent_seconds <= window
                ),
                self.AUTO_WINDOW_STEPS[-1],
            )
        else:
            self._window_seconds = self._manual_window_seconds
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        self.update()

    def set_pixels_per_second(self, value: float) -> None:
        self._manual_window_seconds = None
        self._pixels_per_second = max(2.0, min(45.0, float(value)))
        self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        self.update()

    def _update_window_seconds(self) -> None:
        self._window_seconds = max(
            5.0, self._chart_rect().width() / self._pixels_per_second
        )

    def resizeEvent(self, event) -> None:
        if self._manual_window_seconds is None:
            self._update_window_seconds()
        if self._follow_latest:
            self._view_start = self._max_view_start()
        else:
            self._view_start = min(self._view_start, self._max_view_start())
        self._emit_view_changed()
        super().resizeEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = 5 if event.angleDelta().y() > 0 else -5
            self.set_pixels_per_second(self._pixels_per_second + delta)
            event.accept()
            return
        super().wheelEvent(event)

    def _chart_rect(self) -> QRectF:
        return QRectF(48, 12, max(1, self.width() - 92), max(1, self.height() - 38))

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

    @classmethod
    def _interval_average(
        cls,
        points: tuple[tuple[float, float], ...],
        start: float,
        end: float,
    ) -> float:
        duration = max(0.0, end - start)
        if duration <= 1e-9:
            return cls._value_at(points, start)
        boundaries = [start]
        boundaries.extend(
            seconds for seconds, _value in points if start < seconds < end
        )
        boundaries.append(end)
        total = 0.0
        for left, right in zip(boundaries, boundaries[1:]):
            total += cls._value_at(points, left) * (right - left)
        return total / duration

    def _selection_summary(self) -> str:
        if self._selection_range is None:
            return "区间：未选择"
        start, end = sorted(self._selection_range)
        duration = max(0.0, end - start)
        details: list[str] = []
        for key, label, _color, freeze in self.SERIES_META:
            points = self._series.get(key, ())
            if key not in self._visible_keys or not points:
                continue
            value = self._interval_average(points, start, end)
            suffix = "层" if freeze else "%"
            details.append(f"{label}均值 {value:.1f}{suffix}")
        tail = "  ".join(details[:4])
        return (
            f"区间 {start:.2f}s - {end:.2f}s（{duration:.2f}s）"
            + (f"  {tail}" if tail else "")
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
        fill.setAlpha(34)
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
            chart.bottom() + 3.0,
            64.0,
            20.0,
        )
        painter.fillRect(label_rect, QColor("#e7f5f7"))
        painter.setPen(QColor("#0f6575"))
        painter.drawText(label_rect, Qt.AlignCenter, f"{seconds:.2f}s")

    def _max_view_start(self) -> float:
        return max(0.0, self._extent_seconds - self._window_seconds)

    def _set_view_start(self, value: float) -> None:
        maximum = self._max_view_start()
        self._view_start = max(0.0, min(float(value), maximum))
        self._follow_latest = maximum - self._view_start <= 0.05
        self._emit_view_changed()
        self.update()

    def set_view_start(self, value: float) -> None:
        self._set_view_start(value)

    def _emit_view_changed(self) -> None:
        self.viewChanged.emit(
            self._view_start,
            self._max_view_start(),
            self._window_seconds,
        )

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._chart_rect().contains(event.position())
        ):
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
            delta_seconds = (
                (event.position().x() - self._drag_origin_x)
                * self._window_seconds
                / max(1.0, self._chart_rect().width())
            )
            self._set_view_start(self._drag_origin_start - delta_seconds)
            event.accept()
            return
        chart = self._chart_rect()
        if chart.contains(event.position()):
            self._hover_position = QPointF(event.position())
            seconds = self._seconds_at_x(event.position().x())
            self.timeHovered.emit(seconds)
            details: list[str] = [f"{seconds:.2f}s"]
            for key, label, _color, freeze in self.SERIES_META:
                points = self._series.get(key, ())
                if key not in self._visible_keys or not points:
                    continue
                suffix = "层" if freeze else "%"
                details.append(f"{label}：{self._value_at(points, seconds):.1f}{suffix}")
            QToolTip.showText(
                event.globalPosition().toPoint(), "\n".join(details), self
            )
            self.update()
            event.accept()
            return
        self._hover_position = None
        QToolTip.hideText()
        self.update()
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
            if self._drag_distance <= 4.0:
                chart = self._chart_rect()
                ratio = max(
                    0.0,
                    min(1.0, (event.position().x() - chart.left()) / chart.width()),
                )
                self._selected_seconds = self._view_start + ratio * self._window_seconds
                self.timeSelected.emit(self._selected_seconds)
                self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._follow_latest = True
            self._view_start = self._max_view_start()
            self._emit_view_changed()
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    @staticmethod
    def _value_at(points: tuple[tuple[float, float], ...], seconds: float) -> float:
        value = 0.0
        for point_seconds, point_value in points:
            if point_seconds > seconds:
                break
            value = point_value
        return value

    def _draw_selection_marker(self, painter: QPainter, chart: QRectF) -> None:
        if self._selected_seconds is None:
            return
        if not (
            self._view_start
            <= self._selected_seconds
            <= self._view_start + self._window_seconds
        ):
            return
        x = chart.left() + chart.width() * (
            (self._selected_seconds - self._view_start) / self._window_seconds
        )
        painter.setPen(QPen(QColor("#1d4ed8"), 1.5, Qt.DashLine))
        painter.drawLine(x, chart.top(), x, chart.bottom())
        painter.setPen(QColor("#1d4ed8"))
        painter.drawText(
            QRectF(x - 32, chart.top(), 64, 18),
            Qt.AlignCenter,
            f"{self._selected_seconds:.1f}s",
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f8fbfc"))
        chart = self._chart_rect()
        window_start = self._view_start
        window_end = window_start + self._window_seconds

        painter.setPen(QPen(QColor("#d5e2e7"), 1))
        for ratio, label in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%")):
            y = chart.bottom() - chart.height() * ratio
            painter.drawLine(chart.left(), y, chart.right(), y)
            painter.setPen(QColor("#607d8b"))
            painter.drawText(
                QRectF(0, y - 9, 43, 18), Qt.AlignRight | Qt.AlignVCenter, label
            )
            painter.setPen(QPen(QColor("#d5e2e7"), 1))
        painter.setPen(QColor("#168aad"))
        painter.drawText(
            QRectF(chart.right() + 4, chart.top() - 8, 36, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            "2层",
        )
        painter.drawText(
            QRectF(chart.right() + 4, chart.center().y() - 9, 36, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            "1层",
        )
        self._draw_interval_selection(painter, chart)

        grid_step = DamageCurve._grid_step(self._window_seconds)
        first_grid = (int(window_start) // grid_step + 1) * grid_step
        for second in range(first_grid, int(window_end) + 1, grid_step):
            if second >= window_end - 0.05:
                continue
            x = chart.left() + chart.width() * (
                (second - window_start) / self._window_seconds
            )
            painter.setPen(QPen(QColor("#e7eef1"), 1))
            painter.drawLine(x, chart.top(), x, chart.bottom())
            painter.setPen(QColor("#78909c"))
            painter.drawText(
                QRectF(x - 24, chart.bottom() + 3, 48, 18),
                Qt.AlignCenter,
                f"{second}s",
            )

        visible = [
            key
            for key, _label, _color, _freeze in self.SERIES_META
            if key in self._visible_keys and self._series.get(key)
        ]
        if not visible:
            painter.setPen(QColor("#78909c"))
            painter.drawText(chart, Qt.AlignCenter, "请选择需要显示的 Boss 状态曲线")
            self._draw_crosshair(painter, chart)
            return

        painter.save()
        painter.setClipRect(chart)
        for key in visible:
            _label, color, freeze = self.META_BY_KEY[key]
            points = self._series[key]
            current_value = self._value_at(points, window_start)
            path = QPainterPath()
            start_y = chart.bottom() - chart.height() * min(
                1.0, current_value / (2.0 if freeze else 100.0)
            )
            path.moveTo(chart.left(), start_y)
            for seconds, value in points:
                if seconds <= window_start:
                    continue
                if seconds > window_end:
                    break
                x = chart.left() + chart.width() * (
                    (seconds - window_start) / self._window_seconds
                )
                old_y = chart.bottom() - chart.height() * min(
                    1.0, current_value / (2.0 if freeze else 100.0)
                )
                path.lineTo(x, old_y)
                current_value = value
                new_y = chart.bottom() - chart.height() * min(
                    1.0, current_value / (2.0 if freeze else 100.0)
                )
                path.lineTo(x, new_y)
            end_y = chart.bottom() - chart.height() * min(
                1.0, current_value / (2.0 if freeze else 100.0)
            )
            path.lineTo(chart.right(), end_y)
            painter.setPen(QPen(QColor(color), 2.0))
            painter.drawPath(path)
        painter.restore()
        self._draw_selection_marker(painter, chart)
        self._draw_crosshair(painter, chart)


def _player_buff_curve_points(
    rows: tuple[PlayerBuffSeriesSnapshot, ...],
    target_filter: str,
    type_filter: str,
    current_seconds: float,
    target_job_filter: str = "",
    source_job_filter: str = "",
) -> tuple[tuple[tuple[float, float], ...], str, str]:
    """Build one effective step series from complete player-buff intervals."""
    filtered = [
        row
        for row in rows
        if (not target_filter or row.target_id == target_filter)
        and (not target_job_filter or row.target_job == target_job_filter)
        and (not source_job_filter or row.job == source_job_filter)
        and (
            not type_filter
            or (
                type_filter.startswith("kind:")
                and row.status_kind == type_filter.removeprefix("kind:")
            )
            or (
                type_filter.startswith("key:")
                and row.key == type_filter.removeprefix("key:")
            )
            or (
                type_filter.startswith("skill:")
                and _effect_skill_key(row.skill_name or row.skill_id)
                == type_filter.removeprefix("skill:")
            )
        )
    ]
    current_seconds = max(0.0, float(current_seconds))
    changes: dict[float, list[tuple[int, int]]] = defaultdict(list)
    for index, row in enumerate(filtered):
        for start, end in row.intervals:
            if start > current_seconds or end <= 0:
                continue
            clipped_start = max(0.0, float(start))
            clipped_end = max(clipped_start, float(end))
            changes[clipped_start].append((index, 1))
            if clipped_end <= current_seconds:
                changes[clipped_end].append((index, -1))

    numeric_mode = type_filter.startswith(("key:", "skill:")) and any(
        row.value is not None for row in filtered
    )
    active_counts: dict[int, int] = defaultdict(int)
    points: list[tuple[float, float]] = []
    previous: float | None = None
    boundaries = sorted({0.0, current_seconds, *changes.keys()})
    for seconds in boundaries:
        for index, delta in changes.get(seconds, ()):
            active_counts[index] += delta
            if active_counts[index] <= 0:
                active_counts.pop(index, None)

        grouped: dict[tuple[str, str, str], float] = {}
        for index in active_counts:
            row = filtered[index]
            group = (
                row.target_id,
                _effect_skill_key(row.skill_name or row.skill_id),
                row.key,
            )
            value = float(row.value) if numeric_mode and row.value is not None else 1.0
            grouped[group] = max(grouped.get(group, 0.0), value)
        value = sum(grouped.values()) if numeric_mode else float(len(grouped))
        if previous is None or abs(previous - value) > 1e-9:
            points.append((seconds, value))
            previous = value

    if type_filter.startswith("kind:"):
        label = f"{type_filter.removeprefix('kind:')}数量"
    elif type_filter.startswith("key:"):
        key = type_filter.removeprefix("key:")
        label = next((row.label for row in filtered if row.label), key)
    elif type_filter.startswith("skill:"):
        skill_key = type_filter.removeprefix("skill:")
        label = next(
            (row.skill_name for row in filtered if row.skill_name), skill_key
        )
    else:
        label = "生效增益数量"
    unit = "%" if numeric_mode and "%" in (label + type_filter) else ""
    return tuple(points), label, unit


def _boss_effect_overview_rows(
    rows: tuple[BossEffectSnapshot, ...],
) -> tuple[BossEffectSnapshot, ...]:
    """Order primary Boss effects by phase appearance, then by effect."""
    first_seen: dict[tuple[str, str], float] = {}
    for row in rows:
        identity = (
            "name",
            row.target_name.strip().casefold(),
        ) if row.target_name.strip() else ("id", row.target_id)
        first_seen[identity] = min(
            first_seen.get(identity, float("inf")),
            row.start_seconds,
        )

    def sort_key(row: BossEffectSnapshot) -> tuple[object, ...]:
        identity = (
            "name",
            row.target_name.strip().casefold(),
        ) if row.target_name.strip() else ("id", row.target_id)
        return (
            first_seen.get(identity, float("inf")),
            row.target_name or row.target_id,
            _boss_effect_category_rank(row),
            row.label or row.key,
            row.status_kind,
            row.skill_name,
            row.value if row.value is not None else float("-inf"),
        )

    return tuple(sorted(rows, key=sort_key))


@dataclass(frozen=True, slots=True)
class _BossEffectOverviewItem:
    effect: BossEffectSnapshot
    coverage: float


def _boss_effect_overview_items(
    rows: tuple[BossEffectSnapshot, ...],
    groups: tuple[_BossStatisticsGroup, ...],
    output_windows_by_target: dict[
        str, tuple[tuple[float, float], ...]
    ],
    target_label: str,
) -> tuple[_BossEffectOverviewItem, ...]:
    """Aggregate equal effects over one or more Boss output timelines."""

    selected_target_ids = {
        target_id for group in groups for target_id in group.target_ids
    }
    if not selected_target_ids:
        return ()

    grouped: dict[tuple[object, ...], list[BossEffectSnapshot]] = defaultdict(list)
    for row in rows:
        if row.target_id not in selected_target_ids:
            continue
        displayed_value = (
            round(row.value, 8)
            if row.value is not None
            else row.parameters
        )
        identity = (
            row.status_kind,
            row.label,
            row.skill_name,
            row.job,
            displayed_value,
        )
        grouped[identity].append(row)

    def effect_intervals(
        row: BossEffectSnapshot,
    ) -> tuple[tuple[float, float], ...]:
        if row.effect_code == "freeze_layer_coverage" and not row.intervals:
            return ()
        if row.effective_state and row.value is not None:
            return row.effective_intervals
        return row.intervals or ((row.start_seconds, row.end_seconds),)

    def observed_intervals(
        row: BossEffectSnapshot,
    ) -> tuple[tuple[float, float], ...]:
        if row.effect_code == "freeze_layer_coverage" and not row.intervals:
            return ()
        return row.intervals or ((row.start_seconds, row.end_seconds),)

    items: list[_BossEffectOverviewItem] = []
    for bucket in grouped.values():
        base = min(bucket, key=lambda row: row.start_seconds)
        covered_seconds = 0.0
        observed_covered_seconds = 0.0
        output_seconds = 0.0
        for group in groups:
            windows = next(
                (
                    output_windows_by_target[target_id]
                    for target_id in sorted(group.target_ids)
                    if target_id in output_windows_by_target
                ),
                (),
            )
            window_seconds = sum(
                max(0.0, end - start) for start, end in windows
            )
            output_seconds += window_seconds
            if window_seconds <= 1e-9:
                continue
            intervals = _merge_seconds_windows(
                interval
                for row in bucket
                if row.target_id in group.target_ids
                for interval in effect_intervals(row)
            )
            covered_seconds += (
                _coverage_in_windows(intervals, windows) * window_seconds
            )
            observed = _merge_seconds_windows(
                interval
                for row in bucket
                if row.target_id in group.target_ids
                for interval in observed_intervals(row)
            )
            observed_covered_seconds += (
                _coverage_in_windows(observed, windows) * window_seconds
            )

        all_effective_intervals = _merge_seconds_windows(
            interval for row in bucket for interval in effect_intervals(row)
        )
        all_observed_intervals = _merge_seconds_windows(
            interval for row in bucket for interval in observed_intervals(row)
        )
        source_ids = {row.source_id for row in bucket if row.source_id}
        aggregated = replace(
            base,
            source_id=(next(iter(source_ids)) if len(source_ids) == 1 else ""),
            source_name=(base.source_name if len(source_ids) == 1 else "全队"),
            start_seconds=min(row.start_seconds for row in bucket),
            end_seconds=max(row.end_seconds for row in bucket),
            last_refresh_seconds=max(row.last_refresh_seconds for row in bucket),
            refresh_count=sum(row.refresh_count for row in bucket),
            active=any(row.active for row in bucket),
            uptime_seconds=covered_seconds,
            coverage=(
                observed_covered_seconds / output_seconds
                if output_seconds > 1e-9
                else 0.0
            ),
            target_id="",
            target_name=target_label,
            intervals=all_observed_intervals,
            effective_intervals=all_effective_intervals,
            effective_coverage=(
                covered_seconds / output_seconds if output_seconds > 1e-9 else 0.0
            ),
        )
        items.append(
            _BossEffectOverviewItem(
                effect=aggregated,
                coverage=aggregated.effective_coverage,
            )
        )

    item_by_effect_id = {id(item.effect): item for item in items}
    ordered = _boss_effect_overview_rows(tuple(item.effect for item in items))
    return tuple(item_by_effect_id[id(effect)] for effect in ordered)


def _boss_effect_output_coverage(
    effect: BossEffectSnapshot,
    output_windows_by_target: dict[
        str, tuple[tuple[float, float], ...]
    ],
) -> float:
    """Measure one Boss effect only inside that Boss's output windows."""

    # Synthetic freeze-summary rows deliberately carry an empty interval list
    # when the requested layer was never reached.  Falling back to the row's
    # phase bounds would turn "never reached two layers" into nearly 100%
    # coverage.
    if effect.effect_code == "freeze_layer_coverage" and not effect.intervals:
        return 0.0
    intervals = (
        effect.effective_intervals
        if effect.effective_state and effect.value is not None
        else effect.intervals
    ) or (
        (effect.start_seconds, effect.end_seconds),
    )
    return _coverage_in_windows(
        intervals,
        output_windows_by_target.get(effect.target_id, ()),
    )


def _boss_group_target_display_names(
    groups: tuple[_BossStatisticsGroup, ...],
) -> dict[str, str]:
    """Label repeated encounters without exposing transient target IDs."""

    totals = Counter(group.name.strip().casefold() for group in groups)
    seen: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for group in groups:
        key = group.name.strip().casefold()
        seen[key] += 1
        label = group.name
        if totals[key] > 1:
            label = f"{group.name}（第{seen[key]}次）"
        for target_id in group.target_ids:
            labels[target_id] = label
    return labels


def _effect_series_threshold_intervals(
    points: tuple[tuple[float, float], ...],
    threshold: float,
    end_seconds: float,
) -> tuple[tuple[float, float], ...]:
    """Convert a step-value effect series into threshold-active intervals."""

    ordered = sorted(
        {
            max(0.0, float(seconds)): float(value)
            for seconds, value in points
            if float(seconds) <= end_seconds
        }.items()
    )
    intervals: list[tuple[float, float]] = []
    for index, (start, value) in enumerate(ordered):
        end = (
            ordered[index + 1][0]
            if index + 1 < len(ordered)
            else max(start, float(end_seconds))
        )
        if value >= threshold and end > start:
            intervals.append((start, end))
    return _merge_seconds_windows(intervals)


def _boss_effect_category_rank(effect: BossEffectSnapshot) -> int:
    if effect.key == "__freeze_layer_1_coverage__":
        return -3
    if effect.key == "__freeze_layer_2_coverage__":
        return -2
    text = " ".join(
        str(value or "")
        for value in (
            effect.status_kind,
            effect.key,
            effect.label,
            effect.skill_name,
            effect.parameters,
        )
    )
    if "冰冻" in text or "叠冰" in text:
        return -1
    if "通用增伤" in text or effect.key == "增伤%":
        return 0
    if "魔法增伤" in text or effect.key == "魔法增伤%":
        return 1
    if "物理增伤" in text or effect.key == "物理增伤%":
        return 2
    if "致命" in text and "抗" in text:
        return 3
    if "防御" in text or "物防" in text or "魔防" in text:
        return 4
    if "抗" in text:
        return 5
    if any(
        name in text
        for name in ("毁灭呐喊", "反击怒吼", "正义撞击", "神圣断言")
    ) or "减攻击" in text:
        return 6
    return 7


def _player_buff_category_rank(effect_text: str, status_kind: str = "") -> int:
    text = f"{effect_text} {status_kind}"
    categories = (
        ("攻击", "双攻", "物攻", "魔攻"),
        ("属性攻", "火攻", "水攻", "光攻", "暗攻"),
        ("最终", "增伤"),
        ("致命", "爆伤"),
        ("动作", "速度"),
        ("冷却", "CD", "cd"),
        ("力量", "敏捷", "智力", "体质", "四维"),
        ("防御", "生命", "HP", "hp"),
    )
    for rank, words in enumerate(categories):
        if any(word in text for word in words):
            return rank
    return len(categories)


class PlayerBuffCurve(BossEffectCurve):
    SERIES_KEY = "__player_buff__"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = "生效增益数量"
        self._unit = ""
        self.setMinimumHeight(150)
        self.setToolTip("按住曲线左右拖动可回看历史，Ctrl+滚轮调整时间范围")

    def set_buff_series(
        self,
        points: tuple[tuple[float, float], ...],
        elapsed_seconds: float,
        label: str,
        unit: str,
    ) -> None:
        self._label = label
        self._unit = unit
        self.set_series(((self.SERIES_KEY, points),), elapsed_seconds)

    def _format_axis_value(self, value: float) -> str:
        if abs(value - round(value)) <= 1e-6:
            text = str(int(round(value)))
        else:
            text = f"{value:.1f}"
        return f"{text}{self._unit}"

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#f8fbfc"))
        chart = self._chart_rect()
        window_start = self._view_start
        window_end = window_start + self._window_seconds
        points = self._series.get(self.SERIES_KEY, ())
        visible_values = [
            value
            for seconds, value in points
            if window_start <= seconds <= window_end
        ]
        visible_values.append(self._value_at(points, window_start))
        max_value = max(1.0, max(visible_values, default=0.0))

        painter.setPen(QPen(QColor("#d5e2e7"), 1))
        for ratio in (0.0, 0.5, 1.0):
            y = chart.bottom() - chart.height() * ratio
            painter.drawLine(chart.left(), y, chart.right(), y)
            painter.setPen(QColor("#607d8b"))
            painter.drawText(
                QRectF(0, y - 9, 43, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                self._format_axis_value(max_value * ratio),
            )
            painter.setPen(QPen(QColor("#d5e2e7"), 1))

        grid_step = DamageCurve._grid_step(self._window_seconds)
        first_grid = (int(window_start) // grid_step + 1) * grid_step
        for second in range(first_grid, int(window_end) + 1, grid_step):
            if second >= window_end - 0.05:
                continue
            x = chart.left() + chart.width() * (
                (second - window_start) / self._window_seconds
            )
            painter.setPen(QPen(QColor("#e7eef1"), 1))
            painter.drawLine(x, chart.top(), x, chart.bottom())
            painter.setPen(QColor("#78909c"))
            painter.drawText(
                QRectF(x - 24, chart.bottom() + 3, 48, 18),
                Qt.AlignCenter,
                f"{second}s",
            )

        painter.setPen(QColor("#285f6b"))
        painter.drawText(
            QRectF(chart.left() + 6, chart.top() + 4, chart.width() - 12, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._label,
        )
        if not points:
            painter.setPen(QColor("#78909c"))
            painter.drawText(chart, Qt.AlignCenter, "暂无符合筛选条件的增益曲线")
            return

        current_value = self._value_at(points, window_start)
        path = QPainterPath()
        path.moveTo(
            chart.left(),
            chart.bottom() - chart.height() * current_value / max_value,
        )
        for seconds, value in points:
            if seconds <= window_start:
                continue
            if seconds > window_end:
                break
            x = chart.left() + chart.width() * (
                (seconds - window_start) / self._window_seconds
            )
            path.lineTo(
                x, chart.bottom() - chart.height() * current_value / max_value
            )
            current_value = value
            path.lineTo(
                x, chart.bottom() - chart.height() * current_value / max_value
            )
        path.lineTo(
            chart.right(),
            chart.bottom() - chart.height() * current_value / max_value,
        )
        painter.save()
        painter.setClipRect(chart)
        painter.setPen(QPen(QColor("#2a9d8f"), 2.2))
        painter.drawPath(path)
        painter.restore()
        self._draw_selection_marker(painter, chart)


__all__ = tuple(name for name in globals() if not name.startswith("__"))
