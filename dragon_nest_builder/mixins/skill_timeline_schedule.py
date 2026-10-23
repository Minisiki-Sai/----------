from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
from ..data_loader import as_number

class SkillTimelineScheduleMixin:
    def _skill_burst_blocked_ranges(self) -> tuple[tuple[float, float], ...]:
        rows = getattr(self, "_skill_burst_blocked_intervals", [])
        source_rows = rows if isinstance(rows, (list, tuple)) else []

        def edge_value(row: Any) -> tuple[float, float] | None:
            if isinstance(row, dict):
                start = max(0.0, as_number(row.get("start")))
                return start, max(start, as_number(row.get("end")))
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                start = max(0.0, as_number(row[0]))
                return start, max(start, as_number(row[1]))
            return None

        source_key = (
            id(rows),
            len(source_rows),
            edge_value(source_rows[0]) if source_rows else None,
            edge_value(source_rows[-1]) if source_rows else None,
        )
        if source_key == getattr(
            self, "_skill_burst_blocked_ranges_source_key", None
        ):
            return getattr(self, "_skill_burst_blocked_ranges_cache", ())
        signature: list[tuple[float, float]] = []
        for row in source_rows:
            if isinstance(row, dict):
                start = max(0.0, as_number(row.get("start")))
                end = max(start, as_number(row.get("end")))
            elif isinstance(row, (list, tuple)) and len(row) >= 2:
                start = max(0.0, as_number(row[0]))
                end = max(start, as_number(row[1]))
            else:
                continue
            if end > start + 1e-6:
                signature.append((start, end))
        cache_key = tuple(signature)
        if cache_key == getattr(self, "_skill_burst_blocked_ranges_cache_key", None):
            return getattr(self, "_skill_burst_blocked_ranges_cache", ())

        ranges: list[tuple[float, float]] = []
        ranges.extend(signature)
        ranges.sort()
        merged: list[list[float]] = []
        for start, end in ranges:
            if merged and start <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        result = tuple((start, end) for start, end in merged)
        self._skill_burst_blocked_ranges_cache_key = cache_key
        self._skill_burst_blocked_ranges_source_key = source_key
        self._skill_burst_blocked_ranges_cache = result
        self._skill_burst_blocked_starts_cache = tuple(start for start, _end in result)
        blocked_total = 0.0
        prefix: list[float] = []
        for start, end in result:
            prefix.append(blocked_total)
            blocked_total += end - start
        self._skill_burst_blocked_prefix_cache = tuple(prefix)
        return result
    def _skill_burst_blocked_index(
        self,
    ) -> tuple[
        tuple[tuple[float, float], ...],
        tuple[float, ...],
        tuple[float, ...],
    ]:
        ranges = self._skill_burst_blocked_ranges()
        starts = getattr(self, "_skill_burst_blocked_starts_cache", ())
        prefix = getattr(self, "_skill_burst_blocked_prefix_cache", ())
        if len(starts) != len(ranges) or len(prefix) != len(ranges):
            starts = tuple(start for start, _end in ranges)
            blocked_total = 0.0
            prefix_rows: list[float] = []
            for start, end in ranges:
                prefix_rows.append(blocked_total)
                blocked_total += end - start
            prefix = tuple(prefix_rows)
            self._skill_burst_blocked_starts_cache = starts
            self._skill_burst_blocked_prefix_cache = prefix
        return ranges, starts, prefix
    def _skill_burst_blocked_range_at(self, seconds: float) -> tuple[float, float] | None:
        current = max(0.0, as_number(seconds))
        ranges, starts, _prefix = SkillTimelineEngineMixin._skill_burst_blocked_index(self)
        index = bisect_right(starts, current + 1e-6) - 1
        if index < 0:
            return None
        start, end = ranges[index]
        if start <= current + 1e-6 < end - 1e-6:
            return start, end
        return None
    def _skill_burst_next_block_start(self, seconds: float, window: float) -> float:
        current = max(0.0, as_number(seconds))
        _ranges, starts, _prefix = SkillTimelineEngineMixin._skill_burst_blocked_index(self)
        index = bisect_right(starts, current + 1e-6)
        if index < len(starts):
            return starts[index]
        return max(current, as_number(window))
    def _skill_burst_blocked_duration_before(self, seconds: float) -> float:
        current = max(0.0, as_number(seconds))
        ranges, starts, prefix = SkillTimelineEngineMixin._skill_burst_blocked_index(self)
        index = bisect_right(starts, current) - 1
        if index < 0:
            return 0.0
        start, end = ranges[index]
        return prefix[index] + max(0.0, min(current, end) - start)
    def _skill_burst_active_duration(self, start: float, end: float) -> float:
        left = max(0.0, as_number(start))
        right = max(left, as_number(end))
        blocked = max(
            0.0,
            SkillTimelineEngineMixin._skill_burst_blocked_duration_before(self, right)
            - SkillTimelineEngineMixin._skill_burst_blocked_duration_before(self, left),
        )
        return max(0.0, right - left - blocked)
    def _skill_burst_time_is_blocked(self, seconds: float) -> bool:
        return self._skill_burst_blocked_range_at(seconds) is not None
    @staticmethod
    def _skill_burst_active_crit_resist_reduction(
        effects: list[dict[str, Any]],
    ) -> float:
        aliases = {"减致命抵抗%", "减致命一击抵抗%", "减暴击抵抗%"}
        inputs = [
            EffectInput(
                key=norm(key),
                value=max(0.0, as_number(value)),
                skill_name=BuildEditor._team_effect_skill_name(
                    effect.get("skill")
                ),
                source_id=norm(effect.get("provider")),
                stack_group=norm(effect.get("stack_group")),
            )
            for effect in effects
            for key, value in effect.get("attrs", {}).items()
            if norm(key) in aliases and as_number(value) > 0
        ]
        return as_number(
            EFFECT_STACKING_ENGINE.resolve_active(inputs, 0.0).values.get(
                "减致命抵抗%"
            )
        )
    def _skill_burst_sim_enabled(self, skill: str) -> bool:
        schedule_override = getattr(self, "_skill_burst_schedule_override", None)
        if isinstance(schedule_override, dict):
            if skill == "practical":
                return False
            if skill == "cocktail":
                return bool(schedule_override.get("cocktail"))
            if skill in {"burst", "accel"}:
                return bool(schedule_override.get(skill))
        mode_override = getattr(self, "_skill_burst_mode_override", None)
        if mode_override is not None:
            return skill in mode_override
        widget = getattr(self, f"skill_burst_sim_{skill}_check", None)
        return bool(isinstance(widget, QCheckBox) and widget.isChecked())
    def _skill_burst_external_interval_suppressed(
        self,
        mode: str,
        start: float,
    ) -> bool:
        if not getattr(
            self, "_skill_burst_standalone_external_resistance", False
        ):
            return False
        group = (
            BURST_RESISTANCE_GROUP
            if mode == "burst"
            else TIME_ACCEL_RESISTANCE_GROUP
        )
        current = max(0.0, as_number(start))
        return any(
            norm(row.get("group")) == group
            and as_number(row.get("start")) <= current + 1e-9
            and current < as_number(row.get("end")) - 1e-9
            for row in getattr(
                self, "_skill_burst_local_acceleration_resistance_windows", ()
            )
            if isinstance(row, dict)
        )

    def _skill_burst_filter_external_acceleration_intervals(
        self,
        mode: str,
        intervals: tuple[tuple[float, float], ...],
    ) -> tuple[tuple[float, float], ...]:
        if not getattr(
            self,
            "_skill_burst_standalone_external_resistance",
            False,
        ):
            return intervals
        return tuple(
            (start, end)
            for start, end in intervals
            if not self._skill_burst_external_interval_suppressed(mode, start)
        )

    def _skill_burst_register_local_acceleration_resistance(
        self,
        skill: Any,
        start: float,
        cooldown: float,
    ) -> None:
        if not getattr(
            self, "_skill_burst_standalone_external_resistance", False
        ):
            return
        group = acceleration_resistance_group(skill)
        left = max(0.0, as_number(start))
        right = left + max(0.0, as_number(cooldown))
        if not group or right <= left + 1e-9:
            return
        windows = getattr(
            self, "_skill_burst_local_acceleration_resistance_windows", None
        )
        if not isinstance(windows, list):
            windows = []
            self._skill_burst_local_acceleration_resistance_windows = windows
        key = (group, round(left, 6))
        if any(
            (norm(row.get("group")), round(as_number(row.get("start")), 6))
            == key
            for row in windows
            if isinstance(row, dict)
        ):
            return
        windows.append({
            "group": group,
            "source": self._skill_burst_skill_key(skill),
            "start": left,
            "end": right,
        })
        # Candidate valuation may already have cached a cooldown that included
        # a now-rejected future external acceleration window.
        self._skill_burst_cooldown_ready_cache = {}

    def _skill_burst_mode_intervals(self, mode: str) -> tuple[tuple[float, float], ...]:
        schedule_override = getattr(self, "_skill_burst_schedule_override", None)
        if isinstance(schedule_override, dict):
            intervals = schedule_override.get(mode, [])
            source = intervals if isinstance(intervals, (list, tuple)) else []
            cache = getattr(self, "_skill_burst_mode_intervals_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._skill_burst_mode_intervals_cache = cache
            source_key = (
                id(schedule_override),
                id(intervals),
                len(source),
                self._skill_burst_player_buff_clear_times(),
            )
            cached = cache.get(mode)
            if cached is not None and cached[0] == source_key:
                result = cached[1]
            else:
                result = tuple(
                    (max(0.0, as_number(start)), max(0.0, as_number(end)))
                    for start, end in source
                    if as_number(end) > as_number(start)
                )
                result = self._skill_burst_clip_clearable_buff_intervals(
                    result,
                    {"skill": "精神爆发" if mode == "burst" else "时间加速"},
                )
                cache[mode] = (source_key, result)
            return self._skill_burst_filter_external_acceleration_intervals(
                mode, result
            )
        if self._skill_burst_sim_enabled("practical") or not self._skill_burst_sim_enabled(mode):
            return ()
        duration_key = "精神爆发" if mode == "burst" else "时间加速"
        return self._skill_burst_clip_clearable_buff_intervals(
            ((0.0, SKILL_BURST_SIM_DURATIONS[duration_key]),),
            {"skill": duration_key},
        )
    def _skill_burst_mode_boundary_points(self) -> tuple[float, ...]:
        """Exact, reusable transition points for the two acceleration modes."""
        intervals = (
            self._skill_burst_mode_intervals("burst"),
            self._skill_burst_mode_intervals("accel"),
        )
        cached = getattr(self, "_skill_burst_mode_boundary_points_cache", None)
        if cached is not None and cached[0] == intervals:
            return cached[1]
        points = tuple(sorted({
            as_number(point)
            for mode_intervals in intervals
            for interval in mode_intervals
            for point in interval[:2]
        }))
        self._skill_burst_mode_boundary_points_cache = (intervals, points)
        return points
    def _skill_burst_mode_active_at(self, mode: str, seconds: float) -> bool:
        current = max(0.0, as_number(seconds))
        intervals = self._skill_burst_mode_intervals(mode)
        if not intervals:
            return False
        cache = getattr(self, "_skill_burst_mode_active_index_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_mode_active_index_cache = cache
        indexed = cache.get(mode)
        if indexed is None or indexed[0] != intervals:
            ordered = sorted(intervals)
            starts: list[float] = []
            prefix_ends: list[float] = []
            running_end = float("-inf")
            for start, end in ordered:
                starts.append(start)
                running_end = max(running_end, end)
                prefix_ends.append(running_end)
            indexed = (intervals, tuple(starts), tuple(prefix_ends))
            cache[mode] = indexed
        index = bisect_right(indexed[1], current) - 1
        return index >= 0 and current < indexed[2][index]
    def _skill_burst_mode_toggled(self, mode: str, state: int) -> None:
        if getattr(self, "_skill_burst_mode_syncing", False):
            return
        self._skill_burst_mode_syncing = True
        try:
            if state:
                if mode == "practical":
                    targets = ("burst", "accel", "cocktail")
                else:
                    targets = ("practical",)
                for target in targets:
                    widget = getattr(self, f"skill_burst_sim_{target}_check", None)
                    if isinstance(widget, QCheckBox):
                        widget.setChecked(False)
        finally:
            self._skill_burst_mode_syncing = False
        self.schedule_refresh()
    def _skill_burst_sim_window_seconds(self) -> float:
        schedule_override = getattr(self, "_skill_burst_schedule_override", None)
        if isinstance(schedule_override, dict):
            return max(0.0, as_number(schedule_override.get("window")))
        if self._skill_burst_sim_enabled("practical"):
            return SKILL_BURST_PRACTICAL_WINDOW
        window = 0.0
        if self._skill_burst_sim_enabled("burst"):
            window = max(window, SKILL_BURST_SIM_DURATIONS["精神爆发"])
        if self._skill_burst_sim_enabled("accel"):
            window = max(window, SKILL_BURST_SIM_DURATIONS["时间加速"])
        if window <= 0 and self._skill_burst_sim_enabled("cocktail"):
            window = SKILL_BURST_PRACTICAL_WINDOW
        return window
    def _skill_burst_sim_cdr_percent_at(
        self,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        value = 0.0
        if not self._skill_burst_sim_enabled("practical"):
            if self._skill_burst_sim_enabled("cocktail"):
                value += 12.0
            if self._skill_burst_mode_active_at("burst", seconds):
                value += 40.0
            if self._skill_burst_mode_active_at("accel", seconds):
                value += 70.0
        value += max(0.0, display_value(active_attrs or {}, "冷却缩减%"))
        return min(70.0, max(0.0, value))
    def _skill_burst_sim_action_speed_at(
        self,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
        skill: str = "",
    ) -> float:
        value = 0.0
        if (
            not self._skill_burst_sim_enabled("practical")
            and self._skill_burst_mode_active_at("burst", seconds)
        ):
            value += 40.0
        value += self._skill_burst_scoped_timing_attr(
            skill, active_attrs, "动作速度%"
        )
        value += self._skill_burst_scoped_timing_attr(skill, active_attrs, "攻速%")
        return value

    def _skill_burst_periodic_timing_rows(
        self,
    ) -> tuple[tuple[frozenset[str], str, float, float], ...]:
        actions = getattr(self, "_skill_burst_periodic_buffs", [])
        source = actions if isinstance(actions, list) else []
        signature = tuple(
            (
                id(action),
                tuple(action.get("skill_scope", ())),
                repr(action.get("buff_marker")),
                repr(action.get("attrs", {}).get("动作速度%")),
                repr(action.get("attrs", {}).get("攻速%")),
            )
            for action in source
        )
        cached = getattr(self, "_skill_burst_periodic_timing_rows_cache", None)
        if (
            isinstance(cached, tuple)
            and len(cached) == 3
            and cached[0] is source
            and cached[1] == signature
        ):
            return cached[2]
        rows = tuple(
            (
                frozenset(
                    key
                    for name in action.get("skill_scope", ())
                    if (key := self._skill_burst_skill_key(name))
                ),
                norm(action.get("buff_marker")),
                max(
                    0.0,
                    display_value(action.get("attrs", {}), "动作速度%"),
                ),
                max(
                    0.0,
                    display_value(action.get("attrs", {}), "攻速%"),
                ),
            )
            for action in source
        )
        self._skill_burst_periodic_timing_rows_cache = (
            source,
            signature,
            rows,
        )
        return rows

    def _skill_burst_scoped_timing_attr(
        self,
        skill: str,
        active_attrs: dict[str, Any] | None,
        attr: str,
    ) -> float:
        """Return one timing bonus after removing out-of-scope personal buffs."""

        attrs = active_attrs or {}
        value = max(0.0, display_value(attrs, attr))
        # Every scoped correction below is non-negative and the final value
        # is clamped at zero. Zero cannot become positive by subtracting it;
        # avoid reconstructing all periodic rules for this common case.
        if value <= 0.0:
            return value
        skill_key = self._skill_burst_skill_key(skill)
        if not skill_key:
            return value
        attr_index = 2 if attr == "动作速度%" else 3
        for row in self._skill_burst_periodic_timing_rows():
            scope, marker = row[0], row[1]
            if not scope or skill_key in scope:
                continue
            if not marker or display_value(attrs, marker) <= 0:
                continue
            value -= row[attr_index]
        return max(0.0, value)
    def _skill_burst_is_ultimate_name(self, skill_name: str) -> bool:
        job = norm(self.second_job.currentText())
        skill_key = self._skill_burst_skill_key(skill_name)
        cache = getattr(self, "_skill_burst_is_ultimate_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_is_ultimate_cache = cache
        cache_key = (job, skill_key)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        tree = getattr(self, "skill_build_data", None)
        if not isinstance(tree, dict) or not isinstance(tree.get("skills"), dict):
            tree = self.store.skill_build_tree(job, SKILL_BUILD_LEVEL_CAP)
        for skill in tree.get("skills", {}).values():
            if self._skill_burst_skill_key(skill.get("name")) == skill_key:
                result = self._skill_build_is_ultimate(skill)
                cache[cache_key] = result
                return result
        cache[cache_key] = False
        return False
    def _skill_burst_all_learned_metrics(
        self,
        summary: dict[str, float],
        selected_metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        profile = self._job_profile()
        weighted_skill_panel = self._skill_weighted_panel_profile()
        preferred_type = None if weighted_skill_panel else ("M" if profile.get("attack_attr") == "魔攻" else "P")
        by_skill: dict[str, dict[str, Any]] = {}
        for source in selected_metrics:
            metric = dict(source)
            skill_key = self._skill_burst_skill_key(metric.get("skill"))
            if not skill_key:
                continue
            if not metric.get("skill_glyph_damage_applied"):
                glyph_multiplier = self._skill_glyph_damage_multiplier(skill_key)
                for field in ("raw_score", "effective_score", "timeline_raw_score"):
                    if field in metric:
                        metric[field] = as_number(metric.get(field)) * glyph_multiplier
                for field in ("channel_scores", "timeline_attack_scores"):
                    values = metric.get(field)
                    if isinstance(values, dict):
                        metric[field] = {
                            key: as_number(value) * glyph_multiplier
                            for key, value in values.items()
                        }
                metric["skill_glyph_damage_applied"] = True
            if norm(self.second_job.currentText()) == "火舞":
                base_multiplier = max(
                    0.0,
                    as_number(
                        metric.get("base_multiplier"),
                        self._skill_adjusted_multiplier(metric.get("skill", ""), metric.get("level", "")),
                    ),
                )
                applied_bonus = max(0.0, as_number(metric.get("burning_hand_bonus_applied")))
                base_bonus = self._skill_burning_hand_bonus_for_mode(metric.get("skill", ""), "base")
                applied_total = base_multiplier + applied_bonus
                base_total = base_multiplier + base_bonus
                if applied_total > 1e-9 and abs(base_total - applied_total) > 1e-9:
                    ratio = base_total / applied_total
                    for field in ("raw_score", "effective_score", "timeline_raw_score"):
                        if field in metric:
                            metric[field] = as_number(metric.get(field)) * ratio
                    for field in ("channel_scores", "timeline_attack_scores"):
                        values = metric.get(field)
                        if isinstance(values, dict):
                            metric[field] = {
                                key: as_number(value) * ratio
                                for key, value in values.items()
                            }
                metric["base_multiplier"] = base_multiplier
                metric["burning_hand_base_bonus"] = base_bonus
            if norm(self.second_job.currentText()) in {"圣骑士", "十字军"} and not metric.get("timeline_attack_scores"):
                level_data, _effective_level, _mastery_bonus = self._skill_sim_level_data_with_mastery(
                    metric.get("skill", ""),
                    metric.get("level", ""),
                )
                if level_data:
                    metric_multiplier = (
                        self._skill_adjusted_multiplier(metric.get("skill", ""), metric.get("level", ""))
                        + self._skill_burning_hand_bonus(metric.get("skill", ""))
                    )
                    metric_dps_multiplier = self._skill_sim_dps_multiplier(
                        norm(metric.get("element")),
                        profile,
                    )
                    timeline_scores = self._skill_timeline_attack_scores(
                        level_data,
                        summary,
                        multiplier=metric_multiplier * metric_dps_multiplier,
                        preferred_type="P",
                    )
                    metric["timeline_attack_scores"] = timeline_scores
                    metric["timeline_raw_score"] = sum(timeline_scores.values())
            base_cooldown = max(0.0, as_number(metric.get("base_cooldown")))
            cast_time = max(0.0, as_number(metric.get("cast_time")) or self._skill_burst_sim_cast_time(skill_key, 0.0))
            effective_cooldown = self._skill_burst_effective_cooldown(skill_key, base_cooldown, 0.0)
            metric["dps"] = as_number(metric.get("effective_score")) / effective_cooldown if effective_cooldown > 0 else 0.0
            metric["dpa"] = (
                0.0
                if self._skill_periodic_total_damage_cycle(skill_key) > 0
                else as_number(metric.get("effective_score")) / cast_time
                if cast_time > 0
                else 0.0
            )
            metric["is_ultimate"] = self._skill_burst_is_ultimate_name(skill_key)
            by_skill[skill_key] = metric

        for skill in self.store.skill_damage_names(self.second_job.currentText()):
            weighted_skill_panel = self._skill_weighted_panel_profile(skill)
            skill_key = self._skill_burst_skill_key(skill)
            if not skill_key or skill_key in by_skill or skill == "无":
                continue
            build_level = self._skill_build_level_for_burst_sim(skill)
            if build_level is not None and build_level <= 0:
                continue
            levels = self._skill_sim_available_levels(skill)
            if not levels:
                continue
            level = levels[-1]
            level_data, _effective_level, _mastery_bonus = self._skill_sim_level_data_with_mastery(skill, level)
            # Available levels are already capped by the character level. A +1
            # mastery may resolve to a table row whose own required level is 71+;
            # that enhanced row is still valid and must not hide the learned skill.
            if not level_data:
                continue
            base_multiplier = self._skill_adjusted_multiplier(skill, level)
            burning_hand_base_bonus = self._skill_burning_hand_bonus_for_mode(skill, "base")
            multiplier = base_multiplier + burning_hand_base_bonus
            effective = self._skill_level_effective(
                level_data,
                summary,
                multiplier=multiplier,
                preferred_type=preferred_type,
                weighted_panel=weighted_skill_panel,
            )
            element = norm(self.store.skill_damage_element(self.second_job.currentText(), skill))
            dps_multiplier = self._skill_sim_dps_multiplier(element, profile)
            raw_score = max(0.0, effective * dps_multiplier)
            glyph_damage_multiplier = self._skill_glyph_damage_multiplier(skill)
            raw_score *= glyph_damage_multiplier
            if raw_score <= 0:
                continue
            channel_scores = self._skill_level_channel_scores(
                level_data,
                summary,
                multiplier=multiplier,
                preferred_type=preferred_type,
                weighted_panel=weighted_skill_panel,
                element=element,
            )
            channel_scores = {
                channel: value * dps_multiplier * glyph_damage_multiplier
                for channel, value in channel_scores.items()
                if value > 0
            }
            element_multiplier = self._skill_element_display_multiplier(element, summary)
            base_cooldown = self._skill_burst_original_cooldown(
                skill,
                level_data,
                self._skill_adjusted_cooldown(skill, level_data),
            )
            effective_cooldown = self._skill_burst_effective_cooldown(skill, base_cooldown, 0.0)
            cast_time = self._skill_burst_sim_cast_time(skill, 0.0)
            metric = {
                "row": 1000 + len(by_skill),
                "skill": norm(skill),
                "level": level,
                "base_cooldown": base_cooldown,
                "raw_score": raw_score,
                "effective_score": raw_score * element_multiplier,
                "element": element,
                "cast_time": cast_time,
                "dps": raw_score * element_multiplier / effective_cooldown if effective_cooldown > 0 else 0.0,
                "dpa": (
                    0.0
                    if self._skill_periodic_total_damage_cycle(skill_key) > 0
                    else raw_score * element_multiplier / cast_time
                    if cast_time > 0
                    else 0.0
                ),
                "is_ultimate": self._skill_burst_is_ultimate_name(skill),
                "channel_scores": channel_scores,
                "skill_glyph_damage_applied": True,
                "base_multiplier": base_multiplier,
                "burning_hand_base_bonus": burning_hand_base_bonus,
            }
            if norm(self.second_job.currentText()) in {"圣骑士", "十字军"}:
                timeline_scores = self._skill_timeline_attack_scores(
                    level_data,
                    summary,
                    multiplier=multiplier * dps_multiplier * glyph_damage_multiplier,
                    preferred_type="P",
                )
                metric["timeline_attack_scores"] = timeline_scores
                metric["timeline_raw_score"] = sum(timeline_scores.values())
            by_skill[skill_key] = metric
        return list(by_skill.values())
    @staticmethod
    def _skill_burst_candidate_metric_score(metric: dict[str, Any]) -> float:
        timeline_scores = metric.get("timeline_attack_scores")
        if isinstance(timeline_scores, dict) and any(
            as_number(value) > 0 for value in timeline_scores.values()
        ):
            return max(0.0, as_number(metric.get("timeline_raw_score")))
        return max(0.0, as_number(metric.get("effective_score")))
    @staticmethod
    def _skill_burst_candidate_metric_dpa(metric: dict[str, Any]) -> float:
        timeline_scores = metric.get("timeline_attack_scores")
        if isinstance(timeline_scores, dict) and any(
            as_number(value) > 0 for value in timeline_scores.values()
        ):
            cast_time = max(0.0, as_number(metric.get("cast_time")))
            score = BuildEditor._skill_burst_candidate_metric_score(metric)
            return score / cast_time if cast_time > 0 and score > 0 else 0.0
        dpa = max(0.0, as_number(metric.get("dpa")))
        if dpa > 0:
            return dpa
        if BuildEditor._skill_periodic_total_damage_cycle(metric.get("skill")) <= 0:
            return 0.0
        cast_time = max(0.0, as_number(metric.get("cast_time")))
        score = max(0.0, as_number(metric.get("effective_score")))
        return score / cast_time if cast_time > 0 and score > 0 else 0.0
    @staticmethod
    def _skill_burst_candidate_metric_dps(metric: dict[str, Any]) -> float:
        timeline_scores = metric.get("timeline_attack_scores")
        if isinstance(timeline_scores, dict) and any(
            as_number(value) > 0 for value in timeline_scores.values()
        ):
            cooldown = max(0.0, as_number(metric.get("effective_cooldown")))
            if cooldown <= 0:
                cooldown = max(0.0, as_number(metric.get("base_cooldown")))
            score = BuildEditor._skill_burst_candidate_metric_score(metric)
            return score / cooldown if cooldown > 0 and score > 0 else 0.0
        return max(0.0, as_number(metric.get("dps")))
    @staticmethod
    def _skill_burst_candidate_pool(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates = [
            metric
            for metric in metrics
            if BuildEditor._skill_burst_candidate_metric_dpa(metric) > 0
        ]
        return sorted(
            candidates,
            key=lambda metric: (
                BuildEditor._skill_burst_candidate_metric_dpa(metric),
                BuildEditor._skill_burst_candidate_metric_dps(metric),
            ),
            reverse=True,
        )
    def _skill_burst_mark_timeline_fillers(
        self,
        metrics: list[dict[str, Any]],
        job: str | None = None,
    ) -> list[dict[str, Any]]:
        eligible = [
            metric
            for metric in metrics
            if self._skill_burst_candidate_metric_dpa(metric) > 0
            and as_number(metric.get("effective_score")) > 0
        ]
        real_job = norm(job or self.second_job.currentText())
        if hasattr(self, "_team_real_job"):
            real_job = norm(self._team_real_job(real_job))
        idle_only_skills = SKILL_BURST_IDLE_ONLY_BY_JOB.get(real_job, set())
        protected_skills = {
            self._skill_burst_skill_key(skill)
            for mapping in (
                SKILL_BURST_COMBO_FOLLOWUPS_BY_JOB.get(real_job, {}),
                SKILL_BURST_REQUIRED_PREDECESSORS_BY_JOB.get(real_job, {}),
                SKILL_BURST_REQUIRED_FOLLOWUPS_BY_JOB.get(real_job, {}),
            )
            for skill, related in mapping.items()
            for skill in (skill, *related)
        }
        for metric in eligible:
            skill_key = self._skill_burst_skill_key(metric.get("skill"))
            skill_entry = self._skill_build_entry_for_skill_name(skill_key)
            learned_level = self._skill_build_level_for_burst_sim(skill_key)
            low_investment = bool(
                learned_level is not None
                and learned_level <= 1
                and int(as_number(skill_entry.get("max_level"), 1)) > 1
            )
            metric["timeline_filler"] = bool(
                (low_investment and skill_key not in protected_skills)
                or skill_key in idle_only_skills
            )
            metric["timeline_idle_only"] = skill_key in idle_only_skills
        return eligible
    def _skill_burst_defer_idle_only_ready(
        self,
        ready: list[tuple[float, float, float, float, dict[str, Any]]],
    ) -> list[tuple[float, float, float, float, dict[str, Any]]]:
        """Keep destructive/low-priority skills only when no normal skill is ready."""

        if not ready:
            return ready
        real_job = norm(self.second_job.currentText())
        if hasattr(self, "_team_real_job"):
            real_job = norm(self._team_real_job(real_job))
        idle_only_skills = SKILL_BURST_IDLE_ONLY_BY_JOB.get(real_job, set())
        if not idle_only_skills:
            return ready
        primary_ready = [
            item
            for item in ready
            if self._skill_burst_skill_key(item[4].get("skill"))
            not in idle_only_skills
        ]
        return primary_ready or ready
    def _skill_burst_sim_cast_time(
        self,
        skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        timing = self._skill_effective_cast_timing(
            skill,
            self._skill_burst_sim_action_speed_at(
                seconds, active_attrs, skill=skill
            ),
        )
        return max(0.0, as_number(timing.get("cast_time"))) * self._skill_burst_fury_cast_time_ratio(skill, active_attrs)
    def _skill_burst_sim_full_cast_time(
        self,
        skill: str,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        """Return the accelerated full animation time, ignoring quick links."""
        timing = self._skill_effective_cast_timing(
            skill,
            self._skill_burst_sim_action_speed_at(
                seconds, active_attrs, skill=skill
            ),
        )
        return max(0.0, as_number(timing.get("full_cast_time"))) * self._skill_burst_fury_cast_time_ratio(skill, active_attrs)

    def _skill_burst_fury_cast_time_ratio(self, skill: str, active_attrs: dict[str, Any] | None) -> float:
        """Resolve the normal/6-fury EX animation before reserving timeline time."""
        if norm(self.second_job.currentText()) != '狂战士' or self._skill_burst_skill_key(skill) != '旋转冲击':
            return 1.0
        timing = self._skill_cast_timing(skill)
        unboosted = as_number(timing.get('fury_unboosted_cast_time'))
        if unboosted <= 0:
            return 1.0
        cost, _ = self._skill_burst_fury_consumer(skill)
        if cost > 0 and display_value(active_attrs or {}, SKILL_BURST_FURY_MARKER) < cost:
            return unboosted / max(1e-9, as_number(timing.get('cast_time')))
        return 1.0
    def _skill_burst_original_cooldown(
        self,
        skill: str,
        fallback_level_data: list[Any] | None = None,
        fallback: float = 0.0,
    ) -> float:
        skill_key = self._skill_burst_skill_key(skill)
        ammo_consumer = self._skill_burst_ammo_cost(skill_key) > 0
        timeline_fixed = SKILL_TIMELINE_FIXED_COOLDOWNS_BY_JOB.get(
            (norm(self.second_job.currentText()), skill_key)
        )
        if timeline_fixed is not None:
            return max(0.0, as_number(timeline_fixed))
        if not ammo_consumer:
            fixed_cooldown = self._skill_fixed_cooldown(skill_key)
            if fixed_cooldown is not None:
                return fixed_cooldown
        if not ammo_consumer:
            adjustment = self.store.skill_damage_adjustment(self.second_job.currentText(), skill)
            adjusted_cooldown = as_number(adjustment.get("cooldown")) if isinstance(adjustment, dict) and "cooldown" in adjustment else 0.0
            if adjusted_cooldown > 0:
                return adjusted_cooldown
        skill_item = self._skill_build_entry_for_skill_name(skill)
        build_level = self._skill_build_level_for_burst_sim(skill)
        if skill_item and build_level is not None and build_level > 0:
            level_data = self._skill_build_level_data_for_display(
                skill_item,
                int(build_level),
                self._skill_mastery_bonus_for_skill(skill),
            )
            cooldown = self._skill_level_cooldown(level_data) if level_data else 0.0
            if cooldown > 0:
                return cooldown
        if fallback_level_data is None:
            default_level = self._skill_default_level(skill)
            level_data = self.store.skill_damage_level_data(self.second_job.currentText(), skill, default_level)
            cooldown = self._skill_level_cooldown(level_data) if level_data else 0.0
            if cooldown > 0 or ammo_consumer:
                return cooldown
        cooldown = self._skill_level_cooldown(fallback_level_data) if fallback_level_data else 0.0
        if ammo_consumer:
            return max(0.0, cooldown)
        return cooldown if cooldown > 0 else max(0.0, as_number(fallback))
    def _skill_burst_effective_cooldown(
        self,
        skill: str,
        base_cooldown: float,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        if self._skill_burst_skill_key(skill) == "双飞腿":
            return 0.0
        if self._skill_burst_ammo_cost(skill) > 0:
            # 飞溅/生化导弹/脑电波投射的实战门槛是弹药而非技能CD。
            return 0.0
        timeline_fixed = SKILL_TIMELINE_FIXED_COOLDOWNS_BY_JOB.get(
            (norm(self.second_job.currentText()), self._skill_burst_skill_key(skill))
        )
        if timeline_fixed is not None:
            return max(0.0, as_number(timeline_fixed))
        base = max(0.0, as_number(base_cooldown))
        if base <= 0:
            return 0.0
        ready_at = self._skill_burst_cooldown_ready_at(skill, base, seconds, active_attrs)
        cooldown = max(0.0, ready_at - max(0.0, as_number(seconds)))
        min_cooldown = self._skill_summon_min_cooldown(self._skill_burst_skill_key(skill))
        if min_cooldown is not None:
            cooldown = max(cooldown, min_cooldown)
        return cooldown
    def _skill_burst_cooldown_ready_at(
        self,
        skill: str,
        base_cooldown: float,
        start: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        start_time = max(0.0, as_number(start))
        glyph_cdr = min(100.0, self._skill_glyph_effect_value(skill, "冷却缩减"))
        raw_cdr_windows = (active_attrs or {}).get("__cdr_windows__", ())
        cdr_signature = (active_attrs or {}).get("__cdr_signature__")
        if not isinstance(cdr_signature, tuple):
            cdr_signature = tuple(
                (
                    round(max(0.0, as_number(row[0])), 6),
                    round(max(0.0, as_number(row[1])), 6),
                    round(max(0.0, as_number(row[2])), 6),
                    norm(row[3]) if len(row) >= 4 else f"legacy-{as_number(row[2]):g}",
                )
                for row in raw_cdr_windows
                if isinstance(row, (list, tuple)) and len(row) >= 3
            )
        cooldown_cache = getattr(self, "_skill_burst_cooldown_ready_cache", None)
        if not isinstance(cooldown_cache, dict):
            cooldown_cache = {}
            self._skill_burst_cooldown_ready_cache = cooldown_cache
        cache_key = (
            self._skill_burst_skill_key(skill),
            round(max(0.0, as_number(base_cooldown)), 6),
            round(start_time, 6),
            round(glyph_cdr, 6),
            round(max(0.0, display_value(active_attrs or {}, "冷却缩减%")), 6),
            cdr_signature,
        )
        cached_ready = cooldown_cache.get(cache_key)
        if cached_ready is not None:
            return cached_ready
        remaining_work = max(0.0, as_number(base_cooldown)) * (1 - glyph_cdr / 100)
        ready = self._skill_burst_cooldown_ready_from_work(
            skill,
            remaining_work,
            start_time,
            active_attrs,
            apply_min_cooldown=True,
        )
        cooldown_cache[cache_key] = ready
        if len(cooldown_cache) > 8192:
            cooldown_cache.pop(next(iter(cooldown_cache)))
        return ready
    def _skill_burst_cooldown_ready_from_work(
        self,
        skill: str,
        remaining_work: float,
        start: float,
        active_attrs: dict[str, Any] | None = None,
        *,
        apply_min_cooldown: bool = False,
    ) -> float:
        """Integrate remaining cooldown work through changing CDR windows."""
        start_time = max(0.0, as_number(start))
        remaining_work = max(0.0, as_number(remaining_work))
        if remaining_work <= 0:
            return start_time
        # CDR is clamped to [0, 70], so ready time cannot exceed this bound.
        # Preserve the first later boundary as well: the old integrator used
        # its midpoint to evaluate the last constant-CDR interval.
        latest_possible_ready = start_time + remaining_work
        raw_cdr_windows = (active_attrs or {}).get("__cdr_windows__", ())
        cdr_windows = [
            (
                float(row[0]),
                float(row[1]),
                float(row[2]),
                norm(row[3]) if len(row) >= 4 else f"legacy-{as_number(row[2]):g}",
            )
            for row in raw_cdr_windows
            if isinstance(row, (list, tuple))
            and len(row) >= 3
            and float(row[1]) > start_time
        ]
        mode_points = self._skill_burst_mode_boundary_points()
        first_mode = bisect_right(mode_points, start_time + 1e-9)
        last_mode = bisect_right(mode_points, latest_possible_ready)
        boundaries = set(mode_points[first_mode:last_mode])
        next_boundary = (
            mode_points[last_mode] if last_mode < len(mode_points) else None
        )
        for left, right, _value, _source in cdr_windows:
            for point in (left, right):
                if point <= start_time + 1e-9:
                    continue
                if point <= latest_possible_ready:
                    boundaries.add(max(start_time, point))
                elif next_boundary is None or point < next_boundary:
                    next_boundary = point
        if next_boundary is not None:
            boundaries.add(next_boundary)
        boundaries = sorted(boundaries)
        current = start_time
        for boundary in boundaries:
            midpoint = current + max(1e-6, (boundary - current) / 2)
            cdr = self._skill_burst_cooldown_cdr_at(midpoint, active_attrs, cdr_windows)
            speed = 1 / max(0.3, 1 - cdr / 100)
            progressed = max(0.0, boundary - current) * speed
            if progressed >= remaining_work - 1e-9:
                ready = current + remaining_work / speed
                break
            remaining_work -= progressed
            current = boundary
        else:
            cdr = self._skill_burst_cooldown_cdr_at(current + 1e-6, active_attrs, cdr_windows)
            speed = 1 / max(0.3, 1 - cdr / 100)
            ready = current + remaining_work / speed
        min_cooldown = (
            None
            if self._skill_burst_is_refreshable_periodic_damage(skill)
            else self._skill_summon_min_cooldown(self._skill_burst_skill_key(skill))
        )
        if apply_min_cooldown and min_cooldown is not None:
            ready = max(ready, start_time + min_cooldown)
        return ready
    def _skill_burst_cooldown_work_between(
        self,
        start: float,
        end: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        """Return cooldown work completed between two wall-clock timestamps."""
        start_time = max(0.0, as_number(start))
        end_time = max(start_time, as_number(end))
        if end_time <= start_time + 1e-9:
            return 0.0
        raw_cdr_windows = (active_attrs or {}).get("__cdr_windows__", ())
        cdr_windows = [
            (
                float(row[0]),
                float(row[1]),
                float(row[2]),
                norm(row[3]) if len(row) >= 4 else f"legacy-{as_number(row[2]):g}",
            )
            for row in raw_cdr_windows
            if isinstance(row, (list, tuple))
            and len(row) >= 3
            and float(row[1]) > start_time
            and float(row[0]) < end_time
        ]
        mode_points = self._skill_burst_mode_boundary_points()
        first_mode = bisect_right(mode_points, start_time + 1e-9)
        last_mode = bisect_right(mode_points, end_time - 1e-9)
        boundaries = set(mode_points[first_mode:last_mode])
        boundaries.update(
            max(start_time, min(end_time, as_number(point)))
            for row in cdr_windows
            for point in row[:2]
            if start_time + 1e-9 < as_number(point) < end_time - 1e-9
        )
        boundaries = sorted(boundaries)
        work = 0.0
        current = start_time
        for boundary in [*boundaries, end_time]:
            midpoint = current + max(1e-6, (boundary - current) / 2)
            cdr = self._skill_burst_cooldown_cdr_at(midpoint, active_attrs, cdr_windows)
            speed = 1 / max(0.3, 1 - cdr / 100)
            work += max(0.0, boundary - current) * speed
            current = boundary
        return work
    def _skill_burst_cooldown_cdr_at(
        self,
        seconds: float,
        active_attrs: dict[str, Any] | None,
        cdr_windows: list[tuple[float, float, float, str]],
    ) -> float:
        global_cdr = self._skill_burst_sim_cdr_percent_at(seconds, {})
        if cdr_windows:
            by_source: dict[str, float] = {}
            for start, end, value, source in cdr_windows:
                if start <= seconds < end:
                    by_source[source] = max(by_source.get(source, 0.0), value)
            periodic_cdr = sum(by_source.values())
        else:
            periodic_cdr = max(0.0, display_value(active_attrs or {}, "冷却缩减%"))
        return min(70.0, max(0.0, global_cdr + periodic_cdr))
    def _skill_burst_ammo_rule(self) -> dict[str, Any]:
        job = norm(self.second_job.currentText())
        cached = getattr(self, "_skill_burst_ammo_rule_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == job:
            return cached[1]
        rule = SKILL_BURST_AMMO_RULES_BY_JOB.get(job, {})
        resolved = rule if isinstance(rule, dict) else {}
        self._skill_burst_ammo_rule_cache = (job, resolved)
        return resolved
    def _skill_burst_ammo_cost(self, skill: str) -> int:
        job = norm(self.second_job.currentText())
        skill_key = self._skill_burst_skill_key(skill)
        cache = getattr(self, "_skill_burst_ammo_cost_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_ammo_cost_cache = cache
        cache_key = (job, skill_key)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        rule = self._skill_burst_ammo_rule()
        consumers = rule.get("consumers", {}) if isinstance(rule.get("consumers"), dict) else {}
        result = int(as_number(consumers.get(skill_key)))
        cache[cache_key] = result
        return result
    def _skill_burst_reload_skill(self) -> str:
        return norm(self._skill_burst_ammo_rule().get("reload"))
    def _skill_burst_reload_available(self) -> bool:
        reload_skill = self._skill_burst_reload_skill()
        if not reload_skill:
            return False
        build_level = self._skill_build_level_for_burst_sim(reload_skill)
        return build_level is None or build_level > 0
    def _skill_burst_reload_base_cooldown(self) -> float:
        reload_skill = self._skill_burst_reload_skill()
        if not reload_skill:
            return 0.0
        return self._skill_burst_original_cooldown(reload_skill)
    def _skill_burst_reload_cast_time(
        self,
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        reload_skill = self._skill_burst_reload_skill()
        if not reload_skill:
            return 0.0
        cast_time = self._skill_burst_sim_cast_time(reload_skill, seconds, active_attrs)
        return cast_time if cast_time > 0 else 0.6
    def _skill_burst_reload_damage_values(
        self,
        metric: dict[str, Any] | None,
        active_attrs: dict[str, Any],
        start: float,
        window: float,
        cast_time: float,
    ) -> dict[str, Any]:
        if not isinstance(metric, dict) or cast_time <= 0:
            return {
                "dpa": 0.0,
                "effective_score": 0.0,
                "raw_effective_score": 0.0,
                "channel_scores": {},
                "element": "",
            }
        score = self._skill_burst_window_metric_score(metric, active_attrs, start, window, cast_time)
        raw_score = self._skill_burst_window_raw_metric_score(metric, active_attrs, start, window, cast_time)
        return {
            "dpa": score / cast_time,
            "effective_score": score,
            "raw_effective_score": raw_score,
            "channel_scores": self._skill_burst_scaled_channel_scores(metric, raw_score),
            "element": norm(metric.get("element")),
        }
    def _skill_burst_reload_package_dpa(
        self,
        ammo_blocked: list[tuple[float, float, float, float, dict[str, Any]]],
        reload_metric: dict[str, Any] | None,
        active_attrs: dict[str, Any],
        start: float,
        window: float,
    ) -> float:
        """Evaluate reload together with the best casts enabled by its ammo."""
        rule = self._skill_burst_ammo_rule()
        ammo_gain = max(0, int(as_number(rule.get("gain"))))
        reload_cast = self._skill_burst_reload_cast_time(start, active_attrs)
        if ammo_gain <= 0 or reload_cast <= 0 or not ammo_blocked:
            return 0.0
        reload_score = 0.0
        if isinstance(reload_metric, dict):
            reload_score = self._skill_burst_window_metric_score(
                reload_metric, active_attrs, start, window, reload_cast
            )
        best_package = 0.0
        for _adjusted, dpa, negative_cast, _wait, metric in ammo_blocked:
            skill = self._skill_burst_skill_key(metric.get("skill"))
            ammo_cost = max(1, self._skill_burst_ammo_cost(skill))
            enabled_casts = ammo_gain // ammo_cost
            cast_time = max(0.0, -as_number(negative_cast))
            if enabled_casts <= 0 or cast_time <= 0:
                continue
            consumer_score = max(0.0, as_number(dpa)) * cast_time
            total_time = reload_cast + enabled_casts * cast_time
            package_dpa = (
                reload_score + enabled_casts * consumer_score
            ) / max(1e-9, total_time)
            best_package = max(best_package, package_dpa)
        return best_package
    def _skill_burst_uses_fury(self) -> bool:
        job = norm(self.second_job.currentText())
        cached = getattr(self, "_skill_burst_uses_fury_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == job:
            return cached[1]
        result = bool(JOB_MECHANICS.job_value(job, "resources.fury", {}))
        self._skill_burst_uses_fury_cache = (job, result)
        return result
    def _skill_burst_fury_gain(self, skill: str) -> int:
        if not self._skill_burst_uses_fury():
            return 0
        skill_key = self._skill_burst_skill_key(skill)
        gain = SKILL_BURST_FURY_GENERATORS.get(skill_key, 0)
        if gain <= 0:
            return 0
        return gain if self._skill_burst_learned_ex_node_level(f"{skill_key}EX") > 0 else 0
    def _skill_burst_fury_consumer(self, skill: str) -> tuple[int, float]:
        if not self._skill_burst_uses_fury():
            return 0, 1.0
        if (self._skill_burst_skill_key(skill) == '旋转冲击'
                and self._skill_build_level_for_id('505') == 0):
            return 0, 1.0
        return SKILL_BURST_FURY_CONSUMERS.get(self._skill_burst_skill_key(skill), (0, 1.0))
    def _skill_burst_uses_guardian_phantoms(self) -> bool:
        job = norm(self.second_job.currentText())
        cached = getattr(self, "_skill_burst_uses_guardian_phantoms_cache", None)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == job:
            return cached[1]
        result = bool(JOB_MECHANICS.job_value(job, "resources.guardian_phantom", {}))
        self._skill_burst_uses_guardian_phantoms_cache = (job, result)
        return result
    def _skill_burst_guardian_phantom_gain(self, skill: str) -> int:
        if not self._skill_burst_uses_guardian_phantoms():
            return 0
        return int(SKILL_BURST_GUARDIAN_PHANTOM_GENERATORS.get(self._skill_burst_skill_key(skill), 0))
    def _skill_burst_guardian_phantom_cost(self, skill: str) -> int:
        if not self._skill_burst_uses_guardian_phantoms():
            return 0
        return int(SKILL_BURST_GUARDIAN_PHANTOM_COSTS.get(self._skill_burst_skill_key(skill), 0))
    def _skill_burst_apply_guardian_phantom_use(self, phantoms: int, skill: str) -> tuple[int, bool]:
        if not self._skill_burst_uses_guardian_phantoms():
            return phantoms, True
        cost = self._skill_burst_guardian_phantom_cost(skill)
        used = cost <= 0 or phantoms >= cost
        if used and cost > 0:
            phantoms -= cost
        phantoms = min(
            SKILL_BURST_GUARDIAN_PHANTOM_MAX,
            phantoms + self._skill_burst_guardian_phantom_gain(skill),
        )
        return phantoms, used
    def _skill_burst_apply_fury_use(self, fury: int, skill: str) -> tuple[int, bool]:
        cost, _boost = self._skill_burst_fury_consumer(skill)
        boosted = cost > 0 and fury >= cost
        if boosted:
            fury -= cost
        fury = min(SKILL_BURST_FURY_MAX, fury + self._skill_burst_fury_gain(skill))
        return fury, boosted
    def _skill_burst_state_requirement_satisfied(
        self,
        skill: str,
        active_attrs: dict[str, Any] | None,
    ) -> bool:
        job = norm(self.second_job.currentText())
        skill_key = self._skill_burst_skill_key(skill)
        entry = self.store._skill_damage_entry(job, skill_key)
        required_form = entry.get("required_form") if isinstance(entry, dict) else None
        # Holy hammer has a refreshable 120s stance modeled as permanent.
        # Crippling Punisher is a short active window and needs an explicit gate.
        if job == "烈" and required_form and display_value(active_attrs or {}, "__爆发buff__" + required_form) <= 0:
            return False
        if job == "机械大师":
            mechanic_mode_active = display_value(active_attrs or {}, SKILL_BURST_MECHANIC_MODE_MARKER) > 0
            if skill_key in SKILL_BURST_MECHANIC_MODE_ATTACKS:
                return mechanic_mode_active
            if mechanic_mode_active and (
                skill_key in SKILL_BURST_ENGINEER_SUMMONS
                or skill_key == self._skill_burst_reload_skill()
                or self._skill_burst_ammo_cost(skill_key) > 0
            ):
                return False
        fury_cost, _fury_multiplier = self._skill_burst_fury_consumer(skill_key)
        if fury_cost > 0 and skill_key != '旋转冲击' and display_value(active_attrs or {}, SKILL_BURST_FURY_MARKER) < fury_cost:
            return False
        guardian_cost = self._skill_burst_guardian_phantom_cost(skill_key)
        if guardian_cost > 0 and display_value(active_attrs or {}, SKILL_BURST_GUARDIAN_PHANTOM_MARKER) < guardian_cost:
            return False
        if job in {"重炮手", "机械大师"} and skill_key == "重磅炸弹":
            return (
                display_value(active_attrs or {}, SKILL_BURST_HEAVY_DUCK_COUNT_MARKER)
                >= self._skill_burst_heavy_duck_target_count()
            )
        if job == "圣徒" and skill_key == "冲击之手":
            return display_value(active_attrs or {}, SKILL_BURST_HOLY_RELIC_COUNT_MARKER) >= 1
        if job == "雷神" and skill_key in SKILL_BURST_HOLY_GROUND_FOLLOWUPS:
            return bool(
                display_value(
                    active_attrs or {},
                    SKILL_BURST_HOLY_GROUND_REQUIREMENT_MARKER,
                )
                > 0
            )
        if job in {"噬魂者", "黑暗萨满"} and skill_key == "召唤傀儡":
            return display_value(active_attrs or {}, SKILL_BURST_BRAND_MARKER) > 0
        if job in {"刀锋舞者", "灵魂舞者"} and skill_key == "九天之舞2阶":
            return display_value(active_attrs or {}, SKILL_BURST_DANCE_STAGE_MARKER) > 0
        return True
    def _skill_burst_brand_damage_factor(
        self,
        skill: str,
        active_attrs: dict[str, Any] | None,
    ) -> float:
        job = norm(self.second_job.currentText())
        skill_key = self._skill_burst_skill_key(skill)
        if job not in {"噬魂者", "黑暗萨满"} or skill_key not in {"灵魂之门", "灵魂之纸"}:
            return 1.0
        if display_value(active_attrs or {}, SKILL_BURST_BRAND_MARKER) > 0:
            return 1.0
        if skill_key == "灵魂之门":
            return 0.5
        # 灵魂之纸按未烙印时少一枚符咒还原；两个二转的EX数量不同。
        return 7 / 8 if job == "噬魂者" else 4 / 5
    def _skill_burst_external_same_effect_active_until(
        self,
        skill: Any,
        seconds: float,
    ) -> float:
        """Return when a teammate's currently active same-name effect ends."""
        skill_key = self._skill_burst_skill_key(skill)
        if not skill_key:
            return 0.0
        current = max(0.0, as_number(seconds))
        current_provider = norm(
            getattr(self, "_skill_burst_current_provider_override", "")
        )
        active_until = 0.0
        windows = getattr(self, "_skill_burst_external_team_effect_windows", [])
        cached = getattr(
            self, "_skill_burst_external_effect_window_index", None
        )
        if not (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is windows
            and isinstance(cached[1], dict)
        ):
            index: dict[str, list[tuple[float, float, str]]] = {}
            for row in windows:
                if not isinstance(row, dict):
                    continue
                start = max(0.0, as_number(row.get("start")))
                end = max(start, as_number(row.get("end")))
                effect = row.get("team_effect", {})
                effect = effect if isinstance(effect, dict) else {}
                provider = norm(effect.get("provider") or row.get("provider"))
                names = (
                    row.get("source"),
                    row.get("skill"),
                    row.get("name"),
                    effect.get("skill"),
                )
                candidates = {
                    self._skill_burst_skill_key(norm(name).split("｜")[-1])
                    for name in names
                    if norm(name)
                }
                for candidate in candidates:
                    if candidate:
                        index.setdefault(candidate, []).append(
                            (start, end, provider)
                        )
            cached = (windows, index)
            self._skill_burst_external_effect_window_index = cached
        for start, end, provider in cached[1].get(skill_key, ()):
            if not (start <= current + 1e-6 < end - 1e-6):
                continue
            if current_provider and provider == current_provider:
                continue
            active_until = max(active_until, end)
        return active_until

    def _skill_burst_acceleration_resistance_active_until(
        self,
        skill: Any,
        seconds: float,
    ) -> float:
        """Return the end of the active resistance lock for ``skill``'s group.

        Team simulations include every member's authoritative resistance rows.
        A member must ignore the row created by its own currently pinned cast,
        while standalone simulations treat every supplied row as external.
        """

        group = acceleration_resistance_group(skill)
        if not group:
            return 0.0
        current = max(0.0, as_number(seconds))
        current_provider = norm(
            getattr(self, "_skill_burst_current_provider_override", "")
        )
        active_until = 0.0
        for row in getattr(self, "_skill_burst_resistance_windows", ()):
            if not isinstance(row, dict):
                continue
            row_group = norm(row.get("group")) or acceleration_resistance_group(
                row.get("source") or row.get("skill")
            )
            if row_group != group:
                continue
            provider = norm(row.get("provider"))
            if current_provider and provider == current_provider:
                continue
            start = max(0.0, as_number(row.get("start")))
            end = max(start, as_number(row.get("end")))
            mode = (
                "burst" if group == BURST_RESISTANCE_GROUP else "accel"
            )
            if self._skill_burst_external_interval_suppressed(mode, start):
                continue
            if start <= current + 1e-9 and current < end - 1e-9:
                active_until = max(active_until, end)
        return active_until

    def _skill_burst_has_weighted_team_objective(self) -> bool:
        objective_profile = getattr(
            self,
            "_skill_burst_team_objective_profile",
            None,
        )
        return bool(
            isinstance(objective_profile, dict)
            and any(
                isinstance(member, dict)
                and as_number(member.get("weight")) > 1e-9
                for member in objective_profile.get("members", ())
            )
        )

    def _skill_burst_uses_dynamic_support_order(self) -> bool:
        """Use one measured objective for personal and party timelines.

        The team profile is an optional extension of the owner's objective,
        not the switch that enables objective-based ordering.  Scheme 1 can
        already measure the owner's damage gained from buffs/debuffs, so a
        missing (or zero-weight) party profile must not fall back to the old
        profession opener table.  The two disable flags are retained only for
        A/B regression audits and old-axis comparison.
        """

        return bool(
            self._skill_burst_new_scheme_1_enabled()
            and not (
                getattr(
                    self,
                    "_skill_burst_disable_team_objective_opening_order",
                    False,
                )
                and getattr(
                    self,
                    "_skill_burst_disable_team_objective_pair_order",
                    False,
                )
            )
        )

    def _skill_burst_next_opening_support(
        self,
        support_actions: list[dict[str, Any]],
        used_skills: set[str],
        support_available_at: dict[int, float] | None = None,
        current: float = 0.0,
    ) -> str:
        # Scheme 1 gives every legal Buff/Debuff an owner-relative value.  A
        # team profile merely adds the weighted teammate gain to that same
        # value.  Do not let a fixed profession opener override either the
        # personal or team objective; keep it only behind the audit switch.
        if (
            self._skill_burst_uses_dynamic_support_order()
            and not getattr(
                self,
                "_skill_burst_disable_team_objective_opening_order",
                False,
            )
        ):
            return ""
        current_time = max(0.0, as_number(current))
        available_indexes: dict[str, list[int]] = {}
        for index, action in enumerate(support_actions):
            skill_key = self._skill_burst_skill_key(action.get("skill"))
            if skill_key:
                available_indexes.setdefault(skill_key, []).append(index)

        for skill in SKILL_BURST_OPENING_SUPPORT_BY_JOB.get(norm(self.second_job.currentText()), ()):
            skill_key = self._skill_burst_skill_key(skill)
            if (
                skill_key in used_skills
                or self._skill_burst_external_same_effect_active_until(
                    skill_key, current_time
                )
                > current_time + 1e-6
            ):
                continue
            indexes = available_indexes.get(skill_key, [])
            if not indexes:
                continue
            if support_available_at is not None and not any(
                as_number(support_available_at.get(index)) <= current_time + 1e-6
                for index in indexes
            ):
                # Duplicate team buffs are deliberately staggered. An unavailable
                # earlier opener must not block a later ready setup buff while the
                # first provider is taking its turn.
                continue
            return skill_key
        return ""


def bind_host_globals(host_globals: dict[str, object]) -> None:
    protected = {'bind_host_globals', 'SkillTimelineScheduleMixin'}
    for name, value in host_globals.items():
        if name.startswith('__') or name in protected:
            continue
        globals()[name] = value


# Extracted from skill_timeline_engine.py; algorithm bodies are unchanged.
