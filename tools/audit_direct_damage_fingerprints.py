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
from dragon_nest_builder.live_dps.models import CombatEvent  # noqa: E402
from dragon_nest_builder.live_dps.protocol_v576 import (  # noqa: E402
    _decode_v576_body,
    _v576_candidate_xor_phases,
)
from dragon_nest_builder.live_dps.sources import (  # noqa: E402
    V576DiagnosticReplaySource,
)


def _decoded_record_body(packet: object, decoded: object) -> bytes | None:
    payload = bytes(getattr(packet, "payload", b""))
    marker_offset = int(getattr(decoded, "record_offset", 0) or 0)
    source_actor_id = int(getattr(decoded, "source_actor_id", 0) or 0)
    damage = int(getattr(decoded, "damage", 0) or 0)
    marker = b"\x08\x77\x00" if "direct41" in str(
        getattr(decoded, "layout", "")
    ) else b"\x08\x73\x00"
    damage_relative = 23 if marker == b"\x08\x77\x00" else 19
    for phase in _v576_candidate_xor_phases(payload):
        body = _decode_v576_body(payload, phase)
        if (
            marker_offset >= 0
            and marker_offset + damage_relative + 8 <= len(body)
            and body[marker_offset : marker_offset + 3] == marker
            and int.from_bytes(
                body[marker_offset + 8 : marker_offset + 12], "little"
            )
            == source_actor_id
            and int.from_bytes(
                body[
                    marker_offset + damage_relative :
                    marker_offset + damage_relative + 8
                ],
                "little",
            )
            == damage
        ):
            return body
    return None


