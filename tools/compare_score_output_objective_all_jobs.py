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
from dragon_nest_builder.timeline_parallel import reset_timeline_process_pool  # noqa: E402
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService  # noqa: E402


DEFAULT_TEAM = ["噬魂者", "黑暗女王", "风行者", "圣徒", "炼金圣士", "冰灵", "剑皇", "圣骑士"]
DEFAULT_BLOCKED = [
    {"start": 60.0, "end": 90.0},
    {"start": 180.0, "end": 215.0},
    {"start": 360.0, "end": 400.0},
    {"start": 520.0, "end": 545.0},
]


def all_jobs(store: DataStore) -> list[str]:
    return [
        norm(second)
        for first in store.first_jobs()
        for second in store.second_jobs(first)
        if norm(second)
    ]


def team_for(job: str, jobs: list[str]) -> list[str]:
    pool = [*DEFAULT_TEAM, *jobs]
    result = [job]
    for candidate in pool:
        if candidate and candidate not in result:
            result.append(candidate)
        if len(result) >= 8:
            break
    return result


def objective_profile(editor: Any, owner: str, team: list[str]) -> dict[str, Any]:
    members = []
    for job in team:
        if job == owner:
            continue
        profile = editor._extreme_profile_for_job(job)
        attack_attrs = [
            norm(value)
            for value in profile.get("attack_attrs", ())
            if norm(value) in {"物攻", "魔攻"}
        ]
        if not attack_attrs:
            attack = norm(profile.get("attack_attr"))
            attack_attrs = [attack if attack in {"物攻", "魔攻"} else "物攻"]
        members.append({
            "job": job,
            "weight": 1.0,
            "physical_share": attack_attrs.count("物攻") / len(attack_attrs),
            "magic_share": attack_attrs.count("魔攻") / len(attack_attrs),
            "main_stat": norm(profile.get("main_stat")),
            "element": norm(profile.get("element")),
        })
    return {
        "owner_job": owner,
        "owner_damage": 1.0,
        "other_damage": float(len(members)),
        "members": members,
        "element_resist": 20.0,
        "monster_defense": 15000.0,
        "crit_resist": 19000.0,
        "freeze_coefficient": 0.30,
        "base_element_attack": 42.0,
    }


def scale_objective(profile: dict[str, Any], scale: float) -> dict[str, Any]:
    result = copy.deepcopy(profile)
    for member in result.get("members", ()):
        if isinstance(member, dict):
            member["weight"] = as_number(member.get("weight")) * scale
    result["other_damage"] = as_number(result.get("other_damage")) * scale
    return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    segments = [
        row
        for row in result.get("segments", ())
        if isinstance(row, dict)
        and not row.get("timeline_hidden")
        and norm(row.get("kind")) != "invalid"
    ]
    counts = Counter(norm(row.get("skill")) for row in segments if norm(row.get("skill")))
    damage = Counter()
    for row in segments:
        damage[norm(row.get("skill"))] += max(0.0, as_number(row.get("effective_score")))
    return {
        "total": max(0.0, as_number(result.get("total"))),
        "casts": len(segments),
        "skill_counts": dict(sorted(counts.items())),
        "skill_damage": dict(sorted(damage.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=float, default=600.0)
    parser.add_argument("--objective-scale", type=float, default=0.30)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "verification" / "score_output_objective_all_jobs.json")
    args = parser.parse_args()

    store = DataStore.load()
    calculator = BuildCalculator(store)
    editor = LegacyTimelineService(store, calculator).extreme_runtime(
        copy.deepcopy(store.defaults["build1"])
    )
    jobs = all_jobs(store)
    specs_old: list[dict[str, Any]] = []
    specs_new: list[dict[str, Any]] = []

    for index, job in enumerate(jobs, 1):
        team = team_for(job, jobs)
        effects = editor._extreme_team_effects_for_jobs(team)
        build = editor._extreme_team_score_build_for_job(job)
        base = {
            "job": job,
            "team": team,
            "effects": effects,
            "hand_multiplier": 1.0,
            "window": args.window,
            "include_display_windows": False,
            "build_override": build,
            "blocked_intervals": [row for row in DEFAULT_BLOCKED if row["start"] < args.window],
            "partial_cast_schedule": False,
            "same_environment_theory": True,
            "long_sim_planner": True,
            "score_refinement": False,
        }
        specs_old.append({**copy.deepcopy(base), "team_objective_profile": objective_profile(editor, job, team)})
        specs_new.append({
            **copy.deepcopy(base),
            "team_objective_profile": scale_objective(
                objective_profile(editor, job, team), args.objective_scale
            ),
        })
        print(f"[prepare {index:02d}/{len(jobs):02d}] {job}", flush=True)

    def run(label: str, specs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
        reset_timeline_process_pool()
        editor._extreme_timeline_result_cache = {}
        started = time.perf_counter()

        def progress(done: int, _index: int, job: str) -> None:
            print(f"[{label} {done:02d}/{len(specs):02d}] {job}", flush=True)

        results = editor._extreme_timeline_tasks_parallel(specs, progress_callback=progress)
        return results, time.perf_counter() - started

    old_results, old_seconds = run("old", specs_old)
    new_results, new_seconds = run("new", specs_new)
    rows = []
    for job, old_result, new_result in zip(jobs, old_results, new_results):
        old = summarize(old_result)
        new = summarize(new_result)
        old_total = old["total"]
        changed_skills = []
        all_skill_names = sorted(set(old["skill_counts"]) | set(new["skill_counts"]))
        for skill in all_skill_names:
            old_count = int(old["skill_counts"].get(skill, 0))
            new_count = int(new["skill_counts"].get(skill, 0))
            old_damage = as_number(old["skill_damage"].get(skill))
            new_damage = as_number(new["skill_damage"].get(skill))
            if old_count != new_count or abs(old_damage - new_damage) > 1e-6:
                changed_skills.append({
                    "skill": skill,
                    "old_casts": old_count,
                    "new_casts": new_count,
                    "cast_delta": new_count - old_count,
                    "old_damage": old_damage,
                    "new_damage": new_damage,
                })
        row = {
            "job": job,
            "old_total": old_total,
            "new_total": new["total"],
            "change_percent": ((new["total"] / old_total - 1.0) * 100.0 if old_total > 1e-9 else 0.0),
            "old_casts": old["casts"],
            "new_casts": new["casts"],
            "cast_delta": new["casts"] - old["casts"],
            "changed_skills": changed_skills,
        }
        rows.append(row)
        print(f"{job}: {row['change_percent']:+.3f}% | casts {old['casts']} -> {new['casts']}", flush=True)

    payload = {
        "window": args.window,
        "objective_scale": args.objective_scale,
        "blocked_intervals": DEFAULT_BLOCKED,
        "jobs": len(rows),
        "old_seconds": old_seconds,
        "new_seconds": new_seconds,
        "aggregate_old_total": sum(row["old_total"] for row in rows),
        "aggregate_new_total": sum(row["new_total"] for row in rows),
        "aggregate_change_percent": (
            sum(row["new_total"] for row in rows) / max(1e-9, sum(row["old_total"] for row in rows)) * 100.0 - 100.0
        ),
        "improved_jobs": sum(row["change_percent"] > 0.01 for row in rows),
        "regressed_jobs": sum(row["change_percent"] < -0.01 for row in rows),
        "unchanged_jobs": sum(abs(row["change_percent"]) <= 0.01 for row in rows),
        "rows": sorted(rows, key=lambda row: row["change_percent"], reverse=True),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output, flush=True)
    reset_timeline_process_pool()
    return 0


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
