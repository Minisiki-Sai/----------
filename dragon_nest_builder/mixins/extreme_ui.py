from __future__ import annotations

from ..shared import *
from ..mechanism_timeline import (
    MechanismTimelineEditor,
    active_seconds as mechanism_active_seconds,
    blocked_seconds as mechanism_blocked_seconds,
    lightweight_cdr_buff_schedule,
    lightweight_team_buff_schedule,
    load_mechanism_templates,
    normalize_mechanism_intervals,
    save_mechanism_templates,
)
from ..extreme_schemes import (
    EXTREME_TEAM_TIMELINE_REVISION,
    load_extreme_team_schemes,
    save_extreme_team_scheme,
    save_extreme_team_schemes,
)

BuildEditor = None


class ExtremeUiMixin:
    @staticmethod
    def _extreme_result_breakdown(
        rows: Iterable[dict[str, Any]],
        timelines: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build display-only output and skill breakdowns from completed axes."""
        row_list = [dict(row) for row in rows if isinstance(row, dict)]
        timeline_by_job = {
            norm(timeline.get("job")): timeline
            for timeline in timelines
            if isinstance(timeline, dict) and norm(timeline.get("job"))
        }
        job_rows: list[dict[str, Any]] = []
        skills_by_job: dict[str, list[dict[str, Any]]] = {}
        base_total = 0.0
        hand_total = 0.0
        final_total = sum(max(0.0, as_number(row.get("final_output"))) for row in row_list)
        for row in row_list:
            job = norm(row.get("job"))
            if not job:
                continue
            base_dps = max(0.0, as_number(row.get("base_dps")))
            hand = max(0.0, as_number(row.get("hand_multiplier"), 1.0))
            hand_dps = base_dps * hand
            final_dps = max(0.0, as_number(row.get("final_output")))
            team_gain = final_dps - hand_dps
            job_rows.append({
                "job": job,
                "base_dps": base_dps,
                "hand_multiplier": hand,
                "hand_dps": hand_dps,
                "final_dps": final_dps,
                "team_gain": team_gain,
                "team_multiplier": final_dps / hand_dps if hand_dps > 0 else 0.0,
                "gain_percent": (final_dps / hand_dps - 1.0) * 100.0 if hand_dps > 0 else 0.0,
                "share": final_dps / final_total * 100.0 if final_total > 0 else 0.0,
            })
            base_total += base_dps
            hand_total += hand_dps

            timeline = timeline_by_job.get(job, {})
            segments = [
                segment
                for segment in timeline.get("segments", [])
                if isinstance(segment, dict)
                and norm(segment.get("skill"))
                and not segment.get("timeline_hidden")
            ]
            total_effective = sum(
                max(0.0, as_number(segment.get("effective_score")))
                for segment in segments
            )
            grouped: dict[str, dict[str, Any]] = {}
            for segment in segments:
                skill = norm(segment.get("skill"))
                item = grouped.setdefault(skill, {
                    "skill": skill,
                    "casts": 0,
                    "raw_score": 0.0,
                    "effective_score": 0.0,
                    "cast_time": 0.0,
                    "types": set(),
                })
                effective = max(0.0, as_number(segment.get("effective_score")))
                raw = (
                    max(0.0, as_number(segment.get("raw_effective_score")))
                    if "raw_effective_score" in segment
                    else effective
                )
                start = max(0.0, as_number(segment.get("start")))
                end = max(start, as_number(segment.get("end")))
                item["casts"] += 1
                item["raw_score"] += raw
                item["effective_score"] += effective
                item["cast_time"] += end - start
                segment_type = norm(segment.get("type"))
                if segment_type:
                    item["types"].add(segment_type)
            skill_rows: list[dict[str, Any]] = []
            for item in grouped.values():
                effective = as_number(item.get("effective_score"))
                raw = as_number(item.get("raw_score"))
                segment_types = item.pop("types", set())
                estimated_dps = (
                    final_dps * effective / total_effective
                    if total_effective > 0 else 0.0
                )
                skill_rows.append({
                    **item,
                    "type": "、".join(sorted(segment_types)) or "输出",
                    "buff_gain_percent": (effective / raw - 1.0) * 100.0 if raw > 0 else 0.0,
                    "estimated_dps": estimated_dps,
                    "share": estimated_dps / final_dps * 100.0 if final_dps > 0 else 0.0,
                })
            skills_by_job[job] = sorted(
                skill_rows,
                key=lambda item: (as_number(item.get("estimated_dps")), as_number(item.get("effective_score"))),
                reverse=True,
            )
        return {
            "summary": {
                "base_total": base_total,
                "hand_total": hand_total,
                "final_total": final_total,
                "team_gain": final_total - hand_total,
                "gain_percent": (final_total / hand_total - 1.0) * 100.0 if hand_total > 0 else 0.0,
            },
            "jobs": job_rows,
            "skills": skills_by_job,
        }

    @staticmethod
    def _extreme_dispose_widget(widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setParent(None)
        widget.deleteLater()
        QApplication.sendPostedEvents(widget, QEvent.DeferredDelete)

    def _extreme_clear_table_widgets(
        self,
        table: QTableWidget,
        columns: Iterable[int],
    ) -> None:
        for row in range(table.rowCount()):
            for column in columns:
                widget = table.cellWidget(row, int(column))
                if widget is None:
                    continue
                table.removeCellWidget(row, int(column))
                self._extreme_dispose_widget(widget)

    @staticmethod
    def _extreme_compact_rate_text(value: Any) -> str:
        number = max(0.0, as_number(value))
        for scale, suffix in ((1_000_000_000.0, "b/s"), (1_000_000.0, "m/s"), (1_000.0, "k/s")):
            if number >= scale:
                text = f"{number / scale:.2f}".rstrip("0").rstrip(".")
                return f"{text}{suffix}"
        text = f"{number:.2f}".rstrip("0").rstrip(".")
        return f"{text}/s"

    @classmethod
    def _extreme_signed_rate_text(cls, value: Any) -> str:
        number = as_number(value)
        if abs(number) <= 1e-9:
            return cls._extreme_compact_rate_text(0)
        sign = "+" if number > 0 else "-"
        return f"{sign}{cls._extreme_compact_rate_text(abs(number))}"

    @staticmethod
    def _extreme_output_share(value: Any, total: Any) -> float:
        denominator = max(0.0, as_number(total))
        if denominator <= 1e-9:
            return 0.0
        return max(0.0, as_number(value)) / denominator * 100.0

    @classmethod
    def _extreme_output_shares(cls, values: Iterable[Any]) -> list[float]:
        normalized = [max(0.0, as_number(value)) for value in values]
        if not normalized:
            return []
        total = sum(normalized)
        if total <= 1e-9:
            return [0.0 for _value in normalized]
        shares = [round(cls._extreme_output_share(value, total), 2) for value in normalized]
        largest = max(range(len(normalized)), key=normalized.__getitem__)
        shares[largest] = round(shares[largest] + (100.0 - sum(shares)), 2)
        return shares

    @staticmethod
    def _extreme_pairwise_surrogate_score(
        team: tuple[str, ...],
        initial_team: tuple[str, ...],
        initial_score: float,
        single_scores: dict[str, float],
        pair_interactions: dict[tuple[str, str], float],
    ) -> float:
        """Estimate a larger team from exact one- and two-member additions."""
        initial_set = set(initial_team)
        additions = sorted(job for job in team if job not in initial_set)
        estimate = initial_score
        for job in additions:
            estimate += single_scores.get(job, initial_score) - initial_score
        for first, second in combinations(additions, 2):
            estimate += pair_interactions.get(tuple(sorted((first, second))), 0.0)
        return estimate

    @staticmethod
    def _extreme_prefilter_shortlist(
        candidates: Iterable[tuple[str, ...]],
        estimates: dict[tuple[str, ...], float],
        parent_best: dict[tuple[str, ...], tuple[str, ...]],
        budget: int,
        diversity_jobs: Iterable[str] = (),
    ) -> list[tuple[str, ...]]:
        """Keep global, parent and profession-diverse surrogate leaders."""
        ranked = sorted(
            candidates,
            key=lambda team: (estimates.get(team, float("-inf")), team),
            reverse=True,
        )
        selected = ranked[:max(1, budget)]
        selected_set = set(selected)
        for candidate in parent_best.values():
            if candidate not in selected_set:
                selected.append(candidate)
                selected_set.add(candidate)
        for job in unique(diversity_jobs):
            candidate = next((team for team in ranked if job in team), None)
            if candidate is not None and candidate not in selected_set:
                selected.append(candidate)
                selected_set.add(candidate)
        return selected

    @staticmethod
    def _extreme_adaptive_search_config(free_slots: int, candidate_count: int) -> dict[str, Any]:
        """Balance broad recall with bounded real-timeline refinements."""
        free_slots = max(0, min(TEAM_MEMBER_ROWS, int(free_slots)))
        candidate_count = max(TEAM_MEMBER_ROWS, int(candidate_count))
        if free_slots <= 1:
            beam_width, screening_base, exact_base = 320, 24, 12
        elif free_slots == 2:
            beam_width, screening_base, exact_base = 520, 36, 12
        elif free_slots <= 4:
            beam_width, screening_base, exact_base = 680, 48, 10
        elif free_slots <= 6:
            beam_width, screening_base, exact_base = 760, 56, 10
        else:
            beam_width, screening_base, exact_base = 800, 64, 12
        candidate_bonus = max(0, candidate_count - 24)
        beam_width += min(160, candidate_bonus * 12)
        screening_base += min(12, candidate_bonus * 2)
        return {
            "beam_width": beam_width,
            "prefilter_budget": int(ceil(beam_width * 1.5)),
            "screening_base": screening_base,
            "screening_max": min(96, screening_base + 28),
            "screening_margin": 0.035,
            "exact_base": exact_base,
            "exact_max": min(24, exact_base + 10),
            "exact_margin": 0.018,
            "close_gap": 0.005,
            "neighbor_screening": 36 if free_slots >= 5 else 28,
            "neighbor_exact": 8 if free_slots >= 3 else 12,
            "neighbor_rounds": 2,
            "neighbor_screening_floor": 0.985,
        }

    @staticmethod
    def _extreme_diverse_scored_states(
        scores: dict[tuple[str, ...], float],
        budget: int,
        diversity_jobs: Iterable[str] = (),
    ) -> list[tuple[tuple[str, ...], float]]:
        ranked = sorted(scores.items(), key=lambda item: (item[1], item[0]), reverse=True)
        if len(ranked) <= max(1, budget):
            return ranked
        jobs = unique(diversity_jobs)
        diversity_budget = min(len(jobs), max(1, budget // 4)) if jobs else 0
        global_quota = max(1, budget - diversity_budget)
        selected = ranked[:global_quota]
        selected_teams = {team for team, _value in selected}
        for job in jobs:
            candidate = next((item for item in ranked if job in item[0]), None)
            if candidate is None or candidate[0] in selected_teams:
                continue
            selected.append(candidate)
            selected_teams.add(candidate[0])
            if len(selected) >= budget:
                break
        if len(selected) < budget:
            for item in ranked:
                if item[0] in selected_teams:
                    continue
                selected.append(item)
                selected_teams.add(item[0])
                if len(selected) >= budget:
                    break
        return sorted(selected, key=lambda item: (item[1], item[0]), reverse=True)

    @classmethod
    def _extreme_adaptive_shortlist(
        cls,
        rows: Iterable[tuple[tuple[str, ...], float]],
        base_limit: int,
        max_limit: int,
        relative_margin: float,
        diversity_jobs: Iterable[str] = (),
    ) -> list[tuple[str, ...]]:
        ranked = sorted(rows, key=lambda item: (item[1], item[0]), reverse=True)
        if not ranked:
            return []
        base_limit = max(1, min(int(base_limit), len(ranked)))
        max_limit = max(base_limit, min(int(max_limit), len(ranked)))
        base_scores = dict(ranked)
        selected_rows = cls._extreme_diverse_scored_states(
            base_scores,
            base_limit,
            diversity_jobs,
        )
        selected = [team for team, _value in selected_rows]
        selected_set = set(selected)
        best_score = max(0.0, as_number(ranked[0][1]))
        floor = best_score * max(0.0, 1.0 - max(0.0, relative_margin))
        for team, value in ranked:
            if len(selected) >= max_limit or as_number(value) < floor:
                break
            if team not in selected_set:
                selected.append(team)
                selected_set.add(team)
        return selected

    def _show_extreme_team_dialog(self, jobs: list[str], current_job: str) -> dict[str, Any] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("一键极限优化配队")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "一键极限优化配队会把当前配装按既定规则转换给每个职业，读取各职业默认加点，"
            "并以长时间个人配队实战模拟的八人DPS总和寻找最佳队伍。\n\n"
            "极限优化配队会同时考虑：可选是否需要 T 位（毁灭/圣骑）和奶位（圣徒/药剂/曜）；职业不能重复，但打坐曜和输出曜可同时存在；团队 buff、减抗、增伤、叠冰、-CD 等对全队总输出的影响。"
            "只有明确选择职业的槽位才会固定；八个槽位全部选择“不限制”时，配装1职业也不会被强制加入。\n\n"
            "职业输出系数已取消。手部乘区仍可用于修正玩家操作、装备细节和职业熟练度差异。"
            "计算需要生成多个候选队伍的八人时间轴，会比普通一键配队更慢。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        saved_schemes = load_extreme_team_schemes()
        loaded_scheme: dict[str, Any] | None = None
        saved_box = QGroupBox("已保存的极限配队方案")
        saved_layout = QHBoxLayout(saved_box)
        saved_combo = Combo(
            ["选择方案", *sorted(saved_schemes)],
            "选择方案",
            min_width=260,
            contents_length=0,
        )
        saved_combo.setObjectName("extremeSavedSchemeCombo")
        saved_load_button = app_button("直接载入", primary=True)
        saved_load_button.setObjectName("extremeLoadSavedSchemeButton")
        saved_delete_button = app_button("删除方案")
        saved_delete_button.setObjectName("extremeDeleteSavedSchemeButton")
        saved_layout.addWidget(saved_combo, 1)
        saved_layout.addWidget(saved_load_button)
        saved_layout.addWidget(saved_delete_button)
        layout.addWidget(saved_box)

        def refresh_saved_combo(current: str = "选择方案") -> None:
            values = ["选择方案", *sorted(saved_schemes)]
            self._replace_combo_values(
                saved_combo,
                values,
                current if current in values else "选择方案",
            )

        def load_saved_scheme() -> None:
            nonlocal loaded_scheme
            name = norm(saved_combo.currentText())
            payload = saved_schemes.get(name)
            if not isinstance(payload, dict):
                QMessageBox.information(dialog, "载入极限配队方案", "请先选择需要载入的方案。")
                return
            loaded_scheme = copy.deepcopy(payload)
            loaded_scheme["_loaded_scheme_name"] = name
            dialog.accept()

        def delete_saved_scheme() -> None:
            name = norm(saved_combo.currentText())
            if name not in saved_schemes:
                return
            if QMessageBox.question(
                dialog,
                "删除极限配队方案",
                f"确定删除“{name}”吗？",
            ) != QMessageBox.Yes:
                return
            saved_schemes.pop(name, None)
            try:
                save_extreme_team_schemes(saved_schemes)
            except OSError as exc:
                QMessageBox.warning(dialog, "删除极限配队方案失败", str(exc))
                return
            refresh_saved_combo()

        saved_load_button.clicked.connect(load_saved_scheme)
        saved_delete_button.clicked.connect(delete_saved_scheme)

        option_box = QGroupBox("优化偏好")
        option_layout = QGridLayout(option_box)
        option_layout.addWidget(QLabel("队伍类型"), 0, 0)
        raw_team_sim = self.build.get("team_sim", {}) if isinstance(self.build.get("team_sim", {}), dict) else {}
        saved_mode = norm(raw_team_sim.get("extreme_team_mode")) or "不限"
        team_mode = Combo(["不限", "物理队", "魔法队", "光队", "暗队", "水队", "火队"], saved_mode, min_width=96, contents_length=0)
        option_layout.addWidget(team_mode, 0, 1)
        option_layout.addWidget(QLabel("Boss"), 0, 2)
        saved_boss = norm(raw_team_sim.get("extreme_boss")) or norm(self.boss_combo.currentText() if hasattr(self, "boss_combo") else "无") or "无"
        boss_combo = Combo(["无"] + list(BOSS_PRESETS), saved_boss, min_width=138, contents_length=0)
        option_layout.addWidget(boss_combo, 0, 3)
        require_tank_check = QCheckBox("需要T")
        require_tank_check.setChecked(bool(raw_team_sim.get("extreme_require_tank", True)))
        require_healer_check = QCheckBox("需要奶")
        require_healer_check.setChecked(bool(raw_team_sim.get("extreme_require_healer", True)))
        option_layout.addWidget(require_tank_check, 0, 4)
        option_layout.addWidget(require_healer_check, 0, 5)
        option_layout.addWidget(QLabel("替换分析"), 1, 0)
        saved_replacement_mode = norm(raw_team_sim.get("extreme_replacement_mode")) or norm(getattr(self, "team_extreme_replacement_mode", "")) or "快速"
        replacement_mode_combo = Combo(["快速", "完整"], saved_replacement_mode if saved_replacement_mode in {"快速", "完整"} else "快速", min_width=96, contents_length=0)
        option_layout.addWidget(replacement_mode_combo, 1, 1)
        option_layout.addWidget(QLabel("精算候选"), 1, 2)
        saved_replacement_candidates = max(
            0,
            int(as_number(
                raw_team_sim.get("extreme_replacement_candidates", getattr(self, "team_extreme_replacement_candidates", 2)),
                2,
            )),
        )
        replacement_candidate_spin = QSpinBox()
        replacement_candidate_spin.setRange(0, 12)
        replacement_candidate_spin.setValue(saved_replacement_candidates)
        replacement_candidate_spin.setFixedHeight(EDIT_ROW_HEIGHT)
        replacement_candidate_spin.setToolTip(
            "快速模式下，候选会先生成一次完整规则轴，再对每个队员最有希望的指定数量候选做团队时序同步；"
            "填0则不做时间轴精算，但仍显示粗算替换建议。完整模式会精算全部合法替换。"
        )
        option_layout.addWidget(replacement_candidate_spin, 1, 3)
        option_layout.addWidget(QLabel("固定队友"), 2, 0)
        fixed_widgets: list[Combo] = []
        fixed_options = ["不限制"] + list(jobs)
        fixed_source = getattr(self, "team_extreme_fixed_jobs", raw_team_sim.get("extreme_fixed_jobs", []))
        saved_fixed = []
        if isinstance(fixed_source, list):
            saved_fixed = [norm(item) for item in fixed_source if norm(item)]
        for index in range(TEAM_MEMBER_ROWS):
            current = saved_fixed[index] if index < len(saved_fixed) and saved_fixed[index] in fixed_options else "不限制"
            combo = Combo(fixed_options, current, min_width=118, contents_length=0)
            fixed_widgets.append(combo)
            option_layout.addWidget(combo, 2 + index // 4, 1 + index % 4)
        layout.addWidget(option_box)

        groups = self._job_family_groups(jobs)
        max_jobs = max([len(values) for values in groups.values()] + [1])
        table = AppTableWidget(len(groups), 1 + max_jobs * 2)
        headers = ["0转职业"]
        for index in range(max_jobs):
            headers.extend([f"职业{index + 1}", "手部乘区"])
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        hand_defaults = getattr(self, "team_extreme_hand_multipliers", {})
        hand_spin_by_job: dict[str, QDoubleSpinBox] = {}
        for row, (family, family_jobs) in enumerate(groups.items()):
            table.setItem(row, 0, self._item(family))
            table.setRowHeight(row, EDIT_ROW_HEIGHT)
            for index, job in enumerate(family_jobs):
                base_col = 1 + index * 2
                table.setItem(row, base_col, self._item(job))
                hand_spin = QDoubleSpinBox()
                hand_spin.setRange(0, 10)
                hand_spin.setDecimals(2)
                hand_spin.setSingleStep(0.05)
                hand_spin.setFixedHeight(EDIT_ROW_HEIGHT)
                hand_spin.setValue(float(max(0.0, as_number(hand_defaults.get(job, hand_defaults.get(self._team_real_job(job), 1.0)), 1.0))))
                table.setCellWidget(row, base_col + 1, hand_spin)
                hand_spin_by_job[job] = hand_spin
        widths = [82]
        for _index in range(max_jobs):
            widths.extend([98, 82])
        self._configure_table(table, widths)
        table.setMinimumHeight(420)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始优化")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(1040, 720)
        if dialog.exec() != QDialog.Accepted:
            return None
        if isinstance(loaded_scheme, dict):
            return {"saved_scheme": loaded_scheme}
        hand_out: dict[str, float] = {}
        for job in jobs:
            widget = hand_spin_by_job.get(job)
            hand_out[job] = float(widget.value()) if isinstance(widget, QDoubleSpinBox) else 1.0
        self.team_extreme_hand_multipliers = hand_out
        self.team_extreme_fixed_explicit = True
        fixed_jobs: list[str] = []
        for combo in fixed_widgets:
            text = norm(combo.currentText())
            if text and text != "不限制" and text not in fixed_jobs:
                fixed_jobs.append(text)
        self.team_extreme_fixed_jobs = fixed_jobs[:TEAM_MEMBER_ROWS]
        self.team_extreme_replacement_mode = norm(replacement_mode_combo.currentText()) or "快速"
        self.team_extreme_replacement_candidates = int(replacement_candidate_spin.value())
        return {
            "team_mode": norm(team_mode.currentText()) or "不限",
            "boss": norm(boss_combo.currentText()) or "无",
            "require_tank": bool(require_tank_check.isChecked()),
            "require_healer": bool(require_healer_check.isChecked()),
            "fixed_jobs": fixed_jobs[:TEAM_MEMBER_ROWS],
            "hand_multipliers": hand_out,
            "replacement_mode": self.team_extreme_replacement_mode,
            "replacement_candidates": self.team_extreme_replacement_candidates,
        }

    def _open_saved_extreme_team_scheme(self, payload: dict[str, Any]) -> None:
        team = [norm(job) for job in payload.get("team", []) if norm(job) and norm(job) != "无"]
        if not team:
            raise ValueError("保存的方案没有有效队员。")
        loaded_scheme_name = norm(payload.get("_loaded_scheme_name")) or "未命名方案"
        saved_revision = int(as_number(payload.get("timeline_engine_revision")))
        needs_timeline_recalculation = saved_revision < EXTREME_TEAM_TIMELINE_REVISION
        self._set_extreme_history_status(
            (
                f"正在读取既往极限配队方案“{loaded_scheme_name}”，并按当前时间轴算法重新计算……"
                if needs_timeline_recalculation
                else f"已读取既往极限配队方案“{loaded_scheme_name}”；当前显示保存时结果，可在结果页重新计算时间轴。"
            ),
            "info",
        )
        boss = norm(payload.get("boss")) or "无"
        self.team_extreme_mode = norm(payload.get("team_mode")) or "不限"
        self.team_extreme_boss = boss
        self.team_extreme_require_tank = bool(payload.get("require_tank", True))
        self.team_extreme_require_healer = bool(payload.get("require_healer", True))
        self.team_extreme_replacement_mode = norm(payload.get("replacement_mode")) or "快速"
        self.team_extreme_replacement_candidates = max(
            0,
            int(as_number(payload.get("replacement_candidates"), 2)),
        )
        self.team_extreme_fixed_jobs = [
            norm(job) for job in payload.get("fixed_jobs", []) if norm(job)
        ]
        self.team_extreme_fixed_explicit = bool(payload.get("fixed_explicit", True))
        self.team_extreme_aura_choices = dict(payload.get("aura_choices", {}))
        self.team_extreme_hand_multipliers = dict(payload.get("hand_multipliers", {}))
        self._reset_extreme_timeline_context(boss)
        current_candidates = [job for job in self._team_job_options() if job and job != "无"]
        candidate_jobs = unique([
            *[norm(job) for job in payload.get("candidate_jobs", []) if norm(job)],
            *team,
            *current_candidates,
        ])
        details = copy.deepcopy(payload.get("details", []))
        timelines = copy.deepcopy(payload.get("timelines", []))
        restored_state = copy.deepcopy(payload.get("result_state", {}))
        if not isinstance(restored_state, dict):
            restored_state = {}
        restored_state["_loaded_scheme_name"] = loaded_scheme_name
        restored_state["_loaded_scheme_saved_at"] = norm(payload.get("saved_at"))
        if needs_timeline_recalculation:
            restored_state["_recalculate_standard_timeline_on_open"] = True
        self._show_extreme_team_result_dialog(
            team,
            as_number(payload.get("total_score")),
            details if isinstance(details, list) else [],
            copy.deepcopy(payload.get("grades", {})),
            self.team_extreme_mode,
            list(self.team_extreme_fixed_jobs),
            candidate_jobs,
            hand_multipliers=dict(self.team_extreme_hand_multipliers),
            aura_choices=dict(self.team_extreme_aura_choices),
            timelines=timelines if isinstance(timelines, list) else [],
            member_builds=copy.deepcopy(payload.get("member_builds", [])),
            replacement_context=copy.deepcopy(payload.get("replacement_context", {})),
            restored_state=restored_state,
        )

    def auto_extreme_team(self) -> None:
        if getattr(self, "_extreme_calculation_active", False):
            return
        self._extreme_calculation_active = True
        try:
            self._auto_extreme_team_impl()
        finally:
            self._extreme_calculation_active = False

    def _auto_extreme_team_impl(self) -> None:
        if not hasattr(self, "team_member_table") or not hasattr(self, "team_effect_table"):
            return
        self.build = self.collect_build()
        jobs = [job for job in self._team_job_options() if job and job != "无"]
        current_job = norm(self.second_job.currentText()) if hasattr(self, "second_job") else ""
        if current_job == "曜":
            current_job = "打坐曜"
        if not current_job or current_job == "无":
            QMessageBox.warning(self, "一键极限优化配队", "请先选择当前配装的二转职业。")
            return
        if current_job not in jobs:
            jobs.insert(0, current_job)
        config = self._show_extreme_team_dialog(jobs, current_job)
        if config is None:
            return
        saved_scheme = config.get("saved_scheme")
        if isinstance(saved_scheme, dict):
            try:
                self._open_saved_extreme_team_scheme(saved_scheme)
            except Exception as exc:
                QMessageBox.warning(self, "载入极限配队方案失败", str(exc))
            return
        hand_multipliers = config.get("hand_multipliers", {})
        if not isinstance(hand_multipliers, dict):
            hand_multipliers = {}
        self.team_extreme_aura_choices = {}
        team_mode = norm(config.get("team_mode")) or "不限"
        boss_name = norm(config.get("boss")) or "无"
        require_tank = bool(config.get("require_tank", True))
        require_healer = bool(config.get("require_healer", True))
        replacement_mode = norm(config.get("replacement_mode")) or "快速"
        if replacement_mode not in {"快速", "完整"}:
            replacement_mode = "快速"
        replacement_candidate_limit = max(0, int(as_number(config.get("replacement_candidates"), 2)))
        fixed_jobs = [
            job for job in config.get("fixed_jobs", [])
            if job in jobs
        ]
        locked_jobs = unique(fixed_jobs)
        if len(locked_jobs) > TEAM_MEMBER_ROWS:
            QMessageBox.warning(self, "一键极限优化配队", "固定职业数量超过队伍上限。")
            return
        if self._team_has_real_duplicate(locked_jobs):
            QMessageBox.warning(self, "一键极限优化配队", "固定职业里存在重复职业；打坐曜和输出曜可以同时存在。")
            return
        candidate_jobs = [
            job for job in jobs
            if job in locked_jobs
            or self._extreme_job_allowed_by_mode(job, team_mode)
            or self._team_real_job(job) in TEAM_TANK_JOBS
            or self._team_real_job(job) in TEAM_HEALER_JOBS
        ]
        candidate_jobs = sorted(
            unique(candidate_jobs),
            key=lambda job: (
                0 if job in locked_jobs else 1,
                self._extreme_job_mode_priority(job, team_mode),
                job,
            ),
        )
        if len(candidate_jobs) < TEAM_MEMBER_ROWS:
            QMessageBox.warning(self, "一键极限优化配队", "当前队伍类型和固定职业限制过严，没有足够职业组成 8 人队伍。")
            return
        self.team_extreme_mode = team_mode
        self.team_extreme_boss = boss_name
        self.team_extreme_require_tank = require_tank
        self.team_extreme_require_healer = require_healer
        self.team_extreme_replacement_mode = replacement_mode
        self.team_extreme_replacement_candidates = replacement_candidate_limit
        self.team_extreme_fixed_jobs = fixed_jobs
        self.team_extreme_fixed_explicit = True
        progress = QProgressDialog("正在准备极限优化配队数据...", "取消", 0, 1000, self)
        progress.setWindowTitle("一键极限优化配队")
        progress.setWindowModality(Qt.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        QApplication.processEvents()
        progress.setLabelText("正在生成无 Buff 基础配装...")
        progress.setValue(5)
        QApplication.processEvents()
        extreme_base_build = self._extreme_base_build(self.build, boss_name)
        if progress.wasCanceled():
            self._extreme_dispose_widget(progress)
            return
        progress.setLabelText("正在计算无 Buff 基础面板...")
        progress.setValue(15)
        QApplication.processEvents()
        _rows, base_summary = self.calculator.calculate(extreme_base_build)
        if progress.wasCanceled():
            self._extreme_dispose_widget(progress)
            return
        progress.setLabelText("正在初始化极限配队缓存...")
        progress.setValue(25)
        QApplication.processEvents()
        self._extreme_clean_base_build = copy.deepcopy(extreme_base_build)
        self._extreme_clean_base_summary = dict(base_summary)
        base_effective = as_number(base_summary.get("effective_panel"))
        if not callable(getattr(type(self), "_extreme_base_element_percent", None)):
            raise RuntimeError("极限配队基础属性方法异常")
        if "_extreme_base_element_percent" in getattr(self, "__dict__", {}):
            delattr(self, "_extreme_base_element_percent")
        base_element_percent = self._extreme_base_element_percent(base_summary, current_job)
        self._extreme_base_element_percent_value = base_element_percent
        self._extreme_element_resists = dict(extreme_base_build.get("element_resists", {"火": 0.0, "水": 0.0, "光": 0.0, "暗": 0.0}))
        self._extreme_monster_defense = as_number(extreme_base_build.get("monster_defense"))
        self._extreme_crit_resist_value = as_number(extreme_base_build.get("crit_resist_value", extreme_base_build.get("crit_resist", 0)))
        self._extreme_base_crit_percent = as_number(base_summary.get("crit_percent"))
        self._extreme_base_crit_damage_percent = as_number(base_summary.get("crit_damage_percent"))
        self._extreme_personal_attrs_cache: dict[tuple[str, tuple[tuple[str, float, float, float], ...]], dict[str, float]] = {}
        self._extreme_personal_multiplier_cache: dict[tuple[str, tuple[tuple[str, float], ...], float], float] = {}
        self._extreme_personal_breakdown_cache: dict[tuple[str, tuple[tuple[str, float], ...], float], dict[str, float]] = {}
        self._extreme_converted_build_cache: dict[tuple[str, tuple[tuple[str, float], ...]], dict[str, Any]] = {}
        self._extreme_skill_element_weight_cache: dict[tuple[str, float, float], dict[str, float]] = {}
        self._extreme_team_effect_template_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._extreme_personal_buff_base_rows_cache: dict[str, list[tuple[dict[str, float], float, float]]] = {}
        self._extreme_timeline_result_cache: dict[Any, dict[str, Any]] = {}
        self._extreme_timeline_seed_cache: dict[str, dict[str, Any]] = {}
        self._extreme_timeline_panel_cache_pool: dict[Any, dict[str, dict[Any, Any]]] = {}
        base_unit = base_effective
        cancelled = False
        phase_base = 0
        phase_range = 1000
        phase_total = 1
        phase_done = 0
        phase_update_stride = 1

        def begin_phase(text: str, base: int, span: int, total: int) -> None:
            nonlocal phase_base, phase_range, phase_total, phase_done, phase_update_stride
            phase_base = max(0, min(1000, base))
            phase_range = max(1, span)
            phase_total = max(1, total)
            phase_done = 0
            phase_update_stride = (
                1
                if phase_total <= 120
                else max(4, min(32, phase_total // 160))
            )
            progress.setLabelText(text)
            progress.setValue(phase_base)
            QApplication.processEvents()

        def step(text: str) -> bool:
            nonlocal phase_done, cancelled
            if cancelled:
                return False
            phase_done += 1
            if (
                phase_done > 1
                and phase_done < phase_total
                and phase_done % phase_update_stride != 0
            ):
                return True
            value = min(999, phase_base + int(phase_range * min(1.0, phase_done / phase_total)))
            progress.setLabelText(text)
            progress.setValue(value)
            QApplication.processEvents()
            cancelled = progress.wasCanceled()
            return not cancelled

        def heartbeat(text: str) -> bool:
            nonlocal cancelled
            if cancelled:
                return False
            progress.setLabelText(text)
            QApplication.processEvents()
            cancelled = progress.wasCanceled()
            return not cancelled

        score_cache: dict[tuple[str, ...], float] = {}
        best_aura_cache: dict[tuple[str, ...], dict[str, str]] = {}
        adjusted_effect_cache: dict[tuple[tuple[str, ...], tuple[tuple[str, str], ...]], list[dict[str, Any]]] = {}
        static_score_evaluations = 0

        def adjusted_effects_for_team(team: tuple[str, ...], aura_choices: dict[str, str]) -> list[dict[str, Any]]:
            key = tuple(sorted(team))
            aura_key = self._team_aura_key(aura_choices)
            cache_key = (key, aura_key)
            if cache_key not in adjusted_effect_cache:
                previous_auras = getattr(self, "team_extreme_aura_choices", {})
                self.team_extreme_aura_choices = dict(aura_choices)
                try:
                    effects = self._extreme_team_effects_for_jobs(list(key))
                    base_effects = self._team_effects_with_auto_cdr_glyphs(self._dedup_team_effects(effects))
                    applied_cdr = {
                        self._team_effect_skill_name(effect.get("skill"))
                        for effect in base_effects
                        if self._team_effect_skill_name(effect.get("skill")) in TEAM_CDR_APPLY_SKILLS
                    }
                    adjusted_effect_cache[cache_key] = self._team_effects_with_cdr_adjusted_cooldowns(base_effects, applied_cdr)
                finally:
                    self.team_extreme_aura_choices = previous_auras
            return adjusted_effect_cache[cache_key]

        simulation_window = SKILL_LONG_PRACTICAL_WINDOW
        baseline_timeline_dps: dict[str, float] = {}
        baseline_static_output: dict[str, float] = {}
        baseline_timeline_rows: dict[str, dict[str, Any]] = {}
        begin_phase("正在生成各职业长时间基础轴...", 25, 225, len(candidate_jobs))
        baseline_started_at = time.perf_counter()
        baseline_specs: list[dict[str, Any]] = []
        baseline_meta: list[tuple[str, list[dict[str, Any]]]] = []
        for job in candidate_jobs:
            for aura_choices in self._team_aura_combinations_for_jobs((job,)):
                own_effects = adjusted_effects_for_team((job,), aura_choices)
                baseline_specs.append({
                    "job": job,
                    "team": [job],
                    "effects": own_effects,
                    "hand_multiplier": 1.0,
                    "window": simulation_window,
                    "include_display_windows": False,
                })
                baseline_meta.append((job, own_effects))
        baseline_results = self._extreme_timeline_tasks_parallel(
            baseline_specs,
            progress_callback=lambda completed, _index, job: heartbeat(
                f"正在并行生成各职业基础轴... 已完成 {completed}/{len(baseline_specs)}（{job}）"
            ),
        )
        if cancelled or len(baseline_results) != len(baseline_specs):
            self._extreme_dispose_widget(progress)
            return
        best_by_job: dict[str, tuple[dict[str, Any], float]] = {}
        for (job, own_effects), timeline in zip(baseline_meta, baseline_results):
            current_best = best_by_job.get(job)
            if current_best is None or as_number(timeline.get("dps")) > as_number(current_best[0].get("dps")):
                one_coeff = {job: 1.0, self._team_real_job(job): 1.0}
                static_rows = self._team_extreme_output_details(
                    [job], own_effects, base_unit, one_coeff, base_element_percent
                )
                best_static = as_number(static_rows[0].get("final_output")) if static_rows else 0.0
                best_by_job[job] = (timeline, best_static)
        for job in candidate_jobs:
            best_baseline, best_static = best_by_job.get(
                job,
                ({"dps": 0.0, "segments": [], "window": simulation_window}, 0.0),
            )
            baseline = best_baseline
            baseline_timeline_dps[job] = max(0.0, as_number(baseline.get("dps")))
            baseline_static_output[job] = max(0.0, best_static)
            baseline_timeline_rows[job] = copy.deepcopy(baseline)
            total_elapsed = time.perf_counter() - baseline_started_at
            if not step(f"{job} 基础轴完成；并行累计 {total_elapsed:.1f}s"):
                self._extreme_dispose_widget(progress)
                return

        def score(team: tuple[str, ...]) -> float:
            nonlocal static_score_evaluations
            key = tuple(sorted(team))
            if key not in score_cache:
                static_score_evaluations += 1
                best_value = -1.0
                best_auras: dict[str, str] = {}
                for aura_choices in self._team_aura_combinations_for_jobs(key):
                    adjusted = adjusted_effects_for_team(key, aura_choices)
                    unit_coeffs = {job: 1.0 for job in key}
                    static_values = self._team_extreme_output_values(
                        list(key), adjusted, base_unit, unit_coeffs, base_element_percent
                    )
                    value = 0.0
                    for job, team_static_value in static_values.items():
                        own_static = baseline_static_output.get(job, 0.0)
                        team_static = max(0.0, as_number(team_static_value))
                        team_ratio = team_static / own_static if own_static > 0 else 1.0
                        hand = max(
                            0.0,
                            as_number(
                                hand_multipliers.get(job, hand_multipliers.get(self._team_real_job(job), 1.0)),
                                1.0,
                            ),
                        )
                        value += baseline_timeline_dps.get(job, 0.0) * team_ratio * hand
                    if value > best_value:
                        best_value = value
                        best_auras = dict(aura_choices)
                score_cache[key] = max(0.0, best_value)
                best_aura_cache[key] = best_auras
            return score_cache[key]

        exact_score_cache: dict[tuple[str, ...], float] = {}
        exact_timeline_cache: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        screening_score_cache: dict[tuple[str, ...], float] = {}

        def exact_score(team: tuple[str, ...]) -> float:
            key = tuple(sorted(team))
            if key in exact_score_cache:
                return exact_score_cache[key]
            if key not in best_aura_cache:
                score(key)
            aura_choices = best_aura_cache.get(key, {})
            adjusted = adjusted_effects_for_team(key, aura_choices)
            exact_specs = [
                {
                    "job": job,
                    "team": list(key),
                    "effects": adjusted,
                    "hand_multiplier": max(
                        0.0,
                        as_number(
                            hand_multipliers.get(job, hand_multipliers.get(self._team_real_job(job), 1.0)),
                            1.0,
                        ),
                    ),
                    "window": simulation_window,
                    "include_display_windows": False,
                }
                for job in key
            ]
            timelines = self._extreme_timeline_tasks_parallel(
                exact_specs,
                progress_callback=lambda _completed, _index, job: step(
                    f"正在并行精算 {'、'.join(key)}：{job} 时间轴"
                ),
            )
            value = sum(max(0.0, as_number(row.get("dps"))) for row in timelines)
            exact_score_cache[key] = value
            exact_timeline_cache[key] = timelines
            return value

        def screening_score(team: tuple[str, ...]) -> float:
            """Use a shorter real timeline to rank finalists before 125s exact runs."""
            key = tuple(sorted(team))
            if key in screening_score_cache:
                return screening_score_cache[key]
            if key not in best_aura_cache:
                score(key)
            aura_choices = best_aura_cache.get(key, {})
            adjusted = adjusted_effects_for_team(key, aura_choices)
            screening_window = min(
                max(30.0, as_number(getattr(self, "_extreme_screening_window", 45.0), 45.0)),
                simulation_window,
            )
            screening_specs = [
                {
                    "job": job,
                    "team": list(key),
                    "effects": adjusted,
                    "hand_multiplier": max(
                        0.0,
                        as_number(
                            hand_multipliers.get(job, hand_multipliers.get(self._team_real_job(job), 1.0)),
                            1.0,
                        ),
                    ),
                    "window": screening_window,
                    "include_display_windows": False,
                }
                for job in key
            ]
            screening_timelines = self._extreme_timeline_tasks_parallel(
                screening_specs,
                progress_callback=lambda _completed, _index, job: step(
                    f"正在并行用 {screening_window:g}s 实战轴筛选 {'、'.join(key)}：{job}"
                ),
            )
            value = sum(
                max(0.0, as_number(timeline.get("dps")))
                for timeline in screening_timelines
            )
            screening_score_cache[key] = value
            return value

        def possible(team: tuple[str, ...], remaining_slots: int) -> bool:
            return self._extreme_team_can_still_meet_roles(team, remaining_slots, require_tank, require_healer)

        initial_team = tuple(sorted(locked_jobs))
        if not possible(initial_team, TEAM_MEMBER_ROWS - len(initial_team)):
            role_parts = []
            if require_tank:
                role_parts.append("T 位")
            if require_healer:
                role_parts.append("奶位")
            role_text = "或".join(role_parts) or "队伍"
            QMessageBox.warning(self, "一键极限优化配队", f"固定职业组合无法满足 {role_text} 要求。")
            self._extreme_dispose_widget(progress)
            return
        begin_phase("正在计算初始队伍...", 250, 10, 1)
        if not step("正在计算初始队伍：基础评分"):
            self._extreme_dispose_widget(progress)
            return
        initial_score = score(initial_team)
        states: list[tuple[tuple[str, ...], float]] = [(initial_team, initial_score)]
        best_timelines: list[dict[str, Any]] = []
        search_started_at = time.perf_counter()
        search_generated_candidates = 0
        search_scored_candidates = 0
        search_prefilter_layers: list[dict[str, int]] = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            slots_to_fill = TEAM_MEMBER_ROWS - len(initial_team)
            adaptive_search = self._extreme_adaptive_search_config(
                slots_to_fill,
                len(candidate_jobs),
            )
            beam_width = max(
                32,
                int(as_number(
                    getattr(self, "_extreme_beam_width", adaptive_search["beam_width"]),
                    adaptive_search["beam_width"],
                )),
            )
            use_static_prefilter = (
                slots_to_fill >= 3
                and not bool(getattr(self, "_extreme_disable_static_prefilter", False))
            )
            prefilter_factor = max(
                1,
                int(as_number(getattr(self, "_extreme_static_prefilter_factor", 1), 1)),
            )
            prefilter_budget = max(
                beam_width,
                int(as_number(
                    getattr(self, "_extreme_prefilter_budget", adaptive_search["prefilter_budget"]),
                    adaptive_search["prefilter_budget"],
                )),
            ) * prefilter_factor
            surrogate_single_scores: dict[str, float] = {}
            surrogate_pair_interactions: dict[tuple[str, str], float] = {}
            if use_static_prefilter:
                additions = [
                    job for job in candidate_jobs
                    if self._team_unique_job(job) not in self._team_unique_job_set(initial_team)
                ]
                model_total = len(additions) + len(additions) * max(0, len(additions) - 1) // 2
                begin_phase("正在建立候选组合快速评分模型...", 250, 10, max(1, model_total))
                for job in additions:
                    candidate = tuple(sorted([*initial_team, job]))
                    surrogate_single_scores[job] = score(candidate)
                    if not step(f"正在分析 {job} 的独立贡献"):
                        break
                if not cancelled:
                    for first, second in combinations(additions, 2):
                        candidate = tuple(sorted([*initial_team, first, second]))
                        pair_score = score(candidate)
                        surrogate_pair_interactions[tuple(sorted((first, second)))] = (
                            pair_score
                            - surrogate_single_scores[first]
                            - surrogate_single_scores[second]
                            + initial_score
                        )
                        if not step(f"正在分析 {first} + {second} 的协同贡献"):
                            break
            if cancelled:
                return
            for slot_index, slot in enumerate(range(len(initial_team), TEAM_MEMBER_ROWS)):
                candidates: set[tuple[str, ...]] = set()
                parent_continuations: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
                for team, _team_score in states:
                    team_unique_jobs = self._team_unique_job_set(team)
                    continuations: list[tuple[str, ...]] = []
                    for job in candidate_jobs:
                        if self._team_unique_job(job) in team_unique_jobs:
                            continue
                        candidate = tuple(sorted([*team, job]))
                        if not possible(candidate, TEAM_MEMBER_ROWS - len(candidate)):
                            continue
                        candidates.add(candidate)
                        continuations.append(candidate)
                    if continuations:
                        parent_continuations[team] = continuations
                if not candidates:
                    break
                search_generated_candidates += len(candidates)

                score_candidates = list(candidates)
                prefilter_active = (
                    use_static_prefilter
                    and slot_index >= 2
                    and len(score_candidates) > prefilter_budget
                )
                if prefilter_active:
                    estimates = {
                        candidate: self._extreme_pairwise_surrogate_score(
                            candidate,
                            initial_team,
                            initial_score,
                            surrogate_single_scores,
                            surrogate_pair_interactions,
                        )
                        for candidate in score_candidates
                    }
                    parent_best: dict[tuple[str, ...], tuple[str, ...]] = {}
                    for team, continuations in parent_continuations.items():
                        if continuations:
                            parent_best[team] = max(
                                continuations,
                                key=lambda candidate: estimates[candidate],
                            )
                    score_candidates = self._extreme_prefilter_shortlist(
                        score_candidates,
                        estimates,
                        parent_best,
                        prefilter_budget,
                        candidate_jobs,
                    )
                    search_prefilter_layers.append({
                        "members": slot + 1,
                        "generated": len(candidates),
                        "scored": len(score_candidates),
                    })

                begin_phase(
                    (
                        f"正在搜索第 {slot + 1} 个队员："
                        f"快速筛选 {len(candidates)} -> 精确评分 {len(score_candidates)}..."
                        if prefilter_active
                        else f"正在搜索第 {slot + 1} 个队员..."
                    ),
                    260 + int(350 * slot_index / max(1, slots_to_fill)),
                    max(1, int(350 / max(1, slots_to_fill))),
                    len(score_candidates),
                )
                next_states: dict[tuple[str, ...], float] = {}
                for candidate in score_candidates:
                    if not step(f"正在搜索第 {slot + 1} 个队员：精确评分候选队伍"):
                        break
                    next_states[candidate] = score(candidate)
                    search_scored_candidates += 1
                if cancelled or not next_states:
                    break
                states = self._extreme_diverse_scored_states(
                    next_states,
                    beam_width,
                    candidate_jobs,
                )
            if cancelled:
                return
            legal = [
                (team, value) for team, value in states
                if len(team) == TEAM_MEMBER_ROWS
                and self._extreme_team_has_required_roles(team, require_tank, require_healer)
            ]
            if not legal:
                role_parts = []
                if require_tank:
                    role_parts.append("T 位")
                if require_healer:
                    role_parts.append("奶位")
                role_text = "、".join(role_parts)
                condition_text = f"满足 {role_text}、固定职业和不重复职业条件（打坐曜/输出曜例外）" if role_text else "满足固定职业和不重复职业条件（打坐曜/输出曜例外）"
                QMessageBox.warning(self, "一键极限优化配队", f"没有找到{condition_text}的队伍。")
                return
            best_team, best_score = max(legal, key=lambda item: item[1])
            replace_attempts = max(1, len(best_team) * len(candidate_jobs) + len(best_team) * len(best_team) * len(candidate_jobs) * len(candidate_jobs) // 12)
            begin_phase("第一级：正在做固定约束下的静态替换优化...", 610, 70, replace_attempts)
            best_team_list, best_score = self._optimize_extreme_team_selection(
                list(best_team),
                candidate_jobs,
                best_score,
                base_unit,
                {},
                score,
                step,
                locked_jobs,
                require_tank,
                require_healer,
            )
            if self._extreme_mode_has_preference(team_mode):
                begin_phase("第一级：正在按队伍类型做 10% 门槛保留...", 680, 30, max(1, len(candidate_jobs) * TEAM_MEMBER_ROWS))
                best_team_list, best_score = self._prefer_extreme_mode_team_within_threshold(
                    best_team_list,
                    candidate_jobs,
                    best_score,
                    score,
                    step,
                    locked_jobs,
                    team_mode,
                    require_tank,
                    require_healer,
                    tolerance=0.10,
                )
            finalist_map: dict[tuple[str, ...], float] = {
                team: value
                for team, value in score_cache.items()
                if len(team) == TEAM_MEMBER_ROWS
                and self._extreme_team_has_required_roles(team, require_tank, require_healer)
            }
            finalist_map.update({tuple(sorted(team)): value for team, value in legal})
            finalist_map[tuple(sorted(best_team_list))] = best_score
            screening_override = getattr(self, "_extreme_screening_pool_limit", None)
            if screening_override is None:
                screening_base = int(adaptive_search["screening_base"])
                screening_max = int(adaptive_search["screening_max"])
            else:
                screening_base = screening_max = max(2, int(as_number(screening_override, 2)))
            screening_pool = self._extreme_adaptive_shortlist(
                finalist_map.items(),
                screening_base,
                screening_max,
                as_number(adaptive_search["screening_margin"]),
                candidate_jobs,
            )
            begin_phase(
                f"第二级：正在用短时间实战轴复筛 {len(screening_pool)} 支候选队伍...",
                710,
                60,
                max(1, len(screening_pool) * TEAM_MEMBER_ROWS),
            )
            screening_rows = [(team, screening_score(team)) for team in screening_pool]
            if cancelled:
                return
            exact_override = getattr(self, "_extreme_exact_finalist_limit", None)
            if exact_override is None:
                exact_base = int(adaptive_search["exact_base"])
                exact_max = int(adaptive_search["exact_max"])
            else:
                exact_base = exact_max = max(1, int(as_number(exact_override, 1)))
            finalists = self._extreme_adaptive_shortlist(
                screening_rows,
                exact_base,
                exact_max,
                as_number(adaptive_search["exact_margin"]),
            )
            begin_phase(
                f"第三级：正在用八人长时间实战轴精算 {len(finalists)} 支队伍...",
                770,
                50,
                max(1, len(finalists) * TEAM_MEMBER_ROWS),
            )
            exact_rows = [(team, exact_score(team)) for team in finalists]
            if cancelled:
                return

            def select_exact_winner(rows: list[tuple[tuple[str, ...], float]]) -> tuple[tuple[str, ...], float]:
                exact_best_value = max((value for _team, value in rows), default=0.0)
                eligible = rows
                if self._extreme_mode_has_preference(team_mode) and exact_best_value > 0:
                    floor = exact_best_value * 0.90
                    preferred = [item for item in rows if item[1] >= floor]
                    if preferred:
                        eligible = preferred
                        return max(
                            eligible,
                            key=lambda item: (self._extreme_team_mode_match_count(item[0], team_mode), item[1]),
                        )
                return max(rows, key=lambda item: item[1])

            best_team, best_score = select_exact_winner(exact_rows)
            close_score_expanded = False
            ranked_exact = sorted(exact_rows, key=lambda item: item[1], reverse=True)
            exact_gap = (
                (ranked_exact[0][1] - ranked_exact[1][1]) / max(1e-9, ranked_exact[0][1])
                if len(ranked_exact) > 1 and ranked_exact[0][1] > 0
                else 1.0
            )
            if exact_gap <= as_number(adaptive_search["close_gap"]) and len(finalists) < exact_max:
                existing = set(finalists)
                remaining = [
                    team for team, _value in sorted(screening_rows, key=lambda item: item[1], reverse=True)
                    if team not in existing
                ]
                expanded = remaining[:max(0, exact_max - len(finalists))]
                if expanded:
                    close_score_expanded = True
                    begin_phase(
                        f"第三级：冠亚军过于接近，自动扩容精算 {len(expanded)} 支队伍...",
                        820,
                        15,
                        max(1, len(expanded) * TEAM_MEMBER_ROWS),
                    )
                    exact_rows.extend((team, exact_score(team)) for team in expanded)
                    finalists.extend(expanded)
                    if cancelled:
                        return
                    best_team, best_score = select_exact_winner(exact_rows)

            # Full timelines are nonlinear.  Iteratively verify one-member
            # neighbors around the current exact winner until it is stable.
            seen_exact = {team for team, _value in exact_rows}
            locked_unique = self._team_unique_job_set(locked_jobs)
            neighbor_override = getattr(self, "_extreme_exact_neighbor_limit", None)
            neighbor_limit = (
                max(0, int(as_number(neighbor_override, 0)))
                if neighbor_override is not None
                else int(adaptive_search["neighbor_exact"])
            )
            neighbor_static_floor = max(0.0, min(
                1.0,
                as_number(getattr(self, "_extreme_exact_neighbor_static_floor", 0.98), 0.98),
            ))
            neighbor_rounds_done = 0
            neighbor_converged = neighbor_limit <= 0
            exact_neighbors: list[tuple[str, ...]] = []
            for round_index in range(int(adaptive_search["neighbor_rounds"])):
                if neighbor_limit <= 0:
                    break
                neighbor_scores: dict[tuple[str, ...], float] = {}
                best_team_seed = list(best_team)
                for index, current in enumerate(best_team_seed):
                    if self._team_unique_job(current) in locked_unique:
                        continue
                    for job in candidate_jobs:
                        if self._team_unique_job(job) in self._team_unique_job_set(best_team_seed):
                            continue
                        candidate = best_team_seed[:]
                        candidate[index] = job
                        candidate_key = tuple(sorted(candidate))
                        if candidate_key in seen_exact:
                            continue
                        if not self._extreme_team_has_required_roles(candidate_key, require_tank, require_healer):
                            continue
                        neighbor_scores[candidate_key] = score(candidate_key)
                if not neighbor_scores:
                    neighbor_converged = True
                    break
                winner_static_score = score(tuple(best_team))
                static_neighbors = [
                    (team, value) for team, value in neighbor_scores.items()
                    if value >= winner_static_score * neighbor_static_floor
                ]
                neighbor_screening_pool = self._extreme_adaptive_shortlist(
                    static_neighbors,
                    min(int(adaptive_search["neighbor_screening"]), max(1, len(static_neighbors))),
                    min(int(adaptive_search["neighbor_screening"]) + 12, max(1, len(static_neighbors))),
                    0.02,
                ) if static_neighbors else []
                if not neighbor_screening_pool:
                    neighbor_converged = True
                    break
                phase_base_value = 835 + round_index * 7
                begin_phase(
                    f"第三级：第 {round_index + 1} 轮冠军邻域短轴验证...",
                    phase_base_value,
                    4,
                    max(1, len(neighbor_screening_pool) * TEAM_MEMBER_ROWS),
                )
                screened_neighbors = sorted(
                    ((team, screening_score(team)) for team in neighbor_screening_pool),
                    key=lambda item: item[1],
                    reverse=True,
                )
                current_screening_score = screening_score(tuple(best_team))
                screening_floor = current_screening_score * as_number(
                    adaptive_search["neighbor_screening_floor"], 0.985
                )
                plausible = [item for item in screened_neighbors if item[1] >= screening_floor]
                if not plausible:
                    plausible = screened_neighbors[:min(2, len(screened_neighbors))]
                neighbor_finalists = self._extreme_adaptive_shortlist(
                    plausible,
                    min(neighbor_limit, max(1, len(plausible))),
                    min(neighbor_limit, max(1, len(plausible))),
                    0.0,
                )
                if not neighbor_finalists:
                    neighbor_converged = True
                    break
                begin_phase(
                    f"第三级：第 {round_index + 1} 轮冠军邻域完整轴复核...",
                    phase_base_value + 4,
                    3,
                    max(1, len(neighbor_finalists) * TEAM_MEMBER_ROWS),
                )
                previous_best = tuple(best_team)
                new_rows = [(team, exact_score(team)) for team in neighbor_finalists]
                exact_rows.extend(new_rows)
                exact_neighbors.extend(neighbor_finalists)
                seen_exact.update(neighbor_finalists)
                neighbor_rounds_done += 1
                if cancelled:
                    return
                best_team, best_score = select_exact_winner(exact_rows)
                if tuple(best_team) == previous_best:
                    neighbor_converged = True
                    break
            best_team_list = list(best_team)
            best_timelines = copy.deepcopy(exact_timeline_cache.get(tuple(sorted(best_team_list)), []))
            ranked_exact = sorted(exact_rows, key=lambda item: item[1], reverse=True)
            final_gap = (
                (ranked_exact[0][1] - ranked_exact[1][1]) / max(1e-9, ranked_exact[0][1]) * 100.0
                if len(ranked_exact) > 1 and ranked_exact[0][1] > 0
                else 100.0
            )
            self._extreme_search_diagnostics = {
                "elapsed": time.perf_counter() - search_started_at,
                "generated_candidates": search_generated_candidates,
                "scored_candidates": search_scored_candidates,
                "static_score_evaluations": static_score_evaluations,
                "prefilter_layers": copy.deepcopy(search_prefilter_layers),
                "prefilter_enabled": use_static_prefilter,
                "adaptive_config": copy.deepcopy(adaptive_search),
                "beam_width": beam_width,
                "stage1_full_teams": len(finalist_map),
                "stage2_screening_candidates": len(screening_pool),
                "exact_finalists": len(finalists),
                "exact_neighbors": len(exact_neighbors),
                "neighbor_rounds": neighbor_rounds_done,
                "close_score_expanded": close_score_expanded,
                "final_gap_percent": final_gap,
                "best_team": sorted(best_team_list),
                "verification": (
                    "高精度邻域已收敛"
                    if neighbor_converged and neighbor_rounds_done
                    else "达到邻域复核轮次上限"
                    if neighbor_rounds_done
                    else "完整轴精算完成"
                ),
                "screening_teams": len(screening_score_cache),
            }
        finally:
            QApplication.restoreOverrideCursor()
            progress.setValue(850)
        if cancelled:
            self._extreme_dispose_widget(progress)
            return
        progress.setLabelText("正在应用最优队伍并整理团队效果...")
        QApplication.processEvents()
        best_key = tuple(sorted(best_team_list))
        if best_key not in best_aura_cache:
            best_score = score(tuple(best_team_list))
        best_aura_choices = best_aura_cache.get(best_key, {})
        self._set_team_aura_choices(best_aura_choices)
        self._apply_team_members(best_team_list)
        self._auto_populate_team_effects_from_members(schedule=False)
        selected_cdr = self._selected_team_cdr_skills()
        for mapping in (getattr(self, "team_cdr_glyph_checks", {}), getattr(self, "team_cdr_apply_checks", {})):
            for skill, check in mapping.items():
                if isinstance(check, QCheckBox):
                    check.blockSignals(True)
                    check.setChecked(skill in selected_cdr)
                    check.blockSignals(False)
        self._refresh_team_cdr_usage_visibility()
        base_effects = self._selected_team_effects(use_base_cooldowns=True)
        self._sync_team_effect_cooldowns(base_effects, self._team_applied_cdr_skills())
        self._request_team_effect_auto_sort()
        self.schedule_refresh()
        effects = self._team_effects_for_jobs(best_team_list)
        base_effects = self._team_effects_with_auto_cdr_glyphs(self._dedup_team_effects(effects))
        applied_cdr = {
            self._team_effect_skill_name(effect.get("skill"))
            for effect in base_effects
            if self._team_effect_skill_name(effect.get("skill")) in TEAM_CDR_APPLY_SKILLS
        }
        adjusted = self._team_effects_with_cdr_adjusted_cooldowns(base_effects, applied_cdr)
        progress.setLabelText("正在同步八名队员的实际Buff/debuff施放时机...")
        progress.setValue(860)
        QApplication.processEvents()
        best_timelines = self._extreme_synchronized_team_timelines(
            best_team_list,
            adjusted,
            hand_multipliers,
            best_timelines,
            simulation_window,
            progress_callback=lambda index, job: (
                progress.setLabelText(f"正在按 {job} 的真实施放时刻重算团队效果..."),
                progress.setValue(860 + int(40 * (index + 1) / max(1, len(best_team_list)))),
                QApplication.processEvents(),
            ),
        )
        progress.setLabelText("正在汇总最优队伍的职业输出...")
        progress.setValue(905)
        QApplication.processEvents()
        best_score = sum(max(0.0, as_number(timeline.get("dps"))) for timeline in best_timelines)
        details: list[dict[str, Any]] = []
        for timeline in best_timelines:
            job = norm(timeline.get("job"))
            baseline = baseline_timeline_rows.get(job, {})
            profile = self._team_job_attack_profile(job)
            baseline_dps = max(0.0, as_number(baseline.get("dps")))
            team_dps = max(0.0, as_number(timeline.get("dps")))
            details.append({
                "job": job,
                "attack": profile.get("attack", ""),
                "element": profile.get("element", "") or "无",
                "damage_panel": as_number(timeline.get("damage_panel")),
                "base_average": as_number(baseline.get("average")),
                "base_dps": baseline_dps,
                "team_average": as_number(timeline.get("average")),
                "hand_multiplier": as_number(timeline.get("hand_multiplier"), 1.0),
                "final_output": team_dps,
                "gain_percent": (team_dps / baseline_dps - 1.0) * 100.0 if baseline_dps > 0 else 0.0,
            })
        total_timeline_dps = sum(as_number(row.get("final_output")) for row in details)
        for row in details:
            row["share"] = as_number(row.get("final_output")) / total_timeline_dps * 100.0 if total_timeline_dps > 0 else 0.0
        replacement_score_cache: dict[tuple[str, ...], float] = {
            tuple(sorted(best_team_list)): best_score,
        }
        replacement_first_pass_cache: dict[tuple[str, ...], float] = {}
        replacement_first_pass_rows: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        best_initial_rows = exact_timeline_cache.get(tuple(sorted(best_team_list)), [])
        if best_initial_rows:
            replacement_first_pass_rows[tuple(sorted(best_team_list))] = best_initial_rows
            replacement_first_pass_cache[tuple(sorted(best_team_list))] = sum(
                max(0.0, as_number(timeline.get("dps")))
                for timeline in best_initial_rows
            )

        def timeline_replacement_first_pass_score(team: tuple[str, ...]) -> float:
            key = tuple(sorted(team))
            if key in replacement_first_pass_cache:
                return replacement_first_pass_cache[key]
            if key not in best_aura_cache:
                score(key)
            aura_choices = best_aura_cache.get(key, {})
            adjusted_effect_rows = adjusted_effects_for_team(key, aura_choices)
            replacement_specs = [
                {
                    "job": job,
                    "team": list(key),
                    "effects": adjusted_effect_rows,
                    "hand_multiplier": max(
                        0.0,
                        as_number(
                            hand_multipliers.get(job, hand_multipliers.get(self._team_real_job(job), 1.0)),
                            1.0,
                        ),
                    ),
                    "window": simulation_window,
                    "include_display_windows": False,
                }
                for job in key
            ]
            first_pass = self._extreme_timeline_tasks_parallel(replacement_specs)
            value = sum(max(0.0, as_number(timeline.get("dps"))) for timeline in first_pass)
            replacement_first_pass_rows[key] = first_pass
            replacement_first_pass_cache[key] = value
            return value

        def timeline_replacement_score(team: tuple[str, ...]) -> float:
            key = tuple(sorted(team))
            if key in replacement_score_cache:
                return replacement_score_cache[key]
            timeline_replacement_first_pass_score(key)
            if key not in best_aura_cache:
                score(key)
            aura_choices = best_aura_cache.get(key, {})
            adjusted_effect_rows = adjusted_effects_for_team(key, aura_choices)
            synced = self._extreme_synchronized_team_timelines(
                key,
                adjusted_effect_rows,
                hand_multipliers,
                replacement_first_pass_rows.get(key, []),
                simulation_window,
                include_display_windows=False,
                max_passes=1 if replacement_mode != "完整" else 3,
            )
            value = sum(max(0.0, as_number(timeline.get("dps"))) for timeline in synced)
            replacement_score_cache[key] = value
            return value

        rough_replacement_only = replacement_mode != "完整" and replacement_candidate_limit <= 0
        replacement_score_func = score if rough_replacement_only else timeline_replacement_first_pass_score
        replacement_base_score = (
            score(tuple(best_team_list))
            if rough_replacement_only
            else timeline_replacement_first_pass_score(tuple(best_team_list))
        )
        analysis_mode_text = "粗算" if rough_replacement_only else ("完整" if replacement_mode == "完整" else "快速")
        if rough_replacement_only:
            expected_per_member = max(1, len(candidate_jobs))
        elif replacement_mode == "完整":
            expected_per_member = max(1, len(candidate_jobs) * 2)
        else:
            expected_per_member = max(3, replacement_candidate_limit + 2) + replacement_candidate_limit
        expected_analysis_steps = len(best_team_list) * expected_per_member
        progress.setLabelText(f"正在计算{analysis_mode_text}替换分析...")
        progress.setValue(910)
        QApplication.processEvents()
        analysis_value = 0
        analysis_cancelled = False

        def replacement_progress(member: str, candidate: str) -> bool:
            nonlocal analysis_value, analysis_cancelled
            if analysis_cancelled:
                return False
            if progress.wasCanceled():
                analysis_cancelled = True
                return False
            analysis_value += 1
            if candidate:
                progress.setLabelText(f"正在分析：{member} -> {candidate}")
            else:
                progress.setLabelText(f"正在准备：{member} 的可替换职业")
            progress.setValue(
                min(975, 910 + int(65 * analysis_value / max(1, expected_analysis_steps)))
            )
            QApplication.processEvents()
            return not progress.wasCanceled()

        grades = self._extreme_fixed_job_grades(
            best_team_list,
            fixed_jobs,
            candidate_jobs,
            replacement_score_func,
            replacement_base_score,
            current_job,
            team_mode,
            require_tank,
            require_healer,
            pre_score_func=score,
            max_candidates_per_member=(
                len(candidate_jobs)
                if replacement_mode == "完整" or rough_replacement_only
                else replacement_candidate_limit
            ),
            max_same_family_candidates=1,
            fast_mode=(replacement_mode != "完整"),
            progress_callback=replacement_progress,
            refine_score_func=(None if rough_replacement_only else timeline_replacement_score),
            refined_best_score=(None if rough_replacement_only else best_score),
            max_refined_candidates_per_member=(
                0
                if rough_replacement_only
                else len(candidate_jobs)
                if replacement_mode == "完整"
                else replacement_candidate_limit
            ),
        )
        if analysis_cancelled:
            self._extreme_dispose_widget(progress)
            return

        progress.setLabelText("正在生成并渲染极限配队结果页面...")
        progress.setValue(980)
        QApplication.processEvents()
        try:
            self._show_extreme_team_result_dialog(
                best_team_list,
                best_score,
                details,
                grades,
                team_mode,
                fixed_jobs,
                candidate_jobs,
                hand_multipliers,
                best_aura_choices,
                best_timelines,
                progress_dialog=progress,
                replacement_context={
                    "baseline_timeline_dps": dict(baseline_timeline_dps),
                    "baseline_static_output": dict(baseline_static_output),
                    "base_unit": base_unit,
                    "base_element_percent": base_element_percent,
                    "hand_multipliers": dict(hand_multipliers),
                },
            )
        finally:
            progress.setValue(1000)
            QApplication.processEvents()
            self._extreme_dispose_widget(progress)

    def _show_extreme_team_result_dialog(
        self,
        team: list[str],
        total_score: float,
        details: list[dict[str, Any]],
        grades: dict[str, dict[str, Any]],
        team_mode: str,
        fixed_jobs: list[str],
        candidate_jobs: list[str],
        hand_multipliers: dict[str, float] | None = None,
        aura_choices: dict[str, str] | None = None,
        timelines: list[dict[str, Any]] | None = None,
        member_builds: list[dict[str, Any] | None] | None = None,
        progress_dialog: QProgressDialog | None = None,
        replacement_context: dict[str, Any] | None = None,
        restored_state: dict[str, Any] | None = None,
    ) -> None:
        restored = copy.deepcopy(restored_state) if isinstance(restored_state, dict) else {}
        loaded_scheme_name = norm(restored.get("_loaded_scheme_name"))
        result_page = getattr(self, "team_extreme_result_page", None)
        if not isinstance(result_page, QWidget):
            return
        page_layout = result_page.layout()
        if not isinstance(page_layout, QVBoxLayout):
            page_layout = QVBoxLayout(result_page)
            page_layout.setContentsMargins(8, 8, 8, 8)
        while page_layout.count():
            item = page_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._extreme_dispose_widget(widget)
        dialog = QWidget(result_page)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(dialog)
        result_toolbar = QHBoxLayout()
        result_toolbar.setContentsMargins(6, 0, 6, 0)
        result_toolbar.addWidget(QLabel("极限优化配队结果"))
        result_toolbar.addStretch(1)
        layout.addLayout(result_toolbar)
        result_pages = QTabWidget()
        result_pages.setObjectName("extremeResultPages")
        summary_page = QWidget()
        summary_page_layout = QVBoxLayout(summary_page)
        summary_page_layout.setContentsMargins(6, 6, 6, 6)
        summary_page_layout.setSpacing(6)
        breakdown_page = QWidget()
        breakdown_page.setObjectName("extremeResultBreakdownPage")
        breakdown_page_layout = QVBoxLayout(breakdown_page)
        breakdown_page_layout.setContentsMargins(6, 6, 6, 6)
        breakdown_page_layout.setSpacing(6)
        timeline_page = QWidget()
        timeline_page_layout = QVBoxLayout(timeline_page)
        timeline_page_layout.setContentsMargins(6, 6, 6, 6)
        custom_timeline_page = QWidget()
        custom_timeline_page.setObjectName("extremeCustomTimelinePage")
        custom_timeline_page_layout = QVBoxLayout(custom_timeline_page)
        custom_timeline_page_layout.setContentsMargins(6, 6, 6, 6)
        custom_timeline_page_layout.setSpacing(6)
        custom_timeline_detail_page = QWidget()
        custom_timeline_detail_page.setObjectName("extremeCustomTimelineDetailPage")
        custom_timeline_detail_page_layout = QVBoxLayout(custom_timeline_detail_page)
        custom_timeline_detail_page_layout.setContentsMargins(6, 6, 6, 6)
        custom_timeline_detail_page_layout.setSpacing(6)
        result_pages.addTab(summary_page, "配队结果")
        result_pages.addTab(breakdown_page, "结果拆解")
        result_pages.addTab(timeline_page, "八人125s轴")
        result_pages.addTab(custom_timeline_page, "自选时间轴")
        result_pages.addTab(custom_timeline_detail_page, "自选时间轴详情")
        layout.addWidget(result_pages, 1)
        summary_label = QLabel()
        summary_label.setObjectName("inlineHint")
        summary_page_layout.addWidget(summary_label)
        aura_row = QHBoxLayout()
        aura_row.addWidget(QLabel("圣骑光环"))
        paladin_aura_combo = Combo(
            TEAM_AURA_OPTIONS,
            norm((aura_choices or {}).get("圣骑士")) or self._team_aura_for_job("圣骑士"),
            min_width=TEAM_AURA_COMBO_WIDTH,
            contents_length=0,
        )
        paladin_aura_combo.setFixedWidth(TEAM_AURA_COMBO_WIDTH)
        paladin_aura_combo.view().setFixedWidth(TEAM_AURA_COMBO_WIDTH)
        aura_row.addWidget(paladin_aura_combo)
        aura_row.addWidget(QLabel("十字光环"))
        crusader_aura_combo = Combo(
            TEAM_AURA_OPTIONS,
            norm((aura_choices or {}).get("十字军")) or self._team_aura_for_job("十字军"),
            min_width=TEAM_AURA_COMBO_WIDTH,
            contents_length=0,
        )
        crusader_aura_combo.setFixedWidth(TEAM_AURA_COMBO_WIDTH)
        crusader_aura_combo.view().setFixedWidth(TEAM_AURA_COMBO_WIDTH)
        aura_row.addWidget(crusader_aura_combo)
        aura_row.addStretch(1)
        summary_page_layout.addLayout(aura_row)
        warning_label = QLabel("")
        warning_label.setObjectName("inlineHint")
        summary_page_layout.addWidget(warning_label)
        personal_formula_label = QLabel(
            "最终配队输出 = 八名职业在转换装备、默认加点、个人Buff和当前团队效果下的125秒实战DPS总和。"
            "职业系数不再参与计算；手部乘区直接作用于对应职业的时间轴DPS。"
        )
        personal_formula_label.setObjectName("inlineHint")
        personal_formula_label.setWordWrap(True)
        summary_page_layout.addWidget(personal_formula_label)
        grade_total_row = QHBoxLayout()
        current_grades = copy.deepcopy(grades)
        replacement_mode_note = norm(getattr(self, "team_extreme_replacement_mode", "")) or "快速"
        grade_box = QGroupBox(f"固定职业评分 / 替换分析（{replacement_mode_note}时间轴）")
        grade_box.setMaximumHeight(178)
        grade_outer_layout = QVBoxLayout(grade_box)
        grade_outer_layout.setContentsMargins(8, 8, 8, 8)
        grade_scroll = QScrollArea()
        grade_scroll.setWidgetResizable(True)
        grade_scroll.setFrameShape(QScrollArea.NoFrame)
        grade_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        grade_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        grade_scroll.setMaximumHeight(148)
        grade_content = QWidget()
        grade_layout = QHBoxLayout(grade_content)
        grade_layout.setContentsMargins(0, 0, 0, 0)
        grade_layout.setSpacing(8)
        grade_colors = {"S": "#dc2626", "A": "#ea580c", "B": "#2563eb", "C": "#64748b", "D": "#475569"}

        def render_grade_cards(grade_rows: dict[str, dict[str, Any]]) -> None:
            while grade_layout.count():
                item = grade_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            for job, info in grade_rows.items():
                grade = norm(info.get("grade"))
                replacement_text = norm(info.get("replacement_options")) or norm(info.get("replacement")) or "无"
                label = QLabel(
                    f"<div style='font-size:24pt;font-weight:900;color:{grade_colors.get(grade, '#64748b')};'>{grade}</div>"
                    f"<div style='font-weight:700;'>{job} <span style='color:#64748b;'>({info.get('role', '队员')})</span></div>"
                    f"<div>可替换：{replacement_text}</div>"
                    f"<div>差距：{number_text(info.get('loss', 0), integer=True)}</div>"
                )
                label.setAlignment(Qt.AlignCenter)
                label.setWordWrap(True)
                label.setToolTip(replacement_text)
                label.setFixedSize(190, 128)
                label.setStyleSheet("background: rgba(255,255,255,0.72); border: 1px solid rgba(148,163,184,0.55); border-radius: 8px; padding: 8px;")
                grade_layout.addWidget(label)
            grade_layout.addStretch(1)

        render_grade_cards(current_grades)
        grade_scroll.setWidget(grade_content)
        grade_outer_layout.addWidget(grade_scroll)
        grade_total_row.addWidget(grade_box, 1)
        team_total_box = QGroupBox("队伍总计")
        team_total_layout = QVBoxLayout(team_total_box)
        team_total_table = AppTableWidget(6, 2)
        team_total_table.setHorizontalHeaderLabels(["项目", "数据"])
        team_total_table.verticalHeader().setVisible(False)
        self._configure_table(team_total_table, [90, 260])
        team_total_table.setMinimumWidth(370)
        team_total_table.setMaximumHeight(220)
        team_total_layout.addWidget(team_total_table)
        grade_total_row.addWidget(team_total_box, 0)
        summary_page_layout.addLayout(grade_total_row)
        team_box = QGroupBox("队伍调整与输出详情")
        team_layout = QVBoxLayout(team_box)
        team_table = AppTableWidget(TEAM_MEMBER_ROWS, 12)
        team_table.setObjectName("extremeTeamAdjustmentTable")
        team_table.setHorizontalHeaderLabels([
            "位置", "职业", "攻/属", "手部乘区", "装备", "伤害面板",
            "基础轴平均%", "基础实战DPS", "配队轴平均%", "配队实战DPS",
            "较基础提升", "占比",
        ])
        team_table.verticalHeader().setVisible(False)
        team_options = ["无"] + unique(candidate_jobs)
        job_combos: list[Combo] = []
        hand_spins: list[QDoubleSpinBox] = []
        build_buttons: list[QPushButton] = []
        member_build_overrides: dict[int, dict[str, Any]] = {}
        member_build_jobs: dict[int, str] = {}
        member_build_by_job: dict[str, dict[str, Any]] = {}
        hand_defaults = hand_multipliers or {}
        hand_value_by_job: dict[str, float] = {}
        row_jobs: list[str] = []
        for row in range(TEAM_MEMBER_ROWS):
            team_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            team_table.setItem(row, 0, self._item(f"队员{row + 1}"))
            combo = Combo(team_options, team[row] if row < len(team) else "无", min_width=132, contents_length=0)
            team_table.setCellWidget(row, 1, combo)
            job_combos.append(combo)
            hand_spin = QDoubleSpinBox()
            hand_spin.setRange(0, 10)
            hand_spin.setDecimals(2)
            hand_spin.setSingleStep(0.05)
            hand_spin.setFixedHeight(EDIT_ROW_HEIGHT)
            row_job = team[row] if row < len(team) else ""
            row_jobs.append(norm(row_job))
            hand_value = float(max(0.0, as_number(
                hand_defaults.get(row_job, hand_defaults.get(self._team_real_job(row_job), 1.0)),
                1.0,
            )))
            hand_spin.setValue(hand_value)
            if norm(row_job) and norm(row_job) != "无":
                hand_value_by_job[norm(row_job)] = hand_value
            team_table.setCellWidget(row, 3, hand_spin)
            hand_spins.append(hand_spin)
            build_button = app_button("修改装备")
            build_button.setObjectName(f"extremeMemberBuildButton{row + 1}")
            build_button.setFixedHeight(EDIT_ROW_HEIGHT)
            team_table.setCellWidget(row, 4, build_button)
            build_buttons.append(build_button)
            if isinstance(member_builds, list) and row < len(member_builds) and isinstance(member_builds[row], dict):
                member_build_overrides[row] = copy.deepcopy(member_builds[row])
                member_build_jobs[row] = row_job
                if norm(row_job) and norm(row_job) != "无":
                    member_build_by_job[norm(row_job)] = copy.deepcopy(member_builds[row])
                build_button.setText("已自定义")
        self._configure_table(
            team_table,
            [58, 136, 66, 82, 94, 100, 106, 112, 106, 118, 96, 62],
        )
        team_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        team_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        team_layout.addWidget(team_table)
        summary_page_layout.addWidget(team_box, 1)

        breakdown_summary_label = QLabel("尚未生成结果拆解。")
        breakdown_summary_label.setObjectName("extremeBreakdownSummary")
        breakdown_summary_label.setProperty("class", "sectionValue")
        breakdown_summary_label.setWordWrap(True)
        breakdown_page_layout.addWidget(breakdown_summary_label)
        breakdown_search_label = QLabel("")
        breakdown_search_label.setObjectName("extremeBreakdownSearchTrace")
        breakdown_search_label.setProperty("class", "inlineHint")
        breakdown_search_label.setWordWrap(True)
        breakdown_page_layout.addWidget(breakdown_search_label)

        breakdown_output_box = QGroupBox("八职业输出路径")
        breakdown_output_layout = QVBoxLayout(breakdown_output_box)
        breakdown_output_table = AppTableWidget(0, 9)
        breakdown_output_table.setObjectName("extremeBreakdownOutputTable")
        breakdown_output_table.setHorizontalHeaderLabels([
            "职业", "个人基础轴DPS", "手部乘区", "手部后DPS", "配队实战DPS",
            "团队净增益", "团队倍率", "较手部后提升", "输出占比",
        ])
        breakdown_output_table.verticalHeader().setVisible(False)
        self._configure_table(
            breakdown_output_table,
            [112, 122, 82, 112, 118, 112, 86, 108, 82],
        )
        breakdown_output_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        breakdown_output_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        breakdown_output_table.setMinimumHeight(250)
        breakdown_output_layout.addWidget(breakdown_output_table)
        breakdown_page_layout.addWidget(breakdown_output_box, 3)

        breakdown_lower = QSplitter(Qt.Horizontal)
        breakdown_lower.setObjectName("extremeBreakdownLowerSplitter")
        breakdown_lower.setChildrenCollapsible(False)
        breakdown_skill_box = QGroupBox("技能输出构成")
        breakdown_skill_layout = QVBoxLayout(breakdown_skill_box)
        breakdown_skill_toolbar = QHBoxLayout()
        breakdown_skill_toolbar.addWidget(QLabel("查看职业"))
        breakdown_skill_combo = Combo(["无"], "无", min_width=150, contents_length=0)
        breakdown_skill_combo.setObjectName("extremeBreakdownJobCombo")
        breakdown_skill_toolbar.addWidget(breakdown_skill_combo)
        breakdown_skill_toolbar.addStretch(1)
        breakdown_skill_layout.addLayout(breakdown_skill_toolbar)
        breakdown_skill_table = AppTableWidget(0, 7)
        breakdown_skill_table.setObjectName("extremeBreakdownSkillTable")
        breakdown_skill_table.setHorizontalHeaderLabels([
            "技能", "类型", "次数", "基础百分比", "计Buff百分比", "估算DPS", "职业占比",
        ])
        breakdown_skill_table.verticalHeader().setVisible(False)
        self._configure_table(breakdown_skill_table, [150, 90, 54, 104, 112, 104, 82])
        breakdown_skill_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        breakdown_skill_layout.addWidget(breakdown_skill_table)
        breakdown_lower.addWidget(breakdown_skill_box)

        breakdown_effect_box = QGroupBox("团队效果与覆盖")
        breakdown_effect_layout = QVBoxLayout(breakdown_effect_box)
        breakdown_effect_table = AppTableWidget(0, 7)
        breakdown_effect_table.setObjectName("extremeBreakdownEffectTable")
        breakdown_effect_table.setHorizontalHeaderLabels([
            "类别", "技能", "来源", "具体效果", "CD", "持续", "理论覆盖",
        ])
        breakdown_effect_table.verticalHeader().setVisible(False)
        self._configure_table(breakdown_effect_table, [100, 138, 108, 190, 68, 68, 82])
        breakdown_effect_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        breakdown_effect_layout.addWidget(breakdown_effect_table)
        breakdown_lower.addWidget(breakdown_effect_box)
        breakdown_lower.setStretchFactor(0, 1)
        breakdown_lower.setStretchFactor(1, 1)
        breakdown_lower.setSizes([720, 720])
        breakdown_page_layout.addWidget(breakdown_lower, 4)

        timeline_box = QGroupBox("八人125s轴")
        timeline_box_layout = QVBoxLayout(timeline_box)
        timeline_zoom_row = QHBoxLayout()
        timeline_zoom_row.addWidget(QLabel("时间轴缩放"))
        timeline_zoom_slider = QSlider(Qt.Horizontal)
        timeline_zoom_slider.setObjectName("extremeTimelineZoomSlider")
        timeline_zoom_slider.setRange(10, 80)
        timeline_zoom_slider.setValue(
            int(max(10, min(80, as_number(restored.get("timeline_zoom"), 30))))
        )
        timeline_zoom_slider.setSingleStep(1)
        timeline_zoom_slider.setPageStep(4)
        timeline_zoom_slider.setMaximumWidth(320)
        timeline_zoom_value = QLabel("30 px/s")
        timeline_zoom_value.setMinimumWidth(58)
        timeline_zoom_row.addWidget(timeline_zoom_slider)
        timeline_zoom_row.addWidget(timeline_zoom_value)
        timeline_zoom_row.addStretch(1)
        timeline_box_layout.addLayout(timeline_zoom_row)
        timeline_tabs = QTabWidget()
        timeline_box_layout.addWidget(timeline_tabs)
        timeline_page_layout.addWidget(timeline_box, 1)
        timeline_axes: list[SkillBurstTimelineWidget] = []

        mechanism_box = QGroupBox("Boss机制输出窗口")
        mechanism_layout = QVBoxLayout(mechanism_box)
        mechanism_layout.setContentsMargins(8, 8, 8, 8)
        mechanism_toolbar = QHBoxLayout()
        mechanism_add_button = app_button("添加停止区间")
        mechanism_undo_button = app_button("撤销")
        mechanism_redo_button = app_button("重做")
        mechanism_clear_button = app_button("一键清除机制区间")
        mechanism_template_combo = Combo(
            ["选择机制模板", *sorted(load_mechanism_templates())],
            "选择机制模板",
            min_width=150,
            contents_length=0,
        )
        mechanism_template_combo.setObjectName("extremeMechanismTemplateCombo")
        mechanism_load_button = app_button("载入")
        mechanism_load_button.setObjectName("extremeMechanismTemplateLoadButton")
        mechanism_save_button = app_button("保存模板")
        mechanism_delete_template_button = app_button("删除模板")
        mechanism_recalc_button = app_button("重新排轴", primary=True)
        mechanism_recalc_button.setObjectName("extremeMechanismRecalcButton")
        mechanism_window_spin = QSpinBox()
        mechanism_window_spin.setObjectName("extremeMechanismWindowSpin")
        mechanism_window_spin.setRange(180, 720)
        mechanism_window_spin.setSingleStep(180)
        mechanism_window_spin.setSuffix("s")
        restored_window = int(max(180, min(720, as_number(restored.get("window"), SKILL_CUSTOM_TIMELINE_WINDOW))))
        mechanism_window_spin.setValue(restored_window)
        mechanism_window_spin.setFixedWidth(92)
        for widget in (
            mechanism_add_button,
            mechanism_undo_button,
            mechanism_redo_button,
            mechanism_clear_button,
        ):
            mechanism_toolbar.addWidget(widget)
        mechanism_toolbar.addSpacing(10)
        mechanism_toolbar.addWidget(QLabel("模板"))
        mechanism_toolbar.addWidget(mechanism_template_combo)
        mechanism_toolbar.addWidget(mechanism_load_button)
        mechanism_toolbar.addWidget(mechanism_save_button)
        mechanism_toolbar.addWidget(mechanism_delete_template_button)
        mechanism_toolbar.addSpacing(10)
        mechanism_toolbar.addWidget(QLabel("最大标尺"))
        mechanism_toolbar.addWidget(mechanism_window_spin)
        mechanism_toolbar.addStretch(1)
        mechanism_toolbar.addWidget(mechanism_recalc_button)
        mechanism_layout.addLayout(mechanism_toolbar)

        mechanism_body = QSplitter(Qt.Horizontal)
        mechanism_body.setObjectName("extremeMechanismBodySplitter")
        mechanism_body.setChildrenCollapsible(False)
        mechanism_visual_box = QGroupBox("机制区间编辑")
        mechanism_visual_layout = QVBoxLayout(mechanism_visual_box)
        mechanism_visual_layout.setContentsMargins(6, 8, 6, 6)
        mechanism_editor = MechanismTimelineEditor(mechanism_window_spin.value())
        mechanism_editor.setObjectName("extremeMechanismTimelineEditor")
        mechanism_editor.set_intervals(restored.get("intervals", []))
        mechanism_visual_layout.addWidget(mechanism_editor)
        mechanism_rule_label = QLabel(
            "停止输出期间不开始新的技能或Buff；区间开始前尚未结束的技能可继续完成，但期间造成的伤害不计。CD、Buff/debuff与召唤物持续时间正常流逝。"
        )
        mechanism_rule_label.setObjectName("inlineHint")
        mechanism_rule_label.setWordWrap(True)
        mechanism_visual_layout.addWidget(mechanism_rule_label)
        mechanism_body.addWidget(mechanism_visual_box)

        mechanism_side_tabs = QTabWidget()
        mechanism_side_tabs.setObjectName("extremeMechanismSideTabs")
        mechanism_side_tabs.setMinimumWidth(500)

        cdr_schedule_box = QGroupBox("团队Buff施放日程")
        cdr_schedule_layout = QVBoxLayout(cdr_schedule_box)
        cdr_schedule_layout.setContentsMargins(6, 8, 6, 6)
        cdr_schedule_toolbar = QHBoxLayout()
        cdr_schedule_status = QLabel("点击“重新排轴”后生成轻量智能日程")
        cdr_schedule_status.setObjectName("inlineHint")
        cdr_schedule_restore_button = app_button("恢复自动")
        cdr_schedule_toolbar.addWidget(cdr_schedule_status)
        cdr_schedule_toolbar.addStretch(1)
        cdr_schedule_toolbar.addWidget(cdr_schedule_restore_button)
        cdr_schedule_layout.addLayout(cdr_schedule_toolbar)
        cdr_schedule_table = AppTableWidget(0, 6)
        cdr_schedule_table.setObjectName("extremeCdrBuffScheduleTable")
        cdr_schedule_table.setHorizontalHeaderLabels([
            "Buff", "提供者", "次数", "施放时刻", "生效区间", "锁定",
        ])
        cdr_schedule_table.verticalHeader().setVisible(False)
        self._configure_table(cdr_schedule_table, [104, 150, 54, 104, 150, 60])
        cdr_schedule_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        cdr_schedule_layout.addWidget(cdr_schedule_table)
        mechanism_side_tabs.addTab(cdr_schedule_box, "团队Buff日程")

        mechanism_interval_box = QGroupBox("停止区间明细")
        mechanism_interval_layout = QVBoxLayout(mechanism_interval_box)
        mechanism_interval_layout.setContentsMargins(6, 8, 6, 6)
        mechanism_interval_table = AppTableWidget(0, 5)
        mechanism_interval_table.setObjectName("extremeMechanismIntervalTable")
        mechanism_interval_table.setHorizontalHeaderLabels(["名称", "停止开始", "恢复输出", "持续时间", "操作"])
        mechanism_interval_table.verticalHeader().setVisible(False)
        self._configure_table(mechanism_interval_table, [180, 110, 110, 100, 74])
        mechanism_interval_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        mechanism_interval_layout.addWidget(mechanism_interval_table)
        mechanism_side_tabs.addTab(mechanism_interval_box, "停止区间")
        mechanism_body.addWidget(mechanism_side_tabs)
        mechanism_body.setStretchFactor(0, 3)
        mechanism_body.setStretchFactor(1, 2)
        mechanism_body.setSizes([820, 520])
        mechanism_body.setMinimumHeight(350)
        mechanism_layout.addWidget(mechanism_body, 1)

        custom_summary_label = QLabel(
            f"{mechanism_window_spin.value()}秒自选轴按每行180秒显示。"
            "修改机制区间或最大标尺后，请点击“重新排轴”应用。"
        )
        custom_summary_label.setObjectName("sectionValue")
        custom_summary_label.setWordWrap(True)
        custom_timeline_page_layout.addWidget(custom_summary_label)

        custom_detail_summary_label = QLabel("尚未生成自选时间轴，请在“自选时间轴”页点击“重新排轴”。")
        custom_detail_summary_label.setObjectName("sectionValue")
        custom_detail_summary_label.setWordWrap(True)
        custom_timeline_detail_page_layout.addWidget(custom_detail_summary_label)

        custom_compare_box = QGroupBox("标准轴 / 自选轴职业对比")
        custom_compare_box.setObjectName("extremeCustomTimelineCompareBox")
        custom_compare_layout = QVBoxLayout(custom_compare_box)
        custom_compare_table = AppTableWidget(0, 6)
        custom_compare_table.setObjectName("extremeCustomTimelineCompareTable")
        custom_compare_table.setHorizontalHeaderLabels([
            "职业", "标准125s DPS", "自选轴 DPS", "DPS损失", "损失率", "自选轴输出占比",
        ])
        custom_compare_table.verticalHeader().setVisible(False)
        self._configure_table(custom_compare_table, [110, 120, 120, 120, 86, 104])
        custom_compare_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        custom_compare_layout.addWidget(custom_compare_table)
        custom_compare_box.setMinimumHeight(190)
        custom_page_splitter = QSplitter(Qt.Vertical)
        custom_page_splitter.setObjectName("extremeCustomPageSplitter")
        custom_page_splitter.setChildrenCollapsible(False)
        custom_page_splitter.addWidget(mechanism_box)
        custom_page_splitter.addWidget(custom_compare_box)
        custom_page_splitter.setStretchFactor(0, 3)
        custom_page_splitter.setStretchFactor(1, 2)
        custom_page_splitter.setSizes([500, 260])
        custom_timeline_page_layout.addWidget(custom_page_splitter, 1)

        custom_axis_box = QGroupBox("自选机制八人时间轴")
        custom_axis_layout = QVBoxLayout(custom_axis_box)
        custom_zoom_row = QHBoxLayout()
        custom_zoom_row.addWidget(QLabel("时间轴缩放"))
        custom_zoom_slider = QSlider(Qt.Horizontal)
        custom_zoom_slider.setRange(10, 80)
        custom_zoom_slider.setValue(int(max(10, min(80, as_number(restored.get("custom_zoom"), 30)))))
        custom_zoom_slider.setMaximumWidth(320)
        custom_zoom_value = QLabel("30 px/s")
        custom_zoom_value.setMinimumWidth(58)
        custom_zoom_row.addWidget(custom_zoom_slider)
        custom_zoom_row.addWidget(custom_zoom_value)
        custom_zoom_row.addStretch(1)
        custom_axis_layout.addLayout(custom_zoom_row)
        custom_timeline_tabs = QTabWidget()
        custom_axis_layout.addWidget(custom_timeline_tabs)
        custom_timeline_detail_page_layout.addWidget(custom_axis_box, 1)
        custom_timeline_axes: list[SkillBurstTimelineWidget] = []

        def set_timeline_zoom(value: int) -> None:
            timeline_zoom_value.setText(f"{value} px/s")
            for timeline_axis in timeline_axes:
                timeline_axis.set_pixels_per_second(value)

        timeline_zoom_slider.valueChanged.connect(set_timeline_zoom)

        def set_custom_timeline_zoom(value: int) -> None:
            custom_zoom_value.setText(f"{value} px/s")
            for timeline_axis in custom_timeline_axes:
                timeline_axis.set_pixels_per_second(value)

        custom_zoom_slider.valueChanged.connect(set_custom_timeline_zoom)
        set_timeline_zoom(timeline_zoom_slider.value())
        set_custom_timeline_zoom(custom_zoom_slider.value())

        def selected_members() -> list[tuple[int, str]]:
            return [
                (row, norm(combo.currentText()))
                for row, combo in enumerate(job_combos)
                if norm(combo.currentText()) and norm(combo.currentText()) != "无"
            ]

        def selected_jobs() -> list[str]:
            return [job for _row, job in selected_members()]

        def selected_build_overrides() -> list[dict[str, Any] | None]:
            overrides: list[dict[str, Any] | None] = []
            for _row, job in selected_members():
                saved = member_build_by_job.get(job)
                overrides.append(copy.deepcopy(saved) if isinstance(saved, dict) else None)
            return overrides

        def selected_auras() -> dict[str, str]:
            return {
                "圣骑士": norm(paladin_aura_combo.currentText()) or TEAM_AURA_OPTIONS[0],
                "十字军": norm(crusader_aura_combo.currentText()) or TEAM_AURA_OPTIONS[0],
            }

        def hand_multiplier_by_job() -> dict[str, float]:
            out: dict[str, float] = {}
            for combo, spin in zip(job_combos, hand_spins):
                job = norm(combo.currentText())
                if not job or job == "无":
                    continue
                out[job] = max(0.0, as_number(spin.value(), 1.0))
            return out

        effects_cache: dict[Any, list[dict[str, Any]]] = {}

        def adjusted_effects(jobs: list[str]) -> list[dict[str, Any]]:
            aura_map = selected_auras()
            key = (tuple(jobs), self._team_aura_key(aura_map))
            if key in effects_cache:
                return effects_cache[key]
            previous_auras = getattr(self, "team_extreme_aura_choices", {})
            self.team_extreme_aura_choices = dict(aura_map)
            try:
                effects = self._team_effects_for_jobs(jobs)
                base_effects = self._team_effects_with_auto_cdr_glyphs(self._dedup_team_effects(effects))
                applied_cdr = {
                    self._team_effect_skill_name(effect.get("skill"))
                    for effect in base_effects
                    if self._team_effect_skill_name(effect.get("skill")) in TEAM_CDR_APPLY_SKILLS
                }
                adjusted = self._team_effects_with_cdr_adjusted_cooldowns(base_effects, applied_cdr)
                effects_cache[key] = adjusted
                return adjusted
            finally:
                self.team_extreme_aura_choices = previous_auras

        def team_total_texts(effects: list[dict[str, Any]]) -> dict[str, str]:
            attrs = self._merged_team_attrs(effects)
            cdr_profile = self._team_cdr_profile(effects)
            freeze_profile = self._team_freeze_profile(effects)
            amp_phys, amp_magic = amplification_channels(attrs)
            defense_phys, defense_magic = defense_reduction_channels(attrs)
            resist_parts = []
            for element, attr in (("火", "减火抗%"), ("水", "减水抗%"), ("光", "减光抗%"), ("暗", "减暗抗%")):
                value = display_value(attrs, attr)
                if abs(value) > 1e-9:
                    resist_parts.append(f"{element}{value:.1f}%")
            return {
                "减抗": "，".join(resist_parts) if resist_parts else "0.0%",
                "减防": f"物 {defense_phys:.1f}%，魔 {defense_magic:.1f}%",
                "减致命抵抗": f"{max(0.0, display_value(attrs, '减致命抵抗%')):.1f}%",
                "-CD": f"等效{as_number(cdr_profile.get('equivalent_cdr')):.1f}% / 平均{as_number(cdr_profile.get('average_cdr')):.1f}% / x{as_number(cdr_profile.get('multiplier')):.3f}",
                "增伤": f"物 {amp_phys:.1f}%，魔 {amp_magic:.1f}%",
                "叠冰": f"{as_number(freeze_profile.get('bonus_percent')):.2f}%（{as_number(freeze_profile.get('layers')):.2f}层）",
            }

        def update_team_totals(effects: list[dict[str, Any]]) -> None:
            totals = team_total_texts(effects)
            rows = [(name, totals[name]) for name in ("减抗", "减防", "减致命抵抗", "-CD", "增伤", "叠冰")]
            for row, (name, value) in enumerate(rows):
                team_total_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                team_total_table.setItem(row, 0, self._item(name))
                self._set_summary_item(team_total_table, row, 1, value, align_right=row not in {0, 4, 5})

        breakdown_data: dict[str, Any] = {"summary": {}, "jobs": [], "skills": {}}

        def render_breakdown_skills(job: str) -> None:
            skill_rows = breakdown_data.get("skills", {}).get(norm(job), [])
            breakdown_skill_table.setRowCount(len(skill_rows))
            for row, item in enumerate(skill_rows):
                values = [
                    norm(item.get("skill")),
                    norm(item.get("type")) or "输出",
                    number_text(item.get("casts"), integer=True),
                    self._percent_text(as_number(item.get("raw_score"))),
                    self._percent_text(as_number(item.get("effective_score"))),
                    self._extreme_compact_rate_text(item.get("estimated_dps")),
                    f"{as_number(item.get('share')):.2f}%",
                ]
                breakdown_skill_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                for column, value in enumerate(values):
                    self._set_summary_item(
                        breakdown_skill_table,
                        row,
                        column,
                        value,
                        align_right=column >= 2,
                    )

        breakdown_skill_combo.currentTextChanged.connect(render_breakdown_skills)

        def result_effect_attr_text(effect: dict[str, Any]) -> str:
            attrs = effect.get("display_attrs", effect.get("attrs", {}))
            if not isinstance(attrs, dict):
                return "-"
            parts: list[str] = []
            for attr, raw_value in attrs.items():
                value = as_number(raw_value)
                if abs(value) <= 1e-9:
                    continue
                suffix = "%" if norm(attr).endswith("%") else ""
                parts.append(f"{norm(attr)} {value:g}{suffix}")
            return "，".join(parts) or "-"

        def render_result_breakdown(
            rows: list[dict[str, Any]],
            results: list[dict[str, Any]],
            effects: list[dict[str, Any]],
        ) -> None:
            nonlocal breakdown_data
            breakdown_data = self._extreme_result_breakdown(rows, results)
            summary = breakdown_data.get("summary", {})
            breakdown_summary_label.setText(
                f"个人基础轴 {self._extreme_compact_rate_text(summary.get('base_total'))}　"
                f"手部修正后 {self._extreme_compact_rate_text(summary.get('hand_total'))}　"
                f"最终团队 {self._extreme_compact_rate_text(summary.get('final_total'))}　"
                f"团队净增益 {self._extreme_signed_rate_text(summary.get('team_gain'))}　"
                f"整体提升 {as_number(summary.get('gain_percent')):+.2f}%"
            )
            diagnostics = getattr(self, "_extreme_search_diagnostics", {})
            diagnostics_match = (
                isinstance(diagnostics, dict)
                and diagnostics
                and sorted(norm(job) for job in diagnostics.get("best_team", []) if norm(job))
                == sorted(norm(job) for job in selected_jobs() if norm(job))
            )
            if diagnostics_match:
                breakdown_search_label.setText(
                    "三级筛选："
                    f"第一层 {int(as_number(diagnostics.get('stage1_full_teams')))} 支完整队伍 → "
                    f"第二层 {int(as_number(diagnostics.get('stage2_screening_candidates')))} 支短轴 → "
                    f"第三层 {int(as_number(diagnostics.get('exact_finalists')))} 支125秒精算"
                    f" + {int(as_number(diagnostics.get('exact_neighbors')))} 支邻域复核；"
                    f"前两名分差 {as_number(diagnostics.get('final_gap_percent')):.2f}%；"
                    f"{norm(diagnostics.get('verification')) or '精算完成'}。"
                )
            else:
                breakdown_search_label.setText(
                    "当前结果来自重新计算或已保存方案；输出拆解直接读取已完成时间轴，不会再次进行配队搜索。"
                )

            job_rows = breakdown_data.get("jobs", [])
            breakdown_output_table.setRowCount(len(job_rows))
            jobs: list[str] = []
            for row, item in enumerate(job_rows):
                job = norm(item.get("job"))
                jobs.append(job)
                values = [
                    job,
                    self._extreme_compact_rate_text(item.get("base_dps")),
                    f"×{as_number(item.get('hand_multiplier'), 1.0):.2f}",
                    self._extreme_compact_rate_text(item.get("hand_dps")),
                    self._extreme_compact_rate_text(item.get("final_dps")),
                    self._extreme_signed_rate_text(item.get("team_gain")),
                    f"×{as_number(item.get('team_multiplier')):.3f}",
                    f"{as_number(item.get('gain_percent')):+.2f}%",
                    f"{as_number(item.get('share')):.2f}%",
                ]
                breakdown_output_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                for column, value in enumerate(values):
                    self._set_summary_item(
                        breakdown_output_table,
                        row,
                        column,
                        value,
                        align_right=column > 0,
                    )
            current_job = norm(breakdown_skill_combo.currentText())
            selected_job = current_job if current_job in jobs else (jobs[0] if jobs else "无")
            self._replace_combo_values(breakdown_skill_combo, jobs or ["无"], selected_job)
            render_breakdown_skills(selected_job)

            effect_rows: list[tuple[int, str, str, str, str, float, float, str]] = []
            category_order = {
                "团队Buff": 0,
                "增伤Debuff": 1,
                "减抗Debuff": 2,
                "减防Debuff": 3,
                "减致抗Debuff": 4,
                "叠冰Debuff": 5,
            }
            for effect in effects:
                if not isinstance(effect, dict):
                    continue
                category, _category_key = self._team_practical_effect_type(effect)
                skill = self._team_effect_skill_name(effect.get("skill"))
                provider = norm(effect.get("provider")) or "全队"
                cooldown = max(0.0, as_number(effect.get("cooldown")))
                duration = max(0.0, as_number(effect.get("duration")))
                if bool(effect.get("permanent")) or cooldown <= 0 and duration <= 0:
                    coverage = "常驻"
                elif cooldown > 0:
                    coverage = f"{min(100.0, duration / cooldown * 100.0):.1f}%"
                else:
                    coverage = "触发后"
                effect_rows.append((
                    category_order.get(category, 9),
                    category,
                    skill,
                    provider,
                    result_effect_attr_text(effect),
                    cooldown,
                    duration,
                    coverage,
                ))
            effect_rows.sort(key=lambda item: (item[0], item[2], item[3]))
            breakdown_effect_table.setRowCount(len(effect_rows))
            for row, (_order, category, skill, provider, attrs_text, cooldown, duration, coverage) in enumerate(effect_rows):
                values = [
                    category,
                    skill,
                    provider,
                    attrs_text,
                    self._cooldown_text(cooldown),
                    self._cooldown_text(duration),
                    coverage,
                ]
                breakdown_effect_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                for column, value in enumerate(values):
                    self._set_summary_item(
                        breakdown_effect_table,
                        row,
                        column,
                        value,
                        align_right=column in {4, 5, 6},
                    )

        def render_timeline_tabs(
            results: list[dict[str, Any]],
            *,
            target_tabs: QTabWidget | None = None,
            target_axes: list[SkillBurstTimelineWidget] | None = None,
            zoom_value: int | None = None,
            blocked_intervals: list[dict[str, Any]] | None = None,
            progress_callback=None,
        ) -> None:
            tabs = target_tabs if isinstance(target_tabs, QTabWidget) else timeline_tabs
            axes = target_axes if isinstance(target_axes, list) else timeline_axes
            zoom = int(zoom_value if zoom_value is not None else timeline_zoom_slider.value())
            tabs.setUpdatesEnabled(False)
            while tabs.count():
                old_page = tabs.widget(0)
                tabs.removeTab(0)
                if old_page is not None:
                    self._extreme_dispose_widget(old_page)
            axes.clear()
            for result_index, result in enumerate(results):
                if callable(progress_callback):
                    progress_callback(result_index, norm(result.get("job")))
                job = norm(result.get("job")) or "未知职业"
                page = QWidget()
                page_layout = QVBoxLayout(page)
                page_layout.setContentsMargins(6, 6, 6, 6)
                total_label = QLabel(
                    f"{job}　窗口 {as_number(result.get('window')):g}s　"
                    f"平均 {self._percent_text(as_number(result.get('average')))}/s　"
                    f"实战DPS {number_text(result.get('dps'), integer=True)}/s　"
                    f"伤害面板 {number_text(result.get('damage_panel'), integer=True)}　"
                    f"手部乘区 ×{as_number(result.get('hand_multiplier'), 1.0):.2f}"
                )
                total_label.setObjectName("inlineHint")
                page_layout.addWidget(total_label)
                timeline_scroll = QScrollArea()
                timeline_scroll.setWidgetResizable(False)
                timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
                axis = SkillBurstTimelineWidget(pixels_per_second=30, single_track=True)
                axis.set_pixels_per_second(zoom)
                axis.set_blocked_intervals(blocked_intervals or [])
                axes.append(axis)
                visible_segments = [
                    segment for segment in result.get("segments", [])
                    if not segment.get("timeline_hidden")
                ]
                axis.set_timeline(
                    as_number(result.get("window"), SKILL_LONG_PRACTICAL_WINDOW),
                    visible_segments,
                    f"{job} 配队长时间实战轴",
                )
                axis.set_effect_windows(self._team_practical_display_effect_windows(
                    list(result.get("external_windows", [])),
                    list(result.get("effect_windows", [])),
                    as_number(result.get("window"), SKILL_LONG_PRACTICAL_WINDOW),
                    norm(result.get("provider")),
                    (
                        set(result.get("relevant_resist_attrs", []))
                        if "relevant_resist_attrs" in result
                        else None
                    ),
                ))
                timeline_scroll.setWidget(axis)
                timeline_scroll.setMinimumHeight(230)
                page_layout.addWidget(timeline_scroll, 1)
                segment_rows = [
                    (axis_index, segment)
                    for axis_index, segment in enumerate(visible_segments)
                    if norm(segment.get("skill"))
                ]
                axis_row_by_index = {axis_index: row for row, (axis_index, _segment) in enumerate(segment_rows)}
                first_axis_index_by_skill: dict[str, int] = {}
                for axis_index, segment in segment_rows:
                    skill_key = norm(segment.get("skill"))
                    if skill_key:
                        first_axis_index_by_skill.setdefault(skill_key, axis_index)
                segment_table = AppTableWidget(len(segment_rows), 6)
                segment_table.setObjectName("extremeTimelineDetailTable")
                segment_table.setHorizontalHeaderLabels(["序号", "技能", "类型", "开始", "结束", "单次输出/DPA"])
                segment_table.verticalHeader().setVisible(False)
                segment_table.setSelectionBehavior(QAbstractItemView.SelectRows)
                segment_table.setSelectionMode(QAbstractItemView.SingleSelection)
                self._configure_table(segment_table, [54, 170, 105, 78, 78, 180])
                segment_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
                for row, (axis_index, segment) in enumerate(segment_rows):
                    start = max(0.0, as_number(segment.get("start")))
                    end = max(start, as_number(segment.get("end")))
                    output = max(0.0, as_number(segment.get("effective_score")))
                    dpa = output / max(1e-9, end - start) if output > 0 and end > start else 0.0
                    if as_number(segment.get("actual_damage")) > 0:
                        output_dpa_text = self._compact_damage_text(segment.get("actual_damage"))
                        if as_number(segment.get("actual_dpa")) > 0:
                            output_dpa_text += f" / {self._compact_damage_text(segment.get('actual_dpa'))}/s"
                    else:
                        output_dpa_text = (
                            f"{self._percent_text(output)} / {self._percent_text(dpa)}/s"
                            if output > 0 else "-"
                        )
                    values = [
                        str(row + 1),
                        norm(segment.get("skill")),
                        norm(segment.get("type")),
                        f"{start:.2f}s",
                        f"{end:.2f}s",
                        output_dpa_text,
                    ]
                    segment_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                    for column, value in enumerate(values):
                        self._set_summary_item(segment_table, row, column, value, align_right=column in {0, 3, 4, 5})
                        item = segment_table.item(row, column)
                        if item is not None:
                            item.setData(Qt.UserRole, axis_index)
                    skill_item = segment_table.item(row, 1)
                    SkillBurstTimelineWidget.apply_skill_item_color(skill_item, segment.get("skill"))

                def scroll_extreme_axis_to(index: int, axis_widget=axis, scroll=timeline_scroll) -> None:
                    if not 0 <= index < len(axis_widget.segments):
                        return
                    rect = (
                        axis_widget._segment_rects[index]
                        if 0 <= index < len(axis_widget._segment_rects)
                        else QRectF()
                    )
                    if rect.isNull():
                        segment = axis_widget.segments[index]
                        start = max(0.0, as_number(segment.get("start")))
                        end = max(start, as_number(segment.get("end")))
                        center_x = 72 + (start + (end - start) / 2.0) * axis_widget.pixels_per_second
                    else:
                        center_x = rect.center().x()
                    bar = scroll.horizontalScrollBar()
                    bar.setValue(max(0, int(center_x - scroll.viewport().width() / 2)))

                def select_extreme_segment(
                    index: int,
                    table=segment_table,
                    axis_widget=axis,
                    row_map=axis_row_by_index,
                    scroll_to=scroll_extreme_axis_to,
                ) -> None:
                    row = row_map.get(index)
                    if row is None or not 0 <= row < table.rowCount():
                        return
                    axis_widget.set_selected_index(index)
                    table.setCurrentCell(row, 1)
                    item = table.item(row, 1) or table.item(row, 0)
                    if item is not None:
                        table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                    scroll_to(index, axis_widget)

                def select_extreme_table_row(
                    row: int,
                    _column: int,
                    table=segment_table,
                    axis_widget=axis,
                    scroll_to=scroll_extreme_axis_to,
                ) -> None:
                    item = table.item(row, 1) or table.item(row, 0)
                    if item is None:
                        return
                    axis_index = item.data(Qt.UserRole)
                    if isinstance(axis_index, int):
                        axis_widget.set_selected_index(axis_index)
                        scroll_to(axis_index, axis_widget)

                axis.segmentSelected.connect(select_extreme_segment)
                segment_table.cellClicked.connect(select_extreme_table_row)

                mixed_damage = job in {"圣骑士", "十字军"}
                damage_segments = [
                    segment for segment in result.get("segments", [])
                    if norm(segment.get("skill"))
                ]
                total_output = sum(
                    max(
                        0.0,
                        as_number(
                            segment.get("actual_damage")
                            if mixed_damage
                            else segment.get("effective_score")
                        ),
                    )
                    for segment in damage_segments
                )
                contributions: dict[str, dict[str, float]] = {}
                breakdown_by_skill: dict[str, dict[str, float]] = {}
                for segment in damage_segments:
                    output = max(
                        0.0,
                        as_number(
                            segment.get("actual_damage")
                            if mixed_damage
                            else segment.get("effective_score")
                        ),
                    )
                    if output <= 0:
                        continue
                    skill = norm(segment.get("skill")) or "未知技能"
                    current = contributions.setdefault(skill, {"count": 0.0, "output": 0.0})
                    current["count"] += 1
                    current["output"] += output
                    breakdown = segment.get("periodic_breakdown")
                    if isinstance(breakdown, dict):
                        breakdown_by_skill[skill] = {
                            key: as_number(value, 1.0)
                            for key, value in breakdown.items()
                        }
                contribution_rows = sorted(
                    contributions.items(),
                    key=lambda item: item[1]["output"],
                    reverse=True,
                )
                contribution_table = AppTableWidget(len(contribution_rows), 4)
                contribution_table.setObjectName("extremeTimelineContributionTable")
                contribution_table.setHorizontalHeaderLabels(["技能", "次数", "总输出", "占比"])
                contribution_table.verticalHeader().setVisible(False)
                contribution_table.setSelectionBehavior(QAbstractItemView.SelectRows)
                contribution_table.setSelectionMode(QAbstractItemView.SingleSelection)
                self._configure_table(contribution_table, [138, 52, 108, 66])
                contribution_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
                for row, (skill, values) in enumerate(contribution_rows):
                    contribution_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                    self._set_summary_item(contribution_table, row, 0, skill)
                    skill_item = contribution_table.item(row, 0)
                    if skill_item is not None:
                        SkillBurstTimelineWidget.apply_skill_item_color(skill_item, skill)
                        skill_item.setData(Qt.UserRole, first_axis_index_by_skill.get(skill, -1))
                        breakdown = breakdown_by_skill.get(skill)
                        if breakdown:
                            skill_item.setToolTip(
                                "输出倍率拆解："
                                f"基础属性/烙印 x{breakdown.get('base', 1.0):.3f} × "
                                f"快照 x{breakdown.get('snapshot', 1.0):.3f} × "
                                f"实时面板/属性 x{breakdown.get('dynamic', 1.0):.3f} × "
                                f"实时减益 x{breakdown.get('realtime_debuff', 1.0):.3f} × "
                                f"动作速度 x{breakdown.get('action_speed', 1.0):.3f}；"
                                f"分时积分精确合计 x{breakdown.get('total', 1.0):.3f}"
                            )
                    self._set_summary_item(contribution_table, row, 1, str(int(values["count"])), align_right=True)
                    self._set_summary_item(
                        contribution_table,
                        row,
                        2,
                        self._compact_damage_text(values["output"])
                        if mixed_damage
                        else self._percent_text(values["output"]),
                        align_right=True,
                    )
                    ratio = values["output"] / total_output * 100.0 if total_output > 0 else 0.0
                    self._set_summary_item(contribution_table, row, 3, f"{ratio:.2f}%", align_right=True)

                def select_extreme_contribution_row(
                    row: int,
                    _column: int,
                    table=contribution_table,
                    select_segment=select_extreme_segment,
                ) -> None:
                    item = table.item(row, 0)
                    if item is None:
                        return
                    axis_index = item.data(Qt.UserRole)
                    if isinstance(axis_index, int) and axis_index >= 0:
                        select_segment(axis_index)

                contribution_table.cellClicked.connect(select_extreme_contribution_row)

                contribution_group = QGroupBox("技能输出占比")
                contribution_layout = QVBoxLayout(contribution_group)
                contribution_layout.setContentsMargins(6, 8, 6, 6)
                contribution_layout.addWidget(contribution_table)
                detail_group = QGroupBox("技能列表 / 具体时刻")
                detail_layout = QVBoxLayout(detail_group)
                detail_layout.setContentsMargins(6, 8, 6, 6)
                detail_layout.addWidget(segment_table)
                analysis = QSplitter(Qt.Horizontal)
                analysis.setChildrenCollapsible(False)
                analysis.addWidget(contribution_group)
                analysis.addWidget(detail_group)
                analysis.setStretchFactor(0, 1)
                analysis.setStretchFactor(1, 2)
                analysis.setSizes([390, 860])
                analysis.setMinimumHeight(260)
                page_layout.addWidget(analysis, 1)
                tabs.addTab(page, job)
                QApplication.processEvents()
            tabs.setUpdatesEnabled(True)

        def timeline_rows_for(
            jobs: list[str],
            effects: list[dict[str, Any]],
            hands: dict[str, float],
            build_overrides: list[dict[str, Any] | None],
            *,
            show_progress: bool,
            window_seconds: float = SKILL_LONG_PRACTICAL_WINDOW,
            blocked_intervals: list[dict[str, Any]] | None = None,
            defer_progress_close: bool = False,
            pinned_cdr_schedule: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
            schedule_builder=None,
            fallback_schedule: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
            schedule_selected_handler=None,
            fast_custom: bool = False,
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], QProgressDialog | None]:
            results: list[dict[str, Any]] = []
            rows: list[dict[str, Any]] = []
            baselines: dict[str, dict[str, Any]] = {}
            calc_progress = None
            if show_progress:
                calc_progress = QProgressDialog(
                    f"正在重新计算八人{number_text(window_seconds)}s轴...",
                    "取消",
                    0,
                    max(1, len(jobs) * 3),
                    dialog,
                )
                calc_progress.setWindowTitle("重新计算极限配队")
                calc_progress.setWindowModality(Qt.NonModal)
                calc_progress.setMinimumDuration(0)
                calc_progress.show()
            previous_process_events = getattr(self, "_extreme_timeline_process_events", False)
            previous_progress_callback = getattr(
                self,
                "_extreme_timeline_progress_callback",
                None,
            )
            progress_state = {
                "job": "",
                "stage": "计算",
                "started": 0.0,
                "last_label": 0.0,
            }

            def timeline_progress_heartbeat(current: float, total: float) -> None:
                if calc_progress is None:
                    return
                now = time.perf_counter()
                if now - as_number(progress_state.get("last_label")) < 0.2:
                    return
                progress_state["last_label"] = now
                elapsed = max(0.0, now - as_number(progress_state.get("started")))
                percent = max(0.0, min(100.0, as_number(current) / max(0.01, as_number(total)) * 100.0))
                calc_progress.setLabelText(
                    f"正在{progress_state['stage']} {progress_state['job']} 的配队实战轴... "
                    f"{elapsed:.1f}s，轴进度 {percent:.0f}%"
                )
                QApplication.processEvents()

            self._extreme_timeline_process_events = bool(show_progress)
            self._extreme_timeline_progress_callback = (
                timeline_progress_heartbeat if show_progress else None
            )
            try:
                task_specs: list[dict[str, Any]] = []
                for index, job in enumerate(jobs):
                    hand = max(0.0, as_number(hands.get(job, hands.get(self._team_real_job(job), 1.0)), 1.0))
                    task_specs.append({
                        "job": job,
                        "team": jobs,
                        "effects": effects,
                        "hand_multiplier": hand,
                        "window": window_seconds,
                        "build_override": (build_overrides[index] if index < len(build_overrides) else None),
                        "blocked_intervals": blocked_intervals,
                        "cast_schedule": pinned_cdr_schedule,
                        "partial_cast_schedule": bool(pinned_cdr_schedule),
                        "include_display_windows": not fast_custom,
                        "fast_long_timeline": fast_custom,
                    })

                def batch_progress(offset: int, stage: str):
                    def update(completed: int, _index: int, job: str) -> None:
                        if calc_progress is None:
                            return
                        progress_state.update({
                            "job": job,
                            "stage": stage,
                            "started": progress_state.get("started") or time.perf_counter(),
                            "last_label": 0.0,
                        })
                        calc_progress.setLabelText(
                            f"正在并行{stage}八职业时间轴... 已完成 {completed}/{len(jobs)}（{job}）"
                        )
                        calc_progress.setValue(offset + completed)
                        QApplication.processEvents()
                    return update

                if calc_progress is not None:
                    progress_state.update({
                        "job": "八职业",
                        "stage": "计算",
                        "started": time.perf_counter(),
                        "last_label": 0.0,
                    })
                    calc_progress.setLabelText("正在启动八职业并行时间轴计算...")
                    QApplication.processEvents()
                results = self._extreme_timeline_tasks_parallel(
                    task_specs,
                    progress_callback=batch_progress(0, "计算"),
                )

                if len(results) == len(jobs):
                    if fast_custom:
                        baseline_results = []
                        for job, result in zip(jobs, results):
                            reference = next(
                                (row for row in current_rows if norm(row.get("job")) == norm(job)),
                                {},
                            )
                            baseline_results.append({
                                "job": job,
                                "dps": as_number(reference.get("base_dps"), result.get("dps")),
                                "average": as_number(reference.get("base_average"), result.get("average")),
                            })
                    else:
                        baseline_specs = [
                            {
                                "job": job,
                                "team": [job],
                                "effects": adjusted_effects([job]),
                                "hand_multiplier": 1.0,
                                "window": window_seconds,
                                "build_override": (build_overrides[index] if index < len(build_overrides) else None),
                            }
                            for index, job in enumerate(jobs)
                        ]
                        baseline_results = self._extreme_timeline_tasks_parallel(
                            baseline_specs,
                            progress_callback=batch_progress(len(jobs), "基准"),
                        )
                    for index, (job, result, baseline) in enumerate(
                        zip(jobs, results, baseline_results)
                    ):
                        reference = next(
                            (row for row in current_rows if norm(row.get("job")) == norm(job)),
                            {},
                        )
                        baselines[job] = baseline
                        profile = self._team_job_attack_profile(job)
                        base_dps = max(0.0, as_number(baseline.get("dps")))
                        team_dps = max(0.0, as_number(result.get("dps")))
                        hand = max(0.0, as_number(hands.get(job, hands.get(self._team_real_job(job), 1.0)), 1.0))
                        rows.append({
                            "job": job,
                            "attack": profile.get("attack", ""),
                            "element": profile.get("element", "") or "无",
                            "damage_panel": as_number(result.get("damage_panel")),
                            "base_average": as_number(baseline.get("average")),
                            "base_dps": base_dps,
                            "team_average": as_number(result.get("average")),
                            "hand_multiplier": hand,
                            "final_output": team_dps,
                            "gain_percent": (team_dps / base_dps - 1.0) * 100.0 if base_dps > 0 else 0.0,
                        })
                if len(results) == len(jobs):
                    initial_results = copy.deepcopy(results)
                    synchronized_schedule = pinned_cdr_schedule
                    if callable(schedule_builder):
                        synchronized_schedule = schedule_builder(results, pinned_cdr_schedule or {})
                    results = self._extreme_synchronized_team_timelines(
                        jobs,
                        effects,
                        hands,
                        results,
                        window_seconds,
                        progress_callback=(
                            lambda index, job: (
                                progress_state.update({
                                    "job": job,
                                    "stage": "同步",
                                    "started": time.perf_counter(),
                                    "last_label": 0.0,
                                }),
                                calc_progress.setLabelText(f"正在同步 {job} 的团队效果时机..."),
                                calc_progress.setValue(len(jobs) * 2 + index),
                                QApplication.processEvents(),
                            )
                            if calc_progress is not None
                            else None
                        ),
                        build_overrides=build_overrides,
                        blocked_intervals=blocked_intervals,
                        pinned_cast_schedule=synchronized_schedule,
                        max_passes=1 if fast_custom else 3,
                        fast_long_timeline=fast_custom,
                    )
                    optimized_total = sum(
                        max(0.0, as_number(result.get("dps")))
                        for result in results
                    )
                    initial_total = sum(
                        max(0.0, as_number(result.get("dps")))
                        for result in initial_results
                    )
                    used_fallback = False
                    if (
                        callable(schedule_builder)
                        and fallback_schedule
                        and optimized_total < initial_total - 1e-6
                    ):
                        fallback_results = self._extreme_synchronized_team_timelines(
                            jobs,
                            effects,
                            hands,
                            initial_results,
                            window_seconds,
                            build_overrides=build_overrides,
                            blocked_intervals=blocked_intervals,
                            pinned_cast_schedule=fallback_schedule,
                            max_passes=1 if fast_custom else 3,
                            fast_long_timeline=fast_custom,
                        )
                        fallback_total = sum(
                            max(0.0, as_number(result.get("dps")))
                            for result in fallback_results
                        )
                        if fallback_total > optimized_total + 1e-6:
                            results = fallback_results
                            used_fallback = True
                    if callable(schedule_selected_handler):
                        schedule_selected_handler(not used_fallback)
                    result_by_job = {norm(result.get("job")): result for result in results}
                    for row in rows:
                        job = norm(row.get("job"))
                        result = result_by_job.get(job, {})
                        baseline = baselines.get(job, {})
                        base_dps = max(0.0, as_number(baseline.get("dps")))
                        team_dps = max(0.0, as_number(result.get("dps")))
                        row["damage_panel"] = as_number(result.get("damage_panel"))
                        row["team_average"] = as_number(result.get("average"))
                        row["final_output"] = team_dps
                        row["gain_percent"] = (team_dps / base_dps - 1.0) * 100.0 if base_dps > 0 else 0.0
            finally:
                self._extreme_timeline_process_events = previous_process_events
                self._extreme_timeline_progress_callback = previous_progress_callback
                if calc_progress is not None and not defer_progress_close:
                    calc_progress.close()
                    self._extreme_dispose_widget(calc_progress)
                    calc_progress = None
            total = sum(as_number(row.get("final_output")) for row in rows)
            for row in rows:
                row["share"] = as_number(row.get("final_output")) / total * 100.0 if total > 0 else 0.0
            return rows, results, calc_progress

        current_rows = copy.deepcopy(details)
        current_timelines = copy.deepcopy(timelines or [])

        def refresh_replacement_analysis() -> None:
            nonlocal current_grades
            context = replacement_context if isinstance(replacement_context, dict) else {}
            baseline_dps = context.get("baseline_timeline_dps", {})
            baseline_static = context.get("baseline_static_output", {})
            if not isinstance(baseline_dps, dict) or not isinstance(baseline_static, dict):
                return
            jobs = selected_jobs()
            if len(jobs) != TEAM_MEMBER_ROWS:
                current_grades = {}
                render_grade_cards(current_grades)
                return
            base_unit_value = max(0.0, as_number(context.get("base_unit")))
            base_element_value = max(0.0, as_number(context.get("base_element_percent")))
            if base_unit_value <= 0:
                return
            live_hands = dict(context.get("hand_multipliers", {}))
            live_hands.update(hand_multiplier_by_job())
            score_cache: dict[tuple[str, ...], float] = {}

            def hand_for(job: str) -> float:
                return max(
                    0.0,
                    as_number(
                        live_hands.get(job, live_hands.get(self._team_real_job(job), 1.0)),
                        1.0,
                    ),
                )

            def pre_score(team_rows: tuple[str, ...]) -> float:
                return sum(
                    max(0.0, as_number(baseline_dps.get(job))) * hand_for(job)
                    for job in team_rows
                )

            def rough_score(team_rows: tuple[str, ...]) -> float:
                key = tuple(sorted(team_rows))
                if key in score_cache:
                    return score_cache[key]
                effects = adjusted_effects(list(key))
                static_values = self._team_extreme_output_values(
                    list(key),
                    effects,
                    base_unit_value,
                    {job: 1.0 for job in key},
                    base_element_value,
                )
                value = 0.0
                for job in key:
                    own_static = max(0.0, as_number(baseline_static.get(job)))
                    team_static = max(0.0, as_number(static_values.get(job)))
                    team_ratio = team_static / own_static if own_static > 0 else 1.0
                    value += max(0.0, as_number(baseline_dps.get(job))) * team_ratio * hand_for(job)
                score_cache[key] = value
                return value

            candidate_limit = max(
                2,
                min(5, int(as_number(getattr(self, "team_extreme_replacement_candidates", 2), 2))),
            )
            base_score = rough_score(tuple(jobs))
            current_grades = self._extreme_fixed_job_grades(
                jobs,
                [job for job in fixed_jobs if job in jobs],
                candidate_jobs,
                rough_score,
                base_score,
                norm(self.second_job.currentText()),
                team_mode,
                bool(getattr(self, "team_extreme_require_tank", True)),
                bool(getattr(self, "team_extreme_require_healer", True)),
                pre_score_func=pre_score,
                max_candidates_per_member=candidate_limit,
                max_same_family_candidates=1,
                fast_mode=True,
            )
            render_grade_cards(current_grades)

        def render(
            rows: list[dict[str, Any]],
            results: list[dict[str, Any]],
            render_progress: QProgressDialog | None = None,
        ) -> None:
            jobs = selected_jobs()
            duplicate = self._team_has_real_duplicate(jobs)
            legal = (
                len(jobs) == TEAM_MEMBER_ROWS
                and not duplicate
                and any(self._team_real_job(job) in TEAM_TANK_JOBS for job in jobs)
                and any(self._team_real_job(job) in TEAM_HEALER_JOBS for job in jobs)
            )
            if not legal:
                warning_label.setText("当前队伍需要 8 个不重复职业（打坐曜/输出曜例外），并且至少包含 1 个 T 位和 1 个奶位；此时明细仍会计算，但不视为合法极限队伍。")
            else:
                warning_label.setText("")
            effects = adjusted_effects(jobs)
            score = sum(max(0.0, as_number(row.get("final_output"))) for row in rows)
            update_team_totals(effects)
            render_result_breakdown(rows, results, effects)
            summary_label.setText(
                f"队伍类型：{team_mode or '不限'}    八人配队实战DPS总和：{number_text(score, integer=True)}/s    "
                f"队伍：{'、'.join(jobs) if jobs else '无'}"
            )
            team_table.setRowCount(max(TEAM_MEMBER_ROWS, len(rows)))
            for row, item in enumerate(rows):
                job = norm(item.get("job", ""))
                simulated_output = as_number(item.get("final_output"))
                values = [
                    f"{item.get('attack', '')}/{item.get('element', '无')}",
                    number_text(item.get("damage_panel"), integer=True),
                    f"{self._percent_text(as_number(item.get('base_average')))}/s",
                    f"{number_text(item.get('base_dps'), integer=True)}/s",
                    f"{self._percent_text(as_number(item.get('team_average')))}/s",
                    f"{number_text(simulated_output, integer=True)}/s",
                    f"{as_number(item.get('gain_percent')):+.2f}%",
                    f"{simulated_output / score * 100 if score > 0 else 0.0:.1f}%",
                ]
                for col, value in zip((2, 5, 6, 7, 8, 9, 10, 11), values):
                    self._set_summary_item(team_table, row, col, value, align_right=col != 2)
                team_table.setRowHeight(row, EDIT_ROW_HEIGHT)
            render_base = render_progress.value() if render_progress is not None else 0
            render_span = (
                max(1, render_progress.maximum() - render_base)
                if render_progress is not None else 1
            )

            def render_step(index: int, job: str) -> None:
                if render_progress is None:
                    return
                render_progress.setLabelText(f"正在渲染 {job} 的125s时间轴...")
                render_progress.setValue(
                    min(
                        render_progress.maximum(),
                        render_base + int(render_span * (index + 1) / max(1, len(results))),
                    )
                )

            render_timeline_tabs(results, progress_callback=render_step)

        def mark_dirty() -> None:
            warning_label.setText("队伍、光环或手部乘区已修改，请点击“重新计算时间轴”更新结果。")
            custom_summary_label.setText("队伍设置已修改，请先重新计算标准125秒轴。")
            custom_detail_summary_label.setText("队伍设置已修改；当前详情是上一次结果，请重新计算标准轴后再点击“重新排轴”。")
            jobs = unique(selected_jobs())
            current = norm(convert_job_combo.currentText())
            target = current if current in jobs else (jobs[0] if jobs else "")
            self._replace_combo_values(convert_job_combo, jobs or ["无"], target or "无")

        def member_job_changed(row: int) -> None:
            current_job = norm(job_combos[row].currentText())
            previous_job = row_jobs[row]
            if previous_job and previous_job != "无":
                hand_value_by_job[previous_job] = max(0.0, as_number(hand_spins[row].value(), 1.0))
                previous_build = member_build_overrides.get(row)
                if member_build_jobs.get(row) == previous_job and isinstance(previous_build, dict):
                    member_build_by_job[previous_job] = copy.deepcopy(previous_build)
            row_jobs[row] = current_job
            target_hand = hand_value_by_job.get(
                current_job,
                max(0.0, as_number(
                    hand_defaults.get(current_job, hand_defaults.get(self._team_real_job(current_job), 1.0)),
                    1.0,
                )),
            )
            hand_spins[row].blockSignals(True)
            hand_spins[row].setValue(float(target_hand))
            hand_spins[row].blockSignals(False)
            saved_build = member_build_by_job.get(current_job)
            if current_job and current_job != "无" and isinstance(saved_build, dict):
                member_build_overrides[row] = copy.deepcopy(saved_build)
                member_build_jobs[row] = current_job
                build_buttons[row].setText("已自定义")
            else:
                member_build_overrides.pop(row, None)
                member_build_jobs.pop(row, None)
                build_buttons[row].setText("修改装备")
            mark_dirty()

        def hand_multiplier_changed(row: int) -> None:
            current_job = norm(job_combos[row].currentText())
            if current_job and current_job != "无":
                hand_value_by_job[current_job] = max(0.0, as_number(hand_spins[row].value(), 1.0))
            mark_dirty()

        for row, combo in enumerate(job_combos):
            combo.currentTextChanged.connect(lambda _text, r=row: member_job_changed(r))
        paladin_aura_combo.currentTextChanged.connect(lambda _text: mark_dirty())
        crusader_aura_combo.currentTextChanged.connect(lambda _text: mark_dirty())
        for row, spin in enumerate(hand_spins):
            spin.valueChanged.connect(lambda _value, r=row: hand_multiplier_changed(r))
        render(current_rows, current_timelines, progress_dialog)

        buttons = QHBoxLayout()
        recalc_button = app_button("重新计算时间轴", primary=True)

        def recalculate(
            _checked: bool = False,
            *,
            announce_loaded_scheme: bool = True,
        ) -> bool:
            nonlocal current_rows, current_timelines
            self._reset_extreme_timeline_context(getattr(self, "team_extreme_boss", "无"))
            effects_cache.clear()
            jobs = selected_jobs()
            effects = adjusted_effects(jobs)
            current_rows, current_timelines, recalc_progress = timeline_rows_for(
                jobs,
                effects,
                hand_multiplier_by_job(),
                selected_build_overrides(),
                show_progress=True,
                defer_progress_close=True,
            )
            try:
                if recalc_progress is not None:
                    recalc_progress.setMaximum(max(1, len(jobs) * 2 + len(current_timelines)))
                    recalc_progress.setLabelText("正在渲染配队结果与八人125s轴...")
                render(current_rows, current_timelines, recalc_progress)
                if recalc_progress is not None:
                    recalc_progress.setLabelText("正在刷新当前职业的替换分析...")
                    QApplication.processEvents()
                refresh_replacement_analysis()
                custom_summary_label.setText("标准125秒轴已更新；自选轴不会自动变化，请点击“重新排轴”更新。")
                custom_detail_summary_label.setText("标准轴或队伍设置已变化；当前详情是上一次结果，请点击“重新排轴”更新。")
                if loaded_scheme_name and announce_loaded_scheme:
                    recalculated_score = sum(
                        max(0.0, as_number(row.get("final_output")))
                        for row in current_rows
                    )
                    recalculated_jobs = selected_jobs()
                    self._set_extreme_history_status(
                        f"既往极限配队方案“{loaded_scheme_name}”的125秒时间轴优化完成："
                        f"{'、'.join(recalculated_jobs) if recalculated_jobs else '无有效队员'}；"
                        f"八人配队实战DPS总和 {number_text(recalculated_score, integer=True)}/s。"
                        "可在“极限优化配队结果”页查看完整结果。",
                        "success",
                    )
            finally:
                if recalc_progress is not None:
                    self._extreme_dispose_widget(recalc_progress)
            return True

        recalc_button.clicked.connect(recalculate)

        custom_rows: list[dict[str, Any]] = copy.deepcopy(restored.get("custom_rows", []))
        custom_timelines: list[dict[str, Any]] = copy.deepcopy(restored.get("custom_timelines", []))
        optimized_cdr_rows: list[dict[str, Any]] = copy.deepcopy(restored.get("optimized_cdr_rows", []))
        manual_cdr_locks: dict[tuple[str, str, int], float] = copy.deepcopy(
            restored.get("manual_cdr_locks", {})
        )
        custom_axis_cache: dict[
            Any,
            tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
        ] = {}
        syncing_mechanism_table = False

        def result_state_snapshot() -> dict[str, Any]:
            return {
                "window": mechanism_window_spin.value(),
                "intervals": copy.deepcopy(mechanism_editor.intervals),
                "custom_rows": copy.deepcopy(custom_rows),
                "custom_timelines": copy.deepcopy(custom_timelines),
                "optimized_cdr_rows": copy.deepcopy(optimized_cdr_rows),
                "manual_cdr_locks": copy.deepcopy(manual_cdr_locks),
                "timeline_zoom": timeline_zoom_slider.value(),
                "custom_zoom": custom_zoom_slider.value(),
                "page_index": result_pages.currentIndex(),
                "page_name": result_pages.tabText(result_pages.currentIndex()),
            }

        def custom_result_cache_key(
            jobs: list[str],
            effects: list[dict[str, Any]],
            intervals: list[dict[str, Any]],
            pinned_schedule: dict[tuple[str, str], list[tuple[float, float]]],
        ) -> Any:
            cache_build = self.collect_build()
            cache_build.pop("team_sim", None)
            return (
                tuple(jobs),
                self._team_practical_cache_value(effects),
                self._team_practical_cache_value(hand_multiplier_by_job()),
                self._team_practical_cache_value(selected_build_overrides()),
                self._team_practical_cache_value(cache_build),
                round(custom_window_seconds(), 4),
                self._team_practical_cache_value(intervals),
                self._team_practical_cache_value(pinned_schedule),
                self._team_practical_cache_value(manual_cdr_locks),
            )

        def custom_window_seconds() -> float:
            return max(180.0, min(720.0, as_number(mechanism_window_spin.value(), 600.0)))

        def schedule_rows_to_casts(
            rows: list[dict[str, Any]],
        ) -> dict[tuple[str, str], list[tuple[float, float]]]:
            schedule: dict[tuple[str, str], list[tuple[float, float]]] = {}
            for cast in rows:
                key = (
                    norm(cast.get("provider")),
                    self._skill_burst_skill_key(cast.get("skill")),
                )
                if not all(key):
                    continue
                schedule.setdefault(key, []).append((
                    as_number(cast.get("cast_start")),
                    as_number(cast.get("cast_end")),
                ))
            return schedule

        def intermittent_team_buff_specs(
            effects: list[dict[str, Any]],
            jobs: list[str],
            initial_results: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            result_by_job = {
                norm(result.get("job")): result
                for result in initial_results
            }
            specs: list[dict[str, Any]] = []
            for effect in effects:
                skill = self._skill_burst_skill_key(
                    self._team_effect_skill_name(effect.get("skill"))
                )
                _effect_type, category = self._team_practical_effect_type(effect)
                if (
                    not skill
                    or category != "team_buff"
                    or skill in {"鸡尾酒", "查克拉治愈术", "阿兹那之息"}
                    or bool(effect.get("full_time_equivalent"))
                    or self._team_practical_is_continuous_effect(effect)
                    or not self._team_practical_effect_affects_output(effect)
                ):
                    continue
                cooldown = max(0.0, as_number(effect.get("cooldown")))
                duration = max(0.0, as_number(effect.get("duration")))
                if cooldown <= 0 or duration <= 0:
                    continue
                attrs = {
                    norm(attr): as_number(value)
                    for attr, value in {
                        **effect.get("display_attrs", {}),
                        **effect.get("attrs", {}),
                    }.items()
                    if norm(attr) and abs(as_number(value)) > 1e-9
                }
                job_weights: dict[str, float] = {}
                for job in jobs:
                    base_output = max(
                        1.0,
                        as_number(result_by_job.get(norm(job), {}).get("damage_panel"), 1.0),
                    )
                    baseline_multiplier = max(1e-9, as_number(self._team_extreme_job_breakdown(
                        job,
                        {},
                        base_output,
                        include_boss=True,
                        include_base_element=False,
                        base_neutral_percent=as_number(result_by_job.get(norm(job), {}).get('summary', {}).get('无属性%')),
                    ).get("total_multiplier"), 1.0))
                    multiplier = as_number(self._team_extreme_job_breakdown(
                        job,
                        attrs,
                        base_output,
                        include_boss=True,
                        include_base_element=False,
                        base_neutral_percent=as_number(result_by_job.get(norm(job), {}).get('summary', {}).get('无属性%')),
                    ).get("total_multiplier"), 1.0)
                    gain = max(0.0, multiplier / baseline_multiplier - 1.0)
                    if gain > 1e-9:
                        job_weights[job] = gain
                # CDR/action speed is a timeline gain, so it does not appear
                # in the static panel breakdown. Estimate its marginal value
                # here; the synchronized eight-player timeline below remains
                # the authoritative validation.
                if any(
                    token in attr
                    for attr in attrs
                    for token in ("冷却缩减", "动作速度", "攻速")
                ):
                    cdr = min(70.0, max(
                        [abs(value) for attr, value in attrs.items() if "冷却缩减" in attr]
                        or [0.0]
                    )) / 100.0
                    action_speed = max(
                        [abs(value) for attr, value in attrs.items() if any(
                            token in attr for token in ("动作速度", "攻速")
                        )] or [0.0]
                    ) / 100.0
                    usage_ratio = max(0.0, min(
                        1.0,
                        as_number(
                            effect.get("usage_ratio"),
                            self._team_effect_usage_default(skill),
                        ) / 100.0,
                    ))
                    cdr_gain = (1.0 / max(0.3, 1.0 - cdr) - 1.0) * usage_ratio
                    action_gain = action_speed * usage_ratio * 0.35
                    timeline_gain = min(3.0, cdr_gain + action_gain)
                    if timeline_gain > 1e-9:
                        for job in jobs:
                            job_weights[job] = max(
                                job_weights.get(job, 0.0),
                                timeline_gain,
                            )
                if not job_weights:
                    continue
                exclusive_group = self._skill_burst_exclusive_effect_group(skill)
                specs.append({
                    "provider": norm(effect.get("provider")),
                    "skill": skill,
                    "stack_group": exclusive_group or skill,
                    "cooldown": cooldown,
                    "duration": duration,
                    "cast_time": self._team_practical_effect_cast_time(effect),
                    "job_weights": job_weights,
                })
            return specs

        def rebuild_cdr_schedule_table(rows: list[dict[str, Any]]) -> None:
            self._extreme_clear_table_widgets(cdr_schedule_table, (3, 5))
            cdr_schedule_table.setRowCount(len(rows))
            for row_index, cast in enumerate(rows):
                provider = norm(cast.get("provider"))
                skill = norm(cast.get("skill"))
                occurrence = int(as_number(cast.get("occurrence")))
                lock_key = (provider, skill, occurrence)
                cdr_schedule_table.setRowHeight(row_index, EDIT_ROW_HEIGHT)
                self._set_summary_item(cdr_schedule_table, row_index, 0, skill)
                self._set_summary_item(cdr_schedule_table, row_index, 1, provider)
                self._set_summary_item(cdr_schedule_table, row_index, 2, occurrence + 1, align_right=True)
                start_spin = QDoubleSpinBox()
                start_spin.setRange(0.0, custom_window_seconds())
                start_spin.setDecimals(1)
                start_spin.setSingleStep(0.5)
                start_spin.setSuffix("s")
                start_spin.setValue(as_number(cast.get("cast_start")))
                cdr_schedule_table.setCellWidget(row_index, 3, start_spin)
                self._set_summary_item(
                    cdr_schedule_table,
                    row_index,
                    4,
                    f"{as_number(cast.get('effect_start')):.1f}-{as_number(cast.get('effect_end')):.1f}s",
                )
                lock_check = QCheckBox()
                lock_check.setChecked(lock_key in manual_cdr_locks)
                lock_wrap = QWidget()
                lock_layout = QHBoxLayout(lock_wrap)
                lock_layout.setContentsMargins(0, 0, 0, 0)
                lock_layout.setAlignment(Qt.AlignCenter)
                lock_layout.addWidget(lock_check)
                cdr_schedule_table.setCellWidget(row_index, 5, lock_wrap)

                def update_lock(
                    _value=None,
                    *,
                    key=lock_key,
                    spin=start_spin,
                    check=lock_check,
                ) -> None:
                    if check.isChecked():
                        manual_cdr_locks[key] = spin.value()
                    else:
                        manual_cdr_locks.pop(key, None)
                    custom_summary_label.setText(
                        "团队Buff日程已修改；点击“重新排轴”后应用。"
                    )

                lock_check.toggled.connect(update_lock)
                start_spin.valueChanged.connect(update_lock)
            cdr_schedule_status.setText(
                f"共 {len(rows)} 次施放；已锁定 {len(manual_cdr_locks)} 次。"
            )

        def restore_automatic_cdr_schedule() -> None:
            manual_cdr_locks.clear()
            rebuild_cdr_schedule_table(optimized_cdr_rows)
            custom_summary_label.setText("已清除团队Buff手动锁定；点击“重新排轴”应用自动日程。")

        cdr_schedule_restore_button.clicked.connect(restore_automatic_cdr_schedule)

        def rebuild_mechanism_table(intervals: list[dict[str, Any]]) -> None:
            nonlocal syncing_mechanism_table
            syncing_mechanism_table = True
            mechanism_interval_table.blockSignals(True)
            self._extreme_clear_table_widgets(mechanism_interval_table, (1, 2, 4))
            mechanism_interval_table.setRowCount(len(intervals))
            for row, interval in enumerate(intervals):
                mechanism_interval_table.setRowHeight(row, EDIT_ROW_HEIGHT)
                name_item = self._item(interval.get("name", f"机制{row + 1}"))
                name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
                mechanism_interval_table.setItem(row, 0, name_item)
                start_spin = QDoubleSpinBox()
                start_spin.setRange(0, custom_window_seconds())
                start_spin.setDecimals(1)
                start_spin.setSingleStep(0.5)
                start_spin.setSuffix(" s")
                start_spin.setValue(as_number(interval.get("start")))
                start_spin.setFixedHeight(EDIT_ROW_HEIGHT)
                end_spin = QDoubleSpinBox()
                end_spin.setRange(0, custom_window_seconds())
                end_spin.setDecimals(1)
                end_spin.setSingleStep(0.5)
                end_spin.setSuffix(" s")
                end_spin.setValue(as_number(interval.get("end")))
                end_spin.setFixedHeight(EDIT_ROW_HEIGHT)
                mechanism_interval_table.setCellWidget(row, 1, start_spin)
                mechanism_interval_table.setCellWidget(row, 2, end_spin)
                self._set_summary_item(
                    mechanism_interval_table,
                    row,
                    3,
                    f"{max(0.0, as_number(interval.get('end')) - as_number(interval.get('start'))):.1f}s",
                    align_right=True,
                )
                delete_button = app_button("删除")
                delete_button.setFixedHeight(EDIT_ROW_HEIGHT)
                mechanism_interval_table.setCellWidget(row, 4, delete_button)

                def update_time(target_row: int, key: str, value: float) -> None:
                    if not 0 <= target_row < len(mechanism_editor.intervals):
                        return
                    changed = copy.deepcopy(mechanism_editor.intervals)
                    changed[target_row][key] = value
                    mechanism_editor.set_intervals(changed, record=True)

                start_spin.editingFinished.connect(
                    lambda r=row, spin=start_spin: update_time(r, "start", spin.value())
                )
                end_spin.editingFinished.connect(
                    lambda r=row, spin=end_spin: update_time(r, "end", spin.value())
                )

                def delete_interval(target_row: int) -> None:
                    mechanism_editor.selected_index = target_row
                    mechanism_editor.remove_selected()

                delete_button.clicked.connect(
                    lambda _checked=False, r=row: delete_interval(r)
                )
            mechanism_interval_table.blockSignals(False)
            syncing_mechanism_table = False

        def mechanism_name_changed(item: QTableWidgetItem) -> None:
            if syncing_mechanism_table or item.column() != 0:
                return
            row = item.row()
            if not 0 <= row < len(mechanism_editor.intervals):
                return
            changed = copy.deepcopy(mechanism_editor.intervals)
            changed[row]["name"] = norm(item.text()) or f"机制{row + 1}"
            mechanism_editor.set_intervals(changed, record=True)

        mechanism_interval_table.itemChanged.connect(mechanism_name_changed)

        def render_custom_results(
            intervals: list[dict[str, Any]],
            rows: list[dict[str, Any]],
            results: list[dict[str, Any]],
            progress: QProgressDialog | None = None,
        ) -> None:
            horizon = custom_window_seconds()
            stopped = mechanism_blocked_seconds(intervals, horizon)
            active = mechanism_active_seconds(intervals, horizon)
            standard_by_job = {
                norm(result.get("job")): result for result in current_timelines
            }
            custom_by_job = {norm(result.get("job")): result for result in results}
            standard_dps = sum(max(0.0, as_number(result.get("dps"))) for result in current_timelines)
            custom_dps = sum(max(0.0, as_number(result.get("dps"))) for result in results)
            standard_damage = standard_dps * horizon
            custom_damage = custom_dps * horizon
            active_dps = custom_damage / active if active > 1e-9 else 0.0
            loss = max(0.0, standard_damage - custom_damage)
            loss_rate = loss / standard_damage * 100.0 if standard_damage > 0 else 0.0
            summary_text = (
                f"总输出 {number_text(custom_damage, integer=True)}　"
                f"整场DPS {number_text(custom_dps, integer=True)}/s　"
                f"有效输出时间DPS {number_text(active_dps, integer=True)}/s　"
                f"停止 {stopped:.1f}s / 输出 {active:.1f}s　"
                f"较标准125秒轴平均DPS投影损失 {number_text(loss, integer=True)}（{loss_rate:.2f}%）"
            )
            custom_summary_label.setText(summary_text)
            custom_detail_summary_label.setText(summary_text)

            jobs = selected_jobs()
            output_shares = self._extreme_output_shares(
                max(0.0, as_number(custom_by_job.get(job, {}).get("dps")))
                for job in jobs
            )
            custom_compare_table.setRowCount(len(jobs))
            for row_index, job in enumerate(jobs):
                standard_value = max(0.0, as_number(standard_by_job.get(job, {}).get("dps")))
                custom_value = max(0.0, as_number(custom_by_job.get(job, {}).get("dps")))
                job_loss = max(0.0, standard_value - custom_value)
                job_loss_rate = (
                    job_loss / standard_value * 100.0
                    if standard_value > 0 else 0.0
                )
                values = [
                    job,
                    self._extreme_compact_rate_text(standard_value),
                    self._extreme_compact_rate_text(custom_value),
                    self._extreme_compact_rate_text(job_loss),
                    f"{job_loss_rate:.2f}%",
                    f"{output_shares[row_index]:.2f}%",
                ]
                custom_compare_table.setRowHeight(row_index, EDIT_ROW_HEIGHT)
                for column, value in enumerate(values):
                    self._set_summary_item(
                        custom_compare_table,
                        row_index,
                        column,
                        value,
                        align_right=column != 0,
                    )

            if progress is not None:
                progress.setMaximum(max(progress.maximum(), len(jobs) * 2 + len(results)))
                progress.setLabelText("正在渲染自选机制八人时间轴...")
            render_timeline_tabs(
                results,
                target_tabs=custom_timeline_tabs,
                target_axes=custom_timeline_axes,
                zoom_value=custom_zoom_slider.value(),
                blocked_intervals=intervals,
                progress_callback=(
                    lambda index, job: (
                        progress.setLabelText(f"正在渲染 {job} 的自选机制时间轴..."),
                        progress.setValue(min(progress.maximum(), len(jobs) * 2 + index + 1)),
                    )
                    if progress is not None
                    else None
                ),
            )

        def recalculate_custom_timeline(_checked: bool = False) -> bool:
            nonlocal custom_rows, custom_timelines, optimized_cdr_rows
            jobs = selected_jobs()
            if len(jobs) != TEAM_MEMBER_ROWS:
                custom_summary_label.setText("需要先选择完整的八人队伍。")
                custom_detail_summary_label.setText("需要先选择完整的八人队伍。")
                return False
            if not mechanism_recalc_button.isEnabled():
                return False
            mechanism_recalc_button.setEnabled(False)
            mechanism_recalc_button.setText("正在排轴...")
            cdr_schedule_status.setText("正在计算团队Buff日程...")
            custom_summary_label.setText("正在准备自选机制时间轴，请稍候...")
            QApplication.processEvents()
            intervals = normalize_mechanism_intervals(
                mechanism_editor.intervals,
                custom_window_seconds(),
            )
            progress = None
            succeeded = False
            try:
                self._reset_extreme_timeline_context(getattr(self, "team_extreme_boss", "无"))
                effects_cache.clear()
                effects = adjusted_effects(jobs)
                schedule_started = time.perf_counter()
                cdr_specs: list[dict[str, Any]] = []
                for effect in effects:
                    skill = self._skill_burst_skill_key(
                        self._team_effect_skill_name(effect.get("skill"))
                    )
                    if skill not in {"精神爆发", "时间加速"}:
                        continue
                    cdr_specs.append({
                        "provider": norm(effect.get("provider")),
                        "skill": skill,
                        "cooldown": max(0.01, as_number(effect.get("cooldown"))),
                        "duration": max(0.0, as_number(effect.get("duration"))),
                        "cast_time": self._team_practical_effect_cast_time(effect),
                    })
                # The custom mechanism axis has its own output windows. Do not
                # inherit the long-axis manual anchors as hard phases: the team
                # scheduler chooses every cast from the complete damage curve.
                preferred_starts = {"精神爆发": 0.0, "时间加速": 0.0}
                optimized_cdr_rows = lightweight_cdr_buff_schedule(
                    cdr_specs,
                    intervals,
                    custom_window_seconds(),
                    preferred_starts,
                    manual_cdr_locks,
                )
                baseline_cdr_rows = copy.deepcopy(optimized_cdr_rows)
                pinned_cdr_schedule = schedule_rows_to_casts(optimized_cdr_rows)
                schedule_elapsed = time.perf_counter() - schedule_started
                cdr_schedule_status.setText(
                    f"减CD预排程 {schedule_elapsed:.3f}s；{len(optimized_cdr_rows)} 次施放，"
                    f"已锁定 {len(manual_cdr_locks)} 次。"
                )
                custom_summary_label.setText(
                    f"团队减CD Buff预排程完成：{len(optimized_cdr_rows)} 次施放，"
                    f"耗时 {schedule_elapsed:.3f}s；正在生成初始输出密度。"
                )
                QApplication.processEvents()
                cache_key = custom_result_cache_key(
                    jobs,
                    effects,
                    intervals,
                    pinned_cdr_schedule,
                )
                cached = custom_axis_cache.get(cache_key)
                if cached is not None:
                    custom_rows = copy.deepcopy(cached[0])
                    custom_timelines = copy.deepcopy(cached[1])
                    optimized_cdr_rows = copy.deepcopy(cached[2])
                    rebuild_cdr_schedule_table(optimized_cdr_rows)
                    cdr_schedule_status.setText(
                        f"已复用相同队伍与机制设置的精确结果；{len(optimized_cdr_rows)} 次团队Buff施放。"
                    )
                else:
                    def optimize_team_buff_schedule(
                        initial_results: list[dict[str, Any]],
                        _seed_schedule: dict[tuple[str, str], list[tuple[float, float]]],
                    ) -> dict[tuple[str, str], list[tuple[float, float]]]:
                        nonlocal optimized_cdr_rows
                        buff_started = time.perf_counter()
                        specs = intermittent_team_buff_specs(effects, jobs, initial_results)
                        optimized_cdr_rows = lightweight_team_buff_schedule(
                            specs,
                            intervals,
                            custom_window_seconds(),
                            initial_results,
                            seed_schedule=None,
                            preferred_starts=preferred_starts,
                            locked_starts=manual_cdr_locks,
                        )
                        valid_lock_keys = {
                            (
                                norm(cast.get("provider")),
                                norm(cast.get("skill")),
                                int(as_number(cast.get("occurrence"))),
                            )
                            for cast in optimized_cdr_rows
                        }
                        for lock_key in list(manual_cdr_locks):
                            if lock_key not in valid_lock_keys:
                                manual_cdr_locks.pop(lock_key, None)
                        rebuild_cdr_schedule_table(optimized_cdr_rows)
                        elapsed = time.perf_counter() - buff_started
                        cdr_schedule_status.setText(
                            f"稳定初始轴 + 受限联合优化 {elapsed:.3f}s；"
                            f"{len(optimized_cdr_rows)} 次施放，已锁定 {len(manual_cdr_locks)} 次。"
                        )
                        QApplication.processEvents()
                        return schedule_rows_to_casts(optimized_cdr_rows)

                    def select_validated_team_buff_schedule(use_optimized: bool) -> None:
                        nonlocal optimized_cdr_rows
                        if use_optimized:
                            return
                        optimized_cdr_rows = copy.deepcopy(baseline_cdr_rows)
                        rebuild_cdr_schedule_table(optimized_cdr_rows)
                        cdr_schedule_status.setText(
                            "智能Buff日程未提高八人轴输出，已自动回退到稳定减CD日程。"
                        )

                    custom_rows, custom_timelines, progress = timeline_rows_for(
                        jobs,
                        effects,
                        hand_multiplier_by_job(),
                        selected_build_overrides(),
                        show_progress=True,
                        window_seconds=custom_window_seconds(),
                        blocked_intervals=intervals,
                        defer_progress_close=True,
                        pinned_cdr_schedule=pinned_cdr_schedule,
                        schedule_builder=optimize_team_buff_schedule,
                        fallback_schedule=pinned_cdr_schedule,
                        schedule_selected_handler=select_validated_team_buff_schedule,
                        fast_custom=True,
                    )
                    custom_axis_cache[cache_key] = (
                        copy.deepcopy(custom_rows),
                        copy.deepcopy(custom_timelines),
                        copy.deepcopy(optimized_cdr_rows),
                    )
                    while len(custom_axis_cache) > 2:
                        custom_axis_cache.pop(next(iter(custom_axis_cache)))
                render_custom_results(intervals, custom_rows, custom_timelines, progress)
                succeeded = True
            except Exception as exc:
                message = f"重新排轴失败：{exc}"
                cdr_schedule_status.setText(message)
                custom_summary_label.setText(message)
                custom_detail_summary_label.setText(message)
                QMessageBox.warning(dialog, "重新排轴失败", str(exc))
            finally:
                if progress is not None:
                    self._extreme_dispose_widget(progress)
                mechanism_recalc_button.setEnabled(True)
                mechanism_recalc_button.setText("重新排轴")
                QApplication.processEvents()
            return succeeded

        def mark_custom_timeline_dirty(intervals: list[dict[str, Any]]) -> None:
            rebuild_mechanism_table(intervals)
            custom_summary_label.setText(
                f"机制区间已修改；{number_text(custom_window_seconds(), integer=True)}秒自选轴尚未更新，"
                "点击“重新排轴”后应用。"
            )
            custom_detail_summary_label.setText("机制区间已修改；当前详情是上一次结果，请点击“重新排轴”更新。")

        def custom_window_changed(value: int) -> None:
            nonlocal optimized_cdr_rows
            mechanism_editor.set_window_seconds(float(value))
            optimized_cdr_rows = []
            manual_cdr_locks.clear()
            rebuild_cdr_schedule_table([])
            rebuild_mechanism_table(mechanism_editor.intervals)
            custom_compare_table.setHorizontalHeaderItem(
                2,
                self._item(f"自选{value}s DPS"),
            )
            custom_axis_box.setTitle(f"自选机制八人时间轴（{value}s）")
            custom_summary_label.setText(
                f"最大标尺已改为 {value}s，每行固定180s；点击“重新排轴”应用。"
            )
            custom_detail_summary_label.setText("最大标尺已修改；当前详情仍是上一次结果。")

        mechanism_editor.intervalsChanged.connect(mark_custom_timeline_dirty)
        mechanism_window_spin.valueChanged.connect(custom_window_changed)
        mechanism_editor.historyChanged.connect(
            lambda can_undo, can_redo: (
                mechanism_undo_button.setEnabled(can_undo),
                mechanism_redo_button.setEnabled(can_redo),
            )
        )
        mechanism_add_button.clicked.connect(lambda: mechanism_editor.add_interval())
        mechanism_undo_button.clicked.connect(mechanism_editor.undo)
        mechanism_redo_button.clicked.connect(mechanism_editor.redo)
        mechanism_clear_button.clicked.connect(mechanism_editor.clear_intervals)
        mechanism_recalc_button.clicked.connect(recalculate_custom_timeline)
        mechanism_undo_button.setEnabled(False)
        mechanism_redo_button.setEnabled(False)

        mechanism_templates = load_mechanism_templates()

        def refresh_template_combo(current: str = "选择机制模板") -> None:
            values = ["选择机制模板", *sorted(mechanism_templates)]
            self._replace_combo_values(
                mechanism_template_combo,
                values,
                current if current in values else "选择机制模板",
            )

        def load_selected_template() -> None:
            name = norm(mechanism_template_combo.currentText())
            if name in mechanism_templates:
                intervals = mechanism_templates[name]
                max_end = max(
                    (as_number(interval.get("end")) for interval in intervals),
                    default=0.0,
                )
                if max_end > mechanism_window_spin.value() + 1e-6:
                    required_window = int(min(720, max(180, ceil(max_end / 180.0) * 180)))
                    mechanism_window_spin.setValue(required_window)
                mechanism_editor.set_intervals(intervals, record=True)

        def save_current_template() -> None:
            default_name = (
                norm(mechanism_template_combo.currentText())
                if norm(mechanism_template_combo.currentText()) in mechanism_templates
                else "自选机制方案"
            )
            name, accepted = QInputDialog.getText(
                dialog,
                "保存机制模板",
                "模板名称",
                text=default_name,
            )
            name = norm(name)
            if not accepted or not name:
                return
            mechanism_templates[name] = copy.deepcopy(mechanism_editor.intervals)
            save_mechanism_templates(mechanism_templates)
            refresh_template_combo(name)

        def delete_selected_template() -> None:
            name = norm(mechanism_template_combo.currentText())
            if name not in mechanism_templates:
                return
            if QMessageBox.question(
                dialog,
                "删除机制模板",
                f"确定删除“{name}”吗？",
            ) != QMessageBox.Yes:
                return
            del mechanism_templates[name]
            save_mechanism_templates(mechanism_templates)
            refresh_template_combo()

        mechanism_load_button.clicked.connect(load_selected_template)
        mechanism_save_button.clicked.connect(save_current_template)
        mechanism_delete_template_button.clicked.connect(delete_selected_template)

        rebuild_mechanism_table(mechanism_editor.intervals)
        rebuild_cdr_schedule_table(optimized_cdr_rows)
        if custom_rows and custom_timelines:
            restored_intervals = normalize_mechanism_intervals(
                mechanism_editor.intervals,
                custom_window_seconds(),
            )
            restored_schedule: dict[tuple[str, str], list[tuple[float, float]]] = {}
            for cast in optimized_cdr_rows:
                restored_schedule.setdefault(
                    (
                        norm(cast.get("provider")),
                        self._skill_burst_skill_key(cast.get("skill")),
                    ),
                    [],
                ).append((
                    as_number(cast.get("cast_start")),
                    as_number(cast.get("cast_end")),
                ))
            custom_axis_cache[custom_result_cache_key(
                selected_jobs(),
                adjusted_effects(selected_jobs()),
                restored_intervals,
                restored_schedule,
            )] = (
                copy.deepcopy(custom_rows),
                copy.deepcopy(custom_timelines),
                copy.deepcopy(optimized_cdr_rows),
            )
            render_custom_results(
                restored_intervals,
                custom_rows,
                custom_timelines,
            )
        restored_page_name = norm(restored.get("page_name"))
        restored_page = int(as_number(restored.get("page_index"), 0))
        if restored_page_name:
            matching_page = next(
                (
                    index
                    for index in range(result_pages.count())
                    if norm(result_pages.tabText(index)) == restored_page_name
                ),
                None,
            )
            if matching_page is not None:
                restored_page = matching_page
        elif restored and restored_page >= 1:
            # Saved v3.50 results predate the inserted breakdown page.
            restored_page += 1
        result_pages.setCurrentIndex(max(0, min(result_pages.count() - 1, restored_page)))

        def open_member_build_editor(row: int) -> None:
            job = norm(job_combos[row].currentText())
            if not job or job == "无":
                QMessageBox.information(self, "修改队员装备", "请先为该位置选择职业。")
                return
            try:
                saved = member_build_by_job.get(job)
                initial_build = (
                    copy.deepcopy(saved)
                    if isinstance(saved, dict)
                    else self._extreme_manual_optimization_build(job)
                )
                editor_dialog = QDialog(self.window())
                editor_dialog.setObjectName(f"extremeMemberBuildDialog{row + 1}")
                editor_dialog.setWindowTitle(f"队员{row + 1} · {job} · 独立配装")
                editor_dialog.setWindowModality(Qt.NonModal)
                editor_dialog.setAttribute(Qt.WA_DeleteOnClose)
                editor_layout = QVBoxLayout(editor_dialog)
                editor_layout.setContentsMargins(8, 8, 8, 8)
                hint = QLabel(
                    f"正在修改队员{row + 1}“{job}”的独立配装、技能倍率与技能纹章。应用后只替换该队员的数据，"
                    "不会覆盖配装1，也不会改动其他队员的装备。怪物属性沿用当前极限配队设置。"
                )
                hint.setObjectName("inlineHint")
                hint.setWordWrap(True)
                editor_layout.addWidget(hint)
                member_editor = BuildEditor(
                    f"队员{row + 1} {job}", self.store, self.calculator, initial_build
                )
                member_editor.setObjectName(f"extremeMemberBuildEditor{row + 1}")
                member_editor._skill_sim_ui_active = False
                member_editor._team_sim_ui_active = False
                member_editor.first_job.setEnabled(False)
                member_editor.second_job.setEnabled(False)
                for attr_name in (
                    "boss_combo",
                    "crit_resist",
                    "element_resist_mode",
                    "element_resist",
                    "monster_defense",
                    "monster_defense_rate",
                ):
                    widget = getattr(member_editor, attr_name, None)
                    if isinstance(widget, QWidget):
                        widget.setEnabled(False)
                member_tabs = QTabWidget()
                member_tabs.setObjectName(f"extremeMemberBuildTabs{row + 1}")
                member_tabs.addTab(member_editor, "装备与属性")
                member_tabs.addTab(member_editor._skill_multiplier_override_page(), "技能倍率")
                member_tabs.addTab(member_editor._skill_glyph_page(), "技能纹章")
                editor_layout.addWidget(member_tabs, 1)
                action_row = QHBoxLayout()
                restore_button = app_button("恢复自动转换装备")
                cancel_button = app_button("取消")
                save_button = app_button("应用并重算", primary=True)
                action_row.addWidget(restore_button)
                action_row.addStretch(1)
                action_row.addWidget(cancel_button)
                action_row.addWidget(save_button)
                editor_layout.addLayout(action_row)

                def apply_member_build() -> None:
                    member_editor.apply_skill_multiplier_overrides()
                    if isinstance(getattr(member_editor, "skill_glyph_table", None), QTableWidget):
                        member_editor._skill_glyph_applied_count = int(member_editor.skill_glyph_count_spin.value())
                        member_editor._skill_glyph_applied_rows = member_editor._skill_glyph_rows_from_table()
                        member_editor.build["skill_glyphs"] = {
                            "second_job": member_editor.second_job.currentText(),
                            "count": member_editor._skill_glyph_applied_count,
                            "rows": copy.deepcopy(member_editor._skill_glyph_applied_rows),
                        }
                    custom_build = member_editor.collect_build()
                    target_profile = self._extreme_profile_for_job(self._team_real_job(job))
                    custom_build["first_job"] = target_profile.get("first_job")
                    custom_build["second_job"] = target_profile.get("second_job")
                    custom_build["effective_second_job"] = target_profile.get("second_job")
                    custom_build["team_sim"] = {"members": [], "effects": []}
                    member_build_overrides[row] = copy.deepcopy(custom_build)
                    member_build_jobs[row] = job
                    member_build_by_job[job] = copy.deepcopy(custom_build)
                    build_buttons[row].setText("已自定义")
                    editor_dialog.accept()
                    recalculate()

                def restore_member_build() -> None:
                    member_build_overrides.pop(row, None)
                    member_build_jobs.pop(row, None)
                    member_build_by_job.pop(job, None)
                    build_buttons[row].setText("修改装备")
                    editor_dialog.accept()
                    recalculate()

                save_button.clicked.connect(apply_member_build)
                restore_button.clicked.connect(restore_member_build)
                cancel_button.clicked.connect(editor_dialog.reject)
                editor_dialog.resize(1500, 900)
                editor_dialog.show()
                dialogs = getattr(self, "_extreme_member_build_dialogs", None)
                if not isinstance(dialogs, list):
                    dialogs = []
                    self._extreme_member_build_dialogs = dialogs
                dialogs.append(editor_dialog)
                editor_dialog.destroyed.connect(
                    lambda _obj=None, target=editor_dialog: (
                        dialogs.remove(target) if target in dialogs else None
                    )
                )
            except Exception as exc:
                QMessageBox.warning(self, "打开队员配装失败", str(exc))

        for row, build_button in enumerate(build_buttons):
            build_button.clicked.connect(lambda _checked=False, r=row: open_member_build_editor(r))

        apply_button = app_button("应用到配队模拟", primary=True)

        def apply_to_main() -> None:
            jobs = selected_jobs()
            self._set_team_aura_choices(selected_auras())
            self._apply_team_members(jobs)
            self._auto_populate_team_effects_from_members(schedule=False)
            selected_cdr = self._selected_team_cdr_skills()
            for mapping in (getattr(self, "team_cdr_glyph_checks", {}), getattr(self, "team_cdr_apply_checks", {})):
                for skill, check in mapping.items():
                    if isinstance(check, QCheckBox):
                        check.blockSignals(True)
                        check.setChecked(skill in selected_cdr)
                        check.blockSignals(False)
            self._refresh_team_cdr_usage_visibility()
            base_effects = self._selected_team_effects(use_base_cooldowns=True)
            self._sync_team_effect_cooldowns(base_effects, self._team_applied_cdr_skills())
            self._request_team_effect_auto_sort()
            self.schedule_refresh()

        apply_button.clicked.connect(apply_to_main)
        convert_job_combo = Combo(unique(selected_jobs()), selected_jobs()[0] if selected_jobs() else "", min_width=116, contents_length=0)
        convert_button = app_button("转换为该职业配装1", primary=True)

        def convert_to_build1() -> None:
            job = norm(convert_job_combo.currentText())
            if not job:
                return
            answer = QMessageBox.question(
                self,
                "转换职业配装",
                f"将使用极限配队计算时的装备转换规则，把配装1、模拟加点和技能模拟切换为“{job}”。\n"
                "当前配装1会被替换，是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            try:
                converted = self._extreme_manual_optimization_build(job)
                preserved_jobs = selected_jobs()
                preserved_hands = hand_multiplier_by_job()
                preserved_auras = selected_auras()
                preserved_rows = copy.deepcopy(current_rows)
                preserved_timelines = copy.deepcopy(current_timelines)
                preserved_member_builds = selected_build_overrides()
                preserved_boss = getattr(self, "team_extreme_boss", "无")
                preserved_result_state = result_state_snapshot()
                preserved_replacement_context = copy.deepcopy(replacement_context)
                preserved_grades = copy.deepcopy(current_grades)
                main_window = self.window()
                if not hasattr(main_window, "replace_editor"):
                    raise RuntimeError("未找到配装1编辑器")
                main_window.replace_editor(0, converted)
                new_editor = main_window.editor1
                new_editor.team_extreme_mode = team_mode
                new_editor.team_extreme_boss = preserved_boss
                new_editor.team_extreme_aura_choices = dict(preserved_auras)
                new_editor._show_extreme_team_result_dialog(
                    preserved_jobs,
                    sum(as_number(row.get("final_output")) for row in preserved_rows),
                    preserved_rows,
                    preserved_grades,
                    team_mode,
                    fixed_jobs,
                    candidate_jobs,
                    hand_multipliers=preserved_hands,
                    aura_choices=preserved_auras,
                    timelines=preserved_timelines,
                    member_builds=preserved_member_builds,
                    replacement_context=preserved_replacement_context,
                    restored_state=preserved_result_state,
                )
                main_window.tabs.setCurrentIndex(0)
                QMessageBox.information(
                    main_window,
                    "转换完成",
                    f"配装1已转换为“{job}”计算时使用的装备与默认加点。极限结果已保留；"
                    "修改配装后可回到结果页点击“重新计算时间轴”。",
                )
            except Exception as exc:
                QMessageBox.warning(self, "转换失败", str(exc))

        convert_button.clicked.connect(convert_to_build1)
        back_button = app_button("返回配队设置")
        back_button.clicked.connect(lambda: self.team_sim_tabs.setCurrentWidget(self.team_setup_page))
        save_scheme_button = app_button("保存完整方案", primary=True)
        save_scheme_button.setObjectName("extremeSaveCompleteSchemeButton")
        save_scheme_button.setToolTip("保存当前八名职业、独立装备、时间轴、自选机制和结果数据")

        def save_complete_scheme() -> None:
            jobs = selected_jobs()
            if not jobs:
                return
            default_name = f"{'、'.join(jobs[:3])}{'等' if len(jobs) > 3 else ''}极限配队"
            name, accepted = QInputDialog.getText(
                dialog,
                "保存完整极限配队方案",
                "方案名称",
                text=default_name,
            )
            name = norm(name)
            if not accepted or not name:
                return
            schemes = load_extreme_team_schemes()
            if name in schemes and QMessageBox.question(
                dialog,
                "覆盖极限配队方案",
                f"“{name}”已经存在，是否覆盖？",
            ) != QMessageBox.Yes:
                return

            selected_overrides = selected_build_overrides()
            complete_member_builds: list[dict[str, Any]] = []
            for index, job in enumerate(jobs):
                override = selected_overrides[index] if index < len(selected_overrides) else None
                complete_member_builds.append(
                    copy.deepcopy(override)
                    if isinstance(override, dict)
                    else copy.deepcopy(self._extreme_timeline_build_for_job(job))
                )
            payload = {
                "app_version": APP_VERSION,
                "timeline_engine_revision": EXTREME_TEAM_TIMELINE_REVISION,
                "team": list(jobs),
                "total_score": sum(as_number(row.get("final_output")) for row in current_rows),
                "details": copy.deepcopy(current_rows),
                "grades": copy.deepcopy(current_grades),
                "team_mode": team_mode,
                "boss": norm(getattr(self, "team_extreme_boss", "无")) or "无",
                "require_tank": bool(getattr(self, "team_extreme_require_tank", True)),
                "require_healer": bool(getattr(self, "team_extreme_require_healer", True)),
                "fixed_jobs": list(fixed_jobs),
                "fixed_explicit": bool(getattr(self, "team_extreme_fixed_explicit", True)),
                "candidate_jobs": list(candidate_jobs),
                "replacement_mode": norm(getattr(self, "team_extreme_replacement_mode", "快速")) or "快速",
                "replacement_candidates": max(
                    0,
                    int(as_number(getattr(self, "team_extreme_replacement_candidates", 2), 2)),
                ),
                "hand_multipliers": hand_multiplier_by_job(),
                "aura_choices": selected_auras(),
                "member_builds": complete_member_builds,
                "timelines": copy.deepcopy(current_timelines),
                "team_effects": copy.deepcopy(adjusted_effects(jobs)),
                "replacement_context": copy.deepcopy(replacement_context),
                "result_state": result_state_snapshot(),
                "source_build": copy.deepcopy(self.collect_build()),
            }
            try:
                save_extreme_team_scheme(schemes, name, payload)
            except OSError as exc:
                QMessageBox.warning(dialog, "保存完整极限配队方案失败", str(exc))
                return
            QMessageBox.information(
                dialog,
                "保存完成",
                f"完整极限配队方案“{name}”已保存。下次打开一键极限优化配队时可直接载入。",
            )

        save_scheme_button.clicked.connect(save_complete_scheme)
        result_toolbar.addWidget(save_scheme_button)
        buttons.addStretch(1)
        buttons.addWidget(recalc_button)
        buttons.addWidget(apply_button)
        buttons.addWidget(QLabel("转换职业"))
        buttons.addWidget(convert_job_combo)
        buttons.addWidget(convert_button)
        buttons.addWidget(back_button)
        layout.addLayout(buttons)
        self.team_sim_tabs.setCurrentWidget(result_page)
        if restored.get("_recalculate_standard_timeline_on_open"):
            warning_label.setText("该方案由旧版时间轴引擎保存，正在按当前算法依次更新标准125秒轴和自选机制轴...")

            def upgrade_loaded_scheme_timelines() -> None:
                try:
                    recalculate(False, announce_loaded_scheme=False)
                    self._set_extreme_history_status(
                        f"既往极限配队方案“{loaded_scheme_name or '未命名方案'}”的标准125秒轴已更新；"
                        f"正在按原有机制区间重排{number_text(custom_window_seconds(), integer=True)}秒自选轴……",
                        "info",
                    )
                    if not recalculate_custom_timeline(False):
                        self._set_extreme_history_status(
                            f"既往极限配队方案“{loaded_scheme_name or '未命名方案'}”的标准125秒轴已更新，"
                            "但自选机制轴重排失败，请在结果页点击“重新排轴”重试。",
                            "warning",
                        )
                        return
                    standard_score = sum(
                        max(0.0, as_number(row.get("final_output")))
                        for row in current_rows
                    )
                    custom_score = sum(
                        max(0.0, as_number(result.get("dps")))
                        for result in custom_timelines
                    )
                    apply_to_main()
                    self._set_extreme_history_status(
                        f"既往极限配队方案“{loaded_scheme_name or '未命名方案'}”已按当前算法更新完成："
                        f"标准125秒轴 {number_text(standard_score, integer=True)}/s；"
                        f"自选{number_text(custom_window_seconds(), integer=True)}秒轴 {number_text(custom_score, integer=True)}/s。"
                        "原有机制区间与团队Buff手动锁定均已保留；八个职业及团队效果已同步到“配队设置与收益”。",
                        "success",
                    )
                    warning_label.setText("")
                except Exception as exc:
                    self._set_extreme_history_status(
                        f"既往极限配队方案“{loaded_scheme_name or '未命名方案'}”自动更新失败：{exc}",
                        "warning",
                    )
                    raise

            QTimer.singleShot(0, upgrade_loaded_scheme_timelines)
        elif loaded_scheme_name:
            apply_to_main()
            self._set_extreme_history_status(
                f"既往极限配队方案“{loaded_scheme_name}”已载入；"
                f"{'、'.join(selected_jobs()) if selected_jobs() else '无有效队员'}及其团队效果"
                "已同步到“配队设置与收益”。当前结果使用保存时的时间轴数据。",
                "success",
            )
