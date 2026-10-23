"""Offline 96-player controlled support experiment; no decoding/cloud writes.

Run from repository root: py -3.12 -X utf8 -B tools/audit_score_comparability_experiment_20261019.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.score_experiments import support_reference_result
from dragon_nest_builder.live_dps.scoring import (
    TeamScoreEngine, _coverage_in_windows, _merge_intervals,
    _overflow_balanced_score_contributions,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent

BASE = ROOT / "outputs/verification/all_jobs_published_ab_20261019"
OUT = ROOT / "outputs/verification/scoring_optimization_20261019/comparability"


def load_case(case):
    for root in (BASE, ROOT / "outputs/verification/published_478_485_20261019"):
        p = root / f"{case}_v4.85.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(case)


def total_at_fixed_weights(v, support):
    out, sup = _overflow_balanced_score_contributions(
        v["output_score"], support, v["output_weight"], v["support_weight"])
    return max(0.0, out + sup + v["specialty_contribution"] - v["survival_penalty"])


def team_windows_from_exported_events(raw):
    replay = raw["replay"]
    event_keys = {f.name for f in fields(CombatEvent)}
    cast_keys = {f.name for f in fields(SkillCastEvent)}
    selected = frozenset(raw["score"]["bossTargetIds"])
    events = [CombatEvent(**{k: v for k, v in e.items() if k in event_keys})
              for e in replay["combat_events"] if not e.get("healing") and e.get("target_id") in selected]
    casts = [SkillCastEvent(**{k: v for k, v in e.items() if k in cast_keys})
             for e in replay["skill_cast_events"]]
    return TeamScoreEngine._output_windows_from_events(
        events, casts, [p["source_id"] for p in replay["players"]], selected_target=True)


def main():
    started = time.perf_counter()
    audit = json.loads((BASE / "detailed_attribution/attribution.json").read_text(encoding="utf-8"))
    players, role_rows, body_rows, all_rows, remaining_overflow = [], [], [], [], []
    baseline_error = total_error = 0.0
    for p in audit["players"]:
        v = p["versions"]["4.85"]
        rows = [s["new"] for s in p["supports"] if s.get("new")]
        optional = set()
        if p["job"] == "曜" and v["rotation_job"] == "输出曜":
            optional.add("查克拉治愈术")
        if p["job"] == "药剂师" and v["rotation_job"] == "输出药剂":
            optional.add("牺牲")
        results = {policy: support_reference_result(rows, optional_skills=optional, policy=policy)
                   for policy in ("published", "required_guard", "symmetric_optional")}
        baseline_error = max(baseline_error, abs(results["published"].score - v["support_score"]))
        total_error = max(total_error, abs(total_at_fixed_weights(v, v["support_score"]) - v["total_score"]))
        item = dict(case=p["case"], source_id=p["source_id"], name=p["name"], job=p["job"],
                    baseline_support=v["support_score"], baseline_total=v["total_score"],
                    output_weight=v["output_weight"], support_weight=v["support_weight"],
                    specialty_weight=v["specialty_weight"], optional_skills=sorted(optional),
                    detail_rows=len(rows), policies={})
        for policy, result in results.items():
            item["policies"][policy] = asdict(result) | {
                "support_delta": result.score - v["support_score"],
                "total_fixed_weights": total_at_fixed_weights(v, result.score),
                "total_delta_fixed_weights": total_at_fixed_weights(v, result.score) - v["total_score"],
            }
        players.append(item)
        if results["required_guard"].score > 110:
            remaining_overflow.append(dict(case=p["case"], name=p["name"], job=p["job"],
                score_after_required_guard=results["required_guard"].score,
                optional_actual=results["required_guard"].optional_actual,
                major_effects=[{k: row.get(k) for k in ["skill_name", "effect_key", "actual_contribution",
                    "theory_contribution", "actual_coverage", "theory_coverage", "effective_damage_coverage",
                    "effective_damage_theory_coverage"]} for row in sorted(rows,
                    key=lambda r: float(r.get('actual_contribution') or 0)-float(r.get('theory_contribution') or 0),
                    reverse=True)[:3]],
                conclusion="分母修补不能闭合此项；合法实际高峰覆盖可能优于当前理论计划，不能直接截到100分"))
        for row in rows:
            if not row.get("included_in_score", True):
                continue
            all_rows.append({"case": p["case"], "name": p["name"], "job": p["job"],
                             **{k: row.get(k) for k in ["skill_name", "effect_key", "actual_contribution",
                             "theory_contribution", "actual_coverage", "theory_coverage"]},
                             "optional": row.get("skill_name") in optional})
    support_seconds = time.perf_counter() - started
    for case in sorted({p["case"] for p in audit["players"]}):
        raw = load_case(case)
        for source, plan in raw["plans"].items():
            player = next((p for p in raw["scoreRowsDetailed"] if p["source_id"] == source), {})
            for skill, profile in plan.get("_score_v2", {}).get("profiles", {}).items():
                if not profile.get("body_sensitive"):
                    continue
                for boss in profile.get("boss_windows", []):
                    if 0 < float(boss.get("boss_factor", 1)) < .999999:
                        body_rows.append(dict(case=case, name=player.get("name"), job=player.get("job"),
                                              source_id=source, skill=skill, **boss))
        yao = [p for p in raw["scoreRowsDetailed"] if p["job"] == "曜"]
        if not yao:
            continue
        team = team_windows_from_exported_events(raw)
        for p in yao:
            intervals = _merge_intervals(i for b in raw["replay"]["player_buff_series"]
                if b.get("source_id") == p["source_id"] and b.get("skill_name") == "查克拉治愈术"
                for i in b.get("intervals", []))
            personal = p["output_windows"]
            team_cov = _coverage_in_windows(intervals, team)
            personal_cov = _coverage_in_windows(intervals, personal)
            declared_keys = list(raw["theoryDiagnostics"].get("prefetch_seconds_by_job", {}))
            planner_job = next((k.split(":")[0] for k in declared_keys if k.endswith(":" + p["source_id"])), None)
            role_rows.append(dict(case=case, name=p["name"], source_id=p["source_id"],
                team_output_seconds=sum(b-a for a,b in team), personal_output_seconds=sum(b-a for a,b in personal),
                team_coverage=team_cov, personal_coverage=personal_cov,
                role_before=p["rotation_job"], role_experiment="打坐曜" if team_cov >= .4 else "输出曜",
                planner_job=planner_job, baseline_total=p["total_score"],
                score_after=None, reason="角色改变必须重算治疗职责及权重；不能伪造固定权重总分",
                threshold_sensitivity={str(t): "打坐曜" if personal_cov >= t else "输出曜" for t in [.35,.38,.4,.42,.45]}))
    low = [x for x in body_rows if int(x.get("valid_casts", 0)) <= 2]
    zero = [r for r in all_rows if float(r.get("actual_contribution") or 0)>1e-9 and float(r.get("theory_contribution") or 0)<=1e-9]
    data = dict(
        experiment="offline fixed-cast fixed-role fixed-weight support sensitivity; not full rescoring",
        policies={"required_guard": "required numerator-only rows omitted; optional bonus preserved",
                  "symmetric_optional": "optional work treated neutral in own pool, NOT a theoretical optimum; not recommended for automatic rollout"},
        production_changed="only opt-in role scope; engine._experimental_consistent_role_scope defaults False",
        prior_existing_source_fix="required actual>0/theory0 guard already exists in working source; this experiment quantifies it against published v4.85, not a new implementation claim",
        alchemist_scope_check="药剂依据自身牺牲释放事件或Buff记录判定，不除以个人输出时间；未发现与曜相同的区间阈值分歧，未修改",
        recommendations=[
            "先保证排轴与最终评分使用同一角色，角色判定用所选Boss全队区间，个人输出评价仍用个人区间",
            "required单边收益先标缺少理论参考，保留真实收益展示；后续补合法同口径计划而不是一律拉低辅助分",
            "输出型曜的额外治疗收益明确标奖励分，不把123%直接解释为必修辅助手法完成123%；对称可选池仅敏感性",
            "同Boss技能体型上限使用跨角色证据及置信门槛，不让一次个人低命中自证完整；保留真正体型限制",
            "高峰辅助计划用相同逐时刻团队收益评估，并把通过合法性检查的实际排法作为候选，不承诺当前计划是最优",
            "不以30职业这批人的均值强制拉平；跨职业比较同版本同机制同职责完成，而不是伤害绝对值",
        ],
        limitations=["辅助收益、时间轴、角色权重、承伤扣分全部冻结；不是完整新版本分数",
                     "分母缺失行必须后续同口径重排；暂缓计入不等于玩家未贡献",
                     "职业均值不可作校准真值；样本玩家、巢穴、版本不同",
                     "角色口径变化仅报告证据，未捏造未计算的治疗分和总分",
                     "体型低样本校准仅标记风险，不直接否定真实体型限制"],
        summary=dict(players=len(players), jobs=len({p['job'] for p in players}),
                     support_detail_rows=sum(p['detail_rows'] for p in players), included_rows=len(all_rows),
                     numerator_only_rows=len(zero), required_numerator_only_rows=sum(not r['optional'] for r in zero),
                     optional_numerator_only_rows=sum(r['optional'] for r in zero),
                     required_guard_changed_players=sum(bool(p['policies']['required_guard']['excluded']) for p in players),
                     reduced_body_reference_single_cast_rows=sum(int(x.get('valid_casts',0)) == 1 for x in body_rows),
                     role_changed=sum(r['role_before'] != r['role_experiment'] for r in role_rows),
                     reduced_body_reference_rows=len(body_rows), low_sample_body_reference_rows=len(low),
                     baseline_support_max_abs_error=baseline_error, baseline_total_max_abs_error=total_error,
                     support_experiment_seconds=support_seconds, elapsed_seconds=time.perf_counter()-started),
        players=players, numerator_only_rows=zero, role_scope=role_rows,
        remaining_support_overflow=remaining_overflow,
        reduced_body_reference_rows=body_rows,
    )
    assert baseline_error < 1e-8, baseline_error
    assert total_error < 1e-8, total_error
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "comparability_experiment.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
    for p in players:
        if any(abs(r['support_delta']) > .01 for r in p['policies'].values()):
            print(p['case'],p['job'],p['name'],round(p['baseline_support'],3),
                  {k: (round(v['score'],3), round(v['total_delta_fixed_weights'],3))
                   for k,v in p['policies'].items() if k != 'published'})
    print("role", json.dumps(role_rows, ensure_ascii=False))


if __name__ == '__main__':
    main()
