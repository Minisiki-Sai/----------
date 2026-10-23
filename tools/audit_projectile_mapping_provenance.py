from __future__ import annotations

import argparse
import ast
import bisect
import hashlib
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _DAMAGE_OBJECT_SPECS,
)
from dragon_nest_builder.live_dps.event_ledger import (  # noqa: E402
    V576EventLedger,
)
from dragon_nest_builder.live_dps.npcap import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
)
from dragon_nest_builder.live_dps.protocol_catalogs import (  # noqa: E402
    _load_damage_skill_ids,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
    decode_captured_frame,
    decode_v576_damage_object_registration,
    decode_v576_embedded_direct_damage_events,
    decode_v576_server_damage_object_registrations,
)
from tools.build_dungeon_map_catalog import parse_dnt  # noqa: E402


TARGET_RELATIVE = Path(
    "dragon_nest_builder/live_dps/damage_object_origins.py"
)
DEFAULT_RESOURCE_ROOT = Path(
    r"E:\ext0811.rar\Resource00\Resource00\resource"
)
DEFAULT_CAPTURE_ROOT = Path(
    r"C:\Users\gcg59\Documents\DragonNestDpsCapture"
    r"\live_dps_diagnostics"
)
DEFAULT_JSON = ROOT / "outputs/damage_attribution/projectile_mapping_provenance_audit.json"
DEFAULT_MARKDOWN = ROOT / "outputs/damage_attribution/projectile_mapping_provenance_audit.md"

MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
SKIPPED_ADAPTERS = frozenset(
    {
        IDENTITY_TRACE_ADAPTER_INDEX,
        IDENTITY_METADATA_ADAPTER_INDEX,
        SESSION_EVIDENCE_ADAPTER_INDEX,
    }
)
PACKET_JOIN_MAX_AGE_MS = 30_000
SAFE_PACKET_MIN_HITS = 30

# These groups reproduce the provenance claims immediately above the relevant
# declarations in damage_object_origins.py. They do not promote those claims;
# the independent packet and DNT audits below decide the final classification.
CONTROLLED_FIELD_TEMPLATES = frozenset({3006, 3029, 3039, 3040})
CORPUS_OBJECT_DNT_TEMPLATE = frozenset({3004})
MIXED_PACKET_OR_RESULT_GROUP_TEMPLATES = frozenset(
    {1009, 1010, 1011, 1022, 1036, 1037, 1038, 2033, 2034, 2025, 4004, 4024}
)
SACRED_HAMMER_PACKET_CLAIM = frozenset({4012, 4013})
SHARED_BASE_CORPUS_CLAIM = frozenset(
    {94, 3001, 3012, 3018, 3026, 3027, 3032}
)
UNIQUE_ACT_CORPUS_CLAIM = frozenset({3022, 2013})
ACTION_OWNER_GUARDED = frozenset({5816, 5834})
STRICT_RESOURCE_JOIN_START = frozenset(
    {
        81,
        93,
        106,
        107,
        2000,
        2007,
        2009,
        2012,
        2018,
        2021,
        2029,
        2039,
        2040,
        2206,
        2217,
        3000,
        3005,
        3011,
        3013,
        3017,
        3023,
        3031,
        3036,
        3037,
        4015,
        5002,
        5011,
        5017,
        5047,
        5052,
        5056,
        5060,
        5066,
        5504,
        5521,
        5612,
        5706,
        5712,
        5713,
        5717,
        5718,
        5719,
        5805,
        5806,
        11043,
        11061,
        11087,
        11088,
        11113,
        11116,
        33101,
        33103,
        33108,
        33111,
        33113,
        33114,
        33115,
        33117,
    }
)

RESOURCE_PREFIXES = (
    "plateskillman",
    "darkavenger",
    "sorceress",
    "soceress",
    "academic",
    "assassin",
    "warrior",
    "archer",
    "cleric",
    "hunter",
    "kali",
    "skill",
    "arrow",
)
FAMILY_SUFFIX_PATTERNS = (
    r"destination$",
    r"projectile$",
    r"attack\d*$",
    r"noeffect\d*$",
    r"noeff\d*$",
    r"level\d+$",
    r"combo$",
    r"summon$",
    r"shoot$",
    r"start$",
    r"loop$",
    r"main$",
    r"side$",
    r"first$",
    r"fast$",
    r"small$",
    r"pve$",
    r"dest$",
    r"new$",
    r"sub$",
    r"air$",
    r"big$",
    r"lv\d+$",
    r"ex$",
    r"\d+(?:st|nd|rd|th)?$",
)


@dataclass(frozen=True, slots=True)
class Registration:
    packet_index: int
    timestamp_ms: int
    owner_actor_id: int
    object_id: int
    template_id: int
    direction: str
    source_layout: str


