"""Export real default score panels, without starting capture/cloud/UI services."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dragon_nest_builder import v410_native_core
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.live_dps.theory_plan_builder import _team_score_apply_boss_preset
from dragon_nest_builder.native.build_service import NativeBuildMixin
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService


class BaselineHost(NativeBuildMixin):
    def __init__(self, store, calculator):
        self.store, self.calculator = store, calculator


def report():
    store = DataStore.load()
    calculator = BuildCalculator(store)
    host = BaselineHost(store, calculator)
    baseline = host._default_build("build1")
    runtime = LegacyTimelineService(store, calculator).extreme_runtime(deepcopy(baseline))
    results = []
    for first in store.first_jobs():
        for job in store.second_jobs(first):
            build = runtime._extreme_team_score_build_for_job(job)
            _team_score_apply_boss_preset(build, "红龙佩达")
            rows, summary = calculator.calculate(build)
            stats = {row.attr: row.buffed for row in rows}
            results.append({"job": job, "main_stat": runtime._extreme_profile_for_job(job)["main_stat"],
                "physical_min": summary["physical_min"], "physical_max": summary["physical_max"],
                "physical_average": summary["physical_average"],
                "magic_min": summary["magic_min"], "magic_max": summary["magic_max"],
                "magic_average": summary["magic_average"],
                "strength": stats["力量"], "agility": stats["敏捷"], "intelligence": stats["智力"],
                "talismans": deepcopy(build["talismans"]), "glyph_s": deepcopy(build["glyph_s"]),
                "buffs": deepcopy(build["buffs"]), "summary": summary,
                "panel_rows": [asdict(row) for row in rows], "build": build})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = report()
    lines = ["# 默认评分职业面板（2026-10-03）", "",
        "口径：生产评分默认配装；70L 装备/首饰、固定全 L 卡片基线、职业匹配 B 护符、70S 纹章自动额外属性。",
        "包含默认常驻个人 Buff/被动；不含团队动态增益和爆发状态。物攻/魔攻是面板最小与最大值的平均数，不是 DPS。",
        "Boss 环境使用红龙佩达。手动评分装备修改后，以该队员实际评分面板为准。", "",
        "|职业|主属性|平均物攻|平均魔攻|力量|敏捷|智力|", "|---|---|---:|---:|---:|---:|---:|"]
    for row in results:
        values = [row["job"], row["main_stat"], *[f"{row[key]:,.1f}" for key in ("physical_average", "magic_average")],
                  *[f"{row[key]:,}" for key in ("strength", "agility", "intelligence")]]
        lines.append("|" + "|".join(values) + "|")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
