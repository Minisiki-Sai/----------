from __future__ import annotations

import os
from types import MethodType

from concurrent.futures import FIRST_COMPLETED, wait

from ..shared import *
from ..shared import _ExtremeTimelineProxy
from ..background_priority import score_checkpoint, score_work_clock
from ..skill_glyph_policy import approximate_team_objective_gain_ratio
from ..live_dps.score_glyph_objective import DEBUFF_KEYS, captured_debuff_gain_ratio
from ..team_support_rules import (
    effective_team_buff_duration,
    team_effect_refresh_tail_for_attrs,
)
from ..timeline_parallel import (
    ExtremeTimelineTask,
    calculate_extreme_timeline_task,
    calculate_timing_axis_group,
    ephemeral_timing_axis_cache,
    summarize_timing_axis,
    configured_timeline_workers,
    reset_timeline_process_pool,
    timeline_process_pool,
    warm_timeline_process_pool,
)

BuildEditor = None
_DEFAULT_TEAM_OBJECTIVE = object()


def _release_timing_proxy_method_cycles(proxy: Any) -> None:
    """Drop only regenerable self-bound method caches on a finished proxy.

    PureTimelineRuntime lazily stores bound methods on itself. Those create
    self-cycles which otherwise retain an entire discarded axis until cyclic
    GC runs. Shared prepared/panel caches and all semantic state stay intact.
    """
    for name, value in tuple(vars(proxy).items()):
        if isinstance(value, MethodType) and value.__self__ is proxy:
            delattr(proxy, name)


def _order_timing_groups_by_cost(tasks, groups, job_costs):
    """Queue expensive groups first, keeping each recipient's axes together.

    Only execution order changes. Original indexes still place every returned
    result and select every glyph, so measured cost cannot affect a score.
    """
    return sorted(groups, key=lambda indexes: sum(
        job_costs.get(tasks[index].job, 1.0) for index in indexes
    ), reverse=True)


def _timing_axis_group_chunks(groups, chunk_size=1):
    """Split long recipient queues into small, independently scheduled chunks.

    A recipient can own six or more formal timing axes.  Treating all of them
    as one future hides progress for many minutes and makes a single slow job
    the tail of the entire score.  A timing axis is already self-contained and
    workers retain their prepared immutable runtime between futures, so the
    safest scheduling unit is one axis.  This lets every idle process steal the
    next variant, including variants that belong to a formerly "slow" recipient.
    Original indexes continue to restore every result to its exact semantic
    position.
    """

    size = max(1, int(chunk_size))
    return [
        list(indexes[start:start + size])
        for indexes in groups
        for start in range(0, len(indexes), size)
    ]


def _extreme_parallel_stall_timeout_seconds() -> float:
    """Bound one no-result interval without penalising ordinary long axes."""

    try:
        configured = float(
            os.getenv("DRAGON_NEST_TIMELINE_STALL_TIMEOUT_SECONDS", "300") or 300
        )
    except ValueError:
        configured = 300.0
    return max(30.0, configured)


_SCORE_TRANSFORMED_ATTACK_BASE_SKILLS: dict[str, dict[str, str]] = {
    "圣骑士": {"神圣之锤攻击": "神圣之锤"},
    "十字军": {"神圣之锤攻击": "神圣之锤"},
    "烈": {"致残惩罚者攻击": "致残惩罚者"},
}


