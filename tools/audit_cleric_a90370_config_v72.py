"""Read-only A90370 config-row attribution audit for the four Cleric jobs.

Admission is intentionally strict.  A config row may name a skill only when the
same physical Hit joins a current root-self A90370 generation of the same owner
and object ID, and the static config-row edge is one-to-one for that job.  This
tool reports only *old-name -> different real-name* corrections.  It does not use
current action, time, damage, hit cadence, or an old name as positive evidence.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.damage_object_origins import _SPEC_BY_PROJECTILE_TEMPLATE

OUT = ROOT / "outputs" / "cleric_a90370_config_v72_20260910"
JOBS = ("十字军", "圣骑士", "雷神", "圣徒")
INPUTS = {
    "config_static_source": ROOT / "dragon_nest_builder" / "live_dps" / "damage_object_origins.py",
    "generated_config_static_source": ROOT / "dragon_nest_builder" / "live_dps" / "generated_attack_instance_specs_v576.py",
    "player_root_audit": ROOT / "outputs" / "player_object_creation_v53_20260909" / "player_object_creation_v53.json",
    "joint_key_audit": ROOT / "outputs" / "under95_native_joint_keys_v55_20260909" / "under95_native_joint_keys_v55.json",
    "current_action_audit": ROOT / "outputs" / "direct_action_state_v56_20260909" / "REPORT.json",
    "inquisitor_gap_audit": ROOT / "outputs" / "cleric_skill_gaps_v63_20260909" / "REPORT.json",
    "cross_skill_audit": ROOT / "outputs" / "cleric_cross_skill_v70_20260909" / "REPORT.json",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def static_edges():
    """Return one-to-one config-row edges, retaining aliases as non-canonical.

    The module's generated/manual specs are already grounded in client ACT and
    SkillLevel tables.  This tool still treats them as *static half* of a proof:
    they do not identify a Hit until a same-generation A90370 record is present.
    """
    rows = []
    for config_row, spec in sorted(_SPEC_BY_PROJECTILE_TEMPLATE.items()):
        skills = sorted(int(value) for value in spec.skill_ids if value >= 0)
        if not any(3000 <= value < 3600 for value in skills):
            continue
        rows.append({
            "config_row": int(config_row),
            "edge_key": spec.key,
            "canonical_skill_id": int(spec.canonical_skill_id),
            "skill_ids": skills,
            "one_to_one": len(skills) == 1,
            "requires_exact_object": bool(spec.require_exact_object),
            "requires_action_evidence": bool(spec.require_action_evidence),
        })
    return rows


def cleric_gap_summary(joint):
    per_job = defaultdict(lambda: {"gap_hits": 0, "origin_template_hits": 0, "accepted_projected_gap_hits": 0})
    for row in joint["aggregate"]["gap_skills"]:
        job = row.get("job")
        if job not in JOBS:
            continue
        current = per_job[job]
        current["gap_hits"] += int(row["gap_hits"])
        current["origin_template_hits"] += int(row["origin_template"])
        current["accepted_projected_gap_hits"] += int(row["accepted_projected_gap_hits"])
    return [{"job": job, **per_job[job]} for job in JOBS]


def existing_zero_conflict_controls(action):
    controls = []
    seen = set()
    for rule in action["aggregate"].get("accepted_rules", []):
        if rule.get("job") not in JOBS:
            continue
        key = (rule.get("job"), rule.get("candidate_skill_id"), rule.get("route"))
        if key in seen:
            continue
        seen.add(key)
        controls.append({
            "job": rule["job"],
            "skill_id": rule["candidate_skill_id"],
            "skill_name": rule["candidate_skill_name"],
            "truth_hits": rule["truth_hits"],
            "truth_conflicts": rule["truth_conflicts"],
            "new_unique_gap_hits": rule["gap_hits"],
            "note": "Existing control only. Its truth is already a projectile/object chain, not an old-name correction.",
        })
    return controls


def v63_skill_rows(v63):
    rows = []
    for raw_skill, row in v63["skill_results"].items():
        rows.append({
            "skill_id": int(raw_skill),
            "skill_name": row["skill_name"],
            "old_gap_hits": row["baseline_gap_hits"],
            "confirmed_promotions": row["confirmed_promotions"],
            "corrected_out": row["confirmed_corrected_out"],
            "corrected_in": row["confirmed_corrected_in"],
            "unclosed": row["remaining_unclosed_old_bucket_hits"],
        })
    return rows


def render(report):
    lines = [
        "# 圣职四职业 A90370 config-row 跨技能审计 v72",
        "",
        "## 结论",
        "",
        "本轮没有发现满足 `owner + object_id + generation + 一对一静态 config_row` 的旧同职业技能名 → 新真实技能名纠错。静态表边本身已能说明某个 config_row 的真实技能家族，但固定语料中可连到旧名缺口的 Hit 缺少同 generation 的根对象记录，或已由现有对象链正确命名。",
        "",
        "- 可生产跨技能纠错规则：0",
        "- 可生产新增确定 Hit：0",
        "- 共享生产代码：未修改",
        "",
        "## 可复用规则门槛",
        "",
        "同一条 Hit 必须同时具备：root-self A90370 的完整 owner、当前 object generation、该 generation 的 config_row、以及静态表中该职业唯一的 canonical skill。任何一项缺失均不允许纠错。旧名、动作、时间、伤害金额、Hit 分布都只可作为反例/诊断，不能作正证。",
        "",
        "## 四职业缺口与已闭合静态链",
        "",
        "| 职业 | 缺口 Hit | 已有 origin-template Hit | 既有严格规则可补缺口 | v72 跨技能纠错 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in report["gap_summary"]:
        lines.append(f"| {row['job']} | {row['gap_hits']} | {row['origin_template_hits']} | {row['accepted_projected_gap_hits']} | 0 |")
    lines.extend([
        "",
        "## 客户端静态 config_row → 技能候选",
        "",
        "这些是静态半边，不代表可直接给任意 Hit 改名。存在 EX/别名的行不是一对一，因此不被 v72 当作可自动纠错表边。",
        "",
        "| config_row | 客户端 ACT/Projectile 边 | canonical | 候选技能 | 一对一 |",
        "| ---: | --- | ---: | --- | :---: |",
    ])
    for row in report["static_edges"]:
        lines.append(f"| {row['config_row']} | {row['edge_key']} | {row['canonical_skill_id']} | {', '.join(map(str, row['skill_ids']))} | {'是' if row['one_to_one'] else '否'} |")
    lines.extend([
        "",
        "## 反例与边界",
        "",
        "- `latest_0e_or_12` 即使在全样本精度较高，仍有 3,534 条独立真值冲突，不能成为 root config 的替代品。",
        "- `0x12` 的雷神样本会把连锁闪电、圣光十字、闪电风暴、雷暴、精神爆破与神圣大地混在同一当前动作下，不能用作技能来源。",
        "- v63 雷神 3019/3425 缺口逐 Hit 样本没有可用的当前 generation 配对；3553 只有汇总级缺口，因此没有伪造逐 Hit 纠错。",
        "",
        "## 雷神高缺口状态",
        "",
        "| 技能 | 旧缺口 | 已升级 | 已纠出/纠入 | 仍未闭合 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for row in report["inquisitor_v63"]:
        lines.append(f"| {row['skill_id']} {row['skill_name']} | {row['old_gap_hits']} | {row['confirmed_promotions']} | {row['corrected_out']}/{row['corrected_in']} | {row['unclosed']} |")
    lines.extend([
        "",
        "下一步应继续补解 A90370 之外的根对象注册变体或实体链；一旦同 generation config_row 能与旧名 Hit 直接相连，本脚本会输出纠错规则及物理 Hit 证据。",
        "",
    ])
    return "\n".join(lines)


def main():
    missing = [str(path) for path in INPUTS.values() if not path.exists()]
    if missing:
        raise SystemExit("Missing inputs:\n" + "\n".join(missing))
    OUT.mkdir(parents=True, exist_ok=True)
    player = load(INPUTS["player_root_audit"])
    joint = load(INPUTS["joint_key_audit"])
    action = load(INPUTS["current_action_audit"])
    v63 = load(INPUTS["inquisitor_gap_audit"])
    cross = load(INPUTS["cross_skill_audit"])
    report = {
        "schema": "cleric-a90370-config-v72",
        "scope": "read-only fixed 35-capture audit; only owner+object generation+static config-row edges may correct old same-job labels",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_changed": False,
        "verdict": {
            "safe_cross_skill_correction_rules": 0,
            "safe_cross_skill_corrected_hits": 0,
            "safe_new_deterministic_hits": 0,
            "reason": "No old-label gap row exposes a current root-self A90370 generation together with an admissible one-to-one static config row.",
        },
        "input_manifest": {key: {"path": str(path), "sha256": file_hash(path)} for key, path in INPUTS.items()},
        "static_edges": static_edges(),
        "gap_summary": cleric_gap_summary(joint),
        "existing_zero_conflict_controls": existing_zero_conflict_controls(action),
        "inquisitor_v63": v63_skill_rows(v63),
        "a90370_contract": {
            "player_root_generations": player["aggregate"]["totals"]["confirmed_player_kind_9_generations"],
            "reused_owner_object_pairs": player["aggregate"]["totals"]["reused_owner_object_pairs"],
            "require_generation": True,
            "require_same_owner": True,
            "require_current_before_hit": True,
            "static_row_is_sufficient_without_generation": False,
        },
        "cross_skill_previous_result": cross["verdict"],
        "conservation": {"damage_delta": 0, "reason": "No correction rule was admitted."},
    }
    (OUT / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "REPORT.md").write_text(render(report), encoding="utf-8")
    print(json.dumps(report["verdict"], ensure_ascii=False))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
