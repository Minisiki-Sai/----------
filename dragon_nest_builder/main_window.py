from __future__ import annotations

from .shared import *
from .analytics import schedule_usage_ping
from .editor import BuildEditor
from .ui.compare_page import ComparePage
from .ui.live_dps_page import LiveDpsPage
from .live_dps.npcap_bootstrap import NpcapBootstrap
from .live_dps.action_parser import action_parser_mode, set_action_parser_mode
from .live_dps.v410_score_sync import load_v410_score_sync_config


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} - {AUTHOR_TEXT}")
        icon_path = resource_path(APP_ICON_RELATIVE)
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1500, 900)
        self.store = DataStore.load()
        self.calculator = BuildCalculator(self.store)
        self.tabs = QTabWidget()
        self.editor1 = BuildEditor(
            "配装1",
            self.store,
            self.calculator,
            self.store.defaults["build1"],
            standardize_default=True,
        )
        self.editor2 = BuildEditor(
            "配装2",
            self.store,
            self.calculator,
            self.store.defaults["build2"],
            standardize_default=True,
        )
        self.tabs.addTab(self.editor1, "配装1")
        self.tabs.addTab(self.editor2, "配装2")
        self._rebuild_extra_tabs()
        self.tabs.currentChanged.connect(self._tab_changed)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(12, 8, 12, 12)
        central_layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel(APP_TITLE)
        title.setObjectName("appTitle")
        author = QLabel(f"· {AUTHOR_TEXT}")
        author.setObjectName("authorLabel")
        self.sync_button = app_button("一键同步配装1到配装2", primary=True)
        self.sync_button.clicked.connect(self.sync_build1_to_build2)
        self.sync_button.setObjectName("primaryButton")
        header.addWidget(title)
        header.addWidget(author)
        header.addStretch(1)
        header.addWidget(self.sync_button)
        central_layout.addLayout(header)
        central_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.npcap_bootstrap = NpcapBootstrap(self)
        self.npcap_bootstrap.availability_changed.connect(
            self.live_dps_page.set_npcap_available
        )
        self._menu()
        def schedule_daily_usage() -> None:
            cloud = load_v410_score_sync_config()
            schedule_usage_ping(
                None,
                {"analytics": {
                    "endpoint": cloud.endpoint,
                    "upload_token": cloud.upload_token,
                    "installation_id": cloud.installation_id,
                }},
                APP_VERSION,
            )
        QTimer.singleShot(2600, schedule_daily_usage)
        QTimer.singleShot(3600, self.npcap_bootstrap.check_on_startup)

    def _menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        save_action = file_menu.addAction("导出当前配装")
        load_action = file_menu.addAction("导入到当前配装")
        save_action.triggered.connect(self.export_current)
        load_action.triggered.connect(self.import_current)

        settings_menu = self.menuBar().addMenu("设置")
        self.structured_action_parser_action = settings_menu.addAction(
            "\u7ed3\u6784\u5316\u591a\u4e8b\u4ef6\u6280\u80fd\u89e3\u6790\uff08\u63a8\u8350\uff09"
        )
        self.structured_action_parser_action.setCheckable(True)
        self.structured_action_parser_action.setChecked(
            action_parser_mode() == "structured"
        )
        self.structured_action_parser_action.toggled.connect(
            self._toggle_structured_action_parser
        )
        settings_menu.addSeparator()
        score_upload_action = settings_menu.addAction("Supabase 评分上传设置")
        score_upload_action.triggered.connect(
            self._open_team_score_upload_settings
        )
        settings_menu.addSeparator()
        npcap_action = settings_menu.addAction("\u68c0\u67e5/\u5b89\u88c5 Npcap")
        npcap_action.triggered.connect(self.npcap_bootstrap.check_or_install)

    def _open_team_score_upload_settings(self) -> None:
        live_dps_page = getattr(self, "live_dps_page", None)
        if live_dps_page is not None:
            live_dps_page._open_team_score_sync_settings()

    def _toggle_structured_action_parser(self, enabled: bool) -> None:
        mode = set_action_parser_mode("structured" if enabled else "legacy")
        label = "\u7ed3\u6784\u5316\u591a\u4e8b\u4ef6" if mode == "structured" else "\u517c\u5bb9\u5355\u4e8b\u4ef6"
        self.statusBar().showMessage(
            f"\u6280\u80fd\u89e3\u6790\u5df2\u5207\u6362\u4e3a{label}\u65b9\u6848\uff0c\u4e0b\u6b21\u8f7d\u5165\u8bb0\u5f55\u6216\u5f00\u59cb\u5b9e\u65f6\u7edf\u8ba1\u65f6\u751f\u6548\u3002",
            7000,
        )

    def current_editor(self) -> BuildEditor:
        return self.editor1 if self.tabs.currentIndex() == 0 else self.editor2

    def export_current(self) -> None:
        editor = self.current_editor()
        path, _ = QFileDialog.getSaveFileName(self, "导出配装", f"{editor.name}.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(editor.collect_build(), ensure_ascii=False, indent=2), encoding="utf-8")

    def import_current(self) -> None:
        index = self.tabs.currentIndex()
        if index not in (0, 1):
            QMessageBox.information(self, "导入配装", "请先切换到配装1或配装2。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入配装", "", "JSON (*.json)")
        if not path:
            return
        try:
            build = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self.replace_editor(index, build)

    def sync_build1_to_build2(self) -> None:
        self.replace_editor(1, self.editor1.collect_build())
        self.tabs.setCurrentIndex(1)

    def replace_editor(self, index: int, build: dict[str, Any]) -> None:
        old_editor = self.tabs.widget(index)
        editor = BuildEditor("配装1" if index == 0 else "配装2", self.store, self.calculator, build)
        self.tabs.removeTab(index)
        self.tabs.insertTab(index, editor, "配装1" if index == 0 else "配装2")
        self.tabs.setCurrentIndex(index)
        if index == 0:
            self.editor1 = editor
        else:
            self.editor2 = editor
        if old_editor is not None and old_editor is not editor:
            old_editor.deleteLater()
        self._rebuild_extra_tabs()

    def _rebuild_extra_tabs(self) -> None:
        previous_index = self.tabs.currentIndex()
        skill_build_owner = getattr(self, "skill_build_owner", None)
        if (
            isinstance(skill_build_owner, BuildEditor)
            and qt_object_is_valid(skill_build_owner)
        ):
            skill_build_owner.build = skill_build_owner.collect_build()
            skill_build_owner.release_skill_build_page()
        self.skill_build_owner = None
        old_blocked = self.tabs.blockSignals(True)
        try:
            while self.tabs.count() > 2:
                old_page = self.tabs.widget(2)
                self.tabs.removeTab(2)
                if old_page is not None:
                    old_page.deleteLater()
            self.compare = ComparePage(self.editor1, self.editor2, self.calculator)
            self.tabs.addTab(self.compare, "配装对比")
            self.skill_build_loaded = False
            self.skill_build_loading = False
            self.skill_build_tab_index = self.tabs.count()
            self.skill_build_page = self._lazy_tab_page("切换到此页后加载模拟加点。")
            self.tabs.addTab(self.skill_build_page, "模拟加点")
            self.skill_sim_tab_index = self.tabs.count()
            self.skill_sim_page = self.editor1._skill_sim_page()
            self.tabs.addTab(self.skill_sim_page, "技能模拟")
            self.team_sim_tab_index = self.tabs.count()
            self.team_sim_page = self.editor1._team_sim_page()
            self.tabs.addTab(self.team_sim_page, "配队模拟")
            self.live_dps_tab_index = self.tabs.count()
            self.live_dps_page = LiveDpsPage(
                self.store, timeline_editor=self.editor1
            )
            self.tabs.addTab(self.live_dps_page, "实战统计")
            self.editor1._skill_sim_ui_active = False
            self.editor1._team_sim_ui_active = False
            if previous_index >= 0:
                self.tabs.setCurrentIndex(min(previous_index, self.tabs.count() - 1))
        finally:
            self.tabs.blockSignals(old_blocked)
        current_index = self.tabs.currentIndex()
        self.editor1._skill_sim_ui_active = current_index == getattr(self, "skill_sim_tab_index", -1)
        self.editor1._team_sim_ui_active = current_index == getattr(self, "team_sim_tab_index", -1)
        if self.tabs.currentIndex() == getattr(self, "skill_build_tab_index", -1):
            self._ensure_skill_build_tab()
        self.editor1.refresh()

    @staticmethod
    def _lazy_tab_page(text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(text)
        label.setObjectName("inlineHint")
        label.setAlignment(Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)
        return page

    def _tab_changed(self, index: int) -> None:
        skill_active = index == getattr(self, "skill_sim_tab_index", -1)
        team_active = index == getattr(self, "team_sim_tab_index", -1)
        self.editor1._skill_sim_ui_active = skill_active
        self.editor1._team_sim_ui_active = team_active
        if index == getattr(self, "skill_build_tab_index", -1):
            self._ensure_skill_build_tab()
        elif skill_active or team_active:
            self.editor1.schedule_refresh()
        elif index == getattr(self, "live_dps_tab_index", -1):
            self.live_dps_page.ensure_visible_layout()

    def _ensure_skill_build_tab(self) -> None:
        if getattr(self, "skill_build_loaded", False) or getattr(self, "skill_build_loading", False):
            return
        index = getattr(self, "skill_build_tab_index", -1)
        if index < 0 or index >= self.tabs.count():
            return
        self.skill_build_loading = True
        self.tabs.setUpdatesEnabled(False)
        try:
            page = self.editor1._skill_build_page()
            old_page = self.tabs.widget(index)
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, page, "模拟加点")
            if old_page is not None and old_page is not page:
                old_page.deleteLater()
            self.skill_build_page = page
            self.skill_build_loaded = True
            self.skill_build_owner = self.editor1
            self.tabs.setCurrentIndex(index)
        finally:
            self.tabs.setUpdatesEnabled(True)
            self.skill_build_loading = False

def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    icon_path = resource_path(APP_ICON_RELATIVE)
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    if FLUENT_AVAILABLE:
        setTheme(Theme.LIGHT)
        setThemeColor("#0f8fa6")
    apply_app_style(app)
    splash = create_startup_splash(icon_path)
    splash.show()
    splash.showMessage(
        "正在读取配装与技能数据...",
        Qt.AlignHCenter | Qt.AlignBottom,
        QColor("#476173"),
    )
    app.processEvents()
    window = MainWindow()
    splash.showMessage(
        "界面加载完成",
        Qt.AlignHCenter | Qt.AlignBottom,
        QColor("#0f7f95"),
    )
    app.processEvents()
    window.showMaximized()
    splash.finish(window)
    sys.exit(app.exec())

def create_startup_splash(icon_path: Path) -> QSplashScreen:
    pixmap = QPixmap(520, 210)
    pixmap.fill(QColor("#f7fbfc"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(QColor("#b9dbe2"), 2))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(QRectF(1, 1, 518, 208), 12, 12)
    if icon_path.exists():
        icon = QPixmap(str(icon_path)).scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(40, 44, icon)
    painter.setPen(QColor("#163246"))
    painter.setFont(QFont("Microsoft YaHei UI", 17, QFont.Bold))
    painter.drawText(
        QRectF(130, 42, 350, 36),
        Qt.AlignLeft | Qt.AlignVCenter,
        f"{APP_SHORT_NAME} V{APP_VERSION}",
    )
    painter.setPen(QColor("#0f8fa6"))
    painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.DemiBold))
    painter.drawText(
        QRectF(132, 78, 350, 26),
        Qt.AlignLeft | Qt.AlignVCenter,
        APP_PRODUCT_DESCRIPTION,
    )
    painter.setPen(QColor("#72879a"))
    painter.setFont(QFont("Microsoft YaHei UI", 9))
    painter.drawText(QRectF(132, 108, 330, 24), Qt.AlignLeft | Qt.AlignVCenter, AUTHOR_TEXT)
    painter.drawText(QRectF(132, 134, 330, 24), Qt.AlignLeft | Qt.AlignVCenter, "正在准备工具，请稍候")
    painter.end()
    splash = QSplashScreen(pixmap)
    splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return splash

