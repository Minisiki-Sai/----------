"""Per-category capture switches for the live DPS overlay (lightweight build).

This standalone tool intentionally keeps only the two countdown features the
user needs:

    buffs          增益 / 减益倒计时（个人/团队 buff、Boss 减抗增伤）
    boss_actions   Boss 技能 / 动作（技能倒计时预报的数据源）

Every other decoded event family (damage/DPS, healing, Boss HP, player status,
summons and profile inspection) is dropped at the earliest publish point, so its
downstream accounting, scoring and curves never run.  The two remaining
categories are each controlled by one independent on/off switch.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .models import BossActionEvent, BossStatusEvent


CAPTURE_TOGGLE_LABELS: dict[str, str] = {
    "buffs": "增益 / 减益倒计时",
    "boss_actions": "Boss 技能 / 动作倒计时",
}

# Event class -> category.  Only the two countdown features are captured; any
# event class absent from this map is dropped.
_EVENT_CATEGORY: dict[type, str] = {
    BossStatusEvent: "buffs",
    BossActionEvent: "boss_actions",
}


@dataclass(frozen=True, slots=True)
class CaptureToggles:
    """Feature-category switches.  Every category defaults to enabled."""

    buffs: bool = True
    boss_actions: bool = True

    @classmethod
    def category_names(cls) -> tuple[str, ...]:
        return tuple(field.name for field in fields(cls))

    @classmethod
    def from_mapping(cls, value: Any) -> "CaptureToggles":
        if not isinstance(value, dict):
            return cls()
        kwargs: dict[str, bool] = {}
        for name in cls.category_names():
            kwargs[name] = bool(value.get(name, True))
        return cls(**kwargs)

    def to_mapping(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in self.category_names()}


def classify_event(event: Any) -> str | None:
    """Return the capture-toggle category key for one decoded event.

    Returns ``None`` for any event family this lightweight build does not
    capture (damage/healing/Boss HP/player status/summons/profiles).
    """
    return _EVENT_CATEGORY.get(type(event))


def should_capture(toggles: CaptureToggles | None, event: Any) -> bool:
    """True when the event's category is enabled.

    Events outside the two retained categories are always dropped.  A ``None``
    toggles value means no filtering is configured, so the caller publishes
    everything unchanged (this path is not exercised by the live capture
    service, which always loads a ``CaptureToggles`` instance).
    """
    if toggles is None:
        return True
    category = classify_event(event)
    if category is None:
        return False
    return bool(getattr(toggles, category, True))