@dataclass(frozen=True, slots=True)
class ExactHit:
    packet_index: int
    timestamp_ms: int
    actor_id: int
    object_id: int
    skill_id: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_lines(path: Path) -> dict[int, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: dict[int, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "_DAMAGE_OBJECT_SPECS"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List)):
            continue
        for item in node.value.elts:
            if not isinstance(item, ast.Call):
                continue
            template_node: ast.AST | None = (
                item.args[1] if len(item.args) >= 2 else None
            )
            if template_node is None:
                template_node = next(
                    (
                        keyword.value
                        for keyword in item.keywords
                        if keyword.arg == "projectile_template_id"
                    ),
                    None,
                )
            if template_node is None:
                continue
            try:
                template_id = int(ast.literal_eval(template_node))
            except (TypeError, ValueError):
                continue
            lines[template_id] = int(item.lineno)
    return lines


def _claim_group(template_id: int) -> str:
    if template_id in CONTROLLED_FIELD_TEMPLATES:
        return "controlled_field_capture_plus_client_resource_claim"
    if template_id in CORPUS_OBJECT_DNT_TEMPLATE:
        return "corpus_object_join_plus_client_resource_claim"
    if template_id in MIXED_PACKET_OR_RESULT_GROUP_TEMPLATES:
        return "packet_or_direct_result_group_mixed_claim"
    if template_id in SACRED_HAMMER_PACKET_CLAIM:
        return "act_skilllevel_plus_fixed_slot_packet_claim"
    if template_id in SHARED_BASE_CORPUS_CLAIM:
        return "cross_job_corpus_plus_client_resource_claim"
    if template_id in UNIQUE_ACT_CORPUS_CLAIM:
        return "unique_act_skilllevel_plus_multi_capture_object_claim"
    if template_id in ACTION_OWNER_GUARDED:
        return "client_resource_plus_runtime_action_guard_claim"
    if template_id in STRICT_RESOURCE_JOIN_START:
        return "strict_client_resource_name_join_claim"
    return "unique_act_skilllevel_resource_name_claim"


def _base_resource_token(value: object) -> str:
    text = str(value or "").replace("\\", "/")
    stem = Path(text).stem.casefold()
    token = re.sub(r"[^a-z0-9]", "", stem)
    changed = True
    while changed and token:
        changed = False
        for prefix in RESOURCE_PREFIXES:
            if token.startswith(prefix) and len(token) > len(prefix):
                token = token[len(prefix) :]
                changed = True
                break
    return token


def _family_resource_token(value: object) -> str:
    token = _base_resource_token(value)
    changed = True
    while changed and token:
        changed = False
        for pattern in FAMILY_SUFFIX_PATTERNS:
            stripped = re.sub(pattern, "", token)
            if stripped != token and len(stripped) >= 4:
                token = stripped
                changed = True
                break
    return token


def _load_static_tables(resource_root: Path) -> dict[str, Any]:
    ext = resource_root / "ext"
    projectile_path = ext / "weapontable_projectile.dnt"
    file_path = ext / "filetable.dnt"
    skill_path = ext / "skilltable_character.dnt"
    projectile_rows = {
        int(row["RowId"]): row for row in parse_dnt(projectile_path)
    }
    file_rows = {int(row["RowId"]): row for row in parse_dnt(file_path)}
    skill_rows = {int(row["RowId"]): row for row in parse_dnt(skill_path)}

    params_by_skill: dict[int, set[str]] = defaultdict(set)
    level_row_counts: Counter[int] = Counter()
    level_paths = sorted(ext.glob("skillleveltable_character*.dnt"))
    for level_path in level_paths:
        for row in parse_dnt(level_path):
            skill_id = int(row.get("_SkillIndex", 0) or 0)
            if skill_id <= 0:
                continue
            level_row_counts[skill_id] += 1
            if int(row.get("_ApplyType", 0) or 0) != 0:
                continue
            for index in range(1, 11):
                raw = str(row.get(f"_ProcessParam{index}", "") or "")
                for value in raw.split(";"):
                    clean = value.strip()
                    if (
                        clean
                        and not clean.replace(".", "", 1).lstrip("-").isdigit()
                    ):
                        params_by_skill[skill_id].add(clean)

    exact_index: dict[str, set[int]] = defaultdict(set)
    family_index: dict[str, set[int]] = defaultdict(set)
    names: dict[int, str] = {}
    resources_by_skill: dict[int, set[str]] = defaultdict(set)
    for skill_id, row in skill_rows.items():
        static_name = str(row.get("_StaticName", "") or "").strip()
        names[skill_id] = static_name
        if static_name:
            resources_by_skill[skill_id].add(static_name)
        resources_by_skill[skill_id].update(params_by_skill.get(skill_id, ()))
        for value in resources_by_skill[skill_id]:
            exact = _base_resource_token(value)
            family = _family_resource_token(value)
            if exact:
                exact_index[exact].add(skill_id)
            if family:
                family_index[family].add(skill_id)

    return {
        "paths": {
            "projectile": projectile_path,
            "file": file_path,
            "skill": skill_path,
            "skilllevels": level_paths,
        },
        "projectiles": projectile_rows,
        "files": file_rows,
        "skills": skill_rows,
        "skill_names": names,
        "params_by_skill": params_by_skill,
        "level_row_counts": level_row_counts,
        "exact_index": exact_index,
        "family_index": family_index,
    }


