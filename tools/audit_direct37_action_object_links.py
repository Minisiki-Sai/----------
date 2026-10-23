from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
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
    decode_v576_structured_actions,
)
from dragon_nest_builder.live_dps.sources import V576DiagnosticReplaySource  # noqa: E402


@dataclass(frozen=True, slots=True)
class PacketRow:
    packet_index: int
    timestamp_ms: int
    direction: str
    signature: str
    payload_length: int
    actions: tuple[tuple[int, int], ...]
    token_offsets_by_phase: tuple[bytes, ...]
    direct_marker_ranges_by_phase: tuple[tuple[tuple[int, int], ...], ...]


@dataclass(frozen=True, slots=True)
class DirectRow:
    packet_index: int
    timestamp_ms: int
    source_actor_id: int
    object_id: int
    skill_id: int
    exact: bool


def _direct_ranges(body: bytes) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    for marker in (b"\x08\x73\x00", b"\x08\x77\x00"):
        offset = body.find(marker, 9)
        while offset >= 0:
            ranges.append((max(0, offset - 4), min(len(body), offset + 43)))
            offset = body.find(marker, offset + 1)
    return tuple(ranges)


def _outside_direct_range(offset: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return not any(start <= offset < stop for start, stop in ranges)


def audit_capture(path: Path, window_ms: int) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    skills = frozenset(int(value) for value in decoder.damage_skill_ids)
    packets: list[PacketRow] = []
    direct_rows: list[DirectRow] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction())
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        actions = decode_v576_structured_actions(
            payload,
            direction,
            skills,
            (),
            allow_unknown_actor_ids=True,
        )
        action_pairs = tuple(
            sorted(
                {
                    (int(action.source_actor_id or 0), int(action.skill_id or 0))
                    for action in actions
                    if int(action.source_actor_id or 0) > 0
                    and int(action.skill_id or 0) in skills
                }
            )
        )
        bodies = tuple(
            _decode_v576_body(payload, phase)
            for phase in _v576_candidate_xor_phases(payload)
        )
        packets.append(
            PacketRow(
                packet_index=packet_index,
                timestamp_ms=timestamp_ms,
                direction=direction,
                signature=payload[5:9].hex() if len(payload) >= 9 else "",
                payload_length=len(payload),
                actions=action_pairs,
                token_offsets_by_phase=bodies,
                direct_marker_ranges_by_phase=tuple(_direct_ranges(body) for body in bodies),
            )
        )
        if direction == "server_to_client":
            for hit in decode_v576_embedded_direct_damage_events(payload, direction, skills):
                actor_id = int(hit.source_actor_id or 0)
                object_id = int(hit.damage_object_id or 0)
                if actor_id <= 0 or object_id <= 0:
                    continue
                direct_rows.append(
                    DirectRow(
                        packet_index=packet_index,
                        timestamp_ms=timestamp_ms,
                        source_actor_id=actor_id,
                        object_id=object_id,
                        skill_id=int(hit.skill_id or 0),
                        exact=bool(hit.skill_id_exact),
                    )
                )
        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    same_packet = Counter()
    token_link = Counter()
    token_shapes: dict[tuple[str, str, int, int], Counter[str]] = defaultdict(Counter)
    token_shape_skills: dict[tuple[str, str, int, int], Counter[int]] = defaultdict(Counter)
    examples: list[dict[str, Any]] = []
    packet_times = [row.timestamp_ms for row in packets]
    for hit in direct_rows:
        packet = packets[hit.packet_index - 1]
        same_skills = {
            skill_id
            for actor_id, skill_id in packet.actions
            if actor_id == hit.source_actor_id
        }
        if len(same_skills) == 1:
            candidate = next(iter(same_skills))
            if hit.exact:
                same_packet["support" if candidate == hit.skill_id else "conflict"] += 1
            else:
                same_packet["nonexact_candidates"] += 1

        encoded = hit.object_id.to_bytes(4, "little")
        candidates: list[tuple[PacketRow, int, int, str]] = []
        left = bisect_left(packet_times, hit.timestamp_ms - window_ms)
        right = bisect_right(packet_times, hit.timestamp_ms + 250)
        for other in packets[left:right]:
            delta = other.timestamp_ms - hit.timestamp_ms
            if delta < -window_ms or delta > 250:
                continue
            actor_skills = {
                skill_id
                for actor_id, skill_id in other.actions
                if actor_id == hit.source_actor_id
            }
            if len(actor_skills) != 1:
                continue
            candidate = next(iter(actor_skills))
            matched = False
            relative_offset = -1
            matched_body_hex = ""
            for body, ranges in zip(
                other.token_offsets_by_phase,
                other.direct_marker_ranges_by_phase,
            ):
                offset = body.find(encoded)
                while offset >= 0:
                    if _outside_direct_range(offset, ranges):
                        matched = True
                        relative_offset = offset
                        matched_body_hex = body.hex(" ")
                        break
                    offset = body.find(encoded, offset + 1)
                if matched:
                    break
            if matched:
                candidates.append((other, candidate, relative_offset, matched_body_hex))
        identities = {candidate for _other, candidate, _offset, _body in candidates}
        if len(identities) != 1:
            continue
        candidate = next(iter(identities))
        if hit.exact:
            token_link["support" if candidate == hit.skill_id else "conflict"] += 1
        else:
            token_link["nonexact_candidates"] += 1
        for other, other_skill, offset, _body in candidates:
            shape = (other.direction, other.signature, other.payload_length, offset)
            if hit.exact:
                token_shapes[shape]["support" if other_skill == hit.skill_id else "conflict"] += 1
                token_shape_skills[shape][hit.skill_id] += 1
        if len(examples) < 60 and (hit.exact or len(examples) < 15):
            examples.append(
                {
                    "timestamp_ms": hit.timestamp_ms,
                    "source_actor_id": hit.source_actor_id,
                    "object_id": hit.object_id,
                    "truth_skill_id": hit.skill_id,
                    "truth_exact": hit.exact,
                    "candidate_skill_id": candidate,
                    "matches": [
                        {
                            "delta_ms": other.timestamp_ms - hit.timestamp_ms,
                            "direction": other.direction,
                            "signature": other.signature,
                            "payload_length": other.payload_length,
                            "absolute_token_offset": offset,
                            "decoded_body_hex": body_hex,
                        }
                        for other, _candidate, offset, body_hex in candidates[:10]
                    ],
                }
            )

    shapes = []
    for shape, counts in token_shapes.items():
        shapes.append(
            {
                "direction": shape[0],
                "signature": shape[1],
                "payload_length": shape[2],
                "absolute_token_offset": shape[3],
                "support": int(counts["support"]),
                "conflict": int(counts["conflict"]),
                "truth_skills": {
                    str(k): v for k, v in token_shape_skills[shape].most_common()
                },
            }
        )
    shapes.sort(key=lambda row: (int(row["conflict"]), -int(row["support"])))
    return {
        "capture": str(path),
        "window_ms": window_ms,
        "packets": len(packets),
        "direct37_rows": len(direct_rows),
        "exact_direct37_rows": sum(row.exact for row in direct_rows),
        "same_packet": dict(same_packet),
        "action_packet_object_token": dict(token_link),
        "token_shapes": shapes,
        "examples": examples,
    }


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    same_packet = Counter()
    token_link = Counter()
    shapes: dict[tuple[str, str, int, int], Counter[str]] = defaultdict(Counter)
    captures: dict[tuple[str, str, int, int], set[str]] = defaultdict(set)
    skills: dict[tuple[str, str, int, int], Counter[str]] = defaultdict(Counter)
    for report in reports:
        totals["packets"] += int(report["packets"])
        totals["direct37_rows"] += int(report["direct37_rows"])
        totals["exact_direct37_rows"] += int(report["exact_direct37_rows"])
        same_packet.update(report["same_packet"])
        token_link.update(report["action_packet_object_token"])
        for row in report["token_shapes"]:
            key = (
                str(row["direction"]),
                str(row["signature"]),
                int(row["payload_length"]),
                int(row["absolute_token_offset"]),
            )
            shapes[key]["support"] += int(row["support"])
            shapes[key]["conflict"] += int(row["conflict"])
            captures[key].add(str(report["capture"]))
            skills[key].update(row["truth_skills"])
    rendered_shapes = [
        {
            "direction": key[0],
            "signature": key[1],
            "payload_length": key[2],
            "absolute_token_offset": key[3],
            "support": int(counts["support"]),
            "conflict": int(counts["conflict"]),
            "capture_count": len(captures[key]),
            "truth_skills": dict(skills[key].most_common()),
        }
        for key, counts in shapes.items()
    ]
    rendered_shapes.sort(key=lambda row: (int(row["conflict"]), -int(row["support"])))
    return {
        **dict(totals),
        "same_packet": dict(same_packet),
        "action_packet_object_token": dict(token_link),
        "token_shapes": rendered_shapes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--window-ms", type=int, default=2000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path.resolve(), max(1, args.window_ms)) for path in args.captures]
    result = {
        "schema_version": 1,
        "truth_policy": "Only fixed-slot exact direct37 skills validate candidates.",
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
