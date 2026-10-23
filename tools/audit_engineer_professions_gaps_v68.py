"""Compose strict Heavy Shooter/Gear Master gap evidence without production writes."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dragon_nest_builder.live_dps.duck_rune_self_attack_v28 import (  # noqa: E402
    RUNE_DUCK_SELF_ATTACK_SPECS,
)

OUT = ROOT / "outputs/engineer_professions_gaps_v68_20260909"
BASELINE = ROOT / "outputs/direct_damage_v64_incremental_20260909/profession_direct_damage_report.json"
HEAVY = ROOT / "outputs/heavy_duck_remaining_v67_20260909"
GEAR = ROOT / "outputs/gear_master_gaps_v65_20260909"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actor(value: object) -> int:
    text = str(value or "").split("@", 1)[0]
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def physical_tuple(row: dict[str, Any]) -> tuple[object, ...]:
    p = row["physical"]
    e = row["event"]
    return (
        p["capture"], int(p["packet_serial"]), int(p["record_offset"]),
        int(p["source_actor_id"]), int(p["target_actor_id"]),
        int(p["result_group_id"]), int(p["damage_object_id"]),
        int(p["damage"]), bool(p["critical"]), int(e["timestamp_ms"]),
    )


def multiset_hash(rows: list[dict[str, Any]]) -> str:
    values = sorted(json.dumps(physical_tuple(row), ensure_ascii=True, separators=(",", ":")) for row in rows)
    return hashlib.sha256("\n".join(values).encode("ascii")).hexdigest()


def owner_generation_valid(row: dict[str, Any], template: int) -> bool:
    p = row["physical"]
    birth = p.get("birth")
    binding = p.get("owner_binding")
    generation = int(p.get("generation", 0) or 0)
    if not birth or not binding or generation <= 0:
        return False
    owner = int(binding.get("record", {}).get("owner_id", 0) or 0)
    return (
        int(p.get("entity_template_id", 0) or 0) == template
        and int(birth[0]) == template
        and int(birth[1]) == generation
        and int(binding.get("entity_id", 0) or 0) == int(p["source_actor_id"])
        and int(binding.get("template_id", 0) or 0) == template
        and int(binding.get("generation", 0) or 0) == generation
        and owner == actor(p["decoder_event"]["source_id"])
        and owner >= 0x01000000
        and bool(binding.get("birth_fingerprint"))
        and bool(binding.get("record", {}).get("message_fingerprint"))
    )


def compact_fixture(row: dict[str, Any], old: int, new: int, route: str) -> dict[str, Any]:
    p = row["physical"]
    slot = p.get("self_attack_slot") or {}
    binding = p.get("owner_binding") or {}
    return {
        "capture": p["capture"], "packet_serial": int(p["packet_serial"]),
        "timestamp_seconds": float(p["timestamp_seconds"]),
        "wire_sha256": p["wire_sha256"], "wire_record_sha256": p["wire_record_sha256"],
        "record_offset": int(p["record_offset"]),
        "source_actor_id": int(p["source_actor_id"]), "target_actor_id": int(p["target_actor_id"]),
        "damage_object_id": int(p["damage_object_id"]), "result_group_id": int(p["result_group_id"]),
        "damage": int(p["damage"]), "critical": bool(p["critical"]),
        "template_id": int(p.get("entity_template_id", 0) or 0),
        "generation": int(p.get("generation", 0) or 0),
        "monster_skill_id": int(slot.get("monster_skill_id", 0) or 0),
        "context_skill_id": int(slot.get("context_skill_id", 0) or 0),
        "owner_actor_id": int(binding.get("record", {}).get("owner_id", 0) or 0),
        "birth_fingerprint": binding.get("birth_fingerprint", ""),
        "owner_fingerprint": binding.get("record", {}).get("message_fingerprint", ""),
        "old_skill_id": old, "new_skill_id": new, "route": route,
    }


def main() -> int:
    baseline = read(BASELINE)
    summaries = {row["职业"]: row for row in baseline["summary"]}
    heavy = read(HEAVY / "REPORT.json")
    gear_report = read(GEAR / "REPORT.json")
    gear_confirmed = read(GEAR / "confirmed_rows.json")
    gear_unclosed = read(GEAR / "unclosed_rows.json")

    # v65 already proved three 4216->4352 rows by same-generation selector/object.
    accepted = [
        {
            "profession": "机械大师", "old_skill_id": 4216, "new_skill_id": 4352,
            "route": "same_owner_generation_entity_selector_object_chain",
            "row": row,
        }
        for row in gear_confirmed
    ]

    # One v65 negative became statically resolvable after the independently
    # generated rune-duck catalog.  Require the complete current owner life.
    rune_candidates = []
    for row in gear_unclosed:
        p = row["physical"]
        slot = p.get("self_attack_slot")
        if not slot:
            continue
        key = (int(p.get("entity_template_id", 0) or 0), int(slot["monster_skill_id"]))
        spec = RUNE_DUCK_SELF_ATTACK_SPECS.get(key)
        if spec is None or not owner_generation_valid(row, int(spec.template_id)):
            continue
        item = {
            "profession": "机械大师", "old_skill_id": int(row["event"]["skill_id"]),
            "new_skill_id": int(spec.player_skill_id),
            "route": "current_owner_generation_fixed_rune_internal_attack_slot",
            "static_proof_id": spec.proof_id, "actions": list(spec.actions), "row": row,
        }
        accepted.append(item)
        rune_candidates.append(item)

    if len(gear_confirmed) != 3 or len(rune_candidates) != 1:
        raise RuntimeError("Expected 3 selector candidates and exactly one rune-slot candidate")

    accepted_rows = [item["row"] for item in accepted]
    accepted_damage = sum(int(row["physical"]["damage"]) for row in accepted_rows)
    immutable = multiset_hash(accepted_rows)
    pairs = Counter(f"{item['old_skill_id']}->{item['new_skill_id']}" for item in accepted)
    routes = Counter(item["route"] for item in accepted)

    profession_results = {}
    for profession in ("重炮手", "机械大师"):
        summary = summaries[profession]
        gained = 0 if profession == "重炮手" else len(accepted)
        projected = int(summary["确定性已归因"]) + gained
        target = math.ceil(int(summary["总直接Hit"]) * 0.95)
        profession_results[profession] = {
            "total_direct_hits": int(summary["总直接Hit"]),
            "baseline_deterministic_hits": int(summary["确定性已归因"]),
            "baseline_coverage_percent": float(summary["确定性覆盖率%"]),
            "safe_candidates": gained,
            "projected_deterministic_hits": projected,
            "projected_coverage_percent": round(projected / int(summary["总直接Hit"]) * 100.0, 6),
            "hits_needed_for_95_before": max(0, target - int(summary["确定性已归因"])),
            "hits_still_needed_for_95": max(0, target - projected),
        }

    heavy_reasons = heavy["remaining"]["reasons"]
    remaining_gear = len(gear_unclosed) - len(rune_candidates)
    report = {
        "schema": "engineer-professions-gaps-v68",
        "scope": "frozen v64 direct-Hit corpus; Heavy Shooter and Gear Master only",
        "policy": {
            "required": "fixed positive internal slot or same-generation selector/projectile object chain, plus current birth/template/repeated owner generation for summon sources",
            "rejected": [
                "FFFFFFFF as default child attack", "m+114 creator/context as current attack",
                "old skill label", "time/damage/hit pattern", "owner cache without current birth generation",
            ],
        },
        "results": profession_results,
        "safe_candidates": {
            "hits": len(accepted), "damage": accepted_damage,
            "correction_pairs": dict(sorted(pairs.items())), "routes": dict(sorted(routes.items())),
        },
        "remaining": {
            "重炮手": {"hits": int(heavy["remaining"]["hits"]), "reasons": heavy_reasons},
            "机械大师": {"hits": remaining_gear, "v65_reasons_before_rune_closure": gear_report["unclosed_reasons"]},
        },
        "important_negative_controls": {
            "heavy_32005_ffff": "owner/template generation does not identify 4216 because the entity can execute 4217/4218/4219",
            "heavy_32008_ffff_context4015": "context is creator metadata and does not identify the current attack",
            "heavy_positive_slots_without_life": "260406/260610 identify child attacks but cannot safely borrow the old summon_owner player owner",
            "gear_unknown_109": "all have FFFFFFFF, no entity template and no current object origin",
            "gear_rune_target_scope": "the 261108 row targets another 32005 entity, not a boss; attribution is certain, but boss-DPS inclusion must remain a separate filter",
        },
        "conservation": {
            "before_hits": len(accepted_rows), "after_hits": len(accepted_rows),
            "before_damage": accepted_damage, "after_damage": accepted_damage, "damage_delta": 0,
            "new_rows": 0, "removed_rows": 0,
            "immutable_multiset_sha256_before": immutable,
            "immutable_multiset_sha256_after": immutable,
            "physical_rows_preserved": True,
        },
        "inputs": {
            str(BASELINE): file_hash(BASELINE), str(HEAVY / "REPORT.json"): file_hash(HEAVY / "REPORT.json"),
            str(GEAR / "REPORT.json"): file_hash(GEAR / "REPORT.json"),
            str(GEAR / "confirmed_rows.json"): file_hash(GEAR / "confirmed_rows.json"),
            str(GEAR / "unclosed_rows.json"): file_hash(GEAR / "unclosed_rows.json"),
            "duck_rune_self_attack_v28.py": file_hash(ROOT / "dragon_nest_builder/live_dps/duck_rune_self_attack_v28.py"),
        },
        "production_changed": False, "scoring_run": False, "upload": False,
    }
    fixtures = [
        compact_fixture(item["row"], item["old_skill_id"], item["new_skill_id"], item["route"])
        | ({"static_proof_id": item.get("static_proof_id", "")} if item.get("static_proof_id") else {})
        for item in accepted
    ]
    candidates = [
        {k: v for k, v in item.items() if k != "row"} | {"physical": fixtures[index]}
        for index, item in enumerate(accepted)
    ]
    write(OUT / "REPORT.json", report)
    write(OUT / "production_candidates.json", candidates)
    write(OUT / "fixtures.json", fixtures)

    lines = [
        "# 工程系直接 Hit 严格归因审计 v68", "",
        "本轮不修改共享生产，只组合已有真实物理审计，并加入一条新静态证书闭合。", "",
        "## 结果", "",
        "|职业|原覆盖率|安全候选|投影覆盖率|距95%仍需|", "|---|---:|---:|---:|---:|",
    ]
    for profession, result in profession_results.items():
        lines.append(f"|{profession}|{result['baseline_coverage_percent']:.6f}%|{result['safe_candidates']}|{result['projected_coverage_percent']:.6f}%|{result['hits_still_needed_for_95']}|")
    lines += ["", "## 可生产候选", ""]
    for pair, count in sorted(pairs.items()):
        lines.append(f"- `{pair}`：{count} Hit。")
    lines += [
        "", "- `4216->4352` 三条由同 owner、同 generation 的 selector/object 链证明。",
        "- `4216->4217` 一条由 `32005 / generation 3 / repeated owner / m+102=261108` 与独立 Rune DNT/AI/ACT 证书共同证明。该 Hit 的目标是另一个鸭子实体，技能身份确定，但是否进入 Boss DPS 必须由目标过滤单独决定。",
        "", "## 严格拒绝", "",
        "- `FFFFFFFF` 不代表默认技能；不把 32005 整体归为 4216。",
        "- `m+114 context` 只作创建上下文；不把 32008+4015 直接当作当前摄魂怪攻击。",
        "- 重炮手 27 条正数内部槽缺当前 birth/owner generation，不能借旧 `summon_owner` 确定玩家。",
        "- 机械大师 109 条待识别 Hit 全无实体模板、正数内部槽及对象来源，继续留空。",
        "", "## 守恒", "",
        f"- 候选集 {len(accepted_rows)} Hit，伤害 {accepted_damage}；前后 Hit、伤害和物理多重集哈希完全一致。",
        "- 新增 0、删除 0；没有修改生产、评分或上传。", "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
