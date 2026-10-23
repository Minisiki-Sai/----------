from __future__ import annotations

from bisect import bisect_left, bisect_right

from ..shared import *
from ..hp_conditional import (
    PUNISHMENT_SKILL,
    boss_hp_percent_at,
    conditional_panel_correction,
    punishment_correction,
)

BuildEditor = None


from .skill_timeline_schedule import SkillTimelineScheduleMixin, bind_host_globals as _bind_SkillTimelineScheduleMixin
from .skill_timeline_state import SkillTimelineStateMixin, bind_host_globals as _bind_SkillTimelineStateMixin
from .skill_timeline_effects import SkillTimelineEffectsMixin, bind_host_globals as _bind_SkillTimelineEffectsMixin
from .skill_timeline_output import SkillTimelineOutputMixin, bind_host_globals as _bind_SkillTimelineOutputMixin

class SkillTimelineEngineMixin(SkillTimelineScheduleMixin, SkillTimelineStateMixin, SkillTimelineEffectsMixin, SkillTimelineOutputMixin):
    pass


def bind_runtime_editor(editor_type) -> None:
    """Publish the late-bound BuildEditor to every extracted engine module."""

    global BuildEditor
    BuildEditor = editor_type
    _bind_SkillTimelineScheduleMixin(globals())
    _bind_SkillTimelineStateMixin(globals())
    _bind_SkillTimelineEffectsMixin(globals())
    _bind_SkillTimelineOutputMixin(globals())

# V430_SPLIT_MODULES_APPLIED
_bind_SkillTimelineScheduleMixin(globals())
_bind_SkillTimelineStateMixin(globals())
_bind_SkillTimelineEffectsMixin(globals())
_bind_SkillTimelineOutputMixin(globals())
