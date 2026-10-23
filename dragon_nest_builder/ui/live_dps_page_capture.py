from __future__ import annotations

from .live_dps_page_core import *
from .live_dps_page_curves import *
from .live_dps_timeline_widgets import *

class LiveDpsCaptureMixin:
    def _open_history_folder(self) -> None:
        history_path = self.collector.history_path
        history_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(history_path.parent))
        except OSError as exc:
            QMessageBox.warning(self, "本地历史", f"无法打开保存目录：{exc}")


    def _player_display_details(
        self,
        snapshot: CombatSnapshot,
        events: tuple[CombatEvent | SkillCastEvent, ...] = (),
    ) -> dict[str, tuple[str, str]]:
        jobs_by_source: dict[str, str] = {}
        ordered_ids: list[str] = []
        scored_jobs = {
            str(score.source_id).upper(): score_export_job(score)
            for score in getattr(self, "_team_score_rows", ())
        }
        # The damage table is the canonical roster. Its order also provides a
        # stable suffix for duplicate professions on every statistics page.
        for player in event_platform_for(snapshot).players:
            ordered_ids.append(player.source_id)
            jobs_by_source[player.source_id] = scored_jobs.get(
                str(player.source_id).upper(), player.job
            )
        for event in sorted(events, key=lambda row: row.timestamp_ms):
            if event.source_id not in jobs_by_source:
                ordered_ids.append(event.source_id)
            jobs_by_source[event.source_id] = (
                jobs_by_source.get(event.source_id, "") or event.job
            )
        return _profession_player_display_details(
            ordered_ids,
            jobs_by_source,
            self._profession_player_order,
        )


    def _reset_session_display_state(self) -> None:
        self._profession_player_order.clear()
        self._boss_target_order.clear()
        self._timeline_player_details.clear()
        self._timeline_target_details.clear()
        self._timeline_player_signature = ()
        self._timeline_skill_signature = ()
        self._timeline_snapshot_event_sources = None
        self._timeline_events = ()
        self._rendered_view_revisions.clear()
        self._summary_snapshot_revision = -1
        self._summary_snapshot = None
        self._event_snapshot_revision = -1
        self._event_snapshot = None
        self._latest_snapshot = None
        self._latest_effect_seconds = 0.0
        self._player_buff_filter_options_signature = ()
        self._boss_stats_buff_filter_options_signature = ()
        self._boss_effect_overview_target_signature = ()
        self._boss_effect_overview_groups_by_key.clear()
        self.boss_effect_overview_target_combo.blockSignals(True)
        self.boss_effect_overview_target_combo.clear()
        self.boss_effect_overview_target_combo.addItem("全部Boss", "__all__")
        self.boss_effect_overview_target_combo.blockSignals(False)
        self._damage_curve_boss_phases = ()
        self.curve.set_boss_phases(())
        self.curve.set_boss_hp_series(())
        self.curve.set_named_series((), 0.0)
        self._boss_stats_groups = ()
        self._boss_stats_groups_revision = -1
        self._boss_hp_context_revision = -1
        self._completed_boss_group_keys = frozenset()
        self._fixed_boss_hp_profile_label = ""
        self._fixed_boss_hp_references.clear()
        self._boss_effect_output_windows_revision = -1
        self._boss_effect_output_windows_signature = ()
        self._boss_effect_output_windows_by_target.clear()
        self._boss_freeze_rows_revision = -1
        self._boss_freeze_rows_signature = ()
        self._boss_freeze_rows = ()
        self._boss_stats_group_signature = ()
        self._boss_stats_selected_keys.clear()
        self._boss_stats_realtime_seconds = None
        self._boss_stats_realtime_follow_latest = True
        self._boss_stats_pending_hover_seconds = None
        self._boss_stats_hover_update_scheduled = False
        self._boss_stats_realtime_windows = ()
        self._boss_stats_realtime_groups = ()
        self._boss_stats_realtime_hp_rows = ()
        if hasattr(self, "boss_stats_realtime_table"):
            self.boss_stats_realtime_table.setRowCount(0)
            self.boss_stats_realtime_time_label.setText("时点：最新")
            self.boss_stats_realtime_note.setText(
                "单击下方伤害曲线或 Boss 状态曲线选择时点；未匹配专属参数时按红龙佩达基准估算当前抗性、防御和致命抵抗。"
            )
        self._boss_stats_timeline_events = ()
        self._boss_stats_timeline_cache_key = None
        self._boss_stats_timeline_cache_value = ((), 0.0)
        self._boss_stats_timeline_display_key = None
        self._boss_stats_timeline_table_signature = None
        self._boss_stats_timeline_table_timer.stop()
        self._boss_stats_player_details.clear()
        self._boss_stats_target_details.clear()
        self._boss_stats_action_events = ()
        self._boss_stats_action_timeline_events = ()
        self._boss_stats_action_display_key = None
        self._boss_stats_action_table_signature = None
        self._boss_stats_action_table_timer.stop()
        self._team_score_rows = ()
        self._team_score_revision = -1
        self._team_score_actual_axis_signature = ()
        self._team_score_selected_source_id = ""
        self._team_score_selected_boss_key = "__all__"
        self._team_score_boss_options.clear()
        self._team_score_boss_names.clear()
        self._team_score_boss_windows.clear()
        self._team_score_boss_signature = ()
        self._team_score_selected_boss_preset_key = "__auto__"
        self._team_score_auto_boss_preset_name = _TEAM_SCORE_DEFAULT_BOSS_PRESET
        if hasattr(self, "team_score_boss_preset_combo"):
            self.team_score_boss_preset_combo.blockSignals(True)
            auto_index = self.team_score_boss_preset_combo.findData("__auto__")
            self.team_score_boss_preset_combo.setCurrentIndex(max(0, auto_index))
            if auto_index >= 0:
                self.team_score_boss_preset_combo.setItemText(
                    auto_index, f"自动：{_TEAM_SCORE_DEFAULT_BOSS_PRESET}"
                )
            self.team_score_boss_preset_combo.blockSignals(False)
        self._team_score_theory_scope = ()
        self._team_score_theory_signature = None
        self._team_score_theory_plans = {}
        self._team_score_theory_jobs = ()
        self._team_score_theory_plan_cache.clear()
        self._team_score_build_override_cache.clear()
        self._team_score_rows_cache.clear()
        self._team_score_rendered_boss_key = ""
        self._team_score_rendered_boss_preset_name = ""
        self._team_score_refresh_pending = False
        self._team_score_refresh_timer.stop()


    def _boss_target_details(
        self,
        snapshot: CombatSnapshot,
        events: tuple[CombatEvent | SkillCastEvent, ...],
        player_details: dict[str, tuple[str, str]],
    ) -> dict[str, str]:
        status_names: dict[str, str] = {}
        status_targets: list[str] = []
        platform = event_platform_for(snapshot)
        for effect in platform.boss_effect_events:
            if (
                not effect.target_id
                or effect.target_id in player_details
            ):
                continue
            if effect.target_id not in status_targets:
                status_targets.append(effect.target_id)
            if (
                effect.target_name
                and not _is_anonymous_player_name(
                    effect.target_name, effect.target_id
                )
            ):
                status_names[effect.target_id] = effect.target_name
        for target in platform.damage_targets:
            if target.target_name:
                status_names.setdefault(target.target_id, target.target_name)

        target_counts: Counter[str] = Counter(
            event.target_id
            for event in events
            if isinstance(event, CombatEvent)
            and not event.healing
            and event.target_id
            and event.target_id not in player_details
            and event.target_id != "0x00000000"
        )
        special_targets = {
            "__other_targets__": "其他目标",
            "__unresolved_target__": "未能可靠区分目标",
        }
        candidates = {
            target.target_id
            for target in platform.damage_targets
            if target.target_id not in special_targets
        }
        candidates.update(status_targets)
        if not candidates and target_counts:
            candidates.add(target_counts.most_common(1)[0][0])

        ordered_candidates = [
            target_id for target_id in status_targets if target_id in candidates
        ]
        ordered_candidates.extend(
            target_id
            for target_id, _count in target_counts.most_common()
            if target_id in candidates and target_id not in ordered_candidates
        )
        ordered_candidates.extend(
            target.target_id
            for target in platform.damage_targets
            if target.target_id in candidates
            and target.target_id not in ordered_candidates
        )
        target_identities = {
            target_id: CombatAggregator._target_identity_key(
                target_id,
                status_names.get(target_id, ""),
            )
            for target_id in candidates
        }
        for target_id in ordered_candidates:
            identity = target_identities[target_id]
            if identity not in self._boss_target_order:
                self._boss_target_order.append(identity)

        details = {
            source_id: f"未确认（{name}）"
            for source_id, (name, _job) in player_details.items()
        }
        details.update(special_targets)
        for target_id in candidates:
            identity = target_identities[target_id]
            index = self._boss_target_order.index(identity) + 1
            name = status_names.get(target_id, "")
            # Runtime actor IDs change between phases and make one Boss look
            # like several targets. Keep the ID internally for attribution,
            # but present one stable label for the same name/template.
            details[target_id] = name or f"Boss {index}"
        return details


    def _toggle_capture(self) -> None:
        if self._capture_active:
            self._stop_capture()
        else:
            self._start_capture()


    def set_npcap_available(self, available: bool) -> None:
        self.npcap_button.setText("Npcap 已就绪" if available else "安装 Npcap")
        self.npcap_button.setProperty("npcapAvailable", bool(available))


    def _check_or_install_npcap(self) -> None:
        bootstrap = getattr(self.window(), "npcap_bootstrap", None)
        if bootstrap is None:
            QMessageBox.warning(self, "Npcap", "Npcap 安装组件尚未初始化，请稍后重试。")
            return
        bootstrap.check_or_install()


    def _update_team_score_sync_status(self, message: str = "") -> None:
        config = load_team_score_sync_config(self._live_dps_settings)
        if message:
            short = message if len(message) <= 22 else f"{message[:21]}..."
            self.score_sync_status_label.setText(short)
            self.score_sync_status_label.setToolTip(message)
            # This page can be embedded in more than one top-level window.
            # Query the optional status-bar API directly so a missing Qt type
            # import cannot abort the entire page refresh and leave filters
            # half populated.
            try:
                owner = self.window()
                status_bar_getter = getattr(owner, "statusBar", None)
                if callable(status_bar_getter):
                    status_bar = status_bar_getter()
                    if status_bar is not None:
                        status_bar.showMessage(message, 8000)
            except RuntimeError:
                # The owner may already be pending deletion during shutdown.
                pass
            return
        if config.ready:
            self.score_sync_status_label.setText("云端评分已配置")
            self.score_sync_status_label.setToolTip(
                "评分完成后先保存到本地 SQLite，再自动上传到云端数据库"
            )
        elif config.enabled:
            self.score_sync_status_label.setText("等待配置")
            self.score_sync_status_label.setToolTip(
                "评分会先保存到本地；配置云端评分服务后可补传"
            )
        else:
            self.score_sync_status_label.setText("自动上传已关闭")
            self.score_sync_status_label.setToolTip(
                "评分仍会按内容哈希保存到本地"
            )


    def _open_team_score_sync_settings(self) -> None:
        dialog = SupabaseScoreSyncDialog(self._live_dps_settings, self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._update_team_score_sync_status()
        self._team_score_sync_dispatched_source = ""
        if (
            self._team_score_rows
            and self._team_score_rendered_boss_key == "__all__"
        ):
            self._queue_team_score_sync(self._team_score_rows)
        else:
            self._schedule_team_score_sync_generation()


    def _flush_team_score_offline_queue(self) -> None:
        config = load_team_score_sync_config(self._live_dps_settings)
        if not config.ready:
            return
        if (
            self._team_score_sync_thread is not None
            and self._team_score_sync_thread.isRunning()
        ):
            return
        self._update_team_score_sync_status("正在补传本地评分队列...")
        thread, worker = start_team_score_queue_sync_thread(
            self, config, self._team_score_sync_finished
        )
        self._team_score_sync_thread = thread
        self._team_score_sync_worker = worker
        thread.finished.connect(self._team_score_sync_thread_finished)
        thread.start()


    def _schedule_team_score_sync_generation(self) -> None:
        config = load_team_score_sync_config(self._live_dps_settings)
        if (
            not config.ready
            or self._capture_active
            or self._team_score_capture_path is None
            or not self._team_score_capture_path.is_file()
        ):
            return
        QTimer.singleShot(250, self._generate_team_score_for_sync)


    def _generate_team_score_for_sync(self) -> None:
        config = load_team_score_sync_config(self._live_dps_settings)
        if self._refresh_running or self._team_score_refresh_running:
            QTimer.singleShot(500, self._generate_team_score_for_sync)
            return
        if (
            not config.ready
            or self._capture_active
            or self._team_score_capture_path is None
            or not self._team_score_capture_path.is_file()
        ):
            return
        revision = self.collector.aggregator.revision
        if (
            revision == self._team_score_revision
            and self._team_score_rendered_boss_key == "__all__"
            and self._team_score_rows
        ):
            self._queue_team_score_sync(self._team_score_rows)
            return
        self._team_score_selected_boss_key = "__all__"
        self._update_team_score_sync_status("正在生成整场全队评分...")
        try:
            snapshot = self.collector.snapshot(include_events=True)
            self._event_snapshot_revision = revision
            self._event_snapshot = snapshot
            self._fill_team_scores(snapshot, revision)
        except Exception as exc:
            self._update_team_score_sync_status(f"评分生成失败：{exc}")


    def _set_team_score_capture_path(self, path: Path | None) -> None:
        self._team_score_capture_path = Path(path) if path is not None else None
        self._team_score_sync_dispatched_source = ""
        self._team_score_sync_pending_job = None


    @staticmethod
    def _team_score_sync_source_key(path: Path) -> str:
        try:
            stat = path.stat()
            return f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        except OSError:
            return str(path)


    def _queue_team_score_sync(
        self, rows: tuple[PlayerScore, ...]
    ) -> None:
        path = self._team_score_capture_path
        if path is None or not path.is_file() or self._capture_active or not rows:
            return
        source_key = self._team_score_sync_source_key(path)
        if source_key == self._team_score_sync_dispatched_source:
            return
        groups = self._boss_stats_groups
        actual_dps_by_source = {
            str(player.source_id).upper(): max(0.0, float(player.dps))
            for player in (
                event_platform_for(self._latest_snapshot).players
                if self._latest_snapshot is not None
                else ()
            )
        }
        latest_snapshot = self._latest_snapshot
        from ..live_dps.team_score_sync import attach_team_output_time
        team_output_windows = (
            TeamScoreEngine._output_windows(latest_snapshot, frozenset(
                target_id for group in groups for target_id in group.target_ids))
            if latest_snapshot is not None else None
        )

        def score_detail_for(score: PlayerScore) -> dict[str, object]:
            detail = asdict(score)
            if latest_snapshot is None:
                return detail
            attach_team_output_time(detail, team_output_windows)
            detail["instance_completion"] = dict(
                getattr(latest_snapshot, "instance_completion", {})
            )
            source_id = str(score.source_id).upper()
            detail["_compact_duration_seconds"] = float(
                max(latest_snapshot.elapsed_seconds, latest_snapshot.boss_effect_current_seconds)
            )
            detail["_compact_player_buffs"] = [
                asdict(buff)
                for buff in latest_snapshot.player_buff_series
                if str(buff.target_id).upper() == source_id
            ]
            return detail

        job = TeamScoreSyncJob(
            capture_path=path,
            boss_names=tuple(group.name for group in groups),
            target_ids=tuple(
                target_id
                for group in groups
                for target_id in group.target_ids
            ),
            rows=tuple(
                TeamScoreExportRow(
                    job=score_export_job(score),
                    total_score=score.total_score,
                    output_score=score.output_score,
                    support_score=score.support_score,
                    specialty_score=score.specialty_score,
                    survival_penalty=score.survival_penalty,
                    theory_damage_completion=score.theory_damage_completion,
                    actual_dps=actual_dps_by_source.get(
                        str(score.source_id).upper()
                    ),
                    score_detail=score_detail_for(score),
                )
                for score in rows
            ),
            config=load_team_score_sync_config(self._live_dps_settings),
        )
        if (
            self._team_score_sync_thread is not None
            and self._team_score_sync_thread.isRunning()
        ):
            self._team_score_sync_dispatched_source = source_key
            self._team_score_sync_pending_job = job
            self._update_team_score_sync_status("已有上传任务，当前数据正在排队")
            return
        self._start_team_score_sync(job)
        self._team_score_sync_dispatched_source = source_key


    def _start_team_score_sync(self, job: TeamScoreSyncJob) -> None:
        self._update_team_score_sync_status("正在上传全队评分...")
        thread, worker = start_team_score_sync_thread(
            self, job, self._team_score_sync_finished
        )
        self._team_score_sync_thread = thread
        self._team_score_sync_worker = worker
        thread.finished.connect(self._team_score_sync_thread_finished)
        thread.start()


    @Slot(bool, str)
    def _team_score_sync_finished(self, succeeded: bool, message: str) -> None:
        prefix = "上传完成" if succeeded else "上传失败"
        self._update_team_score_sync_status(f"{prefix}：{message}")


    @Slot()
    def _team_score_sync_thread_finished(self) -> None:
        self._team_score_sync_thread = None
        self._team_score_sync_worker = None
        pending = self._team_score_sync_pending_job
        self._team_score_sync_pending_job = None
        if pending is not None:
            self._start_team_score_sync(pending)


    def _start_capture(self, *, automatic: bool = False) -> None:
        if self._capture_active:
            return
        stream = None
        preloaded_frames: tuple[tuple[float, bytes, int, int], ...] = ()
        try:
            udp_ports, tcp_ports = self._game_transport_ports()
            probe = self._auto_capture_probe
            self._auto_capture_probe = None
            if probe is not None:
                stream, preloaded_frames = probe.handoff()
            self.collector.start_npcap(
                udp_ports,
                tcp_ports,
                port_provider=self._game_transport_ports,
                stream=stream,
                preloaded_frames=preloaded_frames,
            )
        except Exception as exc:
            if stream is not None:
                stream.close()
            self.collector.status = str(exc)
            if not automatic:
                QMessageBox.warning(self, "实战统计", str(exc))
            else:
                self._auto_capture_next_check = time.monotonic() + 5.0
            self._refresh()
            return
        capture_path = getattr(
            self.collector.source, "diagnostic_trace_path", None
        )
        self._reset_session_display_state()
        self._set_team_score_capture_path(
            Path(capture_path) if capture_path is not None else None
        )
        self._capture_active = True
        self._capture_error_shown = False
        self._capture_started_at = time.time()
        self.capture_button.setText("停止实时统计")
        self.capture_button.setEnabled(True)
        self.collector.status = (
            "已确认副本事件，Npcap 自动统计中"
            if automatic else "Npcap 实时统计中"
        )
        self._refresh()


    def _auto_capture_enabled_changed(self, enabled: bool) -> None:
        self._live_dps_settings.setValue(
            "live_dps/auto_capture_enabled", bool(enabled)
        )
        if enabled:
            self._auto_capture_next_check = 0.0
            if not self._capture_active:
                self.status_label.setText("自动统计已开启，等待游戏登录")
            return
        if self._auto_capture_probe is not None:
            self._auto_capture_probe.close()
            self._auto_capture_probe = None
        if not self._capture_active:
            self.status_label.setText("未连接")


    def _poll_auto_capture(self) -> None:
        if not self.auto_capture_checkbox.isChecked() or self._capture_active:
            return
        now = time.monotonic()
        if now < self._auto_capture_paused_until:
            remaining = max(1, math.ceil(self._auto_capture_paused_until - now))
            self.status_label.setText(f"自动统计暂停 {remaining}s")
            return
        probe = self._auto_capture_probe
        if probe is not None:
            if probe.error:
                probe.close()
                self._auto_capture_probe = None
                self._auto_capture_next_check = now + 5.0
                self.status_label.setText(f"轻量监听重试中：{probe.error}")
                return
            if probe.confirmed:
                self.status_label.setText(
                    f"已确认副本事件：{probe.confirmation_reason}"
                )
                self._start_capture(automatic=True)
                return
            self.status_label.setText(
                f"轻量监听中，等待副本事件 | 逻辑包 {probe.packet_count}"
            )
            return
        if now < self._auto_capture_next_check:
            return
        self._auto_capture_next_check = now + 3.0
        try:
            udp_ports, tcp_ports = self._game_transport_ports()
            self._auto_capture_probe = V576AutoCaptureProbe(
                udp_ports,
                self.collector.profile_path.parents[1],
                self.collector.profile.known_players,
                tcp_ports=tcp_ports,
                port_provider=self._game_transport_ports,
            )
            self.status_label.setText("轻量监听中，等待副本事件")
        except Exception:
            self.status_label.setText("自动统计已开启，等待游戏登录")


    @staticmethod
    def _game_transport_ports() -> tuple[list[int], list[int]]:
        pid = _windows_process_id("dragonnest_reborn.exe")
        if pid <= 0:
            pid = _windows_process_id("dragonnest.exe")
        if pid <= 0:
            raise RuntimeError(
                "未找到游戏进程 dragonnest_reborn.exe 或 dragonnest.exe"
            )
        udp_ports = _windows_udp_ports_for_pid(pid)
        tcp_ports = _windows_tcp_ports_for_pid(pid)
        if not udp_ports and not tcp_ports:
            raise RuntimeError("未找到游戏 TCP/UDP 端口，请确认游戏已经登录")
        return udp_ports, tcp_ports


    def _stop_capture(self) -> None:
        if not self._capture_active:
            return
        self.capture_button.setEnabled(False)
        self.collector.status = "正在停止实时统计..."
        self._refresh()
        self._finish_capture("实时统计已停止")


    def _shutdown_capture(self) -> None:
        if self._replay_cancel_event is not None:
            self._replay_cancel_event.set()
        if self._replay_thread is not None and self._replay_thread.isRunning():
            self._replay_thread.quit()
            self._replay_thread.wait(5000)
        if (
            self._team_score_sync_thread is not None
            and self._team_score_sync_thread.isRunning()
        ):
            self._team_score_sync_thread.quit()
            self._team_score_sync_thread.wait(5000)
        if self._auto_capture_probe is not None:
            self._auto_capture_probe.close()
            self._auto_capture_probe = None
        self.collector.close()


    def _poll_capture(self) -> None:
        if not self._capture_active:
            return
        source = self.collector.source
        packet_count = int(getattr(source, "packet_count", 0))
        udp_packet_count = int(getattr(source, "udp_packet_count", 0))
        tcp_packet_count = int(getattr(source, "tcp_packet_count", 0))
        event_count = int(getattr(source, "decoded_event_count", 0))
        elapsed = max(0.0, time.time() - self._capture_started_at)
        ledger_stats = getattr(source, "event_ledger_stats", None)
        ledger_mode = getattr(source, "event_ledger_mode", "")
        ledger_text = ""
        if ledger_stats is not None:
            duplicate_count = int(ledger_stats.event_id_duplicates) + int(
                ledger_stats.fallback_duplicates
            ) + int(getattr(ledger_stats, "mirror_duplicates", 0))
            mode_label = "事件账本" if ledger_mode == "event_id" else "旧去重"
            ledger_text = f" | {mode_label}过滤 {duplicate_count}"
        self.collector.status = (
            f"Npcap 实时统计中 | {elapsed:.0f}s | "
            f"UDP {udp_packet_count} / TCP {tcp_packet_count} | "
            f"逻辑包 {packet_count} | 事件 {event_count}"
        )
        self.collector.status += ledger_text
        self.status_label.setText(self.collector.status)


    def _finish_capture(self, final_status: str | None = None) -> None:
        final_status = final_status or self.collector.status
        try:
            self.collector.poll()
        except Exception:
            pass
        self.collector.stop()
        self.collector.status = final_status
        self._capture_active = False
        if self.auto_capture_checkbox.isChecked():
            self._auto_capture_paused_until = time.monotonic() + 30.0
        self._warm_team_score_workers()
        self.capture_button.setText("开始实时统计")
        self.capture_button.setEnabled(True)
        QTimer.singleShot(0, self._refresh)
        QTimer.singleShot(500, self._schedule_team_score_sync_generation)


    def _load_replay(self) -> None:
        if self._replay_thread is not None and self._replay_thread.isRunning():
            return
        history_directory = (
            self.collector.history_path.parent / "live_dps_diagnostics"
        )
        history_directory.mkdir(parents=True, exist_ok=True)
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "载入实战记录",
            str(history_directory),
            "实时统计记录 (*.dpscap);;Packet Capture (*.pcapng);;JSON Lines (*.jsonl);;所有文件 (*)",
        )
        if not filename:
            return

        self._load_replay_path(filename)


    def _load_replay_path(
        self,
        filename: str | Path,
        *,
        show_progress: bool = True,
    ) -> None:
        """Load a capture selected by either Qt or the V4.10 Tauri shell."""
        if self._replay_thread is not None and self._replay_thread.isRunning():
            return

        if self._capture_active:
            self._finish_capture("实时统计已停止")
        elif self.collector.source is not None:
            self.collector.stop()

        path = Path(filename)
        self._warm_team_score_workers()
        self._replay_loading_path = path
        cancel_event = threading.Event()
        progress = None
        if show_progress:
            progress = QProgressDialog(
                f"正在准备载入：{path.name}",
                "取消",
                0,
                100,
                self,
            )
            progress.setWindowTitle("载入实战记录")
            progress.setWindowModality(Qt.WindowModality.NonModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setValue(0)
        self._v410_replay_progress_value = 0
        self._v410_replay_progress_label = f"正在准备载入：{path.name}"
        self._v410_replay_loading = True

        thread = QThread(self)
        worker = _ReplayLoadWorker(
            path,
            self.collector.profile_path.parents[1],
            dict(self.collector.profile.known_players),
            cancel_event,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._replay_load_progressed)
        worker.loaded.connect(self._replay_load_succeeded)
        worker.failed.connect(self._replay_load_failed)
        worker.cancelled.connect(self._replay_load_cancelled)
        for terminal_signal in (worker.loaded, worker.failed, worker.cancelled):
            terminal_signal.connect(thread.quit)
            terminal_signal.connect(worker.deleteLater)
        thread.finished.connect(self._replay_thread_finished)
        thread.finished.connect(thread.deleteLater)
        if progress is not None:
            progress.canceled.connect(self._cancel_replay_load)

        self._replay_thread = thread
        self._replay_worker = worker
        self._replay_progress = progress
        self._replay_cancel_event = cancel_event
        self.replay_button.setEnabled(False)
        if progress is not None:
            progress.show()
        thread.start()


    @Slot(int, str)
    def _replay_load_progressed(self, value: int, label: str) -> None:
        self._v410_replay_progress_value = max(0, min(100, int(value)))
        self._v410_replay_progress_label = label
        if self._replay_progress is None:
            return
        self._replay_progress.setLabelText(label)
        self._replay_progress.setValue(max(0, min(100, int(value))))


    @Slot()
    def _cancel_replay_load(self) -> None:
        if self._replay_cancel_event is not None:
            self._replay_cancel_event.set()
        if self._replay_progress is not None:
            self._replay_progress.setLabelText("正在取消载入...")
            self._replay_progress.setCancelButton(None)


    @Slot(object, str)
    def _replay_load_succeeded(
        self,
        aggregator: CombatAggregator,
        status: str,
    ) -> None:
        if self._replay_cancel_event is not None and self._replay_cancel_event.is_set():
            self._finish_replay_load_ui()
            return
        self.collector.install_replay_result(aggregator, status)
        self._selected_source_id = ""
        self._reset_session_display_state()
        self._set_team_score_capture_path(self._replay_loading_path)
        self._replay_loading_path = None
        self._finish_replay_load_ui()
        self._begin_view_progress(
            self.live_dps_tabs.currentIndex(),
            "数据解码完成，正在生成当前页面...",
        )
        QTimer.singleShot(0, self._refresh_loaded_replay_view)


    def _refresh_loaded_replay_view(self) -> None:
        self._refresh()
        # Populating the replay tables changes several nested size hints while
        # the modal progress dialog is still leaving the event loop. Without a
        # post-load layout pass, the damage-curve controls can remain clipped
        # until the main window is manually restored/maximized.
        self.ensure_visible_layout()
        self._schedule_team_score_sync_generation()


    @Slot(str)
    def _replay_load_failed(self, message: str) -> None:
        self._finish_replay_load_ui()
        self._replay_loading_path = None
        QMessageBox.warning(self, "载入失败", message)


    @Slot()
    def _replay_load_cancelled(self) -> None:
        self._finish_replay_load_ui()
        self._replay_loading_path = None
        self.collector.status = "已取消载入实战记录"
        self._refresh()


    def _finish_replay_load_ui(self) -> None:
        if self._replay_progress is not None:
            self._replay_progress.close()
            self._replay_progress.deleteLater()
            self._replay_progress = None
        self.replay_button.setEnabled(True)
        self._v410_replay_progress_value = 100
        self._v410_replay_progress_label = "载入完成"
        self._v410_replay_loading = False


    @Slot()
    def _replay_thread_finished(self) -> None:
        self._replay_thread = None
        self._replay_worker = None
        self._replay_cancel_event = None


    def _clear(self) -> None:
        if self._capture_active:
            self._stop_capture()
        self.collector.reset()
        self._set_team_score_capture_path(None)
        self._selected_source_id = ""
        self._reset_session_display_state()
        self._refresh()


    def _poll(self) -> None:
        self._poll_auto_capture()
        self._poll_capture()
        try:
            changed = self.collector.poll()
        except Exception as exc:
            self.collector.stop()
            self.collector.status = f"实时抓包失败：{exc}"
            if self._capture_active and not self._capture_error_shown:
                self._capture_error_shown = True
                QMessageBox.warning(self, "实时统计", self.collector.status)
            self._capture_active = False
            self.capture_button.setText("开始实时统计")
            self.capture_button.setEnabled(True)
            changed = 0
        if changed or self._capture_active:
            # A live session must refresh even when no new attack packet is
            # decoded.  Deaths and idle periods still change the displayed
            # capture clock/DPS, and waiting for the next action made the
            # damage table appear frozen.
            self._ui_refresh_pending = True
        now = time.monotonic()
        refresh_due = (
            now - self._last_ui_refresh_at
            >= (
                self.CAPTURE_COMPLEX_REFRESH_INTERVAL_SECONDS
                if self._capture_active
                and self.live_dps_tabs.currentIndex() in {
                    1,
                    2,
                    getattr(self, "team_score_tab_index", 3),
                }
                else self.UI_REFRESH_INTERVAL_SECONDS
            )
        )
        if (
            self.isVisible()
            and refresh_due
            and self._ui_refresh_pending
        ):
            self._refresh()
            self._ui_refresh_pending = False
