from __future__ import annotations

from ..shared import *


class SkillTimelineOptimizerMixin:
    """Short-horizon ranking layered on top of the deterministic scheduler.

    The scheduler remains responsible for validating and applying the chosen
    action. This layer only compares legal ready actions, so disabling it falls
    back to the old one-step DPA ordering without changing timeline state.
    """

    def _skill_burst_optimizer_profile(self) -> tuple[float, int, int, int]:
        new_scheme = getattr(self, "_skill_burst_new_scheme_1_enabled", None)
        if callable(new_scheme) and new_scheme():
            return 0.0, 0, 0, 0
        if getattr(self, "_skill_burst_disable_hybrid_optimizer", False):
            return 0.0, 0, 0, 0
        job = norm(self.second_job.currentText())
        # The first rollout is deliberately conservative. These jobs produced a
        # measurable gain in the complete 29-job old/new benchmark. Existing
        # strict combo rules remain active for every other job.
        if job not in {"时空领主", "黑暗复仇者"}:
            return 0.0, 0, 0, 0
        if job == "黑暗复仇者":
            return 4.0, 3, 1, 1
        if getattr(self, "_skill_burst_complete_default_rotation", False):
            return 4.0, 3, 3, 3
        return 5.0, 3, 4, 4

    @staticmethod
    def _skill_burst_optimizer_state_rank(state: dict[str, Any], start: float) -> float:
        elapsed = max(0.05, as_number(state.get("time")) - start)
        return max(0.0, as_number(state.get("score"))) / elapsed

    def _skill_burst_optimizer_attrs_at(
        self,
        support_actions: list[dict[str, Any]],
        support_expires_at: dict[int, float],
        current_attrs: dict[str, Any],
        seconds: float,
    ) -> dict[str, Any]:
        attrs = self._skill_burst_active_attrs(support_actions, support_expires_at, seconds)
        for key, value in current_attrs.items():
            if norm(key).startswith("__") and key not in attrs:
                attrs[key] = copy.deepcopy(value)
        return attrs

    def _skill_burst_optimizer_apply_cast(
        self,
        metric: dict[str, Any],
        start: float,
        cast_time: float,
        attrs: dict[str, Any],
        available_at: dict[str, float],
        base_cooldowns: dict[str, float],
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        charged_skill_charges: dict[str, int],
        ammo: int,
    ) -> tuple[dict[str, float], dict[int, float], dict[int, float], dict[str, int], int]:
        skill = self._skill_burst_skill_key(metric.get("skill"))
        next_available = dict(available_at)
        next_support_available = dict(support_available_at)
        next_support_expires = dict(support_expires_at)
        next_charges = dict(charged_skill_charges)
        next_ammo = ammo - self._skill_burst_ammo_cost(skill)
        cooldown = self._skill_burst_effective_cooldown(
            skill,
            as_number(base_cooldowns.get(skill), metric.get("base_cooldown")),
            start,
            attrs,
        )
        if skill in next_charges:
            next_charges[skill] = max(0, next_charges.get(skill, 0) - 1)
            next_available[skill] = start if next_charges[skill] > 0 else start + cooldown
        else:
            next_available[skill] = start + cooldown
        damage_rows = {key: key for key in next_available}
        self._skill_burst_apply_shared_skill_use(
            skill,
            start,
            as_number(base_cooldowns.get(skill), metric.get("base_cooldown")),
            support_actions,
            next_support_available,
            next_support_expires,
            next_available,
            damage_rows,
            base_cooldowns,
            attrs,
            cast_end=start + cast_time,
            charged_counts=next_charges,
            charged_maxes=self._skill_burst_charged_skill_maxes(),
        )
        summon_limit = self._skill_burst_summon_limit(skill)
        if summon_limit and summon_limit[0] == 1:
            next_available[skill] = max(
                next_available.get(skill, start),
                start + cast_time + summon_limit[1],
            )
        return (
            next_available,
            next_support_available,
            next_support_expires,
            next_charges,
            max(0, next_ammo),
        )

    def _skill_burst_short_horizon_dpa(
        self,
        root_item: tuple[float, float, float, float, dict[str, Any]],
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        damage_base_cooldown_by_skill: dict[str, float],
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        active_attrs: dict[str, Any],
        current: float,
        window: float,
        previous_skill: str,
        charged_skill_charges: dict[str, int],
        ammo: int,
        horizon_seconds: float,
        max_depth: int,
        beam_width: int,
        branch_width: int,
    ) -> tuple[float, tuple[str, ...]]:
        root_metric = root_item[4]
        root_skill = self._skill_burst_skill_key(root_metric.get("skill"))
        root_cast = self._skill_burst_scheduled_cast_time(
            root_skill, current, previous_skill, active_attrs
        )
        if root_cast <= 0:
            return max(0.0, root_item[0]), (root_skill,)
        horizon_end = min(window, current + horizon_seconds)
        root_attrs = self._skill_burst_attrs_for_cast(active_attrs, root_skill)
        root_score = self._skill_burst_window_metric_score(
            root_metric, root_attrs, current, window, root_cast
        )
        if current + root_cast > horizon_end + 1e-6:
            root_score *= max(0.0, horizon_end - current) / root_cast
        (
            root_available,
            root_support_available,
            root_support_expires,
            root_charges,
            root_ammo,
        ) = self._skill_burst_optimizer_apply_cast(
            root_metric,
            current,
            root_cast,
            root_attrs,
            damage_available_at,
            damage_base_cooldown_by_skill,
            support_actions,
            support_available_at,
            support_expires_at,
            charged_skill_charges,
            ammo,
        )
        states = [{
            "time": current + root_cast,
            "score": root_score,
            "previous": root_skill,
            "available": root_available,
            "support_available": root_support_available,
            "support_expires": root_support_expires,
            "charges": root_charges,
            "ammo": root_ammo,
            "sequence": (root_skill,),
        }]
        best_state = states[0]
        for _depth in range(1, max_depth):
            expanded: list[dict[str, Any]] = []
            for state in states:
                state_time = as_number(state.get("time"))
                if state_time >= horizon_end - 1e-6:
                    expanded.append(state)
                    continue
                options: list[tuple[float, float, float, dict[str, Any], dict[str, Any]]] = []
                for metric in candidates:
                    skill = self._skill_burst_skill_key(metric.get("skill"))
                    if not skill:
                        continue
                    ready_at = max(state_time, as_number(state["available"].get(skill)))
                    if skill in state["charges"] and state["charges"].get(skill, 0) > 0:
                        ready_at = state_time
                    if ready_at >= horizon_end - 1e-6:
                        continue
                    if self._skill_burst_ammo_cost(skill) > int(state.get("ammo", 0)):
                        continue
                    if not self._skill_burst_predecessor_satisfied(state.get("previous", ""), skill):
                        continue
                    future_attrs = self._skill_burst_optimizer_attrs_at(
                        support_actions,
                        state["support_expires"],
                        active_attrs,
                        ready_at,
                    )
                    if not self._skill_burst_state_requirement_satisfied(skill, future_attrs):
                        continue
                    cast_time = self._skill_burst_scheduled_cast_time(
                        skill, ready_at, state.get("previous", ""), future_attrs
                    )
                    if cast_time <= 0:
                        continue
                    cast_attrs = self._skill_burst_attrs_for_cast(future_attrs, skill)
                    score = self._skill_burst_window_metric_score(
                        metric, cast_attrs, ready_at, window, cast_time
                    )
                    if ready_at + cast_time > horizon_end + 1e-6:
                        score *= max(0.0, horizon_end - ready_at) / cast_time
                    opportunity = self._skill_burst_cooldown_rule_opportunity_score(
                        skill,
                        candidates,
                        state["available"],
                        damage_base_cooldown_by_skill,
                        future_attrs,
                        ready_at,
                    )
                    options.append((
                        (score + opportunity) / max(0.05, cast_time),
                        ready_at,
                        cast_time,
                        metric,
                        future_attrs,
                    ))
                if not options:
                    expanded.append(state)
                    continue
                options.sort(key=lambda row: row[0], reverse=True)
                for _priority, ready_at, cast_time, metric, future_attrs in options[:branch_width]:
                    skill = self._skill_burst_skill_key(metric.get("skill"))
                    cast_attrs = self._skill_burst_attrs_for_cast(future_attrs, skill)
                    score = self._skill_burst_window_metric_score(
                        metric, cast_attrs, ready_at, window, cast_time
                    )
                    if ready_at + cast_time > horizon_end + 1e-6:
                        score *= max(0.0, horizon_end - ready_at) / cast_time
                    (
                        next_available,
                        next_support_available,
                        next_support_expires,
                        next_charges,
                        next_ammo,
                    ) = self._skill_burst_optimizer_apply_cast(
                        metric,
                        ready_at,
                        cast_time,
                        cast_attrs,
                        state["available"],
                        damage_base_cooldown_by_skill,
                        support_actions,
                        state["support_available"],
                        state["support_expires"],
                        state["charges"],
                        int(state.get("ammo", 0)),
                    )
                    expanded.append({
                        "time": ready_at + cast_time,
                        "score": as_number(state.get("score")) + score,
                        "previous": skill,
                        "available": next_available,
                        "support_available": next_support_available,
                        "support_expires": next_support_expires,
                        "charges": next_charges,
                        "ammo": next_ammo,
                        "sequence": (*state.get("sequence", ()), skill),
                    })
            if not expanded:
                break
            expanded.sort(
                key=lambda state: self._skill_burst_optimizer_state_rank(state, current),
                reverse=True,
            )
            states = expanded[:beam_width]
            candidate_best = states[0]
            if self._skill_burst_optimizer_state_rank(candidate_best, current) > self._skill_burst_optimizer_state_rank(best_state, current):
                best_state = candidate_best
        return (
            self._skill_burst_optimizer_state_rank(best_state, current),
            tuple(best_state.get("sequence", (root_skill,))),
        )

    def _skill_burst_rank_ready_with_lookahead(
        self,
        ready: list[tuple[float, float, float, float, dict[str, Any]]],
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        damage_base_cooldown_by_skill: dict[str, float],
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        active_attrs: dict[str, Any],
        current: float,
        window: float,
        previous_skill: str,
        charged_skill_charges: dict[str, int],
        ammo: int,
    ) -> list[tuple[float, float, float, float, dict[str, Any]]]:
        horizon, depth, beam_width, branch_width = self._skill_burst_optimizer_profile()
        if horizon <= 0 or depth <= 1 or len(ready) <= 1:
            return ready
        best_immediate = max((max(0.0, as_number(item[0])) for item in ready), default=0.0)
        ranked: list[tuple[float, float, float, float, dict[str, Any]]] = []
        for item in ready:
            sequence_dpa, sequence = self._skill_burst_short_horizon_dpa(
                item,
                candidates,
                damage_available_at,
                damage_base_cooldown_by_skill,
                support_actions,
                support_available_at,
                support_expires_at,
                active_attrs,
                current,
                window,
                previous_skill,
                charged_skill_charges,
                ammo,
                horizon,
                depth,
                beam_width,
                branch_width,
            )
            immediate = max(0.0, as_number(item[0]))
            # The established one-step score already includes cooldown urgency,
            # support opportunity cost and profession-specific rules. Keep it as
            # the stable anchor, and let short-horizon DPA resolve close choices.
            if best_immediate > 0 and immediate < best_immediate * 0.72:
                hybrid_score = immediate
            else:
                hybrid_score = min(
                    immediate * 1.25,
                    immediate * 0.65 + sequence_dpa * 0.35,
                )
            metric = dict(item[4])
            metric["optimizer_sequence"] = sequence
            metric["optimizer_dpa"] = sequence_dpa
            ranked.append((hybrid_score, item[1], item[2], item[3], metric))
        return ranked
