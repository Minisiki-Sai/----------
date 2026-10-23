from __future__ import annotations

from functools import lru_cache

class SkillTimelineStateMixin:
    def _skill_burst_freeze_source(self, action: dict[str, Any]) -> str:
        skill_key = self._skill_burst_skill_key(action.get("skill"))
        if skill_key in {"冰光束", "寒冰戳刺"}:
            return "炼金冰冻"
        return f"{norm(self.second_job.currentText())}:{skill_key}"
    def _skill_burst_snapshot_rule(
        self,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        explicit = action.get("snapshot_rule")
        if isinstance(explicit, dict):
            return explicit
        rules = JOB_MECHANICS.job_value(
            norm(self.second_job.currentText()),
            "snapshot_buffs",
            {},
        )
        if not isinstance(rules, dict):
            return {}
        skill_key = self._skill_burst_skill_key(action.get("skill"))
        for skill, rule in rules.items():
            if (
                self._skill_burst_skill_key(skill) == skill_key
                and isinstance(rule, dict)
            ):
                return rule
        return {}

    def _skill_burst_snapshot_dependency_attrs(
        self,
        action: dict[str, Any],
    ) -> tuple[str, ...]:
        rule = self._skill_burst_snapshot_rule(action)
        return tuple(
            norm(attr)
            for attr in rule.get("attributes", ())
            if norm(attr)
        )

    def _skill_burst_snapshot_action_should_defer(
        self,
        action: dict[str, Any],
        ready_support: list[tuple[Any, ...]],
        active_attrs: dict[str, Any] | None,
    ) -> bool:
        """Keep a snapshot buff behind any ready action that improves its inputs.

        Snapshot semantics are data-driven in ``job_mechanics.json``.  The
        scheduler therefore does not need to know a profession or skill name:
        future snapshot buffs only declare the attributes they capture.
        """

        rule = self._skill_burst_snapshot_rule(action)
        attrs = self._skill_burst_snapshot_dependency_attrs(action)
        if not attrs or not rule.get("defer_for_ready_boost"):
            return False
        active = active_attrs or {}
        for item in ready_support:
            other = item[5] if len(item) > 5 else None
            if not isinstance(other, dict) or other is action:
                continue
            other_attrs = {
                **dict(other.get("display_attrs", {})),
                **dict(other.get("attrs", {})),
            }
            if any(
                as_number(other_attrs.get(attr))
                > max(0.0, display_value(active, attr)) + 1e-9
                for attr in attrs
            ):
                return True
        return False

    def _skill_burst_snapshot_action_has_active_boost(
        self,
        action: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> bool:
        """Whether casting now captures a stronger-than-baseline snapshot.

        This is deliberately expressed through the snapshot rule and its
        declared attributes.  It lets the scheduler keep a ready snapshot
        skill next to the boost it captures without naming a job or skill.
        """

        if not self._skill_burst_snapshot_rule(action):
            return False
        current = self._skill_burst_snapshot_multiplier(action, active_attrs)
        baseline = self._skill_burst_snapshot_multiplier(action, {})
        return current > baseline * 1.001

    def _skill_burst_snapshot_periodic_action(
        self,
        action: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> None:
        if not self._skill_burst_snapshot_rule(action):
            return
        action["active_multiplier"] = self._skill_burst_snapshot_multiplier(
            action,
            active_attrs,
        )
        action["last_snapshot_multiplier"] = as_number(action.get("active_multiplier"), 1.0)

    def _skill_burst_snapshot_multiplier(
        self,
        action: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> float:
        rule = self._skill_burst_snapshot_rule(action)
        if norm(rule.get("model")) != "attribute_average":
            return max(0.0, as_number(action.get("active_multiplier"), 1.0))
        snapshot_attrs = self._skill_burst_snapshot_dependency_attrs(action)
        if not snapshot_attrs:
            return max(0.0, as_number(action.get("active_multiplier"), 1.0))
        base_active = as_number(
            action.setdefault(
                "base_active_multiplier",
                action.get("active_multiplier", 1.0),
            ),
            1.0,
        )
        inactive = max(0.0, as_number(action.get("inactive_multiplier"), 1.0))
        scales: list[float] = []
        for attr in snapshot_attrs:
            scale = 1 + max(0.0, display_value(active_attrs or {}, attr)) / 100
            for periodic in getattr(self, "_skill_burst_periodic_buffs", []):
                if periodic is action:
                    continue
                marker = norm(periodic.get("buff_marker"))
                if marker and display_value(active_attrs or {}, marker) > 0:
                    display_attrs = periodic.get("display_attrs", {})
                    action_attrs = periodic.get("attrs", {})
                    if abs(as_number(action_attrs.get(attr))) <= 1e-9:
                        scale *= 1 + max(
                            0.0,
                            as_number(display_attrs.get(attr)),
                        ) / 100
            scales.append(scale)
        stat_scale = sum(scales) / len(scales)
        return inactive + max(0.0, base_active - inactive) * stat_scale

    def _skill_burst_judgement_snapshot_multiplier(
        self,
        action: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> float:
        # Compatibility name retained for existing callers and diagnostics.
        return self._skill_burst_snapshot_multiplier(action, active_attrs)
    def _skill_burst_should_refresh_judgement_snapshot(
        self,
        action: dict[str, Any],
        expires_at: float,
        applies_at: float,
        active_attrs: dict[str, Any] | None,
    ) -> bool:
        rule = self._skill_burst_snapshot_rule(action)
        if not rule:
            return False
        refresh_active = any(
            display_value(active_attrs or {}, marker) > 0
            for marker in rule.get("refresh_markers", ())
            if norm(marker)
        )
        remaining = max(0.0, expires_at - applies_at)
        previous_snapshot = max(0.0, as_number(action.get("last_snapshot_multiplier"), action.get("active_multiplier", 1.0)))
        current_snapshot = self._skill_burst_snapshot_multiplier(action, active_attrs)
        if current_snapshot > previous_snapshot * 1.001:
            return True
        # 化身期间再次施放可把已经快过半的高力智快照重新延长40秒。
        return refresh_active and 0 < remaining <= 30.0 and current_snapshot >= previous_snapshot * 0.995
    def _skill_burst_snapshot_boost_end(
        self,
        support_actions: list[dict[str, Any]],
        support_expires_at: dict[int, float],
        current: float,
        *,
        exclude_skill: str = "",
    ) -> float:
        """Return the next time an active STR/INT snapshot boost falls off."""
        excluded = self._skill_burst_skill_key(exclude_skill)
        expiries: list[float] = []
        relevant = {
            "力量%",
            "智力%",
            "物攻%",
            "魔攻%",
            "双攻%",
            "物攻",
            "魔攻",
        }
        for index, action in enumerate(support_actions):
            if self._skill_burst_skill_key(action.get("skill")) == excluded:
                continue
            expiry = max(0.0, as_number(support_expires_at.get(index)))
            if expiry <= current + 1e-6:
                continue
            attrs = {
                **dict(action.get("display_attrs", {})),
                **dict(action.get("attrs", {})),
            }
            if any(abs(as_number(attrs.get(attr))) > 1e-9 for attr in relevant):
                expiries.append(expiry)
        for row in getattr(self, "_skill_burst_external_team_effect_windows", []):
            start = max(0.0, as_number(row.get("start")))
            end = max(start, as_number(row.get("end")))
            if not (start <= current + 1e-6 < end - 1e-6):
                continue
            effect = row.get("team_effect", {})
            attrs = self._team_practical_display_attrs(effect) if isinstance(effect, dict) else {}
            if any(abs(as_number(attrs.get(attr))) > 1e-9 for attr in relevant):
                expiries.append(end)
        return min(expiries, default=0.0)
    def _skill_burst_refreshable_periodic_active_info(
        self,
        segments: list[dict[str, Any]],
        skill: str,
        current: float,
    ) -> tuple[float, float]:
        skill_key = self._skill_burst_skill_key(skill)
        duration = self._skill_burst_summon_duration(skill_key)
        if duration <= 0:
            return 0.0, 0.0
        best_until = 0.0
        snapshot = 0.0
        for segment in reversed(segments):
            if self._skill_burst_skill_key(segment.get("skill")) != skill_key:
                continue
            effect_start = max(as_number(segment.get("start")), as_number(segment.get("end")))
            effect_until = effect_start + duration
            if effect_until <= current + 1e-6:
                continue
            best_until = effect_until
            # Refresh decisions compare the snapshotted attack panel only.
            # Amp/resistance/freeze layers remain live for periodic damage and
            # must not make a low-panel recast look stronger than its snapshot.
            snapshot = max(
                0.0,
                as_number(
                    segment.get("snapshot_attack_multiplier"),
                    segment.get("snapshot_full_score", segment.get("effective_score")),
                ),
            )
            break
        return best_until, snapshot
    def _skill_burst_refreshable_periodic_snapshot_attack(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> float:
        state = self._skill_burst_periodic_snapshot_state(
            metric,
            active_attrs,
            direct_attack_only=False,
        )
        return max(0.0, as_number(state.get("attack"), 1.0))
    def _skill_burst_refreshable_periodic_expected_next_attack(
        self,
        metric: dict[str, Any],
        support_actions: list[dict[str, Any]],
        support_expires_at: dict[int, float],
        active_until: float,
        window: float,
    ) -> float:
        """Estimate the panel available at the next ordinary refresh."""

        expected_at = min(
            max(0.0, as_number(window)),
            max(0.0, as_number(active_until)) + 1e-4,
        )
        blocked = self._skill_burst_blocked_range_at(expected_at)
        if blocked is not None:
            expected_at = min(max(0.0, as_number(window)), blocked[1] + 1e-4)
        expected_attrs = self._skill_burst_active_attrs(
            support_actions,
            support_expires_at,
            expected_at,
        )
        return self._skill_burst_refreshable_periodic_snapshot_attack(
            metric,
            expected_attrs,
        )
    def _skill_burst_refreshable_periodic_delayed_start(
        self,
        skill: str,
        current: float,
        cast_time: float,
        previous_skill: str,
        active_attrs: dict[str, Any],
        active_until: float,
        boost_end: float,
    ) -> float:
        """Return the latest safe refresh start for a running periodic skill."""
        refresh_deadlines = [max(current, active_until)]
        if boost_end > current + 1e-6:
            refresh_deadlines.append(boost_end)
        refresh_deadline = min(refresh_deadlines)
        deadline_cast_time = max(
            cast_time,
            self._skill_burst_scheduled_cast_time(
                skill,
                max(current, refresh_deadline - 0.01),
                previous_skill,
                active_attrs,
            ),
        )
        return max(current, refresh_deadline - deadline_cast_time - 0.01)
    def _skill_burst_combo_followups(self, previous_skill: str) -> set[str]:
        job = norm(self.second_job.currentText())
        previous_key = self._skill_burst_skill_key(previous_skill)
        followups = {
            self._skill_burst_skill_key(skill)
            for skill in SKILL_BURST_COMBO_FOLLOWUPS_BY_JOB.get(job, {}).get(
                previous_key,
                set(),
            )
        }
        # Required-predecessor declarations describe the same atomic relation
        # from the opposite direction. Include that reverse edge so a skill
        # such as 双飞腿 is consumed immediately after its legal trigger and
        # cannot be separated from it by a support cast.
        for skill, predecessors in SKILL_BURST_REQUIRED_PREDECESSORS_BY_JOB.get(
            job,
            {},
        ).items():
            if previous_key in {
                self._skill_burst_skill_key(value) for value in predecessors
            }:
                followups.add(self._skill_burst_skill_key(skill))
        return followups
    def _skill_burst_forced_combo_followups(self, previous_skill: str) -> set[str]:
        followups = self._skill_burst_combo_followups(previous_skill)
        return followups - self._skill_burst_optional_combo_followups(previous_skill)
    def _skill_burst_optional_combo_followups(self, previous_skill: str) -> set[str]:
        job = norm(self.second_job.currentText())
        previous_key = self._skill_burst_skill_key(previous_skill)
        optional = SKILL_BURST_OPTIONAL_COMBO_FOLLOWUPS_BY_JOB.get(job, {}).get(previous_key, set())
        return {self._skill_burst_skill_key(skill) for skill in optional}
    def _skill_burst_dpa_package_followups(self, skill: str) -> set[str]:
        job = norm(self.second_job.currentText())
        key = self._skill_burst_skill_key(skill)
        package = SKILL_BURST_DPA_PACKAGES_BY_JOB.get(job, {}).get(key, set())
        return {self._skill_burst_skill_key(value) for value in package}
    def _skill_burst_dpa_package_reservation_at(
        self,
        metric: dict[str, Any],
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        active_attrs: dict[str, Any],
        current: float,
        window: float,
        cast_time: float,
    ) -> float | None:
        """Reserve an opener only when an early cast would lose a package use."""

        root_skill = self._skill_burst_skill_key(metric.get("skill"))
        targets = self._skill_burst_dpa_package_followups(root_skill)
        if not targets or cast_time <= 0:
            return None
        root_cooldown = self._skill_burst_effective_cooldown(
            root_skill,
            max(0.0, as_number(metric.get("base_cooldown"))),
            current,
            active_attrs,
        )
        next_root_ready = current + max(cast_time, root_cooldown)
        reservations: list[float] = []
        for target_metric in candidates:
            target_skill = self._skill_burst_skill_key(target_metric.get("skill"))
            if target_skill not in targets:
                continue
            target_ready = max(0.0, as_number(damage_available_at.get(target_skill)))
            # Already chainable now, or the opener naturally recovers before
            # the follow-up: neither case needs a reservation.
            if target_ready <= current + cast_time + 1e-6:
                continue
            if target_ready >= next_root_ready - 1e-6:
                continue
            reserved_root_start = max(current, target_ready - cast_time)
            target_cast = self._skill_burst_scheduled_cast_time(
                target_metric.get("skill", ""),
                target_ready,
                root_skill,
                active_attrs,
            )
            if target_cast <= 0:
                continue
            target_cooldown = self._skill_burst_effective_cooldown(
                target_skill,
                max(0.0, as_number(target_metric.get("base_cooldown"))),
                target_ready,
                active_attrs,
            )

            def remaining_uses(first_start: float) -> int:
                if first_start + target_cast > window + 1e-6:
                    return 0
                if target_cooldown <= 1e-6:
                    return 1
                return 1 + int(
                    max(0.0, window - first_start - target_cast)
                    // target_cooldown
                )

            reserved_target_start = max(target_ready, reserved_root_start + cast_time)
            delayed_target_start = next_root_ready + cast_time
            if remaining_uses(reserved_target_start) > remaining_uses(delayed_target_start):
                reservations.append(reserved_root_start)
        return min(reservations) if reservations else None
    def _skill_burst_required_predecessors(self, skill: str) -> set[str]:
        required = SKILL_BURST_REQUIRED_PREDECESSORS_BY_JOB.get(norm(self.second_job.currentText()), {}).get(
            self._skill_burst_skill_key(skill),
            set(),
        )
        return {self._skill_burst_skill_key(value) for value in required}
    def _skill_burst_required_followups(self, skill: str) -> set[str]:
        required = SKILL_BURST_REQUIRED_FOLLOWUPS_BY_JOB.get(norm(self.second_job.currentText()), {}).get(
            self._skill_burst_skill_key(skill),
            set(),
        )
        return {self._skill_burst_skill_key(value) for value in required}
    def _skill_burst_dance_pair_ready_at(
        self, skill: str, support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
    ) -> float:
        """Gate the opener on stage two, without changing either real cooldown."""
        if self._skill_burst_skill_key(skill) != "九天之舞":
            return 0.0
        return min(
            (as_number(support_available_at.get(index))
             for index, action in enumerate(support_actions)
             if self._skill_burst_skill_key(action.get("skill")) == "九天之舞2阶"
             and not action.get("triggered_only")),
            default=float("inf"),
        )

    def _skill_burst_dance_pair_fits(
        self, support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float], start: float,
        followup_start: float, window: float, active_attrs: dict[str, Any],
    ) -> bool:
        # Stage two must already be ready BEFORE starting stage one.  Checking
        # only the opener (or only damage candidates) leaves orphan openers at
        # cooldown boundaries and immediately before an invulnerable phase.
        for index, action in enumerate(support_actions):
            if (self._skill_burst_skill_key(action.get("skill")) != "九天之舞2阶"
                    or action.get("triggered_only")
                    or as_number(support_available_at.get(index)) > start + 1e-6):
                continue
            cast_time = self._skill_burst_support_cast_time(action, followup_start, active_attrs)
            if cast_time > 0 and followup_start + cast_time <= window + 1e-6:
                return True
        return False

    def _skill_burst_required_followup_ready(
        self,
        skill: str,
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        ready_by: float,
        window: float | None = None,
        active_attrs: dict[str, Any] | None = None,
    ) -> bool:
        # Only an explicitly required follow-up may block the root cast.
        # Ordinary quick-link edges constrain adjacency when that follow-up is
        # selected; treating every edge as mandatory starves charged/high-DPA
        # skills whenever the optional follow-up is still cooling down.
        required = self._skill_burst_required_followups(skill)
        if not required:
            return True

        def chain_fits(
            predecessor: str,
            start_at: float,
            remaining: set[str],
        ) -> bool:
            for next_metric in candidates:
                next_skill = self._skill_burst_skill_key(next_metric.get("skill"))
                if next_skill not in remaining:
                    continue
                if damage_available_at.get(next_skill, 0.0) > start_at + 1e-6:
                    continue
                cast_time = self._skill_burst_scheduled_cast_time(
                    next_metric.get("skill", ""),
                    start_at,
                    predecessor,
                    active_attrs,
                )
                finish_at = start_at + max(0.0, cast_time)
                if window is not None and finish_at > window + 1e-6:
                    continue
                nested = self._skill_burst_required_followups(next_skill)
                if not nested:
                    return True
                if chain_fits(next_skill, finish_at, nested):
                    return True
            return False

        return chain_fits(self._skill_burst_skill_key(skill), ready_by, required)
    def _skill_burst_predecessor_satisfied(self, previous_skill: str, skill: str) -> bool:
        """Whether the cast is legal, independently of its quick-cast timing.

        ``quick_predecessors`` only selects the shortened action.  Treating a
        missed quick link as an illegal cast made manual and automatic axes
        either drop the skill or reuse the quick time after the wrong source.
        Actual cast restrictions belong to ``required_predecessors``.
        """
        required = self._skill_burst_required_predecessors(skill)
        previous_key = self._skill_burst_skill_key(previous_skill)
        return not required or previous_key in required
    def _skill_burst_quick_predecessor_satisfied(self, previous_skill: str, skill: str) -> bool:
        job = norm(self.second_job.currentText())
        previous_key = self._skill_burst_skill_key(previous_skill)
        quick_predecessors = SKILL_BURST_QUICK_PREDECESSORS_BY_JOB.get(job, {}).get(
            self._skill_burst_skill_key(skill),
            set(),
        )
        return not quick_predecessors or previous_key in {
            self._skill_burst_skill_key(value) for value in quick_predecessors
        }
    def _skill_burst_immediate_predecessor(
        self,
        previous_skill: str,
        previous_end: float,
        next_start: float,
    ) -> str:
        """Keep a combo predecessor only when the next cast starts immediately."""
        if max(0.0, as_number(next_start)) > max(0.0, as_number(previous_end)) + 1e-6:
            return ""
        return norm(previous_skill)
    def _skill_burst_scheduled_cast_time(
        self,
        skill: str,
        seconds: float,
        previous_skill: str = "",
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        cast_time = self._skill_burst_sim_cast_time(skill, seconds, active_attrs)
        if not self._skill_burst_quick_predecessor_satisfied(previous_skill, skill):
            full_cast_time = self._skill_burst_sim_full_cast_time(
                skill,
                seconds,
                active_attrs,
            )
            if full_cast_time > 0:
                cast_time = full_cast_time
        return cast_time
    def _skill_burst_summon_limit(self, skill: str) -> tuple[int, float] | None:
        skill_key = self._skill_burst_skill_key(skill)
        job_config = SKILL_BURST_SUMMON_LIMITS_BY_JOB.get(norm(self.second_job.currentText()), {})
        config = job_config.get(skill_key, SKILL_BURST_SUMMON_LIMITS.get(skill_key))
        if not isinstance(config, dict):
            return None
        maximum = max(0, int(as_number(config.get("max"))))
        duration = max(0.0, as_number(config.get("duration")))
        return (maximum, duration) if maximum > 0 and duration > 0 else None
    def _skill_burst_shared_summon_group(
        self,
        skill: str,
    ) -> tuple[frozenset[str], int] | None:
        skill_key = self._skill_burst_skill_key(skill)
        job = norm(self.second_job.currentText())
        for group in SKILL_BURST_SHARED_SUMMON_GROUPS:
            if not isinstance(group, dict):
                continue
            jobs = {norm(value) for value in group.get("jobs", []) if norm(value)}
            skills = frozenset(
                self._skill_burst_skill_key(value)
                for value in group.get("skills", [])
                if self._skill_burst_skill_key(value)
            )
            if job in jobs and skill_key in skills:
                return skills, max(1, int(as_number(group.get("max"), 1)))
        return None
    @staticmethod
    def _skill_burst_prune_summon_state(
        summon_active_until: dict[str, list[float]],
        seconds: float,
    ) -> None:
        for summon_skill, expiry_times in list(summon_active_until.items()):
            summon_active_until[summon_skill] = [
                expiry for expiry in expiry_times
                if expiry > seconds + 1e-6
            ]
    def _skill_burst_summon_removal_schedule(
        self,
    ) -> tuple[tuple[float, str], ...]:
        """Return factual summon deaths on the current local combat clock."""

        rows = getattr(self, "_skill_burst_summon_removal_schedule_override", ())
        if not isinstance(rows, (list, tuple)):
            return ()
        normalized: list[tuple[float, str]] = []
        for row in rows:
            if isinstance(row, dict):
                seconds = as_number(row.get("seconds", row.get("time")))
                skill = row.get("skill", row.get("skill_name"))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                seconds, skill = row[0], row[1]
            else:
                continue
            skill_key = self._skill_burst_skill_key(skill)
            if not skill_key:
                continue
            normalized.append((max(0.0, as_number(seconds)), skill_key))
        return tuple(sorted(normalized, key=lambda item: (item[0], item[1])))
    def _skill_burst_apply_summon_removals(
        self,
        summon_active_until: dict[str, list[float]],
        seconds: float,
        cursor: int,
        schedule: tuple[tuple[float, str], ...] | None = None,
    ) -> tuple[int, list[tuple[float, str, float]]]:
        """Consume every factual death reached by the scheduler clock.

        Expiry time is monotonic with cast order for the reviewed summons, so
        the earliest expiry is also the oldest live instance.  A removal that
        occurs before the theoretical axis has that summon active is ignored;
        it must never kill a future cast retroactively.
        """

        schedule = (
            self._skill_burst_summon_removal_schedule()
            if schedule is None
            else schedule
        )
        index = max(0, int(cursor))
        removed: list[tuple[float, str, float]] = []
        current = max(0.0, as_number(seconds))
        while index < len(schedule) and schedule[index][0] <= current + 1e-6:
            removed_at, skill_key = schedule[index]
            active = [
                expiry
                for expiry in summon_active_until.get(skill_key, ())
                if expiry > removed_at + 1e-6
            ]
            if active:
                victim = min(active)
                summon_active_until[skill_key].remove(victim)
                removed.append((removed_at, skill_key, victim))
            index += 1
        return index, removed
    def _skill_burst_summon_capacity_full(
        self,
        skill: str,
        summon_active_until: dict[str, list[float]],
        seconds: float,
    ) -> bool:
        skill_key = self._skill_burst_skill_key(skill)
        # The egg is a direct attack and renews vulnerability. Full pet slots
        # evict the oldest duck on registration; they do not forbid casting.
        if skill_key == "重磅鸭" and norm(self.second_job.currentText()) in {"重炮手", "机械大师"}:
            return False
        summon_limit = self._skill_burst_summon_limit(skill_key)
        if summon_limit:
            active = sum(
                expiry > seconds + 1e-6
                for expiry in summon_active_until.get(skill_key, [])
            )
            if active >= summon_limit[0]:
                return True
        shared_group = self._skill_burst_shared_summon_group(skill_key)
        if shared_group:
            skills, maximum = shared_group
            active = sum(
                sum(
                    expiry > seconds + 1e-6
                    for expiry in summon_active_until.get(group_skill, [])
                )
                for group_skill in skills
            )
            if active >= maximum:
                return True
        return False
    def _skill_burst_summon_refresh_ready_at(
        self,
        skill: str,
        summon_active_until: dict[str, list[float]],
        seconds: float,
        cast_time: float = 0.0,
    ) -> float | None:
        """Return the conservative idle-slot time for an early summon refresh."""
        if getattr(self, "_skill_burst_disable_early_summon_refresh", False):
            return None
        skill_key = self._skill_burst_skill_key(skill)
        relevant_expiries: list[float] = []
        summon_limit = self._skill_burst_summon_limit(skill_key)
        own_active = [
            expiry for expiry in summon_active_until.get(skill_key, [])
            if expiry > seconds + 1e-6
        ]
        if summon_limit and len(own_active) >= summon_limit[0]:
            relevant_expiries.extend(own_active)
        shared_group = self._skill_burst_shared_summon_group(skill_key)
        if shared_group:
            skills, maximum = shared_group
            shared_active = [
                expiry
                for group_skill in skills
                for expiry in summon_active_until.get(group_skill, [])
                if expiry > seconds + 1e-6
            ]
            if len(shared_active) >= maximum:
                relevant_expiries.extend(shared_active)
        if not relevant_expiries:
            return None
        # Begin the recast while the oldest instance is still alive, but only
        # when its remaining life can be fully consumed by the cast animation.
        # The new instance therefore activates at (or after) the old expiry and
        # moves the action into an idle slot without discarding periodic damage.
        refresh_lead = max(0.05, max(0.0, as_number(cast_time)))
        return max(seconds, min(relevant_expiries) - refresh_lead)
    def _skill_burst_register_summon(
        self,
        skill: str,
        summon_active_until: dict[str, list[float]],
        expires_at: float,
        seconds: float,
    ) -> list[tuple[str, float]]:
        """Add one summon and evict the oldest instance from saturated caps."""
        skill_key = self._skill_burst_skill_key(skill)
        self._skill_burst_prune_summon_state(summon_active_until, seconds)
        summon_active_until.setdefault(skill_key, []).append(max(seconds, expires_at))
        removed: list[tuple[str, float]] = []

        def remove_oldest(skills: set[str] | frozenset[str]) -> bool:
            entries = [
                (expiry, group_skill)
                for group_skill in skills
                for expiry in summon_active_until.get(group_skill, [])
                if expiry > seconds + 1e-6
            ]
            if not entries:
                return False
            expiry, group_skill = min(entries, key=lambda item: (item[0], item[1]))
            summon_active_until[group_skill].remove(expiry)
            removed.append((group_skill, expiry))
            return True

        summon_limit = self._skill_burst_summon_limit(skill_key)
        if summon_limit:
            while len(summon_active_until.get(skill_key, [])) > summon_limit[0]:
                if not remove_oldest({skill_key}):
                    break
        shared_group = self._skill_burst_shared_summon_group(skill_key)
        if shared_group:
            skills, maximum = shared_group
            while sum(len(summon_active_until.get(name, [])) for name in skills) > maximum:
                if not remove_oldest(skills):
                    break
        return removed
    def _skill_burst_heavy_duck_target_count(self) -> int:
        requested = int(as_number(
            getattr(self, "_skill_burst_heavy_duck_target_override", SKILL_BURST_HEAVY_DUCK_TARGET_COUNT),
            SKILL_BURST_HEAVY_DUCK_TARGET_COUNT,
        ))
        return max(1, min(3, requested))
    def _skill_burst_shared_summon_limit_reached(
        self,
        skill: str,
        summon_active_until: dict[str, list[float]],
        seconds: float,
    ) -> bool:
        shared_group = self._skill_burst_shared_summon_group(skill)
        if not shared_group:
            return False
        skills, maximum = shared_group
        active_count = sum(
            sum(
                expiry > seconds + 1e-6
                for expiry in summon_active_until.get(group_skill, [])
            )
            for group_skill in skills
        )
        return active_count >= maximum
    def _skill_burst_holy_relic_count(
        self,
        summon_active_until: dict[str, list[float]],
        seconds: float,
    ) -> int:
        relic_skills = set(JOB_MECHANICS.job_value(self.second_job.currentText(), "holy_relic_skills", []))
        if not relic_skills:
            return 0
        return sum(
            sum(expiry > seconds + 1e-6 for expiry in summon_active_until.get(skill, []))
            for skill in relic_skills
        )
    def _skill_burst_attach_summon_state(
        self,
        active_attrs: dict[str, Any],
        summon_active_until: dict[str, list[float]],
        seconds: float,
    ) -> None:
        job = norm(self.second_job.currentText())
        if job in {"重炮手", "机械大师"}:
            active_attrs[SKILL_BURST_HEAVY_DUCK_COUNT_MARKER] = float(
                sum(expiry > seconds + 1e-6 for expiry in summon_active_until.get("重磅鸭", []))
            )
        if job == "圣徒":
            active_attrs[SKILL_BURST_HOLY_RELIC_COUNT_MARKER] = float(
                self._skill_burst_holy_relic_count(summon_active_until, seconds)
            )
    def _skill_burst_combo_bonus(self, previous_skill: str, skill: str) -> float:
        skill_key = self._skill_burst_skill_key(skill)
        followups = self._skill_burst_combo_followups(previous_skill)
        if skill_key not in followups:
            return 1.0
        job = norm(self.second_job.currentText())
        if skill_key in self._skill_burst_optional_combo_followups(previous_skill):
            return 1.0
        return 3.0 if job == "月之领主" else 2.2
    def _skill_burst_charged_skill_maxes(self) -> dict[str, int]:
        charged: dict[str, int] = {}
        tree = getattr(self, "skill_build_data", None)
        if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
            tree = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        charge_pattern = re.compile(r"冷却时间(?:可以|可)储存\s*(\d+)\s*次")
        reset_pattern = re.compile(r"冷却时间额外重置\s*(\d+)\s*次")
        saved = self.build.get("skill_build", {}) if isinstance(self.build.get("skill_build"), dict) else {}
        saved_levels = (
            saved.get("levels", {})
            if isinstance(saved.get("levels"), dict)
            and norm(saved.get("second_job")) == norm(self.second_job.currentText())
            else {}
        )
        default_levels = self._skill_build_payload_levels(
            self._skill_build_default_payload_for_current(), "skill_build"
        )
        for skill_id, skill in tree.get("skills", {}).items():
            name = norm(skill.get("name"))
            skill_key = norm(skill_id)
            spin = getattr(self, "skill_build_level_spins", {}).get(skill_key)
            if isinstance(spin, QSpinBox):
                level = int(spin.value())
            elif skill_key in saved_levels:
                level = int(as_number(saved_levels.get(skill_key)))
            elif skill_key in default_levels:
                level = int(as_number(default_levels.get(skill_key)))
            else:
                level = self._skill_build_free_default_level(skill)
            if not name or level is None or level <= 0:
                continue
            effective_level = int(level) + self._skill_mastery_bonus_for_skill(name)
            level_rows = [
                row
                for row in skill.get("levels", [])
                if isinstance(row, dict) and int(as_number(row.get("level"))) <= effective_level
            ]
            if not level_rows:
                continue
            level_row = max(level_rows, key=lambda row: int(as_number(row.get("level"))))
            matches = [int(value) for value in charge_pattern.findall(norm(level_row.get("desc")))]
            reset_matches = [int(value) + 1 for value in reset_pattern.findall(norm(level_row.get("desc")))]
            maximum = max([*matches, *reset_matches, 0])
            if maximum > 1:
                charged[self._skill_burst_skill_key(name)] = maximum
        return charged
    def _skill_burst_damage_multiplier(
        self,
        skill: str,
        seconds: float | None = None,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        if seconds is None:
            return 1.0
        job = norm(self.second_job.currentText())
        skill_key = self._skill_burst_skill_key(skill)
        dynamic_multiplier = self._skill_burst_action_output_multiplier(
            skill,
            seconds,
            active_attrs,
        )
        action_state_ratio = dynamic_multiplier
        if job == "雷神" and skill_key in SKILL_BURST_HOLY_GROUND_FOLLOWUPS:
            # 圣光十字/神圣暴风的技能面板只在神圣大地生效后的
            # 10 秒强化窗口内保持完整；窗口外回到一半面板。
            holy_ground_active = (
                display_value(
                    active_attrs or {},
                    SKILL_BURST_HOLY_GROUND_REQUIREMENT_MARKER,
                )
                > 0
            )
            if not holy_ground_active:
                dynamic_multiplier *= SKILL_BURST_HOLY_GROUND_OUTSIDE_MULTIPLIER
        dynamic_multiplier *= SKILL_TIMELINE_DAMAGE_MULTIPLIERS_BY_JOB.get((job, skill_key), 1.0)
        from ..live_dps.score_v2 import timeline_factor
        dynamic_multiplier *= timeline_factor(self, skill, seconds, action_state_ratio)
        return dynamic_multiplier
    def _skill_burst_action_output_multiplier(
        self,
        skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        """Return only the output change caused by action-speed hit counts."""
        job = norm(self.second_job.currentText())
        skill_key = self._skill_burst_skill_key(skill)
        dynamic_multiplier = 1.0
        if is_ring_strike(skill_key):
            # Use the same reference for both actual replay and theory.
            # Integrate speed only over its airborne loop (not the launch),
            # then sum the per-Hit ramp instead of multiplying by speed.
            timing = self._skill_effective_cast_timing(skill, 0.0)
            duration = max(0.0, as_number(timing.get("base_cast_time"), as_number(timing.get("cast_time"))))
            start_phase = ring_strike_reference_start_seconds(duration)
            glyph_speed = self._skill_glyph_action_speed(skill)
            speed = self._skill_burst_sim_action_speed_at(seconds, active_attrs, skill=skill_key) + glyph_speed
            loop_start = seconds + start_phase / max(1.0, 1.0 + speed / 100.0)
            loop_duration = max(0.0, duration - start_phase)
            if loop_duration > 0:
                units = self._skill_burst_action_units(
                    loop_start, loop_duration, active_attrs,
                    minimum_persistent_speed=self._skill_burst_persistent_action_speed(active_attrs, skill_key) + glyph_speed,
                    skill=skill_key,
                )
                dynamic_multiplier = ring_strike_output_multiplier(units / loop_duration)
        elif skill_key in SKILL_FIXED_DURATION_ACTION_SPEED_OUTPUT_SKILLS:
            timing = self._skill_effective_cast_timing(skill, 0.0)
            duration = max(0.0, as_number(timing.get("cast_time")))
            if duration > 0:
                dynamic_multiplier *= self._skill_burst_action_units(
                    seconds,
                    duration,
                    active_attrs,
                    skill=skill_key,
                ) / duration
        if job == "黑暗复仇者" and skill_key == "黑暗冲浪":
            dynamic_multiplier = (
                self._skill_dark_suffer_multiplier(seconds, active_attrs)
                / SKILL_DARK_SUFFER_BASE_MULTIPLIER
            )
        stance_rule = self._skill_stance_action_rule(skill_key)
        if stance_rule:
            dynamic_multiplier *= (
                self._skill_stance_action_multiplier(skill_key, seconds, active_attrs)
                / max(0.01, as_number(stance_rule.get("base_multiplier"), 1.0))
            )
        rule = self.build.get('_score_v2_profiles',{}).get(skill_key)
        from ..live_dps.score_v2 import speed_output_factor, uses_native_speed_reference
        native_speed_reference = uses_native_speed_reference(rule)
        if rule and (rule.get('speed_model') in {'flash','hacking','hacking_ex','siege','siege_ex'} or native_speed_reference):
            duration = (max(.001,as_number(self._skill_effective_cast_timing(skill,0.).get('cast_time')))
                if native_speed_reference else (6. if rule['speed_model'].startswith('hacking') else 4.))
            action_units = self._skill_burst_action_units(seconds,duration,active_attrs,skill=skill_key)
            dynamic_multiplier = speed_output_factor(rule,max(0.,action_units/duration-1.)*100,dynamic_multiplier)
        return dynamic_multiplier
    def _skill_burst_persistent_action_speed(
        self,
        active_attrs: dict[str, Any] | None = None,
        skill: str = "",
    ) -> float:
        return self._skill_burst_scoped_timing_attr(
            skill, active_attrs, "动作速度%"
        ) + self._skill_burst_scoped_timing_attr(skill, active_attrs, "攻速%")
    def _skill_burst_replay_persistent_action_speed(
        self,
        observed_speed: float,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        """Remove schedule-owned Spirit Boost speed from replay observations.

        ``LiveCastTimingResolver`` reports the complete action-speed total seen
        by a captured cast.  The long-timeline engine separately integrates
        Spirit Boost through its pinned burst interval.  Feeding the complete
        observed total back into ``active_attrs`` therefore counted Spirit
        Boost twice for fixed-duration skills such as Surround.  Preserve
        personal/otherwise unmodelled speed while leaving the scheduled 40%
        layer to the shared mode timeline.
        """

        modeled_persistent = self._skill_burst_persistent_action_speed(
            active_attrs
        )
        scheduled_speed = 0.0
        if not self._skill_burst_sim_enabled("practical") and any(
            start - 1e-6 <= seconds < end - 1e-6
            for start, end in self._skill_burst_mode_intervals("burst")
        ):
            scheduled_speed = 40.0
        observed_persistent = max(
            0.0,
            as_number(observed_speed) - scheduled_speed,
        )
        return max(modeled_persistent, observed_persistent)
    def _skill_burst_action_units(
        self,
        start: float,
        duration: float,
        active_attrs: dict[str, Any] | None = None,
        minimum_persistent_speed: float = 0.0,
        skill: str = "",
    ) -> float:
        duration = max(0.0, as_number(duration))
        if duration <= 0:
            return 0.0
        persistent_speed = max(
            max(0.0, as_number(minimum_persistent_speed)),
            self._skill_burst_persistent_action_speed(active_attrs, skill),
        )
        action_units = duration * (1 + persistent_speed / 100)
        if not self._skill_burst_sim_enabled("practical"):
            start = max(0.0, as_number(start))
            end = start + duration
            for burst_start, burst_end in self._skill_burst_mode_intervals("burst"):
                overlap = max(0.0, min(end, burst_end) - max(start, burst_start))
                action_units += overlap * 0.4
        return action_units
    def _skill_burst_dynamic_detail(
        self,
        skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> str:
        if is_ring_strike(skill):
            ratio = self._skill_burst_action_output_multiplier(skill, seconds, active_attrs)
            job = norm(self.second_job.currentText())
            ex = job in {"曜", "输出曜", "打坐曜"}
            # The reference damage is already EX-adjusted in the skill panel.
            base_hits = ring_strike_default_hit_count()
            total = ring_strike_total_multiplier(base_hits, ex=ex) * ratio
            return f"默认平地参考；逐Hit递增后封顶；本次{total:g}倍（不按实际落差/Hit反推）"
        if (
            norm(self.second_job.currentText()) == "圣徒"
            and self._skill_burst_skill_key(skill) == "冲击之手"
        ):
            relic_count = max(
                0,
                int(round(display_value(active_attrs or {}, SKILL_BURST_HOLY_RELIC_COUNT_MARKER))),
            )
            return f"在场之手 {relic_count} 个；每个之手结算 1 次冲击之手面板（本次 {relic_count} 倍）"
        if (
            norm(self.second_job.currentText()) == "黑暗复仇者"
            and self._skill_burst_skill_key(skill) == "黑暗冲浪"
        ):
            count = self._skill_dark_suffer_projectile_count(seconds, active_attrs)
            multiplier = count * SKILL_DARK_SUFFER_EXPLOSION_MULTIPLIER
            return f"慢剑气 {count} 枚 × 200%爆炸 = {multiplier:g}倍"
        stance_rule = self._skill_stance_action_rule(skill)
        if stance_rule:
            count = self._skill_stance_action_count(skill, seconds, active_attrs)
            per_hit = max(0.0, as_number(stance_rule.get("per_hit_multiplier"), 1.0))
            fixed = max(0.0, as_number(stance_rule.get("fixed_multiplier")))
            multiplier = count * per_hit + fixed
            detail = f"{norm(stance_rule.get('hit_label')) or '攻击'} {count} 次 × {per_hit * 100:g}%"
            fixed_label = norm(stance_rule.get("fixed_label"))
            if fixed > 0:
                detail += f" + {fixed_label or f'固定{fixed * 100:g}%'}"
            return f"{detail} = {multiplier:g}倍"
        return ""
    def _skill_burst_passive_cooldown_sources(self) -> dict[str, dict[str, Any]]:
        sources = SKILL_BURST_PASSIVE_COOLDOWN_SOURCES_BY_JOB.get(norm(self.second_job.currentText()), {})
        if not isinstance(sources, dict):
            return {}
        return {
            self._skill_burst_skill_key(source_skill): config
            for source_skill, config in sources.items()
            if self._skill_burst_skill_key(source_skill) and isinstance(config, dict)
        }
    def _skill_burst_passive_cooldown_source_interval(
        self,
        source_skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        config = self._skill_burst_passive_cooldown_sources().get(self._skill_burst_skill_key(source_skill), {})
        base_cooldown = max(0.0, as_number(config.get("base_cooldown")))
        interval_multiplier = max(0.0, as_number(config.get("interval_multiplier"), 1.0))
        if base_cooldown <= 0 or interval_multiplier <= 0:
            return 0.0
        ready_at = self._skill_burst_cooldown_ready_at(
            source_skill,
            base_cooldown * interval_multiplier,
            seconds,
            active_attrs,
        )
        return max(0.01, ready_at - max(0.0, as_number(seconds)))
    def _skill_burst_cast_driven_passive_trigger_count(
        self,
        skill: str,
        seconds: float,
    ) -> float:
        if norm(self.second_job.currentText()) != "火舞":
            return 0.0
        config = self._skill_burst_passive_cooldown_sources().get("灼烧之手", {})
        if not config.get("cast_driven"):
            return 0.0
        return self.store.skill_burning_hand_trigger_count(
            self.second_job.currentText(),
            skill,
            self._skill_burning_hand_mode_at(max(0.0, as_number(seconds))),
        )
    def _skill_burst_cooldown_rule_sources_for(self, source_skill: str) -> list[tuple[str, dict[str, Any]]]:
        source_key = self._skill_burst_skill_key(source_skill)
        job = norm(self.second_job.currentText())
        cache = getattr(self, "_skill_burst_cooldown_rule_sources_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_cooldown_rule_sources_cache = cache
        cache_key = (job, source_key)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        rules = self.store.skill_cooldown_rules().get("jobs", {}).get(job, {})
        if not isinstance(rules, dict):
            cache[cache_key] = []
            return []
        matches: list[tuple[str, dict[str, Any]]] = []
        for target_skill, rule in rules.items():
            if not isinstance(rule, dict):
                continue
            target_key = self._skill_burst_skill_key(target_skill)
            sources = rule.get("sources", [])
            if not isinstance(sources, list):
                sources = []
            has_explicit_self_source = any(
                isinstance(source, dict)
                and self._skill_burst_skill_key(source.get("skill")) == source_key
                and target_key == source_key
                for source in sources
            )
            self_refund = max(0.0, as_number(rule.get("self_refund_seconds")))
            if target_key == source_key and self_refund > 0 and not has_explicit_self_source:
                # Keep the original skill-tree cooldown, then apply the EX refund after the cast.
                matches.append((target_key, {
                    "skill": source_key,
                    "type": "fixed_seconds",
                    "value": self_refund,
                    "self_refund": True,
                }))
            for source in sources:
                if isinstance(source, dict) and self._skill_burst_skill_key(source.get("skill")) == source_key:
                    matches.append((target_key, source))
        cache[cache_key] = matches
        return matches
    def _skill_burst_advance_stored_charges(
        self,
        seconds: float,
        available_at: dict[str, float],
        base_cooldowns: dict[str, float],
        counts: dict[str, int],
        maxes: dict[str, int],
        recharge_at: dict[str, Any],
        active_attrs: dict[str, Any] | None = None,
        targets: Iterable[str] | None = None,
    ) -> None:
        """Advance natural recovery before consuming or granting a stored use."""
        for target in targets if targets is not None else counts:
            cap = max(0, int(maxes.get(target, 0)))
            if not cap or target not in counts:
                continue
            count = min(cap, max(0, int(counts[target])))
            timer = recharge_at.get(target)
            if isinstance(timer, list):
                count = min(cap, count + sum(t <= seconds + 1e-6 for t in timer))
                timer = sorted(t for t in timer if t > seconds + 1e-6) if count < cap else []
            else:
                while timer is not None and timer <= seconds + 1e-6 and count < cap:
                    count += 1
                    interval = self._skill_burst_effective_cooldown(
                        target, as_number(base_cooldowns.get(target)), timer, active_attrs
                    )
                    timer = timer + interval if count < cap and interval > 0 else None
                if count == cap:
                    timer = None
            counts[target] = count
            recharge_at[target] = timer
            if count > 0:
                available_at[target] = seconds
            elif isinstance(timer, list) and timer:
                available_at[target] = min(timer)
            elif timer is not None and not isinstance(timer, list):
                available_at[target] = timer

    def _skill_burst_apply_charge_rule_source(
        self,
        source_skill: str,
        cast_end: float,
        available_at: dict[str, float],
        base_cooldowns: dict[str, float],
        counts: dict[str, int],
        maxes: dict[str, int],
        recharge_at: dict[str, Any] | None = None,
        active_attrs: dict[str, Any] | None = None,
    ) -> None:
        """Grant stored uses at cast completion, preserving recharge progress.

        Formal/preview planners store a single serial recharge deadline; manual
        replay stores the entire serial queue. Refund the last missing use, not
        the first partially recharged use. Full stock cancels any pending timer.
        """
        for target, source in self._skill_burst_cooldown_rule_sources_for(source_skill):
            if norm(source.get("type")) != "grant_charge" or target not in counts:
                continue
            cap = max(0, int(maxes.get(target, 0)))
            grant = max(0, int(as_number(source.get("value"))))
            if not cap or not grant:
                continue
            if recharge_at is not None:
                self._skill_burst_advance_stored_charges(
                    cast_end, available_at, base_cooldowns, counts, maxes,
                    recharge_at, active_attrs, (target,),
                )
            count = min(cap, max(0, int(counts[target])))
            timer = recharge_at.get(target) if recharge_at is not None else None
            if isinstance(timer, list):
                pending = sorted(timer)
                added = min(grant, cap - count)
                count += added
                if added:
                    pending = pending[:-added]
                recharge_at[target] = [] if count == cap else pending
            else:
                count = min(cap, count + grant)
                if recharge_at is not None:
                    recharge_at[target] = None if count == cap else timer
            counts[target] = count
            if count > 0:
                available_at[target] = cast_end

    def _skill_burst_apply_cooldown_rule_source(
        self,
        source_skill: str,
        start: float,
        damage_available_at: dict[str, float],
        damage_base_cooldown_by_skill: dict[str, float],
        active_attrs: dict[str, Any] | None = None,
        support_actions: list[dict[str, Any]] | None = None,
        support_available_at: dict[int, float] | None = None,
        trigger_count: float = 1.0,
    ) -> None:
        trigger_count = max(0.0, as_number(trigger_count, 1.0))
        if trigger_count <= 1e-9:
            return
        external_factor = 1 - self._skill_burst_sim_cdr_percent_at(start, active_attrs) / 100
        for target_skill, source in self._skill_burst_cooldown_rule_sources_for(source_skill):
            support_indexes = [
                index
                for index, action in enumerate(support_actions or [])
                if self._skill_burst_skill_key(action.get("skill")) == target_skill
            ]
            if target_skill not in damage_available_at and not support_indexes:
                continue
            source_type = norm(source.get("type"))
            if source_type == "grant_charge":
                # Requires explicit stock state, applied by the shared cast
                # handler at completion. Never turn this into a CD reset.
                continue
            if source_type == "reset":
                if target_skill in damage_available_at:
                    damage_available_at[target_skill] = min(damage_available_at.get(target_skill, start), start)
                for index in support_indexes:
                    if support_available_at is not None:
                        support_available_at[index] = min(support_available_at.get(index, start), start)
                continue
            support_base = max(
                [as_number((support_actions or [])[index].get("base_cooldown")) for index in support_indexes]
                or [0.0]
            )
            target_base = max(0.0, as_number(damage_base_cooldown_by_skill.get(target_skill)), support_base)
            if target_base <= 0:
                continue
            if source_type == "fixed_seconds" and source.get("self_refund"):
                # A self refund reduces the skill's total cooldown before the
                # timeline integrates changing CDR windows. Subtracting wall
                # clock time from an already accelerated ready time can make a
                # cast ready before its own animation has finished when an
                # acceleration window begins shortly after the cast.
                reduced_base = max(
                    0.0,
                    target_base
                    - max(0.0, as_number(source.get("value"))) * trigger_count,
                )
                if target_skill in damage_available_at:
                    damage_available_at[target_skill] = (
                        self._skill_burst_cooldown_ready_at(
                            target_skill,
                            reduced_base,
                            start,
                            active_attrs,
                        )
                    )
                for index in support_indexes:
                    if support_available_at is not None:
                        support_available_at[index] = (
                            self._skill_burst_cooldown_ready_at(
                                target_skill,
                                reduced_base,
                                start,
                                active_attrs,
                            )
                        )
                continue
            if source_type == "fixed_seconds":
                reduction = max(0.0, as_number(source.get("value"))) * trigger_count
            elif source_type == "target_percent":
                reduction = (
                    target_base
                    * max(0.0, as_number(source.get("value")))
                    * external_factor
                    * trigger_count
                )
            else:
                continue
            if reduction <= 0:
                continue
            if target_skill in damage_available_at:
                target_ready_at = as_number(damage_available_at.get(target_skill, start))
                if target_ready_at > start + 1e-6:
                    if source_type == "fixed_seconds":
                        remaining_work = self._skill_burst_cooldown_work_between(
                            start,
                            target_ready_at,
                            active_attrs,
                        )
                        damage_available_at[target_skill] = self._skill_burst_cooldown_ready_from_work(
                            target_skill,
                            max(0.0, remaining_work - reduction),
                            start,
                            active_attrs,
                        )
                    else:
                        remaining = target_ready_at - start
                        damage_available_at[target_skill] = start + max(0.0, remaining - reduction)
            for index in support_indexes:
                if support_available_at is None:
                    continue
                support_ready_at = as_number(support_available_at.get(index, start))
                if support_ready_at > start + 1e-6:
                    if source_type == "fixed_seconds":
                        remaining_work = self._skill_burst_cooldown_work_between(
                            start,
                            support_ready_at,
                            active_attrs,
                        )
                        support_available_at[index] = self._skill_burst_cooldown_ready_from_work(
                            target_skill,
                            max(0.0, remaining_work - reduction),
                            start,
                            active_attrs,
                        )
                    else:
                        remaining = support_ready_at - start
                        support_available_at[index] = start + max(0.0, remaining - reduction)
    def _skill_burst_cooldown_rule_opportunity_score(
        self,
        source_skill: str,
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        damage_base_cooldown_by_skill: dict[str, float],
        active_attrs: dict[str, Any],
        current: float,
    ) -> float:
        candidate_by_skill = {
            self._skill_burst_skill_key(candidate.get("skill")): candidate
            for candidate in candidates
        }
        external_factor = 1 - self._skill_burst_sim_cdr_percent_at(current, active_attrs) / 100
        bonus = 0.0
        effective_source = source_skill
        source_multiplier = 1.0
        burning_triggers = self._skill_burst_cast_driven_passive_trigger_count(
            source_skill,
            current,
        )
        if burning_triggers > 1e-9:
            effective_source = "灼烧之手"
            source_multiplier = burning_triggers
        for target_skill, source in self._skill_burst_cooldown_rule_sources_for(effective_source):
            target_metric = candidate_by_skill.get(target_skill)
            if not target_metric:
                continue
            available_at = as_number(damage_available_at.get(target_skill))
            remaining = max(0.0, available_at - current)
            if remaining <= 1e-6:
                continue
            target_base = max(0.0, as_number(damage_base_cooldown_by_skill.get(target_skill)))
            source_type = norm(source.get("type"))
            if source_type in {"reset", "grant_charge"}:
                reduction = remaining
            elif source_type == "fixed_seconds":
                reduction = (
                    max(0.0, as_number(source.get("value")))
                    * external_factor
                    * source_multiplier
                )
            elif source_type == "target_percent" and target_base > 0:
                reduction = (
                    target_base
                    * max(0.0, as_number(source.get("value")))
                    * external_factor
                    * source_multiplier
                )
            else:
                reduction = 0.0
            actual_reduction = min(remaining, max(0.0, reduction))
            if actual_reduction <= 1e-6:
                continue
            target_cast = self._skill_burst_sim_cast_time(target_metric.get("skill", ""), current, active_attrs)
            if target_cast <= 0:
                continue
            target_score = self._skill_burst_full_metric_score(target_metric, active_attrs, current)
            target_dpa = target_score / target_cast
            raw_value = target_dpa * actual_reduction
            if source_type in {"reset", "grant_charge"}:
                bonus += min(raw_value, target_score * 0.75)
            elif source_type == "fixed_seconds":
                bonus += min(raw_value * 0.25, target_score * 0.20)
            elif source_type == "target_percent":
                bonus += min(raw_value * 0.35, target_score * 0.35)
        return bonus
    @staticmethod
    def _skill_burst_holy_ground_recent(
        active_effects: Iterable[dict[str, Any]],
        current: float,
    ) -> bool:
        current = max(0.0, as_number(current))
        return any(
            BuildEditor._skill_burst_skill_key(effect.get("skill")) == "神圣大地"
            and not norm(effect.get("provider"))
            and -1e-6
            <= current - max(0.0, as_number(effect.get("applied_at")))
            <= SKILL_BURST_HOLY_GROUND_WINDOW_SECONDS + 1e-6
            for effect in active_effects
        )


def bind_host_globals(host_globals: dict[str, object]) -> None:
    protected = {'bind_host_globals', 'SkillTimelineStateMixin'}
    for name, value in host_globals.items():
        if name.startswith('__') or name in protected:
            continue
        globals()[name] = value


# Extracted from skill_timeline_engine.py; algorithm bodies are unchanged.