def _static_evidence_for_spec(
    spec: Any,
    tables: dict[str, Any],
    resource_root: Path,
) -> dict[str, Any]:
    template_id = int(spec.projectile_template_id)
    expected = {int(spec.canonical_skill_id), *map(int, spec.skill_ids)}
    projectile = tables["projectiles"].get(template_id)
    act_file_id = int(projectile.get("_ActName", 0) or 0) if projectile else 0
    file_row = tables["files"].get(act_file_id)
    act_file = str(file_row.get("_FileName", "") or "") if file_row else ""
    act_path = resource_root / "weapon" / act_file if act_file else None
    exact_token = _base_resource_token(act_file)
    family_token = _family_resource_token(act_file)
    exact_candidates = set(tables["exact_index"].get(exact_token, ()))
    family_candidates = set(tables["family_index"].get(family_token, ()))
    effective_candidates = exact_candidates or family_candidates
    bridge_kind = (
        "exact_resource_token"
        if exact_candidates
        else "mechanically_normalized_family_token"
        if family_candidates
        else "no_static_name_bridge"
    )
    expected_candidates = effective_candidates & expected
    outside_candidates = effective_candidates - expected
    static_unique = bool(
        projectile
        and file_row
        and act_path is not None
        and act_path.is_file()
        and expected_candidates
        and not outside_candidates
        and all(tables["level_row_counts"].get(skill_id, 0) > 0 for skill_id in expected_candidates)
    )
    def render_candidates(values: set[int]) -> list[dict[str, Any]]:
        return [
            {
                "skill_id": skill_id,
                "static_name": tables["skill_names"].get(skill_id, ""),
            }
            for skill_id in sorted(values)
        ]

    return {
        "projectile_row_found": projectile is not None,
        "projectile_to_act_numeric_edge": bool(projectile and act_file_id > 0),
        "act_file_id": act_file_id,
        "filetable_row_found": file_row is not None,
        "act_file": act_file,
        "act_file_exists": bool(act_path and act_path.is_file()),
        "skilltable_rows_found": sorted(expected & set(tables["skills"])),
        "skilllevel_numeric_skillindex_rows": {
            str(skill_id): int(tables["level_row_counts"].get(skill_id, 0))
            for skill_id in sorted(expected)
        },
        "projectile_resource_token": exact_token,
        "projectile_family_token": family_token,
        "bridge_kind": bridge_kind,
        "exact_resource_candidates": render_candidates(exact_candidates),
        "normalized_family_candidates": render_candidates(family_candidates),
        "effective_candidates": render_candidates(effective_candidates),
        "mapped_family_candidates": render_candidates(expected_candidates),
        "outside_mapped_family_candidates": render_candidates(outside_candidates),
        "static_unique_name_bridge_supported": static_unique,
        "numeric_skill_foreign_key_found": False,
        "interpretation": (
            "Projectile.RowId -> _ActName -> filetable.RowId is numeric; "
            "SkillTable.RowId -> SkillLevel._SkillIndex is numeric; the edge "
            "between the ACT resource and skill remains a string-name bridge."
        ),
    }


def _iter_logical_packets(path: Path) -> Iterable[Any]:
    transport = V576TransportReassembler()
    ledger = V576EventLedger()
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError("not a DNDP1 capture")
        while True:
            header = handle.read(FRAME_HEADER.size)
            if not header:
                break
            if len(header) != FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = (
                FRAME_HEADER.unpack(header)
            )
            if captured_length > 16 * 1024 * 1024:
                raise ValueError("abnormal frame length")
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter in SKIPPED_ADAPTERS:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical in transport.feed(packet):
                if ledger.accept(logical):
                    yield logical
    for logical in transport.flush():
        if ledger.accept(logical):
            yield logical


