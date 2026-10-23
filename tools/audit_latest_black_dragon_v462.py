from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from dragon_nest_builder.live_dps.aggregator import CombatAggregator  # noqa: E402
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.damage_ledger import (  # noqa: E402
    SkillAttributionLedger,
)
from dragon_nest_builder.live_dps.models import (  # noqa: E402
    CombatEvent,
    PartyMemberEvent,
    PlayerInspectEvent,
    SkillCastEvent,
)
from dragon_nest_builder.live_dps.player_identity import (  # noqa: E402
    resolve_player_identities,
)
from dragon_nest_builder.live_dps.profile import ClientProfile  # noqa: E402


CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture\live_dps_diagnostics"
)
DEFAULT_CAPTURE_NAMES = (
    "0918经典冰灵.dpscap",
    "0916黑硬噬魂.dpscap",
    "0916黑硬剑皇.dpscap",
    "0916黑硬冰.dpscap",
    "0911黑硬噬魂 (2).dpscap",
    "0911黑硬噬魂.dpscap",
    "0905黑硬冰灵.dpscap",
    "0905黑龙经典冰灵.dpscap",
    "0831黑硬曜.dpscap",
    "20260831YEP黑典狙翎.dpscap",
    "20260830YEP黑典游侠.dpscap",
    "2026-8-27黑硬4剑.dpscap",
    "黑龙经典火舞.dpscap",
    "0825黑硬女王.dpscap",
    "0825黑硬风行.dpscap",
)
UNRESOLVED_NAMES = frozenset({"", "待识别伤害", "未学习伤害", "额外伤害", "unknown"})
SUMMON_METHODS = frozenset(
    {
        "summon_owner",
        "supplemental_summon_owner",
        "retroactive_permanent_summon_owner",
        "skill_object_owner",
    }
)
GENERIC_NAME = re.compile(r"^(?:角色\s*)?0x[0-9a-f]+$|^(?:未知角色|未知职业)$", re.I)


_STORE: DataStore | None = None
_KNOWN_PLAYERS: dict[str, dict[str, str]] = {}


def _worker_init() -> None:
    global _STORE, _KNOWN_PLAYERS
    _STORE = DataStore.load()
    try:
        _KNOWN_PLAYERS = ClientProfile.load(
            DATA_DIR / "live_dps_profiles" / "v576.json"
        ).known_players
    except (OSError, ValueError, KeyError):
        _KNOWN_PLAYERS = {}


def _is_unresolved(event: CombatEvent) -> bool:
    return (
        str(event.skill_id or "").strip() == "-1"
        or str(event.skill_name or "").strip().casefold() in UNRESOLVED_NAMES
    )


def _usable_name(value: object) -> str:
    text = str(value or "").strip()
    return "" if not text or GENERIC_NAME.fullmatch(text) else text


def _actor_id(value: object) -> int:
    text = str(value or "").strip()
    try:
        return int(text, 16) if text.casefold().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return 0


def _valid_profile_id(value: object) -> bool:
    """Treat the all-zero wire placeholder as missing, never as a stable ID."""

    return _actor_id(value) > 0


def _percent(value: int | float, total: int | float) -> float:
    return round(float(value) / max(1.0, float(total)) * 100.0, 4)


def _black_dragon(nest: object) -> bool:
    text = str(nest or "").strip()
    return "黑龙" in text and ("经典" in text or "硬核" in text)


