from __future__ import annotations

from functools import lru_cache

class SkillTimelineOutputMixin:
    def _skill_burst_summon_duration(self, skill: str) -> float:
        skill_key = self._skill_burst_skill_key(skill)
        if skill_key in SKILL_REFRESHABLE_PERIODIC_DURATIONS:
            return self._skill_glyph_buff_duration(
                skill_key,
                max(0.0, as_number(SKILL_REFRESHABLE_PERIODIC_DURATIONS.get(skill_key))),
            )
        summon_limit = self._skill_burst_summon_limit(skill)
        if summon_limit:
            return summon_limit[1]
        return max(0.0, as_number(self._skill_summon_min_cooldown(skill_key)))
    @staticmethod
    def _skill_burst_is_refreshable_periodic_damage(skill: str) -> bool:
        return BuildEditor._skill_burst_skill_key(skill) in SKILL_BURST_REFRESHABLE_PERIODIC_DAMAGE
    def _skill_burst_apply_ripper_successive_scores(self, segments: list[dict[str, Any]]) -> None:
        """Resolve each cast against its immediate, gapless predecessor.

        The editable default uses the successive-cast coefficient (1.1).
        Preserve explicit user scaling by applying only the 1.3/1.1 ratio.
        Repeated finalization/reordering must not compound this adjustment.
        """
        if norm(self.second_job.currentText()) != "烈":
            return
        previous = None
        for segment in sorted(segments, key=lambda row: (as_number(row.get("start")), as_number(row.get("end")))):
            if segment.get("timeline_hidden") or norm(segment.get("kind")) == "invalid":
                continue
            key = self._skill_burst_skill_key(segment.get("skill", ""))
            if not key:
                continue
            if key in {"极度打击", "极度打击EX", "极度打击 EX"}:
                consecutive = previous is not None and self._skill_burst_skill_key(previous.get("skill", "")) in {"极度打击", "极度打击EX", "极度打击 EX"} and abs(as_number(segment.get("start")) - as_number(previous.get("end"))) <= 1e-6
                factor = 1.0 if consecutive else 1.3 / 1.1
                old_factor = max(1e-9, as_number(segment.get("ripper_successive_factor"), 1.0))
                ratio = factor / old_factor
                for field in ("effective_score", "raw_effective_score", "dpa"):
                    if field in segment:
                        segment[field] = as_number(segment[field]) * ratio
                if isinstance(segment.get("channel_scores"), dict):
                    segment["channel_scores"] = {channel: as_number(value) * ratio for channel, value in segment["channel_scores"].items()}
                segment["ripper_successive_factor"] = factor
                segment["ripper_route_multiplier"] = 1.1 if consecutive else 1.3
                detail = norm(segment.get("detail"))
                old_note = segment.get("ripper_successive_note", "")
                if old_note:
                    detail = "；".join(part for part in detail.split("；") if part != old_note)
                note = "连续极度打击EX：1.1倍" if consecutive else "单放/首发极度打击EX：1.3倍"
                segment["ripper_successive_note"] = note
                segment["detail"] = "；".join(filter(None, [detail, note]))
            previous = segment

    def _skill_burst_apply_refreshable_periodic_scores(
        self,
        segments: list[dict[str, Any]],
        window: float,
    ) -> None:
        self._skill_burst_apply_ripper_successive_scores(segments)
        grouped: dict[str, list[int]] = defaultdict(list)
        for index, segment in enumerate(segments):
            skill_key = self._skill_burst_skill_key(segment.get("skill"))
            if skill_key in SKILL_BURST_REFRESHABLE_PERIODIC_DAMAGE:
                grouped[skill_key].append(index)
        for skill_key, indexes in grouped.items():
            indexes.sort(key=lambda idx: as_number(segments[idx].get("end")))
            duration = self._skill_burst_summon_duration(skill_key)
            if duration <= 0:
                continue
            for order, index in enumerate(indexes):
                segment = segments[index]
                effect_start = max(as_number(segment.get("start")), as_number(segment.get("end")))
                next_effect_start = (
                    max(as_number(segments[indexes[order + 1]].get("start")), as_number(segments[indexes[order + 1]].get("end")))
                    if order + 1 < len(indexes)
                    else float("inf")
                )
                original_active = max(0.0, min(duration, as_number(window) - effect_start))
                active_until = min(as_number(window), effect_start + duration, next_effect_start)
                active_seconds = self._skill_burst_active_duration(effect_start, active_until)
                ratio = active_seconds / original_active if original_active > 1e-9 else 0.0
                if abs(ratio - 1.0) <= 1e-9:
                    continue
                for key in ("effective_score", "raw_effective_score"):
                    segment[key] = max(0.0, as_number(segment.get(key)) * ratio)
                channel_scores = segment.get("channel_scores")
                if isinstance(channel_scores, dict):
                    segment["channel_scores"] = {
                        key: max(0.0, as_number(value) * ratio)
                        for key, value in channel_scores.items()
                    }
                cast_time = max(0.0, as_number(segment.get("end")) - as_number(segment.get("start")))
                if cast_time > 0:
                    segment["dpa"] = max(0.0, as_number(segment.get("effective_score")) / cast_time)
                detail = norm(segment.get("detail"))
                refresh_detail = f"持续伤害结算{active_seconds:g}/{duration:g}s，提前补只刷新快照不叠加"
                segment["detail"] = "；".join(filter(None, [detail, refresh_detail]))
    def _skill_burst_window_metric_score(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
        seconds: float,
        window: float | None,
        cast_time: float | None = None,
    ) -> float:
        score = self._skill_burst_full_metric_score(metric, active_attrs, seconds)
        return self._skill_burst_prorate_window_score(
            score,
            metric,
            active_attrs,
            seconds,
            window,
            cast_time,
        )
    def _skill_burst_window_raw_metric_score(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
        seconds: float,
        window: float | None,
        cast_time: float | None = None,
    ) -> float:
        raw_score = max(
            0.0,
            as_number(
                metric.get("timeline_raw_score")
                if norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
                and metric.get("timeline_attack_scores")
                else metric.get("raw_score")
            ),
        )
        # Action speed changes the number of actual hits/projectiles, so it is
        # part of the timeline's damage baseline rather than a panel buff.
        raw_score *= self._skill_burst_damage_multiplier(
            metric.get("skill", ""),
            seconds,
            active_attrs,
        )
        if (
            norm(self.second_job.currentText()) == "圣徒"
            and self._skill_burst_skill_key(metric.get("skill")) == "冲击之手"
            and active_attrs is not None
        ):
            relic_count = max(
                0.0,
                display_value(active_attrs, SKILL_BURST_HOLY_RELIC_COUNT_MARKER),
            )
            # The DPS table stores the three-relic baseline. Keep the raw/base
            # timeline display on the same per-relic rule as effective damage.
            raw_score *= relic_count / 3.0
        if norm(self.second_job.currentText()) == "火舞":
            base_multiplier = max(0.0, as_number(metric.get("base_multiplier")))
            base_bonus = max(0.0, as_number(metric.get("burning_hand_base_bonus")))
            current_bonus = self._skill_burning_hand_bonus_for_mode(
                metric.get("skill", ""),
                self._skill_burning_hand_mode_at(max(0.0, as_number(seconds))),
            )
            baseline_total = base_multiplier + base_bonus
            current_total = base_multiplier + current_bonus
            if baseline_total > 1e-9:
                raw_score *= current_total / baseline_total
        return self._skill_burst_prorate_window_score(
            raw_score,
            metric,
            active_attrs,
            seconds,
            window,
            cast_time,
        )
    def _skill_burst_prorate_window_score(
        self,
        score: float,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
        seconds: float,
        window: float | None,
        cast_time: float | None = None,
    ) -> float:
        if window is None:
            return score
        actual_cast_time = (
            self._skill_burst_sim_cast_time(metric.get("skill", ""), seconds, active_attrs)
            if cast_time is None
            else max(0.0, as_number(cast_time))
        )
        summon_duration = self._skill_burst_summon_duration(metric.get("skill", ""))
        # The duck metric is the egg impact, not its later autonomous attacks.
        # Its summon lifetime must not dilute that direct damage near the end.
        if self._skill_burst_skill_key(metric.get("skill", "")) == "重磅鸭":
            summon_duration = 0.0
        if summon_duration > 0:
            effect_start = max(0.0, as_number(seconds) + actual_cast_time)
            effect_end = min(
                max(0.0, as_number(window)),
                effect_start + summon_duration,
            )
            active_seconds = self._skill_burst_active_duration(
                effect_start,
                effect_end,
            )
            full_duration_score = score
            skill_key = self._skill_burst_skill_key(metric.get("skill", ""))
            if skill_key in SKILL_BURST_REFRESHABLE_PERIODIC_DAMAGE:
                base_duration = max(
                    0.0,
                    as_number(SKILL_REFRESHABLE_PERIODIC_DURATIONS.get(skill_key)),
                )
                if base_duration > 0:
                    full_duration_score *= summon_duration / base_duration
            return full_duration_score * min(1.0, active_seconds / summon_duration)
        if actual_cast_time <= 0:
            return 0.0 if self._skill_burst_time_is_blocked(seconds) else score
        cast_end = min(
            max(0.0, as_number(window)),
            max(0.0, as_number(seconds)) + actual_cast_time,
        )
        cast_seconds_in_window = self._skill_burst_active_duration(
            seconds,
            cast_end,
        )
        utilization = max(
            0.0,
            min(1.0, cast_seconds_in_window / actual_cast_time),
        )
        if utilization <= 1e-12:
            return 0.0
        skill = metric.get("skill", "")
        action_output_multiplier = self._skill_burst_action_output_multiplier(
            skill,
            seconds,
            active_attrs,
        )
        damage_fraction = stance_damage_fraction_for_utilization(
            norm(self.second_job.currentText()),
            self._skill_burst_skill_key(skill),
            utilization,
            action_output_multiplier,
        )
        return score * damage_fraction
    @staticmethod
    def _skill_burst_element_key(element: str) -> str:
        element_text = norm(element)
        return element_text if element_text in {"火", "水", "光", "暗"} else "无"
    def _skill_burst_scaled_channel_scores(
        self,
        metric: dict[str, Any] | None,
        raw_score: float,
    ) -> dict[str, float]:
        score = max(0.0, as_number(raw_score))
        if score <= 0:
            return {}
        element = norm((metric or {}).get("element"))
        if element in {"火", "水", "光", "暗"}:
            return {element: score}
        source = metric.get("channel_scores", {}) if isinstance(metric, dict) else {}
        if (
            isinstance(metric, dict)
            and norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
            and isinstance(metric.get("timeline_attack_scores"), dict)
        ):
            source = metric.get("timeline_attack_scores", {})
        if isinstance(source, dict):
            source_total = sum(max(0.0, as_number(value)) for value in source.values())
            if source_total > 0:
                return {
                    channel: score * max(0.0, as_number(value)) / source_total
                    for channel, value in source.items()
                    if channel in SKILL_DAMAGE_CHANNEL_ORDER and as_number(value) > 0
                }
        profile = self._job_profile()
        return {"魔法" if profile.get("attack_attr") == "魔攻" else "物理": score}
    def _skill_burst_timeline_attr_windows(
        self,
        segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        *,
        include_periodic_display_attrs: bool = True,
    ) -> list[dict[str, Any]]:
        action_by_skill = {
            self._skill_burst_skill_key(action.get("skill")): action
            for action in support_actions
            if as_number(action.get("duration")) > 0
        }
        windows: list[dict[str, Any]] = []
        for segment in segments:
            if segment.get("kind") == "passive":
                continue
            action = action_by_skill.get(self._skill_burst_skill_key(segment.get("skill")))
            if not action:
                continue
            if action.get("state_only") and not action.get("periodic_buff"):
                continue
            duration = max(0.0, as_number(action.get("duration")))
            cast_start = max(0.0, as_number(segment.get("start")))
            cast_end = max(cast_start, as_number(segment.get("end")))
            start, raw_end, refresh_tail = team_effect_application_window_for_attrs(
                action.get("skill", ""),
                action.get("attrs", {}),
                cast_start,
                cast_end,
                duration,
                starts_on_cast=self._skill_burst_action_starts_on_cast(action),
                triggered_only=bool(action.get("triggered_only")),
            )
            end = min(window, raw_end)
            refresh_interval = max(0.0, as_number(action.get("refresh_interval")))
            refresh_source_end = end
            refresh_effect_duration = 0.0
            if (
                self._skill_burst_skill_key(action.get("skill")) == "痊愈之手"
                and refresh_interval > 0
                and duration > 0
            ):
                refresh_source_end = min(window, cast_end + duration)
                refresh_effect_duration = duration
                last_refresh = max(start, refresh_source_end - refresh_interval)
                end = min(window, last_refresh + refresh_effect_duration)
            if action.get("aura_toggle"):
                end = min(
                    [
                        max(start, as_number(other.get("start")))
                        for other in segments
                        if norm(other.get("skill")) == "取消机械模式"
                        and as_number(other.get("start")) > start + 1e-6
                    ]
                    or [window]
                )
            if end <= start + 1e-6:
                continue
            window_action = action
            if (
                include_periodic_display_attrs
                and action.get("periodic_buff")
                and isinstance(action.get("display_attrs"), dict)
            ):
                dynamic_attrs = dict(action.get("attrs", {}))
                dynamic_attrs.update({
                    norm(key): as_number(value)
                    for key, value in action.get("display_attrs", {}).items()
                    if norm(key) and abs(as_number(value)) > 1e-9
                })
                window_action = {**action, "attrs": dynamic_attrs}
            window_row = {
                "start": start,
                "end": end,
                "action": window_action,
                "refresh_interval": refresh_interval,
                "refresh_tail": refresh_tail,
                "cast_start": cast_start,
                "cast_end": cast_end,
            }
            if refresh_effect_duration > 0:
                window_row["refresh_source_end"] = refresh_source_end
                window_row["refresh_effect_duration"] = refresh_effect_duration
            windows.append(window_row)
        if self._skill_burst_is_sitting_yao():
            chakra_action = action_by_skill.get("查克拉治愈术")
            windows = [
                row for row in windows
                if self._skill_burst_skill_key(row.get("action", {}).get("skill")) != "查克拉治愈术"
            ]
            if chakra_action:
                occupied = sorted(
                    (
                        max(0.0, as_number(segment.get("start"))),
                        min(window, max(0.0, as_number(segment.get("end")))),
                    )
                    for segment in segments
                    if not segment.get("timeline_hidden")
                    and segment.get("kind") != "passive"
                    and self._skill_burst_skill_key(segment.get("skill")) != "查克拉治愈术"
                    and as_number(segment.get("end")) > as_number(segment.get("start")) + 1e-6
                )
                sitting_intervals: list[tuple[float, float]] = []
                cursor = 0.0
                for cast_start, cast_end in occupied:
                    if cast_start > cursor + 1e-6:
                        sitting_intervals.append((
                            cursor,
                            min(window, cast_start + YAO_MEDITATION_LINGER_SECONDS),
                        ))
                    cursor = max(cursor, cast_end)
                if cursor < window - 1e-6:
                    sitting_intervals.append((cursor, window))
                merged_intervals: list[list[float]] = []
                for start, end in sitting_intervals:
                    if end <= start + 1e-6:
                        continue
                    if merged_intervals and start <= merged_intervals[-1][1] + 1e-6:
                        merged_intervals[-1][1] = max(merged_intervals[-1][1], end)
                    else:
                        merged_intervals.append([start, end])
                windows.extend(
                    {
                        "start": start,
                        "end": end,
                        "action": chakra_action,
                        "refresh_interval": 0.5,
                    }
                    for start, end in merged_intervals
                )
        return windows
    def _skill_burst_active_timeline_window_indexes(
        self,
        rows: list[dict[str, Any]],
        current: float,
        cache_namespace: str,
    ) -> tuple[int, ...]:
        """Return active window indexes through a reusable interval sweep."""

        owner_attr = f"_skill_burst_{cache_namespace}_window_index_owner"
        value_attr = f"_skill_burst_{cache_namespace}_window_index_value"
        if getattr(self, owner_attr, None) is not rows:
            events: dict[float, tuple[list[int], list[int]]] = {}
            for index, row in enumerate(rows):
                start = max(0.0, as_number(row.get("start")))
                end = max(start, as_number(row.get("end")))
                if end <= start + 1e-9:
                    continue
                starts, _ends = events.setdefault(start, ([], []))
                starts.append(index)
                _starts, ends = events.setdefault(end, ([], []))
                ends.append(index)
            points = tuple(sorted(events))
            active: set[int] = set()
            active_rows: list[tuple[int, ...]] = []
            for point in points:
                starts, ends = events[point]
                active.difference_update(ends)
                active.update(starts)
                active_rows.append(tuple(sorted(active)))
            setattr(self, owner_attr, rows)
            setattr(self, value_attr, (points, tuple(active_rows)))
        points, active_rows = getattr(self, value_attr, ((), ()))
        if not points:
            return ()
        index = bisect_right(points, max(0.0, as_number(current)) + 1e-6) - 1
        if index < 0 or index >= len(active_rows):
            return ()
        return active_rows[index]
    def _skill_burst_timeline_cdr_rows(
        self, rows: list[dict[str, Any]], *, external: bool,
    ) -> tuple[tuple[float, float, float, str], ...]:
        """Index immutable completed timeline windows once, in original order.

        These lists belong to one finished timeline, like the interval sweep
        immediately above. Replacement/append and receiver clear times invalidate
        the index; local special timing remains excluded, external timing stays.
        """
        attr = "_skill_burst_external_output_cdr_index" if external else "_skill_burst_local_output_cdr_index"
        clears = self._skill_burst_player_buff_clear_times()
        cached = getattr(self, attr, None)
        if cached is not None and cached[0] is rows and cached[1] == len(rows) and cached[2] == clears:
            return cached[3]
        result = []
        for row in rows:
            effect = row.get("team_effect" if external else "action", {})
            start = max(0.0, as_number(row.get("start")))
            end = max(start, as_number(row.get("end")))
            value = max(0.0, as_number(effect.get("attrs", {}).get("冷却缩减%")))
            if value <= 0 or (not external and effect.get("special_cdr")):
                continue
            for clipped_start, clipped_end in self._skill_burst_clip_clearable_buff_intervals(((start, end),), effect):
                result.append((clipped_start, clipped_end, value,
                    self._skill_burst_cdr_source_key(effect, "external-window" if external else "local-window")))
        immutable = tuple(result)
        setattr(self, attr, (rows, len(rows), clears, immutable))
        return immutable

    def _skill_burst_active_attrs_from_timeline_windows(
        self,
        attr_windows: list[dict[str, Any]],
        current: float,
        exclude_skills: set[str] | None = None,
        external_min_start: float | None = None,
        include_cdr_windows: bool = True,
        runtime_effect_cache: dict | None = None,
    ) -> dict[str, float]:
        refresh_fallbacks = {
            "查克拉治愈术": 0.5,
            "怨恨之像": 0.5,
            "阿兹那之息": 5.0,
            "痊愈之手": 2.0,
        }
        # The merged attributes depend on active mechanical state, not the
        # query time. Cache them for cast lookups too; future CDR windows are
        # time-dependent and must be attached separately on every lookup.
        state_cache = getattr(self, "_skill_burst_periodic_attr_state_cache", None)
        if not isinstance(state_cache, dict):
            state_cache = {}
            self._skill_burst_periodic_attr_state_cache = state_cache
        active_effects: list[dict[str, Any]] = []
        freeze_sources: dict[str, float] = {}
        for index in self._skill_burst_active_timeline_window_indexes(
            attr_windows, current, "local_attr"
        ):
            row = attr_windows[index]
            start = max(0.0, as_number(row.get("start")))
            end = max(start, as_number(row.get("end")))
            if not (start <= current + 1e-6 < end):
                continue
            action = row.get("action", {})
            if not isinstance(action, dict):
                continue
            skill_key = self._skill_burst_skill_key(action.get("skill"))
            if self._skill_burst_action_has_freeze(action):
                source = self._skill_burst_freeze_source(action)
                freeze_sources[source] = max(
                    freeze_sources.get(source, 0.0),
                    self._skill_burst_freeze_layers(action),
                )
            if skill_key in (exclude_skills or set()):
                continue
            action_attrs = action.get("attrs", {})
            if self._skill_burst_special_timing_is_pinned(action):
                action_attrs = {
                    key: value
                    for key, value in action_attrs.items()
                    if norm(key) not in {"冷却缩减%", "动作速度%", "攻速%"}
                }
            applied_at = start
            if external_min_start is not None and start <= external_min_start + 1e-6:
                if row.get("single_refresh_application"):
                    continue
                refresh_interval = max(
                    0.0,
                    as_number(
                        row.get("refresh_interval"),
                        refresh_fallbacks.get(skill_key, 0.0),
                    ),
                )
                if refresh_interval <= 0:
                    refresh_interval = refresh_fallbacks.get(skill_key, 0.0)
                if refresh_interval <= 0:
                    continue
                refresh_source_end = max(
                    start,
                    as_number(row.get("refresh_source_end"), end),
                )
                refresh_sample = min(current, max(start, refresh_source_end - 1e-6))
                refresh_count = max(0, int((refresh_sample - start) // refresh_interval))
                applied_at = start + refresh_count * refresh_interval
                if applied_at <= external_min_start + 1e-6:
                    continue
            action_attrs = self._skill_burst_attrs_after_player_buff_clear(
                action,
                action_attrs,
                applied_at,
                current,
            )
            if not action_attrs:
                continue
            effect_key = (
                False, index, applied_at,
                tuple((repr(key), repr(value)) for key, value in action_attrs.items()),
            ) if runtime_effect_cache is not None else None
            runtime_effect = (
                runtime_effect_cache.get(effect_key)
                if effect_key is not None else None
            )
            if runtime_effect is None:
                runtime_effect = {
                    "_skill_burst_runtime_effect": True,
                    "row": action.get("order", index),
                    "provider": "",
                    "skill": action.get("skill", ""),
                    "level": "",
                    "attrs": action_attrs,
                    "cooldown": 0,
                    "duration": 0,
                    "usage_ratio": 100,
                    "coverage": 1,
                    "applied_at": applied_at,
                    "effective_attrs": action_attrs,
                }
                if effect_key is not None:
                    if len(runtime_effect_cache) >= 16384:
                        runtime_effect_cache.clear()
                    runtime_effect_cache[effect_key] = runtime_effect
            active_effects.append(runtime_effect)

        external_windows = getattr(self, "_skill_burst_external_team_effect_windows", [])
        external_rows = external_windows if isinstance(external_windows, list) else []
        for external_index in self._skill_burst_active_timeline_window_indexes(
            external_rows, current, "external_attr"
        ):
            window_row = external_rows[external_index]
            start = max(0.0, as_number(window_row.get("start")))
            end = max(start, as_number(window_row.get("end")))
            if not (start <= current + 1e-6 < end):
                continue
            effect = window_row.get("team_effect", {})
            if not isinstance(effect, dict):
                continue
            skill_key = self._skill_burst_skill_key(effect.get("skill"))
            if skill_key in (exclude_skills or set()):
                continue
            applied_at = start
            if external_min_start is not None and start <= external_min_start + 1e-6:
                if window_row.get("single_refresh_application"):
                    continue
                refresh_interval = max(
                    0.0,
                    as_number(
                        window_row.get("refresh_interval"),
                        refresh_fallbacks.get(skill_key, 0.0),
                    ),
                )
                if refresh_interval <= 0:
                    refresh_interval = refresh_fallbacks.get(skill_key, 0.0)
                if refresh_interval <= 0:
                    continue
                refresh_source_end = max(
                    start,
                    as_number(window_row.get("refresh_source_end"), end),
                )
                refresh_sample = min(current, max(start, refresh_source_end - 1e-6))
                refresh_count = max(0, int((refresh_sample - start) // refresh_interval))
                applied_at = start + refresh_count * refresh_interval
                if applied_at <= external_min_start + 1e-6:
                    continue
            effect_attrs = self._skill_burst_attrs_after_player_buff_clear(
                effect,
                effect.get("attrs", {}),
                applied_at,
                current,
            )
            if not effect_attrs:
                continue
            effect_key = (
                True, external_index, applied_at,
                tuple((repr(key), repr(value)) for key, value in effect_attrs.items()),
            ) if runtime_effect_cache is not None else None
            runtime_effect = (
                runtime_effect_cache.get(effect_key)
                if effect_key is not None else None
            )
            if runtime_effect is None:
                runtime_effect = {
                    **effect,
                    "attrs": effect_attrs,
                    "_skill_burst_runtime_effect": True,
                    "cooldown": 0.0,
                    "duration": 1.0,
                    "coverage": 1.0,
                    "applied_at": applied_at,
                    "effective_attrs": effect_attrs,
                }
                if effect_key is not None:
                    if len(runtime_effect_cache) >= 16384:
                        runtime_effect_cache.clear()
                    runtime_effect_cache[effect_key] = runtime_effect
            active_effects.append(runtime_effect)
        if not active_effects and not external_windows:
            return {}

        holy_ground_recent = self._skill_burst_holy_ground_recent(
            active_effects,
            current,
        )
        base_replacement = max(
            0.0,
            as_number(
                getattr(
                    self,
                    "_skill_burst_team_base_replacement_multiplier",
                    1.0,
                ),
                1.0,
            ),
        )
        cache_key = (
            self._skill_burst_combined_effect_state_signature(active_effects),
            tuple(sorted(freeze_sources.items())),
            tuple(sorted(exclude_skills or ())),
            holy_ground_recent,
            repr(base_replacement),
        )
        if cache_key in state_cache:
            merged = dict(state_cache[cache_key])
            if include_cdr_windows:
                cdr_windows = [
                    row for rows, external in ((attr_windows, False), (external_rows, True))
                    for row in self._skill_burst_timeline_cdr_rows(rows, external=external)
                    if row[1] > current + 1e-6
                ]
                if cdr_windows:
                    merged["__cdr_windows__"] = cdr_windows
            return merged

        merged = self._skill_burst_cached_merged_team_attrs(active_effects)
        active_crit_down = self._skill_burst_active_crit_resist_reduction(active_effects)
        if active_crit_down > 0:
            merged["减致命抵抗%"] = active_crit_down
        for effect in active_effects:
            for key, value in effect.get("attrs", {}).items():
                marker = norm(key)
                if marker.startswith("__") and abs(as_number(value)) > 1e-9:
                    merged[marker] = max(as_number(merged.get(marker)), as_number(value))
        active_cdr = self._skill_burst_active_cdr_total(active_effects)
        if active_cdr > 0:
            merged["冷却缩减%"] = min(70.0, active_cdr)
        self._skill_burst_set_team_panel_multipliers(merged, active_effects, base_replacement)

        if freeze_sources:
            merged["冰冻层"] = min(2.0, sum(freeze_sources.values()))
            for source, layers in freeze_sources.items():
                merged[f"__冰冻来源__{source}"] = layers
        if holy_ground_recent:
            merged[SKILL_BURST_HOLY_GROUND_REQUIREMENT_MARKER] = 1.0
        state_cache[cache_key] = dict(merged)
        while len(state_cache) > 32768:
            state_cache.pop(next(iter(state_cache)))
        if include_cdr_windows:
            cdr_windows = [
                row for rows, external in ((attr_windows, False), (external_rows, True))
                for row in self._skill_burst_timeline_cdr_rows(rows, external=external)
                if row[1] > current + 1e-6
            ]
            if cdr_windows:
                merged["__cdr_windows__"] = cdr_windows
        return merged
    def _skill_burst_apply_periodic_total_damage_scores(
        self,
        segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> None:
        periodic_segments = [
            segment for segment in segments
            if self._skill_periodic_total_damage_cycle(segment.get("skill")) > 0
            and isinstance(segment.get("periodic_metric"), dict)
        ]
        if not periodic_segments:
            return

        # Resolve summon capacity on the same cast instances that will be
        # scored.  When a full-cap summon is refreshed, its oldest instance
        # stops dealing damage at the new summon's activation time.
        replacement_end_by_id: dict[int, float] = {}
        removal_end_by_id: dict[int, float] = {}
        summon_records: list[dict[str, Any]] = []
        for order, segment in enumerate(periodic_segments):
            skill_key = self._skill_burst_skill_key(segment.get("skill"))
            if segment.get("kind") == "passive":
                continue
            summon_limit = self._skill_burst_summon_limit(skill_key)
            shared_group = self._skill_burst_shared_summon_group(skill_key)
            if not summon_limit and not shared_group:
                continue
            active_start = max(0.0, as_number(segment.get("end")))
            active_duration = self._skill_burst_summon_duration(skill_key)
            if active_duration <= 0:
                continue
            summon_records.append({
                "segment": segment,
                "skill": skill_key,
                "start": active_start,
                "natural_end": active_start + active_duration,
                "order": order,
            })
        summon_records.sort(key=lambda row: (row["start"], row["order"]))
        active_records: list[dict[str, Any]] = []

        def record_end(row: dict[str, Any]) -> float:
            segment_id = id(row["segment"])
            return min(
                as_number(row.get("natural_end")),
                replacement_end_by_id.get(segment_id, float("inf")),
                removal_end_by_id.get(segment_id, float("inf")),
            )

        def evict_oldest(
            matches: Callable[[dict[str, Any]], bool],
            end_at: float,
        ) -> None:
            matching = [row for row in active_records if matches(row)]
            if not matching:
                return
            victim = min(matching, key=lambda row: (row["start"], row["order"]))
            replacement_end_by_id[id(victim["segment"])] = min(
                replacement_end_by_id.get(id(victim["segment"]), float("inf")),
                end_at,
            )
            active_records.remove(victim)

        summon_lifecycle_rows: list[tuple[float, int, int, str, Any]] = [
            (
                as_number(record.get("start")),
                1,
                int(as_number(record.get("order"))),
                "cast",
                record,
            )
            for record in summon_records
        ]
        summon_lifecycle_rows.extend(
            (removed_at, 0, order, "remove", skill_key)
            for order, (removed_at, skill_key) in enumerate(
                self._skill_burst_summon_removal_schedule()
            )
            if 0.0 <= removed_at <= max(0.0, as_number(window)) + 1e-6
        )
        summon_lifecycle_rows.sort(key=lambda row: (row[0], row[1], row[2]))
        for current_start, _priority, _order, lifecycle_kind, payload in summon_lifecycle_rows:
            active_records[:] = [
                row for row in active_records
                if record_end(row) > current_start + 1e-6
            ]
            if lifecycle_kind == "remove":
                matching = [
                    row
                    for row in active_records
                    if row.get("skill") == payload
                    and as_number(row.get("start")) <= current_start + 1e-6
                ]
                if matching:
                    victim = min(
                        matching,
                        key=lambda row: (row["start"], row["order"]),
                    )
                    removal_end_by_id[id(victim["segment"])] = current_start
                    active_records.remove(victim)
                continue
            current_record = payload
            current_skill = norm(current_record.get("skill"))
            summon_limit = self._skill_burst_summon_limit(current_skill)
            if summon_limit:
                while sum(row.get("skill") == current_skill for row in active_records) >= summon_limit[0]:
                    evict_oldest(
                        lambda row: row.get("skill") == current_skill,
                        current_start,
                    )
            shared_group = self._skill_burst_shared_summon_group(current_skill)
            if shared_group:
                shared_skills, shared_maximum = shared_group
                while sum(row.get("skill") in shared_skills for row in active_records) >= shared_maximum:
                    evict_oldest(
                        lambda row: row.get("skill") in shared_skills,
                        current_start,
                    )
            active_records.append(current_record)

        snapshot_attr_keys = {"物攻%", "魔攻%", "火攻%", "水攻%", "光攻%", "暗攻%", "无属性%"}
        snapshot_buff_markers: set[str] = set()
        ignored_stat_markers: set[str] = set()
        for action in support_actions:
            action_attrs = {
                norm(key)
                for source in (action.get("attrs", {}), action.get("display_attrs", {}))
                if isinstance(source, dict)
                for key, value in source.items()
                if abs(as_number(value)) > 1e-9
            }
            marker = norm(action.get("buff_marker"))
            if not marker:
                continue
            if action_attrs & snapshot_attr_keys:
                snapshot_buff_markers.add(marker)
            elif action_attrs & SKILL_PERIODIC_IGNORED_DYNAMIC_STAT_ATTRS:
                ignored_stat_markers.add(marker)

        attr_windows = self._skill_burst_timeline_attr_windows(segments, support_actions, window)
        active_attrs_cache: dict[
            tuple[float, tuple[str, ...], float | None],
            dict[str, float],
        ] = {}
        # Each integration owns immutable window/action rows. Reuse the same
        # private runtime-effect objects across its time slices so their exact
        # signatures/tokens need only be built once. Never share this cache
        # with another axis/integration; refresh times and post-clear attrs
        # remain part of the key, including the summon snapshot refresh path.
        runtime_effect_cache: dict = {}

        def periodic_active_attrs(
            seconds: float,
            excluded: set[str] | None = None,
            applied_after: float | None = None,
        ) -> dict[str, float]:
            cache_key = (
                seconds,
                tuple(sorted(excluded or ())),
                applied_after,
            )
            if cache_key not in active_attrs_cache:
                try:
                    cached = self._skill_burst_active_attrs_from_timeline_windows(
                        attr_windows,
                        seconds,
                        excluded,
                        applied_after,
                        include_cdr_windows=False,
                        runtime_effect_cache=runtime_effect_cache,
                    )
                except TypeError as exc:
                    if not any(
                        key in str(exc)
                        for key in ("include_cdr_windows", "runtime_effect_cache")
                    ):
                        raise
                    # Keep compatibility with lightweight test/extension stubs
                    # that override the legacy four-argument helper.
                    cached = self._skill_burst_active_attrs_from_timeline_windows(
                        attr_windows,
                        seconds,
                        excluded,
                        applied_after,
                    )
                active_attrs_cache[cache_key] = cached
            return active_attrs_cache[cache_key]

        boundaries = {0.0, max(0.0, as_number(window))}
        for row in attr_windows:
            boundaries.add(max(0.0, min(window, as_number(row.get("start")))))
            boundaries.add(max(0.0, min(window, as_number(row.get("end")))))
        external_windows = getattr(self, "_skill_burst_external_team_effect_windows", [])
        for row in external_windows if isinstance(external_windows, list) else []:
            boundaries.add(max(0.0, min(window, as_number(row.get("start")))))
            boundaries.add(max(0.0, min(window, as_number(row.get("end")))))
        for blocked_start, blocked_end in self._skill_burst_blocked_ranges():
            boundaries.add(max(0.0, min(window, blocked_start)))
            boundaries.add(max(0.0, min(window, blocked_end)))
        for removed_at, _skill_key in self._skill_burst_summon_removal_schedule():
            boundaries.add(max(0.0, min(window, removed_at)))
        ordered = sorted(boundaries)
        for segment in periodic_segments:
            cycle = self._skill_periodic_total_damage_cycle(segment.get("skill"))
            if cycle <= 0:
                continue
            skill_key = self._skill_burst_skill_key(segment.get("skill"))
            if segment.get("kind") == "passive":
                active_start = max(0.0, as_number(segment.get("start")))
                active_end = min(max(0.0, as_number(window)), max(active_start, as_number(segment.get("end"), window)))
            else:
                active_start = max(0.0, as_number(segment.get("end")))
                active_duration = self._skill_burst_summon_duration(skill_key)
                active_end = min(
                    max(0.0, as_number(window)),
                    active_start + (active_duration if active_duration > 0 else cycle),
                    replacement_end_by_id.get(id(segment), float("inf")),
                    removal_end_by_id.get(id(segment), float("inf")),
                )
            if active_end <= active_start + 1e-9:
                segment["raw_effective_score"] = 0.0
                segment["effective_score"] = 0.0
                segment["dpa"] = 0.0
                segment["dpa_basis_seconds"] = 0.0
                replaced_at = replacement_end_by_id.get(id(segment))
                if replaced_at is not None:
                    segment["summon_replaced_at"] = replaced_at
                removed_at = removal_end_by_id.get(id(segment))
                if removed_at is not None:
                    segment["summon_removed_at"] = removed_at
                continue
            metric = dict(segment.get("periodic_metric", {}))
            snapshot_state = {"attack": 1.0, "element_bonus": 0.0}
            if skill_key in SKILL_PERIODIC_DOUBLE_SNAPSHOT_BUFFS:
                # Azuna is considered pre-summoned at the start of the timeline.
                # Its snapshot is the opening panel, not the best panel found later.
                sample_time = min(active_end - 1e-6, active_start + 1e-6)
                sample_attrs = periodic_active_attrs(max(active_start, sample_time))
                snapshot_state = self._skill_burst_periodic_snapshot_state(
                    metric,
                    sample_attrs,
                    direct_attack_only=False,
                )
            elif segment.get("kind") != "passive":
                cast_sample = max(
                    0.0,
                    min(active_start - 1e-6, as_number(segment.get("start")) + 1e-6),
                )
                cast_attrs = periodic_active_attrs(cast_sample)
                snapshot_state = self._skill_burst_periodic_snapshot_state(
                    metric,
                    cast_attrs,
                    direct_attack_only=False,
                )
            segment["snapshot_attack_multiplier"] = max(
                0.0,
                as_number(snapshot_state.get("attack"), 1.0),
            )
            segment["snapshot_element_bonus"] = as_number(
                snapshot_state.get("element_bonus"),
            )
            snapshot_multiplier, _unused_dynamic = self._skill_burst_periodic_attack_element_layers(
                metric,
                snapshot_state,
                {},
                False,
            )
            raw_cycle = max(
                0.0,
                as_number(
                    metric.get("timeline_raw_score")
                    if norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
                    and metric.get("timeline_attack_scores")
                    else metric.get("raw_score")
                ),
            )
            # Periodic skills normally use their table value as the raw cycle.
            # Lightning Relic is deliberately halved on the DPS page, then
            # restored for the real timeline, so the raw and buffed timeline
            # columns must both include the timeline-only multiplier.
            raw_cycle *= self._skill_burst_damage_multiplier(
                skill_key,
                active_start,
                {},
            )
            effective_total = 0.0
            raw_total = 0.0
            weighted_dynamic_multiplier = 0.0
            weighted_realtime_multiplier = 0.0
            weighted_action_speed_multiplier = 0.0
            weighted_base_multiplier = 0.0
            damage_seconds = 0.0
            # Keep the exact environment-valued slices of permanent summons.
            # A later personal scoring scope may be narrower than this axis.
            passive_slices = [] if segment.get("kind") == "passive" else None
            channel_totals = {channel: 0.0 for channel in SKILL_DAMAGE_CHANNEL_ORDER}
            # A short-lived summon should not scan every effect boundary in a
            # multi-Boss recording. Locate the exact intersecting slice range;
            # the clipping and midpoint rules below remain unchanged.
            first_slice = max(0, bisect_right(ordered, active_start) - 1)
            stop_slice = min(
                max(0, len(ordered) - 1),
                bisect_left(ordered, active_end),
            )
            for start, end in zip(
                ordered[first_slice:stop_slice],
                ordered[first_slice + 1:stop_slice + 1],
            ):
                slice_start = max(start, active_start)
                slice_end = min(end, active_end)
                duration = max(0.0, slice_end - slice_start)
                if duration <= 1e-9:
                    continue
                midpoint = (slice_start + slice_end) / 2
                if self._skill_burst_time_is_blocked(midpoint):
                    continue
                damage_seconds += duration
                exclude_skills: set[str] = set()
                if self._skill_burst_is_sitting_yao():
                    casting = any(
                        self._skill_burst_skill_key(row.get("skill")) != "查克拉治愈术"
                        and as_number(row.get("start")) <= midpoint < as_number(row.get("end"))
                        for row in segments
                        if not row.get("timeline_hidden")
                    )
                    if casting:
                        exclude_skills.add("查克拉治愈术")
                active_attrs = periodic_active_attrs(midpoint, exclude_skills)
                dynamic_attrs = active_attrs
                if skill_key in SKILL_BURST_ENGINEER_SUMMONS:
                    field_applies = any(
                        self._skill_burst_skill_key(row.get("action", {}).get("skill")) == "工程领域"
                        and as_number(row.get("start")) >= active_start - 1e-6
                        and as_number(row.get("start")) <= midpoint + 1e-6 < as_number(row.get("end"))
                        for row in attr_windows
                    )
                    if not field_applies:
                        active_attrs = dict(active_attrs)
                        active_attrs.pop(SKILL_BURST_ENGINEERING_FIELD_MARKER, None)
                if (
                    skill_key in SKILL_PERIODIC_DYNAMIC_ATTACK_BUFFS
                    and skill_key not in SKILL_PERIODIC_DOUBLE_SNAPSHOT_BUFFS
                ):
                    # These summons snapshot the owner's panel, then only buffs
                    # applied or refreshed after the summon enter the dynamic layer.
                    dynamic_attrs = periodic_active_attrs(
                        midpoint,
                        exclude_skills,
                        active_start,
                    )
                score_attrs = dict(active_attrs)
                score_attrs.pop(SKILL_BURST_TEAM_PANEL_MULTIPLIER, None)
                score_attrs.pop(SKILL_BURST_TEAM_PHYSICAL_PANEL_MULTIPLIER, None)
                score_attrs.pop(SKILL_BURST_TEAM_MAGIC_PANEL_MULTIPLIER, None)
                for attr in snapshot_attr_keys:
                    score_attrs.pop(attr, None)
                for marker in snapshot_buff_markers | ignored_stat_markers:
                    score_attrs.pop(marker, None)
                # Elemental resistance reduction belongs to the complete
                # realtime panel multiplier below.  Leaving it in score_attrs
                # also applies it through _skill_burst_element_multiplier,
                # which made periodic summons receive resistance reduction
                # twice while ordinary skills received it once.
                for element_key in ("火", "水", "光", "暗"):
                    score_attrs.pop(
                        self._skill_burst_resist_attr_for_element(element_key),
                        None,
                    )
                score_attrs.pop("减全抗%", None)
                raw_piece = raw_cycle / cycle * duration
                current_snapshot_multiplier, dynamic_multiplier = (
                    self._skill_burst_periodic_attack_element_layers(
                        metric,
                        snapshot_state,
                        dynamic_attrs,
                        skill_key in SKILL_PERIODIC_DYNAMIC_ATTACK_BUFFS,
                    )
                )
                base_piece_score = self._skill_burst_full_metric_score(
                    metric,
                    score_attrs,
                    midpoint,
                )
                base_multiplier = (
                    base_piece_score / raw_cycle
                    if raw_cycle > 1e-9
                    else 1.0
                )
                realtime_multiplier = self._skill_burst_periodic_realtime_debuff_multiplier(metric, active_attrs)
                # These periodic totals use the summon object's own damage
                # cycle, not the owner's animation speed. In particular,
                # Spirit Boost accelerates the owner but cannot speed up
                # Azuna's attacks. Simulation and score replay share this
                # integration path; retain the diagnostic layer at x1.0.
                action_speed_multiplier = 1.0
                effective_piece = (
                    base_piece_score
                    * current_snapshot_multiplier
                    * dynamic_multiplier
                    * realtime_multiplier
                    / cycle
                    * duration
                )
                effective_piece *= action_speed_multiplier
                raw_total += raw_piece
                effective_total += effective_piece
                if passive_slices is not None:
                    passive_slices.append({
                        "start": slice_start, "end": slice_end,
                        "effective_score": effective_piece,
                    })
                weighted_dynamic_multiplier += dynamic_multiplier * duration
                weighted_realtime_multiplier += realtime_multiplier * duration
                weighted_action_speed_multiplier += action_speed_multiplier * duration
                weighted_base_multiplier += base_multiplier * duration
                for channel, value in self._skill_burst_scaled_channel_scores(metric, raw_piece).items():
                    if channel in channel_totals:
                        channel_totals[channel] += max(0.0, as_number(value))
            segment["raw_effective_score"] = raw_total
            segment["effective_score"] = effective_total
            if passive_slices is not None:
                segment["_score_periodic_slices"] = passive_slices
            active_seconds = max(1e-9, active_end - active_start)
            segment["periodic_active_seconds"] = active_seconds
            segment["periodic_damage_seconds"] = damage_seconds
            segment["periodic_utilization"] = max(
                0.0,
                min(1.0, damage_seconds / active_seconds),
            )
            segment["dpa"] = effective_total / active_seconds
            segment["dpa_basis_seconds"] = active_seconds
            replaced_at = replacement_end_by_id.get(id(segment))
            if replaced_at is not None:
                segment["summon_replaced_at"] = replaced_at
                segment["detail"] = "；".join(filter(None, [
                    norm(segment.get("detail")),
                    f"{replaced_at:g}s被新召唤替换，旧实例停止输出",
                ]))
            removed_at = removal_end_by_id.get(id(segment))
            if removed_at is not None:
                segment["summon_removed_at"] = removed_at
                segment["detail"] = "；".join(filter(None, [
                    norm(segment.get("detail")),
                    f"{removed_at:g}s被清除或击杀，按实际存活时间结算",
                ]))
            segment["channel_scores"] = {
                channel: value
                for channel, value in channel_totals.items()
                if value > 1e-9
            }
            detail = norm(segment.get("detail"))
            active_detail = (
                f"存在 {active_end - active_start:g}/{cycle:g}s；"
                f"有效计伤 {damage_seconds:g}s"
                f"（{damage_seconds / active_seconds * 100:.1f}%）"
            )
            if skill_key in SKILL_PERIODIC_DOUBLE_SNAPSHOT_BUFFS:
                dynamic_detail = "召唤快照，并按时间片再次继承当前攻击Buff/属性攻；实时吃减抗/增伤/减致抗/叠冰"
            elif skill_key in SKILL_PERIODIC_DYNAMIC_ATTACK_BUFFS:
                dynamic_detail = "召唤快照；动态层只吃召唤后新增或刷新的攻击Buff/属性攻；实时吃减抗/增伤/减致抗/叠冰"
            else:
                dynamic_detail = "攻击力/属性攻按召唤快照，按时间片实时吃减抗/增伤/减致抗/叠冰"
            if dynamic_detail not in detail:
                # Use the same denominator as the table's base-percent column.
                # Components are time-weighted diagnostics; their simple product
                # need not equal the exact integral when windows overlap unevenly.
                total_multiplier = effective_total / max(1e-9, raw_total)
                breakdown_detail = (
                    f"基础属性/烙印x{weighted_base_multiplier / active_seconds:.3f}；"
                    f"快照x{snapshot_multiplier:.3f}；"
                    f"实时面板/属性x{weighted_dynamic_multiplier / active_seconds:.3f}；"
                    f"实时减益x{weighted_realtime_multiplier / active_seconds:.3f}；"
                    f"动作速度x{weighted_action_speed_multiplier / active_seconds:.3f}；"
                    f"积分合计x{total_multiplier:.3f}"
                )
                segment["periodic_breakdown"] = {
                    "base": weighted_base_multiplier / active_seconds,
                    "snapshot": snapshot_multiplier,
                    "dynamic": weighted_dynamic_multiplier / active_seconds,
                    "realtime_debuff": weighted_realtime_multiplier / active_seconds,
                    "action_speed": weighted_action_speed_multiplier / active_seconds,
                    "total": total_multiplier,
                }
                segment["detail"] = "；".join(filter(None, [detail, active_detail, dynamic_detail, breakdown_detail]))
    def _skill_burst_attach_mixed_attack_damage(self, segments: list[dict[str, Any]]) -> None:
        self._skill_burst_apply_ripper_successive_scores(segments)
        """Attach panel-scaled damage without changing percent-based rotation scores.

        ``effective_score`` deliberately stays in equivalent-percent units because
        the rotation planners compare skills with it.  Timeline presentation and
        team simulation, however, expose damage values and must therefore use the
        build's damage panel.  The old implementation only populated those fields
        for the two mixed-attack cleric jobs, leaving every other job's percent
        score to be serialized as if it were real damage.
        """

        job = norm(self.second_job.currentText())
        summary = getattr(self, "_skill_burst_base_summary", {})
        if job in {"圣骑士", "十字军"}:
            # Their timeline scores are normalized against the physical channel
            # before the per-skill physical/magical attack mix is applied.
            denominator = max(
                0.0,
                as_number(summary.get("physical_average"))
                or as_number(summary.get("magic_average")),
            )
        else:
            # This is the same V4.01 panel used by the practical-team formula:
            # attack/final multipliers remain in the panel while element is
            # stripped because each segment already contains its element factor.
            denominator = max(0.0, self._team_practical_expected_panel())
        if denominator <= 0:
            return
        for segment in segments:
            raw_output = max(0.0, as_number(segment.get("raw_effective_score")))
            output = max(0.0, as_number(segment.get("effective_score")))
            segment["raw_actual_damage"] = raw_output * denominator / 100
            actual = output * denominator / 100
            cast_time = max(
                0.0,
                as_number(segment.get("end")) - as_number(segment.get("start")),
            )
            dpa_basis = max(
                0.0,
                as_number(segment.get("dpa_basis_seconds"), cast_time),
            )
            segment["actual_damage"] = actual
            segment["actual_dpa"] = actual / dpa_basis if dpa_basis > 0 else 0.0
    def _skill_burst_is_sitting_yao(self) -> bool:
        """Return the explicit rotation variant without depending on UI labels."""

        return bool(getattr(self, "_skill_burst_sitting_yao", False)) or (
            "打坐曜"
            in norm(getattr(self, "_skill_burst_current_provider_override", ""))
        )
    def _skill_burst_fill_sitting_yao_idle(
        self,
        segments: list[dict[str, Any]],
        window: float,
    ) -> None:
        if not self._skill_burst_is_sitting_yao():
            return
        segments[:] = [
            row
            for row in segments
            if self._skill_burst_skill_key(row.get("skill")) != "查克拉治愈术"
        ]
        occupied = sorted(
            (
                max(0.0, as_number(row.get("start"))),
                min(window, max(0.0, as_number(row.get("end")))),
            )
            for row in segments
            if not row.get("timeline_hidden")
            and row.get("kind") != "passive"
            and as_number(row.get("end")) > as_number(row.get("start")) + 1e-6
        )
        cursor = 0.0
        idle_rows: list[dict[str, Any]] = []
        for start, end in occupied:
            if start > cursor + 1e-6:
                idle_rows.append({
                    "start": cursor,
                    "end": start,
                    "skill": "查克拉治愈术",
                    "type": "打坐",
                    "kind": "sitting",
                    "dpa": 0.0,
                    "effective_score": 0.0,
                    "raw_effective_score": 0.0,
                    "channel_scores": {},
                    "cdr": 0.0,
                    "detail": (
                        "每0.5s刷新一次团队Buff，离开打坐后最多保留"
                        f"{YAO_MEDITATION_LINGER_SECONDS:g}s"
                    ),
                })
            cursor = max(cursor, end)
        if cursor < window - 1e-6:
            idle_rows.append({
                "start": cursor,
                "end": window,
                "skill": "查克拉治愈术",
                "type": "打坐",
                "kind": "sitting",
                "dpa": 0.0,
                "effective_score": 0.0,
                "raw_effective_score": 0.0,
                "channel_scores": {},
                "cdr": 0.0,
                "detail": (
                    "每0.5s刷新一次团队Buff，离开打坐后最多保留"
                    f"{YAO_MEDITATION_LINGER_SECONDS:g}s"
                ),
            })
        segments.extend(idle_rows)
        segments.sort(key=lambda row: (as_number(row.get("start")), as_number(row.get("end"))))
    def _skill_burst_channel_totals(
        self,
        segments: list[dict[str, Any]],
        *,
        divisor: float = 1.0,
    ) -> dict[str, float]:
        totals = {channel: 0.0 for channel in SKILL_DAMAGE_CHANNEL_ORDER}
        for segment in segments:
            raw_score = max(0.0, as_number(segment.get("raw_effective_score")))
            channel_scores = segment.get("channel_scores", {})
            if not isinstance(channel_scores, dict) or not channel_scores:
                metric = {
                    "element": segment.get("element", ""),
                    "channel_scores": segment.get("metric_channel_scores", {}),
                }
                channel_scores = self._skill_burst_scaled_channel_scores(metric, raw_score)
            for channel, value in channel_scores.items():
                if channel in totals:
                    totals[channel] += max(0.0, as_number(value))
        scale = max(1e-9, as_number(divisor, 1.0))
        return {
            channel: value / scale
            for channel, value in totals.items()
            if value > 1e-9
        }
    def _skill_burst_channels_html(self, totals: dict[str, float]) -> str:
        parts = []
        for channel in SKILL_DAMAGE_CHANNEL_ORDER:
            value = max(0.0, as_number(totals.get(channel)))
            if value <= 1e-9:
                continue
            color = SKILL_DAMAGE_CHANNEL_COLORS[channel]
            parts.append(
                f'<span style="color:{color}; font-weight:700;">{channel} {self._percent_text(value)}</span>'
            )
        return "&nbsp;&nbsp;".join(parts) if parts else '<span style="color:#64748b;">无伤害 0%</span>'
    def _skill_burst_channels_plain(self, totals: dict[str, float]) -> str:
        parts = [
            f"{channel} {self._percent_text(totals[channel])}"
            for channel in SKILL_DAMAGE_CHANNEL_ORDER
            if as_number(totals.get(channel)) > 1e-9
        ]
        return " / ".join(parts) if parts else "无伤害 0%"
    def _skill_burst_total_text(self, segments: list[dict[str, Any]], window: float) -> str:
        total_percent = sum(max(0.0, as_number(segment.get("effective_score"))) for segment in segments)
        raw_total = sum(max(0.0, as_number(segment.get("raw_effective_score"))) for segment in segments)
        channel_totals = self._skill_burst_channel_totals(segments)
        lines = [
            "总技能百分比",
            self._skill_burst_channels_plain(channel_totals),
            f"基础合计 {self._percent_text(raw_total)}",
            f"计入Buff/属性攻 {self._percent_text(total_percent)}",
        ]
        lines.append(f"窗口 {self._cooldown_text(window)}s")
        return "\n".join(lines)
    def _skill_burst_axis_total_text(self, axis: str, segments: list[dict[str, Any]], window: float) -> str:
        total_percent = sum(max(0.0, as_number(segment.get("effective_score"))) for segment in segments)
        dps_percent = total_percent / max(1e-9, as_number(window))
        channel_totals = self._skill_burst_channel_totals(segments)
        return (
            f'<span style="color:#1e293b; font-weight:700;">{axis}总输出：</span>'
            f"{self._skill_burst_channels_html(channel_totals)}；"
            f'<span style="color:#047857; font-weight:700;">计入Buff/属性攻 {self._percent_text(total_percent)}</span>'
            f'<span style="color:#0369a1; font-weight:700;">&nbsp;|&nbsp;DPS {self._percent_text(dps_percent)}/s</span>'
            f'<span style="color:#64748b;">&nbsp;|&nbsp;窗口 {self._cooldown_text(window)}s</span>'
        )
    def _skill_burst_panel_multiplier_for_attrs(self, attrs: dict[str, Any]) -> float:
        base_panel = max(0.0, as_number(getattr(self, "_skill_burst_base_summary", {}).get("effective_panel")))
        if base_panel <= 0 or not attrs:
            return 1.0
        try:
            build = self.collect_build()
            extra_attrs = dict(build.get("extra_buff_attrs", {}))
            for attr, value in attrs.items():
                extra_attrs[norm(attr)] = as_number(extra_attrs.get(norm(attr))) + as_number(value)
            build["extra_buff_attrs"] = extra_attrs
            _rows, summary = self.calculator.calculate(build)
            return max(0.0, as_number(summary.get("effective_panel")) / base_panel)
        except Exception:
            return 1.0


def bind_host_globals(host_globals: dict[str, object]) -> None:
    protected = {'bind_host_globals', 'SkillTimelineOutputMixin'}
    for name, value in host_globals.items():
        if name.startswith('__') or name in protected:
            continue
        globals()[name] = value


# Extracted from skill_timeline_engine.py; algorithm bodies are unchanged.
