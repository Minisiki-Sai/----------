from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, deque
from dataclasses import asdict, dataclass
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
class Registration:
    timestamp: float
    source_actor_id: int
    damage_object_id: int
    projectile_template_id: int
    effect_resource_id: int
    child_sequence: int


def _all_offsets(body: bytes, token: bytes) -> tuple[int, ...]:
    found: list[int] = []
    offset = body.find(token)
    while offset >= 0:
        found.append(offset)
        offset = body.find(token, offset + 1)
    return tuple(found)


def audit_capture(path: Path, window_ms: int) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    pending: deque[Registration] = deque()
    recent_server_packets: deque[tuple[float, str, int, int, bytes]] = deque()
    registrations = 0
    packet_shapes: Counter[tuple[str, int, str]] = Counter()
    match_counts: Counter[tuple[str, int, int, int, int, int, str]] = Counter()
    matched_registrations: set[tuple[float, int, int, int]] = set()
    examples: dict[tuple[str, int, int, int, int, int, str], list[dict[str, Any]]] = {}
    max_age = max(1, window_ms) / 1000.0

    def record_matches(
        *,
        timestamp: float,
        signature: str,
        payload_length: int,
        phase: int,
        body: bytes,
        rows: tuple[Registration, ...],
    ) -> None:
        for row in rows:
            actor_offsets = _all_offsets(
                body, row.source_actor_id.to_bytes(4, "little")
            )
            object_offsets = _all_offsets(
                body, row.damage_object_id.to_bytes(4, "little")
            )
            template_offsets = _all_offsets(
                body, row.projectile_template_id.to_bytes(4, "little")
            )
            resource_offsets = _all_offsets(
                body, row.effect_resource_id.to_bytes(2, "little")
            )
            if not object_offsets:
                continue
            evidence = (
                "actor+object+template"
                if actor_offsets and template_offsets
                else "actor+object+resource"
                if actor_offsets and resource_offsets
                else "object+template"
                if template_offsets
                else "object+resource"
                if resource_offsets
                else "actor+object"
                if actor_offsets
                else "object-only"
            )
            record_kind = (
                "direct37"
                if b"\x08\x73\x00" in body
                else "direct41"
                if b"\x08\x77\x00" in body
                else "action"
                if any(
                    marker in body
                    for marker in (
                        b"\x0e\x23\x00",
                        b"\x0e\x1f\x00",
                        b"\x12\x24\x00",
                        b"\x12\x20\x00",
                    )
                )
                else "status"
                if any(
                    marker in body
                    for marker in (
                        b"\x13\x4a\x00",
                        b"\x13\x4e\x00",
                        b"\x13\x47\x00",
                    )
                )
                else "other"
            )
            key = (
                signature,
                payload_length,
                phase,
                actor_offsets[0] if actor_offsets else -1,
                object_offsets[0],
                template_offsets[0] if template_offsets else -1,
                resource_offsets[0] if resource_offsets else -1,
                evidence,
                record_kind,
            )
            match_counts[key] += 1
            reg_key = (
                row.timestamp,
                row.source_actor_id,
                row.damage_object_id,
                row.projectile_template_id,
            )
            matched_registrations.add(reg_key)
            bucket = examples.setdefault(key, [])
            if len(bucket) < 3:
                bucket.append(
                    {
                        "delta_ms": round(
                            (timestamp - row.timestamp) * 1000.0, 3
                        ),
                        "source_actor_id": row.source_actor_id,
                        "damage_object_id": row.damage_object_id,
                        "projectile_template_id": row.projectile_template_id,
                        "effect_resource_id": row.effect_resource_id,
                        "child_sequence": row.child_sequence,
                        "body_hex": body.hex(),
                    }
                )

    def feed_events(packet):
        nonlocal registrations
        timestamp = float(packet.timestamp_seconds)
        direction = packet.inferred_direction()
        payload = bytes(packet.payload)
        signature = payload[5:9].hex() if len(payload) >= 9 else ""
        packet_shapes[(str(direction), len(payload), signature)] += 1

        registration = decode_v576_damage_object_registration(
            payload, direction
        )
        if registration is not None:
            registrations += 1
            row = Registration(
                timestamp=timestamp,
                source_actor_id=registration.source_actor_id,
                damage_object_id=registration.damage_object_id,
                projectile_template_id=registration.projectile_template_id,
                effect_resource_id=registration.effect_resource_id,
                child_sequence=registration.child_sequence,
            )
            pending.append(row)
            for (
                server_timestamp,
                server_signature,
                server_length,
                server_phase,
                server_body,
            ) in recent_server_packets:
                record_matches(
                    timestamp=server_timestamp,
                    signature=server_signature,
                    payload_length=server_length,
                    phase=server_phase,
                    body=server_body,
                    rows=(row,),
                )

        while pending and timestamp - pending[0].timestamp > max_age:
            pending.popleft()
        while (
            recent_server_packets
            and timestamp - recent_server_packets[0][0] > max_age
        ):
            recent_server_packets.popleft()

        if direction == "server_to_client":
            for phase in _v576_candidate_xor_phases(payload):
                body = _decode_v576_body(payload, phase)
                if pending:
                    record_matches(
                        timestamp=timestamp,
                        signature=signature,
                        payload_length=len(payload),
                        phase=phase,
                        body=body,
                        rows=tuple(pending),
                    )
                recent_server_packets.append(
                    (timestamp, signature, len(payload), phase, body)
                )

        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    candidates = []
    for key, count in match_counts.most_common():
        (
            signature,
            length,
            phase,
            actor_offset,
            object_offset,
            template_offset,
            resource_offset,
            evidence,
            record_kind,
        ) = key
        candidates.append(
            {
                "signature": signature,
                "payload_length": length,
                "xor_phase": phase,
                "actor_offset": actor_offset,
                "object_offset": object_offset,
                "template_offset": template_offset,
                "resource_offset": resource_offset,
                "evidence": evidence,
                "record_kind": record_kind,
                "matches": count,
                "examples": examples.get(key, ()),
            }
        )
    return {
        "capture": str(path),
        "window_ms": window_ms,
        "registrations": registrations,
        "registrations_with_any_server_object_match": len(matched_registrations),
        "client_107_shapes": [
            {
                "direction": key[0],
                "payload_length": key[1],
                "signature": key[2],
                "count": count,
            }
            for key, count in packet_shapes.most_common()
            if key[0] == "client_to_server" and key[1] == 107
        ],
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find the server broadcast counterpart of the known 107-byte "
            "client damage-object registration."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--window-ms", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [
        audit_capture(path.resolve(), args.window_ms) for path in args.captures
    ]
    result = {
        "schema_version": 1,
        "reports": reports,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
