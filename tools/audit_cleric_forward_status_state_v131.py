"""Audit result-record fields for Cleric state-owned damage.

This is an evidence-only forward-chain audit.  It starts from decoded skill
and status events, then inspects the exact physical direct37/direct41 record
that produced each CombatEvent.  It does not use elapsed-time proximity,
damage amount, hit cadence, or the current display name as a new label.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent, SkillParameterEvent  # noqa: E402
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402
from dragon_nest_builder.live_dps.udp_damage_records import (  # noqa: E402
    inspect_udp_damage_records,
)


JOBS = frozenset({"圣骑士", "十字军"})
FOCUS_SKILLS = frozenset({"3202", "3205", "periodic:shock", "-1"})
FOCUS_METHODS = frozenset(
    {
        "periodic_status",
        "periodic_status_packet",
        "refreshable_pulsing_aura",
        "cast_instance_match",
        "continuation_window",
        "latest_profiled_direct_cast",
        "learned_cast_profile",
        "unresolved",
    }
)


def _physical_fields(packet: object, event: CombatEvent) -> dict[str, Any] | None:
    inventory = inspect_udp_damage_records(
        bytes(getattr(packet, "payload", b"")),
        str(packet.inferred_direction()),
    )
    if not inventory.complete:
        return None
    matches = [
        row
        for row in inventory.damage_records
        if row.admissible_fields
        and row.record_offset == int(event.wire_record_offset)
        and row.source_actor_id == int(event.wire_source_actor_id)
        and row.target_actor_id == int(event.wire_target_actor_id)
        and row.damage == int(event.amount)
        and row.critical == bool(event.critical)
    ]
    if len(matches) != 1:
        return None
    row = matches[0]
    raw = row.record_bytes
    common_cc_offset = 104 if row.family == "direct41" else 100
    source_kind = int.from_bytes(raw[11:12], "little", signed=True)
    common_cc = int(raw[common_cc_offset]) if len(raw) > common_cc_offset else -1

    def u32(marker_relative_offset: int) -> int:
        start = 4 + marker_relative_offset
        return (
            int.from_bytes(raw[start : start + 4], "little")
            if 0 <= start and start + 4 <= len(raw)
            else 0
        )

    return {
        "family": row.family,
        "record_type": f"08/{row.declared_length:02X}",
        "source_kind": source_kind,
        "common_cc": common_cc,
        "result_flags": int(row.result_flags),
        "primary90": u32(102 if row.family == "direct37" else 106),
        "fallbackA4": u32(114 if row.family == "direct37" else 118),
        "damage_object_id": int(row.damage_object_id or 0),
        "opaque_object_context": row.opaque_object_context.hex(),
        "wire_record_sha256": row.wire_record_sha256,
    }


def audit(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original = decoder.feed_events
    rows: Counter[tuple[Any, ...]] = Counter()
    statuses: Counter[tuple[Any, ...]] = Counter()
    samples: list[dict[str, Any]] = []

    def feed_events(packet: object) -> Iterable[object]:
        emitted = tuple(original(packet))
        signature = bytes(getattr(packet, "payload", b""))[5:9].hex()
        for event in emitted:
            if isinstance(event, SkillParameterEvent) and event.skill_id in {"3202", "3205"}:
                statuses[
                    (
                        event.skill_id,
                        int(event.effect_class_id),
                        event.parameters,
                        int(event.duration_ms),
                        event.source_id == event.target_id,
                    )
                ] += 1
                continue
            if not isinstance(event, CombatEvent) or event.healing or event.job not in JOBS:
                continue
            if event.skill_id not in FOCUS_SKILLS and event.attribution_method not in FOCUS_METHODS:
                continue
            fields = _physical_fields(packet, event)
            if fields is None:
                continue
            key = (
                event.job,
                event.skill_id,
                event.attribution_method,
                fields["family"],
                fields["record_type"],
                len(bytes(getattr(packet, "payload", b""))),
                signature,
                fields["source_kind"],
                fields["common_cc"],
                fields["result_flags"],
                fields["primary90"],
                fields["fallbackA4"],
                bool(fields["damage_object_id"]),
                fields["opaque_object_context"],
            )
            rows[key] += 1
            if len(samples) < 120:
                samples.append(
                    {
                        "timestamp_ms": int(event.timestamp_ms),
                        "job": event.job,
                        "skill_id": event.skill_id,
                        "skill_name": event.skill_name,
                        "method": event.attribution_method,
                        "packet_length": len(bytes(getattr(packet, "payload", b""))),
                        "signature": signature,
                        **fields,
                    }
                )
        return emitted

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    names = (
        "job",
        "skill_id",
        "method",
        "family",
        "record_type",
        "packet_length",
        "signature",
        "source_kind",
        "common_cc",
        "result_flags",
        "primary90",
        "fallbackA4",
        "has_damage_object_id",
        "opaque_object_context",
    )
    return {
        "capture": str(path),
        "statuses": [
            {
                "skill_id": key[0],
                "effect_class": key[1],
                "parameters": key[2],
                "duration_ms": key[3],
                "self_target": key[4],
                "count": count,
            }
            for key, count in statuses.most_common()
        ],
        "damage_result_fields": [
            {**dict(zip(names, key)), "hits": count}
            for key, count in rows.most_common()
        ],
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "schema": "cleric-forward-status-state-v131",
        "policy": (
            "Action/status instance first; exact physical result fields only. "
            "No time, amount, cadence, or old-label promotion."
        ),
        "captures": [audit(path.resolve()) for path in args.captures],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
