from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, as_number, norm
from dragon_nest_builder.shared import AUTO_EQUIP_TARGETS
from dragon_nest_builder.skill_glyph_policy import select_skill_glyphs
from dragon_nest_builder.v410_native_core import NativeSimulatorCore
import dragon_nest_builder.native.build_service as build_service_module


def clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key not in {"score", "local_score", "skill_key"}
        }
        for row in rows
        if isinstance(row, dict)
    ]


def row_keys(rows: list[dict[str, Any]]) -> tuple[tuple[str, str, float], ...]:
    return tuple(sorted(
        (
            norm(row.get("skill")),
            norm(row.get("effect")),
            round(max(0.0, as_number(row.get("value"))), 6),
        )
        for row in rows
        if isinstance(row, dict)
    ))


def standard_build(
    core: NativeSimulatorCore,
    first_job: str,
    second_job: str,
) -> dict[str, Any]:
    build = copy.deepcopy(core.store.defaults["build1"])
    build["first_job"] = first_job
    build["second_job"] = second_job
    core.apply_job_defaults(build, second_job)
    build = core.apply_build_preset(
        build,
        "equipment",
        {"level": "70", "target": AUTO_EQUIP_TARGETS["70"][0][0]},
    )
    build = core.apply_build_preset(build, "glyphs", {"aExtra": "自动"})
    build = core.apply_build_preset(build, "runes")
    build = core.apply_build_preset(build, "cards", {"grade": "L"})
    build = core.apply_build_preset(build, "buffs")
    return build


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "diagnostics" / "all_job_glyph_selector_compare.json",
    )
    parser.add_argument("--window", type=float, default=125.0)
    args = parser.parse_args()

    store = DataStore.load()
    core = NativeSimulatorCore(store, BuildCalculator(store))
    original_two_stage = build_service_module.select_skill_glyphs_two_stage
    selection_capture: dict[str, list[dict[str, Any]]] = {}
    active_job = ""

    def compare_selector(
        candidates: Any,
        count: int,
        *,
        score_key: str = "score",
        local_score_key: str = "local_score",
        forced: Any = (),
        fill_to_count: bool = False,
        challenger_count: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [copy.deepcopy(row) for row in candidates if isinstance(row, dict)]
        old_rows = select_skill_glyphs(
            rows,
            count,
            score_key=score_key,
            forced=forced,
            fill_to_count=fill_to_count,
        )
        new_rows = original_two_stage(
            rows,
            count,
            score_key=score_key,
            local_score_key=local_score_key,
            forced=forced,
            fill_to_count=fill_to_count,
            challenger_count=challenger_count,
        )
        selection_capture[active_job] = clean_rows(old_rows)
        return new_rows

    build_service_module.select_skill_glyphs_two_stage = compare_selector
    report_rows: list[dict[str, Any]] = []
    jobs = [
        (first_job, second_job)
        for first_job in store.first_jobs()
        for second_job in store.second_jobs(first_job)
    ]
    started = time.perf_counter()
    try:
        for index, (first_job, second_job) in enumerate(jobs, 1):
            active_job = second_job
            selection_capture.pop(second_job, None)
            build = standard_build(core, first_job, second_job)
            result = core.optimize_skill_glyphs(
                {
                    "build": build,
                    "count": 4,
                    "window": args.window,
                    "cocktail": True,
                    "supportTeamSize": 8,
                }
            )
            old_rows = selection_capture.get(second_job, [])
            new_rows = clean_rows(result.get("rows", []))
            changed = row_keys(old_rows) != row_keys(new_rows)
            old_total = None
            if changed:
                old_build = copy.deepcopy(build)
                old_build["skill_glyphs"] = {
                    "second_job": second_job,
                    "count": 4,
                    "rows": copy.deepcopy(old_rows),
                    "auto_selected": False,
                }
                old_total = as_number(
                    core.quick_timeline(
                        old_build,
                        {"window": args.window, "cocktail": True},
                    ).get("total")
                )
            baseline = as_number(result.get("baseline"))
            new_total = as_number(result.get("optimized"))
            row = {
                "firstJob": first_job,
                "job": second_job,
                "changed": changed,
                "old": old_rows,
                "new": new_rows,
                "baseline": baseline,
                "oldTotal": old_total,
                "newTotal": new_total,
                "oldGainPercent": (
                    (old_total / baseline - 1.0) * 100.0
                    if changed and old_total is not None and baseline > 0
                    else None
                ),
                "newGainPercent": (
                    (new_total / baseline - 1.0) * 100.0 if baseline > 0 else None
                ),
            }
            report_rows.append(row)
            status = "CHANGED" if changed else "same"
            print(f"[{index:02d}/{len(jobs)}] {second_job}: {status}", flush=True)
    finally:
        build_service_module.select_skill_glyphs_two_stage = original_two_stage

    payload = {
        "window": args.window,
        "supportTeamSize": 8,
        "elapsedSeconds": time.perf_counter() - started,
        "totalJobs": len(report_rows),
        "changedJobs": sum(1 for row in report_rows if row["changed"]),
        "rows": report_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"completed: {payload['changedJobs']}/{payload['totalJobs']} changed, "
        f"{payload['elapsedSeconds']:.2f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
