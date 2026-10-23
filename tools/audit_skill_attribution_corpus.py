from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR, DataStore, norm  # noqa: E402
from dragon_nest_builder.hit_analyzer.analyzer import (  # noqa: E402
    SkillHitTemplateLibrary,
    skill_family,
)
from dragon_nest_builder.live_dps.damage_attribution import (  # noqa: E402
    AttributionLayer,
    attribution_layer,
    is_deterministic_attribution,
)
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _DAMAGE_OBJECT_SPECS,
    direct37_damage_object_id,
)
from dragon_nest_builder.live_dps.models import CombatEvent, SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


AUDIT_SCHEMA_VERSION = 2
_STRONG_ACTION_KINDS = frozenset(
    {"skill_action", "status_skill_action", "transformed_basic_attack"}
)
_DIRECT_ACTION_KINDS = frozenset({"skill_cast", "compact_skill_input"})
_RELIABLE_ACTION_KINDS = _STRONG_ACTION_KINDS | _DIRECT_ACTION_KINDS
_UNRESOLVED_SKILL_NAMES = frozenset(
    {"", "待识别伤害", "未学习伤害", "额外伤害", "unknown"}
)
_CANDIDATE_WINDOW_SECONDS = 2.5
_DIRECT_OBJECT_MAX_AGE_SECONDS = 120.0
_SIGNATURE_FILES = (
    "tools/audit_skill_attribution_corpus.py",
    "dragon_nest_builder/live_dps/damage_attribution.py",
    "dragon_nest_builder/live_dps/damage_object_origins.py",
    "dragon_nest_builder/live_dps/capture_sources.py",
    "dragon_nest_builder/live_dps/protocol_v576.py",
    "dragon_nest_builder/live_dps/v576/core_decoder.py",
    "dragon_nest_builder/live_dps/v576/combat_decoder.py",
    "dragon_nest_builder/live_dps/v576/status_decoder.py",
    "dragon_nest_builder/live_dps/replay_normalization.py",
    "dragon_nest_builder/data/live_dps_profiles/v576.json",
)
_DEFAULT_CLIENT_RESOURCE_ROOTS = (
    Path(r"E:\ext0811.rar\Resource00\Resource00\resource"),
    Path(r"G:\Resource00\resource"),
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_gzip_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(path)


def _read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"invalid shard: {path}")
    return value


def _analysis_signature() -> str:
    digest = hashlib.sha256(b"skill-attribution-corpus-v1\0")
    for relative in _SIGNATURE_FILES:
        path = ROOT / relative
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _actor_key(actor_id: int) -> str:
    return f"0x{int(actor_id) & 0xFFFFFFFF:08X}"


def _percent(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator) * 100.0, 4)


