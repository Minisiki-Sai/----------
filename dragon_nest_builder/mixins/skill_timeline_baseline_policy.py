from __future__ import annotations

from ..shared import *


SKILL_TIMELINE_CONSERVATIVE_1_5_JOBS = {
    "剑皇",
    "月之领主",
    "雷神",
}


class SkillTimelineBaselinePolicyMixin:
    """Conservative Scheme 1.5 ranking for the primary timeline scheduler.

    This layer does not mutate cooldown, buff, summon or resource state. It only
    refines actions that the existing Scheme 1 engine has already deemed legal.
    The original score remains the floor, and the exact-replay Scheme 3 pass can
    continue to improve the completed timeline safely.
    """

    def _skill_burst_conservative_1_5_enabled(self) -> bool:
        new_scheme = getattr(self, "_skill_burst_new_scheme_1_enabled", None)
        if callable(new_scheme) and new_scheme():
            return False
        policy_1_6 = getattr(self, "_skill_burst_conservative_1_6_enabled", None)
        if callable(policy_1_6) and policy_1_6():
            return False
        forced = getattr(self, "_skill_burst_force_conservative_1_5", None)
        if forced is None:
            # Compatibility for benchmark files created during the refactor.
            forced = getattr(self, "_skill_burst_force_refactored_baseline", None)
        if forced is not None:
            return bool(forced)
        configured_jobs = getattr(
            self,
            "_skill_burst_conservative_1_5_jobs",
            SKILL_TIMELINE_CONSERVATIVE_1_5_JOBS,
        )
        return norm(self.second_job.currentText()) in {
            norm(job) for job in configured_jobs
        }

    def _skill_burst_refactored_baseline_enabled(self) -> bool:
        """Backward-compatible alias for pre-1.5 benchmark helpers."""
        return self._skill_burst_conservative_1_5_enabled()

    def _skill_burst_combo_package(
        self,
        metric: dict[str, Any],
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        active_attrs: dict[str, Any],
        start: float,
        window: float,
        previous_skill: str,
        *,
        depth: int = 3,
        visited: frozenset[str] = frozenset(),
    ) -> tuple[float, float, tuple[str, ...]]:
        skill = self._skill_burst_skill_key(metric.get("skill"))
        if not skill or skill in visited or depth <= 0:
            return 0.0, 0.0, ()
        cast_time = self._skill_burst_scheduled_cast_time(
            metric.get("skill", ""), start, previous_skill, active_attrs
        )
        if cast_time <= 0 or start + cast_time > window + 1e-6:
            return 0.0, 0.0, ()
        score = self._skill_burst_window_metric_score(
            metric,
            self._skill_burst_attrs_for_cast(active_attrs, metric.get("skill")),
            start,
            window,
            cast_time,
        )
        base_result = (score, cast_time, (skill,))
        followups = self._skill_burst_combo_followups(skill)
        if not followups or depth <= 1:
            return base_result

        released_targets = {
            target
            for target, source in self._skill_burst_cooldown_rule_sources_for(skill)
            if norm(source.get("type")) in {"reset", "grant_charge"}
        }
        next_start = start + cast_time
        best_result = base_result
        best_dpa = score / cast_time
        next_visited = frozenset((*visited, skill))
        for followup in candidates:
            followup_skill = self._skill_burst_skill_key(followup.get("skill"))
            if followup_skill not in followups or followup_skill in next_visited:
                continue
            if (
                followup_skill not in released_targets
                and as_number(damage_available_at.get(followup_skill)) > next_start + 1e-6
            ):
                continue
            if not self._skill_burst_predecessor_satisfied(skill, followup_skill):
                continue
            if not self._skill_burst_state_requirement_satisfied(followup_skill, active_attrs):
                continue
            followup_score, followup_time, followup_path = self._skill_burst_combo_package(
                followup,
                candidates,
                damage_available_at,
                active_attrs,
                next_start,
                window,
                skill,
                depth=depth - 1,
                visited=next_visited,
            )
            package_time = cast_time + followup_time
            if followup_time <= 0 or package_time <= 0:
                continue
            package_score = score + followup_score
            package_dpa = package_score / package_time
            if package_dpa > best_dpa + 1e-9:
                best_dpa = package_dpa
                best_result = (package_score, package_time, (skill, *followup_path))
        return best_result

    def _skill_burst_combo_package_opportunity_dpa(
        self,
        package_score: float,
        package_time: float,
        package_path: tuple[str, ...],
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        active_attrs: dict[str, Any],
        current: float,
        window: float,
    ) -> float:
        """Score a proposed combo across the time occupied by the whole chain.

        A follow-up's full damage can make a weak opener appear overwhelmingly
        valuable even though that follow-up would have been cast later anyway.
        The package therefore pays for high-value damage or support actions that
        become available while the committed chain is still running.
        """
        if package_score <= 0 or package_time <= 0:
            return 0.0
        package_end = min(window, current + package_time)
        package_dpa = package_score / package_time
        path = {self._skill_burst_skill_key(skill) for skill in package_path}
        penalty = 0.0
        for other in candidates:
            other_skill = self._skill_burst_skill_key(other.get("skill"))
            if not other_skill or other_skill in path:
                continue
            ready_at = as_number(damage_available_at.get(other_skill))
            if ready_at <= current + 1e-6 or ready_at >= package_end - 1e-6:
                continue
            other_cast = self._skill_burst_sim_cast_time(other_skill, ready_at, active_attrs)
            if other_cast <= 0:
                continue
            other_score = self._skill_burst_window_metric_score(
                other,
                active_attrs,
                ready_at,
                window,
                other_cast,
            )
            other_dpa = other_score / other_cast
            if other_dpa <= package_dpa * 1.05:
                continue
            penalty += (other_dpa - package_dpa) * (package_end - ready_at)

        for index, action in enumerate(support_actions):
            support_cast = self._skill_burst_support_cast_time(action, current, active_attrs)
            ready_at = max(
                as_number(support_available_at.get(index)),
                as_number(support_expires_at.get(index)) - support_cast,
            )
            if ready_at <= current + 1e-6 or ready_at >= package_end - 1e-6:
                continue
            support_dpa = self._skill_burst_support_opportunity_dpa(
                action,
                ready_at,
                active_attrs,
                candidates,
                window,
            )
            if support_dpa <= package_dpa * 1.02:
                continue
            penalty += (support_dpa - package_dpa) * (package_end - ready_at)
        return max(0.0, package_score - penalty) / package_time

    def _skill_burst_baseline_damage_rank(
        self,
        metric: dict[str, Any],
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        damage_base_cooldown_by_skill: dict[str, float],
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        active_attrs: dict[str, Any],
        current: float,
        window: float,
        cast_time: float,
        raw_dpa: float,
        raw_score: float,
        previous_skill: str,
    ) -> tuple[float, float, dict[str, Any]]:
        adjusted_dpa, wait_until = self._skill_burst_damage_opportunity_score(
            metric,
            candidates,
            damage_available_at,
            damage_base_cooldown_by_skill,
            support_actions,
            support_available_at,
            support_expires_at,
            active_attrs,
            current,
            window,
            cast_time,
            raw_dpa,
            raw_score,
        )
        optional_pair_dpa = self._skill_burst_optional_combo_pair_dpa(
            metric,
            candidates,
            support_actions,
            support_available_at,
            support_expires_at,
            active_attrs,
            current,
            window,
            previous_skill,
        )
        legacy_rank = max(adjusted_dpa, optional_pair_dpa)
        legacy_rank *= self._skill_burst_combo_bonus(previous_skill, metric.get("skill", ""))
        if not self._skill_burst_conservative_1_5_enabled():
            return legacy_rank, wait_until, {
                "policy": "legacy",
                "direct_dpa": raw_dpa,
                "rank_dpa": legacy_rank,
            }

        package_score, package_time, package_path = self._skill_burst_combo_package(
            metric,
            candidates,
            damage_available_at,
            active_attrs,
            current,
            window,
            previous_skill,
        )
        package_dpa = 0.0
        if package_time > cast_time + 1e-6:
            package_dpa = self._skill_burst_combo_package_opportunity_dpa(
                package_score,
                package_time,
                package_path,
                candidates,
                damage_available_at,
                support_actions,
                support_available_at,
                support_expires_at,
                active_attrs,
                current,
                window,
            )
        # The package model does not replay every charge/reset/state side
        # effect, so the battle-tested legacy rank remains a floor. The exact
        # replay optimizer can safely refine the completed baseline afterward.
        rank_dpa = max(legacy_rank, package_dpa)
        return rank_dpa, wait_until, {
            "policy": "conservative_1_5",
            "direct_dpa": raw_dpa,
            "opportunity_dpa": adjusted_dpa,
            "legacy_rank_dpa": legacy_rank,
            "combo_package_dpa": package_dpa,
            "combo_path": package_path,
            "rank_dpa": rank_dpa,
        }
