from __future__ import annotations

import os
import copy
import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.editor import BuildEditor
from dragon_nest_builder.live_dps.aggregator import CombatAggregator
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource
from dragon_nest_builder.ui.live_dps_page import LiveDpsPage


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print(
            "usage: profile_team_score_runtime.py RECORD.dpscap [SNAPSHOT.pkl]",
            flush=True,
        )
        return 2
    started = time.perf_counter()

    def report(label: str) -> None:
        print(f"{time.perf_counter() - started:8.2f}s  {label}", flush=True)

    record = Path(sys.argv[1])
    snapshot_cache = Path(sys.argv[2]) if len(sys.argv) == 3 else None
    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor = BuildEditor(
        "profile",
        store,
        calculator,
        store.defaults["build1"],
        standardize_default=True,
    )
    page = LiveDpsPage(store, timeline_editor=editor)
    report("UI and editor ready")
    audited_specs: list[tuple[str, str, dict]] = []
    if os.getenv("PROFILE_TEAM_SCORE_AUDIT_SPECS") == "1":
        original_parallel = editor._extreme_timeline_tasks_parallel

        def record_spec(phase: str, token: str, spec: dict) -> None:
            if "_timing_precomputed_result" in spec:
                return
            ignored = {"score_exclusion_context", "_timing_ablation_source"}
            audited_specs.append((phase, token, copy.deepcopy({
                key: value for key, value in spec.items() if key not in ignored
            })))

        def audit_parallel(specs, *args, **kwargs):
            for index, spec in enumerate(specs):
                record_spec("baseline", str(index), spec)
            for name in ("followup_factory", "followup_result_factory"):
                callback = kwargs.get(name)
                if not callable(callback):
                    continue

                def audited_callback(*callback_args, _callback=callback, _phase=name):
                    rows = _callback(*callback_args)
                    for token, spec in rows:
                        record_spec(_phase, str(token), spec)
                    return rows

                kwargs[name] = audited_callback
            return original_parallel(specs, *args, **kwargs)

        editor._extreme_timeline_tasks_parallel = audit_parallel
    selected_profile_job = os.getenv("PROFILE_TEAM_SCORE_JOB", "").strip()
    if selected_profile_job:
        from pyinstrument import Profiler

        profile_index = 0

        def selective_parallel(task_specs, progress_callback=None):
            nonlocal profile_index
            results = []
            for index, spec in enumerate(task_specs):
                job = str(spec.get("job", ""))
                if job == selected_profile_job:
                    profile_index += 1
                    profiler = Profiler(interval=0.01)
                    profiler.start()
                    previous_process_events = getattr(
                        editor, "_extreme_timeline_process_events", False
                    )
                    editor._extreme_timeline_process_events = False
                    try:
                        result = editor._extreme_timeline_result_from_spec(spec)
                    finally:
                        editor._extreme_timeline_process_events = previous_process_events
                        profiler.stop()
                    output = Path("outputs") / (
                        f"team_score_{selected_profile_job}_{profile_index}.html"
                    )
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(profiler.output_html(), encoding="utf-8")
                    report(f"profiled {job} -> {output}")
                else:
                    result = {
                        "job": job,
                        "dps": 1.0,
                        "average": 1.0,
                        "total": 1.0,
                        "segments": [],
                        "support_actions": [],
                        "window": spec.get("window", 0.0),
                        "summary": {},
                    }
                results.append(result)
                if callable(progress_callback):
                    progress_callback(len(results), index, job)
            return results

        editor._extreme_timeline_tasks_parallel = selective_parallel

    if snapshot_cache is not None and snapshot_cache.exists():
        with snapshot_cache.open("rb") as handle:
            snapshot = pickle.load(handle)
        report("loaded prepared snapshot cache")
    else:
        source = V576DiagnosticReplaySource(
            record,
            Path("dragon_nest_builder/data"),
        )
        events = source.poll()
        report(f"decoded {len(events):,} events")
        aggregator = CombatAggregator()
        for event in events:
            aggregator.ingest(event)
        snapshot = aggregator.snapshot(include_events=True)
        if snapshot_cache is not None:
            snapshot_cache.parent.mkdir(parents=True, exist_ok=True)
            with snapshot_cache.open("wb") as handle:
                pickle.dump(snapshot, handle, protocol=pickle.HIGHEST_PROTOCOL)
            report("saved prepared snapshot cache")
    report(
        "aggregated "
        + ", ".join(f"{player.job}:{player.name}" for player in snapshot.players[:8])
    )
    target_ids = page._sync_team_score_boss_selector(snapshot)
    preset = page._team_score_resolved_boss_preset_name()
    report(
        f"boss scope ready: {len(target_ids or ())} targets, preset={preset}"
    )

    original_advance = page._advance_view_progress
    quiet_progress = os.getenv("PROFILE_TEAM_SCORE_QUIET", "") == "1"
    last_progress_bucket = -1

    def progress(key: str, value: int, label: str) -> None:
        nonlocal last_progress_bucket
        bucket = int(float(value))
        if not quiet_progress or bucket > last_progress_bucket:
            report(f"progress {value}% {label}")
            last_progress_bucket = max(last_progress_bucket, bucket)
        original_advance(key, value, label)

    page._advance_view_progress = progress
    plans = page._build_team_score_theory_plans(snapshot, target_ids, preset)
    report(
        f"plans ready: {len(plans)}, fallback={getattr(editor, '_extreme_parallel_last_fallback', '')!r}"
    )
    if os.getenv("PROFILE_TEAM_SCORE_DIGEST") == "1":
        summary = {
            source: {
                "dps": plan.get("dps"),
                "total": plan.get("total"),
                "raw_total": plan.get("raw_total"),
                "segments": len(plan.get("segments") or ()),
            }
            for source, plan in plans.items()
        }
        digest = hashlib.sha256(json.dumps(
            summary, sort_keys=True, ensure_ascii=True, default=str
        ).encode("utf-8")).hexdigest()
        report(f"plan digest={digest}; summary={summary!r}")
    if audited_specs:
        exact_groups: list[list[tuple[str, str]]] = []
        raw_groups: list[list[tuple[str, str]]] = []
        for ignore_glyphs, groups in ((False, exact_groups), (True, raw_groups)):
            representatives: list[dict] = []
            for phase, token, spec in audited_specs:
                comparable = dict(spec)
                if ignore_glyphs:
                    comparable.pop("timing_ablation_glyphs", None)
                comparable["_summary_only"] = "timing_ablation_glyphs" in spec
                for representative, group in zip(representatives, groups):
                    if representative == comparable:
                        group.append((phase, token))
                        break
                else:
                    representatives.append(comparable)
                    groups.append([(phase, token)])
        report(
            f"task audit: {len(audited_specs)} calculated specs, "
            f"exact duplicates={sum(len(group)-1 for group in exact_groups)}, "
            f"raw-axis duplicates={sum(len(group)-1 for group in raw_groups)}"
        )
        for group in raw_groups:
            if len(group) > 1:
                report(f"raw-axis equivalent: {group!r}")
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