def apply_app_style(app: QApplication) -> None:
    combo_arrow = resource_path(Path("dragon_nest_builder") / "assets" / "combo_arrow.png").as_posix()
    app.setStyleSheet("""
        QMainWindow, QWidget {
            background: #f3f7f9;
            color: #172033;
        }
        QLabel#appTitle {
            color: #102033;
            font-size: 21px;
            font-weight: 800;
        }
        QLabel#authorLabel {
            color: #62748a;
            font-size: 13px;
            font-weight: 600;
        }
        QLabel#inlineLabel {
            color: #4d6073;
            font-weight: 700;
        }
        QLabel#inlineValue {
            background: #e8f6f8;
            border: 1px solid #bde4ea;
            border-radius: 5px;
            color: #08798c;
            font-weight: 800;
            padding: 3px 7px;
        }
        QLabel#panelTitle {
            color: #26384d;
            font-size: 13px;
            font-weight: 700;
            padding: 0 0 0px 2px;
        }
        QWidget#buildEditorPage {
            background: #f7fafc;
        }
        QFrame#buildPageHeader {
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 0,
                stop: 0 #eaf7f9, stop: 1 #f8fbfc
            );
            border: 1px solid #c9e2e7;
            border-radius: 9px;
        }
        QFrame#buildPageAccent {
            background: #0f8fa6;
            border: 0px;
            border-radius: 2px;
        }
        QLabel#buildPageTitle {
            background: transparent;
            color: #173347;
            font-size: 16px;
            font-weight: 800;
        }
        QLabel#buildPageSubtitle {
            background: transparent;
            color: #63778b;
            font-size: 12px;
        }
        QFrame#buildToolbar {
            background: #ffffff;
            border: 1px solid #d3e2e8;
            border-radius: 8px;
        }
        QFrame#buildToolbar QLabel {
            background: transparent;
        }
        QGroupBox#buildSection, QGroupBox#buildCompactSection {
            background: #ffffff;
            border: 1px solid #cfdee6;
            border-radius: 9px;
            margin-top: 12px;
            color: #21394e;
            font-weight: 800;
        }
        QGroupBox#buildSection::title, QGroupBox#buildCompactSection::title {
            subcontrol-origin: margin;
            left: 12px;
            top: 1px;
            padding: 0 7px;
            background: #f7fafc;
            color: #173b50;
        }
        QLabel#buildSectionHint {
            background: transparent;
            color: #66798c;
            font-size: 12px;
            padding: 0px 1px 2px 1px;
        }
        QLabel#buildValuePreview {
            background: #f1f8fa;
            border: 1px solid #d6e8ec;
            border-radius: 6px;
            color: #315166;
            padding: 5px 8px;
        }
        QLabel#buildBaselineBadge {
            background: #edf3f6;
            border: 1px solid #ccdbe3;
            border-radius: 6px;
            color: #40566a;
            font-weight: 800;
            padding: 4px 9px;
        }
        QLabel#buildBuffedBadge {
            background: #e3f5f3;
            border: 1px solid #acdcd5;
            border-radius: 6px;
            color: #08766f;
            font-weight: 800;
            padding: 4px 9px;
        }
        QTableWidget#buildEditorTable {
            border: 1px solid #cbdce4;
            border-radius: 7px;
            background: #ffffff;
            alternate-background-color: #f5fafb;
        }
        QTableWidget#buildEditorTable QHeaderView::section {
            min-height: 25px;
            background: #e4f1f4;
            color: #294257;
            padding: 3px 7px;
        }
        QGroupBox {
            background: #fbfdfe;
            border: 1px solid #d2e1e8;
            border-radius: 7px;
            margin-top: 10px;
            padding: 7px;
            font-weight: 700;
            color: #273b50;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            top: 0px;
            padding: 0 6px;
            background: #f3f7f9;
        }
        QGroupBox#jobBar {
            margin-top: 6px;
            padding: 5px 7px 6px 7px;
        }
        QGroupBox#summaryCard {
            border: 1px solid #c7dce4;
            background: #ffffff;
        }
        QLabel#summaryValue {
            color: #0f7f95;
            font-size: 20px;
            font-weight: 800;
        }
        QTabWidget::pane {
            border: 1px solid #cfdee7;
            border-radius: 8px;
            background: #fbfdfe;
            top: -1px;
        }
        QTabWidget#buildEditorTabs::pane {
            background: #f7fafc;
            border-color: #c8dce5;
        }
        QTabBar::tab {
            background: #e5edf2;
            color: #526578;
            border: 1px solid #cfdee7;
            border-bottom: none;
            padding: 5px 13px;
            margin-right: 2px;
            border-top-left-radius: 7px;
            border-top-right-radius: 7px;
            font-weight: 600;
        }
        QTabBar::tab:selected {
            background: #fbfdfe;
            color: #0f7f95;
        }
        QTabBar::tab:hover:!selected {
            background: #edf8fa;
            color: #25384c;
        }
        QPushButton {
            background: #ffffff;
            border: 1px solid #c5d4dd;
            border-radius: 7px;
            padding: 6px 13px;
            color: #1f2937;
            font-weight: 600;
        }
        QPushButton:hover {
            background: #eef8fa;
            border-color: #78b7c3;
        }
        QPushButton#miniActionButton {
            padding: 1px 9px;
            border-radius: 5px;
            font-size: 12px;
            font-weight: 600;
            background: #f8fbfc;
        }
        QPushButton#miniActionButton:hover {
            background: #e8f5f8;
            border-color: #7db7c1;
        }
        QPushButton#primaryButton {
            background: #0f8fa6;
            border-color: #0f8fa6;
            color: #ffffff;
        }
        QPushButton#primaryButton:hover {
            background: #0b7285;
            border-color: #0b7285;
        }
        QPushButton#gradeLButton {
            background: #b91c1c;
            border-color: #991b1b;
            color: #fff7ed;
            font-weight: 800;
        }
        QPushButton#gradeLButton:hover {
            background: #dc2626;
            border-color: #b91c1c;
        }
        QPushButton#gradeSButton {
            background: #2563eb;
            border-color: #1d4ed8;
            color: #eff6ff;
            font-weight: 800;
        }
        QPushButton#gradeSButton:hover {
            background: #3b82f6;
            border-color: #2563eb;
        }
        QPushButton#gradeAButton {
            background: #ea580c;
            border-color: #c2410c;
            color: #fff7ed;
            font-weight: 800;
        }
        QPushButton#gradeAButton:hover {
            background: #f97316;
            border-color: #ea580c;
        }
        QPushButton#gradeNoneButton {
            background: #64748b;
            border-color: #475569;
            color: #f8fafc;
            font-weight: 800;
        }
        QPushButton#gradeNoneButton:hover {
            background: #475569;
            border-color: #334155;
        }
        QComboBox, QSpinBox, QDoubleSpinBox {
            background: #ffffff;
            border: 1px solid #c6d6df;
            border-radius: 5px;
            padding: 0px 6px;
            min-height: 24px;
            color: #182033;
        }
        QComboBox {
            padding-left: 6px;
            padding-right: 24px;
            min-height: 24px;
        }
        QComboBox QLineEdit {
            border: 0px;
            background: transparent;
            padding: 0px;
            margin: 0px;
            min-height: 18px;
            selection-background-color: #d7f0f4;
        }
        QComboBox::drop-down {
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 22px;
            border-left: 1px solid #d5e3ea;
            border-top-right-radius: 5px;
            border-bottom-right-radius: 5px;
            background: #eef8fa;
        }
        QComboBox::drop-down:hover {
            background: #dff2f6;
        }
        QComboBox::down-arrow {
            image: url(__COMBO_ARROW__);
            width: 12px;
            height: 12px;
        }
        QComboBox#elementResistModeCombo {
            padding-left: 6px;
            padding-right: 14px;
        }
        QComboBox#elementResistModeCombo::drop-down {
            width: 14px;
        }
        QComboBox#elementResistModeCombo QLineEdit {
            qproperty-alignment: AlignCenter;
        }
        QTableWidget QComboBox {
            margin: 0px;
            padding-top: 0px;
            padding-bottom: 0px;
            min-height: 24px;
            max-height: 26px;
            border-radius: 4px;
        }
        QTableWidget QComboBox QLineEdit {
            margin: 0px;
            padding: 0px 0px 0px 0px;
            min-height: 18px;
        }
        QTableWidget QComboBox#skillLevelCombo {
            padding-left: 0px;
            padding-right: 12px;
        }
        QTableWidget QComboBox#skillLevelCombo::drop-down {
            width: 12px;
            border-left: 0px;
        }
        QTableWidget QComboBox#skillLevelCombo::down-arrow {
            width: 8px;
            height: 8px;
        }
        QTableWidget QComboBox#skillLevelCombo QLineEdit {
            padding-left: 0px;
            padding-right: 0px;
        }
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
            border-color: #0f8fa6;
            background: #ffffff;
        }
        QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
            border-color: #74b7c4;
        }
        QTableWidget {
            background: #ffffff;
            alternate-background-color: #f5fafb;
            gridline-color: #dfeaf0;
            border: 1px solid #cfdee7;
            border-radius: 6px;
            selection-background-color: #d7f0f4;
            selection-color: #111827;
            outline: 0;
            font-size: 12px;
        }
        QHeaderView::section {
            background: #dfeff3;
            color: #24384d;
            border: 0px;
            border-right: 1px solid #c9dde5;
            border-bottom: 1px solid #c5d9e2;
            padding: 2px 6px;
            font-weight: 700;
        }
        QHeaderView::section:vertical {
            background: #eef6f8;
            color: #43566b;
            border-right: 1px solid #cbdde5;
            border-bottom: 1px solid #dfeaf0;
        }
        QTableCornerButton::section {
            background: #dfeff3;
            border: 0px;
            border-right: 1px solid #c9dde5;
            border-bottom: 1px solid #c5d9e2;
        }
        QTableWidget::item {
            padding: 0px;
            border: 0px;
        }
        QTableWidget::item:selected {
            background: #d7f0f4;
            color: #111827;
        }
        QWidget#skillBuildTreePage {
            background: #f8fbfc;
        }
        QGraphicsView#skillTreeZoomView {
            background: #f8fbfc;
            border: 0px;
        }
        QWidget#skillBuildGridHost {
            background: #eef5f7;
            border: 1px solid #d2e1e8;
            border-radius: 10px;
        }
        QWidget#skillBuildEmptyCell {
            background: rgba(255, 255, 255, 0.35);
            border: 1px dashed #d5e3ea;
            border-radius: 8px;
        }
        QWidget#skillBuildNode QLabel#skillBuildName {
            background: transparent;
            color: #1f3146;
            font-weight: 800;
        }
        QWidget#skillBuildNode QLabel#skillBuildReq {
            background: transparent;
            color: #5f7184;
        }
        QWidget#skillBuildNode QLabel#skillBuildMastery {
            background: #f59e0b;
            color: #fff7ed;
            border-radius: 5px;
            padding: 1px 5px;
            font-weight: 900;
        }
        QWidget#skillBuildNode QLabel#skillBuildEffectBadge {
            background: rgba(15, 118, 110, 0.12);
            color: #0f5f59;
            border: 1px solid rgba(20, 184, 166, 0.35);
            border-radius: 5px;
            padding: 2px 5px;
            font-weight: 900;
        }
        QWidget#skillBuildNode QLabel#skillBuildDetail {
            background: rgba(241, 245, 249, 0.82);
            color: #334155;
            border-radius: 5px;
            padding: 3px 4px;
            line-height: 110%;
        }
        QWidget#skillBuildNode QLabel#inlineHint {
            background: transparent;
            color: #54687c;
            font-weight: 700;
        }
        QWidget#skillBuildNode QSpinBox {
            min-height: 22px;
            max-height: 24px;
            padding-left: 2px;
            padding-right: 2px;
        }
        QWidget#skillBuildNode QPushButton {
            min-width: 24px;
            min-height: 24px;
            padding: 0px;
            border-radius: 5px;
            font-weight: 900;
        }
        QScrollArea {
            border: 0;
            background: transparent;
        }
        QSplitter::handle {
            background: #d2e2e8;
            width: 2px;
        }
        QMenuBar {
            background: #f3f7f9;
            color: #334155;
        }
        QMenuBar::item:selected, QMenu::item:selected {
            background: #d7f0f4;
        }
    """.replace("__COMBO_ARROW__", combo_arrow))
