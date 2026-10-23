from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.editor import BuildEditor
from dragon_nest_builder.ui.live_dps_page import LiveDpsPage
from dragon_nest_builder.timeline_parallel import warm_timeline_process_pool


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--profile", type=Path, default=Path("team_score.prof"))
    parser.add_argument("--prewarm", action="store_true")
    parser.add_argument("--no-cprofile", action="store_true")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    started = time.perf_counter()
    store = DataStore.load()
    editor = BuildEditor(
        "profile",
        store,
        BuildCalculator(store),
        store.defaults["build1"],
        standardize_default=True,
    )
    page = LiveDpsPage(store, timeline_editor=editor)
    print(f"ui_init={time.perf_counter() - started:.3f}s", flush=True)
    if args.prewarm:
        started = time.perf_counter()
        warm_timeline_process_pool(16)
        # Submit a real eight-slot warm-up and wait until every initializer has
        # had enough time to finish before the measured score section starts.
        from dragon_nest_builder import timeline_parallel

        while timeline_parallel._WARMUP_FUTURES:
            app.processEvents()
            time.sleep(0.05)
        print(f"prewarm={time.perf_counter() - started:.3f}s", flush=True)

    started = time.perf_counter()
    page.collector.load_diagnostic_capture(args.capture)
    snapshot = page.collector.snapshot(include_events=True)
    print(
        f"decode={time.perf_counter() - started:.3f}s "
        f"combat={len(snapshot.combat_events)} casts={len(snapshot.skill_cast_events)} "
        f"boss_effects={len(snapshot.boss_effect_events)}",
        flush=True,
    )

    page._latest_snapshot = snapshot
    profiler = cProfile.Profile()
    started = time.perf_counter()
    if not args.no_cprofile:
        profiler.enable()
    page._fill_team_scores(snapshot, page.collector.aggregator.revision)
    if not args.no_cprofile:
        profiler.disable()
    elapsed = time.perf_counter() - started
    if not args.no_cprofile:
        profiler.dump_stats(str(args.profile))
    print(f"team_score={elapsed:.3f}s rows={len(page._team_score_rows)}", flush=True)
    print(
        f"theory_diagnostics={getattr(page, '_team_score_theory_diagnostics', {})}",
        flush=True,
    )
    print(
        f"score_diagnostics={getattr(page.score_engine, '_score_runtime_diagnostics', {})}",
        flush=True,
    )
    if getattr(page, "_team_score_theory_error", ""):
        print(f"theory_error={page._team_score_theory_error}", flush=True)
    if args.summary:
        def serializable(value):
            return asdict(value) if is_dataclass(value) else value

        summary = {
            "elapsed": elapsed,
            "diagnostics": getattr(page, "_team_score_theory_diagnostics", {}),
            "team_effects": editor._extreme_team_effects_for_jobs(
                [row.job for row in getattr(page, "_team_score_rows", [])]
            ),
            "rows": [
                serializable(row)
                for row in getattr(page, "_team_score_rows", [])
            ],
            "plans": {
                str(source_id): {
                    "total": plan.get("total"),
                    "raw_total": plan.get("raw_total"),
                    "average": plan.get("average"),
                    "dps": plan.get("dps"),
                    "_score_skill_damages": plan.get("_score_skill_damages", {}),
                    "_score_skill_powers": plan.get("_score_skill_powers", {}),
                    "_score_skill_components": plan.get(
                        "_score_skill_components", {}
                    ),
                    "_score_glyphs": plan.get("_score_glyphs", []),
                    "segments": [
                        {
                            "start": row.get("start"),
                            "end": row.get("end"),
                            "skill": row.get("skill"),
                            "kind": row.get("kind"),
                            "type": row.get("type"),
                            "raw_effective_score": row.get("raw_effective_score"),
                            "effective_score": row.get("effective_score"),
                            "score_actual_damage": row.get("_score_actual_damage"),
                            "periodic_active_seconds": row.get("periodic_active_seconds"),
                            "periodic_damage_seconds": row.get("periodic_damage_seconds"),
                            "periodic_utilization": row.get("periodic_utilization"),
                            "snapshot_attack_multiplier": row.get("snapshot_attack_multiplier"),
                            "snapshot_element_bonus": row.get("snapshot_element_bonus"),
                            "periodic_breakdown": row.get("periodic_breakdown", {}),
                            "detail": row.get("detail"),
                        }
                        for row in plan.get("segments", ())
                        if isinstance(row, dict)
                    ],
                    "actual_periodic_segments": plan.get(
                        "_score_actual_periodic_segments", {}
                    ),
                    "effect_windows": plan.get("effect_windows", []),
                    "external_windows": plan.get("external_windows", []),
                }
                for source_id, plan in getattr(
                    page, "_team_score_theory_plans", {}
                ).items()
                if isinstance(plan, dict)
            },
        }
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if not args.no_cprofile:
        pstats.Stats(profiler).strip_dirs().sort_stats("cumulative").print_stats(50)

    page.deleteLater()
    editor.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
