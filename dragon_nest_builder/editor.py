from __future__ import annotations

from .shared import *
from .shared import _ExtremeTimelineProxy
from .mixins.skill_build import SkillBuildMixin
from .mixins.build_pages import BuildPagesMixin
from .mixins.skill_simulation import SkillSimulationMixin
from .mixins import skill_simulation as _skill_simulation_module
from .mixins.team_simulation import TeamSimulationMixin
from .mixins import team_simulation as _team_simulation_module
from .mixins.editor_state import EditorStateMixin
from .mixins import editor_state as _editor_state_module


class BuildEditor(EditorStateMixin, TeamSimulationMixin, SkillSimulationMixin, BuildPagesMixin, SkillBuildMixin, QWidget):
    def __init__(
        self,
        name: str,
        store: DataStore,
        calculator: BuildCalculator,
        build: dict[str, Any],
        *,
        standardize_default: bool = False,
    ):
        super().__init__()
        self.name = name
        self.store = store
        self.calculator = calculator
        self.skill_cooldown_engine = SkillCooldownEngine(store.skill_cooldown_rules(), store.skill_reference_cooldown)
        self.build = copy.deepcopy(build)
        self.summary_labels: dict[str, QLabel] = {}
        self.pending = False
        self._defer_refresh = False
        self.skill_build_zoom = 1.0
        self.skill_build_canvas_zoom = True
        self.skill_build_zoom_sources: list[QObject] = []
        self.skill_build_canvas_views: list[SkillTreeZoomView] = []
        self._skill_sim_ui_active = True
        self._team_sim_ui_active = True
        self._build_ui()
        if standardize_default:
            self.apply_standard_opening_build()
        else:
            self.refresh()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Wheel and any(watched is source for source in getattr(self, "skill_build_zoom_sources", [])):
            if event.modifiers() & Qt.ControlModifier:
                delta = event.angleDelta().y()
                if delta:
                    self._adjust_skill_build_zoom(1 if delta > 0 else -1)
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.summary_labels = {
            "effective_panel": QLabel("0"),
            "final_percent": QLabel("0"),
            "crit_percent": QLabel("0"),
            "crit_damage_percent": QLabel("0"),
        }
        for title, key in [("等效面板", "effective_panel"), ("最终%", "final_percent"), ("致命%", "crit_percent"), ("爆伤%", "crit_damage_percent")]:
            box = QGroupBox(title)
            box.setObjectName("summaryCard")
            inner = QVBoxLayout(box)
            label = self.summary_labels[key]
            label.setObjectName("summaryValue")
            label.setAlignment(Qt.AlignCenter)
            inner.addWidget(label)
            top.addWidget(box)
        layout.addLayout(top)

        # The old permanent result panel consumed roughly one quarter of the
        # editor width.  Wide equipment, glyph and rune tables were therefore
        # forced behind horizontal scrollbars even on a normal-sized window.
        # Keep the exact same result model, but expose it as a first-class tab
        # so every editor page can use the full content width.
        layout.addWidget(self._left_editor(), 1)

    def _left_editor(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setObjectName("buildEditorTabs")
        tabs.addTab(self._equipment_page(), "装备 / 首饰")
        tabs.addTab(self._engraving_page(), "刻印")
        tabs.addTab(self._glyph_page(), "纹章")
        tabs.addTab(self._rune_page(), "石板")
        tabs.addTab(self._card_page(), "卡片")
        tabs.addTab(self._talisman_page(), "护符")
        tabs.addTab(self._misc_page(), "其他 / Buff")
        tabs.addTab(self._result_table(), "完整属性")
        tabs.addTab(self._free_input_page(), "自由输入")
        self.build_tabs = tabs
        return tabs

_skill_simulation_module.bind_build_editor(BuildEditor)
_team_simulation_module.bind_build_editor(BuildEditor)
_editor_state_module.BuildEditor = BuildEditor
