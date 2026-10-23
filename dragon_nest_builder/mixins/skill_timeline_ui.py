from __future__ import annotations

from ..shared import *
from .timeline_editor import TimelineEditorMixin
from .timeline_runtime import TimelineRuntimeMixin
from .timeline_presenter import TimelinePresenterMixin
from . import timeline_editor as _editor_module
from . import timeline_runtime as _runtime_module
from . import timeline_presenter as _presenter_module

BuildEditor = None


class SkillTimelineUiMixin(TimelinePresenterMixin, TimelineRuntimeMixin, TimelineEditorMixin):
    pass















































def bind_build_editor(editor_type) -> None:
    global BuildEditor
    BuildEditor = editor_type
    _editor_module.BuildEditor = editor_type
    _runtime_module.BuildEditor = editor_type
    _presenter_module.BuildEditor = editor_type