def _audit_capture(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    known_skill_ids = _load_damage_skill_ids(str(DATA_DIR))
    registrations: list[Registration] = []
    hits: list[ExactHit] = []
    packet_count = 0
    for packet_count, packet in enumerate(_iter_logical_packets(path), start=1):
        payload = bytes(packet.payload)
        direction = packet.inferred_direction()
        direction_text = str(direction or "unknown")
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)

        local = decode_v576_damage_object_registration(payload, direction)
        if local is not None:
            registrations.append(
                Registration(
                    packet_index=packet_count,
                    timestamp_ms=timestamp_ms,
                    owner_actor_id=int(local.source_actor_id),
                    object_id=int(local.damage_object_id),
                    template_id=int(local.projectile_template_id),
                    direction=direction_text,
                    source_layout="c2s_107",
                )
            )
        for server in decode_v576_server_damage_object_registrations(
            payload,
            direction,
            include_unmapped=True,
        ):
            registrations.append(
                Registration(
                    packet_index=packet_count,
                    timestamp_ms=timestamp_ms,
                    owner_actor_id=int(server.source_actor_id),
                    object_id=int(server.damage_object_id),
                    template_id=int(server.projectile_template_id),
                    direction=direction_text,
                    source_layout="s2c_095d_0961_096e",
                )
            )
        for hit in decode_v576_embedded_direct_damage_events(
            payload,
            direction,
            known_skill_ids,
        ):
            if (
                hit.skill_id_exact
                and int(hit.skill_id or 0) > 0
                and int(hit.source_actor_id or 0) > 0
                and int(hit.damage_object_id or 0) > 0
            ):
                hits.append(
                    ExactHit(
                        packet_index=packet_count,
                        timestamp_ms=timestamp_ms,
                        actor_id=int(hit.source_actor_id),
                        object_id=int(hit.damage_object_id),
                        skill_id=int(hit.skill_id),
                    )
                )

    by_key: dict[tuple[int, int], list[Registration]] = defaultdict(list)
    for row in registrations:
        by_key[(row.owner_actor_id, row.object_id)].append(row)
    for rows in by_key.values():
        rows.sort(key=lambda row: (row.packet_index, row.timestamp_ms, row.template_id))

    template_rows: dict[int, dict[str, Any]] = {}
    skipped_ambiguous_generation = 0
    skipped_stale = 0
    for hit in hits:
        candidates = by_key.get((hit.actor_id, hit.object_id), ())
        if not candidates:
            continue
        packet_positions = [row.packet_index for row in candidates]
        index = bisect.bisect_right(packet_positions, hit.packet_index) - 1
        if index < 0:
            continue
        latest_packet = candidates[index].packet_index
        same_generation = [
            row for row in candidates if row.packet_index == latest_packet
        ]
        template_ids = {row.template_id for row in same_generation}
        if len(template_ids) != 1:
            skipped_ambiguous_generation += 1
            continue
        registration = candidates[index]
        age_ms = hit.timestamp_ms - registration.timestamp_ms
        if age_ms < 0 or age_ms > PACKET_JOIN_MAX_AGE_MS:
            skipped_stale += 1
            continue
        template_id = next(iter(template_ids))
        row = template_rows.setdefault(
            template_id,
            {
                "skill_counts": Counter(),
                "actors": set(),
                "layouts": Counter(),
                "min_age_ms": age_ms,
                "max_age_ms": age_ms,
            },
        )
        row["skill_counts"][hit.skill_id] += 1
        row["actors"].add(hit.actor_id)
        row["layouts"][registration.source_layout] += 1
        row["min_age_ms"] = min(int(row["min_age_ms"]), age_ms)
        row["max_age_ms"] = max(int(row["max_age_ms"]), age_ms)

    return {
        "capture": str(path),
        "size_bytes": path.stat().st_size,
        "logical_packets": packet_count,
        "registrations": len(registrations),
        "fixed_slot_exact_hits": len(hits),
        "skipped_ambiguous_registration_generation": skipped_ambiguous_generation,
        "skipped_stale_registration_join": skipped_stale,
        "templates": {
            str(template_id): {
                "skill_counts": {
                    str(skill_id): count
                    for skill_id, count in sorted(row["skill_counts"].items())
                },
                "actors": [f"0x{actor_id:08X}" for actor_id in sorted(row["actors"])],
                "registration_layout_counts": dict(row["layouts"]),
                "min_registration_age_ms": int(row["min_age_ms"]),
                "max_registration_age_ms": int(row["max_age_ms"]),
            }
            for template_id, row in sorted(template_rows.items())
        },
    }


