"""Audit raw player hits whose physical target is a training puppet.

The game can apply a puppet hit without emitting a visible puppet damage row.
This report therefore reads the typed UDP records directly, retaining zero-
damage records and object/result identifiers.  Nearby skill casts are emitted
as diagnostics only; they never rename a V2 combat event.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.capture_sources import (  # noqa: E402
    IDENTITY_METADATA_ADAPTER_INDEX,
    IDENTITY_TRACE_ADAPTER_INDEX,
    SESSION_EVIDENCE_ADAPTER_INDEX,
    V576DiagnosticReplaySource,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.models import SkillCastEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    V576TransportReassembler,
)
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


_FRAME_HEADER = struct.Struct("<dBII")
_PUPPET_TEMPLATES = frozenset({33500, 33501})
_NEAR_CAST_MS = 1500


def _skill_name(decoder: object, skill_id: int) -> str:
    row = getattr(decoder, "skill_catalog", {}).get(int(skill_id))
    if isinstance(row, tuple) and row:
        return str(row[0])
    return ""


def _root_facts(decoder: object, owner: int, object_id: int) -> dict[str, int]:
    rows = getattr(getattr(decoder, "player_root_action_objects", None), "_generations", {})
    generations = rows.get((int(owner), int(object_id)), ())
    if not generations:
        return {}
    row = max(generations, key=lambda value: (float(value.timestamp), int(value.generation)))
    return {
        "generation": int(row.generation),
        "skill_id": int(row.skill_id),
        "config_row": int(row.config_row),
        "action_index": int(row.action_index),
        "signal_key": int(row.signal_key),
    }


def _owner_facts(decoder: object, source_actor_id: int) -> dict[str, int | str]:
    """Return exact owner evidence for a raw source actor, for audit only.

    Short entity IDs are resolved only from the replay's birth + explicit
    entity-owner binding.  A player source is self-owned.  Cached/proximity
    summon mappings are intentionally excluded so this report cannot turn a
    weak owner guess into a puppet-hit count.
    """
    source_actor_id = int(source_actor_id)
    evidence = getattr(decoder, "entity_owner_evidence", None)
    binding = getattr(evidence, "bindings", {}).get(source_actor_id)
    if binding is not None:
        return {
            "owner_actor_id": int(binding.record.owner_id),
            "owner_evidence": "entity_owner_record",
            "entity_template_id": int(binding.template_id),
            "entity_generation": int(binding.generation),
        }
    if source_actor_id > 0x1000000:
        return {
            "owner_actor_id": source_actor_id,
            "owner_evidence": "player_source",
        }
    return {
        "owner_actor_id": 0,
        "owner_evidence": "unresolved_source",
    }


def analyze(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    events = source.poll()
    decoder = source._decoder
    origin = float(decoder.first_timestamp or 0.0)
    puppet_actor_ids = {
        int(actor_id)
        for actor_id, template in decoder.monster_entities.items()
        if int(getattr(template, "template_id", 0) or 0) in _PUPPET_TEMPLATES
    }
    casts = [
        event
        for event in events
        if isinstance(event, SkillCastEvent)
    ]

    rows: list[dict[str, Any]] = []
    transport = V576TransportReassembler()
    with path.open("rb") as handle:
        if handle.read(len(V576DiagnosticReplaySource._MAGIC)) != V576DiagnosticReplaySource._MAGIC:
            raise ValueError("不是有效的 Dragon Nest .dpscap 统计记录")
        while True:
            header = handle.read(_FRAME_HEADER.size)
            if not header or len(header) != _FRAME_HEADER.size:
                break
            timestamp, adapter, original_length, captured_length = _FRAME_HEADER.unpack(header)
            frame = handle.read(captured_length)
            if len(frame) != captured_length:
                break
            if adapter in {
                IDENTITY_TRACE_ADAPTER_INDEX,
                IDENTITY_METADATA_ADAPTER_INDEX,
                SESSION_EVIDENCE_ADAPTER_INDEX,
            }:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical in transport.feed(packet):
                if str(logical.transport).upper() != "UDP":
                    continue
                if logical.inferred_direction() != "server_to_client":
                    continue
                inventory = inspect_udp_damage_records(
                    logical.payload, logical.inferred_direction()
                )
                if not inventory.complete:
                    continue
                relative_ms = round((logical.timestamp_seconds - origin) * 1000)
                for record in inventory.damage_records:
                    if record.target_actor_id not in puppet_actor_ids:
                        continue
                    candidates = sorted(
                        (
                            cast
                            for cast in casts
                            if cast.source_id == f"0x{record.source_actor_id:08X}"
                            and abs(cast.timestamp_ms - relative_ms) <= _NEAR_CAST_MS
                        ),
                        key=lambda cast: abs(cast.timestamp_ms - relative_ms),
                    )[:8]
                    root = _root_facts(
                        decoder,
                        record.source_actor_id,
                        int(record.damage_object_id or 0),
                    )
                    owner = _owner_facts(decoder, record.source_actor_id)
                    rows.append(
                        {
                            "timestamp_ms": int(relative_ms),
                            "source_actor_id": int(record.source_actor_id),
                            "target_actor_id": int(record.target_actor_id),
                            "target_template_id": int(
                                getattr(
                                    decoder.monster_entities.get(record.target_actor_id),
                                    "template_id",
                                    0,
                                )
                                or 0
                            ),
                            "family": record.family,
                            "damage": int(record.damage),
                            "critical": bool(record.critical),
                            "damage_object_id": (
                                int(record.damage_object_id)
                                if record.damage_object_id is not None
                                else None
                            ),
                            "result_group_id": int(record.damage_result_group_id),
                            "record_offset": int(record.record_offset),
                            "wire_sha256": record.wire_sha256,
                            "root": root,
                            "owner": owner,
                            # These are deliberately diagnostic candidates.
                            "nearby_casts": [
                                {
                                    "timestamp_ms": int(cast.timestamp_ms),
                                    "skill_id": str(cast.skill_id),
                                    "skill_name": cast.skill_name,
                                    "delta_ms": int(cast.timestamp_ms - relative_ms),
                                }
                                for cast in candidates
                            ],
                        }
                    )

    owner_record_counts = Counter(
        int(row["owner"]["owner_actor_id"])
        for row in rows
    )
    return {
        "schema": "puppet-target-hit-audit-v1",
        "policy": "raw target facts plus diagnostic nearby casts; no relabeling",
        "capture": str(path),
        "puppet_templates": sorted(_PUPPET_TEMPLATES),
        "puppet_actor_ids": sorted(puppet_actor_ids),
        "raw_puppet_target_records": len(rows),
        "zero_damage_records": sum(row["damage"] == 0 for row in rows),
        "owner_record_counts": {
            str(owner_actor_id): count
            for owner_actor_id, count in sorted(owner_record_counts.items())
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = analyze(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    print(
        "puppet_records",
        report["raw_puppet_target_records"],
        "zero_damage",
        report["zero_damage_records"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
