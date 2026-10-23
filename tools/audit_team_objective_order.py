from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from dragon_nest_builder.app import BuildEditor
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, as_number, norm


DEFAULT_TEAM = (
    "十字军",
    "剑皇",
    "狙翎",
    "冰灵",
    "时空领主",
    "噬魂者",
    "圣徒",
    "毁灭者",
)


def all_jobs(store: DataStore) -> list[str]:
    return [
        job
        for first_job in store.first_jobs()
        for job in store.second_jobs(first_job)
        if norm(job)
    ]


def objective_profile(editor: BuildEditor, owner_job: str, team: list[str]) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    skipped_owner = False
    for job in team:
        if norm(job) == norm(owner_job) and not skipped_owner:
            skipped_owner = True
            continue
        profile = editor._extreme_profile_for_job(job)
        attacks = [
            norm(value)
            for value in profile.get("attack_attrs", ())
            if norm(value) in {"物攻", "魔攻"}
        ] or [norm(profile.get("attack_attr")) or "物攻"]
        members.append(
            {
                "job": norm(job),
                # The score planner deliberately uses 30% of captured teammate
                # damage as a near-tie scheduling objective.
                "weight": 0.30,
                "physical_share": attacks.count("物攻") / len(attacks),
                "magic_share": attacks.count("魔攻") / len(attacks),
                "main_stat": norm(profile.get("main_stat")),
                "element": norm(profile.get("element")),
            }
        )
    return {
        "owner_job": norm(owner_job),
        "members": members,
        "monster_defense": 3400.0,
        "element_resist": 20.0,
        "crit_resist": 8122.0,
        "freeze_coefficient": 0.30,
        "base_element_attack": 42.0,
    }


def compact_axis(result: dict[str, Any], seconds: float = 12.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in result.get("segments", ()):
        start = as_number(segment.get("start"))
        if start > seconds + 1e-9:
            continue
        rows.append(
            {
                "skill": norm(segment.get("skill")),
                "start": round(start, 4),
                "end": round(as_number(segment.get("end"), start), 4),
                "kind": norm(segment.get("kind")),
                "score": round(as_number(segment.get("effective_score")), 6),
            }
        )
    return rows


def cast_signature(result: dict[str, Any]) -> tuple[tuple[str, float, str], ...]:
    return tuple(
        (
            norm(row.get("skill")),
            round(as_number(row.get("start")), 4),
            norm(row.get("kind")),
        )
        for row in result.get("segments", ())
    )


def simulate(
    editor: BuildEditor,
    job: str,
    team: list[str],
    effects: list[dict[str, Any]],
    *,
    window: float,
    legacy_order: bool,
) -> dict[str, Any]:
    editor._skill_burst_disable_team_objective_opening_order = bool(legacy_order)
    editor._skill_burst_disable_team_objective_pair_order = bool(legacy_order)
    # These switches are runtime inputs but intentionally not product cache
    # dimensions. Clear only the final audit cache between A/B variants.
    editor._extreme_timeline_result_cache = {}
    return editor._extreme_team_timeline_result(
        job,
        team,
        effects,
        window=window,
        include_display_windows=False,
        long_sim_planner=True,
        score_refinement=False,
        team_objective_profile=objective_profile(editor, job, team),
        timing_summary_only=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare legacy fixed support openers with weighted team-objective ordering."
    )
    parser.add_argument("--jobs", nargs="*", default=[])
    parser.add_argument("--team", nargs="*", default=list(DEFAULT_TEAM))
    parser.add_argument("--window", type=float, default=30.0)
    parser.add_argument("--opening-seconds", type=float, default=12.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app = QApplication.instance() or QApplication([])
    store = DataStore.load()
    calculator = BuildCalculator(store)
    build = copy.deepcopy(store.defaults["build1"])
    editor = BuildEditor(norm(build.get("second_job")) or "十字军", store, calculator, build)
    try:
        jobs = [norm(job) for job in args.jobs if norm(job)] or all_jobs(store)
        team = [norm(job) for job in args.team if norm(job)]
        effects = editor._team_effects_for_jobs(team)
        report: dict[str, Any] = {
            "window": max(30.0, args.window),
            "team": team,
            "jobs": [],
        }
        for index, job in enumerate(jobs, 1):
            if job not in team:
                compared_team = [job, *team[:7]]
                compared_effects = editor._team_effects_for_jobs(compared_team)
            else:
                compared_team = team
                compared_effects = effects
            legacy = simulate(
                editor,
                job,
                compared_team,
                compared_effects,
                window=max(30.0, args.window),
                legacy_order=True,
            )
            weighted = simulate(
                editor,
                job,
                compared_team,
                compared_effects,
                window=max(30.0, args.window),
                legacy_order=False,
            )
            legacy_total = as_number(legacy.get("total"))
            weighted_total = as_number(weighted.get("total"))
            changed = cast_signature(legacy) != cast_signature(weighted)
            row = {
                "job": job,
                "changed": changed,
                "legacy_total": legacy_total,
                "weighted_total": weighted_total,
                "personal_total_delta_percent": (
                    (weighted_total / legacy_total - 1.0) * 100.0
                    if legacy_total > 1e-9
                    else 0.0
                ),
                "legacy_opening": compact_axis(legacy, args.opening_seconds),
                "weighted_opening": compact_axis(weighted, args.opening_seconds),
            }
            report["jobs"].append(row)
            print(
                f"[{index:02d}/{len(jobs):02d}] {job}: "
                f"{'changed' if changed else 'same'}; "
                f"personal {row['personal_total_delta_percent']:+.4f}%",
                flush=True,
            )
        report["summary"] = {
            "job_count": len(report["jobs"]),
            "changed_job_count": sum(bool(row["changed"]) for row in report["jobs"]),
            "changed_jobs": [row["job"] for row in report["jobs"] if row["changed"]],
        }
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload, encoding="utf-8")
        else:
            print(payload)
    finally:
        editor.deleteLater()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
