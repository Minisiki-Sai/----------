from __future__ import annotations

import argparse
import bisect
import json
import sys
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
    decode_v576_damage_object_registration,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


@dataclass(slots=True)
class RegistrationRow:
    capture: str
    timestamp: float
    actor_id: int
    object_id: int
    template_id: int
    resource_id: int
    child_sequence: int
    body: bytes


@dataclass(slots=True)
class ServerRow:
    capture: str
    timestamp: float
    signature: str
    length: int
    phase: int
    body: bytes


def _offsets(body: bytes, token: bytes) -> list[int]:
    found: list[int] = []
    offset = body.find(token, 9)
    while offset >= 0:
        found.append(offset)
        offset = body.find(token, offset + 1)
    return found


def _fields(
    body: bytes,
    width: int,
    *,
    excluded_ranges: tuple[tuple[int, int], ...] = (),
) -> dict[int, int]:
    result: dict[int, int] = {}
    maximum = (1 << (width * 8)) - 1
    for offset in range(9, len(body) - width + 1):
        if any(
            offset < end and offset + width > start
            for start, end in excluded_ranges
        ):
            continue
        value = int.from_bytes(body[offset : offset + width], "little")
        if value in {0, 1, maximum}:
            continue
        if width >= 2 and value <= 0xFF:
            continue
        result[offset] = value
    return result


def collect(path: Path) -> tuple[list[RegistrationRow], list[ServerRow]]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    registrations: list[RegistrationRow] = []
    server_rows: list[ServerRow] = []

    def feed_events(packet):
        payload = bytes(packet.payload)
        direction = packet.inferred_direction()
        registration = decode_v576_damage_object_registration(
            payload, direction
        )
        if registration is not None:
            registrations.append(
                RegistrationRow(
                    capture=str(path),
                    timestamp=float(packet.timestamp_seconds),
                    actor_id=registration.source_actor_id,
                    object_id=registration.damage_object_id,
                    template_id=registration.projectile_template_id,
                    resource_id=registration.effect_resource_id,
                    child_sequence=registration.child_sequence,
                    body=_decode_v576_body(payload, registration.xor_phase),
                )
            )
        if direction == "server_to_client":
            signature = payload[5:9].hex() if len(payload) >= 9 else ""
            for phase in _v576_candidate_xor_phases(payload):
                body = _decode_v576_body(payload, phase)
                # Damage/action/status packets cannot be the missing object
                # creation bridge. Keeping them creates many tautological
                # actor/object matches and hides the structural candidates.
                if any(
                    marker in body
                    for marker in (
                        b"\x08\x73\x00",
                        b"\x08\x77\x00",
                        b"\x0e\x23\x00",
                        b"\x0e\x1f\x00",
                        b"\x12\x24\x00",
                        b"\x12\x20\x00",
                        b"\x13\x4a\x00",
                        b"\x13\x4e\x00",
                        b"\x13\x47\x00",
                    )
                ):
                    continue
                server_rows.append(
                    ServerRow(
                        capture=str(path),
                        timestamp=float(packet.timestamp_seconds),
                        signature=signature,
                        length=len(payload),
                        phase=phase,
                        body=body,
                    )
                )
        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()
    return registrations, server_rows