def _quantile(values: Iterable[float], fraction: float) -> float | None:
    rows = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not rows:
        return None
    if len(rows) == 1:
        return round(rows[0], 6)
    position = max(0.0, min(1.0, fraction)) * (len(rows) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return round(rows[lower], 6)
    weight = position - lower
    return round(rows[lower] * (1.0 - weight) + rows[upper] * weight, 6)


def _compact_family(name: object, skill_id: object = "") -> str:
    return skill_family(name, skill_id)


def _skill_key(job: object, skill_id: object, skill_name: object) -> str:
    return "|".join(
        (
            norm(job) or "未知职业",
            str(skill_id or "").strip() or "NO_ID",
            norm(skill_name) or "UNKNOWN",
        )
    )


def _object_candidate_key(skill_id: object, skill_family_name: object) -> str:
    """Group object candidates by canonical skill, not advanced job label.

    Base/shared skills legitimately appear under several second jobs.  Using
    ``job + skill`` here split one deterministic Projectile candidate into
    several artificial votes and understated its purity.
    """

    normalized_id = str(skill_id or "").strip()
    if normalized_id:
        return f"skill:{normalized_id}"
    return f"family:{norm(skill_family_name) or 'UNKNOWN'}"


def _empty_skill_row(event: CombatEvent | SkillCastEvent) -> dict[str, Any]:
    return {
        "job": norm(event.job) or "未知职业",
        "skill_id": str(event.skill_id or "").strip(),
        "skill_name": norm(event.skill_name) or "UNKNOWN",
        "skill_family": _compact_family(event.skill_name, event.skill_id),
        "hits": 0,
        "real_hits": 0,
        "damage": 0,
        "deterministic_hits": 0,
        "deterministic_damage": 0,
        "methods": {},
        "method_damage": {},
        "layers": {},
        "layer_damage": {},
        "casts": 0,
        "reliable_casts": 0,
        "cast_kinds": {},
        "actors": [],
    }


def _candidate_for_registration(
    registration: dict[str, Any],
    casts_by_actor: dict[int, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    rows = casts_by_actor.get(int(registration["source_actor_id"]), ())
    if not rows:
        return None
    timestamp = float(registration["timestamp"])
    timestamps = [float(row["timestamp"]) for row in rows]
    right = bisect.bisect_right(timestamps, timestamp + 0.15)
    candidates = [
        row
        for row in rows[max(0, right - 12) : right]
        if -0.15 <= timestamp - float(row["timestamp"]) <= _CANDIDATE_WINDOW_SECONDS
    ]
    if not candidates:
        return None
    strong = [row for row in candidates if row["packet_kind"] in _STRONG_ACTION_KINDS]
    if strong:
        candidates = strong
    else:
        direct = [row for row in candidates if row["packet_kind"] in _DIRECT_ACTION_KINDS]
        if direct:
            candidates = direct
    candidates.sort(
        key=lambda row: (
            abs(timestamp - float(row["timestamp"])),
            -float(row["timestamp"]),
        )
    )
    best_distance = abs(timestamp - float(candidates[0]["timestamp"]))
    tied = [
        row
        for row in candidates
        if abs(abs(timestamp - float(row["timestamp"])) - best_distance) <= 0.001
    ]
    tied_families = {
        (norm(row["job"]), norm(row["skill_family"])) for row in tied
    }
    if len(tied_families) > 1:
        return {
            "ambiguous": True,
            "candidate_count": len(tied),
            "delta_seconds": round(
                timestamp - float(candidates[0]["timestamp"]), 6
            ),
        }
    selected = candidates[0]
    return {
        "ambiguous": False,
        "job": norm(selected["job"]),
        "skill_id": str(selected["skill_id"]),
        "skill_name": norm(selected["skill_name"]),
        "skill_family": norm(selected["skill_family"]),
        "packet_kind": str(selected["packet_kind"]),
        "delta_seconds": round(timestamp - float(selected["timestamp"]), 6),
    }


def _analyze_one(path_text: str, relative_path: str, shard_text: str) -> dict[str, Any]:
    path = Path(path_text)
    shard_path = Path(shard_text)
    started = time.perf_counter()
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder  # Audit-only access; no production state is mutated.
    object_ledger = decoder.damage_object_origins

    registrations: list[dict[str, Any]] = []
    registration_keys: set[tuple[float, int, int, int]] = set()
    active_registration: dict[tuple[int, int], int] = {}
    direct_seen: set[tuple[float, int, int, int, int]] = set()
    raw_casts: list[dict[str, Any]] = []
    raw_cast_seen: set[tuple[int, str, str, int, str]] = set()

    original_observe_registration = object_ledger.observe_registration
    original_attribute_direct37 = object_ledger.attribute_direct37
    original_feed_events = decoder.feed_events

    def observe_registration(**kwargs):
        source_actor_id = int(kwargs.get("source_actor_id", 0) or 0)
        damage_object_id = int(kwargs.get("damage_object_id", 0) or 0)
        projectile_template_id = int(
            kwargs.get("projectile_template_id", 0) or 0
        )
        timestamp = float(kwargs.get("timestamp", 0.0) or 0.0)
        dedupe_key = (
            round(timestamp, 6),
            source_actor_id,
            damage_object_id,
            projectile_template_id,
        )
        if (
            source_actor_id > 0
            and damage_object_id > 0
            and projectile_template_id > 0
            and dedupe_key not in registration_keys
        ):
            registration_keys.add(dedupe_key)
            row = {
                "timestamp": timestamp,
                "source_actor_id": source_actor_id,
                "damage_object_id": damage_object_id,
                "projectile_template_id": projectile_template_id,
                "effect_resource_id": int(
                    kwargs.get("effect_resource_id", 0) or 0
                ),
                "child_sequence": int(kwargs.get("child_sequence", 0) or 0),
                "direct37_hits": 0,
                "direct37_damage": 0,
                "last_direct37_timestamp": None,
            }
            registrations.append(row)
            active_registration[(source_actor_id, damage_object_id)] = (
                len(registrations) - 1
            )
        return original_observe_registration(**kwargs)

    def attribute_direct37(**kwargs):
        source_actor_id = int(kwargs.get("source_actor_id", 0) or 0)
        timestamp = float(kwargs.get("timestamp", 0.0) or 0.0)
        damage = int(kwargs.get("damage", 0) or 0)
        record_offset = int(kwargs.get("record_offset", 0) or 0)
        damage_object_id = direct37_damage_object_id(
            kwargs.get("payload", b""),
            kwargs.get("direction"),
            source_actor_id=source_actor_id,
            damage=damage,
            record_offset=record_offset,
        )
        if damage_object_id is not None:
            dedupe_key = (
                round(timestamp, 6),
                source_actor_id,
                int(damage_object_id),
                damage,
                record_offset,
            )
            if dedupe_key not in direct_seen:
                direct_seen.add(dedupe_key)
                registration_index = active_registration.get(
                    (source_actor_id, int(damage_object_id))
                )
                if registration_index is not None:
                    registration = registrations[registration_index]
                    age = timestamp - float(registration["timestamp"])
                    if 0.0 <= age <= _DIRECT_OBJECT_MAX_AGE_SECONDS:
                        registration["direct37_hits"] += 1
                        registration["direct37_damage"] += max(0, damage)
                        registration["last_direct37_timestamp"] = timestamp
        return original_attribute_direct37(**kwargs)

    def feed_events(packet):
        events = original_feed_events(packet)
        packet_timestamp = float(packet.timestamp_seconds)
        for event in events:
            if not isinstance(event, SkillCastEvent):
                continue
            try:
                actor_id = int(str(event.source_id), 16)
            except (TypeError, ValueError):
                continue
            event_timestamp = packet_timestamp
            key = (
                actor_id,
                str(event.skill_id),
                norm(event.skill_name),
                int(event.timestamp_ms),
                str(event.packet_kind).casefold(),
            )
            if key in raw_cast_seen:
                continue
            raw_cast_seen.add(key)
            raw_casts.append(
                {
                    "timestamp": event_timestamp,
                    "timestamp_ms": int(event.timestamp_ms),
                    "source_actor_id": actor_id,
                    "job": norm(event.job) or "未知职业",
                    "skill_id": str(event.skill_id),
                    "skill_name": norm(event.skill_name) or "UNKNOWN",
                    "skill_family": _compact_family(
                        event.skill_name, event.skill_id
                    ),
                    "packet_kind": str(event.packet_kind).casefold(),
                }
            )
        return events

    object_ledger.observe_registration = observe_registration
    object_ledger.attribute_direct37 = attribute_direct37
    decoder.feed_events = feed_events
    try:
        events = source.poll()
    finally:
        source.close()

    casts_by_actor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_casts:
        if row["packet_kind"] in _RELIABLE_ACTION_KINDS:
            casts_by_actor[int(row["source_actor_id"])].append(row)
    for rows in casts_by_actor.values():
        rows.sort(key=lambda row: float(row["timestamp"]))

    template_rows: dict[int, dict[str, Any]] = {}
    for registration in registrations:
        template_id = int(registration["projectile_template_id"])
        row = template_rows.setdefault(
            template_id,
            {
                "projectile_template_id": template_id,
                "registrations": 0,
                "hit_registrations": 0,
                "direct37_hits": 0,
                "direct37_damage": 0,
                "actors": set(),
                "effect_resource_ids": Counter(),
                "child_sequences": Counter(),
                "candidate_votes": Counter(),
                "candidate_details": {},
                "candidate_lags": defaultdict(list),
                "candidate_actors": defaultdict(set),
                "candidate_jobs": defaultdict(Counter),
                "ambiguous_registrations": 0,
                "unmatched_registrations": 0,
            },
        )
        row["registrations"] += 1
        row["actors"].add(_actor_key(registration["source_actor_id"]))
        row["effect_resource_ids"][str(registration["effect_resource_id"])] += 1
        row["child_sequences"][str(registration["child_sequence"])] += 1
        direct_hits = int(registration["direct37_hits"])
        if direct_hits <= 0:
            continue
        row["hit_registrations"] += 1
        row["direct37_hits"] += direct_hits
        row["direct37_damage"] += int(registration["direct37_damage"])
        candidate = _candidate_for_registration(registration, casts_by_actor)
        if candidate is None:
            row["unmatched_registrations"] += 1
            continue
        if bool(candidate.get("ambiguous")):
            row["ambiguous_registrations"] += 1
            continue
        candidate_key = _object_candidate_key(
            candidate["skill_id"], candidate["skill_family"]
        )
        row["candidate_votes"][candidate_key] += 1
        row["candidate_details"][candidate_key] = {
            key: candidate[key]
            for key in (
                "skill_id",
                "skill_name",
                "skill_family",
                "packet_kind",
            )
        }
        row["candidate_jobs"][candidate_key][candidate["job"]] += 1
        row["candidate_lags"][candidate_key].append(
            float(candidate["delta_seconds"])
        )
        row["candidate_actors"][candidate_key].add(
            _actor_key(registration["source_actor_id"])
        )

    serial_templates: list[dict[str, Any]] = []
    for template_id, row in sorted(template_rows.items()):
        candidate_rows = []
        for candidate_key, votes in row["candidate_votes"].most_common():
            lags = row["candidate_lags"][candidate_key]
            candidate_rows.append(
                {
                    "key": candidate_key,
                    **row["candidate_details"][candidate_key],
                    "job": row["candidate_jobs"][candidate_key].most_common(1)[0][0],
                    "jobs": dict(row["candidate_jobs"][candidate_key].most_common()),
                    "votes": int(votes),
                    "lag_p10_seconds": _quantile(lags, 0.10),
                    "lag_median_seconds": _quantile(lags, 0.50),
                    "lag_p90_seconds": _quantile(lags, 0.90),
                    "actors": sorted(row["candidate_actors"][candidate_key]),
                }
            )
        serial_templates.append(
            {
                "projectile_template_id": template_id,
                "registrations": int(row["registrations"]),
                "hit_registrations": int(row["hit_registrations"]),
                "direct37_hits": int(row["direct37_hits"]),
                "direct37_damage": int(row["direct37_damage"]),
                "actors": sorted(row["actors"]),
                "effect_resource_ids": dict(row["effect_resource_ids"].most_common()),
                "child_sequences": dict(row["child_sequences"].most_common()),
                "ambiguous_registrations": int(row["ambiguous_registrations"]),
                "unmatched_registrations": int(row["unmatched_registrations"]),
                "candidates": candidate_rows,
            }
        )

    skills: dict[str, dict[str, Any]] = {}
    skill_actors: dict[str, set[str]] = defaultdict(set)
    method_counts: dict[str, Counter[str]] = defaultdict(Counter)
    method_damage: dict[str, Counter[str]] = defaultdict(Counter)
    layer_counts: dict[str, Counter[str]] = defaultdict(Counter)
    layer_damage: dict[str, Counter[str]] = defaultdict(Counter)
    cast_kind_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for event in events:
        if isinstance(event, CombatEvent):
            if event.healing or int(event.amount) <= 0:
                continue
            key = _skill_key(event.job, event.skill_id, event.skill_name)
            row = skills.setdefault(key, _empty_skill_row(event))
            method = str(event.attribution_method or "unresolved").strip().casefold()
            layer = attribution_layer(method).value
            row["hits"] += 1
            row["real_hits"] += event.has_real_hit_evidence is True
            row["damage"] += int(event.amount)
            if is_deterministic_attribution(method):
                row["deterministic_hits"] += 1
                row["deterministic_damage"] += int(event.amount)
            method_counts[key][method] += 1
            method_damage[key][method] += int(event.amount)
            layer_counts[key][layer] += 1
            layer_damage[key][layer] += int(event.amount)
            skill_actors[key].add(str(event.source_id))
        elif isinstance(event, SkillCastEvent):
            if norm(event.skill_name).casefold() in _UNRESOLVED_SKILL_NAMES:
                continue
            key = _skill_key(event.job, event.skill_id, event.skill_name)
            row = skills.setdefault(key, _empty_skill_row(event))
            kind = str(event.packet_kind or "unknown").strip().casefold()
            row["casts"] += 1
            row["reliable_casts"] += kind in _RELIABLE_ACTION_KINDS
            cast_kind_counts[key][kind] += 1
            skill_actors[key].add(str(event.source_id))

    for key, row in skills.items():
        row["methods"] = dict(method_counts[key].most_common())
        row["method_damage"] = dict(method_damage[key].most_common())
        row["layers"] = dict(layer_counts[key].most_common())
        row["layer_damage"] = dict(layer_damage[key].most_common())
        row["cast_kinds"] = dict(cast_kind_counts[key].most_common())
        row["actors"] = sorted(skill_actors[key])

    payload = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "source": relative_path,
        "size_bytes": path.stat().st_size,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "decoded_events": len(events),
        "skills": list(skills.values()),
        "object_templates": serial_templates,
    }
    _write_gzip_json(shard_path, payload)
    return {
        "source": relative_path,
        "size_bytes": payload["size_bytes"],
        "elapsed_seconds": payload["elapsed_seconds"],
        "decoded_events": payload["decoded_events"],
        "skills": len(skills),
        "object_templates": len(serial_templates),
    }


def _known_template_rows() -> dict[int, dict[str, Any]]:
    return {
        int(spec.projectile_template_id): {
            "key": spec.key,
            "canonical_skill_id": int(spec.canonical_skill_id),
            "skill_ids": sorted(int(value) for value in spec.skill_ids),
            "lifetime_seconds": float(spec.lifetime_seconds),
            "factual_active_seconds": spec.factual_active_seconds,
            "require_exact_object": bool(spec.require_exact_object),
            "require_action_evidence": bool(spec.require_action_evidence),
        }
        for spec in _DAMAGE_OBJECT_SPECS
    }


def _load_projectile_act_catalog() -> tuple[dict[int, dict[str, Any]], str]:
    """Load the stable Projectile -> ACT bridge from the unpacked client.

    Capture correlation identifies which player action is near an object
    registration; the client table independently identifies what that object
    actually is.  Keeping both columns in the audit prevents a nearby later
    action from being mistaken for the object owner.
    """

    from tools.build_dungeon_map_catalog import parse_dnt

    configured = os.getenv("DRAGON_NEST_RESOURCE_ROOT", "").strip()
    roots = ([Path(configured)] if configured else []) + list(
        _DEFAULT_CLIENT_RESOURCE_ROOTS
    )
    for root in roots:
        projectile_path = root / "ext" / "weapontable_projectile.dnt"
        file_path = root / "ext" / "filetable.dnt"
        if not projectile_path.is_file() or not file_path.is_file():
            continue
        file_names = {
            int(row.get("RowId", 0) or 0): str(row.get("_FileName", "") or "")
            for row in parse_dnt(file_path)
            if int(row.get("RowId", 0) or 0) > 0
        }
        result: dict[int, dict[str, Any]] = {}
        for row in parse_dnt(projectile_path):
            template_id = int(row.get("RowId", 0) or 0)
            act_id = int(row.get("_ActName", 0) or 0)
            if template_id <= 0:
                continue
            result[template_id] = {
                "act_name_id": act_id,
                "act_file": file_names.get(act_id, ""),
                "length": int(row.get("_Length", 0) or 0),
            }
        return result, str(root)
    return {}, ""


def _object_candidate_status(row: dict[str, Any], known: bool) -> tuple[str, str]:
    if known:
        return "已确认并启用", "客户端表+对象注册+direct37 精确对象ID闭环"
    hit_registrations = int(row["hit_registrations"])
    matched = sum(int(item["votes"]) for item in row["candidates"])
    files = len(row["files"])
    actors = len(row["actors"])
    if not hit_registrations:
        return "非伤害对象/暂无命中", "只有对象注册，语料内没有同对象 direct37"
    if not row["candidates"]:
        return "待破解", "对象ID已连到direct37，但附近没有可靠技能动作"
    top = row["candidates"][0]
    top_share = float(top["vote_share_percent"])
    matched_ratio = _percent(matched, hit_registrations)
    top_file_count = int(top["file_count"])
    top_actor_count = int(top["actor_count"])
    if (
        hit_registrations >= 8
        and int(row["direct37_hits"]) >= 20
        and files >= 3
        and actors >= 2
        and matched_ratio >= 80.0
        and top_share >= 95.0
        and top_file_count >= 3
        and top_actor_count >= 2
    ):
        return (
            "高置信候选，待客户端表复核",
            "跨文件/跨角色动作一致，且对象ID已连到direct37；尚未核对Projectile/ACT表",
        )
    if top_share >= 80.0 and matched_ratio >= 60.0:
        return "中置信候选，需隔离样本", "动作相关性较强，但文件、角色或纯度未达到晋级门槛"
    return "模板复用或动作歧义", "同一模板附近出现多个技能，不能按最近动作直接定名"


def _skill_status(row: dict[str, Any]) -> tuple[str, str]:
    hits = int(row["hits"])
    damage = int(row["damage"])
    casts = int(row["casts"])
    deterministic_hits = int(row["deterministic_hits"])
    deterministic_damage = int(row["deterministic_damage"])
    layers = {
        key for key, value in row["layers"].items() if int(value) > 0
    }
    deterministic_layers = layers & {
        AttributionLayer.OBJECT.value,
        AttributionLayer.ENTITY.value,
        AttributionLayer.STATUS.value,
        AttributionLayer.DIRECT.value,
    }
    hit_share = _percent(deterministic_hits, hits)
    damage_share = _percent(deterministic_damage, damage)
    if hits <= 0:
        if casts > 0:
            return "动作已识别，暂无伤害样本", "抓包中存在动作，但没有可归属伤害"
        return "暂无样本", "现有抓包没有该技能动作或伤害"
    if damage_share >= 99.5 and hit_share >= 99.5:
        if len(deterministic_layers) > 1:
            return "已确定（多证据链）", "+".join(sorted(deterministic_layers))
        if deterministic_layers:
            layer = next(iter(deterministic_layers))
            labels = {
                "object": "对象ID链",
                "entity": "召唤/实体归属链",
                "status": "状态/周期伤害链",
                "direct": "伤害包内技能ID链",
            }
            return f"已确定（{labels.get(layer, layer)}）", layer
    if damage_share >= 90.0:
        return "基本确定，少量回退", f"确定性伤害覆盖 {damage_share:.2f}%"
    if damage_share >= 50.0:
        return "部分确定", f"仍有 {100.0 - damage_share:.2f}% 伤害依赖推断/未解析"
    if deterministic_damage > 0:
        return "待继续破解", f"确定性伤害仅覆盖 {damage_share:.2f}%"
    if int(row["real_hits"]) <= 0:
        return "非真实Hit/辅助伤害", "不能用来证明一次技能施放"
    return "未形成确定性归因", "当前主要依赖时序、旧模板或仍未解析"


def _merge_reports(
    shard_paths: list[Path],
    output_dir: Path,
    *,
    signature: str,
    duplicates: list[dict[str, str]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    skills: dict[str, dict[str, Any]] = {}
    skill_files: dict[str, set[str]] = defaultdict(set)
    skill_actors: dict[str, set[str]] = defaultdict(set)
    skill_methods: dict[str, Counter[str]] = defaultdict(Counter)
    skill_method_damage: dict[str, Counter[str]] = defaultdict(Counter)
    skill_layers: dict[str, Counter[str]] = defaultdict(Counter)
    skill_layer_damage: dict[str, Counter[str]] = defaultdict(Counter)
    skill_cast_kinds: dict[str, Counter[str]] = defaultdict(Counter)
    templates: dict[int, dict[str, Any]] = {}
    template_candidate_files: dict[tuple[int, str], set[str]] = defaultdict(set)
    template_candidate_actors: dict[tuple[int, str], set[str]] = defaultdict(set)
    file_rows: list[dict[str, Any]] = []

    for shard in shard_paths:
        payload = _read_gzip_json(shard)
        source_name = str(payload.get("source", ""))
        file_rows.append(
            {
                key: payload.get(key)
                for key in (
                    "source",
                    "size_bytes",
                    "elapsed_seconds",
                    "decoded_events",
                )
            }
        )
        for source_row in payload.get("skills", ()):
            if not isinstance(source_row, dict):
                continue
            key = _skill_key(
                source_row.get("job"),
                source_row.get("skill_id"),
                source_row.get("skill_name"),
            )
            row = skills.setdefault(
                key,
                {
                    name: source_row.get(name)
                    for name in ("job", "skill_id", "skill_name", "skill_family")
                }
                | {
                    "hits": 0,
                    "real_hits": 0,
                    "damage": 0,
                    "deterministic_hits": 0,
                    "deterministic_damage": 0,
                    "casts": 0,
                    "reliable_casts": 0,
                },
            )
            for name in (
                "hits",
                "real_hits",
                "damage",
                "deterministic_hits",
                "deterministic_damage",
                "casts",
                "reliable_casts",
            ):
                row[name] += int(source_row.get(name, 0) or 0)
            if int(source_row.get("hits", 0) or 0) or int(
                source_row.get("casts", 0) or 0
            ):
                skill_files[key].add(source_name)
            skill_actors[key].update(str(value) for value in source_row.get("actors", ()))
            skill_methods[key].update(source_row.get("methods", {}))
            skill_method_damage[key].update(source_row.get("method_damage", {}))
            skill_layers[key].update(source_row.get("layers", {}))
            skill_layer_damage[key].update(source_row.get("layer_damage", {}))
            skill_cast_kinds[key].update(source_row.get("cast_kinds", {}))

        for source_row in payload.get("object_templates", ()):
            if not isinstance(source_row, dict):
                continue
            template_id = int(source_row.get("projectile_template_id", 0) or 0)
            if template_id <= 0:
                continue
            row = templates.setdefault(
                template_id,
                {
                    "projectile_template_id": template_id,
                    "registrations": 0,
                    "hit_registrations": 0,
                    "direct37_hits": 0,
                    "direct37_damage": 0,
                    "ambiguous_registrations": 0,
                    "unmatched_registrations": 0,
                    "files": set(),
                    "actors": set(),
                    "effect_resource_ids": Counter(),
                    "child_sequences": Counter(),
                    "candidate_votes": Counter(),
                    "candidate_details": {},
                    "candidate_lags": defaultdict(list),
                    "candidate_jobs": defaultdict(Counter),
                },
            )
            for name in (
                "registrations",
                "hit_registrations",
                "direct37_hits",
                "direct37_damage",
                "ambiguous_registrations",
                "unmatched_registrations",
            ):
                row[name] += int(source_row.get(name, 0) or 0)
            if int(source_row.get("registrations", 0) or 0):
                row["files"].add(source_name)
            row["actors"].update(str(value) for value in source_row.get("actors", ()))
            row["effect_resource_ids"].update(source_row.get("effect_resource_ids", {}))
            row["child_sequences"].update(source_row.get("child_sequences", {}))
            for candidate in source_row.get("candidates", ()):
                if not isinstance(candidate, dict):
                    continue
                candidate_key = str(candidate.get("key", ""))
                votes = int(candidate.get("votes", 0) or 0)
                if not candidate_key or votes <= 0:
                    continue
                row["candidate_votes"][candidate_key] += votes
                row["candidate_details"][candidate_key] = {
                    key: candidate.get(key, "")
                    for key in (
                        "skill_id",
                        "skill_name",
                        "skill_family",
                        "packet_kind",
                    )
                }
                jobs = candidate.get("jobs")
                if isinstance(jobs, dict):
                    row["candidate_jobs"][candidate_key].update(jobs)
                elif candidate.get("job"):
                    row["candidate_jobs"][candidate_key][
                        str(candidate.get("job"))
                    ] += votes
                median = candidate.get("lag_median_seconds")
                if median is not None:
                    row["candidate_lags"][candidate_key].extend(
                        [float(median)] * votes
                    )
                template_candidate_files[(template_id, candidate_key)].add(source_name)
                template_candidate_actors[(template_id, candidate_key)].update(
                    str(value) for value in candidate.get("actors", ())
                )

    known_templates = _known_template_rows()
    projectile_act_catalog, client_resource_root = _load_projectile_act_catalog()
    object_rows: list[dict[str, Any]] = []
    for template_id, row in sorted(templates.items()):
        total_votes = sum(row["candidate_votes"].values())
        candidates = []
        for candidate_key, votes in row["candidate_votes"].most_common():
            details = row["candidate_details"][candidate_key]
            lags = row["candidate_lags"][candidate_key]
            job_counts = row["candidate_jobs"][candidate_key]
            candidates.append(
                {
                    "key": candidate_key,
                    **details,
                    "job": job_counts.most_common(1)[0][0] if job_counts else "",
                    "jobs": dict(job_counts.most_common()),
                    "votes": int(votes),
                    "vote_share_percent": _percent(votes, total_votes),
                    "file_count": len(
                        template_candidate_files[(template_id, candidate_key)]
                    ),
                    "actor_count": len(
                        template_candidate_actors[(template_id, candidate_key)]
                    ),
                    "lag_p10_seconds": _quantile(lags, 0.10),
                    "lag_median_seconds": _quantile(lags, 0.50),
                    "lag_p90_seconds": _quantile(lags, 0.90),
                }
            )
        output_row = {
            "projectile_template_id": template_id,
            "known_spec": known_templates.get(template_id),
            "client_projectile": projectile_act_catalog.get(template_id),
            "registrations": int(row["registrations"]),
            "hit_registrations": int(row["hit_registrations"]),
            "direct37_hits": int(row["direct37_hits"]),
            "direct37_damage": int(row["direct37_damage"]),
            "file_count": len(row["files"]),
            "actor_count": len(row["actors"]),
            "files": sorted(row["files"]),
            "actors": sorted(row["actors"]),
            "matched_registration_percent": _percent(
                total_votes, int(row["hit_registrations"])
            ),
            "ambiguous_registrations": int(row["ambiguous_registrations"]),
            "unmatched_registrations": int(row["unmatched_registrations"]),
            "effect_resource_ids": dict(row["effect_resource_ids"].most_common()),
            "child_sequences": dict(row["child_sequences"].most_common()),
            "candidates": candidates,
        }
        status, reason = _object_candidate_status(
            output_row, template_id in known_templates
        )
        output_row["status"] = status
        output_row["reason"] = reason
        object_rows.append(output_row)

    observed_rows: list[dict[str, Any]] = []
    for key, row in skills.items():
        output_row = {
            **row,
            "file_count": len(skill_files[key]),
            "actor_count": len(skill_actors[key]),
            "deterministic_hit_percent": _percent(
                row["deterministic_hits"], row["hits"]
            ),
            "deterministic_damage_percent": _percent(
                row["deterministic_damage"], row["damage"]
            ),
            "reliable_cast_percent": _percent(
                row["reliable_casts"], row["casts"]
            ),
            "methods": dict(skill_methods[key].most_common()),
            "method_damage": dict(skill_method_damage[key].most_common()),
            "layers": dict(skill_layers[key].most_common()),
            "layer_damage": dict(skill_layer_damage[key].most_common()),
            "cast_kinds": dict(skill_cast_kinds[key].most_common()),
        }
        status, reason = _skill_status(output_row)
        output_row["status"] = status
        output_row["reason"] = reason
        observed_rows.append(output_row)
    observed_rows.sort(
        key=lambda row: (
            norm(row["job"]),
            norm(row["skill_family"]),
            str(row["skill_id"]),
        )
    )

    store = DataStore.load()
    configured: list[dict[str, str]] = []
    configured_jobs: list[str] = []
    for first_job in store.first_jobs():
        for second_job in store.second_jobs(first_job):
            configured_jobs.append(second_job)
            for skill_name in store.skill_damage_names(second_job, mode="both"):
                if norm(skill_name) in {"", "无", "-"}:
                    continue
                configured.append(
                    {
                        "first_job": first_job,
                        "job": second_job,
                        "skill_name": norm(skill_name),
                        "skill_family": _compact_family(skill_name),
                    }
                )
    observed_families = {
        (norm(row["job"]), norm(row["skill_family"])) for row in observed_rows
    }
    missing_rows = [
        row
        for row in configured
        if (norm(row["job"]), norm(row["skill_family"])) not in observed_families
    ]

    status_counts = Counter(row["status"] for row in observed_rows)
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "analysis_signature": signature,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "client_resource_root": client_resource_root,
        "overview": {
            "captures_analyzed": len(file_rows),
            "duplicate_captures_skipped": len(duplicates),
            "capture_errors": len(errors),
            "capture_bytes": sum(int(row.get("size_bytes", 0) or 0) for row in file_rows),
            "configured_second_jobs": len(dict.fromkeys(configured_jobs)),
            "configured_damage_skill_families": len(configured),
            "configured_skill_families_without_observation": len(missing_rows),
            "observed_job_skill_variants": len(observed_rows),
            "projectile_templates_observed": len(object_rows),
            "projectile_templates_with_direct37": sum(
                int(row["direct37_hits"]) > 0 for row in object_rows
            ),
            "known_object_templates_confirmed": sum(
                row["known_spec"] is not None for row in object_rows
            ),
            "high_confidence_object_candidates": sum(
                row["status"] == "高置信候选，待客户端表复核"
                for row in object_rows
            ),
            "skill_status_counts": dict(status_counts.most_common()),
        },
        "definitions": {
            "object": "动作/响应→Projectile模板→damage_object_id→direct37同对象ID",
            "entity": "召唤/技能实体运行ID→所有者→技能",
            "status": "状态实例/周期伤害记录→施放者与技能",
            "direct": "伤害结果包内直接携带并校验技能ID",
            "inference": "时间窗、既有Hit模板或最近施放等非唯一证据",
            "candidate_gate": "对象候选至少8个有Hit注册、20个direct37、3份文件、2个角色、匹配率≥80%、第一候选纯度≥95%；仍须客户端Projectile/ACT表复核",
            "deployment": "本审计不自动修改生产映射，避免语料相关性被误当作协议事实",
        },
        "object_templates": object_rows,
        "skills": observed_rows,
        "configured_without_observation": missing_rows,
        "files": sorted(file_rows, key=lambda row: norm(row.get("source"))),
        "duplicates": duplicates,
        "errors": errors,
    }
    _write_json(output_dir / "skill_attribution_corpus.json", report)

    skill_csv = output_dir / "skill_attribution_by_profession.csv"
    with skill_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "职业",
            "技能ID",
            "技能",
            "技能族",
            "状态",
            "原因",
            "抓包数",
            "角色数",
            "动作数",
            "可靠动作数",
            "伤害记录数",
            "真实Hit数",
            "确定性Hit覆盖率",
            "伤害",
            "确定性伤害覆盖率",
            "对象链",
            "实体链",
            "状态链",
            "直接链",
            "推断层",
            "未解析",
            "归因方法",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in observed_rows:
            writer.writerow(
                {
                    "职业": row["job"],
                    "技能ID": row["skill_id"],
                    "技能": row["skill_name"],
                    "技能族": row["skill_family"],
                    "状态": row["status"],
                    "原因": row["reason"],
                    "抓包数": row["file_count"],
                    "角色数": row["actor_count"],
                    "动作数": row["casts"],
                    "可靠动作数": row["reliable_casts"],
                    "伤害记录数": row["hits"],
                    "真实Hit数": row["real_hits"],
                    "确定性Hit覆盖率": row["deterministic_hit_percent"],
                    "伤害": row["damage"],
                    "确定性伤害覆盖率": row["deterministic_damage_percent"],
                    "对象链": row["layers"].get("object", 0),
                    "实体链": row["layers"].get("entity", 0),
                    "状态链": row["layers"].get("status", 0),
                    "直接链": row["layers"].get("direct", 0),
                    "推断层": row["layers"].get("inference", 0),
                    "未解析": row["layers"].get("unresolved", 0),
                    "归因方法": json.dumps(
                        row["methods"], ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )

    object_csv = output_dir / "projectile_template_candidates.csv"
    with object_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "Projectile模板",
            "客户端ACT",
            "ACT资源ID",
            "状态",
            "原因",
            "已配置",
            "注册数",
            "有Hit注册数",
            "direct37 Hit",
            "抓包数",
            "角色数",
            "动作匹配率",
            "第一候选职业",
            "第一候选职业分布",
            "第一候选技能ID",
            "第一候选技能",
            "第一候选占比",
            "第一候选抓包数",
            "第一候选角色数",
            "中位延迟秒",
            "候选明细",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in object_rows:
            top = row["candidates"][0] if row["candidates"] else {}
            writer.writerow(
                {
                    "Projectile模板": row["projectile_template_id"],
                    "客户端ACT": (row["client_projectile"] or {}).get("act_file", ""),
                    "ACT资源ID": (row["client_projectile"] or {}).get("act_name_id", ""),
                    "状态": row["status"],
                    "原因": row["reason"],
                    "已配置": "是" if row["known_spec"] else "否",
                    "注册数": row["registrations"],
                    "有Hit注册数": row["hit_registrations"],
                    "direct37 Hit": row["direct37_hits"],
                    "抓包数": row["file_count"],
                    "角色数": row["actor_count"],
                    "动作匹配率": row["matched_registration_percent"],
                    "第一候选职业": top.get("job", ""),
                    "第一候选职业分布": json.dumps(
                        top.get("jobs", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "第一候选技能ID": top.get("skill_id", ""),
                    "第一候选技能": top.get("skill_name", ""),
                    "第一候选占比": top.get("vote_share_percent", ""),
                    "第一候选抓包数": top.get("file_count", ""),
                    "第一候选角色数": top.get("actor_count", ""),
                    "中位延迟秒": top.get("lag_median_seconds", ""),
                    "候选明细": json.dumps(
                        row["candidates"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )

    by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observed_rows:
        by_job[norm(row["job"]) or "未知职业"].append(row)
    missing_by_job: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in missing_rows:
        missing_by_job[norm(row["job"])].append(row)
    lines = [
        "# 全职业技能确定性归因审计",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 成功解析：{report['overview']['captures_analyzed']} 份抓包",
        f"- 二转职业：{report['overview']['configured_second_jobs']} 个",
        f"- 观察到职业/技能变体：{report['overview']['observed_job_skill_variants']} 个",
        f"- 对象模板：{report['overview']['projectile_templates_observed']} 个，其中 {report['overview']['projectile_templates_with_direct37']} 个连到 direct37",
        "",
        "“已确定”只表示现有语料中的伤害归属具有唯一协议证据；“待破解”不会自动写入生产映射。",
        "",
    ]
    ordered_jobs = list(dict.fromkeys(configured_jobs))
    ordered_jobs.extend(job for job in sorted(by_job) if job not in ordered_jobs)
    for job in ordered_jobs:
        rows = by_job.get(job, ())
        if not rows and not missing_by_job.get(job):
            continue
        lines.extend((f"## {job}", "", "| 技能 | ID | 状态 | 确定性伤害 | 样本文件 | 主要证据 |", "|---|---:|---|---:|---:|---|"))
        for row in sorted(rows, key=lambda item: (norm(item["skill_family"]), str(item["skill_id"]))):
            main_layers = ", ".join(
                f"{key}:{value}"
                for key, value in row["layers"].items()
                if int(value) > 0
            )
            lines.append(
                "| {skill} | {skill_id} | {status} | {coverage:.2f}% | {files} | {layers} |".format(
                    skill=str(row["skill_name"]).replace("|", "\\|"),
                    skill_id=row["skill_id"],
                    status=row["status"],
                    coverage=float(row["deterministic_damage_percent"]),
                    files=row["file_count"],
                    layers=main_layers or "无伤害证据",
                )
            )
        for row in sorted(missing_by_job.get(job, ()), key=lambda item: norm(item["skill_name"])):
            lines.append(
                f"| {str(row['skill_name']).replace('|', '\\|')} |  | 暂无样本 | 0.00% | 0 | 现有抓包未观察到 |"
            )
        lines.append("")
    (output_dir / "职业逐技能审计.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit deterministic damage attribution and discover Projectile templates across a dpscap corpus."
    )
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    capture_root = args.capture_root.resolve()
    output_dir = args.output.resolve()
    shard_dir = output_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(capture_root.rglob("*.dpscap"))
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        raise SystemExit(f"no .dpscap files under {capture_root}")

    signature = _analysis_signature()
    print(f"[hash] captures={len(paths)} signature={signature[:12]}", flush=True)
    grouped: dict[str, list[tuple[Path, str, str]]] = defaultdict(list)
    for index, path in enumerate(paths, 1):
        combat_id, evidence_id = SkillHitTemplateLibrary.file_fingerprints(path)
        relative = str(path.relative_to(capture_root))
        grouped[combat_id].append((path, relative, evidence_id))
        if index % 20 == 0 or index == len(paths):
            print(f"[hash] {index}/{len(paths)}", flush=True)

    duplicates: list[dict[str, str]] = []
    selected: list[tuple[Path, str, str, str]] = []
    for combat_id, rows in sorted(grouped.items()):
        chosen = max(
            rows,
            key=lambda item: (
                item[0].stat().st_size,
                item[0].stat().st_mtime_ns,
                item[1],
            ),
        )
        selected.append((chosen[0], chosen[1], combat_id, chosen[2]))
        for row in rows:
            if row == chosen:
                continue
            duplicates.append(
                {
                    "path": row[1],
                    "duplicate_of": chosen[1],
                    "combat_content_id": combat_id,
                }
            )

    work: list[tuple[Path, str, Path]] = []
    for path, relative, combat_id, evidence_id in selected:
        shard = shard_dir / (
            f"{combat_id[:28]}-{evidence_id[:10]}-{signature[:10]}.json.gz"
        )
        work.append((path, relative, shard))

    def valid_shard(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            payload = _read_gzip_json(path)
        except (OSError, EOFError, ValueError, TypeError, gzip.BadGzipFile):
            return False
        return int(payload.get("schema_version", 0) or 0) == AUDIT_SCHEMA_VERSION

    pending = [row for row in work if args.force or not valid_shard(row[2])]
    errors: list[dict[str, str]] = []
    print(
        f"[analyze] unique={len(work)} pending={len(pending)} "
        f"resume={len(work) - len(pending)} workers={max(1, args.workers)}",
        flush=True,
    )
    if pending:
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(_analyze_one, str(path), relative, str(shard)): (
                    relative,
                    shard,
                )
                for path, relative, shard in pending
            }
            completed = 0
            for future in as_completed(futures):
                relative, _shard = futures[future]
                completed += 1
                try:
                    diagnostics = future.result()
                    print(
                        f"[analyze] {completed}/{len(pending)} {relative} "
                        f"{diagnostics['elapsed_seconds']}s",
                        flush=True,
                    )
                except Exception as exc:  # pragma: no cover - corpus diagnostics
                    errors.append({"path": relative, "error": repr(exc)})
                    print(
                        f"[error] {completed}/{len(pending)} {relative}: {exc!r}",
                        flush=True,
                    )

    valid_paths = [row[2] for row in work if valid_shard(row[2])]
    print(f"[aggregate] shards={len(valid_paths)} errors={len(errors)}", flush=True)
    report = _merge_reports(
        valid_paths,
        output_dir,
        signature=signature,
        duplicates=duplicates,
        errors=errors,
    )
    print(
        "[done] "
        f"skills={report['overview']['observed_job_skill_variants']} "
        f"templates={report['overview']['projectile_templates_observed']} "
        f"candidates={report['overview']['high_confidence_object_candidates']} "
        f"output={output_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
