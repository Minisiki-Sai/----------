"""Opt-in scoring experiments. No policy in this module is enabled by default.

These helpers deliberately separate missing references from genuine execution
better than a planner. They are not a replacement for re-planning a missing
support denominator, and never normalize professions to a sample mean.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


def role_decision_windows(team_windows, personal_windows, *, enabled=False):
    """Role selection must use the same scope as the already-built team plan.

    Only selects the role-evidence scope; personal damage windows are untouched.
    """
    return team_windows if enabled else personal_windows


@dataclass(frozen=True)
class SupportReferenceResult:
    score: float
    actual: float
    theory: float
    excluded: tuple[tuple[str, str, float], ...]
    optional_actual: float
    scored_rows: int
    required_score: float | None


def support_reference_result(
    rows: Iterable[Mapping], *, optional_skills=(), policy="published"
) -> SupportReferenceResult:
    """Revalue exported detail without changing casts, gains or role weights.

    required_guard: omit required numerator-only rows from a completion ratio.
    symmetric_optional: additionally show actual-only optional work as neutral
    100% work in a separate responsibility pool. Its actual gain is NOT called
    a legal optimum; this remains a sensitivity experiment, not a shipped rule.
    """
    if policy not in {"published", "required_guard", "symmetric_optional"}:
        raise ValueError(policy)
    optional_skills = frozenset(optional_skills)
    actual = theory = optional_actual = required_actual = required_theory = 0.0
    excluded = []
    count = 0
    for row in rows:
        if not row.get("included_in_score", True):
            continue
        a = max(0.0, float(row.get("actual_contribution") or 0.0))
        t = max(0.0, float(row.get("theory_contribution") or 0.0))
        skill, effect = str(row.get("skill_name", "")), str(row.get("effect_key", ""))
        optional = skill in optional_skills
        if policy != "published" and a > 1e-9 and t <= 1e-9 and not optional:
            excluded.append((skill, effect, a))
            continue
        count += 1
        if optional and t <= 1e-9:
            optional_actual += a
            if policy == "symmetric_optional":
                t = a
        elif not optional:
            required_actual += a
            required_theory += t
        actual += a
        theory += t
    return SupportReferenceResult(
        actual / theory * 100.0 if theory > 1e-9 else 100.0,
        actual, theory, tuple(excluded), optional_actual, count,
        required_actual / required_theory * 100.0 if required_theory > 1e-9 else None,
    )
