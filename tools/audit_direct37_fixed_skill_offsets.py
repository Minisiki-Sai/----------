from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
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


def _decoded_body(payload: bytes, row: object) -> bytes | None:
    marker = int(getattr(row, "record_offset", 0) or 0)
    source_actor_id = int(getattr(row, "source_actor_id", 0) or 0)
    damage = int(getattr(row, "damage", 0) or 0)
    for phase in _v576_candidate_xor_phases(payload):
        candidate = _decode_v576_body(payload, phase)
        if marker < 0 or marker + 27 > len(candidate):
            continue
        if candidate[marker : marker + 3] != b"\x08\x73\x00":
            continue
        if int.from_bytes(candidate[marker + 8 : marker + 12], "little") != source_actor_id:
            continue
        if int.from_bytes(candidate[marker + 19 : marker + 27], "little") != damage:
            continue
        return candidate
    return None


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed
    catalog = frozenset(int(value) for value in decoder.damage_skill_ids)
    stats: dict[tuple[str, int, int, int], Counter[str]] = defaultdict(Counter)
    exact_skills: dict[tuple[str, int, int, int], Counter[int]] = defaultdict(Counter)
    value_skills: dict[tuple[str, int, int, int], Counter[int]] = defaultdict(Counter)
    shape_rows: Counter[tuple[str, int, int]] = Counter()
    shape_exact_rows: Counter[tuple[str, int, int]] = Counter()
    seen_rows: set[tuple[int, int, int, int]] = set()

    def inspect(packet: object) -> None:
        payload = bytes(getattr(packet, "payload", b""))
        if len(payload) < 57:
            return
        rows = decode_v576_embedded_direct_damage_events(
            payload,
            packet.inferred_direction(),
            catalog,
        )
        signature = payload[5:9].hex()
        for row in rows:
            marker = int(row.record_offset or 0)
            identity = (
                int(getattr(packet, "timestamp_ms", 0) or 0),
                marker,
                int(row.source_actor_id or 0),
                int(row.damage or 0),
            )
            if identity in seen_rows:
                continue
            seen_rows.add(identity)
            body = _decoded_body(payload, row)
            if body is None:
                continue
            shape = (signature, len(payload), marker)
            shape_rows[shape] += 1
            truth = int(row.skill_id or 0) if row.skill_id_exact else 0
            if truth <= 0:
                continue
            shape_exact_rows[shape] += 1
            start = max(0, marker - 24)
            stop = min(len(body) - 3, marker + 220)
            for offset in range(start, stop):
                value = int.from_bytes(body[offset : offset + 4], "little")
                if value not in catalog:
                    continue
                relative = offset - marker
                key = (*shape, relative)
                stats[key]["support" if value == truth else "conflict"] += 1
                exact_skills[key][truth] += 1
                value_skills[key][value] += 1

    def feed(packet, packet_features=None):
        inspect(packet)
        return original_feed(packet, packet_features)

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()

    rows_rendered: list[dict[str, Any]] = []
    for key, counts in stats.items():
        signature, payload_length, marker, relative = key
        support = int(counts["support"])
        conflict = int(counts["conflict"])
        rows_rendered.append(
            {
                "packet_signature": signature,
                "payload_length": payload_length,
                "record_offset": marker,
                "relative_offset": relative,
                "shape_rows": int(shape_rows[(signature, payload_length, marker)]),
                "shape_exact_rows": int(shape_exact_rows[(signature, payload_length, marker)]),
                "support": support,
                "conflict": conflict,
                "precision_percent": round(support / max(1, support + conflict) * 100.0, 8),
                "truth_skills": {str(k): v for k, v in exact_skills[key].most_common()},
                "field_skills": {str(k): v for k, v in value_skills[key].most_common()},
            }
        )
    rows_rendered.sort(
        key=lambda row: (
            int(row["conflict"]),
            -int(row["support"]),
            str(row["packet_signature"]),
            int(row["record_offset"]),
            int(row["relative_offset"]),
        )
    )
    return {
        "capture": str(path),
        "decoded_direct37_rows": sum(shape_rows.values()),
        "exact_rows": sum(shape_exact_rows.values()),
        "offsets": rows_rendered,
    }


def aggregate(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    combined: dict[tuple[str, int, int, int], Counter[str]] = defaultdict(Counter)
    files: dict[tuple[str, int, int, int], set[str]] = defaultdict(set)
    truth_skills: dict[tuple[str, int, int, int], Counter[str]] = defaultdict(Counter)
    field_skills: dict[tuple[str, int, int, int], Counter[str]] = defaultdict(Counter)
    for report in reports:
        capture = str(report["capture"])
        for row in report["offsets"]:
            key = (
                str(row["packet_signature"]),
                int(row["payload_length"]),
                int(row["record_offset"]),
                int(row["relative_offset"]),
            )
            combined[key]["support"] += int(row["support"])
            combined[key]["conflict"] += int(row["conflict"])
            if int(row["support"]) + int(row["conflict"]) > 0:
                files[key].add(capture)
            truth_skills[key].update(row["truth_skills"])
            field_skills[key].update(row["field_skills"])

    rendered: list[dict[str, Any]] = []
    for key, counts in combined.items():
        support = int(counts["support"])
        conflict = int(counts["conflict"])
        rendered.append(
            {
                "packet_signature": key[0],
                "payload_length": key[1],
                "record_offset": key[2],
                "relative_offset": key[3],
                "support": support,
                "conflict": conflict,
                "precision_percent": round(support / max(1, support + conflict) * 100.0, 8),
                "capture_count": len(files[key]),
                "truth_skills": dict(truth_skills[key].most_common()),
                "field_skills": dict(field_skills[key].most_common()),
                "zero_conflict_candidate": conflict == 0 and support >= 3,
            }
        )
    rendered.sort(
        key=lambda row: (
            not bool(row["zero_conflict_candidate"]),
            -int(row["capture_count"]),
            -int(row["support"]),
            int(row["conflict"]),
        )
    )
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [audit_capture(path.resolve()) for path in args.captures]
    result = {
        "schema_version": 1,
        "truth_policy": (
            "Only direct37 rows whose fixed +102/+114 result-vector slot names a "
            "catalogued skill are truth. Object-chain and inferred assignments are excluded."
        ),
        "reports": reports,
        "aggregate": aggregate(reports),
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
