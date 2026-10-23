from __future__ import annotations

import argparse
import copy
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.app import (  # noqa: E402
    BuildEditor,
    FREE_STAT_PERCENT_ATTRS,
    as_number,
)
from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, norm  # noqa: E402


ELEMENT_ATTRS = ("火攻%", "水攻%", "光攻%", "暗攻%")
DEFAULT_SEED = 20260727


def _set_spin(widget: Any, value: float) -> None:
    if not isinstance(widget, QDoubleSpinBox):
        return
    widget.blockSignals(True)
    widget.setValue(float(value))
    widget.blockSignals(False)


def _clear_skill_buffs(editor: BuildEditor) -> None:
    for row in range(editor.buff_table.rowCount()):
        skill = editor.buff_table.cellWidget(row, 0)
        level = editor.buff_table.cellWidget(row, 1)
        if isinstance(skill, QComboBox):
            editor._set_combo_current(skill, "无")
        if isinstance(level, QComboBox):
            editor._replace_combo_values(level, ["无"], "无")


def _set_all_cards_l(editor: BuildEditor) -> None:
    for row in range(editor.card_table.rowCount()):
        grade = editor.card_table.cellWidget(row, 1)
        if isinstance(grade, QComboBox):
            editor._set_combo_current(grade, "L")


def _set_glyph_attack_extra(editor: BuildEditor) -> None:
    attack = editor._job_profile().get("attack_attr") or "物攻"
    if isinstance(editor.auto_glyph_a_extra, QComboBox):
        editor._set_combo_current(editor.auto_glyph_a_extra, attack)
    editor.apply_auto_glyph_a_extra()


def _equalize_elements(editor: BuildEditor, calculator: BuildCalculator) -> None:
    build = editor.collect_build()
    _rows, summary = calculator.calculate(build)
    values = {attr: as_number(summary.get(attr)) for attr in ELEMENT_ATTRS}
    target = max(values.values(), default=0.0)
    for row, attr in enumerate(FREE_STAT_PERCENT_ATTRS):
        if attr in ELEMENT_ATTRS:
            _set_spin(
                editor.free_stat_percent_table.cellWidget(row, 1),
                max(0.0, target - values.get(attr, 0.0)),
            )
    editor.refresh()


def _capture_skill_metrics(editor: BuildEditor) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    original_burst = editor._update_skill_burst_sim_preview
    original_long = editor._update_skill_long_sim_preview

    def capture(metrics: list[dict[str, Any]], *_args: Any, **_kwargs: Any) -> tuple[list[Any], float]:
        captured[:] = copy.deepcopy(metrics)
        return [], 0.0

    try:
        editor._update_skill_burst_sim_preview = capture
        editor._update_skill_long_sim_preview = lambda *_args, **_kwargs: None
        editor.auto_fill_skill_sim()
    finally:
        editor._update_skill_burst_sim_preview = original_burst
        editor._update_skill_long_sim_preview = original_long
    return captured


def _timeline_score(segments: list[dict[str, Any]], window: float) -> float:
    return sum(
        max(0.0, as_number(segment.get("effective_score")))
        for segment in segments
        if norm(segment.get("kind")) != "invalid"
        and as_number(segment.get("start")) < window - 1e-6
    )


def _optimizer_snapshot(editor: BuildEditor) -> dict[str, Any]:
    stats = copy.deepcopy(getattr(editor, "_skill_burst_local_optimizer_last_stats", {}))
    return {
        "enabled": bool(stats.get("enabled")),
        "evaluations": int(as_number(stats.get("evaluations"))),
        "accepted": int(as_number(stats.get("accepted"))),
        "accepted_moves": list(stats.get("accepted_moves", [])),
        "elapsed": as_number(stats.get("elapsed")),
        "cached": bool(stats.get("cached")),
    }


def _run_long(
    editor: BuildEditor,
    metrics: list[dict[str, Any]],
    window: float,
    enabled: bool,
) -> dict[str, Any]:
    schedule = editor._skill_long_schedule(window)
    editor._skill_burst_disable_local_optimizer = not enabled
    editor._skill_burst_force_local_optimizer = enabled
    editor._skill_burst_local_optimizer_cache = {}
    editor._skill_burst_schedule_override = schedule
    editor.__dict__.pop("_skill_burst_mode_override", None)
    started = time.perf_counter()
    try:
        segments, generated_window = editor._update_skill_burst_sim_preview(metrics, render_ui=False)
    finally:
        editor.__dict__.pop("_skill_burst_schedule_override", None)
    elapsed = time.perf_counter() - started
    score = _timeline_score(segments, generated_window)
    return {
        "score": score,
        "average": score / max(1e-9, generated_window),
        "segments": sum(1 for item in segments if not item.get("timeline_hidden")),
        "window": generated_window,
        "elapsed": elapsed,
        "optimizer": _optimizer_snapshot(editor),
    }


