from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from tools.benchmark_pure_scheme_1_all_jobs import (  # noqa: E402
    SHORT_MODES,
    _configure,
)
from tools.benchmark_timeline_optimizer import (  # noqa: E402
    DEFAULT_SEED,
    _make_team,
    _prepare_editor,
    _timeline_score,
)


DEFAULT_TARGETS = (
    "雷神:long",
    "曜:burst",
    "圣骑士:accel",
    "冰灵:burst_cocktail",
    "刀锋舞者:accel_burst",
    "十字军:team",
)


def _visible(segments: list[dict[str, Any]], window: float) -> list[dict[str, Any]]:
    return [
        segment
        for segment in segments
        if not segment.get("timeline_hidden")
        and norm(segment.get("kind")) != "invalid"
        and as_number(segment.get("start")) < window - 1e-6
    ]


def _summarize(segments: list[dict[str, Any]], window: float) -> dict[str, Any]:
    visible = _visible(segments, window)
    skill_rows: dict[str, dict[str, float]] = defaultdict(
        lambda: {"casts": 0.0, "score": 0.0, "time": 0.0}
    )
    occupied: list[tuple[float, float]] = []
    for segment in visible:
        skill = norm(segment.get("skill")) or norm(segment.get("kind")) or "未命名"
        start = max(0.0, as_number(segment.get("start")))
        end = min(window, max(start, as_number(segment.get("end"), start)))
        row = skill_rows[skill]
        row["casts"] += 1
        row["score"] += max(0.0, as_number(segment.get("effective_score")))
        row["time"] += max(0.0, end - start)
        if end > start + 1e-6:
            occupied.append((start, end))

    merged: list[list[float]] = []
    for start, end in sorted(occupied):
        if not merged or start > merged[-1][1] + 1e-6:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    occupied_time = sum(end - start for start, end in merged)
    score = _timeline_score(segments, window)
    ranked = sorted(
        (
            {
                "skill": skill,
                "casts": int(values["casts"]),
                "score": round(values["score"], 4),
                "share": round(values["score"] / score * 100, 3) if score else 0.0,
                "time": round(values["time"], 4),
            }
            for skill, values in skill_rows.items()
        ),
        key=lambda row: (row["score"], row["casts"]),
        reverse=True,
    )
    return {
        "score": score,
        "average": score / max(1e-9, window),
        "window": window,
        "segments": len(visible),
        "occupied": occupied_time,
        "idle": max(0.0, window - occupied_time),
        "skills": ranked,
        "support_casts": [
            {
                "start": round(as_number(segment.get("start")), 4),
                "end": round(as_number(segment.get("end")), 4),
                "skill": norm(segment.get("skill")),
                "score": round(
                    max(0.0, as_number(segment.get("effective_score"))),
                    4,
                ),
            }
            for segment in visible
            if norm(segment.get("kind")) == "support"
        ],
        "all_casts": [
            {
                "start": round(as_number(segment.get("start")), 4),
                "end": round(as_number(segment.get("end")), 4),
                "skill": norm(segment.get("skill")),
                "score": round(
                    max(0.0, as_number(segment.get("effective_score"))),
                    4,
                ),
                "kind": norm(segment.get("kind")),
            }
            for segment in visible
        ],
        "opening": [
            {
                "start": round(as_number(segment.get("start")), 4),
                "end": round(as_number(segment.get("end")), 4),
                "skill": norm(segment.get("skill")),
                "score": round(max(0.0, as_number(segment.get("effective_score"))), 4),
                "kind": norm(segment.get("kind")),
            }
            for segment in visible[:30]
        ],
    }


