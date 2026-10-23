from __future__ import annotations

from ..shared import *

BuildEditor = None


class TimelineRuntimeMixin:
    def _skill_burst_replay_manual_sequence(
        self,
        sequence: list[str],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        *,
        stop_on_issue: bool = False,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        # A replay is an independent experiment. In particular, comparing a
        # dynamic axis with a recursively generated stable axis can leave the
        # periodic registry pointing at deep copies of the support actions.
        # Shared-skill-use then updates one Judgement snapshot while damage
        # valuation reads another. Bind BOTH paths to replay-local objects.
        had_periodic = hasattr(self, "_skill_burst_periodic_buffs")
        saved_periodic = getattr(self, "_skill_burst_periodic_buffs", [])
        actions, periodic = copy.deepcopy((support_actions, saved_periodic))
        action_by_key = {
            (self._skill_burst_skill_key(a.get("skill")), norm(a.get("buff_marker"))): a
            for a in actions
        }
        bound_periodic = []
        for item in periodic:
            key = (self._skill_burst_skill_key(item.get("skill")), norm(item.get("buff_marker")))
            bound_periodic.append(action_by_key.get(key, item))
        for action in actions:
            if action.get("periodic_buff") and not any(a is action for a in bound_periodic):
                bound_periodic.append(action)
        for action in actions + bound_periodic:
            if "base_active_multiplier" in action and self._skill_burst_snapshot_rule(action):
                action["active_multiplier"] = action["base_active_multiplier"]
                action["last_snapshot_multiplier"] = action["base_active_multiplier"]
        self._skill_burst_periodic_buffs = bound_periodic
        try:
            return self._skill_burst_replay_manual_sequence_with_actions(
                sequence, candidates, actions, window, stop_on_issue=stop_on_issue,
            )
        finally:
            # Rejected/early-exit/error proposals must not modify the next
            # candidate, the automatic axis, or actual-cast valuation.
            if had_periodic:
                self._skill_burst_periodic_buffs = saved_periodic
            else:
                del self._skill_burst_periodic_buffs

    def _skill_burst_replay_manual_sequence_with_actions(
        self,
        sequence: list[str],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        *,
        stop_on_issue: bool = False,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        metrics_by_skill = {
            self._skill_burst_skill_key(metric.get("skill")): metric
            for metric in candidates
        }
        support_indexes_by_skill = {
            self._skill_burst_skill_key(action.get("skill")): index
            for index, action in enumerate(support_actions)
        }
        damage_available_at = {skill: 0.0 for skill in metrics_by_skill}
        damage_row_by_skill = dict((skill, skill) for skill in metrics_by_skill)
        damage_base_cooldown_by_skill = {
            skill: max(0.0, as_number(metric.get("base_cooldown")))
            for skill, metric in metrics_by_skill.items()
        }
        support_available_at = {
            index: max(0.0, as_number(action.get("initial_available_at")))
            for index, action in enumerate(support_actions)
        }
        support_expires_at = {index: 0.0 for index in range(len(support_actions))}
        summon_active_until: dict[str, list[float]] = {}
        charged_maxes = {
            skill: count
            for skill, count in self._skill_burst_charged_skill_maxes().items()
            if skill in metrics_by_skill
        }
        charged_recharges: dict[str, list[float]] = {skill: [] for skill in charged_maxes}
        charged_counts = dict(charged_maxes)
        passive_sources = self._skill_burst_passive_cooldown_sources()
        passive_next = {
            skill: self._skill_burst_passive_cooldown_source_interval(skill, 0.0)
            for skill in passive_sources
        }
        ammo_rule = self._skill_burst_ammo_rule()
        ammo = 0
        ammo_gain = max(1, int(as_number(ammo_rule.get("gain"), 1))) if ammo_rule else 0
        ammo_max = max(ammo_gain, int(as_number(ammo_rule.get("max"), 0))) if ammo_rule else 0
        reload_skill = self._skill_burst_reload_skill()
        current = 0.0
        previous_skill = ""
        fury = 0
        fury_expires_at = 0.0
        guardian_phantoms = 0
        used_skill_keys: set[str] = set()
        segments: list[dict[str, Any]] = []
        issues: list[str] = []
        display_horizon = window + max(6.0, window * 0.5)

        for position, selected_skill in enumerate(sequence, start=1):
            # An optimizer discards the entire proposal on any issue. Once
            # rejected, replaying its remaining (often hundreds of) casts
            # cannot change that decision. Manual/UI replay still completes
            # by default so it retains every diagnostic and invalid segment.
            if stop_on_issue and issues:
                return segments, issues
            blocked_range = self._skill_burst_blocked_range_at(current)
            if blocked_range is not None:
                current = min(display_horizon, blocked_range[1])
                previous_skill = ""
            previous_end = current
            skill_key = self._skill_burst_skill_key(selected_skill)
            metric = metrics_by_skill.get(skill_key)
            support_index = support_indexes_by_skill.get(skill_key)
            action = support_actions[support_index] if support_index is not None else None
            if action is not None and action.get("non_damaging_support"):
                metric = None
            is_reload = bool(reload_skill and skill_key == self._skill_burst_skill_key(reload_skill))
            if metric is None and action is None and not is_reload:
                issues.append(f"第{position}项 {selected_skill} 不在当前加点中")
                continue
            required_followups = self._skill_burst_required_followups(selected_skill)
            next_skill = sequence[position] if position < len(sequence) else ""
            if required_followups and self._skill_burst_skill_key(next_skill) not in required_followups:
                segments.append({
                    "start": current,
                    "end": current,
                    "skill": selected_skill,
                    "type": "后续不满足",
                    "kind": "invalid",
                    "dpa": 0.0,
                    "effective_score": 0.0,
                    "raw_effective_score": 0.0,
                    "element": "",
                    "detail": "需要紧邻指定后续技能",
                })
                issues.append(f"{selected_skill} 后必须紧接指定技能")
                continue
            if not self._skill_burst_predecessor_satisfied(previous_skill, selected_skill):
                segments.append({
                    "start": current,
                    "end": current,
                    "skill": selected_skill,
                    "type": "前置不满足",
                    "kind": "invalid",
                    "dpa": 0.0,
                    "effective_score": 0.0,
                    "raw_effective_score": 0.0,
                    "element": "",
                    "detail": "需要紧邻前置技能",
                })
                issues.append(f"{selected_skill} 的紧邻前置不满足")
                continue

            if fury > 0 and fury_expires_at <= current + 1e-6:
                fury = 0
            self._skill_burst_prune_summon_state(summon_active_until, current)
            active_attrs = self._skill_burst_active_attrs(support_actions, support_expires_at, current)
            if self._skill_burst_uses_fury():
                active_attrs[SKILL_BURST_FURY_MARKER] = float(fury)
            if self._skill_burst_uses_guardian_phantoms():
                active_attrs[SKILL_BURST_GUARDIAN_PHANTOM_MARKER] = float(guardian_phantoms)
            self._skill_burst_attach_summon_state(active_attrs, summon_active_until, current)
            for source_skill in list(passive_next):
                guard = 0
                while passive_next[source_skill] <= current + 1e-6 and guard < 30:
                    trigger_at = passive_next[source_skill]
                    self._skill_burst_apply_cooldown_rule_source(
                        source_skill,
                        trigger_at,
                        damage_available_at,
                        damage_base_cooldown_by_skill,
                        active_attrs,
                        support_actions,
                        support_available_at,
                    )
                    interval = self._skill_burst_passive_cooldown_source_interval(
                        source_skill, trigger_at, active_attrs
                    )
                    passive_next[source_skill] = trigger_at + interval if interval > 0 else window + 1.0
                    guard += 1

            if skill_key in charged_maxes:
                ready_recharges = [value for value in charged_recharges[skill_key] if value <= current + 1e-6]
                if ready_recharges:
                    charged_counts[skill_key] = min(charged_maxes[skill_key], charged_counts[skill_key] + len(ready_recharges))
                    charged_recharges[skill_key] = [value for value in charged_recharges[skill_key] if value > current + 1e-6]
                if charged_counts[skill_key] <= 0 and charged_recharges[skill_key]:
                    current = min(charged_recharges[skill_key])
                    charged_counts[skill_key] = 1
                    charged_recharges[skill_key].remove(current)
            else:
                current = max(current, damage_available_at.get(skill_key, 0.0))
            # A charged damage skill can also own a support/debuff action.  Its
            # stock is still authoritative: the support row must not add a
            # second, ordinary cooldown gate while unused charges remain or
            # after those charges have recharged.
            if support_index is not None and skill_key not in charged_maxes:
                current = max(current, support_available_at.get(support_index, 0.0))
                if skill_key == "九天之舞":
                    current = max(current, self._skill_burst_dance_pair_ready_at(
                        skill_key, support_actions, support_available_at,
                    ))
                    blocked_range = self._skill_burst_blocked_range_at(current)
                    if blocked_range is not None:
                        current = blocked_range[1]
            resistance_until = self._skill_burst_acceleration_resistance_active_until(
                skill_key, current
            )
            if resistance_until > current + 1e-6:
                resistance_label = acceleration_resistance_label(skill_key)
                issues.append(
                    f"第{position}项 {selected_skill} 因{resistance_label}推迟至"
                    f" {resistance_until:.2f}s"
                )
                current = resistance_until
            summon_limit = self._skill_burst_summon_limit(skill_key)
            if current >= display_horizon - 1e-6:
                issues.append(f"第{position}项及之后超出自选轴展示范围")
                break
            immediate_previous = self._skill_burst_immediate_predecessor(
                previous_skill,
                previous_end,
                current,
            )
            if immediate_previous != previous_skill:
                previous_skill = immediate_previous
                if not self._skill_burst_predecessor_satisfied(previous_skill, selected_skill):
                    segments.append({
                        "start": current,
                        "end": current,
                        "skill": selected_skill,
                        "type": "衔接已中断",
                        "kind": "invalid",
                        "dpa": 0.0,
                        "effective_score": 0.0,
                        "raw_effective_score": 0.0,
                        "element": "",
                        "detail": "前置结束后发生等待，无法继续快速衔接",
                    })
                    issues.append(f"{selected_skill} 未能紧接前置技能")
                    continue

            if is_reload:
                active_attrs = self._skill_burst_active_attrs(support_actions, support_expires_at, current)
                self._skill_burst_attach_summon_state(active_attrs, summon_active_until, current)
                cast_time = self._skill_burst_reload_cast_time(current, active_attrs)
                start = current
                end = current + cast_time
                ammo = min(ammo_max, ammo + ammo_gain)
                damage_values = self._skill_burst_reload_damage_values(
                    metric, active_attrs, start, window, cast_time
                )
                segments.append({
                    "start": start, "end": end, "skill": reload_skill, "type": "上弹/伤害", "kind": "ammo",
                    **damage_values, "cdr": self._skill_burst_sim_cdr_percent_at(start, active_attrs),
                    "detail": f"弹药 {ammo}/{ammo_max}",
                })
                previous_skill = reload_skill
                current = end
                continue

            ammo_cost = self._skill_burst_ammo_cost(skill_key)
            if ammo_cost > ammo:
                segments.append({
                    "start": current, "end": current, "skill": selected_skill, "type": "弹药不足", "kind": "invalid",
                    "dpa": 0.0, "effective_score": 0.0, "element": "", "detail": f"需要 {ammo_cost} 层弹药",
                })
                issues.append(f"{selected_skill} 弹药不足")
                continue
            ammo = max(0, ammo - ammo_cost)

            if fury > 0 and fury_expires_at <= current + 1e-6:
                fury = 0
            active_attrs = self._skill_burst_active_attrs(support_actions, support_expires_at, current)
            if self._skill_burst_uses_fury():
                active_attrs[SKILL_BURST_FURY_MARKER] = float(fury)
            if self._skill_burst_uses_guardian_phantoms():
                active_attrs[SKILL_BURST_GUARDIAN_PHANTOM_MARKER] = float(guardian_phantoms)
            self._skill_burst_attach_summon_state(active_attrs, summon_active_until, current)
            if not self._skill_burst_state_requirement_satisfied(selected_skill, active_attrs):
                fury_cost, _fury_multiplier = self._skill_burst_fury_consumer(skill_key)
                guardian_cost = self._skill_burst_guardian_phantom_cost(skill_key)
                requirement = (
                    f"{fury_cost}点怒气"
                    if fury_cost > 0
                    else f"{guardian_cost}个护卫者幻影"
                    if guardian_cost > 0
                    else "烙印"
                    if skill_key in {"召唤傀儡", "灵魂之门", "灵魂之纸"}
                    else "致残惩罚者Buff"
                    if skill_key == "致残惩罚者攻击"
                    else "一阶"
                )
                segments.append({
                    "start": current, "end": current, "skill": selected_skill,
                    "type": "状态不满足", "kind": "invalid", "dpa": 0.0,
                    "effective_score": 0.0, "raw_effective_score": 0.0,
                    "element": "", "detail": f"需要先获得{requirement}状态",
                })
                issues.append(f"{selected_skill} 缺少{requirement}状态")
                continue
            cast_time = (
                self._skill_burst_support_cast_time(action, current, active_attrs)
                if action is not None
                else self._skill_burst_scheduled_cast_time(selected_skill, current, previous_skill, active_attrs)
            )
            if cast_time <= 0:
                issues.append(f"{selected_skill} 缺少施法时间")
                continue
            if skill_key == "九天之舞" and not self._skill_burst_dance_pair_fits(
                support_actions, support_available_at, current, current + cast_time,
                min(window, self._skill_burst_next_block_start(current, window)), active_attrs,
            ):
                issues.append("九天之舞一阶、二阶无法在当前可输出区间内连续完成")
                continue
            start = current
            end = current + cast_time
            outside_window = start >= window - 1e-6
            scoring_attrs = self._skill_burst_attrs_during_action(active_attrs, action)
            score = (
                self._skill_burst_window_metric_score(metric, scoring_attrs, start, window, cast_time)
                if metric is not None and not outside_window
                else 0.0
            )
            raw_score = (
                self._skill_burst_window_raw_metric_score(metric, scoring_attrs, start, window, cast_time)
                if metric is not None and not outside_window
                else 0.0
            )
            snapshot_full_score = (
                self._skill_burst_full_metric_score(metric, scoring_attrs, start)
                if metric is not None and self._skill_burst_is_refreshable_periodic_damage(selected_skill)
                else 0.0
            )
            periodic_metric = None
            if metric is not None and self._skill_periodic_total_damage_cycle(selected_skill) > 0:
                periodic_metric = copy.deepcopy(metric)
            segment_type = norm((action or {}).get("type")) or "伤害"
            if outside_window:
                segment_type = f"窗口外·{segment_type}"
            segments.append({
                "start": start,
                "end": end,
                "skill": norm((action or {}).get("skill")) or norm(metric.get("skill")),
                "type": segment_type,
                "kind": "support" if action is not None else "damage",
                "dpa": score / cast_time if cast_time > 0 else 0.0,
                "effective_score": score,
                "raw_effective_score": raw_score,
                "channel_scores": self._skill_burst_scaled_channel_scores(metric, raw_score),
                "snapshot_full_score": snapshot_full_score,
                "periodic_metric": periodic_metric,
                "element": norm((metric or {}).get("element")),
                "cdr": self._skill_burst_sim_cdr_percent_at(start, active_attrs),
                "detail": self._skill_burst_dynamic_detail(selected_skill, start, active_attrs),
            })
            base_cooldown = max(
                as_number((action or {}).get("damage_base_cooldown")),
                as_number((action or {}).get("base_cooldown")),
                as_number((metric or {}).get("base_cooldown")),
            )
            segments[-1]["cooldown_ready_at"] = self._skill_burst_cooldown_ready_at(
                selected_skill,
                base_cooldown,
                start,
                active_attrs,
            )
            self._skill_burst_apply_shared_skill_use(
                selected_skill,
                start,
                base_cooldown,
                support_actions,
                support_available_at,
                support_expires_at,
                damage_available_at,
                damage_row_by_skill,
                damage_base_cooldown_by_skill,
                active_attrs,
                cast_end=end,
                charged_counts=charged_counts,
                charged_maxes=charged_maxes,
                charged_recharge_at=charged_recharges,
            )
            fury, fury_boosted = self._skill_burst_apply_fury_use(fury, selected_skill)
            if self._skill_burst_uses_fury() and skill_key == '旋转冲击':
                segments[-1]['display_name'] = '真旋转冲击' if fury_boosted else '旋转冲击（普通）' 
            if self._skill_burst_fury_gain(selected_skill) > 0:
                fury_expires_at = end + 60.0
            guardian_phantoms, guardian_used = self._skill_burst_apply_guardian_phantom_use(guardian_phantoms, selected_skill)
            if self._skill_burst_uses_fury():
                fury_cost, _fury_multiplier = self._skill_burst_fury_consumer(selected_skill)
                fury_gain = self._skill_burst_fury_gain(selected_skill)
                fury_detail = (
                    f"怒气强化{'生效' if fury_boosted else '未生效'}，剩余{fury}"
                    if fury_cost > 0
                    else f"获得{fury_gain}怒气，当前{fury}"
                    if fury_gain > 0
                    else ""
                )
                segments[-1]["detail"] = "；".join(filter(None, [
                    norm(segments[-1].get("detail")),
                    fury_detail,
                ]))
            if self._skill_burst_uses_guardian_phantoms():
                guardian_cost = self._skill_burst_guardian_phantom_cost(selected_skill)
                guardian_gain = self._skill_burst_guardian_phantom_gain(selected_skill)
                guardian_detail = (
                    f"消耗{guardian_cost}幻影，剩余{guardian_phantoms}"
                    if guardian_cost > 0 and guardian_used
                    else f"获得{guardian_gain}幻影，当前{guardian_phantoms}"
                    if guardian_gain > 0
                    else ""
                )
                segments[-1]["detail"] = "；".join(filter(None, [
                    norm(segments[-1].get("detail")),
                    guardian_detail,
                ]))
            if skill_key in charged_maxes:
                charged_counts[skill_key] = max(0, charged_counts[skill_key] - 1)
                cooldown = self._skill_burst_effective_cooldown(
                    selected_skill, base_cooldown, start, active_attrs
                )
                if cooldown > 0:
                    recharge_start = max([start, *charged_recharges[skill_key]])
                    charged_recharges[skill_key].append(recharge_start + cooldown)
                damage_available_at[skill_key] = start if charged_counts[skill_key] > 0 else min(charged_recharges[skill_key] or [window])
            if summon_limit:
                self._skill_burst_register_summon(
                    skill_key,
                    summon_active_until,
                    end + summon_limit[1],
                    end,
                )
            elif skill_key in SKILL_BURST_ENGINEER_SUMMONS:
                self._skill_burst_register_summon(
                    skill_key,
                    summon_active_until,
                    end + 30.0,
                    end,
                )
            previous_skill = norm(selected_skill)
            current = end
        self._skill_burst_apply_refreshable_periodic_scores(segments, window)
        return segments, issues

    def _skill_burst_effect_windows(
        self,
        segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
    ) -> list[dict[str, Any]]:
        if getattr(self, "_skill_burst_skip_display_effect_windows", False):
            return []
        effects: list[dict[str, Any]] = self._skill_burst_permanent_personal_effect_windows(window)
        for mode, label in (
            ("burst", "精神爆发  -40%CD / +40%动作速度"),
            ("accel", "时间加速  -70%CD"),
        ):
            for start, end in self._skill_burst_mode_intervals(mode):
                effects.append({
                    "name": label,
                    "start": start,
                    "end": min(window, end),
                    "color": SKILL_BURST_EFFECT_COLORS["team_buff"],
                    "detail": f"{start:.1f}s 开始，{min(window, end):.1f}s 结束",
                    "category": "team_buff",
                    "source": label.split()[0],
                })
        if self._skill_burst_sim_enabled("cocktail"):
            effects.append({
                "name": "鸡尾酒  常驻-12%CD",
                "start": 0.0,
                "end": window,
                "color": SKILL_BURST_EFFECT_COLORS["speed"],
                "detail": "全时间轴覆盖",
                "category": "speed",
                "source": "鸡尾酒",
            })

        action_by_skill = {
            self._skill_burst_skill_key(action.get("skill")): action
            for action in support_actions
            if as_number(action.get("duration")) > 0
        }
        runtime_resistance_windows: list[dict[str, Any]] = []
        seen_resistance_casts: set[tuple[str, float]] = set()
        for segment in segments:
            if segment.get("kind") == "passive":
                continue
            source = self._skill_burst_skill_key(segment.get("skill"))
            group = acceleration_resistance_group(source)
            action = action_by_skill.get(source)
            if not group or not isinstance(action, dict):
                continue
            start = max(0.0, as_number(segment.get("start")))
            key = (source, round(start, 6))
            if key in seen_resistance_casts:
                continue
            seen_resistance_casts.add(key)
            cooldown = max(
                0.0,
                as_number(
                    action.get("resistance_cooldown"),
                    action.get("base_cooldown"),
                ),
            )
            end = min(max(0.0, as_number(window)), start + cooldown)
            if end <= start + 1e-6:
                continue
            runtime_resistance_windows.append({
                "name": acceleration_resistance_label(group),
                "skill": acceleration_resistance_label(group),
                "source": source,
                "group": group,
                "category": "acceleration_resistance",
                "start": start,
                "end": end,
                "detail": (
                    f"{source}施加；抵抗期间不能再次释放或获得同组加速效果"
                ),
                "color": "#697386",
            })
        self._skill_burst_runtime_resistance_windows = runtime_resistance_windows
        triggered_actions_by_skill: dict[str, list[dict[str, Any]]] = {}
        for action in support_actions:
            if not action.get("triggered_only") or as_number(action.get("duration")) <= 0:
                continue
            for trigger_skill in action.get("trigger_skills", []):
                trigger_key = self._skill_burst_skill_key(trigger_skill)
                if trigger_key:
                    triggered_actions_by_skill.setdefault(trigger_key, []).append(action)
        for segment in segments:
            if segment.get("kind") == "passive":
                # Passive summons contribute damage but are not a player buff lane.
                continue
            segment_skill = self._skill_burst_skill_key(segment.get("skill"))
            matching_actions = []
            action = action_by_skill.get(segment_skill)
            if action:
                matching_actions.append(action)
            matching_actions.extend(triggered_actions_by_skill.get(segment_skill, []))
            if not matching_actions:
                continue
            for action in matching_actions:
                self._skill_burst_append_effect_window_for_action(
                    effects, segments, support_actions, segment, action, window
                )
        effects.extend(self._skill_burst_holy_relic_effect_windows(segments, window))

        if self._skill_burst_is_sitting_yao():
            chakra_effect = next(
                (
                    effect for effect in effects
                    if self._skill_burst_skill_key(effect.get("source")) == "查克拉治愈术"
                ),
                None,
            )
            effects = [
                effect for effect in effects
                if self._skill_burst_skill_key(effect.get("source")) != "查克拉治愈术"
            ]
            if chakra_effect:
                sitting = sorted(
                    (
                        max(0.0, as_number(row.get("start"))),
                        min(window, max(0.0, as_number(row.get("end")))),
                    )
                    for row in segments
                    if self._skill_burst_skill_key(row.get("skill")) == "查克拉治愈术"
                    and as_number(row.get("end")) > as_number(row.get("start")) + 1e-6
                )
                effects.extend(
                    {
                        **chakra_effect,
                        "start": start,
                        "end": min(window, end + YAO_MEDITATION_LINGER_SECONDS),
                        "refresh_interval": 0.5,
                    }
                    for start, end in sitting
                    if end > start + 1e-6
                )

        relevant_resists = self._skill_burst_relevant_resist_attrs(
            getattr(self, "_skill_burst_candidate_metrics", [])
        )
        effects = self._skill_burst_collapse_resist_effects(effects, relevant_resists)
        effects = self._skill_burst_collapse_crit_resist_effects(effects)
        effects = self._skill_burst_collapse_exclusive_amp_effects(effects)
        effects = self._skill_burst_clip_display_player_buffs(effects)

        return self._skill_burst_merge_display_effects(effects)

    def _skill_burst_holy_relic_effect_windows(
        self,
        segments: list[dict[str, Any]],
        window: float,
    ) -> list[dict[str, Any]]:
        if norm(self.second_job.currentText()) != "圣徒":
            return []
        relic_skills = ("雷电之手", "治愈之手", "痊愈之手", "掌握之手", "奇迹之手")
        grouped: dict[str, list[tuple[float, int]]] = {skill: [] for skill in relic_skills}
        for segment in segments:
            skill = self._skill_burst_skill_key(segment.get("skill"))
            if skill not in grouped or segment.get("kind") == "passive":
                continue
            summon_limit = self._skill_burst_summon_limit(skill)
            if not summon_limit:
                continue
            start = max(0.0, as_number(segment.get("end")))
            end = min(max(0.0, as_number(window)), start + summon_limit[1])
            if end <= start + 1e-6:
                continue
            grouped[skill].extend(((start, 1), (end, -1)))

        effects: list[dict[str, Any]] = []
        for skill, events in grouped.items():
            if not events:
                continue
            count = 0
            cursor = 0.0
            index = 0
            events.sort(key=lambda item: (item[0], item[1]))
            while index < len(events):
                event_time = events[index][0]
                if count > 0 and event_time > cursor + 1e-6:
                    attrs_text = f"冲击之手计数 +{count:g}"
                    effects.append({
                        "name": f"{skill}｜圣物在场 ×{count}｜{attrs_text}",
                        "start": cursor,
                        "end": event_time,
                        "color": SKILL_BURST_EFFECT_COLORS["personal"],
                        "detail": f"{skill}在场；{attrs_text}",
                        "category": "personal",
                        "source": f"{skill}（圣物）",
                        "attrs_text": attrs_text,
                        "attrs": {"冲击之手计数": float(count)},
                        "display_attrs": {"冲击之手计数": float(count)},
                        "stackable": True,
                    })
                while index < len(events) and abs(events[index][0] - event_time) <= 1e-9:
                    count += events[index][1]
                    index += 1
                cursor = event_time
        return effects

    def _skill_burst_permanent_personal_effect_windows(self, window: float) -> list[dict[str, Any]]:
        """Expose already-calculated permanent personal buffs without casting them again."""
        try:
            # Pure runtimes already own a private materialized build. This
            # function only reads Buff rows: copying the full combat snapshot
            # embedded in that build for every candidate has no semantic use.
            import os
            build = (self.build if getattr(self, '_timeline_is_pure_runtime', False)
                     and os.environ.get('DN_SCORE_EXACT_STATE_REUSE', '1') == '1'
                     else self.collect_build())
        except Exception:
            return []
        selected = [item for item in build.get("buffs", []) if isinstance(item, dict)]
        if not selected:
            return []
        jobs = {norm(self.first_job.currentText()), norm(self.second_job.currentText()), "通用"}
        buff_rows = [
            row
            for row in self.store.tables.get("buff", [])
            if norm(row.get("类别")) == "个人buff" and norm(row.get("来源")) in jobs
        ]
        effects: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in selected:
            label = norm(item.get("skill"))
            skill = self._team_effect_skill_name(label)
            skill_key = self._skill_burst_skill_key(skill)
            if not skill_key or skill_key in seen:
                continue
            row = next(
                (
                    candidate
                    for candidate in buff_rows
                    if self._skill_burst_skill_key(candidate.get("技能")) == skill_key
                    and norm(
                        self.store.buff_activation_rule(
                            candidate.get("来源", ""),
                            candidate.get("技能", ""),
                            candidate.get("类别", ""),
                        ).get("mode")
                    ) == "permanent"
                ),
                None,
            )
            if row is None:
                continue
            level = norm(item.get("level")) or "1"
            attrs = self._team_effect_attrs(
                row.get("技能", skill),
                level,
                row.get("来源", ""),
                include_personal_buffs=True,
            )
            display_attrs = self._team_display_attrs(attrs)
            attrs_text = attr_summary(display_attrs, limit=999)
            if not attrs_text:
                continue
            seen.add(skill_key)
            effects.append({
                "name": "｜".join(filter(None, [skill, "常驻个人buff", attrs_text])),
                "start": 0.0,
                "end": max(0.0, as_number(window)),
                "color": SKILL_BURST_EFFECT_COLORS["personal"],
                "detail": f"全程已计入；{attrs_text}",
                "category": "personal",
                "permanent": True,
                "buff_clear_immune": True,
                "priority": 0.0,
                "source": skill,
                "attrs_text": attrs_text,
                "attrs": dict(display_attrs),
                "display_attrs": dict(display_attrs),
                "stackable": False,
            })
        return effects

    def _skill_burst_append_effect_window_for_action(
        self,
        effects: list[dict[str, Any]],
        segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        segment: dict[str, Any],
        action: dict[str, Any],
        window: float,
    ) -> None:
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
            return
        resist_priority = self._skill_burst_resist_priority(action)
        raw_display_attrs = (
            self._skill_burst_action_resist_values(action)
            if resist_priority > 0
            else action.get("display_attrs") or action.get("attrs", {})
        )
        display_attrs = {
            norm(key): as_number(value)
            for key, value in self._team_display_attrs(raw_display_attrs).items()
            if norm(key) and not norm(key).startswith("__") and abs(as_number(value)) > 1e-9
        }
        attrs_text = attr_summary(display_attrs, limit=999)
        skill_name = norm(action.get("display_name")) or norm(action.get("skill"))
        display_level = norm(action.get("display_level"))
        level_text = f"Lv{display_level}" if display_level else ""
        category = self._skill_burst_effect_category(action, resist_priority)
        refresh_interval = max(0.0, as_number(action.get("refresh_interval")))
        refresh_text = f"每 {refresh_interval:g}s 刷新" if refresh_interval > 0 else ""
        hit_refresh_text = (
            f"连续命中刷新约 {refresh_tail:g}s"
            if refresh_tail > 0
            else ""
        )
        effect_row = {
            "name": "｜".join(filter(None, [skill_name, level_text, norm(action.get("type")), attrs_text])),
            "start": start,
            "end": end,
            "color": SKILL_BURST_EFFECT_COLORS.get(category, SKILL_BURST_EFFECT_COLORS["state"]),
            "detail": "；".join(filter(None, [
                level_text,
                f"单次持续 {duration:g}s" if refresh_tail > 0 else f"持续 {duration:g}s",
                hit_refresh_text,
                refresh_text,
                attrs_text,
            ])),
            "category": category,
            "priority": resist_priority,
            "source": skill_name,
            "attrs_text": attrs_text,
            "attrs": dict(display_attrs),
            "display_attrs": dict(display_attrs),
            "stackable": bool(action.get("stackable_resist")),
            "refresh_interval": refresh_interval,
            "refresh_tail": refresh_tail,
            "effect_duration": duration,
            "cast_start": cast_start,
            "cast_end": cast_end,
        }
        if self._skill_burst_skill_key(skill_name) == "痊愈之手":
            effect_row["display_refresh_pulses"] = True
            effect_row["collapse_refresh_display"] = True
            effect_row["refresh_effect_duration"] = duration
            effect_row["refresh_source_end"] = end
            effect_row["display_horizon"] = window
        effects.extend(self._skill_burst_expand_refresh_display_windows(effect_row))
        # A learned EX can attach freeze to the same cast that already owns an
        # elemental-resistance debuff. The resistance lane only displays its
        # allocated resistance values, so expose freeze as a second lane instead
        # of silently hiding it behind the higher-priority resistance row.
        if resist_priority > 0 and self._skill_burst_action_has_freeze(action):
            freeze_attrs = {
                norm(key): as_number(value)
                for key, value in self._team_display_attrs(action.get("attrs", {})).items()
                if norm(key) in self._team_freeze_attr_keys()
                and abs(as_number(value)) > 1e-9
            }
            freeze_text = attr_summary(freeze_attrs, limit=999)
            if freeze_attrs:
                effects.append({
                    "name": "｜".join(filter(None, [skill_name, "叠冰", freeze_text])),
                    "start": start,
                    "end": end,
                    "color": SKILL_BURST_EFFECT_COLORS["freeze"],
                    "detail": "；".join(filter(None, [f"持续 {duration:g}s", freeze_text])),
                    "category": "freeze",
                    "priority": 0.0,
                    "source": skill_name,
                    "attrs_text": freeze_text,
                    "attrs": dict(freeze_attrs),
                    "display_attrs": dict(freeze_attrs),
                    "stackable": False,
                })
        if segment.get("mechanic_moment_triggered"):
            moment_action = next(
                (
                    candidate for candidate in support_actions
                    if self._skill_burst_skill_key(candidate.get("skill")) == "机械时刻"
                ),
                None,
            )
            if moment_action:
                moment_duration = max(0.0, as_number(moment_action.get("duration"), 10.0))
                moment_attrs = self._team_display_attrs(moment_action.get("display_attrs", {}))
                moment_text = attr_summary(moment_attrs, limit=999)
                effects.append({
                    "name": "｜".join(filter(None, ["机械时刻", moment_text])),
                    "start": cast_end,
                    "end": min(window, cast_end + moment_duration),
                    "color": SKILL_BURST_EFFECT_COLORS["personal"],
                    "detail": "；".join(filter(None, [f"持续 {moment_duration:g}s", moment_text])),
                    "category": "personal",
                    "priority": 0.0,
                    "source": "机械时刻",
                    "attrs_text": moment_text,
                    "stackable": False,
                })

    @staticmethod
    def _skill_burst_expand_refresh_display_windows(
        effect: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Split a refreshed buff into visible pulses without changing coverage."""
        if not effect.get("display_refresh_pulses"):
            return [effect]
        interval = max(0.0, as_number(effect.get("refresh_interval")))
        start = max(0.0, as_number(effect.get("start")))
        end = max(start, as_number(effect.get("end")))
        if interval <= 0 or end <= start + 1e-6:
            return [effect]
        source_end = max(start, as_number(effect.get("refresh_source_end"), end))
        refresh_duration = max(
            interval,
            as_number(effect.get("refresh_effect_duration"), interval),
        )
        display_horizon = max(
            source_end,
            as_number(effect.get("display_horizon"), source_end + refresh_duration),
        )
        refresh_count = max(1, int(ceil(max(0.0, source_end - start) / interval)))
        last_refresh = start + (refresh_count - 1) * interval
        if effect.get("collapse_refresh_display"):
            combined = dict(effect)
            combined["start"] = start
            combined["end"] = min(display_horizon, last_refresh + refresh_duration)
            combined["name"] = norm(effect.get("name"))
            combined["detail"] = (
                f"共刷新{refresh_count}次；每{interval:g}s刷新；"
                f"每次Buff持续{refresh_duration:g}s；"
                f"{norm(effect.get('detail'))}"
            ).rstrip("；")
            combined["refresh_source_end"] = source_end
            combined["refresh_effect_duration"] = refresh_duration
            combined["collapsed_refresh_display"] = True
            return [combined]
        pulses: list[dict[str, Any]] = []
        cursor = start
        index = 1
        while cursor < source_end - 1e-6:
            pulse_end = min(display_horizon, cursor + refresh_duration)
            pulse = dict(effect)
            pulse["start"] = cursor
            pulse["end"] = pulse_end
            pulse["name"] = f"{norm(effect.get('name'))}｜刷新{index}"
            pulse["detail"] = (
                f"第{index}次刷新 {cursor:.1f}s；"
                f"本次Buff持续 {max(0.0, pulse_end - cursor):g}s；"
                f"{norm(effect.get('detail'))}"
            ).rstrip("；")
            pulse["no_merge"] = True
            pulse["single_refresh_application"] = True
            pulses.append(pulse)
            cursor += interval
            index += 1
        return pulses

    @staticmethod
    def _skill_burst_merge_display_effects(effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def key_text(value: Any) -> str:
            if isinstance(value, dict):
                return "|".join(
                    f"{key_text(key)}={key_text(val)}"
                    for key, val in sorted(value.items(), key=lambda item: key_text(item[0]))
                )
            if isinstance(value, (list, tuple, set)):
                return "|".join(key_text(item) for item in value)
            return norm(value)

        def attrs_key(row: dict[str, Any]) -> str:
            effect = row.get("team_effect", {})
            if isinstance(effect, dict):
                attrs = effect.get("display_attrs") or effect.get("effective_attrs") or effect.get("attrs") or {}
                if isinstance(attrs, dict) and attrs:
                    parts = sorted(
                        f"{norm(key)}={round(as_number(value), 6):g}"
                        for key, value in attrs.items()
                        if norm(key) and not norm(key).startswith("__") and abs(as_number(value)) > 1e-9
                    )
                    return "|".join(parts)
            attrs_text = key_text(row.get("attrs_text"))
            if attrs_text:
                return attrs_text
            name = key_text(row.get("name"))
            source = key_text(row.get("source"))
            return name.replace(source, "", 1).strip("｜ 　") if source and name.startswith(source) else name

        def merge_key(row: dict[str, Any]) -> tuple[str, str, str]:
            effect = row.get("team_effect", {})
            effect_skill = (
                BuildEditor._team_effect_skill_name(effect.get("skill"))
                if isinstance(effect, dict)
                else ""
            )
            source = key_text(effect_skill) or key_text(row.get("source")) or key_text(row.get("name"))
            category = key_text(row.get("category"))
            if "｜" in source:
                source = source.rsplit("｜", 1)[-1]
            source = source.replace(" ", "")
            # Provider names must not split one game effect into overlapping
            # lanes. Keep the value in the key, however, so a real 30% -> 40%
            # change remains visible instead of inheriting the first segment's
            # label for the whole union window.
            attrs = key_text(attrs_key(row))
            return (category, source, attrs)

        def merge_detail(row: dict[str, Any]) -> str:
            start = as_number(row.get("start"))
            end = as_number(row.get("end"))
            detail = norm(row.get("detail"))
            return f"{start:.1f}s-{end:.1f}s；{detail}" if detail else f"{start:.1f}s-{end:.1f}s"

        merged: list[dict[str, Any]] = []
        for effect in sorted(effects, key=lambda row: ("|".join(merge_key(row)), as_number(row.get("start")))):
            effect = dict(effect)
            if (
                merged
                and merge_key(merged[-1]) == merge_key(effect)
                and as_number(effect.get("start")) <= as_number(merged[-1].get("end")) + 0.05
                and not merged[-1].get("no_merge")
                and not effect.get("no_merge")
            ):
                merged[-1].setdefault("_merged_details", [merge_detail(merged[-1])])
                merged[-1]["_merged_details"].append(merge_detail(effect))
                merged[-1]["end"] = max(as_number(merged[-1].get("end")), as_number(effect.get("end")))
                merged[-1]["start"] = min(as_number(merged[-1].get("start")), as_number(effect.get("start")))
                merged[-1]["detail"] = "\n".join(merged[-1].get("_merged_details", []))
            else:
                merged.append(effect)
        for row in merged:
            row.pop("_merged_details", None)
        return sorted(merged, key=lambda row: (as_number(row.get("start")), as_number(row.get("end")), norm(row.get("name"))))

    @classmethod
    def _skill_burst_effect_category(cls, action: dict[str, Any], resist_priority: float = 0.0) -> str:
        if resist_priority > 0:
            return "resist"
        if cls._skill_burst_action_has_freeze(action):
            return "freeze"
        if action.get("team_buff") or is_acceleration_resistance_skill(action.get("skill")):
            return "team_buff"
        attrs = action.get("display_attrs") or action.get("attrs", {})
        if any(abs(as_number(attrs.get(key))) > 1e-9 for key in ("冷却缩减%", "动作速度%", "攻速%")):
            return "speed"
        if any(key in cls._team_amp_attr_keys() and abs(as_number(value)) > 1e-9 for key, value in action.get("attrs", {}).items()):
            return "amp"
        action_type = norm(action.get("type")).lower()
        if "召唤" in action_type or "工程" in norm(action.get("skill")):
            return "summon"
        if action.get("periodic_buff"):
            return "personal"
        return "state"

    @staticmethod
    def _skill_burst_collapse_resist_effects(
        effects: list[dict[str, Any]],
        relevant_attrs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        resist_effects = [effect for effect in effects if norm(effect.get("category")) == "resist"]
        if not resist_effects:
            return effects
        others = [effect for effect in effects if norm(effect.get("category")) != "resist"]

        element_attrs = {
            "光": "减光抗%",
            "暗": "减暗抗%",
            "水": "减水抗%",
            "火": "减火抗%",
        }

        def resist_values(effect: dict[str, Any]) -> dict[str, float]:
            values = {
                norm(attr): max(0.0, as_number(value))
                for attr, value in (effect.get("display_attrs") or effect.get("attrs") or {}).items()
                if norm(attr).startswith("减")
                and norm(attr).endswith("抗%")
                and "致命" not in norm(attr)
                and as_number(value) > 0
            }
            for attr, value in re.findall(
                r"(减(?:火|水|光|暗|全)抗%)\s*\+?\s*([0-9]+(?:\.[0-9]+)?)",
                norm(effect.get("attrs_text")),
            ):
                values.setdefault(attr, max(0.0, as_number(value)))
            return values

        # Parse every application once, then let the shared engine retain the
        # client admission decision across later timeline segments.
        values_by_id = {id(effect): resist_values(effect) for effect in resist_effects}
        available_attrs = {
            attr
            for effect in resist_effects
            for attr in values_by_id[id(effect)]
            if attr in element_attrs.values()
        }
        if any("减全抗%" in values_by_id[id(effect)] for effect in resist_effects):
            available_attrs.update(element_attrs.values())
        target_attrs = (
            set(relevant_attrs) & set(element_attrs.values())
            if relevant_attrs is not None
            else available_attrs
        )
        if not target_attrs:
            return others

        inputs: list[EffectInput] = []
        for effect_index, effect in enumerate(resist_effects):
            start = max(0.0, as_number(effect.get("start")))
            end = max(start, as_number(effect.get("end")))
            team_effect = effect.get("team_effect", {})
            if not isinstance(team_effect, dict):
                team_effect = {}
            skill = BuildEditor._team_effect_skill_name(
                team_effect.get("skill")
                or effect.get("skill")
                or effect.get("source")
                or effect.get("name")
            )
            source = norm(effect.get("source")) or norm(effect.get("name"))
            for attr, value in values_by_id[id(effect)].items():
                hit_assumed = effect_rule(EffectInput(
                    key=attr, value=value, skill_name=skill,
                )).group_mode == "exclusive_admission"
                inputs.append(
                    EffectInput(
                        key=attr,
                        value=value,
                        skill_name=skill,
                        source_id=source,
                        target_id="boss",
                        instance_id=f"{effect_index}:{attr}",
                        start=start,
                        end=end,
                        stack_group=(
                            "theory:hit-assumed-resist" if hit_assumed
                            else norm(team_effect.get("stack_group"))
                        ),
                        group_mode="max" if hit_assumed else "",
                        stackable=bool(effect.get("stackable"))
                        or bool(team_effect.get("stackable")),
                        metadata={"effect_index": effect_index},
                    )
                )

        boundaries = sorted({
            max(0.0, as_number(effect.get(key)))
            for effect in resist_effects
            for key in ("start", "end")
        })
        intervals = [
            (start, end)
            for start, end in zip(boundaries, boundaries[1:])
            if end > start + 1e-6
        ]
        resolved_intervals = EFFECT_STACKING_ENGINE.resolve_selected_series(
            inputs,
            ((start + end) / 2.0 for start, end in intervals),
        )
        active_windows: list[dict[str, Any]] = []
        for (start, end), (_at, values, selected_rows) in zip(
            intervals,
            resolved_intervals,
        ):
            for lane_order, (element, attr) in enumerate(element_attrs.items()):
                if attr not in target_attrs:
                    continue
                total = max(0.0, as_number(values.get(attr)))
                if total <= 0:
                    continue
                selected_indexes = list(
                    dict.fromkeys(
                        int(effect.metadata.get("effect_index", -1))
                        for effect, rule in selected_rows
                        if rule.key in {attr, "减全抗%"}
                    )
                )
                winner_effects = [
                    resist_effects[index]
                    for index in selected_indexes
                    if 0 <= index < len(resist_effects)
                ]
                if not winner_effects:
                    continue
                winner = winner_effects[0]
                sources = unique([
                    norm(effect.get("source")) or norm(effect.get("name"))
                    for effect in winner_effects
                    if norm(effect.get("source")) or norm(effect.get("name"))
                ])
                source = "+".join(sources)
                attrs_text = f"{attr}+{total:g}"
                current = dict(winner)
                refresh_tail = max(
                    (
                        max(0.0, as_number(effect.get("refresh_tail")))
                        for effect in winner_effects
                    ),
                    default=0.0,
                )
                effect_duration = max(
                    (
                        max(0.0, as_number(effect.get("effect_duration")))
                        for effect in winner_effects
                    ),
                    default=0.0,
                )
                refresh_detail = (
                    f"单次持续 {effect_duration:g}s；连续命中刷新约 {refresh_tail:g}s"
                    if refresh_tail > 0
                    else ""
                )
                current.update({
                    "start": start,
                    "end": end,
                    "name": "｜".join(filter(None, [f"当前减{element}抗", source, attrs_text])),
                    "source": source,
                    "lane_key": attr,
                    "lane_label": f"{element}减抗",
                    "lane_order": lane_order,
                    "attrs_text": attrs_text,
                    "attrs": {attr: total},
                    "display_attrs": {attr: total},
                    "priority": total,
                    "refresh_tail": refresh_tail,
                    "detail": "；".join(filter(None, [
                        f"当前实际生效减{element}抗 {total:g}%",
                        f"来源：{source}",
                        refresh_detail,
                    ])),
                })
                previous = next(
                    (
                        row for row in reversed(active_windows)
                        if norm(row.get("lane_key")) == attr
                        and abs(as_number(row.get("end")) - start) <= 1e-6
                    ),
                    None,
                )
                if (
                    previous is not None
                    and norm(previous.get("source")) == source
                    and norm(previous.get("attrs_text")) == attrs_text
                ):
                    previous["end"] = end
                else:
                    active_windows.append(current)
        return [*others, *active_windows]

    @staticmethod
    def _skill_burst_collapse_crit_resist_effects(
        effects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        aliases = {"减致命抵抗%", "减致命一击抵抗%", "减暴击抵抗%"}

        def effect_value(effect: dict[str, Any]) -> float:
            team_effect = effect.get("team_effect", {})
            sources = [
                team_effect.get("display_attrs") if isinstance(team_effect, dict) else {},
                team_effect.get("effective_attrs") if isinstance(team_effect, dict) else {},
                team_effect.get("attrs") if isinstance(team_effect, dict) else {},
                effect.get("display_attrs"),
                effect.get("attrs"),
            ]
            return max(
                [
                    max(0.0, as_number(value))
                    for attrs in sources
                    if isinstance(attrs, dict)
                    for key, value in attrs.items()
                    if norm(key) in aliases
                ]
                or [0.0]
            )

        crit_effects = [effect for effect in effects if effect_value(effect) > 0]
        if not crit_effects:
            return effects
        others = [effect for effect in effects if effect not in crit_effects]
        boundaries = sorted({
            max(0.0, as_number(effect.get(key)))
            for effect in crit_effects
            for key in ("start", "end")
        })
        inputs: list[EffectInput] = []
        for effect_index, effect in enumerate(crit_effects):
            team_effect = effect.get("team_effect", {})
            if not isinstance(team_effect, dict):
                team_effect = {}
            skill = BuildEditor._team_effect_skill_name(
                team_effect.get("skill")
                or effect.get("skill")
                or effect.get("source")
                or effect.get("name")
            )
            source = norm(effect.get("source")) or norm(effect.get("name"))
            inputs.append(
                EffectInput(
                    key="减致命抵抗%",
                    value=effect_value(effect),
                    skill_name=skill,
                    source_id=source,
                    target_id="boss",
                    instance_id=str(effect_index),
                    start=max(0.0, as_number(effect.get("start"))),
                    end=max(0.0, as_number(effect.get("end"))),
                    stack_group=norm(team_effect.get("stack_group")),
                    stackable=bool(effect.get("stackable"))
                    or bool(team_effect.get("stackable")),
                    metadata={"effect_index": effect_index},
                )
            )
        intervals = [
            (start, end) for start, end in zip(boundaries, boundaries[1:])
            if end > start + 1e-6
        ]
        # The inputs do not change between boundaries. Normalize/admit them
        # once; no inactive per-instance diagnostics are needed by this caller.
        resolved = EFFECT_STACKING_ENGINE.resolve_selected_series(
            inputs, ((start + end) / 2.0 for start, end in intervals),
            preserve_input_order=True,
        )
        collapsed: list[dict[str, Any]] = []
        for (start, end), (_midpoint, values, selected) in zip(intervals, resolved):
            value = max(
                0.0, as_number(values.get("减致命抵抗%"))
            )
            if value <= 0:
                continue
            selected_indexes = list(
                dict.fromkeys(
                    int(effect.metadata.get("effect_index", -1))
                    for effect, rule in selected
                    if rule.key == "减致命抵抗%"
                )
            )
            winners = [
                crit_effects[index]
                for index in selected_indexes
                if 0 <= index < len(crit_effects)
            ]
            if not winners:
                continue
            winner = winners[0]
            sources = unique(
                norm(effect.get("source")) or norm(effect.get("name"))
                for effect in winners
            )
            source = "+".join(sources)
            attrs = {"减致命抵抗%": value}
            team_effect = dict(winner.get("team_effect", {}))
            team_effect.update({
                "skill": "减致命抗debuff｜当前减致命抗",
                "attrs": dict(attrs),
                "display_attrs": dict(attrs),
            })
            current = dict(winner)
            current.update({
                "start": start,
                "end": end,
                "name": f"当前减致命抗｜{value:g}%｜来源：{source}",
                "source": "当前减致命抗",
                "category": "team_crit_resist",
                "color": SKILL_BURST_EFFECT_COLORS["team_crit_resist"],
                "lane_key": "减致命抵抗%",
                "lane_label": "减致命抗",
                "attrs_text": f"减致命抵抗%+{value:g}",
                "attrs": dict(attrs),
                "display_attrs": dict(attrs),
                "priority": value,
                "detail": f"当前实际生效减致命抗 {value:g}%；来源：{source}",
                "team_effect": team_effect,
            })
            if (
                collapsed
                and abs(as_number(collapsed[-1].get("end")) - start) <= 1e-6
                and abs(as_number(collapsed[-1].get("priority")) - value) <= 1e-9
            ):
                collapsed[-1]["end"] = end
                if source and source not in norm(collapsed[-1].get("detail")):
                    collapsed[-1]["detail"] += f"；来源：{source}"
            else:
                collapsed.append(current)
        return [*others, *collapsed]
