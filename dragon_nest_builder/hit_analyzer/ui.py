from __future__ import annotations

import sys
import threading
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..data_loader import DataStore
from .analyzer import (
    HitObservation,
    SkillHitAnalyzer,
    SkillHitSummary,
    SkillHitTemplateLibrary,
    export_hit_workbook,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CAPTURE_DIR = (
    Path.home() / "Documents" / "DragonNestDpsCapture" / "live_dps_diagnostics"
)
DEFAULT_OUTPUT_DIR = ROOT_DIR / "outputs" / "hit_analyzer"
DEFAULT_LIBRARY_PATH = DEFAULT_OUTPUT_DIR / "skill_hit_templates.json"


def _item(text: object, value: object | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    if value is not None:
        item.setData(Qt.ItemDataRole.UserRole, value)
    return item


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


class _AnalyzeWorker(QObject):
    progress = Signal(int, str)
    completed = Signal(object, object)

    def __init__(
        self,
        paths: list[Path],
        known_fingerprints: set[str],
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.known_fingerprints = known_fingerprints
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        completed: list[tuple[Path, str, list[HitObservation]]] = []
        errors: list[tuple[Path, str]] = []
        try:
            store = DataStore.load()
            analyzer = SkillHitAnalyzer(store)
            count = max(1, len(self.paths))
            for index, path in enumerate(self.paths):
                if self.cancel_event.is_set():
                    break
                try:
                    fingerprint = SkillHitTemplateLibrary.file_fingerprint(path)
                    if fingerprint in self.known_fingerprints:
                        self.progress.emit(
                            round((index + 1) / count * 100),
                            f"已跳过重复记录：{path.name}",
                        )
                        continue

                    def decode_progress(current: int, total: int) -> None:
                        ratio = current / max(1, total)
                        overall = round((index + ratio) / count * 100)
                        self.progress.emit(
                            overall,
                            f"正在解析 {path.name}  {ratio:.0%}",
                        )

                    rows = analyzer.analyze_dpscap(
                        path,
                        progress_callback=decode_progress,
                        cancel_check=self.cancel_event.is_set,
                    )
                    completed.append((path, fingerprint, rows))
                    self.progress.emit(
                        round((index + 1) / count * 100),
                        f"已分析 {path.name}：{len(rows)} 次施放样本",
                    )
                except InterruptedError:
                    break
                except Exception as exc:  # UI boundary: report each bad capture.
                    errors.append((path, str(exc)))
        except Exception as exc:
            errors.append((Path("初始化"), str(exc)))
        self.completed.emit(completed, errors)


class HitAnalyzerWindow(QMainWindow):
    SUMMARY_COLUMNS = (
        "职业",
        "技能",
        "样本",
        "Hit数",
        "候选分布",
        "首Hit",
        "末Hit",
        "出伤跨度",
        "动作锁",
        "资料时间",
        "一致率",
        "直配率",
        "置信度",
        "类型",
        "状态",
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("龙之谷技能 Hit 模板分析器")
        self.resize(1500, 900)
        self.library = SkillHitTemplateLibrary(DEFAULT_LIBRARY_PATH)
        self.summaries_by_key: dict[str, SkillHitSummary] = {}
        self.observations_by_key: dict[str, list[HitObservation]] = defaultdict(list)
        self.worker_thread: QThread | None = None
        self.worker: _AnalyzeWorker | None = None
        self.cancel_event = threading.Event()
        self._build_ui()
        self._apply_style()
        self._refresh_from_library()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        toolbar = QHBoxLayout()
        self.open_files_button = QPushButton("分析记录")
        self.open_folder_button = QPushButton("分析文件夹")
        self.export_button = QPushButton("导出 Excel")
        self.cancel_button = QPushButton("停止")
        self.cancel_button.setEnabled(False)
        toolbar.addWidget(self.open_files_button)
        toolbar.addWidget(self.open_folder_button)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.cancel_button)
        toolbar.addSpacing(18)
        toolbar.addWidget(QLabel("职业"))
        self.job_filter = QComboBox()
        self.job_filter.setMinimumWidth(130)
        toolbar.addWidget(self.job_filter)
        toolbar.addWidget(QLabel("技能"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("筛选技能")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(180)
        toolbar.addWidget(self.search_edit)
        toolbar.addWidget(QLabel("最少样本"))
        self.sample_filter = QSpinBox()
        self.sample_filter.setRange(1, 9999)
        self.sample_filter.setValue(1)
        toolbar.addWidget(self.sample_filter)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("模板库"))
        self.library_label = QLabel(DEFAULT_LIBRARY_PATH.name)
        self.library_label.setToolTip(str(DEFAULT_LIBRARY_PATH))
        self.library_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        toolbar.addWidget(self.library_label)
        root.addLayout(toolbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        root.addWidget(self.progress_bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.summary_table = QTableWidget(0, len(self.SUMMARY_COLUMNS))
        self.summary_table.setHorizontalHeaderLabels(self.SUMMARY_COLUMNS)
        self.summary_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.summary_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.setSortingEnabled(True)
        self.summary_table.verticalHeader().setVisible(False)
        header = self.summary_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        widths = (100, 180, 64, 64, 120, 72, 72, 82, 82, 82, 75, 75, 78, 85)
        for index, width in enumerate(widths):
            self.summary_table.setColumnWidth(index, width)
        splitter.addWidget(self.summary_table)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(6)
        manual_row = QHBoxLayout()
        self.selected_label = QLabel("未选择技能")
        self.selected_label.setStyleSheet("font-weight: 600;")
        manual_row.addWidget(self.selected_label)
        manual_row.addStretch(1)
        manual_row.addWidget(QLabel("确认 Hit 数"))
        self.manual_hit_count = QSpinBox()
        self.manual_hit_count.setRange(0, 999)
        manual_row.addWidget(self.manual_hit_count)
        manual_row.addWidget(QLabel("确认施放时间"))
        self.manual_cast_time = QDoubleSpinBox()
        self.manual_cast_time.setRange(0.0, 120.0)
        self.manual_cast_time.setDecimals(4)
        self.manual_cast_time.setSingleStep(0.0333)
        manual_row.addWidget(self.manual_cast_time)
        self.confirm_check = QCheckBox("人工确认")
        self.confirm_check.setChecked(True)
        manual_row.addWidget(self.confirm_check)
        self.confirm_button = QPushButton("保存模板")
        manual_row.addWidget(self.confirm_button)
        details_layout.addLayout(manual_row)

        self.detail_tabs = QTabWidget()
        self.hit_table = QTableWidget(0, 8)
        self.hit_table.setHorizontalHeaderLabels(
            ("Hit", "中位时间", "P10", "P90", "中位占比", "占比P10", "占比P90", "样本")
        )
        self.hit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hit_table.verticalHeader().setVisible(False)
        self.hit_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.detail_tabs.addTab(self.hit_table, "每 Hit 明细")

        self.sample_table = QTableWidget(0, 12)
        self.sample_table.setHorizontalHeaderLabels(
            (
                "记录",
                "角色",
                "施放时刻",
                "Hit数",
                "首Hit",
                "末Hit",
                "出伤跨度",
                "动作锁",
                "直接匹配",
                "持续/召唤",
                "Hit时间轴",
                "归因方法",
            )
        )
        self.sample_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sample_table.setAlternatingRowColors(True)
        self.sample_table.verticalHeader().setVisible(False)
        self.sample_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.sample_table.horizontalHeader().setStretchLastSection(True)
        for index, width in enumerate((180, 110, 90, 60, 70, 70, 82, 72, 75, 80, 220)):
            self.sample_table.setColumnWidth(index, width)
        self.detail_tabs.addTab(self.sample_table, "施放样本")
        details_layout.addWidget(self.detail_tabs)
        splitter.addWidget(details)
        splitter.setSizes([510, 300])
        root.addWidget(splitter, 1)

        self.status_label = QLabel("就绪")
        root.addWidget(self.status_label)
        self.setCentralWidget(central)

        self.open_files_button.clicked.connect(self._choose_files)
        self.open_folder_button.clicked.connect(self._choose_folder)
        self.export_button.clicked.connect(self._export)
        self.cancel_button.clicked.connect(self._cancel)
        self.job_filter.currentTextChanged.connect(self._apply_filters)
        self.search_edit.textChanged.connect(self._apply_filters)
        self.sample_filter.valueChanged.connect(self._apply_filters)
        self.summary_table.itemSelectionChanged.connect(self._show_selected)
        self.confirm_button.clicked.connect(self._confirm_selected)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f8fa; color: #172b3a; }
            QPushButton { background: #ffffff; border: 1px solid #b9cbd5; padding: 6px 12px; }
            QPushButton:hover { border-color: #1685a1; background: #edf8fb; }
            QPushButton:disabled { color: #8a9ba5; background: #eef2f4; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #ffffff; border: 1px solid #b9cbd5; padding: 4px 6px;
            }
            QTableWidget { background: #ffffff; gridline-color: #d9e5ea; alternate-background-color: #f6fafc; }
            QHeaderView::section { background: #dceff4; border: 0; border-right: 1px solid #c1d7df; padding: 6px; font-weight: 600; }
            QTabWidget::pane { border: 1px solid #c5d8df; background: #ffffff; }
            QTabBar::tab { background: #e6eff3; padding: 7px 16px; border: 1px solid #c5d8df; }
            QTabBar::tab:selected { background: #ffffff; color: #087d98; }
            QProgressBar { border: 1px solid #b9cbd5; background: #ffffff; text-align: center; height: 18px; }
            QProgressBar::chunk { background: #1597a8; }
            """
        )

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 DPS 记录",
            str(DEFAULT_CAPTURE_DIR if DEFAULT_CAPTURE_DIR.exists() else Path.home()),
            "Dragon Nest DPS 记录 (*.dpscap)",
        )
        if paths:
            self._start_analysis([Path(path) for path in paths])

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择记录文件夹",
            str(DEFAULT_CAPTURE_DIR if DEFAULT_CAPTURE_DIR.exists() else Path.home()),
        )
        if not folder:
            return
        paths = sorted(Path(folder).glob("*.dpscap"))
        if not paths:
            QMessageBox.information(self, "没有记录", "该文件夹中没有 .dpscap 文件。")
            return
        self._start_analysis(paths)

    def _start_analysis(self, paths: list[Path]) -> None:
        if self.worker_thread is not None:
            return
        processed = self.library.payload.get("processed_files", {})
        fingerprints = set(processed) if isinstance(processed, dict) else set()
        self.cancel_event = threading.Event()
        self.worker_thread = QThread(self)
        self.worker = _AnalyzeWorker(paths, fingerprints, self.cancel_event)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._analysis_finished)
        self.worker.completed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_deleted)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.open_files_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker_thread.start()

    @Slot(int, str)
    def _on_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, value)))
        self.status_label.setText(message)

    @Slot(object, object)
    def _analysis_finished(
        self,
        completed: list[tuple[Path, str, list[HitObservation]]],
        errors: list[tuple[Path, str]],
    ) -> None:
        for path, fingerprint, rows in completed:
            self.library.add_file(path, fingerprint, rows)
        self._refresh_from_library()
        if errors:
            details = "\n".join(f"{path.name}: {message}" for path, message in errors[:12])
            QMessageBox.warning(self, "部分记录未完成", details)
        elif self.cancel_event.is_set():
            self.status_label.setText("已停止，已完成的记录已经保存。")
        else:
            sample_count = sum(len(rows) for _, _, rows in completed)
            self.status_label.setText(
                f"完成：新增 {len(completed)} 份记录，{sample_count} 次施放样本。"
            )
        self.progress_bar.setValue(100)

    @Slot()
    def _worker_deleted(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        self.open_files_button.setEnabled(True)
        self.open_folder_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("正在停止……")

    def _refresh_from_library(self) -> None:
        summaries = self.library.summaries()
        self.summaries_by_key = {row.key: row for row in summaries}
        self.observations_by_key = defaultdict(list)
        for row in self.library.observations():
            self.observations_by_key[row.profile_key].append(row)
        current_job = self.job_filter.currentText()
        self.job_filter.blockSignals(True)
        self.job_filter.clear()
        self.job_filter.addItem("全部职业")
        self.job_filter.addItems(sorted({row.job for row in summaries}))
        if current_job:
            index = self.job_filter.findText(current_job)
            if index >= 0:
                self.job_filter.setCurrentIndex(index)
        self.job_filter.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self) -> None:
        job = self.job_filter.currentText()
        query = self.search_edit.text().strip().lower()
        minimum = self.sample_filter.value()
        rows = [
            row
            for row in self.summaries_by_key.values()
            if (job in ("", "全部职业") or row.job == job)
            and (not query or query in row.skill_name.lower())
            and row.sample_count >= minimum
        ]
        self.summary_table.setSortingEnabled(False)
        self.summary_table.setRowCount(len(rows))
        for row_index, row in enumerate(sorted(rows, key=lambda item: (item.job, item.skill_name))):
            values = (
                row.job,
                row.skill_name,
                row.sample_count,
                row.display_hit_count,
                " / ".join(f"{count}×{samples}" for count, samples in row.alternative_hit_counts),
                _seconds(row.first_hit_seconds),
                _seconds(row.last_hit_seconds),
                _seconds(row.hit_span_seconds),
                _seconds(row.display_cast_seconds),
                _seconds(row.reference_cast_seconds),
                f"{row.hit_consistency:.1%}",
                f"{row.direct_cast_ratio:.1%}",
                f"{row.confidence:.1f}%",
                "持续/召唤" if row.persistent else "常规",
                row.status,
            )
            for column, value in enumerate(values):
                item = _item(value, row.key if column == 0 else None)
                if column == 12:
                    color = "#207a48" if row.confidence >= 80 else "#a56818" if row.confidence >= 55 else "#a23b3b"
                    item.setForeground(QColor(color))
                self.summary_table.setItem(row_index, column, item)
        self.summary_table.setSortingEnabled(True)
        if rows:
            self.summary_table.selectRow(0)
        else:
            self.selected_label.setText("未选择技能")
            self.hit_table.setRowCount(0)
            self.sample_table.setRowCount(0)
        self.status_label.setText(
            f"模板 {len(self.summaries_by_key)} 个，当前显示 {len(rows)} 个。"
        )

    def _selected_key(self) -> str:
        row = self.summary_table.currentRow()
        if row < 0:
            return ""
        item = self.summary_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _show_selected(self) -> None:
        key = self._selected_key()
        summary = self.summaries_by_key.get(key)
        if summary is None:
            return
        self.selected_label.setText(
            f"{summary.job} · {summary.skill_name}  |  {summary.sample_count} 个样本"
        )
        self.manual_hit_count.setValue(summary.manual_hit_count or summary.hit_count)
        self.manual_cast_time.setValue(
            summary.manual_cast_seconds
            if summary.manual_cast_seconds is not None
            else summary.action_lock_seconds or 0.0
        )
        self.confirm_check.setChecked(summary.status == "已人工确认")
        self.hit_table.setRowCount(len(summary.positions))
        for row_index, position in enumerate(summary.positions):
            values = (
                position.index,
                _seconds(position.time_seconds),
                _seconds(position.time_p10),
                _seconds(position.time_p90),
                f"{position.share:.2%}",
                f"{position.share_p10:.2%}",
                f"{position.share_p90:.2%}",
                position.samples,
            )
            for column, value in enumerate(values):
                self.hit_table.setItem(row_index, column, _item(value))
        samples = sorted(
            self.observations_by_key.get(key, ()),
            key=lambda row: (row.source_file, row.cast_timestamp_ms),
        )
        self.sample_table.setRowCount(len(samples))
        for row_index, row in enumerate(samples):
            values = (
                row.source_file,
                row.source_name or row.source_id,
                f"{row.cast_timestamp_ms / 1000.0:.3f}",
                row.hit_count,
                _seconds(row.first_hit_seconds),
                _seconds(row.last_hit_seconds),
                _seconds(row.hit_span_seconds),
                _seconds(row.action_lock_seconds),
                "是" if row.direct_cast else "否",
                "是" if row.persistent else "否",
                ", ".join(f"{value:.3f}" for value in row.hit_times),
                ", ".join(row.attribution_methods),
            )
            for column, value in enumerate(values):
                self.sample_table.setItem(row_index, column, _item(value))

    def _confirm_selected(self) -> None:
        key = self._selected_key()
        if not key:
            return
        cast_time = self.manual_cast_time.value()
        self.library.set_manual(
            key,
            hit_count=self.manual_hit_count.value() or None,
            cast_seconds=cast_time if cast_time > 0 else None,
            confirmed=self.confirm_check.isChecked(),
        )
        self._refresh_from_library()
        self.status_label.setText("当前技能模板已保存。")

    def _export(self) -> None:
        summaries = self.library.summaries()
        if not summaries:
            QMessageBox.information(self, "没有数据", "请先分析至少一份 .dpscap 记录。")
            return
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        default_path = DEFAULT_OUTPUT_DIR / "龙之谷技能Hit时间与占比模板.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出技能 Hit 模板",
            str(default_path),
            "Excel 工作簿 (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_hit_workbook(Path(path), summaries, self.observations_by_key)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.status_label.setText(f"已导出：{path}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.worker_thread is not None:
            self.cancel_event.set()
            self.worker_thread.quit()
            self.worker_thread.wait(3_000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Dragon Nest Skill Hit Analyzer")
    window = HitAnalyzerWindow()
    window.show()
    return app.exec()
