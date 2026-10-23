from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, norm  # noqa: E402
from dragon_nest_builder.editor import BuildEditor  # noqa: E402
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.scoring import canonical_skill  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.ui.live_dps_page import LiveDpsPage  # noqa: E402


def _placeholder(job: str, window: float) -> dict[str, Any]:
    return {
        "job": job,
        "dps": 1.0,
        "average": 1.0,
        "total": 1.0,
        "segments": [],
        "support_actions": [],
        "effect_windows": [],
        "window": window,
        "summary": {},
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: diagnose_holy_punishment_score.py RECORD.dpscap")
        return 2
    started = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    editor = BuildEditor(
        "holy-punishment-diagnostic",
        store,
        BuildCalculator(store),
        store.defaults["build1"],
        standardize_default=True,
    )
    if os.getenv("HOLY_TRACE") == "1":
        editor._skill_burst_new_scheme_1_trace = []
        editor._skill_burst_new_scheme_1_trace_support_skills = {"神圣惩戒"}
    if os.getenv("HOLY_DISABLE_OPT") == "1":
        editor._skill_burst_disable_conservative_1_6 = True
        editor._skill_burst_disable_local_optimizer = True
        editor._skill_burst_disable_hybrid_optimizer = True
        editor._skill_burst_enable_new_scheme_1_multistart = False
    page = LiveDpsPage(store, timeline_editor=editor)
    debug_window = float(os.getenv("HOLY_DEBUG_WINDOW", "0") or 0.0)
    if debug_window > 0:
        original_localized_timing = page._team_score_localized_timing

        def limited_localized_timing(output_windows, cast_schedule):
            offset, windows, schedule, window = original_localized_timing(
                output_windows, cast_schedule
            )
            limit = min(window, debug_window)
            return (
                offset,
                tuple(
                    (start, min(end, limit))
                    for start, end in windows
                    if start < limit and end > start
                ),
                {
                    key: [
                        (start, min(end, limit))
                        for start, end in intervals
                        if start < limit and end > start
                    ]
                    for key, intervals in schedule.items()
                },
                limit,
            )

        page._team_score_localized_timing = limited_localized_timing
    source = V576DiagnosticReplaySource(Path(sys.argv[1]), ROOT / "dragon_nest_builder/data")
    aggregator = CombatAggregator()
    for event in source.poll():
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    decoded_elapsed = time.perf_counter() - started

    original_parallel = editor._extreme_timeline_tasks_parallel

    def paladin_only(task_specs, progress_callback=None):
        results: list[dict[str, Any]] = []
        for index, spec in enumerate(task_specs):
            job = norm(spec.get("job"))
            result = (
                editor._extreme_timeline_result_from_spec(spec)
                if job == "圣骑士"
                else _placeholder(job, float(spec.get("window", 0.0)))
            )
            results.append(result)
            if callable(progress_callback):
                progress_callback(len(results), index, job)
        return results

    editor._extreme_timeline_tasks_parallel = paladin_only
    target_ids = page._sync_team_score_boss_selector(snapshot)
    preset = page._team_score_resolved_boss_preset_name()
    output_windows = page.score_engine._output_windows(snapshot, target_ids)
    score_offset, local_output_windows, _local_schedule, score_window = (
        page._team_score_localized_timing(output_windows, {})
    )
    print(json.dumps({
        "score_scope": page._team_score_selected_boss_key,
        "score_offset": score_offset,
        "score_window": score_window,
        "output_windows": output_windows,
        "local_output_windows": local_output_windows,
        "blocked_intervals": page._team_score_blocked_intervals(
            local_output_windows, score_window
        ),
    }, ensure_ascii=False), flush=True)
    plans = page._build_team_score_theory_plans(snapshot, target_ids, preset)
    editor._extreme_timeline_tasks_parallel = original_parallel
    rows = page.score_engine.score(
        snapshot,
        theory_plans=plans,
        boss_target_ids=target_ids,
    )
    paladin = next((row for row in rows if row.job == "圣骑士"), None)
    if paladin is None:
        print("paladin not found")
        return 1
    key = canonical_skill("神圣惩戒")
    plan = plans.get(paladin.source_id, {})
    plan_rows = [
        row
        for row in plan.get("segments", ())
        if canonical_skill(row.get("skill")) == key
    ]
    detail = next(
        (row for row in paladin.skill_details if canonical_skill(row.skill_name) == key),
        None,
    )
    timeline = [
        row
        for row in paladin.theory_timeline
        if canonical_skill(row.skill_name) == key
    ]
    payload = {
        "decode_seconds": decoded_elapsed,
        "total_seconds": time.perf_counter() - started,
        "target_ids": len(target_ids or ()),
        "plan_casts": len(plan_rows),
        "plan_segment_count": len(plan.get("segments", ())),
        "plan_last_end": max(
            (float(row.get("end", 0.0)) for row in plan.get("segments", ())),
            default=0.0,
        ),
        "plan_skill_counts": {
            skill: sum(
                1 for row in plan.get("segments", ())
                if canonical_skill(row.get("skill")) == skill
            )
            for skill in sorted({
                canonical_skill(row.get("skill"))
                for row in plan.get("segments", ())
                if canonical_skill(row.get("skill"))
            })
        },
        "plan_times": [
            [round(float(row.get("start", 0.0)), 3), round(float(row.get("end", 0.0)), 3)]
            for row in plan_rows
        ],
        "plan_rows": plan_rows,
        "holy_trace": [
            row
            for row in plan.get("planner_trace", ())
            if (
                canonical_skill(row.get("skill")) == key
                or any(
                    canonical_skill(action.get("skill")) == key
                    for action in row.get("actions", ())
                )
            )
        ],
        "score_detail": (
            {
                "theory_count": detail.theory_count,
                "theory_power": detail.theory_power,
                "expected_duration": detail.expected_duration,
            }
            if detail is not None
            else None
        ),
        "timeline": [
            {
                "start": round(row.seconds, 3),
                "end": round(row.end_seconds, 3),
                "power": round(row.power, 3),
                "expected_duration": round(row.expected_duration, 3),
                "utilized_duration": round(row.utilized_duration, 3),
            }
            for row in timeline
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    editor.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