def _aggregate_packet_evidence(
    reports: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for report in reports:
        capture = str(report["capture"])
        for template_text, values in report["templates"].items():
            template_id = int(template_text)
            row = merged.setdefault(
                template_id,
                {
                    "skill_counts": Counter(),
                    "captures": set(),
                    "actors": set(),
                    "registration_layout_counts": Counter(),
                    "min_registration_age_ms": None,
                    "max_registration_age_ms": None,
                },
            )
            row["skill_counts"].update(
                {int(key): int(value) for key, value in values["skill_counts"].items()}
            )
            row["captures"].add(capture)
            row["actors"].update(values["actors"])
            row["registration_layout_counts"].update(
                values["registration_layout_counts"]
            )
            minimum = int(values["min_registration_age_ms"])
            maximum = int(values["max_registration_age_ms"])
            if row["min_registration_age_ms"] is None:
                row["min_registration_age_ms"] = minimum
                row["max_registration_age_ms"] = maximum
            else:
                row["min_registration_age_ms"] = min(
                    int(row["min_registration_age_ms"]), minimum
                )
                row["max_registration_age_ms"] = max(
                    int(row["max_registration_age_ms"]), maximum
                )
    return merged


def _result_group_risk() -> dict[str, Any]:
    corpus_path = (
        ROOT
        / "outputs/damage_attribution/corpus_201_direct_damage_audit_20260906/summary.json"
    )
    temporal_path = (
        ROOT
        / "outputs/damage_attribution/result_group_temporal_cross_validation_4captures.json"
    )
    result: dict[str, Any] = {
        "excluded_from_mapping_evidence": True,
        "reason": (
            "direct_result_group_link is not an independent skill identity: "
            "result-group values are reused and can carry multiple fixed-slot packet skills."
        ),
    }
    if corpus_path.is_file():
        data = json.loads(corpus_path.read_text(encoding="utf-8"))
        result["corpus_201_source"] = str(corpus_path)
        result["corpus_201_result_group_stats"] = data.get(
            "result_group_stats", {}
        )
    if temporal_path.is_file():
        data = json.loads(temporal_path.read_text(encoding="utf-8"))
        result["four_capture_source"] = str(temporal_path)
        result["four_capture_5ms_stats"] = data.get("windows", {}).get("5", {})
        result["four_capture_contiguous_run_stats"] = data.get(
            "contiguous_runs", {}
        )
    return result


def _classify_mapping(
    *,
    packet_support: int,
    packet_conflicts: int,
    static_evidence: dict[str, Any],
) -> tuple[str, list[str]]:
    if packet_conflicts > 0:
        return "unsafe", [
            f"{packet_conflicts} fixed-slot packet_skill_id anchor(s) contradict the configured family"
        ]
    if (
        static_evidence["outside_mapped_family_candidates"]
        and not static_evidence["mapped_family_candidates"]
        and packet_support == 0
    ):
        return "unsafe", [
            "the current DNT resource-name bridge points outside the configured skill family and has no packet anchor"
        ]
    if packet_support >= SAFE_PACKET_MIN_HITS:
        return "safe", [
            f"{packet_support} exact packet_skill_id anchors, zero conflicts"
        ]
    if packet_support > 0:
        return "needs_more_evidence", [
            f"only {packet_support} exact packet_skill_id anchor(s); below the {SAFE_PACKET_MIN_HITS}-hit independence gate"
        ]
    if static_evidence["static_unique_name_bridge_supported"]:
        return "needs_more_evidence", [
            "DNT chains are numeric on both sides but the ACT-to-skill edge is still only a unique string-name bridge"
        ]
    return "needs_more_evidence", [
        "no fixed-slot packet_skill_id anchor and no unique DNT resource-name bridge"
    ]


def _build_result(
    *,
    resource_root: Path,
    capture_root: Path,
    captures: list[Path],
    reports: list[dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    target = ROOT / TARGET_RELATIVE
    tables = _load_static_tables(resource_root)
    lines = _source_lines(target)
    packet = _aggregate_packet_evidence(reports)
    mappings: list[dict[str, Any]] = []
    for spec in sorted(
        _DAMAGE_OBJECT_SPECS,
        key=lambda row: int(row.projectile_template_id),
    ):
        template_id = int(spec.projectile_template_id)
        family = {int(spec.canonical_skill_id), *map(int, spec.skill_ids)}
        static = _static_evidence_for_spec(spec, tables, resource_root)
        packet_row = packet.get(template_id, {})
        skill_counts = Counter(packet_row.get("skill_counts", {}))
        support_counts = Counter(
            {
                skill_id: count
                for skill_id, count in skill_counts.items()
                if int(skill_id) in family
            }
        )
        conflict_counts = Counter(
            {
                skill_id: count
                for skill_id, count in skill_counts.items()
                if int(skill_id) not in family
            }
        )
        support_hits = sum(support_counts.values())
        conflict_hits = sum(conflict_counts.values())
        classification, reasons = _classify_mapping(
            packet_support=support_hits,
            packet_conflicts=conflict_hits,
            static_evidence=static,
        )
        claim_group = _claim_group(template_id)
        mixed_claim = template_id in MIXED_PACKET_OR_RESULT_GROUP_TEMPLATES
        result_group_dependency = (
            "mixed_claim_superseded_by_packet_evidence"
            if mixed_claim and support_hits >= SAFE_PACKET_MIN_HITS and not conflict_hits
            else "mixed_claim_not_independently_cleared"
            if mixed_claim
            else "not_claimed_as_establishment_basis"
        )
        name_only = (
            support_hits == 0
            and conflict_hits == 0
            and claim_group
            in {
                "unique_act_skilllevel_resource_name_claim",
                "strict_client_resource_name_join_claim",
            }
        )
        mappings.append(
            {
                "projectile_template_id": template_id,
                "key": str(spec.key),
                "declaration_line": lines.get(template_id),
                "canonical_skill_id": int(spec.canonical_skill_id),
                "configured_skill_ids": sorted(map(int, spec.skill_ids)),
                "source_claim_group": claim_group,
                "classification": classification,
                "classification_reasons": reasons,
                "packet_skill_id": {
                    "join_policy": (
                        "latest complete registration generation with exact owner+object, "
                        f"0..{PACKET_JOIN_MAX_AGE_MS} ms; any intervening template registration supersedes the old generation"
                    ),
                    "support_hits": support_hits,
                    "conflict_hits": conflict_hits,
                    "zero_conflict": support_hits > 0 and conflict_hits == 0,
                    "support_skill_counts": {
                        str(key): value for key, value in support_counts.most_common()
                    },
                    "conflict_skill_counts": {
                        str(key): value for key, value in conflict_counts.most_common()
                    },
                    "all_skill_counts": {
                        str(key): value for key, value in skill_counts.most_common()
                    },
                    "capture_count": len(packet_row.get("captures", ())),
                    "actor_count": len(packet_row.get("actors", ())),
                    "captures": sorted(packet_row.get("captures", ())),
                    "actors": sorted(packet_row.get("actors", ())),
                    "registration_layout_counts": dict(
                        packet_row.get("registration_layout_counts", {})
                    ),
                    "min_registration_age_ms": packet_row.get(
                        "min_registration_age_ms"
                    ),
                    "max_registration_age_ms": packet_row.get(
                        "max_registration_age_ms"
                    ),
                },
                "dnt_static": static,
                "direct_result_group_link": {
                    "used_as_audit_evidence": False,
                    "source_claim_was_mixed_packet_or_result_group": mixed_claim,
                    "dependency_assessment": result_group_dependency,
                },
                "provenance_flags": {
                    "only_resource_naming_establishment_claim": name_only,
                    "controlled_or_corpus_temporal_claim_without_packet": (
                        support_hits == 0
                        and claim_group
                        not in {
                            "unique_act_skilllevel_resource_name_claim",
                            "strict_client_resource_name_join_claim",
                        }
                    ),
                    "dnt_static_unique_name_bridge": bool(
                        static["static_unique_name_bridge_supported"]
                    ),
                    "dnt_numeric_skill_foreign_key": False,
                    "packet_safe_gate_passed": bool(
                        support_hits >= SAFE_PACKET_MIN_HITS
                        and conflict_hits == 0
                    ),
                },
            }
        )

    classification_counts = Counter(
        row["classification"] for row in mappings
    )
    claim_counts = Counter(row["source_claim_group"] for row in mappings)
    summary = {
        "mapping_count": len(mappings),
        "unique_projectile_templates": len(
            {row["projectile_template_id"] for row in mappings}
        ),
        "classification_counts": dict(classification_counts),
        "source_claim_group_counts": dict(claim_counts),
        "templates_with_any_packet_anchor": sum(
            row["packet_skill_id"]["support_hits"]
            + row["packet_skill_id"]["conflict_hits"]
            > 0
            for row in mappings
        ),
        "templates_with_zero_conflict_packet_support": sum(
            row["packet_skill_id"]["zero_conflict"] for row in mappings
        ),
        "templates_passing_packet_safe_gate": sum(
            row["provenance_flags"]["packet_safe_gate_passed"]
            for row in mappings
        ),
        "templates_with_packet_conflicts": sum(
            row["packet_skill_id"]["conflict_hits"] > 0 for row in mappings
        ),
        "templates_with_dnt_static_unique_name_bridge": sum(
            row["provenance_flags"]["dnt_static_unique_name_bridge"]
            for row in mappings
        ),
        "templates_with_numeric_dnt_skill_foreign_key": 0,
        "templates_only_claimed_from_resource_naming_without_packet": sum(
            row["provenance_flags"]["only_resource_naming_establishment_claim"]
            for row in mappings
        ),
        "templates_with_non_name_runtime_or_corpus_claim_without_packet": sum(
            row["provenance_flags"][
                "controlled_or_corpus_temporal_claim_without_packet"
            ]
            for row in mappings
        ),
        "mixed_result_group_claim_templates": len(
            MIXED_PACKET_OR_RESULT_GROUP_TEMPLATES
        ),
        "mixed_result_group_claims_cleared_by_packet_gate": sum(
            row["direct_result_group_link"]["dependency_assessment"]
            == "mixed_claim_superseded_by_packet_evidence"
            for row in mappings
        ),
    }
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(
            timezone(timedelta(hours=8), name="Asia/Shanghai")
        ).isoformat(),
        "scope": (
            "All current DamageObjectTemplateSpec projectile-template-to-skill mappings. "
            "Production code is read-only."
        ),
        "policy": {
            "safe": (
                f"At least {SAFE_PACKET_MIN_HITS} exact fixed-slot packet_skill_id anchors "
                "on the same registered owner+object generation and zero conflicts."
            ),
            "needs_more_evidence": (
                "No contradiction, but packet support is absent/below the safe gate or "
                "the ACT-to-skill edge remains a string-name/temporal bridge."
            ),
            "unsafe": (
                "At least one contradictory fixed-slot packet_skill_id anchor, or the only "
                "static name bridge points outside the configured family."
            ),
            "excluded_from_positive_proof": [
                "direct_result_group_link",
                "nearest action in time",
                "damage magnitude/profile",
                "current inferred skill bucket",
            ],
            "important_static_boundary": (
                "No numeric DNT foreign key from Projectile/ACT to Skill was found. "
                "A unique ACT/SkillLevel resource token is reported as static support, "
                "but is not promoted to safe without packet evidence."
            ),
        },
        "inputs": {
            "target": str(target),
            "target_sha256": _sha256(target),
            "resource_root": str(resource_root),
            "capture_root": str(capture_root),
            "captures_discovered": len(captures),
            "captures_audited": len(reports),
            "capture_errors": errors,
            "capture_bytes_audited": sum(
                int(report["size_bytes"]) for report in reports
            ),
            "logical_packets_audited": sum(
                int(report["logical_packets"]) for report in reports
            ),
            "registrations_audited": sum(
                int(report["registrations"]) for report in reports
            ),
            "fixed_slot_exact_hits_audited": sum(
                int(report["fixed_slot_exact_hits"]) for report in reports
            ),
            "dnt_files": {
                key: {
                    "path": str(path),
                    "sha256": _sha256(path),
                }
                for key, path in tables["paths"].items()
                if key != "skilllevels"
            },
            "skilllevel_dnt_file_count": len(tables["paths"]["skilllevels"]),
        },
        "result_group_risk": _result_group_risk(),
        "summary": summary,
        "mappings": mappings,
    }
    return result


def _compact_evidence(row: dict[str, Any]) -> str:
    packet = row["packet_skill_id"]
    static = row["dnt_static"]
    parts = []
    if packet["support_hits"] or packet["conflict_hits"]:
        parts.append(
            f"packet {packet['support_hits']}/{packet['conflict_hits']} (support/conflict)"
        )
    if static["static_unique_name_bridge_supported"]:
        parts.append(f"DNT unique-name:{static['bridge_kind']}")
    elif static["outside_mapped_family_candidates"]:
        outside = ",".join(
            str(item["skill_id"])
            for item in static["outside_mapped_family_candidates"]
        )
        parts.append(f"DNT outside-family:{outside}")
    else:
        parts.append("no unique DNT skill bridge")
    return "; ".join(parts)


def _render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    mappings = result["mappings"]
    classes: dict[str, list[dict[str, Any]]] = {
        name: [row for row in mappings if row["classification"] == name]
        for name in ("safe", "needs_more_evidence", "unsafe")
    }
    lines = [
        "# Projectile template→skill 映射证据审计",
        "",
        f"生成时间：{result['generated_at']}",
        "",
        "## 结论",
        "",
        f"当前生产表共有 **{summary['mapping_count']}** 条、{summary['unique_projectile_templates']} 个唯一 Projectile template。按本报告的 fail-closed 门槛：",
        "",
        f"- `safe`：**{summary['classification_counts'].get('safe', 0)}**",
        f"- `needs_more_evidence`：**{summary['classification_counts'].get('needs_more_evidence', 0)}**",
        f"- `unsafe`：**{summary['classification_counts'].get('unsafe', 0)}**",
        "",
        f"审计覆盖 {result['inputs']['captures_audited']}/{result['inputs']['captures_discovered']} 份抓包、{result['inputs']['logical_packets_audited']:,} 个逻辑包、{result['inputs']['registrations_audited']:,} 条结构合法对象注册和 {result['inputs']['fixed_slot_exact_hits_audited']:,} 条 fixed-slot 精确技能 Hit。对象连接键为同一抓包内最新的 `(owner_actor_id, damage_object_id, registration generation)`，最大 30 秒；任一后续模板注册会覆盖旧代次。",
        "",
        "`direct_result_group_link` 完全未作为正证据。既有 201 包审计中，带 packet 锚点的结果组有 4,205 个出现多技能身份；4 包 leave-one-out 在 5 ms 已有 33 条 exact conflict。因此结果组不能挽救任何缺少独立证据的映射。",
        "",
        "## 判定门槛",
        "",
        f"- `safe`：同一注册对象代次上至少 {SAFE_PACKET_MIN_HITS} 条 fixed-slot `packet_skill_id`，且 0 冲突。",
        "- `needs_more_evidence`：无矛盾，但 packet 样本不足，或 ACT→skill 仍是字符串资源名/时间关联。",
        "- `unsafe`：至少 1 条 fixed-slot packet 冲突，或唯一静态名字候选指向配置族外。",
        "",
        "DNT 的确定边界必须写清：`Projectile.RowId → _ActName → filetable.RowId → ACT` 是数值链，`SkillTable.RowId → SkillLevel._SkillIndex` 也是数值链；两条链之间没有发现数值外键。下文的“DNT static unique”只是唯一字符串资源名桥，不单独升级为 `safe`。",
        "",
        "## 关键结果",
        "",
        f"- 有任意 packet 锚点的模板：{summary['templates_with_any_packet_anchor']}；其中零冲突：{summary['templates_with_zero_conflict_packet_support']}；达到 30-Hit safe gate：{summary['templates_passing_packet_safe_gate']}。",
        f"- 有 packet 冲突的模板：{summary['templates_with_packet_conflicts']}。",
        f"- 有 DNT 静态唯一名字桥的模板：{summary['templates_with_dnt_static_unique_name_bridge']}；真正的 Projectile/ACT→Skill 数值外键：0。",
        f"- 建立注释仅声称资源命名连接、且无 packet 锚点的模板：{summary['templates_only_claimed_from_resource_naming_without_packet']}。",
        f"- 有 corpus/受控捕获/动作时序主张、但无 packet 锚点的模板：{summary['templates_with_non_name_runtime_or_corpus_claim_without_packet']}。",
        f"- 源码中“packet 或 result-group”混合表述的 12 个模板，有 {summary['mixed_result_group_claims_cleared_by_packet_gate']} 个已由独立 packet gate 清除结果组依赖。",
        "",
    ]

    for class_name, title in (
        ("safe", "Safe"),
        ("unsafe", "Unsafe"),
        ("needs_more_evidence", "Needs more evidence"),
    ):
        rows = sorted(
            classes[class_name],
            key=lambda row: (
                -row["packet_skill_id"]["conflict_hits"],
                -row["packet_skill_id"]["support_hits"],
                row["projectile_template_id"],
            ),
        )
        lines.extend(
            [
                f"## {title}（{len(rows)}）",
                "",
                "| Template | key | canonical skill | 证据摘要 |",
                "|---:|---|---:|---|",
            ]
        )
        for row in rows:
            lines.append(
                f"| {row['projectile_template_id']} | `{row['key']}` | {row['canonical_skill_id']} | {_compact_evidence(row)} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 结果组依赖与命名依赖",
            "",
            "源码注释中混合写成“`packet_skill_id` 或 exact direct-result-group”的模板如下。报告只看独立 packet 列；达到 gate 的不再依赖结果组：",
            "",
            "| Template | key | packet support/conflict | 结果组依赖结论 |",
            "|---:|---|---:|---|",
        ]
    )
    for row in mappings:
        if not row["direct_result_group_link"][
            "source_claim_was_mixed_packet_or_result_group"
        ]:
            continue
        packet = row["packet_skill_id"]
        lines.append(
            f"| {row['projectile_template_id']} | `{row['key']}` | {packet['support_hits']}/{packet['conflict_hits']} | {row['direct_result_group_link']['dependency_assessment']} |"
        )

    name_only = [
        row
        for row in mappings
        if row["provenance_flags"]["only_resource_naming_establishment_claim"]
    ]
    lines.extend(
        [
            "",
            "下列模板的源码建立说明只给出 ACT/SkillLevel 资源名连接，且本次没有 packet 锚点；它们不能因名字唯一就视为确定性映射：",
            "",
            ", ".join(
                f"{row['projectile_template_id']} (`{row['key']}`)"
                for row in name_only
            )
            or "无",
            "",
            "下列模板虽有历史 corpus、受控捕获或动作时序主张，但本次仍无 fixed-slot packet 锚点；这些主张不会自动升级为确定性技能身份：",
            "",
            ", ".join(
                f"{row['projectile_template_id']} (`{row['key']}`)"
                for row in mappings
                if row["provenance_flags"][
                    "controlled_or_corpus_temporal_claim_without_packet"
                ]
            )
            or "无",
            "",
            "## 复核入口",
            "",
            f"- 生产映射（只读）：`{result['inputs']['target']}`",
            f"- 机器可读逐模板证据：`{DEFAULT_JSON}`",
            f"- 审计脚本：`{Path(__file__).resolve()}`",
            "",
            "JSON 为完整证据账本，包含每个模板的声明行、DNT 数值边、字符串候选、packet skill 分布、抓包数、actor 数、注册布局与冲突技能 ID。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit provenance for every current Projectile template -> skill mapping."
        )
    )
    parser.add_argument("--resource-root", type=Path, default=DEFAULT_RESOURCE_ROOT)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-captures", type=int)
    args = parser.parse_args()

    resource_root = args.resource_root.resolve()
    capture_root = args.capture_root.resolve()
    captures = sorted(capture_root.rglob("*.dpscap"))
    if args.max_captures is not None:
        captures = captures[: max(0, args.max_captures)]
    if not captures:
        raise SystemExit(f"no captures found under {capture_root}")

    reports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    workers = max(1, int(args.workers))
    # The transport/reassembly decoder processes multi-gigabyte capture
    # corpora.  Recycling workers prevents native/allocator high-water marks
    # from accumulating across dozens of otherwise independent captures.
    with ProcessPoolExecutor(
        max_workers=workers,
        max_tasks_per_child=4,
    ) as pool:
        futures = {
            pool.submit(_audit_capture, str(path)): path for path in captures
        }
        completed = 0
        for future in as_completed(futures):
            path = futures[future]
            completed += 1
            try:
                reports.append(future.result())
            except Exception as exc:  # audit must preserve partial evidence
                errors.append({"capture": str(path), "error": repr(exc)})
            if completed % 10 == 0 or completed == len(captures):
                print(
                    f"audited {completed}/{len(captures)} captures; errors={len(errors)}",
                    flush=True,
                )

    reports.sort(key=lambda row: row["capture"])
    result = _build_result(
        resource_root=resource_root,
        capture_root=capture_root,
        captures=captures,
        reports=reports,
        errors=errors,
    )
    json_output = args.json_output.resolve()
    markdown_output = args.markdown_output.resolve()
    allowed = {DEFAULT_JSON.resolve(), DEFAULT_MARKDOWN.resolve()}
    if {json_output, markdown_output} != allowed:
        raise SystemExit(
            "outputs are intentionally restricted to the two approved audit paths"
        )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_output.write_text(_render_markdown(result), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
