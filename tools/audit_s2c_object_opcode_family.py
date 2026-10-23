from __future__ import annotations

import argparse
import bisect
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)
from tools.audit_direct_damage_release_gate import _classification  # noqa: E402
from tools.audit_s2c_object_record_table_links import (  # noqa: E402
    load_client_tables,
)


EXACT_METHODS = frozenset(
    {"packet_skill_id", "direct_result_group_link", "direct_result_group_run_link"}
)


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    timestamp: float
    packet_index: int
    record_offset: int
    opcode: int
    owner_actor_id: int
    damage_object_id: int
    projectile_template_id: int


def _actor_id(value: object) -> int:
    text = str(value or "").strip()
    if not text.startswith("0x"):
        return 0
    try:
        return int(text, 16)
    except ValueError:
        return 0


def audit_capture(path: Path, projectile_ids: frozenset[int]) -> dict[str, object]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    records: list[ObjectRecord] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        if packet.inferred_direction() == "server_to_client":
            seen: set[tuple[int, int, int, int]] = set()
            for phase in _v576_candidate_xor_phases(payload):
                body = _decode_v576_body(payload, phase)
                offset = body.find(b"\x09", 9)
                while offset >= 0:
                    if (
                        offset >= 4
                        and offset + 48 <= len(body)
                        and body[offset + 2] == 0
                        and body[offset + 3 : offset + 8] == b"\0" * 5
                    ):
                        owner = int.from_bytes(body[offset - 4 : offset], "little")
                        owner_repeat = int.from_bytes(
                            body[offset + 44 : offset + 48], "little"
                        )
                        object_id = int.from_bytes(
                            body[offset + 8 : offset + 12], "little"
                        )
                        template_id = int.from_bytes(
                            body[offset + 12 : offset + 16], "little"
                        )
                        key = (offset, owner, object_id, template_id)
                        if (
                            owner >= 0x01000000
                            and owner_repeat == owner
                            and 0 < object_id <= 0x00FFFFFF
                            and template_id in projectile_ids
                            and key not in seen
                        ):
                            seen.add(key)
                            records.append(
                                ObjectRecord(
                                    timestamp=float(packet.timestamp_seconds),
                                    packet_index=packet_index,
                                    record_offset=offset,
                                    opcode=int(body[offset + 1]),
                                    owner_actor_id=owner,
                                    damage_object_id=object_id,
                                    projectile_template_id=template_id,
                                )
                            )
                    offset = body.find(b"\x09", offset + 1)
        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        events = source.poll()
    finally:
        source.close()

    by_key: dict[tuple[int, int], list[ObjectRecord]] = defaultdict(list)
    for record in records:
        by_key[(record.owner_actor_id, record.damage_object_id)].append(record)
    for values in by_key.values():
        values.sort(key=lambda row: (row.timestamp, row.packet_index, row.record_offset))

    opcode_records: Counter[int] = Counter(row.opcode for row in records)
    joined_by_opcode: Counter[int] = Counter()
    blocking_by_opcode: Counter[int] = Counter()
    exact_by_opcode: Counter[int] = Counter()
    templates: dict[int, dict[str, object]] = defaultdict(
        lambda: {
            "records": 0,
            "opcodes": Counter(),
            "joined_hits": 0,
            "blocking_hits": 0,
            "exact_hits": 0,
            "exact_skill_ids": Counter(),
            "observed_skill_ids": Counter(),
            "blocking_examples": [],
        }
    )
    origin = float(source.capture_origin_timestamp_seconds or 0.0)
    for record in records:
        row = templates[record.projectile_template_id]
        row["records"] = int(row["records"]) + 1
        row["opcodes"][f"09 {record.opcode:02X}"] += 1
    for event in events:
        if not isinstance(event, CombatEvent) or event.healing:
            continue
        actor = _actor_id(event.source_id)
        object_id = int(event.wire_damage_object_id or 0)
        if actor <= 0 or object_id <= 0:
            continue
        candidates = by_key.get((actor, object_id), ())
        if not candidates:
            continue
        timestamp = origin + event.timestamp_ms / 1000.0
        timestamps = [row.timestamp for row in candidates]
        index = bisect.bisect_right(timestamps, timestamp) - 1
        if index < 0:
            continue
        record = candidates[index]
        row = templates[record.projectile_template_id]
        row["joined_hits"] = int(row["joined_hits"]) + 1
        row["observed_skill_ids"][str(event.skill_id)] += 1
        joined_by_opcode[record.opcode] += 1
        method = str(event.attribution_method or "").strip().casefold()
        if method in EXACT_METHODS:
            row["exact_hits"] = int(row["exact_hits"]) + 1
            row["exact_skill_ids"][str(event.skill_id)] += 1
            exact_by_opcode[record.opcode] += 1
        if _classification(event) == "blocking_unproven_direct_hit":
            row["blocking_hits"] = int(row["blocking_hits"]) + 1
            blocking_by_opcode[record.opcode] += 1
            examples = row["blocking_examples"]
            if len(examples) < 12:
                examples.append(
                    {
                        "hit_seconds": round(timestamp - origin, 6),
                        "record_delta_seconds": round(
                            timestamp - record.timestamp, 6
                        ),
                        "owner_actor_id": f"0x{actor:08X}",
                        "damage_object_id": object_id,
                        "opcode": f"09 {record.opcode:02X}",
                        "skill_id": str(event.skill_id),
                        "skill_name": str(event.skill_name),
                        "method": method,
                    }
                )

    return {
        "capture": str(path),
        "records": len(records),
        "opcode_records": {
            f"09 {key:02X}": value for key, value in opcode_records.most_common()
        },
        "joined_hits_by_opcode": {
            f"09 {key:02X}": value for key, value in joined_by_opcode.most_common()
        },
        "blocking_hits_by_opcode": {
            f"09 {key:02X}": value for key, value in blocking_by_opcode.most_common()
        },
        "exact_hits_by_opcode": {
            f"09 {key:02X}": value for key, value in exact_by_opcode.most_common()
        },
        "templates": [
            {"projectile_template_id": template_id, **row}
            for template_id, row in sorted(
                templates.items(),
                key=lambda item: (-int(item[1]["blocking_hits"]), item[0]),
            )
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--resource-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tables = load_client_tables(args.resource_root.resolve())
    projectile_ids = frozenset(int(value) for value in tables["projectiles"])
    reports = [audit_capture(path.resolve(), projectile_ids) for path in args.captures]
    result = {
        "schema_version": 1,
        "projectile_rows": len(projectile_ids),
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
