"""Read-only cross-skill audit for Saint relic damage on the frozen 35-capture corpus.

This is a reporting/acceptance layer over two per-hit replay audits:

* ``audit_saint_hand_skill_gaps_v63`` replays the four relic entity templates;
* ``audit_saint_lightning_master_gaps_v65`` checks the lightning/hold chains
  and rejects foreign Azuna self-attacks.

It deliberately does not use cast time, an old skill label, or a nearby action
to create an attribution.  A production rule needs all of: same owner, same
entity generation, a concrete native self-attack slot (or an equivalent
direct-result / complete parent-child object chain), and one reviewed Saint
player-skill parent.  This tool never changes production code or damage.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HANDS = ROOT / "outputs/saint_relic_cross_skill_v73_20260910/hands/REPORT.json"
DEFAULT_LIGHTNING = ROOT / "outputs/saint_lightning_master_gaps_v65_20260909/REPORT.json"
DEFAULT_OUTPUT = ROOT / "outputs/saint_relic_cross_skill_v73_20260910"

TEMPLATES = {
    30152: ("治愈之手 EX 实体", 3403, "治愈之手"),
    30251: ("痊愈之手 EX 实体", 3422, "痊愈之手"),
    30311: ("掌握之手 EX 实体", 3423, "掌握之手"),
    30401: ("奇迹之手实体", 3412, "奇迹之手"),
}
REVIEWED_SAINT_SLOT_PARENT = {
    30612: (3409, "雷电之手"),
    30615: (3412, "奇迹之手"),
    30616: (3409, "雷电之手"),
    291172: (3412, "奇迹之手"),
    292224: (3423, "掌握之手"),
}
SHOCK_SLOT = 30802


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _template_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {}
    for template_id, (label, nominal_skill_id, nominal_skill_name) in TEMPLATES.items():
        buckets[template_id] = {
            "template_id": template_id,
            "template": label,
            "nominal_relic_skill": {"skill_id": nominal_skill_id, "name": nominal_skill_name},
            "observed_direct37_hits": 0,
            "observed_damage": 0,
            "old_gap_hits": 0,
            "native_slots": defaultdict(lambda: {"hits": 0, "damage": 0}),
            "production_confirmed": [],
            "not_promotable": [],
        }
    for row in rows:
        template_id = _int(row.get("template_id"))
        bucket = buckets.get(template_id)
        if bucket is None:
            continue
        hits = _int(row.get("hits"))
        damage = _int(row.get("damage"))
        gap = _int(row.get("requested_gap_hits"))
        slot = str(row.get("self_attack_slot") or "empty_slot")
        bucket["observed_direct37_hits"] += hits
        bucket["observed_damage"] += damage
        bucket["old_gap_hits"] += gap
        slot_bucket = bucket["native_slots"][slot]
        slot_bucket["hits"] += hits
        slot_bucket["damage"] += damage

        candidate = _int(row.get("candidate_skill_id"))
        eligible = row.get("production_status") == "eligible_rule" and candidate > 0
        if eligible and gap:
            bucket["production_confirmed"].append(
                {
                    "slot": slot,
                    "skill_id": candidate,
                    "skill_name": REVIEWED_SAINT_SLOT_PARENT.get(candidate, (candidate, ""))[1],
                    "hits": hits,
                    "damage": damage,
                    "captures": _int(row.get("distinct_captures")),
                    "owners": _int(row.get("distinct_owners")),
                    "old_label_matches": bool(row.get("candidate_matches_old")),
                    "old_label_conflicts": _int(row.get("candidate_conflicts_old")),
                }
            )

    matrix: list[dict[str, Any]] = []
    for template_id, bucket in buckets.items():
        bucket["native_slots"] = [
            {"slot": slot, **values}
            for slot, values in sorted(bucket["native_slots"].items(), key=lambda item: (-item[1]["hits"], item[0]))
        ]
        confirmed = sum(item["hits"] for item in bucket["production_confirmed"])
        if bucket["old_gap_hits"] > confirmed:
            if template_id == 30311:
                reason = (
                    "同一遗迹实体具备掌握之手与 ShockofRelic 分支；本语料缺少可读的原生 self-attack slot，"
                    "不能整模板回填为掌握之手。"
                )
            elif template_id == 30251:
                reason = "实体 owner/generation 可确认，但 damage result 未携带可读 self-attack slot；owner 不是技能身份。"
            elif template_id == 30152:
                reason = "本轮没有可读 self-attack slot，也没有固定 native result key 或完整父子对象链。"
            else:
                reason = "部分奇迹之手实体伤害没有可读 self-attack slot；不能仅依实体模板或旧标签提升。"
            bucket["not_promotable"].append({"hits": bucket["old_gap_hits"] - confirmed, "reason": reason})
        matrix.append(bucket)
    return matrix


def _foreign_veto(lightning: dict[str, Any]) -> dict[str, Any]:
    replay = lightning.get("current_replay", {})
    by_slot = replay.get("foreign_family_veto_hits", {})
    by_damage = replay.get("foreign_family_veto_damage", {})
    rows = []
    for key in sorted(by_slot):
        rows.append({"old_bucket_to_foreign_slot": key, "hits": _int(by_slot[key]), "damage": _int(by_damage.get(key))})
    return {
        "hits": _int(replay.get("foreign_family_veto_total_hits")),
        "damage": _int(replay.get("foreign_family_veto_total_damage")),
        "rows": rows,
        "rule": (
            "若同一 direct37 记录带有 261801/262101/262301/262302 之一，禁止写入圣徒雷电之手或掌握之手。"
            "这些 slot 静态上只属于阿兹那实体；缺少当前圣徒实体 birth/owner 链时，撤回至未归因而不猜测改派。"
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 圣徒遗迹跨技能纠错审计 v73",
        "",
        "范围：固定 35 份抓包；逐个 direct37 伤害记录复核。没有修改生产代码或原始伤害。",
        "",
        "## 可生产的精确规则",
        "",
        "`实体模板 30401` + `同 owner、同 generation 的当前实体绑定` + `direct37 原生 self-attack slot=30615` → `3412 奇迹之手`。",
        f"本轮新增可确定 Hit：**{report['confirmed_promotions']['hits']}**；伤害：**{report['confirmed_promotions']['damage']}**；"
        f"覆盖 {report['confirmed_promotions']['captures']} 份抓包、{report['confirmed_promotions']['owners']} 位施放者。"
        "该批 Hit 的旧标签本来就是奇迹之手，所以是确定性升级，不是跨技能改名。",
        "",
        "## 四类遗迹实体",
        "",
        "|模板|实体|旧缺口 Hit|本轮可确认|不可闭合原因|",
        "|---:|---|---:|---:|---|",
    ]
    for row in report["template_matrix"]:
        reason = row["not_promotable"][0]["reason"] if row["not_promotable"] else "无"
        confirmed = sum(item["hits"] for item in row["production_confirmed"])
        lines.append(f"|{row['template_id']}|{row['template']}|{row['old_gap_hits']}|{confirmed}|{reason}|")
    lines += [
        "",
        "## ShockofRelic 与反例",
        "",
        "- `30802` 不能映射为 3503：客户端静态全局表将它显式连到弓手 `1303`，与圣徒遗迹共享该怪物 slot，存在跨职业碰撞。没有同 generation 的圣徒专属二次键或父子对象链时，不能把它写为冲击之手。",
        "- `30311` 不能整模板视为掌握之手：同一模板可出现不同遗迹攻击分支；只有原生 slot `292224` 加完整 owner/generation 链才可写入 3423。本轮没有满足该条件的缺口 Hit。",
        f"- 另发现 {report['foreign_veto']['hits']} Hit / {report['foreign_veto']['damage']} 伤害带阿兹那专属 slot，旧桶曾落在 3409/3423；"
        "这类可以用否定规则撤回，但没有圣徒实体链时只能回到未归因，不能强行改派到别的圣徒技能。",
        "",
        "## 守恒与结论",
        "",
        f"- 原始 direct37 伤害守恒：`{report['invariants']['raw_damage_conserved']}`。",
        "- 时间邻近、旧技能名、缓存 summon owner 均未参与候选构造；它们仅用于列出风险和对照。",
        "- 可直接进入生产候选的只有 30401/30615 → 3412。其余分支必须继续解出原生 self-attack slot、固定结果键或完整父子对象链后才可提升。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands-report", type=Path, default=DEFAULT_HANDS)
    parser.add_argument("--lightning-report", type=Path, default=DEFAULT_LIGHTNING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    hands = _load(args.hands_report)
    lightning = _load(args.lightning_report)
    matrix = _template_matrix(list(hands.get("all_strata", [])))
    promotions = [item for row in matrix for item in row["production_confirmed"]]
    confirmed_hits = sum(item["hits"] for item in promotions)
    confirmed_damage = sum(item["damage"] for item in promotions)
    captures = max((item["captures"] for item in promotions), default=0)
    owners = max((item["owners"] for item in promotions), default=0)

    report = {
        "schema": "saint-relic-cross-skill-v73",
        "policy": {
            "read_only": True,
            "fixed_corpus": "35 captures",
            "forbidden_as_positive_evidence": ["time proximity", "old skill label", "cached summon owner"],
            "required_positive_evidence": [
                "same owner",
                "same entity generation",
                "native self-attack slot or fixed result key / complete parent-child chain",
                "reviewed unique Saint player-skill parent",
            ],
        },
        "inputs": {
            "hands_report": str(args.hands_report),
            "hands_sha256": _hash(args.hands_report),
            "lightning_report": str(args.lightning_report),
            "lightning_sha256": _hash(args.lightning_report),
        },
        "confirmed_promotions": {
            "rule": "30401 + exact current owner/generation + direct37 self_attack_slot 30615 -> 3412",
            "hits": confirmed_hits,
            "damage": confirmed_damage,
            "captures": captures,
            "owners": owners,
            "cross_skill_relabels": 0,
            "note": "All promoted hits match the old 3412 label; this upgrades proof quality without moving score damage.",
        },
        "template_matrix": matrix,
        "shock_of_relic": {
            "candidate_skill": {"skill_id": 3503, "name": "冲击之手"},
            "slot": SHOCK_SLOT,
            "status": "blocked_global_collision",
            "reason": (
                "Static global edge for 30802 points to player skill 1303, while all four Saint relic templates expose it; "
                "there is no unique Saint parent, fixed native result key, or complete parent-child chain in this corpus."
            ),
            "production_action": "Do not backfill 3503 from template or slot 30802 alone.",
        },
        "foreign_veto": _foreign_veto(lightning),
        "invariants": {
            "raw_damage_conserved": bool(hands.get("summary", {}).get("read_only_physical_invariant")),
            "production_files_changed": False,
            "candidate_construction_uses_timing": False,
            "candidate_construction_uses_old_label": False,
        },
        "next_evidence_needed": [
            "Decode the self-attack selector branch for 30152 / 30251 / 30311 empty-slot direct37 records.",
            "Find a Saint-specific result key or parent-child object registration for ShockofRelic instead of the globally ambiguous 30802 slot.",
            "Keep the foreign-Azuna veto separate from positive re-attribution until the current Saint entity birth/owner chain is present.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(args.output_dir / "REPORT.json", report)
    (args.output_dir / "REPORT.md").write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "confirmed_hits": confirmed_hits, "confirmed_damage": confirmed_damage}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
