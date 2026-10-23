from __future__ import annotations

from ..shared import *


SKILL_TIMELINE_LOCAL_OPTIMIZER_JOBS = {
    "时空领主",
    "剑皇",
    "影舞者",
    "机械大师",
    "圣骑士",
    "刀锋舞者",
    "暗",
}

SKILL_TIMELINE_LOCAL_OPTIMIZER_VERIFIED_MODES = {
    "时空领主": {frozenset({"accel", "burst"})},
    "剑皇": {
        frozenset({"practical"}),
        frozenset({"cocktail"}),
        frozenset({"accel"}),
        frozenset({"accel", "burst"}),
    },
    "影舞者": {
        frozenset({"accel"}),
        frozenset({"burst", "cocktail"}),
        frozenset({"accel", "burst"}),
    },
    "圣骑士": {
        frozenset({"practical"}),
        frozenset({"cocktail"}),
        frozenset({"burst"}),
        frozenset({"accel", "burst"}),
    },
    "刀锋舞者": {frozenset({"accel"})},
    "暗": {frozenset({"burst"})},
}


class SkillTimelineLocalOptimizerMixin:
    """Conservatively improve a completed timeline through exact replay.

    The deterministic scheduler remains the guaranteed baseline. This pass only
    reorders adjacent, indivisible combo blocks and accepts a change when the
    complete runtime replay produces a strictly higher total output.
    """

    def _skill_burst_local_optimizer_profile(self, window: float) -> tuple[int, int, float]:
        new_scheme = getattr(self, "_skill_burst_new_scheme_1_enabled", None)
        if callable(new_scheme) and new_scheme():
            if getattr(self, "_skill_burst_disable_new_scheme_1_refinement", False):
                return 0, 0, 0.0
            # Refine the independently generated timeline only.  This never
            # builds or compares the legacy axis, and each proposal is accepted
            # by a complete state replay.  Proxy timelines used by the extreme
            # team search stay lightweight; the selected/full visible axis can
            # be refined separately without multiplying team-search cost.
            if (
                getattr(self, "_skill_burst_complete_default_rotation", False)
                and not getattr(self, "_skill_burst_fast_mechanism_refinement", False)
            ):
                return 0, 0, 0.0
            if window >= 120:
                return (
                    max(
                        0,
                        int(getattr(self, "_skill_burst_new_scheme_1_refinement_passes", 2)),
                    ),
                    max(
                        0,
                        int(getattr(self, "_skill_burst_new_scheme_1_refinement_evaluations", 8)),
                    ),
                    max(
                        0.0,
                        as_number(
                            getattr(self, "_skill_burst_new_scheme_1_refinement_seconds", 0.25)
                        ),
                    ),
                )
            return 1, 8, 0.12
        if getattr(self, "_skill_burst_disable_local_optimizer", False):
            return 0, 0, 0.0
        policy_1_6 = getattr(self, "_skill_burst_conservative_1_6_enabled", None)
        if callable(policy_1_6) and policy_1_6():
            # Scheme 1.6 already includes Scheme 3's exact-replay proposals.
            return 0, 0, 0.0
        forced = getattr(self, "_skill_burst_force_local_optimizer", None)
        if forced is not True:
            job = norm(self.second_job.currentText())
            if job not in SKILL_TIMELINE_LOCAL_OPTIMIZER_JOBS:
                return 0, 0, 0.0
            active_modes = frozenset(
                mode
                for mode in ("practical", "cocktail", "burst", "accel")
                if self._skill_burst_sim_enabled(mode)
            )
            if active_modes not in SKILL_TIMELINE_LOCAL_OPTIMIZER_VERIFIED_MODES.get(job, set()):
                return 0, 0, 0.0
        # Full eight-person optimization creates many proxy timelines. Keep the
        # same exact acceptance rule there, with a smaller bounded search.
        if getattr(self, "_skill_burst_complete_default_rotation", False):
            return 1, 10, 0.16
        if window >= 120:
            return 2, 24, 0.45
        return 2, 16, 0.30

    @staticmethod
    def _skill_burst_local_timeline_score(
        segments: list[dict[str, Any]],
        window: float,
    ) -> float:
        return sum(
            max(0.0, as_number(segment.get("effective_score")))
            for segment in segments
            if norm(segment.get("kind")) != "invalid"
            and as_number(segment.get("start")) < window - 1e-6
        )

    def _skill_burst_local_combo_blocks(self, sequence: list[str]) -> list[list[str]]:
        blocks: list[list[str]] = []
        for skill in sequence:
            skill_key = self._skill_burst_skill_key(skill)
            if not skill_key:
                continue
            if not blocks:
                blocks.append([skill_key])
                continue
            previous = self._skill_burst_skill_key(blocks[-1][-1])
            tied = (
                skill_key in self._skill_burst_required_followups(previous)
                or previous in self._skill_burst_required_predecessors(skill_key)
                or skill_key in self._skill_burst_forced_combo_followups(previous)
            )
            snapshot_rule = getattr(self, "_skill_burst_snapshot_rule", None)
            if callable(snapshot_rule):
                rule = snapshot_rule({"skill": skill_key})
                # A boost followed by the snapshot it feeds is one legal
                # setup block. Move a team amplifier before/after that block,
                # never into the snapshot capture window. No job names needed.
                tied = tied or f"__爆发buff__{previous}" in rule.get(
                    "refresh_markers", ()
                )
            if tied:
                blocks[-1].append(skill_key)
            else:
                blocks.append([skill_key])
        return blocks

    def _skill_burst_support_order_comparison(
        self,
        dynamic_segments: list[dict[str, Any]],
        baseline_segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        dynamic_window: float,
        baseline_window: float,
    ) -> dict[str, Any]:
        """Compare complete legal axes without trading away later damage casts."""
        dynamic_owner = self._skill_burst_local_timeline_score(dynamic_segments, dynamic_window)
        baseline_owner = self._skill_burst_local_timeline_score(baseline_segments, baseline_window)
        reference_dps = baseline_owner / max(1e-9, baseline_window)
        dynamic_joint, _, _ = self._skill_burst_joint_timeline_objective(
            dynamic_segments, support_actions, dynamic_window,
            reference_owner_dps=reference_dps,
        )
        baseline_joint, _, _ = self._skill_burst_joint_timeline_objective(
            baseline_segments, support_actions, baseline_window,
            reference_owner_dps=reference_dps,
        )

        def sequence(segments):
            return [self._skill_burst_skill_key(row.get("skill")) for row in segments
                    if norm(row.get("skill")) and not row.get("timeline_hidden")
                    and norm(row.get("kind")) != "invalid"]

        dynamic_keys = sequence(dynamic_segments)
        baseline_keys = sequence(baseline_segments)
        # Exact prefix reordering can slightly reduce the provider's own
        # damage while benefiting the team. A completely regenerated rotation
        # must still retain the owner's damage floor: background team estimates
        # cannot justify losing later high-value casts in an output score axis.
        same_tail = (
            abs(dynamic_window - baseline_window) <= 1e-6
            and sorted(dynamic_keys[:8]) == sorted(baseline_keys[:8])
            and dynamic_keys[8:] == baseline_keys[8:]
        )
        owner_safe = dynamic_owner + 1e-6 >= baseline_owner or same_tail
        if same_tail:
            use_dynamic = dynamic_joint + 1e-6 >= baseline_joint
        else:
            # These are different full rotations, not merely two orders of
            # the same opener. Keep the higher owner-output baseline first;
            # use background team gain only as a tie-breaker. This applies
            # symmetrically, so neither alternative loses a high-value tail.
            use_dynamic = (
                dynamic_owner > baseline_owner + 1e-6
                or abs(dynamic_owner - baseline_owner) <= 1e-6
                and dynamic_joint + 1e-6 >= baseline_joint
            )
        return {
            "use_dynamic": use_dynamic,
            "dynamic_joint_score": dynamic_joint,
            "stable_joint_score": baseline_joint,
            "same_rotation_tail": same_tail,
            "owner_floor_satisfied": owner_safe,
        }

    @staticmethod
    def _skill_burst_local_flatten_blocks(blocks: list[list[str]]) -> list[str]:
        return [skill for block in blocks for skill in block]

    def _skill_burst_continuous_support_timeline_valid(
        self,
        segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> bool:
        """Keep hard continuous-buff rules intact during exact replay."""

        visible = [
            segment
            for segment in segments
            if norm(segment.get("skill"))
            and not segment.get("timeline_hidden")
            and norm(segment.get("kind")) != "invalid"
        ]
        for action in support_actions:
            if not action.get("continuous_uptime"):
                continue
            skill = self._skill_burst_skill_key(action.get("skill"))
            duration = max(0.0, as_number(action.get("duration")))
            casts = sorted(
                (
                    segment for segment in visible
                    if self._skill_burst_skill_key(segment.get("skill")) == skill
                ),
                key=lambda segment: as_number(segment.get("start")),
            )
            if not skill or duration <= 0 or not casts:
                return False
            if (
                self._skill_burst_skill_key(visible[0].get("skill")) != skill
                or as_number(casts[0].get("start")) > 0.05
            ):
                return False
            starts = [as_number(segment.get("start")) for segment in casts]
            if any(
                later - earlier > duration + 1e-6
                for earlier, later in zip(starts, starts[1:])
            ):
                return False
            if starts[-1] + duration < window - 1e-6:
                return False
        return True

    def _skill_burst_local_replay(
        self,
        sequence: list[str],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        hidden_segments: list[dict[str, Any]],
        window: float,
    ) -> tuple[list[dict[str, Any]], int, float]:
        # The final continuous-support validator requires this skill to be
        # the first visible cast. An opener with any other first skill cannot
        # become valid by simulating its entire tail. This is a necessary
        # condition only; valid-looking proposals still receive the full
        # cooldown/state replay and the unchanged final validation below.
        for action in support_actions:
            if not action.get("continuous_uptime"):
                continue
            skill = self._skill_burst_skill_key(action.get("skill"))
            if (
                not skill
                or max(0.0, as_number(action.get("duration"))) <= 0
                or not sequence
                or self._skill_burst_skill_key(sequence[0]) != skill
            ):
                return [], 1, 0.0
        replayed, issues = self._skill_burst_replay_manual_sequence(
            sequence,
            candidates,
            support_actions,
            window,
            stop_on_issue=True,
        )
        invalid_count = sum(
            1 for segment in replayed if norm(segment.get("kind")) == "invalid"
        )
        if invalid_count or issues:
            return replayed, invalid_count + len(issues), 0.0
        if not self._skill_burst_continuous_support_timeline_valid(
            replayed,
            support_actions,
            window,
        ):
            return replayed, 1, 0.0
        replayed.extend(copy.deepcopy(hidden_segments))
        self._skill_burst_apply_periodic_total_damage_scores(
            replayed,
            support_actions,
            window,
        )
        self._skill_burst_attach_mixed_attack_damage(replayed)
        return replayed, 0, self._skill_burst_local_timeline_score(replayed, window)

    def _skill_burst_short_personal_burst_proposals(
        self,
        sequence: list[str],
        metric_by_skill: dict[str, dict[str, Any]],
        short_buff_skills: set[str],
        sequence_density: list[float] | None = None,
    ) -> list[tuple[float, list[str]]]:
        blocks = self._skill_burst_local_combo_blocks(sequence)
        if len(blocks) < 3:
            return []

        density_cursor = 0
        block_densities: list[float] = []
        for block in blocks:
            values = list((sequence_density or [])[density_cursor : density_cursor + len(block)])
            density_cursor += len(block)
            block_densities.append(
                sum(values) / len(values)
                if values
                else 0.0
            )

        def block_dpa(block: list[str], block_index: int) -> float:
            measured = block_densities[block_index]
            if measured > 0.0:
                return measured
            return sum(
                self._skill_burst_local_metric_dpa(metric_by_skill.get(skill, {}))
                for skill in block
            ) / max(1, len(block))

        dpas = [
            block_dpa(block, block_index)
            for block_index, block in enumerate(blocks)
        ]
        proposals: list[tuple[float, list[str]]] = []
        seen: set[tuple[str, ...]] = {tuple(sequence)}
        for source, block in enumerate(blocks):
            if len(block) != 1 or block[0] not in short_buff_skills:
                continue
            current_following = sum(dpas[source + 1 : source + 4])
            for target in range(
                max(0, source - 2),
                min(len(blocks), source + 5),
            ):
                if target in {source, source + 1}:
                    continue
                moved = [list(value) for value in blocks]
                selected = moved.pop(source)
                insert_at = target - 1 if target > source else target
                moved.insert(insert_at, selected)
                candidate = self._skill_burst_local_flatten_blocks(moved)
                key = tuple(candidate)
                if key in seen:
                    continue
                seen.add(key)
                candidate_following = sum(
                    dpas[index]
                    for index in range(
                        max(0, target),
                        min(len(dpas), max(0, target) + 3),
                    )
                    if index != source
                )
                # Exact replay is authoritative. This estimate only decides
                # which nearby candidates receive the small evaluation budget.
                priority = candidate_following - current_following
                priority -= abs(target - source) * max(0.01, dpas[source]) * 0.01
                if priority > 1e-9:
                    proposals.append((priority, candidate))
        proposals.sort(key=lambda row: row[0], reverse=True)
        return proposals

    def _skill_burst_improve_short_personal_burst_timeline(
        self,
        baseline_segments: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        selectable_candidates: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Align short self buffs, accepting only a higher full-axis replay."""
        started = time.monotonic()
        baseline_score = self._skill_burst_local_timeline_score(
            baseline_segments,
            window,
        )
        stats = {
            "enabled": False,
            "evaluations": 0,
            "accepted": 0,
            "baseline_score": baseline_score,
            "final_score": baseline_score,
            "gain": 0.0,
            "gain_percent": 0.0,
            "elapsed": 0.0,
        }
        self._skill_burst_short_personal_burst_last_quality = stats
        if (
            getattr(
                self,
                "_skill_burst_disable_short_personal_burst_scheduler",
                False,
            )
            or window <= 0
            or not candidates
        ):
            return baseline_segments
        short_actions = [
            action
            for action in support_actions
            if self._skill_burst_is_short_personal_burst_action(action)
        ]
        short_buff_skills = {
            self._skill_burst_skill_key(action.get("skill"))
            for action in short_actions
        }
        if not short_buff_skills:
            return baseline_segments
        visible_segments = [
            segment
            for segment in baseline_segments
            if norm(segment.get("skill"))
            and not segment.get("timeline_hidden")
            and norm(segment.get("kind")) != "invalid"
        ]
        sequence = [
            self._skill_burst_skill_key(segment.get("skill"))
            for segment in visible_segments
        ]
        sequence_density = [
            max(0.0, as_number(segment.get("effective_score")))
            / max(
                0.05,
                as_number(segment.get("end")) - as_number(segment.get("start")),
            )
            for segment in visible_segments
        ]
        if not any(skill in short_buff_skills for skill in sequence):
            return baseline_segments
        stats["enabled"] = True
        metric_by_skill = {
            self._skill_burst_skill_key(metric.get("skill")): metric
            for metric in candidates
        }
        hidden_segments = [
            copy.deepcopy(segment)
            for segment in baseline_segments
            if segment.get("timeline_hidden")
        ]
        fast_axis = bool(
            getattr(self, "_skill_burst_fast_mechanism_policy", False)
            or getattr(self, "_skill_burst_complete_default_rotation", False)
        )
        passes = 1
        max_evaluations = 2 if fast_axis or window >= 300.0 else 4
        current_segments = baseline_segments
        current_score = baseline_score
        score_tolerance = max(0.01, abs(baseline_score) * 1e-7)
        evaluated: set[tuple[str, ...]] = {tuple(sequence)}

        for _pass in range(passes):
            proposals = self._skill_burst_short_personal_burst_proposals(
                sequence,
                metric_by_skill,
                short_buff_skills,
                sequence_density,
            )
            round_best: tuple[list[str], list[dict[str, Any]], float] | None = None
            for _priority, candidate_sequence in proposals:
                if stats["evaluations"] >= max_evaluations:
                    break
                key = tuple(candidate_sequence)
                if key in evaluated:
                    continue
                evaluated.add(key)
                stats["evaluations"] += 1
                replayed, invalid_count, candidate_score = self._skill_burst_local_replay(
                    candidate_sequence,
                    candidates,
                    support_actions,
                    hidden_segments,
                    window,
                )
                if invalid_count or candidate_score <= current_score + score_tolerance:
                    continue
                if round_best is None or candidate_score > round_best[2]:
                    round_best = (
                        candidate_sequence,
                        replayed,
                        candidate_score,
                    )
            if round_best is None:
                break
            sequence, current_segments, current_score = round_best
            current_visible = [
                segment
                for segment in current_segments
                if norm(segment.get("skill"))
                and not segment.get("timeline_hidden")
                and norm(segment.get("kind")) != "invalid"
            ]
            sequence_density = [
                max(0.0, as_number(segment.get("effective_score")))
                / max(
                    0.05,
                    as_number(segment.get("end")) - as_number(segment.get("start")),
                )
                for segment in current_visible
            ]
            stats["accepted"] += 1

        stats["final_score"] = current_score
        stats["gain"] = max(0.0, current_score - baseline_score)
        stats["gain_percent"] = (
            stats["gain"] / baseline_score * 100.0
            if baseline_score > 1e-9
            else 0.0
        )
        stats["elapsed"] = time.monotonic() - started
        self._skill_burst_short_personal_burst_last_quality = stats
        return current_segments

    def _skill_burst_improve_opening_periodic_timeline(
        self,
        baseline_segments: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> list[dict[str, Any]]:
        """Move a first periodic cast earlier only after exact full-axis replay."""

        baseline_score = self._skill_burst_local_timeline_score(
            baseline_segments,
            window,
        )
        stats = {
            "enabled": False,
            "evaluations": 0,
            "accepted": 0,
            "baseline_score": baseline_score,
            "final_score": baseline_score,
            "gain": 0.0,
            "gain_percent": 0.0,
        }
        self._skill_burst_opening_periodic_last_quality = stats
        if (
            getattr(
                self,
                "_skill_burst_disable_opening_periodic_optimizer",
                False,
            )
            or window <= 0
            or not candidates
        ):
            return baseline_segments

        visible = [
            segment
            for segment in baseline_segments
            if norm(segment.get("skill"))
            and not segment.get("timeline_hidden")
            and norm(segment.get("kind")) != "invalid"
        ]
        sequence = [
            self._skill_burst_skill_key(segment.get("skill"))
            for segment in visible
        ]
        source = next(
            (
                index
                for index, skill in enumerate(sequence)
                if self._skill_burst_is_refreshable_periodic_damage(skill)
            ),
            -1,
        )
        if source <= 1:
            return baseline_segments

        stats["enabled"] = True
        hidden = [
            copy.deepcopy(segment)
            for segment in baseline_segments
            if segment.get("timeline_hidden")
        ]
        best_segments = baseline_segments
        best_score = baseline_score
        best_sequence = sequence
        tolerance = max(0.01, abs(baseline_score) * 1e-7)
        # Four preceding actions cover the usual opener while keeping the
        # candidate set bounded.
        for target in range(source - 1, max(-1, source - 5), -1):
            proposal = list(sequence)
            periodic_skill = proposal.pop(source)
            proposal.insert(target, periodic_skill)
            stats["evaluations"] += 1
            replayed, invalid_count, score = self._skill_burst_local_replay(
                proposal,
                candidates,
                support_actions,
                hidden,
                window,
            )
            if invalid_count or score <= best_score + tolerance:
                continue
            best_segments = replayed
            best_score = score
            best_sequence = proposal

        # The move can change the best order of the following setup actions.
        # Exact replay remains the sole acceptance authority.
        for _pass in range(2):
            periodic_index = best_sequence.index(sequence[source])
            local_end = min(len(best_sequence), periodic_index + 5)
            round_best: tuple[list[str], list[dict[str, Any]], float] | None = None
            for index in range(periodic_index + 1, local_end - 1):
                proposal = list(best_sequence)
                proposal[index], proposal[index + 1] = proposal[index + 1], proposal[index]
                stats["evaluations"] += 1
                replayed, invalid_count, score = self._skill_burst_local_replay(
                    proposal,
                    candidates,
                    support_actions,
                    hidden,
                    window,
                )
                if invalid_count or score <= best_score + tolerance:
                    continue
                if round_best is None or score > round_best[2]:
                    round_best = (proposal, replayed, score)
            if round_best is None:
                break
            best_sequence, best_segments, best_score = round_best

        if best_score > baseline_score + tolerance:
            stats["accepted"] = 1
            stats["accepted_sequence"] = best_sequence
        stats["final_score"] = best_score
        stats["gain"] = max(0.0, best_score - baseline_score)
        stats["gain_percent"] = (
            stats["gain"] / baseline_score * 100.0
            if baseline_score > 1e-9
            else 0.0
        )
        self._skill_burst_opening_periodic_last_quality = stats
        return best_segments

    def _skill_burst_improve_joint_support_order(
        self,
        baseline_segments: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> list[dict[str, Any]]:
        """Locally reorder early team effects using an exact full-axis replay.

        Only complete combo/state blocks are moved.  The tail sequence remains
        unchanged, so a better Buff/Debuff opener cannot accidentally replace
        a later high-value cast as happened when the whole greedy axis was
        regenerated from a different first action.
        """

        profile = getattr(self, "_skill_burst_team_objective_profile", None)
        baseline_owner = self._skill_burst_local_timeline_score(
            baseline_segments,
            window,
        )
        stats = {
            "enabled": False,
            "evaluations": 0,
            "accepted": 0,
            "baseline_owner_score": baseline_owner,
            "final_owner_score": baseline_owner,
            "baseline_joint_score": baseline_owner,
            "final_joint_score": baseline_owner,
            "repeated_proposals": 0,
            "replay_cache_hits": 0,
            "objective_cache_hits": 0,
        }
        self._skill_burst_joint_support_order_last_quality = stats
        if (
            getattr(
                self,
                "_skill_burst_disable_joint_support_order",
                False,
            )
            or window <= 0
            or not candidates
            or not isinstance(profile, dict)
            or not any(
                isinstance(member, dict)
                and as_number(member.get("weight")) > 1e-9
                for member in profile.get("members", ())
            )
        ):
            return baseline_segments

        visible = [
            segment
            for segment in baseline_segments
            if norm(segment.get("skill"))
            and not segment.get("timeline_hidden")
            and norm(segment.get("kind")) != "invalid"
        ]
        sequence = [
            self._skill_burst_skill_key(segment.get("skill"))
            for segment in visible
        ]
        blocks = self._skill_burst_local_combo_blocks(sequence)
        if len(blocks) < 2:
            return baseline_segments
        team_support = {
            self._skill_burst_skill_key(action.get("skill"))
            for action in support_actions
            if action.get("team_objective_effect")
            and self._skill_burst_skill_key(action.get("skill"))
        }
        opening_limit = min(len(blocks), 8)
        movable = [
            index
            for index, block in enumerate(blocks[:opening_limit])
            if any(skill in team_support for skill in block)
        ]
        if not movable:
            return baseline_segments

        hidden = [
            copy.deepcopy(segment)
            for segment in baseline_segments
            if segment.get("timeline_hidden")
        ]
        reference_owner_dps = baseline_owner / max(1e-9, as_number(window))
        baseline_joint, _owner, _units = self._skill_burst_joint_timeline_objective(
            baseline_segments,
            support_actions,
            window,
            reference_owner_dps=reference_owner_dps,
        )
        stats.update({
            "enabled": True,
            "baseline_joint_score": baseline_joint,
            "final_joint_score": baseline_joint,
        })
        best_blocks = blocks
        best_segments = baseline_segments
        best_joint = baseline_joint
        best_owner = baseline_owner
        tolerance = max(0.01, abs(baseline_joint) * 1e-7)
        proposal_history = getattr(
            self,
            "_skill_burst_joint_support_proposal_history",
            None,
        )
        if not isinstance(proposal_history, set):
            proposal_history = set()
            self._skill_burst_joint_support_proposal_history = proposal_history
        replay_cache = getattr(
            self,
            "_skill_burst_joint_support_replay_cache",
            None,
        )
        if not isinstance(replay_cache, dict):
            replay_cache = {}
            self._skill_burst_joint_support_replay_cache = replay_cache
        objective_cache = getattr(
            self,
            "_skill_burst_joint_support_objective_cache",
            None,
        )
        if not isinstance(objective_cache, dict):
            objective_cache = {}
            self._skill_burst_joint_support_objective_cache = objective_cache
        freeze = getattr(self, "_skill_burst_new_scheme_1_freeze", None)
        replay_context = (
            freeze((candidates, support_actions, hidden, window))
            if callable(freeze)
            else (
                id(candidates),
                id(support_actions),
                id(hidden),
                round(max(0.0, as_number(window)), 9),
            )
        )

        def exact_replay(
            proposal: list[str],
        ) -> tuple[list[dict[str, Any]], int, float]:
            cache_key = (replay_context, tuple(proposal))
            cached = replay_cache.get(cache_key)
            if cached is not None:
                stats["replay_cache_hits"] += 1
                return cached
            value = self._skill_burst_local_replay(
                proposal,
                candidates,
                support_actions,
                hidden,
                window,
            )
            replay_cache[cache_key] = value
            while len(replay_cache) > 256:
                replay_cache.pop(next(iter(replay_cache)))
            return value

        def exact_objective(
            proposal: list[str],
            replayed: list[dict[str, Any]],
        ) -> tuple[float, float, float]:
            cache_key = (
                replay_context,
                tuple(proposal),
                float(reference_owner_dps),
            )
            cached = objective_cache.get(cache_key)
            if cached is not None:
                stats["objective_cache_hits"] += 1
                return cached
            value = self._skill_burst_joint_timeline_objective(
                replayed,
                support_actions,
                window,
                reference_owner_dps=reference_owner_dps,
            )
            objective_cache[cache_key] = value
            while len(objective_cache) > 512:
                objective_cache.pop(next(iter(objective_cache)))
            return value

        # Two bounded passes allow A->B->C to become C->A->B without testing
        # every permutation of a long rotation.
        for _pass in range(2):
            round_best: tuple[
                list[list[str]], list[dict[str, Any]], float, float
            ] | None = None
            current_limit = min(len(best_blocks), 8)
            for source in range(1, current_limit):
                block = best_blocks[source]
                if not any(skill in team_support for skill in block):
                    continue
                # Opening effects only need to cross the nearby setup blocks;
                # a five-block bound keeps all-profession scoring predictable.
                for target in range(max(0, source - 5), source):
                    proposal_blocks = [list(row) for row in best_blocks]
                    moved = proposal_blocks.pop(source)
                    proposal_blocks.insert(target, moved)
                    proposal = self._skill_burst_local_flatten_blocks(
                        proposal_blocks
                    )
                    proposal_key = tuple(proposal)
                    if proposal_key in proposal_history:
                        stats["repeated_proposals"] += 1
                    else:
                        proposal_history.add(proposal_key)
                    stats["evaluations"] += 1
                    replayed, invalid_count, _personal_score = exact_replay(
                        proposal
                    )
                    if invalid_count:
                        continue
                    joint, owner, _team_units = exact_objective(
                        proposal,
                        replayed,
                    )
                    if joint <= best_joint + tolerance:
                        continue
                    if round_best is None or joint > round_best[2]:
                        round_best = (
                            proposal_blocks,
                            replayed,
                            joint,
                            owner,
                        )
            if round_best is None:
                break
            best_blocks, best_segments, best_joint, best_owner = round_best
            stats["accepted"] += 1

        stats.update({
            "final_owner_score": best_owner,
            "final_joint_score": best_joint,
            "joint_gain": max(0.0, best_joint - baseline_joint),
            "owner_delta": best_owner - baseline_owner,
            "opening": self._skill_burst_local_flatten_blocks(best_blocks)[:8],
        })
        self._skill_burst_joint_support_order_last_quality = stats
        return best_segments

    def _skill_burst_local_boundary_priority(
        self,
        left: list[str],
        right: list[str],
        metric_by_skill: dict[str, dict[str, Any]],
        support_skills: set[str],
    ) -> float:
        def block_dpa(block: list[str]) -> float:
            values = [
                self._skill_burst_local_metric_dpa(metric_by_skill.get(skill, {}))
                for skill in block
            ]
            return sum(values) / max(1, len(values))

        left_support = any(skill in support_skills for skill in left)
        right_support = any(skill in support_skills for skill in right)
        priority = block_dpa(right) - block_dpa(left)
        if right_support and not left_support:
            priority += max(1.0, abs(priority)) * 0.35
        if left_support and not right_support:
            priority -= max(1.0, abs(priority)) * 0.50
        return priority

    @staticmethod
    def _skill_burst_local_metric_dpa(metric: dict[str, Any]) -> float:
        cast_time = max(0.0, as_number(metric.get("cast_time")))
        if cast_time <= 0:
            return 0.0
        return max(0.0, as_number(metric.get("effective_score"))) / cast_time

    def _skill_burst_local_proposals(
        self,
        blocks: list[list[str]],
        metric_by_skill: dict[str, dict[str, Any]],
        support_skills: set[str],
        selectable_candidates: list[dict[str, Any]],
        window: float,
    ) -> list[tuple[float, str, list[str]]]:
        proposals: list[tuple[float, str, list[str]]] = []

        def timeline_priority(value: float, position: int) -> float:
            factor = 1.0 + max(0, position) * 0.04
            return value / factor if value >= 0 else value * factor

        for boundary in range(len(blocks) - 1):
            swapped = [list(block) for block in blocks]
            swapped[boundary], swapped[boundary + 1] = swapped[boundary + 1], swapped[boundary]
            proposals.append((
                timeline_priority(
                    self._skill_burst_local_boundary_priority(
                        blocks[boundary],
                        blocks[boundary + 1],
                        metric_by_skill,
                        support_skills,
                    ),
                    boundary,
                ),
                "swap",
                self._skill_burst_local_flatten_blocks(swapped),
            ))

        # Moving a strong block across two or three neighbors catches short
        # buff-window opportunities that adjacent swaps alone cannot reach.
        block_dpa = [
            sum(self._skill_burst_local_metric_dpa(metric_by_skill.get(skill, {})) for skill in block)
            / max(1, len(block))
            for block in blocks
        ]
        for source in range(1, len(blocks)):
            if any(skill in support_skills for skill in blocks[source]):
                continue
            for target in range(max(0, source - 3), source - 1):
                moved = [list(block) for block in blocks]
                block = moved.pop(source)
                moved.insert(target, block)
                crossed = block_dpa[target:source]
                priority = block_dpa[source] - (sum(crossed) / max(1, len(crossed)))
                proposals.append((
                    timeline_priority(priority, target),
                    "move",
                    self._skill_burst_local_flatten_blocks(moved),
                ))

        # Replacement proposals let an omitted high-DPA ready skill displace a
        # weak filler. Strict predecessor/follow-up skills stay block-only.
        replacement_skills = []
        for metric in sorted(
            selectable_candidates,
            key=self._skill_burst_local_metric_dpa,
            reverse=True,
        ):
            skill = self._skill_burst_skill_key(metric.get("skill"))
            if (
                not skill
                or skill in support_skills
                or self._skill_burst_required_predecessors(skill)
                or self._skill_burst_required_followups(skill)
                or self._skill_burst_local_metric_dpa(metric) <= 0
            ):
                continue
            if skill not in replacement_skills:
                replacement_skills.append(skill)
            if len(replacement_skills) >= 5:
                break
        weak_indexes = sorted(
            (
                index for index, block in enumerate(blocks)
                if len(block) == 1 and block[0] not in support_skills
            ),
            key=lambda index: block_dpa[index],
        )[:6]
        for index in weak_indexes:
            for skill in replacement_skills:
                if skill == blocks[index][0]:
                    continue
                replaced = [list(block) for block in blocks]
                replaced[index] = [skill]
                priority = self._skill_burst_local_metric_dpa(metric_by_skill.get(skill, {})) - block_dpa[index]
                proposals.append((
                    timeline_priority(priority, index),
                    "replace",
                    self._skill_burst_local_flatten_blocks(replaced),
                ))

        # A receding-horizon planner can finish with one fewer cast of a strong
        # cooldown skill even though no individual adjacent swap improves the
        # fixed sequence.  Propose one evenly placed extra cast and let exact
        # replay decide whether the complete cooldown/resource/buff state makes
        # it profitable.  This uses only the independently generated timeline;
        # no legacy schedule or rank is consulted.
        occurrence_indexes: dict[str, list[int]] = defaultdict(list)
        for block_index, block in enumerate(blocks):
            for skill in block:
                occurrence_indexes[skill].append(block_index)
        active_window = max(
            0.0,
            self._skill_burst_active_duration(0.0, max(0.0, as_number(window))),
        )
        insertion_candidates: list[tuple[float, str]] = []
        for metric in selectable_candidates:
            skill = self._skill_burst_skill_key(metric.get("skill"))
            cast_time = max(0.0, as_number(metric.get("cast_time")))
            score = max(0.0, as_number(metric.get("effective_score")))
            cooldown = max(cast_time, as_number(metric.get("base_cooldown")))
            occurrences = occurrence_indexes.get(skill, [])
            if (
                not skill
                or skill in support_skills
                or not occurrences
                or cast_time <= 0
                or score <= 0
                or cooldown <= cast_time * 1.05 + 1e-9
                or self._skill_burst_required_predecessors(skill)
                or self._skill_burst_required_followups(skill)
            ):
                continue
            independent_limit = max(1, int(active_window // cooldown) + 1)
            if len(occurrences) >= independent_limit:
                continue
            cycle_value = score / max(cast_time, cooldown)
            insertion_candidates.append((
                self._skill_burst_local_metric_dpa(metric) + cycle_value,
                skill,
            ))
        insertion_candidates.sort(reverse=True)
        for insertion_priority, skill in insertion_candidates[:8]:
            positions = occurrence_indexes.get(skill, [])
            boundaries = [-1, *positions, len(blocks)]
            left, right = max(
                zip(boundaries, boundaries[1:]),
                key=lambda row: row[1] - row[0],
            )
            insert_at = max(0, min(len(blocks), (left + right + 1) // 2))
            inserted = [list(block) for block in blocks]
            inserted.insert(insert_at, [skill])
            proposals.append((
                timeline_priority(insertion_priority, insert_at) * 1000.0,
                "insert",
                self._skill_burst_local_flatten_blocks(inserted),
            ))
        proposals.sort(key=lambda row: row[0], reverse=True)
        return proposals

    def _skill_burst_improve_baseline_timeline(
        self,
        baseline_segments: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        selectable_candidates: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        passes, max_evaluations, time_budget = self._skill_burst_local_optimizer_profile(window)
        started = time.monotonic()
        stats = {
            "job": norm(self.second_job.currentText()),
            "enabled": bool(passes and max_evaluations and time_budget > 0),
            "evaluations": 0,
            "accepted": 0,
            "accepted_moves": [],
            "budget_policy": "fixed_evaluations",
            "max_evaluations": max_evaluations,
            "baseline_score": self._skill_burst_local_timeline_score(baseline_segments, window),
            "final_score": self._skill_burst_local_timeline_score(baseline_segments, window),
            "elapsed": 0.0,
        }
        self._skill_burst_local_optimizer_last_stats = stats
        if not stats["enabled"] or not candidates or window <= 0:
            return baseline_segments

        visible_segments = [
            segment
            for segment in baseline_segments
            if norm(segment.get("skill"))
            and not segment.get("timeline_hidden")
            and norm(segment.get("kind")) != "invalid"
        ]
        sequence = [self._skill_burst_skill_key(segment.get("skill")) for segment in visible_segments]
        if len(sequence) < 3:
            return baseline_segments

        active_modes = tuple(
            mode
            for mode in ("practical", "cocktail", "burst", "accel")
            if self._skill_burst_sim_enabled(mode)
        )
        candidate_signature = tuple(
            (
                self._skill_burst_skill_key(metric.get("skill")),
                round(as_number(metric.get("effective_score")), 5),
                round(as_number(metric.get("base_cooldown")), 5),
                round(as_number(metric.get("cast_time")), 5),
            )
            for metric in candidates
        )
        support_signature = tuple(
            (
                self._skill_burst_skill_key(action.get("skill")),
                round(as_number(action.get("base_cooldown")), 5),
                round(as_number(action.get("duration")), 5),
                repr(action.get("attrs", {})),
            )
            for action in support_actions
        )
        cache_key = (
            "fixed_evaluations_v1", passes, max_evaluations,
            norm(self.second_job.currentText()),
            active_modes,
            round(window, 4),
            tuple(sequence),
            round(as_number(stats["baseline_score"]), 5),
            candidate_signature,
            support_signature,
            repr(getattr(self, "_skill_burst_external_team_effect_windows", [])),
        )
        cache = getattr(self, "_skill_burst_local_optimizer_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_local_optimizer_cache = cache
        cached = cache.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2:
            cached_segments, cached_stats = cached
            stats.update(copy.deepcopy(cached_stats))
            stats["cached"] = True
            stats["elapsed"] = time.monotonic() - started
            self._skill_burst_local_optimizer_last_stats = stats
            return copy.deepcopy(cached_segments)

        hidden_segments = [
            copy.deepcopy(segment)
            for segment in baseline_segments
            if segment.get("timeline_hidden")
        ]
        metric_by_skill = {
            self._skill_burst_skill_key(metric.get("skill")): metric
            for metric in candidates
        }
        support_skills = {
            self._skill_burst_skill_key(action.get("skill"))
            for action in support_actions
        }
        selectable_candidates = list(selectable_candidates or candidates)
        current_segments = baseline_segments
        current_score = as_number(stats["baseline_score"])
        score_tolerance = max(0.01, abs(current_score) * 1e-7)
        evaluated_sequences: set[tuple[str, ...]] = {tuple(sequence)}

        for _pass in range(passes):
            # A wall-clock cutoff makes the score depend on CPU load/cache
            # warmth. Preserve the same bounded proposal budget and order;
            # elapsed time is diagnostic only. The profile's legacy time
            # value is retained only as its existing enable/disable switch.
            if stats["evaluations"] >= max_evaluations:
                break
            blocks = self._skill_burst_local_combo_blocks(sequence)
            if len(blocks) < 2:
                break
            improved_this_pass = False
            for _priority, move_type, candidate_sequence in self._skill_burst_local_proposals(
                blocks,
                metric_by_skill,
                support_skills,
                selectable_candidates,
                window,
            ):
                if stats["evaluations"] >= max_evaluations:
                    break
                key = tuple(candidate_sequence)
                if key in evaluated_sequences:
                    continue
                evaluated_sequences.add(key)
                stats["evaluations"] += 1
                replayed, invalid_count, candidate_score = self._skill_burst_local_replay(
                    candidate_sequence,
                    candidates,
                    support_actions,
                    hidden_segments,
                    window,
                )
                if invalid_count or candidate_score <= current_score + score_tolerance:
                    continue
                sequence = candidate_sequence
                current_segments = replayed
                current_score = candidate_score
                stats["accepted"] += 1
                stats["accepted_moves"].append(move_type)
                improved_this_pass = True
                break
            if not improved_this_pass:
                break

        stats["final_score"] = current_score
        stats["gain"] = current_score - as_number(stats["baseline_score"])
        stats["gain_percent"] = (
            stats["gain"] / as_number(stats["baseline_score"]) * 100
            if as_number(stats["baseline_score"]) > 0
            else 0.0
        )
        stats["elapsed"] = time.monotonic() - started
        self._skill_burst_local_optimizer_last_stats = stats
        cache[cache_key] = (copy.deepcopy(current_segments), copy.deepcopy(stats))
        while len(cache) > 16:
            cache.pop(next(iter(cache)))
        return current_segments