def _analyze(path_text: str) -> dict[str, Any]:
    started = time.perf_counter()
    path = Path(path_text)
    store = _STORE or DataStore.load()
    source = V576DiagnosticReplaySource(path, DATA_DIR, _KNOWN_PLAYERS)
    try:
        decoded = source.poll()
        decoder = source._decoder
        nest = str(getattr(decoder, "detected_nest_name", "") or "").strip()
        nest_confidence = str(
            getattr(decoder, "detected_nest_confidence", "") or ""
        ).strip()
    finally:
        source.close()

    aggregator = CombatAggregator()
    for event in decoded:
        aggregator.ingest(event)
    snapshot = aggregator.snapshot(include_events=True)
    visible_players = tuple(snapshot.players)
    visible_player_ids = {str(row.source_id).strip().upper() for row in visible_players}
    casts = tuple(
        row
        for row in snapshot.skill_cast_events
        if str(row.source_id or "").strip().upper() in visible_player_ids
    )
    outgoing = tuple(
        row
        for row in snapshot.combat_events
        if isinstance(row, CombatEvent)
        and not row.healing
        and int(row.amount) > 0
        and str(row.source_id or "").strip().upper() in visible_player_ids
    )
    real_hits = tuple(row for row in outgoing if row.has_real_hit_evidence is True)
    unresolved = tuple(row for row in outgoing if _is_unresolved(row))
    unresolved_real = tuple(row for row in real_hits if _is_unresolved(row))

    jobs_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    names_by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for row in (*outgoing, *casts):
        source_id = str(row.source_id or "").strip().upper()
        job = str(row.job or "").strip()
        name = _usable_name(row.source_name)
        if source_id and job and job != "未知职业":
            jobs_by_source[source_id][job] += 1
        if source_id and name:
            names_by_source[source_id][name] += 1

    party_rows = tuple(snapshot.party_members)
    profile_rows = tuple(snapshot.player_profiles)
    player_rows = [
        {"source_id": row.source_id, "job": row.job, "name": row.name}
        for row in visible_players
    ]
    identities = resolve_player_identities(player_rows, party_rows, profile_rows)
    identity_by_source = {row.source_id.upper(): row for row in identities}

    combat_sources = sorted(visible_player_ids)
    visible_player_by_source = {
        str(row.source_id).strip().upper(): row for row in visible_players
    }
    player_details = []
    for source_id in combat_sources:
        identity = identity_by_source.get(source_id)
        player = visible_player_by_source[source_id]
        jobs = jobs_by_source[source_id]
        names = names_by_source[source_id]
        resolved_job = str(player.job or "").strip()
        packet_name = (
            str(identity.character_name or "").strip()
            if identity is not None
            else ""
        )
        resolved_name = (
            packet_name
            if packet_name
            else _usable_name(player.name)
        )
        player_details.append(
            {
                "source_id": source_id,
                "name": resolved_name,
                "job": resolved_job,
                "job_candidates": dict(jobs.most_common()),
                "name_candidates": dict(names.most_common()),
                "job_recognized": bool(resolved_job and resolved_job != "未知职业"),
                "name_recognized": bool(_usable_name(packet_name)),
                "display_name_recognized": bool(_usable_name(resolved_name)),
                "identity_status": identity.status if identity is not None else "missing",
                "identity_confidence": identity.confidence if identity is not None else 0.0,
                "profile_id": identity.profile_id if identity is not None else "",
                "profile_id_valid": _valid_profile_id(
                    identity.profile_id if identity is not None else ""
                ),
                "upload_allowed": identity.upload_allowed if identity is not None else False,
                "identity_evidence": list(identity.evidence) if identity is not None else [],
            }
        )

    summon_rows = tuple(
        row
        for row in outgoing
        if int(row.wire_entity_id or 0) > 0
        or str(row.attribution_method or "").strip().casefold() in SUMMON_METHODS
    )
    owners_by_entity_generation: dict[tuple[int, str], set[str]] = defaultdict(set)
    for row in summon_rows:
        entity = int(row.wire_entity_id or 0)
        evidence_id = str(row.entity_owner_evidence_id or "").strip()
        if entity > 0 and evidence_id:
            owners_by_entity_generation[(entity, evidence_id)].add(
                str(row.source_id or "").strip().upper()
            )
    conflicting_entity_generations = {
        key: sorted(owners)
        for key, owners in owners_by_entity_generation.items()
        if len(owners) > 1
    }
    summon_failures: Counter[str] = Counter()
    summon_family_rows: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    verified_summon_hits = 0
    verified_summon_damage = 0
    for row in summon_rows:
        source_id = str(row.source_id or "").strip().upper()
        method = str(row.attribution_method or "").strip().casefold()
        entity = int(row.wire_entity_id or 0)
        evidence_id = str(row.entity_owner_evidence_id or "").strip()
        entity_id_is_owner = entity > 0 and _actor_id(source_id) == entity
        owner_known = source_id in visible_player_ids
        job_known = bool(str(row.job or "").strip() not in {"", "未知职业"})
        skill_known = not _is_unresolved(row)
        evidence_known = bool(
            str(row.entity_owner_evidence_id or "").strip()
            or method in SUMMON_METHODS
            or is_deterministic_attribution(method)
        )
        owner_conflict = bool(
            entity > 0
            and evidence_id
            and (entity, evidence_id) in conflicting_entity_generations
        )
        checks = {
            "owner_not_recognized_as_player": not owner_known,
            "owner_job_unknown": not job_known,
            "skill_unresolved": not skill_known,
            "owner_evidence_missing": not evidence_known,
            "entity_credited_to_itself": entity_id_is_owner,
            "entity_has_multiple_owners": owner_conflict,
        }
        for key, failed in checks.items():
            if failed:
                summon_failures[key] += 1
        verified = not any(checks.values())
        if verified:
            verified_summon_hits += 1
            verified_summon_damage += int(row.amount)
        family_key = (
            str(row.job or "").strip(),
            str(row.skill_id or "").strip(),
            str(row.skill_name or "").strip(),
            int(row.entity_template_id or 0),
        )
        family = summon_family_rows.setdefault(
            family_key,
            {
                "job": family_key[0],
                "skill_id": family_key[1],
                "skill_name": family_key[2],
                "entity_template_id": family_key[3],
                "hits": 0,
                "real_hits": 0,
                "damage": 0,
                "verified_hits": 0,
                "owners": set(),
                "entities": set(),
                "methods": Counter(),
            },
        )
        family["hits"] += 1
        family["real_hits"] += row.has_real_hit_evidence is True
        family["damage"] += int(row.amount)
        family["verified_hits"] += verified
        family["owners"].add(source_id)
        if entity > 0:
            family["entities"].add(f"0x{entity:08X}")
        family["methods"][method or "unresolved"] += 1

    summon_families = []
    for family in summon_family_rows.values():
        summon_families.append(
            {
                **{key: value for key, value in family.items() if key not in {"owners", "entities", "methods"}},
                "owners": sorted(family["owners"]),
                "entities": sorted(family["entities"]),
                "methods": dict(family["methods"].most_common()),
            }
        )
    summon_families.sort(key=lambda row: (-int(row["damage"]), str(row["skill_name"])))

    total_damage = sum(int(row.amount) for row in outgoing)
    unresolved_damage = sum(int(row.amount) for row in unresolved)
    total_summon_damage = sum(int(row.amount) for row in summon_rows)
    return {
        "capture": path.name,
        "capture_path": str(path),
        "size_bytes": path.stat().st_size,
        "nest": nest,
        "nest_confidence": nest_confidence,
        "is_black_dragon_classic_or_hard": _black_dragon(nest),
        "decoded_events": len(decoded),
        "casts": len(casts),
        "damage_records": len(outgoing),
        "total_damage": total_damage,
        "real_hits": len(real_hits),
        "unresolved_records": len(unresolved),
        "unresolved_real_hits": len(unresolved_real),
        "unresolved_damage": unresolved_damage,
        "unresolved_record_rate_percent": _percent(len(unresolved), len(outgoing)),
        "unresolved_real_hit_rate_percent": _percent(len(unresolved_real), len(real_hits)),
        "unresolved_damage_rate_percent": _percent(unresolved_damage, total_damage),
        "combat_player_count": len(combat_sources),
        "recognized_job_count": sum(row["job_recognized"] for row in player_details),
        "recognized_name_count": sum(row["name_recognized"] for row in player_details),
        "stable_profile_id_count": sum(row["profile_id_valid"] for row in player_details),
        "upload_identity_count": sum(row["upload_allowed"] for row in player_details),
        "job_conflict_sources": [
            row["source_id"] for row in player_details if len(row["job_candidates"]) > 1
        ],
        "players": player_details,
        "summon_hits": len(summon_rows),
        "summon_real_hits": sum(row.has_real_hit_evidence is True for row in summon_rows),
        "summon_damage": total_summon_damage,
        "summon_verified_hits": verified_summon_hits,
        "summon_verified_damage": verified_summon_damage,
        "summon_verified_hit_rate_percent": _percent(verified_summon_hits, len(summon_rows)),
        "summon_verified_damage_rate_percent": _percent(verified_summon_damage, total_summon_damage),
        "summon_failure_reasons": dict(summon_failures.most_common()),
        "summon_owner_conflicts": {
            f"0x{entity:08X}/{evidence_id}": owners
            for (entity, evidence_id), owners in conflicting_entity_generations.items()
        },
        "summon_families": summon_families,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _summary(rows: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    valid = [row for row in rows if row["is_black_dragon_classic_or_hard"]]
    total_damage = sum(int(row["total_damage"]) for row in valid)
    unresolved_damage = sum(int(row["unresolved_damage"]) for row in valid)
    total_real_hits = sum(int(row["real_hits"]) for row in valid)
    unresolved_real_hits = sum(int(row["unresolved_real_hits"]) for row in valid)
    summon_hits = sum(int(row["summon_hits"]) for row in valid)
    summon_damage = sum(int(row["summon_damage"]) for row in valid)
    verified_summon_hits = sum(int(row["summon_verified_hits"]) for row in valid)
    verified_summon_damage = sum(int(row["summon_verified_damage"]) for row in valid)
    hit_rates = [float(row["unresolved_real_hit_rate_percent"]) for row in valid]
    damage_rates = [float(row["unresolved_damage_rate_percent"]) for row in valid]
    return {
        "schema": "v462-latest-black-dragon-attribution-audit-v1",
        "captures_requested": len(rows) + len(errors),
        "captures_completed": len(rows),
        "captures_verified_black_dragon": len(valid),
        "errors": errors,
        "total_damage": total_damage,
        "total_unresolved_damage": unresolved_damage,
        "weighted_unresolved_damage_rate_percent": _percent(unresolved_damage, total_damage),
        "total_real_hits": total_real_hits,
        "total_unresolved_real_hits": unresolved_real_hits,
        "weighted_unresolved_real_hit_rate_percent": _percent(unresolved_real_hits, total_real_hits),
        "median_capture_unresolved_damage_rate_percent": round(statistics.median(damage_rates), 4) if damage_rates else 0.0,
        "median_capture_unresolved_real_hit_rate_percent": round(statistics.median(hit_rates), 4) if hit_rates else 0.0,
        "capture_unresolved_damage_rate_range_percent": [min(damage_rates), max(damage_rates)] if damage_rates else [0.0, 0.0],
        "capture_unresolved_real_hit_rate_range_percent": [min(hit_rates), max(hit_rates)] if hit_rates else [0.0, 0.0],
        "combat_player_rows": sum(int(row["combat_player_count"]) for row in valid),
        "recognized_job_rows": sum(int(row["recognized_job_count"]) for row in valid),
        "recognized_name_rows": sum(int(row["recognized_name_count"]) for row in valid),
        "stable_profile_id_rows": sum(int(row["stable_profile_id_count"]) for row in valid),
        "upload_identity_rows": sum(int(row["upload_identity_count"]) for row in valid),
        "job_conflict_source_rows": sum(len(row["job_conflict_sources"]) for row in valid),
        "summon_hits": summon_hits,
        "summon_damage": summon_damage,
        "summon_verified_hits": verified_summon_hits,
        "summon_verified_damage": verified_summon_damage,
        "summon_verified_hit_rate_percent": _percent(verified_summon_hits, summon_hits),
        "summon_verified_damage_rate_percent": _percent(verified_summon_damage, summon_damage),
        "summon_owner_conflict_entities": sum(len(row["summon_owner_conflicts"]) for row in valid),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit current V4.62 attribution on 15 recent Black Dragon captures.")
    parser.add_argument("--capture-root", type=Path, default=CAPTURE_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    paths = [(args.capture_root / name).resolve() for name in DEFAULT_CAPTURE_NAMES]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit("missing captures:\n" + "\n".join(missing))
    args.output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers), initializer=_worker_init) as pool:
        pending = {pool.submit(_analyze, str(path)): path for path in paths}
        for index, future in enumerate(as_completed(pending), 1):
            path = pending[future]
            try:
                row = future.result()
                rows.append(row)
                print(
                    f"[{index}/{len(paths)}] {path.name} "
                    f"unknown-hit={row['unresolved_real_hits']}/{row['real_hits']} "
                    f"unknown-damage={row['unresolved_damage_rate_percent']}% "
                    f"summon={row['summon_verified_hits']}/{row['summon_hits']} "
                    f"players={row['recognized_job_count']}/{row['combat_player_count']} "
                    f"{row['elapsed_seconds']}s",
                    flush=True,
                )
            except Exception as exc:
                errors.append({"capture": path.name, "error": repr(exc)})
                print(f"[{index}/{len(paths)}] ERROR {path.name}: {exc!r}", flush=True)
    order = {name: index for index, name in enumerate(DEFAULT_CAPTURE_NAMES)}
    rows.sort(key=lambda row: order.get(str(row["capture"]), 999))
    report = _summary(rows, errors)
    json_path = args.output / "v462_latest_black_dragon_audit.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_rows = [
        {key: value for key, value in row.items() if not isinstance(value, (list, dict))}
        for row in rows
    ]
    csv_path = args.output / "v462_latest_black_dragon_audit.csv"
    if csv_rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
            writer.writeheader()
            writer.writerows(csv_rows)
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2), flush=True)
    print(f"json={json_path}\ncsv={csv_path}", flush=True)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
