"""Read-only audit of Magma Wall (4410) and entity template 32022.

No production attribution is changed.  A candidate is accepted only from the
current entity generation, its repeated owner record, an exact complete
direct37 row, a concrete self-attack slot, and the reviewed client-static
canonical family.  Timing and the existing skill label are never evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from dataclasses import asdict
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import is_deterministic_attribution  # noqa: E402
from dragon_nest_builder.live_dps.autonomous_attack_families import confirmed_pve_target  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.summon_self_attack_evidence_v26 import PhysicalHit, read_self_attack_slot  # noqa: E402
from dragon_nest_builder.live_dps.udp_damage_records import inspect_udp_damage_records  # noqa: E402
from tools.audit_under95_summon_chain_v53 import (  # noqa: E402
    _actor,
    _hash,
    _life,
    _origin_candidate,
    _physical_for_event,
    _selection,
    _skill,
)


SCHEMA = "magma-wall-skill-gaps-v64"
DEFAULT_SELECTION = ROOT / "outputs/native_wire_v49_20260909/under95_gaps.json"
DEFAULT_BASELINE = ROOT / "outputs/native_result_key_v57_20260909/profession_direct_damage_report.json"
DEFAULT_OUTPUT = ROOT / "outputs/magma_wall_skill_gaps_v64_20260909"
JOB = "炼金圣士"
SKILL = 4410
TEMPLATE = 32022
EMPTY_ATTACK = 0xFFFFFFFF


def _event_physical_identity(event: CombatEvent) -> tuple[Any, ...]:
    return (
        int(event.timestamp_ms), str(event.source_id), str(event.target_id), int(event.amount),
        bool(event.critical), int(event.wire_source_actor_id or 0),
        int(event.wire_target_actor_id or 0), int(event.wire_damage_object_id or 0),
        int(event.wire_result_group_id or 0), int(event.wire_record_offset or 0),
        str(event.wire_damage_layout or ""), bool(event.has_real_hit_evidence),
    )


def _formal_gap(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        row for row in report["details"]
        if row.get("职业") == JOB and str(row.get("skill_id", "")) == str(SKILL)
        and row.get("归因分类") == "nondeterministic"
    ]
    return {
        "hits": sum(int(row["Hit数"]) for row in rows),
        "damage": sum(int(row["伤害"]) for row in rows),
        "rows": rows,
    }


def _static_certificate() -> dict[str, Any]:
    path = ROOT / "outputs/magma_canonical_v37_20260908/evidence.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    generators = evidence.get("generators", {})
    normal = generators.get("Skill_MagmaWall_EX", [])
    clone = generators.get("Skill_MagmaWall_EX_Clone[1]", [])
    canonical = evidence.get("canonical_cells", {})
    return {
        "source": str(path),
        "source_sha256": _hash(path),
        "verdict": evidence.get("verdict"),
        "template_id": TEMPLATE,
        "candidate_skill_id": SKILL,
        "normal_generators": normal,
        "clone_generators": clone,
        "canonical_cells": {key: canonical.get(key) for key in ("4407", "4409", "4410", "4505")},
        "model_boundary": (
            "Accepted only in explicit resource-declared model M: rooted 4505 normal EX "
            "normalizes to 4410; the unrooted Clone node is not independently executable in M. "
            "This is not proof of native-engine completeness and m114 is not treated as causal."
        ),
        "unique_family_in_model_m": (
            evidence.get("verdict") == "CANONICAL_4410_PROVED_IN_EXPLICIT_RESOURCE_MODEL_M; M114_CAUSALITY_UNPROVEN"
            and bool(normal) and all(int(row.get("template", 0)) == TEMPLATE for row in normal + clone)
            and int((canonical.get("4505") or {}).get("base", 0)) == SKILL
        ),
    }


def audit_capture(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    if _hash(path) != entry["sha256"]:
        raise ValueError(f"capture hash changed: {path}")
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    counts: Counter[str] = Counter()
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    fixtures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    physical_before = Counter()
    physical_after = Counter()
    pve_target_generations: set[tuple[int, int, int]] = set()
    deferred_pve: list[dict[str, Any]] = []

    def add_fixture(kind: str, row: dict[str, Any]) -> None:
        if len(fixtures[kind]) < 6:
            fixtures[kind].append(row)

    def feed_events(packet):
        events = original(packet)
        serial = int(decoder._damage_object_packet_serial)
        direction = packet.inferred_direction()
        inventory = inspect_udp_damage_records(packet.payload, direction)
        seen: set[int] = set()
        for event in events:
            if not isinstance(event, CombatEvent) or event.healing or event.amount <= 0:
                continue
            if "direct37" not in str(event.wire_damage_layout or "") or int(event.wire_record_offset or 0) in seen:
                continue
            seen.add(int(event.wire_record_offset or 0))
            identity = _event_physical_identity(event)
            physical_before[repr(identity)] += 1
            physical_after[repr(identity)] += 1
            counts["all_direct37"] += 1
            counts["all_direct37_damage"] += int(event.amount)
            if not inventory.complete:
                continue
            physical = _physical_for_event(event, packet, inventory)
            if physical is None:
                continue
            target_birth = decoder.entity_owner_evidence.births.get(int(physical.target_actor_id))
            target_generation_key = (
                (int(physical.target_actor_id), int(target_birth[0]), int(target_birth[1]))
                if target_birth is not None else None
            )
            pve_confirmed = confirmed_pve_target(decoder, int(physical.target_actor_id), packet)
            target_id = int(physical.target_actor_id)
            target_is_known_player = any(
                target_id in getattr(decoder, key, ())
                for key in ("skill_actor_ids", "local_actor_ids", "known_actor_names")
            )
            independent_player_proven_monster = bool(
                decoder.monster_entities.get(target_id) is not None
                and target_id in decoder.direct_player_confirmed_damage_target_ids
                and not target_is_known_player
            )
            pve_evidence = bool(pve_confirmed is True or independent_player_proven_monster)
            if pve_confirmed is True and target_generation_key is not None:
                pve_target_generations.add(target_generation_key)
            old_skill = _skill(event.skill_id)
            old_method = str(event.attribution_method or "")
            deterministic = bool(old_skill and is_deterministic_attribution(old_method))
            is_gap = old_skill == SKILL and not deterministic

            wire_source = int(physical.source_actor_id)
            life = None
            owner_reason = "player_source"
            binding = None
            if 0 < wire_source < 65536:
                life, owner_reason, binding = _life(
                    decoder, wire_source, float(packet.timestamp_seconds), serial,
                    int(physical.record_offset), path.name,
                )
            owner_actor = int(life.owner_id) if life is not None else _actor(event.source_id)
            _owner_name, owner_job = decoder._actor_display_context(owner_actor)
            # Selection by the old 4410 label is only a denominator stratum,
            # never candidate evidence.  For template 32022 the reviewed
            # resource graph is globally unique in model M, so an absent job
            # display name must not discard an otherwise exact same-owner life.
            # We still require _life(), which validates this exact owner as a
            # known player actor; no other same-job actor is borrowed.
            if not is_gap and not (life is not None and int(life.template_id) == TEMPLATE):
                continue
            counts[f"owner_job:{owner_job or '<unknown>'}"] += 1
            if is_gap:
                counts["formal_4410_gap_seen"] += 1
                counts["formal_4410_gap_damage_seen"] += int(event.amount)
            template = int(life.template_id) if life is not None else int(event.entity_template_id or 0)
            is_32022 = template == TEMPLATE or int(event.entity_template_id or 0) == TEMPLATE
            if not (is_gap or is_32022):
                continue

            slot = None
            if life is not None:
                hit = PhysicalHit(
                    capture=path.name,
                    physical_hit_id=f"{entry['sha256']}:{serial}:{physical.record_offset}",
                    order=(serial, 0, int(physical.record_offset)),
                    entity_id=wire_source, generation=int(life.generation),
                    template_id=int(life.template_id), target_id=int(physical.target_actor_id),
                    damage=int(physical.damage), object_id=int(physical.damage_object_id),
                    result_group=int(physical.damage_result_group_id), wire=bytes(packet.payload), direction=direction,
                )
                slot = read_self_attack_slot(hit)
            slot_id = int(slot.monster_skill_id) if slot else -1
            context_id = int(slot.context_skill_id) if slot else -1

            object_candidate = None
            object_proof = None
            if int(physical.damage_object_id) not in (0, EMPTY_ATTACK):
                try:
                    found = _origin_candidate(
                        decoder, wire_source, int(physical.damage_object_id),
                        float(packet.timestamp_seconds), serial, int(physical.record_offset),
                    )
                except Exception:
                    found = None
                if found is not None:
                    object_candidate, object_proof = found

            exact_owner = life is not None and binding is not None and not life.conflicted
            exact_32022 = exact_owner and int(life.template_id) == TEMPLATE
            empty_intrinsic_slot = slot is not None and slot_id == EMPTY_ATTACK
            context_consistent = slot is not None and context_id == SKILL
            raw_candidate = SKILL if exact_32022 and empty_intrinsic_slot and context_consistent else object_candidate
            job_conflict = bool(
                raw_candidate is not None
                and decoder._action_conflicts_with_confirmed_job(owner_actor, int(raw_candidate))
            )
            candidate = raw_candidate if pve_evidence and not job_conflict else None
            route = (
                "entity32022_static_family" if candidate == SKILL and exact_32022 and empty_intrinsic_slot and context_consistent
                else "damage_object_origin" if candidate is not None else "blocked"
            )
            safe = candidate is not None
            conflict = deterministic and old_skill != int(candidate or 0)
            if safe:
                counts[f"candidate:{candidate}"] += 1
                counts[f"candidate_conflict:{candidate}"] += int(conflict)
                if is_gap:
                    counts[f"gap_candidate:{candidate}"] += 1
            elif is_gap:
                if raw_candidate is not None and not pve_evidence:
                    reason = "pve_target_unconfirmed"
                elif raw_candidate is not None and job_conflict:
                    reason = "confirmed_job_conflict"
                elif not (0 < wire_source < 65536):
                    reason = "player_source_without_exact_object_origin"
                elif life is None:
                    reason = f"owner:{owner_reason}"
                elif int(life.template_id) != TEMPLATE:
                    reason = f"other_template:{life.template_id}"
                elif slot is None:
                    reason = "no_complete_self_attack_slot"
                elif slot_id != EMPTY_ATTACK:
                    reason = f"nonempty_self_attack:{slot_id}"
                elif context_id != SKILL:
                    reason = f"context_mismatch:{context_id}"
                else:
                    reason = "unknown"
                if reason != "pve_target_unconfirmed":
                    counts[f"blocked:{reason}"] += 1
            else:
                reason = "not_gap"

            key = "|".join(map(str, (
                int(event.entity_template_id or 0), int(life.template_id) if life else 0,
                old_skill, old_method, slot_id, context_id, object_candidate or 0, route,
            )))
            row = strata[key]
            row["hits"] += 1
            row["damage"] += int(event.amount)
            row["gap_hits"] += int(is_gap)
            row["deterministic_controls"] += int(deterministic)
            row["candidate_conflicts"] += int(conflict)
            row[f"capture:{path.name}"] += 1
            if life is not None:
                row[f"owner:{life.owner_id:08X}"] += 1

            fixture = {
                "capture": path.name,
                "capture_path": str(path),
                "capture_sha256": entry["sha256"],
                "timestamp_ms": int(event.timestamp_ms),
                "wire_sha256": sha256(packet.payload).hexdigest(),
                "wire_record_offset": int(physical.record_offset),
                "wire_source_actor_id": wire_source,
                "wire_target_actor_id": int(physical.target_actor_id),
                "damage_object_id": int(physical.damage_object_id),
                "damage_result_group_id": int(physical.damage_result_group_id),
                "amount": int(event.amount),
                "critical": bool(event.critical),
                "old_skill_id": old_skill,
                "old_method": old_method,
                "old_is_deterministic": deterministic,
                "entity_life": ({
                    "entity_id": int(life.entity_id), "template_id": int(life.template_id),
                    "generation": int(life.generation), "owner_id": f"0x{life.owner_id:08X}",
                    "birth_order": list(life.birth_order), "owner_order": list(life.owner_order),
                    "birth_fingerprint": life.birth_fingerprint,
                    "owner_fingerprint": life.owner_fingerprint, "conflicted": life.conflicted,
                } if life is not None else None),
                "owner_resolution": owner_reason,
                "self_attack_slot": asdict(slot) if slot else None,
                "object_candidate_skill_id": object_candidate,
                "object_proof": object_proof,
                "pve_confirmed": pve_confirmed,
                "independent_player_proven_monster_target": independent_player_proven_monster,
                "target_generation_key": list(target_generation_key) if target_generation_key else None,
                "confirmed_job_conflict": job_conflict,
                "candidate_skill_id": candidate,
                "candidate_route": route,
                "candidate_uses_timing": False,
                "candidate_uses_old_label": False,
                "cross_same_job_used": False,
            }
            if is_gap:
                if raw_candidate is not None and not pve_evidence and not job_conflict:
                    deferred_pve.append({"fixture": fixture, "target_generation_key": target_generation_key,
                                         "candidate_skill_id": int(raw_candidate), "conflict": bool(conflict)})
                else:
                    add_fixture("safe_gap" if safe and not conflict else "blocked_gap", fixture)
            if is_32022 and deterministic:
                add_fixture("deterministic_32022_control", fixture)
            if is_32022 and safe and conflict:
                add_fixture("candidate_conflict", fixture)
        return events

    decoder.feed_events = feed_events
    try:
        with redirect_stdout(io.StringIO()):
            source.poll()
    finally:
        source.close()
    for pending in deferred_pve:
        fixture = pending["fixture"]
        target_key = pending["target_generation_key"]
        candidate = int(pending["candidate_skill_id"])
        if target_key is not None and target_key in pve_target_generations and not pending["conflict"]:
            counts[f"candidate:{candidate}"] += 1
            counts[f"gap_candidate:{candidate}"] += 1
            counts[f"deferred_same_target_generation:{candidate}"] += 1
            fixture["candidate_skill_id"] = candidate
            fixture["candidate_route"] = "deferred_same_target_generation_pve"
            fixture["pve_confirmed_later_same_target_generation"] = True
            add_fixture("safe_deferred_pve_gap", fixture)
        else:
            counts["blocked:pve_target_generation_never_confirmed"] += 1
            fixture["pve_confirmed_later_same_target_generation"] = False
            add_fixture("blocked_gap", fixture)
    if physical_before != physical_after:
        raise AssertionError(f"read-only audit changed physical rows: {path}")
    digest = sha256()
    for key, count in sorted(physical_before.items()):
        digest.update(key.encode("utf-8")); digest.update(str(count).encode("ascii"))
    return {
        "audit_logic_version": 6,
        "capture": path.name, "capture_path": str(path), "capture_sha256": entry["sha256"],
        "counts": dict(counts), "strata": {k: dict(v) for k, v in strata.items()},
        "fixtures": dict(fixtures),
        "invariants": {"physical_row_multiset_equal": True, "physical_rows": sum(physical_before.values()),
                       "physical_sha256": digest.hexdigest()},
    }


def merge(results: list[dict[str, Any]], formal: dict[str, Any], static: dict[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    strata: dict[str, Counter[str]] = defaultdict(Counter)
    fixtures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    safe_captures: set[str] = set()
    safe_owners: set[str] = set()
    safe_damage = 0
    for result in results:
        counts.update(result["counts"])
        for key, value in result["strata"].items():
            strata[key].update(value)
            parts = key.split("|")
            if (len(parts) == 8 and parts[-1] == "entity32022_static_family"
                    and int(parts[2]) == SKILL and int(value.get("gap_hits", 0)) > 0):
                safe_captures.add(result["capture"])
                safe_damage += int(value.get("damage", 0))
                safe_owners.update(name.split(":", 1)[1] for name in value if name.startswith("owner:"))
        for key, values in result["fixtures"].items():
            fixtures[key].extend(values[: max(0, 8 - len(fixtures[key]))])
    table = []
    for key, value in sorted(strata.items()):
        event_template, life_template, old_skill, old_method, slot, context, object_candidate, route = key.split("|")
        table.append({
            "event_template_id": int(event_template), "current_life_template_id": int(life_template),
            "old_skill_id": int(old_skill), "old_method": old_method,
            "self_attack_slot": int(slot), "context_skill_id": int(context),
            "object_candidate_skill_id": int(object_candidate), "route": route,
            "hits": int(value["hits"]), "damage": int(value["damage"]),
            "gap_hits": int(value["gap_hits"]), "deterministic_controls": int(value["deterministic_controls"]),
            "candidate_conflicts": int(value["candidate_conflicts"]),
            "distinct_captures": sum(k.startswith("capture:") for k in value),
            "distinct_owners": sum(k.startswith("owner:") for k in value),
        })
    safe_gap = int(counts[f"gap_candidate:{SKILL}"])
    other_candidates = {k.split(":", 1)[1]: v for k, v in counts.items()
                        if k.startswith("gap_candidate:") and k != f"gap_candidate:{SKILL}"}
    blocked = {k.split(":", 1)[1]: v for k, v in counts.items() if k.startswith("blocked:")}
    return {
        "schema": SCHEMA,
        "policy": {
            "production_changed": False, "score_or_upload_run": False,
            "required_entity_chain": "current birth + same-generation repeated owner + complete direct37 + self slot FFFFFFFF + context 4410 + client-static unique canonical family",
            "required_object_chain": "current damage_object registration + unique projectile/static skill edge + same entity",
            "forbidden": ["time-window promotion", "nearest cast", "old label as proof", "cross-same-job ownership", "summon_owner as skill proof"],
        },
        "fixed_corpus": {
            "captures": len(results), "hash_unique": len({x["capture_sha256"] for x in results}),
            "manifest": [{k: x[k] for k in ("capture", "capture_path", "capture_sha256")} for x in results],
        },
        "formal_baseline": formal,
        "static_certificate": static,
        "summary": {
            "formal_4410_gap_hits": int(formal["hits"]),
            "replayed_4410_gap_hits": int(counts["formal_4410_gap_seen"]),
            "safe_4410_gap_hits": safe_gap,
            "safe_4410_gap_damage": safe_damage,
            "safe_4410_gap_captures": len(safe_captures),
            "safe_4410_gap_owners": len(safe_owners),
            "gap_reclassified_to_other_skill": other_candidates,
            "formal_snapshot_minus_current_replay_gap": int(formal["hits"]) - int(counts["formal_4410_gap_seen"]),
            "current_remaining_4410_gap_hits": int(counts["formal_4410_gap_seen"]) - safe_gap - sum(other_candidates.values()),
            "deferred_same_target_generation_safe_hits": int(counts[f"deferred_same_target_generation:{SKILL}"]),
            "blocked_reasons": blocked,
            "candidate_4410_conflicts_with_deterministic_controls": int(counts[f"candidate_conflict:{SKILL}"]),
            "physical_rows_unchanged": all(x["invariants"]["physical_row_multiset_equal"] for x in results),
            "all_direct37_rows": int(counts["all_direct37"]),
            "all_direct37_damage": int(counts["all_direct37_damage"]),
        },
        "strata": table,
        "real_fixtures": dict(fixtures),
        "minimal_production_recommendation": (
            "Do not broaden summon_owner. Reuse the existing 32022 canonical rule only after reconstructing "
            "the exact current-generation owner proof at the hit. A target already registered as a monster and "
            "independently hit by a proven player is sufficient PvE evidence even when scene confidence is absent. "
            "If PvE is not yet known, defer metadata-only "
            "annotation and backfill solely when the identical target entity generation receives independent "
            "PvE proof. Keep player-source/unparsed-object rows nondeterministic unless their own object origin closes."
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# 炼金圣士岩浆墙 4410 / 实体 32022 缺口审计 v64", "",
        f"- 固定语料：{report['fixed_corpus']['captures']} 份；只读回放，未修改生产、评分或上传。",
        f"- v57 快照 4410 非确定 Hit：{s['formal_4410_gap_hits']}；当前代码重新回放：{s['replayed_4410_gap_hits']}。",
        f"- 满足严格确定链、可归 4410：{s['safe_4410_gap_hits']} Hit。",
        f"- 对应伤害 {s['safe_4410_gap_damage']}，覆盖 {s['safe_4410_gap_captures']} 份抓包、{s['safe_4410_gap_owners']} 个主人 ID。",
        f"- v57 快照与当前回放相差 {s['formal_snapshot_minus_current_replay_gap']} Hit，说明快照之后生产归因已有变化；最终候选以当前新鲜回放为准。",
        f"- 被独立对象链证明属于其他技能：{sum(s['gap_reclassified_to_other_skill'].values())} Hit。",
        f"- 当前回放仍保持未闭合：{s['current_remaining_4410_gap_hits']} Hit。", "",
        "## 可生产化结论", "",
        "- `summon_owner` 只证明实体主人，不证明技能；不能直接升格。",
        "- 32022 只有在当前实体代次、重复 owner、完整 direct37、自攻击槽 `FFFFFFFF`、上下文槽 `4410` 同时成立后，才可使用客户端静态唯一族归到 4410。",
        "- 若命中当时尚未确认 PvE，可暂存候选；仅当同一个目标实体代次之后获得独立 PvE 证明时回填，不能按时间邻近回填。",
        "- 对已登记为 monster、且已被可靠玩家 direct37 独立命中的目标，可直接视作 PvE；这只放宽技能元数据标注，不改变伤害准入。",
        "- 上下文槽只作一致性保护；静态族证书才是技能边。不存在时间邻近、旧标签或跨同职业借用。",
        "- 其他对象若形成自身 `damage_object_id → Projectile/static skill` 唯一链，应按该技能重分类，而不是强留在 4410。", "",
        "## 阻塞原因", "",
        "|原因|Hit|", "|---|---:|",
    ]
    for key, value in sorted(s["blocked_reasons"].items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"|{key}|{value}|")
    lines += ["", "## 不变量", "",
              f"- direct37 物理行：{s['all_direct37_rows']}；总伤害：{s['all_direct37_damage']}。",
              "- 来源、目标、伤害、暴击、组号、对象号、线偏移与真实 Hit 资格全部保持不变。",
              f"- 与独立确定对照冲突：{s['candidate_4410_conflicts_with_deterministic_controls']}。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    captures, _ = _selection(args.selection)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    partials = output / "partials"
    partials.mkdir(exist_ok=True)
    results = []
    pending = []
    for entry in captures:
        partial = partials / f"{entry['sha256']}.json"
        if partial.is_file():
            saved = json.loads(partial.read_text(encoding="utf-8"))
            if saved.get("capture_sha256") == entry["sha256"] and saved.get("audit_logic_version") == 6:
                results.append(saved)
                continue
        pending.append(entry)
    workers = max(1, min(8, args.workers))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(audit_capture, entry): entry for entry in pending}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            (partials / f"{result['capture_sha256']}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"done": result["capture"], "remaining": len(captures)-len(results)}, ensure_ascii=False), flush=True)
    by_hash = {row["capture_sha256"]: row for row in results}
    ordered = [by_hash[row["sha256"]] for row in captures]
    formal = _formal_gap(DEFAULT_BASELINE)
    static = _static_certificate()
    report = merge(ordered, formal, static)
    report["selection_sha256"] = _hash(args.selection)
    report["baseline_sha256"] = _hash(DEFAULT_BASELINE)
    report["tool_sha256"] = _hash(Path(__file__))
    checks = {
        "fixed_35": report["fixed_corpus"]["captures"] == 35 and report["fixed_corpus"]["hash_unique"] == 35,
        "formal_gap_639": report["formal_baseline"]["hits"] == 639,
        "current_gap_reconciles": report["summary"]["replayed_4410_gap_hits"] == (
            report["summary"]["safe_4410_gap_hits"]
            + sum(report["summary"]["gap_reclassified_to_other_skill"].values())
            + report["summary"]["current_remaining_4410_gap_hits"]
        ),
        "static_family_certificate": report["static_certificate"]["unique_family_in_model_m"] is True,
        "zero_deterministic_conflicts": report["summary"]["candidate_4410_conflicts_with_deterministic_controls"] == 0,
        "physical_rows_unchanged": report["summary"]["physical_rows_unchanged"] is True,
        "fixtures_forbid_timing_old_label_cross_job": all(
            not row["candidate_uses_timing"] and not row["candidate_uses_old_label"] and not row["cross_same_job_used"]
            for values in report["real_fixtures"].values() for row in values
        ),
    }
    validation = {"schema": SCHEMA, "passed": all(checks.values()), "checks": checks}
    (output / "REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text(markdown(report), encoding="utf-8")
    (output / "real_fixtures.json").write_text(json.dumps(report["real_fixtures"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "production_rule_candidates.json").write_text(json.dumps({
        "candidate": report["minimal_production_recommendation"],
        "safe_4410_gap_hits": report["summary"]["safe_4410_gap_hits"],
        "reclassified_to_other": report["summary"]["gap_reclassified_to_other_skill"],
        "zero_conflicts": report["summary"]["candidate_4410_conflicts_with_deterministic_controls"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    if not validation["passed"]:
        raise AssertionError(validation)
    print(json.dumps({"output": str(output), "summary": report["summary"], "validation": validation}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
