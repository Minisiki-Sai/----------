from __future__ import annotations

import argparse
import copy
import json
import pickle
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, as_number, norm
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


def summarize(
    result: dict[str, Any],
    detail_skills: set[str] | None = None,
) -> dict[str, Any]:
    segments = [row for row in result.get("segments", ()) if isinstance(row, dict)]
    counts = Counter(norm(row.get("skill")) for row in segments if norm(row.get("skill")))
    damage = Counter()
    for row in segments:
        damage[norm(row.get("skill"))] += max(0.0, as_number(row.get("effective_score")))
    summary = {
        "total": max(0.0, as_number(result.get("total"))),
        "segments": len(segments),
        "counts": dict(counts),
        "damage": dict(damage),
    }
    if detail_skills:
        summary["casts"] = [
            {
                "skill": norm(row.get("skill")),
                "start": as_number(row.get("start")),
                "end": as_number(row.get("end")),
                "ready": as_number(row.get("cooldown_ready_at")),
                "score": as_number(row.get("effective_score")),
                "cdr": as_number(row.get("cdr")),
            }
            for row in segments
            if norm(row.get("skill")) in detail_skills
        ]
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_dump", type=Path)
    parser.add_argument("job")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--skills", default="")
    parser.add_argument("--ready-debt-weight", type=float)
    parser.add_argument("--ready-debt-dpa-floor", type=float)
    parser.add_argument("--ready-debt-cycle-floor", type=float)
    parser.add_argument("--ready-deadline", type=float)
    parser.add_argument(
        "--objective-scales",
        default="",
        help="Comma-separated multipliers for the captured team-objective member weights.",
    )
    args = parser.parse_args()

    payload = pickle.loads(args.spec_dump.read_bytes())
    job = norm(args.job)
    spec = next(
        row
        for row in payload.get("specs", ())
        if norm(row.get("job")) == job
        and isinstance(row.get("team_objective_profile"), dict)
        and row.get("long_sim_planner")
    )
    variants = {
        "baseline": copy.deepcopy(spec),
        "refined": {
            **copy.deepcopy(spec),
            "score_refinement": True,
        },
        "personal_objective": {
            **copy.deepcopy(spec),
            "team_objective_profile": None,
        },
        "refined_personal": {
            **copy.deepcopy(spec),
            "score_refinement": True,
            "team_objective_profile": None,
        },
        "continuous_personal": {
            **copy.deepcopy(spec),
            "team_objective_profile": None,
            "blocked_intervals": [],
        },
        "isolated_blocked": {
            **copy.deepcopy(spec),
            "effects": [],
            "cast_schedule": {},
            "same_environment_theory": False,
            "score_actual_casts_by_source": {},
            "team_objective_profile": None,
            "boss_hp_segments": None,
        },
        "effects_without_cdr": {
            **copy.deepcopy(spec),
            "effects": [
                copy.deepcopy(row)
                for row in spec.get("effects", ())
                if not any(
                    token in str(row.get("skill") or "")
                    for token in ("精神爆发", "时间加速", "鸡尾酒")
                )
            ],
            "cast_schedule": {
                key: copy.deepcopy(value)
                for key, value in spec.get("cast_schedule", {}).items()
                if not any(token in str(key[1]) for token in ("精神爆发", "时间加速", "鸡尾酒"))
            },
            "team_objective_profile": None,
        },
    }
    for raw_scale in args.objective_scales.split(","):
        if not raw_scale.strip():
            continue
        scale = max(0.0, float(raw_scale))
        variant = copy.deepcopy(spec)
        profile = variant.get("team_objective_profile")
        if isinstance(profile, dict):
            for member in profile.get("members", ()):
                if isinstance(member, dict):
                    member["weight"] = max(0.0, float(member.get("weight", 0.0))) * scale
            profile["other_damage"] = max(0.0, float(profile.get("other_damage", 0.0))) * scale
        variants[f"objective_{scale:g}"] = variant
    if args.baseline_only:
        variants = {"baseline": variants["baseline"]}
    store = DataStore.load()
    runtime = LegacyTimelineService(store, BuildCalculator(store)).extreme_runtime(
        copy.deepcopy(store.defaults["build1"])
    )
    for argument, attribute in (
        (args.ready_debt_weight, "_skill_burst_fast_mechanism_ready_debt_weight"),
        (args.ready_debt_dpa_floor, "_skill_burst_fast_mechanism_ready_debt_dpa_floor"),
        (args.ready_debt_cycle_floor, "_skill_burst_fast_mechanism_ready_debt_cycle_floor"),
        (args.ready_debt_weight, "_skill_burst_new_scheme_1_ready_debt_weight"),
        (args.ready_debt_dpa_floor, "_skill_burst_new_scheme_1_ready_debt_dpa_floor"),
        (args.ready_debt_cycle_floor, "_skill_burst_new_scheme_1_ready_debt_cycle_floor"),
    ):
        if argument is not None:
            setattr(runtime, attribute, argument)
    if args.ready_deadline is not None:
        runtime._skill_burst_ready_deadlines_by_job = {
            "火舞": {"火径": max(0.0, args.ready_deadline)}
        }
    results: dict[str, Any] = {}
    detail_skills = {
        norm(skill) for skill in args.skills.split(",") if norm(skill)
    }
    for name, variant in variants.items():
        runtime._extreme_timeline_result_cache = {}
        started = time.perf_counter()
        result = runtime._extreme_timeline_result_from_spec(variant)
        results[name] = {
            **summarize(result, detail_skills),
            "seconds": time.perf_counter() - started,
        }
    baseline = max(1e-9, as_number(results["baseline"]["total"]))
    for row in results.values():
        row["gain_percent"] = (as_number(row["total"]) / baseline - 1.0) * 100.0
    rendered = json.dumps({"job": job, "variants": results}, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
