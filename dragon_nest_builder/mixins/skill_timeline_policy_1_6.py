from __future__ import annotations

from ..shared import *


# Verified against Scheme 1, Scheme 3 and Scheme 1.5 on long, burst and
# eight-person timelines.  Every accepted change still has a Scheme 1 floor.
SKILL_TIMELINE_CONSERVATIVE_1_6_JOBS = {
    "圣骑士",
    "十字军",
    "噬魂者",
    "黑暗萨满",
    "刀锋舞者",
    "灵魂舞者",
    "剑皇",
}


class SkillTimelinePolicy16Mixin:
    """Exact-replay support-window improvement with Scheme 1 as its floor.

    Scheme 1.5 improves damage-package ranking heuristically and can very
    occasionally choose a worse complete rotation.  Scheme 1.6 instead starts
    from Scheme 1, combines Scheme 3's damage proposals with earlier placements
    for ready buffs/debuffs, and accepts a proposal only when replaying the
    complete visible sequence produces strictly more output.
    """

    def _skill_burst_conservative_1_6_enabled(self) -> bool:
        new_scheme = getattr(self, "_skill_burst_new_scheme_1_enabled", None)
        if callable(new_scheme) and new_scheme():
            return False
        if getattr(self, "_skill_burst_disable_conservative_1_6", False):
            return False
        forced = getattr(self, "_skill_burst_force_conservative_1_6", None)
        if forced is not None:
            return bool(forced)
        return norm(self.second_job.currentText()) in SKILL_TIMELINE_CONSERVATIVE_1_6_JOBS

    def _skill_burst_conservative_1_6_profile(
        self,
        window: float,
    ) -> tuple[int, int, float]:
        if not self._skill_burst_conservative_1_6_enabled():
            return 0, 0, 0.0
        if getattr(self, "_skill_burst_complete_default_rotation", False):
            return 1, 12, 0.24
        if window >= 120:
            return 2, 48, 1.20
        return 2, 48, 0.82

    @staticmethod
    def _skill_burst_conservative_1_6_support_weight(
        action: dict[str, Any],
    ) -> float:
        duration = max(0.0, as_number(action.get("duration")))
        attrs = action.get("attrs", {})
        attr_weight = (
            sum(abs(as_number(value)) for value in attrs.values())
            if isinstance(attrs, dict)
            else 0.0
        )
        multiplier = max(
            0.0,
            as_number(action.get("active_multiplier"), 1.0) - 1.0,
        )
        return duration * (1.0 + attr_weight / 100.0 + multiplier)

    def _skill_burst_conservative_1_6_proposals(
        self,
        blocks: list[list[str]],
        support_actions: list[dict[str, Any]],
    ) -> list[tuple[float, str, list[str]]]:
        if not blocks or not support_actions:
            return []
        support_by_skill = {
            self._skill_burst_skill_key(action.get("skill")): action
            for action in support_actions
            if self._skill_burst_skill_key(action.get("skill"))
            and not action.get("triggered_only")
        }
        support_skills = set(support_by_skill)
        proposals: list[tuple[float, str, list[str]]] = []
        present_skills = {
            self._skill_burst_skill_key(skill)
            for block in blocks
            for skill in block
        }

        for source, block in enumerate(blocks):
            block_support = next(
                (
                    skill
                    for skill in block
                    if self._skill_burst_skill_key(skill) in support_skills
                ),
                "",
            )
            if not block_support or source <= 0:
                continue
            action = support_by_skill[self._skill_burst_skill_key(block_support)]
            weight = self._skill_burst_conservative_1_6_support_weight(action)
            targets = set(range(max(0, source - 4), source))
            if source <= 7:
                targets.add(0)
            for target in targets:
                if target == source:
                    continue
                moved = [list(value) for value in blocks]
                moved_block = moved.pop(source)
                moved.insert(target, moved_block)
                distance = source - target
                opening_bonus = 2.0 if target == 0 else 1.0
                proposals.append((
                    weight * distance * opening_bonus,
                    "support_move",
                    self._skill_burst_local_flatten_blocks(moved),
                ))

        # A useful support action can be omitted entirely by the old one-step
        # threshold.  Exact replay makes a bounded insertion attempt safe.
        missing_actions = [
            action
            for skill, action in support_by_skill.items()
            if skill not in present_skills
            and self._skill_burst_support_cast_time(action, 0.0, {}) > 0
        ]
        missing_actions.sort(
            key=self._skill_burst_conservative_1_6_support_weight,
            reverse=True,
        )
        for action in missing_actions[:3]:
            skill = self._skill_burst_skill_key(action.get("skill"))
            weight = self._skill_burst_conservative_1_6_support_weight(action)
            for target in range(min(3, len(blocks) + 1)):
                inserted = [list(value) for value in blocks]
                inserted.insert(target, [skill])
                proposals.append((
                    weight * (3 - target),
                    "support_insert",
                    self._skill_burst_local_flatten_blocks(inserted),
                ))

        proposals.sort(key=lambda row: row[0], reverse=True)
        return proposals

    def _skill_burst_improve_conservative_1_6_timeline(
        self,
        baseline_segments: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> list[dict[str, Any]]:
        passes, max_evaluations, time_budget = self._skill_burst_conservative_1_6_profile(
            window
        )
        started = time.monotonic()
        baseline_score = self._skill_burst_local_timeline_score(
            baseline_segments,
            window,
        )
        stats = {
            "job": norm(self.second_job.currentText()),
            "enabled": bool(passes and max_evaluations and time_budget > 0),
            "evaluations": 0,
            "accepted": 0,
            "accepted_moves": [],
            "budget_policy": "fixed_evaluations",
            "max_evaluations": max_evaluations,
            "baseline_score": baseline_score,
            "final_score": baseline_score,
            "elapsed": 0.0,
        }
        self._skill_burst_conservative_1_6_last_stats = stats
        if not stats["enabled"] or not candidates or not support_actions or window <= 0:
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
        if len(sequence) < 2:
            return baseline_segments
        hidden_segments = [
            copy.deepcopy(segment)
            for segment in baseline_segments
            if segment.get("timeline_hidden")
        ]

        cache_key = (
            "fixed_evaluations_v1", passes, max_evaluations,
            norm(self.second_job.currentText()),
            tuple(
                mode
                for mode in ("practical", "cocktail", "burst", "accel")
                if self._skill_burst_sim_enabled(mode)
            ),
            round(window, 4),
            tuple(sequence),
            round(baseline_score, 5),
            tuple(
                (
                    self._skill_burst_skill_key(action.get("skill")),
                    round(as_number(action.get("base_cooldown")), 4),
                    round(as_number(action.get("duration")), 4),
                    repr(action.get("attrs", {})),
                )
                for action in support_actions
            ),
            repr(getattr(self, "_skill_burst_external_team_effect_windows", [])),
        )
        cache = getattr(self, "_skill_burst_conservative_1_6_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_conservative_1_6_cache = cache
        cached = cache.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2:
            cached_segments, cached_stats = cached
            stats.update(copy.deepcopy(cached_stats))
            stats["cached"] = True
            stats["elapsed"] = time.monotonic() - started
            self._skill_burst_conservative_1_6_last_stats = stats
            return copy.deepcopy(cached_segments)

        current_segments = baseline_segments
        current_score = baseline_score
        tolerance = max(0.01, abs(current_score) * 1e-7)
        evaluated: set[tuple[str, ...]] = {tuple(sequence)}
        metric_by_skill = {
            self._skill_burst_skill_key(metric.get("skill")): metric
            for metric in candidates
        }
        support_skills = {
            self._skill_burst_skill_key(action.get("skill"))
            for action in support_actions
        }

        for _pass in range(passes):
            # Match the main local optimizer: bounded candidate work, never
            # machine-load-dependent acceptance caused by an elapsed cutoff.
            if stats["evaluations"] >= max_evaluations:
                break
            blocks = self._skill_burst_local_combo_blocks(sequence)
            proposals = self._skill_burst_conservative_1_6_proposals(
                blocks,
                support_actions,
            )
            local_proposals = self._skill_burst_local_proposals(
                blocks,
                metric_by_skill,
                support_skills,
                candidates,
                window,
            )
            per_pass_limit = max(2, max_evaluations // max(1, passes))
            support_limit = min(8, max(1, per_pass_limit // 3))
            local_limit = max(1, per_pass_limit - support_limit)
            proposals = [
                *proposals[:support_limit],
                *local_proposals[:local_limit],
            ]
            proposals.sort(key=lambda row: row[0], reverse=True)
            best_result: tuple[list[str], list[dict[str, Any]], float, str] | None = None
            for _priority, move_type, candidate_sequence in proposals:
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
                if invalid_count or candidate_score <= current_score + tolerance:
                    continue
                if best_result is None or candidate_score > best_result[2]:
                    best_result = (
                        candidate_sequence,
                        replayed,
                        candidate_score,
                        move_type,
                    )
            if best_result is None:
                break
            sequence, current_segments, current_score, move_type = best_result
            stats["accepted"] += 1
            stats["accepted_moves"].append(move_type)

        stats["final_score"] = current_score
        stats["gain"] = current_score - baseline_score
        stats["gain_percent"] = (
            stats["gain"] / baseline_score * 100
            if baseline_score > 0
            else 0.0
        )
        stats["elapsed"] = time.monotonic() - started
        self._skill_burst_conservative_1_6_last_stats = stats
        cache[cache_key] = (copy.deepcopy(current_segments), copy.deepcopy(stats))
        # Timelines are sizeable; a short per-editor LRU-style history keeps
        # repeated UI refreshes fast without retaining dozens of deep copies.
        while len(cache) > 4:
            cache.pop(next(iter(cache)))
        return current_segments