def audit(captures: list[Path], window_ms: int) -> dict[str, Any]:
    registrations: list[RegistrationRow] = []
    server_by_capture: dict[str, list[ServerRow]] = defaultdict(list)
    for path in captures:
        capture_regs, server_rows = collect(path)
        registrations.extend(capture_regs)
        server_by_capture[str(path)].extend(server_rows)

    support: Counter[tuple[str, int, int, int, int, int]] = Counter()
    values: dict[tuple[str, int, int, int, int, int], set[int]] = defaultdict(set)
    templates: dict[
        tuple[str, int, int, int, int, int], Counter[int]
    ] = defaultdict(Counter)
    captures_seen: dict[
        tuple[str, int, int, int, int, int], set[str]
    ] = defaultdict(set)
    examples: dict[
        tuple[str, int, int, int, int, int], list[dict[str, Any]]
    ] = defaultdict(list)
    matched_registrations: set[tuple[str, float, int, int]] = set()
    max_age = max(1, window_ms) / 1000.0
    server_timestamps = {
        capture: [row.timestamp for row in rows]
        for capture, rows in server_by_capture.items()
    }

    for registration in registrations:
        actor_token = registration.actor_id.to_bytes(4, "little")
        object_token = registration.object_id.to_bytes(4, "little")
        ignored = {
            registration.actor_id,
            registration.object_id,
            registration.template_id,
            registration.resource_id,
            registration.child_sequence,
        }
        registration_excluded = (
            (10, 14),
            (19, 23),
            (23, 27),
            (55, 59),
            (59, 63),
            (75, 79),
        )
        registration_fields = {
            width: _fields(
                registration.body,
                width,
                excluded_ranges=registration_excluded,
            )
            for width in (2, 3, 4)
        }
        capture_rows = server_by_capture[registration.capture]
        timestamps = server_timestamps[registration.capture]
        left = bisect.bisect_left(
            timestamps, registration.timestamp - max_age
        )
        right = bisect.bisect_right(
            timestamps, registration.timestamp + max_age
        )
        for server in capture_rows[left:right]:
            age = server.timestamp - registration.timestamp
            if abs(age) > max_age:
                continue
            object_offsets = _offsets(server.body, object_token)
            if not object_offsets:
                continue
            actor_offsets = _offsets(server.body, actor_token)
            if not actor_offsets:
                continue
            matched_registrations.add(
                (
                    registration.capture,
                    registration.timestamp,
                    registration.actor_id,
                    registration.object_id,
                )
            )
            for width in (2, 3, 4):
                excluded_server_ranges = tuple(
                    (offset, offset + 4)
                    for offset in (*actor_offsets, *object_offsets)
                )
                server_fields = _fields(
                    server.body,
                    width,
                    excluded_ranges=excluded_server_ranges,
                )
                server_by_value: dict[int, list[int]] = defaultdict(list)
                for offset, value in server_fields.items():
                    if value not in ignored:
                        server_by_value[value].append(offset)
                for registration_offset, value in registration_fields[
                    width
                ].items():
                    if value in ignored:
                        continue
                    for server_offset in server_by_value.get(value, ()):
                        key = (
                            server.signature,
                            server.length,
                            server.phase,
                            width,
                            registration_offset,
                            server_offset,
                        )
                        support[key] += 1
                        values[key].add(value)
                        templates[key][registration.template_id] += 1
                        captures_seen[key].add(registration.capture)
                        bucket = examples[key]
                        if len(bucket) < 4:
                            bucket.append(
                                {
                                    "capture": registration.capture,
                                    "delta_ms": round(age * 1000.0, 3),
                                    "actor_id": registration.actor_id,
                                    "object_id": registration.object_id,
                                    "template_id": registration.template_id,
                                    "value": value,
                                }
                            )

    candidates: list[dict[str, Any]] = []
    for key, count in support.items():
        distinct = len(values[key])
        if count < 3 or distinct < 3:
            continue
        signature, length, phase, width, reg_offset, server_offset = key
        candidates.append(
            {
                "signature": signature,
                "payload_length": length,
                "phase": phase,
                "width": width,
                "registration_offset": reg_offset,
                "server_offset": server_offset,
                "matches": count,
                "distinct_values": distinct,
                "captures": len(captures_seen[key]),
                "templates": dict(templates[key].most_common()),
                "examples": examples[key],
            }
        )
    candidates.sort(
        key=lambda row: (
            -int(row["captures"]),
            -int(row["distinct_values"]),
            -int(row["matches"]),
        )
    )
    return {
        "schema_version": 1,
        "captures": len(captures),
        "registrations": len(registrations),
        "matched_registrations": len(matched_registrations),
        "window_ms": window_ms,
        "candidate_count": len(candidates),
        "candidates": candidates[:500],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Search for a nontrivial token copied from the known client "
            "damage-object registration into a server object broadcast."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--window-ms", type=int, default=250)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(
        [path.resolve() for path in args.captures],
        max(1, args.window_ms),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
