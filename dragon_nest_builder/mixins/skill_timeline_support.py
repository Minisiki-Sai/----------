from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
import os

from ..shared import *
from ..hp_conditional import (
    CROW_SKILL,
    DANCE_STAGE_TWO_SKILL,
    HIGH_HP_COEFFICIENT_ATTR,
    LOST_HP_COEFFICIENT_ATTR,
    boss_hp_percent_at,
    conditional_team_buff_gain_rate,
)

BuildEditor = None
_EXACT_ROUND2 = os.environ.get('DN_SCORE_EXACT_ROUND2', '1') == '1'

SKILL_BURST_NON_DAMAGING_SUPPORT_SKILLS = frozenset({"致残惩罚者"})

# Long-duration, non-stacking team effects that are expected to be maintained
# whenever their owner can act.  They must not compete as ordinary optional
# support actions: doing so lets a dense damage rotation postpone the refresh
# well past expiry, especially when the same profession appears more than once
# and teammate copies are correctly removed from each personal theory axis.
SKILL_BURST_MANDATORY_TEAM_UPTIME_SKILLS = frozenset({"瞄准"})

# These effects are emitted by the damaging cast itself.  Their team-effect
# rows still carry the pre-adjustment cooldown, while the skill-damage table
# carries the authoritative cooldown that the player can actually recast on.
# Keep this explicit: other support skills may expose an already-reduced DPS
# metric and must continue to use their raw support cooldown.
SKILL_BURST_DAMAGE_COOLDOWN_AUTHORITATIVE_EFFECTS = frozenset({
    ("银色猎人", "螺旋飓风"),
    ("黑暗萨满", "复仇之手"),
})


