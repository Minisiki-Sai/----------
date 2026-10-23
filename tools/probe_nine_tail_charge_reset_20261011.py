"""Local regression diagnostic of the production stored-charge rule."""
import copy
import json
import os
from pathlib import Path
import sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from dragon_nest_builder.app import BuildEditor
from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore

app = QApplication.instance() or QApplication([])
store = DataStore.load()
build = copy.deepcopy(store.defaults["build1"])
build.update(first_job="魔导", second_job="时空领主")
build["skill_build"] = {"second_job": "时空领主", "levels": {"2420": 17, "2401": 16}}
editor = BuildEditor("时空领主", store, BuildCalculator(store), build)
editor._export_skill_sim_page = editor._skill_sim_page()
try:
    tree = store.skill_build_tree("时空领主", 70)
    selected = []
    for sid, skill in tree["skills"].items():
        if skill.get("name") in ("九尾激光", "聚能激光", "职业掌握I", "职业掌握II", "职业掌握III"):
            selected.append({"id": sid, "name": skill["name"], "level": skill.get("levels", [])[-1:]})
    print(json.dumps({"resources": selected, "charges": editor._skill_burst_charged_skill_maxes()}, ensure_ascii=False), flush=True)
    editor._skill_burst_schedule_override = {"window": 30, "burst": [], "accel": [], "cocktail": False}
    metrics = [{"skill": "九尾激光", "base_cooldown": 10, "raw_score": 1000,
                "effective_score": 1000, "cast_time": 1.1, "element": "光"},
               {"skill": "聚能激光", "base_cooldown": 11, "raw_score": 500,
                "effective_score": 500, "cast_time": 2.667, "element": "光"}]
    rows, issues = editor._skill_burst_replay_manual_sequence(
        ["九尾激光"] * 3 + ["聚能激光", "九尾激光"], metrics, [], 30)
    print(json.dumps({"manual": [{k: r.get(k) for k in ("skill", "start", "end", "cooldown_ready_at")} for r in rows], "issues": issues}, ensure_ascii=False), flush=True)
    ready = {"九尾激光": 10.0}
    stock, recharge = {"九尾激光": 0}, {"九尾激光": 10.0}
    editor._skill_burst_apply_charge_rule_source("聚能激光", 5.967, ready,
        {"九尾激光": 10.0}, stock, {"九尾激光": 3}, recharge, {})
    assert stock["九尾激光"] == 1 and recharge["九尾激光"] == 10
    assert abs(rows[4]["start"] - rows[3]["end"]) < 1e-6
    print(json.dumps({"available_at": ready, "charged_recharge_at": recharge,
                      "charge_count": stock, "next_charge_gate_can_cast": stock["九尾激光"] > 0}, ensure_ascii=False))
    state = editor._skill_burst_new_scheme_1_apply_damage(
        metrics[1], 3.3, 2.667, {}, {"九尾激光": 0, "聚能激光": 0},
        {"九尾激光": 10, "聚能激光": 11}, [], {}, {},
        {"九尾激光": 2}, {"九尾激光": 3}, {"九尾激光": 10}, 0, 0, 0)
    print(json.dumps({"auto_lookahead_before_charges": 2,
                      "auto_lookahead_after_charges": state["charged_counts"]["九尾激光"],
                      "expected_after_by_description": 3,
                      "recharge_after": state["charged_recharge_at"]}, ensure_ascii=False), flush=True)
finally:
    editor.deleteLater()
