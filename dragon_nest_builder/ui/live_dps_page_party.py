from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *

class LiveDpsPartyMixin:
    @staticmethod
    def _item(text: str, align: Qt.AlignmentFlag = Qt.AlignCenter) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(align)
        return item


    def _refresh(self) -> None:
        if self._refresh_running:
            self._ui_refresh_pending = True
            return
        self._refresh_running = True
        try:
            self._refresh_current_view()
        finally:
            self._refresh_running = False


    def _refresh_current_view(self) -> None:
        self._last_ui_refresh_at = time.monotonic()
        capture_elapsed = None
        if self._capture_active and self._capture_started_at:
            capture_elapsed = max(0.0, time.time() - self._capture_started_at)
        active_tab = self.live_dps_tabs.currentIndex()
        boss_stats_active = active_tab == 1
        timeline_active = active_tab == 2
        team_score_active = active_tab == getattr(self, "team_score_tab_index", 3)
        party_info_active = active_tab == getattr(self, "party_info_tab_index", 4)
        progress_key = self._view_progress_key(active_tab)
        progress_widgets = self._view_progress_widgets.get(progress_key)
        progress_visible = bool(progress_widgets and progress_widgets[0].isVisible())
        if progress_visible:
            self._set_view_progress(
                progress_key, 12, "正在准备当前页面所需的事件快照..."
            )
        full_boss_effects_active = (
            not boss_stats_active
            and not timeline_active
            and not team_score_active
            and not party_info_active
            and self.curve_tabs.currentIndex() == 1
        )
        full_damage_curve_active = (
            not boss_stats_active
            and not timeline_active
            and not team_score_active
            and not party_info_active
            and self.curve_tabs.currentIndex() == 0
            and not self._capture_active
        )
        view_key = (
            active_tab,
            (
                self.boss_stats_tabs.currentIndex()
                if boss_stats_active
                else (
                    -1
                    if timeline_active
                    else -2
                    if team_score_active
                    else -3
                    if party_info_active
                    else self.curve_tabs.currentIndex()
                )
            ),
        )
        revision = self.collector.aggregator.revision
        self.status_label.setText(self.collector.status)
        if self._rendered_view_revisions.get(view_key) == revision and not self._capture_active:
            if progress_visible:
                self._finish_view_progress(progress_key)
            return
        include_events = (
            timeline_active
            or boss_stats_active
            or (team_score_active and not self._capture_active)
            or full_boss_effects_active
            or full_damage_curve_active
        )
        if (
            include_events
            and self._event_snapshot is not None
            and self._event_snapshot_revision == revision
            and not self._capture_active
        ):
            snapshot = self._event_snapshot
        elif (
            not include_events
            and not self._capture_active
            and self._summary_snapshot is not None
            and self._summary_snapshot_revision == revision
        ):
            snapshot = self._summary_snapshot
        else:
            snapshot = self.collector.snapshot(
                capture_elapsed,
                include_events=include_events,
                live_clock=self._capture_active,
            )
            if include_events:
                self._event_snapshot_revision = revision
                self._event_snapshot = snapshot
            elif (
                not include_events
                and not self._capture_active
            ):
                self._summary_snapshot_revision = revision
                self._summary_snapshot = snapshot
        display_snapshot = snapshot
        self._latest_snapshot = snapshot
        if progress_visible:
            self._set_view_progress(
                progress_key, 30, "事件快照准备完成，正在整理统计数据..."
            )
        dps_elapsed = (
            display_snapshot.dps_elapsed_seconds or display_snapshot.elapsed_seconds
        )
        total_incoming_damage = sum(
            player.incoming_damage
            for player in event_platform_for(snapshot).players
        )
        self.total_label.setText(
            f"总伤害 {_compact_number(snapshot.total_damage)}  |  "
            f"{_compact_number(snapshot.total_damage / dps_elapsed) if dps_elapsed else '0'} DPS  |  "
            f"总治疗 {_compact_number(snapshot.total_healing)}  |  "
            f"总承伤 {_compact_number(total_incoming_damage)}  |  "
            f"有效战斗 {dps_elapsed:.1f}s / 记录 {snapshot.elapsed_seconds:.1f}s"
        )
        ledger_balanced = (
            snapshot.raw_damage_total == snapshot.attributed_damage_total
        )
        unresolved_share = (
            snapshot.unresolved_damage_total / snapshot.attributed_damage_total
            if snapshot.attributed_damage_total > 0
            else 0.0
        )
        self.total_label.setToolTip(
            "双轨账本对账\n"
            f"原始伤害：{_compact_number(snapshot.raw_damage_total)}\n"
            f"技能归因：{_compact_number(snapshot.attributed_damage_total)}\n"
            f"待识别：{_compact_number(snapshot.unresolved_damage_total)} "
            f"({unresolved_share * 100:.2f}%)\n"
            f"伤害反推施放：{snapshot.inferred_cast_count} 次\n"
            f"施放实例纠正：{snapshot.reassigned_hit_count} Hit\n"
            f"对账状态：{'一致' if ledger_balanced else '异常'}"
        )
        curve_elapsed = snapshot.elapsed_seconds
        if capture_elapsed is not None:
            curve_elapsed = max(
                curve_elapsed,
                capture_elapsed,
            )
        if timeline_active:
            if progress_visible:
                self._set_view_progress(
                    progress_key, 42, "正在匹配技能施放、命中与补全记录..."
                )
            self._fill_timeline(
                snapshot,
                curve_elapsed,
            )
        elif boss_stats_active:
            if progress_visible:
                self._set_view_progress(
                    progress_key, 40, self._view_progress_label(active_tab)
                )
            self._fill_boss_statistics(
                snapshot,
                curve_elapsed,
            )
        elif team_score_active:
            if self._capture_active:
                self.team_score_title.setText(
                    "实时抓包进行中：完整评分将在停止抓包后自动生成。"
                )
                if progress_visible:
                    self._finish_view_progress(progress_key)
            else:
                if progress_visible:
                    self._set_view_progress(
                        progress_key, 38, "正在生成各职业标准化理论轴..."
                    )
                self._fill_team_scores(snapshot, revision)
        elif party_info_active:
            if progress_visible:
                self._set_view_progress(
                    progress_key, 55, "正在整理队伍身份与查看信息候选..."
                )
            self._fill_party_info(snapshot)
        else:
            if progress_visible:
                self._set_view_progress(
                    progress_key, 45, self._view_progress_label(active_tab)
                )
            self._fill_players(display_snapshot)
            if progress_visible:
                self._set_view_progress(
                    progress_key, 62, "正在生成伤害曲线与技能构成..."
                )
            self._fill_damage_curve(display_snapshot, curve_elapsed)
            self._fill_skills(display_snapshot)
            boss_current_seconds = max(
                snapshot.boss_effect_current_seconds,
                capture_elapsed or 0.0,
            )
            self._latest_effect_seconds = boss_current_seconds
            if self.curve_tabs.currentIndex() == 1:
                self._fill_boss_effects(snapshot, boss_current_seconds)
            elif self.curve_tabs.currentIndex() == 2:
                self._fill_player_buffs(
                    snapshot,
                    boss_current_seconds,
                    self._player_display_details(snapshot),
                )
        self._rendered_view_revisions[view_key] = revision
        if progress_visible:
            self._finish_view_progress(progress_key)


    def _fill_party_info(self, snapshot: CombatSnapshot) -> None:
        selected_actor_id = self._party_info_selected_actor_id
        if not selected_actor_id and self.party_roster_table.currentRow() >= 0:
            selected_item = self.party_roster_table.item(
                self.party_roster_table.currentRow(), 0
            )
            selected_actor_id = selected_item.text() if selected_item else ""

        members = list(self._party_members_for_snapshot(snapshot))

        self._party_info_members = tuple(members)
        self._party_info_profiles = tuple(
            event_platform_for(snapshot).player_profiles
        )
        self.party_roster_table.blockSignals(True)
        try:
            self.party_roster_table.setRowCount(len(members))
            selected_row = -1
            for row_index, member in enumerate(members):
                values = (member.actor_id, member.job or "未知职业")
                for column, value in enumerate(values):
                    align = Qt.AlignCenter if column == 0 else Qt.AlignLeft | Qt.AlignVCenter
                    self.party_roster_table.setItem(
                        row_index, column, self._item(value, align)
                    )
                if self._party_actor_key(member.actor_id) == self._party_actor_key(
                    selected_actor_id
                ):
                    selected_row = row_index
            if selected_row < 0 and members:
                selected_row = 0
            if selected_row >= 0:
                self.party_roster_table.selectRow(selected_row)
                self._party_info_selected_actor_id = members[selected_row].actor_id
            else:
                self._party_info_selected_actor_id = ""
        finally:
            self.party_roster_table.blockSignals(False)
        self._render_party_member_profile()


    @classmethod
    def _party_members_for_snapshot(
        cls, snapshot: CombatSnapshot
    ) -> tuple[PartyMemberEvent, ...]:
        platform = event_platform_for(snapshot)
        members = list(platform.party_members)
        known_actor_keys = {
            cls._party_actor_key(member.actor_id) for member in members
        }
        for profile in platform.player_profiles:
            profile_actor_id = cls._party_profile_roster_id(profile)
            actor_key = cls._party_actor_key(profile_actor_id)
            # A self-status marker contains no profile body by itself. Keep it
            # available for later merging, but do not present it as an
            # undecoded teammate or an unknown profession in the roster.
            is_empty_self_status = (
                profile.response_layout == "TCP:self_status_request:no_profile_payload"
                and not profile.equipment
                and not profile.cards
                and not profile.job
            )
            if is_empty_self_status:
                continue
            if profile_actor_id and actor_key not in known_actor_keys:
                is_profile_fallback = norm(profile.target_id).casefold().startswith(
                    "profile:"
                )
                is_pending_profile = norm(profile.target_id).casefold().startswith(
                    "inspect:"
                )
                members.append(
                    PartyMemberEvent(
                        timestamp_ms=profile.timestamp_ms,
                        actor_id=profile_actor_id,
                        name="",
                        job=profile.job,
                        level=profile.level,
                        party_position=profile.party_position,
                        source_layout=(
                            "TCP档案标识（尚未关联UDP实时实体）"
                            if is_profile_fallback
                            else "已捕获查看信息响应（档案长度待校准）"
                            if is_pending_profile
                            else "查看信息响应"
                        ),
                        confidence=(
                            0.8
                            if is_profile_fallback
                            else 0.0
                            if is_pending_profile
                            else 1.0
                        ),
                    )
                )
                known_actor_keys.add(actor_key)
        return tuple(members)


    @staticmethod
    def _party_actor_key(value: Any) -> str:
        return norm(value).upper()


    @staticmethod
    def _party_profile_actor_id(profile: PlayerInspectEvent) -> str:
        """Return an identity proven by a decrypted inspect response.

        Town-only captures can contain no UDP combat actions and therefore no
        ``PartyMemberEvent``. The verified TCP profile identifier remains
        useful as a transparent fallback, while unverified request/session
        tokens must never be presented as actor IDs.
        """
        target_id = norm(profile.target_id)
        match = re.fullmatch(
            r"profile:(0x[0-9A-Fa-f]{8})", target_id, flags=re.IGNORECASE
        )
        if match:
            return f"0x{match.group(1)[2:].upper()}"
        if re.fullmatch(r"0x[0-9A-Fa-f]{8}", target_id):
            return f"0x{target_id[2:].upper()}"
        return ""


    @classmethod
    def _party_profile_roster_id(cls, profile: PlayerInspectEvent) -> str:
        verified_id = cls._party_profile_actor_id(profile)
        if verified_id:
            return verified_id
        match = re.fullmatch(
            r"inspect:([0-9A-Fa-f]{4})",
            norm(profile.target_id),
            flags=re.IGNORECASE,
        )
        return f"待解密:{match.group(1).upper()}" if match else ""


    def _party_member_selection_changed(self) -> None:
        row = self.party_roster_table.currentRow()
        if 0 <= row < len(self._party_info_members):
            self._party_info_selected_actor_id = self._party_info_members[row].actor_id
        else:
            self._party_info_selected_actor_id = ""
        self._render_party_member_profile()


    def _party_profile_for_member(
        self, member: PartyMemberEvent
    ) -> PlayerInspectEvent | None:
        def profile_quality(profile: PlayerInspectEvent) -> tuple[int, int, int, int, int, int]:
            return (
                int(bool(profile.equipment)),
                len(profile.equipment),
                int(bool(norm(profile.job))),
                len(profile.stats),
                len(profile.fields),
                profile.timestamp_ms,
            )

        def complete_profile(profile: PlayerInspectEvent) -> PlayerInspectEvent:
            if profile.job or not member.job:
                return profile
            return replace(profile, job=member.job)

        actor_key = self._party_actor_key(member.actor_id)
        exact = [
            profile
            for profile in self._party_info_profiles
            if self._party_actor_key(self._party_profile_roster_id(profile))
            == actor_key
        ]
        if exact:
            return complete_profile(max(exact, key=profile_quality))

        if member.name:
            by_internal_name = [
                profile
                for profile in self._party_info_profiles
                if profile.target_name and profile.target_name == member.name
            ]
            if by_internal_name:
                return complete_profile(max(by_internal_name, key=profile_quality))

        same_job = [
            profile
            for profile in self._party_info_profiles
            if member.job and profile.job == member.job
        ]
        return complete_profile(same_job[0]) if len(same_job) == 1 else None


    def _render_party_member_profile(self) -> None:
        member = next(
            (
                row
                for row in self._party_info_members
                if self._party_actor_key(row.actor_id)
                == self._party_actor_key(self._party_info_selected_actor_id)
            ),
            None,
        )
        if member is None:
            self.party_detail_title.setText("请选择队员")
            self._clear_party_detail_tables()
            return

        job = member.job or "未知职业"
        self.party_detail_title.setText(f"{member.actor_id} · {job}")
        profile = self._party_profile_for_member(member)
        categorized: dict[str, list[tuple[str, str]]] = defaultdict(list)
        if profile is not None:
            for raw_key, raw_value in profile.equipment:
                category, separator, label = norm(raw_key).partition("|")
                if not separator:
                    category, label = "装备", norm(raw_key)
                if category in {"装备", "刻印", "纹章", "石板", "护符"}:
                    categorized[category].append(
                        (label or "记录", norm(raw_value) or "待映射")
                    )

        self._set_party_detail_rows(
            self.party_equipment_table,
            categorized["装备"],
            "当前记录尚未映射出具体装备",
        )
        self._set_party_detail_rows(
            self.party_engraving_table,
            categorized["刻印"],
            "当前记录尚未识别出刻印",
        )
        self._set_party_detail_rows(
            self.party_glyph_table,
            categorized["纹章"],
            "当前记录尚未映射出具体纹章",
        )
        self._set_party_detail_rows(
            self.party_plate_table,
            categorized["石板"],
            "当前记录尚未映射出具体石板",
        )
        card_rows = list(profile.cards) if profile is not None else []
        card_mastery_rows: list[tuple[str, str, str, str, str]] = []
        card_estimate: dict[str, Any] = {}
        if profile is not None:
            card_estimate = self._party_card_estimator.card_estimate_summary(profile)
            for row in card_estimate.get("rows", []):
                if not isinstance(row, dict):
                    continue
                attr = norm(row.get("attr"))
                level_source = norm(row.get("levelSource"))
                source_text = (
                    "实测" if level_source == "observed"
                    else "固定" if level_source == "fixed"
                    else "估算"
                )
                card_mastery_rows.append((
                    norm(row.get("name")),
                    attr,
                    attr_number_text(attr, as_number(row.get("initial"))) or "0",
                    f"{int(as_number(row.get('level')))}级（{source_text}）",
                    attr_number_text(attr, as_number(row.get("boosted"))) or "0",
                ))
        self._set_party_detail_rows(
            self.party_card_mastery_table,
            card_mastery_rows,
            "尚未解密到卡片掌握之力档案",
        )
        self._set_party_detail_rows(
            self.party_card_table,
            card_rows,
            "未解密到独立卡片档案；当前装备档案不包含卡片与卡片掌握等级",
        )
        self._set_party_detail_rows(
            self.party_talisman_table,
            categorized["护符"],
            "当前查看信息响应尚未包含护符档案",
        )
        if card_rows or card_mastery_rows:
            cards_source = norm(card_estimate.get("cardsSource"))
            estimate_level = int(as_number(card_estimate.get("estimatedMasteryLevel")))
            observed_mastery = sum(
                1
                for row in card_estimate.get("rows", [])
                if isinstance(row, dict) and row.get("levelSource") == "observed"
            )
            estimate_note = (
                "卡片初始合计按已解密卡片品质计算"
                if cards_source == "observed"
                else "卡片档案未解密，初始合计按全L最大值的40%～90%均匀分布估算，猜测值取期望65%"
            )
            self.party_card_status.setText(
                f"已解密 {len(card_rows)} 个卡片槽位、{observed_mastery} 项掌握等级；"
                f"{estimate_note}。未读取的掌握等级按 {estimate_level} 级估算（限制在15–25级），"
                "表内“实测/估算”会逐项标明。"
            )
        else:
            self.party_card_status.setText(
                "装备档案已单独解析；卡片页和卡片总属性掌握等级使用另一组登录档案，"
                "当前记录尚未完成该组解密。"
            )
        self._set_party_detail_rows(
            self.party_passive_buff_table,
            self._party_passive_buff_rows(job),
            "未找到该职业默认加点中的被动 Buff",
        )

        other_rows: list[tuple[str, str]] = [
            ("实时ID", member.actor_id),
            ("职业", job),
        ]
        if member.level > 0:
            other_rows.append(("等级", str(member.level)))
        if member.party_position > 0:
            other_rows.append(("队伍位置", str(member.party_position)))
        if member.source_layout:
            other_rows.append(("识别来源", member.source_layout))
        if member.confidence > 0:
            other_rows.append(("识别置信度", f"{member.confidence * 100:.0f}%"))
        if profile is None:
            other_rows.append(("查看信息", "尚未关联到该实时ID的响应"))
        else:
            other_rows.append(("查看信息", profile.status))
            ignored_fields = {"可见文本", "数值模板码（未映射）", "解密头"}
            other_rows.extend(
                (norm(key), norm(value))
                for key, value in profile.fields
                if norm(key) not in ignored_fields
                and not norm(key).startswith("卡片掌握|")
                and norm(key) and norm(value)
            )
            other_rows.extend(
                (norm(key), norm(value))
                for key, value in profile.stats
                if norm(key) and norm(value)
            )
        self._set_party_detail_rows(
            self.party_other_info_table,
            other_rows,
            "暂无其他已验证信息",
        )


    def _clear_party_detail_tables(self) -> None:
        for table in (
            self.party_equipment_table,
            self.party_engraving_table,
            self.party_glyph_table,
            self.party_plate_table,
            self.party_card_mastery_table,
            self.party_card_table,
            self.party_talisman_table,
            self.party_passive_buff_table,
            self.party_other_info_table,
        ):
            table.setRowCount(0)


    def _set_party_detail_rows(
        self,
        table: QTableWidget,
        rows: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
        empty_message: str,
    ) -> None:
        display_rows = list(rows)
        if not display_rows:
            display_rows = [
                tuple(["未识别"] + ["-"] * max(0, table.columnCount() - 2) + [empty_message])
            ]
        table.setRowCount(len(display_rows))
        for row_index, row in enumerate(display_rows):
            values = list(row)[: table.columnCount()]
            values.extend([""] * (table.columnCount() - len(values)))
            for column, value in enumerate(values):
                item = self._item(value, Qt.AlignLeft | Qt.AlignVCenter)
                item.setToolTip(value)
                table.setItem(row_index, column, item)


    def _party_passive_buff_rows(self, job: str) -> list[tuple[str, str, str]]:
        payload = load_skill_build_defaults().get(norm(job), {})
        if not isinstance(payload, dict):
            return []
        normal_build = payload.get("skill_build", {})
        mastery_build = payload.get("skill_mastery_build", {})
        normal_levels = (
            normal_build.get("levels", {}) if isinstance(normal_build, dict) else {}
        )
        mastery_levels = (
            mastery_build.get("levels", {}) if isinstance(mastery_build, dict) else {}
        )
        levels_by_id = {
            str(skill_id): max(0, int(as_number(level)))
            for skill_id, level in normal_levels.items()
        }
        for skill_id, level in mastery_levels.items():
            levels_by_id.setdefault(str(skill_id), max(0, int(as_number(level))))

        skill_names: dict[str, str] = {}
        for mode in ("normal", "master"):
            tree = self.store.skill_build_tree(job, 70, mode=mode)
            for skill_id, row in tree.get("skills", {}).items():
                if isinstance(row, dict) and norm(row.get("name")):
                    skill_names[str(skill_id)] = norm(row.get("name"))
        learned_levels = {
            skill_names.get(skill_id, ""): level
            for skill_id, level in levels_by_id.items()
            if level > 0 and skill_names.get(skill_id, "")
        }

        buff_rows_by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self.store.tables.get("buff", []):
            if not isinstance(row, dict):
                continue
            if norm(row.get("类别")) != "被动":
                continue
            if norm(row.get("来源")) not in {norm(job), "通用"}:
                continue
            skill_name = norm(row.get("技能"))
            if skill_name in learned_levels:
                buff_rows_by_skill[skill_name].append(row)

        result: list[tuple[str, str, str]] = []
        for skill_name in sorted(buff_rows_by_skill):
            learned_level = learned_levels[skill_name]
            rows = buff_rows_by_skill[skill_name]
            available_levels = sorted(
                {
                    max(0, int(as_number(row.get("等级"))))
                    for row in rows
                    if as_number(row.get("等级")) >= 0
                }
            )
            selected_level = max(
                (level for level in available_levels if level <= learned_level),
                default=learned_level,
            )
            effects = [
                self._format_party_passive_effect(row)
                for row in rows
                if max(0, int(as_number(row.get("等级")))) == selected_level
            ]
            effects = [effect for effect in effects if effect]
            if effects:
                result.append((skill_name, f"Lv.{learned_level}", "，".join(effects)))
        return result


    @staticmethod
    def _format_party_passive_effect(row: dict[str, Any]) -> str:
        attribute = norm(row.get("属性"))
        raw_value = row.get("表格数值")
        if not attribute or raw_value in (None, ""):
            return ""
        numeric = as_number(raw_value)
        if isinstance(raw_value, (int, float)) or norm(raw_value).replace(".", "", 1).lstrip("+-").isdigit():
            value_text = f"{numeric:.4f}".rstrip("0").rstrip(".")
            if numeric > 0:
                value_text = f"+{value_text}"
        else:
            value_text = norm(raw_value)
        if attribute.endswith("%"):
            attribute = attribute[:-1]
            value_text += "%"
        return f"{attribute} {value_text}".strip()
