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
from dragon_nest_builder.live_dps.damage_object_origins import (  # noqa: E402
    _SPEC_BY_PROJECTILE_TEMPLATE,
)
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


MARKERS = (
    b"\x09\x5d\x00",
    b"\x09\x61\x00",
    b"\x09\x62\x00",
    b"\x09\x6e\x00",
)
AUDIT_ONLY_TEMPLATE_SKILLS = {3021: frozenset({2207})}


def _mapped_skill_ids(template_id: int) -> frozenset[int]:
    spec = _SPEC_BY_PROJECTILE_TEMPLATE.get(template_id)
    if spec is not None:
        return frozenset({spec.canonical_skill_id, *spec.skill_ids})
    return AUDIT_ONLY_TEMPLATE_SKILLS.get(template_id, frozenset())


@dataclass(frozen=True, slots=True)
class Registration:
    packet_index: int
    timestamp_ms: int
    direction: str
    owner_actor_id: int
    object_id: int
    projectile_template_id: int
    marker: str


@dataclass(frozen=True, slots=True)
class Hit:
    packet_index: int
    timestamp_ms: int
    actor_id: int
    object_id: int
    skill_id: int
    exact: bool


def _offsets(body: bytes, marker: bytes):
    offset = body.find(marker, 9)
    while offset >= 0:
        yield offset
        offset = body.find(marker, offset + 1)


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    skill_ids = frozenset(int(value) for value in decoder.damage_skill_ids)
    registrations: list[Registration] = []
    hits: list[Hit] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction())
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        if direction == "server_to_client":
            for hit in decode_v576_embedded_direct_damage_events(
                payload, direction, skill_ids
            ):
                actor_id = int(hit.source_actor_id or 0)
                object_id = int(hit.damage_object_id or 0)
                if actor_id > 0 and object_id > 0:
                    hits.append(
                        Hit(
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
            for marker in MARKERS:
                for offset in _offsets(body, marker):
                    if offset < 4 or offset + 48 > len(body):
                        continue
                    owner = int.from_bytes(body[offset - 4 : offset], "little")
                    repeated_owner = int.from_bytes(
                        body[offset + 44 : offset + 48], "little"
                    )
                    object_id = int.from_bytes(
                        body[offset + 8 : offset + 12], "little"
                    )
                    template_id = int.from_bytes(
                        body[offset + 12 : offset + 16], "little"
                    )
                    identity = (phase, owner, object_id, offset)
                    if (
                        identity in seen
                        or owner < 0x01000000
                        or repeated_owner != owner
                        or body[offset + 3 : offset + 8] != b"\0" * 5
                        or not 0 < object_id <= 0x00FFFFFF
                        or not _mapped_skill_ids(template_id)
                    ):
                        continue
                    seen.add(identity)
                    registrations.append(
                        Registration(
                            packet_index=packet_index,
                            timestamp_ms=timestamp_ms,
                            direction=direction,
                            owner_actor_id=owner,
                            object_id=object_id,
                            projectile_template_id=template_id,
                            marker=marker.hex(),
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
        rows.sort(key=lambda row: row.packet_index)

    direction_counts = Counter(row.direction for row in registrations)
    exact = Counter()
    joined = Counter()
    template_truth: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []
    for hit in hits:
        candidates = by_key.get((hit.actor_id, hit.object_id), ())
        positions = [row.packet_index for row in candidates]
        index = bisect_right(positions, hit.packet_index) - 1
        if index < 0:
            continue
        registration = candidates[index]
        if hit.timestamp_ms - registration.timestamp_ms > 120_000:
            continue
        mapped_skills = _mapped_skill_ids(
            registration.projectile_template_id
        )
        joined[registration.direction] += 1
        if not hit.exact:
            continue
        exact[registration.direction] += 1
        template_truth[
            (registration.direction, registration.projectile_template_id)
        ][hit.skill_id] += 1
        match = hit.skill_id in mapped_skills
        exact[f"{registration.direction}|support" if match else f"{registration.direction}|conflict"] += 1
        if not match and len(examples) < 80:
            examples.append(
                {
                    "timestamp_ms": hit.timestamp_ms,
                    "actor_id": hit.actor_id,
                    "object_id": hit.object_id,
                    "direction": registration.direction,
                    "marker": registration.marker,
                    "projectile_template_id": registration.projectile_template_id,
                    "mapped_skill_ids": sorted(mapped_skills),
                    "exact_skill_id": hit.skill_id,
                    "registration_delta_ms": (
                        registration.timestamp_ms - hit.timestamp_ms
                    ),
                }
            )

    return {
        "capture": str(path),
        "registrations": len(registrations),
        "registrations_by_direction": dict(direction_counts),
        "joined_hits_by_direction": dict(joined),
        "exact_validation": dict(exact),
        "template_truth": [
            {
                "direction": key[0],
                "projectile_template_id": key[1],
                "skills": {str(k): v for k, v in values.most_common()},
            }
            for key, values in sorted(template_truth.items())
        ],
        "conflict_examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path.resolve()) for path in args.captures]
    totals = Counter()
    for report in reports:
        totals["registrations"] += int(report["registrations"])
        for key, value in report["registrations_by_direction"].items():
            totals[f"registrations|{key}"] += int(value)
        for key, value in report["joined_hits_by_direction"].items():
            totals[f"joined|{key}"] += int(value)
        for key, value in report["exact_validation"].items():
            totals[f"exact|{key}"] += int(value)
    result = {
        "schema_version": 1,
        "policy": (
            "Validate complete owner/object/repeated-owner object records in "
            "both inferred directions against fixed-slot direct37 skill truth."
        ),
        "summary": dict(totals),
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
