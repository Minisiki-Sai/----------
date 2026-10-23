from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator  # noqa: E402
from dragon_nest_builder.data_loader import DataStore, as_number, norm  # noqa: E402
from dragon_nest_builder.timeline_parallel import (  # noqa: E402
    reset_timeline_process_pool,
)
from dragon_nest_builder.v410_legacy_timeline import (  # noqa: E402
    LegacyTimelineService,
)
from tools.compare_score_output_objective_all_jobs import (  # noqa: E402
    DEFAULT_BLOCKED,
    all_jobs,
    objective_profile,
    scale_objective,
    team_for,
)


def _summarize(result: dict[str, Any]) -> dict[str, Any]:
    segments = [
        row
        for row in result.get("segments", ())
        if isinstance(row, dict)
        and not row.get("timeline_hidden")
        and norm(row.get("kind")) != "invalid"
    ]
    counts = Counter(
        norm(row.get("skill")) for row in segments if norm(row.get("skill"))
    )
    damage = Counter()
    for row in segments:
        damage[norm(row.get("skill"))] += max(
            0.0,
            as_number(row.get("effective_score")),
        )
    return {
        "total": max(0.0, as_number(result.get("total"))),
        "support_order_quality": copy.deepcopy(
            result.get("_planner_settings", {}).get(
                "support_order_quality",
                {},
            )
        ),
        "joint_support_order_quality": copy.deepcopy(
            result.get("_planner_settings", {}).get(
                "joint_support_order_quality",
                {},
            )
        ),
        "casts": len(segments),
        "skill_counts": dict(sorted(counts.items())),
        "skill_damage": dict(sorted(damage.items())),
        "opening": [
            {
                "start": round(as_number(row.get("start")), 4),
                "end": round(as_number(row.get("end")), 4),
                "skill": norm(row.get("skill")),
                "kind": norm(row.get("kind")),
            }
            for row in segments[:12]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare legacy fixed profession openers with the generic "
            "weighted-team objective ordering used by score theory axes."
        )
    )
    parser.add_argument("--window", type=float, default=180.0)
    parser.add_argument(
        "--jobs",
        nargs="*",
        default=(),
        help="optional profession subset",
    )
    parser.add_argument("--objective-scale", type=float, default=0.30)
    parser.add_argument(
        "--objective-mode",
        choices=("personal", "team"),
        default="team",
        help=(
            "personal compares the owner's unified objective without teammate "
            "weights; team adds the weighted seven-member objective"
        ),
    )
    parser.add_argument(
        "--allow-regression",
        action="store_true",
        help="write the report but return success even when a profession regresses",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "outputs"
            / "verification"
            / "dynamic_team_opening_all_jobs.json"
        ),
    )
    args = parser.parse_args()

    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor = LegacyTimelineService(store, calculator).extreme_runtime(
        copy.deepcopy(store.defaults["build1"])
    )
    requested_jobs = {norm(job) for job in args.jobs if norm(job)}
    jobs = [
        job
        for job in all_jobs(store)
        if not requested_jobs or norm(job) in requested_jobs
    ]
    specs: list[dict[str, Any]] = []
    for index, job in enumerate(jobs, 1):
        team = team_for(job, jobs)
        effects = editor._extreme_team_effects_for_jobs(team)
        build = editor._extreme_team_score_build_for_job(job)
        profile = (
            scale_objective(
                objective_profile(editor, job, team),
                args.objective_scale,
            )
            if args.objective_mode == "team"
            else None
        )
        specs.append({
            "job": job,
            "team": team,
            "effects": effects,
            "hand_multiplier": 1.0,
            "window": args.window,
            "include_display_windows": False,
            "build_override": build,
            "blocked_intervals": [
                row for row in DEFAULT_BLOCKED if row["start"] < args.window
            ],
            "partial_cast_schedule": False,
            "same_environment_theory": True,
            "long_sim_planner": True,
            "score_refinement": False,
            "team_objective_profile": profile,
        })
        print(f"[prepare {index:02d}/{len(jobs):02d}] {job}", flush=True)

    def run(
        label: str,
        legacy_opening: bool,
    ) -> tuple[list[dict[str, Any]], float]:
        editor._skill_burst_disable_team_objective_opening_order = (
            legacy_opening
        )
        editor._skill_burst_disable_team_objective_pair_order = legacy_opening
        editor._skill_burst_disable_joint_support_order = legacy_opening
        reset_timeline_process_pool()
        editor._extreme_timeline_result_cache = {}
        started = time.perf_counter()

        def progress(done: int, _index: int, job: str) -> None:
            print(f"[{label} {done:02d}/{len(specs):02d}] {job}", flush=True)

        results = editor._extreme_timeline_tasks_parallel(
            specs,
            progress_callback=progress,
        )
        return results, time.perf_counter() - started

    legacy_results, legacy_seconds = run("legacy", True)
    dynamic_results, dynamic_seconds = run("dynamic", False)

    rows: list[dict[str, Any]] = []
    for job, legacy_result, dynamic_result in zip(
        jobs,
        legacy_results,
        dynamic_results,
    ):
        legacy = _summarize(legacy_result)
        dynamic = _summarize(dynamic_result)
        legacy_total = legacy["total"]
        dynamic_total = dynamic["total"]
        delta = dynamic_total - legacy_total
        percent = delta / legacy_total * 100.0 if legacy_total > 1e-9 else 0.0
        changed_skills = []
        skill_names = sorted(
            set(legacy["skill_counts"]) | set(dynamic["skill_counts"])
        )
        for skill in skill_names:
            legacy_count = int(legacy["skill_counts"].get(skill, 0))
            dynamic_count = int(dynamic["skill_counts"].get(skill, 0))
            legacy_damage = as_number(legacy["skill_damage"].get(skill))
            dynamic_damage = as_number(dynamic["skill_damage"].get(skill))
            if (
                legacy_count != dynamic_count
                or abs(dynamic_damage - legacy_damage) > 1e-6
            ):
                changed_skills.append({
                    "skill": skill,
                    "legacy_casts": legacy_count,
                    "dynamic_casts": dynamic_count,
                    "cast_delta": dynamic_count - legacy_count,
                    "legacy_damage": legacy_damage,
                    "dynamic_damage": dynamic_damage,
                    "damage_delta": dynamic_damage - legacy_damage,
                })
        rows.append({
            "job": job,
            "legacy_total": legacy_total,
            "dynamic_total": dynamic_total,
            "delta": delta,
            "change_percent": percent,
            "legacy_casts": legacy["casts"],
            "dynamic_casts": dynamic["casts"],
            "legacy_opening": legacy["opening"],
            "dynamic_opening": dynamic["opening"],
            "opening_changed": legacy["opening"] != dynamic["opening"],
            "support_order_quality": dynamic["support_order_quality"],
            "joint_support_order_quality": dynamic[
                "joint_support_order_quality"
            ],
            "changed_skills": changed_skills,
        })
        print(
            f"{job}: {percent:+.4f}% | "
            f"casts {legacy['casts']} -> {dynamic['casts']} | "
            f"opening {'changed' if legacy['opening'] != dynamic['opening'] else 'same'}",
            flush=True,
        )

    aggregate_legacy = sum(row["legacy_total"] for row in rows)
    aggregate_dynamic = sum(row["dynamic_total"] for row in rows)
    regressed_jobs = sum(row["change_percent"] < -0.01 for row in rows)
    payload = {
        "window": args.window,
        "objective_mode": args.objective_mode,
        "objective_scale": args.objective_scale,
        "jobs": len(rows),
        "legacy_seconds": legacy_seconds,
        "dynamic_seconds": dynamic_seconds,
        "aggregate_legacy_total": aggregate_legacy,
        "aggregate_dynamic_total": aggregate_dynamic,
        "aggregate_change_percent": (
            (aggregate_dynamic / aggregate_legacy - 1.0) * 100.0
            if aggregate_legacy > 1e-9
            else 0.0
        ),
        "opening_changed_jobs": sum(row["opening_changed"] for row in rows),
        "improved_jobs": sum(row["change_percent"] > 0.01 for row in rows),
        "regressed_jobs": regressed_jobs,
        "unchanged_jobs": sum(abs(row["change_percent"]) <= 0.01 for row in rows),
        "acceptance_passed": regressed_jobs == 0,
        "rows": sorted(rows, key=lambda row: row["change_percent"], reverse=True),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output, flush=True)
    reset_timeline_process_pool()
    if regressed_jobs and not args.allow_regression:
        print(
            f"acceptance failed: {regressed_jobs} profession(s) regressed by more than 0.01%",
            flush=True,
        )
        return 3
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
