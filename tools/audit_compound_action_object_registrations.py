from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dragon_nest_builder.data_loader import DATA_DIR  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


OBJECT_MARKERS = (b"\x09\x5d\x00", b"\x09\x61\x00", b"\x09\x6e\x00")
ACTION_MARKERS = (b"\x0e\x23\x00", b"\x0e\x1f\x00", b"\x12\x24\x00", b"\x12\x20\x00")


@dataclass(frozen=True, slots=True)
class Registration:
    packet_index: int
    timestamp_ms: int
    direction: str
    signature: str
    payload_length: int
    phase: int
    owner_actor_id: int
    object_id: int
    object_marker: str
    object_marker_offset: int
    link_field: int
    skill_id: int
    action_marker: str
    action_marker_offset: int


@dataclass(frozen=True, slots=True)
class DirectHit:
    packet_index: int
    timestamp_ms: int
    actor_id: int
    object_id: int
    skill_id: int
    exact: bool


def _offsets(body: bytes, marker: bytes, start: int = 9):
    offset = body.find(marker, start)
    while offset >= 0:
        yield offset
        offset = body.find(marker, offset + 1)


def collect_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    skills = frozenset(int(value) for value in decoder.damage_skill_ids)
    registrations: list[Registration] = []
    hits: list[DirectHit] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction())
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        signature = payload[5:9].hex() if len(payload) >= 9 else ""
        if direction == "server_to_client":
            for hit in decode_v576_embedded_direct_damage_events(payload, direction, skills):
                actor_id = int(hit.source_actor_id or 0)
                object_id = int(hit.damage_object_id or 0)
                if actor_id > 0 and object_id > 0:
                    hits.append(
                        DirectHit(
                            packet_index=packet_index,
                            timestamp_ms=timestamp_ms,
                            actor_id=actor_id,
                            object_id=object_id,
                            skill_id=int(hit.skill_id or 0),
                            exact=bool(hit.skill_id_exact),
                        )
                    )

        seen: set[tuple[int, int, int, int]] = set()
        for phase in _v576_candidate_xor_phases(payload):
            body = _decode_v576_body(payload, phase)
            actions_by_actor: dict[int, list[tuple[int, bytes, int]]] = defaultdict(list)
            for action_marker in ACTION_MARKERS:
                for action_offset in _offsets(body, action_marker, 14):
                    if action_offset < 4 or action_offset + 7 > len(body):
                        continue
                    marker_actor = int.from_bytes(body[action_offset - 4 : action_offset], "little")
                    header_actor = int.from_bytes(body[10:14], "little") if len(body) >= 14 else 0
                    skill_id = int.from_bytes(body[action_offset + 3 : action_offset + 7], "little")
                    if skill_id not in skills:
                        continue
                    for actor_id in {marker_actor, header_actor}:
                        if actor_id >= 0x01000000:
                            actions_by_actor[actor_id].append((skill_id, action_marker, action_offset))

            for object_marker in OBJECT_MARKERS:
                for object_offset in _offsets(body, object_marker):
                    if object_offset < 4 or object_offset + 16 > len(body):
                        continue
                    if body[object_offset + 3 : object_offset + 8] != b"\0" * 5:
                        continue
                    owner = int.from_bytes(body[object_offset - 4 : object_offset], "little")
                    object_id = int.from_bytes(body[object_offset + 8 : object_offset + 12], "little")
                    link_field = int.from_bytes(body[object_offset + 12 : object_offset + 16], "little")
                    if owner < 0x01000000 or not 1 <= object_id <= 0x00FFFFFF:
                        continue
                    actor_actions = actions_by_actor.get(owner, ())
                    identities = {skill_id for skill_id, _marker, _offset in actor_actions}
                    if len(identities) != 1:
                        continue
                    skill_id = next(iter(identities))
                    nearest = min(
                        (row for row in actor_actions if row[0] == skill_id),
                        key=lambda row: abs(row[2] - object_offset),
                    )
                    key = (phase, owner, object_id, skill_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    registrations.append(
                        Registration(
                            packet_index=packet_index,
                            timestamp_ms=timestamp_ms,
                            direction=direction,
                            signature=signature,
                            payload_length=len(payload),
                            phase=phase,
                            owner_actor_id=owner,
                            object_id=object_id,
                            object_marker=object_marker.hex(" ").upper(),
                            object_marker_offset=object_offset,
                            link_field=link_field,
                            skill_id=skill_id,
                            action_marker=nearest[1].hex(" ").upper(),
                            action_marker_offset=nearest[2],
                        )
                    )
        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    by_key: dict[tuple[int, int], list[Registration]] = defaultdict(list)
    for row in registrations:
        by_key[(row.owner_actor_id, row.object_id)].append(row)
    for rows in by_key.values():
        rows.sort(key=lambda row: (row.packet_index, row.object_marker_offset))

    joined = 0
    exact_joined = 0
    exact_matches = 0
    exact_conflicts = 0
    nonexact_joined = 0
    lifecycle_conflicts = 0
    skills = Counter()
    shapes: dict[tuple[str, str, int, str, int, str, int], Counter[str]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []
    for key, rows in by_key.items():
        if len({row.skill_id for row in rows}) > 1:
            lifecycle_conflicts += 1
    for hit in hits:
        candidates = by_key.get((hit.actor_id, hit.object_id), ())
        positions = [row.packet_index for row in candidates]
        index = bisect_right(positions, hit.packet_index) - 1
        if index < 0:
            continue
        registration = candidates[index]
        joined += 1
        skills[registration.skill_id] += 1
        shape = (
            registration.direction,
            registration.signature,
            registration.payload_length,
            registration.object_marker,
            registration.object_marker_offset,
            registration.action_marker,
            registration.action_marker_offset,
        )
        if hit.exact:
            exact_joined += 1
            match = registration.skill_id == hit.skill_id
            exact_matches += int(match)
            exact_conflicts += int(not match)
            shapes[shape]["support" if match else "conflict"] += 1
        else:
            nonexact_joined += 1
        if len(examples) < 80 and (hit.exact or len(examples) < 30):
            examples.append(
                {
                    "hit_timestamp_ms": hit.timestamp_ms,
                    "actor_id": hit.actor_id,
                    "object_id": hit.object_id,
                    "truth_skill_id": hit.skill_id,
                    "truth_exact": hit.exact,
                    "candidate_skill_id": registration.skill_id,
                    "registration_delta_ms": registration.timestamp_ms - hit.timestamp_ms,
                    "registration_packet_delta": registration.packet_index - hit.packet_index,
                    "direction": registration.direction,
                    "signature": registration.signature,
                    "payload_length": registration.payload_length,
                    "object_marker": registration.object_marker,
                    "object_marker_offset": registration.object_marker_offset,
                    "link_field": registration.link_field,
                    "action_marker": registration.action_marker,
                    "action_marker_offset": registration.action_marker_offset,
                }
            )

    shape_rows = [
        {
            "direction": key[0],
            "signature": key[1],
            "payload_length": key[2],
            "object_marker": key[3],
            "object_marker_offset": key[4],
            "action_marker": key[5],
            "action_marker_offset": key[6],
            "support": int(counts["support"]),
            "conflict": int(counts["conflict"]),
        }
        for key, counts in shapes.items()
    ]
    shape_rows.sort(key=lambda row: (int(row["conflict"]), -int(row["support"])))
    return {
        "capture": str(path),
        "registrations": len(registrations),
        "registration_keys": len(by_key),
        "lifecycle_skill_conflicts": lifecycle_conflicts,
        "direct37_rows": len(hits),
        "joined_rows": joined,
        "exact_joined_rows": exact_joined,
        "exact_matches": exact_matches,
        "exact_conflicts": exact_conflicts,
        "nonexact_joined_rows": nonexact_joined,
        "joined_skill_counts": {str(k): v for k, v in skills.most_common()},
        "shape_validation": shape_rows,
        "examples": examples,
    }


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "registrations",
        "registration_keys",
        "lifecycle_skill_conflicts",
        "direct37_rows",
        "joined_rows",
        "exact_joined_rows",
        "exact_matches",
        "exact_conflicts",
        "nonexact_joined_rows",
    )
    totals = {field: sum(int(row[field]) for row in reports) for field in fields}
    totals["exact_precision_percent"] = round(
        totals["exact_matches"] / max(1, totals["exact_joined_rows"]) * 100.0,
        8,
    )
    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [collect_capture(path.resolve()) for path in args.captures]
    result = {
        "schema_version": 1,
        "policy": (
            "The object and one catalogued damage action must share owner and decoded "
            "message/phase. Fixed-slot direct37 skill IDs are the only hit truth."
        ),
        "summary": summarize(reports),
        "reports": reports,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
