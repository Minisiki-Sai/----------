from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from dragon_nest_builder.app import BuildEditor
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, as_number, norm
from dragon_nest_builder.extreme_schemes import load_extreme_team_schemes
from dragon_nest_builder.mechanism_timeline import normalize_mechanism_intervals


OUTPUT = ROOT / "outputs" / "summon_fix_timeline_comparison.json"


def schedule_rows_to_casts(
    editor: BuildEditor,
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], list[tuple[float, float]]]:
    schedule: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for cast in rows:
        key = (
            norm(cast.get("provider")),
            editor._skill_burst_skill_key(cast.get("skill")),
        )
        if not all(key):
            continue
        schedule.setdefault(key, []).append((
            as_number(cast.get("cast_start")),
            as_number(cast.get("cast_end")),
        ))
    return schedule


def effects_for_team_order(
    editor: BuildEditor,
    effects: list[dict[str, Any]],
    team: list[str],
) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    for effect in effects:
        row = copy.deepcopy(effect)
        provider = norm(row.get("provider"))
        provider_job = provider.split(" ", 1)[1] if " " in provider else provider
        provider_real_job = editor._team_real_job(provider_job)
        target_index = next(
            (
                index
                for index, job in enumerate(team)
                if job == provider_job
                or editor._team_real_job(job) == provider_real_job
            ),
            None,
        )
        if target_index is not None:
            row["provider"] = f"队员{target_index + 1} {team[target_index]}"
        remapped.append(row)
    return remapped


def builds_for_team_order(
    editor: BuildEditor,
    scheme: dict[str, Any],
    team: list[str],
) -> list[dict[str, Any] | None]:
    by_job: dict[str, dict[str, Any]] = {}
    for build in scheme.get("member_builds", []):
        if not isinstance(build, dict):
            continue
        job = norm(build.get("effective_second_job") or build.get("second_job"))
        if job:
            by_job[job] = build
    return [
        copy.deepcopy(
            by_job.get(job)
            or by_job.get(editor._team_real_job(job))
        )
        if isinstance(by_job.get(job) or by_job.get(editor._team_real_job(job)), dict)
        else None
        for job in team
    ]


def task_specs(
    team: list[str],
    effects: list[dict[str, Any]],
    builds: list[dict[str, Any] | None],
    hands: dict[str, float],
    window: float,
    *,
    blocked_intervals: list[dict[str, Any]] | None = None,
    pinned_schedule: dict[tuple[str, str], list[tuple[float, float]]] | None = None,
    fast_long_timeline: bool = False,
) -> list[dict[str, Any]]:
    return [
        {
            "job": job,
            "team": team,
            "effects": effects,
            "hand_multiplier": as_number(hands.get(job), 1.0),
            "window": window,
            "build_override": builds[index] if index < len(builds) else None,
            "blocked_intervals": blocked_intervals,
            "cast_schedule": pinned_schedule,
            "partial_cast_schedule": bool(pinned_schedule),
            "include_display_windows": False,
            "fast_long_timeline": fast_long_timeline,
        }
        for index, job in enumerate(team)
    ]


