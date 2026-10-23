from __future__ import annotations

from functools import lru_cache

from ..effect_stacking import EFFECT_STACKING_ENGINE, EffectInput
from ..job_mechanics import JOB_MECHANICS
from ..live_dps.aggregator import _is_recognized_player_job
from ..live_dps.buff_clear import player_buff_clear_times_by_target
from ..live_dps.event_platform import event_platform_for
from ..live_dps.summon_presence import summon_overlay_options
from ..live_dps.aura_state import is_aura_row, is_aura_damage_series, is_inferred_aura_row
from .overlay_lifecycle import acceleration_overlay_row, party_boss_health

class NativeOverlayMixin:
    def _register_capture_score_source(
        self,
        state: dict[str, Any],
        source_hash: str,
        *,
        snapshot: CombatSnapshot | None = None,
        source_file: str | Path | None = None,
    ) -> None:
        """Bind a frozen live snapshot to both its final hash and trace path.

        Replay loading registers ``_replays`` and ``_replay_sources`` together.
        Live capture used to register only the frozen snapshot after Stop.  The
        score page could therefore calculate a final score, but score sync had
        no capture path and silently skipped both the score row and .dpscap
        upload.  Keep active ``live:`` sessions memory-only, then register the
        immutable file as soon as Stop publishes its final hash.
        """

        replay_key = norm(source_hash)
        # Stop hands the exact frozen snapshot to the callback. Prefer that
        # object over the mutable ``live_capture.snapshot_object`` reference:
        # the next run may already have started while the previous score is
        # being registered.
        explicit_snapshot = snapshot is not None
        capture_snapshot = (
            snapshot
            if explicit_snapshot
            else getattr(self.live_capture, "snapshot_object", None)
        )
        if not replay_key or capture_snapshot is None:
            return

        final_key = norm(state.get("scoreSourceHash"))
        # A stopped capture remains available on ``live_capture`` while the
        # user is free to load and score older files.  Never bind that frozen
        # live snapshot to an unrelated offline replay hash.  Doing so made
        # the requested Boss ids come from (for example) 黑龙1 while players,
        # effects and damage came from the previously stopped 黑龙3 capture.
        # Only the active ``live:`` key or this capture's own immutable final
        # key may be registered from live-capture state.
        is_live_key = replay_key.startswith("live:")
        live_key_getter = getattr(self.live_capture, "live_source_hash", None)
        current_live_key = (
            norm(live_key_getter()) if callable(live_key_getter) else ""
        )
        # A ``live:`` key names one concrete recording generation. Never bind
        # an old browser request to the current generation's mutable snapshot.
        is_own_live_key = bool(current_live_key) and replay_key == current_live_key
        is_own_final_key = bool(final_key) and replay_key == final_key
        if (is_live_key and not is_own_live_key) or (
            not is_live_key and not is_own_final_key
        ):
            return

        source_file_text = norm(source_file) or norm(state.get("sourceFile"))
        capture_path: Path | None = None
        if (
            replay_key == final_key
            and not replay_key.startswith("live:")
            and str(state.get("state") or "") == "stopped"
            and bool(state.get("scoreReady"))
            and source_file_text
        ):
            candidate = Path(source_file_text).expanduser()
            if candidate.is_file():
                capture_path = candidate.resolve()

        with self._state_lock:
            # Immutable bindings are write-once for status/resolve callers.
            # Only the Stop callback, which owns the exact snapshot argument,
            # may establish or repair one.
            if (
                not is_live_key
                and not explicit_snapshot
                and replay_key in self._replays
            ):
                if capture_path is not None:
                    self._replay_sources.setdefault(replay_key, capture_path)
                return
            self._replays[replay_key] = capture_snapshot
            if capture_path is not None:
                self._replay_sources[replay_key] = capture_path
            final_map_history = tuple(
                dict(row)
                for row in (state.get("nestMapHistory") or ())
                if isinstance(row, dict)
            )
            if final_map_history:
                # The live decoder already owns the complete, ordered scene
                # evidence. Persist it as one small immutable tuple so upload
                # and the badge select the same latest battle map even when a
                # town/Garden row follows it.
                self._replay_map_history[replay_key] = final_map_history
            # Replay loading records exact map history itself.  A capture that
            # was just stopped previously registered only its frozen snapshot
            # and file path, so direct scoring lost the map ID and fell back to
            # Boss-name inference.  Persist the same exact identity already
            # shown by the live badge before the score/export thread starts.
            if (
                not final_map_history
                and str(state.get("nestConfidence") or "") == "exact_map_id"
            ):
                try:
                    map_id = int(state.get("nestMapId") or 0)
                except (TypeError, ValueError):
                    map_id = 0
                nest_name = str(state.get("nestName") or "").strip()
                if map_id > 0 or nest_name:
                    self._replay_map_history[replay_key] = (
                        {
                            "map_id": map_id,
                            "map_name": nest_name,
                            "nest_name": nest_name,
                            "official_name": nest_name,
                            "content_kind": "dungeon",
                            "observed_at": float(
                                state.get("nestObservedAt") or 0.0
                            ),
                            "layout": "live-final-exact-map-id",
                        },
                    )
            elif not final_map_history:
                # Non-leaders and captures started after the one-shot map
                # transition often have no exact room ID.  The live decoder
                # can still prove one dungeon from a unique formal-map entity
                # template.  Preserve that evidence for the just-stopped
                # replay/movement page instead of reverting to 未识别地图 until
                # the user manually reloads and rescans the file.
                confidence = str(state.get("nestConfidence") or "").strip()
                nest_name = str(state.get("nestName") or "").strip()
                if confidence in {
                    "entry_template",
                    "template",
                    "template_set",
                    "dominant_template_set",
                } and nest_name:
                    self._replay_map_history[replay_key] = (
                        {
                            "map_id": 0,
                            "map_name": nest_name,
                            "nest_name": str(
                                state.get("nestLabel") or nest_name
                            ).strip(),
                            "official_name": nest_name,
                            "content_kind": "dungeon",
                            "observed_at": float(
                                state.get("nestObservedAt") or 0.0
                            ),
                            "layout": (
                                str(state.get("nestMapLayout") or "").strip()
                                or "live-final-formal-map-entity-fallback"
                            ),
                            "confidence": confidence,
                            "template_id": int(
                                state.get("nestTemplateId") or 0
                            ),
                            "inferred": True,
                        },
                    )

    def capture_status(self, include_snapshot: bool = True) -> dict[str, Any]:
        # The main live page needs the progressively updated full snapshot.
        # Returning overlay_status() here leaves the page pinned to a replay
        # loaded before capture because that compact payload deliberately omits
        # the ``snapshot`` field.
        try:
            state = self.live_capture.status(include_snapshot=include_snapshot)
        except TypeError:
            # Lightweight test/embedded capture adapters may still implement
            # the older parameterless status contract.
            state = self.live_capture.status()
            if not include_snapshot:
                state.pop("snapshot", None)
        source_hash = norm(state.get("scoreSourceHash"))
        if not source_hash and isinstance(state.get("snapshot"), dict):
            source_hash = norm((state.get("snapshot") or {}).get("sourceHash"))
        self._register_capture_score_source(state, source_hash)
        auto_score_job_id = norm(state.get("autoScoreJobId"))
        if auto_score_job_id:
            job = self.jobs.status(auto_score_job_id)
            job_state = str(job.get("state") or "")
            state["autoScoreState"] = job_state
            state["autoScoreProgress"] = int(job.get("progress") or 0)
            if job_state == "completed":
                state["autoScoreStatus"] = "后台评分完成，已加入评分上传队列"
            elif job_state == "failed":
                state["autoScoreStatus"] = "后台评分失败，可在全队评分中手动重试"
                state["autoScoreError"] = str(job.get("error") or "")
            elif job_state == "cancelled":
                state["autoScoreStatus"] = "后台评分已取消，可在全队评分中手动重试"
            else:
                state["autoScoreStatus"] = str(
                    job.get("label") or state.get("autoScoreStatus") or "后台评分中"
                )
        teammate_payload = self._teammate_profiles_payload()
        state["teammateInfoRevision"] = teammate_payload["revision"]
        # The normal one-second status poll can wake the inspection overlay
        # when a click arrives or its background panel projection completes.
        # This keeps /teammate-info on a five-second fallback cadence instead
        # of polling it continuously for those transitions.
        inspection_revision = int(as_number(teammate_payload["revision"]))
        presentation_revision = max(0, inspection_revision) * 2
        projection_lock = getattr(self, "_teammate_projection_worker_lock", None)
        if projection_lock is not None:
            with projection_lock:
                cached_inspection = getattr(self, "_teammate_info_cache", None)
                cached_revision = int(as_number(getattr(
                    self, "_teammate_info_cache_revision", -1
                )))
                if cached_revision == inspection_revision and isinstance(cached_inspection, dict):
                    presentation_revision = max(
                        presentation_revision,
                        int(as_number(cached_inspection.get("presentationRevision"))),
                    )
        state["teammatePresentationRevision"] = presentation_revision
        state["teammateProfiles"] = teammate_payload["player_profiles"]
        state["teammateProfilePanels"] = teammate_payload["profile_panels"]
        return state
    def resolve_score_source_hash(self, source_hash: str) -> str:
        """Return the immutable key once a live capture has been finalized.

        A Stop response is intentionally asynchronous, so a browser request
        already in flight can still carry the old ``live:`` key.  Resolve it
        here before job deduplication and worker-limit selection, and register
        the frozen in-memory snapshot so scoring never falls back to replay
        decoding.
        """

        requested = norm(source_hash)
        resolver = getattr(self.live_capture, "resolve_score_source_hash", None)
        resolved = norm(resolver(requested)) if callable(resolver) else requested
        # A final score can be requested either with the stale ``live:`` key
        # (an in-flight browser request) or with the already promoted hash from
        # capture_status().  Register the source path in both cases so the
        # normal replay upload pipeline can persist the score and its capture.
        try:
            state = self.live_capture.status(include_snapshot=False)
        except (AttributeError, TypeError):
            state = {}
        self._register_capture_score_source(state, resolved)
        return resolved or requested
    def capture_timeline(self, after_sequence: int = 0) -> dict[str, Any]:
        return self.live_capture.timeline_status(after_sequence)

    def capture_voice_events(self) -> dict[str, Any]:
        return self.live_capture.voice_status()

    def wait_buff_overlay_change(
        self,
        after_revision: int = 0,
        timeout_ms: int = 15_000,
    ) -> dict[str, object]:
        """Long-poll one tiny Buff lifecycle signal for the desktop publisher."""

        waiter = getattr(self.live_capture, "wait_overlay_buff_revision", None)
        if not callable(waiter):
            return {
                "changed": False,
                "buffStateRevision": max(0, int(after_revision)),
            }
        return waiter(
            max(0, int(after_revision)),
            max(0, min(25_000, int(timeout_ms))) / 1000.0,
        )

    def buff_overlay_status(self) -> dict[str, Any]:
        """Return canonical live effects for the compact desktop overlay.

        This endpoint intentionally uses packet-defined intervals.  The only
        exception is an explicitly reviewed action-marker status whose real
        lifetime is reconstructed from its factual cast plus the shared
        activation rule; generic simulator duration corrections never replace
        the server clock shown by a live overlay.
        """
        # The overlay polls four times per second.  Use its compact state so a
        # long live session does not deep-copy the complete page snapshot on
        # every poll.  Older/test capture implementations can fall back to the
        # full status contract.
        self._ensure_overlay_response_cache()
        overlay_status = getattr(self.live_capture, "overlay_status", None)
        state = (
            overlay_status()
            if callable(overlay_status)
            else self.live_capture.status()
        )
        snapshot = state.get("overlaySnapshot") or state.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {}
        players = snapshot.get("players")
        buffs = snapshot.get("player_buffs")
        summons = snapshot.get("summons")
        player_health = snapshot.get("player_health")
        self_source_id = norm(snapshot.get("selfSourceId"))
        # Buff intervals use the capture-relative status clock. Combat elapsed
        # excludes idle sections and therefore cannot be used as "now" here;
        # mixing the two clocks made durations look longer or shorter in the
        # overlay and kept expired effects alive.
        authoritative_elapsed = max(
            0.0,
            as_number(snapshot.get("boss_effect_current_seconds"))
            or as_number(snapshot.get("elapsed_seconds")),
        )
        elapsed = authoritative_elapsed
        # Correct the packet clock to response time. Snapshots are published
        # every 0.75s, so using the unaged value starts every countdown late.
        published_at = as_number(
            state.get("overlaySnapshotPublishedMonotonic")
            or state.get("snapshotPublishedMonotonic")
        )
        # The capture clock belongs to the capture session, not to packet
        # arrival. Keep extrapolating while the session is starting as well
        # as while it is running; a quiet UDP stream must not freeze the
        # overlay's countdown.
        if state.get("state") in {"starting", "running"} and published_at > 0.0:
            elapsed += max(0.0, time.monotonic() - published_at)

        overlay_revision = int(state.get("overlaySnapshotRevision") or 0)
        cache_key = (overlay_revision, self_source_id)
        with self._overlay_response_cache_lock:
            cached = (
                self._overlay_response_cache
                if self._overlay_response_cache_key == cache_key
                else None
            )
        if cached is not None:
            return self._age_buff_overlay_response(
                cached,
                elapsed,
                state=norm(state.get("state")) or "idle",
                status=norm(state.get("status")),
                latency=dict(state.get("overlayLatency") or {}),
            )

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for source in buffs if isinstance(buffs, list) else []:
            if not isinstance(source, dict):
                continue
            if is_inferred_aura_row(source) or (is_aura_row(source) and not is_aura_damage_series(source)):
                continue
            skill_key = _display_skill_key(
                source.get("skill_name") or source.get("skill_id")
            )
            catalog = self._live_buff_catalog.get(skill_key)
            # The full live page can retain diagnostic statuses. The overlay is
            # deliberately limited to the reviewed display catalog so hit-side
            # packet variants cannot create unrelated rows.
            if not catalog or catalog.get("overlay") != "1":
                continue
            source = acceleration_overlay_row(source, skill_key, elapsed)
            if source is None:
                continue
            target_id = norm(source.get("target_id"))
            grouped.setdefault((target_id, skill_key), []).append(source)

        raw_overlay_buff_casts = snapshot.get("buff_casts")
        if not isinstance(raw_overlay_buff_casts, list):
            raw_overlay_buff_casts = snapshot.get("team_buff_casts")
        if not isinstance(raw_overlay_buff_casts, list):
            raw_overlay_buff_casts = []
        store = getattr(self, "store", None)
        activation_loader = getattr(
            store, "buff_activation_rule_for_job", None
        )
        # A few server states are only short action markers or permanent-looking
        # carrier states. Silver Hunter's observed 1 s 134800 state and
        # Scorching Flame's zero-duration carrier are examples. For explicitly
        # reviewed catalog rows, combine the factual cast timestamp with the
        # authoritative current effect duration.
        for cast in raw_overlay_buff_casts:
            if not isinstance(cast, dict):
                continue
            if is_aura_row(cast):
                continue  # A cast cannot prove the recipient entered its range.
            skill_key = _display_skill_key(
                cast.get("skill_name") or cast.get("skill_id")
            )
            catalog = self._live_buff_catalog.get(skill_key, {})
            duration_from_activation = bool(
                catalog.get("cast_duration_from_activation_rule")
            )
            duration_from_skill_table = bool(
                catalog.get("cast_duration_from_skill_table")
            )
            if not (duration_from_activation or duration_from_skill_table):
                continue
            source_id = norm(cast.get("source_id"))
            source_job = norm(cast.get("source_job")) or norm(cast.get("job"))
            skill_name = norm(cast.get("skill_name")) or norm(
                catalog.get("skill")
            )
            cast_seconds = as_number(cast.get("cast_seconds"))
            activation = (
                activation_loader(
                    source_job,
                    skill_name,
                    "团队buff"
                    if norm(catalog.get("scope")) == "team"
                    else "个人buff",
                )
                if callable(activation_loader)
                else {}
            )
            timing_source = "cast_and_activation_rule"
            duration = max(0.0, as_number(activation.get("duration")))
            if duration_from_skill_table:
                timing_loader = getattr(store, "skill_effect_timing", None)
                fallback_level = int(
                    as_number(catalog.get("skill_table_fallback_level"))
                )
                fallback_max_required_level = max(
                    70,
                    int(
                        as_number(
                            catalog.get("skill_table_fallback_max_required_level"),
                            70,
                        )
                    ),
                )
                timing = (
                    timing_loader(
                        source_job,
                        skill_name,
                        level=fallback_level or None,
                        max_required_level=fallback_max_required_level,
                    )
                    if callable(timing_loader)
                    else {}
                )
                duration = max(0.0, as_number(timing.get("duration")))
                timing_source = "current_skill_table_fallback"
            if not source_id or cast_seconds <= 0.0 or duration <= 0.0:
                continue
            # Skill-table reconstruction exists for newly added/marker-only
            # effects such as Scorching Flame. Once a real finite status packet
            # for this actor is present, it owns the visible lifetime. A short
            # action marker remains insufficient and keeps the reviewed table
            # duration as the fallback.
            if duration_from_skill_table:
                minimum_packet_duration = max(1.0, duration * 0.25)
                packet_rows = grouped.get((source_id, skill_key), [])
                has_trustworthy_packet_interval = any(
                    (
                        norm(row.get("timing_source")) in {"", "server_status", "packet"}
                        and any(
                            isinstance(interval, (list, tuple))
                            and len(interval) >= 2
                            and abs(as_number(interval[0]) - cast_seconds) <= 2.0
                            and as_number(interval[1]) - as_number(interval[0])
                            >= minimum_packet_duration
                            for interval in row.get("intervals", [])
                        )
                    )
                    for row in packet_rows
                )
                if has_trustworthy_packet_interval:
                    continue
            end_seconds = cast_seconds + duration
            grouped.setdefault((source_id, skill_key), []).append(
                {
                    "target_id": source_id,
                    "target_job": source_job,
                    "source_id": source_id,
                    "job": source_job,
                    "skill_name": skill_name,
                    "active": cast_seconds <= elapsed < end_seconds,
                    "last_refresh_seconds": cast_seconds,
                    "start_seconds": cast_seconds,
                    "end_seconds": end_seconds,
                    "intervals": [[cast_seconds, end_seconds]],
                    "open_ended": False,
                    "timing_source": timing_source,
                }
            )

        overlay_buffs: list[dict[str, Any]] = []
        buff_observations: list[dict[str, Any]] = []
        for (target_id, skill_key), rows in grouped.items():
            # One skill application can carry both its real finite Buff and
            # permanent-looking action/resource components under the same
            # skill id. The visible lifetime belongs to the finite status.
            if not is_aura_row(rows[0]):
                rows = self._prefer_finite_live_buff_rows(rows)
            intervals = _merge_display_intervals(
                [
                    [as_number(interval[0]), as_number(interval[1])]
                    for row in rows
                    for interval in row.get("intervals", [])
                    if isinstance(interval, (list, tuple)) and len(interval) >= 2
                ]
            )
            # Retain one tiny latest-receipt observation even after expiry.
            # Missing-Buff alerts need a proven recipient lifetime; absence
            # from the active display list alone is not evidence of removal.
            if intervals:
                buff_observations.append({
                    "target_id": target_id,
                    "catalog_key": skill_key,
                    "end_seconds": max(interval[1] for interval in intervals),
                    "permanent": any(bool(row.get("open_ended")) for row in rows),
                })
            active_intervals = [
                interval
                for interval in intervals
                if interval[0] <= elapsed + 0.05 and interval[1] > elapsed
            ]
            active_rows = [row for row in rows if bool(row.get("active"))]
            if not active_intervals and not active_rows:
                continue

            representative = max(
                rows,
                key=lambda row: as_number(row.get("last_refresh_seconds")),
            )
            catalog = self._live_buff_catalog[skill_key]
            skill_name = norm(representative.get("skill_name")) or norm(
                catalog.get("skill")
            )
            raw_start = (
                max(interval[0] for interval in active_intervals)
                if active_intervals
                else as_number(representative.get("start_seconds"))
            )
            raw_end = (
                max(interval[1] for interval in active_intervals)
                if active_intervals
                else as_number(representative.get("end_seconds"))
            )
            # ``active`` is an event-side hint. It can remain true when the
            # packet that closes the effect is absent (for example while no
            # UDP packets are arriving). A finite interval must therefore
            # remain finite even if that stale flag is still present; only an
            # open-ended row or a deliberately very long interval is
            # permanent.
            source_intervals = [
                [as_number(interval[0]), as_number(interval[1])]
                for row in rows
                for interval in row.get("intervals", [])
                if isinstance(interval, (list, tuple))
                and len(interval) >= 2
                and as_number(interval[1]) > as_number(interval[0]) + 1e-6
            ]
            source_intervals.extend(
                [
                    [
                        as_number(row.get("start_seconds")),
                        as_number(row.get("end_seconds")),
                    ]
                    for row in rows
                    if as_number(row.get("end_seconds"))
                    > as_number(row.get("start_seconds")) + 1e-6
                ]
            )
            has_finite_application = bool(source_intervals)
            # The aggregator clips an open-ended server status to
            # ``snapshot_now + 1ms`` before serializing it.  The overlay clock
            # is deliberately extrapolated by response age, so comparing that
            # clipped end directly with the extrapolated clock makes every
            # still-live permanent status disappear between polls.  Use the
            # explicit marker when available; keep the clock-proximity fallback
            # for older cached snapshots/builds.
            open_ended = any(bool(row.get("open_ended")) for row in rows)
            if not open_ended and active_rows:
                open_ended = (
                    authoritative_elapsed - 0.05
                    <= raw_end
                    <= authoritative_elapsed + 0.25
                )
            last_refresh = max(
                (as_number(row.get("last_refresh_seconds")) for row in rows),
                default=0.0,
            )
            permanent = (
                open_ended
            ) or raw_end - elapsed > 3600
            if permanent:
                start = raw_start
                end = 0.0
                duration = 0.0
            else:
                start = raw_start
                end = raw_end
                # A refreshable status can be one long merged interval.  The
                # visible bar represents the current server application, so it
                # starts at the final refresh without changing the real expiry.
                if (
                    not (
                        catalog.get("cast_duration_from_activation_rule")
                        or catalog.get("cast_duration_from_skill_table")
                    )
                    and raw_start <= last_refresh < raw_end
                ):
                    start = last_refresh
                duration = max(0.1, end - start)
                if end <= elapsed:
                    continue

            timing_representative = max(
                rows,
                key=lambda row: (
                    any(
                        isinstance(interval, (list, tuple))
                        and len(interval) >= 2
                        and as_number(interval[0]) <= elapsed < as_number(interval[1])
                        for interval in row.get("intervals", [])
                    ),
                    max(
                        (
                            as_number(interval[1])
                            for interval in row.get("intervals", [])
                            if isinstance(interval, (list, tuple)) and len(interval) >= 2
                        ),
                        default=as_number(row.get("end_seconds")),
                    ),
                    as_number(row.get("last_refresh_seconds")),
                ),
            )
            timing_source = (
                norm(timing_representative.get("timing_source"))
                or "server_status"
            )

            overlay_buffs.append(
                {
                    "key": f"{target_id}|{skill_key}",
                    "catalog_key": skill_key,
                    "skill_name": skill_name,
                    "label": norm(catalog.get("display")),
                    "status_kind": norm(catalog.get("category")),
                    "display_order": int(catalog.get("display_order") or 0),
                    "provider_order": int(catalog.get("provider_order") or 0),
                    "icon_index": int(catalog.get("icon_index", -1)),
                    "target_id": target_id,
                    "target_job": norm(representative.get("target_job")),
                    # Same-name effects are collapsed to one visible row.  Its
                    # source must still be the actor that supplied the active
                    # application, not a static list of every profession that
                    # can own the skill or every source seen earlier.
                    "source_id": norm(representative.get("source_id"))
                    or self._joined_live_buff_field(rows, "source_id"),
                    "job": norm(representative.get("job"))
                    or self._joined_live_buff_field(rows, "job"),
                    "start_seconds": start,
                    "end_seconds": end,
                    "duration_seconds": duration,
                    "remaining_seconds": 0.0 if permanent else max(0.0, end - elapsed),
                    "permanent": permanent,
                    "timing_source": timing_source,
                    "data_source": (
                        "抓包数据"
                        if timing_source in {"server_status", "packet"}
                        else "预设数据兜底"
                    ),
                }
            )
        overlay_buffs.sort(
            key=lambda row: (
                norm(row.get("target_id")),
                bool(row.get("permanent")),
                as_number(row.get("end_seconds")),
                norm(row.get("skill_name")),
            )
        )
        player_rows: dict[str, dict[str, Any]] = {}
        for row in (players if isinstance(players, list) else []):
            if not isinstance(row, dict):
                continue
            source_id = norm(row.get("source_id"))
            if not source_id or (
                source_id != self_source_id
                and not _is_recognized_player_job(norm(row.get("job")))
            ):
                continue
            player_rows[source_id] = dict(row)
        # Full snapshots omit teammates with no combat totals. Only independent
        # party/action evidence may restore those rows; cached Buff identities
        # alone must never create missing-Buff alert recipients.
        for row in snapshot.get("party_members", []):
            if not isinstance(row, dict):
                continue
            source_id = norm(row.get("actor_id"))
            if (
                not source_id
                or as_number(row.get("confidence")) < 0.65
                or not _is_recognized_player_job(norm(row.get("job")))
            ):
                continue
            existing = player_rows.setdefault(source_id, {"source_id": source_id})
            for field in ("job", "name"):
                if not norm(existing.get(field)):
                    existing[field] = norm(row.get(field))
        if self_source_id:
            player_rows.setdefault(self_source_id, {"source_id": self_source_id})
        for row in (buffs if isinstance(buffs, list) else []):
            if not isinstance(row, dict):
                continue
            target_id = norm(row.get("target_id"))
            if target_id not in player_rows:
                continue
            existing = player_rows[target_id]
            if not norm(existing.get("job")):
                existing["job"] = norm(row.get("target_job"))
            if not norm(existing.get("name")):
                existing["name"] = norm(row.get("target_name"))

        # Keep unrelated cached targets out of the frontend's observed team
        # Buff catalog too, not just its recipient list. The raw ledger stays
        # intact for replay diagnostics and later identity resolution.
        overlay_buffs = [
            row for row in overlay_buffs
            if norm(row.get("target_id")) in player_rows
        ]
        buff_observations = [
            row for row in buff_observations
            if norm(row.get("target_id")) in player_rows
        ]

        overlay_players: list[dict[str, Any]] = []
        for source_id, row in player_rows.items():
            job = norm(row.get("job"))
            profile = self._overlay_job_profile(job)
            overlay_players.append(
                {
                    "source_id": source_id,
                    "job": job,
                    "name": norm(row.get("name")),
                    "damage": int(as_number(row.get("damage"))),
                    "dps": as_number(row.get("dps")),
                    "damage_series": row.get("damage_series", []),
                    **profile,
                }
            )
        overlay_players.sort(
            key=lambda row: (
                0 if norm(row.get("source_id")) == self_source_id else 1,
                norm(row.get("job")),
                norm(row.get("source_id")),
            )
        )

        overlay_summons: list[dict[str, Any]] = []
        for source in summons if isinstance(summons, list) else []:
            if not isinstance(source, dict):
                continue
            catalog_key = norm(source.get("catalog_key"))
            source_id = norm(source.get("source_id"))
            end_seconds = as_number(source.get("end_seconds"))
            if not catalog_key or not source_id or end_seconds <= elapsed:
                continue
            player = player_rows.get(source_id, {})
            overlay_summons.append(
                {
                    "key": norm(source.get("key"))
                    or f"{source_id}|{catalog_key}|{end_seconds:.3f}",
                    "catalog_key": catalog_key,
                    "skill_id": norm(source.get("skill_id")),
                    "skill_name": norm(source.get("skill_name")) or catalog_key,
                    "source_id": source_id,
                    "source_name": norm(source.get("source_name"))
                    or norm(player.get("name")),
                    "source_job": norm(source.get("source_job"))
                    or norm(player.get("job")),
                    "start_seconds": as_number(source.get("start_seconds")),
                    "end_seconds": end_seconds,
                    "duration_seconds": max(
                        0.1, as_number(source.get("duration_seconds"))
                    ),
                    "remaining_seconds": max(0.0, end_seconds - elapsed),
                    "detection_source": norm(source.get("detection_source"))
                    or "skill_cast",
                }
            )
        overlay_summons.sort(
            key=lambda row: (
                norm(row.get("source_job")),
                norm(row.get("source_id")),
                norm(row.get("skill_name")),
                as_number(row.get("end_seconds")),
            )
        )

        boss_effects = snapshot.get("boss_runtime_effects")
        if not isinstance(boss_effects, list):
            boss_effects = snapshot.get("boss_effects")
        boss_targets = snapshot.get("boss_runtime_targets")
        if not isinstance(boss_targets, list):
            boss_targets = snapshot.get("damage_targets")
        ended_boss_ids = set(snapshot.get("endedBossIds") or [])
        if isinstance(boss_effects, list):
            boss_effects = [row for row in boss_effects
                            if norm(row.get("target_id")) not in ended_boss_ids]
        if isinstance(boss_targets, list):
            boss_targets = [row for row in boss_targets
                            if norm(row.get("target_id")) not in ended_boss_ids]
        overlay_debuffs = self._overlay_debuff_rows(
            boss_effects if isinstance(boss_effects, list) else [],
            elapsed,
            boss_targets if isinstance(boss_targets, list) else None,
        )
        icon_indices = getattr(self, "_live_skill_icon_indices", {})
        for debuff in overlay_debuffs:
            for contributor in debuff.get("contributors", []):
                if not isinstance(contributor, dict):
                    continue
                contributor["icon_index"] = int(
                    icon_indices.get(
                        norm(contributor.get("skill_id")),
                        icon_indices.get(
                            _display_skill_key(contributor.get("skill_name")),
                            -1,
                        ),
                    )
                )
        buff_options = sorted(
            (
                {
                    "key": key,
                    "skill": norm(row.get("skill")),
                    "label": norm(row.get("display")),
                    "category": norm(row.get("category")),
                    "scope": norm(row.get("scope")) or "personal",
                    "fullDurationTeam": bool(row.get("full_duration_team")),
                    "provider": norm(row.get("provider")),
                    "displayOrder": int(row.get("display_order") or 0),
                    "providerOrder": int(row.get("provider_order") or 0),
                    "iconIndex": int(row.get("icon_index", -1)),
                    "cooldownTrackable": bool(
                        row.get("cooldown_trackable")
                    ),
                }
                for key, row in self._live_buff_catalog.items()
                if row.get("overlay") == "1"
            ),
            key=lambda row: (
                row["providerOrder"],
                row["displayOrder"],
                row["skill"],
            ),
        )
        buff_clear_revision = int(
            as_number(snapshot.get("player_buff_clear_revision"))
        )
        overlay_buff_clear_events: list[dict[str, Any]] = []
        raw_buff_clear_events = snapshot.get("player_buff_clear_events")
        for source in (
            raw_buff_clear_events
            if isinstance(raw_buff_clear_events, list)
            else []
        ):
            if not isinstance(source, dict):
                continue
            targets: dict[str, dict[str, str]] = {}
            for closed in source.get("closed_buffs", []):
                if not isinstance(closed, dict):
                    continue
                skill_key = _display_skill_key(
                    closed.get("skill_name") or closed.get("skill_id")
                )
                catalog = self._live_buff_catalog.get(skill_key)
                # Match the visible overlay contract: an explicit Boss clear
                # only raises a notification when it actually removed at
                # least one reviewed, selectable Buff from that target.
                if not catalog or catalog.get("overlay") != "1":
                    continue
                target_id = norm(closed.get("target_id"))
                if not target_id:
                    continue
                player = player_rows.get(target_id, {})
                targets[target_id] = {
                    "target_id": target_id,
                    "target_name": norm(closed.get("target_name"))
                    or norm(player.get("name")),
                    "target_job": norm(closed.get("target_job"))
                    or norm(player.get("job")),
                }
            if not targets:
                continue
            revision = int(as_number(source.get("revision")))
            timestamp_seconds = as_number(source.get("timestamp_seconds"))
            overlay_buff_clear_events.append(
                {
                    "key": f"{revision}:{timestamp_seconds:.3f}",
                    "revision": revision,
                    "timestamp_seconds": timestamp_seconds,
                    "source_id": norm(source.get("source_id")),
                    "source_name": norm(source.get("source_name")),
                    "skill_id": norm(source.get("skill_id")),
                    "skill_name": norm(source.get("skill_name")),
                    "targets": list(targets.values()),
                }
            )
        cooldown_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        cooldown_refunds: dict[tuple[str, str], dict[str, Any]] = {}
        raw_cooldown_refunds = snapshot.get("cooldown_refunds")
        for raw_refund in (
            raw_cooldown_refunds
            if isinstance(raw_cooldown_refunds, list)
            else []
        ):
            if not isinstance(raw_refund, dict):
                continue
            refund_source_id = norm(raw_refund.get("source_id"))
            refund_skill_key = _display_skill_key(
                raw_refund.get("target_skill_name")
                or raw_refund.get("target_skill_id")
            )
            if refund_source_id and refund_skill_key:
                cooldown_refunds[(refund_source_id, refund_skill_key)] = dict(
                    raw_refund
                )

        def remember_cooldown(
            *,
            source_id: str,
            source_job: str,
            skill_key: str,
            skill_name: str,
            cast_seconds: float,
        ) -> bool:
            effect_providers = self._live_buff_catalog.get(skill_key, {}).get(
                "effect_providers", []
            )
            if effect_providers and source_job not in effect_providers:
                # This is effect/CD ownership, not the recipient's profession.
                # Keep factual received shield statuses visible to every job.
                return False
            store = getattr(self, "store", None)
            details_loader = getattr(
                store, "skill_reference_cooldown_details", None
            )
            if callable(details_loader):
                cooldown_details = details_loader(
                    source_job, skill_name, 0.0
                )
            else:
                fallback_cooldown = (
                    store.skill_reference_cooldown(
                        source_job, skill_name, 0.0
                    )
                    if store is not None
                    else 0.0
                )
                cooldown_details = {
                    "cooldown": fallback_cooldown,
                    "source": "reference",
                    "source_label": "技能CD数据",
                }
            base_cooldown = max(
                0.0, as_number(cooldown_details.get("cooldown"))
            )
            cooldown_multiplier = 1.0
            mechanic_multipliers = JOB_MECHANICS.job_value(
                source_job, "team_effect_cooldown_multipliers", {}
            )
            if isinstance(mechanic_multipliers, dict):
                cooldown_multiplier = max(
                    0.0,
                    as_number(
                        mechanic_multipliers.get(skill_name), 1.0
                    ),
                )
            cooldown = base_cooldown * cooldown_multiplier
            glyph_percent = _OVERLAY_DEFAULT_COOLDOWN_GLYPH_PERCENT.get(
                skill_name, 0.0
            )
            if glyph_percent > 0.0:
                cooldown *= 1.0 - glyph_percent / 100.0
            if not source_id or cooldown <= 0.0 or cast_seconds <= 0.0:
                return False
            cooldown_key = (source_id, skill_key)
            refund = cooldown_refunds.get(cooldown_key, {})
            refund_cycle = as_number(
                refund.get("cycle_cast_seconds"), -1.0
            )
            refund_seconds = (
                max(0.0, as_number(refund.get("refund_seconds")))
                if abs(refund_cycle - cast_seconds) <= 0.001
                else 0.0
            )
            refund_seconds = min(cooldown, refund_seconds)
            ready_seconds = cast_seconds + cooldown - refund_seconds
            candidate = {
                "key": f"{source_id}|{skill_key}",
                "catalog_key": skill_key,
                "skill_name": skill_name,
                "source_id": source_id,
                "source_job": source_job,
                "cast_seconds": cast_seconds,
                "base_cooldown_seconds": base_cooldown,
                "cooldown_seconds": cooldown,
                "cooldown_source": norm(cooldown_details.get("source")),
                "cooldown_source_label": norm(
                    cooldown_details.get("source_label")
                ),
                "intrinsic_cooldown_multiplier": cooldown_multiplier,
                "cooldown_glyph_percent": glyph_percent,
                "cooldown_refund_seconds": refund_seconds,
                "cooldown_refund_events": int(
                    as_number(refund.get("event_count"))
                ),
                "cooldown_refund_evidence": norm(refund.get("evidence")),
                "ready_seconds": ready_seconds,
                "remaining_seconds": max(
                    0.0, ready_seconds - elapsed
                ),
            }
            previous = cooldown_by_key.get(cooldown_key)
            if previous is None or cast_seconds > as_number(
                previous.get("cast_seconds")
            ):
                cooldown_by_key[cooldown_key] = candidate
            return True

        # A real cast is authoritative for Key Buff CD ownership. Several team
        # buffs fan out one status per recipient and some packet layouts put the
        # recipient in the status source field; deriving cooldown ownership
        # from those mirrors produced one row for every teammate. The compact
        # aggregator keeps only the latest cast per actor/skill, so this remains
        # bounded and does not scan the battle timeline.
        cast_backed_actor_skill_keys: set[tuple[str, str]] = set()
        cast_backed_team_skill_keys: set[str] = set()
        for cast in raw_overlay_buff_casts:
            if not isinstance(cast, dict):
                continue
            skill_key = _display_skill_key(
                cast.get("skill_name") or cast.get("skill_id")
            )
            catalog = self._live_buff_catalog.get(skill_key, {})
            if catalog.get("overlay") != "1":
                continue
            source_id = norm(cast.get("source_id"))
            source_job = norm(cast.get("source_job")) or norm(cast.get("job"))
            skill_name = norm(cast.get("skill_name")) or norm(
                catalog.get("skill")
            )
            if remember_cooldown(
                source_id=source_id,
                source_job=source_job,
                skill_key=skill_key,
                skill_name=skill_name,
                cast_seconds=as_number(cast.get("cast_seconds")),
            ):
                cast_backed_actor_skill_keys.add((source_id, skill_key))
                if norm(catalog.get("scope")) == "team":
                    cast_backed_team_skill_keys.add(skill_key)

        for (target_id, skill_key), rows in grouped.items():
            catalog = self._live_buff_catalog.get(skill_key, {})
            if catalog.get("overlay") != "1":
                continue
            if (
                norm(catalog.get("scope")) == "team"
                and skill_key in cast_backed_team_skill_keys
            ):
                continue
            representative = max(
                rows,
                key=lambda row: as_number(row.get("last_refresh_seconds")),
            )
            source_id = norm(representative.get("source_id"))
            if (source_id, skill_key) in cast_backed_actor_skill_keys:
                continue
            source_job = norm(representative.get("job"))
            skill_name = norm(representative.get("skill_name")) or norm(
                catalog.get("skill")
            )
            cast_seconds = max(
                [
                    as_number(interval[0])
                    for row in rows
                    for interval in row.get("intervals", [])
                    if isinstance(interval, (list, tuple)) and len(interval) >= 2
                ]
                + [as_number(row.get("last_refresh_seconds")) for row in rows],
                default=as_number(representative.get("start_seconds")),
            )
            remember_cooldown(
                source_id=source_id,
                source_job=source_job,
                skill_key=skill_key,
                skill_name=skill_name,
                cast_seconds=cast_seconds,
            )
        cooldown_rows = list(cooldown_by_key.values())
        cooldown_rows.sort(
            key=lambda row: (
                as_number(row.get("remaining_seconds")),
                norm(row.get("source_job")),
                norm(row.get("skill_name")),
            )
        )
        response = {
            "state": state.get("state", "idle"),
            "status": state.get("status", ""),
            "elapsedSeconds": elapsed,
            "overlayRevision": overlay_revision,
            "buffStateRevision": int(
                snapshot.get("player_buff_state_revision") or 0
            ),
            "latency": dict(state.get("overlayLatency") or {}),
            "selfSourceId": self_source_id,
            "bossHealth": party_boss_health(snapshot, set(player_rows)),
            "endedBossIds": sorted(ended_boss_ids),
            "selfAttackTarget": (snapshot.get("lastAttackTargets") or {}).get(self_source_id),
            "players": overlay_players,
            "buffs": overlay_buffs,
            "buffObservations": buff_observations,
            "debuffs": overlay_debuffs,
            "cooldowns": cooldown_rows,
            "summons": overlay_summons,
            "playerHealth": [
                row
                for row in (player_health if isinstance(player_health, list) else [])
                if isinstance(row, dict)
            ],
            "buffClearRevision": buff_clear_revision,
            "buffClearEvents": overlay_buff_clear_events,
            "buffOptions": buff_options,
            "summonOptions": summon_overlay_options(),
        }
        with self._overlay_response_cache_lock:
            self._overlay_response_cache_key = cache_key
            self._overlay_response_cache = response
            self._overlay_json_cache_key = None
            self._overlay_json_cache = b""
        return copy.deepcopy(response)
    @staticmethod
    def _age_buff_overlay_response(
        cached: dict[str, Any],
        elapsed: float,
        *,
        state: str,
        status: str,
        latency: dict[str, Any],
    ) -> dict[str, Any]:
        """Age a cached compact response without rebuilding effect groups."""

        result = copy.deepcopy(cached)
        result["state"] = state
        result["status"] = status
        result["elapsedSeconds"] = elapsed
        result["latency"] = latency
        aged_buffs: list[dict[str, Any]] = []
        for row in result.get("buffs", []):
            end = as_number(row.get("end_seconds"))
            if not row.get("permanent") and end > 0.0 and end <= elapsed:
                continue
            row["remaining_seconds"] = (
                0.0 if row.get("permanent") else max(0.0, end - elapsed)
            )
            aged_buffs.append(row)
        result["buffs"] = aged_buffs
        aged_debuffs: list[dict[str, Any]] = []
        for row in result.get("debuffs", []):
            end = as_number(row.get("end_seconds"))
            if end > 0.0 and end <= elapsed:
                continue
            row["remaining_seconds"] = max(0.0, end - elapsed) if end else 0.0
            aged_contributors: list[dict[str, Any]] = []
            for contributor in row.get("contributors", []):
                contributor_end = as_number(contributor.get("end_seconds"))
                if contributor_end > 0.0 and contributor_end <= elapsed:
                    continue
                contributor["remaining_seconds"] = (
                    max(0.0, contributor_end - elapsed)
                    if contributor_end
                    else 0.0
                )
                aged_contributors.append(contributor)
            row["contributors"] = aged_contributors
            aged_debuffs.append(row)
        result["debuffs"] = aged_debuffs
        for row in result.get("cooldowns", []):
            row["remaining_seconds"] = max(
                0.0, as_number(row.get("ready_seconds")) - elapsed
            )
        aged_summons: list[dict[str, Any]] = []
        for row in result.get("summons", []):
            end = as_number(row.get("end_seconds"))
            if end <= elapsed:
                continue
            row["remaining_seconds"] = max(0.0, end - elapsed)
            aged_summons.append(row)
        result["summons"] = aged_summons
        return result
    def buff_overlay_status_json(self, include_catalog: bool = True) -> bytes:
        """Return compact JSON, optionally omitting immutable option lists.

        The main publisher requests the catalogue once and stores it for every
        on-demand WebView. Subsequent 500 ms snapshots no longer serialize or
        transfer the same Buff/summon option tables.
        """

        self._ensure_overlay_response_cache()
        payload = self.buff_overlay_status()
        if not include_catalog:
            payload.pop("buffOptions", None)
            payload.pop("summonOptions", None)
        cache_key = (
            int(payload.get("overlayRevision") or 0),
            int(as_number(payload.get("elapsedSeconds")) * 4.0),
            norm(payload.get("state")),
            bool(include_catalog),
        )
        with self._overlay_response_cache_lock:
            if self._overlay_json_cache_key == cache_key:
                return self._overlay_json_cache
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        with self._overlay_response_cache_lock:
            self._overlay_json_cache_key = cache_key
            self._overlay_json_cache = encoded
        return encoded
    def _ensure_overlay_response_cache(self) -> None:
        """Initialize cache fields for legacy embedded/test core instances."""

        if hasattr(self, "_overlay_response_cache_lock"):
            return
        self._overlay_response_cache_lock = threading.RLock()
        self._overlay_response_cache_key = None
        self._overlay_response_cache = None
        self._overlay_json_cache_key = None
        self._overlay_json_cache = b""
    @staticmethod
    def _teammate_profile_projection_identity(
        profile: dict[str, Any],
    ) -> str:
        fields = profile.get("fields", ())
        values = {
            norm(row[0]): norm(row[1])
            for row in fields
            if isinstance(row, (list, tuple))
            and len(row) >= 2
            and norm(row[0])
        }
        profile_id = next((
            values.get(key, "")
            for key in ("角色档案标识", "稳定档案ID", "profile_id")
            if values.get(key, "")
        ), "")
        return (
            profile_id.upper()
            or norm(profile.get("target_id")).upper()
            or norm(profile.get("inspection_key"))
        )

    def _teammate_profile_projection_key(
        self, profile: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        try:
            build_context = self.get_build("build1")
        except (AttributeError, KeyError):
            # Lightweight embedded/test hosts may expose profile projection
            # without the persisted build store used by the packaged app.
            build_context = {}
        signature_payload = {
            "identity": self._teammate_profile_projection_identity(profile),
            "target_name": profile.get("target_name"),
            "job": profile.get("job"),
            "timestamp_ms": profile.get("timestamp_ms"),
            "inspection_key": profile.get("inspection_key"),
            "request_fingerprint": profile.get("request_fingerprint"),
            "response_layout": profile.get("response_layout"),
            "fields": profile.get("fields"),
            "equipment": profile.get("equipment"),
            "cards": profile.get("cards"),
            "stats": profile.get("stats"),
            "build": build_context,
        }
        cache_key = hashlib.sha256(json.dumps(
            signature_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")).hexdigest()
        return cache_key, build_context

    def _teammate_profile_projection_cached(
        self, profile: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Read one completed panel without waiting for its calculator."""

        cache_key, _build_context = self._teammate_profile_projection_key(profile)
        lock = getattr(self, "_teammate_profile_projection_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._teammate_profile_projection_lock = lock
        with lock:
            cache = getattr(self, "_teammate_profile_projection_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._teammate_profile_projection_cache = cache
            cached = cache.get(cache_key)
            if cached is not None:
                return copy.deepcopy(cached[0]), copy.deepcopy(cached[1])
        return None

    def _teammate_profile_projection(
        self, profile: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Calculate one profile outside the latency-sensitive cache lock."""

        cache_key, _build_context = self._teammate_profile_projection_key(profile)
        cached = self._teammate_profile_projection_cached(profile)
        if cached is not None:
            return cached

        # Full equipment mapping can take more than a second. Never hold the
        # projection cache lock while it runs: the 200 ms overlay endpoint and
        # the normal live-page status request must remain non-blocking.
        enriched = self._with_profile_panels({
            "player_profiles": [profile],
            "party_members": [],
        })
        profiles = enriched.get("player_profiles", [])
        resolved = (
            profiles[0]
            if isinstance(profiles, list) and profiles
            else copy.deepcopy(profile)
        )
        target_id = norm(resolved.get("target_id"))
        panel = enriched.get("profile_panels", {}).get(target_id, {})
        if not isinstance(panel, dict):
            panel = {}
        lock = getattr(self, "_teammate_profile_projection_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._teammate_profile_projection_lock = lock
        with lock:
            cache = getattr(self, "_teammate_profile_projection_cache", None)
            if not isinstance(cache, dict):
                cache = {}
                self._teammate_profile_projection_cache = cache
            # Viewing equipment is transient. Keep only the current projected
            # panel so long-running clients cannot accumulate complete decoded
            # equipment bodies in this secondary cache.
            cache.clear()
            cache[cache_key] = (copy.deepcopy(resolved), copy.deepcopy(panel))
            return copy.deepcopy(resolved), copy.deepcopy(panel)

    @staticmethod
    def _teammate_identity_result(
        profile: dict[str, Any],
        revision: int,
        *,
        body_decoded: bool,
        message: str,
        panel_state: str,
    ) -> dict[str, Any]:
        target_id = norm(profile.get("target_id"))
        profile_fields = {
            norm(row[0]): norm(row[1])
            for row in profile.get("fields", [])
            if isinstance(row, (list, tuple))
            and len(row) >= 2
            and norm(row[0])
        }
        profile_id = next((
            profile_fields.get(key, "")
            for key in ("角色档案标识", "稳定档案ID", "profile_id")
            if profile_fields.get(key, "")
        ), "")
        if not profile_id and target_id.casefold().startswith("profile:"):
            profile_id = target_id.split(":", 1)[1]
        display_name = norm(profile.get("target_name"))
        if re.fullmatch(r"(?:角色\s*)?0x[0-9A-Fa-f]+", display_name):
            display_name = ""
        inspection_key = (
            norm(profile.get("inspection_key")) or f"revision:{revision}"
        )
        packet_epoch_match = re.search(r":(\d{11,})$", inspection_key)
        return {
            "state": "unavailable",
            "revision": revision,
            "presentationRevision": max(0, revision) * 2,
            "inspectionKey": inspection_key,
            "name": display_name,
            "job": norm(profile.get("job")),
            "gameId": profile_id or target_id,
            "profileId": profile_id,
            "identityResolved": bool(display_name),
            "historyReady": bool(display_name or profile_id),
            "bodyDecoded": bool(body_decoded),
            "panelState": panel_state,
            "message": message,
            "packetRequestEpochMs": (
                int(packet_epoch_match.group(1)) if packet_epoch_match else 0
            ),
            "decodedPublishedEpochMs": int(
                as_number(profile.get("decoded_published_epoch_ms"))
            ),
            "statusPublishedEpochMs": round(time.time() * 1000),
        }

    def _calculate_teammate_info_result(
        self,
        profile: dict[str, Any],
        revision: int,
    ) -> dict[str, Any]:
        resolved, panel = self._teammate_profile_projection(profile)
        fallback = self._teammate_identity_result(
            resolved,
            revision,
            body_decoded=True,
            message=(
                norm(panel.get("message"))
                if isinstance(panel, dict)
                else ""
            ) or "装备正文已解密，但总面板暂时无法计算",
            panel_state="error",
        )
        if not isinstance(panel, dict) or panel.get("state") != "ready":
            fallback["presentationRevision"] = max(0, revision) * 2 + 1
            return fallback

        panel_rows = {
            norm(row.get("key")): row
            for row in panel.get("rows", [])
            if isinstance(row, dict) and norm(row.get("key"))
        }

        def metric(key: str) -> float:
            row = panel_rows.get(key, {})
            return as_number(row.get("estimated", row.get("confirmed")))

        job = norm(resolved.get("job"))
        effective = next(
            (
                row
                for row in self.store.tables.get("effective", [])
                if norm(row.get("职业")) == job
            ),
            {},
        )
        element_candidates: list[tuple[float, int, str]] = []
        for index in range(1, 4):
            element = norm(effective.get(f"属性{index}"))
            if element and element != NONE_TEXT:
                element_candidates.append(
                    (as_number(effective.get(f"权重{index}")), -index, element)
                )
        primary_element = max(element_candidates)[2] if element_candidates else "无"
        element_key = {
            "火": "fire_attack",
            "水": "water_attack",
            "光": "light_attack",
            "暗": "dark_attack",
        }.get(primary_element, "")
        result = {
            **fallback,
            "state": "ready",
            "presentationRevision": max(0, revision) * 2 + 1,
            "panelState": "ready",
            "physicalAttackMin": metric("physical_attack_min"),
            "physicalAttackMax": metric("physical_attack_max"),
            "magicAttackMin": metric("magic_attack_min"),
            "magicAttackMax": metric("magic_attack_max"),
            "critPercent": metric("crit_percent"),
            "critDamagePercent": metric("crit_damage_percent"),
            "finalPercent": metric("final_percent"),
            "element": primary_element,
            "elementAttackPercent": metric(element_key) if element_key else 0.0,
        }
        return result

    def _ensure_teammate_projection_worker(self) -> threading.RLock:
        lock = getattr(self, "_teammate_projection_worker_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._teammate_projection_worker_lock = lock
        with lock:
            thread = getattr(self, "_teammate_projection_worker", None)
            if thread is None or not thread.is_alive():
                self._teammate_projection_pending = None
                self._teammate_projection_active_token = ""
                self._teammate_projection_wake = threading.Event()
                self._teammate_projection_stop = threading.Event()
                thread = threading.Thread(
                    target=self._teammate_projection_loop,
                    name="V410TeammatePanelProjection",
                    daemon=True,
                )
                self._teammate_projection_worker = thread
                thread.start()
        return lock

    def _schedule_teammate_projection(
        self,
        profile: dict[str, Any],
        revision: int,
    ) -> None:
        lock = self._ensure_teammate_projection_worker()
        inspection_key = norm(profile.get("inspection_key"))
        token = f"{int(revision)}:{inspection_key}:{norm(profile.get('response_layout'))}"
        with lock:
            pending = getattr(self, "_teammate_projection_pending", None)
            if (
                token == getattr(self, "_teammate_projection_active_token", "")
                or isinstance(pending, tuple) and pending[0] == token
            ):
                return
            # Only the latest click matters to the two on-demand windows. A
            # single replacement slot prevents ten rapid inspections from
            # becoming ten seconds of obsolete panel work.
            self._teammate_projection_pending = (
                token,
                int(revision),
                copy.deepcopy(profile),
            )
            self._teammate_projection_wake.set()

    def _teammate_projection_loop(self) -> None:
        set_current_thread_low_priority()
        while not self._teammate_projection_stop.is_set():
            self._teammate_projection_wake.wait(0.5)
            self._teammate_projection_wake.clear()
            while not self._teammate_projection_stop.is_set():
                lock = self._teammate_projection_worker_lock
                with lock:
                    pending = self._teammate_projection_pending
                    self._teammate_projection_pending = None
                    if pending is None:
                        self._teammate_projection_active_token = ""
                        break
                    token, revision, profile = pending
                    self._teammate_projection_active_token = token
                started = time.perf_counter()
                try:
                    result = self._calculate_teammate_info_result(
                        profile, revision
                    )
                except Exception as exc:
                    result = self._teammate_identity_result(
                        profile,
                        revision,
                        body_decoded=True,
                        message=f"总面板后台计算失败：{type(exc).__name__}: {exc}",
                        panel_state="error",
                    )
                    result["presentationRevision"] = max(0, revision) * 2 + 1
                result["projectionMs"] = round(
                    (time.perf_counter() - started) * 1000.0, 3
                )
                with lock:
                    self._teammate_projection_active_token = ""
                    current_revision = int(as_number(getattr(
                        self, "_teammate_info_cache_revision", -1
                    )))
                    current = getattr(self, "_teammate_info_cache", None)
                    current_key = (
                        norm(current.get("inspectionKey"))
                        if isinstance(current, dict)
                        else ""
                    )
                    result_key = norm(result.get("inspectionKey"))
                    if current_revision == revision and current_key == result_key:
                        self._teammate_info_cache = copy.deepcopy(result)
                    # Force the normal live page to pick up the now-cached
                    # compact panel without rerunning the calculator.
                    self._teammate_profiles_cache_revision = -1
                if self._teammate_projection_pending is not None:
                    self._teammate_projection_wake.set()

    def _shutdown_teammate_projection_worker(self) -> None:
        stop = getattr(self, "_teammate_projection_stop", None)
        wake = getattr(self, "_teammate_projection_wake", None)
        thread = getattr(self, "_teammate_projection_worker", None)
        if stop is not None:
            stop.set()
        if wake is not None:
            wake.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def teammate_info_status(self) -> dict[str, Any]:
        """Return the latest inspected teammate as one compact panel.

        The raw profile remains in the Python process.  The webview receives
        only the six user-requested panel values, which also keeps polling
        inexpensive and avoids exposing the equipment archive to this overlay.
        """

        projection_lock = self._ensure_teammate_projection_worker()
        with projection_lock:
            cached_revision = int(
                as_number(getattr(self, "_teammate_info_cache_revision", -1))
            )
            cached = copy.deepcopy(getattr(self, "_teammate_info_cache", None))
        provider = self.live_capture.teammate_info_status
        try:
            status = provider(
                cached_revision if isinstance(cached, dict) else None
            )
        except TypeError:
            status = provider()
        revision = int(as_number(status.get("revision")))
        if revision == cached_revision and isinstance(cached, dict):
            return cached
        profile = status.get("profile")
        if revision <= 0 or not isinstance(profile, dict):
            result = {
                "state": "listening",
                "revision": revision,
                "presentationRevision": max(0, revision) * 2,
            }
            with projection_lock:
                self._teammate_info_cache_revision = revision
                self._teammate_info_cache = copy.deepcopy(result)
            return result

        inspection_key = norm(profile.get("inspection_key")) or f"revision:{revision}"

        # A verified 03 15 request is published before the large equipment
        # response is decoded.  Do not run the full build calculator for that
        # identity-only shell: history can use the packet name directly or a
        # recent, unique profile alias. This makes the two on-demand WebViews appear immediately,
        # while a later revision fills in equipment-derived panel metrics.
        raw_equipment = profile.get("equipment")
        response_layout = norm(profile.get("response_layout"))
        if (
            response_layout == "TCP:inspect_request:pending"
            or not isinstance(raw_equipment, (list, tuple))
            or not raw_equipment
        ):
            result = self._teammate_identity_result(
                profile,
                revision,
                body_decoded=False,
                panel_state="waiting_body",
                message=(
                    "已收到查看信息请求，装备正文解析中"
                    if response_layout == "TCP:inspect_request:pending"
                    else "查看信息正文暂时无法解密，仍可按档案 ID 查询既往评分"
                ),
            )
            with projection_lock:
                self._teammate_info_cache_revision = revision
                self._teammate_info_cache = copy.deepcopy(result)
            return result

        # The encrypted body is already available. Publish that fact now and
        # calculate the simulator panel on one coalescing background worker.
        result = self._teammate_identity_result(
            profile,
            revision,
            body_decoded=True,
            panel_state="calculating",
            message="装备正文已收到，正在后台计算模拟面板",
        )
        with projection_lock:
            self._teammate_info_cache_revision = revision
            self._teammate_info_cache = copy.deepcopy(result)
        self._schedule_teammate_projection(profile, revision)
        return result
    def _teammate_profiles_payload(self) -> dict[str, Any]:
        """Project disk-free teammate inspections into the normal UI model."""

        provider = getattr(self.live_capture, "teammate_profiles_status", None)
        if not callable(provider):
            return {
                "revision": 0,
                "player_profiles": [],
                "profile_panels": {},
            }
        cached_revision = int(
            as_number(getattr(self, "_teammate_profiles_cache_revision", -1))
        )
        cached = getattr(self, "_teammate_profiles_cache", None)
        try:
            status = provider(
                cached_revision if isinstance(cached, dict) else None
            )
        except TypeError:
            status = provider()
        revision = int(as_number(status.get("revision")))
        if revision == cached_revision and isinstance(cached, dict):
            return copy.deepcopy(cached)
        profiles = status.get("profiles")
        if not isinstance(profiles, list):
            profiles = []
        resolved_profiles: list[dict[str, Any]] = []
        panels: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            raw_equipment = profile.get("equipment")
            if not isinstance(raw_equipment, (list, tuple)) or not raw_equipment:
                resolved_profiles.append(copy.deepcopy(profile))
                continue
            cached_projection = self._teammate_profile_projection_cached(profile)
            if cached_projection is None:
                resolved_profiles.append(copy.deepcopy(profile))
                self._schedule_teammate_projection(profile, revision)
                continue
            resolved, panel = cached_projection
            resolved_profiles.append(resolved)
            target_id = norm(resolved.get("target_id"))
            if target_id and panel:
                panels[target_id] = panel
        result = {
            "revision": revision,
            "player_profiles": copy.deepcopy(resolved_profiles),
            "profile_panels": copy.deepcopy(panels),
        }
        self._teammate_profiles_cache_revision = revision
        self._teammate_profiles_cache = copy.deepcopy(result)
        return result
    def _overlay_job_profile(self, job: str) -> dict[str, list[str]]:
        tables = getattr(getattr(self, "store", None), "tables", {})
        row = next(
            (
                item
                for item in tables.get("effective", [])
                if norm(item.get("职业")) == norm(job)
            ),
            {},
        )
        attack_text = norm(row.get("攻击1"))
        attack_types = (
            ["physical", "magic"]
            if attack_text == "双攻"
            else (["magic"] if attack_text == "魔攻" else ["physical"])
        )
        element_text = " ".join(
            norm(row.get(key)) for key in ("属性1", "属性2") if norm(row.get(key))
        )
        element_names = (("火", "fire"), ("水", "water"), ("光", "light"), ("暗", "dark"))
        elements = [value for label, value in element_names if label in element_text]
        mixed_elements = {
            "冰灵": ["water", "fire"],
            "烈": ["dark", "fire"],
            "暗": ["dark", "light"],
            "曜": ["light", "dark"],
        }
        mixed_attacks = {"圣骑士", "十字军"}
        if job in mixed_elements:
            elements = mixed_elements[job]
        if job in mixed_attacks:
            attack_types = ["physical", "magic"]
        return {"attack_types": attack_types, "elements": elements}
    @staticmethod
    def _overlay_row_active(row: dict[str, Any], second: float) -> bool:
        return any(
            isinstance(interval, (list, tuple))
            and len(interval) >= 2
            and as_number(interval[0]) <= second < as_number(interval[1])
            for interval in row.get("intervals", [])
        )
    @classmethod
    def _overlay_effect_summary(
        cls, rows: list[dict[str, Any]], second: float
    ) -> dict[str, float]:
        return dict(cls._overlay_effect_resolution(rows, second).values)

    @staticmethod
    def _overlay_effect_resolution(
        rows: list[dict[str, Any]], second: float
    ) -> Any:
        inputs: list[EffectInput] = []
        for row_index, row in enumerate(rows):
            key = norm(row.get("key"))
            value = max(0.0, as_number(row.get("value")))
            if not key or value <= 0.0:
                continue
            intervals = row.get("intervals", [])
            for interval_index, interval in enumerate(intervals):
                if not isinstance(interval, (list, tuple)) or len(interval) < 2:
                    continue
                start = as_number(interval[0])
                end = as_number(interval[1])
                inputs.append(
                    EffectInput(
                        key=key,
                        value=value,
                        skill_id=norm(row.get("skill_id")),
                        skill_name=norm(row.get("skill_name")),
                        job=norm(row.get("job")),
                        source_id=norm(row.get("source_id")),
                        target_id=norm(row.get("target_id")),
                        instance_id=f"{row_index}:{interval_index}",
                        start=start,
                        end=end,
                        stack_group=norm(row.get("stack_group")),
                        stackable=bool(row.get("stackable")),
                        data_source=norm(row.get("data_source"))
                        or norm(row.get("value_source")),
                        metadata={"row_index": row_index},
                    )
                )
        return EFFECT_STACKING_ENGINE.resolve_active(inputs, second)

    @classmethod
    def _overlay_effect_contributors(
        cls,
        rows: list[dict[str, Any]],
        second: float,
        *,
        category: str,
        mode: str,
    ) -> list[dict[str, Any]]:
        """Return the active skills that actually contribute to one total.

        The compact overlay used to publish only the post-stacking total.  Keep
        the exact same replacement groups here so an expanded row never lists
        a weaker, currently suppressed skill as part of that total. Aggregate
        caps belong only to the summary row: each concrete skill keeps its
        packet/catalog effect value instead of being proportionally reduced.
        """

        if category == "damage":
            desired_keys = {
                "增伤%",
                "物理增伤%" if mode == "physical" else "魔法增伤%",
            }
        elif category == "resistance":
            element = {
                "fire": "火",
                "water": "水",
                "light": "光",
                "dark": "暗",
            }.get(mode, "")
            if not element:
                return []
            desired_keys = {"减全抗%", f"减{element}抗%"}
        elif category == "critical":
            desired_keys = {"减致命抵抗%"}
        elif category == "defense":
            desired_keys = {
                "减防御%",
                "减物理防御%" if mode == "physical" else "减魔法防御%",
            }
        elif category == "freeze":
            desired_keys = {"冰冻层"}
        else:
            return []

        resolution = cls._overlay_effect_resolution(rows, second)
        contributors: list[dict[str, Any]] = []
        for contribution in resolution.effective:
            if contribution.rule.key not in desired_keys:
                continue
            effect = contribution.effect
            row_index = int(effect.metadata.get("row_index", -1))
            if not 0 <= row_index < len(rows):
                continue
            row = rows[row_index]
            raw_value = max(0.0, effect.value)
            start, end = effect.start, effect.end
            skill_name = norm(row.get("skill_name")) or norm(row.get("label"))
            contributors.append(
                {
                    "key": "|".join(
                        filter(
                            None,
                            (
                                norm(row.get("target_id")),
                                category,
                                mode,
                                norm(row.get("source_id")),
                                _display_skill_key(skill_name or row.get("skill_id")),
                                norm(row.get("key")),
                            ),
                        )
                    ),
                    "skill_id": norm(row.get("skill_id")),
                    "skill_name": skill_name or "待识别技能",
                    "source_id": norm(row.get("source_id")),
                    "source_name": norm(row.get("source_name")),
                    "source_job": norm(row.get("job")),
                    "effect_key": norm(row.get("key")),
                    "raw_value": raw_value,
                    "current_value": raw_value,
                    "start_seconds": start,
                    "end_seconds": end,
                    "duration_seconds": max(0.1, end - start),
                    "remaining_seconds": max(0.0, end - second),
                    "value_source": norm(row.get("value_source")),
                    "duration_source": norm(row.get("duration_source")),
                    "data_source": norm(row.get("data_source")) or (
                        "抓包数据"
                        if norm(row.get("value_source")) == "packet"
                        and norm(row.get("duration_source")) == "packet"
                        else "抓包优先 / 预设兜底"
                    ),
                }
            )
        contributors.sort(
            key=lambda row: (
                -as_number(row.get("current_value")),
                as_number(row.get("end_seconds")),
                norm(row.get("skill_name")),
                norm(row.get("source_id")),
            )
        )
        return contributors

    @classmethod
    def _overlay_debuff_rows(
        cls,
        sources: list[dict[str, Any]],
        elapsed: float,
        damage_targets: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [row for row in sources if isinstance(row, dict)]
        active = [row for row in rows if cls._overlay_row_active(row, elapsed)]

        # During transitions two entities can briefly retain statuses. Status
        # refreshes are not target-selection evidence: a long/open status on
        # an old entity must not steal the overlay from the Boss the party is
        # actually hitting.
        def target_score(target_id: str) -> tuple[float, int]:
            target_rows = [row for row in active if norm(row.get("target_id")) == target_id]
            latest = max(
                (
                    as_number(interval[0])
                    for row in target_rows
                    for interval in row.get("intervals", [])
                    if isinstance(interval, (list, tuple)) and len(interval) >= 2
                    and as_number(interval[0]) <= elapsed < as_number(interval[1])
                ),
                default=0.0,
            )
            return latest, len(target_rows)

        # Live snapshots pass all recognized runtime Boss targets. Select only
        # by their latest confirmed damage timestamp and keep that target
        # sticky through a stop/mechanism interval. Buffs are built from the
        # independent player-buff ledger above and are deliberately unaffected
        # by this Boss choice.
        damaged_targets = [
            row
            for row in (damage_targets or [])
            if isinstance(row, dict)
            and norm(row.get("target_id"))
            and as_number(row.get("last_ms")) > 0.0
        ]
        if damaged_targets:
            selected_target = max(
                damaged_targets,
                key=lambda row: (
                    as_number(row.get("last_ms")),
                    as_number(row.get("first_ms")),
                    target_score(norm(row.get("target_id"))),
                ),
            )
            target_id = norm(selected_target.get("target_id"))
        elif damage_targets is not None:
            # A Boss status arriving before the first confirmed hit is not
            # enough to know which target the player is fighting.
            target_id = ""
        else:
            # Backward-compatible fallback for old cached snapshots which did
            # not publish runtime target rows.
            target_id = max(
                {
                    norm(row.get("target_id"))
                    for row in active
                    if norm(row.get("target_id"))
                },
                key=target_score,
                default="",
            )
        if not target_id:
            return []
        target_rows = [row for row in rows if norm(row.get("target_id")) == target_id]
        boundaries = sorted(
            {
                as_number(interval[1])
                for row in target_rows
                for interval in row.get("intervals", [])
                if isinstance(interval, (list, tuple)) and len(interval) >= 2
                and as_number(interval[0]) <= elapsed < as_number(interval[1])
                and as_number(interval[1]) > elapsed
            }
        )
        current = cls._overlay_effect_summary(target_rows, elapsed)
        definitions = (
            ("damage", "physical", "物理增伤", "物理增伤合计%"),
            ("damage", "magic", "魔法增伤", "魔法增伤合计%"),
            ("resistance", "fire", "减火抗", "减火抗%"),
            ("resistance", "water", "减水抗", "减水抗%"),
            ("resistance", "light", "减光抗", "减光抗%"),
            ("resistance", "dark", "减暗抗", "减暗抗%"),
            ("critical", "critical", "减致命抗", "减致命抵抗%"),
            ("defense", "physical", "减物防", "减物理防御%"),
            ("defense", "magic", "减魔防", "减魔法防御%"),
            ("freeze", "freeze", "叠冰", "冰冻层"),
        )
        result: list[dict[str, Any]] = []
        for category, mode, label, summary_key in definitions:
            value = current.get(summary_key, 0.0)
            if value <= 0:
                continue
            change_at = 0.0
            next_value = value
            for boundary in boundaries:
                candidate = cls._overlay_effect_summary(target_rows, boundary + 0.001).get(
                    summary_key, 0.0
                )
                if abs(candidate - value) > 1e-9:
                    change_at = boundary
                    next_value = candidate
                    break
            applicable_keys = {
                summary_key,
                "增伤%" if category == "damage" else "",
                f"{mode == 'physical' and '物理' or '魔法'}增伤%" if category == "damage" else "",
                "减全抗%" if category == "resistance" else "",
                "减防御%" if category == "defense" else "",
                f"减{mode == 'physical' and '物理' or '魔法'}防御%" if category == "defense" else "",
            }
            active_source_rows = [
                row
                for row in target_rows
                if cls._overlay_row_active(row, elapsed)
                and norm(row.get("key")) in applicable_keys
            ]
            active_starts = [
                as_number(interval[0])
                for row in active_source_rows
                for interval in row.get("intervals", [])
                if isinstance(interval, (list, tuple)) and len(interval) >= 2
                and as_number(interval[0]) <= elapsed < as_number(interval[1])
            ]
            start = max(active_starts, default=elapsed)
            contributors = cls._overlay_effect_contributors(
                target_rows,
                elapsed,
                category=category,
                mode=mode,
            )
            value_sources = {
                norm(row.get("value_source"))
                for row in active_source_rows
                if norm(row.get("value_source"))
            }
            duration_sources = {
                norm(row.get("duration_source"))
                for row in active_source_rows
                if norm(row.get("duration_source"))
            }
            value_source = (
                "packet" if value_sources == {"packet"}
                else "preset_fallback" if value_sources
                else "unavailable"
            )
            duration_source = (
                "packet" if duration_sources == {"packet"}
                else "preset_fallback" if duration_sources
                else "unavailable"
            )
            result.append(
                {
                    "key": f"{target_id}|{category}|{mode}",
                    "category": category,
                    "mode": mode,
                    "label": label,
                    "target_id": target_id,
                    "current_value": value,
                    "next_value": next_value,
                    "start_seconds": start,
                    "end_seconds": change_at,
                    "duration_seconds": max(0.1, change_at - start) if change_at else 0.0,
                    "remaining_seconds": max(0.0, change_at - elapsed) if change_at else 0.0,
                    "contributors": contributors,
                    "value_source": value_source,
                    "duration_source": duration_source,
                    "data_source": (
                        "抓包数据"
                        if value_source == "packet" and duration_source == "packet"
                        else "抓包优先 / 预设兜底"
                    ),
                }
            )
        return result
    def _skill_cast_payloads(
        self,
        snapshot: CombatSnapshot,
    ) -> list[dict[str, Any]]:
        platform = event_platform_for(snapshot)
        resolver = LiveCastTimingResolver(
            self.store,
            player_buff_clear_times_by_target(
                platform.player_buff_clear_events
            ),
        )
        rows: list[dict[str, Any]] = []
        for event in platform.skill_cast_events:
            timing = resolver.resolve(event, event.timestamp_ms / 1000.0)
            rows.append({
                **_json_safe(event),
                "cast_time": timing.cast_time,
                "action_speed_percent": timing.action_speed_percent,
                "end_timestamp_ms": round(timing.end_seconds * 1000),
            })
        return rows
    def start_capture(self, duration: float = 0.0) -> dict[str, Any]:
        return self.live_capture.start(duration)
    def set_capture_auto(self, enabled: bool) -> dict[str, Any]:
        return self.live_capture.set_auto_enabled(enabled)
    def capture_toggles(self) -> dict[str, Any]:
        from ..live_dps.capture_toggles import CAPTURE_TOGGLE_LABELS
        return {
            "toggles": self.live_capture.get_toggles(),
            "labels": CAPTURE_TOGGLE_LABELS,
        }
    def set_capture_toggles(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..live_dps.capture_toggles import CAPTURE_TOGGLE_LABELS
        raw = payload.get("toggles", {}) if isinstance(payload, dict) else {}
        return {
            "toggles": self.live_capture.set_toggles(raw),
            "labels": CAPTURE_TOGGLE_LABELS,
        }
    def stop_capture(self) -> dict[str, Any]:
        # Manual Stop must acknowledge immediately. Npcap closure, decoder
        # draining and the exact final snapshot continue on the capture thread;
        # the page's existing one-second status poll receives the completed
        # snapshot as soon as it is ready.
        return self.live_capture.request_stop()
def bind_host_globals(host_globals: dict[str, object]) -> None:
    protected = {'bind_host_globals', 'NativeOverlayMixin'}
    for name, value in host_globals.items():
        if name.startswith('__') or name in protected:
            continue
        globals()[name] = value


# Extracted from v410_native_core.py; algorithm bodies are unchanged.
