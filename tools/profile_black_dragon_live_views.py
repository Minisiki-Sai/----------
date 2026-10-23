from __future__ import annotations

import argparse
import cProfile
import io
import pickle
import pstats
import sys
import time
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.action_parser import (
    ACTION_PARSER_SCHEMA_VERSION,
    action_parser_mode,
)
from dragon_nest_builder.live_dps.profile import ClientProfile
from dragon_nest_builder.live_dps.scoring import TeamScoreEngine
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.ui.live_dps_page import (
    _boss_statistics_groups,
    _boss_timeline_events_for_windows,
)


def timed(label: str, callback):
    started = time.perf_counter()
    value = callback()
    elapsed = time.perf_counter() - started
    print(f"{label}: {elapsed:.3f}s")
    return value, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument(
        "--theory-boss",
        type=int,
        action="append",
        default=[],
        help="1-based Boss index whose full team-score theory plan should be timed",
    )
    parser.add_argument(
        "--theory-all",
        action="store_true",
        help="time the full team-score theory plan for all detected Bosses",
    )
    parser.add_argument(
        "--ui-views",
        action="store_true",
        help="time Boss statistics and team-timeline Qt rendering",
    )
    parser.add_argument(
        "--profile-serial-boss",
        type=int,
        action="append",
        default=[],
        help="1-based Boss index whose first job should be profiled in-process",
    )
    parser.add_argument(
        "--profile-score",
        action="store_true",
        help="profile the all-Boss score pass without external theory plans",
    )
    parser.add_argument(
        "--trace-theory-batches",
        action="store_true",
        help="print timeline worker batch sizes and nested elapsed times",
    )
    parser.add_argument(
        "--print-support",
        action="store_true",
        help="print support-score detail rows after building external theory plans",
    )
    parser.add_argument(
        "--print-support-source",
        default="",
        help="print every support-score row for this player source id",
    )
    parser.add_argument(
        "--print-theory-source",
        default="",
        help="print theory effect windows and skill segments for this player source id",
    )
    parser.add_argument(
        "--skip-baseline-scores",
        action="store_true",
        help="skip unrelated baseline score passes when diagnosing theory plans",
    )
    parser.add_argument(
        "--switch-score-bosses",
        action="store_true",
        help="simulate Boss 1 -> Boss 2 -> Boss 1 score selector changes",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_root = root / "dragon_nest_builder" / "data"
    profile_path = data_root / "live_dps_profiles" / "v576.json"
    profile = ClientProfile.load(profile_path)

    capture_stat = args.capture.stat()
    cache_root = root / "outputs" / "profile_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    parser_mode = action_parser_mode()
    cache_path = cache_root / (
        f"{args.capture.stem}_{capture_stat.st_size}_{capture_stat.st_mtime_ns}_"
        f"actions-v{ACTION_PARSER_SCHEMA_VERSION}-{parser_mode}.pickle"
    )
    if cache_path.exists():
        snapshot, _ = timed(
            "snapshot_cache_load",
            lambda: pickle.loads(cache_path.read_bytes()),
        )
    else:
        source, _ = timed(
            "source_init",
            lambda: V576DiagnosticReplaySource(
                args.capture,
                data_root,
                profile.known_players,
            ),
        )
        try:
            events, _ = timed("decode_poll", source.poll)
        finally:
            source.close()
        print(f"decoded_events: {len(events):,}")

        aggregator = CombatAggregator()

        def ingest_all() -> None:
            for event in events:
                aggregator.ingest(event)

        timed("aggregate", ingest_all)
        snapshot, _ = timed(
            "snapshot_with_events",
            lambda: aggregator.snapshot(include_events=True),
        )
        timed("snapshot_cache_save", lambda: cache_path.write_bytes(pickle.dumps(snapshot)))
    print(
        "snapshot: "
        f"combat={len(snapshot.combat_events):,}, "
        f"casts={len(snapshot.skill_cast_events):,}, "
        f"boss_effect_events={len(snapshot.boss_effect_events):,}, "
        f"buff_series={len(snapshot.player_buff_series):,}"
    )
    groups, _ = timed("boss_groups", lambda: _boss_statistics_groups(snapshot))
    store = DataStore.load()
    for index, group in enumerate(groups, start=1):
        result, elapsed = timed(
            f"boss_timeline_{index}_{group.name}",
            lambda group=group: _boss_timeline_events_for_windows(
                snapshot.skill_cast_events,
                snapshot.combat_events,
                group.target_ids,
                group.phase_windows,
                store,
            ),
        )
        print(
            f"  boss={group.name}, ids={len(group.target_ids)}, "
            f"windows={group.phase_windows}, timeline_events={len(result[0]):,}, "
            f"compressed={result[1]:.1f}s, elapsed={elapsed:.3f}s"
        )

    engine = TeamScoreEngine(store)
    all_ids = frozenset(
        target_id for group in groups for target_id in group.target_ids
    )
    if args.profile_score:
        profiler = cProfile.Profile()
        profiler.enable()
        timed(
            "score_without_external_theory_all_bosses",
            lambda: engine.score(snapshot, boss_target_ids=all_ids),
        )
        profiler.disable()
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumtime").print_stats(50)
        print(stream.getvalue())
    elif not args.skip_baseline_scores:
        timed(
            "score_without_external_theory_all_bosses",
            lambda: engine.score(snapshot, boss_target_ids=all_ids),
        )
    if not args.skip_baseline_scores:
        for index, group in enumerate(groups, start=1):
            timed(
                f"score_without_external_theory_boss_{index}",
                lambda group=group: engine.score(
                    snapshot, boss_target_ids=group.target_ids
                ),
            )
    if (
        args.theory_boss
        or args.theory_all
        or args.profile_serial_boss
        or args.ui_views
        or args.print_support
        or args.print_theory_source
        or args.switch_score_bosses
    ):
        from PySide6.QtWidgets import QApplication

        from dragon_nest_builder.calculator import BuildCalculator
        from dragon_nest_builder.editor import BuildEditor
        from dragon_nest_builder.shared import BOSS_PRESETS
        from dragon_nest_builder.ui.live_dps_page import LiveDpsPage

        app = QApplication.instance() or QApplication([])
        calculator = BuildCalculator(store)
        editor = BuildEditor("profile", store, calculator, store.defaults["build1"])
        page = LiveDpsPage(store, timeline_editor=editor)
        boss_preset_name = page._team_score_resolved_boss_preset_name()
        page.score_engine.set_boss_profile(
            boss_preset_name,
            BOSS_PRESETS[boss_preset_name],
        )
        if args.print_support:
            jobs = [player.job for player in snapshot.players[:8]]
            for effect in editor._extreme_team_effects_for_jobs(jobs):
                skill = editor._team_effect_skill_name(effect.get("skill"))
                if skill in {"时间加速", "精神爆发", "鸡尾酒"}:
                    print(f"  support_effect={skill}|{effect!r}")
        if args.trace_theory_batches:
            original_parallel = editor._extreme_timeline_tasks_parallel

            def traced_parallel(specs, progress_callback=None):
                return timed(
                    f"theory_batch_{len(specs)}",
                    lambda: original_parallel(
                        specs, progress_callback=progress_callback
                    ),
                )[0]

            editor._extreme_timeline_tasks_parallel = traced_parallel
            original_support = page._team_score_support_replan_metadata

            def traced_support(**kwargs):
                return timed(
                    "support_counterfactual_replans",
                    lambda: original_support(**kwargs),
                )[0]

            page._team_score_support_replan_metadata = traced_support
        if args.switch_score_bosses:
            page._latest_snapshot = snapshot
            page._event_snapshot = snapshot
            page._event_snapshot_revision = page.collector.aggregator.revision
            page._sync_team_score_boss_selector(snapshot)
            sequence = [1, 2, 1]
            for step, combo_index in enumerate(sequence, start=1):
                if combo_index >= page.team_score_boss_combo.count():
                    continue
                page.team_score_boss_combo.setCurrentIndex(combo_index)
                page._team_score_refresh_timer.stop()
                if step > 1:
                    page._latest_snapshot = replace(
                        snapshot,
                        combat_events=(),
                        skill_cast_events=(),
                        boss_effect_events=(),
                    )
                timed(
                    f"score_switch_step_{step}_combo_{combo_index}",
                    page._run_pending_team_score_refresh,
                )
                selected_ids = page._team_score_boss_options.get(
                    page._team_score_selected_boss_key, frozenset()
                )
                thunder = next(
                    (
                        row
                        for row in page._team_score_rows
                        if row.source_id == "0x4EF704CC"
                    ),
                    None,
                )
                print(
                    f"  score_switch_step={step}|"
                    f"selected={page._team_score_selected_boss_key}|"
                    f"rendered={page._team_score_rendered_boss_key}|"
                    f"ids={sorted(selected_ids)}|"
                    f"plans={len(page._team_score_theory_plans)}|"
                    f"rows={len(page._team_score_rows)}|"
                    f"error={page._team_score_theory_error!r}|"
                    f"thunder_actual={getattr(thunder, 'actual_standard_power', None)}|"
                    f"thunder_theory={getattr(thunder, 'theory_standard_power', None)}"
                )
        if args.theory_all:
            page._team_score_theory_signature = None
            page._team_score_theory_plans = {}
            plans, elapsed = timed(
                "full_theory_all_bosses",
                lambda: page._build_team_score_theory_plans(snapshot, all_ids),
            )
            print(
                f"  theory_plans={len(plans)}, error={page._team_score_theory_error!r}, "
                f"elapsed={elapsed:.3f}s"
            )
            print(
                "  support_replans="
                + repr(
                    {
                        source_id: plan.get("_support_counterfactual_replan", {})
                        for source_id, plan in plans.items()
                        if plan.get("_support_counterfactual_replan")
                    }
                )
            )
            if args.print_support:
                rows = page.score_engine.score(
                    snapshot,
                    theory_plans=plans,
                    boss_target_ids=all_ids,
                )
                for row in rows:
                    if (
                        args.print_support_source
                        and row.source_id == args.print_support_source
                    ):
                        print(
                            "  support_player_all="
                            f"{row.source_id}|{row.job}|support={row.support_score:.6f}|"
                            f"weight={row.support_weight:.6f}|index={row.support_index:.6f}"
                        )
                        for detail in row.support_details:
                            print(
                                "  support_row_all="
                                f"{detail.skill_name}|{detail.effect_key}|"
                                f"actual_cov={detail.actual_coverage:.6f}|"
                                f"theory_cov={detail.theory_coverage:.6f}|"
                                f"completion={detail.completion:.6f}|"
                                f"score_weight={detail.score_weight:.6f}|"
                                f"actual_gain={detail.actual_contribution:.6f}|"
                                f"theory_gain={detail.theory_contribution:.6f}|"
                                f"included={detail.included_in_score}"
                            )
                    for detail in row.support_details:
                        if detail.skill_name in {"时间加速", "精神爆发", "鸡尾酒"}:
                            print(
                                "  support_detail_all="
                                f"{row.job}|{detail.skill_name}|{detail.effect_key}|"
                                f"actual_cov={detail.actual_coverage:.6f}|"
                                f"theory_cov={detail.theory_coverage:.6f}|"
                                f"actual_gain={detail.actual_contribution:.6f}|"
                                f"theory_gain={detail.theory_contribution:.6f}"
                            )
        for boss_index in args.theory_boss:
            if not 1 <= boss_index <= len(groups):
                continue
            page._team_score_theory_signature = None
            page._team_score_theory_plans = {}
            group = groups[boss_index - 1]
            plans, elapsed = timed(
                f"full_theory_boss_{boss_index}_{group.name}",
                lambda group=group: page._build_team_score_theory_plans(
                    snapshot, group.target_ids
                ),
            )
            print(
                f"  theory_plans={len(plans)}, error={page._team_score_theory_error!r}, "
                f"elapsed={elapsed:.3f}s"
            )
            print(
                "  plan_shapes="
                + repr(
                    {
                        source_id: (
                            len(plan.get("segments", ())),
                            round(float(plan.get("raw_total", 0.0)), 3),
                            round(float(plan.get("total", 0.0)), 3),
                        )
                        for source_id, plan in plans.items()
                    }
                )
            )
            print(
                "  support_replans="
                + repr(
                    {
                        source_id: plan.get("_support_counterfactual_replan", {})
                        for source_id, plan in plans.items()
                        if plan.get("_support_counterfactual_replan")
                    }
                )
            )
            if args.print_theory_source:
                plan = plans.get(args.print_theory_source, {})
                print(
                    f"  theory_source_boss_{boss_index}="
                    f"{args.print_theory_source}|job={plan.get('job')}|"
                    f"offset={plan.get('_score_time_offset')}"
                )
                for row in plan.get("external_windows", ()):
                    if row.get("source") in {"精神爆发", "时间加速"}:
                        print(
                            f"  theory_effect_boss_{boss_index}="
                            f"{row.get('source')}|{row.get('start')}|{row.get('end')}|"
                            f"{row.get('attrs')}"
                        )
                for segment in plan.get("segments", ()):
                    start = float(segment.get("start", 0.0))
                    if start > 30.0:
                        continue
                    print(
                        f"  theory_segment_boss_{boss_index}="
                        f"{start:.3f}|{float(segment.get('end', 0.0)):.3f}|"
                        f"{segment.get('skill')}|speed={segment.get('action_speed_percent')}|"
                        f"cdr={segment.get('cdr_percent')}"
                    )
            if args.print_support:
                rows = page.score_engine.score(
                    snapshot,
                    theory_plans=plans,
                    boss_target_ids=group.target_ids,
                )
                for row in rows:
                    if (
                        args.print_support_source
                        and row.source_id == args.print_support_source
                    ):
                        print(
                            f"  support_player_boss_{boss_index}="
                            f"{row.source_id}|{row.job}|support={row.support_score:.6f}|"
                            f"weight={row.support_weight:.6f}|index={row.support_index:.6f}"
                        )
                        for detail in row.support_details:
                            print(
                                f"  support_row_boss_{boss_index}="
                                f"{detail.skill_name}|{detail.effect_key}|"
                                f"actual_cov={detail.actual_coverage:.6f}|"
                                f"theory_cov={detail.theory_coverage:.6f}|"
                                f"completion={detail.completion:.6f}|"
                                f"score_weight={detail.score_weight:.6f}|"
                                f"actual_gain={detail.actual_contribution:.6f}|"
                                f"theory_gain={detail.theory_contribution:.6f}|"
                                f"included={detail.included_in_score}"
                            )
                    for detail in row.support_details:
                        if detail.skill_name in {"时间加速", "精神爆发", "鸡尾酒"}:
                            print(
                                f"  support_detail_boss_{boss_index}="
                                f"{row.job}|{detail.skill_name}|{detail.effect_key}|"
                                f"actual_cov={detail.actual_coverage:.6f}|"
                                f"theory_cov={detail.theory_coverage:.6f}|"
                                f"actual_gain={detail.actual_contribution:.6f}|"
                                f"theory_gain={detail.theory_contribution:.6f}"
                            )
            cached_plans, cached_elapsed = timed(
                f"cached_theory_boss_{boss_index}_{group.name}",
                lambda group=group: page._build_team_score_theory_plans(
                    snapshot, group.target_ids
                ),
            )
            print(
                f"  cached_theory_plans={len(cached_plans)}, "
                f"elapsed={cached_elapsed:.6f}s"
            )
        for boss_index in args.profile_serial_boss:
            if not 1 <= boss_index <= len(groups):
                continue
            page._team_score_theory_signature = None
            page._team_score_theory_plans = {}
            page._team_score_theory_plan_cache.clear()
            group = groups[boss_index - 1]
            original_parallel = editor._extreme_timeline_tasks_parallel

            def profiled_parallel(specs):
                if not specs:
                    return []
                profiler = cProfile.Profile()
                profiler.enable()
                first = editor._extreme_timeline_result_from_spec(specs[0])
                profiler.disable()
                stream = io.StringIO()
                pstats.Stats(profiler, stream=stream).sort_stats("cumtime").print_stats(40)
                print(f"serial_profile_job={specs[0].get('job')}")
                print(stream.getvalue())
                return [first, *original_parallel(specs[1:])]

            editor._extreme_timeline_tasks_parallel = profiled_parallel
            try:
                timed(
                    f"profiled_theory_boss_{boss_index}_{group.name}",
                    lambda group=group: page._build_team_score_theory_plans(
                        snapshot, group.target_ids
                    ),
                )
            finally:
                editor._extreme_timeline_tasks_parallel = original_parallel
        if args.ui_views:
            page._latest_snapshot = snapshot
            for tab_index, label in ((0, "skills"), (1, "curve"), (4, "timeline")):
                page.boss_stats_tabs.setCurrentIndex(tab_index)
                if tab_index == 4:
                    page._boss_stats_timeline_cache_key = None
                    page._boss_stats_timeline_display_key = None
                timed(
                    f"ui_boss_statistics_{label}",
                    lambda: page._fill_boss_statistics(
                        snapshot,
                        max(snapshot.elapsed_seconds, snapshot.dps_elapsed_seconds),
                    ),
                )
                timed(f"ui_process_events_{label}", app.processEvents)
        page.deleteLater()
        editor.deleteLater()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