def compare_rows(
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    old_by_job = {norm(row.get("job")): row for row in old_rows}
    new_by_job = {norm(row.get("job")): row for row in new_rows}
    jobs = [norm(row.get("job")) for row in old_rows if norm(row.get("job"))]
    rows: list[dict[str, Any]] = []
    for job in jobs:
        old = old_by_job.get(job, {})
        new = new_by_job.get(job, {})
        old_dps = max(0.0, as_number(old.get("dps")))
        new_dps = max(0.0, as_number(new.get("dps")))
        rows.append({
            "job": job,
            "old_dps": old_dps,
            "new_dps": new_dps,
            "delta_dps": new_dps - old_dps,
            "delta_percent": (
                (new_dps / old_dps - 1.0) * 100.0 if old_dps > 0 else 0.0
            ),
            "old_total_percent": max(0.0, as_number(old.get("total"))),
            "new_total_percent": max(0.0, as_number(new.get("total"))),
        })
    old_total = sum(row["old_dps"] for row in rows)
    new_total = sum(row["new_dps"] for row in rows)
    return {
        "old_team_dps": old_total,
        "new_team_dps": new_total,
        "delta_team_dps": new_total - old_total,
        "delta_percent": (new_total / old_total - 1.0) * 100.0 if old_total > 0 else 0.0,
        "jobs": rows,
    }


def skill_totals(result: dict[str, Any], skill: str) -> dict[str, float]:
    segments = [
        row for row in result.get("segments", [])
        if norm(row.get("skill")) == skill
    ]
    return {
        "casts": float(len(segments)),
        "raw": sum(max(0.0, as_number(row.get("raw_effective_score"))) for row in segments),
        "effective": sum(max(0.0, as_number(row.get("effective_score"))) for row in segments),
    }


def main() -> None:
    app = QApplication.instance() or QApplication([])
    schemes = load_extreme_team_schemes()
    if not schemes:
        raise RuntimeError("没有找到已保存的极限配队方案。")
    scheme_name = "暗攻竞速队1" if "暗攻竞速队1" in schemes else next(iter(schemes))
    scheme = schemes[scheme_name]
    store = DataStore.load()
    calculator = BuildCalculator(store)
    source_build = copy.deepcopy(scheme.get("source_build") or store.defaults["build1"])
    editor = BuildEditor(scheme_name, store, calculator, source_build)
    editor._export_skill_sim_page = editor._skill_sim_page()
    editor._reset_extreme_timeline_context(scheme.get("boss", "无"))

    hands = {
        norm(job): max(0.0, as_number(value, 1.0))
        for job, value in (scheme.get("hand_multipliers") or {}).items()
    }
    standard_team = [norm(job) for job in scheme.get("team", []) if norm(job)]
    standard_effects = effects_for_team_order(
        editor,
        list(scheme.get("team_effects", [])),
        standard_team,
    )
    standard_builds = builds_for_team_order(editor, scheme, standard_team)
    old_standard = copy.deepcopy(scheme.get("timelines", []))

    print("[1/4] 生成修复后的八人125s初始轴...", flush=True)
    editor._extreme_timeline_result_cache = {}
    standard_initial = editor._extreme_timeline_tasks_parallel(
        task_specs(
            standard_team,
            standard_effects,
            standard_builds,
            hands,
            125.0,
        ),
        progress_callback=lambda completed, _index, job: print(
            f"  125s初始轴 {completed}/8 {job}", flush=True
        ),
    )
    print("[2/4] 同步八人125s团队效果时机...", flush=True)
    new_standard = editor._extreme_synchronized_team_timelines(
        standard_team,
        standard_effects,
        hands,
        standard_initial,
        125.0,
        include_display_windows=False,
        build_overrides=standard_builds,
        max_passes=3,
    )

    result_state = scheme.get("result_state") or {}
    old_custom = copy.deepcopy(result_state.get("custom_timelines", []))
    custom_team = [norm(row.get("job")) for row in old_custom if norm(row.get("job"))]
    custom_effects = effects_for_team_order(
        editor,
        list(scheme.get("team_effects", [])),
        custom_team,
    )
    custom_builds = builds_for_team_order(editor, scheme, custom_team)
    custom_window = max(180.0, min(720.0, as_number(result_state.get("window"), 720.0)))
    intervals = normalize_mechanism_intervals(
        result_state.get("intervals", []),
        custom_window,
    )
    pinned_schedule = schedule_rows_to_casts(
        editor,
        result_state.get("optimized_cdr_rows", []),
    )

    print("[3/4] 生成修复后的自选机制初始轴...", flush=True)
    editor._extreme_timeline_result_cache = {}
    custom_initial = editor._extreme_timeline_tasks_parallel(
        task_specs(
            custom_team,
            custom_effects,
            custom_builds,
            hands,
            custom_window,
            blocked_intervals=intervals,
            pinned_schedule=pinned_schedule,
            fast_long_timeline=True,
        ),
        progress_callback=lambda completed, _index, job: print(
            f"  自选初始轴 {completed}/8 {job}", flush=True
        ),
    )
    print("[4/4] 同步自选机制轴团队效果时机...", flush=True)
    new_custom = editor._extreme_synchronized_team_timelines(
        custom_team,
        custom_effects,
        hands,
        custom_initial,
        custom_window,
        include_display_windows=False,
        build_overrides=custom_builds,
        blocked_intervals=intervals,
        pinned_cast_schedule=pinned_schedule,
        max_passes=1,
        fast_long_timeline=True,
    )

    report = {
        "scheme": scheme_name,
        "boss": scheme.get("boss"),
        "comparison_method": (
            "固定保存方案中的职业、装备、纹章与Boss。125秒轴使用当前三轮团队时机同步；"
            "自选轴固定保存的机制区间与团队Buff日程，以隔离结算变化。"
            "旧值读取修复前缓存，新值使用当前结算与个人排轴重算。"
        ),
        "standard_125s": compare_rows(old_standard, new_standard),
        "custom_timeline": compare_rows(old_custom, new_custom),
    }
    old_standard_soul = next(
        (row for row in old_standard if norm(row.get("job")) == "噬魂者"),
        {},
    )
    new_standard_soul = next(
        (row for row in new_standard if norm(row.get("job")) == "噬魂者"),
        {},
    )
    old_custom_soul = next(
        (row for row in old_custom if norm(row.get("job")) == "噬魂者"),
        {},
    )
    new_custom_soul = next(
        (row for row in new_custom if norm(row.get("job")) == "噬魂者"),
        {},
    )
    report["soul_gate"] = {
        "standard_old": skill_totals(old_standard_soul, "灵魂之门"),
        "standard_new": skill_totals(new_standard_soul, "灵魂之门"),
        "custom_old": skill_totals(old_custom_soul, "灵魂之门"),
        "custom_new": skill_totals(new_custom_soul, "灵魂之门"),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"报告已保存：{OUTPUT}", flush=True)
    editor.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
