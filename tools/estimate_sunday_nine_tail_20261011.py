"""Estimate from uploaded output windows and original theory acceleration.

No packet replay and no cloud writes. Counts are diagnostic estimates, not a
replacement score: old records lack complete recipient/Boss state ledgers.
"""
import copy
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore
from dragon_nest_builder.v410_legacy_timeline import LegacyTimelineService

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--recorded-support', action='store_true',
                    help='Include uploaded recipient Spirit/Scorch and Cocktail windows, without moving Time Acceleration.')
args = parser.parse_args()

records = json.loads((ROOT / "work/sunday_nine_tail_cloud_20261011.json").read_text(encoding="utf-8"))
full = {row["row_fingerprint"]: row["score_detail"] for row in
        json.loads((ROOT / "work/sunday_full_score_inputs_20261011.json").read_text(encoding="utf-8"))}
store = DataStore.load()
fixed = copy.deepcopy(store.skill_cooldown_rules())
legacy = copy.deepcopy(fixed)
legacy["jobs"]["时空领主"]["九尾激光"]["sources"][0]["type"] = "reset"
calculator = BuildCalculator(store)
results = []

def key(value):
    value = str(value).strip().replace(" ", "")
    return value[:-2] if value.upper().endswith("EX") else value

for record in records:
    detail = full[record["historyId"]]
    timeline = record["detail"]["compactTimeline"]
    quantum = timeline.get("q", 100)
    duration = timeline["d"] / quantum
    output_windows, tick = [], 0
    for row in timeline["o"]:
        tick += row[0]
        output_windows.append((tick / quantum, (tick + row[1]) / quantum))
    blocked, end = [], 0.0
    for start, stop in output_windows:
        if start > end:
            blocked.append((end, start))
        end = max(end, stop)
    if end < duration:
        blocked.append((end, duration))
    accel = [(row["start"], row["end"]) for row in record["effectsDecoded"]
             if row["extra"] == [1] and row["skill"].split(" · ")[0] in ("时间加速", "变异时间加速")]
    burst = [(row["start"], row["end"]) for row in record["effectsDecoded"]
             if row["extra"] == [1] and row["skill"].split(" · ")[0] in
             ("精神爆发", "变异精神爆发", "灼热火焰")]
    external = []
    if args.recorded_support:
        # Other professions' received buffs are actualBuff (0), not this
        # character's theoryBuff (1). Never silently replace a missing theory
        # lane with no acceleration. Keep the own Time Acceleration lane above.
        burst = [(row['start'], row['end']) for row in record['effectsDecoded']
                 if row['extra'] == [0] and row['skill'].split(' · ')[0] in
                 ('精神爆发', '变异精神爆发', '灼热火焰')]
        external = [{'start': row['start'], 'end': row['end'],
                     'team_effect': {'skill': '鸡尾酒', 'provider': '药剂师',
                                     'attrs': {'冷却缩减%': 12.0}}}
                    for row in record['effectsDecoded']
                    if row['extra'] == [0] and row['skill'].split(' · ')[0] == '鸡尾酒']
    output = {"recordedAt": record["recordedAt"], "nest": record["nest"],
              "output_seconds": detail["output_seconds"], "uploaded_window_seconds": sum(b-a for a,b in output_windows),
              "original_acceleration_windows": accel, "original_action_speed_windows": burst,
              "actual_nine_tail": next(row["actual_count"] for row in record["skills"] if key(row["skill_name"]) == "九尾激光"),
              "uploaded_theory_nine_tail": next(row["theory_count"] for row in record["skills"] if key(row["skill_name"]) == "九尾激光"),
              "estimates": []}
    output['recorded_cocktail_windows'] = [(r['start'], r['end']) for r in external]
    variants = [("legacy_reset", legacy), ("fixed_one_charge", fixed)]
    if args.recorded_support:
        variants = [("fixed_with_recorded_support", fixed)]
    for label, rules in variants:
        store.skill_cooldown_rules = lambda rules=rules: rules
        service = LegacyTimelineService(store, calculator)
        seed = service.extreme_runtime(copy.deepcopy(store.defaults["build1"]))
        saved = detail.get("scoring_equipment", {}).get("build")
        build = copy.deepcopy(saved) if saved else seed._extreme_team_score_build_for_job("时空领主")
        output["equipment_source"] = "uploaded_scoring_equipment" if saved else "current_standard_score_equipment"
        levels = build.setdefault("skill_build", {}).setdefault("levels", {})
        by_name = {key(row["skill_name"]): row for row in record["skills"]}
        for sid, skill in store.skill_build_tree("时空领主", 70).get("skills", {}).items():
            row = by_name.get(key(skill.get("name")))
            if row and row.get("scoring_level") is not None:
                levels[str(sid)] = int(row["scoring_level"])
        settings = {"window": duration, "mode": "long", "cocktail": False,
                    "blockedIntervals": blocked, "burstIntervals": burst, "accelIntervals": accel,
                    "externalEffectWindows": external,
                    "pinnedSpecialSkillKeys": ["时间加速", "精神爆发", "灼热火焰"]}
        runtime = service._runtime(build, settings)
        # The ordinary UI caps custom windows at 720s, whereas database fights
        # can be longer. Preserve the original uploaded clock for this audit.
        runtime._skill_burst_schedule_override = {"window": duration, "burst": burst, "accel": accel, "cocktail": False}
        runtime._skill_burst_complete_default_rotation = True
        runtime._skill_burst_force_new_scheme_1 = True
        runtime._skill_burst_disable_new_scheme_1_refinement = True
        runtime._skill_burst_disable_conservative_1_6 = True
        runtime._skill_burst_skip_display_effect_windows = True
        # Refunded charge counts must not depend on whether the UI is present.
        started = time.perf_counter()
        _, summary = calculator.calculate(copy.deepcopy(build))
        output['natural_base_cooldown'] = runtime._skill_burst_original_cooldown('九尾激光')
        result = runtime.calculate_timeline([], summary).materialize()
        rows = result["auto_segments"]
        counts = Counter(key(row.get("skill")) for row in rows
                         if not row.get("timeline_hidden") and row.get("kind") not in ("invalid", "wait"))
        entry = {"label": label, "seconds": time.perf_counter() - started,
                 "counts": dict(counts), "nine_tail": counts["九尾激光"],
                 "condensed_laser": counts["聚能激光"],
                 "segments": [{k: row.get(k) for k in ("skill", "start", "end")} for row in rows]}
        output["estimates"].append(entry)
        print(json.dumps({"record": record["recordedAt"], "label": label, "nine_tail": entry["nine_tail"],
                          "condensed_laser": entry["condensed_laser"], "seconds": round(entry["seconds"], 2)}, ensure_ascii=False), flush=True)
    results.append(output)
    filename = ("九尾充能差距_补齐既有辅助窗口估算_20261011.json" if args.recorded_support
                else "九尾充能修复_五场有效输出与原加速估算_20261011.json")
    target = ROOT / 'deliverables' / filename
    target.write_text(json.dumps({"method": "uploaded output windows + unchanged uploaded theory acceleration windows; production pure simulation runtime",
                                  "is_full_rescore": False, "cloud_records_modified": False, "results": results},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
print("Saved estimates: " + str(target), flush=True)