def _run_team(
    editor: BuildEditor,
    metrics: list[dict[str, Any]],
    context: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    editor._skill_burst_disable_local_optimizer = not enabled
    editor._skill_burst_force_local_optimizer = enabled
    editor._skill_burst_local_optimizer_cache = {}
    started = time.perf_counter()
    result = editor._team_practical_simulate(metrics, context)
    elapsed = time.perf_counter() - started
    segments = list(result.get("segments", []))
    window = as_number(result.get("window"))
    score = _timeline_score(segments, window)
    return {
        "score": score,
        "average": score / max(1e-9, window),
        "segments": sum(1 for item in segments if not item.get("timeline_hidden")),
        "window": window,
        "elapsed": elapsed,
        "effects": len(context.get("effects", [])),
        "effect_windows": len(context.get("windows", [])),
        "optimizer": _optimizer_snapshot(editor),
    }


def _gain_percent(before: float, after: float) -> float:
    return (after / before - 1.0) * 100.0 if before > 1e-9 else 0.0


def _display_job(job: str) -> str:
    # 曜 has two practical roles in team simulation. Output曜 is the fair target
    # when measuring the profession's own rotation; its real job remains 曜.
    return "输出曜" if norm(job) == "曜" else job


def _make_team(all_jobs: list[str], target: str, index: int, seed: int) -> list[str]:
    display_target = _display_job(target)
    pool = [_display_job(job) for job in all_jobs if norm(job) != norm(target)]
    rng = random.Random(seed + index * 1009)
    return [display_target, *rng.sample(pool, 7)]


def _prepare_editor(
    app: QApplication,
    store: DataStore,
    calculator: BuildCalculator,
    first_job: str,
    second_job: str,
) -> tuple[BuildEditor, list[dict[str, Any]]]:
    build = copy.deepcopy(store.defaults["build1"])
    build["first_job"] = first_job
    build["second_job"] = second_job
    build["buffs"] = []
    editor = BuildEditor(second_job, store, calculator, build)
    editor._export_skill_build_page = editor._skill_build_page()
    editor._export_skill_sim_page = editor._skill_sim_page()
    editor._export_team_sim_page = editor._team_sim_page()
    app.processEvents()

    editor.auto_equip("60L", "all")
    _set_all_cards_l(editor)
    editor.auto_glyphs()
    _set_glyph_attack_extra(editor)
    editor.auto_runes()
    editor.auto_misc()
    _clear_skill_buffs(editor)
    _equalize_elements(editor, calculator)
    metrics = _capture_skill_metrics(editor)
    editor.refresh()
    return editor, metrics


def benchmark_job(
    app: QApplication,
    store: DataStore,
    calculator: BuildCalculator,
    first_job: str,
    second_job: str,
    all_jobs: list[str],
    index: int,
    seed: int,
    window: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    editor, metrics = _prepare_editor(app, store, calculator, first_job, second_job)
    if not metrics:
        raise RuntimeError("技能模拟没有生成任何技能数据")

    team = _make_team(all_jobs, second_job, index, seed)
    editor._apply_team_members(team)
    editor._auto_populate_team_effects_from_members(schedule=False)
    app.processEvents()
    context = editor._team_practical_runtime_context(metrics, window)

    long_before = _run_long(editor, metrics, window, False)
    long_after = _run_long(editor, metrics, window, True)
    team_before = _run_team(editor, metrics, context, False)
    team_after = _run_team(editor, metrics, context, True)
    row = {
        "index": index,
        "first_job": first_job,
        "second_job": second_job,
        "team_target": _display_job(second_job),
        "team": team,
        "metric_count": len(metrics),
        "long_before": long_before,
        "long_after": long_after,
        "long_gain_percent": _gain_percent(long_before["score"], long_after["score"]),
        "team_before": team_before,
        "team_after": team_after,
        "team_gain_percent": _gain_percent(team_before["score"], team_after["score"]),
        "total_elapsed": time.perf_counter() - started,
    }
    editor.deleteLater()
    app.processEvents()
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare baseline and local-optimized skill timelines.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--job", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    jobs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second and second != "无"
    ]
    if args.job:
        jobs = [item for item in jobs if norm(item[1]) == norm(args.job)]
    indexed_jobs = [
        (index, first, second)
        for index, (first, second) in enumerate(jobs)
        if index % max(1, args.shards) == args.shard
    ]
    all_jobs = [second for _first, second in jobs] if not args.job else [
        second
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second and second != "无"
    ]

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for done, (index, first, second) in enumerate(indexed_jobs, 1):
        try:
            row = benchmark_job(
                app,
                store,
                calculator,
                first,
                second,
                all_jobs,
                index,
                args.seed,
                args.window,
            )
            rows.append(row)
            print(
                f"[{done}/{len(indexed_jobs)}] {second}: "
                f"long {row['long_gain_percent']:+.4f}% / "
                f"team {row['team_gain_percent']:+.4f}% / "
                f"{row['total_elapsed']:.2f}s",
                flush=True,
            )
        except Exception as exc:
            errors.append({"first_job": first, "second_job": second, "error": repr(exc)})
            print(f"[{done}/{len(indexed_jobs)}] {second}: ERROR {exc!r}", flush=True)

    payload = {
        "seed": args.seed,
        "window": args.window,
        "shard": args.shard,
        "shards": args.shards,
        "rows": rows,
        "errors": errors,
    }
    output = args.output or ROOT / "outputs" / "benchmarks" / f"timeline_optimizer_shard_{args.shard}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {output}", flush=True)


if __name__ == "__main__":
    main()
