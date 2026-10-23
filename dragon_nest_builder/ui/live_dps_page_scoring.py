from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *

class LiveDpsScoringMixin:
    @staticmethod
    def _team_score_grade_colors(grade: str) -> tuple[str, str, str]:
        if grade == "SSS":
            return "#9a5a00", "#fff3d6", "#e7bd68"
        if grade in {"SS", "S"}:
            return "#0f7f95", "#e8f6f8", "#9dd7e1"
        if grade == "A":
            return "#25744a", "#eaf6ee", "#a8d5b7"
        if grade == "B":
            return "#446a8b", "#edf4fa", "#b9cee0"
        if grade == "C":
            return "#8a641e", "#faf4e7", "#dfcc9f"
        if grade == "D":
            return "#8b3f45", "#faecee", "#dfb3b7"
        return "#5b626b", "#f1f3f5", "#c7ccd1"


    def _team_score_default_boss_preset_name(
        self, groups: tuple[_BossStatisticsGroup, ...]
    ) -> str:
        if self._team_score_selected_boss_key == "__all__":
            selected_names = [group.name for group in groups]
        else:
            selected_name = self._team_score_boss_names.get(
                self._team_score_selected_boss_key, ""
            )
            selected_names = [selected_name] if selected_name else []

        matches = [
            matched
            for matched in (
                _team_score_match_boss_preset(name) for name in selected_names
            )
            if matched
        ]
        if self._team_score_selected_boss_key != "__all__":
            return matches[0] if matches else _TEAM_SCORE_DEFAULT_BOSS_PRESET

        editor_build = getattr(self.timeline_editor, "build", {})
        configured = _team_score_match_boss_preset(
            editor_build.get("boss") if isinstance(editor_build, dict) else ""
        )
        if configured and configured in matches:
            return configured
        return matches[0] if matches else _TEAM_SCORE_DEFAULT_BOSS_PRESET


    def _sync_team_score_boss_preset_selector(
        self, groups: tuple[_BossStatisticsGroup, ...]
    ) -> str:
        automatic = self._team_score_default_boss_preset_name(groups)
        self._team_score_auto_boss_preset_name = automatic
        auto_index = self.team_score_boss_preset_combo.findData("__auto__")
        if auto_index >= 0:
            self.team_score_boss_preset_combo.setItemText(
                auto_index, f"自动：{automatic}"
            )
        return self._team_score_resolved_boss_preset_name()


    def _team_score_resolved_boss_preset_name(self) -> str:
        selected = norm(self._team_score_selected_boss_preset_key)
        if selected in BOSS_PRESETS:
            return selected
        automatic = norm(self._team_score_auto_boss_preset_name)
        return (
            automatic
            if automatic in BOSS_PRESETS
            else _TEAM_SCORE_DEFAULT_BOSS_PRESET
        )


    def _sync_team_score_boss_selector(
        self, snapshot: CombatSnapshot
    ) -> frozenset[str] | None:
        revision = self.collector.aggregator.revision
        if self._boss_stats_groups_revision == revision:
            groups = self._boss_stats_groups
        else:
            groups = _boss_statistics_groups(snapshot)
            self._boss_stats_groups = groups
            self._boss_stats_groups_revision = revision
        signature = tuple(
            (
                group.identity,
                group.name,
                group.max_hp,
                tuple(sorted(group.target_ids)),
                group.first_ms,
                group.phase_windows,
            )
            for group in groups
        )
        if signature != self._team_score_boss_signature:
            previous = self._team_score_selected_boss_key
            options: dict[str, frozenset[str]] = {}
            option_names: dict[str, str] = {}
            option_windows: dict[str, tuple[tuple[float, float], ...]] = {}
            rows: list[tuple[str, str]] = []
            for index, group in enumerate(groups, start=1):
                key = f"boss:{group.identity[0]}:{group.identity[1]}"
                if key in options:
                    key = f"{key}:{index}"
                options[key] = group.target_ids
                option_names[key] = group.name
                option_windows[key] = group.phase_windows
                label = group.name
                if group.max_hp > 0:
                    label = f"{label}（{_compact_number(group.max_hp)} HP）"
                rows.append((label, key))
            self._team_score_boss_options = options
            self._team_score_boss_names = option_names
            self._team_score_boss_windows = option_windows
            if previous != "__all__" and previous not in options:
                previous = "__all__"
            self._team_score_selected_boss_key = previous
            self.team_score_boss_combo.blockSignals(True)
            self.team_score_boss_combo.clear()
            self.team_score_boss_combo.addItem("全部主要 Boss", "__all__")
            for label, key in rows:
                self.team_score_boss_combo.addItem(label, key)
            selected_index = self.team_score_boss_combo.findData(previous)
            self.team_score_boss_combo.setCurrentIndex(max(0, selected_index))
            self.team_score_boss_combo.setEnabled(bool(rows))
            self.team_score_boss_combo.blockSignals(False)
            self._team_score_boss_signature = signature

        self._sync_team_score_boss_preset_selector(groups)

        if self._team_score_selected_boss_key == "__all__":
            target_ids = frozenset(
                target_id
                for values in self._team_score_boss_options.values()
                for target_id in values
            )
        else:
            target_ids = self._team_score_boss_options.get(
                self._team_score_selected_boss_key, frozenset()
            )
        return target_ids or None


    def _team_score_display_windows(
        self,
        output_windows: tuple[tuple[float, float], ...] = (),
    ) -> tuple[tuple[float, float], ...]:
        if self._team_score_selected_boss_key == "__all__":
            snapshot = self._latest_snapshot
            if snapshot is None:
                return ()
            all_phase_windows = tuple(
                window
                for windows in self._team_score_boss_windows.values()
                for window in windows
            )
            target_ids = frozenset(
                target_id
                for values in self._team_score_boss_options.values()
                for target_id in values
            )
            factual_output_windows = (
                tuple(output_windows)
                or self.score_engine._output_windows(
                    snapshot, target_ids or None
                )
            )
            start, end = _timeline_display_bounds(
                snapshot, factual_output_windows or all_phase_windows
            )
            return ((start, end),) if end > start else ()
        return _merge_seconds_windows(
            (max(0.0, start - _TEAM_SCORE_PREP_CONTEXT_SECONDS), end)
            for start, end in self._team_score_boss_windows.get(
                self._team_score_selected_boss_key, ()
            )
        )


    def _team_score_specialty_windows(self) -> tuple[tuple[float, float], ...]:
        """Return full selected Boss phases for healer/T duty evaluation."""

        if self._team_score_selected_boss_key == "__all__":
            source = (
                window
                for windows in self._team_score_boss_windows.values()
                for window in windows
            )
        else:
            source = iter(
                self._team_score_boss_windows.get(
                    self._team_score_selected_boss_key, ()
                )
            )
        rows = sorted(
            (max(0.0, float(start)), max(float(start), float(end)))
            for start, end in source
            if float(end) > float(start)
        )
        merged: list[list[float]] = []
        for start, end in rows:
            if not merged or start > merged[-1][1] + 1e-6:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        return tuple((start, end) for start, end in merged)


    def _team_score_scope_label(self, boss_key: str | None = None) -> str:
        key = norm(boss_key or self._team_score_selected_boss_key) or "__all__"
        if key == "__all__":
            return "全部主要 Boss"
        return self._team_score_boss_names.get(key, "所选 Boss")


    def _team_score_boss_changed(self, _index: int) -> None:
        selected = norm(self.team_score_boss_combo.currentData()) or "__all__"
        if selected == self._team_score_selected_boss_key:
            return
        self._team_score_selected_boss_key = selected
        self._team_score_revision = -1
        self._team_score_refresh_pending = True
        scope_label = self._team_score_scope_label(selected)
        message = f"正在计算 {scope_label} 的全队评分，当前结果暂未更新..."
        self.team_score_title.setText(message)
        self._begin_view_progress(3, message)
        self._team_score_refresh_timer.start()


    def _team_score_boss_preset_changed(self, _index: int) -> None:
        selected = (
            norm(self.team_score_boss_preset_combo.currentData()) or "__auto__"
        )
        if selected == self._team_score_selected_boss_preset_key:
            return
        self._team_score_selected_boss_preset_key = selected
        self._team_score_revision = -1
        self._team_score_refresh_pending = True
        boss_name = self._team_score_resolved_boss_preset_name()
        self.team_score_title.setText(f"正在按{boss_name}参数重新计算全队评分...")
        self._begin_view_progress(3, f"正在按{boss_name}参数重新计算全队评分...")
        self._team_score_refresh_timer.start()


    def _team_score_glyph_options(
        self, job: str
    ) -> list[tuple[str, dict[str, Any]]]:
        allowed_effects = {
            "伤害增加",
            "冷却缩减",
            "增益持续",
            "动作速度增加",
        }
        options: list[tuple[str, dict[str, Any]]] = []
        seen: set[tuple[str, str, str, float]] = set()
        for entry in self.store.skill_glyphs(self._team_score_real_job_name(job)):
            skill = norm(entry.get("skill"))
            effects = entry.get("effects", {})
            if not skill or not isinstance(effects, dict):
                continue
            for raw_effect, raw_options in effects.items():
                effect = _team_score_glyph_effect_key(raw_effect)
                if effect not in allowed_effects or not isinstance(raw_options, list):
                    continue
                for raw_option in raw_options:
                    if not isinstance(raw_option, dict):
                        continue
                    value = max(0.0, as_number(raw_option.get("value")))
                    if value <= 0:
                        continue
                    grade = norm(raw_option.get("grade"))
                    option = " ".join(
                        filter(None, [grade, f"{value:g}%"])
                    )
                    row = {
                        "skill": skill,
                        "effect": effect,
                        "option": option,
                        "value": value,
                    }
                    key = _team_score_glyph_row_key(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    label = f"{skill} · {effect}+{value:g}%"
                    if grade:
                        label += f"（{grade}）"
                    options.append((label, row))
        return sorted(
            options,
            key=lambda item: (
                canonical_skill(item[1].get("skill")),
                _team_score_glyph_effect_key(item[1].get("effect")),
                -as_number(item[1].get("value")),
            ),
        )


    def _team_score_glyph_job_for_score(self, score: PlayerScore) -> str:
        return self._team_score_real_job_name(score.rotation_job or score.job)


    def _team_score_glyph_rows_from_controls(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for combo in getattr(self, "team_score_glyph_combos", []):
            row = combo.currentData()
            if isinstance(row, dict) and _team_score_glyph_row_key(row)[0]:
                rows.append(copy.deepcopy(row))
        return rows


    def _populate_team_score_glyph_controls(
        self, score: PlayerScore | None
    ) -> None:
        combos = getattr(self, "team_score_glyph_combos", [])
        if not combos:
            return
        self._team_score_glyph_loading = True
        try:
            if score is None:
                self._team_score_glyph_pending_job = ""
                self.team_score_glyph_editor_title.setText("评分技能纹章：请选择队员")
                for combo in combos:
                    combo.clear()
                    combo.addItem("不装备", None)
                    combo.setEnabled(False)
                self.team_score_glyph_apply_button.setEnabled(False)
                self.team_score_glyph_reset_button.setEnabled(False)
                return

            job = self._team_score_glyph_job_for_score(score)
            self._team_score_glyph_pending_job = job
            options = self._team_score_glyph_options(job)
            override = self._team_score_glyph_overrides_by_job.get(job)
            if override is not None:
                selected_rows = copy.deepcopy(override)
                mode = "手动"
            else:
                plan = self._team_score_theory_plans.get(score.source_id, {})
                selected_rows = [
                    copy.deepcopy(row)
                    for row in plan.get("_score_glyphs", ())
                    if isinstance(row, dict)
                ]
                mode = "自动"
            self.team_score_glyph_editor_title.setText(
                f"{job}评分技能纹章（{mode}）"
            )
            for slot, combo in enumerate(combos):
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("不装备", None)
                for label, row in options:
                    combo.addItem(label, copy.deepcopy(row))
                selected = selected_rows[slot] if slot < len(selected_rows) else None
                selected_key = _team_score_glyph_row_key(selected)
                selected_index = 0
                if selected_key[0]:
                    for index in range(1, combo.count()):
                        if _team_score_glyph_row_key(combo.itemData(index)) == selected_key:
                            selected_index = index
                            break
                    else:
                        skill, effect, _option, value = selected_key
                        combo.addItem(
                            f"{skill} · {effect}+{value:g}%（当前）",
                            copy.deepcopy(selected),
                        )
                        selected_index = combo.count() - 1
                combo.setCurrentIndex(selected_index)
                combo.setEnabled(True)
                combo.blockSignals(False)
            self.team_score_glyph_apply_button.setEnabled(False)
            self.team_score_glyph_reset_button.setEnabled(override is not None)
            self.team_score_glyph_editor_hint.setStyleSheet(
                "color:#62798a; border:none; background:transparent;"
            )
            self.team_score_glyph_editor_hint.setText(
                "修改后点击“应用纹章”统一重算；自动方案默认不使用幽灵之魂持续纹章。"
            )
        finally:
            self._team_score_glyph_loading = False


    def _team_score_glyph_selection_changed(self, _index: int) -> None:
        if self._team_score_glyph_loading:
            return
        rows = self._team_score_glyph_rows_from_controls()
        skill_keys = [canonical_skill(row.get("skill")) for row in rows]
        duplicate = len(skill_keys) != len(set(skill_keys))
        self.team_score_glyph_apply_button.setEnabled(
            bool(self._team_score_glyph_pending_job) and not duplicate
        )
        if duplicate:
            self.team_score_glyph_editor_hint.setStyleSheet(
                "color:#b44444; border:none; background:transparent;"
            )
            self.team_score_glyph_editor_hint.setText("同一个技能只能装备1块纹章。")
        else:
            self.team_score_glyph_editor_hint.setStyleSheet(
                "color:#9a5b17; border:none; background:transparent;"
            )
            self.team_score_glyph_editor_hint.setText(
                "纹章选择尚未应用；点击“应用纹章”后重新计算全队评分。"
            )


    def _invalidate_team_score_glyph_results(self, message: str) -> None:
        self._team_score_theory_signature = None
        self._team_score_theory_plans = {}
        self._team_score_theory_jobs = ()
        self._team_score_theory_plan_cache.clear()
        self._team_score_build_override_cache.clear()
        self._team_score_rows_cache.clear()
        self._team_score_revision = -1
        self._team_score_rendered_boss_key = ""
        self._team_score_refresh_pending = True
        self.team_score_title.setText(message)
        self._begin_view_progress(3, message)
        self._team_score_refresh_timer.start(0)


    def _apply_team_score_glyphs(self) -> None:
        job = norm(self._team_score_glyph_pending_job)
        if not job:
            return
        rows = self._team_score_glyph_rows_from_controls()
        skill_keys = [canonical_skill(row.get("skill")) for row in rows]
        if len(skill_keys) != len(set(skill_keys)):
            self._team_score_glyph_selection_changed(-1)
            return
        self._team_score_glyph_overrides_by_job[job] = copy.deepcopy(rows[:4])
        self.team_score_glyph_apply_button.setEnabled(False)
        self.team_score_glyph_reset_button.setEnabled(True)
        self._invalidate_team_score_glyph_results(
            f"正在按{job}手动技能纹章重新计算全队评分..."
        )


    def _reset_team_score_glyphs(self) -> None:
        job = norm(self._team_score_glyph_pending_job)
        if not job:
            return
        self._team_score_glyph_overrides_by_job.pop(job, None)
        self.team_score_glyph_apply_button.setEnabled(False)
        self.team_score_glyph_reset_button.setEnabled(False)
        self._invalidate_team_score_glyph_results(
            f"正在恢复{job}自动技能纹章并重新计算全队评分..."
        )


    def _run_pending_team_score_refresh(self) -> None:
        if self._team_score_refresh_running:
            # Boss/preset change handlers set the pending flag themselves.
            # A stale zero-delay timer firing while processEvents() is used by
            # the planner must not manufacture a second full score pass.
            return
        revision = self.collector.aggregator.revision
        snapshot, should_cache = _team_score_event_snapshot_for_refresh(
            self._latest_snapshot,
            self._event_snapshot,
            self._event_snapshot_revision,
            revision,
            lambda: self.collector.snapshot(include_events=True),
        )
        if snapshot is None:
            self._finish_view_progress("score")
            return
        if should_cache:
            self._event_snapshot = snapshot
            self._event_snapshot_revision = revision
        self._latest_snapshot = snapshot
        try:
            self._fill_team_scores(snapshot, revision)
        except Exception as exc:
            self._team_score_theory_error = repr(exc)
            self._team_score_revision = -1
            self._team_score_rendered_boss_key = ""
            self._team_score_rows = ()
            self.team_score_table.setRowCount(0)
            self.team_score_title.setText(
                f"{self._team_score_scope_label()} 评分失败，请重新选择 Boss 后重试。\n"
                f"错误：{exc}"
            )
        finally:
            self._finish_view_progress("score")


    def _fill_team_scores(self, snapshot: CombatSnapshot, revision: int) -> None:
        if self._team_score_refresh_running:
            self._team_score_refresh_pending = True
            return
        self._team_score_refresh_running = True
        self._team_score_refresh_pending = False
        requested_boss_key = self._team_score_selected_boss_key
        requested_boss_preset_name = self._team_score_resolved_boss_preset_name()
        try:
            self._advance_view_progress(
                "score", 44, "正在确定主要 Boss 输出区间与参评队员..."
            )
            boss_target_ids = self._sync_team_score_boss_selector(snapshot)
            requested_boss_key = self._team_score_selected_boss_key
            requested_boss_preset_name = (
                self._team_score_resolved_boss_preset_name()
            )
            boss_preset = BOSS_PRESETS.get(
                requested_boss_preset_name,
                BOSS_PRESETS[_TEAM_SCORE_DEFAULT_BOSS_PRESET],
            )
            self.score_engine.set_boss_profile(
                requested_boss_preset_name, boss_preset
            )
            timeline_events, _fallback_count = self._timeline_events_for_snapshot(
                snapshot
            )
            score_cast_events = _timeline_score_cast_events(
                timeline_events,
                self.store,
            )
            axis_digest = hashlib.sha256()
            for event in score_cast_events:
                axis_digest.update(
                    repr(
                        (
                            event.source_id,
                            event.timestamp_ms,
                            event.skill_id,
                            event.skill_name,
                            event.packet_kind,
                        )
                    ).encode("utf-8", errors="surrogatepass")
                )
                axis_digest.update(b"\0")
            actual_axis_signature: tuple[object, ...] = (
                len(score_cast_events),
                score_cast_events[0].timestamp_ms if score_cast_events else -1,
                score_cast_events[-1].timestamp_ms if score_cast_events else -1,
                axis_digest.hexdigest(),
            )
            score_snapshot = (
                replace(snapshot, skill_cast_events=score_cast_events)
                if score_cast_events
                else snapshot
            )
            glyph_override_signature = tuple(
                sorted(
                    (
                        job,
                        tuple(_team_score_glyph_row_key(row) for row in rows),
                    )
                    for job, rows in self._team_score_glyph_overrides_by_job.items()
                )
            )
            editor = self.timeline_editor
            cache_value = getattr(editor, "_team_practical_cache_value", None)
            build_context = getattr(editor, "build", {})
            if callable(cache_value):
                build_context = cache_value(build_context)
            disk_cache_key = ""
            if (
                not self._capture_active
                and self._team_score_capture_path is not None
                and self._team_score_capture_path.is_file()
            ):
                try:
                    disk_cache_key = team_score_cache_key(
                        self._team_score_capture_path,
                        (
                            requested_boss_key,
                            requested_boss_preset_name,
                            tuple(sorted(boss_target_ids or ())),
                            actual_axis_signature,
                            glyph_override_signature,
                            repr(build_context),
                        ),
                    )
                except OSError:
                    disk_cache_key = ""
            disk_cache_hit = False
            if (
                revision != self._team_score_revision
                or requested_boss_key != self._team_score_rendered_boss_key
                or requested_boss_preset_name
                != self._team_score_rendered_boss_preset_name
                or actual_axis_signature
                != self._team_score_actual_axis_signature
            ) and disk_cache_key:
                cached_score = self._team_score_disk_cache.load(disk_cache_key)
                if cached_score is not None:
                    cached_rows, cached_glyphs = cached_score
                    self._team_score_rows = cached_rows
                    self._team_score_theory_plans = {
                        source_id: {"_score_glyphs": copy.deepcopy(rows)}
                        for source_id, rows in cached_glyphs.items()
                    }
                    self._team_score_theory_signature = (
                        "disk-score-cache",
                        disk_cache_key,
                    )
                    self._team_score_revision = revision
                    self._team_score_actual_axis_signature = actual_axis_signature
                    self._team_score_rendered_boss_key = requested_boss_key
                    self._team_score_rendered_boss_preset_name = (
                        requested_boss_preset_name
                    )
                    disk_cache_hit = True
                    self._advance_view_progress(
                        "score", 89, "已恢复相同记录的完整评分缓存..."
                    )
            if (
                not disk_cache_hit
                and (
                    revision != self._team_score_revision
                or requested_boss_key != self._team_score_rendered_boss_key
                or requested_boss_preset_name
                != self._team_score_rendered_boss_preset_name
                or actual_axis_signature
                != self._team_score_actual_axis_signature
                )
            ):
                self._advance_view_progress(
                    "score",
                    52,
                    f"正在按{requested_boss_preset_name}参数生成理论轴...",
                )
                theory_plans = self._build_team_score_theory_plans(
                    score_snapshot,
                    boss_target_ids,
                    requested_boss_preset_name,
                )
                if self._team_score_theory_error:
                    self._advance_view_progress(
                        "score",
                        72,
                        f"{self._team_score_scope_label(requested_boss_key)} 的精确理论轴失败，"
                        "正在使用当前 Boss 的安全回退轴...",
                    )
                self._advance_view_progress(
                    "score", 76, "理论轴完成，正在计算输出与辅助完成率..."
                )
                if (
                    requested_boss_key != self._team_score_selected_boss_key
                    or requested_boss_preset_name
                    != self._team_score_resolved_boss_preset_name()
                ):
                    self._team_score_refresh_pending = True
                    return
                rows_cache_key = (
                    revision,
                    requested_boss_key,
                    requested_boss_preset_name,
                    self._team_score_theory_signature,
                    actual_axis_signature,
                )
                rows = self._team_score_rows_cache.get(rows_cache_key)
                if rows is None:
                    def score_progress(
                        completed: int, total: int, job: str
                    ) -> None:
                        value = 76 + int(12 * completed / max(1, total))
                        self._advance_view_progress(
                            "score",
                            value,
                            f"正在计算输出与辅助完成率 {completed}/{total}（{job}）...",
                        )
                        QApplication.processEvents()

                    rows = self.score_engine.score(
                        score_snapshot,
                        theory_plans=theory_plans,
                        boss_target_ids=boss_target_ids,
                        specialty_windows=self._team_score_specialty_windows(),
                        death_windows=(
                            (
                                (
                                    0.0,
                                    _snapshot_recording_end_seconds(
                                        score_snapshot
                                    ),
                                ),
                            )
                            if requested_boss_key == "__all__"
                            else self._team_score_specialty_windows()
                        ),
                        # An empty shared timeline is still authoritative.
                        # Passing None would make the score engine build a
                        # second damage-derived occurrence axis that the team
                        # timeline does not display.
                        actual_timeline_events=score_cast_events,
                        progress_callback=score_progress,
                    )
                    self._team_score_rows_cache[rows_cache_key] = rows
                    if disk_cache_key:
                        glyphs = {
                            source_id: [
                                copy.deepcopy(row)
                                for row in plan.get("_score_glyphs", ())
                                if isinstance(row, dict)
                            ]
                            for source_id, plan in theory_plans.items()
                            if isinstance(plan, dict)
                        }
                        try:
                            self._team_score_disk_cache.save(
                                disk_cache_key,
                                rows,
                                glyphs,
                            )
                        except OSError:
                            pass
                    while len(self._team_score_rows_cache) > 8:
                        self._team_score_rows_cache.pop(
                            next(iter(self._team_score_rows_cache))
                        )
                self._advance_view_progress(
                    "score", 89, "评分完成，正在更新职业列表和评分明细..."
                )
                if (
                    requested_boss_key != self._team_score_selected_boss_key
                    or requested_boss_preset_name
                    != self._team_score_resolved_boss_preset_name()
                ):
                    self._team_score_refresh_pending = True
                    return
                self._team_score_rows = rows
                self._team_score_revision = revision
                self._team_score_actual_axis_signature = actual_axis_signature
                self._team_score_rendered_boss_key = requested_boss_key
                self._team_score_rendered_boss_preset_name = (
                    requested_boss_preset_name
                )
        finally:
            self._team_score_refresh_running = False
            if (
                self._team_score_refresh_pending
                or requested_boss_key != self._team_score_selected_boss_key
                or requested_boss_preset_name
                != self._team_score_resolved_boss_preset_name()
            ):
                self._team_score_refresh_pending = False
                self._team_score_refresh_timer.start(0)
        rows = self._team_score_rows
        if (
            self._team_score_rendered_boss_key == "__all__"
            and requested_boss_key == "__all__"
        ):
            self._queue_team_score_sync(rows)
        self._advance_view_progress(
            "score", 93, "正在渲染评分列表与当前职业时间轴..."
        )
        selected = self._team_score_selected_source_id
        if not selected and rows:
            selected = rows[0].source_id
            self._team_score_selected_source_id = selected
        display = self._player_display_details(snapshot)
        self.team_score_table.blockSignals(True)
        self.team_score_table.setUpdatesEnabled(False)
        self.team_score_table.setRowCount(len(rows))
        selected_row = -1
        for row_index, score in enumerate(rows):
            display_name, display_job = display.get(
                score.source_id, (score.name, score.job)
            )
            score_display_job = score.rotation_job or display_job or score.job
            specialty_text = (
                f"{score.specialty_score:.1f}"
                if score.specialty_score is not None
                else "-"
            )
            values = [
                score.grade,
                display_name,
                score_display_job,
                score.role,
                f"{score.total_score:.1f}",
                f"{score.output_score:.1f}",
                f"{score.support_score:.1f}",
                f"{specialty_text} / -{score.survival_penalty:.1f}",
            ]
            foreground, background, _border = self._team_score_grade_colors(
                score.grade
            )
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (1, 2, 3)
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                item.setData(Qt.UserRole, score.source_id)
                if column in (0, 4):
                    item.setForeground(QColor(foreground))
                    item.setBackground(QColor(background))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.team_score_table.setItem(row_index, column, item)
            if score.source_id == selected:
                selected_row = row_index
        self.team_score_table.setUpdatesEnabled(True)
        if selected_row >= 0:
            self.team_score_table.selectRow(selected_row)
        self.team_score_table.blockSignals(False)
        self._show_team_score_detail(selected)


    def _team_score_selection_changed(self) -> None:
        selection = self.team_score_table.selectionModel().selectedRows()
        if not selection:
            return
        item = self.team_score_table.item(selection[0].row(), 0)
        source_id = str(item.data(Qt.UserRole) or "") if item else ""
        if not source_id:
            return
        self._team_score_selected_source_id = source_id
        self._show_team_score_detail(source_id)


    def _team_score_output_skill_filter_changed(self) -> None:
        source_id = getattr(self, "_team_score_detail_source_id", "")
        score = next(
            (row for row in self._team_score_rows if row.source_id == source_id),
            None,
        )
        if score is not None:
            self._fill_team_score_evidence(score)


    def _show_team_score_detail(self, source_id: str) -> None:
        score = next(
            (row for row in self._team_score_rows if row.source_id == source_id),
            None,
        )
        if score is None:
            self._populate_team_score_glyph_controls(None)
            self.team_score_grade.setText("-")
            self.team_score_title.setText("暂无可评分的队员记录")
            self.team_score_detail_table.setRowCount(0)
            self.team_score_skill_table.setRowCount(0)
            self.team_score_window_table.setRowCount(0)
            self.team_score_support_table.setRowCount(0)
            self.team_score_specialty_table.setRowCount(0)
            self.team_score_specialty_summary.setText("")
            self.team_score_detail_tabs.setTabVisible(
                self.team_score_specialty_tab_index, False
            )
            self.team_score_improvement_summary.setText("")
            self.team_score_improvement_table.setRowCount(0)
            self.team_score_notes.setText("")
            self.team_score_output_timeline.set_comparison_timeline(
                1.0, [], [], "暂无评分时间轴。"
            )
            self.team_score_output_timeline.set_effect_windows([])
            self.team_score_output_timeline.set_metric_series([])
            self.team_score_output_skill_filter.set_options([])
            self.team_score_support_timeline.set_comparison_timeline(
                1.0, [], [], "暂无辅助效果时间轴。"
            )
            return
        self._team_score_detail_source_id = score.source_id
        display_name, display_job = (score.name, score.job)
        if self._latest_snapshot is not None:
            display_name, display_job = self._player_display_details(
                self._latest_snapshot
            ).get(score.source_id, (score.name, score.job))
        score_display_job = score.rotation_job or display_job or score.job
        foreground, background, border = self._team_score_grade_colors(score.grade)
        self.team_score_grade.setText(score.grade)
        self.team_score_grade.setStyleSheet(
            f"font-size:30px; font-weight:900; color:{foreground}; "
            f"background:{background}; border:1px solid {border}; border-radius:6px;"
        )
        total_formula = (
            f"输出{score.output_weight * 100:.0f}% + "
            f"辅助{score.support_weight * 100:.0f}%"
        )
        if score.specialty_weight > 0:
            total_formula += f" + 职业特色{score.specialty_weight * 100:.0f}%"
        is_tank_score = score.role == "T/辅助"
        penalty_label = "T承伤扣分" if is_tank_score else "承伤扣分"
        if is_tank_score:
            penalty_explanation = (
                f"当前评分范围确认死亡 {score.survival_detail.death_count} 次；"
                f"承伤 {_compact_number(score.survival_detail.incoming_damage)}、"
                f"{score.survival_detail.hit_count} Hit；超出团队机制基线的生命损失和"
                "可验证操作损失均按普通职业的三分之一扣分，确认死亡每次扣10分"
            )
        else:
            penalty_explanation = (
                f"所选Boss阶段收到 {score.survival_detail.hit_count} Hit，"
                f"合并为 {score.survival_detail.incident_count} 次受击事件；"
                f"其中全队机制 {score.survival_detail.team_mechanic_incident_count} 次、"
                f"点名机制 {score.survival_detail.targeted_mechanic_incident_count} 次、"
                f"可规避单人伤害 {score.survival_detail.avoidable_incident_count} 次、"
                f"证据不足 {score.survival_detail.uncertain_incident_count} 次；"
                f"完全由机制基线吸收 {score.survival_detail.free_incidents:.0f} 次；"
                f"总承伤 {_compact_number(score.survival_detail.incoming_damage)}，"
                f"团队机制基线承伤 {_compact_number(score.survival_detail.team_mechanic_baseline_damage)}，"
                f"超出基线承伤 {_compact_number(score.survival_detail.avoidable_damage)}；"
                f"标准生命 {_compact_number(score.survival_detail.reference_hp) if score.survival_detail.reference_hp > 0 else '未知'}，"
                f"每分钟团队机制基线 {score.survival_detail.team_mechanic_baseline_hp_loss_per_minute * 100:.1f}%、"
                f"超额生命损失 {score.survival_detail.avoidable_hp_loss_per_minute * 100:.1f}%，"
                f"承伤严重度扣 {score.survival_detail.damage_penalty:.2f}；"
                f"受击后标准伤害损失 {_compact_number(score.survival_detail.interruption_loss)}"
                f"（理论总量 {score.survival_detail.interruption_loss_ratio * 100:.1f}%），"
                f"验证到操作影响 {score.survival_detail.verified_interruption_count} 次，"
                f"操作损失扣 {score.survival_detail.interruption_penalty:.2f}；"
                f"普通承伤合计封顶 {score.survival_detail.non_death_penalty_cap:.2f}；"
                f"确认死亡 {score.survival_detail.death_count} 次，扣 {score.survival_detail.death_penalty:.2f}"
            )
        self.team_score_title.setText(
            f"{display_name} · {score_display_job} · {score.role}\n"
            f"总分 {score.total_score:.1f}（{total_formula}，"
            f"{penalty_label} -{score.survival_penalty:.1f}） · "
            f"评分范围：{self._team_score_scope_label(self._team_score_rendered_boss_key)}"
        )
        boss_preset_name = self._team_score_resolved_boss_preset_name()
        boss_preset = BOSS_PRESETS.get(
            boss_preset_name,
            BOSS_PRESETS[_TEAM_SCORE_DEFAULT_BOSS_PRESET],
        )
        detail_rows: list[tuple[str, str, str]] = [
            (
                "评分范围",
                self._team_score_scope_label(self._team_score_rendered_boss_key),
                "评分、理论轴、实际覆盖和输出区间均只使用该范围的数据",
            ),
            *(
                [(
                    "曜形态",
                    score.rotation_job,
                    f"查克拉治愈术在所选 Boss 可输出区间覆盖达到"
                    f"{_YAO_MEDITATION_ROLE_THRESHOLD * 100:.0f}%时判定为打坐曜，"
                    "否则判定为输出曜",
                )]
                if score.rotation_job in {"输出曜", "打坐曜"}
                else []
            ),
            *(
                [("药剂定位", score.rotation_job, score.role_reason)]
                if score.rotation_job in {"辅助药剂", "输出药剂"}
                else []
            ),
            ("定位依据", score.role, score.role_reason),
            (
                "评分权重",
                total_formula,
                f"本角色计分明细理论收益 {score.support_index:.1f}%，"
                f"按全职业最高理论收益同比例换算为辅助权重 {score.support_weight * 100:.1f}%；"
                "未出现在计分明细中的可学技能、EX普通版备选和同名不可叠加效果不参与权重",
            ),
            (
                "实际计分构成",
                f"输出 {score.output_contribution:.1f} + 辅助 {score.support_contribution:.1f}"
                + (
                f" + 特色 {score.specialty_contribution:.1f}"
                    if score.specialty_weight > 0
                    else ""
                )
                + f" - {penalty_label} {score.survival_penalty:.1f}",
                "输出与辅助的原始分可超过100；超额计分点会挤压另一项上限，"
                "两项合计不超过共享预算，奶/T的40分职责完成率不参与挤压；"
                f"{penalty_label}最后从总分中扣除",
            ),
            (
                penalty_label,
                f"-{score.survival_penalty:.2f}",
                penalty_explanation,
            ),
            (
                "评分 Boss 参数",
                boss_preset_name,
                f"防御 {int(as_number(boss_preset.get('monster_defense')))}；"
                f"致命抵抗 {int(as_number(boss_preset.get('crit_resist')))}；"
                f"四属性抗性 {as_number(boss_preset.get('element_resist')):.1f}%",
            ),
            ("输出评分", f"{score.output_score:.1f}", "标准伤害完成率70% + 爆发/加速窗口兑现率30%"),
            (
                "标准伤害完成率",
                f"{score.theory_damage_completion:.1f}%",
                f"实战 {_compact_number(score.actual_standard_power)} / "
                f"理论 {_compact_number(score.theory_standard_power)}",
            ),
            (
                "高价值技能完成率（诊断）",
                f"{score.high_value_usage:.1f}%",
                "理论输出占前70%的技能完成情况，不再单独计权",
            ),
            (
                "爆发/加速窗口兑现率",
                f"{score.burst_usage:.1f}%",
                "精神爆发与时间加速窗口内实际标准化输出/理论轴窗口输出，占输出评分30%；超过100%时按100%计分",
            ),
            (
                "辅助评分",
                f"{score.support_score:.1f}（基准100）",
                f"理论基准为100、不封顶；初始按{score.support_weight * 100:.1f}%换算，"
                "超过100的部分会挤压输出计分上限",
            ),
            (
                "分项加权收益完成率",
                f"{score.coverage_completion:.1f}%",
                "各效果的反事实实际收益/合法最优收益按理论收益加权，不封顶",
            ),
            (
                "总辅助收益完成率",
                f"{score.contribution_completion:.1f}%",
                "同一团队标准伤害流中，反事实实际收益 / 合法最优收益；重复来源共享贡献",
            ),
        ]
        if score.specialty_score is not None:
            detail_rows.append(
                (
                    score.specialty_label or "职业特色评分",
                    f"{score.specialty_score:.1f}%",
                    "作为独立职责完成率，最高计40分",
                )
            )
        self.team_score_detail_table.setRowCount(len(detail_rows))
        for row_index, values in enumerate(detail_rows):
            for column, value in enumerate(values):
                align = Qt.AlignLeft | Qt.AlignVCenter if column != 1 else Qt.AlignCenter
                item = self._item(value, align)
                if column == 2:
                    item.setToolTip(str(value))
                self.team_score_detail_table.setItem(row_index, column, item)
        self._populate_team_score_glyph_controls(score)
        self.team_score_notes.setText("；".join(score.notes))
        self._fill_team_score_specialty(score)
        self._fill_team_score_evidence(score)


    def _fill_team_score_specialty(self, score: PlayerScore) -> None:
        visible = score.specialty_score is not None
        self.team_score_detail_tabs.setTabVisible(
            self.team_score_specialty_tab_index, visible
        )
        if not visible:
            self.team_score_specialty_summary.setText("")
            self.team_score_specialty_table.setRowCount(0)
            return

        if score.healing_completion is not None:
            rescue = score.healing_rescue_detail
            danger_rescue = str(rescue.get("method", "")).startswith("danger-hp-recovery-v")
            shared_penalty = rescue.get("method") == "danger-hp-recovery-v4"
            episode_penalty = shared_penalty or rescue.get("method") == "danger-hp-recovery-v3"
            rescue_unscored = rescue.get("status") in ("unavailable", "no_demand", "no_opportunity", "not_required")
            legacy_neutral = (danger_rescue and not rescue.get("status")
                              and rescue.get("needed_percent") == 0)
            rescue_text = (
                "已及时恢复，无需重复施救" if rescue.get("status") == "not_required" else
                "无可评价救急机会" if rescue.get("status") == "no_opportunity" else
                "无救急需求" if rescue.get("status") == "no_demand"
                or (legacy_neutral and rescue.get("evaluated_players", 0) > 0)
                else "无法评价" if rescue_unscored or legacy_neutral
                or score.healing_rescue_completion is None
                else f"{score.healing_rescue_completion:.1f}%"
                + ("（估算）" if rescue.get("status") == "estimated" else "")
            )
            weight_text = (
                "有效治疗兑现率×80% + 常规减伤覆盖率×20% − 救急扣分（最多40特色分）"
                if episode_penalty else
                "救急项不计分；有效治疗兑现与常规减伤按5:2折算"
                if rescue_unscored else
                "有效治疗兑现率×50% + 救急完成率×30% + 常规减伤覆盖率×20%"
            )
            self.team_score_specialty_summary.setText(
                "奶职责完成率最高计40分，" + weight_text + "；"
                "其余60分按全职业辅助收益归一化结果分配辅助分上限，剩余为输出分。"
                + ("统计完整Boss阶段。救急按危险血量到60%安全线的实际恢复计算；准确血量优先，缺少时采用有依据的估算血量。"
                   if danger_rescue else "旧版救急口径，请重新评分以使用危险血量恢复规则。")
            )
            rows = [
                (
                    "奶职业特色分",
                    f"{score.healing_completion:.1f}%",
                    weight_text,
                ),
                (
                    "有效治疗兑现率",
                    f"{(score.healing_effective_realization or 0.0):.1f}%",
                    (
                        (
                            f"按实际恢复HP计分：本人恢复 {score.effective_healing:,}，"
                            f"可治疗失血 {rescue.get('healing_volume_healable_loss', 0):,.0f}，"
                            f"未恢复需求按奶人数均摊 {rescue.get('healing_volume_shared_unmet', 0):,.0f}；"
                            if rescue.get('healing_volume_method') == 'factual-hp-restored-v1'
                            else "缺少可量化血量证据，按施放证据兜底；"
                            if rescue.get('healing_volume_method') == 'cast-evidence-fallback-v1'
                            else "旧版按有效施放次数计分；"
                        ) + (
                            f"施放生效率 {score.healing_meaningful_casts}/"
                            f"{score.healing_evaluated_casts if score.healing_evaluated_casts is not None else score.healing_total_casts} 仅作诊断"
                        )
                    ),
                ),
                (
                    "本人救急责任兑现率（仅诊断）" if shared_penalty else "逐次救急平均完成率" if episode_penalty else "危险血量救急完成率" if danger_rescue else "高压力救急完成率" if score.healing_evaluated_casts is not None else "高压力治疗参与率",
                    rescue_text,
                    (rescue.get("evidence", "") if rescue_unscored else
                     "旧记录曾按中性100%计分，并非实际救急满分；请重新评分" if legacy_neutral else
                     f"血量≤{rescue['danger_hp_threshold']*100:g}%开始，恢复到60%结束；3秒内100%，3～8秒降至50%，之后保留50%"
                     if danger_rescue else "旧版按掉血较大的约35%三秒窗口评价"),
                ),
                (
                    "常规减伤覆盖率",
                    f"{(score.healing_damage_reduction_completion or 0.0):.1f}%",
                    "统计保护之盾、牺牲、查克拉治愈术/EX对队伍的平均覆盖；多奶按人数分摊，且不计入辅助收益",
                ),
                (
                    "危险血量事件" if danger_rescue else "高压力窗口",
                    str(rescue['episode_count'] if danger_rescue else score.healing_pressure_windows),
                    rescue['evidence'] if danger_rescue else "旧版治疗压力窗口",
                ),
                (
                    "有效治疗",
                    _compact_number(score.effective_healing),
                    "服务器发送的正向HP变化，仅统计实际队员目标，不包含召唤物",
                ),
                (
                    "过量治疗",
                    "暂不可计算",
                    "当前协议没有技能原始治疗量与治疗前HP，不能可靠反推；本项不参与评分",
                ),
            ]
            if danger_rescue:
                rows.extend([
                    ("累计救急恢复需求", f"{rescue['needed_percent']:.1f} 个百分点", "按每名队员最大HP归一化累计"),
                    ("本人恢复 / 时效折算", f"{rescue['recovered_percent']:.1f} / {rescue['weighted_percent']:.1f} 个百分点", "只统计恢复至60%安全线所需部分"),
                    ("救急评价队员 / 奶人数", f"{rescue['evaluated_players']}/{rescue['party_size']}；{rescue['healer_count']}名奶", f"其中{rescue.get('estimated_players', 0)}名使用估算血量；无可用血量者不纳入；起点未知的阶段只按50%权重"),
                ])
            if episode_penalty:
                rows.extend([
                    ("基础特色分", f"{rescue.get('base_score', 0):.2f}", "有效治疗80%＋常规减伤20%"),
                    ("救急扣分", "不扣分（" + rescue_text + "）" if rescue_unscored
                     else f"−{rescue.get('applied_penalty', 0):.2f} 特色分",
                     f"可评价{rescue.get('scored_episode_count', 0)}次；"
                     + ("每次全队共扣2×(1−团队完成率)，再按未完成责任分摊；及时救好不扣分；死亡期间不承担责任；全队合计最多40特色分；" if shared_penalty else "每次扣2×(1−完成率)，最多扣40特色分；")
                     + f"超过20次统一缩放；不足{rescue.get('min_fatal_window_seconds', 1):g}秒即死亡且无有效救急暂不扣分；总分折算乘40%"),
                ])
            if shared_penalty:
                rows.extend([
                    ("团队救急完成率", f"{rescue['team_completion']:.1f}%" if rescue.get('team_completion') is not None else "无法评价", "团队结果，不等同于本人的救急贡献"),
                    ("无需重复施救", str(rescue.get('not_required_episode_count', 0)), "队友已及时完成救急或已有及时恢复；不扣分，也不记作本人满分"),
                ])
        else:
            self.team_score_specialty_summary.setText(
                "T职责完成率最高计40分；减攻击、挑衅Debuff、蛋壳覆盖各占三分之一。"
                "减攻击按实际联合覆盖/理论轴联合覆盖计算，该项完成率最高100%；"
                "职责总分不超过40分；其余60分按全职业辅助收益"
                "归一化结果分配辅助分上限，剩余为输出分。"
            )
            rows = [
                (
                    "T职责完成率",
                    f"{(score.tank_completion or 0.0):.1f}%",
                    "减攻击、挑衅Debuff和蛋壳覆盖各占三分之一；职责计分最高40分",
                ),
            ]
            if score.tank_attack_down_coverage is not None:
                rows.extend([
                    (
                        "减攻击实际覆盖率",
                        f"{score.tank_attack_down_coverage:.1f}%",
                        "所选主要Boss有效战斗时间内，至少一个本人减攻击效果生效的区间并集",
                    ),
                    (
                        "减攻击理论覆盖率",
                        (
                            f"{score.tank_attack_down_theory_coverage:.1f}%"
                            if score.tank_attack_down_theory_coverage is not None
                            else "待确认"
                        ),
                        score.tank_attack_down_evidence,
                    ),
                ])
            rows.append((
                (
                    "减攻击覆盖完成率"
                    if score.tank_attack_down_coverage is not None
                    else "减攻击完成率（旧口径/待确认）"
                ),
                f"{(score.tank_attack_down_completion or 0.0):.1f}%",
                (
                    "实际联合覆盖/理论联合覆盖，最高100%"
                    if score.tank_attack_down_coverage is not None
                    else "旧记录未保存覆盖率，请重新评分"
                ),
            ))
            if score.tank_taunt_completion is not None:
                if score.tank_taunt_coverage is not None:
                    rows.append((
                        "挑衅实际覆盖率", f"{score.tank_taunt_coverage:.1f}%",
                        score.tank_taunt_evidence,
                    ))
                    rows.append((
                        "挑衅理论覆盖率",
                        (f"{score.tank_taunt_theory_coverage:.1f}%"
                         if score.tank_taunt_theory_coverage is not None else "待确认"),
                        "按实际等级、持续时间及冷却计算",
                    ))
                rows.append(
                    (
                        "挑衅覆盖完成率" if score.tank_taunt_coverage is not None else "挑衅完成率（旧口径/待确认）",
                        f"{score.tank_taunt_completion:.1f}%",
                        "实际覆盖/理论覆盖" if score.tank_taunt_coverage is not None else "旧记录未保存覆盖率，请重新评分",
                    )
                )
            if score.tank_shell_coverage is not None:
                rows.append((
                    "蛋壳实际覆盖率", f"{score.tank_shell_coverage:.1f}%",
                    f"按全队 {score.tank_shell_party_size} 人平均，未收到按 0 覆盖",
                ))
            if score.tank_shell_completion is not None:
                rows.append(
                    (
                        "蛋壳完成率",
                        f"{score.tank_shell_completion:.1f}%",
                        "护卫者姿势、范围炸弹EX等保护效果覆盖完成情况",
                    )
                )
            if score.tank_shell_theory_coverage is not None:
                rows.append((
                    "蛋壳理论轴覆盖率", f"{score.tank_shell_theory_coverage:.1f}%",
                    f"施放者理论轴中 {score.tank_shell_theory_casts} 次有效施放，重叠区间只算一次",
                ))
            if score.tank_shell_evidence:
                rows.append(("蛋壳理论依据", score.tank_shell_evidence,
                             "" if score.tank_shell_completion is not None
                             else "完成率暂不评价，该分项按中性处理"))

        self.team_score_specialty_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column, value in enumerate(values):
                self.team_score_specialty_table.setItem(
                    row_index,
                    column,
                    self._item(
                        value,
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column != 1
                        else Qt.AlignCenter,
                    ),
                )


    def _team_score_shared_actual_timeline(
        self, score: PlayerScore
    ) -> tuple[ScoreTimelineCast, ...]:
        """Slice the shared team-skill axis instead of recognizing casts again."""
        score_actual_timeline = tuple(score.actual_timeline)
        snapshot = self._latest_snapshot
        if snapshot is None:
            return score_actual_timeline

        phase_windows = self._team_score_specialty_windows()
        if not phase_windows:
            return score_actual_timeline
        axis_windows = _merge_seconds_windows(
            [
                (max(0.0, start - _TEAM_SCORE_PREP_CONTEXT_SECONDS), end)
                for start, end in phase_windows
            ]
        )

        selected_key = self._team_score_selected_boss_key
        if selected_key == "__all__":
            target_ids = frozenset(
                target_id
                for values in self._team_score_boss_options.values()
                for target_id in values
            )
        else:
            target_ids = self._team_score_boss_options.get(selected_key, frozenset())

        timeline_events, _fallback_count = self._timeline_events_for_snapshot(snapshot)
        source_events = tuple(
            event
            for event in timeline_events
            if event.source_id == score.source_id
            and self._timestamp_in_windows(event.timestamp_ms, axis_windows)
            and (
                isinstance(event, SkillCastEvent)
                or not target_ids
                or event.target_id in target_ids
            )
        )
        if not source_events:
            return score_actual_timeline

        occurrences = _build_timeline_occurrences(
            source_events,
            0,
            _LiveCastTimingResolver(
                self.store,
                event_platform_for(snapshot).player_buff_clear_events,
            ),
        )
        # This axis is filtered to one player, while action-speed buffs can be
        # team-wide. Reuse timings resolved from the complete team event stream
        # so personal and team speed effects stack correctly.
        observed_cast_timings: dict[
            tuple[str, int, str], tuple[float, float]
        ] = {}
        score_engine = getattr(self, "score_engine", None)
        if score_engine is not None:
            observed_cast_timings = score_engine._observed_cast_timings(snapshot)
        scored_by_key: dict[str, list[ScoreTimelineCast]] = defaultdict(list)
        for row in score_actual_timeline:
            scored_by_key[canonical_skill(row.skill_name)].append(row)
        for rows in scored_by_key.values():
            rows.sort(key=lambda row: row.seconds)
        matched_rows: set[tuple[str, float]] = set()

        result: list[ScoreTimelineCast] = []
        for occurrence in occurrences:
            event = occurrence.event
            skill_key = canonical_skill(event.skill_name)
            start = max(0.0, occurrence.start_seconds)
            end = max(start + 0.05, occurrence.end_seconds)
            action_speed = occurrence.action_speed_percent
            if isinstance(event, SkillCastEvent):
                global_timing = observed_cast_timings.get(
                    (event.source_id, event.timestamp_ms, skill_key)
                )
                if global_timing is not None:
                    cast_time, action_speed = global_timing
                    end = max(start + 0.05, start + max(0.0, cast_time))
            matched: ScoreTimelineCast | None = None
            if isinstance(event, SkillCastEvent):
                for candidate in scored_by_key.get(skill_key, ()):
                    marker = (skill_key, candidate.seconds)
                    if marker in matched_rows:
                        continue
                    if abs(candidate.seconds - start) <= 0.12:
                        matched = candidate
                        matched_rows.add(marker)
                        break
            if matched is not None:
                result.append(
                    ScoreTimelineCast(
                        seconds=start,
                        end_seconds=end,
                        skill_name=event.skill_name,
                        power=matched.power,
                        source=matched.source,
                        high_value=matched.high_value,
                        in_burst_window=matched.in_burst_window,
                        expected_duration=matched.expected_duration,
                        utilized_duration=matched.utilized_duration,
                        utilization_ratio=matched.utilization_ratio,
                        proportional_output=matched.proportional_output,
                        action_speed_percent=action_speed,
                        action_speed_output_multiplier=(
                            matched.action_speed_output_multiplier
                        ),
                    )
                )
                continue

            is_damage_activity = isinstance(event, CombatEvent)
            result.append(
                ScoreTimelineCast(
                    seconds=start,
                    end_seconds=end,
                    skill_name=event.skill_name,
                    power=0.0,
                    source=(
                        "全队技能轴持续出伤（不增加施放次数）"
                        if is_damage_activity
                        else "全队技能轴施放（当前区间不计输出评分）"
                    ),
                    in_burst_window=any(
                        window.start_seconds <= start <= window.end_seconds
                        for window in score.burst_windows
                    ),
                    expected_duration=max(0.0, end - start),
                    utilized_duration=max(0.0, end - start),
                    action_speed_percent=action_speed,
                )
            )
        return tuple(sorted(result, key=lambda row: (row.seconds, row.skill_name)))


    def _team_score_output_effect_rows(
        self,
        score: PlayerScore,
        display_windows: tuple[tuple[float, float], ...],
    ) -> list[dict[str, object]]:
        """Build one complete effect layer for the output score timeline."""

        def mapped_ranges(
            intervals: tuple[tuple[float, float], ...],
        ) -> tuple[tuple[float, float], ...]:
            if not display_windows:
                return intervals
            mapped, _elapsed = _compress_ranges_to_windows(
                intervals, display_windows, preserve_clock=True
            )
            return mapped

        score_engine = getattr(self, "score_engine", None)
        profile_job = score.rotation_job or score.job
        profile = (
            score_engine._job_damage_profile(profile_job)
            if score_engine is not None
            else {}
        )
        rows: list[dict[str, object]] = []
        for detail in score.support_details:
            category, color = _team_score_effect_category(
                detail.kind, detail.effect_key
            )
            # Numeric Boss modifiers are shown below as one effective actual
            # curve. Unknown purple states are not useful on an output axis.
            if category in {
                "team_amp",
                "team_resist",
                "team_crit_resist",
                "team_defense",
                "state",
            }:
                continue
            if category == "team_buff":
                catalog = self._live_buff_display_catalog.get(
                    _effect_skill_key(detail.skill_name), {}
                )
                if catalog:
                    relevant = _team_score_output_buff_relevant(
                        profile_job,
                        profile,
                        catalog.get("display", ""),
                        catalog.get("category", ""),
                    )
                else:
                    relevant = _team_score_output_effect_key_relevant(
                        profile_job, profile, detail.effect_key
                    )
                if not relevant:
                    continue
            effect_detail = (
                f"{detail.effect_key} {detail.magnitude:g}；"
                f"实战覆盖 {detail.actual_coverage * 100:.1f}%"
            )
            intervals = detail.actual_display_intervals or detail.actual_intervals
            for start, end in mapped_ranges(tuple(intervals)):
                rows.append(
                    {
                        "name": f"实战·{detail.skill_name}",
                        "start": start,
                        "end": end,
                        "category": category,
                        "color": color,
                        "lane_key": (
                            f"实战:{canonical_skill(detail.skill_name)}:"
                            f"{canonical_skill(detail.effect_key)}"
                        ),
                        "detail": effect_detail,
                    }
                )

        snapshot = self._latest_snapshot
        if snapshot is None:
            return rows

        support_skill_keys = {
            canonical_skill(detail.skill_name) for detail in score.support_details
        }
        raw_visible_buffs = tuple(
            row
            for row in snapshot.player_buff_series
            if row.target_id == score.source_id
            and _is_catalogued_player_buff(row, self._live_buff_rows_by_skill)
        )
        visible_personal_skill_keys: set[str] = set()

        def is_personal_buff(row: PlayerBuffSeriesSnapshot) -> bool:
            skill_key = _effect_skill_key(row.skill_name)
            catalog = self._live_buff_display_catalog.get(skill_key, {})
            declared_team = catalog.get("scope") == "team" or "团队" in str(
                row.status_kind
            )
            if not declared_team:
                declared_team = any(
                    str(item.get("实时范围", "")).strip() == "team"
                    or str(item.get("类别", "")).strip() == "团队buff"
                    for item in self._live_buff_rows_by_skill.get(skill_key, ())
                )
            return row.source_id == score.source_id and not declared_team

        for personal in (False, True):
            visible_buffs = _merge_player_buff_series_rows(
                tuple(
                    row
                    for row in raw_visible_buffs
                    if is_personal_buff(row) == personal
                ),
                self._live_buff_rows_by_skill,
            )
            for buff in visible_buffs:
                effect_text = self._player_buff_effect_text(buff)
                catalog = self._live_buff_display_catalog.get(
                    _effect_skill_key(buff.skill_name), {}
                )
                category_text = catalog.get("category", "") or buff.status_kind
                if not personal and not _team_score_output_buff_relevant(
                    profile_job, profile, effect_text, category_text
                ):
                    continue
                skill_key = canonical_skill(buff.skill_name)
                if personal:
                    visible_personal_skill_keys.add(skill_key)
                category = "team_personal" if personal else "team_buff"
                color = "#e79b45" if personal else "#46a77a"
                prefix = "个人" if personal else "团队"
                for start, end in mapped_ranges(tuple(buff.intervals)):
                    rows.append(
                        {
                            "name": f"{prefix}·{buff.skill_name}",
                            "start": start,
                            "end": end,
                            "category": category,
                            "color": color,
                            "lane_key": (
                                f"{prefix}:{skill_key}:"
                                f"{canonical_skill(effect_text)}"
                            ),
                            "detail": (
                                f"来源 {buff.job or buff.source_name or prefix}；"
                                f"{effect_text}"
                            ),
                        }
                    )

        selected_key = self._team_score_selected_boss_key
        if selected_key == "__all__":
            target_ids = frozenset(
                target_id
                for values in self._team_score_boss_options.values()
                for target_id in values
            )
        else:
            target_ids = self._team_score_boss_options.get(
                selected_key, frozenset()
            )
        grouped_debuffs: dict[
            tuple[str, str, str, str], list[tuple[float, float]]
        ] = defaultdict(list)
        debuff_rows: dict[tuple[str, str, str, str], BossEffectSnapshot] = {}
        for effect in event_platform_for(snapshot).boss_effects:
            if target_ids and effect.target_id not in target_ids:
                continue
            if (
                effect.source_id == score.source_id
                and canonical_skill(effect.skill_name) in support_skill_keys
            ):
                continue
            key = (
                canonical_skill(effect.skill_name),
                canonical_skill(effect.label),
                norm(effect.status_kind),
                norm(effect.source_id),
            )
            grouped_debuffs[key].extend(
                effect.intervals
                or ((effect.start_seconds, effect.end_seconds),)
            )
            debuff_rows.setdefault(key, effect)
        for key, intervals in grouped_debuffs.items():
            effect = debuff_rows[key]
            category, color = _team_score_effect_category(
                effect.status_kind, effect.label
            )
            # Amplification, resistance, critical resistance and defense use
            # aggregate percentage curves. Retain only useful non-numeric
            # states such as freeze here.
            if category != "team_freeze":
                continue
            merged = _merge_seconds_windows(intervals)
            for start, end in mapped_ranges(merged):
                rows.append(
                    {
                        "name": f"团队·{effect.skill_name or effect.label}",
                        "start": start,
                        "end": end,
                        "category": category,
                        "color": color,
                        "lane_key": (
                            f"团队:{canonical_skill(effect.skill_name)}:"
                            f"{canonical_skill(effect.label)}"
                        ),
                        "detail": (
                            f"来源 {effect.job or effect.source_name or '队友'}；"
                            f"{effect.label}"
                        ),
                    }
                )
        self._team_score_visible_personal_effect_keys = visible_personal_skill_keys
        return rows


    def _team_score_output_metric_rows(
        self,
        score: PlayerScore,
        display_windows: tuple[tuple[float, float], ...],
        duration: float,
    ) -> list[dict[str, object]]:
        metric_rows: list[dict[str, object]] = []
        snapshot = self._latest_snapshot
        if snapshot is not None:
            selected_key = self._team_score_selected_boss_key
            if selected_key == "__all__":
                target_ids = frozenset(
                    target_id
                    for values in self._team_score_boss_options.values()
                    for target_id in values
                )
            else:
                target_ids = self._team_score_boss_options.get(
                    selected_key, frozenset()
                )
            groups = tuple(
                group
                for group in self._boss_stats_groups
                if not target_ids or bool(group.target_ids & target_ids)
            )
            if not groups:
                groups = tuple(
                    group
                    for group in _boss_statistics_groups(snapshot)
                    if not target_ids or bool(group.target_ids & target_ids)
                )
            hp_windows = display_windows or ((0.0, max(1.0, duration)),)
            hp_cache_key = (
                id(snapshot),
                selected_key,
                tuple(hp_windows),
                tuple(group.identity for group in groups),
            )
            if hp_cache_key == self._team_score_hp_metric_cache_key:
                hp_segments = list(self._team_score_hp_metric_segments)
            else:
                _profile_name, fixed_references = resolve_fixed_boss_hp_profile(
                    (group.name for group in groups),
                    capture_hint=self.collector.status,
                    target_ids=(
                        target_id
                        for group in groups
                        for target_id in group.target_ids
                    ),
                )
                hp_rows = _compress_boss_hp_tracks_to_windows(
                    event_platform_for(snapshot).boss_hp_tracks,
                    groups,
                    hp_windows,
                    infer_from_damage=True,
                    completed_group_identities=_completed_boss_group_identities(
                        groups,
                        event_platform_for(snapshot).boss_hp_tracks,
                        terminal_skill_casts=event_platform_for(
                            snapshot
                        ).skill_cast_events,
                        fixed_boss_hp_references=fixed_references,
                    ),
                    fixed_boss_hp_references=fixed_references,
                    preserve_clock=True,
                )
                hp_segments = [
                    {"name": boss_name, "points": tuple(segment)}
                    for boss_name, _confidence, segments in hp_rows
                    for segment in segments
                    if segment
                ]
                self._team_score_hp_metric_cache_key = hp_cache_key
                self._team_score_hp_metric_segments = tuple(hp_segments)
            if hp_segments:
                metric_rows.append(
                    {
                        "name": "Boss血量",
                        "segments": hp_segments,
                        "minimum": 0.0,
                        "maximum": 100.0,
                        "suffix": "%",
                        "color": "#d45b67",
                        "detail": "悬停查看当前 Boss 与该时刻剩余血量",
                    }
                )

            curve_target_ids = target_ids or frozenset(
                target_id for group in groups for target_id in group.target_ids
            )
            if curve_target_ids:
                current_ms = round(
                    max(
                        snapshot.elapsed_seconds,
                        snapshot.dps_elapsed_seconds,
                        duration,
                    )
                    * 1000
                )
                effect_view = self.collector.aggregator.boss_effect_view(
                    curve_target_ids, current_ms=current_ms
                )
                if display_windows:
                    effect_view, _compressed_seconds = (
                        _compress_effect_view_to_windows(
                            effect_view,
                            display_windows,
                            preserve_clock=True,
                        )
                    )
                effect_series = dict(effect_view.series)
                score_engine = getattr(self, "score_engine", None)
                profile_job = score.rotation_job or score.job
                profile = (
                    score_engine._job_damage_profile(profile_job)
                    if score_engine is not None
                    else {}
                )
                for series_key, label, color in _team_score_output_curve_specs(
                    profile_job, profile
                ):
                    points = tuple(effect_series.get(series_key, ()))
                    peak = max((as_number(point[1]) for point in points), default=0.0)
                    if peak <= 1e-9:
                        continue
                    maximum = max(5.0, 5.0 * int((peak + 4.999999) / 5.0))
                    metric_rows.append(
                        {
                            "name": label,
                            "segments": (
                                {"name": label, "points": points},
                            ),
                            "minimum": 0.0,
                            "maximum": maximum,
                            "suffix": "%",
                            "color": color,
                            "detail": (
                                f"实战有效{label}；仅按{profile_job}实际受益的"
                                "攻击类型与属性显示"
                            ),
                        }
                    )

        score_points = _team_score_dynamic_output_points(
            score, duration, display_windows
        )
        score_minimum, score_maximum = _tight_curve_bounds(score_points)
        metric_rows.append(
            {
                "name": "累计输出盈亏",
                "segments": ({"name": score.name, "points": score_points},),
                "minimum": score_minimum,
                "maximum": score_maximum,
                "suffix": "%",
                "color": "#17859c",
                "positive_color": "#24965e",
                "negative_color": "#cf5b65",
                "delta_colors": True,
                "zero_line": True,
                "marker_threshold": 0.5,
                "detail": (
                    "（累计实战标准伤害－累计理论标准伤害）÷全轴理论标准伤害；"
                    "正值表示领先，负值表示落后，绿色表示盈亏改善，红色表示盈亏恶化"
                ),
            }
        )
        return metric_rows


    def _fill_team_score_evidence(self, score: PlayerScore) -> None:
        display_windows = self._team_score_display_windows(
            tuple(score.output_windows)
        )
        actual_display_timeline = self._team_score_shared_actual_timeline(score)
        skill_options = sorted(
            {
                str(row.skill_name): str(row.skill_name)
                for row in (*actual_display_timeline, *score.theory_timeline)
                if str(row.skill_name or "").strip()
            }.items(),
            key=lambda item: item[1],
        )
        self.team_score_output_skill_filter.set_options(skill_options)
        selected_skills = self.team_score_output_skill_filter.selected_values()
        duration = max(
            (
                row.end_seconds
                for row in (*actual_display_timeline, *score.theory_timeline)
            ),
            default=0.0,
        )
        if self._latest_snapshot is not None:
            duration = max(
                duration,
                self._latest_snapshot.elapsed_seconds,
                self._latest_snapshot.dps_elapsed_seconds,
            )
        duration = max(1.0, duration)

        def segment(row) -> dict[str, object]:
            flags: list[str] = [row.source]
            if row.power > 0 or not row.source.startswith("全队技能轴"):
                flags.append(f"标准伤害 {_compact_number(row.power)}")
            if row.action_speed_output_multiplier > 1.0001:
                flags.append(
                    f"动作速度 +{row.action_speed_percent:g}%，"
                    f"攻击次数倍率 ×{row.action_speed_output_multiplier:.3f}"
                )
            if row.proportional_output:
                flags.append(
                    f"持续 {row.utilized_duration:.2f}/{row.expected_duration:.2f}s，"
                    f"计入 {row.utilization_ratio * 100:.1f}%"
                )
            if row.high_value:
                flags.append("高价值技能")
            if row.in_burst_window:
                flags.append("位于爆发/Buff窗口")
            return {
                "skill": row.skill_name,
                "start": row.seconds,
                "end": row.end_seconds,
                "detail": "；".join(flags),
            }

        def display_segments(rows) -> list[dict[str, object]]:
            segments: list[dict[str, object]] = []
            for row in rows:
                if selected_skills and str(row.skill_name) not in selected_skills:
                    continue
                source = segment(row)
                mapped, _elapsed = _compress_ranges_to_windows(
                    ((row.seconds, row.end_seconds),),
                    display_windows,
                    preserve_clock=True,
                )
                if not display_windows:
                    segments.append(source)
                    continue
                for start, end in mapped:
                    result = dict(source)
                    result["start"] = start
                    result["end"] = end
                    segments.append(result)
            return segments

        # Boss selection filters rows but the axis retains full capture time.

        self.team_score_output_timeline.set_comparison_timeline(
            duration,
            display_segments(score.theory_timeline),
            display_segments(actual_display_timeline),
            "理论轴与实战轴均为空。",
        )
        output_effects = self._team_score_output_effect_rows(
            score, display_windows
        )
        for row in score.burst_windows:
            mapped, _elapsed = _compress_ranges_to_windows(
                ((row.start_seconds, row.end_seconds),),
                display_windows,
                preserve_clock=True,
            )
            ranges = mapped if display_windows else (
                (row.start_seconds, row.end_seconds),
            )
            for start, end in ranges:
                output_effects.append(
                    {
                        "name": row.label,
                        "start": start,
                        "end": end,
                        "category": "team_buff",
                        "color": "#46a77a",
                        "detail": (
                            f"实战标准伤害 {_compact_number(row.actual_power)} / "
                            f"理论 {_compact_number(row.theory_power)}，"
                            f"利用率 {row.completion:.1f}%"
                        ),
                    }
                )
        snapshot = self._latest_snapshot
        score_engine = getattr(self, "score_engine", None)
        personal_timing_skills = {
            "精确瞄准",
            "致残惩罚者",
            "幽灵复仇者",
            "幻影凝视",
        }
        if snapshot is not None and score_engine is not None:
            for skill, raw_start, raw_end, cooldown_factor, action_factor in (
                score_engine._combat_modifier_effect_windows(
                    snapshot, score.source_id
                )
            ):
                if skill not in personal_timing_skills:
                    continue
                if canonical_skill(skill) in getattr(
                    self, "_team_score_visible_personal_effect_keys", set()
                ):
                    continue
                mapped, _elapsed = _compress_ranges_to_windows(
                    ((raw_start, raw_end),),
                    display_windows,
                    preserve_clock=True,
                )
                ranges = mapped if display_windows else ((raw_start, raw_end),)
                effects: list[str] = []
                if cooldown_factor < 1.0:
                    effects.append(f"冷却缩减 {(1.0 - cooldown_factor) * 100:g}%")
                if action_factor > 1.0:
                    effects.append(f"动作速度 +{(action_factor - 1.0) * 100:g}%")
                detail = "；".join(effects) or "个人战斗增益"
                for start, end in ranges:
                    output_effects.append(
                        {
                            "name": f"个人·{skill}",
                            "start": start,
                            "end": end,
                            "category": "team_personal",
                            "color": "#e79b45",
                            "lane_key": f"个人:{canonical_skill(skill)}",
                            "detail": detail,
                        }
                    )
        self.team_score_output_timeline.set_effect_windows(output_effects)
        self.team_score_output_timeline.set_metric_series(
            self._team_score_output_metric_rows(
                score, display_windows, duration
            )
        )
        self.team_score_output_summary.setText(
            f"实战标准伤害 {_compact_number(score.actual_standard_power)} / "
            f"理论标准伤害 {_compact_number(score.theory_standard_power)}，"
            f"差距 {_compact_number(score.theory_standard_power - score.actual_standard_power)}"
            + (
                "；低伤害完成率豁免技能额外伤害 "
                + _compact_number(
                    sum(
                        detail.actual_power
                        for detail in score.skill_details
                        if detail.excluded_from_score
                    )
                )
                if any(detail.excluded_from_score for detail in score.skill_details)
                else ""
            )
            + (
                "；当前仅显示所选 Boss 阶段，空白区间保留原抓包时间"
                if (
                    display_windows
                    and self._team_score_selected_boss_key != "__all__"
                )
                else ""
            )
            + "；累计输出盈亏以全轴理论标准伤害为基准，绿色为改善，红色为恶化"
            + (
                f"；当前筛选 {len(selected_skills)} 个技能（仅影响时间轴显示）"
                if selected_skills
                else ""
            )
            + "；时间轴0秒为抓包开始"
        )

        self.team_score_skill_table.setUpdatesEnabled(False)
        self.team_score_skill_table.setRowCount(len(score.skill_details))
        for row_index, detail in enumerate(score.skill_details):
            skill_label = (
                f"{detail.skill_name}（低伤害完成率·"
                f"{'高价值' if detail.high_value else '低价值'}豁免·额外伤害）"
                if detail.excluded_from_score
                else detail.skill_name
            )
            values = [
                skill_label,
                "是" if detail.high_value else "否",
                str(detail.actual_count),
                str(detail.theory_count),
                _compact_number(detail.actual_power),
                _compact_number(detail.theory_power),
                (
                    f"{detail.actual_duration:.2f}/{detail.expected_duration:.2f}s "
                    f"({detail.duration_utilization * 100:.1f}%)"
                    if detail.proportional_output and detail.expected_duration > 0
                    else "-"
                ),
                f"{detail.completion:.1f}%",
                _compact_number(detail.actual_power - detail.theory_power),
            ]
            for column, value in enumerate(values):
                align = (
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column == 0
                    else Qt.AlignCenter
                )
                item = self._item(value, align)
                if column == 0:
                    SkillBurstTimelineWidget.apply_skill_item_color(
                        item, detail.skill_name
                    )
                if detail.excluded_from_score:
                    damage_percent = (
                        detail.actual_power / detail.theory_power * 100.0
                        if detail.theory_power > 0
                        else 0.0
                    )
                    item.setForeground(QColor("#7b8794"))
                    item.setToolTip(
                        f"实战标准伤害/理论标准伤害为 {damage_percent:.1f}%，低于10%，"
                        "不作为技能完成率要求，但仍完整显示在实际轴和理论轴；"
                        "已造成的实战伤害作为额外伤害计入实战标准总伤害。"
                        "每个职业最多豁免1个高价值技能和3个低价值技能；"
                        "同组多个候选时优先选择理论总伤害较高者。"
                    )
                if column == 1 and detail.high_value:
                    item.setForeground(QColor("#a44f12"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.team_score_skill_table.setItem(row_index, column, item)
        self.team_score_skill_table.setUpdatesEnabled(True)

        self.team_score_window_table.setRowCount(len(score.burst_windows))
        for row_index, detail in enumerate(score.burst_windows):
            display_range, _elapsed = _compress_ranges_to_windows(
                ((detail.start_seconds, detail.end_seconds),),
                display_windows,
                preserve_clock=True,
            )
            if display_windows and display_range:
                range_text = " / ".join(
                    f"{start:.2f}s - {end:.2f}s"
                    for start, end in display_range
                )
            else:
                range_text = (
                    f"{detail.start_seconds:.2f}s - {detail.end_seconds:.2f}s"
                )
            values = [
                detail.label,
                range_text,
                _compact_number(detail.actual_power),
                _compact_number(detail.theory_power),
                f"{detail.completion:.1f}%",
                _compact_number(detail.actual_power - detail.theory_power),
            ]
            for column, value in enumerate(values):
                self.team_score_window_table.setItem(
                    row_index,
                    column,
                    self._item(
                        value,
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column == 0
                        else Qt.AlignCenter,
                    ),
                )

        self.team_score_support_table.blockSignals(True)
        self.team_score_support_table.setUpdatesEnabled(False)
        self.team_score_support_table.setRowCount(len(score.support_details))
        for row_index, detail in enumerate(score.support_details):
            values = [
                detail.skill_name,
                detail.kind,
                detail.effect_key,
                f"{detail.magnitude:g}",
                f"{detail.actual_coverage * 100:.1f}%",
                f"{detail.theory_coverage * 100:.1f}%",
                (
                    f"实 {detail.effective_damage_coverage * 100:.1f}% / "
                    f"理 {detail.effective_damage_theory_coverage * 100:.1f}%"
                ),
                f"{detail.support_benefit_completion:.1f}%",
                (
                    f"{detail.score_weight:.1f}%"
                    if detail.included_in_score
                    else "不计分（<5%）"
                ),
                f"{detail.actual_contribution:.2f}",
                f"{detail.theory_contribution:.2f}",
            ]
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column in (0, 2)
                    else Qt.AlignCenter,
                )
                item.setData(Qt.UserRole, row_index)
                if column == 6:
                    item.setToolTip(
                        "同一实战伤害流中，分别落在实际效果轴和理论效果轴内的比例。"
                    )
                if column == 4:
                    hit_assumed_resist = detail.effect_key in {
                        "减火抗%", "减水抗%", "减光抗%", "减暗抗%"
                    }
                    item.setToolTip(
                        (
                            "减抗按真实技能Hit假定施加、每Hit刷新；"
                            "抓包状态未改写。" if hit_assumed_resist
                            else "统一结算后的有效覆盖率；"
                        )
                        + f"包内观察覆盖率 "
                        f"{detail.observed_coverage * 100:.1f}%。"
                        + (
                            f"{detail.effective_state}：{detail.effective_reason}"
                            if detail.effective_state
                            else ""
                        )
                    )
                if not detail.included_in_score:
                    item.setToolTip(
                        "该项完成率低于5%，保留明细用于核对，但不参与辅助评分。"
                    )
                self.team_score_support_table.setItem(row_index, column, item)
        self.team_score_support_table.setUpdatesEnabled(True)
        self.team_score_support_table.blockSignals(False)
        if score.support_details:
            self.team_score_support_table.selectRow(0)
            self._show_team_score_support_timeline(score, 0)
        else:
            self.team_score_support_timeline.set_comparison_timeline(
                duration, [], [], "该职业没有可评分的团队Buff或Debuff。"
            )
            self.team_score_support_timeline.set_metric_series([])
            self.team_score_support_summary.setText(
                "该职业默认加点中未识别到可评分的团队效果。"
            )

        improvement_rows = _team_score_improvement_rows(score)
        recoverable_output = (
            max(0.0, 100.0 - score.theory_damage_completion) * 0.70
            + max(0.0, 100.0 - score.burst_usage) * 0.30
        )
        support_gap = max(0.0, 100.0 - score.support_score)
        self.team_score_improvement_summary.setText(
            f"所选Boss范围内：当前输出分 {score.output_score:.1f}，"
            f"未完成项的公式缺口约 {recoverable_output:.2f}"
            "（可能已被其他超额输出抵消）；"
            f"当前辅助分 {score.support_score:.1f}，距基准100还有 {support_gap:.1f}。"
            "下表按影响优先级列出具体技能和效果；预计值用于排序，"
            "同一问题之间可能互相影响，不应直接相加。"
        )
        self.team_score_improvement_table.setUpdatesEnabled(False)
        self.team_score_improvement_table.setRowCount(len(improvement_rows))
        for row_index, row in enumerate(improvement_rows):
            values = [
                row["direction"],
                row["priority"],
                row["item"],
                row["current"],
                row["advice"],
                row["gain"],
            ]
            for column, value in enumerate(values):
                item = self._item(
                    value,
                    Qt.AlignLeft | Qt.AlignVCenter
                    if column not in (0, 1)
                    else Qt.AlignCenter,
                )
                item.setToolTip(str(value))
                if column == 0:
                    item.setForeground(
                        QColor("#176b8a")
                        if value == "输出分"
                        else QColor("#8a5b18")
                        if value == "辅助分"
                        else QColor("#475569")
                    )
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == 1:
                    item.setForeground(
                        QColor("#b33d49")
                        if value == "高"
                        else QColor("#a16616")
                        if value == "中"
                        else QColor("#527184")
                    )
                self.team_score_improvement_table.setItem(
                    row_index, column, item
                )
        self.team_score_improvement_table.resizeRowsToContents()
        self.team_score_improvement_table.setUpdatesEnabled(True)


    def _team_score_support_selection_changed(self) -> None:
        selection = self.team_score_support_table.selectionModel().selectedRows()
        if not selection:
            return
        source_id = getattr(self, "_team_score_detail_source_id", "")
        score = next(
            (row for row in self._team_score_rows if row.source_id == source_id),
            None,
        )
        if score is None:
            return
        item = self.team_score_support_table.item(selection[0].row(), 0)
        detail_index = int(item.data(Qt.UserRole)) if item is not None else -1
        self._show_team_score_support_timeline(score, detail_index)


    def _show_team_score_support_timeline(
        self, score: PlayerScore, detail_index: int
    ) -> None:
        if not 0 <= detail_index < len(score.support_details):
            return
        detail = score.support_details[detail_index]
        actual_intervals = (
            detail.actual_display_intervals or detail.actual_intervals
        )
        theory_intervals = (
            detail.theory_display_intervals or detail.theory_intervals
        )
        display_windows = self._team_score_display_windows(
            tuple(score.output_windows)
        )
        if display_windows:
            actual_intervals, duration = _compress_ranges_to_windows(
                tuple(actual_intervals), display_windows, preserve_clock=True
            )
            theory_intervals, _elapsed = _compress_ranges_to_windows(
                tuple(theory_intervals), display_windows, preserve_clock=True
            )
        else:
            duration = 0.0
        duration = max(
            max(1.0, duration),
            max(
                (
                    end
                    for start, end in (
                        *actual_intervals,
                        *theory_intervals,
                    )
                ),
                default=0.0,
            ),
        )
        if self._latest_snapshot is not None:
            duration = max(
                duration,
                self._latest_snapshot.elapsed_seconds,
                self._latest_snapshot.dps_elapsed_seconds,
            )
        theory_segments = [
            {
                "skill": detail.skill_name,
                "start": start,
                "end": end,
                "detail": (
                    f"理论 {detail.effect_key} {detail.magnitude:g}；"
                    f"连续覆盖 {end - start:.1f}s"
                ),
            }
            for start, end in theory_intervals
        ]
        actual_segments = [
            {
                "skill": detail.skill_name,
                "start": start,
                "end": end,
                "detail": (
                    f"实际 {detail.effect_key} {detail.magnitude:g}；"
                    f"连续覆盖 {end - start:.1f}s"
                ),
            }
            for start, end in actual_intervals
        ]
        self.team_score_support_timeline.set_comparison_timeline(
            max(1.0, duration),
            theory_segments,
            actual_segments,
            "该效果没有识别到实际或理论覆盖区间。",
        )
        self.team_score_support_timeline.set_effect_windows([])
        hp_segments = list(
            getattr(self, "_team_score_hp_metric_segments", ())
        )
        self.team_score_support_timeline.set_metric_series(
            [
                {
                    "name": "Boss血量",
                    "segments": hp_segments,
                    "minimum": 0.0,
                    "maximum": 100.0,
                    "suffix": "%",
                    "color": "#d45b67",
                    "detail": "所选Boss阶段的剩余血量",
                }
            ]
            if hp_segments
            else []
        )
        self.team_score_support_summary.setText(
            f"{detail.skill_name} · {detail.effect_key} {detail.magnitude:g}："
            f"{'命中假定覆盖' if detail.effect_key in {'减火抗%', '减水抗%', '减光抗%', '减暗抗%'} else '实际覆盖'} "
            f"{detail.actual_coverage * 100:.1f}% / "
            f"包内观察 {detail.observed_coverage * 100:.1f}% / "
            f"理论轴覆盖 {detail.theory_coverage * 100:.1f}%，"
            f"有效伤害覆盖率 实际 {detail.effective_damage_coverage * 100:.1f}% / "
            f"理论 {detail.effective_damage_theory_coverage * 100:.1f}%，"
            f"辅助收益完成率 {detail.support_benefit_completion:.1f}%，"
            + (
                f"结算状态 {detail.effective_state}（{detail.effective_reason}），"
                if detail.effective_state
                else ""
            )
            + (
                f"本职业反事实理论收益权重 {detail.score_weight:.1f}%"
                if detail.included_in_score
                else "完成率低于5%，本项仅保留明细、不参与辅助评分"
            )
            + (
                f"；基准单次持续 {detail.expected_duration:g}s"
                if detail.expected_duration > 0
                else ""
            )
            + (
                "。时间条仅显示所选 Boss 阶段，评分仍只统计 Boss 可输出区间。"
                if display_windows
                else "。时间条显示完整生命周期，评分只统计 Boss 可输出区间。"
            )
        )
