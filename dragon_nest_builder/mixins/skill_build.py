from __future__ import annotations

from ..shared import *
from ..skill_glyph_policy import (
    damage_glyph_multiplier,
    glyph_marginal_score_multiplier,
    linked_cooldown_glyph_skills,
    required_timing_skill_glyphs,
    select_skill_glyphs_two_stage,
)


class SkillBuildMixin:
    def release_skill_build_page(self) -> None:
        """Detach references owned by the lazily mounted skill-build page."""

        for view in getattr(self, "skill_build_canvas_views", []):
            if isinstance(view, SkillTreeZoomView):
                view._refresh_pending = False
                view.content = None
                view.owner = None
        for attr in (
            "skill_build_level_spins",
            "skill_build_nodes",
            "skill_build_group_labels",
            "skill_mastery_spins",
            "skill_mastery_nodes",
            "skill_mastery_group_labels",
            "skill_build_tabs",
            "skill_mastery_tabs",
            "skill_build_summary",
            "skill_mastery_summary",
            "skill_build_empty_label",
            "skill_mastery_empty_label",
            "skill_glyph_table",
            "skill_glyph_count_spin",
            "skill_glyph_apply_button",
            "skill_glyph_apply_hint",
        ):
            self.__dict__.pop(attr, None)
        self.skill_build_zoom_sources = []
        self.skill_build_zoom_empty_cells = []
        self.skill_build_zoom_layouts = []
        self.skill_build_canvas_views = []

    def _skill_build_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(8)
        self.skill_build_data = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        self.skill_mastery_data = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP, mode="master")
        saved_levels, saved_mastery_levels = self._skill_build_initial_levels()
        self.skill_build_level_spins: dict[str, QSpinBox] = {}
        self.skill_build_nodes: dict[str, dict[str, Any]] = {}
        self.skill_build_group_labels: dict[int, QLabel] = {}
        self.skill_mastery_spins: dict[str, QSpinBox] = {}
        self.skill_mastery_nodes: dict[str, dict[str, Any]] = {}
        self.skill_mastery_group_labels: dict[int, QLabel] = {}
        self.skill_build_zoom_sources = []
        self.skill_build_zoom_empty_cells: list[QWidget] = []
        self.skill_build_zoom_layouts: list[tuple[Any, tuple[int, int, int, int], int]] = []
        self.skill_build_canvas_views: list[SkillTreeZoomView] = []
        self._skill_icon_sheet_cache: dict[int, QPixmap] = {}
        self._last_summary: dict[str, float] = {}

        header = QHBoxLayout()
        header.addWidget(QLabel("模拟加点"))
        self.skill_build_summary = QLabel("")
        self.skill_build_summary.setObjectName("inlineHint")
        header.addWidget(self.skill_build_summary)
        self.skill_mastery_summary = QLabel("")
        self.skill_mastery_summary.setObjectName("inlineHint")
        header.addWidget(self.skill_mastery_summary)
        header.addStretch(1)
        update_default_button = app_button("修改为默认加点", primary=True)
        update_default_button.clicked.connect(self.save_current_skill_build_default)
        header.addWidget(update_default_button)
        export_scheme_button = app_button("导出加点方案")
        export_scheme_button.clicked.connect(self.export_skill_build_scheme)
        header.addWidget(export_scheme_button)
        import_scheme_button = app_button("导入加点方案")
        import_scheme_button.clicked.connect(self.import_skill_build_scheme)
        header.addWidget(import_scheme_button)
        reset_button = app_button("一键清零")
        reset_button.clicked.connect(self.reset_skill_build)
        header.addWidget(reset_button)
        layout.addLayout(header)

        self.skill_build_tabs = QTabWidget()
        self.skill_mastery_tabs = self.skill_build_tabs
        self.skill_build_tabs.setCornerWidget(self._skill_build_legend_widget(), Qt.TopRightCorner)
        self._install_skill_build_zoom_filter(self.skill_build_tabs)
        self._install_skill_build_zoom_filter(self.skill_build_tabs.tabBar())
        layout.addWidget(self.skill_build_tabs, 1)
        self.skill_mastery_empty_label = QLabel("")
        self.skill_mastery_empty_label.setObjectName("inlineHint")
        layout.addWidget(self.skill_mastery_empty_label)
        self.skill_build_empty_label = QLabel("")
        self.skill_build_empty_label.setObjectName("inlineHint")
        layout.addWidget(self.skill_build_empty_label)
        self._populate_skill_build_tabs(saved_levels)
        self._populate_skill_mastery_tabs(saved_mastery_levels)
        self._populate_skill_glyph_tab()
        self.update_skill_mastery_preview()
        self.update_skill_build_preview()
        return page

    def _skill_build_legend_widget(self) -> QWidget:
        widget = QWidget()
        widget.setObjectName("skillBuildLegendBar")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(4)
        for kind in ("active", "passive", "team_buff", "personal_buff", "debuff", "ex"):
            palette = self._skill_build_kind_palette(kind)
            label = QLabel(self._skill_build_kind_label(kind))
            label.setObjectName("skillBuildLegend")
            label.setStyleSheet(
                f"background:{palette['bg']}; color:#233044; border:1px solid {palette['border']}; "
                "border-radius:5px; padding:2px 6px; font-size:10px; font-weight:800;"
            )
            layout.addWidget(label)
        return widget

    def _install_skill_build_zoom_filter(self, widget: QObject | None) -> None:
        if widget is None:
            return
        if getattr(self, "skill_build_canvas_zoom", False):
            return
        sources = getattr(self, "skill_build_zoom_sources", [])
        targets: list[QObject] = [widget]
        if isinstance(widget, QScrollArea):
            targets.append(widget.viewport())
        if isinstance(widget, QGraphicsView):
            targets.append(widget.viewport())
        for target in targets:
            if any(target is source for source in sources):
                continue
            target.installEventFilter(self)
            sources.append(target)
        self.skill_build_zoom_sources = sources

    def _skill_build_scaled(self, value: int, minimum: int = 1) -> int:
        zoom = 1.0 if getattr(self, "skill_build_canvas_zoom", False) else float(getattr(self, "skill_build_zoom", 1.0))
        return max(minimum, int(round(value * zoom)))

    def _register_skill_build_zoom_layout(
        self,
        layout: Any,
        margins: tuple[int, int, int, int],
        spacing: int,
    ) -> None:
        layouts = getattr(self, "skill_build_zoom_layouts", [])
        layouts.append((layout, margins, spacing))
        self.skill_build_zoom_layouts = layouts
        self._apply_skill_build_zoom_to_layout(layout, margins, spacing)

    def _apply_skill_build_zoom_to_layout(
        self,
        layout: Any,
        margins: tuple[int, int, int, int],
        spacing: int,
    ) -> None:
        if layout is None:
            return
        scaled_margins = tuple(self._skill_build_scaled(value, 0) for value in margins)
        with contextlib.suppress(Exception):
            layout.setContentsMargins(*scaled_margins)
        with contextlib.suppress(Exception):
            layout.setSpacing(self._skill_build_scaled(spacing, 1))
        if isinstance(layout, QGridLayout):
            layout.setHorizontalSpacing(self._skill_build_scaled(spacing, 1))
            layout.setVerticalSpacing(self._skill_build_scaled(spacing, 1))

    def _set_skill_build_font_size(self, widget: QWidget | None, base_size: int) -> None:
        if widget is None:
            return
        zoom = 1.0 if getattr(self, "skill_build_canvas_zoom", False) else float(getattr(self, "skill_build_zoom", 1.0))
        font = widget.font()
        font.setPointSizeF(max(8.0, base_size * zoom))
        widget.setFont(font)

    def _skill_build_canvas(self, widget: QWidget) -> SkillTreeZoomView:
        view = SkillTreeZoomView(widget, self)
        self.skill_build_canvas_views.append(view)
        self._install_skill_build_zoom_filter(view)
        return view

    def _apply_skill_build_zoom_to_empty(self, widget: QWidget) -> None:
        widget.setMinimumSize(self._skill_build_scaled(190, 120), self._skill_build_scaled(162, 100))

    def _apply_skill_build_zoom_to_node(self, node: dict[str, Any]) -> None:
        for key, margins, spacing in (
            ("layout", (7, 6, 7, 6), 4),
            ("top_layout", (0, 0, 0, 0), 4),
            ("meta_layout", (0, 0, 0, 0), 4),
            ("controls_layout", (0, 0, 0, 0), 4),
        ):
            self._apply_skill_build_zoom_to_layout(node.get(key), margins, spacing)
        widget = node.get("widget")
        if isinstance(widget, QWidget):
            widget.setMinimumSize(self._skill_build_scaled(190, 120), self._skill_build_scaled(162, 100))
            widget.setMaximumWidth(self._skill_build_scaled(260, 170))
        icon = node.get("icon")
        if isinstance(icon, QLabel):
            size = self._skill_build_scaled(30, 22)
            icon.setFixedSize(size, size)
            self._set_skill_build_icon(icon, int(as_number(node.get("icon_index"), -1)), size)
        name = node.get("name")
        if isinstance(name, QLabel):
            name.setMinimumHeight(self._skill_build_scaled(30, 22))
            self._set_skill_build_font_size(name, 12)
        effect = node.get("effect")
        if isinstance(effect, QLabel):
            self._set_skill_build_font_size(effect, 11)
        for key in ("level", "sp", "cd", "req", "mastery"):
            self._set_skill_build_font_size(node.get(key), 11)
        detail = node.get("detail")
        if isinstance(detail, QLabel):
            detail.setMinimumHeight(self._skill_build_scaled(46, 32))
            self._set_skill_build_font_size(detail, 10)
        spin = node.get("spin")
        if isinstance(spin, QSpinBox):
            spin.setFixedHeight(self._skill_build_scaled(24, 20))
            spin.setMinimumWidth(self._skill_build_scaled(54, 44))
            self._set_skill_build_font_size(spin, 12)
        for key in ("minus", "plus"):
            button = node.get(key)
            if isinstance(button, QPushButton):
                size = self._skill_build_scaled(24, 20)
                button.setFixedSize(size, size)
                self._set_skill_build_font_size(button, 12)

    def _apply_skill_build_zoom(self) -> None:
        for view in getattr(self, "skill_build_canvas_views", []):
            if isinstance(view, SkillTreeZoomView):
                view.set_zoom(float(getattr(self, "skill_build_zoom", 1.0)))
        if getattr(self, "skill_build_canvas_zoom", False):
            return
        for layout, margins, spacing in getattr(self, "skill_build_zoom_layouts", []):
            self._apply_skill_build_zoom_to_layout(layout, margins, spacing)
        for node in list(getattr(self, "skill_build_nodes", {}).values()) + list(getattr(self, "skill_mastery_nodes", {}).values()):
            if isinstance(node, dict):
                self._apply_skill_build_zoom_to_node(node)
        for empty in getattr(self, "skill_build_zoom_empty_cells", []):
            if isinstance(empty, QWidget):
                self._apply_skill_build_zoom_to_empty(empty)

    def _adjust_skill_build_zoom(self, direction: int) -> None:
        current = float(getattr(self, "skill_build_zoom", 1.0))
        new_zoom = max(0.65, min(1.15, round(current + direction * 0.08, 2)))
        if abs(new_zoom - current) < 0.001:
            return
        self.skill_build_zoom = new_zoom
        self._apply_skill_build_zoom()

    def _skill_icon_sheet_path(self, icon_index: int) -> Path:
        page = max(1, icon_index // 200 + 1)
        return resource_path(Path("dragon_nest_builder") / "assets" / "skill_icons" / f"skillicon{page:02d}.png")

    def _skill_icon_pixmap(self, icon_index: int, size: int) -> QPixmap | None:
        if icon_index < 0:
            return None
        page = max(1, icon_index // 200 + 1)
        if page not in getattr(self, "_skill_icon_sheet_cache", {}):
            path = self._skill_icon_sheet_path(icon_index)
            self._skill_icon_sheet_cache[page] = QPixmap(str(path)) if path.exists() else QPixmap()
        sheet = self._skill_icon_sheet_cache.get(page)
        if not isinstance(sheet, QPixmap) or sheet.isNull():
            return None
        local = icon_index % 200
        x = (local % 10) * 50
        y = (local // 10) * 50
        return sheet.copy(x, y, 50, 50).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _set_skill_build_icon(self, label: QLabel, icon_index: int, size: int) -> None:
        pixmap = self._skill_icon_pixmap(icon_index, size)
        if pixmap and not pixmap.isNull():
            label.setPixmap(pixmap)
            label.setText("")
            label.setStyleSheet("background: transparent; border-radius: 6px;")
            return
        label.setPixmap(QPixmap())
        label.setText(label.text() or "技")

    def _populate_skill_build_tabs(self, saved_levels: dict[str, Any]) -> None:
        if not hasattr(self, "skill_build_tabs"):
            return
        saved_levels = self._skill_build_sanitize_levels(saved_levels)
        self.skill_build_level_spins = {}
        self.skill_build_nodes = {}
        self.skill_build_group_labels = {}
        self.skill_build_zoom_empty_cells = []
        self.skill_build_zoom_layouts = []
        self.skill_build_canvas_views = []
        self.skill_build_tabs.blockSignals(True)
        while self.skill_build_tabs.count():
            self.skill_build_tabs.removeTab(0)
        groups = self.skill_build_data.get("groups", []) if isinstance(self.skill_build_data.get("groups", []), list) else []
        if hasattr(self, "skill_build_empty_label"):
            self.skill_build_empty_label.setText("" if groups else "当前职业没有可用的加点模拟数据。")
        for group_index, group in enumerate(groups):
            group_page = QWidget()
            group_page.setObjectName("skillBuildTreePage")
            self._install_skill_build_zoom_filter(group_page)
            group_layout = QVBoxLayout(group_page)
            group_layout.setContentsMargins(10, 8, 10, 10)
            group_layout.setSpacing(8)
            self._register_skill_build_zoom_layout(group_layout, (10, 8, 10, 10), 8)
            self._register_skill_build_zoom_layout(group_layout, (10, 8, 10, 10), 8)

            title_row = QHBoxLayout()
            job = group.get("job", {}) if isinstance(group.get("job", {}), dict) else {}
            title = QLabel(norm(job.get("name")) or "职业树")
            title.setObjectName("panelTitle")
            title_row.addWidget(title)
            sp_label = QLabel("")
            sp_label.setObjectName("inlineHint")
            title_row.addWidget(sp_label)
            title_row.addStretch(1)
            group_layout.addLayout(title_row)
            self.skill_build_group_labels[group_index] = sp_label

            grid_host = SkillBuildGridHost()
            grid_host.setObjectName("skillBuildGridHost")
            self._install_skill_build_zoom_filter(grid_host)
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(10, 10, 10, 10)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            self._register_skill_build_zoom_layout(grid, (10, 10, 10, 10), 10)
            self._register_skill_build_zoom_layout(grid, (10, 10, 10, 10), 10)
            skills = group.get("skills", []) if isinstance(group.get("skills", []), list) else []
            skills_by_slot = {int(as_number(skill.get("slot"))): skill for skill in skills if isinstance(skill, dict)}
            group_nodes: dict[str, dict[str, Any]] = {}
            for slot in range(28):
                row, col = divmod(slot, 4)
                skill = skills_by_slot.get(slot)
                if skill:
                    node = self._create_skill_build_node(skill, saved_levels)
                    grid.addWidget(node["widget"], row, col)
                    self.skill_build_nodes[norm(skill.get("id"))] = node
                    group_nodes[norm(skill.get("id"))] = node
                else:
                    empty = QWidget()
                    empty.setObjectName("skillBuildEmptyCell")
                    self._apply_skill_build_zoom_to_empty(empty)
                    self.skill_build_zoom_empty_cells.append(empty)
                    self._install_skill_build_zoom_filter(empty)
                    grid.addWidget(empty, row, col)
            for col in range(4):
                grid.setColumnStretch(col, 1)
            links: list[tuple[QWidget, QWidget]] = []
            for child_id, child_node in group_nodes.items():
                child_skill = child_node.get("skill", {})
                for parent in child_skill.get("parents", []) if isinstance(child_skill.get("parents", []), list) else []:
                    parent_node = group_nodes.get(norm(parent.get("id")))
                    if parent_node:
                        links.append((parent_node["widget"], child_node["widget"]))
            grid_host.arrow_overlay.set_links(links)
            group_layout.addWidget(grid_host, 0, Qt.AlignTop)
            group_layout.addStretch(1)
            self.skill_build_tabs.addTab(self._skill_build_canvas(group_page), norm(job.get("name")) or "职业树")
        self.skill_build_tabs.blockSignals(False)

    def _create_skill_build_node(self, skill: dict[str, Any], saved_levels: dict[str, Any]) -> dict[str, Any]:
        skill_id = norm(skill.get("id"))
        widget = QWidget()
        widget.setObjectName("skillBuildNode")
        widget.setMinimumSize(190, 162)
        widget.setMaximumWidth(260)
        node_layout = QVBoxLayout(widget)
        node_layout.setContentsMargins(7, 6, 7, 6)
        node_layout.setSpacing(4)

        top = QHBoxLayout()
        icon = QLabel(norm(skill.get("name"))[:1] or "技")
        icon.setObjectName("skillBuildIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(30, 30)
        palette = self._skill_build_kind_palette(self._skill_build_kind(skill))
        icon.setStyleSheet(
            f"background: {palette['icon']}; color: white; border-radius: 6px; font-weight: 700;"
        )
        top.addWidget(icon)
        name_label = QLabel(norm(skill.get("name")) or skill_id)
        name_label.setObjectName("skillBuildName")
        name_label.setWordWrap(True)
        name_label.setMinimumHeight(30)
        top.addWidget(name_label, 1)
        node_layout.addLayout(top)

        meta_row = QHBoxLayout()
        level_label = QLabel("")
        level_label.setObjectName("inlineHint")
        meta_row.addWidget(level_label)
        sp_label = QLabel("")
        sp_label.setObjectName("inlineHint")
        meta_row.addWidget(sp_label, 1, Qt.AlignRight)
        node_layout.addLayout(meta_row)

        cd_label = QLabel("")
        cd_label.setObjectName("inlineHint")
        cd_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        node_layout.addWidget(cd_label)

        controls = QHBoxLayout()
        minus_button = QPushButton("-")
        minus_button.setFixedSize(24, 24)
        spin = QSpinBox()
        spin.setRange(0, int(as_number(skill.get("max_level"))))
        spin.setFixedHeight(24)
        spin.setMinimumWidth(54)
        spin.setAlignment(Qt.AlignCenter)
        if skill_id in saved_levels:
            initial_level = int(as_number(saved_levels.get(skill_id), 0))
        else:
            initial_level = self._skill_build_free_default_level(skill)
        spin.setValue(int(max(0, min(spin.maximum(), initial_level))))
        spin.setProperty("previous_value", spin.value())
        plus_button = QPushButton("+")
        plus_button.setFixedSize(24, 24)
        minus_button.clicked.connect(lambda _checked=False, sid=skill_id: self._skill_build_step_level(sid, -1))
        plus_button.clicked.connect(lambda _checked=False, sid=skill_id: self._skill_build_step_level(sid, 1))
        spin.valueChanged.connect(lambda value, sid=skill_id: self._skill_build_spin_changed(sid, value))
        controls.addWidget(minus_button)
        controls.addWidget(spin)
        controls.addWidget(plus_button)
        controls.addStretch(1)
        node_layout.addLayout(controls)

        effect_label = QLabel("")
        effect_label.setObjectName("skillBuildEffectBadge")
        effect_label.setWordWrap(True)
        effect_label.setVisible(False)
        node_layout.addWidget(effect_label)

        req_label = QLabel("")
        req_label.setObjectName("skillBuildReq")
        req_label.setWordWrap(False)
        node_layout.addWidget(req_label)
        mastery_label = QLabel("")
        mastery_label.setObjectName("skillBuildMastery")
        mastery_label.setAlignment(Qt.AlignCenter)
        mastery_label.setVisible(False)
        node_layout.addWidget(mastery_label)
        detail_label = QLabel("")
        detail_label.setObjectName("skillBuildDetail")
        detail_label.setWordWrap(True)
        detail_label.setMinimumHeight(46)
        node_layout.addWidget(detail_label)

        self.skill_build_level_spins[skill_id] = spin
        node = {
            "widget": widget,
            "skill": skill,
            "icon": icon,
            "icon_index": int(as_number(skill.get("icon"), -1)),
            "name": name_label,
            "effect": effect_label,
            "level": level_label,
            "sp": sp_label,
            "cd": cd_label,
            "req": req_label,
            "mastery": mastery_label,
            "detail": detail_label,
            "spin": spin,
            "minus": minus_button,
            "plus": plus_button,
            "layout": node_layout,
            "top_layout": top,
            "meta_layout": meta_row,
            "controls_layout": controls,
        }
        for target in (
            widget,
            icon,
            name_label,
            level_label,
            sp_label,
            cd_label,
            minus_button,
            spin,
            plus_button,
            effect_label,
            req_label,
            mastery_label,
            detail_label,
        ):
            self._install_skill_build_zoom_filter(target)
        self._apply_skill_build_zoom_to_node(node)
        return node

    def _skill_mastery_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(8)
        defaults = self.build.get("skill_mastery_build", {}) if isinstance(self.build.get("skill_mastery_build", {}), dict) else {}
        saved_levels = defaults.get("levels", {}) if isinstance(defaults.get("levels", {}), dict) else {}
        self.skill_mastery_data = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP, mode="master")
        self.skill_mastery_spins: dict[str, QSpinBox] = {}
        self.skill_mastery_nodes: dict[str, dict[str, Any]] = {}
        self.skill_mastery_group_labels: dict[int, QLabel] = {}

        header = QHBoxLayout()
        header.addWidget(QLabel("技能专精"))
        self.skill_mastery_summary = QLabel("")
        self.skill_mastery_summary.setObjectName("inlineHint")
        header.addWidget(self.skill_mastery_summary)
        header.addStretch(1)
        layout.addLayout(header)

        self.skill_mastery_tabs = QTabWidget()
        layout.addWidget(self.skill_mastery_tabs, 1)
        self.skill_mastery_empty_label = QLabel("")
        self.skill_mastery_empty_label.setObjectName("inlineHint")
        layout.addWidget(self.skill_mastery_empty_label)
        self._populate_skill_mastery_tabs(saved_levels)
        self.update_skill_mastery_preview()
        return page

    def _populate_skill_mastery_tabs(self, saved_levels: dict[str, Any]) -> None:
        if not hasattr(self, "skill_mastery_tabs"):
            return
        self.skill_mastery_spins = {}
        self.skill_mastery_nodes = {}
        self.skill_mastery_group_labels = {}
        append_mode = self.skill_mastery_tabs is getattr(self, "skill_build_tabs", None)
        self.skill_mastery_tabs.blockSignals(True)
        if not append_mode:
            while self.skill_mastery_tabs.count():
                self.skill_mastery_tabs.removeTab(0)
        groups = self.skill_mastery_data.get("groups", []) if isinstance(self.skill_mastery_data.get("groups", []), list) else []
        if hasattr(self, "skill_mastery_empty_label"):
            self.skill_mastery_empty_label.setText("" if groups else "当前职业没有可用的技能专精数据。")
        for group_index, group in enumerate(groups):
            group_page = QWidget()
            group_page.setObjectName("skillBuildTreePage")
            self._install_skill_build_zoom_filter(group_page)
            group_layout = QVBoxLayout(group_page)
            group_layout.setContentsMargins(10, 8, 10, 10)
            group_layout.setSpacing(8)
            title_row = QHBoxLayout()
            job = group.get("job", {}) if isinstance(group.get("job", {}), dict) else {}
            title = QLabel(norm(job.get("name")) or "专精树")
            title.setObjectName("panelTitle")
            title_row.addWidget(title)
            sp_label = QLabel("")
            sp_label.setObjectName("inlineHint")
            title_row.addWidget(sp_label)
            title_row.addStretch(1)
            group_layout.addLayout(title_row)
            self.skill_mastery_group_labels[group_index] = sp_label

            grid_host = SkillBuildGridHost()
            grid_host.setObjectName("skillBuildGridHost")
            self._install_skill_build_zoom_filter(grid_host)
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(10, 10, 10, 10)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(10)
            skills = group.get("skills", []) if isinstance(group.get("skills", []), list) else []
            skills_by_slot = {int(as_number(skill.get("slot"))): skill for skill in skills if isinstance(skill, dict)}
            group_nodes: dict[str, dict[str, Any]] = {}
            for slot in range(28):
                row, col = divmod(slot, 4)
                skill = skills_by_slot.get(slot)
                if skill:
                    node = self._create_skill_mastery_node(skill, saved_levels)
                    grid.addWidget(node["widget"], row, col)
                    self.skill_mastery_nodes[norm(skill.get("id"))] = node
                    group_nodes[norm(skill.get("id"))] = node
                else:
                    empty = QWidget()
                    empty.setObjectName("skillBuildEmptyCell")
                    self._apply_skill_build_zoom_to_empty(empty)
                    self.skill_build_zoom_empty_cells.append(empty)
                    self._install_skill_build_zoom_filter(empty)
                    grid.addWidget(empty, row, col)
            for col in range(4):
                grid.setColumnStretch(col, 1)
            links: list[tuple[QWidget, QWidget]] = []
            for child_id, child_node in group_nodes.items():
                child_skill = child_node.get("skill", {})
                for parent in child_skill.get("parents", []) if isinstance(child_skill.get("parents", []), list) else []:
                    parent_node = group_nodes.get(norm(parent.get("id")))
                    if parent_node:
                        links.append((parent_node["widget"], child_node["widget"]))
            grid_host.arrow_overlay.set_links(links)
            group_layout.addWidget(grid_host, 0, Qt.AlignTop)
            group_layout.addStretch(1)
            tab_name = "技能专精" if append_mode and len(groups) == 1 else f"专精-{norm(job.get('name')) or group_index + 1}"
            if not append_mode:
                tab_name = norm(job.get("name")) or "专精树"
            self.skill_mastery_tabs.addTab(self._skill_build_canvas(group_page), tab_name)
        self.skill_mastery_tabs.blockSignals(False)

    def _skill_glyph_entries(self) -> list[dict[str, Any]]:
        return self.store.skill_glyphs(self.second_job.currentText())

    def _skill_multiplier_override_page(self) -> QWidget:
        """Build a compact editor for per-build skill multiplier overrides."""
        page = QWidget()
        page.setObjectName("skillMultiplierOverridePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        hint = QLabel("只修改当前队员的技能倍率；未修改的技能继续读取程序默认数据。")
        hint.setObjectName("inlineHint")
        header.addWidget(hint)
        header.addStretch(1)
        reset_button = app_button("恢复默认倍率")
        header.addWidget(reset_button)
        layout.addLayout(header)

        saved = (
            self.build.get("skill_multiplier_overrides", {})
            if isinstance(self.build.get("skill_multiplier_overrides"), dict)
            else {}
        )
        saved_rows = (
            saved.get("rows", [])
            if norm(saved.get("second_job")) == norm(self.second_job.currentText())
            else []
        )
        saved_values = {
            self._skill_burst_skill_key(row.get("skill")): max(0.0, as_number(row.get("value")))
            for row in saved_rows
            if isinstance(row, dict) and self._skill_burst_skill_key(row.get("skill"))
        }

        rows: list[tuple[str, str, float, float]] = []
        for skill in self.store.skill_damage_names(self.second_job.currentText()):
            if skill == "无":
                continue
            build_level = self._skill_build_level_for_burst_sim(skill)
            if build_level is not None and build_level <= 0:
                continue
            level = self._skill_default_level(skill)
            if level == "无":
                continue
            _level_data, effective_level, mastery_bonus = self._skill_sim_level_data_with_mastery(skill, level)
            multiplier_level = effective_level if mastery_bonus else level
            default_value = self.store.skill_damage_multiplier(
                self.second_job.currentText(), skill, multiplier_level
            )
            skill_key = self._skill_burst_skill_key(skill)
            rows.append((skill, effective_level or level, default_value, saved_values.get(skill_key, default_value)))

        self.skill_multiplier_override_table = AppTableWidget(len(rows), 4)
        self.skill_multiplier_override_table.setObjectName("skillMultiplierOverrideTable")
        self.skill_multiplier_override_table.setHorizontalHeaderLabels(
            ["技能", "当前等级", "默认倍率", "自定义倍率"]
        )
        self.skill_multiplier_override_table.verticalHeader().setVisible(False)
        for row_index, (skill, level, default_value, current_value) in enumerate(rows):
            for column, text in enumerate((skill, level, f"{default_value:g}")):
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.skill_multiplier_override_table.setItem(row_index, column, item)
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 9999.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.05)
            spin.setValue(current_value)
            spin.setProperty("default_multiplier", default_value)
            spin.setSuffix(" 倍")
            self.skill_multiplier_override_table.setCellWidget(row_index, 3, spin)
            self.skill_multiplier_override_table.setRowHeight(row_index, EDIT_ROW_HEIGHT)
        self._configure_table(self.skill_multiplier_override_table, [260, 92, 110, 150])
        self.skill_multiplier_override_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.skill_multiplier_override_table, 1)

        def restore_defaults() -> None:
            for row_index in range(self.skill_multiplier_override_table.rowCount()):
                spin = self.skill_multiplier_override_table.cellWidget(row_index, 3)
                if isinstance(spin, QDoubleSpinBox):
                    spin.setValue(as_number(spin.property("default_multiplier"), 1.0))

        reset_button.clicked.connect(restore_defaults)
        return page

    def apply_skill_multiplier_overrides(self) -> None:
        table = getattr(self, "skill_multiplier_override_table", None)
        if not isinstance(table, QTableWidget):
            return
        rows: list[dict[str, Any]] = []
        for row_index in range(table.rowCount()):
            item = table.item(row_index, 0)
            spin = table.cellWidget(row_index, 3)
            if item is None or not isinstance(spin, QDoubleSpinBox):
                continue
            default_value = as_number(spin.property("default_multiplier"), 1.0)
            current_value = float(spin.value())
            if abs(current_value - default_value) <= 1e-9:
                continue
            rows.append({"skill": item.text().strip(), "value": current_value})
        self.build["skill_multiplier_overrides"] = {
            "second_job": self.second_job.currentText(),
            "rows": rows,
        }

    def _skill_glyph_entry(self, skill: str) -> dict[str, Any]:
        skill_key = self._skill_burst_skill_key(skill)
        for entry in self._skill_glyph_entries():
            if self._skill_burst_skill_key(entry.get("skill")) == skill_key:
                return entry
        return {}

    @staticmethod
    def _skill_glyph_option_text(option: dict[str, Any]) -> str:
        value = as_number(option.get("value"))
        value_text = str(int(value)) if abs(value - int(value)) < 1e-9 else f"{value:g}"
        return " ".join(filter(None, [norm(option.get("grade")), f"{value_text}%"]))

    def _skill_glyph_value_options(self, skill: str, effect: str) -> list[str]:
        entry = self._skill_glyph_entry(skill)
        effects = entry.get("effects", {}) if isinstance(entry.get("effects"), dict) else {}
        options = effects.get(norm(effect), []) if isinstance(effects.get(norm(effect)), list) else []
        return [self._skill_glyph_option_text(option) for option in options]

    def _skill_glyph_value(self, skill: str, effect: str, option_text: str) -> float:
        entry = self._skill_glyph_entry(skill)
        effects = entry.get("effects", {}) if isinstance(entry.get("effects"), dict) else {}
        for option in effects.get(norm(effect), []) if isinstance(effects.get(norm(effect)), list) else []:
            if self._skill_glyph_option_text(option) == norm(option_text):
                return max(0.0, as_number(option.get("value")))
        return 0.0

    def _skill_glyph_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("skillGlyphPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(QLabel("技能纹章数量"))
        saved = self._skill_build_default_glyphs_for_current()
        self.build["skill_glyphs"] = copy.deepcopy(saved)
        saved_rows = saved.get("rows", []) if norm(saved.get("second_job")) == norm(self.second_job.currentText()) else []
        self._skill_glyph_applied_count = max(1, min(7, int(as_number(saved.get("count"), 4))))
        self._skill_glyph_applied_rows = [
            dict(row) for row in saved_rows if isinstance(row, dict)
        ]
        self._skill_glyph_dirty = False
        self.skill_glyph_count_spin = QSpinBox()
        self.skill_glyph_count_spin.setRange(1, 7)
        self.skill_glyph_count_spin.setValue(self._skill_glyph_applied_count)
        self.skill_glyph_count_spin.setFixedWidth(64)
        self.skill_glyph_count_spin.valueChanged.connect(self._skill_glyph_count_changed)
        header.addWidget(self.skill_glyph_count_spin)
        auto_button = app_button("一键纹章", primary=True)
        auto_button.clicked.connect(self.auto_select_skill_glyphs)
        header.addWidget(auto_button)
        self.skill_glyph_apply_button = app_button("应用修改", primary=True)
        self.skill_glyph_apply_button.clicked.connect(self.apply_skill_glyph_changes)
        self.skill_glyph_apply_button.setEnabled(False)
        header.addWidget(self.skill_glyph_apply_button)
        self.skill_glyph_apply_hint = QLabel("修改完成后统一应用，避免每个下拉操作都重算时间轴。")
        self.skill_glyph_apply_hint.setObjectName("inlineHint")
        header.addWidget(self.skill_glyph_apply_hint)
        header.addStretch(1)
        layout.addLayout(header)

        self.skill_glyph_table = AppTableWidget(7, 3)
        self.skill_glyph_table.setHorizontalHeaderLabels(["技能", "纹章效果", "档位 / 数值"])
        self.skill_glyph_table.verticalHeader().setVisible(False)
        entries = self._skill_glyph_entries()
        skills = ["无"] + unique(entry.get("skill") for entry in entries)
        for row in range(7):
            saved_row = saved_rows[row] if isinstance(saved_rows, list) and row < len(saved_rows) else {}
            skill = norm(saved_row.get("skill")) or "无"
            skill_combo = Combo(
                skills,
                skill,
                lambda _text, target=row: self._skill_glyph_skill_changed(target),
                min_width=160,
                contents_length=0,
            )
            self.skill_glyph_table.setCellWidget(row, 0, skill_combo)
            entry = self._skill_glyph_entry(skill)
            effects = list(entry.get("effects", {})) if isinstance(entry.get("effects"), dict) else []
            effect = norm(saved_row.get("effect")) or (effects[0] if effects else "无")
            effect_combo = Combo(
                effects or ["无"],
                effect,
                lambda _text, target=row: self._skill_glyph_effect_changed(target),
                min_width=120,
                contents_length=0,
            )
            self.skill_glyph_table.setCellWidget(row, 1, effect_combo)
            values = self._skill_glyph_value_options(skill, effect)
            selected_value = norm(saved_row.get("option")) or (values[-1] if values else "无")
            value_combo = Combo(values or ["无"], selected_value, self._skill_glyph_mark_dirty, min_width=120, contents_length=0)
            self.skill_glyph_table.setCellWidget(row, 2, value_combo)
            self.skill_glyph_table.setRowHeight(row, EDIT_ROW_HEIGHT)
        self._configure_table(self.skill_glyph_table, [210, 150, 160])
        self.skill_glyph_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.skill_glyph_table)
        self._skill_glyph_count_changed()
        self._skill_glyph_dirty = False
        self.skill_glyph_apply_button.setEnabled(False)
        self.skill_glyph_apply_hint.setText("修改完成后统一应用，避免每个下拉操作都重算时间轴。")
        return page

    def _populate_skill_glyph_tab(self) -> None:
        tabs = getattr(self, "skill_build_tabs", None)
        if not isinstance(tabs, QTabWidget):
            return
        for index in range(tabs.count() - 1, -1, -1):
            if tabs.tabText(index) == "技能纹章":
                old_page = tabs.widget(index)
                tabs.removeTab(index)
                old_page.deleteLater()
        tabs.addTab(self._skill_glyph_page(), "技能纹章")

    def _skill_glyph_count_changed(self, *_args) -> None:
        table = getattr(self, "skill_glyph_table", None)
        if not isinstance(table, QTableWidget):
            return
        count = int(self.skill_glyph_count_spin.value())
        for row in range(table.rowCount()):
            enabled = row < count
            for column in range(table.columnCount()):
                widget = table.cellWidget(row, column)
                if widget is not None:
                    widget.setEnabled(enabled)
        self._skill_glyph_mark_dirty()

    def _skill_glyph_skill_changed(self, row: int) -> None:
        skill = self.combo_text(self.skill_glyph_table, row, 0)
        entry = self._skill_glyph_entry(skill)
        effects = list(entry.get("effects", {})) if isinstance(entry.get("effects"), dict) else []
        effect_combo = self.skill_glyph_table.cellWidget(row, 1)
        if isinstance(effect_combo, Combo):
            self._replace_combo_values(effect_combo, effects or ["无"], effects[0] if effects else "无")
        self._skill_glyph_effect_changed(row)

    def _skill_glyph_effect_changed(self, row: int) -> None:
        skill = self.combo_text(self.skill_glyph_table, row, 0)
        effect = self.combo_text(self.skill_glyph_table, row, 1)
        values = self._skill_glyph_value_options(skill, effect)
        value_combo = self.skill_glyph_table.cellWidget(row, 2)
        if isinstance(value_combo, Combo):
            self._replace_combo_values(value_combo, values or ["无"], values[-1] if values else "无")
        self._skill_glyph_mark_dirty()

    def _skill_glyph_mark_dirty(self, *_args) -> None:
        self._skill_glyph_dirty = True
        button = getattr(self, "skill_glyph_apply_button", None)
        if isinstance(button, QPushButton):
            button.setEnabled(True)
        hint = getattr(self, "skill_glyph_apply_hint", None)
        if isinstance(hint, QLabel):
            hint.setText("存在尚未应用的技能纹章修改。")

    def _skill_glyph_rows_from_table(self) -> list[dict[str, Any]]:
        table = getattr(self, "skill_glyph_table", None)
        if not isinstance(table, QTableWidget):
            return []
        count = min(table.rowCount(), int(self.skill_glyph_count_spin.value()))
        rows: list[dict[str, Any]] = []
        for row in range(count):
            skill = self.combo_text(table, row, 0)
            effect = self.combo_text(table, row, 1)
            option = self.combo_text(table, row, 2)
            value = self._skill_glyph_value(skill, effect, option)
            if skill != "无" and effect != "无" and value > 0:
                rows.append({"skill": skill, "effect": effect, "option": option, "value": value})
        return rows

    def apply_skill_glyph_changes(self) -> None:
        self._skill_glyph_applied_count = int(self.skill_glyph_count_spin.value())
        self._skill_glyph_applied_rows = self._skill_glyph_rows_from_table()
        self._skill_glyph_dirty = False
        self.build["skill_glyphs"] = {
            "second_job": self.second_job.currentText(),
            "count": self._skill_glyph_applied_count,
            "rows": copy.deepcopy(self._skill_glyph_applied_rows),
        }
        button = getattr(self, "skill_glyph_apply_button", None)
        if isinstance(button, QPushButton):
            button.setEnabled(False)
        hint = getattr(self, "skill_glyph_apply_hint", None)
        if isinstance(hint, QLabel):
            hint.setText("技能纹章已应用。")
        self.refresh()

    def _selected_skill_glyphs(self) -> list[dict[str, Any]]:
        applied = getattr(self, "_skill_glyph_applied_rows", None)
        if isinstance(applied, list):
            return copy.deepcopy(applied)
        saved = self.build.get("skill_glyphs", {}) if isinstance(self.build.get("skill_glyphs"), dict) else {}
        return [dict(row) for row in saved.get("rows", []) if isinstance(row, dict)]

    def _skill_glyph_effect_value(self, skill: str, effect: str) -> float:
        skill_key = self._skill_burst_skill_key(skill)
        applied = getattr(self, "_skill_glyph_applied_rows", None)
        if isinstance(applied, list):
            rows = applied
        else:
            saved = (
                self.build.get("skill_glyphs", {})
                if isinstance(self.build.get("skill_glyphs"), dict)
                else {}
            )
            saved_rows = saved.get("rows", [])
            rows = saved_rows if isinstance(saved_rows, (list, tuple)) else []
        cached = getattr(self, "_skill_glyph_effect_index_cache", None)
        if not (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is rows
            and isinstance(cached[1], dict)
        ):
            index: dict[tuple[str, str], float] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                key = (
                    self._skill_burst_skill_key(row.get("skill")),
                    norm(row.get("effect")),
                )
                index[key] = max(
                    index.get(key, 0.0),
                    max(0.0, as_number(row.get("value"))),
                )
            cached = (rows, index)
            self._skill_glyph_effect_index_cache = cached
        return max(
            0.0,
            as_number(cached[1].get((skill_key, norm(effect))))
        )

    def _skill_glyph_damage_multiplier(self, skill: str) -> float:
        bonus = self._skill_glyph_effect_value(skill, "伤害增加")
        if bonus <= 0:
            return 1.0
        final_percent = max(0.0, as_number(getattr(self, "_skill_burst_base_summary", {}).get("final_percent")))
        return damage_glyph_multiplier(bonus, final_percent)

    def _skill_glyph_buff_duration(self, skill: str, duration: float) -> float:
        bonus = max(
            self._skill_glyph_effect_value(skill, "增益持续"),
            self._skill_glyph_effect_value(skill, "增益持续时间增加"),
        )
        return max(0.0, as_number(duration)) * (1 + bonus / 100)

    def _skill_glyph_action_speed(self, skill: str) -> float:
        return max(
            self._skill_glyph_effect_value(skill, "动作速度增加"),
            self._skill_glyph_effect_value(skill, "动作速度"),
        )

    def _auto_skill_glyph_rows(
        self,
        count: int,
        contribution: dict[str, float],
        used_skills: set[str],
        final_percent: float,
        *,
        first_job: str | None = None,
        second_job: str | None = None,
        fill_to_count: bool = False,
        duration_context: dict[str, list[dict[str, float]]] | None = None,
        skill_timing_context: dict[str, dict[str, float]] | None = None,
        excluded_glyphs: set[tuple[str, str]] | None = None,
        forced_glyphs: list[tuple[str, str]] | None = None,
        candidate_glyphs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Choose skill glyphs from output and measurable buff-uptime gains."""

        count = max(1, min(7, int(as_number(count, 4))))
        normalized_contribution = {
            self._skill_burst_skill_key(skill): max(0.0, as_number(value))
            for skill, value in contribution.items()
            if self._skill_burst_skill_key(skill)
        }
        normalized_used = {
            self._skill_burst_skill_key(skill)
            for skill in used_skills
            if self._skill_burst_skill_key(skill)
        }
        candidates: list[tuple[float, float, str, str, str, float]] = []
        final_percent = max(0.0, as_number(final_percent))
        total = sum(normalized_contribution.values())
        normalized_duration_context = {
            self._skill_burst_skill_key(skill): [
                dict(row) for row in rows if isinstance(row, dict)
            ]
            for skill, rows in (duration_context or {}).items()
            if self._skill_burst_skill_key(skill)
        }
        normalized_timing_context = {
            self._skill_burst_skill_key(skill): dict(row)
            for skill, row in (skill_timing_context or {}).items()
            if self._skill_burst_skill_key(skill) and isinstance(row, dict)
        }
        resolved_first_job = norm(first_job) or norm(self.first_job.currentText())
        resolved_second_job = norm(second_job) or norm(self.second_job.currentText())

        def linked_cooldown_package(
            skill_key: str,
        ) -> tuple[float, float]:
            """Return linked output and cast time unlocked by one root cast."""

            linked_keys = {
                self._skill_burst_skill_key(name)
                for name in linked_cooldown_glyph_skills(
                    resolved_second_job,
                    skill_key,
                )
                if self._skill_burst_skill_key(name)
            }
            if not linked_keys:
                return 0.0, 0.0
            linked_output = sum(
                normalized_contribution.get(linked_key, 0.0)
                for linked_key in linked_keys
            )
            root_timing = normalized_timing_context.get(skill_key, {})
            root_casts = max(1.0, as_number(root_timing.get("casts"), 1.0))
            linked_cast_time = 0.0
            for linked_key in linked_keys:
                timing = normalized_timing_context.get(linked_key, {})
                cast_time = max(0.0, as_number(timing.get("cast_time")))
                casts = max(0.0, as_number(timing.get("casts")))
                # Thunderer's ten-second package can fit at most one cast of
                # each 20-second follow-up per Holy Ground application.
                casts_per_root = min(1.0, casts / root_casts)
                linked_cast_time += cast_time * casts_per_root
            return max(0.0, linked_output), max(0.0, linked_cast_time)

        def coverage_counterfactual_score(
            skill_key: str,
            effect_name: str,
            value: float,
        ) -> float:
            """Value only the candidate's extra fixed-axis effect coverage."""

            gain = 0.0
            for context in normalized_duration_context.get(skill_key, []):
                duration = max(0.0, as_number(context.get("duration")))
                cooldown = max(duration, as_number(context.get("cooldown")))
                if duration <= 0 or cooldown <= 0:
                    continue
                refresh_tail = max(0.0, as_number(context.get("refresh_tail")))
                effective_duration = duration + refresh_tail
                base_coverage = min(1.0, effective_duration / cooldown)
                if effect_name == "冷却缩减":
                    glyph_cooldown = cooldown * max(0.05, 1.0 - value / 100.0)
                    glyph_coverage = min(1.0, effective_duration / glyph_cooldown)
                elif effect_name in {"增益持续", "增益持续时间增加"}:
                    # A Buff-duration plate extends each application, not the
                    # lifetime of the summon/attack that keeps refreshing it.
                    glyph_coverage = min(
                        1.0,
                        (duration * (1.0 + value / 100.0) + refresh_tail) / cooldown,
                    )
                else:
                    continue
                coverage_delta = max(0.0, glyph_coverage - base_coverage)
                if coverage_delta <= 1e-12:
                    continue

                objective_gain = max(
                    0.0, as_number(context.get("team_objective_gain_ratio"))
                )
                if objective_gain > 0 or context.get("team_objective_gain_known"):
                    # This ratio already contains the owner's benefit plus the
                    # damage-weighted teammates, so it must not be multiplied by
                    # party size a second time.
                    gain += total * objective_gain * coverage_delta
                    continue

                multiplier = max(
                    1.0, as_number(context.get("active_multiplier"), 1.0)
                )
                team_multiplier = max(
                    1.0, as_number(context.get("team_multiplier"), 1.0)
                )
                cooldown_reduction = min(
                    70.0,
                    max(0.0, as_number(context.get("cooldown_reduction"))),
                )
                if multiplier > 1.0:
                    base_average = 1.0 + base_coverage * (multiplier - 1.0)
                    glyph_average = 1.0 + glyph_coverage * (multiplier - 1.0)
                    gain += total * team_multiplier * max(
                        0.0,
                        glyph_average / max(1e-9, base_average) - 1.0,
                    )
                if cooldown_reduction > 0:
                    active_rate = 1.0 / max(
                        0.3, 1.0 - cooldown_reduction / 100.0
                    )
                    base_rate = 1.0 + base_coverage * (active_rate - 1.0)
                    glyph_rate = 1.0 + glyph_coverage * (active_rate - 1.0)
                    gain += total * team_multiplier * max(
                        0.0,
                        glyph_rate / max(1e-9, base_rate) - 1.0,
                    )
            return max(0.0, gain)
        normalized_exclusions = {
            (
                self._skill_burst_skill_key(skill),
                (
                    "增益持续"
                    if norm(effect) in {"增益持续", "增益持续时间增加"}
                    else norm(effect)
                ),
            )
            for skill, effect in (excluded_glyphs or set())
            if self._skill_burst_skill_key(skill) and norm(effect)
        }
        forced_specs: list[tuple[str, str]] = [
            (norm(skill), norm(effect))
            for skill, effect in (forced_glyphs or [])
            if norm(skill) and norm(effect)
        ]
        normalized_forced_specs = {
            (self._skill_burst_skill_key(skill), effect)
            for skill, effect in forced_specs
        }
        # A full seven-slot build may need its least valuable existing plate
        # replaced by a required timing plate. Rank the actual effects/tiers,
        # not a higher-grade alternative or unrelated skills from the catalogue.
        candidate_values = {
            (
                self._skill_burst_skill_key(row.get("skill")),
                "增益持续" if norm(row.get("effect")) in {"增益持续", "增益持续时间增加"}
                else norm(row.get("effect")),
            ): as_number(row.get("value"))
            for row in (candidate_glyphs or []) if isinstance(row, dict)
        }
        for entry in self._skill_glyph_entries():
            skill = norm(entry.get("skill"))
            skill_key = self._skill_burst_skill_key(skill)
            build_level = self._skill_build_level_for_burst_sim(skill)
            if build_level is not None and build_level <= 0:
                continue
            effects = entry.get("effects", {}) if isinstance(entry.get("effects"), dict) else {}
            for effect, options in effects.items():
                normalized_effect = (
                    "增益持续"
                    if norm(effect) in {"增益持续", "增益持续时间增加"}
                    else norm(effect)
                )
                if (skill_key, normalized_effect) in normalized_exclusions:
                    continue
                if not isinstance(options, list) or not options:
                    continue
                if candidate_glyphs is not None:
                    wanted = candidate_values.get((skill_key, normalized_effect))
                    options = [
                        row for row in options
                        if wanted is not None and abs(as_number(row.get("value")) - wanted) <= 1e-6
                    ]
                    if not options:
                        continue
                option = max(options, key=lambda row: as_number(row.get("value")))
                value = max(0.0, as_number(option.get("value")))
                skill_output = normalized_contribution.get(skill_key, 0.0)
                local_score = 0.0
                if normalized_effect == "伤害增加":
                    score = skill_output * value / 100.0
                    local_score = score
                elif normalized_effect == "冷却缩减":
                    linked_output, linked_cast_time = linked_cooldown_package(
                        skill_key
                    )
                    package_output = skill_output + linked_output
                    direct_gain = (
                        package_output * value / max(1e-9, 100 - value)
                    )
                    coverage_gain = coverage_counterfactual_score(
                        skill_key, normalized_effect, value
                    )
                    # Stage one sees both direct output and a cheap coverage
                    # estimate, so a non-damaging team skill can enter the
                    # shortlist. Stage two subtracts only the extra casts'
                    # fixed-axis opportunity cost; unrelated skills never get
                    # globally replanned for this comparison.
                    score = direct_gain + coverage_gain
                    local_score = score
                    timing_context = normalized_timing_context.get(skill_key, {})
                    window = max(0.0, as_number(timing_context.get("window")))
                    cast_time = max(0.0, as_number(timing_context.get("cast_time")))
                    casts = max(0.0, as_number(timing_context.get("casts")))
                    if window > 0 and cast_time > 0 and casts > 0 and value < 100:
                        extra_casts = casts * value / max(1e-9, 100.0 - value)
                        package_cast_time = cast_time + linked_cast_time
                        other_output_rate = max(
                            0.0,
                            total - package_output,
                        ) / window
                        local_score = max(
                            0.0,
                            local_score
                            - other_output_rate
                            * package_cast_time
                            * extra_casts,
                        )
                elif normalized_effect in {"动作速度增加", "动作速度"}:
                    timing = self.store.skill_cast_time(
                        skill,
                        [
                            norm(second_job) or norm(self.second_job.currentText()),
                            norm(first_job) or norm(self.first_job.currentText()),
                        ],
                    )
                    base_cast = self.store.effective_skill_cast_time(timing)
                    accelerated_cast = self.store.effective_skill_cast_time(
                        timing, value
                    )
                    if base_cast <= 0 or accelerated_cast >= base_cast - 1e-9:
                        # A score policy may deliberately select a utility
                        # action-speed glyph for a channeled/support skill that
                        # has no ordinary damage-cast timing row. Keep it as a
                        # zero-output candidate so the explicit utility policy
                        # can select it without inventing a cast duration.
                        if (skill_key, norm(effect)) not in normalized_forced_specs:
                            continue
                        score = 0.0
                        local_score = 0.0
                    else:
                        # The first stage uses the old inexpensive proxy.  The
                        # local counterfactual values the exact time freed on the
                        # unchanged baseline axis and lets other skills consume
                        # that time at their average output rate.
                        score = skill_output * (1.0 - accelerated_cast / base_cast)
                        local_score = score
                        timing_context = normalized_timing_context.get(skill_key, {})
                        window = max(0.0, as_number(timing_context.get("window")))
                        casts = max(0.0, as_number(timing_context.get("casts")))
                        if window > 0 and casts > 0:
                            saved_time = max(0.0, base_cast - accelerated_cast) * casts
                            other_output_rate = max(0.0, total - skill_output) / window
                            local_score = max(0.0, other_output_rate * saved_time)
                elif normalized_effect in {"增益持续", "增益持续时间增加"}:
                    timing = self.store.skill_effect_timing(
                        norm(second_job) or norm(self.second_job.currentText()), skill
                    )
                    base_duration = max(0.0, as_number(timing.get("duration")))
                    contexts = normalized_duration_context.get(skill_key, [])
                    if contexts:
                        score = coverage_counterfactual_score(
                            skill_key, normalized_effect, value
                        )
                        local_score = score
                    elif base_duration > 0 and skill_output > 0:
                        # Periodic damage whose total panel scales with lifetime,
                        # such as Holy Punishment, gains the same proportion of
                        # total damage as the duration extension.
                        score = skill_output * value / 100.0
                        local_score = score
                    elif base_duration > 0 and skill_key in normalized_used:
                        # Keep a conservative fallback for a real timed buff whose
                        # panel multiplier cannot yet be derived from its attrs.
                        score = total * value / 100.0 * 0.08
                        local_score = score
                    else:
                        # A duration glyph without a measurable timed effect (for
                        # example Devotion's defensive stance) is not an output
                        # glyph and must not fill a theoretical DPS build.
                        continue
                else:
                    continue
                confidence_weight = glyph_marginal_score_multiplier(
                    skill,
                    normalized_effect,
                    resolved_second_job,
                )
                score *= confidence_weight
                local_score *= confidence_weight
                candidates.append(
                    (
                        score,
                        local_score,
                        skill,
                        normalized_effect,
                        self._skill_glyph_option_text(option),
                        value,
                    )
                )
        forced_specs.extend(required_timing_skill_glyphs(resolved_second_job, resolved_first_job))

        selector_candidates = [
            {
                "skill": candidate[2],
                "effect": candidate[3],
                "option": candidate[4],
                "value": candidate[5],
                "score": candidate[0],
                "local_score": candidate[1],
            }
            for candidate in candidates
        ]
        selected = select_skill_glyphs_two_stage(
            selector_candidates,
            count,
            forced=forced_specs,
            fill_to_count=fill_to_count,
        )
        return [
            {
                key: value
                for key, value in candidate.items()
                if key not in {"score", "local_score"}
            }
            for candidate in selected
        ]

    def auto_select_skill_glyphs(self) -> None:
        count = int(self.skill_glyph_count_spin.value())
        # The add-point page is normally open while the long-simulation tab is
        # not mounted, so reusing the last short burst axis systematically
        # overvalued low-cooldown fillers.  Build one cached 125-second,
        # no-glyph baseline instead.  This is the same fixed-axis evidence used
        # by score refinement and prevents the current plate selection from
        # reinforcing itself on the next click.
        segments: list[dict[str, Any]] = []
        sentinel = object()
        saved_rows = getattr(self, "_skill_glyph_applied_rows", sentinel)
        try:
            self._skill_glyph_applied_rows = []
            self.__dict__.pop("_skill_glyph_effect_index_cache", None)
            long_window = self._skill_long_window_seconds()
            context = {
                "effects": (
                    self._selected_team_effects(
                        use_base_cooldowns=True,
                        dedup=True,
                    )
                    if hasattr(self, "team_effect_table")
                    else []
                ),
                "schedule": self._skill_long_schedule(long_window),
                "windows": [],
                "provider": "",
                "duplicate_build_buffs": set(),
            }
            fixed_axis = self._team_practical_cached_simulate(
                [], context, None
            )
            segments = [
                copy.deepcopy(row)
                for row in fixed_axis.get("auto_segments", ())
                if isinstance(row, dict)
            ]
        except Exception:
            segments = []
        finally:
            if saved_rows is sentinel:
                self.__dict__.pop("_skill_glyph_applied_rows", None)
            else:
                self._skill_glyph_applied_rows = saved_rows
            self.__dict__.pop("_skill_glyph_effect_index_cache", None)
        if not segments:
            segments = (
                list(getattr(self, "_skill_long_auto_segments", []))
                or list(getattr(self, "_skill_burst_auto_segments", []))
            )
        contribution: dict[str, float] = {}
        used_skills: set[str] = set()
        for segment in segments:
            key = self._skill_burst_skill_key(segment.get("skill"))
            if key:
                used_skills.add(key)
                contribution[key] = contribution.get(key, 0.0) + max(
                    0.0,
                    as_number(segment.get("effective_score")),
                )
        selected = self._auto_skill_glyph_rows(
            count,
            contribution,
            used_skills,
            max(
                0.0,
                as_number(
                    getattr(self, "_skill_burst_base_summary", {}).get("final_percent")
                ),
            ),
        )
        for row in range(7):
            selected_row = selected[row] if row < len(selected) else {}
            skill = norm(selected_row.get("skill")) or "无"
            effect = norm(selected_row.get("effect")) or "无"
            option = norm(selected_row.get("option")) or "无"
            self._set_combo_current(self.skill_glyph_table.cellWidget(row, 0), skill)
            self._skill_glyph_skill_changed(row)
            self._set_combo_current(self.skill_glyph_table.cellWidget(row, 1), effect)
            self._skill_glyph_effect_changed(row)
            self._set_combo_current(self.skill_glyph_table.cellWidget(row, 2), option)
        self.apply_skill_glyph_changes()

    def _create_skill_mastery_node(self, skill: dict[str, Any], saved_levels: dict[str, Any]) -> dict[str, Any]:
        skill_id = norm(skill.get("id"))
        skill_build_spins = dict(getattr(self, "skill_build_level_spins", {}))
        node = self._create_skill_build_node(skill, saved_levels)
        self.skill_build_level_spins = skill_build_spins
        spin = node["spin"]
        spin.setRange(0, 1)
        spin.setValue(1 if int(as_number(saved_levels.get(skill_id))) > 0 else 0)
        spin.setProperty("previous_value", spin.value())
        with contextlib.suppress(TypeError):
            spin.valueChanged.disconnect()
        with contextlib.suppress(TypeError):
            node["minus"].clicked.disconnect()
        with contextlib.suppress(TypeError):
            node["plus"].clicked.disconnect()
        spin.valueChanged.connect(lambda value, sid=skill_id: self._skill_mastery_spin_changed(sid, value))
        node["minus"].clicked.connect(lambda _checked=False, sid=skill_id: self._skill_mastery_step_level(sid, -1))
        node["plus"].clicked.connect(lambda _checked=False, sid=skill_id: self._skill_mastery_step_level(sid, 1))
        node["level"].setText(f"专精 {spin.value()}/1")
        node["sp"].setText(f"SP {self._skill_build_sp_at_level(skill, spin.value())}")
        base_level = self._skill_mastery_base_level(skill_id)
        node["cd"].setText(self._skill_build_cooldown_text(skill, base_level, 1 if spin.value() > 0 else 0))
        node["detail"].setText("选中后：对应技能树技能显示专精+1，并按高一级面板计算")
        self.skill_mastery_spins[skill_id] = spin
        return node

    def _skill_build_changed(self) -> None:
        self.update_skill_build_preview()
        self.update_skill_mastery_preview()
        if hasattr(self, "skill_sim_table"):
            self._refresh_skill_sim_options()
        self.schedule_refresh()

    def _skill_mastery_changed(self) -> None:
        self.update_skill_mastery_preview()
        self.update_skill_build_preview()
        if hasattr(self, "skill_sim_table"):
            for row in range(SKILL_SIM_ROWS):
                self._skill_sim_level_changed(row)
        self.schedule_refresh()

    def _skill_build_current_levels(self) -> dict[str, int]:
        return {
            skill_id: int(spin.value())
            for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items()
            if isinstance(spin, QSpinBox)
        }

    @staticmethod
    def _skill_build_free_default_level(skill: dict[str, Any]) -> int:
        max_level = int(as_number(skill.get("max_level")))
        rows = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
        if max_level <= 0 or not rows:
            return 0
        return max_level if SkillBuildMixin._skill_build_sp_at_level(skill, max_level) <= 0 else 0

    def _skill_build_group_sp_from_levels(self, levels: dict[str, int]) -> list[int]:
        groups = self.skill_build_data.get("groups", []) if isinstance(self.skill_build_data.get("groups", []), list) else []
        group_sp: list[int] = []
        for group in groups:
            spent = 0
            for skill in group.get("skills", []) if isinstance(group.get("skills", []), list) else []:
                spent += self._skill_build_sp_at_level(skill, int(as_number(levels.get(norm(skill.get("id"))))))
            group_sp.append(spent)
        return group_sp

    def _skill_build_can_set_level(
        self,
        skill: dict[str, Any],
        target_level: int,
        levels: dict[str, int] | None = None,
    ) -> bool:
        skill_id = norm(skill.get("id"))
        if not skill_id:
            return False
        projected = dict(self._skill_build_current_levels() if levels is None else levels)
        current = int(as_number(projected.get(skill_id)))
        max_level = int(as_number(skill.get("max_level")))
        target = max(0, min(max_level, int(as_number(target_level))))
        if target <= current:
            return True
        projected[skill_id] = target
        group_sp = self._skill_build_group_sp_from_levels(projected)
        total_limit = int(as_number(self.skill_build_data.get("total_sp"))) if hasattr(self, "skill_build_data") else 0
        if total_limit and sum(group_sp) > total_limit:
            return False
        groups = self.skill_build_data.get("groups", []) if isinstance(self.skill_build_data.get("groups", []), list) else []
        for index, group in enumerate(groups):
            job = group.get("job", {}) if isinstance(group.get("job", {}), dict) else {}
            limit = int(as_number(job.get("sp")))
            if limit and index < len(group_sp) and group_sp[index] > limit:
                return False
        level_row = self._skill_build_level_row(skill, target)
        if target > 0 and int(as_number(level_row.get("required_level"), 9999)) > SKILL_BUILD_LEVEL_CAP:
            return False
        _req_text, req_ok = self._skill_build_requirement_text(skill, group_sp, projected)
        if not req_ok and not self._skill_build_has_learned_ultimate_peer(skill, projected):
            return False
        selected_exclusive: dict[str, list[str]] = {}
        for projected_id, level in projected.items():
            if int(as_number(level)) <= 0:
                continue
            projected_skill = self.skill_build_data.get("skills", {}).get(projected_id, {}) if isinstance(self.skill_build_data.get("skills", {}), dict) else {}
            for key in self._skill_build_exclusive_keys(projected_skill):
                selected_exclusive.setdefault(key, []).append(projected_id)
        return not any(len(items) > 1 for items in selected_exclusive.values())

    def _skill_build_highest_allowed_level(self, skill_id: str) -> int:
        spin = getattr(self, "skill_build_level_spins", {}).get(norm(skill_id))
        skill = self.skill_build_data.get("skills", {}).get(norm(skill_id), {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        if not isinstance(spin, QSpinBox) or not skill:
            return 0
        current = int(spin.value())
        levels = self._skill_build_current_levels()
        for target in range(int(spin.maximum()), current, -1):
            if self._skill_build_can_set_level(skill, target, levels):
                return target
        return current

    def _skill_build_step_level(self, skill_id: str, direction: int) -> None:
        spin = getattr(self, "skill_build_level_spins", {}).get(norm(skill_id))
        skill = self.skill_build_data.get("skills", {}).get(norm(skill_id), {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        if not isinstance(spin, QSpinBox) or not skill:
            return
        if QApplication.keyboardModifiers() & Qt.ShiftModifier:
            target = spin.minimum() if direction < 0 else self._skill_build_highest_allowed_level(skill_id)
        else:
            target = spin.value() + (1 if direction > 0 else -1)
            target = max(spin.minimum(), min(spin.maximum(), target))
        if direction > 0 and target > spin.value() and not self._skill_build_can_set_level(skill, target):
            return
        spin.setValue(target)

    def _skill_build_spin_changed(self, skill_id: str, value: int) -> None:
        spin = getattr(self, "skill_build_level_spins", {}).get(norm(skill_id))
        skill = self.skill_build_data.get("skills", {}).get(norm(skill_id), {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        if not isinstance(spin, QSpinBox) or not skill:
            self._skill_build_changed()
            return
        previous = int(as_number(spin.property("previous_value"), 0))
        levels = self._skill_build_current_levels()
        levels[norm(skill_id)] = previous
        if value > previous and not self._skill_build_can_set_level(skill, value, levels):
            spin.blockSignals(True)
            spin.setValue(previous)
            spin.blockSignals(False)
            self.update_skill_build_preview()
            return
        spin.setProperty("previous_value", int(value))
        self._skill_build_changed()

    def _skill_mastery_current_levels(self) -> dict[str, int]:
        return {
            skill_id: int(spin.value())
            for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items()
            if isinstance(spin, QSpinBox)
        }

    def _skill_mastery_group_sp_from_levels(self, levels: dict[str, int]) -> list[int]:
        groups = self.skill_mastery_data.get("groups", []) if isinstance(self.skill_mastery_data.get("groups", []), list) else []
        group_sp: list[int] = []
        for group in groups:
            spent = 0
            for skill in group.get("skills", []) if isinstance(group.get("skills", []), list) else []:
                spent += self._skill_build_sp_at_level(skill, int(as_number(levels.get(norm(skill.get("id"))))))
            group_sp.append(spent)
        return group_sp

    def _skill_mastery_base_level(self, skill_id: str) -> int:
        spin = getattr(self, "skill_build_level_spins", {}).get(norm(skill_id))
        return int(spin.value()) if isinstance(spin, QSpinBox) else 0

    def _skill_mastery_can_set_level(
        self,
        skill: dict[str, Any],
        target_level: int,
        levels: dict[str, int] | None = None,
    ) -> bool:
        skill_id = norm(skill.get("id"))
        if not skill_id:
            return False
        projected = dict(levels or self._skill_mastery_current_levels())
        current = int(as_number(projected.get(skill_id)))
        target = max(0, min(1, int(as_number(target_level))))
        if target <= current:
            return True
        if self._skill_mastery_base_level(skill_id) <= 0:
            return False
        projected[skill_id] = target
        group_sp = self._skill_mastery_group_sp_from_levels(projected)
        total_limit = int(as_number(self.skill_mastery_data.get("total_sp"))) if hasattr(self, "skill_mastery_data") else 0
        if total_limit and sum(group_sp) > total_limit:
            return False
        _req_text, req_ok = self._skill_build_requirement_text(skill, group_sp, projected)
        return req_ok

    def _skill_mastery_step_level(self, skill_id: str, direction: int) -> None:
        spin = getattr(self, "skill_mastery_spins", {}).get(norm(skill_id))
        skill = self.skill_mastery_data.get("skills", {}).get(norm(skill_id), {}) if hasattr(self, "skill_mastery_data") and isinstance(self.skill_mastery_data.get("skills", {}), dict) else {}
        if not isinstance(spin, QSpinBox) or not skill:
            return
        target = spin.minimum() if direction < 0 else spin.maximum()
        if not (QApplication.keyboardModifiers() & Qt.ShiftModifier):
            target = spin.value() + (1 if direction > 0 else -1)
        target = max(spin.minimum(), min(spin.maximum(), target))
        if direction > 0 and target > spin.value() and not self._skill_mastery_can_set_level(skill, target):
            return
        spin.setValue(target)

    def _skill_mastery_spin_changed(self, skill_id: str, value: int) -> None:
        spin = getattr(self, "skill_mastery_spins", {}).get(norm(skill_id))
        skill = self.skill_mastery_data.get("skills", {}).get(norm(skill_id), {}) if hasattr(self, "skill_mastery_data") and isinstance(self.skill_mastery_data.get("skills", {}), dict) else {}
        if not isinstance(spin, QSpinBox) or not skill:
            self._skill_mastery_changed()
            return
        previous = int(as_number(spin.property("previous_value"), 0))
        levels = self._skill_mastery_current_levels()
        levels[norm(skill_id)] = previous
        if value > previous and not self._skill_mastery_can_set_level(skill, value, levels):
            spin.blockSignals(True)
            spin.setValue(previous)
            spin.blockSignals(False)
            self.update_skill_mastery_preview()
            return
        spin.setProperty("previous_value", int(value))
        self._skill_mastery_changed()

    def _selected_skill_mastery_ids(self) -> set[str]:
        return {
            skill_id
            for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items()
            if isinstance(spin, QSpinBox) and spin.value() > 0
        }

    def _skill_mastery_bonus_for_skill(self, skill: Any) -> int:
        selected = self._selected_skill_mastery_ids()
        if isinstance(skill, dict):
            keys = {norm(skill.get("id")), norm(skill.get("name"))}
        else:
            skill_name = norm(skill)
            entry = self.store._skill_damage_entry(self.second_job.currentText(), skill_name)
            keys = {skill_name, str(entry.get("id")) if entry else ""}
            tree = getattr(self, "skill_build_data", None)
            if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
                tree = self.store.skill_build_tree(
                    self.second_job.currentText(),
                    SKILL_BUILD_LEVEL_CAP,
                )
            keys.update(
                norm(skill_id)
                for skill_id, item in tree.get("skills", {}).items()
                if norm(item.get("name")) == skill_name
            )
        return 1 if any(key and key in selected for key in keys) else 0

    def update_skill_mastery_preview(self) -> None:
        summary_widget = getattr(self, "skill_mastery_summary", None)
        if (
            not hasattr(self, "skill_mastery_data")
            or not hasattr(self, "skill_mastery_nodes")
            or not qt_object_is_valid(summary_widget)
        ):
            return
        valid_spins = {
            skill_id: spin
            for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items()
            if isinstance(spin, QSpinBox) and qt_object_is_valid(spin)
        }
        self.skill_mastery_spins = valid_spins
        levels = {
            skill_id: int(spin.value())
            for skill_id, spin in valid_spins.items()
        }
        groups = self.skill_mastery_data.get("groups", []) if isinstance(self.skill_mastery_data.get("groups", []), list) else []
        group_sp: list[int] = []
        invalid_count = 0
        for group in groups:
            spent = 0
            for skill in group.get("skills", []) if isinstance(group.get("skills", []), list) else []:
                spent += self._skill_build_sp_at_level(skill, int(as_number(levels.get(norm(skill.get("id"))))))
            group_sp.append(spent)
        for group_index, group in enumerate(groups):
            for skill in group.get("skills", []) if isinstance(group.get("skills", []), list) else []:
                skill_id = norm(skill.get("id"))
                level = int(as_number(levels.get(skill_id)))
                node = self.skill_mastery_nodes.get(skill_id)
                if not node:
                    continue
                req_text, req_ok = self._skill_build_requirement_text(skill, group_sp, levels)
                if self._skill_mastery_base_level(skill_id) <= 0:
                    req_ok = False
                    req_text = f"{req_text}；需先学习普通技能" if req_text != "无" else "需先学习普通技能"
                if level > 0 and not req_ok:
                    invalid_count += 1
                node["spin"].setProperty("previous_value", level)
                node["level"].setText(f"{'已专精' if level > 0 else '未专精'} {level}/1")
                spent = self._skill_build_sp_at_level(skill, level)
                node["sp"].setText(f"SP {spent}")
                base_level = self._skill_mastery_base_level(skill_id)
                cd_text = self._skill_build_cooldown_text(skill, base_level, 1 if level > 0 else 0)
                node["cd"].setText(cd_text)
                node["req"].setText(req_text if req_text != "无" else "选中后专精+1")
                display_summary = getattr(self, "_last_summary", {})
                if base_level > 0:
                    current_line = self._skill_build_damage_line(skill, base_level, display_summary, 1 if level > 0 else 0)
                    next_line = "已满级" if level > 0 else self._skill_build_damage_line(skill, base_level, display_summary, 1)
                else:
                    current_line = "未学习普通技能"
                    next_line = "需先学习普通技能"
                node["detail"].setText(f"当前 {current_line}\n下级 {next_line}")
                node["widget"].setStyleSheet(self._skill_build_node_style(skill, level, req_ok, not req_ok))
                tooltip = "\n".join([
                    norm(skill.get("name")) or skill_id,
                    f"技能类型：{self._skill_build_kind_label(self._skill_build_kind(skill))}",
                    f"专精状态：{level}/1",
                    f"普通技能等级：{base_level}",
                    f"累计消耗 SP：{spent}",
                    cd_text,
                    f"每级 SP：{self._skill_build_sp_breakdown(skill)}",
                    f"前置/需求：{req_text}",
                    f"当前数值：{current_line}",
                    f"下级数值：{next_line}",
                ])
                for widget in (node["widget"], node["name"], node["level"], node["sp"], node["cd"], node["req"], node["spin"]):
                    widget.setToolTip(tooltip)
                node["minus"].setEnabled(level > 0)
                node["plus"].setEnabled(level < 1 and req_ok and self._skill_mastery_can_set_level(skill, level + 1, levels))
            job = group.get("job", {}) if isinstance(group.get("job", {}), dict) else {}
            spent = group_sp[group_index] if group_index < len(group_sp) else 0
            label = getattr(self, "skill_mastery_group_labels", {}).get(group_index)
            total_limit = int(as_number(self.skill_mastery_data.get("total_sp")))
            text = f"{job.get('name')} {spent}/{total_limit or '-'}"
            if isinstance(label, QLabel):
                label.setText(text)
        total_sp = sum(group_sp)
        total_limit = int(as_number(self.skill_mastery_data.get("total_sp")))
        total_text = f"总专精SP {total_sp}/{total_limit or '-'}"
        if total_limit and total_sp > total_limit:
            invalid_count += 1
            total_text += " 超出"
        if invalid_count:
            total_text += f"；{invalid_count}项需要调整"
        self.skill_mastery_summary.setText(total_text)

    def reset_skill_mastery(self) -> None:
        for spin in getattr(self, "skill_mastery_spins", {}).values():
            if isinstance(spin, QSpinBox):
                spin.blockSignals(True)
                spin.setValue(0)
                spin.setProperty("previous_value", spin.value())
                spin.blockSignals(False)
        self.update_skill_mastery_preview()
        self.update_skill_build_preview()
        self.schedule_refresh()

    def _skill_build_default_payload_for_current(self) -> dict[str, Any]:
        payload = load_skill_build_defaults().get(norm(self.second_job.currentText()))
        return payload if isinstance(payload, dict) else {}

    def _skill_build_current_level_sections(self) -> tuple[dict[str, Any], dict[str, Any]]:
        job = norm(self.second_job.currentText())
        build_levels = {
            skill_id: int(spin.value())
            for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items()
            if isinstance(spin, QSpinBox)
            and qt_object_is_valid(spin)
            and spin.value() > 0
        }
        mastery_levels = {
            skill_id: int(spin.value())
            for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items()
            if isinstance(spin, QSpinBox)
            and qt_object_is_valid(spin)
            and spin.value() > 0
        }
        return (
            {"second_job": job, "levels": build_levels},
            {"second_job": job, "levels": mastery_levels},
        )

    def _skill_build_auto_glyphs_for_current(self, count: int = 4) -> dict[str, Any]:
        job = norm(self.second_job.currentText())
        count = max(1, min(7, int(as_number(count, 4))))
        skill_build, mastery_build = self._skill_build_current_level_sections()
        candidate = copy.deepcopy(self.build)
        candidate["first_job"] = norm(self.first_job.currentText())
        candidate["second_job"] = job
        candidate["skill_build"] = skill_build
        candidate["skill_mastery_build"] = mastery_build
        selector = getattr(self, "_extreme_apply_auto_skill_glyphs", None)
        if callable(selector):
            selector(candidate, job, count=count)
        glyphs = candidate.get("skill_glyphs", {})
        if not isinstance(glyphs, dict):
            glyphs = {}
        rows = [
            dict(row)
            for row in glyphs.get("rows", [])
            if isinstance(row, dict)
        ]
        return {
            "second_job": job,
            "count": count,
            "rows": rows[:count],
            "auto_selected": True,
        }

    def _skill_build_default_glyphs_for_current(self) -> dict[str, Any]:
        job = norm(self.second_job.currentText())
        saved = self.build.get("skill_glyphs", {})
        if (
            isinstance(saved, dict)
            and norm(saved.get("second_job")) == job
            and any(isinstance(row, dict) for row in saved.get("rows", []))
        ):
            return copy.deepcopy(saved)

        default_glyphs = self._skill_build_default_payload_for_current().get(
            "skill_glyphs", {}
        )
        if (
            isinstance(default_glyphs, dict)
            and norm(default_glyphs.get("second_job")) == job
            and any(
                isinstance(row, dict) for row in default_glyphs.get("rows", [])
            )
        ):
            return copy.deepcopy(default_glyphs)
        return self._skill_build_auto_glyphs_for_current(4)

    def _apply_skill_build_default_glyphs(self, payload: dict[str, Any]) -> None:
        job = norm(self.second_job.currentText())
        glyphs = payload.get("skill_glyphs", {}) if isinstance(payload, dict) else {}
        if not (
            isinstance(glyphs, dict)
            and norm(glyphs.get("second_job")) == job
            and any(isinstance(row, dict) for row in glyphs.get("rows", []))
        ):
            glyphs = self._skill_build_auto_glyphs_for_current(4)
        self.build["skill_glyphs"] = copy.deepcopy(glyphs)
        self._populate_skill_glyph_tab()

    @staticmethod
    def _skill_build_payload_levels(payload: dict[str, Any], key: str) -> dict[str, Any]:
        section = payload.get(key)
        if not isinstance(section, dict):
            return {}
        levels = section.get("levels")
        return dict(levels) if isinstance(levels, dict) else {}

    def _skill_build_initial_levels(self) -> tuple[dict[str, Any], dict[str, Any]]:
        current_job = norm(self.second_job.currentText())
        default_payload = self._skill_build_default_payload_for_current()
        default_levels = self._skill_build_payload_levels(default_payload, "skill_build")
        default_mastery = self._skill_build_payload_levels(default_payload, "skill_mastery_build")

        saved = self.build.get("skill_build", {}) if isinstance(self.build.get("skill_build", {}), dict) else {}
        saved_mastery = self.build.get("skill_mastery_build", {}) if isinstance(self.build.get("skill_mastery_build", {}), dict) else {}
        saved_job = norm(saved.get("second_job") or saved_mastery.get("second_job"))
        if saved_job and saved_job == current_job:
            return (
                self._skill_build_payload_levels({"skill_build": saved}, "skill_build"),
                self._skill_build_payload_levels({"skill_mastery_build": saved_mastery}, "skill_mastery_build"),
            )
        return default_levels, default_mastery

    def _current_skill_build_default_payload(self) -> dict[str, Any]:
        skill_build, mastery_build = self._skill_build_current_level_sections()
        glyphs = self._skill_build_default_glyphs_for_current()
        return {
            "first_job": self.first_job.currentText(),
            "second_job": self.second_job.currentText(),
            "level_cap": SKILL_BUILD_LEVEL_CAP,
            "saved_at": int(time.time()),
            "skill_build": skill_build,
            "skill_mastery_build": mastery_build,
            "skill_glyphs": copy.deepcopy(glyphs),
        }

    def save_current_skill_build_default(self) -> None:
        reply = QMessageBox.question(
            self,
            "修改默认加点",
            f"是否将当前加点修改为 {self.second_job.currentText()} 的默认加点？\n以后选择该职业时会自动读取这套加点。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        defaults = load_user_skill_build_defaults()
        defaults[norm(self.second_job.currentText())] = self._current_skill_build_default_payload()
        try:
            save_skill_build_defaults(defaults)
        except OSError as exc:
            QMessageBox.warning(self, "修改失败", f"默认加点修改失败：{exc}")
            return
        QMessageBox.information(self, "修改成功", f"已修改 {self.second_job.currentText()} 的默认加点。")

    def export_skill_build_scheme(self) -> None:
        job = norm(self.second_job.currentText())
        default_name = f"{job}加点方案"
        scheme_name, accepted = QInputDialog.getText(
            self,
            "导出加点方案",
            "方案名字：",
            text=default_name,
        )
        scheme_name = norm(scheme_name)
        if not accepted or not scheme_name:
            return
        defaults = load_user_skill_build_defaults()
        schemes_root = defaults.setdefault(SKILL_BUILD_SCHEMES_KEY, {})
        if not isinstance(schemes_root, dict):
            schemes_root = {}
            defaults[SKILL_BUILD_SCHEMES_KEY] = schemes_root
        job_schemes = schemes_root.setdefault(job, {})
        if not isinstance(job_schemes, dict):
            job_schemes = {}
            schemes_root[job] = job_schemes
        if scheme_name in job_schemes:
            reply = QMessageBox.question(
                self,
                "覆盖加点方案",
                f"已经存在“{scheme_name}”，是否覆盖？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        job_schemes[scheme_name] = self._current_skill_build_default_payload()
        try:
            save_skill_build_defaults(defaults)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"加点方案导出失败：{exc}")
            return
        QMessageBox.information(self, "导出成功", f"已导出“{scheme_name}”。")

    def import_skill_build_scheme(self) -> None:
        job = norm(self.second_job.currentText())
        schemes = skill_build_schemes_for_job(
            load_builtin_skill_build_defaults(), job
        )
        schemes.update(
            skill_build_schemes_for_job(load_user_skill_build_defaults(), job)
        )
        if not schemes:
            QMessageBox.information(self, "没有加点方案", f"当前没有找到 {job} 的已导出加点方案。")
            return
        scheme_names = list(schemes)
        scheme_name, accepted = QInputDialog.getItem(
            self,
            "导入加点方案",
            f"已找到 {len(scheme_names)} 个 {job} 加点方案：",
            scheme_names,
            0,
            False,
        )
        if not accepted or scheme_name not in schemes:
            return
        payload = schemes[scheme_name]
        build_levels = ((payload.get("skill_build") or {}).get("levels") or {}) if isinstance(payload.get("skill_build"), dict) else {}
        mastery_levels = ((payload.get("skill_mastery_build") or {}).get("levels") or {}) if isinstance(payload.get("skill_mastery_build"), dict) else {}
        self._apply_skill_build_levels(build_levels, mastery_levels)
        self._apply_skill_build_default_glyphs(payload)
        QMessageBox.information(self, "导入成功", f"已导入“{scheme_name}”。")

    def apply_skill_build_default(self) -> None:
        payload = load_skill_build_defaults().get(norm(self.second_job.currentText()))
        if not isinstance(payload, dict):
            return
        build_levels = ((payload.get("skill_build") or {}).get("levels") or {}) if isinstance(payload.get("skill_build"), dict) else {}
        mastery_levels = ((payload.get("skill_mastery_build") or {}).get("levels") or {}) if isinstance(payload.get("skill_mastery_build"), dict) else {}
        self._apply_skill_build_levels(build_levels, mastery_levels)
        self._apply_skill_build_default_glyphs(payload)

    def _apply_skill_build_levels(self, build_levels: dict[str, Any], mastery_levels: dict[str, Any]) -> None:
        build_levels = self._skill_build_sanitize_levels(build_levels)
        skill_lookup = self.skill_build_data.get("skills", {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items():
            if isinstance(spin, QSpinBox):
                skill = skill_lookup.get(skill_id, {})
                value = int(as_number(build_levels.get(skill_id))) if skill_id in build_levels else self._skill_build_free_default_level(skill)
                spin.blockSignals(True)
                spin.setValue(max(0, min(spin.maximum(), value)))
                spin.setProperty("previous_value", spin.value())
                spin.blockSignals(False)
        for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items():
            if isinstance(spin, QSpinBox):
                spin.blockSignals(True)
                spin.setValue(max(0, min(spin.maximum(), int(as_number(mastery_levels.get(skill_id))))))
                spin.setProperty("previous_value", spin.value())
                spin.blockSignals(False)
        self.update_skill_mastery_preview()
        self.update_skill_build_preview()
        if hasattr(self, "skill_sim_table"):
            self._refresh_skill_sim_options()
            for row in range(SKILL_SIM_ROWS):
                self._skill_sim_level_changed(row)
        self.schedule_refresh()

    def _skill_build_score(self, skill: dict[str, Any], level: int, summary: dict[str, float], mastery_bonus: int = 0) -> float:
        if level <= 0:
            return 0.0
        level_data = self._skill_build_level_data_for_display(skill, level, mastery_bonus)
        if not level_data:
            return 0.0
        skill_name = norm(skill.get("name"))
        shown_level = str(level + max(0, mastery_bonus))
        multiplier = self._skill_adjusted_multiplier(skill_name, shown_level)
        display_multiplier = multiplier + self._skill_burning_hand_bonus(skill_name)
        cooldown = self._skill_level_cooldown(level_data)
        if cooldown <= 0:
            return 0.0
        weighted = self._skill_weighted_panel_profile()
        profile = self._job_profile()
        preferred = None if weighted else ("M" if profile.get("attack_attr") == "魔攻" else "P")
        score = self._skill_level_effective(
            level_data,
            summary,
            multiplier=display_multiplier,
            preferred_type=preferred,
            weighted_panel=weighted,
        ) / cooldown
        element = self.store.skill_damage_element(self.second_job.currentText(), skill_name)
        if norm(element):
            score *= self._skill_element_display_multiplier(element, summary)
        else:
            score *= self._skill_sim_dps_multiplier("", profile)
        return max(0.0, score)

    def _skill_build_personal_buff_names(self) -> set[str]:
        jobs = {norm(self.first_job.currentText()), norm(self.second_job.currentText()), "通用"}
        names: set[str] = set()
        for row in self.store.tables.get("buff", []):
            if norm(row.get("来源")) not in jobs:
                continue
            if norm(row.get("类别")) not in {"个人buff", "被动"}:
                continue
            skill_name = norm(row.get("技能"))
            if skill_name:
                names.add(skill_name)
        return names

    def _skill_build_buff_names_by_category(self, *categories: str) -> set[str]:
        jobs = {norm(self.first_job.currentText()), norm(self.second_job.currentText()), "通用"}
        wanted = {norm(category) for category in categories}
        names: set[str] = set()
        for row in self.store.tables.get("buff", []):
            if norm(row.get("来源")) not in jobs:
                continue
            if norm(row.get("类别")) not in wanted:
                continue
            skill_name = norm(row.get("技能"))
            if skill_name:
                names.add(skill_name)
        return names

    @staticmethod
    def _skill_build_is_ex_skill(skill: dict[str, Any]) -> bool:
        return "EX" in norm(skill.get("name")).upper()

    @staticmethod
    def _skill_build_manual_effect_kind(skill_name: str, skill_id: Any = "") -> str:
        name = norm(skill_name)
        if name == "重击" and norm(skill_id) and norm(skill_id) != "3203":
            return ""
        attrs = TEAM_MANUAL_DEBUFFS.get(name, ({}, []))[0]
        keys = {norm(key) for key in attrs}
        if any(key.startswith("减") for key in keys) or any("增伤" in key for key in keys) or "冰冻层" in keys:
            return "debuff"
        if keys:
            return "team_buff"
        return ""

    @staticmethod
    def _skill_build_base_name_from_ex(skill_name: str) -> str:
        name = norm(skill_name)
        upper = name.upper()
        if upper.endswith(" EX"):
            return name[:-3].strip()
        if upper.endswith("EX"):
            return name[:-2].strip()
        return name

    def _skill_build_ex_enhanced_kind(self, skill: dict[str, Any], levels: dict[str, int]) -> str:
        name = norm(skill.get("name"))
        if not name or self._skill_build_is_ex_skill(skill):
            return ""
        all_skills = self.skill_build_data.get("skills", {}) if isinstance(getattr(self, "skill_build_data", {}).get("skills", {}), dict) else {}
        for ex_id, ex_skill in all_skills.items():
            if int(as_number(levels.get(norm(ex_id)))) <= 0:
                continue
            if not self._skill_build_is_ex_skill(ex_skill):
                continue
            if self._skill_build_base_name_from_ex(ex_skill.get("name")) != name:
                continue
            ex_kind = self._skill_build_manual_effect_kind(ex_skill.get("name"))
            if ex_kind == "debuff":
                return "debuff"
        return ""

    def _skill_build_kind(self, skill: dict[str, Any]) -> str:
        name = norm(skill.get("name"))
        if self._skill_build_is_ex_skill(skill):
            return "ex"
        manual_kind = self._skill_build_manual_effect_kind(name, skill.get("id"))
        if manual_kind:
            return manual_kind
        if name in self._skill_build_buff_names_by_category("团队buff"):
            return "team_buff"
        if (
            name in self._skill_build_buff_names_by_category("个人buff")
            or self._skill_build_has_personal_gain(name)
            or self._skill_build_has_personal_gain_from_desc(skill)
        ):
            return "personal_buff"
        # DN聚集地网页的 _SkillType=1 对应“辅助技能”。在加点树颜色里统一按被动处理。
        is_raw_passive = int(as_number(skill.get("skill_type"))) == 1
        if name in self._skill_build_buff_names_by_category("被动") or is_raw_passive:
            return "passive"
        return "active"

    @staticmethod
    def _skill_build_is_personal_gain_attr(attr: Any) -> bool:
        text = norm(attr)
        if not text:
            return False
        if text.startswith("减") or "抗降低" in text or "增伤" in text or "冰冻层" in text:
            return False
        return True

    def _skill_build_has_personal_gain(self, skill_name: str) -> bool:
        name = norm(skill_name)
        jobs = {norm(self.first_job.currentText()), norm(self.second_job.currentText()), "通用"}
        for row in self.store.tables.get("buff", []):
            if norm(row.get("来源")) not in jobs:
                continue
            if norm(row.get("技能")) != name:
                continue
            if norm(row.get("类别")) == "团队buff":
                continue
            attr = norm(row.get("属性"))
            if attr and self._skill_build_is_personal_gain_attr(attr):
                return True
            levels = row.get("等级", {}) if isinstance(row.get("等级", {}), dict) else {}
            for attrs in levels.values():
                if not isinstance(attrs, dict):
                    continue
                if any(self._skill_build_is_personal_gain_attr(key) for key in attrs):
                    return True
        return False

    @staticmethod
    def _skill_build_desc_at_level(skill: dict[str, Any], level: int) -> str:
        rows = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
        if not rows:
            return norm(skill.get("desc"))
        index = max(0, min(len(rows) - 1, int(level or 1) - 1))
        return norm(rows[index].get("desc")) or norm(skill.get("desc"))

    @staticmethod
    def _skill_build_desc_personal_effect_attrs(skill: dict[str, Any], level: int) -> dict[str, Any]:
        desc = SkillBuildMixin._skill_build_desc_at_level(skill, level)
        if "物理攻击力" in desc or "魔法攻击力" in desc:
            return {}
        attrs: dict[str, Any] = {}
        for label, pattern in [
            ("物理伤害减少%", r"(?:受到)?物理伤害减少\s*([0-9]+(?:\.[0-9]+)?)%"),
            ("魔法伤害减少%", r"(?:受到)?魔法伤害减少\s*([0-9]+(?:\.[0-9]+)?)%"),
            ("超级盔甲%", r"超级盔甲增加\s*([0-9]+(?:\.[0-9]+)?)%"),
        ]:
            match = re.search(pattern, desc)
            if match:
                value = as_number(match.group(1))
                attrs[label] = int(value) if float(value).is_integer() else value
        return attrs

    @staticmethod
    def _skill_build_desc_effect_attrs(skill: dict[str, Any], level: int) -> dict[str, Any]:
        """Extract level-specific buff/debuff values from the original skill description."""
        desc = SkillBuildMixin._skill_build_desc_at_level(skill, max(1, int(level or 1)))
        if not desc:
            return {}
        lines = [line.strip() for line in re.split(r"[\r\n]+", desc) if line.strip()]
        number = r"([0-9]+(?:\.[0-9]+)?)"
        attrs: dict[str, Any] = {}

        def add(label: str, *patterns: str) -> None:
            for pattern in patterns:
                for line in lines:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if not match:
                        continue
                    value = as_number(match.group(1))
                    attrs[label] = int(value) if float(value).is_integer() else value
                    return

        for element in ("火", "水", "光", "暗"):
            add(
                f"{element}攻%",
                rf"(?:自身(?:的)?\s*)?{element}(?:属性)?攻击(?:力)?\s*(?:增加|提高|提升)\s*{number}%",
                rf"(?:增加|提高|提升)\s*(?:自身(?:的)?\s*)?{element}(?:属性)?攻击(?:力)?\s*{number}%",
            )
            add(
                f"减{element}抗%",
                rf"{element}(?:属性)?抗性\s*(?:降低|减少)\s*{number}%",
                rf"(?:降低|减少)\s*(?:敌人(?:的)?\s*)?{element}(?:属性)?抗性\s*{number}%",
            )

        for label, noun in (
            ("物攻%", "物理攻击力"),
            ("魔攻%", "魔法攻击力"),
            ("力量%", "力量"),
            ("敏捷%", "敏捷"),
            ("智慧%", "智慧"),
            ("体质%", "体质"),
            ("最终%", "最终伤害"),
            ("致命%", "致命一击(?:概率)?"),
            ("生命%", "(?:最大)?生命值"),
            ("防御力%", "物理防御力"),
            ("魔防%", "魔法防御力"),
        ):
            add(
                label,
                rf"(?:自身(?:的)?\s*)?{noun}\s*(?:增加|提高|提升)\s*{number}%",
                rf"(?:增加|提高|提升)\s*(?:自身(?:的)?\s*)?{noun}\s*{number}%",
            )

        combined_damage_amp = False
        for line in lines:
            match = re.search(
                rf"(?:所受|受到(?:的)?)?\s*物理[、和及/]魔法伤害(?:量)?\s*(?:增加|提高|提升)\s*{number}%",
                line,
            )
            if match:
                value = as_number(match.group(1))
                attrs["增伤%"] = int(value) if float(value).is_integer() else value
                combined_damage_amp = True
                break
        if not combined_damage_amp:
            for label, noun in (("物理增伤%", "物理伤害(?:量)?"), ("魔法增伤%", "魔法伤害(?:量)?")):
                add(
                    label,
                    rf"{noun}\s*(?:增加|提高|提升)\s*{number}%",
                    rf"(?:增加|提高|提升)\s*{noun}\s*{number}%",
                )
        add(
            "增伤%",
            rf"(?:目标|敌人)[^。；;]{{0,36}}(?:受到(?:的)?\s*)?(?:所有)?伤害(?:量)?\s*(?:增加|提高|提升)\s*{number}%",
            rf"受到(?:的)?\s*(?:所有)?伤害(?:量)?\s*(?:增加|提高|提升)\s*{number}%",
        )
        # “暗属性防御力降低”在游戏文本中表示减暗抗，不能再识别成普通减防。
        add("减防御%", rf"(?<!属性)(?:物理)?防御力\s*(?:降低|减少)\s*{number}%")
        add("减致命抵抗%", rf"致命(?:一击)?抵抗(?:率)?\s*(?:降低|减少)\s*{number}%")
        add("动作速度%", rf"(?:动作|攻击)速度\s*(?:增加|提高|提升)\s*{number}%")
        add("冷却缩减%", rf"冷却(?:时间)?\s*(?:减少|缩短|降低)\s*{number}%")
        return attrs

    @staticmethod
    def _skill_build_desc_effect_note(skill: dict[str, Any], level: int) -> str:
        desc = SkillBuildMixin._skill_build_desc_at_level(skill, max(1, int(level or 1)))
        per_effect = re.search(
            r"每\s*([0-9]+(?:\.[0-9]+)?)\s*点([^，。；;\r\n]+?)额外增加\s*([0-9]+(?:\.[0-9]+)?)%",
            desc,
        )
        cap = re.search(r"最高增加至\s*([0-9]+(?:\.[0-9]+)?)", desc)
        if per_effect:
            source = norm(per_effect.group(2))
            source = {
                "最大魔法攻击力": "最大魔攻",
                "最大物理攻击力": "最大物攻",
                "魔法攻击力": "魔攻",
                "物理攻击力": "物攻",
            }.get(source, source)
            text = f"每{per_effect.group(1)}{source}额外+{per_effect.group(3)}%"
            if cap:
                text += f"，上限{cap.group(1)}%"
            return text
        return ""

    def _skill_build_has_personal_gain_from_desc(self, skill: dict[str, Any]) -> bool:
        return bool(self._skill_build_desc_personal_effect_attrs(skill, 1))

    def _skill_build_display_kind(self, skill: dict[str, Any], levels: dict[str, int]) -> str:
        return self._skill_build_ex_enhanced_kind(skill, levels) or self._skill_build_kind(skill)

    @staticmethod
    def _skill_build_format_attrs(attrs: dict[str, Any], limit: int = 4) -> str:
        parts: list[str] = []
        for key, value in attrs.items():
            if value in (None, ""):
                continue
            number = as_number(value, default=float("nan"))
            if number == number:
                text = f"{int(number)}" if float(number).is_integer() else f"{number:g}"
            else:
                text = norm(value)
            parts.append(f"{norm(key)}+{text}")
        if not parts:
            return ""
        return "；".join(parts[:limit]) + (" ..." if len(parts) > limit else "")

    def _skill_build_buff_effect_attrs(self, skill_name: str, level: int) -> dict[str, Any]:
        name = norm(skill_name)
        jobs = {norm(self.first_job.currentText()), norm(self.second_job.currentText()), "通用"}
        flat_matches: list[dict[str, Any]] = []
        for row in self.store.tables.get("buff", []):
            if norm(row.get("来源")) not in jobs:
                continue
            if norm(row.get("技能")) != name:
                continue
            levels = row.get("等级", {}) if isinstance(row.get("等级", {}), dict) else {}
            if not levels:
                flat_matches.append(row)
                continue
            wanted = str(max(1, int(level or as_number(row.get("最大等级"), 1))))
            if wanted not in levels:
                numeric = sorted((int(as_number(key)) for key in levels), reverse=True)
                wanted = str(next((key for key in numeric if key <= int(as_number(wanted))), numeric[0] if numeric else 1))
            attrs = levels.get(wanted, {})
            return attrs if isinstance(attrs, dict) else {}
        if flat_matches:
            wanted_level = max(1, int(level or max(as_number(row.get("等级")) for row in flat_matches)))
            available = sorted({int(as_number(row.get("等级"))) for row in flat_matches})
            chosen = max((item for item in available if item <= wanted_level), default=available[-1] if available else wanted_level)
            attrs: dict[str, Any] = {}
            for row in flat_matches:
                if int(as_number(row.get("等级"))) != chosen:
                    continue
                attr = norm(row.get("属性"))
                if attr:
                    attrs[attr] = row.get("表格数值")
            return attrs
        return {}

    def _skill_build_effect_badge_text(self, skill: dict[str, Any], level: int, kind: str, levels: dict[str, int]) -> str:
        name = norm(skill.get("name"))
        if name in {"弗里兹科之舞", "幻影之舞"}:
            state = "刀锋强化" if name == "弗里兹科之舞" else "精神强化"
            duration = as_number(
                self.store.skill_effect_timing(
                    self.second_job.currentText(), name, max(1, int(as_number(level)))
                ).get("duration")
            )
            duration_text = f"，持续{duration:g}s" if duration > 0 else ""
            return f"个人：{state}，对应主力技能最终%+60{duration_text}；两种舞蹈共享CD"
        attrs: dict[str, Any] = {}
        label = ""
        if kind in {"team_buff", "personal_buff"}:
            attrs = self._skill_build_buff_effect_attrs(name, level)
            if not attrs and kind == "personal_buff":
                attrs = self._skill_build_desc_personal_effect_attrs(skill, level)
            label = "团队" if kind == "team_buff" else "个人"
        elif kind == "passive":
            attrs = self._skill_build_buff_effect_attrs(name, level)
            label = "被动"
        elif kind == "debuff":
            if self._skill_build_manual_effect_kind(name, skill.get("id")) == "debuff":
                attrs = TEAM_MANUAL_DEBUFFS.get(name, ({}, []))[0]
            label = "减益"
            if not attrs and not self._skill_build_is_ex_skill(skill):
                all_skills = self.skill_build_data.get("skills", {}) if isinstance(getattr(self, "skill_build_data", {}).get("skills", {}), dict) else {}
                for ex_id, ex_skill in all_skills.items():
                    if int(as_number(levels.get(norm(ex_id)))) <= 0:
                        continue
                    if self._skill_build_base_name_from_ex(ex_skill.get("name")) == name:
                        ex_name = norm(ex_skill.get("name"))
                        attrs = TEAM_MANUAL_DEBUFFS.get(ex_name, ({}, []))[0]
                        if attrs:
                            label = f"EX减益"
                            break
        elif kind == "ex":
            manual_kind = self._skill_build_manual_effect_kind(name, skill.get("id"))
            if manual_kind == "debuff":
                attrs = TEAM_MANUAL_DEBUFFS.get(name, ({}, []))[0]
                label = "EX减益"
            elif manual_kind in {"team_buff", "personal_buff"}:
                attrs = TEAM_MANUAL_DEBUFFS.get(name, ({}, []))[0]
                label = "EX加成"
        desc_attrs = self._skill_build_desc_effect_attrs(skill, level)
        if desc_attrs:
            attrs = {**attrs, **desc_attrs}
        text = self._skill_build_format_attrs(attrs)
        note = self._skill_build_desc_effect_note(skill, level)
        details = "；".join(part for part in (text, note) if part)
        return f"{label}：{details}" if details else ""

    @staticmethod
    def _skill_build_kind_label(kind: str) -> str:
        return {
            "active": "主动",
            "passive": "被动",
            "team_buff": "团队buff",
            "personal_buff": "个人buff",
            "debuff": "debuff",
            "ex": "EX强化",
        }.get(kind, "主动")

    @staticmethod
    def _skill_build_kind_palette(kind: str) -> dict[str, str]:
        return {
            "active": {"bg": "#D8ECFF", "border": "#7DB7E8", "icon": "#4F8EC7"},
            "passive": {"bg": "#FFF2C2", "border": "#E4BE5A", "icon": "#C18A24"},
            "team_buff": {"bg": "#DDF6DF", "border": "#86C98E", "icon": "#51965D"},
            "personal_buff": {"bg": "#BFE8D2", "border": "#55A676", "icon": "#2F7D55"},
            "debuff": {"bg": "#FFE3C4", "border": "#E49A50", "icon": "#C56C28"},
            "ex": {"bg": "#E8DCFF", "border": "#A78BFA", "icon": "#7452D6"},
        }.get(kind, {"bg": "#D8ECFF", "border": "#7DB7E8", "icon": "#4F8EC7"})

    def _skill_build_node_style(self, skill: dict[str, Any], level: int, row_ok: bool, unavailable: bool, kind: str = "") -> str:
        palette = self._skill_build_kind_palette(kind or self._skill_build_kind(skill))
        bg = palette["bg"]
        border = palette["border"]
        border_width = 1
        border_style = "dashed"
        if level > 0 and not row_ok:
            bg = "#FFF1F2"
            border = "#F87171"
            border_width = 2
            border_style = "solid"
        elif level > 0:
            border = "#0EA5E9"
            border_width = 2
            border_style = "solid"
        elif unavailable:
            border = "#CBD5E1"
        return (
            f"QWidget#skillBuildNode {{ background:{bg}; border:{border_width}px {border_style} {border}; "
            "border-radius:8px; }"
        )

    @staticmethod
    def _skill_build_base_must_max_names() -> set[str]:
        return {
            "翻滚", "闪现", "闪避", "疾闪", "逃脱", "回旋脚步", "暗影消逝",
            "浮空调整", "力量掌握",
        }

    @staticmethod
    def _skill_build_allows_shared_learning(skill_name: str) -> bool:
        return norm(skill_name) in {"弗里兹科之舞", "幻影之舞"}

    @staticmethod
    def _skill_build_named_exclusive_key(skill_name: str) -> str:
        name = norm(skill_name)
        if SkillBuildMixin._skill_build_allows_shared_learning(name):
            return ""
        for group in BUFF_EXCLUSIVE_SKILL_GROUPS:
            if name in group:
                return "|".join(group)
        return ""

    @staticmethod
    def _skill_build_is_ultimate(skill: dict[str, Any]) -> bool:
        if int(as_number(skill.get("group_index"))) <= 0:
            return False
        max_level = int(as_number(skill.get("raw_max_level"), skill.get("max_level")))
        if max_level != 2:
            return False
        levels = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
        first_required = min((int(as_number(row.get("required_level"), 9999)) for row in levels), default=9999)
        return first_required == 40

    def _skill_build_ultimate_peer_ids(self, skill: dict[str, Any]) -> list[str]:
        if not self._skill_build_is_ultimate(skill):
            return []
        skill_id = norm(skill.get("id"))
        group_index = int(as_number(skill.get("group_index")))
        skill_lookup = self.skill_build_data.get("skills", {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        return [
            norm(peer_id)
            for peer_id, peer in skill_lookup.items()
            if norm(peer_id) != skill_id
            and int(as_number(peer.get("group_index"))) == group_index
            and self._skill_build_is_ultimate(peer)
        ]

    def _skill_build_has_learned_ultimate_peer(self, skill: dict[str, Any], levels: dict[str, int]) -> bool:
        return any(int(as_number(levels.get(peer_id))) > 0 for peer_id in self._skill_build_ultimate_peer_ids(skill))

    def _skill_build_exclusive_keys(self, skill: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        skill_name = norm(skill.get("name"))
        allows_shared_learning = self._skill_build_allows_shared_learning(skill_name)
        duplicate = int(as_number(skill.get("duplicated_type")))
        global_group = int(as_number(skill.get("global_group")))
        is_ultimate = self._skill_build_is_ultimate(skill)
        if duplicate:
            keys.append(f"duplicate:{duplicate}")
        if global_group and not is_ultimate and not allows_shared_learning:
            keys.append(f"global:{global_group}")
        named_group = self._skill_build_named_exclusive_key(skill_name)
        if named_group:
            keys.append(f"name:{named_group}")
        return keys

    def _skill_build_sanitize_levels(self, raw_levels: dict[str, Any]) -> dict[str, int]:
        skill_lookup = self.skill_build_data.get("skills", {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        levels: dict[str, int] = {}
        raw_by_id = {norm(skill_id): value for skill_id, value in raw_levels.items()}
        ordered_skill_ids = sorted(
            (skill_id for skill_id in raw_by_id if skill_id in skill_lookup),
            key=lambda skill_id: (
                int(as_number(skill_lookup[skill_id].get("group_index"))),
                min(
                    (int(as_number(row.get("required_level"), 9999)) for row in skill_lookup[skill_id].get("levels", []) if isinstance(row, dict)),
                    default=9999,
                ),
                int(as_number(skill_lookup[skill_id].get("sort"))),
                int(as_number(skill_lookup[skill_id].get("slot"))),
            ),
        )
        remaining = list(ordered_skill_ids)
        while remaining:
            deferred: list[str] = []
            changed = False
            for skill_key in remaining:
                value = raw_by_id.get(skill_key)
                skill = skill_lookup.get(skill_key)
                if not skill:
                    continue
                target = max(0, min(int(as_number(skill.get("max_level"))), int(as_number(value))))
                if target <= 0:
                    continue
                if self._skill_build_can_set_level(skill, target, levels):
                    levels[skill_key] = target
                    changed = True
                else:
                    deferred.append(skill_key)
            if not changed:
                break
            remaining = deferred
        return levels

    def _skill_build_preferred_exclusive_names(self) -> set[str]:
        second = norm(self.second_job.currentText())
        preferred: set[str] = set()
        if second == "灵魂舞者":
            preferred.add("幻影之舞")
        elif second == "刀锋舞者":
            preferred.add("弗里兹科之舞")
        if second == "十字军":
            preferred.add("雷神之锤")
        elif second == "圣骑士":
            preferred.add("神圣化身")
        if second in {"狙翎", "魔羽"}:
            preferred.add("长弓掌握")
        return preferred

    def _skill_build_is_deprioritized_exclusive(self, skill_name: str) -> bool:
        name = norm(skill_name)
        if self._skill_build_allows_shared_learning(name):
            return False
        preferred = self._skill_build_preferred_exclusive_names()
        if not preferred:
            return False
        for group in BUFF_EXCLUSIVE_SKILL_GROUPS:
            if name in group and any(item in preferred for item in group):
                return name not in preferred
        if name in {"雷神之锤", "神圣化身"} and any(item in preferred for item in {"雷神之锤", "神圣化身"}):
            return name not in preferred
        return False

    def _skill_build_mandatory_level(self, skill: dict[str, Any], personal_buffs: set[str]) -> int:
        name = norm(skill.get("name"))
        max_level = int(as_number(skill.get("max_level")))
        if max_level <= 0:
            return 0
        if self._skill_build_is_deprioritized_exclusive(name):
            return 0
        if self._skill_build_free_default_level(skill) > 0:
            return max_level
        if self._skill_build_is_ex_skill(skill):
            return max_level
        if name in personal_buffs:
            return max_level
        if name in self._skill_build_preferred_exclusive_names():
            return max_level
        if int(as_number(skill.get("group_index"))) == 0 and name in self._skill_build_base_must_max_names():
            return max_level
        if skill.get("default"):
            return min(1, max_level)
        return 0

    def _skill_build_group_sp_gate(self, skill: dict[str, Any]) -> tuple[int, int] | None:
        gates = [
            (0, int(as_number(skill.get("need_basic_sp")))),
            (1, int(as_number(skill.get("need_first_sp")))),
            (2, int(as_number(skill.get("need_second_sp")))),
        ]
        return max((gate for gate in gates if gate[1] > 0), key=lambda item: item[1], default=None)

    @staticmethod
    def _skill_build_increment_sp(skill: dict[str, Any], current: int, target: int) -> int:
        return max(0, SkillBuildMixin._skill_build_sp_at_level(skill, target) - SkillBuildMixin._skill_build_sp_at_level(skill, current))

    def _skill_build_required_changes(
        self,
        skill: dict[str, Any],
        levels: dict[str, int],
        skill_by_id: dict[str, dict[str, Any]],
        visiting: set[str] | None = None,
    ) -> dict[str, int] | None:
        visiting = visiting or set()
        skill_id = norm(skill.get("id"))
        if skill_id in visiting:
            return None
        if self._skill_build_has_learned_ultimate_peer(skill, levels):
            return {}
        visiting.add(skill_id)
        changes: dict[str, int] = {}
        for parent in skill.get("parents", []) if isinstance(skill.get("parents", []), list) else []:
            parent_id = norm(parent.get("id"))
            parent_skill = skill_by_id.get(parent_id)
            need_level = int(as_number(parent.get("level")))
            if not parent_skill or need_level <= int(as_number(levels.get(parent_id))):
                continue
            nested = self._skill_build_required_changes(parent_skill, levels, skill_by_id, visiting)
            if nested is None:
                return None
            for nested_id, nested_level in nested.items():
                changes[nested_id] = max(changes.get(nested_id, 0), nested_level)
            changes[parent_id] = max(changes.get(parent_id, 0), min(need_level, int(as_number(parent_skill.get("max_level")))))
        visiting.discard(skill_id)
        return changes

    def _skill_build_auto_fill_normal(self, summary: dict[str, float]) -> None:
        groups = self.skill_build_data.get("groups", []) if isinstance(self.skill_build_data.get("groups", []), list) else []
        skill_by_id = self.skill_build_data.get("skills", {}) if isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        levels = {skill_id: 0 for skill_id in skill_by_id}
        group_sp = [0 for _group in groups]
        total_limit = int(as_number(self.skill_build_data.get("total_sp")))
        score_cache: dict[tuple[str, int], float] = {}
        personal_buffs = self._skill_build_personal_buff_names()

        def group_limit(index: int) -> int:
            if index < 0 or index >= len(groups):
                return 0
            job = groups[index].get("job", {}) if isinstance(groups[index].get("job", {}), dict) else {}
            return int(as_number(job.get("sp")))

        def score(skill_id: str, level: int) -> float:
            key = (skill_id, level)
            if key not in score_cache:
                score_cache[key] = self._skill_build_score(skill_by_id.get(skill_id, {}), level, summary)
            return score_cache[key]

        def can_apply(changes: dict[str, int]) -> tuple[bool, int, float]:
            delta_sp_by_group = [0 for _group in groups]
            gain = 0.0
            total_cost = 0
            projected_levels = dict(levels)
            for change_id, target_level in changes.items():
                skill = skill_by_id.get(change_id)
                if skill:
                    projected_levels[change_id] = min(int(as_number(target_level)), int(as_number(skill.get("max_level"))))
            selected_exclusive: dict[str, list[str]] = {}
            for projected_id, projected_level in projected_levels.items():
                if projected_level <= 0:
                    continue
                projected_skill = skill_by_id.get(projected_id)
                if not projected_skill:
                    continue
                for key in self._skill_build_exclusive_keys(projected_skill):
                    selected_exclusive.setdefault(key, []).append(projected_id)
            if any(len(items) > 1 for items in selected_exclusive.values()):
                return False, 0, 0.0
            for change_id, target_level in changes.items():
                skill = skill_by_id.get(change_id)
                if not skill:
                    return False, 0, 0.0
                current = int(as_number(levels.get(change_id)))
                target = min(int(as_number(target_level)), int(as_number(skill.get("max_level"))))
                if target <= current:
                    continue
                required_row = self._skill_build_level_row(skill, target)
                if as_number(required_row.get("required_level"), 9999) > SKILL_BUILD_LEVEL_CAP:
                    return False, 0, 0.0
                cost = self._skill_build_increment_sp(skill, current, target)
                group_index = int(as_number(skill.get("group_index")))
                if group_index >= len(delta_sp_by_group):
                    return False, 0, 0.0
                delta_sp_by_group[group_index] += cost
                total_cost += cost
                gain += score(change_id, target) - score(change_id, current)
            projected_group_sp = [
                (group_sp[index] if index < len(group_sp) else 0) + (delta_sp_by_group[index] if index < len(delta_sp_by_group) else 0)
                for index in range(len(groups))
            ]
            for change_id, target_level in changes.items():
                skill = skill_by_id.get(change_id)
                if not skill:
                    return False, 0, 0.0
                if self._skill_build_has_learned_ultimate_peer(skill, projected_levels):
                    continue
                current = int(as_number(levels.get(change_id)))
                target = min(int(as_number(target_level)), int(as_number(skill.get("max_level"))))
                if target <= current:
                    continue
                for gate_index, gate_value in (
                    (0, int(as_number(skill.get("need_basic_sp")))),
                    (1, int(as_number(skill.get("need_first_sp")))),
                    (2, int(as_number(skill.get("need_second_sp")))),
                ):
                    if gate_value > 0 and (gate_index >= len(projected_group_sp) or projected_group_sp[gate_index] < gate_value):
                        return False, 0, 0.0
            if total_cost <= 0:
                return True, 0, gain
            if total_limit and sum(group_sp) + total_cost > total_limit:
                return False, 0, 0.0
            for index, delta in enumerate(delta_sp_by_group):
                limit = group_limit(index)
                if limit and group_sp[index] + delta > limit:
                    return False, 0, 0.0
            return True, total_cost, gain

        def can_apply_any(changes: dict[str, int]) -> tuple[bool, int, float]:
            ok, cost, gain = can_apply(changes)
            return ok, cost, gain

        def apply_changes(changes: dict[str, int]) -> bool:
            ok, _cost, _gain = can_apply_any(changes)
            if not ok:
                return False
            for change_id, target_level in changes.items():
                skill = skill_by_id.get(change_id)
                if not skill:
                    continue
                current = int(as_number(levels.get(change_id)))
                target = min(int(as_number(target_level)), int(as_number(skill.get("max_level"))))
                if target <= current:
                    continue
                cost = self._skill_build_increment_sp(skill, current, target)
                group_index = int(as_number(skill.get("group_index")))
                levels[change_id] = target
                if 0 <= group_index < len(group_sp):
                    group_sp[group_index] += cost
            return True

        def candidate_key(skill_id: str, target: int, cost: int, gain: float, group_priority: float = 1.0) -> tuple[float, float, int]:
            group_index = int(as_number(skill_by_id.get(skill_id, {}).get("group_index")))
            group_weight = {0: 0.18, 1: 1.08, 2: 1.16}.get(group_index, 1.0) * group_priority
            return (gain / max(1, cost) * group_weight, gain * group_weight, group_index)

        def best_increment_for_group(group_index: int) -> dict[str, int] | None:
            best_changes: dict[str, int] | None = None
            best_key = (0.0, 0.0, -1)
            for skill_id, skill in skill_by_id.items():
                if int(as_number(skill.get("group_index"))) != group_index:
                    continue
                current = int(as_number(levels.get(skill_id)))
                max_level = int(as_number(skill.get("max_level")))
                if current >= max_level:
                    continue
                changes = self._skill_build_required_changes(skill, levels, skill_by_id) or {}
                changes[skill_id] = current + 1
                ok, cost, gain = can_apply(changes)
                if not ok:
                    continue
                mandatory = self._skill_build_mandatory_level(skill, personal_buffs) > current
                if not mandatory and gain <= 0:
                    continue
                key = candidate_key(skill_id, current + 1, cost, gain + (1000000.0 if mandatory else 0.0), 1.0)
                if key > best_key:
                    best_key = key
                    best_changes = changes
            return best_changes

        def ensure_group_sp(group_index: int, target_sp: int) -> bool:
            if group_index < 0 or group_index >= len(group_sp):
                return False
            limit = group_limit(group_index)
            target_sp = min(target_sp, limit) if limit else target_sp
            guard = 0
            while group_sp[group_index] < target_sp and guard < 400:
                guard += 1
                changes = best_increment_for_group(group_index)
                if not changes or not apply_changes(changes):
                    return False
            return group_sp[group_index] >= target_sp

        def apply_skill_to(skill_id: str, target_level: int) -> bool:
            skill = skill_by_id.get(skill_id)
            if not skill:
                return False
            gate = None if self._skill_build_has_learned_ultimate_peer(skill, levels) else self._skill_build_group_sp_gate(skill)
            if gate and len(group_sp) > gate[0] and group_sp[gate[0]] < gate[1]:
                if not ensure_group_sp(gate[0], gate[1]):
                    return False
            changes = self._skill_build_required_changes(skill, levels, skill_by_id) or {}
            changes[skill_id] = max(target_level, int(as_number(levels.get(skill_id))))
            for gate_index in range(3):
                gate_target = 0
                for change_id in changes:
                    change_skill = skill_by_id.get(change_id)
                    if change_skill:
                        gate_target = max(gate_target, required_gate_target(change_skill, gate_index))
                if gate_target and (gate_index >= len(group_sp) or group_sp[gate_index] < gate_target):
                    if not ensure_group_sp(gate_index, gate_target):
                        return False
            return apply_changes(changes)

        def required_gate_target(skill: dict[str, Any], gate_index: int, visiting: set[str] | None = None) -> int:
            visiting = visiting or set()
            skill_id = norm(skill.get("id"))
            if skill_id in visiting:
                return 0
            visiting.add(skill_id)
            fields = ("need_basic_sp", "need_first_sp", "need_second_sp")
            target = int(as_number(skill.get(fields[gate_index]))) if 0 <= gate_index < len(fields) else 0
            for parent in skill.get("parents", []) if isinstance(skill.get("parents", []), list) else []:
                parent_skill = skill_by_id.get(norm(parent.get("id")))
                if parent_skill:
                    target = max(target, required_gate_target(parent_skill, gate_index, visiting))
            visiting.discard(skill_id)
            return target

        mandatory_targets = {
            skill_id: target
            for skill_id, skill in skill_by_id.items()
            for target in [self._skill_build_mandatory_level(skill, personal_buffs)]
            if target > 0
        }
        expanded = True
        while expanded:
            expanded = False
            zero_levels = {skill_id: 0 for skill_id in skill_by_id}
            for skill_id, target in list(mandatory_targets.items()):
                skill = skill_by_id.get(skill_id)
                if not skill or target <= 0:
                    continue
                required = self._skill_build_required_changes(skill, zero_levels, skill_by_id) or {}
                for required_id, required_level in required.items():
                    required_skill = skill_by_id.get(required_id)
                    if not required_skill:
                        continue
                    required_level = min(int(as_number(required_level)), int(as_number(required_skill.get("max_level"))))
                    if required_level > int(as_number(mandatory_targets.get(required_id))):
                        mandatory_targets[required_id] = required_level
                        expanded = True
        for group_index in range(len(groups)):
            changed = True
            while changed:
                changed = False
                for skill_id, target in mandatory_targets.items():
                    skill = skill_by_id.get(skill_id, {})
                    if int(as_number(skill.get("group_index"))) != group_index:
                        continue
                    if int(as_number(levels.get(skill_id))) >= target:
                        continue
                    changed = apply_skill_to(skill_id, target) or changed

        later_skills = [
            skill for skill in skill_by_id.values()
            if int(as_number(skill.get("group_index"))) > 0
            and (
                self._skill_build_mandatory_level(skill, personal_buffs) > 0
                or self._skill_build_score(skill, 1, summary) > 0
            )
        ]
        if later_skills:
            ensure_group_sp(0, max(required_gate_target(skill, 0) for skill in later_skills))
        second_skills = [
            skill for skill in skill_by_id.values()
            if int(as_number(skill.get("group_index"))) == 2
            and (
                self._skill_build_mandatory_level(skill, personal_buffs) > 0
                or self._skill_build_score(skill, 1, summary) > 0
            )
        ]
        if second_skills:
            ensure_group_sp(1, max(required_gate_target(skill, 1) for skill in second_skills))

        for preferred_group in (2, 1):
            guard = 0
            while guard < 400:
                guard += 1
                changes = best_increment_for_group(preferred_group)
                if not changes or not apply_changes(changes):
                    break

        while True:
            best_changes: dict[str, int] | None = None
            best_key = (0.0, 0.0, -1)
            for skill_id, skill in skill_by_id.items():
                current = int(as_number(levels.get(skill_id)))
                max_level = int(as_number(skill.get("max_level")))
                if current >= max_level:
                    continue
                need_basic = int(as_number(skill.get("need_basic_sp")))
                need_first = int(as_number(skill.get("need_first_sp")))
                need_second = int(as_number(skill.get("need_second_sp")))
                if need_basic and (len(group_sp) < 1 or group_sp[0] < need_basic):
                    continue
                if need_first and (len(group_sp) < 2 or group_sp[1] < need_first):
                    continue
                if need_second and (len(group_sp) < 3 or group_sp[2] < need_second):
                    continue
                changes = self._skill_build_required_changes(skill, levels, skill_by_id) or {}
                changes[skill_id] = current + 1
                ok, cost, gain = can_apply(changes)
                if not ok:
                    continue
                if gain <= 0:
                    continue
                key = candidate_key(skill_id, current + 1, cost, gain)
                if key > best_key:
                    best_key = key
                    best_changes = changes
            if not best_changes:
                break
            for change_id, target_level in best_changes.items():
                skill = skill_by_id.get(change_id)
                if not skill:
                    continue
                current = int(as_number(levels.get(change_id)))
                target = min(int(as_number(target_level)), int(as_number(skill.get("max_level"))))
                if target <= current:
                    continue
                cost = self._skill_build_increment_sp(skill, current, target)
                group_index = int(as_number(skill.get("group_index")))
                levels[change_id] = target
                if 0 <= group_index < len(group_sp):
                    group_sp[group_index] += cost

        for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items():
            if isinstance(spin, QSpinBox):
                spin.blockSignals(True)
                spin.setValue(max(0, min(spin.maximum(), int(as_number(levels.get(skill_id))))))
                spin.blockSignals(False)

    def _skill_build_auto_fill_mastery(self, summary: dict[str, float]) -> None:
        groups = self.skill_mastery_data.get("groups", []) if isinstance(self.skill_mastery_data.get("groups", []), list) else []
        skills: list[dict[str, Any]] = []
        for group in groups:
            skills.extend(group.get("skills", []) if isinstance(group.get("skills", []), list) else [])
        total_limit = int(as_number(self.skill_mastery_data.get("total_sp")))
        selected: set[str] = set()
        spent = 0
        group_sp = [0 for _group in groups]
        build_levels = {
            skill_id: int(spin.value())
            for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items()
            if isinstance(spin, QSpinBox)
        }
        candidates: list[tuple[float, float, int, str]] = []
        for skill in skills:
            skill_id = norm(skill.get("id"))
            base_level = int(as_number(build_levels.get(skill_id)))
            if base_level <= 0:
                continue
            cost = self._skill_build_sp_at_level(skill, 1)
            if cost <= 0:
                continue
            gain = self._skill_build_score(skill, base_level, summary, mastery_bonus=1) - self._skill_build_score(skill, base_level, summary, mastery_bonus=0)
            if gain <= 0:
                continue
            candidates.append((gain / cost, gain, -cost, skill_id))
        candidates.sort(reverse=True)
        for _ratio, _gain, neg_cost, skill_id in candidates:
            cost = -neg_cost
            if total_limit and spent + cost > total_limit:
                continue
            skill = self.skill_mastery_data.get("skills", {}).get(skill_id, {}) if isinstance(self.skill_mastery_data.get("skills", {}), dict) else {}
            group_index = int(as_number(skill.get("group_index")))
            selected.add(skill_id)
            spent += cost
            if 0 <= group_index < len(group_sp):
                group_sp[group_index] += cost
        for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items():
            if isinstance(spin, QSpinBox):
                spin.blockSignals(True)
                spin.setValue(1 if skill_id in selected else 0)
                spin.setProperty("previous_value", spin.value())
                spin.blockSignals(False)

    def auto_fill_skill_build(self) -> None:
        if not hasattr(self, "skill_build_level_spins"):
            return
        _rows, summary = self.calculator.calculate(self.collect_build())
        for spin in getattr(self, "skill_build_level_spins", {}).values():
            if isinstance(spin, QSpinBox):
                spin.blockSignals(True)
                spin.setValue(0)
                spin.blockSignals(False)
        for spin in getattr(self, "skill_mastery_spins", {}).values():
            if isinstance(spin, QSpinBox):
                spin.blockSignals(True)
                spin.setValue(0)
                spin.blockSignals(False)
        self._skill_build_auto_fill_normal(summary)
        self._skill_build_auto_fill_mastery(summary)
        self.update_skill_mastery_preview()
        self.update_skill_build_preview(summary)
        self.schedule_refresh()

    def _skill_build_level(self, skill_id: Any) -> int:
        widget = getattr(self, "skill_build_level_spins", {}).get(norm(skill_id))
        return int(widget.value()) if isinstance(widget, QSpinBox) else 0

    @staticmethod
    def _skill_build_level_row(skill: dict[str, Any], level: int) -> dict[str, Any]:
        rows = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
        for row in rows:
            if int(as_number(row.get("level"))) == int(level):
                return row
        return {}

    @staticmethod
    def _skill_build_sp_at_level(skill: dict[str, Any], level: int) -> int:
        rows = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
        return sum(int(as_number(row.get("sp"))) for row in rows if int(as_number(row.get("level"))) <= level)

    @staticmethod
    def _skill_build_sp_breakdown(skill: dict[str, Any]) -> str:
        rows = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
        parts = [
            f"Lv{int(as_number(row.get('level')))}:+{int(as_number(row.get('sp')))}"
            for row in rows
            if int(as_number(row.get("level"))) > 0
        ]
        if len(parts) > 12:
            return "，".join(parts[:12]) + " ..."
        return "，".join(parts) if parts else "-"

    def _skill_build_requirement_text(
        self,
        skill: dict[str, Any],
        group_sp: list[int],
        levels: dict[str, int],
    ) -> tuple[str, bool]:
        parts: list[str] = []
        ok = True
        for parent in skill.get("parents", []) if isinstance(skill.get("parents", []), list) else []:
            need = int(as_number(parent.get("level")))
            current = int(as_number(levels.get(norm(parent.get("id")))))
            text = f"{parent.get('name')}Lv{need}"
            if current < need:
                ok = False
                text += f"（当前{current}）"
            parts.append(text)
        need_basic = int(as_number(skill.get("need_basic_sp")))
        need_first = int(as_number(skill.get("need_first_sp")))
        need_second = int(as_number(skill.get("need_second_sp")))
        if need_basic > 0:
            text = f"0转SP≥{need_basic}"
            if len(group_sp) > 0 and group_sp[0] < need_basic:
                ok = False
                text += f"（当前{group_sp[0]}）"
            parts.append(text)
        if need_first > 0:
            text = f"1转SP≥{need_first}"
            if len(group_sp) > 1 and group_sp[1] < need_first:
                ok = False
                text += f"（当前{group_sp[1]}）"
            parts.append(text)
        if need_second > 0:
            text = f"2转SP≥{need_second}"
            if len(group_sp) > 2 and group_sp[2] < need_second:
                ok = False
                text += f"（当前{group_sp[2]}）"
            parts.append(text)
        return "；".join(parts) if parts else "无", ok

    def _skill_build_level_data_for_display(self, skill: dict[str, Any], level: int, mastery_bonus: int = 0) -> list[Any] | None:
        if level <= 0:
            return None
        skill_name = norm(skill.get("name"))
        effective_level = max(1, level + max(0, mastery_bonus))
        mode = "master" if mastery_bonus > 0 else "normal"
        data = self.store.skill_damage_level_data(self.second_job.currentText(), skill_name, str(effective_level), mode=mode)
        if data is None and mastery_bonus > 0:
            data = self.store.skill_damage_level_data(self.second_job.currentText(), skill_name, str(effective_level), mode="normal")
        if data is None:
            data = self.store.skill_damage_level_data(self.second_job.currentText(), skill_name, str(level), mode="normal")
        return data

    def _skill_build_benefit_text(self, skill: dict[str, Any], level: int, mastery_bonus: int = 0) -> str:
        shown_level = max(1, int(as_number(level)) + max(0, mastery_bonus))
        row = self._skill_build_level_row(skill, shown_level) or self._skill_build_level_row(skill, level)
        desc = norm(row.get("desc"))
        if not desc:
            desc = self.store.skill_damage_description(self.second_job.currentText(), norm(skill.get("name")), str(shown_level))
        lines = [part.strip() for part in re.split(r"[\r\n。；;]+", norm(desc)) if part.strip()]
        if not lines:
            return "收益：暂无说明数据"
        important_pattern = re.compile(
            r"(增加|提升|提高|减少|降低|获得|恢复|冷却|持续|伤害|攻击|防御|致命|最终|属性|速度|抗|HP|MP|CD|%)",
            re.IGNORECASE,
        )
        important = [line for line in lines if important_pattern.search(line)]
        numeric_effects = [
            line for line in important
            if re.search(r"\d", line) and not re.search(r"(?:持续)?消耗", line)
        ]
        selected = (numeric_effects or important or lines)[:3]
        desc_text = "；".join(selected)
        desc_text = re.sub(r"\s+", " ", desc_text)
        if self._skill_build_is_ex_skill(skill):
            desc_text = f"EX：{desc_text}"
        if len(desc_text) > 76:
            desc_text = desc_text[:76].rstrip("；,，。 ") + "..."
        return f"收益：{desc_text}"

    def _skill_build_raw_cooldown(self, skill: dict[str, Any], level: int, mastery_bonus: int = 0) -> float:
        base_level = max(1, int(as_number(level)))
        shown_level = max(1, base_level + max(0, mastery_bonus))
        row = self._skill_build_level_row(skill, shown_level) or self._skill_build_level_row(skill, base_level)
        return max(0.0, as_number(row.get("cooldown")))

    def _skill_build_has_direct_damage(self, skill: dict[str, Any], level: int, mastery_bonus: int = 0) -> bool:
        shown_level = max(1, int(as_number(level)) + max(0, mastery_bonus))
        row = self._skill_build_level_row(skill, shown_level) or self._skill_build_level_row(skill, level)
        desc = norm(row.get("desc"))
        for line in re.split(r"[\r\n。；;]+", desc):
            text = line.strip()
            if not re.search(r"(?:物理|魔法)?攻击力\s*\d+(?:\.\d+)?%", text):
                continue
            if re.search(r"(?:提高|提升|增加|减少|降低|转换为?)\s*(?:物理|魔法)?攻击力", text):
                continue
            return True
        return False

    def _skill_build_damage_line(
        self,
        skill: dict[str, Any],
        level: int,
        summary: dict[str, float],
        mastery_bonus: int = 0,
    ) -> str:
        if level <= 0:
            return "未学习"
        kind = self._skill_build_kind(skill)
        if kind in {"passive", "personal_buff", "team_buff"} and not self._skill_build_has_direct_damage(skill, level, mastery_bonus):
            cooldown = self._skill_build_raw_cooldown(skill, level, mastery_bonus)
            cooldown_text = f" | CD{self._cooldown_text(cooldown)}" if cooldown > 0 else ""
            return f"Lv{level}: {self._skill_build_benefit_text(skill, level, mastery_bonus)}{cooldown_text}"
        level_data = self._skill_build_level_data_for_display(skill, level, mastery_bonus)
        level_text = f"Lv{level}"
        if mastery_bonus:
            level_text += f"+{mastery_bonus}"
        if not level_data:
            cooldown = self._skill_build_raw_cooldown(skill, level, mastery_bonus)
            cooldown_text = f" | CD{self._cooldown_text(cooldown)}" if cooldown > 0 else ""
            return f"{level_text}: {self._skill_build_benefit_text(skill, level, mastery_bonus)}{cooldown_text}"
        skill_name = norm(skill.get("name"))
        shown_level = level + max(0, mastery_bonus)
        multiplier = self._skill_adjusted_multiplier(skill_name, shown_level)
        burning_bonus = self._skill_burning_hand_bonus(skill_name)
        display_multiplier = multiplier + burning_bonus
        cooldown = self._skill_build_raw_cooldown(skill, level, mastery_bonus)
        parts: list[str] = []
        for damage_type, percent, fixed in self._skill_level_damages(level_data)[:2]:
            if abs(percent) <= 1e-9 and abs(fixed) <= 1e-9:
                continue
            parts.append(f"{self._damage_type_label(damage_type)} {self._raw_percent_text(percent * display_multiplier)}+{int(fixed * display_multiplier):,}")
        if not parts:
            cooldown_text = f" | CD{self._cooldown_text(cooldown)}" if cooldown > 0 else ""
            return f"{level_text}: {self._skill_build_benefit_text(skill, level, mastery_bonus)}{cooldown_text}"
        effective = 0.0
        if summary:
            weighted = self._skill_weighted_panel_profile()
            effective = self._skill_level_effective(level_data, summary, multiplier=display_multiplier, weighted_panel=weighted)
        dps = effective / cooldown if cooldown > 0 and effective > 0 else 0.0
        damage_text = " / ".join(parts)
        return f"{level_text}: {damage_text} | 倍率{multiplier:g} | CD{self._cooldown_text(cooldown)} | DPS{self._percent_text(dps)}/s"

    def _skill_build_cooldown_text(self, skill: dict[str, Any], level: int, mastery_bonus: int = 0) -> str:
        cooldown = self._skill_build_raw_cooldown(skill, level, mastery_bonus)
        return f"CD {self._cooldown_text(cooldown)}s" if cooldown > 0 else "CD -"

    def update_skill_build_preview(self, summary: dict[str, float] | None = None) -> None:
        summary_widget = getattr(self, "skill_build_summary", None)
        if (
            not hasattr(self, "skill_build_data")
            or not hasattr(self, "skill_build_nodes")
            or not qt_object_is_valid(summary_widget)
        ):
            return
        valid_spins = {
            skill_id: spin
            for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items()
            if isinstance(spin, QSpinBox) and qt_object_is_valid(spin)
        }
        self.skill_build_level_spins = valid_spins
        if summary is not None:
            self._last_summary = summary
        display_summary = summary or getattr(self, "_last_summary", {})
        groups = self.skill_build_data.get("groups", [])
        levels = {
            skill_id: self._skill_build_level(skill_id)
            for skill_id in valid_spins
        }
        group_sp: list[int] = []
        for group in groups:
            spent = 0
            for skill in group.get("skills", []) if isinstance(group.get("skills", []), list) else []:
                level = int(as_number(levels.get(norm(skill.get("id")))))
                spent += self._skill_build_sp_at_level(skill, level)
            group_sp.append(spent)
        total_sp = sum(group_sp)
        total_limit = int(as_number(self.skill_build_data.get("total_sp")))
        invalid_count = 0
        selected_exclusive: dict[str, list[str]] = {}
        for skill_id, level in levels.items():
            if level <= 0:
                continue
            skill = self.skill_build_data.get("skills", {}).get(skill_id, {}) if isinstance(self.skill_build_data.get("skills", {}), dict) else {}
            for key in self._skill_build_exclusive_keys(skill):
                selected_exclusive.setdefault(key, []).append(skill_id)
        for group in groups:
            skills = group.get("skills", []) if isinstance(group.get("skills", []), list) else []
            for skill in skills:
                skill_id = norm(skill.get("id"))
                node = self.skill_build_nodes.get(skill_id)
                if not node:
                    continue
                level = int(as_number(levels.get(norm(skill.get("id")))))
                preview_level = max(1, level)
                level_row = self._skill_build_level_row(skill, preview_level)
                current_row = self._skill_build_level_row(skill, level) if level > 0 else {}
                required_level = int(as_number(current_row.get("required_level"))) if level > 0 else int(as_number(level_row.get("required_level")))
                spent = self._skill_build_sp_at_level(skill, level)
                req_text, req_ok = self._skill_build_requirement_text(skill, group_sp, levels)
                if not req_ok and self._skill_build_has_learned_ultimate_peer(skill, levels):
                    req_ok = True
                    req_text = "已学一个大招，可无视前置"
                conflicting_keys = [key for key in self._skill_build_exclusive_keys(skill) if len(selected_exclusive.get(key, [])) > 1]
                if conflicting_keys:
                    req_ok = False
                    conflict_text = "同组技能互斥"
                    req_text = f"{req_text}；{conflict_text}" if req_text != "无" else conflict_text
                level_ok = required_level <= SKILL_BUILD_LEVEL_CAP
                row_ok = req_ok and level_ok
                if level > 0 and not row_ok:
                    invalid_count += 1
                max_level = int(as_number(skill.get("max_level")))
                level_rows = skill.get("levels", []) if isinstance(skill.get("levels", []), list) else []
                desc = norm(current_row.get("desc")) or norm(level_row.get("desc")) or norm(level_rows[0].get("desc") if level_rows else "")
                mastery_bonus = self._skill_mastery_bonus_for_skill(skill)
                next_level = min(max_level, level + 1) if level > 0 else 1
                node["spin"].setProperty("previous_value", level)
                node["mastery"].setText("专精+1" if mastery_bonus else "")
                node["mastery"].setVisible(bool(mastery_bonus))
                current_line = self._skill_build_damage_line(skill, level, display_summary, mastery_bonus)
                next_line = self._skill_build_damage_line(skill, next_level, display_summary, mastery_bonus) if next_level != level else "已满级"
                display_kind = self._skill_build_display_kind(skill, levels)
                effect_text = self._skill_build_effect_badge_text(skill, preview_level, display_kind, levels)
                node["effect"].setText(effect_text)
                node["effect"].setVisible(bool(effect_text))
                node["detail"].setText(f"当前 {current_line}\n下级 {next_line}")
                node["level"].setText(f"{'已学' if level > 0 else '未学'} Lv {level}/{max_level}")
                node["sp"].setText(f"SP {spent}")
                cd_text = self._skill_build_cooldown_text(skill, level, mastery_bonus)
                node["cd"].setText(cd_text)
                req_display = f"Lv{required_level}" if required_level else "Lv-"
                if not req_ok and req_text != "无":
                    req_display += f" | 前置 {req_text}"
                node["req"].setText(req_display)
                tooltip = "\n".join([
                    norm(skill.get("name")) or skill_id,
                    f"技能类型：{self._skill_build_kind_label(display_kind)}",
                    f"加成/减益：{effect_text or '-'}",
                    f"当前等级：{level}/{max_level}",
                    f"专精加成：+{mastery_bonus}" if mastery_bonus else "专精加成：无",
                    f"累计消耗 SP：{spent}",
                    cd_text,
                    f"每级 SP：{self._skill_build_sp_breakdown(skill)}",
                    f"所需等级：{required_level if required_level else '-'}",
                    f"前置/需求：{req_text}",
                    f"当前数值：{current_line}",
                    f"下级数值：{next_line}",
                    f"说明：{desc or '-'}",
                ])
                for widget in (node["widget"], node["name"], node["level"], node["sp"], node["cd"], node["req"], node["spin"]):
                    widget.setToolTip(tooltip)
                unavailable = not row_ok or (required_level > SKILL_BUILD_LEVEL_CAP)
                node["widget"].setStyleSheet(self._skill_build_node_style(skill, level, row_ok, unavailable, display_kind))
                node["minus"].setEnabled(level > 0)
                node["plus"].setEnabled(level < max_level and self._skill_build_can_set_level(skill, level + 1, levels))
        group_parts = []
        for index, group in enumerate(groups):
            job = group.get("job", {})
            limit = int(as_number(job.get("sp")))
            spent = group_sp[index] if index < len(group_sp) else 0
            text = f"{job.get('name')} {spent}/{limit or '-'}"
            if limit and spent > limit:
                invalid_count += 1
                text += " 超出"
            group_parts.append(text)
            label = getattr(self, "skill_build_group_labels", {}).get(index)
            if isinstance(label, QLabel):
                label.setText(text)
        total_text = f"总SP {total_sp}/{total_limit or '-'}"
        if total_limit and total_sp > total_limit:
            invalid_count += 1
            total_text += " 超出"
        if invalid_count:
            total_text += f"；{invalid_count}项需要调整"
        self.skill_build_summary.setText(f"{total_text}    " + "    ".join(group_parts))

    def reset_skill_build(self) -> None:
        skill_lookup = self.skill_build_data.get("skills", {}) if hasattr(self, "skill_build_data") and isinstance(self.skill_build_data.get("skills", {}), dict) else {}
        for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items():
            if isinstance(spin, QSpinBox):
                skill = skill_lookup.get(skill_id, {})
                spin.blockSignals(True)
                spin.setValue(max(spin.minimum(), min(spin.maximum(), self._skill_build_free_default_level(skill))))
                spin.setProperty("previous_value", spin.value())
                spin.blockSignals(False)
        self.update_skill_build_preview()
        self.schedule_refresh()

    def _refresh_skill_build_options(self, use_default: bool = False) -> None:
        if not hasattr(self, "skill_build_tabs"):
            return
        self.skill_build_data = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        if use_default:
            saved_levels = self._skill_build_payload_levels(self._skill_build_default_payload_for_current(), "skill_build")
        else:
            saved_levels = {
                skill_id: spin.value()
                for skill_id, spin in getattr(self, "skill_build_level_spins", {}).items()
                if isinstance(spin, QSpinBox) and spin.value() > 0
            }
        self._populate_skill_build_tabs(saved_levels)
        self.update_skill_build_preview()

    def _refresh_skill_mastery_options(self, use_default: bool = False) -> None:
        if not hasattr(self, "skill_mastery_tabs"):
            return
        self.skill_mastery_data = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP, mode="master")
        if use_default:
            saved_levels = self._skill_build_payload_levels(self._skill_build_default_payload_for_current(), "skill_mastery_build")
        else:
            saved_levels = {
                skill_id: spin.value()
                for skill_id, spin in getattr(self, "skill_mastery_spins", {}).items()
                if isinstance(spin, QSpinBox) and spin.value() > 0
            }
        self._populate_skill_mastery_tabs(saved_levels)
        self._populate_skill_glyph_tab()
        self.update_skill_mastery_preview()
        self.update_skill_build_preview()
