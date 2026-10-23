from __future__ import annotations

import argparse
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
    decode_v576_embedded_direct_damage_events,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


MARKER = b"\x09\x62\x00"


@dataclass(frozen=True, slots=True)
class SequenceRow:
    timestamp_ms: int
    packet_index: int
    direction: str
    signature: str
    payload_length: int
    marker_offset: int
    owner_before: int
    header_actor: int
    sequence_id: int
    state_code: int
    fields: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class DirectRow:
    timestamp_ms: int
    packet_index: int
    source_actor_id: int
    object_id: int
    skill_id: int
    exact: bool


def collect_capture(path: Path, window_ms: int) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed_events = decoder.feed_events
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    sequence_rows: list[SequenceRow] = []
    direct_rows: list[DirectRow] = []
    packet_index = 0

    def feed_events(packet):
        nonlocal packet_index
        packet_index += 1
        payload = bytes(packet.payload)
        direction = str(packet.inferred_direction())
        timestamp_ms = round(float(packet.timestamp_seconds) * 1000.0)
        signature = payload[5:9].hex() if len(payload) >= 9 else ""
        if direction == "server_to_client":
            for hit in decode_v576_embedded_direct_damage_events(
                payload, direction, catalog
            ):
                object_id = int(hit.damage_object_id or 0)
                actor_id = int(hit.source_actor_id or 0)
                if object_id <= 0 or actor_id <= 0:
                    continue
                direct_rows.append(
                    DirectRow(
                        timestamp_ms=timestamp_ms,
                        packet_index=packet_index,
                        source_actor_id=actor_id,
                        object_id=object_id,
                        skill_id=int(hit.skill_id or 0),
                        exact=bool(hit.skill_id_exact),
                    )
                )

        seen: set[tuple[int, int, int, int]] = set()
        for phase in _v576_candidate_xor_phases(payload):
            body = _decode_v576_body(payload, phase)
            marker = body.find(MARKER, 9)
            while marker >= 0:
                if marker >= 4 and marker + 16 <= len(body):
                    owner_before = int.from_bytes(body[marker - 4 : marker], "little")
                    header_actor = (
                        int.from_bytes(body[10:14], "little") if len(body) >= 14 else 0
                    )
                    sequence_id = int.from_bytes(body[marker + 8 : marker + 12], "little")
                    state_code = int.from_bytes(body[marker + 12 : marker + 16], "little")
                    key = (marker, owner_before, sequence_id, state_code)
                    if key not in seen and (owner_before > 0 or header_actor > 0):
                        seen.add(key)
                        fields: list[tuple[int, int]] = []
                        start = max(0, marker - 16)
                        stop = min(len(body) - 3, marker + 100)
                        for offset in range(start, stop):
                            value = int.from_bytes(body[offset : offset + 4], "little")
                            if 1 <= value <= 0x00FFFFFF:
                                fields.append((offset - marker, value))
                        sequence_rows.append(
                            SequenceRow(
                                timestamp_ms=timestamp_ms,
                                packet_index=packet_index,
                                direction=direction,
                                signature=signature,
                                payload_length=len(payload),
                                marker_offset=marker,
                                owner_before=owner_before,
                                header_actor=header_actor,
                                sequence_id=sequence_id,
                                state_code=state_code,
                                fields=tuple(fields),
                            )
                        )
                marker = body.find(MARKER, marker + 1)
        return original_feed_events(packet)

    decoder.feed_events = feed_events
    try:
        source.poll()
    finally:
        source.close()

    by_actor: dict[int, list[SequenceRow]] = defaultdict(list)
    for row in sequence_rows:
        if row.owner_before > 0:
            by_actor[row.owner_before].append(row)
        if row.header_actor > 0 and row.header_actor != row.owner_before:
            by_actor[row.header_actor].append(row)

    offset_truth: dict[tuple[str, int, int], Counter[str]] = defaultdict(Counter)
    code_truth: dict[tuple[str, int, int, int], Counter[int]] = defaultdict(Counter)
    linked_exact = 0
    linked_nonexact = 0
    linked_rows = 0
    samples: list[dict[str, Any]] = []
    for hit in direct_rows:
        matches: list[tuple[SequenceRow, int]] = []
        for row in by_actor.get(hit.source_actor_id, ()):
            if abs(row.timestamp_ms - hit.timestamp_ms) > window_ms:
                continue
            for relative_offset, value in row.fields:
                if value == hit.object_id:
                    matches.append((row, relative_offset))
        if not matches:
            continue
        linked_rows += 1
        linked_exact += int(hit.exact)
        linked_nonexact += int(not hit.exact)
        if hit.exact:
            for row, relative_offset in matches:
                shape_key = (row.direction, row.payload_length, relative_offset)
                offset_truth[shape_key]["support"] += 1
                code_key = (
                    row.direction,
                    row.payload_length,
                    relative_offset,
                    row.state_code,
                )
                code_truth[code_key][hit.skill_id] += 1
        if len(samples) < 80 and (hit.exact or len(samples) < 20):
            samples.append(
                {
                    "timestamp_ms": hit.timestamp_ms,
                    "source_actor_id": hit.source_actor_id,
                    "damage_object_id": hit.object_id,
                    "skill_id": hit.skill_id,
                    "exact": hit.exact,
                    "matches": [
                        {
                            "delta_ms": row.timestamp_ms - hit.timestamp_ms,
                            "packet_delta": row.packet_index - hit.packet_index,
                            "direction": row.direction,
                            "signature": row.signature,
                            "payload_length": row.payload_length,
                            "marker_offset": row.marker_offset,
                            "relative_offset": relative_offset,
                            "sequence_id": row.sequence_id,
                            "state_code": row.state_code,
                        }
                        for row, relative_offset in matches[:12]
                    ],
                }
            )

    code_rows: list[dict[str, Any]] = []
    for key, skills in code_truth.items():
        total = sum(skills.values())
        top_skill, top_count = skills.most_common(1)[0]
        code_rows.append(
            {
                "direction": key[0],
                "payload_length": key[1],
                "relative_offset": key[2],
                "state_code": key[3],
                "exact_support": total,
                "top_skill_id": top_skill,
                "top_skill_support": top_count,
                "conflicts": total - top_count,
                "skills": {str(k): v for k, v in skills.most_common()},
            }
        )
    code_rows.sort(key=lambda row: (int(row["conflicts"]), -int(row["exact_support"])))
    return {
        "capture": str(path),
        "window_ms": window_ms,
        "sequence_rows": len(sequence_rows),
        "direct37_rows": len(direct_rows),
        "exact_direct37_rows": sum(row.exact for row in direct_rows),
        "linked_rows": linked_rows,
        "linked_exact_rows": linked_exact,
        "linked_nonexact_rows": linked_nonexact,
        "code_skill_candidates": code_rows,
        "samples": samples,
    }


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    code_skills: dict[tuple[str, int, int, int], Counter[int]] = defaultdict(Counter)
    captures: dict[tuple[str, int, int, int], set[str]] = defaultdict(set)
    for report in reports:
        for field in (
            "sequence_rows",
            "direct37_rows",
            "exact_direct37_rows",
            "linked_rows",
            "linked_exact_rows",
            "linked_nonexact_rows",
        ):
            totals[field] += int(report[field])
        for row in report["code_skill_candidates"]:
            key = (
                str(row["direction"]),
                int(row["payload_length"]),
                int(row["relative_offset"]),
                int(row["state_code"]),
            )
            code_skills[key].update({int(k): int(v) for k, v in row["skills"].items()})
            captures[key].add(str(report["capture"]))
    candidates: list[dict[str, Any]] = []
    for key, skills in code_skills.items():
        total = sum(skills.values())
        skill_id, support = skills.most_common(1)[0]
        candidates.append(
            {
                "direction": key[0],
                "payload_length": key[1],
                "relative_offset": key[2],
                "state_code": key[3],
                "exact_support": total,
                "skill_id": skill_id,
                "support": support,
                "conflicts": total - support,
                "capture_count": len(captures[key]),
                "skills": {str(k): v for k, v in skills.most_common()},
                "zero_conflict_candidate": total >= 3 and total == support,
            }
        )
    candidates.sort(
        key=lambda row: (
            not bool(row["zero_conflict_candidate"]),
            -int(row["capture_count"]),
            -int(row["support"]),
        )
    )
    return {**dict(totals), "code_skill_candidates": candidates}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--window-ms", type=int, default=2000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [collect_capture(path.resolve(), max(1, args.window_ms)) for path in args.captures]
    result = {
        "schema_version": 1,
        "truth_policy": "Only fixed-slot exact direct37 skills train 09 62 sequence relations.",
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
