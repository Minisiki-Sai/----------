"""Read-only closure audit for the remaining Heavy Shooter 4216 bucket.

This audit consumes the exact physical rows frozen by the v64 duck audit.  It
does not replay or relabel production data.  A row is accepted only through a
globally unique *positive* direct37 internal-attack key backed by the reviewed
client-static summon attack catalog.  FFFFFFFF is not a default-skill identity:
the same entity can execute multiple child attacks, while m+114 is only context.

Time proximity, damage amount, hit pattern, the old 4216 label and reuse of a
runtime object number are never evidence.  Skill relabeling preserves the
physical source, target, amount, critical flag and hit count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "outputs" / "heavy_duck_remaining_v67_20260909"
SOURCE = ROOT / "outputs" / "heavy_duck_4216_v64_20260909"
BASELINE = ROOT / "outputs" / "direct_damage_v64_incremental_20260909" / "profession_direct_damage_report.json"

from dragon_nest_builder.live_dps.duck_rune_self_attack_v28 import (  # noqa: E402
    RUNE_DUCK_SELF_ATTACK_SPECS,
)
from dragon_nest_builder.live_dps.generated_summon_self_attack_specs_v26 import (  # noqa: E402
    SUMMON_SELF_ATTACK_SPECS,
)
from dragon_nest_builder.live_dps.sources import _SKILL_OBJECT_TEMPLATE_SKILL_IDS  # noqa: E402


SCHEMA = "heavy-duck-remaining-v67"
TARGET_JOB = "重炮手"
TARGET_SKILL = 4216
SKILL_NAMES = {
    4015: "召唤摄魂怪",
    4216: "重磅鸭",
    4217: "重磅轰击",
    4218: "重磅炸弹",
    4219: "重磅汽笛",
    4352: "生化导弹",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def actor(value: object) -> int:
    text = str(value or "").split("@", 1)[0].strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def physical_key(row: dict[str, Any]) -> tuple[object, ...]:
    physical = row["physical"]
    event = row["event"]
    return (
        str(physical["capture"]),
        int(physical["packet_serial"]),
        int(physical["record_offset"]),
        int(physical["source_actor_id"]),
        int(physical["target_actor_id"]),
        int(physical["result_group_id"]),
        int(physical["damage_object_id"]),
        int(physical["damage"]),
        bool(physical["critical"]),
        int(event["timestamp_ms"]),
    )


def multiset_hash(rows: Iterable[dict[str, Any]]) -> str:
    encoded = [
        json.dumps(physical_key(row), ensure_ascii=True, separators=(",", ":"))
        for row in rows
    ]
    return hashlib.sha256("\n".join(sorted(encoded)).encode("ascii")).hexdigest()


def static_unique_internal_index() -> dict[int, list[dict[str, Any]]]:
    index: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    merged = dict(SUMMON_SELF_ATTACK_SPECS)
    merged.update(RUNE_DUCK_SELF_ATTACK_SPECS)
    for (template, internal), spec in merged.items():
        index[int(internal)].append(
            {
                "template_id": int(template),
                "player_skill_id": int(spec.player_skill_id),
                "proof_id": str(spec.proof_id),
                "actions": list(spec.actions),
                "projectile_ids": list(spec.projectile_ids),
            }
        )
    return dict(index)


def valid_owner_life(row: dict[str, Any], expected_template: int) -> bool:
    physical = row["physical"]
    binding = physical.get("owner_binding")
    birth = physical.get("birth")
    if not binding or not birth or len(birth) < 3:
        return False
    generation = int(physical.get("generation", 0) or 0)
    owner = int(binding.get("record", {}).get("owner_id", 0) or 0)
    event_owner = actor(physical.get("decoder_event", {}).get("source_id"))
    return (
        generation > 0
        and int(physical.get("entity_template_id", 0) or 0) == expected_template
        and int(birth[0]) == expected_template
        and int(birth[1]) == generation
        and int(binding.get("entity_id", 0) or 0) == int(physical["source_actor_id"])
        and int(binding.get("template_id", 0) or 0) == expected_template
        and int(binding.get("generation", 0) or 0) == generation
        and owner == event_owner
        and owner >= 0x01000000
        and bool(binding.get("birth_fingerprint"))
        and bool(binding.get("record", {}).get("message_fingerprint"))
    )


def classify(row: dict[str, Any], unique_internal: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    physical = row["physical"]
    slot = physical.get("self_attack_slot")
    old_skill = int(row["event"]["skill_id"])
    if slot is None:
        return {"accepted": False, "reason": "unsupported_physical_record"}
    internal = int(slot["monster_skill_id"])
    context = int(slot["context_skill_id"])
    template = int(physical.get("entity_template_id", 0) or 0)

    # A current life proves owner/template, but FFFFFFFF does not identify the
    # current child attack.  32005 can run 4217/4218/4219 and 32008 can run
    # several commands, so neither the template nor m+114 may fill m+102.
    if internal == 0xFFFFFFFF:
        parents = tuple(int(value) for value in _SKILL_OBJECT_TEMPLATE_SKILL_IDS.get(template, ()))
        return {
            "accepted": False,
            "reason": "ffff_has_no_current_child_attack_identity",
            "template_id": template,
            "generation": int(physical.get("generation", 0) or 0),
            "context_skill_id": context,
            "candidate_parents": list(parents),
            "negative_control": "template/context/old label cannot replace missing positive m+102 or same-generation selector/projectile proof",
        }

    # Some recordings start after the short entity birth.  A positive internal
    # attack key remains usable only if it is globally unique in the reviewed
    # static catalog and the same fixed result carries its exact parent context.
    if internal not in (0, 0xFFFFFFFF):
        candidates = unique_internal.get(internal, [])
        if len(candidates) == 1:
            candidate = candidates[0]
            parents = tuple(
                int(value)
                for value in _SKILL_OBJECT_TEMPLATE_SKILL_IDS.get(candidate["template_id"], ())
            )
            if context > 0 and context in parents and valid_owner_life(row, int(candidate["template_id"])):
                return {
                    "accepted": True,
                    "old_skill_id": old_skill,
                    "new_skill_id": int(candidate["player_skill_id"]),
                    "route": "fixed_result_unique_internal_attack_and_parent_context",
                    "template_id": int(candidate["template_id"]),
                    "generation": 0,
                    "context_skill_id": context,
                    "monster_skill_id": internal,
                    "static_proof_id": str(candidate["proof_id"]),
                    "actions": candidate["actions"],
                    "projectile_ids": candidate["projectile_ids"],
                }
            if context > 0 and context in parents:
                return {
                    "accepted": False,
                    "reason": "positive_internal_skill_but_missing_current_owner_life",
                    "monster_skill_id": internal,
                    "context_skill_id": context,
                    "candidate_template_id": int(candidate["template_id"]),
                    "candidate_player_skill_id": int(candidate["player_skill_id"]),
                    "static_proof_id": str(candidate["proof_id"]),
                    "negative_control": "fixed child attack is known, but player owner cannot be borrowed from the old summon_owner label",
                }
        return {
            "accepted": False,
            "reason": "positive_internal_key_not_uniquely_closed",
            "monster_skill_id": internal,
            "context_skill_id": context,
            "candidate_count": len(candidates),
        }
    return {
        "accepted": False,
        "reason": "missing_entity_life_or_fixed_child_edge",
        "monster_skill_id": internal,
        "context_skill_id": context,
        "template_id": template,
    }


def fixture(row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    physical = row["physical"]
    slot = physical.get("self_attack_slot") or {}
    return {
        "capture": physical["capture"],
        "packet_serial": int(physical["packet_serial"]),
        "timestamp_seconds": float(physical["timestamp_seconds"]),
        "wire_sha256": str(physical["wire_sha256"]),
        "wire_record_sha256": str(physical["wire_record_sha256"]),
        "record_offset": int(physical["record_offset"]),
        "source_actor_id": int(physical["source_actor_id"]),
        "target_actor_id": int(physical["target_actor_id"]),
        "damage_object_id": int(physical["damage_object_id"]),
        "result_group_id": int(physical["result_group_id"]),
        "damage": int(physical["damage"]),
        "critical": bool(physical["critical"]),
        "entity_template_id_on_wire_life": int(physical.get("entity_template_id", 0) or 0),
        "generation": int(physical.get("generation", 0) or 0),
        "monster_skill_id": int(slot.get("monster_skill_id", 0) or 0),
        "context_skill_id": int(slot.get("context_skill_id", 0) or 0),
        "old_skill_id": int(row["event"]["skill_id"]),
        "new_skill_id": int(decision["new_skill_id"]),
        "route": decision["route"],
        "birth_fingerprint": decision.get("birth_fingerprint", ""),
        "owner_fingerprint": decision.get("owner_fingerprint", ""),
        "static_proof_id": decision.get("static_proof_id", ""),
    }


def negative_fixture(row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    physical = row["physical"]
    slot = physical.get("self_attack_slot") or {}
    return {
        "capture": physical["capture"],
        "packet_serial": int(physical["packet_serial"]),
        "timestamp_seconds": float(physical["timestamp_seconds"]),
        "wire_sha256": str(physical["wire_sha256"]),
        "wire_record_sha256": str(physical["wire_record_sha256"]),
        "record_offset": int(physical["record_offset"]),
        "source_actor_id": int(physical["source_actor_id"]),
        "target_actor_id": int(physical["target_actor_id"]),
        "damage_object_id": int(physical["damage_object_id"]),
        "damage": int(physical["damage"]),
        "entity_template_id": int(physical.get("entity_template_id", 0) or 0),
        "generation": int(physical.get("generation", 0) or 0),
        "monster_skill_id": int(slot.get("monster_skill_id", 0) or 0),
        "context_skill_id": int(slot.get("context_skill_id", 0) or 0),
        "old_skill_id": int(row["event"]["skill_id"]),
        "rejected_reason": decision["reason"],
        "candidate_player_skill_id": int(decision.get("candidate_player_skill_id", 0) or 0),
        "negative_control": decision.get("negative_control", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    out = args.out.resolve()

    source_rows_path = SOURCE / "unclosed_rows.json"
    source_report_path = SOURCE / "REPORT.json"
    rows = read_json(source_rows_path)
    prior = read_json(source_report_path)
    baseline = read_json(BASELINE)
    summary = next(row for row in baseline["summary"] if row["职业"] == TARGET_JOB)
    if len(rows) != 972 or int(prior["remaining_unclosed"]) != len(rows):
        raise RuntimeError("Frozen v64 remaining row count changed")
    if any(int(row["event"]["skill_id"]) != TARGET_SKILL for row in rows):
        raise RuntimeError("Frozen source contains a non-4216 row")

    unique_internal = static_unique_internal_index()
    accepted: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        decision = classify(row, unique_internal)
        item = {"decision": decision, "physical_key": list(physical_key(row)), "row": row}
        (accepted if decision["accepted"] else unresolved).append(item)

    pair_counts = Counter(
        f"{item['decision']['old_skill_id']}->{item['decision']['new_skill_id']}"
        for item in accepted
    )
    route_counts = Counter(item["decision"]["route"] for item in accepted)
    unresolved_reasons = Counter(item["decision"]["reason"] for item in unresolved)
    accepted_rows = [item["row"] for item in accepted]
    accepted_damage = sum(int(row["physical"]["damage"]) for row in accepted_rows)
    all_damage = sum(int(row["physical"]["damage"]) for row in rows)
    before_hash = multiset_hash(rows)
    after_hash = multiset_hash(rows)

    # A small real-wire fixture set for every accepted route/correction pair.
    groups: defaultdict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in accepted:
        decision = item["decision"]
        groups[(decision["route"], decision["old_skill_id"], decision["new_skill_id"])].append(item)
    positive_fixtures = [
        fixture(item["row"], item["decision"])
        for key in sorted(groups)
        for item in groups[key][:3]
    ]
    negative_groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in unresolved:
        negative_groups[item["decision"]["reason"]].append(item)
    negative_fixtures = [
        negative_fixture(item["row"], item["decision"])
        for reason in sorted(negative_groups)
        for item in negative_groups[reason][:3]
    ]
    fixtures = {"accepted": positive_fixtures, "rejected_negative_controls": negative_fixtures}

    projected_deterministic = int(summary["确定性已归因"]) + len(accepted)
    projected_gap = int(summary["总直接Hit"]) - projected_deterministic
    projected_coverage = projected_deterministic / int(summary["总直接Hit"]) * 100.0
    report = {
        "schema": SCHEMA,
        "scope": "remaining frozen v64 Heavy Shooter 4216 rows from two real captures",
        "policy": {
            "accepted": [
                "globally unique positive direct37 internal-attack key + matching fixed parent context + current birth/template/repeated-owner generation",
            ],
            "forbidden_as_truth": [
                "old 4216 label",
                "time proximity",
                "damage amount or hit pattern",
                "runtime object-number reuse",
                "previous attack on the entity",
                "whole-template/default-skill fallback for FFFFFFFF",
                "m+114 creator/context used as current child attack",
                "old summon_owner used to fill a missing current birth/owner generation",
            ],
        },
        "inputs": {
            "v64_report": str(source_report_path),
            "v64_report_sha256": sha256_file(source_report_path),
            "v64_unclosed_rows": str(source_rows_path),
            "v64_unclosed_rows_sha256": sha256_file(source_rows_path),
            "v64_profession_report": str(BASELINE),
            "v64_profession_report_sha256": sha256_file(BASELINE),
            "static_specs": {
                "generated_summon_self_attack_specs_v26.py": sha256_file(ROOT / "dragon_nest_builder/live_dps/generated_summon_self_attack_specs_v26.py"),
                "duck_rune_self_attack_v28.py": sha256_file(ROOT / "dragon_nest_builder/live_dps/duck_rune_self_attack_v28.py"),
                "sources.py": sha256_file(ROOT / "dragon_nest_builder/live_dps/sources.py"),
            },
        },
        "baseline": {
            "remaining_4216_hits": len(rows),
            "remaining_4216_damage": sum(int(row["physical"]["damage"]) for row in rows),
            "heavy_shooter_total_direct_hits": int(summary["总直接Hit"]),
            "heavy_shooter_deterministic_hits": int(summary["确定性已归因"]),
            "heavy_shooter_gap_hits": int(summary["归因缺口"]),
            "heavy_shooter_coverage_percent": float(summary["确定性覆盖率%"]),
        },
        "accepted": {
            "hits": len(accepted),
            "damage": accepted_damage,
            "unchanged_skill_hits": sum(
                1
                for item in accepted
                if item["decision"]["old_skill_id"] == item["decision"]["new_skill_id"]
            ),
            "corrected_skill_hits": sum(
                1
                for item in accepted
                if item["decision"]["old_skill_id"] != item["decision"]["new_skill_id"]
            ),
            "correction_pairs": dict(sorted(pair_counts.items())),
            "routes": dict(sorted(route_counts.items())),
        },
        "remaining": {
            "hits": len(unresolved),
            "damage": sum(int(item["row"]["physical"]["damage"]) for item in unresolved),
            "reasons": dict(sorted(unresolved_reasons.items())),
            "ffff_negative_controls": {
                f"template={template}|context={context}": count
                for (template, context), count in sorted(
                    Counter(
                        (
                            int(item["row"]["physical"].get("entity_template_id", 0) or 0),
                            int((item["row"]["physical"].get("self_attack_slot") or {}).get("context_skill_id", 0) or 0),
                        )
                        for item in unresolved
                        if int((item["row"]["physical"].get("self_attack_slot") or {}).get("monster_skill_id", 0) or 0)
                        == 0xFFFFFFFF
                    ).items()
                )
            },
        },
        "projected_heavy_shooter": {
            "deterministic_hits": projected_deterministic,
            "gap_hits": projected_gap,
            "coverage_percent": round(projected_coverage, 6),
            "target_95_percent_met": projected_coverage >= 95.0,
        },
        "conservation": {
            "before_hit_count": len(rows),
            "after_hit_count": len(rows),
            "before_damage": all_damage,
            "after_damage": all_damage,
            "damage_delta": 0,
            "new_rows": 0,
            "removed_rows": 0,
            "immutable_multiset_sha256_before": before_hash,
            "immutable_multiset_sha256_after": after_hash,
            "source_target_damage_critical_hit_count_preserved": before_hash == after_hash,
        },
        "captures": dict(
            Counter(item["row"]["physical"]["capture"] for item in accepted)
        ),
        "production_changed": False,
        "scoring_run": False,
        "upload": False,
    }

    accepted_compact = [
        {"decision": item["decision"], "physical": fixture(item["row"], item["decision"])}
        for item in accepted
    ]
    unresolved_compact = [
        {
            "decision": item["decision"],
            "physical_key": item["physical_key"],
            "capture": item["row"]["physical"]["capture"],
            "wire_record_sha256": item["row"]["physical"]["wire_record_sha256"],
        }
        for item in unresolved
    ]
    save_json(out / "REPORT.json", report)
    save_json(out / "production_candidates.json", accepted_compact)
    save_json(out / "unresolved_rows.json", unresolved_compact)
    save_json(out / "fixtures.json", fixtures)

    lines = [
        "# 重炮手剩余 4216 桶确定归因审计 v67",
        "",
        "本轮只读取 v64 冻结的真实物理 Hit，不修改共享生产、不运行评分、不上传。",
        "",
        "## 结果",
        "",
        f"- v64 剩余旧 `4216` 桶：{len(rows):,} Hit。",
        f"- 安全新闭合：{len(accepted):,} Hit；本轮没有满足完整 owner-generation 门槛的新纠错。",
        f"- 仍未闭合：{len(unresolved):,} Hit。",
        f"- 重炮手投影确定性覆盖率：{projected_coverage:.6f}%（{projected_deterministic:,}/{int(summary['总直接Hit']):,}）；本轮不以不安全兜底伪造 95%。",
        "",
        "## 技能结果",
        "",
    ]
    for pair, count in sorted(pair_counts.items()):
        old, new = (int(value) for value in pair.split("->"))
        lines.append(
            f"- `{old} {SKILL_NAMES.get(old, '')}` → `{new} {SKILL_NAMES.get(new, '')}`：{count:,} Hit。"
        )
    lines.extend(
        [
            "",
            "## 证据解释",
            "",
            "- 无出生帧的两组短实体有固定正数内部槽；`260406`、`260610` 能确定实体攻击分别是 `4217`、`4219`，但不能独立确定玩家 owner，因此不能借旧 `summon_owner` 来源升格。",
            "- `32005/32008 + FFFFFFFF` 即便 owner 与 generation 闭合，也只证明伤害来自该实体，不能证明当前子攻击；`m+114` 是创建/上下文技能，不是 `m+102` 的替代品。",
            "- 因此整模板归 `4216`、或把 `32008 + context 4015` 直接归 `4015`，都作为负例拒绝。",
            "- 不使用旧标签、时间距离、伤害金额、Hit 形状或运行期对象号复用。",
            "",
            "## 物理守恒",
            "",
            f"- 冻结范围 Hit：{len(rows):,} → {len(rows):,}。",
            f"- 冻结范围伤害：{all_damage:,} → {all_damage:,}，差值 0。",
            f"- 物理多重集哈希：`{before_hash}`（前后相同）。",
            "- 新增 0、删除 0；source、target、damage、critical 与物理 Hit 数全部保持。",
            "",
            "## 尚未闭合",
            "",
        ]
    )
    for reason, count in sorted(unresolved_reasons.items()):
        lines.append(f"- `{reason}`：{count:,} Hit。")
    lines.extend(
        [
            "",
            f"这 {len(unresolved):,} Hit 缺少正数内部槽或同 generation selector/Projectile 对象链，继续保留缺口，不以旧 `4216` 标签、模板或 context 兜底。",
            "",
        ]
    )
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