def _generate(
    editor: Any,
    metrics: list[dict[str, Any]],
    scenario: str,
    scheme: str,
    team_context: dict[str, Any] | None,
    window: float,
) -> dict[str, Any]:
    _configure(editor, scheme)
    trace: list[dict[str, Any]] | None = [] if scheme == "pure_new" else None
    if trace is not None:
        editor._skill_burst_new_scheme_1_trace = trace
        editor._skill_burst_new_scheme_1_trace_support_skills = {
            "神圣化身",
            "审判力量",
            "勇敢",
            "幻影之舞",
            "弗利兹科之舞",
            "精神爆破EX",
            "神圣大地",
        }
    if scenario == "team":
        result = editor._team_practical_simulate(metrics, team_context or {})
        summary = _summarize(
            list(result.get("segments", [])),
            as_number(result.get("window"), window),
        )
        summary["trace"] = trace or []
        editor.__dict__.pop("_skill_burst_new_scheme_1_trace", None)
        editor.__dict__.pop("_skill_burst_new_scheme_1_trace_support_skills", None)
        return summary
    if scenario == "long":
        editor._skill_burst_schedule_override = editor._skill_long_schedule(window)
        editor.__dict__.pop("_skill_burst_mode_override", None)
    else:
        editor._skill_burst_mode_override = set(SHORT_MODES[scenario])
        editor.__dict__.pop("_skill_burst_schedule_override", None)
    try:
        segments, generated_window = editor._update_skill_burst_sim_preview(
            metrics,
            render_ui=False,
        )
    finally:
        editor.__dict__.pop("_skill_burst_schedule_override", None)
        editor.__dict__.pop("_skill_burst_mode_override", None)
    summary = _summarize(segments, generated_window)
    summary["trace"] = trace or []
    editor.__dict__.pop("_skill_burst_new_scheme_1_trace", None)
    editor.__dict__.pop("_skill_burst_new_scheme_1_trace_support_skills", None)
    return summary


def _parse_targets(values: list[str]) -> list[tuple[str, str]]:
    result = []
    for value in values:
        job, separator, scenario = value.partition(":")
        if not separator or scenario not in {"long", "team", *SHORT_MODES}:
            raise ValueError(f"Invalid target: {value}")
        result.append((norm(job), scenario))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose pure Scheme 1 losses.")
    parser.add_argument("targets", nargs="*", default=list(DEFAULT_TARGETS))
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "benchmarks" / "pure_scheme_1_loss_diagnostics.json",
    )
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    all_pairs = [
        (first, second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if second
    ]
    all_jobs = [second for _first, second in all_pairs]
    pair_by_job = {norm(second): (index, first, second) for index, (first, second) in enumerate(all_pairs)}
    payload: dict[str, Any] = {"window": args.window, "rows": []}

    for job, scenario in _parse_targets(args.targets):
        index, first_job, second_job = pair_by_job[job]
        editor, metrics = _prepare_editor(
            app,
            store,
            calculator,
            first_job,
            second_job,
        )
        try:
            team_context = None
            team: list[str] = []
            if scenario == "team":
                team = _make_team(all_jobs, second_job, index, args.seed)
                _configure(editor, "stable")
                editor._apply_team_members(team)
                editor._auto_populate_team_effects_from_members(schedule=False)
                app.processEvents()
                team_context = editor._team_practical_runtime_context(metrics, args.window)
            results = {
                scheme: _generate(
                    editor,
                    metrics,
                    scenario,
                    scheme,
                    team_context,
                    args.window,
                )
                for scheme in ("stable", "pure_new")
            }
            stable_score = results["stable"]["score"]
            pure_score = results["pure_new"]["score"]
            row = {
                "job": second_job,
                "scenario": scenario,
                "team": team,
                "gain_percent": (
                    (pure_score / stable_score - 1) * 100 if stable_score > 1e-9 else 0.0
                ),
                "results": results,
            }
            payload["rows"].append(row)
            print(
                f"{second_job}/{scenario}: {row['gain_percent']:+.3f}% "
                f"idle {results['stable']['idle']:.2f}s -> {results['pure_new']['idle']:.2f}s",
                flush=True,
            )
        finally:
            editor.deleteLater()
            app.processEvents()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