def audit_capture(path: Path) -> dict[str, Any]:
    source = V576DiagnosticReplaySource(path, DATA_DIR, {})
    decoder = source._decoder
    original_feed = decoder.feed
    rows: list[dict[str, Any]] = []

    def feed(packet, packet_features=None):
        event = original_feed(packet, packet_features)
        decoded = decoder.last_decoded_message
        layout = str(getattr(decoded, "layout", ""))
        if (
            isinstance(event, CombatEvent)
            and not event.healing
            and decoded is not None
            and "direct37" in layout
        ):
            body = _decoded_record_body(packet, decoded)
            marker_offset = int(getattr(decoded, "record_offset", 0) or 0)
            if body is not None and marker_offset + 64 <= len(body):
                rows.append(
                    {
                        "timestamp_ms": int(event.timestamp_ms),
                        "source_id": str(event.source_id),
                        "job": str(event.job),
                        "assigned_skill_id": str(event.skill_id),
                        "assigned_skill_name": str(event.skill_name),
                        "assigned_method": str(event.attribution_method),
                        "amount": int(event.amount),
                        "target_id": str(event.target_id),
                        "wire_skill_id": int(getattr(decoded, "skill_id", 0) or 0),
                        "wire_skill_exact": bool(
                            getattr(decoded, "skill_id_exact", False)
                        ),
                        "layout": layout,
                        "packet_signature": bytes(packet.payload[5:9]).hex(),
                        "payload_length": len(packet.payload),
                        "record_offset": marker_offset,
                        "damage_object_id": int.from_bytes(
                            body[marker_offset + 13 : marker_offset + 17],
                            "little",
                        ),
                        "result_group_id": int.from_bytes(
                            body[marker_offset + 3 : marker_offset + 7],
                            "little",
                        ),
                        # The result descriptor begins immediately after the
                        # critical/result byte. In corpus samples it remains
                        # stable across skill-ID-bearing rows and their compact
                        # continuation rows while damage, object and target
                        # sequence fields change.
                        "result_fingerprint": bytes(
                            body[marker_offset + 36 : marker_offset + 64]
                        ).hex(),
                        # Kept only while the aggregate field audit runs. The
                        # summary removes it before serialising the report.
                        # Relative offset 0 is the first direct37 marker byte.
                        "_relative_body": bytes(
                            body[
                                max(0, marker_offset - 18) : min(
                                    len(body), marker_offset + 208
                                )
                            ]
                        ),
                        "_relative_body_start": -min(18, marker_offset),
                    }
                )
        return event

    decoder.feed = feed
    try:
        source.poll()
    finally:
        source.close()
    return {"capture": str(path), "rows": rows}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    exact_by_fingerprint: dict[str, Counter[int]] = defaultdict(Counter)
    all_rows: list[dict[str, Any]] = []
    for result in results:
        for row in result["rows"]:
            all_rows.append(row)
            if row["wire_skill_exact"] and row["wire_skill_id"] > 0:
                exact_by_fingerprint[row["result_fingerprint"]][
                    row["wire_skill_id"]
                ] += 1

    unique_map = {
        fingerprint: next(iter(skill_counts))
        for fingerprint, skill_counts in exact_by_fingerprint.items()
        if len(skill_counts) == 1
    }
    conflict_map = {
        fingerprint: dict(skill_counts.most_common())
        for fingerprint, skill_counts in exact_by_fingerprint.items()
        if len(skill_counts) > 1
    }
    nonexact = [row for row in all_rows if not row["wire_skill_exact"]]
    recovered = [
        row for row in nonexact if row["result_fingerprint"] in unique_map
    ]
    agreement = Counter()
    recovered_by_skill: Counter[int] = Counter()
    for row in recovered:
        inferred = unique_map[row["result_fingerprint"]]
        recovered_by_skill[inferred] += 1
        try:
            assigned = int(row["assigned_skill_id"])
        except ValueError:
            assigned = 0
        agreement["same_as_old" if assigned == inferred else "changes_old"] += 1

    # Search the result record for a second deterministic key. The direct
    # skill field itself is deliberately excluded, as are target/source,
    # transient object, damage and critical fields. A candidate is accepted
    # only when it points to one skill across independent actors/captures.
    # This is an audit result, not a production mapping: it reveals protocol
    # fields worth decoding without promoting timing inference to evidence.
    volatile_ranges = (
        (-4, 0),   # target actor
        (3, 17),   # result/source/transient object identities
        (19, 27),  # damage amount
        (35, 36),  # critical/result flags
        (102, 106),
        (114, 118),
    )

    def overlaps_volatile(relative_offset: int, width: int) -> bool:
        end = relative_offset + width
        return any(
            relative_offset < stop and end > start
            for start, stop in volatile_ranges
        )

    feature_labels: dict[tuple[str, int, int, int], Counter[int]] = defaultdict(Counter)
    feature_sources: dict[tuple[str, int, int, int], set[tuple[str, str]]] = defaultdict(set)
    feature_captures: dict[tuple[str, int, int, int], set[str]] = defaultdict(set)
    for result in results:
        capture = str(result["capture"])
        for row in result["rows"]:
            if not row["wire_skill_exact"] or row["wire_skill_id"] <= 0:
                continue
            body = row["_relative_body"]
            start = int(row["_relative_body_start"])
            for width in (2, 4):
                for index in range(0, len(body) - width + 1):
                    relative_offset = start + index
                    if overlaps_volatile(relative_offset, width):
                        continue
                    value = int.from_bytes(body[index : index + width], "little")
                    if value in {0, (1 << (8 * width)) - 1}:
                        continue
                    key = (str(row["job"]), relative_offset, width, value)
                    feature_labels[key][int(row["wire_skill_id"])] += 1
                    feature_sources[key].add((capture, str(row["source_id"])))
                    feature_captures[key].add(capture)

    unique_features: dict[tuple[str, int, int, int], int] = {}
    for key, labels in feature_labels.items():
        support = sum(labels.values())
        independent_sources = len(feature_sources[key])
        captures = len(feature_captures[key])
        if (
            len(labels) == 1
            and support >= 4
            and independent_sources >= 2
            and (captures >= 2 or independent_sources >= 3)
        ):
            unique_features[key] = next(iter(labels))

    field_votes: list[dict[str, Any]] = []
    field_agreement = Counter()
    field_recovered_by_skill: Counter[int] = Counter()
    ambiguous_field_rows = 0
    for result in results:
        capture = str(result["capture"])
        for row in result["rows"]:
            if row["wire_skill_exact"]:
                continue
            body = row["_relative_body"]
            start = int(row["_relative_body_start"])
            votes: Counter[int] = Counter()
            evidence: list[dict[str, int | str]] = []
            for width in (2, 4):
                for index in range(0, len(body) - width + 1):
                    relative_offset = start + index
                    if overlaps_volatile(relative_offset, width):
                        continue
                    value = int.from_bytes(body[index : index + width], "little")
                    key = (str(row["job"]), relative_offset, width, value)
                    skill_id = unique_features.get(key)
                    if skill_id is None:
                        continue
                    votes[skill_id] += 1
                    if len(evidence) < 12:
                        evidence.append(
                            {
                                "relative_offset": relative_offset,
                                "width": width,
                                "value": value,
                                "skill_id": skill_id,
                            }
                        )
            if not votes:
                continue
            if len(votes) != 1:
                ambiguous_field_rows += 1
                continue
            inferred = next(iter(votes))
            try:
                assigned = int(row["assigned_skill_id"])
            except ValueError:
                assigned = 0
            field_agreement[
                "same_as_old" if assigned == inferred else "changes_old"
            ] += 1
            field_recovered_by_skill[inferred] += 1
            if len(field_votes) < 500:
                field_votes.append(
                    {
                        "capture": capture,
                        "timestamp_ms": row["timestamp_ms"],
                        "source_id": row["source_id"],
                        "job": row["job"],
                        "assigned_skill_id": row["assigned_skill_id"],
                        "assigned_skill_name": row["assigned_skill_name"],
                        "assigned_method": row["assigned_method"],
                        "field_skill_id": inferred,
                        "field_evidence": evidence,
                    }
                )

    unique_field_summary = Counter()
    for (job, relative_offset, width, _value), skill_id in unique_features.items():
        unique_field_summary[
            f"{job}|rel={relative_offset:+d}|u{width * 8}|skill={skill_id}"
        ] += 1

    serializable_rows: list[dict[str, Any]] = []
    for row in all_rows:
        serializable_rows.append(
            {
                key: value
                for key, value in row.items()
                if not key.startswith("_relative_body")
            }
        )

    return {
        "captures": [result["capture"] for result in results],
        "direct37_rows": len(all_rows),
        "exact_training_rows": sum(
            int(row["wire_skill_exact"]) for row in all_rows
        ),
        "fingerprints_with_exact_training": len(exact_by_fingerprint),
        "unique_fingerprints": len(unique_map),
        "conflicting_fingerprints": len(conflict_map),
        "nonexact_rows": len(nonexact),
        "recoverable_nonexact_rows": len(recovered),
        "recoverable_percent": round(
            len(recovered) / len(nonexact) * 100.0, 4
        )
        if nonexact
        else 0.0,
        "old_assignment_agreement": dict(agreement),
        "recovered_by_skill": {
            str(skill_id): count
            for skill_id, count in recovered_by_skill.most_common()
        },
        "candidate_field_audit": {
            "unique_feature_keys": len(unique_features),
            "recoverable_nonexact_rows": sum(field_agreement.values()),
            "ambiguous_nonexact_rows": ambiguous_field_rows,
            "old_assignment_agreement": dict(field_agreement),
            "recovered_by_skill": {
                str(skill_id): count
                for skill_id, count in field_recovered_by_skill.most_common()
            },
            "top_unique_fields": dict(
                unique_field_summary.most_common(200)
            ),
            "sample_rows": field_votes,
        },
        "conflicting_fingerprints_detail": conflict_map,
        "unique_fingerprint_skill_map": unique_map,
        "rows": serializable_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit stable direct37 result descriptors against exact wire skills."
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(
        [audit_capture(path.resolve()) for path in args.captures]
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