def _score_metrics_with_transformed_attacks(
    store: Any,
    job: str,
    metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add actual-only transformed attacks to the standard-damage replay.

    These actions are absent from the learned skill table, but their damage
    must be valued by the same timestamped panel/Buff/Debuff environment as an
    ordinary cast.  Clone the underlying skill metric and replace only its
    description multiplier and action duration; the planner's candidate pool
    remains unchanged.
    """

    result = list(metrics)
    by_name = {norm(row.get("skill")): row for row in metrics}
    for attack_name, base_name in _SCORE_TRANSFORMED_ATTACK_BASE_SKILLS.get(
        norm(job), {}
    ).items():
        if attack_name in by_name:
            continue  # The selectable hammer attack already has its own metric.
        base = by_name.get(base_name)
        if not isinstance(base, dict):
            continue
        base_multiplier = max(
            1e-9,
            as_number(store.skill_damage_multiplier(job, base_name)),
        )
        attack_multiplier = max(
            0.0,
            as_number(store.skill_damage_multiplier(job, attack_name)),
        )
        if attack_multiplier <= 0:
            continue
        ratio = attack_multiplier / base_multiplier
        derived = copy.deepcopy(base)
        derived["skill"] = attack_name
        derived["multiplier"] = attack_multiplier
        derived["base_multiplier"] = attack_multiplier
        for field in (
            "raw_score",
            "effective_score",
            "timeline_raw_score",
            "dps",
            "dpa",
        ):
            if field in derived:
                derived[field] = max(0.0, as_number(derived.get(field))) * ratio
        for field in ("channel_scores", "timeline_attack_scores"):
            values = derived.get(field)
            if isinstance(values, dict):
                derived[field] = {
                    key: max(0.0, as_number(value)) * ratio
                    for key, value in values.items()
                }
        timing = store.skill_cast_time(attack_name, [job])
        cast_time = max(
            0.12,
            as_number(
                timing.get("corrected_cast_time"),
                as_number(timing.get("cast_time"), derived.get("cast_time")),
            ),
        )
        derived["cast_time"] = cast_time
        derived["base_cooldown"] = 0.0
        derived["effective_cooldown"] = 0.0
        derived["dpa"] = (
            max(0.0, as_number(derived.get("effective_score"))) / cast_time
        )
        result.append(derived)
    return result


# One production policy is shared by every job. Benchmark tools may override
# these values explicitly, but runtime planning never branches by job.
FAST_MECHANISM_UNIFIED_POLICY: dict[str, Any] = {
    "profile": (5.0, 1.0, 1.0, 2.0, 1.0),
    "ambiguity_ratio": 0.0,
    "override_ratio": 1.0,
    "ready_debt_weight": 1.2760915965964057,
    "ready_debt_dpa_floor": 0.32648920774589857,
    "ready_debt_cycle_floor": 0.7241649974548632,
    "support_horizon": 4.3790490412144685,
    "state_support_horizon": 15.485929628444152,
    "support_candidate_limit": 2,
    "displacement_weight": 0.9342762260758972,
    "short_displacement_weight": 0.5186937979333661,
    "pair_planning": True,
    "boundary_urgency_weight": 0.33173315292421024,
    "boundary_urgency_span": 5.2204719546901055,
}


class ExtremeEngineMixin:
    _EXTREME_PARALLEL_RUNTIME_ATTRS = (
        "_skill_burst_disable_new_scheme_1",
        "_skill_burst_disable_team_objective_opening_order",
        "_skill_burst_disable_team_objective_pair_order",
        "_skill_burst_disable_joint_support_order",
        "_skill_burst_force_new_scheme_1",
        "_skill_burst_new_scheme_1_override_ratio",
        "_skill_burst_new_scheme_1_ready_debt_weight",
        "_skill_burst_new_scheme_1_ready_debt_dpa_floor",
        "_skill_burst_new_scheme_1_ready_debt_cycle_floor",
        "_skill_burst_new_scheme_1_ambiguity_ratio",
        "_skill_burst_enable_new_scheme_1_multistart",
        "_skill_burst_enable_new_scheme_1_quality_floor",
        "_skill_burst_disable_hybrid_optimizer",
        "_skill_burst_disable_local_optimizer",
        "_skill_burst_disable_conservative_1_6",
        "_skill_burst_disable_short_personal_burst_scheduler",
        "_skill_burst_force_conservative_1_5",
        "_skill_burst_force_conservative_1_6",
        "_skill_burst_fast_mechanism_use_legacy_diagnostic",
        "_skill_burst_fast_mechanism_profile",
        "_skill_burst_fast_mechanism_ambiguity_ratio",
        "_skill_burst_fast_mechanism_override_ratio",
        "_skill_burst_fast_mechanism_ready_debt_weight",
        "_skill_burst_fast_mechanism_ready_debt_dpa_floor",
        "_skill_burst_fast_mechanism_ready_debt_cycle_floor",
        "_skill_burst_fast_mechanism_support_horizon",
        "_skill_burst_fast_mechanism_state_support_horizon",
        "_skill_burst_fast_mechanism_support_candidate_limit",
        "_skill_burst_fast_mechanism_displacement_weight",
        "_skill_burst_fast_mechanism_short_displacement_weight",
        "_skill_burst_fast_mechanism_pair_planning",
        "_skill_burst_fast_mechanism_boundary_urgency_weight",
        "_skill_burst_fast_mechanism_boundary_urgency_span",
        "_skill_burst_fast_mechanism_refinement",
        "_skill_burst_fast_mechanism_refinement_seconds",
        "_skill_burst_fast_mechanism_refinement_evaluations",
        "_skill_burst_fast_mechanism_cycle_deadline_weight",
        "_skill_burst_fast_mechanism_cycle_deadline_delay",
        "_skill_burst_fast_mechanism_opening_setup",
        "_extreme_clean_base_build",
        "_extreme_clean_base_summary",
        "_extreme_base_element_percent_value",
        "_extreme_element_resists",
        "_extreme_monster_defense",
        "_extreme_crit_resist_value",
        "_extreme_base_crit_percent",
        "_extreme_base_crit_damage_percent",
        "team_extreme_aura_choices",
    )

    def _extreme_default_team_objective_profile(
        self,
        owner_job: str,
        team: Iterable[str],
        *,
        teammate_weight: float = 0.30,
    ) -> dict[str, Any] | None:
        """Build the generic joint objective used by every team-sim route.

        The ordering is derived from attack channel, main stat and element of
        the selected teammates. No profession or skill receives a named
        priority. Captured-score routes can still supply their more precise
        damage-weighted profile explicitly.
        """

        owner_real_job = self._team_real_job(owner_job)
        skipped_owner = False
        members: list[dict[str, Any]] = []
        for raw_job in team:
            real_job = self._team_real_job(raw_job)
            if real_job == owner_real_job and not skipped_owner:
                skipped_owner = True
                continue
            profile = self._extreme_profile_for_job(real_job)
            attacks = [
                norm(value)
                for value in profile.get("attack_attrs", ())
                if norm(value) in {"物攻", "魔攻"}
            ]
            if not attacks:
                attack = norm(profile.get("attack_attr"))
                attacks = [
                    attack if attack in {"物攻", "魔攻"} else "物攻"
                ]
            members.append({
                "job": real_job,
                "weight": max(0.0, as_number(teammate_weight, 0.30)),
                "physical_share": attacks.count("物攻") / len(attacks),
                "magic_share": attacks.count("魔攻") / len(attacks),
                "main_stat": norm(profile.get("main_stat")),
                "element": norm(profile.get("element")),
            })
        if not members:
            return None
        return {
            "owner_job": owner_real_job,
            "members": members,
            "monster_defense": 3400.0,
            "element_resist": 20.0,
            "crit_resist": 8122.0,
            "freeze_coefficient": DEFAULT_FREEZE_COEFFICIENT,
            "base_element_attack": 42.0,
        }

    def _extreme_timeline_spec_with_objective(
        self,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach a default team objective unless the caller chose one."""

        prepared = dict(spec)
        if "team_objective_profile" not in prepared:
            prepared["team_objective_profile"] = (
                self._extreme_default_team_objective_profile(
                    norm(prepared.get("job")),
                    prepared.get("team", ()),
                )
            )
        return prepared

    def _extreme_parallel_owner_snapshot(self) -> TimelineSimulationSnapshot:
        runtime_values = {
            name: copy.deepcopy(getattr(self, name))
            for name in self._EXTREME_PARALLEL_RUNTIME_ATTRS
            if hasattr(self, name)
        }
        runtime_values["_timeline_extreme_mode"] = True
        build = self.collect_build() if callable(getattr(self, "collect_build", None)) else copy.deepcopy(self.build)
        signature = self._team_practical_cache_value((build, runtime_values))
        cached = getattr(self, "_extreme_parallel_snapshot", None)
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] == signature
            and isinstance(cached[1], TimelineSimulationSnapshot)
        ):
            return cached[1]
        snapshot = TimelineSimulationSnapshot.create(build, [], runtime_values)
        self._extreme_parallel_snapshot = (signature, snapshot)
        return snapshot

    def _extreme_timeline_tasks_parallel(
        self,
        task_specs: list[dict[str, Any]],
        progress_callback=None,
        followup_factory=None,
        followup_progress_callback=None,
        expected_followup_count: int = 0,
        followup_result_factory=None,
        followup_result_progress_callback=None,
        expected_followup_result_count: int = 0,
        heartbeat_callback=None,
    ) -> list[dict[str, Any]]:
        """Calculate independent character axes concurrently and preserve input order.

        ``followup_factory`` may return ``(token, spec)`` pairs after an
        initial result becomes available.  Those independent tasks enter the
        same worker queue immediately, while the remaining initial axes are
        still running.  Their results are exposed through
        ``_extreme_parallel_followup_results`` and never alter the positional
        return value of this method.
        """

        task_specs = [
            self._extreme_timeline_spec_with_objective(spec)
            for spec in task_specs
        ]

        reported_indexes: set[int] = set()
        self._extreme_parallel_followup_results = {}
        self._extreme_parallel_followup_jobs = {}
        self._extreme_parallel_followup_error = ""

        def generated_followups(
            index: int,
            result: dict[str, Any],
        ) -> list[tuple[str, dict[str, Any]]]:
            if not callable(followup_factory):
                return []
            try:
                generated = followup_factory(index, result) or ()
            except Exception as exc:
                self._extreme_parallel_followup_error = repr(exc)
                return []
            rows: list[tuple[str, dict[str, Any]]] = []
            for item in generated:
                if (
                    not isinstance(item, (list, tuple))
                    or len(item) < 2
                    or not isinstance(item[1], dict)
                ):
                    continue
                token = str(item[0] or "")
                if not token:
                    continue
                rows.append((
                    token,
                    self._extreme_timeline_spec_with_objective(item[1]),
                ))
            return rows

        def generated_followup_results(
            token: str,
            result: dict[str, Any],
            completed_results: dict[str, dict[str, Any]],
        ) -> list[tuple[str, dict[str, Any]]]:
            if not callable(followup_result_factory):
                return []
            try:
                generated = followup_result_factory(
                    token,
                    result,
                    completed_results,
                ) or ()
            except Exception as exc:
                self._extreme_parallel_followup_error = repr(exc)
                return []
            rows: list[tuple[str, dict[str, Any]]] = []
            for item in generated:
                if (
                    not isinstance(item, (list, tuple))
                    or len(item) < 2
                    or not isinstance(item[1], dict)
                ):
                    continue
                nested_token = str(item[0] or "")
                if not nested_token:
                    continue
                rows.append((
                    nested_token,
                    self._extreme_timeline_spec_with_objective(item[1]),
                ))
            return rows

        def sequential_results() -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            followup_rows: dict[str, dict[str, Any]] = {}
            followup_jobs: dict[str, str] = {}
            followup_completed = 0
            followup_scheduled = 0
            nested_completed = 0
            nested_scheduled = 0
            for index, spec in enumerate(task_specs):
                result = self._extreme_timeline_result_from_spec(spec)
                rows.append(result)
                if index in reported_indexes or not callable(progress_callback):
                    pass
                else:
                    reported_indexes.add(index)
                    progress_callback(
                        len(reported_indexes),
                        index,
                        norm(spec.get("job")),
                    )
                generated = generated_followups(index, result)
                followup_scheduled += len(generated)
                for token, followup_spec in generated:
                    try:
                        followup_result = self._extreme_timeline_result_from_spec(
                            followup_spec
                        )
                    except Exception as exc:
                        self._extreme_parallel_followup_error = repr(exc)
                        continue
                    followup_rows[token] = followup_result
                    followup_jobs[token] = norm(followup_spec.get("job"))
                    followup_completed += 1
                    if callable(followup_progress_callback):
                        followup_progress_callback(
                            followup_completed,
                            followup_scheduled,
                            token,
                            followup_jobs[token],
                        )
                    generated_nested = generated_followup_results(
                        token,
                        followup_result,
                        followup_rows,
                    )
                    nested_scheduled += len(generated_nested)
                    for nested_token, nested_spec in generated_nested:
                        try:
                            nested_result = self._extreme_timeline_result_from_spec(
                                nested_spec
                            )
                        except Exception as exc:
                            self._extreme_parallel_followup_error = repr(exc)
                            continue
                        followup_rows[nested_token] = nested_result
                        followup_jobs[nested_token] = norm(
                            nested_spec.get("job")
                        )
                        nested_completed += 1
                        if callable(followup_result_progress_callback):
                            followup_result_progress_callback(
                                nested_completed,
                                nested_scheduled,
                                nested_token,
                                followup_jobs[nested_token],
                            )
            self._extreme_parallel_followup_results = followup_rows
            self._extreme_parallel_followup_jobs = followup_jobs
            return rows

        grouped_timing = bool(task_specs) and not callable(followup_factory) and not expected_followup_count and all(
            "_timing_ablation_source" in spec and "timing_ablation_glyphs" in spec
            for spec in task_specs
        )
        workload_count = max(
            len(task_specs),
            len(task_specs)
            + max(0, int(expected_followup_count))
            + max(0, int(expected_followup_result_count)),
        )
        if grouped_timing:
            # One queued task per recipient, not one per ablation axis. Keep
            # the existing worker/memory limits instead of spawning extra
            # workers solely because each recipient has several variants.
            workload_count = len({str(spec["_timing_ablation_source"]) for spec in task_specs})
        instance_overrides = getattr(self, "__dict__", {})
        # PureTimelineRuntime resolves legacy methods lazily and caches the
        # resulting bound method in ``__dict__``.  That is a normal prepared
        # runtime, not a test/UI monkey-patch.  Treating the cached
        # ``_extreme_team_timeline_result`` as a custom override disabled the
        # process pool after the first sequential/helper call, while the UI
        # continued to report the negotiated 4/8 budget.
        has_custom_timeline_override = (
            "_extreme_team_timeline_result" in instance_overrides
            and not bool(getattr(self, "_timeline_is_pure_runtime", False))
        )
        if (
            len(task_specs) <= 1
            or configured_timeline_workers(workload_count) <= 1
            or has_custom_timeline_override
        ):
            return sequential_results()
        accel_widget = getattr(self, "skill_long_accel_start", None)
        first_accel_start = (
            max(0.0, as_number(accel_widget.value()))
            if callable(getattr(accel_widget, "value", None))
            else 60.0
        )
        owner_snapshot = self._extreme_parallel_owner_snapshot()
        def task_from_spec(spec: dict[str, Any]) -> ExtremeTimelineTask:
            return ExtremeTimelineTask(
                owner_snapshot=owner_snapshot,
                job=norm(spec.get("job")),
                team=tuple(norm(member) for member in spec.get("team", [])),
                effects=tuple(spec.get("effects", [])),
                hand_multiplier=max(0.0, as_number(spec.get("hand_multiplier"), 1.0)),
                window=max(30.0, as_number(spec.get("window"), SKILL_BURST_PRACTICAL_WINDOW)),
                first_accel_start=first_accel_start,
                cast_schedule=spec.get("cast_schedule"),
                include_display_windows=bool(spec.get("include_display_windows", True)),
                build_override=spec.get("build_override"),
                blocked_intervals=tuple(spec.get("blocked_intervals") or []),
                partial_cast_schedule=bool(spec.get("partial_cast_schedule", False)),
                fast_long_timeline=bool(spec.get("fast_long_timeline", False)),
                long_sim_planner=bool(spec.get("long_sim_planner", False)),
                score_refinement=bool(spec.get("score_refinement", True)),
                same_environment_theory=bool(
                    spec.get("same_environment_theory", False)
                ),
                provider_override=norm(spec.get("provider_override")),
                score_actual_casts_by_source=spec.get(
                    "score_actual_casts_by_source"
                ),
                team_objective_profile=spec.get("team_objective_profile"),
                boss_hp_segments=(
                    tuple(
                        tuple(
                            (as_number(point[0]), as_number(point[1]))
                            for point in segment
                            if isinstance(point, (list, tuple)) and len(point) >= 2
                        )
                        for segment in (spec.get("boss_hp_segments") or ())
                    )
                    if "boss_hp_segments" in spec
                    else None
                ),
                player_buff_clear_times=tuple(
                    sorted({
                        max(0.0, as_number(value))
                        for value in (spec.get("player_buff_clear_times") or ())
                    })
                ),
                summon_removal_schedule=tuple(
                    sorted(
                        (
                            max(0.0, as_number(row[0])),
                            self._skill_burst_skill_key(row[1]),
                        )
                        for row in (spec.get("summon_removal_schedule") or ())
                        if isinstance(row, (list, tuple))
                        and len(row) >= 2
                        and self._skill_burst_skill_key(row[1])
                    )
                ),
                excluded_skill_keys=tuple(
                    sorted(
                        self._skill_burst_skill_key(value)
                        for value in (spec.get("excluded_skill_keys") or ())
                        if self._skill_burst_skill_key(value)
                    )
                ),
                score_exclusion_context=spec.get("score_exclusion_context"),
                timing_ablation_glyphs=(
                    tuple(spec["timing_ablation_glyphs"])
                    if "timing_ablation_glyphs" in spec else None
                ),
                precomputed_timing_result=spec.get("_timing_precomputed_result"),
                include_cost_hint=True,
            )

        tasks = [
            task_from_spec(spec)
            for spec in task_specs
        ]
        # Size the pool for the complete pipelined workload, not merely the
        # eight initial axes. Otherwise a 23-task score request silently falls
        # back to eight workers and makes the prefetched tasks slower than the
        # former 16-task first wave.
        if expected_followup_count > 0 or expected_followup_result_count > 0:
            # ProcessPoolExecutor grows lazily on Windows. Submitting only the
            # eight initial axes would otherwise create eight processes even
            # when the pipelined workload needs sixteen, leaving every dynamic
            # replan serialized behind its baseline. Pre-warm the complete
            # workload capacity before the first long task is queued.
            warm_timeline_process_pool(workload_count)
        executor = timeline_process_pool(workload_count)
        if executor is None:
            return sequential_results()
        job_costs = getattr(self, "_extreme_timeline_job_costs", None)
        if not isinstance(job_costs, dict):
            job_costs = {}
            self._extreme_timeline_job_costs = job_costs

        def remember_cost(job, value):
            seconds = as_number(value.pop("_parallel_cost_seconds", None))
            if seconds > 0:
                job_costs[job] = seconds
                while len(job_costs) > 64:
                    job_costs.pop(next(iter(job_costs)))
        try:
            if grouped_timing:
                grouped: dict[str, list[int]] = {}
                for index, spec in enumerate(task_specs):
                    grouped.setdefault(str(spec["_timing_ablation_source"]), []).append(index)
                chunks = _timing_axis_group_chunks(grouped.values())
                futures = {
                    executor.submit(calculate_timing_axis_group, tuple(tasks[index] for index in indexes)): indexes
                    for indexes in _order_timing_groups_by_cost(tasks, chunks, job_costs)
                }
                pending_groups = set(futures)
                grouped_results: list[dict[str, Any] | None] = [None] * len(tasks)
                last_completion = score_work_clock()
                last_heartbeat = last_completion
                # A group returns after several axes; don't treat that normal
                # interval as a hung single-axis worker.
                timeout = _extreme_parallel_stall_timeout_seconds() * max(map(len, chunks))
                while pending_groups:
                    score_checkpoint(force=True)
                    done, pending_groups = wait(pending_groups, timeout=0.05, return_when=FIRST_COMPLETED)
                    if not done and score_work_clock() - last_completion >= timeout:
                        raise TimeoutError("timing axis group produced no result")
                    now = score_work_clock()
                    if (
                        callable(heartbeat_callback)
                        and now - last_heartbeat >= 0.75
                    ):
                        active_jobs = sorted({
                            tasks[index].job
                            for future in pending_groups
                            for index in futures[future]
                        })
                        heartbeat_callback(
                            len(reported_indexes),
                            len(tasks),
                            "、".join(active_jobs[:3])
                            + ("等" if len(active_jobs) > 3 else ""),
                        )
                        last_heartbeat = now
                    for future in done:
                        indexes = futures[future]
                        values = future.result()
                        if len(values) != len(indexes) or any(not isinstance(value, dict) for value in values):
                            raise ValueError("Incomplete timing axis group")
                        last_completion = score_work_clock()
                        for index, value in zip(indexes, values):
                            remember_cost(tasks[index].job, value)
                            grouped_results[index] = value
                            reported_indexes.add(index)
                            if callable(progress_callback):
                                progress_callback(len(reported_indexes), index, tasks[index].job)
                    if bool(getattr(self, "_extreme_timeline_process_events", False)):
                        QApplication.processEvents()
                self._extreme_parallel_last_fallback = ""
                return [result for result in grouped_results if isinstance(result, dict)]
            future_metadata = {
                executor.submit(calculate_extreme_timeline_task, task): (
                    "initial",
                    index,
                    "",
                    task.job,
                )
                for index, task in enumerate(tasks)
            }
            pending = set(future_metadata)
            results: list[dict[str, Any] | None] = [None] * len(tasks)
            followup_results: dict[str, dict[str, Any]] = {}
            followup_jobs: dict[str, str] = {}
            completed = 0
            followup_completed = 0
            followup_scheduled = 0
            nested_completed = 0
            nested_scheduled = 0
            last_completion = score_work_clock()
            last_heartbeat = last_completion
            stall_timeout = _extreme_parallel_stall_timeout_seconds()
            while pending:
                score_checkpoint(force=True)
                done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                if not done:
                    if score_work_clock() - last_completion >= stall_timeout:
                        raise TimeoutError(
                            "timeline process pool produced no result for "
                            f"{stall_timeout:.0f}s "
                            f"({completed}/{len(tasks)} completed)"
                        )
                    now = score_work_clock()
                    if (
                        callable(heartbeat_callback)
                        and now - last_heartbeat >= 0.75
                    ):
                        active_jobs = sorted({
                            metadata[3]
                            for future in pending
                            for metadata in (future_metadata[future],)
                            if metadata[3]
                        })
                        heartbeat_callback(
                            completed + followup_completed + nested_completed,
                            len(tasks) + max(
                                followup_scheduled,
                                max(0, int(expected_followup_count)),
                            ) + max(
                                nested_scheduled,
                                max(0, int(expected_followup_result_count)),
                            ),
                            "、".join(active_jobs[:3])
                            + ("等" if len(active_jobs) > 3 else ""),
                        )
                        last_heartbeat = now
                    if bool(getattr(self, "_extreme_timeline_process_events", False)):
                        QApplication.processEvents()
                    continue
                for future in done:
                    kind, index, token, job = future_metadata[future]
                    result = future.result()
                    last_completion = score_work_clock()
                    if isinstance(result, dict):
                        remember_cost(job, result)
                    if kind == "initial":
                        results[index] = result
                        completed += 1
                        reported_indexes.add(index)
                        if callable(progress_callback):
                            progress_callback(completed, index, tasks[index].job)
                        generated = generated_followups(index, result)
                        followup_scheduled += len(generated)
                        for followup_token, followup_spec in generated:
                            followup_task = task_from_spec(followup_spec)
                            followup_future = executor.submit(
                                calculate_extreme_timeline_task,
                                followup_task,
                            )
                            future_metadata[followup_future] = (
                                "followup",
                                -1,
                                followup_token,
                                followup_task.job,
                            )
                            pending.add(followup_future)
                    elif kind == "followup":
                        followup_results[token] = result
                        followup_jobs[token] = job
                        followup_completed += 1
                        if callable(followup_progress_callback):
                            followup_progress_callback(
                                followup_completed,
                                followup_scheduled,
                                token,
                                job,
                            )
                        generated_nested = generated_followup_results(
                            token,
                            result,
                            followup_results,
                        )
                        nested_scheduled += len(generated_nested)
                        for nested_token, nested_spec in generated_nested:
                            nested_task = task_from_spec(nested_spec)
                            nested_future = executor.submit(
                                calculate_extreme_timeline_task,
                                nested_task,
                            )
                            future_metadata[nested_future] = (
                                "nested",
                                -1,
                                nested_token,
                                nested_task.job,
                            )
                            pending.add(nested_future)
                    else:
                        followup_results[token] = result
                        followup_jobs[token] = job
                        nested_completed += 1
                        if callable(followup_result_progress_callback):
                            followup_result_progress_callback(
                                nested_completed,
                                nested_scheduled,
                                token,
                                job,
                            )
                if bool(getattr(self, "_extreme_timeline_process_events", False)):
                    QApplication.processEvents()
            self._extreme_parallel_followup_results = followup_results
            self._extreme_parallel_followup_jobs = followup_jobs
            self._extreme_parallel_last_fallback = ""
            return [result for result in results if isinstance(result, dict)]
        except Exception as exc:
            self._extreme_parallel_last_fallback = repr(exc)
            reset_timeline_process_pool()
            if (
                os.getenv("DRAGON_NEST_TIMELINE_RAISE_POOL_ERROR", "0")
                .strip()
                .lower()
                in {"1", "true", "on", "yes"}
            ):
                raise
            # Re-run in the owner process.  This is slower but independent of
            # a stale/broken worker pool, and now reports each completed job so
            # the UI cannot remain frozen at 0/N throughout the fallback.
            return sequential_results()

    def _extreme_timeline_result_from_spec(
        self,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the regular result method while preserving its positional API."""

        if "_timing_precomputed_result" in spec:
            return spec["_timing_precomputed_result"]
        # The serialized exclusion context contains a full combat snapshot but
        # is not consumed by this sequential invocation. Do not clone it for
        # every recipient; all actual simulator inputs remain private copies.
        copied = copy.deepcopy({k: v for k, v in spec.items() if k != "score_exclusion_context"})
        optional = {
            key: copied[key]
            for key in (
                "cast_schedule",
                "include_display_windows",
                "build_override",
                "blocked_intervals",
                "partial_cast_schedule",
                "fast_long_timeline",
                "long_sim_planner",
                "score_refinement",
                "same_environment_theory",
                "provider_override",
                "score_actual_casts_by_source",
                "team_objective_profile",
                "boss_hp_segments",
                "player_buff_clear_times",
                "summon_removal_schedule",
                "excluded_skill_keys",
            )
            if key in copied
        }
        summary_only = "timing_ablation_glyphs" in copied
        if summary_only:
            optional["timing_summary_only"] = True
        with ephemeral_timing_axis_cache(self, summary_only):
            result = self._extreme_team_timeline_result(
                copied.get("job", ""),
                copied.get("team", []),
                copied.get("effects", []),
                copied.get("hand_multiplier", 1.0),
                copied.get("window", SKILL_BURST_PRACTICAL_WINDOW),
                **optional,
            )
        return summarize_timing_axis(result, copied["timing_ablation_glyphs"]) if summary_only else result

    def _extreme_team_effects_for_jobs(self, jobs: list[str]) -> list[dict[str, Any]]:
        """Build team effects from immutable per-job templates during search."""
        cache = getattr(self, "_extreme_team_effect_template_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._extreme_team_effect_template_cache = cache
        effects: list[dict[str, Any]] = []
        for member_row, job in enumerate(jobs[:TEAM_MEMBER_ROWS]):
            raw_job = norm(job)
            if not raw_job or raw_job == "无":
                continue
            real_job = self._team_real_job(raw_job)
            aura = self._team_aura_for_job(real_job) if real_job in TEAM_AURA_JOBS else ""
            provider = f"队员{member_row + 1} {raw_job}"
            combat_levels = self.build.get('_score_combat_levels_by_provider', {}).get(provider, {})
            cache_key = (raw_job, aura, tuple(sorted(combat_levels.items())))
            templates = cache.get(cache_key)
            if templates is None:
                template_provider = provider if combat_levels else f"队员1 {raw_job}"
                templates = []
                for skill in self._team_skills_for_job(raw_job):
                    skill_name = self._team_effect_skill_name(skill)
                    levels = self._team_effect_levels(skill_name, template_provider)
                    level = self._team_effect_default_level(skill_name, template_provider, levels)
                    attrs = self._team_effect_attrs(skill_name, level, template_provider)
                    if not attrs:
                        continue
                    timing = self._team_effect_timing(template_provider, skill_name, level)
                    cooldown = as_number(timing.get("cooldown"))
                    duration = as_number(timing.get("duration"))
                    templates.append({
                        "row": None,
                        "skill": (
                            self._team_manual_effect_label(skill_name)
                            if skill_name in TEAM_MANUAL_DEBUFFS
                            else skill_name
                        ),
                        "level": level,
                        "attrs": dict(attrs),
                        "cooldown": cooldown,
                        "duration": duration,
                        "usage_ratio": self._team_cdr_usage_ratio(skill_name),
                        "coverage": self._team_effect_coverage(cooldown, duration),
                        "effective_attrs": self._team_effective_attrs(attrs, cooldown, duration),
                        "manual": skill_name in TEAM_MANUAL_DEBUFFS,
                    })
                cache[cache_key] = templates
                while len(cache) > 128:
                    cache.pop(next(iter(cache)))
            provider = f"队员{member_row + 1} {raw_job}"
            effects.extend({**template, "provider": provider} for template in templates)
        return effects

    def _default_extreme_coefficient(self, job: str) -> float:
        job_text = norm(job)
        if job_text == "输出曜":
            base = self._default_extreme_coefficient("曜")
            return base / 0.3 if base > 0 else 1.0
        if job_text == "打坐曜":
            job_text = "曜"
        extracted = self.store.team_extreme_coefficients()
        if job_text in extracted:
            return extracted[job_text]
        if job_text in TEAM_EXTREME_SPECIAL_COEFFICIENTS:
            return TEAM_EXTREME_SPECIAL_COEFFICIENTS[job_text]
        return 1.3 if self._team_job_attack_profile(job_text).get("element") else 1.0

    def _extreme_job_allowed_by_mode(self, job: str, mode: str) -> bool:
        return True

    @staticmethod
    def _extreme_mode_has_preference(mode: str) -> bool:
        return norm(mode) in {"物理队", "魔法队", "火队", "水队", "光队", "暗队"}

    def _extreme_job_matches_mode(self, job: str, mode: str) -> bool:
        mode_text = norm(mode)
        if not self._extreme_mode_has_preference(mode_text):
            return True
        profile = self._team_job_attack_profile(job)
        if mode_text == "物理队":
            return profile["attack"] == "物攻"
        if mode_text == "魔法队":
            return profile["attack"] == "魔攻"
        element_map = {"火队": "火", "水队": "水", "光队": "光", "暗队": "暗"}
        return profile["element"] == element_map.get(mode_text)

    def _extreme_team_mode_match_count(self, team: list[str] | tuple[str, ...], mode: str) -> int:
        if not self._extreme_mode_has_preference(mode):
            return 0
        return sum(1 for job in team if self._extreme_job_matches_mode(job, mode))

    @staticmethod
    def _extreme_team_has_required_roles(
        team: list[str] | tuple[str, ...],
        require_tank: bool = True,
        require_healer: bool = True,
    ) -> bool:
        real_team = [BuildEditor._team_real_job(job) for job in team]
        has_t = any(job in TEAM_TANK_JOBS for job in real_team)
        has_h = any(job in TEAM_HEALER_JOBS for job in real_team)
        return (has_t or not require_tank) and (has_h or not require_healer)

    @staticmethod
    def _extreme_team_can_still_meet_roles(
        team: list[str] | tuple[str, ...],
        remaining_slots: int,
        require_tank: bool = True,
        require_healer: bool = True,
    ) -> bool:
        real_team = [BuildEditor._team_real_job(job) for job in team]
        has_t = any(job in TEAM_TANK_JOBS for job in real_team)
        has_h = any(job in TEAM_HEALER_JOBS for job in real_team)
        missing = (1 if require_tank and not has_t else 0) + (1 if require_healer and not has_h else 0)
        return missing <= remaining_slots

    def _extreme_job_mode_priority(self, job: str, mode: str) -> int:
        mode_text = norm(mode)
        if not mode_text or mode_text == "不限":
            return 1
        return 0 if self._extreme_job_matches_mode(job, mode_text) else 1

    def _extreme_coefficients_for_jobs(self, jobs: list[str]) -> dict[str, float]:
        saved = getattr(self, "team_extreme_coefficients", {})
        return {
            job: as_number(saved.get(job, saved.get(self._team_real_job(job))), self._default_extreme_coefficient(job))
            for job in jobs
        }

    def _extreme_base_build(self, build: dict[str, Any], boss_name: str) -> dict[str, Any]:
        out = copy.deepcopy(build)
        out["buffs"] = []
        out.pop("extra_buff_attrs", None)
        out["boss"] = norm(boss_name) or "无"
        preset = BOSS_PRESETS.get(norm(boss_name))
        if preset:
            out["crit_resist_value"] = int(preset.get("crit_resist", 0))
            element_resist = as_number(preset.get("element_resist"))
            out["element_resist_percent"] = element_resist
            out["element_resists"] = {"火": element_resist, "水": element_resist, "光": element_resist, "暗": element_resist}
            out["monster_defense"] = int(preset.get("monster_defense", 0))
            out["monster_defense_rate"] = BuildCalculator.defense_percent(out["monster_defense"])
        else:
            out["crit_resist_value"] = 0
            out["element_resist_percent"] = 0.0
            out["element_resists"] = {"火": 0.0, "水": 0.0, "光": 0.0, "暗": 0.0}
            out["monster_defense"] = 0
            out["monster_defense_rate"] = 0.0
        return out

    def _extreme_base_element_percent(self, base_summary: dict[str, Any], current_job: str = "") -> float:
        if not self._team_job_attack_profile(current_job).get("element"):
            return 42.0
        return max(as_number(base_summary.get(f"{element}攻%")) for element in ("火", "水", "光", "暗"))

    def _extreme_profile_for_job(self, job: str, first_job: str = "") -> dict[str, Any]:
        second_job = self._team_real_job(job)
        first = norm(first_job) or self._extreme_first_job_for_second(second_job)
        family = self.store.job_family(second_job, first)
        effective = next((row for row in self.store.tables["effective"] if norm(row.get("职业")) == second_job), {})
        raw_attack = norm(effective.get("攻击1")) or "物攻"
        attack_attrs = ["物攻", "魔攻"] if raw_attack == "双攻" else [raw_attack if raw_attack in {"物攻", "魔攻"} else "物攻"]
        attack_attr = attack_attrs[0]
        element = norm(effective.get("属性1"))
        element_attr = f"{element}攻%" if element and element != "无" else ""
        return {
            "first_job": first,
            "second_job": second_job,
            "family": family,
            "attack_attr": attack_attr,
            "attack_attrs": attack_attrs,
            "raw_attack": raw_attack,
            "element_attr": element_attr,
            "element": element if element and element != "无" else "",
            "main_stat": self._best_source_stat(family, attack_attr),
        }

    def _extreme_profile_for_build(self, build: dict[str, Any]) -> dict[str, Any]:
        return self._extreme_profile_for_job(norm(build.get("second_job")), norm(build.get("first_job")))

    def _extreme_convert_attr(
        self,
        attr: str,
        source_profile: dict[str, Any],
        target_profile: dict[str, Any],
        *,
        convert_any_main_stat: bool = False,
    ) -> str:
        text = norm(attr)
        if not text or text == "无":
            return text
        attack_converted = self._attack_attr_counterpart(text, source_profile.get("attack_attr", "物攻"), target_profile.get("attack_attr", "物攻"))
        if attack_converted != text:
            return attack_converted
        base, suffix = self._attr_base_and_suffix(text)
        source_main = norm(source_profile.get("main_stat"))
        target_main = norm(target_profile.get("main_stat")) or source_main
        if base in {"力量", "敏捷", "智力"} and (convert_any_main_stat or base == source_main):
            return f"{target_main}{suffix}"
        return text

    def _extreme_convert_glyph_a_name(
        self,
        name: str,
        source_profile: dict[str, Any],
        target_profile: dict[str, Any],
    ) -> str:
        text = norm(name)
        if not text or text == "无":
            return text
        replacements = [
            (self._glyph_keyword_for_attr(source_profile.get("attack_attr")), self._glyph_keyword_for_attr(target_profile.get("attack_attr"))),
            (self._glyph_keyword_for_attr(source_profile.get("main_stat")), self._glyph_keyword_for_attr(target_profile.get("main_stat"))),
        ]
        for old, new in replacements:
            if old and new and old != new and old in text:
                return self._choose_by_keywords(self.store.glyph_a_names(), [(new,)], forbidden=()) or text
        return text

    def _extreme_attack_glyph_extra(self, name: str, target_profile: dict[str, Any]) -> str:
        text = norm(name)
        attack_keyword = self._glyph_keyword_for_attr(target_profile.get("attack_attr"))
        if attack_keyword and attack_keyword in text:
            return norm(target_profile.get("attack_attr")) or "无"
        return ""

    def _extreme_balance_glyph_a_items(self, items: list[dict[str, Any]], target_profile: dict[str, Any]) -> list[dict[str, Any]]:
        wanted = [self._glyph_keyword_for_attr(target_profile.get("attack_attr")), *self._glyph_a_required_stat_keywords(target_profile)]
        wanted = [keyword for keyword in unique(wanted) if keyword]
        names = [norm(item.get("name")) for item in items]
        missing = [keyword for keyword in wanted if not any(keyword in name for name in names)]
        if not missing:
            return items
        protected = {"最终", "破坏", "魔力", "疾风", "巨熊", "智慧", "致命", "生命", "健康"}
        for keyword in missing:
            replacement = self._choose_by_keywords(self.store.glyph_a_names(), [(keyword,)], forbidden=())
            if not replacement or replacement == "无":
                continue
            used_names = {norm(item.get("name")) for item in items}
            if replacement in used_names:
                continue
            replace_index = -1
            for index in range(len(items) - 1, -1, -1):
                current = norm(items[index].get("name"))
                if current == "无" or not any(key in current for key in protected):
                    replace_index = index
                    break
            if replace_index < 0 and items:
                replace_index = len(items) - 1
            if replace_index >= 0:
                items[replace_index]["name"] = replacement
                extra = self._extreme_attack_glyph_extra(replacement, target_profile)
                if extra:
                    items[replace_index]["extra"] = extra
        return items

    def _extreme_fill_empty_glyph_a_crit_if_needed(self, build: dict[str, Any]) -> None:
        try:
            _rows, summary = self.calculator.calculate(build)
        except Exception:
            return
        if as_number(summary.get("crit_percent")) >= 90.0:
            return
        options = self.store.glyph_a_extra()
        crit_option = self._first_existing(options, ["致命"])
        if not crit_option or crit_option == "无":
            return
        for item in build.get("glyph_a", []):
            if norm(item.get("extra")) not in {"", "无"}:
                continue
            forbidden = self._glyph_a_forbidden_extra(item.get("name"))
            if forbidden and norm(forbidden) == norm(crit_option):
                continue
            item["extra"] = crit_option
            try:
                _rows, summary = self.calculator.calculate(build)
            except Exception:
                continue
            if as_number(summary.get("crit_percent")) >= 90.0:
                break

    def _extreme_row_level_for_name(self, table_name: str, name_key: str, name: str) -> str:
        text = norm(name)
        if not text or text == "无":
            return ""
        for row in self.store.tables.get(table_name, []):
            if norm(row.get(name_key)) == text:
                return norm(row.get("等级"))
        return ""

    def _extreme_equipment_name_for_job(self, item: dict[str, Any], slot: str, target_profile: dict[str, Any]) -> str:
        current_name = norm(item.get("name"))
        level = self._extreme_row_level_for_name("base", "装备名称", current_name)
        if not level:
            return current_name
        preferred_jobs = [target_profile.get("first_job", ""), target_profile.get("family", ""), target_profile.get("second_job", "")]
        if level == "70S":
            preferred_jobs = [target_profile.get("family", ""), target_profile.get("first_job", ""), target_profile.get("second_job", "")]
        for job in preferred_jobs:
            name = self._equipment_choice_for_tier(level, norm(job), slot)
            if name and name != "无":
                return name
        return current_name

    def _extreme_jewelry_name_for_tier(self, item: dict[str, Any], slot: str) -> str:
        current_name = norm(item.get("name"))
        level = self._extreme_row_level_for_name("jewelry", "饰品名称", current_name)
        if not level:
            return current_name
        name = self._jewelry_choice_for_tier(level, slot)
        return name if name and name != "无" else current_name

    def _extreme_convert_enchant_plan(self, slot: str, target_profile: dict[str, Any], weapon_like: bool) -> list[str]:
        options = self.store.enchant_names(weapon_like)
        return self._weapon_enchant_plan(options, target_profile) if weapon_like else self._enchant_plan(options, target_profile)

    def _extreme_jewelry_variant(self, target_profile: dict[str, Any]) -> str:
        if norm(target_profile.get("first_job")) == "贤者":
            return "力敏"
        return {"力量": "力敏", "敏捷": "力敏", "智力": "敏智", "体质": "智体"}.get(norm(target_profile.get("main_stat")), "力敏")

    def _extreme_core_conversion_for_profile(self, core: dict[str, Any], target_profile: dict[str, Any]) -> str:
        conversion = norm(core.get("conversion"))
        options = self.store.core_conversions()
        if norm(target_profile.get("second_job")) in {"圣骑士", "十字军"}:
            return self._first_existing(options, ["魔防→魔攻", conversion])
        if not conversion or conversion == "无":
            conversion = ""
        source = ""
        if "→" in conversion:
            source = norm(conversion.split("→", 1)[0])
        target_attack = norm(target_profile.get("attack_attr")) or "物攻"
        choices: list[str] = []
        if source:
            choices.append(f"{source}→{target_attack}")
        choices.append("致命→物攻" if target_attack == "物攻" else "魔防→魔攻")
        choices.append(conversion)
        return self._first_existing(options, choices)

    def _extreme_card_total_attrs(self, build: dict[str, Any]) -> dict[str, float]:
        raw: dict[str, float] = {}
        for item in build.get("card_grades", []):
            attrs = self.calculator.card_attrs(item.get("name"), item.get("grade") or "无")
            for attr, value in attrs.items():
                raw[attr] = raw.get(attr, 0.0) + as_number(value)
        bonus_levels = build.get("card_bonus_levels", {}) if isinstance(build.get("card_bonus_levels", {}), dict) else {}
        out: dict[str, float] = {}
        for attr, value in raw.items():
            bonus_key = DISPLAY_TO_TARGET.get(attr, attr)
            level = as_number(bonus_levels.get(bonus_key, bonus_levels.get(attr, 0)))
            out[attr] = value * (1 + level * 0.05)
        return out

    def _extreme_card_conversion_delta(
        self,
        build: dict[str, Any],
        source_profile: dict[str, Any],
        target_profile: dict[str, Any],
    ) -> dict[str, float]:
        totals = self._extreme_card_total_attrs(build)
        source_attack = norm(source_profile.get("attack_attr")) or "物攻"
        target_attacks = [attr for attr in target_profile.get("attack_attrs", []) if attr in {"物攻", "魔攻"}] or [norm(target_profile.get("attack_attr")) or "物攻"]
        physical = as_number(totals.get("物攻")) + (as_number(totals.get("最小物攻")) + as_number(totals.get("最大物攻"))) / 2
        magic = as_number(totals.get("魔攻")) + (as_number(totals.get("最小魔攻")) + as_number(totals.get("最大魔攻"))) / 2
        source_total = magic if source_attack == "魔攻" else physical
        target_value = max(physical, magic) if len(set(target_attacks)) > 1 else source_total
        delta: dict[str, float] = {}
        for attack in set(target_attacks):
            current = magic if attack == "魔攻" else physical
            change = target_value - current
            if abs(change) > 1e-9:
                delta[attack] = delta.get(attack, 0.0) + change
        return delta

    def _extreme_converted_build_for_job(self, job: str, personal_attrs: dict[str, float] | None = None) -> dict[str, Any]:
        job = self._team_real_job(job)
        source_build = getattr(self, "_extreme_clean_base_build", self.build)
        cache_key = (norm(job), self._attr_cache_signature(personal_attrs or {}))
        converted_cache = getattr(self, "_extreme_converted_build_cache", None)
        if isinstance(converted_cache, dict) and cache_key in converted_cache:
            return copy.deepcopy(converted_cache[cache_key])
        if personal_attrs:
            # Equipment, glyph, rune and card conversion only depends on the
            # target job. Reuse that expensive base conversion, then layer the
            # current personal-buff attributes on top.
            build = self._extreme_converted_build_for_job(job)
            extra = dict(build.get("extra_buff_attrs", {}))
            self._merge_attrs(extra, personal_attrs)
            build["extra_buff_attrs"] = extra
            if isinstance(converted_cache, dict):
                converted_cache[cache_key] = copy.deepcopy(build)
            return build
        build = copy.deepcopy(source_build)
        source_profile = self._extreme_profile_for_build(source_build)
        target_profile = self._extreme_profile_for_job(job)
        build["first_job"] = target_profile.get("first_job")
        build["second_job"] = target_profile.get("second_job")
        build["effective_second_job"] = target_profile.get("second_job")
        build["buffs"] = []

        equipment: list[dict[str, Any]] = []
        for item in build.get("equipment", []):
            slot = norm(item.get("slot"))
            new_item = copy.deepcopy(item)
            new_item["name"] = self._extreme_equipment_name_for_job(item, slot, target_profile)
            if slot in {"主手", "副手"}:
                new_item["enhance"] = 13
            suffix_values = self.store.suffix_names(target_profile.get("first_job"), target_profile.get("second_job"), new_item.get("name"))
            new_item["suffix"] = self._suffix_choice(suffix_values, slot, target_profile)
            new_item["enchants"] = self._extreme_convert_enchant_plan(slot, target_profile, slot in {"主手", "副手"})
            equipment.append(new_item)
        build["equipment"] = equipment

        jewelry: list[dict[str, Any]] = []
        for item in build.get("jewelry", []):
            slot = norm(item.get("slot"))
            display_slot = "戒指" if slot.startswith("戒指") else slot
            new_item = copy.deepcopy(item)
            new_item["slot"] = display_slot
            new_item["name"] = self._extreme_jewelry_name_for_tier(item, display_slot)
            new_item["variant"] = self._extreme_jewelry_variant(target_profile)
            weapon_like = display_slot == "戒指"
            new_item["enchants"] = self._extreme_convert_enchant_plan(display_slot, target_profile, weapon_like)
            jewelry.append(new_item)
        build["jewelry"] = jewelry

        glyph_a, glyph_s = self._standard_glyph_loadout(target_profile)
        build["glyph_a"] = copy.deepcopy(glyph_a)
        build["glyph_s"] = copy.deepcopy(glyph_s)

        normal_runes, core = self._standard_rune_loadout(target_profile)
        build["normal_runes"] = copy.deepcopy(normal_runes)
        build["core_rune"] = copy.deepcopy(core)

        cards, card_bonus_levels = self._standard_card_profile()
        build["card_grades"] = cards
        build["card_bonus_levels"] = card_bonus_levels
        build["title"] = self._best_title_for_profile(
            target_profile,
            norm(build.get("title")),
        )

        engravings: list[dict[str, Any]] = []
        for item in build.get("engravings", []):
            new_item = copy.deepcopy(item)
            if "extra" in new_item:
                new_item["extra"] = self._extreme_convert_attr(new_item.get("extra"), source_profile, target_profile)
            engravings.append(new_item)
        build["engravings"] = engravings

        extra: dict[str, float] = {}
        self._merge_attrs(extra, self._extreme_card_conversion_delta(build, source_profile, target_profile))
        if personal_attrs:
            self._merge_attrs(extra, personal_attrs)
        build["extra_buff_attrs"] = extra
        if isinstance(converted_cache, dict):
            converted_cache[cache_key] = copy.deepcopy(build)
        return build

    def _extreme_timeline_personal_buff_labels(self, job: str) -> list[str]:
        real_job = self._team_real_job(job)
        job_sources = set(self.store.job_buff_sources(real_job))
        labels: list[str] = []
        for row in self.store.tables.get("buff", []):
            source = norm(row.get("来源"))
            category = norm(row.get("类别"))
            skill = norm(row.get("技能"))
            if source == "通用" and category == "被动" and skill == "力量掌握":
                labels.append(buff_display_label(row))
                break
        for row in self.store.tables.get("buff", []):
            source = norm(row.get("来源"))
            if source not in job_sources:
                continue
            category = norm(row.get("类别"))
            if category not in {"被动", "个人buff"}:
                continue
            if category == "个人buff":
                rule = self.store.buff_activation_rule(source, row.get("技能"), category)
                if rule and norm(rule.get("mode")) != "permanent":
                    continue
            label = buff_display_label(row)
            if label and label not in labels:
                labels.append(label)
        excludes = TEAM_JOB_BUFF_VARIANT_EXCLUDES.get(norm(job), set())
        labels = [
            label
            for label in labels
            if self._team_effect_skill_name(label) not in excludes
        ]
        return self._filter_exclusive_buff_labels(labels)

    def _extreme_timeline_build_for_job(self, job: str) -> dict[str, Any]:
        real_job = self._team_real_job(job)
        build = self._extreme_converted_build_for_job(real_job)
        defaults = load_skill_build_defaults().get(real_job, {})
        current_real_job = self._team_real_job(norm(self.build.get("second_job")))
        if current_real_job == real_job:
            source_skill_build = self.build.get("skill_build", {})
            source_mastery_build = self.build.get("skill_mastery_build", {})
        else:
            source_skill_build = defaults.get("skill_build", {}) if isinstance(defaults, dict) else {}
            source_mastery_build = defaults.get("skill_mastery_build", {}) if isinstance(defaults, dict) else {}
        build["skill_build"] = copy.deepcopy(source_skill_build) if isinstance(source_skill_build, dict) else {}
        build["skill_mastery_build"] = copy.deepcopy(source_mastery_build) if isinstance(source_mastery_build, dict) else {}
        build["buffs"] = []
        provider = f"队员0 {job}"
        for label in self._extreme_timeline_personal_buff_labels(job):
            skill = self._team_effect_skill_name(label)
            levels = self._team_effect_levels(skill, provider)
            level = self._team_effect_default_level(skill, provider, levels)
            if skill and skill != "无" and level and level != "无":
                build["buffs"].append({"skill": label, "level": level})
        build["buffs"] = self._dedup_build_buffs(build["buffs"])
        self._extreme_fill_empty_glyph_a_crit_if_needed(build)
        self._extreme_apply_auto_skill_glyphs(build, job, count=4)
        return build

    def _extreme_apply_team_score_card_profile(
        self,
        build: dict[str, Any],
    ) -> None:
        """Apply the fixed card baseline used by live-combat theory scores."""

        existing = {
            norm(item.get("name")): item
            for item in build.get("card_grades", [])
            if isinstance(item, dict) and norm(item.get("name"))
        }
        names = [norm(name) for name in self.store.card_names() if norm(name)]
        if not names:
            names = list(existing)
        build["card_grades"] = [
            {
                **copy.deepcopy(existing.get(name, {})),
                "name": name,
                "grade": "L",
            }
            for name in names
        ]

        attrs = unique(
            [
                *CARD_PREVIEW_ATTRS,
                *(
                    build.get("card_bonus_levels", {}).keys()
                    if isinstance(build.get("card_bonus_levels"), dict)
                    else ()
                ),
            ]
        )
        build["card_bonus_levels"] = {
            attr: 0 if norm(attr) in {"致命伤害", "爆伤"} else 25
            for attr in attrs
            if norm(attr)
        }

    def _extreme_team_score_build_for_job(self, job: str) -> dict[str, Any]:
        """Return the deterministic equipment/skill baseline for score axes.

        Team scoring must compare every capture against the same build rather
        than inheriting card grades, card growth levels or skill points that
        happen to be open in 配装1. Timeline-owned buffs are removed after the
        one-click buff preset is applied, so their real cast windows remain the
        only source of that bonus.
        """

        real_job = self._team_real_job(job)
        build = self._extreme_timeline_build_for_job(job)
        defaults = load_skill_build_defaults().get(real_job, {})
        if isinstance(defaults, dict):
            build["skill_build"] = copy.deepcopy(
                defaults.get("skill_build", {})
            )
            build["skill_mastery_build"] = copy.deepcopy(
                defaults.get("skill_mastery_build", {})
            )

        self._extreme_apply_team_score_card_profile(build)

        # Match 配装页“一键选择buff”: common Strength Mastery plus the
        # job's passive/confirmed permanent personal effects. Dynamic personal
        # buffs are deliberately removed and are earned only by their casts or
        # triggers on the generated timeline.
        build["buffs"] = []
        provider = f"队员0 {job}"
        for label in self._extreme_timeline_personal_buff_labels(job):
            skill = self._team_effect_skill_name(label)
            levels = self._team_effect_levels(skill, provider)
            level = self._team_effect_default_level(skill, provider, levels)
            if skill and skill != "无" and level and level != "无":
                build["buffs"].append({"skill": label, "level": level})
        build["buffs"] = self._dedup_build_buffs(build["buffs"])
        self._remove_timeline_owned_build_buffs(build, job)

        # Card attack conversion is calculated before this fixed card profile
        # is applied by the generic job converter. Rebuild that layer from the
        # standardized all-L card set so mixed physical/magical jobs are not
        # scored from stale card totals.
        source_build = getattr(self, "_extreme_clean_base_build", self.build)
        source_profile = self._extreme_profile_for_build(source_build)
        target_profile = self._extreme_profile_for_job(real_job)
        build["extra_buff_attrs"] = self._extreme_card_conversion_delta(
            build,
            source_profile,
            target_profile,
        )
        # Do not inherit another job's talismans (or empty defaults) from
        # 配装1. Score equipment and both axes share this same baseline.
        from ..live_dps.score_equipment_defaults import apply_standard_score_offense

        apply_standard_score_offense(self.store, build, target_profile)
        self._extreme_fill_empty_glyph_a_crit_if_needed(build)
        self._extreme_apply_auto_skill_glyphs(
            build,
            job,
            count=4,
            fill_to_count=False,
            excluded_glyphs=self._extreme_score_excluded_glyphs(job),
            forced_glyphs=self._extreme_score_forced_glyphs(job),
        )
        build["_score_standard_profile"] = {
            "equipment": "70L默认装备与首饰",
            "cards": "全L",
            "card_bonus_level": 25,
            "crit_damage_card_bonus_level": 0,
            "talismans": "B护符；主属性/攻击通道匹配，合法攻击额外属性",
            "glyph_s_extra": "自动主攻；禁止攻击额外属性时使用主属性",
            "skill_build": "内置默认加点",
            "buffs": "一键选择buff；理论轴施放/实战UDP同名效果优先",
        }
        return build

    def _extreme_score_excluded_glyphs(
        self,
        job: str,
    ) -> set[tuple[str, str]]:
        """Return glyph choices whose quick estimate is invalid for score axes."""

        real_job = self._team_real_job(job)
        from ..skill_glyph_policy import score_glyph_exclusions

        return score_glyph_exclusions(real_job)

    def _extreme_score_forced_glyphs(
        self,
        job: str,
    ) -> list[tuple[str, str]]:
        """Return score glyphs whose full-axis gain beats the quick estimate."""

        if norm(job) == "打坐曜":
            # Sitting Yao spends the rotation maintaining Chakra Cure instead
            # of filling idle time with damage skills. Value its glyph slots by
            # healing/mitigation utility: faster meditation entry, more frequent
            # emergency healing, higher Chakra Ring mitigation coverage and more
            # frequent persistent healing. Assault already has effectively full
            # uptime without its duration glyph, so it provides no extra value.
            return [
                ("查克拉治愈术", "动作速度增加"),
                ("能量之轮", "冷却缩减"),
                ("查克拉圆环", "冷却缩减"),
                ("奇迹之轮", "冷却缩减"),
            ]
        if self._team_real_job(job) == "银色猎人":
            return [("螺旋飓风", "冷却缩减")]
        return []

    def _extreme_apply_auto_skill_glyphs(
        self,
        build: dict[str, Any],
        job: str,
        *,
        count: int = 4,
        support_team_size: int = 1,
        team_objective_profile: dict[str, Any] | None = None,
        fill_to_count: bool = True,
        excluded_glyphs: set[tuple[str, str]] | None = None,
        forced_glyphs: list[tuple[str, str]] | None = None,
        candidate_glyphs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply the same output-based skill-glyph policy to one converted job."""

        real_job = self._team_real_job(job)
        target_profile = self._extreme_profile_for_job(real_job)
        build["skill_glyphs"] = {
            "second_job": real_job,
            "count": max(1, min(7, int(as_number(count, 4)))),
            "rows": [],
            "auto_selected": True,
        }
        own_effects = self._team_effects_for_jobs([job])
        proxy = _ExtremeTimelineProxy(self, build, own_effects)
        proxy._skill_burst_team_objective_profile = (
            copy.deepcopy(team_objective_profile)
            if isinstance(team_objective_profile, dict)
            else None
        )
        proxy._skill_burst_team_objective_effect_windows = []
        _rows, summary = self.calculator.calculate(build)
        summary = proxy._team_apply_current_mixed_element_panel(dict(summary), {}, build)
        proxy._skill_burst_base_summary = dict(summary)
        all_metrics = proxy._skill_burst_all_learned_metrics(summary, [])
        selected_metrics = self._extreme_timeline_seed_metrics(
            proxy,
            job,
            build,
            all_metrics,
            prefer_saved_skill_sim=False,
            include_fillers=True,
        )
        contribution: dict[str, float] = {}
        skill_timing_context: dict[str, dict[str, float]] = {}
        used_skills: set[str] = set()
        window = 125.0
        for metric in selected_metrics:
            skill_key = proxy._skill_burst_skill_key(metric.get("skill"))
            if not skill_key:
                continue
            used_skills.add(skill_key)
            score = proxy._skill_burst_candidate_metric_score(metric)
            cooldown = max(
                0.01,
                as_number(metric.get("base_cooldown")),
                as_number(metric.get("cast_time")),
            )
            casts = max(1.0, window / cooldown)
            contribution[skill_key] = contribution.get(skill_key, 0.0) + (
                max(0.0, score) * casts
            )
            current_timing = skill_timing_context.setdefault(
                skill_key,
                {
                    "window": window,
                    "cooldown": cooldown,
                    "cast_time": 0.0,
                    "casts": 0.0,
                },
            )
            current_timing["cooldown"] = min(
                max(0.01, as_number(current_timing.get("cooldown"), cooldown)),
                cooldown,
            )
            current_timing["cast_time"] = max(
                as_number(current_timing.get("cast_time")),
                max(0.0, as_number(metric.get("cast_time"))),
            )
            current_timing["casts"] = max(
                as_number(current_timing.get("casts")), casts
            )
        metrics_by_skill = {
            proxy._skill_burst_skill_key(metric.get("skill")): metric
            for metric in all_metrics
            if proxy._skill_burst_skill_key(metric.get("skill"))
        }
        support_actions = proxy._skill_burst_support_actions(metrics_by_skill)
        duration_context: dict[str, list[dict[str, float]]] = {}

        def objective_gain_ratio(action: dict[str, Any]) -> float:
            attrs = {
                norm(key): as_number(value)
                for key, value in action.get("team_objective_attrs", {}).items()
                if norm(key) and abs(as_number(value)) > 1e-9
            }
            if not attrs:
                return 0.0
            profile = proxy._skill_burst_team_objective_profile
            captured_gain = captured_debuff_gain_ratio(proxy, action, profile)
            if captured_gain is not None:
                return captured_gain
            if isinstance(profile, dict) and "glyph_damage_context" in profile and "members" not in profile:
                # The native fallback supplies only the new debuff reference.
                # Preserve its existing non-debuff/timing-plate policy.
                profile = None
            if set(attrs).issubset(DEBUFF_KEYS) and not isinstance(profile, dict):
                # Without captured team damage, only the owner's known skill
                # channels are evidence. Never treat a single-element resist
                # reduction as applying to every member of an eight-player team.
                total = sum(contribution.values())
                gain = 0.0
                for key, output in contribution.items():
                    metric = metrics_by_skill.get(key, {})
                    member = {"element": norm(metric.get("element")),
                              "physical_share": 1.0, "magic_share": 0.0}
                    base = proxy._skill_burst_team_objective_member_multiplier({}, member, {})
                    buffed = proxy._skill_burst_team_objective_member_multiplier(attrs, member, {})
                    gain += output * max(0.0, buffed / max(1e-9, base) - 1.0)
                return gain / total if total > 1e-9 else 0.0
            if isinstance(profile, dict):
                teammate_gain = max(
                    0.0,
                    as_number(proxy._skill_burst_team_objective_gain_ratio(action, 0.0)),
                )
                attack_attrs = [
                    norm(value)
                    for value in target_profile.get("attack_attrs", ())
                    if norm(value) in {"物攻", "魔攻"}
                ]
                if not attack_attrs:
                    attack = norm(target_profile.get("attack_attr"))
                    attack_attrs = [attack if attack in {"物攻", "魔攻"} else "物攻"]
                owner_member = {
                    "physical_share": attack_attrs.count("物攻") / len(attack_attrs),
                    "magic_share": attack_attrs.count("魔攻") / len(attack_attrs),
                    "main_stat": norm(target_profile.get("main_stat")),
                    "element": norm(target_profile.get("element")),
                }
                before = proxy._skill_burst_team_objective_member_multiplier(
                    {}, owner_member, profile
                )
                after = proxy._skill_burst_team_objective_member_multiplier(
                    attrs, owner_member, profile
                )
                owner_gain = (
                    max(0.0, after / before - 1.0)
                    if before > 1e-9 and after > before
                    else 0.0
                )
                return owner_gain + teammate_gain
            return approximate_team_objective_gain_ratio(attrs) * max(
                1, int(as_number(support_team_size, 1))
            )

        for action in support_actions:
            skill_key = proxy._skill_burst_skill_key(action.get("skill"))
            if skill_key:
                used_skills.add(skill_key)
            duration = max(0.0, as_number(action.get("duration")))
            cooldown_reduction = max(
                0.0,
                as_number(action.get("attrs", {}).get("冷却缩减%")),
            )
            active_multiplier = max(
                1.0, as_number(action.get("active_multiplier"), 1.0)
            )
            objective_gain = objective_gain_ratio(action)
            if (
                skill_key
                and duration > 0
                and (
                    active_multiplier > 1.0 + 1e-9
                    or cooldown_reduction > 1e-9
                    or objective_gain > 1e-9
                )
            ):
                duration_context.setdefault(skill_key, []).append(
                    {
                        "duration": duration,
                        # One application is not the whole coverage interval:
                        # summons/later hits refresh the effect before its final
                        # expiry. Reuse the shared timeline rules, not a glyph-
                        # specific list of skills to exclude.
                        "refresh_tail": (
                            max(
                                0.0,
                                effective_team_buff_duration(
                                    action.get("skill", ""), duration
                                ) - duration,
                            )
                            + team_effect_refresh_tail_for_attrs(
                                action.get("skill", ""), action.get("attrs", {})
                            )
                        ),
                        "cooldown": max(
                            duration, as_number(action.get("base_cooldown"), duration)
                        ),
                        "active_multiplier": active_multiplier,
                        "cooldown_reduction": cooldown_reduction,
                        "team_objective_gain_ratio": objective_gain,
                        # A measured zero (wrong element or fully overlapped)
                        # must not fall back to active_multiplier * party size.
                        "team_objective_gain_known": bool(
                            action.get("team_objective_attrs")
                            and set(action["team_objective_attrs"]).issubset(DEBUFF_KEYS)
                        ),
                        "team_multiplier": (
                            max(1, int(as_number(support_team_size, 1)))
                            if bool(action.get("team_buff"))
                            else 1
                        ),
                    }
                )
            if skill_key:
                metric = action.get("metric") if isinstance(action.get("metric"), dict) else {}
                action_cooldown = max(
                    0.01,
                    as_number(metric.get("base_cooldown")),
                    as_number(action.get("base_cooldown")),
                    duration,
                )
                timing = skill_timing_context.setdefault(
                    skill_key,
                    {
                        "window": window,
                        "cooldown": action_cooldown,
                        "cast_time": 0.0,
                        "casts": max(1.0, window / action_cooldown),
                    },
                )
                timing["cast_time"] = max(
                    as_number(timing.get("cast_time")),
                    max(0.0, as_number(metric.get("cast_time"))),
                )
        selected_rows = proxy._auto_skill_glyph_rows(
            count,
            contribution,
            used_skills,
            as_number(summary.get("final_percent")),
            first_job=target_profile.get("first_job"),
            second_job=real_job,
            fill_to_count=fill_to_count,
            duration_context=duration_context,
            skill_timing_context=skill_timing_context,
            excluded_glyphs=excluded_glyphs,
            forced_glyphs=forced_glyphs,
            candidate_glyphs=candidate_glyphs,
        )
        build["skill_glyphs"]["rows"] = copy.deepcopy(selected_rows)
        return selected_rows

    def _extreme_timeline_provider(self, job: str, effects: list[dict[str, Any]]) -> str:
        requested_job = norm(job)
        exact_variant = [
            norm(effect.get("provider"))
            for effect in effects
            if re.sub(r"^队员\d+\s*", "", norm(effect.get("provider"))).strip()
            == requested_job
        ]
        if exact_variant:
            return exact_variant[0]
        exact = [
            norm(effect.get("provider"))
            for effect in effects
            if norm(self._team_provider_job(effect.get("provider", ""))) == requested_job
        ]
        if exact:
            return exact[0]
        real_job = self._team_real_job(job)
        if norm(job) != norm(real_job):
            # Variants such as sitting/output Yao share the same real job, but
            # their rotation state must not borrow another variant's provider.
            return f"队员0 {norm(job)}"
        fallback = [
            norm(effect.get("provider"))
            for effect in effects
            if self._team_real_job(self._team_provider_job(effect.get("provider", ""))) == real_job
        ]
        return fallback[0] if fallback else f"队员0 {job}"

    def _extreme_timeline_duplicate_owned_nonstacking_skills(
        self,
        effects: list[dict[str, Any]],
        provider: str,
    ) -> set[str]:
        """Skills whose teammate copies must not replace this provider's cast."""

        provider_key = norm(provider)
        own_nonstacking_skills = {
            self._team_effect_skill_name(effect.get("skill"))
            for effect in effects
            if norm(effect.get("provider")) == provider_key
            and not self._team_practical_effect_is_stackable(effect)
        }
        return {
            self._team_effect_skill_name(effect.get("skill"))
            for effect in effects
            if norm(effect.get("provider")) != provider_key
            and self._team_effect_skill_name(effect.get("skill"))
            in own_nonstacking_skills
            and not self._team_practical_effect_is_stackable(effect)
        }

    def _extreme_timeline_relevant_external_effects(
        self,
        proxy: _ExtremeTimelineProxy,
        effects: list[dict[str, Any]],
        provider: str,
        metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        relevant_resists = proxy._skill_burst_relevant_resist_attrs(metrics)
        elemental_resists = {"减火抗%", "减水抗%", "减光抗%", "减暗抗%"}
        elemental_attack_resist = {
            "火攻%": "减火抗%",
            "水攻%": "减水抗%",
            "光攻%": "减光抗%",
            "暗攻%": "减暗抗%",
        }
        attack_attr = norm(proxy._job_profile().get("attack_attr"))
        irrelevant_amp_attrs = (
            {"魔法增伤%", "魔法伤害增加%"}
            if attack_attr == "物攻"
            else {"物理增伤%", "物理伤害增加%"}
            if attack_attr == "魔攻"
            else set()
        )
        # A same-name, non-stackable status supplied by another party member
        # must not make the current profession believe its own maintenance
        # skill is unnecessary.  This is especially important for duplicate
        # professions (for example two Snipers and Aim): each personal theory
        # axis still casts its own skill, while the counterfactual allocator
        # later shares the real non-stacking team benefit between providers.
        # Derive the owned set before global effect de-duplication because the
        # de-dup winner may belong to the other provider.
        duplicated_owned_skills = (
            self._extreme_timeline_duplicate_owned_nonstacking_skills(
                effects, provider
            )
        )
        prepared: list[dict[str, Any]] = []
        for effect in self._dedup_team_effects(effects):
            own_effect = norm(effect.get("provider")) == norm(provider)
            skill = self._team_effect_skill_name(effect.get("skill"))
            if not own_effect and skill in duplicated_owned_skills:
                continue
            if own_effect and "打坐曜" in norm(provider) and skill == "查克拉治愈术":
                # Sitting is a personal state rather than an always-on external
                # aura. Its own timeline turns it off during every cast and resumes
                # it afterwards, which is also when summons may receive a refresh.
                continue
            # The job's ordinary debuffs and non-continuous CDR buffs are cast by
            # its own rotation. Teammates receive those effects from the synced
            # cast schedule; keeping an extra external copy on the provider would
            # apply the same Spirit Boost/Time Acceleration twice.
            if (
                own_effect
                and (
                    is_acceleration_resistance_skill(skill)
                    or not self._team_practical_is_continuous_effect(effect)
                )
            ):
                continue
            copied = copy.deepcopy(effect)
            attrs = {
                norm(attr): value
                for attr, value in copied.get("attrs", {}).items()
                if norm(attr) not in irrelevant_amp_attrs
            }
            for attr in list(attrs):
                if attr in elemental_resists and attr not in relevant_resists:
                    attrs.pop(attr, None)
                elif attr in elemental_attack_resist and elemental_attack_resist[attr] not in relevant_resists:
                    attrs.pop(attr, None)
            if not attrs:
                continue
            copied["attrs"] = attrs
            copied["display_attrs"] = dict(attrs)
            prepared.append(copied)
        return prepared

    def _extreme_timeline_cast_schedule(
        self,
        timelines: list[dict[str, Any]],
    ) -> dict[tuple[str, str], list[tuple[float, float]]]:
        """Collect each provider's real support/debuff casts from the first-pass axes."""
        schedule: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for result in timelines:
            provider = norm(result.get("provider"))
            if not provider:
                continue
            for segment in result.get("segments", []):
                if segment.get("timeline_hidden"):
                    continue
                skill = self._skill_burst_skill_key(segment.get("skill"))
                if not skill:
                    continue
                start = max(0.0, as_number(segment.get("start")))
                end = max(start, as_number(segment.get("end")))
                schedule.setdefault((provider, skill), []).append((start, end))
        return schedule

    def _extreme_timeline_seed_metrics(
        self,
        proxy: _ExtremeTimelineProxy,
        job: str,
        build: dict[str, Any],
        all_metrics: list[dict[str, Any]],
        *,
        prefer_saved_skill_sim: bool = True,
        include_fillers: bool = False,
    ) -> list[dict[str, Any]]:
        """Build the damage pool used by an extreme-team practical timeline."""
        real_job = self._team_real_job(job)
        current_job = self._team_real_job(norm(self.build.get("second_job")))
        requested_names: list[str] = []
        if prefer_saved_skill_sim and real_job == current_job:
            saved_rows = build.get("skill_sim", [])
            if isinstance(saved_rows, dict):
                saved_rows = [saved_rows]
            if isinstance(saved_rows, list):
                requested_names.extend(
                    norm(row.get("skill"))
                    for row in saved_rows
                    if isinstance(row, dict) and norm(row.get("skill")) not in {"", "无"}
                )
        if requested_names:
            requested_keys = {
                proxy._skill_burst_skill_key(skill) for skill in requested_names
            }
            for skill in sorted(SKILL_SIM_AUTO_FORCE_INCLUDE_BY_JOB.get(real_job, set())):
                if proxy._skill_burst_skill_key(skill) not in requested_keys:
                    requested_names.append(skill)
                    requested_keys.add(proxy._skill_burst_skill_key(skill))
        if norm(job) == "打坐曜":
            allowed = {"日光焰", "暗影召唤术", "阿兹那之约", "圆环强击"}
            return [
                metric
                for metric in all_metrics
                if proxy._skill_burst_skill_key(metric.get("skill")) in allowed
            ]
        if include_fillers:
            # 极限配队使用完整的已学习伤害池。低DPS技能不会抢占主力技能，
            # 但可以在主循环全部转冷却时填补原本不合理的空白。
            eligible = proxy._skill_burst_mark_timeline_fillers([
                copy.deepcopy(metric)
                for metric in all_metrics
                if proxy._skill_burst_candidate_metric_dpa(metric) > 0
                and as_number(metric.get("effective_score")) > 0
            ], real_job)
            return sorted(
                eligible,
                key=lambda metric: (
                    proxy._skill_burst_candidate_metric_dpa(metric),
                    proxy._skill_burst_candidate_metric_dps(metric),
                ),
                reverse=True,
            )
        if not requested_names:
            return proxy._skill_burst_candidate_pool(all_metrics)

        metrics_by_skill = {
            proxy._skill_burst_skill_key(metric.get("skill")): metric
            for metric in all_metrics
            if proxy._skill_burst_skill_key(metric.get("skill"))
        }
        selected: list[dict[str, Any]] = []
        selected_keys: set[str] = set()
        for skill in requested_names:
            skill_key = proxy._skill_burst_skill_key(skill)
            metric = metrics_by_skill.get(skill_key)
            if metric is None or skill_key in selected_keys:
                continue
            selected.append(metric)
            selected_keys.add(skill_key)
        return selected or proxy._skill_burst_candidate_pool(all_metrics)

    def _extreme_manual_optimization_build(self, job: str) -> dict[str, Any]:
        """Create the exact converted build used by the extreme timeline for editing."""
        build = self._extreme_timeline_build_for_job(job)
        self._remove_timeline_owned_build_buffs(build, job)
        proxy = _ExtremeTimelineProxy(self, build, [])
        _rows, summary = self.calculator.calculate(build)
        summary = proxy._team_apply_current_mixed_element_panel(dict(summary), {}, build)
        all_metrics = proxy._skill_burst_all_learned_metrics(summary, [])
        selected_metrics = self._extreme_timeline_seed_metrics(proxy, job, build, all_metrics)
        build["skill_sim"] = [
            {
                "skill": norm(metric.get("skill")) or "无",
                "level": norm(metric.get("level")) or "无",
                "cooldown": max(0.0, as_number(metric.get("base_cooldown"))),
                "multiplier": self.store.skill_damage_multiplier(
                    self._team_real_job(job),
                    norm(metric.get("skill")),
                    norm(metric.get("level")),
                ),
            }
            for metric in selected_metrics[:SKILL_SIM_ROWS]
        ]
        while len(build["skill_sim"]) < SKILL_SIM_ROWS:
            build["skill_sim"].append({"skill": "无", "level": "无", "cooldown": 0.0, "multiplier": 0.0})
        settings = copy.deepcopy(build.get("skill_sim_settings", {})) if isinstance(build.get("skill_sim_settings"), dict) else {}
        settings["burst_manual_job"] = self._team_real_job(job)
        settings["burst_manual_sequence"] = None
        build["skill_sim_settings"] = settings
        self._remove_timeline_owned_build_buffs(build, job)
        return build

    def _extreme_team_timeline_result(
        self,
        job: str,
        team: list[str] | tuple[str, ...],
        effects: list[dict[str, Any]],
        hand_multiplier: float = 1.0,
        window: float = SKILL_BURST_PRACTICAL_WINDOW,
        cast_schedule: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
        include_display_windows: bool = True,
        build_override: dict[str, Any] | None = None,
        blocked_intervals: list[dict[str, Any]] | list[tuple[float, float]] | None = None,
        partial_cast_schedule: bool = False,
        fast_long_timeline: bool = False,
        long_sim_planner: bool = False,
        score_refinement: bool = True,
        same_environment_theory: bool = False,
        provider_override: str = "",
        score_actual_casts_by_source: dict[str, list[dict[str, Any]]] | None = None,
        team_objective_profile: dict[str, Any] | None | object = _DEFAULT_TEAM_OBJECTIVE,
        boss_hp_segments: tuple[tuple[tuple[float, float], ...], ...] | None = None,
        player_buff_clear_times: tuple[float, ...] | list[float] | None = None,
        summon_removal_schedule: tuple[tuple[float, str], ...] | list[tuple[float, str]] | None = None,
        excluded_skill_keys: set[str] | tuple[str, ...] | None = None,
        timing_summary_only: bool = False,
    ) -> dict[str, Any]:
        # Single-axis calls and batch/worker calls must use the same objective.
        # Explicit None remains a personal-only diagnostic/ablation choice.
        if team_objective_profile is _DEFAULT_TEAM_OBJECTIVE:
            team_objective_profile = self._extreme_default_team_objective_profile(job, team)
        defaults = FAST_MECHANISM_UNIFIED_POLICY
        effective_score_refinement = (
            bool(score_refinement)
            or os.getenv("DRAGON_NEST_SCORE_FORCE_REFINEMENT") == "1"
        ) and os.getenv("DRAGON_NEST_SCORE_SKIP_REFINEMENT") != "1"
        fast_policy = {
            "profile": tuple(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_profile",
                    defaults["profile"],
                )
            ),
            "ambiguity_ratio": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_ambiguity_ratio",
                    defaults["ambiguity_ratio"],
                )
            ),
            "override_ratio": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_override_ratio",
                    defaults["override_ratio"],
                )
            ),
            "ready_debt_weight": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_ready_debt_weight",
                    defaults["ready_debt_weight"],
                )
            ),
            "ready_debt_dpa_floor": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_ready_debt_dpa_floor",
                    defaults["ready_debt_dpa_floor"],
                )
            ),
            "ready_debt_cycle_floor": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_ready_debt_cycle_floor",
                    defaults["ready_debt_cycle_floor"],
                )
            ),
            "support_horizon": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_support_horizon",
                    defaults["support_horizon"],
                )
            ),
            "state_support_horizon": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_state_support_horizon",
                    defaults["state_support_horizon"],
                )
            ),
            "support_candidate_limit": int(
                as_number(
                    getattr(
                        self,
                        "_skill_burst_fast_mechanism_support_candidate_limit",
                        defaults["support_candidate_limit"],
                    )
                )
            ),
            "displacement_weight": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_displacement_weight",
                    defaults["displacement_weight"],
                )
            ),
            "short_displacement_weight": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_short_displacement_weight",
                    defaults["short_displacement_weight"],
                )
            ),
            "pair_planning": bool(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_pair_planning",
                    defaults["pair_planning"],
                )
            ),
            "boundary_urgency_weight": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_boundary_urgency_weight",
                    defaults["boundary_urgency_weight"],
                )
            ),
            "boundary_urgency_span": as_number(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_boundary_urgency_span",
                    defaults["boundary_urgency_span"],
                )
            ),
        }
        cache = getattr(self, "_extreme_timeline_result_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._extreme_timeline_result_cache = cache
        accel_start_widget = getattr(self, "skill_long_accel_start", None)
        first_accel_start = (
            max(0.0, as_number(accel_start_widget.value()))
            if callable(getattr(accel_start_widget, "value", None))
            else 60.0
        )
        effect_signature = tuple(
            sorted(
                (self._team_practical_cache_value(effect) for effect in effects),
                key=repr,
            )
        )
        cast_schedule_signature = tuple(
            sorted(
                (
                    norm(provider),
                    self._skill_burst_skill_key(skill),
                    tuple((round(as_number(start), 4), round(as_number(end), 4)) for start, end in casts),
                )
                for (provider, skill), casts in (cast_schedule or {}).items()
            )
        )
        override_signature = None
        if isinstance(build_override, dict):
            override_cache_value = {
                key: value
                for key, value in build_override.items()
                if key != "team_sim"
            }
            override_signature = self._team_practical_cache_value(override_cache_value)
        blocked_signature = tuple(
            (
                round(as_number(row.get("start")), 3),
                round(as_number(row.get("end")), 3),
            )
            if isinstance(row, dict)
            else (round(as_number(row[0]), 3), round(as_number(row[1]), 3))
            for row in (blocked_intervals or [])
            if isinstance(row, dict) or (isinstance(row, (list, tuple)) and len(row) >= 2)
        )
        score_actual_cast_signature = tuple(
            sorted(
                (
                    str(source_id),
                    tuple(
                        (
                            self._skill_burst_skill_key(row.get("skill")),
                            round(as_number(row.get("start")), 4),
                            round(as_number(row.get("end")), 4),
                            round(as_number(row.get("action_speed_percent")), 2),
                            row.get("_score_buff_start"),
                            row.get("_score_buff_end"),
                            round(
                                as_number(row.get("_score_carry_in_local_start")),
                                4,
                            ),
                            round(
                                as_number(row.get("_score_carry_in_local_end")),
                                4,
                            ),
                        )
                        for row in cast_rows
                        if isinstance(row, dict)
                    ),
                )
                for source_id, cast_rows in (score_actual_casts_by_source or {}).items()
                if isinstance(cast_rows, (list, tuple))
            )
        )
        team_objective_signature = self._team_practical_cache_value(
            team_objective_profile
        ) if isinstance(team_objective_profile, dict) else None
        boss_hp_signature = (
            tuple(
                tuple(
                    (round(as_number(second), 3), round(as_number(hp), 4))
                    for second, hp in segment
                )
                for segment in boss_hp_segments
            )
            if boss_hp_segments is not None
            else None
        )
        player_buff_clear_signature = tuple(
            sorted({
                round(max(0.0, as_number(value)), 6)
                for value in (player_buff_clear_times or ())
            })
        )
        summon_removal_signature = tuple(
            sorted(
                (
                    round(max(0.0, as_number(row[0])), 6),
                    self._skill_burst_skill_key(row[1]),
                )
                for row in (summon_removal_schedule or ())
                if isinstance(row, (list, tuple))
                and len(row) >= 2
                and self._skill_burst_skill_key(row[1])
            )
        )
        excluded_skill_signature = tuple(
            sorted(
                self._skill_burst_skill_key(value)
                for value in (excluded_skill_keys or ())
                if self._skill_burst_skill_key(value)
            )
        )
        cache_key = (
            norm(job),
            tuple(norm(member) for member in team),
            effect_signature,
            round(max(0.0, as_number(hand_multiplier, 1.0)), 4),
            round(max(30.0, as_number(window, SKILL_BURST_PRACTICAL_WINDOW)), 2),
            round(first_accel_start, 2),
            cast_schedule_signature,
            bool(include_display_windows),
            bool(timing_summary_only),
            override_signature,
            blocked_signature,
            bool(partial_cast_schedule),
            bool(fast_long_timeline),
            bool(long_sim_planner),
            bool(effective_score_refinement),
            bool(same_environment_theory),
            norm(provider_override),
            score_actual_cast_signature,
            team_objective_signature,
            boss_hp_signature,
            player_buff_clear_signature,
            summon_removal_signature,
            excluded_skill_signature,
            "score-replay-summon-removal-v6-thunder-policy-v2",
            "fury-key-nodes-preparation-v1",
            "long-sim-policy-v4" if long_sim_planner else "extreme-policy-v1",
            (
                bool(getattr(self, "_skill_burst_fast_mechanism_use_legacy_diagnostic", False)),
                self._team_practical_cache_value(fast_policy),
                bool(getattr(self, "_skill_burst_fast_mechanism_refinement", False)),
                as_number(getattr(self, "_skill_burst_fast_mechanism_refinement_seconds", 0.12)),
                int(as_number(getattr(self, "_skill_burst_fast_mechanism_refinement_evaluations", 4))),
                as_number(getattr(self, "_skill_burst_fast_mechanism_cycle_deadline_weight", 0.0)),
                as_number(getattr(self, "_skill_burst_fast_mechanism_cycle_deadline_delay", 2.0)),
                bool(getattr(self, "_skill_burst_fast_mechanism_opening_setup", False)),
            )
            if fast_long_timeline
            else (),
        )
        if cache_key in cache:
            return copy.deepcopy(cache[cache_key])

        seed_cache = getattr(self, "_extreme_timeline_seed_cache", None)
        if not isinstance(seed_cache, dict):
            seed_cache = {}
            self._extreme_timeline_seed_cache = seed_cache
        seed_key = norm(job) if override_signature is None else (norm(job), override_signature)
        cached_seed = seed_cache.get(seed_key)
        if isinstance(cached_seed, dict):
            build = copy.deepcopy(cached_seed["build"])
            summary = dict(cached_seed["summary"])
            all_metrics = copy.deepcopy(cached_seed["all_metrics"])
            selected_metrics = copy.deepcopy(cached_seed["selected_metrics"])
        else:
            build = (
                copy.deepcopy(build_override)
                if isinstance(build_override, dict)
                else self._extreme_timeline_build_for_job(job)
            )
            if isinstance(build_override, dict):
                real_job = self._team_real_job(job)
                target_profile = self._extreme_profile_for_job(real_job)
                build["first_job"] = target_profile.get("first_job")
                build["second_job"] = target_profile.get("second_job")
                build["effective_second_job"] = target_profile.get("second_job")
            self._remove_timeline_owned_build_buffs(build, job)
            seed_proxy = _ExtremeTimelineProxy(self, build, [])
            _rows, summary = self.calculator.calculate(build)
            summary = seed_proxy._team_apply_current_mixed_element_panel(dict(summary), {}, build)
            all_metrics = seed_proxy._skill_burst_all_learned_metrics(summary, [])
            selected_metrics = self._extreme_timeline_seed_metrics(
                seed_proxy,
                job,
                build,
                all_metrics,
                prefer_saved_skill_sim=False,
                include_fillers=True,
            )
            _release_timing_proxy_method_cycles(seed_proxy)
            seed_cache[seed_key] = {
                "build": copy.deepcopy(build),
                "summary": dict(summary),
                "all_metrics": copy.deepcopy(all_metrics),
                "selected_metrics": copy.deepcopy(selected_metrics),
            }

        proxy = _ExtremeTimelineProxy(self, build, effects)
        # B/H comparison axes need full independent simulation, but never
        # expose the display-only Buff lanes. Mechanical windows remain intact.
        proxy._skill_burst_skip_display_effect_windows = bool(timing_summary_only)
        proxy._team_summary_effect_cache_active = True
        proxy._team_summary_static_build_signature = self.calculator._static_build_signature(
            proxy.build
        )
        panel_cache_pool = getattr(self, "_extreme_timeline_panel_cache_pool", None)
        if not isinstance(panel_cache_pool, dict):
            panel_cache_pool = {}
            self._extreme_timeline_panel_cache_pool = panel_cache_pool
        panel_cache_namespace = panel_cache_pool.setdefault(
            (norm(job), override_signature),
            {},
        )
        # Retain only the current job's reusable pure panel calculations. A
        # long eight-player score otherwise retains every job's large keys,
        # starving the process pool of memory and forcing subsequent phases
        # into serial execution. Cache eviction cannot alter a computed value.
        for old_key in tuple(panel_cache_pool):
            if panel_cache_pool[old_key] is not panel_cache_namespace:
                del panel_cache_pool[old_key]
        for cache_name in (
            "_team_practical_panel_cache",
            "_team_practical_attack_panel_cache",
            "_team_practical_raw_attack_cache",
            "_team_summary_effect_cache",
            "_skill_burst_team_panel_bundle_cache",
            "_skill_burst_merged_team_attrs_cache",
            "_skill_burst_periodic_attr_state_cache",
            "_skill_burst_active_effect_attrs_cache",
        ):
            shared_cache = panel_cache_namespace.setdefault(cache_name, {})
            setattr(proxy, cache_name, shared_cache)
        # The merged-attrs cache is keyed by compact integer effect tokens.
        # Its token registry must have the same lifetime as the shared cache:
        # independent proxies otherwise both assign token 1 to different
        # effects and can silently reuse another axis's panel result.
        proxy._skill_burst_merged_effect_token_registry = (
            panel_cache_namespace.setdefault(
                "_skill_burst_merged_effect_token_registry", {}
            )
        )
        # Team-score theory axes can keep their Boss windows and pinned team
        # schedules while using the exact planning policy of the personal
        # long-duration simulation. Other extreme/custom axes retain their
        # established complete-rotation policy.
        proxy._skill_burst_complete_default_rotation = not long_sim_planner
        stable_external_jobs = {"剑皇"}
        proxy._skill_burst_stable_external_rotation = (
            not long_sim_planner or norm(job) in stable_external_jobs
        )
        proxy._skill_burst_same_environment_theory = bool(
            same_environment_theory
        )
        proxy._skill_burst_boss_hp_segments = boss_hp_signature
        proxy._skill_burst_player_buff_clear_times_override = (
            player_buff_clear_signature
        )
        proxy._skill_burst_summon_removal_schedule_override = (
            summon_removal_signature
        )
        # Keep the rotation variant as engine state. Score timelines normalize
        # providers back to the real job in several places, so the display label
        # is not a reliable way to decide whether idle time means meditation.
        proxy._skill_burst_sitting_yao = norm(job) == "打坐曜"
        proxy._skill_burst_excluded_skill_keys = set(excluded_skill_signature)
        # Diagnostic A/B switch for the team-score planner.  The normal
        # long-simulation policy remains unchanged unless the benchmark tool
        # explicitly requests the no-refinement variant.
        if long_sim_planner and not effective_score_refinement:
            proxy._skill_burst_disable_new_scheme_1_refinement = True
        # The result dialog may opt into throttled event pumping so a long
        # rotation keeps painting its progress dialog. The timeline policy and
        # all candidate scoring remain unchanged.
        proxy._skill_burst_background_process_events = bool(
            getattr(self, "_extreme_timeline_process_events", False)
        )
        proxy._skill_burst_background_progress_callback = getattr(
            self,
            "_extreme_timeline_progress_callback",
            None,
        )
        proxy._skill_burst_blocked_intervals = list(blocked_intervals or [])
        if fast_long_timeline:
            # Custom 180-720 second axes use the independent planner as well.
            # Its mechanism profile keeps a bounded causal look-ahead while
            # avoiding the duplicate multi-start/refinement work that is too
            # expensive across eight long timelines.
            proxy._skill_burst_force_new_scheme_1 = not bool(
                getattr(
                    self,
                    "_skill_burst_fast_mechanism_use_legacy_diagnostic",
                    False,
                )
            )
            proxy._skill_burst_fast_mechanism_policy = True
            proxy._skill_burst_fast_mechanism_profile = tuple(fast_policy["profile"])
            proxy._skill_burst_new_scheme_1_ambiguity_ratio = max(
                0.0,
                min(
                    1.0,
                    as_number(fast_policy["ambiguity_ratio"]),
                ),
            )
            proxy._skill_burst_new_scheme_1_override_ratio = max(
                1.0,
                as_number(fast_policy["override_ratio"]),
            )
            proxy._skill_burst_new_scheme_1_ready_debt_weight = max(
                0.0,
                as_number(fast_policy["ready_debt_weight"]),
            )
            proxy._skill_burst_new_scheme_1_ready_debt_dpa_floor = max(
                0.0,
                as_number(fast_policy["ready_debt_dpa_floor"]),
            )
            proxy._skill_burst_new_scheme_1_ready_debt_cycle_floor = max(
                0.0,
                as_number(fast_policy["ready_debt_cycle_floor"]),
            )
            proxy._skill_burst_enable_new_scheme_1_multistart = False
            proxy._skill_burst_new_scheme_1_opening_setup_enabled = False
            proxy._skill_burst_new_scheme_1_support_horizon = max(
                2.0,
                as_number(
                    fast_policy["support_horizon"],
                    10.0,
                ),
            )
            proxy._skill_burst_new_scheme_1_state_support_horizon = max(
                2.0,
                as_number(
                    fast_policy["state_support_horizon"],
                    12.0,
                ),
            )
            proxy._skill_burst_new_scheme_1_support_candidate_limit = max(
                2,
                int(
                    as_number(
                        fast_policy["support_candidate_limit"],
                        2,
                    )
                ),
            )
            proxy._skill_burst_new_scheme_1_displacement_weight = max(
                0.0,
                as_number(
                    fast_policy["displacement_weight"],
                    1.0,
                ),
            )
            proxy._skill_burst_new_scheme_1_short_cast_displacement_weight = max(
                0.0,
                as_number(
                    fast_policy["short_displacement_weight"],
                    0.75,
                ),
            )
            proxy._skill_burst_new_scheme_1_pair_planning = bool(fast_policy["pair_planning"])
            proxy._skill_burst_fast_mechanism_boundary_urgency_weight = max(
                0.0,
                as_number(
                    fast_policy["boundary_urgency_weight"],
                    0.35,
                ),
            )
            proxy._skill_burst_fast_mechanism_boundary_urgency_span = max(
                0.5,
                as_number(
                    fast_policy["boundary_urgency_span"],
                    3.0,
                ),
            )
            proxy._skill_burst_fast_mechanism_cycle_deadline_weight = max(
                0.0,
                as_number(
                    getattr(
                        self,
                        "_skill_burst_fast_mechanism_cycle_deadline_weight",
                        0.0,
                    )
                ),
            )
            proxy._skill_burst_fast_mechanism_cycle_deadline_delay = max(
                0.25,
                as_number(
                    getattr(
                        self,
                        "_skill_burst_fast_mechanism_cycle_deadline_delay",
                        2.0,
                    ),
                    2.0,
                ),
            )
            fast_refinement = bool(
                getattr(self, "_skill_burst_fast_mechanism_refinement", False)
            )
            proxy._skill_burst_fast_mechanism_refinement = fast_refinement
            proxy._skill_burst_disable_new_scheme_1_refinement = not fast_refinement
            if fast_refinement:
                proxy._skill_burst_new_scheme_1_refinement_passes = 1
                proxy._skill_burst_new_scheme_1_refinement_seconds = max(
                    0.0,
                    as_number(
                        getattr(
                            self,
                            "_skill_burst_fast_mechanism_refinement_seconds",
                            0.12,
                        ),
                        0.12,
                    ),
                )
                proxy._skill_burst_new_scheme_1_refinement_evaluations = max(
                    0,
                    int(
                        as_number(
                            getattr(
                                self,
                                "_skill_burst_fast_mechanism_refinement_evaluations",
                                4,
                            ),
                            4,
                        )
                    ),
                )
            proxy._skill_burst_disable_hybrid_optimizer = True
            proxy._skill_burst_disable_local_optimizer = True
            proxy._skill_burst_disable_conservative_1_6 = True
        acrobat_members = sorted(
            [
                norm(member)
                for member in team
                if self._team_real_job(member) in {"影舞者", "风行者"}
            ],
            key=self._extreme_member_sort_key,
        )
        acrobat_jobs = [self._team_real_job(member) for member in acrobat_members]
        current_real_job = self._team_real_job(job)
        support_initial_phases: dict[str, float] = {}
        current_provider = (
            norm(provider_override)
            or self._extreme_timeline_provider(job, effects)
        )
        intermittent_by_skill: dict[str, list[str]] = {}
        for effect in effects:
            skill_key = self._skill_burst_skill_key(self._team_effect_skill_name(effect.get("skill")))
            provider_key = norm(effect.get("provider"))
            cooldown = max(0.0, as_number(effect.get("cooldown")))
            duration = max(0.0, as_number(effect.get("duration")))
            if not skill_key or not provider_key or cooldown <= 0 or duration >= cooldown - 1e-6:
                continue
            providers = intermittent_by_skill.setdefault(skill_key, [])
            if provider_key not in providers:
                providers.append(provider_key)
        for skill_key, providers in intermittent_by_skill.items():
            providers.sort(key=self._extreme_provider_sort_key)
            if current_provider in providers and len(providers) > 1:
                support_initial_phases[skill_key] = providers.index(current_provider) / len(providers)
        if current_real_job in acrobat_jobs and acrobat_jobs.index(current_real_job) > 0:
            # Keep the established two-acrobat Spirit Burst offset explicit.
            support_initial_phases["精神爆发"] = 0.5
        if support_initial_phases:
            proxy._skill_burst_support_initial_phases = support_initial_phases
        if norm(job) == "打坐曜":
            proxy._skill_burst_allowed_skill_keys = {
                "突袭",
                "查克拉治愈术",
                "阿兹那之息",
                "日光焰",
                "暗影召唤术",
                "阿兹那之约",
                "圆环强击",
            }
        proxy._skill_burst_base_summary = dict(summary)
        provider = (
            norm(provider_override)
            or self._extreme_timeline_provider(job, effects)
        )
        external_effects = self._extreme_timeline_relevant_external_effects(
            proxy, effects, provider, selected_metrics
        )
        duplicate_buffs = proxy._team_duplicate_build_buff_names(external_effects, build)
        proxy._team_practical_duplicate_build_buffs = duplicate_buffs
        proxy._skill_burst_team_base_replacement_multiplier = (
            proxy._team_practical_build_buff_replacement_multiplier(duplicate_buffs)
        )
        proxy._skill_burst_current_provider_override = provider
        proxy._skill_burst_team_objective_profile = (
            copy.deepcopy(team_objective_profile)
            if isinstance(team_objective_profile, dict)
            else None
        )
        pinned_cdr_modes: dict[str, list[tuple[float, float]]] = {
            "burst": [],
            "accel": [],
        }
        scheduled_special_skills: set[str] = set()
        scheduled_own_special_skills: set[str] = set()
        scheduled_own_special_casts: dict[
            str, list[tuple[float, float]]
        ] = {}
        scheduled_special_providers: set[tuple[str, str]] = set()
        special_cdr_durations: dict[tuple[str, str], float] = {}
        fallback_special_cdr_durations: dict[str, float] = {}
        for effect in effects:
            effect_provider = norm(effect.get("provider"))
            effect_skill = self._skill_burst_skill_key(
                self._team_effect_skill_name(effect.get("skill"))
            )
            if not is_acceleration_resistance_skill(effect_skill):
                continue
            duration = max(0.0, as_number(effect.get("duration")))
            if duration <= 0:
                continue
            special_cdr_durations[(effect_provider, effect_skill)] = max(
                special_cdr_durations.get((effect_provider, effect_skill), 0.0),
                duration,
            )
            fallback_special_cdr_durations[effect_skill] = max(
                fallback_special_cdr_durations.get(effect_skill, 0.0),
                duration,
            )
        for (scheduled_provider, scheduled_skill), casts in (cast_schedule or {}).items():
            skill_key = self._skill_burst_skill_key(scheduled_skill)
            resistance_group = acceleration_resistance_group(skill_key)
            mode = (
                "burst"
                if resistance_group == BURST_RESISTANCE_GROUP
                else "accel"
                if resistance_group == TIME_ACCEL_RESISTANCE_GROUP
                else ""
            )
            if not mode:
                continue
            scheduled_provider_key = norm(scheduled_provider)
            scheduled_for_owner = bool(
                same_environment_theory
                and scheduled_provider_key == norm(provider)
            )
            scheduled_special_skills.add(skill_key)
            scheduled_special_providers.add((scheduled_provider_key, skill_key))
            if scheduled_for_owner:
                scheduled_own_special_skills.add(skill_key)
                scheduled_own_special_casts[skill_key] = [
                    (as_number(cast_start), as_number(cast_end))
                    for cast_start, cast_end in casts
                    if as_number(cast_end) > as_number(cast_start)
                ]
            duration = special_cdr_durations.get(
                (scheduled_provider_key, skill_key),
                fallback_special_cdr_durations.get(
                    skill_key,
                    SKILL_BURST_SIM_DURATIONS.get(skill_key, 0.0),
                ),
            )
            if not scheduled_for_owner:
                pinned_cdr_modes[mode].extend(
                    (
                        max(0.0, as_number(cast_end)),
                        min(
                            max(30.0, as_number(window, SKILL_BURST_PRACTICAL_WINDOW)),
                            max(0.0, as_number(cast_end)) + duration,
                        ),
                    )
                    for cast_start, cast_end in casts
                    if as_number(cast_end) > as_number(cast_start)
                    and duration > 0
                )
        for mode, intervals in pinned_cdr_modes.items():
            merged: list[list[float]] = []
            for start, end in sorted(intervals):
                if not merged or start > merged[-1][1] + 1e-6:
                    merged.append([start, end])
                else:
                    merged[-1][1] = max(merged[-1][1], end)
            pinned_cdr_modes[mode] = [
                (start, end) for start, end in merged
            ]
        proxy._skill_burst_schedule_override = {
            "window": max(30.0, as_number(window, SKILL_BURST_PRACTICAL_WINDOW)),
            "burst": pinned_cdr_modes["burst"],
            "accel": pinned_cdr_modes["accel"],
            "cocktail": False,
        }
        # This set controls the current character's own support action attrs.
        # In same-environment theory the owner keeps the real local attrs, so
        # CDR/action speed begins when its planned cast actually finishes. The
        # captured schedule only reserves when that cast should be attempted.
        proxy._skill_burst_pinned_special_skill_keys = (
            set() if same_environment_theory else set(scheduled_special_skills)
        )
        proxy._skill_burst_pinned_support_cast_schedule = (
            copy.deepcopy(scheduled_own_special_casts)
            if same_environment_theory
            else {}
        )
        if same_environment_theory and scheduled_own_special_skills:
            # Sequence-only post optimizers replay skill names but do not carry
            # captured wall-clock reservations. Running them here can move the
            # owner's Spirit Boost/Time Acceleration away from the fight and
            # leave the fixed numerical window behind. The primary long-sim
            # planner already evaluates the complete learned pool and honors
            # these hard reservations directly.
            proxy._skill_burst_disable_new_scheme_1_refinement = True
            proxy._skill_burst_disable_conservative_1_6 = True
            proxy._skill_burst_disable_local_optimizer = True
            proxy._skill_burst_disable_hybrid_optimizer = True
        first_cast_times: dict[Any, float] = {
            "精神爆发": 0.0,
            "时间加速": first_accel_start,
        }
        for staggered_skill, base_start in (
            ("精神爆发", 0.0),
            ("时间加速", first_accel_start),
        ):
            provider_effects: list[tuple[str, dict[str, Any]]] = []
            for effect in effects:
                if self._team_effect_skill_name(effect.get("skill")) != staggered_skill:
                    continue
                effect_provider = norm(effect.get("provider"))
                if effect_provider and all(effect_provider != row[0] for row in provider_effects):
                    provider_effects.append((effect_provider, effect))
            provider_effects.sort(key=lambda row: self._extreme_provider_sort_key(row[0]))
            if len(provider_effects) <= 1:
                continue
            centered_dual_accel = (
                staggered_skill == "时间加速"
                and len(provider_effects) == 2
                and max(30.0, as_number(window, SKILL_BURST_PRACTICAL_WINDOW)) <= 125.0 + 1e-6
                and base_start > 1e-6
            )
            centered_starts: list[float] = []
            if centered_dual_accel:
                horizon = max(30.0, as_number(window, SKILL_BURST_PRACTICAL_WINDOW))
                half_gap = min(30.0, horizon / 4.0)
                center = max(half_gap, min(horizon - half_gap, base_start))
                centered_starts = [center - half_gap, center + half_gap]
            for provider_index, (effect_provider, effect) in enumerate(provider_effects):
                cooldown = max(0.01, as_number(effect.get("cooldown")))
                if centered_starts:
                    first_cast_times[(effect_provider, staggered_skill)] = centered_starts[provider_index]
                else:
                    phase = cooldown * provider_index / len(provider_effects)
                    first_cast_times[(effect_provider, staggered_skill)] = (
                        base_start + phase
                    ) % cooldown
        for (scheduled_provider, scheduled_skill), casts in (cast_schedule or {}).items():
            if not casts:
                continue
            skill_key = self._skill_burst_skill_key(scheduled_skill)
            if not is_acceleration_resistance_skill(skill_key):
                continue
            first_cast_times[(norm(scheduled_provider), skill_key)] = max(
                0.0,
                min(as_number(start) for start, _end in casts),
            )
        if len(acrobat_members) > 1:
            for acrobat_index, member in enumerate(acrobat_members):
                member_provider = self._extreme_timeline_provider(member, effects)
                spirit_effect = next(
                    (
                        effect
                        for effect in effects
                        if norm(effect.get("provider")) == member_provider
                        and self._team_effect_skill_name(effect.get("skill")) == "精神爆发"
                    ),
                    None,
                )
                if spirit_effect:
                    cooldown = max(0.0, as_number(spirit_effect.get("cooldown")))
                    first_cast_times[(member_provider, "精神爆发")] = cooldown * acrobat_index / len(acrobat_members)
        # A present-but-empty schedule means the buff was not used in the
        # captured fight. Keep it pinned to empty instead of auto-scheduling a
        # theoretical cast at a more convenient time.
        pinned_special_skills = scheduled_special_skills
        special_timing_attrs = {
            "\u51b7\u5374\u7f29\u51cf%",
            "\u52a8\u4f5c\u901f\u5ea6%",
            "\u653b\u901f%",
        }
        timeline_external_effects: list[dict[str, Any]] = []
        for effect in external_effects:
            skill_key = self._skill_burst_skill_key(
                self._team_effect_skill_name(effect.get("skill"))
            )
            effect_provider_key = norm(effect.get("provider"))
            pinned_external_special = (
                (effect_provider_key, skill_key) in scheduled_special_providers
                if same_environment_theory
                else skill_key in pinned_special_skills
            )
            if not pinned_external_special:
                timeline_external_effects.append(effect)
                continue
            # The pinned mode intervals below are the single source of the
            # numerical CDR/action-speed effect. Keep a display-only external
            # window so every teammate still shows the provider's real cast.
            display_effect = copy.deepcopy(effect)
            raw_attrs = dict(display_effect.get("attrs", {}))
            display_effect.setdefault("display_attrs", copy.deepcopy(raw_attrs))
            display_effect["attrs"] = {
                key: value
                for key, value in raw_attrs.items()
                if norm(key) not in special_timing_attrs
            }
            timeline_external_effects.append(display_effect)
        proxy._skill_burst_external_team_effect_windows = self._team_practical_effect_windows(
            timeline_external_effects,
            window,
            include_special_cdr=True,
            first_cast_times=first_cast_times,
            cast_schedule=cast_schedule,
            strict_cast_schedule=not partial_cast_schedule,
        )
        if isinstance(team_objective_profile, dict):
            # Keep every teammate channel for the joint score. The ordinary
            # personal axis above intentionally removes physically/magically
            # irrelevant attrs, which is correct for the owner but not for the
            # other seven members receiving this provider's team effect.
            objective_duplicate_skills = (
                self._extreme_timeline_duplicate_owned_nonstacking_skills(
                    effects, provider
                )
            )
            objective_external_effects = [
                copy.deepcopy(effect)
                for effect in self._dedup_team_effects(effects)
                if norm(effect.get("provider")) != norm(provider)
                and self._team_effect_skill_name(effect.get("skill"))
                not in objective_duplicate_skills
            ]
            proxy._skill_burst_team_objective_effect_windows = (
                self._team_practical_effect_windows(
                    objective_external_effects,
                    window,
                    include_special_cdr=True,
                    first_cast_times=first_cast_times,
                    cast_schedule=cast_schedule,
                    strict_cast_schedule=not partial_cast_schedule,
                )
            )
        else:
            proxy._skill_burst_team_objective_effect_windows = []
        proxy._skill_burst_support_initial_times = {
            skill: max(
                0.0,
                as_number(
                    first_cast_times.get(
                        (provider, skill),
                        first_cast_times.get(skill, 0.0),
                    )
                ),
            )
            for skill in scheduled_own_special_skills
            if any(
                norm(effect.get("provider")) == norm(provider)
                and self._team_effect_skill_name(effect.get("skill")) == skill
                for effect in effects
            )
        }
        # The personal long-duration simulator gives the planner only the
        # explicitly configured rows and lets it derive the complete learned
        # pool from the build. Passing the already-expanded extreme-team pool
        # back as explicit rows changes candidate semantics and, in particular,
        # makes refreshable periodic skills such as Holy Punishment refresh far
        # less often. Score theory axes must use the same empty explicit seed;
        # Boss downtime and captured teammate schedules remain on the proxy.
        planner_metrics = [] if long_sim_planner else selected_metrics
        segments, generated_window = proxy._update_skill_burst_sim_preview(
            planner_metrics,
            render_ui=False,
        )
        total = sum(max(0.0, as_number(segment.get("effective_score"))) for segment in segments)
        raw_total = sum(max(0.0, as_number(segment.get("raw_effective_score"))) for segment in segments)
        average = total / generated_window if generated_window > 0 else 0.0
        damage_panel = proxy._team_practical_expected_panel()
        hand = max(0.0, as_number(hand_multiplier, 1.0))
        if timing_summary_only:
            # The scheduler and damage calculation above are unchanged. The
            # rest of this method constructs output-score replay/diagnostics,
            # not inputs to DPS. Keep the original segment order and values so
            # the existing fixed-axis glyph routine performs identical sums.
            compact_result = {
                "segments": [
                    {key: segment[key] for key in ("skill", "start", "kind", "effective_score", "raw_effective_score") if key in segment}
                    for segment in segments
                ],
                "window": generated_window, "summary": dict(summary),
                "damage_panel": damage_panel, "hand_multiplier": hand,
                "total": total, "raw_total": raw_total, "average": average,
                "dps": damage_panel * average / 100.0 * hand,
            }
            _release_timing_proxy_method_cycles(proxy)
            return compact_result
        physical_panel = max(0.0, as_number(summary.get("physical_average")))
        magic_panel = max(0.0, as_number(summary.get("magic_average")))
        weighted_panel_profile = proxy._skill_weighted_panel_profile()
        if weighted_panel_profile:
            score_reference_panel = max(
                0.0,
                proxy._skill_weighted_panel_value(summary, weighted_panel_profile),
            )
        else:
            score_reference_panel = (
                magic_panel
                if proxy._job_profile().get("attack_attr") == "魔攻"
                else physical_panel
            )
        combat_multiplier_without_element = (
            damage_panel / score_reference_panel
            if score_reference_panel > 0
            else 0.0
        )
        score_skill_powers: dict[str, float] = {}
        score_skill_damages: dict[str, float] = {}
        score_skill_components: dict[str, dict[str, Any]] = {}
        score_metrics = _score_metrics_with_transformed_attacks(
            self.store,
            job,
            all_metrics,
        )
        for metric in score_metrics:
            skill_key = proxy._skill_burst_skill_key(metric.get("skill"))
            power = proxy._skill_burst_candidate_metric_score(metric)
            timeline_scores = metric.get("timeline_attack_scores")
            physical_power = 0.0
            magic_power = 0.0
            element = norm(metric.get("element"))
            element_multiplier = 1.0
            normalization_panel = score_reference_panel
            if isinstance(timeline_scores, dict) and any(
                as_number(value) > 0 for value in timeline_scores.values()
            ):
                # Paladin/Crusader skills can contain physical and magical
                # damage rows at the same time. Keep both rows on the common
                # real-damage scale, then apply this skill's element once.
                element_multiplier = proxy._skill_burst_element_multiplier(
                    element,
                    {},
                )
                physical_power = max(
                    0.0,
                    as_number(timeline_scores.get("物理")),
                ) * element_multiplier
                magic_power = max(
                    0.0,
                    as_number(timeline_scores.get("魔法")),
                ) * element_multiplier
                power = physical_power + magic_power
                # _skill_timeline_attack_scores normalizes mixed physical and
                # magical rows against the physical panel (or magic when the
                # physical panel is zero). Restore that exact denominator so
                # Paladin/Crusader skills become real damage instead of a
                # misleading shared percentage.
                normalization_panel = physical_panel or magic_panel
            else:
                # Dark and Light Fury share one skill tree but their skills
                # are individually light, dark, or non-elemental. The metric
                # already carries the corresponding effective value; retain
                # the resolved element multiplier for scoring diagnostics.
                element_multiplier = proxy._skill_burst_element_multiplier(
                    element,
                    {},
                )
            damage_scale = (
                normalization_panel
                * combat_multiplier_without_element
                * hand
                / 100.0
            )
            actual_damage = max(0.0, power * damage_scale)
            if skill_key and actual_damage > score_skill_damages.get(skill_key, 0.0):
                score_skill_powers[skill_key] = power
                score_skill_damages[skill_key] = actual_damage
                score_skill_components[skill_key] = {
                    "physical": physical_power,
                    "magic": magic_power,
                    "element": element,
                    "element_multiplier": element_multiplier,
                    "power": power,
                    "normalization_panel": normalization_panel,
                    "combat_multiplier_without_element": combat_multiplier_without_element,
                    "actual_physical_damage": physical_power * damage_scale,
                    "actual_magic_damage": magic_power * damage_scale,
                    "actual_damage": actual_damage,
                }

        def attach_standard_damage(segment: dict[str, Any]) -> None:
            skill_key = proxy._skill_burst_skill_key(segment.get("skill"))
            base_power = max(0.0, as_number(score_skill_powers.get(skill_key)))
            base_damage = max(0.0, as_number(score_skill_damages.get(skill_key)))
            if base_power <= 1e-9 or base_damage <= 0:
                return
            segment["_score_actual_damage"] = (
                base_damage
                * max(0.0, as_number(segment.get("effective_score")))
                / base_power
            )
            segment["_score_raw_actual_damage"] = (
                base_damage
                * max(0.0, as_number(segment.get("raw_effective_score")))
                / base_power
            )
            if "_score_periodic_slices" in segment:
                # Later axis passes may apply a uniform skill/glyph factor to
                # the integrated total. Carry that same factor into its slices.
                slice_total = sum(max(0.0, as_number(piece.get("effective_score")))
                                  for piece in segment["_score_periodic_slices"])
                slice_scale = (max(0.0, as_number(segment.get("effective_score")))
                               / slice_total if slice_total > 1e-9 else 0.0)
                segment["_score_damage_slices"] = [
                    {
                        "start": piece["start"], "end": piece["end"],
                        "_score_actual_damage": base_damage
                        * max(0.0, as_number(piece.get("effective_score"))) / base_power
                        * slice_scale,
                    }
                    for piece in segment["_score_periodic_slices"]
                ]

        for segment in segments:
            if isinstance(segment, dict):
                attach_standard_damage(segment)

        score_actual_cast_segments: dict[str, list[dict[str, Any]]] = {}
        score_actual_periodic_segments: dict[str, list[dict[str, Any]]] = {}
        # The theoretical axis above consumes the job-level factual removal
        # schedule. Captured casts below are valued per player and are clipped
        # once, later, by ScoreRotationMixin against that owner's exact
        # removal events. Leaving the merged job schedule active here would
        # apply the same loss twice when a job appears more than once.
        theory_summon_removal_schedule = getattr(
            proxy,
            "_skill_burst_summon_removal_schedule_override",
            (),
        )
        proxy._skill_burst_summon_removal_schedule_override = ()
        if isinstance(score_actual_casts_by_source, dict):
            metric_by_skill: dict[str, dict[str, Any]] = {}
            for metric in score_metrics:
                skill_key = proxy._skill_burst_skill_key(metric.get("skill"))
                if not skill_key:
                    continue
                current = metric_by_skill.get(skill_key)
                if current is None or proxy._skill_burst_candidate_metric_score(
                    metric
                ) > proxy._skill_burst_candidate_metric_score(current):
                    metric_by_skill[skill_key] = metric
            support_actions = proxy._skill_burst_support_actions(metric_by_skill)
            support_by_skill = {
                proxy._skill_burst_skill_key(action.get("skill")): action
                for action in support_actions
            }
            for source_id, cast_rows in score_actual_casts_by_source.items():
                if not isinstance(cast_rows, (list, tuple)):
                    continue
                replay_segments: list[dict[str, Any]] = []
                carry_in_attr_windows: list[dict[str, Any]] = []
                for cast_row in cast_rows:
                    if not isinstance(cast_row, dict):
                        continue
                    skill_key = proxy._skill_burst_skill_key(cast_row.get("skill"))
                    metric = metric_by_skill.get(skill_key)
                    action = support_by_skill.get(skill_key)
                    # Passive timeline summons are already present for the
                    # complete score scope.  A captured recast confirms the
                    # summon but must not turn its permanent damage stream into
                    # one short summon cycle.  The single passive row appended
                    # below integrates every Boss-output slice exactly once.
                    if skill_key in SKILL_BURST_SIM_PASSIVE_SUMMONS:
                        continue
                    if metric is None and action is None:
                        continue
                    if "_score_carry_in_local_start" in cast_row:
                        if action is None:
                            continue
                        if action.get("state_only") and not action.get("periodic_buff"):
                            continue
                        duration = max(0.0, as_number(action.get("duration")))
                        local_cast_start = as_number(
                            cast_row.get("_score_carry_in_local_start")
                        )
                        local_cast_end = max(
                            local_cast_start,
                            as_number(
                                cast_row.get("_score_carry_in_local_end"),
                                local_cast_start,
                            ),
                        )
                        active_start = (
                            local_cast_start
                            if proxy._skill_burst_action_starts_on_cast(action)
                            else local_cast_end
                        )
                        active_end = active_start + duration
                        if "_score_buff_start" in cast_row and "_score_buff_end" in cast_row:
                            active_start = as_number(cast_row["_score_buff_start"])
                            active_end = as_number(cast_row["_score_buff_end"])
                        if active_end > 1e-6 and active_start < generated_window - 1e-6:
                            carry_in_attr_windows.append({
                                "start": max(0.0, active_start),
                                "end": min(generated_window, active_end),
                                "action": action,
                                "refresh_interval": max(
                                    0.0, as_number(action.get("refresh_interval"))
                                ),
                            })
                        continue
                    start = max(0.0, as_number(cast_row.get("start")))
                    end = min(
                        generated_window,
                        max(start, as_number(cast_row.get("end"), start)),
                    )
                    if start >= generated_window - 1e-9:
                        continue
                    periodic_metric = None
                    if (
                        metric is not None
                        and proxy._skill_periodic_total_damage_cycle(skill_key) > 0
                    ):
                        periodic_metric = copy.deepcopy(metric)
                    replay_segments.append({
                        **{
                            key: cast_row[key]
                            for key in ("_score_buff_start", "_score_buff_end")
                            if key in cast_row
                        },
                        "start": start,
                        "end": end,
                        "skill": norm(
                            (action or {}).get("skill")
                            or (metric or {}).get("skill")
                            or cast_row.get("skill")
                        ),
                        "type": norm((action or {}).get("type")) or "伤害",
                        "kind": "support" if action is not None else "damage",
                        "dpa": 0.0,
                        "effective_score": 0.0,
                        "raw_effective_score": 0.0,
                        "channel_scores": {},
                        "_score_metric": copy.deepcopy(metric) if metric is not None else None,
                        "action_speed_percent": max(
                            0.0, as_number(cast_row.get("action_speed_percent"))
                        ),
                        "periodic_metric": periodic_metric,
                        "element": norm((metric or {}).get("element")),
                        "detail": "实战施法逐实例重放",
                    })

                for passive_key in SKILL_BURST_SIM_PASSIVE_SUMMONS:
                    metric = metric_by_skill.get(passive_key)
                    if (
                        metric is None
                        or proxy._skill_periodic_total_damage_cycle(passive_key) <= 0
                    ):
                        continue
                    replay_segments.append({
                        "start": 0.0,
                        "end": generated_window,
                        "skill": norm(metric.get("skill")) or passive_key,
                        "type": "常驻召唤",
                        "kind": "passive",
                        "dpa": 0.0,
                        "effective_score": 0.0,
                        "raw_effective_score": 0.0,
                        "channel_scores": {},
                        "_score_metric": copy.deepcopy(metric),
                        "periodic_metric": copy.deepcopy(metric),
                        "element": norm(metric.get("element")),
                        "timeline_hidden": True,
                        "detail": "实战常驻召唤·逐时刻环境重放",
                    })
                if not replay_segments:
                    continue

                # Revalue every captured cast against the same standardized
                # build and timeline-effect model as the theoretical axis.
                # Previously only summon/periodic rows went through this path;
                # ordinary skills stayed at their unbuffed one-cast baseline,
                # which made their actual standardized damage systematically
                # lower than summons.
                attr_windows = proxy._skill_burst_timeline_attr_windows(
                    replay_segments,
                    support_actions,
                    generated_window,
                    include_periodic_display_attrs=False,
                )
                attr_windows.extend(carry_in_attr_windows)
                recorded_buff_windows = {
                    (proxy._skill_burst_skill_key(row.get("skill")),
                     as_number(row.get("start")), as_number(row.get("end"))):
                    (as_number(row["_score_buff_start"]),
                     as_number(row["_score_buff_end"]))
                    for row in replay_segments
                    if "_score_buff_start" in row and "_score_buff_end" in row
                }
                for attr_window in attr_windows:
                    recorded = recorded_buff_windows.get((
                        proxy._skill_burst_skill_key(attr_window.get("action", {}).get("skill")),
                        as_number(attr_window.get("cast_start")),
                        as_number(attr_window.get("cast_end")),
                    ))
                    if recorded is not None:
                        attr_window["start"] = max(0.0, recorded[0])
                        attr_window["end"] = min(generated_window, recorded[1])
                summon_active_until: dict[str, list[float]] = {}
                fury = 0
                fury_expires_at = 0.0
                guardian_phantoms = 0
                ordinary_rows: list[dict[str, Any]] = []
                for replay_segment in sorted(
                    replay_segments,
                    key=lambda row: (
                        as_number(row.get("start")),
                        as_number(row.get("end")),
                        proxy._skill_burst_skill_key(row.get("skill")),
                    ),
                ):
                    metric = replay_segment.get("_score_metric")
                    skill_key = proxy._skill_burst_skill_key(replay_segment.get("skill"))
                    start = max(0.0, as_number(replay_segment.get("start")))
                    end = max(start, as_number(replay_segment.get("end"), start))
                    proxy._skill_burst_prune_summon_state(
                        summon_active_until,
                        start,
                    )
                    if fury > 0 and fury_expires_at <= start + 1e-6:
                        fury = 0

                    active_attrs = proxy._skill_burst_active_attrs_from_timeline_windows(
                        attr_windows, start
                    )
                    if proxy._skill_burst_uses_fury():
                        active_attrs[SKILL_BURST_FURY_MARKER] = float(fury)
                    if proxy._skill_burst_uses_guardian_phantoms():
                        active_attrs[SKILL_BURST_GUARDIAN_PHANTOM_MARKER] = float(
                            guardian_phantoms
                        )
                    proxy._skill_burst_attach_summon_state(
                        active_attrs, summon_active_until, start
                    )
                    observed_action_speed = max(
                        0.0, as_number(replay_segment.get("action_speed_percent"))
                    )
                    if observed_action_speed > 0:
                        current_action_speed = max(
                            0.0,
                            display_value(active_attrs, "\u52a8\u4f5c\u901f\u5ea6%"),
                        )
                        current_attack_speed = max(
                            0.0,
                            display_value(active_attrs, "\u653b\u901f%"),
                        )
                        replay_persistent_speed = (
                            proxy._skill_burst_replay_persistent_action_speed(
                                observed_action_speed,
                                start,
                                active_attrs,
                            )
                        )
                        missing_speed = max(
                            0.0,
                            replay_persistent_speed
                            - current_action_speed
                            - current_attack_speed,
                        )
                        if missing_speed > 0:
                            active_attrs["\u52a8\u4f5c\u901f\u5ea6%"] = (
                                current_action_speed + missing_speed
                            )
                    action = support_by_skill.get(skill_key)
                    scoring_attrs = proxy._skill_burst_attrs_during_action(
                        active_attrs, action
                    )
                    from dragon_nest_builder.live_dps.score_v2 import actual_reference_attrs
                    scoring_attrs = actual_reference_attrs(proxy.build, skill_key, start, scoring_attrs)
                    if action is not None:
                        # Captured support casts are replayed directly instead
                        # of passing through the theoretical scheduler.  Keep
                        # cast-time snapshot mechanics on that path as well:
                        # Crusader's Judgement Power records the STR/INT state
                        # at cast time, so casting it during Divine Avatar must
                        # retain the enlarged conversion multiplier for the
                        # following damage casts.
                        proxy._skill_burst_snapshot_periodic_action(
                            action,
                            scoring_attrs,
                        )
                    if isinstance(metric, dict):
                        replay_segment["effective_score"] = (
                            proxy._skill_burst_full_metric_score(
                                metric, scoring_attrs, start
                            )
                        )
                        replay_segment["raw_effective_score"] = max(
                            0.0, as_number(replay_segment.get("effective_score"))
                        )
                        attach_standard_damage(replay_segment)
                        if (
                            not isinstance(replay_segment.get("periodic_metric"), dict)
                            and as_number(replay_segment.get("_score_actual_damage")) > 0
                        ):
                            ordinary_rows.append({
                                "skill": replay_segment.get("skill"),
                                "start": start,
                                "end": end,
                                "_score_actual_damage": replay_segment.get(
                                    "_score_actual_damage"
                                ),
                            })

                    fury, _fury_boosted = proxy._skill_burst_apply_fury_use(
                        fury, skill_key
                    )
                    if proxy._skill_burst_fury_gain(skill_key) > 0:
                        fury_expires_at = end + 60.0
                    guardian_phantoms, _guardian_used = (
                        proxy._skill_burst_apply_guardian_phantom_use(
                            guardian_phantoms, skill_key
                        )
                    )
                    summon_limit = proxy._skill_burst_summon_limit(skill_key)
                    if summon_limit:
                        proxy._skill_burst_register_summon(
                            skill_key,
                            summon_active_until,
                            end + summon_limit[1],
                            end,
                        )
                    elif skill_key in SKILL_BURST_ENGINEER_SUMMONS:
                        proxy._skill_burst_register_summon(
                            skill_key,
                            summon_active_until,
                            end + 30.0,
                            end,
                        )

                if ordinary_rows:
                    score_actual_cast_segments[str(source_id)] = ordinary_rows

                if any(
                    isinstance(row.get("periodic_metric"), dict)
                    for row in replay_segments
                ):
                    proxy._skill_burst_apply_refreshable_periodic_scores(
                        replay_segments, generated_window
                    )
                    proxy._skill_burst_apply_periodic_total_damage_scores(
                        replay_segments, support_actions, generated_window
                    )
                periodic_rows: list[dict[str, Any]] = []
                for replay_segment in replay_segments:
                    if not isinstance(replay_segment.get("periodic_metric"), dict):
                        continue
                    attach_standard_damage(replay_segment)
                    if as_number(replay_segment.get("_score_actual_damage")) > 0:
                        periodic_rows.append(replay_segment)
                if periodic_rows:
                    score_actual_periodic_segments[str(source_id)] = periodic_rows
        proxy._skill_burst_summon_removal_schedule_override = (
            theory_summon_removal_schedule
        )
        result_support_actions = proxy._skill_burst_support_actions({
            proxy._skill_burst_skill_key(metric.get("skill")): metric
            for metric in all_metrics
        })
        result = {
            "job": norm(job),
            "provider": provider,
            "relevant_resist_attrs": sorted(
                proxy._skill_burst_relevant_resist_attrs(selected_metrics)
            ),
            "window": generated_window,
            "segments": segments,
            "effect_windows": (
                proxy._skill_burst_effect_windows(
                    segments,
                    result_support_actions,
                    generated_window,
                )
                if include_display_windows
                else []
            ),
            "external_windows": (
                copy.deepcopy(proxy._skill_burst_external_team_effect_windows)
                if include_display_windows
                else []
            ),
            "player_buff_clear_times": player_buff_clear_signature,
            "summon_removal_schedule": summon_removal_signature,
            "average": average,
            "total": total,
            "raw_total": raw_total,
            "damage_panel": damage_panel,
            "hand_multiplier": hand,
            "dps": damage_panel * average / 100.0 * hand,
            "summary": dict(summary),
            # Keep the old percentage-equivalent values for diagnostics and
            # compatibility. Team scoring consumes the real-damage map below.
            "_score_skill_powers": score_skill_powers,
            "_score_skill_damages": score_skill_damages,
            "_score_default_damage_per_percent": (
                damage_panel * hand / 100.0
            ),
            "_score_skill_components": score_skill_components,
            "_score_actual_cast_segments": score_actual_cast_segments,
            "_score_actual_periodic_segments": score_actual_periodic_segments,
            "planner_profile": (
                "long_sim"
                if long_sim_planner
                else "fast_long"
                if fast_long_timeline
                else "extreme"
            ),
            "same_environment_theory": bool(same_environment_theory),
            "_planner_settings": {
                "complete_default_rotation": bool(
                    proxy._skill_burst_complete_default_rotation
                ),
                "stable_external_rotation": bool(
                    proxy._skill_burst_stable_external_rotation
                ),
                "multistart": bool(
                    getattr(
                        proxy,
                        "_skill_burst_enable_new_scheme_1_multistart",
                        True,
                    )
                ),
                "hybrid_optimizer": not bool(
                    getattr(proxy, "_skill_burst_disable_hybrid_optimizer", False)
                ),
                "local_optimizer": not bool(
                    getattr(proxy, "_skill_burst_disable_local_optimizer", False)
                ),
                "support_order_quality": copy.deepcopy(
                    getattr(proxy, "_skill_burst_team_opening_last_quality", {})
                ),
                "joint_support_order_quality": copy.deepcopy(
                    getattr(
                        proxy,
                        "_skill_burst_joint_support_order_last_quality",
                        {},
                    )
                ),
            },
        }
        if os.getenv("DRAGON_NEST_TIMELINE_DEBUG") == "1":
            result["_planner_runtime_debug"] = {
                key: repr(value)
                for key, value in sorted(proxy.__dict__.items())
                if key.startswith("_skill_burst_")
                and "cache" not in key
                and "segments" not in key
                and len(repr(value)) < 500
            }
        planner_trace = getattr(proxy, "_skill_burst_new_scheme_1_trace", None)
        if isinstance(planner_trace, list):
            result["planner_trace"] = copy.deepcopy(planner_trace)
            result["planner_quality"] = copy.deepcopy(
                getattr(proxy, "_skill_burst_new_scheme_1_last_quality", {})
            )
        if (
            fast_long_timeline
            and bool(getattr(proxy, "_skill_burst_fast_mechanism_refinement", False))
        ) or os.getenv("DRAGON_NEST_TIMELINE_PROFILE") == "1":
            result["planner_refinement"] = copy.deepcopy(
                getattr(proxy, "_skill_burst_local_optimizer_last_stats", {})
            )
        cache[cache_key] = copy.deepcopy(result)
        while len(cache) > 96:
            cache.pop(next(iter(cache)))
        _release_timing_proxy_method_cycles(proxy)
        return result

    def _extreme_synchronized_team_timelines(
        self,
        team: list[str] | tuple[str, ...],
        effects: list[dict[str, Any]],
        hand_multipliers: dict[str, float],
        initial_timelines: list[dict[str, Any]],
        window: float = SKILL_BURST_PRACTICAL_WINDOW,
        progress_callback=None,
        include_display_windows: bool = True,
        build_overrides: list[dict[str, Any] | None] | None = None,
        blocked_intervals: list[dict[str, Any]] | list[tuple[float, float]] | None = None,
        pinned_cast_schedule: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
        max_passes: int = 3,
        fast_long_timeline: bool = False,
    ) -> list[dict[str, Any]]:
        """Recalculate the final team once with buffs aligned to provider cast times."""
        effect_schedule_keys: set[tuple[str, str]] = set()
        for effect in effects:
            provider = norm(effect.get("provider"))
            skill = self._team_effect_skill_name(effect.get("skill"))
            skill_key = self._skill_burst_skill_key(skill)
            if provider and skill_key:
                effect_schedule_keys.add((provider, skill_key))
                if re.search(r"\s*EX$", skill, flags=re.IGNORECASE):
                    effect_schedule_keys.add((
                        provider,
                        self._skill_burst_skill_key(
                            re.sub(r"\s*EX$", "", skill, flags=re.IGNORECASE)
                        ),
                    ))

        def support_schedule(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[tuple[float, float]]]:
            complete = self._extreme_timeline_cast_schedule(rows)
            schedule = {
                key: casts
                for key, casts in complete.items()
                if key in effect_schedule_keys
            }
            for key, casts in (pinned_cast_schedule or {}).items():
                normalized_key = (norm(key[0]), self._skill_burst_skill_key(key[1]))
                if normalized_key in effect_schedule_keys:
                    schedule[normalized_key] = list(casts)
            return schedule

        def schedule_signature(
            schedule: dict[tuple[str, str], list[tuple[float, float]]],
        ) -> tuple[Any, ...]:
            return tuple(sorted(
                (
                    provider,
                    skill,
                    tuple(
                        (round(as_number(start), 2), round(as_number(end), 2))
                        for start, end in casts
                    ),
                )
                for (provider, skill), casts in schedule.items()
            ))

        current = initial_timelines if fast_long_timeline else copy.deepcopy(initial_timelines)
        for sync_pass in range(max(0, int(max_passes))):
            cast_schedule = support_schedule(current)
            if not cast_schedule:
                return current
            before = schedule_signature(cast_schedule)
            task_specs: list[dict[str, Any]] = []
            for index, job in enumerate(team):
                hand = max(
                    0.0,
                    as_number(
                        hand_multipliers.get(job, hand_multipliers.get(self._team_real_job(job), 1.0)),
                        1.0,
                    ),
                )
                result_kwargs: dict[str, Any] = {
                    "cast_schedule": cast_schedule,
                    "include_display_windows": include_display_windows,
                }
                if fast_long_timeline:
                    result_kwargs["fast_long_timeline"] = True
                if blocked_intervals:
                    result_kwargs["blocked_intervals"] = blocked_intervals
                if (
                    isinstance(build_overrides, list)
                    and index < len(build_overrides)
                    and isinstance(build_overrides[index], dict)
                ):
                    result_kwargs["build_override"] = build_overrides[index]
                task_specs.append({
                    "job": job,
                    "team": list(team),
                    "effects": effects,
                    "hand_multiplier": hand,
                    "window": window,
                    **result_kwargs,
                })
            synchronized = self._extreme_timeline_tasks_parallel(
                task_specs,
                progress_callback=(
                    lambda completed, _index, job: progress_callback(completed - 1, job)
                    if sync_pass == 0 and callable(progress_callback)
                    else None
                ),
            )
            current = synchronized
            if schedule_signature(support_schedule(current)) == before:
                break
        if include_display_windows:
            self._extreme_sync_special_team_buff_display_windows(
                effects,
                current,
                window,
            )
        return current

    def _extreme_sync_special_team_buff_display_windows(
        self,
        effects: list[dict[str, Any]],
        timelines: list[dict[str, Any]],
        window: float,
    ) -> None:
        """Show special team buffs on every recipient at the provider's real cast time."""
        actual_schedule = self._extreme_timeline_cast_schedule(timelines)
        if not actual_schedule:
            return
        horizon = max(1.0, as_number(window, SKILL_BURST_PRACTICAL_WINDOW))
        for result in timelines:
            current_provider = norm(result.get("provider"))
            recipient_effects = [
                effect
                for effect in effects
                if norm(effect.get("provider")) != current_provider
                and self._skill_burst_skill_key(
                    self._team_effect_skill_name(effect.get("skill"))
                ) and is_acceleration_resistance_skill(
                    self._team_effect_skill_name(effect.get("skill"))
                )
            ]
            external = [
                row
                for row in result.get("external_windows", [])
                if not is_acceleration_resistance_skill(row.get("source"))
            ]
            if recipient_effects:
                external.extend(self._team_practical_effect_windows(
                    recipient_effects,
                    horizon,
                    include_special_cdr=True,
                    cast_schedule=actual_schedule,
                    strict_cast_schedule=True,
                ))
            result["external_windows"] = external

    def _optimize_extreme_team_selection(
        self,
        selected: list[str],
        jobs: list[str],
        best_score: float,
        base_unit: float,
        coeffs: dict[str, float],
        score_func,
        progress_step,
        locked_jobs: list[str] | None = None,
        require_tank: bool = True,
        require_healer: bool = True,
    ) -> tuple[list[str], float]:
        current_job = norm(self.second_job.currentText()) if hasattr(self, "second_job") else ""
        locked = set(locked_jobs or [current_job])
        if not selected:
            return selected, best_score
        improved = True
        passes = 0
        while improved and passes < 2:
            improved = False
            passes += 1
            for index, current in enumerate(selected):
                if current in locked:
                    continue
                slot_best = current
                slot_score = best_score
                for job in jobs:
                    if self._team_unique_job(job) in self._team_unique_job_set(selected) or job == current:
                        continue
                    candidate = selected[:]
                    candidate[index] = job
                    if not self._extreme_team_has_required_roles(candidate, require_tank, require_healer):
                        continue
                    if progress_step and not progress_step(f"正在极限替换优化：{current} -> {job}"):
                        return selected, best_score
                    candidate_score = score_func(tuple(candidate))
                    if candidate_score > slot_score + 1e-6:
                        slot_score = candidate_score
                        slot_best = job
                if slot_best != current:
                    selected[index] = slot_best
                    best_score = slot_score
                    improved = True
        improved = True
        pair_passes = 0
        while improved and pair_passes < 2:
            improved = False
            pair_passes += 1
            selected_set = set(selected)
            for left in range(len(selected)):
                if selected[left] in locked:
                    continue
                for right in range(left + 1, len(selected)):
                    if selected[right] in locked:
                        continue
                    current_left = selected[left]
                    current_right = selected[right]
                    pair_best = (current_left, current_right)
                    pair_score = best_score
                    available = [job for job in jobs if job not in selected_set or job in {current_left, current_right}]
                    for first, second in combinations(available, 2):
                        if {first, second} == {current_left, current_right}:
                            continue
                        candidate = selected[:]
                        candidate[left] = first
                        candidate[right] = second
                        if self._team_has_real_duplicate(candidate):
                            continue
                        if not self._extreme_team_has_required_roles(candidate, require_tank, require_healer):
                            continue
                        if progress_step and not progress_step(f"正在极限组合优化：{current_left}/{current_right} -> {first}/{second}"):
                            return selected, best_score
                        candidate_score = score_func(tuple(candidate))
                        if candidate_score > pair_score + 1e-6:
                            pair_score = candidate_score
                            pair_best = (first, second)
                    if pair_best != (current_left, current_right):
                        selected[left], selected[right] = pair_best
                        best_score = pair_score
                        selected_set = set(selected)
                        improved = True
        return selected, best_score

    def _prefer_extreme_mode_team_within_threshold(
        self,
        selected: list[str],
        jobs: list[str],
        best_score: float,
        score_func,
        progress_step,
        locked_jobs: list[str] | None,
        team_mode: str,
        require_tank: bool = True,
        require_healer: bool = True,
        tolerance: float = 0.10,
    ) -> tuple[list[str], float]:
        if not self._extreme_mode_has_preference(team_mode) or not selected or best_score <= 0:
            return selected, best_score
        locked = unique([job for job in (locked_jobs or []) if job])
        floor_score = best_score * max(0.0, 1.0 - tolerance)

        def legal(team: list[str] | tuple[str, ...]) -> bool:
            return (
                len(team) == TEAM_MEMBER_ROWS
                and not self._team_has_real_duplicate(team)
                and all(job in team for job in locked)
                and self._extreme_team_has_required_roles(team, require_tank, require_healer)
            )

        def preference_key(team: list[str] | tuple[str, ...], value: float) -> tuple[int, float]:
            return (self._extreme_team_mode_match_count(team, team_mode), value)

        selected_score = score_func(tuple(selected))
        preferred_team = selected[:]
        preferred_score = selected_score
        preferred_key = preference_key(preferred_team, preferred_score)

        initial = tuple(locked)
        if len(initial) <= TEAM_MEMBER_ROWS:
            states: list[tuple[tuple[str, ...], float]] = [(initial, score_func(initial))]
            slots_to_fill = TEAM_MEMBER_ROWS - len(initial)
            for slot in range(slots_to_fill):
                next_states: dict[tuple[str, ...], float] = {}
                for team, _value in states:
                    for job in jobs:
                        if self._team_unique_job(job) in self._team_unique_job_set(team):
                            continue
                        candidate = tuple([*team, job])
                        if not self._extreme_team_can_still_meet_roles(candidate, TEAM_MEMBER_ROWS - len(candidate), require_tank, require_healer):
                            continue
                        if progress_step and not progress_step(f"正在按队伍类型保留：尝试 {job}"):
                            return preferred_team, preferred_score
                        next_states[candidate] = score_func(candidate)
                if not next_states:
                    break
                states = sorted(
                    next_states.items(),
                    key=lambda item: (
                        self._extreme_team_mode_match_count(item[0], team_mode),
                        item[1],
                    ),
                    reverse=True,
                )[:TEAM_EXTREME_BEAM_WIDTH]
            for team, value in states:
                if not legal(team) or value < floor_score:
                    continue
                key = preference_key(team, value)
                if key > preferred_key:
                    preferred_team = list(team)
                    preferred_score = value
                    preferred_key = key

        improved = True
        while improved:
            improved = False
            locked_set = set(locked)
            for index, current in enumerate(preferred_team):
                if current in locked_set:
                    continue
                current_key = preference_key(preferred_team, preferred_score)
                slot_team = preferred_team[:]
                slot_score = preferred_score
                for job in jobs:
                    if self._team_unique_job(job) in self._team_unique_job_set(preferred_team) or job == current:
                        continue
                    candidate = preferred_team[:]
                    candidate[index] = job
                    if not legal(candidate):
                        continue
                    if progress_step and not progress_step(f"正在按队伍类型替换：{current} -> {job}"):
                        return preferred_team, preferred_score
                    candidate_score = score_func(tuple(candidate))
                    if candidate_score < floor_score:
                        continue
                    candidate_key = preference_key(candidate, candidate_score)
                    if candidate_key > current_key:
                        slot_team = candidate
                        slot_score = candidate_score
                        current_key = candidate_key
                if slot_team != preferred_team:
                    preferred_team = slot_team
                    preferred_score = slot_score
                    improved = True
        return preferred_team, preferred_score

    def _team_extreme_total_output(
        self,
        jobs: list[str],
        effects: list[dict[str, Any]],
        base_unit: float,
        coeffs: dict[str, float],
        base_element_percent: float = 0.0,
        hand_multipliers: dict[str, float] | None = None,
    ) -> float:
        attrs = self._merged_team_attrs(effects)
        cdr_multiplier = self._team_cdr_profile(effects)["multiplier"]
        freeze_multiplier = self._team_freeze_profile(effects)["multiplier"]
        hands = hand_multipliers or {}
        total = 0.0
        for job in jobs:
            real_job = self._team_real_job(job)
            coeff = max(0.0, as_number(coeffs.get(job, coeffs.get(real_job)), self._default_extreme_coefficient(job)))
            hand = max(0.0, as_number(hands.get(job, hands.get(real_job, 1.0)), 1.0))
            personal_attrs = self._extreme_personal_buff_attrs(job, effects)
            personal_breakdown = self._extreme_personal_buff_breakdown(
                job,
                personal_attrs,
                base_unit,
                base_element_percent,
            )
            after_personal = max(0.0, as_number(personal_breakdown.get("after_panel")))
            if after_personal <= 0:
                continue
            team_multiplier = self._team_extreme_job_multiplier(
                job,
                attrs,
                after_personal,
                base_element_percent,
                include_boss=True,
                include_base_element=False,
                base_neutral_percent=as_number(personal_breakdown.get('neutral_after')),
            )
            team_panel = after_personal * team_multiplier * cdr_multiplier * freeze_multiplier
            total += team_panel * coeff * hand
        return total

    def _team_extreme_output_values(
        self,
        jobs: list[str],
        effects: list[dict[str, Any]],
        base_unit: float,
        coeffs: dict[str, float],
        base_element_percent: float = 0.0,
    ) -> dict[str, float]:
        """Return only per-job static outputs for the large candidate search."""
        attrs = self._merged_team_attrs(effects)
        cdr_profile = self._team_cdr_profile(effects)
        freeze_profile = self._team_freeze_profile(effects)
        cdr_signature = self._extreme_cdr_cache_signature(effects)
        values: dict[str, float] = {}
        for job in jobs:
            real_job = self._team_real_job(job)
            coeff = max(
                0.0,
                as_number(
                    coeffs.get(job, coeffs.get(real_job)),
                    self._default_extreme_coefficient(job),
                ),
            )
            personal_attrs = self._extreme_personal_buff_attrs(
                job,
                effects,
                cdr_signature=cdr_signature,
            )
            personal_breakdown = self._extreme_personal_buff_breakdown(
                job,
                personal_attrs,
                base_unit,
                base_element_percent,
            )
            after_personal = max(0.0, as_number(personal_breakdown.get("after_panel")))
            if after_personal <= 0:
                values[job] = 0.0
                continue
            team_multiplier = self._team_extreme_job_multiplier(
                job,
                attrs,
                after_personal,
                base_element_percent,
                include_boss=True,
                include_base_element=False,
                base_neutral_percent=as_number(personal_breakdown.get('neutral_after')),
            )
            values[job] = (
                after_personal
                * team_multiplier
                * as_number(cdr_profile.get("multiplier"), 1.0)
                * as_number(freeze_profile.get("multiplier"), 1.0)
                * coeff
            )
        return values

    def _team_extreme_output_details(
        self,
        jobs: list[str],
        effects: list[dict[str, Any]],
        base_unit: float,
        coeffs: dict[str, float],
        base_element_percent: float = 0.0,
    ) -> list[dict[str, Any]]:
        attrs = self._merged_team_attrs(effects)
        cdr_multiplier = self._team_cdr_profile(effects)["multiplier"]
        freeze_multiplier = self._team_freeze_profile(effects)["multiplier"]
        freeze_profile = self._team_freeze_profile(effects)
        rows: list[dict[str, Any]] = []
        for job in jobs:
            real_job = self._team_real_job(job)
            coeff = max(0.0, as_number(coeffs.get(job, coeffs.get(real_job)), self._default_extreme_coefficient(job)))
            profile = self._team_job_attack_profile(job)
            personal_attrs = self._extreme_personal_buff_attrs(job, effects)
            personal_breakdown = self._extreme_personal_buff_breakdown(
                job,
                personal_attrs,
                base_unit,
                base_element_percent,
            )
            base_output = max(0.0, as_number(personal_breakdown.get("before_panel")))
            if base_output > 0:
                personal_multiplier = as_number(personal_breakdown.get("personal_multiplier"))
                after_personal = max(0.0, as_number(personal_breakdown.get("after_panel")))
                team_multiplier = self._team_extreme_job_multiplier(
                    job,
                    attrs,
                    after_personal,
                    base_element_percent,
                    include_boss=True,
                    include_base_element=False,
                    base_neutral_percent=as_number(personal_breakdown.get('neutral_after')),
                )
                team_breakdown = self._team_extreme_job_breakdown(
                    job,
                    attrs,
                    after_personal,
                    base_element_percent,
                    include_boss=True,
                    include_base_element=False,
                    base_neutral_percent=as_number(personal_breakdown.get('neutral_after')),
                )
            else:
                personal_multiplier = 0.0
                team_multiplier = 0.0
                after_personal = 0.0
                team_breakdown = {}
                personal_breakdown = {}
            team_panel = after_personal * team_multiplier * cdr_multiplier * freeze_multiplier
            mixed_weights = self._extreme_mixed_element_weights(job)
            element_label = "/".join(
                key for key in ("光", "暗", "无")
                if as_number(mixed_weights.get(key)) > 1e-9
            ) if mixed_weights else (profile["element"] or "无")
            final_output = team_panel * coeff
            rows.append({
                "job": job,
                "attack": profile["attack"],
                "element": element_label or "无",
                "coefficient": coeff,
                "base_output": base_output,
                "personal_attrs": attr_summary(self._team_display_attrs(personal_attrs if base_output > 0 else {}), limit=999),
                "personal_before_panel": personal_breakdown.get("before_panel", 0.0),
                "personal_after_panel": personal_breakdown.get("after_panel", 0.0),
                "personal_panel_gain": personal_breakdown.get("panel_gain", 0.0),
                "personal_attack_before": personal_breakdown.get("attack_before", 0.0),
                "personal_attack_after": personal_breakdown.get("attack_after", 0.0),
                "personal_attack_gain_percent": personal_breakdown.get("attack_gain_percent", 0.0),
                "personal_attack_multiplier": personal_breakdown.get("personal_attack_multiplier", 1.0),
                "personal_final_multiplier": personal_breakdown.get("personal_final_multiplier", 1.0),
                "personal_crit_multiplier": personal_breakdown.get("personal_crit_multiplier", 1.0),
                "personal_element_multiplier": personal_breakdown.get("personal_element_multiplier", 1.0),
                "personal_other_multiplier": personal_breakdown.get("personal_other_multiplier", 1.0),
                "personal_final_before": personal_breakdown.get("final_before", 0.0),
                "personal_final_after": personal_breakdown.get("final_after", 0.0),
                "personal_crit_before": personal_breakdown.get("crit_before", 0.0),
                "personal_crit_after": personal_breakdown.get("crit_after", 0.0),
                "personal_crit_damage_before": personal_breakdown.get("crit_damage_before", 0.0),
                "personal_crit_damage_after": personal_breakdown.get("crit_damage_after", 0.0),
                "personal_element_before": personal_breakdown.get("element_before", 0.0),
                "personal_element_after": personal_breakdown.get("element_after", 0.0),
                "personal_multiplier": personal_multiplier,
                "after_personal": after_personal,
                "attack_percent": team_breakdown.get("attack_percent", 0.0),
                "flat_attack": team_breakdown.get("flat_attack", 0.0),
                "final_bonus": team_breakdown.get("final_bonus", 0.0),
                "element_attack": team_breakdown.get("element_attack", 0.0),
                "amp_percent": team_breakdown.get("amp_percent", 0.0),
                "crit_resist_down": team_breakdown.get("crit_resist_down", 0.0),
                "resist_down": team_breakdown.get("resist_down", 0.0),
                "defense_down": team_breakdown.get("defense_down", 0.0),
                "attack_multiplier": team_breakdown.get("attack_multiplier", 1.0),
                "final_multiplier": team_breakdown.get("final_multiplier", 1.0),
                "element_multiplier": team_breakdown.get("element_multiplier", 1.0),
                "amp_multiplier": team_breakdown.get("amp_multiplier", 1.0),
                "crit_multiplier": team_breakdown.get("crit_multiplier", 1.0),
                "resist_multiplier": team_breakdown.get("resist_multiplier", 1.0),
                "defense_multiplier": team_breakdown.get("defense_multiplier", 1.0),
                "team_multiplier": team_multiplier,
                "cdr_multiplier": cdr_multiplier,
                "freeze_multiplier": freeze_multiplier,
                "freeze_layers": freeze_profile.get("layers", 0.0),
                "team_panel": team_panel,
                "final_output": final_output,
            })
        total = sum(as_number(row.get("final_output")) for row in rows)
        for row in rows:
            row["share"] = as_number(row.get("final_output")) / total * 100 if total > 0 else 0.0
        return rows

    def _extreme_personal_buff_labels(self, job: str) -> list[str]:
        job = self._team_real_job(job)
        job_sources = set(self.store.job_buff_sources(job))
        labels: list[str] = []
        for row in self.store.tables["buff"]:
            if (
                norm(row.get("来源")) == "通用"
                and norm(row.get("类别")) == "被动"
                and norm(row.get("技能")) == "力量掌握"
            ):
                label = buff_display_label(row)
                if label not in labels:
                    labels.append(label)
                break
        for row in self.store.tables["buff"]:
            if norm(row.get("来源")) not in job_sources:
                continue
            if norm(row.get("类别")) not in {"被动", "个人buff"}:
                continue
            label = buff_display_label(row)
            if label not in labels:
                labels.append(label)
        return labels

    def _extreme_first_job_for_second(self, job: str) -> str:
        job_text = self._team_real_job(job)
        for row in self.store.tables["character"]:
            if norm(row.get("二转职业")) == job_text:
                return norm(row.get("一转职业"))
        return ""

    def _extreme_cdr_cache_signature(self, effects: list[dict[str, Any]]) -> tuple[tuple[str, float, float, float], ...]:
        rows: list[tuple[str, float, float, float]] = []
        for effect in effects:
            skill = self._team_effect_skill_name(effect.get("skill"))
            if skill not in TEAM_CDR_APPLY_SKILLS:
                continue
            cdr = max(0.0, as_number(effect.get("attrs", {}).get("冷却缩减%")))
            if cdr <= 0:
                continue
            rows.append((
                skill,
                round(max(0.0, as_number(effect.get("cooldown"))), 4),
                round(max(0.0, as_number(effect.get("duration"))), 4),
                round(cdr, 4),
            ))
        return tuple(sorted(rows))

    def _extreme_personal_buff_attrs(
        self,
        job: str,
        team_effects: list[dict[str, Any]],
        *,
        cdr_signature: tuple[tuple[str, float, float, float], ...] | None = None,
    ) -> dict[str, float]:
        display_job = norm(job)
        job = self._team_real_job(job)
        cache_key = (
            norm(job),
            cdr_signature
            if cdr_signature is not None
            else self._extreme_cdr_cache_signature(team_effects),
        )
        cache = getattr(self, "_extreme_personal_attrs_cache", None)
        if isinstance(cache, dict) and cache_key in cache:
            return dict(cache[cache_key])
        total: dict[str, float] = {}
        applied_cdr = {
            self._team_effect_skill_name(effect.get("skill"))
            for effect in team_effects
            if self._team_effect_skill_name(effect.get("skill")) in TEAM_CDR_APPLY_SKILLS
        }
        base_rows_cache = getattr(self, "_extreme_personal_buff_base_rows_cache", None)
        if not isinstance(base_rows_cache, dict):
            base_rows_cache = {}
            self._extreme_personal_buff_base_rows_cache = base_rows_cache
        base_rows_key = display_job or job
        base_rows = base_rows_cache.get(base_rows_key)
        if base_rows is None:
            provider = f"队员0 {display_job or job}"
            base_rows = []
            for skill in self._extreme_personal_buff_labels(job):
                if not skill or skill == "无":
                    continue
                levels = self._team_effect_levels(skill, provider)
                level = self._team_effect_default_level(skill, provider, levels)
                attrs = self._team_effect_attrs(skill, level, provider, include_personal_buffs=True)
                if not attrs:
                    continue
                timing = self._team_effect_timing(provider, skill, level)
                base_rows.append((
                    dict(attrs),
                    as_number(timing.get("cooldown")),
                    as_number(timing.get("duration")),
                ))
            base_rows_cache[base_rows_key] = base_rows
        for attrs, raw_cooldown, duration in base_rows:
            cooldown = raw_cooldown
            if cooldown > 0 and applied_cdr:
                cooldown = self._team_cdr_adjusted_cooldown(cooldown, team_effects, applied_cdr)
            self._merge_attrs(total, self._team_effective_attrs(attrs, cooldown, duration))
        if isinstance(cache, dict):
            cache[cache_key] = dict(total)
        return total

    def _extreme_element_normalizer(self, job: str, base_element_percent: float, include_job_base: bool = True) -> dict[str, float]:
        base_summary = getattr(self, "_extreme_clean_base_summary", {})
        out: dict[str, float] = {}
        for element in ("火", "水", "光", "暗"):
            current = as_number(base_summary.get(f"{element}攻%"))
            if abs(current) > 1e-9:
                out[f"{element}攻%"] = out.get(f"{element}攻%", 0.0) - current
        element = self._team_job_attack_profile(job).get("element")
        if include_job_base and element:
            out[f"{element}攻%"] = out.get(f"{element}攻%", 0.0) + max(0.0, base_element_percent)
        return {attr: value for attr, value in out.items() if abs(value) > 1e-9}

    def _extreme_personal_buff_multiplier(
        self,
        job: str,
        personal_attrs: dict[str, float],
        base_output: float,
        base_element_percent: float,
    ) -> float:
        return as_number(self._extreme_personal_buff_breakdown(
            job,
            personal_attrs,
            base_output,
            base_element_percent,
        ).get("personal_multiplier"))

    def _extreme_effective_attack_value(self, job: str, summary: dict[str, Any]) -> float:
        job = self._team_real_job(job)
        row = next((item for item in self.store.tables["effective"] if norm(item.get("职业")) == norm(job)), {})
        phys = as_number(summary.get("physical_average"))
        magic = as_number(summary.get("magic_average"))
        total = 0.0
        weight_total = 0.0
        for index in range(1, 4):
            attack = norm(row.get(f"攻击{index}"))
            weight = as_number(row.get(f"权重{index}"))
            fixed = as_number(row.get(f"固定{index}"))
            if not attack or attack == "无" or weight <= 0:
                continue
            if attack == "物攻":
                panel = phys
            elif attack == "魔攻":
                panel = magic
            elif attack == "双攻":
                panel = phys + magic
            else:
                panel = 0.0
            total += (panel * weight / 100) + fixed
            weight_total += weight
        return total / (weight_total / 100) if weight_total > 0 else 0.0

    @staticmethod
    def _extreme_summary_crit_factor(summary: dict[str, Any]) -> float:
        crit_percent = min(90.0, max(0.0, as_number(summary.get("crit_percent"))))
        crit_damage_percent = min(100.0, max(0.0, as_number(summary.get("crit_damage_percent"))))
        crit_resist_percent = min(90.0, max(0.0, as_number(summary.get("crit_resist_percent"))))
        effective_crit_rate = (crit_percent / 100) * max(0.0, 1 - crit_resist_percent / 100)
        return 1 + effective_crit_rate * (2 + crit_damage_percent / 100 - 1)

    def _extreme_personal_buff_breakdown(
        self,
        job: str,
        personal_attrs: dict[str, float],
        base_output: float,
        base_element_percent: float,
    ) -> dict[str, float]:
        job = self._team_real_job(job)
        if base_output <= 0:
            return {"personal_multiplier": 0.0}
        cache_key = (norm(job), self._attr_cache_signature(personal_attrs), round(as_number(base_element_percent), 4))
        breakdown_cache = getattr(self, "_extreme_personal_breakdown_cache", None)
        if isinstance(breakdown_cache, dict) and cache_key in breakdown_cache:
            return dict(breakdown_cache[cache_key])
        cache = getattr(self, "_extreme_personal_multiplier_cache", None)
        base_build = self._extreme_converted_build_for_job(job)
        personal_build = self._extreme_converted_build_for_job(job, personal_attrs)
        try:
            _base_rows, base_summary = self.calculator.calculate(base_build)
            _personal_rows, personal_summary = self.calculator.calculate(personal_build)
        except Exception:
            value = self._team_extreme_job_multiplier(
                job,
                personal_attrs,
                base_output,
                base_element_percent,
                include_boss=False,
                include_base_element=True,
            )
            return {"personal_multiplier": value}
        base_effective = as_number(base_summary.get("effective_panel"))
        personal_effective = as_number(personal_summary.get("effective_panel"))
        if norm(job) == "曜":
            base_effective *= self._extreme_mixed_element_correction(job, base_summary)
            personal_effective *= self._extreme_mixed_element_correction(job, personal_summary)
        if base_effective <= 0:
            return {"personal_multiplier": 1.0}
        value = max(0.0, personal_effective / base_effective)
        profile = self._team_job_attack_profile(job)
        element = profile["element"]
        element_key = f"{element}攻%" if element else "无属性%"
        attack_before = self._extreme_effective_attack_value(job, base_summary)
        attack_after = self._extreme_effective_attack_value(job, personal_summary)
        attack_multiplier = attack_after / attack_before if attack_before > 0 else 1.0
        final_multiplier = (1 + as_number(personal_summary.get("final_percent")) / 100) / max(0.01, 1 + as_number(base_summary.get("final_percent")) / 100)
        crit_multiplier = self._extreme_summary_crit_factor(personal_summary) / max(0.01, self._extreme_summary_crit_factor(base_summary))
        element_before = as_number(base_summary.get(element_key)) if element_key else 0.0
        element_after = as_number(personal_summary.get(element_key)) if element_key else 0.0
        element_multiplier = (1 + element_after / 100) / max(0.01, 1 + element_before / 100) if element_key else 1.0
        known_multiplier = attack_multiplier * final_multiplier * crit_multiplier * element_multiplier
        other_multiplier = value / known_multiplier if known_multiplier > 0 else 1.0
        result = {
            "personal_multiplier": value,
            "before_panel": base_effective,
            "after_panel": personal_effective,
            "panel_gain": personal_effective - base_effective,
            "attack_before": attack_before,
            "attack_after": attack_after,
            "attack_gain_percent": (attack_after / attack_before - 1) * 100 if attack_before > 0 else 0.0,
            "personal_attack_multiplier": attack_multiplier,
            "personal_final_multiplier": final_multiplier,
            "personal_crit_multiplier": crit_multiplier,
            "personal_element_multiplier": element_multiplier,
            "personal_other_multiplier": other_multiplier,
            "final_before": as_number(base_summary.get("final_percent")),
            "final_after": as_number(personal_summary.get("final_percent")),
            "crit_before": as_number(base_summary.get("crit_percent")),
            "crit_after": as_number(personal_summary.get("crit_percent")),
            "crit_damage_before": as_number(base_summary.get("crit_damage_percent")),
            "crit_damage_after": as_number(personal_summary.get("crit_damage_percent")),
            "element_before": element_before,
            "element_after": element_after,
            "neutral_after": as_number(personal_summary.get('无属性%')),
        }
        if isinstance(cache, dict):
            cache[cache_key] = value
        if isinstance(breakdown_cache, dict):
            breakdown_cache[cache_key] = dict(result)
        return result

    def _extreme_fixed_job_grades(
        self,
        best_team: list[str],
        fixed_jobs: list[str],
        candidate_jobs: list[str],
        score_func,
        best_score: float,
        current_job: str,
        team_mode: str = "不限",
        require_tank: bool = True,
        require_healer: bool = True,
        pre_score_func=None,
        max_candidates_per_member: int = 5,
        max_same_family_candidates: int = 1,
        fast_mode: bool = True,
        progress_callback=None,
        refine_score_func=None,
        refined_best_score: float | None = None,
        max_refined_candidates_per_member: int = 0,
    ) -> dict[str, dict[str, Any]]:
        grades: dict[str, dict[str, Any]] = {}
        fixed_set = set(fixed_jobs)
        for member in best_team:
            if not member:
                continue
            if callable(progress_callback) and progress_callback(member, "") is False:
                break
            replacement_best = best_score
            replacement_job = ""
            replacement_options: list[tuple[float, str, float]] = []
            legal_candidates: list[str] = []
            for job in candidate_jobs:
                if job == member:
                    continue
                if self._team_unique_job(job) in self._team_unique_job_set(best_team):
                    continue
                candidate = [job if item == member else item for item in best_team]
                if self._team_has_real_duplicate(candidate):
                    continue
                if not self._extreme_team_has_required_roles(candidate, require_tank, require_healer):
                    continue
                legal_candidates.append(job)

            if callable(pre_score_func) and legal_candidates:
                member_family = self.store.job_family(self._team_real_job(member), "")
                same_family_rows: list[tuple[float, str]] = []
                mode_rows: list[tuple[float, str]] = []
                cheap_rows: list[tuple[float, str]] = []
                for job in legal_candidates:
                    candidate = tuple(job if item == member else item for item in best_team)
                    try:
                        cheap_value = as_number(pre_score_func(candidate))
                    except Exception:
                        cheap_value = 0.0
                    cheap_rows.append((cheap_value, job))
                    if self.store.job_family(self._team_real_job(job), "") == member_family:
                        same_family_rows.append((cheap_value, job))
                    if self._extreme_job_matches_mode(job, team_mode):
                        mode_rows.append((cheap_value, job))
                cheap_rows.sort(reverse=True, key=lambda item: item[0])
                same_family_rows.sort(reverse=True, key=lambda item: item[0])
                mode_rows.sort(reverse=True, key=lambda item: item[0])
                picked: list[str] = []

                def pick(job: str) -> None:
                    if job and job not in picked:
                        picked.append(job)

                if fast_mode:
                    candidate_limit = max(0, int(max_candidates_per_member))
                    for _value, job in cheap_rows[:candidate_limit]:
                        pick(job)
                    if candidate_limit > 0:
                        for _value, job in same_family_rows[:max(0, int(max_same_family_candidates))]:
                            pick(job)
                        if self._extreme_mode_has_preference(team_mode) and mode_rows:
                            pick(mode_rows[0][1])
                    legal_candidates = picked
                else:
                    legal_candidates = [job for _value, job in cheap_rows]

            preliminary_rows: list[tuple[float, str, tuple[str, ...]]] = []
            for job in legal_candidates:
                if callable(progress_callback) and progress_callback(member, job) is False:
                    return grades
                candidate = tuple(job if item == member else item for item in best_team)
                preliminary_rows.append((score_func(candidate), job, candidate))

            comparison_best_score = best_score
            scored_rows = preliminary_rows
            if callable(refine_score_func) and preliminary_rows:
                refined_limit = max(0, int(max_refined_candidates_per_member))
                ranked_preliminary = sorted(
                    preliminary_rows,
                    key=lambda item: item[0],
                    reverse=True,
                )
                refine_jobs = {
                    job
                    for _value, job, _candidate in ranked_preliminary[:refined_limit]
                }
                refined_base = max(
                    0.0,
                    as_number(refined_best_score, best_score),
                )
                preliminary_scale = (
                    refined_base / best_score
                    if best_score > 1e-9
                    else 1.0
                )
                scored_rows = []
                for preliminary_value, job, candidate in preliminary_rows:
                    if job in refine_jobs:
                        if callable(progress_callback) and progress_callback(member, job) is False:
                            return grades
                        candidate_score = refine_score_func(candidate)
                    else:
                        # Keep non-finalists visible as a normalized rough
                        # estimate without paying for another synchronized
                        # eight-person replay.
                        candidate_score = preliminary_value * preliminary_scale
                    scored_rows.append((candidate_score, job, candidate))
                comparison_best_score = refined_base

            replacement_best = comparison_best_score
            for candidate_score, job, _candidate in scored_rows:
                gain_percent = (
                    (candidate_score / comparison_best_score - 1.0) * 100
                    if comparison_best_score > 0
                    else 0.0
                )
                member_matches_mode = self._extreme_job_matches_mode(member, team_mode)
                job_matches_mode = self._extreme_job_matches_mode(job, team_mode)
                enough_to_break_mode = (
                    not self._extreme_mode_has_preference(team_mode)
                    or not member_matches_mode
                    or job_matches_mode
                    or gain_percent > 10.0
                )
                mode_recovery_within_threshold = (
                    self._extreme_mode_has_preference(team_mode)
                    and not member_matches_mode
                    and job_matches_mode
                    and gain_percent >= -10.0
                )
                is_useful_replacement = candidate_score > comparison_best_score + 1e-6
                close_replacement = abs(gain_percent) <= 10.0
                if (
                    (is_useful_replacement and enough_to_break_mode)
                    or mode_recovery_within_threshold
                    or close_replacement
                ):
                    replacement_options.append((candidate_score, job, gain_percent))
                    if candidate_score > replacement_best:
                        replacement_best = candidate_score
                        replacement_job = job
            ratio = comparison_best_score / replacement_best if replacement_best > 0 else 1.0
            if ratio >= 0.98:
                grade = "S"
            elif ratio >= 0.94:
                grade = "A"
            elif ratio >= 0.88:
                grade = "B"
            elif ratio >= 0.80:
                grade = "C"
            else:
                grade = "D"
            replacement_options.sort(key=lambda item: item[0], reverse=True)
            replacement_text = "无"
            if replacement_options:
                replacement_text = "、".join(f"{job}({gain:+.1f}%)" for _score, job, gain in replacement_options)
            role = "固定" if member in fixed_set else ("配装1职业" if member == current_job else "队员")
            grades[member] = {
                "grade": grade,
                "ratio": ratio,
                "role": role,
                "replacement": replacement_job or "无",
                "replacement_options": replacement_text,
                "loss": max(0.0, replacement_best - comparison_best_score),
            }
        return grades

    def _team_extreme_job_breakdown(
        self,
        job: str,
        attrs: dict[str, float],
        base_output: float,
        base_element_percent: float = 0.0,
        include_boss: bool = True,
        include_base_element: bool = False,
        base_neutral_percent: float = 0.0,
    ) -> dict[str, float]:
        if base_output <= 0:
            return {"total_multiplier": 0.0}
        profile = self._team_job_attack_profile(job)
        attack = profile["attack"]
        element = profile["element"]
        attack_percent = attack_increase_for_channel(attrs, attack)
        flat_attack = fixed_attack_for_channel(attrs, attack)
        amplified_flat = skill_percent_amplified_flat_attack(attrs, attack)
        if flat_attack >= 0:
            amplified_flat = min(max(0.0, amplified_flat), flat_attack)
        else:
            amplified_flat = max(min(0.0, amplified_flat), flat_attack)
        post_percent_flat = flat_attack - amplified_flat
        attack_multiplier = max(
            0.0,
            (
                (base_output + amplified_flat) * (1 + attack_percent / 100)
                + post_percent_flat
            )
            / base_output,
        )
        final_bonus = max(0.0, display_value(attrs, "最终%") + display_value(attrs, "最终面板%"))
        final_multiplier = 1 + final_bonus / 100
        element_multiplier = 1.0
        mixed_weights = self._extreme_mixed_element_weights(job)
        element_attack = 0.0
        reduction = 0.0
        resist_multiplier = 1.0
        neutral_before = max(0.0, base_neutral_percent)
        neutral_after = max(0.0, neutral_before + display_value(attrs, '无属性%'))
        neutral_multiplier = (100.0 + neutral_after) / (100.0 if include_base_element else 100.0 + neutral_before)
        if mixed_weights:
            weighted_element = 0.0
            weighted_combined = 0.0
            for mixed_element, weight in mixed_weights.items():
                weight_value = max(0.0, as_number(weight))
                if weight_value <= 0:
                    continue
                if mixed_element in {"火", "水", "光", "暗"}:
                    current_element_attack = display_value(attrs, f"{mixed_element}攻%")
                    base_element_attack = max(0.0, base_element_percent)
                    current_reduction = max(0.0, display_value(attrs, f"减{mixed_element}抗%") + display_value(attrs, "减全抗%"))
                    if include_base_element:
                        current_element_multiplier = 1 + max(0.0, base_element_attack + current_element_attack) / 100
                    else:
                        current_element_multiplier = (
                            1 + max(0.0, base_element_attack + current_element_attack) / 100
                        ) / max(0.01, 1 + base_element_attack / 100)
                    current_resist_multiplier = 1.0
                    if include_boss:
                        base_resist = as_number(getattr(self, "_extreme_element_resists", {}).get(mixed_element, 0.0))
                        base_resist_multiplier = max(0.01, 1 - base_resist / 100)
                        current_resist_multiplier = max(0.0, 1 - (base_resist - current_reduction) / 100) / base_resist_multiplier
                    element_attack += (base_element_attack + current_element_attack) * weight_value
                    reduction += current_reduction * weight_value
                else:
                    current_element_multiplier = 1.0 if mixed_element == '不适用' else neutral_multiplier
                    current_resist_multiplier = 1.0
                weighted_element += weight_value * current_element_multiplier
                weighted_combined += weight_value * current_element_multiplier * current_resist_multiplier
            element_multiplier = weighted_element if weighted_element > 0 else 1.0
            resist_multiplier = weighted_combined / max(0.01, element_multiplier)
        elif element == '不适用':
            element_multiplier = 1.0
        elif element:
            element_attack = display_value(attrs, f"{element}攻%")
            if include_base_element:
                element_attack += base_element_percent
            element_multiplier = 1 + max(0.0, element_attack) / 100
        else:
            element_attack = neutral_after
            element_multiplier = neutral_multiplier
        amp_percent = amplification_for_channel(attrs, attack)
        amp_multiplier = 1 + amp_percent / 100
        crit_multiplier = 1.0
        if include_boss:
            # These labels are aliases for the same already-resolved channel;
            # adding them would count one effect more than once.
            crit_down = max(
                0.0,
                display_value(attrs, "减致命抵抗%"),
                display_value(attrs, "减致命一击抵抗%"),
                display_value(attrs, "减暴击抵抗%"),
            )
            crit_multiplier = self._extreme_crit_multiplier(crit_down)
        if include_boss and element in {'火','水','光','暗'} and not mixed_weights:
            base_resist = as_number(getattr(self, "_extreme_element_resists", {}).get(element, 0.0))
            reduction = max(
                0.0,
                display_value(attrs, f"减{element}抗%")
                + display_value(attrs, "减全抗%"),
            )
            resist_multiplier = max(0.0, 1 - (base_resist - reduction) / 100)
        defense_multiplier = 1.0
        if include_boss:
            defense_down = defense_reduction_for_attack(attrs, attack)
            monster_defense = as_number(getattr(self, "_extreme_monster_defense", self.build.get("monster_defense", 0)))
            reduced_defense = max(0.0, monster_defense * (1 - defense_down / 100))
            defense_multiplier = max(0.0, 1 - BuildCalculator.defense_percent(reduced_defense) / 100)
        total_multiplier = attack_multiplier * final_multiplier * element_multiplier * amp_multiplier * crit_multiplier * resist_multiplier * defense_multiplier
        return {
            "attack_percent": attack_percent,
            "flat_attack": flat_attack,
            "skill_percent_amplified_flat_attack": amplified_flat,
            "final_bonus": final_bonus,
            "element_attack": element_attack,
            "amp_percent": amp_percent,
            "crit_resist_down": crit_down if include_boss else 0.0,
            "resist_down": reduction if include_boss and element else 0.0,
            "defense_down": defense_down if include_boss else 0.0,
            "attack_multiplier": attack_multiplier,
            "final_multiplier": final_multiplier,
            "element_multiplier": element_multiplier,
            "amp_multiplier": amp_multiplier,
            "crit_multiplier": crit_multiplier,
            "resist_multiplier": resist_multiplier,
            "defense_multiplier": defense_multiplier,
            "total_multiplier": total_multiplier,
        }

    def _team_extreme_job_multiplier(
        self,
        job: str,
        attrs: dict[str, float],
        base_output: float,
        base_element_percent: float = 0.0,
        include_boss: bool = True,
        include_base_element: bool = False,
        base_neutral_percent: float = 0.0,
    ) -> float:
        return as_number(self._team_extreme_job_breakdown(
            job,
            attrs,
            base_output,
            base_element_percent,
            include_boss=include_boss,
            include_base_element=include_base_element,
            base_neutral_percent=base_neutral_percent,
        ).get("total_multiplier"))

    def _extreme_crit_multiplier(self, crit_resist_reduction_percent: float) -> float:
        crit_percent = min(90.0, max(0.0, as_number(getattr(self, "_extreme_base_crit_percent", 0.0))))
        crit_damage_percent = min(100.0, max(0.0, as_number(getattr(self, "_extreme_base_crit_damage_percent", 0.0))))
        crit_resist_value = max(0.0, as_number(getattr(self, "_extreme_crit_resist_value", 0.0)))
        effective_resist_value = crit_resist_value * (1 - min(100.0, max(0.0, crit_resist_reduction_percent)) / 100)
        crit_resist_percent = min(round(effective_resist_value / 433, 1), 90)
        effective_crit_rate = (crit_percent / 100) * max(0.0, 1 - crit_resist_percent / 100)
        crit_damage = 2 + crit_damage_percent / 100
        return 1 + effective_crit_rate * (crit_damage - 1)

    def _extreme_mixed_element_profile(self, job: str, summary: dict[str, float] | None = None) -> dict[str, Any]:
        job_name = self._team_real_job(job)
        if job_name != "曜":
            return {}
        cache = getattr(self, "_extreme_skill_element_weight_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._extreme_skill_element_weight_cache = cache
        if summary is None:
            try:
                _rows, summary = self.calculator.calculate(self._extreme_converted_build_for_job(job_name))
            except Exception:
                summary = getattr(self, "_extreme_clean_base_summary", {})
        signature = (
            job_name,
            round(as_number(summary.get("physical_average")), 2),
            round(as_number(summary.get("magic_average")), 2),
            as_number(summary.get('无属性%')),
            round(as_number(getattr(self, "_extreme_base_element_percent_value", 42.0)), 4),
            tuple((element, round(as_number(getattr(self, "_extreme_element_resists", {}).get(element, 0.0)), 4)) for element in ("光", "暗")),
            tuple(self._selected_skill_rows_for_job(job_name)),
        )
        if signature in cache:
            return dict(cache[signature])
        base_element_percent = max(0.0, as_number(getattr(self, "_extreme_base_element_percent_value", 42.0), 42.0))
        rows = self._selected_skill_rows_for_job(job_name)
        if not rows:
            rows = [(skill, level, None, None) for _score, _required_level, skill, level in self._auto_skill_dps_candidates_for_job(job_name, summary)]
        raw_totals = {"光": 0.0, "暗": 0.0, "无": 0.0}
        weighted_totals = {"光": 0.0, "暗": 0.0, "无": 0.0}
        element_resists = getattr(self, "_extreme_element_resists", {})
        for skill, level, cooldown, multiplier in rows:
            raw_score, element = self._skill_dps_for_element_weight(
                job_name,
                skill,
                level,
                summary,
                base_element_percent,
                cooldown=cooldown,
                multiplier=multiplier,
                apply_element=False,
            )
            if raw_score <= 0:
                continue
            key = element if element in {"光", "暗", "不适用"} else "无"
            raw_totals[key] = raw_totals.get(key, 0.0) + raw_score
            weighted_score = raw_score
            if key in {"光", "暗"}:
                element_attack = max(base_element_percent, as_number(summary.get(f"{key}攻%")))
                resist_multiplier = max(0.0, 1 - as_number(element_resists.get(key, 0.0)) / 100)
                weighted_score *= (1 + max(0.0, element_attack) / 100) * resist_multiplier
            elif key != '不适用':
                weighted_score *= 1 + max(0.0, as_number(summary.get('无属性%'))) / 100
            weighted_totals[key] = weighted_totals.get(key, 0.0) + weighted_score
        weighted_total = sum(weighted_totals.values())
        raw_total = sum(raw_totals.values())
        if weighted_total <= 0 or raw_total <= 0:
            profile = {"weights": {"光": 0.5, "暗": 0.5, "无": 0.0}, "raw_weights": {"光": 0.5, "暗": 0.5, "无": 0.0}, "correction": 1.0}
        else:
            weights = {key: value / weighted_total for key, value in weighted_totals.items() if value > 1e-9}
            raw_weights = {key: value / raw_total for key, value in raw_totals.items() if value > 1e-9}
            profile_element = self._team_job_attack_profile(job_name).get("element") or "光"
            profile_attack = max(base_element_percent, as_number(summary.get(f"{profile_element}攻%"))) if profile_element in {"火", "水", "光", "暗"} else 0.0
            profile_resist = max(0.0, 1 - as_number(element_resists.get(profile_element, 0.0)) / 100) if profile_element in {"火", "水", "光", "暗"} else 1.0
            pure_profile_total = raw_total * (1 + max(0.0, profile_attack) / 100) * profile_resist
            profile = {
                "weights": weights,
                "raw_weights": raw_weights,
                "correction": weighted_total / pure_profile_total if pure_profile_total > 0 else 1.0,
            }
        cache[signature] = dict(profile)
        return dict(profile)

    def _extreme_mixed_element_weights(self, job: str, summary: dict[str, float] | None = None) -> dict[str, float]:
        return dict(self._extreme_mixed_element_profile(job, summary).get("weights", {}))

    def _extreme_mixed_element_correction(self, job: str, summary: dict[str, float]) -> float:
        return max(0.0, as_number(self._extreme_mixed_element_profile(job, summary).get("correction"), 1.0))

    @staticmethod
    def _extreme_provider_sort_key(provider: Any) -> tuple[str, str]:
        text = norm(provider)
        job = re.sub(r"^队员\d+\s*", "", text).strip()
        return job, text

    def _extreme_member_sort_key(self, member: Any) -> tuple[str, str]:
        text = norm(member)
        return norm(self._team_real_job(text)), text
