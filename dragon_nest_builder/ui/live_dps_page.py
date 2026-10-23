from __future__ import annotations

# Compatibility facade: external callers continue importing every public and
# historical helper from dragon_nest_builder.ui.live_dps_page.
from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *
from .live_dps_page_layout import LiveDpsLayoutMixin
from .live_dps_page_boss_statistics import LiveDpsBossStatisticsMixin
from .live_dps_page_capture import LiveDpsCaptureMixin
from .live_dps_page_party import LiveDpsPartyMixin
from .live_dps_page_views import LiveDpsViewsMixin
from .live_dps_page_scoring import LiveDpsScoringMixin


class LiveDpsPage(
    LiveDpsLayoutMixin,
    LiveDpsBossStatisticsMixin,
    LiveDpsCaptureMixin,
    LiveDpsPartyMixin,
    LiveDpsViewsMixin,
    LiveDpsScoringMixin,
    TheoryPlanBuilderMixin,
    QWidget,
):
    UI_REFRESH_INTERVAL_SECONDS = 2.0
    CAPTURE_COMPLEX_REFRESH_INTERVAL_SECONDS = 3.0


# Preserve the old module-level patching contract.  Code moved into mixins
# keeps its own module globals, therefore assignments made through the legacy
# ``ui.live_dps_page`` import must be mirrored to those implementation modules.
import sys as _compat_sys
import types as _compat_types
from . import live_dps_page_boss_statistics as _split_boss_statistics
from . import live_dps_page_capture as _split_capture
from . import live_dps_page_layout as _split_layout
from . import live_dps_page_party as _split_party
from . import live_dps_page_scoring as _split_scoring
from . import live_dps_page_views as _split_views


class _LiveDpsPageCompatibilityModule(_compat_types.ModuleType):
    _split_targets = (
        _split_boss_statistics,
        _split_capture,
        _split_layout,
        _split_party,
        _split_scoring,
        _split_views,
    )

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for target in self._split_targets:
            if hasattr(target, name):
                setattr(target, name, value)


_compat_sys.modules[__name__].__class__ = _LiveDpsPageCompatibilityModule
