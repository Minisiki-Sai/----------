from __future__ import annotations

from ..shared import *
from .extreme_engine import ExtremeEngineMixin
from .extreme_ui import ExtremeUiMixin
from . import extreme_engine as _engine_module
from . import extreme_ui as _ui_module

BuildEditor = None


class ExtremeTeamMixin(ExtremeUiMixin, ExtremeEngineMixin):
    pass




























































def bind_build_editor(editor_type) -> None:
    global BuildEditor
    BuildEditor = editor_type
    _engine_module.BuildEditor = editor_type
    _ui_module.BuildEditor = editor_type