class SkillTimelineSupportMixin:
    def _skill_burst_is_non_damaging_support(self, skill: Any) -> bool:
        return (
            self._skill_burst_skill_key(skill)
            in SKILL_BURST_NON_DAMAGING_SUPPORT_SKILLS
        )

    @staticmethod
    def _skill_burst_next_pinned_support_start(
        actions: list[dict[str, Any]],
        current: float,
    ) -> float | None:
        """Return the next captured support cast that must reserve the GCD."""

        starts = [
            max(0.0, as_number(cast_start))
            for action in actions
            if action.get("pinned_cast_schedule")
            for cast_start, cast_end in action.get("scheduled_casts", ())
            if as_number(cast_end) > 1e-9
            and as_number(cast_start) > current + 1e-6
        ]
        return min(starts) if starts else None

    def _skill_burst_support_actions(self, metrics_by_skill: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        team_effects = (
            self._selected_team_effects(use_base_cooldowns=True, dedup=True)
            if (
                hasattr(self, "_extreme_selected_effects")
                or hasattr(self, "_timeline_selected_effects")
                or hasattr(self, "team_effect_table")
            )
            else []
        )
        # The team page is a separate scenario. It may supply accurate timing data
        # for this job, but teammates' skills must never become castable here.
        team_effects = self._skill_burst_current_job_team_effects(team_effects)
        resist_allocations = self._team_resist_allocations(team_effects)
        relevant_resist_attrs = self._skill_burst_relevant_resist_attrs(metrics_by_skill.values())
        support_keys = (
            self._team_resist_attr_keys()
            | self._team_amp_attr_keys()
            | self._team_freeze_attr_keys()
            | {"减防御%", "减致命抵抗%"}
        )
        actions: list[dict[str, Any]] = []
        for order, effect in enumerate(team_effects):
            if self._skill_burst_skill_key(self._team_effect_skill_name(effect.get("skill"))) in {
                self._skill_burst_skill_key(value)
                for value in getattr(self, "_team_practical_duplicate_build_buffs", set())
            }:
                continue
            skill = self._team_effect_skill_name(effect.get("skill"))
            if (re.search(r"\s*EX$", skill, re.IGNORECASE)
                    and self._skill_burst_learned_ex_node_level(skill) <= 0):
                continue
            raw_attrs = {
                norm(key): value for key, value in effect.get("attrs", {}).items()
            }
            source_attrs = {
                norm(key): value
                for key, value in effect.get("display_attrs", raw_attrs).items()
            }
            pinned_special = (
                self._skill_burst_skill_key(skill)
                in getattr(self, "_skill_burst_pinned_special_skill_keys", set())
            )
            pinned_cast_schedule = getattr(
                self, "_skill_burst_pinned_support_cast_schedule", {}
            )
            has_scheduled_casts = bool(
                isinstance(pinned_cast_schedule, dict)
                and self._skill_burst_skill_key(skill) in pinned_cast_schedule
            )
            scheduled_casts = (
                list(
                    pinned_cast_schedule.get(
                        self._skill_burst_skill_key(skill), ()
                    )
                )
                if has_scheduled_casts
                else []
            )
            attrs = (
                {
                    key: value
                    for key, value in raw_attrs.items()
                    if key
                    not in {
                        "\u51b7\u5374\u7f29\u51cf%",
                        "\u52a8\u4f5c\u901f\u5ea6%",
                        "\u653b\u901f%",
                    }
                }
                if pinned_special
                else raw_attrs
            )
            sitting_chakra = bool(
                "打坐曜" in norm(effect.get("provider"))
                and self._skill_burst_skill_key(skill) == "查克拉治愈术"
            )
            cooldown = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            if sitting_chakra:
                cooldown = 0.0
                duration = 3600.0
            periodic_buff_attrs = {
                key: value
                for key, value in attrs.items()
                if key not in support_keys and abs(as_number(value)) > 1e-9
            }
            periodic_multiplier = (
                self._skill_burst_panel_multiplier_for_attrs(periodic_buff_attrs)
                if periodic_buff_attrs
                and (sitting_chakra or cooldown > 0 and 0 < duration < cooldown - 1e-9)
                else 1.0
            )
            has_periodic_buff = periodic_multiplier > 1.0 + 1e-6
            allocated_resist = {
                attr: value
                for attr, value in resist_allocations.get(self._team_effect_key(effect), {}).items()
                if attr in relevant_resist_attrs
            }
            has_resist = bool(allocated_resist)
            has_other_support = any(
                key in support_keys
                and key not in self._team_resist_attr_keys()
                and abs(as_number(value)) > 1e-9
                for key, value in attrs.items()
            )
            timeline_special_cdr = bool(
                (
                    hasattr(self, "_extreme_selected_effects")
                    or (
                        hasattr(self, "_timeline_selected_effects")
                        and pinned_special
                    )
                )
                and is_acceleration_resistance_skill(skill)
                and any(
                    abs(as_number(source_attrs.get(key))) > 1e-9
                    for key in ("冷却缩减%", "动作速度%")
                )
            )
            if not has_resist and not has_other_support and not has_periodic_buff and not timeline_special_cdr:
                continue
            if not skill or skill == "无":
                continue
            if self._skill_burst_skill_key(skill) in SKILL_BURST_SIM_PASSIVE_SUMMONS:
                continue
            provider_job = self._team_provider_job(effect.get("provider", ""))
            if norm(provider_job) == norm(self.second_job.currentText()) and not sitting_chakra:
                build_level = self._skill_build_level_for_burst_sim(skill)
                if build_level is None or build_level <= 0:
                    continue
            tags: list[str] = []
            if has_resist:
                tags.append("减抗")
            if "减防御%" in attrs:
                tags.append("减防")
            if "减致命抵抗%" in attrs:
                tags.append("减致抗")
            if any(key in self._team_amp_attr_keys() for key in attrs):
                tags.append("增伤")
            if any(key in self._team_freeze_attr_keys() for key in attrs):
                tags.append("叠冰")
            if timeline_special_cdr:
                tags.append("团队buff")
            marker = f"__爆发buff__{self._skill_burst_skill_key(skill)}" if has_periodic_buff else ""
            action_attrs = dict(attrs)
            if marker:
                # Panel buffs are represented by active_multiplier while the
                # planner is choosing ordinary skills. Keeping their raw attrs
                # here applies the same buff a second time through the panel
                # calculator whenever the team page has already been created.
                runtime_attr_keys = support_keys | {"冷却缩减%", "动作速度%", "攻速%"}
                action_attrs = {
                    key: value
                    for key, value in attrs.items()
                    if key in runtime_attr_keys
                }
                action_attrs[marker] = 1.0
                tags.append("buff")
            actions.append(dict(
                kind="support",
                order=order,
                skill=skill,
                type="/".join(tags) or "前置",
                base_cooldown=cooldown,
                duration=duration,
                attrs=action_attrs,
                resist_attrs=allocated_resist,
                stackable_resist=skill in {"C2H5OH", "神圣大地"},
                periodic_buff=has_periodic_buff,
                buff_marker=marker,
                active_multiplier=periodic_multiplier,
                inactive_multiplier=1.0,
                display_attrs=periodic_buff_attrs,
                element_scope=self._skill_burst_periodic_buff_element_scope(periodic_buff_attrs),
                special_cdr=timeline_special_cdr,
                resistance_cooldown=(cooldown if timeline_special_cdr else 0.0),
                pinned_cast_schedule=has_scheduled_casts,
                scheduled_casts=scheduled_casts,
                initial_available_at=(
                    min(
                        max(0.0, as_number(cast_start))
                        for cast_start, cast_end in scheduled_casts
                        if as_number(cast_end) > 1e-9
                    )
                    if any(as_number(cast_end) > 1e-9 for _start, cast_end in scheduled_casts)
                    else (1.0e12 if has_scheduled_casts else 0.0)
                ),
                team_buff=has_periodic_buff,
                team_objective_effect=True,
                team_objective_attrs=raw_attrs,
                mandatory_uptime=(
                    timeline_special_cdr
                    or
                    sitting_chakra
                    or self._skill_burst_skill_key(skill)
                    in SKILL_BURST_MANDATORY_TEAM_UPTIME_SKILLS
                    or norm(self.second_job.currentText()) in {"刀锋舞者", "灵魂舞者"}
                    and self._skill_burst_skill_key(skill) in {"幽灵之魂", "九天之舞2阶"}
                ),
            ))
            metric = metrics_by_skill.get(self._skill_burst_skill_key(skill))
            if metric:
                actions[-1]["metric"] = metric
                actions[-1]["effective_score"] = as_number(metric.get("effective_score"))
                skill_key = self._skill_burst_skill_key(skill)
                metric_cooldown = max(
                    0.0,
                    as_number(
                        metric.get("base_cooldown"),
                        actions[-1].get("base_cooldown"),
                    ),
                )
                # The DPS metric may carry an already reduced/equivalent CD.
                # Team effects above deliberately request the raw skill CD, so
                # replacing it here applies cooldown reduction a second time.
                authoritative_damage_cooldown = (
                    norm(self.second_job.currentText()),
                    skill_key,
                ) in SKILL_BURST_DAMAGE_COOLDOWN_AUTHORITATIVE_EFFECTS
                if authoritative_damage_cooldown and metric_cooldown > 0:
                    actions[-1]["effect_base_cooldown"] = max(
                        0.0,
                        as_number(actions[-1].get("base_cooldown")),
                    )
                    actions[-1]["base_cooldown"] = metric_cooldown
                elif as_number(actions[-1].get("base_cooldown")) <= 0:
                    actions[-1]["base_cooldown"] = metric_cooldown
                actions[-1]["damage_base_cooldown"] = metric_cooldown
        existing_action_keys = {self._skill_burst_skill_key(action.get("skill")) for action in actions}
        for metric in metrics_by_skill.values():
            skill = norm(metric.get("skill"))
            skill_key = self._skill_burst_skill_key(skill)
            if skill_key in SKILL_BURST_SIM_PASSIVE_SUMMONS:
                continue
            manual_name = self._skill_burst_manual_effect_name_for_metric(skill)
            if not manual_name or skill_key in existing_action_keys:
                continue
            build_level = self._skill_build_level_for_burst_sim(skill)
            if build_level is not None and build_level <= 0:
                continue
            current_provider = f"队员1 {norm(self.second_job.currentText())}"
            if not self._team_manual_effect_applies(manual_name, current_provider):
                continue
            level_options = TEAM_MANUAL_DEBUFFS.get(manual_name, ({}, []))[1]
            level = str(level_options[-1]) if level_options else str(build_level or 1)
            attrs = self._team_effect_attrs(manual_name, level, current_provider, include_personal_buffs=True)
            team_objective_attrs = self._team_effect_attrs(
                manual_name,
                level,
                current_provider,
                include_personal_buffs=False,
            ) or {}
            timing = self._team_effect_timing(current_provider, manual_name, level)
            cooldown = max(0.0, as_number(metric.get("base_cooldown")) or as_number(timing.get("cooldown")))
            duration = max(0.0, as_number(timing.get("duration")))
            if not attrs or duration <= 0:
                continue
            tags: list[str] = []
            if any(key in self._team_resist_attr_keys() for key in attrs):
                tags.append("减抗")
            if any(key in self._team_amp_attr_keys() for key in attrs):
                tags.append("增伤")
            if any(key in self._team_freeze_attr_keys() for key in attrs):
                tags.append("叠冰")
            actions.append({
                "kind": "support",
                "order": 1000 + as_number(metric.get("row")),
                "skill": manual_name,
                "type": "/".join(tags) or "前置",
                "base_cooldown": cooldown,
                "duration": duration,
                "attrs": attrs,
                "resist_attrs": {
                    key: value
                    for key, value in attrs.items()
                    if key in relevant_resist_attrs and key in self._team_resist_attr_keys()
                },
                "stackable_resist": manual_name in {"C2H5OH", "神圣大地"},
                "team_objective_effect": bool(team_objective_attrs),
                "team_objective_attrs": dict(team_objective_attrs),
                "metric": metric,
                "effective_score": as_number(metric.get("effective_score")),
                "damage_base_cooldown": cooldown,
            })
            existing_action_keys.add(skill_key)
        periodic_actions = self._skill_burst_periodic_buff_actions()
        for action in periodic_actions:
            if self._skill_burst_is_non_damaging_support(action.get("skill")):
                action["non_damaging_support"] = True
                action["effective_score"] = 0.0
                action.pop("metric", None)
                continue
            # A cast can deal damage and activate a periodic personal/team buff
            # at the same time.  Keep one shared action, but retain its damage
            # metric so every scheduled refresh contributes damage and counts
            # as a real theoretical cast.  Without this binding skills such as
            # Sniper's Cheating Point were displayed repeatedly on the plan but
            # only the lone cast selected through the damage branch was scored.
            if norm(action.get("kind")) != "support" or action.get("metric"):
                continue
            metric = metrics_by_skill.get(
                self._skill_burst_skill_key(action.get("skill"))
            )
            if not isinstance(metric, dict):
                continue
            action["metric"] = metric
            action["effective_score"] = as_number(metric.get("effective_score"))
            action["damage_base_cooldown"] = max(
                0.0,
                as_number(
                    metric.get("base_cooldown"),
                    action.get("base_cooldown"),
                ),
            )
        actions.extend(periodic_actions)
        actions.extend(self._skill_burst_state_support_actions(metrics_by_skill))
        if norm(self.second_job.currentText()) == "烈" and "致残惩罚者" not in {
            self._skill_burst_skill_key(action.get("skill")) for action in actions
        }:
            cripple_metric = metrics_by_skill.get("致残惩罚者")
            learned_level = self._skill_build_level_for_burst_sim("致残惩罚者")
            if cripple_metric and learned_level is not None and learned_level > 0:
                marker = "__爆发buff__致残惩罚者"
                actions.append({
                    "kind": "support",
                    "order": 1900,
                    "skill": "致残惩罚者",
                    "type": "个人buff",
                    "base_cooldown": max(0.0, as_number(cripple_metric.get("base_cooldown"), 125.0)),
                    "duration": 10.0,
                    "attrs": {"动作速度%": 30.0, marker: 1.0},
                    "resist_attrs": {},
                    "stackable_resist": False,
                    "periodic_buff": True,
                    "buff_marker": marker,
                    "active_multiplier": 1.0,
                    "inactive_multiplier": 1.0,
                    "display_attrs": {"动作速度%": 30.0},
                    "element_scope": "",
                    "non_damaging_support": True,
                    "effective_score": 0.0,
                    "damage_base_cooldown": max(0.0, as_number(cripple_metric.get("base_cooldown"), 125.0)),
                })
        filtered = self._filter_skill_burst_support_actions(actions)
        merged = sorted(self._merge_skill_burst_support_actions(filtered), key=lambda action: as_number(action.get("order")))
        triggered_ex_actions: list[dict[str, Any]] = []
        for action in merged:
            if self._skill_burst_is_non_damaging_support(action.get("skill")):
                action["non_damaging_support"] = True
                action["effective_score"] = 0.0
                action.pop("metric", None)
            action["duration"] = self._skill_glyph_buff_duration(
                action.get("skill", ""),
                action.get("duration", 0.0),
            )
            if is_acceleration_resistance_skill(action.get("skill")):
                resistance_cooldown = max(
                    0.0, as_number(action.get("resistance_cooldown"))
                )
                if resistance_cooldown > 0:
                    action["base_cooldown"] = resistance_cooldown
            if (
                self._skill_burst_skill_key(action.get("skill")) == "霜冻"
                and "减水抗%" in relevant_resist_attrs
            ):
                # Frost keeps its base 30% water-resistance reduction for 12s.
                # Frost EX adds a separate four-second freeze layer below; it
                # must not replace the base debuff when both share one cast.
                action["duration"] = max(12.0, as_number(action.get("duration")))
                action.setdefault("attrs", {})["减水抗%"] = max(
                    30.0,
                    as_number(action.get("attrs", {}).get("减水抗%")),
                )
                action.setdefault("resist_attrs", {})["减水抗%"] = max(
                    30.0,
                    as_number(action.get("resist_attrs", {}).get("减水抗%")),
                )
                action.setdefault("display_attrs", {})["减水抗%"] = 30.0
                tags = [part for part in norm(action.get("type")).split("/") if part]
                if "减抗" not in tags:
                    tags.insert(0, "减抗")
                action["type"] = "/".join(tags)
            if (
                self._skill_burst_skill_key(action.get("skill")) == "霜冻"
                and self._skill_burst_learned_ex_node_level("霜冻EX") > 0
            ):
                frost_ex_level = max(
                    1,
                    self._skill_burst_learned_ex_node_level("霜冻EX"),
                )
                frost_ex_timing = self._team_effect_timing(
                    f"队员1 {norm(self.second_job.currentText())}",
                    "霜冻EX",
                    str(frost_ex_level),
                )
                freeze_duration = max(
                    0.0,
                    as_number(frost_ex_timing.get("duration"), 4.0),
                ) or 4.0
                action.setdefault("attrs", {}).pop("冰冻层", None)
                action.setdefault("display_attrs", {}).pop("冰冻层", None)
                action["type"] = "/".join(
                    part for part in norm(action.get("type")).split("/")
                    if part and part != "叠冰"
                )
                triggered_ex_actions.append({
                    "kind": "support",
                    "order": as_number(action.get("order")) + 0.01,
                    "skill": "霜冻EX叠冰",
                    "display_name": "霜冻EX（随霜冻触发）",
                    "type": "叠冰",
                    "base_cooldown": 0.0,
                    "duration": freeze_duration,
                    "attrs": {"冰冻层": 1.0},
                    "display_attrs": {"冰冻层": 1.0},
                    "resist_attrs": {},
                    "triggered_only": True,
                    "trigger_skills": ["霜冻"],
                    "effective_score": 0.0,
                })
            if self._skill_burst_skill_key(action.get("skill")) == "痊愈之手":
                action["refresh_interval"] = 2.0
        merged.extend(triggered_ex_actions)
        merged.sort(key=lambda action: as_number(action.get("order")))
        initial_phases = getattr(self, "_skill_burst_support_initial_phases", {})
        if isinstance(initial_phases, dict):
            for action in merged:
                phase = max(
                    0.0,
                    min(1.0, as_number(initial_phases.get(self._skill_burst_skill_key(action.get("skill"))))),
                )
                if phase > 0:
                    action["initial_available_at"] = max(0.0, as_number(action.get("base_cooldown"))) * phase
        initial_times = getattr(self, "_skill_burst_support_initial_times", {})
        if isinstance(initial_times, dict):
            for action in merged:
                skill_key = self._skill_burst_skill_key(action.get("skill"))
                if skill_key in initial_times:
                    if action.get("pinned_cast_schedule"):
                        scheduled_starts = [
                            max(0.0, as_number(cast_start))
                            for cast_start, cast_end in action.get("scheduled_casts", ())
                            if as_number(cast_end) > as_number(cast_start) + 1e-9
                        ]
                        action["initial_available_at"] = (
                            min(scheduled_starts) if scheduled_starts else 1.0e12
                        )
                    else:
                        action["initial_available_at"] = max(
                            0.0, as_number(initial_times.get(skill_key))
                        )
        snapshot_rules = JOB_MECHANICS.job_value(
            norm(self.second_job.currentText()),
            "snapshot_buffs",
            {},
        )
        if isinstance(snapshot_rules, dict):
            normalized_snapshot_rules = {
                self._skill_burst_skill_key(skill): rule
                for skill, rule in snapshot_rules.items()
                if self._skill_burst_skill_key(skill) and isinstance(rule, dict)
            }
            for action in merged:
                rule = normalized_snapshot_rules.get(
                    self._skill_burst_skill_key(action.get("skill"))
                )
                if rule:
                    action["snapshot_rule"] = copy.deepcopy(rule)
        self._skill_burst_periodic_buffs = [action for action in merged if action.get("periodic_buff")]
        owner_weights = self._skill_burst_resist_damage_weights(metrics_by_skill.values())
        for action in merged:
            action["owner_resist_damage_weights"] = owner_weights
        return merged

    def _skill_burst_current_job_team_effects(
        self,
        effects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        provider_override = norm(getattr(self, "_skill_burst_current_provider_override", ""))
        if provider_override:
            return [
                effect
                for effect in effects
                if norm(effect.get("provider")) == provider_override
            ]
        current_job = norm(self.second_job.currentText())
        return [
            effect
            for effect in effects
            if norm(self._team_provider_job(effect.get("provider", ""))) == current_job
        ]

    def _skill_burst_state_support_actions(
        self,
        metrics_by_skill: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        job = norm(self.second_job.currentText())
        actions: list[dict[str, Any]] = []
        if job in {"重炮手", "机械大师"}:
            field_level = self._skill_build_level_for_burst_sim("工程领域")
            if field_level is None or field_level > 0:
                field_multiplier = self._skill_burst_engineering_field_multiplier()
                actions.append({
                    "kind": "support",
                    "order": 1650,
                    "skill": "工程领域",
                    "type": "召唤物buff",
                    # 游戏原始冷却为2秒；循环只需在21秒召唤物增益结束时补放。
                    "base_cooldown": 21.0,
                    "game_cooldown": 2.0,
                    "duration": 21.0,
                    "attrs": {SKILL_BURST_ENGINEERING_FIELD_MARKER: 1.0},
                    "display_attrs": {"召唤物按最大物攻": 1.0},
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": SKILL_BURST_ENGINEERING_FIELD_MARKER,
                    "active_multiplier": field_multiplier,
                    "inactive_multiplier": 1.0,
                    "skill_scope": sorted(SKILL_BURST_ENGINEER_SUMMONS),
                    "effective_score": 0.0,
                })
        if job == "圣骑士":
            taunt_level = self._skill_build_level_for_burst_sim("挑衅")
            if taunt_level is None or taunt_level > 0:
                taunt_skill = self._skill_build_entry_for_skill_name("挑衅")
                if taunt_level is None:
                    taunt_level = int(as_number(taunt_skill.get("max_level"), 6))
                taunt_desc = self._skill_build_desc_at_level(taunt_skill, int(taunt_level))
                taunt_duration = self._duration_from_text(taunt_desc) or 16.0
                taunt_cooldown = max(0.0, self._skill_burst_original_cooldown("挑衅") or 10.0)
                actions.append({
                    "kind": "support",
                    "order": 1640,
                    "skill": "挑衅",
                    "type": "仇恨/护卫者幻影",
                    "base_cooldown": taunt_cooldown,
                    "duration": taunt_duration,
                    "attrs": {SKILL_BURST_GUARDIAN_PHANTOM_MARKER: 1.0},
                    "display_attrs": {"护卫者幻影": 1.0},
                    "resist_attrs": {},
                    "state_only": True,
                    "mandatory_uptime": True,
                    "effective_score": 0.0,
                })
        if job == "圣徒":
            relic_specs = (
                ("治愈之手", 60.0, 8.0),
                ("痊愈之手", 25.0, 18.0),
                ("掌握之手", 37.0, 27.0),
            )
            for relic_order, (skill, fallback_cooldown, duration) in enumerate(relic_specs, start=1645):
                learned = self._skill_build_level_for_burst_sim(skill)
                if learned is not None and learned <= 0:
                    continue
                actions.append({
                    "kind": "support",
                    "order": relic_order,
                    "skill": skill,
                    "type": "圣物召唤",
                    "base_cooldown": max(
                        0.0,
                        self._skill_burst_original_cooldown(skill) or fallback_cooldown,
                    ),
                    "duration": duration,
                    "attrs": {},
                    "display_attrs": {"冲击之手计数": 1.0},
                    "resist_attrs": {},
                    "state_only": True,
                    "summon_state": True,
                    "stackable_summon": True,
                    "mandatory_uptime": True,
                    "effective_score": 0.0,
                })
        if job == "机械大师":
            mode_level = self._skill_build_level_for_burst_sim("机械模式")
            if mode_level is None or mode_level > 0:
                moment_level = self._skill_build_level_for_burst_sim("机械时刻")
                active_multiplier, inactive_multiplier = self._skill_burst_triggered_buff_multipliers(
                    "机械模式",
                    {"物攻%": 18.0},
                )
                actions.append({
                    "kind": "support",
                    "order": 1660,
                    "skill": "机械模式",
                    "display_name": "机械模式（光环）",
                    "type": "光环buff",
                    "base_cooldown": 0.0,
                    "duration": 3600.0,
                    "attrs": {SKILL_BURST_MECHANIC_MODE_MARKER: 1.0},
                    "display_attrs": {"物攻%": 18.0},
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": SKILL_BURST_MECHANIC_MODE_MARKER,
                    "active_multiplier": active_multiplier,
                    "inactive_multiplier": inactive_multiplier,
                    "aura_toggle": True,
                    "effective_score": 0.0,
                })
                if moment_level is None or moment_level > 0:
                    moment_active, moment_inactive = self._skill_burst_triggered_buff_multipliers(
                        "机械时刻",
                        {"物攻%": 20.0, "致命面板%": 20.0},
                    )
                    actions.append({
                        "kind": "support",
                        "order": 1661,
                        "skill": "机械时刻",
                        "type": "触发buff",
                        "base_cooldown": 60.0,
                        "duration": 30.0,
                        "attrs": {SKILL_BURST_MECHANIC_MOMENT_MARKER: 1.0},
                        "display_attrs": {"物攻%": 20.0, "致命面板%": 20.0},
                        "resist_attrs": {},
                        "periodic_buff": True,
                        "buff_marker": SKILL_BURST_MECHANIC_MOMENT_MARKER,
                        "active_multiplier": moment_active,
                        "inactive_multiplier": moment_inactive,
                        "triggered_only": True,
                        "effective_score": 0.0,
                    })
        if job == "噬魂者":
            learned = self._skill_build_level_for_burst_sim("幽灵复仇者")
            metric = metrics_by_skill.get("幽灵复仇者")
            if learned is not None and learned > 0:
                actions.append({
                    "kind": "support", "order": 1670, "skill": "幽灵复仇者", "type": "个人buff",
                    "base_cooldown": 125.0, "damage_base_cooldown": 125.0, "duration": 18.0,
                    "attrs": {"__爆发buff__幽灵复仇者": 1.0, "冷却缩减%": 30.0, "动作速度%": 30.0},
                    "display_attrs": {"冷却缩减%": 30.0, "动作速度%": 30.0},
                    "resist_attrs": {}, "periodic_buff": True,
                    "buff_marker": "__爆发buff__幽灵复仇者",
                    "active_multiplier": 1.0, "inactive_multiplier": 1.0,
                    "metric": metric if isinstance(metric, dict) else None,
                    "effective_score": as_number((metric or {}).get("effective_score")),
                })
        if job in {"影", "烈"}:
            learned = self._skill_build_level_for_burst_sim("暗淡消逝")
            if learned is not None and learned > 0:
                active_multiplier, inactive_multiplier = self._skill_burst_triggered_buff_multipliers(
                    "暗淡消逝", {"物攻%": 35.0, "魔攻%": 35.0}
                )
                actions.append({
                    "kind": "support", "order": 1671, "skill": "暗淡消逝", "type": "个人buff",
                    "base_cooldown": 20.0, "duration": 5.0,
                    "attrs": {"__爆发buff__暗淡消逝": 1.0},
                    "display_attrs": {"物攻%": 35.0, "魔攻%": 35.0}, "resist_attrs": {},
                    "periodic_buff": True, "buff_marker": "__爆发buff__暗淡消逝",
                    "active_multiplier": active_multiplier, "inactive_multiplier": inactive_multiplier,
                    "effective_score": 0.0,
                })
        if job in {"刀锋舞者", "灵魂舞者"}:
            preferred_dance = "弗里兹科之舞" if job == "刀锋舞者" else "幻影之舞"
            preferred_scope = (
                SKILL_BURST_BLADE_DANCE_SCOPE
                if job == "刀锋舞者"
                else SKILL_BURST_SPIRIT_DANCE_SCOPE
            )
            learned = self._skill_build_level_for_burst_sim(preferred_dance)
            metric = metrics_by_skill.get(preferred_dance)
            if learned is not None and learned > 0:
                timing = self.store.skill_effect_timing(job, preferred_dance, learned)
                final_percent = max(
                    0.0,
                    as_number(getattr(self, "_skill_burst_base_summary", {}).get("final_percent")),
                )
                marker = f"__爆发buff__{preferred_dance}"
                actions.append({
                    "kind": "support",
                    "order": 1670,
                    "skill": preferred_dance,
                    "type": "个人buff",
                    "base_cooldown": max(0.0, as_number(timing.get("cooldown"), 15.0)),
                    "damage_base_cooldown": max(0.0, as_number(timing.get("cooldown"), 15.0)),
                    "duration": max(0.0, as_number(timing.get("duration"), 12.0)),
                    "attrs": {marker: 1.0},
                    "display_attrs": {"最终%": 60.0},
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": marker,
                    "active_multiplier": (100 + final_percent + 60.0) / max(1e-9, 100 + final_percent),
                    "inactive_multiplier": 1.0,
                    "skill_scope": sorted(preferred_scope),
                    "metric": metric if isinstance(metric, dict) else None,
                    "effective_score": as_number((metric or {}).get("effective_score")),
                    "mandatory_uptime": True,
                })
            dance_specs = (
                ("弗里兹科", 1672, 5.0, 5.0, {"致命面板%": 30.0}, SKILL_BURST_BLADE_DANCE_SCOPE),
                ("幻影凝视", 1673, 20.0, 10.0, {"动作速度%": 15.0}, SKILL_BURST_SPIRIT_DANCE_SCOPE),
            )
            for skill, order, cooldown, duration, display_attrs, scope in dance_specs:
                learned = self._skill_build_level_for_burst_sim(skill)
                metric = metrics_by_skill.get(skill)
                if learned is None or learned <= 0:
                    continue
                active_multiplier, inactive_multiplier = self._skill_burst_triggered_buff_multipliers(
                    skill, display_attrs
                )
                marker = f"__爆发buff__{skill}"
                timing_attrs = {
                    key: value for key, value in display_attrs.items()
                    if key in {"动作速度%", "攻速%", "冷却缩减%"}
                }
                actions.append({
                    "kind": "support", "order": order, "skill": skill, "type": "个人buff",
                    "base_cooldown": cooldown, "damage_base_cooldown": cooldown, "duration": duration,
                    "attrs": {marker: 1.0, **timing_attrs}, "display_attrs": display_attrs,
                    "resist_attrs": {}, "periodic_buff": True, "buff_marker": marker,
                    "active_multiplier": active_multiplier, "inactive_multiplier": inactive_multiplier,
                    "skill_scope": sorted(scope),
                    "metric": metric if isinstance(metric, dict) else None,
                    "effective_score": as_number((metric or {}).get("effective_score")),
                })
        if job == "狂战士":
            mastery_level = self._skill_build_level_for_id("507")
            if mastery_level is None or mastery_level > 0:
                display_attrs = {"致命面板%": 20.0, "力量%": 50.0}
                active_multiplier, inactive_multiplier = self._skill_burst_triggered_buff_multipliers(
                    "致命一击掌握",
                    display_attrs,
                )
                actions.append({
                    "kind": "support",
                    "order": 1700,
                    "skill": "致命一击掌握触发",
                    "cast_skill": "反击怒吼",
                    "display_name": "致命一击掌握（反击怒吼）",
                    "type": "个人buff",
                    "base_cooldown": max(0.0, self._skill_burst_original_cooldown("反击怒吼") or 13.0),
                    "duration": 10.0,
                    "attrs": {SKILL_BURST_BERSERKER_MASTERY_MARKER: 1.0},
                    "display_attrs": display_attrs,
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": SKILL_BURST_BERSERKER_MASTERY_MARKER,
                    "active_multiplier": active_multiplier,
                    "inactive_multiplier": inactive_multiplier,
                    "effective_score": 0.0,
                    "mandatory_uptime": True,
                })
        if job == "毁灭者":
            mastery_level = self._skill_build_level_for_id("557")
            if mastery_level is None or mastery_level > 0:
                active_multiplier, inactive_multiplier = self._skill_burst_triggered_buff_multipliers(
                    "力量掌握",
                    {"独立伤害乘区%": 10.0},
                )
                actions.append({
                    "kind": "support",
                    "order": 1700,
                    "skill": "力量掌握触发",
                    "cast_skill": "战吼",
                    "display_name": "力量掌握（战吼/反击怒吼/毁灭呐喊）",
                    "type": "个人buff",
                    "base_cooldown": max(0.0, self._skill_burst_original_cooldown("战吼") or 10.0),
                    "duration": 10.0,
                    "attrs": {SKILL_BURST_DESTROYER_MASTERY_MARKER: 1.0},
                    "display_attrs": {"独立伤害乘区%": 10.0},
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": SKILL_BURST_DESTROYER_MASTERY_MARKER,
                    "active_multiplier": active_multiplier,
                    "inactive_multiplier": inactive_multiplier,
                    "effective_score": 0.0,
                    "mandatory_uptime": True,
                })
        if job in {"噬魂者", "黑暗萨满"}:
            puppet_level = self._skill_build_level_for_burst_sim("召唤傀儡")
            if puppet_level is not None and puppet_level > 0:
                puppet_metric = metrics_by_skill.get("召唤傀儡")
                puppet_timing = self._team_effect_timing(
                    job,
                    "召唤傀儡",
                    puppet_level,
                )
                puppet_attrs = self._team_effect_attrs(
                    "召唤傀儡",
                    puppet_level,
                    job,
                    include_personal_buffs=True,
                ) or {"增伤%": 20.0}
                puppet_team_attrs = self._team_effect_attrs(
                    "召唤傀儡",
                    puppet_level,
                    job,
                    include_personal_buffs=False,
                ) or {"增伤%": 20.0}
                actions.append({
                    "kind": "support",
                    "order": 1799,
                    "skill": "召唤傀儡",
                    "type": "烙印增伤",
                    "base_cooldown": max(
                        0.0,
                        as_number(puppet_timing.get("cooldown"), 30.0),
                    ),
                    "damage_base_cooldown": max(
                        0.0,
                        as_number((puppet_metric or {}).get("base_cooldown"), 30.0),
                    ),
                    "duration": max(
                        0.0,
                        as_number(puppet_timing.get("duration"), 14.0),
                    ),
                    "attrs": puppet_attrs,
                    "display_attrs": puppet_attrs,
                    "team_objective_effect": True,
                    "team_objective_attrs": dict(puppet_team_attrs),
                    "resist_attrs": {},
                    "metric": puppet_metric if isinstance(puppet_metric, dict) else None,
                    "effective_score": as_number((puppet_metric or {}).get("effective_score")),
                })
            metric = metrics_by_skill.get("科兹污点")
            learned = self._skill_build_level_for_burst_sim("科兹污点")
            if metric and learned is not None and learned > 0:
                marker = SKILL_BURST_BRAND_MARKER
                actions.append({
                    "kind": "support",
                    "order": 1800,
                    "skill": "科兹污点",
                    "type": "烙印前置",
                    "base_cooldown": max(0.0, as_number(metric.get("base_cooldown"), 10.0)),
                    "damage_base_cooldown": max(0.0, as_number(metric.get("base_cooldown"), 10.0)),
                    "duration": 23.0,
                    "attrs": {marker: 1.0},
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": marker,
                    "state_dependents": ("灵魂之门", "灵魂之纸", "召唤傀儡"),
                    "defer_ready_damage_count": 1,
                    "active_multiplier": 1.0,
                    "inactive_multiplier": 1.0,
                    "display_attrs": {"烙印": 1.0},
                    "metric": metric,
                    "effective_score": as_number(metric.get("effective_score")),
                })
        if job in {"刀锋舞者", "灵魂舞者"}:
            stage_one = metrics_by_skill.get("九天之舞")
            stage_two_level = self._skill_build_level_for_burst_sim("九天之舞2阶")
            stage_one_level = self._skill_build_level_for_burst_sim("九天之舞")
            if stage_one_level is not None and stage_one_level > 0 and stage_two_level is not None and stage_two_level > 0:
                marker = SKILL_BURST_DANCE_STAGE_MARKER
                stage_one_action = {
                    "kind": "support",
                    "order": 1790,
                    "skill": "九天之舞",
                    "type": "阶段前置",
                    "base_cooldown": max(0.0, as_number((stage_one or {}).get("base_cooldown"), 90.0)),
                    "damage_base_cooldown": max(0.0, as_number((stage_one or {}).get("base_cooldown"), 90.0)),
                    "duration": 5.0,
                    "attrs": {marker: 1.0},
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": marker,
                    "active_multiplier": 1.0,
                    "inactive_multiplier": 1.0,
                    "display_attrs": {"可衔接2阶": 1.0},
                    "effective_score": as_number((stage_one or {}).get("effective_score")),
                    "mandatory_uptime": True,
                }
                if isinstance(stage_one, dict):
                    stage_one_action["metric"] = stage_one
                actions.append(stage_one_action)
                stage_two = metrics_by_skill.get("九天之舞2阶")
                stage_two_attrs = self._team_effect_attrs(
                    "九天之舞2阶",
                    str(max(1, int(stage_two_level))),
                    job,
                    include_personal_buffs=True,
                )
                stage_two_team_attrs = self._team_effect_attrs(
                    "九天之舞2阶",
                    str(max(1, int(stage_two_level))),
                    job,
                    include_personal_buffs=False,
                ) or {}
                actions.append({
                    "kind": "support",
                    "order": 1791,
                    "skill": "九天之舞2阶",
                    "type": "团队buff",
                    "base_cooldown": max(0.0, as_number((stage_two or {}).get("base_cooldown"), 90.0)),
                    "damage_base_cooldown": max(0.0, as_number((stage_two or {}).get("base_cooldown"), 90.0)),
                    "duration": 15.0,
                    "attrs": stage_two_attrs,
                    "resist_attrs": {},
                    "periodic_buff": True,
                    "buff_marker": "__爆发buff__九天之舞2阶",
                    "active_multiplier": 1.225,
                    "inactive_multiplier": 1.0,
                    "display_attrs": stage_two_attrs,
                    "team_objective_effect": True,
                    "team_objective_attrs": dict(stage_two_team_attrs),
                    "metric": stage_two if isinstance(stage_two, dict) else None,
                    "effective_score": as_number((stage_two or {}).get("effective_score")),
                    "mandatory_uptime": True,
                })
        actions.extend(self._skill_burst_rule_trigger_actions())
        return actions

    def _skill_burst_rule_trigger_actions(self) -> list[dict[str, Any]]:
        job = norm(self.second_job.currentText())
        job_sources = set(
            self.store.job_buff_sources(
                job,
                self.first_job.currentText() if hasattr(self, "first_job") else "",
            )
        )
        explicit_skills = {"力量掌握", "致命一击掌握", "机械时刻"}
        actions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self.store.tables.get("buff", []):
            source = norm(row.get("来源"))
            if norm(row.get("类别")) != "个人buff" or source not in job_sources:
                continue
            skill = norm(row.get("技能"))
            skill_key = self._skill_burst_skill_key(skill)
            if not skill_key or skill_key in seen or skill_key in explicit_skills:
                continue
            rule = self.store.buff_activation_rule(source, skill, "个人buff")
            if norm(rule.get("mode")) != "triggered":
                continue
            learned = self._skill_build_level_for_burst_sim(skill)
            if learned is not None and learned <= 0:
                continue
            label = buff_display_label(row)
            levels = self.store.skill_levels(label)
            level = max(levels, key=as_number) if levels else "1"
            attrs = self.calculator.buff_attrs(label, level)
            timeline_attrs = {norm(attr) for attr in rule.get("timeline_attrs", []) if norm(attr)}
            if timeline_attrs:
                attrs = {attr: value for attr, value in attrs.items() if norm(attr) in timeline_attrs}
            duration = max(0.0, as_number(rule.get("duration")))
            if duration <= 0 and rule.get("duration_from_skill"):
                timing = self._team_effect_timing(
                    f"队员1 {norm(self.second_job.currentText())}", skill, level
                )
                duration = max(0.0, as_number(timing.get("duration")))
            if not attrs or duration <= 0:
                continue
            skill_scope = [
                self._skill_burst_skill_key(value)
                for value in rule.get("skill_scope", [])
                if self._skill_burst_skill_key(value)
            ]
            if skill_scope:
                skill_power = max(0.0, as_number(attrs.get("技能攻击力%")))
                first_target = (
                    max(0.0, as_number(attrs.get("首目标增伤%")))
                    if rule.get("first_target")
                    else 0.0
                )
                active_multiplier = 1.0 + (skill_power + first_target) / 100.0
                inactive_multiplier = 1.0
            else:
                active_multiplier, inactive_multiplier = self._skill_burst_triggered_buff_multipliers(
                    skill,
                    attrs,
                )
            marker = f"__触发buff__{job}__{skill_key}"
            actions.append({
                "kind": "support",
                "order": 1720 + len(actions),
                "skill": f"{skill_key}触发状态",
                "display_name": skill,
                "type": "触发型个人buff",
                "base_cooldown": 0.0,
                "duration": duration,
                "attrs": {marker: 1.0},
                "display_attrs": attrs,
                "resist_attrs": {},
                "periodic_buff": True,
                "buff_marker": marker,
                "active_multiplier": active_multiplier,
                "inactive_multiplier": inactive_multiplier,
                "triggered_only": True,
                "trigger_skills": [
                    self._skill_burst_skill_key(value)
                    for value in rule.get("trigger_skills", [])
                    if self._skill_burst_skill_key(value)
                ],
                "trigger_element": norm(rule.get("trigger_element")),
                "trigger_on_damage": bool(rule.get("trigger_on_damage")),
                "skill_scope": skill_scope,
                "effective_score": 0.0,
            })
            seen.add(skill_key)
        return actions

    def _skill_burst_engineering_field_multiplier(self) -> float:
        minimum = maximum = 0.0
        try:
            rows, _summary = self.calculator.calculate(self.collect_build())
            row_values = {norm(row.attr): max(0.0, as_number(row.buffed)) for row in rows}
            minimum = row_values.get("最小物攻", 0.0)
            maximum = row_values.get("最大物攻", 0.0)
        except Exception:
            pass
        if maximum <= 0:
            average_fallback = max(
                0.0,
                as_number(getattr(self, "_skill_burst_base_summary", {}).get("physical_average")),
            )
            minimum = maximum = average_fallback
        maximum = max(minimum, maximum)
        average = (minimum + maximum) / 2
        return maximum / average if average > 0 else 1.0

    def _skill_burst_triggered_buff_multipliers(
        self,
        buff_skill: str,
        attrs: dict[str, float],
    ) -> tuple[float, float]:
        try:
            base_build = (self.build if _EXACT_ROUND2 and getattr(self, '_timeline_is_pure_runtime', False)
                          else self.collect_build())
            _rows, base_summary = self.calculator.calculate(base_build)
            base_panel = max(0.0, as_number(base_summary.get("effective_panel")))
            if base_panel <= 0:
                return 1.0, 1.0
            selected = any(
                self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill")))
                == self._skill_burst_skill_key(buff_skill)
                for item in base_build.get("buffs", [])
                if isinstance(item, dict)
            )
            # Only buffs and extra_buff_attrs are replaced below; calculator
            # reads the remaining equipment/skills/combat context unchanged.
            adjusted_build = dict(base_build) if _EXACT_ROUND2 else copy.deepcopy(base_build)
            if selected:
                adjusted_build["buffs"] = [
                    item
                    for item in adjusted_build.get("buffs", [])
                    if self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill")))
                    != self._skill_burst_skill_key(buff_skill)
                ]
            _rows, inactive_summary = self.calculator.calculate(adjusted_build)
            inactive_multiplier = max(0.0, as_number(inactive_summary.get("effective_panel")) / base_panel)
            extra_attrs = dict(adjusted_build.get("extra_buff_attrs", {}))
            for attr, value in attrs.items():
                extra_attrs[norm(attr)] = as_number(extra_attrs.get(norm(attr))) + as_number(value)
            adjusted_build["extra_buff_attrs"] = extra_attrs
            _rows, active_summary = self.calculator.calculate(adjusted_build)
            return max(0.0, as_number(active_summary.get("effective_panel")) / base_panel), inactive_multiplier
        except Exception:
            return 1.0, 1.0

    def _skill_burst_periodic_buff_actions(self) -> list[dict[str, Any]]:
        jobs = {norm(self.first_job.currentText()), norm(self.second_job.currentText()), "通用"}
        selected_levels: dict[str, str] = {}
        buff_table = getattr(self, "buff_table", None)
        if isinstance(buff_table, QTableWidget):
            for row in range(buff_table.rowCount()):
                label = self.combo_text(buff_table, row, 0)
                skill = self._team_effect_skill_name(label)
                level = self.combo_text(buff_table, row, 1)
                if skill and skill != "无" and level and level != "无":
                    selected_levels[self._skill_burst_skill_key(skill)] = level

        base_build = (self.build if _EXACT_ROUND2 and getattr(self, '_timeline_is_pure_runtime', False)
                      else self.collect_build())
        for item in base_build.get("buffs", []):
            if not isinstance(item, dict):
                continue
            skill = self._team_effect_skill_name(item.get("skill"))
            level = norm(item.get("level"))
            if skill and skill != "无" and level and level != "无":
                selected_levels.setdefault(self._skill_burst_skill_key(skill), level)

        buff_rows: dict[str, dict[str, Any]] = {}
        for row in self.store.tables.get("buff", []):
            category = norm(row.get("类别"))
            source = norm(row.get("来源"))
            skill = norm(row.get("技能"))
            if category not in {"个人buff", "团队buff"} or source not in jobs or not skill:
                continue
            key = self._skill_burst_skill_key(skill)
            current = buff_rows.setdefault(key, {"skill": skill, "category": category, "source": source})
            if source == norm(self.second_job.currentText()):
                current.update({"skill": skill, "category": category, "source": source})

        # HP-conditional active buffs live in the team-effect catalog rather
        # than the regular buff table.  Keep learned self-casts available to
        # standalone long simulations as well as team/score simulations.
        current_job = norm(self.second_job.currentText())
        for skill in TEAM_JOB_BUFFS.get(current_job, []):
            skill = self._team_effect_skill_name(skill)
            skill_key = self._skill_burst_skill_key(skill)
            if skill_key not in {CROW_SKILL, DANCE_STAGE_TWO_SKILL}:
                continue
            buff_rows.setdefault(
                skill_key,
                {"skill": skill, "category": "团队buff", "source": current_job},
            )

        base_panel = max(0.0, as_number(getattr(self, "_skill_burst_base_summary", {}).get("effective_panel")))
        actions: list[dict[str, Any]] = []
        for order, (skill_key, info) in enumerate(buff_rows.items(), start=2000):
            skill = norm(info.get("skill"))
            category = norm(info.get("category"))
            source = norm(info.get("source"))
            activation_rule = self.store.buff_activation_rule(source, skill, category)
            activation_mode = norm(activation_rule.get("mode"))
            if category == "个人buff" and activation_mode == "self_skill":
                learned_level = self._skill_build_level_for_burst_sim(skill)
                if learned_level is not None and learned_level <= 0:
                    continue
                attr_level = selected_levels.get(skill_key)
                available_attr_levels = self.store.skill_levels(skill)
                if not attr_level and available_attr_levels:
                    attr_level = max(available_attr_levels, key=as_number)
                attrs = self._team_effect_attrs(
                    skill,
                    attr_level or "1",
                    self.second_job.currentText(),
                    include_personal_buffs=True,
                )
                skill_scope = [
                    self._skill_burst_skill_key(value)
                    for value in activation_rule.get("skill_scope", [])
                    if self._skill_burst_skill_key(value)
                ]
                multiplier = self._skill_burst_panel_multiplier_for_attrs(attrs)
                if attrs and skill_scope and multiplier > 1.0 + 1e-6:
                    actions.append({
                        "kind": "self_skill_buff",
                        "order": order,
                        "skill": skill,
                        "type": "技能过程个人buff",
                        "duration": max(0.0, as_number(activation_rule.get("duration"))),
                        "attrs": {},
                        "display_attrs": attrs,
                        "resist_attrs": {},
                        "periodic_buff": True,
                        "buff_marker": "",
                        "active_multiplier": multiplier,
                        "inactive_multiplier": multiplier,
                        "skill_scope": skill_scope,
                        "effective_score": 0.0,
                        "timeline_hidden": True,
                    })
                continue
            if category == "个人buff" and activation_mode in {
                "permanent", "triggered", "mode", "self_skill"
            }:
                continue
            if skill_key in {
                self._skill_burst_skill_key(value)
                for value in getattr(self, "_team_practical_duplicate_build_buffs", set())
            }:
                continue
            if norm(self.second_job.currentText()) == "机械大师" and skill_key == "机械时刻":
                # 机械时刻由进入机械模式触发，不是独立施法技能。
                continue
            learned_level = self._skill_build_level_for_burst_sim(skill)
            if learned_level is None or learned_level <= 0:
                continue
            selected_level = selected_levels.get(skill_key)
            mastery_bonus = max(0, self._skill_mastery_bonus_for_skill(skill))
            base_timing_level = max(
                1,
                int(as_number(selected_level, learned_level)),
            )
            timing_level = str(base_timing_level + mastery_bonus)
            attr_level = timing_level
            available_attr_levels = [
                level
                for level in self.store.skill_levels(skill)
                if norm(level) and norm(level) != "无"
            ]
            if available_attr_levels and attr_level not in available_attr_levels:
                target_level = as_number(attr_level)
                eligible_levels = [
                    level for level in available_attr_levels
                    if as_number(level) <= target_level + 1e-9
                ]
                attr_level = max(
                    eligible_levels or available_attr_levels,
                    key=as_number,
                )
            timing = self._team_effect_timing(
                f"队员1 {norm(self.second_job.currentText())}",
                skill,
                timing_level,
            )
            cooldown = max(0.0, as_number(timing.get("cooldown")))
            attrs = self._team_effect_attrs(
                skill,
                attr_level,
                self.second_job.currentText(),
                include_personal_buffs=True,
            )
            timeline_attrs = {
                norm(attr) for attr in activation_rule.get("timeline_attrs", []) if norm(attr)
            }
            if timeline_attrs:
                attrs = {attr: value for attr, value in attrs.items() if norm(attr) in timeline_attrs}
            if not attrs or base_panel <= 0:
                continue
            duration = self._skill_burst_periodic_buff_duration(attrs, timing)
            force_timeline_cast = bool(activation_rule.get("force_timeline_cast"))
            if (
                cooldown <= 0
                or duration <= 0
                or (duration >= cooldown - 1e-9 and not force_timeline_cast)
            ):
                continue

            is_selected = selected_level is not None
            active_multiplier = 1.0
            inactive_multiplier = 1.0
            try:
                adjusted_build = dict(base_build) if _EXACT_ROUND2 else copy.deepcopy(base_build)
                if is_selected:
                    adjusted_build["buffs"] = [
                        item
                        for item in adjusted_build.get("buffs", [])
                        if self._skill_burst_skill_key(self._team_effect_skill_name(item.get("skill"))) != skill_key
                    ]
                    _rows, adjusted_summary = self.calculator.calculate(adjusted_build)
                    inactive_multiplier = max(0.0, as_number(adjusted_summary.get("effective_panel")) / base_panel)
                else:
                    extra_attrs = dict(adjusted_build.get("extra_buff_attrs", {}))
                    for attr, value in attrs.items():
                        extra_attrs[norm(attr)] = as_number(extra_attrs.get(norm(attr))) + as_number(value)
                    adjusted_build["extra_buff_attrs"] = extra_attrs
                    _rows, adjusted_summary = self.calculator.calculate(adjusted_build)
                    active_multiplier = max(0.0, as_number(adjusted_summary.get("effective_panel")) / base_panel)
            except Exception:
                continue
            timing_attrs = {
                norm(attr): as_number(value)
                for attr, value in attrs.items()
                if norm(attr) in {"动作速度%", "攻速%", "冷却缩减%"}
                and abs(as_number(value)) > 1e-9
            }
            if active_multiplier <= inactive_multiplier + 1e-6 and not timing_attrs:
                continue
            marker = f"__爆发buff__{skill_key}"
            acceleration_resistance = is_acceleration_resistance_skill(skill)
            resistance_cooldown = cooldown * (
                1.0
                - min(
                    100.0,
                    max(0.0, self._skill_glyph_effect_value(skill, "冷却缩减")),
                )
                / 100.0
            )
            actions.append({
                "kind": "support",
                "order": order,
                "skill": skill,
                "type": norm(info.get("category")) or "buff",
                "base_cooldown": cooldown,
                "duration": duration,
                "attrs": {marker: 1.0, **timing_attrs},
                "display_attrs": attrs,
                "team_buff": category == "团队buff",
                "team_objective_effect": category == "团队buff",
                "team_objective_attrs": dict(attrs) if category == "团队buff" else {},
                "display_level": attr_level,
                "element_scope": self._skill_burst_periodic_buff_element_scope(attrs),
                "resist_attrs": {},
                "periodic_buff": True,
                "buff_marker": marker,
                "active_multiplier": active_multiplier,
                "inactive_multiplier": inactive_multiplier,
                "effective_score": 0.0,
                "mandatory_uptime": bool(activation_rule.get("mandatory_uptime")),
                "continuous_uptime": bool(activation_rule.get("continuous_uptime")),
                "special_cdr": acceleration_resistance,
                "resistance_cooldown": (
                    resistance_cooldown if acceleration_resistance else 0.0
                ),
            })
        return actions

    @staticmethod
    def _skill_burst_periodic_buff_element_scope(attrs: dict[str, Any]) -> list[str]:
        element_attrs = {
            "火攻%": "火",
            "水攻%": "水",
            "光攻%": "光",
            "暗攻%": "暗",
        }
        scopes = {
            element
            for attr, element in element_attrs.items()
            if abs(as_number(attrs.get(attr))) > 1e-9
        }
        if not scopes:
            return []
        element_only_attrs = set(element_attrs) | {
            "火抗%", "水抗%", "光抗%", "暗抗%",
            "移速%", "动作速度%", "攻速%",
        }
        meaningful_attrs = {
            norm(attr)
            for attr, value in attrs.items()
            if abs(as_number(value)) > 1e-9
        }
        return sorted(scopes) if meaningful_attrs <= element_only_attrs else []

    @classmethod
    def _skill_burst_periodic_buff_duration(
        cls,
        attrs: dict[str, Any],
        timing: dict[str, Any],
    ) -> float:
        desc = norm(timing.get("desc"))
        if not desc:
            return max(0.0, as_number(timing.get("duration")))
        aliases = {
            "力量%": ("力量",),
            "敏捷%": ("敏捷",),
            "智力%": ("智力",),
            "体质%": ("体质",),
            "物攻%": ("物理攻击力", "物理攻击"),
            "魔攻%": ("魔法攻击力", "魔法攻击"),
            "火攻%": ("火属性攻击", "火攻击"),
            "水攻%": ("水属性攻击", "水攻击"),
            "光攻%": ("光属性攻击", "光攻击"),
            "暗攻%": ("暗属性攻击", "暗攻击"),
            "致命%": ("致命一击", "暴击"),
            "暴击%": ("致命一击", "暴击"),
            "爆伤%": ("致命伤害", "暴击伤害"),
            "最终%": ("最终伤害",),
            "动作速度%": ("动作速度",),
            "攻速%": ("攻击速度",),
            "冷却缩减%": ("冷却时间", "冷却缩减", "冷却减少"),
        }
        attr_terms: set[str] = set()
        for attr, value in attrs.items():
            if abs(as_number(value)) <= 1e-9:
                continue
            key = norm(attr)
            attr_terms.update(aliases.get(key, (key.rstrip("%"),)))
        durations: list[float] = []
        for sentence in re.split(r"[。；\n|]", desc):
            if attr_terms and not any(term and term in sentence for term in attr_terms):
                continue
            duration = cls._duration_from_text(sentence)
            if duration > 0:
                durations.append(duration)
        if durations:
            return max(durations)
        return max(0.0, as_number(timing.get("duration")))

    @staticmethod
    def _skill_burst_action_has_freeze(action: dict[str, Any]) -> bool:
        attrs = action.get("attrs", {})
        return any(key in BuildEditor._team_freeze_attr_keys() and abs(as_number(value)) > 1e-9 for key, value in attrs.items())

    @classmethod
    def _skill_burst_freeze_layers(cls, action: dict[str, Any]) -> float:
        if not cls._skill_burst_action_has_freeze(action):
            return 0.0
        return min(2.0, max(1.0, as_number(action.get("attrs", {}).get("冰冻层"), 1.0)))

    @classmethod
    def _skill_burst_action_starts_on_cast(cls, action: dict[str, Any]) -> bool:
        if action.get("periodic_buff"):
            return False
        action_type = norm(action.get("type")).lower()
        if any(
            marker in action_type
            for marker in ("debuff", "\u51cf\u6297", "\u589e\u4f24", "\u53e0\u51b0", "\u51cf\u9632", "\u51cf\u81f4\u547d")
        ):
            return True
        attrs = action.get("attrs", {})
        debuff_keys = (
            cls._team_resist_attr_keys()
            | cls._team_amp_attr_keys()
            | cls._team_freeze_attr_keys()
            | {"减防御%", "减致命抵抗%"}
        )
        return any(norm(key) in debuff_keys and abs(as_number(value)) > 1e-9 for key, value in attrs.items())

    def _skill_burst_attrs_during_action(
        self,
        active_attrs: dict[str, Any] | None,
        action: dict[str, Any] | None,
    ) -> dict[str, Any]:
        identity_cache = getattr(
            self,
            "_skill_burst_action_attrs_identity_cache",
            None,
        )
        if not isinstance(identity_cache, dict):
            identity_cache = {}
            self._skill_burst_action_attrs_identity_cache = identity_cache
        identity_key = (id(active_attrs), id(action))
        identity_cached = identity_cache.get(identity_key)
        if (
            isinstance(identity_cached, tuple)
            and len(identity_cached) == 3
            and identity_cached[0] is active_attrs
            and identity_cached[1] is action
        ):
            return dict(identity_cached[2])
        base_attrs = dict(active_attrs or {})
        if not action or not self._skill_burst_action_starts_on_cast(action):
            result = base_attrs
            if len(identity_cache) >= 16384:
                identity_cache.pop(next(iter(identity_cache)))
            identity_cache[identity_key] = (active_attrs, action, dict(result))
            return result
        merge_cache = None
        merge_key = None
        if getattr(self, "_skill_burst_fast_mechanism_policy", False):
            merge_cache = getattr(self, "_skill_burst_fast_action_attrs_cache", None)
            if not isinstance(merge_cache, dict):
                merge_cache = {}
                self._skill_burst_fast_action_attrs_cache = merge_cache
            merge_key = (
                tuple(sorted((norm(key), repr(value)) for key, value in base_attrs.items())),
                self._skill_burst_skill_key(action.get("skill")),
                tuple(
                    sorted(
                        (norm(key), repr(value))
                        for key, value in action.get("attrs", {}).items()
                    )
                ),
            )
            cached = merge_cache.get(merge_key)
            if isinstance(cached, dict):
                result = dict(cached)
                if len(identity_cache) >= 16384:
                    identity_cache.pop(next(iter(identity_cache)))
                identity_cache[identity_key] = (
                    active_attrs,
                    action,
                    dict(result),
                )
                return result
        visible_base_attrs = {
            key: value
            for key, value in base_attrs.items()
            if not norm(key).startswith("__") and norm(key) != "冰冻层"
        }
        effects = [
            {"row": -1, "skill": "已生效效果", "attrs": visible_base_attrs, "effective_attrs": visible_base_attrs},
            {
                "_skill_burst_runtime_effect": True,
                "row": action.get("order", 0),
                "skill": action.get("skill", ""),
                "attrs": action.get("attrs", {}),
                "effective_attrs": action.get("attrs", {}),
            },
        ]
        merged = self._skill_burst_cached_merged_team_attrs(effects)
        for key, value in base_attrs.items():
            if norm(key).startswith("__"):
                merged[norm(key)] = max(
                    as_number(merged.get(norm(key))), as_number(value)
                )
        for raw_key, raw_value in action.get("attrs", {}).items():
            key = canonical_defense_reduction_key(raw_key)
            if not key:
                continue
            value = max(0.0, as_number(raw_value))
            rule = effect_rule(
                EffectInput(
                    key=key,
                    value=value,
                    skill_name=norm(action.get("skill")),
                    stack_group=norm(action.get("stack_group")),
                    stackable=bool(action.get("stackable")),
                )
            )
            marker = effect_lane_marker(rule)
            previous = max(0.0, as_number(base_attrs.get(marker)))
            merged[key] = min(
                DEFENSE_REDUCTION_CAP_PERCENT,
                max(0.0, as_number(base_attrs.get(key)))
                + max(0.0, value - previous),
            )
            merged[marker] = max(previous, value)
        active_layers = min(2.0, max(0.0, display_value(base_attrs, "冰冻层")))
        if self._skill_burst_action_has_freeze(action):
            source = self._skill_burst_freeze_source(action)
            source_marker = f"__冰冻来源__{source}"
            if display_value(base_attrs, source_marker) <= 0:
                active_layers = min(2.0, active_layers + self._skill_burst_freeze_layers(action))
            merged[source_marker] = self._skill_burst_freeze_layers(action)
        if active_layers > 0:
            merged["冰冻层"] = active_layers
        if merge_cache is not None and merge_key is not None:
            merge_cache[merge_key] = dict(merged)
            # A 10--15 minute score axis has far more than 256 exact active
            # states. The old bound continuously evicted states before the
            # next cooldown cycle revisited them, turning this into a cache in
            # name only. Values are compact attr dicts and eviction does not
            # affect semantics, so retain a complete long-axis working set.
            while len(merge_cache) > 8192:
                merge_cache.pop(next(iter(merge_cache)))
        if len(identity_cache) >= 16384:
            identity_cache.pop(next(iter(identity_cache)))
        identity_cache[identity_key] = (active_attrs, action, dict(merged))
        return merged

    def _skill_burst_attrs_for_cast(
        self,
        active_attrs: dict[str, Any] | None,
        skill: Any,
    ) -> dict[str, Any]:
        attrs = dict(active_attrs or {})
        if (
            "打坐曜" in norm(getattr(self, "_skill_burst_current_provider_override", ""))
            and self._skill_burst_skill_key(skill) != "查克拉治愈术"
        ):
            attrs.pop("__爆发buff__查克拉治愈术", None)
        return attrs

    @staticmethod
    def _skill_burst_action_has_non_resist_support(action: dict[str, Any]) -> bool:
        attrs = action.get("attrs", {})
        resist_keys = BuildEditor._team_resist_attr_keys()
        low_priority_keys = {"减防御%", "减致命抵抗%"}
        return any(
            norm(key) not in resist_keys
            and norm(key) not in low_priority_keys
            and abs(as_number(value)) > 1e-9
            for key, value in attrs.items()
        )

    @staticmethod
    def _skill_burst_action_is_low_priority_support(action: dict[str, Any]) -> bool:
        attrs = action.get("attrs", {})
        has_low_priority = any(
            key in {"减防御%", "减致命抵抗%"} and abs(as_number(value)) > 1e-9
            for key, value in attrs.items()
        )
        if not has_low_priority:
            return False
        has_major = bool(action.get("resist_attrs"))
        has_major = has_major or any(
            key in BuildEditor._team_amp_attr_keys() and abs(as_number(value)) > 1e-9
            for key, value in attrs.items()
        )
        has_major = has_major or any(
            key in BuildEditor._team_freeze_attr_keys() and abs(as_number(value)) > 1e-9
            for key, value in attrs.items()
        )
        return not has_major

    def _skill_burst_has_ready_combo_followup(
        self,
        previous_skill: str,
        candidates: list[dict[str, Any]],
        damage_available_at: dict[str, float],
        current: float,
    ) -> bool:
        followups = self._skill_burst_forced_combo_followups(previous_skill)
        if not followups:
            return False
        for metric in candidates:
            skill_key = self._skill_burst_skill_key(metric.get("skill"))
            if skill_key not in followups:
                continue
            if not self._skill_burst_predecessor_satisfied(previous_skill, metric.get("skill", "")):
                continue
            if damage_available_at.get(skill_key, 0.0) > current + 1e-6:
                continue
            if self._skill_burst_scheduled_cast_time(metric.get("skill", ""), current, previous_skill) > 0:
                return True
        return False

    @classmethod
    def _skill_burst_action_blocked_by_active_resist(
        cls,
        action: dict[str, Any],
        actions: list[dict[str, Any]],
        expires_at: dict[int, float],
        current: float,
    ) -> bool:
        # The theory axis models a resistance effect after a damaging hit,
        # regardless of whether a different live client:36 application might
        # have occupied the admission lane. Keep the factual packet overlay
        # separate; support scheduling must not discard this cast upfront.
        return False

    def _skill_burst_support_cooldown(
        self,
        action: dict[str, Any],
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        base = max(0.0, as_number(action.get("base_cooldown")))
        if base <= 0:
            return 0.0
        if action.get("special_cdr"):
            # Spirit Boost and Time Acceleration use the team setting's own
            # cooldown (including their glyph), but must not accelerate their
            # own recovery or one another.
            return max(
                0.0,
                as_number(action.get("resistance_cooldown"), base),
            )
        return max(
            0.0,
            self._skill_burst_cooldown_ready_at(action.get("skill", ""), base, seconds, active_attrs)
            - max(0.0, as_number(seconds)),
        )

    def _skill_burst_support_cast_time(
        self,
        action: dict[str, Any],
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        current = as_number(seconds)
        cache_key = (id(action), id(active_attrs), current)
        cache = getattr(self, "_skill_burst_support_cast_identity_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_support_cast_identity_cache = cache
        cached = cache.get(cache_key)
        if (
            isinstance(cached, tuple)
            and len(cached) == 3
            and cached[0] is action
            and cached[1] is active_attrs
        ):
            return cached[2]
        cast_skill = norm(action.get("cast_skill")) or norm(action.get("skill"))
        cast_time = self._skill_burst_sim_cast_time(
            cast_skill,
            current,
            active_attrs,
        )
        result = cast_time if cast_time > 0 else 0.6
        if len(cache) >= 16384:
            cache.pop(next(iter(cache)))
        cache[cache_key] = (action, active_attrs, result)
        return result

    def _skill_burst_support_dpa(
        self,
        action: dict[str, Any],
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
        window: float | None = None,
    ) -> float:
        cast_time = self._skill_burst_support_cast_time(action, seconds, active_attrs)
        if cast_time <= 0:
            return 0.0
        scoring_attrs = self._skill_burst_attrs_during_action(active_attrs, action)
        metric = action.get("metric")
        score = (
            self._skill_burst_window_metric_score(metric, scoring_attrs, seconds, window, cast_time)
            if isinstance(metric, dict)
            else max(0.0, as_number(action.get("effective_score")))
        )
        return score / cast_time

    def _skill_burst_team_objective_active_effects(
        self,
        seconds: float,
    ) -> list[dict[str, Any]]:
        windows = getattr(self, "_skill_burst_team_objective_effect_windows", [])
        if not isinstance(windows, list) or not windows:
            return []
        clear_times = tuple(self._skill_burst_player_buff_clear_times())
        source_key = (id(windows), len(windows), clear_times)
        cached = getattr(self, "_skill_burst_team_objective_window_index", None)
        if not isinstance(cached, dict) or cached.get("source_key") != source_key:
            buckets: dict[int, list[tuple[float, float, dict[str, Any]]]] = defaultdict(list)
            boundaries: set[float] = set(clear_times)
            for row in windows:
                if not isinstance(row, dict):
                    continue
                start = max(0.0, as_number(row.get("start")))
                end = max(start, as_number(row.get("end")))
                effect = row.get("team_effect", {})
                if end <= start + 1e-9 or not isinstance(effect, dict):
                    continue
                entry = (start, end, effect)
                boundaries.update((start, end))
                for bucket in range(
                    int(start // 5.0),
                    int(max(start, end - 1e-9) // 5.0) + 1,
                ):
                    buckets[bucket].append(entry)
            cached = {
                "source_key": source_key,
                "buckets": {
                    key: tuple(value) for key, value in buckets.items()
                },
                "boundaries": tuple(sorted(boundaries)),
                # Candidate schedules add their own boundaries, so many
                # different midpoints still fall inside the same immutable
                # external-effect interval. Resolve that interval once.
                "active_by_interval": {},
            }
            self._skill_burst_team_objective_window_index = cached

        current = max(0.0, as_number(seconds))
        boundaries = cached["boundaries"]
        interval_index = bisect_right(boundaries, current) - 1
        if (
            interval_index < 0
            or interval_index >= len(boundaries) - 1
        ):
            return []
        active_by_interval = cached["active_by_interval"]
        interval_active = active_by_interval.get(interval_index)
        if interval_active is not None:
            return list(interval_active)

        clear_epoch = max(
            (
                as_number(value)
                for value in clear_times
                if as_number(value) <= current + 1e-6
            ),
            default=-1.0,
        )
        transformed_cache = getattr(
            self,
            "_skill_burst_team_objective_transformed_effect_cache",
            None,
        )
        if not isinstance(transformed_cache, dict):
            transformed_cache = {}
            self._skill_burst_team_objective_transformed_effect_cache = (
                transformed_cache
            )
        active: list[dict[str, Any]] = []
        for start, end, effect in cached["buckets"].get(int(current // 5.0), ()):
            if not (start <= current + 1e-6 < end - 1e-6):
                continue
            transform_key = (id(effect), start, clear_epoch)
            transformed = transformed_cache.get(transform_key, False)
            if transformed is False:
                attrs = self._skill_burst_attrs_after_player_buff_clear(
                    effect,
                    effect.get("attrs", {}),
                    start,
                    current,
                )
                transformed = (
                    {
                        **effect,
                        "attrs": attrs,
                        "effective_attrs": attrs,
                        "cooldown": 0.0,
                        "duration": 0.0,
                        "coverage": 1.0,
                        "applied_at": start,
                        # This row is immutable after construction and is
                        # reused by every interval/objective lookup.  Let the
                        # exact effect-signature cache retain its normalized
                        # fields instead of rebuilding them thousands of
                        # times during formal team scoring.
                        "_skill_burst_runtime_effect": True,
                    }
                    if attrs
                    else None
                )
                if len(transformed_cache) >= 16384:
                    transformed_cache.pop(next(iter(transformed_cache)))
                transformed_cache[transform_key] = transformed
            if not isinstance(transformed, dict):
                continue
            active.append(transformed)
        active_by_interval[interval_index] = tuple(active)
        return active

    @staticmethod
    def _skill_burst_team_objective_attr_total(
        attrs: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float:
        return sum(as_number(attrs.get(key)) for key in keys)

    def _skill_burst_team_objective_member_multiplier(
        self,
        attrs: dict[str, Any],
        member: dict[str, Any],
        profile: dict[str, Any],
        *,
        attrs_signature: tuple[tuple[str, float], ...] | None = None,
    ) -> float:
        main_stat = norm(member.get("main_stat"))
        # Support-action search asks for the same before/after party state
        # thousands of times.  Resolving stacking aliases, attack channels and
        # defense channels for every candidate used to dominate one recipient
        # axis and produced the familiar "one worker busy, the rest idle" tail.
        # Cache the pure multiplier by every value this function reads.  The
        # key deliberately keeps exact numeric values (no quantisation), so the
        # optimization cannot change the chosen action or the final score.
        if attrs_signature is None:
            attrs_signature = tuple(sorted(
                (str(key), as_number(value))
                for key, value in attrs.items()
            ))
        cache_key = (
            attrs_signature,
            main_stat,
            as_number(member.get("physical_share")),
            as_number(member.get("magic_share")),
            norm(member.get("element")),
            as_number(profile.get("monster_defense")),
            as_number(profile.get("base_attack"), 30000.0),
            as_number(profile.get("base_element_attack"), 42.0),
            as_number(profile.get("element_resist")),
            as_number(
                profile.get("freeze_coefficient"),
                DEFAULT_FREEZE_COEFFICIENT,
            ),
            as_number(profile.get("crit_resist")),
        )
        multiplier_cache = getattr(
            self,
            "_skill_burst_team_objective_member_multiplier_cache",
            None,
        )
        if not isinstance(multiplier_cache, dict):
            multiplier_cache = {}
            self._skill_burst_team_objective_member_multiplier_cache = (
                multiplier_cache
            )
        cached_multiplier = multiplier_cache.get(cache_key)
        if cached_multiplier is not None:
            return cached_multiplier

        stat_multiplier = 1.0 + max(0.0, as_number(attrs.get(f"{main_stat}%"))) / 100

        monster_defense = max(0.0, as_number(profile.get("monster_defense")))
        base_attack = max(1.0, as_number(profile.get("base_attack"), 30000.0))

        # Resolve the three stacked channel families once per state.  The old
        # nested helper resolved every family once for physical and once again
        # for magical damage, even though both values are returned together.
        channel_cache = getattr(
            self,
            "_skill_burst_team_objective_channel_state_cache",
            None,
        )
        if not isinstance(channel_cache, dict):
            channel_cache = {}
            self._skill_burst_team_objective_channel_state_cache = (
                channel_cache
            )
        channel_state = channel_cache.get(attrs_signature)
        if channel_state is None:
            attack_increases = attack_increase_channels(attrs)
            amplifications = amplification_channels(attrs)
            defense_reductions = defense_reduction_channels(attrs)
            fixed_attacks = (
                fixed_attack_for_channel(attrs, "物攻"),
                fixed_attack_for_channel(attrs, "魔攻"),
            )
            amplified_flats = (
                skill_percent_amplified_flat_attack(attrs, "物攻"),
                skill_percent_amplified_flat_attack(attrs, "魔攻"),
            )
            channel_state = (
                attack_increases,
                amplifications,
                defense_reductions,
                fixed_attacks,
                amplified_flats,
            )
            channel_cache[attrs_signature] = channel_state
            while len(channel_cache) > 32768:
                channel_cache.pop(next(iter(channel_cache)))
        (
            attack_increases,
            amplifications,
            defense_reductions,
            fixed_attacks,
            amplified_flats,
        ) = channel_state

        def channel_multiplier(attack: str) -> float:
            channel_index = 0 if attack == "物攻" else 1
            attack_percent = attack_increases[channel_index]
            flat_attack = fixed_attacks[channel_index]
            amplified_flat = amplified_flats[channel_index]
            if flat_attack >= 0:
                amplified_flat = min(max(0.0, amplified_flat), flat_attack)
            else:
                amplified_flat = max(min(0.0, amplified_flat), flat_attack)
            post_percent_flat = flat_attack - amplified_flat
            attack_multiplier = max(
                0.0,
                (
                    (base_attack + amplified_flat) * (1.0 + attack_percent / 100)
                    + post_percent_flat
                )
                / base_attack,
            )
            amp_percent = amplifications[channel_index]
            defense_down = defense_reductions[channel_index]
            defense_multiplier = 1.0
            if monster_defense > 0:
                reduced_defense = monster_defense * (
                    1.0 - min(DEFENSE_REDUCTION_CAP_PERCENT, max(0.0, defense_down)) / 100
                )
                defense_multiplier = max(
                    0.0,
                    1.0 - BuildCalculator.defense_percent(reduced_defense) / 100,
                )
            return (
                attack_multiplier
                * stat_multiplier
                * (1.0 + min(100.0, max(0.0, amp_percent)) / 100)
                * defense_multiplier
            )

        physical_share = max(0.0, as_number(member.get("physical_share")))
        magic_share = max(0.0, as_number(member.get("magic_share")))
        channel_total = physical_share + magic_share
        if channel_total <= 1e-9:
            physical_share, magic_share, channel_total = 1.0, 0.0, 1.0
        channel_multiplier_value = (
            physical_share * channel_multiplier("物攻")
            + magic_share * channel_multiplier("魔攻")
        ) / channel_total

        element_multiplier = 1.0
        element = norm(member.get("element"))
        if element in {"火", "水", "光", "暗"}:
            base_element_attack = as_number(profile.get("base_element_attack"), 42.0)
            attack_bonus = as_number(attrs.get(f"{element}攻%"))
            base_resist = as_number(profile.get("element_resist"))
            resist_down = max(
                0.0,
                as_number(attrs.get(f"减{element}抗%"))
                + as_number(attrs.get("减全抗%")),
            )
            element_multiplier = max(
                0.0,
                (1.0 + (base_element_attack + attack_bonus) / 100)
                * (1.0 - (base_resist - resist_down) / 100),
            )

        freeze_layers = min(2.0, max(0.0, as_number(attrs.get("冰冻层"))))
        freeze_multiplier = 1.0 + freeze_layers * max(
            0.0,
            as_number(profile.get("freeze_coefficient"), DEFAULT_FREEZE_COEFFICIENT),
        ) / 2

        crit_resist = min(90.0, max(0.0, as_number(profile.get("crit_resist"))))
        crit_down = min(100.0, max(0.0, as_number(attrs.get("减致命抵抗%"))))
        effective_crit_rate = 0.90 * max(
            0.0,
            1.0 - crit_resist * (1.0 - crit_down / 100) / 100,
        )
        crit_multiplier = 1.0 + effective_crit_rate * 2.0

        cdr = min(70.0, max(0.0, as_number(attrs.get("冷却缩减%"))))
        action_speed = max(
            0.0,
            as_number(attrs.get("动作速度%")) + as_number(attrs.get("攻速%")),
        )
        timing_multiplier = (
            1.0
            + cdr / 100 * 0.55
            + min(100.0, action_speed) / 100 * 0.90
        )
        multiplier = max(
            0.0,
            channel_multiplier_value
            * element_multiplier
            * freeze_multiplier
            * crit_multiplier
            * timing_multiplier,
        )
        # Bound per-runtime memory without making eviction affect semantics.
        # Timeline search has strong temporal locality, so clearing only after
        # a generous limit retains practically all useful repeated states.
        multiplier_cache[cache_key] = multiplier
        while len(multiplier_cache) > 32768:
            multiplier_cache.pop(next(iter(multiplier_cache)))
        return multiplier

    def _skill_burst_team_objective_dynamic_hp_attrs(
        self,
        attrs: dict[str, Any],
        seconds: float,
    ) -> dict[str, Any]:
        boss_hp_segments = getattr(self, "_skill_burst_boss_hp_segments", None)
        if boss_hp_segments is None:
            return attrs
        result = dict(attrs)
        hp_percent = boss_hp_percent_at(boss_hp_segments, seconds)
        high_coefficient = max(
            0.0, as_number(result.pop(HIGH_HP_COEFFICIENT_ATTR, 0.0))
        )
        lost_coefficient = max(
            0.0, as_number(result.pop(LOST_HP_COEFFICIENT_ATTR, 0.0))
        )
        dynamic_amp = 0.0
        if high_coefficient > 0:
            dynamic_amp += conditional_team_buff_gain_rate(
                CROW_SKILL, high_coefficient, hp_percent
            ) * 100.0
        if lost_coefficient > 0:
            dynamic_amp += conditional_team_buff_gain_rate(
                DANCE_STAGE_TWO_SKILL, lost_coefficient, hp_percent
            ) * 100.0
        if dynamic_amp > 0:
            result["增伤%"] = as_number(result.get("增伤%")) + dynamic_amp
        return result

    def _skill_burst_team_objective_gain_ratio(
        self,
        action: dict[str, Any],
        seconds: float | None,
        active_attrs: dict[str, Any] | None = None,
    ) -> float:
        profile = self._skill_burst_effective_objective_profile()
        if (
            not isinstance(profile, dict)
            or not action.get("team_objective_effect")
            or not isinstance(profile.get("members"), list)
        ):
            return 0.0
        action_attrs = {
            norm(key): as_number(value)
            for key, value in action.get("team_objective_attrs", {}).items()
            if norm(key) and abs(as_number(value)) > 1e-9
        }
        if not action_attrs:
            return 0.0
        current = max(0.0, as_number(seconds))
        before_effects = self._skill_burst_team_objective_active_effects(current)
        states = getattr(self, "_skill_burst_local_objective_states", None)
        token = int(as_number((active_attrs or {}).get("__local_team_objective_state__")))
        if states is not None and token in states[1]:
            before_effects = [*before_effects, *states[1][token]]

        def effect_signature(effect: dict[str, Any]) -> tuple[Any, ...]:
            cached_signature = effect.get(
                "_skill_burst_team_objective_signature"
            )
            if isinstance(cached_signature, tuple):
                return cached_signature
            skill = norm(effect.get("skill"))
            exclusive_group = self._skill_burst_exclusive_effect_group(
                self._team_effect_skill_name(skill)
            )
            # Absolute refresh time is irrelevant to every merge lane except
            # the ranger last-applied physical-amplification lane.  Attack-buff
            # replacement only checks whether a runtime timestamp exists; all
            # other lanes use ordered value/coverage. Canonicalising that fact
            # lets equivalent recasts share the exact same calculation.
            if exclusive_group == "游侠物理增伤":
                applied_signature: Any = (
                    None
                    if effect.get("applied_at") is None
                    else as_number(effect.get("applied_at"))
                )
            elif exclusive_group == "团队攻击力增益":
                applied_signature = effect.get("applied_at") is not None
            else:
                applied_signature = None
            signature = (
                norm(effect.get("provider")),
                skill,
                repr(effect.get("row")),
                norm(effect.get("level")),
                tuple(sorted(
                    (norm(key), as_number(value))
                    for key, value in effect.get("attrs", {}).items()
                )),
                as_number(effect.get("cooldown")),
                as_number(effect.get("duration")),
                norm(effect.get("stack_group")),
                bool(effect.get("stackable")),
                applied_signature,
                bool(effect.get("_skill_burst_runtime_effect")),
            )
            effect["_skill_burst_team_objective_signature"] = signature
            return signature

        before_signature = tuple(effect_signature(effect) for effect in before_effects)
        members_signature = tuple(
            (
                norm(member.get("main_stat")),
                as_number(member.get("physical_share")),
                as_number(member.get("magic_share")),
                norm(member.get("element")),
                as_number(member.get("weight")),
            )
            for member in profile.get("members", ())
            if isinstance(member, dict)
        )
        boss_hp_segments = getattr(self, "_skill_burst_boss_hp_segments", None)
        hp_conditional_keys = {
            HIGH_HP_COEFFICIENT_ATTR,
            LOST_HP_COEFFICIENT_ATTR,
        }
        has_hp_conditional = any(
            norm(key) in hp_conditional_keys
            for effect in before_effects
            for key in effect.get("attrs", {})
        ) or any(norm(key) in hp_conditional_keys for key in action_attrs)
        hp_signature = (
            boss_hp_percent_at(boss_hp_segments, current)
            if boss_hp_segments is not None and has_hp_conditional
            else None
        )
        gain_cache_key = (
            before_signature,
            norm(action.get("skill")),
            tuple(sorted(action_attrs.items())),
            norm(getattr(self, "_skill_burst_current_provider_override", "")),
            hp_signature,
            members_signature,
            as_number(profile.get("monster_defense")),
            as_number(profile.get("base_attack"), 30000.0),
            as_number(profile.get("base_element_attack"), 42.0),
            as_number(profile.get("element_resist")),
            as_number(
                profile.get("freeze_coefficient"),
                DEFAULT_FREEZE_COEFFICIENT,
            ),
            as_number(profile.get("crit_resist")),
        )
        gain_cache = getattr(
            self,
            "_skill_burst_team_objective_gain_ratio_cache",
            None,
        )
        if not isinstance(gain_cache, dict):
            gain_cache = {}
            self._skill_burst_team_objective_gain_ratio_cache = gain_cache
        if gain_cache_key in gain_cache:
            return gain_cache[gain_cache_key]

        candidate_effect = {
            "provider": norm(getattr(self, "_skill_burst_current_provider_override", "")),
            "skill": action.get("skill", ""),
            "attrs": action_attrs,
            "effective_attrs": action_attrs,
            "cooldown": 0.0,
            "duration": 0.0,
            "coverage": 1.0,
            "applied_at": current,
        }

        def merged_attrs_cached(
            effects: list[dict[str, Any]],
        ) -> dict[str, float]:
            # _merged_team_attrs is pure but relatively expensive: support
            # search revisits the same active window for every candidate.  Its
            # result depends only on the ordered fields below, including the
            # runtime lane marker and applied time used by last-wins groups.
            signature = tuple(effect_signature(effect) for effect in effects)
            cache = getattr(
                self,
                "_skill_burst_team_objective_merged_attrs_cache",
                None,
            )
            if not isinstance(cache, dict):
                cache = {}
                self._skill_burst_team_objective_merged_attrs_cache = cache
            cached = cache.get(signature)
            if cached is None:
                cached = self._merged_team_attrs(effects)
                cache[signature] = cached
                # Ten-to-fifteen minute team axes can exceed 8k exact support
                # states. Clearing the entire dictionary at that boundary made
                # the second half recompute every early state. Keep a bounded
                # FIFO working set instead; eviction cannot alter a value.
                while len(cache) > 32768:
                    cache.pop(next(iter(cache)))
            # Dynamic HP and CDR are added below; never mutate the cached
            # immutable state shared by another candidate evaluation.
            return dict(cached)

        before_attrs = self._skill_burst_team_objective_dynamic_hp_attrs(
            merged_attrs_cached(before_effects), current
        )
        # A recast replaces this provider's same skill, including freeze and
        # independently stackable resistance lanes; it is not a new source.
        after_effects = [
            effect for effect in before_effects
            if not (
                norm(effect.get("provider")) == norm(candidate_effect.get("provider"))
                and self._team_effect_skill_name(effect.get("skill", ""))
                == self._team_effect_skill_name(candidate_effect.get("skill", ""))
            )
        ] + [candidate_effect]
        after_attrs = self._skill_burst_team_objective_dynamic_hp_attrs(
            merged_attrs_cached(after_effects), current
        )
        before_attrs["冷却缩减%"] = self._skill_burst_active_cdr_total(before_effects)
        after_attrs["冷却缩减%"] = self._skill_burst_active_cdr_total(after_effects)

        gain = 0.0
        for member in profile.get("members", ()):
            if not isinstance(member, dict):
                continue
            weight = max(0.0, as_number(member.get("weight")))
            if weight <= 1e-9:
                continue
            before = self._skill_burst_team_objective_member_multiplier(
                before_attrs,
                member,
                profile,
            )
            after = self._skill_burst_team_objective_member_multiplier(
                after_attrs,
                member,
                profile,
            )
            if before > 1e-9 and after > before + 1e-9:
                gain += weight * (after / before - 1.0)
        result = max(0.0, gain)
        gain_cache[gain_cache_key] = result
        while len(gain_cache) > 32768:
            gain_cache.pop(next(iter(gain_cache)))
        return result

    def _skill_burst_effective_objective_profile(self) -> dict[str, Any] | None:
        """Return the explicit party objective or a one-owner fallback.

        A standalone timeline still needs a concrete damage receiver when it
        compares defense/resistance debuffs with self buffs.  Previously the
        absence of seven teammate weights disabled that calculation and sent
        the scheduler back to a fixed profession opener.  Model the owner as a
        weight-1 member instead; explicit score/team profiles remain unchanged
        and continue to contain only the additional teammates.
        """

        explicit = getattr(self, "_skill_burst_team_objective_profile", None)
        if isinstance(explicit, dict) and any(
            isinstance(member, dict)
            and as_number(member.get("weight")) > 1e-9
            for member in explicit.get("members", ())
        ):
            return explicit

        profile_getter = getattr(self, "_job_profile", None)
        job_profile = profile_getter() if callable(profile_getter) else {}
        if not isinstance(job_profile, dict):
            job_profile = {}
        attacks = [
            norm(value)
            for value in job_profile.get("attack_attrs", ())
            if norm(value) in {"物攻", "魔攻"}
        ]
        if not attacks:
            attack = norm(job_profile.get("attack_attr"))
            attacks = [attack if attack in {"物攻", "魔攻"} else "物攻"]
        element_attr = norm(job_profile.get("element_attr"))
        element = element_attr[:-2] if element_attr.endswith("攻%") else ""
        second_job_widget = getattr(self, "second_job", None)
        current_text = getattr(second_job_widget, "currentText", None)
        owner_job = norm(job_profile.get("second_job")) or (
            norm(current_text()) if callable(current_text) else ""
        )
        if not owner_job:
            return None
        return {
            "owner_job": owner_job,
            "members": [{
                "job": owner_job,
                "weight": 1.0,
                "physical_share": attacks.count("物攻") / len(attacks),
                "magic_share": attacks.count("魔攻") / len(attacks),
                "main_stat": norm(job_profile.get("main_stat")),
                "element": element,
            }],
            "monster_defense": 3400.0,
            "element_resist": 20.0,
            "crit_resist": 8122.0,
            "freeze_coefficient": DEFAULT_FREEZE_COEFFICIENT,
            "base_element_attack": 42.0,
        }

    def _skill_burst_joint_timeline_objective(
        self,
        segments: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        window: float,
        *,
        reference_owner_dps: float | None = None,
    ) -> tuple[float, float, float]:
        """Score one complete axis with the same owner+team objective.

        ``members[].weight`` is expressed relative to the owner's output.
        Integrating their multipliers over the generated Buff/Debuff windows
        therefore makes opening-order acceptance use the same units as the
        owner's timeline score.  This prevents a locally attractive order from
        disturbing the rest of the rotation, while also preventing the old
        personal-only guard from undoing a real team gain.
        """

        horizon = max(0.0, as_number(window))
        owner_score = self._skill_burst_local_timeline_score(segments, horizon)
        explicit = getattr(self, "_skill_burst_team_objective_profile", None)
        if (
            horizon <= 1e-9
            or not isinstance(explicit, dict)
            or not any(
                isinstance(member, dict)
                and as_number(member.get("weight")) > 1e-9
                for member in explicit.get("members", ())
            )
        ):
            return owner_score, owner_score, 0.0

        team_skills = {
            self._skill_burst_skill_key(action.get("skill"))
            for action in support_actions
            if action.get("team_objective_effect")
            and self._skill_burst_skill_key(action.get("skill"))
        }
        if not team_skills:
            return owner_score, owner_score, 0.0

        previous_skip = getattr(
            self,
            "_skill_burst_skip_display_effect_windows",
            False,
        )
        self._skill_burst_skip_display_effect_windows = False
        try:
            generated = self._skill_burst_effect_windows(
                segments,
                support_actions,
                horizon,
            )
        finally:
            self._skill_burst_skip_display_effect_windows = previous_skip

        own_windows = [
            row
            for row in generated
            if isinstance(row, dict)
            and self._skill_burst_skill_key(row.get("source")) in team_skills
            and isinstance(row.get("attrs"), dict)
            and as_number(row.get("end")) > as_number(row.get("start")) + 1e-9
        ]
        external_windows = [
            row
            for row in getattr(
                self,
                "_skill_burst_team_objective_effect_windows",
                (),
            )
            if isinstance(row, dict)
        ]
        boundaries = {0.0, horizon}
        for row in (*own_windows, *external_windows):
            boundaries.add(max(0.0, min(horizon, as_number(row.get("start")))))
            boundaries.add(max(0.0, min(horizon, as_number(row.get("end")))))
        for value in getattr(
            self,
            "_skill_burst_player_buff_clear_times_override",
            (),
        ) or ():
            boundaries.add(max(0.0, min(horizon, as_number(value))))
        points = sorted(boundaries)
        own_window_entries: list[
            tuple[float, float, dict[str, Any]]
        ] = []
        for row in own_windows:
            row_start = max(0.0, as_number(row.get("start")))
            row_end = max(row_start, as_number(row.get("end")))
            attrs = {
                norm(key): as_number(value)
                for key, value in row.get("attrs", {}).items()
                if norm(key) and abs(as_number(value)) > 1e-9
            }
            if not attrs:
                continue
            own_window_entries.append((
                row_start,
                row_end,
                {
                    "skill": row.get("source", ""),
                    "attrs": attrs,
                    "effective_attrs": attrs,
                    "cooldown": 0.0,
                    "duration": 0.0,
                    "coverage": 1.0,
                    "applied_at": row_start,
                    "_skill_burst_runtime_effect": True,
                },
            ))
        # ``points`` contains every own-window boundary.  The former loop
        # tested every own window again at every interval midpoint, making one
        # exact joint-objective evaluation O(boundaries * windows).  Opening
        # order search evaluates that objective many times, so long team axes
        # spent most of their time rescanning windows whose active state had
        # not changed.
        #
        # Keep the identical original-window order (important for last-wins
        # effect lanes), but update the active indexes only at their boundary
        # events.  The resulting active-effect list is exactly the one produced
        # by ``row_start <= midpoint < row_end`` and changes no score or search
        # decision.
        own_start_events: dict[float, list[int]] = defaultdict(list)
        own_end_events: dict[float, list[int]] = defaultdict(list)
        for entry_index, (row_start, row_end, _runtime_effect) in enumerate(
            own_window_entries
        ):
            clipped_start = max(0.0, min(horizon, row_start))
            clipped_end = max(0.0, min(horizon, row_end))
            if clipped_end <= clipped_start + 1e-9:
                continue
            own_start_events[clipped_start].append(entry_index)
            own_end_events[clipped_end].append(entry_index)
        active_own_indexes: list[int] = []
        member_rows = tuple(
            member
            for member in explicit.get("members", ())
            if isinstance(member, dict)
        )
        profile_signature = (
            tuple(
                (
                    as_number(member.get("weight")),
                    as_number(member.get("physical_share")),
                    as_number(member.get("magic_share")),
                    norm(member.get("main_stat")),
                    norm(member.get("element")),
                )
                for member in member_rows
            ),
            as_number(explicit.get("monster_defense")),
            as_number(explicit.get("base_attack"), 30000.0),
            as_number(explicit.get("base_element_attack"), 42.0),
            as_number(explicit.get("element_resist")),
            as_number(
                explicit.get("freeze_coefficient"),
                DEFAULT_FREEZE_COEFFICIENT,
            ),
            as_number(explicit.get("crit_resist")),
        )
        weighted_cache = getattr(
            self,
            "_skill_burst_team_objective_weighted_multiplier_cache",
            None,
        )
        if not isinstance(weighted_cache, dict):
            weighted_cache = {}
            self._skill_burst_team_objective_weighted_multiplier_cache = (
                weighted_cache
            )

        weighted_units = 0.0
        for start, end in zip(points, points[1:]):
            if end <= start + 1e-9:
                continue
            # End is exclusive and start is inclusive, matching the former
            # midpoint predicate.  Maintain indexes in their source order so
            # the merged stacking resolver receives the exact same sequence.
            for entry_index in own_end_events.get(start, ()):
                position = bisect_left(active_own_indexes, entry_index)
                if (
                    position < len(active_own_indexes)
                    and active_own_indexes[position] == entry_index
                ):
                    active_own_indexes.pop(position)
            for entry_index in own_start_events.get(start, ()):
                insort(active_own_indexes, entry_index)
            current = (start + end) / 2
            active_effects = self._skill_burst_team_objective_active_effects(
                current
            )
            active_effects.extend(
                own_window_entries[entry_index][2]
                for entry_index in active_own_indexes
            )
            attrs = self._skill_burst_team_objective_dynamic_hp_attrs(
                self._skill_burst_cached_merged_team_attrs(active_effects),
                current,
            )
            attrs["冷却缩减%"] = self._skill_burst_active_cdr_total(
                active_effects
            )
            # Every member sees the same exact merged state.  Build its
            # canonical signature once for the interval rather than sorting
            # the same attribute dictionary again for all eight members.
            attrs_signature = tuple(sorted(
                (str(key), as_number(value))
                for key, value in attrs.items()
            ))
            weighted_key = (attrs_signature, profile_signature)
            cached_weighted = weighted_cache.get(weighted_key)
            if cached_weighted is None:
                multiplier = 0.0
                for member in member_rows:
                    weight = max(0.0, as_number(member.get("weight")))
                    if weight <= 1e-9:
                        continue
                    multiplier += weight * (
                        self._skill_burst_team_objective_member_multiplier(
                            attrs,
                            member,
                            explicit,
                            attrs_signature=attrs_signature,
                        )
                    )
                weighted_cache[weighted_key] = multiplier
                while len(weighted_cache) > 32768:
                    weighted_cache.pop(next(iter(weighted_cache)))
            else:
                multiplier = cached_weighted
            weighted_units += (end - start) * multiplier

        owner_dps = (
            max(0.0, as_number(reference_owner_dps))
            if reference_owner_dps is not None
            else owner_score / horizon
        )
        joint_score = owner_score + owner_dps * weighted_units
        return joint_score, owner_score, weighted_units

    def _skill_burst_support_gain_ratio(
        self,
        action: dict[str, Any],
        active_attrs: dict[str, Any] | None,
        seconds: float | None = None,
    ) -> float:
        """Memoize repeated valuations inside one scheduler decision.

        Scheme 1 intentionally asks for the same support action several times
        while ranking it, comparing setup order and resolving near ties.  The
        action and active-attribute dictionaries are immutable for that entire
        decision.  Retain both objects in the cache entry (rather than trusting
        a recyclable ``id``) so this identity shortcut is exact and cannot leak
        across later states or counterfactual axes.
        """

        active = active_attrs or {}
        current = None if seconds is None else as_number(seconds)
        cache_key = (id(action), id(active), current)
        cache = getattr(self, "_skill_burst_support_gain_identity_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_support_gain_identity_cache = cache
        cached = cache.get(cache_key)
        if (
            isinstance(cached, tuple)
            and len(cached) == 3
            and cached[0] is action
            and cached[1] is active
        ):
            return cached[2]
        result = self._skill_burst_support_gain_ratio_uncached(
            action,
            active,
            seconds,
        )
        if len(cache) >= 16384:
            cache.pop(next(iter(cache)))
        cache[cache_key] = (action, active, result)
        return result

    def _skill_burst_support_gain_ratio_uncached(
        self,
        action: dict[str, Any],
        active_attrs: dict[str, Any] | None,
        seconds: float | None = None,
    ) -> float:
        active = active_attrs or {}
        if action.get("periodic_buff"):
            marker = norm(action.get("buff_marker"))
            if marker and display_value(active, marker) > 0:
                return 0.0
            skill_key = self._skill_burst_skill_key(action.get("skill"))
            if (
                getattr(self, "_skill_burst_boss_hp_segments", None) is not None
                and skill_key in {CROW_SKILL, DANCE_STAGE_TWO_SKILL}
            ):
                coefficient_attr = (
                    HIGH_HP_COEFFICIENT_ATTR
                    if skill_key == CROW_SKILL
                    else LOST_HP_COEFFICIENT_ATTR
                )
                hp_percent = boss_hp_percent_at(
                    self._skill_burst_boss_hp_segments,
                    max(0.0, as_number(seconds)),
                )
                own_gain = conditional_team_buff_gain_rate(
                    skill_key,
                    max(0.0, as_number(action.get("attrs", {}).get(coefficient_attr))),
                    hp_percent,
                )
                return own_gain + self._skill_burst_team_objective_gain_ratio(
                    action, seconds, active
                )
            inactive = max(1e-9, as_number(action.get("inactive_multiplier"), 1.0))
            active_multiplier = max(0.0, as_number(action.get("active_multiplier"), 1.0))
            return max(0.0, active_multiplier / inactive - 1.0) + (
                self._skill_burst_team_objective_gain_ratio(action, seconds, active)
            )
        gain = 0.0
        owner_weights = action.get("owner_resist_damage_weights")
        for attr, value in self._skill_burst_action_resist_values(action).items():
            share = max(0.0, as_number(owner_weights.get(attr))) if isinstance(owner_weights, dict) else 1.0
            gain += share * max(0.0, value - display_value(active, attr)) / 100
        attrs = action.get("attrs", {})
        for attr in self._team_amp_attr_keys():
            gain += max(0.0, as_number(attrs.get(attr)) - display_value(active, attr)) / 100
        if self._skill_burst_action_has_freeze(action):
            active_layers = min(2.0, max(0.0, display_value(active, "冰冻层")))
            same_source_active = display_value(
                active,
                f"__冰冻来源__{self._skill_burst_freeze_source(action)}",
            ) > 0
            added_layers = 0.0 if same_source_active else min(
                2.0 - active_layers,
                max(0.0, as_number(attrs.get("冰冻层"))),
            )
            gain += added_layers * DEFAULT_FREEZE_COEFFICIENT / 2
        # 减防和减致抗属于较弱的外围收益，只用于同档伤害技能之间的取舍。
        # 候选动作必须沿用统一客户端槽；同槽只增加高于当前层的差值。
        current_phys, current_magic = defense_reduction_channels(active)
        has_defense_reduction = any(
            canonical_defense_reduction_key(raw_key)
            and abs(as_number(raw_value)) > 1e-9
            for raw_key, raw_value in attrs.items()
        )
        if has_defense_reduction:
            candidate_attrs = self._skill_burst_attrs_during_action(
                active,
                action,
            )
            candidate_phys, candidate_magic = defense_reduction_channels(
                candidate_attrs
            )
        else:
            candidate_phys, candidate_magic = current_phys, current_magic
        gain += max(
            0.0,
            (candidate_phys - current_phys + candidate_magic - current_magic) / 2,
        ) / 400
        gain += max(0.0, as_number(attrs.get("减致命抵抗%")) - display_value(active, "减致命抵抗%")) / 400
        if self._skill_burst_guardian_phantom_gain(action.get("skill", "")) > 0:
            missing = max(0.0, 2.0 - display_value(active, SKILL_BURST_GUARDIAN_PHANTOM_MARKER))
            gain += 0.18 * min(1.0, missing)
        gain += self._skill_burst_team_objective_gain_ratio(action, seconds, active)
        return max(0.0, gain)

    def _skill_burst_cycle_score_per_second(
        self,
        candidates: list[dict[str, Any]],
        active_attrs: dict[str, Any] | None,
        seconds: float,
    ) -> float:
        total = 0.0
        for metric in candidates:
            cast_time = self._skill_burst_sim_cast_time(metric.get("skill", ""), seconds, active_attrs)
            cooldown = self._skill_burst_effective_cooldown(
                metric.get("skill", ""),
                as_number(metric.get("base_cooldown")),
                seconds,
                active_attrs,
            )
            interval = max(cast_time, cooldown)
            if interval > 0:
                total += self._skill_burst_full_metric_score(metric, active_attrs, seconds) / interval
        return total

    def _skill_burst_support_action_indexes(
        self,
        support_actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the immutable support-action lookup once per simulated axis.

        The scorer calls ``_skill_burst_apply_shared_skill_use`` for every cast.
        Re-scanning the whole support table there is exact but needlessly
        expensive.  Keep original list indexes in every bucket so iteration and
        overwrite order remain byte-for-byte equivalent to the former scans.
        """
        if (
            getattr(self, "_skill_burst_support_action_index_source", None)
            is support_actions
        ):
            cached = getattr(
                self,
                "_skill_burst_support_action_index_value",
                None,
            )
            if isinstance(cached, dict):
                return cached

        by_skill: dict[str, list[int]] = {}
        freeze_by_source: dict[str, list[int]] = {}
        exclusive_by_group: dict[str, list[int]] = {}
        resistance_by_group: dict[str, list[int]] = {}
        by_marker: dict[str, list[int]] = {}
        triggered: list[int] = []
        ultimate: list[int] = []
        dance: list[int] = []
        dance_skills = {"弗里兹科之舞", "幻影之舞"}

        for index, action in enumerate(support_actions):
            skill_key = self._skill_burst_skill_key(action.get("skill"))
            by_skill.setdefault(skill_key, []).append(index)
            if self._skill_burst_action_has_freeze(action):
                freeze_by_source.setdefault(
                    self._skill_burst_freeze_source(action),
                    [],
                ).append(index)
            exclusive_group = self._skill_burst_exclusive_effect_group(skill_key)
            if exclusive_group:
                exclusive_by_group.setdefault(exclusive_group, []).append(index)
            resistance_group = acceleration_resistance_group(skill_key)
            if resistance_group:
                resistance_by_group.setdefault(resistance_group, []).append(index)
            marker = norm(action.get("buff_marker"))
            if marker:
                by_marker.setdefault(marker, []).append(index)
            if action.get("triggered_only"):
                triggered.append(index)
            if self._skill_burst_is_ultimate_name(skill_key):
                ultimate.append(index)
            if skill_key in dance_skills:
                dance.append(index)

        result: dict[str, Any] = {
            "by_skill": {key: tuple(value) for key, value in by_skill.items()},
            "freeze_by_source": {
                key: tuple(value) for key, value in freeze_by_source.items()
            },
            "exclusive_by_group": {
                key: tuple(value) for key, value in exclusive_by_group.items()
            },
            "resistance_by_group": {
                key: tuple(value) for key, value in resistance_by_group.items()
            },
            "by_marker": {key: tuple(value) for key, value in by_marker.items()},
            "triggered": tuple(triggered),
            "ultimate": tuple(ultimate),
            "dance": tuple(dance),
        }
        self._skill_burst_support_action_index_source = support_actions
        self._skill_burst_support_action_index_value = result
        return result

    def _skill_burst_apply_shared_skill_use(
        self,
        skill: str,
        start: float,
        damage_base_cooldown: float,
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        damage_available_at: dict[str, float],
        damage_row_by_skill: dict[str, str],
        damage_base_cooldown_by_skill: dict[str, float],
        active_attrs: dict[str, Any] | None = None,
        *,
        cast_end: float | None = None,
        charged_counts: dict[str, int] | None = None,
        charged_maxes: dict[str, int] | None = None,
        charged_recharge_at: dict[str, Any] | None = None,
    ) -> None:
        skill_key = self._skill_burst_skill_key(skill)
        cooldown_attrs = dict(active_attrs or {})
        support_indexes = self._skill_burst_support_action_indexes(support_actions)
        matching_indexes = support_indexes["by_skill"].get(skill_key, ())
        cast_action = (
            support_actions[matching_indexes[0]] if matching_indexes else None
        )
        resistance_group = acceleration_resistance_group(skill_key)
        if resistance_group:
            local_resistance_cooldown = (
                max(
                    0.0,
                    as_number(
                        cast_action.get("resistance_cooldown"),
                        cast_action.get("base_cooldown"),
                    ),
                )
                if cast_action is not None
                else max(0.0, as_number(damage_base_cooldown))
            )
            self._skill_burst_register_local_acceleration_resistance(
                skill_key,
                start,
                local_resistance_cooldown,
            )
        if cast_action and not cast_action.get("special_cdr"):
            cast_cdr = max(0.0, as_number(cast_action.get("attrs", {}).get("冷却缩减%")))
            cast_duration = max(0.0, as_number(cast_action.get("duration")))
            if cast_cdr > 0 and cast_duration > 0:
                cast_end = start + self._skill_burst_support_cast_time(cast_action, start, active_attrs)
                windows = list(cooldown_attrs.get("__cdr_windows__", []))
                windows.append(
                    (
                        cast_end,
                        cast_end + cast_duration,
                        cast_cdr,
                        self._skill_burst_cdr_source_key(cast_action, skill_key),
                    )
                )
                cooldown_attrs["__cdr_windows__"] = windows
        cooldown = self._skill_burst_effective_cooldown(skill, damage_base_cooldown, start, cooldown_attrs)
        if skill_key in damage_available_at:
            if cast_action and cast_action.get("pinned_cast_schedule"):
                next_starts = [
                    max(0.0, as_number(cast_start))
                    for cast_start, cast_end in cast_action.get("scheduled_casts", ())
                    if as_number(cast_end) > as_number(cast_start) + 1e-9
                    and as_number(cast_start) > start + 1e-6
                ]
                damage_available_at[skill_key] = (
                    min(next_starts) if next_starts else 1.0e12
                )
            else:
                damage_available_at[skill_key] = start + max(0.0, cooldown)
        for index in matching_indexes:
            action = support_actions[index]
            self._skill_burst_snapshot_periodic_action(action, active_attrs)
            support_cooldown = self._skill_burst_support_cooldown(action, start, cooldown_attrs)
            shared_cooldown = max(support_cooldown, cooldown)
            scheduled_casts = action.get("scheduled_casts", ())
            if action.get("pinned_cast_schedule") and isinstance(
                scheduled_casts, (list, tuple)
            ):
                next_starts = [
                    max(0.0, as_number(cast_start))
                    for cast_start, cast_end in scheduled_casts
                    if as_number(cast_end) > 1e-9
                    and as_number(cast_start) > start + 1e-6
                ]
                support_available_at[index] = (
                    min(next_starts) if next_starts else 1.0e12
                )
            else:
                support_available_at[index] = start + max(0.0, shared_cooldown)
            duration = as_number(action.get("duration"))
            if duration > 0:
                cast_ends_at = start + self._skill_burst_support_cast_time(action, start, active_attrs)
                applies_at, effect_ends_at, _refresh_tail = (
                    team_effect_application_window_for_attrs(
                        action.get("skill", ""),
                        action.get("attrs", {}),
                        start,
                        cast_ends_at,
                        duration,
                        starts_on_cast=self._skill_burst_action_starts_on_cast(action),
                        triggered_only=bool(action.get("triggered_only")),
                    )
                )
                if self._skill_burst_action_has_freeze(action):
                    source = self._skill_burst_freeze_source(action)
                    for other_index in support_indexes["freeze_by_source"].get(
                        source, ()
                    ):
                        if other_index == index:
                            continue
                        support_expires_at[other_index] = min(
                            support_expires_at.get(other_index, applies_at),
                            applies_at,
                        )
                exclusive_group = self._skill_burst_exclusive_effect_group(action.get("skill"))
                if exclusive_group:
                    for other_index in support_indexes["exclusive_by_group"].get(
                        exclusive_group, ()
                    ):
                        if other_index == index:
                            continue
                        support_expires_at[other_index] = min(
                            support_expires_at.get(other_index, applies_at),
                            applies_at,
                        )
                support_expires_at[index] = effect_ends_at
            damage_row = damage_row_by_skill.get(skill_key)
            if damage_row is not None:
                if action.get("pinned_cast_schedule"):
                    next_starts = [
                        max(0.0, as_number(cast_start))
                        for cast_start, cast_end in action.get("scheduled_casts", ())
                        if as_number(cast_end) > as_number(cast_start) + 1e-9
                        and as_number(cast_start) > start + 1e-6
                    ]
                    damage_available_at[damage_row] = (
                        min(next_starts) if next_starts else 1.0e12
                    )
                else:
                    damage_available_at[damage_row] = start + max(
                        0.0, shared_cooldown
                    )
        if resistance_group:
            resistance_cooldown = (
                self._skill_burst_support_cooldown(
                    cast_action,
                    start,
                    cooldown_attrs,
                )
                if cast_action is not None
                else cooldown
            )
            resistance_end = start + max(0.0, resistance_cooldown)
            for index in support_indexes["resistance_by_group"].get(
                resistance_group, ()
            ):
                support_available_at[index] = max(
                    support_available_at.get(index, 0.0),
                    resistance_end,
                )
        if skill_key == "机械模式":
            for index in support_indexes["by_skill"].get("机械时刻", ()):
                action = support_actions[index]
                if support_available_at.get(index, 0.0) > start + 1e-6:
                    continue
                cast_ends_at = start + self._skill_burst_support_cast_time(
                    cast_action or {"skill": "机械模式"},
                    start,
                    active_attrs,
                )
                support_expires_at[index] = cast_ends_at + max(0.0, as_number(action.get("duration"), 10.0))
                support_available_at[index] = self._skill_burst_cooldown_ready_at(
                    "机械时刻",
                    max(0.0, as_number(action.get("base_cooldown"), 30.0)),
                    start,
                    active_attrs,
                )
        if (
            norm(self.second_job.currentText()) == "毁灭者"
            and skill_key in SKILL_BURST_DESTROYER_MASTERY_TRIGGERS
        ):
            trigger_end = start + max(
                0.0,
                self._skill_burst_sim_cast_time(skill_key, start, active_attrs),
            )
            for index in support_indexes["by_marker"].get(
                SKILL_BURST_DESTROYER_MASTERY_MARKER, ()
            ):
                action = support_actions[index]
                support_expires_at[index] = trigger_end + max(
                    0.0,
                    as_number(action.get("duration"), 10.0),
                )
        cast_element = norm(self.store.skill_damage_element(self.second_job.currentText(), skill_key))
        is_damage_skill = skill_key in damage_base_cooldown_by_skill
        trigger_end = start + max(0.0, self._skill_burst_sim_cast_time(skill_key, start, active_attrs))
        for index in support_indexes["triggered"]:
            action = support_actions[index]
            trigger_skills = {
                self._skill_burst_skill_key(value)
                for value in action.get("trigger_skills", [])
                if self._skill_burst_skill_key(value)
            }
            trigger_element = norm(action.get("trigger_element"))
            triggered = skill_key in trigger_skills
            triggered = triggered or bool(trigger_element and cast_element == trigger_element)
            triggered = triggered or bool(action.get("trigger_on_damage") and is_damage_skill)
            if not triggered:
                continue
            support_expires_at[index] = trigger_end + max(
                0.0,
                as_number(action.get("duration")),
            )
        if self._skill_burst_is_ultimate_name(skill_key):
            for other_skill, base_cooldown in damage_base_cooldown_by_skill.items():
                if not self._skill_burst_is_ultimate_name(other_skill):
                    continue
                other_cooldown = self._skill_burst_effective_cooldown(
                    other_skill,
                    base_cooldown,
                    start,
                    cooldown_attrs,
                )
                damage_available_at[other_skill] = max(
                    damage_available_at.get(other_skill, 0.0),
                    start + max(0.0, other_cooldown),
                )
            for index in support_indexes["ultimate"]:
                action = support_actions[index]
                other_cooldown = self._skill_burst_support_cooldown(action, start, cooldown_attrs)
                support_available_at[index] = max(
                    support_available_at.get(index, 0.0),
                    start + max(0.0, other_cooldown),
                )
        if skill_key in {"弗里兹科之舞", "幻影之舞"}:
            for index in support_indexes["dance"]:
                action = support_actions[index]
                shared = self._skill_burst_support_cooldown(action, start, cooldown_attrs)
                support_available_at[index] = max(
                    support_available_at.get(index, 0.0),
                    start + shared,
                )
        self._skill_burst_apply_cooldown_rule_source(
            skill_key,
            start,
            damage_available_at,
            damage_base_cooldown_by_skill,
            active_attrs,
            support_actions,
            support_available_at,
        )

        if charged_counts is not None and cast_end is not None:
            self._skill_burst_apply_charge_rule_source(
                skill_key, cast_end, damage_available_at,
                damage_base_cooldown_by_skill, charged_counts,
                charged_maxes or {}, charged_recharge_at, active_attrs,
            )

    def _skill_burst_support_opportunity_dpa(
        self,
        action: dict[str, Any],
        seconds: float,
        active_attrs: dict[str, Any] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        window: float | None = None,
    ) -> float:
        dpa = self._skill_burst_support_dpa(action, seconds, active_attrs, window)
        cast_time = self._skill_burst_support_cast_time(action, seconds, active_attrs)
        application_start, application_end, _refresh_tail = (
            team_effect_application_window_for_attrs(
                action.get("skill", ""),
                action.get("attrs", {}),
                seconds,
                seconds + cast_time,
                max(0.0, as_number(action.get("duration"))),
                starts_on_cast=self._skill_burst_action_starts_on_cast(action),
                triggered_only=bool(action.get("triggered_only")),
            )
        )
        duration = max(0.0, application_end - application_start)
        if window is not None:
            duration = min(duration, max(0.0, window - application_start))
        if cast_time <= 0 or duration <= 0 or not candidates:
            return dpa
        cycle_score = self._skill_burst_cycle_score_per_second(candidates, active_attrs, seconds)
        if action.get("periodic_buff"):
            active_with_buff = dict(active_attrs or {})
            for attr, value in action.get("attrs", {}).items():
                key = norm(attr)
                active_with_buff[key] = display_value(active_with_buff, key) + as_number(value)
            buffed_cycle_score = self._skill_burst_cycle_score_per_second(candidates, active_with_buff, seconds)
            support_value = max(0.0, buffed_cycle_score - cycle_score) * duration
        else:
            support_value = cycle_score * self._skill_burst_support_gain_ratio(
                action,
                active_attrs,
                seconds,
            ) * duration
        return dpa + support_value / cast_time

    def _skill_burst_optional_combo_pair_dpa(
        self,
        metric: dict[str, Any],
        candidates: list[dict[str, Any]],
        support_actions: list[dict[str, Any]],
        support_available_at: dict[int, float],
        support_expires_at: dict[int, float],
        active_attrs: dict[str, Any],
        current: float,
        window: float,
        previous_skill: str,
    ) -> float:
        followups = self._skill_burst_optional_combo_followups(metric.get("skill", ""))
        if not followups:
            return 0.0
        first_cast = self._skill_burst_scheduled_cast_time(
            metric.get("skill", ""), current, previous_skill, active_attrs
        )
        if first_cast <= 0:
            return 0.0
        first_score = self._skill_burst_window_metric_score(
            metric,
            active_attrs,
            current,
            window,
            first_cast,
        )
        followup_start = current + first_cast
        best_pair_dpa = 0.0
        for index, action in enumerate(support_actions):
            if self._skill_burst_skill_key(action.get("skill")) not in followups:
                continue
            if support_available_at.get(index, 0.0) > followup_start + 1e-6:
                continue
            followup_cast = self._skill_burst_support_cast_time(action, followup_start, active_attrs)
            if followup_cast <= 0 or support_expires_at.get(index, 0.0) > followup_start + followup_cast + 1e-6:
                continue
            if self._skill_burst_action_has_freeze(action):
                active_layers = min(2.0, max(0.0, display_value(active_attrs, "冰冻层")))
                if active_layers >= 2.0 - 1e-9:
                    continue
            followup_dpa = self._skill_burst_support_opportunity_dpa(
                action,
                followup_start,
                active_attrs,
                candidates,
                window,
            )
            pair_cast = first_cast + followup_cast
            if pair_cast > 0:
                best_pair_dpa = max(best_pair_dpa, (first_score + followup_dpa * followup_cast) / pair_cast)
        return best_pair_dpa

    def _skill_burst_damage_opportunity_score(
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
    ) -> tuple[float, float]:
        end = min(window, current + cast_time)
        if cast_time <= 0 or end <= current + 1e-6:
            return raw_dpa, 0.0
        penalty = 0.0
        earliest_blocked = 0.0
        planned_followups = self._skill_burst_combo_followups(metric.get("skill", ""))
        for other in candidates:
            if other is metric:
                continue
            key = self._skill_burst_skill_key(other.get("skill"))
            if key in planned_followups:
                # A combo opener is supposed to occupy the time before its
                # left/right-key followup becomes ready; that is not lost output.
                continue
            if not self._skill_burst_predecessor_satisfied(
                metric.get("skill", ""),
                other.get("skill", ""),
            ):
                # A skill that cannot legally follow the current action is not
                # an opportunity the current action can delay.
                continue
            available_at = as_number(damage_available_at.get(key))
            if available_at <= current + 1e-6 or available_at >= end - 1e-6:
                continue
            other_cast = self._skill_burst_sim_cast_time(other.get("skill", ""), available_at, active_attrs)
            if other_cast <= 0:
                continue
            other_score = self._skill_burst_window_metric_score(
                other,
                active_attrs,
                available_at,
                window,
                other_cast,
            )
            other_dpa = other_score / other_cast if other_cast > 0 else 0.0
            if other_dpa <= raw_dpa * 1.18:
                continue
            delayed_seconds = end - available_at
            penalty += (other_dpa - raw_dpa) * delayed_seconds
            earliest_blocked = available_at if earliest_blocked <= 0 else min(earliest_blocked, available_at)
        for index, action in enumerate(support_actions):
            support_cast = self._skill_burst_support_cast_time(action, current, active_attrs)
            ready_at = max(as_number(support_available_at.get(index)), as_number(support_expires_at.get(index)) - support_cast)
            if ready_at <= current + 1e-6 or ready_at >= end - 1e-6:
                continue
            if self._skill_burst_action_has_freeze(action):
                active_freeze_layers = sum(
                    1
                    for active_index, active_action in enumerate(support_actions)
                    if active_index != index
                    and self._skill_burst_action_has_freeze(active_action)
                    and support_expires_at.get(active_index, 0.0) > ready_at + 1e-6
                )
                if active_freeze_layers >= 2:
                    continue
            if self._skill_burst_action_blocked_by_active_resist(action, support_actions, support_expires_at, ready_at + support_cast):
                continue
            support_dpa = self._skill_burst_support_opportunity_dpa(action, ready_at, active_attrs)
            if support_dpa <= raw_dpa * 1.05:
                continue
            delayed_seconds = end - ready_at
            penalty += (support_dpa - raw_dpa) * delayed_seconds
            earliest_blocked = ready_at if earliest_blocked <= 0 else min(earliest_blocked, ready_at)
        cooldown_bonus = self._skill_burst_cooldown_rule_opportunity_score(
            metric.get("skill", ""),
            candidates,
            damage_available_at,
            damage_base_cooldown_by_skill,
            active_attrs,
            current,
        )
        adjusted_score = raw_score + cooldown_bonus - penalty
        if getattr(self, "_skill_burst_complete_default_rotation", False):
            skill_key = self._skill_burst_skill_key(metric.get("skill"))
            base_cooldown = max(
                1.0,
                as_number(damage_base_cooldown_by_skill.get(skill_key), 1.0),
            )
            remaining_window = max(0.0, window - current)
            future_casts = max(0, int((remaining_window - cast_time) // base_cooldown))
            latest_start = window - cast_time - future_casts * base_cooldown
            slack = max(0.0, latest_start - current)
            # A long axis may give a small bonus only when delaying this cast is
            # about to lose an entire future use. The former ready-age ramp could
            # multiply a stale low-DPA skill many times and invert the rotation.
            urgency_span = max(0.25, min(cast_time, 2.0))
            if slack < urgency_span:
                adjusted_score += raw_score * 0.15 * (1.0 - slack / urgency_span)
        return adjusted_score / cast_time, earliest_blocked
